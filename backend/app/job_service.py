import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from pydantic import BaseModel

from . import course_source_service
from .course import DEFAULT_COURSE_ID
from .card_generation_run_store import delete_runs_for_job
from .course_store import get_course
from .job import VideoJob, VideoJobStatus, utc_now
from .job_store import (
    create_job,
    delete_job,
    get_job,
    list_jobs,
    update_job,
)
from .knowledge_card_store import delete_cards_for_job
from .media_probe import MediaProbeError, probe_video
from .transcript_chunk_store import delete_chunks_for_job
from .transcript_store import load_transcription
from .transcription import TranscriptSegment, TranscriptionResult
from .video_pipeline import VideoPipeline


ALLOWED_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
}

TERMINAL_STATUSES = {
    VideoJobStatus.completed,
    VideoJobStatus.failed,
}


class JobServiceError(Exception):
    pass


class MissingFilenameError(JobServiceError):
    pass


class UnsupportedVideoExtensionError(JobServiceError):
    pass


class UnsupportedContentTypeError(JobServiceError):
    pass


class InvalidVideoError(JobServiceError):
    pass


class JobNotFoundError(JobServiceError):
    pass


class CourseNotFoundError(JobServiceError):
    pass


class InvalidJobStatusError(JobServiceError):
    pass


class TranscriptNotReadyError(JobServiceError):
    pass


class InvalidTranscriptContextError(JobServiceError):
    pass


class TranscriptContext(BaseModel):
    job_id: str
    source_video: str
    start_seconds: float
    end_seconds: float
    segments: list[TranscriptSegment]
    text: str


def create_video_job(
    *,
    video_file: BinaryIO,
    original_filename: str | None,
    content_type: str | None,
    upload_dir: Path,
    course_id: str | None = None,
) -> VideoJob:
    if not original_filename:
        raise MissingFilenameError(
            "Uploaded file must have a filename."
        )

    suffix = Path(original_filename).suffix.lower()

    if suffix not in ALLOWED_VIDEO_EXTENSIONS:
        raise UnsupportedVideoExtensionError(
            f"Unsupported video extension: {suffix or 'none'}"
        )

    if not content_type or not content_type.startswith("video/"):
        raise UnsupportedContentTypeError(
            f"Unsupported content type: {content_type}"
        )

    target_course_id = (course_id or DEFAULT_COURSE_ID).strip()

    if not target_course_id:
        target_course_id = DEFAULT_COURSE_ID

    if get_course(target_course_id) is None:
        raise CourseNotFoundError("Course not found.")

    video_id = uuid4().hex
    destination = upload_dir / f"{video_id}{suffix}"

    try:
        upload_dir.mkdir(parents=True, exist_ok=True)

        video_sha256 = _copy_with_sha256(video_file, destination)

        probe_video(destination)

    except MediaProbeError as exc:
        destination.unlink(missing_ok=True)

        raise InvalidVideoError(
            "Uploaded file is not a valid video."
        ) from exc

    except Exception:
        destination.unlink(missing_ok=True)
        raise

    now = utc_now()
    job = VideoJob(
        id=video_id,
        course_id=target_course_id,
        video_path=destination,
        status=VideoJobStatus.uploaded,
        original_filename=original_filename,
        stored_name=destination.name,
        size_bytes=destination.stat().st_size,
        created_at=now,
        updated_at=now,
    )

    create_job(job, video_sha256=video_sha256)
    course_source_service.sync_video_source(job.id)

    return job


def list_video_jobs() -> list[VideoJob]:
    return list_jobs()


def get_video_job(job_id: str) -> VideoJob:
    job = get_job(job_id)

    if job is None:
        raise JobNotFoundError("Job not found.")

    return job


def start_job(job_id: str) -> VideoJob:
    job = get_video_job(job_id)

    if job.status != VideoJobStatus.uploaded:
        raise InvalidJobStatusError(
            f"Job cannot run from status: {job.status.value}"
        )

    _mark_started(job)
    update_job(job)
    course_source_service.sync_video_source(job.id)

    return job


