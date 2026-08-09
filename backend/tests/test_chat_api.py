from __future__ import annotations

import json
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.chat_service as chat_service
import app.chat_store as chat_store
import app.main as main
import app.rag_service as rag_service
import app.source_search_service as source_search_service
from app.chat import ChatMessageCreate
from app.chat_graph import ChatGraphContext
from app.chat_grounding import INSUFFICIENT_EVIDENCE_MESSAGE
from app.chat_service import (
    SAFE_GENERATION_MESSAGE,
    SAFE_RETRIEVAL_MESSAGE,
    SAFE_SOURCE_CHANGED_MESSAGE,
    SAFE_TIMEOUT_MESSAGE,
)
from app.course import Course, CourseCreate
from app.course_service import create_video_course
from app.course_source import (
    CourseSource,
    CourseSourceChunk,
    PdfPageLocator,
    SourceSearchResponse,
    SourceSearchResult,
    hash_source_chunk_text,
)
from app.course_source_store import (
    delete_source_projection,
    replace_source_projection,
    set_source_enabled,
)
from app.llm_client import LLMTimeoutError
from app.rag import RagRetrieveResponse
from app.source_asset import SourceAsset
from app.source_asset_store import create_source_asset
from app.source_search_service import SourceSearchError


class RecordingLLM:
    def __init__(self, outcomes: list[object]) -> None:
        self.settings = SimpleNamespace(
            provider="test-local",
            model="test-chat-model",
            max_tokens=2048,
        )
        self.outcomes = list(outcomes)
        self.calls: list[tuple[list[object], dict[str, object]]] = []

    def create_chat_completion(self, messages, **kwargs) -> str:
        self.calls.append((list(messages), dict(kwargs)))
        if not self.outcomes:
            raise AssertionError("The local language model must not be called.")
        outcome = self.outcomes.pop(0)
        if callable(outcome):
            outcome = outcome()
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, str)
        return outcome


class RecordingSearch:
    def __init__(self, results: list[SourceSearchResult]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, object, object]] = []

    def __call__(self, course_id, request, *, embedder=None):
        self.calls.append((course_id, request, embedder))
        return SourceSearchResponse(
            question=request.question,
            results=list(self.results),
        )


class BlockingLLM(RecordingLLM):
    def __init__(
        self,
        *,
        started: Event,
        release: Event,
        output: str,
    ) -> None:
        super().__init__([output])
        self.started = started
        self.release = release

    def create_chat_completion(self, messages, **kwargs) -> str:
        self.calls.append((list(messages), dict(kwargs)))
        self.started.set()
        if not self.release.wait(timeout=5):
            raise AssertionError("Timed out waiting to release the fake LLM.")
        return self.outcomes.pop(0)


def _create_course(title: str = "Optimization") -> Course:
    return create_video_course(CourseCreate(title=title))


def _project_pdf_source(
    *,
    course_id: str,
    asset_id: str,
    texts: list[str],
    enabled: bool = True,
) -> tuple[CourseSource, list[CourseSourceChunk], list[SourceSearchResult]]:
    create_source_asset(
        SourceAsset(
            id=asset_id,
            course_id=course_id,
            asset_type="pdf",
            original_filename=f"{asset_id}.pdf",
            stored_path=str(Path("test-sources") / f"{asset_id}.pdf"),
            mime_type="application/pdf",
            size_bytes=0,
            sha256=f"test-{asset_id}",
            extraction_status="ready",
        )
    )
    source = CourseSource(
        id=f"asset:{asset_id}",
        course_id=course_id,
        origin_type="source_asset",
        origin_id=asset_id,
        source_type="pdf",
        title=f"{asset_id}.pdf",
        content_status="ready",
        enabled=enabled,
    )
    chunks: list[CourseSourceChunk] = []
    results: list[SourceSearchResult] = []
    for ordinal, text in enumerate(texts):
        locator = PdfPageLocator(
            asset_id=asset_id,
            page_number=ordinal + 1,
        )
        chunk = CourseSourceChunk(
            id=f"source_unit:{asset_id}-page-{ordinal + 1}",
            source_id=source.id,
            origin_type="source_unit",
            origin_id=f"{asset_id}-page-{ordinal + 1}",
            chunk_type="page",
            ordinal=ordinal,
            text=text,
            text_hash=hash_source_chunk_text(text),
            locator=locator,
            chunker_version="test-source-unit-v1",
        )
        chunks.append(chunk)
        results.append(
            SourceSearchResult(
                chunk_id=chunk.id,
                source_id=source.id,
                source_title=source.title,
                source_type=source.source_type,
                chunk_type=chunk.chunk_type,
                quote=chunk.text,
                score=0.92 - ordinal * 0.01,
                locator=locator,
            )
        )
    replace_source_projection(source, chunks)
    return source, chunks, results


