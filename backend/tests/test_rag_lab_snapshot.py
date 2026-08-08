from pathlib import Path

import pytest

from app.course import DEFAULT_COURSE_ID
from app.db import get_db_path
from app.job import VideoJob, VideoJobStatus
from app.job_store import create_job
from app.knowledge_card import KnowledgeCard
from app.knowledge_card_store import create_card
from rag_lab.corpus import snapshot_course_corpus
from rag_lab.io import sha256_file


def test_snapshot_course_corpus_preserves_claim_provenance(tmp_path: Path) -> None:
    job = VideoJob(
        id="lecture-job",
        video_path=tmp_path / "lecture.mp4",
        status=VideoJobStatus.completed,
    )
    create_job(job)
    card = KnowledgeCard(
        id="gradient-card",
        job_id=job.id,
        title="Gradient",
        summary="A local rate of change.",
        source_start_seconds=4.0,
        source_end_seconds=8.0,
        claims=[
            {
                "text": "A gradient contains partial derivatives.",
                "evidence": [
                    {
                        "quote": "The gradient is the vector of partial derivatives.",
                        "segment_start_seconds": 4.0,
                        "segment_end_seconds": 8.0,
                    }
                ],
            }
        ],
    )
    create_card(card)

    snapshot = snapshot_course_corpus(
        DEFAULT_COURSE_ID,
        snapshot_id="test-snapshot",
    )

    assert snapshot.snapshot_id == "test-snapshot"
    assert snapshot.cards[0].card_id == card.id
    assert snapshot.cards[0].claims[0].claim_id == card.claims[0].id
    assert snapshot.cards[0].claims[0].evidence[0].quote.startswith("The gradient")
    assert snapshot.source_database_sha256 == sha256_file(get_db_path())
    assert len(snapshot.snapshot_sha256) == 64


def test_snapshot_rejects_a_database_path_different_from_store_state(
    tmp_path: Path,
) -> None:
    unrelated_database = tmp_path / "unrelated.db"
    unrelated_database.touch()

    with pytest.raises(ValueError, match="must match the database configured"):
        snapshot_course_corpus(
            DEFAULT_COURSE_ID,
            database_path=unrelated_database,
        )
