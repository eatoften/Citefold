from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import subprocess

import pytest

from golden_graph.canonical_io import canonical_json_bytes
from golden_graph.ssh_attestation import (
    ExternalMaintainerAttestationError,
    ExternalMaintainerAttestationReceipt,
    verify_external_maintainer_attestation,
)
import golden_graph.ssh_attestation as ssh_attestation


NAMESPACE = "video-course-cards-gold-seal-v1"
SIGNER_IDENTITY = "maintainer-01@example.test"


@dataclass(frozen=True)
class _SignedFixture:
    ssh_keygen: str
    challenge: bytes
    challenge_path: Path
    signature_path: Path
    allowed_signers_path: Path
    public_key_path: Path
    private_key_path: Path


@pytest.fixture
def signed_fixture(tmp_path: Path) -> _SignedFixture:
    try:
        ssh_keygen = ssh_attestation._resolve_ssh_keygen("ssh-keygen")
    except ExternalMaintainerAttestationError:
        pytest.skip("OpenSSH ssh-keygen is not available at a trusted path")

    private_key = tmp_path / "maintainer_ed25519"
    _generate_ed25519_key(ssh_keygen, private_key)
    public_key = private_key.with_suffix(".pub")
    allowed_signers = tmp_path / "allowed_signers"
    allowed_signers.write_bytes(
        SIGNER_IDENTITY.encode("ascii")
        + b" "
        + public_key.read_bytes().strip()
        + b"\n"
    )
    challenge = canonical_json_bytes({
        "artifact_role": "gold_bundle_key_control_challenge",
        "gold_bundle_sha256": "a" * 64,
        "schema_version": 1,
    })
    challenge_path = tmp_path / "challenge.json"
    challenge_path.write_bytes(challenge)
    signature_path = _sign_file(
        ssh_keygen,
        private_key,
        challenge_path,
        namespace=NAMESPACE,
    )
    return _SignedFixture(
        ssh_keygen=ssh_keygen,
        challenge=challenge,
        challenge_path=challenge_path,
        signature_path=signature_path,
        allowed_signers_path=allowed_signers,
        public_key_path=public_key,
        private_key_path=private_key,
    )


def test_verifies_exact_canonical_challenge_and_returns_bound_receipt(
    signed_fixture: _SignedFixture,
) -> None:
    receipt = _verify(signed_fixture)

    fingerprint = subprocess.run(
        [
            signed_fixture.ssh_keygen,
            "-lf",
            str(signed_fixture.public_key_path),
            "-E",
            "sha256",
        ],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.split()[1]
    assert receipt.signer_identity == SIGNER_IDENTITY
    assert receipt.namespace == NAMESPACE
    assert receipt.challenge_sha256 == _sha256(signed_fixture.challenge)
    assert receipt.allowed_signers_sha256 == _sha256(
        signed_fixture.allowed_signers_path.read_bytes()
    )
    assert receipt.signature_sha256 == _sha256(
        signed_fixture.signature_path.read_bytes()
    )
    assert receipt.public_key_fingerprint == fingerprint


def test_rejects_wrong_namespace(signed_fixture: _SignedFixture) -> None:
    with pytest.raises(ExternalMaintainerAttestationError):
        _verify(signed_fixture, namespace="different-gold-seal-v1")


def test_rejects_wrong_expected_identity(
    signed_fixture: _SignedFixture,
) -> None:
    with pytest.raises(ExternalMaintainerAttestationError):
        _verify(
            signed_fixture,
            expected_signer_identity="another-maintainer@example.test",
        )


def test_rejects_wrong_payload(signed_fixture: _SignedFixture) -> None:
    different_challenge = canonical_json_bytes({
        "artifact_role": "gold_bundle_key_control_challenge",
        "gold_bundle_sha256": "b" * 64,
        "schema_version": 1,
    })

    with pytest.raises(ExternalMaintainerAttestationError):
        _verify(signed_fixture, challenge_bytes=different_challenge)


def test_rejects_signature_from_unallowed_key(
    signed_fixture: _SignedFixture,
    tmp_path: Path,
) -> None:
    other_key = tmp_path / "other_ed25519"
    _generate_ed25519_key(signed_fixture.ssh_keygen, other_key)
    other_challenge = tmp_path / "same-challenge.json"
    other_challenge.write_bytes(signed_fixture.challenge)
    other_signature = _sign_file(
        signed_fixture.ssh_keygen,
        other_key,
        other_challenge,
        namespace=NAMESPACE,
    )

    with pytest.raises(ExternalMaintainerAttestationError):
        _verify(signed_fixture, signature_path=other_signature)


def test_rejects_noncanonical_challenge_before_openssh(
    signed_fixture: _SignedFixture,
) -> None:
    noncanonical = b'{"schema_version": 1}\n'

    with pytest.raises(
        ExternalMaintainerAttestationError,
        match="exact canonical JSON bytes",
    ):
        _verify(signed_fixture, challenge_bytes=noncanonical)


def test_rejects_symlinked_authority_file(
    signed_fixture: _SignedFixture,
    tmp_path: Path,
) -> None:
    symlink = tmp_path / "allowed_signers_symlink"
    try:
        symlink.symlink_to(signed_fixture.allowed_signers_path)
    except OSError:
        pytest.skip("Creating symlinks is not permitted on this platform")

    with pytest.raises(ExternalMaintainerAttestationError):
        _verify(signed_fixture, allowed_signers_path=symlink)


def test_rejects_hardlinked_authority_file(
    signed_fixture: _SignedFixture,
    tmp_path: Path,
) -> None:
    hardlink_source = tmp_path / "hardlink_source"
    hardlink_alias = tmp_path / "hardlink_alias"
    hardlink_source.write_bytes(signed_fixture.allowed_signers_path.read_bytes())
    try:
        os.link(hardlink_source, hardlink_alias)
    except OSError:
        pytest.skip("Creating hard links is not permitted on this platform")

    with pytest.raises(ExternalMaintainerAttestationError):
        _verify(signed_fixture, allowed_signers_path=hardlink_source)


def test_subprocess_uses_snapshots_and_sanitized_environment(
    signed_fixture: _SignedFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_run = subprocess.run
    observed: dict[str, object] = {}

    def inspect_then_run(command: list[str], **kwargs: object):
        observed["command"] = command
        observed["environment"] = kwargs["env"]
        assert command[4] != str(signed_fixture.allowed_signers_path)
        assert command[10] != str(signed_fixture.signature_path)
        assert Path(command[4]).read_bytes() == (
            signed_fixture.allowed_signers_path.read_bytes()
        )
        assert Path(command[10]).read_bytes() == (
            signed_fixture.signature_path.read_bytes()
        )
        # Mutating the caller paths after the authority read cannot change the
        # bytes used by OpenSSH in this verification call.
        signed_fixture.allowed_signers_path.write_bytes(b"replaced\n")
        signed_fixture.signature_path.write_bytes(b"replaced\n")
        return real_run(command, **kwargs)

    monkeypatch.setenv("GIT_SSH_COMMAND", "should-not-reach-verifier")
    monkeypatch.setenv("GIT_DIR", "should-not-reach-verifier")
    monkeypatch.setenv("SSH_AUTH_SOCK", "should-not-reach-verifier")
    monkeypatch.setenv("SSH_ASKPASS", "should-not-reach-verifier")
    monkeypatch.setenv("LD_PRELOAD", "should-not-reach-verifier")
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", "should-not-reach-verifier")
    monkeypatch.setenv("PATH", str(tmp_path / "hostile-bin"))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "hostile-program-data"))
    monkeypatch.setattr(ssh_attestation.subprocess, "run", inspect_then_run)

    receipt = _verify(signed_fixture)

    environment = observed["environment"]
    assert isinstance(environment, dict)
    assert observed["command"][0] == signed_fixture.ssh_keygen
    forbidden = {
        "PATH",
        "LD_PRELOAD",
        "DYLD_INSERT_LIBRARIES",
        "GIT_DIR",
        "GIT_SSH_COMMAND",
        "SSH_ASKPASS",
        "SSH_AUTH_SOCK",
    }
    assert forbidden.isdisjoint({key.upper() for key in environment})
    if os.name == "nt":
        assert environment["PROGRAMDATA"] != os.environ["PROGRAMDATA"]
    assert receipt.signer_identity == SIGNER_IDENTITY


