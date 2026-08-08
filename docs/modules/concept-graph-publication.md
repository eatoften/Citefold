# Immutable Concept Graph Publication

- **Program:** G1.3
- **Status:** implemented and verified as the G1 authority boundary
- **Depends on:** [G1.2 draft lifecycle](concept-graph-draft-lifecycle.md)
- **Architecture owner:** [ADR-0008](../decisions/ADR-0008-evidence-grounded-concept-graph-and-deterministic-paths.md)
- **Consumers:** future G3 path/query services through
  `require_current_authoritative_version`

## Responsibility

G1.3 converts a mutable, reviewed draft graph into a self-contained immutable
version. Draft rows remain authoring state; only a sealed version may become
path-serving truth.

```text
current draft heads + current Source authority + immutable review receipts
-> deterministic preview and manifest hash
-> BEGIN IMMEDIATE + active-head/draft compare-and-swap
-> complete normalized snapshot children
-> version seal + monotonic active head + operation receipt
-> immutable historical read model
```

This module does not generate Concepts, answer chat questions, calculate
learning paths, or render graph UI. Those consumers must not bypass the
published-version guard.

## Selection and fail-closed policy

The Concept set contains only current heads in
`active + accepted + validity=current`. Candidate, rejected, stale, merged,
retired, and tombstoned Concept heads are non-authoritative and excluded. The
relation set contains every current head in
`accepted + validity=current`. Excluded draft rows and their counts do not
affect the manifest.

Every selected Concept must have current evidence and exactly one matching
immutable `concept_review` receipt. Every selected relation is validated; an
invalid accepted relation blocks the whole publication and is never silently
pruned. Validation requires:

- both endpoint identities are included at their exact bound revisions;
- symmetric identity endpoints use canonical ordering and no relation is a
  self-loop;
- every evidence snapshot resolves to the active Chunk in the same current
  Source projection, including generation, type, text hash, canonical Locator,
  and exact quote;
- source-asserted evidence uses only `relation_assertion`; pedagogical evidence
  covers both endpoint roles and matches the corresponding Concept evidence
  fingerprint;
- accepted review receipt actor and timestamp equal the revision, its exact
  result triple names the revision, its request hash is lowercase SHA-256, and
  `review_revision = result_revision - 1`;
- the selected prerequisite subgraph is acyclic;
- at least one Concept is selected.

Preview issues use stable codes and entity coordinates. Responses return at
most 100 issues plus exact total/truncation fields. The manifest stores the
bounded stable sample, exact count, and a streaming SHA-256 over length-prefixed
canonical issue coordinates in deterministic validation order, so invalid-
draft CAS identity remains bounded.

## Hash protocols

`concept-graph-content-v1` hashes compact, sorted-key UTF-8 JSON containing the
complete immutable Concept, alias, Concept evidence, relation, endpoint
binding, relation evidence, model provenance, and review receipt provenance.
It excludes version number, publisher metadata, publication time, and dynamic
Source currentness. The same exact immutable materialization therefore has the
same content hash across version attempts. Runtime entity/evidence IDs,
projection generations, review receipt IDs/hashes, and revision timestamps are
part of the snapshot; independently imported logical gold graphs need not hash
equally.

`concept-graph-draft-manifest-v1` hashes the selected full content, all
accepted/current relations even when invalid, endpoint-head observations,
stable issue identity, and the content hash. It deliberately excludes
non-authoritative rows and wall-clock computation time.

`concept-graph-publication-request-v1` separately binds an operation receipt
to the normalized request, course, and route. Generated version numbers and
timestamps are not client-controlled hash input.

`concept-graph-concept-aggregate-v1` and
`concept-graph-relation-aggregate-v1` are domain-separated per-owner hashes.
Each commits every published semantic parent field plus its canonically ordered
aliases/evidence. They are stored beside the parent row so a bounded child-page
read can detect same-row-count corruption without loading the whole graph. The
derived aggregate hashes are deliberately excluded from the unchanged
`concept-graph-content-v1` payload: that protocol continues to hash the
underlying snapshot values, and full reads independently recompute both layers.

All hashes use SHA-256 over compact sorted UTF-8 JSON with non-ASCII text
preserved. Preflight counts, UTF-8 byte estimates, and final canonical byte
checks cap publication materialization at 64 MiB. Oversized live Chunk or
Locator observations fail closed in both draft and historical-authority
checks; a version cannot be published already stale by the read policy.

## Atomic publication protocol

The strict request body requires all fields, including an explicitly nullable
`expected_active_version`:

```text
operation_id
expected_active_version
expected_draft_manifest_hash
actor
reason
```

Inside one `BEGIN IMMEDIATE` transaction the store:

1. rechecks that the course is active;
2. replays a matching publication receipt before draft, Source, or CAS checks,
   while changed operation-ID reuse returns `409`;
3. compares the active version with `expected_active_version`;
4. rebuilds the complete draft manifest in the same SQLite snapshot and
   compares its hash;
5. rejects blocking issues, an empty Concept set, or content identical to the
   active version;
