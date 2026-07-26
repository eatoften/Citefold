from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from rag_lab.controller_benchmark import (
    AnswerCorrectnessAssessment,
    AuthoringProvenance,
    ControllerBenchmarkAuditError,
    ControllerBenchmarkDataset,
    ControllerBenchmarkItem,
    ControllerBenchmarkPrediction,
    ControllerBenchmarkReview,
    ControllerBenchmarkSeal,
    ControllerBenchmarkSplitManifest,
    ConceptRegistryEntry,
    EvaluationRunBinding,
    EvidenceReference,
    GraphIndependenceManifest,
    ItemReviewDecision,
    RequiredConcept,
    ReviewChecks,
    audit_dataset,
    audit_double_review,
    audit_graph_independence,
    audit_runtime_memory_binding,
    audit_seal,
    audit_split_manifest,
    concept_registry_payload_sha256,
    current_evaluator_code_sha256,
    dataset_payload_sha256,
    derive_trace_evaluation_inputs,
    evidence_catalog_payload_sha256,
    evaluation_run_binding_payload_sha256,
    evaluate_controller_predictions,
    evaluate_item_prediction,
    independence_payload_sha256,
    minimum_evidence_units,
    review_payload_sha256,
    runtime_graph_payload_sha256,
    seal_payload_sha256,
    split_manifest_payload_sha256,
)
from rag_lab.controller_schemas import (
    AbstainAction,
    AbstainObservation,
    AnswerAction,
    AnswerObservation,
    ConceptSearchObservation,
    ControllerConceptHit,
    ControllerConceptNode,
    ControllerCost,
    ControllerEvidenceNode,
    ControllerEvidenceHit,
    ControllerKnowledgeNeed,
    ControllerProtocol,
    ControllerMemorySnapshot,
    ControllerRelationEdge,
    ControllerRelationHit,
    ControllerState,
    ControllerStep,
    ControllerTrace,
    ControllerVerificationResult,
    EvidenceSearchObservation,
    ExpandTypedNeighborAction,
    GraphExpansionObservation,
    SearchConceptAction,
    SearchEvidenceAction,
    VerificationObservation,
    VerifySupportAction,
    action_fingerprint,
    controller_state_sha256,
    controller_memory_payload_sha256,
    controller_protocol_payload_sha256,
    controller_trace_payload_sha256,
    reduce_controller_state,
)
from rag_lab.io import sha256_file


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64
HASH_G = "1" * 64
HASH_H = "2" * 64

T0 = datetime(2025, 12, 31, tzinfo=UTC)
T1 = datetime(2026, 1, 1, tzinfo=UTC)
T2 = datetime(2026, 1, 2, tzinfo=UTC)
T3 = datetime(2026, 1, 3, tzinfo=UTC)
T4 = datetime(2026, 1, 4, tzinfo=UTC)
T5 = datetime(2026, 1, 5, tzinfo=UTC)
T6 = datetime(2026, 1, 6, tzinfo=UTC)
T7 = datetime(2026, 1, 7, tzinfo=UTC)


def _evidence(
    evidence_id: str,
    *,
    modality: str = "card_text",
) -> dict[str, str]:
    return {
        "evidence_id": evidence_id,
        "card_id": f"card-{evidence_id}",
        "claim_id": f"claim-{evidence_id}",
        "modality": modality,
    }


def _provenance() -> dict[str, object]:
    return {
        "author_ids": ["author-1"],
        "source_artifact_sha256s": [HASH_A],
        "selection_bases": [
            "learning_objective",
            "independent_evidence_bundle",
        ],
        "blind_to_runtime_graph": True,
        "blind_to_retriever_outputs": True,
        "used_system_outcomes_for_selection": False,
    }


def _trace_for_item(
    item: ControllerBenchmarkItem,
    *,
    run_binding: EvaluationRunBinding,
    final_action: str | None = "answer",
    status: str = "completed",
    retrieved_concept_ids: list[str] | None = None,
    retrieved_evidence_ids: list[str] | None = None,
    path_edges: list[tuple[str, str, str]] | None = None,
    answer_text: str | None = None,
    citation_evidence_ids: list[str] | None = None,
    retrieval_calls: int | None = None,
    prompt_characters: int = 100,
    completion_tokens: int | None = 20,
    latency_milliseconds: float = 10.0,
    bind_hits_to_memory: bool = True,
) -> ControllerTrace:
    """Build a runner-shaped canonical trace for evaluator counterexamples."""

    concept_ids = list(dict.fromkeys(retrieved_concept_ids or []))
    evidence_ids = list(dict.fromkeys(retrieved_evidence_ids or []))
    edges = path_edges or []
    retrieval_action_count = (
        int(bool(concept_ids)) + int(bool(evidence_ids)) + len(edges)
    )
    target_retrieval_calls = (
        retrieval_action_count
        if retrieval_calls is None
        else retrieval_calls
    )
    if target_retrieval_calls < retrieval_action_count:
        raise ValueError("Trace fixture cannot under-report retrieval actions.")
    extra_retrieval_calls = target_retrieval_calls - retrieval_action_count
    first_retrieval = True

    need = ControllerKnowledgeNeed(
        need_id="need-1",
        description=item.question,
    )
    initial_state = ControllerState(
        question_id=item.question_id,
        question=item.question,
        knowledge_needs=[need],
        cost=ControllerCost(
            prompt_characters=prompt_characters,
            completion_tokens=completion_tokens,
            elapsed_milliseconds=latency_milliseconds,
        ),
    )
    state = initial_state
    steps: list[ControllerStep] = []

    def retrieval_cost(
        *,
        concept_searches: int = 0,
        evidence_searches: int = 0,
        graph_expansions: int = 0,
        unique_concepts: int = 0,
        unique_evidence: int = 0,
    ) -> ControllerCost:
        nonlocal first_retrieval
        calls = 1
        if first_retrieval:
            calls += extra_retrieval_calls
            first_retrieval = False
        return ControllerCost(
            steps=1,
            retrieval_calls=calls,
            concept_searches=concept_searches,
            evidence_searches=evidence_searches,
            graph_expansions=graph_expansions,
            unique_concepts=unique_concepts,
            unique_evidence=unique_evidence,
        )

    def append_step(action, observation) -> None:
        nonlocal state
        next_state = reduce_controller_state(state, action, observation)
        step = ControllerStep(
            step_index=state.step_index,
            state_before_sha256=controller_state_sha256(state),
            state_before=state,
            action=action,
            observation=observation,
            state_after=next_state,
        )
        steps.append(step)
        state = next_state

    if concept_ids:
        action = SearchConceptAction(
            need_id=need.need_id,
            query=item.question,
            top_k=len(concept_ids),
        )
        append_step(
            action,
            ConceptSearchObservation(
                action_fingerprint=action_fingerprint(action),
                hits=[
                    ControllerConceptHit(
                        concept_id=concept_id,
                        score=1.0,
                        rank=rank,
                        retrieval_source="dense_card_proxy",
                    )
                    for rank, concept_id in enumerate(concept_ids, start=1)
                ],
                novel_concept_ids=concept_ids,
                cost=retrieval_cost(
                    concept_searches=1,
                    unique_concepts=len(concept_ids),
                ),
            ),
        )

    for edge_index, (source_id, target_id, relation_type) in enumerate(edges):
        if source_id not in state.retrieved_concept_ids:
            raise ValueError(
                f"Graph fixture anchor was not retrieved first: {source_id}."
            )
        action = ExpandTypedNeighborAction(
            need_id=need.need_id,
            anchor_concept_ids=[source_id],
            relation_types=[relation_type],
            direction="outgoing",
            max_neighbors_per_anchor=1,
        )
        matching_relations = [
            relation
            for relation in run_binding.memory.relations
            if (
                relation.source_concept_id,
                relation.target_concept_id,
                relation.relation_type,
            )
            == (source_id, target_id, relation_type)
        ]
        if not matching_relations and bind_hits_to_memory:
            raise ValueError(
                "Graph fixture edge is absent from frozen memory: "
                f"{source_id}->{target_id}:{relation_type}."
            )
        relation = (
            sorted(
                matching_relations,
                key=lambda value: value.relation_id,
            )[0]
            if matching_relations
            else None
        )
        relation_id = (
            relation.relation_id
            if relation is not None
            else f"invented-relation-{edge_index}"
        )
        target_is_novel = target_id not in state.retrieved_concept_ids
        append_step(
            action,
            GraphExpansionObservation(
                action_fingerprint=action_fingerprint(action),
                hits=[
                    ControllerRelationHit(
                        relation_id=relation_id,
                        source_concept_id=source_id,
                        target_concept_id=target_id,
                        relation_type=relation_type,
                        traversal_direction="outgoing",
                        score=relation.score if relation is not None else 1.0,
                        rank=1,
                    )
                ],
                novel_concept_ids=[target_id] if target_is_novel else [],
                novel_relation_ids=[relation_id],
                duplicate_ids=[] if target_is_novel else [target_id],
                cost=retrieval_cost(
                    graph_expansions=1,
                    unique_concepts=int(target_is_novel),
                ),
            ),
        )

    if evidence_ids:
        action = SearchEvidenceAction(
            need_ids=[need.need_id],
            query=item.question,
            top_k=len(evidence_ids),
        )
        memory_evidence = {
            entry.evidence_id: entry
            for entry in run_binding.memory.evidence
        }
        unknown_evidence = sorted(set(evidence_ids) - set(memory_evidence))
        if unknown_evidence and bind_hits_to_memory:
            raise ValueError(
                "Evidence fixture ids are absent from frozen memory: "
                f"{unknown_evidence}."
            )
        append_step(
            action,
            EvidenceSearchObservation(
                action_fingerprint=action_fingerprint(action),
                hits=[
                    ControllerEvidenceHit(
                        evidence_id=evidence_id,
                        concept_id=(
                            memory_evidence[evidence_id].concept_id
                            if evidence_id in memory_evidence
                            else (
                                state.retrieved_concept_ids[0]
                                if state.retrieved_concept_ids
                                else "invented-concept"
                            )
                        ),
                        claim_id=(
                            memory_evidence[evidence_id].claim_id
                            if evidence_id in memory_evidence
                            else f"claim-{evidence_id}"
                        ),
                        score=1.0,
                        rank=rank,
                        retrieval_source="bm25_evidence",
                    )
                    for rank, evidence_id in enumerate(evidence_ids, start=1)
                ],
                novel_evidence_ids=evidence_ids,
                cost=retrieval_cost(
                    evidence_searches=1,
                    unique_evidence=len(evidence_ids),
                ),
            ),
        )

    if status == "completed" and final_action == "answer":
        if not evidence_ids:
            raise ValueError("Answer traces require retrieved evidence.")
        action = VerifySupportAction(
            need_ids=[need.need_id],
            evidence_ids=evidence_ids,
        )
        append_step(
            action,
            VerificationObservation(
                action_fingerprint=action_fingerprint(action),
                results=[
                    ControllerVerificationResult(
                        need_id=need.need_id,
                        status="supported",
                        support_concept_ids=state.retrieved_concept_ids,
                        support_evidence_ids=evidence_ids,
                        confidence=1.0,
                    )
                ],
                cost=ControllerCost(steps=1, verifications=1),
            ),
        )
        answer_action = AnswerAction(supported_need_ids=[need.need_id])
        append_step(
            answer_action,
            AnswerObservation(
                action_fingerprint=action_fingerprint(answer_action),
                cost=ControllerCost(steps=1),
            ),
        )
        stop_reason = "answer"
        resolved_answer = (
            answer_text
            if answer_text is not None
            else item.reference_answers[0]
        )
        resolved_citations = (
            citation_evidence_ids
            if citation_evidence_ids is not None
            else [evidence_ids[0]]
        )
        error_type = None
        error_message = None
    elif status == "completed" and final_action == "abstain":
        abstain_action = AbstainAction(reason_code="insufficient_evidence")
        append_step(
            abstain_action,
            AbstainObservation(
                action_fingerprint=action_fingerprint(abstain_action),
                reason_code=abstain_action.reason_code,
                cost=ControllerCost(steps=1),
            ),
        )
        stop_reason = "abstain"
        resolved_answer = None
        resolved_citations = []
        error_type = None
        error_message = None
    elif status == "failed":
        stop_reason = "environment_error"
        resolved_answer = None
        resolved_citations = []
        error_type = "RuntimeError"
        error_message = "controller crashed"
    else:
        stop_reason = "max_steps"
        resolved_answer = None
        resolved_citations = []
        error_type = None
        error_message = None

    payload = {
        "trace_id": f"trace-{item.question_id}-{status}-{final_action}",
        "protocol_id": run_binding.protocol.protocol_id,
        "protocol_sha256": run_binding.protocol.protocol_sha256,
        "memory_id": run_binding.memory_id,
        "memory_sha256": run_binding.memory_sha256,
        "question_id": item.question_id,
        "policy_name": run_binding.policy_name,
        "initial_state": initial_state,
        "steps": steps,
        "final_state": state,
        "stop_reason": stop_reason,
        "status": status,
        "final_answer": resolved_answer,
        "citation_evidence_ids": resolved_citations,
        "error_type": error_type,
        "error_message": error_message,
        "created_at": T6,
        "completed_at": T7,
    }
    provisional = ControllerTrace.model_construct(
        **payload,
        trace_sha256="0" * 64,
    )
    return ControllerTrace(
        **payload,
        trace_sha256=controller_trace_payload_sha256(provisional),
    )


