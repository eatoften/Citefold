from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from pydantic import Field

from .controller_schemas import (
    AbstainAction,
    AnswerAction,
    ControllerAction,
    ControllerBudget,
    ControllerCost,
    ControllerKnowledgeNeed,
    ControllerModel,
    ControllerNeedStatus,
    ControllerRelationDirection,
    ControllerRelationType,
    ControllerState,
    ExpandTypedNeighborAction,
    SearchConceptAction,
    SearchEvidenceAction,
    VerifySupportAction,
    action_fingerprint,
)


_MAX_QUERY_CHARACTERS = 2_000
_PUBLIC_NEED_ID = "question-need"
_ALL_RELATION_TYPES: tuple[ControllerRelationType, ...] = (
    "prerequisite",
    "related",
    "example_of",
    "contrast_with",
    "part_of",
)


class ControllerDecisionContext(ControllerModel):
    """Runtime-only policy input.

    Deliberately limited to the observable controller state and the public
    budget. Benchmark annotations and evaluation labels do not belong in the
    policy boundary.
    """

    state: ControllerState
    budget: ControllerBudget


class ControllerInitializationOutcome(ControllerModel):
    """Optional metered result for learned/LLM need planners."""

    needs: list[ControllerKnowledgeNeed]
    cost: ControllerCost = Field(default_factory=ControllerCost)


class ControllerDecisionOutcome(ControllerModel):
    """Optional metered result for learned/LLM action policies."""

    action: ControllerAction
    cost: ControllerCost = Field(default_factory=ControllerCost)


@runtime_checkable
class ControllerPolicy(Protocol):
    """Deterministic policy contract used by the controller runtime."""

    name: str

    def initialize(
        self,
        question_id: str,
        question: str,
    ) -> list[ControllerKnowledgeNeed] | ControllerInitializationOutcome:
        """Create knowledge needs from public question text only."""

    def decide(
        self,
        context: ControllerDecisionContext,
    ) -> ControllerAction | ControllerDecisionOutcome:
        """Select the next action from observable runtime state."""


def _public_question_need(
    question_id: str,
    question: str,
) -> list[ControllerKnowledgeNeed]:
    if not question_id.strip():
        raise ValueError("question_id cannot be blank.")
    normalized_question = " ".join(question.strip().split())
    if not normalized_question:
        raise ValueError("question cannot be blank.")
    return [
        ControllerKnowledgeNeed(
            need_id=_PUBLIC_NEED_ID,
            description=normalized_question,
            need_type="concept",
            required=True,
        )
    ]


def _bounded_query(value: str) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError("A controller search query cannot be blank.")
    return normalized[:_MAX_QUERY_CHARACTERS].rstrip()


def _target_needs(
    state: ControllerState,
) -> list[ControllerKnowledgeNeed]:
    required = [need for need in state.knowledge_needs if need.required]
    return required or list(state.knowledge_needs)


def _pending_need_ids(state: ControllerState) -> list[str]:
    targets = _target_needs(state)
    pending = [
        need.need_id
        for need in targets
        if need.status != "supported"
    ]
    return pending or [need.need_id for need in targets]


def _supported_need_ids(state: ControllerState) -> list[str]:
    return [
        need.need_id
        for need in state.knowledge_needs
        if need.status == "supported"
    ]


def _terminal_from_status(
    state: ControllerState,
) -> ControllerAction | None:
    targets = _target_needs(state)
    statuses: set[ControllerNeedStatus] = {
        need.status for need in targets
    }
    if "contradicted" in statuses:
        return AbstainAction(reason_code="contradictory_evidence")
    if "unresolvable" in statuses:
        return AbstainAction(reason_code="insufficient_evidence")
    if all(need.status == "supported" for need in targets):
        supported_ids = _supported_need_ids(state)
        if supported_ids:
            return AnswerAction(supported_need_ids=supported_ids)
    return None


def _fixed_terminal(state: ControllerState) -> ControllerAction:
    terminal = _terminal_from_status(state)
    if terminal is not None:
        return terminal
    return AbstainAction(reason_code="insufficient_evidence")


def _new_evidence_ids(state: ControllerState) -> list[str]:
    verified = set(state.verified_evidence_ids)
    return [
        evidence_id
        for evidence_id in state.retrieved_evidence_ids
        if evidence_id not in verified
    ]


def _action_is_fresh(
    state: ControllerState,
    action: ControllerAction,
) -> bool:
    return action_fingerprint(action) not in set(
        state.attempted_action_fingerprints
    )


def _can_search_concepts(
    state: ControllerState,
    budget: ControllerBudget,
) -> bool:
    return (
        state.cost.retrieval_calls < budget.max_retrieval_calls
        and state.cost.concept_searches < budget.max_concept_searches
    )


