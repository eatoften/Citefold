from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import threading
import zipfile
from collections import Counter
from collections.abc import Iterator, Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal
from uuid import uuid4

from .migrations import latest_schema_version
from .trash import (
    COURSE_PURGE_ARTIFACT_FIELDS,
    COURSE_PURGE_MANAGED_ROOTS,
    COURSE_PURGE_METADATA_KEY,
    COURSE_PURGE_PHASES,
    COURSE_PURGE_PLAN_FIELDS,
    COURSE_PURGE_PLAN_VERSION,
    ENTITY_PURGE_METADATA_KEY,
    ENTITY_PURGE_TYPES,
    ENTITY_SOURCE_EXTENSIONS,
    ENTITY_VIDEO_EXTENSIONS,
    validate_entity_purge_plan,
)


BACKUP_FORMAT = "video-course-cards-workspace-backup"
BACKUP_FORMAT_VERSION = 1
BACKUP_EXTENSION = ".vcc-backup"
APP_NAME = "Video Course Cards"
APP_VERSION = "0.1.1"
MANIFEST_PATH = "manifest.json"
DATABASE_ARCHIVE_PATH = "workspace/database.sqlite3"
MANAGED_ROOTS = ("uploads", "audio", "transcripts", "sources")
EXCLUDED_ROOTS = ("backups", "restore", "logs", "exports")
PENDING_RESTORE_FILENAME = "pending-restore.json"
RESTORE_RECEIPT_FILENAME = "restore-receipt.json"
RESTORE_RESULT_FILENAME = "last-restore-result.json"
WORKSPACE_STATE_FILENAME = "workspace-state.json"
INITIAL_WORKSPACE_GENERATION = 1

_RESTORE_LOCK = threading.RLock()
_DATABASE_RESTORE_NAMES = (
    "database-wal",
    "database-shm",
    "database-journal",
    "database",
)


class WorkspaceBackupError(RuntimeError):
    """Base class for workspace backup and restore failures."""


class BackupValidationError(WorkspaceBackupError):
    """Raised when a backup is malformed, unsafe, or internally inconsistent."""


class RestoreQueueError(WorkspaceBackupError):
    """Raised when a validated backup cannot be queued for restart."""


class RestoreRollbackError(WorkspaceBackupError):
    """Raised when a failed restore cannot put the original workspace back."""


@dataclass(frozen=True)
class _RestoreReplacement:
    name: str
    restored: Path | None
    target: Path
    rollback: Path


@dataclass(frozen=True)
class _ManagedPathOwner:
    entity_type: str
    entity_id: str
    course_id: str


@dataclass(frozen=True)
class BackupLimits:
    max_archive_bytes: int = 50 * 1024**3
    max_entry_bytes: int = 25 * 1024**3
    max_uncompressed_bytes: int = 100 * 1024**3
    max_entries: int = 100_000
    max_manifest_bytes: int = 2 * 1024**2
    max_expansion_ratio: float = 1_000.0

    def __post_init__(self) -> None:
        integer_limits = (
            self.max_archive_bytes,
            self.max_entry_bytes,
            self.max_uncompressed_bytes,
            self.max_entries,
            self.max_manifest_bytes,
        )
        if any(value <= 0 for value in integer_limits):
            raise ValueError("Backup limits must be positive.")
        if self.max_expansion_ratio <= 0:
            raise ValueError("Backup expansion ratio must be positive.")


@dataclass(frozen=True)
class ValidatedWorkspaceBackup:
    path: Path
    archive_sha256: str
    archive_size_bytes: int
    created_at: str
    app_version: str
    backup_kind: Literal["manual", "pre_restore"]
    schema_version: int
    source_data_dir: str
    entry_count: int
    managed_file_count: int
    total_uncompressed_bytes: int
    manifest: Mapping[str, Any]


@dataclass(frozen=True)
class WorkspaceBackupSummary:
    path: Path
    valid: bool
    archive_size_bytes: int
    modified_at: str
    archive_sha256: str | None = None
    created_at: str | None = None
    app_version: str | None = None
    backup_kind: Literal["manual", "pre_restore"] | None = None
    schema_version: int | None = None
    entry_count: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class PendingWorkspaceRestore:
    restore_id: str
    backup_id: str
    marker_path: Path
    backup_path: Path
    backup_sha256: str
    queued_at: str
    schema_version: int
    phase: Literal["queued", "swapping", "swapped"] = "queued"
    workspace_generation: int = INITIAL_WORKSPACE_GENERATION


@dataclass(frozen=True)
class WorkspaceRestoreResult:
    restore_id: str
    backup_id: str
    status: Literal["staged", "applied", "failed", "canceled"]
    backup_path: Path
    applied_at: str | None = None
    pre_restore_backup_path: Path | None = None
    error: str | None = None
    failure_record_path: Path | None = None
    workspace_generation: int = INITIAL_WORKSPACE_GENERATION


@dataclass(frozen=True)
class WorkspaceRestoreState:
    workspace_generation: int
    pending: PendingWorkspaceRestore | None
    last_result: WorkspaceRestoreResult | None


def create_workspace_backup(
    *,
    db_path: Path,
    data_dir: Path,
    backup_dir: Path | None = None,
    app_version: str = APP_VERSION,
    backup_kind: Literal["manual", "pre_restore"] = "manual",
    current_schema_version: int | None = None,
    limits: BackupLimits = BackupLimits(),
    now: datetime | None = None,
) -> ValidatedWorkspaceBackup:
    """Create and then independently validate a portable workspace backup."""

    database_path = _require_regular_file(db_path, label="Workspace database")
    workspace_root = _require_directory(data_dir, create=True)
    destination_dir = _require_directory(
        backup_dir or workspace_root / "backups",
        create=True,
    )
    _ensure_control_directory_outside_managed(
        destination_dir,
        data_dir=workspace_root,
        label="Backup directory",
    )
    _ensure_database_outside_managed(
        database_path,
        data_dir=workspace_root,
    )
    expected_schema = (
        latest_schema_version()
        if current_schema_version is None
        else current_schema_version
    )
    if expected_schema < 0:
        raise ValueError("Current schema version cannot be negative.")
    if backup_kind not in {"manual", "pre_restore"}:
        raise ValueError("Unsupported backup kind.")

    created_at = _utc_now(now)
    stamp = _filename_stamp(now)
    filename_kind = "workspace" if backup_kind == "manual" else "pre-restore"
    final_path = destination_dir / (
        f"vcc-{filename_kind}-{stamp}-{uuid4().hex[:8]}{BACKUP_EXTENSION}"
    )
    temporary_archive = destination_dir / f".{final_path.name}.tmp"

    try:
        with tempfile.TemporaryDirectory(
            prefix=".vcc-backup-staging-",
            dir=destination_dir,
        ) as temporary_directory:
            staging_root = Path(temporary_directory)
            payload_root = staging_root / "payload"
            payload_root.mkdir()
            staged_database = payload_root / DATABASE_ARCHIVE_PATH
            staged_database.parent.mkdir(parents=True)
            _sqlite_backup(database_path, staged_database)
            schema_version = _database_schema_version(staged_database)
            if schema_version > expected_schema:
                raise BackupValidationError(
                    "Workspace database schema is newer than this application "
                    f"({schema_version} > {expected_schema})."
                )

            entries: list[dict[str, object]] = [
                _entry_for_file(
                    staged_database,
                    archive_path=DATABASE_ARCHIVE_PATH,
                    kind="database",
                )
            ]
            for root_name in MANAGED_ROOTS:
                source_root = workspace_root / root_name
                staged_root = payload_root / "workspace" / root_name
                staged_root.mkdir(parents=True, exist_ok=True)
                for source_file, relative_path in _iter_managed_files(
                    source_root
                ):
                    archive_path = (
                        PurePosixPath("workspace")
                        / root_name
                        / PurePosixPath(*relative_path.parts)
                    ).as_posix()
                    staged_file = staged_root.joinpath(*relative_path.parts)
                    _copy_regular_file(source_file, staged_file)
                    entry = _entry_for_file(
                        staged_file,
                        archive_path=archive_path,
                        kind=root_name,
                    )
                    if int(entry["size_bytes"]) > limits.max_entry_bytes:
                        raise BackupValidationError(
                            f"Backup entry is too large: {archive_path}"
                        )
                    entries.append(entry)

            _validate_database_file_references(
                staged_database,
                source_data_dir=workspace_root,
                archive_paths={str(item["path"]) for item in entries},
            )
            manifest = _build_manifest(
                created_at=created_at,
                app_version=app_version,
                backup_kind=backup_kind,
                schema_version=schema_version,
                source_data_dir=workspace_root,
                entries=entries,
            )
            manifest_bytes = _canonical_json(manifest)
            if len(manifest_bytes) > limits.max_manifest_bytes:
                raise BackupValidationError("Backup manifest is too large.")

            total_bytes = sum(int(item["size_bytes"]) for item in entries)
            if len(entries) + 1 > limits.max_entries:
                raise BackupValidationError("Backup contains too many entries.")
            if total_bytes + len(manifest_bytes) > limits.max_uncompressed_bytes:
                raise BackupValidationError(
                    "Backup exceeds the uncompressed size limit."
                )

            with zipfile.ZipFile(
                temporary_archive,
                mode="w",
                allowZip64=True,
            ) as archive:
                _write_zip_bytes(
                    archive,
                    MANIFEST_PATH,
                    manifest_bytes,
                    compression=zipfile.ZIP_DEFLATED,
                )
                for entry in entries:
                    archive_path = str(entry["path"])
                    staged_file = payload_root.joinpath(
                        *PurePosixPath(archive_path).parts
                    )
                    archive.write(
                        staged_file,
                        arcname=archive_path,
                        compress_type=_compression_for(staged_file),
                    )

        if temporary_archive.stat().st_size > limits.max_archive_bytes:
            raise BackupValidationError("Backup archive is too large.")
        os.replace(temporary_archive, final_path)
        try:
            return validate_workspace_backup(
                final_path,
                current_schema_version=expected_schema,
                limits=limits,
            )
        except Exception:
            final_path.unlink(missing_ok=True)
            raise
    finally:
        temporary_archive.unlink(missing_ok=True)


