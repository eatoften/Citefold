"""Commit--reveal workflow for prediction-blind Relation Pass A.

Pass A labels are resolved into a redacted immutable artifact, but that
artifact remains inside the gitignored annotation boundary until Pass B is
sealed.  Only a neutral hash commitment, detached key-control attestation, and
commitment seal are published.  This prevents ordinary Pass B tooling from
seeing Pass A labels while making later label substitution detectable.

The software validates structure, evidence, privacy, lineage, and registered
key control.  It cannot prove reviewer humanity, prediction blindness, elapsed
time, or semantic correctness.
"""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets

from pydantic import ValidationError

from .annotation_artifacts import (
    CanonicalArtifactAuthority,
    load_canonical_artifact,
    load_private_canonical_artifact,
    preflight_canonical_artifact,
    preflight_private_canonical_artifact,
    publish_canonical_artifact,
    publish_private_canonical_artifact,
)
from .annotation_attestation import (
    AnnotationAttestationError,
    verify_and_build_detached_key_attestation,
    verify_embedded_detached_key_attestation,
)
from .annotation_evidence import (
    AnnotationEvidenceError,
    AnnotationEvidenceSourceAuthority,
    bind_annotation_evidence_source,
    reject_public_source_copy,
    resolve_evidence_selection,
    validate_public_evidence_span,
)
from .annotation_models import (
    DetachedKeyAttestationArtifact,
    EvidenceSpan,
    RELATION_PASS_A_ATTESTATION_NAMESPACE,
)
from .annotation_workflow import (
    ConceptStagePaths,
    SealedConceptInventoryAuthority,
    default_concept_stage_paths,
    load_sealed_concept_inventory,
)
from .canonical_io import canonical_json_bytes, load_hashed_canonical_json
from .protocol import FrozenProtocolAuthority
from .relation_annotation_models import (
    RELATION_PASS_A_RELEASE_POLICY,
    RELATION_PASS_A_REVIEWER_ATTESTATION,
    RelationEvidenceSpan,
    RelationJudgment,
    RelationJudgmentDraft,
    RelationPairDecision,
    RelationPairDecisionDraft,
    RelationPassAArtifact,
    RelationPassASeal,
    RelationPassASealRequest,
    RelationPassAWorksheet,
    RelationWorksheetConcept,
    relation_evidence_sort_key,
)
from .reviewer_policy import (
    ReviewerKeyPolicyAuthority,
    ReviewerKeyPolicyError,
    load_historical_reviewer_key_policy,
    revalidate_active_reviewer_key_policy,
)
from .schemas import GoldenGraphProtocol
from .source_slice_builder import (
    PrivateSourceSliceMaterializationReceipt,
    SourceSliceBuildError,
    load_private_source_slice_materialization,
)
from .ssh_attestation import ExternalMaintainerAttestationReceipt


MAX_RELATION_PASS_A_WORKSHEET_BYTES = 2 * 1024 * 1024

_PREPARED_TOKEN = object()
_SIGNED_TOKEN = object()
_COMMITMENT_TOKEN = object()
_SEALED_TOKEN = object()


class RelationPassAWorkflowError(ValueError):
    """Raised when a Relation Pass A transition fails closed."""


@dataclass(frozen=True, slots=True, init=False)
class PreparedRelationPassA:
    """Private redacted labels and their unsigned neutral commitment."""

    artifact: RelationPassAArtifact
    artifact_sha256: str
    seal_request: RelationPassASealRequest
    seal_request_sha256: str
    _validation_token: object = field(repr=False, compare=False)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("PreparedRelationPassA must come from its workflow")

    def __post_init__(self) -> None:
        if self._validation_token is not _PREPARED_TOKEN:
            raise ValueError("Invalid prepared Relation Pass A token")
        _require_sha(self.artifact_sha256, "artifact_sha256")
        _require_sha(self.seal_request_sha256, "seal_request_sha256")


@dataclass(frozen=True, slots=True, init=False)
class SignedRelationPassA:
    """Pass A commitment leaves after registered-key approval."""

    prepared: PreparedRelationPassA
    attestation_artifact: DetachedKeyAttestationArtifact
    attestation_artifact_sha256: str
    seal: RelationPassASeal
    seal_sha256: str
    _validation_token: object = field(repr=False, compare=False)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("SignedRelationPassA must come from its workflow")

    def __post_init__(self) -> None:
        if self._validation_token is not _SIGNED_TOKEN:
            raise ValueError("Invalid signed Relation Pass A token")
        _require_sha(
            self.attestation_artifact_sha256,
            "attestation_artifact_sha256",
        )
        _require_sha(self.seal_sha256, "seal_sha256")


@dataclass(frozen=True, slots=True)
class RelationPassAPrivatePaths:
    """Private label-bearing path unavailable to public-only consumers."""

    artifact: Path


@dataclass(frozen=True, slots=True)
class RelationPassAPublicCommitmentPaths:
    """Three public, label-free commitment leaves."""

    seal_request: Path
    attestation: Path
    seal: Path


@dataclass(frozen=True, slots=True)
class RelationPassAStagePaths:
    """Explicitly separated private and public path capabilities."""

    private: RelationPassAPrivatePaths
    public: RelationPassAPublicCommitmentPaths


@dataclass(frozen=True, slots=True)
class _VerifiedRelationContext:
    """Fresh replay results consumed instead of caller-owned capabilities."""

    sealed_concepts: SealedConceptInventoryAuthority
    reviewer_key_policy: ReviewerKeyPolicyAuthority
    source_authority: AnnotationEvidenceSourceAuthority


@dataclass(frozen=True, slots=True, init=False)
class RelationPassACommitmentAuthority:
    """Public neutral commitment intentionally carrying no Pass A labels."""

    sealed_concepts: SealedConceptInventoryAuthority
    reviewer_key_policy: ReviewerKeyPolicyAuthority
    seal_request: CanonicalArtifactAuthority[RelationPassASealRequest]
    attestation: CanonicalArtifactAuthority[DetachedKeyAttestationArtifact]
    seal: CanonicalArtifactAuthority[RelationPassASeal]
    key_control_receipt: ExternalMaintainerAttestationReceipt
    _validation_token: object = field(repr=False, compare=False)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError(
            "RelationPassACommitmentAuthority must come from its strict loader"
        )

    def __post_init__(self) -> None:
        if self._validation_token is not _COMMITMENT_TOKEN:
            raise ValueError("Invalid Relation Pass A commitment token")
        if hasattr(self, "artifact") or hasattr(self, "pair_decisions"):
            raise ValueError("Public Pass A commitment cannot expose labels")


@dataclass(frozen=True, slots=True, init=False)
class SealedRelationPassAAuthority:
    """Deeply reloaded local Pass A authority; labels remain embargoed."""

    commitment: RelationPassACommitmentAuthority
    private_artifact: CanonicalArtifactAuthority[RelationPassAArtifact] = field(
        repr=False
    )
    _validation_token: object = field(repr=False, compare=False)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError(
            "SealedRelationPassAAuthority must come from its strict loader"
        )

    def __post_init__(self) -> None:
        if self._validation_token is not _SEALED_TOKEN:
            raise ValueError("Invalid sealed Relation Pass A authority token")


