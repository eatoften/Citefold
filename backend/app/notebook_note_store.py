from __future__ import annotations

import hashlib
import json
from datetime import datetime
from sqlite3 import Connection, IntegrityError, Row
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from .course_source import source_id_for_note
from .course_source_store import (
    delete_source_projection_in_connection,
    get_source,
    replace_source_projection_in_connection,
)
from .db import connect, ensure_db
from .job import utc_now
from .notebook_note import (
    ChatAnswerNotebookNoteOriginSnapshot,
    FreeNotebookNoteOriginSnapshot,
    NotebookNote,
    NotebookNoteCitationSnapshot,
    NotebookNoteCitationSpan,
    NotebookNoteOriginSnapshot,
    NotebookNoteSourceSnapshot,
    NotebookNoteSummary,
)
from .notebook_note_source import build_note_source_projection
from .trash_store import put_trash_item, remove_trash_item_for_entity


_ORIGIN_ADAPTER = TypeAdapter(NotebookNoteOriginSnapshot)


class NotebookNoteStoreError(RuntimeError):
    pass


class NotebookNoteRevisionConflictError(NotebookNoteStoreError):
    def __init__(self, current: NotebookNote | None) -> None:
        super().__init__("This note was updated by another editor.")
        self.current = current


class NotebookNoteScopeError(NotebookNoteStoreError):
    pass


class NotebookNoteCaptureError(NotebookNoteStoreError):
    pass


class NotebookNoteDeletedCaptureError(NotebookNoteStoreError):
    pass


def _datetime_text(value: datetime) -> str:
    return value.isoformat()


def _datetime_from_text(value: object) -> datetime:
    return datetime.fromisoformat(str(value))


def _json_object(value: object) -> dict[str, object]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError) as exc:
        raise NotebookNoteStoreError("Stored note JSON is invalid.") from exc
    if not isinstance(parsed, dict):
        raise NotebookNoteStoreError("Stored note JSON is invalid.")
    return parsed


def _row_to_note(row: Row) -> NotebookNote:
    try:
        origin = _ORIGIN_ADAPTER.validate_python(
            _json_object(row["origin_snapshot_json"])
        )
    except ValidationError as exc:
        raise NotebookNoteStoreError(
            "Stored note provenance is invalid."
        ) from exc
    published_revision = (
        int(row["published_revision"])
        if row["published_revision"] is not None
        else None
    )
    revision = int(row["revision"])
    return NotebookNote(
        id=str(row["id"]),
        course_id=str(row["course_id"]),
        title=str(row["title"]),
        body_markdown=str(row["body_markdown"]),
        revision=revision,
        origin_type=str(row["origin_type"]),
        origin_snapshot=origin,
        published_snapshot_id=(
            str(row["published_snapshot_id"])
            if row["published_snapshot_id"] is not None
            else None
        ),
        published_revision=published_revision,
        is_source_outdated=(
            published_revision is not None and published_revision != revision
        ),
        created_at=_datetime_from_text(row["created_at"]),
        updated_at=_datetime_from_text(row["updated_at"]),
    )


def _row_to_summary(row: Row) -> NotebookNoteSummary:
    published_revision = (
        int(row["published_revision"])
        if row["published_revision"] is not None
        else None
    )
    revision = int(row["revision"])
    return NotebookNoteSummary(
        id=str(row["id"]),
        course_id=str(row["course_id"]),
        title=str(row["title"]),
        body_preview=_body_preview(str(row["body_markdown"])),
        revision=revision,
        origin_type=str(row["origin_type"]),
        citation_count=int(row["citation_count"]),
        published_snapshot_id=(
            str(row["published_snapshot_id"])
            if row["published_snapshot_id"] is not None
            else None
        ),
        published_revision=published_revision,
        is_source_outdated=(
            published_revision is not None and published_revision != revision
        ),
        created_at=_datetime_from_text(row["created_at"]),
        updated_at=_datetime_from_text(row["updated_at"]),
    )


