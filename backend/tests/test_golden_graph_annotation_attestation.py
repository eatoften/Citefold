from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import subprocess
from typing import Literal, cast

import pytest
from pydantic import ValidationError

from golden_graph.annotation_attestation import (
    AnnotationAttestationError,
    VerifiedAnnotationAttestation,
    canonical_attestation_challenge_bytes,
    detached_key_attestation_reference,
    verify_and_build_detached_key_attestation,
    verify_embedded_detached_key_attestation,
)
from golden_graph.annotation_models import (
    CONCEPT_ATTESTATION_NAMESPACE,
    ConceptInventorySeal,
    DetachedKeyAttestationArtifact,
    G2AttestationNamespace,
    G2_ATTESTATION_NAMESPACES,
    GOLD_BUNDLE_ATTESTATION_NAMESPACE,
    RELATION_PASS_A_ATTESTATION_NAMESPACE,
    RELATION_PASS_B_ATTESTATION_NAMESPACE,
    StrictAnnotationModel,
)
from golden_graph.canonical_io import canonical_json_bytes
from golden_graph.reviewer_policy import (
    ReviewerKeyPolicyAuthority,
    build_reviewer_key_policy,
    load_repository_reviewer_key_policy,
    publish_reviewer_key_policy,
)
import golden_graph.reviewer_policy as reviewer_policy
from golden_graph.ssh_attestation import ExternalMaintainerAttestationError
import golden_graph.ssh_attestation as ssh_attestation


REVIEWER_ID = "maintainer-01"


class _Challenge(StrictAnnotationModel):
    schema_version: Literal[1]
    artifact_role: Literal["golden_graph_test_attestation_challenge"]
    namespace: G2AttestationNamespace
    reviewer_id: str
    payload_sha256: str


@dataclass(frozen=True)
class _KeyFixture:
    ssh_keygen: str
    private_key: Path
    public_algorithm: str
    public_blob: str


@dataclass(frozen=True)
class _PolicyFixture:
    key: _KeyFixture
    authority: ReviewerKeyPolicyAuthority


@pytest.fixture(scope="module")
def ssh_keygen() -> str:
    try:
        return ssh_attestation._resolve_ssh_keygen("ssh-keygen")
    except ExternalMaintainerAttestationError:
        pytest.skip("OpenSSH ssh-keygen is not available at a trusted path")


@pytest.fixture
def policy_fixture(tmp_path: Path, ssh_keygen: str) -> _PolicyFixture:
    key = _new_key(tmp_path / "primary", ssh_keygen)
    authority = _policy_authority(
        tmp_path / "primary-policy.json",
        key=key,
        reviewer_id=REVIEWER_ID,
        active=True,
    )
    return _PolicyFixture(key=key, authority=authority)


@pytest.mark.parametrize(
    "namespace",
    (
        CONCEPT_ATTESTATION_NAMESPACE,
        GOLD_BUNDLE_ATTESTATION_NAMESPACE,
        RELATION_PASS_A_ATTESTATION_NAMESPACE,
        RELATION_PASS_B_ATTESTATION_NAMESPACE,
    ),
)
def test_all_registered_namespaces_build_and_reverify_exact_challenges(
    namespace: G2AttestationNamespace,
    policy_fixture: _PolicyFixture,
    tmp_path: Path,
) -> None:
    challenge = _challenge(namespace)
    challenge_bytes = canonical_attestation_challenge_bytes(
        challenge,
        expected_namespace=namespace,
        expected_signer_identity=REVIEWER_ID,
    )
    assert challenge_bytes == canonical_json_bytes(challenge)
    assert challenge_bytes.endswith(b"\n")
    signature = _sign_challenge(
        policy_fixture.key,
        challenge_bytes,
        namespace=namespace,
        directory=tmp_path,
    )

    verified = verify_and_build_detached_key_attestation(
        challenge=challenge,
        expected_namespace=namespace,
        reviewer_key_policy=policy_fixture.authority,
        signature_path=signature,
    )
    replay = verify_embedded_detached_key_attestation(
        challenge=challenge,
        expected_namespace=namespace,
        reviewer_key_policy=policy_fixture.authority,
        artifact=verified.artifact,
    )

    assert verified.artifact.namespace == namespace
    assert verified.artifact.signer_identity == REVIEWER_ID
    assert verified.artifact.signed_payload_sha256 == hashlib.sha256(
        challenge_bytes
    ).hexdigest()
    assert verified.reference == detached_key_attestation_reference(
        verified.artifact
    )
    assert replay.artifact == verified.artifact
    assert replay.reference == verified.reference
    assert replay.receipt.challenge_sha256 == verified.receipt.challenge_sha256
    assert canonical_json_bytes(replay.artifact) == canonical_json_bytes(
        DetachedKeyAttestationArtifact.model_validate(
            verified.artifact.model_dump(mode="python", exclude_none=False)
        )
    )


