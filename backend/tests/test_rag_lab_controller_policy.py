from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from rag_lab.controller_policy import (
    ControllerDecisionContext,
    ControllerPolicy,
    EvidenceGapController,
    FixedDenseController,
    FixedDenseTypedGraphController,
)
from rag_lab.controller_schemas import (
    AbstainAction,
    AnswerAction,
    ControllerBudget,
    ControllerCost,
    ControllerKnowledgeNeed,
    ControllerState,
    ExpandTypedNeighborAction,
    SearchConceptAction,
    SearchEvidenceAction,
    VerifySupportAction,
    action_fingerprint,
)


def _need(
    *,
    need_type: str = "concept",
    status: str = "unresolved",
    support_concept_ids: list[str] | None = None,
    support_evidence_ids: list[str] | None = None,
) -> ControllerKnowledgeNeed:
    return ControllerKnowledgeNeed(
        need_id="need-1",
        description="Explain the public course concept",
        need_type=need_type,
        status=status,
        support_concept_ids=support_concept_ids or [],
        support_evidence_ids=support_evidence_ids or [],
    )


def _state(
    *,
    need: ControllerKnowledgeNeed | None = None,
    concept_ids: list[str] | None = None,
    evidence_ids: list[str] | None = None,
    verified_ids: list[str] | None = None,
    relation_ids: list[str] | None = None,
    attempted: list[str] | None = None,
    concept_searches: int = 0,
    evidence_searches: int = 0,
    graph_expansions: int = 0,
    verifications: int = 0,
    no_progress: int = 0,
) -> ControllerState:
    concepts = concept_ids or []
    evidence = evidence_ids or []
    verified = verified_ids or []
    relations = relation_ids or []
    steps = (
        concept_searches
        + evidence_searches
        + graph_expansions
        + verifications
    )
    return ControllerState(
        question_id="q-1",
        question="Explain the public course concept",
        knowledge_needs=[need or _need()],
        retrieved_concept_ids=concepts,
        retrieved_evidence_ids=evidence,
        verified_evidence_ids=verified,
        traversed_relation_ids=relations,
        attempted_action_fingerprints=attempted or [],
        consecutive_no_progress=no_progress,
        step_index=steps,
        cost=ControllerCost(
            steps=steps,
            retrieval_calls=(
                concept_searches
                + evidence_searches
                + graph_expansions
            ),
            concept_searches=concept_searches,
            evidence_searches=evidence_searches,
            graph_expansions=graph_expansions,
            verifications=verifications,
            unique_concepts=len(concepts),
            unique_evidence=len(evidence),
        ),
    )


def _context(
    state: ControllerState,
    budget: ControllerBudget | None = None,
) -> ControllerDecisionContext:
    return ControllerDecisionContext(
        state=state,
        budget=budget or ControllerBudget(),
    )


def test_policy_boundary_contains_only_runtime_public_state() -> None:
    assert set(ControllerDecisionContext.model_fields) == {
        "state",
        "budget",
    }
    with pytest.raises(ValidationError):
        ControllerDecisionContext.model_validate(
            {
                "state": _state(),
                "budget": ControllerBudget(),
                "benchmark_gold": {"required_concept_ids": ["secret"]},
            }
        )

    for policy_type in (
        FixedDenseController,
        FixedDenseTypedGraphController,
        EvidenceGapController,
    ):
        policy = policy_type()
        assert isinstance(policy, ControllerPolicy)
        signature = str(inspect.signature(policy_type)).lower()
        assert "gold" not in signature
        assert "benchmark" not in signature


def test_initialize_uses_only_public_question_text() -> None:
    policies: list[ControllerPolicy] = [
        FixedDenseController(),
        FixedDenseTypedGraphController(),
        EvidenceGapController(),
    ]
    for policy in policies:
        needs = policy.initialize(
            "public-question-id",
            "  What   is a singular value? ",
        )
        assert needs == [
            ControllerKnowledgeNeed(
                need_id="question-need",
                description="What is a singular value?",
                need_type="concept",
            )
        ]