def _row_to_snapshot(row: Row) -> NotebookNoteSourceSnapshot:
    return NotebookNoteSourceSnapshot(
        id=str(row["id"]),
        note_id=str(row["note_id"]),
        course_id=str(row["course_id"]),
        note_revision=int(row["note_revision"]),
        title=str(row["title"]),
        body_markdown=str(row["body_markdown"]),
        content_hash=str(row["content_hash"]),
        created_at=_datetime_from_text(row["created_at"]),
    )


_NOTE_SELECT = """
SELECT
    notes.*,
    snapshots.id AS published_snapshot_id,
    snapshots.note_revision AS published_revision
FROM notebook_notes AS notes
LEFT JOIN notebook_note_source_snapshots AS snapshots
    ON snapshots.note_id = notes.id
    AND snapshots.note_revision = (
        SELECT MAX(latest.note_revision)
        FROM notebook_note_source_snapshots AS latest
        WHERE latest.note_id = notes.id
    )
"""


def _get_note(
    conn: Connection,
    course_id: str,
    note_id: str,
    *,
    include_deleted: bool,
    include_deleted_course: bool = False,
) -> NotebookNote | None:
    note_clause = "" if include_deleted else "AND notes.deleted_at IS NULL"
    course_clause = (
        "" if include_deleted_course else "AND courses.deleted_at IS NULL"
    )
    row = conn.execute(
        f"""
        {_NOTE_SELECT}
        INNER JOIN courses ON courses.id = notes.course_id
        WHERE notes.id = ? AND notes.course_id = ?
          {note_clause}
          {course_clause}
        """,
        (note_id, course_id),
    ).fetchone()
    return _row_to_note(row) if row is not None else None


def get_notebook_note(
    course_id: str,
    note_id: str,
    *,
    include_deleted: bool = False,
    include_deleted_course: bool = False,
) -> NotebookNote | None:
    ensure_db()
    with connect() as conn:
        return _get_note(
            conn,
            course_id,
            note_id,
            include_deleted=include_deleted,
            include_deleted_course=include_deleted_course,
        )


def get_notebook_note_course_id(note_id: str) -> str | None:
    ensure_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT course_id FROM notebook_notes WHERE id = ?",
            (note_id,),
        ).fetchone()
    return str(row["course_id"]) if row is not None else None


def list_notebook_notes(
    course_id: str,
    *,
    limit: int = 1000,
    offset: int = 0,
) -> list[NotebookNoteSummary]:
    ensure_db()
    with connect() as conn:
        _require_active_course(conn, course_id)
        rows = conn.execute(
            """
            SELECT
                notes.*,
                snapshots.id AS published_snapshot_id,
                snapshots.note_revision AS published_revision,
                COUNT(citations.id) AS citation_count
            FROM notebook_notes AS notes
            INNER JOIN courses ON courses.id = notes.course_id
            LEFT JOIN notebook_note_source_snapshots AS snapshots
                ON snapshots.note_id = notes.id
                AND snapshots.note_revision = (
                    SELECT MAX(latest.note_revision)
                    FROM notebook_note_source_snapshots AS latest
                    WHERE latest.note_id = notes.id
                )
            LEFT JOIN notebook_note_citations AS citations
                ON citations.note_id = notes.id
            WHERE notes.course_id = ?
              AND notes.deleted_at IS NULL
              AND courses.deleted_at IS NULL
            GROUP BY notes.id
            ORDER BY notes.updated_at DESC, notes.id
            LIMIT ? OFFSET ?
            """,
            (course_id, limit, offset),
        ).fetchall()
    return [_row_to_summary(row) for row in rows]


def create_free_notebook_note(note: NotebookNote) -> NotebookNote:
    ensure_db()
    if note.origin_type != "free" or not isinstance(
        note.origin_snapshot,
        FreeNotebookNoteOriginSnapshot,
    ):
        raise ValueError("Free note provenance is invalid.")
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _require_active_course(conn, note.course_id)
        _insert_note(conn, note)
        saved = _get_note(
            conn,
            note.course_id,
            note.id,
            include_deleted=False,
        )
    if saved is None:
        raise NotebookNoteStoreError("Saved note could not be reloaded.")
    return saved


