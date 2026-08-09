# Concept Graph Deterministic Path Engine

- **Status:** G3 backend slice implemented; G4 product integration pending
- **Architecture owner:**
  [ADR-0008](../decisions/ADR-0008-evidence-grounded-concept-graph-and-deterministic-paths.md)
- **Implementation:** `backend/app/concept_graph_path.py`,
  `backend/app/concept_graph_path_service.py`, and
  `backend/app/concept_graph_path_api.py`

## Responsibility and data flow

The path engine is a read-only interpreter of one immutable published Concept
Graph. It never promotes candidates, changes graph truth, or asks an LLM to
choose a path.

```text
course + exact graph version + bounded query
-> one SQLite read snapshot validates and hydrates the published graph
-> service requires the active version with current Source authority
-> pure engine normalizes Concept/relation adjacency
-> Local, Trace, or Learning algorithm
-> ordered evidence-bearing DTO + canonical result hash
```

The store binds every load to `(course_id, version_number)`. The version
metadata, integrity checks, Concepts, Relations, and their immutable evidence
snapshots are observed in the same SQLite read transaction. The service then
rejects a historical head or Source-stale active version; mutable draft rows
and the legacy Card graph are never path inputs.

## Frozen G3-v1 query contract

These values were chosen for this G3 checkpoint. They were not present in the
earlier G0 protocol and must not be described as retrospectively preregistered.

| Setting | Default | Maximum |
| --- | ---: | ---: |
| Local `max_hops` | 2 | 5 |
| Local `max_nodes` | 100 | 500 |
| Trace `max_hops` | 6 | 10 |
| Trace `max_nodes` | 200 | 500 |
| Learning `max_nodes` | 200 | 500 |

The normalized relation-type priority is:

```text
prerequisite < part_of < example_of < related < contrast_with
```

Omitted relation filters select all five types in that order. Empty or unknown
filters fail validation. There is no path-level review-status filter because a
published graph already contains only accepted/current Concepts and Relations.

Direction semantics are exact:

- Local defaults to `both` so an Obsidian-like neighborhood includes incoming
  and outgoing context; Trace defaults to `outgoing` so A-to-B follows stored
  semantic direction unless the caller explicitly changes it;
- directed `A -> B` is traversed `A -> B` for `outgoing`, `B -> A` for
  `incoming`, and either way for `both`;
- `related` and `contrast_with` are stored once with canonical endpoints but
  traverse both ways in every direction mode;
- a Trace step preserves the stored Relation and separately reports
  `from_concept_id`, `to_concept_id`, and whether traversal reversed the stored
  direction.

## Algorithms

### Local Graph

Local Graph performs visited-on-discovery BFS from a required root. The root is
distance zero, and `max_hops=0` returns only that root. Nodes are admitted in a
stable BFS order until `max_nodes`; `truncated_by_max_nodes` records omitted
eligible nodes. The response then includes the allowed-type induced relations
whose two endpoints were admitted. Cycles never duplicate a node.

### Relationship Trace

Trace returns one deterministic shortest-hop path. Stable adjacency ordering
uses relation priority, neighbor ID, Relation ID, and traversal orientation.
The source equal to the target is a valid zero-hop path. Its terminal states
are:

- `found`: a shortest path and ordered steps are present;
- `unreachable`: the filtered directional frontier was fully exhausted;
- `limits_reached`: the hop boundary or node cap prevented that conclusion.

A bounded miss is therefore never mislabeled as globally unreachable.

### Learning Path

Learning Path follows incoming `prerequisite` edges from the target to build
the complete ancestor closure, including the target. It uses all prerequisite
edges induced by that closure, emits stable batch Kahn layers, and emits one
stable heap-based Kahn linearization. A cycle is an integrity failure. If the
closure exceeds `max_nodes`, the API returns a typed size error rather than an
incomplete path that silently omits a prerequisite.

## Determinism, evidence, and failures

Every result names the course, graph version, and graph content hash. Its
domain-separated SHA-256 result hash binds the normalized request, terminal or
truncation state, ordered Concept identities, ordered Relation/step identities,
traversal orientation, and Learning layers. Volatile request time and measured
latency are excluded.

Node and edge payloads reuse `PublishedConcept` and `PublishedRelation` rather
than inventing a second evidence schema. They include immutable quote, Chunk
hash, projection generation, typed Source Locator, rationale, support basis,
review provenance, and relation evidence roles. This makes the result
inspectable, but it does **not** yet make the Locator clickable: G4 still needs
a server-owned graph-evidence target/content resolver keyed by course, version,
owner kind, owner ID, and evidence ID.

| Condition | HTTP result |
| --- | --- |
| missing course, version, or Concept in that version | `404` |
| malformed filter or bound | `422` |
| complete Learning closure exceeds its node bound | `413` |
| requested version is not active or its Source authority is stale | structured `409` |
| SQLite lock exhaustion | `503` with `Retry-After: 1` |
| corrupt count/hash, missing endpoint, duplicate semantic edge, or cycle | safe `500` |

## HTTP surface

```text
GET /courses/{course_id}/concept-graph/versions/{version}/paths/local
GET /courses/{course_id}/concept-graph/versions/{version}/paths/trace
GET /courses/{course_id}/concept-graph/versions/{version}/paths/learning
```

All inputs other than course and version are validated query parameters. The
exact version in the route makes a late response auditable and lets G4 prevent
a response from an old course/version scope from replacing current UI state.

## Complexity and measured debt

Once a normalized adjacency representation exists, Local and Trace are
`O(V + E)` over the visited subgraph. Learning closure and layering are linear
apart from stable sorting/heap tie-breaking. The current request path does not
yet realize the ADR's amortized materialization target: integrity validation
loads the snapshot, hydration loads it again, and the engine rebuilds/sorts its
index for every request. There is no bounded cross-request adjacency cache.

This is explicit performance debt. Before reporting the registered 1,000- and
10,000-node P95 targets, consolidate validation/hydration, add a bounded cache
keyed by `(course_id, version_number, content_hash)`, report cold
materialization separately, and run the synthetic profile. No current latency
claim is supported.

## Verification and non-claims

Focused verification:

```powershell
cd backend
uv run pytest -q tests/test_concept_graph_path.py `
  tests/test_concept_graph_path_api.py
```

The current focused suite covers shortest stable traces, direction and
symmetric edges, unreachable versus bounded search, deterministic Local and
Learning results, evidence-bearing responses, closure bounds, cycle/duplicate/
missing-endpoint failures, exact-version APIs, invalid bounds, and Source drift.
An independent run passed all seven focused tests.

The final local non-regression run passed `1125` backend tests with `7`
skipped. An independent property-style review also cross-checked Trace distance
and Learning ancestor closure over 100 generated DAGs. The only warning was the
pre-existing Starlette/httpx deprecation.

This checkpoint supports the claim that a tested deterministic backend path
engine exists. It does not provide the G4 Path View, clickable graph evidence,
browser E2E, a public-course golden graph, measured path accuracy, measured
latency, improved learning outcomes, or superiority over NotebookLM.