def test_relative_test_only_executable_override_is_rejected(
    signed_fixture: _SignedFixture,
) -> None:
    with pytest.raises(
        ExternalMaintainerAttestationError,
        match="test-only.*absolute path",
    ):
        _verify(
            signed_fixture,
            ssh_keygen_executable=Path("hostile-bin/ssh-keygen"),
        )


def test_absolute_test_only_executable_override_remains_available(
    signed_fixture: _SignedFixture,
) -> None:
    receipt = _verify(
        signed_fixture,
        ssh_keygen_executable=Path(signed_fixture.ssh_keygen),
    )

    assert receipt.signer_identity == SIGNER_IDENTITY


def test_receipt_cannot_be_constructed_directly() -> None:
    with pytest.raises(TypeError):
        ExternalMaintainerAttestationReceipt()


def _verify(
    fixture: _SignedFixture,
    *,
    challenge_bytes: bytes | None = None,
    namespace: str = NAMESPACE,
    expected_signer_identity: str = SIGNER_IDENTITY,
    allowed_signers_path: Path | None = None,
    signature_path: Path | None = None,
    ssh_keygen_executable: str | Path | None = None,
) -> ExternalMaintainerAttestationReceipt:
    arguments: dict[str, object] = {}
    if ssh_keygen_executable is not None:
        arguments["ssh_keygen_executable"] = ssh_keygen_executable
    return verify_external_maintainer_attestation(
        challenge_bytes=(
            fixture.challenge if challenge_bytes is None else challenge_bytes
        ),
        namespace=namespace,
        expected_signer_identity=expected_signer_identity,
        allowed_signers_path=(
            fixture.allowed_signers_path
            if allowed_signers_path is None
            else allowed_signers_path
        ),
        signature_path=(
            fixture.signature_path if signature_path is None else signature_path
        ),
        **arguments,
    )


def _generate_ed25519_key(ssh_keygen: str, private_key_path: Path) -> None:
    subprocess.run(
        [
            ssh_keygen,
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            "g2-attestation-test",
            "-f",
            str(private_key_path),
        ],
        capture_output=True,
        check=True,
    )


def _sign_file(
    ssh_keygen: str,
    private_key_path: Path,
    challenge_path: Path,
    *,
    namespace: str,
) -> Path:
    subprocess.run(
        [
            ssh_keygen,
            "-Y",
            "sign",
            "-f",
            str(private_key_path),
            "-n",
            namespace,
            str(challenge_path),
        ],
        capture_output=True,
        check=True,
    )
    signature_path = Path(f"{challenge_path}.sig")
    assert signature_path.is_file()
    return signature_path


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