def new_relation_pass_a_worksheet(
    *,
    sealed_concepts: SealedConceptInventoryAuthority,
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
    worksheet_id: str,
) -> RelationPassAWorksheet:
    """Create an exhaustive pending worksheet without generating any labels."""

    context = _require_relation_context(
        sealed_concepts=sealed_concepts,
        reviewer_key_policy=reviewer_key_policy,
        require_active_policy=True,
    )
    sealed_concepts = context.sealed_concepts
    reviewer_key_policy = context.reviewer_key_policy
    protocol = sealed_concepts.protocol.protocol
    inventory = sealed_concepts.inventory.artifact
    manifest = sealed_concepts.pair_manifest.artifact
    source = sealed_concepts.source
    concepts = tuple(
        RelationWorksheetConcept(
            concept_key=concept.concept_key,
            preferred_name=concept.preferred_name,
        )
        for concept in inventory.concepts
    )
    names = {item.concept_key: item.preferred_name for item in concepts}
    pair_decisions = tuple(
        RelationPairDecisionDraft(
            pair_id=pair.pair_id,
            left_concept_key=pair.left_concept_key,
            left_preferred_name=names[pair.left_concept_key],
            right_concept_key=pair.right_concept_key,
            right_preferred_name=names[pair.right_concept_key],
            outcome="pending",
            none_rationale=None,
            relations=(),
        )
        for pair in manifest.pairs
    )
    return RelationPassAWorksheet(
        schema_version=1,
        artifact_role="golden_graph_relation_pass_a_worksheet",
        worksheet_status="draft",
        worksheet_id=worksheet_id,
        commitment_nonce_hex=secrets.token_hex(32),
        protocol_id=protocol.protocol_id,
        frozen_protocol_sha256=sealed_concepts.protocol.protocol_sha256,
        semantic_source_catalog_sha256=(
            source.materialization.source_catalog_sha256
        ),
        chunk_manifest_sha256=source.materialization.chunk_manifest_sha256,
        private_materialization_sha256=source.artifact_sha256,
        annotation_guide_sha256=protocol.review.annotation_guide_sha256,
        concept_inventory_sha256=sealed_concepts.inventory.artifact_sha256,
        concept_inventory_seal_sha256=sealed_concepts.seal.artifact_sha256,
        relation_pair_manifest_sha256=(
            sealed_concepts.pair_manifest.artifact_sha256
        ),
        reviewer_key_policy_sha256=reviewer_key_policy.policy_sha256,
        reviewer_key_policy_git_commit=(
            reviewer_key_policy.registration_commit_sha
        ),
        reviewer_id=protocol.review.reviewer_id,
        pass_role="A",
        blind_to_system_proposals_declaration=True,
        software_authenticated_prediction_blindness=False,
        software_authenticated_reviewer_identity=False,
        reviewer_actor_kind_declaration=None,
        reviewer_attestation_statement=None,
        reviewer_attested_at_utc=None,
        minimum_delay_hours_before_pass_b=(
            protocol.review.minimum_delay_hours
        ),
        label_release_policy=RELATION_PASS_A_RELEASE_POLICY,
        concept_count=inventory.concept_count,
        concepts=concepts,
        pair_count=manifest.pair_count,
        pair_decisions=pair_decisions,
    )


def parse_relation_pass_a_worksheet(payload: bytes) -> RelationPassAWorksheet:
    """Parse bounded human-edited bytes without echoing private contents."""

    if (
        not isinstance(payload, bytes)
        or not 1 <= len(payload) <= MAX_RELATION_PASS_A_WORKSHEET_BYTES
    ):
        raise RelationPassAWorkflowError(
            "Relation Pass A worksheet is empty or exceeds its byte limit"
        )
    try:
        decoded = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
        return RelationPassAWorksheet.model_validate(decoded)
    except RelationPassAWorkflowError:
        raise
    except (
        UnicodeError,
        ValueError,
        TypeError,
        RecursionError,
        OverflowError,
        ValidationError,
    ):
        raise RelationPassAWorkflowError(
            "Relation Pass A worksheet failed strict validation"
        ) from None


def prepare_relation_pass_a(
    *,
    sealed_concepts: SealedConceptInventoryAuthority,
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
    worksheet: RelationPassAWorksheet,
) -> PreparedRelationPassA:
    """Resolve a complete worksheet into private labels and a neutral request."""

    context = _require_relation_context(
        sealed_concepts=sealed_concepts,
        reviewer_key_policy=reviewer_key_policy,
        require_active_policy=True,
    )
    sealed_concepts = context.sealed_concepts
    reviewer_key_policy = context.reviewer_key_policy
    source_authority = context.source_authority
    try:
        worksheet = RelationPassAWorksheet.model_validate(
            worksheet.model_dump(mode="python", exclude_none=False)
        )
    except (TypeError, ValueError, ValidationError):
        raise RelationPassAWorkflowError(
            "Relation Pass A worksheet failed canonical model validation"
        ) from None
    _validate_worksheet_binding(
        sealed_concepts=sealed_concepts,
        reviewer_key_policy=reviewer_key_policy,
        worksheet=worksheet,
    )
    if worksheet.worksheet_status != "complete":
        raise RelationPassAWorkflowError(
            "Relation Pass A worksheet must be complete before preparation"
        )
    if (
        worksheet.reviewer_attestation_statement
        != RELATION_PASS_A_REVIEWER_ATTESTATION
    ):
        raise RelationPassAWorkflowError(
            "Relation Pass A reviewer attestation does not match the protocol"
        )
    _reject_future_reviewer_attestation(worksheet.reviewer_attested_at_utc)
    _reject_new_public_source_copy(
        worksheet,
        source_authority=source_authority,
    )

    concept_evidence = {
        concept.concept_key: concept.evidence
        for concept in sealed_concepts.inventory.artifact.concepts
    }
    resolved_decisions = tuple(
        _resolve_pair_decision(
            decision,
            source_authority=source_authority,
            concept_evidence=concept_evidence,
        )
        for decision in worksheet.pair_decisions
    )
    worksheet_sha256 = _model_sha(worksheet)
    protocol = sealed_concepts.protocol.protocol
    source = sealed_concepts.source.materialization
    inventory = sealed_concepts.inventory.artifact
    artifact = RelationPassAArtifact(
        schema_version=1,
        artifact_role="golden_graph_relation_pass_a_artifact",
        status="complete_relation_pass_a_not_adjudicated",
        protocol_id=protocol.protocol_id,
        frozen_protocol_sha256=sealed_concepts.protocol.protocol_sha256,
        semantic_source_catalog_sha256=source.source_catalog_sha256,
        chunk_manifest_sha256=source.chunk_manifest_sha256,
        annotation_guide_sha256=protocol.review.annotation_guide_sha256,
        concept_inventory_sha256=sealed_concepts.inventory.artifact_sha256,
        concept_inventory_seal_sha256=sealed_concepts.seal.artifact_sha256,
        relation_pair_manifest_sha256=(
            sealed_concepts.pair_manifest.artifact_sha256
        ),
        relation_pass_a_worksheet_sha256=worksheet_sha256,
        commitment_nonce_hex=worksheet.commitment_nonce_hex,
        reviewer_key_policy_sha256=reviewer_key_policy.policy_sha256,
        reviewer_key_policy_git_commit=(
            reviewer_key_policy.registration_commit_sha
        ),
        reviewer_id=worksheet.reviewer_id,
        reviewer_actor_kind_declaration="human",
        pass_role="A",
        blind_to_system_proposals_declaration=True,
        software_authenticated_prediction_blindness=False,
        software_authenticated_reviewer_identity=False,
        reviewer_attestation_statement=(
            RELATION_PASS_A_REVIEWER_ATTESTATION
        ),
        reviewer_attested_at_utc=worksheet.reviewer_attested_at_utc,
        minimum_delay_hours_before_pass_b=(
            protocol.review.minimum_delay_hours
        ),
        label_release_policy=RELATION_PASS_A_RELEASE_POLICY,
        concept_count=inventory.concept_count,
        concept_keys=sealed_concepts.pair_manifest.artifact.concept_keys,
        pair_count=len(resolved_decisions),
        none_pair_count=sum(
            item.outcome == "none" for item in resolved_decisions
        ),
        positive_pair_count=sum(
            item.outcome == "relations" for item in resolved_decisions
        ),
        relation_count=sum(
            len(item.relations) for item in resolved_decisions
        ),
        pair_decisions=resolved_decisions,
    )
    artifact_sha256 = _model_sha(artifact)
    seal_request = RelationPassASealRequest(
        schema_version=1,
        artifact_role="golden_graph_relation_pass_a_seal_request",
        namespace=RELATION_PASS_A_ATTESTATION_NAMESPACE,
        protocol_id=artifact.protocol_id,
        frozen_protocol_sha256=artifact.frozen_protocol_sha256,
        semantic_source_catalog_sha256=artifact.semantic_source_catalog_sha256,
        chunk_manifest_sha256=artifact.chunk_manifest_sha256,
        concept_inventory_sha256=artifact.concept_inventory_sha256,
        concept_inventory_seal_sha256=artifact.concept_inventory_seal_sha256,
        relation_pair_manifest_sha256=(
            artifact.relation_pair_manifest_sha256
        ),
        relation_pass_a_artifact_sha256=artifact_sha256,
        relation_pass_a_worksheet_sha256=worksheet_sha256,
        reviewer_key_policy_sha256=artifact.reviewer_key_policy_sha256,
        reviewer_key_policy_git_commit=(
            artifact.reviewer_key_policy_git_commit
        ),
        reviewer_id=artifact.reviewer_id,
        reviewer_actor_kind_declaration="human",
        pass_role="A",
        blind_to_system_proposals_declaration=True,
        software_authenticated_prediction_blindness=False,
        software_authenticated_reviewer_identity=False,
        reviewer_attested_at_utc=artifact.reviewer_attested_at_utc,
        minimum_delay_hours_before_pass_b=(
            artifact.minimum_delay_hours_before_pass_b
        ),
        software_authenticated_minimum_delay=False,
        pair_count=artifact.pair_count,
        labels_embargoed_at_commitment=True,
        label_release_policy=RELATION_PASS_A_RELEASE_POLICY,
        approval_statement=(
            "key_control_approval_only_not_proof_of_humanity_or_blindness"
        ),
    )
    return _issue_prepared(artifact=artifact, seal_request=seal_request)


