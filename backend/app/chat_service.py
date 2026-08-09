from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from uuid import uuid4

from . import (
    chat_store,
    course_service,
    course_source_service,
    source_search_service,
)
from .chat import (
    ChatCitation,
    ChatConversation,
    ChatConversationCreate,
    ChatConversationDetail,
    ChatConversationUpdate,
    ChatMessage,
    ChatMessageCreate,
    ChatTurnResponse,
)
from .chat_grounding import (
    GroundedChatAnswer,
    GroundedChatOutputError,
    build_grounding_evidence,
    insufficient_evidence_answer,
    parse_grounded_chat_output,
)
from .chat_graph import ChatGraphContext, load_graph_chat_context
from .chat_prompt import (
    CHAT_PROMPT_VERSION,
    ChatHistoryEntry,
    build_grounded_chat_messages,
    build_grounded_chat_repair_messages,
)
from .course_source import (
    SourceSearchRequest,
    SourceSearchResult,
    hash_source_chunk_text,
)
from .embedding import TextEmbedder
from .job import utc_now
from .llm_client import LLMClientError, LLMTimeoutError, LocalLLMClient


LOGGER = logging.getLogger(__name__)

DEFAULT_CONVERSATION_TITLE = "New chat"
RETRIEVAL_TOP_K = 8
RETRIEVAL_MIN_SCORE = 0.25
RETRIEVAL_QUERY_MAX_CHARACTERS = 1500
RETRIEVAL_HISTORY_USER_MESSAGES = 2
GENERATION_HISTORY_MESSAGES = 6
GENERATION_HISTORY_MAX_CHARACTERS = 6000
EVIDENCE_MAX_CHARACTERS = 16000
EVIDENCE_CHUNK_MAX_CHARACTERS = 3000
GENERATION_MAX_TOKENS = 2048

SAFE_INTERRUPTED_MESSAGE = (
    "The previous answer was interrupted. Send the question again to retry."
)
SAFE_RETRIEVAL_MESSAGE = (
    "Source search failed. Check the local model settings and retry."
)
SAFE_SOURCE_CHANGED_MESSAGE = (
    "The selected sources changed while answering. Refresh and retry."
)
SAFE_GENERATION_MESSAGE = (
    "The local language model could not produce a grounded answer. Retry "
    "or choose another model."
)
SAFE_TIMEOUT_MESSAGE = (
    "The local language model timed out. Retry or choose a faster model."
)
SAFE_CANCELED_MESSAGE = "Answer generation was canceled before publication."


class ChatServiceError(Exception):
    pass


class ChatGenerationCancellationRequested(ChatServiceError):
    pass


class ChatConversationNotFoundError(ChatServiceError):
    pass


class ChatTurnConflictError(ChatServiceError):
    pass


class ChatSourceChangedError(ChatServiceError):
    pass


class ChatRetrievalError(ChatServiceError):
    pass


class ChatGenerationError(ChatServiceError):
    pass


class ChatGenerationTimeoutError(ChatGenerationError):
    pass


def create_chat_conversation(
    course_id: str,
    request: ChatConversationCreate,
) -> ChatConversation:
    course_service.get_video_course(course_id)
    source_ids = _resolve_source_snapshot(course_id, request.source_ids)
    now = utc_now()
    conversation = ChatConversation(
        id=uuid4().hex,
        course_id=course_id,
        title=request.title or DEFAULT_CONVERSATION_TITLE,
        selected_source_ids=source_ids,
        created_at=now,
        updated_at=now,
    )
    try:
        chat_store.create_conversation(conversation)
    except chat_store.ChatSourceSnapshotConflictError as exc:
        raise ChatSourceChangedError(SAFE_SOURCE_CHANGED_MESSAGE) from exc
    return _get_conversation(conversation.id)


def list_chat_conversations(course_id: str) -> list[ChatConversation]:
    course_service.get_video_course(course_id)
    return chat_store.list_conversations_for_course(course_id)


def get_chat_conversation(
    conversation_id: str,
) -> ChatConversationDetail:
    conversation = chat_store.get_conversation_detail(conversation_id)
    if conversation is None:
        raise ChatConversationNotFoundError("Chat conversation not found.")
    return conversation


