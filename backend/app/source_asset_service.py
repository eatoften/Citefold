from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from . import course_source_service
from . import course_service
from .job import utc_now
from .settings import get_app_path_settings
from .source_asset import (
    SourceAsset,
    SourceAssetDetail,
    SourceAssetImportResult,
    SourceAssetType,
    SourceUnit,
)
from .source_asset_parser import SourceAssetParseError, parse_source_asset
from .source_asset_store import (
    create_source_asset,
    delete_source_asset,
    get_source_asset,
    list_source_assets_for_course,
    list_source_units_for_asset,
    purge_source_asset as purge_source_asset_record,
    replace_source_units,
    restore_source_asset as restore_source_asset_record,
    update_source_asset,
)


MAX_SOURCE_ASSET_BYTES = 50 * 1024 * 1024
SOURCE_IMPORT_QUEUE_FAILED_MESSAGE = (
    "Source parsing could not be queued. Retry this source."
)
SOURCE_IMPORT_FAILED_MESSAGE = "Source parsing failed. Retry this source."
EXTENSION_TYPES: dict[str, SourceAssetType] = {
    ".pptx": "pptx",
    ".pdf": "pdf",
    ".docx": "docx",
    ".txt": "text",
    ".md": "text",
    ".markdown": "text",
}


class SourceAssetServiceError(Exception):
    pass


class SourceAssetNotFoundError(SourceAssetServiceError):
    pass


class InvalidSourceAssetError(SourceAssetServiceError):
    pass


class SourceAssetExtractionError(SourceAssetServiceError):
    pass


class SourceAssetProcessingCancellationRequested(Exception):
    pass


def import_course_source_asset(
    course_id: str,
    *,
    filename: str | None,
    content_type: str | None,
    content: bytes,
) -> SourceAssetImportResult:
    asset = stage_course_source_asset(
        course_id,
        filename=filename,
        content_type=content_type,
        content=content,
    )
    return process_source_asset(asset.id)


def stage_course_source_asset(
    course_id: str,
    *,
    filename: str | None,
    content_type: str | None,
    content: bytes,
) -> SourceAssetDetail:
    course = course_service.get_video_course(course_id)
    cleaned_name = Path(filename or "").name.strip()
    if not cleaned_name:
        raise InvalidSourceAssetError("Source filename is required.")
    extension = Path(cleaned_name).suffix.lower()
    asset_type = EXTENSION_TYPES.get(extension)
    if asset_type is None:
        raise InvalidSourceAssetError(
            "Supported source files are PPTX, PDF, DOCX, TXT, and Markdown."
        )
    if not content:
        raise InvalidSourceAssetError("Source file is empty.")
    if len(content) > MAX_SOURCE_ASSET_BYTES:
        raise InvalidSourceAssetError("Source file cannot exceed 50 MB.")

    now = utc_now()
    asset_id = uuid4().hex
    source_root = get_app_path_settings().source_dir
    course_dir = source_root / course.id
    course_dir.mkdir(parents=True, exist_ok=True)
    stored_path = course_dir / f"{asset_id}{extension}"
    stored_path.write_bytes(content)
    asset = SourceAsset(
        id=asset_id,
        course_id=course.id,
        asset_type=asset_type,
        original_filename=cleaned_name,
        stored_path=str(stored_path),
        mime_type=content_type,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        extraction_status="pending",
        created_at=now,
        updated_at=now,
    )
    create_source_asset(asset)
    course_source_service.sync_source_asset(asset.id)
    detail = get_source_asset(asset.id)
    if detail is None:
        raise SourceAssetNotFoundError("Staged source asset was not found.")
    return detail