def signoff_prepared_relation_pass_a(
    *,
    prepared: PreparedRelationPassA,
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
    signature_path: Path,
) -> SignedRelationPassA:
    """Verify Pass A's detached signature and derive its neutral root seal."""

    _require_prepared_consistent(prepared)
    _validate_prepared_policy_binding(prepared, reviewer_key_policy)
    try:
        verified = verify_and_build_detached_key_attestation(
            challenge=prepared.seal_request,
            expected_namespace=RELATION_PASS_A_ATTESTATION_NAMESPACE,
            reviewer_key_policy=reviewer_key_policy,
            signature_path=signature_path,
        )
    except AnnotationAttestationError:
        raise RelationPassAWorkflowError(
            "Relation Pass A key attestation could not be verified"
        ) from None
    attestation_sha256 = _model_sha(verified.artifact)
    artifact = prepared.artifact
    request = prepared.seal_request
    seal = RelationPassASeal(
        schema_version=1,
        artifact_role="golden_graph_relation_pass_a_seal",
        status="relation_pass_a_commitment_only_not_gold_bundle",
        protocol_id=artifact.protocol_id,
        frozen_protocol_sha256=artifact.frozen_protocol_sha256,
        concept_inventory_sha256=artifact.concept_inventory_sha256,
        concept_inventory_seal_sha256=artifact.concept_inventory_seal_sha256,
        relation_pair_manifest_sha256=(
            artifact.relation_pair_manifest_sha256
        ),
        relation_pass_a_artifact_sha256=prepared.artifact_sha256,
        relation_pass_a_seal_request_sha256=prepared.seal_request_sha256,
        detached_attestation_artifact_sha256=attestation_sha256,
        reviewer_key_policy_sha256=request.reviewer_key_policy_sha256,
        reviewer_key_policy_git_commit=request.reviewer_key_policy_git_commit,
        reviewer_id=request.reviewer_id,
        reviewer_actor_kind_declaration="human",
        pass_role="A",
        blind_to_system_proposals_declaration=True,
        software_authenticated_prediction_blindness=False,
        software_authenticated_reviewer_identity=False,
        reviewer_attested_at_utc=request.reviewer_attested_at_utc,
        minimum_delay_hours_before_pass_b=(
            request.minimum_delay_hours_before_pass_b
        ),
        software_authenticated_minimum_delay=False,
        pair_count=request.pair_count,
        labels_embargoed_at_commitment=True,
        labels_unreleased_at_commitment=True,
        label_release_policy=RELATION_PASS_A_RELEASE_POLICY,
        detached_attestation=verified.reference,
    )
    return _issue_signed(
        prepared=prepared,
        attestation_artifact=verified.artifact,
        seal=seal,
    )


def publish_relation_pass_a_stage(
    *,
    signed: SignedRelationPassA,
    paths: RelationPassAStagePaths,
    repository_root: Path,
    public_artifact_root: Path,
    sealed_concepts: SealedConceptInventoryAuthority,
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
) -> SealedRelationPassAAuthority:
    """Publish the private label leaf and neutral public root, seal last."""

    _require_stage_paths(paths)
    signed = _snapshot_signed_for_publication(signed)
    context = _require_relation_context(
        sealed_concepts=sealed_concepts,
        reviewer_key_policy=reviewer_key_policy,
        require_active_policy=True,
    )
    sealed_concepts = context.sealed_concepts
    reviewer_key_policy = context.reviewer_key_policy
    _validate_signed_for_publication(
        signed=signed,
        sealed_concepts=sealed_concepts,
        reviewer_key_policy=reviewer_key_policy,
        source_authority=context.source_authority,
    )
    root = Path(repository_root).resolve(strict=True)
    public_root = Path(public_artifact_root).resolve(strict=True)
    preflights = (
        preflight_private_canonical_artifact(
            paths.private.artifact,
            signed.prepared.artifact,
            repository_root=root,
        ),
        preflight_canonical_artifact(
            paths.public.seal_request,
            signed.prepared.seal_request,
            allowed_root=public_root,
        ),
        preflight_canonical_artifact(
            paths.public.attestation,
            signed.attestation_artifact,
            allowed_root=public_root,
        ),
        preflight_canonical_artifact(
            paths.public.seal,
            signed.seal,
            allowed_root=public_root,
        ),
    )
    if preflights != (
        signed.prepared.artifact_sha256,
        signed.prepared.seal_request_sha256,
        signed.attestation_artifact_sha256,
        signed.seal_sha256,
    ):
        raise RelationPassAWorkflowError(
            "Relation Pass A publication preflight changed canonical bytes"
        )

    published = (
        publish_private_canonical_artifact(
            paths.private.artifact,
            signed.prepared.artifact,
            repository_root=root,
        ),
        publish_canonical_artifact(
            paths.public.seal_request,
            signed.prepared.seal_request,
            allowed_root=public_root,
        ),
        publish_canonical_artifact(
            paths.public.attestation,
            signed.attestation_artifact,
            allowed_root=public_root,
        ),
        publish_canonical_artifact(
            paths.public.seal,
            signed.seal,
            allowed_root=public_root,
        ),
    )
    if published != preflights:
        raise RelationPassAWorkflowError(
            "Relation Pass A publication changed canonical hashes"
        )
    return load_sealed_relation_pass_a(
        paths=paths,
        repository_root=root,
        public_artifact_root=public_root,
        sealed_concepts=sealed_concepts,
        reviewer_key_policy=reviewer_key_policy,
    )


