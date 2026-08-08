from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import uuid4

import pytest

from app.concept_graph import (
    Concept,
    ConceptRelation,
    EvidenceReferenceCreate,
    GraphOperationRequest,
    RelationEvidenceReferenceCreate,
)
from app.concept_graph_store import (
    EvidenceChunkNotFoundError,
    GraphEvidenceStaleError,
    create_concept_candidate,
    create_relation_candidate,
    get_concept,
    get_relation,
)
from app.course import CourseCreate
from app.course_service import (
    create_video_course,
    delete_video_course,
    restore_video_course,
)
from app.course_source import (
    CourseSource,
    CourseSourceChunk,
    PdfPageLocator,
    VideoTimeLocator,
    hash_source_chunk_text,
)
from app.course_source_store import (
    delete_source_projection,
    list_source_chunks,
    move_sources_to_course,
    replace_source_projection,
)
from app.db import connect
from app.job import VideoJob, VideoJobStatus, utc_now
from app.job_store import create_job, delete_job, purge_job, restore_job
from app.notebook_note import NotebookNoteCreate, NotebookNotePromotionRequest
from app.notebook_note_service import (
    create_course_notebook_note,
    publish_notebook_note_as_source,
)
from app.notebook_note_store import (
    purge_notebook_note,
    restore_notebook_note,
    soft_delete_notebook_note,
)
from app.source_asset import SourceAsset
from app.source_asset_store import (
    create_source_asset,
    delete_source_asset,
    purge_source_asset,
    restore_source_asset,
)


@dataclass(frozen=True)
class _OriginProjection:
    course_id: str
    source: CourseSource
    chunk: CourseSourceChunk
    quote: str
    soft_delete: Callable[[], None]
    restore: Callable[[], None]
    purge: Callable[[], None]


def _operation_args() -> dict[str, object]:
    return {
        "operation": GraphOperationRequest(
            operation_id=uuid4().hex,
            actor="currentness-test",
            reason="Create a grounded test aggregate.",
        ),
        "request_hash": uuid4().hex + uuid4().hex,
    }


def _candidate(course_id: str, name: str) -> Concept:
    now = utc_now()
    return Concept(
        id=uuid4().hex,
        course_id=course_id,
        preferred_name=name,
        short_definition=f"Definition of {name}.",
        created_at=now,
        updated_at=now,
    )


def _ground_concept(
    course_id: str,
    chunk: CourseSourceChunk,
    quote: str,
    *,
    name: str = "Projection identity",
) -> Concept:
    return create_concept_candidate(
        _candidate(course_id, name),
        [EvidenceReferenceCreate(chunk_id=chunk.id, quote=quote)],
        [uuid4().hex],
        **_operation_args(),
    )


def _asset_projection(suffix: str) -> _OriginProjection:
    course = create_video_course(CourseCreate(title=f"Asset {suffix}"))
    asset_id = f"asset-{suffix}"
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
    quote = "Asset evidence"
    text = f"{quote} remains addressable."
    source = CourseSource(
        id=f"asset:{asset_id}",
        course_id=course.id,
        origin_type="source_asset",
        origin_id=asset_id,
        source_type="pdf",
        title=f"{suffix}.pdf",
        content_status="ready",
    )
    chunk = CourseSourceChunk(
        id=f"source_unit:{asset_id}-page",
        source_id=source.id,
        origin_type="source_unit",
        origin_id=f"{asset_id}-page",
        chunk_type="page",
        ordinal=0,
        text=text,
        text_hash=hash_source_chunk_text(text),
        locator=PdfPageLocator(asset_id=asset_id, page_number=1),
        chunker_version="currentness-v1",
    )
    replace_source_projection(source, [chunk])

    def soft_delete() -> None:
        assert delete_source_asset(asset_id)

    def restore() -> None:
        assert restore_source_asset(asset_id)

    def purge() -> None:
        assert purge_source_asset(asset_id)
        delete_source_projection(source.id)

    return _OriginProjection(
        course.id,
        source,
        chunk,
        quote,
        soft_delete,
        restore,
        purge,
    )


