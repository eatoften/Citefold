from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


SHA256_PATTERN = r"^[0-9a-f]{64}$"

ControllerTaskType = Literal["multi_hop", "prerequisite", "unanswerable"]
ControllerSplit = Literal["development", "test"]
Answerability = Literal["answerable", "unanswerable"]
ConceptRole = Literal["anchor", "target", "bridge", "prerequisite"]
ConceptMentionStatus = Literal["explicit", "implicit"]
ConceptNecessity = Literal["answer", "reasoning_only"]
EvidenceModality = Literal[
    "card_text",
    "transcript",
    "slide_text",
    "frame",
    "diagram",
]
RelationType = Literal[
    "prerequisite",
    "related",
    "causes",
    "enables",
    "depends_on",
    "part_of",
    "example_of",
    "contrast_with",
    "supports",
    "derives",
]
ModalityMode = Literal["text_only", "visual_only", "cross_modal_all", "either"]
DifficultyLevel = Literal["easy", "medium", "hard"]
LexicalOverlapBucket = Literal["low", "medium", "high"]
UnanswerableSubtype = Literal[
    "corpus_absent",
    "missing_bridge",
    "insufficient_evidence",
    "ambiguous",
    "conflicting_evidence",
]

TEXT_MODALITIES = frozenset({"card_text", "transcript", "slide_text"})
VISUAL_MODALITIES = frozenset({"frame", "diagram"})


def utc_now() -> datetime:
    return datetime.now(UTC)


def _normalized_nonempty(value: str) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError("Value cannot be blank.")
    return normalized


def _ensure_unique(values: list[str], label: str) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique.")
    return values


def _ensure_sha256_values(values: list[str], label: str) -> list[str]:
    _ensure_unique(values, label)
    for value in values:
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError(f"{label} must contain lowercase SHA-256 values.")
    return values


def _ensure_timezone_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamps must be timezone-aware.")
    return value


class StrictControllerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequiredConcept(StrictControllerModel):
    concept_id: str = Field(min_length=1)
    role: ConceptRole
    mention_status: ConceptMentionStatus
    necessity: ConceptNecessity

    @field_validator("concept_id")
    @classmethod
    def normalize_concept_id(cls, value: str) -> str:
        return _normalized_nonempty(value)


class ConceptRegistryEntry(StrictControllerModel):
    """Canonical semantic concept metadata frozen with the benchmark."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    concept_id: str = Field(min_length=1)
    canonical_name: str = Field(min_length=1)
    definition: str = Field(min_length=1)

    @field_validator("concept_id", "canonical_name", "definition")
    @classmethod
    def normalize_registry_text(cls, value: str) -> str:
        return _normalized_nonempty(value)


class EvidenceReference(StrictControllerModel):
    evidence_id: str = Field(min_length=1)
    card_id: str = Field(min_length=1)
    claim_id: str = Field(min_length=1)
    modality: EvidenceModality

    @field_validator("evidence_id", "card_id", "claim_id")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        return _normalized_nonempty(value)


class EvidenceAlternative(StrictControllerModel):
    """One conjunctive evidence set within a requirement's disjunction."""

    evidence: list[EvidenceReference] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_unique_evidence(self) -> "EvidenceAlternative":
        _ensure_unique(
            [reference.evidence_id for reference in self.evidence],
            "Evidence ids within an alternative",
        )
        return self


