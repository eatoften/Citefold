from __future__ import annotations

import pytest

from rag_lab.controller_memory import (
    FrozenControllerMemory,
    audit_controller_memory_snapshot,
    build_card_proxy_memory_snapshot,
)
from rag_lab.controller_schemas import (
    ExpandTypedNeighborAction,
    SearchConceptAction,
    SearchEvidenceAction,
    action_fingerprint,
    controller_memory_payload_sha256,
)
from rag_lab.io import sha256_value
from rag_lab.reviews import review_payload_sha256
from rag_lab.schemas import (
    RagAnnotationReview,
    RagCorpusSnapshot,
    RagEmbeddingRecord,
    RagEmbeddingSnapshot,
    RagGraphDecision,
)


class FakeEmbedder:
    def embed_texts(self, texts, **_):
        return [
            [1.0, 0.0] if "gradient" in text.lower() else [0.0, 1.0]
            for text in texts
        ]


def _artifacts():
    corpus = RagCorpusSnapshot(
        snapshot_id="snapshot",
        course_id="course",
        source_database_sha256="a" * 64,
        snapshot_sha256="0" * 64,
        cards=[
            {
                "card_id": "concept-a",
                "job_id": "job-a",
                "lecture_name": "lecture-a.mp4",
                "title": "Gradient estimation",
                "summary": "A gradient estimate drives optimization.",
                "document_text": "gradient estimate variance",
                "content_status": "reviewed",
                "source_start_seconds": 1,
                "source_end_seconds": 5,
                "claims": [
                    {
                        "claim_id": "claim-a",
                        "text": "A minibatch estimates a gradient.",
                        "evidence": [
                            {
                                "evidence_id": "evidence-a",
                                "quote": "The minibatch gradient is an estimate.",
                                "start_seconds": 1,
                                "end_seconds": 3,
                            }
                        ],
                    }
                ],
            },
            {
                "card_id": "concept-b",
                "job_id": "job-b",
                "lecture_name": "lecture-b.mp4",
                "title": "Optimization",
                "summary": "Optimization consumes a gradient estimate.",
                "document_text": "optimization update learning rate",
                "content_status": "reviewed",
                "source_start_seconds": 6,
                "source_end_seconds": 10,
                "claims": [
                    {
                        "claim_id": "claim-b",
                        "text": "The learning rate scales an update.",
                        "evidence": [
                            {
                                "evidence_id": "evidence-b",
                                "quote": "Scale the optimization update.",
                                "start_seconds": 6,
                                "end_seconds": 8,
                            }
                        ],
                    }
                ],
            },
            {
                "card_id": "concept-c",
                "job_id": "job-c",
                "lecture_name": "lecture-c.mp4",
                "title": "Images",
                "summary": "Images contain pixels.",
                "document_text": "image pixels",
                "content_status": "reviewed",
                "source_start_seconds": 11,
                "source_end_seconds": 15,
                "claims": [
                    {
                        "claim_id": "claim-c",
                        "text": "Images contain pixels.",
                        "evidence": [
                            {
                                "evidence_id": "evidence-c",
                                "quote": "Pixels form an image.",
                                "start_seconds": 11,
                                "end_seconds": 13,
                            }
                        ],
                    }
                ],
            },
        ],
    )
    corpus.snapshot_sha256 = sha256_value(
        {
            "course_id": corpus.course_id,
            "cards": [
                card.model_dump(mode="json") for card in corpus.cards
            ],
            "relations": [],
        }
    )
    review = RagAnnotationReview(
        review_id="review",
        corpus_sha256=corpus.snapshot_sha256,
        review_status="candidate",
        graph_decisions=[
            RagGraphDecision(
                source_card_id="concept-a",
                target_card_id="concept-b",
                accepted=True,
                relation_type="prerequisite",
                reviewer_id="reviewer",
                review_notes="Gradient estimation precedes optimization.",
            ),
            RagGraphDecision(
                source_card_id="concept-a",
                target_card_id="concept-c",
                accepted=False,
                reviewer_id="reviewer",
                review_notes="This proposed relation is not supported.",
            ),
        ],
        review_sha256="0" * 64,
    )
    review.review_sha256 = review_payload_sha256(review)
    records = [
        RagEmbeddingRecord(card_id="concept-b", vector=[0.0, 1.0]),
        RagEmbeddingRecord(card_id="concept-a", vector=[1.0, 0.0]),
        RagEmbeddingRecord(card_id="concept-c", vector=[0.2, 0.8]),
    ]
    embeddings = RagEmbeddingSnapshot(
        corpus_sha256=corpus.snapshot_sha256,
        model="fake",
        dimension=2,
        normalized=True,
        indexing_milliseconds=1,
        records=records,
        embeddings_sha256=sha256_value(
            [record.model_dump(mode="json") for record in records]
        ),
    )
    return corpus, review, embeddings


def _memory() -> FrozenControllerMemory:
    corpus, review, embeddings = _artifacts()
    return FrozenControllerMemory(
        corpus,
        review,
        embeddings,
        FakeEmbedder(),
        memory_id="memory",
    )


