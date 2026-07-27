from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import (
    BaseModel,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from .job import utc_now


class ReliableTaskStatus(str, Enum):
    queued = "queued"
    running = "running"
    canceling = "canceling"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"


ACTIVE_TASK_STATUSES = frozenset(
    {
        ReliableTaskStatus.queued,
        ReliableTaskStatus.running,
        ReliableTaskStatus.canceling,
    }
)
TERMINAL_TASK_STATUSES = frozenset(
    {
        ReliableTaskStatus.succeeded,
        ReliableTaskStatus.failed,
        ReliableTaskStatus.canceled,
    }
)


class ReliableTaskEventType(str, Enum):
    created = "created"
    claimed = "claimed"
    progress_updated = "progress_updated"
    cancel_requested = "cancel_requested"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"
    retry_queued = "retry_queued"
    interrupted = "interrupted"
    transitioned = "transitioned"


class TaskProgress(BaseModel):
    current: float = Field(default=0.0, ge=0.0)
    total: float | None = Field(default=None, ge=0.0)
    stage: str | None = Field(default=None, max_length=100)
    message: str | None = Field(default=None, max_length=500)
    details: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("stage", "message")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.strip().split())
        return cleaned or None

    @model_validator(mode="after")
    def validate_bounds(self) -> "TaskProgress":
        if self.total is not None and self.current > self.total:
            raise ValueError("Task progress cannot exceed its total.")
        return self

    @property
    def fraction(self) -> float | None:
        if self.total is None or self.total == 0:
            return None
        return self.current / self.total


class ReliableTask(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    kind: str = Field(min_length=1, max_length=100)
    course_id: str | None = Field(default=None, max_length=100)
    resource_type: str | None = Field(default=None, max_length=100)
    resource_id: str | None = Field(default=None, max_length=200)
    status: ReliableTaskStatus = ReliableTaskStatus.queued
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    result: dict[str, JsonValue] | None = None
    request_fingerprint: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        exclude=True,
    )
    idempotency_key: str | None = Field(default=None, max_length=200)
    active_key: str | None = Field(default=None, max_length=300)
    priority: int = Field(default=0, ge=-100, le=100)
    attempt: int = Field(default=1, ge=1)
    max_attempts: int = Field(default=3, ge=1, le=20)
    recovery_count: int = Field(default=0, ge=0)
    progress: TaskProgress = Field(default_factory=TaskProgress)
    cancel_requested_at: datetime | None = None
    worker_id: str | None = Field(default=None, max_length=200)
    claim_token: str | None = Field(
        default=None,
        max_length=100,
        exclude=True,
    )
    error_code: str | None = Field(default=None, max_length=100)
    error_message: str | None = Field(default=None, max_length=1000)
    retryable: bool = True
    available_at: datetime = Field(default_factory=utc_now)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    heartbeat_at: datetime | None = None

    @field_validator(
        "kind",
        "course_id",
        "resource_type",
        "resource_id",
        "idempotency_key",
        "active_key",
        "worker_id",
        "claim_token",
        "error_code",
        "error_message",
    )
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def validate_state(self) -> "ReliableTask":
        if self.attempt > self.max_attempts:
            raise ValueError("Task attempt cannot exceed max_attempts.")

        has_claim = bool(self.worker_id and self.claim_token)
        if self.status in {
            ReliableTaskStatus.running,
            ReliableTaskStatus.canceling,
        }:
            if not has_claim:
                raise ValueError("Running tasks require a worker claim.")
        elif self.worker_id is not None or self.claim_token is not None:
            raise ValueError("Only running tasks may retain a worker claim.")

        if (
            self.status == ReliableTaskStatus.canceling
            and self.cancel_requested_at is None
        ):
            raise ValueError("Canceling tasks require a cancellation request.")

        if self.status in TERMINAL_TASK_STATUSES:
            if self.completed_at is None:
                raise ValueError("Terminal tasks require completed_at.")
        elif self.completed_at is not None:
            raise ValueError("Non-terminal tasks cannot have completed_at.")

        if (
            self.status == ReliableTaskStatus.failed
            and not self.error_message
        ):
            raise ValueError("Failed tasks require a safe error message.")
        return self


class TaskEvent(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    task_id: str = Field(min_length=1, max_length=100)
    sequence: int = Field(ge=1)
    event_type: ReliableTaskEventType
    from_status: ReliableTaskStatus | None = None
    to_status: ReliableTaskStatus | None = None
    message: str | None = Field(default=None, max_length=1000)
    data: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
