from . import course_service
from . import course_source_service
from . import job_service
from .embedding import EmbeddingError, SentenceTransformerEmbedder, TextEmbedder
from .transcript_chunk import TranscriptChunk, TranscriptChunkGenerationRequest
from .transcript_chunk_store import (
    create_chunks,
    list_chunks_for_course,
    list_chunks_for_job,
    replace_chunks_for_job,
)
from .transcript_chunker import (
    SemanticChunkerConfig,
    chunk_transcript_segments,
)


class TranscriptChunkServiceError(Exception):
    pass


class InvalidTranscriptChunkConfigError(TranscriptChunkServiceError):
    pass


class TranscriptChunkGenerationError(TranscriptChunkServiceError):
    pass


def generate_job_chunks(
    job_id: str,
    request: TranscriptChunkGenerationRequest | None = None,
    *,
    embedder: TextEmbedder | None = None,
) -> list[TranscriptChunk]:
    job = job_service.get_video_job(job_id)
    transcript = job_service.get_job_transcript(job.id)
    chunk_request = request or TranscriptChunkGenerationRequest()
    config = _config_from_request(chunk_request)
    active_embedder = embedder or _create_default_embedder()

    try:
        chunks = chunk_transcript_segments(
            transcript.segments,
            embedder=active_embedder,
            config=config,
            course_id=job.course_id,
            job_id=job.id,
        )
    except EmbeddingError as exc:
        raise TranscriptChunkGenerationError(str(exc)) from exc
    except ValueError as exc:
        raise InvalidTranscriptChunkConfigError(str(exc)) from exc

    if chunk_request.replace_existing:
        replace_chunks_for_job(job.id, chunks)
    else:
        create_chunks(chunks)

    course_source_service.sync_video_source(job.id)

    return chunks


def generate_course_chunks(
    course_id: str,
    request: TranscriptChunkGenerationRequest | None = None,
    *,
    embedder: TextEmbedder | None = None,
) -> list[TranscriptChunk]:
    course = course_service.get_video_course(course_id)
    jobs = course_service.list_course_jobs(course.id)
    chunk_request = request or TranscriptChunkGenerationRequest()
    active_embedder = embedder or _create_default_embedder()
    generated_chunks: list[TranscriptChunk] = []

    for job in jobs:
        if job.transcript_path is None:
            continue

        generated_chunks.extend(
            generate_job_chunks(
                job.id,
                chunk_request,
                embedder=active_embedder,
            )
        )

    return generated_chunks


def list_job_chunks(job_id: str) -> list[TranscriptChunk]:
    job = job_service.get_video_job(job_id)

    return list_chunks_for_job(job.id)


def list_course_transcript_chunks(course_id: str) -> list[TranscriptChunk]:
    course = course_service.get_video_course(course_id)

    return list_chunks_for_course(course.id)


def _config_from_request(
    request: TranscriptChunkGenerationRequest,
) -> SemanticChunkerConfig:
    try:
        return SemanticChunkerConfig(
            context_radius=request.context_radius,
            min_chunk_seconds=request.min_chunk_seconds,
            max_chunk_seconds=request.max_chunk_seconds,
            boundary_percentile=request.boundary_percentile,
        )
    except ValueError as exc:
        raise InvalidTranscriptChunkConfigError(str(exc)) from exc


def _create_default_embedder() -> TextEmbedder:
    return SentenceTransformerEmbedder()
