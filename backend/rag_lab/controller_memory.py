from __future__ import annotations

import math
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from app.embedding import TextEmbedder

from .controller_schemas import (
    ConceptSearchObservation,
    ControllerConceptHit,
    ControllerConceptNode,
    ControllerCost,
    ControllerEvidenceHit,
    ControllerEvidenceNode,
    ControllerMemorySnapshot,
    ControllerRelationEdge,
    ControllerRelationHit,
    ControllerRetrievalConfig,
    EvidenceSearchObservation,
    ExpandTypedNeighborAction,
    GraphExpansionObservation,
    SearchConceptAction,
    SearchEvidenceAction,
    action_fingerprint,
    controller_memory_payload_sha256,
)
from .io import sha256_value
from .retrievers import rank_dense, tokenize_for_retrieval
from .reviews import audit_annotation_review
from .schemas import (
    RagAnnotationReview,
    RagCorpusSnapshot,
    RagEmbeddingSnapshot,
)


def build_card_proxy_memory_snapshot(
    corpus: RagCorpusSnapshot,
    review: RagAnnotationReview,
    *,
    memory_id: str | None = None,
    evidence_bm25_k1: float = 1.2,
    evidence_bm25_b: float = 0.75,
) -> ControllerMemorySnapshot:
    """Build a deterministic card-proxy memory from frozen RAG artifacts."""

    _audit_corpus_snapshot(corpus)
    audit_annotation_review(review, corpus)

    concepts = sorted(
        (
            ControllerConceptNode(
                concept_id=card.card_id,
                title=card.title,
                summary=card.summary,
                document_text=card.document_text,
                source_card_ids=[card.card_id],
            )
            for card in corpus.cards
        ),
        key=lambda item: item.concept_id,
    )
    evidence = sorted(
        (
            ControllerEvidenceNode(
                evidence_id=item.evidence_id,
                concept_id=card.card_id,
                claim_id=claim.claim_id,
                claim_text=claim.text,
                text=item.quote,
                modality="transcript",
                source_job_id=card.job_id,
                source_name=card.lecture_name,
                locator={
                    "start_seconds": item.start_seconds,
                    "end_seconds": item.end_seconds,
                },
                extraction_method="rag_corpus_snapshot",
                extraction_version=corpus.schema_version,
            )
            for card in corpus.cards
            for claim in card.claims
            for item in claim.evidence
        ),
        key=lambda item: item.evidence_id,
    )
    accepted = sorted(
        (
            decision
            for decision in review.graph_decisions
            if decision.accepted
        ),
        key=lambda item: (
            item.source_card_id,
            item.target_card_id,
            item.relation_type or "",
        ),
    )
    relations = [
        ControllerRelationEdge(
            relation_id=_reviewed_relation_id(
                decision.source_card_id,
                decision.target_card_id,
                decision.relation_type or "",
            ),
            source_concept_id=decision.source_card_id,
            target_concept_id=decision.target_card_id,
            relation_type=decision.relation_type,
            score=1.0,
            review_status=review.review_status,
        )
        for decision in accepted
    ]
    resolved_memory_id = memory_id or (
        f"card-proxy-{corpus.snapshot_sha256[:12]}-"
        f"{review.review_sha256[:12]}"
    )
    memory = ControllerMemorySnapshot(
        memory_id=resolved_memory_id,
        concept_granularity="card_proxy",
        corpus_sha256=corpus.snapshot_sha256,
        review_sha256=review.review_sha256,
        retrieval_config=ControllerRetrievalConfig(
            evidence_bm25_k1=evidence_bm25_k1,
            evidence_bm25_b=evidence_bm25_b,
        ),
        concepts=concepts,
        evidence=evidence,
        relations=relations,
        memory_sha256="0" * 64,
    )
    memory.memory_sha256 = controller_memory_payload_sha256(memory)
    audit_controller_memory_snapshot(memory)
    return memory