class EvidenceRequirement(StrictControllerModel):
    """All requirements are mandatory; any one alternative can satisfy a requirement."""

    requirement_id: str = Field(min_length=1)
    supports_concept_ids: list[str] = Field(min_length=1)
    alternatives: list[EvidenceAlternative] = Field(min_length=1, max_length=8)

    @field_validator("requirement_id")
    @classmethod
    def normalize_requirement_id(cls, value: str) -> str:
        return _normalized_nonempty(value)

    @field_validator("supports_concept_ids")
    @classmethod
    def validate_support_concepts(cls, values: list[str]) -> list[str]:
        normalized = [_normalized_nonempty(value) for value in values]
        return _ensure_unique(normalized, "Supported concept ids")

    @model_validator(mode="after")
    def validate_unique_alternatives(self) -> "EvidenceRequirement":
        signatures = [
            tuple(sorted(reference.evidence_id for reference in alternative.evidence))
            for alternative in self.alternatives
        ]
        if len(signatures) != len(set(signatures)):
            raise ValueError("Evidence alternatives must be semantically distinct.")
        return self


class ReasoningEdge(StrictControllerModel):
    source_concept_id: str = Field(min_length=1)
    target_concept_id: str = Field(min_length=1)
    relation_type: RelationType
    supporting_requirement_ids: list[str] = Field(min_length=1)

    @field_validator("source_concept_id", "target_concept_id")
    @classmethod
    def normalize_concept_identifier(cls, value: str) -> str:
        return _normalized_nonempty(value)

    @field_validator("supporting_requirement_ids")
    @classmethod
    def validate_requirement_ids(cls, values: list[str]) -> list[str]:
        normalized = [_normalized_nonempty(value) for value in values]
        return _ensure_unique(normalized, "Edge supporting requirement ids")

    @model_validator(mode="after")
    def validate_directed_edge(self) -> "ReasoningEdge":
        if self.source_concept_id == self.target_concept_id:
            raise ValueError("A directed reasoning edge cannot be a self edge.")
        return self


class ValidReasoningPath(StrictControllerModel):
    path_id: str = Field(min_length=1)
    concept_ids: list[str] = Field(min_length=2)
    edges: list[ReasoningEdge] = Field(min_length=1)
    covers_requirement_ids: list[str] = Field(min_length=1)

    @field_validator("path_id")
    @classmethod
    def normalize_path_id(cls, value: str) -> str:
        return _normalized_nonempty(value)

    @field_validator("concept_ids", "covers_requirement_ids")
    @classmethod
    def validate_identifier_lists(cls, values: list[str]) -> list[str]:
        normalized = [_normalized_nonempty(value) for value in values]
        return _ensure_unique(normalized, "Path identifiers")

    @model_validator(mode="after")
    def validate_connected_directed_path(self) -> "ValidReasoningPath":
        if len(self.edges) != len(self.concept_ids) - 1:
            raise ValueError("A path needs exactly one edge between consecutive concepts.")
        for index, edge in enumerate(self.edges):
            expected = (self.concept_ids[index], self.concept_ids[index + 1])
            actual = (edge.source_concept_id, edge.target_concept_id)
            if actual != expected:
                raise ValueError(
                    "Reasoning edges must follow the declared concept order and direction."
                )
        return self


class ModalityRequirement(StrictControllerModel):
    mode: ModalityMode
    required_modalities: list[EvidenceModality] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_mode(self) -> "ModalityRequirement":
        _ensure_unique(list(self.required_modalities), "Required modalities")
        modalities = set(self.required_modalities)
        if self.mode == "text_only" and not modalities.issubset(TEXT_MODALITIES):
            raise ValueError("text_only can contain only textual modalities.")
        if self.mode == "visual_only" and not modalities.issubset(VISUAL_MODALITIES):
            raise ValueError("visual_only can contain only visual modalities.")
        if self.mode == "cross_modal_all":
            if not modalities.intersection(TEXT_MODALITIES) or not modalities.intersection(
                VISUAL_MODALITIES
            ):
                raise ValueError(
                    "cross_modal_all requires at least one text and one visual modality."
                )
        if self.mode == "either" and len(modalities) < 2:
            raise ValueError("either requires at least two interchangeable modalities.")
        return self


