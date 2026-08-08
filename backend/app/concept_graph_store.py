from __future__ import annotations

import json
from datetime import datetime
from sqlite3 import Connection, Row
from uuid import uuid4

from .concept_graph import (
    Concept,
    ConceptAlias,
    ConceptEvidence,
    ConceptMergeRequest,
    ConceptRetireRequest,
    ConceptRevisionEdit,
    ConceptRelation,
    ConceptRelationSummary,
    ConceptSummary,
    EvidenceReferenceCreate,
    GraphMarkStaleRequest,
    GraphOperationRequest,
    GraphReviewRequest,
    RelationEndpointRevisionBinding,
    RelationEvidence,
    RelationEvidenceReferenceCreate,
    RelationReviewRequest,
    RelationRevisionEdit,
    normalize_alias_key,
)
from .course_source import hash_source_chunk_text
from .db import connect, ensure_db
from .source_projection_identity import canonical_source_locator_json
from .job import utc_now


class ConceptGraphStoreError(RuntimeError):
    pass


class EvidenceChunkNotFoundError(ConceptGraphStoreError):
    pass


class EvidenceQuoteMismatchError(ConceptGraphStoreError):
    pass


class RelationEndpointNotFoundError(ConceptGraphStoreError):
    pass


class RelationEvidenceMismatchError(ConceptGraphStoreError):
    pass


class RelationEvidenceDriftError(ConceptGraphStoreError):
    pass


class DuplicateRelationError(ConceptGraphStoreError):
    pass


class GraphRevisionConflictError(ConceptGraphStoreError):
    pass


class GraphReviewTransitionError(ConceptGraphStoreError):
    pass


class GraphOperationReuseError(ConceptGraphStoreError):
    pass


class GraphMergeDependencyError(ConceptGraphStoreError):
    pass


class PrerequisiteCycleError(ConceptGraphStoreError):
    pass


class GraphEntityNotFoundError(ConceptGraphStoreError):
    pass


class GraphEvidenceStaleError(ConceptGraphStoreError):
    pass


EvidenceFingerprint = tuple[str, str, str, str, str, str | None]


def _source_root_is_current_sql(source_alias: str) -> str:
    return f"""
    (
        EXISTS (
            SELECT 1 FROM courses
            WHERE courses.id = {source_alias}.course_id
              AND courses.deleted_at IS NULL
        )
        AND (
        ({source_alias}.origin_type = 'video_job' AND EXISTS (
            SELECT 1 FROM jobs
            WHERE jobs.id = {source_alias}.origin_id
              AND jobs.course_id = {source_alias}.course_id
              AND jobs.deleted_at IS NULL
        ))
        OR
        ({source_alias}.origin_type = 'source_asset' AND EXISTS (
            SELECT 1 FROM source_assets
            WHERE source_assets.id = {source_alias}.origin_id
              AND source_assets.course_id = {source_alias}.course_id
              AND source_assets.deleted_at IS NULL
        ))
        OR
        ({source_alias}.origin_type = 'notebook_note' AND EXISTS (
            SELECT 1 FROM notebook_notes
            WHERE notebook_notes.id = {source_alias}.origin_id
              AND notebook_notes.course_id = {source_alias}.course_id
              AND notebook_notes.deleted_at IS NULL
        ))
        )
    )
    """


