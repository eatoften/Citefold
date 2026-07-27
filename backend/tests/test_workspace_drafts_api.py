from fastapi.testclient import TestClient

from app.course import DEFAULT_COURSE_ID
from app.main import app
from app.workspace_draft_store import (
    clear_drafts,
    get_draft,
    put_draft,
)
from app.workspace_draft import WorkspaceDraftPut


client = TestClient(app)


def test_workspace_draft_round_trip_and_revision_conflict():
    draft_id = "study-document:document-1"
    first = client.put(
        f"/workspace/drafts/{draft_id}",
        json={
            "course_id": DEFAULT_COURSE_ID,
            "draft_type": "study_document",
            "entity_id": "document-1",
            "payload": {"title": "Recovered", "body": "First"},
            "expected_revision": 0,
            "base_updated_at": "2026-07-27T01:00:00+00:00",
        },
    )
    assert first.status_code == 200
    assert first.json()["revision"] == 1

    second = client.put(
        f"/workspace/drafts/{draft_id}",
        json={
            "course_id": DEFAULT_COURSE_ID,
            "draft_type": "study_document",
            "entity_id": "document-1",
            "payload": {"title": "Recovered", "body": "Newest"},
            "expected_revision": 1,
        },
    )
    assert second.status_code == 200
    assert second.json()["revision"] == 2

    stale = client.put(
        f"/workspace/drafts/{draft_id}",
        json={
            "course_id": DEFAULT_COURSE_ID,
            "draft_type": "study_document",
            "entity_id": "document-1",
            "payload": {"body": "Stale"},
            "expected_revision": 1,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["current"]["revision"] == 2
    assert stale.json()["detail"]["current"]["payload"]["body"] == "Newest"

    loaded = client.get(f"/workspace/drafts/{draft_id}")
    assert loaded.status_code == 200
    assert loaded.json()["payload"]["body"] == "Newest"


def test_workspace_draft_scope_list_and_conditional_delete():
    for draft_id, draft_type in (
        ("chat-composer:new", "chat_composer"),
        ("card-editor:one", "card_editor"),
    ):
        response = client.put(
            f"/workspace/drafts/{draft_id}",
            json={
                "course_id": DEFAULT_COURSE_ID,
                "draft_type": draft_type,
                "payload": {"value": draft_id},
                "expected_revision": 0,
            },
        )
        assert response.status_code == 200

    listed = client.get(
        "/workspace/drafts",
        params={
            "course_id": DEFAULT_COURSE_ID,
            "draft_type": "chat_composer",
        },
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [
        "chat-composer:new"
    ]

    conflict = client.delete(
        "/workspace/drafts/chat-composer%3Anew",
        params={"expected_revision": 2},
    )
    assert conflict.status_code == 409
    deleted = client.delete(
        "/workspace/drafts/chat-composer%3Anew",
        params={"expected_revision": 1},
    )
    assert deleted.status_code == 204
    assert client.get(
        "/workspace/drafts/chat-composer%3Anew"
    ).status_code == 404


def test_workspace_draft_store_compare_and_swap_keeps_latest():
    clear_drafts()
    request = WorkspaceDraftPut(
        course_id=DEFAULT_COURSE_ID,
        draft_type="generated_cards",
        payload={"cards": [1]},
        expected_revision=0,
    )
    first = put_draft("generated:one", request)
    assert first.revision == 1
    assert get_draft(first.id) == first
