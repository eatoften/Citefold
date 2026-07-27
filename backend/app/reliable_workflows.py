from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from . import auto_card_generation_service
from . import chat_service
from . import course_service
from . import course_source_service
from . import job_service
from . import learning_document_service
from . import source_asset_service
from . import source_index_service
from .card_generation_run import AutoCardGenerationRequest
from .chat import ChatMessageCreate
from .course_source import SourceIndexRequest
from .job import VideoJobStatus
from .learning_document import LearningDocumentGenerateRequest
from .reliable_task import TaskProgress
from .reliable_task_manager import (
    ReliableTaskCancellationRequested,
    ReliableTaskContext,
    ReliableTaskExecutionError,
    ReliableTaskManager,
)


VIDEO_PROCESSING_TASK = "video_processing"
SOURCE_INDEX_TASK = "source_index"
SOURCE_IMPORT_TASK = "source_import"
AUTO_CARD_GENERATION_TASK = "auto_card_generation"
CHAT_GENERATION_TASK = "chat_generation"
LEARNING_DOCUMENT_GENERATION_TASK = "learning_document_generation"


def register_reliable_workflows(
    manager: ReliableTaskManager,
    *,
    video_pipeline_factory,
    llm_client_factory,
    artifact_root: Path,
) -> None:
    manager.register(
        VIDEO_PROCESSING_TASK,
        _video_handler(
            video_pipeline_factory=video_pipeline_factory,
            artifact_root=artifact_root,
        ),
        replace=True,
    )
    manager.register(
        SOURCE_INDEX_TASK,
        _source_index_handler,
        replace=True,
    )
    manager.register(
        SOURCE_IMPORT_TASK,
        _source_import_handler,
        replace=True,
    )
    manager.register(
        AUTO_CARD_GENERATION_TASK,
        _auto_card_handler(llm_client_factory),
        replace=True,
    )
    manager.register(
        CHAT_GENERATION_TASK,
        _chat_handler(llm_client_factory),
        replace=True,
    )
    manager.register(
        LEARNING_DOCUMENT_GENERATION_TASK,
        _learning_document_handler(llm_client_factory),
        replace=True,
    )


def _video_handler(
    *,
    video_pipeline_factory,
    artifact_root: Path,
):
    def handle(context: ReliableTaskContext):
        job_id = _required_text(context, "job_id")
        try:
            job = job_service.get_video_job(job_id)
            if job.status == VideoJobStatus.uploaded:
                job = job_service.start_job(job.id)
            elif job.status in {
                VideoJobStatus.failed,
                VideoJobStatus.canceled,
            }:
                job = job_service.retry_job(job.id)
            elif job.status == VideoJobStatus.completed:
                return {"job": job.model_dump(mode="json")}

            stage_order = {
                VideoJobStatus.probing: (1, "Inspecting video"),
                VideoJobStatus.extracting_audio: (2, "Extracting audio"),
                VideoJobStatus.transcribing: (3, "Transcribing lecture"),
                VideoJobStatus.completed: (4, "Video ready"),
            }

            def on_progress(updated_job) -> None:
                current, message = stage_order.get(
                    updated_job.status,
                    (0, "Preparing video"),
                )
                try:
                    context.report_progress(
                        current=current,
                        total=4,
                        stage=updated_job.status.value,
                        message=message,
                    )
                except ReliableTaskCancellationRequested as exc:
                    raise job_service.JobPipelineCancellationRequested() from exc

            context.report_progress(
                TaskProgress(
                    current=0,
                    total=4,
                    stage="starting",
                    message="Preparing the video pipeline",
                )
            )
            job_service.run_job_pipeline(
                job.id,
                video_pipeline_factory,
                artifact_root,
                on_progress=on_progress,
            )
            completed = job_service.get_video_job(job.id)
            if completed.status == VideoJobStatus.canceled:
                raise ReliableTaskCancellationRequested(
                    "Video processing was canceled."
                )
            if completed.status != VideoJobStatus.completed:
                raise ReliableTaskExecutionError(
                    completed.error_message
                    or "Video processing did not complete.",
                    error_code="video_processing_failed",
                )
            return {"job": completed.model_dump(mode="json")}
        except job_service.JobPipelineCancellationRequested as exc:
            raise ReliableTaskCancellationRequested(
                "Video processing was canceled."
            ) from exc
        except ReliableTaskCancellationRequested:
            raise
        except job_service.JobServiceError as exc:
            raise ReliableTaskExecutionError(
                str(exc),
                error_code="video_job_invalid",
            ) from exc

    return handle


