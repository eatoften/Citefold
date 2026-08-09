from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import cache
from ipaddress import ip_address
import os
from pathlib import Path
import secrets
from typing import Annotated, Literal, TypeVar
from uuid import uuid4

from fastapi import (
    FastAPI,
    Form,
    HTTPException,
    Path as ApiPath,
    Query,
    Request,
    UploadFile,
)
from fastapi import status
from fastapi import Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from . import auto_card_generation_service
from . import card_embedding_service
from . import card_relation_service
from . import card_service
from . import chat_service
from . import citation_target_service
from . import concept_graph_publication_service
from . import course_source_service
from . import course_service
from . import export_service
from . import job_service
from . import knowledge_card_service
from . import knowledge_card_note_service
from . import learning_document_service
from . import notebook_note_service
from . import rag_service
from . import review_item_service
from . import review_service
from . import runtime_service
from . import reliable_task_store
from . import source_asset_service
from . import source_index_service
from . import source_search_service
from . import transcript_chunk_service
from . import topic_service
from . import topic_suggestion_service
from . import trash_service
from . import trash_store
from . import workspace_draft_service
from . import workspace_backup
from .course import Course, CourseCreate, CourseUpdate
from .course_source import (
    CourseSource,
    CourseSourceChunk,
    CourseSourceUpdate,
    SourceIndexRequest,
    SourceIndexResult,
    SourceSearchRequest,
    SourceSearchResponse,
)
from .card_generation_run import AutoCardGenerationRequest, CardGenerationRun
from .chat import (
    ChatConversation,
    ChatConversationCreate,
    ChatConversationDetail,
    ChatConversationUpdate,
    ChatMessageCreate,
    ChatTurnResponse,
)
from .citation_target import CitationTargetResponse
from .citation_content_response import build_citation_content_response
from .citation_target_store import CitationSnapshotRecord
from .concept_graph_api import router as concept_graph_router
from .concept_graph_publication_api import (
    raise_concept_graph_publication_http_error,
    router as concept_graph_publication_router,
)
from .concept_graph_path_api import router as concept_graph_path_router
from .card_embedding import CardEmbeddingBatchResult, CardEmbeddingStatus
from .card_relation import (
    CardRelatedCardsResponse,
    CardRelation,
    CardRelationClassificationResult,
    CardRelationClassifyRequest,
    CardRelationCreate,
    CardRelationRecomputeRequest,
    CardRelationRecomputeResult,
    CardRelationUpdate,
    CourseCardRelationsGraph,
)
from .db import get_db_path, init_db
from .job import VideoJob, VideoJobStatus
from .job_service import TranscriptContext
from .knowledge_card import (
    KnowledgeCard,
    KnowledgeCardCreate,
    KnowledgeCardDetail,
    KnowledgeCardIndexItem,
    KnowledgeCardUpdate,
)
from .review_item import ReviewItem, ReviewItemCreate, ReviewItemUpdate
from .review import ReviewQueue, ReviewRatingRequest, ReviewRatingResult
from .knowledge_card_note import (
    KnowledgeCardNote,
    KnowledgeCardNoteCreate,
    KnowledgeCardNoteUpdate,
)
from .learning_document import (
    LearningDocument,
    LearningDocumentCardLinkCreate,
    LearningDocumentCreate,
    LearningDocumentDetail,
    LearningDocumentGenerateRequest,
    LearningDocumentGenerationResult,
    LearningDocumentRestoreRequest,
    LearningDocumentUpdate,
)
from .notebook_note import (
    NotebookNote,
    NotebookNoteChatCaptureRequest,
    NotebookNoteCreate,
    NotebookNotePromotionRequest,
    NotebookNotePromotionResult,
    NotebookNoteSummary,
    NotebookNoteUpdate,
)
from .llm_client import LLMModelList, LLMStatus, LocalLLMClient
from .rag import RagRetrieveRequest, RagRetrieveResponse
from .runtime_status import RuntimeStatus
from .reliable_task import (
    ACTIVE_TASK_STATUSES,
    TERMINAL_TASK_STATUSES,
    ReliableTask,
    ReliableTaskStatus,
    TaskEvent,
)
from .reliable_task_manager import ReliableTaskManager
from .reliable_task_store import (
    ReliableTaskActiveConflictError,
    ReliableTaskIdempotencyConflictError,
    ReliableTaskNotFoundError,
    ReliableTaskReservation,
    ReliableTaskRetryError,
    ReliableTaskStateConflictError,
)
from .reliable_workflows import (
    AUTO_CARD_GENERATION_TASK,
    CHAT_GENERATION_TASK,
    LEARNING_DOCUMENT_GENERATION_TASK,
    SOURCE_INDEX_TASK,
    SOURCE_IMPORT_TASK,
    VIDEO_PROCESSING_TASK,
    register_reliable_workflows,
)
from .source_asset import SourceAssetDetail, SourceAssetImportResult, SourceUnit
from .settings import get_app_path_settings
from .transcription import FasterWhisperTranscriber, TranscriptionResult
from .transcript_chunk import TranscriptChunk, TranscriptChunkGenerationRequest
from .topic import (
    CourseMap,
    SetPrimaryTopicRequest,
    Topic,
    TopicCardMembership,
    TopicCreate,
    TopicRelation,
    TopicRelationCreate,
    TopicMergeRequest,
    TopicSplitRequest,
    TopicSuggestionRequest,
    TopicSuggestionResult,
    TopicUpdate,
)
from .trash import TrashItem
from .video_pipeline import VideoPipeline
from .workspace_draft import WorkspaceDraft, WorkspaceDraftPut
from .workspace_lifecycle import workspace_lifecycle_lock


APP_PATHS = get_app_path_settings()
DATA_DIR = APP_PATHS.data_dir
UPLOAD_DIR = APP_PATHS.upload_dir
SOURCE_DIR = APP_PATHS.source_dir

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
SOURCE_DIR.mkdir(parents=True, exist_ok=True)


ModelT = TypeVar("ModelT", bound=BaseModel)
SYNC_TASK_TIMEOUT_SECONDS = 3600.0


class VideoUploadResponse(BaseModel):
    id: str
    course_id: str
    filename: str
    stored_name: str
    size_bytes: int
    status: VideoJobStatus


class SourceAssetTaskResponse(BaseModel):
    asset: SourceAssetDetail
    task: ReliableTask


class WorkspaceBackupRecord(BaseModel):
    id: str
    valid: bool
    archive_size_bytes: int
    modified_at: str
    archive_sha256: str | None = None
    created_at: str | None = None
    app_version: str | None = None
    backup_kind: str | None = None
    schema_version: int | None = None
    entry_count: int | None = None
    managed_file_count: int | None = None
    total_uncompressed_bytes: int | None = None
    error: str | None = None


class PendingWorkspaceRestoreResponse(BaseModel):
    restore_id: str
    backup_id: str
    backup_sha256: str
    queued_at: str
    schema_version: int
    phase: str
    workspace_generation: int


class WorkspaceRestoreResultResponse(BaseModel):
    restore_id: str
    backup_id: str
    status: str
    applied_at: str | None = None
    pre_restore_backup_id: str | None = None
    error: str | None = None
    workspace_generation: int


class WorkspaceRestoreStatusResponse(BaseModel):
    workspace_generation: int
    pending: PendingWorkspaceRestoreResponse | None = None
    last_result: WorkspaceRestoreResultResponse | None = None


def _runtime_workspace_data_dir() -> Path:
    """Keep test/custom database workspaces isolated from the app data root."""

    if DATA_DIR.resolve() != APP_PATHS.data_dir.resolve():
        return DATA_DIR.resolve()
    database_path = get_db_path().resolve()
    configured_path = APP_PATHS.db_path.resolve()
    if database_path == configured_path:
        return DATA_DIR.resolve()
    if database_path.parent.name == "data":
        return database_path.parent.parent
    return database_path.parent


def _backup_record(
    item: (
        workspace_backup.WorkspaceBackupSummary
        | workspace_backup.ValidatedWorkspaceBackup
    ),
) -> WorkspaceBackupRecord:
    if isinstance(item, workspace_backup.ValidatedWorkspaceBackup):
        modified_at = (
            item.path.stat().st_mtime
            if item.path.exists()
            else 0.0
        )
        return WorkspaceBackupRecord(
            id=item.path.name,
            valid=True,
            archive_size_bytes=item.archive_size_bytes,
            modified_at=datetime.fromtimestamp(
                modified_at,
                tz=timezone.utc,
            ).isoformat(),
            archive_sha256=item.archive_sha256,
            created_at=item.created_at,
            app_version=item.app_version,
            backup_kind=item.backup_kind,
            schema_version=item.schema_version,
            entry_count=item.entry_count,
            managed_file_count=item.managed_file_count,
            total_uncompressed_bytes=item.total_uncompressed_bytes,
        )
    return WorkspaceBackupRecord(
        id=item.path.name,
        valid=item.valid,
        archive_size_bytes=item.archive_size_bytes,
        modified_at=item.modified_at,
        archive_sha256=item.archive_sha256,
        created_at=item.created_at,
        app_version=item.app_version,
        backup_kind=item.backup_kind,
        schema_version=item.schema_version,
        entry_count=item.entry_count,
        error=item.error,
    )


def _workspace_backup_path(backup_id: str) -> Path:
    if (
        not backup_id
        or Path(backup_id).name != backup_id
        or not backup_id.endswith(workspace_backup.BACKUP_EXTENSION)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid workspace backup id.",
        )
    backup_dir = _runtime_workspace_data_dir() / "backups"
    candidate = (backup_dir / backup_id).resolve()
    if candidate.parent != backup_dir.resolve():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid workspace backup id.",
        )
    if not candidate.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace backup not found.",
        )
    return candidate


def _restore_response(
    item: workspace_backup.PendingWorkspaceRestore,
) -> PendingWorkspaceRestoreResponse:
    return PendingWorkspaceRestoreResponse(
        restore_id=item.restore_id,
        backup_id=item.backup_id,
        backup_sha256=item.backup_sha256,
        queued_at=item.queued_at,
        schema_version=item.schema_version,
        phase=item.phase,
        workspace_generation=item.workspace_generation,
    )


def _restore_result_payload(
    result: workspace_backup.WorkspaceRestoreResult | None,
) -> WorkspaceRestoreResultResponse | None:
    if result is None:
        return None
    return WorkspaceRestoreResultResponse(
        restore_id=result.restore_id,
        backup_id=result.backup_id,
        status=result.status,
        applied_at=result.applied_at,
        pre_restore_backup_id=(
            result.pre_restore_backup_path.name
            if result.pre_restore_backup_path is not None
            else None
        ),
        error=result.error,
        workspace_generation=result.workspace_generation,
    )