def process_source_asset(
    asset_id: str,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> SourceAssetImportResult:
    asset = get_source_asset(asset_id)
    if asset is None:
        raise SourceAssetNotFoundError("Source asset not found.")
    asset.extraction_status = "pending"
    asset.error_message = None
    asset.updated_at = utc_now()
    update_source_asset(asset)
    course_source_service.sync_source_asset(asset.id)
    path = Path(asset.stored_path)
    try:
        content = path.read_bytes()
    except OSError as exc:
        _mark_source_asset_failed(
            asset,
            "The staged source file could not be read.",
        )
        raise SourceAssetExtractionError(
            "The staged source file could not be read."
        ) from exc
    try:
        _checkpoint(checkpoint)
        units, metadata = parse_source_asset(asset.id, asset.asset_type, content)
        _checkpoint(checkpoint)
        if not units:
            raise SourceAssetParseError(
                "No extractable text was found. Scanned files need OCR, which is not enabled yet."
            )
        replace_source_units(asset.id, units)
        asset.extraction_status = "ready"
        asset.metadata = metadata
        asset.error_message = None
        asset.updated_at = utc_now()
        update_source_asset(asset)
        course_source_service.sync_source_asset(asset.id)
    except SourceAssetProcessingCancellationRequested:
        asset.extraction_status = "pending"
        asset.error_message = "Canceled before source parsing was published."
        asset.updated_at = utc_now()
        update_source_asset(asset)
        course_source_service.sync_source_asset(asset.id)
        raise
    except SourceAssetParseError as exc:
        _mark_source_asset_failed(asset, str(exc))
        raise SourceAssetExtractionError(str(exc)) from exc
    except SourceAssetServiceError:
        raise
    except Exception as exc:
        _mark_source_asset_failed(asset, SOURCE_IMPORT_FAILED_MESSAGE)
        raise SourceAssetExtractionError(
            SOURCE_IMPORT_FAILED_MESSAGE
        ) from exc

    detail = get_source_asset(asset.id)
    if detail is None:
        raise SourceAssetNotFoundError("Imported source asset was not found.")
    return SourceAssetImportResult(asset=detail, units=units)


def mark_source_asset_enqueue_failed(asset_id: str) -> SourceAssetDetail:
    asset = get_source_asset(asset_id)
    if asset is None:
        raise SourceAssetNotFoundError("Source asset not found.")
    _mark_source_asset_failed(asset, SOURCE_IMPORT_QUEUE_FAILED_MESSAGE)
    failed = get_source_asset(asset.id)
    if failed is None:
        raise SourceAssetNotFoundError("Source asset not found.")
    return failed


def prepare_source_asset_retry(asset_id: str) -> SourceAssetDetail:
    asset = get_source_asset(asset_id)
    if asset is None:
        raise SourceAssetNotFoundError("Source asset not found.")
    if asset.extraction_status == "ready":
        raise InvalidSourceAssetError("This source is already ready.")
    asset.extraction_status = "pending"
    asset.error_message = None
    asset.updated_at = utc_now()
    update_source_asset(asset)
    course_source_service.sync_source_asset(asset.id)
    pending = get_source_asset(asset.id)
    if pending is None:
        raise SourceAssetNotFoundError("Source asset not found.")
    return pending


def _mark_source_asset_failed(
    asset: SourceAsset,
    message: str,
) -> None:
    asset.extraction_status = "failed"
    asset.error_message = message
    asset.updated_at = utc_now()
    update_source_asset(asset)
    course_source_service.sync_source_asset(asset.id)


def _checkpoint(checkpoint: Callable[[], None] | None) -> None:
    if checkpoint is not None:
        checkpoint()


def list_course_source_assets(course_id: str) -> list[SourceAssetDetail]:
    course = course_service.get_video_course(course_id)
    return list_source_assets_for_course(course.id)


def list_source_asset_units(asset_id: str) -> list[SourceUnit]:
    if get_source_asset(asset_id) is None:
        raise SourceAssetNotFoundError("Source asset not found.")
    return list_source_units_for_asset(asset_id)


def remove_source_asset(asset_id: str) -> None:
    asset = get_source_asset(asset_id)
    if asset is None:
        raise SourceAssetNotFoundError("Source asset not found.")
    delete_source_asset(asset.id)


def restore_deleted_source_asset(asset_id: str) -> SourceAssetDetail:
    if not restore_source_asset_record(asset_id):
        raise SourceAssetNotFoundError(
            "Deleted source asset not found or its course is still in trash."
        )
    course_source_service.sync_source_asset(asset_id)
    restored = get_source_asset(asset_id)
    if restored is None:
        raise SourceAssetNotFoundError("Restored source asset was not found.")
    return restored


def purge_deleted_source_asset(
    asset_id: str,
    *,
    allow_parent_deleted: bool = False,
) -> None:
    asset = get_source_asset(asset_id, include_deleted=True)
    if asset is None:
        raise SourceAssetNotFoundError("Deleted source asset not found.")
    purge_source_asset_records(
        asset.id,
        allow_parent_deleted=allow_parent_deleted,
    )
    course_source_service.remove_asset_source(asset.id)
    _unlink_source_artifact(
        Path(asset.stored_path),
        get_app_path_settings().source_dir,
    )


def purge_source_asset_records(
    asset_id: str,
    *,
    allow_parent_deleted: bool = False,
    preserve_trash_item: bool = False,
    allow_missing: bool = False,
) -> None:
    """Delete source rows without touching its projection or managed file."""

    asset = get_source_asset(asset_id, include_deleted=True)
    if asset is None:
        if allow_missing:
            return
        raise SourceAssetNotFoundError("Deleted source asset not found.")
    if not purge_source_asset_record(
        asset.id,
        allow_parent_deleted=allow_parent_deleted,
        preserve_trash_item=preserve_trash_item,
    ):
        raise InvalidSourceAssetError(
            "Only a deleted source asset can be purged."
        )


def _unlink_source_artifact(path: Path, root: Path) -> None:
    try:
        resolved = path.resolve()
        resolved_root = root.resolve()
    except OSError as exc:
        raise SourceAssetServiceError(
            "The managed source file path could not be resolved."
        ) from exc
    if resolved == resolved_root or resolved_root not in resolved.parents:
        raise InvalidSourceAssetError(
            "The managed source file is outside the source directory."
        )
    try:
        resolved.unlink(missing_ok=True)
    except OSError as exc:
        raise SourceAssetServiceError(
            "The managed source file could not be deleted."
        ) from exc
