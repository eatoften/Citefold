from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import NoReturn

from .settings import get_app_path_settings
from .trash import (
    COURSE_PURGE_ARTIFACT_FIELDS,
    COURSE_PURGE_MANAGED_ROOTS,
    COURSE_PURGE_METADATA_KEY,
    COURSE_PURGE_PHASES,
    COURSE_PURGE_PLAN_FIELDS,
    COURSE_PURGE_PLAN_VERSION,
    ENTITY_PURGE_METADATA_KEY,
    ENTITY_PURGE_PHASES,
    ENTITY_PURGE_PLAN_VERSION,
    ENTITY_PURGE_TYPES,
    ENTITY_SOURCE_EXTENSIONS,
    ENTITY_VIDEO_EXTENSIONS,
    TrashItem,
    TrashItemStatus,
    validate_entity_purge_plan,
    validate_entity_relative_path,
)
from .trash_store import (
    compare_and_set_trash_item_status,
    get_trash_item,
    list_trash_items,
    remove_claimed_trash_item,
    update_claimed_trash_item_metadata,
)
from .workspace_lifecycle import workspace_lifecycle_lock


_RESTORE_CLAIMABLE: tuple[TrashItemStatus, ...] = (
    "trashed",
    "restore_failed",
)
_PURGE_CLAIMABLE: tuple[TrashItemStatus, ...] = (
    "trashed",
    "restore_failed",
    "purge_failed",
)
class TrashServiceError(Exception):
    pass


class TrashItemNotFoundError(TrashServiceError):
    pass


class TrashOperationError(TrashServiceError):
    pass


def list_workspace_trash(
    *,
    course_id: str | None = None,
) -> list[TrashItem]:
    return list_trash_items(course_id=course_id)


def restore_workspace_trash_item(item_id: str) -> TrashItem:
    with workspace_lifecycle_lock():
        item = _claim_operation(
            item_id,
            expected_statuses=_RESTORE_CLAIMABLE,
            status="restoring",
            operation="restore",
        )
        try:
            _restore(item)
            _finalize_operation(item.id, expected_status="restoring")
        except Exception as exc:
            _mark_operation_failed(
                item.id,
                expected_status="restoring",
                failed_status="restore_failed",
            )
            if isinstance(exc, TrashServiceError):
                raise
            raise TrashOperationError(
                f"Could not restore {item.display_name}."
            ) from exc
        return item


def purge_workspace_trash_item(
    item_id: str,
    *,
    artifact_root: Path | None = None,
) -> TrashItem:
    with workspace_lifecycle_lock():
        item = _claim_operation(
            item_id,
            expected_statuses=_PURGE_CLAIMABLE,
            status="purging",
            operation="permanently delete",
        )
        resolved_artifact_root = (
            artifact_root or get_app_path_settings().data_dir
        )
        try:
            if item.entity_type == "course":
                item = _prepare_course_purge(
                    item,
                    artifact_root=resolved_artifact_root,
                )
            elif item.entity_type in ENTITY_PURGE_TYPES:
                item = _prepare_entity_purge(
                    item,
                    artifact_root=resolved_artifact_root,
                )
            _purge(item, artifact_root=resolved_artifact_root)
            _finalize_operation(item.id, expected_status="purging")
        except Exception as exc:
            _mark_operation_failed(
                item.id,
                expected_status="purging",
                failed_status="purge_failed",
            )
            if isinstance(exc, TrashServiceError):
                raise
            raise TrashOperationError(
                f"Could not permanently delete {item.display_name}."
            ) from exc
        return item


def _claim_operation(
    item_id: str,
    *,
    expected_statuses: tuple[TrashItemStatus, ...],
    status: TrashItemStatus,
    operation: str,
) -> TrashItem:
    claimed = compare_and_set_trash_item_status(
        item_id,
        expected_statuses=expected_statuses,
        status=status,
    )
    if claimed is not None:
        return claimed
    current = get_trash_item(item_id)
    if current is None:
        raise TrashItemNotFoundError("Trash item not found.")
    if current.status in {"purging", "purge_failed"} and status == "restoring":
        raise TrashOperationError(
            "Permanent deletion already started for this item; it can no "
            "longer be restored."
        )
    raise TrashOperationError(
        f"Cannot {operation} this item while its trash state is "
        f"{current.status!r}."
    )


def _mark_operation_failed(
    item_id: str,
    *,
    expected_status: TrashItemStatus,
    failed_status: TrashItemStatus,
) -> None:
    compare_and_set_trash_item_status(
        item_id,
        expected_statuses=(expected_status,),
        status=failed_status,
    )


