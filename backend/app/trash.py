from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field


TrashEntityType = Literal[
    "course",
    "video_job",
    "source_asset",
    "knowledge_card",
    "learning_document",
    "chat_conversation",
]
TrashItemStatus = Literal[
    "trashed",
    "restoring",
    "restore_failed",
    "purging",
    "purge_failed",
]

COURSE_PURGE_METADATA_KEY = "course_purge"
COURSE_PURGE_PLAN_VERSION = 2
COURSE_PURGE_PHASES = (
    "planned",
    "conversations",
    "documents",
    "artifacts",
    "assets",
    "jobs",
    "topics",
    "course",
)
COURSE_PURGE_PLAN_FIELDS = frozenset(
    {"version", "course_id", "phase", "artifacts"}
)
COURSE_PURGE_ARTIFACT_FIELDS = frozenset({"path", "root"})
COURSE_PURGE_MANAGED_ROOTS = (
    "uploads",
    "audio",
    "transcripts",
    "sources",
)

ENTITY_PURGE_METADATA_KEY = "entity_purge"
ENTITY_PURGE_PLAN_VERSION = 2
ENTITY_PURGE_PHASES = (
    "planned",
    "projection",
    "database",
    "artifacts",
)
ENTITY_PURGE_PLAN_FIELDS = frozenset(
    {"version", "entity_type", "phase", "artifacts"}
)
ENTITY_PURGE_ARTIFACT_FIELDS = frozenset({"root", "relative_path"})
ENTITY_PURGE_TYPES = frozenset({"video_job", "source_asset"})
ENTITY_PURGE_ROOTS = frozenset(
    {"uploads", "transcripts", "audio", "sources"}
)
ENTITY_VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".mkv", ".webm"})
ENTITY_SOURCE_EXTENSIONS = frozenset(
    {".pptx", ".pdf", ".docx", ".txt", ".md", ".markdown"}
)


def validate_entity_relative_path(value: str) -> PurePosixPath:
    relative = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or relative.is_absolute()
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(
            "Entity purge recovery plan contains an unsafe artifact path."
        )
    return relative


def validate_entity_purge_plan(
    value: object,
    *,
    entity_type: object,
    entity_id: object,
    course_id: object,
) -> dict[str, object]:
    """Validate a purge journal against the entity's managed file namespace."""

    if (
        not isinstance(value, dict)
        or set(value) != ENTITY_PURGE_PLAN_FIELDS
        or type(value.get("version")) is not int
        or value.get("version") != ENTITY_PURGE_PLAN_VERSION
        or value.get("entity_type") != entity_type
        or not isinstance(entity_type, str)
        or entity_type not in ENTITY_PURGE_TYPES
        or not isinstance(entity_id, str)
        or not entity_id
        or any(character in entity_id for character in ("/", "\\"))
    ):
        raise ValueError("Entity purge recovery plan is invalid.")
    phase = value.get("phase")
    artifacts = value.get("artifacts")
    if phase not in ENTITY_PURGE_PHASES or not isinstance(artifacts, list):
        raise ValueError("Entity purge recovery plan is invalid.")

    artifacts_by_root: dict[str, list[PurePosixPath]] = {}
    seen_artifacts: set[tuple[str, str]] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(
            artifact
        ) != ENTITY_PURGE_ARTIFACT_FIELDS:
            raise ValueError("Entity purge recovery plan is invalid.")
        root_name = artifact.get("root")
        relative_value = artifact.get("relative_path")
        if (
            not isinstance(root_name, str)
            or root_name not in ENTITY_PURGE_ROOTS
            or not isinstance(relative_value, str)
        ):
            raise ValueError("Entity purge recovery plan is invalid.")
        relative = validate_entity_relative_path(relative_value)
        identity = (root_name, relative_value)
        if identity in seen_artifacts:
            raise ValueError(
                "Entity purge recovery plan contains duplicate artifacts."
            )
        seen_artifacts.add(identity)
        artifacts_by_root.setdefault(root_name, []).append(relative)

    if entity_type == "video_job":
        if (
            len(artifacts_by_root.get("uploads", [])) != 1
            or len(artifacts_by_root.get("audio", [])) != 1
            or len(artifacts_by_root.get("transcripts", [])) > 1
            or set(artifacts_by_root)
            - {"uploads", "transcripts", "audio"}
        ):
            raise ValueError(
                "Video purge recovery plan has an invalid artifact set."
            )
        upload = artifacts_by_root["uploads"][0]
        audio = artifacts_by_root["audio"][0]
        transcripts = artifacts_by_root.get("transcripts", [])
        if (
            len(upload.parts) != 1
            or upload.stem != entity_id
            or upload.suffix.lower() not in ENTITY_VIDEO_EXTENSIONS
            or audio.parts != (f"{entity_id}.wav",)
            or (
                transcripts
                and transcripts[0].parts != (f"{entity_id}.json",)
            )
        ):
            raise ValueError(
                "Video purge recovery plan is outside the entity namespace."
            )
    else:
        if (
            not isinstance(course_id, str)
            or not course_id
            or any(character in course_id for character in ("/", "\\"))
            or set(artifacts_by_root) != {"sources"}
            or len(artifacts_by_root["sources"]) != 1
        ):
            raise ValueError(
                "Source purge recovery plan has an invalid artifact set."
            )
        source = artifacts_by_root["sources"][0]
        if (
            len(source.parts) != 2
            or source.parts[0] != course_id
            or source.stem != entity_id
            or source.suffix.lower() not in ENTITY_SOURCE_EXTENSIONS
        ):
            raise ValueError(
                "Source purge recovery plan is outside the entity namespace."
            )
    return value


class TrashItem(BaseModel):
    """A recoverable root object hidden from the active workspace."""

    id: str
    entity_type: TrashEntityType
    entity_id: str
    course_id: str | None = None
    display_name: str
    deleted_at: datetime
    purge_after: datetime | None = None
    status: TrashItemStatus = "trashed"
    metadata: dict[str, object] = Field(default_factory=dict)
    restored_at: datetime | None = None
