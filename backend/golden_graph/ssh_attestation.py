"""Verify an external maintainer's detached OpenSSH attestation.

This module establishes control of a key authorized for one signer identity.
It does not prove that the signer is human, that a review happened, or that the
signed statement is true.  Those claims require separate workflow evidence.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
from typing import Any

from .canonical_io import (
    CanonicalArtifactError,
    canonical_json_bytes,
    read_bounded_regular_bytes,
)


MAX_ATTESTATION_CHALLENGE_BYTES = 2 * 1024 * 1024
MAX_ALLOWED_SIGNERS_BYTES = 256 * 1024
MAX_SSH_SIGNATURE_BYTES = 64 * 1024
DEFAULT_VERIFY_TIMEOUT_SECONDS = 10.0

_DEFAULT_SSH_KEYGEN_SENTINEL = "ssh-keygen"
_TRUSTED_UNIX_SSH_KEYGEN_PATHS = (
    Path("/usr/bin/ssh-keygen"),
    Path("/bin/ssh-keygen"),
    Path("/usr/local/bin/ssh-keygen"),
)

_ATTESTATION_TOKEN = object()
_LOWER_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_NAMESPACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SIGNER_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+:-]{0,254}$")
_SSH_FINGERPRINT = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")
_SSH_ARMOR_BODY = re.compile(rb"^[A-Za-z0-9+/=]+$")
_SSH_SIGNATURE_BEGIN = b"-----BEGIN SSH SIGNATURE-----"
_SSH_SIGNATURE_END = b"-----END SSH SIGNATURE-----"
_SSH_SIGNATURE_MAGIC = b"SSHSIG"


class ExternalMaintainerAttestationError(ValueError):
    """Raised when key-control evidence cannot be verified safely."""


@dataclass(frozen=True, slots=True, init=False)
class ExternalMaintainerAttestationReceipt:
    """Token-gated receipt proving authorized signing-key control only.

    In particular, the receipt is not proof that ``signer_identity`` names a
    human reviewer.  It records the identity OpenSSH matched in the supplied
    ``allowed_signers`` policy and the exact bytes covered by that check.
    """

    signer_identity: str
    namespace: str
    challenge_sha256: str
    allowed_signers_sha256: str
    signature_sha256: str
    public_key_fingerprint: str | None
    _validation_token: object = field(repr=False, compare=False)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError(
            "ExternalMaintainerAttestationReceipt must come from its verifier"
        )

    def __post_init__(self) -> None:
        if self._validation_token is not _ATTESTATION_TOKEN:
            raise ValueError("Invalid external attestation receipt token")
        _validate_namespace(self.namespace)
        _validate_signer_identity(self.signer_identity)
        for label, digest in (
            ("challenge_sha256", self.challenge_sha256),
            ("allowed_signers_sha256", self.allowed_signers_sha256),
            ("signature_sha256", self.signature_sha256),
        ):
            if _LOWER_SHA256.fullmatch(digest) is None:
                raise ValueError(f"{label} must be a lowercase SHA-256 digest")
        if (
            self.public_key_fingerprint is not None
            and _SSH_FINGERPRINT.fullmatch(self.public_key_fingerprint) is None
        ):
            raise ValueError("public_key_fingerprint is not an OpenSSH SHA256")


def verify_external_maintainer_attestation(
    *,
    challenge_bytes: bytes,
    namespace: str,
    expected_signer_identity: str,
    allowed_signers_path: Path,
    signature_path: Path,
    ssh_keygen_executable: str | Path = _DEFAULT_SSH_KEYGEN_SENTINEL,
    timeout_seconds: float = DEFAULT_VERIFY_TIMEOUT_SECONDS,
) -> ExternalMaintainerAttestationReceipt:
    """Verify exact canonical challenge bytes with an external OpenSSH key.

    ``namespace`` supplies OpenSSH SSHSIG domain separation and
    ``expected_signer_identity`` is matched by OpenSSH against the supplied
    ``allowed_signers`` policy.  Success proves only possession of an allowed
    private key.  It is not evidence that the signer is a human reviewer.

    The two authority files are read through stable regular-file descriptors,
    copied into a private temporary directory, and only those snapshots are
    passed to ``ssh-keygen``.  Consequently a later path replacement cannot
    change the policy or signature checked by the subprocess.

    Production callers must leave ``ssh_keygen_executable`` at its default.
    A non-default value is an explicitly absolute, test-only injection seam;
    it must never be derived from ``PATH`` or untrusted application input.
    """

    canonical_challenge = _require_canonical_challenge(challenge_bytes)
    verified_namespace = _validate_namespace(namespace)
    verified_identity = _validate_signer_identity(expected_signer_identity)
    timeout = _validate_timeout(timeout_seconds)

    allowed_path, allowed_signers = _read_resolved_authority_file(
        allowed_signers_path,
        max_bytes=MAX_ALLOWED_SIGNERS_BYTES,
        label="allowed signers file",
    )
    signature_resolved_path, signature = _read_resolved_authority_file(
        signature_path,
        max_bytes=MAX_SSH_SIGNATURE_BYTES,
        label="detached SSH signature",
    )
    # Both names are intentionally retained until after their stable reads.
    # This also makes accidental use of a mutable caller path in the command
    # below conspicuous during review.
    del allowed_path, signature_resolved_path

    if b"\x00" in allowed_signers:
        raise ExternalMaintainerAttestationError(
            "Allowed signers file cannot contain NUL bytes"
        )
    fingerprint = _signature_public_key_fingerprint(
        signature,
        expected_namespace=verified_namespace,
    )
    executable = _resolve_ssh_keygen(ssh_keygen_executable)

    try:
        with tempfile.TemporaryDirectory(
            prefix="vcc-ssh-attestation-"
        ) as temporary_directory:
            temporary_root = Path(temporary_directory)
            _make_directory_private(temporary_root)
            allowed_snapshot = temporary_root / "allowed_signers"
            signature_snapshot = temporary_root / "attestation.sig"
            _write_private_snapshot(allowed_snapshot, allowed_signers)
            _write_private_snapshot(signature_snapshot, signature)

            completed = subprocess.run(
                [
                    executable,
                    "-Y",
                    "verify",
                    "-f",
                    str(allowed_snapshot),
                    "-I",
                    verified_identity,
                    "-n",
                    verified_namespace,
                    "-s",
                    str(signature_snapshot),
                ],
                input=canonical_challenge,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                shell=False,
                timeout=timeout,
                env=_sanitized_environment(),
            )
    except subprocess.TimeoutExpired as exc:
        raise ExternalMaintainerAttestationError(
            "OpenSSH attestation verification timed out"
        ) from exc
    except OSError as exc:
        raise ExternalMaintainerAttestationError(
            "OpenSSH attestation verifier could not be executed"
        ) from exc

    if completed.returncode != 0:
        raise ExternalMaintainerAttestationError(
            "Detached OpenSSH attestation did not verify"
        )

    return _issue_receipt(
        signer_identity=verified_identity,
        namespace=verified_namespace,
        challenge_sha256=_sha256(canonical_challenge),
        allowed_signers_sha256=_sha256(allowed_signers),
        signature_sha256=_sha256(signature),
        public_key_fingerprint=fingerprint,
    )


def _require_canonical_challenge(challenge_bytes: bytes) -> bytes:
    if not isinstance(challenge_bytes, bytes):
        raise ExternalMaintainerAttestationError(
            "Attestation challenge must be exact bytes"
        )
    if not 1 <= len(challenge_bytes) <= MAX_ATTESTATION_CHALLENGE_BYTES:
        raise ExternalMaintainerAttestationError(
            "Attestation challenge is empty or exceeds its byte limit"
        )
    try:
        decoded = json.loads(
            challenge_bytes.decode("utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_nonfinite_number,
        )
        if not isinstance(decoded, dict):
            raise ExternalMaintainerAttestationError(
                "Attestation challenge must be a canonical JSON object"
            )
        if canonical_json_bytes(decoded) != challenge_bytes:
            raise ExternalMaintainerAttestationError(
                "Attestation challenge must use exact canonical JSON bytes"
            )
    except ExternalMaintainerAttestationError:
        raise
    except (
        CanonicalArtifactError,
        UnicodeError,
        ValueError,
        TypeError,
        RecursionError,
        OverflowError,
    ) as exc:
        raise ExternalMaintainerAttestationError(
            "Attestation challenge is not canonical UTF-8 JSON"
        ) from exc
    return challenge_bytes


def _read_resolved_authority_file(
    path: Path,
    *,
    max_bytes: int,
    label: str,
) -> tuple[Path, bytes]:
    try:
        lexical_path = Path(os.path.abspath(os.fspath(path)))
        resolved_path = lexical_path.resolve(strict=True)
        if os.path.normcase(str(resolved_path)) != os.path.normcase(
            str(lexical_path)
        ):
            raise ExternalMaintainerAttestationError(
                f"{label} cannot traverse symbolic links or reparse points"
            )
        payload = read_bounded_regular_bytes(
            lexical_path,
            max_bytes=max_bytes,
            label=label,
        )
    except ExternalMaintainerAttestationError:
        raise
    except (CanonicalArtifactError, OSError, TypeError, ValueError) as exc:
        raise ExternalMaintainerAttestationError(
            f"Cannot safely read {label}"
        ) from exc
    return resolved_path, payload


def _validate_namespace(namespace: str) -> str:
    if not isinstance(namespace, str) or _NAMESPACE.fullmatch(namespace) is None:
        raise ExternalMaintainerAttestationError(
            "OpenSSH namespace must be a safe 1..128 character ASCII token"
        )
    return namespace


def _validate_signer_identity(identity: str) -> str:
    if (
        not isinstance(identity, str)
        or _SIGNER_IDENTITY.fullmatch(identity) is None
    ):
        raise ExternalMaintainerAttestationError(
            "Expected signer identity must be a safe 1..255 character ASCII token"
        )
    return identity


def _validate_timeout(timeout_seconds: float) -> float:
    if isinstance(timeout_seconds, bool) or not isinstance(
        timeout_seconds, (int, float)
    ):
        raise ExternalMaintainerAttestationError(
            "Verification timeout must be a number"
        )
    timeout = float(timeout_seconds)
    if not 0.1 <= timeout <= 60.0:
        raise ExternalMaintainerAttestationError(
            "Verification timeout must be between 0.1 and 60 seconds"
        )
    return timeout


def _resolve_ssh_keygen(executable: str | Path) -> str:
    candidate = os.fspath(executable)
    if not candidate or "\x00" in candidate:
        raise ExternalMaintainerAttestationError(
            "ssh-keygen executable is invalid"
        )

    if candidate == _DEFAULT_SSH_KEYGEN_SENTINEL:
        trusted_candidates = (
            (_windows_system_directory() / "OpenSSH" / "ssh-keygen.exe",)
            if os.name == "nt"
            else _TRUSTED_UNIX_SSH_KEYGEN_PATHS
        )
        for trusted_candidate in trusted_candidates:
            resolved = _resolve_regular_executable(trusted_candidate)
            if resolved is not None:
                return resolved
        raise ExternalMaintainerAttestationError(
            "OpenSSH ssh-keygen is unavailable at a trusted system path"
        )

    # Test-only override.  Requiring an absolute path makes it impossible for
    # a hostile PATH to reinterpret even an intentionally injected command.
    explicit = Path(candidate)
    if not explicit.is_absolute():
        raise ExternalMaintainerAttestationError(
            "The test-only ssh-keygen override must be an absolute path"
        )
    resolved = _resolve_regular_executable(explicit)
    if resolved is None:
        raise ExternalMaintainerAttestationError(
            "The test-only ssh-keygen override is not a regular file"
        )
    return resolved


def _resolve_regular_executable(candidate: Path) -> str | None:
    try:
        resolved = candidate.resolve(strict=True)
        if not resolved.is_file():
            return None
        return str(resolved)
    except OSError:
        return None


def _windows_system_directory() -> Path:
    """Resolve System32 through the OS, never through inherited environment."""

    if os.name != "nt":
        raise ExternalMaintainerAttestationError(
            "The Windows system directory is unavailable on this platform"
        )
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(32_768)
        get_system_directory = ctypes.windll.kernel32.GetSystemDirectoryW
        length = get_system_directory(
            buffer,
            len(buffer),
        )
        if length <= 0 or length >= len(buffer):
            raise OSError("GetSystemDirectoryW failed")
        resolved = Path(buffer.value).resolve(strict=True)
        if not resolved.is_dir():
            raise OSError("Windows system directory is not a directory")
        return resolved
    except (AttributeError, OSError, ValueError) as exc:
        raise ExternalMaintainerAttestationError(
            "Cannot resolve the trusted Windows system directory"
        ) from exc


def _windows_program_data_directory() -> Path:
    """Resolve the machine-wide ProgramData directory from trusted HKLM."""

    if os.name != "nt":
        raise ExternalMaintainerAttestationError(
            "The Windows ProgramData directory is unavailable on this platform"
        )
    try:
        import winreg

        key_path = (
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
        )
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            raw_value, _value_type = winreg.QueryValueEx(key, "Common AppData")
        if not isinstance(raw_value, str) or not raw_value:
            raise OSError("Common AppData is not a path")
        resolved = Path(raw_value).resolve(strict=True)
        if not resolved.is_dir():
            raise OSError("ProgramData is not a directory")
        return resolved
    except (ImportError, OSError, ValueError) as exc:
        raise ExternalMaintainerAttestationError(
            "Cannot resolve the trusted Windows ProgramData directory"
        ) from exc


def _signature_public_key_fingerprint(
    signature: bytes,
    *,
    expected_namespace: str,
) -> str | None:
    """Return the key fingerprint from a strict v1 SSHSIG, if understood."""

    try:
        lines = signature.splitlines()
        if (
            len(lines) < 3
            or lines[0] != _SSH_SIGNATURE_BEGIN
            or lines[-1] != _SSH_SIGNATURE_END
            or any(not line for line in lines[1:-1])
            or any(_SSH_ARMOR_BODY.fullmatch(line) is None for line in lines[1:-1])
        ):
            raise ValueError("invalid SSHSIG armor")
        binary = base64.b64decode(b"".join(lines[1:-1]), validate=True)
        if not binary.startswith(_SSH_SIGNATURE_MAGIC):
            raise ValueError("invalid SSHSIG magic")
        offset = len(_SSH_SIGNATURE_MAGIC)
        version, offset = _read_u32(binary, offset)
        if version != 1:
            return None
        public_key, offset = _read_ssh_string(binary, offset)
        embedded_namespace, offset = _read_ssh_string(binary, offset)
        _reserved, offset = _read_ssh_string(binary, offset)
        _hash_algorithm, offset = _read_ssh_string(binary, offset)
        _signature_blob, offset = _read_ssh_string(binary, offset)
        if offset != len(binary) or not public_key:
            raise ValueError("invalid SSHSIG fields")
        if embedded_namespace != expected_namespace.encode("ascii"):
            raise ExternalMaintainerAttestationError(
                "Detached signature namespace does not match the requested one"
            )
        algorithm, algorithm_end = _read_ssh_string(public_key, 0)
        if (
            not algorithm
            or algorithm_end >= len(public_key)
            or any(byte < 0x21 or byte > 0x7E for byte in algorithm)
        ):
            raise ValueError("invalid SSH public-key blob")
        digest = hashlib.sha256(public_key).digest()
        encoded = base64.b64encode(digest).decode("ascii").rstrip("=")
        return f"SHA256:{encoded}"
    except ExternalMaintainerAttestationError:
        raise
    except (binascii.Error, UnicodeError, ValueError, struct.error):
        return None


def _read_u32(payload: bytes, offset: int) -> tuple[int, int]:
    if offset + 4 > len(payload):
        raise ValueError("truncated SSH uint32")
    return struct.unpack(">I", payload[offset : offset + 4])[0], offset + 4


def _read_ssh_string(payload: bytes, offset: int) -> tuple[bytes, int]:
    length, body_offset = _read_u32(payload, offset)
    end = body_offset + length
    if end > len(payload):
        raise ValueError("truncated SSH string")
    return payload[body_offset:end], end


def _make_directory_private(path: Path) -> None:
    try:
        os.chmod(path, 0o700)
    except OSError as exc:
        raise ExternalMaintainerAttestationError(
            "Cannot protect attestation snapshot directory"
        ) from exc


def _write_private_snapshot(path: Path, payload: bytes) -> None:
    descriptor = -1
    try:
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise ExternalMaintainerAttestationError(
            "Cannot create private attestation snapshot"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _sanitized_environment() -> dict[str, str]:
    # Deliberately construct an allowlist instead of filtering the inherited
    # environment.  In particular, PATH and dynamic-loader variables must not
    # be able to substitute or inject code into the trusted verifier process.
    environment = {"LC_ALL": "C", "LANG": "C"}
    if os.name == "nt":
        system_root = _windows_system_directory().parent
        environment["SystemRoot"] = str(system_root)
        environment["WINDIR"] = str(system_root)
        # Windows OpenSSH exits before verification when PROGRAMDATA is absent.
        # Resolve the machine-wide value from HKLM rather than trusting the
        # caller-controlled environment variable of the same name.
        environment["PROGRAMDATA"] = str(_windows_program_data_directory())
    return environment


def _issue_receipt(
    *,
    signer_identity: str,
    namespace: str,
    challenge_sha256: str,
    allowed_signers_sha256: str,
    signature_sha256: str,
    public_key_fingerprint: str | None,
) -> ExternalMaintainerAttestationReceipt:
    receipt = object.__new__(ExternalMaintainerAttestationReceipt)
    object.__setattr__(receipt, "signer_identity", signer_identity)
    object.__setattr__(receipt, "namespace", namespace)
    object.__setattr__(receipt, "challenge_sha256", challenge_sha256)
    object.__setattr__(receipt, "allowed_signers_sha256", allowed_signers_sha256)
    object.__setattr__(receipt, "signature_sha256", signature_sha256)
    object.__setattr__(receipt, "public_key_fingerprint", public_key_fingerprint)
    object.__setattr__(receipt, "_validation_token", _ATTESTATION_TOKEN)
    receipt.__post_init__()
    return receipt


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ExternalMaintainerAttestationError(
                f"Duplicate challenge object key: {key}"
            )
        result[key] = value
    return result


def _reject_nonfinite_number(value: str) -> None:
    raise ExternalMaintainerAttestationError(
        f"Non-finite challenge number is forbidden: {value}"
    )