def validate_workspace_backup(
    backup_path: Path,
    *,
    current_schema_version: int | None = None,
    limits: BackupLimits = BackupLimits(),
) -> ValidatedWorkspaceBackup:
    """Validate paths, sizes, hashes, SQLite integrity, and schema compatibility."""

    path = _require_regular_file(backup_path, label="Backup archive")
    archive_size = path.stat().st_size
    if archive_size > limits.max_archive_bytes:
        raise BackupValidationError("Backup archive is too large.")
    expected_schema = (
        latest_schema_version()
        if current_schema_version is None
        else current_schema_version
    )
    if expected_schema < 0:
        raise ValueError("Current schema version cannot be negative.")

    archive_sha256 = _sha256_file(path)
    try:
        with zipfile.ZipFile(path, mode="r") as archive:
            infos = archive.infolist()
            _validate_zip_infos(infos, limits=limits)
            info_by_name = {info.filename: info for info in infos}
            manifest_info = info_by_name.get(MANIFEST_PATH)
            if manifest_info is None:
                raise BackupValidationError(
                    "Backup does not contain manifest.json."
                )
            if manifest_info.file_size > limits.max_manifest_bytes:
                raise BackupValidationError("Backup manifest is too large.")
            try:
                manifest = json.loads(
                    archive.read(manifest_info).decode("utf-8")
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BackupValidationError(
                    "Backup manifest is not valid UTF-8 JSON."
                ) from exc

            normalized = _validate_manifest(
                manifest,
                info_by_name=info_by_name,
                current_schema_version=expected_schema,
            )
            for entry in normalized["entries"]:
                info = info_by_name[str(entry["path"])]
                actual_hash = _sha256_zip_entry(archive, info)
                if actual_hash != entry["sha256"]:
                    raise BackupValidationError(
                        f"Backup entry hash mismatch: {entry['path']}"
                    )

            database_entry = next(
                item
                for item in normalized["entries"]
                if item["kind"] == "database"
            )
            with tempfile.TemporaryDirectory(
                prefix=".vcc-backup-validation-",
                dir=path.parent,
            ) as temporary_directory:
                staged_database = Path(temporary_directory) / "database.sqlite3"
                _copy_zip_entry(
                    archive,
                    info_by_name[str(database_entry["path"])],
                    staged_database,
                )
                _check_database(staged_database)
                actual_schema = _database_schema_version(staged_database)
                if actual_schema != normalized["schema"]["version"]:
                    raise BackupValidationError(
                        "Backup database schema does not match the manifest."
                    )
                _validate_database_file_references(
                    staged_database,
                    source_data_dir=Path(
                        normalized["workspace"]["source_data_dir"]
                    ),
                    archive_paths={
                        str(item["path"]) for item in normalized["entries"]
                    },
                )
    except zipfile.BadZipFile as exc:
        raise BackupValidationError("Backup is not a valid ZIP archive.") from exc

    return ValidatedWorkspaceBackup(
        path=path,
        archive_sha256=archive_sha256,
        archive_size_bytes=archive_size,
        created_at=str(normalized["created_at"]),
        app_version=str(normalized["app"]["version"]),
        backup_kind=normalized["backup_kind"],
        schema_version=int(normalized["schema"]["version"]),
        source_data_dir=str(normalized["workspace"]["source_data_dir"]),
        entry_count=int(normalized["counts"]["entries"]),
        managed_file_count=int(normalized["counts"]["managed_files"]),
        total_uncompressed_bytes=int(normalized["counts"]["total_bytes"]),
        manifest=normalized,
    )


def list_workspace_backups(
    *,
    data_dir: Path,
    backup_dir: Path | None = None,
    current_schema_version: int | None = None,
    limits: BackupLimits = BackupLimits(),
) -> list[WorkspaceBackupSummary]:
    """List valid and invalid backups without letting one corrupt file hide others."""

    root = _require_directory(
        backup_dir or Path(data_dir) / "backups",
        create=True,
    )
    summaries: list[WorkspaceBackupSummary] = []
    candidates = [
        item
        for item in root.iterdir()
        if item.name.endswith(BACKUP_EXTENSION) and not _is_link(item)
    ]
    candidates.sort(key=_safe_mtime_ns, reverse=True)
    for candidate in candidates:
        modified_at = datetime.now(timezone.utc).isoformat()
        candidate_size = 0
        try:
            candidate_stat = candidate.stat()
            candidate_size = candidate_stat.st_size
            modified_at = datetime.fromtimestamp(
                candidate_stat.st_mtime,
                tz=timezone.utc,
            ).isoformat()
            validated = validate_workspace_backup(
                candidate,
                current_schema_version=current_schema_version,
                limits=limits,
            )
            summaries.append(
                WorkspaceBackupSummary(
                    path=candidate.resolve(),
                    valid=True,
                    archive_size_bytes=validated.archive_size_bytes,
                    modified_at=modified_at,
                    archive_sha256=validated.archive_sha256,
                    created_at=validated.created_at,
                    app_version=validated.app_version,
                    backup_kind=validated.backup_kind,
                    schema_version=validated.schema_version,
                    entry_count=validated.entry_count,
                )
            )
        except (OSError, WorkspaceBackupError) as exc:
            summaries.append(
                WorkspaceBackupSummary(
                    path=candidate.absolute(),
                    valid=False,
                    archive_size_bytes=candidate_size,
                    modified_at=modified_at,
                    error=str(exc),
                )
            )
    return summaries


def queue_workspace_restore(
    backup_path: Path,
    *,
    data_dir: Path,
    current_schema_version: int | None = None,
    restore_dir: Path | None = None,
    limits: BackupLimits = BackupLimits(),
    now: datetime | None = None,
) -> PendingWorkspaceRestore:
    """Queue exactly one restore without replacing an existing request."""

    with _RESTORE_LOCK:
        workspace_root, queue_root = _restore_roots(
            data_dir=data_dir,
            restore_dir=restore_dir,
        )
        marker_path = queue_root / PENDING_RESTORE_FILENAME
        receipt_path = queue_root / RESTORE_RECEIPT_FILENAME
        if marker_path.exists() or receipt_path.exists():
            raise RestoreQueueError(
                "A workspace restore is already queued or being applied. "
                "Cancel the pending restore before choosing another backup."
            )

        validated = validate_workspace_backup(
            backup_path,
            current_schema_version=current_schema_version,
            limits=limits,
        )
        queued_at = _utc_now(now)
        restore_id = uuid4().hex
        backup_id = validated.path.name
        queued_name = (
            f"queued-{_filename_stamp(now)}-{restore_id[:8]}"
            f"{BACKUP_EXTENSION}"
        )
        queued_path = queue_root / queued_name
        temporary_path = queue_root / f".{queued_name}.tmp"
        workspace_generation = _read_workspace_state(
            queue_root
        )["workspace_generation"]

        try:
            _copy_regular_file(validated.path, temporary_path)
            copied = validate_workspace_backup(
                temporary_path,
                current_schema_version=current_schema_version,
                limits=limits,
            )
            if copied.archive_sha256 != validated.archive_sha256:
                raise RestoreQueueError(
                    "Backup changed while it was being queued."
                )
            os.replace(temporary_path, queued_path)
            marker = {
                "format": BACKUP_FORMAT,
                "format_version": BACKUP_FORMAT_VERSION,
                "restore_id": restore_id,
                "backup_id": backup_id,
                "queued_at": queued_at,
                "backup_filename": queued_name,
                "backup_sha256": copied.archive_sha256,
                "schema_version": copied.schema_version,
                "workspace_generation": workspace_generation,
            }
            # Exclusive creation is the final compare-and-set. A concurrent
            # request can never replace an already-published restore marker.
            _write_atomic_json(marker_path, marker)
        except FileExistsError as exc:
            queued_path.unlink(missing_ok=True)
            raise RestoreQueueError(
                "A workspace restore was queued concurrently."
            ) from exc
        except Exception:
            temporary_path.unlink(missing_ok=True)
            if not marker_path.exists():
                queued_path.unlink(missing_ok=True)
            raise

        return _pending_from_marker(
            marker,
            marker_path=marker_path,
            queue_root=queue_root,
            phase="queued",
        )


def get_pending_workspace_restore(
    *,
    data_dir: Path,
    restore_dir: Path | None = None,
) -> PendingWorkspaceRestore | None:
    with _RESTORE_LOCK:
        _, queue_root = _restore_roots(
            data_dir=data_dir,
            restore_dir=restore_dir,
        )
        marker_path = queue_root / PENDING_RESTORE_FILENAME
        if not marker_path.exists():
            return None
        marker = _read_restore_marker(marker_path, queue_root)
        phase: Literal["queued", "swapping", "swapped"] = "queued"
        receipt_path = queue_root / RESTORE_RECEIPT_FILENAME
        if receipt_path.exists():
            receipt = _read_restore_receipt(receipt_path, queue_root)
            if receipt["restore_id"] == marker["restore_id"]:
                if receipt["phase"] in {
                    "rolling_back",
                    "rollback_finalizing",
                }:
                    phase = "swapping"
                elif receipt["phase"] == "finalizing":
                    phase = "swapped"
                else:
                    phase = receipt["phase"]
        return _pending_from_marker(
            marker,
            marker_path=marker_path,
            queue_root=queue_root,
            phase=phase,
        )


def cancel_pending_workspace_restore(
    restore_id: str,
    *,
    data_dir: Path,
    restore_dir: Path | None = None,
    now: datetime | None = None,
) -> WorkspaceRestoreResult:
    """Cancel a queued restore before the swap phase has started."""

    with _RESTORE_LOCK:
        _, queue_root = _restore_roots(
            data_dir=data_dir,
            restore_dir=restore_dir,
        )
        marker_path = queue_root / PENDING_RESTORE_FILENAME
        if not marker_path.exists():
            raise RestoreQueueError("No workspace restore is pending.")
        marker = _read_restore_marker(marker_path, queue_root)
        if marker["restore_id"] != restore_id:
            raise RestoreQueueError(
                "The pending restore identity does not match this request."
            )
        if (queue_root / RESTORE_RECEIPT_FILENAME).exists():
            raise RestoreQueueError(
                "The workspace restore has already started and cannot be "
                "canceled."
            )

        backup_path = queue_root / marker["backup_filename"]
        marker_path.unlink()
        backup_path.unlink(missing_ok=True)
        result = WorkspaceRestoreResult(
            restore_id=restore_id,
            backup_id=marker["backup_id"],
            status="canceled",
            backup_path=backup_path,
            error="Restore canceled before restart.",
            workspace_generation=marker["workspace_generation"],
        )
        _persist_restore_result(queue_root, result, now=now)
        return result


def get_workspace_restore_state(
    *,
    data_dir: Path,
    restore_dir: Path | None = None,
) -> WorkspaceRestoreState:
    """Return durable generation, pending identity, and the last result."""

    with _RESTORE_LOCK:
        _, queue_root = _restore_roots(
            data_dir=data_dir,
            restore_dir=restore_dir,
        )
        pending = get_pending_workspace_restore(
            data_dir=data_dir,
            restore_dir=queue_root,
        )
        return WorkspaceRestoreState(
            workspace_generation=_read_workspace_state(
                queue_root
            )["workspace_generation"],
            pending=pending,
            last_result=_read_persisted_restore_result(queue_root),
        )


def apply_pending_workspace_restore(
    *,
    db_path: Path,
    data_dir: Path,
    current_schema_version: int | None = None,
    restore_dir: Path | None = None,
    backup_dir: Path | None = None,
    limits: BackupLimits = BackupLimits(),
    now: datetime | None = None,
) -> WorkspaceRestoreResult | None:
    """Swap a queued restore but retain rollback data until finalization."""

    with _RESTORE_LOCK:
        workspace_root, queue_root = _restore_roots(
            data_dir=data_dir,
            restore_dir=restore_dir,
        )
        database_path = Path(db_path).absolute()
        _ensure_database_outside_managed(
            database_path,
            data_dir=workspace_root,
        )
        marker_path = queue_root / PENDING_RESTORE_FILENAME
        receipt_path = queue_root / RESTORE_RECEIPT_FILENAME

        if receipt_path.exists():
            receipt = _read_restore_receipt(receipt_path, queue_root)
            if receipt["phase"] in {"swapping", "rolling_back"}:
                interrupted_error = (
                    "Restore rollback was interrupted."
                    if receipt["phase"] == "rolling_back"
                    else "Restore was interrupted during the filesystem swap."
                )
                return _rollback_interrupted_restore(
                    receipt,
                    db_path=database_path,
                    workspace_root=workspace_root,
                    queue_root=queue_root,
                    error=interrupted_error,
                    now=now,
                )
            if receipt["phase"] == "rollback_finalizing":
                return _finish_rollback_finalizing_restore(
                    receipt,
                    queue_root=queue_root,
                )
            if receipt["phase"] == "swapped":
                return _staged_result_from_receipt(receipt, queue_root)
            if receipt["phase"] == "finalizing":
                return _finish_finalizing_restore(receipt, queue_root)
        if not marker_path.exists():
            return None

        marker = _read_restore_marker(marker_path, queue_root)
        backup_path = queue_root / marker["backup_filename"]
        pre_restore_backup: Path | None = None
        transaction_root: Path | None = None
        swap_state: list[
            tuple[_RestoreReplacement, Path | None]
        ] = []
        receipt: dict[str, Any] | None = None
        try:
            validated = validate_workspace_backup(
                backup_path,
                current_schema_version=current_schema_version,
                limits=limits,
            )
            if validated.archive_sha256 != marker["backup_sha256"]:
                raise BackupValidationError(
                    "Queued backup hash does not match the restore marker."
                )
            if validated.schema_version != marker["schema_version"]:
                raise BackupValidationError(
                    "Queued backup schema does not match the restore marker."
                )

            transaction_name = f".apply-{uuid4().hex}"
            transaction_root = queue_root / transaction_name
            extracted_root = transaction_root / "extracted"
            rollback_root = transaction_root / "rollback"
            (transaction_root / "failed").mkdir(parents=True)
            extracted_root.mkdir()
            rollback_root.mkdir()
            _extract_validated_backup(validated, extracted_root)
            staged_database = extracted_root / DATABASE_ARCHIVE_PATH
            _rebase_database_paths(
                staged_database,
                old_data_dir=Path(validated.source_data_dir),
                new_data_dir=workspace_root,
            )
            _check_database(staged_database)

            if database_path.is_file():
                pre_restore = create_workspace_backup(
                    db_path=database_path,
                    data_dir=workspace_root,
                    backup_dir=backup_dir,
                    app_version=APP_VERSION,
                    backup_kind="pre_restore",
                    current_schema_version=current_schema_version,
                    limits=limits,
                    now=now,
                )
                pre_restore_backup = pre_restore.path

            replacements = _restore_replacements(
                transaction_root=transaction_root,
                workspace_root=workspace_root,
                db_path=database_path,
            )
            # apply_pending_workspace_restore runs before init_db, and every
            # SQLite connection used for validation or the safety backup is
            # closed before this preflight. On Windows, a leaked connection
            # also makes these atomic moves fail before the new DB is
            # published.
            for replacement in replacements:
                _ensure_replaceable_target(replacement.target)
            had_previous = {
                replacement.name: replacement.target.exists()
                for replacement in replacements
            }
            receipt = {
                "format": BACKUP_FORMAT,
                "format_version": BACKUP_FORMAT_VERSION,
                "restore_id": marker["restore_id"],
                "backup_id": marker["backup_id"],
                "backup_filename": marker["backup_filename"],
                "backup_sha256": marker["backup_sha256"],
                "queued_at": marker["queued_at"],
                "schema_version": marker["schema_version"],
                "phase": "swapping",
                "transaction_name": transaction_name,
                "pre_restore_backup_id": (
                    pre_restore_backup.name
                    if pre_restore_backup is not None
                    else None
                ),
                "pre_restore_backup_path": (
                    str(pre_restore_backup.resolve())
                    if pre_restore_backup is not None
                    else None
                ),
                "workspace_generation_before": marker[
                    "workspace_generation"
                ],
                "workspace_generation_after": (
                    marker["workspace_generation"] + 1
                ),
                "had_previous": had_previous,
                "swapped_at": None,
                "committed_at": None,
                "rollback_error": None,
                "rollback_completed_at": None,
                "failure_id": None,
            }
            _write_atomic_json(receipt_path, receipt)

            for replacement in replacements:
                _ensure_replaceable_target(replacement.target)
                replacement.rollback.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                previous: Path | None = None
                if replacement.target.exists():
                    _move_path(
                        replacement.target,
                        replacement.rollback,
                    )
                    previous = replacement.rollback
                swap_state.append((replacement, previous))
                if replacement.restored is not None:
                    replacement.target.parent.mkdir(
                        parents=True,
                        exist_ok=True,
                    )
                    _move_path(
                        replacement.restored,
                        replacement.target,
                    )

            _check_database(database_path)
            receipt["phase"] = "swapped"
            receipt["swapped_at"] = _utc_now(now)
            _replace_atomic_json(receipt_path, receipt)
            return _staged_result_from_receipt(receipt, queue_root)
        except RestoreRollbackError:
            raise
        except Exception as exc:
            rollback_error = _rollback_workspace_swap(
                swap_state,
                transaction_root=transaction_root,
            )
            if rollback_error is not None:
                raise RestoreRollbackError(
                    "Restore failed and the original workspace could not be "
                    f"fully restored: {rollback_error}"
                ) from exc
            if receipt is not None:
                return _begin_rollback_finalization(
                    receipt,
                    queue_root=queue_root,
                    error=str(exc),
                    now=now,
                )
            return _fail_pending_restore(
                marker=marker,
                queue_root=queue_root,
                transaction_root=transaction_root,
                pre_restore_backup=pre_restore_backup,
                error=str(exc),
                now=now,
            )


def finalize_pending_workspace_restore(
    restore_id: str,
    *,
    db_path: Path,
    data_dir: Path,
    restore_dir: Path | None = None,
    now: datetime | None = None,
) -> WorkspaceRestoreResult:
    """Commit a swapped workspace after initialization and recovery succeed."""

    with _RESTORE_LOCK:
        workspace_root, queue_root = _restore_roots(
            data_dir=data_dir,
            restore_dir=restore_dir,
        )
        del workspace_root
        receipt_path = queue_root / RESTORE_RECEIPT_FILENAME
        if not receipt_path.exists():
            result = _read_persisted_restore_result(queue_root)
            if result is not None and result.restore_id == restore_id:
                return result
            raise RestoreQueueError("Restore receipt not found.")
        receipt = _read_restore_receipt(receipt_path, queue_root)
        if receipt["restore_id"] != restore_id:
            raise RestoreQueueError("Restore receipt identity mismatch.")
        if receipt["phase"] == "finalizing":
            return _finish_finalizing_restore(receipt, queue_root)
        if receipt["phase"] != "swapped":
            raise RestoreQueueError("Restore has not completed its swap phase.")
        _check_database(Path(db_path))

        receipt = dict(receipt)
        receipt["phase"] = "finalizing"
        receipt["committed_at"] = _utc_now(now)
        _replace_atomic_json(receipt_path, receipt)
        return _finish_finalizing_restore(receipt, queue_root)


def rollback_pending_workspace_restore(
    restore_id: str,
    *,
    db_path: Path,
    data_dir: Path,
    error: str,
    restore_dir: Path | None = None,
    now: datetime | None = None,
) -> WorkspaceRestoreResult:
    """Roll back a swapped workspace when startup validation fails."""

    with _RESTORE_LOCK:
        workspace_root, queue_root = _restore_roots(
            data_dir=data_dir,
            restore_dir=restore_dir,
        )
        receipt_path = queue_root / RESTORE_RECEIPT_FILENAME
        if not receipt_path.exists():
            raise RestoreQueueError("Restore receipt not found.")
        receipt = _read_restore_receipt(receipt_path, queue_root)
        if receipt["restore_id"] != restore_id:
            raise RestoreQueueError("Restore receipt identity mismatch.")
        if receipt["phase"] == "rollback_finalizing":
            return _finish_rollback_finalizing_restore(
                receipt,
                queue_root=queue_root,
            )
        if receipt["phase"] == "swapped":
            receipt = dict(receipt)
            receipt["phase"] = "rolling_back"
            _replace_atomic_json(receipt_path, receipt)
        elif receipt["phase"] != "rolling_back":
            raise RestoreQueueError(
                "Restore has not completed its swap phase."
            )
        rollback_error = _rollback_workspace_from_receipt(
            receipt,
            db_path=Path(db_path).absolute(),
            workspace_root=workspace_root,
            queue_root=queue_root,
        )
        if rollback_error is not None:
            raise RestoreRollbackError(
                "The restore transaction is retained for manual recovery "
                "because rollback failed: "
                f"{rollback_error}"
            )
        return _begin_rollback_finalization(
            receipt,
            queue_root=queue_root,
            error=error,
            now=now,
        )


def _restore_roots(
    *,
    data_dir: Path,
    restore_dir: Path | None,
) -> tuple[Path, Path]:
    workspace_root = _require_directory(data_dir, create=True)
    queue_root = _require_directory(
        restore_dir or workspace_root / "restore",
        create=True,
    )
    _ensure_control_directory_outside_managed(
        queue_root,
        data_dir=workspace_root,
        label="Restore directory",
    )
    return workspace_root, queue_root


def _pending_from_marker(
    marker: Mapping[str, Any],
    *,
    marker_path: Path,
    queue_root: Path,
    phase: Literal["queued", "swapping", "swapped"],
) -> PendingWorkspaceRestore:
    return PendingWorkspaceRestore(
        restore_id=str(marker["restore_id"]),
        backup_id=str(marker["backup_id"]),
        marker_path=marker_path.resolve(),
        backup_path=(
            queue_root / str(marker["backup_filename"])
        ).resolve(),
        backup_sha256=str(marker["backup_sha256"]),
        queued_at=str(marker["queued_at"]),
        schema_version=int(marker["schema_version"]),
        phase=phase,
        workspace_generation=int(marker["workspace_generation"]),
    )


def _read_workspace_state(queue_root: Path) -> dict[str, Any]:
    path = queue_root / WORKSPACE_STATE_FILENAME
    if not path.exists():
        return {
            "workspace_generation": INITIAL_WORKSPACE_GENERATION,
            "last_restore_id": None,
        }
    raw = _read_json_file(path, "Workspace state")
    generation = raw.get("workspace_generation")
    last_restore_id = raw.get("last_restore_id")
    if (
        raw.get("format") != BACKUP_FORMAT
        or raw.get("format_version") != BACKUP_FORMAT_VERSION
        or not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < INITIAL_WORKSPACE_GENERATION
        or (
            last_restore_id is not None
            and (
                not isinstance(last_restore_id, str)
                or not last_restore_id
            )
        )
    ):
        raise RestoreQueueError("Workspace generation state is invalid.")
    return {
        "workspace_generation": generation,
        "last_restore_id": last_restore_id,
    }


def _restore_replacements(
    *,
    transaction_root: Path,
    workspace_root: Path,
    db_path: Path,
) -> list[_RestoreReplacement]:
    extracted_root = transaction_root / "extracted"
    rollback_root = transaction_root / "rollback"
    replacements = [
        _RestoreReplacement(
            name=root_name,
            restored=extracted_root / "workspace" / root_name,
            target=workspace_root / root_name,
            rollback=rollback_root / "workspace" / root_name,
        )
        for root_name in MANAGED_ROOTS
    ]
    # A crash can leave WAL, SHM, or rollback-journal files next to the main
    # database. They belong to the old DB and must be quarantined before the
    # restored main file is atomically published; otherwise SQLite may replay
    # old transaction state into it.
    replacements.extend(
        _RestoreReplacement(
            name=f"database-{suffix[1:]}",
            restored=None,
            target=Path(f"{db_path}{suffix}"),
            rollback=rollback_root / f"database.sqlite3{suffix}",
        )
        for suffix in ("-wal", "-shm", "-journal")
    )
    replacements.append(
        _RestoreReplacement(
            name="database",
            restored=extracted_root / DATABASE_ARCHIVE_PATH,
            target=db_path,
            rollback=rollback_root / "database.sqlite3",
        )
    )
    return replacements


def _rollback_replacement_order(
    replacements: list[_RestoreReplacement],
) -> list[_RestoreReplacement]:
    by_name = {replacement.name: replacement for replacement in replacements}
    # Restore all dependent file roots first. SQLite sidecars are then made
    # coherent before the old main database is published as the final step.
    ordered_names = [
        *reversed(MANAGED_ROOTS),
        *_DATABASE_RESTORE_NAMES,
    ]
    return [
        by_name[name]
        for name in ordered_names
        if name in by_name
    ]


def _read_restore_receipt(
    receipt_path: Path,
    queue_root: Path,
) -> dict[str, Any]:
    raw = _read_json_file(receipt_path, "Restore receipt")
    required_text = (
        "restore_id",
        "backup_id",
        "backup_filename",
        "backup_sha256",
        "queued_at",
        "transaction_name",
    )
    if (
        raw.get("format") != BACKUP_FORMAT
        or raw.get("format_version") != BACKUP_FORMAT_VERSION
        or any(
            not isinstance(raw.get(key), str) or not raw[key]
            for key in required_text
        )
        or raw.get("phase") not in {
            "swapping",
            "swapped",
            "finalizing",
            "rolling_back",
            "rollback_finalizing",
        }
        or Path(raw["backup_filename"]).name != raw["backup_filename"]
        or Path(raw["backup_id"]).name != raw["backup_id"]
        or Path(raw["transaction_name"]).name != raw["transaction_name"]
        or not raw["transaction_name"].startswith(".apply-")
        or len(raw["backup_sha256"]) != 64
        or any(
            character not in "0123456789abcdef"
            for character in raw["backup_sha256"]
        )
    ):
        raise RestoreQueueError("Restore receipt is invalid.")
    transaction_root = (queue_root / raw["transaction_name"]).resolve()
    if transaction_root.parent != queue_root.resolve():
        raise RestoreQueueError("Restore receipt transaction path is unsafe.")
    had_previous = raw.get("had_previous")
    expected_names = {*MANAGED_ROOTS, *_DATABASE_RESTORE_NAMES}
    if (
        not isinstance(had_previous, dict)
        or set(had_previous) != expected_names
        or any(
            not isinstance(had_previous[name], bool)
            for name in expected_names
        )
    ):
        raise RestoreQueueError("Restore receipt swap state is invalid.")
    for key in (
        "schema_version",
        "workspace_generation_before",
        "workspace_generation_after",
    ):
        value = raw.get(key)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
        ):
            raise RestoreQueueError("Restore receipt generation is invalid.")
    if (
        raw["workspace_generation_after"]
        != raw["workspace_generation_before"] + 1
    ):
        raise RestoreQueueError("Restore receipt generation is invalid.")
    _parse_datetime(raw["queued_at"], "Restore receipt queued_at")
    if raw.get("swapped_at") is not None:
        if not isinstance(raw["swapped_at"], str):
            raise RestoreQueueError("Restore receipt swapped_at is invalid.")
        _parse_datetime(raw["swapped_at"], "Restore receipt swapped_at")
    committed_at = raw.get("committed_at")
    if raw["phase"] == "finalizing":
        if not isinstance(committed_at, str):
            raise RestoreQueueError(
                "Restore receipt committed_at is invalid."
            )
        _parse_datetime(
            committed_at,
            "Restore receipt committed_at",
        )
    elif committed_at is not None:
        raise RestoreQueueError("Restore receipt committed_at is invalid.")
    rollback_error = raw.get("rollback_error")
    rollback_completed_at = raw.get("rollback_completed_at")
    failure_id = raw.get("failure_id")
    if raw["phase"] == "rollback_finalizing":
        if (
            not isinstance(rollback_error, str)
            or not rollback_error
            or not isinstance(rollback_completed_at, str)
            or failure_id != f"restore-{raw['restore_id']}"
        ):
            raise RestoreQueueError(
                "Restore receipt rollback completion is invalid."
            )
        _parse_datetime(
            rollback_completed_at,
            "Restore receipt rollback_completed_at",
        )
    elif any(
        value is not None
        for value in (
            rollback_error,
            rollback_completed_at,
            failure_id,
        )
    ):
        raise RestoreQueueError(
            "Restore receipt rollback completion is invalid."
        )
    pre_restore_path = raw.get("pre_restore_backup_path")
    if pre_restore_path is not None and (
        not isinstance(pre_restore_path, str)
        or not _is_absolute_portable_path(pre_restore_path)
    ):
        raise RestoreQueueError(
            "Restore receipt pre-restore backup path is invalid."
        )
    return raw


