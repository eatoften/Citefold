"""Strict schema for a reproducible, redacted golden-graph protocol."""

from __future__ import annotations

from datetime import date
from pathlib import PurePosixPath
import re
from typing import Annotated, Literal, TypeVar

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


SHA256_PATTERN = r"^[0-9a-f]{64}$"
SAFE_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,127}$"
_SAFE_ID = re.compile(SAFE_ID_PATTERN)
_WINDOWS_UNSAFE_PATH_CHARS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_PATH_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)

ProtocolStatus = Literal["draft", "frozen"]
BenchmarkPartition = Literal["authoring", "development", "sealed_transfer"]
MetricComparison = Literal["eq", "gte", "lte"]
ConfidenceBoundSide = Literal["none", "lower", "upper"]
MetricEvidenceScope = Literal[
    "authoring_graph_invariants",
    "future_confirmatory_claim_gate",
    "synthetic_graph_performance",
]
RequiredMetricProtocol = Literal[
    "gold_bundle_seal",
    "grounded_answer_evaluation_bundle",
    "synthetic_graph_performance_v1",
]
MetricId = Literal[
    "accepted_current_evidence_validity",
    "graph_integrity_violation_count",
    "deterministic_path_hash_rate",
    "golden_path_validity",
    "edge_evidence_completeness",
    "locator_open_rate",
    "concept_inventory_coverage",
    "accepted_current_isolate_rate",
    "retrieval_recall_at_5",
    "citation_precision",
    "citation_recall",
    "abstention_f1",
    "concept_proposal_f1",
    "concept_evidence_precision",
    "relation_proposal_precision",
    "relation_proposal_recall",
    "path_api_p95_1000_nodes_ms",
    "path_api_p95_10000_nodes_ms",
]
ReportOnlyMetricId = Literal["relation_proposal_recall"]
V1ConceptRelationType = Literal[
    "prerequisite",
    "part_of",
    "example_of",
    "related",
    "contrast_with",
]
V1RelationSupportBasis = Literal["source_asserted", "pedagogical_inference"]
V1RelationEvidenceSupportRole = Literal[
    "relation_assertion",
    "source_endpoint",
    "target_endpoint",
]

_JsonArrayItem = TypeVar("_JsonArrayItem")


def _json_array_to_tuple(value: object) -> object:
    """Preserve strict JSON-array input while making parsed state immutable."""

    if isinstance(value, list):
        return tuple(value)
    return value


JsonArrayTuple = Annotated[
    tuple[_JsonArrayItem, ...],
    BeforeValidator(_json_array_to_tuple),
]


class StrictProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class AcquisitionIdentity(StrictProtocolModel):
    manifest_path: str = Field(min_length=1, max_length=500)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    corpus_id: str = Field(pattern=SAFE_ID_PATTERN)
    repository_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    asset_id: str = Field(pattern=SAFE_ID_PATTERN)
    partition: BenchmarkPartition
    raw_sha256: str = Field(pattern=SHA256_PATTERN)
    license_spdx: str = Field(min_length=1, max_length=100)
    redistribution_allowed: bool

    @field_validator("license_spdx")
    @classmethod
    def validate_trimmed_license(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("license_spdx must be trimmed")
        return value

    @field_validator("manifest_path")
    @classmethod
    def validate_manifest_path(cls, value: str) -> str:
        return _normalized_repository_path(value, label="manifest_path")


class PageScope(StrictProtocolModel):
    numbering: Literal["pdf_page_1_based"]
    asset_page_count: int | None = Field(ge=1, le=10_000)
    included_pages: JsonArrayTuple[int] | None = Field(
        max_length=10_000,
    )
    excluded_pages: JsonArrayTuple[int] | None = Field(
        max_length=10_000,
    )
    inclusion_reason: str | None = Field(min_length=1, max_length=2_000)
    exclusion_reason: str | None = Field(min_length=1, max_length=2_000)

    @field_validator("included_pages", "excluded_pages")
    @classmethod
    def validate_ordered_unique_pages(
        cls, value: tuple[int, ...] | None
    ) -> tuple[int, ...] | None:
        if value is None:
            return value
        if any(
            isinstance(page, bool) or not isinstance(page, int) or page < 1
            for page in value
        ):
            raise ValueError("Page numbers must be positive strict integers")
        if value != tuple(sorted(set(value))):
            raise ValueError("Page numbers must be unique and sorted")
        return value

    @field_validator("inclusion_reason", "exclusion_reason")
    @classmethod
    def validate_trimmed_reason(cls, value: str | None) -> str | None:
        if value is not None and value != value.strip():
            raise ValueError("Page-scope reasons must be trimmed")
        return value

    @model_validator(mode="after")
    def reject_partial_draft_scope(self) -> "PageScope":
        values = (
            self.asset_page_count,
            self.included_pages,
            self.excluded_pages,
            self.inclusion_reason,
            self.exclusion_reason,
        )
        if any(value is not None for value in values) and any(
            value is None for value in values
        ):
            raise ValueError("Page scope must be entirely empty or fully specified")
        return self


class ToolIdentity(StrictProtocolModel):
    implementation: str = Field(min_length=1, max_length=200)
    distribution_name: str = Field(min_length=1, max_length=200)
    implementation_path: str = Field(min_length=1, max_length=500)
    implementation_sha256: str = Field(pattern=SHA256_PATTERN)
    version: str = Field(min_length=1, max_length=100)
    config_path: str = Field(min_length=1, max_length=500)
    config_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("implementation", "distribution_name", "version")
    @classmethod
    def validate_trimmed_tool_text(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("Tool identity text must be trimmed")
        return value

    @field_validator("implementation_path", "config_path")
    @classmethod
    def validate_implementation_path(cls, value: str) -> str:
        return _normalized_repository_path(value, label="tool implementation_path")


class ProjectionIdentity(StrictProtocolModel):
    parser: ToolIdentity | None
    chunker: ToolIdentity | None
    dependency_snapshot_path: str = Field(min_length=1, max_length=500)
    dependency_snapshot_sha256: str | None = Field(
        pattern=SHA256_PATTERN
    )
    uv_lock_path: Literal["backend/uv.lock"]
    uv_lock_sha256: str = Field(pattern=SHA256_PATTERN)
    source_catalog_path: str = Field(min_length=1, max_length=500)
    source_catalog_hash_protocol: Literal["semantic-id-independent-v1"]
    semantic_source_catalog_sha256: str | None = Field(
        pattern=SHA256_PATTERN
    )
    chunk_manifest_path: str = Field(min_length=1, max_length=500)
    chunk_manifest_sha256: str | None = Field(pattern=SHA256_PATTERN)

    @field_validator(
        "dependency_snapshot_path",
        "source_catalog_path",
        "chunk_manifest_path",
    )
    @classmethod
    def validate_artifact_path(cls, value: str) -> str:
        return _normalized_repository_path(value, label="projection artifact path")


class RelationOntology(StrictProtocolModel):
    relation_types: JsonArrayTuple[V1ConceptRelationType] = Field(min_length=1)
    support_bases: JsonArrayTuple[V1RelationSupportBasis] = Field(min_length=1)
    support_roles: JsonArrayTuple[V1RelationEvidenceSupportRole] = Field(
        min_length=1
    )
    symmetric_endpoints_canonicalized: Literal[True]
    prerequisite_cycles_forbidden: Literal[True]
    negative_pairs_are_evaluation_labels_only: Literal[True]

    @field_validator("relation_types", "support_bases", "support_roles")
    @classmethod
    def reject_duplicate_ontology_values(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Ontology values must be unique")
        return value


class DelayedBlindReview(StrictProtocolModel):
    reviewer_id: str = Field(pattern=SAFE_ID_PATTERN)
    required_reviewer_actor_kind: Literal["human"]
    human_attestation_required_at_gold_seal: Literal[True]
    annotation_guide_path: Literal["docs/graph-annotation-protocol.md"]
    annotation_guide_sha256: str = Field(pattern=SHA256_PATTERN)
    review_mode: Literal["solo_delayed_two_pass"]
    minimum_delay_hours: Literal[72]
    pass_b_blind_to_pass_a_labels: Literal[True]
    both_passes_blind_to_system_proposals: Literal[True]
    adjudication_required_before_gold_freeze: Literal[True]
    agreement_measure: Literal["temporal_intra_rater"]
    inter_rater_claim_allowed: Literal[False]
    human_decisions_required: Literal[True]


class MinimumSamples(StrictProtocolModel):
    applies_to: Literal["automatic_proposal_and_grounded_answer_claim_gates"]
    answerable_questions: int = Field(ge=40)
    unanswerable_questions: int = Field(ge=20)
    atomic_claim_units: int = Field(ge=80)
    exact_citation_opportunities: int = Field(ge=80)
    proposal_gate_closed_world_concepts: int = Field(ge=50)
    proposal_gate_gold_relations: int = Field(ge=50)
    gold_instances_per_relation_type: int = Field(ge=10)
    supported_relation_types_for_macro_claim: int = Field(ge=3)


class ConfidenceInterval(StrictProtocolModel):
    method: Literal["paired_cluster_bootstrap"]
    confidence_level: Literal[0.95]
    resamples: Literal[10_000]
    seed: int = Field(ge=0, le=2**32 - 1)
    resampling_unit: Literal["lecture"]
    minimum_resampling_clusters: int = Field(ge=5)
    insufficient_clusters_policy: Literal[
        "diagnostic_only_no_confirmatory_ci"
    ]
    zero_support_policy: Literal["not_applicable_excluded_from_macro"]


class MetricTarget(StrictProtocolModel):
    metric_id: MetricId
    comparison: MetricComparison
    threshold: float = Field(allow_inf_nan=False)
    unit: Literal["proportion", "count", "milliseconds"]
    confidence_interval_required: bool
    confidence_bound_side: ConfidenceBoundSide
    confidence_bound: float | None = Field(allow_inf_nan=False)
    evidence_scope: MetricEvidenceScope
    required_protocol: RequiredMetricProtocol

    @model_validator(mode="after")
    def validate_confidence_contract(self) -> "MetricTarget":
        if self.unit == "proportion" and not 0 <= self.threshold <= 1:
            raise ValueError("Proportion thresholds must be within [0, 1]")
        if self.unit == "count" and (
            self.threshold < 0 or not self.threshold.is_integer()
        ):
            raise ValueError("Count thresholds must be non-negative integers")
        if self.unit == "milliseconds" and self.threshold <= 0:
            raise ValueError("Latency thresholds must be positive")
        if not self.confidence_interval_required:
            if self.confidence_bound_side != "none" or self.confidence_bound is not None:
                raise ValueError("Non-statistical targets cannot declare a confidence bound")
        elif self.confidence_bound_side == "none":
            raise ValueError("Statistical targets must declare a confidence-bound side")
        return self


class ReportOnlyMetric(StrictProtocolModel):
    metric_id: ReportOnlyMetricId
    unit: Literal["proportion"]
    evidence_scope: Literal["future_confirmatory_claim_gate"]
    required_protocol: Literal["gold_bundle_seal"]
    interval_reporting: Literal["two_sided_95_percent_when_cluster_eligible"]
    pass_threshold_registered: Literal[False]


class EvaluationContract(StrictProtocolModel):
    threshold_owner_id: str = Field(pattern=SAFE_ID_PATTERN)
    confirmatory: Literal[False]
    reported_metrics: JsonArrayTuple[MetricId] = Field(min_length=1)
    targets: JsonArrayTuple[MetricTarget] = Field(min_length=1)
    report_only_metrics: JsonArrayTuple[ReportOnlyMetric] = Field(min_length=1)
    future_claim_minimum_samples: MinimumSamples
    confidence_interval: ConfidenceInterval
    alias_matching_edge_semantics: Literal[
        "normalized_preferred_name_or_alias_exact_equality"
    ]
    future_alias_table_binding_policy: Literal[
        "gold_bundle_seal_requires_frozen_alias_table_hash"
    ]
    future_performance_binding_policy: Literal[
        "path_latency_requires_separate_frozen_performance_authority"
    ]
    concept_matching: Literal[
        "one_to_one_maximum_bipartite_nfkc_casefold_whitespace"
    ]
    relation_matching: Literal["exact_type_direction_and_normalized_endpoints"]
    zero_denominator_result: Literal["not_applicable"]

    @model_validator(mode="after")
    def validate_unique_metric_contract(self) -> "EvaluationContract":
        if len(self.reported_metrics) != len(set(self.reported_metrics)):
            raise ValueError("reported_metrics must be unique")
        target_ids = [target.metric_id for target in self.targets]
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("Metric targets must be unique")
        if not set(target_ids).issubset(self.reported_metrics):
            raise ValueError("Every target must refer to a reported metric")
        report_only_ids = [item.metric_id for item in self.report_only_metrics]
        if report_only_ids != ["relation_proposal_recall"]:
            raise ValueError(
                "report_only_metrics must contain exactly relation_proposal_recall"
            )
        if set(target_ids) & set(report_only_ids):
            raise ValueError("A metric cannot be both targeted and report-only")
        if set(target_ids) | set(report_only_ids) != set(self.reported_metrics):
            raise ValueError("Every reported metric requires a complete contract")
        return self


class RightsAndRedaction(StrictProtocolModel):
    attribution: str = Field(min_length=1, max_length=1_000)
    license_spdx: str = Field(min_length=1, max_length=100)
    redistribution_allowed: bool
    source_bytes_committed: Literal[False]
    public_artifacts_redacted: Literal[True]
    public_source_text_included: Literal[False]
    public_locator_policy: Literal["logical_page_ids_hashes_and_offsets_only"]

    @field_validator("attribution", "license_spdx")
    @classmethod
    def validate_trimmed_rights_text(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("Rights text must be trimmed")
        return value


class ReleaseControls(StrictProtocolModel):
    path_evaluation_embargoed_until_gold_freeze: Literal[True]
    golden_graph_partition_policy: Literal["authoring_only"]
    sealed_transfer_access_policy: Literal[
        "append_only_access_ledger_required"
    ]
    flagship_automatic_proposal_claim_eligible: Literal[False]
    held_out_claim_eligible: Literal[False]
    counterfactual_fixture_role: Literal["schema_trust_smoke_only"]
    counterfactual_fixture_is_closed_world_gold: Literal[False]


class GoldenGraphProtocol(StrictProtocolModel):
    schema_version: Literal[1]
    artifact_role: Literal["golden_graph_protocol"]
    claim_scope: Literal["authoring_engineering_fixture"]
    protocol_id: str = Field(pattern=SAFE_ID_PATTERN)
    protocol_status: ProtocolStatus
    registered_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    acquisition: AcquisitionIdentity
    page_scope: PageScope
    projection: ProjectionIdentity
    ontology: RelationOntology
    review: DelayedBlindReview
    evaluation: EvaluationContract
    rights: RightsAndRedaction
    release: ReleaseControls

    @field_validator("protocol_id")
    @classmethod
    def validate_safe_protocol_id(cls, value: str) -> str:
        if not _SAFE_ID.fullmatch(value):
            raise ValueError("protocol_id is unsafe")
        return value

    @field_validator("registered_date")
    @classmethod
    def validate_calendar_date(cls, value: str) -> str:
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("registered_date must be a real ISO calendar date") from exc
        if parsed.isoformat() != value:
            raise ValueError("registered_date must use canonical YYYY-MM-DD form")
        return value


def _normalized_repository_path(value: str, *, label: str) -> str:
    path = PurePosixPath(value)
    portable_parts = all(
        not any(ord(char) < 32 or char in _WINDOWS_UNSAFE_PATH_CHARS for char in part)
        and not part.endswith((" ", "."))
        and part.split(".", 1)[0].upper() not in _WINDOWS_RESERVED_PATH_STEMS
        for part in path.parts
    )
    if (
        value != value.strip()
        or path.is_absolute()
        or "\\" in value
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
        or not portable_parts
    ):
        raise ValueError(
            f"{label} must be a normalized, cross-platform relative POSIX path"
        )
    return value