def _multi_hop_payload(
    *,
    question_id: str = "multi-hop-001",
    split: str = "development",
) -> dict[str, object]:
    return {
        "question_id": question_id,
        "question_family_id": f"family-{question_id}",
        "learning_objective_cluster_id": f"objective-{question_id}",
        "source_evidence_bundle_id": f"bundle-{question_id}",
        "split": split,
        "task_type": "multi_hop",
        "question": "How does the anchor lead through the bridge to the target?",
        "answerability": "answerable",
        "reference_answers": ["The anchor enables the bridge, which derives the target."],
        "required_concepts": [
            {
                "concept_id": "anchor",
                "role": "anchor",
                "mention_status": "explicit",
                "necessity": "answer",
            },
            {
                "concept_id": "bridge",
                "role": "bridge",
                "mention_status": "implicit",
                "necessity": "reasoning_only",
            },
            {
                "concept_id": "target",
                "role": "target",
                "mention_status": "explicit",
                "necessity": "answer",
            },
        ],
        "evidence_requirements": [
            {
                "requirement_id": "req-anchor",
                "supports_concept_ids": ["anchor"],
                "alternatives": [{"evidence": [_evidence("e-anchor")]}],
            },
            {
                "requirement_id": "req-bridge",
                "supports_concept_ids": ["bridge"],
                "alternatives": [
                    {"evidence": [_evidence("e-bridge-direct")]},
                    {
                        "evidence": [
                            _evidence("e-bridge-part-1"),
                            _evidence("e-bridge-part-2"),
                        ]
                    },
                ],
            },
            {
                "requirement_id": "req-target",
                "supports_concept_ids": ["target"],
                "alternatives": [{"evidence": [_evidence("e-target")]}],
            },
        ],
        "valid_reasoning_paths": [
            {
                "path_id": "path-primary",
                "concept_ids": ["anchor", "bridge", "target"],
                "edges": [
                    {
                        "source_concept_id": "anchor",
                        "target_concept_id": "bridge",
                        "relation_type": "part_of",
                        "supporting_requirement_ids": [
                            "req-anchor",
                            "req-bridge",
                        ],
                    },
                    {
                        "source_concept_id": "bridge",
                        "target_concept_id": "target",
                        "relation_type": "example_of",
                        "supporting_requirement_ids": [
                            "req-bridge",
                            "req-target",
                        ],
                    },
                ],
                "covers_requirement_ids": [
                    "req-anchor",
                    "req-bridge",
                    "req-target",
                ],
            }
        ],
        "modality_requirement": {
            "mode": "text_only",
            "required_modalities": ["card_text"],
        },
        "difficulty": {
            "level": "hard",
            "relation_hops": 2,
            "distinct_evidence_units": 3,
            "implicit_concept_count": 1,
            "cross_lecture": False,
            "hard_negative_count": 0,
            "lexical_overlap_bucket": "low",
        },
        "authoring_provenance": _provenance(),
        "review_status": "reviewed",
    }


def _prerequisite_payload() -> dict[str, object]:
    return {
        "question_id": "prerequisite-001",
        "question_family_id": "family-prerequisite-001",
        "learning_objective_cluster_id": "objective-prerequisite-001",
        "source_evidence_bundle_id": "bundle-prerequisite-001",
        "split": "development",
        "task_type": "prerequisite",
        "question": "What prior idea is needed to understand the target operation?",
        "answerability": "answerable",
        "reference_answers": ["The prerequisite idea must be understood first."],
        "required_concepts": [
            {
                "concept_id": "prior",
                "role": "prerequisite",
                "mention_status": "implicit",
                "necessity": "reasoning_only",
            },
            {
                "concept_id": "target-operation",
                "role": "target",
                "mention_status": "explicit",
                "necessity": "answer",
            },
            {
                "concept_id": "question-anchor",
                "role": "anchor",
                "mention_status": "explicit",
                "necessity": "answer",
            },
        ],
        "evidence_requirements": [
            {
                "requirement_id": "req-prior",
                "supports_concept_ids": ["prior"],
                "alternatives": [{"evidence": [_evidence("e-prior")]}],
            },
            {
                "requirement_id": "req-target-operation",
                "supports_concept_ids": ["target-operation"],
                "alternatives": [{"evidence": [_evidence("e-target-operation")]}],
            },
            {
                "requirement_id": "req-question-anchor",
                "supports_concept_ids": ["question-anchor"],
                "alternatives": [{"evidence": [_evidence("e-question-anchor")]}],
            },
        ],
        "valid_reasoning_paths": [
            {
                "path_id": "path-prerequisite",
                "concept_ids": ["question-anchor", "prior", "target-operation"],
                "edges": [
                    {
                        "source_concept_id": "question-anchor",
                        "target_concept_id": "prior",
                        "relation_type": "depends_on",
                        "supporting_requirement_ids": [
                            "req-prior",
                            "req-question-anchor",
                        ],
                    },
                    {
                        "source_concept_id": "prior",
                        "target_concept_id": "target-operation",
                        "relation_type": "prerequisite",
                        "supporting_requirement_ids": [
                            "req-question-anchor",
                            "req-target-operation",
                        ],
                    },
                ],
                "covers_requirement_ids": [
                    "req-prior",
                    "req-target-operation",
                    "req-question-anchor",
                ],
            }
        ],
        "modality_requirement": {
            "mode": "text_only",
            "required_modalities": ["card_text"],
        },
        "difficulty": {
            "level": "medium",
            "relation_hops": 2,
            "distinct_evidence_units": 3,
            "implicit_concept_count": 1,
            "cross_lecture": False,
            "hard_negative_count": 0,
            "lexical_overlap_bucket": "medium",
        },
        "authoring_provenance": _provenance(),
        "review_status": "reviewed",
    }


def _unanswerable_payload(
    *,
    question_id: str = "unanswerable-001",
    split: str = "test",
) -> dict[str, object]:
    return {
        "question_id": question_id,
        "question_family_id": f"family-{question_id}",
        "learning_objective_cluster_id": f"objective-{question_id}",
        "source_evidence_bundle_id": f"bundle-{question_id}",
        "split": split,
        "task_type": "unanswerable",
        "question": "Which missing bridge determines the unsupported conclusion?",
        "answerability": "unanswerable",
        "reference_answers": [],
        "required_concepts": [],
        "evidence_requirements": [],
        "valid_reasoning_paths": [],
        "modality_requirement": {
            "mode": "text_only",
            "required_modalities": ["card_text"],
        },
        "difficulty": {
            "level": "medium",
            "relation_hops": 0,
            "distinct_evidence_units": 0,
            "implicit_concept_count": 0,
            "cross_lecture": False,
            "hard_negative_count": 1,
            "lexical_overlap_bucket": "high",
        },
        "hard_negatives": [_evidence("e-near-miss")],
        "unanswerable_certificate": {
            "subtype": "missing_bridge",
            "unresolved_information_need": "No evidence establishes the bridge.",
            "closest_supported_concept_ids": ["nearby-concept"],
            "partial_evidence": [_evidence("e-near-miss")],
            "negative_search_audit": {
                "keyword_queries": ["missing bridge", "unsupported conclusion"],
                "bm25_top_n": 20,
                "dense_top_n": 20,
                "manually_reviewed_evidence_ids": ["e-near-miss"],
                "conclusion": "no_complete_support_found",
            },
        },
        "authoring_provenance": _provenance(),
        "review_status": "reviewed",
    }


