from __future__ import annotations

from sqlite3 import Connection


def create_reliable_task_tables(conn: Connection) -> None:
    """Create the durable task tables without importing the database module."""

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reliable_tasks (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            course_id TEXT,
            resource_type TEXT,
            resource_id TEXT,
            status TEXT NOT NULL CHECK (
                status IN (
                    'queued',
                    'running',
                    'canceling',
                    'succeeded',
                    'failed',
                    'canceled'
                )
            ),
            payload_json TEXT NOT NULL,
            result_json TEXT,
            request_fingerprint TEXT NOT NULL,
            idempotency_key TEXT,
            active_key TEXT,
            priority INTEGER NOT NULL DEFAULT 0,
            attempt INTEGER NOT NULL DEFAULT 1,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            recovery_count INTEGER NOT NULL DEFAULT 0,
            progress_json TEXT NOT NULL DEFAULT '{}',
            cancel_requested_at TEXT,
            worker_id TEXT,
            claim_token TEXT,
            error_code TEXT,
            error_message TEXT,
            retryable INTEGER NOT NULL DEFAULT 1,
            available_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            heartbeat_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_reliable_tasks_runnable
        ON reliable_tasks (
            status,
            available_at,
            priority DESC,
            created_at
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_reliable_tasks_course
        ON reliable_tasks (course_id, updated_at DESC)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_reliable_tasks_idempotency
        ON reliable_tasks (kind, idempotency_key)
        WHERE idempotency_key IS NOT NULL
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_reliable_tasks_active
        ON reliable_tasks (active_key)
        WHERE active_key IS NOT NULL
          AND status IN ('queued', 'running', 'canceling')
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reliable_task_events (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            from_status TEXT,
            to_status TEXT,
            message TEXT,
            data_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE (task_id, sequence)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_reliable_task_events_task
        ON reliable_task_events (task_id, sequence)
        """
    )
