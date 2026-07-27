from collections.abc import Callable
from hashlib import sha256
from uuid import uuid4

from . import card_service
from . import job_service
from . import knowledge_card_service
from . import transcript_chunk_service
from .card_generation_run import (
    AutoCardGenerationRequest,
    CardGenerationRun,
)
from .card_generation_chunk_store import (
    CardGenerationChunkResult,
    clear_failed_chunk_results,
    list_chunk_results,
    publish_chunk_success,
    record_chunk_failure,
)
from .card_generation_run_store import (
    cancel_running_run,
    claim_run_attempt,
    create_run,
    fail_running_run,
    get_run,
    list_runs_for_job,
    mark_pending_run_failed,
    reconcile_run_from_chunk_results,
    recover_active_runs,
)
from .job import utc_now
from .knowledge_card import KnowledgeCard, KnowledgeCardCreate
from .review_item import ReviewItem, ReviewItemCreate
from .knowledge_card_store import list_cards_for_job
from .transcript_chunk import TranscriptChunk
from .transcript_chunk_store import list_chunks_for_job


class AutoCardGenerationServiceError(Exception):
    pass


class CardGenerationRunNotFoundError(AutoCardGenerationServiceError):
    pass


class InvalidAutoCardGenerationRequestError(AutoCardGenerationServiceError):
    pass


class AutoCardGenerationCancellationRequested(Exception):
    pass


def start_auto_card_generation(
    job_id: str,
    request: AutoCardGenerationRequest | None = None,
) -> CardGenerationRun:
    job = job_service.get_video_job(job_id)
    job_service.get_job_transcript(job.id)
    generation_request = request or AutoCardGenerationRequest()
    now = utc_now()
    run = CardGenerationRun(
        id=uuid4().hex,
        job_id=job.id,
        status="pending",
        model=_clean_optional_text(generation_request.model),
        card_count_per_chunk=generation_request.card_count_per_chunk,
        request=generation_request,
        created_at=now,
        updated_at=now,
    )

    create_run(run)

    return run


def get_card_generation_run(run_id: str) -> CardGenerationRun:
    run = get_run(run_id)

    if run is None:
        raise CardGenerationRunNotFoundError(
            "Card generation run not found."
        )

    return run


def list_job_card_generation_runs(job_id: str) -> list[CardGenerationRun]:
    job = job_service.get_video_job(job_id)

    return list_runs_for_job(job.id)


def recover_interrupted_card_generation_runs() -> int:
    return len(
        recover_active_runs(
            error_message=(
                "The app stopped before card generation finished. Retry it."
            )
        )
    )


def mark_card_generation_enqueue_failed(run_id: str) -> None:
    mark_pending_run_failed(
        run_id,
        error_message=(
            "Card generation could not be queued. Retry the operation."
        ),
    )


def run_auto_card_generation(
    run_id: str,
    llm_client_factory: Callable[[], card_service.CardLLMClient],
    *,
    checkpoint: Callable[[], None] | None = None,
    progress: Callable[[CardGenerationRun], None] | None = None,
) -> None:
    try:
        run = get_card_generation_run(run_id)
    except CardGenerationRunNotFoundError:
        return

    _checkpoint(checkpoint)
    claimed_run = claim_run_attempt(run.id)
    if claimed_run is None:
        return
    run = claimed_run
    selected_chunks: list[TranscriptChunk] | None = None

    try:
        _progress(progress, run)
        previous_results = {
            result.chunk_id: result
            for result in list_chunk_results(run.id)
        }
        chunks = _prepare_chunks(
            run,
            resume_from_results=bool(previous_results),
        )
        selected_chunks = _limit_chunks(chunks, run.request.max_chunks)
        _validate_resume_results(
            selected_chunks,
            previous_results=previous_results,
        )
        clear_failed_chunk_results(run.id)
        reconciled = reconcile_run_from_chunk_results(
            run.id,
            _chunk_keys(selected_chunks),
            phase="running",
        )
        if reconciled is None:
            return
        run = reconciled
        _progress(progress, run)

        if not selected_chunks:
            reconcile_run_from_chunk_results(
                run.id,
                [],
                phase="final",
            )
            return

        remaining_chunks = [
            chunk
            for chunk in selected_chunks
            if (
                previous_results.get(chunk.id) is None
                or previous_results[chunk.id].status != "succeeded"
            )
        ]
        if not remaining_chunks:
            reconcile_run_from_chunk_results(
                run.id,
                _chunk_keys(selected_chunks),
                phase="final",
            )
            return

        llm_client = llm_client_factory()

        for chunk in remaining_chunks:
            _checkpoint(checkpoint)
            _process_chunk(
                run,
                chunk,
                llm_client=llm_client,
            )
            reconciled = reconcile_run_from_chunk_results(
                run.id,
                _chunk_keys(selected_chunks),
                phase="running",
            )
            if reconciled is None:
                return
            run = reconciled
            _progress(progress, run)

        _checkpoint(checkpoint)
        reconcile_run_from_chunk_results(
            run.id,
            _chunk_keys(selected_chunks),
            phase="final",
        )
        # Completion is the publication boundary for this domain workflow.
        # The reliable-task handler must be allowed to return after it commits
        # even if a cancellation request arrives concurrently; invoking the
        # cancel-aware progress callback here could rewrite this completed run
        # as canceled.
    except AutoCardGenerationCancellationRequested:
        if selected_chunks is None:
            cancel_running_run(run.id)
        else:
            reconciled = reconcile_run_from_chunk_results(
                run.id,
                _chunk_keys(selected_chunks),
                phase="canceled",
            )
            if reconciled is not None and reconciled.status == "completed":
                return
        raise
    except Exception as exc:
        if selected_chunks is None:
            fail_running_run(run.id, error_message=str(exc))
        else:
            reconcile_run_from_chunk_results(
                run.id,
                _chunk_keys(selected_chunks),
                phase="final",
                failure_message=str(exc),
            )


