from datetime import datetime
from pathlib import Path
from sqlite3 import Row

from .course import DEFAULT_COURSE_ID
from .db import connect, ensure_db
from .job import VideoJob, VideoJobStatus, utc_now
from .media_metadata import VideoMetadata
from .trash_store import put_trash_item, remove_trash_item_for_entity


def _metadata_to_json(job: VideoJob) -> str | None:
    if job.metadata is None:
        return None

    return job.metadata.model_dump_json()


def _path_to_text(path: Path | None) -> str | None:
    if path is None:
        return None

    return str(path)


def _datetime_to_text(value: datetime | None) -> str | None:
    if value is None:
        return None

    return value.isoformat()


def _datetime_from_text(
    value: str | None,
    fallback: datetime | None = None,
) -> datetime | None:
    if value is None:
        return fallback

    return datetime.fromisoformat(value)


def _row_to_job(row: Row) -> VideoJob:
    metadata = None
    if row["metadata"] is not None:
        metadata = VideoMetadata.model_validate_json(row["metadata"])

    transcript_path = None
    if row["transcript_path"] is not None:
        transcript_path = Path(row["transcript_path"])

    return VideoJob(
        id=row["id"],
        course_id=row["course_id"] or DEFAULT_COURSE_ID,
        video_path=Path(row["video_path"]),
        status=VideoJobStatus(row["status"]),
        original_filename=row["original_filename"],
        stored_name=row["stored_name"],
        size_bytes=row["size_bytes"],
        metadata=metadata,
        transcript_path=transcript_path,
        error_message=row["error_message"],
        created_at=_datetime_from_text(
            row["created_at"],
            fallback=utc_now(),
        ),
        updated_at=_datetime_from_text(
            row["updated_at"],
            fallback=utc_now(),
        ),
        started_at=_datetime_from_text(row["started_at"]),
        completed_at=_datetime_from_text(row["completed_at"]),
    )


def create_job(
    job: VideoJob,
    *,
    video_sha256: str | None = None,
) -> None:
    ensure_db()

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                id,
                course_id,
                video_path,
                status,
                original_filename,
                stored_name,
                size_bytes,
                metadata,
                transcript_path,
                error_message,
                created_at,
                updated_at,
                started_at,
                completed_at,
                video_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.id,
                job.course_id,
                str(job.video_path),
                job.status.value,
                job.original_filename,
                job.stored_name,
                job.size_bytes,
                _metadata_to_json(job),
                _path_to_text(job.transcript_path),
                job.error_message,
                _datetime_to_text(job.created_at),
                _datetime_to_text(job.updated_at),
                _datetime_to_text(job.started_at),
                _datetime_to_text(job.completed_at),
                video_sha256,
            ),
        )


def get_job(
    job_id: str,
    *,
    include_deleted: bool = False,
) -> VideoJob | None:
    ensure_db()
    deleted_filter = (
        ""
        if include_deleted
        else """
            AND jobs.deleted_at IS NULL
            AND courses.deleted_at IS NULL
        """
    )

    with connect() as conn:
        row = conn.execute(
            f"""
            SELECT jobs.*
            FROM jobs
            INNER JOIN courses ON courses.id = jobs.course_id
            WHERE jobs.id = ?
              {deleted_filter}
            """,
            (job_id,),
        ).fetchone()

    if row is None:
        return None

    return _row_to_job(row)


def get_job_video_sha256(job_id: str) -> str | None:
    ensure_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT jobs.video_sha256
            FROM jobs
            INNER JOIN courses ON courses.id = jobs.course_id
            WHERE jobs.id = ?
              AND jobs.deleted_at IS NULL
              AND courses.deleted_at IS NULL
            """,
            (job_id,),
        ).fetchone()
    if row is None or row["video_sha256"] is None:
        return None
    value = str(row["video_sha256"]).strip().lower()
    return value or None


def list_jobs_missing_video_sha256() -> list[VideoJob]:
    """Return legacy jobs that still lack a persisted upload fingerprint."""

    ensure_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT jobs.*
            FROM jobs
            INNER JOIN courses ON courses.id = jobs.course_id
            WHERE (video_sha256 IS NULL OR trim(video_sha256) = '')
              AND jobs.deleted_at IS NULL
              AND courses.deleted_at IS NULL
            ORDER BY jobs.created_at ASC, jobs.id ASC
            """
        ).fetchall()
    return [_row_to_job(row) for row in rows]


def set_job_video_sha256_if_missing(
    job_id: str,
    sha256: str,
) -> str | None:
    """Persist a legacy fingerprint once without replacing an existing value."""

    ensure_db()
    normalized = sha256.strip().lower()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE jobs
            SET video_sha256 = ?
            WHERE id = ?
              AND (video_sha256 IS NULL OR trim(video_sha256) = '')
            """,
            (normalized, job_id),
        )
        row = conn.execute(
            "SELECT video_sha256 FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    if row is None or row["video_sha256"] is None:
        return None
    value = str(row["video_sha256"]).strip().lower()
    return value or None


def list_jobs(*, include_deleted: bool = False) -> list[VideoJob]:
    ensure_db()
    deleted_filter = (
        ""
        if include_deleted
        else """
            WHERE jobs.deleted_at IS NULL
              AND courses.deleted_at IS NULL
        """
    )

    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT jobs.*
            FROM jobs
            INNER JOIN courses ON courses.id = jobs.course_id
            {deleted_filter}
            ORDER BY jobs.created_at DESC, jobs.id DESC
            """
        ).fetchall()

    return [_row_to_job(row) for row in rows]


