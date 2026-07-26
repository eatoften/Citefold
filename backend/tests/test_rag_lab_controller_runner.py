from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import pytest
from pydantic import ValidationError

from rag_lab.controller_policy import (
    ControllerDecisionOutcome,
    ControllerInitializationOutcome,
)
from rag_lab.controller_runner import (
    ControllerAnswerOutcome,
    ControllerDecisionContext,
    ControllerVerificationOutcome,
    run_controller_episode,
)
from rag_lab.controller_schemas import (
    AbstainAction,
    AnswerAction,
    ConceptSearchObservation,
    ControllerAction,
    ControllerBudget,
    ControllerConceptHit,
    ControllerCost,
    ControllerEvidenceHit,
    ControllerKnowledgeNeed,
    ControllerProtocol,
    ControllerRelationHit,
    ControllerTrace,
    ControllerVerificationResult,
    EvidenceSearchObservation,
    ExpandTypedNeighborAction,
    GraphExpansionObservation,
    SearchConceptAction,
    SearchEvidenceAction,
    VerifySupportAction,
    action_fingerprint,
    controller_protocol_payload_sha256,
    controller_trace_payload_sha256,
)


class _ScriptedPolicy:
    name = "scripted"

    def __init__(self, actions: Sequence[object]) -> None:
        self._actions = list(actions)
        self._index = 0

    def initialize(
        self,
        *,
        question_id: str,
        question: str,
    ) -> list[ControllerKnowledgeNeed]:
        return [
            ControllerKnowledgeNeed(
                need_id="need-1",
                description=question,
            )
        ]

    def decide(
        self,
        context: ControllerDecisionContext,
    ) -> object:
        action = self._actions[self._index]
        self._index += 1
        return action


class _MeteredPolicy(_ScriptedPolicy):
    def __init__(
        self,
        actions: Sequence[ControllerAction],
        *,
        initialization_cost: ControllerCost | None = None,
        decision_cost: ControllerCost | None = None,
    ) -> None:
        super().__init__(actions)
        self.initialization_cost = initialization_cost or ControllerCost()
        self.decision_cost = decision_cost or ControllerCost()

    def initialize(
        self,
        *,
        question_id: str,
        question: str,
    ) -> ControllerInitializationOutcome:
        return ControllerInitializationOutcome(
            needs=super().initialize(
                question_id=question_id,
                question=question,
            ),
            cost=self.initialization_cost,
        )

    def decide(
        self,
        context: ControllerDecisionContext,
    ) -> ControllerDecisionOutcome:
        return ControllerDecisionOutcome(
            action=super().decide(context),
            cost=self.decision_cost,
        )


class _FailingInitializationPolicy:
    name = "scripted"

    def initialize(self, **_) -> list[ControllerKnowledgeNeed]:
        raise RuntimeError("planner initialization failed")

    def decide(self, context: ControllerDecisionContext) -> ControllerAction:
        raise AssertionError("decide must not run after initialization fails")


