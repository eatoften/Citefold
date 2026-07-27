import json
from collections.abc import Sequence
from datetime import datetime
from sqlite3 import Row
from typing import Literal

from .card_generation_run import (
    AutoCardGenerationRequest,
    CardGenerationRun,
    CardGenerationRunError,
)
from .db import connect, ensure_db


CardGenerationReconcilePhase = Literal["running", "final", "canceled"]


def _datetime_to_text(value: datetime | None) -> str | None:
    if value is None:
        return None

    return value.isoformat()


def _datetime_from_text(value: str | None) -> datetime | None:
    if value is None:
        return None

    return datetime.fromisoformat(value)


def _errors_to_json(run: CardGenerationRun) -> str:
    return json.dumps(
        [
            error.model_dump(mode="json")
            for error in run.errors
        ],
        ensure_ascii=False,
    )


def _errors_from_json(value: str) -> list[CardGenerationRunError]:
    raw_errors = json.loads(value)

    if not isinstance(raw_errors, list):
        return []

    return [
        CardGenerationRunError.model_validate(error)
        for error in raw_errors
        if isinstance(error, dict)
    ]


def _request_to_json(run: CardGenerationRun) -> str:
    return run.request.model_dump_json()


def _request_from_json(value: str) -> AutoCardGenerationRequest:
    return AutoCardGenerationRequest.model_validate_json(value)


def _row_to_run(row: Row) -> CardGenerationRun:
    return CardGenerationRun(
        id=row["id"],
        job_id=row["job_id"],
        mode=row["mode"],
        status=row["status"],
        model=row["model"],
        card_count_per_chunk=row["card_count_per_chunk"],
        total_chunks=row["total_chunks"],
        completed_chunks=row["completed_chunks"],
        succeeded_chunks=row["succeeded_chunks"],
        failed_chunks=row["failed_chunks"],
        cards_created=row["cards_created"],
        error_message=row["error_message"],
        errors=_errors_from_json(row["errors_json"]),
        request=_request_from_json(row["request_json"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        started_at=_datetime_from_text(row["started_at"]),
        completed_at=_datetime_from_text(row["completed_at"]),
    )


def create_run(run: CardGenerationRun) -> None:
    ensure_db()

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO card_generation_runs (
                id,
                job_id,
                mode,
                status,
                model,
                card_count_per_chunk,
                total_chunks,
                completed_chunks,
                succeeded_chunks,
                failed_chunks,
                cards_created,
                error_message,
                errors_json,
                request_json,
                created_at,
                updated_at,
                started_at,
                completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.id,
                run.job_id,
                run.mode,
                run.status,
                run.model,
                run.card_count_per_chunk,
                run.total_chunks,
                run.completed_chunks,
                run.succeeded_chunks,
                run.failed_chunks,
                run.cards_created,
                run.error_message,
                _errors_to_json(run),
                _request_to_json(run),
                _datetime_to_text(run.created_at),
                _datetime_to_text(run.updated_at),
                _datetime_to_text(run.started_at),
                _datetime_to_text(run.completed_at),
            ),
        )


def get_run(run_id: str) -> CardGenerationRun | None:
    ensure_db()

    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM card_generation_runs WHERE id = ?",
            (run_id,),
        ).fetchone()

    if row is None:
        return None

    return _row_to_run(row)