def test_wrong_namespace_and_payload_are_rejected(
    policy_fixture: _PolicyFixture,
    tmp_path: Path,
) -> None:
    challenge = _challenge(RELATION_PASS_A_ATTESTATION_NAMESPACE)
    verified = _build_verified(
        policy_fixture,
        challenge,
        directory=tmp_path,
    )

    with pytest.raises(AnnotationAttestationError, match="not bound"):
        verify_embedded_detached_key_attestation(
            challenge=_challenge(RELATION_PASS_B_ATTESTATION_NAMESPACE),
            expected_namespace=RELATION_PASS_B_ATTESTATION_NAMESPACE,
            reviewer_key_policy=policy_fixture.authority,
            artifact=verified.artifact,
        )

    with pytest.raises(AnnotationAttestationError, match="not bound"):
        verify_embedded_detached_key_attestation(
            challenge=_challenge(
                RELATION_PASS_A_ATTESTATION_NAMESPACE,
                payload_sha256="b" * 64,
            ),
            expected_namespace=RELATION_PASS_A_ATTESTATION_NAMESPACE,
            reviewer_key_policy=policy_fixture.authority,
            artifact=verified.artifact,
        )


def test_wrong_policy_and_signer_are_rejected(
    policy_fixture: _PolicyFixture,
    tmp_path: Path,
    ssh_keygen: str,
) -> None:
    challenge = _challenge(GOLD_BUNDLE_ATTESTATION_NAMESPACE)
    verified = _build_verified(
        policy_fixture,
        challenge,
        directory=tmp_path,
    )
    other_key = _new_key(tmp_path / "other", ssh_keygen)
    other_policy = _policy_authority(
        tmp_path / "other-policy.json",
        key=other_key,
        reviewer_id=REVIEWER_ID,
        active=True,
    )

    with pytest.raises(AnnotationAttestationError, match="not bound"):
        verify_embedded_detached_key_attestation(
            challenge=challenge,
            expected_namespace=GOLD_BUNDLE_ATTESTATION_NAMESPACE,
            reviewer_key_policy=other_policy,
            artifact=verified.artifact,
        )

    payload = verified.artifact.model_dump(mode="python", exclude_none=False)
    changed_policy = verified.artifact.allowed_signers_policy_utf8.replace(
        f"{REVIEWER_ID} ",
        "maintainer-02 ",
        1,
    )
    payload["signer_identity"] = "maintainer-02"
    payload["allowed_signers_policy_utf8"] = changed_policy
    payload["allowed_signers_sha256"] = hashlib.sha256(
        changed_policy.encode("ascii")
    ).hexdigest()
    wrong_signer = DetachedKeyAttestationArtifact.model_validate(payload)

    with pytest.raises(AnnotationAttestationError, match="not bound"):
        verify_embedded_detached_key_attestation(
            challenge=challenge,
            expected_namespace=GOLD_BUNDLE_ATTESTATION_NAMESPACE,
            reviewer_key_policy=policy_fixture.authority,
            artifact=wrong_signer,
        )