def _staged_result_from_receipt(
    receipt: Mapping[str, Any],
    queue_root: Path,
) -> WorkspaceRestoreResult:
    return WorkspaceRestoreResult(
        restore_id=str(receipt["restore_id"]),
        backup_id=str(receipt["backup_id"]),
        status="staged",
        backup_path=queue_root / str(receipt["backup_filename"]),
        applied_at=(
            str(receipt["swapped_at"])
            if receipt.get("swapped_at") is not None
            else None
        ),
        pre_restore_backup_path=_pre_restore_path(receipt),
        workspace_generation=int(
            receipt["workspace_generation_after"]
        ),
    )


def _pre_restore_path(
    receipt: Mapping[str, Any],
) -> Path | None:
    value = receipt.get("pre_restore_backup_path")
    return Path(value) if isinstance(value, str) and value else None


def _rollback_workspace_from_receipt(
    receipt: Mapping[str, Any],
    *,
    db_path: Path,
    workspace_root: Path,
    queue_root: Path,
) -> str | None:
    transaction_root = queue_root / str(receipt["transaction_name"])
    replacements = _restore_replacements(
        transaction_root=transaction_root,
        workspace_root=workspace_root,
        db_path=db_path,
    )
    failed_root = transaction_root / "failed"
    errors: list[str] = []
    for index, replacement in enumerate(
        _rollback_replacement_order(replacements)
    ):
        try:
            had_previous = bool(
                receipt["had_previous"][replacement.name]
            )
            if had_previous:
                # No rollback artifact means this replacement was never
                # started; the target is still the original workspace path.
                if not replacement.rollback.exists():
                    continue
                if replacement.target.exists():
                    displaced = (
                        failed_root
                        / f"recovery-{index}-{replacement.target.name}"
                    )
                    displaced.parent.mkdir(parents=True, exist_ok=True)
                    _move_path(replacement.target, displaced)
                replacement.target.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                _move_path(
                    replacement.rollback,
                    replacement.target,
                )
                continue

            # When no original target existed, a missing extracted path means
            # it was moved into the live workspace and must be displaced.
            was_published = (
                replacement.restored is None
                or not replacement.restored.exists()
            )
            if was_published and replacement.target.exists():
                displaced = (
                    failed_root
                    / f"recovery-{index}-{replacement.target.name}"
                )
                displaced.parent.mkdir(parents=True, exist_ok=True)
                _move_path(replacement.target, displaced)
        except OSError as exc:
            errors.append(f"{replacement.target}: {exc}")
    return "; ".join(errors) if errors else None


