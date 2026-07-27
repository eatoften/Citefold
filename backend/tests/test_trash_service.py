import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest

import app.source_asset_service as source_asset_service
import app.trash_service as trash_service
from app.chat import ChatConversation
from app.chat_store import (
    create_conversation,
    get_conversation,
)
from app.chat_service import delete_chat_conversation
from app.course import CourseCreate
from app.course_service import (
    create_video_course,
    delete_video_course,
    get_video_course,
)
from app.db import connect
from app.course_source_store import get_source
from app.course_source_service import (
    CourseSourceNotFoundError,
    get_course_source,
    sync_source_asset,
    sync_video_source,
)
from app.job import VideoJob, VideoJobStatus, utc_now
from app.job_service import delete_video_job
from app.job_store import create_job, get_job
from app.knowledge_card import KnowledgeCard
from app.knowledge_card_service import delete_saved_card
from app.knowledge_card_store import create_card, get_card
from app.learning_document import (
    LearningDocument,
    LearningDocumentCardLink,
    LearningDocumentVersion,
)
from app.learning_document_service import delete_saved_learning_document
from app.learning_document_store import (
    create_document_version,
    create_learning_document,
    get_learning_document,
    list_document_card_links,
    list_document_versions,
    upsert_document_card_link,
)
from app.source_asset import SourceAsset, SourceUnit
from app.source_asset_store import (
    create_source_asset,
    get_source_asset,
    list_source_units_for_asset,
    replace_source_units,
)
from app.trash_service import (
    TrashOperationError,
    list_workspace_trash,
    purge_workspace_trash_item,
    restore_workspace_trash_item,
)
from app.trash_store import (
    compare_and_set_trash_item_status,
    get_trash_item,
    recover_interrupted_trash_operations,
)


def _job(tmp_path: Path, *, job_id: str = "trash-job") -> VideoJob:
    video_path = tmp_path / "uploads" / f"{job_id}.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video")
    job = VideoJob(
        id=job_id,
        video_path=video_path,
        status=VideoJobStatus.completed,
    )
    create_job(job)
    return job


def _card(job_id: str, *, card_id: str = "trash-card") -> KnowledgeCard:
    card = KnowledgeCard(
        id=card_id,
        job_id=job_id,
        title="Recoverable card",
        summary="The card and its relationships survive a soft delete.",
        claims=[
            {
                "text": "The relationship is durable.",
                "evidence": [
                    {
                        "quote": "The relationship is durable.",
                        "segment_start_seconds": 0,
                        "segment_end_seconds": 1,
                    }
                ],
            }
        ],
        source_start_seconds=0,
        source_end_seconds=1,
    )
    create_card(card)
    return card