def test_card_proxy_builder_is_canonical_and_uses_only_accepted_edges() -> None:
    corpus, review, _ = _artifacts()

    memory = build_card_proxy_memory_snapshot(
        corpus,
        review,
        memory_id="memory",
    )

    assert [item.concept_id for item in memory.concepts] == [
        "concept-a",
        "concept-b",
        "concept-c",
    ]
    assert [item.evidence_id for item in memory.evidence] == [
        "evidence-a",
        "evidence-b",
        "evidence-c",
    ]
    assert len(memory.relations) == 1
    assert memory.relations[0].relation_type == "prerequisite"
    assert memory.relations[0].review_status == "candidate"
    assert memory.memory_sha256 == controller_memory_payload_sha256(memory)
    assert audit_controller_memory_snapshot(memory)["passed"] is True


def test_memory_hash_binds_bm25_ranking_parameters() -> None:
    corpus, review, embeddings = _artifacts()
    default_memory = FrozenControllerMemory(
        corpus,
        review,
        embeddings,
        FakeEmbedder(),
        memory_id="memory",
    )
    changed_memory = FrozenControllerMemory(
        corpus,
        review,
        embeddings,
        FakeEmbedder(),
        memory_id="memory",
        evidence_bm25_k1=1.8,
        evidence_bm25_b=0.4,
    )

    assert (
        default_memory.snapshot.memory_sha256
        != changed_memory.snapshot.memory_sha256
    )
    assert (
        changed_memory.snapshot.retrieval_config.evidence_bm25_k1
        == 1.8
    )
    assert (
        changed_memory.snapshot.retrieval_config.evidence_bm25_b
        == 0.4
    )


def test_builder_rejects_noncanonical_corpus_and_review() -> None:
    corpus, review, _ = _artifacts()
    corpus.snapshot_sha256 = "f" * 64
    with pytest.raises(ValueError, match="Corpus snapshot hash"):
        build_card_proxy_memory_snapshot(corpus, review)

    corpus, review, _ = _artifacts()
    review.review_sha256 = "f" * 64
    with pytest.raises(ValueError, match="Review hash"):
        build_card_proxy_memory_snapshot(corpus, review)


def test_frozen_memory_rejects_invalid_embedding_artifacts() -> None:
    corpus, review, embeddings = _artifacts()
    bad_hash = embeddings.model_copy(
        update={"embeddings_sha256": "f" * 64}
    )
    with pytest.raises(ValueError, match="Embedding snapshot hash"):
        FrozenControllerMemory(
            corpus,
            review,
            bad_hash,
            FakeEmbedder(),
        )

    bad_ids = embeddings.model_copy(
        update={
            "records": embeddings.records[:-1],
            "embeddings_sha256": sha256_value(
                [
                    record.model_dump(mode="json")
                    for record in embeddings.records[:-1]
                ]
            ),
        }
    )
    with pytest.raises(ValueError, match="card ids"):
        FrozenControllerMemory(
            corpus,
            review,
            bad_ids,
            FakeEmbedder(),
        )

    bad_dimension_records = [
        *embeddings.records[:-1],
        RagEmbeddingRecord(card_id="concept-c", vector=[0.0]),
    ]
    bad_dimension = embeddings.model_copy(
        update={
            "records": bad_dimension_records,
            "embeddings_sha256": sha256_value(
                [
                    record.model_dump(mode="json")
                    for record in bad_dimension_records
                ]
            ),
        }
    )
    with pytest.raises(ValueError, match="declared dimension"):
        FrozenControllerMemory(
            corpus,
            review,
            bad_dimension,
            FakeEmbedder(),
        )


def test_concept_search_is_dense_deterministic_and_tracks_novelty() -> None:
    memory = _memory()
    action = SearchConceptAction(
        need_id="need",
        query="gradient",
        top_k=2,
    )

    observation = memory.search_concepts(
        action,
        seen_concept_ids={"concept-a"},
    )

    assert [item.concept_id for item in observation.hits] == [
        "concept-a",
        "concept-c",
    ]
    assert observation.novel_concept_ids == ["concept-c"]
    assert observation.duplicate_ids == ["concept-a"]
    assert observation.action_fingerprint == action_fingerprint(action)
    assert observation.cost.steps == 1
    assert observation.cost.retrieval_calls == 1
    assert observation.cost.concept_searches == 1
    assert observation.cost.unique_concepts == 1
    assert observation.cost.context_characters == len(
        "gradient estimate variance"
    ) + len("image pixels")

    excluded = memory.search_concepts(
        action.model_copy(
            update={"exclude_concept_ids": ["concept-a"]}
        ),
        seen_concept_ids=set(),
    )
    assert excluded.hits[0].concept_id == "concept-c"
    assert [item.rank for item in excluded.hits] == [1, 2]