def test_historical_policy_verifies_but_cannot_authorize_new_work(
    policy_fixture: _PolicyFixture,
    tmp_path: Path,
) -> None:
    challenge = _challenge(CONCEPT_ATTESTATION_NAMESPACE)
    verified = _build_verified(
        policy_fixture,
        challenge,
        directory=tmp_path,
    )
    historical = reviewer_policy._issue_policy_authority(
        policy=policy_fixture.authority.policy,
        repository_root=policy_fixture.authority.repository_root,
        artifact_path=policy_fixture.authority.artifact_path,
        policy_sha256=policy_fixture.authority.policy_sha256,
        registration_commit_sha=(
            policy_fixture.authority.registration_commit_sha
        ),
        verified_head_sha=policy_fixture.authority.verified_head_sha,
        policy_blob_oid=policy_fixture.authority.policy_blob_oid,
        active_at_verified_head=False,
    )

    replay = verify_embedded_detached_key_attestation(
        challenge=challenge,
        expected_namespace=CONCEPT_ATTESTATION_NAMESPACE,
        reviewer_key_policy=historical,
        artifact=verified.artifact,
    )
    assert replay.artifact == verified.artifact

    signature = _sign_challenge(
        policy_fixture.key,
        canonical_json_bytes(challenge),
        namespace=CONCEPT_ATTESTATION_NAMESPACE,
        directory=tmp_path / "new-work",
    )
    with pytest.raises(
        AnnotationAttestationError,
        match="repository-issued reviewer-key policy",
    ):
        verify_and_build_detached_key_attestation(
            challenge=challenge,
            expected_namespace=CONCEPT_ATTESTATION_NAMESPACE,
            reviewer_key_policy=historical,
            signature_path=signature,
        )


def test_concept_only_policy_cannot_authorize_relation_or_gold_work(
    tmp_path: Path,
    ssh_keygen: str,
) -> None:
    key = _new_key(tmp_path / "concept-only", ssh_keygen)
    authority = _policy_authority(
        tmp_path / "concept-only-policy.json",
        key=key,
        reviewer_id=REVIEWER_ID,
        active=True,
        allowed_namespaces=(CONCEPT_ATTESTATION_NAMESPACE,),
    )

    for namespace in (
        RELATION_PASS_A_ATTESTATION_NAMESPACE,
        RELATION_PASS_B_ATTESTATION_NAMESPACE,
        GOLD_BUNDLE_ATTESTATION_NAMESPACE,
    ):
        with pytest.raises(AnnotationAttestationError, match="does not authorize"):
            verify_and_build_detached_key_attestation(
                challenge=_challenge(namespace),
                expected_namespace=namespace,
                reviewer_key_policy=authority,
                signature_path=tmp_path / "unread-signature.sig",
            )


def test_challenge_and_capability_boundaries_fail_closed() -> None:
    challenge = _challenge(CONCEPT_ATTESTATION_NAMESPACE)
    with pytest.raises(AnnotationAttestationError, match="namespace"):
        canonical_attestation_challenge_bytes(
            challenge,
            expected_namespace=cast(G2AttestationNamespace, "unregistered-stage"),
            expected_signer_identity=REVIEWER_ID,
        )
    with pytest.raises(AnnotationAttestationError, match="reviewer"):
        canonical_attestation_challenge_bytes(
            challenge,
            expected_namespace=CONCEPT_ATTESTATION_NAMESPACE,
            expected_signer_identity="maintainer-02",
        )
    with pytest.raises(TypeError):
        VerifiedAnnotationAttestation()


def test_namespace_registry_is_sorted_complete_and_unique() -> None:
    assert G2_ATTESTATION_NAMESPACES == tuple(
        sorted(G2_ATTESTATION_NAMESPACES)
    )
    assert len(G2_ATTESTATION_NAMESPACES) == len(
        set(G2_ATTESTATION_NAMESPACES)
    ) == 4


