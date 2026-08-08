"""Strict manifest parsing for public-course benchmark assets.

The manifest is data, not authority: network destinations are restricted by a
hard-coded allowlist and every remotely supplied byte must match two frozen
content identities before it can be published locally.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from urllib.parse import quote, urlsplit


TRUSTED_DOWNLOAD_HOSTS = frozenset({"raw.githubusercontent.com"})
SUPPORTED_PARTITIONS = frozenset(
    {"authoring", "development", "sealed_transfer"}
)
MAX_REGISTERED_ASSETS = 256
MAX_REGISTERED_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_SAFE_ID_LENGTH = 128
_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_MIME = re.compile(r"^[a-z0-9.+-]+/[a-z0-9.+-]+$")
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)


class ManifestError(ValueError):
    """Raised when a benchmark manifest violates the acquisition contract."""


@dataclass(frozen=True, slots=True)
class AssetSpec:
    """One immutable upstream asset registered for local acquisition."""

    asset_id: str
    title: str
    partition: str
    upstream_path: str
    canonical_url: str
    output_filename: str
    byte_size: int
    media_type: str
    accepted_content_types: tuple[str, ...]
    git_blob_sha1: str
    sha256: str
    license_spdx: str
    redistribution_allowed: bool


@dataclass(frozen=True, slots=True)
class CourseRegistration:
    institution: str
    course_code: str
    title: str
    term: str


@dataclass(frozen=True, slots=True)
class CorpusManifest:
    """Validated acquisition instructions for a frozen external corpus."""

    schema_version: int
    corpus_id: str
    registered_at: str
    course: CourseRegistration
    attribution: str
    repository_url: str
    repository_slug: str
    commit_sha: str
    license_spdx: str
    license_url: str
    license_blob_sha1: str
    license_sha256: str
    default_output_directory: str
    allowed_hosts: tuple[str, ...]
    timeout_seconds: float
    asset_deadline_seconds: float
    max_asset_bytes: int
    max_assets: int
    max_total_bytes: int
    assets: tuple[AssetSpec, ...]


def load_manifest(path: Path) -> CorpusManifest:
    """Load and validate a UTF-8 JSON manifest."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"Cannot read manifest {path}: {exc}") from exc
    return parse_manifest(payload)