def load_relation_pass_a_commitment(
    *,
    paths: RelationPassAPublicCommitmentPaths,
    public_artifact_root: Path,
    sealed_concepts: SealedConceptInventoryAuthority,
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
) -> RelationPassACommitmentAuthority:
    """Load only neutral public leaves; this API cannot expose Pass A labels."""

    _require_public_paths(paths)
    context = _require_relation_context(
        sealed_concepts=sealed_concepts,
        reviewer_key_policy=reviewer_key_policy,
        require_active_policy=False,
    )
    sealed_concepts = context.sealed_concepts
    reviewer_key_policy = context.reviewer_key_policy
    public_root = Path(public_artifact_root).resolve(strict=True)
    seal_request = load_canonical_artifact(
        paths.seal_request,
        RelationPassASealRequest,
        allowed_root=public_root,
    )
    attestation = load_canonical_artifact(
        paths.attestation,
        DetachedKeyAttestationArtifact,
        allowed_root=public_root,
    )
    seal = load_canonical_artifact(
        paths.seal,
        RelationPassASeal,
        allowed_root=public_root,
    )
    _validate_public_commitment_binding(
        sealed_concepts=sealed_concepts,
        reviewer_key_policy=reviewer_key_policy,
        seal_request=seal_request,
        attestation=attestation,
        seal=seal,
    )
    try:
        verified = verify_embedded_detached_key_attestation(
            challenge=seal_request.artifact,
            expected_namespace=RELATION_PASS_A_ATTESTATION_NAMESPACE,
            reviewer_key_policy=reviewer_key_policy,
            artifact=attestation.artifact,
        )
    except AnnotationAttestationError:
        raise RelationPassAWorkflowError(
            "Published Relation Pass A attestation is invalid"
        ) from None
    if verified.reference != seal.artifact.detached_attestation:
        raise RelationPassAWorkflowError(
            "Relation Pass A seal differs from its verified attestation"
        )
    return _issue_commitment_authority(
        sealed_concepts=sealed_concepts,
        reviewer_key_policy=reviewer_key_policy,
        seal_request=seal_request,
        attestation=attestation,
        seal=seal,
        key_control_receipt=verified.receipt,
    )


def load_sealed_relation_pass_a(
    *,
    paths: RelationPassAStagePaths,
    repository_root: Path,
    public_artifact_root: Path,
    sealed_concepts: SealedConceptInventoryAuthority,
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
) -> SealedRelationPassAAuthority:
    """Deeply reload the local hidden label artifact and public commitment."""

    _require_stage_paths(paths)
    commitment = load_relation_pass_a_commitment(
        paths=paths.public,
        public_artifact_root=public_artifact_root,
        sealed_concepts=sealed_concepts,
        reviewer_key_policy=reviewer_key_policy,
    )
    private_artifact = load_private_canonical_artifact(
        paths.private.artifact,
        RelationPassAArtifact,
        repository_root=repository_root,
    )
    sealed_concepts = commitment.sealed_concepts
    reviewer_key_policy = commitment.reviewer_key_policy
    source_authority = bind_annotation_evidence_source(
        sealed_concepts.source
    )
    _validate_private_artifact_binding(
        sealed_concepts=sealed_concepts,
        reviewer_key_policy=reviewer_key_policy,
        source_authority=source_authority,
        private_artifact=private_artifact,
        seal_request=commitment.seal_request,
        seal=commitment.seal,
    )
    return _issue_sealed_authority(
        commitment=commitment,
        private_artifact=private_artifact,
    )


def default_relation_pass_a_stage_paths(
    repository_root: Path,
    sealed_concepts: SealedConceptInventoryAuthority,
) -> RelationPassAStagePaths:
    """Derive stable private/public paths from the sealed protocol identity."""

    root = Path(repository_root).resolve(strict=True)
    protocol = sealed_concepts.protocol.protocol
    public_directory = (
        root / "backend/golden_graph/artifacts" / protocol.acquisition.corpus_id
    )
    private_directory = (
        root
        / "backend/data/golden_graph/annotations"
        / protocol.protocol_id
    )
    name = protocol.protocol_id
    return RelationPassAStagePaths(
        private=RelationPassAPrivatePaths(
            artifact=(
                private_directory / "relation-pass-a.artifact.private.json"
            ),
        ),
        public=RelationPassAPublicCommitmentPaths(
            seal_request=(
                public_directory / f"{name}.relation-pass-a.seal-request.json"
            ),
            attestation=(
                public_directory / f"{name}.relation-pass-a.attestation.json"
            ),
            seal=public_directory / f"{name}.relation-pass-a.seal.json",
        ),
    )


def _require_public_paths(paths: RelationPassAPublicCommitmentPaths) -> None:
    if not isinstance(paths, RelationPassAPublicCommitmentPaths):
        raise RelationPassAWorkflowError(
            "Public commitment loading requires public-only Relation paths"
        )
    _require_distinct_output_paths(
        (paths.seal_request, paths.attestation, paths.seal),
        label="Relation Pass A public",
    )


def _require_stage_paths(paths: RelationPassAStagePaths) -> None:
    if (
        not isinstance(paths, RelationPassAStagePaths)
        or not isinstance(paths.private, RelationPassAPrivatePaths)
        or not isinstance(paths.public, RelationPassAPublicCommitmentPaths)
    ):
        raise RelationPassAWorkflowError(
            "Relation Pass A publication requires separated stage paths"
        )
    _require_distinct_output_paths(
        (
            paths.private.artifact,
            paths.public.seal_request,
            paths.public.attestation,
            paths.public.seal,
        ),
        label="Relation Pass A stage",
    )


def _require_distinct_output_paths(
    paths: tuple[Path, ...],
    *,
    label: str,
) -> None:
    try:
        identities = tuple(
            path.parent.resolve(strict=True) / path.name for path in paths
        )
    except (OSError, RuntimeError):
        raise RelationPassAWorkflowError(
            f"{label} output parents must already exist"
        ) from None
    if len(set(identities)) != len(identities):
        raise RelationPassAWorkflowError(
            f"{label} output paths must be pairwise distinct"
        )


def _resolve_pair_decision(
    decision: RelationPairDecisionDraft,
    *,
    source_authority: AnnotationEvidenceSourceAuthority,
    concept_evidence: dict[str, tuple[EvidenceSpan, ...]],
) -> RelationPairDecision:
    if decision.outcome == "pending":
        raise RelationPassAWorkflowError(
            "A complete Relation Pass A worksheet contains a pending pair"
        )
    resolved_relations = tuple(
        _resolve_relation_judgment(
            judgment,
            source_authority=source_authority,
            concept_evidence=concept_evidence,
        )
        for judgment in decision.relations
    )
    return RelationPairDecision(
        pair_id=decision.pair_id,
        left_concept_key=decision.left_concept_key,
        right_concept_key=decision.right_concept_key,
        outcome=decision.outcome,
        none_rationale=decision.none_rationale,
        relations=resolved_relations,
    )


def _resolve_relation_judgment(
    judgment: RelationJudgmentDraft,
    *,
    source_authority: AnnotationEvidenceSourceAuthority,
    concept_evidence: dict[str, tuple[EvidenceSpan, ...]],
) -> RelationJudgment:
    try:
        resolved = tuple(
            sorted(
                (
                    RelationEvidenceSpan(
                        support_role=item.support_role,
                        span=resolve_evidence_selection(
                            item.selection,
                            source_authority=source_authority,
                        ),
                    )
                    for item in judgment.evidence
                ),
                key=relation_evidence_sort_key,
            )
        )
    except (AnnotationEvidenceError, TypeError, ValueError, ValidationError):
        raise RelationPassAWorkflowError(
            "Relation evidence selection failed Source validation"
        ) from None
    relation = RelationJudgment(
        relation_type=judgment.relation_type,
        source_concept_key=judgment.source_concept_key,
        target_concept_key=judgment.target_concept_key,
        support_basis=judgment.support_basis,
        proposal_origin_declaration="human",
        evidence=resolved,
        review_rationale=judgment.review_rationale,
    )
    if relation.support_basis == "pedagogical_inference":
        expected_by_role = {
            "source_endpoint": concept_evidence[relation.source_concept_key],
            "target_endpoint": concept_evidence[relation.target_concept_key],
        }
        for evidence in relation.evidence:
            if evidence.span not in expected_by_role[evidence.support_role]:
                raise RelationPassAWorkflowError(
                    "Inferred Relation endpoint evidence must match its sealed "
                    "Concept evidence"
                )
    return relation


