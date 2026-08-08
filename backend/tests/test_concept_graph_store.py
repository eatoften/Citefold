from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.concept_graph import (
    Concept,
    ConceptRelation,
    EvidenceReferenceCreate,
    RelationEvidenceReferenceCreate,
)
from app.concept_graph_store import (
    EvidenceQuoteMismatchError,
    create_concept_candidate,
    create_relation_candidate,
    get_concept,
    get_relation,
)
from app.course import CourseCreate
from app.course_service import create_video_course
from app.course_source import (
    CourseSource,
    CourseSourceChunk,
    TextSectionLocator,
    hash_source_chunk_text,
)
from app.course_source_store import replace_source_projection
from app.db import connect
from app.job import utc_now
from app.source_asset import SourceAsset
from app.source_asset_store import create_source_asset


def _grounding_fixture() -> tuple[str, CourseSourceChunk]:
    course = create_video_course(CourseCreate(title="Store contract"))
    create_source_asset(
        SourceAsset(
            id="stack-notes",
            course_id=course.id,
            asset_type="text",
            original_filename="Stack notes",
            stored_path="stack-notes.txt",
            size_bytes=1,
            sha256="a" * 64,
            extraction_status="ready",
        )
    )
    text = "A stack follows last-in, first-out order."
    chunk = CourseSourceChunk(
        id="source_unit:stack-section",
        source_id="asset:stack-notes",
        origin_type="source_unit",
        origin_id="stack-section",
        chunk_type="text",
        ordinal=0,
        text=text,
        text_hash=hash_source_chunk_text(text),
        locator=TextSectionLocator(
            asset_id="stack-notes",
            section_number=2,
        ),
        chunker_version="test-store-v1",
    )
    replace_source_projection(
        CourseSource(
            id=chunk.source_id,
            course_id=course.id,
            origin_type="source_asset",
            origin_id="stack-notes",
            source_type="text",
            title="Stack notes",
            content_status="ready",
        ),
        [chunk],
    )
    return course.id, chunk


def _candidate(course_id: str, name: str) -> Concept:
    now = utc_now()
    return Concept(
        id=uuid4().hex,
        course_id=course_id,
        preferred_name=name,
        short_definition=f"Definition of {name}.",
        revision=1,
        review_status="candidate",
        validity_status="current",
        proposal_origin="human",
        created_at=now,
        updated_at=now,
    )


def test_store_atomically_snapshots_grounded_concept_evidence() -> None:
    course_id, chunk = _grounding_fixture()
    valid = _candidate(course_id, "Stack")

    stored = create_concept_candidate(
        valid,
        [
            EvidenceReferenceCreate(
                chunk_id=chunk.id,
                quote="last-in, first-out",
            )
        ],
        [uuid4().hex],
    )

    assert stored.evidence[0].source_id == chunk.source_id
    assert stored.evidence[0].chunk_text_hash == chunk.text_hash
    assert stored.evidence[0].locator.kind == "text_section"

    invalid = _candidate(course_id, "Queue")
    with pytest.raises(EvidenceQuoteMismatchError):
        create_concept_candidate(
            invalid,
            [
                EvidenceReferenceCreate(
                    chunk_id=chunk.id,
                    quote="first-in, first-out",
                )
            ],
            [uuid4().hex],
        )

    assert get_concept(course_id, valid.id) is not None
    assert get_concept(course_id, invalid.id) is None
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == 1
        assert (
            conn.execute("SELECT COUNT(*) FROM concept_evidence").fetchone()[0]
            == 1
        )


