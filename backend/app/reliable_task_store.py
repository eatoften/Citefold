from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from sqlite3 import Connection, Row
from uuid import uuid4

from pydantic import JsonValue

from .db import connect, ensure_db
from .job import utc_now
from .reliable_task import (
    ReliableTask,
    ReliableTaskEventType,
    ReliableTaskStatus,
    TaskEvent,
    TaskProgress,
)


class ReliableTaskStoreError(RuntimeError):
    pass


class ReliableTaskNotFoundError(ReliableTaskStoreError):
    def __init__(self, task_id: str) -> None:
        super().__init__(f"Reliable task {task_id!r} does not exist.")
        self.task_id = task_id


class ReliableTaskStateConflictError(ReliableTaskStoreError):
    def __init__(
        self,
        task_id: str,
        status: ReliableTaskStatus,
        operation: str,
    ) -> None:
        super().__init__(
            f"Task {task_id!r} is {status.value}; cannot {operation}."
        )
        self.task_id = task_id
        self.status = status
        self.operation = operation


class ReliableTaskClaimLostError(ReliableTaskStoreError):
    def __init__(
        self,
        task_id: str,
        status: ReliableTaskStatus | None = None,
    ) -> None:
        detail = "" if status is None else f" Current status: {status.value}."
        super().__init__(f"Task {task_id!r} claim is no longer valid.{detail}")
        self.task_id = task_id
        self.status = status


class ReliableTaskIdempotencyConflictError(ReliableTaskStoreError):
    def __init__(self, kind: str, idempotency_key: str) -> None:
        super().__init__(
            "The idempotency key was already used for a different "
            f"{kind!r} task."
        )
        self.kind = kind
        self.idempotency_key = idempotency_key


class ReliableTaskActiveConflictError(ReliableTaskStoreError):
    def __init__(self, active_key: str, active_task_id: str) -> None:
        super().__init__(
            f"Active task {active_task_id!r} already owns {active_key!r}."
        )
        self.active_key = active_key
        self.active_task_id = active_task_id


class ReliableTaskRetryError(ReliableTaskStoreError):
    pass


@dataclass(frozen=True)
class ReliableTaskReservation:
    task: ReliableTask
    replayed: bool = False


def reserve_task(
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
    available_at: datetime | None = None,
) -> ReliableTaskReservation:
    task = ReliableTask(
        id=task_id or uuid4().hex,
        kind=kind,
        course_id=course_id,
        resource_type=resource_type,
        resource_id=resource_id,
        payload=payload or {},
        idempotency_key=idempotency_key,
        active_key=active_key,
        priority=priority,
        max_attempts=max_attempts,
        available_at=available_at or utc_now(),
    )
    return create_task(task)