def _split_manifest_for_items(
    items: list[ControllerBenchmarkItem],
) -> ControllerBenchmarkSplitManifest:
    manifest = ControllerBenchmarkSplitManifest(
        manifest_id="split-v1",
        benchmark_id="controller-v2-fixture",
        created_at=T0,
        assignments=[
            {
                "question_id": item.question_id,
                "split": item.split,
                "question_family_id": item.question_family_id,
                "learning_objective_cluster_id": (
                    item.learning_objective_cluster_id
                ),
                "source_evidence_bundle_id": item.source_evidence_bundle_id,
            }
            for item in items
        ],
        manifest_sha256="0" * 64,
    )
    return manifest.model_copy(
        update={"manifest_sha256": split_manifest_payload_sha256(manifest)}
    )


def _split_manifest(
    dataset: ControllerBenchmarkDataset,
) -> ControllerBenchmarkSplitManifest:
    return _split_manifest_for_items(dataset.items)


def _dataset_for_items(
    items: list[ControllerBenchmarkItem],
    *,
    lifecycle_status: str = "adjudicated",
) -> ControllerBenchmarkDataset:
    split_manifest = _split_manifest_for_items(items)
    evidence_by_id: dict[str, EvidenceReference] = {}
    for item in items:
        references = [
            reference
            for requirement in item.evidence_requirements
            for alternative in requirement.alternatives
            for reference in alternative.evidence
        ]
        references.extend(item.hard_negatives)
        if item.unanswerable_certificate is not None:
            references.extend(item.unanswerable_certificate.partial_evidence)
        for reference in references:
            evidence_by_id.setdefault(reference.evidence_id, reference)
        if item.unanswerable_certificate is not None:
            for evidence_id in (
                item.unanswerable_certificate.negative_search_audit
                .manually_reviewed_evidence_ids
            ):
                evidence_by_id.setdefault(
                    evidence_id,
                    EvidenceReference.model_validate(_evidence(evidence_id)),
                )
    evidence_catalog = [
        evidence_by_id[evidence_id] for evidence_id in sorted(evidence_by_id)
    ]
    concept_ids = {
        concept.concept_id
        for item in items
        for concept in item.required_concepts
    }
    concept_ids.update(
        concept_id
        for item in items
        if item.unanswerable_certificate is not None
        for concept_id in (
            item.unanswerable_certificate.closest_supported_concept_ids
        )
    )
    concept_registry = [
        ConceptRegistryEntry(
            concept_id=concept_id,
            canonical_name=concept_id.replace("-", " ").title(),
            definition=f"Frozen benchmark definition for {concept_id}.",
        )
        for concept_id in sorted(concept_ids)
    ]
    dataset = ControllerBenchmarkDataset(
        benchmark_id="controller-v2-fixture",
        corpus_sha256=HASH_B,
        concept_registry_sha256=concept_registry_payload_sha256(
            concept_registry
        ),
        evidence_catalog_sha256=evidence_catalog_payload_sha256(
            evidence_catalog
        ),
        annotation_protocol_sha256=HASH_E,
        split_manifest_sha256=split_manifest.manifest_sha256,
        created_at=T3,
        lifecycle_status=lifecycle_status,
        dataset_sha256="0" * 64,
        concept_registry=concept_registry,
        evidence_catalog=evidence_catalog,
        items=items,
    )
    return dataset.model_copy(
        update={"dataset_sha256": dataset_payload_sha256(dataset)}
    )


def _dataset(*, lifecycle_status: str = "adjudicated") -> ControllerBenchmarkDataset:
    return _dataset_for_items(
        [
            ControllerBenchmarkItem.model_validate(_multi_hop_payload()),
            ControllerBenchmarkItem.model_validate(_unanswerable_payload()),
        ],
        lifecycle_status=lifecycle_status,
    )


def _memory_for_dataset(
    dataset: ControllerBenchmarkDataset,
) -> ControllerMemorySnapshot:
    evidence_owner: dict[str, str] = {}
    for item in dataset.items:
        for requirement in item.evidence_requirements:
            owner = requirement.supports_concept_ids[0]
            for alternative in requirement.alternatives:
                for reference in alternative.evidence:
                    evidence_owner.setdefault(reference.evidence_id, owner)
        certificate_owner = (
            item.unanswerable_certificate.closest_supported_concept_ids[0]
            if item.unanswerable_certificate is not None
            and item.unanswerable_certificate.closest_supported_concept_ids
            else None
        )
        if certificate_owner is not None:
            for reference in item.hard_negatives:
                evidence_owner.setdefault(reference.evidence_id, certificate_owner)
            for reference in item.unanswerable_certificate.partial_evidence:
                evidence_owner.setdefault(reference.evidence_id, certificate_owner)
            for evidence_id in (
                item.unanswerable_certificate.negative_search_audit
                .manually_reviewed_evidence_ids
            ):
                evidence_owner.setdefault(evidence_id, certificate_owner)

    fallback_concept_id = dataset.concept_registry[0].concept_id
    source_cards: dict[str, set[str]] = {
        entry.concept_id: set() for entry in dataset.concept_registry
    }
    evidence_nodes: list[ControllerEvidenceNode] = []
    for entry in dataset.evidence_catalog:
        concept_id = evidence_owner.get(entry.evidence_id, fallback_concept_id)
        source_cards[concept_id].add(entry.card_id)
        evidence_nodes.append(
            ControllerEvidenceNode(
                evidence_id=entry.evidence_id,
                concept_id=concept_id,
                claim_id=entry.claim_id,
                claim_text=f"Frozen claim for {entry.evidence_id}.",
                text=f"Frozen evidence text for {entry.evidence_id}.",
                modality={
                    "card_text": "document",
                    "transcript": "transcript",
                    "slide_text": "slide_text",
                    "frame": "video_frame",
                    "diagram": "slide_image",
                }[entry.modality],
                source_job_id="benchmark-fixture-job",
                source_name="benchmark-fixture-source",
                locator={"card_id": entry.card_id},
                extraction_method="benchmark_fixture",
                extraction_version="1.0",
                confidence=1.0,
            )
        )

    concept_nodes = [
        ControllerConceptNode(
            concept_id=entry.concept_id,
            title=entry.canonical_name,
            summary=entry.definition,
            document_text=(
                f"{entry.canonical_name}\n{entry.definition}"
            ),
            source_card_ids=sorted(source_cards[entry.concept_id])
            or [f"card-{entry.concept_id}"],
        )
        for entry in dataset.concept_registry
    ]
    relation_signatures = sorted(
        {
            (
                edge.source_concept_id,
                edge.target_concept_id,
                edge.relation_type,
            )
            for item in dataset.items
            for path in item.valid_reasoning_paths
            for edge in path.edges
        }
    )
    relation_nodes = [
        ControllerRelationEdge(
            relation_id=f"runtime-relation-{index}",
            source_concept_id=source_id,
            target_concept_id=target_id,
            relation_type=relation_type,
            score=1.0,
            review_status="human_verified",
        )
        for index, (source_id, target_id, relation_type) in enumerate(
            relation_signatures
        )
    ]
    provisional = ControllerMemorySnapshot(
        memory_id="benchmark-memory",
        concept_granularity="concept_node",
        corpus_sha256=dataset.corpus_sha256,
        review_sha256=HASH_A,
        created_at=T1,
        concepts=concept_nodes,
        evidence=evidence_nodes,
        relations=relation_nodes,
        memory_sha256="0" * 64,
    )
    return provisional.model_copy(
        update={
            "memory_sha256": controller_memory_payload_sha256(provisional)
        }
    )


def _manifest(
    dataset: ControllerBenchmarkDataset | None = None,
    memory: ControllerMemorySnapshot | None = None,
) -> GraphIndependenceManifest:
    bound_dataset = dataset or _dataset()
    bound_memory = memory or _memory_for_dataset(bound_dataset)
    manifest = GraphIndependenceManifest(
        manifest_id="independence-v1",
        runtime_graph_sha256=runtime_graph_payload_sha256(bound_memory),
        graph_frozen_at=T1,
        benchmark_authoring_started_at=T2,
        created_at=T3,
        graph_reviewer_ids=["graph-reviewer"],
        question_author_ids=["author-1"],
        benchmark_reviewer_ids=["reviewer-1", "reviewer-2"],
        adjudicator_ids=["adjudicator-1"],
        question_inputs=[
            {
                "artifact_sha256": HASH_B,
                "artifact_type": "corpus_snapshot",
                "parent_artifact_sha256s": [],
            },
            {
                "artifact_sha256": bound_dataset.concept_registry_sha256,
                "artifact_type": "concept_registry",
                "parent_artifact_sha256s": [HASH_B],
            },
            {
                "artifact_sha256": bound_dataset.evidence_catalog_sha256,
                "artifact_type": "evidence_catalog",
                "parent_artifact_sha256s": [HASH_B],
            },
            {
                "artifact_sha256": HASH_E,
                "artifact_type": "annotation_protocol",
                "parent_artifact_sha256s": [],
            },
            {
                "artifact_sha256": HASH_A,
                "artifact_type": "independent_evidence_bundle",
                "parent_artifact_sha256s": [
                    bound_dataset.concept_registry_sha256,
                    bound_dataset.evidence_catalog_sha256,
                ],
            },
        ],
        manifest_sha256="0" * 64,
    )
    return manifest.model_copy(
        update={"manifest_sha256": independence_payload_sha256(manifest)}
    )


def _passing_checks() -> ReviewChecks:
    return ReviewChecks(
        question_clarity=True,
        answerability=True,
        required_concepts=True,
        evidence_or_failure_certificate=True,
        reasoning_paths=True,
        modality=True,
        difficulty=True,
        hard_negatives=True,
    )