def test_evidence_search_is_scoped_bm25_and_tracks_duplicates() -> None:
    memory = _memory()
    action = SearchEvidenceAction(
        need_ids=["need"],
        query="minibatch gradient estimate",
        scope_concept_ids=["concept-a", "concept-b"],
        top_k=2,
    )

    observation = memory.search_evidence(
        action,
        seen_evidence_ids={"evidence-a"},
    )

    assert [item.evidence_id for item in observation.hits] == [
        "evidence-a",
        "evidence-b",
    ]
    assert observation.novel_evidence_ids == ["evidence-b"]
    assert observation.duplicate_ids == ["evidence-a"]
    assert all(
        item.retrieval_source == "bm25_evidence"
        for item in observation.hits
    )
    expected_characters = sum(
        len(
            memory.evidence_node(evidence_id).claim_text
            + "\n"
            + memory.evidence_node(evidence_id).text
        )
        for evidence_id in ("evidence-a", "evidence-b")
    )
    assert observation.cost.context_characters == expected_characters
    assert observation.cost.evidence_searches == 1
    assert observation.cost.unique_evidence == 1

    scoped = memory.search_evidence(
        action.model_copy(
            update={
                "scope_concept_ids": ["concept-b"],
                "top_k": 3,
            }
        ),
        seen_evidence_ids=set(),
    )
    assert [item.evidence_id for item in scoped.hits] == ["evidence-b"]


def test_typed_expansion_preserves_direction_type_and_novelty() -> None:
    memory = _memory()
    outgoing = ExpandTypedNeighborAction(
        need_id="need",
        anchor_concept_ids=["concept-a"],
        relation_types=["prerequisite"],
        direction="outgoing",
        max_neighbors_per_anchor=2,
    )

    observation = memory.expand_typed_neighbors(
        outgoing,
        seen_concept_ids={"concept-a"},
        traversed_relation_ids=set(),
    )

    assert len(observation.hits) == 1
    relation = observation.hits[0]
    assert relation.source_concept_id == "concept-a"
    assert relation.target_concept_id == "concept-b"
    assert relation.traversal_direction == "outgoing"
    assert observation.novel_concept_ids == ["concept-b"]
    assert observation.novel_relation_ids == [relation.relation_id]
    assert observation.cost.graph_expansions == 1
    assert observation.cost.unique_concepts == 1
    assert observation.cost.context_characters == len(
        "optimization update learning rate"
    )

    no_incoming = memory.expand_typed_neighbors(
        outgoing.model_copy(update={"direction": "incoming"}),
        seen_concept_ids=set(),
        traversed_relation_ids=set(),
    )
    assert no_incoming.hits == []

    incoming = memory.expand_typed_neighbors(
        outgoing.model_copy(
            update={
                "anchor_concept_ids": ["concept-b"],
                "direction": "incoming",
            }
        ),
        seen_concept_ids={"concept-a"},
        traversed_relation_ids={relation.relation_id},
    )
    assert incoming.hits[0].traversal_direction == "incoming"
    assert incoming.novel_concept_ids == []
    assert incoming.novel_relation_ids == []
    assert incoming.duplicate_ids == [
        relation.relation_id,
        "concept-a",
    ]

    wrong_type = memory.expand_typed_neighbors(
        outgoing.model_copy(update={"relation_types": ["related"]}),
        seen_concept_ids=set(),
        traversed_relation_ids=set(),
    )
    assert wrong_type.hits == []


def test_memory_actions_reject_unknown_scope_or_anchor_ids() -> None:
    memory = _memory()
    with pytest.raises(ValueError, match="Unknown graph anchor"):
        memory.expand_typed_neighbors(
            ExpandTypedNeighborAction(
                need_id="need",
                anchor_concept_ids=["missing"],
                relation_types=["related"],
                direction="both",
                max_neighbors_per_anchor=1,
            ),
            seen_concept_ids=set(),
            traversed_relation_ids=set(),
        )

    with pytest.raises(ValueError, match="Unknown evidence-search"):
        memory.search_evidence(
            SearchEvidenceAction(
                need_ids=["need"],
                query="query",
                scope_concept_ids=["missing"],
                top_k=1,
            ),
            seen_evidence_ids=set(),
        )


def test_memory_actions_truncate_before_unique_or_context_budget_overrun() -> None:
    memory = _memory()
    concept_action = SearchConceptAction(
        need_id="need",
        query="gradient",
        top_k=3,
    )
    limited_concepts = memory.search_concepts(
        concept_action,
        seen_concept_ids=set(),
        max_novel_concepts=1,
        context_character_limit=10_000,
    )
    assert len(limited_concepts.hits) == 1
    assert limited_concepts.cost.unique_concepts == 1

    no_context = memory.search_concepts(
        concept_action,
        seen_concept_ids=set(),
        max_novel_concepts=3,
        context_character_limit=1,
    )
    assert no_context.hits == []
    assert no_context.cost.context_characters == 0

    evidence_action = SearchEvidenceAction(
        need_ids=["need"],
        query="gradient",
        scope_concept_ids=[],
        top_k=3,
    )
    limited_evidence = memory.search_evidence(
        evidence_action,
        seen_evidence_ids=set(),
        max_novel_evidence=1,
        context_character_limit=10_000,
    )
    assert len(limited_evidence.hits) == 1
    assert limited_evidence.cost.unique_evidence == 1