def test_trash_operation_claim_is_compare_and_set_safe() -> None:
    course = create_video_course(CourseCreate(title="Concurrent trash claim"))
    delete_video_course(course.id)
    item = next(
        item
        for item in list_workspace_trash()
        if item.entity_id == course.id
    )
    barrier = Barrier(2)

    def claim(status):
        barrier.wait(timeout=5)
        return compare_and_set_trash_item_status(
            item.id,
            expected_statuses=("trashed",),
            status=status,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = [
            future.result(timeout=5)
            for future in (
                executor.submit(claim, "restoring"),
                executor.submit(claim, "purging"),
            )
        ]

    assert sum(outcome is not None for outcome in outcomes) == 1
    persisted = get_trash_item(item.id)
    assert persisted is not None
    assert persisted.status in {"restoring", "purging"}


def test_interrupted_restore_recovers_idempotently_and_can_retry() -> None:
    course = create_video_course(CourseCreate(title="Interrupted restore"))
    delete_video_course(course.id)
    item = next(
        item
        for item in list_workspace_trash()
        if item.entity_id == course.id
    )
    claimed = compare_and_set_trash_item_status(
        item.id,
        expected_statuses=("trashed",),
        status="restoring",
    )
    assert claimed is not None
    assert "course_purge" not in claimed.metadata

    recovered = recover_interrupted_trash_operations()

    assert [entry.id for entry in recovered] == [item.id]
    assert recovered[0].status == "restore_failed"
    assert recovered[0].metadata == claimed.metadata
    assert recover_interrupted_trash_operations() == []

    restore_workspace_trash_item(item.id)

    assert get_video_course(course.id).id == course.id
    assert get_trash_item(item.id) is None


def test_interrupted_purge_preserves_phase_plan_and_can_retry(
    tmp_path: Path,
) -> None:
    course = create_video_course(CourseCreate(title="Interrupted purge"))
    delete_video_course(course.id)
    item = next(
        item
        for item in list_workspace_trash()
        if item.entity_id == course.id
    )
    claimed = compare_and_set_trash_item_status(
        item.id,
        expected_statuses=("trashed",),
        status="purging",
    )
    assert claimed is not None
    planned = trash_service._prepare_course_purge(
        claimed,
        artifact_root=tmp_path,
    )
    expected_metadata = planned.metadata

    recovered = recover_interrupted_trash_operations()

    assert [entry.id for entry in recovered] == [item.id]
    assert recovered[0].status == "purge_failed"
    assert recovered[0].metadata == expected_metadata
    assert recovered[0].metadata["course_purge"]["phase"] == "planned"
    assert recover_interrupted_trash_operations() == []

    purge_workspace_trash_item(item.id, artifact_root=tmp_path)

    assert get_trash_item(item.id) is None


def test_workspace_startup_recovers_interrupted_trash_claim() -> None:
    import app.main as main

    course = create_video_course(CourseCreate(title="Startup trash recovery"))
    delete_video_course(course.id)
    item = next(
        item
        for item in list_workspace_trash()
        if item.entity_id == course.id
    )
    claimed = compare_and_set_trash_item_status(
        item.id,
        expected_statuses=("trashed",),
        status="restoring",
    )
    assert claimed is not None

    main._initialize_workspace_before_task_dispatch(main.app)

    report = main.app.state.trash_recovery_report
    assert [entry.id for entry in report] == [item.id]
    persisted = get_trash_item(item.id)
    assert persisted is not None
    assert persisted.status == "restore_failed"


def test_failed_course_purge_is_irreversible_and_retryable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    course = create_video_course(CourseCreate(title="Retry purge"))
    delete_video_course(course.id)
    item = next(
        item
        for item in list_workspace_trash()
        if item.entity_id == course.id
    )
    original = trash_service._purge_course_documents

    def fail_documents(_course_id: str, _artifact_root: Path) -> None:
        raise RuntimeError("injected purge failure")

    monkeypatch.setattr(
        trash_service,
        "_purge_course_documents",
        fail_documents,
    )
    with pytest.raises(TrashOperationError):
        purge_workspace_trash_item(item.id, artifact_root=tmp_path)

    failed = get_trash_item(item.id)
    assert failed is not None
    assert failed.status == "purge_failed"
    with pytest.raises(TrashOperationError, match="no longer be restored"):
        restore_workspace_trash_item(item.id)

    monkeypatch.setattr(
        trash_service,
        "_purge_course_documents",
        original,
    )
    purge_workspace_trash_item(item.id, artifact_root=tmp_path)

    assert get_trash_item(item.id) is None


def test_course_purge_rejects_artifacts_owned_by_another_course(
    tmp_path: Path,
) -> None:
    from app.job_store import move_jobs_to_course
    from app.trash_store import update_claimed_trash_item_metadata

    data_root = tmp_path / "data"
    course = create_video_course(CourseCreate(title="Planned purge"))
    victim_course = create_video_course(CourseCreate(title="Keep files"))
    victim_job = _job(data_root, job_id="other-course-job")
    move_jobs_to_course(victim_job.course_id, victim_course.id)
    victim_audio = (
        data_root / "audio" / f"{victim_job.video_path.stem}.wav"
    )
    victim_audio.parent.mkdir(parents=True)
    victim_audio.write_bytes(b"victim-audio")
    delete_video_course(course.id)
    item = next(
        item
        for item in list_workspace_trash()
        if item.entity_id == course.id
    )
    claimed = compare_and_set_trash_item_status(
        item.id,
        expected_statuses=("trashed",),
        status="purging",
    )
    assert claimed is not None
    metadata = dict(claimed.metadata)
    metadata["course_purge"] = {
        "version": trash_service.COURSE_PURGE_PLAN_VERSION,
        "course_id": course.id,
        "phase": "planned",
        "artifacts": [
            {
                "path": str(victim_job.video_path.resolve()),
                "root": str((data_root / "uploads").resolve()),
            },
            {
                "path": str(victim_audio.resolve()),
                "root": str((data_root / "audio").resolve()),
            },
        ],
    }
    updated = update_claimed_trash_item_metadata(
        item.id,
        expected_status="purging",
        metadata=metadata,
    )
    assert updated is not None
    assert (
        compare_and_set_trash_item_status(
            item.id,
            expected_statuses=("purging",),
            status="purge_failed",
        )
        is not None
    )

    with pytest.raises(TrashOperationError, match="does not match"):
        purge_workspace_trash_item(item.id, artifact_root=data_root)

    failed = get_trash_item(item.id)
    assert failed is not None
    assert failed.status == "purge_failed"
    assert get_job(victim_job.id) is not None
    assert victim_job.video_path.read_bytes() == b"video"
    assert victim_audio.read_bytes() == b"victim-audio"


def test_course_purge_rejects_path_shared_by_another_course_record(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    course = create_video_course(CourseCreate(title="Delete shared path"))
    victim_course = create_video_course(CourseCreate(title="Keep shared path"))
    shared_path = data_root / "uploads" / "victim-job.mp4"
    shared_path.parent.mkdir(parents=True)
    shared_path.write_bytes(b"shared-video")
    create_job(
        VideoJob(
            id="purged-job",
            course_id=course.id,
            video_path=shared_path,
            status=VideoJobStatus.completed,
        )
    )
    create_job(
        VideoJob(
            id="victim-job",
            course_id=victim_course.id,
            video_path=shared_path,
            status=VideoJobStatus.completed,
        )
    )
    delete_video_course(course.id)
    item = next(
        item
        for item in list_workspace_trash()
        if item.entity_id == course.id
    )

    with pytest.raises(TrashOperationError, match="still referenced"):
        purge_workspace_trash_item(item.id, artifact_root=data_root)

    failed = get_trash_item(item.id)
    assert failed is not None
    assert failed.status == "purge_failed"
    assert get_job("victim-job") is not None
    assert shared_path.read_bytes() == b"shared-video"


def test_course_retry_never_unlinks_after_artifact_phase_completed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    course = create_video_course(CourseCreate(title="Crash after artifacts"))
    survivor_course = create_video_course(
        CourseCreate(title="Owns recreated file")
    )
    shared_path = data_root / "uploads" / "course-retry-job.mp4"
    shared_path.parent.mkdir(parents=True)
    shared_path.write_bytes(b"original-owner")
    create_job(
        VideoJob(
            id="course-retry-job",
            course_id=course.id,
            video_path=shared_path,
            status=VideoJobStatus.completed,
        )
    )
    delete_video_course(course.id)
    item = next(
        item
        for item in list_workspace_trash()
        if item.entity_type == "course"
        and item.entity_id == course.id
    )
    original_purge_assets = trash_service._purge_course_assets

    def crash_after_artifacts(_course_id: str, _root: Path) -> None:
        raise RuntimeError("simulated crash after artifact publication")

    monkeypatch.setattr(
        trash_service,
        "_purge_course_assets",
        crash_after_artifacts,
    )
    with pytest.raises(TrashOperationError):
        purge_workspace_trash_item(item.id, artifact_root=data_root)

    failed = get_trash_item(item.id)
    assert failed is not None
    assert failed.metadata["course_purge"]["phase"] == "artifacts"
    assert not shared_path.exists()

    create_job(
        VideoJob(
            id="survivor-job",
            course_id=survivor_course.id,
            video_path=shared_path,
            status=VideoJobStatus.completed,
        )
    )
    shared_path.write_bytes(b"recreated-for-new-owner")
    monkeypatch.setattr(
        trash_service,
        "_purge_course_assets",
        original_purge_assets,
    )

    purge_workspace_trash_item(item.id, artifact_root=data_root)

    assert get_job("survivor-job") is not None
    assert shared_path.read_bytes() == b"recreated-for-new-owner"


def test_job_soft_delete_restore_and_purge_preserve_then_remove_files(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    job = _job(data_root)
    card = _card(job.id)
    sync_video_source(job.id)

    delete_video_job(job.id, data_root)

    assert get_job(job.id) is None
    assert get_job(job.id, include_deleted=True) is not None
    assert get_card(card.id) is None
    assert get_card(card.id, include_deleted=True) is not None
    assert job.video_path.is_file()
    assert get_source(f"job:{job.id}") is not None
    with pytest.raises(CourseSourceNotFoundError):
        get_course_source(f"job:{job.id}")
    item = next(
        item
        for item in list_workspace_trash()
        if item.entity_type == "video_job"
    )

    restore_workspace_trash_item(item.id)

    assert get_job(job.id) is not None
    assert get_card(card.id) is not None
    assert job.video_path.is_file()
    assert get_source(f"job:{job.id}") is not None

    delete_video_job(job.id, data_root)
    item = next(
        item
        for item in list_workspace_trash()
        if item.entity_type == "video_job"
    )
    purge_workspace_trash_item(item.id, artifact_root=data_root)

    assert get_job(job.id, include_deleted=True) is None
    assert get_card(card.id, include_deleted=True) is None
    assert not job.video_path.exists()


def test_failed_job_artifact_delete_keeps_retryable_purge_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    job = _job(data_root, job_id="locked-job")
    delete_video_job(job.id, data_root)
    item = next(
        item
        for item in list_workspace_trash()
        if item.entity_type == "video_job"
    )
    locked_path = job.video_path.resolve()
    original_unlink = Path.unlink

    def fail_locked_path(
        path: Path,
        *args,
        **kwargs,
    ) -> None:
        if path.resolve() == locked_path:
            raise PermissionError("simulated Windows file lock")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_locked_path)

    with pytest.raises(TrashOperationError):
        purge_workspace_trash_item(item.id, artifact_root=data_root)

    failed = get_trash_item(item.id)
    assert failed is not None
    assert failed.status == "purge_failed"
    assert failed.metadata["entity_purge"]["phase"] == "database"
    assert get_job(job.id, include_deleted=True) is None
    assert job.video_path.is_file()

    monkeypatch.setattr(Path, "unlink", original_unlink)
    purge_workspace_trash_item(item.id, artifact_root=data_root)

    assert get_trash_item(item.id) is None
    assert not job.video_path.exists()


def test_parent_course_preserves_unfinished_child_artifact_journal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    course = create_video_course(CourseCreate(title="Parent waits for child"))
    job = _job(data_root, job_id="unfinished-child-job")
    from app.job_store import move_jobs_to_course

    move_jobs_to_course(job.course_id, course.id)
    job.course_id = course.id
    delete_video_job(job.id, data_root)
    child = next(
        item
        for item in list_workspace_trash()
        if item.entity_type == "video_job"
        and item.entity_id == job.id
    )
    locked_path = job.video_path.resolve()
    original_unlink = Path.unlink

    def fail_locked_path(
        path: Path,
        *args,
        **kwargs,
    ) -> None:
        if path.resolve() == locked_path:
            raise PermissionError("simulated child artifact lock")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_locked_path)
    with pytest.raises(TrashOperationError):
        purge_workspace_trash_item(child.id, artifact_root=data_root)

    failed_child = get_trash_item(child.id)
    assert failed_child is not None
    assert failed_child.status == "purge_failed"
    assert failed_child.metadata["entity_purge"]["phase"] == "database"
    assert get_job(job.id, include_deleted=True) is None
    assert job.video_path.is_file()

    delete_video_course(course.id)
    parent = next(
        item
        for item in list_workspace_trash()
        if item.entity_type == "course"
        and item.entity_id == course.id
    )
    with pytest.raises(
        TrashOperationError,
        match="unfinished child purge",
    ):
        purge_workspace_trash_item(parent.id, artifact_root=data_root)

    failed_parent = get_trash_item(parent.id)
    assert failed_parent is not None
    assert failed_parent.status == "purge_failed"
    assert get_trash_item(child.id) is not None
    assert job.video_path.is_file()

    monkeypatch.setattr(Path, "unlink", original_unlink)
    purge_workspace_trash_item(child.id, artifact_root=data_root)
    assert get_trash_item(child.id) is None
    assert not job.video_path.exists()

    purge_workspace_trash_item(parent.id, artifact_root=data_root)
    assert get_trash_item(parent.id) is None


@pytest.mark.parametrize(
    "artifacts",
    [
        [],
        [{"root": "workspace", "relative_path": "data/jobs.db"}],
        [
            {
                "root": "uploads",
                "relative_path": "victim-job.mp4",
            },
            {
                "root": "audio",
                "relative_path": "victim-job.wav",
            },
        ],
    ],
    ids=[
        "empty-plan",
        "legacy-workspace-root",
        "different-entity-namespace",
    ],
)
def test_job_purge_rejects_forged_or_empty_artifact_plan(
    tmp_path: Path,
    artifacts: list[dict[str, str]],
) -> None:
    from app.trash_store import update_claimed_trash_item_metadata

    data_root = tmp_path / "data"
    sentinel = data_root / "data" / "jobs.db"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_bytes(b"must-not-delete")
    victim_upload = data_root / "uploads" / "victim-job.mp4"
    victim_audio = data_root / "audio" / "victim-job.wav"
    victim_upload.parent.mkdir(parents=True)
    victim_audio.parent.mkdir(parents=True)
    victim_upload.write_bytes(b"victim-video")
    victim_audio.write_bytes(b"victim-audio")
    job = _job(data_root, job_id="forged-plan-job")
    delete_video_job(job.id, data_root)
    item = next(
        item
        for item in list_workspace_trash()
        if item.entity_type == "video_job"
    )
    claimed = compare_and_set_trash_item_status(
        item.id,
        expected_statuses=("trashed",),
        status="purging",
    )
    assert claimed is not None
    metadata = dict(claimed.metadata)
    metadata["entity_purge"] = {
        "version": trash_service.ENTITY_PURGE_PLAN_VERSION,
        "entity_type": "video_job",
        "phase": "planned",
        "artifacts": artifacts,
    }
    updated = update_claimed_trash_item_metadata(
        item.id,
        expected_status="purging",
        metadata=metadata,
    )
    assert updated is not None
    assert (
        compare_and_set_trash_item_status(
            item.id,
            expected_statuses=("purging",),
            status="purge_failed",
        )
        is not None
    )

    with pytest.raises(TrashOperationError, match="purge recovery plan"):
        purge_workspace_trash_item(item.id, artifact_root=data_root)

    failed = get_trash_item(item.id)
    assert failed is not None
    assert failed.status == "purge_failed"
    assert get_job(job.id, include_deleted=True) is not None
    assert sentinel.read_bytes() == b"must-not-delete"
    assert victim_upload.read_bytes() == b"victim-video"
    assert victim_audio.read_bytes() == b"victim-audio"


def test_job_purge_rejects_path_shared_by_another_record(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    job = _job(data_root, job_id="shared-job")
    create_job(
        VideoJob(
            id="other-job",
            video_path=job.video_path,
            status=VideoJobStatus.completed,
        )
    )
    delete_video_job(job.id, data_root)
    item = next(
        item
        for item in list_workspace_trash()
        if item.entity_type == "video_job"
        and item.entity_id == job.id
    )

    with pytest.raises(TrashOperationError, match="still referenced"):
        purge_workspace_trash_item(item.id, artifact_root=data_root)

    failed = get_trash_item(item.id)
    assert failed is not None
    assert failed.status == "purge_failed"
    assert failed.metadata["entity_purge"]["phase"] == "database"
    assert get_job(job.id, include_deleted=True) is None
    assert get_job("other-job") is not None
    assert job.video_path.read_bytes() == b"video"


@pytest.mark.skipif(
    os.name != "nt",
    reason="Windows path identity is case-insensitive.",
)
def test_entity_purge_uses_windows_case_insensitive_path_identity(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    job = _job(data_root, job_id="case-owner")
    create_job(
        VideoJob(
            id="CASE-OWNER",
            video_path=data_root / "uploads" / "CASE-OWNER.MP4",
            status=VideoJobStatus.completed,
        )
    )
    delete_video_job(job.id, data_root)
    item = next(
        item
        for item in list_workspace_trash()
        if item.entity_type == "video_job"
        and item.entity_id == job.id
    )

    with pytest.raises(TrashOperationError, match="still referenced"):
        purge_workspace_trash_item(item.id, artifact_root=data_root)

    assert job.video_path.read_bytes() == b"video"


@pytest.mark.parametrize(
    "malformation",
    [
        "job-id-blob",
        "job-id-empty",
        "course-id-blob",
        "course-id-empty",
        "path-blob",
        "path-empty",
        "path-relative",
        "path-outside-managed-root",
        "path-noncanonical-name",
        "transcript-path-blob",
    ],
)
def test_entity_purge_fails_closed_on_malformed_durable_owner(
    tmp_path: Path,
    malformation: str,
) -> None:
    data_root = tmp_path / "data"
    job = _job(data_root, job_id="malformed-owner-target")
    foreign_path = data_root / "uploads" / "foreign-owner.mp4"
    foreign_path.write_bytes(b"foreign")
    create_job(
        VideoJob(
            id="foreign-owner",
            video_path=foreign_path,
            status=VideoJobStatus.completed,
        )
    )
    mutations: dict[str, tuple[str, object]] = {
        "job-id-blob": ("id", sqlite3.Binary(b"foreign-owner")),
        "job-id-empty": ("id", ""),
        "course-id-blob": (
            "course_id",
            sqlite3.Binary(b"uncategorized"),
        ),
        "course-id-empty": ("course_id", ""),
        "path-blob": (
            "video_path",
            sqlite3.Binary(str(job.video_path).encode("utf-8")),
        ),
        "path-empty": ("video_path", ""),
        "path-relative": (
            "video_path",
            "uploads/foreign-owner.mp4",
        ),
        "path-outside-managed-root": (
            "video_path",
            str((tmp_path / "outside" / "foreign-owner.mp4").resolve()),
        ),
        "path-noncanonical-name": (
            "video_path",
            str((data_root / "uploads" / "another-name.mp4").resolve()),
        ),
        "transcript-path-blob": (
            "transcript_path",
            sqlite3.Binary(
                str(
                    data_root
                    / "transcripts"
                    / "foreign-owner.json"
                ).encode("utf-8")
            ),
        ),
    }
    column, value = mutations[malformation]
    with connect() as conn:
        conn.execute(
            f'UPDATE jobs SET "{column}" = ? WHERE id = ?',
            (value, "foreign-owner"),
        )

    delete_video_job(job.id, data_root)
    item = next(
        item
        for item in list_workspace_trash()
        if item.entity_type == "video_job"
        and item.entity_id == job.id
    )

    with pytest.raises(
        TrashOperationError,
        match="ownership could not be verified",
    ):
        purge_workspace_trash_item(item.id, artifact_root=data_root)

    failed = get_trash_item(item.id)
    assert failed is not None
    assert failed.status == "purge_failed"
    assert failed.metadata["entity_purge"]["phase"] == "database"
    with connect() as conn:
        remaining_jobs = int(
            conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        )
    assert remaining_jobs == 1
    assert job.video_path.read_bytes() == b"video"


def test_entity_purge_fails_closed_on_malformed_source_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    source_root = data_root / "sources"
    source_path = source_root / "uncategorized" / "foreign-source.pdf"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"%PDF")
    now = utc_now()
    create_source_asset(
        SourceAsset(
            id="foreign-source",
            course_id="uncategorized",
            asset_type="pdf",
            original_filename="foreign-source.pdf",
            stored_path=str(source_path),
            size_bytes=4,
            sha256="foreign",
            extraction_status="ready",
            created_at=now,
            updated_at=now,
        )
    )
    with connect() as conn:
        conn.execute(
            "UPDATE source_assets SET stored_path = ? WHERE id = ?",
            (
                sqlite3.Binary(str(source_path).encode("utf-8")),
                "foreign-source",
            ),
        )
    monkeypatch.setattr(
        trash_service,
        "get_app_path_settings",
        lambda: SimpleNamespace(
            data_dir=data_root,
            source_dir=source_root,
        ),
    )
    job = _job(data_root, job_id="source-owner-target")
    delete_video_job(job.id, data_root)
    item = next(
        item
        for item in list_workspace_trash()
        if item.entity_type == "video_job"
        and item.entity_id == job.id
    )

    with pytest.raises(
        TrashOperationError,
        match="ownership could not be verified",
    ):
        purge_workspace_trash_item(item.id, artifact_root=data_root)

    assert job.video_path.read_bytes() == b"video"
    with connect() as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM source_assets WHERE id = 'foreign-source'"
            ).fetchone()
            is not None
        )