def capture_chat_answer(
    course_id: str,
    message_id: str,
    *,
    note_id: str,
    title: str | None,
) -> tuple[NotebookNote, bool]:
    """Capture one grounded answer and its evidence in one transaction."""

    ensure_db()
    now = utc_now()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _require_active_course(conn, course_id)
        existing_row = conn.execute(
            """
            SELECT id, course_id, deleted_at
            FROM notebook_notes
            WHERE origin_type = 'chat_answer' AND origin_message_id = ?
            """,
            (message_id,),
        ).fetchone()
        if existing_row is not None:
            if str(existing_row["course_id"]) != course_id:
                raise NotebookNoteScopeError("Chat message was not found.")
            if existing_row["deleted_at"] is not None:
                raise NotebookNoteDeletedCaptureError(
                    "This answer is already saved in Trash. Restore that note."
                )
            existing = _get_note(
                conn,
                course_id,
                str(existing_row["id"]),
                include_deleted=False,
            )
            if existing is None:
                raise NotebookNoteStoreError(
                    "Saved Chat note could not be reloaded."
                )
            return existing, True

        message = conn.execute(
            """
            SELECT
                messages.*,
                conversations.title AS conversation_title,
                conversations.course_id AS course_id
            FROM chat_messages AS messages
            INNER JOIN chat_conversations AS conversations
                ON conversations.id = messages.conversation_id
            INNER JOIN courses ON courses.id = conversations.course_id
            WHERE messages.id = ?
              AND conversations.course_id = ?
              AND conversations.deleted_at IS NULL
              AND courses.deleted_at IS NULL
            """,
            (message_id, course_id),
        ).fetchone()
        if message is None:
            raise NotebookNoteScopeError("Chat message was not found.")
        if (
            str(message["role"]) != "assistant"
            or str(message["status"]) != "complete"
            or str(message["answer_status"]) != "answered"
        ):
            raise NotebookNoteCaptureError(
                "Only a completed grounded answer can be saved as a note."
            )

        answer_text = str(message["content"])
        citation_rows = conn.execute(
            """
            SELECT *
            FROM chat_citations
            WHERE message_id = ?
            ORDER BY ordinal, id
            """,
            (message_id,),
        ).fetchall()
        if not citation_rows:
            raise NotebookNoteCaptureError(
                "A grounded answer must have at least one citation."
            )
        snapshots: list[NotebookNoteCitationSnapshot] = []
        normalized_rows: list[tuple[Row, NotebookNoteCitationSnapshot]] = []
        for citation_row in citation_rows:
            span_rows = conn.execute(
                """
                SELECT sentence_index, start_offset, end_offset
                FROM chat_citation_spans
                WHERE message_id = ? AND citation_id = ?
                ORDER BY sentence_index, start_offset, end_offset, id
                """,
                (message_id, citation_row["id"]),
            ).fetchall()
            if not span_rows:
                raise NotebookNoteCaptureError(
                    "A grounded citation is missing its sentence span."
                )
            snapshot = NotebookNoteCitationSnapshot(
                id=uuid4().hex,
                origin_citation_id=str(citation_row["id"]),
                ordinal=int(citation_row["ordinal"]),
                source_id=str(citation_row["source_id"]),
                chunk_id=str(citation_row["chunk_id"]),
                chunk_text_hash=str(citation_row["chunk_text_hash"]),
                source_title=str(citation_row["source_title"]),
                source_type=str(citation_row["source_type"]),
                quote=str(citation_row["quote"]),
                score=float(citation_row["score"]),
                locator=_json_object(citation_row["locator_json"]),
                spans=[
                    NotebookNoteCitationSpan(
                        sentence_index=int(span_row["sentence_index"]),
                        start_offset=int(span_row["start_offset"]),
                        end_offset=int(span_row["end_offset"]),
                    )
                    for span_row in span_rows
                ],
            )
            if any(
                span.end_offset > len(answer_text)
                for span in snapshot.spans
            ):
                raise NotebookNoteCaptureError(
                    "A grounded citation exceeds the saved answer."
                )
            snapshots.append(snapshot)
            normalized_rows.append((citation_row, snapshot))

        conversation_id = str(message["conversation_id"])
        origin = ChatAnswerNotebookNoteOriginSnapshot(
            conversation_id=conversation_id,
            message_id=message_id,
            answer_text=answer_text,
            provider=(
                str(message["provider"])
                if message["provider"] is not None
                else None
            ),
            model=(
                str(message["model"])
                if message["model"] is not None
                else None
            ),
            citations=snapshots,
        )
        note = NotebookNote(
            id=note_id,
            course_id=course_id,
            title=title or str(message["conversation_title"]),
            body_markdown=answer_text,
            revision=1,
            origin_type="chat_answer",
            origin_snapshot=origin,
            created_at=now,
            updated_at=now,
        )
        _insert_note(conn, note)
        for citation_row, snapshot in normalized_rows:
            conn.execute(
                """
                INSERT INTO notebook_note_citations (
                    id, note_id, note_revision, origin_citation_id, ordinal,
                    source_id, chunk_id, chunk_text_hash, source_title,
                    source_type, quote, score, locator_json, created_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.id,
                    note.id,
                    snapshot.origin_citation_id,
                    snapshot.ordinal,
                    snapshot.source_id,
                    snapshot.chunk_id,
                    snapshot.chunk_text_hash,
                    snapshot.source_title,
                    snapshot.source_type,
                    snapshot.quote,
                    snapshot.score,
                    json.dumps(snapshot.locator, ensure_ascii=False),
                    _datetime_text(now),
                ),
            )
            for span in snapshot.spans:
                conn.execute(
                    """
                    INSERT INTO notebook_note_citation_spans (
                        id, note_id, citation_id, sentence_index,
                        start_offset, end_offset, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid4().hex,
                        note.id,
                        snapshot.id,
                        span.sentence_index,
                        span.start_offset,
                        span.end_offset,
                        _datetime_text(now),
                    ),
                )
        saved = _get_note(
            conn,
            course_id,
            note.id,
            include_deleted=False,
        )
    if saved is None:
        raise NotebookNoteStoreError("Saved Chat note could not be reloaded.")
    return saved, False