def test_legacy_concept_attestation_and_seal_canonical_vectors_are_stable() -> None:
    allowed_signers = "maintainer-01 ssh-ed25519 AAAA\n"
    signature = (
        "-----BEGIN SSH SIGNATURE-----\n"
        "AAAA\n"
        "-----END SSH SIGNATURE-----\n"
    )
    artifact = DetachedKeyAttestationArtifact(
        schema_version=1,
        artifact_role="golden_graph_detached_key_attestation",
        signer_identity=REVIEWER_ID,
        namespace=CONCEPT_ATTESTATION_NAMESPACE,
        signed_payload_sha256="a" * 64,
        allowed_signers_policy_utf8=allowed_signers,
        allowed_signers_sha256=hashlib.sha256(
            allowed_signers.encode("ascii")
        ).hexdigest(),
        signature_armored=signature,
        signature_sha256=hashlib.sha256(signature.encode("ascii")).hexdigest(),
        public_key_fingerprint=f"SHA256:{'A' * 43}",
        key_control_only_not_proof_of_humanity=True,
    )
    reference = detached_key_attestation_reference(artifact)
    seal = ConceptInventorySeal(
        schema_version=1,
        artifact_role="golden_graph_concept_inventory_seal",
        status="concept_inventory_only_not_gold_bundle",
        protocol_id="fixture-g2-v1",
        frozen_protocol_sha256="b" * 64,
        concept_inventory_sha256="c" * 64,
        gold_alias_table_sha256="d" * 64,
        concept_inventory_seal_request_sha256="a" * 64,
        detached_attestation_artifact_sha256="e" * 64,
        reviewer_key_policy_sha256="f" * 64,
        reviewer_key_policy_git_commit="1" * 40,
        reviewer_id=REVIEWER_ID,
        reviewer_actor_kind_declaration="human",
        blind_to_system_proposals_declaration=True,
        software_authenticated_prediction_blindness=False,
        software_authenticated_reviewer_identity=False,
        concept_count=12,
        excluded_candidate_count=0,
        total_candidate_count=12,
        detached_attestation=reference,
    )

    assert tuple(DetachedKeyAttestationArtifact.model_fields) == (
        "schema_version",
        "artifact_role",
        "signer_identity",
        "namespace",
        "signed_payload_sha256",
        "allowed_signers_policy_utf8",
        "allowed_signers_sha256",
        "signature_armored",
        "signature_sha256",
        "public_key_fingerprint",
        "key_control_only_not_proof_of_humanity",
    )
    assert tuple(type(reference).model_fields) == (
        "signer_identity",
        "namespace",
        "signed_payload_sha256",
        "allowed_signers_sha256",
        "signature_sha256",
        "public_key_fingerprint",
        "key_control_only_not_proof_of_humanity",
    )
    assert tuple(ConceptInventorySeal.model_fields) == (
        "schema_version",
        "artifact_role",
        "status",
        "protocol_id",
        "frozen_protocol_sha256",
        "concept_inventory_sha256",
        "gold_alias_table_sha256",
        "concept_inventory_seal_request_sha256",
        "detached_attestation_artifact_sha256",
        "reviewer_key_policy_sha256",
        "reviewer_key_policy_git_commit",
        "reviewer_id",
        "reviewer_actor_kind_declaration",
        "blind_to_system_proposals_declaration",
        "software_authenticated_prediction_blindness",
        "software_authenticated_reviewer_identity",
        "concept_count",
        "excluded_candidate_count",
        "total_candidate_count",
        "detached_attestation",
    )
    assert hashlib.sha256(canonical_json_bytes(artifact)).hexdigest() == (
        "d5b252bf44c2a2cc3920698b8e77c85aa21a14017f39283dffa70dbb5d7a2496"
    )
    assert hashlib.sha256(canonical_json_bytes(reference)).hexdigest() == (
        "89083d520bc5a11f978a4fdf91c2a58714329a684c3ea03650cab3863c340546"
    )
    assert hashlib.sha256(canonical_json_bytes(seal)).hexdigest() == (
        "f2e082dd5287f919fd2dbdd9161a987c0d874173c91716bec6f560062710c9e9"
    )

    cross_stage = seal.model_dump(mode="python", exclude_none=False)
    cross_stage["detached_attestation"]["namespace"] = (
        RELATION_PASS_A_ATTESTATION_NAMESPACE
    )
    with pytest.raises(ValidationError, match="namespace"):
        ConceptInventorySeal.model_validate(cross_stage)


def _challenge(
    namespace: G2AttestationNamespace,
    *,
    payload_sha256: str = "a" * 64,
) -> _Challenge:
    return _Challenge(
        schema_version=1,
        artifact_role="golden_graph_test_attestation_challenge",
        namespace=namespace,
        reviewer_id=REVIEWER_ID,
        payload_sha256=payload_sha256,
    )


