from datetime import datetime
from sqlite3 import Row

from .course import Course, utc_now
from .db import connect, ensure_db
from .trash_store import put_trash_item, remove_trash_item_for_entity


def _datetime_to_text(value: datetime) -> str:
    return value.isoformat()


def _datetime_from_text(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _row_to_course(row: Row) -> Course:
    keys = set(row.keys())

    return Course(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        created_at=_datetime_from_text(row["created_at"]),
        updated_at=_datetime_from_text(row["updated_at"]),
        job_count=row["job_count"] if "job_count" in keys else 0,
        card_count=row["card_count"] if "card_count" in keys else 0,
    )


def create_course(course: Course) -> None:
    ensure_db()

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO courses (
                id,
                title,
                description,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                course.id,
                course.title,
                course.description,
                _datetime_to_text(course.created_at),
                _datetime_to_text(course.updated_at),
            ),
        )


def get_course(
    course_id: str,
    *,
    include_deleted: bool = False,
) -> Course | None:
    ensure_db()
    deleted_filter = "" if include_deleted else "AND c.deleted_at IS NULL"

    with connect() as conn:
        row = conn.execute(
            f"""
            SELECT
                c.*,
                COUNT(DISTINCT j.id) AS job_count,
                COUNT(k.id) AS card_count
            FROM courses c
            LEFT JOIN jobs j
                ON j.course_id = c.id
                AND j.deleted_at IS NULL
            LEFT JOIN knowledge_cards k
                ON k.job_id = j.id
                AND k.deleted_at IS NULL
            WHERE c.id = ?
              {deleted_filter}
            GROUP BY c.id
            """,
            (course_id,),
        ).fetchone()

    if row is None:
        return None

    return _row_to_course(row)


def list_courses() -> list[Course]:
    ensure_db()

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                c.*,
                COUNT(DISTINCT j.id) AS job_count,
                COUNT(k.id) AS card_count
            FROM courses c
            LEFT JOIN jobs j
                ON j.course_id = c.id
                AND j.deleted_at IS NULL
            LEFT JOIN knowledge_cards k
                ON k.job_id = j.id
                AND k.deleted_at IS NULL
            WHERE c.deleted_at IS NULL
            GROUP BY c.id
            ORDER BY c.updated_at DESC, c.title ASC
            """
        ).fetchall()

    return [_row_to_course(row) for row in rows]


def update_course(course: Course) -> None:
    ensure_db()

    with connect() as conn:
        conn.execute(
            """
            UPDATE courses
            SET title = ?,
                description = ?,
                updated_at = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            (
                course.title,
                course.description,
                _datetime_to_text(course.updated_at),
                course.id,
            ),
        )


def delete_course(course_id: str) -> bool:
    """Hide a course without moving or deleting any of its children."""

    ensure_db()
    deleted_at = utc_now()

    with connect() as conn:
        row = conn.execute(
            """
            SELECT title FROM courses
            WHERE id = ? AND deleted_at IS NULL
            """,
            (course_id,),
        ).fetchone()
        if row is None:
            return False
        cursor = conn.execute(
            """
            UPDATE courses SET deleted_at = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            (_datetime_to_text(deleted_at), course_id),
        )
        if cursor.rowcount != 1:
            return False
        put_trash_item(
            conn,
            entity_type="course",
            entity_id=course_id,
            course_id=course_id,
            display_name=str(row["title"]),
            deleted_at=deleted_at,
        )
    return True


def restore_course(course_id: str) -> bool:
    ensure_db()
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE courses SET deleted_at = NULL, updated_at = ?
            WHERE id = ? AND deleted_at IS NOT NULL
            """,
            (_datetime_to_text(utc_now()), course_id),
        )
        if cursor.rowcount != 1:
            return False
        remove_trash_item_for_entity(
            conn,
            entity_type="course",
            entity_id=course_id,
        )
    return True


def purge_course(
    course_id: str,
    *,
    preserve_course_trash_item: bool = False,
) -> bool:
    ensure_db()
    with connect() as conn:
        cursor = conn.execute(
            """
            DELETE FROM courses
            WHERE id = ? AND deleted_at IS NOT NULL
            """,
            (course_id,),
        )
        if cursor.rowcount != 1:
            return False
        if preserve_course_trash_item:
            conn.execute(
                """
                DELETE FROM trash_items
                WHERE course_id = ?
                  AND NOT (
                      entity_type = 'course'
                      AND entity_id = ?
                  )
                """,
                (course_id, course_id),
            )
        else:
            conn.execute(
                "DELETE FROM trash_items WHERE course_id = ?",
                (course_id,),
            )
    return True
