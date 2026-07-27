from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Collection
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Protocol

from pydantic import BaseModel, JsonValue

from .reliable_task import (
    ReliableTask,
    ReliableTaskStatus,
    TaskProgress,
)
from .reliable_task_store import (
    ReliableTaskClaimLostError,
    ReliableTaskNotFoundError,
    ReliableTaskReservation,
    cancellation_requested,
    claim_task,
    fail_task,
    get_task,
    heartbeat_task,
    list_tasks,
    list_runnable_tasks,
    mark_task_canceled,
    recover_interrupted_tasks,
    request_task_cancel,
    reserve_task,
    retry_task,
    succeed_task,
    update_task_progress,
)
from .workspace_lifecycle import workspace_lifecycle_lock


logger = logging.getLogger(__name__)


class ReliableTaskCancellationRequested(RuntimeError):
    pass


class ReliableTaskExecutionError(RuntimeError):
    def __init__(
        self,
        public_message: str,
        *,
        error_code: str = "task_execution_failed",
        retryable: bool = True,
    ) -> None:
        cleaned_message = public_message.strip()
        cleaned_code = error_code.strip()
        if not cleaned_message or not cleaned_code:
            raise ValueError("Task execution errors need safe public details.")
        super().__init__(cleaned_message)
        self.public_message = cleaned_message
        self.error_code = cleaned_code
        self.retryable = retryable


TaskHandlerResult = dict[str, JsonValue] | BaseModel | None


class ReliableTaskHandler(Protocol):
    def __call__(
        self,
        context: "ReliableTaskContext",
    ) -> TaskHandlerResult: ...


class ReliableTaskHandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, ReliableTaskHandler] = {}
        self._lock = threading.RLock()

    def register(
        self,
        kind: str,
        handler: ReliableTaskHandler,
        *,
        replace: bool = False,
    ) -> None:
        cleaned_kind = kind.strip()
        if not cleaned_kind:
            raise ValueError("Task kind is required.")
        with self._lock:
            if cleaned_kind in self._handlers and not replace:
                raise ValueError(
                    f"A handler is already registered for {cleaned_kind!r}."
                )
            self._handlers[cleaned_kind] = handler

    def unregister(self, kind: str) -> None:
        with self._lock:
            self._handlers.pop(kind, None)

    def resolve(self, kind: str) -> ReliableTaskHandler | None:
        with self._lock:
            return self._handlers.get(kind)

    def kinds(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._handlers)


class ReliableTaskContext:
    def __init__(self, task: ReliableTask) -> None:
        if (
            task.status != ReliableTaskStatus.running
            or task.claim_token is None
        ):
            raise ValueError("Task contexts require a claimed task.")
        self.task = task
        self.task_id = task.id
        self.claim_token = task.claim_token

    @property
    def payload(self) -> dict[str, JsonValue]:
        return self.task.payload

    def checkpoint(self) -> None:
        if cancellation_requested(self.task_id, self.claim_token):
            raise ReliableTaskCancellationRequested(
                f"Cancellation requested for task {self.task_id!r}."
            )
        self.task = heartbeat_task(self.task_id, self.claim_token)

    def report_progress(
        self,
        progress: TaskProgress | None = None,
        *,
        current: float | None = None,
        total: float | None = None,
        stage: str | None = None,
        message: str | None = None,
        details: dict[str, JsonValue] | None = None,
    ) -> ReliableTask:
        self.checkpoint()
        if progress is None:
            progress = TaskProgress(
                current=0.0 if current is None else current,
                total=total,
                stage=stage,
                message=message,
                details=details or {},
            )
        elif any(
            value is not None
            for value in (current, total, stage, message, details)
        ):
            raise ValueError(
                "Pass a TaskProgress object or progress fields, not both."
            )
        self.task = update_task_progress(
            self.task_id,
            self.claim_token,
            progress,
        )
        return self.task


