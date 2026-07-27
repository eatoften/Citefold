from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.course import DEFAULT_COURSE_ID, CourseCreate
from app.course_service import create_video_course, delete_video_course
from app.db import connect
from app.reliable_task import (
    ReliableTask,
    ReliableTaskEventType,
    ReliableTaskStatus,
    TaskProgress,
)
from app.reliable_task_schema import create_reliable_task_tables
from app.reliable_task_store import (
    ReliableTaskActiveConflictError,
    ReliableTaskClaimLostError,
    ReliableTaskIdempotencyConflictError,
    ReliableTaskRetryError,
    claim_task,
    clear_reliable_tasks,
    create_task,
    fail_task,
    get_task,
    list_task_events,
    mark_task_canceled,
    recover_interrupted_tasks,
    request_task_cancel,
    retry_task,
    succeed_task,
    update_task_progress,
)


@pytest.fixture(autouse=True)
def reliable_task_tables():
    with connect() as conn:
        create_reliable_task_tables(conn)
    clear_reliable_tasks()
    yield
    clear_reliable_tasks()


def make_task(
    task_id: str,
    *,
    payload: dict[str, object] | None = None,
    idempotency_key: str | None = None,
    active_key: str | None = None,
    max_attempts: int = 3,
) -> ReliableTask:
    return ReliableTask(
        id=task_id,
        kind="source.index",
        course_id=DEFAULT_COURSE_ID,
        resource_type="course",
        resource_id=DEFAULT_COURSE_ID,
        payload=payload or {"source_ids": ["source-1"]},
        idempotency_key=idempotency_key,
        active_key=active_key,
        max_attempts=max_attempts,
    )


def test_create_is_idempotent_but_rejects_key_reuse():
    first = create_task(
        make_task(
            "task-1",
            idempotency_key="request-1",
            active_key="source.index:course-1",
        )
    )
    replay = create_task(
        make_task(
            "task-2",
            idempotency_key="request-1",
            active_key="source.index:course-1",
        )
    )

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.task.id == "task-1"

    with pytest.raises(ReliableTaskIdempotencyConflictError):
        create_task(
            make_task(
                "task-3",
                payload={"source_ids": ["different"]},
                idempotency_key="request-1",
                active_key="source.index:course-1",
            )
        )


def test_active_key_prevents_overlapping_work_until_terminal():
    create_task(make_task("task-1", active_key="source.index:course-1"))

    with pytest.raises(ReliableTaskActiveConflictError) as conflict:
        create_task(
            make_task("task-2", active_key="source.index:course-1")
        )

    assert conflict.value.active_task_id == "task-1"
    canceled = request_task_cancel("task-1")
    assert canceled.status == ReliableTaskStatus.canceled

    second = create_task(
        make_task("task-2", active_key="source.index:course-1")
    )
    assert second.task.id == "task-2"


