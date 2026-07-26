from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Protocol
from uuid import uuid4

from pydantic import Field

from .controller_policy import (
    ControllerDecisionContext,
    ControllerDecisionOutcome,
    ControllerInitializationOutcome,
    ControllerPolicy,
)
from .controller_schemas import (
    AbstainAction,
    AbstainObservation,
    AnswerAction,
    AnswerObservation,
    ConceptSearchObservation,
    ControllerAction,
    ControllerBudget,
    ControllerBudgetField,
    ControllerCost,
    ControllerEvidenceNode,
    ControllerKnowledgeNeed,
    ControllerModel,
    ControllerObservation,
    ControllerProtocol,
    ControllerState,
    ControllerStep,
    ControllerTrace,
    ControllerVerificationResult,
    CONTROLLER_ACTION_ADAPTER,
    CONTROLLER_OBSERVATION_ADAPTER,
    EvidenceSearchObservation,
    ExpandTypedNeighborAction,
    GraphExpansionObservation,
    SearchConceptAction,
    SearchEvidenceAction,
    VerificationObservation,
    VerifySupportAction,
    action_fingerprint,
    audit_controller_observation,
    controller_minimum_action_cost,
    controller_state_sha256,
    controller_trace_payload_sha256,
    reduce_controller_state,
    utc_now,
)
from .retrievers import tokenize_for_retrieval


class ControllerMemory(Protocol):
    def search_concepts(
        self,
        action: SearchConceptAction,
        *,
        seen_concept_ids: set[str],
        max_novel_concepts: int | None = None,
        context_character_limit: int | None = None,
    ) -> ConceptSearchObservation: ...

    def search_evidence(
        self,
        action: SearchEvidenceAction,
        *,
        seen_evidence_ids: set[str],
        max_novel_evidence: int | None = None,
        context_character_limit: int | None = None,
    ) -> EvidenceSearchObservation: ...

    def expand_typed_neighbors(
        self,
        action: ExpandTypedNeighborAction,
        *,
        seen_concept_ids: set[str],
        traversed_relation_ids: set[str],
        max_novel_concepts: int | None = None,
        context_character_limit: int | None = None,
    ) -> GraphExpansionObservation: ...


class EvidenceSupportVerifier(Protocol):
    def verify(
        self,
        action: VerifySupportAction,
        state: ControllerState,
    ) -> "ControllerVerificationOutcome": ...


class ControllerAnswerer(Protocol):
    def answer(
        self,
        action: AnswerAction,
        state: ControllerState,
    ) -> "ControllerAnswerOutcome": ...


class ControllerVerificationOutcome(ControllerModel):
    results: list[ControllerVerificationResult]
    cost: ControllerCost = Field(default_factory=ControllerCost)


class ControllerAnswerOutcome(ControllerModel):
    answer: str = Field(min_length=1)
    citation_evidence_ids: list[str] = Field(min_length=1)
    cost: ControllerCost = Field(default_factory=ControllerCost)


class ControllerActionRejected(ValueError):
    pass


class ControllerBudgetExhausted(ControllerActionRejected):
    pass


class ControllerPolicyFailure(RuntimeError):
    pass


class ControllerEnvironmentFailure(RuntimeError):
    pass


