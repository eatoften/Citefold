"""Strict, immutable schemas for the G2.1 Concept annotation artifacts.

These models validate artifact shape and deterministic ordering only.  They do
not perform Source resolution, artifact I/O, signature verification, or issue
an authority that a reviewer is human.  ``proposal_origin_declaration`` and
``reviewer_actor_kind_declaration`` preserve the maintainer's declarations;
the detached signature reference proves key control only when a separate
verifier has checked it.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from itertools import combinations
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.concept_graph import normalize_alias_display, normalize_alias_key

from .schemas import JsonArrayTuple, SAFE_ID_PATTERN, SHA256_PATTERN


_LOGICAL_PAGE_ID_PATTERN = r"^page-[0-9]{4,5}$"
_UTC_TIMESTAMP_PATTERN = (
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-"
    r"(?:0[1-9]|[12][0-9]|3[01])T"
    r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$"
)
_SSH_SHA256_FINGERPRINT_PATTERN = r"^SHA256:[A-Za-z0-9+/]{43}=?$"
_SSH_ED25519_ALLOWED_SIGNER_PATTERN = (
    r"^[A-Za-z0-9][A-Za-z0-9._@+:-]{0,254} "
    r"ssh-ed25519 [A-Za-z0-9+/]+={0,2}\n$"
)
_SSH_SIGNATURE_PATTERN = (
    r"^-----BEGIN SSH SIGNATURE-----\n"
    r"(?:[A-Za-z0-9+/=]+\n)+"
    r"-----END SSH SIGNATURE-----\n?$"
)
_GIT_COMMIT_OID_PATTERN = r"^[0-9a-f]{40}$"
_RELATION_PAIR_DOMAIN = b"video-course-cards-g2-relation-pair-v1\x00"

CONCEPT_ATTESTATION_NAMESPACE = "video-course-cards-g2-concepts-v1"
GOLD_BUNDLE_ATTESTATION_NAMESPACE = "video-course-cards-g2-gold-bundle-v1"
RELATION_PASS_A_ATTESTATION_NAMESPACE = (
    "video-course-cards-g2-relation-pass-a-v1"
)
RELATION_PASS_B_ATTESTATION_NAMESPACE = (
    "video-course-cards-g2-relation-pass-b-v1"
)
G2AttestationNamespace = Literal[
    "video-course-cards-g2-concepts-v1",
    "video-course-cards-g2-gold-bundle-v1",
    "video-course-cards-g2-relation-pass-a-v1",
    "video-course-cards-g2-relation-pass-b-v1",
]
# ReviewerKeyPolicy requires a sorted, duplicate-free namespace sequence.
G2_ATTESTATION_NAMESPACES: tuple[G2AttestationNamespace, ...] = (
    CONCEPT_ATTESTATION_NAMESPACE,
    GOLD_BUNDLE_ATTESTATION_NAMESPACE,
    RELATION_PASS_A_ATTESTATION_NAMESPACE,
    RELATION_PASS_B_ATTESTATION_NAMESPACE,
)

ConceptDecision = Literal["include", "exclude"]
ConceptWorksheetStatus = Literal["draft", "complete"]


class StrictAnnotationModel(BaseModel):
    """Shared fail-closed Pydantic configuration for G2 artifacts."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        hide_input_in_errors=True,
    )


class EvidenceSelectionDraft(StrictAnnotationModel):
    """Private human selection resolved against frozen Source bytes later.

    ``page_global_utf8_start`` disambiguates repeated exact text on a page.  It
    is optional while a worksheet is being authored.  ``exact_quote`` remains
    private and is deliberately hidden from model/error representations.
    """

    chunk_ordinal: int = Field(ge=0, le=999)
    logical_page_id: str = Field(pattern=_LOGICAL_PAGE_ID_PATTERN)
    semantic_chunk_sha256: str = Field(pattern=SHA256_PATTERN)
    page_global_utf8_start: int | None = Field(default=None, ge=0)
    exact_quote: str = Field(min_length=1, max_length=16_000, repr=False)

    @field_validator("exact_quote")
    @classmethod
    def exact_quote_contains_semantic_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("exact_quote must contain non-whitespace text")
        return value


