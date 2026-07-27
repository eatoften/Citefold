from __future__ import annotations

from contextlib import contextmanager
from threading import RLock

from . import course_service
from .course_source import (
    CourseSource,
    CourseSourceChunk,
    DocxParagraphLocator,
    PdfPageLocator,
    PptSlideLocator,
    TextSectionLocator,
    VideoTimeLocator,
    hash_source_chunk_text,
    source_chunk_id,
    source_id_for_asset,
    source_id_for_job,
)
from .course_source_store import (
    delete_source_projections_for_course,
    delete_source_projection,
    get_source,
    list_source_chunks as list_projected_source_chunks,
    list_sources_for_course,
    replace_course_source_projection,
    replace_source_projection,
    recover_active_source_indexes,
    set_source_enabled,
)
from .job import VideoJob, VideoJobStatus
from .job_store import get_job, list_jobs_for_course
from .notebook_note_source import build_note_source_projection
from .notebook_note_store import (
    get_notebook_note,
    list_published_notebook_notes_for_course,
)
from .source_asset import SourceAssetDetail, SourceUnit
from .source_asset_store import (
    get_source_asset,
    list_source_assets_for_course,
    list_source_units_for_asset,
)
from .transcript_chunk import TranscriptChunk
from .transcript_chunk_store import list_chunks_for_job


_reconciliation_lock = RLock()


class CourseSourceServiceError(Exception):
    pass


class CourseSourceNotFoundError(CourseSourceServiceError):
    pass


class CourseSourceScopeError(CourseSourceServiceError):
    pass


class CourseSourceUnavailableError(CourseSourceServiceError):
    pass


@contextmanager
def source_projection_lifecycle():
    """Serialize derived-root snapshots with full projection replacement."""

    with _reconciliation_lock:
        yield


def reconcile_course_sources(course_id: str) -> list[CourseSource]:
    with _reconciliation_lock:
        course = course_service.get_video_course(course_id)
        # Tombstoned roots remain part of the derived projection so restore
        # keeps chunk identity, embeddings, and historical citations intact.
        jobs = list_jobs_for_course(course.id, include_deleted=True)
        assets = list_source_assets_for_course(
            course.id,
            include_deleted=True,
        )
        sources: list[CourseSource] = []
        chunks: list[CourseSourceChunk] = []

        for job in jobs:
            source = _source_from_job(job)
            sources.append(source)
            chunks.extend(
                _chunk_from_transcript(source.id, chunk)
                for chunk in list_chunks_for_job(job.id)
            )

        for asset in assets:
            source = _source_from_asset(asset)
            sources.append(source)
            chunks.extend(
                _chunk_from_source_unit(source, unit)
                for unit in list_source_units_for_asset(asset.id)
            )

        for note, snapshot in list_published_notebook_notes_for_course(
            course.id,
            include_deleted=True,
        ):
            source, note_chunks = build_note_source_projection(note, snapshot)
            sources.append(source)
            chunks.extend(note_chunks)

        replace_course_source_projection(course.id, sources, chunks)
        return _active_sources(list_sources_for_course(course.id))


def list_course_sources(course_id: str) -> list[CourseSource]:
    course = course_service.get_video_course(course_id)
    return _active_sources(list_sources_for_course(course.id))


def get_course_source(source_id: str) -> CourseSource:
    source = get_source(source_id)
    if source is None or not _source_root_is_active(source):
        raise CourseSourceNotFoundError("Source not found.")
    return source


