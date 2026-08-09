from __future__ import annotations

import json
from dataclasses import dataclass, replace
from sqlite3 import Row

from .db import connect, ensure_db


@dataclass(frozen=True)
class CitationSnapshotRecord:
    citation_id: str
    message_id: str
    source_id: str
    chunk_id: str
    chunk_text_hash: str
    source_title: str
    source_type: str
    quote: str
    locator: dict[str, object]
    source_course_id: str | None
    source_origin_type: str | None
    source_origin_id: str | None
    current_source_type: str | None
    current_chunk_text: str | None
    current_chunk_text_hash: str | None
    current_chunk_locator: dict[str, object] | None
    current_chunk_ordinal: int | None
    current_chunk_active: bool | None
    context: tuple[CitationContextRecord, ...] = ()
    projection_generation_id: str | None = None
    current_projection_generation_id: str | None = None
    current_source_status: str | None = None
    source_root_current: bool | None = None


@dataclass(frozen=True)
class CitationContextRecord:
    chunk_id: str
    ordinal: int
    text: str
    locator: dict[str, object]


def get_citation_snapshot_for_course(
    course_id: str,
    citation_id: str,
) -> CitationSnapshotRecord | None:
    """Read the server-owned citation and its current canonical projection."""

    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN")
        row = conn.execute(
            """
            SELECT
                citations.id AS citation_id,
                citations.message_id,
                citations.source_id,
                citations.chunk_id,
                citations.chunk_text_hash,
                citations.source_title,
                citations.source_type,
                citations.quote,
                citations.locator_json,
                sources.course_id AS source_course_id,
                sources.origin_type AS source_origin_type,
                sources.origin_id AS source_origin_id,
                sources.source_type AS current_source_type,
                chunks.text AS current_chunk_text,
                chunks.text_hash AS current_chunk_text_hash,
                chunks.locator_json AS current_chunk_locator_json,
                chunks.ordinal AS current_chunk_ordinal,
                chunks.is_active AS current_chunk_active
            FROM chat_citations AS citations
            INNER JOIN chat_messages AS messages
                ON messages.id = citations.message_id
            INNER JOIN chat_conversations AS conversations
                ON conversations.id = messages.conversation_id
            LEFT JOIN sources
                ON sources.id = citations.source_id
            LEFT JOIN source_chunks AS chunks
                ON chunks.id = citations.chunk_id
                AND chunks.source_id = citations.source_id
            WHERE citations.id = ?
              AND conversations.course_id = ?
            """,
            (citation_id, course_id),
        ).fetchone()
        if row is None:
            row = conn.execute(
                """
                SELECT
                    citations.id AS citation_id,
                    COALESCE(
                        notes.origin_message_id,
                        notes.id
                    ) AS message_id,
                    citations.source_id,
                    citations.chunk_id,
                    citations.chunk_text_hash,
                    citations.source_title,
                    citations.source_type,
                    citations.quote,
                    citations.locator_json,
                    sources.course_id AS source_course_id,
                    sources.origin_type AS source_origin_type,
                    sources.origin_id AS source_origin_id,
                    sources.source_type AS current_source_type,
                    chunks.text AS current_chunk_text,
                    chunks.text_hash AS current_chunk_text_hash,
                    chunks.locator_json AS current_chunk_locator_json,
                    chunks.ordinal AS current_chunk_ordinal,
                    chunks.is_active AS current_chunk_active
                FROM notebook_note_citations AS citations
                INNER JOIN notebook_notes AS notes
                    ON notes.id = citations.note_id
                INNER JOIN courses
                    ON courses.id = notes.course_id
                LEFT JOIN sources
                    ON sources.id = citations.source_id
                LEFT JOIN source_chunks AS chunks
                    ON chunks.id = citations.chunk_id
                    AND chunks.source_id = citations.source_id
                WHERE citations.id = ?
                  AND notes.course_id = ?
                  AND notes.deleted_at IS NULL
                  AND courses.deleted_at IS NULL
                """,
                (citation_id, course_id),
            ).fetchone()
        if row is None:
            return None
        snapshot = _row_to_snapshot(row)
        if snapshot.current_chunk_ordinal is None:
            return snapshot
        rows = conn.execute(
            """
            SELECT chunks.id, chunks.ordinal, chunks.text, chunks.locator_json
            FROM source_chunks AS chunks
            INNER JOIN sources ON sources.id = chunks.source_id
            WHERE sources.course_id = ?
              AND chunks.source_id = ?
              AND chunks.is_active = 1
              AND chunks.ordinal BETWEEN ? AND ?
            ORDER BY chunks.ordinal, chunks.id
            """,
            (
                course_id,
                snapshot.source_id,
                max(0, snapshot.current_chunk_ordinal - 1),
                snapshot.current_chunk_ordinal + 1,
            ),
        ).fetchall()
        return replace(
            snapshot,
            context=tuple(
                CitationContextRecord(
                    chunk_id=str(context_row["id"]),
                    ordinal=int(context_row["ordinal"]),
                    text=str(context_row["text"]),
                    locator=_json_object(context_row["locator_json"]),
                )
                for context_row in rows
            ),
        )


def _row_to_snapshot(row: Row) -> CitationSnapshotRecord:
    current_locator = (
        _json_object(row["current_chunk_locator_json"])
        if row["current_chunk_locator_json"] is not None
        else None
    )
    return CitationSnapshotRecord(
        citation_id=str(row["citation_id"]),
        message_id=str(row["message_id"]),
        source_id=str(row["source_id"]),
        chunk_id=str(row["chunk_id"]),
        chunk_text_hash=str(row["chunk_text_hash"]),
        source_title=str(row["source_title"]),
        source_type=str(row["source_type"]),
        quote=str(row["quote"]),
        locator=_json_object(row["locator_json"]),
        source_course_id=(
            str(row["source_course_id"])
            if row["source_course_id"] is not None
            else None
        ),
        source_origin_type=(
            str(row["source_origin_type"])
            if row["source_origin_type"] is not None
            else None
        ),
        source_origin_id=(
            str(row["source_origin_id"])
            if row["source_origin_id"] is not None
            else None
        ),
        current_source_type=(
            str(row["current_source_type"])
            if row["current_source_type"] is not None
            else None
        ),
        current_chunk_text=(
            str(row["current_chunk_text"])
            if row["current_chunk_text"] is not None
            else None
        ),
        current_chunk_text_hash=(
            str(row["current_chunk_text_hash"])
            if row["current_chunk_text_hash"] is not None
            else None
        ),
        current_chunk_locator=current_locator,
        current_chunk_ordinal=(
            int(row["current_chunk_ordinal"])
            if row["current_chunk_ordinal"] is not None
            else None
        ),
        current_chunk_active=(
            bool(row["current_chunk_active"])
            if row["current_chunk_active"] is not None
            else None
        ),
    )


def _json_object(value: object) -> dict[str, object]:
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