def update_chat_conversation(
    conversation_id: str,
    request: ChatConversationUpdate,
) -> ChatConversation:
    conversation = _get_conversation(conversation_id)
    update = request.model_dump(exclude_unset=True)
    title = (
        request.title
        if "title" in update and request.title is not None
        else None
    )
    source_ids: list[str] | None = None
    if "source_ids" in update and request.source_ids is not None:
        source_ids = _resolve_source_snapshot(
            conversation.course_id,
            request.source_ids,
        )
    try:
        updated = chat_store.patch_conversation(
            conversation.id,
            title=title,
            source_ids=source_ids,
        )
    except chat_store.ChatSourceSnapshotConflictError as exc:
        raise ChatSourceChangedError(SAFE_SOURCE_CHANGED_MESSAGE) from exc
    if updated is None:
        raise ChatConversationNotFoundError("Chat conversation not found.")
    return updated


def delete_chat_conversation(conversation_id: str) -> None:
    if not chat_store.delete_conversation(conversation_id):
        raise ChatConversationNotFoundError("Chat conversation not found.")


def restore_chat_conversation(conversation_id: str) -> ChatConversationDetail:
    if not chat_store.restore_conversation(conversation_id):
        raise ChatConversationNotFoundError(
            "Deleted chat conversation not found or its course is still in trash."
        )
    return get_chat_conversation(conversation_id)


def purge_chat_conversation(
    conversation_id: str,
    *,
    allow_parent_deleted: bool = False,
) -> None:
    if not chat_store.purge_conversation(
        conversation_id,
        allow_parent_deleted=allow_parent_deleted,
    ):
        raise ChatConversationNotFoundError(
            "Deleted chat conversation not found."
        )


def recover_interrupted_chat_turns() -> int:
    return chat_store.recover_active_turns(
        safe_error_message=SAFE_INTERRUPTED_MESSAGE,
    )


def move_course_conversations(
    source_course_id: str,
    target_course_id: str,
) -> int:
    return chat_store.move_conversations_to_course(
        source_course_id,
        target_course_id,
        safe_error_message=SAFE_INTERRUPTED_MESSAGE,
    )


