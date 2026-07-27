from __future__ import annotations

import hashlib
import os
import stat as stat_module
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from pydantic import TypeAdapter, ValidationError

from .citation_target import (
    CitationMediaKind,
    CitationTargetContext,
    CitationTargetResponse,
)
from .citation_target_store import (
    CitationSnapshotRecord,
    get_citation_snapshot_for_course,
)
from .course_source import (
    DocxParagraphLocator,
    NotebookNoteSectionLocator,
    PdfPageLocator,
    PptSlideLocator,
    SourceLocator,
    TextSectionLocator,
    VideoTimeLocator,
)
from .course_store import get_course
from .job import VideoJob
from .job_store import (
    get_job,
    get_job_video_sha256,
    list_jobs_missing_video_sha256,
    set_job_video_sha256_if_missing,
)
from .notebook_note_source import build_note_source_projection
from .notebook_note_store import get_note_source_snapshot, get_notebook_note
from .settings import get_app_path_settings
from .source_asset import SourceAssetDetail
from .source_asset_store import get_source_asset


SOURCE_LOCATOR_ADAPTER = TypeAdapter(SourceLocator)
VIDEO_MIME_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
}
AUDIO_MIME_TYPES = {
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
}
ASSET_MIME_TYPES = {
    "pdf": "application/pdf",
    "pptx": (
        "application/vnd.openxmlformats-officedocument."
        "presentationml.presentation"
    ),
    "docx": (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"
    ),
    "text": "text/plain; charset=utf-8",
}
ASSET_EXTENSIONS = {
    "pdf": {".pdf"},
    "pptx": {".pptx"},
    "docx": {".docx"},
    "text": {".md", ".markdown", ".txt"},
}


class CitationTargetServiceError(RuntimeError):
    pass


class CitationTargetNotFoundError(CitationTargetServiceError):
    pass


