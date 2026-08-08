from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.card_embedding_text import build_card_embedding_text
from app.card_relation_store import list_card_relations_for_course
from app.db import get_db_path
from app.job_store import list_jobs_for_course
from app.knowledge_card_store import list_cards_for_course

from .io import sha256_file, sha256_value
from .schemas import (
    RagCorpusCard,
    RagCorpusClaim,
    RagCorpusEvidence,
    RagCorpusRelation,
    RagCorpusSnapshot,
)


def snapshot_course_corpus(
    course_id: str,
    *,
    snapshot_id: str | None = None,
    database_path: Path | None = None,
) -> RagCorpusSnapshot:
    normalized_course_id = course_id.strip()
    if not normalized_course_id:
        raise ValueError("course_id is required.")

    resolved_database = get_db_path().resolve()
    if (
        database_path is not None
        and database_path.resolve() != resolved_database
    ):
        raise ValueError(
            "database_path must match the database configured for app stores."
        )
    if not resolved_database.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {resolved_database}")

    jobs = {job.id: job for job in list_jobs_for_course(normalized_course_id)}
    cards = []
    for card in list_cards_for_course(normalized_course_id):
        job = jobs.get(card.job_id)
        if job is None:
            raise ValueError(f"Card {card.id} references a job outside the course.")
        cards.append(
            RagCorpusCard(
                card_id=card.id,
                job_id=card.job_id,
                lecture_name=(
                    job.original_filename
                    or job.stored_name
                    or job.video_path.name
                ),
                title=card.title,
                summary=card.summary,
                document_text=build_card_embedding_text(card),
                content_status=card.content_status,
                source_start_seconds=card.source_start_seconds,
                source_end_seconds=card.source_end_seconds,
                claims=[
                    RagCorpusClaim(
                        claim_id=claim.id,
                        text=claim.text,
                        evidence=[
                            RagCorpusEvidence(
                                evidence_id=evidence.id,
                                quote=evidence.quote,
                                start_seconds=evidence.segment_start_seconds,
                                end_seconds=evidence.segment_end_seconds,
                            )
                            for evidence in claim.evidence
                        ],
                    )
                    for claim in card.claims
                ],
                tags=card.tags,
            )
        )
    cards.sort(key=lambda card: (card.lecture_name, card.source_start_seconds, card.card_id))

    card_ids = {card.card_id for card in cards}
    relations = [
        RagCorpusRelation(
            relation_id=relation.id,
            source_card_id=relation.source_card_id,
            target_card_id=relation.target_card_id,
            relation_type=relation.relation_type,
            score=relation.score,
            method=relation.method,
            status=relation.status,
            explanation=relation.explanation,
        )
        for relation in list_card_relations_for_course(normalized_course_id)
        if relation.source_card_id in card_ids and relation.target_card_id in card_ids
    ]
    relations.sort(
        key=lambda relation: (
            relation.source_card_id,
            relation.target_card_id,
            relation.relation_type,
            relation.relation_id,
        )
    )
    snapshot_payload = {
        "course_id": normalized_course_id,
        "cards": [card.model_dump(mode="json") for card in cards],
        "relations": [relation.model_dump(mode="json") for relation in relations],
    }
    return RagCorpusSnapshot(
        snapshot_id=snapshot_id or f"rag-corpus-{uuid4().hex[:12]}",
        course_id=normalized_course_id,
        source_database_sha256=sha256_file(resolved_database),
        snapshot_sha256=sha256_value(snapshot_payload),
        cards=cards,
        relations=relations,
    )