def send_chat_message(
    conversation_id: str,
    request: ChatMessageCreate,
    *,
    llm_client: LocalLLMClient,
    embedder: TextEmbedder | None = None,
    checkpoint: Callable[[], None] | None = None,
    retry_failed: bool = False,
) -> ChatTurnResponse:
    conversation = _get_conversation(conversation_id)
    course_service.get_video_course(conversation.course_id)
    existing_reservation = chat_store.get_turn_reservation(
        conversation.id,
        request.client_request_id,
    )
    if existing_reservation is not None:
        requested_source_ids = request.source_ids
    elif request.source_ids is None:
        requested_source_ids = None
    else:
        requested_source_ids = _resolve_source_snapshot(
            conversation.course_id,
            request.source_ids,
        )
    provider = (
        existing_reservation.assistant_message.provider
        if existing_reservation is not None
        else llm_client.settings.provider
    )
    response_model = (
        request.model
        or (
            existing_reservation.assistant_message.model
            if existing_reservation is not None
            else llm_client.settings.model
        )
    )
    turn_id = uuid4().hex
    try:
        reservation = chat_store.reserve_turn(
            conversation.id,
            turn_id=turn_id,
            user_message_id=uuid4().hex,
            assistant_message_id=uuid4().hex,
            client_request_id=request.client_request_id,
            content=request.content,
            source_ids=requested_source_ids,
            provider=provider,
            model=response_model,
            replace_title_if=DEFAULT_CONVERSATION_TITLE,
            auto_title=_title_from_question(request.content),
            retry_failed=retry_failed,
        )
    except chat_store.ChatSourceSnapshotConflictError as exc:
        raise ChatSourceChangedError(SAFE_SOURCE_CHANGED_MESSAGE) from exc
    except chat_store.ChatTurnConflictError as exc:
        raise ChatTurnConflictError(
            "Another answer is already being generated in this chat."
        ) from exc
    except chat_store.ChatStoreIntegrityError as exc:
        raise ChatConversationNotFoundError(
            "Chat conversation not found."
        ) from exc

    if reservation.is_replay:
        return _replay_turn(reservation)

    try:
        conversation = reservation.conversation
        source_ids = list(reservation.source_ids)
        history_messages = [
            message
            for message in chat_store.list_messages_for_conversation(
                conversation.id
            )
            if message.id != reservation.user_message.id
        ]
        retrieval_query = build_retrieval_query(
            request.content,
            history_messages,
        )
        chat_store.transition_turn(
            reservation.turn_id,
            generation_token=reservation.generation_token,
            expected_status="pending",
            status="retrieving",
            retrieval_query=retrieval_query,
        )

        if not source_ids:
            _checkpoint(checkpoint)
            return _finish_abstention(
                reservation,
                insufficient_evidence_answer(),
                reason="no_sources",
                provider=provider,
                model=response_model,
            )

        try:
            search_response = source_search_service.search_course_sources(
                conversation.course_id,
                SourceSearchRequest(
                    question=retrieval_query,
                    source_ids=source_ids,
                    top_k=RETRIEVAL_TOP_K,
                    min_score=RETRIEVAL_MIN_SCORE,
                ),
                embedder=embedder,
            )
        except source_search_service.SourceSearchConflictError as exc:
            _fail_turn(
                reservation,
                SAFE_SOURCE_CHANGED_MESSAGE,
                error_code="source_changed",
            )
            raise ChatSourceChangedError(
                SAFE_SOURCE_CHANGED_MESSAGE
            ) from exc
        except source_search_service.SourceSearchError as exc:
            _fail_turn(
                reservation,
                SAFE_RETRIEVAL_MESSAGE,
                error_code="retrieval_unavailable",
            )
            raise ChatRetrievalError(SAFE_RETRIEVAL_MESSAGE) from exc
        except (
            course_service.CourseServiceError,
            course_source_service.CourseSourceServiceError,
        ) as exc:
            _fail_turn(
                reservation,
                SAFE_SOURCE_CHANGED_MESSAGE,
                error_code="source_changed",
            )
            raise ChatSourceChangedError(
                SAFE_SOURCE_CHANGED_MESSAGE
            ) from exc

        unexpected_source_ids = sorted(
            {
                result.source_id
                for result in search_response.results
                if result.source_id not in set(source_ids)
            }
        )
        if unexpected_source_ids:
            _fail_turn(
                reservation,
                SAFE_SOURCE_CHANGED_MESSAGE,
                error_code="source_changed",
            )
            raise ChatSourceChangedError(SAFE_SOURCE_CHANGED_MESSAGE)

        selected_results = select_bounded_evidence(search_response.results)
        evidence = build_grounding_evidence(selected_results)
        if not evidence:
            _checkpoint(checkpoint)
            return _finish_abstention(
                reservation,
                insufficient_evidence_answer(),
                reason="no_evidence",
                provider=provider,
                model=response_model,
            )

        # Resolve optional derived navigation only after Source retrieval so a
        # graph invalidated by a concurrent Source reprojection is not carried
        # into generation or persisted with the answer.
        graph_context = load_graph_chat_context(
            conversation.course_id,
            request.content,
            source_ids,
        )
        chat_store.transition_turn(
            reservation.turn_id,
            generation_token=reservation.generation_token,
            expected_status="retrieving",
            status="generating",
            retrieval_query=retrieval_query,
        )
        history = build_generation_history(history_messages)
        try:
            answer = _generate_grounded_answer(
                request.content,
                evidence,
                history,
                graph_context,
                llm_client=llm_client,
                model=response_model,
            )
        except ChatGenerationTimeoutError:
            _fail_turn(
                reservation,
                SAFE_TIMEOUT_MESSAGE,
                error_code="llm_timeout",
            )
            raise
        except ChatGenerationError:
            _fail_turn(
                reservation,
                SAFE_GENERATION_MESSAGE,
                error_code="invalid_grounded_output",
            )
            raise
        _checkpoint(checkpoint)
        if answer.status == "insufficient_evidence":
            return _finish_abstention(
                reservation,
                answer,
                reason="model_insufficient_evidence",
                provider=provider,
                model=response_model,
            )

        chat_store.transition_turn(
            reservation.turn_id,
            generation_token=reservation.generation_token,
            expected_status="generating",
            status="validating",
            retrieval_query=retrieval_query,
        )
        citations = _chat_citations(
            reservation.assistant_message.id,
            answer,
        )
        metadata = {
            "prompt_version": CHAT_PROMPT_VERSION,
            "retrieval_query": retrieval_query,
            "evidence_count": len(evidence),
            "history_message_count": len(history),
            "retrieval_mode": "semantic",
        }
        if graph_context is not None:
            metadata["graph_context"] = graph_context.model_dump(mode="json")
        _checkpoint(checkpoint)
        try:
            assistant_message = chat_store.complete_turn(
                reservation.assistant_message.id,
                generation_token=reservation.generation_token,
                content=answer.content,
                answer_status="answered",
                citations=citations,
                provider=provider,
                model=response_model,
                metadata=metadata,
            )
        except chat_store.ChatEvidenceConflictError as exc:
            _fail_turn(
                reservation,
                SAFE_SOURCE_CHANGED_MESSAGE,
                error_code="source_changed",
            )
            raise ChatSourceChangedError(
                SAFE_SOURCE_CHANGED_MESSAGE
            ) from exc
        except chat_store.ChatMessageStateConflictError as exc:
            raise ChatTurnConflictError(
                "This answer attempt is no longer current."
            ) from exc

        return _turn_response(
            reservation,
            assistant_message,
            status="completed",
        )
    except ChatGenerationCancellationRequested:
        _fail_turn(
            reservation,
            SAFE_CANCELED_MESSAGE,
            error_code="canceled",
        )
        raise
    except ChatServiceError:
        raise
    except chat_store.ChatMessageStateConflictError as exc:
        raise ChatTurnConflictError(
            "This answer attempt is no longer current."
        ) from exc
    except Exception as exc:
        LOGGER.exception("Unexpected grounded chat failure.")
        _fail_turn(
            reservation,
            SAFE_GENERATION_MESSAGE,
            error_code="unexpected_error",
        )
        raise ChatGenerationError(SAFE_GENERATION_MESSAGE) from exc