class ConceptDecisionDraft(StrictAnnotationModel):
    """One private, prediction-blind Concept inclusion or exclusion decision."""

    candidate_key: str = Field(pattern=SAFE_ID_PATTERN)
    candidate_label: str = Field(min_length=1, max_length=200)
    decision: ConceptDecision
    preferred_name: str | None = Field(default=None, min_length=1, max_length=200)
    short_definition: str | None = Field(
        default=None,
        min_length=1,
        max_length=4_000,
    )
    aliases: JsonArrayTuple[str] = Field(default=(), max_length=32)
    evidence: JsonArrayTuple[EvidenceSelectionDraft] = Field(
        default=(),
        max_length=32,
    )
    decision_rationale: str = Field(min_length=1, max_length=4_000)

    @field_validator("candidate_label")
    @classmethod
    def canonical_candidate_label(cls, value: str) -> str:
        return _require_canonical_display(value, label="candidate_label")

    @field_validator("preferred_name")
    @classmethod
    def canonical_optional_preferred_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_canonical_display(value, label="preferred_name")

    @field_validator("short_definition", "decision_rationale")
    @classmethod
    def canonical_explanatory_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_canonical_display(value, label="Concept explanation")

    @field_validator("aliases")
    @classmethod
    def canonical_aliases(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_alias_sequence(values)

    @field_validator("evidence")
    @classmethod
    def canonical_draft_evidence(
        cls,
        values: tuple[EvidenceSelectionDraft, ...],
    ) -> tuple[EvidenceSelectionDraft, ...]:
        identities = tuple(_draft_evidence_sort_key(item) for item in values)
        if identities != tuple(sorted(identities)):
            raise ValueError("Concept draft evidence must be canonically sorted")
        if len(identities) != len(set(identities)):
            raise ValueError("Concept draft evidence must be unique")
        return values

    @model_validator(mode="after")
    def conditional_decision_fields(self) -> "ConceptDecisionDraft":
        if self.decision == "include":
            if self.preferred_name is None or self.short_definition is None:
                raise ValueError(
                    "Included candidates require a preferred name and definition"
                )
            if not self.evidence:
                raise ValueError("Included candidates require Source evidence")
            preferred_key = normalize_alias_key(self.preferred_name)
            if preferred_key in {normalize_alias_key(alias) for alias in self.aliases}:
                raise ValueError(
                    "An alias cannot duplicate the included preferred name"
                )
            return self

        if (
            self.preferred_name is not None
            or self.short_definition is not None
            or self.aliases
            or self.evidence
        ):
            raise ValueError(
                "Excluded candidates cannot carry included-Concept fields"
            )
        return self


class ConceptAnnotationWorksheet(StrictAnnotationModel):
    """Private mutable authoring content represented by a strict value model.

    A valid ``complete`` worksheet is still only a self-declared submission.
    It is not a seal and this model deliberately exposes no authority factory.
    """

    schema_version: Literal[1]
    artifact_role: Literal["golden_graph_concept_annotation_worksheet"]
    worksheet_status: ConceptWorksheetStatus
    worksheet_id: str = Field(pattern=SAFE_ID_PATTERN)
    protocol_id: str = Field(pattern=SAFE_ID_PATTERN)
    frozen_protocol_sha256: str = Field(pattern=SHA256_PATTERN)
    semantic_source_catalog_sha256: str = Field(pattern=SHA256_PATTERN)
    chunk_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    private_materialization_sha256: str = Field(pattern=SHA256_PATTERN)
    annotation_guide_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewer_key_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewer_key_policy_git_commit: str = Field(
        pattern=_GIT_COMMIT_OID_PATTERN,
    )
    reviewer_id: str = Field(pattern=SAFE_ID_PATTERN)
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
    candidates: JsonArrayTuple[ConceptDecisionDraft] = Field(max_length=100)

    @field_validator("reviewer_attestation_statement")
    @classmethod
    def canonical_attestation_statement(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_canonical_display(value, label="reviewer attestation")

    @field_validator("reviewer_attested_at_utc")
    @classmethod
    def real_utc_timestamp(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError as exc:
            raise ValueError(
                "reviewer_attested_at_utc must be a real UTC time"
            ) from exc
        if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
            raise ValueError("reviewer_attested_at_utc must be canonical UTC")
        return value

    @model_validator(mode="after")
    def validate_worksheet(self) -> "ConceptAnnotationWorksheet":
        keys = tuple(item.candidate_key for item in self.candidates)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("Candidate keys must be sorted and unique")

        included = tuple(
            item for item in self.candidates if item.decision == "include"
        )
        _validate_global_names(included)

        if self.worksheet_status == "complete":
            if not 12 <= len(included) <= 20:
                raise ValueError(
                    "A complete worksheet requires 12..20 included Concepts"
                )
            if (
                self.reviewer_actor_kind_declaration != "human"
                or self.reviewer_attestation_statement is None
                or self.reviewer_attested_at_utc is None
            ):
                raise ValueError(
                    "A complete worksheet requires all reviewer declarations"
                )
        elif any(
            value is not None
            for value in (
                self.reviewer_actor_kind_declaration,
                self.reviewer_attestation_statement,
                self.reviewer_attested_at_utc,
            )
        ):
            raise ValueError(
                "A draft worksheet cannot contain a partial/final attestation"
            )
        return self


class EvidenceSpan(StrictAnnotationModel):
    """Resolved, redacted, logical evidence coordinates safe to publish."""

    chunk_ordinal: int = Field(ge=0, le=999)
    logical_page_id: str = Field(pattern=_LOGICAL_PAGE_ID_PATTERN)
    semantic_chunk_sha256: str = Field(pattern=SHA256_PATTERN)
    page_utf8_start: int = Field(ge=0)
    page_utf8_end: int = Field(gt=0)
    offset_unit: Literal["utf8_bytes"]
    semantic_span_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def ordered_page_offsets(self) -> "EvidenceSpan":
        if self.page_utf8_end <= self.page_utf8_start:
            raise ValueError("Evidence end must exceed its start")
        return self


class SealedConcept(StrictAnnotationModel):
    """One accepted Concept in the redacted Concept inventory."""

    concept_key: str = Field(pattern=SAFE_ID_PATTERN)
    preferred_name: str = Field(min_length=1, max_length=200)
    short_definition: str = Field(min_length=1, max_length=4_000)
    aliases: JsonArrayTuple[str] = Field(max_length=32)
    review_status: Literal["accepted"]
    validity_status: Literal["current"]
    proposal_origin_declaration: Literal["human"]
    evidence: JsonArrayTuple[EvidenceSpan] = Field(min_length=1, max_length=32)
    review_rationale: str = Field(min_length=1, max_length=4_000)

    @field_validator("preferred_name")
    @classmethod
    def canonical_preferred_name(cls, value: str) -> str:
        return _require_canonical_display(value, label="preferred_name")

    @field_validator("short_definition", "review_rationale")
    @classmethod
    def canonical_concept_text(cls, value: str) -> str:
        return _require_canonical_display(value, label="Concept explanation")

    @field_validator("aliases")
    @classmethod
    def canonical_sealed_aliases(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_alias_sequence(values)

    @field_validator("evidence")
    @classmethod
    def canonical_resolved_evidence(
        cls,
        values: tuple[EvidenceSpan, ...],
    ) -> tuple[EvidenceSpan, ...]:
        identities = tuple(_resolved_evidence_sort_key(item) for item in values)
        if identities != tuple(sorted(identities)):
            raise ValueError("Resolved Concept evidence must be canonically sorted")
        if len(identities) != len(set(identities)):
            raise ValueError("Resolved Concept evidence must be unique")
        return values

    @model_validator(mode="after")
    def preferred_name_is_not_an_alias(self) -> "SealedConcept":
        preferred_key = normalize_alias_key(self.preferred_name)
        if preferred_key in {normalize_alias_key(alias) for alias in self.aliases}:
            raise ValueError("An alias cannot duplicate its preferred name")
        return self


class ExcludedConceptCandidate(StrictAnnotationModel):
    """Redacted audit record for a maintainer-declared exclusion."""

    candidate_key: str = Field(pattern=SAFE_ID_PATTERN)
    candidate_label: str = Field(min_length=1, max_length=200)
    decision: Literal["exclude"]
    proposal_origin_declaration: Literal["human"]
    decision_rationale: str = Field(min_length=1, max_length=4_000)

    @field_validator("candidate_label", "decision_rationale")
    @classmethod
    def canonical_exclusion_text(cls, value: str) -> str:
        return _require_canonical_display(value, label="Concept exclusion text")


class ConceptInventory(StrictAnnotationModel):
    """The final accepted ``C_gold`` inventory; exclusions are not Concepts."""

    schema_version: Literal[1]
    artifact_role: Literal["golden_graph_concept_inventory"]
    protocol_id: str = Field(pattern=SAFE_ID_PATTERN)
    frozen_protocol_sha256: str = Field(pattern=SHA256_PATTERN)
    semantic_source_catalog_sha256: str = Field(pattern=SHA256_PATTERN)
    chunk_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    concept_annotation_worksheet_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewer_key_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewer_key_policy_git_commit: str = Field(
        pattern=_GIT_COMMIT_OID_PATTERN,
    )
    reviewer_id: str = Field(pattern=SAFE_ID_PATTERN)
    reviewer_actor_kind_declaration: Literal["human"]
    blind_to_system_proposals_declaration: Literal[True]
    software_authenticated_prediction_blindness: Literal[False]
    software_authenticated_reviewer_identity: Literal[False]
    concept_count: int = Field(ge=12, le=20)
    concepts: JsonArrayTuple[SealedConcept] = Field(min_length=12, max_length=20)

    @model_validator(mode="after")
    def canonical_closed_world_inventory(self) -> "ConceptInventory":
        if self.concept_count != len(self.concepts):
            raise ValueError("concept_count must equal the accepted inventory size")
        keys = tuple(item.concept_key for item in self.concepts)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("Sealed Concepts must be sorted by unique concept_key")
        _validate_global_names(self.concepts)
        return self


class GoldAliasEntry(StrictAnnotationModel):
    """One exact display/normalization row derived from ``C_gold``."""

    concept_key: str = Field(pattern=SAFE_ID_PATTERN)
    name_kind: Literal["preferred_name", "alias"]
    display_text: str = Field(min_length=1, max_length=200)
    normalized_text: str = Field(min_length=1, max_length=200)

    @field_validator("display_text")
    @classmethod
    def canonical_alias_display(cls, value: str) -> str:
        return _require_canonical_display(value, label="alias display_text")

    @model_validator(mode="after")
    def exact_normalized_alias_key(self) -> "GoldAliasEntry":
        if self.normalized_text != normalize_alias_key(self.display_text):
            raise ValueError("normalized_text must use Concept Graph normalization")
        return self


class GoldAliasTable(StrictAnnotationModel):
    """Public, collision-free matching table bound to one Concept inventory."""

    schema_version: Literal[1]
    artifact_role: Literal["golden_graph_alias_table"]
    concept_inventory_sha256: str = Field(pattern=SHA256_PATTERN)
    concept_count: int = Field(ge=12, le=20)
    entry_count: int = Field(ge=12, le=660)
    entries: JsonArrayTuple[GoldAliasEntry] = Field(min_length=12, max_length=660)

    @model_validator(mode="after")
    def canonical_alias_table(self) -> "GoldAliasTable":
        if self.entry_count != len(self.entries):
            raise ValueError("entry_count must equal alias-table size")
        concept_keys = {entry.concept_key for entry in self.entries}
        if len(concept_keys) != self.concept_count:
            raise ValueError("Alias table must cover exactly concept_count Concepts")
        actual = tuple(_alias_entry_sort_key(entry) for entry in self.entries)
        if actual != tuple(sorted(actual)):
            raise ValueError("Alias-table entries must be canonically sorted")
        normalized = tuple(entry.normalized_text for entry in self.entries)
        if len(normalized) != len(set(normalized)):
            raise ValueError("Alias-table normalized names must be globally unique")
        preferred_counts = {
            key: sum(
                entry.concept_key == key and entry.name_kind == "preferred_name"
                for entry in self.entries
            )
            for key in concept_keys
        }
        if set(preferred_counts.values()) != {1}:
            raise ValueError("Every Concept needs exactly one preferred-name row")
        return self


class ConceptInventorySealRequest(StrictAnnotationModel):
    """Canonical redacted commitment signed outside the application process."""

    schema_version: Literal[1]
    artifact_role: Literal["golden_graph_concept_inventory_seal_request"]
    namespace: Literal["video-course-cards-g2-concepts-v1"]
    protocol_id: str = Field(pattern=SAFE_ID_PATTERN)
    frozen_protocol_sha256: str = Field(pattern=SHA256_PATTERN)
    semantic_source_catalog_sha256: str = Field(pattern=SHA256_PATTERN)
    chunk_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    concept_inventory_sha256: str = Field(pattern=SHA256_PATTERN)
    gold_alias_table_sha256: str = Field(pattern=SHA256_PATTERN)
    concept_annotation_worksheet_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewer_key_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewer_key_policy_git_commit: str = Field(
        pattern=_GIT_COMMIT_OID_PATTERN,
    )
    reviewer_id: str = Field(pattern=SAFE_ID_PATTERN)
    reviewer_actor_kind_declaration: Literal["human"]
    blind_to_system_proposals_declaration: Literal[True]
    software_authenticated_prediction_blindness: Literal[False]
    software_authenticated_reviewer_identity: Literal[False]
    concept_count: int = Field(ge=12, le=20)
    excluded_candidate_count: int = Field(ge=0, le=88)
    total_candidate_count: int = Field(ge=12, le=100)
    approval_statement: Literal[
        "key_control_approval_only_not_proof_of_humanity"
    ]

    @model_validator(mode="after")
    def exhaustive_candidate_counts(self) -> "ConceptInventorySealRequest":
        if self.total_candidate_count != (
            self.concept_count + self.excluded_candidate_count
        ):
            raise ValueError("Seal-request candidate counts must be exhaustive")
        return self


class DetachedKeyAttestationReference(StrictAnnotationModel):
    """Hashes and signer metadata from separately verified detached bytes."""

    signer_identity: str = Field(min_length=1, max_length=320)
    namespace: G2AttestationNamespace
    signed_payload_sha256: str = Field(pattern=SHA256_PATTERN)
    allowed_signers_sha256: str = Field(pattern=SHA256_PATTERN)
    signature_sha256: str = Field(pattern=SHA256_PATTERN)
    public_key_fingerprint: str = Field(
        pattern=_SSH_SHA256_FINGERPRINT_PATTERN,
    )
    key_control_only_not_proof_of_humanity: Literal[True]

    @field_validator("signer_identity", "namespace")
    @classmethod
    def canonical_attestation_identity(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("Attestation identity fields must be trimmed and safe")
        return value


class DetachedKeyAttestationArtifact(StrictAnnotationModel):
    """Portable OpenSSH proof bytes for one redacted seal request.

    The allowed-signers policy is intentionally restricted to one Ed25519 key
    and no options or comment.  This keeps the public artifact deterministic
    and prevents an accidental email address or unrelated key from entering
    the repository.  It still proves key control only, never humanity.
    """

    schema_version: Literal[1]
    artifact_role: Literal["golden_graph_detached_key_attestation"]
    signer_identity: str = Field(min_length=1, max_length=255)
    namespace: G2AttestationNamespace
    signed_payload_sha256: str = Field(pattern=SHA256_PATTERN)
    allowed_signers_policy_utf8: str = Field(
        pattern=_SSH_ED25519_ALLOWED_SIGNER_PATTERN,
        repr=False,
    )
    allowed_signers_sha256: str = Field(pattern=SHA256_PATTERN)
    signature_armored: str = Field(
        pattern=_SSH_SIGNATURE_PATTERN,
        max_length=64 * 1024,
        repr=False,
    )
    signature_sha256: str = Field(pattern=SHA256_PATTERN)
    public_key_fingerprint: str = Field(
        pattern=_SSH_SHA256_FINGERPRINT_PATTERN,
    )
    key_control_only_not_proof_of_humanity: Literal[True]

    @model_validator(mode="after")
    def exact_embedded_authority_hashes(self) -> "DetachedKeyAttestationArtifact":
        if not self.allowed_signers_policy_utf8.startswith(
            f"{self.signer_identity} ssh-ed25519 "
        ):
            raise ValueError("Allowed-signers identity must match signer_identity")
        if hashlib.sha256(
            self.allowed_signers_policy_utf8.encode("utf-8")
        ).hexdigest() != self.allowed_signers_sha256:
            raise ValueError("Allowed-signers policy hash is inconsistent")
        if hashlib.sha256(
            self.signature_armored.encode("ascii")
        ).hexdigest() != self.signature_sha256:
            raise ValueError("Detached signature hash is inconsistent")
        return self


class ConceptInventorySeal(StrictAnnotationModel):
    """Public root for Concept inventory only, never a Relation gold claim."""

    schema_version: Literal[1]
    artifact_role: Literal["golden_graph_concept_inventory_seal"]
    status: Literal["concept_inventory_only_not_gold_bundle"]
    protocol_id: str = Field(pattern=SAFE_ID_PATTERN)
    frozen_protocol_sha256: str = Field(pattern=SHA256_PATTERN)
    concept_inventory_sha256: str = Field(pattern=SHA256_PATTERN)
    gold_alias_table_sha256: str = Field(pattern=SHA256_PATTERN)
    concept_inventory_seal_request_sha256: str = Field(pattern=SHA256_PATTERN)
    detached_attestation_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewer_key_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    reviewer_key_policy_git_commit: str = Field(
        pattern=_GIT_COMMIT_OID_PATTERN,
    )
    reviewer_id: str = Field(pattern=SAFE_ID_PATTERN)
    reviewer_actor_kind_declaration: Literal["human"]
    blind_to_system_proposals_declaration: Literal[True]
    software_authenticated_prediction_blindness: Literal[False]
    software_authenticated_reviewer_identity: Literal[False]
    concept_count: int = Field(ge=12, le=20)
    excluded_candidate_count: int = Field(ge=0, le=88)
    total_candidate_count: int = Field(ge=12, le=100)
    detached_attestation: DetachedKeyAttestationReference

    @model_validator(mode="after")
    def validate_non_gold_seal(self) -> "ConceptInventorySeal":
        if self.total_candidate_count != (
            self.concept_count + self.excluded_candidate_count
        ):
            raise ValueError("Concept seal candidate counts must be exhaustive")
        if self.detached_attestation.signer_identity != self.reviewer_id:
            raise ValueError("Attested signer identity must match reviewer_id")
        if (
            self.detached_attestation.namespace
            != "video-course-cards-g2-concepts-v1"
        ):
            raise ValueError("Detached attestation namespace is invalid")
        if (
            self.detached_attestation.signed_payload_sha256
            != self.concept_inventory_seal_request_sha256
        ):
            raise ValueError("Detached attestation must bind the seal request")
        return self


class RelationPair(StrictAnnotationModel):
    """One deterministically identified unordered pair from frozen ``C_gold``."""

    pair_id: str = Field(pattern=SHA256_PATTERN)
    left_concept_key: str = Field(pattern=SAFE_ID_PATTERN)
    right_concept_key: str = Field(pattern=SAFE_ID_PATTERN)

    @model_validator(mode="after")
    def canonical_pair(self) -> "RelationPair":
        if self.left_concept_key >= self.right_concept_key:
            raise ValueError("Relation pair endpoints must be strictly sorted")
        expected = relation_pair_id(
            self.left_concept_key,
            self.right_concept_key,
        )
        if self.pair_id != expected:
            raise ValueError("Relation pair id does not match its endpoints")
        return self


class RelationPairManifest(StrictAnnotationModel):
    """The complete N*(N-1)/2 pair universe for one sealed inventory."""

    schema_version: Literal[1]
    artifact_role: Literal["golden_graph_relation_pair_manifest"]
    status: Literal["complete_relation_pair_universe"]
    concept_inventory_seal_sha256: str = Field(pattern=SHA256_PATTERN)
    concept_inventory_sha256: str = Field(pattern=SHA256_PATTERN)
    concept_count: int = Field(ge=12, le=20)
    concept_keys: JsonArrayTuple[str] = Field(min_length=12, max_length=20)
    pair_count: int = Field(ge=66, le=190)
    pairs: JsonArrayTuple[RelationPair] = Field(min_length=66, max_length=190)

    @model_validator(mode="after")
    def exact_complete_pair_universe(self) -> "RelationPairManifest":
        keys = self.concept_keys
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("Manifest Concept keys must be sorted and unique")
        if any(not _is_safe_id(key) for key in keys):
            raise ValueError("Manifest Concept keys must use the safe-key grammar")
        if self.concept_count != len(keys):
            raise ValueError("concept_count must equal Concept-key count")

        expected_count = self.concept_count * (self.concept_count - 1) // 2
        if self.pair_count != expected_count or len(self.pairs) != expected_count:
            raise ValueError("Relation pair count must equal N*(N-1)/2")

        expected = tuple(
            (
                relation_pair_id(left, right),
                left,
                right,
            )
            for left, right in combinations(keys, 2)
        )
        actual = tuple(
            (pair.pair_id, pair.left_concept_key, pair.right_concept_key)
            for pair in self.pairs
        )
        if actual != expected:
            raise ValueError(
                "Relation pairs must exactly match the canonical complete universe"
            )
        return self


def relation_pair_id(left_concept_key: str, right_concept_key: str) -> str:
    """Return the domain-separated lowercase SHA-256 for a canonical pair."""

    if not _is_safe_id(left_concept_key) or not _is_safe_id(right_concept_key):
        raise ValueError("Relation pair endpoints must use the safe-key grammar")
    if left_concept_key >= right_concept_key:
        raise ValueError("Relation pair endpoints must be strictly sorted")
    digest = hashlib.sha256()
    digest.update(_RELATION_PAIR_DOMAIN)
    digest.update(left_concept_key.encode("ascii"))
    digest.update(b"\x00")
    digest.update(right_concept_key.encode("ascii"))
    return digest.hexdigest()


def _require_canonical_display(value: str, *, label: str) -> str:
    normalized = normalize_alias_display(value)
    if not normalized or normalized != value:
        raise ValueError(f"{label} must use canonical NFKC/whitespace display")
    return value


def _validate_alias_sequence(values: tuple[str, ...]) -> tuple[str, ...]:
    for value in values:
        if len(value) > 200:
            raise ValueError("Concept aliases cannot exceed 200 characters")
        _require_canonical_display(value, label="Concept alias")
        if len(normalize_alias_key(value)) > 200:
            raise ValueError("Normalized Concept aliases cannot exceed 200 characters")
    identities = tuple((normalize_alias_key(value), value) for value in values)
    if identities != tuple(sorted(identities)):
        raise ValueError("Concept aliases must be canonically sorted")
    normalized = tuple(item[0] for item in identities)
    if len(normalized) != len(set(normalized)):
        raise ValueError("Concept aliases must be unique after normalization")
    return values


def _validate_global_names(concepts: tuple[object, ...]) -> None:
    normalized: list[str] = []
    for concept in concepts:
        preferred_name = getattr(concept, "preferred_name")
        aliases = getattr(concept, "aliases")
        if preferred_name is None:
            continue
        normalized.append(normalize_alias_key(preferred_name))
        normalized.extend(normalize_alias_key(alias) for alias in aliases)
    if len(normalized) != len(set(normalized)):
        raise ValueError(
            "Preferred names and aliases must be globally collision-free"
        )


def _draft_evidence_sort_key(
    evidence: EvidenceSelectionDraft,
) -> tuple[int, str, int, str, str]:
    quote_sha256 = hashlib.sha256(evidence.exact_quote.encode("utf-8")).hexdigest()
    return (
        evidence.chunk_ordinal,
        evidence.logical_page_id,
        -1
        if evidence.page_global_utf8_start is None
        else evidence.page_global_utf8_start,
        evidence.semantic_chunk_sha256,
        quote_sha256,
    )


def _resolved_evidence_sort_key(
    evidence: EvidenceSpan,
) -> tuple[int, str, int, int, str, str]:
    return (
        evidence.chunk_ordinal,
        evidence.logical_page_id,
        evidence.page_utf8_start,
        evidence.page_utf8_end,
        evidence.semantic_chunk_sha256,
        evidence.semantic_span_sha256,
    )


def _alias_entry_sort_key(entry: GoldAliasEntry) -> tuple[str, int, str, str]:
    return (
        entry.concept_key,
        0 if entry.name_kind == "preferred_name" else 1,
        entry.normalized_text,
        entry.display_text,
    )


def _is_safe_id(value: str) -> bool:
    return re.fullmatch(SAFE_ID_PATTERN, value) is not None


__all__ = [
    "CONCEPT_ATTESTATION_NAMESPACE",
    "ConceptAnnotationWorksheet",
    "ConceptDecisionDraft",
    "ConceptInventory",
    "ConceptInventorySeal",
    "ConceptInventorySealRequest",
    "DetachedKeyAttestationArtifact",
    "DetachedKeyAttestationReference",
    "EvidenceSelectionDraft",
    "EvidenceSpan",
    "ExcludedConceptCandidate",
    "G2AttestationNamespace",
    "G2_ATTESTATION_NAMESPACES",
    "GOLD_BUNDLE_ATTESTATION_NAMESPACE",
    "GoldAliasEntry",
    "GoldAliasTable",
    "RelationPair",
    "RelationPairManifest",
    "RELATION_PASS_A_ATTESTATION_NAMESPACE",
    "RELATION_PASS_B_ATTESTATION_NAMESPACE",
    "SealedConcept",
    "relation_pair_id",
]