def test_current_read_uses_only_current_revision_evidence() -> None:
    course_id, chunk = _grounding_fixture()
    revision_one = _candidate(course_id, "Stack")
    stored = create_concept_candidate(
        revision_one,
        [EvidenceReferenceCreate(chunk_id=chunk.id, quote="last-in")],
        [uuid4().hex],
    )
    revision_two_time = utc_now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO concept_revisions (
                concept_id, course_id, revision, preferred_name,
                short_definition, identity_status, review_status,
                validity_status, proposal_origin, created_at, updated_at
            ) VALUES (?, ?, 2, 'LIFO stack', 'A revised definition.',
                      'active', 'candidate', 'current', 'human', ?, ?)
            """,
            (
                stored.id,
                course_id,
                revision_two_time.isoformat(),
                revision_two_time.isoformat(),
            ),
        )
        conn.execute(
            """
            INSERT INTO concept_evidence (
                id, course_id, concept_id, concept_revision, source_id,
                chunk_id, chunk_text_hash, source_title, source_type, quote,
                locator_json, ordinal, created_at
            ) VALUES (?, ?, ?, 2, ?, ?, ?, 'Stack notes', 'text',
                      'A stack', ?, 0, ?)
            """,
            (
                uuid4().hex,
                course_id,
                stored.id,
                chunk.source_id,
                chunk.id,
                chunk.text_hash,
                json.dumps(chunk.locator.model_dump(mode="json")),
                revision_two_time.isoformat(),
            ),
        )
        conn.execute(
            """
            UPDATE concepts
            SET current_revision = 2, updated_at = ?
            WHERE id = ? AND course_id = ?
            """,
            (revision_two_time.isoformat(), stored.id, course_id),
        )

    current = get_concept(course_id, stored.id)

    assert current is not None
    assert current.revision == 2
    assert current.preferred_name == "LIFO stack"
    assert [item.quote for item in current.evidence] == ["A stack"]
    assert current.evidence[0].projection_generation_id is None
    assert current.evidence[0].projection_is_current is False
    assert "legacy_projection_generation" in (
        current.evidence[0].projection_currentness_reasons
    )
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_revisions WHERE concept_id = ?",
            (stored.id,),
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_evidence WHERE concept_id = ?",
            (stored.id,),
        ).fetchone()[0] == 2
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_relation_current_read_preserves_old_revision_evidence() -> None:
    course_id, chunk = _grounding_fixture()
    left = _candidate(course_id, "Stack")
    right = _candidate(course_id, "LIFO")
    for concept, quote in ((left, "A stack"), (right, "last-in")):
        create_concept_candidate(
            concept,
            [EvidenceReferenceCreate(chunk_id=chunk.id, quote=quote)],
            [uuid4().hex],
        )
    now = utc_now()
    revision_one = ConceptRelation(
        id=uuid4().hex,
        course_id=course_id,
        source_concept_id=left.id,
        target_concept_id=right.id,
        relation_type="prerequisite",
        support_basis="source_asserted",
        rationale="The source establishes the relation.",
        revision=1,
        review_status="candidate",
        validity_status="current",
        proposal_origin="human",
        created_at=now,
        updated_at=now,
    )
    stored = create_relation_candidate(
        revision_one,
        [
            RelationEvidenceReferenceCreate(
                chunk_id=chunk.id,
                quote="A stack follows last-in",
                support_role="relation_assertion",
            )
        ],
        [uuid4().hex],
    )
    revision_two_time = utc_now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO concept_relation_revisions (
                relation_id, course_id, revision, support_basis, rationale,
                review_status, validity_status, proposal_origin,
                created_at, updated_at
            ) VALUES (?, ?, 2, 'source_asserted', 'Revised rationale.',
                      'candidate', 'current', 'human', ?, ?)
            """,
            (
                stored.id,
                course_id,
                revision_two_time.isoformat(),
                revision_two_time.isoformat(),
            ),
        )
        conn.execute(
            """
            INSERT INTO relation_evidence (
                id, course_id, relation_id, relation_revision, support_role,
                source_id, chunk_id, chunk_text_hash, source_title,
                source_type, quote, locator_json, ordinal, created_at
            ) VALUES (?, ?, ?, 2, 'relation_assertion', ?, ?, ?,
                      'Stack notes', 'text', 'first-out order', ?, 0, ?)
            """,
            (
                uuid4().hex,
                course_id,
                stored.id,
                chunk.source_id,
                chunk.id,
                chunk.text_hash,
                json.dumps(chunk.locator.model_dump(mode="json")),
                revision_two_time.isoformat(),
            ),
        )
        conn.execute(
            """
            UPDATE concept_relations
            SET current_revision = 2, updated_at = ?
            WHERE id = ? AND course_id = ?
            """,
            (revision_two_time.isoformat(), stored.id, course_id),
        )

    current = get_relation(course_id, stored.id)

    assert current is not None
    assert current.revision == 2
    assert current.rationale == "Revised rationale."
    assert [item.quote for item in current.evidence] == ["first-out order"]
    assert current.evidence[0].projection_generation_id is None
    assert current.evidence[0].projection_is_current is False
    with connect() as conn:
        assert conn.execute(
            """
            SELECT COUNT(*) FROM concept_relation_revisions
            WHERE relation_id = ?
            """,
            (stored.id,),
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM relation_evidence WHERE relation_id = ?",
            (stored.id,),
        ).fetchone()[0] == 2
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