def create_task(task: ReliableTask) -> ReliableTaskReservation:
    if task.status != ReliableTaskStatus.queued:
        raise ValueError("New reliable tasks must start in queued state.")

    ensure_db()
    fingerprint = _request_fingerprint(task)
    now = utc_now()
    created_at = task.created_at
    available_at = task.available_at

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")

        if task.idempotency_key is not None:
            existing = conn.execute(
                """
                SELECT *
                FROM reliable_tasks
                WHERE kind = ? AND idempotency_key = ?
                """,
                (task.kind, task.idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["request_fingerprint"] != fingerprint:
                    raise ReliableTaskIdempotencyConflictError(
                        task.kind,
                        task.idempotency_key,
                    )
                return ReliableTaskReservation(
                    task=_row_to_task(existing),
                    replayed=True,
                )

        if task.active_key is not None:
            active_task_id = _find_active_task_id(conn, task.active_key)
            if active_task_id is not None:
                raise ReliableTaskActiveConflictError(
                    task.active_key,
                    active_task_id,
                )

        try:
            conn.execute(
                """
                INSERT INTO reliable_tasks (
                    id, kind, course_id, resource_type, resource_id, status,
                    payload_json, result_json, request_fingerprint,
                    idempotency_key, active_key, priority, attempt,
                    max_attempts, recovery_count, progress_json,
                    cancel_requested_at, worker_id, claim_token, error_code,
                    error_message, retryable, available_at, created_at,
                    updated_at, started_at, completed_at, heartbeat_at
                ) VALUES (
                    ?, ?, ?, ?, ?, 'queued', ?, NULL, ?, ?, ?, ?, 1, ?, 0,
                    ?, NULL, NULL, NULL, NULL, NULL, 1, ?, ?, ?, NULL, NULL,
                    NULL
                )
                """,
                (
                    task.id,
                    task.kind,
                    task.course_id,
                    task.resource_type,
                    task.resource_id,
                    _json_text(task.payload),
                    fingerprint,
                    task.idempotency_key,
                    task.active_key,
                    task.priority,
                    task.max_attempts,
                    _json_text(task.progress.model_dump(mode="json")),
                    _datetime_text(available_at),
                    _datetime_text(created_at),
                    _datetime_text(now),
                ),
            )
        except sqlite3.IntegrityError:
            # Resolve partial-index conflicts into stable domain errors.
            if task.idempotency_key is not None:
                existing = conn.execute(
                    """
                    SELECT *
                    FROM reliable_tasks
                    WHERE kind = ? AND idempotency_key = ?
                    """,
                    (task.kind, task.idempotency_key),
                ).fetchone()
                if existing is not None:
                    if existing["request_fingerprint"] == fingerprint:
                        return ReliableTaskReservation(
                            task=_row_to_task(existing),
                            replayed=True,
                        )
                    raise ReliableTaskIdempotencyConflictError(
                        task.kind,
                        task.idempotency_key,
                    ) from None
            if task.active_key is not None:
                active_task_id = _find_active_task_id(
                    conn,
                    task.active_key,
                )
                if active_task_id is not None:
                    raise ReliableTaskActiveConflictError(
                        task.active_key,
                        active_task_id,
                    ) from None
            raise

        _append_event(
            conn,
            task_id=task.id,
            event_type=ReliableTaskEventType.created,
            from_status=None,
            to_status=ReliableTaskStatus.queued,
            message="Task queued.",
            data={"attempt": 1},
            created_at=now,
        )
        row = _require_task_row(conn, task.id)
    return ReliableTaskReservation(task=_row_to_task(row))


def get_task(task_id: str) -> ReliableTask | None:
    ensure_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM reliable_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    return None if row is None else _row_to_task(row)


def list_tasks(
    *,
    course_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    statuses: Collection[ReliableTaskStatus | str] | None = None,
    limit: int = 100,
) -> list[ReliableTask]:
    ensure_db()
    bounded_limit = max(1, min(limit, 500))
    conditions: list[str] = []
    parameters: list[object] = []
    if course_id is not None:
        conditions.append("course_id = ?")
        parameters.append(course_id)
    if resource_type is not None:
        conditions.append("resource_type = ?")
        parameters.append(resource_type)
    if resource_id is not None:
        conditions.append("resource_id = ?")
        parameters.append(resource_id)
    if statuses:
        status_values = [_status_value(status) for status in statuses]
        placeholders = ", ".join("?" for _ in status_values)
        conditions.append(f"status IN ({placeholders})")
        parameters.extend(status_values)
    where_clause = (
        "" if not conditions else "WHERE " + " AND ".join(conditions)
    )
    parameters.append(bounded_limit)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM reliable_tasks
            {where_clause}
            ORDER BY updated_at DESC, id
            LIMIT ?
            """,
            parameters,
        ).fetchall()
    return [_row_to_task(row) for row in rows]


def list_runnable_tasks(
    *,
    limit: int = 100,
    kinds: Collection[str] | None = None,
) -> list[ReliableTask]:
    ensure_db()
    now = _datetime_text(utc_now())
    parameters: list[object] = [now]
    kind_clause = ""
    if kinds:
        cleaned_kinds = sorted({kind.strip() for kind in kinds if kind.strip()})
        if not cleaned_kinds:
            return []
        placeholders = ", ".join("?" for _ in cleaned_kinds)
        kind_clause = f"AND kind IN ({placeholders})"
        parameters.extend(cleaned_kinds)
    parameters.append(max(1, min(limit, 500)))
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM reliable_tasks
            WHERE status = 'queued' AND available_at <= ?
            {kind_clause}
            ORDER BY priority DESC, available_at, created_at, id
            LIMIT ?
            """,
            parameters,
        ).fetchall()
    return [_row_to_task(row) for row in rows]


def list_task_events(task_id: str) -> list[TaskEvent]:
    ensure_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM reliable_task_events
            WHERE task_id = ?
            ORDER BY sequence
            """,
            (task_id,),
        ).fetchall()
    return [_row_to_event(row) for row in rows]


def claim_task(task_id: str, worker_id: str) -> ReliableTask | None:
    cleaned_worker_id = worker_id.strip()
    if not cleaned_worker_id:
        raise ValueError("worker_id is required.")

    ensure_db()
    now = utc_now()
    claim_token = uuid4().hex
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            UPDATE reliable_tasks
            SET status = 'running',
                worker_id = ?,
                claim_token = ?,
                started_at = ?,
                updated_at = ?,
                heartbeat_at = ?
            WHERE id = ?
              AND status = 'queued'
              AND available_at <= ?
            """,
            (
                cleaned_worker_id,
                claim_token,
                _datetime_text(now),
                _datetime_text(now),
                _datetime_text(now),
                task_id,
                _datetime_text(now),
            ),
        )
        if cursor.rowcount != 1:
            return None
        _append_event(
            conn,
            task_id=task_id,
            event_type=ReliableTaskEventType.claimed,
            from_status=ReliableTaskStatus.queued,
            to_status=ReliableTaskStatus.running,
            message="Task claimed by a worker.",
            data={"worker_id": cleaned_worker_id},
            created_at=now,
        )
        row = _require_task_row(conn, task_id)
    return _row_to_task(row)


