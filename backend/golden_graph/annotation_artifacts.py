"""Crash-recoverable canonical artifact I/O for G2 annotation stages.

The functions in this module establish byte identity and publication
immutability.  They do not establish that an annotation was made by a human;
that authority belongs to a separately verified detached attestation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

from .canonical_io import (
    CanonicalArtifactError,
    canonical_json_bytes,
    load_hashed_canonical_json,
    read_bounded_regular_bytes,
)
from .trusted_git import (
    TrustedGitError,
    minimal_git_environment,
    resolve_trusted_git_executable,
)


MAX_ANNOTATION_ARTIFACT_BYTES = 2 * 1024 * 1024
_PUBLICATION_TOKEN = object()
_ArtifactModel = TypeVar("_ArtifactModel", bound=BaseModel)
_FORBIDDEN_PUBLIC_KEYS = frozenset({
    "exact_quote",
    "quote",
    "source_text",
    "chunk_text",
    "page_text",
    "transcript",
    "transcript_text",
    "private_path",
    "local_path",
    "runtime_source_id",
    "runtime_chunk_id",
    "projection_generation_id",
})


class AnnotationArtifactError(ValueError):
    """Raised when a G2 artifact cannot be loaded or published safely."""


class _PublicationRaceError(AnnotationArtifactError):
    """Internal signal that another writer atomically claimed the target."""


@dataclass(frozen=True, slots=True, init=False)
class CanonicalArtifactAuthority(Generic[_ArtifactModel]):
    """Token-gated receipt for one validated canonical artifact.

    This receipt proves only schema, hash, and persisted-byte consistency.  It
    is deliberately not named a human-review authority.
    """

    artifact: _ArtifactModel
    artifact_path: Path
    artifact_sha256: str
    _validation_token: object = field(repr=False, compare=False)

    def __init__(self, *_args, **_kwargs) -> None:
        raise TypeError(
            "CanonicalArtifactAuthority must come from a strict loader"
        )

    def __post_init__(self) -> None:
        if self._validation_token is not _PUBLICATION_TOKEN:
            raise ValueError("Invalid canonical artifact authority token")
        if not self.artifact_path.is_absolute():
            raise ValueError("Canonical artifact path must be absolute")
        if not _is_lower_sha256(self.artifact_sha256):
            raise ValueError("Canonical artifact SHA-256 is invalid")


def publish_canonical_artifact(
    path: Path,
    artifact: BaseModel,
    *,
    allowed_root: Path | None = None,
    max_bytes: int = MAX_ANNOTATION_ARTIFACT_BYTES,
    reject_private_fields: bool = True,
) -> str:
    """Publish or recover one immutable canonical JSON artifact and sidecar.

    Publication writes the JSON first and its SHA-256 sidecar second.  A crash
    between those writes is recoverable by an identical retry; an existing
    conflicting leaf is never overwritten or removed.
    """

    resolved_path, payload, digest, sidecar_path, sidecar = (
        _preflight_canonical_artifact_bytes(
            path,
            artifact,
            allowed_root=allowed_root,
            max_bytes=max_bytes,
            reject_private_fields=reject_private_fields,
        )
    )
    if not resolved_path.exists():
        try:
            _write_exclusive_durable(resolved_path, payload)
        except _PublicationRaceError:
            _require_exact_regular_bytes_after_publication_race(
                resolved_path,
                payload,
                max_bytes=max_bytes,
                label="annotation artifact",
            )
    if not sidecar_path.exists():
        try:
            _write_exclusive_durable(sidecar_path, sidecar)
        except _PublicationRaceError:
            _require_exact_regular_bytes_after_publication_race(
                sidecar_path,
                sidecar,
                max_bytes=1024,
                label="annotation artifact sidecar",
            )

    _require_exact_regular_bytes_after_publication_race(
        resolved_path,
        payload,
        max_bytes=max_bytes,
        label="annotation artifact",
    )
    _require_exact_regular_bytes_after_publication_race(
        sidecar_path,
        sidecar,
        max_bytes=1024,
        label="annotation artifact sidecar",
    )
    return digest


def preflight_canonical_artifact(
    path: Path,
    artifact: BaseModel,
    *,
    allowed_root: Path | None = None,
    max_bytes: int = MAX_ANNOTATION_ARTIFACT_BYTES,
    reject_private_fields: bool = True,
) -> str:
    """Validate one immutable JSON/sidecar publication without writing bytes.

    Multi-leaf workflows call this for every leaf before publishing the first
    one.  That makes a pre-existing conflict fail before the workflow leaves a
    partial DAG; a crash during the later writes is still recovered by an
    identical retry and cannot become authority until the DAG root exists.
    """

    _, _, digest, _, _ = _preflight_canonical_artifact_bytes(
        path,
        artifact,
        allowed_root=allowed_root,
        max_bytes=max_bytes,
        reject_private_fields=reject_private_fields,
    )
    return digest


def load_canonical_artifact(
    path: Path,
    model_type: type[_ArtifactModel],
    *,
    allowed_root: Path | None = None,
    reject_private_fields: bool = True,
) -> CanonicalArtifactAuthority[_ArtifactModel]:
    """Load a canonical artifact after sidecar, schema, and byte checks."""

    resolved_path = _resolve_existing_path(path, allowed_root=allowed_root)
    try:
        decoded, digest = load_hashed_canonical_json(resolved_path)
        if reject_private_fields:
            _reject_forbidden_public_keys(decoded)
        artifact = model_type.model_validate(decoded)
        if canonical_json_bytes(artifact) != read_bounded_regular_bytes(
            resolved_path,
            max_bytes=MAX_ANNOTATION_ARTIFACT_BYTES,
            label="annotation artifact",
        ):
            raise AnnotationArtifactError(
                "Annotation artifact changed under typed canonicalization"
            )
    except AnnotationArtifactError:
        raise
    except (CanonicalArtifactError, ValidationError, OSError, ValueError) as exc:
        raise AnnotationArtifactError(
            f"Invalid annotation artifact: {resolved_path.name}"
        ) from exc
    return _issue_authority(
        artifact=artifact,
        path=resolved_path,
        digest=digest,
    )


def write_new_private_worksheet(
    path: Path,
    artifact: BaseModel,
    *,
    repository_root: Path,
    max_bytes: int = MAX_ANNOTATION_ARTIFACT_BYTES,
    human_readable: bool = False,
) -> str:
    """Write one new mutable worksheet inside the gitignored G2 boundary.

    Worksheets intentionally have no sidecar and are not authorities.  The
    caller may edit them before sealing, but initialization never overwrites an
    existing file.  The later seal command reloads and revalidates all fields
    against source authority.
    """

    root = Path(repository_root).resolve(strict=True)
    private_root = root / "backend/data/golden_graph/annotations"
    private_root.mkdir(parents=True, exist_ok=True)
    resolved_path = _resolve_output_path(path, allowed_root=private_root)
    _require_gitignored(root, resolved_path)
    if human_readable:
        payload = (
            json.dumps(
                artifact.model_dump(mode="json", exclude_none=False),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")
    else:
        payload = canonical_json_bytes(artifact)
    if not 1 <= len(payload) <= max_bytes:
        raise AnnotationArtifactError(
            f"Annotation worksheet must contain 1..{max_bytes} bytes"
        )
    if os.path.lexists(resolved_path):
        raise AnnotationArtifactError(
            f"Annotation worksheet already exists: {resolved_path.name}"
        )
    _write_exclusive_durable(resolved_path, payload)
    _require_gitignored(root, resolved_path)
    return hashlib.sha256(payload).hexdigest()


def publish_private_canonical_artifact(
    path: Path,
    artifact: BaseModel,
    *,
    repository_root: Path,
) -> str:
    """Publish one immutable canonical leaf inside the ignored G2 boundary."""

    root = Path(repository_root).resolve(strict=True)
    private_root = (root / "backend/data/golden_graph/annotations").resolve(
        strict=True
    )
    _require_gitignored(root, path)
    digest = publish_canonical_artifact(
        path,
        artifact,
        allowed_root=private_root,
        reject_private_fields=False,
    )
    _require_gitignored(root, path)
    _require_gitignored(root, path.with_suffix(".sha256"))
    return digest


def preflight_private_canonical_artifact(
    path: Path,
    artifact: BaseModel,
    *,
    repository_root: Path,
) -> str:
    """Validate an immutable private leaf without publishing any bytes."""

    root = Path(repository_root).resolve(strict=True)
    private_root = (root / "backend/data/golden_graph/annotations").resolve(
        strict=True
    )
    _require_gitignored(root, path)
    digest = preflight_canonical_artifact(
        path,
        artifact,
        allowed_root=private_root,
        reject_private_fields=False,
    )
    _require_gitignored(root, path)
    _require_gitignored(root, path.with_suffix(".sha256"))
    return digest


def load_private_canonical_artifact(
    path: Path,
    model_type: type[_ArtifactModel],
    *,
    repository_root: Path,
) -> CanonicalArtifactAuthority[_ArtifactModel]:
    """Load one immutable private leaf while rechecking its Git boundary."""

    root = Path(repository_root).resolve(strict=True)
    private_root = (root / "backend/data/golden_graph/annotations").resolve(
        strict=True
    )
    _require_gitignored(root, path)
    _require_gitignored(root, path.with_suffix(".sha256"))
    authority = load_canonical_artifact(
        path,
        model_type,
        allowed_root=private_root,
        reject_private_fields=False,
    )
    _require_gitignored(root, authority.artifact_path)
    return authority


def read_private_worksheet_bytes(
    path: Path,
    *,
    repository_root: Path,
    max_bytes: int = MAX_ANNOTATION_ARTIFACT_BYTES,
) -> bytes:
    """Read a mutable worksheet without treating it as sealed authority."""

    root = Path(repository_root).resolve(strict=True)
    private_root = (root / "backend/data/golden_graph/annotations").resolve(
        strict=True
    )
    resolved_path = _resolve_existing_path(path, allowed_root=private_root)
    _require_gitignored(root, resolved_path)
    try:
        return read_bounded_regular_bytes(
            resolved_path,
            max_bytes=max_bytes,
            label="private annotation worksheet",
        )
    except CanonicalArtifactError as exc:
        raise AnnotationArtifactError(
            "Private annotation worksheet could not be read safely"
        ) from exc


def _issue_authority(
    *,
    artifact: _ArtifactModel,
    path: Path,
    digest: str,
) -> CanonicalArtifactAuthority[_ArtifactModel]:
    receipt = object.__new__(CanonicalArtifactAuthority)
    object.__setattr__(receipt, "artifact", artifact)
    object.__setattr__(receipt, "artifact_path", path.resolve(strict=True))
    object.__setattr__(receipt, "artifact_sha256", digest)
    object.__setattr__(receipt, "_validation_token", _PUBLICATION_TOKEN)
    receipt.__post_init__()
    return receipt


def _resolve_output_path(path: Path, *, allowed_root: Path | None) -> Path:
    if path.suffix != ".json" or path.name.casefold().endswith(".sha256"):
        raise AnnotationArtifactError("Artifact output must be a JSON file")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise AnnotationArtifactError("Artifact output parent must exist") from exc
    resolved = parent / path.name
    _require_under_root(resolved, allowed_root)
    return resolved


def _resolve_existing_path(path: Path, *, allowed_root: Path | None) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise AnnotationArtifactError("Annotation artifact does not exist") from exc
    _require_under_root(resolved, allowed_root)
    return resolved


def _require_under_root(path: Path, allowed_root: Path | None) -> None:
    if allowed_root is None:
        return
    try:
        path.relative_to(Path(allowed_root).resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise AnnotationArtifactError(
            "Annotation artifact must stay inside its configured root"
        ) from exc


def _require_compatible_or_absent(
    path: Path,
    expected: bytes,
    *,
    max_bytes: int,
    label: str,
) -> None:
    if not os.path.lexists(path):
        return
    _require_exact_regular_bytes_after_publication_race(
        path,
        expected,
        max_bytes=max_bytes,
        label=label,
    )


def _require_exact_regular_bytes_after_publication_race(
    path: Path,
    expected: bytes,
    *,
    max_bytes: int,
    label: str,
) -> None:
    """Reconcile only a short atomic-install race, then fail closed."""

    deadline = time.monotonic() + 0.5
    while True:
        try:
            _require_exact_regular_bytes(
                path,
                expected,
                max_bytes=max_bytes,
                label=label,
            )
            return
        except AnnotationArtifactError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.005)


def _preflight_canonical_artifact_bytes(
    path: Path,
    artifact: BaseModel,
    *,
    allowed_root: Path | None,
    max_bytes: int,
    reject_private_fields: bool,
) -> tuple[Path, bytes, str, Path, bytes]:
    resolved_path = _resolve_output_path(path, allowed_root=allowed_root)
    if reject_private_fields:
        _reject_forbidden_public_keys(
            artifact.model_dump(mode="json", exclude_none=False)
        )
    payload = canonical_json_bytes(artifact)
    if not 1 <= len(payload) <= max_bytes:
        raise AnnotationArtifactError(
            f"Annotation artifact must contain 1..{max_bytes} bytes"
        )
    digest = hashlib.sha256(payload).hexdigest()
    sidecar_path = resolved_path.with_suffix(".sha256")
    sidecar = f"{digest}  {resolved_path.name}\n".encode("utf-8")
    _require_compatible_or_absent(
        resolved_path,
        payload,
        max_bytes=max_bytes,
        label="annotation artifact",
    )
    _require_compatible_or_absent(
        sidecar_path,
        sidecar,
        max_bytes=1024,
        label="annotation artifact sidecar",
    )
    return resolved_path, payload, digest, sidecar_path, sidecar


def _require_exact_regular_bytes(
    path: Path,
    expected: bytes,
    *,
    max_bytes: int,
    label: str,
) -> None:
    try:
        existing = read_bounded_regular_bytes(
            path,
            max_bytes=max_bytes,
            label=label,
        )
    except CanonicalArtifactError as exc:
        raise AnnotationArtifactError(f"Unsafe or invalid {label}") from exc
    if existing != expected:
        raise AnnotationArtifactError(f"Conflicting immutable {label}")


def _write_exclusive_durable(path: Path, payload: bytes) -> None:
    """Install complete bytes atomically without exposing a partial target."""

    staging = path.with_name(
        f".{path.name}.{secrets.token_hex(16)}.publishing"
    )
    descriptor = -1
    installed = False
    try:
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(staging, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "nt":
            os.rename(staging, path)
        else:
            os.link(staging, path, follow_symlinks=False)
            os.unlink(staging)
        installed = True
        _fsync_parent_directory(path.parent)
    except FileExistsError:
        raise _PublicationRaceError(
            f"Artifact publication raced with another writer: {path.name}"
        ) from None
    except OSError as exc:
        raise AnnotationArtifactError(
            f"Cannot durably publish artifact: {path.name}"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not installed and os.path.lexists(staging):
            try:
                os.unlink(staging)
            except OSError as exc:
                raise AnnotationArtifactError(
                    "Cannot clean failed artifact publication staging file"
                ) from exc


def _fsync_parent_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    descriptor = -1
    try:
        descriptor = os.open(
            directory,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        os.fsync(descriptor)
    except OSError as exc:
        raise AnnotationArtifactError(
            "Cannot durably publish artifact directory entry"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _require_gitignored(repository_root: Path, path: Path) -> None:
    root = Path(repository_root).resolve(strict=True)
    try:
        relative = path.resolve(strict=False).relative_to(root).as_posix()
    except (OSError, ValueError) as exc:
        raise AnnotationArtifactError(
            "Private annotation artifacts must stay inside the repository"
        ) from exc
    if any(character in relative for character in "*?[]") or relative.startswith(":"):
        raise AnnotationArtifactError(
            "Private annotation path contains unsupported Git path syntax"
        )
    try:
        git_executable = resolve_trusted_git_executable()
        environment = minimal_git_environment()
    except TrustedGitError as exc:
        raise AnnotationArtifactError(
            "Cannot verify the private annotation Git boundary"
        ) from exc
    try:
        top_level = subprocess.run(
            [
                git_executable,
                "--literal-pathspecs",
                "-C",
                str(root),
                "rev-parse",
                "--show-toplevel",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            shell=False,
            env=environment,
        )
        if top_level.returncode != 0:
            raise AnnotationArtifactError(
                "Private annotation root must be a valid Git worktree"
            )
        try:
            actual_root = Path(
                top_level.stdout.decode("utf-8", errors="strict").strip()
            ).resolve(strict=True)
        except (OSError, UnicodeError, ValueError) as exc:
            raise AnnotationArtifactError(
                "Cannot verify the private annotation Git worktree"
            ) from exc
        if actual_root != root:
            raise AnnotationArtifactError(
                "Private annotation Git worktree does not match repository root"
            )

        check_ignore_environment = dict(environment)
        # Git check-ignore does not accept literal pathspec magic.  The path
        # grammar above excludes pathspec metacharacters instead.
        check_ignore_environment.pop("GIT_LITERAL_PATHSPECS", None)
        ignored = subprocess.run(
            [
                git_executable,
                "-C",
                str(root),
                "check-ignore",
                "--quiet",
                "--",
                relative,
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            shell=False,
            env=check_ignore_environment,
        )
        tracked = subprocess.run(
            [
                git_executable,
                "--literal-pathspecs",
                "-C",
                str(root),
                "ls-files",
                "--error-unmatch",
                "--",
                relative,
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            shell=False,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AnnotationArtifactError(
            "Cannot verify the private annotation Git boundary"
        ) from exc
    if ignored.returncode != 0 or tracked.returncode == 0:
        raise AnnotationArtifactError(
            "Private annotation artifacts must be ignored and untracked"
        )
    if tracked.returncode not in (0, 1):
        raise AnnotationArtifactError(
            "Cannot verify whether the private annotation artifact is tracked"
        )
def _is_lower_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _reject_forbidden_public_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() in _FORBIDDEN_PUBLIC_KEYS:
                raise AnnotationArtifactError(
                    f"Forbidden public annotation field: {key}"
                )
            _reject_forbidden_public_keys(child)
    elif isinstance(value, list):
        for child in value:
            _reject_forbidden_public_keys(child)
