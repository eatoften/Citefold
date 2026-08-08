# Concept Graph Draft Lifecycle

- **Program:** G1.2
- **Status:** G1.2a-G1.2d implemented and locally verified; G1.3 pending
- **Depends on:** [G1.1 candidate substrate](concept-graph-substrate.md)
- **Projection identity:** [G1.2a Source generations](source-projection-generation.md)
- **Architecture owner:** [ADR-0008](../decisions/ADR-0008-evidence-grounded-concept-graph-and-deterministic-paths.md)
- **Next module:** immutable graph publication (G1.3)

## Responsibility

G1.2 turns grounded candidates into a reviewable draft graph without making
that draft authoritative. It owns append-only entity revisions, compare-and-
swap review, evidence currentness, aliases, merge/retirement history, and the
prerequisite acyclicity invariant.

```text
grounded G1.1 candidate
-> review or edit against an expected revision
-> append immutable revision and evidence/alias snapshot
-> CAS stable head
-> invalidate affected incident relations synchronously
-> accepted + current draft object
```

G1.2 does not generate candidates with an LLM, annotate CS336, publish a graph
version, expose path queries, or render graph UI. An accepted draft object is
eligible for G1.3 publication; it is not yet path-serving truth.

## Two correctness prerequisites

### Source projection generation

Text hash alone cannot identify an evidence projection. A Chunk may keep the
same text while its page, slide, time range, paragraph, ordinal, chunker
contract, or Source type changes. It may also drift and later return to the
same bytes; old reviewed evidence must not silently become current again.

Every ready Source projection therefore has:

```text
projection_generation_id  = opaque, never reused after a different projection
projection_manifest_hash  = deterministic hash of the complete projection
```

The canonical manifest includes the projection-contract version, Source type,
and every active Chunk sorted by stable ordering with its ID, ordinal, text
hash, canonical typed Locator, and chunker version. Publishing an identical
manifest retains the generation ID. Publishing a different manifest creates a
new ID in the same transaction as the projection replacement.

Concept and relation evidence snapshot the generation ID. Legacy graph
evidence with no trustworthy generation remains auditable but is ineligible
for acceptance or publication until regrounded. Content drifting back to an
old manifest does not revive its former generation.

### Relation endpoint revision binding

A relation revision must name the exact Concept revisions it was reviewed
against. Stable Concept IDs alone are insufficient because either endpoint can
advance independently.

```text
relation revision
-> source endpoint Concept ID + revision
-> target endpoint Concept ID + revision
```

Changing either Concept head makes the relation revision ineligible. An edit,
reground, review transition, or explicit stale transition appends stale
revisions for every incident current relation in the same write transaction
rather than waiting for a cleanup job. G1.2c applies the same primitive to
merge and retirement.

## Initial-create operation contract

Concept and relation candidate creation requires strict operation metadata:

```text
operation_id
actor
reason
candidate-specific payload
```

There is no `expected_revision` because the stable identity does not exist
yet. The service hashes `concept-graph-create-v1`, the actual route template,
course, entity type, create kind, and the complete normalized client request.
Generated entity/evidence IDs and timestamps are deliberately outside the
hash. For symmetric relations this hash captures the client's normalized
endpoint and support-role request before domain endpoint canonicalization;
reversing endpoints and roles under the same operation ID is therefore a
different request and returns `409`.

Inside one `BEGIN IMMEDIATE`, the store checks the course-scoped operation
ledger before Source, quote, endpoint, or relation-uniqueness validation. A
matching receipt returns the exact immutable revision created originally;
currentness fields are freshly derived and may differ after Source or endpoint
drift. Changed reuse returns `409`. A new operation inserts identity, revision,
complete children, and receipt atomically. Concurrent retries converge on one
aggregate. Distinct operations may create semantically duplicate Concept
candidates, while the permanent relation identity constraint still rejects a
duplicate relation and rolls back its unused receipt.

This required metadata is an intentional breaking change to an internal
experimental API. There is currently no frontend caller of these create
routes, so no public UI workflow is being silently broken.
`actor` is currently a client-supplied audit label, not an authenticated user
identity; authentication and principal binding remain a release concern.

## Revision and operation contract

Every post-create revision mutation carries:

