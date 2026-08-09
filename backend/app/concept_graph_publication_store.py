from __future__ import annotations

import hashlib
import heapq
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from sqlite3 import Connection, Row
from typing import Any, Callable, Literal

from .citation_target_store import (
    CitationContextRecord,
    CitationSnapshotRecord,
)
from .concept_graph import canonicalize_relation_endpoints
from .concept_graph_publication import (
    GraphPublicationCounts,
    GraphPublicationIssue,
    GraphPublicationPreview,
    GraphPublicationRequest,
    GraphVersionMetadata,
    GraphVersionPage,
    PublishedConcept,
    PublishedConceptAlias,
    PublishedConceptPage,
    PublishedEvidence,
    PublishedGraphSnapshot,
    PublishedRelation,
    PublishedRelationEvidence,
    PublishedRelationPage,
)
from .course_source import hash_source_chunk_text
from .db import connect, ensure_db
from .job import utc_now
from .source_projection_identity import canonical_source_locator_json


CONTENT_HASH_PROTOCOL = "concept-graph-content-v1"
DRAFT_MANIFEST_PROTOCOL = "concept-graph-draft-manifest-v1"
CONCEPT_AGGREGATE_HASH_PROTOCOL = "concept-graph-concept-aggregate-v1"
RELATION_AGGREGATE_HASH_PROTOCOL = "concept-graph-relation-aggregate-v1"
MAX_CONCEPTS = 5_000
MAX_RELATIONS = 10_000
MAX_ALIASES = 25_000
MAX_CONCEPT_EVIDENCE = 50_000
MAX_RELATION_EVIDENCE = 100_000
MAX_ISSUES = 100
MAX_DRAFT_SERIALIZED_BYTES = 64 * 1024 * 1024
MAX_AUTHORITY_CHUNK_CHARS = 65_536
GRAPH_HYDRATION_BATCH_SIZE = 500


class ConceptGraphPublicationStoreError(RuntimeError):
    pass


class PublicationNotFoundError(ConceptGraphPublicationStoreError):
    pass


class PublicationConflictError(ConceptGraphPublicationStoreError):
    pass


class PublicationOperationReuseError(PublicationConflictError):
    pass


class PublicationValidationError(PublicationConflictError):
    pass


class PublicationTooLargeError(ConceptGraphPublicationStoreError):
    pass


class PublicationIntegrityError(ConceptGraphPublicationStoreError):
    pass


@dataclass(frozen=True)
class DraftSnapshot:
    concepts: list[dict[str, object]]
    relations: list[dict[str, object]]
    endpoint_heads: list[dict[str, object]]
    all_issues: "BoundedIssues"
    counts: GraphPublicationCounts
    content_hash: str
    manifest_hash: str


@dataclass
class BoundedIssues:
    """Bound validation memory while retaining deterministic CAS identity."""

    visible: list[GraphPublicationIssue] = field(default_factory=list)
    total: int = 0
    _hasher: Any = field(default_factory=hashlib.sha256, repr=False)

    def append(self, issue: GraphPublicationIssue) -> None:
        self.total += 1
        stable = {
            "code": issue.code,
            "entity_type": issue.entity_type,
            "entity_id": issue.entity_id,
            "revision": issue.revision,
        }
        encoded = _canonical_bytes(stable)
        self._hasher.update(len(encoded).to_bytes(8, "big"))
        self._hasher.update(encoded)
        if len(self.visible) < MAX_ISSUES:
            self.visible.append(issue)

    def extend(self, issues: list[GraphPublicationIssue]) -> None:
        for issue in issues:
            self.append(issue)

    def sort(
        self,
        *,
        key: Callable[[GraphPublicationIssue], object],
    ) -> None:
        self.visible.sort(key=key)

    def __len__(self) -> int:
        return self.total

    def __bool__(self) -> bool:
        return self.total > 0

    def __getitem__(self, item: slice) -> list[GraphPublicationIssue]:
        return self.visible[item]

    @property
    def digest(self) -> str:
        return self._hasher.copy().hexdigest()


# Tests may replace this hook to prove that every partial write rolls back.
_publication_fault_hook: Callable[[str], None] | None = None


def preview_publication(course_id: str) -> GraphPublicationPreview:
    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN")
        _require_active_course(conn, course_id)
        snapshot = _build_draft_snapshot(conn, course_id)
        active_row = _select_active_version_row(conn, course_id)
        return _preview_from_snapshot(snapshot, active_row)


def publish_version(
    course_id: str,
    request: GraphPublicationRequest,
    *,
    request_hash: str,
) -> GraphVersionMetadata:
    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _require_active_course(conn, course_id)
        replay = conn.execute(
            """
            SELECT request_hash, result_version_number
            FROM concept_graph_publication_operations
            WHERE course_id = ? AND operation_id = ?
            """,
            (course_id, request.operation_id),
        ).fetchone()
        if replay is not None:
            if replay["request_hash"] != request_hash:
                raise PublicationOperationReuseError(
                    "Publication operation id was already used for a "
                    "different request."
                )
            return _load_version_metadata(
                conn,
                course_id,
                int(replay["result_version_number"]),
                verify=True,
            )

        active_row = _select_active_version_row(conn, course_id)
        active_version = (
            int(active_row["version_number"])
            if active_row is not None
            else None
        )
        if active_version != request.expected_active_version:
            raise PublicationConflictError(
                "The active Concept graph version changed. Refresh the "
                "publication preview and retry."
            )

        snapshot = _build_draft_snapshot(conn, course_id)
        if snapshot.manifest_hash != request.expected_draft_manifest_hash:
            raise PublicationConflictError(
                "The Concept graph draft changed. Refresh the publication "
                "preview and retry."
            )
        if snapshot.all_issues:
            raise PublicationValidationError(
                "The Concept graph draft has publication-blocking issues."
            )
        if not snapshot.concepts:
            raise PublicationValidationError(
                "At least one accepted current Concept is required."
            )
        if (
            active_row is not None
            and active_row["content_hash"] == snapshot.content_hash
        ):
            raise PublicationConflictError(
                "The Concept graph draft has no content changes to publish."
            )

        version_number = (active_version or 0) + 1
        published_at = utc_now()
        _insert_snapshot_children(
            conn,
            course_id=course_id,
            version_number=version_number,
            snapshot=snapshot,
        )
        _run_fault_hook("after_children")
        conn.execute(
            """
            INSERT INTO concept_graph_versions (
                course_id, version_number, parent_version_number,
                draft_manifest_hash, content_hash, concept_count,
                concept_alias_count, relation_count, concept_evidence_count,
                relation_evidence_count, published_by, publication_reason,
                published_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                course_id,
                version_number,
                active_version,
                snapshot.manifest_hash,
                snapshot.content_hash,
                snapshot.counts.concepts,
                snapshot.counts.concept_aliases,
                snapshot.counts.relations,
                snapshot.counts.concept_evidence,
                snapshot.counts.relation_evidence,
                request.actor,
                request.reason,
                published_at.isoformat(),
            ),
        )
        _run_fault_hook("after_version_seal")
        if active_version is None:
            conn.execute(
                """
                INSERT INTO concept_graph_version_heads (
                    course_id, active_version_number, updated_at
                ) VALUES (?, ?, ?)
                """,
                (course_id, version_number, published_at.isoformat()),
            )
        else:
            cursor = conn.execute(
                """
                UPDATE concept_graph_version_heads
                SET active_version_number = ?, updated_at = ?
                WHERE course_id = ? AND active_version_number = ?
                """,
                (
                    version_number,
                    published_at.isoformat(),
                    course_id,
                    active_version,
                ),
            )
            if cursor.rowcount != 1:
                raise PublicationConflictError(
                    "The active Concept graph version changed."
                )
        _run_fault_hook("after_head")
        conn.execute(
            """
            INSERT INTO concept_graph_publication_operations (
                course_id, operation_id, request_hash,
                expected_active_version_number,
                expected_draft_manifest_hash, actor, reason,
                result_version_number, result_content_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                course_id,
                request.operation_id,
                request_hash,
                request.expected_active_version,
                request.expected_draft_manifest_hash,
                request.actor,
                request.reason,
                version_number,
                snapshot.content_hash,
                published_at.isoformat(),
            ),
        )
        _run_fault_hook("after_receipt")
        return _load_version_metadata(
            conn, course_id, version_number, verify=True
        )


def list_versions(
    course_id: str,
    *,
    limit: int,
    cursor: int | None,
) -> GraphVersionPage:
    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN")
        _require_active_course(conn, course_id)
        rows = conn.execute(
            """
            SELECT versions.*, heads.active_version_number,
                   CASE WHEN heads.active_version_number =
                                  versions.version_number
                        THEN 1 ELSE 0 END AS is_active_version,
                   (SELECT COUNT(*) FROM concept_graph_version_concepts
                    WHERE course_id = versions.course_id
                      AND version_number = versions.version_number)
                       AS actual_concepts,
                   (SELECT COUNT(*)
                    FROM concept_graph_version_concept_aliases
                    WHERE course_id = versions.course_id
                      AND version_number = versions.version_number)
                       AS actual_aliases,
                   (SELECT COUNT(*) FROM concept_graph_version_relations
                    WHERE course_id = versions.course_id
                      AND version_number = versions.version_number)
                       AS actual_relations,
                   (SELECT COUNT(*)
                    FROM concept_graph_version_concept_evidence
                    WHERE course_id = versions.course_id
                      AND version_number = versions.version_number)
                       AS actual_concept_evidence,
                   (SELECT COUNT(*)
                    FROM concept_graph_version_relation_evidence
                    WHERE course_id = versions.course_id
                      AND version_number = versions.version_number)
                       AS actual_relation_evidence
            FROM concept_graph_versions AS versions
            LEFT JOIN concept_graph_version_heads AS heads
                ON heads.course_id = versions.course_id
            WHERE versions.course_id = ?
              AND (? IS NULL OR versions.version_number < ?)
            ORDER BY versions.version_number DESC
            LIMIT ?
            """,
            (course_id, cursor, cursor, limit + 1),
        ).fetchall()
        visible = rows[:limit]
        for row in visible:
            _validate_metadata_counts(row)
        authority = _source_authority_by_versions(
            conn, course_id, [int(row["version_number"]) for row in visible]
        )
        items = [
            _metadata_from_row(row, authority[int(row["version_number"])])
            for row in visible
        ]
        next_cursor = (
            str(visible[-1]["version_number"])
            if len(rows) > limit and visible
            else None
        )
        return GraphVersionPage(items=items, next_cursor=next_cursor)


def get_version(course_id: str, version_number: int) -> GraphVersionMetadata:
    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN")
        _require_active_course(conn, course_id)
        return _load_version_metadata(
            conn, course_id, version_number, verify=True
        )


def get_current_version(course_id: str) -> GraphVersionMetadata:
    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN")
        _require_active_course(conn, course_id)
        row = _select_active_version_row(conn, course_id)
        if row is None:
            raise PublicationNotFoundError(
                "No published Concept graph version exists for this course."
            )
        return _load_version_metadata(
            conn, course_id, int(row["version_number"]), verify=True
        )


