from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime

from pydantic import BaseModel

from ..controller_schemas import (
    ControllerMemorySnapshot,
    controller_memory_payload_sha256,
)
from .schemas import (
    ConceptRegistryEntry,
    ControllerBenchmarkDataset,
    ControllerBenchmarkReview,
    ControllerBenchmarkSeal,
    ControllerBenchmarkSplitManifest,
    EvidenceReference,
    GraphIndependenceManifest,
)


class ControllerBenchmarkAuditError(ValueError):
    pass


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _model_payload(
    model: BaseModel,
    *,
    excluded_fields: set[str],
) -> dict[str, object]:
    return model.model_dump(mode="json", exclude=excluded_fields)


def _sha256_payload(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def dataset_payload_sha256(dataset: ControllerBenchmarkDataset) -> str:
    # Lifecycle is operational state, not benchmark content. This permits an
    # adjudicated immutable payload to become sealed without changing its identity.
    return _sha256_payload(
        _model_payload(
            dataset,
            excluded_fields={"dataset_sha256", "lifecycle_status"},
        )
    )


def evidence_catalog_payload_sha256(
    evidence_catalog: list[EvidenceReference],
) -> str:
    """Hash the immutable evidence-id metadata map independent of list order."""

    entries = sorted(
        (
            entry.model_dump(mode="json")
            for entry in evidence_catalog
        ),
        key=lambda entry: str(entry["evidence_id"]),
    )
    return _sha256_payload(
        {
            "schema_version": "1.0",
            "evidence": entries,
        }
    )


def concept_registry_payload_sha256(
    concept_registry: list[ConceptRegistryEntry],
) -> str:
    """Hash the immutable concept registry independent of list order."""

    entries = sorted(
        (
            entry.model_dump(mode="json")
            for entry in concept_registry
        ),
        key=lambda entry: str(entry["concept_id"]),
    )
    return _sha256_payload(
        {
            "schema_version": "1.0",
            "concepts": entries,
        }
    )


def runtime_graph_payload_sha256(memory: ControllerMemorySnapshot) -> str:
    """Hash the exact typed, directed runtime graph in a memory snapshot."""

    relations = sorted(
        (
            relation.model_dump(mode="json")
            for relation in memory.relations
        ),
        key=lambda relation: str(relation["relation_id"]),
    )
    return _sha256_payload(
        {
            "schema_version": "1.0",
            "relations": relations,
        }
    )


def review_payload_sha256(review: ControllerBenchmarkReview) -> str:
    return _sha256_payload(
        _model_payload(
            review,
            excluded_fields={"review_sha256", "review_status"},
        )
    )


def independence_payload_sha256(manifest: GraphIndependenceManifest) -> str:
    return _sha256_payload(
        _model_payload(
            manifest,
            excluded_fields={"manifest_sha256"},
        )
    )


def split_manifest_payload_sha256(
    manifest: ControllerBenchmarkSplitManifest,
) -> str:
    return _sha256_payload(
        _model_payload(
            manifest,
            excluded_fields={"manifest_sha256"},
        )
    )


def seal_payload_sha256(seal: ControllerBenchmarkSeal) -> str:
    return _sha256_payload(
        _model_payload(seal, excluded_fields={"seal_sha256"})
    )


def audit_dataset(dataset: ControllerBenchmarkDataset) -> dict[str, object]:
    errors: list[str] = []
    expected_hash = dataset_payload_sha256(dataset)
    if dataset.dataset_sha256 != expected_hash:
        errors.append("Benchmark dataset hash is not canonical.")
    expected_registry_hash = concept_registry_payload_sha256(
        dataset.concept_registry
    )
    if dataset.concept_registry_sha256 != expected_registry_hash:
        errors.append("Concept-registry hash is not canonical.")
    expected_catalog_hash = evidence_catalog_payload_sha256(
        dataset.evidence_catalog
    )
    if dataset.evidence_catalog_sha256 != expected_catalog_hash:
        errors.append("Evidence-catalog hash is not canonical.")

    family_splits: dict[str, set[str]] = defaultdict(set)
    objective_cluster_splits: dict[str, set[str]] = defaultdict(set)
    bundle_splits: dict[str, set[str]] = defaultdict(set)
    concept_structure_splits: dict[tuple[object, ...], set[str]] = defaultdict(set)
    evidence_structure_splits: dict[tuple[str, ...], set[str]] = defaultdict(set)
    path_structure_splits: dict[tuple[object, ...], set[str]] = defaultdict(set)
    evidence_id_splits: dict[str, set[str]] = defaultdict(set)
    catalog_by_id = {
        entry.evidence_id: entry for entry in dataset.evidence_catalog
    }
    registry_ids = {
        entry.concept_id for entry in dataset.concept_registry
    }

    for item in dataset.items:
        family_splits[item.question_family_id].add(item.split)
        objective_cluster_splits[item.learning_objective_cluster_id].add(item.split)
        bundle_splits[item.source_evidence_bundle_id].add(item.split)
        required_ids = {
            concept.concept_id for concept in item.required_concepts
        }
        missing_required = sorted(required_ids - registry_ids)
        if missing_required:
            errors.append(
                f"{item.question_id} references required concepts absent "
                f"from the registry: {missing_required}."
            )
        if item.required_concepts:
            concept_signature = tuple(
                sorted(
                    (
                        concept.concept_id,
                        concept.role,
                        concept.mention_status,
                        concept.necessity,
                    )
                    for concept in item.required_concepts
                )
            )
            concept_structure_splits[concept_signature].add(item.split)
        gold_references = [
            reference
            for requirement in item.evidence_requirements
            for alternative in requirement.alternatives
            for reference in alternative.evidence
        ]
        evidence_ids = tuple(
            sorted(
                {
                    reference.evidence_id
                    for reference in gold_references
                }
            )
        )
        if evidence_ids:
            evidence_structure_splits[evidence_ids].add(item.split)
        certificate = item.unanswerable_certificate
        partial_references = (
            [] if certificate is None else certificate.partial_evidence
        )
        manually_reviewed_ids = (
            []
            if certificate is None
            else certificate.negative_search_audit.manually_reviewed_evidence_ids
        )
        typed_references = [
            ("gold", reference) for reference in gold_references
        ] + [
            ("hard_negative", reference) for reference in item.hard_negatives
        ] + [
            ("partial_evidence", reference)
            for reference in partial_references
        ]
        all_role_ids = {
            reference.evidence_id for _, reference in typed_references
        }.union(manually_reviewed_ids)
        for evidence_id in all_role_ids:
            evidence_id_splits[evidence_id].add(item.split)
            if evidence_id not in catalog_by_id:
                errors.append(
                    f"{item.question_id} references evidence absent from the "
                    f"catalog: {evidence_id}."
                )
        for role, reference in typed_references:
            catalog_entry = catalog_by_id.get(reference.evidence_id)
            if catalog_entry is None:
                continue
            if _evidence_identity(reference) != _evidence_identity(
                catalog_entry
            ):
                errors.append(
                    f"{item.question_id} {role} evidence metadata conflicts "
                    f"with the immutable catalog tuple for "
                    f"{reference.evidence_id}."
                )
        for path in item.valid_reasoning_paths:
            path_endpoint_ids = {
                edge.source_concept_id
                for edge in path.edges
            }.union(
                edge.target_concept_id for edge in path.edges
            )
            missing_endpoints = sorted(path_endpoint_ids - registry_ids)
            if missing_endpoints:
                errors.append(
                    f"{item.question_id} reasoning-path endpoints are absent "
                    f"from the concept registry: {missing_endpoints}."
                )
            path_signature = tuple(
                (
                    edge.source_concept_id,
                    edge.target_concept_id,
                    edge.relation_type,
                )
                for edge in path.edges
            )
            path_structure_splits[path_signature].add(item.split)
        if item.unanswerable_certificate is not None:
            missing_closest = sorted(
                set(
                    item.unanswerable_certificate.closest_supported_concept_ids
                )
                - registry_ids
            )
            if missing_closest:
                errors.append(
                    f"{item.question_id} failure certificate references "
                    f"concepts absent from the registry: {missing_closest}."
                )

    _append_cross_split_errors(errors, family_splits, "question family")
    _append_cross_split_errors(
        errors,
        objective_cluster_splits,
        "learning-objective cluster",
    )
    _append_cross_split_errors(errors, bundle_splits, "source evidence bundle")
    _append_cross_split_errors(
        errors,
        concept_structure_splits,
        "required-concept structure",
    )
    _append_cross_split_errors(
        errors,
        evidence_structure_splits,
        "gold-evidence structure",
    )
    _append_cross_split_errors(
        errors,
        evidence_id_splits,
        "evidence id across gold/negative/certificate/review roles",
    )
    _append_cross_split_errors(
        errors,
        path_structure_splits,
        "reasoning-path structure",
    )

    if errors:
        raise ControllerBenchmarkAuditError("\n".join(errors))
    return {
        "passed": True,
        "benchmark_id": dataset.benchmark_id,
        "dataset_sha256": expected_hash,
        "question_count": len(dataset.items),
        "concept_registry_count": len(dataset.concept_registry),
        "evidence_catalog_count": len(dataset.evidence_catalog),
        "development_count": sum(
            item.split == "development" for item in dataset.items
        ),
        "test_count": sum(item.split == "test" for item in dataset.items),
    }


def _evidence_identity(
    reference: EvidenceReference,
) -> tuple[str, str, str, str]:
    return (
        reference.evidence_id,
        reference.card_id,
        reference.claim_id,
        reference.modality,
    )


def audit_split_manifest(
    dataset: ControllerBenchmarkDataset,
    manifest: ControllerBenchmarkSplitManifest,
) -> dict[str, object]:
    errors: list[str] = []
    expected_hash = split_manifest_payload_sha256(manifest)
    if manifest.manifest_sha256 != expected_hash:
        errors.append("Split manifest hash is not canonical.")
    if dataset.split_manifest_sha256 != expected_hash:
        errors.append(
            "Dataset split-manifest hash does not match the supplied manifest."
        )
    if manifest.benchmark_id != dataset.benchmark_id:
        errors.append("Split manifest benchmark id does not match the dataset.")
    if _as_utc(manifest.created_at) >= _as_utc(dataset.created_at):
        errors.append(
            "Split manifest must predate benchmark dataset authoring."
        )

    expected_assignments = {
        item.question_id: (
            item.split,
            item.question_family_id,
            item.learning_objective_cluster_id,
            item.source_evidence_bundle_id,
        )
        for item in dataset.items
    }
    observed_assignments = {
        assignment.question_id: (
            assignment.split,
            assignment.question_family_id,
            assignment.learning_objective_cluster_id,
            assignment.source_evidence_bundle_id,
        )
        for assignment in manifest.assignments
    }
    cluster_splits: dict[str, set[str]] = defaultdict(set)
    for assignment in manifest.assignments:
        cluster_splits[assignment.learning_objective_cluster_id].add(
            assignment.split
        )
    _append_cross_split_errors(
        errors,
        cluster_splits,
        "split-manifest learning-objective cluster",
    )
    missing = sorted(set(expected_assignments) - set(observed_assignments))
    unknown = sorted(set(observed_assignments) - set(expected_assignments))
    changed = sorted(
        question_id
        for question_id in set(expected_assignments).intersection(
            observed_assignments
        )
        if expected_assignments[question_id] != observed_assignments[question_id]
    )
    if missing:
        errors.append(f"Split manifest is missing questions: {missing}")
    if unknown:
        errors.append(f"Split manifest has unknown questions: {unknown}")
    if changed:
        errors.append(f"Split assignments disagree with the dataset: {changed}")

    if errors:
        raise ControllerBenchmarkAuditError("\n".join(errors))
    return {
        "passed": True,
        "manifest_sha256": expected_hash,
        "assignment_count": len(manifest.assignments),
    }


def _append_cross_split_errors(
    errors: list[str],
    assignments: dict[object, set[str]],
    label: str,
) -> None:
    leaked = [key for key, splits in assignments.items() if len(splits) > 1]
    if leaked:
        errors.append(
            f"Cross-split leakage detected for {label}: {len(leaked)} collision(s)."
        )


def audit_graph_independence(
    dataset: ControllerBenchmarkDataset,
    manifest: GraphIndependenceManifest,
) -> dict[str, object]:
    errors: list[str] = []
    expected_hash = independence_payload_sha256(manifest)
    if manifest.manifest_sha256 != expected_hash:
        errors.append("Graph-independence manifest hash is not canonical.")
    if _as_utc(manifest.graph_frozen_at) > _as_utc(
        manifest.benchmark_authoring_started_at
    ):
        errors.append("Runtime graph must be frozen before benchmark authoring starts.")

    artifacts = {
        artifact.artifact_sha256: artifact
        for artifact in manifest.question_inputs
    }
    if manifest.runtime_graph_sha256 in artifacts:
        errors.append("Runtime graph cannot be a question-authoring input.")
    expected_frozen_inputs = {
        "corpus_snapshot": dataset.corpus_sha256,
        "concept_registry": dataset.concept_registry_sha256,
        "evidence_catalog": dataset.evidence_catalog_sha256,
        "annotation_protocol": dataset.annotation_protocol_sha256,
    }
    for artifact_type, expected_artifact_hash in expected_frozen_inputs.items():
        matching = [
            artifact
            for artifact in manifest.question_inputs
            if artifact.artifact_type == artifact_type
        ]
        if len(matching) != 1:
            errors.append(
                f"Question inputs need exactly one {artifact_type} artifact."
            )
        elif matching[0].artifact_sha256 != expected_artifact_hash:
            errors.append(
                f"Question input {artifact_type} does not match the frozen dataset source."
            )

    lineage_errors: set[str] = set()
    for artifact in manifest.question_inputs:
        if (
            artifact.artifact_type
            in {"curriculum_outline", "independent_evidence_bundle"}
            and not artifact.parent_artifact_sha256s
        ):
            lineage_errors.add(
                f"{artifact.artifact_sha256} has no verifiable frozen-source lineage."
            )
        for parent_hash in artifact.parent_artifact_sha256s:
            if parent_hash == manifest.runtime_graph_sha256:
                lineage_errors.add(
                    f"{artifact.artifact_sha256} is derived from the runtime graph."
                )
            elif parent_hash not in artifacts:
                lineage_errors.add(
                    f"{artifact.artifact_sha256} has unknown lineage parent {parent_hash}."
                )
    errors.extend(sorted(lineage_errors))

    lineage_cache: dict[str, set[str]] = {}

    def lineage(artifact_hash: str, stack: tuple[str, ...] = ()) -> set[str]:
        if artifact_hash in lineage_cache:
            return lineage_cache[artifact_hash]
        if artifact_hash in stack:
            errors.append(
                f"Question input lineage contains a cycle at {artifact_hash}."
            )
            return {artifact_hash}
        artifact = artifacts.get(artifact_hash)
        if artifact is None:
            return {artifact_hash}
        ancestors = {artifact_hash}
        for parent_hash in artifact.parent_artifact_sha256s:
            if parent_hash in artifacts:
                ancestors.update(lineage(parent_hash, (*stack, artifact_hash)))
        lineage_cache[artifact_hash] = ancestors
        return ancestors

    graph_reviewers = set(manifest.graph_reviewer_ids)
    question_authors = set(manifest.question_author_ids)
    benchmark_reviewers = set(manifest.benchmark_reviewer_ids)
    adjudicators = set(manifest.adjudicator_ids)
    role_pairs = (
        ("graph reviewers and question authors", graph_reviewers, question_authors),
        ("graph and benchmark reviewers", graph_reviewers, benchmark_reviewers),
        ("question authors and benchmark reviewers", question_authors, benchmark_reviewers),
        ("adjudicators and graph reviewers", adjudicators, graph_reviewers),
        ("adjudicators and question authors", adjudicators, question_authors),
        ("adjudicators and benchmark reviewers", adjudicators, benchmark_reviewers),
    )
    for label, left, right in role_pairs:
        overlap = left.intersection(right)
        if overlap:
            errors.append(f"Independent roles overlap ({label}): {sorted(overlap)}")

    allowed_inputs = set(artifacts)
    frozen_source_hashes = set(expected_frozen_inputs.values())
    for item in dataset.items:
        unknown_authors = set(item.authoring_provenance.author_ids) - question_authors
        if unknown_authors:
            errors.append(
                f"{item.question_id} has undeclared question authors: "
                f"{sorted(unknown_authors)}"
            )
        unknown_inputs = (
            set(item.authoring_provenance.source_artifact_sha256s) - allowed_inputs
        )
        if unknown_inputs:
            errors.append(
                f"{item.question_id} has undeclared authoring inputs: "
                f"{sorted(unknown_inputs)}"
            )
        for input_hash in set(
            item.authoring_provenance.source_artifact_sha256s
        ).intersection(allowed_inputs):
            if not lineage(input_hash).intersection(frozen_source_hashes):
                errors.append(
                    f"{item.question_id} input {input_hash} is not traceable "
                    "to a frozen benchmark source."
                )

    if errors:
        raise ControllerBenchmarkAuditError("\n".join(errors))
    return {
        "passed": True,
        "manifest_sha256": expected_hash,
        "runtime_graph_sha256": manifest.runtime_graph_sha256,
        "role_count": len(
            graph_reviewers | question_authors | benchmark_reviewers | adjudicators
        ),
    }


def audit_runtime_memory_binding(
    dataset: ControllerBenchmarkDataset,
    manifest: GraphIndependenceManifest,
    memory: ControllerMemorySnapshot,
) -> dict[str, object]:
    """Bind official evaluation ids to one canonical runtime memory snapshot."""

    errors: list[str] = []
    validated_memory = ControllerMemorySnapshot.model_validate(
        memory.model_dump(mode="python")
    )
    expected_memory_hash = controller_memory_payload_sha256(validated_memory)
    if validated_memory.memory_sha256 != expected_memory_hash:
        errors.append("Controller memory hash is not canonical.")
    if validated_memory.corpus_sha256 != dataset.corpus_sha256:
        errors.append("Controller memory corpus does not match the benchmark.")

    memory_concepts = {
        concept.concept_id: concept for concept in validated_memory.concepts
    }
    registry_ids = {
        concept.concept_id for concept in dataset.concept_registry
    }
    missing_concepts = sorted(registry_ids - set(memory_concepts))
    if missing_concepts:
        errors.append(
            "Controller memory is missing benchmark registry concepts: "
            f"{missing_concepts}."
        )

    memory_evidence = {
        evidence.evidence_id: evidence for evidence in validated_memory.evidence
    }
    for catalog_entry in dataset.evidence_catalog:
        memory_entry = memory_evidence.get(catalog_entry.evidence_id)
        if memory_entry is None:
            errors.append(
                "Controller memory is missing benchmark evidence: "
                f"{catalog_entry.evidence_id}."
            )
            continue
        if (
            memory_entry.claim_id != catalog_entry.claim_id
            or memory_entry.modality
            != _controller_memory_modality(catalog_entry.modality)
        ):
            errors.append(
                "Controller memory evidence metadata conflicts with the "
                f"catalog: {catalog_entry.evidence_id}."
            )
        owning_concept = memory_concepts.get(memory_entry.concept_id)
        if (
            owning_concept is None
            or catalog_entry.card_id not in owning_concept.source_card_ids
        ):
            errors.append(
                "Controller memory evidence is not bound to its catalog card: "
                f"{catalog_entry.evidence_id}."
            )

    expected_graph_hash = runtime_graph_payload_sha256(validated_memory)
    if manifest.runtime_graph_sha256 != expected_graph_hash:
        errors.append(
            "Graph-independence runtime graph does not match controller memory."
        )

    if errors:
        raise ControllerBenchmarkAuditError("\n".join(errors))
    return {
        "passed": True,
        "memory_sha256": expected_memory_hash,
        "runtime_graph_sha256": expected_graph_hash,
        "concept_count": len(memory_concepts),
        "evidence_count": len(memory_evidence),
    }


def _controller_memory_modality(benchmark_modality: str) -> str:
    return {
        "card_text": "document",
        "transcript": "transcript",
        "slide_text": "slide_text",
        "frame": "video_frame",
        "diagram": "slide_image",
    }[benchmark_modality]


def audit_double_review(
    dataset: ControllerBenchmarkDataset,
    review: ControllerBenchmarkReview,
    manifest: GraphIndependenceManifest,
) -> dict[str, object]:
    errors: list[str] = []
    if review.benchmark_sha256 != dataset.dataset_sha256:
        errors.append("Review benchmark hash does not match the dataset.")
    expected_hash = review_payload_sha256(review)
    if review.review_sha256 != expected_hash:
        errors.append("Benchmark review hash is not canonical.")

    items = {item.question_id: item for item in dataset.items}
    decisions_by_question: dict[str, list[object]] = defaultdict(list)
    allowed_reviewers = set(manifest.benchmark_reviewer_ids)
    for decision in review.decisions:
        item = items.get(decision.question_id)
        if item is None:
            errors.append(f"Review references unknown item: {decision.question_id}")
            continue
        decisions_by_question[decision.question_id].append(decision)
        if decision.reviewer_id not in allowed_reviewers:
            errors.append(
                f"Undeclared benchmark reviewer: {decision.reviewer_id}"
            )
        if decision.reviewer_id in item.authoring_provenance.author_ids:
            errors.append(
                f"Question author cannot review their own item: {item.question_id}"
            )

    adjudications = {
        adjudication.question_id: adjudication
        for adjudication in review.adjudications
    }
    allowed_adjudicators = set(manifest.adjudicator_ids)
    accepted_count = 0
    for item in dataset.items:
        if item.review_status == "pending":
            errors.append(f"{item.question_id} is still pending field review.")
        decisions = decisions_by_question.get(item.question_id, [])
        reviewer_ids = {decision.reviewer_id for decision in decisions}
        adjudication = adjudications.get(item.question_id)
        if item.review_status == "adjudicated" and adjudication is None:
            errors.append(
                f"{item.question_id} is marked adjudicated without a "
                "corresponding adjudication."
            )
        if adjudication is not None and item.review_status != "adjudicated":
            errors.append(
                f"{item.question_id} has adjudication but is not marked adjudicated."
            )
        if len(reviewer_ids) < 2:
            errors.append(
                f"{item.question_id} has fewer than two independent reviewers."
            )
            continue
        decision_values = {
            decision.overall_decision for decision in decisions
        }
        if decision_values == {"accept"}:
            if adjudication is not None:
                errors.append(
                    f"{item.question_id} has adjudication without a real disagreement."
                )
            else:
                accepted_count += 1
            continue
        if decision_values == {"reject"}:
            errors.append(
                f"{item.question_id} was unanimously rejected and cannot be adjudicated "
                "into acceptance."
            )
            if adjudication is not None:
                errors.append(
                    f"{item.question_id} has adjudication without a real disagreement."
                )
            continue
        if adjudication is None:
            errors.append(
                f"{item.question_id} has review disagreement without adjudication."
            )
            continue
        if adjudication.adjudicator_id not in allowed_adjudicators:
            errors.append(
                f"Undeclared adjudicator: {adjudication.adjudicator_id}"
            )
        if adjudication.adjudicator_id in reviewer_ids:
            errors.append(
                f"Adjudicator must be independent for {item.question_id}."
            )
        if adjudication.final_decision != "accept":
            errors.append(f"{item.question_id} was not finally accepted.")
        else:
            accepted_count += 1

    unknown_adjudications = set(adjudications) - set(items)
    if unknown_adjudications:
        errors.append(
            f"Adjudications reference unknown items: {sorted(unknown_adjudications)}"
        )

    if errors:
        raise ControllerBenchmarkAuditError("\n".join(errors))
    return {
        "passed": True,
        "review_sha256": expected_hash,
        "question_count": len(items),
        "accepted_count": accepted_count,
        "decision_count": len(review.decisions),
        "adjudication_count": len(review.adjudications),
    }


def audit_seal(
    dataset: ControllerBenchmarkDataset,
    review: ControllerBenchmarkReview,
    manifest: GraphIndependenceManifest,
    split_manifest: ControllerBenchmarkSplitManifest,
    seal: ControllerBenchmarkSeal,
) -> dict[str, object]:
    # Run every upstream audit so a syntactically valid seal cannot bypass a
    # changed dataset, leakage, role conflict, or unresolved review.
    audit_dataset(dataset)
    audit_graph_independence(dataset, manifest)
    audit_double_review(dataset, review, manifest)
    audit_split_manifest(dataset, split_manifest)

    errors: list[str] = []
    if dataset.lifecycle_status != "sealed":
        errors.append("Dataset lifecycle must be sealed.")
    if review.review_status != "human_verified":
        errors.append("Review must be human_verified before sealing.")
    expected_seal_hash = seal_payload_sha256(seal)
    if seal.seal_sha256 != expected_seal_hash:
        errors.append("Seal hash is not canonical.")
    expected_bindings = {
        "benchmark_sha256": dataset.dataset_sha256,
        "review_sha256": review.review_sha256,
        "independence_manifest_sha256": manifest.manifest_sha256,
        "split_manifest_sha256": split_manifest.manifest_sha256,
    }
    for field_name, expected in expected_bindings.items():
        if getattr(seal, field_name) != expected:
            errors.append(f"Seal {field_name} does not match its frozen artifact.")
    if _as_utc(split_manifest.created_at) >= _as_utc(
        manifest.benchmark_authoring_started_at
    ):
        errors.append(
            "Split manifest must predate benchmark authoring and annotations."
        )
    if _as_utc(split_manifest.created_at) >= _as_utc(review.created_at):
        errors.append(
            "Split manifest must predate benchmark review annotations."
        )
    latest_input_time = max(
        _as_utc(dataset.created_at),
        _as_utc(review.created_at),
        _as_utc(manifest.created_at),
        _as_utc(split_manifest.created_at),
        _as_utc(manifest.graph_frozen_at),
        _as_utc(manifest.benchmark_authoring_started_at),
    )
    if _as_utc(seal.sealed_at) < latest_input_time:
        errors.append("Seal timestamp predates an artifact it binds.")

    if errors:
        raise ControllerBenchmarkAuditError("\n".join(errors))
    return {
        "passed": True,
        "seal_sha256": expected_seal_hash,
        "benchmark_sha256": dataset.dataset_sha256,
        "review_sha256": review.review_sha256,
        "independence_manifest_sha256": manifest.manifest_sha256,
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ControllerBenchmarkAuditError("Audit timestamps must be timezone-aware.")
    return value.astimezone(UTC)