```text
operation_id
expected_revision
actor
reason
operation-specific payload
```

One course-scoped `BEGIN IMMEDIATE` transaction performs:

1. look up `operation_id`;
2. return the stored result for the same canonical request hash, or `409` if
   the ID was reused for different input;
3. read and compare the stable head with `expected_revision`;
4. validate the transition, current Source generation, evidence bundle,
   endpoint revisions, uniqueness, and prerequisite acyclicity where needed;
5. insert revision `expected_revision + 1` and its complete evidence, alias,
   or endpoint-binding snapshot;
6. update the head with `WHERE current_revision = expected_revision` and
   require exactly one row;
7. store the bounded operation result and commit.

An error rolls back the revision, child rows, head movement, incident-edge
invalidation, and operation record together. Lost responses are recoverable by
replaying the same operation ID after restart.

For an accept/reject transition from candidate revision `N`, the new review
revision is `N + 1` and its `review_revision=N`. The field identifies what the
human actually reviewed; it is not a second optimistic-lock counter.

## State transitions

### Concept

| Current state | Operation | Resulting revision |
| --- | --- | --- |
| `active / candidate / current` | accept | `active / accepted / current` |
| candidate or stale active revision | reject | `active / rejected / preserved validity` |
| any current active revision | mark stale | same review state, `stale` |
| active revision | edit or reground | `active / candidate / current` |
| active revision | merge | `merged` with survivor redirect |
| active revision | retire | `retired` without redirect |

Merged and retired identities cannot be silently reactivated in G1.2.
Accepting a Concept requires at least one locatable evidence item whose saved
projection generation equals the current ready Source generation.

### Relation

| Current state | Operation | Resulting revision |
| --- | --- | --- |
| `candidate / current` | accept | `accepted / current` with endpoint bindings |
| candidate or stale revision | reject | `rejected` with preserved history |
| any current revision | mark stale | same review state, `stale` |
| eligible endpoints | edit or reground | `candidate / current` |

Acceptance rechecks both endpoints as same-course `active + accepted + current`,
their exact revisions, Source generations, typed support roles, rationale, and
relation identity. Accepting `A prerequisite B` additionally rejects the
transaction if the current accepted draft already contains a path `B => A`.
This internal cycle guard protects data integrity; it is not the G3 path API.

Every new or edited relation candidate stores its exact endpoint binding.
Relation review requests repeat both endpoint revisions, and acceptance
requires them to equal both the stored candidate binding and the current
eligible Concept heads. Review never silently rebinds a candidate. A legacy
candidate with no binding must be edited/regrounded before acceptance; it may
still be rejected because rejection records a judgment about the preserved
candidate rather than asserting current endpoint truth. A bound stale
candidate may likewise be rejected against its saved endpoint revisions even
after either endpoint head advances.

## Aliases, merge, and retirement

Aliases are a complete revision-owned snapshot. Each stores display text and a
normalized form produced by Unicode NFKC, whitespace collapse, case-folding,
and trimming. Normalized aliases are unique inside one Concept revision.
Different Concepts may share an alias because ambiguity is real and must be
resolved by context or review rather than a database-wide false constraint.

Merging source Concept `L` into survivor `S` requires both expected revisions,
same-course active identities, and non-self endpoints. The terminal `L`
revision stores the redirect while preserving `L`'s review/validity state and
copying its complete revision-owned aliases and evidence for audit. It never
copies them into or advances `S`, and it does not rewrite incident relation
endpoints. Every incident relation becomes stale atomically. Consolidating
evidence into `S` requires a separate candidate revision and review.

Retirement records no redirect, preserves the source revision snapshot, and
also stales incident relations. A Concept that is still the survivor of
another current merge redirect cannot itself merge or retire until the
dependency is resolved. This forbids redirect chains and cycles while allowing
many independent source Concepts to merge directly into one active survivor.

## Read model and failure contract

Historical revision reads return stored review/validity and immutable evidence.
Current reads additionally compute, without rewriting history:

```text
evidence_current
endpoint_revisions_current
eligible_for_publication
currentness_reasons[]
```

Reasons are stable bounded codes rather than exception text. Publication never
trusts a cached eligibility flag; G1.3 rechecks the complete snapshot.