def list_version_concepts(
    course_id: str,
    version_number: int,
    *,
    limit: int,
    cursor: str | None,
) -> PublishedConceptPage:
    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN")
        _require_active_course(conn, course_id)
        _verify_version_counts(conn, course_id, version_number)
        rows = conn.execute(
            """
            SELECT * FROM concept_graph_version_concepts
            WHERE course_id = ? AND version_number = ?
              AND (? IS NULL OR concept_id > ?)
            ORDER BY concept_id
            LIMIT ?
            """,
            (course_id, version_number, cursor, cursor, limit + 1),
        ).fetchall()
        visible = rows[:limit]
        items = _published_concepts_from_rows(
            conn, course_id, version_number, visible
        )
        next_cursor = (
            str(visible[-1]["concept_id"])
            if len(rows) > limit and visible
            else None
        )
        return PublishedConceptPage(items=items, next_cursor=next_cursor)


def list_version_relations(
    course_id: str,
    version_number: int,
    *,
    limit: int,
    cursor: str | None,
) -> PublishedRelationPage:
    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN")
        _require_active_course(conn, course_id)
        _verify_version_counts(conn, course_id, version_number)
        rows = conn.execute(
            """
            SELECT * FROM concept_graph_version_relations
            WHERE course_id = ? AND version_number = ?
              AND (? IS NULL OR relation_id > ?)
            ORDER BY relation_id
            LIMIT ?
            """,
            (course_id, version_number, cursor, cursor, limit + 1),
        ).fetchall()
        visible = rows[:limit]
        items = _published_relations_from_rows(
            conn, course_id, version_number, visible
        )
        next_cursor = (
            str(visible[-1]["relation_id"])
            if len(rows) > limit and visible
            else None
        )
        return PublishedRelationPage(items=items, next_cursor=next_cursor)


def get_version_evidence_snapshot(
    course_id: str,
    version_number: int,
    *,
    owner_type: Literal["concept", "relation"],
    owner_id: str,
    evidence_id: str,
) -> CitationSnapshotRecord:
    """Load one integrity-checked published evidence item and live Source view."""

    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN")
        _require_active_course(conn, course_id)
        _verify_version_counts(conn, course_id, version_number)
        if owner_type == "concept":
            row = conn.execute(
                """
                SELECT * FROM concept_graph_version_concepts
                WHERE course_id = ? AND version_number = ? AND concept_id = ?
                """,
                (course_id, version_number, owner_id),
            ).fetchone()
            owners = _published_concepts_from_rows(
                conn,
                course_id,
                version_number,
                [row] if row is not None else [],
            )
        else:
            row = conn.execute(
                """
                SELECT * FROM concept_graph_version_relations
                WHERE course_id = ? AND version_number = ? AND relation_id = ?
                """,
                (course_id, version_number, owner_id),
            ).fetchone()
            owners = _published_relations_from_rows(
                conn,
                course_id,
                version_number,
                [row] if row is not None else [],
            )
        if not owners:
            raise PublicationNotFoundError(
                "Published Concept graph evidence not found."
            )
        evidence = next(
            (
                item
                for item in owners[0].evidence
                if item.evidence_id == evidence_id
            ),
            None,
        )
        if evidence is None:
            raise PublicationNotFoundError(
                "Published Concept graph evidence not found."
            )
        return _citation_snapshot_for_published_evidence(
            conn,
            course_id=course_id,
            owner_type=owner_type,
            owner_id=owner_id,
            evidence=evidence,
        )


def load_version_graph_snapshot(
    course_id: str,
    version_number: int,
) -> PublishedGraphSnapshot:
    """Load one complete immutable graph in a single SQLite read snapshot."""

    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN")
        _require_active_course(conn, course_id)
        version = _load_version_metadata(
            conn,
            course_id,
            version_number,
            verify=True,
        )
        concept_rows = conn.execute(
            """
            SELECT * FROM concept_graph_version_concepts
            WHERE course_id = ? AND version_number = ?
            ORDER BY concept_id
            """,
            (course_id, version_number),
        ).fetchall()
        relation_rows = conn.execute(
            """
            SELECT * FROM concept_graph_version_relations
            WHERE course_id = ? AND version_number = ?
            ORDER BY relation_id
            """,
            (course_id, version_number),
        ).fetchall()
        concepts = [
            item
            for start in range(
                0,
                len(concept_rows),
                GRAPH_HYDRATION_BATCH_SIZE,
            )
            for item in _published_concepts_from_rows(
                conn,
                course_id,
                version_number,
                concept_rows[start : start + GRAPH_HYDRATION_BATCH_SIZE],
            )
        ]
        relations = [
            item
            for start in range(
                0,
                len(relation_rows),
                GRAPH_HYDRATION_BATCH_SIZE,
            )
            for item in _published_relations_from_rows(
                conn,
                course_id,
                version_number,
                relation_rows[start : start + GRAPH_HYDRATION_BATCH_SIZE],
            )
        ]
        if (
            len(concepts) != version.counts.concepts
            or len(relations) != version.counts.relations
        ):
            raise PublicationIntegrityError(
                "Published Concept graph snapshot changed while loading."
            )
        return PublishedGraphSnapshot(
            version=version,
            concepts=concepts,
            relations=relations,
        )


def _preview_from_snapshot(
    snapshot: DraftSnapshot,
    active_row: Row | None,
) -> GraphPublicationPreview:
    active_version = (
        int(active_row["version_number"]) if active_row is not None else None
    )
    has_changes = (
        active_row is None or active_row["content_hash"] != snapshot.content_hash
    )
    visible_issues = snapshot.all_issues[:MAX_ISSUES]
    return GraphPublicationPreview(
        active_version=active_version,
        draft_manifest_hash=snapshot.manifest_hash,
        content_hash=snapshot.content_hash,
        publishable=not snapshot.all_issues and bool(snapshot.concepts),
        has_changes=has_changes,
        issues=visible_issues,
        issue_count=len(snapshot.all_issues),
        issues_truncated=len(snapshot.all_issues) > MAX_ISSUES,
        counts=snapshot.counts,
        computed_at=utc_now(),
    )


def _require_active_course(conn: Connection, course_id: str) -> None:
    row = conn.execute(
        """
        SELECT 1 FROM courses
        WHERE id = ? AND deleted_at IS NULL
        """,
        (course_id,),
    ).fetchone()
    if row is None:
        raise PublicationNotFoundError("Course not found.")


def _citation_snapshot_for_published_evidence(
    conn: Connection,
    *,
    course_id: str,
    owner_type: Literal["concept", "relation"],
    owner_id: str,
    evidence: PublishedEvidence,
) -> CitationSnapshotRecord:
    source_row = conn.execute(
        f"""
        SELECT sources.course_id AS source_course_id,
               sources.origin_type AS source_origin_type,
               sources.origin_id AS source_origin_id,
               sources.source_type AS current_source_type,
               sources.content_status AS current_source_status,
               sources.projection_generation_id
                   AS current_projection_generation_id,
               chunks.id AS current_chunk_id,
               chunks.text AS current_chunk_text,
               chunks.text_hash AS current_chunk_text_hash,
               chunks.locator_json AS current_chunk_locator_json,
               chunks.ordinal AS current_chunk_ordinal,
               CASE WHEN {_source_root_is_current_sql("sources")}
                    THEN 1 ELSE 0 END AS source_root_current
        FROM sources
        LEFT JOIN source_chunks AS chunks
            ON chunks.id = ?
           AND chunks.source_id = sources.id
           AND chunks.is_active = 1
        WHERE sources.id = ? AND sources.course_id = ?
        """,
        (evidence.chunk_id, evidence.source_id, course_id),
    ).fetchone()

    current = dict(source_row) if source_row is not None else {}
    current_text = current.get("current_chunk_text")
    stored_current_hash = current.get("current_chunk_text_hash")
    actual_current_hash = (
        hash_source_chunk_text(current_text) if current_text is not None else None
    )
    current_hash = (
        actual_current_hash
        if actual_current_hash == stored_current_hash
        else None
    )
    current_locator = (
        _safe_locator_object(current["current_chunk_locator_json"])
        if current.get("current_chunk_locator_json") is not None
        else None
    )
    current_ordinal = current.get("current_chunk_ordinal")
    context: tuple[CitationContextRecord, ...] = ()
    if current_ordinal is not None:
        context_rows = conn.execute(
            """
            SELECT id, ordinal, text, locator_json
            FROM source_chunks
            WHERE source_id = ? AND is_active = 1
              AND ordinal BETWEEN ? AND ?
            ORDER BY ordinal, id
            """,
            (
                evidence.source_id,
                max(0, current_ordinal - 1),
                current_ordinal + 1,
            ),
        ).fetchall()
        context_items: list[CitationContextRecord] = []
        for row in context_rows:
            locator = _safe_locator_object(row["locator_json"])
            if locator is None:
                continue
            context_items.append(
                CitationContextRecord(
                    chunk_id=str(row["id"]),
                    ordinal=int(row["ordinal"]),
                    text=str(row["text"]),
                    locator=locator,
                )
            )
        context = tuple(context_items)

    return CitationSnapshotRecord(
        citation_id=evidence.evidence_id,
        message_id=f"{owner_type}:{owner_id}",
        source_id=evidence.source_id,
        chunk_id=evidence.chunk_id,
        chunk_text_hash=evidence.chunk_text_hash,
        source_title=evidence.source_title,
        source_type=evidence.source_type,
        quote=evidence.quote,
        locator=evidence.locator.model_dump(mode="json"),
        source_course_id=current.get("source_course_id"),
        source_origin_type=current.get("source_origin_type"),
        source_origin_id=current.get("source_origin_id"),
        current_source_type=current.get("current_source_type"),
        current_chunk_text=current_text,
        current_chunk_text_hash=current_hash,
        current_chunk_locator=current_locator,
        current_chunk_ordinal=current_ordinal,
        current_chunk_active=current.get("current_chunk_id") is not None,
        context=context,
        projection_generation_id=evidence.projection_generation_id,
        current_projection_generation_id=current.get(
            "current_projection_generation_id"
        ),
        current_source_status=current.get("current_source_status"),
        source_root_current=bool(current.get("source_root_current")),
    )


def _safe_locator_object(value: object) -> dict[str, object] | None:
    try:
        parsed = json.loads(str(value))
        if not isinstance(parsed, dict):
            return None
        return json.loads(canonical_source_locator_json(parsed))
    except (TypeError, ValueError):
        return None