def run_controller_episode(
    *,
    question_id: str,
    question: str,
    policy: ControllerPolicy,
    memory: ControllerMemory,
    verifier: EvidenceSupportVerifier,
    answerer: ControllerAnswerer,
    protocol: ControllerProtocol,
    memory_id: str,
    trace_id: str | None = None,
) -> ControllerTrace:
    if protocol.policy_name != policy.name:
        raise ValueError("Protocol policy_name does not match the policy.")

    started_at = utc_now()
    initialize_started = time.perf_counter()
    initialization_error: Exception | None = None
    try:
        raw_initialization = policy.initialize(
            question_id=question_id,
            question=question,
        )
        initialize_elapsed = (
            time.perf_counter() - initialize_started
        ) * 1000
        if isinstance(raw_initialization, ControllerInitializationOutcome):
            initial_needs = raw_initialization.needs
            initialization_cost = _with_measured_elapsed(
                raw_initialization.cost,
                initialize_elapsed,
            )
        else:
            initial_needs = raw_initialization
            initialization_cost = ControllerCost(
                prompt_characters=len(question),
                elapsed_milliseconds=initialize_elapsed,
            )
        _audit_policy_cost(initialization_cost, protocol.budget)
        initial_state = ControllerState(
            question_id=question_id,
            question=question,
            knowledge_needs=initial_needs,
            cost=initialization_cost,
        )
    except Exception as exc:
        initialization_error = exc
        initialization_cost = ControllerCost(
            prompt_characters=len(question),
            elapsed_milliseconds=(
                time.perf_counter() - initialize_started
            )
            * 1000,
        )
        initial_state = ControllerState(
            question_id=question_id,
            question=question,
            knowledge_needs=[
                ControllerKnowledgeNeed(
                    need_id="policy-initialization-failed",
                    description=question,
                )
            ],
            cost=initialization_cost,
        )
    state = initial_state
    steps: list[ControllerStep] = []
    stop_reason = "max_steps"
    status = "forced_abstain"
    final_answer: str | None = None
    citation_evidence_ids: list[str] = []
    error_type: str | None = None
    error_message: str | None = None
    terminal_decision_cost = ControllerCost()
    terminal_environment_cost = ControllerCost()
    terminal_proposed_action: ControllerAction | None = None
    terminal_minimum_action_cost = ControllerCost()
    budget_exhausted_fields: list[ControllerBudgetField] = []

    try:
        if initialization_error is not None:
            raise ControllerPolicyFailure(
                str(initialization_error)
            ) from initialization_error
        while True:
            exceeded = state.cost.exceeded_limits(protocol.budget)
            if exceeded:
                stop_reason = "budget_exhausted"
                status = "forced_abstain"
                budget_exhausted_fields = exceeded
                break
            if state.step_index >= protocol.budget.max_steps:
                stop_reason = "max_steps"
                status = "forced_abstain"
                break
            try:
                decision_context = ControllerDecisionContext(
                    state=state,
                    budget=protocol.budget,
                )
                decision_started = time.perf_counter()
                raw_decision = policy.decide(decision_context)
                decision_elapsed = (
                    time.perf_counter() - decision_started
                ) * 1000
            except Exception as exc:
                terminal_decision_cost = ControllerCost(
                    prompt_characters=len(
                        decision_context.model_dump_json()
                    ),
                    completion_tokens=None,
                    elapsed_milliseconds=(
                        time.perf_counter() - decision_started
                    )
                    * 1000,
                )
                raise ControllerPolicyFailure(str(exc)) from exc
            try:
                if isinstance(raw_decision, ControllerDecisionOutcome):
                    raw_action = raw_decision.action
                    decision_cost = _with_measured_elapsed(
                        raw_decision.cost,
                        decision_elapsed,
                    )
                else:
                    raw_action = raw_decision
                    decision_cost = ControllerCost(
                        prompt_characters=len(
                            decision_context.model_dump_json()
                        ),
                        elapsed_milliseconds=decision_elapsed,
                    )
            except Exception as exc:
                terminal_decision_cost = ControllerCost(
                    prompt_characters=len(
                        decision_context.model_dump_json()
                    ),
                    completion_tokens=None,
                    elapsed_milliseconds=decision_elapsed,
                )
                raise ControllerPolicyFailure(
                    f"Policy returned invalid cost metadata: {exc}"
                ) from exc
            terminal_decision_cost = decision_cost
            _audit_policy_cost(decision_cost, protocol.budget)
            try:
                action = CONTROLLER_ACTION_ADAPTER.validate_python(
                    raw_action
                )
            except Exception as exc:
                raise ControllerActionRejected(
                    f"Policy returned an invalid action: {exc}"
                ) from exc
            _guard_action(
                action,
                state,
                protocol,
                check_budget=False,
            )
            minimum_action_cost = controller_minimum_action_cost(action)
            projected_cost = state.cost.plus(decision_cost).plus(
                minimum_action_cost
            )
            exceeded = projected_cost.exceeded_limits(protocol.budget)
            if exceeded:
                stop_reason = "budget_exhausted"
                status = "forced_abstain"
                terminal_proposed_action = action
                terminal_minimum_action_cost = minimum_action_cost
                budget_exhausted_fields = exceeded
                break
            action_started = time.perf_counter()
            observation: ControllerObservation | None = None
            try:
                observation, answer_outcome = _execute_action(
                    action=action,
                    state=state,
                    memory=memory,
                    verifier=verifier,
                    answerer=answerer,
                    budget=protocol.budget,
                )
                action_elapsed = (
                    time.perf_counter() - action_started
                ) * 1000
                observation = observation.model_copy(
                    update={
                        "cost": _with_measured_elapsed(
                            observation.cost,
                            action_elapsed,
                        )
                    }
                )
                _audit_observation(
                    action,
                    observation,
                    state,
                    protocol.budget,
                )
                new_state = reduce_controller_state(
                    state,
                    action,
                    observation,
                    decision_cost=decision_cost,
                )
            except ControllerActionRejected:
                raise
            except Exception as exc:
                measured_environment_cost = ControllerCost(
                    completion_tokens=None,
                    elapsed_milliseconds=(
                        time.perf_counter() - action_started
                    )
                    * 1000,
                )
                terminal_environment_cost = (
                    observation.cost
                    if observation is not None
                    else measured_environment_cost
                )
                raise ControllerEnvironmentFailure(str(exc)) from exc
            step = ControllerStep(
                step_index=state.step_index,
                state_before_sha256=controller_state_sha256(state),
                state_before=state,
                decision_cost=decision_cost,
                action=action,
                observation=observation,
                state_after=new_state,
            )
            steps.append(step)
            state = new_state
            terminal_decision_cost = ControllerCost()
            terminal_environment_cost = ControllerCost()

            exceeded = state.cost.exceeded_limits(protocol.budget)
            if exceeded:
                stop_reason = "budget_exhausted"
                status = "forced_abstain"
                budget_exhausted_fields = exceeded
                break
            if isinstance(action, AnswerAction):
                if answer_outcome is None:
                    raise RuntimeError("Answer action returned no answer.")
                allowed_citations = {
                    evidence_id
                    for need in state.knowledge_needs
                    if need.need_id in action.supported_need_ids
                    for evidence_id in need.support_evidence_ids
                }
                if not set(answer_outcome.citation_evidence_ids).issubset(
                    allowed_citations
                ):
                    raise RuntimeError(
                        "Answer citations must support the answer action's "
                        "declared knowledge needs."
                    )
                stop_reason = "answer"
                status = "completed"
                final_answer = answer_outcome.answer
                citation_evidence_ids = (
                    answer_outcome.citation_evidence_ids
                )
                break
            if isinstance(action, AbstainAction):
                stop_reason = "abstain"
                status = "completed"
                break
            if (
                state.consecutive_no_progress
                >= protocol.budget.max_consecutive_no_progress
            ):
                stop_reason = "no_progress"
                status = "forced_abstain"
                break
    except ControllerBudgetExhausted:
        stop_reason = "budget_exhausted"
        status = "forced_abstain"
        budget_exhausted_fields = (
            state.cost.plus(terminal_decision_cost).exceeded_limits(
                protocol.budget
            )
        )
    except ControllerActionRejected:
        stop_reason = "invalid_action"
        status = "forced_abstain"
    except ControllerPolicyFailure as exc:
        stop_reason = "policy_error"
        status = "failed"
        source = exc.__cause__ or exc
        error_type = type(source).__name__
        error_message = str(source) or repr(source)
    except ControllerEnvironmentFailure as exc:
        stop_reason = "environment_error"
        status = "failed"
        source = exc.__cause__ or exc
        error_type = type(source).__name__
        error_message = str(source) or repr(source)
    except Exception as exc:  # defensive runner boundary
        stop_reason = "environment_error"
        status = "failed"
        error_type = type(exc).__name__
        error_message = str(exc) or repr(exc)

    completed_at = utc_now()
    payload = {
        "trace_id": trace_id or f"controller-{uuid4().hex[:12]}",
        "protocol_id": protocol.protocol_id,
        "protocol_sha256": protocol.protocol_sha256,
        "memory_id": memory_id,
        "memory_sha256": protocol.memory_sha256,
        "question_id": question_id,
        "policy_name": policy.name,
        "initial_state": initial_state,
        "steps": steps,
        "final_state": state,
        "terminal_decision_cost": terminal_decision_cost,
        "terminal_environment_cost": terminal_environment_cost,
        "terminal_proposed_action": terminal_proposed_action,
        "terminal_minimum_action_cost": terminal_minimum_action_cost,
        "budget_exhausted_fields": budget_exhausted_fields,
        "stop_reason": stop_reason,
        "status": status,
        "final_answer": final_answer,
        "citation_evidence_ids": citation_evidence_ids,
        "error_type": error_type,
        "error_message": error_message,
        "created_at": started_at,
        "completed_at": completed_at,
    }
    provisional = ControllerTrace.model_construct(
        **payload,
        trace_sha256="0" * 64,
    )
    return ControllerTrace(
        **payload,
        trace_sha256=controller_trace_payload_sha256(provisional),
    )