def test_claim_is_atomic_when_workers_race():
    create_task(make_task("task-1"))
    barrier = Barrier(2)

    def attempt_claim(worker_id: str):
        barrier.wait()
        return claim_task("task-1", worker_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(
            executor.map(attempt_claim, ["worker-a", "worker-b"])
        )

    winners = [task for task in claims if task is not None]
    assert len(winners) == 1
    assert winners[0].status == ReliableTaskStatus.running
    assert winners[0].claim_token
    assert "claim_token" not in winners[0].model_dump(mode="json")
    assert "request_fingerprint" not in winners[0].model_dump(mode="json")

    events = list_task_events("task-1")
    assert [event.event_type for event in events] == [
        ReliableTaskEventType.created,
        ReliableTaskEventType.claimed,
    ]


def test_claim_token_guards_progress_and_terminal_transition():
    create_task(make_task("task-1"))
    claimed = claim_task("task-1", "worker-a")
    assert claimed is not None
    assert claimed.claim_token is not None

    with pytest.raises(ReliableTaskClaimLostError):
        update_task_progress(
            "task-1",
            "stale-token",
            TaskProgress(current=1, total=2),
        )

    progressed = update_task_progress(
        "task-1",
        claimed.claim_token,
        TaskProgress(
            current=1,
            total=2,
            stage="extract",
            message="Extracting source text",
        ),
    )
    assert progressed.progress.current == 1

    completed = succeed_task(
        "task-1",
        claimed.claim_token,
        {"indexed_units": 12},
    )
    assert completed.status == ReliableTaskStatus.succeeded
    assert completed.result == {"indexed_units": 12}
    assert completed.claim_token is None

    with pytest.raises(ReliableTaskClaimLostError):
        succeed_task("task-1", claimed.claim_token, {})

    events = list_task_events("task-1")
    assert [event.sequence for event in events] == list(
        range(1, len(events) + 1)
    )
    assert events[-1].event_type == ReliableTaskEventType.succeeded


def test_running_cancel_is_cooperative_and_retry_resets_attempt():
    create_task(make_task("task-1", max_attempts=2))
    claimed = claim_task("task-1", "worker-a")
    assert claimed is not None
    assert claimed.claim_token is not None

    canceling = request_task_cancel("task-1")
    assert canceling.status == ReliableTaskStatus.canceling
    assert canceling.claim_token == claimed.claim_token

    canceled = mark_task_canceled("task-1", claimed.claim_token)
    assert canceled.status == ReliableTaskStatus.canceled
    assert canceled.completed_at is not None

    retried = retry_task("task-1")
    assert retried.status == ReliableTaskStatus.queued
    assert retried.attempt == 2
    assert retried.cancel_requested_at is None
    assert retried.completed_at is None
    assert retried.progress == TaskProgress()

    second_claim = claim_task("task-1", "worker-b")
    assert second_claim is not None
    assert second_claim.claim_token is not None
    request_task_cancel("task-1")
    mark_task_canceled("task-1", second_claim.claim_token)

    with pytest.raises(ReliableTaskRetryError):
        retry_task("task-1")


def test_completed_handler_can_settle_canceling_task_as_succeeded():
    create_task(make_task("task-1"))
    claimed = claim_task("task-1", "worker-a")
    assert claimed is not None
    assert claimed.claim_token is not None
    request_task_cancel("task-1")

    completed = succeed_task(
        "task-1",
        claimed.claim_token,
        {"unexpected": True},
    )

    assert completed.status == ReliableTaskStatus.succeeded
    assert completed.result == {"unexpected": True}
    assert completed.cancel_requested_at is not None


def test_retry_rejects_a_deleted_resource():
    course = create_video_course(CourseCreate(title="Deleted retry target"))
    task = ReliableTask(
        id="task-deleted-course",
        kind="source.index",
        course_id=course.id,
        resource_type="course",
        resource_id=course.id,
        max_attempts=2,
    )
    create_task(task)
    claimed = claim_task(task.id, "worker-a")
    assert claimed is not None
    assert claimed.claim_token is not None
    fail_task(
        task.id,
        claimed.claim_token,
        error_code="test_failure",
        error_message="Safe test failure.",
    )
    delete_video_course(course.id)

    with pytest.raises(
        ReliableTaskRetryError,
        match="no longer active",
    ):
        retry_task(task.id)


def test_startup_recovery_invalidates_claim_and_allows_explicit_retry():
    create_task(make_task("task-1", max_attempts=2))
    claimed = claim_task("task-1", "worker-a")
    assert claimed is not None
    assert claimed.claim_token is not None

    recovered = recover_interrupted_tasks()

    assert [task.id for task in recovered] == ["task-1"]
    interrupted = get_task("task-1")
    assert interrupted is not None
    assert interrupted.status == ReliableTaskStatus.failed
    assert interrupted.error_code == "interrupted"
    assert interrupted.recovery_count == 1
    assert interrupted.retryable is True
    assert interrupted.claim_token is None

    with pytest.raises(ReliableTaskClaimLostError):
        update_task_progress(
            "task-1",
            claimed.claim_token,
            TaskProgress(current=1),
        )

    retried = retry_task("task-1")
    assert retried.status == ReliableTaskStatus.queued
    assert retried.attempt == 2

    second_claim = claim_task("task-1", "worker-b")
    assert second_claim is not None
    second_recovery = recover_interrupted_tasks()
    assert second_recovery[0].retryable is False

    with pytest.raises(ReliableTaskRetryError):
        retry_task("task-1")


def test_startup_recovery_finishes_pending_cancellation_as_canceled():
    create_task(make_task("task-1", max_attempts=2))
    claimed = claim_task("task-1", "worker-a")
    assert claimed is not None
    assert claimed.claim_token is not None
    request_task_cancel("task-1")

    recovered = recover_interrupted_tasks()

    assert [task.id for task in recovered] == ["task-1"]
    task = recovered[0]
    assert task.status == ReliableTaskStatus.canceled
    assert task.error_code is None
    assert task.error_message is None
    assert task.retryable is True
    assert task.recovery_count == 1
    assert task.claim_token is None
    assert list_task_events(task.id)[-1].event_type == (
        ReliableTaskEventType.canceled
    )
