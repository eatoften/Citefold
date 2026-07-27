from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from threading import Barrier

import pytest

from app.chat import ChatCitation, ChatConversation
from app.chat_store import (
    ChatEvidenceConflictError,
    ChatIdempotencyConflictError,
    ChatMessageStateConflictError,
    ChatTurnConflictError,
    clear_chat,
    complete_turn,
    create_conversation,
    delete_conversation,
    fail_turn,
    get_conversation,
    get_conversation_detail,
    get_message,
    get_turn_reservation,
    list_conversations_for_course,
    move_conversations_to_course,
    patch_conversation,
    recover_active_turns,
    refuse_turn,
    reserve_turn,
    transition_turn,
    update_conversation,
)
from app.db import configure_db, connect, get_db_path, init_db


COURSE_ID = "chat-course"
OTHER_COURSE_ID = "other-course"
SOURCE_ID = "asset:notes"
CHUNK_ID = "source_unit:notes-1"
CHUNK_TEXT = "Gradient descent updates parameters using the loss gradient."
CHUNK_HASH = sha256(CHUNK_TEXT.encode("utf-8")).hexdigest()
NOW = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)


@pytest.fixture
def chat_evidence() -> None:
    with connect() as conn:
        for course_id, title in (
            (COURSE_ID, "Machine Learning"),
            (OTHER_COURSE_ID, "Other"),
        ):
            conn.execute(
                """
                INSERT INTO courses (
                    id, title, description, created_at, updated_at
                ) VALUES (?, ?, NULL, ?, ?)
                """,
                (course_id, title, NOW.isoformat(), NOW.isoformat()),
            )
        conn.execute(
            """
            INSERT INTO sources (
                id, course_id, origin_type, origin_id, source_type, title,
                content_status, index_status, index_generation, index_model,
                index_dimension, enabled, size_bytes, mime_type,
                metadata_json, error_message, index_error,
                created_at, updated_at, indexed_at
            ) VALUES (
                ?, ?, 'source_asset', 'notes', 'text', 'Lecture notes',
                'ready', 'ready', NULL, 'test-model', 2, 1, 100,
                'text/plain', '{}', NULL, NULL, ?, ?, ?
            )
            """,
            (
                SOURCE_ID,
                COURSE_ID,
                NOW.isoformat(),
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        conn.execute(
            """
            INSERT INTO source_chunks (
                id, source_id, origin_type, origin_id, chunk_type, ordinal,
                text, text_hash, locator_json, chunker_version, is_active,
                created_at, updated_at
            ) VALUES (
                ?, ?, 'source_unit', 'notes-1', 'text', 0,
                ?, ?, ?, 'test-v1', 1, ?, ?
            )
            """,
            (
                CHUNK_ID,
                SOURCE_ID,
                CHUNK_TEXT,
                CHUNK_HASH,
                (
                    '{"schema_version":1,"kind":"text_section",'
                    '"asset_id":"notes","section_number":1,"metadata":{}}'
                ),
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )


def _conversation(
    conversation_id: str = "conversation-1",
    *,
    course_id: str = COURSE_ID,
    source_ids: list[str] | None = None,
) -> ChatConversation:
    return ChatConversation(
        id=conversation_id,
        course_id=course_id,
        title="Gradient descent",
        selected_source_ids=source_ids if source_ids is not None else [SOURCE_ID],
        created_at=NOW,
        updated_at=NOW,
    )


def _reserve(
    *,
    conversation_id: str = "conversation-1",
    request_id: str = "request-1",
    suffix: str = "1",
):
    return reserve_turn(
        conversation_id,
        turn_id=f"turn-{suffix}",
        user_message_id=f"user-{suffix}",
        assistant_message_id=f"assistant-{suffix}",
        client_request_id=request_id,
        content="What is gradient descent?",
        source_ids=[SOURCE_ID],
        provider="local",
        model="test-model",
    )


def _citation(
    message_id: str,
    answer: str,
    *,
    chunk_hash: str = CHUNK_HASH,
    source_id: str = SOURCE_ID,
    chunk_id: str = CHUNK_ID,
    source_title: str = "Lecture notes",
    asset_id: str = "notes",
) -> ChatCitation:
    cited_text = "It updates parameters using the loss gradient."
    start = answer.index(cited_text)
    return ChatCitation(
        id="citation-1",
        message_id=message_id,
        ordinal=1,
        sentence_index=0,
        start_offset=start,
        end_offset=start + len(cited_text),
        source_id=source_id,
        chunk_id=chunk_id,
        chunk_text_hash=chunk_hash,
        source_title=source_title,
        source_type="text",
        quote="updates parameters using the loss gradient",
        score=0.92,
        locator={
            "schema_version": 1,
            "kind": "text_section",
            "asset_id": asset_id,
            "section_number": 1,
            "metadata": {},
        },
        created_at=NOW,
    )


def test_conversation_and_messages_survive_reinitialization(
    chat_evidence,
) -> None:
    create_conversation(_conversation())
    reservation = _reserve()
    db_path = get_db_path()

    configure_db(db_path)
    init_db()

    detail = get_conversation_detail("conversation-1")
    assert detail is not None
    assert detail.message_count == 2
    assert [message.sequence for message in detail.messages] == [1, 2]
    replay = get_turn_reservation("conversation-1", "request-1")
    assert replay is not None
    assert replay.turn_id == reservation.turn_id
    assert replay.generation_token == reservation.generation_token


def test_conversation_crud_lists_and_updates(chat_evidence) -> None:
    conversation = _conversation()
    create_conversation(conversation)

    stored = get_conversation(conversation.id)
    assert stored is not None
    stored.title = "Optimization"
    stored.selected_source_ids = []
    stored.updated_at = datetime.now(timezone.utc)
    assert update_conversation(stored) is True

    listed = list_conversations_for_course(COURSE_ID)
    assert [item.id for item in listed] == [conversation.id]
    assert listed[0].title == "Optimization"
    assert listed[0].selected_source_ids == []


def test_same_client_request_is_idempotent_under_concurrency(
    chat_evidence,
) -> None:
    create_conversation(_conversation())
    barrier = Barrier(2)

    def run(suffix: str):
        barrier.wait()
        return _reserve(suffix=suffix)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, ("a", "b")))

    assert {result.replayed for result in results} == {False, True}
    assert len({result.turn_id for result in results}) == 1
    assert len({result.user_message.id for result in results}) == 1
    detail = get_conversation_detail("conversation-1")
    assert detail is not None
    assert len(detail.messages) == 2

    with pytest.raises(ChatIdempotencyConflictError):
        reserve_turn(
            "conversation-1",
            turn_id="different-turn",
            user_message_id="different-user",
            assistant_message_id="different-assistant",
            client_request_id="request-1",
            content="A different question",
            source_ids=[SOURCE_ID],
            provider="local",
            model="test-model",
        )


def test_conversation_scoped_idempotency_ignores_later_source_selection(
    chat_evidence,
) -> None:
    create_conversation(_conversation())
    first = reserve_turn(
        "conversation-1",
        turn_id="turn-original",
        user_message_id="user-original",
        assistant_message_id="assistant-original",
        client_request_id="request-conversation-scope",
        content="What is gradient descent?",
        source_ids=None,
        provider="local",
        model="test-model",
    )
    assert first.source_scope_mode == "conversation"
    assert first.generation_token is not None
    refuse_turn(
        first.assistant_message.id,
        generation_token=first.generation_token,
        content="There is not enough evidence.",
        refusal_reason="insufficient_evidence",
    )
    assert patch_conversation(
        "conversation-1",
        source_ids=[],
    ) is not None

    replay = reserve_turn(
        "conversation-1",
        turn_id="turn-replay",
        user_message_id="user-replay",
        assistant_message_id="assistant-replay",
        client_request_id="request-conversation-scope",
        content="What is gradient descent?",
        source_ids=None,
        provider="local",
        model="test-model",
    )

    assert replay.replayed is True
    assert replay.turn_id == first.turn_id
    assert replay.source_ids == [SOURCE_ID]
    with pytest.raises(ChatIdempotencyConflictError):
        reserve_turn(
            "conversation-1",
            turn_id="turn-explicit-replay",
            user_message_id="user-explicit-replay",
            assistant_message_id="assistant-explicit-replay",
            client_request_id="request-conversation-scope",
            content="What is gradient descent?",
            source_ids=[SOURCE_ID],
            provider="local",
            model="test-model",
        )


def test_only_one_active_turn_can_be_reserved_per_conversation(
    chat_evidence,
) -> None:
    create_conversation(_conversation())
    barrier = Barrier(2)

    def run(index: int):
        barrier.wait()
        try:
            return _reserve(
                request_id=f"request-{index}",
                suffix=str(index),
            )
        except ChatTurnConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(run, (1, 2)))

    assert sum(not isinstance(item, Exception) for item in outcomes) == 1
    assert sum(isinstance(item, ChatTurnConflictError) for item in outcomes) == 1


