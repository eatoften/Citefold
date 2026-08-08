from __future__ import annotations

import hashlib
import json
import sqlite3
from sqlite3 import IntegrityError, OperationalError
from typing import Callable, TypeVar
from uuid import uuid4

from . import course_service
from .concept_graph import (
    Concept,
    ConceptCreate,
    ConceptPage,
    ConceptMergeRequest,
    ConceptRetireRequest,
    ConceptRevisionEdit,
    ConceptRelation,
    ConceptRelationCreate,
    ConceptRelationPage,
    GraphMarkStaleRequest,
    GraphMutationRequest,
    GraphReviewRequest,
    RelationReviewRequest,
    RelationRevisionEdit,
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
    GraphEntityNotFoundError,
    GraphEvidenceStaleError,
    GraphMergeDependencyError,
    GraphOperationReuseError,
    GraphReviewTransitionError,
    GraphRevisionConflictError,
    PrerequisiteCycleError,
    create_concept_candidate,
    create_relation_candidate,
    edit_concept_revision,
    edit_relation_revision,
    get_concept as get_stored_concept,
    get_concept_revision as get_stored_concept_revision,
    get_relation as get_stored_relation,
    get_relation_revision as get_stored_relation_revision,
    list_concept_summaries_for_course,
    list_relation_summaries_for_course,
    mark_concept_revision_stale,
    merge_concept_identity,
    retire_concept_identity,
    mark_relation_revision_stale,
    review_concept_revision,
    review_relation_revision,
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


class ConceptGraphBusyError(ConceptGraphServiceError):
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
    return _run_graph_write(
        lambda: create_concept_candidate(
            concept,
            request.evidence,
            [uuid4().hex for _ in request.evidence],
            [uuid4().hex for _ in request.aliases],
            request.aliases,
            operation=request,
            request_hash=_create_hash(
                course_id=course.id,
                entity_type="concept",
                kind="concept_create",
                path="/courses/{course_id}/concepts",
                request=request,
            ),
        ),
        entity_type="concept",
    )


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


def get_course_concept_revision(
    course_id: str,
    concept_id: str,
    revision: int,
) -> Concept:
    course = _require_course(course_id)
    concept = get_stored_concept_revision(course.id, concept_id, revision)
    if concept is None:
        raise ConceptNotFoundError(
            "Concept revision not found in the selected course."
        )
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
    return _run_graph_write(
        lambda: create_relation_candidate(
            relation,
            evidence_requests,
            [uuid4().hex for _ in evidence_requests],
            operation=request,
            request_hash=_create_hash(
                course_id=course.id,
                entity_type="relation",
                kind="relation_create",
                path="/courses/{course_id}/concept-relations",
                request=request,
            ),
        ),
        entity_type="relation",
    )


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


def get_course_relation_revision(
    course_id: str,
    relation_id: str,
    revision: int,
) -> ConceptRelation:
    course = _require_course(course_id)
    relation = get_stored_relation_revision(course.id, relation_id, revision)
    if relation is None:
        raise ConceptRelationNotFoundError(
            "Concept relation revision not found in the selected course."
        )
    return relation


def edit_course_concept(
    course_id: str,
    concept_id: str,
    request: ConceptRevisionEdit,
) -> Concept:
    course = _require_course(course_id)
    return _run_graph_write(
        lambda: edit_concept_revision(
            course.id,
            concept_id,
            request,
            _mutation_hash(
                course_id=course.id,
                entity_type="concept",
                entity_id=concept_id,
                kind="concept_edit",
                path="/courses/{course_id}/concepts/{concept_id}",
                request=request,
            ),
        ),
        entity_type="concept",
    )


def review_course_concept(
    course_id: str,
    concept_id: str,
    request: GraphReviewRequest,
) -> Concept:
    course = _require_course(course_id)
    return _run_graph_write(
        lambda: review_concept_revision(
            course.id,
            concept_id,
            request,
            _mutation_hash(
                course_id=course.id,
                entity_type="concept",
                entity_id=concept_id,
                kind="concept_review",
                path="/courses/{course_id}/concepts/{concept_id}/review",
                request=request,
            ),
        ),
        entity_type="concept",
    )


def mark_course_concept_stale(
    course_id: str,
    concept_id: str,
    request: GraphMarkStaleRequest,
) -> Concept:
    course = _require_course(course_id)
    return _run_graph_write(
        lambda: mark_concept_revision_stale(
            course.id,
            concept_id,
            request,
            _mutation_hash(
                course_id=course.id,
                entity_type="concept",
                entity_id=concept_id,
                kind="concept_mark_stale",
                path="/courses/{course_id}/concepts/{concept_id}/mark-stale",
                request=request,
            ),
        ),
        entity_type="concept",
    )


def merge_course_concept(
    course_id: str,
    concept_id: str,
    request: ConceptMergeRequest,
) -> Concept:
    course = _require_course(course_id)
    return _run_graph_write(
        lambda: merge_concept_identity(
            course.id,
            concept_id,
            request,
            _mutation_hash(
                course_id=course.id,
                entity_type="concept",
                entity_id=concept_id,
                kind="concept_merge",
                path="/courses/{course_id}/concepts/{concept_id}/merge",
                request=request,
            ),
        ),
        entity_type="concept",
    )


def retire_course_concept(
    course_id: str,
    concept_id: str,
    request: ConceptRetireRequest,
) -> Concept:
    course = _require_course(course_id)
    return _run_graph_write(
        lambda: retire_concept_identity(
            course.id,
            concept_id,
            request,
            _mutation_hash(
                course_id=course.id,
                entity_type="concept",
                entity_id=concept_id,
                kind="concept_retire",
                path="/courses/{course_id}/concepts/{concept_id}/retire",
                request=request,
            ),
        ),
        entity_type="concept",
    )


def edit_course_relation(
    course_id: str,
    relation_id: str,
    request: RelationRevisionEdit,
) -> ConceptRelation:
    course = _require_course(course_id)
    return _run_graph_write(
        lambda: edit_relation_revision(
            course.id,
            relation_id,
            request,
            _mutation_hash(
                course_id=course.id,
                entity_type="relation",
                entity_id=relation_id,
                kind="relation_edit",
                path=(
                    "/courses/{course_id}/concept-relations/"
                    "{relation_id}"
                ),
                request=request,
            ),
        ),
        entity_type="relation",
    )


def review_course_relation(
    course_id: str,
    relation_id: str,
    request: RelationReviewRequest,
) -> ConceptRelation:
    course = _require_course(course_id)
    return _run_graph_write(
        lambda: review_relation_revision(
            course.id,
            relation_id,
            request,
            _mutation_hash(
                course_id=course.id,
                entity_type="relation",
                entity_id=relation_id,
                kind="relation_review",
                path=(
                    "/courses/{course_id}/concept-relations/"
                    "{relation_id}/review"
                ),
                request=request,
            ),
        ),
        entity_type="relation",
    )


def mark_course_relation_stale(
    course_id: str,
    relation_id: str,
    request: GraphMarkStaleRequest,
) -> ConceptRelation:
    course = _require_course(course_id)
    return _run_graph_write(
        lambda: mark_relation_revision_stale(
            course.id,
            relation_id,
            request,
            _mutation_hash(
                course_id=course.id,
                entity_type="relation",
                entity_id=relation_id,
                kind="relation_mark_stale",
                path=(
                    "/courses/{course_id}/concept-relations/"
                    "{relation_id}/mark-stale"
                ),
                request=request,
            ),
        ),
        entity_type="relation",
    )


GraphWriteResult = TypeVar("GraphWriteResult", Concept, ConceptRelation)


def _run_graph_write(
    operation: Callable[[], GraphWriteResult],
    *,
    entity_type: str,
) -> GraphWriteResult:
    try:
        return operation()
    except GraphEntityNotFoundError as exc:
        if entity_type == "concept":
            raise ConceptNotFoundError(str(exc)) from exc
        raise ConceptRelationNotFoundError(str(exc)) from exc
    except (EvidenceChunkNotFoundError, RelationEndpointNotFoundError) as exc:
        raise GraphEvidenceNotFoundError(str(exc)) from exc
    except (
        EvidenceQuoteMismatchError,
        RelationEvidenceMismatchError,
        GraphReviewTransitionError,
    ) as exc:
        raise InvalidConceptGraphRequestError(str(exc)) from exc
    except (
        DuplicateRelationError,
        RelationEvidenceDriftError,
        GraphOperationReuseError,
        GraphRevisionConflictError,
        GraphEvidenceStaleError,
        GraphMergeDependencyError,
        PrerequisiteCycleError,
    ) as exc:
        raise ConceptGraphConflictError(str(exc)) from exc
    except OperationalError as exc:
        if _is_sqlite_busy(exc):
            raise ConceptGraphBusyError(
                "Concept graph is busy; retry the operation."
            ) from exc
        raise ConceptGraphPersistenceError(
            "Concept graph persistence failed."
        ) from exc
    except IntegrityError as exc:
        raise ConceptGraphPersistenceError(
            "Concept graph persistence failed."
        ) from exc
    except Exception as exc:
        raise ConceptGraphPersistenceError(
            "Concept graph persistence failed."
        ) from exc


def _mutation_hash(
    *,
    course_id: str,
    entity_type: str,
    entity_id: str,
    kind: str,
    path: str,
    request: GraphMutationRequest,
) -> str:
    payload = {
        "protocol": "concept-graph-mutation-v1",
        "course_id": course_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "kind": kind,
        "path": path,
        "request": request.model_dump(mode="json"),
    }
    return _canonical_hash(payload)


def _create_hash(
    *,
    course_id: str,
    entity_type: str,
    kind: str,
    path: str,
    request: ConceptCreate | ConceptRelationCreate,
) -> str:
    payload = {
        "protocol": "concept-graph-create-v1",
        "course_id": course_id,
        "entity_type": entity_type,
        "kind": kind,
        "path": path,
        "request": request.model_dump(mode="json"),
    }
    return _canonical_hash(payload)


def _canonical_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_sqlite_busy(exc: OperationalError) -> bool:
    error_code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(error_code, int):
        primary_code = error_code & 0xFF
        return primary_code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
    return str(exc).strip().lower() in {
        "database is locked",
        "database table is locked",
        "database is busy",
        "database table is busy",
    }


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