def update_task_progress(
    task_id: str,
    claim_token: str,
    progress: TaskProgress,
) -> ReliableTask:
    ensure_db()
    now = utc_now()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            UPDATE reliable_tasks
            SET progress_json = ?, updated_at = ?, heartbeat_at = ?
            WHERE id = ? AND status = 'running' AND claim_token = ?
            """,
            (
                _json_text(progress.model_dump(mode="json")),
                _datetime_text(now),
                _datetime_text(now),
                task_id,
                claim_token,
            ),
        )
        if cursor.rowcount != 1:
            _raise_claim_lost(conn, task_id)
        _append_event(
            conn,
            task_id=task_id,
            event_type=ReliableTaskEventType.progress_updated,
            from_status=ReliableTaskStatus.running,
            to_status=ReliableTaskStatus.running,
            message=progress.message,
            data={"progress": progress.model_dump(mode="json")},
            created_at=now,
        )
        row = _require_task_row(conn, task_id)
    return _row_to_task(row)


def heartbeat_task(task_id: str, claim_token: str) -> ReliableTask:
    ensure_db()
    now = utc_now()
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE reliable_tasks
            SET heartbeat_at = ?, updated_at = ?
            WHERE id = ?
              AND status = 'running'
              AND claim_token = ?
            """,
            (
                _datetime_text(now),
                _datetime_text(now),
                task_id,
                claim_token,
            ),
        )
        if cursor.rowcount != 1:
            _raise_claim_lost(conn, task_id)
        row = _require_task_row(conn, task_id)
    return _row_to_task(row)


def cancellation_requested(task_id: str, claim_token: str) -> bool:
    ensure_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT status, claim_token
            FROM reliable_tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()
    if row is None:
        raise ReliableTaskNotFoundError(task_id)
    status = ReliableTaskStatus(row["status"])
    if row["claim_token"] != claim_token or status not in {
        ReliableTaskStatus.running,
        ReliableTaskStatus.canceling,
    }:
        raise ReliableTaskClaimLostError(task_id, status)
    return status == ReliableTaskStatus.canceling


