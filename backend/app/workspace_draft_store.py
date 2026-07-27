from __future__ import annotations

import json
from datetime import datetime
from sqlite3 import IntegrityError, Row

from .db import connect, ensure_db
from .job import utc_now
from .workspace_draft import WorkspaceDraft, WorkspaceDraftPut


class DraftRevisionConflictError(Exception):
    def __init__(self, current: WorkspaceDraft | None) -> None:
        super().__init__("The draft changed after this editor loaded it.")
        self.current = current


def _to_text(value: datetime) -> str:
    return value.isoformat()


def _row_to_draft(row: Row) -> WorkspaceDraft:
    payload = json.loads(row["payload_json"])
    if not isinstance(payload, dict):
        payload = {}
    return WorkspaceDraft(
        id=str(row["id"]),
        course_id=str(row["course_id"]),
        draft_type=str(row["draft_type"]),
        entity_id=(
            str(row["entity_id"])
            if row["entity_id"] is not None
            else None
        ),
        payload=payload,
        revision=int(row["revision"]),
        base_updated_at=(
            str(row["base_updated_at"])
            if row["base_updated_at"] is not None
            else None
        ),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )


def get_draft(draft_id: str) -> WorkspaceDraft | None:
    ensure_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM workspace_drafts WHERE id = ?",
            (draft_id,),
        ).fetchone()
    return _row_to_draft(row) if row is not None else None


def list_drafts(
    *,
    course_id: str | None = None,
    draft_type: str | None = None,
) -> list[WorkspaceDraft]:
    ensure_db()
    clauses: list[str] = []
    parameters: list[object] = []
    if course_id is not None:
        clauses.append("course_id = ?")
        parameters.append(course_id)
    if draft_type is not None:
        clauses.append("draft_type = ?")
        parameters.append(draft_type)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM workspace_drafts
            {where}
            ORDER BY updated_at DESC, id ASC
            """,
            parameters,
        ).fetchall()
    return [_row_to_draft(row) for row in rows]


def put_draft(
    draft_id: str,
    request: WorkspaceDraftPut,
    *,
    now: datetime | None = None,
) -> WorkspaceDraft:
    ensure_db()
    current_time = now or utc_now()
    payload_json = json.dumps(
        request.payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM workspace_drafts WHERE id = ?",
            (draft_id,),
        ).fetchone()
        current = _row_to_draft(row) if row is not None else None
        if current is None:
            if request.expected_revision not in (None, 0):
                raise DraftRevisionConflictError(None)
            try:
                conn.execute(
                    """
                    INSERT INTO workspace_drafts (
                        id, course_id, draft_type, entity_id, payload_json,
                        revision, base_updated_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        draft_id,
                        request.course_id,
                        request.draft_type,
                        request.entity_id,
                        payload_json,
                        request.base_updated_at,
                        _to_text(current_time),
                        _to_text(current_time),
                    ),
                )
            except IntegrityError as exc:
                latest = conn.execute(
                    "SELECT * FROM workspace_drafts WHERE id = ?",
                    (draft_id,),
                ).fetchone()
                raise DraftRevisionConflictError(
                    _row_to_draft(latest) if latest is not None else None
                ) from exc
        else:
            if (
                request.course_id != current.course_id
                or request.draft_type != current.draft_type
                or request.entity_id != current.entity_id
            ):
                raise DraftRevisionConflictError(current)
            expected = request.expected_revision
            if expected is not None and expected != current.revision:
                raise DraftRevisionConflictError(current)
            next_revision = current.revision + 1
            cursor = conn.execute(
                """
                UPDATE workspace_drafts
                SET payload_json = ?, revision = ?, base_updated_at = ?,
                    updated_at = ?
                WHERE id = ? AND revision = ?
                """,
                (
                    payload_json,
                    next_revision,
                    request.base_updated_at,
                    _to_text(current_time),
                    draft_id,
                    current.revision,
                ),
            )
            if cursor.rowcount != 1:
                latest = conn.execute(
                    "SELECT * FROM workspace_drafts WHERE id = ?",
                    (draft_id,),
                ).fetchone()
                raise DraftRevisionConflictError(
                    _row_to_draft(latest) if latest is not None else None
                )
        saved = conn.execute(
            "SELECT * FROM workspace_drafts WHERE id = ?",
            (draft_id,),
        ).fetchone()
    if saved is None:
        raise RuntimeError("Draft write completed without a persisted row.")
    return _row_to_draft(saved)


def delete_draft(
    draft_id: str,
    *,
    expected_revision: int | None = None,
) -> bool:
    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM workspace_drafts WHERE id = ?",
            (draft_id,),
        ).fetchone()
        if row is None:
            return False
        current = _row_to_draft(row)
        if (
            expected_revision is not None
            and current.revision != expected_revision
        ):
            raise DraftRevisionConflictError(current)
        cursor = conn.execute(
            "DELETE FROM workspace_drafts WHERE id = ?",
            (draft_id,),
        )
    return cursor.rowcount == 1


def clear_drafts() -> None:
    ensure_db()
    with connect() as conn:
        conn.execute("DELETE FROM workspace_drafts")
