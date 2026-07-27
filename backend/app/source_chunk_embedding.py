from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .embedding import EmbeddingVector
from .job import utc_now


class SourceChunkEmbedding(BaseModel):
    chunk_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    dimension: int = Field(ge=1)
    text_hash: str = Field(min_length=64, max_length=64)
    vector: EmbeddingVector = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SourceChunkEmbeddingInfo(BaseModel):
    chunk_id: str
    source_id: str
    model: str
    dimension: int
    text_hash: str
    created_at: datetime
    updated_at: datetime


class SourceIndexCommitResult(BaseModel):
    committed_embeddings: int = Field(ge=0)
    ready_source_ids: list[str] = Field(default_factory=list)
    stale_source_ids: list[str] = Field(default_factory=list)