def _rollback_interrupted_restore(
    receipt: Mapping[str, Any],
    *,
    db_path: Path,
    workspace_root: Path,
    queue_root: Path,
    error: str,
    now: datetime | None,
) -> WorkspaceRestoreResult:
    rollback_error = _rollback_workspace_from_receipt(
        receipt,
        db_path=db_path,
        workspace_root=workspace_root,
        queue_root=queue_root,
    )
    if rollback_error is not None:
        raise RestoreRollbackError(
            "The interrupted restore transaction is retained for manual "
            "recovery because rollback failed: "
            f"{rollback_error}"
        )
    return _begin_rollback_finalization(
        receipt,
        queue_root=queue_root,
        error=error,
        now=now,
    )


def _begin_rollback_finalization(
    receipt: Mapping[str, Any],
    *,
    queue_root: Path,
    error: str,
    now: datetime | None,
) -> WorkspaceRestoreResult:
    updated = dict(receipt)
    updated["phase"] = "rollback_finalizing"
    updated["rollback_error"] = error
    updated["rollback_completed_at"] = _utc_now(now)
    updated["failure_id"] = f"restore-{receipt['restore_id']}"
    _replace_atomic_json(
        queue_root / RESTORE_RECEIPT_FILENAME,
        updated,
    )
    return _finish_rollback_finalizing_restore(
        updated,
        queue_root=queue_root,
    )


def _finish_rollback_finalizing_restore(
    receipt: Mapping[str, Any],
    *,
    queue_root: Path,
) -> WorkspaceRestoreResult:
    """Publish a rolled-back failure and remove its receipt last."""

    error = receipt.get("rollback_error")
    completed_at = receipt.get("rollback_completed_at")
    failure_id = receipt.get("failure_id")
    if (
        receipt.get("phase") != "rollback_finalizing"
        or not isinstance(error, str)
        or not error
        or not isinstance(completed_at, str)
        or not isinstance(failure_id, str)
        or failure_id != f"restore-{receipt['restore_id']}"
    ):
        raise RestoreQueueError(
            "Rolled-back restore receipt is invalid."
        )
    completed_time = _parse_datetime(
        completed_at,
        "Restore receipt rollback_completed_at",
    )
    failed_root = queue_root / "failed"
    failed_root.mkdir(parents=True, exist_ok=True)
    backup_path = queue_root / str(receipt["backup_filename"])
    failed_backup = failed_root / f"{failure_id}{BACKUP_EXTENSION}"
    if backup_path.exists() and failed_backup.exists():
        raise RestoreQueueError(
            "Rolled-back restore backup cleanup is ambiguous."
        )
    if backup_path.exists():
        _move_path(backup_path, failed_backup)
    archived_backup = failed_backup.name if failed_backup.is_file() else None
    failure_record = failed_root / f"{failure_id}.json"
    _replace_atomic_json(
        failure_record,
        {
            "format": BACKUP_FORMAT,
            "format_version": BACKUP_FORMAT_VERSION,
            "failed_at": completed_at,
            "error": error,
            "backup_filename": archived_backup,
        },
    )
    _remove_finalized_restore_path(
        queue_root / PENDING_RESTORE_FILENAME,
    )
    _remove_finalized_restore_path(
        queue_root / str(receipt["transaction_name"]),
        recursive=True,
    )
    result = WorkspaceRestoreResult(
        restore_id=str(receipt["restore_id"]),
        backup_id=str(receipt["backup_id"]),
        status="failed",
        backup_path=backup_path,
        pre_restore_backup_path=_pre_restore_path(receipt),
        error=error,
        failure_record_path=failure_record,
        workspace_generation=int(receipt["workspace_generation_before"]),
    )
    _persist_restore_result(
        queue_root,
        result,
        now=completed_time,
    )
    _remove_finalized_restore_path(
        queue_root / RESTORE_RECEIPT_FILENAME,
    )
    return result


def _fail_pending_restore(
    *,
    marker: Mapping[str, Any],
    queue_root: Path,
    transaction_root: Path | None,
    pre_restore_backup: Path | None,
    error: str,
    now: datetime | None,
) -> WorkspaceRestoreResult:
    marker_path = queue_root / PENDING_RESTORE_FILENAME
    backup_path = queue_root / str(marker["backup_filename"])
    failure_record = _quarantine_failed_restore(
        marker_path=marker_path,
        backup_path=backup_path,
        queue_root=queue_root,
        error=error,
        now=now,
    )
    (queue_root / RESTORE_RECEIPT_FILENAME).unlink(missing_ok=True)
    if transaction_root is not None and transaction_root.exists():
        shutil.rmtree(transaction_root, ignore_errors=True)
    result = WorkspaceRestoreResult(
        restore_id=str(marker["restore_id"]),
        backup_id=str(marker["backup_id"]),
        status="failed",
        backup_path=backup_path,
        pre_restore_backup_path=pre_restore_backup,
        error=error,
        failure_record_path=failure_record,
        workspace_generation=int(marker["workspace_generation"]),
    )
    _persist_restore_result(queue_root, result, now=now)
    return result


def _cleanup_finalized_restore(
    receipt: Mapping[str, Any],
    queue_root: Path,
) -> None:
    _remove_finalized_restore_path(
        queue_root / PENDING_RESTORE_FILENAME,
    )
    _remove_finalized_restore_path(
        queue_root / str(receipt["backup_filename"]),
    )
    transaction_root = queue_root / str(receipt["transaction_name"])
    _remove_finalized_restore_path(transaction_root, recursive=True)
    # The receipt is the durable commit fence. It must be removed last so a
    # crash at any earlier cleanup boundary resumes cleanup instead of trying
    # to roll back a workspace whose rollback material may already be gone.
    _remove_finalized_restore_path(
        queue_root / RESTORE_RECEIPT_FILENAME,
    )


def _remove_finalized_restore_path(
    path: Path,
    *,
    recursive: bool = False,
) -> None:
    if recursive:
        if path.exists():
            shutil.rmtree(path)
        return
    path.unlink(missing_ok=True)


def _finish_finalizing_restore(
    receipt: Mapping[str, Any],
    queue_root: Path,
) -> WorkspaceRestoreResult:
    """Finish an irreversibly committed restore without attempting rollback."""

    if receipt.get("phase") != "finalizing" or not isinstance(
        receipt.get("committed_at"),
        str,
    ):
        raise RestoreQueueError("Committed restore receipt is invalid.")
    committed_at = str(receipt["committed_at"])
    committed_time = _parse_datetime(
        committed_at,
        "Restore receipt committed_at",
    )
    generation = int(receipt["workspace_generation_after"])
    _replace_atomic_json(
        queue_root / WORKSPACE_STATE_FILENAME,
        {
            "format": BACKUP_FORMAT,
            "format_version": BACKUP_FORMAT_VERSION,
            "workspace_generation": generation,
            "last_restore_id": receipt["restore_id"],
            "updated_at": committed_at,
        },
    )
    result = WorkspaceRestoreResult(
        restore_id=str(receipt["restore_id"]),
        backup_id=str(receipt["backup_id"]),
        status="applied",
        backup_path=queue_root / str(receipt["backup_filename"]),
        applied_at=committed_at,
        pre_restore_backup_path=_pre_restore_path(receipt),
        workspace_generation=generation,
    )
    _persist_restore_result(queue_root, result, now=committed_time)
    _cleanup_finalized_restore(receipt, queue_root)
    return result


def _persist_restore_result(
    queue_root: Path,
    result: WorkspaceRestoreResult,
    *,
    now: datetime | None,
) -> None:
    _replace_atomic_json(
        queue_root / RESTORE_RESULT_FILENAME,
        {
            "format": BACKUP_FORMAT,
            "format_version": BACKUP_FORMAT_VERSION,
            "restore_id": result.restore_id,
            "backup_id": result.backup_id,
            "status": result.status,
            "backup_filename": result.backup_path.name,
            "applied_at": result.applied_at,
            "pre_restore_backup_path": (
                str(result.pre_restore_backup_path)
                if result.pre_restore_backup_path is not None
                else None
            ),
            "error": result.error,
            "failure_record_path": (
                str(result.failure_record_path)
                if result.failure_record_path is not None
                else None
            ),
            "workspace_generation": result.workspace_generation,
            "recorded_at": _utc_now(now),
        },
    )


