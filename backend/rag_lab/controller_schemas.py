from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    TypeAdapter,
    model_validator,
)

from .io import sha256_value


ControllerActionType = Literal[
    "search_concept",
    "search_evidence",
    "expand_typed_neighbor",
    "verify_support",
    "answer",
    "abstain",
]
ControllerNeedType = Literal[
    "concept",
    "prerequisite",
    "relation",
    "evidence",
]
ControllerNeedStatus = Literal[
    "unresolved",
    "partially_supported",
    "supported",
    "contradicted",
    "unresolvable",
]
ControllerRelationType = Literal[
    "prerequisite",
    "related",
    "example_of",
    "contrast_with",
    "part_of",
]
ControllerRelationDirection = Literal["incoming", "outgoing", "both"]
ControllerAbstainReason = Literal[
    "insufficient_evidence",
    "contradictory_evidence",
    "budget_exhausted",
    "no_progress",
    "invalid_policy_output",
]
ControllerStopReason = Literal[
    "answer",
    "abstain",
    "budget_exhausted",
    "max_steps",
    "no_progress",
    "invalid_action",
    "policy_error",
    "environment_error",
]
ControllerTraceStatus = Literal["completed", "forced_abstain", "failed"]
ControllerBudgetField = Literal[
    "steps",
    "retrieval_calls",
    "concept_searches",
    "evidence_searches",
    "graph_expansions",
    "verifications",
    "unique_concepts",
    "unique_evidence",
    "context_characters",
    "prompt_characters",
    "completion_tokens",
    "elapsed_milliseconds",
]
ControllerEvidenceModality = Literal[
    "transcript",
    "slide_text",
    "slide_image",
    "video_frame",
    "document",
    "equation",
    "code",
]