def update_notebook_note(
    course_id: str,
    note_id: str,
    *,
    expected_revision: int,
    title: str | None,
    body_markdown: str | None,
) -> NotebookNote | None:
    ensure_db()
    now = utc_now()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = _get_note(
            conn,
            course_id,
            note_id,
            include_deleted=False,
        )
        if current is None:
            return None
        if current.revision != expected_revision:
            raise NotebookNoteRevisionConflictError(current)
        next_title = title if title is not None else current.title
        next_body = (
            body_markdown if body_markdown is not None else current.body_markdown
        )
        if next_title == current.title and next_body == current.body_markdown:
            return current
        cursor = conn.execute(
            """
            UPDATE notebook_notes
            SET title = ?, body_markdown = ?, revision = revision + 1,
                updated_at = ?
            WHERE id = ? AND course_id = ? AND revision = ?
              AND deleted_at IS NULL
            """,
            (
                next_title,
                next_body,
                _datetime_text(now),
                note_id,
                course_id,
                expected_revision,
            ),
        )
        if cursor.rowcount != 1:
            latest = _get_note(
                conn,
                course_id,
                note_id,
                include_deleted=False,
            )
            raise NotebookNoteRevisionConflictError(latest)
        saved = _get_note(
            conn,
            course_id,
            note_id,
            include_deleted=False,
        )
    if saved is None:
        raise NotebookNoteStoreError("Updated note could not be reloaded.")
    return saved