def _build_draft_snapshot(conn: Connection, course_id: str) -> DraftSnapshot:
    _enforce_draft_bounds(conn, course_id)
    _register_evidence_currentness_function(conn)
    concept_rows = conn.execute(
        """
        SELECT revisions.*, concepts.id AS concept_id,
               concepts.current_revision
        FROM concepts
        INNER JOIN concept_revisions AS revisions
            ON revisions.concept_id = concepts.id
           AND revisions.course_id = concepts.course_id
           AND revisions.revision = concepts.current_revision
        WHERE concepts.course_id = ?
          AND revisions.identity_status = 'active'
          AND revisions.review_status = 'accepted'
          AND revisions.validity_status = 'current'
        ORDER BY concepts.id
        """,
        (course_id,),
    ).fetchall()
    relation_rows = conn.execute(
        """
        SELECT revisions.*, identities.id AS relation_id,
               identities.source_concept_id,
               identities.target_concept_id,
               identities.relation_type,
               identities.current_revision,
               bindings.source_concept_id AS binding_source_concept_id,
               bindings.source_concept_revision,
               bindings.target_concept_id AS binding_target_concept_id,
               bindings.target_concept_revision,
               bindings.created_at AS binding_created_at
        FROM concept_relations AS identities
        INNER JOIN concept_relation_revisions AS revisions
            ON revisions.relation_id = identities.id
           AND revisions.course_id = identities.course_id
           AND revisions.revision = identities.current_revision
        LEFT JOIN relation_endpoint_revisions AS bindings
            ON bindings.relation_id = identities.id
           AND bindings.course_id = identities.course_id
           AND bindings.relation_revision = identities.current_revision
        WHERE identities.course_id = ?
          AND revisions.review_status = 'accepted'
          AND revisions.validity_status = 'current'
        ORDER BY identities.id
        """,
        (course_id,),
    ).fetchall()

    concept_ids = [str(row["concept_id"]) for row in concept_rows]
    relation_ids = [str(row["relation_id"]) for row in relation_rows]
    aliases = _load_draft_aliases(conn, course_id, concept_ids)
    concept_evidence = _load_draft_concept_evidence(
        conn, course_id, concept_ids
    )
    relation_evidence = _load_draft_relation_evidence(
        conn, course_id, relation_ids
    )
    concept_receipts = _load_current_review_receipts(
        conn, course_id, entity_type="concept"
    )
    relation_receipts = _load_current_review_receipts(
        conn, course_id, entity_type="relation"
    )

    issues = BoundedIssues()
    concepts: list[dict[str, object]] = []
    included_revisions: dict[str, int] = {}
    for ordinal, row in enumerate(concept_rows):
        concept_id = str(row["concept_id"])
        revision = int(row["revision"])
        receipt, receipt_issues = _resolve_review_receipt(
            concept_receipts.get((concept_id, revision), []),
            entity_type="concept",
            entity_id=concept_id,
            revision=revision,
            review_actor=row["review_actor"],
            reviewed_at=row["reviewed_at"],
            review_revision=row["review_revision"],
        )
        issues.extend(receipt_issues)
        evidence_items = concept_evidence.get(concept_id, [])
        for item in evidence_items:
            if not item.pop("_current"):
                issues.append(
                    _issue(
                        "concept_evidence_not_current",
                        "concept",
                        concept_id,
                        revision,
                        "Concept evidence no longer resolves to the current "
                        "Source projection.",
                    )
                )
        if not evidence_items:
            issues.append(
                _issue(
                    "concept_evidence_missing",
                    "concept",
                    concept_id,
                    revision,
                    "An accepted Concept must retain grounded evidence.",
                )
            )
        concepts.append(
            _draft_concept(
                row,
                ordinal=ordinal,
                aliases=aliases.get(concept_id, []),
                evidence=evidence_items,
                receipt=receipt,
            )
        )
        included_revisions[concept_id] = revision

    concept_by_id = {
        str(item["concept_id"]): item for item in concepts
    }
    evidence_fingerprints_by_concept_id = {
        concept_id: {
            _evidence_fingerprint(item) for item in concept["evidence"]
        }
        for concept_id, concept in concept_by_id.items()
    }

    endpoint_heads = _load_endpoint_heads(conn, course_id, relation_rows)
    endpoint_head_map = {
        str(item["concept_id"]): item for item in endpoint_heads
    }
    relations: list[dict[str, object]] = []
    prerequisite_edges: list[tuple[str, str]] = []
    for ordinal, row in enumerate(relation_rows):
        relation_id = str(row["relation_id"])
        revision = int(row["revision"])
        receipt, receipt_issues = _resolve_review_receipt(
            relation_receipts.get((relation_id, revision), []),
            entity_type="relation",
            entity_id=relation_id,
            revision=revision,
            review_actor=row["review_actor"],
            reviewed_at=row["reviewed_at"],
            review_revision=row["review_revision"],
        )
        issues.extend(receipt_issues)
        evidence_items = relation_evidence.get(relation_id, [])
        for item in evidence_items:
            if not item.pop("_current"):
                issues.append(
                    _issue(
                        "relation_evidence_not_current",
                        "relation",
                        relation_id,
                        revision,
                        "Relation evidence no longer resolves to the current "
                        "Source projection.",
                    )
                )
        if not evidence_items:
            issues.append(
                _issue(
                    "relation_evidence_missing",
                    "relation",
                    relation_id,
                    revision,
                    "An accepted relation must retain grounded evidence.",
                )
            )
        issues.extend(
            _validate_relation(
                row,
                evidence_items,
                included_revisions,
                evidence_fingerprints_by_concept_id,
                endpoint_head_map,
            )
        )
        if row["relation_type"] == "prerequisite":
            prerequisite_edges.append(
                (
                    str(row["source_concept_id"]),
                    str(row["target_concept_id"]),
                )
            )
        relations.append(
            _draft_relation(
                row,
                ordinal=ordinal,
                evidence=evidence_items,
                receipt=receipt,
            )
        )

    if not concepts:
        issues.append(
            _issue(
                "concept_set_empty",
                "graph",
                None,
                None,
                "At least one accepted current Concept is required.",
            )
        )
    if _has_directed_cycle(set(included_revisions), prerequisite_edges):
        issues.append(
            _issue(
                "prerequisite_cycle",
                "graph",
                None,
                None,
                "Accepted prerequisite relations contain a cycle.",
            )
        )
    issues.sort(key=_issue_sort_key)

    counts = GraphPublicationCounts(
        concepts=len(concepts),
        relations=len(relations),
        concept_aliases=sum(len(item["aliases"]) for item in concepts),
        concept_evidence=sum(len(item["evidence"]) for item in concepts),
        relation_evidence=sum(len(item["evidence"]) for item in relations),
    )
    content_payload = {
        "protocol": CONTENT_HASH_PROTOCOL,
        "concepts": concepts,
        "relations": relations,
    }
    content_bytes = _canonical_bytes(content_payload)
    if len(content_bytes) > MAX_DRAFT_SERIALIZED_BYTES:
        raise PublicationTooLargeError(
            "Canonical Concept graph content exceeds the 64 MiB limit."
        )
    content_hash = hashlib.sha256(content_bytes).hexdigest()
    manifest_payload = {
        "protocol": DRAFT_MANIFEST_PROTOCOL,
        "concepts": concepts,
        "relations": relations,
        "endpoint_heads": endpoint_heads,
        "issues": {
            "count": len(issues),
            "digest": issues.digest,
            "sample": [
                {
                    "code": item.code,
                    "entity_type": item.entity_type,
                    "entity_id": item.entity_id,
                    "revision": item.revision,
                }
                for item in issues.visible
            ],
        },
        "content_hash": content_hash,
    }
    manifest_bytes = _canonical_bytes(manifest_payload)
    if len(manifest_bytes) > MAX_DRAFT_SERIALIZED_BYTES:
        raise PublicationTooLargeError(
            "Canonical Concept graph manifest exceeds the 64 MiB limit."
        )
    return DraftSnapshot(
        concepts=concepts,
        relations=relations,
        endpoint_heads=endpoint_heads,
        all_issues=issues,
        counts=counts,
        content_hash=content_hash,
        manifest_hash=hashlib.sha256(manifest_bytes).hexdigest(),
    )


def _enforce_draft_bounds(conn: Connection, course_id: str) -> None:
    row = conn.execute(
        """
        WITH selected_concepts AS (
            SELECT identities.id, identities.current_revision
            FROM concepts AS identities
            INNER JOIN concept_revisions AS revisions
                ON revisions.concept_id = identities.id
               AND revisions.course_id = identities.course_id
               AND revisions.revision = identities.current_revision
            WHERE identities.course_id = ?
              AND revisions.identity_status = 'active'
              AND revisions.review_status = 'accepted'
              AND revisions.validity_status = 'current'
        ), selected_relations AS (
            SELECT identities.id, identities.current_revision
            FROM concept_relations AS identities
            INNER JOIN concept_relation_revisions AS revisions
                ON revisions.relation_id = identities.id
               AND revisions.course_id = identities.course_id
               AND revisions.revision = identities.current_revision
            WHERE identities.course_id = ?
              AND revisions.review_status = 'accepted'
              AND revisions.validity_status = 'current'
        )
        SELECT
            (SELECT COUNT(*) FROM selected_concepts) AS concepts,
            (SELECT COUNT(*) FROM selected_relations) AS relations,
            (SELECT COUNT(*) FROM concept_aliases AS aliases
             INNER JOIN selected_concepts AS selected
                ON selected.id = aliases.concept_id
               AND selected.current_revision = aliases.concept_revision)
                AS aliases,
            (SELECT COUNT(*) FROM concept_evidence AS evidence
             INNER JOIN selected_concepts AS selected
                ON selected.id = evidence.concept_id
               AND selected.current_revision = evidence.concept_revision)
                AS concept_evidence,
            (SELECT COUNT(*) FROM relation_evidence AS evidence
             INNER JOIN selected_relations AS selected
                ON selected.id = evidence.relation_id
               AND selected.current_revision = evidence.relation_revision)
                AS relation_evidence,
            (SELECT COALESCE(SUM(
                length(CAST(revisions.preferred_name AS BLOB))
                + length(CAST(revisions.short_definition AS BLOB))
                + length(CAST(COALESCE(revisions.provider, '') AS BLOB))
                + length(CAST(COALESCE(revisions.model, '') AS BLOB))
                + length(CAST(COALESCE(revisions.prompt_protocol, '') AS BLOB))
                + length(CAST(COALESCE(revisions.output_version, '') AS BLOB))
            ), 0)
             FROM concept_revisions AS revisions
             INNER JOIN selected_concepts AS selected
                ON selected.id = revisions.concept_id
               AND selected.current_revision = revisions.revision)
            + (SELECT COALESCE(SUM(
                length(CAST(display_text AS BLOB))
                + length(CAST(normalized_text AS BLOB))
            ), 0) FROM concept_aliases AS aliases
             INNER JOIN selected_concepts AS selected
                ON selected.id = aliases.concept_id
               AND selected.current_revision = aliases.concept_revision)
            + (SELECT COALESCE(SUM(
                length(CAST(quote AS BLOB))
                + length(CAST(locator_json AS BLOB))
                + length(CAST(source_title AS BLOB))
            ), 0) FROM concept_evidence AS evidence
             INNER JOIN selected_concepts AS selected
                ON selected.id = evidence.concept_id
               AND selected.current_revision = evidence.concept_revision)
            + (SELECT COALESCE(SUM(
                length(CAST(revisions.rationale AS BLOB))
                + length(CAST(COALESCE(revisions.provider, '') AS BLOB))
                + length(CAST(COALESCE(revisions.model, '') AS BLOB))
                + length(CAST(COALESCE(revisions.prompt_protocol, '') AS BLOB))
                + length(CAST(COALESCE(revisions.output_version, '') AS BLOB))
            ), 0)
             FROM concept_relation_revisions AS revisions
             INNER JOIN selected_relations AS selected
                ON selected.id = revisions.relation_id
               AND selected.current_revision = revisions.revision)
            + (SELECT COALESCE(SUM(
                length(CAST(quote AS BLOB))
                + length(CAST(locator_json AS BLOB))
                + length(CAST(source_title AS BLOB))
            ), 0) FROM relation_evidence AS evidence
             INNER JOIN selected_relations AS selected
                ON selected.id = evidence.relation_id
               AND selected.current_revision = evidence.relation_revision)
                AS payload_bytes
        """,
        (course_id, course_id),
    ).fetchone()
    assert row is not None
    limits = (
        ("Concept", int(row["concepts"]), MAX_CONCEPTS),
        ("relation", int(row["relations"]), MAX_RELATIONS),
        ("Concept alias", int(row["aliases"]), MAX_ALIASES),
        (
            "Concept evidence",
            int(row["concept_evidence"]),
            MAX_CONCEPT_EVIDENCE,
        ),
        (
            "relation evidence",
            int(row["relation_evidence"]),
            MAX_RELATION_EVIDENCE,
        ),
    )
    for label, count, limit in limits:
        if count > limit:
            raise PublicationTooLargeError(
                f"{label} count {count} exceeds the publication limit {limit}."
            )
    conservative_bytes = (
        int(row["payload_bytes"])
        + int(row["concepts"]) * 4_000
        + int(row["relations"]) * 5_000
        + int(row["aliases"]) * 500
        + (
            int(row["concept_evidence"])
            + int(row["relation_evidence"])
        )
        * 1_600
    )
    if conservative_bytes > MAX_DRAFT_SERIALIZED_BYTES:
        raise PublicationTooLargeError(
            "Concept graph draft exceeds the 64 MiB publication limit."
        )


