from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from .db import connect, ensure_db
from .knowledge_card import KnowledgeCard
from .review_item import ReviewItem
from .transcript_chunk import TranscriptChunk


@dataclass(frozen=True)
class CardGenerationChunkResult:
    run_id: str
    chunk_id: str
    chunk_index: int
    status: str
    cards_created: int
    error_message: str | None
    created_at: datetime
    updated_at: datetime


def list_chunk_results(run_id: str) -> list[CardGenerationChunkResult]:
    ensure_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM card_generation_chunk_results
            WHERE run_id = ?
            ORDER BY chunk_index, chunk_id
            """,
            (run_id,),
        ).fetchall()
    return [
        CardGenerationChunkResult(
            run_id=str(row["run_id"]),
            chunk_id=str(row["chunk_id"]),
            chunk_index=int(row["chunk_index"]),
            status=str(row["status"]),
            cards_created=int(row["cards_created"]),
            error_message=(
                str(row["error_message"])
                if row["error_message"] is not None
                else None
            ),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )
        for row in rows
    ]


def clear_failed_chunk_results(run_id: str) -> None:
    """Start a retry attempt while preserving already-published successes."""

    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            DELETE FROM card_generation_chunk_results
            WHERE run_id = ? AND status = 'failed'
            """,
            (run_id,),
        )


def publish_chunk_success(
    run_id: str,
    chunk: TranscriptChunk,
    *,
    cards: list[KnowledgeCard],
    review_items: list[ReviewItem],
    now: datetime,
) -> int:
    """Atomically publish one chunk's cards and its completion record."""

    ensure_db()
    card_ids = {card.id for card in cards}
    if len(card_ids) != len(cards):
        raise ValueError("Generated chunk contains duplicate card identities.")
    if any(item.card_id not in card_ids for item in review_items):
        raise ValueError(
            "Generated review item does not belong to this chunk's cards."
        )

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            """
            SELECT status, cards_created
            FROM card_generation_chunk_results
            WHERE run_id = ? AND chunk_id = ?
            """,
            (run_id, chunk.id),
        ).fetchone()
        if existing is not None and existing["status"] == "succeeded":
            return int(existing["cards_created"])

        for card in cards:
            conn.execute(
                """
                INSERT INTO knowledge_cards (
                    id, job_id, card_kind, title, summary, key_points,
                    claims, unsupported_terms, tags, content_status,
                    source_start_seconds, source_end_seconds, provider, model,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    card.id,
                    card.job_id,
                    card.card_kind,
                    card.title,
                    card.summary,
                    json.dumps(card.key_points, ensure_ascii=False),
                    json.dumps(
                        [
                            claim.model_dump(mode="json")
                            for claim in card.claims
                        ],
                        ensure_ascii=False,
                    ),
                    json.dumps(card.unsupported_terms, ensure_ascii=False),
                    json.dumps(card.tags, ensure_ascii=False),
                    card.content_status,
                    card.source_start_seconds,
                    card.source_end_seconds,
                    card.provider,
                    card.model,
                    card.created_at.isoformat(),
                    card.updated_at.isoformat(),
                ),
            )

        for item in review_items:
            conn.execute(
                """
                INSERT INTO review_items (
                    id, card_id, item_type, prompt, expected_answer,
                    source_claim_ids, source, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.card_id,
                    item.item_type,
                    item.prompt,
                    item.expected_answer,
                    json.dumps(item.source_claim_ids, ensure_ascii=False),
                    item.source,
                    item.status,
                    item.created_at.isoformat(),
                    item.updated_at.isoformat(),
                ),
            )

        conn.execute(
            """
            INSERT INTO card_generation_chunk_results (
                run_id, chunk_id, chunk_index, status, cards_created,
                error_message, created_at, updated_at
            ) VALUES (?, ?, ?, 'succeeded', ?, NULL, ?, ?)
            ON CONFLICT(run_id, chunk_id) DO UPDATE SET
                chunk_index = excluded.chunk_index,
                status = excluded.status,
                cards_created = excluded.cards_created,
                error_message = NULL,
                updated_at = excluded.updated_at
            """,
            (
                run_id,
                chunk.id,
                chunk.chunk_index,
                len(cards),
                now.isoformat(),
                now.isoformat(),
            ),
        )
    return len(cards)


def record_chunk_failure(
    run_id: str,
    chunk: TranscriptChunk,
    *,
    error_message: str,
    now: datetime,
) -> None:
    ensure_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO card_generation_chunk_results (
                run_id, chunk_id, chunk_index, status, cards_created,
                error_message, created_at, updated_at
            ) VALUES (?, ?, ?, 'failed', 0, ?, ?, ?)
            ON CONFLICT(run_id, chunk_id) DO UPDATE SET
                chunk_index = excluded.chunk_index,
                status = excluded.status,
                cards_created = 0,
                error_message = excluded.error_message,
                updated_at = excluded.updated_at
            WHERE card_generation_chunk_results.status != 'succeeded'
            """,
            (
                run_id,
                chunk.id,
                chunk.chunk_index,
                error_message,
                now.isoformat(),
                now.isoformat(),
            ),
        )