def parse_manifest(payload: object) -> CorpusManifest:
    """Validate a decoded manifest without trusting values as code or paths."""

    root = _mapping(payload, "manifest")
    _exact_keys(
        root,
        {
            "schema_version",
            "corpus_id",
            "registered_at",
            "course",
            "attribution",
            "source_repository",
            "acquisition",
            "assets",
        },
        "manifest",
    )

    schema_version = _integer(root["schema_version"], "schema_version")
    if schema_version != 1:
        raise ManifestError(f"Unsupported schema_version: {schema_version}")
    corpus_id = _safe_id(root["corpus_id"], "corpus_id")
    registered_at = _text(root["registered_at"], "registered_at")
    course_payload = _mapping(root["course"], "course")
    _exact_keys(
        course_payload,
        {"institution", "course_code", "title", "term"},
        "course",
    )
    course = CourseRegistration(
        institution=_text(course_payload["institution"], "course.institution"),
        course_code=_text(course_payload["course_code"], "course.course_code"),
        title=_text(course_payload["title"], "course.title"),
        term=_text(course_payload["term"], "course.term"),
    )
    attribution = _text(root["attribution"], "attribution")

    source = _mapping(root["source_repository"], "source_repository")
    _exact_keys(
        source,
        {
            "url",
            "slug",
            "commit_sha",
            "license_spdx",
            "license_url",
            "license_blob_sha1",
            "license_sha256",
        },
        "source_repository",
    )
    repository_url = _https_url(source["url"], "source_repository.url")
    repository_slug = _repository_slug(source["slug"])
    expected_repository_url = f"https://github.com/{repository_slug}"
    if repository_url.rstrip("/") != expected_repository_url:
        raise ManifestError(
            "source_repository.url does not match source_repository.slug"
        )
    commit_sha = _hex(source["commit_sha"], 40, "source_repository.commit_sha")
    license_spdx = _text(source["license_spdx"], "source_repository.license_spdx")
    if license_spdx != "MIT":
        raise ManifestError("The registered CS336 corpus must use SPDX MIT")
    license_url = _https_url(source["license_url"], "source_repository.license_url")
    expected_license_url = (
        f"https://raw.githubusercontent.com/{repository_slug}/{commit_sha}/LICENSE"
    )
    if license_url != expected_license_url:
        raise ManifestError("License URL must be pinned to the registered commit")
    license_blob_sha1 = _hex(
        source["license_blob_sha1"], 40, "source_repository.license_blob_sha1"
    )
    license_sha256 = _hex(
        source["license_sha256"], 64, "source_repository.license_sha256"
    )

    acquisition = _mapping(root["acquisition"], "acquisition")
    _exact_keys(
        acquisition,
        {
            "default_output_directory",
            "allowed_hosts",
            "timeout_seconds",
            "asset_deadline_seconds",
            "max_asset_bytes",
            "max_assets",
            "max_total_bytes",
        },
        "acquisition",
    )
    default_output_directory = _relative_posix_path(
        acquisition["default_output_directory"],
        "acquisition.default_output_directory",
    )
    if not default_output_directory.startswith(
        "backend/data/public_course_benchmarks/"
    ):
        raise ManifestError(
            "default_output_directory must stay under the gitignored benchmark root"
        )
    allowed_hosts = _string_tuple(acquisition["allowed_hosts"], "allowed_hosts")
    if not allowed_hosts:
        raise ManifestError("allowed_hosts must not be empty")
    if not set(allowed_hosts).issubset(TRUSTED_DOWNLOAD_HOSTS):
        raise ManifestError("Manifest requests a host outside the code allowlist")
    timeout_seconds = _positive_number(
        acquisition["timeout_seconds"], "acquisition.timeout_seconds"
    )
    if timeout_seconds > 120:
        raise ManifestError("timeout_seconds exceeds the 120-second safety limit")
    asset_deadline_seconds = _positive_number(
        acquisition["asset_deadline_seconds"],
        "acquisition.asset_deadline_seconds",
    )
    if asset_deadline_seconds > 600:
        raise ManifestError(
            "asset_deadline_seconds exceeds the 600-second safety limit"
        )
    if asset_deadline_seconds < timeout_seconds:
        raise ManifestError(
            "asset_deadline_seconds must be at least timeout_seconds"
        )
    max_asset_bytes = _integer(
        acquisition["max_asset_bytes"], "acquisition.max_asset_bytes"
    )
    if not 1 <= max_asset_bytes <= 64 * 1024 * 1024:
        raise ManifestError("max_asset_bytes is outside the supported range")
    max_assets = _integer(acquisition["max_assets"], "acquisition.max_assets")
    if not 1 <= max_assets <= MAX_REGISTERED_ASSETS:
        raise ManifestError("max_assets is outside the supported range")
    max_total_bytes = _integer(
        acquisition["max_total_bytes"], "acquisition.max_total_bytes"
    )
    if not 1 <= max_total_bytes <= MAX_REGISTERED_TOTAL_BYTES:
        raise ManifestError("max_total_bytes is outside the supported range")

    raw_assets = root["assets"]
    if not isinstance(raw_assets, list) or not raw_assets:
        raise ManifestError("assets must be a non-empty list")
    if len(raw_assets) > max_assets:
        raise ManifestError("assets exceeds acquisition.max_assets")
    assets = tuple(
        _parse_asset(
            item,
            index=index,
            repository_slug=repository_slug,
            commit_sha=commit_sha,
            corpus_license_spdx=license_spdx,
            allowed_hosts=allowed_hosts,
            max_asset_bytes=max_asset_bytes,
        )
        for index, item in enumerate(raw_assets)
    )
    _reject_duplicates(assets)
    if sum(asset.byte_size for asset in assets) > max_total_bytes:
        raise ManifestError("asset bytes exceed acquisition.max_total_bytes")

    return CorpusManifest(
        schema_version=schema_version,
        corpus_id=corpus_id,
        registered_at=registered_at,
        course=course,
        attribution=attribution,
        repository_url=repository_url,
        repository_slug=repository_slug,
        commit_sha=commit_sha,
        license_spdx=license_spdx,
        license_url=license_url,
        license_blob_sha1=license_blob_sha1,
        license_sha256=license_sha256,
        default_output_directory=default_output_directory,
        allowed_hosts=allowed_hosts,
        timeout_seconds=timeout_seconds,
        asset_deadline_seconds=asset_deadline_seconds,
        max_asset_bytes=max_asset_bytes,
        max_assets=max_assets,
        max_total_bytes=max_total_bytes,
        assets=assets,
    )