def _require_relation_context(
    *,
    sealed_concepts: SealedConceptInventoryAuthority,
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
    require_active_policy: bool,
) -> _VerifiedRelationContext:
    if not isinstance(sealed_concepts, SealedConceptInventoryAuthority):
        raise RelationPassAWorkflowError(
            "Relation Pass A requires a sealed Concept-inventory authority"
        )
    if not isinstance(reviewer_key_policy, ReviewerKeyPolicyAuthority):
        raise RelationPassAWorkflowError(
            "Relation Pass A requires a repository-issued reviewer-key policy"
        )
    try:
        sealed_concepts.__post_init__()
        reviewer_key_policy.__post_init__()
        if require_active_policy:
            reviewer_key_policy = revalidate_active_reviewer_key_policy(
                reviewer_key_policy
            )
        else:
            reviewer_key_policy = load_historical_reviewer_key_policy(
                repository_root=reviewer_key_policy.repository_root,
                protocol_id=reviewer_key_policy.policy.protocol_id,
                frozen_protocol_sha256=(
                    reviewer_key_policy.policy.frozen_protocol_sha256
                ),
                reviewer_id=reviewer_key_policy.policy.reviewer_id,
                registration_commit_sha=(
                    reviewer_key_policy.registration_commit_sha
                ),
            )
        if (
            sealed_concepts.reviewer_key_policy.repository_root
            != reviewer_key_policy.repository_root
        ):
            raise ValueError("Concept and Relation policies use different repos")
        sealed_concepts = _require_sealed_concepts_consistent(
            sealed_concepts,
            repository_root=reviewer_key_policy.repository_root,
        )
        source_authority = bind_annotation_evidence_source(
            sealed_concepts.source
        )
    except (
        AnnotationEvidenceError,
        AttributeError,
        TypeError,
        ReviewerKeyPolicyError,
        SourceSliceBuildError,
        ValueError,
    ):
        raise RelationPassAWorkflowError(
            "Relation Pass A upstream authorities failed deep validation"
        ) from None
    protocol = sealed_concepts.protocol.protocol
    policy = reviewer_key_policy.policy
    if (
        policy.protocol_id != protocol.protocol_id
        or policy.frozen_protocol_sha256
        != sealed_concepts.protocol.protocol_sha256
        or policy.reviewer_id != protocol.review.reviewer_id
        or RELATION_PASS_A_ATTESTATION_NAMESPACE
        not in policy.allowed_namespaces
    ):
        raise RelationPassAWorkflowError(
            "Reviewer-key policy does not authorize Relation Pass A"
        )
    if (
        not protocol.review.both_passes_blind_to_system_proposals
        or not protocol.review.pass_b_blind_to_pass_a_labels
        or protocol.review.minimum_delay_hours != 72
    ):
        raise RelationPassAWorkflowError(
            "Frozen protocol does not authorize delayed blind Relation review"
        )
    return _VerifiedRelationContext(
        sealed_concepts=sealed_concepts,
        reviewer_key_policy=reviewer_key_policy,
        source_authority=source_authority,
    )


def _require_sealed_concepts_consistent(
    authority: SealedConceptInventoryAuthority,
    *,
    repository_root: Path,
) -> SealedConceptInventoryAuthority:
    if not isinstance(authority.protocol, FrozenProtocolAuthority) or not isinstance(
        authority.source,
        PrivateSourceSliceMaterializationReceipt,
    ):
        raise ValueError("Concept authority has invalid upstream capabilities")
    authority.protocol.__post_init__()
    authority.source.__post_init__()
    authority.reviewer_key_policy.__post_init__()
    authority.key_control_receipt.__post_init__()
    protocol_model = GoldenGraphProtocol.model_validate(
        authority.protocol.protocol.model_dump(
            mode="python",
            exclude_none=False,
        )
    )
    if _model_sha(protocol_model) != authority.protocol.protocol_sha256:
        raise ValueError("Frozen protocol authority changed in memory")
    leaves = (
        authority.inventory,
        authority.alias_table,
        authority.seal_request,
        authority.attestation,
        authority.seal,
        authority.pair_manifest,
    )
    for leaf in leaves:
        if not isinstance(leaf, CanonicalArtifactAuthority):
            raise ValueError("Concept authority contains an invalid artifact leaf")
        leaf.__post_init__()
        if _model_sha(leaf.artifact) != leaf.artifact_sha256:
            raise ValueError("Concept authority artifact hash changed in memory")
    protocol = authority.protocol.protocol
    materialization = authority.source.materialization
    inventory = authority.inventory.artifact
    seal = authority.seal.artifact
    manifest = authority.pair_manifest.artifact
    if (
        protocol.protocol_status != "frozen"
        or inventory.protocol_id != protocol.protocol_id
        or inventory.frozen_protocol_sha256 != authority.protocol.protocol_sha256
        or inventory.semantic_source_catalog_sha256
        != materialization.source_catalog_sha256
        or inventory.chunk_manifest_sha256
        != materialization.chunk_manifest_sha256
        or seal.concept_inventory_sha256 != authority.inventory.artifact_sha256
        or seal.gold_alias_table_sha256 != authority.alias_table.artifact_sha256
        or manifest.concept_inventory_sha256
        != authority.inventory.artifact_sha256
        or manifest.concept_inventory_seal_sha256
        != authority.seal.artifact_sha256
    ):
        raise ValueError("Sealed Concept authority has inconsistent lineage")
    root = Path(repository_root).resolve(strict=True)
    expected_protocol_path = (
        root
        / "backend/golden_graph/protocols"
        / f"{protocol_model.protocol_id}.frozen.json"
    ).resolve(strict=True)
    if authority.protocol.artifact_path != expected_protocol_path:
        raise ValueError("Frozen protocol path is not repository-derived")
    decoded_protocol, protocol_digest = load_hashed_canonical_json(
        expected_protocol_path
    )
    disk_protocol = GoldenGraphProtocol.model_validate(decoded_protocol)
    if (
        protocol_digest != authority.protocol.protocol_sha256
        or disk_protocol != protocol_model
        or _model_sha(disk_protocol) != protocol_digest
        or authority.protocol.acquisition_manifest_sha256
        != disk_protocol.acquisition.manifest_sha256
    ):
        raise ValueError("Frozen protocol differs from canonical disk bytes")
    protocol_snapshot = copy(authority.protocol)
    object.__setattr__(protocol_snapshot, "protocol", disk_protocol)
    source_snapshot = load_private_source_slice_materialization(
        repository_root=root,
        artifact_path=authority.source.artifact_path,
        expected_protocol=disk_protocol,
    )
    if (
        source_snapshot.artifact_path != authority.source.artifact_path
        or source_snapshot.artifact_sha256 != authority.source.artifact_sha256
        or source_snapshot.materialization != authority.source.materialization
    ):
        raise ValueError("Private Source differs from canonical disk bytes")
    bind_annotation_evidence_source(source_snapshot)
    concept_policy = load_historical_reviewer_key_policy(
        repository_root=root,
        protocol_id=authority.reviewer_key_policy.policy.protocol_id,
        frozen_protocol_sha256=(
            authority.reviewer_key_policy.policy.frozen_protocol_sha256
        ),
        reviewer_id=authority.reviewer_key_policy.policy.reviewer_id,
        registration_commit_sha=(
            authority.reviewer_key_policy.registration_commit_sha
        ),
    )
    if not _same_historical_policy(
        concept_policy,
        authority.reviewer_key_policy,
    ):
        raise ValueError("Concept reviewer policy differs from Git history")
    expected_paths = default_concept_stage_paths(root, protocol_snapshot)
    actual_paths = ConceptStagePaths(
        inventory=authority.inventory.artifact_path,
        alias_table=authority.alias_table.artifact_path,
        seal_request=authority.seal_request.artifact_path,
        attestation=authority.attestation.artifact_path,
        seal=authority.seal.artifact_path,
        pair_manifest=authority.pair_manifest.artifact_path,
    )
    if actual_paths != expected_paths:
        raise ValueError("Concept authority paths are not repository-derived")
    public_root = (root / "backend/golden_graph/artifacts").resolve(strict=True)
    replay = load_sealed_concept_inventory(
        paths=expected_paths,
        public_artifact_root=public_root,
        frozen_protocol=protocol_snapshot,
        source_materialization=source_snapshot,
        reviewer_key_policy=concept_policy,
    )
    if (
        replay.protocol != authority.protocol
        or replay.source != authority.source
        or replay.inventory != authority.inventory
        or replay.alias_table != authority.alias_table
        or replay.seal_request != authority.seal_request
        or replay.attestation != authority.attestation
        or replay.seal != authority.seal
        or replay.pair_manifest != authority.pair_manifest
        or replay.key_control_receipt != authority.key_control_receipt
    ):
        raise ValueError("Sealed Concept authority differs from disk replay")
    return replay