def _validate_resume_results(
    chunks: list[TranscriptChunk],
    *,
    previous_results: dict[str, CardGenerationChunkResult],
) -> None:
    selected_ids = {chunk.id for chunk in chunks}
    published = [
        result
        for result in previous_results.values()
        if result.status == "succeeded"
    ]
    missing = [
        result.chunk_id
        for result in published
        if result.chunk_id not in selected_ids
    ]
    if missing:
        raise InvalidAutoCardGenerationRequestError(
            "Transcript chunks changed after cards were published; "
            "start a new generation run."
        )


def _chunk_keys(chunks: list[TranscriptChunk]) -> list[tuple[str, int]]:
    return [
        (chunk.id, chunk.chunk_index)
        for chunk in chunks
    ]


def _checkpoint(checkpoint: Callable[[], None] | None) -> None:
    if checkpoint is not None:
        checkpoint()


def _progress(
    progress: Callable[[CardGenerationRun], None] | None,
    run: CardGenerationRun,
) -> None:
    if progress is not None:
        progress(run)


def _prepare_chunks(
    run: CardGenerationRun,
    *,
    resume_from_results: bool,
) -> list[TranscriptChunk]:
    chunks = list_chunks_for_job(run.job_id)

    if (
        (run.request.regenerate_chunks and not resume_from_results)
        or not chunks
    ):
        if resume_from_results and not chunks:
            raise InvalidAutoCardGenerationRequestError(
                "Transcript chunks are missing for a partially published run."
            )
        chunks = transcript_chunk_service.generate_job_chunks(
            run.job_id,
            run.request.chunking,
        )

    return chunks


def _limit_chunks(
    chunks: list[TranscriptChunk],
    max_chunks: int | None,
) -> list[TranscriptChunk]:
    if max_chunks is None:
        return chunks

    return chunks[:max_chunks]


def _process_chunk(
    run: CardGenerationRun,
    chunk: TranscriptChunk,
    *,
    llm_client: card_service.CardLLMClient,
) -> None:
    try:
        draft = card_service.draft_knowledge_cards(
            card_service.CardDraftRequest(
                job_id=chunk.job_id,
                start_seconds=chunk.start_seconds,
                end_seconds=chunk.end_seconds,
                card_count=run.request.card_count_per_chunk,
                focus=run.request.focus,
                model=run.request.model,
            ),
            llm_client=llm_client,
        )

        cards, review_items = _build_new_cards_from_draft(
            run,
            chunk,
            draft,
        )
        publish_chunk_success(
            run.id,
            chunk,
            cards=cards,
            review_items=review_items,
            now=utc_now(),
        )
    except Exception as exc:
        record_chunk_failure(
            run.id,
            chunk,
            error_message=str(exc),
            now=utc_now(),
        )


def _build_new_cards_from_draft(
    run: CardGenerationRun,
    chunk: TranscriptChunk,
    draft: card_service.CardDraftResponse,
) -> tuple[list[KnowledgeCard], list[ReviewItem]]:
    existing_signatures = {
        _card_signature(
            title=card.title,
            source_start_seconds=card.source_start_seconds,
            source_end_seconds=card.source_end_seconds,
        )
        for card in list_cards_for_job(run.job_id)
    }
    cards: list[KnowledgeCard] = []
    review_items: list[ReviewItem] = []

    for ordinal, card in enumerate(draft.cards):
        signature = _card_signature(
            title=card.title,
            source_start_seconds=card.source_start_seconds,
            source_end_seconds=card.source_end_seconds,
        )

        if signature in existing_signatures:
            continue

        card_id = _operation_id(
            "card",
            run.id,
            chunk.id,
            str(ordinal),
        )
        request = KnowledgeCardCreate(
            title=card.title,
            summary=card.summary,
            key_points=card.key_points,
            claims=card.claims,
            unsupported_terms=card.unsupported_terms,
            tags=[],
            content_status="draft",
            source_start_seconds=card.source_start_seconds,
            source_end_seconds=card.source_end_seconds,
            provider=draft.provider,
            model=draft.model,
        )
        saved_card = knowledge_card_service.build_job_card(
            run.job_id,
            request,
            card_id=card_id,
        )
        review_request = ReviewItemCreate(
            item_type="basic",
            prompt=card.question,
            expected_answer=card.answer,
            source_claim_ids=[claim.id for claim in card.claims],
            source="generated",
        )
        review_items.append(
            ReviewItem(
                id=_operation_id("review", card_id),
                card_id=card_id,
                item_type=review_request.item_type,
                prompt=review_request.prompt.strip(),
                expected_answer=review_request.expected_answer.strip(),
                source_claim_ids=[
                    value.strip()
                    for value in review_request.source_claim_ids
                    if value.strip()
                ],
                source=review_request.source,
                status=review_request.status,
                created_at=saved_card.created_at,
                updated_at=saved_card.updated_at,
            )
        )
        cards.append(saved_card)
        existing_signatures.add(signature)

    return cards, review_items


def _operation_id(*parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return sha256(payload).hexdigest()[:32]


def _card_signature(
    *,
    title: str,
    source_start_seconds: float,
    source_end_seconds: float,
) -> tuple[str, float, float]:
    return (
        " ".join(title.lower().split()),
        round(source_start_seconds, 2),
        round(source_end_seconds, 2),
    )


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None

    stripped = value.strip()

    return stripped or None