class _ToyMemory:
    def __init__(
        self,
        *,
        return_hits: bool = True,
        fail: bool = False,
        concept_cost: ControllerCost | None = None,
    ) -> None:
        self.return_hits = return_hits
        self.fail = fail
        self.concept_cost = concept_cost
        self.concept_calls = 0

    def search_concepts(
        self,
        action: SearchConceptAction,
        *,
        seen_concept_ids: set[str],
        max_novel_concepts: int | None = None,
        context_character_limit: int | None = None,
    ) -> ConceptSearchObservation:
        self.concept_calls += 1
        if self.fail:
            raise OSError("index unavailable")
        returned = ["concept-1"] if self.return_hits else []
        novel = [item for item in returned if item not in seen_concept_ids]
        duplicate = [item for item in returned if item in seen_concept_ids]
        return ConceptSearchObservation(
            action_fingerprint=action_fingerprint(action),
            novel_concept_ids=novel,
            duplicate_ids=duplicate,
            hits=[
                ControllerConceptHit(
                    concept_id="concept-1",
                    score=1,
                    rank=1,
                    retrieval_source="toy",
                )
            ]
            if returned
            else [],
            cost=self.concept_cost
            or ControllerCost(
                steps=1,
                retrieval_calls=1,
                concept_searches=1,
                unique_concepts=len(novel),
                context_characters=10 if returned else 0,
            ),
        )

    def search_evidence(
        self,
        action: SearchEvidenceAction,
        *,
        seen_evidence_ids: set[str],
        max_novel_evidence: int | None = None,
        context_character_limit: int | None = None,
    ) -> EvidenceSearchObservation:
        returned = ["evidence-1"] if self.return_hits else []
        novel = [item for item in returned if item not in seen_evidence_ids]
        duplicate = [item for item in returned if item in seen_evidence_ids]
        return EvidenceSearchObservation(
            action_fingerprint=action_fingerprint(action),
            novel_evidence_ids=novel,
            duplicate_ids=duplicate,
            hits=[
                ControllerEvidenceHit(
                    evidence_id="evidence-1",
                    concept_id="concept-1",
                    claim_id="claim-1",
                    score=1,
                    rank=1,
                    retrieval_source="toy",
                )
            ]
            if returned
            else [],
            cost=ControllerCost(
                steps=1,
                retrieval_calls=1,
                evidence_searches=1,
                unique_evidence=len(novel),
                context_characters=12 if returned else 0,
            ),
        )

    def expand_typed_neighbors(
        self,
        action: ExpandTypedNeighborAction,
        *,
        seen_concept_ids: set[str],
        traversed_relation_ids: set[str],
        max_novel_concepts: int | None = None,
        context_character_limit: int | None = None,
    ) -> GraphExpansionObservation:
        return GraphExpansionObservation(
            action_fingerprint=action_fingerprint(action),
            hits=[
                ControllerRelationHit(
                    relation_id="relation-1",
                    source_concept_id="concept-1",
                    target_concept_id="concept-2",
                    relation_type="related",
                    traversal_direction="outgoing",
                    score=1,
                    rank=1,
                )
            ],
            novel_concept_ids=["concept-2"],
            novel_relation_ids=["relation-1"],
            cost=ControllerCost(
                steps=1,
                retrieval_calls=1,
                graph_expansions=1,
                unique_concepts=1,
                context_characters=10,
            ),
        )


class _ToyVerifier:
    def verify(
        self,
        action: VerifySupportAction,
        state,
    ) -> ControllerVerificationOutcome:
        return ControllerVerificationOutcome(
            results=[
                ControllerVerificationResult(
                    need_id=need_id,
                    status="supported",
                    support_concept_ids=["concept-1"],
                    support_evidence_ids=["evidence-1"],
                    confidence=1,
                )
                for need_id in action.need_ids
            ]
        )


class _ToyAnswerer:
    def answer(
        self,
        action: AnswerAction,
        state,
    ) -> ControllerAnswerOutcome:
        return ControllerAnswerOutcome(
            answer="Grounded answer.",
            citation_evidence_ids=["evidence-1"],
        )


def _protocol(
    *,
    budget: ControllerBudget | None = None,
) -> ControllerProtocol:
    values = dict(
        protocol_id="controller-test",
        corpus_sha256="a" * 64,
        review_sha256="b" * 64,
        memory_sha256="c" * 64,
        policy_name="scripted",
        budget=budget or ControllerBudget(),
    )
    provisional = ControllerProtocol(
        **values,
        protocol_sha256="0" * 64,
    )
    return ControllerProtocol(
        **values,
        protocol_sha256=controller_protocol_payload_sha256(provisional),
    )


def _run(
    actions: Sequence[object] = (),
    *,
    memory: _ToyMemory | None = None,
    protocol: ControllerProtocol | None = None,
    policy: object | None = None,
):
    return run_controller_episode(
        question_id="question-1",
        question="Why?",
        policy=policy or _ScriptedPolicy(actions),  # type: ignore[arg-type]
        memory=memory or _ToyMemory(),
        verifier=_ToyVerifier(),
        answerer=_ToyAnswerer(),
        protocol=protocol or _protocol(),
        memory_id="memory-1",
        trace_id="trace-1",
    )


def test_controller_runner_records_replayable_success_trace() -> None:
    trace = _run(
        [
            SearchConceptAction(
                need_id="need-1",
                query="Why?",
                top_k=1,
            ),
            SearchEvidenceAction(
                need_ids=["need-1"],
                query="Why?",
                scope_concept_ids=["concept-1"],
                top_k=1,
            ),
            VerifySupportAction(
                need_ids=["need-1"],
                evidence_ids=["evidence-1"],
            ),
            AnswerAction(supported_need_ids=["need-1"]),
        ]
    )
    assert trace.status == "completed"
    assert trace.stop_reason == "answer"
    assert trace.final_answer == "Grounded answer."
    assert trace.citation_evidence_ids == ["evidence-1"]
    assert trace.final_state.cost.steps == 4
    assert trace.final_state.cost.retrieval_calls == 2
    assert trace.final_state.verified_evidence_ids == ["evidence-1"]
    assert len(trace.trace_sha256) == 64


