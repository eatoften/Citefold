from __future__ import annotations

from datetime import datetime, timezone
import hashlib

import pytest

from app.chat_graph import build_graph_chat_context
from app.concept_graph_path import (
    GraphPathConceptNotFoundError,
    GraphPathIntegrityError,
    GraphPathLimitError,
    learning_path,
    local_graph,
    relationship_trace,
)
from app.concept_graph_publication import (
    GraphPublicationCounts,
    GraphVersionMetadata,
    PublishedConcept,
    PublishedEvidence,
    PublishedGraphSnapshot,
    PublishedRelation,
    PublishedRelationEvidence,
)


NOW = datetime(2026, 8, 9, tzinfo=timezone.utc)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _evidence(owner: str, *, relation: bool = False):
    fields = {
        "evidence_id": f"evidence-{owner}",
        "source_id": "asset:path-fixture",
        "chunk_id": "source_unit:path-fixture-page-1",
        "chunk_text_hash": _hash("Path fixture source text."),
        "projection_generation_id": "projection-path-fixture",
        "source_title": "path-fixture.pdf",
        "source_type": "pdf",
        "quote": "Path fixture source text.",
        "locator": {
            "schema_version": 1,
            "kind": "pdf_page",
            "asset_id": "path-fixture",
            "page_number": 1,
        },
        "ordinal": 0,
        "created_at": NOW,
    }
    if relation:
        return PublishedRelationEvidence(
            **fields,
            support_role="relation_assertion",
        )
    return PublishedEvidence(**fields)


def _concept(concept_id: str) -> PublishedConcept:
    return PublishedConcept(
        concept_id=concept_id,
        concept_revision=2,
        preferred_name=f"Concept {concept_id.upper()}",
        short_definition=f"Definition for {concept_id}.",
        identity_status="active",
        review_status="accepted",
        validity_status="current",
        proposal_origin="human",
        review_operation_id=f"review-{concept_id}",
        review_operation_request_hash=_hash(f"review-{concept_id}"),
        review_actor="reviewer@example.test",
        review_reason="Accepted for the deterministic path fixture.",
        reviewed_at=NOW,
        review_revision=1,
        revision_created_at=NOW,
        revision_updated_at=NOW,
        aggregate_hash=_hash(f"concept-{concept_id}"),
        ordinal=ord(concept_id) - ord("a"),
        evidence=[_evidence(concept_id)],
    )


def _relation(
    relation_id: str,
    source: str,
    target: str,
    relation_type: str = "prerequisite",
) -> PublishedRelation:
    return PublishedRelation(
        relation_id=relation_id,
        relation_revision=2,
        source_concept_id=source,
        source_concept_revision=2,
        target_concept_id=target,
        target_concept_revision=2,
        relation_type=relation_type,
        support_basis="source_asserted",
        rationale=f"{source} connects to {target}.",
        review_status="accepted",
        validity_status="current",
        proposal_origin="human",
        review_operation_id=f"review-{relation_id}",
        review_operation_request_hash=_hash(f"review-{relation_id}"),
        review_actor="reviewer@example.test",
        review_reason="Accepted for the deterministic path fixture.",
        reviewed_at=NOW,
        review_revision=1,
        binding_created_at=NOW,
        revision_created_at=NOW,
        revision_updated_at=NOW,
        aggregate_hash=_hash(f"relation-{relation_id}"),
        ordinal=0,
        evidence=[_evidence(relation_id, relation=True)],
    )


def _snapshot(
    *,
    extra_relations: list[PublishedRelation] | None = None,
) -> PublishedGraphSnapshot:
    concepts = [_concept(item) for item in "abcde"]
    relations = [
        _relation("r-ab", "a", "b"),
        _relation("r-ac", "a", "c"),
        _relation("r-bd", "b", "d"),
        _relation("r-cd", "c", "d"),
        _relation("r-de", "d", "e", "related"),
        *(extra_relations or []),
    ]
    counts = GraphPublicationCounts(
        concepts=len(concepts),
        relations=len(relations),
        concept_aliases=0,
        concept_evidence=len(concepts),
        relation_evidence=len(relations),
    )
    return PublishedGraphSnapshot(
        version=GraphVersionMetadata(
            course_id="course-path-fixture",
            version_number=1,
            draft_manifest_hash=_hash("draft"),
            content_hash=_hash("content"),
            counts=counts,
            published_by="publisher@example.test",
            publication_reason="Publish deterministic path fixture.",
            published_at=NOW,
            is_active_version=True,
            source_authority_current=True,
            source_authority_issue_count=0,
        ),
        concepts=concepts,
        relations=relations,
    )


def test_trace_is_shortest_stable_and_respects_direction_and_symmetry() -> None:
    snapshot = _snapshot()

    first = relationship_trace(
        snapshot,
        source_concept_id="a",
        target_concept_id="d",
    )
    repeated = relationship_trace(
        snapshot,
        source_concept_id="a",
        target_concept_id="d",
    )
    incoming = relationship_trace(
        snapshot,
        source_concept_id="d",
        target_concept_id="a",
        relation_types=["prerequisite"],
        direction_mode="incoming",
    )
    symmetric = relationship_trace(
        snapshot,
        source_concept_id="e",
        target_concept_id="d",
        relation_types=["related"],
    )

    assert first.status == "found"
    assert [item.concept_id for item in first.nodes] == ["a", "b", "d"]
    assert first.result_hash == repeated.result_hash
    assert all(
        step.traversed_against_relation_direction for step in incoming.steps
    )
    assert symmetric.status == "found"
    assert symmetric.steps[0].traversed_against_relation_direction is False