def test_turn_transitions_are_generation_token_cas(chat_evidence) -> None:
    create_conversation(_conversation())
    reservation = _reserve()
    token = reservation.generation_token
    assert token is not None

    assert transition_turn(
        reservation.turn_id,
        generation_token=token,
        expected_status="pending",
        status="retrieving",
        retrieval_query="gradient descent",
    )
    with pytest.raises(ChatMessageStateConflictError):
        transition_turn(
            reservation.turn_id,
            generation_token="stale-token",
            expected_status="retrieving",
            status="generating",
        )
    assert transition_turn(
        reservation.turn_id,
        generation_token=token,
        expected_status="retrieving",
        status="generating",
    )
    assert transition_turn(
        reservation.turn_id,
        generation_token=token,
        expected_status="generating",
        status="validating",
    )


def test_answer_citations_spans_and_turn_complete_atomically(
    chat_evidence,
) -> None:
    create_conversation(_conversation())
    reservation = _reserve()
    token = reservation.generation_token
    assert token is not None
    answer = "It updates parameters using the loss gradient."

    with pytest.raises(ChatEvidenceConflictError):
        complete_turn(
            reservation.assistant_message.id,
            generation_token=token,
            content=answer,
            citations=[
                _citation(
                    reservation.assistant_message.id,
                    answer,
                    chunk_hash="0" * 64,
                )
            ],
        )

    still_generating = get_message(reservation.assistant_message.id)
    assert still_generating is not None
    assert still_generating.status == "generating"
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM chat_citations"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT status FROM chat_turns WHERE id = ?",
            (reservation.turn_id,),
        ).fetchone()[0] == "pending"

    completed = complete_turn(
        reservation.assistant_message.id,
        generation_token=token,
        content=answer,
        citations=[_citation(reservation.assistant_message.id, answer)],
        provider="local",
        model="test-model",
        metadata={"retrieved": 1},
    )
    assert completed.status == "complete"
    assert completed.answer_status == "answered"
    assert len(completed.citations) == 1
    assert completed.citations[0].chunk_text_hash == CHUNK_HASH
    with connect() as conn:
        turn = conn.execute(
            """
            SELECT status, generation_token, completed_at
            FROM chat_turns WHERE id = ?
            """,
            (reservation.turn_id,),
        ).fetchone()
        assert tuple(turn) == ("completed", None, turn["completed_at"])
        assert turn["completed_at"] is not None
        assert conn.execute(
            "SELECT COUNT(*) FROM chat_citations"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM chat_citation_spans"
        ).fetchone()[0] == 1

    with pytest.raises(ChatMessageStateConflictError):
        complete_turn(
            reservation.assistant_message.id,
            generation_token=token,
            content=answer,
            citations=[_citation(reservation.assistant_message.id, answer)],
        )


