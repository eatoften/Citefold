from __future__ import annotations

from datetime import datetime
from sqlite3 import Row

from .card_embedding import vector_from_blob, vector_to_blob
from .db import connect, ensure_db
from .source_chunk_embedding import (
    SourceChunkEmbedding,
    SourceChunkEmbeddingInfo,
    SourceIndexCommitResult,
)


def _datetime_to_text(value: datetime) -> str:
    return value.isoformat()


def _datetime_from_text(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _row_to_embedding(row: Row) -> SourceChunkEmbedding:
    return SourceChunkEmbedding(
        chunk_id=row["chunk_id"],
        source_id=row["source_id"],
        model=row["model"],
        dimension=row["dimension"],
        text_hash=row["text_hash"],
        vector=vector_from_blob(
            row["vector"],
            dimension=row["dimension"],
        ),
        created_at=_datetime_from_text(row["created_at"]),
        updated_at=_datetime_from_text(row["updated_at"]),
    )


def _row_to_info(row: Row) -> SourceChunkEmbeddingInfo:
    return SourceChunkEmbeddingInfo(
        chunk_id=row["chunk_id"],
        source_id=row["source_id"],
        model=row["model"],
        dimension=row["dimension"],
        text_hash=row["text_hash"],
        created_at=_datetime_from_text(row["created_at"]),
        updated_at=_datetime_from_text(row["updated_at"]),
    )


def upsert_source_chunk_embeddings(
    embeddings: list[SourceChunkEmbedding],
) -> None:
    if not embeddings:
        return
    ensure_db()
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO source_chunk_embeddings (
                chunk_id, source_id, model, dimension, text_hash, vector,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chunk_id, model) DO UPDATE SET
                source_id = excluded.source_id,
                dimension = excluded.dimension,
                text_hash = excluded.text_hash,
                vector = excluded.vector,
                updated_at = excluded.updated_at
            """,
            [
                (
                    embedding.chunk_id,
                    embedding.source_id,
                    embedding.model,
                    embedding.dimension,
                    embedding.text_hash,
                    vector_to_blob(embedding.vector),
                    _datetime_to_text(embedding.created_at),
                    _datetime_to_text(embedding.updated_at),
                )
                for embedding in embeddings
            ],
        )


def commit_source_index(
    source_ids: list[str],
    *,
    expected_course_id: str,
    expected_generation: str,
    model: str,
    dimension: int,
    embeddings: list[SourceChunkEmbedding],
    indexed_at: datetime,
) -> SourceIndexCommitResult:
    if not source_ids:
        return SourceIndexCommitResult()
    ensure_db()
    committed = 0
    ready_source_ids: list[str] = []
    stale_source_ids: list[str] = []

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for embedding in embeddings:
            current = conn.execute(
                """
                SELECT 1
                FROM source_chunks
                INNER JOIN sources
                    ON sources.id = source_chunks.source_id
                WHERE source_chunks.id = ?
                  AND source_chunks.source_id = ?
                  AND source_chunks.text_hash = ?
                  AND source_chunks.is_active = 1
                  AND sources.content_status = 'ready'
                  AND sources.course_id = ?
                  AND sources.index_generation = ?
                """,
                (
                    embedding.chunk_id,
                    embedding.source_id,
                    embedding.text_hash,
                    expected_course_id,
                    expected_generation,
                ),
            ).fetchone()
            if current is None:
                continue
            conn.execute(
                """
                INSERT INTO source_chunk_embeddings (
                    chunk_id, source_id, model, dimension, text_hash, vector,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chunk_id, model) DO UPDATE SET
                    source_id = excluded.source_id,
                    dimension = excluded.dimension,
                    text_hash = excluded.text_hash,
                    vector = excluded.vector,
                    updated_at = excluded.updated_at
                """,
                (
                    embedding.chunk_id,
                    embedding.source_id,
                    embedding.model,
                    embedding.dimension,
                    embedding.text_hash,
                    vector_to_blob(embedding.vector),
                    _datetime_to_text(embedding.created_at),
                    _datetime_to_text(embedding.updated_at),
                ),
            )
            committed += 1

        for source_id in source_ids:
            source_exists = conn.execute(
                """
                SELECT 1
                FROM sources
                WHERE id = ? AND course_id = ?
                  AND index_generation = ?
                """,
                (
                    source_id,
                    expected_course_id,
                    expected_generation,
                ),
            ).fetchone()
            if source_exists is None:
                conn.execute(
                    """
                    UPDATE sources
                    SET index_status = 'stale',
                        index_generation = NULL,
                        index_error = ?,
                        updated_at = ?
                    WHERE id = ? AND index_status = 'indexing'
                      AND index_generation = ?
                    """,
                    (
                        "Source changed while indexing.",
                        _datetime_to_text(indexed_at),
                        source_id,
                        expected_generation,
                    ),
                )
                stale_source_ids.append(source_id)
                continue
            conn.execute(
                """
                DELETE FROM source_chunk_embeddings
                WHERE source_id = ? AND model = ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM source_chunks
                      WHERE source_chunks.id =
                                source_chunk_embeddings.chunk_id
                        AND source_chunks.source_id = ?
                        AND source_chunks.is_active = 1
                        AND source_chunks.text_hash =
                                source_chunk_embeddings.text_hash
                  )
                """,
                (source_id, model, source_id),
            )
            active_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM source_chunks
                INNER JOIN sources
                    ON sources.id = source_chunks.source_id
                WHERE source_chunks.source_id = ?
                  AND source_chunks.is_active = 1
                  AND sources.content_status = 'ready'
                  AND sources.course_id = ?
                """,
                (source_id, expected_course_id),
            ).fetchone()[0]
            indexed_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM source_chunks
                INNER JOIN source_chunk_embeddings
                    ON source_chunk_embeddings.chunk_id = source_chunks.id
                    AND source_chunk_embeddings.model = ?
                    AND source_chunk_embeddings.dimension = ?
                    AND source_chunk_embeddings.text_hash =
                            source_chunks.text_hash
                WHERE source_chunks.source_id = ?
                  AND source_chunks.is_active = 1
                """,
                (model, dimension, source_id),
            ).fetchone()[0]
            if active_count > 0 and indexed_count == active_count:
                conn.execute(
                    """
                    UPDATE sources
                    SET index_status = 'ready',
                        index_generation = NULL,
                        index_model = ?,
                        index_dimension = ?,
                        index_error = NULL,
                        indexed_at = ?,
                        updated_at = ?
                    WHERE id = ? AND index_generation = ?
                    """,
                    (
                        model,
                        dimension,
                        _datetime_to_text(indexed_at),
                        _datetime_to_text(indexed_at),
                        source_id,
                        expected_generation,
                    ),
                )
                ready_source_ids.append(source_id)
            else:
                next_status = "stale" if active_count else "not_indexed"
                conn.execute(
                    """
                    UPDATE sources
                    SET index_status = ?,
                        index_generation = NULL,
                        index_model = ?,
                        index_dimension = ?,
                        index_error = ?,
                        updated_at = ?
                    WHERE id = ? AND index_generation = ?
                    """,
                    (
                        next_status,
                        model,
                        dimension,
                        "Source changed while indexing.",
                        _datetime_to_text(indexed_at),
                        source_id,
                        expected_generation,
                    ),
                )
                stale_source_ids.append(source_id)

    return SourceIndexCommitResult(
        committed_embeddings=committed,
        ready_source_ids=ready_source_ids,
        stale_source_ids=stale_source_ids,
    )


def get_source_chunk_embedding_info(
    chunk_id: str,
    model: str,
) -> SourceChunkEmbeddingInfo | None:
    ensure_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT
                chunk_id, source_id, model, dimension, text_hash,
                created_at, updated_at
            FROM source_chunk_embeddings
            WHERE chunk_id = ? AND model = ?
            """,
            (chunk_id, model),
        ).fetchone()
    return _row_to_info(row) if row is not None else None