def promote_notebook_note(
    course_id: str,
    note_id: str,
    *,
    expected_revision: int,
    snapshot_id: str,
    now: datetime | None = None,
):
    """Snapshot the exact revision and replace its Source in one transaction."""

    ensure_db()
    created_at = now or utc_now()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = _get_note(
            conn,
            course_id,
            note_id,
            include_deleted=False,
        )
        if current is None:
            return None
        if current.revision != expected_revision:
            raise NotebookNoteRevisionConflictError(current)
        existing_row = conn.execute(
            """
            SELECT *
            FROM notebook_note_source_snapshots
            WHERE note_id = ? AND note_revision = ?
            """,
            (note_id, expected_revision),
        ).fetchone()
        replayed = existing_row is not None
        if existing_row is None:
            snapshot = NotebookNoteSourceSnapshot(
                id=snapshot_id,
                note_id=note_id,
                course_id=course_id,
                note_revision=current.revision,
                title=current.title,
                body_markdown=current.body_markdown,
                content_hash=hashlib.sha256(
                    current.body_markdown.encode("utf-8")
                ).hexdigest(),
                created_at=created_at,
            )
            conn.execute(
                """
                INSERT INTO notebook_note_source_snapshots (
                    id, note_id, course_id, note_revision, title,
                    body_markdown, content_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.id,
                    snapshot.note_id,
                    snapshot.course_id,
                    snapshot.note_revision,
                    snapshot.title,
                    snapshot.body_markdown,
                    snapshot.content_hash,
                    _datetime_text(snapshot.created_at),
                ),
            )
        else:
            snapshot = _row_to_snapshot(existing_row)
        source, chunks = build_note_source_projection(current, snapshot)
        replace_source_projection_in_connection(conn, source, chunks)
        saved = _get_note(
            conn,
            course_id,
            note_id,
            include_deleted=False,
        )
    if saved is None:
        raise NotebookNoteStoreError("Published note could not be reloaded.")
    canonical_source = get_source(source.id)
    if canonical_source is None:
        raise NotebookNoteStoreError(
            "Published Source could not be reloaded."
        )
    return saved, snapshot, canonical_source, replayed


def soft_delete_notebook_note(
    course_id: str,
    note_id: str,
    *,
    expected_revision: int,
) -> NotebookNote | None:
    ensure_db()
    deleted_at = utc_now()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = _get_note(
            conn,
            course_id,
            note_id,
            include_deleted=False,
        )
        if current is None:
            return None
        if current.revision != expected_revision:
            raise NotebookNoteRevisionConflictError(current)
        cursor = conn.execute(
            """
            UPDATE notebook_notes
            SET deleted_at = ?, updated_at = ?
            WHERE id = ? AND course_id = ? AND revision = ?
              AND deleted_at IS NULL
            """,
            (
                _datetime_text(deleted_at),
                _datetime_text(deleted_at),
                note_id,
                course_id,
                expected_revision,
            ),
        )
        if cursor.rowcount != 1:
            latest = _get_note(
                conn,
                course_id,
                note_id,
                include_deleted=False,
            )
            raise NotebookNoteRevisionConflictError(latest)
        put_trash_item(
            conn,
            entity_type="notebook_note",
            entity_id=note_id,
            course_id=course_id,
            display_name=current.title,
            deleted_at=deleted_at,
            metadata={
                "revision": current.revision,
                "source_id": (
                    source_id_for_note(note_id)
                    if current.published_snapshot_id is not None
                    else None
                ),
            },
        )
    return current


def restore_notebook_note(course_id: str, note_id: str) -> NotebookNote | None:
    ensure_db()
    now = utc_now()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT 1
            FROM notebook_notes AS notes
            INNER JOIN courses ON courses.id = notes.course_id
            WHERE notes.id = ? AND notes.course_id = ?
              AND notes.deleted_at IS NOT NULL
              AND courses.deleted_at IS NULL
            """,
            (note_id, course_id),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            """
            UPDATE notebook_notes
            SET deleted_at = NULL, updated_at = ?
            WHERE id = ? AND course_id = ? AND deleted_at IS NOT NULL
            """,
            (_datetime_text(now), note_id, course_id),
        )
        remove_trash_item_for_entity(
            conn,
            entity_type="notebook_note",
            entity_id=note_id,
        )
        restored = _get_note(
            conn,
            course_id,
            note_id,
            include_deleted=False,
        )
    return restored


