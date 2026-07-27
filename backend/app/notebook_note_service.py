from __future__ import annotations

from uuid import uuid4

from . import course_service
from .job import utc_now
from .notebook_note import (
    FreeNotebookNoteOriginSnapshot,
    NotebookNote,
    NotebookNoteChatCaptureRequest,
    NotebookNoteCreate,
    NotebookNotePromotionRequest,
    NotebookNotePromotionResult,
    NotebookNoteSummary,
    NotebookNoteUpdate,
)
from .notebook_note_store import (
    NotebookNoteCaptureError,
    NotebookNoteDeletedCaptureError,
    NotebookNoteRevisionConflictError,
    NotebookNoteScopeError,
    NotebookNoteStoreError,
    capture_chat_answer,
    create_free_notebook_note,
    get_notebook_note,
    get_notebook_note_course_id,
    list_notebook_notes,
    promote_notebook_note,
    purge_notebook_note,
    purge_notebook_notes_for_course,
    restore_notebook_note,
    soft_delete_notebook_note,
    update_notebook_note,
)


class NotebookNoteServiceError(Exception):
    pass


class NotebookNoteNotFoundError(NotebookNoteServiceError):
    pass


class NotebookNoteConflictError(NotebookNoteServiceError):
    def __init__(self, current: NotebookNote | None) -> None:
        super().__init__("This note was updated by another editor.")
        self.current = current


class InvalidNotebookNoteError(NotebookNoteServiceError):
    pass


class NotebookNoteCaptureConflictError(NotebookNoteServiceError):
    pass


def list_course_notebook_notes(
    course_id: str,
    *,
    limit: int = 1000,
    offset: int = 0,
) -> list[NotebookNoteSummary]:
    _require_course(course_id)
    try:
        return list_notebook_notes(course_id, limit=limit, offset=offset)
    except NotebookNoteScopeError as exc:
        raise NotebookNoteNotFoundError("Course was not found.") from exc


def get_course_notebook_note(
    course_id: str,
    note_id: str,
) -> NotebookNote:
    _require_course(course_id)
    note = get_notebook_note(course_id, note_id)
    if note is None:
        raise NotebookNoteNotFoundError("Note was not found.")
    return note


def create_course_notebook_note(
    course_id: str,
    request: NotebookNoteCreate,
) -> NotebookNote:
    course = _require_course(course_id)
    body = _clean_body(request.body_markdown)
    now = utc_now()
    note = NotebookNote(
        id=uuid4().hex,
        course_id=course.id,
        title=_note_title(request.title, body),
        body_markdown=body,
        revision=1,
        origin_type="free",
        origin_snapshot=FreeNotebookNoteOriginSnapshot(),
        created_at=now,
        updated_at=now,
    )
    try:
        return create_free_notebook_note(note)
    except NotebookNoteStoreError as exc:
        raise InvalidNotebookNoteError(str(exc)) from exc


def capture_chat_answer_as_notebook_note(
    course_id: str,
    message_id: str,
    request: NotebookNoteChatCaptureRequest,
) -> NotebookNote:
    _require_course(course_id)
    try:
        note, _replayed = capture_chat_answer(
            course_id,
            message_id.strip(),
            note_id=uuid4().hex,
            title=(
                _clean_title(request.title)
                if request.title is not None
                else None
            ),
        )
        return note
    except NotebookNoteScopeError as exc:
        raise NotebookNoteNotFoundError("Chat message was not found.") from exc
    except NotebookNoteDeletedCaptureError as exc:
        raise NotebookNoteCaptureConflictError(str(exc)) from exc
    except NotebookNoteCaptureError as exc:
        raise InvalidNotebookNoteError(str(exc)) from exc
    except NotebookNoteStoreError as exc:
        raise InvalidNotebookNoteError(str(exc)) from exc


