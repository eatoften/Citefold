from __future__ import annotations

import logging
from uuid import uuid4

from . import course_source_service
from . import job_service
from . import transcript_chunk_service
from .course_source import SourceIndexRequest, SourceIndexResult
from .course_source_store import (
    begin_source_index,
    fail_source_index,
    list_chunks_for_sources,
)
from .embedding import SentenceTransformerEmbedder, TextEmbedder
from .job import utc_now
from .settings import get_embedding_settings
from .source_chunk_embedding import SourceChunkEmbedding
from .source_chunk_embedding_store import (
    commit_source_index,
    list_source_chunk_embedding_infos,
)


class SourceIndexServiceError(Exception):
    pass


class SourceIndexGenerationError(SourceIndexServiceError):
    pass


class SourceIndexConflictError(SourceIndexServiceError):
    pass


LOGGER = logging.getLogger(__name__)
SAFE_MODEL_FAILURE = (
    "The local embedding model failed. Check its settings and retry."
)


def index_course_sources(
    course_id: str,
    request: SourceIndexRequest | None = None,
    *,
    embedder: TextEmbedder | None = None,
    expected_dimension: int | None = None,
) -> SourceIndexResult:
    index_request = request or SourceIndexRequest()
    active_embedder = embedder or SentenceTransformerEmbedder()
    model_name = _embedder_model_name(active_embedder)
    selected = course_source_service.resolve_course_sources(
        course_id,
        index_request.source_ids,
    )

    try:
        _ensure_video_chunks(
            selected,
            regenerate=index_request.regenerate_video_chunks,
            embedder=active_embedder,
        )
    except (
        transcript_chunk_service.TranscriptChunkServiceError,
        course_source_service.CourseSourceServiceError,
        job_service.JobServiceError,
    ) as exc:
        raise SourceIndexGenerationError(
            "Source chunks could not be prepared for indexing."
        ) from exc
    except Exception as exc:
        LOGGER.exception("Unexpected source chunk preparation failure.")
        raise SourceIndexGenerationError(SAFE_MODEL_FAILURE) from exc

    selected = course_source_service.resolve_course_sources(
        course_id,
        [source.id for source in selected],
    )
    selected_ids = [source.id for source in selected]
    chunks = list_chunks_for_sources(selected_ids)
    chunks_by_source = {
        source_id: [
            chunk
            for chunk in chunks
            if chunk.source_id == source_id
        ]
        for source_id in selected_ids
    }
    available = [
        source
        for source in selected
        if (
            source.content_status == "ready"
            and bool(chunks_by_source[source.id])
        )
    ]
    unavailable_ids = [
        source.id
        for source in selected
        if source not in available
    ]
    available_ids = [source.id for source in available]
    available_chunks = [
        chunk
        for source in available
        for chunk in chunks_by_source[source.id]
    ]

    if not available_chunks:
        return SourceIndexResult(
            source_ids=selected_ids,
            total_sources=len(selected),
            unavailable_source_ids=unavailable_ids,
            total_chunks=0,
            embedded_chunks=0,
            skipped_chunks=0,
            model=model_name,
        )

    generation = str(uuid4())
    if not begin_source_index(
        available_ids,
        expected_course_id=course_id,
        generation=generation,
        model=model_name,
    ):
        raise SourceIndexConflictError(
            "Sources changed before indexing; retry the request."
        )
    try:
        existing_infos = {
            info.chunk_id: info
            for info in list_source_chunk_embedding_infos(
                available_ids,
                model=model_name,
            )
        }
        reported_dimension = (
            expected_dimension
            or _reported_embedding_dimension(active_embedder)
        )
        pending = [
            chunk
            for chunk in available_chunks
            if (
                chunk.id not in existing_infos
                or existing_infos[chunk.id].text_hash != chunk.text_hash
                or (
                    reported_dimension is not None
                    and existing_infos[chunk.id].dimension
                    != reported_dimension
                )
            )
        ]
        existing_dimensions = {
            info.dimension
            for info in existing_infos.values()
        }
        if reported_dimension is None and len(existing_dimensions) > 1:
            pending = list(available_chunks)

        vector_by_chunk_id: dict[str, list[float]] = {}
        if reported_dimension is None and not pending:
            probe_chunk = available_chunks[0]
            probe_vector = _embed_chunks(
                active_embedder,
                [probe_chunk],
            )[0]
            reported_dimension = len(probe_vector)
            if any(
                info.dimension != reported_dimension
                for info in existing_infos.values()
            ):
                pending = list(available_chunks)
                vector_by_chunk_id[probe_chunk.id] = probe_vector

        remaining = [
            chunk
            for chunk in pending
            if chunk.id not in vector_by_chunk_id
        ]
        for chunk, vector in zip(
            remaining,
            _embed_chunks(active_embedder, remaining),
        ):
            vector_by_chunk_id[chunk.id] = vector

        vectors = [
            vector_by_chunk_id[chunk.id]
            for chunk in pending
        ]
        vector_dimensions = {len(vector) for vector in vectors}
        if len(vector_dimensions) > 1:
            raise SourceIndexGenerationError(
                "Embedding model returned inconsistent vector dimensions."
            )
        vector_dimension = next(iter(vector_dimensions), None)
        dimension = (
            expected_dimension
            or reported_dimension
            or vector_dimension
            or _existing_dimension(existing_infos)
        )
        if dimension is None:
            raise SourceIndexGenerationError(
                "Embedding dimension could not be determined."
            )
        if (
            vector_dimension is not None
            and vector_dimension != dimension
        ):
            raise SourceIndexGenerationError(
                "Embedding model returned an unexpected vector dimension."
            )

        pending_ids = {chunk.id for chunk in pending}
        dimension_mismatches = [
            chunk
            for chunk in available_chunks
            if (
                chunk.id not in pending_ids
                and existing_infos[chunk.id].dimension != dimension
            )
        ]
        if dimension_mismatches:
            mismatch_vectors = _embed_chunks(
                active_embedder,
                dimension_mismatches,
            )
            if any(len(vector) != dimension for vector in mismatch_vectors):
                raise SourceIndexGenerationError(
                    "Embedding model returned inconsistent vector dimensions."
                )
            pending.extend(dimension_mismatches)
            vectors.extend(mismatch_vectors)

        now = utc_now()
        commit_result = commit_source_index(
            available_ids,
            expected_course_id=course_id,
            expected_generation=generation,
            model=model_name,
            dimension=dimension,
            indexed_at=now,
            embeddings=[
                SourceChunkEmbedding(
                    chunk_id=chunk.id,
                    source_id=chunk.source_id,
                    model=model_name,
                    dimension=len(vector),
                    text_hash=chunk.text_hash,
                    vector=vector,
                    created_at=now,
                    updated_at=now,
                )
                for chunk, vector in zip(pending, vectors)
            ],
        )
        if commit_result.stale_source_ids:
            raise SourceIndexConflictError(
                "Sources changed while indexing; retry the request."
            )
    except SourceIndexConflictError:
        raise
    except SourceIndexGenerationError as exc:
        fail_source_index(
            available_ids,
            generation=generation,
            model=model_name,
            error=str(exc),
        )
        raise
    except Exception as exc:
        LOGGER.exception("Unexpected source embedding failure.")
        fail_source_index(
            available_ids,
            generation=generation,
            model=model_name,
            error=SAFE_MODEL_FAILURE,
        )
        raise SourceIndexGenerationError(SAFE_MODEL_FAILURE) from exc

    return SourceIndexResult(
        source_ids=selected_ids,
        total_sources=len(selected),
        unavailable_source_ids=unavailable_ids,
        total_chunks=len(available_chunks),
        embedded_chunks=commit_result.committed_embeddings,
        skipped_chunks=len(available_chunks) - len(pending),
        model=model_name,
        dimension=dimension,
    )