def _guard_action(
    action: ControllerAction,
    state: ControllerState,
    protocol: ControllerProtocol,
    *,
    check_budget: bool = True,
) -> None:
    if action.action_type not in protocol.allowed_actions:
        raise ControllerActionRejected(
            f"Action is disabled by the protocol: {action.action_type}"
        )
    fingerprint = action_fingerprint(action)
    if fingerprint in state.attempted_action_fingerprints:
        raise ControllerActionRejected("An identical action cannot repeat.")

    needs = {need.need_id: need for need in state.knowledge_needs}
    referenced_need_ids: Sequence[str]
    if isinstance(action, SearchConceptAction):
        referenced_need_ids = [action.need_id]
    elif isinstance(action, SearchEvidenceAction):
        referenced_need_ids = action.need_ids
    elif isinstance(action, ExpandTypedNeighborAction):
        referenced_need_ids = [action.need_id]
    elif isinstance(action, VerifySupportAction):
        referenced_need_ids = action.need_ids
    elif isinstance(action, AnswerAction):
        referenced_need_ids = action.supported_need_ids
    else:
        referenced_need_ids = []
    unknown_needs = sorted(set(referenced_need_ids).difference(needs))
    if unknown_needs:
        raise ControllerActionRejected(
            f"Action references unknown needs: {unknown_needs}"
        )

    if isinstance(action, SearchConceptAction):
        if action.top_k > protocol.budget.max_top_k:
            raise ControllerActionRejected("Concept top_k exceeds protocol.")
    elif isinstance(action, SearchEvidenceAction):
        if action.top_k > protocol.budget.max_top_k:
            raise ControllerActionRejected("Evidence top_k exceeds protocol.")
        if not set(action.scope_concept_ids).issubset(
            state.retrieved_concept_ids
        ):
            raise ControllerActionRejected(
                "Evidence search scope must contain only retrieved concepts."
            )
    elif isinstance(action, ExpandTypedNeighborAction):
        if (
            len(action.anchor_concept_ids)
            > protocol.budget.max_anchors_per_expansion
        ):
            raise ControllerActionRejected(
                "Graph action has too many anchors."
            )
        if (
            action.max_neighbors_per_anchor > protocol.budget.max_top_k
        ):
            raise ControllerActionRejected(
                "Graph-neighbor limit exceeds protocol."
            )
        if not set(action.anchor_concept_ids).issubset(
            state.retrieved_concept_ids
        ):
            raise ControllerActionRejected(
                "Graph anchors must have been retrieved."
            )
    elif isinstance(action, VerifySupportAction):
        if not set(action.evidence_ids).issubset(
            state.retrieved_evidence_ids
        ):
            raise ControllerActionRejected(
                "Verification can only inspect retrieved evidence."
            )
    elif isinstance(action, AnswerAction):
        requested = set(action.supported_need_ids)
        required = {
            need.need_id
            for need in state.knowledge_needs
            if need.required
        }
        actually_supported = {
            need.need_id
            for need in state.knowledge_needs
            if need.status == "supported"
        }
        if not required.issubset(requested):
            raise ControllerActionRejected(
                "Answer omits a required knowledge need."
            )
        if not requested.issubset(actually_supported):
            raise ControllerActionRejected(
                "Answer cites an unsupported knowledge need."
            )

    if check_budget:
        next_cost = state.cost.plus(controller_minimum_action_cost(action))
        exceeded = next_cost.exceeded_limits(protocol.budget)
        if exceeded:
            raise ControllerBudgetExhausted(
                f"Action would exceed budget: {', '.join(exceeded)}"
            )