def _load_draft_aliases(
    conn: Connection,
    course_id: str,
    concept_ids: list[str],
) -> dict[str, list[dict[str, object]]]:
    if not concept_ids:
        return {}
    rows = conn.execute(
        """
        SELECT aliases.*
        FROM concept_aliases AS aliases
        INNER JOIN concepts AS identities
            ON identities.id = aliases.concept_id
           AND identities.course_id = aliases.course_id
           AND identities.current_revision = aliases.concept_revision
        INNER JOIN concept_revisions AS revisions
            ON revisions.concept_id = identities.id
           AND revisions.course_id = identities.course_id
           AND revisions.revision = identities.current_revision
        WHERE identities.course_id = ?
          AND revisions.identity_status = 'active'
          AND revisions.review_status = 'accepted'
          AND revisions.validity_status = 'current'
        ORDER BY aliases.concept_id, aliases.ordinal, aliases.id
        """,
        (course_id,),
    ).fetchall()
    result: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        result[str(row["concept_id"])].append(
            {
                "alias_id": str(row["id"]),
                "display_text": str(row["display_text"]),
                "normalized_text": str(row["normalized_text"]),
                "ordinal": int(row["ordinal"]),
                "created_at": str(row["created_at"]),
            }
        )
    return dict(result)


def _load_draft_concept_evidence(
    conn: Connection,
    course_id: str,
    concept_ids: list[str],
) -> dict[str, list[dict[str, object]]]:
    if not concept_ids:
        return {}
    rows = conn.execute(
        f"""
        SELECT evidence.*,
               {_evidence_currentness_sql("evidence", "sources", "courses", "chunks")}
                   AS evidence_is_current
        FROM concept_evidence AS evidence
        INNER JOIN concepts AS identities
            ON identities.id = evidence.concept_id
           AND identities.course_id = evidence.course_id
           AND identities.current_revision = evidence.concept_revision
        INNER JOIN concept_revisions AS revisions
            ON revisions.concept_id = identities.id
           AND revisions.course_id = identities.course_id
           AND revisions.revision = identities.current_revision
        LEFT JOIN sources
            ON sources.id = evidence.source_id
           AND sources.course_id = evidence.course_id
        LEFT JOIN courses
            ON courses.id = evidence.course_id
           AND courses.deleted_at IS NULL
        LEFT JOIN source_chunks AS chunks
            ON chunks.id = evidence.chunk_id
           AND chunks.source_id = evidence.source_id
           AND chunks.is_active = 1
        WHERE identities.course_id = ?
          AND revisions.identity_status = 'active'
          AND revisions.review_status = 'accepted'
          AND revisions.validity_status = 'current'
        ORDER BY evidence.concept_id, evidence.ordinal, evidence.id
        """,
        (course_id,),
    ).fetchall()
    result: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        result[str(row["concept_id"])].append(_draft_evidence(row))
    return dict(result)


def _load_draft_relation_evidence(
    conn: Connection,
    course_id: str,
    relation_ids: list[str],
) -> dict[str, list[dict[str, object]]]:
    if not relation_ids:
        return {}
    rows = conn.execute(
        f"""
        SELECT evidence.*,
               {_evidence_currentness_sql("evidence", "sources", "courses", "chunks")}
                   AS evidence_is_current
        FROM relation_evidence AS evidence
        INNER JOIN concept_relations AS identities
            ON identities.id = evidence.relation_id
           AND identities.course_id = evidence.course_id
           AND identities.current_revision = evidence.relation_revision
        INNER JOIN concept_relation_revisions AS revisions
            ON revisions.relation_id = identities.id
           AND revisions.course_id = identities.course_id
           AND revisions.revision = identities.current_revision
        LEFT JOIN sources
            ON sources.id = evidence.source_id
           AND sources.course_id = evidence.course_id
        LEFT JOIN courses
            ON courses.id = evidence.course_id
           AND courses.deleted_at IS NULL
        LEFT JOIN source_chunks AS chunks
            ON chunks.id = evidence.chunk_id
           AND chunks.source_id = evidence.source_id
           AND chunks.is_active = 1
        WHERE identities.course_id = ?
          AND revisions.review_status = 'accepted'
          AND revisions.validity_status = 'current'
        ORDER BY evidence.relation_id, evidence.ordinal, evidence.id
        """,
        (course_id,),
    ).fetchall()
    result: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        item = _draft_evidence(row)
        item["support_role"] = str(row["support_role"])
        result[str(row["relation_id"])].append(item)
    return dict(result)