class DifficultyProfile(StrictControllerModel):
    level: DifficultyLevel
    relation_hops: int = Field(ge=0, le=8)
    distinct_evidence_units: int = Field(ge=0, le=32)
    implicit_concept_count: int = Field(ge=0, le=16)
    cross_lecture: bool
    hard_negative_count: int = Field(ge=0, le=32)
    lexical_overlap_bucket: LexicalOverlapBucket


class NegativeSearchAudit(StrictControllerModel):
    keyword_queries: list[str] = Field(min_length=1)
    bm25_top_n: int = Field(ge=1)
    dense_top_n: int = Field(ge=1)
    manually_reviewed_evidence_ids: list[str] = Field(min_length=1)
    conclusion: Literal["no_complete_support_found"]

    @field_validator("keyword_queries")
    @classmethod
    def normalize_queries(cls, values: list[str]) -> list[str]:
        normalized = [_normalized_nonempty(value) for value in values]
        return _ensure_unique(normalized, "Negative-search queries")

    @field_validator("manually_reviewed_evidence_ids")
    @classmethod
    def validate_reviewed_evidence_ids(cls, values: list[str]) -> list[str]:
        normalized = [_normalized_nonempty(value) for value in values]
        return _ensure_unique(normalized, "Manually reviewed evidence ids")


class UnanswerableCertificate(StrictControllerModel):
    subtype: UnanswerableSubtype
    unresolved_information_need: str = Field(min_length=1)
    closest_supported_concept_ids: list[str] = Field(default_factory=list)
    partial_evidence: list[EvidenceReference] = Field(default_factory=list)
    negative_search_audit: NegativeSearchAudit

    @field_validator("unresolved_information_need")
    @classmethod
    def normalize_information_need(cls, value: str) -> str:
        return _normalized_nonempty(value)

    @field_validator("closest_supported_concept_ids")
    @classmethod
    def validate_closest_concepts(cls, values: list[str]) -> list[str]:
        normalized = [_normalized_nonempty(value) for value in values]
        return _ensure_unique(normalized, "Closest supported concept ids")

    @model_validator(mode="after")
    def validate_partial_evidence(self) -> "UnanswerableCertificate":
        _ensure_unique(
            [reference.evidence_id for reference in self.partial_evidence],
            "Partial evidence ids",
        )
        return self


class AuthoringProvenance(StrictControllerModel):
    author_ids: list[str] = Field(min_length=1)
    source_artifact_sha256s: list[str] = Field(min_length=1)
    selection_bases: list[
        Literal[
            "learning_objective",
            "independent_evidence_bundle",
            "curriculum_dependency",
            "intrinsic_missing_information",
        ]
    ] = Field(min_length=1)
    blind_to_runtime_graph: Literal[True] = True
    blind_to_retriever_outputs: Literal[True] = True
    used_system_outcomes_for_selection: Literal[False] = False

    @field_validator("author_ids")
    @classmethod
    def validate_author_ids(cls, values: list[str]) -> list[str]:
        normalized = [_normalized_nonempty(value) for value in values]
        return _ensure_unique(normalized, "Author ids")

    @field_validator("source_artifact_sha256s")
    @classmethod
    def validate_source_hashes(cls, values: list[str]) -> list[str]:
        return _ensure_sha256_values(values, "Source artifact hashes")

    @field_validator("selection_bases")
    @classmethod
    def validate_selection_bases(cls, values: list[str]) -> list[str]:
        return _ensure_unique(values, "Selection bases")


def minimum_evidence_units(requirements: list[EvidenceRequirement]) -> int:
    """Return the minimum number of distinct evidence units satisfying the DNF."""

    candidate_unions: set[frozenset[str]] = {frozenset()}
    for requirement in requirements:
        expanded = {
            existing
            | frozenset(reference.evidence_id for reference in alternative.evidence)
            for existing in candidate_unions
            for alternative in requirement.alternatives
        }
        # Superset states cannot improve the final minimum and are removed to
        # keep validation bounded for ordinary annotation bundles.
        candidate_unions = {
            candidate
            for candidate in expanded
            if not any(other < candidate for other in expanded)
        }
    return min((len(candidate) for candidate in candidate_unions), default=0)