def _can_search_evidence(
    state: ControllerState,
    budget: ControllerBudget,
) -> bool:
    return (
        state.cost.retrieval_calls < budget.max_retrieval_calls
        and state.cost.evidence_searches < budget.max_evidence_searches
    )


def _can_expand_graph(
    state: ControllerState,
    budget: ControllerBudget,
) -> bool:
    return (
        state.cost.retrieval_calls < budget.max_retrieval_calls
        and state.cost.graph_expansions < budget.max_graph_expansions
    )


def _can_verify(
    state: ControllerState,
    budget: ControllerBudget,
) -> bool:
    return state.cost.verifications < budget.max_verifications


def _hard_stop(
    state: ControllerState,
    budget: ControllerBudget,
) -> ControllerAction | None:
    if state.step_index >= budget.max_steps:
        return AbstainAction(reason_code="budget_exhausted")
    if (
        budget.max_elapsed_milliseconds is not None
        and state.cost.elapsed_milliseconds
        >= budget.max_elapsed_milliseconds
    ):
        return AbstainAction(reason_code="budget_exhausted")
    if (
        state.consecutive_no_progress
        >= budget.max_consecutive_no_progress
    ):
        return AbstainAction(reason_code="no_progress")
    return None


def _evidence_query(
    state: ControllerState,
    need_ids: Sequence[str],
) -> str:
    selected = set(need_ids)
    descriptions = [
        need.description
        for need in state.knowledge_needs
        if need.need_id in selected
    ]
    return _bounded_query(" ".join(descriptions) or state.question)


class _DeterministicController:
    name = "deterministic"

    def __init__(self, *, top_k: int = 5) -> None:
        if top_k < 1 or top_k > 100:
            raise ValueError("top_k must be between 1 and 100.")
        self.top_k = top_k

    def initialize(
        self,
        question_id: str,
        question: str,
    ) -> list[ControllerKnowledgeNeed]:
        return _public_question_need(question_id, question)

    def _top_k(self, budget: ControllerBudget) -> int:
        return min(self.top_k, budget.max_top_k)

    def _concept_action(
        self,
        state: ControllerState,
        budget: ControllerBudget,
        need: ControllerKnowledgeNeed,
    ) -> SearchConceptAction:
        return SearchConceptAction(
            need_id=need.need_id,
            query=_bounded_query(need.description),
            top_k=self._top_k(budget),
            exclude_concept_ids=state.retrieved_concept_ids,
        )

    def _evidence_action(
        self,
        state: ControllerState,
        budget: ControllerBudget,
        need_ids: Sequence[str],
    ) -> SearchEvidenceAction:
        return SearchEvidenceAction(
            need_ids=list(need_ids),
            query=_evidence_query(state, need_ids),
            scope_concept_ids=state.retrieved_concept_ids,
            top_k=self._top_k(budget),
            exclude_evidence_ids=state.retrieved_evidence_ids,
        )

    @staticmethod
    def _verification_action(
        state: ControllerState,
        need_ids: Sequence[str],
    ) -> VerifySupportAction | None:
        evidence_ids = _new_evidence_ids(state)
        if not evidence_ids:
            return None
        return VerifySupportAction(
            need_ids=list(need_ids),
            evidence_ids=evidence_ids,
        )


class FixedDenseController(_DeterministicController):
    """One-pass dense baseline with a fixed auditable action sequence."""

    name = "fixed_dense"

    def decide(
        self,
        context: ControllerDecisionContext,
    ) -> ControllerAction:
        state = context.state
        budget = context.budget

        terminal = _terminal_from_status(state)
        if terminal is not None:
            return terminal
        hard_stop = _hard_stop(state, budget)
        if hard_stop is not None:
            return hard_stop

        target_needs = _target_needs(state)
        need_ids = _pending_need_ids(state)

        if state.cost.concept_searches == 0:
            if not _can_search_concepts(state, budget):
                return AbstainAction(reason_code="budget_exhausted")
            action = self._concept_action(
                state,
                budget,
                target_needs[0],
            )
            if not _action_is_fresh(state, action):
                return AbstainAction(reason_code="no_progress")
            return action

        if state.cost.evidence_searches == 0:
            if not _can_search_evidence(state, budget):
                return AbstainAction(reason_code="budget_exhausted")
            action = self._evidence_action(
                state,
                budget,
                need_ids,
            )
            if not _action_is_fresh(state, action):
                return AbstainAction(reason_code="no_progress")
            return action

        if state.cost.verifications == 0:
            action = self._verification_action(state, need_ids)
            if action is None:
                return AbstainAction(reason_code="insufficient_evidence")
            if not _can_verify(state, budget):
                return AbstainAction(reason_code="budget_exhausted")
            if not _action_is_fresh(state, action):
                return AbstainAction(reason_code="no_progress")
            return action

        return _fixed_terminal(state)