def test_evidence_gap_public_cues_make_graph_branch_reachable() -> None:
    policy = EvidenceGapController()
    relation_need = policy.initialize(
        "q-relation",
        "How does the training function compare with prediction?",
    )[0]
    prerequisite_need = policy.initialize(
        "q-prerequisite",
        "What do I need to know before learning backpropagation?",
    )[0]

    assert relation_need.need_type == "relation"
    assert prerequisite_need.need_type == "prerequisite"
    action = policy.decide(
        _context(
            _state(
                need=relation_need,
                concept_ids=["concept-1"],
                concept_searches=1,
            )
        )
    )
    assert isinstance(action, ExpandTypedNeighborAction)


def test_fixed_dense_controller_follows_one_pass_sequence() -> None:
    policy = FixedDenseController(top_k=7)

    concept_action = policy.decide(_context(_state()))
    assert isinstance(concept_action, SearchConceptAction)
    assert concept_action.top_k == 7

    evidence_action = policy.decide(
        _context(
            _state(
                concept_ids=["concept-1"],
                concept_searches=1,
            )
        )
    )
    assert isinstance(evidence_action, SearchEvidenceAction)
    assert evidence_action.scope_concept_ids == ["concept-1"]

    verify_action = policy.decide(
        _context(
            _state(
                concept_ids=["concept-1"],
                evidence_ids=["evidence-1"],
                concept_searches=1,
                evidence_searches=1,
            )
        )
    )
    assert isinstance(verify_action, VerifySupportAction)
    assert verify_action.evidence_ids == ["evidence-1"]

    answer = policy.decide(
        _context(
            _state(
                need=_need(
                    status="supported",
                    support_concept_ids=["concept-1"],
                    support_evidence_ids=["evidence-1"],
                ),
                concept_ids=["concept-1"],
                evidence_ids=["evidence-1"],
                verified_ids=["evidence-1"],
                concept_searches=1,
                evidence_searches=1,
                verifications=1,
            )
        )
    )
    assert isinstance(answer, AnswerAction)
    assert answer.supported_need_ids == ["need-1"]


def test_fixed_dense_abstains_when_evidence_search_is_empty() -> None:
    action = FixedDenseController().decide(
        _context(
            _state(
                concept_ids=["concept-1"],
                concept_searches=1,
                evidence_searches=1,
            )
        )
    )
    assert action == AbstainAction(reason_code="insufficient_evidence")


def test_fixed_policy_never_repeats_an_attempted_action() -> None:
    policy = FixedDenseController()
    first = policy.decide(_context(_state()))
    repeated = _state(attempted=[action_fingerprint(first)])
    assert policy.decide(_context(repeated)) == AbstainAction(
        reason_code="no_progress"
    )


def test_fixed_typed_graph_inserts_exactly_one_graph_step() -> None:
    policy = FixedDenseTypedGraphController(
        relation_types=("prerequisite", "contrast_with"),
        direction="both",
        max_neighbors_per_anchor=3,
    )
    graph_action = policy.decide(
        _context(
            _state(
                concept_ids=["concept-1"],
                concept_searches=1,
            )
        )
    )
    assert isinstance(graph_action, ExpandTypedNeighborAction)
    assert graph_action.anchor_concept_ids == ["concept-1"]
    assert graph_action.relation_types == [
        "prerequisite",
        "contrast_with",
    ]
    assert graph_action.max_neighbors_per_anchor == 3

    evidence_action = policy.decide(
        _context(
            _state(
                concept_ids=["concept-1", "concept-2"],
                relation_ids=["relation-1"],
                concept_searches=1,
                graph_expansions=1,
            )
        )
    )
    assert isinstance(evidence_action, SearchEvidenceAction)
    assert evidence_action.scope_concept_ids == [
        "concept-1",
        "concept-2",
    ]


