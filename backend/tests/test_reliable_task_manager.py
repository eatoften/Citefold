import time
from threading import Event

import pytest

import app.reliable_task_manager as reliable_task_manager_module
from app.db import connect
from app.reliable_task import ReliableTask, ReliableTaskStatus
from app.reliable_task_manager import (
    ReliableTaskContext,
    ReliableTaskExecutionError,
    ReliableTaskManager,
)
from app.reliable_task_schema import create_reliable_task_tables
from app.reliable_task_store import clear_reliable_tasks, create_task, get_task


@pytest.fixture(autouse=True)
def reliable_task_tables():
    with connect() as conn:
        create_reliable_task_tables(conn)
    clear_reliable_tasks()
    yield
    clear_reliable_tasks()


def queued_task(task_id: str, kind: str = "test.work") -> ReliableTask:
    return ReliableTask(
        id=task_id,
        kind=kind,
        payload={"value": task_id},
    )


def test_manager_runs_registered_handler_and_persists_progress():
    manager = ReliableTaskManager(
        max_workers=1,
        max_queue_size=1,
        poll_interval_seconds=0.02,
    )

    def handler(context: ReliableTaskContext):
        context.report_progress(
            current=1,
            total=1,
            stage="done",
            message="Finished test work",
        )
        return {"echo": context.payload["value"]}

    manager.register("test.work", handler)
    manager.start(recover=False)
    try:
        reservation = manager.enqueue(
            kind="test.work",
            task_id="task-1",
            payload={"value": "hello"},
        )
        completed = manager.wait_for_task(
            reservation.task.id,
            {ReliableTaskStatus.succeeded},
        )
    finally:
        manager.shutdown()

    assert completed.result == {"echo": "hello"}
    assert completed.progress.current == 1
    assert completed.progress.stage == "done"


def test_enqueue_returns_durable_reservation_when_notification_fails(
    monkeypatch,
):
    manager = ReliableTaskManager(
        max_workers=1,
        max_queue_size=0,
        poll_interval_seconds=0.02,
    )
    manager.register(
        "test.work",
        lambda context: {"task_id": context.task_id},
    )
    original_get_task = reliable_task_manager_module.get_task
    failed_once = False

    def fail_first_notification(task_id: str):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise RuntimeError("transient notification failure")
        return original_get_task(task_id)

    monkeypatch.setattr(
        reliable_task_manager_module,
        "get_task",
        fail_first_notification,
    )
    try:
        reservation = manager.enqueue(
            kind="test.work",
            task_id="durable-notify-failure",
        )
        queued = original_get_task(reservation.task.id)
        assert queued is not None
        assert queued.status == ReliableTaskStatus.queued

        manager.start(recover=False)
        completed = manager.wait_for_task(
            reservation.task.id,
            {ReliableTaskStatus.succeeded},
        )
    finally:
        manager.shutdown()

    assert completed.result == {
        "task_id": "durable-notify-failure"
    }


def test_manager_honors_cooperative_cancel_checkpoint():
    started = Event()
    continue_work = Event()
    manager = ReliableTaskManager(
        max_workers=1,
        max_queue_size=1,
        poll_interval_seconds=0.02,
    )

    def handler(context: ReliableTaskContext):
        started.set()
        assert continue_work.wait(2)
        context.checkpoint()
        return {"unexpected": True}

    manager.register("test.work", handler)
    manager.start(recover=False)
    try:
        reservation = manager.enqueue(
            kind="test.work",
            task_id="task-1",
        )
        assert started.wait(2)
        canceling = manager.request_cancel(reservation.task.id)
        assert canceling.status == ReliableTaskStatus.canceling
        continue_work.set()
        canceled = manager.wait_for_task(
            reservation.task.id,
            {ReliableTaskStatus.canceled},
        )
    finally:
        continue_work.set()
        manager.shutdown()

    assert canceled.result is None
    assert canceled.cancel_requested_at is not None


def test_late_cancel_after_publication_settles_as_succeeded():
    published = Event()
    return_from_handler = Event()
    manager = ReliableTaskManager(
        max_workers=1,
        max_queue_size=1,
        poll_interval_seconds=0.02,
    )

    def handler(_: ReliableTaskContext):
        # This represents a handler whose domain transaction has committed.
        published.set()
        assert return_from_handler.wait(2)
        return {"published": True}

    manager.register("test.work", handler)
    manager.start(recover=False)
    try:
        reservation = manager.enqueue(
            kind="test.work",
            task_id="task-1",
        )
        assert published.wait(2)
        canceling = manager.request_cancel(reservation.task.id)
        assert canceling.status == ReliableTaskStatus.canceling
        return_from_handler.set()
        completed = manager.wait_for_task(
            reservation.task.id,
            {ReliableTaskStatus.succeeded},
        )
    finally:
        return_from_handler.set()
        manager.shutdown()

    assert completed.result == {"published": True}
    assert completed.cancel_requested_at is not None


