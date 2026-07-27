from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class WorkspaceDraft(BaseModel):
    id: str
    course_id: str
    draft_type: str
    entity_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    revision: int = Field(ge=1)
    base_updated_at: str | None = None
    created_at: datetime
    updated_at: datetime


class WorkspaceDraftPut(BaseModel):
    course_id: str = Field(min_length=1, max_length=200)
    draft_type: str = Field(min_length=1, max_length=80)
    entity_id: str | None = Field(default=None, max_length=200)
    payload: dict[str, Any] = Field(default_factory=dict)
    expected_revision: int | None = Field(default=None, ge=0)
    base_updated_at: str | None = Field(default=None, max_length=100)

    @field_validator("course_id", "draft_type")
    @classmethod
    def clean_required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value cannot be empty")
        return cleaned

    @field_validator("entity_id", "base_updated_at")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class WorkspaceDraftList(BaseModel):
    drafts: list[WorkspaceDraft]
