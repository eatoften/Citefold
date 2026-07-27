from __future__ import annotations

import hashlib
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
    replace_source_units,
    update_source_asset,
)


MAX_SOURCE_ASSET_BYTES = 50 * 1024 * 1024
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


def import_course_source_asset(
    course_id: str,
    *,
    filename: str | None,
    content_type: str | None,
    content: bytes,
) -> SourceAssetImportResult:
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

    try:
        units, metadata = parse_source_asset(asset.id, asset.asset_type, content)
        if not units:
            raise SourceAssetParseError(
                "No extractable text was found. Scanned files need OCR, which is not enabled yet."
            )
        replace_source_units(asset.id, units)
        asset.extraction_status = "ready"
        asset.metadata = metadata
        asset.updated_at = utc_now()
        update_source_asset(asset)
        course_source_service.sync_source_asset(asset.id)
    except SourceAssetParseError as exc:
        asset.extraction_status = "failed"
        asset.error_message = str(exc)
        asset.updated_at = utc_now()
        update_source_asset(asset)
        course_source_service.sync_source_asset(asset.id)
        raise SourceAssetExtractionError(str(exc)) from exc

    detail = get_source_asset(asset.id)
    if detail is None:
        raise SourceAssetNotFoundError("Imported source asset was not found.")
    return SourceAssetImportResult(asset=detail, units=units)


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
    path = Path(asset.stored_path)
    root = get_app_path_settings().source_dir.resolve()
    try:
        resolved = path.resolve()
        if resolved.is_relative_to(root) and resolved.is_file():
            resolved.unlink()
    except OSError:
        pass
    delete_source_asset(asset.id)
    course_source_service.remove_asset_source(asset.id)
