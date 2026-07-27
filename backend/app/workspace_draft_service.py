from __future__ import annotations

from . import course_service
from .workspace_draft import WorkspaceDraft, WorkspaceDraftPut
from .workspace_draft_store import (
    DraftRevisionConflictError,
    delete_draft,
    get_draft,
    list_drafts,
    put_draft,
)


MAX_DRAFT_PAYLOAD_BYTES = 2 * 1024 * 1024


class WorkspaceDraftServiceError(Exception):
    pass


class WorkspaceDraftNotFoundError(WorkspaceDraftServiceError):
    pass


class WorkspaceDraftConflictError(WorkspaceDraftServiceError):
    def __init__(self, current: WorkspaceDraft | None) -> None:
        super().__init__("This draft was updated by another editor.")
        self.current = current


class WorkspaceDraftTooLargeError(WorkspaceDraftServiceError):
    pass


def save_workspace_draft(
    draft_id: str,
    request: WorkspaceDraftPut,
) -> WorkspaceDraft:
    cleaned_id = draft_id.strip()
    if not cleaned_id or len(cleaned_id) > 240:
        raise WorkspaceDraftServiceError("Draft id is invalid.")
    course_service.get_video_course(request.course_id)
    payload_size = len(request.model_dump_json().encode("utf-8"))
    if payload_size > MAX_DRAFT_PAYLOAD_BYTES:
        raise WorkspaceDraftTooLargeError(
            "Draft payload cannot exceed 2 MB."
        )
    try:
        return put_draft(cleaned_id, request)
    except DraftRevisionConflictError as exc:
        raise WorkspaceDraftConflictError(exc.current) from exc


def get_workspace_draft(draft_id: str) -> WorkspaceDraft:
    draft = get_draft(draft_id)
    if draft is None:
        raise WorkspaceDraftNotFoundError("Draft not found.")
    course_service.get_video_course(draft.course_id)
    return draft


def list_workspace_drafts(
    *,
    course_id: str | None = None,
    draft_type: str | None = None,
) -> list[WorkspaceDraft]:
    if course_id is not None:
        course_service.get_video_course(course_id)
    cleaned_type = draft_type.strip() if draft_type else None
    return list_drafts(course_id=course_id, draft_type=cleaned_type)


def remove_workspace_draft(
    draft_id: str,
    *,
    expected_revision: int | None = None,
) -> None:
    try:
        deleted = delete_draft(
            draft_id,
            expected_revision=expected_revision,
        )
    except DraftRevisionConflictError as exc:
        raise WorkspaceDraftConflictError(exc.current) from exc
    if not deleted:
        raise WorkspaceDraftNotFoundError("Draft not found.")
