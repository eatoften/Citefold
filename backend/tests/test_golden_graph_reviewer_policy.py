from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import struct
import subprocess

import pytest

from golden_graph.canonical_io import canonical_json_bytes
from golden_graph.reviewer_policy import (
    ReviewerKeyPolicy,
    ReviewerKeyPolicyAuthority,
    ReviewerKeyPolicyError,
    build_reviewer_key_policy,
    load_historical_reviewer_key_policy,
    load_repository_reviewer_key_policy,
    publish_reviewer_key_policy,
    require_active_reviewer_key_policy,
    require_attestation_matches_reviewer_policy,
    reviewer_key_policy_path,
)


PROTOCOL_ID = "fixture-golden-graph-v1"
PROTOCOL_SHA256 = "a" * 64
REVIEWER_ID = "maintainer-01"
NAMESPACE = "video-course-cards-g2-concepts-v1"


@dataclass(frozen=True, slots=True)
class _RegisteredPolicyRepository:
    root: Path
    policy: ReviewerKeyPolicy
    registration_commit: str


def test_registered_policy_loads_from_exact_ancestor_blob_and_sidecar(
    tmp_path: Path,
) -> None:
    repository = _registered_policy_repository(tmp_path)

    authority = _load(repository)

    assert authority.policy == repository.policy
    assert authority.policy.registration_mode == "repository_history"
    assert "status" not in authority.policy.model_fields_set
    assert authority.registration_commit_sha == repository.registration_commit
    assert authority.policy_sha256 == hashlib.sha256(
        canonical_json_bytes(repository.policy)
    ).hexdigest()
    assert len(authority.policy_blob_oid) == 40
    assert authority.artifact_path == reviewer_key_policy_path(
        repository.root,
        protocol_id=PROTOCOL_ID,
        reviewer_id=REVIEWER_ID,
    ).resolve()
    assert authority.active_at_verified_head is True
    require_active_reviewer_key_policy(authority)
    with pytest.raises(TypeError, match="repository loader"):
        ReviewerKeyPolicyAuthority()


def test_self_authorized_replacement_key_is_not_registered(
    tmp_path: Path,
) -> None:
    repository = _registered_policy_repository(tmp_path)
    authority = _load(repository)
    attacker_policy = _policy(seed=bytes(range(32, 64)))

    with pytest.raises(ReviewerKeyPolicyError, match="not authorized"):
        require_attestation_matches_reviewer_policy(
            authority,
            signer_identity=REVIEWER_ID,
            namespace=NAMESPACE,
            allowed_signers_sha256=attacker_policy.allowed_signers_sha256,
            public_key_fingerprint=attacker_policy.public_key_fingerprint,
        )


def test_new_uncommitted_policy_cannot_issue_repository_authority(
    tmp_path: Path,
) -> None:
    root = _initialize_repository(tmp_path)
    registration_commit = _git_stdout(root, "rev-parse", "HEAD")
    policy = _policy()
    publish_reviewer_key_policy(repository_root=root, policy=policy)

    with pytest.raises(ReviewerKeyPolicyError, match="policy blob"):
        _load_values(
            root,
            registration_commit=registration_commit,
        )


def test_tampered_local_policy_fails_even_with_rewritten_sidecar(
    tmp_path: Path,
) -> None:
    repository = _registered_policy_repository(tmp_path)
    path = reviewer_key_policy_path(
        repository.root,
        protocol_id=PROTOCOL_ID,
        reviewer_id=REVIEWER_ID,
    )
    attacker_policy = _policy(seed=bytes(range(32, 64)))
    payload = canonical_json_bytes(attacker_policy)
    digest = hashlib.sha256(payload).hexdigest()
    path.write_bytes(payload)
    path.with_suffix(".sha256").write_bytes(
        f"{digest}  {path.name}\n".encode("utf-8")
    )

    with pytest.raises(ReviewerKeyPolicyError, match="Working-tree"):
        _load(repository)


def test_revoked_policy_remains_historically_verifiable_after_untracked_recreation(
    tmp_path: Path,
) -> None:
    repository = _registered_policy_repository(tmp_path)
    path = reviewer_key_policy_path(
        repository.root,
        protocol_id=PROTOCOL_ID,
        reviewer_id=REVIEWER_ID,
    )
    sidecar_path = path.with_suffix(".sha256")
    policy_bytes = path.read_bytes()
    sidecar_bytes = sidecar_path.read_bytes()
    _git(
        repository.root,
        "rm",
        path.relative_to(repository.root).as_posix(),
        sidecar_path.relative_to(repository.root).as_posix(),
    )
    _git(repository.root, "commit", "-m", "revoke reviewer policy")

    historical = _load_historical(repository)
    assert historical.policy == repository.policy
    assert historical.active_at_verified_head is False

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(policy_bytes)
    sidecar_path.write_bytes(sidecar_bytes)

    with pytest.raises(ReviewerKeyPolicyError, match="policy blob"):
        _load(repository)

    with pytest.raises(ReviewerKeyPolicyError, match="active current-HEAD"):
        require_active_reviewer_key_policy(historical)
    require_attestation_matches_reviewer_policy(
        historical,
        signer_identity=REVIEWER_ID,
        namespace=NAMESPACE,
        allowed_signers_sha256=repository.policy.allowed_signers_sha256,
        public_key_fingerprint=repository.policy.public_key_fingerprint,
    )


