from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .source_asset import SourceAssetType


CitationAvailability = Literal["available", "snapshot_only"]
CitationMediaKind = Literal[
    "video",
    "audio",
    "pdf",
    "document",
    "text",
]


class CitationTargetContext(BaseModel):
    chunk_id: str = Field(min_length=1)
    ordinal: int = Field(ge=0)
    text: str = Field(min_length=1)
    locator: dict[str, object]
    is_target: bool


class CitationTargetResponse(BaseModel):
    citation_id: str = Field(min_length=1)
    availability: CitationAvailability
    reason: str | None = None
    reason_message: str | None = None
    source_id: str = Field(min_length=1)
    source_title: str = Field(min_length=1)
    source_type: SourceAssetType
    quote: str = Field(min_length=1)
    locator: dict[str, object]
    media_kind: CitationMediaKind | None = None
    media_url: str | None = None
    mime_type: str | None = None
    target_chunk_id: str | None = None
    context: list[CitationTargetContext] = Field(default_factory=list)
