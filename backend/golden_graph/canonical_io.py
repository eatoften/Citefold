"""Canonical JSON and strict SHA-256 sidecars for protocol artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from pydantic import BaseModel


MAX_PROTOCOL_BYTES = 2 * 1024 * 1024
MAX_SIDECAR_BYTES = 1024
_SIDECAR = re.compile(rb"([0-9a-f]{64})  ([^\x00-\x1f\\/]+)\n")


class CanonicalArtifactError(ValueError):
    """Raised when canonical encoding or an artifact sidecar is invalid."""


def canonical_json_bytes(value: object) -> bytes:
    """Encode one JSON value using the protocol's byte-level identity rules."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_none=False)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise CanonicalArtifactError(
            f"Value is not canonical JSON data: {exc}"
        ) from exc
    # One terminal LF keeps committed artifacts POSIX-text-friendly while the
    # JSON body remains sorted and compact. The LF is part of the content hash.
    return encoded.encode("utf-8") + b"\n"


def load_hashed_canonical_json(path: Path) -> tuple[object, str]:
    """Load canonical UTF-8 JSON after exact sidecar and digest validation."""

    payload = read_bounded_regular_bytes(
        path,
        max_bytes=MAX_PROTOCOL_BYTES,
        label="protocol artifact",
    )
    sidecar = read_bounded_regular_bytes(
        path.with_suffix(".sha256"),
        max_bytes=MAX_SIDECAR_BYTES,
        label="SHA-256 sidecar",
    )
    if not payload or len(payload) > MAX_PROTOCOL_BYTES:
        raise CanonicalArtifactError(
            f"Protocol artifact must contain 1..{MAX_PROTOCOL_BYTES} bytes"
        )

    match = _SIDECAR.fullmatch(sidecar)
    if match is None:
        raise CanonicalArtifactError(f"Invalid SHA-256 sidecar for {path.name}")
    digest_bytes, filename_bytes = match.groups()
    try:
        sidecar_filename = filename_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CanonicalArtifactError(
            f"Invalid UTF-8 filename in sidecar for {path.name}"
        ) from exc
    if sidecar_filename != path.name:
        raise CanonicalArtifactError(f"SHA-256 sidecar names the wrong artifact")

    digest = hashlib.sha256(payload).hexdigest()
    if digest != digest_bytes.decode("ascii"):
        raise CanonicalArtifactError(f"SHA-256 mismatch for {path.name}")

    try:
        decoded = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_nonfinite_number,
        )
        canonical_payload = canonical_json_bytes(decoded)
    except CanonicalArtifactError:
        raise
    except (
        UnicodeError,
        ValueError,
        TypeError,
        RecursionError,
        OverflowError,
    ) as exc:
        raise CanonicalArtifactError(f"Invalid UTF-8 JSON: {path.name}") from exc
    if canonical_payload != payload:
        raise CanonicalArtifactError(
            f"Protocol artifact is not canonical compact sorted JSON: {path.name}"
        )
    return decoded, digest


def write_draft_hashed_canonical_json(path: Path, value: object) -> str:
    """Write a draft canonical artifact and sidecar for authoring/tests only.

    This overwrite-capable helper is not a publication primitive. Callers own
    directory creation and draft replacement policy, and frozen protocols must
    go through ``freeze_protocol`` instead.
    """

    if path.name.casefold().endswith(".frozen.json"):
        raise CanonicalArtifactError(
            "Draft authoring helper cannot write a frozen protocol artifact"
        )
    payload = canonical_json_bytes(value)
    if not payload or len(payload) > MAX_PROTOCOL_BYTES:
        raise CanonicalArtifactError(
            f"Protocol artifact must contain 1..{MAX_PROTOCOL_BYTES} bytes"
        )
    digest = hashlib.sha256(payload).hexdigest()
    path.write_bytes(payload)
    path.with_suffix(".sha256").write_bytes(
        f"{digest}  {path.name}\n".encode("utf-8")
    )
    return digest


def read_bounded_regular_bytes(
    path: Path,
    *,
    max_bytes: int,
    label: str,
) -> bytes:
    """Read one stable regular-file descriptor without following a symlink."""

    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    descriptor = -1
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(path, flags)
        opened_metadata = os.fstat(descriptor)
        visible_metadata = path.lstat()
        if (
            not stat.S_ISREG(opened_metadata.st_mode)
            or not stat.S_ISREG(visible_metadata.st_mode)
            or _is_reparse_point(opened_metadata)
            or _is_reparse_point(visible_metadata)
            or _stable_file_identity(opened_metadata)
            != _stable_file_identity(visible_metadata)
        ):
            raise CanonicalArtifactError(
                f"{label} must be a regular file: {path}"
            )
        if opened_metadata.st_nlink != 1:
            raise CanonicalArtifactError(
                f"{label} cannot be hard-linked: {path}"
            )
        if not 1 <= opened_metadata.st_size <= max_bytes:
            raise CanonicalArtifactError(
                f"{label} must contain 1..{max_bytes} bytes: {path}"
            )
        payload = bytearray()
        while len(payload) <= max_bytes:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, max_bytes + 1 - len(payload)),
            )
            if not chunk:
                break
            payload.extend(chunk)
        final_metadata = os.fstat(descriptor)
        final_visible_metadata = path.lstat()
        if (
            _stable_file_identity(final_metadata)
            != _stable_file_identity(opened_metadata)
            or _stable_file_identity(final_visible_metadata)
            != _stable_file_identity(opened_metadata)
            or _is_reparse_point(final_metadata)
            or _is_reparse_point(final_visible_metadata)
            or final_metadata.st_nlink != 1
            or not 1 <= len(payload) <= max_bytes
            or len(payload) != final_metadata.st_size
        ):
            raise CanonicalArtifactError(
                f"{label} must contain 1..{max_bytes} bytes: {path}"
            )
        return bytes(payload)
    except CanonicalArtifactError:
        raise
    except OSError as exc:
        raise CanonicalArtifactError(f"Cannot read {label}: {path}") from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _stable_file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    """Return metadata that must remain unchanged across one authority read."""

    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        getattr(metadata, "st_file_attributes", 0),
    )


def _is_reparse_point(metadata: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(
        reparse_flag
        and getattr(metadata, "st_file_attributes", 0) & reparse_flag
    )


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CanonicalArtifactError(f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_number(value: str) -> None:
    raise CanonicalArtifactError(f"Non-finite JSON number is forbidden: {value}")