def purge_notebook_note(
    course_id: str,
    note_id: str,
    *,
    allow_parent_deleted: bool = False,
) -> bool:
    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT notes.deleted_at AS note_deleted_at,
                   courses.deleted_at AS course_deleted_at
            FROM notebook_notes AS notes
            INNER JOIN courses ON courses.id = notes.course_id
            WHERE notes.id = ? AND notes.course_id = ?
            """,
            (note_id, course_id),
        ).fetchone()
        if row is None:
            return False
        if row["note_deleted_at"] is None and not (
            allow_parent_deleted and row["course_deleted_at"] is not None
        ):
            return False
        _purge_note_rows(conn, course_id, note_id)
    return True


def purge_notebook_notes_for_course(course_id: str) -> None:
    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT id FROM notebook_notes WHERE course_id = ?",
            (course_id,),
        ).fetchall()
        for row in rows:
            _purge_note_rows(conn, course_id, str(row["id"]))
        conn.execute(
            "DELETE FROM workspace_drafts WHERE course_id = ?",
            (course_id,),
        )


def _purge_note_rows(
    conn: Connection,
    course_id: str,
    note_id: str,
) -> None:
    delete_source_projection_in_connection(conn, source_id_for_note(note_id))
    conn.execute(
        "DELETE FROM notebook_note_citation_spans WHERE note_id = ?",
        (note_id,),
    )
    conn.execute(
        "DELETE FROM notebook_note_citations WHERE note_id = ?",
        (note_id,),
    )
    conn.execute(
        "DELETE FROM notebook_note_source_snapshots WHERE note_id = ?",
        (note_id,),
    )
    canonical_draft_ids = (
        f"notebook-note:{note_id}",
        f"notebook-note-editor:{note_id}",
        f"note-editor:{note_id}",
    )
    conn.execute(
        """
        DELETE FROM workspace_drafts
        WHERE course_id = ?
          AND (
              entity_id = ?
              OR id IN (?, ?, ?)
          )
        """,
        (course_id, note_id, *canonical_draft_ids),
    )
    conn.execute(
        """
        DELETE FROM trash_items
        WHERE entity_type = 'notebook_note' AND entity_id = ?
        """,
        (note_id,),
    )
    conn.execute(
        "DELETE FROM notebook_notes WHERE id = ? AND course_id = ?",
        (note_id, course_id),
    )


def list_published_notebook_notes_for_course(
    course_id: str,
    *,
    include_deleted: bool,
) -> list[tuple[NotebookNote, NotebookNoteSourceSnapshot]]:
    ensure_db()
    deleted_clause = "" if include_deleted else "AND notes.deleted_at IS NULL"
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT
                notes.*,
                snapshots.id AS published_snapshot_id,
                snapshots.note_revision AS published_revision,
                snapshots.id AS snapshot_id,
                snapshots.note_id AS snapshot_note_id,
                snapshots.course_id AS snapshot_course_id,
                snapshots.title AS snapshot_title,
                snapshots.body_markdown AS snapshot_body_markdown,
                snapshots.content_hash AS snapshot_content_hash,
                snapshots.created_at AS snapshot_created_at
            FROM notebook_notes AS notes
            INNER JOIN notebook_note_source_snapshots AS snapshots
                ON snapshots.note_id = notes.id
                AND snapshots.note_revision = (
                    SELECT MAX(latest.note_revision)
                    FROM notebook_note_source_snapshots AS latest
                    WHERE latest.note_id = notes.id
                )
            WHERE notes.course_id = ?
              {deleted_clause}
            ORDER BY notes.id
            """,
            (course_id,),
        ).fetchall()
    results: list[tuple[NotebookNote, NotebookNoteSourceSnapshot]] = []
    for row in rows:
        results.append(
            (
                _row_to_note(row),
                NotebookNoteSourceSnapshot(
                    id=str(row["snapshot_id"]),
                    note_id=str(row["snapshot_note_id"]),
                    course_id=str(row["snapshot_course_id"]),
                    note_revision=int(row["published_revision"]),
                    title=str(row["snapshot_title"]),
                    body_markdown=str(row["snapshot_body_markdown"]),
                    content_hash=str(row["snapshot_content_hash"]),
                    created_at=_datetime_from_text(
                        row["snapshot_created_at"]
                    ),
                ),
            )
        )
    return results