class FixedDenseTypedGraphController(_DeterministicController):
    """Fixed dense baseline with one typed graph expansion inserted."""

    name = "fixed_dense_typed_graph"

    def __init__(
        self,
        *,
        top_k: int = 5,
        relation_types: Sequence[ControllerRelationType] = (
            "prerequisite",
            "related",
            "example_of",
            "contrast_with",
            "part_of",
        ),
        direction: ControllerRelationDirection = "both",
        max_neighbors_per_anchor: int = 5,
    ) -> None:
        super().__init__(top_k=top_k)
        self.relation_types = _validate_relation_types(relation_types)
        if direction not in {"incoming", "outgoing", "both"}:
            raise ValueError("Unsupported graph-expansion direction.")
        self.direction = direction
        if max_neighbors_per_anchor < 1 or max_neighbors_per_anchor > 100:
            raise ValueError(
                "max_neighbors_per_anchor must be between 1 and 100."
            )
        self.max_neighbors_per_anchor = max_neighbors_per_anchor

    def _graph_action(
        self,
        state: ControllerState,
        budget: ControllerBudget,
        need: ControllerKnowledgeNeed,
    ) -> ExpandTypedNeighborAction:
        anchor_limit = budget.max_anchors_per_expansion
        return ExpandTypedNeighborAction(
            need_id=need.need_id,
            anchor_concept_ids=state.retrieved_concept_ids[:anchor_limit],
            relation_types=list(self.relation_types),
            direction=self.direction,
            max_neighbors_per_anchor=min(
                self.max_neighbors_per_anchor,
                budget.max_top_k,
            ),
            exclude_relation_ids=state.traversed_relation_ids,
        )

    def decide(
        self,
        context: ControllerDecisionContext,
    ) -> ControllerAction:
        state = context.state
        budget = context.budget

        terminal = _terminal_from_status(state)
        if terminal is not None:
            return terminal
        hard_stop = _hard_stop(state, budget)
        if hard_stop is not None:
            return hard_stop

        target_needs = _target_needs(state)
        need_ids = _pending_need_ids(state)

        if state.cost.concept_searches == 0:
            if not _can_search_concepts(state, budget):
                return AbstainAction(reason_code="budget_exhausted")
            action = self._concept_action(
                state,
                budget,
                target_needs[0],
            )
            if not _action_is_fresh(state, action):
                return AbstainAction(reason_code="no_progress")
            return action

        if (
            state.cost.graph_expansions == 0
            and state.retrieved_concept_ids
            and _can_expand_graph(state, budget)
        ):
            action = self._graph_action(
                state,
                budget,
                target_needs[0],
            )
            if not _action_is_fresh(state, action):
                return AbstainAction(reason_code="no_progress")
            return action

        if state.cost.evidence_searches == 0:
            if not _can_search_evidence(state, budget):
                return AbstainAction(reason_code="budget_exhausted")
            action = self._evidence_action(
                state,
                budget,
                need_ids,
            )
            if not _action_is_fresh(state, action):
                return AbstainAction(reason_code="no_progress")
            return action

        if state.cost.verifications == 0:
            action = self._verification_action(state, need_ids)
            if action is None:
                return AbstainAction(reason_code="insufficient_evidence")
            if not _can_verify(state, budget):
                return AbstainAction(reason_code="budget_exhausted")
            if not _action_is_fresh(state, action):
                return AbstainAction(reason_code="no_progress")
            return action

        return _fixed_terminal(state)