def test_staged_policy_deletion_cannot_issue_active_authority(
    tmp_path: Path,
) -> None:
    repository = _registered_policy_repository(tmp_path)
    path = reviewer_key_policy_path(
        repository.root,
        protocol_id=PROTOCOL_ID,
        reviewer_id=REVIEWER_ID,
    )
    _git(
        repository.root,
        "rm",
        path.relative_to(repository.root).as_posix(),
        path.with_suffix(".sha256").relative_to(repository.root).as_posix(),
    )

    with pytest.raises(ReviewerKeyPolicyError, match="index file"):
        _load(repository)


def test_staged_policy_change_cannot_issue_active_authority(
    tmp_path: Path,
) -> None:
    repository = _registered_policy_repository(tmp_path)
    path = reviewer_key_policy_path(
        repository.root,
        protocol_id=PROTOCOL_ID,
        reviewer_id=REVIEWER_ID,
    )
    attacker_policy = _policy(seed=bytes(range(32, 64)))
    payload = canonical_json_bytes(attacker_policy)
    digest = hashlib.sha256(payload).hexdigest()
    path.write_bytes(payload)
    path.with_suffix(".sha256").write_bytes(
        f"{digest}  {path.name}\n".encode("utf-8")
    )
    _git(
        repository.root,
        "add",
        path.relative_to(repository.root).as_posix(),
        path.with_suffix(".sha256").relative_to(repository.root).as_posix(),
    )

    with pytest.raises(ReviewerKeyPolicyError, match="Index"):
        _load(repository)


def test_policy_registration_on_diverged_branch_is_not_ancestor(
    tmp_path: Path,
) -> None:
    root = _initialize_repository(tmp_path)
    initial_commit = _git_stdout(root, "rev-parse", "HEAD")
    _git(root, "switch", "-c", "policy-registration")
    publish_reviewer_key_policy(repository_root=root, policy=_policy())
    _git(root, "add", "backend/golden_graph/attestations")
    _git(root, "commit", "-m", "register reviewer policy")
    registration_commit = _git_stdout(root, "rev-parse", "HEAD")
    _git(root, "switch", "--detach", initial_commit)

    with pytest.raises(ReviewerKeyPolicyError, match="not an ancestor"):
        _load_values(root, registration_commit=registration_commit)


def test_repository_subdirectory_cannot_be_substituted_as_top_level(
    tmp_path: Path,
) -> None:
    repository = _registered_policy_repository(tmp_path)

    with pytest.raises(ReviewerKeyPolicyError, match="Git top-level"):
        _load_values(
            repository.root / "backend",
            registration_commit=repository.registration_commit,
        )