def get_note_source_snapshot(
    course_id: str,
    note_id: str,
    snapshot_id: str,
    *,
    require_active_note: bool = True,
) -> NotebookNoteSourceSnapshot | None:
    ensure_db()
    active_clause = (
        "AND notes.deleted_at IS NULL AND courses.deleted_at IS NULL"
        if require_active_note
        else ""
    )
    with connect() as conn:
        row = conn.execute(
            f"""
            SELECT snapshots.*
            FROM notebook_note_source_snapshots AS snapshots
            INNER JOIN notebook_notes AS notes ON notes.id = snapshots.note_id
            INNER JOIN courses ON courses.id = notes.course_id
            WHERE snapshots.id = ?
              AND snapshots.note_id = ?
              AND snapshots.course_id = ?
              AND notes.course_id = ?
              {active_clause}
            """,
            (snapshot_id, note_id, course_id, course_id),
        ).fetchone()
    return _row_to_snapshot(row) if row is not None else None


def clear_notebook_notes() -> None:
    ensure_db()
    with connect() as conn:
        source_rows = conn.execute(
            "SELECT id FROM sources WHERE origin_type = 'notebook_note'"
        ).fetchall()
        for row in source_rows:
            delete_source_projection_in_connection(conn, str(row["id"]))
        conn.execute("DELETE FROM notebook_note_citation_spans")
        conn.execute("DELETE FROM notebook_note_citations")
        conn.execute("DELETE FROM notebook_note_source_snapshots")
        conn.execute(
            "DELETE FROM trash_items WHERE entity_type = 'notebook_note'"
        )
        conn.execute("DELETE FROM notebook_notes")


def _insert_note(conn: Connection, note: NotebookNote) -> None:
    try:
        conn.execute(
            """
            INSERT INTO notebook_notes (
                id, course_id, title, body_markdown, revision, origin_type,
                origin_message_id, origin_conversation_id,
                origin_snapshot_json, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                note.id,
                note.course_id,
                note.title,
                note.body_markdown,
                note.revision,
                note.origin_type,
                (
                    note.origin_snapshot.message_id
                    if isinstance(
                        note.origin_snapshot,
                        ChatAnswerNotebookNoteOriginSnapshot,
                    )
                    else None
                ),
                (
                    note.origin_snapshot.conversation_id
                    if isinstance(
                        note.origin_snapshot,
                        ChatAnswerNotebookNoteOriginSnapshot,
                    )
                    else None
                ),
                note.origin_snapshot.model_dump_json(),
                _datetime_text(note.created_at),
                _datetime_text(note.updated_at),
            ),
        )
    except IntegrityError as exc:
        raise NotebookNoteStoreError("Note identity already exists.") from exc


def _require_active_course(conn: Connection, course_id: str) -> None:
    row = conn.execute(
        """
        SELECT 1 FROM courses
        WHERE id = ? AND deleted_at IS NULL
        """,
        (course_id,),
    ).fetchone()
    if row is None:
        raise NotebookNoteScopeError("Course was not found.")


def _body_preview(body: str, limit: int = 240) -> str:
    collapsed = " ".join(body.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"