class EvidenceGapController(_DeterministicController):
    """Single-need heuristic controller driven by public question/state only."""

    name = "evidence_gap"

    def __init__(
        self,
        *,
        top_k: int = 5,
        relation_types: Sequence[ControllerRelationType] = (
            "prerequisite",
            "related",
            "example_of",
            "contrast_with",
            "part_of",
        ),
        max_neighbors_per_anchor: int = 5,
    ) -> None:
        super().__init__(top_k=top_k)
        self.relation_types = _validate_relation_types(relation_types)
        if max_neighbors_per_anchor < 1 or max_neighbors_per_anchor > 100:
            raise ValueError(
                "max_neighbors_per_anchor must be between 1 and 100."
            )
        self.max_neighbors_per_anchor = max_neighbors_per_anchor

    def initialize(
        self,
        question_id: str,
        question: str,
    ) -> list[ControllerKnowledgeNeed]:
        base = _public_question_need(question_id, question)[0]
        normalized = base.description.lower()
        prerequisite_cues = (
            "prerequisite",
            "before learning",
            "before understanding",
            "need to know",
            "depends on",
        )
        relation_cues = (
            "compare",
            "contrast",
            "difference between",
            "relationship between",
            "relate to",
        )
        if any(cue in normalized for cue in prerequisite_cues):
            need_type = "prerequisite"
        elif any(cue in normalized for cue in relation_cues):
            need_type = "relation"
        else:
            need_type = "concept"
        return [
            ControllerKnowledgeNeed(
                need_id=base.need_id,
                description=base.description,
                need_type=need_type,
                required=True,
            )
        ]

    def _relation_policy(
        self,
        need: ControllerKnowledgeNeed,
    ) -> tuple[
        tuple[ControllerRelationType, ...],
        ControllerRelationDirection,
    ]:
        if need.need_type == "prerequisite":
            return ("prerequisite",), "incoming"
        return self.relation_types, "both"

    def _graph_action(
        self,
        state: ControllerState,
        budget: ControllerBudget,
        need: ControllerKnowledgeNeed,
    ) -> ExpandTypedNeighborAction | None:
        anchors = (
            need.support_concept_ids or state.retrieved_concept_ids
        )
        if not anchors:
            return None
        relation_types, direction = self._relation_policy(need)
        return ExpandTypedNeighborAction(
            need_id=need.need_id,
            anchor_concept_ids=anchors[
                : budget.max_anchors_per_expansion
            ],
            relation_types=list(relation_types),
            direction=direction,
            max_neighbors_per_anchor=min(
                self.max_neighbors_per_anchor,
                budget.max_top_k,
            ),
            exclude_relation_ids=state.traversed_relation_ids,
        )

    def decide(
        self,
        context: ControllerDecisionContext,
    ) -> ControllerAction:
        state = context.state
        budget = context.budget

        terminal = _terminal_from_status(state)
        if terminal is not None:
            return terminal
        hard_stop = _hard_stop(state, budget)
        if hard_stop is not None:
            return hard_stop

        targets = _target_needs(state)
        need = next(
            item for item in targets if item.status != "supported"
        )

        verification = self._verification_action(
            state,
            [need.need_id],
        )
        if (
            verification is not None
            and state.cost.evidence_searches > 0
        ):
            if _can_verify(state, budget):
                return verification
            return AbstainAction(reason_code="budget_exhausted")

        if need.need_type in {"prerequisite", "relation"}:
            graph_action = self._graph_action(state, budget, need)
            graph_not_yet_used = state.cost.graph_expansions == 0
            if graph_action is not None and graph_not_yet_used:
                if _can_expand_graph(state, budget):
                    if _action_is_fresh(state, graph_action):
                        return graph_action
                elif not _can_search_evidence(state, budget):
                    return AbstainAction(reason_code="budget_exhausted")

            if graph_action is None and need.status == "unresolved":
                if _can_search_concepts(state, budget):
                    concept_action = self._concept_action(
                        state,
                        budget,
                        need,
                    )
                    if _action_is_fresh(state, concept_action):
                        return concept_action
                elif not _can_search_evidence(state, budget):
                    return AbstainAction(reason_code="budget_exhausted")

        elif (
            need.need_type == "concept"
            and need.status == "unresolved"
            and not state.retrieved_concept_ids
        ):
            concept_action = self._concept_action(
                state,
                budget,
                need,
            )
            if _can_search_concepts(state, budget):
                if _action_is_fresh(state, concept_action):
                    return concept_action
            elif not _can_search_evidence(state, budget):
                return AbstainAction(reason_code="budget_exhausted")

        if _can_search_evidence(state, budget):
            evidence_action = self._evidence_action(
                state,
                budget,
                [need.need_id],
            )
            if _action_is_fresh(state, evidence_action):
                return evidence_action
            return AbstainAction(reason_code="no_progress")

        if verification is not None and _can_verify(state, budget):
            return verification
        return AbstainAction(reason_code="budget_exhausted")


def _validate_relation_types(
    relation_types: Sequence[ControllerRelationType],
) -> tuple[ControllerRelationType, ...]:
    values = tuple(relation_types)
    if not values:
        raise ValueError("At least one relation type is required.")
    if len(values) != len(set(values)):
        raise ValueError("relation_types must be unique.")
    unsupported = set(values) - set(_ALL_RELATION_TYPES)
    if unsupported:
        raise ValueError(
            f"Unsupported relation types: {sorted(unsupported)}."
        )
    return values
