"""Download registered benchmark assets without executing or trusting them."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .manifest import (
    AssetSpec,
    CorpusManifest,
    ManifestError,
    load_manifest,
    validate_download_url,
)


DEFAULT_PARTITIONS = frozenset({"authoring", "development"})
_READ_SIZE = 128 * 1024
_PDF_MAGIC = b"%PDF-"


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
    open_url: Callable | None = None,
) -> tuple[AcquisitionResult, ...]:
    """Acquire selected partitions into an ignored, caller-owned directory.

    The default deliberately excludes ``sealed_transfer``.  Acquisition never
    mutates the manifest and refuses to replace an invalid existing file.
    ``open_url`` is an offline-test seam; production calls use an opener whose
    redirects are checked before they are followed.
    """

    selected_partitions = frozenset(partitions)
    unknown = selected_partitions - {
        "authoring",
        "development",
        "sealed_transfer",
    }
    if unknown:
        raise AcquisitionError(f"Unknown partitions: {sorted(unknown)}")
    assets = [
        asset for asset in manifest.assets if asset.partition in selected_partitions
    ]
    if not assets:
        raise AcquisitionError("No manifest assets match the selected partitions")

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


def _verify_local_file(path: Path, asset: AssetSpec, max_asset_bytes: int) -> None:
    try:
        before = path.lstat()
    except OSError as exc:
        raise AcquisitionError(f"Cannot inspect existing file: {path}") from exc
    if _is_link_or_reparse(before) or not stat.S_ISREG(before.st_mode):
        raise AcquisitionError(f"Existing file does not match {asset.asset_id}")
    if before.st_size != asset.byte_size:
        raise AcquisitionError(f"Existing file does not match {asset.asset_id}")
    if before.st_size > max_asset_bytes:
        raise AcquisitionError(f"Existing file exceeds the safety limit: {path}")
    if os.name != "nt" and before.st_mode & (
        stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    ):
        raise AcquisitionError(f"Existing benchmark asset is executable: {path}")

    sha256 = hashlib.sha256()
    blob_sha1 = hashlib.sha1(usedforsecurity=False)
    blob_sha1.update(f"blob {asset.byte_size}\0".encode("ascii"))
    leading = b""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        file_descriptor = os.open(path, flags)
    except OSError as exc:
        raise AcquisitionError(f"Cannot safely open existing file: {path}") from exc
    try:
        opened = os.fstat(file_descriptor)
        if (
            _is_link_or_reparse(opened)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_size != asset.byte_size
        ):
            raise AcquisitionError(f"Existing file changed while opening: {path}")
        if _has_distinct_identity(before, opened) and not _same_identity(before, opened):
            raise AcquisitionError(f"Existing file changed while opening: {path}")
        handle = os.fdopen(file_descriptor, "rb", closefd=False)
        while chunk := handle.read(_READ_SIZE):
            if not leading:
                leading = chunk[: len(_PDF_MAGIC)]
            sha256.update(chunk)
            blob_sha1.update(chunk)
        handle.close()
    finally:
        os.close(file_descriptor)
    try:
        after = path.lstat()
    except OSError as exc:
        raise AcquisitionError(f"Existing file changed after verification: {path}") from exc
    if (
        _is_link_or_reparse(after)
        or not stat.S_ISREG(after.st_mode)
        or (_has_distinct_identity(opened, after) and not _same_identity(opened, after))
    ):
        raise AcquisitionError(f"Existing file changed after verification: {path}")
    if leading != _PDF_MAGIC:
        raise AcquisitionError(f"Existing PDF signature mismatch for {asset.asset_id}")
    if sha256.hexdigest() != asset.sha256:
        raise AcquisitionError(f"Existing SHA-256 mismatch for {asset.asset_id}")
    if blob_sha1.hexdigest() != asset.git_blob_sha1:
        raise AcquisitionError(f"Existing Git blob SHA-1 mismatch for {asset.asset_id}")


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
    )
    print(json.dumps([asdict(item) for item in results], indent=2))


if __name__ == "__main__":
    main()