def raise_workspace_backup_http_error(exc: Exception) -> None:
    if isinstance(exc, workspace_backup.BackupValidationError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    if isinstance(
        exc,
        (
            workspace_backup.RestoreQueueError,
            workspace_backup.WorkspaceBackupError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Workspace backup operation failed.",
    ) from exc


def raise_trash_http_error(
    exc: trash_service.TrashServiceError,
) -> None:
    if isinstance(exc, trash_service.TrashItemNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    if isinstance(exc, trash_service.TrashOperationError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(exc),
    ) from exc


def raise_http_error(exc: job_service.JobServiceError) -> None:
    if isinstance(exc, job_service.MissingFilenameError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if isinstance(
        exc,
        (
            job_service.UnsupportedVideoExtensionError,
            job_service.UnsupportedContentTypeError,
            job_service.InvalidVideoError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(exc),
        ) from exc

    if isinstance(
        exc,
        (
            job_service.JobNotFoundError,
            job_service.CourseNotFoundError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    if isinstance(
        exc,
        (
            job_service.InvalidJobStatusError,
            job_service.TranscriptNotReadyError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    if isinstance(exc, job_service.InvalidTranscriptContextError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected job service error.",
    ) from exc


def raise_workspace_draft_http_error(
    exc: workspace_draft_service.WorkspaceDraftServiceError,
) -> None:
    if isinstance(
        exc,
        workspace_draft_service.WorkspaceDraftNotFoundError,
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    if isinstance(
        exc,
        workspace_draft_service.WorkspaceDraftConflictError,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": str(exc),
                "current": (
                    exc.current.model_dump(mode="json")
                    if exc.current is not None
                    else None
                ),
            },
        ) from exc
    if isinstance(
        exc,
        workspace_draft_service.WorkspaceDraftTooLargeError,
    ):
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(exc),
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(exc),
    ) from exc


def raise_reliable_task_http_error(exc: Exception) -> None:
    if isinstance(exc, (ReliableTaskNotFoundError,)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found.",
        ) from exc
    if isinstance(
        exc,
        (
            ReliableTaskActiveConflictError,
            ReliableTaskIdempotencyConflictError,
            ReliableTaskRetryError,
            ReliableTaskStateConflictError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Task operation failed.",
    ) from exc


def wait_for_legacy_task_result(
    task: ReliableTask,
    *,
    result_key: str,
    response_model: type[ModelT],
) -> ModelT:
    """Keep legacy blocking responses while using the durable task path."""

    try:
        completed = get_reliable_task_manager().wait_for_task(
            task.id,
            TERMINAL_TASK_STATUSES,
            timeout_seconds=SYNC_TASK_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=(
                "The operation is still running in Activity. "
                "You can safely leave this page and return later."
            ),
            headers={"X-Task-ID": task.id},
        ) from exc

    if completed.status == ReliableTaskStatus.canceled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The operation was canceled.",
            headers={"X-Task-ID": completed.id},
        )
    if completed.status == ReliableTaskStatus.failed:
        status_code, detail = _legacy_task_failure_response(completed)
        raise HTTPException(
            status_code=status_code,
            detail=detail,
            headers={"X-Task-ID": completed.id},
        )

    payload = completed.result.get(result_key)
    try:
        return response_model.model_validate(payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The completed task returned an invalid result.",
            headers={"X-Task-ID": completed.id},
        ) from exc


def _legacy_task_failure_response(task: ReliableTask) -> tuple[int, str]:
    code = task.error_code or ""
    if code == "source_index_conflict":
        return (
            status.HTTP_409_CONFLICT,
            "Sources changed while indexing. Please retry.",
        )
    if code == "source_index_failed":
        return (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Source indexing failed. Check the local model settings and retry.",
        )

    status_codes = {
        "source_asset_not_found": status.HTTP_404_NOT_FOUND,
        "invalid_source_asset": status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        "source_asset_extraction_failed": (
            status.HTTP_422_UNPROCESSABLE_CONTENT
        ),
        "chat_conversation_not_found": status.HTTP_404_NOT_FOUND,
        "chat_course_not_found": status.HTTP_404_NOT_FOUND,
        "chat_source_not_found": status.HTTP_404_NOT_FOUND,
        "chat_source_scope": status.HTTP_400_BAD_REQUEST,
        "chat_source_unavailable": status.HTTP_409_CONFLICT,
        "chat_conflict": status.HTTP_409_CONFLICT,
        "chat_retrieval_failed": status.HTTP_503_SERVICE_UNAVAILABLE,
        "chat_generation_timeout": status.HTTP_504_GATEWAY_TIMEOUT,
        "chat_generation_failed": status.HTTP_502_BAD_GATEWAY,
        "learning_document_not_found": status.HTTP_404_NOT_FOUND,
        "invalid_learning_document": status.HTTP_400_BAD_REQUEST,
        "learning_document_generation_failed": status.HTTP_502_BAD_GATEWAY,
    }
    status_code = status_codes.get(
        code,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
    safe_defaults = {
        "source_import_failed": "Unexpected source asset service error.",
        "chat_failed": "Unexpected chat service error.",
        "chat_source_failed": "Unexpected source service error.",
        "learning_document_failed": (
            "Unexpected learning document service error."
        ),
    }
    detail = safe_defaults.get(
        code,
        task.error_message or "The background operation failed.",
    )
    return status_code, detail


def require_no_active_tasks(
    *,
    course_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    conflict_detail: str | None = None,
) -> None:
    active = reliable_task_store.list_tasks(
        course_id=course_id,
        resource_type=resource_type,
        resource_id=resource_id,
        statuses=ACTIVE_TASK_STATUSES,
        limit=1,
    )
    if not active:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=conflict_detail
        or (
            "This item has an active background task. Cancel it in "
            "Activity before moving the item to Trash."
        ),
        headers={"X-Task-ID": active[0].id},
    )


def reserve_source_import_task(
    asset: SourceAssetDetail,
    *,
    idempotency_key: str | None = None,
) -> ReliableTaskReservation:
    """Pair a staged source with a durable task or publish queue failure."""

    try:
        return get_reliable_task_manager().enqueue(
            kind=SOURCE_IMPORT_TASK,
            course_id=asset.course_id,
            resource_type="source_asset",
            resource_id=asset.id,
            payload={"asset_id": asset.id},
            idempotency_key=(
                idempotency_key or f"source-import:{asset.id}"
            ),
            active_key=f"source-import:{asset.id}",
        )
    except Exception:
        # ReliableTaskManager.enqueue only raises before a durable reservation
        # exists. Persist a visible domain failure so an uploaded source can
        # never remain indefinitely "pending" without a task.
        source_asset_service.mark_source_asset_enqueue_failed(asset.id)
        raise


def raise_card_http_error(exc: card_service.CardServiceError) -> None:
    if isinstance(exc, card_service.InvalidCardDraftRequestError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if isinstance(exc, card_service.CardGenerationTimeoutError):
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=str(exc),
        ) from exc

    if isinstance(exc, card_service.CardGenerationError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    if isinstance(exc, card_service.CardOutputParseError):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected card service error.",
    ) from exc


def raise_knowledge_card_http_error(
    exc: knowledge_card_service.KnowledgeCardServiceError,
) -> None:
    if isinstance(exc, knowledge_card_service.KnowledgeCardNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    if isinstance(exc, knowledge_card_service.InvalidKnowledgeCardError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected knowledge card service error.",
    ) from exc


def raise_knowledge_card_note_http_error(
    exc: knowledge_card_note_service.KnowledgeCardNoteServiceError,
) -> None:
    if isinstance(
        exc,
        (
            knowledge_card_note_service.KnowledgeCardNoteNotFoundError,
            knowledge_card_note_service.KnowledgeCardForNoteNotFoundError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    if isinstance(
        exc,
        knowledge_card_note_service.InvalidKnowledgeCardNoteError,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected knowledge card note service error.",
    ) from exc


def raise_notebook_note_http_error(
    exc: notebook_note_service.NotebookNoteServiceError,
) -> None:
    if isinstance(
        exc,
        notebook_note_service.NotebookNoteNotFoundError,
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    if isinstance(exc, notebook_note_service.NotebookNoteConflictError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": str(exc),
                "current": (
                    exc.current.model_dump(mode="json")
                    if exc.current is not None
                    else None
                ),
            },
        ) from exc
    if isinstance(
        exc,
        notebook_note_service.NotebookNoteCaptureConflictError,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    if isinstance(exc, notebook_note_service.InvalidNotebookNoteError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected notebook note service error.",
    ) from exc


def raise_source_asset_http_error(
    exc: source_asset_service.SourceAssetServiceError,
) -> None:
    if isinstance(exc, source_asset_service.SourceAssetNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    if isinstance(exc, source_asset_service.InvalidSourceAssetError):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(exc),
        ) from exc
    if isinstance(exc, source_asset_service.SourceAssetExtractionError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected source asset service error.",
    ) from exc


def raise_course_source_http_error(
    exc: course_source_service.CourseSourceServiceError,
) -> None:
    if isinstance(
        exc,
        course_source_service.CourseSourceNotFoundError,
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    if isinstance(exc, course_source_service.CourseSourceScopeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    if isinstance(
        exc,
        course_source_service.CourseSourceUnavailableError,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected source service error.",
    ) from exc


def raise_source_index_http_error(
    exc: source_index_service.SourceIndexServiceError,
) -> None:
    if isinstance(exc, source_index_service.SourceIndexConflictError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sources changed while indexing. Please retry.",
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            "Source indexing failed. Check the local model settings "
            "and retry."
        ),
    ) from exc


def raise_source_search_http_error(
    exc: source_search_service.SourceSearchServiceError,
) -> None:
    if isinstance(exc, source_search_service.SourceSearchConflictError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sources changed while searching. Please retry.",
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            "Source search failed. Check the local model settings "
            "and retry."
        ),
    ) from exc


def raise_learning_document_http_error(
    exc: learning_document_service.LearningDocumentServiceError,
) -> None:
    if isinstance(
        exc,
        (
            learning_document_service.LearningDocumentNotFoundError,
            learning_document_service.LearningDocumentCardNotFoundError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    if isinstance(exc, learning_document_service.InvalidLearningDocumentError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    if isinstance(exc, learning_document_service.LearningDocumentGenerationError):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected learning document service error.",
    ) from exc


def raise_review_item_http_error(
    exc: review_item_service.ReviewItemServiceError,
) -> None:
    if isinstance(
        exc,
        (
            review_item_service.ReviewItemNotFoundError,
            review_item_service.ReviewItemCardNotFoundError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    if isinstance(exc, review_item_service.InvalidReviewItemError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected review item service error.",
    ) from exc


def raise_review_http_error(exc: review_service.ReviewServiceError) -> None:
    if isinstance(exc, review_service.ReviewItemNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    if isinstance(exc, review_service.InvalidReviewRequestError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected review service error.",
    ) from exc


def raise_course_http_error(exc: course_service.CourseServiceError) -> None:
    if isinstance(exc, course_service.CourseNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    if isinstance(
        exc,
        (
            course_service.InvalidCourseError,
            course_service.DefaultCourseDeleteError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected course service error.",
    ) from exc


def raise_topic_http_error(exc: topic_service.TopicServiceError) -> None:
    if isinstance(
        exc,
        (topic_service.TopicNotFoundError, topic_service.TopicCardNotFoundError),
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    if isinstance(exc, topic_service.InvalidTopicError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected topic service error.",
    ) from exc


def raise_topic_suggestion_http_error(
    exc: topic_suggestion_service.TopicSuggestionError,
) -> None:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(exc),
    ) from exc


def raise_transcript_chunk_http_error(
    exc: transcript_chunk_service.TranscriptChunkServiceError,
) -> None:
    if isinstance(
        exc,
        transcript_chunk_service.InvalidTranscriptChunkConfigError,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if isinstance(
        exc,
        transcript_chunk_service.TranscriptChunkGenerationError,
    ):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected transcript chunk service error.",
    ) from exc


def raise_auto_generation_http_error(
    exc: auto_card_generation_service.AutoCardGenerationServiceError,
) -> None:
    if isinstance(
        exc,
        auto_card_generation_service.CardGenerationRunNotFoundError,
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    if isinstance(
        exc,
        auto_card_generation_service.InvalidAutoCardGenerationRequestError,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected auto card generation service error.",
    ) from exc


def raise_card_embedding_http_error(
    exc: card_embedding_service.CardEmbeddingServiceError,
) -> None:
    if isinstance(
        exc,
        card_embedding_service.CardEmbeddingCardNotFoundError,
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    if isinstance(
        exc,
        card_embedding_service.CardEmbeddingGenerationError,
    ):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected card embedding service error.",
    ) from exc


def raise_card_relation_http_error(
    exc: card_relation_service.CardRelationServiceError,
) -> None:
    if isinstance(
        exc,
        (
            card_relation_service.CardRelationNotFoundError,
            card_relation_service.CardRelationCardNotFoundError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    if isinstance(
        exc,
        card_relation_service.InvalidCardRelationRequestError,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if isinstance(
        exc,
        card_relation_service.CardRelationClassificationTimeoutError,
    ):
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=str(exc),
        ) from exc

    if isinstance(
        exc,
        card_relation_service.CardRelationClassificationError,
    ):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected card relation service error.",
    ) from exc


def raise_rag_http_error(exc: rag_service.RagServiceError) -> None:
    if isinstance(exc, rag_service.RagScopeMismatchError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if isinstance(exc, rag_service.RagRetrievalError):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected RAG service error.",
    ) from exc


def raise_chat_http_error(exc: chat_service.ChatServiceError) -> None:
    if isinstance(
        exc,
        chat_service.ChatConversationNotFoundError,
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    if isinstance(
        exc,
        (
            chat_service.ChatTurnConflictError,
            chat_service.ChatSourceChangedError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    if isinstance(exc, chat_service.ChatRetrievalError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    if isinstance(exc, chat_service.ChatGenerationTimeoutError):
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=str(exc),
        ) from exc

    if isinstance(exc, chat_service.ChatGenerationError):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected chat service error.",
    ) from exc


def raise_citation_target_http_error(
    exc: citation_target_service.CitationTargetServiceError,
) -> None:
    headers = {"Cache-Control": "private, no-store"}
    if isinstance(
        exc,
        citation_target_service.CitationTargetNotFoundError,
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Citation not found.",
            headers=headers,
        ) from exc
    if isinstance(
        exc,
        citation_target_service.CitationContentUnavailableError,
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_409_CONFLICT
                if exc.integrity_conflict
                else status.HTTP_410_GONE
            ),
            detail=str(exc),
            headers=headers,
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected citation target service error.",
        headers=headers,
    ) from exc


def require_loopback_client(request: Request) -> None:
    host = request.client.host if request.client is not None else ""
    try:
        address = ip_address(host)
        mapped = getattr(address, "ipv4_mapped", None)
        is_loopback = address.is_loopback or bool(
            mapped is not None and mapped.is_loopback
        )
    except ValueError:
        is_loopback = False
    if not is_loopback:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Citation source access is limited to this device.",
        )


def archive_response(archive: export_service.MarkdownArchive) -> Response:
    return Response(
        content=archive.content,
        media_type=archive.media_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{archive.filename}"'
            ),
        },
    )


def _initialize_workspace_before_task_dispatch(app: FastAPI) -> None:
    """Validate and reconcile the selected workspace before workers run."""

    init_db()
    app.state.trash_recovery_report = (
        trash_store.recover_interrupted_trash_operations()
    )
    app.state.reliable_task_recovery_report = (
        reliable_task_store.recover_interrupted_tasks()
    )
    app.state.legacy_video_fingerprint_backfill_report = (
        citation_target_service.backfill_legacy_video_fingerprints()
    )
    chat_service.recover_interrupted_chat_turns()
    job_service.recover_interrupted_video_jobs()
    auto_card_generation_service.recover_interrupted_card_generation_runs()
    course_source_service.recover_interrupted_source_indexes()
    for course in course_service.list_video_courses():
        course_source_service.reconcile_course_sources(course.id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    staged_restore = workspace_backup.apply_pending_workspace_restore(
        db_path=get_db_path(),
        data_dir=_runtime_workspace_data_dir(),
    )
    app.state.workspace_restore_result = staged_restore
    try:
        _initialize_workspace_before_task_dispatch(app)
    except Exception as initialization_error:
        if (
            staged_restore is None
            or staged_restore.status != "staged"
        ):
            raise
        try:
            app.state.workspace_restore_result = (
                workspace_backup.rollback_pending_workspace_restore(
                    staged_restore.restore_id,
                    db_path=get_db_path(),
                    data_dir=_runtime_workspace_data_dir(),
                    error=(
                        "Restored workspace failed startup validation: "
                        f"{initialization_error}"
                    ),
                )
            )
        except Exception as rollback_error:
            raise RuntimeError(
                "Restored workspace failed startup validation. Its restore "
                "transaction was retained for manual recovery because "
                "automatic rollback could not complete."
            ) from rollback_error
        # A successful rollback returns the original workspace. It must pass
        # the same initialization gate before this process can serve requests.
        _initialize_workspace_before_task_dispatch(app)
    else:
        if (
            staged_restore is not None
            and staged_restore.status == "staged"
        ):
            try:
                app.state.workspace_restore_result = (
                    workspace_backup.finalize_pending_workspace_restore(
                        staged_restore.restore_id,
                        db_path=get_db_path(),
                        data_dir=_runtime_workspace_data_dir(),
                    )
                )
            except Exception as finalization_error:
                raise RuntimeError(
                    "Restored workspace passed startup validation, but its "
                    "receipt could not be finalized. The restore transaction "
                    "was retained and will be retried on the next startup."
                ) from finalization_error

    manager = get_reliable_task_manager()
    manager.start(recover=False)
    app.state.reliable_task_manager = manager
    try:
        yield
    finally:
        manager.shutdown(wait=False, cancel_futures=True)
        get_reliable_task_manager.cache_clear()


app = FastAPI(lifespan=lifespan)
app.include_router(concept_graph_router)
app.include_router(concept_graph_publication_router)
app.include_router(concept_graph_path_router)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://tauri.localhost",
        "tauri://localhost",
    ],
    allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1):\d+|https?://tauri\.localhost|tauri://localhost)$",
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)


@cache
def get_video_pipeline() -> VideoPipeline:
    transcriber = FasterWhisperTranscriber(
        model_size="base",
        device="cpu",
        compute_type="int8",
    )

    return VideoPipeline(
        transcriber=transcriber,
    )


@cache
def get_llm_client() -> LocalLLMClient:
    return LocalLLMClient()


@cache
def get_reliable_task_manager() -> ReliableTaskManager:
    manager = ReliableTaskManager(
        max_workers=2,
        max_queue_size=12,
        poll_interval_seconds=0.25,
        worker_id_prefix="desktop",
    )
    register_reliable_workflows(
        manager,
        video_pipeline_factory=lambda: get_video_pipeline(),
        llm_client_factory=lambda: get_llm_client(),
        artifact_root=DATA_DIR,
    )
    return manager


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "application_id": "video-course-cards",
        "api_version": 1,
        "instance_token": os.environ.get(
            "VCC_BACKEND_INSTANCE_TOKEN"
        ),
    }


@app.get(
    "/workspace/drafts",
    response_model=list[WorkspaceDraft],
)
def list_workspace_drafts(
    course_id: str | None = Query(default=None),
    draft_type: str | None = Query(default=None),
) -> list[WorkspaceDraft]:
    try:
        return workspace_draft_service.list_workspace_drafts(
            course_id=course_id,
            draft_type=draft_type,
        )
    except (
        course_service.CourseServiceError,
        workspace_draft_service.WorkspaceDraftServiceError,
    ) as exc:
        if isinstance(exc, course_service.CourseServiceError):
            raise_course_http_error(exc)
        raise_workspace_draft_http_error(exc)


@app.get(
    "/workspace/drafts/{draft_id}",
    response_model=WorkspaceDraft,
)
def get_workspace_draft(draft_id: str) -> WorkspaceDraft:
    try:
        return workspace_draft_service.get_workspace_draft(draft_id)
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except workspace_draft_service.WorkspaceDraftServiceError as exc:
        raise_workspace_draft_http_error(exc)


@app.put(
    "/workspace/drafts/{draft_id}",
    response_model=WorkspaceDraft,
)
def put_workspace_draft(
    draft_id: str,
    request: WorkspaceDraftPut,
) -> WorkspaceDraft:
    try:
        return workspace_draft_service.save_workspace_draft(
            draft_id,
            request,
        )
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except workspace_draft_service.WorkspaceDraftServiceError as exc:
        raise_workspace_draft_http_error(exc)


@app.delete(
    "/workspace/drafts/{draft_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_workspace_draft(
    draft_id: str,
    expected_revision: int | None = Query(default=None, ge=1),
) -> Response:
    try:
        workspace_draft_service.remove_workspace_draft(
            draft_id,
            expected_revision=expected_revision,
        )
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except workspace_draft_service.WorkspaceDraftServiceError as exc:
        raise_workspace_draft_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/trash", response_model=list[TrashItem])
def list_workspace_trash(
    course_id: str | None = Query(default=None),
) -> list[TrashItem]:
    return trash_service.list_workspace_trash(course_id=course_id)


@app.post(
    "/trash/{item_id}/restore",
    response_model=TrashItem,
)
def restore_workspace_trash(item_id: str) -> TrashItem:
    try:
        return trash_service.restore_workspace_trash_item(item_id)
    except trash_service.TrashServiceError as exc:
        raise_trash_http_error(exc)


@app.delete(
    "/trash/{item_id}",
    response_model=TrashItem,
)
def purge_workspace_trash(item_id: str) -> TrashItem:
    try:
        return trash_service.purge_workspace_trash_item(
            item_id,
            artifact_root=_runtime_workspace_data_dir(),
        )
    except trash_service.TrashServiceError as exc:
        raise_trash_http_error(exc)


@app.get(
    "/workspace/backups",
    response_model=list[WorkspaceBackupRecord],
)
def list_workspace_backup_records() -> list[WorkspaceBackupRecord]:
    try:
        return [
            _backup_record(item)
            for item in workspace_backup.list_workspace_backups(
                data_dir=_runtime_workspace_data_dir(),
            )
        ]
    except Exception as exc:
        raise_workspace_backup_http_error(exc)


@app.post(
    "/workspace/backups",
    response_model=WorkspaceBackupRecord,
    status_code=status.HTTP_201_CREATED,
)
def create_workspace_backup_record() -> WorkspaceBackupRecord:
    try:
        with workspace_lifecycle_lock():
            require_no_active_tasks(
                conflict_detail=(
                    "Wait for background activity to finish or cancel it "
                    "before creating a workspace backup."
                ),
            )
            created = workspace_backup.create_workspace_backup(
                db_path=get_db_path(),
                data_dir=_runtime_workspace_data_dir(),
        )
        return _backup_record(created)
    except HTTPException:
        raise
    except Exception as exc:
        raise_workspace_backup_http_error(exc)


@app.post(
    "/workspace/backups/import",
    response_model=WorkspaceBackupRecord,
    status_code=status.HTTP_201_CREATED,
)
def import_workspace_backup(
    backup: UploadFile,
) -> WorkspaceBackupRecord:
    filename = (backup.filename or "").strip()
    if not filename.lower().endswith(workspace_backup.BACKUP_EXTENSION):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                "Choose a Video Course Cards .vcc-backup archive."
            ),
        )

    backup_dir = _runtime_workspace_data_dir() / "backups"
    backup_id = (
        f"vcc-imported-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-"
        f"{uuid4().hex[:8]}{workspace_backup.BACKUP_EXTENSION}"
    )
    final_path = backup_dir / backup_id
    temporary_path = backup_dir / f".{backup_id}.tmp"
    size_bytes = 0
    limit = workspace_backup.BackupLimits().max_archive_bytes
    try:
        with workspace_lifecycle_lock():
            require_no_active_tasks(
                conflict_detail=(
                    "Wait for background activity to finish or cancel it "
                    "before importing a workspace backup."
                ),
            )
            backup_dir.mkdir(parents=True, exist_ok=True)
            with temporary_path.open("xb") as destination:
                while chunk := backup.file.read(1024 * 1024):
                    size_bytes += len(chunk)
                    if size_bytes > limit:
                        raise workspace_backup.BackupValidationError(
                            "Backup archive is too large."
                        )
                    destination.write(chunk)
            workspace_backup.validate_workspace_backup(
                temporary_path,
            )
            temporary_path.replace(final_path)
            validated = workspace_backup.validate_workspace_backup(
                final_path
            )
            return _backup_record(validated)
    except HTTPException:
        raise
    except Exception as exc:
        temporary_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise_workspace_backup_http_error(exc)


@app.get(
    "/workspace/backups/{backup_id}/validate",
    response_model=WorkspaceBackupRecord,
)
def validate_workspace_backup_record(
    backup_id: str,
) -> WorkspaceBackupRecord:
    try:
        return _backup_record(
            workspace_backup.validate_workspace_backup(
                _workspace_backup_path(backup_id),
            )
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise_workspace_backup_http_error(exc)


@app.get("/workspace/backups/{backup_id}/download")
def download_workspace_backup(backup_id: str) -> FileResponse:
    path = _workspace_backup_path(backup_id)
    return FileResponse(
        path,
        media_type="application/zip",
        filename=path.name,
    )


@app.post(
    "/workspace/backups/{backup_id}/restore",
    response_model=PendingWorkspaceRestoreResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def queue_workspace_backup_restore(
    backup_id: str,
) -> PendingWorkspaceRestoreResponse:
    try:
        with workspace_lifecycle_lock():
            require_no_active_tasks(
                conflict_detail=(
                    "Wait for background activity to finish or cancel it "
                    "before scheduling a workspace restore."
                ),
            )
            pending = workspace_backup.queue_workspace_restore(
                _workspace_backup_path(backup_id),
                data_dir=_runtime_workspace_data_dir(),
            )
        return _restore_response(pending)
    except HTTPException:
        raise
    except Exception as exc:
        raise_workspace_backup_http_error(exc)


@app.get(
    "/workspace/restore-status",
    response_model=WorkspaceRestoreStatusResponse,
)
def get_workspace_restore_status(
) -> WorkspaceRestoreStatusResponse:
    try:
        restore_state = workspace_backup.get_workspace_restore_state(
            data_dir=_runtime_workspace_data_dir(),
        )
    except Exception as exc:
        raise_workspace_backup_http_error(exc)
    return WorkspaceRestoreStatusResponse(
        workspace_generation=restore_state.workspace_generation,
        pending=(
            _restore_response(restore_state.pending)
            if restore_state.pending is not None
            else None
        ),
        last_result=_restore_result_payload(
            restore_state.last_result
        ),
    )


@app.delete(
    "/workspace/restore-pending/{restore_id}",
    response_model=WorkspaceRestoreResultResponse,
)
def cancel_workspace_backup_restore(
    restore_id: str,
) -> WorkspaceRestoreResultResponse:
    try:
        canceled = workspace_backup.cancel_pending_workspace_restore(
            restore_id,
            data_dir=_runtime_workspace_data_dir(),
        )
        response = _restore_result_payload(canceled)
        if response is None:
            raise RuntimeError("Canceled restore did not produce a result.")
        return response
    except Exception as exc:
        raise_workspace_backup_http_error(exc)


@app.get("/tasks", response_model=list[ReliableTask])
def list_reliable_tasks(
    course_id: str | None = Query(default=None),
    task_status: list[ReliableTaskStatus] | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ReliableTask]:
    return reliable_task_store.list_tasks(
        course_id=course_id,
        statuses=task_status,
        limit=limit,
    )


@app.get("/tasks/{task_id}", response_model=ReliableTask)
def get_reliable_task(task_id: str) -> ReliableTask:
    task = reliable_task_store.get_task(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found.",
        )
    return task


@app.get(
    "/tasks/{task_id}/events",
    response_model=list[TaskEvent],
)
def list_reliable_task_events(task_id: str) -> list[TaskEvent]:
    if reliable_task_store.get_task(task_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found.",
        )
    return reliable_task_store.list_task_events(task_id)


@app.post(
    "/tasks/{task_id}/cancel",
    response_model=ReliableTask,
)
def cancel_reliable_task(task_id: str) -> ReliableTask:
    try:
        return get_reliable_task_manager().request_cancel(task_id)
    except Exception as exc:
        raise_reliable_task_http_error(exc)


@app.post(
    "/tasks/{task_id}/retry",
    response_model=ReliableTask,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_reliable_task(task_id: str) -> ReliableTask:
    try:
        return get_reliable_task_manager().retry(task_id)
    except Exception as exc:
        raise_reliable_task_http_error(exc)


@app.get(
    "/runtime/status",
    response_model=RuntimeStatus,
)
def get_runtime_status() -> RuntimeStatus:
    return runtime_service.get_runtime_status()


@app.post(
    "/runtime/check",
    response_model=RuntimeStatus,
)
def check_runtime_status() -> RuntimeStatus:
    return runtime_service.get_runtime_status()


@app.post("/runtime/quiesce")
def quiesce_runtime(
    request: Request,
    response: Response,
) -> dict[str, str]:
    """Stop accepting task work before the desktop owner restarts/exits."""

    instance_token = os.environ.get(
        "VCC_BACKEND_INSTANCE_TOKEN",
        "",
    )
    presented_token = request.headers.get(
        "X-VCC-Instance-Token",
        "",
    )
    if instance_token and not secrets.compare_digest(
        presented_token,
        instance_token,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Backend instance identity did not match.",
        )

    request.app.state.runtime_quiescing = True
    safely_quiesced = get_reliable_task_manager().quiesce(
        timeout_seconds=4.0,
    )
    if not safely_quiesced:
        response.status_code = status.HTTP_409_CONFLICT
        return {
            "status": "timeout",
            "instance_token": instance_token,
            "message": (
                "Background work did not reach a safe checkpoint before "
                "the shutdown deadline."
            ),
        }
    return {
        "status": "quiesced",
        "instance_token": instance_token,
        "message": "Background work reached a safe recovery checkpoint.",
    }


@app.get(
    "/llm/status",
    response_model=LLMStatus,
)
def get_llm_status() -> LLMStatus:
    return get_llm_client().check_status()


@app.get(
    "/llm/models",
    response_model=LLMModelList,
)
def list_llm_models() -> LLMModelList:
    return get_llm_client().list_models()


@app.post("/videos/inspect")
async def inspect_video(video: UploadFile):
    return {
        "filename": video.filename,
        "content_type": video.content_type,
        "size_bytes": video.size,
    }


@app.post(
    "/videos",
    response_model=VideoUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_video(
    video: UploadFile,
    course_id: str | None = Form(default=None),
) -> VideoUploadResponse:
    try:
        job = job_service.create_video_job(
            video_file=video.file,
            original_filename=video.filename,
            content_type=video.content_type,
            upload_dir=UPLOAD_DIR,
            course_id=course_id,
        )
    except job_service.JobServiceError as exc:
        raise_http_error(exc)

    return VideoUploadResponse(
        id=job.id,
        course_id=job.course_id,
        filename=job.original_filename or "",
        stored_name=job.stored_name or "",
        size_bytes=job.size_bytes or 0,
        status=job.status,
    )


@app.get(
    "/courses",
    response_model=list[Course],
)
def list_courses() -> list[Course]:
    return course_service.list_video_courses()


@app.post(
    "/courses",
    response_model=Course,
    status_code=status.HTTP_201_CREATED,
)
def create_course(request: CourseCreate) -> Course:
    try:
        return course_service.create_video_course(request)
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)


@app.patch(
    "/courses/{course_id}",
    response_model=Course,
)
def update_course(
    course_id: str,
    request: CourseUpdate,
) -> Course:
    try:
        return course_service.update_video_course(course_id, request)
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)


@app.delete(
    "/courses/{course_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_course(course_id: str) -> Response:
    with workspace_lifecycle_lock():
        require_no_active_tasks(course_id=course_id)
        try:
            course_service.delete_video_course(course_id)
        except course_service.CourseServiceError as exc:
            raise_course_http_error(exc)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get(
    "/courses/{course_id}/jobs",
    response_model=list[VideoJob],
)
def list_course_jobs(course_id: str) -> list[VideoJob]:
    try:
        return course_service.list_course_jobs(course_id)
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)


@app.get(
    "/courses/{course_id}/cards",
    response_model=list[KnowledgeCard],
)
def list_course_cards(course_id: str) -> list[KnowledgeCard]:
    try:
        return course_service.list_course_cards(course_id)
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)


@app.get(
    "/courses/{course_id}/source-assets",
    response_model=list[SourceAssetDetail],
)
def list_course_source_assets(course_id: str) -> list[SourceAssetDetail]:
    try:
        return source_asset_service.list_course_source_assets(course_id)
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)


@app.post(
    "/courses/{course_id}/source-assets",
    response_model=SourceAssetImportResult,
    status_code=status.HTTP_201_CREATED,
)
async def import_course_source_asset(
    course_id: str,
    file: UploadFile,
) -> SourceAssetImportResult:
    try:
        asset = source_asset_service.stage_course_source_asset(
            course_id,
            filename=file.filename,
            content_type=file.content_type,
            content=await file.read(),
        )
        reservation = reserve_source_import_task(asset)
        return await run_in_threadpool(
            wait_for_legacy_task_result,
            reservation.task,
            result_key="import",
            response_model=SourceAssetImportResult,
        )
    except HTTPException:
        raise
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except source_asset_service.SourceAssetServiceError as exc:
        raise_source_asset_http_error(exc)
    except Exception as exc:
        raise_reliable_task_http_error(exc)


@app.post(
    "/courses/{course_id}/source-asset-tasks",
    response_model=SourceAssetTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_course_source_asset(
    course_id: str,
    file: UploadFile,
) -> SourceAssetTaskResponse:
    try:
        asset = source_asset_service.stage_course_source_asset(
            course_id,
            filename=file.filename,
            content_type=file.content_type,
            content=await file.read(),
        )
        reservation = reserve_source_import_task(asset)
        return SourceAssetTaskResponse(
            asset=asset,
            task=reservation.task,
        )
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except source_asset_service.SourceAssetServiceError as exc:
        raise_source_asset_http_error(exc)
    except Exception as exc:
        raise_reliable_task_http_error(exc)


@app.post(
    "/source-assets/{asset_id}/processing-tasks",
    response_model=SourceAssetTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def requeue_source_asset_processing(
    asset_id: str,
) -> SourceAssetTaskResponse:
    """Create or retry the durable parser task for an existing source."""

    try:
        with workspace_lifecycle_lock():
            asset = source_asset_service.prepare_source_asset_retry(
                asset_id
            )
            existing = reliable_task_store.list_tasks(
                resource_type="source_asset",
                resource_id=asset.id,
                limit=1,
            )
            latest = existing[0] if existing else None
            if (
                latest is not None
                and latest.status in ACTIVE_TASK_STATUSES
            ):
                task = latest
            elif (
                latest is not None
                and latest.status
                in {
                    ReliableTaskStatus.failed,
                    ReliableTaskStatus.canceled,
                }
            ):
                try:
                    task = get_reliable_task_manager().retry(latest.id)
                except ReliableTaskRetryError:
                    # Preserve the exhausted task as audit history and start a
                    # fresh attempt. Otherwise a source can become permanently
                    # unretryable after the task-level attempt budget is used.
                    task = reserve_source_import_task(
                        asset,
                        idempotency_key=(
                            f"source-import:{asset.id}:"
                            f"{asset.updated_at.isoformat()}"
                        ),
                    ).task
                except Exception:
                    source_asset_service.mark_source_asset_enqueue_failed(
                        asset.id
                    )
                    raise
            else:
                reservation = reserve_source_import_task(
                    asset,
                    idempotency_key=(
                        f"source-import:{asset.id}:"
                        f"{asset.updated_at.isoformat()}"
                    ),
                )
                task = reservation.task
        return SourceAssetTaskResponse(asset=asset, task=task)
    except source_asset_service.SourceAssetServiceError as exc:
        raise_source_asset_http_error(exc)
    except Exception as exc:
        raise_reliable_task_http_error(exc)


@app.get(
    "/source-assets/{asset_id}/units",
    response_model=list[SourceUnit],
)
def list_source_asset_units(asset_id: str) -> list[SourceUnit]:
    try:
        return source_asset_service.list_source_asset_units(asset_id)
    except source_asset_service.SourceAssetServiceError as exc:
        raise_source_asset_http_error(exc)


@app.delete(
    "/source-assets/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_source_asset(asset_id: str) -> Response:
    with workspace_lifecycle_lock():
        require_no_active_tasks(
            resource_type="source_asset",
            resource_id=asset_id,
        )
        try:
            source_asset_service.remove_source_asset(asset_id)
        except source_asset_service.SourceAssetServiceError as exc:
            raise_source_asset_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get(
    "/courses/{course_id}/sources",
    response_model=list[CourseSource],
)
def list_course_sources(course_id: str) -> list[CourseSource]:
    try:
        return course_source_service.list_course_sources(course_id)
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except course_source_service.CourseSourceServiceError as exc:
        raise_course_source_http_error(exc)


@app.get(
    "/sources/{source_id}",
    response_model=CourseSource,
)
def get_course_source(source_id: str) -> CourseSource:
    try:
        return course_source_service.get_course_source(source_id)
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except course_source_service.CourseSourceServiceError as exc:
        raise_course_source_http_error(exc)


@app.get(
    "/sources/{source_id}/chunks",
    response_model=list[CourseSourceChunk],
)
def list_course_source_chunks(
    source_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[CourseSourceChunk]:
    try:
        return course_source_service.list_source_chunks(
            source_id,
            limit=limit,
            offset=offset,
        )
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except course_source_service.CourseSourceServiceError as exc:
        raise_course_source_http_error(exc)


@app.patch(
    "/sources/{source_id}",
    response_model=CourseSource,
)
def update_course_source(
    source_id: str,
    request: CourseSourceUpdate,
) -> CourseSource:
    try:
        return course_source_service.update_source_enabled(
            source_id,
            enabled=request.enabled,
        )
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except course_source_service.CourseSourceServiceError as exc:
        raise_course_source_http_error(exc)


@app.post(
    "/courses/{course_id}/sources/index",
    response_model=SourceIndexResult,
)
def index_course_sources(
    course_id: str,
    request: SourceIndexRequest | None = None,
) -> SourceIndexResult:
    try:
        course = course_service.get_video_course(course_id)
        index_request = request or SourceIndexRequest()
        course_source_service.resolve_course_sources(
            course.id,
            index_request.source_ids,
        )
        reservation = get_reliable_task_manager().enqueue(
            kind=SOURCE_INDEX_TASK,
            course_id=course.id,
            resource_type="course",
            resource_id=course.id,
            payload={
                "course_id": course.id,
                "request": index_request.model_dump(mode="json"),
            },
            active_key=f"source-index:{course.id}",
        )
        return wait_for_legacy_task_result(
            reservation.task,
            result_key="index",
            response_model=SourceIndexResult,
        )
    except HTTPException:
        raise
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except course_source_service.CourseSourceServiceError as exc:
        raise_course_source_http_error(exc)
    except Exception as exc:
        raise_reliable_task_http_error(exc)


@app.post(
    "/courses/{course_id}/source-index-tasks",
    response_model=ReliableTask,
    status_code=status.HTTP_202_ACCEPTED,
)
def enqueue_course_source_index(
    course_id: str,
    request: SourceIndexRequest | None = None,
) -> ReliableTask:
    try:
        course = course_service.get_video_course(course_id)
        reservation = get_reliable_task_manager().enqueue(
            kind=SOURCE_INDEX_TASK,
            course_id=course.id,
            resource_type="course",
            resource_id=course.id,
            payload={
                "course_id": course.id,
                "request": (
                    request or SourceIndexRequest()
                ).model_dump(mode="json"),
            },
            active_key=f"source-index:{course.id}",
        )
        return reservation.task
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except Exception as exc:
        raise_reliable_task_http_error(exc)


@app.post(
    "/courses/{course_id}/sources/search",
    response_model=SourceSearchResponse,
)
def search_course_sources(
    course_id: str,
    request: SourceSearchRequest,
) -> SourceSearchResponse:
    try:
        return source_search_service.search_course_sources(
            course_id,
            request,
        )
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except course_source_service.CourseSourceServiceError as exc:
        raise_course_source_http_error(exc)
    except source_search_service.SourceSearchServiceError as exc:
        raise_source_search_http_error(exc)


@app.get(
    "/courses/{course_id}/chat/conversations",
    response_model=list[ChatConversation],
)
def list_chat_conversations(
    course_id: str,
) -> list[ChatConversation]:
    try:
        return chat_service.list_chat_conversations(course_id)
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)


@app.post(
    "/courses/{course_id}/chat/conversations",
    response_model=ChatConversation,
    status_code=status.HTTP_201_CREATED,
)
def create_chat_conversation(
    course_id: str,
    request: ChatConversationCreate,
) -> ChatConversation:
    try:
        return chat_service.create_chat_conversation(course_id, request)
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except course_source_service.CourseSourceServiceError as exc:
        raise_course_source_http_error(exc)
    except chat_service.ChatServiceError as exc:
        raise_chat_http_error(exc)


@app.get(
    "/chat/conversations/{conversation_id}",
    response_model=ChatConversationDetail,
)
def get_chat_conversation(
    conversation_id: str,
) -> ChatConversationDetail:
    try:
        return chat_service.get_chat_conversation(conversation_id)
    except chat_service.ChatServiceError as exc:
        raise_chat_http_error(exc)


@app.patch(
    "/chat/conversations/{conversation_id}",
    response_model=ChatConversation,
)
def update_chat_conversation(
    conversation_id: str,
    request: ChatConversationUpdate,
) -> ChatConversation:
    try:
        return chat_service.update_chat_conversation(
            conversation_id,
            request,
        )
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except course_source_service.CourseSourceServiceError as exc:
        raise_course_source_http_error(exc)
    except chat_service.ChatServiceError as exc:
        raise_chat_http_error(exc)


@app.delete(
    "/chat/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_chat_conversation(conversation_id: str) -> Response:
    with workspace_lifecycle_lock():
        require_no_active_tasks(
            resource_type="chat_conversation",
            resource_id=conversation_id,
        )
        try:
            chat_service.delete_chat_conversation(conversation_id)
        except chat_service.ChatServiceError as exc:
            raise_chat_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post(
    "/chat/conversations/{conversation_id}/messages",
    response_model=ChatTurnResponse,
)
def send_chat_message(
    conversation_id: str,
    request: ChatMessageCreate,
) -> ChatTurnResponse:
    try:
        conversation = chat_service.get_chat_conversation(conversation_id)
        reservation = get_reliable_task_manager().enqueue(
            kind=CHAT_GENERATION_TASK,
            course_id=conversation.course_id,
            resource_type="chat_conversation",
            resource_id=conversation.id,
            payload={
                "conversation_id": conversation.id,
                "request": request.model_dump(mode="json"),
            },
            idempotency_key=(
                f"{conversation.id}:{request.client_request_id}"
            ),
            active_key=f"chat:{conversation.id}",
        )
        result = wait_for_legacy_task_result(
            reservation.task,
            result_key="turn",
            response_model=ChatTurnResponse,
        )
        if reservation.replayed:
            return result.model_copy(update={"replayed": True})
        return result
    except HTTPException:
        raise
    except chat_service.ChatServiceError as exc:
        raise_chat_http_error(exc)
    except Exception as exc:
        raise_reliable_task_http_error(exc)


@app.post(
    "/chat/conversations/{conversation_id}/message-tasks",
    response_model=ReliableTask,
    status_code=status.HTTP_202_ACCEPTED,
)
def enqueue_chat_message(
    conversation_id: str,
    request: ChatMessageCreate,
) -> ReliableTask:
    try:
        conversation = chat_service.get_chat_conversation(conversation_id)
        reservation = get_reliable_task_manager().enqueue(
            kind=CHAT_GENERATION_TASK,
            course_id=conversation.course_id,
            resource_type="chat_conversation",
            resource_id=conversation.id,
            payload={
                "conversation_id": conversation.id,
                "request": request.model_dump(mode="json"),
            },
            idempotency_key=(
                f"{conversation.id}:{request.client_request_id}"
            ),
            active_key=f"chat:{conversation.id}",
        )
        return reservation.task
    except chat_service.ChatServiceError as exc:
        raise_chat_http_error(exc)
    except Exception as exc:
        raise_reliable_task_http_error(exc)


@app.get(
    "/courses/{course_id}/chat/citations/{citation_id}/target",
    response_model=CitationTargetResponse,
)
def get_chat_citation_target(
    course_id: str,
    citation_id: str,
    request: Request,
    response: Response,
) -> CitationTargetResponse:
    require_loopback_client(request)
    response.headers["Cache-Control"] = "private, no-store"
    media_url = str(
        request.url_for(
            "get_chat_citation_content",
            course_id=course_id,
            citation_id=citation_id,
        )
    )
    try:
        return citation_target_service.resolve_citation_target(
            course_id,
            citation_id,
            media_url=media_url,
        )
    except citation_target_service.CitationTargetServiceError as exc:
        raise_citation_target_http_error(exc)


@app.api_route(
    "/courses/{course_id}/chat/citations/{citation_id}/content",
    methods=["GET", "HEAD"],
    name="get_chat_citation_content",
)
def get_chat_citation_content(
    course_id: str,
    citation_id: str,
    request: Request,
) -> Response:
    require_loopback_client(request)
    try:
        managed_file = citation_target_service.resolve_citation_content(
            course_id,
            citation_id,
        )
    except citation_target_service.CitationTargetServiceError as exc:
        raise_citation_target_http_error(exc)
    return build_citation_content_response(
        managed_file,
        method=request.method,
        range_header=request.headers.get("range"),
    )


GraphEvidenceResourceId = Annotated[
    str,
    ApiPath(min_length=1, max_length=200),
]
GraphEvidenceVersion = Annotated[int, ApiPath(ge=1)]


def _published_graph_evidence_snapshot(
    course_id: str,
    version_number: int,
    owner_type: Literal["concepts", "relations"],
    owner_id: str,
    evidence_id: str,
) -> CitationSnapshotRecord:
    try:
        return concept_graph_publication_service.get_course_version_evidence_snapshot(
            course_id,
            version_number,
            owner_type=("concept" if owner_type == "concepts" else "relation"),
            owner_id=owner_id,
            evidence_id=evidence_id,
        )
    except (
        concept_graph_publication_service.ConceptGraphPublicationServiceError
    ) as exc:
        raise_concept_graph_publication_http_error(exc)


@app.get(
    (
        "/courses/{course_id}/concept-graph/versions/{version_number}/"
        "{owner_type}/{owner_id}/evidence/{evidence_id}/target"
    ),
    response_model=CitationTargetResponse,
)
def get_published_graph_evidence_target(
    course_id: GraphEvidenceResourceId,
    version_number: GraphEvidenceVersion,
    owner_type: Literal["concepts", "relations"],
    owner_id: GraphEvidenceResourceId,
    evidence_id: GraphEvidenceResourceId,
    request: Request,
    response: Response,
) -> CitationTargetResponse:
    require_loopback_client(request)
    response.headers["Cache-Control"] = "private, no-store"
    media_url = str(
        request.url_for(
            "get_published_graph_evidence_content",
            course_id=course_id,
            version_number=version_number,
            owner_type=owner_type,
            owner_id=owner_id,
            evidence_id=evidence_id,
        )
    )
    record = _published_graph_evidence_snapshot(
        course_id,
        version_number,
        owner_type,
        owner_id,
        evidence_id,
    )
    try:
        return citation_target_service.resolve_source_evidence_target(
            course_id,
            record,
            media_url=media_url,
        )
    except citation_target_service.CitationTargetServiceError as exc:
        raise_citation_target_http_error(exc)


@app.api_route(
    (
        "/courses/{course_id}/concept-graph/versions/{version_number}/"
        "{owner_type}/{owner_id}/evidence/{evidence_id}/content"
    ),
    methods=["GET", "HEAD"],
    name="get_published_graph_evidence_content",
)
def get_published_graph_evidence_content(
    course_id: GraphEvidenceResourceId,
    version_number: GraphEvidenceVersion,
    owner_type: Literal["concepts", "relations"],
    owner_id: GraphEvidenceResourceId,
    evidence_id: GraphEvidenceResourceId,
    request: Request,
) -> Response:
    require_loopback_client(request)
    record = _published_graph_evidence_snapshot(
        course_id,
        version_number,
        owner_type,
        owner_id,
        evidence_id,
    )
    try:
        managed_file = citation_target_service.resolve_source_evidence_content(
            course_id,
            record,
        )
    except citation_target_service.CitationTargetServiceError as exc:
        raise_citation_target_http_error(exc)
    return build_citation_content_response(
        managed_file,
        method=request.method,
        range_header=request.headers.get("range"),
    )


@app.get(
    "/courses/{course_id}/notes",
    response_model=list[NotebookNoteSummary],
)
def list_course_notebook_notes(
    course_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[NotebookNoteSummary]:
    try:
        return notebook_note_service.list_course_notebook_notes(
            course_id,
            limit=limit,
            offset=offset,
        )
    except notebook_note_service.NotebookNoteServiceError as exc:
        raise_notebook_note_http_error(exc)


@app.post(
    "/courses/{course_id}/notes",
    response_model=NotebookNote,
    status_code=status.HTTP_201_CREATED,
)
def create_course_notebook_note(
    course_id: str,
    request: NotebookNoteCreate,
) -> NotebookNote:
    try:
        return notebook_note_service.create_course_notebook_note(
            course_id,
            request,
        )
    except notebook_note_service.NotebookNoteServiceError as exc:
        raise_notebook_note_http_error(exc)


@app.post(
    "/courses/{course_id}/notes/from-chat/{message_id}",
    response_model=NotebookNote,
    status_code=status.HTTP_201_CREATED,
)
def capture_chat_answer_as_notebook_note(
    course_id: str,
    message_id: str,
    request: NotebookNoteChatCaptureRequest | None = None,
) -> NotebookNote:
    try:
        return notebook_note_service.capture_chat_answer_as_notebook_note(
            course_id,
            message_id,
            request or NotebookNoteChatCaptureRequest(),
        )
    except notebook_note_service.NotebookNoteServiceError as exc:
        raise_notebook_note_http_error(exc)


@app.get(
    "/courses/{course_id}/notes/{note_id}",
    response_model=NotebookNote,
)
def get_course_notebook_note(
    course_id: str,
    note_id: str,
) -> NotebookNote:
    try:
        return notebook_note_service.get_course_notebook_note(
            course_id,
            note_id,
        )
    except notebook_note_service.NotebookNoteServiceError as exc:
        raise_notebook_note_http_error(exc)


@app.patch(
    "/courses/{course_id}/notes/{note_id}",
    response_model=NotebookNote,
)
def update_course_notebook_note(
    course_id: str,
    note_id: str,
    request: NotebookNoteUpdate,
) -> NotebookNote:
    try:
        return notebook_note_service.update_course_notebook_note(
            course_id,
            note_id,
            request,
        )
    except notebook_note_service.NotebookNoteServiceError as exc:
        raise_notebook_note_http_error(exc)


@app.delete(
    "/courses/{course_id}/notes/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_course_notebook_note(
    course_id: str,
    note_id: str,
    expected_revision: int = Query(ge=1),
) -> Response:
    with workspace_lifecycle_lock():
        try:
            notebook_note_service.delete_course_notebook_note(
                course_id,
                note_id,
                expected_revision=expected_revision,
            )
        except notebook_note_service.NotebookNoteServiceError as exc:
            raise_notebook_note_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post(
    "/courses/{course_id}/notes/{note_id}/source",
    response_model=NotebookNotePromotionResult,
)
def publish_notebook_note_as_source(
    course_id: str,
    note_id: str,
    request: NotebookNotePromotionRequest,
) -> NotebookNotePromotionResult:
    with workspace_lifecycle_lock():
        try:
            return notebook_note_service.publish_notebook_note_as_source(
                course_id,
                note_id,
                request,
            )
        except notebook_note_service.NotebookNoteServiceError as exc:
            raise_notebook_note_http_error(exc)


@app.get(
    "/courses/{course_id}/learning-documents",
    response_model=list[LearningDocument],
)
def list_course_learning_documents(course_id: str) -> list[LearningDocument]:
    try:
        return learning_document_service.list_course_learning_documents(course_id)
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)


@app.get(
    "/courses/{course_id}/map",
    response_model=CourseMap,
)
def get_course_map(course_id: str) -> CourseMap:
    try:
        return topic_service.get_course_map(course_id)
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except topic_service.TopicServiceError as exc:
        raise_topic_http_error(exc)


@app.get(
    "/courses/{course_id}/review/queue",
    response_model=ReviewQueue,
)
def get_course_review_queue(
    course_id: str,
    topic_id: str | None = None,
    limit: int = 50,
) -> ReviewQueue:
    try:
        return review_service.get_course_review_queue(
            course_id,
            topic_id=topic_id,
            limit=limit,
        )
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except (review_service.ReviewServiceError, topic_service.TopicServiceError) as exc:
        if isinstance(exc, topic_service.TopicServiceError):
            raise_topic_http_error(exc)
        raise_review_http_error(exc)


@app.post(
    "/courses/{course_id}/topics",
    response_model=Topic,
    status_code=status.HTTP_201_CREATED,
)
def create_course_topic(course_id: str, request: TopicCreate) -> Topic:
    try:
        return topic_service.create_course_topic(course_id, request)
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except topic_service.TopicServiceError as exc:
        raise_topic_http_error(exc)


@app.post(
    "/courses/{course_id}/topics/suggest",
    response_model=TopicSuggestionResult,
)
def suggest_course_topics(
    course_id: str,
    request: TopicSuggestionRequest,
) -> TopicSuggestionResult:
    try:
        return topic_suggestion_service.suggest_course_topics(
            course_id,
            request,
            llm_client=get_llm_client(),
        )
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except topic_suggestion_service.TopicSuggestionError as exc:
        raise_topic_suggestion_http_error(exc)


@app.post("/topics/{topic_id}/accept", response_model=Topic)
def accept_suggested_topic(topic_id: str) -> Topic:
    try:
        return topic_service.accept_suggested_topic(topic_id)
    except topic_service.TopicServiceError as exc:
        raise_topic_http_error(exc)


@app.post("/topics/{topic_id}/merge", response_model=Topic)
def merge_course_topics(topic_id: str, request: TopicMergeRequest) -> Topic:
    try:
        return topic_service.merge_course_topics(topic_id, request)
    except topic_service.TopicServiceError as exc:
        raise_topic_http_error(exc)


@app.post(
    "/topics/{topic_id}/split",
    response_model=Topic,
    status_code=status.HTTP_201_CREATED,
)
def split_course_topic(topic_id: str, request: TopicSplitRequest) -> Topic:
    try:
        return topic_service.split_course_topic(topic_id, request)
    except topic_service.TopicServiceError as exc:
        raise_topic_http_error(exc)


@app.patch("/topics/{topic_id}", response_model=Topic)
def update_course_topic(topic_id: str, request: TopicUpdate) -> Topic:
    try:
        return topic_service.update_course_topic(topic_id, request)
    except topic_service.TopicServiceError as exc:
        raise_topic_http_error(exc)


@app.delete("/topics/{topic_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_course_topic(topic_id: str) -> Response:
    try:
        topic_service.delete_course_topic(topic_id)
    except topic_service.TopicServiceError as exc:
        raise_topic_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.put(
    "/cards/{card_id}/primary-topic",
    response_model=TopicCardMembership,
)
def set_card_primary_topic(
    card_id: str,
    request: SetPrimaryTopicRequest,
) -> TopicCardMembership:
    try:
        return topic_service.set_card_primary_topic(card_id, request)
    except (topic_service.TopicServiceError, course_service.CourseServiceError) as exc:
        if isinstance(exc, course_service.CourseServiceError):
            raise_course_http_error(exc)
        raise_topic_http_error(exc)


@app.post(
    "/courses/{course_id}/topic-relations",
    response_model=TopicRelation,
    status_code=status.HTTP_201_CREATED,
)
def create_course_topic_relation(
    course_id: str,
    request: TopicRelationCreate,
) -> TopicRelation:
    try:
        return topic_service.create_course_topic_relation(course_id, request)
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except topic_service.TopicServiceError as exc:
        raise_topic_http_error(exc)


@app.delete(
    "/topic-relations/{relation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_course_topic_relation(relation_id: str) -> Response:
    try:
        topic_service.delete_course_topic_relation(relation_id)
    except topic_service.TopicServiceError as exc:
        raise_topic_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post(
    "/courses/{course_id}/chunks",
    response_model=list[TranscriptChunk],
)
def generate_course_transcript_chunks(
    course_id: str,
    request: TranscriptChunkGenerationRequest | None = None,
) -> list[TranscriptChunk]:
    try:
        return transcript_chunk_service.generate_course_chunks(
            course_id,
            request,
        )
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except job_service.JobServiceError as exc:
        raise_http_error(exc)
    except transcript_chunk_service.TranscriptChunkServiceError as exc:
        raise_transcript_chunk_http_error(exc)


@app.get(
    "/courses/{course_id}/chunks",
    response_model=list[TranscriptChunk],
)
def list_course_transcript_chunks(course_id: str) -> list[TranscriptChunk]:
    try:
        return transcript_chunk_service.list_course_transcript_chunks(
            course_id
        )
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)


@app.get(
    "/courses/{course_id}/card-index",
    response_model=list[KnowledgeCardIndexItem],
)
def list_course_card_index(
    course_id: str,
) -> list[KnowledgeCardIndexItem]:
    try:
        return course_service.list_course_card_index(course_id)
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)


@app.post(
    "/courses/{course_id}/card-embeddings",
    response_model=CardEmbeddingBatchResult,
)
def embed_course_cards(course_id: str) -> CardEmbeddingBatchResult:
    try:
        return card_embedding_service.embed_course_cards(course_id)
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except card_embedding_service.CardEmbeddingServiceError as exc:
        raise_card_embedding_http_error(exc)


@app.get(
    "/courses/{course_id}/card-embeddings/status",
    response_model=CardEmbeddingStatus,
)
def get_course_card_embedding_status(
    course_id: str,
) -> CardEmbeddingStatus:
    try:
        return card_embedding_service.get_course_card_embedding_status(
            course_id
        )
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except card_embedding_service.CardEmbeddingServiceError as exc:
        raise_card_embedding_http_error(exc)


@app.post(
    "/courses/{course_id}/card-relations/recompute",
    response_model=CardRelationRecomputeResult,
)
def recompute_course_card_relations(
    course_id: str,
    request: CardRelationRecomputeRequest | None = None,
) -> CardRelationRecomputeResult:
    try:
        return card_relation_service.recompute_course_card_relations(
            course_id,
            request,
        )
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except card_relation_service.CardRelationServiceError as exc:
        raise_card_relation_http_error(exc)


@app.post(
    "/courses/{course_id}/card-relations",
    response_model=CardRelation,
    status_code=status.HTTP_201_CREATED,
)
def create_course_card_relation(
    course_id: str,
    request: CardRelationCreate,
) -> CardRelation:
    try:
        return card_relation_service.create_manual_card_relation(
            course_id,
            request,
        )
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except card_relation_service.CardRelationServiceError as exc:
        raise_card_relation_http_error(exc)


@app.get(
    "/courses/{course_id}/card-relations",
    response_model=CourseCardRelationsGraph,
)
def get_course_card_relations(
    course_id: str,
) -> CourseCardRelationsGraph:
    try:
        return card_relation_service.get_course_card_relations_graph(
            course_id
        )
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)


@app.delete(
    "/courses/{course_id}/cards",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_course_cards(course_id: str) -> Response:
    with workspace_lifecycle_lock():
        require_no_active_tasks(course_id=course_id)
        try:
            course_service.delete_all_course_cards(course_id)
        except course_service.CourseServiceError as exc:
            raise_course_http_error(exc)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get(
    "/jobs",
    response_model=list[VideoJob],
)
def list_jobs() -> list[VideoJob]:
    return job_service.list_video_jobs()


@app.get(
    "/jobs/{job_id}",
    response_model=VideoJob,
)
def get_job(job_id: str) -> VideoJob:
    try:
        job = job_service.get_video_job(job_id)
    except job_service.JobServiceError as exc:
        raise_http_error(exc)

    return job


@app.post(
    "/jobs/{job_id}/run",
    response_model=VideoJob,
    status_code=status.HTTP_202_ACCEPTED,
)
def run_job(
    job_id: str,
    response: Response,
) -> VideoJob:
    try:
        job = job_service.start_job(job_id)
    except job_service.JobServiceError as exc:
        raise_http_error(exc)

    try:
        reservation = get_reliable_task_manager().enqueue(
            kind=VIDEO_PROCESSING_TASK,
            course_id=job.course_id,
            resource_type="video_job",
            resource_id=job.id,
            payload={"job_id": job.id},
            idempotency_key=(
                f"video:{job.id}:{job.started_at.isoformat()}"
                if job.started_at is not None
                else f"video:{job.id}:initial"
            ),
            active_key=f"video:{job.id}",
        )
    except Exception as exc:
        job.status = VideoJobStatus.failed
        job.error_message = "Video task could not be queued."
        job_service.save_job_progress(job)
        raise_reliable_task_http_error(exc)
    response.headers["X-Task-ID"] = reservation.task.id
    return job


@app.post(
    "/jobs/{job_id}/retry",
    response_model=VideoJob,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_job(
    job_id: str,
    response: Response,
) -> VideoJob:
    try:
        job = job_service.retry_job(job_id)
    except job_service.JobServiceError as exc:
        raise_http_error(exc)

    try:
        reservation = get_reliable_task_manager().enqueue(
            kind=VIDEO_PROCESSING_TASK,
            course_id=job.course_id,
            resource_type="video_job",
            resource_id=job.id,
            payload={"job_id": job.id},
            idempotency_key=(
                f"video:{job.id}:{job.started_at.isoformat()}"
                if job.started_at is not None
                else f"video:{job.id}:retry"
            ),
            active_key=f"video:{job.id}",
        )
    except Exception as exc:
        job.status = VideoJobStatus.failed
        job.error_message = "Video retry could not be queued."
        job_service.save_job_progress(job)
        raise_reliable_task_http_error(exc)
    response.headers["X-Task-ID"] = reservation.task.id
    return job


@app.delete(
    "/jobs/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_job(job_id: str) -> Response:
    with workspace_lifecycle_lock():
        require_no_active_tasks(
            resource_type="video_job",
            resource_id=job_id,
        )
        try:
            job_service.delete_video_job(
                job_id,
                _runtime_workspace_data_dir(),
            )
        except job_service.JobServiceError as exc:
            raise_http_error(exc)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get(
    "/jobs/{job_id}/transcript",
    response_model=TranscriptionResult,
)
def get_job_transcript(job_id: str) -> TranscriptionResult:
    try:
        return job_service.get_job_transcript(job_id)
    except job_service.JobServiceError as exc:
        raise_http_error(exc)


@app.get(
    "/jobs/{job_id}/context",
    response_model=TranscriptContext,
)
def get_transcript_context(
    job_id: str,
    start_seconds: float,
    end_seconds: float,
) -> TranscriptContext:
    try:
        return job_service.get_transcript_context(
            job_id,
            start_seconds,
            end_seconds,
        )
    except job_service.JobServiceError as exc:
        raise_http_error(exc)


@app.post(
    "/jobs/{job_id}/chunks",
    response_model=list[TranscriptChunk],
)
def generate_job_transcript_chunks(
    job_id: str,
    request: TranscriptChunkGenerationRequest | None = None,
) -> list[TranscriptChunk]:
    try:
        return transcript_chunk_service.generate_job_chunks(
            job_id,
            request,
        )
    except job_service.JobServiceError as exc:
        raise_http_error(exc)
    except transcript_chunk_service.TranscriptChunkServiceError as exc:
        raise_transcript_chunk_http_error(exc)


@app.get(
    "/jobs/{job_id}/chunks",
    response_model=list[TranscriptChunk],
)
def list_job_transcript_chunks(job_id: str) -> list[TranscriptChunk]:
    try:
        return transcript_chunk_service.list_job_chunks(job_id)
    except job_service.JobServiceError as exc:
        raise_http_error(exc)


@app.post(
    "/jobs/{job_id}/card-embeddings",
    response_model=CardEmbeddingBatchResult,
)
def embed_job_cards(job_id: str) -> CardEmbeddingBatchResult:
    try:
        return card_embedding_service.embed_job_cards(job_id)
    except job_service.JobServiceError as exc:
        raise_http_error(exc)
    except card_embedding_service.CardEmbeddingServiceError as exc:
        raise_card_embedding_http_error(exc)


@app.get(
    "/jobs/{job_id}/card-embeddings/status",
    response_model=CardEmbeddingStatus,
)
def get_job_card_embedding_status(job_id: str) -> CardEmbeddingStatus:
    try:
        return card_embedding_service.get_job_card_embedding_status(job_id)
    except job_service.JobServiceError as exc:
        raise_http_error(exc)
    except card_embedding_service.CardEmbeddingServiceError as exc:
        raise_card_embedding_http_error(exc)


@app.post(
    "/cards/draft",
    response_model=card_service.CardDraftResponse,
)
def draft_cards(
    request: card_service.CardDraftRequest,
) -> card_service.CardDraftResponse:
    try:
        return card_service.draft_knowledge_cards(
            request,
            llm_client=get_llm_client(),
        )
    except job_service.JobServiceError as exc:
        raise_http_error(exc)
    except card_service.CardServiceError as exc:
        raise_card_http_error(exc)


@app.post(
    "/jobs/{job_id}/cards/auto-generate",
    response_model=CardGenerationRun,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_auto_card_generation(
    job_id: str,
    response: Response,
    request: AutoCardGenerationRequest | None = None,
) -> CardGenerationRun:
    try:
        run = auto_card_generation_service.start_auto_card_generation(
            job_id,
            request,
        )
    except job_service.JobServiceError as exc:
        raise_http_error(exc)
    except auto_card_generation_service.AutoCardGenerationServiceError as exc:
        raise_auto_generation_http_error(exc)

    try:
        reservation = get_reliable_task_manager().enqueue(
            kind=AUTO_CARD_GENERATION_TASK,
            course_id=job_service.get_video_job(job_id).course_id,
            resource_type="video_job",
            resource_id=job_id,
            payload={"run_id": run.id},
            idempotency_key=f"auto-cards:{run.id}",
            active_key=f"auto-cards:{job_id}",
        )
    except Exception as exc:
        auto_card_generation_service.mark_card_generation_enqueue_failed(
            run.id
        )
        raise_reliable_task_http_error(exc)
    response.headers["X-Task-ID"] = reservation.task.id

    return run


@app.get(
    "/card-generation-runs/{run_id}",
    response_model=CardGenerationRun,
)
def get_card_generation_run(run_id: str) -> CardGenerationRun:
    try:
        return auto_card_generation_service.get_card_generation_run(run_id)
    except auto_card_generation_service.AutoCardGenerationServiceError as exc:
        raise_auto_generation_http_error(exc)


@app.get(
    "/jobs/{job_id}/card-generation-runs",
    response_model=list[CardGenerationRun],
)
def list_job_card_generation_runs(job_id: str) -> list[CardGenerationRun]:
    try:
        return auto_card_generation_service.list_job_card_generation_runs(
            job_id
        )
    except job_service.JobServiceError as exc:
        raise_http_error(exc)


@app.post(
    "/rag/retrieve",
    response_model=RagRetrieveResponse,
)
def retrieve_rag_cards(
    request: RagRetrieveRequest,
) -> RagRetrieveResponse:
    try:
        return rag_service.retrieve_cards(request)
    except job_service.JobServiceError as exc:
        raise_http_error(exc)
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except card_embedding_service.CardEmbeddingServiceError as exc:
        raise_card_embedding_http_error(exc)
    except rag_service.RagServiceError as exc:
        raise_rag_http_error(exc)


@app.get("/jobs/{job_id}/cards/export/markdown")
def export_job_cards_markdown(job_id: str) -> Response:
    try:
        return archive_response(
            export_service.export_job_cards_markdown(job_id)
        )
    except job_service.JobServiceError as exc:
        raise_http_error(exc)


@app.post(
    "/jobs/{job_id}/cards/export/markdown/local",
    response_model=export_service.SavedMarkdownArchive,
)
def save_job_cards_markdown_export(job_id: str):
    try:
        archive = export_service.export_job_cards_markdown(job_id)
        return export_service.save_archive_to_disk(archive)
    except job_service.JobServiceError as exc:
        raise_http_error(exc)


@app.post(
    "/jobs/{job_id}/cards/export/markdown/folder",
    response_model=export_service.SavedMarkdownFolder,
)
def save_job_cards_markdown_folder_export(job_id: str):
    try:
        return export_service.save_job_cards_markdown_folder(job_id)
    except job_service.JobServiceError as exc:
        raise_http_error(exc)


@app.get(
    "/jobs/{job_id}/cards",
    response_model=list[KnowledgeCardDetail],
)
def list_job_cards(job_id: str) -> list[KnowledgeCardDetail]:
    try:
        return knowledge_card_service.list_job_cards(job_id)
    except job_service.JobServiceError as exc:
        raise_http_error(exc)


@app.post(
    "/jobs/{job_id}/cards",
    response_model=KnowledgeCardDetail,
    status_code=status.HTTP_201_CREATED,
)
def save_job_card(
    job_id: str,
    request: KnowledgeCardCreate,
) -> KnowledgeCardDetail:
    try:
        return knowledge_card_service.save_job_card(job_id, request)
    except job_service.JobServiceError as exc:
        raise_http_error(exc)
    except knowledge_card_service.KnowledgeCardServiceError as exc:
        raise_knowledge_card_http_error(exc)


@app.delete(
    "/jobs/{job_id}/cards",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_job_cards(job_id: str) -> Response:
    with workspace_lifecycle_lock():
        require_no_active_tasks(
            resource_type="video_job",
            resource_id=job_id,
        )
        try:
            knowledge_card_service.delete_all_job_cards(job_id)
        except job_service.JobServiceError as exc:
            raise_http_error(exc)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/cards/export/markdown")
def export_all_cards_markdown() -> Response:
    return archive_response(
        export_service.export_all_cards_markdown()
    )


@app.post(
    "/cards/export/markdown/local",
    response_model=export_service.SavedMarkdownArchive,
)
def save_all_cards_markdown_export():
    archive = export_service.export_all_cards_markdown()
    return export_service.save_archive_to_disk(archive)


@app.post(
    "/cards/export/markdown/folder",
    response_model=export_service.SavedMarkdownFolder,
)
def save_all_cards_markdown_folder_export():
    return export_service.save_all_cards_markdown_folder()


@app.get(
    "/cards/{card_id}/related",
    response_model=CardRelatedCardsResponse,
)
def get_card_related_cards(card_id: str) -> CardRelatedCardsResponse:
    try:
        return card_relation_service.get_related_cards(card_id)
    except card_relation_service.CardRelationServiceError as exc:
        raise_card_relation_http_error(exc)


@app.get(
    "/cards/{card_id}",
    response_model=KnowledgeCardDetail,
)
def get_saved_card(card_id: str) -> KnowledgeCardDetail:
    try:
        return knowledge_card_service.get_saved_card(card_id)
    except knowledge_card_service.KnowledgeCardServiceError as exc:
        raise_knowledge_card_http_error(exc)


@app.post(
    "/cards/{card_id}/embedding",
    response_model=CardEmbeddingBatchResult,
)
def embed_saved_card(card_id: str) -> CardEmbeddingBatchResult:
    try:
        return card_embedding_service.embed_card(card_id)
    except card_embedding_service.CardEmbeddingServiceError as exc:
        raise_card_embedding_http_error(exc)


@app.get(
    "/cards/{card_id}/embedding/status",
    response_model=CardEmbeddingStatus,
)
def get_saved_card_embedding_status(card_id: str) -> CardEmbeddingStatus:
    try:
        return card_embedding_service.get_card_embedding_status(card_id)
    except card_embedding_service.CardEmbeddingServiceError as exc:
        raise_card_embedding_http_error(exc)


@app.get(
    "/cards/{card_id}/learning-documents",
    response_model=list[LearningDocument],
)
def list_card_learning_documents(card_id: str) -> list[LearningDocument]:
    try:
        return learning_document_service.list_card_learning_documents(card_id)
    except learning_document_service.LearningDocumentServiceError as exc:
        raise_learning_document_http_error(exc)


@app.post(
    "/cards/{card_id}/learning-documents",
    response_model=LearningDocumentDetail,
    status_code=status.HTTP_201_CREATED,
)
def create_card_learning_document(
    card_id: str,
    request: LearningDocumentCreate,
) -> LearningDocumentDetail:
    try:
        return learning_document_service.create_card_learning_document(
            card_id,
            request,
        )
    except learning_document_service.LearningDocumentServiceError as exc:
        raise_learning_document_http_error(exc)


@app.get(
    "/learning-documents/{document_id}",
    response_model=LearningDocumentDetail,
)
def get_learning_document(document_id: str) -> LearningDocumentDetail:
    try:
        return learning_document_service.get_saved_learning_document(document_id)
    except learning_document_service.LearningDocumentServiceError as exc:
        raise_learning_document_http_error(exc)


@app.patch(
    "/learning-documents/{document_id}",
    response_model=LearningDocumentDetail,
)
def update_learning_document(
    document_id: str,
    request: LearningDocumentUpdate,
) -> LearningDocumentDetail:
    try:
        return learning_document_service.update_saved_learning_document(
            document_id,
            request,
        )
    except learning_document_service.LearningDocumentServiceError as exc:
        raise_learning_document_http_error(exc)


@app.delete(
    "/learning-documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_learning_document(document_id: str) -> Response:
    with workspace_lifecycle_lock():
        require_no_active_tasks(
            resource_type="learning_document",
            resource_id=document_id,
        )
        try:
            learning_document_service.delete_saved_learning_document(
                document_id
            )
        except learning_document_service.LearningDocumentServiceError as exc:
            raise_learning_document_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post(
    "/learning-documents/{document_id}/cards",
    response_model=LearningDocumentDetail,
)
def add_learning_document_card(
    document_id: str,
    request: LearningDocumentCardLinkCreate,
) -> LearningDocumentDetail:
    try:
        return learning_document_service.add_learning_document_card(
            document_id,
            request,
        )
    except learning_document_service.LearningDocumentServiceError as exc:
        raise_learning_document_http_error(exc)


@app.delete(
    "/learning-documents/{document_id}/cards/{card_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_learning_document_card(document_id: str, card_id: str) -> Response:
    try:
        learning_document_service.remove_learning_document_card(
            document_id,
            card_id,
        )
    except learning_document_service.LearningDocumentServiceError as exc:
        raise_learning_document_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post(
    "/learning-documents/{document_id}/generate",
    response_model=LearningDocumentGenerationResult,
)
def generate_learning_document(
    document_id: str,
    request: LearningDocumentGenerateRequest,
) -> LearningDocumentGenerationResult:
    try:
        document = learning_document_service.get_saved_learning_document(
            document_id
        )
        reservation = get_reliable_task_manager().enqueue(
            kind=LEARNING_DOCUMENT_GENERATION_TASK,
            course_id=document.course_id,
            resource_type="learning_document",
            resource_id=document.id,
            payload={
                "document_id": document.id,
                "request": request.model_dump(mode="json"),
            },
            active_key=f"learning-document:{document.id}",
        )
        return wait_for_legacy_task_result(
            reservation.task,
            result_key="generation",
            response_model=LearningDocumentGenerationResult,
        )
    except HTTPException:
        raise
    except learning_document_service.LearningDocumentServiceError as exc:
        raise_learning_document_http_error(exc)
    except Exception as exc:
        raise_reliable_task_http_error(exc)


@app.post(
    "/learning-documents/{document_id}/generation-tasks",
    response_model=ReliableTask,
    status_code=status.HTTP_202_ACCEPTED,
)
def enqueue_learning_document_generation(
    document_id: str,
    request: LearningDocumentGenerateRequest,
) -> ReliableTask:
    try:
        document = learning_document_service.get_saved_learning_document(
            document_id
        )
        reservation = get_reliable_task_manager().enqueue(
            kind=LEARNING_DOCUMENT_GENERATION_TASK,
            course_id=document.course_id,
            resource_type="learning_document",
            resource_id=document.id,
            payload={
                "document_id": document.id,
                "request": request.model_dump(mode="json"),
            },
            active_key=f"learning-document:{document.id}",
        )
        return reservation.task
    except learning_document_service.LearningDocumentServiceError as exc:
        raise_learning_document_http_error(exc)
    except Exception as exc:
        raise_reliable_task_http_error(exc)


@app.post(
    "/learning-documents/{document_id}/restore",
    response_model=LearningDocumentDetail,
)
def restore_learning_document(
    document_id: str,
    request: LearningDocumentRestoreRequest,
) -> LearningDocumentDetail:
    try:
        return learning_document_service.restore_learning_document_version(
            document_id,
            request,
        )
    except learning_document_service.LearningDocumentServiceError as exc:
        raise_learning_document_http_error(exc)


@app.get(
    "/cards/{card_id}/notes",
    response_model=list[KnowledgeCardNote],
)
def list_card_notes(card_id: str) -> list[KnowledgeCardNote]:
    try:
        return knowledge_card_note_service.list_card_notes(card_id)
    except knowledge_card_note_service.KnowledgeCardNoteServiceError as exc:
        raise_knowledge_card_note_http_error(exc)


@app.post(
    "/cards/{card_id}/notes",
    response_model=KnowledgeCardNote,
    status_code=status.HTTP_201_CREATED,
)
def save_card_note(
    card_id: str,
    request: KnowledgeCardNoteCreate,
) -> KnowledgeCardNote:
    try:
        return knowledge_card_note_service.save_card_note(card_id, request)
    except knowledge_card_note_service.KnowledgeCardNoteServiceError as exc:
        raise_knowledge_card_note_http_error(exc)


@app.patch(
    "/card-notes/{note_id}",
    response_model=KnowledgeCardNote,
)
def update_card_note(
    note_id: str,
    request: KnowledgeCardNoteUpdate,
) -> KnowledgeCardNote:
    try:
        return knowledge_card_note_service.update_card_note(note_id, request)
    except knowledge_card_note_service.KnowledgeCardNoteServiceError as exc:
        raise_knowledge_card_note_http_error(exc)


@app.delete(
    "/card-notes/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_card_note(note_id: str) -> Response:
    try:
        knowledge_card_note_service.delete_card_note(note_id)
    except knowledge_card_note_service.KnowledgeCardNoteServiceError as exc:
        raise_knowledge_card_note_http_error(exc)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.patch(
    "/card-relations/{relation_id}",
    response_model=CardRelation,
)
def update_card_relation(
    relation_id: str,
    request: CardRelationUpdate,
) -> CardRelation:
    try:
        return card_relation_service.update_saved_card_relation(
            relation_id,
            request,
        )
    except card_relation_service.CardRelationServiceError as exc:
        raise_card_relation_http_error(exc)


@app.post(
    "/card-relations/{relation_id}/classify",
    response_model=CardRelationClassificationResult,
)
def classify_card_relation(
    relation_id: str,
    request: CardRelationClassifyRequest,
) -> CardRelationClassificationResult:
    try:
        return card_relation_service.classify_saved_card_relation(
            relation_id,
            request,
            llm_client=get_llm_client(),
        )
    except card_relation_service.CardRelationServiceError as exc:
        raise_card_relation_http_error(exc)


@app.delete(
    "/card-relations/{relation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_card_relation(relation_id: str) -> Response:
    try:
        card_relation_service.delete_saved_card_relation(relation_id)
    except card_relation_service.CardRelationServiceError as exc:
        raise_card_relation_http_error(exc)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.patch(
    "/cards/{card_id}",
    response_model=KnowledgeCardDetail,
)
def update_saved_card(
    card_id: str,
    request: KnowledgeCardUpdate,
) -> KnowledgeCardDetail:
    try:
        return knowledge_card_service.update_saved_card(card_id, request)
    except knowledge_card_service.KnowledgeCardServiceError as exc:
        raise_knowledge_card_http_error(exc)


@app.get(
    "/cards/{card_id}/review-items",
    response_model=list[ReviewItem],
)
def list_card_review_items(card_id: str) -> list[ReviewItem]:
    try:
        return review_item_service.list_card_review_items(card_id)
    except review_item_service.ReviewItemServiceError as exc:
        raise_review_item_http_error(exc)


@app.post(
    "/cards/{card_id}/review-items",
    response_model=ReviewItem,
    status_code=status.HTTP_201_CREATED,
)
def create_card_review_item(
    card_id: str,
    request: ReviewItemCreate,
) -> ReviewItem:
    try:
        return review_item_service.save_card_review_item(card_id, request)
    except review_item_service.ReviewItemServiceError as exc:
        raise_review_item_http_error(exc)


@app.patch(
    "/review-items/{item_id}",
    response_model=ReviewItem,
)
def update_review_item(
    item_id: str,
    request: ReviewItemUpdate,
) -> ReviewItem:
    try:
        return review_item_service.update_saved_review_item(item_id, request)
    except review_item_service.ReviewItemServiceError as exc:
        raise_review_item_http_error(exc)


@app.post(
    "/review-items/{item_id}/rate",
    response_model=ReviewRatingResult,
)
def rate_review_item(
    item_id: str,
    request: ReviewRatingRequest,
) -> ReviewRatingResult:
    try:
        return review_service.rate_review_item(item_id, request)
    except review_service.ReviewServiceError as exc:
        raise_review_http_error(exc)


@app.delete(
    "/review-items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_review_item(item_id: str) -> Response:
    try:
        review_item_service.delete_saved_review_item(item_id)
    except review_item_service.ReviewItemServiceError as exc:
        raise_review_item_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.delete(
    "/cards/{card_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_saved_card(card_id: str) -> Response:
    try:
        knowledge_card_service.delete_saved_card(card_id)
    except knowledge_card_service.KnowledgeCardServiceError as exc:
        raise_knowledge_card_http_error(exc)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
