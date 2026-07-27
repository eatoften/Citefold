from fastapi.testclient import TestClient
import pytest

import app.card_embedding_service as card_embedding_service
import app.main as main
from app.card_embedding import vector_from_blob, vector_to_blob
from app.card_embedding_store import (
    get_card_embedding,
    list_card_embeddings_for_course,
    list_card_embeddings_for_job,
)
from app.card_embedding_text import (
    build_card_embedding_text,
    hash_card_embedding_text,
)
from app.job import VideoJob, VideoJobStatus
from app.job_store import create_job
from app.knowledge_card import KnowledgeCard


client = TestClient(main.app)


class FakeEmbedder:
    model_name = "sentence-transformers/all-MiniLM-L6-v2"

    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = vectors
        self.texts: list[str] = []

    def embed_texts(
        self,
        texts,
        *,
        batch_size=None,
    ) -> list[list[float]]:
        self.texts = list(texts)

        return self.vectors[:len(self.texts)]


def create_uploaded_job(tmp_path, job_id: str = "job-123") -> VideoJob:
    job = VideoJob(
        id=job_id,
        video_path=tmp_path / "lecture.mp4",
        status=VideoJobStatus.completed,
        original_filename="lecture.mp4",
    )
    create_job(job)

    return job


def card_payload(title: str = "Linear Algebra") -> dict:
    return {
        "title": title,
        "summary": "Linear algebra is one of the course's core subjects.",
        "key_points": [
            "Symmetric matrices",
            "Orthogonal matrices",
        ],
        "claims": [
            {
                "text": (
                    "Linear algebra is one of the course's core subjects."
                ),
                "evidence": [
                    {
                        "quote": "The first big subject is linear algebra.",
                        "segment_start_seconds": 36.44,
                        "segment_end_seconds": 44.0,
                    }
                ],
            }
        ],
        "unsupported_terms": [],
        "question": "What is the first big subject?",
        "answer": "Linear algebra.",
        "difficulty": "easy",
        "tags": [
            "linear algebra",
            "matrix",
        ],
        "source_start_seconds": 36.44,
        "source_end_seconds": 92.68,
        "provider": "ollama",
        "model": "qwen3:4b",
    }


def save_card(job_id: str, title: str = "Linear Algebra") -> dict:
    response = client.post(
        f"/jobs/{job_id}/cards",
        json=card_payload(title),
    )

    assert response.status_code == 201

    return response.json()


def test_card_embedding_text_serializes_structured_card(tmp_path):
    job = create_uploaded_job(tmp_path)
    created_card = save_card(job.id)
    card = KnowledgeCard.model_validate(created_card)

    text = build_card_embedding_text(card)

    assert "Title:\nLinear Algebra" in text
    assert "Summary:" in text
    assert "- Symmetric matrices" in text
    assert "- Claim: Linear algebra is one of the course's core subjects." in text
    assert "Evidence: The first big subject is linear algebra." in text
    assert "Active recall:" not in text
    assert "Tags:\nlinear algebra, matrix" in text
    assert "ollama" not in text
    assert hash_card_embedding_text(text) == hash_card_embedding_text(text)


def test_vector_blob_round_trip():
    blob = vector_to_blob([1.0, 0.25, -0.5])

    assert vector_from_blob(blob, dimension=3) == pytest.approx(
        [1.0, 0.25, -0.5]
    )


def test_embed_job_cards_and_skip_current_embeddings(monkeypatch, tmp_path):
    job = create_uploaded_job(tmp_path)
    created_card = save_card(job.id)
    first_embedder = FakeEmbedder([[1.0, 0.0]])
    monkeypatch.setattr(
        card_embedding_service,
        "_create_default_embedder",
        lambda: first_embedder,
    )

    response = client.post(f"/jobs/{job.id}/card-embeddings")

    assert response.status_code == 200
    assert response.json() == {
        "total_cards": 1,
        "embedded_cards": 1,
        "skipped_cards": 0,
        "model": first_embedder.model_name,
        "dimension": 2,
    }
    assert first_embedder.texts

    embedding = get_card_embedding(created_card["id"])

    assert embedding is not None
    assert embedding.vector == pytest.approx([1.0, 0.0])

    second_embedder = FakeEmbedder([[0.0, 1.0]])
    monkeypatch.setattr(
        card_embedding_service,
        "_create_default_embedder",
        lambda: second_embedder,
    )

    second_response = client.post(f"/jobs/{job.id}/card-embeddings")

    assert second_response.status_code == 200
    assert second_response.json() == {
        "total_cards": 1,
        "embedded_cards": 0,
        "skipped_cards": 1,
        "model": second_embedder.model_name,
        "dimension": 2,
    }
    assert second_embedder.texts == []


