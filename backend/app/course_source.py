from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .job import utc_now
from .source_asset import SourceAssetType


SOURCE_SCHEMA_VERSION = 1

SourceOriginType = Literal["video_job", "source_asset", "notebook_note"]
SourceContentStatus = Literal["pending", "processing", "ready", "failed"]
SourceIndexStatus = Literal[
    "not_indexed",
    "indexing",
    "ready",
    "stale",
    "failed",
]
SourceChunkOriginType = Literal[
    "transcript_chunk",
    "source_unit",
    "notebook_note_snapshot",
]
SourceChunkType = Literal[
    "transcript",
    "slide",
    "page",
    "paragraph",
    "text",
    "video_frame",
]


class VideoTimeLocator(BaseModel):
    schema_version: Literal[1] = SOURCE_SCHEMA_VERSION
    kind: Literal["video_time"] = "video_time"
    job_id: str | None = Field(default=None, min_length=1)
    asset_id: str | None = Field(default=None, min_length=1)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    segment_ids: list[int] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_range(self) -> "VideoTimeLocator":
        if (self.job_id is None) == (self.asset_id is None):
            raise ValueError(
                "Video locator needs exactly one job id or source asset id."
            )
        if self.end_seconds < self.start_seconds:
            raise ValueError(
                "Video locator end must be greater than or equal to start."
            )
        return self


class PdfPageLocator(BaseModel):
    schema_version: Literal[1] = SOURCE_SCHEMA_VERSION
    kind: Literal["pdf_page"] = "pdf_page"
    asset_id: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    metadata: dict[str, object] = Field(default_factory=dict)


class PptSlideLocator(BaseModel):
    schema_version: Literal[1] = SOURCE_SCHEMA_VERSION
    kind: Literal["ppt_slide"] = "ppt_slide"
    asset_id: str = Field(min_length=1)
    slide_number: int = Field(ge=1)
    metadata: dict[str, object] = Field(default_factory=dict)


class DocxParagraphLocator(BaseModel):
    schema_version: Literal[1] = SOURCE_SCHEMA_VERSION
    kind: Literal["docx_paragraph"] = "docx_paragraph"
    asset_id: str = Field(min_length=1)
    paragraph_number: int = Field(ge=1)
    metadata: dict[str, object] = Field(default_factory=dict)


class TextSectionLocator(BaseModel):
    schema_version: Literal[1] = SOURCE_SCHEMA_VERSION
    kind: Literal["text_section"] = "text_section"
    asset_id: str = Field(min_length=1)
    section_number: int = Field(ge=1)
    metadata: dict[str, object] = Field(default_factory=dict)


class NotebookNoteSectionLocator(BaseModel):
    schema_version: Literal[1] = SOURCE_SCHEMA_VERSION
    kind: Literal["note_section"] = "note_section"
    note_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    section_number: int = Field(ge=1)
    metadata: dict[str, object] = Field(default_factory=dict)


SourceLocator = Annotated[
    VideoTimeLocator
    | PdfPageLocator
    | PptSlideLocator
    | DocxParagraphLocator
    | TextSectionLocator
    | NotebookNoteSectionLocator,
    Field(discriminator="kind"),
]


class CourseSource(BaseModel):
    id: str = Field(min_length=1)
    course_id: str = Field(min_length=1)
    origin_type: SourceOriginType
    origin_id: str = Field(min_length=1)
    source_type: SourceAssetType
    title: str = Field(min_length=1)
    content_status: SourceContentStatus
    index_status: SourceIndexStatus = "not_indexed"
    index_model: str | None = None
    index_dimension: int | None = Field(default=None, ge=1)
    enabled: bool = True
    chunk_count: int = Field(default=0, ge=0)
    indexed_chunk_count: int = Field(default=0, ge=0)
    projection_generation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
    )
    projection_manifest_hash: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    size_bytes: int | None = Field(default=None, ge=0)
    mime_type: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    error_message: str | None = None
    index_error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    indexed_at: datetime | None = None


class CourseSourceChunk(BaseModel):
    id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    origin_type: SourceChunkOriginType
    origin_id: str = Field(min_length=1)
    chunk_type: SourceChunkType
    ordinal: int = Field(ge=0)
    text: str = Field(min_length=1)
    text_hash: str = Field(min_length=64, max_length=64)
    locator: SourceLocator
    chunker_version: str = Field(min_length=1)
    is_active: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CourseSourceUpdate(BaseModel):
    enabled: bool


class SourceIndexRequest(BaseModel):
    source_ids: list[str] = Field(default_factory=list)
    regenerate_video_chunks: bool = False

    @field_validator("source_ids")
    @classmethod
    def clean_source_ids(cls, values: list[str]) -> list[str]:
        return clean_source_ids(values)


class SourceIndexResult(BaseModel):
    source_ids: list[str] = Field(default_factory=list)
    total_sources: int = Field(ge=0)
    unavailable_source_ids: list[str] = Field(default_factory=list)
    total_chunks: int = Field(ge=0)
    embedded_chunks: int = Field(ge=0)
    skipped_chunks: int = Field(ge=0)
    model: str
    dimension: int | None = Field(default=None, ge=1)


class SourceSearchRequest(BaseModel):
    question: str = Field(min_length=1)
    source_ids: list[str] = Field(default_factory=list)
    top_k: int = Field(default=8, ge=1, le=50)
    min_score: float | None = Field(default=None, ge=-1.0, le=1.0)

    @field_validator("question")
    @classmethod
    def clean_question(cls, value: str) -> str:
        question = " ".join(value.strip().split())
        if not question:
            raise ValueError("Question is required.")
        return question

    @field_validator("source_ids")
    @classmethod
    def clean_selected_sources(cls, values: list[str]) -> list[str]:
        return clean_source_ids(values)


class SourceSearchResult(BaseModel):
    chunk_id: str
    source_id: str
    source_title: str
    source_type: SourceAssetType
    chunk_type: SourceChunkType
    quote: str
    score: float = Field(ge=-1.0, le=1.0)
    locator: SourceLocator


class SourceSearchResponse(BaseModel):
    question: str
    results: list[SourceSearchResult] = Field(default_factory=list)


def source_id_for_job(job_id: str) -> str:
    return f"job:{job_id}"


def source_id_for_asset(asset_id: str) -> str:
    return f"asset:{asset_id}"


def source_id_for_note(note_id: str) -> str:
    return f"note:{note_id}"


def source_chunk_id(
    origin_type: SourceChunkOriginType,
    origin_id: str,
) -> str:
    return f"{origin_type}:{origin_id}"


def hash_source_chunk_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def clean_source_ids(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        source_id = value.strip()
        if not source_id or source_id in seen:
            continue
        cleaned.append(source_id)
        seen.add(source_id)
    return cleaned