def _read_persisted_restore_result(
    queue_root: Path,
) -> WorkspaceRestoreResult | None:
    path = queue_root / RESTORE_RESULT_FILENAME
    if not path.exists():
        return None
    raw = _read_json_file(path, "Restore result")
    if (
        raw.get("format") != BACKUP_FORMAT
        or raw.get("format_version") != BACKUP_FORMAT_VERSION
        or raw.get("status") not in {
            "staged",
            "applied",
            "failed",
            "canceled",
        }
        or not isinstance(raw.get("restore_id"), str)
        or not raw["restore_id"]
        or not isinstance(raw.get("backup_id"), str)
        or not raw["backup_id"]
        or not isinstance(raw.get("backup_filename"), str)
        or Path(raw["backup_filename"]).name != raw["backup_filename"]
        or not isinstance(raw.get("workspace_generation"), int)
        or isinstance(raw["workspace_generation"], bool)
        or raw["workspace_generation"] < INITIAL_WORKSPACE_GENERATION
    ):
        raise RestoreQueueError("Restore result is invalid.")
    return WorkspaceRestoreResult(
        restore_id=raw["restore_id"],
        backup_id=raw["backup_id"],
        status=raw["status"],
        backup_path=queue_root / raw["backup_filename"],
        applied_at=raw.get("applied_at"),
        pre_restore_backup_path=(
            Path(raw["pre_restore_backup_path"])
            if isinstance(raw.get("pre_restore_backup_path"), str)
            else None
        ),
        error=raw.get("error"),
        failure_record_path=(
            Path(raw["failure_record_path"])
            if isinstance(raw.get("failure_record_path"), str)
            else None
        ),
        workspace_generation=raw["workspace_generation"],
    )


def _read_json_file(path: Path, label: str) -> dict[str, Any]:
    if _is_link(path) or not path.is_file():
        raise RestoreQueueError(f"{label} is not a regular file.")
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RestoreQueueError(f"{label} is invalid.") from exc
    if not isinstance(parsed, dict):
        raise RestoreQueueError(f"{label} is invalid.")
    return parsed


def _build_manifest(
    *,
    created_at: str,
    app_version: str,
    backup_kind: Literal["manual", "pre_restore"],
    schema_version: int,
    source_data_dir: Path,
    entries: list[dict[str, object]],
) -> dict[str, object]:
    by_kind = Counter(str(item["kind"]) for item in entries)
    return {
        "format": BACKUP_FORMAT,
        "format_version": BACKUP_FORMAT_VERSION,
        "created_at": created_at,
        "backup_kind": backup_kind,
        "app": {
            "name": APP_NAME,
            "version": app_version,
        },
        "schema": {"version": schema_version},
        "workspace": {
            "source_data_dir": str(source_data_dir.resolve()),
            "database_path": DATABASE_ARCHIVE_PATH,
            "managed_roots": list(MANAGED_ROOTS),
            "excluded_roots": list(EXCLUDED_ROOTS),
        },
        "entries": entries,
        "counts": {
            "entries": len(entries),
            "managed_files": len(entries) - 1,
            "total_bytes": sum(int(item["size_bytes"]) for item in entries),
            "by_kind": dict(sorted(by_kind.items())),
        },
    }


