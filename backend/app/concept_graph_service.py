from __future__ import annotations

from sqlite3 import IntegrityError
from uuid import uuid4

from . import course_service
from .concept_graph import (
    Concept,
    ConceptCreate,
    ConceptPage,
    ConceptRelation,
    ConceptRelationCreate,
    ConceptRelationPage,
    RelationEvidenceReferenceCreate,
    canonicalize_relation_endpoints,
)
from .concept_graph_store import (
    DuplicateRelationError,
    EvidenceChunkNotFoundError,
    EvidenceQuoteMismatchError,
    RelationEvidenceDriftError,
    RelationEvidenceMismatchError,
    RelationEndpointNotFoundError,
    create_concept_candidate,
    create_relation_candidate,
    get_concept as get_stored_concept,
    get_relation as get_stored_relation,
    list_concept_summaries_for_course,
    list_relation_summaries_for_course,
)
from .job import utc_now


class ConceptGraphServiceError(RuntimeError):
    pass


class ConceptGraphCourseNotFoundError(ConceptGraphServiceError):
    pass


class ConceptNotFoundError(ConceptGraphServiceError):
    pass


class ConceptRelationNotFoundError(ConceptGraphServiceError):
    pass


class GraphEvidenceNotFoundError(ConceptGraphServiceError):
    pass


class InvalidConceptGraphRequestError(ConceptGraphServiceError):
    pass


class ConceptGraphConflictError(ConceptGraphServiceError):
    pass


class ConceptGraphPersistenceError(ConceptGraphServiceError):
    pass


def create_grounded_concept_candidate(
    course_id: str,
    request: ConceptCreate,
) -> Concept:
    course = _require_course(course_id)
    now = utc_now()
    concept = Concept(
        id=uuid4().hex,
        course_id=course.id,
        preferred_name=request.preferred_name,
        short_definition=request.short_definition,
        revision=1,
        identity_status="active",
        review_status="candidate",
        validity_status="current",
        proposal_origin="human",
        created_at=now,
        updated_at=now,
    )
    try:
        return create_concept_candidate(
            concept,
            request.evidence,
            [uuid4().hex for _ in request.evidence],
        )
    except EvidenceChunkNotFoundError as exc:
        raise GraphEvidenceNotFoundError(str(exc)) from exc
    except EvidenceQuoteMismatchError as exc:
        raise InvalidConceptGraphRequestError(str(exc)) from exc
    except IntegrityError as exc:
        raise ConceptGraphPersistenceError(
            "Concept graph persistence failed."
        ) from exc


def list_course_concepts(
    course_id: str,
    *,
    limit: int,
    cursor: str | None,
) -> ConceptPage:
    course = _require_course(course_id)
    items, next_cursor = list_concept_summaries_for_course(
        course.id,
        limit=limit,
        cursor=cursor,
    )
    return ConceptPage(items=items, next_cursor=next_cursor)


def get_course_concept(course_id: str, concept_id: str) -> Concept:
    course = _require_course(course_id)
    concept = get_stored_concept(course.id, concept_id)
    if concept is None:
        raise ConceptNotFoundError("Concept not found in the selected course.")
    return concept


def create_grounded_relation_candidate(
    course_id: str,
    request: ConceptRelationCreate,
) -> ConceptRelation:
    course = _require_course(course_id)
    source_id, target_id = canonicalize_relation_endpoints(
        request.relation_type,
        request.source_concept_id,
        request.target_concept_id,
    )
    now = utc_now()
    evidence_requests = _canonicalize_endpoint_evidence_roles(
        request,
        canonical_source_id=source_id,
    )
    relation = ConceptRelation(
        id=uuid4().hex,
        course_id=course.id,
        source_concept_id=source_id,
        target_concept_id=target_id,
        relation_type=request.relation_type,
        support_basis=request.support_basis,
        rationale=request.rationale,
        revision=1,
        review_status="candidate",
        validity_status="current",
        proposal_origin="human",
        created_at=now,
        updated_at=now,
    )
    try:
        return create_relation_candidate(
            relation,
            evidence_requests,
            [uuid4().hex for _ in evidence_requests],
        )
    except EvidenceChunkNotFoundError as exc:
        raise GraphEvidenceNotFoundError(str(exc)) from exc
    except EvidenceQuoteMismatchError as exc:
        raise InvalidConceptGraphRequestError(str(exc)) from exc
    except RelationEndpointNotFoundError as exc:
        raise ConceptNotFoundError(str(exc)) from exc
    except RelationEvidenceMismatchError as exc:
        raise InvalidConceptGraphRequestError(str(exc)) from exc
    except (DuplicateRelationError, RelationEvidenceDriftError) as exc:
        raise ConceptGraphConflictError(
            str(exc)
        ) from exc
    except IntegrityError as exc:
        raise ConceptGraphPersistenceError(
            "Concept graph persistence failed."
        ) from exc


def list_course_relations(
    course_id: str,
    *,
    limit: int,
    cursor: str | None,
) -> ConceptRelationPage:
    course = _require_course(course_id)
    items, next_cursor = list_relation_summaries_for_course(
        course.id,
        limit=limit,
        cursor=cursor,
    )
    return ConceptRelationPage(items=items, next_cursor=next_cursor)


def get_course_relation(
    course_id: str,
    relation_id: str,
) -> ConceptRelation:
    course = _require_course(course_id)
    relation = get_stored_relation(course.id, relation_id)
    if relation is None:
        raise ConceptRelationNotFoundError(
            "Concept relation not found in the selected course."
        )
    return relation


def _require_course(course_id: str):
    try:
        return course_service.get_video_course(course_id)
    except course_service.CourseServiceError as exc:
        raise ConceptGraphCourseNotFoundError(
            "Course not found."
        ) from exc


def _canonicalize_endpoint_evidence_roles(
    request: ConceptRelationCreate,
    *,
    canonical_source_id: str,
) -> list[RelationEvidenceReferenceCreate]:
    if canonical_source_id == request.source_concept_id:
        return list(request.evidence)

    swapped_roles = {
        "source_endpoint": "target_endpoint",
        "target_endpoint": "source_endpoint",
    }
    return [
        item.model_copy(
            update={
                "support_role": swapped_roles.get(
                    item.support_role,
                    item.support_role,
                )
            }
        )
        for item in request.evidence
    ]