def _create_conversation(
    client: TestClient,
    course_id: str,
    *,
    source_ids: list[str] | None,
) -> dict[str, object]:
    payload: dict[str, object] = {"title": "Optimization questions"}
    if source_ids is not None:
        payload["source_ids"] = source_ids
    response = client.post(
        f"/courses/{course_id}/chat/conversations",
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _send_message(
    client: TestClient,
    conversation_id: str,
    *,
    content: str,
    request_id: str,
    source_ids: list[str] | None = None,
):
    payload: dict[str, object] = {
        "content": content,
        "client_request_id": request_id,
    }
    if source_ids is not None:
        payload["source_ids"] = source_ids
    return client.post(
        f"/chat/conversations/{conversation_id}/messages",
        json=payload,
    )


def _answered_output(
    sentences: list[tuple[str, list[str]]],
) -> str:
    return json.dumps(
        {
            "status": "answered",
            "sentences": [
                {
                    "text": text,
                    "evidence_ids": evidence_ids,
                }
                for text, evidence_ids in sentences
            ],
        }
    )


def _patch_chat_dependencies(
    monkeypatch,
    *,
    llm: RecordingLLM,
    search: RecordingSearch,
) -> None:
    monkeypatch.setattr(main, "get_llm_client", lambda: llm)
    monkeypatch.setattr(
        source_search_service,
        "search_course_sources",
        search,
    )


def _generation_payload(llm_call) -> dict[str, object]:
    messages, _ = llm_call
    return json.loads(messages[1].content)


def _graph_context(course_id: str) -> ChatGraphContext:
    return ChatGraphContext(
        course_id=course_id,
        graph_version=2,
        graph_content_hash="a" * 64,
        result_hash="b" * 64,
        concepts=[
            {
                "concept_id": "sparse",
                "concept_revision": 2,
                "preferred_name": "Sparse Attention",
            },
            {
                "concept_id": "full",
                "concept_revision": 4,
                "preferred_name": "Full Attention",
            },
        ],
        steps=[
            {
                "ordinal": 0,
                "relation_id": "full-sparse",
                "relation_revision": 5,
                "relation_type": "prerequisite",
                "support_basis": "pedagogical_inference",
                "from_concept_id": "sparse",
                "to_concept_id": "full",
                "traversed_against_relation_direction": True,
            }
        ],
    )


def test_conversation_create_list_and_restart_persistence() -> None:
    course = _create_course()

    with TestClient(main.app) as first_client:
        conversation = _create_conversation(
            first_client,
            course.id,
            source_ids=[],
        )
        listed = first_client.get(
            f"/courses/{course.id}/chat/conversations"
        )
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [conversation["id"]]

    with TestClient(main.app) as restarted_client:
        detail = restarted_client.get(
            f"/chat/conversations/{conversation['id']}"
        )
        listed = restarted_client.get(
            f"/courses/{course.id}/chat/conversations"
        )

    assert detail.status_code == 200
    assert detail.json()["id"] == conversation["id"]
    assert detail.json()["messages"] == []
    assert [item["id"] for item in listed.json()] == [conversation["id"]]


def test_none_source_scope_snapshots_enabled_sources_and_empty_stays_empty() -> None:
    course = _create_course()
    enabled, _, _ = _project_pdf_source(
        course_id=course.id,
        asset_id="enabled",
        texts=["Enabled source evidence."],
    )
    _project_pdf_source(
        course_id=course.id,
        asset_id="disabled",
        texts=["Disabled source evidence."],
        enabled=False,
    )
    client = TestClient(main.app)

    default_scope = _create_conversation(
        client,
        course.id,
        source_ids=None,
    )
    empty_scope = _create_conversation(
        client,
        course.id,
        source_ids=[],
    )

    assert default_scope["selected_source_ids"] == [enabled.id]
    assert empty_scope["selected_source_ids"] == []


def test_cross_course_source_is_rejected_before_search_or_model(
    monkeypatch,
) -> None:
    first_course = _create_course("First")
    second_course = _create_course("Second")
    foreign_source, _, foreign_results = _project_pdf_source(
        course_id=second_course.id,
        asset_id="foreign",
        texts=["Evidence from another course."],
    )
    llm = RecordingLLM([])
    search = RecordingSearch(foreign_results)
    _patch_chat_dependencies(monkeypatch, llm=llm, search=search)
    client = TestClient(main.app)
    conversation = _create_conversation(
        client,
        first_course.id,
        source_ids=[],
    )

    response = _send_message(
        client,
        conversation["id"],
        content="Use the other course.",
        request_id="cross-course-request",
        source_ids=[foreign_source.id],
    )

    assert response.status_code == 400
    assert "do not belong to this course" in response.json()["detail"]
    assert search.calls == []
    assert llm.calls == []
    assert client.get(
        f"/chat/conversations/{conversation['id']}"
    ).json()["messages"] == []


def test_search_cannot_escape_the_turn_source_snapshot(monkeypatch) -> None:
    course = _create_course()
    selected, _, _ = _project_pdf_source(
        course_id=course.id,
        asset_id="selected",
        texts=["The user selected this evidence."],
    )
    unselected, _, unselected_results = _project_pdf_source(
        course_id=course.id,
        asset_id="unselected",
        texts=["This source is in the course but was not selected."],
    )
    llm = RecordingLLM(
        [_answered_output([("An out-of-scope answer.", ["E1"])])]
    )
    search = RecordingSearch(unselected_results)
    _patch_chat_dependencies(monkeypatch, llm=llm, search=search)
    client = TestClient(main.app)
    conversation = _create_conversation(
        client,
        course.id,
        source_ids=[selected.id],
    )

    response = _send_message(
        client,
        conversation["id"],
        content="Only use the selected source.",
        request_id="selected-scope-request",
        source_ids=[selected.id],
    )

    assert response.status_code == 409
    assert response.json() == {"detail": SAFE_SOURCE_CHANGED_MESSAGE}
    assert unselected.id not in response.text
    assert llm.calls == []
    detail = client.get(
        f"/chat/conversations/{conversation['id']}"
    ).json()
    assert detail["messages"][-1]["status"] == "failed"
    assert detail["messages"][-1]["error_message"] == SAFE_SOURCE_CHANGED_MESSAGE


def test_stale_conversation_sources_do_not_break_title_patch(
    monkeypatch,
) -> None:
    course = _create_course()
    source, _, _ = _project_pdf_source(
        course_id=course.id,
        asset_id="removed",
        texts=["This source will be removed."],
    )
    llm = RecordingLLM([])
    search = RecordingSearch([])
    _patch_chat_dependencies(monkeypatch, llm=llm, search=search)
    client = TestClient(main.app)
    conversation = _create_conversation(
        client,
        course.id,
        source_ids=[source.id],
    )
    delete_source_projection(source.id)

    renamed = client.patch(
        f"/chat/conversations/{conversation['id']}",
        json={"title": "Renamed after source removal"},
    )
    response = _send_message(
        client,
        conversation["id"],
        content="Can this stale source still be used?",
        request_id="stale-source-request",
    )

    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title"] == "Renamed after source removal"
    assert renamed.json()["selected_source_ids"] == [source.id]
    assert response.status_code == 409
    assert response.json() == {"detail": SAFE_SOURCE_CHANGED_MESSAGE}
    assert search.calls == []
    assert llm.calls == []
    assert client.get(
        f"/chat/conversations/{conversation['id']}"
    ).json()["messages"] == []


def test_follow_up_history_enters_retrieval_and_generation(
    monkeypatch,
) -> None:
    course = _create_course()
    source, _, results = _project_pdf_source(
        course_id=course.id,
        asset_id="history",
        texts=["Gradient descent iteratively updates model parameters."],
    )
    first_answer = "Gradient descent updates the model parameters."
    second_answer = "It is useful because it reduces the objective."
    llm = RecordingLLM(
        [
            _answered_output([(first_answer, ["E1"])]),
            _answered_output([(second_answer, ["E1"])]),
        ]
    )
    search = RecordingSearch(results)
    _patch_chat_dependencies(monkeypatch, llm=llm, search=search)
    client = TestClient(main.app)
    conversation = _create_conversation(
        client,
        course.id,
        source_ids=[source.id],
    )

    first = _send_message(
        client,
        conversation["id"],
        content="What does gradient descent update?",
        request_id="history-1",
    )
    second = _send_message(
        client,
        conversation["id"],
        content="Why is that useful?",
        request_id="history-2",
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert len(search.calls) == 2
    second_query = search.calls[1][1].question
    assert "What does gradient descent update?" in second_query
    assert "Why is that useful?" in second_query
    assert first_answer not in second_query

    second_payload = _generation_payload(llm.calls[1])
    assert second_payload["question_untrusted"] == "Why is that useful?"
    assert second_payload["conversation_history_untrusted"] == [
        {
            "role": "user",
            "content": "What does gradient descent update?",
        },
        {
            "role": "assistant",
            "content": first_answer,
        },
    ]


def test_published_graph_path_is_context_not_source_evidence(
    monkeypatch,
) -> None:
    course = _create_course()
    source, _, results = _project_pdf_source(
        course_id=course.id,
        asset_id="graph-context",
        texts=["Sparse attention reduces the cost of full attention."],
    )
    llm = RecordingLLM(
        [
            _answered_output(
                [("Sparse attention can reduce attention cost.", ["E1"])]
            )
        ]
    )
    search = RecordingSearch(results)
    _patch_chat_dependencies(monkeypatch, llm=llm, search=search)
    graph_calls: list[tuple[str, str, list[str]]] = []

    def load_context(course_id, question, source_ids):
        graph_calls.append((course_id, question, list(source_ids)))
        return _graph_context(course_id)

    monkeypatch.setattr(
        chat_service,
        "load_graph_chat_context",
        load_context,
    )
    client = TestClient(main.app)
    conversation = _create_conversation(
        client,
        course.id,
        source_ids=[source.id],
    )
    question = "How are Sparse Attention and Full Attention connected?"

    response = _send_message(
        client,
        conversation["id"],
        content=question,
        request_id="graph-context-request",
    )

    assert response.status_code == 200, response.text
    assert graph_calls == [(course.id, question, [source.id])]
    prompt = _generation_payload(llm.calls[0])
    assert prompt["evidence_untrusted"][0]["quote"] == results[0].quote
    assert "score" not in prompt["evidence_untrusted"][0]
    assert prompt["concept_graph_plan_untrusted"]["steps"] == [
        {
            "ordinal": 0,
            "relation_type": "prerequisite",
            "support_basis": "pedagogical_inference",
            "from_concept_id": "sparse",
            "to_concept_id": "full",
            "traversed_against_relation_direction": True,
        }
    ]
    assistant = response.json()["assistant_message"]
    assert assistant["metadata"]["retrieval_mode"] == "semantic"
    assert assistant["metadata"]["graph_context"] == (
        _graph_context(course.id).model_dump(mode="json")
    )
    assert assistant["citations"][0]["score"] == results[0].score


def test_graph_context_does_not_mask_semantic_retrieval_failure(
    monkeypatch,
) -> None:
    course = _create_course()
    source, _, _ = _project_pdf_source(
        course_id=course.id,
        asset_id="graph-retrieval-failure",
        texts=["The Source remains the only factual authority."],
    )
    llm = RecordingLLM([])
    monkeypatch.setattr(main, "get_llm_client", lambda: llm)
    monkeypatch.setattr(
        chat_service,
        "load_graph_chat_context",
        lambda course_id, question, source_ids: _graph_context(course_id),
    )

    def fail_search(course_id, request, *, embedder=None):
        raise SourceSearchError("private embedding failure")

    monkeypatch.setattr(
        source_search_service,
        "search_course_sources",
        fail_search,
    )
    client = TestClient(main.app)
    conversation = _create_conversation(
        client,
        course.id,
        source_ids=[source.id],
    )

    response = _send_message(
        client,
        conversation["id"],
        content="How are Full Attention and Sparse Attention connected?",
        request_id="graph-retrieval-failure-request",
    )

    assert response.status_code == 503
    assert response.json() == {"detail": SAFE_RETRIEVAL_MESSAGE}
    assert llm.calls == []


def test_answer_has_sentence_level_citations_and_typed_locators(
    monkeypatch,
) -> None:
    course = _create_course()
    source, _, results = _project_pdf_source(
        course_id=course.id,
        asset_id="citations",
        texts=[
            "Normalization places features on comparable scales.",
            "Comparable scales prevent one feature from dominating by magnitude.",
        ],
    )
    sentences = [
        "Normalization puts features on comparable scales.",
        "This prevents magnitude alone from dominating.",
    ]
    llm = RecordingLLM(
        [
            _answered_output(
                [
                    (sentences[0], ["E1"]),
                    (sentences[1], ["E2"]),
                ]
            )
        ]
    )
    search = RecordingSearch(results)
    _patch_chat_dependencies(monkeypatch, llm=llm, search=search)
    client = TestClient(main.app)
    conversation = _create_conversation(
        client,
        course.id,
        source_ids=[source.id],
    )

    response = _send_message(
        client,
        conversation["id"],
        content="Why normalize features?",
        request_id="citation-request",
    )

    assert response.status_code == 200, response.text
    assistant = response.json()["assistant_message"]
    assert assistant["answer_status"] == "answered"
    assert assistant["content"] == " ".join(sentences)
    assert len(assistant["citations"]) == len(sentences)
    assert {
        citation["sentence_index"]
        for citation in assistant["citations"]
    } == {0, 1}
    for citation in assistant["citations"]:
        cited_sentence = assistant["content"][
            citation["start_offset"] : citation["end_offset"]
        ]
        assert cited_sentence == sentences[citation["sentence_index"]]
        assert citation["quote"] in {
            result.quote
            for result in results
        }
        assert citation["locator"]["kind"] == "pdf_page"
        assert citation["locator"]["asset_id"] == "citations"
        assert citation["locator"]["page_number"] in {1, 2}

    persisted = client.get(
        f"/chat/conversations/{conversation['id']}"
    ).json()["messages"]
    assert persisted[-1]["citations"] == assistant["citations"]


def test_model_insufficient_evidence_is_normal_abstention(
    monkeypatch,
) -> None:
    course = _create_course()
    source, _, results = _project_pdf_source(
        course_id=course.id,
        asset_id="insufficient",
        texts=["The source mentions optimization but not the requested proof."],
    )
    llm = RecordingLLM(
        ['{"status":"insufficient_evidence","sentences":[]}']
    )
    search = RecordingSearch(results)
    _patch_chat_dependencies(monkeypatch, llm=llm, search=search)
    client = TestClient(main.app)
    conversation = _create_conversation(
        client,
        course.id,
        source_ids=[source.id],
    )

    response = _send_message(
        client,
        conversation["id"],
        content="Prove the convergence rate.",
        request_id="insufficient-request",
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "refused"
    assistant = response.json()["assistant_message"]
    assert assistant["status"] == "complete"
    assert assistant["answer_status"] == "abstained"
    assert assistant["content"] == INSUFFICIENT_EVIDENCE_MESSAGE
    assert assistant["citations"] == []
    assert len(llm.calls) == 1


def test_no_sources_abstains_without_search_or_model(monkeypatch) -> None:
    course = _create_course()
    llm = RecordingLLM([])
    search = RecordingSearch([])
    _patch_chat_dependencies(monkeypatch, llm=llm, search=search)
    client = TestClient(main.app)
    conversation = _create_conversation(
        client,
        course.id,
        source_ids=[],
    )

    response = _send_message(
        client,
        conversation["id"],
        content="What does the course say?",
        request_id="no-sources-request",
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "refused"
    assert (
        response.json()["assistant_message"]["content"]
        == INSUFFICIENT_EVIDENCE_MESSAGE
    )
    assert search.calls == []
    assert llm.calls == []


def test_no_retrieved_evidence_abstains_without_model(monkeypatch) -> None:
    course = _create_course()
    source, _, _ = _project_pdf_source(
        course_id=course.id,
        asset_id="no-hit",
        texts=["An unrelated source chunk."],
    )
    llm = RecordingLLM([])
    search = RecordingSearch([])
    _patch_chat_dependencies(monkeypatch, llm=llm, search=search)
    client = TestClient(main.app)
    conversation = _create_conversation(
        client,
        course.id,
        source_ids=[source.id],
    )

    response = _send_message(
        client,
        conversation["id"],
        content="What is the convergence theorem?",
        request_id="no-evidence-request",
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "refused"
    assert len(search.calls) == 1
    assert llm.calls == []


def test_invalid_output_gets_one_repair_then_safe_502(
    monkeypatch,
) -> None:
    course = _create_course()
    source, _, results = _project_pdf_source(
        course_id=course.id,
        asset_id="repair",
        texts=["Evidence remains available for the repair attempt."],
    )
    llm = RecordingLLM(["not valid JSON", "{}"])
    search = RecordingSearch(results)
    _patch_chat_dependencies(monkeypatch, llm=llm, search=search)
    client = TestClient(main.app)
    conversation = _create_conversation(
        client,
        course.id,
        source_ids=[source.id],
    )

    response = _send_message(
        client,
        conversation["id"],
        content="Answer with grounded evidence.",
        request_id="repair-request",
    )

    assert response.status_code == 502
    assert response.json() == {"detail": SAFE_GENERATION_MESSAGE}
    assert len(llm.calls) == 2
    repair_payload = _generation_payload(llm.calls[1])
    assert repair_payload["invalid_candidate_untrusted"] == "not valid JSON"
    detail = client.get(
        f"/chat/conversations/{conversation['id']}"
    ).json()
    assert detail["messages"][-1]["status"] == "failed"
    assert detail["messages"][-1]["error_message"] == SAFE_GENERATION_MESSAGE
    assert "not valid JSON" not in json.dumps(detail)


def test_timeout_returns_504_and_persists_only_safe_error(
    monkeypatch,
) -> None:
    course = _create_course()
    source, _, results = _project_pdf_source(
        course_id=course.id,
        asset_id="timeout",
        texts=["Evidence for a request that will time out."],
    )
    secret = "C:\\Users\\alice\\private-model.log"
    llm = RecordingLLM([LLMTimeoutError(secret)])
    search = RecordingSearch(results)
    _patch_chat_dependencies(monkeypatch, llm=llm, search=search)
    client = TestClient(main.app)
    conversation = _create_conversation(
        client,
        course.id,
        source_ids=[source.id],
    )

    response = _send_message(
        client,
        conversation["id"],
        content="Why did this time out?",
        request_id="timeout-request",
    )

    assert response.status_code == 504
    assert response.json() == {"detail": SAFE_TIMEOUT_MESSAGE}
    detail = client.get(
        f"/chat/conversations/{conversation['id']}"
    ).json()
    assistant = detail["messages"][-1]
    assert assistant["status"] == "failed"
    assert assistant["error_message"] == SAFE_TIMEOUT_MESSAGE
    assert secret not in response.text
    assert secret not in json.dumps(detail)

    replay = _send_message(
        client,
        conversation["id"],
        content="Why did this time out?",
        request_id="timeout-request",
    )
    assert replay.status_code == 504
    assert replay.json() == {"detail": SAFE_TIMEOUT_MESSAGE}
    assert len(llm.calls) == 1


def test_client_request_replay_does_not_repeat_search_or_generation(
    monkeypatch,
) -> None:
    course = _create_course()
    source, _, results = _project_pdf_source(
        course_id=course.id,
        asset_id="replay",
        texts=["One request id should produce exactly one answer."],
    )
    llm = RecordingLLM(
        [_answered_output([("Exactly one answer is generated.", ["E1"])])]
    )
    search = RecordingSearch(results)
    _patch_chat_dependencies(monkeypatch, llm=llm, search=search)
    client = TestClient(main.app)
    conversation = _create_conversation(
        client,
        course.id,
        source_ids=[source.id],
    )

    first = _send_message(
        client,
        conversation["id"],
        content="Generate one answer.",
        request_id="stable-request-id",
    )
    replay = _send_message(
        client,
        conversation["id"],
        content="Generate one answer.",
        request_id="stable-request-id",
    )

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["turn_id"] == first.json()["turn_id"]
    assert (
        replay.json()["assistant_message"]["id"]
        == first.json()["assistant_message"]["id"]
    )
    assert len(search.calls) == 1
    assert len(llm.calls) == 1


def test_canceled_generation_is_not_published_and_retries_same_turn(
    monkeypatch,
) -> None:
    course = _create_course()
    source, _, results = _project_pdf_source(
        course_id=course.id,
        asset_id="cancel-retry",
        texts=["Cancellation must happen before answer publication."],
    )
    first_llm = RecordingLLM(
        [_answered_output([("This answer must not be published.", ["E1"])])]
    )
    search = RecordingSearch(results)
    _patch_chat_dependencies(monkeypatch, llm=first_llm, search=search)
    client = TestClient(main.app)
    conversation = _create_conversation(
        client,
        course.id,
        source_ids=[source.id],
    )
    request = ChatMessageCreate(
        content="Can cancellation prevent publication?",
        client_request_id="stable-cancel-request",
    )

    def cancel_before_publication() -> None:
        raise chat_service.ChatGenerationCancellationRequested()

    with pytest.raises(chat_service.ChatGenerationCancellationRequested):
        chat_service.send_chat_message(
            str(conversation["id"]),
            request,
            llm_client=first_llm,
            checkpoint=cancel_before_publication,
        )

    canceled_detail = client.get(
        f"/chat/conversations/{conversation['id']}"
    ).json()
    assert len(canceled_detail["messages"]) == 2
    assert canceled_detail["messages"][-1]["status"] == "failed"
    assert canceled_detail["messages"][-1]["content"] == ""

    retry_llm = RecordingLLM(
        [_answered_output([("The retried answer is grounded.", ["E1"])])]
    )
    retried = chat_service.send_chat_message(
        str(conversation["id"]),
        request,
        llm_client=retry_llm,
        checkpoint=lambda: None,
        retry_failed=True,
    )

    assert retried.status == "completed"
    assert retried.client_request_id == request.client_request_id
    assert retried.turn_id == canceled_detail["messages"][0]["turn_id"]
    retried_detail = client.get(
        f"/chat/conversations/{conversation['id']}"
    ).json()
    assert len(retried_detail["messages"]) == 2
    assert retried_detail["messages"][-1]["status"] == "complete"
    assert len(first_llm.calls) == 1
    assert len(retry_llm.calls) == 1


def test_same_conversation_rejects_concurrent_turn_with_409(
    monkeypatch,
) -> None:
    course = _create_course()
    source, _, results = _project_pdf_source(
        course_id=course.id,
        asset_id="concurrent",
        texts=["Concurrent turns must be serialized."],
    )
    started = Event()
    release = Event()
    llm = BlockingLLM(
        started=started,
        release=release,
        output=_answered_output(
            [("The first answer owns the active turn.", ["E1"])]
        ),
    )
    search = RecordingSearch(results)
    _patch_chat_dependencies(monkeypatch, llm=llm, search=search)
    setup_client = TestClient(main.app)
    conversation = _create_conversation(
        setup_client,
        course.id,
        source_ids=[source.id],
    )
    first_result: dict[str, object] = {}

    def send_first() -> None:
        first_result["response"] = _send_message(
            TestClient(main.app),
            conversation["id"],
            content="First question",
            request_id="concurrent-1",
        )

    thread = Thread(target=send_first)
    thread.start()
    try:
        assert started.wait(timeout=5)
        second = _send_message(
            TestClient(main.app),
            conversation["id"],
            content="Second question",
            request_id="concurrent-2",
        )
        assert second.status_code == 409
    finally:
        release.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    first = first_result["response"]
    assert first.status_code == 200, first.text
    assert len(llm.calls) == 1


def test_concurrent_title_patch_after_reservation_is_preserved(
    monkeypatch,
) -> None:
    course = _create_course()
    source, _, results = _project_pdf_source(
        course_id=course.id,
        asset_id="title-race",
        texts=["The answer has stable evidence."],
    )
    llm = RecordingLLM(
        [_answered_output([("The answer is grounded.", ["E1"])])]
    )
    search = RecordingSearch(results)
    _patch_chat_dependencies(monkeypatch, llm=llm, search=search)
    original_reserve = chat_store.reserve_turn

    def reserve_then_rename(*args, **kwargs):
        reservation = original_reserve(*args, **kwargs)
        updated = chat_store.patch_conversation(
            reservation.conversation.id,
            title="Concurrent user title",
        )
        assert updated is not None
        return reservation

    monkeypatch.setattr(chat_store, "reserve_turn", reserve_then_rename)
    client = TestClient(main.app)
    conversation = _create_conversation(
        client,
        course.id,
        source_ids=[source.id],
    )
    client.patch(
        f"/chat/conversations/{conversation['id']}",
        json={"title": "New chat"},
    )

    response = _send_message(
        client,
        conversation["id"],
        content="What is the answer?",
        request_id="title-race-request",
        source_ids=[source.id],
    )

    assert response.status_code == 200, response.text
    assert response.json()["conversation"]["title"] == "Concurrent user title"


def test_source_removed_after_reservation_finalizes_the_turn(
    monkeypatch,
) -> None:
    course = _create_course()
    source, _, results = _project_pdf_source(
        course_id=course.id,
        asset_id="reservation-race",
        texts=["Evidence that disappears after reservation."],
    )
    llm = RecordingLLM(
        [_answered_output([("The source supplied the answer.", ["E1"])])]
    )
    search = RecordingSearch(results)
    _patch_chat_dependencies(monkeypatch, llm=llm, search=search)
    original_reserve = chat_store.reserve_turn

    def reserve_then_remove_source(*args, **kwargs):
        reservation = original_reserve(*args, **kwargs)
        delete_source_projection(source.id)
        return reservation

    monkeypatch.setattr(
        chat_store,
        "reserve_turn",
        reserve_then_remove_source,
    )
    client = TestClient(main.app)
    conversation = _create_conversation(
        client,
        course.id,
        source_ids=[source.id],
    )

    response = _send_message(
        client,
        conversation["id"],
        content="Use evidence that may change.",
        request_id="reservation-race-request",
        source_ids=[source.id],
    )

    assert response.status_code == 409
    assert response.json() == {"detail": SAFE_SOURCE_CHANGED_MESSAGE}
    detail = client.get(
        f"/chat/conversations/{conversation['id']}"
    ).json()
    assert detail["messages"][-1]["status"] == "failed"
    assert detail["messages"][-1]["error_message"] == SAFE_SOURCE_CHANGED_MESSAGE
    replay = _send_message(
        client,
        conversation["id"],
        content="Use evidence that may change.",
        request_id="reservation-race-request",
        source_ids=[source.id],
    )
    assert replay.status_code == 409
    assert replay.json() == {"detail": SAFE_SOURCE_CHANGED_MESSAGE}
    assert len(llm.calls) == 1


def test_disabled_selected_source_is_rejected_before_reservation(
    monkeypatch,
) -> None:
    course = _create_course()
    source, _, results = _project_pdf_source(
        course_id=course.id,
        asset_id="disabled-before-reservation",
        texts=["Evidence that is later disabled."],
    )
    llm = RecordingLLM([])
    search = RecordingSearch(results)
    _patch_chat_dependencies(monkeypatch, llm=llm, search=search)
    client = TestClient(main.app)
    conversation = _create_conversation(
        client,
        course.id,
        source_ids=[source.id],
    )
    assert set_source_enabled(source.id, False) is True

    response = _send_message(
        client,
        conversation["id"],
        content="Use the disabled source.",
        request_id="disabled-before-reservation-request",
    )

    assert response.status_code == 409
    assert response.json() == {"detail": SAFE_SOURCE_CHANGED_MESSAGE}
    assert search.calls == []
    assert llm.calls == []
    detail = client.get(
        f"/chat/conversations/{conversation['id']}"
    ).json()
    assert detail["messages"] == []


def test_source_disabled_before_citation_commit_returns_409(
    monkeypatch,
) -> None:
    course = _create_course()
    source, _, results = _project_pdf_source(
        course_id=course.id,
        asset_id="disabled-before-commit",
        texts=["Evidence that is disabled during generation."],
    )

    def disable_source_then_answer() -> str:
        assert set_source_enabled(source.id, False) is True
        return _answered_output(
            [("An answer based on disabled evidence.", ["E1"])]
        )

    llm = RecordingLLM([disable_source_then_answer])
    search = RecordingSearch(results)
    _patch_chat_dependencies(monkeypatch, llm=llm, search=search)
    client = TestClient(main.app)
    conversation = _create_conversation(
        client,
        course.id,
        source_ids=[source.id],
    )

    response = _send_message(
        client,
        conversation["id"],
        content="Use evidence that may be disabled.",
        request_id="disabled-before-commit-request",
    )

    assert response.status_code == 409
    assert response.json() == {"detail": SAFE_SOURCE_CHANGED_MESSAGE}
    detail = client.get(
        f"/chat/conversations/{conversation['id']}"
    ).json()
    assert detail["messages"][-1]["status"] == "failed"
    assert detail["messages"][-1]["error_message"] == SAFE_SOURCE_CHANGED_MESSAGE
    assert len(llm.calls) == 1


def test_source_change_before_citation_commit_returns_409(
    monkeypatch,
) -> None:
    course = _create_course()
    source, chunks, results = _project_pdf_source(
        course_id=course.id,
        asset_id="changing",
        texts=["The original evidence text."],
    )

    def mutate_source_then_answer() -> str:
        changed_text = "The source changed after retrieval."
        changed_chunk = chunks[0].model_copy(
            update={
                "text": changed_text,
                "text_hash": hash_source_chunk_text(changed_text),
            }
        )
        replace_source_projection(source, [changed_chunk])
        return _answered_output(
            [("An answer based on the old evidence.", ["E1"])]
        )

    llm = RecordingLLM([mutate_source_then_answer])
    search = RecordingSearch(results)
    _patch_chat_dependencies(monkeypatch, llm=llm, search=search)
    client = TestClient(main.app)
    conversation = _create_conversation(
        client,
        course.id,
        source_ids=[source.id],
    )

    response = _send_message(
        client,
        conversation["id"],
        content="Use the current source.",
        request_id="source-cas-request",
    )

    assert response.status_code == 409
    assert response.json() == {"detail": SAFE_SOURCE_CHANGED_MESSAGE}
    detail = client.get(
        f"/chat/conversations/{conversation['id']}"
    ).json()
    assert detail["messages"][-1]["status"] == "failed"
    assert (
        detail["messages"][-1]["error_message"]
        == SAFE_SOURCE_CHANGED_MESSAGE
    )


def test_legacy_rag_retrieve_contract_remains_available(monkeypatch) -> None:
    def fake_retrieve(request):
        return RagRetrieveResponse(question=request.question, results=[])

    monkeypatch.setattr(rag_service, "retrieve_cards", fake_retrieve)
    response = TestClient(main.app).post(
        "/rag/retrieve",
        json={
            "question": "What is linear algebra?",
            "top_k": 5,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "question": "What is linear algebra?",
        "results": [],
    }