def _checkpoint(checkpoint: Callable[[], None] | None) -> None:
    if checkpoint is not None:
        checkpoint()


def build_retrieval_query(
    question: str,
    messages: Sequence[ChatMessage],
) -> str:
    prior_questions = [
        message.content
        for message in messages
        if message.role == "user" and message.status == "complete"
    ][-RETRIEVAL_HISTORY_USER_MESSAGES:]
    current = question.strip()
    if len(current) >= RETRIEVAL_QUERY_MAX_CHARACTERS:
        return current[:RETRIEVAL_QUERY_MAX_CHARACTERS]

    selected: list[str] = [current]
    remaining = RETRIEVAL_QUERY_MAX_CHARACTERS - len(current)
    for prior in reversed(prior_questions):
        candidate = prior.strip()
        separator_cost = 1
        if not candidate or len(candidate) + separator_cost > remaining:
            continue
        selected.insert(0, candidate)
        remaining -= len(candidate) + separator_cost
    return "\n".join(selected)


def build_generation_history(
    messages: Sequence[ChatMessage],
) -> list[ChatHistoryEntry]:
    eligible = [
        message
        for message in messages
        if message.status == "complete" and message.content.strip()
    ]
    selected: list[ChatMessage] = []
    used = 0
    for message in reversed(eligible):
        size = len(message.content)
        if size > GENERATION_HISTORY_MAX_CHARACTERS - used:
            continue
        selected.append(message)
        used += size
        if len(selected) >= GENERATION_HISTORY_MESSAGES:
            break
    return [
        ChatHistoryEntry(role=message.role, content=message.content)
        for message in reversed(selected)
    ]


def select_bounded_evidence(
    results: Sequence[SourceSearchResult],
) -> list[SourceSearchResult]:
    selected: list[SourceSearchResult] = []
    used = 0
    for result in results:
        size = len(result.quote)
        if size > EVIDENCE_CHUNK_MAX_CHARACTERS:
            continue
        if used + size > EVIDENCE_MAX_CHARACTERS:
            continue
        selected.append(result)
        used += size
        if len(selected) >= RETRIEVAL_TOP_K:
            break
    return selected


def _generate_grounded_answer(
    question: str,
    evidence,
    history: Sequence[ChatHistoryEntry],
    graph_context: ChatGraphContext | None,
    *,
    llm_client: LocalLLMClient,
    model: str,
) -> GroundedChatAnswer:
    messages = build_grounded_chat_messages(
        question,
        evidence,
        history,
        graph_context,
    )
    try:
        raw_output = llm_client.create_chat_completion(
            messages,
            model=model,
            temperature=0.0,
            max_tokens=GENERATION_MAX_TOKENS,
            response_format={"type": "json_object"},
        )
    except LLMTimeoutError as exc:
        raise ChatGenerationTimeoutError(SAFE_TIMEOUT_MESSAGE) from exc
    except LLMClientError as exc:
        raise ChatGenerationError(SAFE_GENERATION_MESSAGE) from exc

    try:
        return parse_grounded_chat_output(raw_output, evidence)
    except GroundedChatOutputError:
        repair_messages = build_grounded_chat_repair_messages(
            question,
            evidence,
            raw_output,
            history,
            graph_context,
        )
        try:
            repaired_output = llm_client.create_chat_completion(
                repair_messages,
                model=model,
                temperature=0.0,
                max_tokens=GENERATION_MAX_TOKENS,
                response_format={"type": "json_object"},
            )
        except LLMTimeoutError as exc:
            raise ChatGenerationTimeoutError(SAFE_TIMEOUT_MESSAGE) from exc
        except LLMClientError as exc:
            raise ChatGenerationError(SAFE_GENERATION_MESSAGE) from exc
        try:
            return parse_grounded_chat_output(repaired_output, evidence)
        except GroundedChatOutputError as exc:
            raise ChatGenerationError(SAFE_GENERATION_MESSAGE) from exc