def _load_current_review_receipts(
    conn: Connection,
    course_id: str,
    *,
    entity_type: str,
) -> dict[tuple[str, int], list[dict[str, object]]]:
    if entity_type == "concept":
        rows = conn.execute(
            """
            SELECT operations.*
            FROM concept_graph_operations AS operations
            INNER JOIN concepts AS identities
                ON identities.course_id = operations.course_id
               AND identities.id = operations.entity_id
               AND identities.current_revision = operations.result_revision
            INNER JOIN concept_revisions AS revisions
                ON revisions.course_id = identities.course_id
               AND revisions.concept_id = identities.id
               AND revisions.revision = identities.current_revision
            WHERE operations.course_id = ?
              AND operations.kind = 'concept_review'
              AND operations.entity_type = 'concept'
              AND revisions.identity_status = 'active'
              AND revisions.review_status = 'accepted'
              AND revisions.validity_status = 'current'
            ORDER BY operations.entity_id, operations.result_revision,
                     operations.operation_id
            """,
            (course_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT operations.*
            FROM concept_graph_operations AS operations
            INNER JOIN concept_relations AS identities
                ON identities.course_id = operations.course_id
               AND identities.id = operations.entity_id
               AND identities.current_revision = operations.result_revision
            INNER JOIN concept_relation_revisions AS revisions
                ON revisions.course_id = identities.course_id
               AND revisions.relation_id = identities.id
               AND revisions.revision = identities.current_revision
            WHERE operations.course_id = ?
              AND operations.kind = 'relation_review'
              AND operations.entity_type = 'relation'
              AND revisions.review_status = 'accepted'
              AND revisions.validity_status = 'current'
            ORDER BY operations.entity_id, operations.result_revision,
                     operations.operation_id
            """,
            (course_id,),
        ).fetchall()
    result: dict[tuple[str, int], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        result[(str(row["entity_id"]), int(row["result_revision"]))].append(
            dict(row)
        )
    return dict(result)


def _load_endpoint_heads(
    conn: Connection,
    course_id: str,
    relation_rows: list[Row],
) -> list[dict[str, object]]:
    if not relation_rows:
        return []
    rows = conn.execute(
        """
        WITH accepted_relations AS (
            SELECT identities.source_concept_id,
                   identities.target_concept_id
            FROM concept_relations AS identities
            INNER JOIN concept_relation_revisions AS revisions
                ON revisions.relation_id = identities.id
               AND revisions.course_id = identities.course_id
               AND revisions.revision = identities.current_revision
            WHERE identities.course_id = ?
              AND revisions.review_status = 'accepted'
              AND revisions.validity_status = 'current'
        ), endpoints AS (
            SELECT source_concept_id AS concept_id FROM accepted_relations
            UNION
            SELECT target_concept_id AS concept_id FROM accepted_relations
        )
        SELECT endpoints.concept_id, identities.current_revision,
               revisions.identity_status, revisions.review_status,
               revisions.validity_status
        FROM endpoints
        LEFT JOIN concepts AS identities
            ON identities.id = endpoints.concept_id
           AND identities.course_id = ?
        LEFT JOIN concept_revisions AS revisions
            ON revisions.concept_id = identities.id
           AND revisions.course_id = identities.course_id
           AND revisions.revision = identities.current_revision
        ORDER BY endpoints.concept_id
        """,
        (course_id, course_id),
    ).fetchall()
    return [
        {
            "concept_id": str(row["concept_id"]),
            "current_revision": (
                int(row["current_revision"])
                if row["current_revision"] is not None
                else None
            ),
            "identity_status": row["identity_status"],
            "review_status": row["review_status"],
            "validity_status": row["validity_status"],
        }
        for row in rows
    ]


def _resolve_review_receipt(
    receipts: list[dict[str, object]],
    *,
    entity_type: str,
    entity_id: str,
    revision: int,
    review_actor: object,
    reviewed_at: object,
    review_revision: object,
) -> tuple[dict[str, object] | None, list[GraphPublicationIssue]]:
    if not receipts:
        return None, [
            _issue(
                "review_receipt_missing",
                entity_type,
                entity_id,
                revision,
                "Accepted revision has no matching immutable review receipt.",
            )
        ]
    if len(receipts) != 1:
        return receipts[0], [
            _issue(
                "review_receipt_ambiguous",
                entity_type,
                entity_id,
                revision,
                "Accepted revision has multiple matching review receipts.",
            )
        ]
    receipt = receipts[0]
    mismatch = receipt["actor"] != review_actor
    mismatch = mismatch or receipt["created_at"] != reviewed_at
    mismatch = mismatch or review_revision != revision - 1
    request_hash = str(receipt["request_hash"])
    mismatch = mismatch or len(request_hash) != 64 or any(
        character not in "0123456789abcdef" for character in request_hash
    )
    try:
        result = json.loads(str(receipt["result_json"]))
    except (TypeError, json.JSONDecodeError):
        result = None
    mismatch = mismatch or result != {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "revision": revision,
    }
    mismatch = mismatch or not str(receipt["reason"]).strip()
    if mismatch:
        return receipt, [
            _issue(
                "review_receipt_mismatch",
                entity_type,
                entity_id,
                revision,
                "Review receipt actor does not match the accepted revision.",
            )
        ]
    return receipt, []


def _draft_concept(
    row: Row,
    *,
    ordinal: int,
    aliases: list[dict[str, object]],
    evidence: list[dict[str, object]],
    receipt: dict[str, object] | None,
) -> dict[str, object]:
    return {
        "ordinal": ordinal,
        "concept_id": str(row["concept_id"]),
        "concept_revision": int(row["revision"]),
        "preferred_name": str(row["preferred_name"]),
        "short_definition": str(row["short_definition"]),
        "identity_status": str(row["identity_status"]),
        "review_status": str(row["review_status"]),
        "validity_status": str(row["validity_status"]),
        "proposal_origin": str(row["proposal_origin"]),
        "provider": row["provider"],
        "model": row["model"],
        "prompt_protocol": row["prompt_protocol"],
        "output_version": row["output_version"],
        "review_operation_id": (
            str(receipt["operation_id"]) if receipt is not None else None
        ),
        "review_operation_request_hash": (
            str(receipt["request_hash"]) if receipt is not None else None
        ),
        "review_actor": (
            str(receipt["actor"]) if receipt is not None else row["review_actor"]
        ),
        "review_reason": (
            str(receipt["reason"]) if receipt is not None else None
        ),
        "reviewed_at": row["reviewed_at"],
        "review_revision": row["review_revision"],
        "revision_created_at": str(row["created_at"]),
        "revision_updated_at": str(row["updated_at"]),
        "aliases": aliases,
        "evidence": evidence,
    }


def _draft_relation(
    row: Row,
    *,
    ordinal: int,
    evidence: list[dict[str, object]],
    receipt: dict[str, object] | None,
) -> dict[str, object]:
    return {
        "ordinal": ordinal,
        "relation_id": str(row["relation_id"]),
        "relation_revision": int(row["revision"]),
        "source_concept_id": str(row["source_concept_id"]),
        "source_concept_revision": (
            int(row["source_concept_revision"])
            if row["source_concept_revision"] is not None
            else None
        ),
        "target_concept_id": str(row["target_concept_id"]),
        "target_concept_revision": (
            int(row["target_concept_revision"])
            if row["target_concept_revision"] is not None
            else None
        ),
        "relation_type": str(row["relation_type"]),
        "support_basis": str(row["support_basis"]),
        "rationale": str(row["rationale"]),
        "review_status": str(row["review_status"]),
        "validity_status": str(row["validity_status"]),
        "proposal_origin": str(row["proposal_origin"]),
        "provider": row["provider"],
        "model": row["model"],
        "prompt_protocol": row["prompt_protocol"],
        "output_version": row["output_version"],
        "review_operation_id": (
            str(receipt["operation_id"]) if receipt is not None else None
        ),
        "review_operation_request_hash": (
            str(receipt["request_hash"]) if receipt is not None else None
        ),
        "review_actor": (
            str(receipt["actor"]) if receipt is not None else row["review_actor"]
        ),
        "review_reason": (
            str(receipt["reason"]) if receipt is not None else None
        ),
        "reviewed_at": row["reviewed_at"],
        "review_revision": row["review_revision"],
        "binding_created_at": row["binding_created_at"],
        "revision_created_at": str(row["created_at"]),
        "revision_updated_at": str(row["updated_at"]),
        "evidence": evidence,
    }


def _draft_evidence(row: Row) -> dict[str, object]:
    return {
        "evidence_id": str(row["id"]),
        "source_id": str(row["source_id"]),
        "chunk_id": str(row["chunk_id"]),
        "chunk_text_hash": str(row["chunk_text_hash"]),
        "projection_generation_id": row["projection_generation_id"],
        "source_title": str(row["source_title"]),
        "source_type": str(row["source_type"]),
        "quote": str(row["quote"]),
        "locator_json": _canonical_locator_json(row["locator_json"]),
        "ordinal": int(row["ordinal"]),
        "created_at": str(row["created_at"]),
        "_current": bool(row["evidence_is_current"]),
    }


def _validate_relation(
    row: Row,
    evidence: list[dict[str, object]],
    included_revisions: dict[str, int],
    evidence_fingerprints_by_concept_id: dict[
        str, set[tuple[object, ...]]
    ],
    endpoint_head_map: dict[str, dict[str, object]],
) -> list[GraphPublicationIssue]:
    relation_id = str(row["relation_id"])
    revision = int(row["revision"])
    source_id = str(row["source_concept_id"])
    target_id = str(row["target_concept_id"])
    issues: list[GraphPublicationIssue] = []
    canonical_source, canonical_target = canonicalize_relation_endpoints(
        row["relation_type"], source_id, target_id
    )
    if (
        source_id == target_id
        or canonical_source != source_id
        or canonical_target != target_id
    ):
        issues.append(
            _issue(
                "relation_identity_noncanonical",
                "relation",
                relation_id,
                revision,
                "Relation identity is not canonical.",
            )
        )

    source_revision = row["source_concept_revision"]
    target_revision = row["target_concept_revision"]
    if source_revision is None or target_revision is None:
        issues.append(
            _issue(
                "relation_endpoint_binding_missing",
                "relation",
                relation_id,
                revision,
                "Relation has no immutable endpoint revision binding.",
            )
        )
    elif (
        row["binding_source_concept_id"] != source_id
        or row["binding_target_concept_id"] != target_id
    ):
        issues.append(
            _issue(
                "relation_endpoint_binding_identity_mismatch",
                "relation",
                relation_id,
                revision,
                "Relation endpoint binding does not match its identity.",
            )
        )
    for endpoint_id, bound_revision, label in (
        (source_id, source_revision, "source"),
        (target_id, target_revision, "target"),
    ):
        head = endpoint_head_map.get(endpoint_id)
        included_revision = included_revisions.get(endpoint_id)
        if head is None or head["current_revision"] is None:
            issues.append(
                _issue(
                    f"relation_{label}_endpoint_missing",
                    "relation",
                    relation_id,
                    revision,
                    f"Relation {label} endpoint is unavailable.",
                )
            )
        elif included_revision is None:
            issues.append(
                _issue(
                    f"relation_{label}_endpoint_not_publishable",
                    "relation",
                    relation_id,
                    revision,
                    f"Relation {label} endpoint is not in the publishable "
                    "Concept set.",
                )
            )
        elif bound_revision != included_revision:
            issues.append(
                _issue(
                    f"relation_{label}_revision_mismatch",
                    "relation",
                    relation_id,
                    revision,
                    f"Relation {label} binding is not the current included "
                    "Concept revision.",
                )
            )

    roles = {str(item["support_role"]) for item in evidence}
    if row["support_basis"] == "source_asserted":
        if roles != {"relation_assertion"}:
            issues.append(
                _issue(
                    "relation_support_role_invalid",
                    "relation",
                    relation_id,
                    revision,
                    "Source-asserted relation evidence must use only the "
                    "relation-assertion role.",
                )
            )
    else:
        if roles != {"source_endpoint", "target_endpoint"}:
            issues.append(
                _issue(
                    "relation_support_role_invalid",
                    "relation",
                    relation_id,
                    revision,
                    "Pedagogical relation evidence must cover both endpoints.",
                )
            )
        for item in evidence:
            role = str(item["support_role"])
            if role not in {"source_endpoint", "target_endpoint"}:
                continue
            endpoint_id = source_id if role == "source_endpoint" else target_id
            fingerprints = evidence_fingerprints_by_concept_id.get(
                endpoint_id, set()
            )
            if _evidence_fingerprint(item) not in fingerprints:
                issues.append(
                    _issue(
                        "relation_endpoint_evidence_mismatch",
                        "relation",
                        relation_id,
                        revision,
                        "Pedagogical relation evidence does not match the "
                        "bound endpoint Concept evidence.",
                    )
                )
    return issues


def _evidence_fingerprint(item: dict[str, object]) -> tuple[object, ...]:
    return (
        item["source_id"],
        item["chunk_id"],
        item["chunk_text_hash"],
        item["quote"],
        item["locator_json"],
        item["projection_generation_id"],
    )


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


def _register_evidence_currentness_function(conn: Connection) -> None:
    conn.create_function(
        "concept_graph_evidence_is_current",
        15,
        _sqlite_evidence_is_current,
        deterministic=True,
    )


def _evidence_currentness_sql(
    evidence_alias: str,
    source_alias: str,
    course_alias: str,
    chunk_alias: str,
) -> str:
    return f"""
    concept_graph_evidence_is_current(
        {evidence_alias}.projection_generation_id,
        {evidence_alias}.source_type,
        {evidence_alias}.chunk_text_hash,
        {evidence_alias}.locator_json,
        {evidence_alias}.quote,
        {course_alias}.id,
        {source_alias}.id,
        {source_alias}.content_status,
        {source_alias}.projection_generation_id,
        {source_alias}.source_type,
        {chunk_alias}.id,
        CASE
            WHEN length({chunk_alias}.text) <= {MAX_AUTHORITY_CHUNK_CHARS}
            THEN {chunk_alias}.text
        END,
        {chunk_alias}.text_hash,
        CASE
            WHEN length({chunk_alias}.locator_json)
                 <= {MAX_AUTHORITY_CHUNK_CHARS}
            THEN {chunk_alias}.locator_json
        END,
        CASE WHEN {_source_root_is_current_sql(source_alias)}
             THEN 1 ELSE 0 END
    )
    """


def _has_directed_cycle(
    nodes: set[str],
    edges: list[tuple[str, str]],
) -> bool:
    adjacency: dict[str, set[str]] = {node: set() for node in nodes}
    indegree = {node: 0 for node in nodes}
    for source_id, target_id in sorted(set(edges)):
        if source_id not in nodes or target_id not in nodes:
            continue
        if target_id not in adjacency[source_id]:
            adjacency[source_id].add(target_id)
            indegree[target_id] += 1
    ready = [node for node, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    visited = 0
    while ready:
        node = heapq.heappop(ready)
        visited += 1
        for target_id in sorted(adjacency[node]):
            indegree[target_id] -= 1
            if indegree[target_id] == 0:
                heapq.heappush(ready, target_id)
    return visited != len(nodes)


def _issue(
    code: str,
    entity_type: str,
    entity_id: str | None,
    revision: int | None,
    message: str,
) -> GraphPublicationIssue:
    return GraphPublicationIssue(
        code=code,
        entity_type=entity_type,
        entity_id=entity_id,
        revision=revision,
        message=message,
    )


def _issue_sort_key(item: GraphPublicationIssue) -> tuple[object, ...]:
    return (
        item.entity_type,
        item.entity_id or "",
        item.revision or 0,
        item.code,
    )


def _canonical_locator_json(value: object) -> str:
    return canonical_source_locator_json(value)


def _canonical_hash(payload: dict[str, object]) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _concept_aggregate_hash(concept: dict[str, object]) -> str:
    return _canonical_hash(
        {
            "protocol": CONCEPT_AGGREGATE_HASH_PROTOCOL,
            "concept": concept,
        }
    )


def _relation_aggregate_hash(relation: dict[str, object]) -> str:
    return _canonical_hash(
        {
            "protocol": RELATION_AGGREGATE_HASH_PROTOCOL,
            "relation": relation,
        }
    )


def _canonical_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _run_fault_hook(stage: str) -> None:
    if _publication_fault_hook is not None:
        _publication_fault_hook(stage)


def _insert_snapshot_children(
    conn: Connection,
    *,
    course_id: str,
    version_number: int,
    snapshot: DraftSnapshot,
) -> None:
    conn.executemany(
        """
        INSERT INTO concept_graph_version_concepts (
            course_id, version_number, ordinal, concept_id,
            concept_revision, preferred_name, short_definition,
            identity_status, review_status, validity_status,
            proposal_origin, provider, model, prompt_protocol,
            output_version, review_operation_id, review_actor,
            review_operation_request_hash, review_reason, reviewed_at,
            review_revision,
            revision_created_at, revision_updated_at, aggregate_hash
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        [
            (
                course_id,
                version_number,
                item["ordinal"],
                item["concept_id"],
                item["concept_revision"],
                item["preferred_name"],
                item["short_definition"],
                item["identity_status"],
                item["review_status"],
                item["validity_status"],
                item["proposal_origin"],
                item["provider"],
                item["model"],
                item["prompt_protocol"],
                item["output_version"],
                item["review_operation_id"],
                item["review_actor"],
                item["review_operation_request_hash"],
                item["review_reason"],
                item["reviewed_at"],
                item["review_revision"],
                item["revision_created_at"],
                item["revision_updated_at"],
                _concept_aggregate_hash(item),
            )
            for item in snapshot.concepts
        ],
    )
    conn.executemany(
        """
        INSERT INTO concept_graph_version_concept_aliases (
            course_id, version_number, concept_id, concept_revision,
            ordinal, alias_id, display_text, normalized_text, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                course_id,
                version_number,
                concept["concept_id"],
                concept["concept_revision"],
                alias["ordinal"],
                alias["alias_id"],
                alias["display_text"],
                alias["normalized_text"],
                alias["created_at"],
            )
            for concept in snapshot.concepts
            for alias in concept["aliases"]
        ],
    )
    conn.executemany(
        """
        INSERT INTO concept_graph_version_concept_evidence (
            course_id, version_number, concept_id, concept_revision,
            ordinal, evidence_id, source_id, chunk_id, chunk_text_hash,
            projection_generation_id, source_title, source_type, quote,
            locator_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                course_id,
                version_number,
                concept["concept_id"],
                concept["concept_revision"],
                evidence["ordinal"],
                evidence["evidence_id"],
                evidence["source_id"],
                evidence["chunk_id"],
                evidence["chunk_text_hash"],
                evidence["projection_generation_id"],
                evidence["source_title"],
                evidence["source_type"],
                evidence["quote"],
                evidence["locator_json"],
                evidence["created_at"],
            )
            for concept in snapshot.concepts
            for evidence in concept["evidence"]
        ],
    )
    conn.executemany(
        """
        INSERT INTO concept_graph_version_relations (
            course_id, version_number, ordinal, relation_id,
            relation_revision, source_concept_id, source_concept_revision,
            target_concept_id, target_concept_revision, relation_type,
            support_basis, rationale, review_status, validity_status,
            proposal_origin, provider, model, prompt_protocol,
            output_version, review_operation_id, review_actor,
            review_operation_request_hash, review_reason, reviewed_at,
            review_revision,
            binding_created_at, revision_created_at, revision_updated_at,
            aggregate_hash
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        [
            (
                course_id,
                version_number,
                item["ordinal"],
                item["relation_id"],
                item["relation_revision"],
                item["source_concept_id"],
                item["source_concept_revision"],
                item["target_concept_id"],
                item["target_concept_revision"],
                item["relation_type"],
                item["support_basis"],
                item["rationale"],
                item["review_status"],
                item["validity_status"],
                item["proposal_origin"],
                item["provider"],
                item["model"],
                item["prompt_protocol"],
                item["output_version"],
                item["review_operation_id"],
                item["review_actor"],
                item["review_operation_request_hash"],
                item["review_reason"],
                item["reviewed_at"],
                item["review_revision"],
                item["binding_created_at"],
                item["revision_created_at"],
                item["revision_updated_at"],
                _relation_aggregate_hash(item),
            )
            for item in snapshot.relations
        ],
    )
    conn.executemany(
        """
        INSERT INTO concept_graph_version_relation_evidence (
            course_id, version_number, relation_id, relation_revision,
            ordinal, evidence_id, support_role, source_id, chunk_id,
            chunk_text_hash, projection_generation_id, source_title,
            source_type, quote, locator_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                course_id,
                version_number,
                relation["relation_id"],
                relation["relation_revision"],
                evidence["ordinal"],
                evidence["evidence_id"],
                evidence["support_role"],
                evidence["source_id"],
                evidence["chunk_id"],
                evidence["chunk_text_hash"],
                evidence["projection_generation_id"],
                evidence["source_title"],
                evidence["source_type"],
                evidence["quote"],
                evidence["locator_json"],
                evidence["created_at"],
            )
            for relation in snapshot.relations
            for evidence in relation["evidence"]
        ],
    )


