"""Reusable, policy-bound detached SSH attestations for G2 annotation stages.

The low-level OpenSSH verifier proves control of a permitted key.  This module
adds the G2 workflow invariants shared by Concept, Relation Pass A, Relation
Pass B, and GoldBundle sealing: a supported namespace, an exact canonical
challenge, a repository-issued reviewer-key policy, and a portable public
artifact/reference pair.  None of these checks proves reviewer humanity,
prediction blindness, elapsed time, or semantic correctness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import re
import tempfile
from typing import cast

from pydantic import BaseModel, ValidationError

from .annotation_models import (
    DetachedKeyAttestationArtifact,
    DetachedKeyAttestationReference,
    G2AttestationNamespace,
    G2_ATTESTATION_NAMESPACES,
)
from .canonical_io import (
    CanonicalArtifactError,
    canonical_json_bytes,
    read_bounded_regular_bytes,
)
from .reviewer_policy import (
    ReviewerKeyPolicyAuthority,
    ReviewerKeyPolicyError,
    revalidate_active_reviewer_key_policy,
    require_active_reviewer_key_policy,
    require_attestation_matches_reviewer_policy,
)
from .schemas import SAFE_ID_PATTERN
from .ssh_attestation import (
    MAX_ATTESTATION_CHALLENGE_BYTES,
    MAX_SSH_SIGNATURE_BYTES,
    ExternalMaintainerAttestationError,
    ExternalMaintainerAttestationReceipt,
    verify_external_maintainer_attestation,
)


_VERIFIED_ATTESTATION_TOKEN = object()


class AnnotationAttestationError(ValueError):
    """Raised when a G2 detached-attestation transition fails closed."""


@dataclass(frozen=True, slots=True, init=False)
class VerifiedAnnotationAttestation:
    """Token-gated bundle for one cryptographically verified public artifact.

    The capability proves registered-key control over the exact canonical
    challenge only.  It deliberately carries no human-review authority.
    """

    artifact: DetachedKeyAttestationArtifact
    reference: DetachedKeyAttestationReference
    receipt: ExternalMaintainerAttestationReceipt
    _validation_token: object = field(repr=False, compare=False)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError(
            "VerifiedAnnotationAttestation must come from its verifier"
        )

    def __post_init__(self) -> None:
        if self._validation_token is not _VERIFIED_ATTESTATION_TOKEN:
            raise ValueError("Invalid verified annotation-attestation token")
        if not isinstance(self.artifact, DetachedKeyAttestationArtifact):
            raise ValueError("Verified attestation artifact has an invalid type")
        if not isinstance(self.reference, DetachedKeyAttestationReference):
            raise ValueError("Verified attestation reference has an invalid type")
        if not isinstance(self.receipt, ExternalMaintainerAttestationReceipt):
            raise ValueError("Verified attestation receipt has an invalid type")
        try:
            self.receipt.__post_init__()
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("Verified attestation receipt is invalid") from exc
        expected_reference = _reference_from_artifact(self.artifact)
        if self.reference != expected_reference or not _receipt_matches_artifact(
            self.receipt,
            self.artifact,
        ):
            raise ValueError("Verified attestation bundle is inconsistent")


def canonical_attestation_challenge_bytes(
    challenge: BaseModel,
    *,
    expected_namespace: G2AttestationNamespace,
    expected_signer_identity: str,
) -> bytes:
    """Return exact canonical bytes after checking stage and signer bindings.

    Every G2 seal request is expected to expose ``namespace`` and
    ``reviewer_id`` fields.  Checking them before serialization prevents a
    caller from asking the right registered key to sign a request for a
    different stage or reviewer.
    """

    namespace = _require_supported_namespace(expected_namespace)
    if (
        not isinstance(expected_signer_identity, str)
        or re.fullmatch(SAFE_ID_PATTERN, expected_signer_identity) is None
    ):
        raise AnnotationAttestationError(
            "Expected attestation signer identity is invalid"
        )
    if not isinstance(challenge, BaseModel):
        raise AnnotationAttestationError(
            "Attestation challenge must be a typed model"
        )
    if getattr(challenge, "namespace", None) != namespace:
        raise AnnotationAttestationError(
            "Attestation challenge namespace does not match the stage"
        )
    if getattr(challenge, "reviewer_id", None) != expected_signer_identity:
        raise AnnotationAttestationError(
            "Attestation challenge reviewer does not match the signer"
        )
    try:
        payload = canonical_json_bytes(challenge)
    except (CanonicalArtifactError, TypeError, ValueError) as exc:
        raise AnnotationAttestationError(
            "Attestation challenge cannot be serialized canonically"
        ) from exc
    if not 1 <= len(payload) <= MAX_ATTESTATION_CHALLENGE_BYTES:
        raise AnnotationAttestationError(
            "Attestation challenge is empty or exceeds its byte limit"
        )
    return payload


def verify_and_build_detached_key_attestation(
    *,
    challenge: BaseModel,
    expected_namespace: G2AttestationNamespace,
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
    signature_path: Path,
) -> VerifiedAnnotationAttestation:
    """Verify an external signature and build its portable public artifact.

    This is an authoring transition and therefore requires an *active* policy
    at current ``HEAD``.  The allowed-signers bytes come only from that policy;
    callers cannot self-authorize a fresh key at signing time.
    """

    supplied_policy = _require_policy_authority(
        reviewer_key_policy,
        require_active=True,
    )
    policy = _revalidate_authoring_policy(supplied_policy)
    namespace = _require_supported_namespace(expected_namespace)
    challenge_bytes = canonical_attestation_challenge_bytes(
        challenge,
        expected_namespace=namespace,
        expected_signer_identity=policy.policy.reviewer_id,
    )
    _require_namespace_authorized(policy, namespace)
    try:
        allowed_signers = policy.policy.allowed_signers_policy_utf8.encode(
            "ascii"
        )
        with tempfile.TemporaryDirectory(
            prefix="vcc-g2-registered-reviewer-policy-"
        ) as temporary_directory:
            allowed_path = Path(temporary_directory) / "allowed_signers"
            _write_new_snapshot(allowed_path, allowed_signers)
            receipt = verify_external_maintainer_attestation(
                challenge_bytes=challenge_bytes,
                namespace=namespace,
                expected_signer_identity=policy.policy.reviewer_id,
                allowed_signers_path=allowed_path,
                signature_path=signature_path,
            )
        signature = read_bounded_regular_bytes(
            signature_path,
            max_bytes=MAX_SSH_SIGNATURE_BYTES,
            label="detached SSH signature",
        )
        require_attestation_matches_reviewer_policy(
            policy,
            signer_identity=receipt.signer_identity,
            namespace=receipt.namespace,
            allowed_signers_sha256=receipt.allowed_signers_sha256,
            public_key_fingerprint=receipt.public_key_fingerprint,
        )
        artifact = _artifact_from_verified_bytes(
            challenge_bytes=challenge_bytes,
            namespace=namespace,
            allowed_signers=allowed_signers,
            signature=signature,
            receipt=receipt,
        )
    except AnnotationAttestationError:
        raise
    except (
        CanonicalArtifactError,
        ExternalMaintainerAttestationError,
        OSError,
        ReviewerKeyPolicyError,
        TypeError,
        UnicodeError,
        ValidationError,
        ValueError,
    ):
        raise AnnotationAttestationError(
            "Detached annotation attestation could not be verified"
        ) from None
    final_policy = _revalidate_authoring_policy(policy)
    if final_policy.verified_head_sha != policy.verified_head_sha:
        raise AnnotationAttestationError(
            "Reviewer-key repository HEAD changed during attestation"
        )
    return _issue_verified_attestation(artifact=artifact, receipt=receipt)


def verify_embedded_detached_key_attestation(
    *,
    challenge: BaseModel,
    expected_namespace: G2AttestationNamespace,
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
    artifact: DetachedKeyAttestationArtifact,
) -> VerifiedAnnotationAttestation:
    """Deeply verify one persisted detached-attestation artifact.

    Historical policy authorities are intentionally accepted here so a key
    removal can revoke new work without invalidating an already sealed bundle.
    """

    policy = _require_policy_authority(
        reviewer_key_policy,
        require_active=False,
    )
    namespace = _require_supported_namespace(expected_namespace)
    challenge_bytes = canonical_attestation_challenge_bytes(
        challenge,
        expected_namespace=namespace,
        expected_signer_identity=policy.policy.reviewer_id,
    )
    _require_namespace_authorized(policy, namespace)
    validated_artifact = _validate_artifact(artifact)
    challenge_sha256 = hashlib.sha256(challenge_bytes).hexdigest()
    if (
        validated_artifact.namespace != namespace
        or validated_artifact.signer_identity != policy.policy.reviewer_id
        or validated_artifact.signed_payload_sha256 != challenge_sha256
        or validated_artifact.allowed_signers_policy_utf8
        != policy.policy.allowed_signers_policy_utf8
    ):
        raise AnnotationAttestationError(
            "Embedded attestation is not bound to its challenge and policy"
        )
    try:
        allowed_signers = validated_artifact.allowed_signers_policy_utf8.encode(
            "ascii"
        )
        signature = validated_artifact.signature_armored.encode("ascii")
        with tempfile.TemporaryDirectory(
            prefix="vcc-g2-embedded-attestation-"
        ) as temporary_directory:
            root = Path(temporary_directory)
            allowed_path = root / "allowed_signers"
            signature_path = root / "attestation.sig"
            _write_new_snapshot(allowed_path, allowed_signers)
            _write_new_snapshot(signature_path, signature)
            receipt = verify_external_maintainer_attestation(
                challenge_bytes=challenge_bytes,
                namespace=namespace,
                expected_signer_identity=policy.policy.reviewer_id,
                allowed_signers_path=allowed_path,
                signature_path=signature_path,
            )
        require_attestation_matches_reviewer_policy(
            policy,
            signer_identity=receipt.signer_identity,
            namespace=receipt.namespace,
            allowed_signers_sha256=receipt.allowed_signers_sha256,
            public_key_fingerprint=receipt.public_key_fingerprint,
        )
    except (
        ExternalMaintainerAttestationError,
        OSError,
        ReviewerKeyPolicyError,
        TypeError,
        ValueError,
    ):
        raise AnnotationAttestationError(
            "Embedded annotation attestation could not be verified"
        ) from None
    if not _receipt_matches_artifact(receipt, validated_artifact):
        raise AnnotationAttestationError(
            "Embedded attestation receipt differs from its public artifact"
        )
    return _issue_verified_attestation(
        artifact=validated_artifact,
        receipt=receipt,
    )


def detached_key_attestation_reference(
    artifact: DetachedKeyAttestationArtifact,
) -> DetachedKeyAttestationReference:
    """Derive a deterministic reference; this alone verifies no signature."""

    return _reference_from_artifact(_validate_artifact(artifact))


def _require_supported_namespace(value: str) -> G2AttestationNamespace:
    if value not in G2_ATTESTATION_NAMESPACES:
        raise AnnotationAttestationError(
            "Attestation namespace is not a registered G2 stage"
        )
    return cast(G2AttestationNamespace, value)


def _require_policy_authority(
    authority: ReviewerKeyPolicyAuthority,
    *,
    require_active: bool,
) -> ReviewerKeyPolicyAuthority:
    if not isinstance(authority, ReviewerKeyPolicyAuthority):
        raise AnnotationAttestationError(
            "A repository-issued reviewer-key policy authority is required"
        )
    try:
        authority.__post_init__()
        if require_active:
            require_active_reviewer_key_policy(authority)
    except (AttributeError, ReviewerKeyPolicyError, TypeError, ValueError):
        raise AnnotationAttestationError(
            "A repository-issued reviewer-key policy authority is required"
        ) from None
    return authority


def _require_namespace_authorized(
    authority: ReviewerKeyPolicyAuthority,
    namespace: G2AttestationNamespace,
) -> None:
    if namespace not in authority.policy.allowed_namespaces:
        raise AnnotationAttestationError(
            "Reviewer-key policy does not authorize this G2 stage"
        )


def _revalidate_authoring_policy(
    authority: ReviewerKeyPolicyAuthority,
) -> ReviewerKeyPolicyAuthority:
    try:
        return revalidate_active_reviewer_key_policy(authority)
    except ReviewerKeyPolicyError:
        raise AnnotationAttestationError(
            "A current repository reviewer-key policy authority is required"
        ) from None


def _artifact_from_verified_bytes(
    *,
    challenge_bytes: bytes,
    namespace: G2AttestationNamespace,
    allowed_signers: bytes,
    signature: bytes,
    receipt: ExternalMaintainerAttestationReceipt,
) -> DetachedKeyAttestationArtifact:
    if (
        receipt.challenge_sha256 != hashlib.sha256(challenge_bytes).hexdigest()
        or receipt.allowed_signers_sha256
        != hashlib.sha256(allowed_signers).hexdigest()
        or receipt.signature_sha256 != hashlib.sha256(signature).hexdigest()
        or receipt.namespace != namespace
        or receipt.public_key_fingerprint is None
    ):
        raise AnnotationAttestationError(
            "Attestation inputs changed during cryptographic verification"
        )
    try:
        return DetachedKeyAttestationArtifact(
            schema_version=1,
            artifact_role="golden_graph_detached_key_attestation",
            signer_identity=receipt.signer_identity,
            namespace=namespace,
            signed_payload_sha256=receipt.challenge_sha256,
            allowed_signers_policy_utf8=allowed_signers.decode("ascii"),
            allowed_signers_sha256=receipt.allowed_signers_sha256,
            signature_armored=signature.decode("ascii"),
            signature_sha256=receipt.signature_sha256,
            public_key_fingerprint=receipt.public_key_fingerprint,
            key_control_only_not_proof_of_humanity=True,
        )
    except (TypeError, UnicodeError, ValidationError, ValueError) as exc:
        raise AnnotationAttestationError(
            "Verified attestation bytes cannot form a public artifact"
        ) from exc


def _validate_artifact(
    artifact: DetachedKeyAttestationArtifact,
) -> DetachedKeyAttestationArtifact:
    if not isinstance(artifact, DetachedKeyAttestationArtifact):
        raise AnnotationAttestationError(
            "Detached attestation artifact has an invalid type"
        )
    try:
        validated = DetachedKeyAttestationArtifact.model_validate(
            artifact.model_dump(mode="python", exclude_none=False)
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as exc:
        raise AnnotationAttestationError(
            "Detached attestation artifact failed strict validation"
        ) from exc
    _require_supported_namespace(validated.namespace)
    return validated


def _reference_from_artifact(
    artifact: DetachedKeyAttestationArtifact,
) -> DetachedKeyAttestationReference:
    return DetachedKeyAttestationReference(
        signer_identity=artifact.signer_identity,
        namespace=artifact.namespace,
        signed_payload_sha256=artifact.signed_payload_sha256,
        allowed_signers_sha256=artifact.allowed_signers_sha256,
        signature_sha256=artifact.signature_sha256,
        public_key_fingerprint=artifact.public_key_fingerprint,
        key_control_only_not_proof_of_humanity=True,
    )


def _receipt_matches_artifact(
    receipt: ExternalMaintainerAttestationReceipt,
    artifact: DetachedKeyAttestationArtifact,
) -> bool:
    return (
        receipt.signer_identity == artifact.signer_identity
        and receipt.namespace == artifact.namespace
        and receipt.challenge_sha256 == artifact.signed_payload_sha256
        and receipt.allowed_signers_sha256 == artifact.allowed_signers_sha256
        and receipt.signature_sha256 == artifact.signature_sha256
        and receipt.public_key_fingerprint == artifact.public_key_fingerprint
    )


def _write_new_snapshot(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
    except OSError:
        raise AnnotationAttestationError(
            "Cannot create private attestation snapshot"
        ) from None


def _issue_verified_attestation(
    *,
    artifact: DetachedKeyAttestationArtifact,
    receipt: ExternalMaintainerAttestationReceipt,
) -> VerifiedAnnotationAttestation:
    capability = object.__new__(VerifiedAnnotationAttestation)
    object.__setattr__(capability, "artifact", artifact)
    object.__setattr__(
        capability,
        "reference",
        _reference_from_artifact(artifact),
    )
    object.__setattr__(capability, "receipt", receipt)
    object.__setattr__(
        capability,
        "_validation_token",
        _VERIFIED_ATTESTATION_TOKEN,
    )
    capability.__post_init__()
    return capability


__all__ = [
    "AnnotationAttestationError",
    "VerifiedAnnotationAttestation",
    "canonical_attestation_challenge_bytes",
    "detached_key_attestation_reference",
    "verify_and_build_detached_key_attestation",
    "verify_embedded_detached_key_attestation",
]
