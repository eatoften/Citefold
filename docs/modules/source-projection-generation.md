# Source Projection Generation

- **Program:** G1.2a
- **Status:** implemented; full backend/frontend verification complete
- **Depends on:** [G1.1 candidate substrate](concept-graph-substrate.md)
- **Next slice:** G1.2b review/CAS lifecycle (implemented)

## Responsibility

This checkpoint gives every persisted Source projection an identity that cannot
silently return after the projection drifts away and later returns to the same
text. A text hash is not sufficient: page, slide, timestamp, ordinal, Chunk
type, or chunker changes can all change what an evidence address means.

The one projection publication boundary is
`course_source_store.replace_*_projection`. Video transcript, document asset,
whole-course reconciliation, and Notebook Note publication all pass through
this boundary. It atomically publishes Source metadata, projection identity,
active Chunks, removals, and index-staleness changes in one SQLite
transaction. Notebook Note snapshot and projection publication also share the
caller's transaction. Video/document root creation and later projection sync
remain separate workflow steps; this module does not claim cross-step
atomicity for those roots.

## Identity contract

Every persisted Source has:

```text
projection_generation_id  opaque identity for one consecutive projection
projection_manifest_hash  deterministic SHA-256 of the complete projection
```

The canonical manifest is compact, key-sorted JSON and contains:

- projection-contract version;
- stable Source ID and Source type;
- every active Chunk ordered by `(ordinal, id)`;
- Chunk ID, Chunk type, ordinal, recomputed text hash, typed canonical Locator,
  and chunker version.

The store rejects inactive inputs, duplicate IDs, duplicate ordinals, a Chunk
ID already owned by another Source, invalid typed Locators, non-finite Locator
numbers, and a caller-supplied text hash that does not equal SHA-256 of the
text. Canonical Source IDs must continue to name the same origin root. The
database also enforces unique active ordinals and unique non-null generation
IDs.

An identical consecutive manifest retains its generation. A changed manifest
gets a new UUID in the same transaction as replacement. Therefore the sequence
`A -> B -> A` has three distinct generations even though the first and last
manifest hashes match. A whole-workspace backup/restore intentionally
preserves the backed-up generation because it restores that exact database
snapshot rather than republishing a projection.

## Graph evidence snapshot and currentness

New Concept and relation evidence snapshots the current Source generation.
Evidence is current only when all of these still hold:

1. the evidence course and Source course match and the course is not deleted;
2. the Source's video job, source asset, or Notebook Note root exists and is
   not deleted;
3. the Source is ready and its generation equals the snapshot;
4. the exact Chunk is active under the same Source;
5. Source type, Chunk text hash, typed canonical Locator, and exact quote still
   match the current Chunk.

Reads expose `projection_is_current` and bounded
`projection_currentness_reasons`. Historical evidence is never rewritten.
Soft-delete makes evidence ineligible; restoring the unchanged same Source
root can make it eligible again. Purging the Source or republishing a changed
projection cannot revive the old generation.

Migration v10 assigns a generation and deterministic manifest to every
existing Source after validating persisted text hashes and active ordinals.
Existing v9 Concept/relation evidence keeps a nullable generation and reads as
`legacy_projection_generation` until a later revision regrounds it. The
migration does not guess that historical evidence was captured from the newly
assigned generation.

## Transaction and failure contract

- single-Source and whole-course replacements acquire `BEGIN IMMEDIATE`;
- caller-owned Note publication uses its existing immediate transaction;
- generation/manifest, Chunk replacement, removed Chunk embeddings, and
  index state commit or roll back together;
- a globally stable Chunk ID cannot be reparented by `ON CONFLICT`;
- migration hash/ordinal validation, schema changes, backfill, indexes,
  triggers, and migration ledger update roll back together;
- source reads and graph history remain available after Source purge, but
  purged evidence is explicitly ineligible.

## Verified cases

- canonical ordering/key/numeric normalization and non-finite rejection;
- identical republish, locator-only drift, and `A -> B -> A`;
- ID, Source type, Chunk type, ordinal, text hash, Locator, and chunker drift;
- stale caller hash, duplicate ID/ordinal, inactive input, cross-Source ID,
  concurrent serialized replacement, and injected publication rollback;
- v9 upgrade, nullable legacy evidence, idempotency, validation failure, and
  injected migration rollback;
- generation snapshots on Concept and relation evidence;
- video, document, and Notebook Note soft-delete/restore/purge currentness;
- course deletion and Source course-move fences;
- backup/restore preservation of the exact backed-up generation;
- existing Source, Chat, Note, citation, backup, and graph compatibility.

## Deliberate boundary

G1.2a itself does not own review transitions. G1.2b now consumes this
projection identity for append-only edit/review/stale operations, aliases,
relation endpoint revision binding, synchronous incident invalidation, and
the prerequisite acceptance guard. Merge/retirement, graph publication, path
algorithms, LLM candidate generation, and UI remain later checkpoints so
projection identity cannot be confused with semantic review state.