def _select_active_version_row(
    conn: Connection,
    course_id: str,
) -> Row | None:
    return conn.execute(
        """
        SELECT versions.*, heads.active_version_number,
               1 AS is_active_version
        FROM concept_graph_version_heads AS heads
        INNER JOIN concept_graph_versions AS versions
            ON versions.course_id = heads.course_id
           AND versions.version_number = heads.active_version_number
        WHERE heads.course_id = ?
        """,
        (course_id,),
    ).fetchone()


def _load_version_metadata(
    conn: Connection,
    course_id: str,
    version_number: int,
    *,
    verify: bool,
) -> GraphVersionMetadata:
    row = conn.execute(
        """
        SELECT versions.*, heads.active_version_number,
               CASE WHEN heads.active_version_number = versions.version_number
                    THEN 1 ELSE 0 END AS is_active_version
        FROM concept_graph_versions AS versions
        LEFT JOIN concept_graph_version_heads AS heads
            ON heads.course_id = versions.course_id
        WHERE versions.course_id = ? AND versions.version_number = ?
        """,
        (course_id, version_number),
    ).fetchone()
    if row is None:
        raise PublicationNotFoundError(
            "Published Concept graph version not found."
        )
    if verify:
        _verify_version_snapshot(conn, course_id, version_number, row=row)
    authority = _source_authority_by_versions(
        conn, course_id, [version_number]
    )[version_number]
    return _metadata_from_row(row, authority)


def _metadata_from_row(
    row: Row,
    authority: tuple[list[GraphPublicationIssue], int],
) -> GraphVersionMetadata:
    issues, total = authority
    return GraphVersionMetadata(
        course_id=str(row["course_id"]),
        version_number=int(row["version_number"]),
        parent_version_number=(
            int(row["parent_version_number"])
            if row["parent_version_number"] is not None
            else None
        ),
        draft_manifest_hash=str(row["draft_manifest_hash"]),
        content_hash=str(row["content_hash"]),
        counts=GraphPublicationCounts(
            concepts=int(row["concept_count"]),
            relations=int(row["relation_count"]),
            concept_aliases=int(row["concept_alias_count"]),
            concept_evidence=int(row["concept_evidence_count"]),
            relation_evidence=int(row["relation_evidence_count"]),
        ),
        published_by=str(row["published_by"]),
        publication_reason=str(row["publication_reason"]),
        published_at=datetime.fromisoformat(str(row["published_at"])),
        is_active_version=bool(row["is_active_version"]),
        source_authority_current=total == 0,
        source_authority_issues=issues[:MAX_ISSUES],
        source_authority_issue_count=total,
        source_authority_issues_truncated=total > MAX_ISSUES,
    )


def _verify_version_snapshot(
    conn: Connection,
    course_id: str,
    version_number: int,
    *,
    row: Row | None = None,
) -> None:
    if row is None:
        row = conn.execute(
            """
            SELECT * FROM concept_graph_versions
            WHERE course_id = ? AND version_number = ?
            """,
            (course_id, version_number),
        ).fetchone()
    if row is None:
        raise PublicationNotFoundError(
            "Published Concept graph version not found."
        )
    if (
        int(row["concept_count"]) > MAX_CONCEPTS
        or int(row["concept_alias_count"]) > MAX_ALIASES
        or int(row["relation_count"]) > MAX_RELATIONS
        or int(row["concept_evidence_count"]) > MAX_CONCEPT_EVIDENCE
        or int(row["relation_evidence_count"]) > MAX_RELATION_EVIDENCE
    ):
        raise PublicationIntegrityError(
            "Published Concept graph snapshot exceeds integrity limits."
        )
    concepts, relations = _load_snapshot_content(
        conn, course_id, version_number
    )
    counts = (
        len(concepts),
        sum(len(item["aliases"]) for item in concepts),
        len(relations),
        sum(len(item["evidence"]) for item in concepts),
        sum(len(item["evidence"]) for item in relations),
    )
    declared = (
        int(row["concept_count"]),
        int(row["concept_alias_count"]),
        int(row["relation_count"]),
        int(row["concept_evidence_count"]),
        int(row["relation_evidence_count"]),
    )
    content_hash = _canonical_hash(
        {
            "protocol": CONTENT_HASH_PROTOCOL,
            "concepts": concepts,
            "relations": relations,
        }
    )
    if counts != declared or content_hash != row["content_hash"]:
        raise PublicationIntegrityError(
            "Published Concept graph snapshot failed integrity validation."
        )


def _verify_version_counts(
    conn: Connection,
    course_id: str,
    version_number: int,
) -> None:
    row = conn.execute(
        """
        SELECT versions.concept_count, versions.concept_alias_count,
               versions.relation_count, versions.concept_evidence_count,
               versions.relation_evidence_count,
               (SELECT COUNT(*) FROM concept_graph_version_concepts
                WHERE course_id = versions.course_id
                  AND version_number = versions.version_number)
                   AS actual_concepts,
               (SELECT COUNT(*)
                FROM concept_graph_version_concept_aliases
                WHERE course_id = versions.course_id
                  AND version_number = versions.version_number)
                   AS actual_aliases,
               (SELECT COUNT(*) FROM concept_graph_version_relations
                WHERE course_id = versions.course_id
                  AND version_number = versions.version_number)
                   AS actual_relations,
               (SELECT COUNT(*)
                FROM concept_graph_version_concept_evidence
                WHERE course_id = versions.course_id
                  AND version_number = versions.version_number)
                   AS actual_concept_evidence,
               (SELECT COUNT(*)
                FROM concept_graph_version_relation_evidence
                WHERE course_id = versions.course_id
                  AND version_number = versions.version_number)
                   AS actual_relation_evidence
        FROM concept_graph_versions AS versions
        WHERE versions.course_id = ? AND versions.version_number = ?
        """,
        (course_id, version_number),
    ).fetchone()
    if row is None:
        raise PublicationNotFoundError(
            "Published Concept graph version not found."
        )
    _validate_metadata_counts(row)