def request_task_cancel(task_id: str) -> ReliableTask:
    ensure_db()
    now = utc_now()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _require_task_row(conn, task_id)
        status = ReliableTaskStatus(row["status"])
        if status == ReliableTaskStatus.queued:
            conn.execute(
                """
                UPDATE reliable_tasks
                SET status = 'canceled',
                    cancel_requested_at = ?,
                    completed_at = ?,
                    updated_at = ?,
                    retryable = 1
                WHERE id = ? AND status = 'queued'
                """,
                (
                    _datetime_text(now),
                    _datetime_text(now),
                    _datetime_text(now),
                    task_id,
                ),
            )
            _append_event(
                conn,
                task_id=task_id,
                event_type=ReliableTaskEventType.cancel_requested,
                from_status=ReliableTaskStatus.queued,
                to_status=ReliableTaskStatus.canceled,
                message="Queued task canceled.",
                created_at=now,
            )
        elif status == ReliableTaskStatus.running:
            conn.execute(
                """
                UPDATE reliable_tasks
                SET status = 'canceling',
                    cancel_requested_at = ?,
                    updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (
                    _datetime_text(now),
                    _datetime_text(now),
                    task_id,
                ),
            )
            _append_event(
                conn,
                task_id=task_id,
                event_type=ReliableTaskEventType.cancel_requested,
                from_status=ReliableTaskStatus.running,
                to_status=ReliableTaskStatus.canceling,
                message="Cancellation requested.",
                created_at=now,
            )
        row = _require_task_row(conn, task_id)
    return _row_to_task(row)


def succeed_task(
    task_id: str,
    claim_token: str,
    result: dict[str, JsonValue] | None = None,
) -> ReliableTask:
    ensure_db()
    now = utc_now()
    result_value = result or {}
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        before = conn.execute(
            """
            SELECT status
            FROM reliable_tasks
            WHERE id = ? AND claim_token = ?
            """,
            (task_id, claim_token),
        ).fetchone()
        cursor = conn.execute(
            """
            UPDATE reliable_tasks
            SET status = 'succeeded',
                result_json = ?,
                worker_id = NULL,
                claim_token = NULL,
                error_code = NULL,
                error_message = NULL,
                completed_at = ?,
                updated_at = ?,
                heartbeat_at = ?
            WHERE id = ?
              AND status IN ('running', 'canceling')
              AND claim_token = ?
            """,
            (
                _json_text(result_value),
                _datetime_text(now),
                _datetime_text(now),
                _datetime_text(now),
                task_id,
                claim_token,
            ),
        )
        if cursor.rowcount != 1:
            _raise_claim_lost(conn, task_id)
        from_status = ReliableTaskStatus(before["status"])
        _append_event(
            conn,
            task_id=task_id,
            event_type=ReliableTaskEventType.succeeded,
            from_status=from_status,
            to_status=ReliableTaskStatus.succeeded,
            message="Task completed.",
            created_at=now,
        )
        row = _require_task_row(conn, task_id)
    return _row_to_task(row)


def fail_task(
    task_id: str,
    claim_token: str,
    *,
    error_code: str,
    error_message: str,
    retryable: bool = True,
) -> ReliableTask:
    cleaned_code = error_code.strip()
    cleaned_message = error_message.strip()
    if not cleaned_code or not cleaned_message:
        raise ValueError("A safe error code and message are required.")

    ensure_db()
    now = utc_now()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            UPDATE reliable_tasks
            SET status = 'failed',
                worker_id = NULL,
                claim_token = NULL,
                error_code = ?,
                error_message = ?,
                retryable = ?,
                completed_at = ?,
                updated_at = ?,
                heartbeat_at = ?
            WHERE id = ?
              AND status = 'running'
              AND claim_token = ?
            """,
            (
                cleaned_code[:100],
                cleaned_message[:1000],
                int(retryable),
                _datetime_text(now),
                _datetime_text(now),
                _datetime_text(now),
                task_id,
                claim_token,
            ),
        )
        if cursor.rowcount != 1:
            _raise_claim_lost(conn, task_id)
        _append_event(
            conn,
            task_id=task_id,
            event_type=ReliableTaskEventType.failed,
            from_status=ReliableTaskStatus.running,
            to_status=ReliableTaskStatus.failed,
            message=cleaned_message[:1000],
            data={
                "error_code": cleaned_code[:100],
                "retryable": retryable,
            },
            created_at=now,
        )
        row = _require_task_row(conn, task_id)
    return _row_to_task(row)


def mark_task_canceled(
    task_id: str,
    claim_token: str,
) -> ReliableTask:
    ensure_db()
    now = utc_now()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        before = conn.execute(
            """
            SELECT status
            FROM reliable_tasks
            WHERE id = ? AND claim_token = ?
            """,
            (task_id, claim_token),
        ).fetchone()
        cursor = conn.execute(
            """
            UPDATE reliable_tasks
            SET status = 'canceled',
                cancel_requested_at = COALESCE(cancel_requested_at, ?),
                worker_id = NULL,
                claim_token = NULL,
                completed_at = ?,
                updated_at = ?,
                heartbeat_at = ?
            WHERE id = ?
              AND status IN ('running', 'canceling')
              AND claim_token = ?
            """,
            (
                _datetime_text(now),
                _datetime_text(now),
                _datetime_text(now),
                _datetime_text(now),
                task_id,
                claim_token,
            ),
        )
        if cursor.rowcount != 1:
            _raise_claim_lost(conn, task_id)
        from_status = ReliableTaskStatus(before["status"])
        _append_event(
            conn,
            task_id=task_id,
            event_type=ReliableTaskEventType.canceled,
            from_status=from_status,
            to_status=ReliableTaskStatus.canceled,
            message="Task canceled.",
            created_at=now,
        )
        row = _require_task_row(conn, task_id)
    return _row_to_task(row)