def _finalize_operation(
    item_id: str,
    *,
    expected_status: TrashItemStatus,
) -> None:
    current = get_trash_item(item_id)
    if current is None:
        # Existing entity stores finalize their own tombstones. Course purge
        # deliberately preserves its root tombstone until artifact cleanup.
        return
    if not remove_claimed_trash_item(
        item_id,
        expected_status=expected_status,
    ):
        raise TrashOperationError(
            "Trash operation lost ownership before it could be finalized."
        )


def _restore(item: TrashItem) -> None:
    if item.entity_type == "course":
        from .course_service import restore_video_course

        restore_video_course(item.entity_id)
        return
    if item.entity_type == "video_job":
        from .job_service import restore_video_job

        restore_video_job(item.entity_id)
        return
    if item.entity_type == "source_asset":
        from .source_asset_service import restore_deleted_source_asset

        restore_deleted_source_asset(item.entity_id)
        return
    if item.entity_type == "knowledge_card":
        from .knowledge_card_service import restore_saved_card

        restore_saved_card(item.entity_id)
        return
    if item.entity_type == "learning_document":
        from .learning_document_service import (
            restore_saved_learning_document,
        )

        restore_saved_learning_document(item.entity_id)
        return
    if item.entity_type == "chat_conversation":
        from .chat_service import restore_chat_conversation

        restore_chat_conversation(item.entity_id)
        return
    raise TrashOperationError(
        f"Unsupported trash entity type: {item.entity_type}"
    )


def _purge(item: TrashItem, *, artifact_root: Path) -> None:
    if item.entity_type == "course":
        _purge_course(item, artifact_root=artifact_root)
        return
    if item.entity_type == "video_job":
        _purge_entity(item, artifact_root=artifact_root)
        return
    if item.entity_type == "source_asset":
        _purge_entity(item, artifact_root=artifact_root)
        return
    if item.entity_type == "knowledge_card":
        from .knowledge_card_service import purge_saved_card

        purge_saved_card(item.entity_id)
        return
    if item.entity_type == "learning_document":
        from .learning_document_service import purge_saved_learning_document

        purge_saved_learning_document(item.entity_id)
        return
    if item.entity_type == "chat_conversation":
        from .chat_service import purge_chat_conversation

        purge_chat_conversation(item.entity_id)
        return
    raise TrashOperationError(
        f"Unsupported trash entity type: {item.entity_type}"
    )


def _prepare_entity_purge(
    item: TrashItem,
    *,
    artifact_root: Path,
) -> TrashItem:
    existing = item.metadata.get(ENTITY_PURGE_METADATA_KEY)
    if existing is not None:
        _validate_entity_purge_plan(existing, item=item)
        return item

    metadata = dict(item.metadata)
    metadata[ENTITY_PURGE_METADATA_KEY] = {
        "version": ENTITY_PURGE_PLAN_VERSION,
        "entity_type": item.entity_type,
        "phase": "planned",
        "artifacts": _collect_entity_artifacts(
            item,
            artifact_root=artifact_root,
        ),
    }
    updated = update_claimed_trash_item_metadata(
        item.id,
        expected_status="purging",
        metadata=metadata,
    )
    if updated is None:
        raise TrashOperationError(
            "Entity purge lost ownership while its recovery plan was saved."
        )
    return updated


def _validate_entity_purge_plan(
    value: object,
    *,
    item: TrashItem,
) -> dict[str, object]:
    try:
        return validate_entity_purge_plan(
            value,
            entity_type=item.entity_type,
            entity_id=item.entity_id,
            course_id=item.course_id,
        )
    except ValueError as exc:
        raise TrashOperationError(str(exc)) from exc


