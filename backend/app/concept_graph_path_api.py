from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status

from . import concept_graph_path_service as path_service
from .concept_graph import ConceptRelationType
from .concept_graph_path import (
    GraphDirectionMode,
    LearningPathResult,
    LocalGraphResult,
    MAX_GRAPH_RESULT_NODES,
    MAX_LOCAL_GRAPH_HOPS,
    MAX_RELATIONSHIP_TRACE_HOPS,
    RELATION_TYPE_PRIORITY,
    RelationshipTraceResult,
)


router = APIRouter(tags=["concept-graph-paths"])
ResourceId = Annotated[str, Path(min_length=1, max_length=200)]


@router.get(
    (
        "/courses/{course_id}/concept-graph/versions/{version_number}/"
        "paths/local"
    ),
    response_model=LocalGraphResult,
)
def get_local_graph(
    course_id: ResourceId,
    version_number: int = Path(ge=1),
    root_concept_id: str = Query(min_length=1, max_length=200),
    relation_types: list[ConceptRelationType] | None = Query(default=None),
    direction_mode: GraphDirectionMode = Query(default="both"),
    max_hops: int = Query(default=2, ge=0, le=MAX_LOCAL_GRAPH_HOPS),
    max_nodes: int = Query(
        default=100,
        ge=1,
        le=MAX_GRAPH_RESULT_NODES,
    ),
) -> LocalGraphResult:
    try:
        return path_service.get_local_graph(
            course_id,
            version_number,
            root_concept_id=root_concept_id,
            relation_types=relation_types or RELATION_TYPE_PRIORITY,
            direction_mode=direction_mode,
            max_hops=max_hops,
            max_nodes=max_nodes,
        )
    except path_service.ConceptGraphPathServiceError as exc:
        _raise_http_error(exc)


@router.get(
    (
        "/courses/{course_id}/concept-graph/versions/{version_number}/"
        "paths/trace"
    ),
    response_model=RelationshipTraceResult,
)
def get_relationship_trace(
    course_id: ResourceId,
    version_number: int = Path(ge=1),
    source_concept_id: str = Query(min_length=1, max_length=200),
    target_concept_id: str = Query(min_length=1, max_length=200),
    relation_types: list[ConceptRelationType] | None = Query(default=None),
    direction_mode: GraphDirectionMode = Query(default="outgoing"),
    max_hops: int = Query(
        default=6,
        ge=0,
        le=MAX_RELATIONSHIP_TRACE_HOPS,
    ),
    max_nodes: int = Query(
        default=200,
        ge=1,
        le=MAX_GRAPH_RESULT_NODES,
    ),
) -> RelationshipTraceResult:
    try:
        return path_service.get_relationship_trace(
            course_id,
            version_number,
            source_concept_id=source_concept_id,
            target_concept_id=target_concept_id,
            relation_types=relation_types or RELATION_TYPE_PRIORITY,
            direction_mode=direction_mode,
            max_hops=max_hops,
            max_nodes=max_nodes,
        )
    except path_service.ConceptGraphPathServiceError as exc:
        _raise_http_error(exc)


@router.get(
    (
        "/courses/{course_id}/concept-graph/versions/{version_number}/"
        "paths/learning"
    ),
    response_model=LearningPathResult,
)
def get_learning_path(
    course_id: ResourceId,
    version_number: int = Path(ge=1),
    target_concept_id: str = Query(min_length=1, max_length=200),
    max_nodes: int = Query(
        default=200,
        ge=1,
        le=MAX_GRAPH_RESULT_NODES,
    ),
) -> LearningPathResult:
    try:
        return path_service.get_learning_path(
            course_id,
            version_number,
            target_concept_id=target_concept_id,
            max_nodes=max_nodes,
        )
    except path_service.ConceptGraphPathServiceError as exc:
        _raise_http_error(exc)


def _raise_http_error(exc: path_service.ConceptGraphPathServiceError) -> None:
    if isinstance(exc, path_service.ConceptGraphPathNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    if isinstance(exc, path_service.InvalidConceptGraphPathRequestError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    if isinstance(exc, path_service.ConceptGraphPathLimitError):
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(exc),
        ) from exc
    if isinstance(
        exc,
        path_service.ConceptGraphVersionNotAuthoritativeError,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": exc.code,
                "message": str(exc),
                "version": exc.version,
            },
        ) from exc
    if isinstance(exc, path_service.ConceptGraphPathBusyError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            headers={"Retry-After": "1"},
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected Concept graph path service error.",
    ) from exc


__all__ = ["router"]