def _same_historical_policy(
    left: ReviewerKeyPolicyAuthority,
    right: ReviewerKeyPolicyAuthority,
) -> bool:
    return (
        left.policy == right.policy
        and left.repository_root == right.repository_root
        and left.artifact_path == right.artifact_path
        and left.policy_sha256 == right.policy_sha256
        and left.registration_commit_sha == right.registration_commit_sha
        and left.policy_blob_oid == right.policy_blob_oid
    )


def _validate_worksheet_binding(
    *,
    sealed_concepts: SealedConceptInventoryAuthority,
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
    worksheet: RelationPassAWorksheet,
) -> None:
    protocol = sealed_concepts.protocol.protocol
    source = sealed_concepts.source
    inventory = sealed_concepts.inventory.artifact
    manifest = sealed_concepts.pair_manifest.artifact
    expected = (
        protocol.protocol_id,
        sealed_concepts.protocol.protocol_sha256,
        source.materialization.source_catalog_sha256,
        source.materialization.chunk_manifest_sha256,
        source.artifact_sha256,
        protocol.review.annotation_guide_sha256,
        sealed_concepts.inventory.artifact_sha256,
        sealed_concepts.seal.artifact_sha256,
        sealed_concepts.pair_manifest.artifact_sha256,
        reviewer_key_policy.policy_sha256,
        reviewer_key_policy.registration_commit_sha,
        protocol.review.reviewer_id,
        protocol.review.minimum_delay_hours,
        inventory.concept_count,
        tuple(
            (item.concept_key, item.preferred_name)
            for item in inventory.concepts
        ),
        manifest.pair_count,
        tuple(
            (item.pair_id, item.left_concept_key, item.right_concept_key)
            for item in manifest.pairs
        ),
    )
    actual = (
        worksheet.protocol_id,
        worksheet.frozen_protocol_sha256,
        worksheet.semantic_source_catalog_sha256,
        worksheet.chunk_manifest_sha256,
        worksheet.private_materialization_sha256,
        worksheet.annotation_guide_sha256,
        worksheet.concept_inventory_sha256,
        worksheet.concept_inventory_seal_sha256,
        worksheet.relation_pair_manifest_sha256,
        worksheet.reviewer_key_policy_sha256,
        worksheet.reviewer_key_policy_git_commit,
        worksheet.reviewer_id,
        worksheet.minimum_delay_hours_before_pass_b,
        worksheet.concept_count,
        tuple(
            (item.concept_key, item.preferred_name)
            for item in worksheet.concepts
        ),
        worksheet.pair_count,
        tuple(
            (item.pair_id, item.left_concept_key, item.right_concept_key)
            for item in worksheet.pair_decisions
        ),
    )
    if actual != expected:
        raise RelationPassAWorkflowError(
            "Relation Pass A worksheet authority binding is inconsistent"
        )


def _validate_prepared_policy_binding(
    prepared: PreparedRelationPassA,
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
) -> None:
    if not isinstance(reviewer_key_policy, ReviewerKeyPolicyAuthority):
        raise RelationPassAWorkflowError(
            "A repository-issued Relation Pass A policy is required"
        )
    try:
        reviewer_key_policy.__post_init__()
        revalidate_active_reviewer_key_policy(reviewer_key_policy)
    except (AttributeError, ReviewerKeyPolicyError, TypeError, ValueError):
        raise RelationPassAWorkflowError(
            "An active Relation Pass A key policy is required"
        ) from None
    expected = (
        reviewer_key_policy.policy_sha256,
        reviewer_key_policy.registration_commit_sha,
        reviewer_key_policy.policy.reviewer_id,
    )
    if (
        (
            prepared.artifact.reviewer_key_policy_sha256,
            prepared.artifact.reviewer_key_policy_git_commit,
            prepared.artifact.reviewer_id,
        )
        != expected
        or (
            prepared.seal_request.reviewer_key_policy_sha256,
            prepared.seal_request.reviewer_key_policy_git_commit,
            prepared.seal_request.reviewer_id,
        )
        != expected
        or RELATION_PASS_A_ATTESTATION_NAMESPACE
        not in reviewer_key_policy.policy.allowed_namespaces
    ):
        raise RelationPassAWorkflowError(
            "Prepared Relation Pass A is not bound to its key policy"
        )


def _validate_public_commitment_binding(
    *,
    sealed_concepts: SealedConceptInventoryAuthority,
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
    seal_request: CanonicalArtifactAuthority[RelationPassASealRequest],
    attestation: CanonicalArtifactAuthority[DetachedKeyAttestationArtifact],
    seal: CanonicalArtifactAuthority[RelationPassASeal],
) -> None:
    _validate_public_commitment_models(
        sealed_concepts=sealed_concepts,
        reviewer_key_policy=reviewer_key_policy,
        request=seal_request.artifact,
        request_sha256=seal_request.artifact_sha256,
        attestation_sha256=attestation.artifact_sha256,
        root=seal.artifact,
    )


def _validate_public_commitment_models(
    *,
    sealed_concepts: SealedConceptInventoryAuthority,
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
    request: RelationPassASealRequest,
    request_sha256: str,
    attestation_sha256: str,
    root: RelationPassASeal,
) -> None:
    protocol = sealed_concepts.protocol.protocol
    source = sealed_concepts.source.materialization
    expected_request_context = (
        protocol.protocol_id,
        sealed_concepts.protocol.protocol_sha256,
        source.source_catalog_sha256,
        source.chunk_manifest_sha256,
        sealed_concepts.inventory.artifact_sha256,
        sealed_concepts.seal.artifact_sha256,
        sealed_concepts.pair_manifest.artifact_sha256,
        reviewer_key_policy.policy_sha256,
        reviewer_key_policy.registration_commit_sha,
        protocol.review.reviewer_id,
        protocol.review.minimum_delay_hours,
        sealed_concepts.pair_manifest.artifact.pair_count,
    )
    actual_request_context = (
        request.protocol_id,
        request.frozen_protocol_sha256,
        request.semantic_source_catalog_sha256,
        request.chunk_manifest_sha256,
        request.concept_inventory_sha256,
        request.concept_inventory_seal_sha256,
        request.relation_pair_manifest_sha256,
        request.reviewer_key_policy_sha256,
        request.reviewer_key_policy_git_commit,
        request.reviewer_id,
        request.minimum_delay_hours_before_pass_b,
        request.pair_count,
    )
    expected_root = RelationPassASeal(
        schema_version=1,
        artifact_role="golden_graph_relation_pass_a_seal",
        status="relation_pass_a_commitment_only_not_gold_bundle",
        protocol_id=request.protocol_id,
        frozen_protocol_sha256=request.frozen_protocol_sha256,
        concept_inventory_sha256=request.concept_inventory_sha256,
        concept_inventory_seal_sha256=request.concept_inventory_seal_sha256,
        relation_pair_manifest_sha256=request.relation_pair_manifest_sha256,
        relation_pass_a_artifact_sha256=(
            request.relation_pass_a_artifact_sha256
        ),
        relation_pass_a_seal_request_sha256=request_sha256,
        detached_attestation_artifact_sha256=attestation_sha256,
        reviewer_key_policy_sha256=request.reviewer_key_policy_sha256,
        reviewer_key_policy_git_commit=request.reviewer_key_policy_git_commit,
        reviewer_id=request.reviewer_id,
        reviewer_actor_kind_declaration="human",
        pass_role="A",
        blind_to_system_proposals_declaration=True,
        software_authenticated_prediction_blindness=False,
        software_authenticated_reviewer_identity=False,
        reviewer_attested_at_utc=request.reviewer_attested_at_utc,
        minimum_delay_hours_before_pass_b=(
            request.minimum_delay_hours_before_pass_b
        ),
        software_authenticated_minimum_delay=False,
        pair_count=request.pair_count,
        labels_embargoed_at_commitment=True,
        labels_unreleased_at_commitment=True,
        label_release_policy=RELATION_PASS_A_RELEASE_POLICY,
        detached_attestation=root.detached_attestation,
    )
    if actual_request_context != expected_request_context or root != expected_root:
        raise RelationPassAWorkflowError(
            "Published Relation Pass A commitment has inconsistent lineage"
        )
    _reject_future_reviewer_attestation(request.reviewer_attested_at_utc)