RETRIEVAL_ACTION_TYPES: frozenset[ControllerActionType] = frozenset(
    {
        "search_concept",
        "search_evidence",
        "expand_typed_neighbor",
    }
)
TERMINAL_ACTION_TYPES: frozenset[ControllerActionType] = frozenset(
    {"answer", "abstain"}
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _unique(values: list[str], *, label: str) -> list[str]:
    cleaned = [value.strip() for value in values]
    if any(not value for value in cleaned):
        raise ValueError(f"{label} cannot contain blank values.")
    if len(cleaned) != len(set(cleaned)):
        raise ValueError(f"{label} must be unique.")
    return cleaned


def _normalized_text(value: str) -> str:
    return " ".join(value.strip().split())


class ControllerModel(BaseModel):
    """Closed research contract: schema drift must fail loudly."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ControllerBudget(ControllerModel):
    max_steps: int = Field(default=8, ge=1)
    max_retrieval_calls: int = Field(default=5, ge=0)
    max_concept_searches: int = Field(default=2, ge=0)
    max_evidence_searches: int = Field(default=2, ge=0)
    max_graph_expansions: int = Field(default=2, ge=0)
    max_verifications: int = Field(default=2, ge=0)
    max_unique_concepts: int = Field(default=12, ge=0)
    max_unique_evidence: int = Field(default=30, ge=0)
    max_context_characters: int = Field(default=12_000, ge=0)
    max_prompt_characters: int = Field(default=60_000, ge=0)
    max_completion_tokens: int | None = Field(default=None, ge=0)
    max_elapsed_milliseconds: float | None = Field(default=120_000, ge=0)
    max_consecutive_no_progress: int = Field(default=2, ge=1)
    max_top_k: int = Field(default=10, ge=1, le=100)
    max_anchors_per_expansion: int = Field(default=5, ge=1, le=100)

    @model_validator(mode="after")
    def validate_internal_limits(self) -> "ControllerBudget":
        if self.max_retrieval_calls > (
            self.max_concept_searches
            + self.max_evidence_searches
            + self.max_graph_expansions
        ):
            raise ValueError(
                "max_retrieval_calls cannot exceed the sum of retrieval-action "
                "limits."
            )
        return self


class ControllerCost(ControllerModel):
    steps: int = Field(default=0, ge=0)
    retrieval_calls: int = Field(default=0, ge=0)
    concept_searches: int = Field(default=0, ge=0)
    evidence_searches: int = Field(default=0, ge=0)
    graph_expansions: int = Field(default=0, ge=0)
    verifications: int = Field(default=0, ge=0)
    unique_concepts: int = Field(default=0, ge=0)
    unique_evidence: int = Field(default=0, ge=0)
    context_characters: int = Field(default=0, ge=0)
    prompt_characters: int = Field(default=0, ge=0)
    completion_tokens: int | None = Field(default=0, ge=0)
    elapsed_milliseconds: float = Field(default=0, ge=0)

    def plus(self, other: "ControllerCost") -> "ControllerCost":
        if self.completion_tokens is None or other.completion_tokens is None:
            completion_tokens = None
        else:
            completion_tokens = (
                self.completion_tokens + other.completion_tokens
            )
        return ControllerCost(
            steps=self.steps + other.steps,
            retrieval_calls=self.retrieval_calls + other.retrieval_calls,
            concept_searches=(
                self.concept_searches + other.concept_searches
            ),
            evidence_searches=(
                self.evidence_searches + other.evidence_searches
            ),
            graph_expansions=(
                self.graph_expansions + other.graph_expansions
            ),
            verifications=self.verifications + other.verifications,
            unique_concepts=self.unique_concepts + other.unique_concepts,
            unique_evidence=self.unique_evidence + other.unique_evidence,
            context_characters=(
                self.context_characters + other.context_characters
            ),
            prompt_characters=(
                self.prompt_characters + other.prompt_characters
            ),
            completion_tokens=completion_tokens,
            elapsed_milliseconds=(
                self.elapsed_milliseconds + other.elapsed_milliseconds
            ),
        )

    def exceeded_limits(
        self,
        budget: ControllerBudget,
    ) -> list[ControllerBudgetField]:
        exceeded: list[ControllerBudgetField] = []
        pairs = (
            ("steps", budget.max_steps),
            ("retrieval_calls", budget.max_retrieval_calls),
            ("concept_searches", budget.max_concept_searches),
            ("evidence_searches", budget.max_evidence_searches),
            ("graph_expansions", budget.max_graph_expansions),
            ("verifications", budget.max_verifications),
            ("unique_concepts", budget.max_unique_concepts),
            ("unique_evidence", budget.max_unique_evidence),
            ("context_characters", budget.max_context_characters),
            ("prompt_characters", budget.max_prompt_characters),
        )
        for field_name, limit in pairs:
            if getattr(self, field_name) > limit:
                exceeded.append(field_name)
        if (
            budget.max_completion_tokens is not None
            and self.completion_tokens is not None
            and self.completion_tokens > budget.max_completion_tokens
        ):
            exceeded.append("completion_tokens")
        if (
            budget.max_elapsed_milliseconds is not None
            and self.elapsed_milliseconds
            > budget.max_elapsed_milliseconds
        ):
            exceeded.append("elapsed_milliseconds")
        return exceeded


class ControllerKnowledgeNeed(ControllerModel):
    need_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    need_type: ControllerNeedType = "concept"
    required: bool = True
    status: ControllerNeedStatus = "unresolved"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    support_concept_ids: list[str] = Field(default_factory=list)
    support_evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_support(self) -> "ControllerKnowledgeNeed":
        self.need_id = self.need_id.strip()
        self.description = _normalized_text(self.description)
        self.support_concept_ids = _unique(
            self.support_concept_ids,
            label="Knowledge-need concept ids",
        )
        self.support_evidence_ids = _unique(
            self.support_evidence_ids,
            label="Knowledge-need evidence ids",
        )
        if self.status == "supported" and not self.support_evidence_ids:
            raise ValueError(
                "A supported knowledge need requires supporting evidence."
            )
        if self.status == "unresolved" and (
            self.support_concept_ids or self.support_evidence_ids
        ):
            raise ValueError(
                "An unresolved knowledge need cannot carry support ids."
            )
        return self


class ControllerState(ControllerModel):
    question_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    knowledge_needs: list[ControllerKnowledgeNeed] = Field(min_length=1)
    retrieved_concept_ids: list[str] = Field(default_factory=list)
    retrieved_evidence_ids: list[str] = Field(default_factory=list)
    verified_evidence_ids: list[str] = Field(default_factory=list)
    traversed_relation_ids: list[str] = Field(default_factory=list)
    attempted_action_fingerprints: list[str] = Field(default_factory=list)
    answerability_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    consecutive_no_progress: int = Field(default=0, ge=0)
    step_index: int = Field(default=0, ge=0)
    cost: ControllerCost = Field(default_factory=ControllerCost)

    @model_validator(mode="after")
    def validate_state(self) -> "ControllerState":
        self.question_id = self.question_id.strip()
        self.question = _normalized_text(self.question)
        for field_name, label in (
            ("retrieved_concept_ids", "Retrieved concept ids"),
            ("retrieved_evidence_ids", "Retrieved evidence ids"),
            ("verified_evidence_ids", "Verified evidence ids"),
            ("traversed_relation_ids", "Traversed relation ids"),
            (
                "attempted_action_fingerprints",
                "Attempted action fingerprints",
            ),
        ):
            setattr(
                self,
                field_name,
                _unique(getattr(self, field_name), label=label),
            )
        need_ids = [need.need_id for need in self.knowledge_needs]
        if len(need_ids) != len(set(need_ids)):
            raise ValueError("Knowledge-need ids must be unique.")
        known_concepts = set(self.retrieved_concept_ids)
        known_evidence = set(self.retrieved_evidence_ids)
        if not set(self.verified_evidence_ids).issubset(known_evidence):
            raise ValueError("Verified evidence must have been retrieved.")
        for need in self.knowledge_needs:
            if not set(need.support_concept_ids).issubset(known_concepts):
                raise ValueError(
                    f"Knowledge need {need.need_id} cites an unseen concept."
                )
            if not set(need.support_evidence_ids).issubset(known_evidence):
                raise ValueError(
                    f"Knowledge need {need.need_id} cites unseen evidence."
                )
            if need.support_evidence_ids and not set(
                need.support_evidence_ids
            ).issubset(self.verified_evidence_ids):
                raise ValueError(
                    f"Knowledge need {need.need_id} uses unverified evidence."
                )
        if self.cost.steps != self.step_index:
            raise ValueError("State cost.steps must equal step_index.")
        if self.cost.unique_concepts != len(self.retrieved_concept_ids):
            raise ValueError(
                "State unique-concept cost must match retrieved concepts."
            )
        if self.cost.unique_evidence != len(self.retrieved_evidence_ids):
            raise ValueError(
                "State unique-evidence cost must match retrieved evidence."
            )
        return self


class SearchConceptAction(ControllerModel):
    action_type: Literal["search_concept"] = "search_concept"
    need_id: str = Field(min_length=1)
    query: str = Field(min_length=1, max_length=2_000)
    top_k: int = Field(ge=1, le=100)
    exclude_concept_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize(self) -> "SearchConceptAction":
        self.need_id = self.need_id.strip()
        self.query = _normalized_text(self.query)
        self.exclude_concept_ids = _unique(
            self.exclude_concept_ids,
            label="Excluded concept ids",
        )
        return self


class SearchEvidenceAction(ControllerModel):
    action_type: Literal["search_evidence"] = "search_evidence"
    need_ids: list[str] = Field(min_length=1)
    query: str = Field(min_length=1, max_length=2_000)
    scope_concept_ids: list[str] = Field(default_factory=list)
    top_k: int = Field(ge=1, le=100)
    exclude_evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize(self) -> "SearchEvidenceAction":
        self.need_ids = _unique(self.need_ids, label="Evidence-search need ids")
        self.query = _normalized_text(self.query)
        self.scope_concept_ids = _unique(
            self.scope_concept_ids,
            label="Evidence-search concept scope",
        )
        self.exclude_evidence_ids = _unique(
            self.exclude_evidence_ids,
            label="Excluded evidence ids",
        )
        return self


class ExpandTypedNeighborAction(ControllerModel):
    action_type: Literal["expand_typed_neighbor"] = "expand_typed_neighbor"
    need_id: str = Field(min_length=1)
    anchor_concept_ids: list[str] = Field(min_length=1)
    relation_types: list[ControllerRelationType] = Field(min_length=1)
    direction: ControllerRelationDirection
    max_neighbors_per_anchor: int = Field(ge=1, le=100)
    exclude_relation_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize(self) -> "ExpandTypedNeighborAction":
        self.need_id = self.need_id.strip()
        self.anchor_concept_ids = _unique(
            self.anchor_concept_ids,
            label="Graph-expansion anchor concepts",
        )
        if len(self.relation_types) != len(set(self.relation_types)):
            raise ValueError("Graph-expansion relation types must be unique.")
        self.exclude_relation_ids = _unique(
            self.exclude_relation_ids,
            label="Excluded relation ids",
        )
        return self


class VerifySupportAction(ControllerModel):
    action_type: Literal["verify_support"] = "verify_support"
    need_ids: list[str] = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def normalize(self) -> "VerifySupportAction":
        self.need_ids = _unique(self.need_ids, label="Verification need ids")
        self.evidence_ids = _unique(
            self.evidence_ids,
            label="Verification evidence ids",
        )
        return self


class AnswerAction(ControllerModel):
    action_type: Literal["answer"] = "answer"
    supported_need_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def normalize(self) -> "AnswerAction":
        self.supported_need_ids = _unique(
            self.supported_need_ids,
            label="Answer supported-need ids",
        )
        return self


class AbstainAction(ControllerModel):
    action_type: Literal["abstain"] = "abstain"
    reason_code: ControllerAbstainReason


ControllerAction: TypeAlias = Annotated[
    SearchConceptAction
    | SearchEvidenceAction
    | ExpandTypedNeighborAction
    | VerifySupportAction
    | AnswerAction
    | AbstainAction,
    Field(discriminator="action_type"),
]
CONTROLLER_ACTION_ADAPTER = TypeAdapter(ControllerAction)


def action_fingerprint(action: ControllerAction) -> str:
    payload = action.model_dump(mode="json")
    for key in (
        "need_ids",
        "supported_need_ids",
        "exclude_concept_ids",
        "scope_concept_ids",
        "exclude_evidence_ids",
        "anchor_concept_ids",
        "relation_types",
        "exclude_relation_ids",
        "evidence_ids",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            payload[key] = sorted(value)
    if "query" in payload:
        payload["query"] = _normalized_text(payload["query"]).lower()
    return sha256_value(payload)


class ControllerConceptNode(ControllerModel):
    concept_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    document_text: str = Field(min_length=1)
    source_card_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_source_ids(self) -> "ControllerConceptNode":
        self.source_card_ids = _unique(
            self.source_card_ids,
            label="Concept source-card ids",
        )
        return self


class ControllerEvidenceNode(ControllerModel):
    evidence_id: str = Field(min_length=1)
    concept_id: str = Field(min_length=1)
    claim_id: str = Field(min_length=1)
    claim_text: str = Field(min_length=1)
    text: str = Field(min_length=1)
    modality: ControllerEvidenceModality = "transcript"
    source_job_id: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    locator: dict[str, object] = Field(default_factory=dict)
    extraction_method: str = Field(default="product_snapshot", min_length=1)
    extraction_version: str = Field(default="1.0", min_length=1)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class ControllerRelationEdge(ControllerModel):
    relation_id: str = Field(min_length=1)
    source_concept_id: str = Field(min_length=1)
    target_concept_id: str = Field(min_length=1)
    relation_type: ControllerRelationType
    score: float = Field(ge=-1.0, le=1.0)
    review_status: Literal["candidate", "human_verified"] = "candidate"

    @model_validator(mode="after")
    def validate_distinct_concepts(self) -> "ControllerRelationEdge":
        if self.source_concept_id == self.target_concept_id:
            raise ValueError("A controller relation cannot be a self edge.")
        return self


class ControllerRetrievalConfig(ControllerModel):
    dense_method: Literal["cosine_similarity"] = "cosine_similarity"
    dense_ranking_version: Literal["rank_dense_v1"] = "rank_dense_v1"
    evidence_method: Literal["bm25"] = "bm25"
    evidence_bm25_k1: float = Field(default=1.2, gt=0)
    evidence_bm25_b: float = Field(default=0.75, ge=0, le=1)
    tokenizer_version: Literal["ascii_alnum_apostrophe_hyphen_v1"] = (
        "ascii_alnum_apostrophe_hyphen_v1"
    )
    tie_break: Literal["score_desc_id_asc"] = "score_desc_id_asc"


class ControllerMemorySnapshot(ControllerModel):
    schema_version: Literal["1.0"] = "1.0"
    memory_id: str = Field(min_length=1)
    concept_granularity: Literal["card_proxy", "concept_node"] = "card_proxy"
    corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    retrieval_config: ControllerRetrievalConfig = Field(
        default_factory=ControllerRetrievalConfig
    )
    created_at: datetime = Field(default_factory=utc_now)
    concepts: list[ControllerConceptNode] = Field(min_length=1)
    evidence: list[ControllerEvidenceNode] = Field(min_length=1)
    relations: list[ControllerRelationEdge] = Field(default_factory=list)
    memory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_memory(self) -> "ControllerMemorySnapshot":
        concept_ids = [concept.concept_id for concept in self.concepts]
        evidence_ids = [item.evidence_id for item in self.evidence]
        relation_ids = [relation.relation_id for relation in self.relations]
        for label, values in (
            ("Concept", concept_ids),
            ("Evidence", evidence_ids),
            ("Relation", relation_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} ids must be unique.")
        known_concepts = set(concept_ids)
        for item in self.evidence:
            if item.concept_id not in known_concepts:
                raise ValueError(
                    f"Evidence references an unknown concept: "
                    f"{item.evidence_id}."
                )
        for relation in self.relations:
            if (
                relation.source_concept_id not in known_concepts
                or relation.target_concept_id not in known_concepts
            ):
                raise ValueError(
                    f"Relation references an unknown concept: "
                    f"{relation.relation_id}."
                )
        return self


class ControllerConceptHit(ControllerModel):
    concept_id: str = Field(min_length=1)
    score: float
    rank: int = Field(ge=1)
    retrieval_source: str = Field(min_length=1)


class ControllerEvidenceHit(ControllerModel):
    evidence_id: str = Field(min_length=1)
    concept_id: str = Field(min_length=1)
    claim_id: str = Field(min_length=1)
    score: float
    rank: int = Field(ge=1)
    retrieval_source: str = Field(min_length=1)


class ControllerRelationHit(ControllerModel):
    relation_id: str = Field(min_length=1)
    source_concept_id: str = Field(min_length=1)
    target_concept_id: str = Field(min_length=1)
    relation_type: ControllerRelationType
    traversal_direction: Literal["incoming", "outgoing"]
    score: float
    rank: int = Field(ge=1)


class ControllerVerificationResult(ControllerModel):
    need_id: str = Field(min_length=1)
    status: ControllerNeedStatus
    support_concept_ids: list[str] = Field(default_factory=list)
    support_evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_verification(self) -> "ControllerVerificationResult":
        self.support_concept_ids = _unique(
            self.support_concept_ids,
            label="Verification concept ids",
        )
        self.support_evidence_ids = _unique(
            self.support_evidence_ids,
            label="Verification evidence ids",
        )
        if self.status == "supported" and not self.support_evidence_ids:
            raise ValueError(
                "Supported verification requires supporting evidence."
            )
        if self.status in {"unresolved", "unresolvable"} and (
            self.support_concept_ids or self.support_evidence_ids
        ):
            raise ValueError(
                "An unresolved verification cannot carry support ids."
            )
        return self


class ControllerObservationBase(ControllerModel):
    action_type: ControllerActionType
    action_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    novel_concept_ids: list[str] = Field(default_factory=list)
    novel_evidence_ids: list[str] = Field(default_factory=list)
    novel_relation_ids: list[str] = Field(default_factory=list)
    duplicate_ids: list[str] = Field(default_factory=list)
    cost: ControllerCost

    @model_validator(mode="after")
    def validate_common_ids(self) -> "ControllerObservationBase":
        for field_name, label in (
            ("novel_concept_ids", "Novel concept ids"),
            ("novel_evidence_ids", "Novel evidence ids"),
            ("novel_relation_ids", "Novel relation ids"),
            ("duplicate_ids", "Duplicate observation ids"),
        ):
            setattr(
                self,
                field_name,
                _unique(getattr(self, field_name), label=label),
            )
        return self


class ConceptSearchObservation(ControllerObservationBase):
    action_type: Literal["search_concept"] = "search_concept"
    hits: list[ControllerConceptHit] = Field(default_factory=list)


class EvidenceSearchObservation(ControllerObservationBase):
    action_type: Literal["search_evidence"] = "search_evidence"
    hits: list[ControllerEvidenceHit] = Field(default_factory=list)


class GraphExpansionObservation(ControllerObservationBase):
    action_type: Literal["expand_typed_neighbor"] = "expand_typed_neighbor"
    hits: list[ControllerRelationHit] = Field(default_factory=list)


class VerificationObservation(ControllerObservationBase):
    action_type: Literal["verify_support"] = "verify_support"
    results: list[ControllerVerificationResult] = Field(default_factory=list)


class AnswerObservation(ControllerObservationBase):
    action_type: Literal["answer"] = "answer"


class AbstainObservation(ControllerObservationBase):
    action_type: Literal["abstain"] = "abstain"
    reason_code: ControllerAbstainReason


ControllerObservation: TypeAlias = Annotated[
    ConceptSearchObservation
    | EvidenceSearchObservation
    | GraphExpansionObservation
    | VerificationObservation
    | AnswerObservation
    | AbstainObservation,
    Field(discriminator="action_type"),
]
CONTROLLER_OBSERVATION_ADAPTER = TypeAdapter(ControllerObservation)


def controller_minimum_action_cost(
    action: ControllerAction,
) -> ControllerCost:
    values: dict[str, int] = {"steps": 1}
    if isinstance(action, SearchConceptAction):
        values.update(retrieval_calls=1, concept_searches=1)
    elif isinstance(action, SearchEvidenceAction):
        values.update(retrieval_calls=1, evidence_searches=1)
    elif isinstance(action, ExpandTypedNeighborAction):
        values.update(retrieval_calls=1, graph_expansions=1)
    elif isinstance(action, VerifySupportAction):
        values.update(verifications=1)
    return ControllerCost(**values)


def audit_controller_observation(
    action: ControllerAction,
    observation: ControllerObservation,
    state: ControllerState,
) -> None:
    if observation.action_type != action.action_type:
        raise ValueError("Observation action type does not match its action.")
    if observation.action_fingerprint != action_fingerprint(action):
        raise ValueError("Observation fingerprint does not match its action.")
    if observation.cost.steps != 1:
        raise ValueError("Every executed action must consume one step.")
    minimum = controller_minimum_action_cost(action)
    for field_name in (
        "retrieval_calls",
        "concept_searches",
        "evidence_searches",
        "graph_expansions",
        "verifications",
    ):
        if getattr(observation.cost, field_name) < getattr(
            minimum,
            field_name,
        ):
            raise ValueError(
                f"Observation under-reports {field_name} cost."
            )
    if observation.cost.unique_concepts != len(
        observation.novel_concept_ids
    ):
        raise ValueError(
            "Observation unique-concept cost does not match novel ids."
        )
    if observation.cost.unique_evidence != len(
        observation.novel_evidence_ids
    ):
        raise ValueError(
            "Observation unique-evidence cost does not match novel ids."
        )
    if set(observation.novel_concept_ids).intersection(
        state.retrieved_concept_ids
    ):
        raise ValueError("A seen concept was reported as novel.")
    if set(observation.novel_evidence_ids).intersection(
        state.retrieved_evidence_ids
    ):
        raise ValueError("Seen evidence was reported as novel.")
    if set(observation.novel_relation_ids).intersection(
        state.traversed_relation_ids
    ):
        raise ValueError("A traversed relation was reported as novel.")
    ranks: list[int]
    if isinstance(observation, ConceptSearchObservation):
        if not isinstance(action, SearchConceptAction):
            raise ValueError("Concept hits require a concept-search action.")
        if (
            observation.novel_evidence_ids
            or observation.novel_relation_ids
        ):
            raise ValueError("Concept search returned unrelated novel ids.")
        hit_ids = [hit.concept_id for hit in observation.hits]
        ranks = [hit.rank for hit in observation.hits]
        if len(hit_ids) != len(set(hit_ids)):
            raise ValueError("Concept-search hit ids must be unique.")
        if len(hit_ids) > action.top_k:
            raise ValueError("Concept search returned more than top_k hits.")
        if set(hit_ids).intersection(action.exclude_concept_ids):
            raise ValueError("Concept search returned an excluded concept.")
        expected_novel = [
            item for item in hit_ids
            if item not in state.retrieved_concept_ids
        ]
        expected_duplicates = [
            item for item in hit_ids
            if item in state.retrieved_concept_ids
        ]
        if (
            observation.novel_concept_ids != expected_novel
            or observation.duplicate_ids != expected_duplicates
        ):
            raise ValueError(
                "Concept hits do not match novel/duplicate concept ids."
            )
    elif isinstance(observation, EvidenceSearchObservation):
        if not isinstance(action, SearchEvidenceAction):
            raise ValueError("Evidence hits require an evidence-search action.")
        if (
            observation.novel_concept_ids
            or observation.novel_relation_ids
        ):
            raise ValueError("Evidence search returned unrelated novel ids.")
        hit_ids = [hit.evidence_id for hit in observation.hits]
        ranks = [hit.rank for hit in observation.hits]
        if len(hit_ids) != len(set(hit_ids)):
            raise ValueError("Evidence-search hit ids must be unique.")
        if len(hit_ids) > action.top_k:
            raise ValueError("Evidence search returned more than top_k hits.")
        if set(hit_ids).intersection(action.exclude_evidence_ids):
            raise ValueError("Evidence search returned excluded evidence.")
        if action.scope_concept_ids and any(
            hit.concept_id not in action.scope_concept_ids
            for hit in observation.hits
        ):
            raise ValueError("Evidence hit is outside the requested scope.")
        expected_novel = [
            item for item in hit_ids
            if item not in state.retrieved_evidence_ids
        ]
        expected_duplicates = [
            item for item in hit_ids
            if item in state.retrieved_evidence_ids
        ]
        if (
            observation.novel_evidence_ids != expected_novel
            or observation.duplicate_ids != expected_duplicates
        ):
            raise ValueError(
                "Evidence hits do not match novel/duplicate evidence ids."
            )
    elif isinstance(observation, GraphExpansionObservation):
        if not isinstance(action, ExpandTypedNeighborAction):
            raise ValueError("Graph hits require a graph-expansion action.")
        if observation.novel_evidence_ids:
            raise ValueError("Graph expansion returned evidence ids.")
        relation_ids = [hit.relation_id for hit in observation.hits]
        ranks = [hit.rank for hit in observation.hits]
        if len(relation_ids) != len(set(relation_ids)):
            raise ValueError("Graph relation-hit ids must be unique.")
        if len(relation_ids) > (
            len(action.anchor_concept_ids)
            * action.max_neighbors_per_anchor
        ):
            raise ValueError("Graph expansion returned too many hits.")
        if set(relation_ids).intersection(action.exclude_relation_ids):
            raise ValueError("Graph expansion returned an excluded relation.")
        neighbors: list[str] = []
        for hit in observation.hits:
            if hit.relation_type not in action.relation_types:
                raise ValueError("Graph hit has a disallowed relation type.")
            if (
                action.direction != "both"
                and hit.traversal_direction != action.direction
            ):
                raise ValueError("Graph hit has a disallowed direction.")
            if hit.traversal_direction == "outgoing":
                if hit.source_concept_id not in action.anchor_concept_ids:
                    raise ValueError(
                        "Outgoing graph hit does not start at an anchor."
                    )
                neighbors.append(hit.target_concept_id)
            else:
                if hit.target_concept_id not in action.anchor_concept_ids:
                    raise ValueError(
                        "Incoming graph hit does not end at an anchor."
                    )
                neighbors.append(hit.source_concept_id)
        ordered_neighbors = list(dict.fromkeys(neighbors))
        expected_novel_relations = [
            item for item in relation_ids
            if item not in state.traversed_relation_ids
        ]
        expected_novel_concepts = [
            item for item in ordered_neighbors
            if item not in state.retrieved_concept_ids
        ]
        expected_duplicates = list(
            dict.fromkeys(
                [
                    item for item in relation_ids
                    if item in state.traversed_relation_ids
                ]
                + [
                    item for item in ordered_neighbors
                    if item in state.retrieved_concept_ids
                ]
            )
        )
        if (
            observation.novel_relation_ids
            != expected_novel_relations
            or observation.novel_concept_ids != expected_novel_concepts
            or observation.duplicate_ids != expected_duplicates
        ):
            raise ValueError(
                "Graph hits do not match novel/duplicate ids."
            )
    else:
        ranks = []
        if (
            observation.novel_concept_ids
            or observation.novel_evidence_ids
            or observation.novel_relation_ids
            or observation.duplicate_ids
        ):
            raise ValueError(
                "Non-retrieval observation cannot return retrieval ids."
            )
    if ranks != list(range(1, len(ranks) + 1)):
        raise ValueError("Observation hit ranks must be contiguous.")


def reduce_controller_state(
    state: ControllerState,
    action: ControllerAction,
    observation: ControllerObservation,
    *,
    decision_cost: ControllerCost | None = None,
) -> ControllerState:
    """Pure transition function shared by execution and trace validation."""

    concept_ids = [
        *state.retrieved_concept_ids,
        *observation.novel_concept_ids,
    ]
    evidence_ids = [
        *state.retrieved_evidence_ids,
        *observation.novel_evidence_ids,
    ]
    relation_ids = [
        *state.traversed_relation_ids,
        *observation.novel_relation_ids,
    ]
    verified_ids = list(state.verified_evidence_ids)
    needs = [need.model_copy(deep=True) for need in state.knowledge_needs]
    support_changed = False

    if isinstance(action, VerifySupportAction):
        if not isinstance(observation, VerificationObservation):
            raise ValueError(
                "Verification action requires a verification observation."
            )
        result_need_ids = [
            result.need_id for result in observation.results
        ]
        if len(result_need_ids) != len(set(result_need_ids)):
            raise ValueError(
                "Verifier returned duplicate knowledge-need results."
            )
        results = {result.need_id: result for result in observation.results}
        if set(results) != set(action.need_ids):
            raise ValueError(
                "Verifier must return exactly one result per requested need."
            )
        verified_ids.extend(
            evidence_id
            for result in observation.results
            for evidence_id in result.support_evidence_ids
            if evidence_id not in verified_ids
        )
        by_id = {need.need_id: need for need in needs}
        for need_id, result in results.items():
            if need_id not in by_id:
                raise ValueError(
                    "Verifier returned an unknown knowledge need."
                )
            if not set(result.support_evidence_ids).issubset(
                action.evidence_ids
            ):
                raise ValueError(
                    "Verifier cited evidence outside its action."
                )
            current = by_id[need_id]
            before = current.model_dump(mode="json")
            if current.status == "supported":
                if result.status != "supported":
                    continue
                next_status = "supported"
                next_confidence = max(
                    current.confidence,
                    result.confidence,
                )
                next_concepts = list(
                    dict.fromkeys(
                        [
                            *current.support_concept_ids,
                            *result.support_concept_ids,
                        ]
                    )
                )
                next_evidence = list(
                    dict.fromkeys(
                        [
                            *current.support_evidence_ids,
                            *result.support_evidence_ids,
                        ]
                    )
                )
            else:
                next_status = result.status
                next_confidence = result.confidence
                next_concepts = result.support_concept_ids
                next_evidence = result.support_evidence_ids
            by_id[need_id] = ControllerKnowledgeNeed(
                need_id=current.need_id,
                description=current.description,
                need_type=current.need_type,
                required=current.required,
                status=next_status,
                confidence=next_confidence,
                support_concept_ids=next_concepts,
                support_evidence_ids=next_evidence,
            )
            support_changed = (
                support_changed
                or before != by_id[need_id].model_dump(mode="json")
            )
        needs = [by_id[need.need_id] for need in needs]

    made_progress = any(
        (
            observation.novel_concept_ids,
            observation.novel_evidence_ids,
            observation.novel_relation_ids,
        )
    ) or support_changed
    next_no_progress = (
        0 if made_progress else state.consecutive_no_progress + 1
    )
    required = [need for need in needs if need.required]
    answerability_confidence = (
        1.0
        if not required
        else sum(
            need.confidence if need.status == "supported" else 0.0
            for need in required
        )
        / len(required)
    )
    return ControllerState(
        question_id=state.question_id,
        question=state.question,
        knowledge_needs=needs,
        retrieved_concept_ids=concept_ids,
        retrieved_evidence_ids=evidence_ids,
        verified_evidence_ids=verified_ids,
        traversed_relation_ids=relation_ids,
        attempted_action_fingerprints=[
            *state.attempted_action_fingerprints,
            observation.action_fingerprint,
        ],
        answerability_confidence=answerability_confidence,
        consecutive_no_progress=next_no_progress,
        step_index=state.step_index + 1,
        cost=state.cost.plus(decision_cost or ControllerCost()).plus(
            observation.cost
        ),
    )


class ControllerStep(ControllerModel):
    step_index: int = Field(ge=0)
    state_before_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state_before: ControllerState
    decision_cost: ControllerCost = Field(default_factory=ControllerCost)
    action: ControllerAction
    observation: ControllerObservation
    state_after: ControllerState

    @model_validator(mode="after")
    def validate_transition(self) -> "ControllerStep":
        if self.state_before.step_index != self.step_index:
            raise ValueError("Step index does not match the state before.")
        if self.state_after.step_index != self.step_index + 1:
            raise ValueError("State after must advance exactly one step.")
        if self.state_before_sha256 != controller_state_sha256(
            self.state_before
        ):
            raise ValueError("State-before hash is not canonical.")
        fingerprint = action_fingerprint(self.action)
        if self.observation.action_fingerprint != fingerprint:
            raise ValueError(
                "Observation fingerprint does not match the action."
            )
        if (
            fingerprint
            not in self.state_after.attempted_action_fingerprints
        ):
            raise ValueError("State after must record the attempted action.")
        audit_controller_observation(
            self.action,
            self.observation,
            self.state_before,
        )
        expected_state = reduce_controller_state(
            self.state_before,
            self.action,
            self.observation,
            decision_cost=self.decision_cost,
        )
        if self.state_after != expected_state:
            raise ValueError(
                "State after does not match the canonical reducer replay."
            )
        return self


StrictTopK = Annotated[StrictInt, Field(ge=1, le=100)]
StrictMaxItems = Annotated[StrictInt, Field(ge=1)]


class ControllerPolicyConfigBase(ControllerModel):
    """Base type for a policy's closed, versioned configuration."""


class FixedDensePolicyConfig(ControllerPolicyConfigBase):
    top_k: StrictTopK
    max_items: StrictMaxItems | None = None
    verifier: Literal["deterministic_lexical_smoke_v1"]
    answerer: Literal["extractive_evidence_smoke_v1"]
    claim_scope: Literal["development_debug_only"]


class FixedDenseTypedGraphPolicyConfig(ControllerPolicyConfigBase):
    top_k: StrictTopK
    max_items: StrictMaxItems | None = None
    verifier: Literal["deterministic_lexical_smoke_v1"]
    answerer: Literal["extractive_evidence_smoke_v1"]
    claim_scope: Literal["development_debug_only"]


class EvidenceGapPolicyConfig(ControllerPolicyConfigBase):
    top_k: StrictTopK
    max_items: StrictMaxItems | None = None
    verifier: Literal["deterministic_lexical_smoke_v1"]
    answerer: Literal["extractive_evidence_smoke_v1"]
    claim_scope: Literal["development_debug_only"]


class CustomControllerPolicyConfig(ControllerPolicyConfigBase):
    """Closed empty config for externally defined evaluation fixtures."""


ControllerPolicyConfig: TypeAlias = (
    FixedDensePolicyConfig
    | FixedDenseTypedGraphPolicyConfig
    | EvidenceGapPolicyConfig
    | CustomControllerPolicyConfig
)

BUILT_IN_POLICY_CONFIG_TYPES: dict[
    str,
    type[ControllerPolicyConfigBase],
] = {
    "fixed_dense": FixedDensePolicyConfig,
    "fixed_dense_typed_graph": FixedDenseTypedGraphPolicyConfig,
    "evidence_gap": EvidenceGapPolicyConfig,
}


class ControllerProtocol(ControllerModel):
    schema_version: Literal["1.0"] = "1.0"
    protocol_id: str = Field(min_length=1)
    corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    memory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    benchmark_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    embedding_snapshot_file_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    embedding_records_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    query_encoder_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    retrieval_config_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    code_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    split: Literal["development", "test"] = "development"
    policy_name: str = Field(min_length=1)
    concept_granularity: Literal["card_proxy", "concept_node"] = "card_proxy"
    evidence_retrieval: Literal["bm25"] = "bm25"
    allowed_actions: list[ControllerActionType] = Field(
        default_factory=lambda: [
            "search_concept",
            "search_evidence",
            "expand_typed_neighbor",
            "verify_support",
            "answer",
            "abstain",
        ]
    )
    budget: ControllerBudget = Field(default_factory=ControllerBudget)
    seed: int = 20260725
    oracle_only: bool = False
    policy_config: ControllerPolicyConfig = Field(
        default_factory=CustomControllerPolicyConfig
    )
    protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="before")
    @classmethod
    def validate_policy_config_contract(
        cls,
        values: object,
    ) -> object:
        if not isinstance(values, dict):
            return values
        policy_name = values.get("policy_name")
        config_type = BUILT_IN_POLICY_CONFIG_TYPES.get(
            policy_name,
            CustomControllerPolicyConfig,
        )
        raw_config = values.get("policy_config", {})
        parsed_config = config_type.model_validate(raw_config)
        updated = dict(values)
        updated["policy_config"] = parsed_config
        return updated

    @model_validator(mode="after")
    def validate_protocol(self) -> "ControllerProtocol":
        if len(self.allowed_actions) != len(set(self.allowed_actions)):
            raise ValueError("Allowed controller actions must be unique.")
        if not TERMINAL_ACTION_TYPES.intersection(self.allowed_actions):
            raise ValueError(
                "A controller protocol requires answer or abstain."
            )
        if bool(self.embedding_snapshot_file_sha256) != bool(
            self.embedding_records_sha256
        ):
            raise ValueError(
                "Embedding file and record hashes must be provided together."
            )
        if self.embedding_snapshot_file_sha256 and not (
            self.query_encoder_sha256
        ):
            raise ValueError(
                "A frozen embedding snapshot requires the exact query "
                "encoder hash."
            )
        if self.embedding_snapshot_file_sha256 and not (
            self.retrieval_config_sha256
        ):
            raise ValueError(
                "A frozen embedding snapshot requires retrieval-config "
                "provenance."
            )
        expected_config_type = BUILT_IN_POLICY_CONFIG_TYPES.get(
            self.policy_name,
            CustomControllerPolicyConfig,
        )
        if type(self.policy_config) is not expected_config_type:
            raise ValueError(
                "Policy config type does not match policy_name."
            )
        return self