def _build_verified(
    fixture: _PolicyFixture,
    challenge: _Challenge,
    *,
    directory: Path,
) -> VerifiedAnnotationAttestation:
    challenge_bytes = canonical_attestation_challenge_bytes(
        challenge,
        expected_namespace=challenge.namespace,
        expected_signer_identity=REVIEWER_ID,
    )
    signature = _sign_challenge(
        fixture.key,
        challenge_bytes,
        namespace=challenge.namespace,
        directory=directory,
    )
    return verify_and_build_detached_key_attestation(
        challenge=challenge,
        expected_namespace=challenge.namespace,
        reviewer_key_policy=fixture.authority,
        signature_path=signature,
    )


def _new_key(directory: Path, ssh_keygen: str) -> _KeyFixture:
    directory.mkdir(parents=True, exist_ok=True)
    private_key = directory / "maintainer_ed25519"
    subprocess.run(
        [
            ssh_keygen,
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            "g2-shared-attestation-test",
            "-f",
            str(private_key),
        ],
        capture_output=True,
        check=True,
    )
    public_fields = private_key.with_suffix(".pub").read_text(
        encoding="ascii"
    ).split()
    assert public_fields[0] == "ssh-ed25519"
    return _KeyFixture(
        ssh_keygen=ssh_keygen,
        private_key=private_key,
        public_algorithm=public_fields[0],
        public_blob=public_fields[1],
    )


def _policy_authority(
    artifact_path: Path,
    *,
    key: _KeyFixture,
    reviewer_id: str,
    active: bool,
    allowed_namespaces: tuple[str, ...] = G2_ATTESTATION_NAMESPACES,
) -> ReviewerKeyPolicyAuthority:
    allowed_signers = (
        f"{reviewer_id} {key.public_algorithm} {key.public_blob}\n"
    )
    policy = build_reviewer_key_policy(
        protocol_id="test-g2-attestation-protocol",
        frozen_protocol_sha256="f" * 64,
        reviewer_id=reviewer_id,
        allowed_signers_policy_utf8=allowed_signers,
        allowed_namespaces=allowed_namespaces,
    )
    repository_root = (artifact_path.parent / f"{artifact_path.stem}-repo").resolve()
    repository_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--quiet", str(repository_root)],
        check=True,
        capture_output=True,
    )
    for key_name, value in (
        ("user.name", "Annotation Attestation Test"),
        ("user.email", "annotation-attestation@example.invalid"),
    ):
        subprocess.run(
            ["git", "-C", str(repository_root), "config", key_name, value],
            check=True,
            capture_output=True,
        )
    seed = repository_root / "seed.txt"
    seed.write_text("seed\n", encoding="utf-8", newline="\n")
    subprocess.run(
        ["git", "-C", str(repository_root), "add", "--", "seed.txt"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repository_root), "commit", "-q", "-m", "seed"],
        check=True,
        capture_output=True,
    )
    (repository_root / "backend/golden_graph").mkdir(parents=True)
    publish_reviewer_key_policy(repository_root=repository_root, policy=policy)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository_root),
            "add",
            "--",
            "backend/golden_graph/attestations",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository_root),
            "commit",
            "-q",
            "-m",
            "register reviewer key",
        ],
        check=True,
        capture_output=True,
    )
    registration_commit_sha = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    authority = load_repository_reviewer_key_policy(
        repository_root=repository_root,
        protocol_id=policy.protocol_id,
        frozen_protocol_sha256=policy.frozen_protocol_sha256,
        reviewer_id=policy.reviewer_id,
        registration_commit_sha=registration_commit_sha,
    )
    if active:
        return authority
    return reviewer_policy._issue_policy_authority(
        policy=authority.policy,
        repository_root=authority.repository_root,
        artifact_path=authority.artifact_path,
        policy_sha256=authority.policy_sha256,
        registration_commit_sha=authority.registration_commit_sha,
        verified_head_sha=authority.verified_head_sha,
        policy_blob_oid=authority.policy_blob_oid,
        active_at_verified_head=False,
    )


def _sign_challenge(
    key: _KeyFixture,
    challenge_bytes: bytes,
    *,
    namespace: G2AttestationNamespace,
    directory: Path,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    challenge_path = directory / f"{namespace}.challenge.json"
    challenge_path.write_bytes(challenge_bytes)
    subprocess.run(
        [
            key.ssh_keygen,
            "-Y",
            "sign",
            "-f",
            str(key.private_key),
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