def list_jobs_for_course(
    course_id: str,
    *,
    include_deleted: bool = False,
) -> list[VideoJob]:
    ensure_db()
    deleted_filter = (
        ""
        if include_deleted
        else """
            AND jobs.deleted_at IS NULL
            AND courses.deleted_at IS NULL
        """
    )

    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT jobs.*
            FROM jobs
            INNER JOIN courses ON courses.id = jobs.course_id
            WHERE jobs.course_id = ?
              {deleted_filter}
            ORDER BY jobs.created_at DESC, jobs.id DESC
            """,
            (course_id,),
        ).fetchall()

    return [_row_to_job(row) for row in rows]


def update_job(job: VideoJob) -> None:
    ensure_db()

    with connect() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET course_id = ?,
                status = ?,
                original_filename = ?,
                stored_name = ?,
                size_bytes = ?,
                metadata = ?,
                transcript_path = ?,
                error_message = ?,
                created_at = ?,
                updated_at = ?,
                started_at = ?,
                completed_at = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            (
                job.course_id,
                job.status.value,
                job.original_filename,
                job.stored_name,
                job.size_bytes,
                _metadata_to_json(job),
                _path_to_text(job.transcript_path),
                job.error_message,
                _datetime_to_text(job.created_at),
                _datetime_to_text(job.updated_at),
                _datetime_to_text(job.started_at),
                _datetime_to_text(job.completed_at),
                job.id,
            ),
        )


def move_jobs_to_course(
    source_course_id: str,
    target_course_id: str,
) -> None:
    ensure_db()

    with connect() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET course_id = ?
            WHERE course_id = ?
            """,
            (
                target_course_id,
                source_course_id,
            ),
        )


def delete_job(job_id: str) -> bool:
    ensure_db()
    deleted_at = utc_now()

    with connect() as conn:
        row = conn.execute(
            """
            SELECT course_id, original_filename, stored_name
            FROM jobs
            WHERE id = ? AND deleted_at IS NULL
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            return False
        cursor = conn.execute(
            """
            UPDATE jobs SET deleted_at = ?, updated_at = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            (
                _datetime_to_text(deleted_at),
                _datetime_to_text(deleted_at),
                job_id,
            ),
        )
        if cursor.rowcount != 1:
            return False
        display_name = (
            row["original_filename"] or row["stored_name"] or job_id
        )
        put_trash_item(
            conn,
            entity_type="video_job",
            entity_id=job_id,
            course_id=row["course_id"],
            display_name=str(display_name),
            deleted_at=deleted_at,
        )
    return True


def restore_job(job_id: str) -> bool:
    ensure_db()
    now = utc_now()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT jobs.course_id
            FROM jobs
            INNER JOIN courses ON courses.id = jobs.course_id
            WHERE jobs.id = ?
              AND jobs.deleted_at IS NOT NULL
              AND courses.deleted_at IS NULL
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            return False
        cursor = conn.execute(
            """
            UPDATE jobs SET deleted_at = NULL, updated_at = ?
            WHERE id = ? AND deleted_at IS NOT NULL
            """,
            (_datetime_to_text(now), job_id),
        )
        if cursor.rowcount != 1:
            return False
        remove_trash_item_for_entity(
            conn,
            entity_type="video_job",
            entity_id=job_id,
        )
    return True


def purge_job(
    job_id: str,
    *,
    allow_parent_deleted: bool = False,
    preserve_trash_item: bool = False,
) -> bool:
    ensure_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT jobs.deleted_at AS job_deleted_at,
                   courses.deleted_at AS course_deleted_at
            FROM jobs
            INNER JOIN courses ON courses.id = jobs.course_id
            WHERE jobs.id = ?
            """,
            (job_id,),
        ).fetchone()
        if row is None or (
            row["job_deleted_at"] is None
            and not (
                allow_parent_deleted
                and row["course_deleted_at"] is not None
            )
        ):
            return False
        cursor = conn.execute(
            "DELETE FROM jobs WHERE id = ?",
            (job_id,),
        )
        if cursor.rowcount != 1:
            return False
        if not preserve_trash_item:
            remove_trash_item_for_entity(
                conn,
                entity_type="video_job",
                entity_id=job_id,
            )
    return True


def clear_jobs() -> None:
    ensure_db()

    with connect() as conn:
        conn.execute(
            "DELETE FROM trash_items WHERE entity_type = 'video_job'"
        )
        conn.execute("DELETE FROM jobs")


def recover_active_jobs(
    *,
    error_message: str,
) -> list[str]:
    """Turn process-owned legacy states into explicit retryable failures."""

    ensure_db()
    now = utc_now()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT id FROM jobs
            WHERE status IN ('probing', 'extracting_audio', 'transcribing')
              AND deleted_at IS NULL
            ORDER BY created_at, id
            """
        ).fetchall()
        job_ids = [str(row["id"]) for row in rows]
        if job_ids:
            placeholders = ",".join("?" for _ in job_ids)
            conn.execute(
                f"""
                UPDATE jobs
                SET status = 'failed',
                    error_message = ?,
                    updated_at = ?,
                    completed_at = ?
                WHERE id IN ({placeholders})
                """,
                (
                    error_message,
                    now.isoformat(),
                    now.isoformat(),
                    *job_ids,
                ),
            )
    return job_ids