def retry_job(job_id: str) -> VideoJob:
    job = get_video_job(job_id)

    if job.status != VideoJobStatus.failed:
        raise InvalidJobStatusError(
            f"Job cannot retry from status: {job.status.value}"
        )

    job.metadata = None
    job.transcript_path = None

    _mark_started(job)
    update_job(job)
    course_source_service.sync_video_source(job.id)

    return job


def delete_video_job(
    job_id: str,
    artifact_root: Path,
) -> None:
    job = get_video_job(job_id)

    delete_runs_for_job(job.id)
    delete_cards_for_job(job.id)
    delete_chunks_for_job(job.id)
    delete_job(job.id)
    course_source_service.remove_video_source(job.id)

    _unlink_artifact(job.video_path, artifact_root)

    if job.transcript_path is not None:
        _unlink_artifact(job.transcript_path, artifact_root)

    _unlink_artifact(
        artifact_root / "audio" / f"{job.video_path.stem}.wav",
        artifact_root,
    )


def run_job_pipeline(
    job_id: str,
    get_pipeline: Callable[[], VideoPipeline],
    artifact_root: Path,
) -> None:
    try:
        job = get_video_job(job_id)
    except JobNotFoundError:
        return

    try:
        pipeline = get_pipeline()

        pipeline.process(
            video_path=job.video_path,
            artifact_root=artifact_root,
            job=job,
            on_job_update=save_job_progress,
        )

        save_job_progress(job)

    except Exception as exc:
        job.status = VideoJobStatus.failed
        job.error_message = str(exc)

        save_job_progress(job)


def save_job_progress(job: VideoJob) -> None:
    now = utc_now()

    job.updated_at = now

    if job.status == VideoJobStatus.probing and job.started_at is None:
        job.started_at = now

    if job.status in TERMINAL_STATUSES and job.completed_at is None:
        job.completed_at = now

    update_job(job)
    course_source_service.sync_video_source(job.id)


def get_job_transcript(job_id: str) -> TranscriptionResult:
    job = get_video_job(job_id)

    if job.transcript_path is None:
        raise TranscriptNotReadyError(
            "Transcript is not available for this job."
        )

    try:
        return load_transcription(job.transcript_path)
    except FileNotFoundError as exc:
        raise TranscriptNotReadyError(
            "Transcript file is missing."
        ) from exc


def get_transcript_context(
    job_id: str,
    start_seconds: float,
    end_seconds: float,
) -> TranscriptContext:
    if start_seconds < 0 or end_seconds < 0:
        raise InvalidTranscriptContextError(
            "Context times must be non-negative."
        )

    if end_seconds <= start_seconds:
        raise InvalidTranscriptContextError(
            "Context end must be greater than start."
        )

    job = get_video_job(job_id)
    transcript = get_job_transcript(job_id)

    context_segments = [
        segment
        for segment in transcript.segments
        if (
            segment.end_seconds > start_seconds
            and segment.start_seconds < end_seconds
        )
    ]

    return TranscriptContext(
        job_id=job.id,
        source_video=(
            job.original_filename
            or job.stored_name
            or job.video_path.name
        ),
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        segments=context_segments,
        text="\n".join(
            segment.text
            for segment in context_segments
        ),
    )


def _mark_started(job: VideoJob) -> None:
    now = utc_now()

    job.status = VideoJobStatus.probing
    job.error_message = None
    job.started_at = now
    job.completed_at = None
    job.updated_at = now


def _unlink_artifact(path: Path, artifact_root: Path) -> None:
    try:
        resolved_path = path.resolve()
        resolved_root = artifact_root.resolve()
    except OSError:
        return

    if (
        resolved_path != resolved_root
        and resolved_root not in resolved_path.parents
    ):
        return

    if resolved_path.is_file():
        resolved_path.unlink(missing_ok=True)


def _copy_with_sha256(
    source: BinaryIO,
    destination: Path,
    *,
    chunk_size: int = 1024 * 1024,
) -> str:
    digest = hashlib.sha256()
    with destination.open("wb") as output_file:
        while True:
            chunk = source.read(chunk_size)
            if not chunk:
                break
            output_file.write(chunk)
            digest.update(chunk)
    return digest.hexdigest()
