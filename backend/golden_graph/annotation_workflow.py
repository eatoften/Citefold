"""G2.1 Concept-inventory preparation, signing, publication, and reload.

The workflow keeps private Source quotes in the mutable worksheet, resolves
them to redacted logical byte spans, and publishes only after an external
OpenSSH key attests to the exact redacted commitment.  The signature proves
key control, not that a reviewer is human or that the declaration is true.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from itertools import combinations
import json
from pathlib import Path

from pydantic import ValidationError

from app.concept_graph import normalize_alias_key

from .annotation_artifacts import (
    CanonicalArtifactAuthority,
    load_canonical_artifact,
    preflight_canonical_artifact,
    publish_canonical_artifact,
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
    evidence_span_sort_key,
    reject_public_source_copy,
    resolve_evidence_selection,
    validate_public_evidence_span,
)
from .annotation_models import (
    CONCEPT_ATTESTATION_NAMESPACE,
    ConceptAnnotationWorksheet,
    ConceptInventory,
    ConceptInventorySeal,
    ConceptInventorySealRequest,
    DetachedKeyAttestationArtifact,
    EvidenceSelectionDraft,
    EvidenceSpan,
    GoldAliasEntry,
    GoldAliasTable,
    RelationPair,
    RelationPairManifest,
    SealedConcept,
    relation_pair_id,
)
from .canonical_io import canonical_json_bytes
from .protocol import FrozenProtocolAuthority
from .reviewer_policy import (
    ReviewerKeyPolicyAuthority,
    ReviewerKeyPolicyError,
    revalidate_active_reviewer_key_policy,
)
from .source_slice_builder import PrivateSourceSliceMaterializationReceipt
from .ssh_attestation import ExternalMaintainerAttestationReceipt


CONCEPT_REVIEWER_ATTESTATION = (
    "I personally authored this prediction-blind Concept inventory and approve "
    "its redacted commitment for maintainer signing."
)
MAX_WORKSHEET_BYTES = 2 * 1024 * 1024

_PREPARED_TOKEN = object()
_SIGNED_TOKEN = object()
_SEALED_TOKEN = object()


class ConceptAnnotationWorkflowError(ValueError):
    """Raised when a G2.1 state transition cannot be authorized safely."""


@dataclass(frozen=True, slots=True, init=False)
class PreparedConceptInventory:
    """Validated redacted candidate leaves awaiting external key approval."""

    inventory: ConceptInventory
    inventory_sha256: str
    alias_table: GoldAliasTable
    alias_table_sha256: str
    seal_request: ConceptInventorySealRequest
    seal_request_sha256: str
    excluded_candidate_count: int
    total_candidate_count: int
    _validation_token: object = field(repr=False, compare=False)

    def __init__(self, *_args, **_kwargs) -> None:
        raise TypeError("PreparedConceptInventory must come from its workflow")

    def __post_init__(self) -> None:
        if self._validation_token is not _PREPARED_TOKEN:
            raise ValueError("Invalid prepared Concept-inventory token")
        _require_sha(self.inventory_sha256, "inventory_sha256")
        _require_sha(self.alias_table_sha256, "alias_table_sha256")
        _require_sha(self.seal_request_sha256, "seal_request_sha256")


@dataclass(frozen=True, slots=True, init=False)
class SignedConceptInventory:
    """Complete public leaves derived after a valid key-control attestation."""

    prepared: PreparedConceptInventory
    attestation_artifact: DetachedKeyAttestationArtifact
    attestation_artifact_sha256: str
    seal: ConceptInventorySeal
    seal_sha256: str
    pair_manifest: RelationPairManifest
    pair_manifest_sha256: str
    _validation_token: object = field(repr=False, compare=False)

    def __init__(self, *_args, **_kwargs) -> None:
        raise TypeError("SignedConceptInventory must come from its workflow")

    def __post_init__(self) -> None:
        if self._validation_token is not _SIGNED_TOKEN:
            raise ValueError("Invalid signed Concept-inventory token")
        for label, digest in (
            ("attestation_artifact_sha256", self.attestation_artifact_sha256),
            ("seal_sha256", self.seal_sha256),
            ("pair_manifest_sha256", self.pair_manifest_sha256),
        ):
            _require_sha(digest, label)


@dataclass(frozen=True, slots=True)
class ConceptStagePaths:
    """Exact public paths for one immutable G2.1 artifact DAG."""

    inventory: Path
    alias_table: Path
    seal_request: Path
    attestation: Path
    seal: Path
    pair_manifest: Path


@dataclass(frozen=True, slots=True, init=False)
class SealedConceptInventoryAuthority:
    """Token-gated, source-verified authority for the published G2.1 stage.

    It proves a deterministic artifact DAG, current private evidence bytes, and
    an authorized signing key.  It does not prove reviewer humanity or a
    completed Relation gold bundle.
    """

    protocol: FrozenProtocolAuthority
    source: PrivateSourceSliceMaterializationReceipt = field(repr=False)
    reviewer_key_policy: ReviewerKeyPolicyAuthority
    inventory: CanonicalArtifactAuthority[ConceptInventory]
    alias_table: CanonicalArtifactAuthority[GoldAliasTable]
    seal_request: CanonicalArtifactAuthority[ConceptInventorySealRequest]
    attestation: CanonicalArtifactAuthority[DetachedKeyAttestationArtifact]
    seal: CanonicalArtifactAuthority[ConceptInventorySeal]
    pair_manifest: CanonicalArtifactAuthority[RelationPairManifest]
    key_control_receipt: ExternalMaintainerAttestationReceipt
    _validation_token: object = field(repr=False, compare=False)

    def __init__(self, *_args, **_kwargs) -> None:
        raise TypeError(
            "SealedConceptInventoryAuthority must come from its strict loader"
        )

    def __post_init__(self) -> None:
        if self._validation_token is not _SEALED_TOKEN:
            raise ValueError("Invalid sealed Concept-inventory authority token")


def new_concept_annotation_worksheet(
    *,
    frozen_protocol: FrozenProtocolAuthority,
    source_materialization: PrivateSourceSliceMaterializationReceipt,
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
    worksheet_id: str,
) -> ConceptAnnotationWorksheet:
    """Create an empty private worksheet; no labels or attestation are filled."""

    _bind_annotation_source(source_materialization)
    _validate_upstream(frozen_protocol, source_materialization)
    _validate_reviewer_key_policy(frozen_protocol, reviewer_key_policy)
    _require_active_policy(reviewer_key_policy)
    protocol = frozen_protocol.protocol
    materialization = source_materialization.materialization
    return ConceptAnnotationWorksheet(
        schema_version=1,
        artifact_role="golden_graph_concept_annotation_worksheet",
        worksheet_status="draft",
        worksheet_id=worksheet_id,
        protocol_id=protocol.protocol_id,
        frozen_protocol_sha256=frozen_protocol.protocol_sha256,
        semantic_source_catalog_sha256=(
            materialization.source_catalog_sha256
        ),
        chunk_manifest_sha256=materialization.chunk_manifest_sha256,
        private_materialization_sha256=(
            source_materialization.artifact_sha256
        ),
        annotation_guide_sha256=protocol.review.annotation_guide_sha256,
        reviewer_key_policy_sha256=reviewer_key_policy.policy_sha256,
        reviewer_key_policy_git_commit=(
            reviewer_key_policy.registration_commit_sha
        ),
        reviewer_id=protocol.review.reviewer_id,
        blind_to_system_proposals_declaration=True,
        software_authenticated_prediction_blindness=False,
        software_authenticated_reviewer_identity=False,
        reviewer_actor_kind_declaration=None,
        reviewer_attestation_statement=None,
        reviewer_attested_at_utc=None,
        candidates=(),
    )


def parse_concept_annotation_worksheet(
    payload: bytes,
) -> ConceptAnnotationWorksheet:
    """Parse a bounded human-edited worksheet without echoing private input."""

    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_WORKSHEET_BYTES:
        raise ConceptAnnotationWorkflowError(
            "Concept worksheet is empty or exceeds its byte limit"
        )
    try:
        decoded = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
        return ConceptAnnotationWorksheet.model_validate(decoded)
    except ConceptAnnotationWorkflowError:
        raise
    except (
        UnicodeError,
        ValueError,
        TypeError,
        RecursionError,
        OverflowError,
        ValidationError,
    ) as exc:
        raise ConceptAnnotationWorkflowError(
            "Concept worksheet failed strict validation"
        ) from exc


def prepare_concept_inventory(
    *,
    frozen_protocol: FrozenProtocolAuthority,
    source_materialization: PrivateSourceSliceMaterializationReceipt,
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
    worksheet: ConceptAnnotationWorksheet,
) -> PreparedConceptInventory:
    """Resolve one complete private worksheet into redacted unsigned leaves."""

    source_authority = _bind_annotation_source(source_materialization)
    _validate_upstream(frozen_protocol, source_materialization)
    _validate_reviewer_key_policy(frozen_protocol, reviewer_key_policy)
    _require_active_policy(reviewer_key_policy)
    try:
        worksheet = ConceptAnnotationWorksheet.model_validate(
            worksheet.model_dump(mode="python", exclude_none=False)
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise ConceptAnnotationWorkflowError(
            "Concept worksheet failed canonical model validation"
        ) from exc
    _validate_worksheet_binding(
        frozen_protocol=frozen_protocol,
        source_materialization=source_materialization,
        reviewer_key_policy=reviewer_key_policy,
        worksheet=worksheet,
    )
    if worksheet.worksheet_status != "complete":
        raise ConceptAnnotationWorkflowError(
            "Concept worksheet must be complete before preparation"
        )
    if worksheet.reviewer_attestation_statement != CONCEPT_REVIEWER_ATTESTATION:
        raise ConceptAnnotationWorkflowError(
            "Concept worksheet reviewer attestation does not match the protocol"
        )
    _reject_future_reviewer_attestation(worksheet.reviewer_attested_at_utc)

    public_concept_values = tuple(
        value
        for decision in worksheet.candidates
        if decision.decision == "include"
        for value in (
            decision.candidate_key,
            decision.preferred_name,
            decision.short_definition,
            *decision.aliases,
            decision.decision_rationale,
        )
        if value is not None
    )
    _reject_public_source_copy(
        public_concept_values,
        source_authority=source_authority,
    )
    concepts: list[SealedConcept] = []
    excluded_count = 0
    for decision in worksheet.candidates:
        if decision.decision == "exclude":
            excluded_count += 1
            continue
        assert decision.preferred_name is not None
        assert decision.short_definition is not None
        resolved = tuple(
            sorted(
                (
                    _resolve_evidence_selection(
                        selection,
                        source_authority=source_authority,
                    )
                    for selection in decision.evidence
                ),
                key=evidence_span_sort_key,
            )
        )
        concepts.append(
            SealedConcept(
                concept_key=decision.candidate_key,
                preferred_name=decision.preferred_name,
                short_definition=decision.short_definition,
                aliases=decision.aliases,
                review_status="accepted",
                validity_status="current",
                proposal_origin_declaration="human",
                evidence=resolved,
                review_rationale=decision.decision_rationale,
            )
        )

    concepts.sort(key=lambda concept: concept.concept_key)
    worksheet_sha256 = _model_sha(worksheet)
    materialization = source_materialization.materialization
    inventory = ConceptInventory(
        schema_version=1,
        artifact_role="golden_graph_concept_inventory",
        protocol_id=frozen_protocol.protocol.protocol_id,
        frozen_protocol_sha256=frozen_protocol.protocol_sha256,
        semantic_source_catalog_sha256=materialization.source_catalog_sha256,
        chunk_manifest_sha256=materialization.chunk_manifest_sha256,
        concept_annotation_worksheet_sha256=worksheet_sha256,
        reviewer_key_policy_sha256=reviewer_key_policy.policy_sha256,
        reviewer_key_policy_git_commit=(
            reviewer_key_policy.registration_commit_sha
        ),
        reviewer_id=worksheet.reviewer_id,
        reviewer_actor_kind_declaration="human",
        blind_to_system_proposals_declaration=True,
        software_authenticated_prediction_blindness=False,
        software_authenticated_reviewer_identity=False,
        concept_count=len(concepts),
        concepts=tuple(concepts),
    )
    inventory_sha256 = _model_sha(inventory)
    alias_table = _build_alias_table(inventory, inventory_sha256)
    alias_table_sha256 = _model_sha(alias_table)
    seal_request = ConceptInventorySealRequest(
        schema_version=1,
        artifact_role="golden_graph_concept_inventory_seal_request",
        namespace=CONCEPT_ATTESTATION_NAMESPACE,
        protocol_id=inventory.protocol_id,
        frozen_protocol_sha256=inventory.frozen_protocol_sha256,
        semantic_source_catalog_sha256=(
            inventory.semantic_source_catalog_sha256
        ),
        chunk_manifest_sha256=inventory.chunk_manifest_sha256,
        concept_inventory_sha256=inventory_sha256,
        gold_alias_table_sha256=alias_table_sha256,
        concept_annotation_worksheet_sha256=worksheet_sha256,
        reviewer_key_policy_sha256=reviewer_key_policy.policy_sha256,
        reviewer_key_policy_git_commit=(
            reviewer_key_policy.registration_commit_sha
        ),
        reviewer_id=inventory.reviewer_id,
        reviewer_actor_kind_declaration="human",
        blind_to_system_proposals_declaration=True,
        software_authenticated_prediction_blindness=False,
        software_authenticated_reviewer_identity=False,
        concept_count=inventory.concept_count,
        excluded_candidate_count=excluded_count,
        total_candidate_count=len(worksheet.candidates),
        approval_statement="key_control_approval_only_not_proof_of_humanity",
    )
    return _issue_prepared(
        inventory=inventory,
        alias_table=alias_table,
        seal_request=seal_request,
        excluded_candidate_count=excluded_count,
        total_candidate_count=len(worksheet.candidates),
    )


def signoff_prepared_concept_inventory(
    *,
    prepared: PreparedConceptInventory,
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
    signature_path: Path,
) -> SignedConceptInventory:
    """Verify detached key approval and derive the Concept seal and all pairs."""

    _require_prepared_consistent(prepared)
    _validate_prepared_policy_binding(prepared, reviewer_key_policy)
    try:
        verified_attestation = verify_and_build_detached_key_attestation(
            challenge=prepared.seal_request,
            expected_namespace=CONCEPT_ATTESTATION_NAMESPACE,
            reviewer_key_policy=reviewer_key_policy,
            signature_path=signature_path,
        )
    except AnnotationAttestationError:
        raise ConceptAnnotationWorkflowError(
            "Concept-inventory key attestation could not be verified"
        ) from None
    attestation_artifact = verified_attestation.artifact
    attestation_sha256 = _model_sha(attestation_artifact)
    reference = verified_attestation.reference
    seal = ConceptInventorySeal(
        schema_version=1,
        artifact_role="golden_graph_concept_inventory_seal",
        status="concept_inventory_only_not_gold_bundle",
        protocol_id=prepared.inventory.protocol_id,
        frozen_protocol_sha256=prepared.inventory.frozen_protocol_sha256,
        concept_inventory_sha256=prepared.inventory_sha256,
        gold_alias_table_sha256=prepared.alias_table_sha256,
        concept_inventory_seal_request_sha256=prepared.seal_request_sha256,
        detached_attestation_artifact_sha256=attestation_sha256,
        reviewer_key_policy_sha256=reviewer_key_policy.policy_sha256,
        reviewer_key_policy_git_commit=(
            reviewer_key_policy.registration_commit_sha
        ),
        reviewer_id=prepared.inventory.reviewer_id,
        reviewer_actor_kind_declaration="human",
        blind_to_system_proposals_declaration=True,
        software_authenticated_prediction_blindness=False,
        software_authenticated_reviewer_identity=False,
        concept_count=prepared.inventory.concept_count,
        excluded_candidate_count=prepared.excluded_candidate_count,
        total_candidate_count=prepared.total_candidate_count,
        detached_attestation=reference,
    )
    seal_sha256 = _model_sha(seal)
    pair_manifest = _build_pair_manifest(
        prepared.inventory,
        concept_inventory_sha256=prepared.inventory_sha256,
        seal_sha256=seal_sha256,
    )
    return _issue_signed(
        prepared=prepared,
        attestation_artifact=attestation_artifact,
        seal=seal,
        pair_manifest=pair_manifest,
    )


def publish_concept_inventory_stage(
    *,
    signed: SignedConceptInventory,
    paths: ConceptStagePaths,
    public_artifact_root: Path,
    frozen_protocol: FrozenProtocolAuthority,
    source_materialization: PrivateSourceSliceMaterializationReceipt,
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
) -> SealedConceptInventoryAuthority:
    """Publish the six-leaf G2.1 DAG without overwrite, then reload it."""

    _bind_annotation_source(source_materialization)
    _validate_upstream(frozen_protocol, source_materialization)
    _validate_reviewer_key_policy(frozen_protocol, reviewer_key_policy)
    _require_active_policy(reviewer_key_policy)
    _validate_prepared_policy_binding(signed.prepared, reviewer_key_policy)
    _require_signed_consistent(signed)
    if (
        signed.seal.reviewer_key_policy_sha256
        != reviewer_key_policy.policy_sha256
        or signed.seal.reviewer_key_policy_git_commit
        != reviewer_key_policy.registration_commit_sha
    ):
        raise ConceptAnnotationWorkflowError(
            "Signed Concept seal is not bound to reviewer-key policy"
        )
    leaves = (
        (paths.inventory, signed.prepared.inventory, signed.prepared.inventory_sha256),
        (
            paths.alias_table,
            signed.prepared.alias_table,
            signed.prepared.alias_table_sha256,
        ),
        (
            paths.seal_request,
            signed.prepared.seal_request,
            signed.prepared.seal_request_sha256,
        ),
        (
            paths.attestation,
            signed.attestation_artifact,
            signed.attestation_artifact_sha256,
        ),
        (
            paths.pair_manifest,
            signed.pair_manifest,
            signed.pair_manifest_sha256,
        ),
        # The seal is the DAG root and is deliberately published last.  A
        # crash can leave recoverable orphans, never an authoritative root.
        (paths.seal, signed.seal, signed.seal_sha256),
    )
    for path, artifact, expected_hash in leaves:
        preflight_hash = preflight_canonical_artifact(
            path,
            artifact,
            allowed_root=public_artifact_root,
        )
        if preflight_hash != expected_hash:
            raise ConceptAnnotationWorkflowError(
                "Concept-stage preflight hash changed unexpectedly"
            )
    for path, artifact, expected_hash in leaves:
        actual = publish_canonical_artifact(
            path,
            artifact,
            allowed_root=public_artifact_root,
        )
        if actual != expected_hash:
            raise ConceptAnnotationWorkflowError(
                "Published Concept-stage artifact hash changed unexpectedly"
            )
    return load_sealed_concept_inventory(
        paths=paths,
        public_artifact_root=public_artifact_root,
        frozen_protocol=frozen_protocol,
        source_materialization=source_materialization,
        reviewer_key_policy=reviewer_key_policy,
    )


def load_sealed_concept_inventory(
    *,
    paths: ConceptStagePaths,
    public_artifact_root: Path,
    frozen_protocol: FrozenProtocolAuthority,
    source_materialization: PrivateSourceSliceMaterializationReceipt,
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
) -> SealedConceptInventoryAuthority:
    """Reload and deeply verify a persisted Concept seal and complete pairs."""

    source_authority = _bind_annotation_source(source_materialization)
    _validate_upstream(frozen_protocol, source_materialization)
    _validate_reviewer_key_policy(frozen_protocol, reviewer_key_policy)
    inventory = load_canonical_artifact(
        paths.inventory,
        ConceptInventory,
        allowed_root=public_artifact_root,
    )
    alias_table = load_canonical_artifact(
        paths.alias_table,
        GoldAliasTable,
        allowed_root=public_artifact_root,
    )
    seal_request = load_canonical_artifact(
        paths.seal_request,
        ConceptInventorySealRequest,
        allowed_root=public_artifact_root,
    )
    attestation = load_canonical_artifact(
        paths.attestation,
        DetachedKeyAttestationArtifact,
        allowed_root=public_artifact_root,
    )
    seal = load_canonical_artifact(
        paths.seal,
        ConceptInventorySeal,
        allowed_root=public_artifact_root,
    )
    pair_manifest = load_canonical_artifact(
        paths.pair_manifest,
        RelationPairManifest,
        allowed_root=public_artifact_root,
    )
    _validate_loaded_inventory_binding(
        frozen_protocol=frozen_protocol,
        source_materialization=source_materialization,
        source_authority=source_authority,
        reviewer_key_policy=reviewer_key_policy,
        inventory=inventory,
    )
    expected_aliases = _build_alias_table(
        inventory.artifact,
        inventory.artifact_sha256,
    )
    if alias_table.artifact != expected_aliases:
        raise ConceptAnnotationWorkflowError(
            "Published alias table is not derived from the Concept inventory"
        )
    expected_request = _expected_seal_request(
        inventory=inventory,
        alias_table=alias_table,
        seal=seal.artifact,
    )
    if seal_request.artifact != expected_request:
        raise ConceptAnnotationWorkflowError(
            "Published Concept seal request has inconsistent bindings"
        )
    try:
        verified_attestation = verify_embedded_detached_key_attestation(
            challenge=seal_request.artifact,
            expected_namespace=CONCEPT_ATTESTATION_NAMESPACE,
            reviewer_key_policy=reviewer_key_policy,
            artifact=attestation.artifact,
        )
    except AnnotationAttestationError:
        raise ConceptAnnotationWorkflowError(
            "Embedded Concept-inventory key attestation is invalid"
        ) from None
    key_receipt = verified_attestation.receipt
    expected_reference = verified_attestation.reference
    expected_seal = ConceptInventorySeal(
        schema_version=1,
        artifact_role="golden_graph_concept_inventory_seal",
        status="concept_inventory_only_not_gold_bundle",
        protocol_id=inventory.artifact.protocol_id,
        frozen_protocol_sha256=inventory.artifact.frozen_protocol_sha256,
        concept_inventory_sha256=inventory.artifact_sha256,
        gold_alias_table_sha256=alias_table.artifact_sha256,
        concept_inventory_seal_request_sha256=seal_request.artifact_sha256,
        detached_attestation_artifact_sha256=attestation.artifact_sha256,
        reviewer_key_policy_sha256=reviewer_key_policy.policy_sha256,
        reviewer_key_policy_git_commit=(
            reviewer_key_policy.registration_commit_sha
        ),
        reviewer_id=inventory.artifact.reviewer_id,
        reviewer_actor_kind_declaration="human",
        blind_to_system_proposals_declaration=True,
        software_authenticated_prediction_blindness=False,
        software_authenticated_reviewer_identity=False,
        concept_count=inventory.artifact.concept_count,
        excluded_candidate_count=seal_request.artifact.excluded_candidate_count,
        total_candidate_count=seal_request.artifact.total_candidate_count,
        detached_attestation=expected_reference,
    )
    if seal.artifact != expected_seal:
        raise ConceptAnnotationWorkflowError(
            "Published Concept inventory seal is not deeply bound"
        )
    expected_pairs = _build_pair_manifest(
        inventory.artifact,
        concept_inventory_sha256=inventory.artifact_sha256,
        seal_sha256=seal.artifact_sha256,
    )
    if pair_manifest.artifact != expected_pairs:
        raise ConceptAnnotationWorkflowError(
            "Published Relation pair manifest is incomplete or inconsistent"
        )
    return _issue_sealed_authority(
        protocol=frozen_protocol,
        source=source_materialization,
        reviewer_key_policy=reviewer_key_policy,
        inventory=inventory,
        alias_table=alias_table,
        seal_request=seal_request,
        attestation=attestation,
        seal=seal,
        pair_manifest=pair_manifest,
        key_control_receipt=key_receipt,
    )


def default_concept_stage_paths(
    repository_root: Path,
    frozen_protocol: FrozenProtocolAuthority,
) -> ConceptStagePaths:
    """Return stable public G2.1 paths derived only from the protocol identity."""

    root = Path(repository_root).resolve(strict=True)
    directory = (
        root
        / "backend/golden_graph/artifacts"
        / frozen_protocol.protocol.acquisition.corpus_id
    )
    name = frozen_protocol.protocol.protocol_id
    return ConceptStagePaths(
        inventory=directory / f"{name}.concept-inventory.json",
        alias_table=directory / f"{name}.alias-table.json",
        seal_request=directory / f"{name}.concept-seal-request.json",
        attestation=directory / f"{name}.concept-attestation.json",
        seal=directory / f"{name}.concept-inventory.seal.json",
        pair_manifest=directory / f"{name}.relation-pairs.manifest.json",
    )


def _bind_annotation_source(
    source_materialization: PrivateSourceSliceMaterializationReceipt,
) -> AnnotationEvidenceSourceAuthority:
    try:
        return bind_annotation_evidence_source(source_materialization)
    except AnnotationEvidenceError:
        raise ConceptAnnotationWorkflowError(
            "G2.1 requires a deeply validated private Source authority"
        ) from None


def _validate_upstream(
    frozen_protocol: FrozenProtocolAuthority,
    source_materialization: PrivateSourceSliceMaterializationReceipt,
) -> None:
    if not isinstance(frozen_protocol, FrozenProtocolAuthority) or not isinstance(
        source_materialization,
        PrivateSourceSliceMaterializationReceipt,
    ):
        raise ConceptAnnotationWorkflowError(
            "G2.1 requires loader-issued protocol and Source authorities"
        )
    try:
        frozen_protocol.__post_init__()
        source_materialization.__post_init__()
        protocol = frozen_protocol.protocol
        materialization = source_materialization.materialization
    except (AttributeError, TypeError, ValueError):
        raise ConceptAnnotationWorkflowError(
            "G2.1 requires loader-issued protocol and Source authorities"
        ) from None
    if (
        protocol.protocol_status != "frozen"
        or materialization.protocol_id != protocol.protocol_id
        or materialization.source_catalog_sha256
        != protocol.projection.semantic_source_catalog_sha256
        or materialization.chunk_manifest_sha256
        != protocol.projection.chunk_manifest_sha256
    ):
        raise ConceptAnnotationWorkflowError(
            "Frozen protocol and private Source materialization do not match"
        )


def _validate_reviewer_key_policy(
    frozen_protocol: FrozenProtocolAuthority,
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
) -> None:
    if not isinstance(reviewer_key_policy, ReviewerKeyPolicyAuthority):
        raise ConceptAnnotationWorkflowError(
            "G2.1 requires a repository-issued reviewer-key policy authority"
        )
    try:
        reviewer_key_policy.__post_init__()
        policy = reviewer_key_policy.policy
        protocol = frozen_protocol.protocol
    except (AttributeError, TypeError, ValueError):
        raise ConceptAnnotationWorkflowError(
            "G2.1 requires a repository-issued reviewer-key policy authority"
        ) from None
    if (
        policy.protocol_id != protocol.protocol_id
        or policy.frozen_protocol_sha256 != frozen_protocol.protocol_sha256
        or policy.reviewer_id != protocol.review.reviewer_id
        or CONCEPT_ATTESTATION_NAMESPACE not in policy.allowed_namespaces
    ):
        raise ConceptAnnotationWorkflowError(
            "Reviewer-key policy does not authorize this frozen protocol"
        )


def _validate_prepared_policy_binding(
    prepared: PreparedConceptInventory,
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
) -> None:
    if not isinstance(reviewer_key_policy, ReviewerKeyPolicyAuthority):
        raise ConceptAnnotationWorkflowError(
            "A repository-issued reviewer-key policy authority is required"
        )
    try:
        reviewer_key_policy.__post_init__()
        revalidate_active_reviewer_key_policy(reviewer_key_policy)
    except (AttributeError, ReviewerKeyPolicyError, TypeError, ValueError):
        raise ConceptAnnotationWorkflowError(
            "A repository-issued reviewer-key policy authority is required"
        ) from None
    expected = (
        reviewer_key_policy.policy_sha256,
        reviewer_key_policy.registration_commit_sha,
        reviewer_key_policy.policy.reviewer_id,
    )
    if (
        (
            prepared.inventory.reviewer_key_policy_sha256,
            prepared.inventory.reviewer_key_policy_git_commit,
            prepared.inventory.reviewer_id,
        )
        != expected
        or (
            prepared.seal_request.reviewer_key_policy_sha256,
            prepared.seal_request.reviewer_key_policy_git_commit,
            prepared.seal_request.reviewer_id,
        )
        != expected
        or CONCEPT_ATTESTATION_NAMESPACE
        not in reviewer_key_policy.policy.allowed_namespaces
    ):
        raise ConceptAnnotationWorkflowError(
            "Prepared Concept inventory is not bound to reviewer-key policy"
        )


def _require_active_policy(
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
) -> None:
    try:
        revalidate_active_reviewer_key_policy(reviewer_key_policy)
    except ReviewerKeyPolicyError:
        raise ConceptAnnotationWorkflowError(
            "G2.1 authoring requires an active current reviewer-key policy"
        ) from None


def _validate_worksheet_binding(
    *,
    frozen_protocol: FrozenProtocolAuthority,
    source_materialization: PrivateSourceSliceMaterializationReceipt,
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
    worksheet: ConceptAnnotationWorksheet,
) -> None:
    protocol = frozen_protocol.protocol
    materialization = source_materialization.materialization
    expected = (
        protocol.protocol_id,
        frozen_protocol.protocol_sha256,
        materialization.source_catalog_sha256,
        materialization.chunk_manifest_sha256,
        source_materialization.artifact_sha256,
        protocol.review.annotation_guide_sha256,
        reviewer_key_policy.policy_sha256,
        reviewer_key_policy.registration_commit_sha,
        protocol.review.reviewer_id,
    )
    actual = (
        worksheet.protocol_id,
        worksheet.frozen_protocol_sha256,
        worksheet.semantic_source_catalog_sha256,
        worksheet.chunk_manifest_sha256,
        worksheet.private_materialization_sha256,
        worksheet.annotation_guide_sha256,
        worksheet.reviewer_key_policy_sha256,
        worksheet.reviewer_key_policy_git_commit,
        worksheet.reviewer_id,
    )
    if actual != expected:
        raise ConceptAnnotationWorkflowError(
            "Concept worksheet authority binding does not match G0.2"
        )


def _resolve_evidence_selection(
    selection: EvidenceSelectionDraft,
    *,
    source_authority: AnnotationEvidenceSourceAuthority,
) -> EvidenceSpan:
    try:
        return resolve_evidence_selection(
            selection,
            source_authority=source_authority,
        )
    except AnnotationEvidenceError as exc:
        raise ConceptAnnotationWorkflowError(str(exc)) from None


def _validate_loaded_inventory_binding(
    *,
    frozen_protocol: FrozenProtocolAuthority,
    source_materialization: PrivateSourceSliceMaterializationReceipt,
    source_authority: AnnotationEvidenceSourceAuthority,
    reviewer_key_policy: ReviewerKeyPolicyAuthority,
    inventory: CanonicalArtifactAuthority[ConceptInventory],
) -> None:
    protocol = frozen_protocol.protocol
    materialization = source_materialization.materialization
    if (
        inventory.artifact.protocol_id != protocol.protocol_id
        or inventory.artifact.frozen_protocol_sha256
        != frozen_protocol.protocol_sha256
        or inventory.artifact.semantic_source_catalog_sha256
        != materialization.source_catalog_sha256
        or inventory.artifact.chunk_manifest_sha256
        != materialization.chunk_manifest_sha256
        or inventory.artifact.reviewer_id != protocol.review.reviewer_id
        or inventory.artifact.reviewer_key_policy_sha256
        != reviewer_key_policy.policy_sha256
        or inventory.artifact.reviewer_key_policy_git_commit
        != reviewer_key_policy.registration_commit_sha
    ):
        raise ConceptAnnotationWorkflowError(
            "Published Concept inventory differs from frozen authority"
        )
    _reject_public_source_copy(
        tuple(
            value
            for concept in inventory.artifact.concepts
            for value in (
                concept.concept_key,
                concept.preferred_name,
                concept.short_definition,
                *concept.aliases,
                concept.review_rationale,
            )
        ),
        source_authority=source_authority,
    )
    for concept in inventory.artifact.concepts:
        for evidence in concept.evidence:
            _validate_public_evidence_span(
                evidence,
                source_authority=source_authority,
            )


def _validate_public_evidence_span(
    evidence: EvidenceSpan,
    *,
    source_authority: AnnotationEvidenceSourceAuthority,
) -> None:
    try:
        validate_public_evidence_span(
            evidence,
            source_authority=source_authority,
        )
    except AnnotationEvidenceError as exc:
        raise ConceptAnnotationWorkflowError(str(exc)) from None


def _build_alias_table(
    inventory: ConceptInventory,
    inventory_sha256: str,
) -> GoldAliasTable:
    entries: list[GoldAliasEntry] = []
    for concept in inventory.concepts:
        entries.append(
            GoldAliasEntry(
                concept_key=concept.concept_key,
                name_kind="preferred_name",
                display_text=concept.preferred_name,
                normalized_text=normalize_alias_key(concept.preferred_name),
            )
        )
        entries.extend(
            GoldAliasEntry(
                concept_key=concept.concept_key,
                name_kind="alias",
                display_text=alias,
                normalized_text=normalize_alias_key(alias),
            )
            for alias in concept.aliases
        )
    entries.sort(
        key=lambda entry: (
            entry.concept_key,
            0 if entry.name_kind == "preferred_name" else 1,
            entry.normalized_text,
            entry.display_text,
        )
    )
    return GoldAliasTable(
        schema_version=1,
        artifact_role="golden_graph_alias_table",
        concept_inventory_sha256=inventory_sha256,
        concept_count=inventory.concept_count,
        entry_count=len(entries),
        entries=tuple(entries),
    )


def _build_pair_manifest(
    inventory: ConceptInventory,
    *,
    concept_inventory_sha256: str,
    seal_sha256: str,
) -> RelationPairManifest:
    keys = tuple(concept.concept_key for concept in inventory.concepts)
    pairs = tuple(
        RelationPair(
            pair_id=relation_pair_id(left, right),
            left_concept_key=left,
            right_concept_key=right,
        )
        for left, right in combinations(keys, 2)
    )
    return RelationPairManifest(
        schema_version=1,
        artifact_role="golden_graph_relation_pair_manifest",
        status="complete_relation_pair_universe",
        concept_inventory_seal_sha256=seal_sha256,
        concept_inventory_sha256=concept_inventory_sha256,
        concept_count=inventory.concept_count,
        concept_keys=keys,
        pair_count=len(pairs),
        pairs=pairs,
    )


def _expected_seal_request(
    *,
    inventory: CanonicalArtifactAuthority[ConceptInventory],
    alias_table: CanonicalArtifactAuthority[GoldAliasTable],
    seal: ConceptInventorySeal,
) -> ConceptInventorySealRequest:
    return ConceptInventorySealRequest(
        schema_version=1,
        artifact_role="golden_graph_concept_inventory_seal_request",
        namespace=CONCEPT_ATTESTATION_NAMESPACE,
        protocol_id=inventory.artifact.protocol_id,
        frozen_protocol_sha256=inventory.artifact.frozen_protocol_sha256,
        semantic_source_catalog_sha256=(
            inventory.artifact.semantic_source_catalog_sha256
        ),
        chunk_manifest_sha256=inventory.artifact.chunk_manifest_sha256,
        concept_inventory_sha256=inventory.artifact_sha256,
        gold_alias_table_sha256=alias_table.artifact_sha256,
        concept_annotation_worksheet_sha256=(
            inventory.artifact.concept_annotation_worksheet_sha256
        ),
        reviewer_key_policy_sha256=(
            inventory.artifact.reviewer_key_policy_sha256
        ),
        reviewer_key_policy_git_commit=(
            inventory.artifact.reviewer_key_policy_git_commit
        ),
        reviewer_id=inventory.artifact.reviewer_id,
        reviewer_actor_kind_declaration="human",
        blind_to_system_proposals_declaration=True,
        software_authenticated_prediction_blindness=False,
        software_authenticated_reviewer_identity=False,
        concept_count=inventory.artifact.concept_count,
        excluded_candidate_count=seal.excluded_candidate_count,
        total_candidate_count=seal.total_candidate_count,
        approval_statement="key_control_approval_only_not_proof_of_humanity",
    )


def _require_prepared_consistent(prepared: PreparedConceptInventory) -> None:
    if (
        _model_sha(prepared.inventory) != prepared.inventory_sha256
        or _model_sha(prepared.alias_table) != prepared.alias_table_sha256
        or _model_sha(prepared.seal_request) != prepared.seal_request_sha256
        or prepared.alias_table
        != _build_alias_table(prepared.inventory, prepared.inventory_sha256)
    ):
        raise ConceptAnnotationWorkflowError(
            "Prepared Concept-inventory capability is internally inconsistent"
        )


def _require_signed_consistent(signed: SignedConceptInventory) -> None:
    _require_prepared_consistent(signed.prepared)
    if (
        _model_sha(signed.attestation_artifact)
        != signed.attestation_artifact_sha256
        or _model_sha(signed.seal) != signed.seal_sha256
        or _model_sha(signed.pair_manifest) != signed.pair_manifest_sha256
        or signed.pair_manifest
        != _build_pair_manifest(
            signed.prepared.inventory,
            concept_inventory_sha256=signed.prepared.inventory_sha256,
            seal_sha256=signed.seal_sha256,
        )
    ):
        raise ConceptAnnotationWorkflowError(
            "Signed Concept-inventory capability is internally inconsistent"
        )


def _reject_public_source_copy(
    public_values: tuple[str, ...],
    *,
    source_authority: AnnotationEvidenceSourceAuthority,
) -> None:
    try:
        reject_public_source_copy(
            public_values,
            source_authority=source_authority,
        )
    except AnnotationEvidenceError as exc:
        raise ConceptAnnotationWorkflowError(str(exc)) from None


def _reject_future_reviewer_attestation(value: str | None) -> None:
    if value is None:
        raise ConceptAnnotationWorkflowError(
            "A complete worksheet requires a reviewer attestation time"
        )
    try:
        attested = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:  # Model validation should already reject this.
        raise ConceptAnnotationWorkflowError(
            "Reviewer attestation time is invalid"
        ) from exc
    if attested > datetime.now(timezone.utc):
        raise ConceptAnnotationWorkflowError(
            "Reviewer attestation time cannot be in the future"
        )


def _issue_prepared(
    *,
    inventory: ConceptInventory,
    alias_table: GoldAliasTable,
    seal_request: ConceptInventorySealRequest,
    excluded_candidate_count: int,
    total_candidate_count: int,
) -> PreparedConceptInventory:
    prepared = object.__new__(PreparedConceptInventory)
    object.__setattr__(prepared, "inventory", inventory)
    object.__setattr__(prepared, "inventory_sha256", _model_sha(inventory))
    object.__setattr__(prepared, "alias_table", alias_table)
    object.__setattr__(prepared, "alias_table_sha256", _model_sha(alias_table))
    object.__setattr__(prepared, "seal_request", seal_request)
    object.__setattr__(prepared, "seal_request_sha256", _model_sha(seal_request))
    object.__setattr__(prepared, "excluded_candidate_count", excluded_candidate_count)
    object.__setattr__(prepared, "total_candidate_count", total_candidate_count)
    object.__setattr__(prepared, "_validation_token", _PREPARED_TOKEN)
    prepared.__post_init__()
    return prepared


def _issue_signed(
    *,
    prepared: PreparedConceptInventory,
    attestation_artifact: DetachedKeyAttestationArtifact,
    seal: ConceptInventorySeal,
    pair_manifest: RelationPairManifest,
) -> SignedConceptInventory:
    signed = object.__new__(SignedConceptInventory)
    object.__setattr__(signed, "prepared", prepared)
    object.__setattr__(signed, "attestation_artifact", attestation_artifact)
    object.__setattr__(
        signed,
        "attestation_artifact_sha256",
        _model_sha(attestation_artifact),
    )
    object.__setattr__(signed, "seal", seal)
    object.__setattr__(signed, "seal_sha256", _model_sha(seal))
    object.__setattr__(signed, "pair_manifest", pair_manifest)
    object.__setattr__(
        signed,
        "pair_manifest_sha256",
        _model_sha(pair_manifest),
    )
    object.__setattr__(signed, "_validation_token", _SIGNED_TOKEN)
    signed.__post_init__()
    return signed


def _issue_sealed_authority(
    **values: object,
) -> SealedConceptInventoryAuthority:
    authority = object.__new__(SealedConceptInventoryAuthority)
    for name, value in values.items():
        object.__setattr__(authority, name, value)
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
            raise ConceptAnnotationWorkflowError(
                "Concept worksheet contains a duplicate object key"
            )
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ConceptAnnotationWorkflowError(
        "Concept worksheet contains a non-finite number"
    )


__all__ = [
    "CONCEPT_ATTESTATION_NAMESPACE",
    "CONCEPT_REVIEWER_ATTESTATION",
    "ConceptAnnotationWorkflowError",
    "ConceptStagePaths",
    "PreparedConceptInventory",
    "SealedConceptInventoryAuthority",
    "SignedConceptInventory",
    "default_concept_stage_paths",
    "load_sealed_concept_inventory",
    "new_concept_annotation_worksheet",
    "parse_concept_annotation_worksheet",
    "prepare_concept_inventory",
    "publish_concept_inventory_stage",
    "signoff_prepared_concept_inventory",
]