def _ensure_video_chunks(
    sources,
    *,
    regenerate: bool,
    embedder: TextEmbedder,
) -> None:
    for source in sources:
        if source.source_type != "video" or source.content_status != "ready":
            continue
        if source.chunk_count and not regenerate:
            continue
        transcript_chunk_service.generate_job_chunks(
            source.origin_id,
            embedder=embedder,
        )


def _embedder_model_name(embedder: TextEmbedder) -> str:
    model_name = getattr(embedder, "model_name", None)
    if isinstance(model_name, str) and model_name.strip():
        return model_name.strip()
    return get_embedding_settings().model


def _existing_dimension(existing_infos) -> int | None:
    for info in existing_infos.values():
        return info.dimension
    return None


def _reported_embedding_dimension(
    embedder: TextEmbedder,
) -> int | None:
    dimension = getattr(embedder, "embedding_dimension", None)
    if dimension is None:
        getter = getattr(embedder, "get_embedding_dimension", None)
        if callable(getter):
            dimension = getter()
    if dimension is None:
        return None
    if not isinstance(dimension, int) or dimension < 1:
        raise SourceIndexGenerationError(
            "Embedding model reported an invalid vector dimension."
        )
    return dimension


def _embed_chunks(
    embedder: TextEmbedder,
    chunks,
) -> list[list[float]]:
    if not chunks:
        return []
    vectors = embedder.embed_texts([chunk.text for chunk in chunks])
    if len(vectors) != len(chunks):
        raise SourceIndexGenerationError(
            "Embedding model returned the wrong number of vectors."
        )
    if any(not vector for vector in vectors):
        raise SourceIndexGenerationError(
            "Embedding model returned an empty vector."
        )
    return vectors
