from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from sqlite3 import Connection, Row
from typing import Literal
from uuid import uuid4

from pydantic import ValidationError

from .chat import (
    ChatAnswerStatus,
    ChatCitation,
    ChatConversation,
    ChatConversationDetail,
    ChatMessage,
    ChatTurnStatus,
)
from .course_source import clean_source_ids
from .db import connect, ensure_db
from .job import utc_now


ACTIVE_TURN_STATUSES = (
    "pending",
    "retrieving",
    "generating",
    "validating",
)
ActiveChatTurnStatus = Literal[
    "pending",
    "retrieving",
    "generating",
    "validating",
]
ChatSourceScopeMode = Literal["conversation", "explicit"]


class ChatStoreError(RuntimeError):
    pass


class ChatStoreIntegrityError(ChatStoreError):
    pass


class ChatTurnConflictError(ChatStoreError):
    pass


class ChatIdempotencyConflictError(ChatTurnConflictError):
    pass


class ChatMessageStateConflictError(ChatStoreError):
    pass


class ChatEvidenceConflictError(ChatStoreError):
    pass


class ChatSourceSnapshotConflictError(ChatStoreError):
    pass


@dataclass(frozen=True)
class ChatTurnReservation:
    turn_id: str
    client_request_id: str
    status: ChatTurnStatus
    generation_token: str | None
    source_ids: list[str]
    source_scope_mode: ChatSourceScopeMode
    error_code: str | None
    conversation: ChatConversation
    user_message: ChatMessage
    assistant_message: ChatMessage
    replayed: bool = False

    @property
    def is_replay(self) -> bool:
        return self.replayed


def create_conversation(conversation: ChatConversation) -> None:
    ensure_db()
    source_ids = clean_source_ids(conversation.selected_source_ids)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _require_course(conn, conversation.course_id)
        _validate_source_snapshot(conn, conversation.course_id, source_ids)
        conn.execute(
            """
            INSERT INTO chat_conversations (
                id, course_id, title, status, selected_source_ids_json,
                next_sequence, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                conversation.id,
                conversation.course_id,
                conversation.title,
                conversation.status,
                _json_text(source_ids),
                _datetime_text(conversation.created_at),
                _datetime_text(conversation.updated_at),
            ),
        )


def get_conversation(conversation_id: str) -> ChatConversation | None:
    ensure_db()
    with connect() as conn:
        return _get_conversation(conn, conversation_id)


def get_conversation_detail(
    conversation_id: str,
) -> ChatConversationDetail | None:
    ensure_db()
    with connect() as conn:
        conversation = _get_conversation(conn, conversation_id)
        if conversation is None:
            return None
        messages = _list_messages(conn, conversation_id)
    return ChatConversationDetail(
        **conversation.model_dump(),
        messages=messages,
    )


def list_conversations_for_course(
    course_id: str,
) -> list[ChatConversation]:
    ensure_db()
    with connect() as conn:
        rows = conn.execute(
            _CONVERSATION_SELECT
            + """
            WHERE conversations.course_id = ?
            GROUP BY conversations.id
            ORDER BY conversations.updated_at DESC, conversations.id
            """,
            (course_id,),
        ).fetchall()
    return [_row_to_conversation(row) for row in rows]


def update_conversation(conversation: ChatConversation) -> bool:
    ensure_db()
    source_ids = clean_source_ids(conversation.selected_source_ids)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT course_id FROM chat_conversations WHERE id = ?",
            (conversation.id,),
        ).fetchone()
        if row is None:
            return False
        if row["course_id"] != conversation.course_id:
            raise ChatStoreIntegrityError(
                "A conversation cannot change courses through update."
            )
        _validate_source_snapshot(conn, conversation.course_id, source_ids)
        cursor = conn.execute(
            """
            UPDATE chat_conversations
            SET title = ?, status = ?, selected_source_ids_json = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                conversation.title,
                conversation.status,
                _json_text(source_ids),
                _datetime_text(conversation.updated_at),
                conversation.id,
            ),
        )
        return cursor.rowcount == 1


def patch_conversation(
    conversation_id: str,
    *,
    title: str | None = None,
    source_ids: list[str] | None = None,
) -> ChatConversation | None:
    """Update only explicitly supplied fields without overwriting concurrent edits."""

    ensure_db()
    cleaned_title = title.strip() if title is not None else None
    if title is not None:
        _require_non_empty(cleaned_title or "", "Conversation title is required.")
    cleaned_source_ids = (
        clean_source_ids(source_ids)
        if source_ids is not None
        else None
    )
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT course_id FROM chat_conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if row is None:
            return None

        assignments: list[str] = []
        parameters: list[object] = []
        if cleaned_title is not None:
            assignments.append("title = ?")
            parameters.append(cleaned_title)
        if cleaned_source_ids is not None:
            _validate_source_snapshot(
                conn,
                str(row["course_id"]),
                cleaned_source_ids,
            )
            assignments.append("selected_source_ids_json = ?")
            parameters.append(_json_text(cleaned_source_ids))
        if not assignments:
            return _get_conversation(conn, conversation_id)

        assignments.append("updated_at = ?")
        parameters.append(_datetime_text(utc_now()))
        parameters.append(conversation_id)
        cursor = conn.execute(
            f"""
            UPDATE chat_conversations
            SET {", ".join(assignments)}
            WHERE id = ?
            """,
            parameters,
        )
        if cursor.rowcount != 1:
            return None
        return _get_conversation(conn, conversation_id)


