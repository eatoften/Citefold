from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from ..controller_schemas import (
    ControllerMemorySnapshot,
    ControllerProtocol,
    ControllerState,
    ControllerTrace,
    controller_memory_payload_sha256,
    controller_protocol_payload_sha256,
    controller_trace_payload_sha256,
)
from ..io import sha256_file, sha256_value
from .audit import (
    audit_runtime_memory_binding,
    audit_seal,
    runtime_graph_payload_sha256,
    seal_payload_sha256,
)
from .schemas import (
    ControllerBenchmarkDataset,
    ControllerBenchmarkItem,
    ControllerBenchmarkReview,
    ControllerBenchmarkSeal,
    ControllerBenchmarkSplitManifest,
    ControllerSplit,
    GraphIndependenceManifest,
    RelationType,
    StrictControllerModel,
)


class PredictedReasoningEdge(StrictControllerModel):
    """A typed edge derived from graph-expansion hits in a canonical trace."""

    source_concept_id: str = Field(min_length=1)
    target_concept_id: str = Field(min_length=1)
    relation_type: RelationType

    @model_validator(mode="after")
    def validate_distinct_nodes(self) -> "PredictedReasoningEdge":
        if self.source_concept_id == self.target_concept_id:
            raise ValueError("A derived directed edge cannot be a self edge.")
        return self