def test_quiesce_stops_accepting_and_waits_for_cooperative_cancel():
    started = Event()
    manager = ReliableTaskManager(
        max_workers=1,
        max_queue_size=0,
        poll_interval_seconds=0.01,
    )

    def handler(context: ReliableTaskContext):
        started.set()
        while True:
            context.checkpoint()
            time.sleep(0.01)

    manager.register("test.work", handler)
    manager.start(recover=False)
    try:
        reservation = manager.enqueue(
            kind="test.work",
            task_id="task-quiesce",
        )
        assert started.wait(2)
        waiting = manager.enqueue(
            kind="test.work",
            task_id="task-waiting-at-quiesce",
        )
        assert get_task(waiting.task.id).status == ReliableTaskStatus.queued

        assert manager.quiesce(timeout_seconds=2) is True
        canceled = manager.wait_for_task(
            reservation.task.id,
            {ReliableTaskStatus.canceled},
        )
        canceled_waiting = manager.wait_for_task(
            waiting.task.id,
            {ReliableTaskStatus.canceled},
        )
        with pytest.raises(RuntimeError, match="not accepting"):
            manager.enqueue(kind="test.work", task_id="after-quiesce")
    finally:
        manager.shutdown()

    assert canceled.status == ReliableTaskStatus.canceled
    assert canceled_waiting.status == ReliableTaskStatus.canceled


def test_quiesce_timeout_does_not_report_a_safe_idle_state():
    started = Event()
    release = Event()
    manager = ReliableTaskManager(
        max_workers=1,
        max_queue_size=0,
        poll_interval_seconds=0.01,
    )

    def handler(_: ReliableTaskContext):
        started.set()
        assert release.wait(2)
        return {"unexpected": True}

    manager.register("test.work", handler)
    manager.start(recover=False)
    try:
        reservation = manager.enqueue(
            kind="test.work",
            task_id="task-quiesce-timeout",
        )
        assert started.wait(2)

        assert manager.quiesce(timeout_seconds=0.01) is False
        assert get_task(reservation.task.id).status == (
            ReliableTaskStatus.canceling
        )
        release.set()
        completed = manager.wait_for_task(
            reservation.task.id,
            {ReliableTaskStatus.succeeded},
        )
    finally:
        release.set()
        manager.shutdown()

    assert completed.result == {"unexpected": True}
    assert completed.cancel_requested_at is not None


def test_manager_pool_capacity_is_bounded():
    started = Event()
    continue_work = Event()
    manager = ReliableTaskManager(
        max_workers=1,
        max_queue_size=0,
        poll_interval_seconds=0.02,
    )

    def handler(context: ReliableTaskContext):
        started.set()
        assert continue_work.wait(2)
        return {"task_id": context.task_id}

    manager.register("test.work", handler)
    create_task(queued_task("task-1"))
    create_task(queued_task("task-2"))
    try:
        assert manager.submit("task-1") is True
        assert started.wait(2)
        assert manager.submit("task-2") is False
        continue_work.set()
        manager.wait_for_task(
            "task-1",
            {ReliableTaskStatus.succeeded},
        )
        manager.wait_for_idle()

        assert manager.submit("task-2") is True
        manager.wait_for_task(
            "task-2",
            {ReliableTaskStatus.succeeded},
        )
    finally:
        continue_work.set()
        manager.shutdown()


def test_manager_persists_only_safe_handler_errors():
    manager = ReliableTaskManager(
        max_workers=1,
        max_queue_size=0,
        poll_interval_seconds=0.02,
    )

    def handler(_: ReliableTaskContext):
        raise RuntimeError("secret-provider-token")

    manager.register("test.work", handler)
    create_task(queued_task("task-1"))
    try:
        assert manager.submit("task-1")
        failed = manager.wait_for_task(
            "task-1",
            {ReliableTaskStatus.failed},
        )
    finally:
        manager.shutdown()

    assert failed.error_code == "handler_failed"
    assert failed.error_message == "Task execution failed."
    assert "secret-provider-token" not in failed.error_message


def test_manager_preserves_explicit_safe_execution_error():
    manager = ReliableTaskManager(
        max_workers=1,
        max_queue_size=0,
        poll_interval_seconds=0.02,
    )

    def handler(_: ReliableTaskContext):
        raise ReliableTaskExecutionError(
            "The selected source is no longer available.",
            error_code="source_missing",
            retryable=False,
        )

    manager.register("test.work", handler)
    create_task(queued_task("task-1"))
    try:
        manager.submit("task-1")
        failed = manager.wait_for_task(
            "task-1",
            {ReliableTaskStatus.failed},
        )
    finally:
        manager.shutdown()

    persisted = get_task("task-1")
    assert failed.error_code == "source_missing"
    assert failed.retryable is False
    assert persisted == failed
