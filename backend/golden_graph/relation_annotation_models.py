"""Strict schemas for the delayed, prediction-blind Relation Pass A stage.

The mutable worksheet and the immutable Relation decisions remain inside the
gitignored annotation boundary until Relation Pass B has been sealed.  The
public request and seal expose only hashes and process declarations, never
labels, relation counts, endpoints, evidence, or rationale.  These models
validate shape and deterministic ordering; they do not prove that a reviewer
is human, blind to predictions, or semantically correct.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import heapq
from itertools import combinations
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.concept_graph import normalize_alias_display

from .annotation_models import (
    DetachedKeyAttestationReference,
    EvidenceSelectionDraft,
    EvidenceSpan,
    RELATION_PASS_A_ATTESTATION_NAMESPACE,
    StrictAnnotationModel,
    relation_pair_id,
)
from .annotation_evidence import evidence_span_sort_key
from .schemas import (
    JsonArrayTuple,
    SAFE_ID_PATTERN,
    SHA256_PATTERN,
    V1ConceptRelationType,
    V1RelationEvidenceSupportRole,
    V1RelationSupportBasis,
)


_LOGICAL_PAGE_ID_PATTERN = r"^page-[0-9]{4,5}$"
_UTC_TIMESTAMP_PATTERN = (
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-"
    r"(?:0[1-9]|[12][0-9]|3[01])T"
    r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$"
)
_GIT_COMMIT_OID_PATTERN = r"^[0-9a-f]{40}$"
_COMMITMENT_NONCE_PATTERN = r"^[0-9a-f]{64}$"

RELATION_PASS_A_REVIEWER_ATTESTATION = (
    "I personally completed Relation Pass A over the exhaustive frozen pair "
    "universe while blind to system proposals and approve its embargoed "
    "decision commitment for maintainer signing."
)
RELATION_PASS_A_RELEASE_POLICY = "after_relation_pass_b_seal"

_RELATION_TYPE_ORDER: dict[V1ConceptRelationType, int] = {
    "prerequisite": 0,
    "part_of": 1,
    "example_of": 2,
    "related": 3,
    "contrast_with": 4,
}
_SUPPORT_BASIS_ORDER: dict[V1RelationSupportBasis, int] = {
    "source_asserted": 0,
    "pedagogical_inference": 1,
}
_SUPPORT_ROLE_ORDER: dict[V1RelationEvidenceSupportRole, int] = {
    "relation_assertion": 0,
    "source_endpoint": 1,
    "target_endpoint": 2,
}
_SYMMETRIC_RELATION_TYPES = frozenset({"related", "contrast_with"})

RelationPassAOutcome = Literal["pending", "none", "relations"]
CompletedRelationOutcome = Literal["none", "relations"]


class RelationWorksheetConcept(StrictAnnotationModel):
    """Public Concept display copied into the private human worksheet."""

    concept_key: str = Field(pattern=SAFE_ID_PATTERN)
    preferred_name: str = Field(min_length=1, max_length=200)

    @field_validator("preferred_name")
    @classmethod
    def canonical_name(cls, value: str) -> str:
        return _require_canonical_text(value, label="Concept preferred name")


class RelationEvidenceSelectionDraft(StrictAnnotationModel):
    """One private exact-quote selection with its independent support role."""

    support_role: V1RelationEvidenceSupportRole
    selection: EvidenceSelectionDraft


class RelationJudgmentDraft(StrictAnnotationModel):
    """One private typed/directed positive judgment for an unordered pair."""

    relation_type: V1ConceptRelationType
    source_concept_key: str = Field(pattern=SAFE_ID_PATTERN)
    target_concept_key: str = Field(pattern=SAFE_ID_PATTERN)
    support_basis: V1RelationSupportBasis
    proposal_origin_declaration: Literal["human"]
    evidence: JsonArrayTuple[RelationEvidenceSelectionDraft] = Field(
        min_length=1,
        max_length=32,
    )
    review_rationale: str = Field(min_length=1, max_length=2_000)

    @field_validator("review_rationale")
    @classmethod
    def canonical_rationale(cls, value: str) -> str:
        return _require_canonical_text(value, label="Relation rationale")

    @field_validator("evidence")
    @classmethod
    def canonical_evidence(
        cls,
        values: tuple[RelationEvidenceSelectionDraft, ...],
    ) -> tuple[RelationEvidenceSelectionDraft, ...]:
        identities = tuple(_draft_evidence_sort_key(item) for item in values)
        if identities != tuple(sorted(identities)):
            raise ValueError("Relation draft evidence must be canonically sorted")
        if len(identities) != len(set(identities)):
            raise ValueError("Relation draft evidence must be unique")
        return values

    @model_validator(mode="after")
    def valid_direction_and_support(self) -> "RelationJudgmentDraft":
        if self.source_concept_key == self.target_concept_key:
            raise ValueError("Relation judgments cannot contain self-loops")
        if (
            self.relation_type in _SYMMETRIC_RELATION_TYPES
            and self.source_concept_key > self.target_concept_key
        ):
            raise ValueError(
                "Symmetric Relation endpoints must use canonical ordering"
            )
        _validate_support_contract(
            self.support_basis,
            tuple(item.support_role for item in self.evidence),
        )
        return self


class RelationPairDecisionDraft(StrictAnnotationModel):
    """One pending, negative, or positive decision in the private worksheet."""

    pair_id: str = Field(pattern=SHA256_PATTERN)
    left_concept_key: str = Field(pattern=SAFE_ID_PATTERN)
    left_preferred_name: str = Field(min_length=1, max_length=200)
    right_concept_key: str = Field(pattern=SAFE_ID_PATTERN)
    right_preferred_name: str = Field(min_length=1, max_length=200)
    outcome: RelationPassAOutcome
    none_rationale: str | None = Field(default=None, min_length=1, max_length=2_000)
    relations: JsonArrayTuple[RelationJudgmentDraft] = Field(
        default=(),
        max_length=5,
    )

    @field_validator("left_preferred_name", "right_preferred_name")
    @classmethod
    def canonical_endpoint_name(cls, value: str) -> str:
        return _require_canonical_text(value, label="Relation endpoint name")

    @field_validator("none_rationale")
    @classmethod
    def canonical_none_rationale(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_canonical_text(value, label="Negative-pair rationale")

    @field_validator("relations")
    @classmethod
    def canonical_relations(
        cls,
        values: tuple[RelationJudgmentDraft, ...],
    ) -> tuple[RelationJudgmentDraft, ...]:
        identities = tuple(_relation_identity(item) for item in values)
        if identities != tuple(sorted(identities)):
            raise ValueError("Relation judgments must be canonically sorted")
        if len(identities) != len(set(identities)):
            raise ValueError("A pair cannot repeat a normalized Relation key")
        relation_types = tuple(item.relation_type for item in values)
        if len(relation_types) != len(set(relation_types)):
            raise ValueError("A pair can contain at most one Relation of each type")
        if "related" in {item.relation_type for item in values} and len(values) > 1:
            raise ValueError(
                "Generic related cannot coexist with a more specific Relation"
            )
        return values

    @model_validator(mode="after")
    def valid_pair_outcome(self) -> "RelationPairDecisionDraft":
        _validate_pair_identity(
            self.pair_id,
            self.left_concept_key,
            self.right_concept_key,
        )
        endpoints = {self.left_concept_key, self.right_concept_key}
        if any(
            {item.source_concept_key, item.target_concept_key} != endpoints
            for item in self.relations
        ):
            raise ValueError("Every Relation must use the decision pair endpoints")
        if self.outcome == "pending":
            if self.none_rationale is not None or self.relations:
                raise ValueError("A pending pair cannot contain a decision")
        elif self.outcome == "none":
            if self.none_rationale is None or self.relations:
                raise ValueError("A none decision needs rationale and no Relations")
        elif self.none_rationale is not None or not self.relations:
            raise ValueError(
                "A relations decision needs Relations and no none rationale"
            )
        return self


class RelationPassAWorksheet(StrictAnnotationModel):
    """Mutable private Pass A worksheet over one exact complete pair universe."""

    schema_version: Literal[1]
    artifact_role: Literal["golden_graph_relation_pass_a_worksheet"]
    worksheet_status: Literal["draft", "complete"]
    worksheet_id: str = Field(pattern=SAFE_ID_PATTERN)
    commitment_nonce_hex: str = Field(
        pattern=_COMMITMENT_NONCE_PATTERN,
        repr=False,
    )
    protocol_id: str = Field(pattern=SAFE_ID_PATTERN)
    frozen_protocol_sha256: str = Field(pattern=SHA256_PATTERN)
    semantic_source_catalog_sha256: str = Field(pattern=SHA256_PATTERN)
    chunk_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    private_materialization_sha256: str = Field(pattern=SHA256_PATTERN)
    annotation_guide_sha256: str = Field(pattern=SHA256_PATTERN)
    concept_inventory_sha256: str = Field(pattern=SHA256_PATTERN)
    concept_inventory_seal_sha256: str = Field(pattern=SHA256_PATTERN)
    relation_pair_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewer_key_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewer_key_policy_git_commit: str = Field(pattern=_GIT_COMMIT_OID_PATTERN)
    reviewer_id: str = Field(pattern=SAFE_ID_PATTERN)
    pass_role: Literal["A"]
    blind_to_system_proposals_declaration: Literal[True]
    software_authenticated_prediction_blindness: Literal[False]
    software_authenticated_reviewer_identity: Literal[False]
    reviewer_actor_kind_declaration: Literal["human"] | None
    reviewer_attestation_statement: str | None = Field(
        default=None,
        min_length=1,
        max_length=1_000,
    )
    reviewer_attested_at_utc: str | None = Field(
        default=None,
        pattern=_UTC_TIMESTAMP_PATTERN,
    )
    minimum_delay_hours_before_pass_b: Literal[72]
    label_release_policy: Literal["after_relation_pass_b_seal"]
    concept_count: int = Field(ge=12, le=20)
    concepts: JsonArrayTuple[RelationWorksheetConcept] = Field(
        min_length=12,
        max_length=20,
    )
    pair_count: int = Field(ge=66, le=190)
    pair_decisions: JsonArrayTuple[RelationPairDecisionDraft] = Field(
        min_length=66,
        max_length=190,
    )

    @field_validator("reviewer_attestation_statement")
    @classmethod
    def canonical_attestation(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_canonical_text(value, label="Reviewer attestation")

    @field_validator("reviewer_attested_at_utc")
    @classmethod
    def real_utc_timestamp(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_utc_timestamp(value)

    @model_validator(mode="after")
    def exact_closed_world_packet(self) -> "RelationPassAWorksheet":
        concept_keys = tuple(item.concept_key for item in self.concepts)
        if concept_keys != tuple(sorted(concept_keys)):
            raise ValueError("Worksheet Concepts must be canonically sorted")
        if len(concept_keys) != len(set(concept_keys)):
            raise ValueError("Worksheet Concept keys must be unique")
        if self.concept_count != len(concept_keys):
            raise ValueError("concept_count must equal worksheet Concept count")
        _validate_complete_pair_sequence(
            concept_keys,
            self.pair_count,
            self.pair_decisions,
        )
        name_by_key = {
            item.concept_key: item.preferred_name for item in self.concepts
        }
        if any(
            decision.left_preferred_name
            != name_by_key[decision.left_concept_key]
            or decision.right_preferred_name
            != name_by_key[decision.right_concept_key]
            for decision in self.pair_decisions
        ):
            raise ValueError("Pair endpoint names must match worksheet Concepts")

        declarations = (
            self.reviewer_actor_kind_declaration,
            self.reviewer_attestation_statement,
            self.reviewer_attested_at_utc,
        )
        if self.worksheet_status == "complete":
            if any(item.outcome == "pending" for item in self.pair_decisions):
                raise ValueError("A complete Pass A worksheet has no pending pairs")
            if (
                self.reviewer_actor_kind_declaration != "human"
                or self.reviewer_attestation_statement is None
                or self.reviewer_attested_at_utc is None
            ):
                raise ValueError(
                    "A complete Pass A worksheet needs reviewer declarations"
                )
        elif any(value is not None for value in declarations):
            raise ValueError("A draft worksheet cannot contain final attestation")
        return self


class RelationEvidenceSpan(StrictAnnotationModel):
    """One redacted, locatable Relation evidence span and support role."""

    support_role: V1RelationEvidenceSupportRole
    span: EvidenceSpan


class RelationJudgment(StrictAnnotationModel):
    """One redacted positive Relation judgment inside the embargoed artifact."""

    relation_type: V1ConceptRelationType
    source_concept_key: str = Field(pattern=SAFE_ID_PATTERN)
    target_concept_key: str = Field(pattern=SAFE_ID_PATTERN)
    support_basis: V1RelationSupportBasis
    proposal_origin_declaration: Literal["human"]
    evidence: JsonArrayTuple[RelationEvidenceSpan] = Field(
        min_length=1,
        max_length=32,
    )
    review_rationale: str = Field(min_length=1, max_length=2_000)

    @field_validator("review_rationale")
    @classmethod
    def canonical_rationale(cls, value: str) -> str:
        return _require_canonical_text(value, label="Relation rationale")

    @field_validator("evidence")
    @classmethod
    def canonical_evidence(
        cls,
        values: tuple[RelationEvidenceSpan, ...],
    ) -> tuple[RelationEvidenceSpan, ...]:
        identities = tuple(_resolved_evidence_sort_key(item) for item in values)
        if identities != tuple(sorted(identities)):
            raise ValueError("Resolved Relation evidence must be canonically sorted")
        if len(identities) != len(set(identities)):
            raise ValueError("Resolved Relation evidence must be unique")
        return values

    @model_validator(mode="after")
    def valid_direction_and_support(self) -> "RelationJudgment":
        if self.source_concept_key == self.target_concept_key:
            raise ValueError("Relation judgments cannot contain self-loops")
        if (
            self.relation_type in _SYMMETRIC_RELATION_TYPES
            and self.source_concept_key > self.target_concept_key
        ):
            raise ValueError(
                "Symmetric Relation endpoints must use canonical ordering"
            )
        _validate_support_contract(
            self.support_basis,
            tuple(item.support_role for item in self.evidence),
        )
        return self


class RelationPairDecision(StrictAnnotationModel):
    """One completed, redacted pair decision; negatives remain evaluation labels."""

    pair_id: str = Field(pattern=SHA256_PATTERN)
    left_concept_key: str = Field(pattern=SAFE_ID_PATTERN)
    right_concept_key: str = Field(pattern=SAFE_ID_PATTERN)
    outcome: CompletedRelationOutcome
    none_rationale: str | None = Field(default=None, min_length=1, max_length=2_000)
    relations: JsonArrayTuple[RelationJudgment] = Field(default=(), max_length=5)

    @field_validator("none_rationale")
    @classmethod
    def canonical_none_rationale(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_canonical_text(value, label="Negative-pair rationale")

    @field_validator("relations")
    @classmethod
    def canonical_relations(
        cls,
        values: tuple[RelationJudgment, ...],
    ) -> tuple[RelationJudgment, ...]:
        identities = tuple(_relation_identity(item) for item in values)
        if identities != tuple(sorted(identities)):
            raise ValueError("Relation judgments must be canonically sorted")
        if len(identities) != len(set(identities)):
            raise ValueError("A pair cannot repeat a normalized Relation key")
        relation_types = tuple(item.relation_type for item in values)
        if len(relation_types) != len(set(relation_types)):
            raise ValueError("A pair can contain at most one Relation of each type")
        if "related" in {item.relation_type for item in values} and len(values) > 1:
            raise ValueError(
                "Generic related cannot coexist with a more specific Relation"
            )
        return values

    @model_validator(mode="after")
    def valid_pair_outcome(self) -> "RelationPairDecision":
        _validate_pair_identity(
            self.pair_id,
            self.left_concept_key,
            self.right_concept_key,
        )
        endpoints = {self.left_concept_key, self.right_concept_key}
        if any(
            {item.source_concept_key, item.target_concept_key} != endpoints
            for item in self.relations
        ):
            raise ValueError("Every Relation must use the decision pair endpoints")
        if self.outcome == "none":
            if self.none_rationale is None or self.relations:
                raise ValueError("A none decision needs rationale and no Relations")
        elif self.none_rationale is not None or not self.relations:
            raise ValueError(
                "A relations decision needs Relations and no none rationale"
            )
        return self


class RelationPassAArtifact(StrictAnnotationModel):
    """Immutable, redacted Pass A labels kept private until Pass B is sealed."""

    schema_version: Literal[1]
    artifact_role: Literal["golden_graph_relation_pass_a_artifact"]
    status: Literal["complete_relation_pass_a_not_adjudicated"]
    protocol_id: str = Field(pattern=SAFE_ID_PATTERN)
    frozen_protocol_sha256: str = Field(pattern=SHA256_PATTERN)
    semantic_source_catalog_sha256: str = Field(pattern=SHA256_PATTERN)
    chunk_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    annotation_guide_sha256: str = Field(pattern=SHA256_PATTERN)
    concept_inventory_sha256: str = Field(pattern=SHA256_PATTERN)
    concept_inventory_seal_sha256: str = Field(pattern=SHA256_PATTERN)
    relation_pair_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    relation_pass_a_worksheet_sha256: str = Field(pattern=SHA256_PATTERN)
    commitment_nonce_hex: str = Field(
        pattern=_COMMITMENT_NONCE_PATTERN,
        repr=False,
    )
    reviewer_key_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewer_key_policy_git_commit: str = Field(pattern=_GIT_COMMIT_OID_PATTERN)
    reviewer_id: str = Field(pattern=SAFE_ID_PATTERN)
    reviewer_actor_kind_declaration: Literal["human"]
    pass_role: Literal["A"]
    blind_to_system_proposals_declaration: Literal[True]
    software_authenticated_prediction_blindness: Literal[False]
    software_authenticated_reviewer_identity: Literal[False]
    reviewer_attestation_statement: Literal[
        "I personally completed Relation Pass A over the exhaustive frozen pair "
        "universe while blind to system proposals and approve its embargoed "
        "decision commitment for maintainer signing."
    ]
    reviewer_attested_at_utc: str = Field(pattern=_UTC_TIMESTAMP_PATTERN)
    minimum_delay_hours_before_pass_b: Literal[72]
    label_release_policy: Literal["after_relation_pass_b_seal"]
    concept_count: int = Field(ge=12, le=20)
    concept_keys: JsonArrayTuple[str] = Field(min_length=12, max_length=20)
    pair_count: int = Field(ge=66, le=190)
    none_pair_count: int = Field(ge=0, le=190)
    positive_pair_count: int = Field(ge=0, le=190)
    relation_count: int = Field(ge=0, le=950)
    pair_decisions: JsonArrayTuple[RelationPairDecision] = Field(
        min_length=66,
        max_length=190,
    )

    @field_validator("reviewer_attested_at_utc")
    @classmethod
    def real_utc_timestamp(cls, value: str) -> str:
        return _validate_utc_timestamp(value)

    @model_validator(mode="after")
    def exact_closed_world_decisions(self) -> "RelationPassAArtifact":
        keys = self.concept_keys
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("Pass A Concept keys must be sorted and unique")
        if self.concept_count != len(keys):
            raise ValueError("concept_count must equal Pass A Concept-key count")
        _validate_complete_pair_sequence(keys, self.pair_count, self.pair_decisions)
        none_count = sum(item.outcome == "none" for item in self.pair_decisions)
        positive_count = len(self.pair_decisions) - none_count
        relation_count = sum(
            len(item.relations) for item in self.pair_decisions
        )
        if (
            self.none_pair_count != none_count
            or self.positive_pair_count != positive_count
            or self.relation_count != relation_count
        ):
            raise ValueError("Pass A summary counts must be exactly derived")
        _reject_prerequisite_cycle(keys, self.pair_decisions)
        return self


class RelationPassASealRequest(StrictAnnotationModel):
    """Public neutral commitment signed while Pass A labels remain private."""

    schema_version: Literal[1]
    artifact_role: Literal["golden_graph_relation_pass_a_seal_request"]
    namespace: Literal["video-course-cards-g2-relation-pass-a-v1"]
    protocol_id: str = Field(pattern=SAFE_ID_PATTERN)
    frozen_protocol_sha256: str = Field(pattern=SHA256_PATTERN)
    semantic_source_catalog_sha256: str = Field(pattern=SHA256_PATTERN)
    chunk_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    concept_inventory_sha256: str = Field(pattern=SHA256_PATTERN)
    concept_inventory_seal_sha256: str = Field(pattern=SHA256_PATTERN)
    relation_pair_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    relation_pass_a_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    relation_pass_a_worksheet_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewer_key_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewer_key_policy_git_commit: str = Field(pattern=_GIT_COMMIT_OID_PATTERN)
    reviewer_id: str = Field(pattern=SAFE_ID_PATTERN)
    reviewer_actor_kind_declaration: Literal["human"]
    pass_role: Literal["A"]
    blind_to_system_proposals_declaration: Literal[True]
    software_authenticated_prediction_blindness: Literal[False]
    software_authenticated_reviewer_identity: Literal[False]
    reviewer_attested_at_utc: str = Field(pattern=_UTC_TIMESTAMP_PATTERN)
    minimum_delay_hours_before_pass_b: Literal[72]
    software_authenticated_minimum_delay: Literal[False]
    pair_count: int = Field(ge=66, le=190)
    labels_embargoed_at_commitment: Literal[True]
    label_release_policy: Literal["after_relation_pass_b_seal"]
    approval_statement: Literal[
        "key_control_approval_only_not_proof_of_humanity_or_blindness"
    ]

    @field_validator("reviewer_attested_at_utc")
    @classmethod
    def real_utc_timestamp(cls, value: str) -> str:
        return _validate_utc_timestamp(value)


class RelationPassASeal(StrictAnnotationModel):
    """Public Pass A root; a commitment only, never Relation gold."""

    schema_version: Literal[1]
    artifact_role: Literal["golden_graph_relation_pass_a_seal"]
    status: Literal["relation_pass_a_commitment_only_not_gold_bundle"]
    protocol_id: str = Field(pattern=SAFE_ID_PATTERN)
    frozen_protocol_sha256: str = Field(pattern=SHA256_PATTERN)
    concept_inventory_sha256: str = Field(pattern=SHA256_PATTERN)
    concept_inventory_seal_sha256: str = Field(pattern=SHA256_PATTERN)
    relation_pair_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    relation_pass_a_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    relation_pass_a_seal_request_sha256: str = Field(pattern=SHA256_PATTERN)
    detached_attestation_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewer_key_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewer_key_policy_git_commit: str = Field(pattern=_GIT_COMMIT_OID_PATTERN)
    reviewer_id: str = Field(pattern=SAFE_ID_PATTERN)
    reviewer_actor_kind_declaration: Literal["human"]
    pass_role: Literal["A"]
    blind_to_system_proposals_declaration: Literal[True]
    software_authenticated_prediction_blindness: Literal[False]
    software_authenticated_reviewer_identity: Literal[False]
    reviewer_attested_at_utc: str = Field(pattern=_UTC_TIMESTAMP_PATTERN)
    minimum_delay_hours_before_pass_b: Literal[72]
    software_authenticated_minimum_delay: Literal[False]
    pair_count: int = Field(ge=66, le=190)
    labels_embargoed_at_commitment: Literal[True]
    labels_unreleased_at_commitment: Literal[True]
    label_release_policy: Literal["after_relation_pass_b_seal"]
    detached_attestation: DetachedKeyAttestationReference

    @field_validator("reviewer_attested_at_utc")
    @classmethod
    def real_utc_timestamp(cls, value: str) -> str:
        return _validate_utc_timestamp(value)

    @model_validator(mode="after")
    def exact_pass_a_attestation(self) -> "RelationPassASeal":
        if self.detached_attestation.signer_identity != self.reviewer_id:
            raise ValueError("Attested signer identity must match reviewer_id")
        if (
            self.detached_attestation.namespace
            != RELATION_PASS_A_ATTESTATION_NAMESPACE
        ):
            raise ValueError("Detached attestation namespace is invalid")
        if (
            self.detached_attestation.signed_payload_sha256
            != self.relation_pass_a_seal_request_sha256
        ):
            raise ValueError("Detached attestation must bind the Pass A request")
        return self


def relation_sort_key(
    relation: RelationJudgmentDraft | RelationJudgment,
) -> tuple[int, str, str]:
    """Return the canonical identity order for one normalized Relation."""

    return _relation_identity(relation)


def relation_evidence_sort_key(
    evidence: RelationEvidenceSelectionDraft | RelationEvidenceSpan,
) -> tuple[object, ...]:
    """Return the canonical role-plus-span order for Relation evidence."""

    if isinstance(evidence, RelationEvidenceSelectionDraft):
        return _draft_evidence_sort_key(evidence)
    return _resolved_evidence_sort_key(evidence)


def _require_canonical_text(value: str, *, label: str) -> str:
    normalized = normalize_alias_display(value)
    if not normalized or normalized != value:
        raise ValueError(f"{label} must use canonical NFKC/whitespace display")
    return value


def _validate_utc_timestamp(value: str) -> str:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ValueError("Relation timestamp must be a real UTC time") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ValueError("Relation timestamp must be canonical UTC")
    return value


def _validate_pair_identity(pair_id: str, left: str, right: str) -> None:
    if left >= right:
        raise ValueError("Relation pair endpoints must be strictly sorted")
    if pair_id != relation_pair_id(left, right):
        raise ValueError("Relation pair id does not match its endpoints")


def _validate_complete_pair_sequence(
    concept_keys: tuple[str, ...],
    pair_count: int,
    decisions: tuple[object, ...],
) -> None:
    expected = tuple(
        (relation_pair_id(left, right), left, right)
        for left, right in combinations(concept_keys, 2)
    )
    actual = tuple(
        (
            getattr(item, "pair_id"),
            getattr(item, "left_concept_key"),
            getattr(item, "right_concept_key"),
        )
        for item in decisions
    )
    if pair_count != len(expected) or actual != expected:
        raise ValueError(
            "Relation decisions must equal the canonical complete pair universe"
        )


def _draft_evidence_sort_key(
    evidence: RelationEvidenceSelectionDraft,
) -> tuple[object, ...]:
    selection = evidence.selection
    quote_sha256 = hashlib.sha256(
        selection.exact_quote.encode("utf-8")
    ).hexdigest()
    return (
        _SUPPORT_ROLE_ORDER[evidence.support_role],
        selection.chunk_ordinal,
        selection.logical_page_id,
        -1
        if selection.page_global_utf8_start is None
        else selection.page_global_utf8_start,
        selection.semantic_chunk_sha256,
        quote_sha256,
    )


def _resolved_evidence_sort_key(
    evidence: RelationEvidenceSpan,
) -> tuple[object, ...]:
    return (
        _SUPPORT_ROLE_ORDER[evidence.support_role],
        *evidence_span_sort_key(evidence.span),
    )


def _relation_identity(
    relation: RelationJudgmentDraft | RelationJudgment,
) -> tuple[int, str, str]:
    return (
        _RELATION_TYPE_ORDER[relation.relation_type],
        relation.source_concept_key,
        relation.target_concept_key,
    )


def _validate_support_contract(
    support_basis: V1RelationSupportBasis,
    support_roles: tuple[V1RelationEvidenceSupportRole, ...],
) -> None:
    role_set = set(support_roles)
    if support_basis == "source_asserted":
        if role_set != {"relation_assertion"}:
            raise ValueError(
                "source_asserted accepts only relation_assertion evidence"
            )
        return
    if role_set != {"source_endpoint", "target_endpoint"}:
        raise ValueError(
            "pedagogical_inference accepts only evidence for both endpoints"
        )


def _reject_prerequisite_cycle(
    concept_keys: tuple[str, ...],
    decisions: tuple[RelationPairDecision, ...],
) -> None:
    adjacency = {key: set() for key in concept_keys}
    indegree = {key: 0 for key in concept_keys}
    for decision in decisions:
        for relation in decision.relations:
            if relation.relation_type != "prerequisite":
                continue
            source = relation.source_concept_key
            target = relation.target_concept_key
            if target not in adjacency[source]:
                adjacency[source].add(target)
                indegree[target] += 1

    ready = [key for key in concept_keys if indegree[key] == 0]
    heapq.heapify(ready)
    visited = 0
    while ready:
        source = heapq.heappop(ready)
        visited += 1
        for target in sorted(adjacency[source]):
            indegree[target] -= 1
            if indegree[target] == 0:
                heapq.heappush(ready, target)
    if visited != len(concept_keys):
        raise ValueError("Pass A prerequisite judgments must form a DAG")


__all__ = [
    "RELATION_PASS_A_RELEASE_POLICY",
    "RELATION_PASS_A_REVIEWER_ATTESTATION",
    "RelationEvidenceSelectionDraft",
    "RelationEvidenceSpan",
    "RelationJudgment",
    "RelationJudgmentDraft",
    "RelationPairDecision",
    "RelationPairDecisionDraft",
    "RelationPassAArtifact",
    "RelationPassAOutcome",
    "RelationPassASeal",
    "RelationPassASealRequest",
    "RelationPassAWorksheet",
    "RelationWorksheetConcept",
    "relation_evidence_sort_key",
    "relation_sort_key",
]