@pytest.mark.parametrize(
    ("need", "state_kwargs", "expected_type"),
    [
        (
            _need(need_type="evidence"),
            {},
            SearchEvidenceAction,
        ),
        (
            _need(
                need_type="prerequisite",
                status="partially_supported",
                support_concept_ids=["concept-1"],
            ),
            {"concept_ids": ["concept-1"], "concept_searches": 1},
            ExpandTypedNeighborAction,
        ),
        (
            _need(
                status="partially_supported",
                support_concept_ids=["concept-1"],
                support_evidence_ids=["evidence-1"],
            ),
            {
                "concept_ids": ["concept-1"],
                "evidence_ids": ["evidence-1"],
                "verified_ids": ["evidence-1"],
                "concept_searches": 1,
                "evidence_searches": 1,
            },
            SearchEvidenceAction,
        ),
    ],
)
def test_evidence_gap_selects_action_from_need_type_and_status(
    need: ControllerKnowledgeNeed,
    state_kwargs: dict[str, object],
    expected_type: type[object],
) -> None:
    state = _state(need=need, **state_kwargs)
    first = EvidenceGapController().decide(_context(state))
    second = EvidenceGapController().decide(_context(state))
    assert isinstance(first, expected_type)
    assert first == second


def test_evidence_gap_uses_prerequisite_direction() -> None:
    action = EvidenceGapController().decide(
        _context(
            _state(
                need=_need(
                    need_type="prerequisite",
                    status="partially_supported",
                    support_concept_ids=["concept-1"],
                ),
                concept_ids=["concept-1"],
                concept_searches=1,
            )
        )
    )
    assert isinstance(action, ExpandTypedNeighborAction)
    assert action.relation_types == ["prerequisite"]
    assert action.direction == "incoming"


def test_evidence_gap_moves_from_found_concept_to_evidence() -> None:
    action = EvidenceGapController().decide(
        _context(
            _state(
                concept_ids=["concept-1"],
                concept_searches=1,
            )
        )
    )
    assert isinstance(action, SearchEvidenceAction)
    assert action.scope_concept_ids == ["concept-1"]


def test_evidence_gap_stops_on_supported_or_contradicted_need() -> None:
    supported = _state(
        need=_need(
            status="supported",
            support_concept_ids=["concept-1"],
            support_evidence_ids=["evidence-1"],
        ),
        concept_ids=["concept-1"],
        evidence_ids=["evidence-1"],
        verified_ids=["evidence-1"],
    )
    answer = EvidenceGapController().decide(_context(supported))
    assert answer == AnswerAction(supported_need_ids=["need-1"])

    contradicted = EvidenceGapController().decide(
        _context(_state(need=_need(status="contradicted")))
    )
    assert contradicted == AbstainAction(
        reason_code="contradictory_evidence"
    )


def test_evidence_gap_does_not_repeat_an_identical_failed_search() -> None:
    policy = EvidenceGapController()
    state = _state()
    first = policy.decide(_context(state))
    assert isinstance(first, SearchConceptAction)

    repeated_state = _state(
        attempted=[action_fingerprint(first)],
        concept_searches=1,
    )
    fallback = policy.decide(_context(repeated_state))
    assert isinstance(fallback, SearchEvidenceAction)

    no_progress_state = _state(
        attempted=[
            action_fingerprint(first),
            action_fingerprint(fallback),
        ],
        concept_searches=1,
        evidence_searches=1,
    )
    stopped = policy.decide(_context(no_progress_state))
    assert stopped == AbstainAction(reason_code="no_progress")


def test_policy_returns_budget_abstention_when_required_action_is_blocked() -> None:
    budget = ControllerBudget(
        max_retrieval_calls=0,
        max_concept_searches=0,
        max_evidence_searches=0,
        max_graph_expansions=0,
    )
    action = FixedDenseController().decide(
        _context(_state(), budget)
    )
    assert action == AbstainAction(reason_code="budget_exhausted")