def _validate_metadata_counts(row: Row) -> None:
    declared = (
        row["concept_count"],
        row["concept_alias_count"],
        row["relation_count"],
        row["concept_evidence_count"],
        row["relation_evidence_count"],
    )
    actual = (
        row["actual_concepts"],
        row["actual_aliases"],
        row["actual_relations"],
        row["actual_concept_evidence"],
        row["actual_relation_evidence"],
    )
    if declared != actual:
        raise PublicationIntegrityError(
            "Published Concept graph snapshot failed integrity validation."
        )


def _load_snapshot_content(
    conn: Connection,
    course_id: str,
    version_number: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    concept_rows = conn.execute(
        """
        SELECT * FROM concept_graph_version_concepts
        WHERE course_id = ? AND version_number = ?
        ORDER BY ordinal, concept_id
        """,
        (course_id, version_number),
    ).fetchall()
    relation_rows = conn.execute(
        """
        SELECT * FROM concept_graph_version_relations
        WHERE course_id = ? AND version_number = ?
        ORDER BY ordinal, relation_id
        """,
        (course_id, version_number),
    ).fetchall()
    aliases = _snapshot_alias_dict(conn, course_id, version_number)
    concept_evidence = _snapshot_concept_evidence_dict(
        conn, course_id, version_number
    )
    relation_evidence = _snapshot_relation_evidence_dict(
        conn, course_id, version_number
    )
    concepts = [
        _snapshot_concept_dict(
            row,
            aliases.get(str(row["concept_id"]), []),
            concept_evidence.get(str(row["concept_id"]), []),
        )
        for row in concept_rows
    ]
    relations = [
        _snapshot_relation_dict(
            row,
            relation_evidence.get(str(row["relation_id"]), []),
        )
        for row in relation_rows
    ]
    for row, concept in zip(concept_rows, concepts, strict=True):
        if _concept_aggregate_hash(concept) != row["aggregate_hash"]:
            raise PublicationIntegrityError(
                "Published Concept aggregate failed integrity validation."
            )
    for row, relation in zip(relation_rows, relations, strict=True):
        if _relation_aggregate_hash(relation) != row["aggregate_hash"]:
            raise PublicationIntegrityError(
                "Published relation aggregate failed integrity validation."
            )
    return concepts, relations


def _snapshot_alias_dict(
    conn: Connection,
    course_id: str,
    version_number: int,
) -> dict[str, list[dict[str, object]]]:
    rows = conn.execute(
        """
        SELECT * FROM concept_graph_version_concept_aliases
        WHERE course_id = ? AND version_number = ?
        ORDER BY concept_id, ordinal, alias_id
        """,
        (course_id, version_number),
    ).fetchall()
    result: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        result[str(row["concept_id"])].append(
            {
                "alias_id": str(row["alias_id"]),
                "display_text": str(row["display_text"]),
                "normalized_text": str(row["normalized_text"]),
                "ordinal": int(row["ordinal"]),
                "created_at": str(row["created_at"]),
            }
        )
    return dict(result)


def _snapshot_concept_evidence_dict(
    conn: Connection,
    course_id: str,
    version_number: int,
) -> dict[str, list[dict[str, object]]]:
    rows = conn.execute(
        """
        SELECT * FROM concept_graph_version_concept_evidence
        WHERE course_id = ? AND version_number = ?
        ORDER BY concept_id, ordinal, evidence_id
        """,
        (course_id, version_number),
    ).fetchall()
    result: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        result[str(row["concept_id"])].append(_snapshot_evidence_dict(row))
    return dict(result)


def _snapshot_relation_evidence_dict(
    conn: Connection,
    course_id: str,
    version_number: int,
) -> dict[str, list[dict[str, object]]]:
    rows = conn.execute(
        """
        SELECT * FROM concept_graph_version_relation_evidence
        WHERE course_id = ? AND version_number = ?
        ORDER BY relation_id, ordinal, evidence_id
        """,
        (course_id, version_number),
    ).fetchall()
    result: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        item = _snapshot_evidence_dict(row)
        item["support_role"] = str(row["support_role"])
        result[str(row["relation_id"])].append(item)
    return dict(result)


def _snapshot_evidence_dict(row: Row) -> dict[str, object]:
    return {
        "evidence_id": str(row["evidence_id"]),
        "source_id": str(row["source_id"]),
        "chunk_id": str(row["chunk_id"]),
        "chunk_text_hash": str(row["chunk_text_hash"]),
        "projection_generation_id": str(row["projection_generation_id"]),
        "source_title": str(row["source_title"]),
        "source_type": str(row["source_type"]),
        "quote": str(row["quote"]),
        "locator_json": _canonical_locator_json(row["locator_json"]),
        "ordinal": int(row["ordinal"]),
        "created_at": str(row["created_at"]),
    }


def _snapshot_concept_dict(
    row: Row,
    aliases: list[dict[str, object]],
    evidence: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "ordinal": int(row["ordinal"]),
        "concept_id": str(row["concept_id"]),
        "concept_revision": int(row["concept_revision"]),
        "preferred_name": str(row["preferred_name"]),
        "short_definition": str(row["short_definition"]),
        "identity_status": str(row["identity_status"]),
        "review_status": str(row["review_status"]),
        "validity_status": str(row["validity_status"]),
        "proposal_origin": str(row["proposal_origin"]),
        "provider": row["provider"],
        "model": row["model"],
        "prompt_protocol": row["prompt_protocol"],
        "output_version": row["output_version"],
        "review_operation_id": str(row["review_operation_id"]),
        "review_operation_request_hash": str(
            row["review_operation_request_hash"]
        ),
        "review_actor": str(row["review_actor"]),
        "review_reason": str(row["review_reason"]),
        "reviewed_at": str(row["reviewed_at"]),
        "review_revision": int(row["review_revision"]),
        "revision_created_at": str(row["revision_created_at"]),
        "revision_updated_at": str(row["revision_updated_at"]),
        "aliases": aliases,
        "evidence": evidence,
    }


def _snapshot_relation_dict(
    row: Row,
    evidence: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "ordinal": int(row["ordinal"]),
        "relation_id": str(row["relation_id"]),
        "relation_revision": int(row["relation_revision"]),
        "source_concept_id": str(row["source_concept_id"]),
        "source_concept_revision": int(row["source_concept_revision"]),
        "target_concept_id": str(row["target_concept_id"]),
        "target_concept_revision": int(row["target_concept_revision"]),
        "relation_type": str(row["relation_type"]),
        "support_basis": str(row["support_basis"]),
        "rationale": str(row["rationale"]),
        "review_status": str(row["review_status"]),
        "validity_status": str(row["validity_status"]),
        "proposal_origin": str(row["proposal_origin"]),
        "provider": row["provider"],
        "model": row["model"],
        "prompt_protocol": row["prompt_protocol"],
        "output_version": row["output_version"],
        "review_operation_id": str(row["review_operation_id"]),
        "review_operation_request_hash": str(
            row["review_operation_request_hash"]
        ),
        "review_actor": str(row["review_actor"]),
        "review_reason": str(row["review_reason"]),
        "reviewed_at": str(row["reviewed_at"]),
        "review_revision": int(row["review_revision"]),
        "binding_created_at": str(row["binding_created_at"]),
        "revision_created_at": str(row["revision_created_at"]),
        "revision_updated_at": str(row["revision_updated_at"]),
        "evidence": evidence,
    }


def _published_concepts_from_rows(
    conn: Connection,
    course_id: str,
    version_number: int,
    rows: list[Row],
) -> list[PublishedConcept]:
    if not rows:
        return []
    identifiers = [str(row["concept_id"]) for row in rows]
    placeholders = ",".join("?" for _ in identifiers)
    alias_rows = conn.execute(
        f"""
        SELECT * FROM concept_graph_version_concept_aliases
        WHERE course_id = ? AND version_number = ?
          AND concept_id IN ({placeholders})
        ORDER BY concept_id, ordinal, alias_id
        """,
        (course_id, version_number, *identifiers),
    ).fetchall()
    evidence_rows = conn.execute(
        f"""
        SELECT * FROM concept_graph_version_concept_evidence
        WHERE course_id = ? AND version_number = ?
          AND concept_id IN ({placeholders})
        ORDER BY concept_id, ordinal, evidence_id
        """,
        (course_id, version_number, *identifiers),
    ).fetchall()
    alias_snapshots: dict[str, list[dict[str, object]]] = defaultdict(list)
    evidence_snapshots: dict[str, list[dict[str, object]]] = defaultdict(list)
    for alias in alias_rows:
        alias_snapshots[str(alias["concept_id"])].append(
            {
                "alias_id": str(alias["alias_id"]),
                "display_text": str(alias["display_text"]),
                "normalized_text": str(alias["normalized_text"]),
                "ordinal": int(alias["ordinal"]),
                "created_at": str(alias["created_at"]),
            }
        )
    for item in evidence_rows:
        evidence_snapshots[str(item["concept_id"])].append(
            _snapshot_evidence_dict(item)
        )
    for row in rows:
        concept_id = str(row["concept_id"])
        aggregate = _snapshot_concept_dict(
            row,
            alias_snapshots.get(concept_id, []),
            evidence_snapshots.get(concept_id, []),
        )
        if _concept_aggregate_hash(aggregate) != row["aggregate_hash"]:
            raise PublicationIntegrityError(
                "Published Concept aggregate failed integrity validation."
            )

    aliases: dict[str, list[PublishedConceptAlias]] = defaultdict(list)
    evidence: dict[str, list[PublishedEvidence]] = defaultdict(list)
    for alias in alias_rows:
        aliases[str(alias["concept_id"])].append(
            PublishedConceptAlias(
                alias_id=alias["alias_id"],
                display_text=alias["display_text"],
                normalized_text=alias["normalized_text"],
                ordinal=alias["ordinal"],
                created_at=datetime.fromisoformat(alias["created_at"]),
            )
        )
    for item in evidence_rows:
        evidence[str(item["concept_id"])].append(
            _published_evidence_from_row(item)
        )
    return [
        PublishedConcept(
            **_published_concept_fields(row),
            aliases=aliases.get(str(row["concept_id"]), []),
            evidence=evidence.get(str(row["concept_id"]), []),
        )
        for row in rows
    ]


def _published_relations_from_rows(
    conn: Connection,
    course_id: str,
    version_number: int,
    rows: list[Row],
) -> list[PublishedRelation]:
    if not rows:
        return []
    identifiers = [str(row["relation_id"]) for row in rows]
    placeholders = ",".join("?" for _ in identifiers)
    evidence_rows = conn.execute(
        f"""
        SELECT * FROM concept_graph_version_relation_evidence
        WHERE course_id = ? AND version_number = ?
          AND relation_id IN ({placeholders})
        ORDER BY relation_id, ordinal, evidence_id
        """,
        (course_id, version_number, *identifiers),
    ).fetchall()
    evidence_snapshots: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in evidence_rows:
        snapshot = _snapshot_evidence_dict(item)
        snapshot["support_role"] = str(item["support_role"])
        evidence_snapshots[str(item["relation_id"])].append(snapshot)
    for row in rows:
        relation_id = str(row["relation_id"])
        aggregate = _snapshot_relation_dict(
            row, evidence_snapshots.get(relation_id, [])
        )
        if _relation_aggregate_hash(aggregate) != row["aggregate_hash"]:
            raise PublicationIntegrityError(
                "Published relation aggregate failed integrity validation."
            )

    evidence: dict[str, list[PublishedRelationEvidence]] = defaultdict(list)
    for item in evidence_rows:
        base = _published_evidence_fields(item)
        evidence[str(item["relation_id"])].append(
            PublishedRelationEvidence(
                **base, support_role=item["support_role"]
            )
        )
    return [
        PublishedRelation(
            **_published_relation_fields(row),
            evidence=evidence.get(str(row["relation_id"]), []),
        )
        for row in rows
    ]


def _published_evidence_fields(row: Row) -> dict[str, object]:
    return {
        "evidence_id": row["evidence_id"],
        "source_id": row["source_id"],
        "chunk_id": row["chunk_id"],
        "chunk_text_hash": row["chunk_text_hash"],
        "projection_generation_id": row["projection_generation_id"],
        "source_title": row["source_title"],
        "source_type": row["source_type"],
        "quote": row["quote"],
        "locator": json.loads(row["locator_json"]),
        "ordinal": row["ordinal"],
        "created_at": datetime.fromisoformat(row["created_at"]),
    }


def _published_evidence_from_row(row: Row) -> PublishedEvidence:
    return PublishedEvidence(**_published_evidence_fields(row))


def _published_concept_fields(row: Row) -> dict[str, object]:
    return {
        "concept_id": row["concept_id"],
        "concept_revision": row["concept_revision"],
        "preferred_name": row["preferred_name"],
        "short_definition": row["short_definition"],
        "identity_status": row["identity_status"],
        "review_status": row["review_status"],
        "validity_status": row["validity_status"],
        "proposal_origin": row["proposal_origin"],
        "provider": row["provider"],
        "model": row["model"],
        "prompt_protocol": row["prompt_protocol"],
        "output_version": row["output_version"],
        "review_operation_id": row["review_operation_id"],
        "review_operation_request_hash": row[
            "review_operation_request_hash"
        ],
        "review_actor": row["review_actor"],
        "review_reason": row["review_reason"],
        "reviewed_at": datetime.fromisoformat(row["reviewed_at"]),
        "review_revision": row["review_revision"],
        "revision_created_at": datetime.fromisoformat(
            row["revision_created_at"]
        ),
        "revision_updated_at": datetime.fromisoformat(
            row["revision_updated_at"]
        ),
        "aggregate_hash": row["aggregate_hash"],
        "ordinal": row["ordinal"],
    }


def _published_relation_fields(row: Row) -> dict[str, object]:
    return {
        "relation_id": row["relation_id"],
        "relation_revision": row["relation_revision"],
        "source_concept_id": row["source_concept_id"],
        "source_concept_revision": row["source_concept_revision"],
        "target_concept_id": row["target_concept_id"],
        "target_concept_revision": row["target_concept_revision"],
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
        "review_operation_id": row["review_operation_id"],
        "review_operation_request_hash": row[
            "review_operation_request_hash"
        ],
        "review_actor": row["review_actor"],
        "review_reason": row["review_reason"],
        "reviewed_at": datetime.fromisoformat(row["reviewed_at"]),
        "review_revision": row["review_revision"],
        "binding_created_at": datetime.fromisoformat(
            row["binding_created_at"]
        ),
        "revision_created_at": datetime.fromisoformat(
            row["revision_created_at"]
        ),
        "revision_updated_at": datetime.fromisoformat(
            row["revision_updated_at"]
        ),
        "aggregate_hash": row["aggregate_hash"],
        "ordinal": row["ordinal"],
    }


def _source_authority_by_versions(
    conn: Connection,
    course_id: str,
    version_numbers: list[int],
) -> dict[int, tuple[list[GraphPublicationIssue], int]]:
    result: dict[int, tuple[list[GraphPublicationIssue], int]] = {
        version: ([], 0) for version in version_numbers
    }
    if not version_numbers:
        return result
    placeholders = ",".join("?" for _ in version_numbers)
    _register_evidence_currentness_function(conn)
    currentness_sql = _evidence_currentness_sql(
        "evidence", "sources", "courses", "chunks"
    )
    rows = conn.execute(
        f"""
        WITH observations AS (
            SELECT evidence.version_number, evidence.evidence_id,
                   evidence.source_id,
                   'concept' AS entity_type,
                   evidence.concept_id AS entity_id,
                   evidence.concept_revision AS entity_revision,
                   {currentness_sql} AS evidence_is_current
            FROM concept_graph_version_concept_evidence AS evidence
            LEFT JOIN sources
                ON sources.id = evidence.source_id
               AND sources.course_id = evidence.course_id
            LEFT JOIN courses
                ON courses.id = evidence.course_id
               AND courses.deleted_at IS NULL
            LEFT JOIN source_chunks AS chunks
                ON chunks.id = evidence.chunk_id
               AND chunks.source_id = evidence.source_id
               AND chunks.is_active = 1
            WHERE evidence.course_id = ?
              AND evidence.version_number IN ({placeholders})
            UNION ALL
            SELECT evidence.version_number, evidence.evidence_id,
                   evidence.source_id,
                   'relation' AS entity_type,
                   evidence.relation_id AS entity_id,
                   evidence.relation_revision AS entity_revision,
                   {currentness_sql} AS evidence_is_current
            FROM concept_graph_version_relation_evidence AS evidence
            LEFT JOIN sources
                ON sources.id = evidence.source_id
               AND sources.course_id = evidence.course_id
            LEFT JOIN courses
                ON courses.id = evidence.course_id
               AND courses.deleted_at IS NULL
            LEFT JOIN source_chunks AS chunks
                ON chunks.id = evidence.chunk_id
               AND chunks.source_id = evidence.source_id
               AND chunks.is_active = 1
            WHERE evidence.course_id = ?
              AND evidence.version_number IN ({placeholders})
        ), stale AS (
            SELECT version_number, evidence_id, source_id, entity_type,
                   entity_id, entity_revision
            FROM observations
            WHERE evidence_is_current = 0
        ), ranked AS (
            SELECT version_number, evidence_id, source_id, entity_type,
                   entity_id, entity_revision,
                   COUNT(*) OVER (PARTITION BY version_number) AS total,
                   ROW_NUMBER() OVER (
                       PARTITION BY version_number
                       ORDER BY entity_type, entity_id, evidence_id
                   ) AS issue_ordinal
            FROM stale
        )
        SELECT * FROM ranked
        WHERE issue_ordinal <= ?
        ORDER BY version_number, issue_ordinal
        """,
        (
            course_id,
            *version_numbers,
            course_id,
            *version_numbers,
            MAX_ISSUES,
        ),
    ).fetchall()
    visible_by_version: dict[int, list[GraphPublicationIssue]] = defaultdict(list)
    count_by_version: dict[int, int] = defaultdict(int)
    for row in rows:
        version_number = int(row["version_number"])
        count_by_version[version_number] = int(row["total"])
        visible_by_version[version_number].append(
            _issue(
                "published_source_authority_stale",
                str(row["entity_type"]),
                str(row["entity_id"]),
                int(row["entity_revision"]),
                "Published evidence no longer resolves to the current "
                "Source projection.",
            )
        )
    for version_number in version_numbers:
        visible = visible_by_version.get(version_number, [])
        visible.sort(key=_issue_sort_key)
        result[version_number] = (
            visible,
            count_by_version.get(version_number, 0),
        )
    return result


def _sqlite_evidence_is_current(
    projection_generation_id: object,
    source_type: object,
    chunk_text_hash: object,
    locator_json: object,
    quote: object,
    current_course_id: object,
    current_source_id: object,
    current_source_status: object,
    current_projection_generation_id: object,
    current_source_type: object,
    current_chunk_id: object,
    current_chunk_text: object,
    current_chunk_text_hash: object,
    current_chunk_locator_json: object,
    source_root_is_current: object,
) -> int:
    return int(
        _evidence_values_are_current(
            projection_generation_id,
            source_type,
            chunk_text_hash,
            locator_json,
            quote,
            current_course_id,
            current_source_id,
            current_source_status,
            current_projection_generation_id,
            current_source_type,
            current_chunk_id,
            current_chunk_text,
            current_chunk_text_hash,
            current_chunk_locator_json,
            source_root_is_current,
        )
    )


@lru_cache(maxsize=256)
def _evidence_values_are_current(
    projection_generation_id: object,
    source_type: object,
    chunk_text_hash: object,
    locator_json: object,
    quote: object,
    current_course_id: object,
    current_source_id: object,
    current_source_status: object,
    current_projection_generation_id: object,
    current_source_type: object,
    current_chunk_id: object,
    current_chunk_text: object,
    current_chunk_text_hash: object,
    current_chunk_locator_json: object,
    source_root_is_current: object,
) -> bool:
    live = _cached_live_observation(
        current_course_id,
        current_source_id,
        current_source_status,
        current_projection_generation_id,
        current_source_type,
        current_chunk_id,
        current_chunk_text,
        current_chunk_text_hash,
        current_chunk_locator_json,
        source_root_is_current,
    )
    if live is None:
        return False
    actual_hash, canonical_locator, text = live
    if (
        projection_generation_id is None
        or projection_generation_id != current_projection_generation_id
        or source_type != current_source_type
    ):
        return False
    if chunk_text_hash != actual_hash or current_chunk_text_hash != actual_hash:
        return False
    try:
        if _canonical_locator_json(locator_json) != canonical_locator:
            return False
    except (TypeError, ValueError):
        return False
    return str(quote) in text


@lru_cache(maxsize=64)
def _cached_live_observation(
    current_course_id: object,
    current_source_id: object,
    current_source_status: object,
    current_projection_generation_id: object,
    current_source_type: object,
    current_chunk_id: object,
    current_chunk_text: object,
    current_chunk_text_hash: object,
    current_chunk_locator_json: object,
    source_root_is_current: object,
) -> tuple[str, str, str] | None:
    if (
        current_course_id is None
        or current_source_id is None
        or current_source_status != "ready"
        or not bool(source_root_is_current)
        or current_projection_generation_id is None
        or current_source_type is None
        or current_chunk_id is None
        or current_chunk_text is None
        or current_chunk_locator_json is None
    ):
        return None
    text = str(current_chunk_text)
    if len(text) > MAX_AUTHORITY_CHUNK_CHARS:
        return None
    locator = str(current_chunk_locator_json)
    if len(locator) > MAX_AUTHORITY_CHUNK_CHARS:
        return None
    try:
        canonical_locator = _canonical_locator_json(locator)
    except (TypeError, ValueError):
        return None
    return hash_source_chunk_text(text), canonical_locator, text