def _validate_manifest(
    raw: object,
    *,
    info_by_name: Mapping[str, zipfile.ZipInfo],
    current_schema_version: int,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise BackupValidationError("Backup manifest must be a JSON object.")
    manifest = raw
    if manifest.get("format") != BACKUP_FORMAT:
        raise BackupValidationError("Unsupported backup format.")
    if manifest.get("format_version") != BACKUP_FORMAT_VERSION:
        raise BackupValidationError("Unsupported backup format version.")
    created_at = manifest.get("created_at")
    if not isinstance(created_at, str):
        raise BackupValidationError("Backup created_at is invalid.")
    _parse_datetime(created_at, "Backup created_at")
    backup_kind = manifest.get("backup_kind")
    if backup_kind not in {"manual", "pre_restore"}:
        raise BackupValidationError("Backup kind is invalid.")

    app = manifest.get("app")
    if (
        not isinstance(app, dict)
        or app.get("name") != APP_NAME
        or not isinstance(app.get("version"), str)
        or not app["version"].strip()
    ):
        raise BackupValidationError("Backup app metadata is invalid.")
    schema = manifest.get("schema")
    if not isinstance(schema, dict):
        raise BackupValidationError("Backup schema metadata is invalid.")
    schema_version = schema.get("version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version < 0
    ):
        raise BackupValidationError("Backup schema version is invalid.")
    if schema_version > current_schema_version:
        raise BackupValidationError(
            "Backup schema is newer than this application "
            f"({schema_version} > {current_schema_version})."
        )

    workspace = manifest.get("workspace")
    if not isinstance(workspace, dict):
        raise BackupValidationError("Backup workspace metadata is invalid.")
    source_data_dir = workspace.get("source_data_dir")
    if (
        not isinstance(source_data_dir, str)
        or not source_data_dir.strip()
        or "\x00" in source_data_dir
        or not _is_absolute_portable_path(source_data_dir)
    ):
        raise BackupValidationError("Backup source data directory is invalid.")
    if workspace.get("database_path") != DATABASE_ARCHIVE_PATH:
        raise BackupValidationError("Backup database path is invalid.")
    if workspace.get("managed_roots") != list(MANAGED_ROOTS):
        raise BackupValidationError("Backup managed roots are invalid.")
    if workspace.get("excluded_roots") != list(EXCLUDED_ROOTS):
        raise BackupValidationError("Backup excluded roots are invalid.")

    raw_entries = manifest.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise BackupValidationError("Backup entries are invalid.")
    entries: list[dict[str, object]] = []
    seen_paths: set[str] = set()
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            raise BackupValidationError("Backup entry must be an object.")
        entry_path = raw_entry.get("path")
        kind = raw_entry.get("kind")
        sha256 = raw_entry.get("sha256")
        size_bytes = raw_entry.get("size_bytes")
        if not isinstance(entry_path, str):
            raise BackupValidationError("Backup entry path is invalid.")
        _validate_member_name(entry_path)
        if entry_path in seen_paths:
            raise BackupValidationError(
                f"Duplicate backup entry path: {entry_path}"
            )
        seen_paths.add(entry_path)
        expected_kind = _entry_kind(entry_path)
        if kind != expected_kind:
            raise BackupValidationError(
                f"Backup entry kind is invalid: {entry_path}"
            )
        if (
            not isinstance(sha256, str)
            or len(sha256) != 64
            or any(character not in "0123456789abcdef" for character in sha256)
        ):
            raise BackupValidationError(
                f"Backup entry hash is invalid: {entry_path}"
            )
        if (
            not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or size_bytes < 0
        ):
            raise BackupValidationError(
                f"Backup entry size is invalid: {entry_path}"
            )
        info = info_by_name.get(entry_path)
        if info is None:
            raise BackupValidationError(
                f"Backup entry is missing from the archive: {entry_path}"
            )
        if info.file_size != size_bytes:
            raise BackupValidationError(
                f"Backup entry size mismatch: {entry_path}"
            )
        entries.append(
            {
                "path": entry_path,
                "kind": kind,
                "sha256": sha256,
                "size_bytes": size_bytes,
            }
        )

    database_entries = [
        entry for entry in entries if entry["kind"] == "database"
    ]
    if len(database_entries) != 1:
        raise BackupValidationError(
            "Backup must contain exactly one database entry."
        )
    archive_names = set(info_by_name)
    expected_names = seen_paths | {MANIFEST_PATH}
    if archive_names != expected_names:
        unexpected = sorted(archive_names - expected_names)
        raise BackupValidationError(
            "Backup contains undeclared entries"
            + (f": {', '.join(unexpected)}" if unexpected else ".")
        )

    counts = manifest.get("counts")
    if not isinstance(counts, dict):
        raise BackupValidationError("Backup counts are invalid.")
    expected_by_kind = dict(
        sorted(Counter(str(item["kind"]) for item in entries).items())
    )
    expected_total = sum(int(item["size_bytes"]) for item in entries)
    expected_counts = {
        "entries": len(entries),
        "managed_files": len(entries) - 1,
        "total_bytes": expected_total,
        "by_kind": expected_by_kind,
    }
    if counts != expected_counts:
        raise BackupValidationError("Backup counts do not match its entries.")

    return {
        "format": BACKUP_FORMAT,
        "format_version": BACKUP_FORMAT_VERSION,
        "created_at": created_at,
        "backup_kind": backup_kind,
        "app": {"name": APP_NAME, "version": app["version"]},
        "schema": {"version": schema_version},
        "workspace": {
            "source_data_dir": source_data_dir,
            "database_path": DATABASE_ARCHIVE_PATH,
            "managed_roots": list(MANAGED_ROOTS),
            "excluded_roots": list(EXCLUDED_ROOTS),
        },
        "entries": entries,
        "counts": expected_counts,
    }


def _validate_zip_infos(
    infos: list[zipfile.ZipInfo],
    *,
    limits: BackupLimits,
) -> None:
    if not infos:
        raise BackupValidationError("Backup archive is empty.")
    if len(infos) > limits.max_entries:
        raise BackupValidationError("Backup contains too many entries.")
    seen: set[str] = set()
    total_size = 0
    for info in infos:
        _validate_member_name(info.filename)
        if info.filename in seen:
            raise BackupValidationError(
                f"Backup contains a duplicate ZIP entry: {info.filename}"
            )
        seen.add(info.filename)
        if info.is_dir():
            raise BackupValidationError(
                f"Backup contains an unexpected directory entry: {info.filename}"
            )
        unix_mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(unix_mode):
            raise BackupValidationError(
                f"Backup contains a symbolic link: {info.filename}"
            )
        file_type = stat.S_IFMT(unix_mode)
        if file_type not in {0, stat.S_IFREG}:
            raise BackupValidationError(
                f"Backup contains a special file: {info.filename}"
            )
        if info.flag_bits & 0x1:
            raise BackupValidationError(
                f"Encrypted backup entries are unsupported: {info.filename}"
            )
        if info.compress_type not in {
            zipfile.ZIP_STORED,
            zipfile.ZIP_DEFLATED,
        }:
            raise BackupValidationError(
                f"Unsupported compression method: {info.filename}"
            )
        if info.file_size > limits.max_entry_bytes:
            raise BackupValidationError(
                f"Backup entry is too large: {info.filename}"
            )
        total_size += info.file_size
        if total_size > limits.max_uncompressed_bytes:
            raise BackupValidationError(
                "Backup exceeds the uncompressed size limit."
            )
        if info.file_size > 0:
            compressed = max(1, info.compress_size)
            if info.file_size / compressed > limits.max_expansion_ratio:
                raise BackupValidationError(
                    f"Backup entry expansion ratio is too high: {info.filename}"
                )


def _validate_member_name(name: str) -> None:
    if not name or len(name) > 512 or "\x00" in name or "\\" in name:
        raise BackupValidationError(f"Unsafe backup entry path: {name!r}")
    if name.startswith("/") or "//" in name:
        raise BackupValidationError(f"Unsafe backup entry path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or path.as_posix() != name:
        raise BackupValidationError(f"Unsafe backup entry path: {name!r}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise BackupValidationError(f"Unsafe backup entry path: {name!r}")
    for part in path.parts:
        if (
            ":" in part
            or part.endswith((" ", "."))
            or any(ord(character) < 32 for character in part)
            or _is_windows_device_name(part)
        ):
            raise BackupValidationError(f"Unsafe backup entry path: {name!r}")


def _is_windows_device_name(name: str) -> bool:
    stem = name.split(".", 1)[0].rstrip(" .").upper()
    return stem in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }


def _entry_kind(path: str) -> str:
    if path == DATABASE_ARCHIVE_PATH:
        return "database"
    parts = PurePosixPath(path).parts
    if (
        len(parts) >= 3
        and parts[0] == "workspace"
        and parts[1] in MANAGED_ROOTS
    ):
        return parts[1]
    raise BackupValidationError(f"Backup entry is outside managed roots: {path}")


def _iter_managed_files(root: Path) -> Iterator[tuple[Path, Path]]:
    if not root.exists():
        return
    if _is_link(root) or not root.is_dir():
        raise BackupValidationError(
            f"Managed workspace root is not a regular directory: {root}"
        )
    for directory, directory_names, file_names in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        current = Path(directory)
        for name in list(directory_names):
            child = current / name
            if _is_link(child):
                raise BackupValidationError(
                    f"Managed workspace contains a symbolic link: {child}"
                )
        for name in sorted(file_names):
            source = current / name
            if _is_link(source) or not source.is_file():
                raise BackupValidationError(
                    f"Managed workspace contains a special file: {source}"
                )
            yield source, source.relative_to(root)
        directory_names.sort()


def _entry_for_file(
    path: Path,
    *,
    archive_path: str,
    kind: str,
) -> dict[str, object]:
    _validate_member_name(archive_path)
    return {
        "path": archive_path,
        "kind": kind,
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _sqlite_backup(source_path: Path, destination_path: Path) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(sqlite3.connect(source_path)) as source:
            check = source.execute("PRAGMA quick_check").fetchone()
            if check is None or check[0] != "ok":
                detail = check[0] if check is not None else "no result"
                raise BackupValidationError(
                    f"Workspace database quick_check failed: {detail}"
                )
            with closing(sqlite3.connect(destination_path)) as destination:
                source.backup(destination)
    except sqlite3.DatabaseError as exc:
        destination_path.unlink(missing_ok=True)
        raise BackupValidationError(
            "Workspace database cannot be backed up."
        ) from exc
    _check_database(destination_path)


def _check_database(path: Path) -> None:
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
            check = conn.execute("PRAGMA quick_check").fetchone()
    except sqlite3.DatabaseError as exc:
        raise BackupValidationError(
            "Backup database is not a valid SQLite database."
        ) from exc
    if check is None or check[0] != "ok":
        detail = check[0] if check is not None else "no result"
        raise BackupValidationError(
            f"Backup database quick_check failed: {detail}"
        )


def _database_schema_version(path: Path) -> int:
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
            table = conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'schema_migrations'
                """
            ).fetchone()
            if table is None:
                return 0
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()
            return int(row[0]) if row is not None else 0
    except (sqlite3.DatabaseError, TypeError, ValueError) as exc:
        raise BackupValidationError(
            "Backup database schema metadata is invalid."
        ) from exc


def _validate_database_file_references(
    database_path: Path,
    *,
    source_data_dir: Path,
    archive_paths: set[str],
) -> None:
    root = source_data_dir.absolute()
    try:
        with closing(
            sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
        ) as conn:
            _validate_normalized_archive_paths(archive_paths)
            owners: dict[tuple[str, str], _ManagedPathOwner] = {}
            _validate_job_file_references(
                conn,
                source_data_dir=root,
                archive_paths=archive_paths,
                owners=owners,
            )
            _validate_source_asset_file_references(
                conn,
                source_data_dir=root,
                archive_paths=archive_paths,
                owners=owners,
            )
            _validate_trash_managed_path_ownership(
                conn,
                source_data_dir=root,
                owners=owners,
            )
    except sqlite3.DatabaseError as exc:
        raise BackupValidationError(
            "Backup database file references cannot be validated."
        ) from exc


def _validate_normalized_archive_paths(archive_paths: set[str]) -> None:
    normalized: dict[tuple[str, str], str] = {}
    for archive_path in archive_paths:
        parts = PurePosixPath(archive_path).parts
        if (
            len(parts) < 3
            or parts[0] != "workspace"
            or parts[1] not in MANAGED_ROOTS
        ):
            continue
        key = _managed_path_key(parts[1], Path(*parts[2:]))
        previous = normalized.get(key)
        if previous is not None and previous != archive_path:
            raise BackupValidationError(
                "Backup contains managed paths that collide after "
                f"normalization: {previous!r} and {archive_path!r}."
            )
        normalized[key] = archive_path


def _database_table_columns(
    conn: sqlite3.Connection,
    table: str,
) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute(
            f'PRAGMA table_info("{table}")'
        ).fetchall()
    }


def _require_database_columns(
    conn: sqlite3.Connection,
    table: str,
    required: set[str],
) -> bool:
    columns = _database_table_columns(conn, table)
    if not columns:
        return False
    if not required.issubset(columns):
        raise BackupValidationError(
            f"Backup table {table!r} cannot establish managed path ownership."
        )
    return True


def _managed_identifier(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or "\x00" in value
        or any(character in value for character in ("/", "\\"))
    ):
        raise BackupValidationError(f"{label} is invalid.")
    return value


def _managed_path_key(
    managed_root: str,
    relative: Path,
) -> tuple[str, str]:
    if managed_root not in MANAGED_ROOTS or not relative.parts:
        raise BackupValidationError("Managed file path is invalid.")
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise BackupValidationError("Managed file path is invalid.")
    portable = PurePosixPath(*relative.parts).as_posix()
    return managed_root, portable.casefold()


def _managed_archive_path(managed_root: str, relative: Path) -> str:
    return (
        PurePosixPath("workspace")
        / managed_root
        / PurePosixPath(*relative.parts)
    ).as_posix()


def _require_managed_archive_entry(
    *,
    managed_root: str,
    relative: Path,
    archive_paths: set[str],
) -> None:
    archive_path = _managed_archive_path(managed_root, relative)
    if archive_path not in archive_paths:
        raise BackupValidationError(
            "Database references a managed file that is missing from the "
            f"backup: {archive_path}"
        )


def _register_record_owner(
    owners: dict[tuple[str, str], _ManagedPathOwner],
    *,
    managed_root: str,
    relative: Path,
    owner: _ManagedPathOwner,
) -> tuple[str, str]:
    key = _managed_path_key(managed_root, relative)
    previous = owners.get(key)
    if previous is not None:
        raise BackupValidationError(
            "A normalized managed path is referenced by different records: "
            f"{_managed_archive_path(managed_root, relative)}."
        )
    owners[key] = owner
    return key


def _validate_job_file_references(
    conn: sqlite3.Connection,
    *,
    source_data_dir: Path,
    archive_paths: set[str],
    owners: dict[tuple[str, str], _ManagedPathOwner],
) -> None:
    required = {"id", "course_id", "video_path", "transcript_path"}
    if not _require_database_columns(conn, "jobs", required):
        return
    for job_id_value, course_id_value, video_value, transcript_value in (
        conn.execute(
            """
            SELECT id, course_id, video_path, transcript_path
            FROM jobs
            ORDER BY rowid
            """
        ).fetchall()
    ):
        job_id = _managed_identifier(job_id_value, label="Job id")
        course_id = _managed_identifier(
            course_id_value,
            label="Job course id",
        )
        if not isinstance(video_value, str) or not video_value.strip():
            raise BackupValidationError("Database path jobs.video_path is invalid.")
        video_relative = _relative_managed_path(
            video_value,
            source_data_dir=source_data_dir,
            expected_root="uploads",
        )
        if (
            len(video_relative.parts) != 1
            or video_relative.stem != job_id
            or video_relative.suffix.lower() not in ENTITY_VIDEO_EXTENSIONS
        ):
            raise BackupValidationError(
                "Job video path is outside the job's managed namespace."
            )
        owner = _ManagedPathOwner(
            entity_type="video_job",
            entity_id=job_id,
            course_id=course_id,
        )
        _register_record_owner(
            owners,
            managed_root="uploads",
            relative=video_relative,
            owner=owner,
        )
        _require_managed_archive_entry(
            managed_root="uploads",
            relative=video_relative,
            archive_paths=archive_paths,
        )

        audio_relative = Path(f"{job_id}.wav")
        _register_record_owner(
            owners,
            managed_root="audio",
            relative=audio_relative,
            owner=owner,
        )

        if transcript_value is None:
            continue
        if (
            not isinstance(transcript_value, str)
            or not transcript_value.strip()
        ):
            raise BackupValidationError(
                "Database path jobs.transcript_path is invalid."
            )
        transcript_relative = _relative_managed_path(
            transcript_value,
            source_data_dir=source_data_dir,
            expected_root="transcripts",
        )
        if transcript_relative.parts != (f"{job_id}.json",):
            raise BackupValidationError(
                "Job transcript path is outside the job's managed namespace."
            )
        _register_record_owner(
            owners,
            managed_root="transcripts",
            relative=transcript_relative,
            owner=owner,
        )
        _require_managed_archive_entry(
            managed_root="transcripts",
            relative=transcript_relative,
            archive_paths=archive_paths,
        )


def _validate_source_asset_file_references(
    conn: sqlite3.Connection,
    *,
    source_data_dir: Path,
    archive_paths: set[str],
    owners: dict[tuple[str, str], _ManagedPathOwner],
) -> None:
    required = {"id", "course_id", "stored_path"}
    if not _require_database_columns(conn, "source_assets", required):
        return
    rows = conn.execute(
        """
        SELECT id, course_id, stored_path
        FROM source_assets
        ORDER BY rowid
        """
    ).fetchall()
    for asset_id_value, course_id_value, stored_value in rows:
        asset_id = _managed_identifier(
            asset_id_value,
            label="Source asset id",
        )
        course_id = _managed_identifier(
            course_id_value,
            label="Source asset course id",
        )
        if not isinstance(stored_value, str) or not stored_value.strip():
            raise BackupValidationError(
                "Database path source_assets.stored_path is invalid."
            )
        relative = _relative_managed_path(
            stored_value,
            source_data_dir=source_data_dir,
            expected_root="sources",
        )
        if (
            len(relative.parts) != 2
            or relative.parts[0] != course_id
            or relative.stem != asset_id
            or relative.suffix.lower() not in ENTITY_SOURCE_EXTENSIONS
        ):
            raise BackupValidationError(
                "Source asset path is outside the asset's managed namespace."
            )
        owner = _ManagedPathOwner(
            entity_type="source_asset",
            entity_id=asset_id,
            course_id=course_id,
        )
        _register_record_owner(
            owners,
            managed_root="sources",
            relative=relative,
            owner=owner,
        )
        _require_managed_archive_entry(
            managed_root="sources",
            relative=relative,
            archive_paths=archive_paths,
        )


def _validate_trash_managed_path_ownership(
    conn: sqlite3.Connection,
    *,
    source_data_dir: Path,
    owners: dict[tuple[str, str], _ManagedPathOwner],
) -> None:
    required = {
        "id",
        "entity_type",
        "entity_id",
        "course_id",
        "metadata_json",
    }
    if not _require_database_columns(conn, "trash_items", required):
        return

    entity_claims: dict[tuple[str, str], _ManagedPathOwner] = {}
    course_claims: dict[tuple[str, str], str] = {}
    rows = conn.execute(
        """
        SELECT entity_type, entity_id, course_id, metadata_json
        FROM trash_items
        ORDER BY id
        """
    ).fetchall()
    for entity_type, entity_id_value, course_id_value, metadata_json in rows:
        if not isinstance(metadata_json, str):
            raise BackupValidationError("Trash recovery metadata is invalid.")
        try:
            metadata = json.loads(metadata_json)
        except (TypeError, ValueError) as exc:
            raise BackupValidationError(
                "Trash recovery metadata is invalid."
            ) from exc
        if not isinstance(metadata, dict):
            raise BackupValidationError("Trash recovery metadata is invalid.")

        if ENTITY_PURGE_METADATA_KEY in metadata:
            entity_id = _managed_identifier(
                entity_id_value,
                label="Trash entity id",
            )
            course_id = _managed_identifier(
                course_id_value,
                label="Trash entity course id",
            )
            plan = metadata[ENTITY_PURGE_METADATA_KEY]
            if (
                not isinstance(plan, dict)
                or type(plan.get("version")) is not int
            ):
                raise BackupValidationError(
                    "Entity purge recovery plan version is invalid."
                )
            try:
                validated = validate_entity_purge_plan(
                    plan,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    course_id=course_id,
                )
            except ValueError as exc:
                raise BackupValidationError(str(exc)) from exc
            artifacts = validated["artifacts"]
            assert isinstance(artifacts, list)
            plan_owner = _ManagedPathOwner(
                entity_type=str(entity_type),
                entity_id=entity_id,
                course_id=course_id,
            )
            plan_keys: set[tuple[str, str]] = set()
            for artifact in artifacts:
                assert isinstance(artifact, dict)
                managed_root = str(artifact["root"])
                relative = Path(
                    *PurePosixPath(
                        str(artifact["relative_path"])
                    ).parts
                )
                key = _managed_path_key(managed_root, relative)
                if key in plan_keys:
                    raise BackupValidationError(
                        "Entity purge recovery plan contains duplicate "
                        "normalized managed paths."
                    )
                _claim_entity_purge_path(
                    key,
                    plan_owner=plan_owner,
                    record_owners=owners,
                    entity_claims=entity_claims,
                    course_claims=course_claims,
                )
                plan_keys.add(key)

            record_keys = {
                key
                for key, owner in owners.items()
                if owner == plan_owner
            }
            if record_keys and plan_keys != record_keys:
                raise BackupValidationError(
                    "Entity purge recovery plan does not match its entity "
                    "record."
                )

        if COURSE_PURGE_METADATA_KEY in metadata:
            course_id = _managed_identifier(
                entity_id_value,
                label="Course trash entity id",
            )
            rebased = _rebase_course_purge_plan(
                metadata[COURSE_PURGE_METADATA_KEY],
                old_data_dir=source_data_dir,
                new_data_dir=source_data_dir,
                expected_course_id=course_id,
            )
            if entity_type != "course":
                raise BackupValidationError(
                    "Course purge recovery metadata belongs to an invalid "
                    "entity."
                )
            artifacts = rebased["artifacts"]
            phase = rebased["phase"]
            assert isinstance(artifacts, list)
            assert isinstance(phase, str)
            plan_keys: set[tuple[str, str]] = set()
            for artifact in artifacts:
                assert isinstance(artifact, dict)
                parts = _relative_workspace_parts(
                    str(artifact["path"]),
                    source_data_dir=source_data_dir,
                )
                managed_root = parts[0]
                key = _managed_path_key(
                    managed_root,
                    Path(*parts[1:]),
                )
                if key in plan_keys:
                    raise BackupValidationError(
                        "Course purge recovery plan contains duplicate "
                        "normalized managed paths."
                    )
                _claim_course_purge_path(
                    key,
                    course_id=course_id,
                    record_owners=owners,
                    entity_claims=entity_claims,
                    course_claims=course_claims,
                )
                plan_keys.add(key)

            if COURSE_PURGE_PHASES.index(phase) <= (
                COURSE_PURGE_PHASES.index("artifacts")
            ):
                expected_keys = {
                    key
                    for key, owner in owners.items()
                    if owner.course_id == course_id
                }
                if plan_keys != expected_keys:
                    raise BackupValidationError(
                        "Course purge recovery plan does not match the course "
                        "records."
                    )


def _claim_entity_purge_path(
    key: tuple[str, str],
    *,
    plan_owner: _ManagedPathOwner,
    record_owners: dict[tuple[str, str], _ManagedPathOwner],
    entity_claims: dict[tuple[str, str], _ManagedPathOwner],
    course_claims: dict[tuple[str, str], str],
) -> None:
    record_owner = record_owners.get(key)
    if record_owner is not None and record_owner != plan_owner:
        raise BackupValidationError(
            "Entity purge recovery plan references another entity's managed "
            "path."
        )
    previous_entity = entity_claims.get(key)
    if previous_entity is not None:
        raise BackupValidationError(
            "Different entity purge plans reference the same managed path."
        )
    claimed_course = course_claims.get(key)
    if claimed_course is not None and claimed_course != plan_owner.course_id:
        raise BackupValidationError(
            "Entity and course purge plans share a managed path across "
            "courses."
        )
    entity_claims[key] = plan_owner


def _claim_course_purge_path(
    key: tuple[str, str],
    *,
    course_id: str,
    record_owners: dict[tuple[str, str], _ManagedPathOwner],
    entity_claims: dict[tuple[str, str], _ManagedPathOwner],
    course_claims: dict[tuple[str, str], str],
) -> None:
    record_owner = record_owners.get(key)
    if record_owner is not None and record_owner.course_id != course_id:
        raise BackupValidationError(
            "Course purge recovery plan references another course's managed "
            "path."
        )
    entity_owner = entity_claims.get(key)
    if entity_owner is not None and entity_owner.course_id != course_id:
        raise BackupValidationError(
            "Course and entity purge plans share a managed path across "
            "courses."
        )
    previous_course = course_claims.get(key)
    if previous_course is not None:
        raise BackupValidationError(
            "Different course purge plans reference the same managed path."
        )
    course_claims[key] = course_id


def _relative_managed_path(
    value: str,
    *,
    source_data_dir: Path,
    expected_root: str,
) -> Path:
    source_text = str(source_data_dir)
    try:
        if _looks_like_windows_path(value) or _looks_like_windows_path(
            source_text
        ):
            candidate = PureWindowsPath(value)
            base = PureWindowsPath(source_text) / expected_root
            relative_parts = candidate.relative_to(base).parts
        else:
            candidate = Path(value)
            base = source_data_dir / expected_root
            relative_parts = candidate.relative_to(base).parts
    except ValueError as exc:
        raise BackupValidationError(
            "Database references a path outside the managed workspace: "
            f"{value}"
        ) from exc
    if not relative_parts:
        raise BackupValidationError(f"Database file path is invalid: {value}")
    if any(part in {"", ".", ".."} for part in relative_parts):
        raise BackupValidationError(f"Database file path is invalid: {value}")
    return Path(*relative_parts)


def _rebase_database_paths(
    database_path: Path,
    *,
    old_data_dir: Path,
    new_data_dir: Path,
) -> None:
    replacements = (
        ("jobs", "video_path", "uploads"),
        ("jobs", "transcript_path", "transcripts"),
        ("source_assets", "stored_path", "sources"),
    )
    try:
        with closing(sqlite3.connect(database_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            for table, column, managed_root in replacements:
                if not _table_has_column(conn, table, column):
                    continue
                rows = conn.execute(
                    f'SELECT rowid, "{column}" FROM "{table}" '
                    f'WHERE "{column}" IS NOT NULL'
                ).fetchall()
                for rowid, value in rows:
                    if not isinstance(value, str) or not value.strip():
                        raise BackupValidationError(
                            f"Database path {table}.{column} is invalid."
                        )
                    relative = _relative_managed_path(
                        value,
                        source_data_dir=old_data_dir,
                        expected_root=managed_root,
                    )
                    rebased = new_data_dir / managed_root / relative
                    conn.execute(
                        f'UPDATE "{table}" SET "{column}" = ? WHERE rowid = ?',
                        (str(rebased), rowid),
                    )
            _rebase_and_validate_trash_purge_plans(
                conn,
                old_data_dir=old_data_dir,
                new_data_dir=new_data_dir,
            )
            conn.commit()
    except Exception:
        try:
            conn.rollback()
        except (NameError, sqlite3.Error):
            pass
        raise


def _rebase_and_validate_trash_purge_plans(
    conn: sqlite3.Connection,
    *,
    old_data_dir: Path,
    new_data_dir: Path,
) -> None:
    """Rebase course plans and reject unsafe entity purge journals."""

    table_exists = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'trash_items'
        """
    ).fetchone()
    if table_exists is None:
        return

    required_columns = {
        "entity_type",
        "entity_id",
        "course_id",
        "metadata_json",
    }
    actual_columns = {
        str(row[1])
        for row in conn.execute(
            'PRAGMA table_info("trash_items")'
        ).fetchall()
    }
    if not required_columns.issubset(actual_columns):
        raise BackupValidationError(
            "Trash recovery metadata cannot be safely rebased."
        )

    rows = conn.execute(
        """
        SELECT rowid, entity_type, entity_id, course_id, metadata_json
        FROM trash_items
        """
    ).fetchall()
    for rowid, entity_type, entity_id, course_id, metadata_json in rows:
        if not isinstance(metadata_json, str):
            raise BackupValidationError(
                "Trash recovery metadata is invalid."
            )
        try:
            metadata = json.loads(metadata_json)
        except (TypeError, ValueError) as exc:
            raise BackupValidationError(
                "Trash recovery metadata is invalid."
            ) from exc
        if not isinstance(metadata, dict):
            raise BackupValidationError(
                "Trash recovery metadata is invalid."
            )
        has_course_plan = COURSE_PURGE_METADATA_KEY in metadata
        has_entity_plan = ENTITY_PURGE_METADATA_KEY in metadata
        if not has_course_plan and not has_entity_plan:
            continue
        rebased_metadata = dict(metadata)
        if has_course_plan:
            if entity_type != "course":
                raise BackupValidationError(
                    "Course purge recovery metadata belongs to an invalid "
                    "entity."
                )
            plan = metadata[COURSE_PURGE_METADATA_KEY]
            rebased_metadata[COURSE_PURGE_METADATA_KEY] = (
                _rebase_course_purge_plan(
                    plan,
                    old_data_dir=old_data_dir,
                    new_data_dir=new_data_dir,
                    expected_course_id=entity_id,
                )
            )
            _validate_course_purge_plan_records(
                conn,
                rebased_metadata[COURSE_PURGE_METADATA_KEY],
                new_data_dir=new_data_dir,
            )
        if has_entity_plan:
            if entity_type not in ENTITY_PURGE_TYPES:
                raise BackupValidationError(
                    "Entity purge recovery metadata belongs to an invalid "
                    "entity."
                )
            try:
                validate_entity_purge_plan(
                    metadata[ENTITY_PURGE_METADATA_KEY],
                    entity_type=entity_type,
                    entity_id=entity_id,
                    course_id=course_id,
                )
            except ValueError as exc:
                raise BackupValidationError(str(exc)) from exc
        conn.execute(
            """
            UPDATE trash_items
            SET metadata_json = ?
            WHERE rowid = ?
            """,
            (
                json.dumps(rebased_metadata, ensure_ascii=False),
                rowid,
            ),
        )


def _rebase_course_purge_plan(
    value: object,
    *,
    old_data_dir: Path,
    new_data_dir: Path,
    expected_course_id: object,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != COURSE_PURGE_PLAN_FIELDS:
        raise BackupValidationError(
            "Course purge recovery plan has unsupported fields."
        )
    version = value.get("version")
    course_id = value.get("course_id")
    phase = value.get("phase")
    artifacts = value.get("artifacts")
    if (
        type(version) is not int
        or version != COURSE_PURGE_PLAN_VERSION
        or not isinstance(course_id, str)
        or not course_id
        or course_id != expected_course_id
        or any(character in course_id for character in ("/", "\\"))
        or not isinstance(phase, str)
        or phase not in COURSE_PURGE_PHASES
        or not isinstance(artifacts, list)
    ):
        raise BackupValidationError(
            "Course purge recovery plan is invalid."
        )

    rebased_artifacts: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for artifact in artifacts:
        if (
            not isinstance(artifact, dict)
            or set(artifact) != COURSE_PURGE_ARTIFACT_FIELDS
        ):
            raise BackupValidationError(
                "Course purge artifact has unsupported fields."
            )
        path = artifact.get("path")
        root = artifact.get("root")
        if (
            not isinstance(path, str)
            or not path.strip()
            or "\x00" in path
            or not isinstance(root, str)
            or not root.strip()
            or "\x00" in root
        ):
            raise BackupValidationError(
                "Course purge artifact path is invalid."
            )

        path_parts = _relative_workspace_parts(
            path,
            source_data_dir=old_data_dir,
        )
        if (
            len(path_parts) < 2
            or path_parts[0] not in COURSE_PURGE_MANAGED_ROOTS
        ):
            raise BackupValidationError(
                "Course purge artifact is outside a managed workspace root."
            )
        managed_root = path_parts[0]
        root_parts = _relative_workspace_parts(
            root,
            source_data_dir=old_data_dir,
        )
        if root_parts != (managed_root,):
            raise BackupValidationError(
                "Course purge artifact root is outside its managed workspace "
                "root."
            )
        relative_parts = path_parts[1:]
        if (
            managed_root == "sources"
            and (
                len(relative_parts) != 2
                or relative_parts[0] != course_id
            )
        ) or (
            managed_root != "sources"
            and len(relative_parts) != 1
        ):
            raise BackupValidationError(
                "Course purge artifact is outside the course namespace."
            )

        rebased = {
            "path": str(new_data_dir.joinpath(*path_parts)),
            "root": str(new_data_dir / managed_root),
        }
        identity = (rebased["path"], rebased["root"])
        if identity in seen:
            raise BackupValidationError(
                "Course purge recovery plan contains duplicate artifacts."
            )
        seen.add(identity)
        rebased_artifacts.append(rebased)

    return {
        "version": version,
        "course_id": course_id,
        "phase": phase,
        "artifacts": rebased_artifacts,
    }


def _validate_course_purge_plan_records(
    conn: sqlite3.Connection,
    value: object,
    *,
    new_data_dir: Path,
) -> None:
    """Bind a still-actionable course artifact plan to its persisted rows."""

    if not isinstance(value, dict):
        raise BackupValidationError(
            "Course purge recovery plan is invalid."
        )
    phase = value.get("phase")
    if (
        not isinstance(phase, str)
        or phase not in COURSE_PURGE_PHASES
    ):
        raise BackupValidationError(
            "Course purge recovery plan is invalid."
        )
    if COURSE_PURGE_PHASES.index(phase) > COURSE_PURGE_PHASES.index(
        "artifacts"
    ):
        # Artifact deletion is already durably complete; later phases never
        # consult the file list again.
        return

    course_id = value.get("course_id")
    artifacts = value.get("artifacts")
    if not isinstance(course_id, str) or not isinstance(artifacts, list):
        raise BackupValidationError(
            "Course purge recovery plan is invalid."
        )
    for table, required in (
        (
            "jobs",
            {"id", "course_id", "video_path", "transcript_path"},
        ),
        (
            "source_assets",
            {"id", "course_id", "stored_path"},
        ),
    ):
        columns = {
            str(row[1])
            for row in conn.execute(
                f'PRAGMA table_info("{table}")'
            ).fetchall()
        }
        if not required.issubset(columns):
            raise BackupValidationError(
                "Course purge records cannot be safely validated."
            )

    expected: set[tuple[str, str]] = set()

    def add(path: Path, managed_root: str) -> None:
        expected.add(
            (
                str(path.resolve()),
                str((new_data_dir / managed_root).resolve()),
            )
        )

    job_rows = conn.execute(
        """
        SELECT id, video_path, transcript_path
        FROM jobs
        WHERE course_id = ?
        """,
        (course_id,),
    ).fetchall()
    for job_id, video_path, transcript_path in job_rows:
        if (
            not isinstance(job_id, str)
            or not isinstance(video_path, str)
            or not video_path
        ):
            raise BackupValidationError(
                "Course purge job records are invalid."
            )
        add(Path(video_path), "uploads")
        if transcript_path is not None:
            if not isinstance(transcript_path, str) or not transcript_path:
                raise BackupValidationError(
                    "Course purge job records are invalid."
                )
            add(Path(transcript_path), "transcripts")
        add(
            new_data_dir
            / "audio"
            / f"{Path(video_path).stem}.wav",
            "audio",
        )

    asset_rows = conn.execute(
        """
        SELECT id, stored_path
        FROM source_assets
        WHERE course_id = ?
        """,
        (course_id,),
    ).fetchall()
    for asset_id, stored_path in asset_rows:
        if (
            not isinstance(asset_id, str)
            or not isinstance(stored_path, str)
            or not stored_path
        ):
            raise BackupValidationError(
                "Course purge source records are invalid."
            )
        add(Path(stored_path), "sources")

    actual = {
        (
            str(Path(str(artifact["path"])).resolve()),
            str(Path(str(artifact["root"])).resolve()),
        )
        for artifact in artifacts
        if isinstance(artifact, dict)
    }
    if actual != expected:
        raise BackupValidationError(
            "Course purge recovery plan does not match the course records."
        )


def _relative_workspace_parts(
    value: str,
    *,
    source_data_dir: Path,
) -> tuple[str, ...]:
    source_text = str(source_data_dir)
    try:
        if _looks_like_windows_path(value) or _looks_like_windows_path(
            source_text
        ):
            candidate = PureWindowsPath(value)
            base = PureWindowsPath(source_text)
        else:
            candidate = PurePosixPath(value)
            base = PurePosixPath(source_text)
        if not candidate.is_absolute() or not base.is_absolute():
            raise ValueError
        relative_parts = candidate.relative_to(base).parts
    except ValueError as exc:
        raise BackupValidationError(
            "Course purge recovery plan references a path outside the "
            f"workspace: {value}"
        ) from exc
    if any(part in {"", ".", ".."} for part in relative_parts):
        raise BackupValidationError(
            f"Course purge recovery plan path is invalid: {value}"
        )
    return tuple(relative_parts)


def _table_has_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
) -> bool:
    table_row = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table,),
    ).fetchone()
    if table_row is None:
        return False
    return column in {
        str(row[1])
        for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    }