def _collect_entity_artifacts(
    item: TrashItem,
    *,
    artifact_root: Path,
) -> list[dict[str, str]]:
    if item.entity_type == "video_job":
        from .job_store import get_job

        job = get_job(item.entity_id, include_deleted=True)
        if job is None:
            raise TrashOperationError(
                "Deleted video data was missing before its purge plan "
                "could be created."
            )
        artifacts = [
            _entity_artifact_record(
                job.video_path,
                root=artifact_root / "uploads",
                root_name="uploads",
            )
        ]
        if job.transcript_path is not None:
            artifacts.append(
                _entity_artifact_record(
                    job.transcript_path,
                    root=artifact_root / "transcripts",
                    root_name="transcripts",
                )
            )
        artifacts.append(
            _entity_artifact_record(
                artifact_root / "audio" / f"{job.video_path.stem}.wav",
                root=artifact_root / "audio",
                root_name="audio",
            )
        )
        return artifacts

    if item.entity_type == "source_asset":
        from .source_asset_store import get_source_asset

        asset = get_source_asset(item.entity_id, include_deleted=True)
        if asset is None:
            raise TrashOperationError(
                "Deleted source data was missing before its purge plan "
                "could be created."
            )
        source_root = get_app_path_settings().source_dir
        return [
            _entity_artifact_record(
                Path(asset.stored_path),
                root=source_root,
                root_name="sources",
            )
        ]

    raise TrashOperationError(
        f"Unsupported entity purge type: {item.entity_type}"
    )


def _entity_artifact_record(
    path: Path,
    *,
    root: Path,
    root_name: str,
) -> dict[str, str]:
    try:
        resolved_path = path.resolve()
        resolved_root = root.resolve()
        relative = resolved_path.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise TrashOperationError(
            "A managed entity artifact is outside its configured root."
        ) from exc
    if not relative.parts:
        raise TrashOperationError(
            "A managed entity artifact cannot be its configured root."
        )
    relative_path = PurePosixPath(*relative.parts).as_posix()
    try:
        validate_entity_relative_path(relative_path)
    except ValueError as exc:
        raise TrashOperationError(str(exc)) from exc
    return {"root": root_name, "relative_path": relative_path}


def _purge_entity(item: TrashItem, *, artifact_root: Path) -> None:
    plan = _validate_entity_purge_plan(
        item.metadata.get(ENTITY_PURGE_METADATA_KEY),
        item=item,
    )
    completed_phase = str(plan["phase"])

    if not _entity_phase_completed(completed_phase, "projection"):
        _purge_entity_projection(item)
        item = _mark_entity_purge_phase(item, "projection")
        completed_phase = "projection"

    if not _entity_phase_completed(completed_phase, "database"):
        _purge_entity_database(item)
        item = _mark_entity_purge_phase(item, "database")
        completed_phase = "database"

    if not _entity_phase_completed(completed_phase, "artifacts"):
        _purge_entity_artifacts(
            plan,
            item=item,
            artifact_root=artifact_root,
        )
        _mark_entity_purge_phase(item, "artifacts")


def _entity_phase_completed(completed_phase: str, candidate: str) -> bool:
    return ENTITY_PURGE_PHASES.index(completed_phase) >= (
        ENTITY_PURGE_PHASES.index(candidate)
    )


def _mark_entity_purge_phase(
    item: TrashItem,
    phase: str,
) -> TrashItem:
    metadata = dict(item.metadata)
    plan = dict(
        _validate_entity_purge_plan(
            metadata.get(ENTITY_PURGE_METADATA_KEY),
            item=item,
        )
    )
    plan["phase"] = phase
    metadata[ENTITY_PURGE_METADATA_KEY] = plan
    updated = update_claimed_trash_item_metadata(
        item.id,
        expected_status="purging",
        metadata=metadata,
    )
    if updated is None:
        raise TrashOperationError(
            "Entity purge lost ownership while progress was saved."
        )
    return updated


def _purge_entity_projection(item: TrashItem) -> None:
    from . import course_source_service

    if item.entity_type == "video_job":
        course_source_service.remove_video_source(item.entity_id)
    elif item.entity_type == "source_asset":
        course_source_service.remove_asset_source(item.entity_id)
    else:
        raise TrashOperationError(
            f"Unsupported entity purge type: {item.entity_type}"
        )


def _purge_entity_database(item: TrashItem) -> None:
    if item.entity_type == "video_job":
        from .job_service import purge_video_job_records

        purge_video_job_records(
            item.entity_id,
            preserve_trash_item=True,
            allow_missing=True,
        )
        return
    if item.entity_type == "source_asset":
        from .source_asset_service import purge_source_asset_records

        purge_source_asset_records(
            item.entity_id,
            preserve_trash_item=True,
            allow_missing=True,
        )
        return
    raise TrashOperationError(
        f"Unsupported entity purge type: {item.entity_type}"
    )


