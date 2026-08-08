from __future__ import annotations

import json
from datetime import datetime
from sqlite3 import Connection, Row

from .concept_graph import (
    Concept,
    ConceptEvidence,
    ConceptRelation,
    ConceptRelationSummary,
    ConceptSummary,
    EvidenceReferenceCreate,
    RelationEvidence,
    RelationEvidenceReferenceCreate,
)
from .course_source import hash_source_chunk_text
from .db import connect, ensure_db
from .source_projection_identity import canonical_source_locator_json


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
) -> Concept:
    if len(evidence_requests) != len(evidence_ids):
        raise ValueError("Every Concept evidence reference needs an id.")

    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _insert_concept_identity(conn, concept)
        _insert_concept_revision(conn, concept)
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

    return concept.model_copy(update={"evidence": evidence})


def create_relation_candidate(
    relation: ConceptRelation,
    evidence_requests: list[RelationEvidenceReferenceCreate],
    evidence_ids: list[str],
) -> ConceptRelation:
    if len(evidence_requests) != len(evidence_ids):
        raise ValueError("Every relation evidence reference needs an id.")

    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        endpoint_evidence = _require_relation_endpoints(conn, relation)
        _require_relation_identity_available(conn, relation)
        _insert_relation_identity(conn, relation)
        _insert_relation_revision(conn, relation)
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

    return relation.model_copy(update={"evidence": evidence})


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
        evidence = _list_concept_revision_evidence(
            conn,
            concept_id,
            int(row["revision"]),
        )
    return _row_to_concept(row, evidence)


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
        evidence = _list_relation_revision_evidence(
            conn,
            relation_id,
            int(row["revision"]),
        )
    return _row_to_relation(row, evidence)


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
        conn.execute("DELETE FROM relation_evidence")
        conn.execute("DELETE FROM concept_relation_revisions")
        conn.execute("DELETE FROM concept_relations")
        conn.execute("DELETE FROM concept_evidence")
        conn.execute("DELETE FROM concept_revisions")
        conn.execute("DELETE FROM concepts")


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
        evidence_rows = conn.execute(
            """
            SELECT source_id, chunk_id, chunk_text_hash, quote, locator_json,
                   projection_generation_id
            FROM concept_evidence
            WHERE course_id = ?
              AND concept_id = ?
              AND concept_revision = ?
            """,
            (relation.course_id, concept_id, row["revision"]),
        ).fetchall()
        result[concept_id] = [
            _row_evidence_fingerprint(item)
            for item in evidence_rows
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
