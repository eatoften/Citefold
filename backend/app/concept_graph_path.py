"""Deterministic traversal over one immutable, evidence-bearing graph version."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import hashlib
import heapq
import json
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .concept_graph import (
    ConceptRelationType,
    SYMMETRIC_RELATION_TYPES,
    canonicalize_relation_endpoints,
)
from .concept_graph_publication import (
    PublishedConcept,
    PublishedGraphSnapshot,
    PublishedRelation,
)


GraphDirectionMode = Literal["outgoing", "incoming", "both"]
RelationshipTraceStatus = Literal["found", "unreachable", "limits_reached"]
RELATION_TYPE_PRIORITY: tuple[ConceptRelationType, ...] = (
    "prerequisite",
    "part_of",
    "example_of",
    "related",
    "contrast_with",
)
DEFAULT_LOCAL_GRAPH_HOPS = 2
DEFAULT_RELATIONSHIP_TRACE_HOPS = 6
MAX_LOCAL_GRAPH_HOPS = 5
MAX_RELATIONSHIP_TRACE_HOPS = 10
MAX_GRAPH_RESULT_NODES = 500


class GraphPathRequestError(ValueError):
    pass


class GraphPathConceptNotFoundError(GraphPathRequestError):
    pass


class GraphPathLimitError(GraphPathRequestError):
    pass


class GraphPathIntegrityError(RuntimeError):
    pass


class StrictPathModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LocalGraphNode(StrictPathModel):
    distance: int = Field(ge=0, le=MAX_RELATIONSHIP_TRACE_HOPS)
    concept: PublishedConcept


class LocalGraphResult(StrictPathModel):
    kind: Literal["local_graph"] = "local_graph"
    course_id: str
    graph_version: int = Field(ge=1)
    graph_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    root_concept_id: str
    relation_types: list[ConceptRelationType]
    direction_mode: GraphDirectionMode
    max_hops: int = Field(ge=0, le=MAX_RELATIONSHIP_TRACE_HOPS)
    max_nodes: int = Field(ge=1, le=MAX_GRAPH_RESULT_NODES)
    truncated_by_max_nodes: bool
    nodes: list[LocalGraphNode]
    relations: list[PublishedRelation]
    result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class RelationshipTraceStep(StrictPathModel):
    ordinal: int = Field(ge=0)
    from_concept_id: str
    to_concept_id: str
    traversed_against_relation_direction: bool
    relation: PublishedRelation


class RelationshipTraceResult(StrictPathModel):
    kind: Literal["relationship_trace"] = "relationship_trace"
    course_id: str
    graph_version: int = Field(ge=1)
    graph_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_concept_id: str
    target_concept_id: str
    relation_types: list[ConceptRelationType]
    direction_mode: GraphDirectionMode
    max_hops: int = Field(ge=0, le=MAX_RELATIONSHIP_TRACE_HOPS)
    max_nodes: int = Field(ge=1, le=MAX_GRAPH_RESULT_NODES)
    status: RelationshipTraceStatus
    truncated_by_max_hops: bool
    truncated_by_max_nodes: bool
    hop_count: int | None = Field(default=None, ge=0)
    nodes: list[PublishedConcept]
    steps: list[RelationshipTraceStep]
    result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class LearningPathLayer(StrictPathModel):
    index: int = Field(ge=0)
    concept_ids: list[str] = Field(min_length=1)


class LearningPathResult(StrictPathModel):
    kind: Literal["learning_path"] = "learning_path"
    course_id: str
    graph_version: int = Field(ge=1)
    graph_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_concept_id: str
    max_nodes: int = Field(ge=1, le=MAX_GRAPH_RESULT_NODES)
    nodes: list[PublishedConcept]
    relations: list[PublishedRelation]
    layers: list[LearningPathLayer]
    linearization: list[str]
    result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class _Transition:
    neighbor_id: str
    relation: PublishedRelation
    traversed_against_direction: bool


@dataclass(frozen=True, slots=True)
class _GraphIndex:
    snapshot: PublishedGraphSnapshot
    concepts: dict[str, PublishedConcept]
    relations: dict[str, PublishedRelation]
    transitions: dict[
        GraphDirectionMode,
        dict[str, tuple[_Transition, ...]],
    ]
    incoming_prerequisites: dict[str, tuple[PublishedRelation, ...]]


def local_graph(
    snapshot: PublishedGraphSnapshot,
    *,
    root_concept_id: str,
    relation_types: Sequence[ConceptRelationType] = RELATION_TYPE_PRIORITY,
    direction_mode: GraphDirectionMode = "both",
    max_hops: int = DEFAULT_LOCAL_GRAPH_HOPS,
    max_nodes: int = 100,
) -> LocalGraphResult:
    """Return the deterministic induced N-hop neighborhood of one Concept."""

    index = _build_index(snapshot)
    _require_limits(
        max_hops=max_hops,
        max_allowed_hops=MAX_LOCAL_GRAPH_HOPS,
        max_nodes=max_nodes,
    )
    _require_concept(index, root_concept_id)
    allowed = _normalize_relation_types(relation_types)
    distances = {root_concept_id: 0}
    queue = deque([root_concept_id])
    truncated = False
    while queue:
        current = queue.popleft()
        distance = distances[current]
        if distance >= max_hops:
            continue
        for transition in _transitions(
            index,
            current,
            allowed=allowed,
            direction_mode=direction_mode,
        ):
            if transition.neighbor_id in distances:
                continue
            if len(distances) >= max_nodes:
                truncated = True
                continue
            distances[transition.neighbor_id] = distance + 1
            queue.append(transition.neighbor_id)

    ordered_ids = sorted(distances, key=lambda item: (distances[item], item))
    selected = set(ordered_ids)
    relations = sorted(
        (
            relation
            for relation in index.relations.values()
            if relation.relation_type in allowed
            and relation.source_concept_id in selected
            and relation.target_concept_id in selected
        ),
        key=_relation_sort_key,
    )
    hash_payload = {
        **_result_hash_base(index),
        "kind": "local_graph",
        "root_concept_id": root_concept_id,
        "relation_types": list(allowed),
        "direction_mode": direction_mode,
        "max_hops": max_hops,
        "max_nodes": max_nodes,
        "truncated_by_max_nodes": truncated,
        "nodes": [
            [item, distances[item], index.concepts[item].aggregate_hash]
            for item in ordered_ids
        ],
        "relations": [
            [item.relation_id, item.aggregate_hash] for item in relations
        ],
    }
    return LocalGraphResult(
        course_id=snapshot.version.course_id,
        graph_version=snapshot.version.version_number,
        graph_content_hash=snapshot.version.content_hash,
        root_concept_id=root_concept_id,
        relation_types=list(allowed),
        direction_mode=direction_mode,
        max_hops=max_hops,
        max_nodes=max_nodes,
        truncated_by_max_nodes=truncated,
        nodes=[
            LocalGraphNode(
                distance=distances[item],
                concept=index.concepts[item],
            )
            for item in ordered_ids
        ],
        relations=relations,
        result_hash=_canonical_hash(hash_payload),
    )


def relationship_trace(
    snapshot: PublishedGraphSnapshot,
    *,
    source_concept_id: str,
    target_concept_id: str,
    relation_types: Sequence[ConceptRelationType] = RELATION_TYPE_PRIORITY,
    direction_mode: GraphDirectionMode = "outgoing",
    max_hops: int = DEFAULT_RELATIONSHIP_TRACE_HOPS,
    max_nodes: int = 200,
) -> RelationshipTraceResult:
    """Find one shortest-hop trace with stable equal-path tie-breaking."""

    index = _build_index(snapshot)
    _require_limits(
        max_hops=max_hops,
        max_allowed_hops=MAX_RELATIONSHIP_TRACE_HOPS,
        max_nodes=max_nodes,
    )
    _require_concept(index, source_concept_id)
    _require_concept(index, target_concept_id)
    allowed = _normalize_relation_types(relation_types)
    parents: dict[str, tuple[str, PublishedRelation, bool]] = {}
    distances = {source_concept_id: 0}
    queue = deque([source_concept_id])
    truncated_by_hops = False
    truncated = False
    found = source_concept_id == target_concept_id
    while queue and not found:
        current = queue.popleft()
        distance = distances[current]
        if distance >= max_hops:
            truncated_by_hops = truncated_by_hops or any(
                transition.neighbor_id not in distances
                for transition in _transitions(
                    index,
                    current,
                    allowed=allowed,
                    direction_mode=direction_mode,
                )
            )
            continue
        for transition in _transitions(
            index,
            current,
            allowed=allowed,
            direction_mode=direction_mode,
        ):
            neighbor = transition.neighbor_id
            if neighbor in distances:
                continue
            if len(distances) >= max_nodes:
                truncated = True
                continue
            distances[neighbor] = distance + 1
            parents[neighbor] = (
                current,
                transition.relation,
                transition.traversed_against_direction,
            )
            if neighbor == target_concept_id:
                found = True
                break
            queue.append(neighbor)

    node_ids: list[str] = []
    raw_steps: list[tuple[str, str, PublishedRelation, bool]] = []
    if found:
        cursor = target_concept_id
        node_ids.append(cursor)
        while cursor != source_concept_id:
            previous, relation, reversed_direction = parents[cursor]
            raw_steps.append(
                (previous, cursor, relation, reversed_direction)
            )
            cursor = previous
            node_ids.append(cursor)
        node_ids.reverse()
        raw_steps.reverse()

    status: RelationshipTraceStatus = (
        "found"
        if found
        else "limits_reached"
        if truncated or truncated_by_hops
        else "unreachable"
    )
    hash_payload = {
        **_result_hash_base(index),
        "kind": "relationship_trace",
        "source_concept_id": source_concept_id,
        "target_concept_id": target_concept_id,
        "relation_types": list(allowed),
        "direction_mode": direction_mode,
        "max_hops": max_hops,
        "max_nodes": max_nodes,
        "status": status,
        "truncated_by_max_hops": truncated_by_hops,
        "truncated_by_max_nodes": truncated,
        "nodes": [
            [item, index.concepts[item].aggregate_hash] for item in node_ids
        ],
        "steps": [
            [
                start,
                end,
                relation.relation_id,
                relation.aggregate_hash,
                reversed_direction,
            ]
            for start, end, relation, reversed_direction in raw_steps
        ],
    }
    return RelationshipTraceResult(
        course_id=snapshot.version.course_id,
        graph_version=snapshot.version.version_number,
        graph_content_hash=snapshot.version.content_hash,
        source_concept_id=source_concept_id,
        target_concept_id=target_concept_id,
        relation_types=list(allowed),
        direction_mode=direction_mode,
        max_hops=max_hops,
        max_nodes=max_nodes,
        status=status,
        truncated_by_max_hops=truncated_by_hops,
        truncated_by_max_nodes=truncated,
        hop_count=len(raw_steps) if found else None,
        nodes=[index.concepts[item] for item in node_ids],
        steps=[
            RelationshipTraceStep(
                ordinal=ordinal,
                from_concept_id=start,
                to_concept_id=end,
                traversed_against_relation_direction=reversed_direction,
                relation=relation,
            )
            for ordinal, (
                start,
                end,
                relation,
                reversed_direction,
            ) in enumerate(raw_steps)
        ],
        result_hash=_canonical_hash(hash_payload),
    )


def learning_path(
    snapshot: PublishedGraphSnapshot,
    *,
    target_concept_id: str,
    max_nodes: int = 200,
) -> LearningPathResult:
    """Topologically order the complete prerequisite closure of a target."""

    index = _build_index(snapshot)
    _require_limits(max_hops=0, max_allowed_hops=0, max_nodes=max_nodes)
    _require_concept(index, target_concept_id)
    closure = {target_concept_id}
    queue = deque([target_concept_id])
    while queue:
        current = queue.popleft()
        for relation in index.incoming_prerequisites.get(current, ()):
            prerequisite = relation.source_concept_id
            if prerequisite in closure:
                continue
            if len(closure) >= max_nodes:
                raise GraphPathLimitError(
                    "The prerequisite closure exceeds max_nodes."
                )
            closure.add(prerequisite)
            queue.append(prerequisite)

    relations = sorted(
        (
            relation
            for relation in index.relations.values()
            if relation.relation_type == "prerequisite"
            and relation.source_concept_id in closure
            and relation.target_concept_id in closure
        ),
        key=_relation_sort_key,
    )
    outgoing: dict[str, list[str]] = defaultdict(list)
    indegree = {concept_id: 0 for concept_id in closure}
    for relation in relations:
        outgoing[relation.source_concept_id].append(
            relation.target_concept_id
        )
        indegree[relation.target_concept_id] += 1
    for values in outgoing.values():
        values.sort()

    layers: list[list[str]] = []
    remaining = dict(indegree)
    frontier = sorted(item for item, degree in remaining.items() if degree == 0)
    processed = 0
    while frontier:
        layers.append(frontier)
        processed += len(frontier)
        next_frontier: list[str] = []
        for concept_id in frontier:
            for target in outgoing.get(concept_id, []):
                remaining[target] -= 1
                if remaining[target] == 0:
                    next_frontier.append(target)
        frontier = sorted(next_frontier)
    if processed != len(closure):
        raise GraphPathIntegrityError(
            "The published prerequisite graph contains a cycle."
        )

    heap = [item for item, degree in indegree.items() if degree == 0]
    heapq.heapify(heap)
    working_indegree = dict(indegree)
    linearization: list[str] = []
    while heap:
        concept_id = heapq.heappop(heap)
        linearization.append(concept_id)
        for target in outgoing.get(concept_id, []):
            working_indegree[target] -= 1
            if working_indegree[target] == 0:
                heapq.heappush(heap, target)
    if len(linearization) != len(closure):
        raise GraphPathIntegrityError(
            "The published prerequisite graph contains a cycle."
        )

    linear_positions = {
        concept_id: position
        for position, concept_id in enumerate(linearization)
    }
    ordered_relations = sorted(
        relations,
        key=lambda item: (
            linear_positions[item.source_concept_id],
            linear_positions[item.target_concept_id],
            item.relation_id,
        ),
    )
    hash_payload = {
        **_result_hash_base(index),
        "kind": "learning_path",
        "target_concept_id": target_concept_id,
        "max_nodes": max_nodes,
        "linearization": [
            [item, index.concepts[item].aggregate_hash]
            for item in linearization
        ],
        "layers": layers,
        "relations": [
            [item.relation_id, item.aggregate_hash]
            for item in ordered_relations
        ],
    }
    return LearningPathResult(
        course_id=snapshot.version.course_id,
        graph_version=snapshot.version.version_number,
        graph_content_hash=snapshot.version.content_hash,
        target_concept_id=target_concept_id,
        max_nodes=max_nodes,
        nodes=[index.concepts[item] for item in linearization],
        relations=ordered_relations,
        layers=[
            LearningPathLayer(index=index_value, concept_ids=concept_ids)
            for index_value, concept_ids in enumerate(layers)
        ],
        linearization=linearization,
        result_hash=_canonical_hash(hash_payload),
    )


def _build_index(snapshot: PublishedGraphSnapshot) -> _GraphIndex:
    version = snapshot.version
    if not version.is_active_version or not version.source_authority_current:
        raise GraphPathIntegrityError(
            "Paths require the active graph version with current Source evidence."
        )
    if (
        len(snapshot.concepts) != version.counts.concepts
        or len(snapshot.relations) != version.counts.relations
    ):
        raise GraphPathIntegrityError(
            "Published graph counts do not match the path snapshot."
        )
    concepts = {item.concept_id: item for item in snapshot.concepts}
    relations = {item.relation_id: item for item in snapshot.relations}
    if len(concepts) != len(snapshot.concepts):
        raise GraphPathIntegrityError("Published graph has duplicate Concepts.")
    if len(relations) != len(snapshot.relations):
        raise GraphPathIntegrityError("Published graph has duplicate relations.")

    for concept in concepts.values():
        if (
            concept.identity_status != "active"
            or concept.review_status != "accepted"
            or concept.validity_status != "current"
        ):
            raise GraphPathIntegrityError(
                "Published graph contains an ineligible Concept."
            )

    transitions: dict[
        GraphDirectionMode,
        dict[str, list[_Transition]],
    ] = {
        "outgoing": defaultdict(list),
        "incoming": defaultdict(list),
        "both": defaultdict(list),
    }
    incoming: dict[str, list[PublishedRelation]] = defaultdict(list)
    semantic_relation_keys: set[tuple[str, str, str]] = set()
    for relation in relations.values():
        if (
            relation.review_status != "accepted"
            or relation.validity_status != "current"
            or relation.source_concept_id not in concepts
            or relation.target_concept_id not in concepts
            or relation.source_concept_id == relation.target_concept_id
        ):
            raise GraphPathIntegrityError(
                "Published graph contains an ineligible relation."
            )
        canonical_source, canonical_target = canonicalize_relation_endpoints(
            relation.relation_type,
            relation.source_concept_id,
            relation.target_concept_id,
        )
        if (
            relation.relation_type in SYMMETRIC_RELATION_TYPES
            and (
                canonical_source,
                canonical_target,
            )
            != (
                relation.source_concept_id,
                relation.target_concept_id,
            )
        ):
            raise GraphPathIntegrityError(
                "Published graph contains a noncanonical symmetric relation."
            )
        semantic_key = (
            relation.relation_type,
            canonical_source,
            canonical_target,
        )
        if semantic_key in semantic_relation_keys:
            raise GraphPathIntegrityError(
                "Published graph contains a duplicate semantic relation."
            )
        semantic_relation_keys.add(semantic_key)
        source = relation.source_concept_id
        target = relation.target_concept_id
        forward = _Transition(target, relation, False)
        reverse = _Transition(source, relation, True)
        if relation.relation_type in SYMMETRIC_RELATION_TYPES:
            symmetric_reverse = _Transition(source, relation, False)
            for direction in transitions:
                transitions[direction][source].append(forward)
                transitions[direction][target].append(symmetric_reverse)
        else:
            transitions["outgoing"][source].append(forward)
            transitions["incoming"][target].append(reverse)
            transitions["both"][source].append(forward)
            transitions["both"][target].append(reverse)
        if relation.relation_type == "prerequisite":
            incoming[relation.target_concept_id].append(relation)
    return _GraphIndex(
        snapshot=snapshot,
        concepts=concepts,
        relations=relations,
        transitions={
            direction: {
                key: tuple(sorted(values, key=_transition_sort_key))
                for key, values in by_concept.items()
            }
            for direction, by_concept in transitions.items()
        },
        incoming_prerequisites={
            key: tuple(sorted(values, key=_relation_sort_key))
            for key, values in incoming.items()
        },
    )


def _transitions(
    index: _GraphIndex,
    concept_id: str,
    *,
    allowed: tuple[ConceptRelationType, ...],
    direction_mode: GraphDirectionMode,
) -> tuple[_Transition, ...]:
    if direction_mode not in {"outgoing", "incoming", "both"}:
        raise GraphPathRequestError("direction_mode is invalid.")
    return tuple(
        item
        for item in index.transitions[direction_mode].get(concept_id, ())
        if item.relation.relation_type in allowed
    )


def _transition_sort_key(
    transition: _Transition,
) -> tuple[int, str, str, bool]:
    return (
        RELATION_TYPE_PRIORITY.index(transition.relation.relation_type),
        transition.neighbor_id,
        transition.relation.relation_id,
        transition.traversed_against_direction,
    )


def _normalize_relation_types(
    relation_types: Sequence[ConceptRelationType],
) -> tuple[ConceptRelationType, ...]:
    values = set(relation_types)
    if not values or not values.issubset(RELATION_TYPE_PRIORITY):
        raise GraphPathRequestError("At least one known relation type is required.")
    return tuple(item for item in RELATION_TYPE_PRIORITY if item in values)


def _require_limits(
    *,
    max_hops: int,
    max_allowed_hops: int,
    max_nodes: int,
) -> None:
    if not 0 <= max_hops <= max_allowed_hops:
        raise GraphPathRequestError(
            f"max_hops must be between 0 and {max_allowed_hops}."
        )
    if not 1 <= max_nodes <= MAX_GRAPH_RESULT_NODES:
        raise GraphPathRequestError(
            f"max_nodes must be between 1 and {MAX_GRAPH_RESULT_NODES}."
        )


def _require_concept(index: _GraphIndex, concept_id: str) -> None:
    if concept_id not in index.concepts:
        raise GraphPathConceptNotFoundError(
            "Concept is absent from the requested graph version."
        )


def _relation_sort_key(
    relation: PublishedRelation,
) -> tuple[int, str, str, str]:
    return (
        RELATION_TYPE_PRIORITY.index(relation.relation_type),
        relation.source_concept_id,
        relation.target_concept_id,
        relation.relation_id,
    )


def _result_hash_base(index: _GraphIndex) -> dict[str, object]:
    version = index.snapshot.version
    return {
        "protocol": "concept-graph-path-result-v1",
        "course_id": version.course_id,
        "graph_version": version.version_number,
        "graph_content_hash": version.content_hash,
    }


def _canonical_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "GraphDirectionMode",
    "GraphPathConceptNotFoundError",
    "GraphPathIntegrityError",
    "GraphPathLimitError",
    "GraphPathRequestError",
    "LearningPathResult",
    "LocalGraphResult",
    "DEFAULT_LOCAL_GRAPH_HOPS",
    "DEFAULT_RELATIONSHIP_TRACE_HOPS",
    "MAX_LOCAL_GRAPH_HOPS",
    "MAX_RELATIONSHIP_TRACE_HOPS",
    "MAX_GRAPH_RESULT_NODES",
    "RELATION_TYPE_PRIORITY",
    "RelationshipTraceResult",
    "learning_path",
    "local_graph",
    "relationship_trace",
]