def test_list_card_embeddings_for_job_and_course_returns_vectors(
    monkeypatch,
    tmp_path,
):
    job = create_uploaded_job(tmp_path)
    first_card = save_card(job.id, "Linear Algebra")
    second_card = save_card(job.id, "Deep Learning")
    fake_embedder = FakeEmbedder(
        [
            [1.0, 0.0],
            [0.0, 1.0],
        ]
    )
    monkeypatch.setattr(
        card_embedding_service,
        "_create_default_embedder",
        lambda: fake_embedder,
    )

    response = client.post(f"/jobs/{job.id}/card-embeddings")

    assert response.status_code == 200

    job_embeddings = list_card_embeddings_for_job(job.id)
    course_embeddings = list_card_embeddings_for_course(job.course_id)

    assert {
        embedding.card_id
        for embedding in job_embeddings
    } == {
        first_card["id"],
        second_card["id"],
    }
    assert {
        embedding.card_id
        for embedding in course_embeddings
    } == {
        first_card["id"],
        second_card["id"],
    }

    job_vectors_by_card_id = {
        embedding.card_id: embedding.vector
        for embedding in job_embeddings
    }

    assert job_vectors_by_card_id[first_card["id"]] == pytest.approx(
        [1.0, 0.0]
    )
    assert job_vectors_by_card_id[second_card["id"]] == pytest.approx(
        [0.0, 1.0]
    )


def test_card_embedding_status_detects_stale_card(monkeypatch, tmp_path):
    job = create_uploaded_job(tmp_path)
    created_card = save_card(job.id)
    fake_embedder = FakeEmbedder([[1.0, 0.0]])
    monkeypatch.setattr(
        card_embedding_service,
        "_create_default_embedder",
        lambda: fake_embedder,
    )

    embed_response = client.post(
        f"/cards/{created_card['id']}/embedding"
    )

    assert embed_response.status_code == 200

    status_response = client.get(
        f"/jobs/{job.id}/card-embeddings/status"
    )

    assert status_response.status_code == 200
    assert status_response.json() == {
        "total_cards": 1,
        "embedded_cards": 1,
        "missing_cards": 0,
        "stale_cards": 0,
        "model": fake_embedder.model_name,
        "dimension": 2,
    }

    update_response = client.patch(
        f"/cards/{created_card['id']}",
        json={"title": "Singular Value Decomposition"},
    )

    assert update_response.status_code == 200

    stale_response = client.get(
        f"/cards/{created_card['id']}/embedding/status"
    )

    assert stale_response.status_code == 200
    assert stale_response.json() == {
        "total_cards": 1,
        "embedded_cards": 0,
        "missing_cards": 0,
        "stale_cards": 1,
        "model": fake_embedder.model_name,
        "dimension": 2,
    }


def test_delete_card_preserves_embedding_for_restore(monkeypatch, tmp_path):
    job = create_uploaded_job(tmp_path)
    created_card = save_card(job.id)
    fake_embedder = FakeEmbedder([[1.0, 0.0]])
    monkeypatch.setattr(
        card_embedding_service,
        "_create_default_embedder",
        lambda: fake_embedder,
    )

    client.post(f"/cards/{created_card['id']}/embedding")

    assert get_card_embedding(created_card["id"]) is not None

    delete_response = client.delete(f"/cards/{created_card['id']}")

    assert delete_response.status_code == 204
    assert get_card_embedding(created_card["id"]) is not None
    assert client.get(
        f"/cards/{created_card['id']}/embedding/status"
    ).status_code == 404

    trash_item = next(
        item
        for item in client.get("/trash").json()
        if item["entity_type"] == "knowledge_card"
        and item["entity_id"] == created_card["id"]
    )
    assert client.post(
        f"/trash/{trash_item['id']}/restore"
    ).status_code == 200
    assert client.get(
        f"/cards/{created_card['id']}/embedding/status"
    ).status_code == 200


def test_card_embedding_status_returns_404_for_missing_card():
    response = client.get("/cards/missing-card/embedding/status")

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Knowledge card not found."
    }
