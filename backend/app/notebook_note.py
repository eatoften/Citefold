from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .course_source import CourseSource
from .job import utc_now
from .source_asset import SourceAssetType


NotebookNoteOriginType = Literal["free", "chat_answer"]


class NotebookNoteCitationSpan(BaseModel):
    sentence_index: int = Field(ge=0)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_offsets(self) -> "NotebookNoteCitationSpan":
        if self.end_offset <= self.start_offset:
            raise ValueError(
                "Citation end offset must be greater than start offset."
            )
        return self


class NotebookNoteCitationSnapshot(BaseModel):
    id: str = Field(min_length=1)
    origin_citation_id: str = Field(min_length=1)
    ordinal: int = Field(ge=1)
    source_id: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    chunk_text_hash: str = Field(min_length=64, max_length=64)
    source_title: str = Field(min_length=1)
    source_type: SourceAssetType
    quote: str = Field(min_length=1)
    score: float = Field(ge=-1.0, le=1.0)
    locator: dict[str, object]
    spans: list[NotebookNoteCitationSpan] = Field(min_length=1)


class FreeNotebookNoteOriginSnapshot(BaseModel):
    origin_type: Literal["free"] = "free"


class ChatAnswerNotebookNoteOriginSnapshot(BaseModel):
    origin_type: Literal["chat_answer"] = "chat_answer"
    conversation_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    answer_text: str = Field(min_length=1)
    provider: str | None = None
    model: str | None = None
    citations: list[NotebookNoteCitationSnapshot] = Field(min_length=1)


NotebookNoteOriginSnapshot = Annotated[
    FreeNotebookNoteOriginSnapshot | ChatAnswerNotebookNoteOriginSnapshot,
    Field(discriminator="origin_type"),
]


class NotebookNote(BaseModel):
    id: str = Field(min_length=1)
    course_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    body_markdown: str = Field(min_length=1)
    revision: int = Field(ge=1)
    origin_type: NotebookNoteOriginType
    origin_snapshot: NotebookNoteOriginSnapshot
    published_snapshot_id: str | None = None
    published_revision: int | None = Field(default=None, ge=1)
    is_source_outdated: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_origin(self) -> "NotebookNote":
        if self.origin_type != self.origin_snapshot.origin_type:
            raise ValueError("Note origin and immutable snapshot disagree.")
        return self


class NotebookNoteSummary(BaseModel):
    id: str = Field(min_length=1)
    course_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    body_preview: str
    revision: int = Field(ge=1)
    origin_type: NotebookNoteOriginType
    citation_count: int = Field(default=0, ge=0)
    published_snapshot_id: str | None = None
    published_revision: int | None = Field(default=None, ge=1)
    is_source_outdated: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class NotebookNoteCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    body_markdown: str = Field(min_length=1, max_length=1_000_000)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.strip().split())
        return cleaned or None

    @field_validator("body_markdown")
    @classmethod
    def clean_body(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Note body is required.")
        return value


class NotebookNoteUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    body_markdown: str | None = Field(
        default=None,
        min_length=1,
        max_length=1_000_000,
    )
    expected_revision: int = Field(ge=1)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.strip().split())
        if not cleaned:
            raise ValueError("Note title is required.")
        return cleaned

    @field_validator("body_markdown")
    @classmethod
    def clean_body(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Note body is required.")
        return value


class NotebookNoteChatCaptureRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.strip().split())
        return cleaned or None


class NotebookNotePromotionRequest(BaseModel):
    expected_revision: int = Field(ge=1)


class NotebookNoteSourceSnapshot(BaseModel):
    id: str = Field(min_length=1)
    note_id: str = Field(min_length=1)
    course_id: str = Field(min_length=1)
    note_revision: int = Field(ge=1)
    title: str = Field(min_length=1)
    body_markdown: str = Field(min_length=1)
    content_hash: str = Field(min_length=64, max_length=64)
    created_at: datetime = Field(default_factory=utc_now)


class NotebookNotePromotionResult(BaseModel):
    note: NotebookNote
    snapshot: NotebookNoteSourceSnapshot
    source: CourseSource
    replayed: bool = False