def _purge_entity_artifacts(
    plan: dict[str, object],
    *,
    item: TrashItem,
    artifact_root: Path,
) -> None:
    validated = _validate_entity_purge_plan(plan, item=item)
    artifacts = validated["artifacts"]
    assert isinstance(artifacts, list)
    settings = get_app_path_settings()
    roots = {
        "uploads": artifact_root / "uploads",
        "transcripts": artifact_root / "transcripts",
        "audio": artifact_root / "audio",
        "sources": settings.source_dir,
    }
    resolved_artifacts: list[Path] = []
    for artifact in artifacts:
        assert isinstance(artifact, dict)
        root = roots[str(artifact["root"])]
        try:
            relative = validate_entity_relative_path(
                str(artifact["relative_path"])
            )
        except ValueError as exc:
            raise TrashOperationError(str(exc)) from exc
        try:
            resolved_root = root.resolve()
            path = resolved_root.joinpath(*relative.parts)
            resolved_path = path.resolve()
        except OSError as exc:
            raise TrashOperationError(
                "A managed entity artifact path could not be resolved."
            ) from exc
        if (
            resolved_path == resolved_root
            or resolved_root not in resolved_path.parents
        ):
            raise TrashOperationError(
                "A managed entity artifact is outside its configured root."
            )
        resolved_artifacts.append(resolved_path)

    _assert_no_foreign_record_references(
        resolved_artifacts,
        artifact_root=artifact_root,
    )
    for resolved_path in resolved_artifacts:
        try:
            resolved_path.unlink(missing_ok=True)
        except OSError as exc:
            raise TrashOperationError(
                "Could not delete a managed entity artifact."
            ) from exc


def _prepare_course_purge(
    item: TrashItem,
    *,
    artifact_root: Path,
) -> TrashItem:
    existing = item.metadata.get(COURSE_PURGE_METADATA_KEY)
    if existing is not None:
        _validate_course_purge_plan(
            existing,
            workspace_root=artifact_root,
            expected_course_id=item.entity_id,
        )
        return item

    metadata = dict(item.metadata)
    metadata[COURSE_PURGE_METADATA_KEY] = {
        "version": COURSE_PURGE_PLAN_VERSION,
        "course_id": item.entity_id,
        "phase": "planned",
        "artifacts": _collect_course_artifacts(
            item.entity_id,
            artifact_root=artifact_root,
        ),
    }
    updated = update_claimed_trash_item_metadata(
        item.id,
        expected_status="purging",
        metadata=metadata,
    )
    if updated is None:
        raise TrashOperationError(
            "Course purge lost ownership while its recovery plan was saved."
        )
    return updated


def _validate_course_purge_plan(
    value: object,
    *,
    workspace_root: Path | None = None,
    expected_course_id: str | None = None,
) -> dict[str, object]:
    if (
        not isinstance(value, dict)
        or set(value) != COURSE_PURGE_PLAN_FIELDS
        or type(value.get("version")) is not int
        or value.get("version") != COURSE_PURGE_PLAN_VERSION
    ):
        raise TrashOperationError("Course purge recovery plan is invalid.")
    course_id = value.get("course_id")
    phase = value.get("phase")
    artifacts = value.get("artifacts")
    if (
        not isinstance(course_id, str)
        or not course_id
        or any(character in course_id for character in ("/", "\\"))
        or (
            expected_course_id is not None
            and course_id != expected_course_id
        )
        or phase not in COURSE_PURGE_PHASES
        or not isinstance(artifacts, list)
    ):
        raise TrashOperationError("Course purge recovery plan is invalid.")
    seen: set[tuple[str, str]] = set()
    for artifact in artifacts:
        if (
            not isinstance(artifact, dict)
            or set(artifact) != COURSE_PURGE_ARTIFACT_FIELDS
            or not isinstance(artifact.get("path"), str)
            or not isinstance(artifact.get("root"), str)
        ):
            raise TrashOperationError("Course purge recovery plan is invalid.")
        identity = (str(artifact["path"]), str(artifact["root"]))
        if identity in seen:
            raise TrashOperationError(
                "Course purge recovery plan contains duplicate artifacts."
            )
        seen.add(identity)
        if workspace_root is not None:
            _validate_planned_artifact(
                artifact,
                workspace_root=workspace_root,
                course_id=course_id,
            )
    return value