def retry_task(
    task_id: str,
    *,
    available_at: datetime | None = None,
) -> ReliableTask:
    ensure_db()
    now = utc_now()
    next_available_at = available_at or now
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _require_task_row(conn, task_id)
        status = ReliableTaskStatus(row["status"])
        if status not in {
            ReliableTaskStatus.failed,
            ReliableTaskStatus.canceled,
        }:
            raise ReliableTaskStateConflictError(task_id, status, "retry")
        if status == ReliableTaskStatus.failed and not bool(row["retryable"]):
            raise ReliableTaskRetryError(
                f"Task {task_id!r} is not marked retryable."
            )
        if row["attempt"] >= row["max_attempts"]:
            raise ReliableTaskRetryError(
                f"Task {task_id!r} exhausted its retry attempts."
            )
        _require_retry_resource_active(conn, row)
        active_key = row["active_key"]
        if active_key is not None:
            active_task_id = _find_active_task_id(
                conn,
                active_key,
                exclude_task_id=task_id,
            )
            if active_task_id is not None:
                raise ReliableTaskActiveConflictError(
                    active_key,
                    active_task_id,
                )

        try:
            conn.execute(
                """
                UPDATE reliable_tasks
                SET status = 'queued',
                    attempt = attempt + 1,
                    result_json = NULL,
                    progress_json = '{}',
                    cancel_requested_at = NULL,
                    worker_id = NULL,
                    claim_token = NULL,
                    error_code = NULL,
                    error_message = NULL,
                    retryable = 1,
                    available_at = ?,
                    updated_at = ?,
                    started_at = NULL,
                    completed_at = NULL,
                    heartbeat_at = NULL
                WHERE id = ? AND status IN ('failed', 'canceled')
                """,
                (
                    _datetime_text(next_available_at),
                    _datetime_text(now),
                    task_id,
                ),
            )
        except sqlite3.IntegrityError:
            if active_key is not None:
                active_task_id = _find_active_task_id(
                    conn,
                    active_key,
                    exclude_task_id=task_id,
                )
                if active_task_id is not None:
                    raise ReliableTaskActiveConflictError(
                        active_key,
                        active_task_id,
                    ) from None
            raise
        _append_event(
            conn,
            task_id=task_id,
            event_type=ReliableTaskEventType.retry_queued,
            from_status=status,
            to_status=ReliableTaskStatus.queued,
            message="Task queued for retry.",
            data={"attempt": int(row["attempt"]) + 1},
            created_at=now,
        )
        row = _require_task_row(conn, task_id)
    return _row_to_task(row)