def test_completion_rejects_same_course_source_outside_turn_snapshot(
    chat_evidence,
) -> None:
    foreign_source_id = "asset:unselected"
    foreign_chunk_id = "source_unit:unselected-1"
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO sources (
                id, course_id, origin_type, origin_id, source_type, title,
                content_status, index_status, enabled, metadata_json,
                created_at, updated_at
            ) VALUES (
                ?, ?, 'source_asset', 'unselected', 'text', 'Unselected notes',
                'ready', 'ready', 1, '{}', ?, ?
            )
            """,
            (
                foreign_source_id,
                COURSE_ID,
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        conn.execute(
            """
            INSERT INTO source_chunks (
                id, source_id, origin_type, origin_id, chunk_type, ordinal,
                text, text_hash, locator_json, chunker_version, is_active,
                created_at, updated_at
            ) VALUES (
                ?, ?, 'source_unit', 'unselected-1', 'text', 0,
                ?, ?, ?, 'test-v1', 1, ?, ?
            )
            """,
            (
                foreign_chunk_id,
                foreign_source_id,
                CHUNK_TEXT,
                CHUNK_HASH,
                (
                    '{"schema_version":1,"kind":"text_section",'
                    '"asset_id":"unselected","section_number":1,'
                    '"metadata":{}}'
                ),
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
    create_conversation(_conversation())
    reservation = _reserve()
    assert reservation.generation_token is not None
    answer = "It updates parameters using the loss gradient."

    with pytest.raises(
        ChatEvidenceConflictError,
        match="outside the turn source snapshot",
    ):
        complete_turn(
            reservation.assistant_message.id,
            generation_token=reservation.generation_token,
            content=answer,
            citations=[
                _citation(
                    reservation.assistant_message.id,
                    answer,
                    source_id=foreign_source_id,
                    chunk_id=foreign_chunk_id,
                    source_title="Unselected notes",
                    asset_id="unselected",
                )
            ],
        )

    active = get_message(reservation.assistant_message.id)
    assert active is not None
    assert active.status == "generating"
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM chat_citations"
        ).fetchone()[0] == 0


def test_refusal_and_failure_are_persisted(chat_evidence) -> None:
    create_conversation(_conversation("refusal"))
    refusal = _reserve(conversation_id="refusal", suffix="refusal")
    assert refusal.generation_token is not None
    refused = refuse_turn(
        refusal.assistant_message.id,
        generation_token=refusal.generation_token,
        content="The selected sources do not contain enough evidence.",
        refusal_reason="insufficient_evidence",
    )
    assert refused.status == "complete"
    assert refused.answer_status == "abstained"
    assert refused.citations == []

    create_conversation(_conversation("failure"))
    failure = _reserve(conversation_id="failure", suffix="failure")
    assert failure.generation_token is not None
    failed = fail_turn(
        failure.assistant_message.id,
        generation_token=failure.generation_token,
        safe_error_message="The local model failed. Please retry.",
    )
    assert failed.status == "failed"
    assert failed.error_message == "The local model failed. Please retry."


def test_startup_recovery_fails_orphaned_active_turn_and_allows_next(
    chat_evidence,
) -> None:
    create_conversation(_conversation())
    first = _reserve()

    assert recover_active_turns() == 1
    recovered = get_message(first.assistant_message.id)
    assert recovered is not None
    assert recovered.status == "failed"
    assert "interrupted" in (recovered.error_message or "").lower()

    second = _reserve(request_id="request-2", suffix="2")
    assert second.user_message.sequence == 3
    assert second.assistant_message.sequence == 4


def test_delete_conversation_explicitly_cleans_all_chat_tables(
    chat_evidence,
) -> None:
    create_conversation(_conversation())
    reservation = _reserve()
    assert reservation.generation_token is not None
    answer = "It updates parameters using the loss gradient."
    complete_turn(
        reservation.assistant_message.id,
        generation_token=reservation.generation_token,
        content=answer,
        citations=[_citation(reservation.assistant_message.id, answer)],
    )

    assert delete_conversation("conversation-1") is True
    assert delete_conversation("conversation-1") is False
    with connect() as conn:
        for table in (
            "chat_citation_spans",
            "chat_citations",
            "chat_messages",
            "chat_turns",
            "chat_conversations",
        ):
            assert conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0] == 0


def test_clear_chat_explicitly_cleans_all_conversations(chat_evidence) -> None:
    create_conversation(_conversation("conversation-a"))
    create_conversation(_conversation("conversation-b"))
    _reserve(conversation_id="conversation-a", suffix="a")
    _reserve(conversation_id="conversation-b", suffix="b")

    clear_chat()

    assert list_conversations_for_course(COURSE_ID) == []
    with connect() as conn:
        for table in (
            "chat_citation_spans",
            "chat_citations",
            "chat_messages",
            "chat_turns",
            "chat_conversations",
        ):
            assert conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0] == 0


def test_move_conversations_recovers_active_turns_and_keeps_history(
    chat_evidence,
) -> None:
    create_conversation(_conversation())
    reservation = _reserve()

    assert move_conversations_to_course(COURSE_ID, OTHER_COURSE_ID) == 1
    moved = get_conversation_detail("conversation-1")
    assert moved is not None
    assert moved.course_id == OTHER_COURSE_ID
    assert moved.selected_source_ids == []
    assert moved.messages[-1].status == "failed"
    with connect() as conn:
        turn = conn.execute(
            "SELECT status, source_ids_json FROM chat_turns WHERE id = ?",
            (reservation.turn_id,),
        ).fetchone()
    assert turn["status"] == "failed"
    assert SOURCE_ID in turn["source_ids_json"]
