"""Download registered benchmark assets without executing or trusting them."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .manifest import (
    AssetSpec,
    CorpusManifest,
    ManifestError,
    SUPPORTED_PARTITIONS,
    load_manifest,
    validate_download_url,
)


DEFAULT_PARTITIONS = frozenset({"authoring", "development"})
DEFAULT_VERIFICATION_PARTITIONS = frozenset({"authoring"})
_READ_SIZE = 128 * 1024
_PDF_MAGIC = b"%PDF-"
_CANONICAL_BENCHMARK_PREFIX = ("backend", "data", "public_course_benchmarks")
_VERIFIED_ASSET_TOKEN = object()


class AcquisitionError(RuntimeError):
    """Raised before unverified bytes can become a local benchmark asset."""


@dataclass(frozen=True, slots=True)
class AcquisitionResult:
    asset_id: str
    partition: str
    path: str
    byte_size: int
    sha256: str
    status: str


@dataclass(frozen=True, slots=True, init=False)
class VerifiedAssetReceipt:
    """Proof that one exact registered local asset passed full verification.

    The constructor token keeps ordinary callers from promoting a bare path or
    self-asserted digest into verification authority.  Python module internals
    are not a security boundary, so consumers must still obtain receipts only
    from :func:`verify_registered_asset`.
    """

    corpus_id: str
    asset_id: str
    partition: str
    path: Path
    media_type: str
    byte_size: int
    sha256: str
    git_blob_sha1: str
    file_device: int
    file_inode: int
    file_mtime_ns: int
    file_ctime_ns: int
    _validation_token: object = field(init=False, repr=False, compare=False)

    def __init__(self, *_args, **_kwargs) -> None:
        raise TypeError(
            "VerifiedAssetReceipt must come from verify_registered_asset"
        )

    def __post_init__(self) -> None:
        if self._validation_token is not _VERIFIED_ASSET_TOKEN:
            raise ValueError(
                "VerifiedAssetReceipt must come from verify_registered_asset"
            )
        if not self.corpus_id or not self.asset_id:
            raise ValueError("Verified asset identity must not be empty")
        if self.partition not in SUPPORTED_PARTITIONS:
            raise ValueError("Verified asset partition is unsupported")
        if not self.path.is_absolute():
            raise ValueError("Verified asset path must be absolute")
        if self.media_type != "application/pdf" or self.byte_size <= 0:
            raise ValueError("Verified asset metadata is invalid")
        for label, digest, length in (
            ("sha256", self.sha256, 64),
            ("git_blob_sha1", self.git_blob_sha1, 40),
        ):
            if len(digest) != length or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ValueError(f"{label} must be lowercase hexadecimal")
        if self.file_inode <= 0:
            raise ValueError("Verified asset file identity is unavailable")


def _issue_verified_asset_receipt(
    *,
    manifest: CorpusManifest,
    asset: AssetSpec,
    path: Path,
    verified: os.stat_result,
) -> VerifiedAssetReceipt:
    receipt = object.__new__(VerifiedAssetReceipt)
    values = {
        "corpus_id": manifest.corpus_id,
        "asset_id": asset.asset_id,
        "partition": asset.partition,
        "path": path,
        "media_type": asset.media_type,
        "byte_size": asset.byte_size,
        "sha256": asset.sha256,
        "git_blob_sha1": asset.git_blob_sha1,
        "file_device": int(verified.st_dev),
        "file_inode": int(verified.st_ino),
        "file_mtime_ns": _stat_time_ns(verified, "mtime"),
        "file_ctime_ns": _stat_time_ns(verified, "ctime"),
        "_validation_token": _VERIFIED_ASSET_TOKEN,
    }
    for name, value in values.items():
        object.__setattr__(receipt, name, value)
    receipt.__post_init__()
    return receipt


class _AllowlistedRedirectHandler(HTTPRedirectHandler):
    def __init__(self, allowed_hosts: tuple[str, ...]) -> None:
        super().__init__()
        self._allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        try:
            validate_download_url(newurl, self._allowed_hosts)
        except ManifestError as exc:
            raise AcquisitionError(f"Rejected redirect: {exc}") from exc
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def acquire_manifest(
    manifest: CorpusManifest,
    output_directory: Path,
    *,
    partitions: Iterable[str] = DEFAULT_PARTITIONS,
    asset_ids: Iterable[str] | None = None,
    open_url: Callable | None = None,
) -> tuple[AcquisitionResult, ...]:
    """Acquire selected partitions into an ignored, caller-owned directory.

    The default deliberately excludes ``sealed_transfer``.  When ``asset_ids``
    is supplied, every exact ID must exist and belong to an authorized
    partition; request order is preserved and duplicates are rejected.
    Acquisition never mutates the manifest and refuses to replace an invalid
    existing file. ``open_url`` is an offline-test seam; production calls use
    an opener whose redirects are checked before they are followed.
    """

    selected_partitions = _validated_partitions(partitions)
    assets = _select_registered_assets(
        manifest,
        selected_partitions,
        asset_ids=asset_ids,
    )

    output_directory = _ensure_plain_directory(output_directory)

    if open_url is None:
        opener = build_opener(_AllowlistedRedirectHandler(manifest.allowed_hosts))
        open_url = opener.open

    results = []
    for asset in assets:
        results.append(
            acquire_asset(
                manifest,
                asset,
                output_directory,
                open_url=open_url,
            )
        )
    return tuple(results)


def verify_registered_asset(
    manifest: CorpusManifest,
    asset_id: str,
    repository_root: Path,
    allowed_partitions: Iterable[str] = DEFAULT_VERIFICATION_PARTITIONS,
) -> VerifiedAssetReceipt:
    """Verify one manifest-selected asset at its sole canonical local path.

    No caller-selected asset path is accepted.  The path is derived from the
    repository root and the manifest's gitignored benchmark directory.  This
    function is read-only: a missing directory or file fails without creating
    any filesystem component.
    """

    partitions = _validated_partitions(allowed_partitions)
    asset = _select_registered_assets(
        manifest,
        partitions,
        asset_ids=(asset_id,),
    )[0]
    root = Path(os.path.abspath(repository_root))
    _validate_directory_chain(root, require_complete=True)
    destination = _canonical_registered_asset_path(manifest, asset, root)
    _validate_directory_chain(destination.parent, require_complete=True)
    verified = _verify_local_file(
        destination,
        asset,
        manifest.max_asset_bytes,
    )
    return _issue_verified_asset_receipt(
        manifest=manifest,
        asset=asset,
        path=destination,
        verified=verified,
    )


def acquire_asset(
    manifest: CorpusManifest,
    asset: AssetSpec,
    output_directory: Path,
    *,
    open_url: Callable,
) -> AcquisitionResult:
    """Download, verify, and atomically publish one non-executable PDF."""

    try:
        validate_download_url(asset.canonical_url, manifest.allowed_hosts)
    except ManifestError as exc:
        raise AcquisitionError(str(exc)) from exc

    output_directory = _ensure_plain_directory(output_directory)
    partition_directory = _ensure_plain_directory(
        output_directory / asset.partition
    )
    destination = partition_directory / asset.output_filename
    if destination.parent != partition_directory:
        raise AcquisitionError("Asset output escaped the selected directory")
    if os.path.lexists(destination):
        _verify_local_file(destination, asset, manifest.max_asset_bytes)
        return _result(asset, destination, status="already_verified")

    request = Request(
        asset.canonical_url,
        headers={
            "Accept": "application/pdf, application/octet-stream;q=0.8",
            "Accept-Encoding": "identity",
            "User-Agent": "Video-Course-Cards-benchmark-acquisition/1",
        },
        method="GET",
    )
    temporary_path: Path | None = None
    deadline = time.monotonic() + manifest.asset_deadline_seconds
    try:
        remaining = _remaining_seconds(deadline, asset.asset_id)
        with open_url(
            request,
            timeout=min(manifest.timeout_seconds, remaining),
        ) as response:
            _enforce_deadline(deadline, asset.asset_id)
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            if status != 200:
                raise AcquisitionError(f"Unexpected HTTP status for {asset.asset_id}: {status}")
            final_url = response.geturl()
            validate_download_url(final_url, manifest.allowed_hosts)
            if final_url != asset.canonical_url:
                raise AcquisitionError(
                    f"Final URL differs from the registered URL for {asset.asset_id}"
                )
            content_encoding = (response.headers.get("Content-Encoding") or "identity").lower()
            if content_encoding != "identity":
                raise AcquisitionError("Encoded responses are not accepted")
            content_type = (
                (response.headers.get("Content-Type") or "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            if content_type not in asset.accepted_content_types:
                raise AcquisitionError(
                    f"Unexpected Content-Type for {asset.asset_id}: {content_type or '<missing>'}"
                )
            content_length = _parse_content_length(
                response.headers.get("Content-Length"), asset.asset_id
            )
            if content_length != asset.byte_size:
                raise AcquisitionError(
                    f"Content-Length mismatch for {asset.asset_id}: {content_length}"
                )

            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{asset.asset_id}.",
                suffix=".part",
                dir=partition_directory,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                _stream_verified(
                    response,
                    temporary,
                    asset,
                    manifest.max_asset_bytes,
                    deadline=deadline,
                )
                temporary.flush()
                os.fsync(temporary.fileno())

        os.chmod(temporary_path, stat.S_IRUSR | stat.S_IWUSR)
        try:
            os.link(temporary_path, destination)
        except FileExistsError:
            _verify_local_file(destination, asset, manifest.max_asset_bytes)
            return _result(asset, destination, status="already_verified")
        return _result(asset, destination, status="downloaded")
    except (HTTPError, URLError, TimeoutError, OSError, ManifestError) as exc:
        raise AcquisitionError(f"Acquisition failed for {asset.asset_id}: {exc}") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _stream_verified(  # noqa: ANN001
    response,
    output,
    asset: AssetSpec,
    max_asset_bytes: int,
    *,
    deadline: float,
) -> None:
    sha256 = hashlib.sha256()
    blob_sha1 = hashlib.sha1(usedforsecurity=False)
    blob_sha1.update(f"blob {asset.byte_size}\0".encode("ascii"))
    byte_count = 0
    leading = bytearray()
    while True:
        _enforce_deadline(deadline, asset.asset_id)
        chunk = response.read(_READ_SIZE)
        _enforce_deadline(deadline, asset.asset_id)
        if not chunk:
            break
        byte_count += len(chunk)
        if byte_count > asset.byte_size or byte_count > max_asset_bytes:
            raise AcquisitionError(f"Response exceeded the registered size for {asset.asset_id}")
        if len(leading) < len(_PDF_MAGIC):
            leading.extend(chunk[: len(_PDF_MAGIC) - len(leading)])
        sha256.update(chunk)
        blob_sha1.update(chunk)
        output.write(chunk)

    if bytes(leading) != _PDF_MAGIC:
        raise AcquisitionError(f"PDF signature mismatch for {asset.asset_id}")
    if byte_count != asset.byte_size:
        raise AcquisitionError(f"Downloaded size mismatch for {asset.asset_id}")
    if sha256.hexdigest() != asset.sha256:
        raise AcquisitionError(f"SHA-256 mismatch for {asset.asset_id}")
    if blob_sha1.hexdigest() != asset.git_blob_sha1:
        raise AcquisitionError(f"Git blob SHA-1 mismatch for {asset.asset_id}")


def _verify_local_file(
    path: Path,
    asset: AssetSpec,
    max_asset_bytes: int,
) -> os.stat_result:
    try:
        before = path.lstat()
    except OSError as exc:
        raise AcquisitionError(f"Cannot inspect existing file: {path}") from exc
    _validate_local_file_metadata(before, path, asset, max_asset_bytes)

    sha256 = hashlib.sha256()
    blob_sha1 = hashlib.sha1(usedforsecurity=False)
    blob_sha1.update(f"blob {asset.byte_size}\0".encode("ascii"))
    leading = b""
    byte_count = 0
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        file_descriptor = os.open(path, flags)
    except OSError as exc:
        raise AcquisitionError(f"Cannot safely open existing file: {path}") from exc
    try:
        opened = os.fstat(file_descriptor)
        _validate_local_file_metadata(opened, path, asset, max_asset_bytes)
        _require_same_identity(before, opened, path, stage="while opening")
        _require_stable_read_window(before, opened, path, stage="while opening")
        with os.fdopen(file_descriptor, "rb", closefd=False) as handle:
            while chunk := handle.read(_READ_SIZE):
                byte_count += len(chunk)
                if byte_count > asset.byte_size or byte_count > max_asset_bytes:
                    raise AcquisitionError(
                        f"Existing file exceeded the registered size: {path}"
                    )
                if not leading:
                    leading = chunk[: len(_PDF_MAGIC)]
                sha256.update(chunk)
                blob_sha1.update(chunk)
        read_finished = os.fstat(file_descriptor)
        _validate_local_file_metadata(
            read_finished,
            path,
            asset,
            max_asset_bytes,
        )
        _require_same_identity(opened, read_finished, path, stage="while reading")
        _require_stable_read_window(
            opened,
            read_finished,
            path,
            stage="while reading",
        )
    finally:
        os.close(file_descriptor)
    try:
        after = path.lstat()
    except OSError as exc:
        raise AcquisitionError(
            f"Existing file changed after verification: {path}"
        ) from exc
    _validate_local_file_metadata(after, path, asset, max_asset_bytes)
    _require_same_identity(
        read_finished,
        after,
        path,
        stage="after verification",
    )
    _require_stable_read_window(
        read_finished,
        after,
        path,
        stage="after verification",
    )
    if byte_count != asset.byte_size:
        raise AcquisitionError(f"Existing size mismatch for {asset.asset_id}")
    if leading != _PDF_MAGIC:
        raise AcquisitionError(f"Existing PDF signature mismatch for {asset.asset_id}")
    if sha256.hexdigest() != asset.sha256:
        raise AcquisitionError(f"Existing SHA-256 mismatch for {asset.asset_id}")
    if blob_sha1.hexdigest() != asset.git_blob_sha1:
        raise AcquisitionError(f"Existing Git blob SHA-1 mismatch for {asset.asset_id}")
    return after


def _validate_local_file_metadata(
    metadata,  # noqa: ANN001
    path: Path,
    asset: AssetSpec,
    max_asset_bytes: int,
) -> None:
    if _is_link_or_reparse(metadata) or not stat.S_ISREG(metadata.st_mode):
        raise AcquisitionError(f"Existing file does not match {asset.asset_id}")
    if getattr(metadata, "st_nlink", 0) != 1:
        raise AcquisitionError(f"Existing file is not single-link: {path}")
    if metadata.st_size != asset.byte_size:
        raise AcquisitionError(f"Existing file does not match {asset.asset_id}")
    if metadata.st_size > max_asset_bytes:
        raise AcquisitionError(f"Existing file exceeds the safety limit: {path}")
    if os.name != "nt" and metadata.st_mode & (
        stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    ):
        raise AcquisitionError(f"Existing benchmark asset is executable: {path}")


def _require_same_identity(  # noqa: ANN001
    first,
    second,
    path: Path,
    *,
    stage: str,
) -> None:
    if not _has_distinct_identity(first, second) or not _same_identity(first, second):
        raise AcquisitionError(f"Existing file changed {stage}: {path}")


def _require_stable_read_window(
    first,  # noqa: ANN001
    second,  # noqa: ANN001
    path: Path,
    *,
    stage: str,
) -> None:
    if _read_window_metadata(first) != _read_window_metadata(second):
        raise AcquisitionError(f"Existing file changed {stage}: {path}")


def _read_window_metadata(metadata) -> tuple[int, int, int, int]:  # noqa: ANN001
    return (
        int(metadata.st_size),
        int(metadata.st_nlink),
        _stat_time_ns(metadata, "mtime"),
        _stat_time_ns(metadata, "ctime"),
    )


def _stat_time_ns(metadata, kind: str) -> int:  # noqa: ANN001
    nanoseconds = getattr(metadata, f"st_{kind}_ns", None)
    if isinstance(nanoseconds, int):
        return nanoseconds
    seconds = getattr(metadata, f"st_{kind}", None)
    if not isinstance(seconds, (int, float)):
        raise AcquisitionError(f"File {kind} timestamp is unavailable")
    return int(seconds * 1_000_000_000)


def _validated_partitions(partitions: Iterable[str]) -> frozenset[str]:
    if isinstance(partitions, (str, bytes)):
        raise AcquisitionError("partitions must be an iterable of partition names")
    try:
        selected = tuple(partitions)
    except TypeError as exc:
        raise AcquisitionError("partitions must be iterable") from exc
    if not all(isinstance(partition, str) for partition in selected):
        raise AcquisitionError("partitions must contain only strings")
    selected_partitions = frozenset(selected)
    unknown = selected_partitions - SUPPORTED_PARTITIONS
    if unknown:
        raise AcquisitionError(f"Unknown partitions: {sorted(unknown)}")
    if not selected_partitions:
        raise AcquisitionError("At least one authorized partition is required")
    return selected_partitions


def _select_registered_assets(
    manifest: CorpusManifest,
    selected_partitions: frozenset[str],
    *,
    asset_ids: Iterable[str] | None,
) -> tuple[AssetSpec, ...]:
    assets_by_id: dict[str, AssetSpec] = {}
    for asset in manifest.assets:
        if asset.asset_id in assets_by_id:
            raise AcquisitionError(
                f"Manifest contains duplicate asset ID: {asset.asset_id}"
            )
        assets_by_id[asset.asset_id] = asset

    if asset_ids is None:
        selected = tuple(
            asset
            for asset in manifest.assets
            if asset.partition in selected_partitions
        )
        if not selected:
            raise AcquisitionError(
                "No manifest assets match the selected partitions"
            )
        return selected

    if isinstance(asset_ids, (str, bytes)):
        raise AcquisitionError("asset_ids must be an iterable of exact asset IDs")
    try:
        requested_ids = tuple(asset_ids)
    except TypeError as exc:
        raise AcquisitionError("asset_ids must be iterable") from exc
    if not requested_ids:
        raise AcquisitionError("At least one exact asset ID is required")
    if not all(isinstance(asset_id, str) for asset_id in requested_ids):
        raise AcquisitionError("asset_ids must contain only strings")
    if len(requested_ids) != len(set(requested_ids)):
        raise AcquisitionError("Duplicate requested asset IDs are forbidden")

    unknown_ids = sorted(set(requested_ids) - set(assets_by_id))
    if unknown_ids:
        raise AcquisitionError(f"Unknown asset IDs: {unknown_ids}")
    unauthorized_ids = [
        asset_id
        for asset_id in requested_ids
        if assets_by_id[asset_id].partition not in selected_partitions
    ]
    if unauthorized_ids:
        raise AcquisitionError(
            "Requested asset IDs are outside the authorized partitions: "
            f"{unauthorized_ids}"
        )
    return tuple(assets_by_id[asset_id] for asset_id in requested_ids)


def _canonical_registered_asset_path(
    manifest: CorpusManifest,
    asset: AssetSpec,
    repository_root: Path,
) -> Path:
    relative_output = PurePosixPath(manifest.default_output_directory)
    expected_parts = (*_CANONICAL_BENCHMARK_PREFIX, manifest.corpus_id)
    if (
        relative_output.is_absolute()
        or "\\" in manifest.default_output_directory
        or relative_output.parts != expected_parts
        or relative_output.as_posix() != manifest.default_output_directory
    ):
        raise AcquisitionError(
            "Manifest output directory is not the canonical ignored corpus path"
        )
    if (
        not asset.output_filename
        or asset.output_filename in {".", ".."}
        or "/" in asset.output_filename
        or "\\" in asset.output_filename
    ):
        raise AcquisitionError("Registered asset filename is not one safe component")

    output_directory = repository_root.joinpath(*relative_output.parts)
    partition_directory = output_directory / asset.partition
    destination = partition_directory / asset.output_filename
    if destination.parent != partition_directory:
        raise AcquisitionError("Registered asset path escaped its partition")
    try:
        destination.relative_to(repository_root)
    except ValueError as exc:
        raise AcquisitionError("Registered asset path escaped the repository") from exc
    return destination


def _parse_content_length(value: str | None, asset_id: str) -> int:
    if value is None:
        raise AcquisitionError(f"Missing Content-Length for {asset_id}")
    try:
        result = int(value)
    except ValueError as exc:
        raise AcquisitionError(f"Invalid Content-Length for {asset_id}") from exc
    if result < 0:
        raise AcquisitionError(f"Invalid Content-Length for {asset_id}")
    return result


def _ensure_plain_directory(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    _validate_directory_chain(absolute)
    if os.path.lexists(absolute):
        return absolute
    try:
        absolute.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AcquisitionError(f"Cannot create output directory: {absolute}") from exc
    _validate_directory_chain(absolute, require_complete=True)
    return absolute


def _validate_directory_chain(path: Path, *, require_complete: bool = False) -> None:
    anchor = Path(path.anchor)
    current = anchor
    anchor_part_count = len(anchor.parts)
    for part in path.parts[anchor_part_count:]:
        current /= part
        if not os.path.lexists(current):
            if require_complete:
                raise AcquisitionError(f"Output directory is missing: {current}")
            return
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise AcquisitionError(f"Cannot inspect output path: {current}") from exc
        if _is_link_or_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise AcquisitionError(
                f"Output path contains a link or reparse point: {current}"
            )


def _is_link_or_reparse(metadata) -> bool:  # noqa: ANN001
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    return bool(stat.S_ISLNK(metadata.st_mode) or file_attributes & reparse_flag)


def _remaining_seconds(deadline: float, asset_id: str) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise AcquisitionError(f"Asset deadline exceeded for {asset_id}")
    return remaining


def _enforce_deadline(deadline: float, asset_id: str) -> None:
    _remaining_seconds(deadline, asset_id)


def _has_distinct_identity(first, second) -> bool:  # noqa: ANN001
    return bool(
        getattr(first, "st_ino", 0)
        and getattr(second, "st_ino", 0)
    )


def _same_identity(first, second) -> bool:  # noqa: ANN001
    return (
        getattr(first, "st_dev", None),
        getattr(first, "st_ino", None),
    ) == (
        getattr(second, "st_dev", None),
        getattr(second, "st_ino", None),
    )


def _result(asset: AssetSpec, path: Path, *, status: str) -> AcquisitionResult:
    return AcquisitionResult(
        asset_id=asset.asset_id,
        partition=asset.partition,
        path=str(path),
        byte_size=asset.byte_size,
        sha256=asset.sha256,
        status=status,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Acquire hash-pinned public-course benchmark PDFs."
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument(
        "--asset-id",
        dest="asset_ids",
        action="append",
        help=(
            "Acquire one exact registered asset ID. Repeat for multiple assets; "
            "each ID must belong to an authorized partition."
        ),
    )
    parser.add_argument(
        "--include-sealed-transfer",
        action="store_true",
        help="Explicitly acquire the sealed-transfer partition as well.",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    manifest = load_manifest(args.manifest.resolve())
    repository_root = Path(__file__).resolve().parents[2]
    output_directory = (
        args.output_directory
        if args.output_directory is not None
        else repository_root / manifest.default_output_directory
    )
    partitions = set(DEFAULT_PARTITIONS)
    if args.include_sealed_transfer:
        partitions.add("sealed_transfer")
    results = acquire_manifest(
        manifest,
        output_directory,
        partitions=partitions,
        asset_ids=args.asset_ids,
    )
    print(json.dumps([asdict(item) for item in results], indent=2))


if __name__ == "__main__":
    main()