def _validate_planned_artifact(
    artifact: dict[str, object],
    *,
    workspace_root: Path,
    course_id: str,
) -> tuple[Path, Path, str]:
    path = Path(str(artifact["path"]))
    root = Path(str(artifact["root"]))
    if not path.is_absolute() or not root.is_absolute():
        raise TrashOperationError(
            "Course purge recovery plan contains a non-absolute path."
        )
    try:
        resolved_workspace = workspace_root.resolve()
        resolved_path = path.resolve()
        resolved_root = root.resolve()
    except OSError as exc:
        raise TrashOperationError(
            "A managed course artifact path could not be resolved."
        ) from exc

    matching_managed_roots = [
        (name, (resolved_workspace / name).resolve())
        for name in COURSE_PURGE_MANAGED_ROOTS
        if (resolved_workspace / name).resolve() in resolved_path.parents
    ]
    if len(matching_managed_roots) != 1:
        raise TrashOperationError(
            "A managed course artifact is outside the current workspace."
        )
    managed_root_name, managed_root = matching_managed_roots[0]
    if resolved_root != managed_root:
        raise TrashOperationError(
            "A managed course artifact root is outside the current workspace."
        )
    relative = resolved_path.relative_to(managed_root)
    if (
        managed_root_name == "sources"
        and (
            len(relative.parts) != 2
            or relative.parts[0] != course_id
        )
    ) or (
        managed_root_name != "sources"
        and len(relative.parts) != 1
    ):
        raise TrashOperationError(
            "A managed course artifact is outside the course namespace."
        )
    return resolved_path, resolved_root, managed_root_name


def _collect_course_artifacts(
    course_id: str,
    *,
    artifact_root: Path,
) -> list[dict[str, str]]:
    from .job_store import list_jobs_for_course
    from .source_asset_store import list_source_assets_for_course

    records: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(path: Path, root: Path) -> None:
        try:
            resolved_path = path.resolve()
            resolved_root = root.resolve()
        except OSError as exc:
            raise TrashOperationError(
                "A managed course artifact path could not be resolved."
            ) from exc
        if (
            resolved_path == resolved_root
            or resolved_root not in resolved_path.parents
        ):
            raise TrashOperationError(
                "A managed course artifact is outside its configured root."
            )
        key = (str(resolved_path), str(resolved_root))
        if key in seen:
            return
        seen.add(key)
        records.append({"path": key[0], "root": key[1]})

    for job in list_jobs_for_course(course_id, include_deleted=True):
        add(job.video_path, artifact_root / "uploads")
        if job.transcript_path is not None:
            add(job.transcript_path, artifact_root / "transcripts")
        add(
            artifact_root / "audio" / f"{job.video_path.stem}.wav",
            artifact_root / "audio",
        )

    source_root = artifact_root / "sources"
    for asset in list_source_assets_for_course(
        course_id,
        include_deleted=True,
    ):
        add(Path(asset.stored_path), source_root)
    return records


def _purge_course(item: TrashItem, *, artifact_root: Path) -> None:
    """Resume an irreversible course purge from its last completed phase.

    Existing stores own separate transactions and file side effects, so one
    SQLite transaction cannot safely reuse them. The persisted phase plan makes
    every completed stage monotonic and retryable. The item stays in a purge
    state until all database rows and managed files are gone, and can therefore
    never expose a partially purged course through restore.
    """

    _assert_no_unfinished_child_entity_purges(item.entity_id)
    plan = _validate_course_purge_plan(
        item.metadata.get(COURSE_PURGE_METADATA_KEY),
        workspace_root=artifact_root,
        expected_course_id=item.entity_id,
    )
    completed_phase = str(plan["phase"])

    pre_artifact_stages = (
        ("conversations", _purge_course_conversations),
        ("documents", _purge_course_documents),
    )
    for phase, operation in pre_artifact_stages:
        if _phase_completed(completed_phase, phase):
            continue
        operation(item.entity_id, artifact_root)
        item = _mark_course_purge_phase(item, phase)
        completed_phase = phase

    if not _phase_completed(completed_phase, "artifacts"):
        _assert_course_artifact_plan_matches_records(
            plan,
            course_id=item.entity_id,
            artifact_root=artifact_root,
        )
        _purge_planned_artifacts(
            plan,
            workspace_root=artifact_root,
        )
        item = _mark_course_purge_phase(item, "artifacts")
        completed_phase = "artifacts"

    post_artifact_stages = (
        ("assets", _purge_course_assets),
        ("jobs", _purge_course_jobs),
        ("topics", _purge_course_topics),
        ("course", _purge_course_record),
    )
    for phase, operation in post_artifact_stages:
        if _phase_completed(completed_phase, phase):
            continue
        operation(item.entity_id, artifact_root)
        item = _mark_course_purge_phase(item, phase)
        completed_phase = phase


