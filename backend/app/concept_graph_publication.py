from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .concept_graph import (
    ConceptIdentityStatus,
    ConceptRelationType,
    GraphReviewStatus,
    GraphValidityStatus,
    ProposalOrigin,
    RelationEvidenceSupportRole,
    RelationSupportBasis,
)
from .course_source import SourceLocator
from .source_asset import SourceAssetType


PublicationEntityType = Literal["concept", "relation", "graph", "source"]


class GraphPublicationRequest(BaseModel):
    """A compare-and-swap request to publish one immutable graph version."""

    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=1, max_length=100)
    expected_active_version: int | None = Field(ge=1)
    expected_draft_manifest_hash: str = Field(min_length=64, max_length=64)
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2_000)

    @field_validator("operation_id", mode="before")
    @classmethod
    def clean_operation_id(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Operation id is required.")
        return cleaned

    @field_validator("actor", "reason", mode="before")
    @classmethod
    def clean_operation_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        cleaned = " ".join(value.strip().split())
        if not cleaned:
            raise ValueError("Operation metadata is required.")
        return cleaned

    @field_validator("expected_draft_manifest_hash")
    @classmethod
    def validate_manifest_hash(cls, value: str) -> str:
        if any(character not in "0123456789abcdef" for character in value):
            raise ValueError("Draft manifest hash must be lowercase hexadecimal.")
        return value


class GraphPublicationIssue(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    entity_type: PublicationEntityType
    entity_id: str | None = Field(default=None, max_length=200)
    revision: int | None = Field(default=None, ge=1)
    message: str = Field(min_length=1, max_length=500)


class GraphPublicationCounts(BaseModel):
    concepts: int = Field(ge=0)
    relations: int = Field(ge=0)
    concept_aliases: int = Field(ge=0)
    concept_evidence: int = Field(ge=0)
    relation_evidence: int = Field(ge=0)


class GraphPublicationPreview(BaseModel):
    active_version: int | None = Field(default=None, ge=1)
    draft_manifest_hash: str = Field(min_length=64, max_length=64)
    content_hash: str = Field(min_length=64, max_length=64)
    publishable: bool
    has_changes: bool
    issues: list[GraphPublicationIssue] = Field(default_factory=list)
    issue_count: int = Field(ge=0)
    issues_truncated: bool = False
    counts: GraphPublicationCounts
    computed_at: datetime


class PublishedEvidence(BaseModel):
    evidence_id: str = Field(min_length=1, max_length=200)
    source_id: str = Field(min_length=1, max_length=200)
    chunk_id: str = Field(min_length=1, max_length=200)
    chunk_text_hash: str = Field(min_length=64, max_length=64)
    projection_generation_id: str = Field(min_length=1, max_length=200)
    source_title: str = Field(min_length=1)
    source_type: SourceAssetType
    quote: str = Field(min_length=1, max_length=16_000)
    locator: SourceLocator
    ordinal: int = Field(ge=0, le=31)
    created_at: datetime


class PublishedConceptAlias(BaseModel):
    alias_id: str = Field(min_length=1, max_length=200)
    display_text: str = Field(min_length=1, max_length=200)
    normalized_text: str = Field(min_length=1, max_length=200)
    ordinal: int = Field(ge=0, le=31)
    created_at: datetime


class PublishedConcept(BaseModel):
    concept_id: str = Field(min_length=1, max_length=200)
    concept_revision: int = Field(ge=1)
    preferred_name: str = Field(min_length=1, max_length=200)
    short_definition: str = Field(min_length=1, max_length=4_000)
    identity_status: ConceptIdentityStatus
    review_status: GraphReviewStatus
    validity_status: GraphValidityStatus
    proposal_origin: ProposalOrigin
    provider: str | None = None
    model: str | None = None
    prompt_protocol: str | None = None
    output_version: str | None = None
    review_operation_id: str = Field(min_length=1, max_length=100)
    review_operation_request_hash: str = Field(min_length=64, max_length=64)
    review_actor: str = Field(min_length=1, max_length=200)
    review_reason: str = Field(min_length=1, max_length=2_000)
    reviewed_at: datetime
    review_revision: int = Field(ge=1)
    revision_created_at: datetime
    revision_updated_at: datetime
    aggregate_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    ordinal: int = Field(ge=0)
    aliases: list[PublishedConceptAlias] = Field(default_factory=list)
    evidence: list[PublishedEvidence] = Field(default_factory=list)


class PublishedRelationEvidence(PublishedEvidence):
    support_role: RelationEvidenceSupportRole


class PublishedRelation(BaseModel):
    relation_id: str = Field(min_length=1, max_length=200)
    relation_revision: int = Field(ge=1)
    source_concept_id: str = Field(min_length=1, max_length=200)
    source_concept_revision: int = Field(ge=1)
    target_concept_id: str = Field(min_length=1, max_length=200)
    target_concept_revision: int = Field(ge=1)
    relation_type: ConceptRelationType
    support_basis: RelationSupportBasis
    rationale: str = Field(min_length=1, max_length=4_000)
    review_status: GraphReviewStatus
    validity_status: GraphValidityStatus
    proposal_origin: ProposalOrigin
    provider: str | None = None
    model: str | None = None
    prompt_protocol: str | None = None
    output_version: str | None = None
    review_operation_id: str = Field(min_length=1, max_length=100)
    review_operation_request_hash: str = Field(min_length=64, max_length=64)
    review_actor: str = Field(min_length=1, max_length=200)
    review_reason: str = Field(min_length=1, max_length=2_000)
    reviewed_at: datetime
    review_revision: int = Field(ge=1)
    binding_created_at: datetime
    revision_created_at: datetime
    revision_updated_at: datetime
    aggregate_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    ordinal: int = Field(ge=0)
    evidence: list[PublishedRelationEvidence] = Field(default_factory=list)


class GraphVersionMetadata(BaseModel):
    course_id: str = Field(min_length=1, max_length=200)
    version_number: int = Field(ge=1)
    parent_version_number: int | None = Field(default=None, ge=1)
    draft_manifest_hash: str = Field(min_length=64, max_length=64)
    content_hash: str = Field(min_length=64, max_length=64)
    counts: GraphPublicationCounts
    published_by: str = Field(min_length=1, max_length=200)
    publication_reason: str = Field(min_length=1, max_length=2_000)
    published_at: datetime
    is_active_version: bool
    source_authority_current: bool
    source_authority_issues: list[GraphPublicationIssue] = Field(
        default_factory=list
    )
    source_authority_issue_count: int = Field(ge=0)
    source_authority_issues_truncated: bool = False


class GraphVersionPage(BaseModel):
    items: list[GraphVersionMetadata] = Field(default_factory=list)
    next_cursor: str | None = Field(default=None, max_length=200)


class PublishedConceptPage(BaseModel):
    items: list[PublishedConcept] = Field(default_factory=list)
    next_cursor: str | None = Field(default=None, max_length=200)


class PublishedRelationPage(BaseModel):
    items: list[PublishedRelation] = Field(default_factory=list)
    next_cursor: str | None = Field(default=None, max_length=200)