class ControllerExecutionCost(StrictControllerModel):
    """Evaluation projection of cost counters recorded by ControllerTrace."""

    retrieval_calls: int = Field(ge=0)
    controller_steps: int = Field(ge=0)
    concept_searches: int = Field(ge=0)
    evidence_searches: int = Field(ge=0)
    graph_expansions: int = Field(ge=0)
    verifications: int = Field(ge=0)
    unique_concepts: int = Field(ge=0)
    unique_evidence: int = Field(ge=0)
    context_characters: int = Field(ge=0)
    prompt_characters: int = Field(ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    latency_milliseconds: float = Field(ge=0)


class TraceEvaluationInputs(StrictControllerModel):
    question_id: str
    trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_status: Literal["completed", "forced_abstain", "failed"]
    final_action: Literal["answer", "abstain"] | None
    answer_text: str | None
    citation_evidence_ids: list[str]
    retrieved_concept_ids: list[str]
    retrieved_evidence_ids: list[str]
    predicted_path_edges: list[PredictedReasoningEdge]
    cost: ControllerExecutionCost


class ControllerBenchmarkPrediction(StrictControllerModel):
    """System output binding; every scored field is derived from this trace."""

    trace: ControllerTrace

    @model_validator(mode="after")
    def validate_canonical_trace(self) -> "ControllerBenchmarkPrediction":
        # Revalidate nested models so model_copy cannot bypass trace invariants.
        validated = ControllerTrace.model_validate(
            self.trace.model_dump(mode="python")
        )
        expected_hash = controller_trace_payload_sha256(validated)
        if validated.trace_sha256 != expected_hash:
            raise ValueError("Prediction trace hash is not canonical.")
        _validate_benchmark_trace_origin(validated)
        self.trace = validated
        return self

    @property
    def question_id(self) -> str:
        return self.trace.question_id


class EvaluationRunBinding(StrictControllerModel):
    """Development-diagnostic binding of declared benchmark/runtime artifacts."""

    binding_id: str = Field(min_length=1)
    created_at: datetime
    benchmark_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    benchmark_review: ControllerBenchmarkReview
    graph_independence_manifest: GraphIndependenceManifest
    split_manifest: ControllerBenchmarkSplitManifest
    benchmark_seal: ControllerBenchmarkSeal
    split: ControllerSplit
    protocol: ControllerProtocol
    memory: ControllerMemorySnapshot
    memory_id: str = Field(min_length=1)
    memory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_name: str = Field(min_length=1)
    controller_code_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluator_code_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_preregistration(self) -> "EvaluationRunBinding":
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("Evaluation binding timestamp must be timezone-aware.")
        if self.benchmark_seal.seal_sha256 != seal_payload_sha256(
            self.benchmark_seal
        ):
            raise ValueError("Evaluation binding contains a non-canonical seal.")
        if self.benchmark_seal.benchmark_sha256 != self.benchmark_sha256:
            raise ValueError("Evaluation binding seal targets another benchmark.")
        artifact_bindings = {
            "review_sha256": self.benchmark_review.review_sha256,
            "independence_manifest_sha256": (
                self.graph_independence_manifest.manifest_sha256
            ),
            "split_manifest_sha256": self.split_manifest.manifest_sha256,
        }
        for field_name, expected in artifact_bindings.items():
            if getattr(self.benchmark_seal, field_name) != expected:
                raise ValueError(
                    f"Evaluation seal does not bind supplied {field_name}."
                )
        if self.created_at < self.benchmark_seal.sealed_at:
            raise ValueError(
                "Evaluation preregistration cannot predate the benchmark seal."
            )
        if self.protocol.protocol_sha256 != controller_protocol_payload_sha256(
            self.protocol
        ):
            raise ValueError(
                "Evaluation binding contains a non-canonical protocol."
            )
        if self.protocol.benchmark_sha256 != self.benchmark_sha256:
            raise ValueError(
                "Preregistered protocol targets another benchmark."
            )
        if self.protocol.split != self.split:
            raise ValueError("Preregistered protocol split does not match binding.")
        if self.protocol.oracle_only:
            raise ValueError(
                "Oracle-only protocols are forbidden for benchmark evaluation."
            )
        if self.protocol.memory_sha256 != self.memory_sha256:
            raise ValueError("Preregistered protocol memory hash does not match.")
        validated_memory = ControllerMemorySnapshot.model_validate(
            self.memory.model_dump(mode="python")
        )
        if (
            validated_memory.memory_sha256
            != controller_memory_payload_sha256(validated_memory)
        ):
            raise ValueError(
                "Evaluation binding contains a non-canonical controller memory."
            )
        if validated_memory.memory_id != self.memory_id:
            raise ValueError("Preregistered memory id does not match its snapshot.")
        if validated_memory.memory_sha256 != self.memory_sha256:
            raise ValueError(
                "Preregistered memory hash does not match its snapshot."
            )
        if validated_memory.corpus_sha256 != self.protocol.corpus_sha256:
            raise ValueError(
                "Preregistered memory corpus does not match the protocol."
            )
        if (
            validated_memory.concept_granularity
            != self.protocol.concept_granularity
        ):
            raise ValueError(
                "Preregistered concept granularity does not match memory."
            )
        if (
            self.graph_independence_manifest.runtime_graph_sha256
            != runtime_graph_payload_sha256(validated_memory)
        ):
            raise ValueError(
                "Graph-independence runtime graph does not match controller "
                "memory."
            )
        if self.protocol.policy_name != self.policy_name:
            raise ValueError("Preregistered policy name does not match.")
        if self.protocol.code_sha256 != self.controller_code_sha256:
            raise ValueError(
                "Preregistered protocol must bind the exact controller code hash."
            )
        if self.evaluator_code_sha256 != current_evaluator_code_sha256():
            raise ValueError(
                "Evaluation binding does not match the current evaluator code."
            )
        if self.binding_sha256 != evaluation_run_binding_payload_sha256(self):
            raise ValueError("Evaluation-run binding hash is not canonical.")
        return self


def evaluation_run_binding_payload_sha256(
    binding: EvaluationRunBinding,
) -> str:
    return sha256_value(
        binding.model_dump(
            mode="json",
            exclude={"binding_sha256"},
        )
    )


def current_evaluator_code_sha256() -> str:
    """Composite-hash local source files that can change diagnostic scores."""

    benchmark_root = Path(__file__).resolve().parent
    rag_lab_root = benchmark_root.parent
    dependencies = sorted(
        {
            *benchmark_root.glob("*.py"),
            rag_lab_root / "controller_schemas.py",
            rag_lab_root / "io.py",
        },
        key=lambda path: path.as_posix(),
    )
    return sha256_value(
        [
            {
                "path": path.relative_to(rag_lab_root).as_posix(),
                "sha256": sha256_file(path),
            }
            for path in dependencies
        ]
    )


class AnswerCorrectnessAssessment(StrictControllerModel):
    """Paper-ineligible semantic diagnostic, never supplied by the controller."""

    question_id: str = Field(min_length=1)
    trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verdict: Literal["correct", "partially_correct", "incorrect"]
    assessment_method: Literal[
        "human",
        "frozen_semantic_judge",
    ]
    evaluator_id: str = Field(min_length=1)
    assessment_protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    notes: str = Field(min_length=1)
    evaluation_scope: Literal["development_diagnostic"] = (
        "development_diagnostic"
    )
    paper_eligible: Literal[False] = False


class MetricFraction(StrictControllerModel):
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    value: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_fraction(self) -> "MetricFraction":
        if self.numerator > self.denominator:
            raise ValueError("Metric numerator cannot exceed denominator.")
        expected = (
            None if self.denominator == 0 else self.numerator / self.denominator
        )
        if self.value != expected:
            raise ValueError("Metric value must equal its exact fraction.")
        return self


class ItemControllerMetrics(StrictControllerModel):
    evaluation_scope: Literal["development_diagnostic"] = (
        "development_diagnostic"
    )
    execution_provenance_status: Literal[
        "declarative_membership_audit_only"
    ] = "declarative_membership_audit_only"
    paper_claim_eligible: Literal[False] = False
    question_id: str
    trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_status: Literal["completed", "forced_abstain", "failed"]
    final_action: Literal["answer", "abstain"] | None
    answer_text: str | None
    citation_evidence_ids: list[str]
    answer_correctness_source: Literal[
        "not_scored",
        "deterministic_exact_match",
        "development_diagnostic_external",
    ]
    answer_correctness: MetricFraction
    required_concept_recall: MetricFraction
    evidence_requirement_recall: MetricFraction
    evidence_precision: MetricFraction
    valid_path_success: MetricFraction
    best_valid_path_edge_recall: MetricFraction
    stop_correctness: MetricFraction
    hard_negative_hits: int = Field(ge=0)
    hard_negative_hit_rate: MetricFraction
    retrieval_metrics_diagnostic_only: Literal[True] = True
    retrieval_control_quality_score: float = Field(ge=0, le=1)
    retrieved_evidence_count: int = Field(ge=0)
    cost: ControllerExecutionCost


class AggregateControllerCost(StrictControllerModel):
    total_retrieval_calls: int = Field(ge=0)
    total_controller_steps: int = Field(ge=0)
    total_prompt_characters: int = Field(ge=0)
    total_completion_tokens: int | None = Field(default=None, ge=0)
    total_retrieved_evidence: int = Field(ge=0)
    mean_retrieval_calls: float = Field(ge=0)
    mean_controller_steps: float = Field(ge=0)
    mean_prompt_characters: float = Field(ge=0)
    mean_completion_tokens: float | None = Field(default=None, ge=0)
    mean_retrieved_evidence: float = Field(ge=0)
    mean_latency_milliseconds: float = Field(ge=0)


class ControllerMetricReport(StrictControllerModel):
    evaluation_scope: Literal["development_diagnostic"] = (
        "development_diagnostic"
    )
    execution_provenance_status: Literal[
        "declarative_membership_audit_only"
    ] = "declarative_membership_audit_only"
    paper_claim_eligible: Literal[False] = False
    evaluation_binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    benchmark_seal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    memory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    controller_code_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluator_code_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    split: ControllerSplit
    question_count: int = Field(ge=0)
    answerable_count: int = Field(ge=0)
    unanswerable_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    forced_abstain_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    failed_trace_retrieval_metrics_are_diagnostic_only: Literal[True] = True
    answer_scoring_status: Literal[
        "not_scored",
        "partially_scored",
        "fully_scored",
    ]
    answer_correctness_source: Literal[
        "not_scored",
        "deterministic_exact_match",
        "development_diagnostic_external",
    ]
    answer_correctness_paper_eligible: Literal[False] = False
    answer_assessment_count: int = Field(ge=0)
    answer_correctness: MetricFraction
    required_concept_recall: MetricFraction
    evidence_requirement_recall: MetricFraction
    evidence_precision: MetricFraction
    valid_path_success_rate: MetricFraction
    best_valid_path_edge_recall: MetricFraction
    stop_correctness: MetricFraction
    hard_negative_hits: int = Field(ge=0)
    hard_negative_hit_rate: MetricFraction
    retrieval_control_quality_score: float = Field(ge=0, le=1)
    cost: AggregateControllerCost
    retrieval_control_quality_per_retrieval_call: float | None = Field(
        default=None,
        ge=0,
    )
    retrieval_control_quality_per_1000_prompt_characters: float | None = Field(
        default=None,
        ge=0,
    )
    by_item: list[ItemControllerMetrics]


def derive_trace_evaluation_inputs(
    trace: ControllerTrace,
) -> TraceEvaluationInputs:
    """Project all scorable system outputs from one validated trace."""

    validated = ControllerTrace.model_validate(trace.model_dump(mode="python"))
    expected_hash = controller_trace_payload_sha256(validated)
    if validated.trace_sha256 != expected_hash:
        raise ValueError("Trace hash is not canonical.")
    _validate_benchmark_trace_origin(validated)

    if validated.status == "failed":
        final_action: Literal["answer", "abstain"] | None = None
    elif validated.stop_reason == "answer":
        final_action = "answer"
    else:
        final_action = "abstain"

    edges: list[PredictedReasoningEdge] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for step in validated.steps:
        observation = step.observation
        if observation.action_type != "expand_typed_neighbor":
            continue
        for hit in observation.hits:
            signature = (
                hit.source_concept_id,
                hit.target_concept_id,
                hit.relation_type,
            )
            if signature in seen_edges:
                continue
            seen_edges.add(signature)
            edges.append(
                PredictedReasoningEdge(
                    source_concept_id=hit.source_concept_id,
                    target_concept_id=hit.target_concept_id,
                    relation_type=hit.relation_type,
                )
            )

    total_cost = (
        validated.final_state.cost.plus(validated.terminal_decision_cost).plus(
            validated.terminal_environment_cost
        )
    )
    return TraceEvaluationInputs(
        question_id=validated.question_id,
        trace_sha256=validated.trace_sha256,
        execution_status=validated.status,
        final_action=final_action,
        answer_text=validated.final_answer,
        citation_evidence_ids=validated.citation_evidence_ids,
        retrieved_concept_ids=validated.final_state.retrieved_concept_ids,
        retrieved_evidence_ids=validated.final_state.retrieved_evidence_ids,
        predicted_path_edges=edges,
        cost=ControllerExecutionCost(
            retrieval_calls=total_cost.retrieval_calls,
            controller_steps=total_cost.steps,
            concept_searches=total_cost.concept_searches,
            evidence_searches=total_cost.evidence_searches,
            graph_expansions=total_cost.graph_expansions,
            verifications=total_cost.verifications,
            unique_concepts=len(validated.final_state.retrieved_concept_ids),
            unique_evidence=len(validated.final_state.retrieved_evidence_ids),
            context_characters=total_cost.context_characters,
            prompt_characters=total_cost.prompt_characters,
            completion_tokens=total_cost.completion_tokens,
            latency_milliseconds=total_cost.elapsed_milliseconds,
        ),
    )


def evaluate_item_prediction(
    item: ControllerBenchmarkItem,
    prediction: ControllerBenchmarkPrediction,
    *,
    dataset: ControllerBenchmarkDataset,
    run_binding: EvaluationRunBinding,
    answer_assessment: AnswerCorrectnessAssessment | None = None,
    answer_scoring: Literal[
        "not_scored",
        "exact_match",
        "development_diagnostic",
    ] = "not_scored",
) -> ItemControllerMetrics:
    _audit_formal_evaluation_binding(dataset, run_binding)
    frozen_item = next(
        (
            candidate
            for candidate in dataset.items
            if candidate.question_id == item.question_id
        ),
        None,
    )
    if frozen_item != item:
        raise ValueError(
            "Scored item is not the exact frozen item in the sealed dataset."
        )
    if item.split == "test":
        raise ValueError(
            "Test evaluation is fail-closed until a one-use ledger and "
            "separated test-gold loader are implemented."
        )
    if (
        answer_scoring == "development_diagnostic"
        and item.split != "development"
    ):
        raise ValueError(
            "Unsealed semantic assessments are development diagnostics and "
            "cannot score test items."
        )
    _audit_trace_binding(item, prediction.trace, run_binding)
    derived = derive_trace_evaluation_inputs(prediction.trace)
    if item.question_id != derived.question_id:
        raise ValueError("Prediction trace question id does not match the item.")
    answer_correctness, answer_correctness_source = _answer_correctness(
        item,
        derived,
        answer_assessment,
        answer_scoring=answer_scoring,
    )

    predicted_concepts = set(derived.retrieved_concept_ids)
    required_concepts = {concept.concept_id for concept in item.required_concepts}
    concept_recall = _fraction(
        len(required_concepts.intersection(predicted_concepts)),
        len(required_concepts),
    )

    retrieved = set(derived.retrieved_evidence_ids)
    satisfied_requirements = sum(
        any(
            {
                reference.evidence_id
                for reference in alternative.evidence
            }.issubset(retrieved)
            for alternative in requirement.alternatives
        )
        for requirement in item.evidence_requirements
    )
    requirement_recall = _fraction(
        satisfied_requirements,
        len(item.evidence_requirements),
    )
    acceptable_evidence = {
        reference.evidence_id
        for requirement in item.evidence_requirements
        for alternative in requirement.alternatives
        for reference in alternative.evidence
    }
    evidence_precision = _fraction(
        len(retrieved.intersection(acceptable_evidence)),
        len(retrieved),
    )

    predicted_edges = {
        _edge_signature(edge) for edge in derived.predicted_path_edges
    }
    best_match = 0
    best_denominator = 0
    complete_path = False
    for path in sorted(item.valid_reasoning_paths, key=lambda value: value.path_id):
        gold_edges = {_edge_signature(edge) for edge in path.edges}
        matched = len(gold_edges.intersection(predicted_edges))
        if gold_edges.issubset(predicted_edges):
            complete_path = True
        if (
            best_denominator == 0
            or matched / len(gold_edges) > best_match / best_denominator
        ):
            best_match = matched
            best_denominator = len(gold_edges)
    path_success = _fraction(
        int(complete_path),
        int(bool(item.valid_reasoning_paths)),
    )
    path_edge_recall = _fraction(best_match, best_denominator)

    expected_action = (
        "answer" if item.answerability == "answerable" else "abstain"
    )
    stop_correct = (
        derived.execution_status != "failed"
        and derived.final_action == expected_action
    )
    stop_correctness = _fraction(int(stop_correct), 1)

    hard_negative_ids = {
        reference.evidence_id for reference in item.hard_negatives
    }
    hard_negative_hits = len(retrieved.intersection(hard_negative_ids))
    hard_negative_hit_rate = _fraction(
        hard_negative_hits,
        len(hard_negative_ids),
    )

    if derived.execution_status == "failed":
        retrieval_control_quality_score = 0.0
    else:
        quality_components = [stop_correctness.value or 0.0]
        if item.answerability == "answerable":
            quality_components.extend(
                [
                    concept_recall.value or 0.0,
                    requirement_recall.value or 0.0,
                    evidence_precision.value or 0.0,
                    path_success.value or 0.0,
                ]
            )
        elif (
            item.unanswerable_certificate is not None
            and item.unanswerable_certificate.subtype == "corpus_absent"
        ):
            quality_components.append(1.0 if not retrieved else 0.0)
        if hard_negative_hit_rate.denominator:
            quality_components.append(
                1.0 - (hard_negative_hit_rate.value or 0.0)
            )
        retrieval_control_quality_score = (
            sum(quality_components) / len(quality_components)
        )

    return ItemControllerMetrics(
        evaluation_scope="development_diagnostic",
        execution_provenance_status="declarative_membership_audit_only",
        paper_claim_eligible=False,
        question_id=item.question_id,
        trace_sha256=derived.trace_sha256,
        execution_status=derived.execution_status,
        final_action=derived.final_action,
        answer_text=derived.answer_text,
        citation_evidence_ids=derived.citation_evidence_ids,
        answer_correctness_source=answer_correctness_source,
        answer_correctness=answer_correctness,
        required_concept_recall=concept_recall,
        evidence_requirement_recall=requirement_recall,
        evidence_precision=evidence_precision,
        valid_path_success=path_success,
        best_valid_path_edge_recall=path_edge_recall,
        stop_correctness=stop_correctness,
        hard_negative_hits=hard_negative_hits,
        hard_negative_hit_rate=hard_negative_hit_rate,
        retrieval_metrics_diagnostic_only=True,
        retrieval_control_quality_score=retrieval_control_quality_score,
        retrieved_evidence_count=len(retrieved),
        cost=derived.cost,
    )


def evaluate_controller_predictions(
    dataset: ControllerBenchmarkDataset,
    predictions: list[ControllerBenchmarkPrediction],
    *,
    split: ControllerSplit,
    run_binding: EvaluationRunBinding,
    answer_assessments: list[AnswerCorrectnessAssessment] | None = None,
    answer_scoring: Literal[
        "not_scored",
        "exact_match",
        "development_diagnostic",
    ] = "not_scored",
) -> ControllerMetricReport:
    """Return development diagnostics, never formal or paper-eligible results.

    The current evaluator checks declarative trace membership and metadata
    against a supplied memory snapshot. It does not reconstruct that memory
    from canonical corpus/source artifacts, replay the deterministic retriever
    and runner, or attest retrieval scores, ranking, and actual execution.
    """

    validated_binding = EvaluationRunBinding.model_validate(
        run_binding.model_dump(mode="python")
    )
    _audit_formal_evaluation_binding(dataset, validated_binding)
    if validated_binding.split != split:
        raise ValueError("Evaluation split does not match preregistration.")
    if validated_binding.protocol.corpus_sha256 != dataset.corpus_sha256:
        raise ValueError(
            "Preregistered protocol corpus does not match the benchmark."
        )
    if split == "test":
        raise ValueError(
            "Test evaluation is fail-closed until a one-use ledger and "
            "separated test-gold loader are implemented."
        )
    selected_items = [item for item in dataset.items if item.split == split]
    if not selected_items:
        raise ValueError(f"Benchmark has no {split} items.")

    prediction_ids = [prediction.question_id for prediction in predictions]
    if len(prediction_ids) != len(set(prediction_ids)):
        raise ValueError("Predictions contain duplicate trace question ids.")
    expected_ids = {item.question_id for item in selected_items}
    observed_ids = set(prediction_ids)
    if observed_ids != expected_ids:
        missing = sorted(expected_ids - observed_ids)
        unknown = sorted(observed_ids - expected_ids)
        raise ValueError(
            f"Prediction coverage mismatch for split={split}; "
            f"missing={missing}, unknown={unknown}."
        )

    assessments = answer_assessments or []
    if answer_scoring == "development_diagnostic":
        if split != "development":
            raise ValueError(
                "Unsealed semantic assessments are development diagnostics "
                "and cannot score the test split."
            )
        if not assessments:
            raise ValueError(
                "development_diagnostic scoring requires external assessments."
            )
    elif assessments:
        raise ValueError(
            "External assessments require answer_scoring="
            "'development_diagnostic'; exact_match is derived internally."
        )
    assessment_ids = [assessment.question_id for assessment in assessments]
    if len(assessment_ids) != len(set(assessment_ids)):
        raise ValueError("Answer assessments contain duplicate question ids.")
    unknown_assessments = sorted(set(assessment_ids) - expected_ids)
    if unknown_assessments:
        raise ValueError(
            f"Answer assessments reference unselected questions: "
            f"{unknown_assessments}."
        )
    assessments_by_id = {
        assessment.question_id: assessment for assessment in assessments
    }
    predictions_by_id = {
        prediction.question_id: prediction for prediction in predictions
    }
    by_item = [
        evaluate_item_prediction(
            item,
            predictions_by_id[item.question_id],
            dataset=dataset,
            run_binding=validated_binding,
            answer_assessment=assessments_by_id.get(item.question_id),
            answer_scoring=answer_scoring,
        )
        for item in sorted(selected_items, key=lambda value: value.question_id)
    ]

    question_count = len(selected_items)
    total_retrieval_calls = sum(
        metric.cost.retrieval_calls for metric in by_item
    )
    total_controller_steps = sum(
        metric.cost.controller_steps for metric in by_item
    )
    total_prompt_characters = sum(
        metric.cost.prompt_characters for metric in by_item
    )
    completion_values = [
        metric.cost.completion_tokens for metric in by_item
    ]
    total_completion_tokens = (
        None
        if any(value is None for value in completion_values)
        else sum(value or 0 for value in completion_values)
    )
    total_retrieved_evidence = sum(
        metric.retrieved_evidence_count for metric in by_item
    )
    mean_retrieval_calls = total_retrieval_calls / question_count
    mean_prompt_characters = total_prompt_characters / question_count
    retrieval_control_quality_score = (
        sum(metric.retrieval_control_quality_score for metric in by_item)
        / question_count
    )
    assessable_answer_count = sum(
        item.answerability == "answerable" and metric.final_action == "answer"
        for item, metric in zip(
            sorted(selected_items, key=lambda value: value.question_id),
            by_item,
        )
    )
    if answer_scoring == "not_scored":
        scoring_status = "not_scored"
    elif answer_scoring == "exact_match":
        scoring_status = "fully_scored"
    elif len(assessments) < assessable_answer_count:
        scoring_status = "partially_scored"
    else:
        scoring_status = "fully_scored"
    answer_correctness_source = {
        "not_scored": "not_scored",
        "exact_match": "deterministic_exact_match",
        "development_diagnostic": "development_diagnostic_external",
    }[answer_scoring]

    return ControllerMetricReport(
        evaluation_scope="development_diagnostic",
        execution_provenance_status="declarative_membership_audit_only",
        paper_claim_eligible=False,
        evaluation_binding_sha256=validated_binding.binding_sha256,
        benchmark_seal_sha256=(
            validated_binding.benchmark_seal.seal_sha256
        ),
        protocol_sha256=validated_binding.protocol.protocol_sha256,
        memory_sha256=validated_binding.memory_sha256,
        controller_code_sha256=(
            validated_binding.controller_code_sha256
        ),
        evaluator_code_sha256=validated_binding.evaluator_code_sha256,
        split=split,
        question_count=question_count,
        answerable_count=sum(
            item.answerability == "answerable" for item in selected_items
        ),
        unanswerable_count=sum(
            item.answerability == "unanswerable" for item in selected_items
        ),
        completed_count=sum(
            metric.execution_status == "completed" for metric in by_item
        ),
        forced_abstain_count=sum(
            metric.execution_status == "forced_abstain" for metric in by_item
        ),
        failed_count=sum(
            metric.execution_status == "failed" for metric in by_item
        ),
        answer_scoring_status=scoring_status,
        answer_correctness_source=answer_correctness_source,
        answer_correctness_paper_eligible=False,
        answer_assessment_count=len(assessments),
        answer_correctness=_sum_fractions(
            metric.answer_correctness for metric in by_item
        ),
        required_concept_recall=_sum_fractions(
            metric.required_concept_recall for metric in by_item
        ),
        evidence_requirement_recall=_sum_fractions(
            metric.evidence_requirement_recall for metric in by_item
        ),
        evidence_precision=_sum_fractions(
            metric.evidence_precision for metric in by_item
        ),
        valid_path_success_rate=_sum_fractions(
            metric.valid_path_success for metric in by_item
        ),
        best_valid_path_edge_recall=_sum_fractions(
            metric.best_valid_path_edge_recall for metric in by_item
        ),
        stop_correctness=_sum_fractions(
            metric.stop_correctness for metric in by_item
        ),
        hard_negative_hits=sum(
            metric.hard_negative_hits for metric in by_item
        ),
        hard_negative_hit_rate=_sum_fractions(
            metric.hard_negative_hit_rate for metric in by_item
        ),
        retrieval_control_quality_score=retrieval_control_quality_score,
        cost=AggregateControllerCost(
            total_retrieval_calls=total_retrieval_calls,
            total_controller_steps=total_controller_steps,
            total_prompt_characters=total_prompt_characters,
            total_completion_tokens=total_completion_tokens,
            total_retrieved_evidence=total_retrieved_evidence,
            mean_retrieval_calls=mean_retrieval_calls,
            mean_controller_steps=total_controller_steps / question_count,
            mean_prompt_characters=mean_prompt_characters,
            mean_completion_tokens=(
                None
                if total_completion_tokens is None
                else total_completion_tokens / question_count
            ),
            mean_retrieved_evidence=total_retrieved_evidence / question_count,
            mean_latency_milliseconds=(
                sum(metric.cost.latency_milliseconds for metric in by_item)
                / question_count
            ),
        ),
        retrieval_control_quality_per_retrieval_call=(
            None
            if mean_retrieval_calls == 0
            else retrieval_control_quality_score / mean_retrieval_calls
        ),
        retrieval_control_quality_per_1000_prompt_characters=(
            None
            if mean_prompt_characters == 0
            else retrieval_control_quality_score
            / (mean_prompt_characters / 1000)
        ),
        by_item=by_item,
    )


def _answer_correctness(
    item: ControllerBenchmarkItem,
    derived: TraceEvaluationInputs,
    assessment: AnswerCorrectnessAssessment | None,
    *,
    answer_scoring: Literal[
        "not_scored",
        "exact_match",
        "development_diagnostic",
    ],
) -> tuple[
    MetricFraction,
    Literal[
        "not_scored",
        "deterministic_exact_match",
        "development_diagnostic_external",
    ],
]:
    if answer_scoring == "not_scored":
        if assessment is not None:
            raise ValueError(
                "External assessment supplied while answer scoring is disabled."
            )
        return _fraction(0, 0), "not_scored"
    if answer_scoring == "exact_match":
        if assessment is not None:
            raise ValueError(
                "Exact match is derived from trace text and frozen references; "
                "an external verdict is forbidden."
            )
        if item.answerability != "answerable":
            return _fraction(0, 0), "deterministic_exact_match"
        prediction_text = (
            _normalize_answer(derived.answer_text)
            if derived.final_action == "answer" and derived.answer_text
            else None
        )
        references = {
            _normalize_answer(reference)
            for reference in item.reference_answers
        }
        return (
            _fraction(int(prediction_text in references), 1),
            "deterministic_exact_match",
        )
    if assessment is None:
        return _fraction(0, 0), "development_diagnostic_external"
    if item.answerability != "answerable" or derived.final_action != "answer":
        raise ValueError(
            "Answer correctness can be assessed only for an answerable item "
            "with an answer trace."
        )
    if assessment.question_id != item.question_id:
        raise ValueError("Answer assessment question id does not match the item.")
    if assessment.trace_sha256 != derived.trace_sha256:
        raise ValueError("Answer assessment is bound to a different trace.")
    numerator = {
        "incorrect": 0,
        "partially_correct": 1,
        "correct": 2,
    }[assessment.verdict]
    return (
        _fraction(numerator, 2),
        "development_diagnostic_external",
    )


def _normalize_answer(value: str) -> str:
    return " ".join(value.casefold().split())


def _audit_trace_binding(
    item: ControllerBenchmarkItem,
    trace: ControllerTrace,
    binding: EvaluationRunBinding,
) -> None:
    validated_binding = EvaluationRunBinding.model_validate(
        binding.model_dump(mode="python")
    )
    if item.split != validated_binding.split:
        raise ValueError("Item split does not match evaluation preregistration.")
    if trace.initial_state.question != item.question:
        raise ValueError(
            "Trace initial question does not match the frozen benchmark item."
        )
    expected_trace_fields = {
        "protocol_id": validated_binding.protocol.protocol_id,
        "protocol_sha256": validated_binding.protocol.protocol_sha256,
        "memory_id": validated_binding.memory_id,
        "memory_sha256": validated_binding.memory_sha256,
        "policy_name": validated_binding.policy_name,
    }
    for field_name, expected in expected_trace_fields.items():
        if getattr(trace, field_name) != expected:
            raise ValueError(
                f"Trace {field_name} does not match evaluation preregistration."
            )
    if trace.created_at < validated_binding.created_at:
        raise ValueError(
            "Trace predates its preregistered evaluation binding."
        )
    _audit_trace_against_memory(trace, validated_binding.memory)


def _audit_formal_evaluation_binding(
    dataset: ControllerBenchmarkDataset,
    binding: EvaluationRunBinding,
) -> None:
    validated = EvaluationRunBinding.model_validate(
        binding.model_dump(mode="python")
    )
    if dataset.lifecycle_status != "sealed":
        raise ValueError(
            "Official evaluation requires a sealed benchmark dataset."
        )
    audit_seal(
        dataset,
        validated.benchmark_review,
        validated.graph_independence_manifest,
        validated.split_manifest,
        validated.benchmark_seal,
    )
    audit_runtime_memory_binding(
        dataset,
        validated.graph_independence_manifest,
        validated.memory,
    )
    if validated.benchmark_sha256 != dataset.dataset_sha256:
        raise ValueError(
            "Evaluation binding targets a different benchmark dataset."
        )


def _validate_benchmark_trace_origin(trace: ControllerTrace) -> None:
    """Require the fresh-state runner shape before trusting trace-derived ids."""

    initial = trace.initial_state
    if trace.question_id != initial.question_id:
        raise ValueError("Trace and initial-state question ids do not match.")
    if trace.question_id != trace.final_state.question_id:
        raise ValueError("Trace and final-state question ids do not match.")
    if initial.step_index != 0:
        raise ValueError("Benchmark traces must begin at controller step zero.")
    if any(
        (
            initial.retrieved_concept_ids,
            initial.retrieved_evidence_ids,
            initial.verified_evidence_ids,
            initial.traversed_relation_ids,
            initial.attempted_action_fingerprints,
        )
    ):
        raise ValueError(
            "Benchmark traces must begin from a fresh state without preloaded "
            "retrieval ids."
        )
    for field_name in (
        "steps",
        "retrieval_calls",
        "concept_searches",
        "evidence_searches",
        "graph_expansions",
        "verifications",
        "unique_concepts",
        "unique_evidence",
    ):
        if getattr(initial.cost, field_name):
            raise ValueError(
                "Benchmark trace initialization cannot pre-report controller "
                f"cost: {field_name}."
            )


def _audit_trace_against_memory(
    trace: ControllerTrace,
    memory: ControllerMemorySnapshot,
) -> None:
    """Reject trace ids or hit metadata not present in the frozen memory."""

    validated_memory = ControllerMemorySnapshot.model_validate(
        memory.model_dump(mode="python")
    )
    if (
        validated_memory.memory_sha256
        != controller_memory_payload_sha256(validated_memory)
    ):
        raise ValueError("Cannot audit a trace against non-canonical memory.")

    concepts = {
        concept.concept_id: concept
        for concept in validated_memory.concepts
    }
    evidence = {
        item.evidence_id: item for item in validated_memory.evidence
    }
    relations = {
        relation.relation_id: relation
        for relation in validated_memory.relations
    }
    known_all_ids = set(concepts) | set(evidence) | set(relations)

    states = [trace.initial_state, trace.final_state]
    for step in trace.steps:
        states.extend((step.state_before, step.state_after))
    for state in states:
        _audit_state_memory_ids(
            state,
            concept_ids=set(concepts),
            evidence_ids=set(evidence),
            relation_ids=set(relations),
        )
    _require_known_memory_ids(
        trace.citation_evidence_ids,
        evidence,
        "trace citation evidence",
    )

    for step in trace.steps:
        action = step.action
        observation = step.observation
        _require_known_memory_ids(
            observation.novel_concept_ids,
            concepts,
            "observation novel concept",
        )
        _require_known_memory_ids(
            observation.novel_evidence_ids,
            evidence,
            "observation novel evidence",
        )
        _require_known_memory_ids(
            observation.novel_relation_ids,
            relations,
            "observation novel relation",
        )
        _require_known_memory_ids(
            observation.duplicate_ids,
            known_all_ids,
            "observation duplicate",
        )

        if action.action_type == "search_concept":
            _require_known_memory_ids(
                action.exclude_concept_ids,
                concepts,
                "concept-search exclusion",
            )
            expected_ranks = list(range(1, len(observation.hits) + 1))
            if [hit.rank for hit in observation.hits] != expected_ranks:
                raise ValueError("Concept-hit ranks are not canonical.")
            for hit in observation.hits:
                _require_known_memory_ids(
                    [hit.concept_id],
                    concepts,
                    "concept hit",
                )
                if hit.concept_id in action.exclude_concept_ids:
                    raise ValueError("Concept hit violates its action exclusion.")
                if hit.retrieval_source != "dense_card_proxy":
                    raise ValueError(
                        "Concept hit has a non-runtime retrieval source."
                    )
        elif action.action_type == "search_evidence":
            _require_known_memory_ids(
                action.scope_concept_ids,
                concepts,
                "evidence-search scope",
            )
            _require_known_memory_ids(
                action.exclude_evidence_ids,
                evidence,
                "evidence-search exclusion",
            )
            expected_ranks = list(range(1, len(observation.hits) + 1))
            if [hit.rank for hit in observation.hits] != expected_ranks:
                raise ValueError("Evidence-hit ranks are not canonical.")
            for hit in observation.hits:
                memory_hit = evidence.get(hit.evidence_id)
                if memory_hit is None:
                    raise ValueError(
                        f"Unknown evidence hit id in frozen memory: "
                        f"{hit.evidence_id}."
                    )
                if (
                    hit.concept_id != memory_hit.concept_id
                    or hit.claim_id != memory_hit.claim_id
                ):
                    raise ValueError(
                        "Evidence-hit metadata conflicts with frozen memory: "
                        f"{hit.evidence_id}."
                    )
                if hit.evidence_id in action.exclude_evidence_ids:
                    raise ValueError("Evidence hit violates its action exclusion.")
                if (
                    action.scope_concept_ids
                    and hit.concept_id not in action.scope_concept_ids
                ):
                    raise ValueError("Evidence hit falls outside its action scope.")
                if hit.retrieval_source != "bm25_evidence":
                    raise ValueError(
                        "Evidence hit has a non-runtime retrieval source."
                    )
        elif action.action_type == "expand_typed_neighbor":
            _require_known_memory_ids(
                action.anchor_concept_ids,
                concepts,
                "graph-expansion anchor",
            )
            _require_known_memory_ids(
                action.exclude_relation_ids,
                relations,
                "graph-expansion exclusion",
            )
            expected_ranks = list(range(1, len(observation.hits) + 1))
            if [hit.rank for hit in observation.hits] != expected_ranks:
                raise ValueError("Relation-hit ranks are not canonical.")
            for hit in observation.hits:
                memory_hit = relations.get(hit.relation_id)
                if memory_hit is None:
                    raise ValueError(
                        f"Unknown relation hit id in frozen memory: "
                        f"{hit.relation_id}."
                    )
                expected_metadata = (
                    memory_hit.source_concept_id,
                    memory_hit.target_concept_id,
                    memory_hit.relation_type,
                    memory_hit.score,
                )
                observed_metadata = (
                    hit.source_concept_id,
                    hit.target_concept_id,
                    hit.relation_type,
                    hit.score,
                )
                if observed_metadata != expected_metadata:
                    raise ValueError(
                        "Relation-hit metadata conflicts with frozen memory: "
                        f"{hit.relation_id}."
                    )
                if hit.relation_id in action.exclude_relation_ids:
                    raise ValueError("Relation hit violates its action exclusion.")
                if hit.relation_type not in action.relation_types:
                    raise ValueError(
                        "Relation hit violates requested relation types."
                    )
                if hit.traversal_direction == "outgoing":
                    direction_allowed = action.direction in {"outgoing", "both"}
                    anchored = (
                        hit.source_concept_id in action.anchor_concept_ids
                    )
                else:
                    direction_allowed = action.direction in {"incoming", "both"}
                    anchored = (
                        hit.target_concept_id in action.anchor_concept_ids
                    )
                if not direction_allowed or not anchored:
                    raise ValueError(
                        "Relation hit violates graph traversal direction or "
                        "anchor."
                    )
        elif action.action_type == "verify_support":
            _require_known_memory_ids(
                action.evidence_ids,
                evidence,
                "verification evidence",
            )
            for result in observation.results:
                _require_known_memory_ids(
                    result.support_concept_ids,
                    concepts,
                    "verification support concept",
                )
                _require_known_memory_ids(
                    result.support_evidence_ids,
                    evidence,
                    "verification support evidence",
                )
                if not set(result.support_evidence_ids).issubset(
                    action.evidence_ids
                ):
                    raise ValueError(
                        "Verification result cites evidence outside its action."
                    )


def _audit_state_memory_ids(
    state: ControllerState,
    *,
    concept_ids: set[str],
    evidence_ids: set[str],
    relation_ids: set[str],
) -> None:
    _require_known_memory_ids(
        state.retrieved_concept_ids,
        concept_ids,
        "state retrieved concept",
    )
    _require_known_memory_ids(
        state.retrieved_evidence_ids,
        evidence_ids,
        "state retrieved evidence",
    )
    _require_known_memory_ids(
        state.verified_evidence_ids,
        evidence_ids,
        "state verified evidence",
    )
    _require_known_memory_ids(
        state.traversed_relation_ids,
        relation_ids,
        "state traversed relation",
    )
    for need in state.knowledge_needs:
        _require_known_memory_ids(
            need.support_concept_ids,
            concept_ids,
            "knowledge-need support concept",
        )
        _require_known_memory_ids(
            need.support_evidence_ids,
            evidence_ids,
            "knowledge-need support evidence",
        )


def _require_known_memory_ids(
    values: list[str],
    known: object,
    label: str,
) -> None:
    unknown = sorted(set(values).difference(known))
    if unknown:
        raise ValueError(f"Unknown {label} ids in frozen memory: {unknown}.")


def _edge_signature(edge: object) -> tuple[str, str, str]:
    return (
        str(getattr(edge, "source_concept_id")),
        str(getattr(edge, "target_concept_id")),
        str(getattr(edge, "relation_type")),
    )


def _fraction(numerator: int, denominator: int) -> MetricFraction:
    return MetricFraction(
        numerator=numerator,
        denominator=denominator,
        value=None if denominator == 0 else numerator / denominator,
    )


def _sum_fractions(values) -> MetricFraction:
    numerator = 0
    denominator = 0
    for value in values:
        numerator += value.numerator
        denominator += value.denominator
    return _fraction(numerator, denominator)
