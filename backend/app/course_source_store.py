from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from sqlite3 import Row

from .course_source import (
    CourseSource,
    CourseSourceChunk,
    hash_source_chunk_text,
)
from .db import connect, ensure_db
from .source_projection_identity import (
    ProjectionManifestChunk,
    build_projection_manifest_hash,
    select_projection_generation_id,
)


def _datetime_to_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _datetime_from_text(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _row_to_source(row: Row) -> CourseSource:
    keys = set(row.keys())
    return CourseSource(
        id=row["id"],
        course_id=row["course_id"],
        origin_type=row["origin_type"],
        origin_id=row["origin_id"],
        source_type=row["source_type"],
        title=row["title"],
        content_status=row["content_status"],
        index_status=row["index_status"],
        index_model=row["index_model"],
        index_dimension=row["index_dimension"],
        enabled=bool(row["enabled"]),
        chunk_count=row["chunk_count"] if "chunk_count" in keys else 0,
        indexed_chunk_count=(
            row["indexed_chunk_count"]
            if "indexed_chunk_count" in keys
            else 0
        ),
        projection_generation_id=row["projection_generation_id"],
        projection_manifest_hash=row["projection_manifest_hash"],
        size_bytes=row["size_bytes"],
        mime_type=row["mime_type"],
        metadata=json.loads(row["metadata_json"]),
        error_message=row["error_message"],
        index_error=row["index_error"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        indexed_at=_datetime_from_text(row["indexed_at"]),
    )


def _row_to_chunk(row: Row) -> CourseSourceChunk:
    return CourseSourceChunk(
        id=row["id"],
        source_id=row["source_id"],
        origin_type=row["origin_type"],
        origin_id=row["origin_id"],
        chunk_type=row["chunk_type"],
        ordinal=row["ordinal"],
        text=row["text"],
        text_hash=row["text_hash"],
        locator=json.loads(row["locator_json"]),
        chunker_version=row["chunker_version"],
        is_active=bool(row["is_active"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def replace_course_source_projection(
    course_id: str,
    sources: list[CourseSource],
    chunks: list[CourseSourceChunk],
) -> None:
    ensure_db()
    chunks_by_source: dict[str, list[CourseSourceChunk]] = defaultdict(list)
    for chunk in chunks:
        chunks_by_source[chunk.source_id].append(chunk)
    source_ids = {source.id for source in sources}
    if len(source_ids) != len(sources):
        raise ValueError("Source projection ids must be unique.")
    if set(chunks_by_source) - source_ids:
        raise ValueError("Source chunk belongs to an unknown source.")

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing_rows = conn.execute(
            "SELECT id FROM sources WHERE course_id = ?",
            (course_id,),
        ).fetchall()
        existing_source_ids = {str(row["id"]) for row in existing_rows}

        for source in sources:
            if source.course_id != course_id:
                raise ValueError("Source projection course ids must match.")
            source_chunks = chunks_by_source.get(source.id, [])
            if any(chunk.source_id != source.id for chunk in source_chunks):
                raise ValueError("Source chunk belongs to a different source.")
            _replace_projection(conn, source, source_chunks)

        removed_source_ids = existing_source_ids - source_ids
        _delete_sources(conn, removed_source_ids)


def replace_source_projection(
    source: CourseSource,
    chunks: list[CourseSourceChunk],
) -> None:
    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        replace_source_projection_in_connection(conn, source, chunks)


def replace_source_projection_in_connection(
    conn,
    source: CourseSource,
    chunks: list[CourseSourceChunk],
) -> None:
    """Replace one derived projection inside the caller-owned transaction."""

    if any(chunk.source_id != source.id for chunk in chunks):
        raise ValueError("Source chunk belongs to a different source.")
    _replace_projection(conn, source, chunks)


def list_sources_for_course(course_id: str) -> list[CourseSource]:
    ensure_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                sources.*,
                COUNT(DISTINCT source_chunks.id) AS chunk_count,
                COUNT(DISTINCT source_chunk_embeddings.chunk_id)
                    AS indexed_chunk_count
            FROM sources
            LEFT JOIN source_chunks
                ON source_chunks.source_id = sources.id
                AND source_chunks.is_active = 1
            LEFT JOIN source_chunk_embeddings
                ON source_chunk_embeddings.chunk_id = source_chunks.id
                AND source_chunk_embeddings.model = sources.index_model
                AND source_chunk_embeddings.dimension =
                        sources.index_dimension
                AND source_chunk_embeddings.text_hash =
                        source_chunks.text_hash
            WHERE sources.course_id = ?
            GROUP BY sources.id
            ORDER BY sources.updated_at DESC, sources.id
            """,
            (course_id,),
        ).fetchall()
    return [_row_to_source(row) for row in rows]


def get_source(source_id: str) -> CourseSource | None:
    ensure_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT
                sources.*,
                COUNT(DISTINCT source_chunks.id) AS chunk_count,
                COUNT(DISTINCT source_chunk_embeddings.chunk_id)
                    AS indexed_chunk_count
            FROM sources
            LEFT JOIN source_chunks
                ON source_chunks.source_id = sources.id
                AND source_chunks.is_active = 1
            LEFT JOIN source_chunk_embeddings
                ON source_chunk_embeddings.chunk_id = source_chunks.id
                AND source_chunk_embeddings.model = sources.index_model
                AND source_chunk_embeddings.dimension =
                        sources.index_dimension
                AND source_chunk_embeddings.text_hash =
                        source_chunks.text_hash
            WHERE sources.id = ?
            GROUP BY sources.id
            """,
            (source_id,),
        ).fetchone()
    return _row_to_source(row) if row is not None else None


def list_source_chunks(
    source_id: str,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[CourseSourceChunk]:
    ensure_db()
    parameters: list[object] = [source_id]
    pagination = ""
    if limit is not None:
        pagination = " LIMIT ? OFFSET ?"
        parameters.extend([limit, offset])
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM source_chunks
            WHERE source_id = ? AND is_active = 1
            ORDER BY ordinal, id
            """
            + pagination,
            parameters,
        ).fetchall()
    return [_row_to_chunk(row) for row in rows]


def list_chunks_for_sources(
    source_ids: list[str],
) -> list[CourseSourceChunk]:
    if not source_ids:
        return []
    ensure_db()
    placeholders = ",".join("?" for _ in source_ids)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM source_chunks
            WHERE source_id IN ({placeholders}) AND is_active = 1
            ORDER BY source_id, ordinal, id
            """,
            source_ids,
        ).fetchall()
    return [_row_to_chunk(row) for row in rows]


def set_source_enabled(source_id: str, enabled: bool) -> bool:
    ensure_db()
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE sources
            SET enabled = ?, updated_at = ?
            WHERE id = ?
            """,
            (int(enabled), datetime.now().astimezone().isoformat(), source_id),
        )
    return cursor.rowcount > 0


def begin_source_index(
    source_ids: list[str],
    *,
    expected_course_id: str,
    generation: str,
    model: str,
) -> bool:
    if not source_ids:
        return True
    ensure_db()
    placeholders = ",".join("?" for _ in source_ids)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        matched = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM sources
            WHERE id IN ({placeholders}) AND course_id = ?
            """,
            [*source_ids, expected_course_id],
        ).fetchone()[0]
        if matched != len(source_ids):
            return False
        conn.execute(
            f"""
            UPDATE sources
            SET index_status = 'indexing',
                index_generation = ?,
                index_model = ?,
                index_error = NULL,
                indexed_at = NULL,
                updated_at = ?
            WHERE id IN ({placeholders}) AND course_id = ?
            """,
            [
                generation,
                model,
                datetime.now().astimezone().isoformat(),
                *source_ids,
                expected_course_id,
            ],
        )
    return True


def fail_source_index(
    source_ids: list[str],
    *,
    generation: str,
    model: str,
    error: str,
) -> None:
    if not source_ids:
        return
    ensure_db()
    placeholders = ",".join("?" for _ in source_ids)
    with connect() as conn:
        conn.execute(
            f"""
            UPDATE sources
            SET index_status = 'failed',
                index_generation = NULL,
                index_model = ?,
                index_error = ?,
                updated_at = ?
            WHERE id IN ({placeholders})
              AND index_generation = ?
            """,
            [
                model,
                error,
                datetime.now().astimezone().isoformat(),
                *source_ids,
                generation,
            ],
        )


def list_chunks_for_course_sources(
    course_id: str,
    source_ids: list[str],
) -> list[CourseSourceChunk]:
    if not source_ids:
        return []
    ensure_db()
    placeholders = ",".join("?" for _ in source_ids)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT source_chunks.*
            FROM source_chunks
            INNER JOIN sources
                ON sources.id = source_chunks.source_id
            WHERE sources.course_id = ?
              AND source_chunks.source_id IN ({placeholders})
              AND source_chunks.is_active = 1
            ORDER BY source_chunks.source_id,
                     source_chunks.ordinal,
                     source_chunks.id
            """,
            [course_id, *source_ids],
        ).fetchall()
    return [_row_to_chunk(row) for row in rows]


def move_sources_to_course(
    source_course_id: str,
    target_course_id: str,
) -> None:
    ensure_db()
    with connect() as conn:
        conn.execute(
            """
            UPDATE sources
            SET course_id = ?,
                index_status = CASE
                    WHEN index_generation IS NOT NULL THEN 'stale'
                    ELSE index_status
                END,
                index_generation = NULL,
                index_error = CASE
                    WHEN index_generation IS NOT NULL
                        THEN 'Source changed while indexing.'
                    ELSE index_error
                END,
                updated_at = ?
            WHERE course_id = ?
            """,
            (
                target_course_id,
                datetime.now().astimezone().isoformat(),
                source_course_id,
            ),
        )


def delete_source_projection(source_id: str) -> None:
    ensure_db()
    with connect() as conn:
        delete_source_projection_in_connection(conn, source_id)


def delete_source_projection_in_connection(conn, source_id: str) -> None:
    """Delete one projection inside the caller-owned transaction."""

    _delete_sources(conn, {source_id})


def delete_source_projections_for_course(course_id: str) -> None:
    ensure_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT id FROM sources WHERE course_id = ?",
            (course_id,),
        ).fetchall()
        _delete_sources(conn, {str(row["id"]) for row in rows})


def clear_course_sources() -> None:
    ensure_db()
    with connect() as conn:
        conn.execute("DELETE FROM source_chunk_embeddings")
        conn.execute("DELETE FROM source_chunks")
        conn.execute("DELETE FROM sources")


def recover_active_source_indexes(*, error_message: str) -> int:
    ensure_db()
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE sources
            SET index_status = 'failed',
                index_generation = NULL,
                index_error = ?,
                updated_at = ?
            WHERE index_status = 'indexing'
               OR index_generation IS NOT NULL
            """,
            (error_message, datetime.now().astimezone().isoformat()),
        )
    return cursor.rowcount


def _replace_projection(
    conn,
    source: CourseSource,
    chunks: list[CourseSourceChunk],
) -> None:
    expected_source_id = {
        "video_job": f"job:{source.origin_id}",
        "source_asset": f"asset:{source.origin_id}",
        "notebook_note": f"note:{source.origin_id}",
    }[source.origin_type]
    if source.id != expected_source_id:
        raise ValueError("Source id does not match its origin identity.")
    if any(not chunk.is_active for chunk in chunks):
        raise ValueError(
            "Replacement projections accept active Source chunks only."
        )
    chunk_ids = [chunk.id for chunk in chunks]
    if len(chunk_ids) != len(set(chunk_ids)):
        raise ValueError("Source projection chunk ids must be unique.")
    ordinals = [chunk.ordinal for chunk in chunks]
    if len(ordinals) != len(set(ordinals)):
        raise ValueError("Source projection chunk ordinals must be unique.")
    if any(
        hash_source_chunk_text(chunk.text) != chunk.text_hash
        for chunk in chunks
    ):
        raise ValueError("Source chunk text hash does not match its text.")
    if chunk_ids:
        placeholders = ",".join("?" for _ in chunk_ids)
        conflicting_owner = conn.execute(
            f"""
            SELECT id, source_id
            FROM source_chunks
            WHERE id IN ({placeholders}) AND source_id != ?
            LIMIT 1
            """,
            [*chunk_ids, source.id],
        ).fetchone()
        if conflicting_owner is not None:
            raise ValueError(
                "A Source chunk id cannot move between Sources."
            )

    manifest_hash = build_projection_manifest_hash(
        source_id=source.id,
        source_type=source.source_type,
        chunks=(
            ProjectionManifestChunk(
                id=chunk.id,
                chunk_type=chunk.chunk_type,
                ordinal=chunk.ordinal,
                text_hash=chunk.text_hash,
                locator=chunk.locator,
                chunker_version=chunk.chunker_version,
            )
            for chunk in chunks
        ),
    )
    current = conn.execute(
        """
        SELECT origin_type, origin_id, projection_generation_id,
               projection_manifest_hash
        FROM sources
        WHERE id = ?
        """,
        (source.id,),
    ).fetchone()
    if current is not None and (
        current["origin_type"] != source.origin_type
        or current["origin_id"] != source.origin_id
    ):
        raise ValueError("A Source id cannot move between origin roots.")
    generation_id = select_projection_generation_id(
        current_generation_id=(
            str(current["projection_generation_id"])
            if current is not None
            and current["projection_generation_id"] is not None
            else None
        ),
        current_manifest_hash=(
            str(current["projection_manifest_hash"])
            if current is not None
            and current["projection_manifest_hash"] is not None
            else None
        ),
        next_manifest_hash=manifest_hash,
    )
    _upsert_source(
        conn,
        source,
        projection_generation_id=generation_id,
        projection_manifest_hash=manifest_hash,
    )
    _replace_source_chunks(conn, source, chunks)


def _upsert_source(
    conn,
    source: CourseSource,
    *,
    projection_generation_id: str,
    projection_manifest_hash: str,
) -> None:
    conn.execute(
        """
        INSERT INTO sources (
            id, course_id, origin_type, origin_id, source_type, title,
            content_status, index_status, index_generation, index_model,
            index_dimension, projection_generation_id,
            projection_manifest_hash,
            enabled, size_bytes, mime_type, metadata_json, error_message,
            index_error, created_at, updated_at, indexed_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?
        )
        ON CONFLICT(id) DO UPDATE SET
            course_id = excluded.course_id,
            origin_type = excluded.origin_type,
            origin_id = excluded.origin_id,
            source_type = excluded.source_type,
            title = excluded.title,
            content_status = excluded.content_status,
            projection_generation_id = excluded.projection_generation_id,
            projection_manifest_hash = excluded.projection_manifest_hash,
            size_bytes = excluded.size_bytes,
            mime_type = excluded.mime_type,
            metadata_json = excluded.metadata_json,
            error_message = excluded.error_message,
            created_at = excluded.created_at,
            updated_at = excluded.updated_at
        """,
        (
            source.id,
            source.course_id,
            source.origin_type,
            source.origin_id,
            source.source_type,
            source.title,
            source.content_status,
            source.index_status,
            source.index_model,
            source.index_dimension,
            projection_generation_id,
            projection_manifest_hash,
            int(source.enabled),
            source.size_bytes,
            source.mime_type,
            json.dumps(source.metadata, ensure_ascii=False),
            source.error_message,
            source.index_error,
            _datetime_to_text(source.created_at),
            _datetime_to_text(source.updated_at),
            _datetime_to_text(source.indexed_at),
        ),
    )


def _replace_source_chunks(
    conn,
    source: CourseSource,
    chunks: list[CourseSourceChunk],
) -> None:
    existing_rows = conn.execute(
        """
        SELECT id, text_hash
        FROM source_chunks
        WHERE source_id = ? AND is_active = 1
        """,
        (source.id,),
    ).fetchall()
    existing_hashes = {
        str(row["id"]): str(row["text_hash"])
        for row in existing_rows
    }
    next_hashes = {chunk.id: chunk.text_hash for chunk in chunks}
    content_changed = existing_hashes != next_hashes
    index_generation_row = conn.execute(
        "SELECT index_generation FROM sources WHERE id = ?",
        (source.id,),
    ).fetchone()
    indexing_was_in_progress = (
        index_generation_row is not None
        and index_generation_row["index_generation"] is not None
    )
    # Release the partial unique ordinal index before an atomic reorder. The
    # transaction either reactivates the complete next projection or rolls
    # this temporary state back.
    conn.execute(
        """
        UPDATE source_chunks
        SET is_active = 0
        WHERE source_id = ? AND is_active = 1
        """,
        (source.id,),
    )
    removed_chunk_ids = set(existing_hashes) - set(next_hashes)
    _delete_chunks(conn, removed_chunk_ids)

    for chunk in chunks:
        conn.execute(
            """
            INSERT INTO source_chunks (
                id, source_id, origin_type, origin_id, chunk_type, ordinal,
                text, text_hash, locator_json, chunker_version, is_active,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                source_id = excluded.source_id,
                origin_type = excluded.origin_type,
                origin_id = excluded.origin_id,
                chunk_type = excluded.chunk_type,
                ordinal = excluded.ordinal,
                text = excluded.text,
                text_hash = excluded.text_hash,
                locator_json = excluded.locator_json,
                chunker_version = excluded.chunker_version,
                is_active = excluded.is_active,
                updated_at = excluded.updated_at
            """,
            (
                chunk.id,
                chunk.source_id,
                chunk.origin_type,
                chunk.origin_id,
                chunk.chunk_type,
                chunk.ordinal,
                chunk.text,
                chunk.text_hash,
                json.dumps(
                    chunk.locator.model_dump(mode="json"),
                    ensure_ascii=False,
                ),
                chunk.chunker_version,
                int(chunk.is_active),
                _datetime_to_text(chunk.created_at),
                _datetime_to_text(chunk.updated_at),
            ),
        )

    if source.content_status != "ready" or not chunks:
        conn.execute(
            """
            UPDATE sources
            SET index_status = 'not_indexed',
                index_generation = NULL,
                index_error = NULL,
                indexed_at = NULL
            WHERE id = ?
            """,
            (source.id,),
        )
    elif content_changed:
        embedded_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM source_chunk_embeddings
            WHERE source_id = ?
            """,
            (source.id,),
        ).fetchone()[0]
        next_status = (
            "stale"
            if embedded_count or indexing_was_in_progress
            else "not_indexed"
        )
        conn.execute(
            """
            UPDATE sources
            SET index_status = ?,
                index_generation = NULL,
                index_error = NULL
            WHERE id = ?
            """,
            (next_status, source.id),
        )


def _delete_sources(conn, source_ids: set[str]) -> None:
    if not source_ids:
        return
    placeholders = ",".join("?" for _ in source_ids)
    parameters = list(source_ids)
    conn.execute(
        f"""
        DELETE FROM source_chunk_embeddings
        WHERE source_id IN ({placeholders})
        """,
        parameters,
    )
    conn.execute(
        f"DELETE FROM source_chunks WHERE source_id IN ({placeholders})",
        parameters,
    )
    conn.execute(
        f"DELETE FROM sources WHERE id IN ({placeholders})",
        parameters,
    )


def _delete_chunks(conn, chunk_ids: set[str]) -> None:
    if not chunk_ids:
        return
    placeholders = ",".join("?" for _ in chunk_ids)
    parameters = list(chunk_ids)
    conn.execute(
        f"""
        DELETE FROM source_chunk_embeddings
        WHERE chunk_id IN ({placeholders})
        """,
        parameters,
    )
    conn.execute(
        f"DELETE FROM source_chunks WHERE id IN ({placeholders})",
        parameters,
    )