def expected_difficulty_level(
    profile: DifficultyProfile,
    task_type: ControllerTaskType,
) -> DifficultyLevel:
    score = 0
    if profile.relation_hops >= 3:
        score += 2
    elif profile.relation_hops == 2:
        score += 1
    if profile.distinct_evidence_units >= 3:
        score += 2
    elif profile.distinct_evidence_units == 2:
        score += 1
    score += min(2, profile.implicit_concept_count)
    score += int(profile.cross_lecture)
    if profile.hard_negative_count >= 2:
        score += 2
    elif profile.hard_negative_count == 1:
        score += 1
    if task_type == "unanswerable":
        score += int(profile.lexical_overlap_bucket == "high")
    else:
        score += int(profile.lexical_overlap_bucket == "low")
    if score <= 1:
        return "easy"
    if score <= 4:
        return "medium"
    return "hard"


class ControllerBenchmarkItem(StrictControllerModel):
    question_id: str = Field(min_length=1)
    question_family_id: str = Field(min_length=1)
    learning_objective_cluster_id: str = Field(min_length=1)
    source_evidence_bundle_id: str = Field(min_length=1)
    split: ControllerSplit
    task_type: ControllerTaskType
    question: str = Field(min_length=5)
    answerability: Answerability
    reference_answers: list[str] = Field(default_factory=list)
    required_concepts: list[RequiredConcept] = Field(default_factory=list)
    evidence_requirements: list[EvidenceRequirement] = Field(default_factory=list)
    valid_reasoning_paths: list[ValidReasoningPath] = Field(default_factory=list)
    modality_requirement: ModalityRequirement
    difficulty: DifficultyProfile
    hard_negatives: list[EvidenceReference] = Field(default_factory=list)
    unanswerable_certificate: UnanswerableCertificate | None = None
    authoring_provenance: AuthoringProvenance
    review_status: Literal["pending", "reviewed", "adjudicated"] = "pending"

    @field_validator(
        "question_id",
        "question_family_id",
        "learning_objective_cluster_id",
        "source_evidence_bundle_id",
        "question",
    )
    @classmethod
    def normalize_text_fields(cls, value: str) -> str:
        return _normalized_nonempty(value)

    @field_validator("reference_answers")
    @classmethod
    def normalize_reference_answers(cls, values: list[str]) -> list[str]:
        normalized = [_normalized_nonempty(value) for value in values]
        return _ensure_unique(normalized, "Reference answers")

    @model_validator(mode="after")
    def validate_ground_truth(self) -> "ControllerBenchmarkItem":
        concept_ids = [concept.concept_id for concept in self.required_concepts]
        _ensure_unique(concept_ids, "Required concept ids")
        requirement_ids = [
            requirement.requirement_id for requirement in self.evidence_requirements
        ]
        _ensure_unique(requirement_ids, "Evidence requirement ids")
        path_ids = [path.path_id for path in self.valid_reasoning_paths]
        _ensure_unique(path_ids, "Valid path ids")
        hard_negative_ids = [
            reference.evidence_id for reference in self.hard_negatives
        ]
        _ensure_unique(hard_negative_ids, "Hard-negative evidence ids")

        is_unanswerable = self.task_type == "unanswerable"
        if is_unanswerable != (self.answerability == "unanswerable"):
            raise ValueError(
                "The unanswerable task type and answerability label must agree."
            )

        if is_unanswerable:
            self._validate_unanswerable_shape()
        else:
            self._validate_answerable_shape(
                concept_ids=concept_ids,
                requirement_ids=requirement_ids,
            )

        gold_evidence = {
            reference.evidence_id
            for requirement in self.evidence_requirements
            for alternative in requirement.alternatives
            for reference in alternative.evidence
        }
        overlap = gold_evidence.intersection(hard_negative_ids)
        if overlap:
            raise ValueError(
                f"Hard negatives cannot be gold evidence: {sorted(overlap)}"
            )

        self._validate_modality()
        self._validate_difficulty()
        return self

    def _validate_unanswerable_shape(self) -> None:
        if self.reference_answers:
            raise ValueError("Unanswerable items cannot have reference answers.")
        if self.required_concepts:
            raise ValueError("Unanswerable items cannot claim supported required concepts.")
        if self.evidence_requirements or self.valid_reasoning_paths:
            raise ValueError(
                "Unanswerable items cannot have complete evidence or valid paths."
            )
        if self.unanswerable_certificate is None:
            raise ValueError("Unanswerable items need a failure certificate.")
        if (
            self.unanswerable_certificate.subtype != "corpus_absent"
            and not self.hard_negatives
        ):
            raise ValueError(
                "In-domain unanswerable items need at least one hard negative."
            )

    def _validate_answerable_shape(
        self,
        *,
        concept_ids: list[str],
        requirement_ids: list[str],
    ) -> None:
        if self.answerability != "answerable":
            raise ValueError("Multi-hop and prerequisite items must be answerable.")
        if not self.reference_answers:
            raise ValueError("Answerable items need at least one reference answer.")
        if not concept_ids or not requirement_ids or not self.valid_reasoning_paths:
            raise ValueError(
                "Answerable items need concepts, evidence requirements, and paths."
            )
        if self.unanswerable_certificate is not None:
            raise ValueError("Answerable items cannot have an unanswerable certificate.")

        concepts = set(concept_ids)
        requirements = set(requirement_ids)
        roles = {concept.role for concept in self.required_concepts}
        if "anchor" not in roles or "target" not in roles:
            raise ValueError("Answerable items need anchor and target concepts.")

        supported_concepts: set[str] = set()
        for requirement in self.evidence_requirements:
            unknown = set(requirement.supports_concept_ids) - concepts
            if unknown:
                raise ValueError(
                    f"Evidence requirement references unknown concepts: {sorted(unknown)}"
                )
            supported_concepts.update(requirement.supports_concept_ids)
        missing_support = concepts - supported_concepts
        if missing_support:
            raise ValueError(
                f"Every required concept needs evidence support: {sorted(missing_support)}"
            )

        for path in self.valid_reasoning_paths:
            if set(path.concept_ids) != concepts:
                raise ValueError(
                    "Every complete valid path must contain all required concepts."
                )
            roles_by_id = {
                concept.concept_id: concept.role
                for concept in self.required_concepts
            }
            ordered_roles = [
                roles_by_id[concept_id] for concept_id in path.concept_ids
            ]
            if ordered_roles[0] != "anchor" or ordered_roles[-1] != "target":
                raise ValueError(
                    "Every reasoning path must run from its anchor to its target."
                )
            if any(
                role not in {"bridge", "prerequisite"}
                for role in ordered_roles[1:-1]
            ):
                raise ValueError(
                    "Reasoning-path interior concepts must be bridge or "
                    "prerequisite roles."
                )
            if set(path.covers_requirement_ids) != requirements:
                raise ValueError(
                    "Every complete valid path must cover every evidence requirement."
                )
            for edge in path.edges:
                unknown = set(edge.supporting_requirement_ids) - requirements
                if unknown:
                    raise ValueError(
                        f"Reasoning edge references unknown requirements: {sorted(unknown)}"
                    )

        if self.task_type == "multi_hop":
            if min(len(path.edges) for path in self.valid_reasoning_paths) < 2:
                raise ValueError(
                    "A true multi-hop item requires at least two directed relation edges."
                )
        elif self.task_type == "prerequisite":
            prerequisite_ids = {
                concept.concept_id
                for concept in self.required_concepts
                if concept.role == "prerequisite"
                and concept.mention_status == "implicit"
            }
            if not prerequisite_ids:
                raise ValueError(
                    "A prerequisite item needs an implicit prerequisite concept."
                )
            if not all(
                any(
                    edge.relation_type == "prerequisite"
                    and edge.source_concept_id in prerequisite_ids
                    for edge in path.edges
                )
                for path in self.valid_reasoning_paths
            ):
                raise ValueError(
                    "Every prerequisite path must direct an implicit prerequisite "
                    "toward its dependent concept."
                )

    def _validate_modality(self) -> None:
        if self.answerability == "unanswerable":
            return
        all_references = [
            reference
            for requirement in self.evidence_requirements
            for alternative in requirement.alternatives
            for reference in alternative.evidence
        ]
        observed = {reference.modality for reference in all_references}
        required = set(self.modality_requirement.required_modalities)
        missing = required - observed
        if missing:
            raise ValueError(
                f"Declared modalities are absent from gold evidence: {sorted(missing)}"
            )
        if self.modality_requirement.mode == "text_only" and not observed.issubset(
            TEXT_MODALITIES
        ):
            raise ValueError("text_only items cannot include visual gold evidence.")
        if self.modality_requirement.mode == "visual_only" and not observed.issubset(
            VISUAL_MODALITIES
        ):
            raise ValueError("visual_only items cannot include textual gold evidence.")
        if self.modality_requirement.mode == "cross_modal_all":
            # This conservative rule guarantees every DNF completion carries each
            # mandatory modality without enumerating an exponential Cartesian product.
            for modality in required:
                modality_is_mandatory = any(
                    all(
                        modality
                        in {
                            reference.modality
                            for reference in alternative.evidence
                        }
                        for alternative in requirement.alternatives
                    )
                    for requirement in self.evidence_requirements
                )
                if not modality_is_mandatory:
                    raise ValueError(
                        "Every cross_modal_all modality must be mandatory in at least "
                        "one evidence requirement."
                    )

    def _validate_difficulty(self) -> None:
        expected_hops = (
            min(len(path.edges) for path in self.valid_reasoning_paths)
            if self.valid_reasoning_paths
            else 0
        )
        expected_evidence = minimum_evidence_units(self.evidence_requirements)
        expected_implicit = sum(
            concept.mention_status == "implicit"
            for concept in self.required_concepts
        )
        expected_hard_negatives = len(self.hard_negatives)
        observed_axes = (
            self.difficulty.relation_hops,
            self.difficulty.distinct_evidence_units,
            self.difficulty.implicit_concept_count,
            self.difficulty.hard_negative_count,
        )
        expected_axes = (
            expected_hops,
            expected_evidence,
            expected_implicit,
            expected_hard_negatives,
        )
        if observed_axes != expected_axes:
            raise ValueError(
                "Difficulty axes do not match the annotated ground truth: "
                f"expected {expected_axes}, got {observed_axes}."
            )
        expected_level = expected_difficulty_level(self.difficulty, self.task_type)
        if self.difficulty.level != expected_level:
            raise ValueError(
                f"Difficulty level must be deterministically derived as {expected_level}."
            )