def recover_interrupted_tasks() -> list[ReliableTask]:
    """Fail orphaned claims so the user can explicitly retry them safely."""

    ensure_db()
    now = utc_now()
    recovered_ids: list[str] = []
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT id, status, attempt, max_attempts
            FROM reliable_tasks
            WHERE status IN ('running', 'canceling')
            ORDER BY created_at, id
            """
        ).fetchall()
        for row in rows:
            previous_status = ReliableTaskStatus(row["status"])
            retryable = row["attempt"] < row["max_attempts"]
            if previous_status == ReliableTaskStatus.canceling:
                conn.execute(
                    """
                    UPDATE reliable_tasks
                    SET status = 'canceled',
                        recovery_count = recovery_count + 1,
                        worker_id = NULL,
                        claim_token = NULL,
                        error_code = NULL,
                        error_message = NULL,
                        retryable = 1,
                        completed_at = ?,
                        updated_at = ?,
                        heartbeat_at = ?
                    WHERE id = ? AND status = 'canceling'
                    """,
                    (
                        _datetime_text(now),
                        _datetime_text(now),
                        _datetime_text(now),
                        row["id"],
                    ),
                )
                _append_event(
                    conn,
                    task_id=row["id"],
                    event_type=ReliableTaskEventType.canceled,
                    from_status=previous_status,
                    to_status=ReliableTaskStatus.canceled,
                    message="Pending cancellation completed during startup.",
                    created_at=now,
                )
            else:
                conn.execute(
                    """
                    UPDATE reliable_tasks
                    SET status = 'failed',
                        recovery_count = recovery_count + 1,
                        worker_id = NULL,
                        claim_token = NULL,
                        error_code = 'interrupted',
                        error_message = ?,
                        retryable = ?,
                        completed_at = ?,
                        updated_at = ?,
                        heartbeat_at = ?
                    WHERE id = ? AND status = 'running'
                    """,
                    (
                        "The app stopped before this task finished.",
                        int(retryable),
                        _datetime_text(now),
                        _datetime_text(now),
                        _datetime_text(now),
                        row["id"],
                    ),
                )
                _append_event(
                    conn,
                    task_id=row["id"],
                    event_type=ReliableTaskEventType.interrupted,
                    from_status=previous_status,
                    to_status=ReliableTaskStatus.failed,
                    message="Interrupted task recovered as failed.",
                    data={"retryable": retryable},
                    created_at=now,
                )
            recovered_ids.append(row["id"])
        recovered_rows = [
            _require_task_row(conn, task_id)
            for task_id in recovered_ids
        ]
    return [_row_to_task(row) for row in recovered_rows]


def clear_reliable_tasks() -> None:
    ensure_db()
    with connect() as conn:
        conn.execute("DELETE FROM reliable_task_events")
        conn.execute("DELETE FROM reliable_tasks")


def _request_fingerprint(task: ReliableTask) -> str:
    canonical_request = {
        "kind": task.kind,
        "course_id": task.course_id,
        "resource_type": task.resource_type,
        "resource_id": task.resource_id,
        "payload": task.payload,
        "active_key": task.active_key,
        "max_attempts": task.max_attempts,
    }
    return hashlib.sha256(
        _json_text(canonical_request).encode("utf-8")
    ).hexdigest()


def _require_retry_resource_active(conn: Connection, row: Row) -> None:
    course_id = row["course_id"]
    if course_id is not None:
        course = conn.execute(
            """
            SELECT 1
            FROM courses
            WHERE id = ? AND deleted_at IS NULL
            """,
            (course_id,),
        ).fetchone()
        if course is None:
            raise ReliableTaskRetryError(
                "The task course is no longer active."
            )

    resource_type = row["resource_type"]
    resource_id = row["resource_id"]
    if resource_type is None or resource_id is None:
        return
    queries = {
        "course": """
            SELECT 1 FROM courses
            WHERE id = ? AND deleted_at IS NULL
        """,
        "video_job": """
            SELECT 1
            FROM jobs
            INNER JOIN courses ON courses.id = jobs.course_id
            WHERE jobs.id = ?
              AND jobs.deleted_at IS NULL
              AND courses.deleted_at IS NULL
        """,
        "source_asset": """
            SELECT 1
            FROM source_assets
            INNER JOIN courses ON courses.id = source_assets.course_id
            WHERE source_assets.id = ?
              AND source_assets.deleted_at IS NULL
              AND courses.deleted_at IS NULL
        """,
        "chat_conversation": """
            SELECT 1
            FROM chat_conversations
            INNER JOIN courses ON courses.id = chat_conversations.course_id
            WHERE chat_conversations.id = ?
              AND chat_conversations.deleted_at IS NULL
              AND courses.deleted_at IS NULL
        """,
        "learning_document": """
            SELECT 1
            FROM learning_documents
            INNER JOIN courses ON courses.id = learning_documents.course_id
            WHERE learning_documents.id = ?
              AND learning_documents.deleted_at IS NULL
              AND courses.deleted_at IS NULL
        """,
        "knowledge_card": """
            SELECT 1
            FROM knowledge_cards
            INNER JOIN jobs ON jobs.id = knowledge_cards.job_id
            INNER JOIN courses ON courses.id = jobs.course_id
            WHERE knowledge_cards.id = ?
              AND knowledge_cards.deleted_at IS NULL
              AND jobs.deleted_at IS NULL
              AND courses.deleted_at IS NULL
        """,
    }
    query = queries.get(str(resource_type))
    if query is None:
        return
    if conn.execute(query, (resource_id,)).fetchone() is None:
        raise ReliableTaskRetryError(
            "The task resource is no longer active."
        )


def _find_active_task_id(
    conn: Connection,
    active_key: str,
    *,
    exclude_task_id: str | None = None,
) -> str | None:
    if exclude_task_id is None:
        row = conn.execute(
            """
            SELECT id
            FROM reliable_tasks
            WHERE active_key = ?
              AND status IN ('queued', 'running', 'canceling')
            LIMIT 1
            """,
            (active_key,),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT id
            FROM reliable_tasks
            WHERE active_key = ?
              AND status IN ('queued', 'running', 'canceling')
              AND id != ?
            LIMIT 1
            """,
            (active_key, exclude_task_id),
        ).fetchone()
    return None if row is None else str(row["id"])


