from contextlib import asynccontextmanager
from functools import cache

from fastapi import (
    BackgroundTasks,
    FastAPI,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi import status
from fastapi import Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import auto_card_generation_service
from . import card_embedding_service
from . import card_relation_service
from . import card_service
from . import chat_service
from . import course_source_service
from . import course_service
from . import export_service
from . import job_service
from . import knowledge_card_service
from . import knowledge_card_note_service
from . import learning_document_service
from . import rag_service
from . import review_item_service
from . import review_service
from . import runtime_service
from . import source_asset_service
from . import source_index_service
from . import source_search_service
from . import transcript_chunk_service
from . import topic_service
from . import topic_suggestion_service
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
from .db import init_db
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
from .llm_client import LLMModelList, LLMStatus, LocalLLMClient
from .rag import RagRetrieveRequest, RagRetrieveResponse
from .runtime_status import RuntimeStatus
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
from .video_pipeline import VideoPipeline


APP_PATHS = get_app_path_settings()
DATA_DIR = APP_PATHS.data_dir
UPLOAD_DIR = APP_PATHS.upload_dir
SOURCE_DIR = APP_PATHS.source_dir

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
SOURCE_DIR.mkdir(parents=True, exist_ok=True)


class VideoUploadResponse(BaseModel):
    id: str
    course_id: str
    filename: str
    stored_name: str
    size_bytes: int
    status: VideoJobStatus


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    chat_service.recover_interrupted_chat_turns()
    for course in course_service.list_video_courses():
        course_source_service.reconcile_course_sources(course.id)

    yield


app = FastAPI(lifespan=lifespan)


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


@app.get("/health")
def health_check():
    return {"status": "ok"}


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
        return source_asset_service.import_course_source_asset(
            course_id,
            filename=file.filename,
            content_type=file.content_type,
            content=await file.read(),
        )
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except source_asset_service.SourceAssetServiceError as exc:
        raise_source_asset_http_error(exc)


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
        return source_index_service.index_course_sources(
            course_id,
            request,
        )
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except course_source_service.CourseSourceServiceError as exc:
        raise_course_source_http_error(exc)
    except source_index_service.SourceIndexServiceError as exc:
        raise_source_index_http_error(exc)


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
        return chat_service.send_chat_message(
            conversation_id,
            request,
            llm_client=get_llm_client(),
        )
    except course_service.CourseServiceError as exc:
        raise_course_http_error(exc)
    except course_source_service.CourseSourceServiceError as exc:
        raise_course_source_http_error(exc)
    except chat_service.ChatServiceError as exc:
        raise_chat_http_error(exc)


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
    background_tasks: BackgroundTasks,
) -> VideoJob:
    try:
        job = job_service.start_job(job_id)
    except job_service.JobServiceError as exc:
        raise_http_error(exc)

    background_tasks.add_task(
        job_service.run_job_pipeline,
        job.id,
        get_video_pipeline,
        DATA_DIR,
    )
    return job


@app.post(
    "/jobs/{job_id}/retry",
    response_model=VideoJob,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_job(
    job_id: str,
    background_tasks: BackgroundTasks,
) -> VideoJob:
    try:
        job = job_service.retry_job(job_id)
    except job_service.JobServiceError as exc:
        raise_http_error(exc)

    background_tasks.add_task(
        job_service.run_job_pipeline,
        job.id,
        get_video_pipeline,
        DATA_DIR,
    )
    return job


@app.delete(
    "/jobs/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_job(job_id: str) -> Response:
    try:
        job_service.delete_video_job(job_id, DATA_DIR)
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
    background_tasks: BackgroundTasks,
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

    background_tasks.add_task(
        auto_card_generation_service.run_auto_card_generation,
        run.id,
        get_llm_client,
    )

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
    try:
        learning_document_service.delete_saved_learning_document(document_id)
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
        return learning_document_service.generate_learning_document(
            document_id,
            request,
            llm_client=get_llm_client(),
        )
    except learning_document_service.LearningDocumentServiceError as exc:
        raise_learning_document_http_error(exc)


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