| Condition | Response |
| --- | --- |
| course/entity/Source outside scope | `404` |
| malformed or forbidden transition, self merge, invalid role bundle | `422` |
| stale CAS, operation reuse, evidence drift, cycle, merge dependency | `409` |
| temporary SQLite write-lock exhaustion | `503` with bounded retry guidance |
| unexpected persistence failure | safe `500` without SQL or local paths |

### Implemented G1.2 HTTP surface

```text
POST  /courses/{course_id}/concepts
GET   /courses/{course_id}/concepts/{concept_id}/revisions/{revision}
PATCH /courses/{course_id}/concepts/{concept_id}
POST  /courses/{course_id}/concepts/{concept_id}/review
POST  /courses/{course_id}/concepts/{concept_id}/mark-stale
POST  /courses/{course_id}/concepts/{concept_id}/merge
POST  /courses/{course_id}/concepts/{concept_id}/retire

POST  /courses/{course_id}/concept-relations
GET   /courses/{course_id}/concept-relations/{relation_id}/revisions/{revision}
PATCH /courses/{course_id}/concept-relations/{relation_id}
POST  /courses/{course_id}/concept-relations/{relation_id}/review
POST  /courses/{course_id}/concept-relations/{relation_id}/mark-stale
```

Mutation request bodies forbid unknown fields. The operation hash uses
canonical JSON over protocol version, actual route template, course, entity
kind/ID, operation kind, and the complete validated request. Operation IDs are
opaque trimmed strings (maximum 100 characters); their internal whitespace is
not rewritten.

## Implementation slices

1. **G1.2a projection identity:** Source generation/manifest, evidence snapshot,
   legacy-stale policy, and drift/revert tests.
2. **G1.2b review core:** operation ledger, Concept/relation CAS revisions,
   aliases, endpoint bindings, historical reads, currentness DTOs, synchronous
   incident invalidation for implemented head changes, prerequisite cycle
   guard, concurrency, busy-lock, idempotency, and rollback recovery tests.
3. **G1.2c normalization integrity:** merge/retirement and reuse of the proven
   incident-invalidation primitive for those identity transitions, dual-head
   CAS, replayable receipts, and redirect-dependency protection.
4. **G1.2d reliable initial creation:** strict create metadata, canonical
   request hashes, replay-before-validation, atomic aggregate receipts, and
   same-operation concurrency convergence.

Each slice receives an independent commit and remote CI result. G1.2 is not
an immutable publication layer: G1.3 remains responsible for freezing an
authoritative graph version. G1.2 does not implement G2 fixtures/evaluation,
G3 paths, G4 UI, or LLM candidate generation.
The G1.1 Concept/relation create endpoints now use the v12-reserved
`concept_create` and `relation_create` ledger kinds. This adds reliable initial
creation without changing the permanent relation-identity rule or implying
G1.3 publication.

Workspace backup/restore already preserves the SQLite operation ledger as part
of the database snapshot. A packaged-workspace restore followed by create
replay remains a G1 release integration test rather than a claim of this
focused slice.

Migration v12 also makes revision-owned Concept/relation revisions, evidence,
and aliases reject in-place `UPDATE`s at the database boundary. Production
mutations append child-complete revisions and move stable heads; `DELETE`
remains available for explicit aggregate cleanup and course deletion.

## Required adversarial tests

- two concurrent reviews with the same expected revision yield one success;
- same operation replay returns the prior result; changed payload returns
  `409` and adds no revision;
- initial-create replay precedes Source/endpoint validation, concurrent retries
  converge, and malformed or dangling receipts fail safely;
- Source update before/after acceptance never admits mismatched generation;
- locator-only drift and drift-then-revert keep old evidence ineligible;
- two serialized prerequisite accepts that would jointly form a cycle cannot
  both succeed;
- Concept edit/merge racing relation acceptance cannot publish stale endpoint
  bindings;
- merge/retire stales every incident relation or rolls back completely;
- injected failure at revision, child-row, head, invalidation, and operation
  steps leaves no partial write;
- restart replay, `foreign_key_check`, `quick_check`, backup/restore, and all
  pre-existing Source/Chat/Card/Note behaviors pass.