class ReliableTaskManager:
    """Runs registered durable tasks with a bounded local worker pool."""

    def __init__(
        self,
        *,
        max_workers: int = 2,
        max_queue_size: int = 8,
        poll_interval_seconds: float = 0.25,
        worker_id_prefix: str = "local",
        registry: ReliableTaskHandlerRegistry | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least one.")
        if max_queue_size < 0:
            raise ValueError("max_queue_size cannot be negative.")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive.")
        cleaned_prefix = worker_id_prefix.strip()
        if not cleaned_prefix:
            raise ValueError("worker_id_prefix is required.")

        self.max_workers = max_workers
        self.max_queue_size = max_queue_size
        self.poll_interval_seconds = poll_interval_seconds
        self.worker_id_prefix = cleaned_prefix
        self.registry = registry or ReliableTaskHandlerRegistry()
        self._capacity = threading.BoundedSemaphore(
            max_workers + max_queue_size
        )
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="reliable-task",
        )
        self._scheduled: set[str] = set()
        self._scheduled_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._dispatcher: threading.Thread | None = None
        self._started = False
        self._quiescing = False
        self._closed = False

    def register(
        self,
        kind: str,
        handler: ReliableTaskHandler,
        *,
        replace: bool = False,
    ) -> None:
        self.registry.register(kind, handler, replace=replace)
        self._wake_event.set()

    def start(self, *, recover: bool = True) -> list[ReliableTask]:
        with self._state_lock:
            if self._closed:
                raise RuntimeError("A closed task manager cannot be restarted.")
            if self._started:
                return []
            recovered = recover_interrupted_tasks() if recover else []
            self._quiescing = False
            self._stop_event.clear()
            self._dispatcher = threading.Thread(
                target=self._dispatch_loop,
                name="reliable-task-dispatcher",
                daemon=True,
            )
            self._started = True
            self._dispatcher.start()
        self._wake_event.set()
        return recovered

    def enqueue(
        self,
        *,
        kind: str,
        payload: dict[str, JsonValue] | None = None,
        task_id: str | None = None,
        course_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        idempotency_key: str | None = None,
        active_key: str | None = None,
        priority: int = 0,
        max_attempts: int = 3,
    ) -> ReliableTaskReservation:
        with workspace_lifecycle_lock():
            with self._state_lock:
                if self._closed or self._quiescing:
                    raise RuntimeError(
                        "The task manager is not accepting new work."
                    )
                reservation = reserve_task(
                    kind=kind,
                    payload=payload,
                    task_id=task_id,
                    course_id=course_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    idempotency_key=idempotency_key,
                    active_key=active_key,
                    priority=priority,
                    max_attempts=max_attempts,
                )
                if reservation.task.status == ReliableTaskStatus.queued:
                    self.notify(reservation.task.id)
                return reservation

    def notify(self, task_id: str) -> bool:
        try:
            task = get_task(task_id)
            accepted = bool(
                task is not None
                and task.status == ReliableTaskStatus.queued
                and self.registry.resolve(task.kind) is not None
                and self.submit(task_id)
            )
        except Exception:
            # Once reserve_task/retry_task commits, the durable queue is the
            # source of truth. A best-effort wake-up failure must not make the
            # caller believe that no task was created and compensate a domain
            # reservation that a later dispatcher can still execute.
            logger.exception(
                "Reliable task %s was persisted but could not be notified.",
                task_id,
            )
            accepted = False
        self._wake_event.set()
        return accepted

    def submit(self, task_id: str) -> bool:
        with self._state_lock:
            if self._closed or self._quiescing:
                return False
            with self._scheduled_lock:
                if task_id in self._scheduled:
                    return False
                if not self._capacity.acquire(blocking=False):
                    return False
                self._scheduled.add(task_id)
            try:
                future = self._executor.submit(self._execute_task, task_id)
            except RuntimeError:
                with self._scheduled_lock:
                    self._scheduled.discard(task_id)
                self._capacity.release()
                return False
            future.add_done_callback(
                lambda completed, scheduled_id=task_id: self._task_done(
                    scheduled_id,
                    completed,
                )
            )
        return True

    def dispatch_once(self) -> int:
        kinds = self.registry.kinds()
        if not kinds or self._closed:
            return 0
        tasks = list_runnable_tasks(
            limit=self.max_workers + self.max_queue_size,
            kinds=kinds,
        )
        accepted = 0
        for task in tasks:
            if self.submit(task.id):
                accepted += 1
        return accepted

    def request_cancel(self, task_id: str) -> ReliableTask:
        task = request_task_cancel(task_id)
        self._wake_event.set()
        return task

    def retry(self, task_id: str) -> ReliableTask:
        with workspace_lifecycle_lock():
            with self._state_lock:
                if self._closed or self._quiescing:
                    raise RuntimeError(
                        "The task manager is not accepting new work."
                    )
                task = retry_task(task_id)
                self.notify(task.id)
                return task

    def wait_for_task(
        self,
        task_id: str,
        statuses: Collection[ReliableTaskStatus],
        *,
        timeout_seconds: float = 5.0,
    ) -> ReliableTask:
        deadline = time.monotonic() + timeout_seconds
        expected = set(statuses)
        while True:
            task = get_task(task_id)
            if task is None:
                raise KeyError(task_id)
            if task.status in expected:
                return task
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Task {task_id!r} did not reach the expected state."
                )
            time.sleep(min(0.02, self.poll_interval_seconds))

    def wait_for_idle(self, *, timeout_seconds: float = 5.0) -> None:
        deadline = time.monotonic() + timeout_seconds
        while True:
            with self._scheduled_lock:
                idle = not self._scheduled
            if idle:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("Reliable task manager did not become idle.")
            time.sleep(min(0.02, self.poll_interval_seconds))

    def quiesce(self, *, timeout_seconds: float = 5.0) -> bool:
        """Stop dispatch and cooperatively cancel all accepted work."""

        if timeout_seconds < 0:
            raise ValueError("Quiesce timeout cannot be negative.")
        deadline = time.monotonic() + timeout_seconds
        with self._state_lock:
            if self._closed:
                with self._scheduled_lock:
                    return not self._scheduled
            self._quiescing = True
            self._stop_event.set()
            self._wake_event.set()
            dispatcher = self._dispatcher

        if dispatcher is not None and dispatcher is not threading.current_thread():
            remaining = max(0.0, deadline - time.monotonic())
            dispatcher.join(timeout=remaining)

        while queued_tasks := list_tasks(
            statuses={ReliableTaskStatus.queued},
            limit=500,
        ):
            for task in queued_tasks:
                try:
                    request_task_cancel(task.id)
                except ReliableTaskNotFoundError:
                    continue

        with self._scheduled_lock:
            scheduled_ids = tuple(self._scheduled)
        for task_id in scheduled_ids:
            try:
                request_task_cancel(task_id)
            except ReliableTaskNotFoundError:
                continue

        while True:
            with self._scheduled_lock:
                idle = not self._scheduled
            if idle:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(0.02, self.poll_interval_seconds))

    def shutdown(
        self,
        *,
        wait: bool = True,
        cancel_futures: bool = False,
    ) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._quiescing = True
            self._stop_event.set()
            self._wake_event.set()
            dispatcher = self._dispatcher
        if dispatcher is not None:
            dispatcher.join(
                timeout=None if wait else self.poll_interval_seconds * 2
            )
        self._executor.shutdown(
            wait=wait,
            cancel_futures=cancel_futures,
        )

    def __enter__(self) -> "ReliableTaskManager":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.shutdown()

    def _dispatch_loop(self) -> None:
        while not self._stop_event.is_set():
            self.dispatch_once()
            self._wake_event.wait(self.poll_interval_seconds)
            self._wake_event.clear()

    def _execute_task(self, task_id: str) -> None:
        thread_id = threading.get_ident()
        worker_id = f"{self.worker_id_prefix}-{thread_id}"
        claimed = claim_task(task_id, worker_id)
        if claimed is None or claimed.claim_token is None:
            return
        claim_token = claimed.claim_token
        context = ReliableTaskContext(claimed)
        handler = self.registry.resolve(claimed.kind)
        if handler is None:
            self._fail_claim(
                task_id,
                claim_token,
                error_code="handler_not_registered",
                error_message="No task handler is available.",
                retryable=True,
            )
            return

        try:
            context.checkpoint()
            result = _normalize_result(handler(context))
            succeed_task(task_id, claim_token, result)
        except ReliableTaskCancellationRequested:
            self._cancel_claim(task_id, claim_token)
        except ReliableTaskClaimLostError:
            self._settle_canceling_claim(task_id, claim_token)
        except Exception as exc:
            logger.exception(
                "Reliable task %s (%s) failed.",
                task_id,
                claimed.kind,
            )
            if self._is_canceling(task_id, claim_token):
                self._cancel_claim(task_id, claim_token)
            elif isinstance(exc, ReliableTaskExecutionError):
                self._fail_claim(
                    task_id,
                    claim_token,
                    error_code=exc.error_code,
                    error_message=exc.public_message,
                    retryable=exc.retryable,
                )
            else:
                self._fail_claim(
                    task_id,
                    claim_token,
                    error_code="handler_failed",
                    error_message="Task execution failed.",
                    retryable=True,
                )

    def _task_done(
        self,
        task_id: str,
        future: Future[None],
    ) -> None:
        with self._scheduled_lock:
            self._scheduled.discard(task_id)
        self._capacity.release()
        self._wake_event.set()
        try:
            future.result()
        except Exception:
            logger.exception(
                "Reliable task worker crashed outside its safety boundary."
            )

    @staticmethod
    def _is_canceling(task_id: str, claim_token: str) -> bool:
        try:
            return cancellation_requested(task_id, claim_token)
        except (ReliableTaskClaimLostError, ReliableTaskNotFoundError):
            return False

    @staticmethod
    def _cancel_claim(task_id: str, claim_token: str) -> None:
        try:
            mark_task_canceled(task_id, claim_token)
        except (ReliableTaskClaimLostError, ReliableTaskNotFoundError):
            return

    @staticmethod
    def _fail_claim(
        task_id: str,
        claim_token: str,
        *,
        error_code: str,
        error_message: str,
        retryable: bool,
    ) -> None:
        try:
            fail_task(
                task_id,
                claim_token,
                error_code=error_code,
                error_message=error_message,
                retryable=retryable,
            )
        except ReliableTaskClaimLostError:
            ReliableTaskManager._settle_canceling_claim(
                task_id,
                claim_token,
            )
        except ReliableTaskNotFoundError:
            return

    @staticmethod
    def _settle_canceling_claim(
        task_id: str,
        claim_token: str,
    ) -> None:
        task = get_task(task_id)
        if (
            task is not None
            and task.status == ReliableTaskStatus.canceling
            and task.claim_token == claim_token
        ):
            ReliableTaskManager._cancel_claim(task_id, claim_token)


def _normalize_result(
    result: TaskHandlerResult,
) -> dict[str, JsonValue]:
    if result is None:
        return {}
    if isinstance(result, BaseModel):
        value = result.model_dump(mode="json")
    else:
        value = result
    if not isinstance(value, dict):
        raise TypeError("Reliable task handlers must return a JSON object.")
    return value