def _source_index_handler(context: ReliableTaskContext):
    course_id = _required_text(context, "course_id")
    raw_request = context.payload.get("request") or {}
    try:
        request = SourceIndexRequest.model_validate(raw_request)
    except ValidationError as exc:
        raise ReliableTaskExecutionError(
            "Source indexing settings are invalid.",
            error_code="invalid_source_index_request",
            retryable=False,
        ) from exc
    context.report_progress(
        current=0,
        total=3,
        stage="preparing",
        message="Preparing source excerpts",
    )

    def checkpoint() -> None:
        try:
            context.checkpoint()
        except ReliableTaskCancellationRequested as exc:
            raise (
                source_index_service.SourceIndexCancellationRequested()
            ) from exc

    def progress(
        current: float,
        total: float | None,
        stage: str,
        message: str,
    ) -> None:
        try:
            context.report_progress(
                current=current,
                total=total,
                stage=stage,
                message=message,
            )
        except ReliableTaskCancellationRequested as exc:
            raise (
                source_index_service.SourceIndexCancellationRequested()
            ) from exc

    try:
        result = source_index_service.index_course_sources(
            course_id,
            request,
            checkpoint=checkpoint,
            progress=progress,
        )
    except source_index_service.SourceIndexCancellationRequested as exc:
        raise ReliableTaskCancellationRequested(
            "Source indexing was canceled."
        ) from exc
    except ReliableTaskCancellationRequested:
        raise
    except source_index_service.SourceIndexServiceError as exc:
        error_code = (
            "source_index_conflict"
            if isinstance(
                exc,
                source_index_service.SourceIndexConflictError,
            )
            else "source_index_failed"
        )
        raise ReliableTaskExecutionError(
            str(exc),
            error_code=error_code,
        ) from exc
    return {"index": result.model_dump(mode="json")}


def _source_import_handler(context: ReliableTaskContext):
    asset_id = _required_text(context, "asset_id")

    def checkpoint() -> None:
        try:
            context.checkpoint()
        except ReliableTaskCancellationRequested as exc:
            raise (
                source_asset_service
                .SourceAssetProcessingCancellationRequested()
            ) from exc

    context.report_progress(
        current=0,
        total=2,
        stage="parsing",
        message="Extracting text and source locators",
    )
    try:
        result = source_asset_service.process_source_asset(
            asset_id,
            checkpoint=checkpoint,
        )
    except (
        source_asset_service.SourceAssetProcessingCancellationRequested
    ) as exc:
        raise ReliableTaskCancellationRequested(
            "Source parsing was canceled."
        ) from exc
    except source_asset_service.SourceAssetServiceError as exc:
        if isinstance(
            exc,
            source_asset_service.SourceAssetNotFoundError,
        ):
            error_code = "source_asset_not_found"
            retryable = False
        elif isinstance(
            exc,
            source_asset_service.InvalidSourceAssetError,
        ):
            error_code = "invalid_source_asset"
            retryable = False
        elif isinstance(
            exc,
            source_asset_service.SourceAssetExtractionError,
        ):
            error_code = "source_asset_extraction_failed"
            retryable = True
        else:
            error_code = "source_import_failed"
            retryable = True
        raise ReliableTaskExecutionError(
            str(exc),
            error_code=error_code,
            retryable=retryable,
        ) from exc
    return {"import": result.model_dump(mode="json")}


def _auto_card_handler(llm_client_factory):
    def handle(context: ReliableTaskContext):
        run_id = _required_text(context, "run_id")
        context.report_progress(
            current=0,
            total=None,
            stage="preparing",
            message="Preparing transcript chunks",
        )

        def checkpoint() -> None:
            try:
                context.checkpoint()
            except ReliableTaskCancellationRequested as exc:
                raise (
                    auto_card_generation_service
                    .AutoCardGenerationCancellationRequested()
                ) from exc

        def on_progress(run) -> None:
            try:
                context.report_progress(
                    current=run.completed_chunks,
                    total=run.total_chunks or None,
                    stage=run.status,
                    message=(
                        f"Created {run.cards_created} cards from "
                        f"{run.completed_chunks} chunks"
                    ),
                )
            except ReliableTaskCancellationRequested as exc:
                raise (
                    auto_card_generation_service
                    .AutoCardGenerationCancellationRequested()
                ) from exc

        try:
            auto_card_generation_service.run_auto_card_generation(
                run_id,
                llm_client_factory,
                checkpoint=checkpoint,
                progress=on_progress,
            )
        except (
            auto_card_generation_service
            .AutoCardGenerationCancellationRequested
        ) as exc:
            raise ReliableTaskCancellationRequested(
                "Card generation was canceled."
            ) from exc
        run = auto_card_generation_service.get_card_generation_run(run_id)
        if run.status == "canceled":
            raise ReliableTaskCancellationRequested(
                "Card generation was canceled."
            )
        if run.status != "completed":
            raise ReliableTaskExecutionError(
                run.error_message or "Card generation failed.",
                error_code="card_generation_failed",
            )
        return {"run": run.model_dump(mode="json")}

    return handle


