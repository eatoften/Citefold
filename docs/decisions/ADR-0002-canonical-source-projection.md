# ADR-0002: Project Existing Evidence into a Canonical Source Index

- **Status:** Accepted
- **Date:** 2026-07-27
- **Decision owners:** Project maintainer and Codex implementation agent

## Context

The repository already has two mature evidence stores:

- video uploads, transcript segments, and semantic transcript chunks are owned
  by the job pipeline;
- PDF, PPTX, DOCX, Markdown, text, audio, and video imports are owned by source
  assets and source units.

These stores have different lifecycles and locator shapes. Product retrieval
searched generated cards, so a user could not select all original course
materials through one contract. Replacing the existing stores in one migration
would put working upload, transcription, Study, and card workflows at risk.

The installed course corpus also contains about 5.33 GB of video. A design that
copies or hashes each media file during schema migration would make application
startup slow and fragile without improving text retrieval.

## Decision

1. Keep `jobs` / `transcript_chunks` and
   `source_assets` / `source_units` as the origin stores.
2. Add a canonical, query-oriented projection:
   `sources`, `source_chunks`, and `source_chunk_embeddings`.
3. Use deterministic identifiers so later citations remain stable:
   `job:<id>`, `asset:<id>`, `transcript_chunk:<id>`, and
   `source_unit:<id>`.
4. Treat knowledge cards as derived artifacts. They are not canonical Sources
   and the legacy card retrieval API remains compatible.
5. Represent locations as a versioned Pydantic discriminated union:
   `video_time`, `pdf_page`, `ppt_slide`, `docx_paragraph`, or
   `text_section`.
6. Backfill only stored metadata and extracted text during migration. Do not
   open media files and do not generate model embeddings in a schema migration.
7. Before a pending file-database migration, run `PRAGMA quick_check`, create a
   SQLite-native backup, and validate that backup.
8. Update the projection incrementally from origin-store write workflows.
   Serialize projection sync, deletion, course moves, and reconciliation in
   the process so an old origin snapshot cannot win. Reconcile all sources once
   on application startup as a repair path; HTTP reads must not silently
   rebuild or write the projection.
9. Persist source-chunk vectors by chunk and model. Index status is valid only
   for the active model, vector dimension, and chunk text hash.
10. Give each index attempt a unique generation token. Beginning a newer
    attempt replaces the token; source edits and course moves invalidate it.
    Commit or fail state only when the expected course and generation still
    match, so an older task cannot overwrite a newer result.
11. Commit an index with a transactionally checked compare-and-swap. If a
    source changes while vectors are being computed, keep it stale and require
    a retry rather than publishing mixed-version evidence.
12. Bind final search chunk and embedding reads to `sources.course_id` in SQL.
    A source moved after initial validation must not be returned from its former
    course.

## Alternatives considered

### Build a read-only union facade

Rejected as the product contract. A union view would avoid duplicate text, but
it would not provide stable canonical chunk IDs, persistent per-source index
state, enable/disable selection, or a safe anchor for future message citations.

### Move videos into `source_assets`

Rejected for this stage. It would duplicate lifecycle ownership, require
expensive media inspection or hashing, and risk breaking the working
transcription pipeline. A video job can satisfy the Source contract without
changing its origin store.

### Rewrite all evidence stores at once

Rejected. A flag-day migration would unnecessarily couple source-first product
work to every upload, Study, card, and review workflow.

### Generate embeddings during database migration

Rejected. Schema migrations must be deterministic, offline, and recoverable.
Model availability, downloads, memory, and inference latency do not satisfy
those constraints.

### Reconcile the projection on every read

Rejected after review. It turns reads into course-wide writes, creates lock
contention, and can let an older snapshot overwrite a concurrent update.

## Consequences

Positive:

- one API can list, select, index, and search videos and documents together;
- future citations can persist canonical source and chunk IDs;
- origin workflows and legacy APIs remain compatible;
- migrations are bounded by extracted text rather than media size;
- indexing can skip unchanged chunks and detect concurrent edits.
- concurrent index attempts and course moves cannot publish an older status or
  cross the course boundary;

Costs and risks:

- evidence text exists in both the origin store and its query projection;
- every origin mutation needs an explicit projection sync hook;
- startup reconciliation adds bounded local work and remains a repair
  mechanism, not a substitute for write-through consistency;
- the current index request is synchronous and dense-only;
- model identity currently means name plus vector dimension; an unchanged
  dimension cannot reveal replacement weights without a future fingerprint;
- projection serialization is process-local and matches the single-process
  desktop runtime, not a future multi-worker server;
- full referential integrity is enforced in services because the legacy schema
  does not enable foreign keys globally.

## Validation

This decision is successful when:

1. existing databases migrate without changing jobs, cards, or card vectors;
2. migration never opens a video file and can restore from a validated backup;
3. video and document chunks expose typed, round-trippable locators;
4. source reads are pure and origin writes update the projection;
5. unchanged chunks are not re-embedded;
6. model/dimension changes and concurrent source edits cannot report a false
   ready state;
7. mixed-source retrieval respects course, source-selection, and enabled-state
   boundaries;
8. all existing backend tests and the frontend quality gates remain green.