def _finish_abstention(
    reservation,
    answer: GroundedChatAnswer,
    *,
    reason: str,
    provider: str,
    model: str,
) -> ChatTurnResponse:
    try:
        assistant_message = chat_store.refuse_turn(
            reservation.assistant_message.id,
            generation_token=reservation.generation_token,
            content=answer.content,
            reason=reason,
            provider=provider,
            model=model,
            metadata={"prompt_version": CHAT_PROMPT_VERSION},
        )
    except chat_store.ChatMessageStateConflictError as exc:
        raise ChatTurnConflictError(
            "This answer attempt is no longer current."
        ) from exc
    return _turn_response(
        reservation,
        assistant_message,
        status="refused",
    )


def _fail_turn(
    reservation,
    safe_message: str,
    *,
    error_code: str,
) -> None:
    try:
        chat_store.fail_turn(
            reservation.assistant_message.id,
            generation_token=reservation.generation_token,
            safe_error_message=safe_message,
            error_code=error_code,
        )
    except chat_store.ChatMessageStateConflictError:
        LOGGER.info(
            "Chat turn failure arrived after its generation token expired."
        )


def _replay_turn(reservation) -> ChatTurnResponse:
    assistant = reservation.assistant_message
    if assistant.status == "generating":
        raise ChatTurnConflictError(
            "This request is already being processed."
        )
    if assistant.status == "failed":
        safe_message = assistant.error_message or SAFE_GENERATION_MESSAGE
        if reservation.error_code == "llm_timeout":
            raise ChatGenerationTimeoutError(safe_message)
        if reservation.error_code == "retrieval_unavailable":
            raise ChatRetrievalError(safe_message)
        if reservation.error_code == "source_changed":
            raise ChatSourceChangedError(safe_message)
        raise ChatGenerationError(safe_message)
    status = (
        "refused"
        if assistant.answer_status == "abstained"
        else "completed"
    )
    return _turn_response(
        reservation,
        assistant,
        status=status,
        replayed=True,
    )


def _turn_response(
    reservation,
    assistant_message: ChatMessage,
    *,
    status: str,
    replayed: bool = False,
) -> ChatTurnResponse:
    return ChatTurnResponse(
        turn_id=reservation.turn_id,
        client_request_id=reservation.client_request_id,
        status=status,
        source_ids=reservation.source_ids,
        replayed=replayed,
        conversation=_get_conversation(
            reservation.user_message.conversation_id
        ),
        user_message=reservation.user_message,
        assistant_message=assistant_message,
    )


def _chat_citations(
    message_id: str,
    answer: GroundedChatAnswer,
) -> list[ChatCitation]:
    evidence_records: dict[str, tuple[str, int]] = {}
    citations: list[ChatCitation] = []
    created_at = utc_now()
    for item in answer.citations:
        record = evidence_records.get(item.chunk_id)
        if record is None:
            record = (uuid4().hex, len(evidence_records) + 1)
            evidence_records[item.chunk_id] = record
        citation_id, ordinal = record
        citations.append(
            ChatCitation(
                id=citation_id,
                message_id=message_id,
                ordinal=ordinal,
                sentence_index=item.sentence_index,
                start_offset=item.start_offset,
                end_offset=item.end_offset,
                source_id=item.source_id,
                chunk_id=item.chunk_id,
                chunk_text_hash=hash_source_chunk_text(item.quote),
                source_title=item.source_title,
                source_type=item.source_type,
                quote=item.quote,
                score=item.score,
                locator=item.locator,
                created_at=created_at,
            )
        )
    return citations


def _resolve_source_snapshot(
    course_id: str,
    requested_source_ids: list[str] | None,
) -> list[str]:
    if requested_source_ids is None:
        return [
            source.id
            for source in course_source_service.list_course_sources(course_id)
            if source.enabled
        ]
    if not requested_source_ids:
        return []
    sources = course_source_service.resolve_course_sources(
        course_id,
        requested_source_ids,
    )
    return [source.id for source in sources]


def _get_conversation(conversation_id: str) -> ChatConversation:
    conversation = chat_store.get_conversation(conversation_id)
    if conversation is None:
        raise ChatConversationNotFoundError("Chat conversation not found.")
    return conversation


def _title_from_question(question: str) -> str:
    title = " ".join(question.strip().split())
    if len(title) <= 72:
        return title
    return title[:69].rstrip() + "..."
