from uuid import uuid4

from .course import (
    DEFAULT_COURSE_ID,
    Course,
    CourseCreate,
    CourseUpdate,
    utc_now,
)
from .course_store import (
    create_course,
    delete_course,
    get_course,
    list_courses,
    purge_course,
    restore_course,
    update_course,
)
from .job import VideoJob
from .job_store import list_jobs_for_course
from .knowledge_card import KnowledgeCard, KnowledgeCardIndexItem
from .knowledge_card_store import (
    delete_cards_for_course,
    list_card_index_for_course,
    list_cards_for_course,
)


class CourseServiceError(Exception):
    pass


class CourseNotFoundError(CourseServiceError):
    pass


class InvalidCourseError(CourseServiceError):
    pass


class DefaultCourseDeleteError(CourseServiceError):
    pass


def list_video_courses() -> list[Course]:
    return list_courses()


def get_video_course(course_id: str) -> Course:
    course = get_course(course_id)

    if course is None:
        raise CourseNotFoundError("Course not found.")

    return course


def create_video_course(request: CourseCreate) -> Course:
    title = request.title.strip()

    if not title:
        raise InvalidCourseError("Course title is required.")

    now = utc_now()
    course = Course(
        id=uuid4().hex,
        title=title,
        description=_clean_optional_text(request.description),
        created_at=now,
        updated_at=now,
    )

    create_course(course)

    return course


def update_video_course(
    course_id: str,
    request: CourseUpdate,
) -> Course:
    course = get_video_course(course_id)
    update_data = request.model_dump(exclude_unset=True)

    if "title" in update_data and request.title is not None:
        title = request.title.strip()

        if not title:
            raise InvalidCourseError("Course title is required.")

        course.title = title

    if "description" in update_data:
        course.description = _clean_optional_text(request.description)

    course.updated_at = utc_now()
    update_course(course)

    return get_video_course(course.id)


def delete_video_course(course_id: str) -> None:
    course = get_video_course(course_id)

    if course.id == DEFAULT_COURSE_ID:
        raise DefaultCourseDeleteError("Default course cannot be deleted.")

    delete_course(course.id)


def restore_video_course(course_id: str) -> Course:
    if not restore_course(course_id):
        raise CourseNotFoundError("Deleted course not found.")
    from . import course_source_service

    course_source_service.reconcile_course_sources(course_id)
    return get_video_course(course_id)


def purge_deleted_video_course(
    course_id: str,
    *,
    preserve_course_trash_item: bool = False,
) -> None:
    """Purge course metadata after its dependent records have been purged."""

    if course_id == DEFAULT_COURSE_ID:
        raise DefaultCourseDeleteError("Default course cannot be deleted.")
    if not purge_course(
        course_id,
        preserve_course_trash_item=preserve_course_trash_item,
    ):
        raise CourseNotFoundError("Deleted course not found.")


def list_course_jobs(course_id: str) -> list[VideoJob]:
    course = get_video_course(course_id)

    return list_jobs_for_course(course.id)


def list_course_cards(course_id: str) -> list[KnowledgeCard]:
    course = get_video_course(course_id)

    return list_cards_for_course(course.id)


def list_course_card_index(course_id: str) -> list[KnowledgeCardIndexItem]:
    course = get_video_course(course_id)

    return list_card_index_for_course(course.id)


def delete_all_course_cards(course_id: str) -> None:
    course = get_video_course(course_id)

    delete_cards_for_course(course.id)


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None

    stripped = value.strip()

    if not stripped:
        return None

    return stripped