def test_chat_graph_context_uses_one_exact_fully_source_scoped_trace() -> None:
    snapshot = _snapshot()

    context = build_graph_chat_context(
        snapshot,
        "How are Concept A and Concept D connected?",
        selected_source_ids=["asset:path-fixture"],
    )

    assert context is not None
    assert context.strategy == "relationship_trace"
    assert context.graph_version == 1
    assert [item.concept_id for item in context.concepts] == [
        "a",
        "b",
        "d",
    ]
    assert [item.relation_id for item in context.steps] == [
        "r-ab",
        "r-bd",
    ]
    assert all(
        item.support_basis == "source_asserted" for item in context.steps
    )
    reverse_context = build_graph_chat_context(
        snapshot,
        "How are Concept D and Concept A connected?",
        selected_source_ids=["asset:path-fixture"],
    )
    assert reverse_context is not None
    assert [item.concept_id for item in reverse_context.concepts] == [
        "d",
        "b",
        "a",
    ]
    assert all(
        item.traversed_against_relation_direction
        for item in reverse_context.steps
    )
    assert build_graph_chat_context(
        snapshot,
        "How are Concept A and Concept D connected?",
        selected_source_ids=["asset:not-selected"],
    ) is None
    assert build_graph_chat_context(
        snapshot,
        "Explain something else.",
        selected_source_ids=["asset:path-fixture"],
    ) is None
    assert build_graph_chat_context(
        snapshot,
        "Compare Concept A, Concept B, and Concept D.",
        selected_source_ids=["asset:path-fixture"],
    ) is None

    private_evidence = _evidence("private").model_copy(
        update={
            "evidence_id": "evidence-private",
            "source_id": "asset:not-selected",
        }
    )
    mixed_concept = snapshot.concepts[0].model_copy(
        update={
            "evidence": [
                *snapshot.concepts[0].evidence,
                private_evidence,
            ]
        }
    )
    mixed_snapshot = snapshot.model_copy(
        update={"concepts": [mixed_concept, *snapshot.concepts[1:]]}
    )
    assert build_graph_chat_context(
        mixed_snapshot,
        "How are Concept A and Concept D connected?",
        selected_source_ids=["asset:path-fixture"],
    ) is None


def test_trace_distinguishes_unreachable_from_bounded_search() -> None:
    snapshot = _snapshot()

    bounded = relationship_trace(
        snapshot,
        source_concept_id="a",
        target_concept_id="d",
        max_hops=1,
    )
    unreachable = relationship_trace(
        snapshot,
        source_concept_id="a",
        target_concept_id="e",
        relation_types=["related"],
    )

    assert bounded.status == "limits_reached"
    assert bounded.truncated_by_max_hops is True
    assert bounded.nodes == []
    assert unreachable.status == "unreachable"
    assert unreachable.truncated_by_max_hops is False
    assert unreachable.truncated_by_max_nodes is False


def test_local_graph_and_learning_path_are_deterministic_and_evidence_bearing() -> None:
    snapshot = _snapshot()

    local = local_graph(
        snapshot,
        root_concept_id="a",
        relation_types=["prerequisite"],
        max_hops=1,
        max_nodes=2,
    )
    learning = learning_path(snapshot, target_concept_id="d")

    assert [(item.concept.concept_id, item.distance) for item in local.nodes] == [
        ("a", 0),
        ("b", 1),
    ]
    assert local.truncated_by_max_nodes is True
    assert [item.relation_id for item in local.relations] == ["r-ab"]
    assert learning.linearization == ["a", "b", "c", "d"]
    assert [item.concept_ids for item in learning.layers] == [
        ["a"],
        ["b", "c"],
        ["d"],
    ]
    assert all(item.evidence for item in learning.nodes)
    assert all(item.evidence for item in learning.relations)


def test_learning_path_fails_closed_for_limits_cycles_and_unknown_concepts() -> None:
    snapshot = _snapshot()
    with pytest.raises(GraphPathLimitError):
        learning_path(snapshot, target_concept_id="d", max_nodes=3)
    with pytest.raises(GraphPathConceptNotFoundError):
        local_graph(snapshot, root_concept_id="missing")

    cyclic = _snapshot(extra_relations=[_relation("r-da", "d", "a")])
    with pytest.raises(GraphPathIntegrityError):
        learning_path(cyclic, target_concept_id="d")

    duplicate = _snapshot(
        extra_relations=[_relation("r-ab-duplicate", "a", "b")]
    )
    with pytest.raises(GraphPathIntegrityError):
        local_graph(duplicate, root_concept_id="a")

    missing_endpoint = _snapshot(
        extra_relations=[_relation("r-az", "a", "z")]
    )
    with pytest.raises(GraphPathIntegrityError):
        relationship_trace(
            missing_endpoint,
            source_concept_id="a",
            target_concept_id="d",
        )
