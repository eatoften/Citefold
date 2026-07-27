from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

from .course import DEFAULT_COURSE_ID
from .media_metadata import VideoMetadata


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class VideoJobStatus(str, Enum):
    uploaded = "uploaded"
    probing = "probing"
    extracting_audio = "extracting_audio"
    transcribing = "transcribing"
    completed = "completed"
    failed = "failed"
    canceled = "canceled"


class VideoJob(BaseModel):
    id: str
    course_id: str = DEFAULT_COURSE_ID
    video_path: Path
    status: VideoJobStatus
    original_filename: str | None = None
    stored_name: str | None = None
    size_bytes: int | None = None
    metadata: VideoMetadata | None = None
    transcript_path: Path | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