def _review(dataset: ControllerBenchmarkDataset) -> ControllerBenchmarkReview:
    decisions = [
        ItemReviewDecision(
            question_id=item.question_id,
            reviewer_id=reviewer_id,
            checks=_passing_checks(),
            overall_decision="accept",
            notes="Independently checked every field against the frozen sources.",
        )
        for item in dataset.items
        for reviewer_id in ("reviewer-1", "reviewer-2")
    ]
    review = ControllerBenchmarkReview(
        review_id="review-v1",
        benchmark_sha256=dataset.dataset_sha256,
        created_at=T4,
        review_status="human_verified",
        decisions=decisions,
        review_sha256="0" * 64,
    )
    return review.model_copy(
        update={"review_sha256": review_payload_sha256(review)}
    )


def _seal(
    dataset: ControllerBenchmarkDataset,
    review: ControllerBenchmarkReview,
    manifest: GraphIndependenceManifest,
    split_manifest: ControllerBenchmarkSplitManifest,
) -> ControllerBenchmarkSeal:
    seal = ControllerBenchmarkSeal(
        seal_id="seal-v1",
        benchmark_sha256=dataset.dataset_sha256,
        review_sha256=review.review_sha256,
        independence_manifest_sha256=manifest.manifest_sha256,
        split_manifest_sha256=split_manifest.manifest_sha256,
        sealed_at=T5,
        status="sealed",
        seal_sha256="0" * 64,
    )
    return seal.model_copy(update={"seal_sha256": seal_payload_sha256(seal)})


def _evaluation_binding(
    dataset: ControllerBenchmarkDataset,
    *,
    split: str,
    oracle_only: bool = False,
) -> EvaluationRunBinding:
    review = _review(dataset)
    memory = _memory_for_dataset(dataset)
    manifest = _manifest(dataset, memory)
    split_manifest = _split_manifest(dataset)
    seal = _seal(dataset, review, manifest, split_manifest)
    protocol_payload = {
        "protocol_id": "benchmark-protocol",
        "corpus_sha256": dataset.corpus_sha256,
        "review_sha256": review.review_sha256,
        "memory_sha256": memory.memory_sha256,
        "benchmark_sha256": dataset.dataset_sha256,
        "code_sha256": HASH_H,
        "split": split,
        "policy_name": "fixture-policy",
        "concept_granularity": memory.concept_granularity,
        "oracle_only": oracle_only,
    }
    provisional_protocol = ControllerProtocol.model_construct(
        **protocol_payload,
        protocol_sha256="0" * 64,
    )
    protocol = ControllerProtocol(
        **protocol_payload,
        protocol_sha256=controller_protocol_payload_sha256(
            provisional_protocol
        ),
    )
    binding_payload = {
        "binding_id": f"binding-{dataset.benchmark_id}-{split}",
        "created_at": T5,
        "benchmark_sha256": dataset.dataset_sha256,
        "benchmark_review": review,
        "graph_independence_manifest": manifest,
        "split_manifest": split_manifest,
        "benchmark_seal": seal,
        "split": split,
        "protocol": protocol,
        "memory": memory,
        "memory_id": memory.memory_id,
        "memory_sha256": protocol.memory_sha256,
        "policy_name": protocol.policy_name,
        "controller_code_sha256": protocol.code_sha256,
        "evaluator_code_sha256": current_evaluator_code_sha256(),
    }
    provisional_binding = EvaluationRunBinding.model_construct(
        **binding_payload,
        binding_sha256="0" * 64,
    )
    return EvaluationRunBinding(
        **binding_payload,
        binding_sha256=evaluation_run_binding_payload_sha256(
            provisional_binding
        ),
    )


def test_models_forbid_undeclared_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RequiredConcept(
            concept_id="concept",
            role="target",
            mention_status="explicit",
            necessity="answer",
            undocumented_label=True,
        )


def test_evaluator_code_hash_is_composite_not_metrics_file_only() -> None:
    metrics_path = (
        Path(__file__).parents[1]
        / "rag_lab"
        / "controller_benchmark"
        / "metrics.py"
    )
    assert current_evaluator_code_sha256() != sha256_file(metrics_path)
    assert current_evaluator_code_sha256() == current_evaluator_code_sha256()


def test_reasoning_path_roles_must_run_anchor_through_bridge_to_target() -> None:
    payload = _multi_hop_payload()
    payload["valid_reasoning_paths"][0]["concept_ids"] = [
        "bridge",
        "anchor",
        "target",
    ]
    payload["valid_reasoning_paths"][0]["edges"] = [
        {
            "source_concept_id": "bridge",
            "target_concept_id": "anchor",
            "relation_type": "part_of",
            "supporting_requirement_ids": ["req-anchor", "req-bridge"],
        },
        {
            "source_concept_id": "anchor",
            "target_concept_id": "target",
            "relation_type": "example_of",
            "supporting_requirement_ids": ["req-anchor", "req-target"],
        },
    ]
    with pytest.raises(ValidationError, match="anchor to its target"):
        ControllerBenchmarkItem.model_validate(payload)


def test_valid_multi_hop_contract_supports_dnf_evidence() -> None:
    item = ControllerBenchmarkItem.model_validate(_multi_hop_payload())

    assert len(item.required_concepts) == 3
    assert len(item.valid_reasoning_paths[0].edges) == 2
    assert minimum_evidence_units(item.evidence_requirements) == 3
    assert item.difficulty.level == "hard"


def test_dnf_rejects_duplicate_alternatives_and_path_requires_directional_chain() -> None:
    duplicate_alternative = _multi_hop_payload()
    bridge = duplicate_alternative["evidence_requirements"][1]
    bridge["alternatives"][1] = bridge["alternatives"][0]
    with pytest.raises(ValidationError, match="semantically distinct"):
        ControllerBenchmarkItem.model_validate(duplicate_alternative)

    disconnected = _multi_hop_payload()
    disconnected["valid_reasoning_paths"][0]["edges"][1][
        "source_concept_id"
    ] = "anchor"
    with pytest.raises(ValidationError, match="concept order and direction"):
        ControllerBenchmarkItem.model_validate(disconnected)


def test_true_multi_hop_and_prerequisite_semantics_are_enforced() -> None:
    prerequisite = ControllerBenchmarkItem.model_validate(_prerequisite_payload())
    assert prerequisite.task_type == "prerequisite"

    explicit_prerequisite = _prerequisite_payload()
    explicit_prerequisite["required_concepts"][0]["mention_status"] = "explicit"
    explicit_prerequisite["difficulty"]["implicit_concept_count"] = 0
    with pytest.raises(ValidationError, match="implicit prerequisite"):
        ControllerBenchmarkItem.model_validate(explicit_prerequisite)

    one_hop = _prerequisite_payload()
    one_hop["task_type"] = "multi_hop"
    one_hop["required_concepts"] = [
        one_hop["required_concepts"][2],
        one_hop["required_concepts"][1],
    ]
    one_hop["evidence_requirements"] = [
        one_hop["evidence_requirements"][2],
        one_hop["evidence_requirements"][1],
    ]
    one_hop["valid_reasoning_paths"] = [
        {
            "path_id": "one-hop",
            "concept_ids": ["question-anchor", "target-operation"],
            "edges": [
                {
                    "source_concept_id": "question-anchor",
                    "target_concept_id": "target-operation",
                    "relation_type": "enables",
                    "supporting_requirement_ids": [
                        "req-question-anchor",
                        "req-target-operation",
                    ],
                }
            ],
            "covers_requirement_ids": [
                "req-question-anchor",
                "req-target-operation",
            ],
        }
    ]
    one_hop["difficulty"].update(
        {
            "level": "easy",
            "relation_hops": 1,
            "distinct_evidence_units": 2,
            "implicit_concept_count": 0,
        }
    )
    with pytest.raises(ValidationError, match="at least two directed relation edges"):
        ControllerBenchmarkItem.model_validate(one_hop)


def test_unanswerable_near_miss_requires_certificate_and_hard_negative() -> None:
    valid = ControllerBenchmarkItem.model_validate(_unanswerable_payload())
    assert valid.unanswerable_certificate is not None

    missing_certificate = _unanswerable_payload()
    missing_certificate["unanswerable_certificate"] = None
    with pytest.raises(ValidationError, match="failure certificate"):
        ControllerBenchmarkItem.model_validate(missing_certificate)

    missing_hard_negative = _unanswerable_payload()
    missing_hard_negative["hard_negatives"] = []
    missing_hard_negative["difficulty"]["hard_negative_count"] = 0
    missing_hard_negative["difficulty"]["level"] = "easy"
    with pytest.raises(ValidationError, match="hard negative"):
        ControllerBenchmarkItem.model_validate(missing_hard_negative)


def test_modality_and_difficulty_are_recomputed_from_ground_truth() -> None:
    wrong_modality = _multi_hop_payload()
    wrong_modality["modality_requirement"] = {
        "mode": "visual_only",
        "required_modalities": ["diagram"],
    }
    with pytest.raises(ValidationError, match="absent from gold evidence"):
        ControllerBenchmarkItem.model_validate(wrong_modality)

    wrong_difficulty = _multi_hop_payload()
    wrong_difficulty["difficulty"]["relation_hops"] = 3
    with pytest.raises(ValidationError, match="Difficulty axes"):
        ControllerBenchmarkItem.model_validate(wrong_difficulty)


def test_dataset_hash_is_canonical_and_lifecycle_independent() -> None:
    dataset = _dataset()
    report = audit_dataset(dataset)

    assert report["passed"]
    sealed_copy = dataset.model_copy(update={"lifecycle_status": "sealed"})
    assert dataset_payload_sha256(sealed_copy) == dataset.dataset_sha256

    changed = dataset.model_copy(update={"benchmark_id": "changed"})
    with pytest.raises(ControllerBenchmarkAuditError, match="not canonical"):
        audit_dataset(changed)


def test_dataset_audit_binds_concept_registry_contents_and_references() -> None:
    dataset = _dataset()
    tampered_registry = [
        entry.model_copy(
            update={"definition": "Tampered semantic definition."}
        )
        if entry.concept_id == "anchor"
        else entry
        for entry in dataset.concept_registry
    ]
    tampered = dataset.model_copy(update={"concept_registry": tampered_registry})
    with pytest.raises(
        ControllerBenchmarkAuditError,
        match="Concept-registry hash",
    ):
        audit_dataset(tampered)

    missing_registry = [
        entry
        for entry in dataset.concept_registry
        if entry.concept_id != "anchor"
    ]
    missing_payload = dataset.model_copy(
        update={
            "concept_registry": missing_registry,
            "concept_registry_sha256": concept_registry_payload_sha256(
                missing_registry
            ),
            "dataset_sha256": "0" * 64,
        }
    )
    missing_payload = missing_payload.model_copy(
        update={"dataset_sha256": dataset_payload_sha256(missing_payload)}
    )
    with pytest.raises(
        ControllerBenchmarkAuditError,
        match="required concepts absent",
    ):
        audit_dataset(missing_payload)


