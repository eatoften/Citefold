from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

import app.citation_target_service as citation_target_service
import app.course_source_service as course_source_service
import app.main as main
import app.notebook_note_store as notebook_note_store
import app.source_index_service as source_index_service
import app.source_search_service as source_search_service
from app.chat import ChatCitation, ChatConversation
from app.chat_store import (
    complete_turn,
    create_conversation,
    delete_conversation,
    purge_conversation,
    refuse_turn,
    reserve_turn,
)
from app.course import Course
from app.course_source_store import get_source, list_source_chunks
from app.course_store import create_course
from app.db import connect, get_db_path
from app.notebook_note import (
    NotebookNoteCreate,
    NotebookNotePromotionRequest,
    NotebookNoteUpdate,
)
from app.notebook_note_service import (
    create_course_notebook_note,
    delete_course_notebook_note,
    publish_notebook_note_as_source,
    purge_deleted_notebook_note,
    update_course_notebook_note,
)
from app.notebook_note_store import get_notebook_note
from app.source_asset import SourceAsset, SourceUnit
from app.source_asset_store import create_source_asset, replace_source_units
from app.trash_service import purge_workspace_trash_item
from app.trash_store import list_trash_items
from app.workspace_backup import (
    DATABASE_ARCHIVE_PATH,
    apply_pending_workspace_restore,
    create_workspace_backup,
    finalize_pending_workspace_restore,
    queue_workspace_restore,
)
from app.workspace_draft import WorkspaceDraftPut
from app.workspace_draft_store import get_draft, put_draft


COURSE_ID = "notes-course"
OTHER_COURSE_ID = "other-notes-course"
SOURCE_ID = "asset:grounding"
CHUNK_ID = "source_unit:grounding-1"
CHUNK_TEXT = "Gradient descent updates parameters using the loss gradient."
CHUNK_HASH = sha256(CHUNK_TEXT.encode("utf-8")).hexdigest()
NOW = datetime(2026, 7, 28, 8, tzinfo=timezone.utc)
client = TestClient(main.app, client=("127.0.0.1", 50000))


def _create_courses() -> None:
    for course_id, title in (
        (COURSE_ID, "Machine Learning"),
        (OTHER_COURSE_ID, "Other course"),
    ):
        create_course(
            Course(
                id=course_id,
                title=title,
                created_at=NOW,
                updated_at=NOW,
            )
        )


