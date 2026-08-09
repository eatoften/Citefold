from __future__ import annotations

import logging
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from . import concept_graph_publication_service
from .concept_graph import ConceptRelationType, RelationSupportBasis
from .concept_graph_path import GraphPathIntegrityError, relationship_trace
from .concept_graph_publication import (
    PublishedConcept,
    PublishedEvidence,
    PublishedGraphSnapshot,
    PublishedRelationEvidence,
)


LOGGER = logging.getLogger(__name__)

GRAPH_CHAT_SCHEMA_VERSION = 1
GRAPH_CHAT_MAX_HOPS = 6
GRAPH_CHAT_MAX_NODES = 32


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatGraphConcept(_StrictModel):
    concept_id: str = Field(min_length=1)
    concept_revision: int = Field(ge=1)
    preferred_name: str = Field(min_length=1)


class ChatGraphStep(_StrictModel):
    ordinal: int = Field(ge=0)
    relation_id: str = Field(min_length=1)
    relation_revision: int = Field(ge=1)
    relation_type: ConceptRelationType
    support_basis: RelationSupportBasis
    from_concept_id: str = Field(min_length=1)
    to_concept_id: str = Field(min_length=1)
    traversed_against_relation_direction: bool = False


class ChatGraphContext(_StrictModel):
    """Immutable published path snapshot persisted with one Chat answer."""

    schema_version: Literal[1] = GRAPH_CHAT_SCHEMA_VERSION
    course_id: str = Field(min_length=1)
    graph_version: int = Field(ge=1)
    graph_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    strategy: Literal["relationship_trace"] = "relationship_trace"
    concepts: list[ChatGraphConcept] = Field(min_length=2)
    steps: list[ChatGraphStep] = Field(min_length=1)


class _ConceptMatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    concept: PublishedConcept
    start: int
    end: int


def load_graph_chat_context(
    course_id: str,
    question: str,
    selected_source_ids: list[str],
) -> ChatGraphContext | None:
    """Return optional path context without changing Source retrieval."""

    if not selected_source_ids:
        return None
    try:
        version = (
            concept_graph_publication_service.require_current_authoritative_version(
                course_id
            )
        )
        snapshot = concept_graph_publication_service.load_course_graph_snapshot(
            course_id,
            version.version_number,
        )
        if (
            not snapshot.version.is_active_version
            or not snapshot.version.source_authority_current
        ):
            return None
        return build_graph_chat_context(
            snapshot,
            question,
            selected_source_ids=selected_source_ids,
        )
    except (
        concept_graph_publication_service.PublishedVersionNotFoundError,
        concept_graph_publication_service.CurrentVersionAuthorityStaleError,
    ):
        return None
    except (
        concept_graph_publication_service.ConceptGraphPublicationServiceError,
        GraphPathIntegrityError,
    ):
        LOGGER.warning(
            "Concept Graph context was unavailable; using Source search only.",
            exc_info=True,
        )
        return None


def build_graph_chat_context(
    snapshot: PublishedGraphSnapshot,
    question: str,
    *,
    selected_source_ids: list[str],
) -> ChatGraphContext | None:
    """Resolve exactly two explicit Concepts to one safe deterministic trace."""

    selected_sources = set(selected_source_ids)
    if (
        not question.strip()
        or not selected_sources
        or not snapshot.version.is_active_version
        or not snapshot.version.source_authority_current
    ):
        return None

    eligible_concepts = [
        concept
        for concept in snapshot.concepts
        if _is_fully_source_scoped(concept.evidence, selected_sources)
    ]
    matches = _match_concepts(question, eligible_concepts)
    if len(matches) != 2:
        return None

    trace = relationship_trace(
        snapshot,
        source_concept_id=matches[0].concept.concept_id,
        target_concept_id=matches[1].concept.concept_id,
        direction_mode="both",
        max_hops=GRAPH_CHAT_MAX_HOPS,
        max_nodes=GRAPH_CHAT_MAX_NODES,
    )
    if (
        trace.status != "found"
        or not trace.nodes
        or not trace.steps
        or any(
            not _is_fully_source_scoped(node.evidence, selected_sources)
            for node in trace.nodes
        )
        or any(
            not _is_fully_source_scoped(
                step.relation.evidence,
                selected_sources,
            )
            for step in trace.steps
        )
    ):
        return None

    return ChatGraphContext(
        course_id=snapshot.version.course_id,
        graph_version=snapshot.version.version_number,
        graph_content_hash=snapshot.version.content_hash,
        result_hash=trace.result_hash,
        concepts=[
            ChatGraphConcept(
                concept_id=concept.concept_id,
                concept_revision=concept.concept_revision,
                preferred_name=concept.preferred_name,
            )
            for concept in trace.nodes
        ],
        steps=[
            ChatGraphStep(
                ordinal=step.ordinal,
                relation_id=step.relation.relation_id,
                relation_revision=step.relation.relation_revision,
                relation_type=step.relation.relation_type,
                support_basis=step.relation.support_basis,
                from_concept_id=step.from_concept_id,
                to_concept_id=step.to_concept_id,
                traversed_against_relation_direction=(
                    step.traversed_against_relation_direction
                ),
            )
            for step in trace.steps
        ],
    )


def _is_fully_source_scoped(
    evidence: list[PublishedEvidence] | list[PublishedRelationEvidence],
    selected_sources: set[str],
) -> bool:
    """A derived graph owner is visible only if all its evidence is selected."""

    return bool(evidence) and all(
        item.source_id in selected_sources for item in evidence
    )


def _match_concepts(
    question: str,
    concepts: list[PublishedConcept],
) -> list[_ConceptMatch]:
    normalized_question = _normalize_text(question)
    candidates: list[_ConceptMatch] = []
    for concept in concepts:
        terms = [concept.preferred_name]
        terms.extend(alias.display_text for alias in concept.aliases)
        best: tuple[int, int] | None = None
        for value in terms:
            term = _normalize_text(value)
            if len(term) < 2:
                continue
            position = _find_term(normalized_question, term)
            if position is None:
                continue
            candidate = (position, position + len(term))
            if best is None or (candidate[0], -len(term)) < (
                best[0],
                -(best[1] - best[0]),
            ):
                best = candidate
        if best is not None:
            candidates.append(
                _ConceptMatch(
                    concept=concept,
                    start=best[0],
                    end=best[1],
                )
            )

    ordered = sorted(
        candidates,
        key=lambda item: (
            item.start,
            -(item.end - item.start),
            item.concept.preferred_name.casefold(),
            item.concept.concept_id,
        ),
    )
    selected: list[_ConceptMatch] = []
    for candidate in ordered:
        if any(
            candidate.start >= existing.start
            and candidate.end <= existing.end
            for existing in selected
        ):
            continue
        selected.append(candidate)
    return selected


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(
        "".join(
            character if character.isalnum() else " "
            for character in normalized
        ).split()
    )


def _find_term(question: str, term: str) -> int | None:
    start = 0
    ascii_term = all(ord(character) < 128 for character in term)
    while True:
        position = question.find(term, start)
        if position < 0:
            return None
        end = position + len(term)
        if not ascii_term or (
            (position == 0 or question[position - 1] == " ")
            and (end == len(question) or question[end] == " ")
        ):
            return position
        start = position + 1


__all__ = [
    "ChatGraphContext",
    "build_graph_chat_context",
    "load_graph_chat_context",
]