def test_dataset_audit_rejects_cross_split_family_leakage() -> None:
    dataset = _dataset()
    leaked_test_item = dataset.items[1].model_copy(
        update={"question_family_id": dataset.items[0].question_family_id}
    )
    leaked = dataset.model_copy(
        update={
            "items": [dataset.items[0], leaked_test_item],
            "dataset_sha256": "0" * 64,
        }
    )
    leaked = leaked.model_copy(
        update={"dataset_sha256": dataset_payload_sha256(leaked)}
    )

    with pytest.raises(ControllerBenchmarkAuditError, match="question family"):
        audit_dataset(leaked)


def test_learning_objective_rewrite_cannot_cross_splits() -> None:
    development_item = ControllerBenchmarkItem.model_validate(_multi_hop_payload())
    rewritten = _multi_hop_payload(
        question_id="multi-hop-rewrite-002",
        split="test",
    )
    rewritten["question"] = (
        "Trace the independently worded second objective through its hidden step."
    )
    rewritten["learning_objective_cluster_id"] = (
        development_item.learning_objective_cluster_id
    )
    concept_map = {
        "anchor": "second-anchor",
        "bridge": "second-bridge",
        "target": "second-target",
    }
    requirement_map = {
        "req-anchor": "second-req-anchor",
        "req-bridge": "second-req-bridge",
        "req-target": "second-req-target",
    }
    for concept in rewritten["required_concepts"]:
        concept["concept_id"] = concept_map[concept["concept_id"]]
    for requirement in rewritten["evidence_requirements"]:
        requirement["requirement_id"] = requirement_map[
            requirement["requirement_id"]
        ]
        requirement["supports_concept_ids"] = [
            concept_map[concept_id]
            for concept_id in requirement["supports_concept_ids"]
        ]
        for alternative in requirement["alternatives"]:
            for reference in alternative["evidence"]:
                reference["evidence_id"] = f"second-{reference['evidence_id']}"
                reference["card_id"] = f"second-{reference['card_id']}"
                reference["claim_id"] = f"second-{reference['claim_id']}"
    for path in rewritten["valid_reasoning_paths"]:
        path["path_id"] = f"second-{path['path_id']}"
        path["concept_ids"] = [
            concept_map[concept_id] for concept_id in path["concept_ids"]
        ]
        path["covers_requirement_ids"] = [
            requirement_map[requirement_id]
            for requirement_id in path["covers_requirement_ids"]
        ]
        for edge in path["edges"]:
            edge["source_concept_id"] = concept_map[edge["source_concept_id"]]
            edge["target_concept_id"] = concept_map[edge["target_concept_id"]]
            edge["supporting_requirement_ids"] = [
                requirement_map[requirement_id]
                for requirement_id in edge["supporting_requirement_ids"]
            ]
    test_item = ControllerBenchmarkItem.model_validate(rewritten)
    dataset = _dataset_for_items([development_item, test_item])
    split_manifest = _split_manifest(dataset)

    with pytest.raises(
        ControllerBenchmarkAuditError,
        match="learning-objective cluster",
    ):
        audit_dataset(dataset)
    with pytest.raises(
        ControllerBenchmarkAuditError,
        match="split-manifest learning-objective cluster",
    ):
        audit_split_manifest(dataset, split_manifest)


def test_dataset_audit_rejects_partial_gold_evidence_overlap_across_splits() -> None:
    development_item = ControllerBenchmarkItem.model_validate(_multi_hop_payload())
    test_payload = _multi_hop_payload(
        question_id="multi-hop-002",
        split="test",
    )
    test_payload["question"] = (
        "How does a second phrasing connect its anchor, bridge, and target?"
    )
    test_payload["evidence_requirements"][2]["alternatives"][0]["evidence"][0][
        "evidence_id"
    ] = "e-target-second"
    test_item = ControllerBenchmarkItem.model_validate(test_payload)
    dataset = _dataset_for_items([development_item, test_item])

    with pytest.raises(
        ControllerBenchmarkAuditError,
        match="evidence id across",
    ):
        audit_dataset(dataset)


@pytest.mark.parametrize(
    "role",
    ["hard_negative", "partial_evidence", "manual_review"],
)
def test_cross_split_evidence_closure_covers_every_annotation_role(
    role: str,
) -> None:
    development_item = ControllerBenchmarkItem.model_validate(
        _multi_hop_payload()
    )
    test_payload = _unanswerable_payload()
    if role == "hard_negative":
        test_payload["hard_negatives"] = [_evidence("e-anchor")]
    elif role == "partial_evidence":
        test_payload["unanswerable_certificate"]["partial_evidence"] = [
            _evidence("e-anchor")
        ]
    else:
        test_payload["unanswerable_certificate"]["negative_search_audit"][
            "manually_reviewed_evidence_ids"
        ] = ["e-anchor"]
    test_item = ControllerBenchmarkItem.model_validate(test_payload)
    dataset = _dataset_for_items([development_item, test_item])

    with pytest.raises(
        ControllerBenchmarkAuditError,
        match="evidence id across",
    ):
        audit_dataset(dataset)


def test_evidence_catalog_enforces_membership_and_immutable_identity_tuple() -> None:
    dataset = _dataset()
    missing_catalog = [
        entry
        for entry in dataset.evidence_catalog
        if entry.evidence_id != "e-anchor"
    ]
    missing = dataset.model_copy(
        update={
            "evidence_catalog": missing_catalog,
            "evidence_catalog_sha256": evidence_catalog_payload_sha256(
                missing_catalog
            ),
            "dataset_sha256": "0" * 64,
        }
    )
    missing = missing.model_copy(
        update={"dataset_sha256": dataset_payload_sha256(missing)}
    )
    with pytest.raises(
        ControllerBenchmarkAuditError,
        match="absent from the catalog",
    ):
        audit_dataset(missing)

    conflicting_catalog = [
        (
            entry.model_copy(update={"card_id": "different-card"})
            if entry.evidence_id == "e-anchor"
            else entry
        )
        for entry in dataset.evidence_catalog
    ]
    conflicting = dataset.model_copy(
        update={
            "evidence_catalog": conflicting_catalog,
            "evidence_catalog_sha256": evidence_catalog_payload_sha256(
                conflicting_catalog
            ),
            "dataset_sha256": "0" * 64,
        }
    )
    conflicting = conflicting.model_copy(
        update={"dataset_sha256": dataset_payload_sha256(conflicting)}
    )
    with pytest.raises(
        ControllerBenchmarkAuditError,
        match="immutable catalog tuple",
    ):
        audit_dataset(conflicting)


def test_created_at_is_bound_by_every_artifact_hash_used_by_seal() -> None:
    dataset = _dataset()
    review = _review(dataset)
    manifest = _manifest(dataset)
    split_manifest = _split_manifest(dataset)

    assert dataset_payload_sha256(
        dataset.model_copy(update={"created_at": T4})
    ) != dataset.dataset_sha256
    assert review_payload_sha256(
        review.model_copy(update={"created_at": T5})
    ) != review.review_sha256
    assert independence_payload_sha256(
        manifest.model_copy(update={"created_at": T4})
    ) != manifest.manifest_sha256
    assert split_manifest_payload_sha256(
        split_manifest.model_copy(update={"created_at": T4})
    ) != split_manifest.manifest_sha256


def test_graph_independence_requires_frozen_graph_blinding_and_disjoint_roles() -> None:
    dataset = _dataset()
    manifest = _manifest(dataset)
    assert audit_graph_independence(dataset, manifest)["passed"]

    invalid_payload = manifest.model_dump(mode="python")
    invalid_payload["question_inputs"].append(
        {
            "artifact_sha256": HASH_H,
            "artifact_type": "independent_evidence_bundle",
            "parent_artifact_sha256s": [manifest.runtime_graph_sha256],
        }
    )
    invalid_payload["question_author_ids"] = ["author-1", "graph-reviewer"]
    invalid_payload["manifest_sha256"] = "0" * 64
    invalid = GraphIndependenceManifest.model_validate(invalid_payload)
    invalid = invalid.model_copy(
        update={"manifest_sha256": independence_payload_sha256(invalid)}
    )
    with pytest.raises(ControllerBenchmarkAuditError) as exc_info:
        audit_graph_independence(dataset, invalid)
    assert "derived from the runtime graph" in str(exc_info.value)
    assert "roles overlap" in str(exc_info.value)


def test_graph_independence_binds_typed_inputs_to_frozen_dataset_sources() -> None:
    dataset = _dataset()
    manifest = _manifest(dataset)
    mismatched_payload = manifest.model_dump(mode="python")
    corpus_input = next(
        artifact
        for artifact in mismatched_payload["question_inputs"]
        if artifact["artifact_type"] == "corpus_snapshot"
    )
    corpus_input["artifact_sha256"] = HASH_H
    mismatched_payload["manifest_sha256"] = "0" * 64
    mismatched = GraphIndependenceManifest.model_validate(mismatched_payload)
    mismatched = mismatched.model_copy(
        update={"manifest_sha256": independence_payload_sha256(mismatched)}
    )

    with pytest.raises(
        ControllerBenchmarkAuditError,
        match="corpus_snapshot does not match",
    ):
        audit_graph_independence(dataset, mismatched)


def test_runtime_graph_manifest_is_bound_to_frozen_controller_memory() -> None:
    dataset = _dataset()
    memory = _memory_for_dataset(dataset)
    manifest = _manifest(dataset, memory)
    assert audit_runtime_memory_binding(dataset, manifest, memory)["passed"]

    mismatched = manifest.model_copy(update={"runtime_graph_sha256": HASH_G})
    with pytest.raises(
        ControllerBenchmarkAuditError,
        match="does not match controller memory",
    ):
        audit_runtime_memory_binding(dataset, mismatched, memory)