def validate_download_url(url: str, allowed_hosts: tuple[str, ...]) -> None:
    """Reject non-HTTPS, credentialed, redirected, or unregistered hosts."""

    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise ManifestError("Benchmark downloads require HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ManifestError("Credential-bearing benchmark URLs are forbidden")
    host = (parsed.hostname or "").lower()
    if host not in allowed_hosts or host not in TRUSTED_DOWNLOAD_HOSTS:
        raise ManifestError(f"Download host is not allowlisted: {host or '<missing>'}")
    if parsed.port not in (None, 443):
        raise ManifestError("Benchmark URLs may only use the default HTTPS port")
    if parsed.fragment:
        raise ManifestError("Benchmark download URLs may not contain fragments")


def _parse_asset(
    payload: object,
    *,
    index: int,
    repository_slug: str,
    commit_sha: str,
    corpus_license_spdx: str,
    allowed_hosts: tuple[str, ...],
    max_asset_bytes: int,
) -> AssetSpec:
    label = f"assets[{index}]"
    item = _mapping(payload, label)
    _exact_keys(
        item,
        {
            "asset_id",
            "title",
            "partition",
            "upstream_path",
            "canonical_url",
            "output_filename",
            "byte_size",
            "media_type",
            "accepted_content_types",
            "git_blob_sha1",
            "sha256",
            "license_spdx",
            "redistribution_allowed",
        },
        label,
    )
    asset_id = _safe_id(item["asset_id"], f"{label}.asset_id")
    title = _text(item["title"], f"{label}.title")
    partition = _text(item["partition"], f"{label}.partition")
    if partition not in SUPPORTED_PARTITIONS:
        raise ManifestError(f"{label}.partition is unsupported: {partition}")
    upstream_path = _relative_posix_path(
        item["upstream_path"], f"{label}.upstream_path"
    )
    if not upstream_path.lower().endswith(".pdf"):
        raise ManifestError(f"{label}.upstream_path must be a PDF")
    canonical_url = _https_url(item["canonical_url"], f"{label}.canonical_url")
    validate_download_url(canonical_url, allowed_hosts)
    expected_url = (
        f"https://raw.githubusercontent.com/{repository_slug}/{commit_sha}/"
        f"{quote(upstream_path, safe='/')}"
    )
    if canonical_url != expected_url:
        raise ManifestError(
            f"{label}.canonical_url is not the pinned raw URL for upstream_path"
        )
    output_filename = _safe_id(
        item["output_filename"], f"{label}.output_filename"
    )
    if not output_filename.endswith(".pdf"):
        raise ManifestError(f"{label}.output_filename must end in .pdf")
    byte_size = _integer(item["byte_size"], f"{label}.byte_size")
    if not 1 <= byte_size <= max_asset_bytes:
        raise ManifestError(f"{label}.byte_size exceeds the acquisition limit")
    media_type = _mime(item["media_type"], f"{label}.media_type")
    if media_type != "application/pdf":
        raise ManifestError(f"{label}.media_type must be application/pdf")
    registered_content_types = _string_tuple(
        item["accepted_content_types"], f"{label}.accepted_content_types"
    )
    if not registered_content_types:
        raise ManifestError(f"{label}.accepted_content_types must not be empty")
    accepted_content_types = tuple(
        _mime(content_type, f"{label}.accepted_content_types")
        for content_type in registered_content_types
    )
    git_blob_sha1 = _hex(item["git_blob_sha1"], 40, f"{label}.git_blob_sha1")
    sha256 = _hex(item["sha256"], 64, f"{label}.sha256")
    license_spdx = _text(item["license_spdx"], f"{label}.license_spdx")
    if license_spdx != corpus_license_spdx:
        raise ManifestError(f"{label}.license_spdx differs from the corpus license")
    redistribution_allowed = item["redistribution_allowed"]
    if not isinstance(redistribution_allowed, bool):
        raise ManifestError(f"{label}.redistribution_allowed must be boolean")

    return AssetSpec(
        asset_id=asset_id,
        title=title,
        partition=partition,
        upstream_path=upstream_path,
        canonical_url=canonical_url,
        output_filename=output_filename,
        byte_size=byte_size,
        media_type=media_type,
        accepted_content_types=accepted_content_types,
        git_blob_sha1=git_blob_sha1,
        sha256=sha256,
        license_spdx=license_spdx,
        redistribution_allowed=redistribution_allowed,
    )


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        raise ManifestError(f"{label} must be an object with string keys")
    return value


def _exact_keys(
    value: Mapping[str, Any], expected: set[str], label: str
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ManifestError(f"{label} keys mismatch; missing={missing}, extra={extra}")


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ManifestError(f"{label} must be a non-empty trimmed string")
    return value


def _safe_id(value: object, label: str) -> str:
    text = _text(value, label)
    if len(text) > MAX_SAFE_ID_LENGTH:
        raise ManifestError(f"{label} exceeds {MAX_SAFE_ID_LENGTH} characters")
    if not _SAFE_ID.fullmatch(text):
        raise ManifestError(f"{label} contains unsafe characters")
    windows_stem = text.split(".", 1)[0].upper()
    if windows_stem in _WINDOWS_RESERVED_NAMES:
        raise ManifestError(f"{label} uses a reserved Windows name")
    return text


def _repository_slug(value: object) -> str:
    slug = _text(value, "source_repository.slug")
    parts = slug.split("/")
    if len(parts) != 2 or any(not _SAFE_ID.fullmatch(part.lower()) for part in parts):
        raise ManifestError("source_repository.slug must be owner/repository")
    return slug


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ManifestError(f"{label} must be an integer")
    return value


def _positive_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ManifestError(f"{label} must be a positive number")
    return float(value)


def _hex(value: object, length: int, label: str) -> str:
    text = _text(value, label)
    pattern = _HEX_40 if length == 40 else _HEX_64
    if not pattern.fullmatch(text):
        raise ManifestError(f"{label} must be {length} lowercase hexadecimal chars")
    return text


def _mime(value: object, label: str) -> str:
    text = _text(value, label).lower()
    if not _MIME.fullmatch(text):
        raise ManifestError(f"{label} is not a valid media type")
    return text


def _https_url(value: object, label: str) -> str:
    text = _text(value, label)
    parsed = urlsplit(text)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ManifestError(f"{label} must be an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ManifestError(f"{label} may not contain credentials")
    return text


def _relative_posix_path(value: object, label: str) -> str:
    text = _text(value, label)
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or "\\" in text
        or text != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ManifestError(f"{label} must be a normalized relative POSIX path")
    return text


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ManifestError(f"{label} must be a list of strings")
    result = tuple(value)
    if len(result) != len(set(result)):
        raise ManifestError(f"{label} contains duplicates")
    return result


def _reject_duplicates(assets: tuple[AssetSpec, ...]) -> None:
    for attribute in (
        "asset_id",
        "upstream_path",
        "canonical_url",
        "output_filename",
        "git_blob_sha1",
        "sha256",
    ):
        values = [getattr(asset, attribute) for asset in assets]
        if len(values) != len(set(values)):
            raise ManifestError(f"Duplicate asset {attribute}")