def _execute_action(
    *,
    action: ControllerAction,
    state: ControllerState,
    memory: ControllerMemory,
    verifier: EvidenceSupportVerifier,
    answerer: ControllerAnswerer,
    budget: ControllerBudget,
) -> tuple[ControllerObservation, ControllerAnswerOutcome | None]:
    remaining_context = max(
        0,
        budget.max_context_characters - state.cost.context_characters,
    )
    if isinstance(action, SearchConceptAction):
        return (
            memory.search_concepts(
                action,
                seen_concept_ids=set(state.retrieved_concept_ids),
                max_novel_concepts=max(
                    0,
                    budget.max_unique_concepts
                    - state.cost.unique_concepts,
                ),
                context_character_limit=remaining_context,
            ),
            None,
        )
    if isinstance(action, SearchEvidenceAction):
        return (
            memory.search_evidence(
                action,
                seen_evidence_ids=set(state.retrieved_evidence_ids),
                max_novel_evidence=max(
                    0,
                    budget.max_unique_evidence
                    - state.cost.unique_evidence,
                ),
                context_character_limit=remaining_context,
            ),
            None,
        )
    if isinstance(action, ExpandTypedNeighborAction):
        return (
            memory.expand_typed_neighbors(
                action,
                seen_concept_ids=set(state.retrieved_concept_ids),
                traversed_relation_ids=set(
                    state.traversed_relation_ids
                ),
                max_novel_concepts=max(
                    0,
                    budget.max_unique_concepts
                    - state.cost.unique_concepts,
                ),
                context_character_limit=remaining_context,
            ),
            None,
        )
    if isinstance(action, VerifySupportAction):
        outcome = verifier.verify(action, state)
        cost = ControllerCost(steps=1, verifications=1).plus(
            outcome.cost
        )
        return (
            VerificationObservation(
                action_fingerprint=action_fingerprint(action),
                results=outcome.results,
                cost=cost,
            ),
            None,
        )
    if isinstance(action, AnswerAction):
        outcome = answerer.answer(action, state)
        return (
            AnswerObservation(
                action_fingerprint=action_fingerprint(action),
                cost=ControllerCost(steps=1).plus(outcome.cost),
            ),
            outcome,
        )
    if isinstance(action, AbstainAction):
        return (
            AbstainObservation(
                action_fingerprint=action_fingerprint(action),
                reason_code=action.reason_code,
                cost=ControllerCost(steps=1),
            ),
            None,
        )
    raise ControllerActionRejected("Unsupported controller action.")


