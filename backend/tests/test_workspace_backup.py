from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import trash_service, workspace_backup
from app.trash_service import TrashOperationError
from app.workspace_backup import (
    BACKUP_EXTENSION,
    BackupLimits,
    BackupValidationError,
    RestoreQueueError,
    apply_pending_workspace_restore,
    cancel_pending_workspace_restore,
    create_workspace_backup,
    finalize_pending_workspace_restore,
    get_pending_workspace_restore,
    get_workspace_restore_state,
    list_workspace_backups,
    queue_workspace_restore,
    rollback_pending_workspace_restore,
    validate_workspace_backup,
)


SCHEMA_VERSION = 4
FIXED_TIME = datetime(2026, 7, 27, 12, 30, tzinfo=timezone.utc)


def _workspace(
    root: Path,
    *,
    label: str,
    schema_version: int = SCHEMA_VERSION,
) -> tuple[Path, Path]:
    data_dir = root / label
    db_path = data_dir / "data" / "jobs.db"
    for directory in (
        "uploads",
        "audio",
        "transcripts",
        "sources/course-1",
        "backups",
        "restore",
        "logs",
        "exports",
    ):
        (data_dir / directory).mkdir(parents=True, exist_ok=True)

    video_path = data_dir / "uploads" / "video-1.mp4"
    transcript_path = data_dir / "transcripts" / "video-1.json"
    source_path = data_dir / "sources" / "course-1" / "source-1.pdf"
    video_path.write_bytes(f"{label}-video".encode())
    (data_dir / "audio" / "video-1.wav").write_bytes(
        f"{label}-audio".encode()
    )
    transcript_path.write_text(
        json.dumps({"label": label}),
        encoding="utf-8",
    )
    source_path.write_bytes(f"{label}-source".encode())
    (data_dir / "logs" / "backend.log").write_text("excluded log")
    (data_dir / "exports" / "cards.md").write_text("excluded export")
    (data_dir / "backups" / "nested.db").write_bytes(b"excluded backup")
    (data_dir / "restore" / "pending.tmp").write_bytes(b"excluded restore")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,
                course_id TEXT,
                video_path TEXT NOT NULL,
                transcript_path TEXT
            );
            CREATE TABLE source_assets (
                id TEXT PRIMARY KEY,
                course_id TEXT,
                stored_path TEXT NOT NULL
            );
            CREATE TABLE workspace_marker (
                label TEXT NOT NULL
            );
            """
        )
        for version in range(1, schema_version + 1):
            conn.execute(
                """
                INSERT INTO schema_migrations (version, name, applied_at)
                VALUES (?, ?, ?)
                """,
                (version, f"migration-{version}", FIXED_TIME.isoformat()),
            )
        conn.execute(
            """
            INSERT INTO jobs (
                id,
                course_id,
                video_path,
                transcript_path
            )
            VALUES (?, 'course-1', ?, ?)
            """,
            ("video-1", str(video_path.resolve()), str(transcript_path.resolve())),
        )
        conn.execute(
            """
            INSERT INTO source_assets (id, course_id, stored_path)
            VALUES (?, 'course-1', ?)
            """,
            ("source-1", str(source_path.resolve())),
        )
        conn.execute(
            "INSERT INTO workspace_marker (label) VALUES (?)",
            (label,),
        )
        conn.commit()
    return data_dir, db_path


def _read_zip(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path, mode="r") as archive:
        return {
            info.filename: archive.read(info)
            for info in archive.infolist()
        }


def _rewrite_zip(path: Path, values: dict[str, bytes]) -> None:
    temporary = path.with_suffix(".rewrite")
    with zipfile.ZipFile(temporary, mode="w") as archive:
        for name, value in values.items():
            archive.writestr(name, value)
    os.replace(temporary, path)


def _database_from_backup(backup_path: Path, destination: Path) -> Path:
    with zipfile.ZipFile(backup_path, mode="r") as archive:
        destination.write_bytes(
            archive.read(workspace_backup.DATABASE_ARCHIVE_PATH)
        )
    return destination


def _replace_backup_database(
    backup_path: Path,
    database_path: Path,
) -> None:
    contents = _read_zip(backup_path)
    database_bytes = database_path.read_bytes()
    manifest = json.loads(contents[workspace_backup.MANIFEST_PATH])
    database_entry = next(
        entry
        for entry in manifest["entries"]
        if entry["path"] == workspace_backup.DATABASE_ARCHIVE_PATH
    )
    database_entry["size_bytes"] = len(database_bytes)
    database_entry["sha256"] = hashlib.sha256(database_bytes).hexdigest()
    manifest["counts"]["total_bytes"] = sum(
        entry["size_bytes"] for entry in manifest["entries"]
    )
    contents[workspace_backup.DATABASE_ARCHIVE_PATH] = database_bytes
    contents[workspace_backup.MANIFEST_PATH] = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    _rewrite_zip(backup_path, contents)


def _add_course_purge_plan(
    db_path: Path,
    data_dir: Path,
    *,
    artifacts: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    plan: dict[str, object] = {
        "version": trash_service.COURSE_PURGE_PLAN_VERSION,
        "course_id": "course-1",
        "phase": "documents",
        "artifacts": artifacts
        if artifacts is not None
        else [
            {
                "path": str(
                    (data_dir / "uploads" / "video-1.mp4").resolve()
                ),
                "root": str((data_dir / "uploads").resolve()),
            },
            {
                "path": str(
                    (data_dir / "audio" / "video-1.wav").resolve()
                ),
                "root": str((data_dir / "audio").resolve()),
            },
            {
                "path": str(
                    (
                        data_dir
                        / "transcripts"
                        / "video-1.json"
                    ).resolve()
                ),
                "root": str((data_dir / "transcripts").resolve()),
            },
            {
                "path": str(
                    (
                        data_dir
                        / "sources"
                        / "course-1"
                        / "source-1.pdf"
                    ).resolve()
                ),
                "root": str((data_dir / "sources").resolve()),
            },
        ],
    }
    metadata = {
        "course_purge": plan,
        "unrelated_metadata": "preserved",
    }
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE trash_items (
                id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                course_id TEXT,
                metadata_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO trash_items (
                id,
                entity_type,
                entity_id,
                course_id,
                metadata_json
            )
            VALUES (?, 'course', 'course-1', 'course-1', ?)
            """,
            ("trash-course-1", json.dumps(metadata)),
        )
        conn.commit()
    return plan


