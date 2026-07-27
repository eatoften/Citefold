# Productization Log

Last updated: 2026-07-27

This append-only log records verified product engineering work. It exists to
preserve the reasoning behind the implementation, not to advertise unverified
features. The forward-looking source of truth is
[`roadmap.md`](roadmap.md); major architectural decisions are recorded in
[`decisions`](decisions).

## Entry format

Each completed stage records:

- the user outcome and reason for doing the work now;
- scope and explicit non-goals;
- decisions, alternatives, and consequences;
- technology used and the responsibility of each component;
- problems encountered, root causes, and resolutions;
- exact automated and manual verification;
- known limitations, commit subject, and next gate.

## P0.0 — Product contract and engineering record

**Status:** Complete

**Date:** 2026-07-27

**Branch:** `codex/notebooklm-product-core`

### User outcome

The repository now has one current product direction and a staged definition of
done. Research remains available, but it can no longer silently compete with
the product roadmap for the next engineering task.

### Why this stage now

The application already contains strong learning primitives—timestamped cards,
versioned Study documents, FSRS Review, Course Map, and a graph—but the visible
Ask workflow only retrieves similar cards. Starting with chat UI or another
retrieval experiment would have extended the existing card-first split and
created another migration later.

The product needs a stable contract before introducing persistent sources,
conversations, citations, and user data that must survive upgrades.

### Scope and non-goals

Included:

- one active product roadmap with independently verifiable stages;
- an append-only implementation journal;
- an architecture decision for the local-first source-first direction;
- explicit archival status for the obsolete MVP progress document;
- an explicit pause and resume gate in the research master plan.

Not included:

- no runtime feature is claimed by this stage;
- no existing research artifact is modified or deleted;
- no README product claim is changed before the grounded workflow exists.

### Decisions and evidence

1. **Course is the notebook boundary.** Existing course IDs already scope
   videos, source assets, cards, topics, Study documents, and review queues.
2. **Original materials are Sources; cards are derived artifacts.** This keeps
   citations anchored to a transcript or document rather than to an LLM-written
   summary.
3. **The target information architecture is Sources / Chat / Studio.** Course
   Map, Review, and graph capabilities are retained and reorganized instead of
   deleted.
4. **Local-first remains a product constraint.** Authentication, cloud sync,
   public sharing, and native mobile applications are not P0 requirements.
5. **A stage is a Git boundary.** Code, tests, documentation, and verification
   travel together in one independently reviewable commit.

See
[`ADR-0001`](decisions/ADR-0001-source-first-local-course-notebook.md).

### Technology baseline

| Component | Technology | Product responsibility |
| --- | --- | --- |
| Desktop shell | Tauri 2 / Rust | Package and supervise the local application |
| UI | React 19 / TypeScript / Vite | Source, chat, citation, and learning workflows |
| API | FastAPI / Pydantic | Typed local HTTP boundary and workflow validation |
| Persistence | SQLite | Durable local source of truth |
| Media | FFmpeg / ffprobe / faster-whisper | Validate video and produce timestamped transcripts |
| Retrieval | sentence-transformers MiniLM | Local semantic indexing baseline |
| Generation | Ollama-compatible local LLM client | Grounded answers and learning outputs |
| Verification | pytest, TypeScript build, ESLint | Regression and release gates |

The table records responsibilities rather than treating a list of libraries as
an architecture.

### Problems encountered

- `docs/progress.md` still described SQLite and desktop packaging as future
  work and showed early test counts. It is now visibly retained as a historical
  archive so a recruiter does not mistake it for current status.
- `docs/roadmap.md` marked the citation-first assistant as deferred, which
  contradicted the new product priority. It now points to active stages
  P0.1-P0.3.
- The research plan and product roadmap both looked like active master plans.
  The research plan now has explicit pause and resume gates.
- GitHub CLI is not installed in the local environment. Stage pushes use the
  configured Git remote; pull-request creation is outside the requested scope.

### Verification

Repository and Git baseline:

```text
branch: codex/notebooklm-product-core
base:   main at 1e3f279
remote: origin -> https://github.com/eatoften/Video_Course_Cards
working tree before this stage: clean
```

Focused pre-change backend regression:

```text
uv run pytest -q \
  tests/test_rag_schema.py \
  tests/test_rag_retriever.py \
  tests/test_rag_service.py \
  tests/test_rag_api.py \
  tests/test_source_asset_parser.py \
  tests/test_transcript_store.py \
  tests/test_transcript_api.py \
  tests/test_transcript_chunks.py \
  tests/test_learning_documents_api.py

33 passed, 1 warning
```

Documentation review confirmed that the active roadmap, historical progress
file, research pause, ADR, and this log refer to one another consistently.

### Known limitations

