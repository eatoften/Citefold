from __future__ import annotations

import hashlib
import json
import sqlite3
from sqlite3 import IntegrityError, OperationalError
from typing import Callable, Literal, TypeVar

from . import course_service
from .citation_target_store import CitationSnapshotRecord
from .concept_graph_publication import (
    GraphPublicationPreview,
    GraphPublicationRequest,
    GraphVersionMetadata,
    GraphVersionPage,
    PublishedGraphSnapshot,
    PublishedConceptPage,
    PublishedRelationPage,
)
from .concept_graph_publication_store import (
    ConceptGraphPublicationStoreError,
    PublicationConflictError,
    PublicationIntegrityError,
    PublicationNotFoundError,
    PublicationOperationReuseError,
    PublicationTooLargeError,
    get_version_evidence_snapshot,
    get_current_version,
    get_version,
    list_version_concepts,
    list_version_relations,
    load_version_graph_snapshot,
    list_versions,
    preview_publication,
    publish_version,
)


T = TypeVar("T")


class ConceptGraphPublicationServiceError(RuntimeError):
    pass


class PublicationCourseNotFoundError(ConceptGraphPublicationServiceError):
    pass


class PublishedVersionNotFoundError(ConceptGraphPublicationServiceError):
    pass


class InvalidPublicationRequestError(ConceptGraphPublicationServiceError):
    pass


class ConceptGraphPublicationConflictError(
    ConceptGraphPublicationServiceError
):
    pass


class CurrentVersionAuthorityStaleError(
    ConceptGraphPublicationConflictError
):
    def __init__(self, version: GraphVersionMetadata) -> None:
        super().__init__(
            "The active Concept graph version is no longer authoritative "
            "against the current Sources."
        )
        self.version = version


class ConceptGraphPublicationTooLargeError(
    ConceptGraphPublicationServiceError
):
    pass


class ConceptGraphPublicationBusyError(ConceptGraphPublicationServiceError):
    pass


class ConceptGraphPublicationPersistenceError(
    ConceptGraphPublicationServiceError
):
    pass


def preview_course_publication(course_id: str) -> GraphPublicationPreview:
    course = _require_course(course_id)
    return _run_store(lambda: preview_publication(course.id))


def publish_course_version(
    course_id: str,
    request: GraphPublicationRequest,
) -> GraphVersionMetadata:
    course = _require_course(course_id)
    request_hash = _publication_request_hash(course.id, request)
    return _run_store(
        lambda: publish_version(
            course.id, request, request_hash=request_hash
        )
    )


def list_course_versions(
    course_id: str,
    *,
    limit: int,
    cursor: str | None,
) -> GraphVersionPage:
    course = _require_course(course_id)
    parsed_cursor: int | None = None
    if cursor is not None:
        try:
            parsed_cursor = int(cursor)
        except ValueError as exc:
            raise InvalidPublicationRequestError(
                "Publication cursor is invalid."
            ) from exc
        if parsed_cursor < 1 or str(parsed_cursor) != cursor:
            raise InvalidPublicationRequestError(
                "Publication cursor is invalid."
            )
    return _run_store(
        lambda: list_versions(
            course.id, limit=limit, cursor=parsed_cursor
        )
    )


def get_course_version(
    course_id: str,
    version_number: int,
) -> GraphVersionMetadata:
    course = _require_course(course_id)
    return _run_store(lambda: get_version(course.id, version_number))


def get_current_course_version(course_id: str) -> GraphVersionMetadata:
    course = _require_course(course_id)
    version = _run_store(lambda: get_current_version(course.id))
    if not version.source_authority_current:
        raise CurrentVersionAuthorityStaleError(version)
    return version


def require_current_authoritative_version(
    course_id: str,
) -> GraphVersionMetadata:
    """Internal G3 guard: return only a current authoritative snapshot."""

    return get_current_course_version(course_id)


def list_course_version_concepts(
    course_id: str,
    version_number: int,
    *,
    limit: int,
    cursor: str | None,
) -> PublishedConceptPage:
    course = _require_course(course_id)
    return _run_store(
        lambda: list_version_concepts(
            course.id,
            version_number,
            limit=limit,
            cursor=cursor,
        )
    )


def list_course_version_relations(
    course_id: str,
    version_number: int,
    *,
    limit: int,
    cursor: str | None,
) -> PublishedRelationPage:
    course = _require_course(course_id)
    return _run_store(
        lambda: list_version_relations(
            course.id,
            version_number,
            limit=limit,
            cursor=cursor,
        )
    )


def load_course_graph_snapshot(
    course_id: str,
    version_number: int,
) -> PublishedGraphSnapshot:
    """Return one historical snapshot; G3 decides if it is authoritative."""

    course = _require_course(course_id)
    return _run_store(
        lambda: load_version_graph_snapshot(course.id, version_number)
    )


def get_course_version_evidence_snapshot(
    course_id: str,
    version_number: int,
    *,
    owner_type: Literal["concept", "relation"],
    owner_id: str,
    evidence_id: str,
) -> CitationSnapshotRecord:
    course = _require_course(course_id)
    return _run_store(
        lambda: get_version_evidence_snapshot(
            course.id,
            version_number,
            owner_type=owner_type,
            owner_id=owner_id,
            evidence_id=evidence_id,
        )
    )


def _require_course(course_id: str):
    try:
        return course_service.get_video_course(course_id)
    except course_service.CourseServiceError as exc:
        raise PublicationCourseNotFoundError("Course not found.") from exc


def _publication_request_hash(
    course_id: str,
    request: GraphPublicationRequest,
) -> str:
    payload = {
        "protocol": "concept-graph-publication-request-v1",
        "course_id": course_id,
        "path": "/courses/{course_id}/concept-graph/versions",
        "request": request.model_dump(mode="json"),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _run_store(operation: Callable[[], T]) -> T:
    try:
        return operation()
    except PublicationNotFoundError as exc:
        raise PublishedVersionNotFoundError(str(exc)) from exc
    except (
        PublicationConflictError,
        PublicationOperationReuseError,
    ) as exc:
        raise ConceptGraphPublicationConflictError(str(exc)) from exc
    except PublicationTooLargeError as exc:
        raise ConceptGraphPublicationTooLargeError(str(exc)) from exc
    except OperationalError as exc:
        if _is_sqlite_busy(exc):
            raise ConceptGraphPublicationBusyError(
                "Concept graph publication storage is busy."
            ) from exc
        raise ConceptGraphPublicationPersistenceError(
            "Concept graph publication storage failed."
        ) from exc
    except (IntegrityError, PublicationIntegrityError) as exc:
        raise ConceptGraphPublicationPersistenceError(
            "Concept graph publication integrity validation failed."
        ) from exc
    except (
        sqlite3.DatabaseError,
        ValueError,
        TypeError,
        UnicodeError,
    ) as exc:
        raise ConceptGraphPublicationPersistenceError(
            "Concept graph publication storage failed."
        ) from exc
    except ConceptGraphPublicationStoreError as exc:
        raise ConceptGraphPublicationPersistenceError(
            "Concept graph publication storage failed."
        ) from exc


def _is_sqlite_busy(exc: OperationalError) -> bool:
    error_code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(error_code, int):
        return (error_code & 0xFF) in {
            sqlite3.SQLITE_BUSY,
            sqlite3.SQLITE_LOCKED,
        }
    return str(exc).strip().lower() in {
        "database is locked",
        "database table is locked",
        "database is busy",
        "database table is busy",
    }