def _chat_handler(llm_client_factory):
    def handle(context: ReliableTaskContext):
        conversation_id = _required_text(context, "conversation_id")
        raw_request = context.payload.get("request") or {}
        try:
            request = ChatMessageCreate.model_validate(raw_request)
        except ValidationError as exc:
            raise ReliableTaskExecutionError(
                "Chat request is invalid.",
                error_code="invalid_chat_request",
                retryable=False,
            ) from exc
        context.report_progress(
            current=0,
            total=2,
            stage="grounding",
            message="Retrieving and validating source evidence",
        )

        def checkpoint() -> None:
            try:
                context.checkpoint()
            except ReliableTaskCancellationRequested as exc:
                raise (
                    chat_service.ChatGenerationCancellationRequested()
                ) from exc

        try:
            turn = chat_service.send_chat_message(
                conversation_id,
                request,
                llm_client=llm_client_factory(),
                checkpoint=checkpoint,
                retry_failed=context.task.attempt > 1,
            )
        except chat_service.ChatGenerationCancellationRequested as exc:
            raise ReliableTaskCancellationRequested(
                "Chat generation was canceled."
            ) from exc
        except course_source_service.CourseSourceServiceError as exc:
            if isinstance(
                exc,
                course_source_service.CourseSourceNotFoundError,
            ):
                error_code = "chat_source_not_found"
                retryable = True
            elif isinstance(
                exc,
                course_source_service.CourseSourceScopeError,
            ):
                error_code = "chat_source_scope"
                retryable = False
            elif isinstance(
                exc,
                course_source_service.CourseSourceUnavailableError,
            ):
                error_code = "chat_source_unavailable"
                retryable = True
            else:
                error_code = "chat_source_failed"
                retryable = True
            raise ReliableTaskExecutionError(
                str(exc),
                error_code=error_code,
                retryable=retryable,
            ) from exc
        except course_service.CourseServiceError as exc:
            raise ReliableTaskExecutionError(
                str(exc),
                error_code="chat_course_not_found",
                retryable=False,
            ) from exc
        except chat_service.ChatServiceError as exc:
            if isinstance(
                exc,
                chat_service.ChatConversationNotFoundError,
            ):
                error_code = "chat_conversation_not_found"
                retryable = False
            elif isinstance(
                exc,
                (
                    chat_service.ChatTurnConflictError,
                    chat_service.ChatSourceChangedError,
                ),
            ):
                error_code = "chat_conflict"
                retryable = True
            elif isinstance(exc, chat_service.ChatRetrievalError):
                error_code = "chat_retrieval_failed"
                retryable = True
            elif isinstance(
                exc,
                chat_service.ChatGenerationTimeoutError,
            ):
                error_code = "chat_generation_timeout"
                retryable = True
            elif isinstance(exc, chat_service.ChatGenerationError):
                error_code = "chat_generation_failed"
                retryable = True
            else:
                error_code = "chat_failed"
                retryable = True
            raise ReliableTaskExecutionError(
                str(exc),
                error_code=error_code,
                retryable=retryable,
            ) from exc
        return {"turn": turn.model_dump(mode="json")}

    return handle


def _learning_document_handler(llm_client_factory):
    def handle(context: ReliableTaskContext):
        document_id = _required_text(context, "document_id")
        raw_request = context.payload.get("request") or {}
        try:
            request = LearningDocumentGenerateRequest.model_validate(
                raw_request
            )
        except ValidationError as exc:
            raise ReliableTaskExecutionError(
                "Study generation settings are invalid.",
                error_code="invalid_learning_document_request",
                retryable=False,
            ) from exc
        context.report_progress(
            current=0,
            total=2,
            stage="selecting_evidence",
            message="Selecting cards and source excerpts",
        )
        try:
            result = learning_document_service.generate_learning_document(
                document_id,
                request,
                llm_client=llm_client_factory(),
                checkpoint=context.checkpoint,
                operation_id=context.task.id,
            )
        except learning_document_service.LearningDocumentServiceError as exc:
            if isinstance(
                exc,
                (
                    learning_document_service.LearningDocumentNotFoundError,
                    learning_document_service.LearningDocumentCardNotFoundError,
                ),
            ):
                error_code = "learning_document_not_found"
                retryable = False
            elif isinstance(
                exc,
                learning_document_service.InvalidLearningDocumentError,
            ):
                error_code = "invalid_learning_document"
                retryable = False
            elif isinstance(
                exc,
                learning_document_service.LearningDocumentGenerationError,
            ):
                error_code = "learning_document_generation_failed"
                retryable = True
            else:
                error_code = "learning_document_failed"
                retryable = True
            raise ReliableTaskExecutionError(
                str(exc),
                error_code=error_code,
                retryable=retryable,
            ) from exc
        return {"generation": result.model_dump(mode="json")}

    return handle


def _required_text(
    context: ReliableTaskContext,
    key: str,
) -> str:
    value = context.payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ReliableTaskExecutionError(
        "Task payload is incomplete.",
        error_code="invalid_task_payload",
        retryable=False,
    )