- P0.0 changes project governance only; the runtime is still card-first.
- The current Ask panel still performs retrieval without answer generation.
- Exact commit SHA is intentionally left to Git history and the delivery
  report; a commit cannot reliably contain its own final SHA.

### Git checkpoint

Intended commit subject:

```text
docs(product): define NotebookLM-inspired productization gates
```

### Next gate

P0.1 must expose video transcript chunks and imported document units through
one typed Source/Chunk/Locator contract, build a persistent incremental index,
preserve the legacy `/rag/retrieve` API, and pass migration plus mixed-source
retrieval tests.

## P0.1 - Unified Sources

**Status:** Complete

**Date:** 2026-07-27

**Branch:** `codex/notebooklm-product-core`

### User outcome

The backend can now treat a course's videos and imported documents as one
selectable evidence library. A client can list Sources, inspect their chunks,
enable or disable them, index them incrementally, and retrieve mixed video and
document evidence with an exact typed location.

This is the evidence foundation for Chat and citations. It does not yet claim a
new visible Sources page or a generated answer.

### Why this stage now

The old Ask flow searched generated knowledge cards. Video transcripts and
local documents had separate schemas, lifecycles, and locators. Building
conversation persistence first would have anchored citations to that split and
made future answer history difficult to migrate safely.

The first irreversible user data in the new product loop should therefore be a
stable Source and chunk identity, not an LLM response.

### Scope and non-goals

Included:

- canonical `Source`, `SourceChunk`, and versioned typed `Locator` models;
- unified video, audio, PDF, PPTX, DOCX, Markdown, and text source projection;
- deterministic source/chunk IDs suitable for persistent citations;
- per-source enable/disable selection;
- persistent source-chunk embeddings with incremental skip behavior;
- course- and source-scoped mixed evidence search;
- a versioned migration, pre-migration backup, and startup repair;
- compatibility with jobs, source assets, cards, Study, and `/rag/retrieve`.

Not included:

- no answer generation, multi-turn history, or abstention policy;
- no citation viewer or frontend source-management workspace;
- no durable background indexing queue, cancellation, or progress UI;
- no hybrid lexical retrieval, reranking, OCR redesign, or web sources.

### API contract

```text
GET   /courses/{course_id}/sources
GET   /sources/{source_id}
GET   /sources/{source_id}/chunks?limit=&offset=
PATCH /sources/{source_id}                    { enabled }
POST  /courses/{course_id}/sources/index
POST  /courses/{course_id}/sources/search
```

The locator union is:

```text
video_time | pdf_page | ppt_slide | docx_paragraph | text_section
```

Video-time locators resolve to the originating job when one exists and fall
back to the imported asset ID for standalone audio/video evidence.

### Decisions and alternatives

The chosen design is a canonical query projection over the existing origin
stores. Video jobs and document assets remain authoritative; `sources`,
`source_chunks`, and `source_chunk_embeddings` provide the stable product
contract.

This was chosen over:

- a pure SQL union, which could not own persistent selection and indexing
  state or stable future citation IDs;
- moving 5.33 GB of installed videos into `source_assets`, which would
  duplicate lifecycle ownership and make migration unnecessarily expensive;
- a flag-day rewrite, which would risk mature upload, Study, card, and review
  workflows;
- model inference during migration, which would make schema upgrades depend on
  model availability and memory.

Cards remain derived artifacts. The legacy card retrieval endpoint stays
available, but future factual Chat citations will point to original source
chunks.

See
[`ADR-0002`](decisions/ADR-0002-canonical-source-projection.md).

### Technology and responsibilities

| Component | Technology | Responsibility |
| --- | --- | --- |
| Contract | Pydantic discriminated unions | Validate locator kind and fields at the API boundary |
| API | FastAPI | Course isolation, selection, pagination, index, and search endpoints |
| Origin data | Existing job/transcript and asset/unit services | Remain the authoritative evidence lifecycle |
| Query projection | SQLite | Stable source IDs, chunk text, status, selection, and vector metadata |
| Migration | SQLite savepoint + backup API + `quick_check` | Atomic forward upgrade with a recoverable pre-migration copy |
| Embeddings | Existing sentence-transformer abstraction | Local dense vectors and cosine retrieval |
| Consistency | Lock-serialized projection writes + generation-token compare-and-swap | Prevent stale projection/task overwrites and publish only for the expected course/chunk generation |
| Verification | pytest, compileall, ESLint, TypeScript, Vite | Regression, schema, API, and build gates |

### Problems encountered and resolutions

1. **Migration cost was initially easy to underestimate.** The real workspace
   contains five videos totaling 5,328,217,687 bytes. Migration now reads only
   database metadata and extracted UTF-8 chunk text; it never opens or hashes a
   media file.
2. **A read-time full reconciliation caused writes and race risk.** Source GET
   requests are now pure. Origin mutation workflows update their own
   projection; one startup reconciliation remains as an explicit repair path.
