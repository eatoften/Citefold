from __future__ import annotations

from datetime import datetime
from typing import Literal
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .course_source import SourceLocator
from .job import utc_now
from .source_asset import SourceAssetType


GraphReviewStatus = Literal["candidate", "accepted", "rejected"]
GraphValidityStatus = Literal["current", "stale", "tombstoned"]
ConceptIdentityStatus = Literal["active", "merged", "retired"]
ProposalOrigin = Literal["human", "model", "import"]
ConceptRelationType = Literal[
    "prerequisite",
    "part_of",
    "example_of",
    "related",
    "contrast_with",
]
RelationSupportBasis = Literal[
    "source_asserted",
    "pedagogical_inference",
]
RelationEvidenceSupportRole = Literal[
    "relation_assertion",
    "source_endpoint",
    "target_endpoint",
]
GraphReviewDecision = Literal["accept", "reject"]

SYMMETRIC_RELATION_TYPES = {"related", "contrast_with"}


class EvidenceReferenceCreate(BaseModel):
    """A client reference that the server must resolve and snapshot."""

    chunk_id: str = Field(min_length=1, max_length=200)
    quote: str = Field(min_length=1, max_length=16_000)

    @field_validator("chunk_id")
    @classmethod
    def clean_chunk_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Evidence chunk id is required.")
        return cleaned

    @field_validator("quote")
    @classmethod
    def validate_quote(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Evidence quote is required.")
        return value


class RelationEvidenceReferenceCreate(EvidenceReferenceCreate):
    support_role: RelationEvidenceSupportRole


class ConceptCreate(BaseModel):
    preferred_name: str = Field(min_length=1, max_length=200)
    short_definition: str = Field(min_length=1, max_length=4_000)
    evidence: list[EvidenceReferenceCreate] = Field(
        min_length=1,
        max_length=32,
    )
    aliases: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("preferred_name", "short_definition")
    @classmethod
    def clean_required_text(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if not cleaned:
            raise ValueError("Concept text is required.")
        return cleaned

    @model_validator(mode="after")
    def reject_duplicate_evidence(self) -> "ConceptCreate":
        identities = [
            (item.chunk_id, item.quote)
            for item in self.evidence
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("Concept evidence references must be unique.")
        self.aliases = validate_aliases(
            self.aliases,
            preferred_name=self.preferred_name,
        )
        return self


class ConceptRelationCreate(BaseModel):
    source_concept_id: str = Field(min_length=1, max_length=200)
    target_concept_id: str = Field(min_length=1, max_length=200)
    relation_type: ConceptRelationType
    support_basis: RelationSupportBasis
    rationale: str = Field(min_length=1, max_length=4_000)
    evidence: list[RelationEvidenceReferenceCreate] = Field(
        min_length=1,
        max_length=32,
    )

    @field_validator("source_concept_id", "target_concept_id")
    @classmethod
    def clean_concept_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Relation endpoint is required.")
        return cleaned

    @field_validator("rationale")
    @classmethod
    def clean_rationale(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if not cleaned:
            raise ValueError("Relation rationale is required.")
        return cleaned

    @model_validator(mode="after")
    def validate_evidence_contract(self) -> "ConceptRelationCreate":
        if self.source_concept_id == self.target_concept_id:
            raise ValueError("A Concept relation cannot be a self-loop.")

        roles = {item.support_role for item in self.evidence}
        if self.support_basis == "source_asserted":
            if roles != {"relation_assertion"}:
                raise ValueError(
                    "A source-asserted relation only accepts "
                    "relation-assertion evidence."
                )
        elif roles != {"source_endpoint", "target_endpoint"}:
            raise ValueError(
                "A pedagogical inference only accepts evidence for both "
                "endpoints."
            )

        identities = [
            (item.support_role, item.chunk_id, item.quote)
            for item in self.evidence
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("Relation evidence references must be unique.")
        return self


class CandidateRecord(BaseModel):
    revision: int = Field(default=1, ge=1)
    review_status: GraphReviewStatus = "candidate"
    validity_status: GraphValidityStatus = "current"
    proposal_origin: ProposalOrigin = "human"
    provider: str | None = None
    model: str | None = None
    prompt_protocol: str | None = None
    output_version: str | None = None
    review_actor: str | None = None
    reviewed_at: datetime | None = None
    review_revision: int | None = Field(default=None, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ConceptAlias(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    course_id: str = Field(min_length=1, max_length=200)
    concept_id: str = Field(min_length=1, max_length=200)
    concept_revision: int = Field(ge=1)
    display_text: str = Field(min_length=1, max_length=200)
    normalized_text: str = Field(min_length=1, max_length=200)
    ordinal: int = Field(ge=0)
    created_at: datetime = Field(default_factory=utc_now)


class RelationEndpointRevisionBinding(BaseModel):
    relation_id: str = Field(min_length=1, max_length=200)
    course_id: str = Field(min_length=1, max_length=200)
    relation_revision: int = Field(ge=1)
    source_concept_id: str = Field(min_length=1, max_length=200)
    source_concept_revision: int = Field(ge=1)
    target_concept_id: str = Field(min_length=1, max_length=200)
    target_concept_revision: int = Field(ge=1)
    created_at: datetime = Field(default_factory=utc_now)


class GraphMutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=1)
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2_000)

    @field_validator("operation_id")
    @classmethod
    def clean_operation_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Operation id is required.")
        return cleaned

    @field_validator("actor", "reason")
    @classmethod
    def clean_mutation_text(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if not cleaned:
            raise ValueError("Mutation metadata is required.")
        return cleaned


class ConceptRevisionEdit(GraphMutationRequest):
    preferred_name: str = Field(min_length=1, max_length=200)
    short_definition: str = Field(min_length=1, max_length=4_000)
    aliases: list[str] = Field(default_factory=list, max_length=32)
    evidence: list[EvidenceReferenceCreate] = Field(min_length=1, max_length=32)

    @field_validator("preferred_name", "short_definition")
    @classmethod
    def clean_required_text(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if not cleaned:
            raise ValueError("Concept text is required.")
        return cleaned

    @model_validator(mode="after")
    def validate_edit(self) -> "ConceptRevisionEdit":
        self.aliases = validate_aliases(
            self.aliases,
            preferred_name=self.preferred_name,
        )
        identities = [(item.chunk_id, item.quote) for item in self.evidence]
        if len(identities) != len(set(identities)):
            raise ValueError("Concept evidence references must be unique.")
        return self


class RelationRevisionEdit(GraphMutationRequest):
    support_basis: RelationSupportBasis
    rationale: str = Field(min_length=1, max_length=4_000)
    expected_source_concept_revision: int = Field(ge=1)
    expected_target_concept_revision: int = Field(ge=1)
    evidence: list[RelationEvidenceReferenceCreate] = Field(
        min_length=1,
        max_length=32,
    )

    @field_validator("rationale")
    @classmethod
    def clean_rationale(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if not cleaned:
            raise ValueError("Relation rationale is required.")
        return cleaned

    @model_validator(mode="after")
    def validate_evidence_contract(self) -> "RelationRevisionEdit":
        _validate_relation_evidence_roles(self.support_basis, self.evidence)
        return self


class GraphReviewRequest(GraphMutationRequest):
    decision: GraphReviewDecision


class RelationReviewRequest(GraphReviewRequest):
    expected_source_concept_revision: int = Field(ge=1)
    expected_target_concept_revision: int = Field(ge=1)


class GraphMarkStaleRequest(GraphMutationRequest):
    pass


class ConceptMergeRequest(GraphMutationRequest):
    survivor_concept_id: str = Field(min_length=1, max_length=200)
    expected_survivor_revision: int = Field(ge=1)

    @field_validator("survivor_concept_id")
    @classmethod
    def clean_survivor_concept_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Survivor Concept id is required.")
        return cleaned


class ConceptRetireRequest(GraphMutationRequest):
    pass


class SourceEvidenceSnapshot(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    course_id: str = Field(min_length=1, max_length=200)
    source_id: str = Field(min_length=1, max_length=200)
    chunk_id: str = Field(min_length=1, max_length=200)
    chunk_text_hash: str = Field(min_length=64, max_length=64)
    projection_generation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
    )
    projection_is_current: bool = False
    projection_currentness_reasons: list[str] = Field(default_factory=list)
    source_title: str = Field(min_length=1)
    source_type: SourceAssetType
    quote: str = Field(min_length=1, max_length=16_000)
    locator: SourceLocator
    ordinal: int = Field(ge=0)
    created_at: datetime = Field(default_factory=utc_now)


class ConceptEvidence(SourceEvidenceSnapshot):
    concept_id: str = Field(min_length=1, max_length=200)
    concept_revision: int = Field(ge=1)


class RelationEvidence(SourceEvidenceSnapshot):
    relation_id: str = Field(min_length=1, max_length=200)
    relation_revision: int = Field(ge=1)
    support_role: RelationEvidenceSupportRole


class ConceptRecord(CandidateRecord):
    id: str = Field(min_length=1, max_length=200)
    course_id: str = Field(min_length=1, max_length=200)
    preferred_name: str = Field(min_length=1, max_length=200)
    short_definition: str = Field(min_length=1, max_length=4_000)
    identity_status: ConceptIdentityStatus = "active"
    merged_into_concept_id: str | None = Field(default=None, max_length=200)


class Concept(ConceptRecord):
    evidence: list[ConceptEvidence] = Field(default_factory=list)
    aliases: list[ConceptAlias] = Field(default_factory=list)
    is_current_revision: bool = True
    evidence_current: bool = False
    eligible_for_publication: bool = False
    currentness_reasons: list[str] = Field(default_factory=list)


class ConceptSummary(ConceptRecord):
    evidence_count: int = Field(ge=0)


class ConceptRelationRecord(CandidateRecord):
    id: str = Field(min_length=1, max_length=200)
    course_id: str = Field(min_length=1, max_length=200)
    source_concept_id: str = Field(min_length=1, max_length=200)
    target_concept_id: str = Field(min_length=1, max_length=200)
    relation_type: ConceptRelationType
    support_basis: RelationSupportBasis
    rationale: str = Field(min_length=1, max_length=4_000)


class ConceptRelation(ConceptRelationRecord):
    evidence: list[RelationEvidence] = Field(default_factory=list)
    endpoint_binding: RelationEndpointRevisionBinding | None = None
    is_current_revision: bool = True
    evidence_current: bool = False
    endpoint_revisions_current: bool = False
    eligible_for_publication: bool = False
    currentness_reasons: list[str] = Field(default_factory=list)


class ConceptRelationSummary(ConceptRelationRecord):
    evidence_count: int = Field(ge=0)


class ConceptPage(BaseModel):
    items: list[ConceptSummary] = Field(default_factory=list)
    next_cursor: str | None = Field(default=None, max_length=200)


class ConceptRelationPage(BaseModel):
    items: list[ConceptRelationSummary] = Field(default_factory=list)
    next_cursor: str | None = Field(default=None, max_length=200)


def canonicalize_relation_endpoints(
    relation_type: ConceptRelationType,
    source_concept_id: str,
    target_concept_id: str,
) -> tuple[str, str]:
    if relation_type not in SYMMETRIC_RELATION_TYPES:
        return source_concept_id, target_concept_id
    return tuple(sorted((source_concept_id, target_concept_id)))


def normalize_alias_display(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().split())


def normalize_alias_key(value: str) -> str:
    return normalize_alias_display(value).casefold()


def validate_aliases(
    values: list[str],
    *,
    preferred_name: str | None = None,
) -> list[str]:
    cleaned: list[str] = []
    normalized: set[str] = set()
    for value in values:
        display = normalize_alias_display(value)
        if not display:
            raise ValueError("Concept aliases cannot be blank.")
        if len(display) > 200:
            raise ValueError("Concept aliases cannot exceed 200 characters.")
        key = normalize_alias_key(display)
        if len(key) > 200:
            raise ValueError(
                "Normalized Concept aliases cannot exceed 200 characters."
            )
        if key in normalized:
            raise ValueError("Concept aliases must be unique after normalization.")
        normalized.add(key)
        cleaned.append(display)
    if preferred_name is not None:
        preferred_key = normalize_alias_key(preferred_name)
        if preferred_key in normalized:
            raise ValueError(
                "A Concept alias cannot duplicate its preferred name."
            )
    return cleaned


def _validate_relation_evidence_roles(
    support_basis: RelationSupportBasis,
    evidence: list[RelationEvidenceReferenceCreate],
) -> None:
    roles = {item.support_role for item in evidence}
    if support_basis == "source_asserted":
        if roles != {"relation_assertion"}:
            raise ValueError(
                "A source-asserted relation only accepts "
                "relation-assertion evidence."
            )
    elif roles != {"source_endpoint", "target_endpoint"}:
        raise ValueError(
            "A pedagogical inference only accepts evidence for both endpoints."
        )
    identities = [
        (item.support_role, item.chunk_id, item.quote)
        for item in evidence
    ]
    if len(identities) != len(set(identities)):
        raise ValueError("Relation evidence references must be unique.")