def _validate_private_artifact_binding(
    *,
    sealed_concepts: SealedConceptInventoryAuthority,
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
    source_authority: AnnotationEvidenceSourceAuthority,
    private_artifact: CanonicalArtifactAuthority[RelationPassAArtifact],
    seal_request: CanonicalArtifactAuthority[RelationPassASealRequest],
    seal: CanonicalArtifactAuthority[RelationPassASeal],
) -> None:
    _validate_private_artifact_models(
        sealed_concepts=sealed_concepts,
        reviewer_key_policy=reviewer_key_policy,
        source_authority=source_authority,
        artifact=private_artifact.artifact,
        artifact_sha256=private_artifact.artifact_sha256,
        request=seal_request.artifact,
        root=seal.artifact,
    )


def _validate_private_artifact_models(
    *,
    sealed_concepts: SealedConceptInventoryAuthority,
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
    source_authority: AnnotationEvidenceSourceAuthority,
    artifact: RelationPassAArtifact,
    artifact_sha256: str,
    request: RelationPassASealRequest,
    root: RelationPassASeal,
) -> None:
    protocol = sealed_concepts.protocol.protocol
    source = sealed_concepts.source.materialization
    expected = (
        protocol.protocol_id,
        sealed_concepts.protocol.protocol_sha256,
        source.source_catalog_sha256,
        source.chunk_manifest_sha256,
        protocol.review.annotation_guide_sha256,
        sealed_concepts.inventory.artifact_sha256,
        sealed_concepts.seal.artifact_sha256,
        sealed_concepts.pair_manifest.artifact_sha256,
        reviewer_key_policy.policy_sha256,
        reviewer_key_policy.registration_commit_sha,
        protocol.review.reviewer_id,
        protocol.review.minimum_delay_hours,
        sealed_concepts.pair_manifest.artifact.concept_keys,
        sealed_concepts.pair_manifest.artifact.pair_count,
    )
    actual = (
        artifact.protocol_id,
        artifact.frozen_protocol_sha256,
        artifact.semantic_source_catalog_sha256,
        artifact.chunk_manifest_sha256,
        artifact.annotation_guide_sha256,
        artifact.concept_inventory_sha256,
        artifact.concept_inventory_seal_sha256,
        artifact.relation_pair_manifest_sha256,
        artifact.reviewer_key_policy_sha256,
        artifact.reviewer_key_policy_git_commit,
        artifact.reviewer_id,
        artifact.minimum_delay_hours_before_pass_b,
        artifact.concept_keys,
        artifact.pair_count,
    )
    if (
        actual != expected
        or artifact_sha256 != request.relation_pass_a_artifact_sha256
        or artifact_sha256 != root.relation_pass_a_artifact_sha256
        or artifact.relation_pass_a_worksheet_sha256
        != request.relation_pass_a_worksheet_sha256
        or artifact.reviewer_attested_at_utc
        != request.reviewer_attested_at_utc
    ):
        raise RelationPassAWorkflowError(
            "Private Relation Pass A labels differ from their public commitment"
        )
    try:
        reject_public_source_copy(
            tuple(
                value
                for decision in artifact.pair_decisions
                for value in (
                    decision.none_rationale,
                    *(item.review_rationale for item in decision.relations),
                )
                if value is not None
            ),
            source_authority=source_authority,
        )
        for decision in artifact.pair_decisions:
            for relation in decision.relations:
                for evidence in relation.evidence:
                    validate_public_evidence_span(
                        evidence.span,
                        source_authority=source_authority,
                    )
    except AnnotationEvidenceError:
        raise RelationPassAWorkflowError(
            "Persisted Relation Pass A evidence or privacy validation failed"
        ) from None
    _validate_inference_evidence_against_concepts(
        artifact,
        sealed_concepts=sealed_concepts,
    )


def _validate_inference_evidence_against_concepts(
    artifact: RelationPassAArtifact,
    *,
    sealed_concepts: SealedConceptInventoryAuthority,
) -> None:
    concept_evidence = {
        concept.concept_key: concept.evidence
        for concept in sealed_concepts.inventory.artifact.concepts
    }
    for decision in artifact.pair_decisions:
        for relation in decision.relations:
            if relation.support_basis != "pedagogical_inference":
                continue
            expected_by_role = {
                "source_endpoint": concept_evidence[
                    relation.source_concept_key
                ],
                "target_endpoint": concept_evidence[
                    relation.target_concept_key
                ],
            }
            for evidence in relation.evidence:
                if evidence.span not in expected_by_role[evidence.support_role]:
                    raise RelationPassAWorkflowError(
                        "Persisted inference evidence differs from sealed "
                        "Concept evidence"
                    )


def _reject_new_public_source_copy(
    worksheet: RelationPassAWorksheet,
    *,
    source_authority: AnnotationEvidenceSourceAuthority,
) -> None:
    values = tuple(
        value
        for decision in worksheet.pair_decisions
        for value in (
            decision.none_rationale,
            *(item.review_rationale for item in decision.relations),
        )
        if value is not None
    )
    try:
        reject_public_source_copy(values, source_authority=source_authority)
    except AnnotationEvidenceError as exc:
        raise RelationPassAWorkflowError(str(exc)) from None


def _reject_future_reviewer_attestation(value: str | None) -> None:
    if value is None:
        raise RelationPassAWorkflowError(
            "A complete Pass A worksheet needs an attestation time"
        )
    try:
        attested = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        raise RelationPassAWorkflowError(
            "Relation Pass A attestation time is invalid"
        ) from None
    if attested > datetime.now(timezone.utc):
        raise RelationPassAWorkflowError(
            "Relation Pass A attestation time cannot be in the future"
        )


def _validate_signed_for_publication(
    *,
    signed: SignedRelationPassA,
    sealed_concepts: SealedConceptInventoryAuthority,
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
    source_authority: AnnotationEvidenceSourceAuthority,
) -> None:
    """Replay every signed/private binding before the first durable write."""

    _require_signed_consistent(signed)
    _validate_prepared_policy_binding(signed.prepared, reviewer_key_policy)
    _validate_public_commitment_models(
        sealed_concepts=sealed_concepts,
        reviewer_key_policy=reviewer_key_policy,
        request=signed.prepared.seal_request,
        request_sha256=signed.prepared.seal_request_sha256,
        attestation_sha256=signed.attestation_artifact_sha256,
        root=signed.seal,
    )
    try:
        verified = verify_embedded_detached_key_attestation(
            challenge=signed.prepared.seal_request,
            expected_namespace=RELATION_PASS_A_ATTESTATION_NAMESPACE,
            reviewer_key_policy=reviewer_key_policy,
            artifact=signed.attestation_artifact,
        )
    except AnnotationAttestationError:
        raise RelationPassAWorkflowError(
            "Signed Relation Pass A attestation is invalid before publication"
        ) from None
    if verified.reference != signed.seal.detached_attestation:
        raise RelationPassAWorkflowError(
            "Signed Relation Pass A seal differs from its attestation"
        )
    _validate_private_artifact_models(
        sealed_concepts=sealed_concepts,
        reviewer_key_policy=reviewer_key_policy,
        source_authority=source_authority,
        artifact=signed.prepared.artifact,
        artifact_sha256=signed.prepared.artifact_sha256,
        request=signed.prepared.seal_request,
        root=signed.seal,
    )