class ControllerBenchmarkDataset(StrictControllerModel):
    schema_version: Literal["2.0"] = "2.0"
    benchmark_id: str = Field(min_length=1)
    corpus_sha256: str = Field(pattern=SHA256_PATTERN)
    concept_registry_sha256: str = Field(pattern=SHA256_PATTERN)
    evidence_catalog_sha256: str = Field(pattern=SHA256_PATTERN)
    annotation_protocol_sha256: str = Field(pattern=SHA256_PATTERN)
    split_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    created_at: datetime = Field(default_factory=utc_now)
    lifecycle_status: Literal[
        "draft",
        "independently_annotated",
        "double_reviewed",
        "adjudicated",
        "sealed",
        "opened",
    ] = "draft"
    dataset_sha256: str = Field(pattern=SHA256_PATTERN)
    concept_registry: list[ConceptRegistryEntry] = Field(min_length=1)
    evidence_catalog: list[EvidenceReference] = Field(min_length=1)
    items: list[ControllerBenchmarkItem] = Field(min_length=1)

    @field_validator("benchmark_id")
    @classmethod
    def normalize_benchmark_id(cls, value: str) -> str:
        return _normalized_nonempty(value)

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return _ensure_timezone_aware(value)

    @model_validator(mode="after")
    def validate_unique_items(self) -> "ControllerBenchmarkDataset":
        _ensure_unique(
            [entry.concept_id for entry in self.concept_registry],
            "Concept-registry ids",
        )
        _ensure_unique(
            [entry.evidence_id for entry in self.evidence_catalog],
            "Evidence-catalog ids",
        )
        _ensure_unique(
            [item.question_id for item in self.items],
            "Question ids",
        )
        normalized_questions = [
            " ".join(item.question.lower().split()) for item in self.items
        ]
        _ensure_unique(normalized_questions, "Normalized questions")
        return self


