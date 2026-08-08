# Concept Graph Substrate

- **Program:** G1
- **First slice:** G1.1 grounded manual candidates
- **Status:** G1.1 implemented and locally verified; full G1 in progress
- **Architecture owner:** [ADR-0008](../decisions/ADR-0008-evidence-grounded-concept-graph-and-deterministic-paths.md)
- **Evidence rules:** [Graph annotation protocol](../graph-annotation-protocol.md)
- **Projection identity:** [G1.2a Source generations](source-projection-generation.md)

## Responsibility

This module introduces a canonical Concept layer without renaming or replacing
Cards. Its first slice persists human-proposed Concepts and typed relations
together with immutable evidence snapshots from the shared
`CourseSourceChunk + Locator` evidence backbone.

The slice proves one vertical contract:

```text
current CourseSourceChunk
-> grounded manual candidate request
-> course/source/quote validation
-> one SQLite aggregate transaction
-> course-scoped Concept/Relation read API
```

It deliberately does not publish an authoritative graph. A G1.1 object is
always `review_status=candidate`, `validity_status=current`, and
`proposal_origin=human`. This slice stores immutable revision history but
exposes no revision mutation API. Later G1 slices own CAS review transitions,
aliases, merge/retirement workflows, immutable graph versions, prerequisite
cycle validation, and atomic publication.

## Why this is separate from Card

| Object | Product responsibility | Evidence authority |
| --- | --- | --- |
| Source/Chunk/Locator | preserve original material and an addressable projection | authoritative route to the material |
| Concept | stable, course-scoped semantic identity | must cite current Source evidence |
| ConceptRelation | typed, reviewed structure between Concepts | must cite the relation assertion or both endpoints |
| Card | regenerable learning presentation that may combine several Concepts | secondary provenance only |

`KnowledgeCard.card_kind="concept"` remains a Card label. It is never treated
as a Concept ID or accepted Concept revision.

## Domain aggregates

### Concept candidate

The create boundary accepts:

```text
preferred_name
short_definition
evidence[]:
  chunk_id
  exact quote
```

The server resolves and snapshots:

```text
course_id
source_id
chunk_id
chunk_text_hash
typed locator
quote
```

At least one and at most 32 evidence items are allowed. A quote is bounded to
16,000 characters. The chunk must be active, belong to a
Source in the requested course, and contain the exact quote. The
client cannot supply a trusted locator or hash.

### Relation candidate

The create boundary accepts:

```text
source_concept_id
target_concept_id
relation_type
support_basis
rationale
evidence[]:
  chunk_id
  exact quote
  support_role
```

Supported relation types and direction are:

| Type | Stored meaning |
| --- | --- |
| `prerequisite` | source should be understood before target |
| `part_of` | source is a component of target |
| `example_of` | source is an example of target |
| `related` | reviewed symmetric association |
| `contrast_with` | reviewed symmetric comparison/opposition |

`related` and `contrast_with` endpoints are canonicalized before persistence,
so the same symmetric edge cannot be created twice in reverse order. Directed
relations retain the requested direction.

Evidence roles depend on `support_basis`:

- `source_asserted` allows only `relation_assertion` items;
- `pedagogical_inference` allows only `source_endpoint` and `target_endpoint`
  items, requires both roles plus a human rationale, and each item must match
  the corresponding endpoint Concept revision's complete evidence fingerprint
  `(source_id, chunk_id, chunk_text_hash, quote, canonical locator)`;
- model confidence, embedding similarity, or a Card ID cannot satisfy either
  rule.

## Layer boundaries

```text
API
  validates transport shape and translates known service errors
Service
  resolves course, fixes candidate provenance, and canonicalizes edge order
Store
  rechecks endpoints, current chunks, quote/fingerprint/roles, and persists
  identity + revision + evidence in one transaction
SQLite migration
  supplies CHECK/unique/index constraints and additive compatibility
```

The store must not call an LLM, parse files, infer course ownership, or create
partially grounded objects. Existing Card, CardRelation, Topic, Source, Chat,
and citation tables remain unchanged.

## Storage contract

G1.1 adds stable identity heads, immutable revisions, and revision-owned
evidence:

```text
concepts                    -> current_revision pointer
concept_revisions           -> immutable Concept state
concept_evidence            -> owned by one Concept revision
concept_relations           -> stable course/type/endpoints identity
concept_relation_revisions  -> immutable review/support state
relation_evidence           -> owned by one relation revision
```

The current pointer is protected by a deferred composite foreign key, allowing
identity and revision to be created atomically while preventing a committed
head from naming a missing revision. Detail reads join only the current
revision and its evidence. Old revisions and evidence remain auditable.

Evidence tables retain snapshot values even if a later Source projection is
replaced. G1.2a additionally snapshots the Source projection generation and
computes currentness from course/root availability, generation, active
same-Source Chunk, Source type, hash, typed Locator, and exact quote.
Historical audit never depends on the old Chunk row still existing, and
legacy v9 evidence without a trustworthy generation is explicitly ineligible
until regrounded.

Required database protections include:

- positive revision numbers;
- enumerated review, validity, proposal, relation, support, and role values;
- non-empty names, definitions, quotes, rationales, and identities;
- no self-relations;
- one permanent normalized relation identity per course, type, and endpoints;
- indexes for course-scoped lists and aggregate evidence reads.

Rejected relations evolve through a later revision of the same stable
identity; they are not recreated under a second identity. Prerequisite cycle
validation remains reserved for the later acceptance/publication transaction.

Course ownership, quote containment, support-role completeness, and atomic
aggregate writes are rechecked in the service/store transaction rather than
trusted only to Pydantic.

## Failure contract

| Condition | API behavior |
| --- | --- |
| course, Concept, Source, or Chunk does not exist in scope | `404` |
| full evidence fingerprint drift or a duplicate stable relation exists | `409` |
| empty/mismatched quote, self edge, invalid role bundle, or malformed request | `422` |
| unexpected database/model failure | safe `500` without local paths or raw SQL |

No failed request may leave an aggregate without its required evidence or an
evidence row without its aggregate.

## G1.1 acceptance

- clean install and upgrade from migration v8 both create the schema;
- an injected migration failure rolls back tables and migration ledger state;
- Concept and relation aggregate writes are atomic and duplicate behavior is
  explicit;
- cross-course chunks and Concept endpoints are rejected;
- quote/hash/Locator snapshots come from the server-owned active Chunk;
- reverse symmetric duplicates and self edges are rejected;
- support-basis evidence roles are enforced;
- list reads are summaries ordered by stable ID with `limit <= 20` and a
  stable cursor; detail reads return only current-revision evidence;
- list/get reads never cross course boundaries;
- all pre-existing backend tests remain green.

Focused commands are recorded with the implementation commit. Full G1 later
adds review/currentness transitions, aliases/merge history, draft CAS,
prerequisite acyclicity, immutable graph versions, and publication tests.