def _phase_completed(completed_phase: str, candidate: str) -> bool:
    return COURSE_PURGE_PHASES.index(completed_phase) >= (
        COURSE_PURGE_PHASES.index(candidate)
    )


def _mark_course_purge_phase(item: TrashItem, phase: str) -> TrashItem:
    metadata = dict(item.metadata)
    plan = dict(
        _validate_course_purge_plan(
            metadata.get(COURSE_PURGE_METADATA_KEY),
            expected_course_id=item.entity_id,
        )
    )
    plan["phase"] = phase
    metadata[COURSE_PURGE_METADATA_KEY] = plan
    updated = update_claimed_trash_item_metadata(
        item.id,
        expected_status="purging",
        metadata=metadata,
    )
    if updated is None:
        raise TrashOperationError(
            "Course purge lost ownership while progress was saved."
        )
    return updated


def _assert_course_artifact_plan_matches_records(
    plan: dict[str, object],
    *,
    course_id: str,
    artifact_root: Path,
) -> None:
    artifacts = plan["artifacts"]
    assert isinstance(artifacts, list)
    actual = {
        (
            str(Path(str(artifact["path"])).resolve()),
            str(Path(str(artifact["root"])).resolve()),
        )
        for artifact in artifacts
        if isinstance(artifact, dict)
    }
    expected_records = _collect_course_artifacts(
        course_id,
        artifact_root=artifact_root,
    )
    expected = {
        (
            str(Path(record["path"]).resolve()),
            str(Path(record["root"]).resolve()),
        )
        for record in expected_records
    }
    if actual != expected:
        raise TrashOperationError(
            "Course purge recovery plan does not match the course records."
        )


def _assert_no_unfinished_child_entity_purges(course_id: str) -> None:
    """Keep a retryable child journal until its last file is durably gone."""

    from .job_store import get_job
    from .source_asset_store import get_source_asset

    for child in list_trash_items(course_id=course_id):
        if child.entity_type not in ENTITY_PURGE_TYPES:
            continue
        raw_plan = child.metadata.get(ENTITY_PURGE_METADATA_KEY)
        if raw_plan is None:
            continue
        plan = _validate_entity_purge_plan(raw_plan, item=child)
        phase = str(plan["phase"])
        if _entity_phase_completed(phase, "artifacts"):
            continue
        if child.entity_type == "video_job":
            record_exists = (
                get_job(child.entity_id, include_deleted=True) is not None
            )
        else:
            record_exists = (
                get_source_asset(
                    child.entity_id,
                    include_deleted=True,
                )
                is not None
            )
        if not record_exists:
            raise TrashOperationError(
                "Course purge is blocked because an unfinished child purge "
                "still owns managed artifacts. Retry that child deletion first."
            )


def _purge_course_conversations(
    course_id: str,
    _artifact_root: Path,
) -> None:
    from . import chat_store
    from .chat_service import purge_chat_conversation

    for conversation in chat_store.list_conversations_for_course(
        course_id,
        include_deleted=True,
    ):
        purge_chat_conversation(
            conversation.id,
            allow_parent_deleted=True,
        )


def _purge_course_documents(
    course_id: str,
    _artifact_root: Path,
) -> None:
    from .learning_document_service import purge_saved_learning_document
    from .learning_document_store import list_learning_documents_for_course

    for document in list_learning_documents_for_course(
        course_id,
        include_deleted=True,
    ):
        purge_saved_learning_document(
            document.id,
            allow_parent_deleted=True,
        )


def _purge_course_assets(course_id: str, _artifact_root: Path) -> None:
    from . import course_source_service
    from .source_asset_service import purge_source_asset_records
    from .source_asset_store import list_source_assets_for_course

    for asset in list_source_assets_for_course(
        course_id,
        include_deleted=True,
    ):
        course_source_service.remove_asset_source(asset.id)
        purge_source_asset_records(
            asset.id,
            allow_parent_deleted=True,
        )


def _purge_course_jobs(course_id: str, _artifact_root: Path) -> None:
    from . import course_source_service
    from .job_service import purge_video_job_records
    from .job_store import list_jobs_for_course

    for job in list_jobs_for_course(course_id, include_deleted=True):
        course_source_service.remove_video_source(job.id)
        purge_video_job_records(
            job.id,
            allow_parent_deleted=True,
        )


def _purge_course_topics(course_id: str, _artifact_root: Path) -> None:
    from .topic_store import delete_topics_for_course

    delete_topics_for_course(course_id)


