"""Application boundary for deterministic Concept graph path queries."""

from __future__ import annotations

from collections.abc import Sequence

from . import concept_graph_publication_service as publication_service
from .concept_graph import ConceptRelationType
from .concept_graph_path import (
    GraphDirectionMode,
    GraphPathConceptNotFoundError,
    GraphPathIntegrityError,
    GraphPathLimitError,
    GraphPathRequestError,
    LearningPathResult,
    LocalGraphResult,
    RELATION_TYPE_PRIORITY,
    RelationshipTraceResult,
    learning_path,
    local_graph,
    relationship_trace,
)
from .concept_graph_publication import PublishedGraphSnapshot


class ConceptGraphPathServiceError(RuntimeError):
    pass


class ConceptGraphPathNotFoundError(ConceptGraphPathServiceError):
    pass


class InvalidConceptGraphPathRequestError(ConceptGraphPathServiceError):
    pass


class ConceptGraphPathLimitError(ConceptGraphPathServiceError):
    pass


class ConceptGraphVersionNotAuthoritativeError(
    ConceptGraphPathServiceError
):
    def __init__(self, *, code: str, version: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.version = version


class ConceptGraphPathBusyError(ConceptGraphPathServiceError):
    pass


class ConceptGraphPathPersistenceError(ConceptGraphPathServiceError):
    pass


def get_local_graph(
    course_id: str,
    version_number: int,
    *,
    root_concept_id: str,
    relation_types: Sequence[ConceptRelationType] = RELATION_TYPE_PRIORITY,
    direction_mode: GraphDirectionMode = "both",
    max_hops: int = 2,
    max_nodes: int = 100,
) -> LocalGraphResult:
    snapshot = _load_authoritative_snapshot(course_id, version_number)
    return _run_engine(
        lambda: local_graph(
            snapshot,
            root_concept_id=root_concept_id,
            relation_types=relation_types,
            direction_mode=direction_mode,
            max_hops=max_hops,
            max_nodes=max_nodes,
        )
    )


def get_relationship_trace(
    course_id: str,
    version_number: int,
    *,
    source_concept_id: str,
    target_concept_id: str,
    relation_types: Sequence[ConceptRelationType] = RELATION_TYPE_PRIORITY,
    direction_mode: GraphDirectionMode = "outgoing",
    max_hops: int = 6,
    max_nodes: int = 200,
) -> RelationshipTraceResult:
    snapshot = _load_authoritative_snapshot(course_id, version_number)
    return _run_engine(
        lambda: relationship_trace(
            snapshot,
            source_concept_id=source_concept_id,
            target_concept_id=target_concept_id,
            relation_types=relation_types,
            direction_mode=direction_mode,
            max_hops=max_hops,
            max_nodes=max_nodes,
        )
    )


def get_learning_path(
    course_id: str,
    version_number: int,
    *,
    target_concept_id: str,
    max_nodes: int = 200,
) -> LearningPathResult:
    snapshot = _load_authoritative_snapshot(course_id, version_number)
    return _run_engine(
        lambda: learning_path(
            snapshot,
            target_concept_id=target_concept_id,
            max_nodes=max_nodes,
        )
    )


def _load_authoritative_snapshot(
    course_id: str,
    version_number: int,
) -> PublishedGraphSnapshot:
    try:
        snapshot = publication_service.load_course_graph_snapshot(
            course_id,
            version_number,
        )
    except (
        publication_service.PublicationCourseNotFoundError,
        publication_service.PublishedVersionNotFoundError,
    ) as exc:
        raise ConceptGraphPathNotFoundError(str(exc)) from exc
    except publication_service.ConceptGraphPublicationBusyError as exc:
        raise ConceptGraphPathBusyError(str(exc)) from exc
    except publication_service.ConceptGraphPublicationServiceError as exc:
        raise ConceptGraphPathPersistenceError(str(exc)) from exc

    version = snapshot.version
    if not version.is_active_version:
        raise ConceptGraphVersionNotAuthoritativeError(
            code="concept_graph_version_not_active",
            version=version.version_number,
            message="Paths are served only from the active graph version.",
        )
    if not version.source_authority_current:
        raise ConceptGraphVersionNotAuthoritativeError(
            code="concept_graph_source_authority_stale",
            version=version.version_number,
            message=(
                "The active graph version has stale Source evidence and must "
                "be reviewed and republished before path traversal."
            ),
        )
    return snapshot


def _run_engine(operation):
    try:
        return operation()
    except GraphPathConceptNotFoundError as exc:
        raise ConceptGraphPathNotFoundError(str(exc)) from exc
    except GraphPathLimitError as exc:
        raise ConceptGraphPathLimitError(str(exc)) from exc
    except GraphPathRequestError as exc:
        raise InvalidConceptGraphPathRequestError(str(exc)) from exc
    except GraphPathIntegrityError as exc:
        raise ConceptGraphPathPersistenceError(
            "Published Concept graph path integrity validation failed."
        ) from exc


__all__ = [
    "ConceptGraphPathBusyError",
    "ConceptGraphPathLimitError",
    "ConceptGraphPathNotFoundError",
    "ConceptGraphPathPersistenceError",
    "ConceptGraphPathServiceError",
    "ConceptGraphVersionNotAuthoritativeError",
    "InvalidConceptGraphPathRequestError",
    "get_learning_path",
    "get_local_graph",
    "get_relationship_trace",
]