def delete_conversation(conversation_id: str) -> bool:
    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        exists = conn.execute(
            "SELECT 1 FROM chat_conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if exists is None:
            return False

        message_rows = conn.execute(
            "SELECT id FROM chat_messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchall()
        message_ids = [str(row["id"]) for row in message_rows]
        if message_ids:
            placeholders = ",".join("?" for _ in message_ids)
            conn.execute(
                f"""
                DELETE FROM chat_citation_spans
                WHERE message_id IN ({placeholders})
                """,
                message_ids,
            )
            conn.execute(
                f"""
                DELETE FROM chat_citations
                WHERE message_id IN ({placeholders})
                """,
                message_ids,
            )
        conn.execute(
            "DELETE FROM chat_messages WHERE conversation_id = ?",
            (conversation_id,),
        )
        conn.execute(
            "DELETE FROM chat_turns WHERE conversation_id = ?",
            (conversation_id,),
        )
        conn.execute(
            "DELETE FROM chat_conversations WHERE id = ?",
            (conversation_id,),
        )
        return True


def clear_chat() -> None:
    ensure_db()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM chat_citation_spans")
        conn.execute("DELETE FROM chat_citations")
        conn.execute("DELETE FROM chat_messages")
        conn.execute("DELETE FROM chat_turns")
        conn.execute("DELETE FROM chat_conversations")


def get_message(message_id: str) -> ChatMessage | None:
    ensure_db()
    with connect() as conn:
        return _get_message(conn, message_id)


def list_messages_for_conversation(
    conversation_id: str,
    *,
    limit: int | None = None,
) -> list[ChatMessage]:
    if limit is not None and limit < 1:
        raise ValueError("Message limit must be positive.")
    ensure_db()
    with connect() as conn:
        return _list_messages(conn, conversation_id, limit=limit)


def reserve_turn(
    conversation_id: str,
    *,
    turn_id: str,
    user_message_id: str,
    assistant_message_id: str,
    client_request_id: str,
    content: str,
    source_ids: list[str] | None,
    provider: str | None = None,
    model: str | None = None,
    replace_title_if: str | None = None,
    auto_title: str | None = None,
) -> ChatTurnReservation:
    ensure_db()
    cleaned_content = content.strip()
    cleaned_request_id = client_request_id.strip()
    requested_source_ids = (
        clean_source_ids(source_ids)
        if source_ids is not None
        else None
    )
    source_scope_mode: ChatSourceScopeMode = (
        "conversation" if source_ids is None else "explicit"
    )
    _require_non_empty(cleaned_content, "User message content is required.")
    _require_non_empty(cleaned_request_id, "Client request id is required.")
    cleaned_replace_title = (
        replace_title_if.strip()
        if replace_title_if is not None
        else None
    )
    cleaned_auto_title = auto_title.strip() if auto_title is not None else None
    if (cleaned_replace_title is None) != (cleaned_auto_title is None):
        raise ChatStoreIntegrityError(
            "Automatic title replacement needs both expected and new titles."
        )
    if cleaned_replace_title is not None:
        _require_non_empty(
            cleaned_replace_title,
            "Expected conversation title is required.",
        )
        _require_non_empty(
            cleaned_auto_title or "",
            "Automatic conversation title is required.",
        )
    identifiers = {
        turn_id.strip(),
        user_message_id.strip(),
        assistant_message_id.strip(),
    }
    if "" in identifiers or len(identifiers) != 3:
        raise ChatStoreIntegrityError(
            "Turn and message ids must be non-empty and distinct."
        )

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conversation_row = conn.execute(
            "SELECT * FROM chat_conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if conversation_row is None:
            raise ChatStoreIntegrityError("Conversation does not exist.")

        existing = conn.execute(
            """
            SELECT *
            FROM chat_turns
            WHERE conversation_id = ? AND client_request_id = ?
            """,
            (conversation_id, cleaned_request_id),
        ).fetchone()
        if existing is not None:
            reservation = _reservation_from_turn(
                conn,
                existing,
                replayed=True,
            )
            _validate_idempotent_replay(
                reservation,
                content=cleaned_content,
                source_ids=requested_source_ids,
                source_scope_mode=source_scope_mode,
                provider=provider,
                model=model,
            )
            return reservation

        if conversation_row["status"] != "active":
            raise ChatTurnConflictError(
                "Archived conversations cannot accept new turns."
            )
        active = conn.execute(
            f"""
            SELECT id
            FROM chat_turns
            WHERE conversation_id = ?
              AND status IN ({_placeholders(ACTIVE_TURN_STATUSES)})
            """,
            (conversation_id, *ACTIVE_TURN_STATUSES),
        ).fetchone()
        if active is not None:
            raise ChatTurnConflictError(
                "The conversation already has an active turn."
            )

        course_id = str(conversation_row["course_id"])
        cleaned_source_ids = (
            _json_string_list(
                conversation_row["selected_source_ids_json"],
                field="conversation selected sources",
            )
            if requested_source_ids is None
            else requested_source_ids
        )
        _validate_source_snapshot(conn, course_id, cleaned_source_ids)
        sequence = int(conversation_row["next_sequence"])
        if sequence < 1:
            raise ChatStoreIntegrityError(
                "Conversation sequence state is invalid."
            )
        now = utc_now()
        now_text = _datetime_text(now)
        generation_token = uuid4().hex
        conn.execute(
            """
            INSERT INTO chat_turns (
                id, conversation_id, client_request_id, user_message_id,
                assistant_message_id, status, source_ids_json,
                source_scope_mode, retrieval_query, provider, model,
                generation_token,
                refusal_reason, error_code, error_message,
                created_at, updated_at, started_at, completed_at
            ) VALUES (
                ?, ?, ?, ?, ?, 'pending', ?, ?, NULL, ?, ?, ?,
                NULL, NULL, NULL, ?, ?, ?, NULL
            )
            """,
            (
                turn_id,
                conversation_id,
                cleaned_request_id,
                user_message_id,
                assistant_message_id,
                _json_text(cleaned_source_ids),
                source_scope_mode,
                provider,
                model,
                generation_token,
                now_text,
                now_text,
                now_text,
            ),
        )
        conn.execute(
            """
            INSERT INTO chat_messages (
                id, conversation_id, turn_id, sequence, role, content,
                status, answer_status, reply_to_message_id, error_message,
                provider, model, metadata_json, created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, 'user', ?, 'complete', NULL, NULL, NULL,
                NULL, NULL, '{}', ?, ?
            )
            """,
            (
                user_message_id,
                conversation_id,
                turn_id,
                sequence,
                cleaned_content,
                now_text,
                now_text,
            ),
        )
        conn.execute(
            """
            INSERT INTO chat_messages (
                id, conversation_id, turn_id, sequence, role, content,
                status, answer_status, reply_to_message_id, error_message,
                provider, model, metadata_json, created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, 'assistant', '', 'generating', NULL, ?, NULL,
                ?, ?, '{}', ?, ?
            )
            """,
            (
                assistant_message_id,
                conversation_id,
                turn_id,
                sequence + 1,
                user_message_id,
                provider,
                model,
                now_text,
                now_text,
            ),
        )
        if cleaned_replace_title is None:
            conn.execute(
                """
                UPDATE chat_conversations
                SET selected_source_ids_json = ?, next_sequence = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    _json_text(cleaned_source_ids),
                    sequence + 2,
                    now_text,
                    conversation_id,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE chat_conversations
                SET title = CASE
                        WHEN next_sequence = 1 AND title = ? THEN ?
                        ELSE title
                    END,
                    selected_source_ids_json = ?, next_sequence = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    cleaned_replace_title,
                    cleaned_auto_title,
                    _json_text(cleaned_source_ids),
                    sequence + 2,
                    now_text,
                    conversation_id,
                ),
            )
        turn_row = conn.execute(
            "SELECT * FROM chat_turns WHERE id = ?",
            (turn_id,),
        ).fetchone()
        if turn_row is None:
            raise ChatStoreIntegrityError("Reserved turn disappeared.")
        return _reservation_from_turn(conn, turn_row, replayed=False)


def get_turn_reservation(
    conversation_id: str,
    client_request_id: str,
) -> ChatTurnReservation | None:
    ensure_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM chat_turns
            WHERE conversation_id = ? AND client_request_id = ?
            """,
            (conversation_id, client_request_id),
        ).fetchone()
        if row is None:
            return None
        return _reservation_from_turn(conn, row, replayed=True)


def transition_turn(
    turn_id: str,
    *,
    generation_token: str,
    expected_status: ActiveChatTurnStatus,
    status: ActiveChatTurnStatus,
    retrieval_query: str | None = None,
) -> bool:
    allowed = {
        ("pending", "retrieving"),
        ("retrieving", "generating"),
        ("generating", "validating"),
    }
    if (expected_status, status) not in allowed:
        raise ValueError(
            f"Unsupported turn transition: {expected_status} -> {status}."
        )
    ensure_db()
    now_text = _datetime_text(utc_now())
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            UPDATE chat_turns
            SET status = ?,
                retrieval_query = COALESCE(?, retrieval_query),
                updated_at = ?,
                started_at = COALESCE(started_at, ?)
            WHERE id = ? AND generation_token = ? AND status = ?
            """,
            (
                status,
                retrieval_query,
                now_text,
                now_text,
                turn_id,
                generation_token,
                expected_status,
            ),
        )
        if cursor.rowcount != 1:
            raise ChatMessageStateConflictError(
                "Turn transition lost its compare-and-set race."
            )
        return True


def complete_turn(
    assistant_message_id: str,
    *,
    generation_token: str,
    content: str,
    answer_status: ChatAnswerStatus = "answered",
    citations: list[ChatCitation],
    provider: str | None = None,
    model: str | None = None,
    metadata: dict[str, object] | None = None,
) -> ChatMessage:
    ensure_db()
    answer = content.strip()
    _require_non_empty(answer, "Assistant answer content is required.")
    if answer_status != "answered":
        raise ChatStoreIntegrityError(
            "Grounded completion must have answered status."
        )
    if not citations:
        raise ChatStoreIntegrityError(
            "Grounded answers require at least one citation."
        )
    metadata_json = _json_text(metadata or {})

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        turn = _require_active_turn(
            conn,
            assistant_message_id,
            generation_token,
        )
        conversation = _get_conversation(conn, str(turn["conversation_id"]))
        if conversation is None:
            raise ChatStoreIntegrityError("Turn conversation is missing.")
        grouped_citations = _prepare_citations(
            citations,
            assistant_message_id=assistant_message_id,
            answer=answer,
        )
        _validate_canonical_evidence(
            conn,
            course_id=conversation.course_id,
            allowed_source_ids=_json_string_list(
                turn["source_ids_json"],
                field="turn sources",
            ),
            citations=grouped_citations,
        )

        now_text = _datetime_text(utc_now())
        for citation, spans in grouped_citations:
            conn.execute(
                """
                INSERT INTO chat_citations (
                    id, message_id, ordinal, source_id, chunk_id,
                    chunk_text_hash, source_title, source_type, quote, score,
                    locator_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    citation.id,
                    assistant_message_id,
                    citation.ordinal,
                    citation.source_id,
                    citation.chunk_id,
                    citation.chunk_text_hash,
                    citation.source_title,
                    citation.source_type,
                    citation.quote,
                    citation.score,
                    _json_text(_locator_dict(citation.locator)),
                    _datetime_text(citation.created_at),
                ),
            )
            for span in spans:
                conn.execute(
                    """
                    INSERT INTO chat_citation_spans (
                        id, message_id, citation_id, sentence_index,
                        start_offset, end_offset, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid4().hex,
                        assistant_message_id,
                        citation.id,
                        span.sentence_index,
                        span.start_offset,
                        span.end_offset,
                        now_text,
                    ),
                )

        message_cursor = conn.execute(
            """
            UPDATE chat_messages
            SET content = ?, status = 'complete', answer_status = 'answered',
                error_message = NULL, provider = COALESCE(?, provider),
                model = COALESCE(?, model), metadata_json = ?, updated_at = ?
            WHERE id = ? AND status = 'generating'
            """,
            (
                answer,
                provider,
                model,
                metadata_json,
                now_text,
                assistant_message_id,
            ),
        )
        if message_cursor.rowcount != 1:
            raise ChatMessageStateConflictError(
                "Assistant message is no longer generating."
            )
        turn_cursor = conn.execute(
            f"""
            UPDATE chat_turns
            SET status = 'completed', provider = COALESCE(?, provider),
                model = COALESCE(?, model), generation_token = NULL,
                updated_at = ?, completed_at = ?
            WHERE id = ? AND generation_token = ?
              AND status IN ({_placeholders(ACTIVE_TURN_STATUSES)})
            """,
            (
                provider,
                model,
                now_text,
                now_text,
                turn["id"],
                generation_token,
                *ACTIVE_TURN_STATUSES,
            ),
        )
        if turn_cursor.rowcount != 1:
            raise ChatMessageStateConflictError(
                "Turn completion lost its compare-and-set race."
            )
        _touch_conversation(conn, str(turn["conversation_id"]), now_text)
        message = _get_message(conn, assistant_message_id)
        if message is None:
            raise ChatStoreIntegrityError("Completed assistant message is missing.")
        return message


def refuse_turn(
    assistant_message_id: str,
    *,
    generation_token: str,
    content: str,
    reason: str | None = None,
    refusal_reason: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    metadata: dict[str, object] | None = None,
) -> ChatMessage:
    answer = content.strip()
    if (
        reason is not None
        and refusal_reason is not None
        and reason != refusal_reason
    ):
        raise ChatStoreIntegrityError(
            "Conflicting refusal reasons were provided."
        )
    cleaned_reason = (reason if reason is not None else refusal_reason or "").strip()
    _require_non_empty(answer, "Refusal content is required.")
    _require_non_empty(cleaned_reason, "Refusal reason is required.")
    return _finish_without_citations(
        assistant_message_id,
        generation_token=generation_token,
        message_status="complete",
        answer_status="abstained",
        turn_status="refused",
        content=answer,
        error_message=None,
        error_code=None,
        refusal_reason=cleaned_reason,
        provider=provider,
        model=model,
        metadata=metadata,
    )


def fail_turn(
    assistant_message_id: str,
    *,
    generation_token: str,
    safe_error_message: str,
    error_code: str = "generation_failed",
    provider: str | None = None,
    model: str | None = None,
    metadata: dict[str, object] | None = None,
) -> ChatMessage:
    error_message = safe_error_message.strip()
    cleaned_error_code = error_code.strip()
    _require_non_empty(error_message, "Safe error message is required.")
    _require_non_empty(cleaned_error_code, "Error code is required.")
    return _finish_without_citations(
        assistant_message_id,
        generation_token=generation_token,
        message_status="failed",
        answer_status=None,
        turn_status="failed",
        content="",
        error_message=error_message,
        error_code=cleaned_error_code,
        refusal_reason=None,
        provider=provider,
        model=model,
        metadata=metadata,
    )


def recover_active_turns(
    *,
    safe_error_message: str = (
        "The previous answer was interrupted. Please retry."
    ),
) -> int:
    ensure_db()
    error_message = safe_error_message.strip()
    _require_non_empty(error_message, "Safe recovery error is required.")
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        return _recover_active_turns(
            conn,
            safe_error_message=error_message,
        )


def move_conversations_to_course(
    source_course_id: str,
    target_course_id: str,
    *,
    safe_error_message: str = (
        "The answer was interrupted while its course was moved. "
        "Please retry."
    ),
) -> int:
    ensure_db()
    recovery_message = safe_error_message.strip()
    _require_non_empty(recovery_message, "Safe recovery error is required.")
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _require_course(conn, target_course_id)
        rows = conn.execute(
            """
            SELECT id, selected_source_ids_json
            FROM chat_conversations
            WHERE course_id = ?
            ORDER BY id
            """,
            (source_course_id,),
        ).fetchall()
        if not rows:
            return 0

        conversation_ids = [str(row["id"]) for row in rows]
        _recover_active_turns(
            conn,
            safe_error_message=recovery_message,
            conversation_ids=conversation_ids,
        )
        target_source_rows = conn.execute(
            "SELECT id FROM sources WHERE course_id = ?",
            (target_course_id,),
        ).fetchall()
        target_source_ids = {
            str(source_row["id"])
            for source_row in target_source_rows
        }
        now_text = _datetime_text(utc_now())
        for row in rows:
            selected = _json_string_list(
                row["selected_source_ids_json"],
                field="conversation selected sources",
            )
            filtered = [
                source_id
                for source_id in selected
                if source_id in target_source_ids
            ]
            conn.execute(
                """
                UPDATE chat_conversations
                SET course_id = ?, selected_source_ids_json = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    target_course_id,
                    _json_text(filtered),
                    now_text,
                    row["id"],
                ),
            )
        return len(rows)


def _finish_without_citations(
    assistant_message_id: str,
    *,
    generation_token: str,
    message_status: Literal["complete", "failed"],
    answer_status: ChatAnswerStatus | None,
    turn_status: Literal["refused", "failed"],
    content: str,
    error_message: str | None,
    error_code: str | None,
    refusal_reason: str | None,
    provider: str | None,
    model: str | None,
    metadata: dict[str, object] | None,
) -> ChatMessage:
    ensure_db()
    metadata_json = _json_text(metadata or {})
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        turn = _require_active_turn(
            conn,
            assistant_message_id,
            generation_token,
        )
        now_text = _datetime_text(utc_now())
        message_cursor = conn.execute(
            """
            UPDATE chat_messages
            SET content = ?, status = ?, answer_status = ?,
                error_message = ?, provider = COALESCE(?, provider),
                model = COALESCE(?, model), metadata_json = ?, updated_at = ?
            WHERE id = ? AND status = 'generating'
            """,
            (
                content,
                message_status,
                answer_status,
                error_message,
                provider,
                model,
                metadata_json,
                now_text,
                assistant_message_id,
            ),
        )
        if message_cursor.rowcount != 1:
            raise ChatMessageStateConflictError(
                "Assistant message is no longer generating."
            )
        turn_cursor = conn.execute(
            f"""
            UPDATE chat_turns
            SET status = ?, provider = COALESCE(?, provider),
                model = COALESCE(?, model), generation_token = NULL,
                refusal_reason = ?, error_code = ?, error_message = ?,
                updated_at = ?, completed_at = ?
            WHERE id = ? AND generation_token = ?
              AND status IN ({_placeholders(ACTIVE_TURN_STATUSES)})
            """,
            (
                turn_status,
                provider,
                model,
                refusal_reason,
                error_code,
                error_message,
                now_text,
                now_text,
                turn["id"],
                generation_token,
                *ACTIVE_TURN_STATUSES,
            ),
        )
        if turn_cursor.rowcount != 1:
            raise ChatMessageStateConflictError(
                "Turn finalization lost its compare-and-set race."
            )
        _touch_conversation(conn, str(turn["conversation_id"]), now_text)
        message = _get_message(conn, assistant_message_id)
        if message is None:
            raise ChatStoreIntegrityError("Finalized assistant message is missing.")
        return message


def _recover_active_turns(
    conn: Connection,
    *,
    safe_error_message: str,
    conversation_ids: list[str] | None = None,
) -> int:
    parameters: list[object] = list(ACTIVE_TURN_STATUSES)
    where = f"status IN ({_placeholders(ACTIVE_TURN_STATUSES)})"
    if conversation_ids is not None:
        if not conversation_ids:
            return 0
        where += (
            f" AND conversation_id IN ({_placeholders(conversation_ids)})"
        )
        parameters.extend(conversation_ids)
    rows = conn.execute(
        f"""
        SELECT id, conversation_id, assistant_message_id
        FROM chat_turns
        WHERE {where}
        ORDER BY created_at, id
        """,
        parameters,
    ).fetchall()
    now_text = _datetime_text(utc_now())
    touched_conversations: set[str] = set()
    for row in rows:
        message_cursor = conn.execute(
            """
            UPDATE chat_messages
            SET content = '', status = 'failed', answer_status = NULL,
                error_message = ?, updated_at = ?
            WHERE id = ? AND conversation_id = ? AND status = 'generating'
            """,
            (
                safe_error_message,
                now_text,
                row["assistant_message_id"],
                row["conversation_id"],
            ),
        )
        if message_cursor.rowcount != 1:
            raise ChatStoreIntegrityError(
                "An active turn has no generating assistant message."
            )
        conn.execute(
            """
            UPDATE chat_turns
            SET status = 'failed', generation_token = NULL,
                error_code = 'interrupted', error_message = ?,
                updated_at = ?, completed_at = ?
            WHERE id = ?
            """,
            (safe_error_message, now_text, now_text, row["id"]),
        )
        touched_conversations.add(str(row["conversation_id"]))
    for conversation_id in touched_conversations:
        _touch_conversation(conn, conversation_id, now_text)
    return len(rows)


def _require_active_turn(
    conn: Connection,
    assistant_message_id: str,
    generation_token: str,
) -> Row:
    row = conn.execute(
        f"""
        SELECT *
        FROM chat_turns
        WHERE assistant_message_id = ?
          AND generation_token = ?
          AND status IN ({_placeholders(ACTIVE_TURN_STATUSES)})
        """,
        (
            assistant_message_id,
            generation_token,
            *ACTIVE_TURN_STATUSES,
        ),
    ).fetchone()
    if row is None:
        raise ChatMessageStateConflictError(
            "Turn is no longer active for this generation token."
        )
    message = conn.execute(
        """
        SELECT role, status, conversation_id, turn_id
        FROM chat_messages
        WHERE id = ?
        """,
        (assistant_message_id,),
    ).fetchone()
    if (
        message is None
        or message["role"] != "assistant"
        or message["status"] != "generating"
        or message["conversation_id"] != row["conversation_id"]
        or message["turn_id"] != row["id"]
    ):
        raise ChatStoreIntegrityError(
            "Active turn and assistant message are inconsistent."
        )
    return row


def _prepare_citations(
    citations: list[ChatCitation],
    *,
    assistant_message_id: str,
    answer: str,
) -> list[tuple[ChatCitation, list[ChatCitation]]]:
    grouped: dict[str, tuple[ChatCitation, list[ChatCitation]]] = {}
    ordinals: set[int] = set()
    chunks: set[str] = set()
    spans: set[tuple[str, int, int, int]] = set()
    for citation in citations:
        if isinstance(citation.locator, dict):
            raise ChatStoreIntegrityError(
                "New citations require a supported canonical locator."
            )
        if citation.message_id != assistant_message_id:
            raise ChatStoreIntegrityError(
                "Citation belongs to a different assistant message."
            )
        if citation.end_offset > len(answer):
            raise ChatStoreIntegrityError(
                "Citation offsets exceed the assistant answer."
            )
        span_key = (
            citation.id,
            citation.sentence_index,
            citation.start_offset,
            citation.end_offset,
        )
        if span_key in spans:
            raise ChatStoreIntegrityError("Duplicate citation span.")
        spans.add(span_key)

        existing = grouped.get(citation.id)
        if existing is None:
            if citation.ordinal in ordinals:
                raise ChatStoreIntegrityError("Duplicate citation ordinal.")
            if citation.chunk_id in chunks:
                raise ChatStoreIntegrityError(
                    "One answer cannot persist a chunk twice."
                )
            ordinals.add(citation.ordinal)
            chunks.add(citation.chunk_id)
            grouped[citation.id] = (citation, [citation])
            continue

        core, existing_spans = existing
        if _citation_core(core) != _citation_core(citation):
            raise ChatStoreIntegrityError(
                "Citation spans disagree about their evidence."
            )
        existing_spans.append(citation)
    return sorted(
        grouped.values(),
        key=lambda item: (item[0].ordinal, item[0].id),
    )


def _citation_core(citation: ChatCitation) -> tuple[object, ...]:
    return (
        citation.id,
        citation.message_id,
        citation.ordinal,
        citation.source_id,
        citation.chunk_id,
        citation.chunk_text_hash,
        citation.source_title,
        citation.source_type,
        citation.quote,
        citation.score,
        json.dumps(
            _locator_dict(citation.locator),
            ensure_ascii=False,
            sort_keys=True,
        ),
    )


def _validate_canonical_evidence(
    conn: Connection,
    *,
    course_id: str,
    allowed_source_ids: list[str],
    citations: list[tuple[ChatCitation, list[ChatCitation]]],
) -> None:
    allowed_sources = set(allowed_source_ids)
    for citation, _ in citations:
        if citation.source_id not in allowed_sources:
            raise ChatEvidenceConflictError(
                "Citation evidence is outside the turn source snapshot."
            )
        row = conn.execute(
            """
            SELECT
                chunks.source_id,
                chunks.text,
                chunks.text_hash,
                chunks.locator_json,
                chunks.is_active,
                sources.course_id,
                sources.enabled,
                sources.source_type
            FROM source_chunks AS chunks
            JOIN sources ON sources.id = chunks.source_id
            WHERE chunks.id = ? AND chunks.source_id = ?
            """,
            (citation.chunk_id, citation.source_id),
        ).fetchone()
        if (
            row is None
            or row["course_id"] != course_id
            or not bool(row["enabled"])
            or not bool(row["is_active"])
            or row["text_hash"] != citation.chunk_text_hash
        ):
            raise ChatEvidenceConflictError(
                "Citation evidence changed before the answer was saved."
            )
        if citation.quote not in str(row["text"]):
            raise ChatEvidenceConflictError(
                "Citation quote is not present in the canonical source chunk."
            )
        if row["source_type"] != citation.source_type:
            raise ChatEvidenceConflictError(
                "Citation source type changed before the answer was saved."
            )
        current_locator = _json_object(
            row["locator_json"],
            field="source chunk locator",
        )
        if current_locator != _locator_dict(citation.locator):
            raise ChatEvidenceConflictError(
                "Citation locator changed before the answer was saved."
            )


def _reservation_from_turn(
    conn: Connection,
    row: Row,
    *,
    replayed: bool,
) -> ChatTurnReservation:
    conversation = _get_conversation(conn, str(row["conversation_id"]))
    user_message = _get_message(conn, str(row["user_message_id"]))
    assistant_message = _get_message(conn, str(row["assistant_message_id"]))
    if (
        conversation is None
        or user_message is None
        or assistant_message is None
        or user_message.role != "user"
        or assistant_message.role != "assistant"
        or user_message.turn_id != row["id"]
        or assistant_message.turn_id != row["id"]
    ):
        raise ChatStoreIntegrityError(
            "Turn reservation references incomplete records."
        )
    try:
        status = ChatTurnStatus.__args__[
            ChatTurnStatus.__args__.index(row["status"])
        ]
    except (AttributeError, ValueError):
        raise ChatStoreIntegrityError("Stored turn status is invalid.") from None
    source_scope_mode = str(row["source_scope_mode"])
    if source_scope_mode not in ("conversation", "explicit"):
        raise ChatStoreIntegrityError(
            "Stored turn source scope mode is invalid."
        )
    return ChatTurnReservation(
        turn_id=str(row["id"]),
        client_request_id=str(row["client_request_id"]),
        status=status,
        generation_token=row["generation_token"],
        source_ids=_json_string_list(
            row["source_ids_json"],
            field="turn sources",
        ),
        source_scope_mode=source_scope_mode,
        error_code=row["error_code"],
        conversation=conversation,
        user_message=user_message,
        assistant_message=assistant_message,
        replayed=replayed,
    )


def _validate_idempotent_replay(
    reservation: ChatTurnReservation,
    *,
    content: str,
    source_ids: list[str] | None,
    source_scope_mode: ChatSourceScopeMode,
    provider: str | None,
    model: str | None,
) -> None:
    if (
        reservation.user_message.content != content
        or reservation.source_scope_mode != source_scope_mode
        or (
            source_scope_mode == "explicit"
            and reservation.source_ids != source_ids
        )
        or reservation.assistant_message.provider != provider
        or reservation.assistant_message.model != model
    ):
        raise ChatIdempotencyConflictError(
            "Client request id was reused with a different payload."
        )


_CONVERSATION_SELECT = """
SELECT
    conversations.*,
    COUNT(messages.id) AS message_count,
    MAX(messages.created_at) AS last_message_at
FROM chat_conversations AS conversations
LEFT JOIN chat_messages AS messages
    ON messages.conversation_id = conversations.id
"""


def _get_conversation(
    conn: Connection,
    conversation_id: str,
) -> ChatConversation | None:
    row = conn.execute(
        _CONVERSATION_SELECT
        + """
        WHERE conversations.id = ?
        GROUP BY conversations.id
        """,
        (conversation_id,),
    ).fetchone()
    return _row_to_conversation(row) if row is not None else None


def _row_to_conversation(row: Row) -> ChatConversation:
    return ChatConversation(
        id=row["id"],
        course_id=row["course_id"],
        title=row["title"],
        status=row["status"],
        selected_source_ids=_json_string_list(
            row["selected_source_ids_json"],
            field="conversation selected sources",
        ),
        message_count=int(row["message_count"]),
        last_message_at=_datetime_from_text(row["last_message_at"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _get_message(conn: Connection, message_id: str) -> ChatMessage | None:
    row = conn.execute(
        "SELECT * FROM chat_messages WHERE id = ?",
        (message_id,),
    ).fetchone()
    return _row_to_message(conn, row) if row is not None else None


def _list_messages(
    conn: Connection,
    conversation_id: str,
    *,
    limit: int | None = None,
) -> list[ChatMessage]:
    if limit is None:
        rows = conn.execute(
            """
            SELECT *
            FROM chat_messages
            WHERE conversation_id = ?
            ORDER BY sequence, id
            """,
            (conversation_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT *
            FROM (
                SELECT *
                FROM chat_messages
                WHERE conversation_id = ?
                ORDER BY sequence DESC, id DESC
                LIMIT ?
            )
            ORDER BY sequence, id
            """,
            (conversation_id, limit),
        ).fetchall()
    return [_row_to_message(conn, row) for row in rows]


def _row_to_message(conn: Connection, row: Row) -> ChatMessage:
    citations = (
        _load_citations(conn, str(row["id"]))
        if row["role"] == "assistant"
        else []
    )
    try:
        return ChatMessage(
            id=row["id"],
            conversation_id=row["conversation_id"],
            turn_id=row["turn_id"],
            sequence=row["sequence"],
            role=row["role"],
            content=row["content"],
            status=row["status"],
            answer_status=row["answer_status"],
            reply_to_message_id=row["reply_to_message_id"],
            error_message=row["error_message"],
            provider=row["provider"],
            model=row["model"],
            metadata=_json_object(
                row["metadata_json"],
                field="chat message metadata",
            ),
            citations=citations,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
    except ValidationError as exc:
        raise ChatStoreIntegrityError(
            "Stored chat message violates the chat contract."
        ) from exc


def _load_citations(
    conn: Connection,
    message_id: str,
) -> list[ChatCitation]:
    rows = conn.execute(
        """
        SELECT
            citations.*,
            spans.id AS span_id,
            spans.sentence_index,
            spans.start_offset,
            spans.end_offset
        FROM chat_citations AS citations
        LEFT JOIN chat_citation_spans AS spans
            ON spans.citation_id = citations.id
            AND spans.message_id = citations.message_id
        WHERE citations.message_id = ?
        ORDER BY
            citations.ordinal,
            spans.sentence_index,
            spans.start_offset,
            spans.end_offset,
            spans.id
        """,
        (message_id,),
    ).fetchall()
    citations: list[ChatCitation] = []
    for row in rows:
        if row["span_id"] is None:
            raise ChatStoreIntegrityError(
                "Stored citation has no sentence span."
            )
        try:
            citations.append(
                ChatCitation(
                    id=row["id"],
                    message_id=row["message_id"],
                    ordinal=row["ordinal"],
                    sentence_index=row["sentence_index"],
                    start_offset=row["start_offset"],
                    end_offset=row["end_offset"],
                    source_id=row["source_id"],
                    chunk_id=row["chunk_id"],
                    chunk_text_hash=row["chunk_text_hash"],
                    source_title=row["source_title"],
                    source_type=row["source_type"],
                    quote=row["quote"],
                    score=row["score"],
                    locator=_json_object(
                        row["locator_json"],
                        field="chat citation locator",
                    ),
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
            )
        except ValidationError as exc:
            raise ChatStoreIntegrityError(
                "Stored citation violates the citation contract."
            ) from exc
    return citations


def _require_course(conn: Connection, course_id: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM courses WHERE id = ?",
        (course_id,),
    ).fetchone()
    if row is None:
        raise ChatStoreIntegrityError("Conversation course does not exist.")


def _validate_source_snapshot(
    conn: Connection,
    course_id: str,
    source_ids: list[str],
) -> None:
    if not source_ids:
        return
    rows = conn.execute(
        f"""
        SELECT id, enabled
        FROM sources
        WHERE course_id = ?
          AND id IN ({_placeholders(source_ids)})
        """,
        (course_id, *source_ids),
    ).fetchall()
    found = {
        str(row["id"])
        for row in rows
        if bool(row["enabled"])
    }
    missing = [source_id for source_id in source_ids if source_id not in found]
    if missing:
        raise ChatSourceSnapshotConflictError(
            "Chat source snapshot contains disabled or out-of-course sources."
        )


def _touch_conversation(
    conn: Connection,
    conversation_id: str,
    now_text: str,
) -> None:
    cursor = conn.execute(
        "UPDATE chat_conversations SET updated_at = ? WHERE id = ?",
        (now_text, conversation_id),
    )
    if cursor.rowcount != 1:
        raise ChatStoreIntegrityError("Turn conversation disappeared.")


def _datetime_text(value: datetime) -> str:
    return value.isoformat()


def _datetime_from_text(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _json_text(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ChatStoreIntegrityError(
            "Chat data must be JSON serializable."
        ) from exc


def _locator_dict(locator: object) -> dict[str, object]:
    if isinstance(locator, dict):
        return dict(locator)
    model_dump = getattr(locator, "model_dump", None)
    if not callable(model_dump):
        raise ChatStoreIntegrityError("Citation locator is invalid.")
    value = model_dump(mode="json")
    if not isinstance(value, dict):
        raise ChatStoreIntegrityError("Citation locator is invalid.")
    return value


def _json_object(value: str, *, field: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ChatStoreIntegrityError(f"Stored {field} is invalid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ChatStoreIntegrityError(f"Stored {field} must be an object.")
    return parsed


def _json_string_list(value: str, *, field: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ChatStoreIntegrityError(f"Stored {field} is invalid JSON.") from exc
    if not isinstance(parsed, list) or any(
        not isinstance(item, str)
        for item in parsed
    ):
        raise ChatStoreIntegrityError(f"Stored {field} must be a string list.")
    return clean_source_ids(parsed)


def _placeholders(values: object) -> str:
    return ",".join("?" for _ in values)


def _require_non_empty(value: str, message: str) -> None:
    if not value:
        raise ChatStoreIntegrityError(message)