def test_purge_plan_versions_must_be_integers(tmp_path: Path) -> None:
    item = SimpleNamespace(
        entity_type="video_job",
        entity_id="strict-version-job",
        course_id="uncategorized",
    )
    entity_plan = {
        "version": 2.0,
        "entity_type": "video_job",
        "phase": "planned",
        "artifacts": [
            {
                "root": "uploads",
                "relative_path": "strict-version-job.mp4",
            },
            {
                "root": "audio",
                "relative_path": "strict-version-job.wav",
            },
        ],
    }
    with pytest.raises(TrashOperationError, match="invalid"):
        trash_service._validate_entity_purge_plan(entity_plan, item=item)

    course_plan = {
        "version": 2.0,
        "course_id": "strict-version-course",
        "phase": "planned",
        "artifacts": [],
    }
    with pytest.raises(TrashOperationError, match="invalid"):
        trash_service._validate_course_purge_plan(
            course_plan,
            workspace_root=tmp_path,
            expected_course_id="strict-version-course",
        )


def test_job_purge_retries_after_later_artifact_is_locked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.job_store import update_job

    data_root = tmp_path / "data"
    job = _job(data_root, job_id="partial-artifact-job")
    transcript_path = (
        data_root / "transcripts" / f"{job.video_path.stem}.json"
    )
    transcript_path.parent.mkdir(parents=True)
    transcript_path.write_text('{"segments": []}', encoding="utf-8")
    job.transcript_path = transcript_path
    update_job(job)
    audio_path = data_root / "audio" / f"{job.video_path.stem}.wav"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"audio")
    delete_video_job(job.id, data_root)
    item = next(
        item
        for item in list_workspace_trash()
        if item.entity_type == "video_job"
    )
    locked_path = audio_path.resolve()
    original_unlink = Path.unlink

    def fail_locked_path(
        path: Path,
        *args,
        **kwargs,
    ) -> None:
        if path.resolve() == locked_path:
            raise PermissionError("simulated later artifact lock")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_locked_path)
    with pytest.raises(TrashOperationError):
        purge_workspace_trash_item(item.id, artifact_root=data_root)

    failed = get_trash_item(item.id)
    assert failed is not None
    assert failed.status == "purge_failed"
    assert failed.metadata["entity_purge"]["phase"] == "database"
    assert get_job(job.id, include_deleted=True) is None
    assert not job.video_path.exists()
    assert not transcript_path.exists()
    assert audio_path.is_file()

    monkeypatch.setattr(Path, "unlink", original_unlink)
    purge_workspace_trash_item(item.id, artifact_root=data_root)

    assert get_trash_item(item.id) is None
    assert not audio_path.exists()