class CitationContentUnavailableError(CitationTargetServiceError):
    def __init__(
        self,
        reason: str,
        message: str,
        *,
        integrity_conflict: bool = False,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.integrity_conflict = integrity_conflict


@dataclass
class ManagedCitationFile:
    path: Path
    filename: str
    mime_type: str
    media_kind: CitationMediaKind
    size_bytes: int
    sha256: str
    handle: BinaryIO | None = None

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None


@dataclass(frozen=True)
class LegacyVideoFingerprintFailure:
    job_id: str
    reason: str


@dataclass(frozen=True)
class LegacyVideoFingerprintBackfillReport:
    scanned: int
    backfilled: int
    refused: int
    failures: tuple[LegacyVideoFingerprintFailure, ...]


@dataclass(frozen=True)
class CitationResolution:
    target: CitationTargetResponse
    managed_file: ManagedCitationFile | None


def backfill_legacy_video_fingerprints(
) -> LegacyVideoFingerprintBackfillReport:
    """Fingerprint legacy uploads before Chat and citation APIs are served.

    This is the only trust-on-first-use boundary for videos created before
    upload-time hashing existed.  A job that cannot prove its managed path,
    immutable storage name, recorded size, and stable readable contents stays
    unverified instead of being fingerprinted by a later citation request.
    """

    jobs = list_jobs_missing_video_sha256()
    failures: list[LegacyVideoFingerprintFailure] = []
    backfilled = 0
    paths = get_app_path_settings()
    for job in jobs:
        managed_file: ManagedCitationFile | None = None
        try:
            managed_file = _open_job_file(
                job,
                upload_root=paths.upload_dir,
                media_kind="video",
                keep_open=True,
                require_fingerprint=False,
                repeat_hash=True,
            )
            actual_hash = managed_file.sha256
            persisted_hash = set_job_video_sha256_if_missing(
                job.id,
                actual_hash,
            )
            if persisted_hash != actual_hash:
                raise CitationContentUnavailableError(
                    "fingerprint_conflict",
                    "The legacy video fingerprint changed during startup.",
                    integrity_conflict=True,
                )
            backfilled += 1
        except CitationContentUnavailableError as exc:
            failures.append(
                LegacyVideoFingerprintFailure(
                    job_id=job.id,
                    reason=exc.reason,
                )
            )
        except OSError:
            failures.append(
                LegacyVideoFingerprintFailure(
                    job_id=job.id,
                    reason="file_lifecycle_error",
                )
            )
        finally:
            if managed_file is not None:
                managed_file.close()
    return LegacyVideoFingerprintBackfillReport(
        scanned=len(jobs),
        backfilled=backfilled,
        refused=len(failures),
        failures=tuple(failures),
    )


def resolve_citation_target(
    course_id: str,
    citation_id: str,
    *,
    media_url: str | None = None,
) -> CitationTargetResponse:
    resolution = _resolve(course_id, citation_id, keep_open=False)
    if resolution.managed_file is None:
        return resolution.target
    return resolution.target.model_copy(
        update={
            "media_url": media_url,
            "mime_type": resolution.managed_file.mime_type,
        }
    )


def resolve_citation_content(
    course_id: str,
    citation_id: str,
) -> ManagedCitationFile:
    resolution = _resolve(course_id, citation_id, keep_open=True)
    if resolution.managed_file is not None:
        return resolution.managed_file
    raise CitationContentUnavailableError(
        resolution.target.reason or "source_unavailable",
        resolution.target.reason_message
        or "The cited source file is no longer available.",
        integrity_conflict=resolution.target.reason
        in {
            "file_changed_during_validation",
            "file_lifecycle_error",
            "file_integrity_mismatch",
            "source_changed",
        },
    )


def _resolve(
    course_id: str,
    citation_id: str,
    *,
    keep_open: bool,
) -> CitationResolution:
    if get_course(course_id) is None:
        raise CitationTargetNotFoundError("Citation not found.")
    record = get_citation_snapshot_for_course(course_id, citation_id)
    if record is None:
        raise CitationTargetNotFoundError("Citation not found.")

    try:
        locator = SOURCE_LOCATOR_ADAPTER.validate_python(record.locator)
    except ValidationError:
        return _snapshot_only(
            record,
            reason="unsupported_locator",
            message="This historical citation uses an unsupported locator.",
        )

    if isinstance(locator, NotebookNoteSectionLocator):
        return _resolve_notebook_note_snapshot(
            record,
            course_id=course_id,
            locator=locator,
        )

    source_problem = _canonical_source_problem(
        record,
        course_id=course_id,
        locator=locator,
    )
    if source_problem is not None:
        return _snapshot_only(
            record,
            reason=source_problem[0],
            message=source_problem[1],
            media_kind=_media_kind(record.source_type, locator),
        )

    context = [
        CitationTargetContext(
            chunk_id=item.chunk_id,
            ordinal=item.ordinal,
            text=item.text,
            locator=item.locator,
            is_target=item.chunk_id == record.chunk_id,
        )
        for item in record.context
    ]
    media_kind = _media_kind(record.source_type, locator)
    try:
        managed_file = _resolve_managed_file(
            record,
            course_id=course_id,
            locator=locator,
            media_kind=media_kind,
            keep_open=keep_open,
        )
    except CitationContentUnavailableError as exc:
        return _snapshot_only(
            record,
            reason=exc.reason,
            message=str(exc),
            media_kind=media_kind,
            target_chunk_id=record.chunk_id,
            context=context,
        )

    try:
        target = CitationTargetResponse(
            citation_id=record.citation_id,
            availability="available",
            source_id=record.source_id,
            source_title=record.source_title,
            source_type=record.source_type,
            quote=record.quote,
            locator=record.locator,
            media_kind=managed_file.media_kind,
            mime_type=managed_file.mime_type,
            target_chunk_id=record.chunk_id,
            context=context,
        )
    except Exception:
        managed_file.close()
        raise
    return CitationResolution(target=target, managed_file=managed_file)


def _resolve_notebook_note_snapshot(
    record: CitationSnapshotRecord,
    *,
    course_id: str,
    locator: NotebookNoteSectionLocator,
) -> CitationResolution:
    """Resolve historical note evidence from its immutable source snapshot."""

    if record.source_course_id is None:
        return _snapshot_only(
            record,
            reason="source_removed",
            message=(
                "The cited note Source was removed. "
                "The saved quotation remains available."
            ),
            media_kind="text",
        )
    if record.source_course_id != course_id:
        return _snapshot_only(
            record,
            reason="source_moved",
            message="The cited note Source is no longer part of this course.",
            media_kind="text",
        )
    if (
        record.source_origin_type != "notebook_note"
        or record.source_origin_id != locator.note_id
        or record.current_source_type != record.source_type
    ):
        return _snapshot_only(
            record,
            reason="source_changed",
            message="The cited note Source changed after this answer.",
            media_kind="text",
        )

    note = get_notebook_note(course_id, locator.note_id)
    snapshot = get_note_source_snapshot(
        course_id,
        locator.note_id,
        locator.snapshot_id,
        require_active_note=True,
    )
    if (
        note is None
        or snapshot is None
        or hashlib.sha256(
            snapshot.body_markdown.encode("utf-8")
        ).hexdigest()
        != snapshot.content_hash
    ):
        return _snapshot_only(
            record,
            reason="source_changed",
            message="The cited note or source snapshot is no longer active.",
            media_kind="text",
        )
    try:
        historical_source, historical_chunks = build_note_source_projection(
            note,
            snapshot,
        )
    except ValueError:
        return _snapshot_only(
            record,
            reason="source_changed",
            message="The cited note snapshot can no longer be reconstructed.",
            media_kind="text",
        )

    target_chunk = next(
        (
            chunk
            for chunk in historical_chunks
            if chunk.locator.section_number == locator.section_number
        ),
        None,
    )
    target_locator = (
        target_chunk.locator.model_dump(mode="json")
        if target_chunk is not None
        else None
    )
    if (
        historical_source.id != record.source_id
        or historical_source.source_type != record.source_type
        or historical_source.title != record.source_title
        or target_chunk is None
        or target_chunk.id != record.chunk_id
        or target_chunk.text_hash != record.chunk_text_hash
        or target_locator != record.locator
        or record.quote not in target_chunk.text
    ):
        return _snapshot_only(
            record,
            reason="source_changed",
            message="The cited note snapshot no longer matches its evidence.",
            media_kind="text",
        )

    context = [
        CitationTargetContext(
            chunk_id=chunk.id,
            ordinal=chunk.ordinal,
            text=chunk.text,
            locator=chunk.locator.model_dump(mode="json"),
            is_target=chunk.id == target_chunk.id,
        )
        for chunk in historical_chunks
        if abs(chunk.ordinal - target_chunk.ordinal) <= 1
    ]
    return CitationResolution(
        target=CitationTargetResponse(
            citation_id=record.citation_id,
            availability="available",
            source_id=record.source_id,
            source_title=record.source_title,
            source_type=record.source_type,
            quote=record.quote,
            locator=record.locator,
            media_kind="text",
            target_chunk_id=record.chunk_id,
            context=context,
        ),
        managed_file=None,
    )


def _canonical_source_problem(
    record: CitationSnapshotRecord,
    *,
    course_id: str,
    locator: SourceLocator,
) -> tuple[str, str] | None:
    if record.source_course_id is None:
        return (
            "source_removed",
            "The cited source was removed. The saved quotation remains available.",
        )
    if record.source_course_id != course_id:
        return (
            "source_moved",
            "The cited source is no longer part of this course.",
        )
    if (
        record.current_source_type != record.source_type
        or record.current_chunk_text is None
        or record.current_chunk_text_hash is None
        or record.current_chunk_locator is None
        or record.current_chunk_ordinal is None
        or not record.current_chunk_active
        or record.current_chunk_text_hash != record.chunk_text_hash
        or record.quote not in record.current_chunk_text
        or record.current_chunk_locator != record.locator
    ):
        return (
            "source_changed",
            "The cited source changed after this answer was created.",
        )
    if not _locator_matches_source(record, locator):
        return (
            "source_changed",
            "The cited locator no longer matches its canonical source.",
        )
    return None


def _locator_matches_source(
    record: CitationSnapshotRecord,
    locator: SourceLocator,
) -> bool:
    if record.source_origin_type == "video_job":
        return (
            isinstance(locator, VideoTimeLocator)
            and locator.job_id == record.source_origin_id
            and locator.asset_id is None
        )
    if record.source_origin_type == "notebook_note":
        return (
            isinstance(locator, NotebookNoteSectionLocator)
            and locator.note_id == record.source_origin_id
            and record.source_course_id is not None
            and get_note_source_snapshot(
                record.source_course_id,
                locator.note_id,
                locator.snapshot_id,
                require_active_note=True,
            )
            is not None
        )
    if record.source_origin_type != "source_asset":
        return False
    if isinstance(locator, VideoTimeLocator):
        if locator.asset_id is not None:
            return locator.asset_id == record.source_origin_id
        asset = get_source_asset(record.source_origin_id or "")
        return (
            asset is not None
            and asset.job_id is not None
            and locator.job_id == asset.job_id
        )
    return getattr(locator, "asset_id", None) == record.source_origin_id


def _resolve_managed_file(
    record: CitationSnapshotRecord,
    *,
    course_id: str,
    locator: SourceLocator,
    media_kind: CitationMediaKind | None,
    keep_open: bool,
) -> ManagedCitationFile:
    if media_kind is None:
        raise CitationContentUnavailableError(
            "unsupported_locator",
            "This citation does not have a supported source target.",
        )
    if isinstance(locator, VideoTimeLocator) and locator.job_id is not None:
        if record.source_origin_type == "source_asset":
            linked_asset = get_source_asset(record.source_origin_id or "")
            if (
                linked_asset is None
                or linked_asset.course_id != course_id
                or linked_asset.job_id != locator.job_id
            ):
                raise CitationContentUnavailableError(
                    "source_changed",
                    "The cited media link no longer matches its source.",
                    integrity_conflict=True,
                )
        return _resolve_job_file(
            locator.job_id,
            course_id=course_id,
            media_kind=media_kind,
            keep_open=keep_open,
        )

    asset_id = getattr(locator, "asset_id", None)
    if not isinstance(asset_id, str) or not asset_id:
        raise CitationContentUnavailableError(
            "media_unavailable",
            "The cited source has no resolvable media owner.",
        )
    return _resolve_asset_file(
        asset_id,
        course_id=course_id,
        media_kind=media_kind,
        keep_open=keep_open,
    )


def _resolve_job_file(
    job_id: str,
    *,
    course_id: str,
    media_kind: CitationMediaKind,
    keep_open: bool,
) -> ManagedCitationFile:
    job = get_job(job_id)
    if job is None:
        raise CitationContentUnavailableError(
            "owner_deleted",
            "The cited video was deleted.",
        )
    if job.course_id != course_id:
        raise CitationContentUnavailableError(
            "source_moved",
            "The cited video is no longer part of this course.",
        )
    return _open_job_file(
        job,
        upload_root=get_app_path_settings().upload_dir,
        media_kind=media_kind,
        keep_open=keep_open,
        require_fingerprint=True,
        repeat_hash=False,
    )


def _open_job_file(
    job: VideoJob,
    *,
    upload_root: Path,
    media_kind: CitationMediaKind,
    keep_open: bool,
    require_fingerprint: bool,
    repeat_hash: bool,
) -> ManagedCitationFile:
    expected_hash = get_job_video_sha256(job.id)
    if require_fingerprint and expected_hash is None:
        raise CitationContentUnavailableError(
            "legacy_fingerprint_unverified",
            (
                "This legacy video has not passed startup fingerprint "
                "verification. The saved quotation remains available."
            ),
        )
    if expected_hash is not None and not _is_sha256(expected_hash):
        raise CitationContentUnavailableError(
            "file_integrity_mismatch",
            "The cited video has an invalid stored fingerprint.",
            integrity_conflict=True,
        )

    path = _resolve_job_path(job, upload_root)
    opened: BinaryIO | None = None
    try:
        opened = _open_regular_file(path)
        opened_stat = os.fstat(opened.fileno())
        if job.size_bytes is None or opened_stat.st_size != job.size_bytes:
            raise CitationContentUnavailableError(
                "file_integrity_mismatch",
                "The cited video file no longer matches the uploaded video.",
                integrity_conflict=True,
            )
        actual_hash = _hash_open_file(opened, repeat=repeat_hash)
        if expected_hash is not None and expected_hash != actual_hash:
            raise CitationContentUnavailableError(
                "file_integrity_mismatch",
                "The cited video file no longer matches the uploaded video.",
                integrity_conflict=True,
            )
        result = ManagedCitationFile(
            path=path,
            filename=job.original_filename or path.name,
            mime_type=_media_mime(path, media_kind),
            media_kind=media_kind,
            size_bytes=opened_stat.st_size,
            sha256=actual_hash,
            handle=opened if keep_open else None,
        )
        if not keep_open:
            opened.close()
        return result
    except CitationContentUnavailableError:
        if opened is not None:
            opened.close()
        raise
    except OSError as exc:
        if opened is not None:
            opened.close()
        raise CitationContentUnavailableError(
            "file_lifecycle_error",
            "The cited video changed while it was being opened.",
            integrity_conflict=True,
        ) from exc


def _resolve_job_path(job: VideoJob, upload_root: Path) -> Path:
    stored_name = job.stored_name
    if (
        not stored_name
        or Path(stored_name).name != stored_name
        or Path(stored_name).stem != job.id
        or Path(stored_name).suffix.lower() not in VIDEO_MIME_TYPES
    ):
        raise CitationContentUnavailableError(
            "file_integrity_mismatch",
            "The cited video file no longer matches its storage identity.",
            integrity_conflict=True,
        )
    candidates: list[Path | None] = []
    if job.video_path.name == stored_name:
        candidates.append(job.video_path)
    candidates.append(
        _rebased_job_path(job.id, stored_name, upload_root)
    )
    path = _first_managed_file(candidates, root=upload_root)
    if path is None:
        raise CitationContentUnavailableError(
            "file_missing",
            "The cited video file is missing or outside managed storage.",
        )
    if path.name != stored_name:
        raise CitationContentUnavailableError(
            "file_integrity_mismatch",
            "The cited video file no longer matches its storage identity.",
            integrity_conflict=True,
        )
    return path


def _resolve_asset_file(
    asset_id: str,
    *,
    course_id: str,
    media_kind: CitationMediaKind,
    keep_open: bool,
) -> ManagedCitationFile:
    asset = get_source_asset(asset_id)
    if asset is None:
        raise CitationContentUnavailableError(
            "owner_deleted",
            "The cited source file was deleted.",
        )
    if asset.course_id != course_id:
        raise CitationContentUnavailableError(
            "source_moved",
            "The cited source file is no longer part of this course.",
        )
    paths = get_app_path_settings()
    candidates = [Path(asset.stored_path)]
    candidates.extend(_relocated_asset_candidates(asset, paths.source_dir))
    path = _first_managed_file(candidates, root=paths.source_dir)
    if path is None:
        reason = (
            "asset_media_unavailable"
            if isinstance(media_kind, str) and media_kind in {"video", "audio"}
            else "file_missing"
        )
        raise CitationContentUnavailableError(
            reason,
            "The cited source file is missing or outside managed storage.",
        )
    expected_extensions = ASSET_EXTENSIONS.get(asset.asset_type)
    if (
        path.stem != asset.id
        or (
            expected_extensions is not None
            and path.suffix.lower() not in expected_extensions
        )
    ):
        raise CitationContentUnavailableError(
            "file_integrity_mismatch",
            "The cited source file no longer matches its storage identity.",
            integrity_conflict=True,
        )
    expected_hash = asset.sha256.strip().lower()
    if not _is_sha256(expected_hash):
        raise CitationContentUnavailableError(
            "file_integrity_mismatch",
            "The cited source has an invalid stored fingerprint.",
            integrity_conflict=True,
        )
    opened: BinaryIO | None = None
    try:
        opened = _open_regular_file(path)
        opened_stat = os.fstat(opened.fileno())
        actual_hash = _hash_open_file(opened)
        if (
            opened_stat.st_size != asset.size_bytes
            or actual_hash != expected_hash
        ):
            raise CitationContentUnavailableError(
                "file_integrity_mismatch",
                "The cited source file no longer matches the imported source.",
                integrity_conflict=True,
            )
        result = ManagedCitationFile(
            path=path,
            filename=asset.original_filename,
            mime_type=_asset_mime(asset, path, media_kind),
            media_kind=media_kind,
            size_bytes=opened_stat.st_size,
            sha256=actual_hash,
            handle=opened if keep_open else None,
        )
        if not keep_open:
            opened.close()
        return result
    except CitationContentUnavailableError:
        if opened is not None:
            opened.close()
        raise
    except OSError as exc:
        if opened is not None:
            opened.close()
        raise CitationContentUnavailableError(
            "file_lifecycle_error",
            "The cited source changed while it was being opened.",
            integrity_conflict=True,
        ) from exc


def _snapshot_only(
    record: CitationSnapshotRecord,
    *,
    reason: str,
    message: str,
    media_kind: CitationMediaKind | None = None,
    target_chunk_id: str | None = None,
    context: list[CitationTargetContext] | None = None,
) -> CitationResolution:
    return CitationResolution(
        target=CitationTargetResponse(
            citation_id=record.citation_id,
            availability="snapshot_only",
            reason=reason,
            reason_message=message,
            source_id=record.source_id,
            source_title=record.source_title,
            source_type=record.source_type,
            quote=record.quote,
            locator=record.locator,
            media_kind=media_kind,
            target_chunk_id=target_chunk_id,
            context=context or [],
        ),
        managed_file=None,
    )


def _media_kind(
    source_type: str,
    locator: SourceLocator,
) -> CitationMediaKind | None:
    if isinstance(locator, VideoTimeLocator):
        return "audio" if source_type == "audio" else "video"
    if isinstance(locator, PdfPageLocator):
        return "pdf"
    if isinstance(locator, (PptSlideLocator, DocxParagraphLocator)):
        return "document"
    if isinstance(locator, TextSectionLocator):
        return "text"
    if isinstance(locator, NotebookNoteSectionLocator):
        return "text"
    return None


def _first_managed_file(
    candidates: list[Path | None],
    *,
    root: Path,
) -> Path | None:
    try:
        resolved_root = root.resolve(strict=True)
    except OSError:
        return None
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if (
            resolved != resolved_root
            and resolved.is_relative_to(resolved_root)
            and resolved.is_file()
        ):
            return resolved
    return None


def _rebased_job_path(
    job_id: str,
    stored_name: str | None,
    root: Path,
) -> Path | None:
    if not stored_name or Path(stored_name).name != stored_name:
        return None
    suffix = Path(stored_name).suffix.lower()
    if (
        suffix not in VIDEO_MIME_TYPES
        or Path(stored_name).stem != job_id
    ):
        return None
    return root / stored_name


def _relocated_asset_candidates(
    asset: SourceAssetDetail,
    source_root: Path,
) -> list[Path]:
    suffix = (
        Path(asset.stored_path).suffix.lower()
        or Path(asset.original_filename).suffix.lower()
    )
    if not suffix or any(char in suffix for char in ("/", "\\")):
        return []
    expected_name = f"{asset.id}{suffix}"
    try:
        if not source_root.is_dir():
            return []
        candidates = [
            candidate
            for candidate in source_root.glob(f"*/{expected_name}")
            if candidate.is_file()
        ]
    except OSError:
        return []
    return candidates if len(candidates) == 1 else []


def _media_mime(path: Path, media_kind: CitationMediaKind) -> str:
    suffix = path.suffix.lower()
    mapping = AUDIO_MIME_TYPES if media_kind == "audio" else VIDEO_MIME_TYPES
    mime_type = mapping.get(suffix)
    if mime_type is None:
        raise CitationContentUnavailableError(
            "unsupported_media_type",
            "The cited media format is not supported for preview.",
        )
    return mime_type


def _asset_mime(
    asset: SourceAssetDetail,
    path: Path,
    media_kind: CitationMediaKind,
) -> str:
    if media_kind in {"video", "audio"}:
        return _media_mime(path, media_kind)
    mime_type = ASSET_MIME_TYPES.get(asset.asset_type)
    if mime_type is None:
        raise CitationContentUnavailableError(
            "unsupported_media_type",
            "The cited source format is not supported for preview.",
        )
    return mime_type


def _open_binary_no_follow(path: Path) -> BinaryIO:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        return os.fdopen(descriptor, "rb", closefd=True)
    except Exception:
        os.close(descriptor)
        raise


def _open_regular_file(path: Path) -> BinaryIO:
    handle = _open_binary_no_follow(path)
    try:
        opened_stat = os.fstat(handle.fileno())
        if not stat_module.S_ISREG(opened_stat.st_mode):
            raise CitationContentUnavailableError(
                "file_integrity_mismatch",
                "The cited source is not a regular managed file.",
                integrity_conflict=True,
            )
        current_stat = os.stat(path, follow_symlinks=False)
        if not os.path.samestat(opened_stat, current_stat):
            raise CitationContentUnavailableError(
                "file_changed_during_validation",
                "The cited source changed while it was being opened.",
                integrity_conflict=True,
            )
        return handle
    except Exception:
        handle.close()
        raise


def _hash_open_file(
    handle: BinaryIO,
    *,
    repeat: bool = False,
) -> str:
    before = os.fstat(handle.fileno())
    first = _sha256_pass(handle)
    after = os.fstat(handle.fileno())
    if _stat_signature(before) != _stat_signature(after):
        raise CitationContentUnavailableError(
            "file_changed_during_validation",
            "The cited source changed while it was being validated.",
            integrity_conflict=True,
        )
    if repeat:
        second = _sha256_pass(handle)
        final_stat = os.fstat(handle.fileno())
        if (
            first != second
            or _stat_signature(after) != _stat_signature(final_stat)
        ):
            raise CitationContentUnavailableError(
                "file_changed_during_validation",
                "The cited source changed while it was being validated.",
                integrity_conflict=True,
            )
    handle.seek(0)
    return first


def _sha256_pass(handle: BinaryIO) -> str:
    handle.seek(0)
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _stat_signature(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        value.st_dev,
        value.st_ino,
    )


def _is_sha256(value: str) -> bool:
    return (
        len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