def update_course_notebook_note(
    course_id: str,
    note_id: str,
    request: NotebookNoteUpdate,
) -> NotebookNote:
    _require_course(course_id)
    update = request.model_dump(exclude_unset=True)
    title = (
        _clean_title(request.title)
        if "title" in update and request.title is not None
        else None
    )
    body = (
        _clean_body(request.body_markdown)
        if "body_markdown" in update
        and request.body_markdown is not None
        else None
    )
    try:
        saved = update_notebook_note(
            course_id,
            note_id,
            expected_revision=request.expected_revision,
            title=title,
            body_markdown=body,
        )
    except NotebookNoteRevisionConflictError as exc:
        raise NotebookNoteConflictError(exc.current) from exc
    if saved is None:
        raise NotebookNoteNotFoundError("Note was not found.")
    return saved


def publish_notebook_note_as_source(
    course_id: str,
    note_id: str,
    request: NotebookNotePromotionRequest,
) -> NotebookNotePromotionResult:
    _require_course(course_id)
    from .course_source_service import source_projection_lifecycle

    try:
        with source_projection_lifecycle():
            result = promote_notebook_note(
                course_id,
                note_id,
                expected_revision=request.expected_revision,
                snapshot_id=uuid4().hex,
            )
    except NotebookNoteRevisionConflictError as exc:
        raise NotebookNoteConflictError(exc.current) from exc
    except ValueError as exc:
        raise InvalidNotebookNoteError(str(exc)) from exc
    if result is None:
        raise NotebookNoteNotFoundError("Note was not found.")
    note, snapshot, source, replayed = result
    return NotebookNotePromotionResult(
        note=note,
        snapshot=snapshot,
        source=source,
        replayed=replayed,
    )


def delete_course_notebook_note(
    course_id: str,
    note_id: str,
    *,
    expected_revision: int,
) -> None:
    _require_course(course_id)
    try:
        deleted = soft_delete_notebook_note(
            course_id,
            note_id,
            expected_revision=expected_revision,
        )
    except NotebookNoteRevisionConflictError as exc:
        raise NotebookNoteConflictError(exc.current) from exc
    if deleted is None:
        raise NotebookNoteNotFoundError("Note was not found.")


def restore_deleted_notebook_note(note_id: str) -> NotebookNote:
    course_id = get_notebook_note_course_id(note_id)
    if course_id is None:
        raise NotebookNoteNotFoundError("Deleted note was not found.")
    restored = restore_notebook_note(course_id, note_id)
    if restored is None:
        raise NotebookNoteNotFoundError(
            "Deleted note was not found or its course is still in Trash."
        )
    return restored


def purge_deleted_notebook_note(
    note_id: str,
    *,
    allow_parent_deleted: bool = False,
) -> None:
    course_id = get_notebook_note_course_id(note_id)
    if course_id is None:
        raise NotebookNoteNotFoundError("Deleted note was not found.")
    from .course_source_service import source_projection_lifecycle

    with source_projection_lifecycle():
        purged = purge_notebook_note(
            course_id,
            note_id,
            allow_parent_deleted=allow_parent_deleted,
        )
    if not purged:
        raise NotebookNoteNotFoundError("Deleted note was not found.")


def purge_course_notebook_notes(course_id: str) -> None:
    from .course_source_service import source_projection_lifecycle

    with source_projection_lifecycle():
        purge_notebook_notes_for_course(course_id)


def _require_course(course_id: str):
    try:
        return course_service.get_video_course(course_id)
    except course_service.CourseServiceError as exc:
        raise NotebookNoteNotFoundError("Course was not found.") from exc


def _clean_title(value: str) -> str:
    cleaned = " ".join(value.strip().split())
    if not cleaned:
        raise InvalidNotebookNoteError("Note title is required.")
    return cleaned[:200]


def _clean_body(value: str) -> str:
    if not value.strip():
        raise InvalidNotebookNoteError("Note body is required.")
    if len(value.encode("utf-8")) > 2 * 1024 * 1024:
        raise InvalidNotebookNoteError("Note body cannot exceed 2 MB.")
    return value


def _note_title(value: str | None, body: str) -> str:
    if value is not None:
        return _clean_title(value)
    for line in body.splitlines():
        cleaned = " ".join(line.lstrip("#").strip().split())
        if cleaned:
            return cleaned[:120]
    return "Untitled note"