def test_double_review_requires_two_independent_reviewers_per_item() -> None:
    dataset = _dataset()
    manifest = _manifest(dataset)
    review = _review(dataset)
    assert audit_double_review(dataset, review, manifest)["accepted_count"] == 2

    incomplete = review.model_copy(
        update={
            "decisions": [
                decision
                for decision in review.decisions
                if decision.reviewer_id == "reviewer-1"
            ],
            "review_sha256": "0" * 64,
        }
    )
    incomplete = incomplete.model_copy(
        update={"review_sha256": review_payload_sha256(incomplete)}
    )
    with pytest.raises(ControllerBenchmarkAuditError, match="fewer than two"):
        audit_double_review(dataset, incomplete, manifest)


def test_review_disagreement_requires_independent_adjudication() -> None:
    source = _dataset()
    adjudicated_item = source.items[0].model_copy(
        update={"review_status": "adjudicated"}
    )
    dataset = source.model_copy(
        update={
            "items": [adjudicated_item, source.items[1]],
            "dataset_sha256": "0" * 64,
        }
    )
    dataset = dataset.model_copy(
        update={"dataset_sha256": dataset_payload_sha256(dataset)}
    )
    rejected_checks = _passing_checks().model_copy(
        update={"reasoning_paths": False}
    )
    decisions = [
        ItemReviewDecision(
            question_id=item.question_id,
            reviewer_id=reviewer_id,
            checks=(
                rejected_checks
                if item.question_id == "multi-hop-001"
                and reviewer_id == "reviewer-2"
                else _passing_checks()
            ),
            overall_decision=(
                "reject"
                if item.question_id == "multi-hop-001"
                and reviewer_id == "reviewer-2"
                else "accept"
            ),
            notes="Independent field-level review.",
        )
        for item in dataset.items
        for reviewer_id in ("reviewer-1", "reviewer-2")
    ]
    unresolved = ControllerBenchmarkReview(
        review_id="review-disagreement",
        benchmark_sha256=dataset.dataset_sha256,
        created_at=T4,
        review_status="double_reviewed",
        decisions=decisions,
        review_sha256="0" * 64,
    )
    unresolved = unresolved.model_copy(
        update={"review_sha256": review_payload_sha256(unresolved)}
    )
    manifest = _manifest(dataset)
    with pytest.raises(ControllerBenchmarkAuditError) as exc_info:
        audit_double_review(dataset, unresolved, manifest)
    assert "without adjudication" in str(exc_info.value)
    assert "marked adjudicated without a corresponding" in str(exc_info.value)

    resolved_payload = unresolved.model_dump(mode="python")
    resolved_payload.update(
        {
            "review_status": "human_verified",
            "adjudications": [
                {
                    "question_id": "multi-hop-001",
                    "adjudicator_id": "adjudicator-1",
                    "final_checks": _passing_checks(),
                    "final_decision": "accept",
                    "notes": "Resolved against the frozen concept and evidence sources.",
                }
            ],
            "review_sha256": "0" * 64,
        }
    )
    resolved = ControllerBenchmarkReview.model_validate(resolved_payload)
    resolved = resolved.model_copy(
        update={"review_sha256": review_payload_sha256(resolved)}
    )

    assert audit_double_review(dataset, resolved, manifest)["accepted_count"] == 2


def test_unanimous_reject_cannot_be_overturned_by_adjudication() -> None:
    source = _dataset()
    rejected_item = source.items[0].model_copy(
        update={"review_status": "adjudicated"}
    )
    dataset = source.model_copy(
        update={
            "items": [rejected_item, source.items[1]],
            "dataset_sha256": "0" * 64,
        }
    )
    dataset = dataset.model_copy(
        update={"dataset_sha256": dataset_payload_sha256(dataset)}
    )
    rejected_checks = _passing_checks().model_copy(
        update={"reasoning_paths": False}
    )
    decisions = [
        ItemReviewDecision(
            question_id=item.question_id,
            reviewer_id=reviewer_id,
            checks=(
                rejected_checks
                if item.question_id == rejected_item.question_id
                else _passing_checks()
            ),
            overall_decision=(
                "reject"
                if item.question_id == rejected_item.question_id
                else "accept"
            ),
            notes="Independent field-level review.",
        )
        for item in dataset.items
        for reviewer_id in ("reviewer-1", "reviewer-2")
    ]
    review = ControllerBenchmarkReview(
        review_id="unanimous-reject",
        benchmark_sha256=dataset.dataset_sha256,
        created_at=T4,
        review_status="human_verified",
        decisions=decisions,
        adjudications=[
            {
                "question_id": rejected_item.question_id,
                "adjudicator_id": "adjudicator-1",
                "final_checks": _passing_checks(),
                "final_decision": "accept",
                "notes": "Attempted override.",
            }
        ],
        review_sha256="0" * 64,
    )
    review = review.model_copy(
        update={"review_sha256": review_payload_sha256(review)}
    )

    with pytest.raises(
        ControllerBenchmarkAuditError,
        match="unanimously rejected",
    ):
        audit_double_review(dataset, review, _manifest(dataset))


def test_seal_binds_all_verified_artifacts() -> None:
    dataset = _dataset(lifecycle_status="sealed")
    manifest = _manifest(dataset)
    split_manifest = _split_manifest(dataset)
    review = _review(dataset)
    seal = _seal(dataset, review, manifest, split_manifest)

    assert audit_split_manifest(dataset, split_manifest)["passed"]
    assert audit_seal(
        dataset,
        review,
        manifest,
        split_manifest,
        seal,
    )["passed"]

    wrong_binding = seal.model_copy(
        update={"split_manifest_sha256": HASH_A, "seal_sha256": "0" * 64}
    )
    wrong_binding = wrong_binding.model_copy(
        update={"seal_sha256": seal_payload_sha256(wrong_binding)}
    )
    with pytest.raises(ControllerBenchmarkAuditError, match="split_manifest"):
        audit_seal(dataset, review, manifest, split_manifest, wrong_binding)


def test_seal_recomputes_supplied_split_manifest_instead_of_trusting_dataset() -> None:
    dataset = _dataset(lifecycle_status="sealed")
    manifest = _manifest(dataset)
    split_manifest = _split_manifest(dataset)
    review = _review(dataset)
    seal = _seal(dataset, review, manifest, split_manifest)

    tampered_payload = split_manifest.model_dump(mode="python")
    tampered_payload["assignments"][0]["split"] = "test"
    tampered_payload["manifest_sha256"] = "0" * 64
    tampered = ControllerBenchmarkSplitManifest.model_validate(tampered_payload)
    tampered = tampered.model_copy(
        update={"manifest_sha256": split_manifest_payload_sha256(tampered)}
    )

    with pytest.raises(
        ControllerBenchmarkAuditError,
        match="Dataset split-manifest hash",
    ):
        audit_seal(dataset, review, manifest, tampered, seal)


def test_split_manifest_must_predate_dataset_authoring() -> None:
    dataset = _dataset()
    late_manifest = _split_manifest(dataset).model_copy(
        update={"created_at": dataset.created_at, "manifest_sha256": "0" * 64}
    )
    late_manifest = late_manifest.model_copy(
        update={
            "manifest_sha256": split_manifest_payload_sha256(late_manifest)
        }
    )
    rebound_dataset = dataset.model_copy(
        update={
            "split_manifest_sha256": late_manifest.manifest_sha256,
            "dataset_sha256": "0" * 64,
        }
    )
    rebound_dataset = rebound_dataset.model_copy(
        update={"dataset_sha256": dataset_payload_sha256(rebound_dataset)}
    )
    with pytest.raises(
        ControllerBenchmarkAuditError,
        match="must predate benchmark dataset authoring",
    ):
        audit_split_manifest(rebound_dataset, late_manifest)


def test_seal_timestamp_must_follow_graph_freeze_and_authoring_start() -> None:
    dataset = _dataset(lifecycle_status="sealed")
    split_manifest = _split_manifest(dataset)
    review = _review(dataset)
    manifest_payload = _manifest(dataset).model_dump(mode="python")
    manifest_payload["benchmark_authoring_started_at"] = datetime(
        2026,
        1,
        6,
        tzinfo=UTC,
    )
    manifest_payload["manifest_sha256"] = "0" * 64
    manifest = GraphIndependenceManifest.model_validate(manifest_payload)
    manifest = manifest.model_copy(
        update={"manifest_sha256": independence_payload_sha256(manifest)}
    )
    seal = _seal(dataset, review, manifest, split_manifest)

    with pytest.raises(
        ControllerBenchmarkAuditError,
        match="predates an artifact",
    ):
        audit_seal(dataset, review, manifest, split_manifest, seal)


def test_deterministic_metrics_score_concepts_dnf_and_directed_typed_path() -> None:
    item = ControllerBenchmarkItem.model_validate(_multi_hop_payload())
    dataset = _dataset_for_items([item], lifecycle_status="sealed")
    binding = _evaluation_binding(dataset, split="development")
    trace = _trace_for_item(
        item,
        run_binding=binding,
        retrieved_concept_ids=["anchor"],
        retrieved_evidence_ids=[
            "e-anchor",
            "e-bridge-part-1",
            "e-bridge-part-2",
            "e-target",
        ],
        path_edges=[("anchor", "bridge", "part_of")],
    )
    prediction = ControllerBenchmarkPrediction(trace=trace)

    metrics = evaluate_item_prediction(
        item,
        prediction,
        dataset=dataset,
        run_binding=binding,
    )

    assert metrics.required_concept_recall.value == pytest.approx(2 / 3)
    assert metrics.evidence_requirement_recall.value == 1.0
    assert metrics.valid_path_success.value == 0.0
    assert metrics.best_valid_path_edge_recall.value == 0.5


def test_path_metric_cannot_score_edges_absent_from_frozen_runtime_graph() -> None:
    item = ControllerBenchmarkItem.model_validate(_multi_hop_payload())
    dataset = _dataset_for_items([item], lifecycle_status="sealed")
    binding = _evaluation_binding(dataset, split="development")
    with pytest.raises(ValueError, match="absent from frozen memory"):
        _trace_for_item(
            item,
            run_binding=binding,
            retrieved_concept_ids=["bridge"],
            retrieved_evidence_ids=["e-anchor"],
            path_edges=[
                ("bridge", "anchor", "part_of"),
                ("bridge", "target", "related"),
            ],
            answer_text="A non-reference answer.",
        )


