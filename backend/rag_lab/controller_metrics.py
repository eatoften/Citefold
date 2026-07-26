from __future__ import annotations

from statistics import mean
from typing import Literal

from pydantic import Field

from .controller_schemas import (
    ControllerModel,
    ControllerRelationType,
    ControllerTrace,
    GraphExpansionObservation,
)
from .schemas import RagBenchmarkItem


class ControllerPathEdgeTarget(ControllerModel):
    relation_id: str = Field(min_length=1)
    source_concept_id: str = Field(min_length=1)
    target_concept_id: str = Field(min_length=1)
    relation_type: ControllerRelationType
    traversal_direction: Literal["incoming", "outgoing"]


class ControllerEvaluationTarget(ControllerModel):
    """System-independent labels used to score one controller trajectory."""

    question_id: str = Field(min_length=1)
    answerable: bool
    required_concept_ids: list[str] = Field(default_factory=list)
    evidence_requirement_alternatives: list[list[list[str]]] = Field(
        default_factory=list
    )
    valid_relation_path_alternatives: list[
        list[ControllerPathEdgeTarget]
    ] = Field(
        default_factory=list
    )


class ControllerTraceMetrics(ControllerModel):
    question_id: str
    policy_name: str
    answerable: bool
    predicted_answerable: bool | None
    stop_correct: bool | None
    required_concept_recall: float | None
    evidence_requirement_recall: float | None
    valid_path_success: bool | None
    first_full_concept_step: int | None
    first_full_evidence_step: int | None
    retrieval_calls: int = Field(ge=0)
    concept_searches: int = Field(ge=0)
    evidence_searches: int = Field(ge=0)
    graph_expansions: int = Field(ge=0)
    verifications: int = Field(ge=0)
    steps: int = Field(ge=0)
    unique_concepts: int = Field(ge=0)
    unique_evidence: int = Field(ge=0)
    context_characters: int = Field(ge=0)
    prompt_characters: int = Field(ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    elapsed_milliseconds: float = Field(ge=0)
    stop_reason: str
    status: str


class ControllerAggregateMetrics(ControllerModel):
    question_count: int = Field(ge=1)
    evaluated_question_count: int = Field(ge=0)
    failed_question_count: int = Field(ge=0)
    answerable_count: int = Field(ge=0)
    stop_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    mean_required_concept_recall: float | None
    mean_evidence_requirement_recall: float | None
    valid_path_success_rate: float | None
    mean_retrieval_calls: float = Field(ge=0)
    mean_concept_searches: float = Field(ge=0)
    mean_evidence_searches: float = Field(ge=0)
    mean_graph_expansions: float = Field(ge=0)
    mean_verifications: float = Field(ge=0)
    mean_steps: float = Field(ge=0)
    mean_unique_concepts: float = Field(ge=0)
    mean_unique_evidence: float = Field(ge=0)
    mean_context_characters: float = Field(ge=0)
    mean_prompt_characters: float = Field(ge=0)
    mean_completion_tokens: float | None = Field(default=None, ge=0)
    mean_elapsed_milliseconds: float = Field(ge=0)


def target_from_legacy_item(
    item: RagBenchmarkItem,
) -> ControllerEvaluationTarget:
    """Explicit compatibility adapter; cards are only concept proxies here."""

    evidence_requirements = [
        [[reference.evidence_id]]
        for reference in item.evidence
    ]
    return ControllerEvaluationTarget(
        question_id=item.question_id,
        answerable=item.answerable,
        required_concept_ids=list(item.gold_card_ids),
        evidence_requirement_alternatives=evidence_requirements,
    )


def evaluate_controller_trace(
    trace: ControllerTrace,
    target: ControllerEvaluationTarget,
) -> ControllerTraceMetrics:
    if trace.question_id != target.question_id:
        raise ValueError("Trace and evaluation target question ids differ.")

    total_cost = (
        trace.final_state.cost.plus(trace.terminal_decision_cost).plus(
            trace.terminal_environment_cost
        )
    )
    final_concepts = set(trace.final_state.retrieved_concept_ids)
    final_evidence = set(trace.final_state.retrieved_evidence_ids)
    required_concepts = set(target.required_concept_ids)

    evaluation_valid = trace.status != "failed"
    if target.answerable and evaluation_valid:
        concept_recall = _set_recall(final_concepts, required_concepts)
        evidence_recall = _requirement_recall(
            final_evidence,
            target.evidence_requirement_alternatives,
        )
        path_success = _path_success(
            trace,
            target.valid_relation_path_alternatives,
        )
        first_concept_step = _first_full_step(
            trace,
            required_concepts,
            field_name="retrieved_concept_ids",
        )
        first_evidence_step = _first_requirement_step(
            trace,
            target.evidence_requirement_alternatives,
        )
    else:
        concept_recall = None
        evidence_recall = None
        path_success = None
        first_concept_step = None
        first_evidence_step = None

    predicted_answerable = (
        trace.stop_reason == "answer" if evaluation_valid else None
    )
    return ControllerTraceMetrics(
        question_id=trace.question_id,
        policy_name=trace.policy_name,
        answerable=target.answerable,
        predicted_answerable=predicted_answerable,
        stop_correct=(
            predicted_answerable == target.answerable
            if predicted_answerable is not None
            else None
        ),
        required_concept_recall=concept_recall,
        evidence_requirement_recall=evidence_recall,
        valid_path_success=path_success,
        first_full_concept_step=first_concept_step,
        first_full_evidence_step=first_evidence_step,
        retrieval_calls=total_cost.retrieval_calls,
        concept_searches=total_cost.concept_searches,
        evidence_searches=total_cost.evidence_searches,
        graph_expansions=total_cost.graph_expansions,
        verifications=total_cost.verifications,
        steps=total_cost.steps,
        unique_concepts=total_cost.unique_concepts,
        unique_evidence=total_cost.unique_evidence,
        context_characters=total_cost.context_characters,
        prompt_characters=total_cost.prompt_characters,
        completion_tokens=total_cost.completion_tokens,
        elapsed_milliseconds=total_cost.elapsed_milliseconds,
        stop_reason=trace.stop_reason,
        status=trace.status,
    )


def aggregate_controller_metrics(
    records: list[ControllerTraceMetrics],
) -> ControllerAggregateMetrics:
    if not records:
        raise ValueError("At least one controller metric record is required.")
    concept_values = [
        value
        for record in records
        if (value := record.required_concept_recall) is not None
    ]
    evidence_values = [
        value
        for record in records
        if (value := record.evidence_requirement_recall) is not None
    ]
    path_values = [
        float(value)
        for record in records
        if (value := record.valid_path_success) is not None
    ]
    stop_values = [
        float(record.stop_correct)
        for record in records
        if record.stop_correct is not None
    ]
    completion_values = [
        record.completion_tokens
        for record in records
        if record.completion_tokens is not None
    ]
    return ControllerAggregateMetrics(
        question_count=len(records),
        evaluated_question_count=sum(
            record.status != "failed" for record in records
        ),
        failed_question_count=sum(
            record.status == "failed" for record in records
        ),
        answerable_count=sum(record.answerable for record in records),
        stop_accuracy=mean(stop_values) if stop_values else None,
        mean_required_concept_recall=(
            mean(concept_values) if concept_values else None
        ),
        mean_evidence_requirement_recall=(
            mean(evidence_values) if evidence_values else None
        ),
        valid_path_success_rate=mean(path_values) if path_values else None,
        mean_retrieval_calls=mean(
            record.retrieval_calls for record in records
        ),
        mean_concept_searches=mean(
            record.concept_searches for record in records
        ),
        mean_evidence_searches=mean(
            record.evidence_searches for record in records
        ),
        mean_graph_expansions=mean(
            record.graph_expansions for record in records
        ),
        mean_verifications=mean(
            record.verifications for record in records
        ),
        mean_steps=mean(record.steps for record in records),
        mean_unique_concepts=mean(
            record.unique_concepts for record in records
        ),
        mean_unique_evidence=mean(
            record.unique_evidence for record in records
        ),
        mean_context_characters=mean(
            record.context_characters for record in records
        ),
        mean_prompt_characters=mean(
            record.prompt_characters for record in records
        ),
        mean_completion_tokens=(
            mean(completion_values)
            if len(completion_values) == len(records)
            else None
        ),
        mean_elapsed_milliseconds=mean(
            record.elapsed_milliseconds for record in records
        ),
    )


def _set_recall(retrieved: set[str], required: set[str]) -> float:
    if not required:
        return 1.0
    return len(retrieved.intersection(required)) / len(required)


def _requirement_recall(
    retrieved: set[str],
    requirements: list[list[list[str]]],
) -> float:
    if not requirements:
        return 1.0
    satisfied = sum(
        any(set(alternative).issubset(retrieved) for alternative in group)
        for group in requirements
    )
    return satisfied / len(requirements)


def _path_success(
    trace: ControllerTrace,
    paths: list[list[ControllerPathEdgeTarget]],
) -> bool | None:
    if not paths:
        return None
    observed = [
        (
            hit.relation_id,
            hit.source_concept_id,
            hit.target_concept_id,
            hit.relation_type,
            hit.traversal_direction,
        )
        for step in trace.steps
        if isinstance(step.observation, GraphExpansionObservation)
        for hit in sorted(step.observation.hits, key=lambda item: item.rank)
        if hit.relation_id in step.observation.novel_relation_ids
    ]
    expected_paths = [
        [
            (
                edge.relation_id,
                edge.source_concept_id,
                edge.target_concept_id,
                edge.relation_type,
                edge.traversal_direction,
            )
            for edge in path
        ]
        for path in paths
    ]
    return any(_is_subsequence(path, observed) for path in expected_paths)


def _is_subsequence(
    expected: list[tuple[str, str, str, str, str]],
    observed: list[tuple[str, str, str, str, str]],
) -> bool:
    if not expected:
        return True
    index = 0
    for item in observed:
        if item == expected[index]:
            index += 1
            if index == len(expected):
                return True
    return False


def _first_full_step(
    trace: ControllerTrace,
    required: set[str],
    *,
    field_name: str,
) -> int | None:
    if not required:
        return 0
    states = [trace.initial_state, *(step.state_after for step in trace.steps)]
    for state in states:
        if required.issubset(set(getattr(state, field_name))):
            return state.step_index
    return None


def _first_requirement_step(
    trace: ControllerTrace,
    requirements: list[list[list[str]]],
) -> int | None:
    if not requirements:
        return 0
    states = [trace.initial_state, *(step.state_after for step in trace.steps)]
    for state in states:
        if _requirement_recall(
            set(state.retrieved_evidence_ids),
            requirements,
        ) == 1.0:
            return state.step_index
    return None