3. **Embedding outside a transaction created a time-of-check/time-of-use
   window.** Index commit now rechecks course ownership, source existence,
   readiness, chunk ID, active state, and text hash inside one transaction. A
   concurrent edit, deletion, or course move returns conflict and leaves the
   source stale.
4. **Model names do not guarantee vector compatibility.** Index state and
   indexed counts now bind model, dimension, and text hash. Direct indexing
   uses the model's declared dimension when available and a one-chunk probe
   otherwise, so a same-name model that changes output dimension is re-indexed
   safely.
5. **Imported media could lose its real video target.** The source projection
   preserves a linked job ID, and `video_time` supports either job or asset
   ownership. Runtime sync and migration now emit the same locator shape.
6. **Legacy unit names did not exactly match the canonical contract.**
   `transcript_segment` is normalized to canonical `transcript` while retaining
   its video-time locator.
7. **Caller-owned transactions did not prove migration atomicity.**
   `apply_migrations` now owns an internal savepoint and tests failure rollback
   of schema, data, and migration-version records.
8. **Ordinary model runtime failures bypassed service errors.** OOM and backend
   `RuntimeError` failures are now normalized, move an indexing Source to
   `failed`, and retain the original exception only in application logs.
9. **Raw local model errors could expose machine paths.** Both HTTP errors and
   the public `Source.index_error` field now contain stable retry guidance, not
   the original machine path.
10. **Search performed inference before validating scope.** Course and selected
    Sources are resolved first. Missing/cross-course requests preserve their
    404/400 semantics, and an empty notebook returns without loading a model.
11. **Concurrent projection writers could publish an old origin snapshot.**
    Full reconciliation, per-source sync, deletion, and course moves now share
    one process lock. HTTP GET misses remain pure 404s rather than hidden repair
    writes.
12. **An older failed index could overwrite a newer successful one.** Every
    attempt now owns a UUID generation token. Begin, commit, and failure
    transitions are course- and generation-scoped; source edits and moves
    invalidate the token. A short `BEGIN IMMEDIATE` protects the final
    compare-and-swap.
13. **A source could move after search validation but before evidence reads.**
    Final chunk and vector queries now join `sources` and require the original
    course ID. Concurrent moves therefore return no former-course evidence.

### Verification

Focused Source, migration, and course regression after concurrency fixes:

```text
37 passed, 1 warning
```

Complete backend regression after the source implementation:

```text
443 passed, 1 warning in 71.47s
```

Frontend repository gates:

```text
ESLint: passed
TypeScript + Vite production build: passed
```

The one warning is the existing Starlette TestClient deprecation notice for its
legacy `httpx` bridge; it is unrelated to this stage.

The current real database was exercised only through an isolated temporary
copy:

```text
jobs:                 5 -> 5
knowledge_cards:    118 -> 118
card_embeddings:    101 -> 101
canonical sources:          8
canonical chunks:         491
index generations:     8 NULL
schema migration:           1 (unified_source_index)
migrated quick_check:       ok
backup quick_check:         ok
migration time:       0.027929 s
```

The eight Sources are five video jobs and three document assets. The 491 chunks
are 121 transcript chunks and 370 source units. Instrumentation observed zero
video opens and exactly 491 text-hash inputs totaling 376,021 bytes. The
original `jobs.db` SHA-256 remained:

```text
019587307a58e4b16e024c4fd2ef7c197ac3bf7575471a7d7b46c23d29a33026
```

The migration backup was independently readable and remained at the
pre-migration schema. Temporary files were removed.

### Known limitations

- Indexing is a synchronous request and does not yet expose durable progress,
  cancellation, or retry state. P0.5 owns that reliability work.
- Retrieval is a dense cosine baseline without lexical recall or reranking.
- Startup reconciliation is intentionally a repair mechanism; future source
  types must add their own write-through sync hook.
- Origin writes and projection sync still span two SQLite transactions. The
  shared lock prevents stale overwrites, and startup repair recovers drift
  after restart, but a durable retry/outbox belongs to P0.5 task reliability.
- The projection lock matches the current single-process desktop backend. A
  future multi-worker deployment would require cross-process coordination.
- A changed vector dimension is detected, but a model whose name and dimension
  stay constant while its weights change needs a persisted model
  revision/fingerprint before old vectors can be invalidated automatically.
- The projection duplicates extracted text to buy stable IDs and independent
  query lifecycle.
- This stage has no new frontend surface. It becomes user-visible through
  P0.2-P0.4.

### Git checkpoint

Intended commit subject:

```text
feat(sources): unify course evidence under source chunks
```

### Next gate

P0.2 will add durable conversations and messages, bounded multi-turn context,
source-scoped retrieval, grounded local answer generation, explicit abstention,
and persisted sentence-level citation records. P0.3 will make those citations
open their exact original location.
