from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rag_lab.controller_metrics import (
    ControllerEvaluationTarget,
    _is_subsequence,
    aggregate_controller_metrics,
    evaluate_controller_trace,
)
from rag_lab.controller_schemas import (
    AnswerAction,
    AnswerObservation,
    ControllerCost,
    ControllerKnowledgeNeed,
    ControllerState,
    ControllerStep,
    ControllerTrace,
    action_fingerprint,
    controller_state_sha256,
    controller_trace_payload_sha256,
)


def _trace() -> ControllerTrace:
    supported_need = ControllerKnowledgeNeed(
        need_id="n1",
        description="Find support",
        status="supported",
        confidence=1.0,
        support_concept_ids=["c1", "c2"],
        support_evidence_ids=["e1", "e2"],
    )
    initial = ControllerState(
        question_id="q1",
        question="Why?",
        knowledge_needs=[supported_need],
        retrieved_concept_ids=["c1", "c2"],
        retrieved_evidence_ids=["e1", "e2"],
        verified_evidence_ids=["e1", "e2"],
        cost=ControllerCost(
            retrieval_calls=2,
            concept_searches=1,
            evidence_searches=1,
            verifications=1,
            unique_concepts=2,
            unique_evidence=2,
            context_characters=50,
        ),
    )
    action = AnswerAction(supported_need_ids=["n1"])
    final = ControllerState(
        question_id="q1",
        question="Why?",
        knowledge_needs=[supported_need],
        retrieved_concept_ids=["c1", "c2"],
        retrieved_evidence_ids=["e1", "e2"],
        verified_evidence_ids=["e1", "e2"],
        attempted_action_fingerprints=[action_fingerprint(action)],
        answerability_confidence=1.0,
        consecutive_no_progress=1,
        step_index=1,
        cost=ControllerCost(
            steps=1,
            retrieval_calls=2,
            concept_searches=1,
            evidence_searches=1,
            verifications=1,
            unique_concepts=2,
            unique_evidence=2,
            context_characters=50,
            elapsed_milliseconds=12,
        ),
    )
    step = ControllerStep(
        step_index=0,
        state_before_sha256=controller_state_sha256(initial),
        state_before=initial,
        action=action,
        observation=AnswerObservation(
            action_fingerprint=action_fingerprint(action),
            cost=ControllerCost(
                steps=1,
                elapsed_milliseconds=12,
            ),
        ),
        state_after=final,
    )
    now = datetime(2026, 7, 25, tzinfo=UTC)
    payload = dict(
        trace_id="trace-1",
        protocol_id="protocol-1",
        protocol_sha256="a" * 64,
        memory_id="memory-1",
        memory_sha256="b" * 64,
        question_id="q1",
        policy_name="test",
        initial_state=initial,
        steps=[step],
        final_state=final,
        stop_reason="answer",
        status="completed",
        final_answer="Because.",
        citation_evidence_ids=["e1"],
        created_at=now,
        completed_at=now,
    )
    provisional = ControllerTrace.model_construct(
        **payload,
        trace_sha256="0" * 64,
    )
    return ControllerTrace(
        **payload,
        trace_sha256=controller_trace_payload_sha256(provisional),
    )


def _failed_trace() -> ControllerTrace:
    need = ControllerKnowledgeNeed(
        need_id="n1",
        description="Find support",
    )
    state = ControllerState(
        question_id="q1",
        question="Why?",
        knowledge_needs=[need],
    )
    now = datetime(2026, 7, 25, tzinfo=UTC)
    payload = dict(
        trace_id="trace-failed",
        protocol_id="protocol-1",
        protocol_sha256="a" * 64,
        memory_id="memory-1",
        memory_sha256="b" * 64,
        question_id="q1",
        policy_name="test",
        initial_state=state,
        steps=[],
        final_state=state,
        terminal_decision_cost=ControllerCost(
            prompt_characters=10,
            completion_tokens=None,
            elapsed_milliseconds=2,
        ),
        stop_reason="policy_error",
        status="failed",
        error_type="RuntimeError",
        error_message="policy failed",
        created_at=now,
        completed_at=now,
    )
    provisional = ControllerTrace.model_construct(
        **payload,
        trace_sha256="0" * 64,
    )
    return ControllerTrace(
        **payload,
        trace_sha256=controller_trace_payload_sha256(provisional),
    )


def test_controller_metrics_score_alternative_evidence_groups() -> None:
    trace = _trace()
    target = ControllerEvaluationTarget(
        question_id="q1",
        answerable=True,
        required_concept_ids=["c1", "c2"],
        evidence_requirement_alternatives=[
            [["e1"], ["equivalent-evidence"]],
            [["e2", "e3"]],
        ],
    )
    metrics = evaluate_controller_trace(trace, target)
    assert metrics.required_concept_recall == 1.0
    assert metrics.evidence_requirement_recall == 0.5
    assert metrics.first_full_concept_step == 0
    assert metrics.first_full_evidence_step is None
    assert metrics.stop_correct


def test_controller_metrics_aggregate_cost_and_stop_accuracy() -> None:
    positive = evaluate_controller_trace(
        _trace(),
        ControllerEvaluationTarget(
            question_id="q1",
            answerable=True,
            required_concept_ids=["c1", "missing"],
        ),
    )
    aggregate = aggregate_controller_metrics([positive])
    assert aggregate.mean_required_concept_recall == 0.5
    assert aggregate.stop_accuracy == 1.0
    assert aggregate.mean_retrieval_calls == 2
    assert positive.concept_searches == 1
    assert positive.evidence_searches == 1
    assert positive.graph_expansions == 0
    assert positive.verifications == 1
    assert aggregate.mean_concept_searches == 1
    assert aggregate.mean_evidence_searches == 1
    assert aggregate.mean_graph_expansions == 0
    assert aggregate.mean_verifications == 1


def test_controller_metrics_reject_empty_aggregate() -> None:
    with pytest.raises(ValueError, match="At least one"):
        aggregate_controller_metrics([])


def test_path_matching_preserves_direction_and_order() -> None:
    first = ("r1", "c1", "c2", "prerequisite", "outgoing")
    second = ("r2", "c2", "c3", "related", "outgoing")
    reversed_direction = (
        "r1",
        "c1",
        "c2",
        "prerequisite",
        "incoming",
    )

    assert _is_subsequence([first, second], [first, second])
    assert not _is_subsequence([first, second], [second, first])
    assert not _is_subsequence([first], [reversed_direction])


def test_failed_trace_is_excluded_from_stop_accuracy() -> None:
    target = ControllerEvaluationTarget(
        question_id="q1",
        answerable=True,
    )
    failed = evaluate_controller_trace(_failed_trace(), target)
    successful = evaluate_controller_trace(_trace(), target)

    assert failed.predicted_answerable is None
    assert failed.stop_correct is None
    aggregate = aggregate_controller_metrics([successful, failed])
    assert aggregate.question_count == 2
    assert aggregate.evaluated_question_count == 1
    assert aggregate.failed_question_count == 1
    assert aggregate.stop_accuracy == 1.0