def _audit_observation(
    action: ControllerAction,
    observation: ControllerObservation,
    state: ControllerState,
    budget: ControllerBudget,
) -> None:
    try:
        canonical = CONTROLLER_OBSERVATION_ADAPTER.validate_python(
            observation.model_dump()
        )
    except Exception as exc:
        raise RuntimeError(
            f"Environment observation is not canonical: {exc}"
        ) from exc
    if canonical != observation:
        raise RuntimeError("Environment observation is not canonical.")
    if (
        budget.max_completion_tokens is not None
        and observation.cost.completion_tokens is None
    ):
        raise RuntimeError(
            "Completion-token budget requires metered observations."
        )
    audit_controller_observation(action, observation, state)
    exceeded = state.cost.plus(observation.cost).exceeded_limits(budget)
    disallowed_overruns = [
        field
        for field in exceeded
        if field != "elapsed_milliseconds"
    ]
    if disallowed_overruns:
        raise RuntimeError(
            "Environment returned an over-budget observation: "
            + ", ".join(disallowed_overruns)
        )


def _audit_policy_cost(
    cost: ControllerCost,
    budget: ControllerBudget,
    *,
    require_metered_completion: bool = True,
) -> None:
    try:
        ControllerCost.model_validate(cost.model_dump())
    except Exception as exc:
        raise ControllerPolicyFailure(
            f"Policy cost is not canonical: {exc}"
        ) from exc
    forbidden = (
        "steps",
        "retrieval_calls",
        "concept_searches",
        "evidence_searches",
        "graph_expansions",
        "verifications",
        "unique_concepts",
        "unique_evidence",
        "context_characters",
    )
    nonzero = [field for field in forbidden if getattr(cost, field) != 0]
    if nonzero:
        raise ControllerPolicyFailure(
            "Policy cost contains environment-owned fields: "
            + ", ".join(nonzero)
        )
    if (
        require_metered_completion
        and budget.max_completion_tokens is not None
        and cost.completion_tokens is None
    ):
        raise ControllerPolicyFailure(
            "Completion-token budget requires metered policy calls."
        )