def list_source_chunk_embeddings(
    source_ids: list[str],
    *,
    expected_course_id: str,
    model: str,
) -> list[SourceChunkEmbedding]:
    if not source_ids:
        return []
    ensure_db()
    placeholders = ",".join("?" for _ in source_ids)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT source_chunk_embeddings.*
            FROM source_chunk_embeddings
            INNER JOIN sources
                ON sources.id = source_chunk_embeddings.source_id
            WHERE sources.course_id = ?
              AND source_chunk_embeddings.source_id IN ({placeholders})
              AND source_chunk_embeddings.model = ?
            ORDER BY source_chunk_embeddings.source_id,
                     source_chunk_embeddings.chunk_id
            """,
            [expected_course_id, *source_ids, model],
        ).fetchall()
    return [_row_to_embedding(row) for row in rows]


def list_source_chunk_embedding_infos(
    source_ids: list[str],
    *,
    model: str,
) -> list[SourceChunkEmbeddingInfo]:
    if not source_ids:
        return []
    ensure_db()
    placeholders = ",".join("?" for _ in source_ids)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT
                chunk_id, source_id, model, dimension, text_hash,
                created_at, updated_at
            FROM source_chunk_embeddings
            WHERE source_id IN ({placeholders}) AND model = ?
            ORDER BY source_id, chunk_id
            """,
            [*source_ids, model],
        ).fetchall()
    return [_row_to_info(row) for row in rows]


def delete_stale_source_chunk_embeddings(
    source_id: str,
    *,
    model: str,
    active_chunk_ids: list[str],
) -> None:
    ensure_db()
    with connect() as conn:
        if not active_chunk_ids:
            conn.execute(
                """
                DELETE FROM source_chunk_embeddings
                WHERE source_id = ? AND model = ?
                """,
                (source_id, model),
            )
            return
        placeholders = ",".join("?" for _ in active_chunk_ids)
        conn.execute(
            f"""
            DELETE FROM source_chunk_embeddings
            WHERE source_id = ? AND model = ?
              AND chunk_id NOT IN ({placeholders})
            """,
            [source_id, model, *active_chunk_ids],
        )


def delete_source_chunk_embeddings_for_source(source_id: str) -> None:
    ensure_db()
    with connect() as conn:
        conn.execute(
            "DELETE FROM source_chunk_embeddings WHERE source_id = ?",
            (source_id,),
        )


def clear_source_chunk_embeddings() -> None:
    ensure_db()
    with connect() as conn:
        conn.execute("DELETE FROM source_chunk_embeddings")
