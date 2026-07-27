from __future__ import annotations

import json

from app.chat import ChatMessage
from app.chat_grounding import (
    build_grounding_evidence,
    parse_grounded_chat_output,
)
from app.chat_service import (
    EVIDENCE_CHUNK_MAX_CHARACTERS,
    GENERATION_HISTORY_MAX_CHARACTERS,
    GENERATION_HISTORY_MESSAGES,
    RETRIEVAL_QUERY_MAX_CHARACTERS,
    _chat_citations,
    build_generation_history,
    build_retrieval_query,
    select_bounded_evidence,
)
from app.course_source import SourceSearchResult


def _message(
    sequence: int,
    *,
    role: str = "user",
    content: str,
    status: str = "complete",
) -> ChatMessage:
    return ChatMessage(
        id=f"message-{sequence}",
        conversation_id="conversation-1",
        turn_id=f"turn-{sequence}",
        sequence=sequence,
        role=role,
        content=content,
        status=status,
        answer_status=(
            "abstained"
            if role == "assistant" and status == "complete"
            else None
        ),
        error_message=(
            "Safe failure."
            if role == "assistant" and status == "failed"
            else None
        ),
    )


def _result(index: int, quote: str) -> SourceSearchResult:
    return SourceSearchResult(
        chunk_id=f"source_unit:unit-{index}",
        source_id="asset:notes",
        source_title="Notes",
        source_type="text",
        chunk_type="text",
        quote=quote,
        score=0.9 - index / 100,
        locator={
            "kind": "text_section",
            "asset_id": "notes",
            "section_number": index + 1,
        },
    )


def test_retrieval_query_uses_recent_user_questions_not_assistant_text() -> None:
    messages = [
        _message(1, content="Explain optimization."),
        _message(
            2,
            role="assistant",
            content="Assistant text must not enter retrieval.",
        ),
        _message(3, content="What is gradient descent?"),
    ]

    query = build_retrieval_query("Why does it converge?", messages)

    assert query == (
        "Explain optimization.\n"
        "What is gradient descent?\n"
        "Why does it converge?"
    )
    assert "Assistant text" not in query


def test_retrieval_query_keeps_current_question_inside_hard_budget() -> None:
    question = "q" * (RETRIEVAL_QUERY_MAX_CHARACTERS + 50)

    query = build_retrieval_query(
        question,
        [_message(1, content="old context")],
    )

    assert query == question[:RETRIEVAL_QUERY_MAX_CHARACTERS]
    assert len(query) == RETRIEVAL_QUERY_MAX_CHARACTERS


def test_generation_history_keeps_whole_recent_messages_and_budget() -> None:
    messages = [
        _message(index, content=str(index) * 1000)
        for index in range(1, 8)
    ]

    history = build_generation_history(messages)

    assert len(history) == GENERATION_HISTORY_MESSAGES
    assert [item.content[0] for item in history] == [
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
    ]
    assert sum(len(item.content) for item in history) <= (
        GENERATION_HISTORY_MAX_CHARACTERS
    )
    assert all(len(item.content) == 1000 for item in history)


def test_evidence_budget_skips_oversized_chunks_without_truncating_quotes() -> None:
    oversized = "x" * (EVIDENCE_CHUNK_MAX_CHARACTERS + 1)
    results = [_result(0, oversized)] + [
        _result(index + 1, str(index) * 2800)
        for index in range(6)
    ]

    selected = select_bounded_evidence(results)

    assert all(result.quote != oversized for result in selected)
    assert all(len(result.quote) == 2800 for result in selected)
    assert len(selected) == 5
    assert sum(len(result.quote) for result in selected) == 14000


def test_repeated_chunk_citations_share_one_snapshot_and_multiple_spans() -> None:
    evidence = build_grounding_evidence(
        [_result(0, "A canonical source chunk.")]
    )
    answer = parse_grounded_chat_output(
        json.dumps(
            {
                "status": "answered",
                "sentences": [
                    {"text": "First fact.", "evidence_ids": ["E1"]},
                    {"text": "Second fact.", "evidence_ids": ["E1"]},
                ],
            }
        ),
        evidence,
    )

    citations = _chat_citations("assistant-1", answer)

    assert len(citations) == 2
    assert citations[0].id == citations[1].id
    assert citations[0].ordinal == citations[1].ordinal == 1
    assert {
        (citation.sentence_index, citation.start_offset, citation.end_offset)
        for citation in citations
    } == {
        (0, 0, len("First fact.")),
        (
            1,
            len("First fact.") + 1,
            len("First fact. Second fact."),
        ),
    }
