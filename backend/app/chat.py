from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .course_source import SourceLocator, clean_source_ids
from .job import utc_now
from .source_asset import SourceAssetType


ChatRole = Literal["user", "assistant"]
ChatMessageStatus = Literal["generating", "complete", "failed"]
ChatAnswerStatus = Literal["answered", "abstained"]
ChatConversationStatus = Literal["active", "archived"]
ChatTurnStatus = Literal[
    "pending",
    "retrieving",
    "generating",
    "validating",
    "completed",
    "refused",
    "failed",
]


class ChatConversation(BaseModel):
    id: str = Field(min_length=1)
    course_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=160)
    status: ChatConversationStatus = "active"
    selected_source_ids: list[str] = Field(default_factory=list)
    message_count: int = Field(default=0, ge=0)
    last_message_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ChatCitation(BaseModel):
    id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    ordinal: int = Field(ge=1)
    sentence_index: int = Field(ge=0)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=1)
    source_id: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    chunk_text_hash: str = Field(min_length=64, max_length=64)
    source_title: str = Field(min_length=1)
    source_type: SourceAssetType
    quote: str = Field(min_length=1)
    score: float = Field(ge=-1.0, le=1.0)
    locator: SourceLocator
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_offsets(self) -> "ChatCitation":
        if self.end_offset <= self.start_offset:
            raise ValueError(
                "Citation end offset must be greater than start offset."
            )
        return self


class ChatMessage(BaseModel):
    id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    role: ChatRole
    content: str
    status: ChatMessageStatus
    answer_status: ChatAnswerStatus | None = None
    reply_to_message_id: str | None = None
    error_message: str | None = None
    provider: str | None = None
    model: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    citations: list[ChatCitation] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_state(self) -> "ChatMessage":
        if self.role == "user":
            if self.status != "complete":
                raise ValueError("User messages must be complete.")
            if not self.content.strip():
                raise ValueError("User message content is required.")
            if self.answer_status is not None:
                raise ValueError(
                    "User messages cannot have an answer status."
                )
            if self.citations:
                raise ValueError("User messages cannot have citations.")
        elif self.status == "complete":
            if not self.content.strip():
                raise ValueError(
                    "Complete assistant message content is required."
                )
            if self.answer_status is None:
                raise ValueError(
                    "Complete assistant messages need an answer status."
                )
            if self.answer_status == "answered" and not self.citations:
                raise ValueError(
                    "Grounded assistant answers need citations."
                )
            if self.answer_status == "abstained" and self.citations:
                raise ValueError(
                    "Abstained assistant messages cannot have citations."
                )
        elif self.status == "failed" and not self.error_message:
            raise ValueError(
                "Failed assistant messages need a safe error message."
            )
        elif self.status == "generating" and self.answer_status is not None:
            raise ValueError(
                "Generating assistant messages cannot have an answer status."
            )

        for citation in self.citations:
            if citation.message_id != self.id:
                raise ValueError(
                    "Citation belongs to a different message."
                )
            if citation.end_offset > len(self.content):
                raise ValueError(
                    "Citation offsets exceed the assistant message."
                )
        return self


class ChatConversationDetail(ChatConversation):
    messages: list[ChatMessage] = Field(default_factory=list)


class ChatConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=160)
    source_ids: list[str] | None = None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        title = " ".join(value.strip().split())
        return title or None

    @field_validator("source_ids")
    @classmethod
    def clean_sources(
        cls,
        value: list[str] | None,
    ) -> list[str] | None:
        return None if value is None else clean_source_ids(value)


class ChatConversationUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=160)
    source_ids: list[str] | None = None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        title = " ".join(value.strip().split())
        if not title:
            raise ValueError("Conversation title is required.")
        return title

    @field_validator("source_ids")
    @classmethod
    def clean_sources(
        cls,
        value: list[str] | None,
    ) -> list[str] | None:
        return None if value is None else clean_source_ids(value)


class ChatMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    client_request_id: str = Field(min_length=1, max_length=100)
    source_ids: list[str] | None = None
    model: str | None = Field(default=None, max_length=200)

    @field_validator("content")
    @classmethod
    def clean_content(cls, value: str) -> str:
        content = value.strip()
        if not content:
            raise ValueError("Message content is required.")
        return content

    @field_validator("client_request_id")
    @classmethod
    def clean_client_request_id(cls, value: str) -> str:
        request_id = value.strip()
        if not request_id:
            raise ValueError("Client request id is required.")
        return request_id

    @field_validator("source_ids")
    @classmethod
    def clean_sources(
        cls,
        value: list[str] | None,
    ) -> list[str] | None:
        return None if value is None else clean_source_ids(value)

    @field_validator("model")
    @classmethod
    def clean_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        model = value.strip()
        return model or None


class ChatTurnResponse(BaseModel):
    turn_id: str = Field(min_length=1)
    client_request_id: str = Field(min_length=1)
    status: ChatTurnStatus
    source_ids: list[str] = Field(default_factory=list)
    replayed: bool = False
    conversation: ChatConversation
    user_message: ChatMessage
    assistant_message: ChatMessage