def _with_measured_elapsed(
    reported: ControllerCost,
    measured_elapsed_milliseconds: float,
) -> ControllerCost:
    payload = reported.model_dump()
    payload["elapsed_milliseconds"] = max(
        reported.elapsed_milliseconds,
        measured_elapsed_milliseconds,
    )
    return ControllerCost.model_validate(payload)


class DeterministicEvidenceVerifier:
    """Transparent smoke verifier; it is a baseline, not a semantic judge."""

    def __init__(
        self,
        evidence: Sequence[ControllerEvidenceNode],
        *,
        minimum_overlap: float = 0.05,
    ) -> None:
        if not 0 <= minimum_overlap <= 1:
            raise ValueError("minimum_overlap must be in [0, 1].")
        self._evidence = {item.evidence_id: item for item in evidence}
        self.minimum_overlap = minimum_overlap

    def verify(
        self,
        action: VerifySupportAction,
        state: ControllerState,
    ) -> ControllerVerificationOutcome:
        started = time.perf_counter()
        selected = [self._evidence[item] for item in action.evidence_ids]
        needs = {need.need_id: need for need in state.knowledge_needs}
        results: list[ControllerVerificationResult] = []
        for need_id in action.need_ids:
            need = needs[need_id]
            need_tokens = set(tokenize_for_retrieval(need.description))
            scored: list[tuple[ControllerEvidenceNode, float]] = []
            for item in selected:
                evidence_tokens = set(
                    tokenize_for_retrieval(
                        f"{item.claim_text} {item.text}"
                    )
                )
                denominator = max(len(need_tokens), 1)
                score = len(need_tokens.intersection(evidence_tokens)) / (
                    denominator
                )
                scored.append((item, score))
            supported = [
                item for item, score in scored if score >= self.minimum_overlap
            ]
            best_score = max((score for _, score in scored), default=0.0)
            status = (
                "supported"
                if supported
                else ("partially_supported" if selected else "unresolvable")
            )
            results.append(
                ControllerVerificationResult(
                    need_id=need_id,
                    status=status,
                    support_concept_ids=list(
                        dict.fromkeys(item.concept_id for item in supported)
                    ),
                    support_evidence_ids=[
                        item.evidence_id for item in supported
                    ],
                    confidence=min(1.0, best_score),
                )
            )
        return ControllerVerificationOutcome(
            results=results,
            cost=ControllerCost(
                elapsed_milliseconds=(
                    time.perf_counter() - started
                )
                * 1000,
            ),
        )


class ExtractiveEvidenceAnswerer:
    """Deterministic trace smoke-answerer with evidence-bound citations."""

    def __init__(
        self,
        evidence: Sequence[ControllerEvidenceNode],
    ) -> None:
        self._evidence = {item.evidence_id: item for item in evidence}

    def answer(
        self,
        action: AnswerAction,
        state: ControllerState,
    ) -> ControllerAnswerOutcome:
        started = time.perf_counter()
        needs = {need.need_id: need for need in state.knowledge_needs}
        citation_ids = list(
            dict.fromkeys(
                evidence_id
                for need_id in action.supported_need_ids
                for evidence_id in needs[need_id].support_evidence_ids
            )
        )
        if not citation_ids:
            raise ValueError("Answer requires verified supporting evidence.")
        if not set(citation_ids).issubset(state.verified_evidence_ids):
            raise ValueError("Answer citations must be verified.")
        claims = list(
            dict.fromkeys(
                self._evidence[evidence_id].claim_text
                for evidence_id in citation_ids
            )
        )
        return ControllerAnswerOutcome(
            answer=" ".join(claims),
            citation_evidence_ids=citation_ids,
            cost=ControllerCost(
                elapsed_milliseconds=(
                    time.perf_counter() - started
                )
                * 1000,
            ),
        )