def _looks_like_windows_path(value: str) -> bool:
    return len(value) >= 3 and value[1:3] in {":\\", ":/"}


def _is_absolute_portable_path(value: str) -> bool:
    return (
        PureWindowsPath(value).is_absolute()
        or PurePosixPath(value).is_absolute()
    )


def _extract_validated_backup(
    validated: ValidatedWorkspaceBackup,
    destination_root: Path,
) -> None:
    with zipfile.ZipFile(validated.path, mode="r") as archive:
        info_by_name = {info.filename: info for info in archive.infolist()}
        for entry in validated.manifest["entries"]:
            archive_path = str(entry["path"])
            destination = destination_root.joinpath(
                *PurePosixPath(archive_path).parts
            )
            _copy_zip_entry(
                archive,
                info_by_name[archive_path],
                destination,
            )
    for root_name in MANAGED_ROOTS:
        (destination_root / "workspace" / root_name).mkdir(
            parents=True,
            exist_ok=True,
        )


def _rollback_workspace_swap(
    swap_state: list[
        tuple[_RestoreReplacement, Path | None]
    ],
    *,
    transaction_root: Path | None,
) -> str | None:
    if not swap_state or transaction_root is None:
        return None
    failed_root = transaction_root / "failed"
    errors: list[str] = []
    state_by_name = {
        replacement.name: (replacement, previous)
        for replacement, previous in swap_state
    }
    ordered_state = [
        state_by_name[replacement.name]
        for replacement in _rollback_replacement_order(
            [replacement for replacement, _ in swap_state]
        )
    ]
    for index, (replacement, previous) in enumerate(ordered_state):
        try:
            if replacement.target.exists():
                displaced = (
                    failed_root
                    / f"{index}-{replacement.target.name}"
                )
                displaced.parent.mkdir(parents=True, exist_ok=True)
                _move_path(replacement.target, displaced)
            if previous is not None and previous.exists():
                replacement.target.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                _move_path(previous, replacement.target)
        except OSError as exc:
            errors.append(f"{replacement.target}: {exc}")
    return "; ".join(errors) if errors else None