def _video_projection(suffix: str) -> _OriginProjection:
    course = create_video_course(CourseCreate(title=f"Video {suffix}"))
    job_id = f"job-{suffix}"
    create_job(
        VideoJob(
            id=job_id,
            course_id=course.id,
            video_path=Path(f"{suffix}.mp4"),
            status=VideoJobStatus.completed,
            original_filename=f"{suffix}.mp4",
        )
    )
    quote = "Video evidence"
    text = f"{quote} remains addressable."
    source = CourseSource(
        id=f"job:{job_id}",
        course_id=course.id,
        origin_type="video_job",
        origin_id=job_id,
        source_type="video",
        title=f"{suffix}.mp4",
        content_status="ready",
    )
    chunk = CourseSourceChunk(
        id=f"transcript_chunk:{job_id}-chunk",
        source_id=source.id,
        origin_type="transcript_chunk",
        origin_id=f"{job_id}-chunk",
        chunk_type="transcript",
        ordinal=0,
        text=text,
        text_hash=hash_source_chunk_text(text),
        locator=VideoTimeLocator(
            job_id=job_id,
            start_seconds=1,
            end_seconds=3,
        ),
        chunker_version="currentness-v1",
    )
    replace_source_projection(source, [chunk])

    def soft_delete() -> None:
        assert delete_job(job_id)

    def restore() -> None:
        assert restore_job(job_id)

    def purge() -> None:
        assert purge_job(job_id)
        delete_source_projection(source.id)

    return _OriginProjection(
        course.id,
        source,
        chunk,
        quote,
        soft_delete,
        restore,
        purge,
    )


def _note_projection(suffix: str) -> _OriginProjection:
    course = create_video_course(CourseCreate(title=f"Note {suffix}"))
    quote = "Notebook evidence"
    note = create_course_notebook_note(
        course.id,
        NotebookNoteCreate(
            title=f"Note {suffix}",
            body_markdown=f"# Evidence\n\n{quote} remains addressable.",
        ),
    )
    published = publish_notebook_note_as_source(
        course.id,
        note.id,
        NotebookNotePromotionRequest(expected_revision=1),
    )
    chunks = list_source_chunks(published.source.id)
    chunk = next(item for item in chunks if quote in item.text)

    def soft_delete() -> None:
        deleted = soft_delete_notebook_note(
            course.id,
            note.id,
            expected_revision=1,
        )
        assert deleted is not None

    def restore() -> None:
        assert restore_notebook_note(course.id, note.id) is not None

    def purge() -> None:
        assert purge_notebook_note(course.id, note.id)

    return _OriginProjection(
        course.id,
        published.source,
        chunk,
        quote,
        soft_delete,
        restore,
        purge,
    )


@pytest.mark.parametrize(
    "builder",
    [_asset_projection, _video_projection, _note_projection],
    ids=["source-asset", "video-job", "notebook-note"],
)
def test_origin_delete_restore_and_purge_control_evidence_currentness(
    builder: Callable[[str], _OriginProjection],
) -> None:
    projection = builder(uuid4().hex[:8])
    concept = _ground_concept(
        projection.course_id,
        projection.chunk,
        projection.quote,
    )
    initial = get_concept(projection.course_id, concept.id)
    assert initial is not None
    assert initial.evidence[0].projection_is_current is True
    generation_id = initial.evidence[0].projection_generation_id

    projection.soft_delete()
    deleted = get_concept(projection.course_id, concept.id)
    assert deleted is not None
    assert deleted.evidence[0].projection_is_current is False
    assert "source_root_unavailable" in (
        deleted.evidence[0].projection_currentness_reasons
    )

    projection.restore()
    restored = get_concept(projection.course_id, concept.id)
    assert restored is not None
    assert restored.evidence[0].projection_is_current is True, (
        restored.evidence[0].projection_currentness_reasons
    )
    assert restored.evidence[0].projection_generation_id == generation_id

    projection.soft_delete()
    projection.purge()
    purged = get_concept(projection.course_id, concept.id)
    assert purged is not None
    assert purged.evidence[0].projection_is_current is False
    assert "source_unavailable" in (
        purged.evidence[0].projection_currentness_reasons
    )