def test_duplicate_action_is_blocked_without_executing_twice() -> None:
    action = SearchConceptAction(
        need_id="need-1",
        query="Why?",
        top_k=1,
    )
    trace = _run([action, action])
    assert trace.status == "forced_abstain"
    assert trace.stop_reason == "invalid_action"
    assert len(trace.steps) == 1


def test_two_no_progress_steps_force_abstention() -> None:
    trace = _run(
        [
            SearchConceptAction(
                need_id="need-1",
                query="first query",
                top_k=1,
            ),
            SearchConceptAction(
                need_id="need-1",
                query="second query",
                top_k=1,
            ),
            AbstainAction(reason_code="no_progress"),
        ],
        memory=_ToyMemory(return_hits=False),
    )
    assert trace.status == "forced_abstain"
    assert trace.stop_reason == "no_progress"
    assert len(trace.steps) == 2


def test_budget_is_checked_before_second_retrieval() -> None:
    budget = ControllerBudget(
        max_retrieval_calls=1,
        max_concept_searches=1,
        max_evidence_searches=0,
        max_graph_expansions=0,
    )
    trace = _run(
        [
            SearchConceptAction(
                need_id="need-1",
                query="Why?",
                top_k=1,
            ),
            SearchEvidenceAction(
                need_ids=["need-1"],
                query="Why?",
                scope_concept_ids=["concept-1"],
                top_k=1,
            ),
        ],
        protocol=_protocol(budget=budget),
    )
    assert trace.status == "forced_abstain"
    assert trace.stop_reason == "budget_exhausted"
    assert trace.final_state.cost.retrieval_calls == 1
    assert isinstance(trace.terminal_proposed_action, SearchEvidenceAction)
    assert trace.terminal_minimum_action_cost == ControllerCost(
        steps=1,
        retrieval_calls=1,
        evidence_searches=1,
    )
    assert trace.budget_exhausted_fields == [
        "retrieval_calls",
        "evidence_searches",
    ]


def test_environment_failure_is_not_counted_as_model_abstention() -> None:
    trace = _run(
        [
            SearchConceptAction(
                need_id="need-1",
                query="Why?",
                top_k=1,
            )
        ],
        memory=_ToyMemory(fail=True),
    )
    assert trace.status == "failed"
    assert trace.stop_reason == "environment_error"
    assert trace.error_type == "OSError"


def test_malformed_policy_output_fails_closed_as_invalid_action() -> None:
    trace = _run(
        [
            {
                "action_type": "search_concept",
                "need_id": "need-1",
                "query": "Why?",
                "top_k": 1,
                "unexpected": "field",
            }
        ]  # type: ignore[list-item]
    )
    assert trace.status == "forced_abstain"
    assert trace.stop_reason == "invalid_action"
    assert trace.error_type is None


def test_policy_initialization_failure_is_a_failed_trace() -> None:
    trace = _run(policy=_FailingInitializationPolicy())

    assert trace.status == "failed"
    assert trace.stop_reason == "policy_error"
    assert trace.error_type == "RuntimeError"
    assert trace.error_message == "planner initialization failed"
    assert trace.steps == []


@pytest.mark.parametrize(
    ("decision_cost", "budget"),
    [
        (
            ControllerCost(elapsed_milliseconds=10_000),
            ControllerBudget(max_elapsed_milliseconds=1_000),
        ),
        (
            ControllerCost(completion_tokens=2),
            ControllerBudget(max_completion_tokens=1),
        ),
    ],
    ids=["elapsed", "completion"],
)
def test_decision_budget_is_checked_before_action_execution(
    decision_cost: ControllerCost,
    budget: ControllerBudget,
) -> None:
    memory = _ToyMemory()
    action = SearchConceptAction(
        need_id="need-1",
        query="Why?",
        top_k=1,
    )
    trace = _run(
        memory=memory,
        protocol=_protocol(budget=budget),
        policy=_MeteredPolicy(
            [action],
            decision_cost=decision_cost,
        ),
    )

    assert trace.status == "forced_abstain"
    assert trace.stop_reason == "budget_exhausted"
    assert trace.steps == []
    assert (
        trace.terminal_decision_cost.completion_tokens
        == decision_cost.completion_tokens
    )
    assert (
        trace.terminal_decision_cost.elapsed_milliseconds
        >= decision_cost.elapsed_milliseconds
    )
    assert memory.concept_calls == 0


