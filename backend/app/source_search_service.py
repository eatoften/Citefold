from __future__ import annotations

import logging

from . import course_source_service
from .course_source import (
    SourceIndexRequest,
    SourceSearchRequest,
    SourceSearchResponse,
    SourceSearchResult,
)
from .course_source_store import list_chunks_for_course_sources
from .embedding import (
    SentenceTransformerEmbedder,
    TextEmbedder,
    cosine_similarity,
)
from .source_chunk_embedding_store import list_source_chunk_embeddings
from .source_index_service import (
    SourceIndexConflictError,
    SourceIndexGenerationError,
    index_course_sources,
)


class SourceSearchServiceError(Exception):
    pass


class SourceSearchError(SourceSearchServiceError):
    pass


class SourceSearchConflictError(SourceSearchServiceError):
    pass


LOGGER = logging.getLogger(__name__)
SAFE_SEARCH_MODEL_FAILURE = (
    "The local embedding model failed. Check its settings and retry."
)


def search_course_sources(
    course_id: str,
    request: SourceSearchRequest,
    *,
    embedder: TextEmbedder | None = None,
) -> SourceSearchResponse:
    selected = course_source_service.resolve_course_sources(
        course_id,
        request.source_ids,
    )
    if not selected:
        return SourceSearchResponse(
            question=request.question,
            results=[],
        )

    active_embedder = embedder or SentenceTransformerEmbedder()
    try:
        query_vectors = active_embedder.embed_texts([request.question])
    except Exception as exc:
        LOGGER.exception("Unexpected source search embedding failure.")
        raise SourceSearchError(SAFE_SEARCH_MODEL_FAILURE) from exc
    if len(query_vectors) != 1 or not query_vectors[0]:
        raise SourceSearchError(
            "Embedding model returned the wrong number of query vectors."
        )
    query_vector = query_vectors[0]

    try:
        index_result = index_course_sources(
            course_id,
            SourceIndexRequest(
                source_ids=[source.id for source in selected],
            ),
            embedder=active_embedder,
            expected_dimension=len(query_vector),
        )
    except SourceIndexConflictError as exc:
        raise SourceSearchConflictError(str(exc)) from exc
    except SourceIndexGenerationError as exc:
        raise SourceSearchError(str(exc)) from exc

    unavailable = set(index_result.unavailable_source_ids)
    selected_ids = [
        source_id
        for source_id in index_result.source_ids
        if source_id not in unavailable
    ]
    if not selected_ids:
        return SourceSearchResponse(
            question=request.question,
            results=[],
        )

    sources = course_source_service.resolve_course_sources(
        course_id,
        selected_ids,
    )
    sources_by_id = {source.id: source for source in sources}
    chunks = list_chunks_for_course_sources(
        course_id,
        selected_ids,
    )
    chunks_by_id = {chunk.id: chunk for chunk in chunks}
    embeddings = list_source_chunk_embeddings(
        selected_ids,
        expected_course_id=course_id,
        model=index_result.model,
    )
    candidates = [
        (chunks_by_id[embedding.chunk_id], embedding)
        for embedding in embeddings
        if (
            embedding.chunk_id in chunks_by_id
            and embedding.text_hash
            == chunks_by_id[embedding.chunk_id].text_hash
        )
    ]
    if not candidates:
        return SourceSearchResponse(
            question=request.question,
            results=[],
        )

    results: list[SourceSearchResult] = []
    for chunk, embedding in candidates:
        if len(query_vector) != embedding.dimension:
            raise SourceSearchError(
                "Query and source embedding dimensions do not match."
            )
        score = cosine_similarity(query_vector, embedding.vector)
        if request.min_score is not None and score < request.min_score:
            continue
        source = sources_by_id[chunk.source_id]
        results.append(
            SourceSearchResult(
                chunk_id=chunk.id,
                source_id=source.id,
                source_title=source.title,
                source_type=source.source_type,
                chunk_type=chunk.chunk_type,
                quote=chunk.text,
                score=score,
                locator=chunk.locator,
            )
        )

    return SourceSearchResponse(
        question=request.question,
        results=sorted(
            results,
            key=lambda item: (-item.score, item.source_id, item.chunk_id),
        )[:request.top_k],
    )