def list_source_chunks(
    source_id: str,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[CourseSourceChunk]:
    source = get_course_source(source_id)
    return list_projected_source_chunks(
        source.id,
        limit=limit,
        offset=offset,
    )


def update_source_enabled(
    source_id: str,
    *,
    enabled: bool,
) -> CourseSource:
    source = get_course_source(source_id)
    if not set_source_enabled(source.id, enabled):
        raise CourseSourceNotFoundError("Source not found.")
    updated = get_source(source.id)
    if updated is None:
        raise CourseSourceNotFoundError("Source not found.")
    return updated


def resolve_course_sources(
    course_id: str,
    source_ids: list[str],
    *,
    require_ready: bool = False,
) -> list[CourseSource]:
    sources = list_course_sources(course_id)
    sources_by_id = {source.id: source for source in sources}

    if source_ids:
        missing = [
            source_id
            for source_id in source_ids
            if source_id not in sources_by_id
        ]
        if missing:
            raise CourseSourceScopeError(
                "Selected sources do not belong to this course: "
                + ", ".join(missing)
            )
        selected = [sources_by_id[source_id] for source_id in source_ids]
    else:
        selected = [source for source in sources if source.enabled]

    if require_ready:
        unavailable = [
            source.id
            for source in selected
            if source.content_status != "ready"
        ]
        if unavailable:
            raise CourseSourceUnavailableError(
                "Selected sources are not ready: " + ", ".join(unavailable)
            )

    return selected


def sync_video_source(job_id: str) -> CourseSource:
    with _reconciliation_lock:
        job = get_job(job_id)
        source_id = source_id_for_job(job_id)
        if job is None:
            delete_source_projection(source_id)
            raise CourseSourceNotFoundError("Source not found.")
        source = _source_from_job(job)
        chunks = [
            _chunk_from_transcript(source.id, chunk)
            for chunk in list_chunks_for_job(job.id)
        ]
        replace_source_projection(source, chunks)
        synced = get_source(source.id)
        if synced is None:
            raise CourseSourceNotFoundError("Source not found.")
        return synced


def sync_source_asset(asset_id: str) -> CourseSource:
    with _reconciliation_lock:
        asset = get_source_asset(asset_id)
        source_id = source_id_for_asset(asset_id)
        if asset is None:
            delete_source_projection(source_id)
            raise CourseSourceNotFoundError("Source not found.")
        source = _source_from_asset(asset)
        chunks = [
            _chunk_from_source_unit(source, unit)
            for unit in list_source_units_for_asset(asset.id)
        ]
        replace_source_projection(source, chunks)
        synced = get_source(source.id)
        if synced is None:
            raise CourseSourceNotFoundError("Source not found.")
        return synced


def remove_video_source(job_id: str) -> None:
    with _reconciliation_lock:
        delete_source_projection(source_id_for_job(job_id))


def remove_asset_source(asset_id: str) -> None:
    with _reconciliation_lock:
        delete_source_projection(source_id_for_asset(asset_id))


def remove_course_sources(course_id: str) -> None:
    with _reconciliation_lock:
        delete_source_projections_for_course(course_id)


def recover_interrupted_source_indexes() -> int:
    return recover_active_source_indexes(
        error_message=(
            "The app stopped before indexing finished. Retry indexing."
        )
    )


def _active_sources(
    sources: list[CourseSource],
) -> list[CourseSource]:
    return [
        source
        for source in sources
        if _source_root_is_active(source)
    ]


def _source_root_is_active(source: CourseSource) -> bool:
    try:
        course_service.get_video_course(source.course_id)
    except course_service.CourseServiceError:
        return False
    if source.origin_type == "video_job":
        return get_job(source.origin_id) is not None
    if source.origin_type == "source_asset":
        return get_source_asset(source.origin_id) is not None
    if source.origin_type == "notebook_note":
        return (
            get_notebook_note(source.course_id, source.origin_id) is not None
        )
    return False


def move_course_sources(
    source_course_id: str,
    target_course_id: str,
) -> None:
    from .course_source_store import move_sources_to_course

    with _reconciliation_lock:
        move_sources_to_course(source_course_id, target_course_id)


def _source_from_job(job: VideoJob) -> CourseSource:
    metadata = (
        job.metadata.model_dump(mode="json")
        if job.metadata is not None
        else {}
    )
    content_status = {
        VideoJobStatus.uploaded: "pending",
        VideoJobStatus.probing: "processing",
        VideoJobStatus.extracting_audio: "processing",
        VideoJobStatus.transcribing: "processing",
        VideoJobStatus.completed: "ready",
        VideoJobStatus.failed: "failed",
        VideoJobStatus.canceled: "failed",
    }[job.status]
    return CourseSource(
        id=source_id_for_job(job.id),
        course_id=job.course_id,
        origin_type="video_job",
        origin_id=job.id,
        source_type="video",
        title=(
            job.original_filename
            or job.stored_name
            or job.video_path.name
        ),
        content_status=content_status,
        size_bytes=job.size_bytes,
        metadata=metadata,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _source_from_asset(asset: SourceAssetDetail) -> CourseSource:
    metadata = dict(asset.metadata)
    if asset.job_id:
        metadata["job_id"] = asset.job_id
    return CourseSource(
        id=source_id_for_asset(asset.id),
        course_id=asset.course_id,
        origin_type="source_asset",
        origin_id=asset.id,
        source_type=asset.asset_type,
        title=asset.original_filename,
        content_status=asset.extraction_status,
        size_bytes=asset.size_bytes,
        mime_type=asset.mime_type,
        metadata=metadata,
        error_message=asset.error_message,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
    )


def _chunk_from_transcript(
    source_id: str,
    chunk: TranscriptChunk,
) -> CourseSourceChunk:
    return CourseSourceChunk(
        id=source_chunk_id("transcript_chunk", chunk.id),
        source_id=source_id,
        origin_type="transcript_chunk",
        origin_id=chunk.id,
        chunk_type="transcript",
        ordinal=chunk.chunk_index,
        text=chunk.text,
        text_hash=hash_source_chunk_text(chunk.text),
        locator=VideoTimeLocator(
            job_id=chunk.job_id,
            start_seconds=chunk.start_seconds,
            end_seconds=chunk.end_seconds,
            segment_ids=chunk.segment_ids,
        ),
        chunker_version=chunk.chunker_version,
        created_at=chunk.created_at,
        updated_at=chunk.created_at,
    )


def _chunk_from_source_unit(
    source: CourseSource,
    unit: SourceUnit,
) -> CourseSourceChunk:
    locator = _locator_for_source_unit(source, unit)
    chunk_type = (
        "transcript"
        if unit.unit_type == "transcript_segment"
        else unit.unit_type
    )
    return CourseSourceChunk(
        id=source_chunk_id("source_unit", unit.id),
        source_id=source.id,
        origin_type="source_unit",
        origin_id=unit.id,
        chunk_type=chunk_type,
        ordinal=unit.ordinal,
        text=unit.text,
        text_hash=hash_source_chunk_text(unit.text),
        locator=locator,
        chunker_version="source-unit-v1",
        created_at=unit.created_at,
        updated_at=unit.created_at,
    )


def _locator_for_source_unit(
    source: CourseSource,
    unit: SourceUnit,
):
    raw = unit.locator
    metadata = {
        key: value
        for key, value in raw.items()
        if key
        not in {
            "start_seconds",
            "end_seconds",
            "timestamp_seconds",
            "segment_ids",
            "page_number",
            "slide_number",
            "paragraph_number",
            "section_number",
        }
    }
    if (
        source.source_type in {"video", "audio"}
        or unit.unit_type in {"transcript_segment", "video_frame"}
    ):
        start_seconds, end_seconds = _video_time_range(raw)
        return VideoTimeLocator(
            job_id=(
                str(source.metadata["job_id"])
                if source.metadata.get("job_id")
                else None
            ),
            asset_id=(
                None
                if source.metadata.get("job_id")
                else source.origin_id
            ),
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            segment_ids=_int_list(raw.get("segment_ids")),
            metadata=metadata,
        )
    if source.source_type == "pdf":
        return PdfPageLocator(
            asset_id=source.origin_id,
            page_number=_positive_int(
                raw.get("page_number"),
                unit.ordinal + 1,
            ),
            metadata=metadata,
        )
    if source.source_type == "pptx":
        return PptSlideLocator(
            asset_id=source.origin_id,
            slide_number=_positive_int(
                raw.get("slide_number"),
                unit.ordinal + 1,
            ),
            metadata=metadata,
        )
    if source.source_type == "docx":
        return DocxParagraphLocator(
            asset_id=source.origin_id,
            paragraph_number=_positive_int(
                raw.get("paragraph_number"),
                unit.ordinal + 1,
            ),
            metadata=metadata,
        )
    return TextSectionLocator(
        asset_id=source.origin_id,
        section_number=_positive_int(
            raw.get("section_number"),
            unit.ordinal + 1,
        ),
        metadata=metadata,
    )


def _positive_int(value: object, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed >= 1 else fallback


def _non_negative_float(value: object, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed >= 0 else fallback


def _video_time_range(
    raw: dict[str, object],
) -> tuple[float, float]:
    timestamp = _non_negative_float(raw.get("timestamp_seconds"), 0)
    start = _non_negative_float(raw.get("start_seconds"), timestamp)
    end = _non_negative_float(raw.get("end_seconds"), start)
    return start, max(start, end)


def _int_list(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    parsed: list[int] = []
    for item in value:
        try:
            parsed.append(int(item))
        except (TypeError, ValueError):
            continue
    return parsed