def test_hostile_git_dir_and_work_tree_cannot_redirect_git_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted = _registered_policy_repository(tmp_path / "trusted")
    hostile = _initialize_repository(tmp_path / "hostile")
    monkeypatch.setenv("GIT_DIR", str(hostile / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(hostile))
    monkeypatch.setenv("GIT_INDEX_FILE", str(hostile / "attacker-index"))
    monkeypatch.setenv("PATH", str(hostile / "attacker-bin"))
    monkeypatch.setenv("LD_PRELOAD", str(hostile / "attacker-loader.so"))
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", str(hostile / "attacker.dylib"))

    authority = _load(trusted)

    assert authority.registration_commit_sha == trusted.registration_commit
    assert authority.artifact_path.is_relative_to(trusted.root)


def test_mutable_git_replace_ref_cannot_rewrite_registered_commit(
    tmp_path: Path,
) -> None:
    repository = _registered_policy_repository(tmp_path)
    trusted_commit = repository.registration_commit
    _git(repository.root, "switch", "-c", "attacker-replacement")
    path = reviewer_key_policy_path(
        repository.root,
        protocol_id=PROTOCOL_ID,
        reviewer_id=REVIEWER_ID,
    )
    replacement_policy = _policy(seed=bytes(range(32, 64)))
    payload = canonical_json_bytes(replacement_policy)
    digest = hashlib.sha256(payload).hexdigest()
    path.write_bytes(payload)
    path.with_suffix(".sha256").write_bytes(
        f"{digest}  {path.name}\n".encode("utf-8")
    )
    _git(repository.root, "add", "backend/golden_graph/attestations")
    _git(repository.root, "commit", "-m", "attempt replacement policy")
    replacement_commit = _git_stdout(repository.root, "rev-parse", "HEAD")
    _git(repository.root, "switch", "--detach", trusted_commit)
    _git(repository.root, "replace", trusted_commit, replacement_commit)

    authority = _load(repository)

    assert authority.policy == repository.policy
    assert authority.registration_commit_sha == trusted_commit


def test_policy_requires_one_canonical_ed25519_row_and_sorted_namespaces() -> None:
    valid_line = _allowed_signers_line(REVIEWER_ID, bytes(range(32)))

    with pytest.raises(ReviewerKeyPolicyError):
        build_reviewer_key_policy(
            protocol_id=PROTOCOL_ID,
            frozen_protocol_sha256=PROTOCOL_SHA256,
            reviewer_id=REVIEWER_ID,
            allowed_signers_policy_utf8=valid_line.rstrip("\n"),
            allowed_namespaces=(NAMESPACE,),
        )
    with pytest.raises(ReviewerKeyPolicyError):
        build_reviewer_key_policy(
            protocol_id=PROTOCOL_ID,
            frozen_protocol_sha256=PROTOCOL_SHA256,
            reviewer_id=REVIEWER_ID,
            allowed_signers_policy_utf8=(valid_line.rstrip("\n") + " comment\n"),
            allowed_namespaces=(NAMESPACE,),
        )
    with pytest.raises(ReviewerKeyPolicyError):
        build_reviewer_key_policy(
            protocol_id=PROTOCOL_ID,
            frozen_protocol_sha256=PROTOCOL_SHA256,
            reviewer_id=REVIEWER_ID,
            allowed_signers_policy_utf8=valid_line,
            allowed_namespaces=("z-namespace", "a-namespace"),
        )


def _registered_policy_repository(root: Path) -> _RegisteredPolicyRepository:
    root = _initialize_repository(root)
    policy = _policy()
    publish_reviewer_key_policy(repository_root=root, policy=policy)
    _git(root, "add", "backend/golden_graph/attestations")
    _git(root, "commit", "-m", "register reviewer policy")
    return _RegisteredPolicyRepository(
        root=root,
        policy=policy,
        registration_commit=_git_stdout(root, "rev-parse", "HEAD"),
    )


def _initialize_repository(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Reviewer Policy Test")
    _git(root, "config", "user.email", "reviewer-policy@example.test")
    golden_graph = root / "backend/golden_graph"
    golden_graph.mkdir(parents=True)
    (root / ".gitattributes").write_text("* text=auto eol=lf\n", encoding="utf-8")
    marker = golden_graph / ".repository-marker"
    marker.write_text("reviewer policy test repository\n", encoding="utf-8")
    _git(root, "add", ".gitattributes", "backend/golden_graph/.repository-marker")
    _git(root, "commit", "-m", "initialize repository")
    return root


def _policy(*, seed: bytes = bytes(range(32))) -> ReviewerKeyPolicy:
    return build_reviewer_key_policy(
        protocol_id=PROTOCOL_ID,
        frozen_protocol_sha256=PROTOCOL_SHA256,
        reviewer_id=REVIEWER_ID,
        allowed_signers_policy_utf8=_allowed_signers_line(REVIEWER_ID, seed),
        allowed_namespaces=(NAMESPACE,),
    )


def _allowed_signers_line(reviewer_id: str, public_key: bytes) -> str:
    assert len(public_key) == 32
    blob = _ssh_string(b"ssh-ed25519") + _ssh_string(public_key)
    encoded = base64.b64encode(blob).decode("ascii")
    return f"{reviewer_id} ssh-ed25519 {encoded}\n"


def _ssh_string(value: bytes) -> bytes:
    return struct.pack(">I", len(value)) + value


def _load(repository: _RegisteredPolicyRepository) -> ReviewerKeyPolicyAuthority:
    return _load_values(
        repository.root,
        registration_commit=repository.registration_commit,
    )


def _load_historical(
    repository: _RegisteredPolicyRepository,
) -> ReviewerKeyPolicyAuthority:
    return load_historical_reviewer_key_policy(
        repository_root=repository.root,
        protocol_id=PROTOCOL_ID,
        frozen_protocol_sha256=PROTOCOL_SHA256,
        reviewer_id=REVIEWER_ID,
        registration_commit_sha=repository.registration_commit,
    )


def _load_values(
    root: Path,
    *,
    registration_commit: str,
) -> ReviewerKeyPolicyAuthority:
    return load_repository_reviewer_key_policy(
        repository_root=root,
        protocol_id=PROTOCOL_ID,
        frozen_protocol_sha256=PROTOCOL_SHA256,
        reviewer_id=REVIEWER_ID,
        registration_commit_sha=registration_commit,
    )


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
    )


def _git_stdout(root: Path, *arguments: str) -> str:
    return _git(root, *arguments).stdout.strip()
