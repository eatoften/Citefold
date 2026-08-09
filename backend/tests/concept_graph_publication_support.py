from __future__ import annotations

from uuid import uuid4

from app.concept_graph import (
    ConceptCreate,
    ConceptRelationCreate,
    GraphReviewRequest,
    RelationReviewRequest,
)
from app.concept_graph_service import (
    create_grounded_concept_candidate,
    create_grounded_relation_candidate,
    review_course_concept,
    review_course_relation,
)
from app.course import Course, CourseCreate
from app.course_service import create_video_course
from app.course_source import (
    CourseSource,
    CourseSourceChunk,
    PdfPageLocator,
    hash_source_chunk_text,
)
from app.course_source_store import replace_source_projection
from app.source_asset import SourceAsset
from app.source_asset_store import create_source_asset


def make_course_source(
    suffix: str,
    *,
    text: str = "Alpha precedes Beta. Gamma contrasts with Alpha.",
) -> tuple[Course, CourseSource, CourseSourceChunk]:
    course = create_video_course(CourseCreate(title=f"Publication {suffix}"))
    asset_id = f"publication-{suffix}-{uuid4().hex}"
    source_id = f"asset:{asset_id}"
    create_source_asset(
        SourceAsset(
            id=asset_id,
            course_id=course.id,
            asset_type="pdf",
            original_filename=f"{suffix}.pdf",
            stored_path=f"{suffix}.pdf",
            size_bytes=1,
            sha256="a" * 64,
            extraction_status="ready",
        )
    )
    source = CourseSource(
        id=source_id,
        course_id=course.id,
        origin_type="source_asset",
        origin_id=asset_id,
        source_type="pdf",
        title=f"{suffix}.pdf",
        content_status="ready",
    )
    chunk = CourseSourceChunk(
        id=f"source_unit:{asset_id}-page-1",
        source_id=source_id,
        origin_type="source_unit",
        origin_id=f"{asset_id}-page-1",
        chunk_type="page",
        ordinal=0,
        text=text,
        text_hash=hash_source_chunk_text(text),
        locator=PdfPageLocator(asset_id=asset_id, page_number=1),
        chunker_version="publication-test-v1",
    )
    replace_source_projection(source, [chunk])
    return course, source, chunk


def accepted_concept(
    course_id: str,
    chunk: CourseSourceChunk,
    name: str,
    quote: str,
):
    candidate = create_grounded_concept_candidate(
        course_id,
        ConceptCreate(
            operation_id=uuid4().hex,
            actor="author@example.test",
            reason="Create a grounded publication candidate.",
            preferred_name=name,
            short_definition=f"Definition for {name}.",
            aliases=[f"{name} alias"],
            evidence=[{"chunk_id": chunk.id, "quote": quote}],
        ),
    )
    return review_course_concept(
        course_id,
        candidate.id,
        GraphReviewRequest(
            operation_id=uuid4().hex,
            actor="reviewer@example.test",
            reason="The evidence directly supports this Concept.",
            expected_revision=candidate.revision,
            decision="accept",
        ),
    )


def accepted_relation(
    course_id: str,
    chunk: CourseSourceChunk,
    source_id: str,
    target_id: str,
    *,
    evidence_quote: str = "Alpha precedes Beta",
):
    candidate = create_grounded_relation_candidate(
        course_id,
        ConceptRelationCreate(
            operation_id=uuid4().hex,
            actor="author@example.test",
            reason="Create a grounded prerequisite candidate.",
            source_concept_id=source_id,
            target_concept_id=target_id,
            relation_type="prerequisite",
            support_basis="source_asserted",
            rationale="The source explicitly orders the Concepts.",
            evidence=[
                {
                    "chunk_id": chunk.id,
                    "quote": evidence_quote,
                    "support_role": "relation_assertion",
                }
            ],
        ),
    )
    assert candidate.endpoint_binding is not None
    return review_course_relation(
        course_id,
        candidate.id,
        RelationReviewRequest(
            operation_id=uuid4().hex,
            actor="reviewer@example.test",
            reason="The evidence directly supports this relation.",
            expected_revision=candidate.revision,
            expected_source_concept_revision=(
                candidate.endpoint_binding.source_concept_revision
            ),
            expected_target_concept_revision=(
                candidate.endpoint_binding.target_concept_revision
            ),
            decision="accept",
        ),
    )