def _snapshot_signed_for_publication(
    supplied: SignedRelationPassA,
) -> SignedRelationPassA:
    """Detach publication bytes from a caller-owned mutable object graph."""

    _require_signed_consistent(supplied)
    expected_hashes = (
        supplied.prepared.artifact_sha256,
        supplied.prepared.seal_request_sha256,
        supplied.attestation_artifact_sha256,
        supplied.seal_sha256,
    )
    try:
        prepared = _issue_prepared(
            artifact=RelationPassAArtifact.model_validate(
                supplied.prepared.artifact.model_dump(
                    mode="python",
                    exclude_none=False,
                )
            ),
            seal_request=RelationPassASealRequest.model_validate(
                supplied.prepared.seal_request.model_dump(
                    mode="python",
                    exclude_none=False,
                )
            ),
        )
        snapshot = _issue_signed(
            prepared=prepared,
            attestation_artifact=DetachedKeyAttestationArtifact.model_validate(
                supplied.attestation_artifact.model_dump(
                    mode="python",
                    exclude_none=False,
                )
            ),
            seal=RelationPassASeal.model_validate(
                supplied.seal.model_dump(
                    mode="python",
                    exclude_none=False,
                )
            ),
        )
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise RelationPassAWorkflowError(
            "Signed Relation Pass A changed while being snapshotted"
        ) from None
    snapshot_hashes = (
        snapshot.prepared.artifact_sha256,
        snapshot.prepared.seal_request_sha256,
        snapshot.attestation_artifact_sha256,
        snapshot.seal_sha256,
    )
    if snapshot_hashes != expected_hashes:
        raise RelationPassAWorkflowError(
            "Signed Relation Pass A changed while being snapshotted"
        )
    return snapshot


def _require_prepared_consistent(prepared: PreparedRelationPassA) -> None:
    if not isinstance(prepared, PreparedRelationPassA):
        raise RelationPassAWorkflowError(
            "A workflow-issued prepared Relation Pass A is required"
        )
    try:
        prepared.__post_init__()
        artifact = RelationPassAArtifact.model_validate(
            prepared.artifact.model_dump(mode="python", exclude_none=False)
        )
        request = RelationPassASealRequest.model_validate(
            prepared.seal_request.model_dump(
                mode="python",
                exclude_none=False,
            )
        )
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise RelationPassAWorkflowError(
            "Prepared Relation Pass A capability is invalid"
        ) from None
    if (
        artifact != prepared.artifact
        or request != prepared.seal_request
        or _model_sha(prepared.artifact) != prepared.artifact_sha256
        or _model_sha(prepared.seal_request) != prepared.seal_request_sha256
        or prepared.seal_request.relation_pass_a_artifact_sha256
        != prepared.artifact_sha256
        or prepared.seal_request.relation_pass_a_worksheet_sha256
        != prepared.artifact.relation_pass_a_worksheet_sha256
    ):
        raise RelationPassAWorkflowError(
            "Prepared Relation Pass A capability is internally inconsistent"
        )


def _require_signed_consistent(signed: SignedRelationPassA) -> None:
    if not isinstance(signed, SignedRelationPassA):
        raise RelationPassAWorkflowError(
            "A workflow-issued signed Relation Pass A is required"
        )
    try:
        signed.__post_init__()
        attestation = DetachedKeyAttestationArtifact.model_validate(
            signed.attestation_artifact.model_dump(
                mode="python",
                exclude_none=False,
            )
        )
        seal = RelationPassASeal.model_validate(
            signed.seal.model_dump(mode="python", exclude_none=False)
        )
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise RelationPassAWorkflowError(
            "Signed Relation Pass A capability is invalid"
        ) from None
    _require_prepared_consistent(signed.prepared)
    if (
        attestation != signed.attestation_artifact
        or seal != signed.seal
        or _model_sha(signed.attestation_artifact)
        != signed.attestation_artifact_sha256
        or _model_sha(signed.seal) != signed.seal_sha256
        or signed.seal.relation_pass_a_artifact_sha256
        != signed.prepared.artifact_sha256
        or signed.seal.relation_pass_a_seal_request_sha256
        != signed.prepared.seal_request_sha256
        or signed.seal.detached_attestation_artifact_sha256
        != signed.attestation_artifact_sha256
    ):
        raise RelationPassAWorkflowError(
            "Signed Relation Pass A capability is internally inconsistent"
        )


def _issue_prepared(
    *,
    artifact: RelationPassAArtifact,
    seal_request: RelationPassASealRequest,
) -> PreparedRelationPassA:
    prepared = object.__new__(PreparedRelationPassA)
    object.__setattr__(prepared, "artifact", artifact)
    object.__setattr__(prepared, "artifact_sha256", _model_sha(artifact))
    object.__setattr__(prepared, "seal_request", seal_request)
    object.__setattr__(
        prepared,
        "seal_request_sha256",
        _model_sha(seal_request),
    )
    object.__setattr__(prepared, "_validation_token", _PREPARED_TOKEN)
    prepared.__post_init__()
    return prepared


def _issue_signed(
    *,
    prepared: PreparedRelationPassA,
    attestation_artifact: DetachedKeyAttestationArtifact,
    seal: RelationPassASeal,
) -> SignedRelationPassA:
    signed = object.__new__(SignedRelationPassA)
    object.__setattr__(signed, "prepared", prepared)
    object.__setattr__(signed, "attestation_artifact", attestation_artifact)
    object.__setattr__(
        signed,
        "attestation_artifact_sha256",
        _model_sha(attestation_artifact),
    )
    object.__setattr__(signed, "seal", seal)
    object.__setattr__(signed, "seal_sha256", _model_sha(seal))
    object.__setattr__(signed, "_validation_token", _SIGNED_TOKEN)
    signed.__post_init__()
    return signed


def _issue_commitment_authority(
    **values: object,
) -> RelationPassACommitmentAuthority:
    authority = object.__new__(RelationPassACommitmentAuthority)
    for name, value in values.items():
        object.__setattr__(authority, name, value)
    object.__setattr__(authority, "_validation_token", _COMMITMENT_TOKEN)
    authority.__post_init__()
    return authority


def _issue_sealed_authority(
    *,
    commitment: RelationPassACommitmentAuthority,
    private_artifact: CanonicalArtifactAuthority[RelationPassAArtifact],
) -> SealedRelationPassAAuthority:
    authority = object.__new__(SealedRelationPassAAuthority)
    object.__setattr__(authority, "commitment", commitment)
    object.__setattr__(authority, "private_artifact", private_artifact)
    object.__setattr__(authority, "_validation_token", _SEALED_TOKEN)
    authority.__post_init__()
    return authority


def _model_sha(model: object) -> str:
    return hashlib.sha256(canonical_json_bytes(model)).hexdigest()


def _require_sha(value: str, label: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{label} must be a lowercase SHA-256")


def _object_without_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RelationPassAWorkflowError(
                "Relation Pass A worksheet contains a duplicate object key"
            )
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise RelationPassAWorkflowError(
        "Relation Pass A worksheet contains a non-finite number"
    )


__all__ = [
    "MAX_RELATION_PASS_A_WORKSHEET_BYTES",
    "PreparedRelationPassA",
    "RelationPassACommitmentAuthority",
    "RelationPassAPrivatePaths",
    "RelationPassAPublicCommitmentPaths",
    "RelationPassAStagePaths",
    "RelationPassAWorkflowError",
    "SealedRelationPassAAuthority",
    "SignedRelationPassA",
    "default_relation_pass_a_stage_paths",
    "load_relation_pass_a_commitment",
    "load_sealed_relation_pass_a",
    "new_relation_pass_a_worksheet",
    "parse_relation_pass_a_worksheet",
    "prepare_relation_pass_a",
    "publish_relation_pass_a_stage",
    "signoff_prepared_relation_pass_a",
]
