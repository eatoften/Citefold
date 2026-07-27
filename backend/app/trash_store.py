from __future__ import annotations

import json
from collections.abc import Collection
from datetime import datetime, timedelta
from sqlite3 import Connection, Row
from uuid import uuid4

from .db import connect, ensure_db
from .trash import TrashEntityType, TrashItem, TrashItemStatus


DEFAULT_TRASH_RETENTION_DAYS = 30


def _to_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _from_text(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _row_to_trash_item(row: Row) -> TrashItem:
    return TrashItem(
        id=row["id"],
        entity_type=row["entity_type"],
        entity_id=row["entity_id"],
        course_id=row["course_id"],
        display_name=row["display_name"],
        deleted_at=datetime.fromisoformat(row["deleted_at"]),
        purge_after=_from_text(row["purge_after"]),
        status=row["status"],
        metadata=json.loads(row["metadata_json"]),
        restored_at=_from_text(row["restored_at"]),
    )


def put_trash_item(
    conn: Connection,
    *,
    entity_type: TrashEntityType,
    entity_id: str,
    course_id: str | None,
    display_name: str,
    deleted_at: datetime,
    metadata: dict[str, object] | None = None,
    purge_after: datetime | None = None,
) -> str:
    """Insert a tombstone in the same transaction as the root soft delete."""

    item_id = uuid4().hex
    effective_purge_after = purge_after or (
        deleted_at + timedelta(days=DEFAULT_TRASH_RETENTION_DAYS)
    )
    conn.execute(
        """
        INSERT INTO trash_items (
            id, entity_type, entity_id, course_id, display_name,
            deleted_at, purge_after, status, metadata_json, restored_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'trashed', ?, NULL)
        ON CONFLICT(entity_type, entity_id) DO UPDATE SET
            course_id = excluded.course_id,
            display_name = excluded.display_name,
            deleted_at = excluded.deleted_at,
            purge_after = excluded.purge_after,
            status = 'trashed',
            metadata_json = excluded.metadata_json,
            restored_at = NULL
        """,
        (
            item_id,
            entity_type,
            entity_id,
            course_id,
            display_name,
            _to_text(deleted_at),
            _to_text(effective_purge_after),
            json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )
    row = conn.execute(
        """
        SELECT id FROM trash_items
        WHERE entity_type = ? AND entity_id = ?
        """,
        (entity_type, entity_id),
    ).fetchone()
    if row is None:
        raise RuntimeError("Trash tombstone was not persisted.")
    return str(row["id"])


def remove_trash_item_for_entity(
    conn: Connection,
    *,
    entity_type: TrashEntityType,
    entity_id: str,
) -> None:
    conn.execute(
        """
        DELETE FROM trash_items
        WHERE entity_type = ? AND entity_id = ?
        """,
        (entity_type, entity_id),
    )


def get_trash_item(item_id: str) -> TrashItem | None:
    ensure_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM trash_items WHERE id = ?",
            (item_id,),
        ).fetchone()
    return _row_to_trash_item(row) if row is not None else None


def get_trash_item_for_entity(
    entity_type: TrashEntityType,
    entity_id: str,
) -> TrashItem | None:
    ensure_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM trash_items
            WHERE entity_type = ? AND entity_id = ?
            """,
            (entity_type, entity_id),
        ).fetchone()
    return _row_to_trash_item(row) if row is not None else None


def list_trash_items(
    *,
    course_id: str | None = None,
) -> list[TrashItem]:
    ensure_db()
    where = ""
    parameters: tuple[object, ...] = ()
    if course_id is not None:
        where = "WHERE course_id = ?"
        parameters = (course_id,)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM trash_items
            {where}
            ORDER BY deleted_at DESC, id
            """,
            parameters,
        ).fetchall()
    return [_row_to_trash_item(row) for row in rows]


def set_trash_item_status(
    item_id: str,
    status: TrashItemStatus,
) -> bool:
    ensure_db()
    with connect() as conn:
        cursor = conn.execute(
            "UPDATE trash_items SET status = ? WHERE id = ?",
            (status, item_id),
        )
    return cursor.rowcount == 1


def compare_and_set_trash_item_status(
    item_id: str,
    *,
    expected_statuses: Collection[TrashItemStatus],
    status: TrashItemStatus,
) -> TrashItem | None:
    """Claim or transition a trash item only from an expected state."""

    allowed = tuple(dict.fromkeys(expected_statuses))
    if not allowed:
        raise ValueError("At least one expected trash status is required.")
    ensure_db()
    placeholders = ",".join("?" for _ in allowed)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            f"""
            UPDATE trash_items
            SET status = ?
            WHERE id = ? AND status IN ({placeholders})
            """,
            (status, item_id, *allowed),
        )
        if cursor.rowcount != 1:
            return None
        row = conn.execute(
            "SELECT * FROM trash_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Claimed trash item disappeared.")
    return _row_to_trash_item(row)


def update_claimed_trash_item_metadata(
    item_id: str,
    *,
    expected_status: TrashItemStatus,
    metadata: dict[str, object],
) -> TrashItem | None:
    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            UPDATE trash_items
            SET metadata_json = ?
            WHERE id = ? AND status = ?
            """,
            (
                json.dumps(metadata, ensure_ascii=False),
                item_id,
                expected_status,
            ),
        )
        if cursor.rowcount != 1:
            return None
        row = conn.execute(
            "SELECT * FROM trash_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Updated trash item disappeared.")
    return _row_to_trash_item(row)


def remove_claimed_trash_item(
    item_id: str,
    *,
    expected_status: TrashItemStatus,
) -> bool:
    """Finalize a centrally managed operation without deleting another claim."""

    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "DELETE FROM trash_items WHERE id = ? AND status = ?",
            (item_id, expected_status),
        )
    return cursor.rowcount == 1


def recover_interrupted_trash_operations() -> list[TrashItem]:
    """Release operation claims left behind by an interrupted process.

    A restore has not crossed an irreversible boundary, so it becomes a
    retryable restore failure. A purge may already have completed one or more
    persisted phases and therefore becomes a retryable purge failure. Only the
    status changes: metadata, including any durable purge plan, is preserved.
    """

    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT id
            FROM trash_items
            WHERE status IN ('restoring', 'purging')
            ORDER BY deleted_at, id
            """
        ).fetchall()
        recovered_ids = [str(row["id"]) for row in rows]
        if not recovered_ids:
            return []

        conn.execute(
            """
            UPDATE trash_items
            SET status = CASE status
                WHEN 'restoring' THEN 'restore_failed'
                WHEN 'purging' THEN 'purge_failed'
                ELSE status
            END
            WHERE status IN ('restoring', 'purging')
            """
        )
        placeholders = ",".join("?" for _ in recovered_ids)
        recovered_rows = conn.execute(
            f"""
            SELECT *
            FROM trash_items
            WHERE id IN ({placeholders})
            ORDER BY deleted_at, id
            """,
            recovered_ids,
        ).fetchall()
    return [_row_to_trash_item(row) for row in recovered_rows]


def clear_trash_items() -> None:
    ensure_db()
    with connect() as conn:
        conn.execute("DELETE FROM trash_items")