def _add_entity_purge_plan(
    db_path: Path,
    *,
    entity_id: str,
    artifacts: list[dict[str, str]],
) -> dict[str, object]:
    plan: dict[str, object] = {
        "version": trash_service.ENTITY_PURGE_PLAN_VERSION,
        "entity_type": "video_job",
        "phase": "database",
        "artifacts": artifacts,
    }
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE trash_items (
                id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                course_id TEXT,
                metadata_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO trash_items (
                id,
                entity_type,
                entity_id,
                course_id,
                metadata_json
            )
            VALUES (?, 'video_job', ?, 'course-1', ?)
            """,
            (
                f"trash-{entity_id}",
                entity_id,
                json.dumps({"entity_purge": plan}),
            ),
        )
        conn.commit()
    return plan


def test_create_validate_and_list_workspace_backup(tmp_path: Path) -> None:
    data_dir, db_path = _workspace(tmp_path, label="source")

    created = create_workspace_backup(
        db_path=db_path,
        data_dir=data_dir,
        current_schema_version=SCHEMA_VERSION,
        now=FIXED_TIME,
    )

    assert created.path.suffix == BACKUP_EXTENSION
    assert created.backup_kind == "manual"
    assert created.schema_version == SCHEMA_VERSION
    assert created.managed_file_count == 4
    assert created.entry_count == 5
    assert len(created.archive_sha256) == 64
    with zipfile.ZipFile(created.path) as archive:
        names = set(archive.namelist())
    assert names == {
        "manifest.json",
        "workspace/database.sqlite3",
        "workspace/uploads/video-1.mp4",
        "workspace/audio/video-1.wav",
        "workspace/transcripts/video-1.json",
        "workspace/sources/course-1/source-1.pdf",
    }
    assert all(
        f"workspace/{excluded}/" not in name
        for excluded in workspace_backup.EXCLUDED_ROOTS
        for name in names
    )

    validated = validate_workspace_backup(
        created.path,
        current_schema_version=SCHEMA_VERSION,
    )
    assert validated.archive_sha256 == created.archive_sha256
    assert validated.manifest["app"] == {
        "name": "Video Course Cards",
        "version": "0.1.1",
    }
    assert validated.manifest["counts"]["by_kind"] == {
        "audio": 1,
        "database": 1,
        "sources": 1,
        "transcripts": 1,
        "uploads": 1,
    }

    summaries = list_workspace_backups(
        data_dir=data_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    assert len(summaries) == 1
    assert summaries[0].valid is True
    assert summaries[0].archive_sha256 == created.archive_sha256

    # SQLite and ZIP handles are closed before returning, including on Windows.
    renamed_database = db_path.with_name("jobs-renamed.db")
    os.replace(db_path, renamed_database)
    os.replace(renamed_database, db_path)
    renamed_backup = created.path.with_name("renamed.vcc-backup")
    os.replace(created.path, renamed_backup)
    os.replace(renamed_backup, created.path)


@pytest.mark.parametrize(
    ("column", "relative_parts", "message"),
    [
        (
            "video_path",
            ("uploads", "another-job.mp4"),
            "Job video path",
        ),
        (
            "video_path",
            ("uploads", "video-1.pdf"),
            "Job video path",
        ),
        (
            "video_path",
            ("uploads", "nested", "video-1.mp4"),
            "Job video path",
        ),
        (
            "transcript_path",
            ("transcripts", "another-job.json"),
            "Job transcript path",
        ),
        (
            "transcript_path",
            ("transcripts", "nested", "video-1.json"),
            "Job transcript path",
        ),
    ],
)
def test_import_rejects_noncanonical_job_managed_paths(
    tmp_path: Path,
    column: str,
    relative_parts: tuple[str, ...],
    message: str,
) -> None:
    data_dir, db_path = _workspace(tmp_path, label="job-path-owner")
    backup = create_workspace_backup(
        db_path=db_path,
        data_dir=data_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    imported_db = _database_from_backup(
        backup.path,
        tmp_path / f"invalid-{column}.db",
    )
    with closing(sqlite3.connect(imported_db)) as conn:
        conn.execute(
            f'UPDATE jobs SET "{column}" = ? WHERE id = ?',
            (str(data_dir.joinpath(*relative_parts)), "video-1"),
        )
        conn.commit()
    _replace_backup_database(backup.path, imported_db)

    with pytest.raises(BackupValidationError, match=message):
        validate_workspace_backup(
            backup.path,
            current_schema_version=SCHEMA_VERSION,
        )


@pytest.mark.parametrize(
    "relative_parts",
    [
        ("course-2", "source-1.pdf"),
        ("course-1", "another-source.pdf"),
        ("course-1", "source-1.exe"),
        ("course-1", "nested", "source-1.pdf"),
    ],
)
def test_import_rejects_noncanonical_source_asset_paths(
    tmp_path: Path,
    relative_parts: tuple[str, ...],
) -> None:
    data_dir, db_path = _workspace(tmp_path, label="source-path-owner")
    backup = create_workspace_backup(
        db_path=db_path,
        data_dir=data_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    imported_db = _database_from_backup(
        backup.path,
        tmp_path / "invalid-source-path.db",
    )
    with closing(sqlite3.connect(imported_db)) as conn:
        conn.execute(
            """
            UPDATE source_assets
            SET stored_path = ?
            WHERE id = 'source-1'
            """,
            (str(data_dir.joinpath("sources", *relative_parts)),),
        )
        conn.commit()
    _replace_backup_database(backup.path, imported_db)

    with pytest.raises(BackupValidationError, match="Source asset path"):
        validate_workspace_backup(
            backup.path,
            current_schema_version=SCHEMA_VERSION,
        )


def test_import_rejects_casefolded_path_shared_by_different_records(
    tmp_path: Path,
) -> None:
    data_dir, db_path = _workspace(tmp_path, label="duplicate-path-owner")
    backup = create_workspace_backup(
        db_path=db_path,
        data_dir=data_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    imported_db = _database_from_backup(
        backup.path,
        tmp_path / "duplicate-path.db",
    )
    with closing(sqlite3.connect(imported_db)) as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                id,
                course_id,
                video_path,
                transcript_path
            )
            VALUES ('VIDEO-1', 'course-2', ?, NULL)
            """,
            (str(data_dir / "uploads" / "VIDEO-1.MP4"),),
        )
        conn.commit()
    _replace_backup_database(backup.path, imported_db)

    with pytest.raises(BackupValidationError, match="different records"):
        validate_workspace_backup(
            backup.path,
            current_schema_version=SCHEMA_VERSION,
        )