def test_course_trash_owns_hidden_subtree_and_restores_it_in_place(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    course = create_video_course(CourseCreate(title="Trash course"))
    job = _job(data_root, job_id="course-trash-job")
    from app.job_store import move_jobs_to_course

    move_jobs_to_course(job.course_id, course.id)
    job.course_id = course.id
    sync_video_source(job.id)

    delete_video_course(course.id)

    assert get_job(job.id) is None
    persisted = get_job(job.id, include_deleted=True)
    assert persisted is not None
    assert persisted.course_id == course.id
    assert get_source(f"job:{job.id}") is not None
    with pytest.raises(CourseSourceNotFoundError):
        get_course_source(f"job:{job.id}")
    item = next(
        item
        for item in list_workspace_trash()
        if item.entity_type == "course"
    )

    restore_workspace_trash_item(item.id)

    assert get_video_course(course.id).id == course.id
    assert get_job(job.id) is not None
    assert get_source(f"job:{job.id}") is not None

    delete_video_course(course.id)
    item = next(
        item
        for item in list_workspace_trash()
        if item.entity_type == "course"
    )
    purge_workspace_trash_item(item.id, artifact_root=data_root)

    assert get_job(job.id, include_deleted=True) is None
    assert not job.video_path.exists()


def test_source_asset_soft_delete_restores_units_and_projection_then_purges_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "sources"
    stored_path = (
        source_root / "uncategorized" / "trash-asset.pdf"
    )
    stored_path.parent.mkdir(parents=True)
    stored_path.write_bytes(b"%PDF")
    now = utc_now()
    asset = SourceAsset(
        id="trash-asset",
        course_id="uncategorized",
        asset_type="pdf",
        original_filename="handout.pdf",
        stored_path=str(stored_path),
        size_bytes=4,
        sha256="abc",
        extraction_status="ready",
        created_at=now,
        updated_at=now,
    )
    unit = SourceUnit(
        id="trash-unit",
        asset_id=asset.id,
        unit_type="page",
        ordinal=0,
        text="A recoverable source page.",
        locator={"page_number": 1},
    )
    create_source_asset(asset)
    replace_source_units(asset.id, [unit])
    sync_source_asset(asset.id)
    monkeypatch.setattr(
        source_asset_service,
        "get_app_path_settings",
        lambda: SimpleNamespace(source_dir=source_root),
    )
    monkeypatch.setattr(
        trash_service,
        "get_app_path_settings",
        lambda: SimpleNamespace(
            data_dir=source_root.parent,
            source_dir=source_root,
        ),
    )

    source_asset_service.remove_source_asset(asset.id)

    assert get_source_asset(asset.id) is None
    assert get_source_asset(asset.id, include_deleted=True) is not None
    assert stored_path.is_file()
    assert get_source(f"asset:{asset.id}") is not None
    with pytest.raises(CourseSourceNotFoundError):
        get_course_source(f"asset:{asset.id}")
    item = next(
        item
        for item in list_workspace_trash()
        if item.entity_type == "source_asset"
    )

    restore_workspace_trash_item(item.id)

    assert [saved.id for saved in list_source_units_for_asset(asset.id)] == [
        unit.id
    ]
    assert get_source(f"asset:{asset.id}") is not None

    source_asset_service.remove_source_asset(asset.id)
    item = next(
        item
        for item in list_workspace_trash()
        if item.entity_type == "source_asset"
    )
    purge_workspace_trash_item(item.id)

    assert get_source_asset(asset.id, include_deleted=True) is None
    assert not stored_path.exists()


def test_source_purge_rejects_path_shared_by_another_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "data" / "sources"
    stored_path = (
        source_root / "uncategorized" / "shared-source.pdf"
    )
    stored_path.parent.mkdir(parents=True)
    stored_path.write_bytes(b"%PDF")
    now = utc_now()
    asset = SourceAsset(
        id="shared-source",
        course_id="uncategorized",
        asset_type="pdf",
        original_filename="shared-source.pdf",
        stored_path=str(stored_path),
        size_bytes=4,
        sha256="shared",
        extraction_status="ready",
        created_at=now,
        updated_at=now,
    )
    other = asset.model_copy(
        update={
            "id": "other-source",
            "original_filename": "other-source.pdf",
            "sha256": "other",
        }
    )
    create_source_asset(asset)
    create_source_asset(other)
    sync_source_asset(asset.id)
    sync_source_asset(other.id)
    settings = SimpleNamespace(
        data_dir=source_root.parent,
        source_dir=source_root,
    )
    monkeypatch.setattr(
        source_asset_service,
        "get_app_path_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        trash_service,
        "get_app_path_settings",
        lambda: settings,
    )
    source_asset_service.remove_source_asset(asset.id)
    item = next(
        item
        for item in list_workspace_trash()
        if item.entity_type == "source_asset"
        and item.entity_id == asset.id
    )

    with pytest.raises(TrashOperationError, match="still referenced"):
        purge_workspace_trash_item(item.id)

    failed = get_trash_item(item.id)
    assert failed is not None
    assert failed.status == "purge_failed"
    assert failed.metadata["entity_purge"]["phase"] == "database"
    assert get_source_asset(asset.id, include_deleted=True) is None
    assert get_source_asset(other.id) is not None
    assert stored_path.read_bytes() == b"%PDF"


def test_failed_source_artifact_delete_is_not_reported_as_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "data" / "sources"
    stored_path = (
        source_root
        / "uncategorized"
        / "locked-trash-asset.pdf"
    )
    stored_path.parent.mkdir(parents=True)
    stored_path.write_bytes(b"%PDF")
    now = utc_now()
    asset = SourceAsset(
        id="locked-trash-asset",
        course_id="uncategorized",
        asset_type="pdf",
        original_filename=stored_path.name,
        stored_path=str(stored_path),
        size_bytes=4,
        sha256="locked",
        extraction_status="ready",
        created_at=now,
        updated_at=now,
    )
    create_source_asset(asset)
    sync_source_asset(asset.id)
    settings = SimpleNamespace(
        data_dir=source_root.parent,
        source_dir=source_root,
    )
    monkeypatch.setattr(
        source_asset_service,
        "get_app_path_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        trash_service,
        "get_app_path_settings",
        lambda: settings,
    )
    source_asset_service.remove_source_asset(asset.id)
    item = next(
        item
        for item in list_workspace_trash()
        if item.entity_type == "source_asset"
    )
    locked_path = stored_path.resolve()
    original_unlink = Path.unlink

    def fail_locked_path(
        path: Path,
        *args,
        **kwargs,
    ) -> None:
        if path.resolve() == locked_path:
            raise PermissionError("simulated Windows file lock")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_locked_path)

    with pytest.raises(TrashOperationError):
        purge_workspace_trash_item(item.id)

    failed = get_trash_item(item.id)
    assert failed is not None
    assert failed.status == "purge_failed"
    assert failed.metadata["entity_purge"]["phase"] == "database"
    assert get_source_asset(asset.id, include_deleted=True) is None
    assert stored_path.is_file()

    monkeypatch.setattr(Path, "unlink", original_unlink)
    purge_workspace_trash_item(item.id)

    assert get_trash_item(item.id) is None
    assert not stored_path.exists()