def test_locator_drift_then_revert_never_revives_old_graph_evidence() -> None:
    projection = _asset_projection("drift-revert-currentness")
    left = _ground_concept(
        projection.course_id,
        projection.chunk,
        "Asset evidence",
        name="Asset",
    )
    right = _ground_concept(
        projection.course_id,
        projection.chunk,
        "addressable",
        name="Address",
    )
    original_generation = left.evidence[0].projection_generation_id
    drifted_chunk = projection.chunk.model_copy(
        update={
            "locator": PdfPageLocator(
                asset_id=projection.source.origin_id,
                page_number=2,
            )
        }
    )
    replace_source_projection(projection.source, [drifted_chunk])
    drifted = get_concept(projection.course_id, left.id)
    assert drifted is not None
    assert drifted.evidence[0].projection_is_current is False
    assert set(drifted.evidence[0].projection_currentness_reasons).issuperset(
        {"projection_generation_mismatch", "locator_mismatch"}
    )

    replace_source_projection(projection.source, [projection.chunk])
    reverted = get_concept(projection.course_id, left.id)
    assert reverted is not None
    assert reverted.evidence[0].projection_is_current is False
    assert reverted.evidence[0].projection_generation_id == original_generation
    assert reverted.evidence[0].projection_currentness_reasons == [
        "projection_generation_mismatch"
    ]

    now = utc_now()
    inference = ConceptRelation(
        id=uuid4().hex,
        course_id=projection.course_id,
        source_concept_id=left.id,
        target_concept_id=right.id,
        relation_type="prerequisite",
        support_basis="pedagogical_inference",
        rationale="The learner should understand Asset first.",
        created_at=now,
        updated_at=now,
    )
    # G1.2b now fails before relation support comparison because endpoint
    # evidence from an obsolete projection is not bindable at all.
    with pytest.raises(GraphEvidenceStaleError):
        create_relation_candidate(
            inference,
            [
                RelationEvidenceReferenceCreate(
                    chunk_id=projection.chunk.id,
                    quote="Asset evidence",
                    support_role="source_endpoint",
                ),
                RelationEvidenceReferenceCreate(
                    chunk_id=projection.chunk.id,
                    quote="addressable",
                    support_role="target_endpoint",
                ),
            ],
            [uuid4().hex, uuid4().hex],
            **_operation_args(),
        )


def test_relation_snapshots_generation_and_course_scope_fences_currentness() -> None:
    projection = _asset_projection("relation-generation")
    left = _ground_concept(
        projection.course_id,
        projection.chunk,
        "Asset evidence",
        name="Asset",
    )
    right = _ground_concept(
        projection.course_id,
        projection.chunk,
        "addressable",
        name="Address",
    )
    now = utc_now()
    relation = create_relation_candidate(
        ConceptRelation(
            id=uuid4().hex,
            course_id=projection.course_id,
            source_concept_id=left.id,
            target_concept_id=right.id,
            relation_type="prerequisite",
            support_basis="source_asserted",
            rationale="The source states both ideas together.",
            created_at=now,
            updated_at=now,
        ),
        [
            RelationEvidenceReferenceCreate(
                chunk_id=projection.chunk.id,
                quote="Asset evidence remains addressable",
                support_role="relation_assertion",
            )
        ],
        [uuid4().hex],
        **_operation_args(),
    )
    assert relation.evidence[0].projection_generation_id
    assert relation.evidence[0].projection_is_current is True

    target = create_video_course(CourseCreate(title="Moved target"))
    move_sources_to_course(projection.course_id, target.id)
    moved = get_relation(projection.course_id, relation.id)
    assert moved is not None
    assert moved.evidence[0].projection_is_current is False
    assert "source_unavailable" in (
        moved.evidence[0].projection_currentness_reasons
    )


def test_course_delete_restore_controls_evidence_currentness() -> None:
    projection = _asset_projection("course-lifecycle")
    concept = _ground_concept(
        projection.course_id,
        projection.chunk,
        projection.quote,
    )
    delete_video_course(projection.course_id)
    deleted = get_concept(projection.course_id, concept.id)
    assert deleted is not None
    assert deleted.evidence[0].projection_is_current is False
    assert "course_unavailable" in (
        deleted.evidence[0].projection_currentness_reasons
    )
    restore_video_course(projection.course_id)
    restored = get_concept(projection.course_id, concept.id)
    assert restored is not None
    assert restored.evidence[0].projection_is_current is False
    assert set(restored.evidence[0].projection_currentness_reasons).issuperset(
        {"projection_generation_mismatch", "chunk_unavailable"}
    )


def test_currentness_recomputes_text_hash_instead_of_trusting_chunk_row() -> None:
    projection = _asset_projection("defensive-hash")
    concept = _ground_concept(
        projection.course_id,
        projection.chunk,
        projection.quote,
    )
    with connect() as conn:
        conn.execute(
            "UPDATE source_chunks SET text = text || ' tampered' WHERE id = ?",
            (projection.chunk.id,),
        )

    current = get_concept(projection.course_id, concept.id)
    assert current is not None
    assert current.evidence[0].projection_is_current is False
    assert "chunk_hash_mismatch" in (
        current.evidence[0].projection_currentness_reasons
    )
    with pytest.raises(EvidenceChunkNotFoundError):
        _ground_concept(
            projection.course_id,
            projection.chunk,
            projection.quote,
            name="Tampered",
        )
