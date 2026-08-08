from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status

from . import concept_graph_service
from .concept_graph import (
    Concept,
    ConceptCreate,
    ConceptPage,
    ConceptRelation,
    ConceptRelationCreate,
    ConceptRelationPage,
)


router = APIRouter(tags=["concept-graph"])
ResourceId = Annotated[str, Path(min_length=1, max_length=200)]


@router.post(
    "/courses/{course_id}/concepts",
    response_model=Concept,
    status_code=status.HTTP_201_CREATED,
)
def create_course_concept(
    course_id: ResourceId,
    request: ConceptCreate,
) -> Concept:
    try:
        return concept_graph_service.create_grounded_concept_candidate(
            course_id,
            request,
        )
    except concept_graph_service.ConceptGraphServiceError as exc:
        _raise_http_error(exc)


@router.get(
    "/courses/{course_id}/concepts",
    response_model=ConceptPage,
)
def list_course_concepts(
    course_id: ResourceId,
    limit: int = Query(default=20, ge=1, le=20),
    cursor: str | None = Query(default=None, min_length=1, max_length=200),
) -> ConceptPage:
    try:
        return concept_graph_service.list_course_concepts(
            course_id,
            limit=limit,
            cursor=cursor,
        )
    except concept_graph_service.ConceptGraphServiceError as exc:
        _raise_http_error(exc)


@router.get(
    "/courses/{course_id}/concepts/{concept_id}",
    response_model=Concept,
)
def get_course_concept(
    course_id: ResourceId,
    concept_id: ResourceId,
) -> Concept:
    try:
        return concept_graph_service.get_course_concept(
            course_id,
            concept_id,
        )
    except concept_graph_service.ConceptGraphServiceError as exc:
        _raise_http_error(exc)


@router.post(
    "/courses/{course_id}/concept-relations",
    response_model=ConceptRelation,
    status_code=status.HTTP_201_CREATED,
)
def create_course_concept_relation(
    course_id: ResourceId,
    request: ConceptRelationCreate,
) -> ConceptRelation:
    try:
        return concept_graph_service.create_grounded_relation_candidate(
            course_id,
            request,
        )
    except concept_graph_service.ConceptGraphServiceError as exc:
        _raise_http_error(exc)


@router.get(
    "/courses/{course_id}/concept-relations",
    response_model=ConceptRelationPage,
)
def list_course_concept_relations(
    course_id: ResourceId,
    limit: int = Query(default=20, ge=1, le=20),
    cursor: str | None = Query(default=None, min_length=1, max_length=200),
) -> ConceptRelationPage:
    try:
        return concept_graph_service.list_course_relations(
            course_id,
            limit=limit,
            cursor=cursor,
        )
    except concept_graph_service.ConceptGraphServiceError as exc:
        _raise_http_error(exc)


@router.get(
    "/courses/{course_id}/concept-relations/{relation_id}",
    response_model=ConceptRelation,
)
def get_course_concept_relation(
    course_id: ResourceId,
    relation_id: ResourceId,
) -> ConceptRelation:
    try:
        return concept_graph_service.get_course_relation(
            course_id,
            relation_id,
        )
    except concept_graph_service.ConceptGraphServiceError as exc:
        _raise_http_error(exc)


def _raise_http_error(
    exc: concept_graph_service.ConceptGraphServiceError,
) -> None:
    if isinstance(
        exc,
        (
            concept_graph_service.ConceptGraphCourseNotFoundError,
            concept_graph_service.ConceptNotFoundError,
            concept_graph_service.ConceptRelationNotFoundError,
            concept_graph_service.GraphEvidenceNotFoundError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    if isinstance(
        exc,
        concept_graph_service.InvalidConceptGraphRequestError,
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    if isinstance(exc, concept_graph_service.ConceptGraphConflictError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected Concept graph service error.",
    ) from exc