def test_card_document_and_chat_restore_their_preserved_relationships(
    tmp_path: Path,
) -> None:
    job = _job(tmp_path, job_id="relationship-job")
    card = _card(job.id, card_id="relationship-card")
    now = utc_now()
    document = LearningDocument(
        id="relationship-document",
        course_id="uncategorized",
        title="Recoverable guide",
        summary="A guide linked to a card.",
        body_markdown="# Guide",
        status="draft",
        generation_mode="manual",
        created_at=now,
        updated_at=now,
    )
    create_learning_document(document)
    upsert_document_card_link(
        LearningDocumentCardLink(
            id="relationship-link",
            document_id=document.id,
            card_id=card.id,
            role="primary_anchor",
            position=0,
            created_at=now,
        )
    )
    create_document_version(
        LearningDocumentVersion(
            id="relationship-version",
            document_id=document.id,
            version_number=1,
            title=document.title,
            summary=document.summary,
            body_markdown=document.body_markdown,
            change_source="manual",
            created_at=now,
        )
    )
    conversation = ChatConversation(
        id="relationship-chat",
        course_id="uncategorized",
        title="Recoverable chat",
        created_at=now,
        updated_at=now,
    )
    create_conversation(conversation)

    delete_saved_card(card.id)
    card_item = next(
        item
        for item in list_workspace_trash()
        if item.entity_type == "knowledge_card"
    )
    assert list_document_card_links(document.id)[0].card_id == card.id
    restore_workspace_trash_item(card_item.id)
    assert get_card(card.id) is not None

    delete_saved_learning_document(document.id)
    document_item = next(
        item
        for item in list_workspace_trash()
        if item.entity_type == "learning_document"
    )
    assert get_learning_document(document.id) is None
    assert len(list_document_versions(document.id)) == 1
    assert len(list_document_card_links(document.id)) == 1
    restore_workspace_trash_item(document_item.id)
    assert get_learning_document(document.id) is not None

    delete_chat_conversation(conversation.id)
    chat_item = next(
        item
        for item in list_workspace_trash()
        if item.entity_type == "chat_conversation"
    )
    assert get_conversation(conversation.id) is None
    assert get_conversation(
        conversation.id,
        include_deleted=True,
    ) is not None
    restore_workspace_trash_item(chat_item.id)
    assert get_conversation(conversation.id) is not None
