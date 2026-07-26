from __future__ import annotations

import pytest
from pydantic import ValidationError

from rag_lab.controller_schemas import (
    CONTROLLER_ACTION_ADAPTER,
    ControllerBudget,
    ControllerConceptHit,
    ControllerCost,
    ControllerKnowledgeNeed,
    ControllerState,
    ControllerStep,
    ControllerProtocol,
    ControllerVerificationResult,
    SearchConceptAction,
    SearchEvidenceAction,
    VerificationObservation,
    VerifySupportAction,
    action_fingerprint,
    controller_state_sha256,
    reduce_controller_state,
)


def test_actions_use_a_closed_discriminated_contract() -> None:
    action = CONTROLLER_ACTION_ADAPTER.validate_python(
        {
            "action_type": "search_concept",
            "need_id": "need-1",
            "query": "  implicit   prerequisite ",
            "top_k": 3,
            "exclude_concept_ids": [],
        }
    )
    assert isinstance(action, SearchConceptAction)
    assert action.query == "implicit prerequisite"

    with pytest.raises(ValidationError):
        CONTROLLER_ACTION_ADAPTER.validate_python(
            {
                "action_type": "search_concept",
                "need_id": "need-1",
                "query": "query",
                "top_k": 3,
                "scope_concept_ids": ["illegal-for-this-action"],
            }
        )

    with pytest.raises(ValidationError):
        CONTROLLER_ACTION_ADAPTER.validate_python(
            {
                "action_type": "invented_action",
                "query": "query",
            }
        )


def test_action_fingerprint_normalizes_query_and_set_like_ids() -> None:
    left = SearchEvidenceAction(
        need_ids=["n2", "n1"],
        query="  Find   EVIDENCE ",
        scope_concept_ids=["c2", "c1"],
        top_k=2,
    )
    right = SearchEvidenceAction(
        need_ids=["n1", "n2"],
        query="find evidence",
        scope_concept_ids=["c1", "c2"],
        top_k=2,
    )
    assert action_fingerprint(left) == action_fingerprint(right)


def test_controller_state_enforces_monotonic_evidence_invariants() -> None:
    need = ControllerKnowledgeNeed(
        need_id="n1",
        description="Need evidence",
        status="supported",
        support_concept_ids=["c1"],
        support_evidence_ids=["e1"],
        confidence=0.9,
    )
    with pytest.raises(ValidationError, match="unverified evidence"):
        ControllerState(
            question_id="q1",
            question="Question?",
            knowledge_needs=[need],
            retrieved_concept_ids=["c1"],
            retrieved_evidence_ids=["e1"],
            cost=ControllerCost(
                unique_concepts=1,
                unique_evidence=1,
            ),
        )

    state = ControllerState(
        question_id="q1",
        question="Question?",
        knowledge_needs=[need],
        retrieved_concept_ids=["c1"],
        retrieved_evidence_ids=["e1"],
        verified_evidence_ids=["e1"],
        cost=ControllerCost(
            unique_concepts=1,
            unique_evidence=1,
        ),
    )
    assert len(controller_state_sha256(state)) == 64


def test_budget_rejects_an_impossible_aggregate_call_limit() -> None:
    with pytest.raises(ValidationError, match="sum of retrieval-action"):
        ControllerBudget(
            max_retrieval_calls=7,
            max_concept_searches=2,
            max_evidence_searches=2,
            max_graph_expansions=2,
        )


@pytest.mark.parametrize("invalid_top_k", [True, 1.0, "1"])
def test_builtin_policy_config_rejects_coerced_integer_types(
    invalid_top_k: object,
) -> None:
    with pytest.raises(ValidationError):
        ControllerProtocol(
            protocol_id="protocol",
            corpus_sha256="a" * 64,
            review_sha256="b" * 64,
            memory_sha256="c" * 64,
            policy_name="fixed_dense",
            policy_config={
                "top_k": invalid_top_k,
                "verifier": "deterministic_lexical_smoke_v1",
                "answerer": "extractive_evidence_smoke_v1",
                "claim_scope": "development_debug_only",
            },
            protocol_sha256="d" * 64,
        )


def test_policy_config_is_closed_and_policy_specific() -> None:
    common = {
        "protocol_id": "protocol",
        "corpus_sha256": "a" * 64,
        "review_sha256": "b" * 64,
        "memory_sha256": "c" * 64,
        "protocol_sha256": "d" * 64,
    }
    with pytest.raises(ValidationError, match="Extra inputs"):
        ControllerProtocol(
            **common,
            policy_name="fixed_dense",
            policy_config={
                "top_k": 1,
                "verifier": "deterministic_lexical_smoke_v1",
                "answerer": "extractive_evidence_smoke_v1",
                "claim_scope": "development_debug_only",
                "unknown": 1,
            },
        )
    with pytest.raises(ValidationError, match="Extra inputs"):
        ControllerProtocol(
            **common,
            policy_name="external_fixture",
            policy_config={"top_k": 1},
        )