def _purge_course_record(course_id: str, _artifact_root: Path) -> None:
    from .course_service import (
        CourseNotFoundError,
        purge_deleted_video_course,
    )

    try:
        purge_deleted_video_course(
            course_id,
            preserve_course_trash_item=True,
        )
    except CourseNotFoundError:
        # A crash can happen after deleting the course row but before recording
        # this phase. Since purge is irreversible, a retry safely continues.
        return


def _purge_planned_artifacts(
    plan: dict[str, object],
    *,
    workspace_root: Path,
) -> None:
    validated = _validate_course_purge_plan(
        plan,
        workspace_root=workspace_root,
    )
    course_id = str(validated["course_id"])
    artifacts = validated["artifacts"]
    assert isinstance(artifacts, list)
    resolved_artifacts: list[Path] = []
    for artifact in artifacts:
        assert isinstance(artifact, dict)
        resolved_path, _, _ = _validate_planned_artifact(
            artifact,
            workspace_root=workspace_root,
            course_id=course_id,
        )
        resolved_artifacts.append(resolved_path)

    _assert_no_foreign_record_references(
        resolved_artifacts,
        artifact_root=workspace_root,
        ignored_course_id=course_id,
    )
    for resolved_path in resolved_artifacts:
        try:
            if resolved_path.is_symlink() or resolved_path.is_file():
                resolved_path.unlink(missing_ok=True)
            elif resolved_path.exists():
                raise TrashOperationError(
                    "A managed course artifact is no longer a regular file."
                )
        except OSError as exc:
            raise TrashOperationError(
                "A managed course artifact could not be permanently deleted."
            ) from exc


def _assert_no_foreign_record_references(
    artifacts: list[Path],
    *,
    artifact_root: Path,
    ignored_course_id: str | None = None,
) -> None:
    """Refuse deletion while another durable record still owns an artifact."""

    planned = {_path_identity(path) for path in artifacts}
    if not planned:
        return

    from .db import connect

    try:
        with connect() as conn:
            job_rows = conn.execute(
                """
                SELECT id, course_id, video_path, transcript_path
                FROM jobs
                """
            ).fetchall()
            source_rows = conn.execute(
                """
                SELECT id, course_id, stored_path
                FROM source_assets
                """
            ).fetchall()
    except Exception as exc:
        raise TrashOperationError(
            "Managed artifact ownership could not be verified."
        ) from exc

    references: list[tuple[str, object, object, object]] = []
    for row in job_rows:
        job_id = row["id"]
        course_id = row["course_id"]
        video_path = row["video_path"]
        references.append(("video job", job_id, course_id, video_path))
        transcript_path = row["transcript_path"]
        if transcript_path is not None:
            references.append(
                (
                    "video job",
                    job_id,
                    course_id,
                    transcript_path,
                )
            )
        if isinstance(video_path, str) and video_path:
            references.append(
                (
                    "video job",
                    job_id,
                    course_id,
                    artifact_root
                    / "audio"
                    / f"{Path(video_path).stem}.wav",
                )
            )

    for row in source_rows:
        references.append(
            (
                "source",
                row["id"],
                row["course_id"],
                row["stored_path"],
            )
        )

    conflicts: list[str] = []
    for entity_type, entity_id, course_id, raw_path in references:
        if (
            ignored_course_id is not None
            and isinstance(course_id, str)
            and course_id == ignored_course_id
        ):
            continue
        path = _absolute_record_path_or_none(raw_path)
        if path is not None and _path_identity(path) in planned:
            conflicts.append(f"{entity_type} {entity_id!r}")
    if conflicts:
        owners = ", ".join(sorted(set(conflicts)))
        raise TrashOperationError(
            "Managed artifact is still referenced by another record: "
            f"{owners}."
        )

    _validate_managed_record_ownership(
        job_rows,
        source_rows,
        artifact_root=artifact_root,
    )


def _absolute_record_path_or_none(value: object) -> Path | None:
    if not isinstance(value, (str, Path)):
        return None
    try:
        candidate = Path(value)
    except (TypeError, ValueError):
        return None
    return candidate if candidate.is_absolute() else None