def test_unavoidable_elapsed_overrun_is_recorded_before_budget_stop() -> None:
    memory = _ToyMemory(
        concept_cost=ControllerCost(
            steps=1,
            retrieval_calls=1,
            concept_searches=1,
            unique_concepts=1,
            context_characters=10,
            elapsed_milliseconds=10_000,
        )
    )
    budget = ControllerBudget(max_elapsed_milliseconds=1_000)
    trace = _run(
        [
            SearchConceptAction(
                need_id="need-1",
                query="Why?",
                top_k=1,
            )
        ],
        memory=memory,
        protocol=_protocol(budget=budget),
    )

    assert trace.status == "forced_abstain"
    assert trace.stop_reason == "budget_exhausted"
    assert len(trace.steps) == 1
    assert trace.final_state.cost.elapsed_milliseconds >= 10_000


def test_over_budget_completion_from_environment_fails_the_trace() -> None:
    memory = _ToyMemory(
        concept_cost=ControllerCost(
            steps=1,
            retrieval_calls=1,
            concept_searches=1,
            unique_concepts=1,
            context_characters=10,
            completion_tokens=2,
        )
    )
    budget = ControllerBudget(max_completion_tokens=1)
    trace = _run(
        [
            SearchConceptAction(
                need_id="need-1",
                query="Why?",
                top_k=1,
            )
        ],
        memory=memory,
        protocol=_protocol(budget=budget),
    )

    assert trace.status == "failed"
    assert trace.stop_reason == "environment_error"
    assert trace.steps == []
    assert trace.terminal_environment_cost.completion_tokens == 2


def test_completed_trace_requires_a_terminal_action_and_committed_cost() -> None:
    trace = _run([AbstainAction(reason_code="insufficient_evidence")])
    without_terminal = trace.model_copy(
        update={
            "steps": [],
            "final_state": trace.initial_state,
            "trace_sha256": "0" * 64,
        }
    )
    without_terminal = without_terminal.model_copy(
        update={
            "trace_sha256": controller_trace_payload_sha256(
                without_terminal
            )
        }
    )
    with pytest.raises(ValidationError, match="final abstain action"):
        ControllerTrace.model_validate(without_terminal.model_dump())

    uncommitted = trace.model_copy(
        update={
            "terminal_decision_cost": ControllerCost(
                prompt_characters=1
            ),
            "trace_sha256": "0" * 64,
        }
    )
    uncommitted = uncommitted.model_copy(
        update={
            "trace_sha256": controller_trace_payload_sha256(uncommitted)
        }
    )
    with pytest.raises(ValidationError, match="uncommitted terminal cost"):
        ControllerTrace.model_validate(uncommitted.model_dump())


def test_nonfinite_policy_cost_fails_without_creating_noncanonical_trace() -> None:
    invalid_cost = ControllerCost().model_copy(
        update={"elapsed_milliseconds": float("inf")}
    )
    trace = _run(
        [AbstainAction(reason_code="insufficient_evidence")],
        policy=_MeteredPolicy(
            [AbstainAction(reason_code="insufficient_evidence")],
            decision_cost=invalid_cost,
        ),
    )

    assert trace.status == "failed"
    assert trace.stop_reason == "policy_error"
    assert trace.error_type == "ValidationError"
    assert trace.terminal_decision_cost.elapsed_milliseconds >= 0


def test_trace_timestamps_must_be_timezone_aware() -> None:
    trace = _run([AbstainAction(reason_code="insufficient_evidence")])
    naive = trace.model_copy(
        update={
            "created_at": datetime(2026, 7, 25),
            "completed_at": datetime(2026, 7, 25),
            "trace_sha256": "0" * 64,
        }
    )
    naive = naive.model_copy(
        update={"trace_sha256": controller_trace_payload_sha256(naive)}
    )

    with pytest.raises(ValidationError, match="timezone-aware"):
        ControllerTrace.model_validate(naive.model_dump())


def test_max_steps_trace_rejects_fake_terminal_cost() -> None:
    trace = _run(
        [
            SearchConceptAction(
                need_id="need-1",
                query="Why?",
                top_k=1,
            )
        ],
        protocol=_protocol(budget=ControllerBudget(max_steps=1)),
    )
    assert trace.stop_reason == "max_steps"
    forged = trace.model_copy(
        update={
            "terminal_decision_cost": ControllerCost(
                prompt_characters=1
            ),
            "trace_sha256": "0" * 64,
        }
    )
    forged = forged.model_copy(
        update={"trace_sha256": controller_trace_payload_sha256(forged)}
    )

    with pytest.raises(ValidationError, match="cannot carry terminal cost"):
        ControllerTrace.model_validate(forged.model_dump())
