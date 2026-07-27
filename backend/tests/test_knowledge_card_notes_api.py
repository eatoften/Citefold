from fastapi.testclient import TestClient

import app.main as main
from app.job import VideoJob, VideoJobStatus
from app.job_store import create_job
from app.knowledge_card import KnowledgeCard
from app.knowledge_card_note_store import get_note
from app.knowledge_card_store import create_card


client = TestClient(main.app)


def create_uploaded_job(tmp_path, job_id: str = "job-123") -> VideoJob:
    job = VideoJob(
        id=job_id,
        video_path=tmp_path / "lecture.mp4",
        status=VideoJobStatus.completed,
        original_filename="lecture.mp4",
    )
    create_job(job)

    return job


def create_saved_card(tmp_path, card_id: str = "card-123") -> KnowledgeCard:
    job = create_uploaded_job(tmp_path)
    card = KnowledgeCard(
        id=card_id,
        job_id=job.id,
        title="Singular Value Decomposition",
        summary="SVD factors a matrix into structured pieces.",
        key_points=[
            "Matrix factorization",
        ],
        claims=[
            {
                "text": "SVD is a matrix factorization.",
                "evidence": [
                    {
                        "quote": "singular value decomposition",
                        "segment_start_seconds": 1.0,
                        "segment_end_seconds": 2.0,
                    }
                ],
            }
        ],
        source_start_seconds=1.0,
        source_end_seconds=2.0,
    )
    create_card(card)

    return card


def note_payload() -> dict:
    return {
        "note_type": "user_note",
        "title": "My intuition",
        "body": "SVD is a way to separate rotation and scaling.",
        "source": "user",
        "sources": [],
    }


def test_save_and_list_card_notes(tmp_path):
    card = create_saved_card(tmp_path)

    create_response = client.post(
        f"/cards/{card.id}/notes",
        json=note_payload(),
    )

    assert create_response.status_code == 201

    created_note = create_response.json()

    assert created_note["id"]
    assert created_note["card_id"] == card.id
    assert created_note["note_type"] == "user_note"
    assert created_note["title"] == "My intuition"
    assert created_note["body"] == (
        "SVD is a way to separate rotation and scaling."
    )
    assert created_note["source"] == "user"
    assert created_note["sources"] == []

    list_response = client.get(f"/cards/{card.id}/notes")

    assert list_response.status_code == 200
    assert [
        note["id"]
        for note in list_response.json()
    ] == [created_note["id"]]


def test_update_card_note(tmp_path):
    card = create_saved_card(tmp_path)
    created_note = client.post(
        f"/cards/{card.id}/notes",
        json=note_payload(),
    ).json()

    response = client.patch(
        f"/card-notes/{created_note['id']}",
        json={
            "title": "Better explanation",
            "body": "SVD gives orthogonal directions ranked by strength.",
            "sources": [
                {
                    "title": "Course page",
                    "url": "https://ocw.mit.edu/",
                }
            ],
        },
    )

    assert response.status_code == 200

    updated_note = response.json()

    assert updated_note["id"] == created_note["id"]
    assert updated_note["title"] == "Better explanation"
    assert updated_note["body"] == (
        "SVD gives orthogonal directions ranked by strength."
    )
    assert updated_note["sources"] == [
        {
            "title": "Course page",
            "url": "https://ocw.mit.edu/",
            "accessed_at": None,
        }
    ]
    assert updated_note["updated_at"] >= created_note["updated_at"]


def test_delete_card_note(tmp_path):
    card = create_saved_card(tmp_path)
    created_note = client.post(
        f"/cards/{card.id}/notes",
        json=note_payload(),
    ).json()

    response = client.delete(f"/card-notes/{created_note['id']}")

    assert response.status_code == 204
    assert get_note(created_note["id"]) is None


def test_card_notes_return_404_for_missing_card():
    response = client.get("/cards/missing-card/notes")

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Knowledge card not found."
    }


def test_save_card_note_rejects_blank_body(tmp_path):
    card = create_saved_card(tmp_path)

    response = client.post(
        f"/cards/{card.id}/notes",
        json={
            **note_payload(),
            "body": "   ",
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Note body is required."
    }


def test_delete_card_preserves_notes_for_restore(tmp_path):
    card = create_saved_card(tmp_path)
    created_note = client.post(
        f"/cards/{card.id}/notes",
        json=note_payload(),
    ).json()

    response = client.delete(f"/cards/{card.id}")

    assert response.status_code == 204
    assert get_note(created_note["id"]) is not None
    trash_item = next(
        item
        for item in client.get("/trash").json()
        if item["entity_type"] == "knowledge_card"
        and item["entity_id"] == card.id
    )
    assert client.post(
        f"/trash/{trash_item['id']}/restore"
    ).status_code == 200
    notes = client.get(f"/cards/{card.id}/notes").json()
    assert [item["id"] for item in notes] == [created_note["id"]]