def _quarantine_failed_restore(
    *,
    marker_path: Path,
    backup_path: Path,
    queue_root: Path,
    error: str,
    now: datetime | None,
) -> Path | None:
    failed_root = queue_root / "failed"
    failed_root.mkdir(parents=True, exist_ok=True)
    failure_id = f"{_filename_stamp(now)}-{uuid4().hex[:8]}"
    archived_backup: str | None = None
    if backup_path.is_file() and backup_path.parent == queue_root:
        failed_backup = failed_root / f"{failure_id}{BACKUP_EXTENSION}"
        try:
            _move_path(backup_path, failed_backup)
            archived_backup = failed_backup.name
        except OSError:
            archived_backup = backup_path.name
    record_path = failed_root / f"{failure_id}.json"
    record = {
        "format": BACKUP_FORMAT,
        "format_version": BACKUP_FORMAT_VERSION,
        "failed_at": _utc_now(now),
        "error": error,
        "backup_filename": archived_backup,
    }
    try:
        _write_atomic_json(record_path, record)
        marker_path.unlink(missing_ok=True)
        return record_path
    except OSError:
        return None


def _read_restore_marker(
    marker_path: Path,
    queue_root: Path,
) -> dict[str, Any]:
    if _is_link(marker_path) or not marker_path.is_file():
        raise RestoreQueueError("Pending restore marker is not a regular file.")
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RestoreQueueError("Pending restore marker is invalid.") from exc
    if (
        not isinstance(marker, dict)
        or marker.get("format") != BACKUP_FORMAT
        or marker.get("format_version") != BACKUP_FORMAT_VERSION
    ):
        raise RestoreQueueError("Pending restore marker is invalid.")
    filename = marker.get("backup_filename")
    restore_id = marker.get("restore_id")
    backup_id = marker.get("backup_id")
    sha256 = marker.get("backup_sha256")
    queued_at = marker.get("queued_at")
    schema_version = marker.get("schema_version")
    workspace_generation = marker.get("workspace_generation")
    if (
        not isinstance(restore_id, str)
        or not restore_id
        or len(restore_id) > 128
        or not isinstance(backup_id, str)
        or Path(backup_id).name != backup_id
        or not backup_id.endswith(BACKUP_EXTENSION)
        or not isinstance(filename, str)
        or Path(filename).name != filename
        or not filename.endswith(BACKUP_EXTENSION)
        or not isinstance(sha256, str)
        or len(sha256) != 64
        or any(character not in "0123456789abcdef" for character in sha256)
        or not isinstance(queued_at, str)
        or not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version < 0
        or not isinstance(workspace_generation, int)
        or isinstance(workspace_generation, bool)
        or workspace_generation < INITIAL_WORKSPACE_GENERATION
    ):
        raise RestoreQueueError("Pending restore marker is invalid.")
    _parse_datetime(queued_at, "Pending restore queued_at")
    backup_path = queue_root / filename
    if backup_path.parent != queue_root:
        raise RestoreQueueError("Pending restore path is unsafe.")
    return {
        "restore_id": restore_id,
        "backup_id": backup_id,
        "backup_filename": filename,
        "backup_sha256": sha256,
        "queued_at": queued_at,
        "schema_version": schema_version,
        "workspace_generation": workspace_generation,
    }


def _write_atomic_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical_json(value)
    with path.open("xb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())


def _replace_atomic_json(
    path: Path,
    value: Mapping[str, object],
) -> None:
    temporary_path = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        _write_atomic_json(temporary_path, value)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _write_zip_bytes(
    archive: zipfile.ZipFile,
    name: str,
    value: bytes,
    *,
    compression: int,
) -> None:
    info = zipfile.ZipInfo(
        filename=name,
        date_time=(1980, 1, 1, 0, 0, 0),
    )
    info.compress_type = compression
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    archive.writestr(info, value)


def _copy_zip_entry(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    destination: Path,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(info, mode="r") as source, destination.open("xb") as output:
        shutil.copyfileobj(source, output, length=1024 * 1024)


def _copy_regular_file(source: Path, destination: Path) -> None:
    if _is_link(source) or not source.is_file():
        raise BackupValidationError(f"File is not regular: {source}")
    before = source.stat()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_file, destination.open("xb") as output:
        shutil.copyfileobj(input_file, output, length=1024 * 1024)
        output.flush()
        os.fsync(output.fileno())
    after = source.stat()
    if (
        _is_link(source)
        or before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        destination.unlink(missing_ok=True)
        raise BackupValidationError(
            f"File changed while it was being backed up: {source}"
        )


def _require_regular_file(path: Path, *, label: str) -> Path:
    candidate = Path(path)
    if _is_link(candidate) or not candidate.is_file():
        raise BackupValidationError(f"{label} is not a regular file: {path}")
    return candidate.resolve()


def _require_directory(path: Path, *, create: bool) -> Path:
    candidate = Path(path)
    if create:
        candidate.mkdir(parents=True, exist_ok=True)
    if _is_link(candidate) or not candidate.is_dir():
        raise WorkspaceBackupError(
            f"Workspace path is not a regular directory: {path}"
        )
    return candidate.resolve()


def _ensure_replaceable_target(path: Path) -> None:
    if _is_link(path):
        raise BackupValidationError(
            f"Restore target cannot be a symbolic link: {path}"
        )
    if path.exists() and not (path.is_file() or path.is_dir()):
        raise BackupValidationError(
            f"Restore target is a special file: {path}"
        )


def _ensure_control_directory_outside_managed(
    path: Path,
    *,
    data_dir: Path,
    label: str,
) -> None:
    for root_name in MANAGED_ROOTS:
        managed_root = (data_dir / root_name).resolve()
        try:
            path.relative_to(managed_root)
        except ValueError:
            continue
        raise WorkspaceBackupError(
            f"{label} cannot be inside managed workspace root "
            f"{root_name!r}."
        )


def _ensure_database_outside_managed(
    path: Path,
    *,
    data_dir: Path,
) -> None:
    resolved = path.resolve(strict=False)
    for root_name in MANAGED_ROOTS:
        managed_root = (data_dir / root_name).resolve()
        try:
            resolved.relative_to(managed_root)
        except ValueError:
            continue
        raise WorkspaceBackupError(
            "Workspace database cannot be inside a managed file root."
        )


def _move_path(source: Path, destination: Path) -> None:
    os.replace(source, destination)


def _safe_mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return -1


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction()) if callable(is_junction) else False


def _compression_for(path: Path) -> int:
    if path.suffix.lower() in {
        ".mp4",
        ".mov",
        ".mkv",
        ".webm",
        ".mp3",
        ".wav",
        ".pdf",
        ".pptx",
        ".docx",
        ".zip",
    }:
        return zipfile.ZIP_STORED
    return zipfile.ZIP_DEFLATED


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_zip_entry(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
) -> str:
    digest = hashlib.sha256()
    with archive.open(info, mode="r") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _utc_now(value: datetime | None) -> str:
    timestamp = value or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).isoformat()


def _filename_stamp(value: datetime | None) -> str:
    timestamp = value or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _parse_datetime(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BackupValidationError(f"{label} is invalid.") from exc
    if parsed.tzinfo is None:
        raise BackupValidationError(f"{label} must include a timezone.")
    return parsed