def _append_event(
    conn: Connection,
    *,
    task_id: str,
    event_type: ReliableTaskEventType,
    from_status: ReliableTaskStatus | None,
    to_status: ReliableTaskStatus | None,
    message: str | None,
    data: dict[str, JsonValue] | None = None,
    created_at: datetime,
) -> None:
    sequence = int(
        conn.execute(
            """
            SELECT COALESCE(MAX(sequence), 0) + 1
            FROM reliable_task_events
            WHERE task_id = ?
            """,
            (task_id,),
        ).fetchone()[0]
    )
    conn.execute(
        """
        INSERT INTO reliable_task_events (
            id, task_id, sequence, event_type, from_status, to_status,
            message, data_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uuid4().hex,
            task_id,
            sequence,
            event_type.value,
            None if from_status is None else from_status.value,
            None if to_status is None else to_status.value,
            message,
            _json_text(data or {}),
            _datetime_text(created_at),
        ),
    )


def _require_task_row(conn: Connection, task_id: str) -> Row:
    row = conn.execute(
        "SELECT * FROM reliable_tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        raise ReliableTaskNotFoundError(task_id)
    return row


def _raise_claim_lost(conn: Connection, task_id: str) -> None:
    row = conn.execute(
        "SELECT status FROM reliable_tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        raise ReliableTaskNotFoundError(task_id)
    raise ReliableTaskClaimLostError(
        task_id,
        ReliableTaskStatus(row["status"]),
    )


def _row_to_task(row: Row) -> ReliableTask:
    result_json = row["result_json"]
    return ReliableTask(
        id=row["id"],
        kind=row["kind"],
        course_id=row["course_id"],
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        status=row["status"],
        payload=_json_object(row["payload_json"]),
        result=(
            None
            if result_json is None
            else _json_object(result_json)
        ),
        request_fingerprint=row["request_fingerprint"],
        idempotency_key=row["idempotency_key"],
        active_key=row["active_key"],
        priority=row["priority"],
        attempt=row["attempt"],
        max_attempts=row["max_attempts"],
        recovery_count=row["recovery_count"],
        progress=TaskProgress.model_validate(
            _json_object(row["progress_json"])
        ),
        cancel_requested_at=_datetime_from_text(
            row["cancel_requested_at"]
        ),
        worker_id=row["worker_id"],
        claim_token=row["claim_token"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        retryable=bool(row["retryable"]),
        available_at=datetime.fromisoformat(row["available_at"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        started_at=_datetime_from_text(row["started_at"]),
        completed_at=_datetime_from_text(row["completed_at"]),
        heartbeat_at=_datetime_from_text(row["heartbeat_at"]),
    )


def _row_to_event(row: Row) -> TaskEvent:
    return TaskEvent(
        id=row["id"],
        task_id=row["task_id"],
        sequence=row["sequence"],
        event_type=row["event_type"],
        from_status=row["from_status"],
        to_status=row["to_status"],
        message=row["message"],
        data=_json_object(row["data_json"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _json_text(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Reliable task data must be valid JSON.") from exc


def _json_object(value: str) -> dict[str, JsonValue]:
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise ReliableTaskStoreError(
            "Reliable task JSON columns must contain objects."
        )
    return decoded


def _datetime_text(value: datetime) -> str:
    return value.isoformat()


def _datetime_from_text(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _status_value(status: ReliableTaskStatus | str) -> str:
    return (
        status.value
        if isinstance(status, ReliableTaskStatus)
        else ReliableTaskStatus(status).value
    )