def test_import_rejects_entity_plan_sharing_another_course_record(
    tmp_path: Path,
) -> None:
    data_dir, db_path = _workspace(tmp_path, label="entity-course-owner")
    backup = create_workspace_backup(
        db_path=db_path,
        data_dir=data_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    imported_db = _database_from_backup(
        backup.path,
        tmp_path / "entity-course-owner.db",
    )
    _add_entity_purge_plan(
        imported_db,
        entity_id="video-1",
        artifacts=[
            {"root": "uploads", "relative_path": "video-1.mp4"},
            {"root": "audio", "relative_path": "video-1.wav"},
            {
                "root": "transcripts",
                "relative_path": "video-1.json",
            },
        ],
    )
    with closing(sqlite3.connect(imported_db)) as conn:
        conn.execute(
            """
            UPDATE trash_items
            SET course_id = 'course-2'
            WHERE entity_id = 'video-1'
            """
        )
        conn.commit()
    _replace_backup_database(backup.path, imported_db)

    with pytest.raises(BackupValidationError, match="another entity"):
        validate_workspace_backup(
            backup.path,
            current_schema_version=SCHEMA_VERSION,
        )


def test_import_rejects_float_entity_purge_version(tmp_path: Path) -> None:
    data_dir, db_path = _workspace(tmp_path, label="float-plan-version")
    backup = create_workspace_backup(
        db_path=db_path,
        data_dir=data_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    imported_db = _database_from_backup(
        backup.path,
        tmp_path / "float-plan-version.db",
    )
    _add_entity_purge_plan(
        imported_db,
        entity_id="video-1",
        artifacts=[
            {"root": "uploads", "relative_path": "video-1.mp4"},
            {"root": "audio", "relative_path": "video-1.wav"},
            {
                "root": "transcripts",
                "relative_path": "video-1.json",
            },
        ],
    )
    with closing(sqlite3.connect(imported_db)) as conn:
        metadata = json.loads(
            conn.execute(
                """
                SELECT metadata_json
                FROM trash_items
                WHERE entity_id = 'video-1'
                """
            ).fetchone()[0]
        )
        metadata["entity_purge"]["version"] = 2.0
        conn.execute(
            """
            UPDATE trash_items
            SET metadata_json = ?
            WHERE entity_id = 'video-1'
            """,
            (json.dumps(metadata),),
        )
        conn.commit()
    _replace_backup_database(backup.path, imported_db)

    with pytest.raises(BackupValidationError, match="version"):
        validate_workspace_backup(
            backup.path,
            current_schema_version=SCHEMA_VERSION,
        )


def test_backup_accepts_entity_plan_matching_its_record(tmp_path: Path) -> None:
    data_dir, db_path = _workspace(tmp_path, label="matching-entity-plan")
    _add_entity_purge_plan(
        db_path,
        entity_id="video-1",
        artifacts=[
            {"root": "uploads", "relative_path": "video-1.mp4"},
            {"root": "audio", "relative_path": "video-1.wav"},
            {
                "root": "transcripts",
                "relative_path": "video-1.json",
            },
        ],
    )

    backup = create_workspace_backup(
        db_path=db_path,
        data_dir=data_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    validated = validate_workspace_backup(
        backup.path,
        current_schema_version=SCHEMA_VERSION,
    )

    assert validated.schema_version == SCHEMA_VERSION


def test_sqlite_backup_captures_committed_wal_with_writer_open(
    tmp_path: Path,
) -> None:
    data_dir, db_path = _workspace(tmp_path, label="wal")
    writer = sqlite3.connect(db_path)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE wal_evidence (value TEXT NOT NULL)")
        writer.execute(
            "INSERT INTO wal_evidence (value) VALUES ('committed-in-wal')"
        )
        writer.commit()

        created = create_workspace_backup(
            db_path=db_path,
            data_dir=data_dir,
            current_schema_version=SCHEMA_VERSION,
        )

        extracted = _database_from_backup(
            created.path,
            tmp_path / "wal-backup.db",
        )
        with closing(sqlite3.connect(extracted)) as conn:
            value = conn.execute(
                "SELECT value FROM wal_evidence"
            ).fetchone()[0]
        assert value == "committed-in-wal"
    finally:
        writer.close()


def test_restore_quarantines_crash_wal_before_publishing_new_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir, source_db = _workspace(
        tmp_path,
        label="stale-wal-source",
    )
    with closing(sqlite3.connect(source_db)) as conn:
        conn.execute(
            "CREATE TABLE wal_evidence (value TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO wal_evidence (value) VALUES ('restored')"
        )
        conn.commit()
    backup = create_workspace_backup(
        db_path=source_db,
        data_dir=source_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    target_dir, target_db = _workspace(
        tmp_path,
        label="stale-wal-target",
    )

    # Build a valid WAL from the exact database shape that restore will
    # publish after path rebasing. If left beside that new main file, SQLite
    # demonstrably replays the stale transaction.
    compatible_db = _database_from_backup(
        backup.path,
        tmp_path / "compatible-stale-wal.db",
    )
    workspace_backup._rebase_database_paths(
        compatible_db,
        old_data_dir=source_dir,
        new_data_dir=target_dir,
    )
    writer = sqlite3.connect(compatible_db)
    try:
        assert writer.execute(
            "PRAGMA journal_mode=WAL"
        ).fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        compatible_main = compatible_db.read_bytes()
        writer.execute(
            "INSERT INTO wal_evidence (value) VALUES ('stale-crash')"
        )
        writer.commit()
        stale_wal = Path(f"{compatible_db}-wal").read_bytes()
        stale_shm = Path(f"{compatible_db}-shm").read_bytes()
    finally:
        writer.close()

    probe_db = tmp_path / "stale-wal-probe.db"
    probe_db.write_bytes(compatible_main)
    Path(f"{probe_db}-wal").write_bytes(stale_wal)
    Path(f"{probe_db}-shm").write_bytes(stale_shm)
    with closing(sqlite3.connect(probe_db)) as conn:
        assert [
            row[0]
            for row in conn.execute(
                "SELECT value FROM wal_evidence ORDER BY rowid"
            )
        ] == ["restored", "stale-crash"]

    pending = queue_workspace_restore(
        backup.path,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    real_create_backup = workspace_backup.create_workspace_backup
    injected = False

    def create_backup_then_inject_sidecars(**kwargs):
        nonlocal injected
        created = real_create_backup(**kwargs)
        if kwargs.get("backup_kind") == "pre_restore":
            Path(f"{target_db}-wal").write_bytes(stale_wal)
            Path(f"{target_db}-shm").write_bytes(stale_shm)
            Path(f"{target_db}-journal").write_bytes(b"stale-journal")
            injected = True
        return created

    monkeypatch.setattr(
        workspace_backup,
        "create_workspace_backup",
        create_backup_then_inject_sidecars,
    )

    staged = apply_pending_workspace_restore(
        db_path=target_db,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )

    assert injected is True
    assert staged is not None
    assert staged.status == "staged"
    receipt = json.loads(
        (
            target_dir
            / "restore"
            / workspace_backup.RESTORE_RECEIPT_FILENAME
        ).read_text(encoding="utf-8")
    )
    transaction_root = (
        target_dir / "restore" / receipt["transaction_name"]
    )
    assert (
        transaction_root / "rollback" / "database.sqlite3-wal"
    ).read_bytes() == stale_wal
    assert (
        transaction_root / "rollback" / "database.sqlite3-shm"
    ).read_bytes() == stale_shm
    assert (
        transaction_root / "rollback" / "database.sqlite3-journal"
    ).read_bytes() == b"stale-journal"

    applied = finalize_pending_workspace_restore(
        staged.restore_id,
        db_path=target_db,
        data_dir=target_dir,
    )

    assert applied.status == "applied"
    assert not transaction_root.exists()
    assert not pending.marker_path.exists()
    with closing(sqlite3.connect(target_db)) as conn:
        assert [
            row[0]
            for row in conn.execute(
                "SELECT value FROM wal_evidence ORDER BY rowid"
            )
        ] == ["restored"]


def test_interrupted_database_bundle_swap_restores_wal_and_shm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir, source_db = _workspace(
        tmp_path,
        label="bundle-crash-source",
    )
    backup = create_workspace_backup(
        db_path=source_db,
        data_dir=source_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    target_dir, target_db = _workspace(
        tmp_path,
        label="bundle-crash-target",
    )
    with closing(sqlite3.connect(target_db)) as conn:
        conn.execute(
            "CREATE TABLE crash_evidence (value TEXT NOT NULL)"
        )
        conn.commit()

    writer = sqlite3.connect(target_db)
    try:
        assert writer.execute(
            "PRAGMA journal_mode=WAL"
        ).fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        original_main = target_db.read_bytes()
        writer.execute(
            "INSERT INTO crash_evidence (value) VALUES "
            "('committed-before-crash')"
        )
        writer.commit()
        original_wal = Path(f"{target_db}-wal").read_bytes()
        original_shm = Path(f"{target_db}-shm").read_bytes()
    finally:
        writer.close()

    pending = queue_workspace_restore(
        backup.path,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    real_create_backup = workspace_backup.create_workspace_backup
    real_move = workspace_backup._move_path

    def create_backup_then_restore_crash_bundle(**kwargs):
        created = real_create_backup(**kwargs)
        if kwargs.get("backup_kind") == "pre_restore":
            target_db.write_bytes(original_main)
            Path(f"{target_db}-wal").write_bytes(original_wal)
            Path(f"{target_db}-shm").write_bytes(original_shm)
        return created

    class SimulatedProcessCrash(BaseException):
        pass

    def crash_before_database_publish(
        source: Path,
        destination: Path,
    ) -> None:
        if (
            source.name == "database.sqlite3"
            and "extracted" in source.parts
            and destination == target_db
        ):
            raise SimulatedProcessCrash()
        real_move(source, destination)

    monkeypatch.setattr(
        workspace_backup,
        "create_workspace_backup",
        create_backup_then_restore_crash_bundle,
    )
    monkeypatch.setattr(
        workspace_backup,
        "_move_path",
        crash_before_database_publish,
    )

    with pytest.raises(SimulatedProcessCrash):
        apply_pending_workspace_restore(
            db_path=target_db,
            data_dir=target_dir,
            current_schema_version=SCHEMA_VERSION,
        )

    assert get_pending_workspace_restore(
        data_dir=target_dir
    ).phase == "swapping"
    monkeypatch.setattr(workspace_backup, "_move_path", real_move)

    recovered = apply_pending_workspace_restore(
        db_path=target_db,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )

    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.restore_id == pending.restore_id
    assert "interrupted" in (recovered.error or "").lower()
    assert target_db.read_bytes() == original_main
    assert Path(f"{target_db}-wal").read_bytes() == original_wal
    assert Path(f"{target_db}-shm").read_bytes() == original_shm
    with closing(sqlite3.connect(target_db)) as conn:
        assert conn.execute(
            "SELECT value FROM crash_evidence"
        ).fetchone()[0] == "committed-before-crash"
        assert conn.execute(
            "SELECT label FROM workspace_marker"
        ).fetchone()[0] == "bundle-crash-target"


def test_restore_into_clean_install_without_existing_database(
    tmp_path: Path,
) -> None:
    source_data_dir, source_db_path = _workspace(
        tmp_path,
        label="clean-install-source",
    )
    created = create_workspace_backup(
        db_path=source_db_path,
        data_dir=source_data_dir,
        current_schema_version=SCHEMA_VERSION,
    )

    target_data_dir = tmp_path / "clean-install-target"
    target_db_path = target_data_dir / "data" / "jobs.db"
    target_data_dir.mkdir()
    queue_workspace_restore(
        created.path,
        data_dir=target_data_dir,
        current_schema_version=SCHEMA_VERSION,
    )

    staged = apply_pending_workspace_restore(
        db_path=target_db_path,
        data_dir=target_data_dir,
        current_schema_version=SCHEMA_VERSION,
    )

    assert staged is not None
    assert staged.status == "staged"
    result = finalize_pending_workspace_restore(
        staged.restore_id,
        db_path=target_db_path,
        data_dir=target_data_dir,
    )
    assert result.status == "applied"
    assert result.pre_restore_backup_path is None
    with closing(sqlite3.connect(target_db_path)) as conn:
        assert conn.execute(
            "SELECT label FROM workspace_marker"
        ).fetchone()[0] == "clean-install-source"
    assert (
        target_data_dir / "uploads" / "video-1.mp4"
    ).read_bytes() == b"clean-install-source-video"


def test_validation_rejects_hash_tampering_and_list_marks_invalid(
    tmp_path: Path,
) -> None:
    data_dir, db_path = _workspace(tmp_path, label="tampered")
    created = create_workspace_backup(
        db_path=db_path,
        data_dir=data_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    contents = _read_zip(created.path)
    contents["workspace/uploads/video-1.mp4"] = b"same-size-evil"
    _rewrite_zip(created.path, contents)

    with pytest.raises(BackupValidationError, match="hash mismatch"):
        validate_workspace_backup(
            created.path,
            current_schema_version=SCHEMA_VERSION,
        )

    summaries = list_workspace_backups(
        data_dir=data_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    assert summaries[0].valid is False
    assert "hash mismatch" in (summaries[0].error or "")


def test_validation_rejects_future_schema_even_when_manifest_is_tampered(
    tmp_path: Path,
) -> None:
    data_dir, db_path = _workspace(tmp_path, label="future")
    created = create_workspace_backup(
        db_path=db_path,
        data_dir=data_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    contents = _read_zip(created.path)
    manifest = json.loads(contents["manifest.json"])
    manifest["schema"]["version"] = SCHEMA_VERSION + 1
    contents["manifest.json"] = json.dumps(manifest).encode()
    _rewrite_zip(created.path, contents)

    with pytest.raises(BackupValidationError, match="newer"):
        validate_workspace_backup(
            created.path,
            current_schema_version=SCHEMA_VERSION,
        )


def test_create_rejects_database_from_future_schema(tmp_path: Path) -> None:
    data_dir, db_path = _workspace(
        tmp_path,
        label="future-source",
        schema_version=SCHEMA_VERSION + 1,
    )

    with pytest.raises(BackupValidationError, match="newer"):
        create_workspace_backup(
            db_path=db_path,
            data_dir=data_dir,
            current_schema_version=SCHEMA_VERSION,
        )


@pytest.mark.parametrize(
    ("entry_name", "external_attr", "expected"),
    [
        ("../outside.txt", 0, "Unsafe backup entry path"),
        (
            "workspace/uploads/link.mp4",
            (stat.S_IFLNK | 0o777) << 16,
            "symbolic link",
        ),
    ],
)
def test_validation_rejects_zip_slip_and_symlink_entries(
    tmp_path: Path,
    entry_name: str,
    external_attr: int,
    expected: str,
) -> None:
    archive_path = tmp_path / f"attack-{uuid_safe(entry_name)}.vcc-backup"
    info = zipfile.ZipInfo(entry_name)
    info.create_system = 3
    info.external_attr = external_attr
    with zipfile.ZipFile(archive_path, mode="w") as archive:
        archive.writestr("manifest.json", b"{}")
        archive.writestr(info, b"attack")

    with pytest.raises(BackupValidationError, match=expected):
        validate_workspace_backup(
            archive_path,
            current_schema_version=SCHEMA_VERSION,
        )


def uuid_safe(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:8]


def test_validation_enforces_bounded_entry_size(tmp_path: Path) -> None:
    data_dir, db_path = _workspace(tmp_path, label="bounded")
    created = create_workspace_backup(
        db_path=db_path,
        data_dir=data_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    limits = BackupLimits(
        max_archive_bytes=10 * 1024**2,
        max_entry_bytes=8,
        max_uncompressed_bytes=10 * 1024**2,
        max_entries=100,
        max_manifest_bytes=1024**2,
    )

    with pytest.raises(BackupValidationError, match="too large"):
        validate_workspace_backup(
            created.path,
            current_schema_version=SCHEMA_VERSION,
            limits=limits,
        )


def test_validation_rejects_corrupt_sqlite_with_matching_manifest_hash(
    tmp_path: Path,
) -> None:
    data_dir, db_path = _workspace(tmp_path, label="bad-db")
    created = create_workspace_backup(
        db_path=db_path,
        data_dir=data_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    contents = _read_zip(created.path)
    corrupt = b"not a sqlite database"
    contents[workspace_backup.DATABASE_ARCHIVE_PATH] = corrupt
    manifest = json.loads(contents["manifest.json"])
    database_entry = next(
        entry for entry in manifest["entries"]
        if entry["kind"] == "database"
    )
    database_entry["size_bytes"] = len(corrupt)
    database_entry["sha256"] = hashlib.sha256(corrupt).hexdigest()
    manifest["counts"]["total_bytes"] = sum(
        entry["size_bytes"] for entry in manifest["entries"]
    )
    contents["manifest.json"] = json.dumps(manifest).encode()
    _rewrite_zip(created.path, contents)

    with pytest.raises(BackupValidationError, match="SQLite"):
        validate_workspace_backup(
            created.path,
            current_schema_version=SCHEMA_VERSION,
        )


def test_queue_and_restart_apply_restore_with_path_rebasing(
    tmp_path: Path,
) -> None:
    source_dir, source_db = _workspace(tmp_path, label="source-workspace")
    backup = create_workspace_backup(
        db_path=source_db,
        data_dir=source_dir,
        current_schema_version=SCHEMA_VERSION,
        now=FIXED_TIME,
    )
    target_dir, target_db = _workspace(tmp_path, label="target-workspace")
    pending = queue_workspace_restore(
        backup.path,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
        now=FIXED_TIME,
    )

    assert pending.backup_path.is_file()
    assert get_pending_workspace_restore(data_dir=target_dir) == pending

    staged = apply_pending_workspace_restore(
        db_path=target_db,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
        now=FIXED_TIME,
    )

    assert staged is not None
    assert staged.status == "staged"
    assert pending.marker_path.exists()
    assert pending.backup_path.exists()
    assert get_pending_workspace_restore(data_dir=target_dir).phase == "swapped"
    result = finalize_pending_workspace_restore(
        staged.restore_id,
        db_path=target_db,
        data_dir=target_dir,
        now=FIXED_TIME,
    )
    assert result.status == "applied"
    assert result.error is None
    assert result.pre_restore_backup_path is not None
    assert result.pre_restore_backup_path.is_file()
    assert not pending.marker_path.exists()
    assert not pending.backup_path.exists()
    assert (target_dir / "uploads" / "video-1.mp4").read_bytes() == (
        b"source-workspace-video"
    )
    assert (target_dir / "audio" / "video-1.wav").read_bytes() == (
        b"source-workspace-audio"
    )
    with closing(sqlite3.connect(target_db)) as conn:
        marker = conn.execute(
            "SELECT label FROM workspace_marker"
        ).fetchone()[0]
        video_path, transcript_path = conn.execute(
            "SELECT video_path, transcript_path FROM jobs"
        ).fetchone()
        source_path = conn.execute(
            "SELECT stored_path FROM source_assets"
        ).fetchone()[0]
    assert marker == "source-workspace"
    assert Path(video_path) == (
        target_dir / "uploads" / "video-1.mp4"
    ).resolve()
    assert Path(transcript_path) == (
        target_dir / "transcripts" / "video-1.json"
    ).resolve()
    assert Path(source_path) == (
        target_dir / "sources" / "course-1" / "source-1.pdf"
    ).resolve()

    pre_restore = validate_workspace_backup(
        result.pre_restore_backup_path,
        current_schema_version=SCHEMA_VERSION,
    )
    assert pre_restore.backup_kind == "pre_restore"
    assert pre_restore.path.name.startswith("vcc-pre-restore-")
    pre_restore_db = _database_from_backup(
        pre_restore.path,
        tmp_path / "pre-restore.db",
    )
    with closing(sqlite3.connect(pre_restore_db)) as conn:
        previous_marker = conn.execute(
            "SELECT label FROM workspace_marker"
        ).fetchone()[0]
    assert previous_marker == "target-workspace"
    state = get_workspace_restore_state(data_dir=target_dir)
    assert state.pending is None
    assert state.workspace_generation == 2
    assert state.last_result == result


def test_restore_rebases_course_purge_plan_before_artifact_cleanup(
    tmp_path: Path,
) -> None:
    source_dir, source_db = _workspace(
        tmp_path,
        label="purge-plan-source",
    )
    old_plan = _add_course_purge_plan(source_db, source_dir)
    backup = create_workspace_backup(
        db_path=source_db,
        data_dir=source_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    target_dir, target_db = _workspace(
        tmp_path,
        label="purge-plan-target",
    )
    queue_workspace_restore(
        backup.path,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )

    staged = apply_pending_workspace_restore(
        db_path=target_db,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )

    assert staged is not None
    assert staged.status == "staged"
    finalize_pending_workspace_restore(
        staged.restore_id,
        db_path=target_db,
        data_dir=target_dir,
    )
    with closing(sqlite3.connect(target_db)) as conn:
        metadata = json.loads(
            conn.execute(
                """
                SELECT metadata_json FROM trash_items
                WHERE id = 'trash-course-1'
                """
            ).fetchone()[0]
        )
    rebased_plan = metadata["course_purge"]
    assert metadata["unrelated_metadata"] == "preserved"
    assert rebased_plan["artifacts"] == [
        {
            "path": str(
                (target_dir / "uploads" / "video-1.mp4").resolve()
            ),
            "root": str((target_dir / "uploads").resolve()),
        },
        {
            "path": str(
                (target_dir / "audio" / "video-1.wav").resolve()
            ),
            "root": str((target_dir / "audio").resolve()),
        },
        {
            "path": str(
                (
                    target_dir
                    / "transcripts"
                    / "video-1.json"
                ).resolve()
            ),
            "root": str((target_dir / "transcripts").resolve()),
        },
        {
            "path": str(
                (
                    target_dir
                    / "sources"
                    / "course-1"
                    / "source-1.pdf"
                ).resolve()
            ),
            "root": str((target_dir / "sources").resolve()),
        },
    ]

    with pytest.raises(TrashOperationError, match="current workspace"):
        trash_service._purge_planned_artifacts(
            old_plan,
            workspace_root=target_dir,
        )
    assert (source_dir / "uploads" / "video-1.mp4").is_file()
    assert (
        source_dir / "sources" / "course-1" / "source-1.pdf"
    ).is_file()

    trash_service._purge_planned_artifacts(
        rebased_plan,
        workspace_root=target_dir,
    )
    assert not (target_dir / "uploads" / "video-1.mp4").exists()
    assert not (target_dir / "audio" / "video-1.wav").exists()
    assert not (
        target_dir / "transcripts" / "video-1.json"
    ).exists()
    assert not (
        target_dir / "sources" / "course-1" / "source-1.pdf"
    ).exists()
    assert (source_dir / "uploads" / "video-1.mp4").is_file()
    assert (
        source_dir / "sources" / "course-1" / "source-1.pdf"
    ).is_file()


def test_import_rejects_external_course_purge_artifact(
    tmp_path: Path,
) -> None:
    external = tmp_path / "external-sentinel.txt"
    external.write_text("must survive", encoding="utf-8")
    source_dir, source_db = _workspace(
        tmp_path,
        label="external-plan-source",
    )
    backup = create_workspace_backup(
        db_path=source_db,
        data_dir=source_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    imported_db = _database_from_backup(
        backup.path,
        tmp_path / "external-plan-import.db",
    )
    _add_course_purge_plan(
        imported_db,
        source_dir,
        artifacts=[
            {
                "path": str(external.resolve()),
                "root": str(tmp_path.resolve()),
            }
        ],
    )
    _replace_backup_database(backup.path, imported_db)

    with pytest.raises(BackupValidationError, match="outside the workspace"):
        validate_workspace_backup(
            backup.path,
            current_schema_version=SCHEMA_VERSION,
        )

    assert external.read_text(encoding="utf-8") == "must survive"


def test_import_rejects_entity_plan_for_another_managed_file(
    tmp_path: Path,
) -> None:
    source_dir, source_db = _workspace(
        tmp_path,
        label="forged-entity-plan-source",
    )
    backup = create_workspace_backup(
        db_path=source_db,
        data_dir=source_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    imported_db = _database_from_backup(
        backup.path,
        tmp_path / "forged-entity-plan-import.db",
    )
    _add_entity_purge_plan(
        imported_db,
        entity_id="different-job",
        artifacts=[
            {
                "root": "uploads",
                "relative_path": "video-1.mp4",
            },
            {
                "root": "audio",
                "relative_path": "video-1.wav",
            },
        ],
    )
    _replace_backup_database(backup.path, imported_db)

    with pytest.raises(BackupValidationError, match="entity namespace"):
        validate_workspace_backup(
            backup.path,
            current_schema_version=SCHEMA_VERSION,
        )


def test_import_rejects_course_plan_for_another_course_files(
    tmp_path: Path,
) -> None:
    source_dir, source_db = _workspace(
        tmp_path,
        label="forged-course-plan-source",
    )
    other_video = source_dir / "uploads" / "video-2.mp4"
    other_audio = source_dir / "audio" / "video-2.wav"
    other_transcript = source_dir / "transcripts" / "video-2.json"
    other_source = (
        source_dir / "sources" / "course-2" / "source-2.pdf"
    )
    other_video.write_bytes(b"other-video")
    other_audio.write_bytes(b"other-audio")
    other_transcript.write_text("{}", encoding="utf-8")
    other_source.parent.mkdir(parents=True)
    other_source.write_bytes(b"other-source")
    with closing(sqlite3.connect(source_db)) as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                id,
                course_id,
                video_path,
                transcript_path
            )
            VALUES ('video-2', 'course-2', ?, ?)
            """,
            (str(other_video.resolve()), str(other_transcript.resolve())),
        )
        conn.execute(
            """
            INSERT INTO source_assets (id, course_id, stored_path)
            VALUES ('source-2', 'course-2', ?)
            """,
            (str(other_source.resolve()),),
        )
        conn.commit()
    backup = create_workspace_backup(
        db_path=source_db,
        data_dir=source_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    imported_db = _database_from_backup(
        backup.path,
        tmp_path / "forged-course-plan-import.db",
    )
    _add_course_purge_plan(
        imported_db,
        source_dir,
        artifacts=[
            {
                "path": str(other_video.resolve()),
                "root": str((source_dir / "uploads").resolve()),
            },
            {
                "path": str(other_audio.resolve()),
                "root": str((source_dir / "audio").resolve()),
            },
            {
                "path": str(other_transcript.resolve()),
                "root": str((source_dir / "transcripts").resolve()),
            },
        ],
    )
    _replace_backup_database(backup.path, imported_db)

    with pytest.raises(BackupValidationError, match="another course"):
        validate_workspace_backup(
            backup.path,
            current_schema_version=SCHEMA_VERSION,
        )


def test_import_rejects_unknown_course_purge_artifact_fields(
    tmp_path: Path,
) -> None:
    source_dir, source_db = _workspace(
        tmp_path,
        label="unknown-field-source",
    )
    backup = create_workspace_backup(
        db_path=source_db,
        data_dir=source_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    imported_db = _database_from_backup(
        backup.path,
        tmp_path / "unknown-field-import.db",
    )
    _add_course_purge_plan(
        imported_db,
        source_dir,
        artifacts=[
            {
                "path": str(
                    (source_dir / "uploads" / "video-1.mp4").resolve()
                ),
                "root": str(source_dir.resolve()),
                "unexpected_path": str((tmp_path / "outside").resolve()),
            }
        ],
    )
    _replace_backup_database(backup.path, imported_db)

    with pytest.raises(BackupValidationError, match="unsupported fields"):
        validate_workspace_backup(
            backup.path,
            current_schema_version=SCHEMA_VERSION,
        )


def test_pending_restore_cannot_be_overwritten_and_can_be_canceled(
    tmp_path: Path,
) -> None:
    source_dir, source_db = _workspace(tmp_path, label="queue-source")
    backup = create_workspace_backup(
        db_path=source_db,
        data_dir=source_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    target_dir, _ = _workspace(tmp_path, label="queue-target")
    pending = queue_workspace_restore(
        backup.path,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )

    with pytest.raises(RestoreQueueError, match="already queued"):
        queue_workspace_restore(
            backup.path,
            data_dir=target_dir,
            current_schema_version=SCHEMA_VERSION,
        )
    with pytest.raises(RestoreQueueError, match="identity"):
        cancel_pending_workspace_restore(
            "different-restore",
            data_dir=target_dir,
        )

    canceled = cancel_pending_workspace_restore(
        pending.restore_id,
        data_dir=target_dir,
        now=FIXED_TIME,
    )

    assert canceled.status == "canceled"
    assert canceled.restore_id == pending.restore_id
    assert get_pending_workspace_restore(data_dir=target_dir) is None
    state = get_workspace_restore_state(data_dir=target_dir)
    assert state.workspace_generation == 1
    assert state.last_result == canceled
    assert not pending.backup_path.exists()


def test_staged_restore_is_resumable_until_startup_finalizes(
    tmp_path: Path,
) -> None:
    source_dir, source_db = _workspace(tmp_path, label="resume-source")
    backup = create_workspace_backup(
        db_path=source_db,
        data_dir=source_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    target_dir, target_db = _workspace(tmp_path, label="resume-target")
    pending = queue_workspace_restore(
        backup.path,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )

    staged = apply_pending_workspace_restore(
        db_path=target_db,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    resumed = apply_pending_workspace_restore(
        db_path=target_db,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )

    assert staged is not None
    assert resumed == staged
    assert resumed.status == "staged"
    assert resumed.restore_id == pending.restore_id
    with pytest.raises(RestoreQueueError, match="already started"):
        cancel_pending_workspace_restore(
            pending.restore_id,
            data_dir=target_dir,
        )
    assert get_workspace_restore_state(
        data_dir=target_dir
    ).workspace_generation == 1


@pytest.mark.parametrize("had_existing_workspace", [True, False])
@pytest.mark.parametrize("crash_after_cleanup_step", [1, 2, 3, 4])
def test_committed_restore_resumes_cleanup_without_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    had_existing_workspace: bool,
    crash_after_cleanup_step: int,
) -> None:
    source_dir, source_db = _workspace(
        tmp_path,
        label="finalize-crash-source",
    )
    backup = create_workspace_backup(
        db_path=source_db,
        data_dir=source_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    if had_existing_workspace:
        target_dir, target_db = _workspace(
            tmp_path,
            label="finalize-crash-target",
        )
    else:
        target_dir = tmp_path / "finalize-crash-clean-target"
        target_dir.mkdir()
        target_db = target_dir / "data" / "jobs.db"
    queue_workspace_restore(
        backup.path,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    staged = apply_pending_workspace_restore(
        db_path=target_db,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    assert staged is not None

    queue_root = target_dir / "restore"
    receipt_path = queue_root / workspace_backup.RESTORE_RECEIPT_FILENAME
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    transaction_root = queue_root / receipt["transaction_name"]
    queued_backup = queue_root / receipt["backup_filename"]
    real_remove = workspace_backup._remove_finalized_restore_path
    cleanup_steps = 0

    class SimulatedFinalizationCrash(BaseException):
        pass

    def remove_then_crash(
        path: Path,
        *,
        recursive: bool = False,
    ) -> None:
        nonlocal cleanup_steps
        real_remove(path, recursive=recursive)
        cleanup_steps += 1
        if cleanup_steps == crash_after_cleanup_step:
            raise SimulatedFinalizationCrash()

    monkeypatch.setattr(
        workspace_backup,
        "_remove_finalized_restore_path",
        remove_then_crash,
    )
    with pytest.raises(SimulatedFinalizationCrash):
        finalize_pending_workspace_restore(
            staged.restore_id,
            db_path=target_db,
            data_dir=target_dir,
        )

    with closing(sqlite3.connect(target_db)) as conn:
        assert conn.execute(
            "SELECT label FROM workspace_marker"
        ).fetchone()[0] == "finalize-crash-source"
    if receipt_path.exists():
        interrupted = json.loads(receipt_path.read_text(encoding="utf-8"))
        assert interrupted["phase"] == "finalizing"

    monkeypatch.setattr(
        workspace_backup,
        "_remove_finalized_restore_path",
        real_remove,
    )
    apply_pending_workspace_restore(
        db_path=target_db,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    recovered = finalize_pending_workspace_restore(
        staged.restore_id,
        db_path=target_db,
        data_dir=target_dir,
    )

    assert recovered.status == "applied"
    assert recovered.workspace_generation == 2
    assert not receipt_path.exists()
    assert not (queue_root / workspace_backup.PENDING_RESTORE_FILENAME).exists()
    assert not queued_backup.exists()
    assert not transaction_root.exists()
    with closing(sqlite3.connect(target_db)) as conn:
        assert conn.execute(
            "SELECT label FROM workspace_marker"
        ).fetchone()[0] == "finalize-crash-source"
    state = get_workspace_restore_state(data_dir=target_dir)
    assert state.pending is None
    assert state.workspace_generation == 2
    assert state.last_result == recovered


@pytest.mark.parametrize("had_existing_workspace", [True, False])
@pytest.mark.parametrize(
    "crash_boundary",
    ["commit-fence", "workspace-state", "restore-result"],
)
def test_finalizing_fence_resumes_state_and_result_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    had_existing_workspace: bool,
    crash_boundary: str,
) -> None:
    source_dir, source_db = _workspace(
        tmp_path,
        label="commit-fence-source",
    )
    backup = create_workspace_backup(
        db_path=source_db,
        data_dir=source_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    if had_existing_workspace:
        target_dir, target_db = _workspace(
            tmp_path,
            label="commit-fence-target",
        )
    else:
        target_dir = tmp_path / "commit-fence-clean-target"
        target_dir.mkdir()
        target_db = target_dir / "data" / "jobs.db"
    queue_workspace_restore(
        backup.path,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    staged = apply_pending_workspace_restore(
        db_path=target_db,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    assert staged is not None

    real_replace = workspace_backup._replace_atomic_json
    real_persist_result = workspace_backup._persist_restore_result
    real_cleanup = workspace_backup._cleanup_finalized_restore

    class SimulatedCommitCrash(BaseException):
        pass

    if crash_boundary == "commit-fence":

        def replace_fence_then_crash(path: Path, value: object) -> None:
            real_replace(path, value)
            if (
                path.name == workspace_backup.RESTORE_RECEIPT_FILENAME
                and isinstance(value, dict)
                and value.get("phase") == "finalizing"
            ):
                raise SimulatedCommitCrash()

        monkeypatch.setattr(
            workspace_backup,
            "_replace_atomic_json",
            replace_fence_then_crash,
        )
    elif crash_boundary == "workspace-state":

        def crash_before_result(*args, **kwargs) -> None:
            raise SimulatedCommitCrash()

        monkeypatch.setattr(
            workspace_backup,
            "_persist_restore_result",
            crash_before_result,
        )
    else:

        def crash_before_cleanup(*args, **kwargs) -> None:
            raise SimulatedCommitCrash()

        monkeypatch.setattr(
            workspace_backup,
            "_cleanup_finalized_restore",
            crash_before_cleanup,
        )

    with pytest.raises(SimulatedCommitCrash):
        finalize_pending_workspace_restore(
            staged.restore_id,
            db_path=target_db,
            data_dir=target_dir,
        )

    receipt_path = (
        target_dir
        / "restore"
        / workspace_backup.RESTORE_RECEIPT_FILENAME
    )
    interrupted = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert interrupted["phase"] == "finalizing"
    assert isinstance(interrupted["committed_at"], str)
    with closing(sqlite3.connect(target_db)) as conn:
        assert conn.execute(
            "SELECT label FROM workspace_marker"
        ).fetchone()[0] == "commit-fence-source"

    monkeypatch.setattr(
        workspace_backup,
        "_replace_atomic_json",
        real_replace,
    )
    monkeypatch.setattr(
        workspace_backup,
        "_persist_restore_result",
        real_persist_result,
    )
    monkeypatch.setattr(
        workspace_backup,
        "_cleanup_finalized_restore",
        real_cleanup,
    )
    recovered = apply_pending_workspace_restore(
        db_path=target_db,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )

    assert recovered is not None
    assert recovered.status == "applied"
    assert recovered.workspace_generation == 2
    assert not receipt_path.exists()
    state = get_workspace_restore_state(data_dir=target_dir)
    assert state.workspace_generation == 2
    assert state.last_result == recovered
    with closing(sqlite3.connect(target_db)) as conn:
        assert conn.execute(
            "SELECT label FROM workspace_marker"
        ).fetchone()[0] == "commit-fence-source"


def test_startup_validation_failure_rolls_back_staged_restore(
    tmp_path: Path,
) -> None:
    source_dir, source_db = _workspace(
        tmp_path,
        label="startup-failure-source",
    )
    backup = create_workspace_backup(
        db_path=source_db,
        data_dir=source_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    target_dir, target_db = _workspace(
        tmp_path,
        label="startup-failure-target",
    )
    pending = queue_workspace_restore(
        backup.path,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    staged = apply_pending_workspace_restore(
        db_path=target_db,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )

    assert staged is not None
    rolled_back = rollback_pending_workspace_restore(
        staged.restore_id,
        db_path=target_db,
        data_dir=target_dir,
        error="injected startup validation failure",
        now=FIXED_TIME,
    )

    assert rolled_back.status == "failed"
    assert rolled_back.restore_id == pending.restore_id
    assert "startup validation" in (rolled_back.error or "")
    with closing(sqlite3.connect(target_db)) as conn:
        assert conn.execute(
            "SELECT label FROM workspace_marker"
        ).fetchone()[0] == "startup-failure-target"
    assert (target_dir / "uploads" / "video-1.mp4").read_bytes() == (
        b"startup-failure-target-video"
    )
    state = get_workspace_restore_state(data_dir=target_dir)
    assert state.pending is None
    assert state.workspace_generation == 1
    assert state.last_result == rolled_back


def test_interrupted_explicit_rollback_resumes_before_database_init(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir, source_db = _workspace(
        tmp_path,
        label="rollback-crash-source",
    )
    backup = create_workspace_backup(
        db_path=source_db,
        data_dir=source_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    target_dir, target_db = _workspace(
        tmp_path,
        label="rollback-crash-target",
    )
    queue_workspace_restore(
        backup.path,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    staged = apply_pending_workspace_restore(
        db_path=target_db,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    assert staged is not None

    receipt_path = (
        target_dir
        / "restore"
        / workspace_backup.RESTORE_RECEIPT_FILENAME
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    rollback_database = (
        target_dir
        / "restore"
        / receipt["transaction_name"]
        / "rollback"
        / "database.sqlite3"
    )
    real_move = workspace_backup._move_path

    class SimulatedRollbackCrash(BaseException):
        pass

    def crash_before_original_database_publish(
        source: Path,
        destination: Path,
    ) -> None:
        if source == rollback_database and destination == target_db:
            raise SimulatedRollbackCrash()
        real_move(source, destination)

    monkeypatch.setattr(
        workspace_backup,
        "_move_path",
        crash_before_original_database_publish,
    )
    with pytest.raises(SimulatedRollbackCrash):
        rollback_pending_workspace_restore(
            staged.restore_id,
            db_path=target_db,
            data_dir=target_dir,
            error="injected initialization failure",
        )

    interrupted_receipt = json.loads(
        receipt_path.read_text(encoding="utf-8")
    )
    assert interrupted_receipt["phase"] == "rolling_back"
    assert get_pending_workspace_restore(
        data_dir=target_dir
    ).phase == "swapping"
    monkeypatch.setattr(workspace_backup, "_move_path", real_move)

    recovered = apply_pending_workspace_restore(
        db_path=target_db,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )

    assert recovered is not None
    assert recovered.status == "failed"
    assert "rollback was interrupted" in (recovered.error or "").lower()
    with closing(sqlite3.connect(target_db)) as conn:
        assert conn.execute(
            "SELECT label FROM workspace_marker"
        ).fetchone()[0] == "rollback-crash-target"


@pytest.mark.parametrize("had_existing_workspace", [True, False])
@pytest.mark.parametrize(
    "crash_boundary",
    ["marker", "result", "receipt"],
)
def test_rolled_back_restore_resumes_failure_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    had_existing_workspace: bool,
    crash_boundary: str,
) -> None:
    source_dir, source_db = _workspace(
        tmp_path,
        label="rollback-finalize-source",
    )
    backup = create_workspace_backup(
        db_path=source_db,
        data_dir=source_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    if had_existing_workspace:
        target_dir, target_db = _workspace(
            tmp_path,
            label="rollback-finalize-target",
        )
    else:
        target_dir = tmp_path / "rollback-finalize-clean-target"
        target_dir.mkdir()
        target_db = target_dir / "data" / "jobs.db"
    queue_workspace_restore(
        backup.path,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    staged = apply_pending_workspace_restore(
        db_path=target_db,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    assert staged is not None
    queue_root = target_dir / "restore"
    receipt_path = queue_root / workspace_backup.RESTORE_RECEIPT_FILENAME
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    transaction_root = queue_root / receipt["transaction_name"]
    real_remove = workspace_backup._remove_finalized_restore_path
    real_persist_result = workspace_backup._persist_restore_result

    class SimulatedRollbackFinalizationCrash(BaseException):
        pass

    if crash_boundary in {"marker", "receipt"}:
        expected_name = (
            workspace_backup.PENDING_RESTORE_FILENAME
            if crash_boundary == "marker"
            else workspace_backup.RESTORE_RECEIPT_FILENAME
        )

        def remove_then_crash(
            path: Path,
            *,
            recursive: bool = False,
        ) -> None:
            real_remove(path, recursive=recursive)
            if path.name == expected_name:
                raise SimulatedRollbackFinalizationCrash()

        monkeypatch.setattr(
            workspace_backup,
            "_remove_finalized_restore_path",
            remove_then_crash,
        )
    else:

        def persist_then_crash(*args, **kwargs) -> None:
            real_persist_result(*args, **kwargs)
            raise SimulatedRollbackFinalizationCrash()

        monkeypatch.setattr(
            workspace_backup,
            "_persist_restore_result",
            persist_then_crash,
        )

    with pytest.raises(SimulatedRollbackFinalizationCrash):
        rollback_pending_workspace_restore(
            staged.restore_id,
            db_path=target_db,
            data_dir=target_dir,
            error="injected startup validation failure",
        )

    if receipt_path.exists():
        interrupted = json.loads(receipt_path.read_text(encoding="utf-8"))
        assert interrupted["phase"] == "rollback_finalizing"
    monkeypatch.setattr(
        workspace_backup,
        "_remove_finalized_restore_path",
        real_remove,
    )
    monkeypatch.setattr(
        workspace_backup,
        "_persist_restore_result",
        real_persist_result,
    )
    resumed = apply_pending_workspace_restore(
        db_path=target_db,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    state = get_workspace_restore_state(data_dir=target_dir)
    result = resumed or state.last_result

    assert result is not None
    assert result.status == "failed"
    assert result.workspace_generation == 1
    assert state.workspace_generation == 1
    assert state.last_result == result
    assert not receipt_path.exists()
    assert not (queue_root / workspace_backup.PENDING_RESTORE_FILENAME).exists()
    assert not transaction_root.exists()
    assert result.failure_record_path is not None
    assert result.failure_record_path.is_file()
    if had_existing_workspace:
        with closing(sqlite3.connect(target_db)) as conn:
            assert conn.execute(
                "SELECT label FROM workspace_marker"
            ).fetchone()[0] == "rollback-finalize-target"
    else:
        assert not target_db.exists()


def test_restore_failure_rolls_back_and_quarantines_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir, source_db = _workspace(tmp_path, label="rollback-source")
    backup = create_workspace_backup(
        db_path=source_db,
        data_dir=source_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    target_dir, target_db = _workspace(tmp_path, label="rollback-target")
    pending = queue_workspace_restore(
        backup.path,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    real_move = workspace_backup._move_path
    injected = False

    def fail_once(source: Path, destination: Path) -> None:
        nonlocal injected
        if (
            not injected
            and source.name == "audio"
            and "extracted" in source.parts
        ):
            injected = True
            raise OSError("injected swap failure")
        real_move(source, destination)

    monkeypatch.setattr(workspace_backup, "_move_path", fail_once)

    result = apply_pending_workspace_restore(
        db_path=target_db,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )

    assert result is not None
    assert result.status == "failed"
    assert "injected swap failure" in (result.error or "")
    assert result.pre_restore_backup_path is not None
    assert result.pre_restore_backup_path.is_file()
    assert result.failure_record_path is not None
    assert result.failure_record_path.is_file()
    assert not pending.marker_path.exists()
    assert not pending.backup_path.exists()
    assert (target_dir / "uploads" / "video-1.mp4").read_bytes() == (
        b"rollback-target-video"
    )
    assert (target_dir / "audio" / "video-1.wav").read_bytes() == (
        b"rollback-target-audio"
    )
    with closing(sqlite3.connect(target_db)) as conn:
        marker = conn.execute(
            "SELECT label FROM workspace_marker"
        ).fetchone()[0]
    assert marker == "rollback-target"


def test_swap_failure_resumes_rollback_result_publication_after_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir, source_db = _workspace(
        tmp_path,
        label="apply-crash-source",
    )
    backup = create_workspace_backup(
        db_path=source_db,
        data_dir=source_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    target_dir, target_db = _workspace(
        tmp_path,
        label="apply-crash-target",
    )
    queue_workspace_restore(
        backup.path,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    real_move = workspace_backup._move_path
    real_persist_result = workspace_backup._persist_restore_result
    injected = False

    class SimulatedFailurePublicationCrash(BaseException):
        pass

    def fail_swap_once(source: Path, destination: Path) -> None:
        nonlocal injected
        if (
            not injected
            and source.name == "audio"
            and "extracted" in source.parts
        ):
            injected = True
            raise OSError("injected swap failure before rollback result")
        real_move(source, destination)

    def crash_before_result(*args, **kwargs) -> None:
        raise SimulatedFailurePublicationCrash()

    monkeypatch.setattr(workspace_backup, "_move_path", fail_swap_once)
    monkeypatch.setattr(
        workspace_backup,
        "_persist_restore_result",
        crash_before_result,
    )

    with pytest.raises(SimulatedFailurePublicationCrash):
        apply_pending_workspace_restore(
            db_path=target_db,
            data_dir=target_dir,
            current_schema_version=SCHEMA_VERSION,
        )

    queue_root = target_dir / "restore"
    receipt_path = queue_root / workspace_backup.RESTORE_RECEIPT_FILENAME
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["phase"] == "rollback_finalizing"
    assert not (
        queue_root / workspace_backup.PENDING_RESTORE_FILENAME
    ).exists()
    assert not (queue_root / receipt["transaction_name"]).exists()
    with closing(sqlite3.connect(target_db)) as conn:
        assert conn.execute(
            "SELECT label FROM workspace_marker"
        ).fetchone()[0] == "apply-crash-target"

    monkeypatch.setattr(workspace_backup, "_move_path", real_move)
    monkeypatch.setattr(
        workspace_backup,
        "_persist_restore_result",
        real_persist_result,
    )
    resumed = apply_pending_workspace_restore(
        db_path=target_db,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    state = get_workspace_restore_state(data_dir=target_dir)

    assert resumed is not None
    assert resumed.status == "failed"
    assert resumed.workspace_generation == 1
    assert state.workspace_generation == 1
    assert state.last_result == resumed
    assert not receipt_path.exists()


def test_tampered_queued_backup_is_rejected_before_workspace_changes(
    tmp_path: Path,
) -> None:
    source_dir, source_db = _workspace(tmp_path, label="queued-source")
    backup = create_workspace_backup(
        db_path=source_db,
        data_dir=source_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    target_dir, target_db = _workspace(tmp_path, label="queued-target")
    pending = queue_workspace_restore(
        backup.path,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )
    with pending.backup_path.open("ab") as output:
        output.write(b"tampered-after-queue")

    result = apply_pending_workspace_restore(
        db_path=target_db,
        data_dir=target_dir,
        current_schema_version=SCHEMA_VERSION,
    )

    assert result is not None
    assert result.status == "failed"
    assert "hash" in (result.error or "").lower()
    with closing(sqlite3.connect(target_db)) as conn:
        marker = conn.execute(
            "SELECT label FROM workspace_marker"
        ).fetchone()[0]
    assert marker == "queued-target"