class ControllerTrace(ControllerModel):
    schema_version: Literal["1.0"] = "1.0"
    trace_id: str = Field(min_length=1)
    protocol_id: str = Field(min_length=1)
    protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    memory_id: str = Field(min_length=1)
    memory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    question_id: str = Field(min_length=1)
    policy_name: str = Field(min_length=1)
    initial_state: ControllerState
    steps: list[ControllerStep] = Field(default_factory=list)
    final_state: ControllerState
    terminal_decision_cost: ControllerCost = Field(
        default_factory=ControllerCost
    )
    terminal_environment_cost: ControllerCost = Field(
        default_factory=ControllerCost
    )
    terminal_proposed_action: ControllerAction | None = None
    terminal_minimum_action_cost: ControllerCost = Field(
        default_factory=ControllerCost
    )
    budget_exhausted_fields: list[ControllerBudgetField] = Field(
        default_factory=list
    )
    stop_reason: ControllerStopReason
    status: ControllerTraceStatus
    final_answer: str | None = None
    citation_evidence_ids: list[str] = Field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime = Field(default_factory=utc_now)
    trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_trace(self) -> "ControllerTrace":
        self.citation_evidence_ids = _unique(
            self.citation_evidence_ids,
            label="Trace citation evidence ids",
        )
        if len(self.budget_exhausted_fields) != len(
            set(self.budget_exhausted_fields)
        ):
            raise ValueError("Budget-exhausted fields must be unique.")
        previous = self.initial_state
        for expected_index, step in enumerate(self.steps):
            if step.step_index != expected_index:
                raise ValueError("Controller steps must be contiguous.")
            if step.state_before != previous:
                raise ValueError("Controller trace state chain is broken.")
            if expected_index < len(self.steps) - 1 and isinstance(
                step.action,
                (AnswerAction, AbstainAction),
            ):
                raise ValueError(
                    "No controller step may follow a terminal action."
                )
            previous = step.state_after
        if self.final_state != previous:
            raise ValueError("Final state does not match the final step.")
        if self.status == "failed":
            if not self.error_type or not self.error_message:
                raise ValueError(
                    "A failed trace requires an error type and message."
                )
        elif self.error_type is not None or self.error_message is not None:
            raise ValueError(
                "A non-failed trace cannot contain infrastructure errors."
            )
        allowed_stops = {
            "completed": {"answer", "abstain"},
            "forced_abstain": {
                "budget_exhausted",
                "max_steps",
                "no_progress",
                "invalid_action",
            },
            "failed": {"policy_error", "environment_error"},
        }
        if self.stop_reason not in allowed_stops[self.status]:
            raise ValueError(
                "Trace status and stop reason are inconsistent."
            )
        if (
            self.created_at.utcoffset() is None
            or self.completed_at.utcoffset() is None
        ):
            raise ValueError("Trace timestamps must be timezone-aware.")
        if self.completed_at < self.created_at:
            raise ValueError("Trace completion cannot precede creation.")
        if self.final_answer is not None and self.stop_reason != "answer":
            raise ValueError("Only an answer stop can include a final answer.")
        if (
            self.stop_reason == "answer"
            and (
                self.final_answer is None
                or not self.final_answer.strip()
            )
        ):
            raise ValueError("An answer stop requires a final answer.")
        if self.stop_reason == "answer":
            if not self.citation_evidence_ids:
                raise ValueError(
                    "An answer stop requires at least one citation."
                )
            if not self.steps or not isinstance(
                self.steps[-1].action,
                AnswerAction,
            ):
                raise ValueError(
                    "An answer stop requires a final answer action."
                )
            supported_need_ids = set(
                self.steps[-1].action.supported_need_ids
            )
            allowed_citations = {
                evidence_id
                for need in self.final_state.knowledge_needs
                if need.need_id in supported_need_ids
                for evidence_id in need.support_evidence_ids
            }
            if not set(self.citation_evidence_ids).issubset(
                allowed_citations
            ):
                raise ValueError(
                    "Answer citations do not support its declared needs."
                )
        elif self.final_answer is not None or self.citation_evidence_ids:
            raise ValueError(
                "Only an answer stop may include answer content or citations."
            )
        if self.stop_reason == "abstain":
            if not self.steps or not isinstance(
                self.steps[-1].action,
                AbstainAction,
            ):
                raise ValueError(
                    "An abstain stop requires a final abstain action."
                )
            if not isinstance(
                self.steps[-1].observation,
                AbstainObservation,
            ) or (
                self.steps[-1].observation.reason_code
                != self.steps[-1].action.reason_code
            ):
                raise ValueError(
                    "Abstain action and observation reasons must match."
                )
        if self.status == "completed" and (
            self.terminal_decision_cost != ControllerCost()
            or self.terminal_environment_cost != ControllerCost()
        ):
            raise ValueError(
                "A completed trace cannot carry uncommitted terminal cost."
            )
        if (
            self.terminal_environment_cost != ControllerCost()
            and self.stop_reason != "environment_error"
        ):
            raise ValueError(
                "Only an environment-error trace may carry terminal "
                "environment cost."
            )
        if self.stop_reason in {"max_steps", "no_progress"} and (
            self.terminal_decision_cost != ControllerCost()
            or self.terminal_environment_cost != ControllerCost()
        ):
            raise ValueError(
                f"A {self.stop_reason} trace cannot carry terminal cost."
            )
        if self.terminal_proposed_action is None:
            if self.terminal_minimum_action_cost != ControllerCost():
                raise ValueError(
                    "Terminal minimum cost requires a proposed action."
                )
        else:
            if self.stop_reason != "budget_exhausted":
                raise ValueError(
                    "A terminal proposed action is only valid for "
                    "pre-action budget exhaustion."
                )
            expected_minimum = controller_minimum_action_cost(
                self.terminal_proposed_action
            )
            if self.terminal_minimum_action_cost != expected_minimum:
                raise ValueError(
                    "Terminal minimum action cost is not canonical."
                )
        if self.stop_reason == "budget_exhausted":
            if not self.budget_exhausted_fields:
                raise ValueError(
                    "Budget exhaustion requires the exhausted fields."
                )
        elif self.budget_exhausted_fields:
            raise ValueError(
                "Only budget exhaustion may list exhausted fields."
            )
        if not set(self.citation_evidence_ids).issubset(
            self.final_state.verified_evidence_ids
        ):
            raise ValueError(
                "Trace citations must reference verified evidence."
            )
        if self.trace_sha256 != controller_trace_payload_sha256(self):
            raise ValueError("Trace hash is not canonical.")
        return self


def controller_state_sha256(state: ControllerState) -> str:
    return sha256_value(state.model_dump(mode="json"))


def controller_memory_payload_sha256(
    memory: ControllerMemorySnapshot,
) -> str:
    payload = memory.model_dump(
        mode="json",
        exclude={"memory_sha256", "created_at"},
    )
    return sha256_value(payload)


def controller_protocol_payload_sha256(protocol: ControllerProtocol) -> str:
    payload = protocol.model_dump(mode="json", exclude={"protocol_sha256"})
    return sha256_value(payload)


def controller_trace_payload_sha256(trace: ControllerTrace) -> str:
    payload = trace.model_dump(
        mode="json",
        exclude={"trace_sha256"},
    )
    return sha256_value(payload)