class ReviewChecks(StrictControllerModel):
    question_clarity: bool
    answerability: bool
    required_concepts: bool
    evidence_or_failure_certificate: bool
    reasoning_paths: bool
    modality: bool
    difficulty: bool
    hard_negatives: bool

    def all_passed(self) -> bool:
        return all(self.model_dump().values())


class ItemReviewDecision(StrictControllerModel):
    question_id: str = Field(min_length=1)
    reviewer_id: str = Field(min_length=1)
    checks: ReviewChecks
    overall_decision: Literal["accept", "reject"]
    notes: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_overall_decision(self) -> "ItemReviewDecision":
        expected = "accept" if self.checks.all_passed() else "reject"
        if self.overall_decision != expected:
            raise ValueError(
                "Overall review decision must agree with all field-level checks."
            )
        return self


class ItemAdjudication(StrictControllerModel):
    question_id: str = Field(min_length=1)
    adjudicator_id: str = Field(min_length=1)
    final_checks: ReviewChecks
    final_decision: Literal["accept", "reject"]
    notes: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_final_decision(self) -> "ItemAdjudication":
        expected = "accept" if self.final_checks.all_passed() else "reject"
        if self.final_decision != expected:
            raise ValueError(
                "Final adjudication must agree with all field-level checks."
            )
        return self