def _validate_managed_record_ownership(
    job_rows: list[object],
    source_rows: list[object],
    *,
    artifact_root: Path,
) -> None:
    """Validate every durable owner before allowing any managed unlink."""

    owners: dict[str, str] = {}
    upload_root = artifact_root / "uploads"
    transcript_root = artifact_root / "transcripts"
    audio_root = artifact_root / "audio"
    source_root = get_app_path_settings().source_dir

    for row in job_rows:
        job_id = _record_identifier(row["id"], label="Video job id")
        course_id = _record_identifier(
            row["course_id"],
            label="Video job course id",
        )
        video_path, video_relative = _validated_record_path(
            row["video_path"],
            root=upload_root,
            label="Video job path",
        )
        if (
            len(video_relative.parts) != 1
            or video_relative.stem != job_id
            or video_relative.suffix.lower() not in ENTITY_VIDEO_EXTENSIONS
        ):
            _raise_ownership_validation_error(
                "A video job path is outside its canonical namespace."
            )
        owner = f"video job {job_id!r} in course {course_id!r}"
        _register_record_path_owner(owners, video_path, owner=owner)

        audio_path, audio_relative = _validated_record_path(
            audio_root / f"{job_id}.wav",
            root=audio_root,
            label="Video job audio path",
        )
        if audio_relative.parts != (f"{job_id}.wav",):
            _raise_ownership_validation_error(
                "A video job audio path is outside its canonical namespace."
            )
        _register_record_path_owner(owners, audio_path, owner=owner)

        transcript_value = row["transcript_path"]
        if transcript_value is not None:
            transcript_path, transcript_relative = _validated_record_path(
                transcript_value,
                root=transcript_root,
                label="Video job transcript path",
            )
            if transcript_relative.parts != (f"{job_id}.json",):
                _raise_ownership_validation_error(
                    "A video job transcript path is outside its canonical "
                    "namespace."
                )
            _register_record_path_owner(
                owners,
                transcript_path,
                owner=owner,
            )

    for row in source_rows:
        asset_id = _record_identifier(
            row["id"],
            label="Source asset id",
        )
        course_id = _record_identifier(
            row["course_id"],
            label="Source asset course id",
        )
        source_path, source_relative = _validated_record_path(
            row["stored_path"],
            root=source_root,
            label="Source asset path",
        )
        if (
            len(source_relative.parts) != 2
            or source_relative.parts[0] != course_id
            or source_relative.stem != asset_id
            or source_relative.suffix.lower() not in ENTITY_SOURCE_EXTENSIONS
        ):
            _raise_ownership_validation_error(
                "A source asset path is outside its canonical namespace."
            )
        _register_record_path_owner(
            owners,
            source_path,
            owner=f"source asset {asset_id!r} in course {course_id!r}",
        )


def _record_identifier(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or value.endswith((" ", "."))
        or "\x00" in value
        or any(character in value for character in ("/", "\\", ":"))
        or any(ord(character) < 32 for character in value)
    ):
        _raise_ownership_validation_error(f"{label} is invalid.")
    return value


def _validated_record_path(
    value: object,
    *,
    root: Path,
    label: str,
) -> tuple[Path, Path]:
    if not isinstance(value, (str, Path)):
        _raise_ownership_validation_error(f"{label} is invalid.")
    try:
        candidate = Path(value)
    except (TypeError, ValueError) as exc:
        _raise_ownership_validation_error(f"{label} is invalid.", cause=exc)
    if not candidate.is_absolute():
        _raise_ownership_validation_error(f"{label} is not absolute.")
    try:
        configured_root = root.absolute()
        relative = candidate.relative_to(configured_root)
        resolved_root = configured_root.resolve()
        resolved_path = candidate.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        _raise_ownership_validation_error(
            f"{label} is outside its managed root.",
            cause=exc,
        )
    if (
        not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or resolved_path == resolved_root
        or resolved_root not in resolved_path.parents
    ):
        _raise_ownership_validation_error(
            f"{label} is outside its managed root."
        )
    return resolved_path, Path(*relative.parts)


def _register_record_path_owner(
    owners: dict[str, str],
    path: Path,
    *,
    owner: str,
) -> None:
    identity = _path_identity(path)
    previous = owners.get(identity)
    if previous is not None:
        _raise_ownership_validation_error(
            "A normalized managed path is referenced by different records: "
            f"{previous} and {owner}."
        )
    owners[identity] = owner


def _raise_ownership_validation_error(
    detail: str,
    *,
    cause: BaseException | None = None,
) -> NoReturn:
    error = TrashOperationError(
        "Managed artifact ownership could not be verified. " + detail
    )
    if cause is None:
        raise error
    raise error from cause


def _path_identity(path: Path) -> str:
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise TrashOperationError(
            "A managed artifact path could not be resolved."
        ) from exc
    return os.path.normcase(os.path.normpath(str(resolved)))