@pytest.mark.parametrize("nonfinite", [float("nan"), float("inf"), -float("inf")])
def test_controller_models_reject_nonfinite_numbers(
    nonfinite: float,
) -> None:
    with pytest.raises(ValidationError):
        ControllerCost(elapsed_milliseconds=nonfinite)
    with pytest.raises(ValidationError):
        ControllerConceptHit(
            concept_id="c1",
            score=nonfinite,
            rank=1,
            retrieval_source="test",
        )


def _verification_state(
    need: ControllerKnowledgeNeed | None = None,
) -> ControllerState:
    selected_need = need or ControllerKnowledgeNeed(
        need_id="n1",
        description="Need evidence",
    )
    return ControllerState(
        question_id="q1",
        question="Question?",
        knowledge_needs=[selected_need],
        retrieved_concept_ids=["c1"],
        retrieved_evidence_ids=["e1", "e2"],
        verified_evidence_ids=list(selected_need.support_evidence_ids),
        cost=ControllerCost(
            unique_concepts=1,
            unique_evidence=2,
        ),
    )


def _verification_observation(
    action: VerifySupportAction,
    results: list[ControllerVerificationResult],
) -> VerificationObservation:
    return VerificationObservation(
        action_fingerprint=action_fingerprint(action),
        results=results,
        cost=ControllerCost(steps=1, verifications=1),
    )


def test_reducer_verifies_only_evidence_returned_by_verifier() -> None:
    state = _verification_state()
    action = VerifySupportAction(
        need_ids=["n1"],
        evidence_ids=["e1", "e2"],
    )
    observation = _verification_observation(
        action,
        [
            ControllerVerificationResult(
                need_id="n1",
                status="supported",
                support_concept_ids=["c1"],
                support_evidence_ids=["e1"],
                confidence=0.9,
            )
        ],
    )

    reduced = reduce_controller_state(state, action, observation)

    assert reduced.verified_evidence_ids == ["e1"]
    assert reduced.knowledge_needs[0].support_evidence_ids == ["e1"]


def test_reducer_does_not_downgrade_an_already_supported_need() -> None:
    supported = ControllerKnowledgeNeed(
        need_id="n1",
        description="Need evidence",
        status="supported",
        support_concept_ids=["c1"],
        support_evidence_ids=["e1"],
        confidence=0.8,
    )
    state = _verification_state(supported)
    action = VerifySupportAction(
        need_ids=["n1"],
        evidence_ids=["e2"],
    )
    observation = _verification_observation(
        action,
        [
            ControllerVerificationResult(
                need_id="n1",
                status="unresolved",
                confidence=0.1,
            )
        ],
    )

    reduced = reduce_controller_state(state, action, observation)

    assert reduced.knowledge_needs[0] == supported
    assert reduced.verified_evidence_ids == ["e1"]


def test_reducer_rejects_duplicate_verifier_need_results() -> None:
    state = _verification_state()
    action = VerifySupportAction(
        need_ids=["n1"],
        evidence_ids=["e1"],
    )
    duplicate = ControllerVerificationResult(
        need_id="n1",
        status="supported",
        support_concept_ids=["c1"],
        support_evidence_ids=["e1"],
        confidence=1,
    )
    observation = _verification_observation(
        action,
        [duplicate, duplicate],
    )

    with pytest.raises(ValueError, match="duplicate knowledge-need"):
        reduce_controller_state(state, action, observation)


def test_controller_step_rejects_a_forged_reducer_state() -> None:
    state = _verification_state()
    action = VerifySupportAction(
        need_ids=["n1"],
        evidence_ids=["e1"],
    )
    observation = _verification_observation(
        action,
        [
            ControllerVerificationResult(
                need_id="n1",
                status="supported",
                support_concept_ids=["c1"],
                support_evidence_ids=["e1"],
                confidence=1,
            )
        ],
    )
    canonical = reduce_controller_state(state, action, observation)
    forged = canonical.model_copy(
        update={"consecutive_no_progress": 99}
    )

    with pytest.raises(ValidationError, match="canonical reducer replay"):
        ControllerStep(
            step_index=0,
            state_before_sha256=controller_state_sha256(state),
            state_before=state,
            action=action,
            observation=observation,
            state_after=forged,
        )