class ControllerBenchmarkReview(StrictControllerModel):
    schema_version: Literal["2.0"] = "2.0"
    review_id: str = Field(min_length=1)
    benchmark_sha256: str = Field(pattern=SHA256_PATTERN)
    created_at: datetime = Field(default_factory=utc_now)
    review_status: Literal[
        "candidate",
        "double_reviewed",
        "adjudicated",
        "human_verified",
    ] = "candidate"
    decisions: list[ItemReviewDecision] = Field(min_length=1)
    adjudications: list[ItemAdjudication] = Field(default_factory=list)
    review_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return _ensure_timezone_aware(value)

    @model_validator(mode="after")
    def validate_unique_review_records(self) -> "ControllerBenchmarkReview":
        pairs = [
            (decision.question_id, decision.reviewer_id)
            for decision in self.decisions
        ]
        if len(pairs) != len(set(pairs)):
            raise ValueError("A reviewer can decide each item only once.")
        _ensure_unique(
            [adjudication.question_id for adjudication in self.adjudications],
            "Adjudicated question ids",
        )
        return self


class QuestionInputArtifact(StrictControllerModel):
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    artifact_type: Literal[
        "corpus_snapshot",
        "concept_registry",
        "evidence_catalog",
        "annotation_protocol",
        "curriculum_outline",
        "independent_evidence_bundle",
    ]
    parent_artifact_sha256s: list[str] = Field(default_factory=list)

    @field_validator("parent_artifact_sha256s")
    @classmethod
    def validate_parent_hashes(cls, values: list[str]) -> list[str]:
        return _ensure_sha256_values(values, "Parent artifact hashes")

    @model_validator(mode="after")
    def validate_no_self_parent(self) -> "QuestionInputArtifact":
        if self.artifact_sha256 in self.parent_artifact_sha256s:
            raise ValueError("A question input artifact cannot derive from itself.")
        return self