def test_prediction_ids_actions_and_cost_are_trace_derived_only() -> None:
    item = ControllerBenchmarkItem.model_validate(_multi_hop_payload())
    dataset = _dataset_for_items([item], lifecycle_status="sealed")
    binding = _evaluation_binding(dataset, split="development")
    trace = _trace_for_item(
        item,
        run_binding=binding,
        retrieved_concept_ids=["anchor", "bridge", "target"],
        retrieved_evidence_ids=["e-anchor"],
    )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ControllerBenchmarkPrediction(
            trace=trace,
            retrieved_evidence_ids=[],
            cost={"retrieval_calls": 0},
        )

    derived = derive_trace_evaluation_inputs(trace)
    assert derived.retrieved_evidence_ids == ["e-anchor"]
    assert derived.final_action == "answer"
    assert derived.cost.retrieval_calls == 2

    supported_need = ControllerKnowledgeNeed(
        need_id="need-1",
        description=item.question,
        status="supported",
        confidence=1.0,
        support_concept_ids=["anchor"],
        support_evidence_ids=["e-anchor"],
    )
    preloaded = ControllerState(
        question_id=item.question_id,
        question=item.question,
        knowledge_needs=[supported_need],
        retrieved_concept_ids=["anchor"],
        retrieved_evidence_ids=["e-anchor"],
        verified_evidence_ids=["e-anchor"],
        answerability_confidence=1.0,
        cost=ControllerCost(unique_concepts=1, unique_evidence=1),
    )
    answer_action = AnswerAction(supported_need_ids=["need-1"])
    answer_observation = AnswerObservation(
        action_fingerprint=action_fingerprint(answer_action),
        cost=ControllerCost(steps=1),
    )
    preloaded_final = reduce_controller_state(
        preloaded,
        answer_action,
        answer_observation,
    )
    preloaded_step = ControllerStep(
        step_index=0,
        state_before_sha256=controller_state_sha256(preloaded),
        state_before=preloaded,
        action=answer_action,
        observation=answer_observation,
        state_after=preloaded_final,
    )
    payload = {
        "trace_id": "preloaded-zero-cost",
        "protocol_id": binding.protocol.protocol_id,
        "protocol_sha256": binding.protocol.protocol_sha256,
        "memory_id": binding.memory_id,
        "memory_sha256": binding.memory_sha256,
        "question_id": item.question_id,
        "policy_name": binding.policy_name,
        "initial_state": preloaded,
        "steps": [preloaded_step],
        "final_state": preloaded_final,
        "stop_reason": "answer",
        "status": "completed",
        "final_answer": item.reference_answers[0],
        "citation_evidence_ids": ["e-anchor"],
        "created_at": T6,
        "completed_at": T7,
    }
    provisional = ControllerTrace.model_construct(
        **payload,
        trace_sha256="0" * 64,
    )
    canonical_but_preloaded = ControllerTrace(
        **payload,
        trace_sha256=controller_trace_payload_sha256(provisional),
    )
    with pytest.raises(ValidationError, match="fresh state"):
        ControllerBenchmarkPrediction(trace=canonical_but_preloaded)


def test_empty_answer_cannot_enter_prediction_or_receive_full_score() -> None:
    item = ControllerBenchmarkItem.model_validate(_multi_hop_payload())
    dataset = _dataset_for_items([item], lifecycle_status="sealed")
    binding = _evaluation_binding(dataset, split="development")
    trace = _trace_for_item(
        item,
        run_binding=binding,
        retrieved_concept_ids=["anchor"],
        retrieved_evidence_ids=["e-anchor"],
    )
    invalid = trace.model_copy(
        update={"final_answer": "", "trace_sha256": "0" * 64}
    )
    invalid = invalid.model_copy(
        update={"trace_sha256": controller_trace_payload_sha256(invalid)}
    )
    with pytest.raises(ValidationError, match="requires a final answer"):
        ControllerBenchmarkPrediction(trace=invalid)

    wrong_answer = ControllerBenchmarkPrediction(
        trace=_trace_for_item(
            item,
            run_binding=binding,
            retrieved_concept_ids=["anchor", "bridge", "target"],
            retrieved_evidence_ids=[
                "e-anchor",
                "e-bridge-direct",
                "e-target",
            ],
            path_edges=[
                ("anchor", "bridge", "part_of"),
                ("bridge", "target", "example_of"),
            ],
            answer_text="This is semantically unrelated.",
        )
    )
    metrics = evaluate_item_prediction(
        item,
        wrong_answer,
        dataset=dataset,
        run_binding=binding,
        answer_scoring="exact_match",
    )
    assert metrics.answer_correctness.value == 0.0
    assert metrics.retrieval_control_quality_score == 1.0


def test_unanswerable_stop_correctness_and_failed_quality_are_distinct() -> None:
    item = ControllerBenchmarkItem.model_validate(
        _unanswerable_payload(
            question_id="unanswerable-development",
            split="development",
        )
    )
    dataset = _dataset_for_items([item], lifecycle_status="sealed")
    binding = _evaluation_binding(dataset, split="development")
    abstention = ControllerBenchmarkPrediction(
        trace=_trace_for_item(
            item,
            run_binding=binding,
            final_action="abstain",
            retrieved_evidence_ids=["e-near-miss"],
        )
    )
    answer = ControllerBenchmarkPrediction(
        trace=_trace_for_item(
            item,
            run_binding=binding,
            final_action="answer",
            retrieved_evidence_ids=["e-near-miss"],
            answer_text="Unsupported answer.",
        )
    )
    failure = ControllerBenchmarkPrediction(
        trace=_trace_for_item(
            item,
            run_binding=binding,
            final_action=None,
            status="failed",
            retrieved_evidence_ids=["e-near-miss"],
        )
    )

    abstention_metrics = evaluate_item_prediction(
        item,
        abstention,
        dataset=dataset,
        run_binding=binding,
    )
    assert abstention_metrics.stop_correctness.value == 1.0
    assert abstention_metrics.hard_negative_hits == 1
    assert abstention_metrics.evidence_precision.value == 0.0
    assert (
        evaluate_item_prediction(
            item,
            answer,
            dataset=dataset,
            run_binding=binding,
        ).stop_correctness.value
        == 0.0
    )
    failed_metrics = evaluate_item_prediction(
        item,
        failure,
        dataset=dataset,
        run_binding=binding,
    )
    assert failed_metrics.stop_correctness.value == 0.0
    assert failed_metrics.required_concept_recall.denominator == 0
    assert failed_metrics.retrieved_evidence_count == 1
    assert failed_metrics.retrieval_metrics_diagnostic_only
    assert failed_metrics.retrieval_control_quality_score == 0.0


def test_aggregate_metrics_require_sealed_preregistered_single_split() -> None:
    item = ControllerBenchmarkItem.model_validate(_multi_hop_payload())
    dataset = _dataset_for_items([item], lifecycle_status="sealed")
    binding = _evaluation_binding(dataset, split="development")
    prediction = ControllerBenchmarkPrediction(
        trace=_trace_for_item(
            item,
            run_binding=binding,
            retrieved_concept_ids=["anchor"],
            retrieved_evidence_ids=[
                "e-anchor",
                "e-bridge-direct",
                "e-target",
            ],
            path_edges=[
                ("anchor", "bridge", "part_of"),
                ("bridge", "target", "example_of"),
            ],
        )
    )

    with pytest.raises(TypeError):
        evaluate_controller_predictions(
            dataset,
            [prediction],
            split="development",
        )

    report = evaluate_controller_predictions(
        dataset,
        [prediction],
        split="development",
        run_binding=binding,
    )

    assert report.split == "development"
    assert report.answerable_count == 1
    assert report.stop_correctness.value == 1.0
    assert report.required_concept_recall.value == 1.0
    assert report.evidence_requirement_recall.value == 1.0
    assert report.evidence_precision.value == 1.0
    assert report.valid_path_success_rate.value == 1.0
    assert report.evaluation_binding_sha256 == binding.binding_sha256
    assert report.evaluator_code_sha256 == current_evaluator_code_sha256()


def test_returning_all_evidence_loses_precision_and_quality_cost() -> None:
    payload = _multi_hop_payload()
    payload["hard_negatives"] = [_evidence("e-hard-negative")]
    payload["difficulty"]["hard_negative_count"] = 1
    item = ControllerBenchmarkItem.model_validate(payload)
    dataset = _dataset_for_items([item], lifecycle_status="sealed")
    unrelated = EvidenceReference.model_validate(_evidence("e-unrelated"))
    expanded_catalog = [*dataset.evidence_catalog, unrelated]
    dataset = dataset.model_copy(
        update={
            "evidence_catalog": expanded_catalog,
            "evidence_catalog_sha256": evidence_catalog_payload_sha256(
                expanded_catalog
            ),
            "dataset_sha256": "0" * 64,
        }
    )
    dataset = dataset.model_copy(
        update={"dataset_sha256": dataset_payload_sha256(dataset)}
    )
    binding = _evaluation_binding(dataset, split="development")
    path_edges = [
        ("anchor", "bridge", "part_of"),
        ("bridge", "target", "example_of"),
    ]
    minimal = ControllerBenchmarkPrediction(
        trace=_trace_for_item(
            item,
            run_binding=binding,
            retrieved_concept_ids=["anchor"],
            retrieved_evidence_ids=[
                "e-anchor",
                "e-bridge-direct",
                "e-target",
            ],
            path_edges=path_edges,
        )
    )
    return_everything = ControllerBenchmarkPrediction(
        trace=_trace_for_item(
            item,
            run_binding=binding,
            retrieved_concept_ids=["anchor"],
            retrieved_evidence_ids=[
                "e-anchor",
                "e-bridge-direct",
                "e-bridge-part-1",
                "e-bridge-part-2",
                "e-target",
                "e-hard-negative",
                "e-unrelated",
            ],
            path_edges=path_edges,
            retrieval_calls=10,
            prompt_characters=900,
            completion_tokens=100,
            latency_milliseconds=100.0,
        )
    )

    minimal_report = evaluate_controller_predictions(
        dataset,
        [minimal],
        split="development",
        run_binding=binding,
    )
    bloated_report = evaluate_controller_predictions(
        dataset,
        [return_everything],
        split="development",
        run_binding=binding,
    )

    assert minimal_report.retrieval_control_quality_score == 1.0
    assert bloated_report.evidence_precision.value == pytest.approx(5 / 7)
    assert bloated_report.hard_negative_hits == 1
    assert bloated_report.retrieval_control_quality_score < 1.0
    assert (
        bloated_report.retrieval_control_quality_per_retrieval_call
        < minimal_report.retrieval_control_quality_per_retrieval_call
    )
    assert bloated_report.cost.total_retrieved_evidence == 7