6. inserts all snapshot children against deferred foreign keys;
7. inserts the version row, whose seal trigger verifies parent/head state and
   every declared child count;
8. advances the head by exactly one with compare-and-swap semantics;
9. inserts the immutable publication receipt and commits.

Any failure rolls back children, seal, head, and receipt. Same-operation
retries converge on the original version even if current draft or Source state
has since changed. Distinct concurrent operations against one preview permit
only one head advance.

## Immutable storage and reads

The version tables copy all display, evidence, binding, provenance, and review
data. They have no foreign keys to mutable draft or Source tables. Child rows
are inserted only before the seal; sealed versions, children, heads, and
publication receipts reject update or direct delete. Permanent course purge
remains the only cascade-delete path.

Historical reads remain available after draft edits or Source drift. They
recompute `source_authority_current` against current Source projections without
rewriting history. The `/current` endpoint returns a structured `409` when the
active snapshot is no longer Source-authoritative; historical metadata and
children remain readable and expose the dynamic false state. G3 must use the
same internal authority guard.

G3 must also perform its authority check and adjacency load in one read
transaction. Calling the guard and then loading edges in a later transaction
would reintroduce a Source/head TOCTOU window; G1.3 intentionally defines this
consumer contract but does not yet implement G3 adjacency queries.

Multi-query reads use explicit read transactions. Version-list authority is a
single bulk SQL scan with windowed per-version counts and at most 100 returned
issues per version; it does not perform a query per version or materialize all
evidence in Python. The shared SQLite currentness UDF receives the exact 15
snapshot/live values, returns only a boolean into query rows, and uses bounded
LRU caches (256 exact evidence comparisons and 64 live observations). Long
Chunk text and Locator values therefore do not accumulate in Python result
sets, while repeated evidence observations avoid redundant parsing and hashing.

Child pages use bounded keyset cursors. They verify the seal/count invariants
and recompute the domain-separated aggregate hash for every returned Concept or
Relation, avoiding a full-graph hash while still detecting parent, alias, or
evidence mutation with an unchanged row count. Metadata, current-version, and
operation-replay reads recompute every aggregate hash and then the unchanged
full canonical content hash. Database triggers prevent ordinary sealed-row
mutation; count, per-aggregate, and full-content verification are independent
fail-closed layers for externally corrupted databases.

## HTTP surface and failures

```text
GET  /courses/{course_id}/concept-graph/publication-preview
POST /courses/{course_id}/concept-graph/versions
GET  /courses/{course_id}/concept-graph/versions
GET  /courses/{course_id}/concept-graph/versions/current
GET  /courses/{course_id}/concept-graph/versions/{version_number}
GET  /courses/{course_id}/concept-graph/versions/{version_number}/concepts
GET  /courses/{course_id}/concept-graph/versions/{version_number}/relations
```

| Condition | Response |
| --- | --- |
| inactive course or missing version | `404` |
| malformed strict body or cursor | `422` |
| stale active/draft CAS, operation reuse, invalid/no-change draft | `409` |
| active version Source authority drift | structured `409` |
| configured graph/byte bound exceeded | `413` |
| SQLite lock exhaustion | `503` with `Retry-After: 1` |
| corrupt snapshot or unexpected persistence failure | safe `500` |

## Verification scope

Dedicated tests cover functional preview/publish/replay and paged historical
reads; course isolation and route ordering; Source drift; strict request and
production preview/publish golden vectors for request, content, manifest, and
both aggregate protocols; excluded-head manifest stability; concurrent
distinct and identical operations; rollback and same-operation recovery after
every write stage; missing receipts and invalid accepted relations; UTF-8 and
live-observation bounds and cache reuse; sealed-row guards; aggregate-only
corruption; and same-count parent/alias/evidence corruption on child pages.
Migration tests separately prove schema compatibility, lowercase 64-character
aggregate hashes, child ordinals bounded to `0..31`, and database triggers. A
workspace-backup integration test additionally opens the generated archive and
verifies that the version, active head, sealed evidence, content hash, and
publication receipt are all present.

## Maintainer debt: G1.3b internal split

The publication boundary is separate from the mutable draft store, but its
first correctness slice deliberately keeps transaction orchestration,
canonical snapshot encoding, draft validation, sealed reads, and dynamic
Source authority in one internal store module. That module is now large enough
that further feature work must begin with a behavior-preserving G1.3b split:

1. `publication_draft` owns bounded selection, validation, issue streaming,
   cycle checks, and content/manifest construction;
2. `publication_snapshot` owns the one canonical codec plus sealed child
   insertion, integrity verification, metadata, and paged historical reads;
3. `publication_authority` owns the single shared Source/Chunk/generation/hash/
   Locator/quote currentness predicate and bulk historical evaluator.

The existing publication service/store functions remain the stable facade and
transaction owner. The split must move the currentness and canonicalization
primitives rather than copy them, preserve golden hashes byte-for-byte, and run
the full concurrency/fault/integrity suite before any feature is added. This is
recorded debt rather than a late mechanical refactor in G1.3 because changing
module seams after the transaction and hash contracts were verified would add
release risk without changing user-visible capability.