class GraphIndependenceManifest(StrictControllerModel):
    schema_version: Literal["2.0"] = "2.0"
    manifest_id: str = Field(min_length=1)
    runtime_graph_sha256: str = Field(pattern=SHA256_PATTERN)
    graph_frozen_at: datetime
    benchmark_authoring_started_at: datetime
    created_at: datetime = Field(default_factory=utc_now)
    graph_reviewer_ids: list[str] = Field(min_length=1)
    question_author_ids: list[str] = Field(min_length=1)
    benchmark_reviewer_ids: list[str] = Field(min_length=2)
    adjudicator_ids: list[str] = Field(min_length=1)
    question_inputs: list[QuestionInputArtifact] = Field(min_length=4)
    question_authors_blind_to_runtime_graph: Literal[True] = True
    reviewers_blind_to_runtime_graph: Literal[True] = True
    selection_used_system_outcomes: Literal[False] = False
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator(
        "graph_reviewer_ids",
        "question_author_ids",
        "benchmark_reviewer_ids",
        "adjudicator_ids",
    )
    @classmethod
    def validate_role_ids(cls, values: list[str]) -> list[str]:
        normalized = [_normalized_nonempty(value) for value in values]
        return _ensure_unique(normalized, "Role ids")

    @field_validator(
        "graph_frozen_at",
        "benchmark_authoring_started_at",
        "created_at",
    )
    @classmethod
    def validate_timestamps(cls, value: datetime) -> datetime:
        return _ensure_timezone_aware(value)

    @model_validator(mode="after")
    def validate_unique_question_inputs(self) -> "GraphIndependenceManifest":
        _ensure_unique(
            [artifact.artifact_sha256 for artifact in self.question_inputs],
            "Question input artifact hashes",
        )
        return self


class SplitAssignment(StrictControllerModel):
    question_id: str = Field(min_length=1)
    split: ControllerSplit
    question_family_id: str = Field(min_length=1)
    learning_objective_cluster_id: str = Field(min_length=1)
    source_evidence_bundle_id: str = Field(min_length=1)

    @field_validator(
        "question_id",
        "question_family_id",
        "learning_objective_cluster_id",
        "source_evidence_bundle_id",
    )
    @classmethod
    def normalize_assignment_ids(cls, value: str) -> str:
        return _normalized_nonempty(value)


class ControllerBenchmarkSplitManifest(StrictControllerModel):
    schema_version: Literal["2.0"] = "2.0"
    manifest_id: str = Field(min_length=1)
    benchmark_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    assignments: list[SplitAssignment] = Field(min_length=1)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("manifest_id", "benchmark_id")
    @classmethod
    def normalize_manifest_ids(cls, value: str) -> str:
        return _normalized_nonempty(value)

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return _ensure_timezone_aware(value)

    @model_validator(mode="after")
    def validate_unique_assignments(self) -> "ControllerBenchmarkSplitManifest":
        _ensure_unique(
            [assignment.question_id for assignment in self.assignments],
            "Split-manifest question ids",
        )
        return self


class ControllerBenchmarkSeal(StrictControllerModel):
    schema_version: Literal["2.0"] = "2.0"
    seal_id: str = Field(min_length=1)
    benchmark_sha256: str = Field(pattern=SHA256_PATTERN)
    review_sha256: str = Field(pattern=SHA256_PATTERN)
    independence_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    split_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    sealed_at: datetime
    status: Literal["sealed"]
    seal_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("sealed_at")
    @classmethod
    def validate_sealed_at(cls, value: datetime) -> datetime:
        return _ensure_timezone_aware(value)