def _insert_grounding_source() -> None:
    locator = {
        "schema_version": 1,
        "kind": "text_section",
        "asset_id": "grounding",
        "section_number": 1,
        "metadata": {},
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO sources (
                id, course_id, origin_type, origin_id, source_type, title,
                content_status, index_status, index_generation, index_model,
                index_dimension, enabled, size_bytes, mime_type,
                metadata_json, error_message, index_error,
                created_at, updated_at, indexed_at
            ) VALUES (
                ?, ?, 'source_asset', 'grounding', 'text', 'Grounding notes',
                'ready', 'not_indexed', NULL, NULL, NULL, 1, 100,
                'text/plain', '{}', NULL, NULL, ?, ?, NULL
            )
            """,
            (SOURCE_ID, COURSE_ID, NOW.isoformat(), NOW.isoformat()),
        )
        conn.execute(
            """
            INSERT INTO source_chunks (
                id, source_id, origin_type, origin_id, chunk_type, ordinal,
                text, text_hash, locator_json, chunker_version, is_active,
                created_at, updated_at
            ) VALUES (?, ?, 'source_unit', 'grounding-1', 'text', 0,
                      ?, ?, ?, 'test-v1', 1, ?, ?)
            """,
            (
                CHUNK_ID,
                SOURCE_ID,
                CHUNK_TEXT,
                CHUNK_HASH,
                json.dumps(locator),
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )


def _insert_managed_grounding_source(workspace_dir: Path) -> None:
    source_path = (
        workspace_dir / "sources" / COURSE_ID / "grounding.txt"
    )
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_bytes = CHUNK_TEXT.encode("utf-8")
    source_path.write_bytes(source_bytes)
    create_source_asset(
        SourceAsset(
            id="grounding",
            course_id=COURSE_ID,
            asset_type="text",
            original_filename="Grounding notes",
            stored_path=str(source_path),
            mime_type="text/plain",
            size_bytes=len(source_bytes),
            sha256=CHUNK_HASH,
            extraction_status="ready",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    replace_source_units(
        "grounding",
        [
            SourceUnit(
                id="grounding-1",
                asset_id="grounding",
                unit_type="text",
                ordinal=0,
                text=CHUNK_TEXT,
                locator={"section_number": 1},
                created_at=NOW,
            )
        ],
    )
    course_source_service.sync_source_asset("grounding")


def _create_grounded_answer(
    *,
    conversation_id: str = "conversation-1",
    assistant_message_id: str = "assistant-1",
) -> tuple[str, str]:
    create_conversation(
        ChatConversation(
            id=conversation_id,
            course_id=COURSE_ID,
            title="Gradient descent",
            selected_source_ids=[SOURCE_ID],
            created_at=NOW,
            updated_at=NOW,
        )
    )
    reservation = reserve_turn(
        conversation_id,
        turn_id=f"turn-{assistant_message_id}",
        user_message_id=f"user-{assistant_message_id}",
        assistant_message_id=assistant_message_id,
        client_request_id=f"request-{assistant_message_id}",
        content="What is gradient descent?",
        source_ids=[SOURCE_ID],
        provider="local",
        model="test-model",
    )
    assert reservation.generation_token is not None
    answer = "It updates parameters using the loss gradient."
    cited = "It updates parameters using the loss gradient."
    complete_turn(
        assistant_message_id,
        generation_token=reservation.generation_token,
        content=answer,
        citations=[
            ChatCitation(
                id=f"chat-citation-{assistant_message_id}",
                message_id=assistant_message_id,
                ordinal=1,
                sentence_index=0,
                start_offset=answer.index(cited),
                end_offset=answer.index(cited) + len(cited),
                source_id=SOURCE_ID,
                chunk_id=CHUNK_ID,
                chunk_text_hash=CHUNK_HASH,
                source_title="Grounding notes",
                source_type="text",
                quote="updates parameters using the loss gradient",
                score=0.95,
                locator={
                    "schema_version": 1,
                    "kind": "text_section",
                    "asset_id": "grounding",
                    "section_number": 1,
                    "metadata": {},
                },
                created_at=NOW,
            )
        ],
        provider="local",
        model="test-model",
    )
    return conversation_id, assistant_message_id


def _create_free_note(body: str = "# SVD\n\nA useful decomposition.") -> dict:
    response = client.post(
        f"/courses/{COURSE_ID}/notes",
        json={"title": "SVD intuition", "body_markdown": body},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_note_crud_revision_conflict_and_course_isolation() -> None:
    _create_courses()
    created = _create_free_note()
    assert created["revision"] == 1
    assert created["origin_snapshot"] == {"origin_type": "free"}
    assert created["published_revision"] is None

    listed = client.get(f"/courses/{COURSE_ID}/notes").json()
    assert listed == [
        {
            "id": created["id"],
            "course_id": COURSE_ID,
            "title": "SVD intuition",
            "body_preview": "# SVD A useful decomposition.",
            "revision": 1,
            "origin_type": "free",
            "citation_count": 0,
            "published_snapshot_id": None,
            "published_revision": None,
            "is_source_outdated": False,
            "created_at": created["created_at"],
            "updated_at": created["updated_at"],
        }
    ]
    assert client.get(
        f"/courses/{OTHER_COURSE_ID}/notes/{created['id']}"
    ).status_code == 404
    assert client.patch(
        f"/courses/{OTHER_COURSE_ID}/notes/{created['id']}",
        json={"body_markdown": "leak", "expected_revision": 1},
    ).status_code == 404

    updated = client.patch(
        f"/courses/{COURSE_ID}/notes/{created['id']}",
        json={
            "body_markdown": "# SVD\n\nOrthogonal directions.",
            "expected_revision": 1,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    conflict = client.patch(
        f"/courses/{COURSE_ID}/notes/{created['id']}",
        json={"body_markdown": "stale", "expected_revision": 1},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["current"]["revision"] == 2


def test_chat_capture_is_idempotent_and_survives_conversation_purge() -> None:
    _create_courses()
    _insert_grounding_source()
    conversation_id, message_id = _create_grounded_answer()

    first = client.post(
        f"/courses/{COURSE_ID}/notes/from-chat/{message_id}",
        json={},
    )
    assert first.status_code == 201, first.text
    note = first.json()
    origin = note["origin_snapshot"]
    assert note["origin_type"] == "chat_answer"
    assert origin["answer_text"] == note["body_markdown"]
    assert origin["provider"] == "local"
    assert origin["model"] == "test-model"
    assert len(origin["citations"]) == 1
    note_citation = origin["citations"][0]
    assert note_citation["id"] != note_citation["origin_citation_id"]
    assert note_citation["origin_citation_id"] == (
        f"chat-citation-{message_id}"
    )
    assert note_citation["spans"] == [
        {
            "sentence_index": 0,
            "start_offset": 0,
            "end_offset": len(origin["answer_text"]),
        }
    ]

    replay = client.post(
        f"/courses/{COURSE_ID}/notes/from-chat/{message_id}",
        json={"title": "A different retry title"},
    )
    assert replay.status_code == 201
    assert replay.json()["id"] == note["id"]
    assert replay.json()["title"] == note["title"]
    assert client.post(
        f"/courses/{OTHER_COURSE_ID}/notes/from-chat/{message_id}",
        json={},
    ).status_code == 404

    assert delete_conversation(conversation_id)
    assert purge_conversation(conversation_id)
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM chat_citations"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM notebook_note_citations"
        ).fetchone()[0] == 1

    target = client.get(
        f"/courses/{COURSE_ID}/chat/citations/{note_citation['id']}/target"
    )
    assert target.status_code == 200, target.text
    assert target.json()["availability"] == "snapshot_only"
    assert client.get(
        f"/courses/{OTHER_COURSE_ID}/chat/citations/"
        f"{note_citation['id']}/target"
    ).status_code == 404

    assert client.delete(
        f"/courses/{COURSE_ID}/notes/{note['id']}?expected_revision=1"
    ).status_code == 204
    assert client.get(
        f"/courses/{COURSE_ID}/chat/citations/{note_citation['id']}/target"
    ).status_code == 404
    note_trash = next(
        item
        for item in list_trash_items(course_id=COURSE_ID)
        if item.entity_type == "notebook_note"
    )
    assert client.post(f"/trash/{note_trash.id}/restore").status_code == 200
    restored_target = client.get(
        f"/courses/{COURSE_ID}/chat/citations/{note_citation['id']}/target"
    )
    assert restored_target.status_code == 200
    assert restored_target.json()["citation_id"] == note_citation["id"]


def test_chat_capture_rejects_non_answer_and_ungrounded_states() -> None:
    _create_courses()
    _insert_grounding_source()
    create_conversation(
        ChatConversation(
            id="invalid-capture-conversation",
            course_id=COURSE_ID,
            title="Invalid capture",
            selected_source_ids=[SOURCE_ID],
            created_at=NOW,
            updated_at=NOW,
        )
    )
    generating = reserve_turn(
        "invalid-capture-conversation",
        turn_id="invalid-generating-turn",
        user_message_id="invalid-user",
        assistant_message_id="invalid-generating-assistant",
        client_request_id="invalid-generating-request",
        content="What is gradient descent?",
        source_ids=[SOURCE_ID],
        provider="local",
        model="test-model",
    )
    assert client.post(
        f"/courses/{COURSE_ID}/notes/from-chat/"
        f"{generating.assistant_message.id}",
        json={},
    ).status_code == 400
    assert client.post(
        f"/courses/{COURSE_ID}/notes/from-chat/"
        f"{generating.user_message.id}",
        json={},
    ).status_code == 400
    assert generating.generation_token is not None
    refused = refuse_turn(
        generating.assistant_message.id,
        generation_token=generating.generation_token,
        content="The selected sources do not contain enough evidence.",
        refusal_reason="insufficient_evidence",
    )
    assert refused.answer_status == "abstained"
    assert client.post(
        f"/courses/{COURSE_ID}/notes/from-chat/{refused.id}",
        json={},
    ).status_code == 400


def test_explicit_promotion_refresh_reconcile_and_trash_lifecycle() -> None:
    _create_courses()
    note = _create_free_note()
    assert all(
        source.id != f"note:{note['id']}"
        for source in course_source_service.list_course_sources(COURSE_ID)
    )

    first = client.post(
        f"/courses/{COURSE_ID}/notes/{note['id']}/source",
        json={"expected_revision": 1},
    )
    assert first.status_code == 200, first.text
    first_result = first.json()
    assert first_result["replayed"] is False
    assert first_result["source"]["id"] == f"note:{note['id']}"
    assert first_result["source"]["origin_type"] == "notebook_note"
    assert first_result["source"]["origin_id"] == note["id"]
    assert first_result["source"]["index_status"] == "not_indexed"
    snapshot_one = first_result["snapshot"]
    chunks_one = list_source_chunks(first_result["source"]["id"])
    assert chunks_one
    assert chunks_one[0].origin_type == "notebook_note_snapshot"
    assert chunks_one[0].locator.kind == "note_section"
    assert chunks_one[0].locator.snapshot_id == snapshot_one["id"]

    replay = client.post(
        f"/courses/{COURSE_ID}/notes/{note['id']}/source",
        json={"expected_revision": 1},
    ).json()
    assert replay["replayed"] is True
    assert replay["snapshot"]["id"] == snapshot_one["id"]

    updated = client.patch(
        f"/courses/{COURSE_ID}/notes/{note['id']}",
        json={
            "body_markdown": "# SVD\n\nA deliberately refreshed source.",
            "expected_revision": 1,
        },
    ).json()
    assert updated["revision"] == 2
    assert updated["published_revision"] == 1
    assert updated["is_source_outdated"] is True
    assert [
        chunk.id for chunk in list_source_chunks(first_result["source"]["id"])
    ] == [chunk.id for chunk in chunks_one]
    stale = client.post(
        f"/courses/{COURSE_ID}/notes/{note['id']}/source",
        json={"expected_revision": 1},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["current"]["revision"] == 2

    refreshed = client.post(
        f"/courses/{COURSE_ID}/notes/{note['id']}/source",
        json={"expected_revision": 2},
    )
    assert refreshed.status_code == 200
    refreshed_result = refreshed.json()
    assert refreshed_result["snapshot"]["id"] != snapshot_one["id"]
    assert refreshed_result["note"]["published_revision"] == 2
    chunks_two = list_source_chunks(first_result["source"]["id"])
    assert chunks_two[0].locator.snapshot_id == (
        refreshed_result["snapshot"]["id"]
    )
    assert chunks_two[0].id != chunks_one[0].id

    course_source_service.reconcile_course_sources(COURSE_ID)
    reconciled = get_source(f"note:{note['id']}")
    assert reconciled is not None
    assert reconciled.metadata["note_revision"] == 2

    draft_id = f"notebook-note:{note['id']}"
    put_draft(
        draft_id,
        WorkspaceDraftPut(
            course_id=COURSE_ID,
            draft_type="notebook_note",
            entity_id=note["id"],
            payload={"body_markdown": "unsaved"},
        ),
    )
    deleted = client.delete(
        f"/courses/{COURSE_ID}/notes/{note['id']}"
        "?expected_revision=2"
    )
    assert deleted.status_code == 204
    assert get_source(f"note:{note['id']}") is not None
    assert all(
        source.id != f"note:{note['id']}"
        for source in course_source_service.list_course_sources(COURSE_ID)
    )
    note_trash = next(
        item
        for item in list_trash_items(course_id=COURSE_ID)
        if item.entity_type == "notebook_note"
    )
    restored = client.post(f"/trash/{note_trash.id}/restore")
    assert restored.status_code == 200
    assert any(
        source.id == f"note:{note['id']}"
        for source in course_source_service.list_course_sources(COURSE_ID)
    )
    assert [
        chunk.id for chunk in list_source_chunks(f"note:{note['id']}")
    ] == [chunk.id for chunk in chunks_two]

    assert client.delete(
        f"/courses/{COURSE_ID}/notes/{note['id']}"
        "?expected_revision=2"
    ).status_code == 204
    note_trash = next(
        item
        for item in list_trash_items(course_id=COURSE_ID)
        if item.entity_type == "notebook_note"
    )
    assert client.delete(f"/trash/{note_trash.id}").status_code == 200
    assert get_notebook_note(
        COURSE_ID,
        note["id"],
        include_deleted=True,
    ) is None
    assert get_source(f"note:{note['id']}") is None
    assert get_draft(draft_id) is None
    with connect() as conn:
        assert conn.execute(
            """
            SELECT COUNT(*) FROM notebook_note_source_snapshots
            WHERE note_id = ?
            """,
            (note["id"],),
        ).fetchone()[0] == 0


def test_promotion_rolls_back_snapshot_when_source_projection_fails(
    monkeypatch,
) -> None:
    _create_courses()
    note = create_course_notebook_note(
        COURSE_ID,
        NotebookNoteCreate(
            title="Atomic publication",
            body_markdown="The snapshot and Source commit together.",
        ),
    )

    def fail_projection(*_args, **_kwargs):
        raise RuntimeError("injected projection failure")

    monkeypatch.setattr(
        notebook_note_store,
        "replace_source_projection_in_connection",
        fail_projection,
    )
    with pytest.raises(RuntimeError, match="injected projection failure"):
        publish_notebook_note_as_source(
            COURSE_ID,
            note.id,
            NotebookNotePromotionRequest(expected_revision=1),
        )

    assert get_source(f"note:{note.id}") is None
    with connect() as conn:
        assert conn.execute(
            """
            SELECT COUNT(*) FROM notebook_note_source_snapshots
            WHERE note_id = ?
            """,
            (note.id,),
        ).fetchone()[0] == 0


def test_published_note_is_searchable_and_trash_removes_source_scope(
    monkeypatch,
) -> None:
    _create_courses()
    note = _create_free_note(
        "# Optimization\n\nMomentum smooths gradient updates."
    )
    promotion = client.post(
        f"/courses/{COURSE_ID}/notes/{note['id']}/source",
        json={"expected_revision": 1},
    )
    assert promotion.status_code == 200
    source_id = promotion.json()["source"]["id"]

    class FakeEmbedder:
        model_name = "test/notebook-note-embedding-v1"
        embedding_dimension = 2

        def embed_texts(self, texts, *, batch_size=None):
            del batch_size
            return [
                (
                    [1.0, 0.0]
                    if "momentum" in text.lower()
                    else [0.5, 0.5]
                )
                for text in texts
            ]

    fake = FakeEmbedder()
    monkeypatch.setattr(
        source_index_service,
        "SentenceTransformerEmbedder",
        lambda: fake,
    )
    monkeypatch.setattr(
        source_search_service,
        "SentenceTransformerEmbedder",
        lambda: fake,
    )

    searched = client.post(
        f"/courses/{COURSE_ID}/sources/search",
        json={
            "question": "How does momentum update parameters?",
            "source_ids": [source_id],
            "top_k": 5,
        },
    )
    assert searched.status_code == 200, searched.text
    assert searched.json()["results"][0]["source_id"] == source_id
    assert searched.json()["results"][0]["locator"]["kind"] == "note_section"

    assert client.delete(
        f"/courses/{COURSE_ID}/notes/{note['id']}?expected_revision=1"
    ).status_code == 204
    hidden = client.post(
        f"/courses/{COURSE_ID}/sources/search",
        json={
            "question": "How does momentum update parameters?",
            "source_ids": [source_id],
            "top_k": 5,
        },
    )
    assert hidden.status_code == 400

    note_trash = next(
        item
        for item in list_trash_items(course_id=COURSE_ID)
        if item.entity_type == "notebook_note"
    )
    assert client.post(f"/trash/{note_trash.id}/restore").status_code == 200
    restored = client.post(
        f"/courses/{COURSE_ID}/sources/search",
        json={
            "question": "How does momentum update parameters?",
            "source_ids": [source_id],
            "top_k": 5,
        },
    )
    assert restored.status_code == 200
    assert restored.json()["results"][0]["source_id"] == source_id


def test_published_note_source_grounds_real_async_chat_turn(
    monkeypatch,
) -> None:
    _create_courses()
    note = _create_free_note(
        "# Momentum\n\n"
        "Momentum smooths gradient updates and reduces oscillation."
    )
    promoted = client.post(
        f"/courses/{COURSE_ID}/notes/{note['id']}/source",
        json={"expected_revision": 1},
    )
    assert promoted.status_code == 200, promoted.text
    promotion = promoted.json()
    source = promotion["source"]
    snapshot = promotion["snapshot"]
    chunks = list_source_chunks(source["id"])
    expected_chunk = next(
        chunk
        for chunk in chunks
        if "reduces oscillation" in chunk.text
    )

    class FakeEmbedder:
        model_name = "test/notebook-note-chat-embedding-v1"
        embedding_dimension = 2

        def embed_texts(self, texts, *, batch_size=None):
            del batch_size
            return [[1.0, 0.0] for _ in texts]

    class FakeLLM:
        def __init__(self) -> None:
            self.settings = SimpleNamespace(
                provider="test-local",
                model="test-note-chat-model",
                max_tokens=2048,
            )
            self.calls = []

        def create_chat_completion(self, messages, **kwargs) -> str:
            self.calls.append((list(messages), dict(kwargs)))
            return json.dumps(
                {
                    "status": "answered",
                    "sentences": [
                        {
                            "text": (
                                "Momentum reduces oscillation by "
                                "smoothing gradient updates."
                            ),
                            "evidence_ids": ["E1"],
                        }
                    ],
                }
            )

    embedder = FakeEmbedder()
    llm = FakeLLM()
    monkeypatch.setattr(main, "get_llm_client", lambda: llm)
    monkeypatch.setattr(
        source_index_service,
        "SentenceTransformerEmbedder",
        lambda: embedder,
    )
    monkeypatch.setattr(
        source_search_service,
        "SentenceTransformerEmbedder",
        lambda: embedder,
    )

    conversation_response = client.post(
        f"/courses/{COURSE_ID}/chat/conversations",
        json={
            "title": "Notebook source grounding",
            "source_ids": [source["id"]],
        },
    )
    assert conversation_response.status_code == 201, (
        conversation_response.text
    )
    conversation = conversation_response.json()
    assert conversation["selected_source_ids"] == [source["id"]]

    queued = client.post(
        f"/chat/conversations/{conversation['id']}/message-tasks",
        json={
            "content": "How does momentum affect optimization?",
            "client_request_id": "note-source-chat-task",
            "source_ids": [source["id"]],
        },
    )
    assert queued.status_code == 202, queued.text
    task_id = queued.json()["id"]

    deadline = monotonic() + 5
    task = queued.json()
    while task["status"] not in {"succeeded", "failed", "canceled"}:
        assert monotonic() < deadline, task
        sleep(0.01)
        polled = client.get(f"/tasks/{task_id}")
        assert polled.status_code == 200, polled.text
        task = polled.json()

    assert task["status"] == "succeeded", task
    assert task["result"] is not None
    assistant = task["result"]["turn"]["assistant_message"]
    assert assistant["answer_status"] == "answered"
    assert assistant["content"] == (
        "Momentum reduces oscillation by smoothing gradient updates."
    )
    assert len(llm.calls) == 1
    assert len(assistant["citations"]) == 1
    citation = assistant["citations"][0]
    assert citation["source_id"] == source["id"] == f"note:{note['id']}"
    assert citation["chunk_id"] == expected_chunk.id
    assert citation["chunk_text_hash"] == expected_chunk.text_hash
    assert citation["quote"] == expected_chunk.text
    assert citation["locator"] == expected_chunk.locator.model_dump(
        mode="json"
    )
    assert citation["locator"]["kind"] == "note_section"
    assert citation["locator"]["note_id"] == note["id"]
    assert citation["locator"]["snapshot_id"] == snapshot["id"]

    persisted = client.get(
        f"/chat/conversations/{conversation['id']}"
    )
    assert persisted.status_code == 200, persisted.text
    detail = persisted.json()
    assert detail["selected_source_ids"] == [source["id"]]
    assert detail["messages"][-1] == assistant

    target = client.get(
        f"/courses/{COURSE_ID}/chat/citations/{citation['id']}/target"
    )
    assert target.status_code == 200, target.text
    target_payload = target.json()
    assert target_payload["availability"] == "available"
    assert target_payload["media_kind"] == "text"
    assert target_payload["media_url"] is None
    assert target_payload["target_chunk_id"] == expected_chunk.id
    assert target_payload["locator"] == citation["locator"]
    assert any(
        item["chunk_id"] == expected_chunk.id
        and item["text"] == expected_chunk.text
        for item in target_payload["context"]
    )


def test_note_source_citation_is_available_without_a_managed_file() -> None:
    _create_courses()
    note = _create_free_note("# Momentum\n\nMomentum smooths gradient updates.")
    promotion = client.post(
        f"/courses/{COURSE_ID}/notes/{note['id']}/source",
        json={"expected_revision": 1},
    ).json()
    source = promotion["source"]
    chunk = list_source_chunks(source["id"])[0]
    answer = "Momentum smooths gradient updates."
    create_conversation(
        ChatConversation(
            id="note-source-conversation",
            course_id=COURSE_ID,
            title="Momentum",
            selected_source_ids=[source["id"]],
            created_at=NOW,
            updated_at=NOW,
        )
    )
    reservation = reserve_turn(
        "note-source-conversation",
        turn_id="note-source-turn",
        user_message_id="note-source-user",
        assistant_message_id="note-source-assistant",
        client_request_id="note-source-request",
        content="What does momentum do?",
        source_ids=[source["id"]],
        provider="local",
        model="test-model",
    )
    assert reservation.generation_token is not None
    complete_turn(
        "note-source-assistant",
        generation_token=reservation.generation_token,
        content=answer,
        citations=[
            ChatCitation(
                id="note-source-citation",
                message_id="note-source-assistant",
                ordinal=1,
                sentence_index=0,
                start_offset=0,
                end_offset=len(answer),
                source_id=source["id"],
                chunk_id=chunk.id,
                chunk_text_hash=chunk.text_hash,
                source_title=source["title"],
                source_type="text",
                quote="Momentum smooths gradient updates.",
                score=0.99,
                locator=chunk.locator.model_dump(mode="json"),
                created_at=NOW,
            )
        ],
    )

    target = client.get(
        f"/courses/{COURSE_ID}/chat/citations/note-source-citation/target"
    )
    assert target.status_code == 200, target.text
    assert target.json()["availability"] == "available"
    assert target.json()["media_kind"] == "text"
    assert target.json()["locator"]["note_id"] == note["id"]
    assert target.json()["media_url"] is None
    original_snapshot_id = target.json()["locator"]["snapshot_id"]
    original_chunk_id = target.json()["target_chunk_id"]

    updated = client.patch(
        f"/courses/{COURSE_ID}/notes/{note['id']}",
        json={
            "body_markdown": (
                "# Momentum\n\n"
                "The refreshed revision discusses Nesterov acceleration."
            ),
            "expected_revision": 1,
        },
    ).json()
    assert updated["revision"] == 2
    refreshed = client.post(
        f"/courses/{COURSE_ID}/notes/{note['id']}/source",
        json={"expected_revision": 2},
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["snapshot"]["id"] != original_snapshot_id

    historical = client.get(
        f"/courses/{COURSE_ID}/chat/citations/note-source-citation/target"
    )
    assert historical.status_code == 200
    historical_target = historical.json()
    assert historical_target["availability"] == "available"
    assert historical_target["target_chunk_id"] == original_chunk_id
    assert all(
        item["locator"]["snapshot_id"] == original_snapshot_id
        for item in historical_target["context"]
    )
    historical_text = " ".join(
        item["text"] for item in historical_target["context"]
    )
    assert "Momentum smooths gradient updates." in historical_text
    assert "Nesterov acceleration" not in historical_text
    assert client.get(
        "/courses/"
        f"{COURSE_ID}/chat/citations/note-source-citation/content"
    ).status_code == 410

    delete_course_notebook_note(
        COURSE_ID,
        note["id"],
        expected_revision=2,
    )
    tombstoned = client.get(
        f"/courses/{COURSE_ID}/chat/citations/note-source-citation/target"
    )
    assert tombstoned.status_code == 200
    assert tombstoned.json()["availability"] == "snapshot_only"
    note_trash = next(
        item
        for item in list_trash_items(course_id=COURSE_ID)
        if item.entity_type == "notebook_note"
    )
    assert client.post(f"/trash/{note_trash.id}/restore").status_code == 200
    restored = client.get(
        f"/courses/{COURSE_ID}/chat/citations/note-source-citation/target"
    )
    assert restored.status_code == 200
    assert restored.json()["availability"] == "available"
    assert restored.json()["target_chunk_id"] == original_chunk_id


def test_course_purge_removes_notes_sources_and_all_course_drafts(
    tmp_path: Path,
) -> None:
    _create_courses()
    note = _create_free_note()
    client.post(
        f"/courses/{COURSE_ID}/notes/{note['id']}/source",
        json={"expected_revision": 1},
    )
    put_draft(
        "unrelated-course-draft",
        WorkspaceDraftPut(
            course_id=COURSE_ID,
            draft_type="chat_composer",
            payload={"text": "draft"},
        ),
    )
    from app.course_service import delete_video_course

    delete_video_course(COURSE_ID)
    course_trash = next(
        item
        for item in list_trash_items(course_id=COURSE_ID)
        if item.entity_type == "course"
    )
    purge_workspace_trash_item(course_trash.id, artifact_root=tmp_path)
    assert get_source(f"note:{note['id']}") is None
    assert get_draft("unrelated-course-draft") is None
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM notebook_notes WHERE course_id = ?",
            (COURSE_ID,),
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM courses WHERE id = ?",
            (COURSE_ID,),
        ).fetchone()[0] == 0


def test_reconcile_and_promotion_share_one_projection_lock(
    monkeypatch,
) -> None:
    _create_courses()
    note = create_course_notebook_note(
        COURSE_ID,
        NotebookNoteCreate(title="Lock", body_markdown="revision one"),
    )
    publish_notebook_note_as_source(
        COURSE_ID,
        note.id,
        NotebookNotePromotionRequest(expected_revision=1),
    )
    updated = update_course_notebook_note(
        COURSE_ID,
        note.id,
        NotebookNoteUpdate(
            body_markdown="revision two",
            expected_revision=1,
        ),
    )
    entered = Event()
    release = Event()
    promotion_finished = Event()
    original_replace = (
        course_source_service.replace_course_source_projection
    )

    def blocked_replace(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original_replace(*args, **kwargs)

    monkeypatch.setattr(
        course_source_service,
        "replace_course_source_projection",
        blocked_replace,
    )

    def promote():
        result = publish_notebook_note_as_source(
            COURSE_ID,
            note.id,
            NotebookNotePromotionRequest(
                expected_revision=updated.revision,
            ),
        )
        promotion_finished.set()
        return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        reconcile_future = executor.submit(
            course_source_service.reconcile_course_sources,
            COURSE_ID,
        )
        assert entered.wait(5)
        promotion_future = executor.submit(promote)
        assert not promotion_finished.wait(0.2)
        release.set()
        reconcile_future.result(timeout=5)
        promotion_future.result(timeout=5)

    source = get_source(f"note:{note.id}")
    assert source is not None
    assert source.metadata["note_revision"] == 2


def test_reconcile_and_purge_cannot_resurrect_note_source(
    monkeypatch,
) -> None:
    _create_courses()
    note = create_course_notebook_note(
        COURSE_ID,
        NotebookNoteCreate(title="Purge lock", body_markdown="published"),
    )
    publish_notebook_note_as_source(
        COURSE_ID,
        note.id,
        NotebookNotePromotionRequest(expected_revision=1),
    )
    delete_course_notebook_note(
        COURSE_ID,
        note.id,
        expected_revision=1,
    )
    entered = Event()
    release = Event()
    purge_finished = Event()
    original_replace = (
        course_source_service.replace_course_source_projection
    )

    def blocked_replace(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original_replace(*args, **kwargs)

    monkeypatch.setattr(
        course_source_service,
        "replace_course_source_projection",
        blocked_replace,
    )

    def purge():
        purge_deleted_notebook_note(note.id)
        purge_finished.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        reconcile_future = executor.submit(
            course_source_service.reconcile_course_sources,
            COURSE_ID,
        )
        assert entered.wait(5)
        purge_future = executor.submit(purge)
        assert not purge_finished.wait(0.2)
        release.set()
        reconcile_future.result(timeout=5)
        purge_future.result(timeout=5)

    assert get_source(f"note:{note.id}") is None
    assert get_notebook_note(
        COURSE_ID,
        note.id,
        include_deleted=True,
    ) is None


def test_workspace_backup_contains_v8_note_lineage(tmp_path: Path) -> None:
    _create_courses()
    note = _create_free_note()
    client.post(
        f"/courses/{COURSE_ID}/notes/{note['id']}/source",
        json={"expected_revision": 1},
    )
    backup = create_workspace_backup(
        db_path=get_db_path(),
        data_dir=tmp_path,
    )
    assert backup.schema_version == 9
    extracted_db = tmp_path / "extracted.sqlite3"
    with ZipFile(backup.path) as archive:
        extracted_db.write_bytes(archive.read(DATABASE_ARCHIVE_PATH))
    with sqlite3.connect(extracted_db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM notebook_notes"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM notebook_note_source_snapshots"
        ).fetchone()[0] == 1


def test_workspace_backup_restore_round_trip_preserves_published_chat_note(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create_courses()
    db_path = get_db_path()
    workspace_dir = db_path.parent
    managed_paths = (
        citation_target_service.get_app_path_settings().model_copy(
            update={
                "data_dir": workspace_dir,
                "db_path": db_path,
                "source_dir": workspace_dir / "sources",
            }
        )
    )
    monkeypatch.setattr(
        citation_target_service,
        "get_app_path_settings",
        lambda: managed_paths,
    )
    _insert_managed_grounding_source(workspace_dir)
    _, message_id = _create_grounded_answer(
        conversation_id="backup-note-conversation",
        assistant_message_id="backup-note-assistant",
    )
    captured_response = client.post(
        f"/courses/{COURSE_ID}/notes/from-chat/{message_id}",
        json={"title": "Gradient descent answer"},
    )
    assert captured_response.status_code == 201, captured_response.text
    captured = captured_response.json()
    note_id = captured["id"]
    note_citation_id = captured["origin_snapshot"]["citations"][0]["id"]

    published_response = client.post(
        f"/courses/{COURSE_ID}/notes/{note_id}/source",
        json={"expected_revision": 1},
    )
    assert published_response.status_code == 200, published_response.text
    published = published_response.json()
    source_id = published["source"]["id"]
    snapshot_id = published["snapshot"]["id"]
    backup_note = client.get(
        f"/courses/{COURSE_ID}/notes/{note_id}"
    ).json()
    backup_source = get_source(source_id)
    assert backup_source is not None
    backup_chunks = list_source_chunks(source_id)
    assert backup_chunks
    backup_citation_response = client.get(
        f"/courses/{COURSE_ID}/chat/citations/"
        f"{note_citation_id}/target"
    )
    assert backup_citation_response.status_code == 200
    backup_citation_target = backup_citation_response.json()
    assert backup_citation_target["availability"] == "available"

    backup = create_workspace_backup(
        db_path=db_path,
        data_dir=workspace_dir,
        backup_dir=tmp_path / "note-backups",
    )

    changed_response = client.patch(
        f"/courses/{COURSE_ID}/notes/{note_id}",
        json={
            "title": "Changed after backup",
            "body_markdown": "This revision must disappear after restore.",
            "expected_revision": 1,
        },
    )
    assert changed_response.status_code == 200
    assert changed_response.json()["revision"] == 2
    republished_response = client.post(
        f"/courses/{COURSE_ID}/notes/{note_id}/source",
        json={"expected_revision": 2},
    )
    assert republished_response.status_code == 200
    changed_snapshot_id = republished_response.json()["snapshot"]["id"]
    assert changed_snapshot_id != snapshot_id
    assert client.delete(
        f"/courses/{COURSE_ID}/notes/{note_id}?expected_revision=2"
    ).status_code == 204
    note_trash = next(
        item
        for item in list_trash_items(course_id=COURSE_ID)
        if item.entity_type == "notebook_note"
    )
    assert client.delete(f"/trash/{note_trash.id}").status_code == 200
    assert get_notebook_note(
        COURSE_ID,
        note_id,
        include_deleted=True,
    ) is None
    assert get_source(source_id) is None
    assert client.get(
        f"/courses/{COURSE_ID}/chat/citations/"
        f"{note_citation_id}/target"
    ).status_code == 404

    pending = queue_workspace_restore(
        backup.path,
        data_dir=workspace_dir,
    )
    staged = apply_pending_workspace_restore(
        db_path=db_path,
        data_dir=workspace_dir,
    )
    assert staged is not None
    assert staged.restore_id == pending.restore_id
    assert staged.status == "staged"

    # Production performs this reconciliation gate during the restart that
    # applies a queued restore, before committing the restore receipt.
    main._initialize_workspace_before_task_dispatch(main.app)
    restored_result = finalize_pending_workspace_restore(
        staged.restore_id,
        db_path=db_path,
        data_dir=workspace_dir,
    )
    assert restored_result.status == "applied"

    restored_note_response = client.get(
        f"/courses/{COURSE_ID}/notes/{note_id}"
    )
    assert restored_note_response.status_code == 200
    restored_note = restored_note_response.json()
    assert restored_note == backup_note
    assert restored_note["revision"] == 1
    assert restored_note["origin_type"] == "chat_answer"
    assert restored_note["origin_snapshot"] == captured["origin_snapshot"]
    assert restored_note["published_snapshot_id"] == snapshot_id
    assert restored_note["published_revision"] == 1
    assert restored_note["is_source_outdated"] is False

    restored_source = get_source(source_id)
    assert restored_source is not None
    assert restored_source.id == backup_source.id
    assert restored_source.course_id == backup_source.course_id
    assert restored_source.origin_type == backup_source.origin_type
    assert restored_source.origin_id == backup_source.origin_id
    assert restored_source.source_type == backup_source.source_type
    assert restored_source.title == backup_source.title
    assert restored_source.metadata == backup_source.metadata
    assert restored_source.metadata["note_revision"] == 1
    assert restored_source.metadata["snapshot_id"] == snapshot_id

    restored_chunks = list_source_chunks(source_id)
    assert [
        (
            chunk.id,
            chunk.origin_type,
            chunk.origin_id,
            chunk.ordinal,
            chunk.text,
            chunk.text_hash,
            chunk.locator.model_dump(mode="json"),
        )
        for chunk in restored_chunks
    ] == [
        (
            chunk.id,
            chunk.origin_type,
            chunk.origin_id,
            chunk.ordinal,
            chunk.text,
            chunk.text_hash,
            chunk.locator.model_dump(mode="json"),
        )
        for chunk in backup_chunks
    ]
    assert all(
        chunk.locator.snapshot_id == snapshot_id
        for chunk in restored_chunks
    )

    with connect() as conn:
        restored_snapshots = conn.execute(
            """
            SELECT
                id, note_id, course_id, note_revision, title,
                body_markdown, content_hash
            FROM notebook_note_source_snapshots
            WHERE note_id = ?
            ORDER BY note_revision
            """,
            (note_id,),
        ).fetchall()
    assert [dict(row) for row in restored_snapshots] == [
        {
            "id": snapshot_id,
            "note_id": note_id,
            "course_id": COURSE_ID,
            "note_revision": 1,
            "title": backup_note["title"],
            "body_markdown": backup_note["body_markdown"],
            "content_hash": published["snapshot"]["content_hash"],
        }
    ]
    assert all(
        row["id"] != changed_snapshot_id
        for row in restored_snapshots
    )

    restored_citation_response = client.get(
        f"/courses/{COURSE_ID}/chat/citations/"
        f"{note_citation_id}/target"
    )
    assert restored_citation_response.status_code == 200
    assert restored_citation_response.json() == backup_citation_target