def test_answer_scoring_is_deterministic_or_explicitly_paper_ineligible() -> None:
    item = ControllerBenchmarkItem.model_validate(_multi_hop_payload())
    dataset = _dataset_for_items([item], lifecycle_status="sealed")
    binding = _evaluation_binding(dataset, split="development")
    prediction = ControllerBenchmarkPrediction(
        trace=_trace_for_item(
            item,
            run_binding=binding,
            retrieved_concept_ids=["anchor"],
            retrieved_evidence_ids=[
                "e-anchor",
                "e-bridge-direct",
                "e-target",
            ],
            path_edges=[
                ("anchor", "bridge", "part_of"),
                ("bridge", "target", "example_of"),
            ],
        )
    )

    unscored = evaluate_controller_predictions(
        dataset,
        [prediction],
        split="development",
        run_binding=binding,
    )
    assert unscored.answer_correctness.value is None
    assert unscored.answer_correctness_source == "not_scored"
    assert unscored.by_item[0].answer_text == item.reference_answers[0]
    assert unscored.by_item[0].citation_evidence_ids

    exact = evaluate_controller_predictions(
        dataset,
        [prediction],
        split="development",
        run_binding=binding,
        answer_scoring="exact_match",
    )
    assert exact.answer_correctness.value == 1.0
    assert exact.answer_correctness_source == "deterministic_exact_match"
    assert not exact.answer_correctness_paper_eligible
    assert exact.evaluation_scope == "development_diagnostic"
    assert (
        exact.execution_provenance_status
        == "declarative_membership_audit_only"
    )
    assert not exact.paper_claim_eligible

    with pytest.raises(ValidationError):
        AnswerCorrectnessAssessment(
            question_id=item.question_id,
            trace_sha256=prediction.trace.trace_sha256,
            verdict="correct",
            assessment_method="exact_match",
            evaluator_id="not-allowed",
            assessment_protocol_sha256=HASH_A,
            notes="An externally supplied exact-match verdict is forbidden.",
        )

    assessment = AnswerCorrectnessAssessment(
        question_id=item.question_id,
        trace_sha256=prediction.trace.trace_sha256,
        verdict="partially_correct",
        assessment_method="human",
        evaluator_id="diagnostic-reviewer",
        assessment_protocol_sha256=HASH_A,
        notes="Development-only semantic diagnostic.",
    )
    diagnostic = evaluate_controller_predictions(
        dataset,
        [prediction],
        split="development",
        run_binding=binding,
        answer_scoring="development_diagnostic",
        answer_assessments=[assessment],
    )
    assert diagnostic.answer_correctness.value == 0.5
    assert (
        diagnostic.answer_correctness_source
        == "development_diagnostic_external"
    )
    assert not diagnostic.answer_correctness_paper_eligible
    assert assessment.evaluation_scope == "development_diagnostic"
    assert not assessment.paper_eligible

    wrong_binding = assessment.model_copy(update={"trace_sha256": HASH_H})
    with pytest.raises(ValueError, match="different trace"):
        evaluate_controller_predictions(
            dataset,
            [prediction],
            split="development",
            run_binding=binding,
            answer_scoring="development_diagnostic",
            answer_assessments=[wrong_binding],
        )


def test_fabricated_known_id_trace_remains_explicitly_paper_ineligible() -> None:
    item = ControllerBenchmarkItem.model_validate(_multi_hop_payload())
    dataset = _dataset_for_items([item], lifecycle_status="sealed")
    binding = _evaluation_binding(dataset, split="development")
    # These are real memory ids, but the fixture invents the dense ordering and
    # scores instead of replaying a canonical retriever/runner execution.
    prediction = ControllerBenchmarkPrediction(
        trace=_trace_for_item(
            item,
            run_binding=binding,
            retrieved_concept_ids=["target", "anchor", "bridge"],
            retrieved_evidence_ids=[
                "e-target",
                "e-anchor",
                "e-bridge-direct",
            ],
            path_edges=[
                ("anchor", "bridge", "part_of"),
                ("bridge", "target", "example_of"),
            ],
        )
    )
    report = evaluate_controller_predictions(
        dataset,
        [prediction],
        split="development",
        run_binding=binding,
        answer_scoring="exact_match",
    )

    assert report.answer_correctness.value == 1.0
    assert report.evaluation_scope == "development_diagnostic"
    assert (
        report.execution_provenance_status
        == "declarative_membership_audit_only"
    )
    assert not report.paper_claim_eligible
    assert not report.answer_correctness_paper_eligible
    item_metrics = report.by_item[0]
    assert item_metrics.evaluation_scope == "development_diagnostic"
    assert (
        item_metrics.execution_provenance_status
        == "declarative_membership_audit_only"
    )
    assert not item_metrics.paper_claim_eligible
    assert item_metrics.retrieval_metrics_diagnostic_only

    ineligible_payload = report.model_dump(mode="python")
    ineligible_payload["paper_claim_eligible"] = True
    ineligible_payload["answer_correctness_paper_eligible"] = True
    with pytest.raises(ValidationError):
        type(report).model_validate(ineligible_payload)

    item_payload = item_metrics.model_dump(mode="python")
    item_payload["paper_claim_eligible"] = True
    item_payload["retrieval_metrics_diagnostic_only"] = False
    with pytest.raises(ValidationError):
        type(item_metrics).model_validate(item_payload)


@pytest.mark.parametrize(
    ("trace_kwargs", "expected_error"),
    [
        (
            {
                "retrieved_concept_ids": ["invented-concept"],
                "retrieved_evidence_ids": ["e-anchor"],
                "bind_hits_to_memory": False,
            },
            "Unknown state retrieved concept ids",
        ),
        (
            {
                "retrieved_concept_ids": ["anchor"],
                "retrieved_evidence_ids": ["invented-evidence"],
                "bind_hits_to_memory": False,
            },
            "Unknown state retrieved evidence ids",
        ),
        (
            {
                "retrieved_concept_ids": ["anchor"],
                "retrieved_evidence_ids": ["e-anchor"],
                "path_edges": [("anchor", "bridge", "related")],
                "bind_hits_to_memory": False,
            },
            "Unknown state traversed relation ids",
        ),
    ],
)
def test_formal_evaluator_rejects_hits_absent_from_frozen_memory(
    trace_kwargs: dict[str, object],
    expected_error: str,
) -> None:
    item = ControllerBenchmarkItem.model_validate(_multi_hop_payload())
    dataset = _dataset_for_items([item], lifecycle_status="sealed")
    binding = _evaluation_binding(dataset, split="development")
    prediction = ControllerBenchmarkPrediction(
        trace=_trace_for_item(item, run_binding=binding, **trace_kwargs)
    )

    with pytest.raises(ValueError, match=expected_error):
        evaluate_item_prediction(
            item,
            prediction,
            dataset=dataset,
            run_binding=binding,
        )


def test_formal_evaluator_rejects_unbound_trace_question_and_upstream_forgery() -> None:
    item = ControllerBenchmarkItem.model_validate(_multi_hop_payload())
    dataset = _dataset_for_items([item], lifecycle_status="sealed")
    binding = _evaluation_binding(dataset, split="development")
    prediction = ControllerBenchmarkPrediction(
        trace=_trace_for_item(
            item,
            run_binding=binding,
            retrieved_concept_ids=["anchor"],
            retrieved_evidence_ids=["e-anchor"],
        )
    )

    fake_item = item.model_copy(
        update={"question": "A different question with the same identifier."}
    )
    wrong_question_prediction = ControllerBenchmarkPrediction(
        trace=_trace_for_item(
            fake_item,
            run_binding=binding,
            retrieved_concept_ids=["anchor"],
            retrieved_evidence_ids=["e-anchor"],
            answer_text="Answer.",
        )
    )
    with pytest.raises(ValueError, match="initial question"):
        evaluate_item_prediction(
            item,
            wrong_question_prediction,
            dataset=dataset,
            run_binding=binding,
        )

    tampered_trace = prediction.trace.model_copy(
        update={"memory_sha256": HASH_H, "trace_sha256": "0" * 64}
    )
    tampered_trace = tampered_trace.model_copy(
        update={
            "trace_sha256": controller_trace_payload_sha256(tampered_trace)
        }
    )
    unbound_prediction = ControllerBenchmarkPrediction(trace=tampered_trace)
    with pytest.raises(ValueError, match="memory_sha256"):
        evaluate_item_prediction(
            item,
            unbound_prediction,
            dataset=dataset,
            run_binding=binding,
        )

    forged_review = binding.benchmark_review.model_copy(
        update={"created_at": T1}
    )
    provisional_binding = binding.model_copy(
        update={
            "benchmark_review": forged_review,
            "binding_sha256": "0" * 64,
        }
    )
    forged_binding = provisional_binding.model_copy(
        update={
            "binding_sha256": evaluation_run_binding_payload_sha256(
                provisional_binding
            )
        }
    )
    with pytest.raises(
        ControllerBenchmarkAuditError,
        match="review hash is not canonical",
    ):
        evaluate_controller_predictions(
            dataset,
            [prediction],
            split="development",
            run_binding=forged_binding,
        )

    with pytest.raises(ValidationError, match="Oracle-only"):
        _evaluation_binding(
            dataset,
            split="development",
            oracle_only=True,
        )


def test_test_gold_evaluation_is_fail_closed_without_one_use_ledger() -> None:
    item = ControllerBenchmarkItem.model_validate(_unanswerable_payload())
    dataset = _dataset_for_items([item], lifecycle_status="sealed")
    binding = _evaluation_binding(dataset, split="test")
    prediction = ControllerBenchmarkPrediction(
        trace=_trace_for_item(
            item,
            run_binding=binding,
            final_action="abstain",
            retrieved_evidence_ids=["e-near-miss"],
        )
    )

    with pytest.raises(ValueError, match="one-use ledger"):
        evaluate_controller_predictions(
            dataset,
            [prediction],
            split="test",
            run_binding=binding,
        )
