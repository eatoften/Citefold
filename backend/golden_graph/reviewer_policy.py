"""Repository-governed trust roots for reviewer SSH attestations.

An SSH signature proves possession of a private key.  It proves that the key
is authorized for a reviewer only when the corresponding public-key policy was
registered independently of the signature being checked.  This module makes a
tracked historical Git blob that independent trust root.

The policy and authority deliberately do not prove that a signer is human or
that the signed annotation is correct.  They bind one repository-registered
key, reviewer identifier, protocol, and set of SSHSIG namespaces.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import subprocess
from typing import Literal, Mapping

from pydantic import ConfigDict, BaseModel, Field, field_validator, model_validator

from .annotation_artifacts import AnnotationArtifactError, publish_canonical_artifact
from .canonical_io import canonical_json_bytes, read_bounded_regular_bytes
from .schemas import JsonArrayTuple, SAFE_ID_PATTERN, SHA256_PATTERN
from .trusted_git import (
    TrustedGitError,
    minimal_git_environment,
    resolve_trusted_git_executable,
)


REVIEWER_POLICY_DIRECTORY = PurePosixPath(
    "backend/golden_graph/attestations"
)
MAX_REVIEWER_POLICY_BYTES = 256 * 1024

_POLICY_AUTHORITY_TOKEN = object()
_FULL_GIT_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_LOWER_SHA256 = re.compile(SHA256_PATTERN)
_NAMESPACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FINGERPRINT = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")
_SAFE_ID = re.compile(SAFE_ID_PATTERN)
_SSH_ED25519 = b"ssh-ed25519"


class ReviewerKeyPolicyError(ValueError):
    """Raised when a reviewer trust root is invalid or not repository-owned."""


class ReviewerKeyPolicy(BaseModel):
    """One public, versioned reviewer-key registration.

    ``allowed_signers_policy_utf8`` is intentionally exactly one canonical
    OpenSSH allowed-signers line with no options or comment.  The line names
    the protocol reviewer and contains one structurally valid Ed25519 key.
    """

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        hide_input_in_errors=True,
    )

    schema_version: Literal[1]
    artifact_role: Literal["golden_graph_reviewer_key_policy"]
    registration_mode: Literal["repository_history"]
    protocol_id: str = Field(pattern=SAFE_ID_PATTERN)
    frozen_protocol_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewer_id: str = Field(pattern=SAFE_ID_PATTERN)
    allowed_namespaces: JsonArrayTuple[str] = Field(min_length=1, max_length=8)
    allowed_signers_policy_utf8: str = Field(
        min_length=1,
        max_length=4_096,
        repr=False,
    )
    allowed_signers_sha256: str = Field(pattern=SHA256_PATTERN)
    public_key_fingerprint: str = Field(pattern=r"^SHA256:[A-Za-z0-9+/]{43}$")
    signature_proves_registered_key_control_only: Literal[True]
    signature_does_not_prove_reviewer_humanity: Literal[True]

    @field_validator("allowed_namespaces")
    @classmethod
    def canonical_allowed_namespaces(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if values != tuple(sorted(values)) or len(values) != len(set(values)):
            raise ValueError("Allowed namespaces must be sorted and unique")
        if any(_NAMESPACE.fullmatch(value) is None for value in values):
            raise ValueError("Allowed namespace is not a safe SSHSIG namespace")
        return values

    @model_validator(mode="after")
    def exact_key_policy_binding(self) -> "ReviewerKeyPolicy":
        expected_identity, public_blob = _parse_allowed_signers_line(
            self.allowed_signers_policy_utf8
        )
        if expected_identity != self.reviewer_id:
            raise ValueError("Allowed-signers principal must equal reviewer_id")
        policy_bytes = self.allowed_signers_policy_utf8.encode("ascii")
        if hashlib.sha256(policy_bytes).hexdigest() != self.allowed_signers_sha256:
            raise ValueError("Allowed-signers policy hash is inconsistent")
        expected_fingerprint = _public_key_fingerprint(public_blob)
        if self.public_key_fingerprint != expected_fingerprint:
            raise ValueError("Reviewer public-key fingerprint is inconsistent")
        return self


@dataclass(frozen=True, slots=True, init=False)
class ReviewerKeyPolicyAuthority:
    """Token-gated receipt for a policy recorded in reachable Git history.

    The receipt establishes exact policy bytes at ``registration_commit_sha``
    and their equality with the canonical local artifact.  It is not a human
    identity or annotation-quality authority.
    """

    policy: ReviewerKeyPolicy
    artifact_path: Path
    policy_sha256: str
    registration_commit_sha: str
    verified_head_sha: str
    policy_blob_oid: str
    active_at_verified_head: bool
    _validation_token: object = field(repr=False, compare=False)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError(
            "ReviewerKeyPolicyAuthority must come from its repository loader"
        )

    def __post_init__(self) -> None:
        if self._validation_token is not _POLICY_AUTHORITY_TOKEN:
            raise ValueError("Invalid reviewer-key policy authority token")
        if not self.artifact_path.is_absolute():
            raise ValueError("Reviewer-key policy path must be absolute")
        if _LOWER_SHA256.fullmatch(self.policy_sha256) is None:
            raise ValueError("Reviewer-key policy SHA-256 is invalid")
        if hashlib.sha256(canonical_json_bytes(self.policy)).hexdigest() != (
            self.policy_sha256
        ):
            raise ValueError("Reviewer-key policy authority hash is inconsistent")
        for label, value in (
            ("registration commit", self.registration_commit_sha),
            ("verified HEAD", self.verified_head_sha),
            ("policy blob", self.policy_blob_oid),
        ):
            if _FULL_GIT_SHA1.fullmatch(value) is None:
                raise ValueError(f"Reviewer-key {label} must be a full Git SHA-1")
        if type(self.active_at_verified_head) is not bool:
            raise ValueError(
                "Reviewer-key policy active capability fact must be boolean"
            )


def build_reviewer_key_policy(
    *,
    protocol_id: str,
    frozen_protocol_sha256: str,
    reviewer_id: str,
    allowed_signers_policy_utf8: str,
    allowed_namespaces: tuple[str, ...],
) -> ReviewerKeyPolicy:
    """Build a strict policy while deriving hashes and key fingerprint.

    This helper consumes public-key policy text only; it never reads, creates,
    or handles a private key.
    """

    _require_safe_id(protocol_id, "protocol_id")
    _require_sha256(frozen_protocol_sha256, "frozen_protocol_sha256")
    _require_safe_id(reviewer_id, "reviewer_id")
    identity, public_blob = _parse_allowed_signers_line(
        allowed_signers_policy_utf8
    )
    if identity != reviewer_id:
        raise ReviewerKeyPolicyError(
            "Allowed-signers principal must equal reviewer_id"
        )
    policy_bytes = allowed_signers_policy_utf8.encode("ascii")
    try:
        return ReviewerKeyPolicy(
            schema_version=1,
            artifact_role="golden_graph_reviewer_key_policy",
            registration_mode="repository_history",
            protocol_id=protocol_id,
            frozen_protocol_sha256=frozen_protocol_sha256,
            reviewer_id=reviewer_id,
            allowed_namespaces=allowed_namespaces,
            allowed_signers_policy_utf8=allowed_signers_policy_utf8,
            allowed_signers_sha256=hashlib.sha256(policy_bytes).hexdigest(),
            public_key_fingerprint=_public_key_fingerprint(public_blob),
            signature_proves_registered_key_control_only=True,
            signature_does_not_prove_reviewer_humanity=True,
        )
    except (TypeError, ValueError) as exc:
        raise ReviewerKeyPolicyError("Reviewer-key policy is invalid") from exc


def reviewer_key_policy_path(
    repository_root: Path,
    *,
    protocol_id: str,
    reviewer_id: str,
) -> Path:
    """Return the sole repository path for one protocol/reviewer policy."""

    _require_safe_id(protocol_id, "protocol_id")
    _require_safe_id(reviewer_id, "reviewer_id")
    root = Path(repository_root).resolve(strict=True)
    filename = f"{protocol_id}.{reviewer_id}.reviewer-key-policy.json"
    return root.joinpath(*REVIEWER_POLICY_DIRECTORY.parts, filename)


def publish_reviewer_key_policy(
    *,
    repository_root: Path,
    policy: ReviewerKeyPolicy,
) -> str:
    """Publish a canonical policy and sidecar without overwriting conflicts.

    Publication alone issues no trust authority.  A caller must commit both
    files and later use :func:`load_repository_reviewer_key_policy` with that
    full commit SHA.
    """

    root, _head, _environment = _require_repository_top_level(repository_root)
    try:
        policy = ReviewerKeyPolicy.model_validate(
            policy.model_dump(mode="python", exclude_none=False)
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise ReviewerKeyPolicyError("Reviewer-key policy is invalid") from exc
    directory = root.joinpath(*REVIEWER_POLICY_DIRECTORY.parts)
    _create_policy_directory(root, directory)
    path = reviewer_key_policy_path(
        root,
        protocol_id=policy.protocol_id,
        reviewer_id=policy.reviewer_id,
    )
    try:
        digest = publish_canonical_artifact(
            path,
            policy,
            allowed_root=directory,
            max_bytes=MAX_REVIEWER_POLICY_BYTES,
        )
    except AnnotationArtifactError as exc:
        raise ReviewerKeyPolicyError(
            "Reviewer-key policy could not be published safely"
        ) from exc
    _require_safe_repository_leaf(root, path)
    _require_safe_repository_leaf(root, path.with_suffix(".sha256"))
    return digest


def load_repository_reviewer_key_policy(
    *,
    repository_root: Path,
    protocol_id: str,
    frozen_protocol_sha256: str,
    reviewer_id: str,
    registration_commit_sha: str,
) -> ReviewerKeyPolicyAuthority:
    """Issue active authority only for an unchanged current policy.

    Registration history is necessary but not sufficient for new signing
    authority.  The exact registered policy and sidecar must also remain in
    current ``HEAD``, the index, and the working tree.  Removing either file
    from current repository state therefore revokes the policy for new work
    without making historical signatures unverifiable.
    """

    historical = load_historical_reviewer_key_policy(
        repository_root=repository_root,
        protocol_id=protocol_id,
        frozen_protocol_sha256=frozen_protocol_sha256,
        reviewer_id=reviewer_id,
        registration_commit_sha=registration_commit_sha,
    )
    root, head_before, environment = _require_repository_top_level(
        repository_root
    )
    if head_before != historical.verified_head_sha:
        raise ReviewerKeyPolicyError(
            "Repository HEAD changed during reviewer-key policy verification"
        )

    path = reviewer_key_policy_path(
        root,
        protocol_id=protocol_id,
        reviewer_id=reviewer_id,
    )
    sidecar_path = path.with_suffix(".sha256")
    logical_path = path.relative_to(root).as_posix()
    logical_sidecar = sidecar_path.relative_to(root).as_posix()
    canonical_policy = canonical_json_bytes(historical.policy)
    expected_sidecar = (
        f"{historical.policy_sha256}  {path.name}\n".encode("utf-8")
    )

    _head_policy_oid, head_policy = _read_git_blob(
        root,
        commit_sha=head_before,
        logical_path=logical_path,
        environment=environment,
        max_bytes=MAX_REVIEWER_POLICY_BYTES,
    )
    _head_sidecar_oid, head_sidecar = _read_git_blob(
        root,
        commit_sha=head_before,
        logical_path=logical_sidecar,
        environment=environment,
        max_bytes=1_024,
    )
    if head_policy != canonical_policy or head_sidecar != expected_sidecar:
        raise ReviewerKeyPolicyError(
            "Current HEAD reviewer-key policy differs from its registration"
        )

    index_policy_oid, index_policy = _read_index_blob(
        root,
        logical_path=logical_path,
        environment=environment,
        max_bytes=MAX_REVIEWER_POLICY_BYTES,
    )
    index_sidecar_oid, index_sidecar = _read_index_blob(
        root,
        logical_path=logical_sidecar,
        environment=environment,
        max_bytes=1_024,
    )
    if index_policy != canonical_policy or index_sidecar != expected_sidecar:
        raise ReviewerKeyPolicyError(
            "Index reviewer-key policy differs from its registration"
        )

    _require_safe_repository_leaf(root, path)
    _require_safe_repository_leaf(root, sidecar_path)
    try:
        local_policy = read_bounded_regular_bytes(
            path,
            max_bytes=MAX_REVIEWER_POLICY_BYTES,
            label="reviewer-key policy artifact",
        )
        local_sidecar = read_bounded_regular_bytes(
            sidecar_path,
            max_bytes=1_024,
            label="reviewer-key policy sidecar",
        )
    except (OSError, ValueError) as exc:
        raise ReviewerKeyPolicyError(
            "Reviewer-key policy working tree is invalid"
        ) from exc
    if local_policy != canonical_policy or local_sidecar != expected_sidecar:
        raise ReviewerKeyPolicyError(
            "Working-tree reviewer-key policy differs from its registration"
        )

    final_index_policy_oid, _final_index_policy = _read_index_blob(
        root,
        logical_path=logical_path,
        environment=environment,
        max_bytes=MAX_REVIEWER_POLICY_BYTES,
    )
    final_index_sidecar_oid, _final_index_sidecar = _read_index_blob(
        root,
        logical_path=logical_sidecar,
        environment=environment,
        max_bytes=1_024,
    )
    if (
        final_index_policy_oid != index_policy_oid
        or final_index_sidecar_oid != index_sidecar_oid
    ):
        raise ReviewerKeyPolicyError(
            "Repository index changed during reviewer-key policy verification"
        )
    head_after = _git_hex(
        root,
        ("rev-parse", "--verify", "HEAD^{commit}"),
        environment,
        label="current repository HEAD",
    )
    if head_after != head_before:
        raise ReviewerKeyPolicyError(
            "Repository HEAD changed during reviewer-key policy verification"
        )
    return _issue_policy_authority(
        policy=historical.policy,
        artifact_path=path,
        policy_sha256=historical.policy_sha256,
        registration_commit_sha=registration_commit_sha,
        verified_head_sha=head_before,
        policy_blob_oid=historical.policy_blob_oid,
        active_at_verified_head=True,
    )


def load_historical_reviewer_key_policy(
    *,
    repository_root: Path,
    protocol_id: str,
    frozen_protocol_sha256: str,
    reviewer_id: str,
    registration_commit_sha: str,
) -> ReviewerKeyPolicyAuthority:
    """Reconstruct a policy directly from its reachable registration commit.

    This historical capability keeps old seals verifiable after a policy is
    revoked from current repository state.  It deliberately cannot authorize
    new signing work; callers needing that authority must use
    :func:`load_repository_reviewer_key_policy` and
    :func:`require_active_reviewer_key_policy`.
    """

    _require_safe_id(protocol_id, "protocol_id")
    _require_safe_id(reviewer_id, "reviewer_id")
    _require_sha256(frozen_protocol_sha256, "frozen_protocol_sha256")
    _require_full_commit(registration_commit_sha)
    root, head_before, environment = _require_repository_top_level(
        repository_root
    )
    recorded_commit = _git_hex(
        root,
        ("rev-parse", "--verify", f"{registration_commit_sha}^{{commit}}"),
        environment,
        label="reviewer-key registration commit",
    )
    if recorded_commit != registration_commit_sha:
        raise ReviewerKeyPolicyError(
            "Reviewer-key registration must name an exact commit object"
        )
    ancestor = _run_git(
        root,
        (
            "merge-base",
            "--is-ancestor",
            registration_commit_sha,
            head_before,
        ),
        environment,
        capture_stdout=False,
    )
    if ancestor.returncode != 0:
        raise ReviewerKeyPolicyError(
            "Reviewer-key registration commit is not an ancestor of current HEAD"
        )

    path = reviewer_key_policy_path(
        root,
        protocol_id=protocol_id,
        reviewer_id=reviewer_id,
    )
    sidecar_path = path.with_suffix(".sha256")
    logical_path = path.relative_to(root).as_posix()
    logical_sidecar = sidecar_path.relative_to(root).as_posix()
    blob_oid, recorded_policy = _read_git_blob(
        root,
        commit_sha=registration_commit_sha,
        logical_path=logical_path,
        environment=environment,
        max_bytes=MAX_REVIEWER_POLICY_BYTES,
    )
    _sidecar_oid, recorded_sidecar = _read_git_blob(
        root,
        commit_sha=registration_commit_sha,
        logical_path=logical_sidecar,
        environment=environment,
        max_bytes=1_024,
    )
    policy, policy_sha256 = _decode_policy_and_sidecar(
        recorded_policy,
        recorded_sidecar,
        artifact_name=path.name,
    )
    _validate_expected_policy(
        policy,
        protocol_id=protocol_id,
        frozen_protocol_sha256=frozen_protocol_sha256,
        reviewer_id=reviewer_id,
    )

    head_after = _git_hex(
        root,
        ("rev-parse", "--verify", "HEAD^{commit}"),
        environment,
        label="current repository HEAD",
    )
    if head_after != head_before:
        raise ReviewerKeyPolicyError(
            "Repository HEAD changed during reviewer-key policy verification"
        )
    return _issue_policy_authority(
        policy=policy,
        artifact_path=path,
        policy_sha256=policy_sha256,
        registration_commit_sha=registration_commit_sha,
        verified_head_sha=head_before,
        policy_blob_oid=blob_oid,
        active_at_verified_head=False,
    )


def require_active_reviewer_key_policy(
    authority: ReviewerKeyPolicyAuthority,
) -> None:
    """Fail unless a loader established current policy activation."""

    _require_valid_policy_authority(authority)
    if not authority.active_at_verified_head:
        raise ReviewerKeyPolicyError(
            "An active current-HEAD reviewer-key policy authority is required"
        )


def require_attestation_matches_reviewer_policy(
    authority: ReviewerKeyPolicyAuthority,
    *,
    signer_identity: str,
    namespace: str,
    allowed_signers_sha256: str,
    public_key_fingerprint: str | None,
) -> None:
    """Fail unless verified attestation metadata matches a registered policy.

    This is a comparison boundary, not an attestation verifier.  Callers must
    first obtain the metadata from cryptographic SSHSIG verification.
    """

    _require_valid_policy_authority(authority)
    policy = authority.policy
    if (
        signer_identity != policy.reviewer_id
        or namespace not in policy.allowed_namespaces
        or allowed_signers_sha256 != policy.allowed_signers_sha256
        or public_key_fingerprint != policy.public_key_fingerprint
    ):
        raise ReviewerKeyPolicyError(
            "Attestation metadata is not authorized by reviewer-key policy"
        )


def _require_valid_policy_authority(
    authority: ReviewerKeyPolicyAuthority,
) -> None:
    if not isinstance(authority, ReviewerKeyPolicyAuthority):
        raise ReviewerKeyPolicyError(
            "A repository-issued reviewer-key policy authority is required"
        )
    try:
        authority.__post_init__()
    except (AttributeError, TypeError, ValueError) as exc:
        raise ReviewerKeyPolicyError(
            "A repository-issued reviewer-key policy authority is required"
        ) from exc


def _validate_expected_policy(
    policy: ReviewerKeyPolicy,
    *,
    protocol_id: str,
    frozen_protocol_sha256: str,
    reviewer_id: str,
) -> None:
    if (
        policy.protocol_id != protocol_id
        or policy.frozen_protocol_sha256 != frozen_protocol_sha256
        or policy.reviewer_id != reviewer_id
    ):
        raise ReviewerKeyPolicyError(
            "Reviewer-key policy differs from expected protocol authority"
        )


def _decode_policy_and_sidecar(
    payload: bytes,
    sidecar: bytes,
    *,
    artifact_name: str,
) -> tuple[ReviewerKeyPolicy, str]:
    if not 1 <= len(payload) <= MAX_REVIEWER_POLICY_BYTES:
        raise ReviewerKeyPolicyError(
            "Registered reviewer-key policy exceeds its byte limit"
        )
    digest = hashlib.sha256(payload).hexdigest()
    expected_sidecar = f"{digest}  {artifact_name}\n".encode("utf-8")
    if sidecar != expected_sidecar:
        raise ReviewerKeyPolicyError(
            "Registered reviewer-key policy sidecar is invalid"
        )
    try:
        policy = ReviewerKeyPolicy.model_validate_json(payload, strict=True)
        if canonical_json_bytes(policy) != payload:
            raise ReviewerKeyPolicyError(
                "Registered reviewer-key policy is not canonical JSON"
            )
    except ReviewerKeyPolicyError:
        raise
    except (TypeError, ValueError) as exc:
        raise ReviewerKeyPolicyError(
            "Registered reviewer-key policy artifact is invalid"
        ) from exc
    return policy, digest


def _create_policy_directory(root: Path, directory: Path) -> None:
    parent = directory.parent
    _require_safe_repository_directory(root, parent)
    try:
        directory.mkdir(mode=0o755, exist_ok=True)
    except OSError as exc:
        raise ReviewerKeyPolicyError(
            "Cannot create reviewer-key policy directory"
        ) from exc
    _require_safe_repository_directory(root, directory)


def _require_repository_top_level(
    repository_root: Path,
) -> tuple[Path, str, dict[str, str]]:
    try:
        lexical = Path(os.path.abspath(os.fspath(repository_root)))
        root = lexical.resolve(strict=True)
    except (OSError, TypeError, ValueError) as exc:
        raise ReviewerKeyPolicyError("Repository root does not exist") from exc
    if os.path.normcase(str(lexical)) != os.path.normcase(str(root)):
        raise ReviewerKeyPolicyError("Repository root cannot traverse links")
    if not root.is_dir():
        raise ReviewerKeyPolicyError("Repository root must be a directory")
    environment = _sanitized_git_environment()
    top = _git_text(
        root,
        ("rev-parse", "--show-toplevel"),
        environment,
        label="repository top-level",
        max_bytes=16_384,
    )
    try:
        top_path = Path(top).resolve(strict=True)
    except OSError as exc:
        raise ReviewerKeyPolicyError("Git top-level does not exist") from exc
    if os.path.normcase(str(top_path)) != os.path.normcase(str(root)):
        raise ReviewerKeyPolicyError(
            "repository_root must be the expected Git top-level"
        )
    head = _git_hex(
        root,
        ("rev-parse", "--verify", "HEAD^{commit}"),
        environment,
        label="current repository HEAD",
    )
    return root, head, environment


def _sanitized_git_environment() -> dict[str, str]:
    try:
        return minimal_git_environment()
    except TrustedGitError as exc:
        raise ReviewerKeyPolicyError(
            "Cannot establish a trusted Git environment"
        ) from exc


def _run_git(
    repository_root: Path,
    command: tuple[str, ...],
    environment: Mapping[str, str],
    *,
    capture_stdout: bool,
) -> subprocess.CompletedProcess[bytes]:
    try:
        git_executable = resolve_trusted_git_executable()
        return subprocess.run(
            [git_executable, "-C", str(repository_root), *command],
            check=False,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError, TrustedGitError) as exc:
        raise ReviewerKeyPolicyError(
            "Cannot verify reviewer-key repository authority"
        ) from exc


def _git_text(
    repository_root: Path,
    command: tuple[str, ...],
    environment: Mapping[str, str],
    *,
    label: str,
    max_bytes: int = 512,
) -> str:
    completed = _run_git(
        repository_root,
        command,
        environment,
        capture_stdout=True,
    )
    if completed.returncode != 0 or len(completed.stdout) > max_bytes:
        raise ReviewerKeyPolicyError(f"Cannot resolve {label}")
    try:
        value = completed.stdout.decode("utf-8").strip()
    except UnicodeError as exc:
        raise ReviewerKeyPolicyError(f"Cannot decode {label}") from exc
    if not value:
        raise ReviewerKeyPolicyError(f"Cannot resolve {label}")
    return value


def _git_hex(
    repository_root: Path,
    command: tuple[str, ...],
    environment: Mapping[str, str],
    *,
    label: str,
) -> str:
    value = _git_text(
        repository_root,
        command,
        environment,
        label=label,
    )
    if _FULL_GIT_SHA1.fullmatch(value) is None:
        raise ReviewerKeyPolicyError(f"{label} is not a full Git SHA-1")
    return value


def _read_git_blob(
    repository_root: Path,
    *,
    commit_sha: str,
    logical_path: str,
    environment: Mapping[str, str],
    max_bytes: int,
) -> tuple[str, bytes]:
    relative = PurePosixPath(logical_path)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or "\\" in logical_path
    ):
        raise ReviewerKeyPolicyError("Reviewer-key Git path is invalid")
    object_name = _git_hex(
        repository_root,
        ("rev-parse", "--verify", f"{commit_sha}:{logical_path}"),
        environment,
        label="reviewer-key policy blob",
    )
    return object_name, _read_git_object_blob(
        repository_root,
        object_name=object_name,
        environment=environment,
        max_bytes=max_bytes,
        label="registered reviewer-key policy",
    )


def _read_git_object_blob(
    repository_root: Path,
    *,
    object_name: str,
    environment: Mapping[str, str],
    max_bytes: int,
    label: str,
) -> bytes:
    object_type = _git_text(
        repository_root,
        ("cat-file", "-t", object_name),
        environment,
        label=f"{label} blob type",
    )
    if object_type != "blob":
        raise ReviewerKeyPolicyError(f"{label} object is not a blob")
    size_text = _git_text(
        repository_root,
        ("cat-file", "-s", object_name),
        environment,
        label=f"{label} blob size",
    )
    try:
        size = int(size_text)
    except ValueError as exc:
        raise ReviewerKeyPolicyError(f"{label} blob size is invalid") from exc
    if not 1 <= size <= max_bytes:
        raise ReviewerKeyPolicyError(f"{label} blob exceeds its byte limit")
    completed = _run_git(
        repository_root,
        ("cat-file", "blob", object_name),
        environment,
        capture_stdout=True,
    )
    if completed.returncode != 0 or len(completed.stdout) != size:
        raise ReviewerKeyPolicyError(f"{label} blob is absent or unstable")
    return completed.stdout


def _read_index_blob(
    repository_root: Path,
    *,
    logical_path: str,
    environment: Mapping[str, str],
    max_bytes: int,
) -> tuple[str, bytes]:
    relative = PurePosixPath(logical_path)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or "\\" in logical_path
    ):
        raise ReviewerKeyPolicyError("Reviewer-key Git path is invalid")
    completed = _run_git(
        repository_root,
        ("ls-files", "--stage", "--", logical_path),
        environment,
        capture_stdout=True,
    )
    if completed.returncode != 0 or len(completed.stdout) > 4_096:
        raise ReviewerKeyPolicyError(
            "Cannot resolve reviewer-key policy index entry"
        )
    try:
        row = completed.stdout.decode("ascii")
    except UnicodeError as exc:
        raise ReviewerKeyPolicyError(
            "Reviewer-key policy index entry is invalid"
        ) from exc
    match = re.fullmatch(
        rf"100644 ([0-9a-f]{{40}}) 0\t{re.escape(logical_path)}\n?",
        row,
    )
    if match is None:
        raise ReviewerKeyPolicyError(
            "Reviewer-key policy must be an ordinary stage-zero index file"
        )
    object_name = match.group(1)
    return object_name, _read_git_object_blob(
        repository_root,
        object_name=object_name,
        environment=environment,
        max_bytes=max_bytes,
        label="reviewer-key policy index",
    )


def _require_safe_repository_directory(root: Path, path: Path) -> None:
    _require_safe_repository_entry(root, path, require_directory=True)


def _require_safe_repository_leaf(root: Path, path: Path) -> None:
    _require_safe_repository_entry(root, path, require_directory=False)


def _require_safe_repository_entry(
    root: Path,
    path: Path,
    *,
    require_directory: bool,
) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ReviewerKeyPolicyError(
            "Reviewer-key policy path escaped repository root"
        ) from exc
    current = root
    try:
        for index, part in enumerate(relative.parts):
            current = current / part
            metadata = current.lstat()
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            if stat.S_ISLNK(metadata.st_mode) or (
                reparse_flag
                and getattr(metadata, "st_file_attributes", 0) & reparse_flag
            ):
                raise ReviewerKeyPolicyError(
                    "Reviewer-key policy path cannot traverse links"
                )
            final = index == len(relative.parts) - 1
            if (not final or require_directory) and not stat.S_ISDIR(
                metadata.st_mode
            ):
                raise ReviewerKeyPolicyError(
                    "Reviewer-key policy parent must be a directory"
                )
            if final and not require_directory:
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                    raise ReviewerKeyPolicyError(
                        "Reviewer-key policy leaf must be a single-link regular file"
                    )
        current.resolve(strict=True).relative_to(root)
    except ReviewerKeyPolicyError:
        raise
    except (OSError, ValueError) as exc:
        raise ReviewerKeyPolicyError(
            "Reviewer-key policy path is absent or unsafe"
        ) from exc


def _parse_allowed_signers_line(value: str) -> tuple[str, bytes]:
    if not isinstance(value, str):
        raise ReviewerKeyPolicyError("Allowed-signers policy must be text")
    try:
        encoded = value.encode("ascii")
    except UnicodeError as exc:
        raise ReviewerKeyPolicyError(
            "Allowed-signers policy must be canonical ASCII"
        ) from exc
    if not encoded.endswith(b"\n") or encoded.count(b"\n") != 1:
        raise ReviewerKeyPolicyError(
            "Allowed-signers policy must contain exactly one newline-terminated row"
        )
    parts = encoded[:-1].split(b" ")
    if len(parts) != 3 or any(not part for part in parts):
        raise ReviewerKeyPolicyError(
            "Allowed-signers row must contain principal, type, and key only"
        )
    identity_bytes, algorithm, encoded_blob = parts
    try:
        identity = identity_bytes.decode("ascii")
    except UnicodeError as exc:
        raise ReviewerKeyPolicyError(
            "Allowed-signers principal is not ASCII"
        ) from exc
    if _SAFE_ID.fullmatch(identity) is None or algorithm != _SSH_ED25519:
        raise ReviewerKeyPolicyError(
            "Allowed-signers row must name the reviewer and one Ed25519 key"
        )
    try:
        public_blob = base64.b64decode(encoded_blob, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ReviewerKeyPolicyError(
            "Allowed-signers Ed25519 key is invalid Base64"
        ) from exc
    if base64.b64encode(public_blob) != encoded_blob:
        raise ReviewerKeyPolicyError(
            "Allowed-signers Ed25519 key must use canonical Base64"
        )
    try:
        embedded_algorithm, offset = _read_ssh_string(public_blob, 0)
        public_key, offset = _read_ssh_string(public_blob, offset)
    except ValueError as exc:
        raise ReviewerKeyPolicyError(
            "Allowed-signers Ed25519 key blob is malformed"
        ) from exc
    if (
        embedded_algorithm != _SSH_ED25519
        or len(public_key) != 32
        or offset != len(public_blob)
    ):
        raise ReviewerKeyPolicyError(
            "Allowed-signers key is not a canonical Ed25519 public key"
        )
    return identity, public_blob


def _read_ssh_string(payload: bytes, offset: int) -> tuple[bytes, int]:
    if offset + 4 > len(payload):
        raise ValueError("truncated SSH string length")
    length = struct.unpack(">I", payload[offset : offset + 4])[0]
    start = offset + 4
    end = start + length
    if end > len(payload):
        raise ValueError("truncated SSH string body")
    return payload[start:end], end


def _public_key_fingerprint(public_blob: bytes) -> str:
    encoded = base64.b64encode(hashlib.sha256(public_blob).digest())
    fingerprint = f"SHA256:{encoded.decode('ascii').rstrip('=')}"
    if _FINGERPRINT.fullmatch(fingerprint) is None:  # defensive invariant
        raise ReviewerKeyPolicyError("OpenSSH public-key fingerprint is invalid")
    return fingerprint


def _require_safe_id(value: str, label: str) -> None:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise ReviewerKeyPolicyError(f"{label} must use the safe-id grammar")


def _require_sha256(value: str, label: str) -> None:
    if not isinstance(value, str) or _LOWER_SHA256.fullmatch(value) is None:
        raise ReviewerKeyPolicyError(f"{label} must be a lowercase SHA-256")


def _require_full_commit(value: str) -> None:
    if (
        not isinstance(value, str)
        or value == "0" * 40
        or _FULL_GIT_SHA1.fullmatch(value) is None
    ):
        raise ReviewerKeyPolicyError(
            "registration_commit_sha must be a non-placeholder full Git SHA-1"
        )


def _issue_policy_authority(
    *,
    policy: ReviewerKeyPolicy,
    artifact_path: Path,
    policy_sha256: str,
    registration_commit_sha: str,
    verified_head_sha: str,
    policy_blob_oid: str,
    active_at_verified_head: bool,
) -> ReviewerKeyPolicyAuthority:
    authority = object.__new__(ReviewerKeyPolicyAuthority)
    object.__setattr__(authority, "policy", policy)
    object.__setattr__(authority, "artifact_path", artifact_path)
    object.__setattr__(authority, "policy_sha256", policy_sha256)
    object.__setattr__(
        authority,
        "registration_commit_sha",
        registration_commit_sha,
    )
    object.__setattr__(authority, "verified_head_sha", verified_head_sha)
    object.__setattr__(authority, "policy_blob_oid", policy_blob_oid)
    object.__setattr__(
        authority,
        "active_at_verified_head",
        active_at_verified_head,
    )
    object.__setattr__(authority, "_validation_token", _POLICY_AUTHORITY_TOKEN)
    authority.__post_init__()
    return authority


__all__ = [
    "MAX_REVIEWER_POLICY_BYTES",
    "REVIEWER_POLICY_DIRECTORY",
    "ReviewerKeyPolicy",
    "ReviewerKeyPolicyAuthority",
    "ReviewerKeyPolicyError",
    "build_reviewer_key_policy",
    "load_historical_reviewer_key_policy",
    "load_repository_reviewer_key_policy",
    "publish_reviewer_key_policy",
    "require_active_reviewer_key_policy",
    "require_attestation_matches_reviewer_policy",
    "reviewer_key_policy_path",
]