def list_runs_for_job(job_id: str) -> list[CardGenerationRun]:
    ensure_db()

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM card_generation_runs
            WHERE job_id = ?
            ORDER BY created_at DESC
            """,
            (job_id,),
        ).fetchall()

    return [
        _row_to_run(row)
        for row in rows
    ]


def update_run(run: CardGenerationRun) -> None:
    ensure_db()

    with connect() as conn:
        conn.execute(
            """
            UPDATE card_generation_runs
            SET status = ?,
                model = ?,
                card_count_per_chunk = ?,
                total_chunks = ?,
                completed_chunks = ?,
                succeeded_chunks = ?,
                failed_chunks = ?,
                cards_created = ?,
                error_message = ?,
                errors_json = ?,
                request_json = ?,
                updated_at = ?,
                started_at = ?,
                completed_at = ?
            WHERE id = ?
            """,
            (
                run.status,
                run.model,
                run.card_count_per_chunk,
                run.total_chunks,
                run.completed_chunks,
                run.succeeded_chunks,
                run.failed_chunks,
                run.cards_created,
                run.error_message,
                _errors_to_json(run),
                _request_to_json(run),
                _datetime_to_text(run.updated_at),
                _datetime_to_text(run.started_at),
                _datetime_to_text(run.completed_at),
                run.id,
            ),
        )


def claim_run_attempt(run_id: str) -> CardGenerationRun | None:
    """Claim one pending/retryable run without reviving an active/completed run."""

    ensure_db()
    now = datetime.now().astimezone()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            UPDATE card_generation_runs
            SET status = 'running',
                error_message = NULL,
                errors_json = '[]',
                updated_at = ?,
                started_at = ?,
                completed_at = NULL
            WHERE id = ?
              AND status IN ('pending', 'failed', 'canceled')
            """,
            (
                _datetime_to_text(now),
                _datetime_to_text(now),
                run_id,
            ),
        )
        if cursor.rowcount != 1:
            return None
        row = conn.execute(
            "SELECT * FROM card_generation_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    return _row_to_run(row) if row is not None else None


def reconcile_run_from_chunk_results(
    run_id: str,
    selected_chunks: Sequence[tuple[str, int]],
    *,
    phase: CardGenerationReconcilePhase,
    failure_message: str | None = None,
) -> CardGenerationRun | None:
    """Atomically derive run counters and terminal state from the chunk ledger."""

    ensure_db()
    chunk_index_by_id = dict(selected_chunks)
    if len(chunk_index_by_id) != len(selected_chunks):
        raise ValueError("Selected chunks must have unique identities.")

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT * FROM card_generation_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if current is None:
            return None

        result_rows: list[Row] = []
        if chunk_index_by_id:
            placeholders = ",".join("?" for _ in chunk_index_by_id)
            result_rows = conn.execute(
                f"""
                SELECT chunk_id, chunk_index, status, cards_created,
                       error_message
                FROM card_generation_chunk_results
                WHERE run_id = ?
                  AND chunk_id IN ({placeholders})
                ORDER BY chunk_index, chunk_id
                """,
                (run_id, *chunk_index_by_id),
            ).fetchall()

        results_by_id = {
            str(row["chunk_id"]): row
            for row in result_rows
        }
        succeeded = [
            row
            for row in result_rows
            if row["status"] == "succeeded"
        ]
        failed = [
            row
            for row in result_rows
            if row["status"] == "failed"
        ]
        total_chunks = len(chunk_index_by_id)
        completed_chunks = len(results_by_id)
        succeeded_chunks = len(succeeded)
        failed_chunks = len(failed)
        cards_created = sum(int(row["cards_created"]) for row in succeeded)
        missing_chunks = total_chunks - completed_chunks
        all_succeeded = succeeded_chunks == total_chunks
        errors = [
            CardGenerationRunError(
                chunk_id=str(row["chunk_id"]),
                chunk_index=int(row["chunk_index"]),
                message=(
                    str(row["error_message"])
                    if row["error_message"] is not None
                    else "Card generation failed for this transcript chunk."
                ),
            )
            for row in failed
        ]

        now = datetime.now().astimezone()
        if current["status"] == "completed":
            status = "completed"
            error_message = None
            errors = []
            completed_at = current["completed_at"] or _datetime_to_text(now)
        elif phase == "running":
            status = "running"
            error_message = None
            completed_at = None
        elif phase == "canceled" and not all_succeeded:
            status = "canceled"
            error_message = "Canceled by the user."
            completed_at = _datetime_to_text(now)
        elif all_succeeded:
            status = "completed"
            error_message = None
            errors = []
            completed_at = _datetime_to_text(now)
        else:
            status = "failed"
            if failure_message:
                error_message = failure_message
            elif failed_chunks:
                error_message = (
                    f"{failed_chunks} of {total_chunks} transcript chunks "
                    "failed. Retry the task to continue."
                )
            else:
                error_message = (
                    f"{missing_chunks} of {total_chunks} transcript chunks "
                    "did not finish. Retry the task to continue."
                )
            completed_at = _datetime_to_text(now)

        updated_at = _datetime_to_text(now)
        conn.execute(
            """
            UPDATE card_generation_runs
            SET status = ?,
                total_chunks = ?,
                completed_chunks = ?,
                succeeded_chunks = ?,
                failed_chunks = ?,
                cards_created = ?,
                error_message = ?,
                errors_json = ?,
                updated_at = ?,
                completed_at = ?
            WHERE id = ?
            """,
            (
                status,
                total_chunks,
                completed_chunks,
                succeeded_chunks,
                failed_chunks,
                cards_created,
                error_message,
                json.dumps(
                    [
                        error.model_dump(mode="json")
                        for error in errors
                    ],
                    ensure_ascii=False,
                ),
                updated_at,
                completed_at,
                run_id,
            ),
        )
        row = conn.execute(
            "SELECT * FROM card_generation_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    return _row_to_run(row) if row is not None else None


def fail_running_run(run_id: str, *, error_message: str) -> None:
    ensure_db()
    now = datetime.now().astimezone()
    with connect() as conn:
        conn.execute(
            """
            UPDATE card_generation_runs
            SET status = 'failed',
                error_message = ?,
                updated_at = ?,
                completed_at = ?
            WHERE id = ? AND status = 'running'
            """,
            (
                error_message,
                _datetime_to_text(now),
                _datetime_to_text(now),
                run_id,
            ),
        )


def cancel_running_run(run_id: str) -> None:
    ensure_db()
    now = datetime.now().astimezone()
    with connect() as conn:
        conn.execute(
            """
            UPDATE card_generation_runs
            SET status = 'canceled',
                error_message = 'Canceled by the user.',
                updated_at = ?,
                completed_at = ?
            WHERE id = ? AND status = 'running'
            """,
            (
                _datetime_to_text(now),
                _datetime_to_text(now),
                run_id,
            ),
        )


def delete_runs_for_job(job_id: str) -> None:
    ensure_db()

    with connect() as conn:
        conn.execute(
            """
            DELETE FROM card_generation_chunk_results
            WHERE run_id IN (
                SELECT id FROM card_generation_runs WHERE job_id = ?
            )
            """,
            (job_id,),
        )
        conn.execute(
            "DELETE FROM card_generation_runs WHERE job_id = ?",
            (job_id,),
        )


def clear_runs() -> None:
    ensure_db()
    with connect() as conn:
        conn.execute("DELETE FROM card_generation_chunk_results")
        conn.execute("DELETE FROM card_generation_runs")


def mark_pending_run_failed(
    run_id: str,
    *,
    error_message: str,
) -> bool:
    ensure_db()
    now = datetime.now().astimezone()
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE card_generation_runs
            SET status = 'failed',
                error_message = ?,
                updated_at = ?,
                completed_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (
                error_message,
                _datetime_to_text(now),
                _datetime_to_text(now),
                run_id,
            ),
        )
    return cursor.rowcount == 1


def recover_active_runs(*, error_message: str) -> list[str]:
    ensure_db()
    now = datetime.now().astimezone()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT id FROM card_generation_runs
            WHERE status IN ('pending', 'running')
            ORDER BY created_at, id
            """
        ).fetchall()
        run_ids = [str(row["id"]) for row in rows]
        if run_ids:
            placeholders = ",".join("?" for _ in run_ids)
            conn.execute(
                f"""
                UPDATE card_generation_runs
                SET status = 'failed',
                    error_message = ?,
                    updated_at = ?,
                    completed_at = ?
                WHERE id IN ({placeholders})
                """,
                (
                    error_message,
                    _datetime_to_text(now),
                    _datetime_to_text(now),
                    *run_ids,
                ),
            )
    return run_ids