def _datetime_to_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _datetime_from_text(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def create_concept_candidate(
    concept: Concept,
    evidence_requests: list[EvidenceReferenceCreate],
    evidence_ids: list[str],
    alias_ids: list[str] | None = None,
    alias_names: list[str] | None = None,
    *,
    operation: GraphOperationRequest,
    request_hash: str,
) -> Concept:
    if len(evidence_requests) != len(evidence_ids):
        raise ValueError("Every Concept evidence reference needs an id.")

    if alias_ids is None:
        alias_ids = []
    if alias_names is None:
        alias_names = []
    if len(alias_names) != len(alias_ids):
        raise ValueError("Every Concept alias needs an id.")

    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        replay = _replay_create_operation(
            conn,
            course_id=concept.course_id,
            operation_id=operation.operation_id,
            request_hash=request_hash,
            kind="concept_create",
            entity_type="concept",
        )
        if replay is not None:
            entity_id, revision = replay
            return _load_concept_revision_or_raise(
                conn, concept.course_id, entity_id, revision
            )
        _insert_concept_identity(conn, concept)
        _insert_concept_revision(conn, concept)
        aliases = _build_aliases(
            concept,
            alias_names,
            alias_ids,
        )
        for alias in aliases:
            _insert_concept_alias(conn, alias)
        evidence = [
            _snapshot_concept_evidence(
                conn,
                concept=concept,
                request=request,
                evidence_id=evidence_ids[ordinal],
                ordinal=ordinal,
            )
            for ordinal, request in enumerate(evidence_requests)
        ]
        for item in evidence:
            _insert_concept_evidence(conn, item)
        _record_operation(
            conn,
            course_id=concept.course_id,
            operation_id=operation.operation_id,
            kind="concept_create",
            request_hash=request_hash,
            actor=operation.actor,
            reason=operation.reason,
            entity_type="concept",
            entity_id=concept.id,
            result_revision=concept.revision,
            created_at=concept.created_at,
        )
        row = _select_concept_revision(
            conn, concept.course_id, concept.id, concept.revision
        )
        assert row is not None
        return _load_concept(conn, row)


def create_relation_candidate(
    relation: ConceptRelation,
    evidence_requests: list[RelationEvidenceReferenceCreate],
    evidence_ids: list[str],
    *,
    operation: GraphOperationRequest,
    request_hash: str,
) -> ConceptRelation:
    if len(evidence_requests) != len(evidence_ids):
        raise ValueError("Every relation evidence reference needs an id.")

    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        replay = _replay_create_operation(
            conn,
            course_id=relation.course_id,
            operation_id=operation.operation_id,
            request_hash=request_hash,
            kind="relation_create",
            entity_type="relation",
        )
        if replay is not None:
            entity_id, revision = replay
            return _load_relation_revision_or_raise(
                conn, relation.course_id, entity_id, revision
            )
        endpoint_evidence = _require_relation_endpoints(conn, relation)
        _require_relation_identity_available(conn, relation)
        _insert_relation_identity(conn, relation)
        _insert_relation_revision(conn, relation)
        _bind_relation_to_current_endpoints(conn, relation)
        evidence = [
            _snapshot_relation_evidence(
                conn,
                relation=relation,
                request=request,
                evidence_id=evidence_ids[ordinal],
                ordinal=ordinal,
            )
            for ordinal, request in enumerate(evidence_requests)
        ]
        _require_relation_support(relation, evidence, endpoint_evidence)
        for item in evidence:
            _insert_relation_evidence(conn, item)
        _record_operation(
            conn,
            course_id=relation.course_id,
            operation_id=operation.operation_id,
            kind="relation_create",
            request_hash=request_hash,
            actor=operation.actor,
            reason=operation.reason,
            entity_type="relation",
            entity_id=relation.id,
            result_revision=relation.revision,
            created_at=relation.created_at,
        )
        row = _select_relation_revision(
            conn, relation.course_id, relation.id, relation.revision
        )
        assert row is not None
        return _load_relation(conn, row)


def edit_concept_revision(
    course_id: str,
    concept_id: str,
    request: ConceptRevisionEdit,
    request_hash: str,
) -> Concept:
    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        replay = _replay_operation(
            conn,
            course_id=course_id,
            operation_id=request.operation_id,
            request_hash=request_hash,
            kind="concept_edit",
            entity_type="concept",
            entity_id=concept_id,
        )
        if replay is not None:
            return _load_concept_revision_or_raise(
                conn, course_id, concept_id, replay
            )
        current = _require_current_concept_row(conn, course_id, concept_id)
        _require_expected_revision(current, request.expected_revision)
        _require_active_concept_identity(current, action="edited")

        now = utc_now()
        revision = int(current["revision"]) + 1
        concept = Concept(
            id=concept_id,
            course_id=course_id,
            revision=revision,
            preferred_name=request.preferred_name,
            short_definition=request.short_definition,
            identity_status="active",
            review_status="candidate",
            validity_status="current",
            proposal_origin="human",
            created_at=now,
            updated_at=now,
        )
        _insert_concept_revision(conn, concept)
        aliases = _build_aliases(
            concept,
            request.aliases,
            [uuid4().hex for _ in request.aliases],
        )
        for alias in aliases:
            _insert_concept_alias(conn, alias)
        evidence = [
            _snapshot_concept_evidence(
                conn,
                concept=concept,
                request=item,
                evidence_id=uuid4().hex,
                ordinal=ordinal,
            )
            for ordinal, item in enumerate(request.evidence)
        ]
        for item in evidence:
            _insert_concept_evidence(conn, item)
        _stale_incident_relations(
            conn,
            course_id=course_id,
            concept_id=concept_id,
            now=now,
        )
        _advance_concept_head(
            conn, course_id, concept_id, request.expected_revision, revision, now
        )
        _record_operation(
            conn,
            course_id=course_id,
            operation_id=request.operation_id,
            kind="concept_edit",
            request_hash=request_hash,
            actor=request.actor,
            reason=request.reason,
            entity_type="concept",
            entity_id=concept_id,
            result_revision=revision,
            created_at=now,
        )
        row = _select_concept_revision(conn, course_id, concept_id, revision)
        assert row is not None
        return _load_concept(conn, row)


def review_concept_revision(
    course_id: str,
    concept_id: str,
    request: GraphReviewRequest,
    request_hash: str,
) -> Concept:
    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        replay = _replay_operation(
            conn,
            course_id=course_id,
            operation_id=request.operation_id,
            request_hash=request_hash,
            kind="concept_review",
            entity_type="concept",
            entity_id=concept_id,
        )
        if replay is not None:
            return _load_concept_revision_or_raise(
                conn, course_id, concept_id, replay
            )
        current = _require_current_concept_row(conn, course_id, concept_id)
        _require_expected_revision(current, request.expected_revision)
        _require_active_concept_identity(current, action="reviewed")
        if current["review_status"] != "candidate":
            raise GraphReviewTransitionError(
                "Only a candidate Concept can be reviewed."
            )
        if request.decision == "accept":
            if current["validity_status"] != "current":
                raise GraphReviewTransitionError(
                    "Only a current Concept candidate can be accepted."
                )
            _require_current_concept_evidence(
                conn, concept_id, int(current["revision"])
            )

        now = utc_now()
        revision = int(current["revision"]) + 1
        concept = _copy_concept_revision(
            current,
            revision=revision,
            review_status=(
                "accepted" if request.decision == "accept" else "rejected"
            ),
            review_actor=request.actor,
            reviewed_at=now,
            review_revision=int(current["revision"]),
            now=now,
        )
        _insert_concept_revision(conn, concept)
        _copy_concept_children(
            conn,
            concept=concept,
            source_revision=int(current["revision"]),
        )
        _stale_incident_relations(
            conn,
            course_id=course_id,
            concept_id=concept_id,
            now=now,
        )
        _advance_concept_head(
            conn, course_id, concept_id, request.expected_revision, revision, now
        )
        _record_operation(
            conn,
            course_id=course_id,
            operation_id=request.operation_id,
            kind="concept_review",
            request_hash=request_hash,
            actor=request.actor,
            reason=request.reason,
            entity_type="concept",
            entity_id=concept_id,
            result_revision=revision,
            created_at=now,
        )
        row = _select_concept_revision(conn, course_id, concept_id, revision)
        assert row is not None
        return _load_concept(conn, row)


def mark_concept_revision_stale(
    course_id: str,
    concept_id: str,
    request: GraphMarkStaleRequest,
    request_hash: str,
) -> Concept:
    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        replay = _replay_operation(
            conn,
            course_id=course_id,
            operation_id=request.operation_id,
            request_hash=request_hash,
            kind="concept_mark_stale",
            entity_type="concept",
            entity_id=concept_id,
        )
        if replay is not None:
            return _load_concept_revision_or_raise(
                conn, course_id, concept_id, replay
            )
        current = _require_current_concept_row(conn, course_id, concept_id)
        _require_expected_revision(current, request.expected_revision)
        _require_active_concept_identity(current, action="marked stale")
        if current["validity_status"] != "current":
            raise GraphReviewTransitionError(
                "Only a current Concept revision can be marked stale."
            )
        now = utc_now()
        revision = int(current["revision"]) + 1
        concept = _copy_concept_revision(
            current,
            revision=revision,
            validity_status="stale",
            now=now,
        )
        _insert_concept_revision(conn, concept)
        _copy_concept_children(
            conn,
            concept=concept,
            source_revision=int(current["revision"]),
        )
        _stale_incident_relations(
            conn,
            course_id=course_id,
            concept_id=concept_id,
            now=now,
        )
        _advance_concept_head(
            conn, course_id, concept_id, request.expected_revision, revision, now
        )
        _record_operation(
            conn,
            course_id=course_id,
            operation_id=request.operation_id,
            kind="concept_mark_stale",
            request_hash=request_hash,
            actor=request.actor,
            reason=request.reason,
            entity_type="concept",
            entity_id=concept_id,
            result_revision=revision,
            created_at=now,
        )
        row = _select_concept_revision(conn, course_id, concept_id, revision)
        assert row is not None
        return _load_concept(conn, row)


def merge_concept_identity(
    course_id: str,
    concept_id: str,
    request: ConceptMergeRequest,
    request_hash: str,
) -> Concept:
    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        replay = _replay_operation(
            conn,
            course_id=course_id,
            operation_id=request.operation_id,
            request_hash=request_hash,
            kind="concept_merge",
            entity_type="concept",
            entity_id=concept_id,
        )
        if replay is not None:
            return _load_concept_revision_or_raise(
                conn, course_id, concept_id, replay
            )
        source = _require_current_concept_row(conn, course_id, concept_id)
        _require_expected_revision(source, request.expected_revision)
        _require_active_concept_identity(source, action="merged")
        if concept_id == request.survivor_concept_id:
            raise GraphReviewTransitionError(
                "A Concept cannot be merged into itself."
            )
        survivor = _require_current_concept_row(
            conn, course_id, request.survivor_concept_id
        )
        _require_expected_revision(
            survivor, request.expected_survivor_revision
        )
        _require_active_concept_identity(survivor, action="used as survivor")
        _require_no_incoming_merge_redirects(conn, course_id, concept_id)

        now = utc_now()
        revision = int(source["revision"]) + 1
        concept = _copy_concept_revision(
            source,
            revision=revision,
            identity_status="merged",
            merged_into_concept_id=request.survivor_concept_id,
            now=now,
        )
        _insert_concept_revision(conn, concept)
        _copy_concept_children(
            conn, concept=concept, source_revision=int(source["revision"])
        )
        _stale_incident_relations(
            conn, course_id=course_id, concept_id=concept_id, now=now
        )
        _advance_concept_head(
            conn,
            course_id,
            concept_id,
            request.expected_revision,
            revision,
            now,
        )
        _record_operation(
            conn,
            course_id=course_id,
            operation_id=request.operation_id,
            kind="concept_merge",
            request_hash=request_hash,
            actor=request.actor,
            reason=request.reason,
            entity_type="concept",
            entity_id=concept_id,
            result_revision=revision,
            created_at=now,
        )
        row = _select_concept_revision(conn, course_id, concept_id, revision)
        assert row is not None
        return _load_concept(conn, row)


def retire_concept_identity(
    course_id: str,
    concept_id: str,
    request: ConceptRetireRequest,
    request_hash: str,
) -> Concept:
    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        replay = _replay_operation(
            conn,
            course_id=course_id,
            operation_id=request.operation_id,
            request_hash=request_hash,
            kind="concept_retire",
            entity_type="concept",
            entity_id=concept_id,
        )
        if replay is not None:
            return _load_concept_revision_or_raise(
                conn, course_id, concept_id, replay
            )
        current = _require_current_concept_row(conn, course_id, concept_id)
        _require_expected_revision(current, request.expected_revision)
        _require_active_concept_identity(current, action="retired")
        _require_no_incoming_merge_redirects(conn, course_id, concept_id)

        now = utc_now()
        revision = int(current["revision"]) + 1
        concept = _copy_concept_revision(
            current,
            revision=revision,
            identity_status="retired",
            merged_into_concept_id=None,
            now=now,
        )
        _insert_concept_revision(conn, concept)
        _copy_concept_children(
            conn, concept=concept, source_revision=int(current["revision"])
        )
        _stale_incident_relations(
            conn, course_id=course_id, concept_id=concept_id, now=now
        )
        _advance_concept_head(
            conn,
            course_id,
            concept_id,
            request.expected_revision,
            revision,
            now,
        )
        _record_operation(
            conn,
            course_id=course_id,
            operation_id=request.operation_id,
            kind="concept_retire",
            request_hash=request_hash,
            actor=request.actor,
            reason=request.reason,
            entity_type="concept",
            entity_id=concept_id,
            result_revision=revision,
            created_at=now,
        )
        row = _select_concept_revision(conn, course_id, concept_id, revision)
        assert row is not None
        return _load_concept(conn, row)


def edit_relation_revision(
    course_id: str,
    relation_id: str,
    request: RelationRevisionEdit,
    request_hash: str,
) -> ConceptRelation:
    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        replay = _replay_operation(
            conn,
            course_id=course_id,
            operation_id=request.operation_id,
            request_hash=request_hash,
            kind="relation_edit",
            entity_type="relation",
            entity_id=relation_id,
        )
        if replay is not None:
            return _load_relation_revision_or_raise(
                conn, course_id, relation_id, replay
            )
        current = _require_current_relation_row(conn, course_id, relation_id)
        _require_expected_revision(current, request.expected_revision)
        endpoint_evidence = _require_expected_relation_endpoints(
            conn,
            current,
            request.expected_source_concept_revision,
            request.expected_target_concept_revision,
            require_accepted=False,
        )
        now = utc_now()
        revision = int(current["revision"]) + 1
        relation = ConceptRelation(
            id=relation_id,
            course_id=course_id,
            revision=revision,
            source_concept_id=current["source_concept_id"],
            target_concept_id=current["target_concept_id"],
            relation_type=current["relation_type"],
            support_basis=request.support_basis,
            rationale=request.rationale,
            review_status="candidate",
            validity_status="current",
            proposal_origin="human",
            created_at=now,
            updated_at=now,
        )
        _insert_relation_revision(conn, relation)
        evidence = [
            _snapshot_relation_evidence(
                conn,
                relation=relation,
                request=item,
                evidence_id=uuid4().hex,
                ordinal=ordinal,
            )
            for ordinal, item in enumerate(request.evidence)
        ]
        _require_relation_support(relation, evidence, endpoint_evidence)
        for item in evidence:
            _insert_relation_evidence(conn, item)
        _insert_relation_endpoint_binding(
            conn,
            relation,
            request.expected_source_concept_revision,
            request.expected_target_concept_revision,
            now,
        )
        _advance_relation_head(
            conn, course_id, relation_id, request.expected_revision, revision, now
        )
        _record_operation(
            conn,
            course_id=course_id,
            operation_id=request.operation_id,
            kind="relation_edit",
            request_hash=request_hash,
            actor=request.actor,
            reason=request.reason,
            entity_type="relation",
            entity_id=relation_id,
            result_revision=revision,
            created_at=now,
        )
        row = _select_relation_revision(conn, course_id, relation_id, revision)
        assert row is not None
        return _load_relation(conn, row)


def review_relation_revision(
    course_id: str,
    relation_id: str,
    request: RelationReviewRequest,
    request_hash: str,
) -> ConceptRelation:
    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        replay = _replay_operation(
            conn,
            course_id=course_id,
            operation_id=request.operation_id,
            request_hash=request_hash,
            kind="relation_review",
            entity_type="relation",
            entity_id=relation_id,
        )
        if replay is not None:
            return _load_relation_revision_or_raise(
                conn, course_id, relation_id, replay
            )
        current = _require_current_relation_row(conn, course_id, relation_id)
        _require_expected_revision(current, request.expected_revision)
        if current["review_status"] != "candidate":
            raise GraphReviewTransitionError(
                "Only a candidate relation can be reviewed."
            )
        binding = _get_relation_endpoint_binding(
            conn, relation_id, int(current["revision"])
        )
        if binding is None:
            if request.decision == "accept":
                raise GraphReviewTransitionError(
                    "A legacy relation without endpoint revision binding "
                    "must be edited and regrounded before acceptance."
                )
        elif (
            binding.source_concept_revision
            != request.expected_source_concept_revision
            or binding.target_concept_revision
            != request.expected_target_concept_revision
        ):
            raise GraphRevisionConflictError(
                "The reviewed endpoint revisions no longer match the "
                "relation candidate binding."
            )
        evidence = _list_relation_revision_evidence(
            conn, relation_id, int(current["revision"])
        )
        if request.decision == "accept":
            endpoint_evidence = _require_expected_relation_endpoints(
                conn,
                current,
                request.expected_source_concept_revision,
                request.expected_target_concept_revision,
                require_accepted=True,
            )
            if current["validity_status"] != "current":
                raise GraphReviewTransitionError(
                    "Only a current relation candidate can be accepted."
                )
            _require_current_relation_evidence(evidence)
            relation_for_support = _row_to_relation(current, evidence)
            _require_relation_support(
                relation_for_support, evidence, endpoint_evidence
            )
            if current["relation_type"] == "prerequisite":
                _require_no_prerequisite_cycle(
                    conn,
                    course_id=course_id,
                    relation_id=relation_id,
                    source_concept_id=str(current["source_concept_id"]),
                    target_concept_id=str(current["target_concept_id"]),
                )

        now = utc_now()
        revision = int(current["revision"]) + 1
        relation = _copy_relation_revision(
            current,
            revision=revision,
            review_status=(
                "accepted" if request.decision == "accept" else "rejected"
            ),
            review_actor=request.actor,
            reviewed_at=now,
            review_revision=int(current["revision"]),
            now=now,
        )
        _insert_relation_revision(conn, relation)
        _copy_relation_evidence(
            conn,
            relation=relation,
            source_revision=int(current["revision"]),
        )
        if request.decision == "accept":
            assert binding is not None
        _copy_relation_binding(
            conn,
            relation=relation,
            source_revision=int(current["revision"]),
        )
        _advance_relation_head(
            conn, course_id, relation_id, request.expected_revision, revision, now
        )
        _record_operation(
            conn,
            course_id=course_id,
            operation_id=request.operation_id,
            kind="relation_review",
            request_hash=request_hash,
            actor=request.actor,
            reason=request.reason,
            entity_type="relation",
            entity_id=relation_id,
            result_revision=revision,
            created_at=now,
        )
        row = _select_relation_revision(conn, course_id, relation_id, revision)
        assert row is not None
        return _load_relation(conn, row)


def mark_relation_revision_stale(
    course_id: str,
    relation_id: str,
    request: GraphMarkStaleRequest,
    request_hash: str,
) -> ConceptRelation:
    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        replay = _replay_operation(
            conn,
            course_id=course_id,
            operation_id=request.operation_id,
            request_hash=request_hash,
            kind="relation_mark_stale",
            entity_type="relation",
            entity_id=relation_id,
        )
        if replay is not None:
            return _load_relation_revision_or_raise(
                conn, course_id, relation_id, replay
            )
        current = _require_current_relation_row(conn, course_id, relation_id)
        _require_expected_revision(current, request.expected_revision)
        if current["validity_status"] != "current":
            raise GraphReviewTransitionError(
                "Only a current relation revision can be marked stale."
            )
        now = utc_now()
        revision = int(current["revision"]) + 1
        relation = _copy_relation_revision(
            current,
            revision=revision,
            validity_status="stale",
            now=now,
        )
        _insert_relation_revision(conn, relation)
        _copy_relation_evidence(
            conn,
            relation=relation,
            source_revision=int(current["revision"]),
        )
        _copy_relation_binding(
            conn,
            relation=relation,
            source_revision=int(current["revision"]),
        )
        _advance_relation_head(
            conn, course_id, relation_id, request.expected_revision, revision, now
        )
        _record_operation(
            conn,
            course_id=course_id,
            operation_id=request.operation_id,
            kind="relation_mark_stale",
            request_hash=request_hash,
            actor=request.actor,
            reason=request.reason,
            entity_type="relation",
            entity_id=relation_id,
            result_revision=revision,
            created_at=now,
        )
        row = _select_relation_revision(conn, course_id, relation_id, revision)
        assert row is not None
        return _load_relation(conn, row)


def get_concept(course_id: str, concept_id: str) -> Concept | None:
    ensure_db()
    with connect() as conn:
        row = conn.execute(
            _CONCEPT_CURRENT_SELECT
            + " WHERE concepts.course_id = ? AND concepts.id = ?",
            (course_id, concept_id),
        ).fetchone()
        if row is None:
            return None
        return _load_concept(conn, row)


def get_concept_revision(
    course_id: str,
    concept_id: str,
    revision: int,
) -> Concept | None:
    ensure_db()
    with connect() as conn:
        row = _select_concept_revision(
            conn,
            course_id,
            concept_id,
            revision,
        )
        return _load_concept(conn, row) if row is not None else None


def list_concept_summaries_for_course(
    course_id: str,
    *,
    limit: int,
    cursor: str | None,
) -> tuple[list[ConceptSummary], str | None]:
    _validate_page(limit, cursor)
    ensure_db()
    where = " WHERE concepts.course_id = ?"
    parameters: list[object] = [course_id]
    if cursor is not None:
        where += " AND concepts.id > ?"
        parameters.append(cursor)
    parameters.append(limit + 1)
    with connect() as conn:
        rows = conn.execute(
            _CONCEPT_CURRENT_SELECT
            + where
            + " ORDER BY concepts.id LIMIT ?",
            parameters,
        ).fetchall()
    page_rows = rows[:limit]
    next_cursor = (
        str(page_rows[-1]["id"])
        if len(rows) > limit and page_rows
        else None
    )
    return [_row_to_concept_summary(row) for row in page_rows], next_cursor


def get_relation(
    course_id: str,
    relation_id: str,
) -> ConceptRelation | None:
    ensure_db()
    with connect() as conn:
        row = conn.execute(
            _RELATION_CURRENT_SELECT
            + " WHERE relations.course_id = ? AND relations.id = ?",
            (course_id, relation_id),
        ).fetchone()
        if row is None:
            return None
        return _load_relation(conn, row)


def get_relation_revision(
    course_id: str,
    relation_id: str,
    revision: int,
) -> ConceptRelation | None:
    ensure_db()
    with connect() as conn:
        row = _select_relation_revision(
            conn,
            course_id,
            relation_id,
            revision,
        )
        return _load_relation(conn, row) if row is not None else None


def list_relation_summaries_for_course(
    course_id: str,
    *,
    limit: int,
    cursor: str | None,
) -> tuple[list[ConceptRelationSummary], str | None]:
    _validate_page(limit, cursor)
    ensure_db()
    where = " WHERE relations.course_id = ?"
    parameters: list[object] = [course_id]
    if cursor is not None:
        where += " AND relations.id > ?"
        parameters.append(cursor)
    parameters.append(limit + 1)
    with connect() as conn:
        rows = conn.execute(
            _RELATION_CURRENT_SELECT
            + where
            + " ORDER BY relations.id LIMIT ?",
            parameters,
        ).fetchall()
    page_rows = rows[:limit]
    next_cursor = (
        str(page_rows[-1]["id"])
        if len(rows) > limit and page_rows
        else None
    )
    return [_row_to_relation_summary(row) for row in page_rows], next_cursor


def clear_concept_graph() -> None:
    ensure_db()
    with connect() as conn:
        conn.execute("DELETE FROM concept_graph_operations")
        conn.execute("DELETE FROM relation_endpoint_revisions")
        conn.execute("DELETE FROM relation_evidence")
        conn.execute("DELETE FROM concept_relation_revisions")
        conn.execute("DELETE FROM concept_relations")
        conn.execute("DELETE FROM concept_aliases")
        conn.execute("DELETE FROM concept_evidence")
        conn.execute("DELETE FROM concept_revisions")
        conn.execute("DELETE FROM concepts")


def _build_aliases(
    concept: Concept,
    names: list[str],
    alias_ids: list[str],
) -> list[ConceptAlias]:
    return [
        ConceptAlias(
            id=alias_ids[ordinal],
            course_id=concept.course_id,
            concept_id=concept.id,
            concept_revision=concept.revision,
            display_text=name,
            normalized_text=normalize_alias_key(name),
            ordinal=ordinal,
            created_at=concept.created_at,
        )
        for ordinal, name in enumerate(names)
    ]


def _insert_concept_alias(conn: Connection, alias: ConceptAlias) -> None:
    conn.execute(
        """
        INSERT INTO concept_aliases (
            id, course_id, concept_id, concept_revision, display_text,
            normalized_text, ordinal, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            alias.id,
            alias.course_id,
            alias.concept_id,
            alias.concept_revision,
            alias.display_text,
            alias.normalized_text,
            alias.ordinal,
            _datetime_to_text(alias.created_at),
        ),
    )


def _list_concept_aliases(
    conn: Connection,
    concept_id: str,
    revision: int,
) -> list[ConceptAlias]:
    rows = conn.execute(
        """
        SELECT * FROM concept_aliases
        WHERE concept_id = ? AND concept_revision = ?
        ORDER BY ordinal, id
        """,
        (concept_id, revision),
    ).fetchall()
    return [
        ConceptAlias(
            id=row["id"],
            course_id=row["course_id"],
            concept_id=row["concept_id"],
            concept_revision=row["concept_revision"],
            display_text=row["display_text"],
            normalized_text=row["normalized_text"],
            ordinal=row["ordinal"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
        for row in rows
    ]


def _bind_relation_to_current_endpoints(
    conn: Connection,
    relation: ConceptRelation,
) -> RelationEndpointRevisionBinding:
    rows = conn.execute(
        """
        SELECT id, current_revision
        FROM concepts
        WHERE course_id = ? AND id IN (?, ?)
        """,
        (
            relation.course_id,
            relation.source_concept_id,
            relation.target_concept_id,
        ),
    ).fetchall()
    revisions = {str(row["id"]): int(row["current_revision"]) for row in rows}
    if set(revisions) != {
        relation.source_concept_id,
        relation.target_concept_id,
    }:
        raise RelationEndpointNotFoundError(
            "Both relation endpoints must exist in the selected course."
        )
    return _insert_relation_endpoint_binding(
        conn,
        relation,
        revisions[relation.source_concept_id],
        revisions[relation.target_concept_id],
        relation.created_at,
    )


def _insert_relation_endpoint_binding(
    conn: Connection,
    relation: ConceptRelation,
    source_revision: int,
    target_revision: int,
    created_at: datetime,
) -> RelationEndpointRevisionBinding:
    binding = RelationEndpointRevisionBinding(
        relation_id=relation.id,
        course_id=relation.course_id,
        relation_revision=relation.revision,
        source_concept_id=relation.source_concept_id,
        source_concept_revision=source_revision,
        target_concept_id=relation.target_concept_id,
        target_concept_revision=target_revision,
        created_at=created_at,
    )
    conn.execute(
        """
        INSERT INTO relation_endpoint_revisions (
            relation_id, course_id, relation_revision,
            source_concept_id, source_concept_revision,
            target_concept_id, target_concept_revision, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            binding.relation_id,
            binding.course_id,
            binding.relation_revision,
            binding.source_concept_id,
            binding.source_concept_revision,
            binding.target_concept_id,
            binding.target_concept_revision,
            _datetime_to_text(binding.created_at),
        ),
    )
    return binding


def _get_relation_endpoint_binding(
    conn: Connection,
    relation_id: str,
    revision: int,
) -> RelationEndpointRevisionBinding | None:
    row = conn.execute(
        """
        SELECT * FROM relation_endpoint_revisions
        WHERE relation_id = ? AND relation_revision = ?
        """,
        (relation_id, revision),
    ).fetchone()
    if row is None:
        return None
    return RelationEndpointRevisionBinding(
        relation_id=row["relation_id"],
        course_id=row["course_id"],
        relation_revision=row["relation_revision"],
        source_concept_id=row["source_concept_id"],
        source_concept_revision=row["source_concept_revision"],
        target_concept_id=row["target_concept_id"],
        target_concept_revision=row["target_concept_revision"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _select_concept_revision(
    conn: Connection,
    course_id: str,
    concept_id: str,
    revision: int,
) -> Row | None:
    return conn.execute(
        _CONCEPT_REVISION_SELECT
        + " WHERE concepts.course_id = ? AND concepts.id = ? "
        + "AND revisions.revision = ?",
        (course_id, concept_id, revision),
    ).fetchone()


def _select_relation_revision(
    conn: Connection,
    course_id: str,
    relation_id: str,
    revision: int,
) -> Row | None:
    return conn.execute(
        _RELATION_REVISION_SELECT
        + " WHERE relations.course_id = ? AND relations.id = ? "
        + "AND revisions.revision = ?",
        (course_id, relation_id, revision),
    ).fetchone()


def _require_current_concept_row(
    conn: Connection,
    course_id: str,
    concept_id: str,
) -> Row:
    row = conn.execute(
        _CONCEPT_CURRENT_SELECT
        + " WHERE concepts.course_id = ? AND concepts.id = ?",
        (course_id, concept_id),
    ).fetchone()
    if row is None:
        raise GraphEntityNotFoundError(
            "Concept not found in the selected course."
        )
    return row


def _require_current_relation_row(
    conn: Connection,
    course_id: str,
    relation_id: str,
) -> Row:
    row = conn.execute(
        _RELATION_CURRENT_SELECT
        + " WHERE relations.course_id = ? AND relations.id = ?",
        (course_id, relation_id),
    ).fetchone()
    if row is None:
        raise GraphEntityNotFoundError(
            "Concept relation not found in the selected course."
        )
    return row


def _require_expected_revision(row: Row, expected_revision: int) -> None:
    if int(row["revision"]) != expected_revision:
        raise GraphRevisionConflictError(
            "The expected revision is no longer current."
        )


def _require_active_concept_identity(row: Row, *, action: str) -> None:
    if row["identity_status"] != "active":
        raise GraphReviewTransitionError(
            f"Only an active Concept can be {action}."
        )


def _require_no_incoming_merge_redirects(
    conn: Connection,
    course_id: str,
    concept_id: str,
) -> None:
    row = conn.execute(
        """
        SELECT merged.id
        FROM concepts AS merged
        INNER JOIN concept_revisions AS revisions
            ON revisions.concept_id = merged.id
           AND revisions.course_id = merged.course_id
           AND revisions.revision = merged.current_revision
        WHERE merged.course_id = ?
          AND revisions.identity_status = 'merged'
          AND revisions.merged_into_concept_id = ?
        LIMIT 1
        """,
        (course_id, concept_id),
    ).fetchone()
    if row is not None:
        raise GraphMergeDependencyError(
            "A Concept with incoming merge redirects cannot be merged or "
            "retired."
        )


def _copy_concept_revision(
    row: Row,
    *,
    revision: int,
    now: datetime,
    **changes: object,
) -> Concept:
    values: dict[str, object] = {
        "id": row["id"],
        "course_id": row["course_id"],
        "revision": revision,
        "preferred_name": row["preferred_name"],
        "short_definition": row["short_definition"],
        "identity_status": row["identity_status"],
        "merged_into_concept_id": row["merged_into_concept_id"],
        "review_status": row["review_status"],
        "validity_status": row["validity_status"],
        "proposal_origin": row["proposal_origin"],
        "provider": row["provider"],
        "model": row["model"],
        "prompt_protocol": row["prompt_protocol"],
        "output_version": row["output_version"],
        "review_actor": row["review_actor"],
        "reviewed_at": _datetime_from_text(row["reviewed_at"]),
        "review_revision": row["review_revision"],
        "created_at": now,
        "updated_at": now,
    }
    values.update(changes)
    return Concept(**values)


def _copy_relation_revision(
    row: Row,
    *,
    revision: int,
    now: datetime,
    **changes: object,
) -> ConceptRelation:
    values: dict[str, object] = {
        "id": row["id"],
        "course_id": row["course_id"],
        "revision": revision,
        "source_concept_id": row["source_concept_id"],
        "target_concept_id": row["target_concept_id"],
        "relation_type": row["relation_type"],
        "support_basis": row["support_basis"],
        "rationale": row["rationale"],
        "review_status": row["review_status"],
        "validity_status": row["validity_status"],
        "proposal_origin": row["proposal_origin"],
        "provider": row["provider"],
        "model": row["model"],
        "prompt_protocol": row["prompt_protocol"],
        "output_version": row["output_version"],
        "review_actor": row["review_actor"],
        "reviewed_at": _datetime_from_text(row["reviewed_at"]),
        "review_revision": row["review_revision"],
        "created_at": now,
        "updated_at": now,
    }
    values.update(changes)
    return ConceptRelation(**values)


def _copy_concept_children(
    conn: Connection,
    *,
    concept: Concept,
    source_revision: int,
) -> None:
    for source in _list_concept_aliases(conn, concept.id, source_revision):
        _insert_concept_alias(
            conn,
            source.model_copy(
                update={
                    "id": uuid4().hex,
                    "concept_revision": concept.revision,
                }
            ),
        )
    for source in _list_concept_revision_evidence(
        conn, concept.id, source_revision
    ):
        _insert_concept_evidence(
            conn,
            source.model_copy(
                update={
                    "id": uuid4().hex,
                    "concept_revision": concept.revision,
                }
            ),
        )


def _copy_relation_evidence(
    conn: Connection,
    *,
    relation: ConceptRelation,
    source_revision: int,
) -> None:
    for source in _list_relation_revision_evidence(
        conn, relation.id, source_revision
    ):
        _insert_relation_evidence(
            conn,
            source.model_copy(
                update={
                    "id": uuid4().hex,
                    "relation_revision": relation.revision,
                }
            ),
        )


def _copy_relation_binding(
    conn: Connection,
    *,
    relation: ConceptRelation,
    source_revision: int,
) -> None:
    source = _get_relation_endpoint_binding(
        conn, relation.id, source_revision
    )
    if source is None:
        return
    _insert_relation_endpoint_binding(
        conn,
        relation,
        source.source_concept_revision,
        source.target_concept_revision,
        source.created_at,
    )


def _advance_concept_head(
    conn: Connection,
    course_id: str,
    concept_id: str,
    expected_revision: int,
    revision: int,
    now: datetime,
) -> None:
    result = conn.execute(
        """
        UPDATE concepts SET current_revision = ?, updated_at = ?
        WHERE id = ? AND course_id = ? AND current_revision = ?
        """,
        (
            revision,
            _datetime_to_text(now),
            concept_id,
            course_id,
            expected_revision,
        ),
    )
    if result.rowcount != 1:
        raise GraphRevisionConflictError(
            "The expected Concept revision is no longer current."
        )


def _advance_relation_head(
    conn: Connection,
    course_id: str,
    relation_id: str,
    expected_revision: int,
    revision: int,
    now: datetime,
) -> None:
    result = conn.execute(
        """
        UPDATE concept_relations SET current_revision = ?, updated_at = ?
        WHERE id = ? AND course_id = ? AND current_revision = ?
        """,
        (
            revision,
            _datetime_to_text(now),
            relation_id,
            course_id,
            expected_revision,
        ),
    )
    if result.rowcount != 1:
        raise GraphRevisionConflictError(
            "The expected relation revision is no longer current."
        )


def _stale_incident_relations(
    conn: Connection,
    *,
    course_id: str,
    concept_id: str,
    now: datetime,
) -> None:
    rows = conn.execute(
        _RELATION_CURRENT_SELECT
        + " WHERE relations.course_id = ? "
        + "AND (relations.source_concept_id = ? "
        + "OR relations.target_concept_id = ?)",
        (course_id, concept_id, concept_id),
    ).fetchall()
    for row in rows:
        if row["validity_status"] != "current":
            continue
        source_revision = int(row["revision"])
        relation = _copy_relation_revision(
            row,
            revision=source_revision + 1,
            validity_status="stale",
            now=now,
        )
        _insert_relation_revision(conn, relation)
        _copy_relation_evidence(
            conn, relation=relation, source_revision=source_revision
        )
        _copy_relation_binding(
            conn, relation=relation, source_revision=source_revision
        )
        _advance_relation_head(
            conn,
            course_id,
            relation.id,
            source_revision,
            relation.revision,
            now,
        )


def _record_operation(
    conn: Connection,
    *,
    course_id: str,
    operation_id: str,
    kind: str,
    request_hash: str,
    actor: str,
    reason: str,
    entity_type: str,
    entity_id: str,
    result_revision: int,
    created_at: datetime,
) -> None:
    result_json = json.dumps(
        {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "revision": result_revision,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    conn.execute(
        """
        INSERT INTO concept_graph_operations (
            course_id, operation_id, kind, request_hash, actor, reason,
            entity_type, entity_id, result_revision, result_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            course_id,
            operation_id,
            kind,
            request_hash,
            actor,
            reason,
            entity_type,
            entity_id,
            result_revision,
            result_json,
            _datetime_to_text(created_at),
        ),
    )


def _replay_operation(
    conn: Connection,
    *,
    course_id: str,
    operation_id: str,
    request_hash: str,
    kind: str,
    entity_type: str,
    entity_id: str,
) -> int | None:
    replay = _read_operation_receipt(
        conn,
        course_id=course_id,
        operation_id=operation_id,
        request_hash=request_hash,
        kind=kind,
        entity_type=entity_type,
    )
    if replay is None:
        return None
    stored_entity_id, revision = replay
    if stored_entity_id != entity_id:
        raise GraphOperationReuseError(
            "This operation id was already used for a different request."
        )
    return revision


def _replay_create_operation(
    conn: Connection,
    *,
    course_id: str,
    operation_id: str,
    request_hash: str,
    kind: str,
    entity_type: str,
) -> tuple[str, int] | None:
    return _read_operation_receipt(
        conn,
        course_id=course_id,
        operation_id=operation_id,
        request_hash=request_hash,
        kind=kind,
        entity_type=entity_type,
    )


def _read_operation_receipt(
    conn: Connection,
    *,
    course_id: str,
    operation_id: str,
    request_hash: str,
    kind: str,
    entity_type: str,
) -> tuple[str, int] | None:
    row = conn.execute(
        """
        SELECT kind, request_hash, entity_type, entity_id, result_revision,
               result_json
        FROM concept_graph_operations
        WHERE course_id = ? AND operation_id = ?
        """,
        (course_id, operation_id),
    ).fetchone()
    if row is None:
        return None
    if (
        row["request_hash"] != request_hash
        or row["kind"] != kind
        or row["entity_type"] != entity_type
    ):
        raise GraphOperationReuseError(
            "This operation id was already used for a different request."
        )
    entity_id = str(row["entity_id"])
    revision = int(row["result_revision"])
    try:
        receipt = json.loads(row["result_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Stored graph operation receipt is invalid.") from exc
    if receipt != {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "revision": revision,
    }:
        raise RuntimeError("Stored graph operation receipt is inconsistent.")
    return entity_id, revision


def _require_current_concept_evidence(
    conn: Connection,
    concept_id: str,
    revision: int,
) -> list[ConceptEvidence]:
    evidence = _list_concept_revision_evidence(conn, concept_id, revision)
    if not evidence:
        raise GraphReviewTransitionError(
            "A Concept needs evidence before it can be accepted or bound."
        )
    if not all(item.projection_is_current for item in evidence):
        raise GraphEvidenceStaleError(
            "Concept evidence is no longer current."
        )
    return evidence


def _require_current_relation_evidence(
    evidence: list[RelationEvidence],
) -> None:
    if not evidence:
        raise GraphReviewTransitionError(
            "A relation needs evidence before it can be accepted."
        )
    if not all(item.projection_is_current for item in evidence):
        raise GraphEvidenceStaleError(
            "Relation evidence is no longer current."
        )


def _require_expected_relation_endpoints(
    conn: Connection,
    relation_row: Row,
    expected_source_revision: int,
    expected_target_revision: int,
    *,
    require_accepted: bool,
) -> dict[str, list[EvidenceFingerprint]]:
    course_id = str(relation_row["course_id"])
    expected = {
        str(relation_row["source_concept_id"]): expected_source_revision,
        str(relation_row["target_concept_id"]): expected_target_revision,
    }
    rows = conn.execute(
        _CONCEPT_CURRENT_SELECT
        + " WHERE concepts.course_id = ? AND concepts.id IN (?, ?)",
        (
            course_id,
            relation_row["source_concept_id"],
            relation_row["target_concept_id"],
        ),
    ).fetchall()
    by_id = {str(row["id"]): row for row in rows}
    if set(by_id) != set(expected):
        raise RelationEndpointNotFoundError(
            "Both relation endpoints must exist in the selected course."
        )
    result: dict[str, list[EvidenceFingerprint]] = {}
    for concept_id, expected_revision in expected.items():
        row = by_id[concept_id]
        if int(row["revision"]) != expected_revision:
            raise GraphRevisionConflictError(
                "The expected endpoint revision is no longer current."
            )
        if (
            row["identity_status"] != "active"
            or row["validity_status"] != "current"
            or row["review_status"] == "rejected"
        ):
            raise GraphReviewTransitionError(
                "Relation endpoints must be active current Concepts."
            )
        if require_accepted and row["review_status"] != "accepted":
            raise GraphReviewTransitionError(
                "Accepted relations require accepted endpoint Concepts."
            )
        evidence = _require_current_concept_evidence(
            conn, concept_id, expected_revision
        )
        result[concept_id] = [_evidence_fingerprint(item) for item in evidence]
    return result


def _require_no_prerequisite_cycle(
    conn: Connection,
    *,
    course_id: str,
    relation_id: str,
    source_concept_id: str,
    target_concept_id: str,
) -> None:
    rows = conn.execute(
        """
        SELECT identities.id, identities.source_concept_id,
               identities.target_concept_id
        FROM concept_relations AS identities
        INNER JOIN concept_relation_revisions AS revisions
            ON revisions.relation_id = identities.id
           AND revisions.course_id = identities.course_id
           AND revisions.revision = identities.current_revision
        WHERE identities.course_id = ?
          AND identities.relation_type = 'prerequisite'
          AND revisions.review_status = 'accepted'
          AND revisions.validity_status = 'current'
          AND identities.id != ?
        """,
        (course_id, relation_id),
    ).fetchall()
    adjacency: dict[str, set[str]] = {}
    for row in rows:
        adjacency.setdefault(str(row["source_concept_id"]), set()).add(
            str(row["target_concept_id"])
        )
    adjacency.setdefault(source_concept_id, set()).add(target_concept_id)

    stack = [target_concept_id]
    visited: set[str] = set()
    while stack:
        current = stack.pop()
        if current == source_concept_id:
            raise PrerequisiteCycleError(
                "Accepting this prerequisite relation would create a cycle."
            )
        if current in visited:
            continue
        visited.add(current)
        stack.extend(adjacency.get(current, ()))


def _load_concept_revision_or_raise(
    conn: Connection,
    course_id: str,
    concept_id: str,
    revision: int,
) -> Concept:
    row = _select_concept_revision(conn, course_id, concept_id, revision)
    if row is None:
        raise RuntimeError("Stored Concept operation result is missing.")
    return _load_concept(conn, row)


def _load_relation_revision_or_raise(
    conn: Connection,
    course_id: str,
    relation_id: str,
    revision: int,
) -> ConceptRelation:
    row = _select_relation_revision(conn, course_id, relation_id, revision)
    if row is None:
        raise RuntimeError("Stored relation operation result is missing.")
    return _load_relation(conn, row)


def _load_concept(conn: Connection, row: Row) -> Concept:
    revision = int(row["revision"])
    concept = _row_to_concept(
        row,
        _list_concept_revision_evidence(conn, str(row["id"]), revision),
    )
    aliases = _list_concept_aliases(conn, str(row["id"]), revision)
    return _decorate_concept(
        concept.model_copy(update={"aliases": aliases}),
        is_current_revision=revision == int(row["head_revision"]),
    )


def _decorate_concept(
    concept: Concept,
    *,
    is_current_revision: bool,
) -> Concept:
    reasons: list[str] = []
    if not is_current_revision:
        reasons.append("not_current_revision")
    if concept.identity_status != "active":
        reasons.append("identity_not_active")
    if concept.review_status != "accepted":
        reasons.append("review_not_accepted")
    if concept.validity_status != "current":
        reasons.append("validity_not_current")
    if not concept.evidence:
        reasons.append("evidence_missing")
    evidence_current = bool(concept.evidence) and all(
        item.projection_is_current for item in concept.evidence
    )
    if concept.evidence and not evidence_current:
        reasons.append("evidence_not_current")
    return concept.model_copy(
        update={
            "is_current_revision": is_current_revision,
            "evidence_current": evidence_current,
            "eligible_for_publication": not reasons,
            "currentness_reasons": reasons,
        }
    )


def _load_relation(conn: Connection, row: Row) -> ConceptRelation:
    revision = int(row["revision"])
    evidence = _list_relation_revision_evidence(
        conn, str(row["id"]), revision
    )
    binding = _get_relation_endpoint_binding(conn, str(row["id"]), revision)
    relation = _row_to_relation(row, evidence).model_copy(
        update={"endpoint_binding": binding}
    )
    endpoint_concepts: dict[str, Concept] = {}
    for concept_id in (
        relation.source_concept_id,
        relation.target_concept_id,
    ):
        endpoint_row = conn.execute(
            _CONCEPT_CURRENT_SELECT
            + " WHERE concepts.course_id = ? AND concepts.id = ?",
            (relation.course_id, concept_id),
        ).fetchone()
        if endpoint_row is not None:
            endpoint_concepts[concept_id] = _load_concept(conn, endpoint_row)
    return _decorate_relation(
        relation,
        is_current_revision=revision == int(row["head_revision"]),
        endpoint_concepts=endpoint_concepts,
    )


def _decorate_relation(
    relation: ConceptRelation,
    *,
    is_current_revision: bool,
    endpoint_concepts: dict[str, Concept],
) -> ConceptRelation:
    reasons: list[str] = []
    if not is_current_revision:
        reasons.append("not_current_revision")
    if relation.review_status != "accepted":
        reasons.append("review_not_accepted")
    if relation.validity_status != "current":
        reasons.append("validity_not_current")
    if not relation.evidence:
        reasons.append("relation_evidence_missing")
    evidence_current = bool(relation.evidence) and all(
        item.projection_is_current for item in relation.evidence
    )
    if relation.evidence and not evidence_current:
        reasons.append("relation_evidence_not_current")

    roles = {item.support_role for item in relation.evidence}
    roles_valid = (
        roles == {"relation_assertion"}
        if relation.support_basis == "source_asserted"
        else roles == {"source_endpoint", "target_endpoint"}
    )
    if relation.evidence and not roles_valid:
        reasons.append("support_roles_invalid")

    binding = relation.endpoint_binding
    endpoint_revisions_current = False
    if binding is None:
        reasons.append("legacy_endpoint_binding")
    else:
        source = endpoint_concepts.get(relation.source_concept_id)
        target = endpoint_concepts.get(relation.target_concept_id)
        binding_identity_matches = (
            binding.course_id == relation.course_id
            and binding.source_concept_id == relation.source_concept_id
            and binding.target_concept_id == relation.target_concept_id
        )
        if not binding_identity_matches:
            reasons.append("endpoint_binding_identity_mismatch")
        endpoint_revisions_current = (
            binding_identity_matches
            and source is not None
            and target is not None
            and source.revision == binding.source_concept_revision
            and target.revision == binding.target_concept_revision
        )
        if not endpoint_revisions_current:
            reasons.append("endpoint_revision_mismatch")
        if (
            source is None
            or target is None
            or not source.eligible_for_publication
            or not target.eligible_for_publication
        ):
            reasons.append("endpoint_not_publishable")
    return relation.model_copy(
        update={
            "is_current_revision": is_current_revision,
            "evidence_current": evidence_current,
            "endpoint_revisions_current": endpoint_revisions_current,
            "eligible_for_publication": not reasons,
            "currentness_reasons": reasons,
        }
    )


def _insert_concept_identity(conn: Connection, concept: Concept) -> None:
    conn.execute(
        """
        INSERT INTO concepts (
            id, course_id, current_revision, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            concept.id,
            concept.course_id,
            concept.revision,
            _datetime_to_text(concept.created_at),
            _datetime_to_text(concept.updated_at),
        ),
    )


def _insert_concept_revision(conn: Connection, concept: Concept) -> None:
    conn.execute(
        """
        INSERT INTO concept_revisions (
            concept_id, course_id, revision, preferred_name,
            short_definition, identity_status, merged_into_concept_id,
            review_status, validity_status, proposal_origin, provider, model,
            prompt_protocol, output_version, review_actor, reviewed_at,
            review_revision, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            concept.id,
            concept.course_id,
            concept.revision,
            concept.preferred_name,
            concept.short_definition,
            concept.identity_status,
            concept.merged_into_concept_id,
            concept.review_status,
            concept.validity_status,
            concept.proposal_origin,
            concept.provider,
            concept.model,
            concept.prompt_protocol,
            concept.output_version,
            concept.review_actor,
            _datetime_to_text(concept.reviewed_at),
            concept.review_revision,
            _datetime_to_text(concept.created_at),
            _datetime_to_text(concept.updated_at),
        ),
    )


def _insert_concept_evidence(
    conn: Connection,
    evidence: ConceptEvidence,
) -> None:
    conn.execute(
        """
        INSERT INTO concept_evidence (
            id, course_id, concept_id, concept_revision, source_id, chunk_id,
            chunk_text_hash, projection_generation_id, source_title,
            source_type, quote, locator_json, ordinal, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            evidence.id,
            evidence.course_id,
            evidence.concept_id,
            evidence.concept_revision,
            evidence.source_id,
            evidence.chunk_id,
            evidence.chunk_text_hash,
            evidence.projection_generation_id,
            evidence.source_title,
            evidence.source_type,
            evidence.quote,
            _locator_json(evidence.locator),
            evidence.ordinal,
            _datetime_to_text(evidence.created_at),
        ),
    )


def _require_relation_identity_available(
    conn: Connection,
    relation: ConceptRelation,
) -> None:
    existing = conn.execute(
        """
        SELECT id
        FROM concept_relations
        WHERE course_id = ?
          AND relation_type = ?
          AND source_concept_id = ?
          AND target_concept_id = ?
        """,
        (
            relation.course_id,
            relation.relation_type,
            relation.source_concept_id,
            relation.target_concept_id,
        ),
    ).fetchone()
    if existing is not None:
        raise DuplicateRelationError(
            "This stable Concept relation identity already exists."
        )


def _insert_relation_identity(
    conn: Connection,
    relation: ConceptRelation,
) -> None:
    conn.execute(
        """
        INSERT INTO concept_relations (
            id, course_id, source_concept_id, target_concept_id,
            relation_type, current_revision, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            relation.id,
            relation.course_id,
            relation.source_concept_id,
            relation.target_concept_id,
            relation.relation_type,
            relation.revision,
            _datetime_to_text(relation.created_at),
            _datetime_to_text(relation.updated_at),
        ),
    )


def _insert_relation_revision(
    conn: Connection,
    relation: ConceptRelation,
) -> None:
    conn.execute(
        """
        INSERT INTO concept_relation_revisions (
            relation_id, course_id, revision, support_basis, rationale,
            review_status, validity_status, proposal_origin, provider, model,
            prompt_protocol, output_version, review_actor, reviewed_at,
            review_revision, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            relation.id,
            relation.course_id,
            relation.revision,
            relation.support_basis,
            relation.rationale,
            relation.review_status,
            relation.validity_status,
            relation.proposal_origin,
            relation.provider,
            relation.model,
            relation.prompt_protocol,
            relation.output_version,
            relation.review_actor,
            _datetime_to_text(relation.reviewed_at),
            relation.review_revision,
            _datetime_to_text(relation.created_at),
            _datetime_to_text(relation.updated_at),
        ),
    )


def _insert_relation_evidence(
    conn: Connection,
    evidence: RelationEvidence,
) -> None:
    conn.execute(
        """
        INSERT INTO relation_evidence (
            id, course_id, relation_id, relation_revision, support_role,
            source_id, chunk_id, chunk_text_hash, projection_generation_id,
            source_title, source_type, quote, locator_json, ordinal,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            evidence.id,
            evidence.course_id,
            evidence.relation_id,
            evidence.relation_revision,
            evidence.support_role,
            evidence.source_id,
            evidence.chunk_id,
            evidence.chunk_text_hash,
            evidence.projection_generation_id,
            evidence.source_title,
            evidence.source_type,
            evidence.quote,
            _locator_json(evidence.locator),
            evidence.ordinal,
            _datetime_to_text(evidence.created_at),
        ),
    )


def _snapshot_concept_evidence(
    conn: Connection,
    *,
    concept: Concept,
    request: EvidenceReferenceCreate,
    evidence_id: str,
    ordinal: int,
) -> ConceptEvidence:
    row = _require_grounding_chunk(conn, concept.course_id, request)
    return ConceptEvidence(
        id=evidence_id,
        course_id=concept.course_id,
        concept_id=concept.id,
        concept_revision=concept.revision,
        source_id=row["source_id"],
        chunk_id=row["id"],
        chunk_text_hash=row["text_hash"],
        projection_generation_id=row["projection_generation_id"],
        projection_is_current=True,
        source_title=row["source_title"],
        source_type=row["source_type"],
        quote=request.quote,
        locator=json.loads(row["locator_json"]),
        ordinal=ordinal,
        created_at=concept.created_at,
    )


def _snapshot_relation_evidence(
    conn: Connection,
    *,
    relation: ConceptRelation,
    request: RelationEvidenceReferenceCreate,
    evidence_id: str,
    ordinal: int,
) -> RelationEvidence:
    row = _require_grounding_chunk(conn, relation.course_id, request)
    return RelationEvidence(
        id=evidence_id,
        course_id=relation.course_id,
        relation_id=relation.id,
        relation_revision=relation.revision,
        support_role=request.support_role,
        source_id=row["source_id"],
        chunk_id=row["id"],
        chunk_text_hash=row["text_hash"],
        projection_generation_id=row["projection_generation_id"],
        projection_is_current=True,
        source_title=row["source_title"],
        source_type=row["source_type"],
        quote=request.quote,
        locator=json.loads(row["locator_json"]),
        ordinal=ordinal,
        created_at=relation.created_at,
    )


def _require_grounding_chunk(
    conn: Connection,
    course_id: str,
    request: EvidenceReferenceCreate,
) -> Row:
    row = conn.execute(
        f"""
        SELECT source_chunks.*, sources.title AS source_title,
               sources.source_type AS source_type,
               sources.projection_generation_id
        FROM source_chunks
        INNER JOIN sources ON sources.id = source_chunks.source_id
        WHERE source_chunks.id = ?
          AND source_chunks.is_active = 1
          AND sources.course_id = ?
          AND sources.content_status = 'ready'
          AND sources.projection_generation_id IS NOT NULL
          AND {_source_root_is_current_sql("sources")}
        """,
        (request.chunk_id, course_id),
    ).fetchone()
    if row is None:
        raise EvidenceChunkNotFoundError(
            "Evidence chunk is unavailable in the selected course."
        )
    if hash_source_chunk_text(str(row["text"])) != row["text_hash"]:
        raise EvidenceChunkNotFoundError(
            "Evidence chunk is unavailable in the selected course."
        )
    try:
        canonical_source_locator_json(row["locator_json"])
    except (TypeError, ValueError) as exc:
        raise EvidenceChunkNotFoundError(
            "Evidence chunk is unavailable in the selected course."
        ) from exc
    if request.quote not in str(row["text"]):
        raise EvidenceQuoteMismatchError(
            "Evidence quote must be an exact substring of the current chunk."
        )
    return row


def _require_relation_endpoints(
    conn: Connection,
    relation: ConceptRelation,
) -> dict[str, list[EvidenceFingerprint]]:
    rows = conn.execute(
        """
        SELECT concepts.id, concepts.course_id,
               concepts.current_revision AS revision,
               revisions.identity_status,
               revisions.review_status,
               revisions.validity_status
        FROM concepts
        INNER JOIN concept_revisions AS revisions
            ON revisions.concept_id = concepts.id
           AND revisions.course_id = concepts.course_id
           AND revisions.revision = concepts.current_revision
        WHERE concepts.course_id = ? AND concepts.id IN (?, ?)
        """,
        (
            relation.course_id,
            relation.source_concept_id,
            relation.target_concept_id,
        ),
    ).fetchall()
    valid_rows = {
        str(row["id"]): row
        for row in rows
        if row["identity_status"] == "active"
        and row["review_status"] != "rejected"
        and row["validity_status"] == "current"
    }
    if set(valid_rows) != {
        relation.source_concept_id,
        relation.target_concept_id,
    }:
        raise RelationEndpointNotFoundError(
            "Both relation endpoints must be active current Concepts in the "
            "selected course."
        )

    result: dict[str, list[EvidenceFingerprint]] = {}
    for concept_id, row in valid_rows.items():
        evidence = _require_current_concept_evidence(
            conn, concept_id, int(row["revision"])
        )
        result[concept_id] = [
            _evidence_fingerprint(item) for item in evidence
        ]
    return result


def _require_relation_support(
    relation: ConceptRelation,
    evidence: list[RelationEvidence],
    endpoint_evidence: dict[str, list[EvidenceFingerprint]],
) -> None:
    roles = {item.support_role for item in evidence}
    if relation.support_basis == "source_asserted":
        if roles != {"relation_assertion"}:
            raise RelationEvidenceMismatchError(
                "A source-asserted relation only accepts "
                "relation-assertion evidence."
            )
        return

    if roles != {"source_endpoint", "target_endpoint"}:
        raise RelationEvidenceMismatchError(
            "A pedagogical inference only accepts evidence for both endpoints."
        )
    expected_by_role = {
        "source_endpoint": endpoint_evidence[relation.source_concept_id],
        "target_endpoint": endpoint_evidence[relation.target_concept_id],
    }
    for item in evidence:
        expected = expected_by_role[item.support_role]
        pair_matches = [
            fingerprint
            for fingerprint in expected
            if fingerprint[1] == item.chunk_id
            and fingerprint[3] == item.quote
        ]
        if not pair_matches:
            raise RelationEvidenceMismatchError(
                f"{item.support_role} evidence must match evidence on its "
                "current Concept revision."
            )
        if _evidence_fingerprint(item) not in pair_matches:
            raise RelationEvidenceDriftError(
                f"{item.support_role} evidence changed after the endpoint "
                "Concept revision was created."
            )


def _list_concept_revision_evidence(
    conn: Connection,
    concept_id: str,
    revision: int,
) -> list[ConceptEvidence]:
    rows = conn.execute(
        f"""
        SELECT evidence.*,
               sources.id AS current_source_id,
               sources.content_status AS current_source_status,
               sources.projection_generation_id
                   AS current_projection_generation_id,
               sources.source_type AS current_source_type,
               courses.id AS current_course_id,
               current_chunk.id AS current_chunk_id,
               current_chunk.text AS current_chunk_text,
               current_chunk.text_hash AS current_chunk_text_hash,
               current_chunk.locator_json AS current_chunk_locator_json,
               CASE WHEN {_source_root_is_current_sql("sources")}
                    THEN 1 ELSE 0 END AS source_root_is_current
        FROM concept_evidence AS evidence
        LEFT JOIN sources
            ON sources.id = evidence.source_id
           AND sources.course_id = evidence.course_id
        LEFT JOIN courses
            ON courses.id = evidence.course_id
           AND courses.deleted_at IS NULL
        LEFT JOIN source_chunks AS current_chunk
            ON current_chunk.id = evidence.chunk_id
           AND current_chunk.source_id = evidence.source_id
           AND current_chunk.is_active = 1
        WHERE evidence.concept_id = ? AND evidence.concept_revision = ?
        ORDER BY evidence.ordinal, evidence.id
        """,
        (concept_id, revision),
    ).fetchall()
    return [_row_to_concept_evidence(row) for row in rows]


def _list_relation_revision_evidence(
    conn: Connection,
    relation_id: str,
    revision: int,
) -> list[RelationEvidence]:
    rows = conn.execute(
        f"""
        SELECT evidence.*,
               sources.id AS current_source_id,
               sources.content_status AS current_source_status,
               sources.projection_generation_id
                   AS current_projection_generation_id,
               sources.source_type AS current_source_type,
               courses.id AS current_course_id,
               current_chunk.id AS current_chunk_id,
               current_chunk.text AS current_chunk_text,
               current_chunk.text_hash AS current_chunk_text_hash,
               current_chunk.locator_json AS current_chunk_locator_json,
               CASE WHEN {_source_root_is_current_sql("sources")}
                    THEN 1 ELSE 0 END AS source_root_is_current
        FROM relation_evidence AS evidence
        LEFT JOIN sources
            ON sources.id = evidence.source_id
           AND sources.course_id = evidence.course_id
        LEFT JOIN courses
            ON courses.id = evidence.course_id
           AND courses.deleted_at IS NULL
        LEFT JOIN source_chunks AS current_chunk
            ON current_chunk.id = evidence.chunk_id
           AND current_chunk.source_id = evidence.source_id
           AND current_chunk.is_active = 1
        WHERE evidence.relation_id = ? AND evidence.relation_revision = ?
        ORDER BY evidence.ordinal, evidence.id
        """,
        (relation_id, revision),
    ).fetchall()
    return [_row_to_relation_evidence(row) for row in rows]


def _row_to_concept(
    row: Row,
    evidence: list[ConceptEvidence],
) -> Concept:
    return Concept(evidence=evidence, **_concept_fields(row))


def _row_to_concept_summary(row: Row) -> ConceptSummary:
    return ConceptSummary(
        evidence_count=row["evidence_count"],
        **_concept_fields(row),
    )


def _concept_fields(row: Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "course_id": row["course_id"],
        "preferred_name": row["preferred_name"],
        "short_definition": row["short_definition"],
        "identity_status": row["identity_status"],
        "merged_into_concept_id": row["merged_into_concept_id"],
        **_candidate_fields(row),
    }


def _row_to_relation(
    row: Row,
    evidence: list[RelationEvidence],
) -> ConceptRelation:
    return ConceptRelation(evidence=evidence, **_relation_fields(row))


def _row_to_relation_summary(row: Row) -> ConceptRelationSummary:
    return ConceptRelationSummary(
        evidence_count=row["evidence_count"],
        **_relation_fields(row),
    )


def _relation_fields(row: Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "course_id": row["course_id"],
        "source_concept_id": row["source_concept_id"],
        "target_concept_id": row["target_concept_id"],
        "relation_type": row["relation_type"],
        "support_basis": row["support_basis"],
        "rationale": row["rationale"],
        **_candidate_fields(row),
    }


def _candidate_fields(row: Row) -> dict[str, object]:
    return {
        "revision": row["revision"],
        "review_status": row["review_status"],
        "validity_status": row["validity_status"],
        "proposal_origin": row["proposal_origin"],
        "provider": row["provider"],
        "model": row["model"],
        "prompt_protocol": row["prompt_protocol"],
        "output_version": row["output_version"],
        "review_actor": row["review_actor"],
        "reviewed_at": _datetime_from_text(row["reviewed_at"]),
        "review_revision": row["review_revision"],
        "created_at": datetime.fromisoformat(row["revision_created_at"]),
        "updated_at": datetime.fromisoformat(row["revision_updated_at"]),
    }


def _row_to_concept_evidence(row: Row) -> ConceptEvidence:
    projection_is_current, currentness_reasons = _projection_currentness(row)
    return ConceptEvidence(
        id=row["id"],
        course_id=row["course_id"],
        concept_id=row["concept_id"],
        concept_revision=row["concept_revision"],
        source_id=row["source_id"],
        chunk_id=row["chunk_id"],
        chunk_text_hash=row["chunk_text_hash"],
        projection_generation_id=row["projection_generation_id"],
        projection_is_current=projection_is_current,
        projection_currentness_reasons=currentness_reasons,
        source_title=row["source_title"],
        source_type=row["source_type"],
        quote=row["quote"],
        locator=json.loads(row["locator_json"]),
        ordinal=row["ordinal"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_relation_evidence(row: Row) -> RelationEvidence:
    projection_is_current, currentness_reasons = _projection_currentness(row)
    return RelationEvidence(
        id=row["id"],
        course_id=row["course_id"],
        relation_id=row["relation_id"],
        relation_revision=row["relation_revision"],
        support_role=row["support_role"],
        source_id=row["source_id"],
        chunk_id=row["chunk_id"],
        chunk_text_hash=row["chunk_text_hash"],
        projection_generation_id=row["projection_generation_id"],
        projection_is_current=projection_is_current,
        projection_currentness_reasons=currentness_reasons,
        source_title=row["source_title"],
        source_type=row["source_type"],
        quote=row["quote"],
        locator=json.loads(row["locator_json"]),
        ordinal=row["ordinal"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _projection_currentness(row: Row) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    generation_id = row["projection_generation_id"]
    if generation_id is None:
        reasons.append("legacy_projection_generation")
    if row["current_course_id"] is None:
        reasons.append("course_unavailable")

    if row["current_source_id"] is None:
        reasons.append("source_unavailable")
    else:
        if row["current_source_status"] != "ready":
            reasons.append("source_not_ready")
        if not bool(row["source_root_is_current"]):
            reasons.append("source_root_unavailable")
        if generation_id != row["current_projection_generation_id"]:
            reasons.append("projection_generation_mismatch")
        if row["source_type"] != row["current_source_type"]:
            reasons.append("source_type_mismatch")

    if row["current_chunk_id"] is None:
        reasons.append("chunk_unavailable")
    else:
        current_text = str(row["current_chunk_text"])
        actual_current_hash = hash_source_chunk_text(current_text)
        if (
            row["chunk_text_hash"] != actual_current_hash
            or row["current_chunk_text_hash"] != actual_current_hash
        ):
            reasons.append("chunk_hash_mismatch")
        try:
            locator_matches = canonical_source_locator_json(
                row["locator_json"]
            ) == canonical_source_locator_json(
                row["current_chunk_locator_json"]
            )
        except (TypeError, ValueError):
            locator_matches = False
        if not locator_matches:
            reasons.append("locator_mismatch")
        if str(row["quote"]) not in current_text:
            reasons.append("quote_mismatch")
    return not reasons, reasons


def _row_evidence_fingerprint(row: Row) -> EvidenceFingerprint:
    return (
        str(row["source_id"]),
        str(row["chunk_id"]),
        str(row["chunk_text_hash"]),
        str(row["quote"]),
        _canonical_locator_json(row["locator_json"]),
        (
            str(row["projection_generation_id"])
            if row["projection_generation_id"] is not None
            else None
        ),
    )


def _evidence_fingerprint(
    evidence: ConceptEvidence | RelationEvidence,
) -> EvidenceFingerprint:
    return (
        evidence.source_id,
        evidence.chunk_id,
        evidence.chunk_text_hash,
        evidence.quote,
        _canonical_locator_json(evidence.locator),
        evidence.projection_generation_id,
    )


def _locator_json(locator: object) -> str:
    model_dump = getattr(locator, "model_dump", None)
    if not callable(model_dump):
        raise ValueError("Evidence locator is invalid.")
    return json.dumps(model_dump(mode="json"), ensure_ascii=False)


def _canonical_locator_json(locator: object) -> str:
    return canonical_source_locator_json(locator)


def _validate_page(limit: int, cursor: str | None) -> None:
    if not 1 <= limit <= 20:
        raise ValueError("Concept graph page limit must be between 1 and 20.")
    if cursor is not None and not 1 <= len(cursor) <= 200:
        raise ValueError("Concept graph cursor is invalid.")


_CONCEPT_CURRENT_SELECT = """
SELECT
    concepts.id,
    concepts.course_id,
    concepts.current_revision AS head_revision,
    concepts.current_revision AS revision,
    revisions.preferred_name,
    revisions.short_definition,
    revisions.identity_status,
    revisions.merged_into_concept_id,
    revisions.review_status,
    revisions.validity_status,
    revisions.proposal_origin,
    revisions.provider,
    revisions.model,
    revisions.prompt_protocol,
    revisions.output_version,
    revisions.review_actor,
    revisions.reviewed_at,
    revisions.review_revision,
    revisions.created_at AS revision_created_at,
    revisions.updated_at AS revision_updated_at,
    (
        SELECT COUNT(*)
        FROM concept_evidence
        WHERE concept_evidence.concept_id = concepts.id
          AND concept_evidence.concept_revision = concepts.current_revision
    ) AS evidence_count
FROM concepts
INNER JOIN concept_revisions AS revisions
    ON revisions.concept_id = concepts.id
   AND revisions.course_id = concepts.course_id
   AND revisions.revision = concepts.current_revision
"""


_RELATION_CURRENT_SELECT = """
SELECT
    relations.id,
    relations.course_id,
    relations.source_concept_id,
    relations.target_concept_id,
    relations.relation_type,
    relations.current_revision AS head_revision,
    relations.current_revision AS revision,
    revisions.support_basis,
    revisions.rationale,
    revisions.review_status,
    revisions.validity_status,
    revisions.proposal_origin,
    revisions.provider,
    revisions.model,
    revisions.prompt_protocol,
    revisions.output_version,
    revisions.review_actor,
    revisions.reviewed_at,
    revisions.review_revision,
    revisions.created_at AS revision_created_at,
    revisions.updated_at AS revision_updated_at,
    (
        SELECT COUNT(*)
        FROM relation_evidence
        WHERE relation_evidence.relation_id = relations.id
          AND relation_evidence.relation_revision = relations.current_revision
    ) AS evidence_count
FROM concept_relations AS relations
INNER JOIN concept_relation_revisions AS revisions
    ON revisions.relation_id = relations.id
   AND revisions.course_id = relations.course_id
   AND revisions.revision = relations.current_revision
"""


_CONCEPT_REVISION_SELECT = """
SELECT
    concepts.id,
    concepts.course_id,
    concepts.current_revision AS head_revision,
    revisions.revision AS revision,
    revisions.preferred_name,
    revisions.short_definition,
    revisions.identity_status,
    revisions.merged_into_concept_id,
    revisions.review_status,
    revisions.validity_status,
    revisions.proposal_origin,
    revisions.provider,
    revisions.model,
    revisions.prompt_protocol,
    revisions.output_version,
    revisions.review_actor,
    revisions.reviewed_at,
    revisions.review_revision,
    revisions.created_at AS revision_created_at,
    revisions.updated_at AS revision_updated_at,
    (
        SELECT COUNT(*)
        FROM concept_evidence
        WHERE concept_evidence.concept_id = concepts.id
          AND concept_evidence.concept_revision = revisions.revision
    ) AS evidence_count
FROM concepts
INNER JOIN concept_revisions AS revisions
    ON revisions.concept_id = concepts.id
   AND revisions.course_id = concepts.course_id
"""


_RELATION_REVISION_SELECT = """
SELECT
    relations.id,
    relations.course_id,
    relations.source_concept_id,
    relations.target_concept_id,
    relations.relation_type,
    relations.current_revision AS head_revision,
    revisions.revision AS revision,
    revisions.support_basis,
    revisions.rationale,
    revisions.review_status,
    revisions.validity_status,
    revisions.proposal_origin,
    revisions.provider,
    revisions.model,
    revisions.prompt_protocol,
    revisions.output_version,
    revisions.review_actor,
    revisions.reviewed_at,
    revisions.review_revision,
    revisions.created_at AS revision_created_at,
    revisions.updated_at AS revision_updated_at,
    (
        SELECT COUNT(*)
        FROM relation_evidence
        WHERE relation_evidence.relation_id = relations.id
          AND relation_evidence.relation_revision = revisions.revision
    ) AS evidence_count
FROM concept_relations AS relations
INNER JOIN concept_relation_revisions AS revisions
    ON revisions.relation_id = relations.id
   AND revisions.course_id = relations.course_id
"""