def audit_controller_memory_snapshot(
    memory: ControllerMemorySnapshot,
) -> dict[str, object]:
    expected_sha256 = controller_memory_payload_sha256(memory)
    if memory.memory_sha256 != expected_sha256:
        raise ValueError("Controller memory hash is not canonical.")
    return {
        "passed": True,
        "memory_sha256": expected_sha256,
        "concept_count": len(memory.concepts),
        "evidence_count": len(memory.evidence),
        "relation_count": len(memory.relations),
    }


class FrozenControllerMemory:
    """Read-only retrieval environment backed by fully pinned artifacts."""

    def __init__(
        self,
        corpus: RagCorpusSnapshot,
        review: RagAnnotationReview,
        embedding_snapshot: RagEmbeddingSnapshot,
        query_embedder: TextEmbedder,
        *,
        memory_id: str | None = None,
        query_encoder_sha256: str | None = None,
        evidence_bm25_k1: float = 1.2,
        evidence_bm25_b: float = 0.75,
    ) -> None:
        self.snapshot = build_card_proxy_memory_snapshot(
            corpus,
            review,
            memory_id=memory_id,
            evidence_bm25_k1=evidence_bm25_k1,
            evidence_bm25_b=evidence_bm25_b,
        )
        audit_controller_memory_snapshot(self.snapshot)
        self._validate_embedding_snapshot(embedding_snapshot)
        self.embedding_snapshot = embedding_snapshot
        self._query_embedder = query_embedder
        self.query_encoder_sha256 = query_encoder_sha256
        self._concepts = {
            item.concept_id: item for item in self.snapshot.concepts
        }
        self._evidence = {
            item.evidence_id: item for item in self.snapshot.evidence
        }
        self._relations = {
            item.relation_id: item for item in self.snapshot.relations
        }
        self._concept_vectors = {
            item.card_id: tuple(item.vector)
            for item in embedding_snapshot.records
        }
        self._evidence_index = _TextBm25Index.build(
            {
                item.evidence_id: _evidence_visible_text(item)
                for item in self.snapshot.evidence
            },
            k1=evidence_bm25_k1,
            b=evidence_bm25_b,
        )
        outgoing: defaultdict[str, list[ControllerRelationEdge]] = defaultdict(
            list
        )
        incoming: defaultdict[str, list[ControllerRelationEdge]] = defaultdict(
            list
        )
        for relation in self.snapshot.relations:
            outgoing[relation.source_concept_id].append(relation)
            incoming[relation.target_concept_id].append(relation)
        self._outgoing = {
            concept_id: tuple(_sort_relations(relations))
            for concept_id, relations in outgoing.items()
        }
        self._incoming = {
            concept_id: tuple(_sort_relations(relations))
            for concept_id, relations in incoming.items()
        }

    @property
    def memory_sha256(self) -> str:
        return self.snapshot.memory_sha256

    def search_concepts(
        self,
        action: SearchConceptAction,
        *,
        seen_concept_ids: set[str],
        max_novel_concepts: int | None = None,
        context_character_limit: int | None = None,
    ) -> ConceptSearchObservation:
        self._require_known(
            action.exclude_concept_ids,
            self._concepts,
            label="excluded concept",
        )
        self._require_known(
            seen_concept_ids,
            self._concepts,
            label="seen concept",
        )
        started = time.perf_counter()
        query_vectors = self._query_embedder.embed_texts([action.query])
        if len(query_vectors) != 1:
            raise ValueError(
                "The query embedder must return exactly one query vector."
            )
        query_vector = query_vectors[0]
        if len(query_vector) != self.embedding_snapshot.dimension:
            raise ValueError(
                "Query embedding dimension does not match the frozen index."
            )
        excluded = set(action.exclude_concept_ids)
        ranked = rank_dense(query_vector, self._concept_vectors)
        selected = []
        selected_novel_count = 0
        selected_characters = 0
        for item in ranked:
            if item.card_id in excluded:
                continue
            is_novel = item.card_id not in seen_concept_ids
            if (
                is_novel
                and max_novel_concepts is not None
                and selected_novel_count >= max_novel_concepts
            ):
                continue
            visible_characters = _concept_visible_characters(
                self._concepts[item.card_id]
            )
            if (
                context_character_limit is not None
                and selected_characters + visible_characters
                > context_character_limit
            ):
                continue
            selected.append(item)
            selected_characters += visible_characters
            selected_novel_count += int(is_novel)
            if len(selected) >= action.top_k:
                break
        hits = [
            ControllerConceptHit(
                concept_id=item.card_id,
                score=item.score,
                rank=rank,
                retrieval_source="dense_card_proxy",
            )
            for rank, item in enumerate(selected, start=1)
        ]
        returned_ids = [item.concept_id for item in hits]
        novel_ids = [
            concept_id
            for concept_id in returned_ids
            if concept_id not in seen_concept_ids
        ]
        duplicate_ids = [
            concept_id
            for concept_id in returned_ids
            if concept_id in seen_concept_ids
        ]
        elapsed = (time.perf_counter() - started) * 1000
        return ConceptSearchObservation(
            action_fingerprint=action_fingerprint(action),
            novel_concept_ids=novel_ids,
            duplicate_ids=duplicate_ids,
            hits=hits,
            cost=ControllerCost(
                steps=1,
                retrieval_calls=1,
                concept_searches=1,
                unique_concepts=len(novel_ids),
                context_characters=selected_characters,
                elapsed_milliseconds=elapsed,
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
        self._require_known(
            action.scope_concept_ids,
            self._concepts,
            label="evidence-search concept scope",
        )
        self._require_known(
            action.exclude_evidence_ids,
            self._evidence,
            label="excluded evidence",
        )
        self._require_known(
            seen_evidence_ids,
            self._evidence,
            label="seen evidence",
        )
        started = time.perf_counter()
        scope = (
            set(action.scope_concept_ids)
            if action.scope_concept_ids
            else set(self._concepts)
        )
        allowed_evidence_ids = {
            item.evidence_id
            for item in self.snapshot.evidence
            if item.concept_id in scope
        }
        ranked = self._evidence_index.rank(
            action.query,
            allowed_ids=allowed_evidence_ids,
            excluded_ids=set(action.exclude_evidence_ids),
            top_k=None,
        )
        selected: list[tuple[str, float]] = []
        selected_novel_count = 0
        selected_characters = 0
        for evidence_id, score in ranked:
            is_novel = evidence_id not in seen_evidence_ids
            if (
                is_novel
                and max_novel_evidence is not None
                and selected_novel_count >= max_novel_evidence
            ):
                continue
            visible_characters = len(
                _evidence_visible_text(self._evidence[evidence_id])
            )
            if (
                context_character_limit is not None
                and selected_characters + visible_characters
                > context_character_limit
            ):
                continue
            selected.append((evidence_id, score))
            selected_characters += visible_characters
            selected_novel_count += int(is_novel)
            if len(selected) >= action.top_k:
                break
        hits = [
            ControllerEvidenceHit(
                evidence_id=evidence_id,
                concept_id=self._evidence[evidence_id].concept_id,
                claim_id=self._evidence[evidence_id].claim_id,
                score=score,
                rank=rank,
                retrieval_source="bm25_evidence",
            )
            for rank, (evidence_id, score) in enumerate(selected, start=1)
        ]
        returned_ids = [item.evidence_id for item in hits]
        novel_ids = [
            evidence_id
            for evidence_id in returned_ids
            if evidence_id not in seen_evidence_ids
        ]
        duplicate_ids = [
            evidence_id
            for evidence_id in returned_ids
            if evidence_id in seen_evidence_ids
        ]
        elapsed = (time.perf_counter() - started) * 1000
        return EvidenceSearchObservation(
            action_fingerprint=action_fingerprint(action),
            novel_evidence_ids=novel_ids,
            duplicate_ids=duplicate_ids,
            hits=hits,
            cost=ControllerCost(
                steps=1,
                retrieval_calls=1,
                evidence_searches=1,
                unique_evidence=len(novel_ids),
                context_characters=selected_characters,
                elapsed_milliseconds=elapsed,
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
        self._require_known(
            action.anchor_concept_ids,
            self._concepts,
            label="graph anchor concept",
        )
        self._require_known(
            seen_concept_ids,
            self._concepts,
            label="seen concept",
        )
        self._require_known(
            action.exclude_relation_ids,
            self._relations,
            label="excluded relation",
        )
        self._require_known(
            traversed_relation_ids,
            self._relations,
            label="traversed relation",
        )
        started = time.perf_counter()
        allowed_types = set(action.relation_types)
        excluded_relations = set(action.exclude_relation_ids)
        candidates: list[
            tuple[str, ControllerRelationEdge, str, str]
        ] = []
        for anchor_id in sorted(action.anchor_concept_ids):
            anchor_candidates: list[
                tuple[ControllerRelationEdge, str, str]
            ] = []
            if action.direction in {"outgoing", "both"}:
                anchor_candidates.extend(
                    (relation, "outgoing", relation.target_concept_id)
                    for relation in self._outgoing.get(anchor_id, ())
                    if relation.relation_type in allowed_types
                    and relation.relation_id not in excluded_relations
                )
            if action.direction in {"incoming", "both"}:
                anchor_candidates.extend(
                    (relation, "incoming", relation.source_concept_id)
                    for relation in self._incoming.get(anchor_id, ())
                    if relation.relation_type in allowed_types
                    and relation.relation_id not in excluded_relations
                )
            anchor_candidates.sort(
                key=lambda item: (
                    -item[0].score,
                    item[0].relation_id,
                    item[1],
                    item[2],
                )
            )
            candidates.extend(
                (anchor_id, relation, direction, neighbor_id)
                for relation, direction, neighbor_id in anchor_candidates[
                    : action.max_neighbors_per_anchor
                ]
            )

        selected: list[tuple[ControllerRelationEdge, str, str]] = []
        selected_relation_ids: set[str] = set()
        selected_concept_ids: set[str] = set()
        selected_novel_concept_ids: set[str] = set()
        selected_characters = 0
        for _, relation, direction, neighbor_id in candidates:
            if relation.relation_id in selected_relation_ids:
                continue
            first_concept_occurrence = (
                neighbor_id not in selected_concept_ids
            )
            novel_concept = (
                first_concept_occurrence
                and neighbor_id not in seen_concept_ids
            )
            if (
                novel_concept
                and max_novel_concepts is not None
                and len(selected_novel_concept_ids)
                >= max_novel_concepts
            ):
                continue
            visible_characters = (
                _concept_visible_characters(self._concepts[neighbor_id])
                if first_concept_occurrence
                else 0
            )
            if (
                context_character_limit is not None
                and selected_characters + visible_characters
                > context_character_limit
            ):
                continue
            selected_relation_ids.add(relation.relation_id)
            selected_concept_ids.add(neighbor_id)
            if novel_concept:
                selected_novel_concept_ids.add(neighbor_id)
            selected_characters += visible_characters
            selected.append((relation, direction, neighbor_id))

        hits = [
            ControllerRelationHit(
                relation_id=relation.relation_id,
                source_concept_id=relation.source_concept_id,
                target_concept_id=relation.target_concept_id,
                relation_type=relation.relation_type,
                traversal_direction=direction,
                score=relation.score,
                rank=rank,
            )
            for rank, (relation, direction, _) in enumerate(
                selected,
                start=1,
            )
        ]
        returned_relation_ids = [
            relation.relation_id for relation, _, _ in selected
        ]
        returned_concept_ids = _ordered_unique(
            neighbor_id for _, _, neighbor_id in selected
        )
        novel_relation_ids = [
            relation_id
            for relation_id in returned_relation_ids
            if relation_id not in traversed_relation_ids
        ]
        novel_concept_ids = [
            concept_id
            for concept_id in returned_concept_ids
            if concept_id not in seen_concept_ids
        ]
        duplicate_ids = _ordered_unique(
            [
                relation_id
                for relation_id in returned_relation_ids
                if relation_id in traversed_relation_ids
            ]
            + [
                concept_id
                for concept_id in returned_concept_ids
                if concept_id in seen_concept_ids
            ]
        )
        elapsed = (time.perf_counter() - started) * 1000
        return GraphExpansionObservation(
            action_fingerprint=action_fingerprint(action),
            novel_concept_ids=novel_concept_ids,
            novel_relation_ids=novel_relation_ids,
            duplicate_ids=duplicate_ids,
            hits=hits,
            cost=ControllerCost(
                steps=1,
                retrieval_calls=1,
                graph_expansions=1,
                unique_concepts=len(novel_concept_ids),
                context_characters=selected_characters,
                elapsed_milliseconds=elapsed,
            ),
        )

    def concept_node(self, concept_id: str) -> ControllerConceptNode:
        try:
            return self._concepts[concept_id]
        except KeyError as exc:
            raise ValueError(f"Unknown concept id: {concept_id}") from exc

    def evidence_node(self, evidence_id: str) -> ControllerEvidenceNode:
        try:
            return self._evidence[evidence_id]
        except KeyError as exc:
            raise ValueError(f"Unknown evidence id: {evidence_id}") from exc

    def _validate_embedding_snapshot(
        self,
        embedding_snapshot: RagEmbeddingSnapshot,
    ) -> None:
        if embedding_snapshot.corpus_sha256 != self.snapshot.corpus_sha256:
            raise ValueError(
                "Embedding snapshot corpus hash does not match controller "
                "memory."
            )
        record_ids = [item.card_id for item in embedding_snapshot.records]
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("Embedding snapshot card ids must be unique.")
        concept_ids = {item.concept_id for item in self.snapshot.concepts}
        if set(record_ids) != concept_ids:
            raise ValueError(
                "Embedding snapshot card ids do not match card-proxy concepts."
            )
        if any(
            len(item.vector) != embedding_snapshot.dimension
            for item in embedding_snapshot.records
        ):
            raise ValueError(
                "Embedding vectors do not match the declared dimension."
            )
        expected_hash = sha256_value(
            [
                item.model_dump(mode="json")
                for item in embedding_snapshot.records
            ]
        )
        if embedding_snapshot.embeddings_sha256 != expected_hash:
            raise ValueError("Embedding snapshot hash is not canonical.")

    @staticmethod
    def _require_known(
        values: Iterable[str],
        known: Mapping[str, object],
        *,
        label: str,
    ) -> None:
        unknown = sorted(set(values).difference(known))
        if unknown:
            raise ValueError(f"Unknown {label} ids: {unknown[:5]}")


@dataclass(frozen=True)
class _TextBm25Index:
    document_ids: tuple[str, ...]
    term_frequencies: tuple[Counter[str], ...]
    document_lengths: tuple[int, ...]
    inverse_document_frequencies: dict[str, float]
    average_document_length: float
    k1: float
    b: float

    @classmethod
    def build(
        cls,
        documents: Mapping[str, str],
        *,
        k1: float,
        b: float,
    ) -> "_TextBm25Index":
        if not documents:
            raise ValueError("Evidence BM25 requires at least one document.")
        if k1 <= 0 or not 0 <= b <= 1:
            raise ValueError("Invalid evidence BM25 parameters.")
        ordered = sorted(documents.items())
        frequencies = tuple(
            Counter(tokenize_for_retrieval(text))
            for _, text in ordered
        )
        lengths = tuple(sum(items.values()) for items in frequencies)
        document_frequency: Counter[str] = Counter()
        for items in frequencies:
            document_frequency.update(items.keys())
        document_count = len(ordered)
        inverse_document_frequencies = {
            term: math.log(
                1.0
                + (document_count - count + 0.5) / (count + 0.5)
            )
            for term, count in document_frequency.items()
        }
        return cls(
            document_ids=tuple(item_id for item_id, _ in ordered),
            term_frequencies=frequencies,
            document_lengths=lengths,
            inverse_document_frequencies=inverse_document_frequencies,
            average_document_length=sum(lengths) / document_count,
            k1=k1,
            b=b,
        )

    def rank(
        self,
        query: str,
        *,
        allowed_ids: set[str],
        excluded_ids: set[str],
        top_k: int | None,
    ) -> list[tuple[str, float]]:
        query_terms = tokenize_for_retrieval(query)
        scores: list[tuple[str, float]] = []
        for document_id, frequencies, length in zip(
            self.document_ids,
            self.term_frequencies,
            self.document_lengths,
        ):
            if (
                document_id not in allowed_ids
                or document_id in excluded_ids
            ):
                continue
            normalization = self.k1 * (
                1.0
                - self.b
                + self.b
                * length
                / max(self.average_document_length, 1e-12)
            )
            score = 0.0
            for term in query_terms:
                frequency = frequencies.get(term, 0)
                if frequency == 0:
                    continue
                score += self.inverse_document_frequencies.get(
                    term,
                    0.0,
                ) * (
                    frequency
                    * (self.k1 + 1.0)
                    / (frequency + normalization)
                )
            scores.append((document_id, score))
        scores.sort(key=lambda item: (-item[1], item[0]))
        return scores if top_k is None else scores[:top_k]


def _audit_corpus_snapshot(corpus: RagCorpusSnapshot) -> None:
    card_ids = [card.card_id for card in corpus.cards]
    claim_ids = [
        claim.claim_id
        for card in corpus.cards
        for claim in card.claims
    ]
    evidence_ids = [
        item.evidence_id
        for card in corpus.cards
        for claim in card.claims
        for item in claim.evidence
    ]
    relation_ids = [relation.relation_id for relation in corpus.relations]
    for label, values in (
        ("card", card_ids),
        ("claim", claim_ids),
        ("evidence", evidence_ids),
        ("relation", relation_ids),
    ):
        if len(values) != len(set(values)):
            raise ValueError(f"Corpus {label} ids must be unique.")
    known_cards = set(card_ids)
    if any(
        relation.source_card_id not in known_cards
        or relation.target_card_id not in known_cards
        for relation in corpus.relations
    ):
        raise ValueError("Corpus relation references an unknown card.")
    expected_sha256 = sha256_value(
        {
            "course_id": corpus.course_id,
            "cards": [
                card.model_dump(mode="json")
                for card in corpus.cards
            ],
            "relations": [
                relation.model_dump(mode="json")
                for relation in corpus.relations
            ],
        }
    )
    if corpus.snapshot_sha256 != expected_sha256:
        raise ValueError("Corpus snapshot hash is not canonical.")


def _reviewed_relation_id(
    source_concept_id: str,
    target_concept_id: str,
    relation_type: str,
) -> str:
    digest = sha256_value(
        {
            "source_concept_id": source_concept_id,
            "target_concept_id": target_concept_id,
            "relation_type": relation_type,
        }
    )
    return f"reviewed-{digest[:24]}"


def _sort_relations(
    relations: Sequence[ControllerRelationEdge],
) -> list[ControllerRelationEdge]:
    return sorted(
        relations,
        key=lambda item: (
            -item.score,
            item.relation_id,
            item.source_concept_id,
            item.target_concept_id,
        ),
    )


def _ordered_unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _concept_visible_characters(concept: ControllerConceptNode) -> int:
    return len(concept.document_text)


def _evidence_visible_text(evidence: ControllerEvidenceNode) -> str:
    return f"{evidence.claim_text}\n{evidence.text}"
