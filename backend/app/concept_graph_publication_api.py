from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status

from . import concept_graph_publication_service as publication_service
from .concept_graph_publication import (
    GraphPublicationPreview,
    GraphPublicationRequest,
    GraphVersionMetadata,
    GraphVersionPage,
    PublishedConceptPage,
    PublishedRelationPage,
)


router = APIRouter(tags=["concept-graph-publication"])
ResourceId = Annotated[str, Path(min_length=1, max_length=200)]


@router.get(
    "/courses/{course_id}/concept-graph/publication-preview",
    response_model=GraphPublicationPreview,
)
def get_publication_preview(course_id: ResourceId) -> GraphPublicationPreview:
    try:
        return publication_service.preview_course_publication(course_id)
    except publication_service.ConceptGraphPublicationServiceError as exc:
        raise_concept_graph_publication_http_error(exc)


@router.post(
    "/courses/{course_id}/concept-graph/versions",
    response_model=GraphVersionMetadata,
    status_code=status.HTTP_201_CREATED,
)
def publish_graph_version(
    course_id: ResourceId,
    request: GraphPublicationRequest,
) -> GraphVersionMetadata:
    try:
        return publication_service.publish_course_version(course_id, request)
    except publication_service.ConceptGraphPublicationServiceError as exc:
        raise_concept_graph_publication_http_error(exc)


@router.get(
    "/courses/{course_id}/concept-graph/versions",
    response_model=GraphVersionPage,
)
def list_graph_versions(
    course_id: ResourceId,
    limit: int = Query(default=20, ge=1, le=20),
    cursor: str | None = Query(default=None, min_length=1, max_length=20),
) -> GraphVersionPage:
    try:
        return publication_service.list_course_versions(
            course_id, limit=limit, cursor=cursor
        )
    except publication_service.ConceptGraphPublicationServiceError as exc:
        raise_concept_graph_publication_http_error(exc)


@router.get(
    "/courses/{course_id}/concept-graph/versions/current",
    response_model=GraphVersionMetadata,
)
def get_current_graph_version(course_id: ResourceId) -> GraphVersionMetadata:
    try:
        return publication_service.get_current_course_version(course_id)
    except publication_service.ConceptGraphPublicationServiceError as exc:
        raise_concept_graph_publication_http_error(exc)


@router.get(
    "/courses/{course_id}/concept-graph/versions/{version_number}",
    response_model=GraphVersionMetadata,
)
def get_graph_version(
    course_id: ResourceId,
    version_number: int = Path(ge=1),
) -> GraphVersionMetadata:
    try:
        return publication_service.get_course_version(
            course_id, version_number
        )
    except publication_service.ConceptGraphPublicationServiceError as exc:
        raise_concept_graph_publication_http_error(exc)


@router.get(
    (
        "/courses/{course_id}/concept-graph/versions/{version_number}/"
        "concepts"
    ),
    response_model=PublishedConceptPage,
)
def list_graph_version_concepts(
    course_id: ResourceId,
    version_number: int = Path(ge=1),
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = Query(default=None, min_length=1, max_length=200),
) -> PublishedConceptPage:
    try:
        return publication_service.list_course_version_concepts(
            course_id,
            version_number,
            limit=limit,
            cursor=cursor,
        )
    except publication_service.ConceptGraphPublicationServiceError as exc:
        raise_concept_graph_publication_http_error(exc)


@router.get(
    (
        "/courses/{course_id}/concept-graph/versions/{version_number}/"
        "relations"
    ),
    response_model=PublishedRelationPage,
)
def list_graph_version_relations(
    course_id: ResourceId,
    version_number: int = Path(ge=1),
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = Query(default=None, min_length=1, max_length=200),
) -> PublishedRelationPage:
    try:
        return publication_service.list_course_version_relations(
            course_id,
            version_number,
            limit=limit,
            cursor=cursor,
        )
    except publication_service.ConceptGraphPublicationServiceError as exc:
        raise_concept_graph_publication_http_error(exc)


def raise_concept_graph_publication_http_error(
    exc: publication_service.ConceptGraphPublicationServiceError,
) -> None:
    if isinstance(
        exc,
        (
            publication_service.PublicationCourseNotFoundError,
            publication_service.PublishedVersionNotFoundError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    if isinstance(
        exc, publication_service.InvalidPublicationRequestError
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    if isinstance(
        exc, publication_service.CurrentVersionAuthorityStaleError
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "concept_graph_source_authority_stale",
                "message": str(exc),
                "version": exc.version.model_dump(mode="json"),
            },
        ) from exc
    if isinstance(
        exc, publication_service.ConceptGraphPublicationConflictError
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    if isinstance(
        exc, publication_service.ConceptGraphPublicationTooLargeError
    ):
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)
        ) from exc
    if isinstance(exc, publication_service.ConceptGraphPublicationBusyError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            headers={"Retry-After": "1"},
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected Concept graph publication service error.",
    ) from exc
