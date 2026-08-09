# Productization Log

Last updated: 2026-08-08

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

## P0.2 - Durable Grounded Chat

**Status:** Complete

**Date:** 2026-07-27

**Branch:** `codex/notebooklm-product-core`

### User outcome

Ask is now a persistent, source-grounded conversation rather than a list of
similar cards. A learner can choose the course Sources, ask a question, follow
up using bounded conversation context, reopen the conversation after restart,
and distinguish an evidence refusal from a failed local-model request.

Every published answer item has at least one server-owned citation snapshot.
The UI expands the supporting quote and typed locator beside the sentence.
Opening that locator in the original video or document remains the explicit
P0.3 boundary.

### Why this stage now

P0.1 supplied one canonical index for original videos and documents. The
highest-value next step was to prove that this evidence could support a durable
question-answer loop without falling back to derived cards or model-written
citations.

Doing the persistence and failure model before a full Chat page also prevents
the future Sources / Chat / Studio navigation from being built around transient
component state. The reusable feature slice can move from the existing Ask rail
into the P0.4 workspace without changing its data contract.

### Scope and non-goals

Included:

- persistent course-scoped conversations, turns, and ordered messages;
- per-conversation and per-turn Source snapshots;
- bounded multi-turn retrieval and generation context;
- local dense retrieval over canonical source chunks;
- strict local-LLM JSON generation with one repair attempt;
- deterministic insufficient-evidence refusal;
- immutable citation and sentence-span snapshots;
- request idempotency across a lost browser response;
- startup recovery of interrupted turns;
- a React Chat feature slice with Source selection, history, recommendations,
  evidence previews, explicit states, and bounded status polling;
- compatibility for the existing `/rag/retrieve` card endpoint;
- a resume-facing README update that presents the verified product direction
  before the paused research program.

Not included:

- clicking a citation does not yet seek a video or open a document location;
- the top-level navigation is not yet Sources / Chat / Studio;
- answer streaming, cancellation, durable background execution, and task-level
  retry belong to P0.5;
- note capture and Studio output generation belong to P1.1-P1.2;
- frontend unit/component test infrastructure belongs to P1.4.

### Storage and API contract

Migration v2 adds five normalized tables:

| Table | Responsibility |
| --- | --- |
| `chat_conversations` | Course, title, archive state, selected Source snapshot, and list metadata |
| `chat_turns` | Request ID, state machine, Source snapshot, generation token, query, refusal, and safe failure |
| `chat_messages` | Ordered user messages and reserved/final assistant messages |
| `chat_citations` | One immutable Source/chunk/quote/locator snapshot per cited chunk and message |
| `chat_citation_spans` | Every answer sentence range supported by a citation snapshot |

The active development database had already applied an earlier, uncommitted v2
before independent review added `source_scope_mode` and removed a redundant
turn state. Rewriting v2 would make fresh tests pass while leaving that real
database incompatible. Forward migration v3 therefore rebuilds `chat_turns`
atomically, preserves existing rows, maps legacy `abstained` turns to
`refused`, adds the scope mode, and recreates both turn indexes.

The local API is:

```text
GET    /courses/{course_id}/chat/conversations
POST   /courses/{course_id}/chat/conversations
GET    /chat/conversations/{conversation_id}
PATCH  /chat/conversations/{conversation_id}
DELETE /chat/conversations/{conversation_id}
POST   /chat/conversations/{conversation_id}/messages
```

The message request includes a browser-owned `client_request_id`. Replaying the
same ID and payload returns the existing terminal result; a different payload
is rejected. A partial unique index permits only one active turn per
conversation.

### Decisions and alternatives

1. **Persist a state machine, not just chat text.** A turn transitions through
   `pending -> retrieving -> generating -> validating` and finishes as
   `completed`, `refused`, or `failed`.
2. **Reserve before inference.** The user message and assistant placeholder are
   created atomically before embedding or generation. Inference runs outside
   SQLite transactions; generation-token compare-and-swap protects every
   transition and final commit.
3. **Snapshot Source scope per turn.** Later selection changes do not rewrite
   the meaning of historical answers.
4. **Make the server own evidence.** The model may cite only temporary labels
   assigned to current retrieval results. The server replaces them with
   immutable Source, chunk, quote, locator, hash, and score records.
5. **Fail closed.** No Sources or no evidence returns a deterministic refusal
   without loading the LLM. Invalid JSON is repaired once; a second invalid
   response becomes a safe failure rather than uncited prose.
6. **Treat history as context, never evidence.** Only current retrieval results
   may support factual answer items.
7. **Keep the request synchronous for P0.2.** Durable terminal state and
   recovery are proven before adding streaming and cancellation.

Extending the card-only retrieval route, storing one JSON conversation blob,
trusting model-authored citations, holding a database transaction through
inference, and resolving historical Source scope dynamically were rejected.
The complete rationale is
[`ADR-0003`](decisions/ADR-0003-durable-grounded-chat-state-machine.md).

### Context and grounding budgets

| Budget | Bound |
| --- | ---: |
| Retrieval history | current question + 2 recent user questions |
| Retrieval query | 1,500 characters |
| Generation history | 6 complete messages |
| Generation history text | 6,000 characters |
| Evidence count | 8 chunks |
| Evidence per chunk | 3,000 characters |
| Evidence total | 16,000 characters |
| Generated output | 2,048 tokens |
| Dense cosine floor | 0.25 |

The `0.25` floor is deliberately conservative. On an isolated copy of the real
CS231n corpus, two in-domain English questions produced top scores of `0.569`
and `0.666`; three unrelated English questions produced `0.186`, `0.165`, and
`0.192`. A previous `0.15` floor admitted all three unrelated examples and was
raised before acceptance. This is a small product calibration, not a general
retrieval benchmark.

### Technology and responsibilities

| Component | Technology | Responsibility |
| --- | --- | --- |
| API contract | FastAPI + Pydantic | Validate conversation, turn, message, grounding, and typed locator states |
| Durable state | SQLite | Persist conversations, lifecycle transitions, idempotency, messages, and citation snapshots |
| Concurrency | SQLite transactions, partial unique index, generation-token CAS | Reserve once, allow one active turn, reject stale completion |
| Retrieval | P0.1 Source index + MiniLM cosine search | Retrieve original source chunks inside the selected Source scope |
| Generation | Existing Ollama-compatible local LLM client | Produce strict grounded JSON without cloud dependency |
| Grounding | Pydantic discriminated output + server evidence allow-list | Reject unknown labels, multi-sentence items, and uncited prose |
| UI | React 19 + TypeScript feature slice | Manage conversations, Source selection, request envelope, polling, and citation previews |
| Styling | Scoped CSS | Support full-page reuse and the compact Ask rail with visible keyboard focus |
| Verification | pytest, ESLint, TypeScript, Vite, browser inspection | State-machine, API, regression, build, and interaction gates |

### Problems encountered and resolutions

1. **The old Ask path was not an answer system.** It searched generated cards
   and rendered them in local component state. The new path uses canonical
   original-source chunks and durable messages; the legacy endpoint remains
   compatible but no longer powers Ask.
2. **A successful server turn could be duplicated after a lost HTTP response.**
   The first UI implementation generated a new request ID for every Retry.
   Independent acceptance caught the gap. The UI now retains the complete send
   envelope—question, request ID, Source snapshot, and model—and replays it
   unchanged while delivery is uncertain. A server-confirmed failed message
   instead offers “Ask again as a new turn.”
3. **A turn could be stranded immediately after reservation.** Conversation
   title/Source updates originally happened in a second unguarded call before
   the failure finalizer. Reservation now resolves and validates the Source
   snapshot and applies the initial title atomically, so there is no
   post-reservation update window.
4. **Conversation edits could be overwritten by stale objects.** Narrow
   transactional patches and reservation-owned metadata replace whole-row
   updates in concurrent paths.
5. **Same-course evidence was not enough isolation.** A regressed or forged
   search result could reference an unselected Source in the same course.
   Final citation commit now enforces the turn's persisted Source allow-list in
   addition to course membership.
6. **A replayed failed request changed HTTP semantics.** Stored timeout,
   retrieval, and Source-change error codes now replay as the original 504,
   503, and 409 classes rather than collapsing to a generic 502.
7. **Deleted or moved selected Sources could produce a misleading conversation
   404.** Source scope is now resolved transactionally at reservation and maps
   stale selection to an explicit Source-changed conflict.
8. **The database and API turn-state vocabularies drifted.** The unused
   `abstained` database value was removed; `refused` is the one persisted
   insufficient-evidence terminal state. Because the real development
   database had already recorded the earlier v2, this was repaired through a
   forward v3 migration instead of silently rewriting migration history.
9. **“Sentence-level” initially trusted model formatting too much.** Strict
   output validation now rejects an item containing multiple detectable
   natural-language sentences before citation spans are computed.
10. **The model could invent plausible evidence metadata.** It now sees only
    temporary labels. Labels outside the server allow-list, duplicated labels,
    extra keys, malformed JSON, or missing citations fail validation.
11. **Sources can change during generation.** The final transaction rechecks
    course, turn Source scope, Source enabled state, chunk active state, text
    hash, quote, and typed locator before publishing the answer.
12. **Prompt injection can live in both history and Sources.** The prompt
    labels the question, history, titles, evidence, and repair candidate as
    untrusted data. History may resolve references but cannot support a claim.
13. **Refusal and infrastructure failure were easy to conflate.** `refused`
    produces a complete assistant message with no citations; retrieval/model
    failures persist safe error codes and messages without exposing local
    machine paths.
14. **Browser and Python count Unicode differently from UTF-16 string
    offsets.** The frontend maps citation spans over Unicode code points so
    emoji and supplementary characters align with Python offsets.
15. **Conditional compact-rail rows caused hidden overflow.** Browser
    inspection measured the panel and workspace scroll bounds, exposed the
    grid-row bug, and verified the corrected fixed row placement in both
    collapsed and expanded Source states.
16. **A remounted panel could show a generating message forever.** A bounded,
    abortable, course- and conversation-guarded poll now refreshes persisted
    state for up to one minute and exposes a manual refresh state afterward.
17. **Native inputs lost their focus indicator.** Hidden Source checkboxes and
    the composer now transfer visible focus styling to their container.
18. **SQLite backup files remained locked on Windows.** Python's SQLite
    connection context manager commits or rolls back but does not close the
    connection. Migration backup source, destination, and validation
    connections now close explicitly. A regression opens, closes, renames, and
    deletes the backup in the same process.
19. **The UI initially treated a disabled but processed Source as ready.** The
    backend correctly rejected it, but the picker still offered it and counted
    it toward send readiness. Selection, Select all, send snapshots, counts,
    status labels, and disabled controls now share the same
    `enabled && ready` rule while still allowing an already-selected disabled
    Source to be unchecked.

### Verification

Focused Source migration and Chat regression after the final Windows backup
handle fix:

```text
66 passed, 1 warning
```

Complete backend regression after all review fixes:

```text
504 passed, 1 warning in 100.52 s
```

Backend bytecode compilation and whitespace validation also passed. The final
backup-handle change was followed by the 66-test focused run because it touches
only migration backup connection lifetime. A final 20-test Chat API pass added
and verified both disabled-Source race cases before the complete 504-test run.

Frontend repository gates:

```text
ESLint: passed
TypeScript + Vite production build: passed
```

Manual browser inspection of the compact Ask rail verified:

```text
collapsed panel: no panel or workspace overflow
expanded Sources: no panel or workspace overflow
console warnings/errors: none
```

The final focus selectors were then reviewed statically and passed ESLint and
the production build. The one backend warning is the existing Starlette
TestClient deprecation notice for its legacy `httpx` bridge; it is unrelated
to this stage.

#### Real database migration

Starting the normal local backend first applied migrations v1 and v2 to the
active database and automatically created:

```text
backend/data/backups/jobs.pre-migration-v2-20260727T060725400205Z.db
```

This was an expected application-startup write, but it was not the intended
read-only UI inspection path and is recorded explicitly. Independent review
then found the already-applied-v2 compatibility issue described above.

The v2-to-v3 upgrade was rehearsed on an isolated copy before the real database
was touched:

```text
before / after quick_check:  ok / ok
migration time:              0.030072 s
business and chat counts:    unchanged
source_scope_mode:           present
legacy abstained CHECK:      removed
turn indexes:                both present
generated backup:            quick_check ok, schema version 2
temporary cleanup:           succeeded in the same process
real input SHA-256:          unchanged
```

The real database then followed that same migration path and created:

```text
backend/data/backups/jobs.pre-migration-v3-20260727T065434325947Z.db
```

The active file, pre-v3 backup, and original legacy backup were checked:

```text
active quick_check:      ok
pre-v3 backup check:     ok
legacy backup check:     ok
jobs:                     5
knowledge_cards:        118
card_embeddings:        101
canonical sources:        8
canonical chunks:       491
source embeddings:         0
chat table rows:           0 in all five tables
active migrations:        1 unified_source_index
                          2 grounded_chat
                          3 align_grounded_chat_turn_contract
pre-v3 migrations:        1 unified_source_index
                          2 grounded_chat
active SHA-256:         f097ce715543bbc1dca502dc3319d836a304efb3b9a1929f24d137014ba44060
pre-v3 SHA-256:         539f08a04903f4c4b25cba981e5bd66bf0a1b8d6e15e471d5c536b9e93904e5a
legacy SHA-256:         0b5706687f70f78af9959ef1e9de5a7ba2a07032a750c4a9d15c71369f18ec50
```

The pre-v3 backup preserves the complete version-2 database. The earlier
pre-v2 backup is independently readable and contains the unchanged legacy data
without `schema_migrations`.

The complete v1-to-v3 sequence was repeated on an isolated temporary copy of
that legacy backup:

```text
migration time:           0.047402 s
migrated quick_check:     ok
generated backup check:   ok
migrations:               [(1, unified_source_index),
                           (2, grounded_chat),
                           (3, align_grounded_chat_turn_contract)]
jobs/cards/vectors:       5 / 118 / 101
sources/chunks:           8 / 491
source embeddings:        0
chat table rows:          0 in all five tables
legacy input hash:        unchanged
temporary files:          removed
```

#### Retrieval calibration

The real corpus was copied to an isolated temporary database and indexed there;
the active database retained zero source embeddings. Scores were:

```text
in-domain English:        0.569, 0.666
unrelated English:        0.186, 0.165, 0.192
Chinese -> English source 0.198
unrelated Chinese:        0.178
```

This supports the conservative `0.25` default for the current English corpus
and also exposes the cross-language limitation rather than hiding it.
Temporary databases and generated vectors were removed.

### Known limitations

- P0.3 must connect the stored typed locator to the video player and document
  viewers. P0.2 displays the quote and location but does not claim click-through.
- The synchronous POST cannot stream tokens or cancel local inference. P0.5
  will place long work behind recoverable, cancellable tasks.
- Dense MiniLM retrieval has no lexical fallback, query expansion, or reranker.
- `all-MiniLM-L6-v2` is not a reliable Chinese-to-English retriever. The current
  conservative floor refuses the sampled Chinese question; multilingual
  retrieval requires an explicit model/evaluation decision.
- Citation coverage, Source allow-list, and immutable snapshots do not prove
  semantic entailment between every claim and quote. A versioned evaluation
  set and optional entailment/claim verifier remain future work.
- Chunks longer than 3,000 characters are skipped rather than truncated so the
  stored quote and hash stay exact. Chunk splitting should be improved for long
  PDF pages.
- History is bounded by recent complete messages, not guaranteed whole
  user/assistant turn pairs.
- Conversation detail is not paginated and will eventually need windowing.
- Automatic conversation creation is not itself idempotent; a lost create
  response can leave an extra empty conversation, though it cannot duplicate a
  turn or LLM call.
- Frontend behavior is covered by type/lint/build gates, backend black-box API
  tests, and manual browser inspection. Component and end-to-end automation
  remain a P1.4 requirement.
- The feature boundary is clearer, but `chat_store.py` and `useChat.ts` are
  still large modules. P1.4 should split SQL persistence/state transitions and
  browser synchronization/polling into independently tested units.
- Polling is bounded to one minute. A slow synchronous generation may require
  manual refresh until P0.5 owns durable task progress and cancellation.
- Process-local coordination matches the current desktop runtime, not a future
  multi-worker server.

### Git checkpoint

Intended commit subject:

```text
feat(chat): add persistent source-grounded conversations
```

### Next gate

P0.3 will make every persisted citation actionable. Clicking an answer
sentence or citation chip must:

- seek the exact video timestamp;
- open a PDF at its page;
- open a PPTX at its slide;
- open a DOCX at its paragraph;
- or open a text/Markdown Source at its section.

The acceptance gate includes stale/missing local files, deleted Sources,
locator-version handling, keyboard activation, and a visible fallback rather
than a dead click.

## P0.3 - Verifiable Citation Navigation

**Date:** 2026-07-27
**Status:** Complete
**Decision record:**
[`ADR-0004`](decisions/ADR-0004-server-authoritative-citation-navigation.md)

### User outcome

Every citation emitted by grounded Chat is now an actionable evidence link.
One click opens a single Source inspector and returns to the exact normalized
location used by retrieval:

```text
video / audio -> canonical start time
PDF           -> canonical page + extracted page text
PPTX          -> canonical extracted slide
DOCX          -> canonical extracted non-empty paragraph
TXT / MD      -> canonical extracted section
```

The saved quotation is rendered before live resolution and is never removed
when the current Source is missing, changed, or unsupported. When the current
canonical chunk is still valid but only the original file is unavailable, the
last verified extracted context remains visible and read-only.

### Why this stage now

P0.1 established one Source/Chunk/Locator contract and P0.2 persisted immutable
sentence-level citation snapshots. Without a safe navigation layer, those
citations were still labels rather than verifiable product behavior. Building
the viewer before the P0.4 navigation rewrite also provides one reusable
component for both the compact Ask rail and the future top-level Chat surface.

### Scope and non-goals

Implemented:

- course-scoped citation target and content endpoints;
- server-authoritative resolution from an opaque citation ID;
- current Source/chunk/owner/integrity validation;
- historical snapshot fallback with explicit reason codes;
- exact video/audio seeking and PDF page fragments;
- exact extracted-unit navigation for PPTX, DOCX, text, and Markdown;
- native citation buttons, modal keyboard behavior, focus return, and safe
  text-node highlighting;
- managed-file streaming with full, bounded, open-ended, and suffix byte
  ranges;
- upload-time video fingerprints and a guarded legacy-fingerprint upgrade;
- frontend component-test infrastructure for citation behavior.

Deliberately not implemented:

- the Sources / Chat / Studio navigation rewrite, which is P0.4;
- pixel-perfect PowerPoint or Word rendering;
- operating-system file launching as the primary navigation contract;
- remote API binding or a general-purpose local file server;
- task cancellation, backup/restore UI, and path-field compatibility cleanup,
  which remain P0.5 work.

### API and trust contract

The browser sends only:

```text
course_id + server-issued citation_id
```

It never supplies a locator, Source ID, asset ID, job ID, or path for the
server to trust. The server resolves:

```text
chat citation
-> assistant message
-> conversation course
-> immutable Source/chunk snapshot
-> current canonical Source/chunk
-> current owner
-> managed file
```

Endpoints:

```text
GET      /courses/{course_id}/chat/citations/{citation_id}/target
GET|HEAD /courses/{course_id}/chat/citations/{citation_id}/content
```

Another course's citation and an unknown citation intentionally produce the
same `404`. Target responses return either:

```text
available      current chunk and managed file still match
snapshot_only  saved evidence remains, but the current original cannot be
               claimed as the evidence used by the answer
```

The response contains no absolute local path. Both target metadata and content
are private/no-store. Content is restricted to loopback clients, uses a
server-controlled MIME type, is forced inline, and includes `nosniff`.

### Decisions and alternatives

1. **Server-authoritative citation IDs instead of client locators.** A typed
   locator is useful display data but is not a file capability. Resolving the
   persisted citation again centralizes course isolation, current-health
   checks, relocation, and deletion semantics.
2. **Historical truth and current health are separate.** Rewriting or deleting
   a citation when a file changes would corrupt the answer's provenance.
   Silently opening a changed file would be worse. The immutable quotation
   therefore survives while live navigation degrades explicitly.
3. **One internal inspector instead of default OS launching.** Browser and
   Tauri now share one testable behavior. PDF can use the embedded viewer;
   Office formats use the exact normalized unit that retrieval actually saw.
4. **Open and stream one verified file handle.** Returning a validated `Path`
   and letting `FileResponse` reopen it leaves a path-swap and symlink race.
   The content response now hashes and streams the same no-follow regular-file
   handle and implements bounded single-range semantics around that handle.
5. **Cryptographic content checks instead of metadata-only caching.** File
   size, timestamps, and inode/file ID do not prove content identity on
   Windows. Every content decision uses SHA-256; a metadata-keyed digest cache
   was removed after an independent review reproduced a same-size overwrite
   with restored timestamps.
6. **Controlled startup backfill instead of citation-time trust on first use.**
   New uploads hash bytes during the existing copy pass. Legacy rows are
   fingerprinted before Chat is served, only after managed-root, immutable
   storage-name, recorded-size, regular-file, stable-read checks. A row that
   cannot pass remains null and every later citation read returns
   `legacy_fingerprint_unverified`; citation reads never establish trust.
7. **Tolerant historical locator reads and strict new writes.** An unknown
   future kind or schema version disables only that old citation. New citations
   must validate against the supported version-1 discriminated union.
8. **Extracted text is the deterministic document fallback.** A PDF plugin may
   be unavailable and Office layout conversion would add a large dependency.
   Canonical extracted context is always sufficient to verify the quote and
   exact normalized location.

Rejected alternatives included a client-supplied `/files?path=...` endpoint,
trusting the locator sent back by the browser, returning a dead error when the
current file is gone, and making pixel-perfect Office conversion a prerequisite
for evidence navigation.

### Technology and responsibilities

| Component | Technology | Responsibility |
| --- | --- | --- |
| Target API | FastAPI + Pydantic | Course isolation, response validation, stable snapshot/current states |
| Citation join | SQLite store query | Resolve immutable citation ownership and current canonical Source state |
| Integrity | SHA-256 + upload-copy hashing | Detect changed video and document bytes without trusting client metadata |
| File boundary | `resolve(strict=True)`, no-follow open, `fstat`, regular-file checks | Constrain bytes to managed roots and bind validation to the opened object |
| Streaming | Starlette `StreamingResponse` + bounded range parser | Serve GET/HEAD, 200/206/416, and seekable media from the verified handle |
| Schema upgrade | SQLite migration v4 | Add nullable `jobs.video_sha256` with validated pre-migration backup |
| Inspector | React 19 + TypeScript + native `<dialog>` | Lazy-load one keyboard-accessible viewer and preserve the saved quote |
| Deep links | HTML media APIs + PDF page fragment | Seek media and open exact PDF pages without desktop-only dependencies |
| Document fallback | Canonical Source chunks | Highlight exact slide/page/paragraph/section text with React text nodes |
| Desktop boundary | Tauri CSP | Permit only loopback media/frame content required by the inspector |
| Frontend tests | Vitest + Testing Library + jsdom | Verify URL trust, locator versions, seeking, fallback, focus, and highlighting |

### Problems encountered and resolutions

1. **The first file endpoint design still reopened a validated path.** This
   created a time-of-check/time-of-use window between integrity validation and
   response streaming. The final design keeps the opened handle, verifies its
   identity and digest, and streams that same handle.
2. **A performance cache was not an integrity cache.** The initial SHA cache
   used size, mtime, ctime, and inode as its key. An independent Windows test
   overwrote a file with equal-length bytes, restored mtime, kept the other
   fields unchanged, and received the old digest. The cache was deleted and a
   regression test now reproduces that exact attack.
3. **Legacy-video hashing initially happened on the first citation click.**
   That could bless any bytes present before the first click. Backfill moved
   into FastAPI lifespan immediately after schema initialization and before
   interrupted-turn recovery or Source reconciliation. The read path now
   refuses every still-unfingerprinted legacy video.
4. **File lifecycle races surfaced as 500s.** Delete, permission, and open
   failures between resolution steps are normalized to path-free
   `file_lifecycle_error` or `file_missing` states. Target metadata remains a
   `200 snapshot_only`; the content endpoint returns a stable gone/conflict
   class.
5. **The initial frontend trusted any returned media URL.** The resolver now
   accepts only HTTP(S), the exact API origin, and the exact encoded current
   course/citation `/content` path. It rejects credentials, query strings,
   fragments, `javascript:`, `data:`, cross-origin URLs, and other API paths.
6. **A known locator kind with a future schema version looked valid.** All
   formatting, PDF navigation, and media seeking now gate on
   `schema_version === 1`; future versions display an explicit unsupported
   label.
7. **Snapshot-only mode hid still-trustworthy extracted context.** Backend
   file-integrity failures already retained canonical context, but the UI
   rendered it only for `available`. The inspector now distinguishes a missing
   original file from Source/chunk drift and keeps safe extracted context
   visible.
8. **Relocated data roots invalidated old absolute paths.** Resolution treats
   immutable stored names and owner IDs as authority, rebases video files under
   the configured upload root, and accepts a document relocation only when
   exactly one owner-named candidate under the managed Source root matches its
   persisted hash.
9. **The first migration rehearsal left its temporary backup locked.**
   `sqlite3.Connection` used as a context manager commits or rolls back but
   does not close the connection. The rehearsal script was changed to an
   explicit closing context, the guarded temporary directory was removed, and
   the complete rehearsal was rerun successfully before touching the real
   database.
10. **Windows blocked the `npm.ps1` shim.** Validation uses the equivalent
    `npm.cmd` entrypoint, so the repository did not require a machine-wide
    execution-policy change.

### Verification

Backend coverage includes:

- all five typed locator kinds and imported audio/video;
- cross-course and unknown-citation indistinguishability;
- Source disabled, deleted, moved, changed, and re-indexed states;
- missing file, owner mismatch, bad stored identity, relocation, and hash
  mismatch;
- same-size byte replacement with restored mtime;
- managed-root and symbolic-link/reparse escape rejection;
- stable lifecycle-error mapping and no path leakage;
- upload-time video hashing, startup-only legacy backfill, failed-backfill
  refusal, and no citation-time TOFU;
- full GET, HEAD, bounded, open-ended, and suffix Range responses plus 416;
- the single verified open handle being the one streamed;
- unknown historical locator survival and strict new-citation writes;
- v3-to-v4 backup/migration and fresh-schema idempotency.

Final automated gates:

```text
backend focused citation/migration/job tests: 33 passed / 1 skipped
backend full pytest suite:                     526 passed / 1 skipped
backend compileall:                            pass
frontend Vitest:                               2 files / 14 tests pass
frontend ESLint:                               pass
frontend TypeScript + production build:        pass
npm audit --audit-level=high:                  0 vulnerabilities
git diff --check:                              pass
```

The skipped test requires Windows symlink-creation permission that this host
does not grant. The only warning is the existing upstream
Starlette-TestClient/httpx deprecation; it is not produced by P0.3 behavior.

Production bundle checkpoint:

```text
main JS:               498.91 kB / 150.71 kB gzip
CitationInspector JS:    7.75 kB /   2.58 kB gzip
CitationInspector CSS:   5.47 kB /   1.64 kB gzip
```

The inspector remains a real lazy chunk; the main bundle stays below Vite's
500 kB warning threshold.

#### Manual browser acceptance

An isolated temporary workspace was seeded with one text Source, one completed
historical conversation, and one sentence citation. In the real Vite/FastAPI
application at a 1280 x 720 viewport:

```text
open course -> expand rail -> Ask -> click [1]
-> Source inspector title = optimization-notes.md
-> locator = Section 1
-> saved quote is visible
-> the identical quote is marked inside canonical context
-> target article receives focus
-> Escape closes the dialog
-> focus returns to the original citation button
```

The 920 x 680 dialog stayed inside the viewport, the page had no horizontal
overflow, and the browser emitted no warnings or errors. Both temporary
servers, browser tabs, seeded database, and temporary Source files were
stopped or removed after acceptance.

#### Real database migration

Migration v4 was rehearsed on an isolated copy before the active database was
opened:

```text
quick_check before / after: ok / ok
applied versions:           [4]
elapsed:                    0.030957 seconds
rows before / after:
  jobs                      5 / 5
  cards                     118 / 118
  sources                   8 / 8
  source_chunks             491 / 491
  chat rows                 0 / 0
backup schema:              v1-v3, no video_sha256 column
```

The active migration then created:

```text
backend/data/backups/jobs.pre-migration-v4-20260727T080412405704Z.db
```

Before the later data-only legacy fingerprint backfill, a second SQLite-native
backup was created and validated:

```text
backend/data/backups/
jobs.pre-video-fingerprint-backfill-20260727T082942224771Z.db
```

Post-migration checks:

```text
active quick_check: ok
active schema:      v1-v4, video_sha256 present
backup quick_check: ok
backup schema:      v1-v3, video_sha256 absent
row counts:         unchanged
```

Integrity checkpoints:

```text
pre-migration active SHA-256:
F097CE715543BBC1DCA502DC3319D836A304EFB3B9A1929F24D137014BA44060

post-v4/pre-backfill active SHA-256:
f28f0ba3591540f7962cd3db1341b20a6000034f2057fbdc465956cbcab58642

pre-v4 backup SHA-256:
5ab97300915cbf1aa529f05fd14e92a1e82cb55bdb86a634b0335c9cb68a1e55

pre-backfill v4 backup SHA-256:
7f77ec0fed73591f59671f6672709790a0be943c0cf56bfbe5e1e31e81d12cef
```

The legacy backfill was first run against a SQLite-native database copy while
reading the real managed uploads. The copy accepted all five rows, the source
database SHA-256 remained unchanged, and both databases passed `quick_check`.
The backed-up active database was then upgraded through the same service:

```text
rehearsal:       5 scanned / 5 backfilled / 0 refused
active:          5 scanned / 5 backfilled / 0 refused
fingerprints:    5 / 5 independently matched managed files
verified bytes:  5,328,217,687
quick_check:     ok
schema versions: 1, 2, 3, 4
row counts:      unchanged

final active database SHA-256:
cae70b36dc8e6954096719405c608ab5817b0fe04728c9f4004f3a1a533c17ad
```

### Independent acceptance

Two independent read-only reviews reproduced and blocked release on the
legacy TOFU window and the metadata-keyed hash-cache flaw. A third full
acceptance review also identified the path-reopen race, unknown-version
locator behavior, untrusted frontend media URLs, and hidden snapshot context.
All release-blocking findings were converted into regression tests and fixed
before the stage checkpoint. The one environment-dependent symlink test is
skipped on this Windows host because creating symlinks is not permitted; the
no-follow open, resolved-root containment, owner-name checks, and outside-root
tests still run. A Linux CI job remains advisable.

### Known limitations

- A legacy video fingerprint proves the bytes present during the controlled
  v4 startup upgrade, not that no local process changed the file before that
  upgrade. This is an explicit one-time local trust boundary. Any video that
  cannot pass the upgrade remains snapshot-only until it is re-imported.
- Strong file integrity requires a complete SHA-256 pass. Target resolution
  and content delivery currently validate independently, so first open of a
  large video may be noticeably slower. P0.5 should measure this and consider
  an immutable/content-addressed managed store rather than reintroducing a
  metadata-only cache.
- Streaming the verified handle closes path replacement and symlink races, but
  this local desktop design is not a defense against a privileged malicious
  process modifying the same open file in place during delivery.
- PDF rendering depends on embedded-browser support; extracted page text is
  the deterministic fallback. PPTX and DOCX navigation is exact at the
  normalized retrieval unit, not pixel-perfect original layout.
- The PDF frame has no reliable cross-browser load-error signal. Failure does
  not remove the quote or extracted context.
- Loopback-only access prevents remote file serving but is not a per-launch
  authentication token. A token is required before any future remote binding.
- Older compatibility responses still expose local path fields. The citation
  viewer does not consume them; removal belongs to a versioned P0.5/P1.4 API
  cleanup.
- Component tests cover the citation feature slice and manual browser
  acceptance covers the host integration. A durable app-level end-to-end suite
  and Linux symlink CI remain P1.4 work.

### Git checkpoint

Intended commit subject:

```text
feat(citations): open grounded evidence at its source location
```

### Next gate

P0.4 will replace the tool-first shell with the product's source-first
information architecture:

```text
course notebook
-> Sources
-> Chat
-> Studio
```

Study, Review, Course Map, and Explore remain available as secondary or Studio
workflows rather than five co-equal starting points. The acceptance gate will
cover responsive navigation, preserved deep links, current feature parity,
clear empty/loading/error states, and a measured reduction of `App.tsx`
responsibility rather than a cosmetic label change.

## P0.4 - Source-First Workspace and Canonical Navigation

- **Status:** Complete
- **Started:** 2026-07-27
- **Accepted:** 2026-07-27
- **Acceptance evidence:** implementation, independent review, automated
  suites, real-browser acceptance, and documentation complete; Git identity
  and post-push remote equality are reported by Git and the delivery summary
- **Decision record:**
  [`ADR-0005`](decisions/ADR-0005-source-first-workspace-and-route-contract.md)

### User outcome

The course notebook now has three primary destinations:

```text
Sources -> Chat -> Studio
```

Sources is the evidence catalog. It lists video/audio and document Sources
together, reports extraction and indexing state, imports supported documents,
enables or disables retrieval globally, indexes ready Sources, previews exact
canonical chunks and typed locators, deletes document assets, and opens the
corresponding video workflow. The established video/transcript/card-generation
workflow is hidden by default and opens as an on-demand compatibility detail
after **Add video** or selection of an existing video/job.

Chat is a full course workspace instead of an Ask tab inside the card rail. It
retains conversation history, per-conversation Source selection, multi-turn
context, explicit insufficient-evidence behavior, retry state, and
sentence-level citations. The active conversation has a canonical URL and is
restored when the user returns to a course's Chat.

Studio keeps the existing learning capabilities as five secondary tools:

```text
Cards | Study | Review | Course map | Explore
```

Cards provides the default Studio landing and reuses the existing card editor
and note/review details. Study, Review, Course Map, and Explore retain their
implemented workflows without competing with Sources and Chat as primary
product concepts.

The three destinations use real links and canonical URLs. Refresh,
copy/paste, modified-click behavior, legacy links, and browser back/forward
are part of the navigation contract rather than accidental component state.

### Why this stage now

P0.1-P0.3 completed the backend contracts for unified Sources, persistent
grounded Chat, and exact citation navigation. The previous interface still
presented Workspace, Study, Review, Course Map, and Explore as five unrelated
starting points and hid Chat inside a card-oriented side rail. A user could
not infer the product's core loop from its navigation.

P0.4 makes the completed evidence pipeline legible before adding reliability,
Notes, or new Studio outputs:

```text
collect evidence
-> ask and verify
-> turn understanding into durable learning artifacts
```

This order also avoids building P0.5 and P1 workflows into a shell that is
already known to have the wrong ownership boundaries.

### Scope and non-goals

Included:

- exactly three primary navigation links: Sources, Chat, and Studio;
- a typed canonical query-route contract with legacy URL migration;
- one host-owned browser-history write policy;
- course-scoped route and asynchronous-response isolation;
- a unified mixed-media/document Sources catalog and canonical chunk preview;
- full-width Chat with restorable conversation links;
- Studio shells for Cards, Study, Review, Course Map, and Explore;
- responsive desktop/bottom navigation, a skip link, route-heading focus, one
  main landmark per rendered destination, and explicit status/error semantics;
- extracted feature modules and tests for route, Sources, Chat, Studio, and
  cross-course race behavior;
- reuse of the P0.1-P0.3 backend with no schema or response-model migration.

Explicitly excluded:

- P0.5 autosave, unsaved-change guards, recoverable/cancellable tasks,
  trash/undo, and backup/restore UI;
- P1.1 free Notes, save-answer-to-note, and note-to-Source;
- P1.2 a persistent Studio output library, Overview, FAQ, Study Guide, Quiz,
  or Flashcard generators;
- P1.3 onboarding, sample courses, global search, localization, and complete
  product-wide accessibility remediation;
- P1.4 completion of the `App.tsx` decomposition, a shared API client,
  route-level code splitting, and durable app-level end-to-end automation;
- a database migration or change to retrieval/citation semantics.

### Navigation and URL contract

Canonical primary routes:

```text
?view=sources&course={course_id}
?view=chat&course={course_id}&conversation={conversation_id}
?view=studio&tool={cards|study|review|map|explore}&course={course_id}
```

Destination-owned optional parameters:

```text
Sources: source, job
Chat:    conversation
Studio:  card
Study:   card, document
```

Irrelevant entity parameters are removed when the destination changes.
Changing course clears entity selections unless the navigation explicitly
supplies a replacement in the new course.

Legacy compatibility:

```text
workspace + no card -> sources
workspace + card    -> studio/cards
study               -> studio/study
review              -> studio/review
course-map          -> studio/map
graph               -> studio/explore
missing + card      -> studio/cards
missing without card, or unknown -> sources
```

The route module is pure: it parses, normalizes, serializes, canonicalizes, and
builds URLs without mutating browser history. The App host owns normal
`pushState`, repair `replaceState`, startup canonicalization, and `popstate`.
Writing the current canonical URL is a no-op rather than a duplicate entry.

The route retains query parameters it does not own. It removes invalid empty
IDs and validates entity ownership only after the current course collection
has loaded. An invalid course or cross-course entity is repaired with
`replaceState`, so a bad link cannot display stale data and the Back button
does not revisit the rejected state.

### Source scope semantics

Two similar-looking controls intentionally have different lifetimes:

```text
Source.enabled
  = course-wide permission for a Source to participate in future retrieval

conversation.selected_source_ids
  = durable evidence subset used by one Chat conversation
```

Sources owns import, extraction state, global enable/disable, indexing,
deletion, and canonical chunk inspection. Chat may select only enabled, ready
Sources and persists that narrower list on the conversation. One conversation
cannot disable a Source for another. Disabling a Source globally affects
future retrieval but does not rewrite historical messages or citation
snapshots.

### Implementation architecture and technology

The stage stays inside the existing React 19, TypeScript 6, Vite 8,
Testing Library, Vitest, and CSS stack. It does not add a routing or state
management dependency.

New or extracted responsibilities:

| Slice | Responsibility |
| --- | --- |
| `features/navigation/appRoute.ts` | Typed route grammar, pure parsing/serialization, canonicalization, legacy mapping, destination ownership |
| `AppSidebar.tsx` | Exactly three accessible primary links |
| `features/sources/sourceApi.ts` | Source catalog, chunk, enable, index, import, and delete HTTP boundary |
| `features/sources/SourcesLibrary.tsx` | Mixed Source list, state summaries, document import, global lifecycle controls, chunk preview, deep-link repair |
| `features/chat/ChatWorkspace.tsx` | Route-level Chat heading, course scope, and full Chat surface |
| `features/chat/useChat.ts` | Conversation URL restoration and course/request epochs in addition to existing durable Chat behavior |
| `features/studio/StudioWorkspace.tsx` | Studio heading, course scope, secondary link navigation, and tool outlet |
| `features/studio/CardsWorkspace.tsx` | Card landing, stats, filters, grid, loading, and empty states |
| `App.tsx` | Host orchestration, the only integrated history writer, validated course transitions, legacy workflow composition |

Study no longer presents a second document-import/delete owner. It reads and
selects existing Sources for document generation and sends the user to Sources
for lifecycle management.

Course-scoped loaders use the mechanism appropriate to their ownership:

- `AbortController` cancels fetches when a feature unmounts or course changes;
- request epochs reject results from a previous feature/course lifetime;
- monotonic sequence references protect legacy App-owned jobs, card index, and
  card detail loads;
- state is cleared immediately on course change before the next request
  begins;
- route restoration waits for the current course collection before accepting
  a deep-linked entity.

The responsive shell turns the desktop sidebar into a labeled bottom
navigation at the narrow breakpoint. The card rail becomes inert and
`aria-hidden` when closed, exposes expanded state on its toggle, closes with
Escape, and returns focus. Nested feature `<main>` elements and duplicate route
`<h1>` elements were replaced by one host main and one route heading.

### Decisions and evidence

1. **Group by learner intent, not code history.** Sources, Chat, and Studio
   match evidence, reasoning, and artifact workflows. Cards, Study, Review,
   Map, and Explore remain useful but do not each define a product.
2. **Use real links backed by a pure route contract.** Links preserve browser
   behavior and are inspectable and testable. Pure functions separate URL
   grammar from React state and history side effects.
3. **Canonicalize legacy links instead of breaking them.** Browser history,
   exported Markdown, documentation, and bookmarks are part of a local-first
   user's durable workspace.
4. **Avoid a router migration in an information-architecture stage.** The
   query-based contract already represents the required deep links. Adding a
   framework would broaden regression risk without improving the current user
   outcome.
5. **Make course selection an isolation boundary.** UI clearing plus
   cancellation/epochs prevents a slow course-A response from corrupting the
   visible course-B workspace.
6. **Keep global Source availability separate from conversation scope.**
   Notebook policy and question scope have different consequences and
   persistence lifetimes.
7. **Do not invent an empty Studio Overview.** Cards is the useful default.
   The output-library data model and product language belong to P1.2.
8. **Do not couple navigation to a schema change.** P0.1-P0.3 already expose
   the required backend contracts. An additive frontend stage is easier to
   verify and roll back.

Alternatives rejected in
[`ADR-0005`](decisions/ADR-0005-source-first-workspace-and-route-contract.md)
include cosmetic relabeling, retaining five primary tools, feature-owned
history writes, conflating enabled and selected Sources, immediately adding an
empty Overview, removing legacy URLs, and introducing a routing framework
during this stage.

### Problems encountered and resolutions

1. **URL state and component state could become two sources of truth.** The
   first integration points still had feature-specific navigation behavior.
   Route parsing and serialization moved into one pure module, while App owns
   the commit policy and passes callbacks into features.
2. **Legacy URLs encoded product history rather than the new hierarchy.**
   Explicit migration rules preserve every old view, including the important
   distinction between Workspace with and without a card.
3. **Changing course could display a late response from the old course.**
   Existing views were audited independently. Abort controllers, request
   epochs, monotonic sequences, immediate state invalidation, and regression
   tests now protect the extracted and legacy loaders.
4. **A deep link could name an entity before ownership data was available.**
   Job and card restoration now waits for the current course list. Unknown or
   wrong-course IDs show a scoped error and repair the URL rather than opening
   whatever object happens to remain in memory.
5. **Chat needed to reconcile URL restoration with its default
   conversation.** A requested conversation is opened when it belongs to the
   course. Otherwise the first valid conversation is selected and the URL is
   replaced, not pushed. Creating or intentionally selecting a conversation
   pushes a navigable history entry.
6. **Source lifecycle controls were duplicated in Study.** Study keeps the
   evidence-selection workflow required for generation but delegates import
   and deletion to Sources through an explicit “Manage sources” action.
7. **Embedding existing features produced nested main landmarks and duplicate
   headings.** The route shell now owns `<main>` and `<h1>`; embedded feature
   views render sections and subordinate headings.
8. **The closed card rail remained in the keyboard/accessibility tree.** The
   rail is inert and hidden from assistive technology when closed, exposes
   control state, supports Escape, and restores focus.
9. **The narrow rail became icon-only.** The responsive design now uses a
   labeled bottom navigation and adjusts content and card-rail geometry around
   it.
10. **The host remains structurally large.** Before P0.4 the audit recorded
    approximately 4,087 lines and 62 `useState` calls in `App.tsx`. The current
    checkpoint measures 4,759 lines and 66 `useState` declarations
    calls because P0.4 added host integration while extracting new route and
    workspace slices. This stage reduces responsibility concentration but not
    host size. A claimed `App.tsx` refactor would be false; decomposition and
    shared API/state boundaries remain a P1.4 acceptance gate.
11. **The production bundle crossed Vite's default warning threshold during
    implementation.** The accepted build retains a 537.76 kB main JavaScript
    chunk (160.42 kB gzip) and Vite's greater-than-500-kB warning. Route-level
    code splitting and host decomposition are P1.4 work; P0.4 records the
    warning rather than hiding it.
12. **Windows blocks the `npm.ps1` shim under the current execution policy.**
    Validation uses the equivalent `npm.cmd` entrypoint and does not change
    machine policy.
13. **Explore overflowed at a 1280-pixel desktop viewport during final
    acceptance.** The graph toolbar retained a three-column grid after the
    Studio host removed its local course selector, constraining the recompute
    controls to the wrong column. The embedded variant now uses a two-column
    grid, a component regression test fixes the ownership contract, and both
    1280x720 and 360x640 browser checks report no horizontal overflow.

### Verification matrix

Every row below was rerun or inspected from the accepted P0.4 tree.

| Area | Automated or manual coverage | Current record |
| --- | --- | --- |
| Route grammar | Canonical parse/serialize, owned-parameter cleanup, course-change cleanup, all legacy mappings, unknown values, non-owned query retention, and no history side effects | `appRoute.test.ts` passed inside the final 16-file / 104-test suite |
| Primary navigation | Three real links, labels, active state, canonical hrefs, and modified-click preservation | `AppSidebar.test.tsx` and route-level App tests passed inside the final suite |
| Sources API | List/chunks, enable, index, import, delete, response/error/204 handling | `sourceApi.test.ts` passed inside the final suite |
| Sources workspace | Mixed list, exact preview/locator, import, empty/loading/error/status behavior, invalid deep-link repair, refresh preservation, and late course-A response isolation | `SourcesLibrary.test.tsx` passed inside the final suite |
| Chat route shell | Course selector, full Chat composition, conversation restoration/change events, source scope, and accessible message-log semantics | `ChatWorkspace.test.tsx`, `ChatPanel.test.tsx`, and `useChat.route.test.tsx` passed inside the final suite |
| Studio | Five link destinations, active tool, Cards filters/grid/loading/empty, embedded Explore toolbar, and preserved tool hrefs | `StudioWorkspace.test.tsx`, `CardsWorkspace.test.tsx`, and `GraphView.test.tsx` passed inside the final suite |
| Course isolation | Abort/epoch behavior and “late A never overwrites B” for host-owned state, Study, Review, Course Map, and Explore | `App.route.test.tsx` plus the four feature-view test files passed inside the final suite |
| Full frontend | All Vitest suites | `npm.cmd test -- --run`: **16 files, 104 tests passed** in 11.37 s |
| Static frontend | ESLint | `npm.cmd run lint`: passed with no findings |
| Production frontend | TypeScript project build and Vite production bundle | `npm.cmd run build`: passed; HTML 0.46 kB (0.29 gzip), CSS 81.64 kB (14.54 gzip), Citation Inspector JS 7.75 kB (2.58 gzip), Study JS 129.28 kB (38.75 gzip), main JS 537.76 kB (160.42 gzip); the main chunk retains the documented >500 kB warning |
| Dependencies | High-severity npm audit | `npm.cmd audit --audit-level=high`: **0 vulnerabilities** |
| Backend regression | Full pytest suite even though P0.4 has no backend/schema change | `python -m pytest`: **526 passed, 1 skipped, 1 upstream deprecation warning** |
| Repository hygiene | Whitespace/error check and scoped diff review | `git diff --check`: passed; final status and diff statistics reviewed before staging |
| Desktop browser | Sources -> Chat -> Studio, every Studio tool, mixed Source preview, explicit video opening, Chat history shell, routing, and citation continuity | 1280x720 against an isolated SQLite directory and a course containing imported `README.md`: Source preview/locator, hidden-by-default video workflow, Chat source/history surface, five Studio tools, and route headings passed with no application console warnings/errors or horizontal overflow. Durable conversation/citation behavior remains covered by the accepted P0.2/P0.3 browser fixtures and the unchanged passing integration suites |
| Narrow browser | Labeled bottom navigation, no horizontal overflow, usable Source/Chat/Studio layouts, card rail geometry, and keyboard focus | True 360x640 browser override: Sources, Chat composer, horizontally scrollable Studio tool bar, and Explore passed; the composer scrolled above the fixed bottom bar; App integration tests passed the rail inert/Escape/focus-return contract |
| History compatibility | Paste every legacy URL, canonical replacement, deliberate pushes, Back/Forward restoration, and invalid entity repair | Real browser passed Workspace, Study, Review, Course Map, Graph, unknown view, and missing-view-card mappings; deliberate Sources -> Chat -> Cards -> Study pushes restored through Back/Forward; an invalid card was removed after canonical Studio/Cards migration |
| Accessibility smoke | Skip link, one main, one route heading, heading focus after navigation, native link behavior, rail Escape/focus return, and announced states | Desktop and narrow browser checks found one `main`, one route `h1`, `#main-content` skip target, destination heading focus, labeled native links, and zero application console warnings/errors; App/component tests cover alerts/statuses and closed-rail keyboard behavior |

### Known limitations

- P0.4 establishes one visible Sources catalog, but the older video ingestion,
  transcript selection, and card-generation implementation remains a large
  host-owned compatibility workflow. It is on demand rather than visible by
  default; extraction into its own feature slice remains P1.4.
- Studio is an information architecture around existing tools, not yet a
  persistent library of generated outputs.
- `App.tsx` remains 4,759 lines with 66 `useState` declarations and still
  coordinates substantial API and state logic. Extracted slices are a
  boundary, not completion of P1.4.
- There is no durable app-level end-to-end test suite yet. Component coverage
  now includes route-level App integration tests, but real-browser acceptance
  remains a manual checkpoint.
- The production build retains a 537.76 kB main JavaScript chunk and Vite's
  >500 kB warning; code splitting and host decomposition remain P1.4.
- Product-wide dirty-state protection, auto-save, task recovery,
  backup/restore, Notes, onboarding, global search, and full accessibility
  remediation are intentionally not claimed here.
- The current desktop release remains `v0.1.1`; the accepted P0.4 branch is
  not yet a newly packaged desktop release.

### Git checkpoint

Intended commit subject:

```text
feat(workspace): organize notebooks around sources chat and studio
```

Final checkpoint policy:

```text
commit identity: the independent commit containing this P0.4 entry
remote verification: origin/codex/notebooklm-product-core must equal local HEAD
```

The immutable SHA is reported by Git and in the delivery summary; a commit
cannot truthfully contain its own hash. The remote equality check is performed
after the commit is pushed.

### Next gate

With P0.4 accepted and pushed, P0.5 hardens the local notebook lifecycle:

```text
automatic draft preservation
-> unsaved-change protection and recoverable deletion
-> cancellable/retryable/restart-recoverable processing tasks
-> validated backup and restore
-> safe desktop shutdown and restart recovery
```

P0.5 must preserve the Source/Chat/Studio route and course-isolation contracts
rather than creating another task-specific navigation surface.

## P0.5 - Local Workspace Reliability and Recovery

Date: 2026-07-27

Branch: `codex/notebooklm-product-core`

Status: Complete

### User outcome

The local notebook no longer treats a renderer refresh, backend restart,
failed model call, or mistaken delete as an unrecoverable boundary:

```text
editing
-> immediate device draft
-> revisioned workspace draft
-> explicit domain save

long-running operation
-> durable reservation
-> visible progress
-> cooperative cancel / retry / restart recovery

ordinary delete
-> Trash
-> same-identity restore
-> explicit permanent purge

workspace disaster recovery
-> validated full backup
-> restart-bound staged restore
-> initialization check
-> applied result or rollback
```

Activity and Data & recovery are global utilities rather than a fourth
primary destination. The canonical Sources / Chat / Studio structure and
course-scoped URL contract remain intact.

### Why this stage now

P0.1-P0.4 made the product useful enough that losing work became the highest
risk. The previous system mixed request-owned background work, hard deletion,
domain-specific status rows, open-database filesystem replacement, and a
desktop host that inferred ownership from a TCP port. Those shortcuts are
acceptable for a prototype but not for a flagship portfolio project whose
central claim is local-first ownership.

Reliability is implemented before Notes because free notes, saved answers, and
Studio outputs would otherwise multiply the same loss and recovery problems.
P0.5 establishes shared primitives that P1 features can reuse instead of
building another special case.

### Scope and non-goals

Delivered:

- reusable device-first and revisioned workspace draft persistence;
- save/conflict state and browser/window leave protection on the main editing
  surfaces;
- one durable task protocol for video processing, Source import/indexing,
  automatic cards, Chat generation, and Study generation;
- persisted progress/events, bounded execution, idempotent reservation,
  claim fencing, cooperative cancellation, retry, and startup recovery;
- recoverable deletion for courses, video jobs, document Sources, knowledge
  cards, Study documents, and Chat conversations;
- full-workspace backup validation, import/export, staged restore, an
  additional pre-restore safety archive, path rebasing, and restart-time
  publication;
- global Activity and Data & recovery surfaces;
- exact desktop backend instance identity and owned-child shutdown.

Explicit non-goals:

- cloud sync, accounts, remote workers, or multi-device collaboration;
- preempting arbitrary native Whisper, FFmpeg, or LLM calls mid-instruction;
- encrypting backup archives or managing encryption keys;
- an automatic Trash retention scheduler;
- packaging and publishing a new desktop release;
- the App decomposition and bundle work reserved for P1.4.

### Architecture and invariants

#### Drafts

Draft persistence and domain publication are separate:

```text
keystroke
-> localStorage record in the active workspace generation
-> 800 ms debounced workspace_drafts compare-and-swap
-> explicit Save publishes the domain entity/version
-> successful publication clears the draft
```

The renderer registers whether every dirty surface is protected. A
`beforeunload` warning exists only while a change is not durable on either the
device or server. Restore changes the workspace generation so a newer draft
from the replaced workspace cannot silently overwrite restored data. Device
protection is credited only after writing and reading back the exact current
payload, not merely because an older key exists. While a queued restore is
pending, the renderer keeps polling its exact identity; observing a new
authoritative generation quarantines old drafts and reloads the application
even when the backend was restarted outside the desktop restart command.

#### Durable tasks

The generic state machine is:

```text
queued -> running -> succeeded
queued -> canceled
running -> canceling -> canceled
running -> failed
canceling -> succeeded when completion beat the cancellation checkpoint
running at startup -> failed(error_code = interrupted)
canceling at startup -> canceled
retryable failed/canceled -> queued as the next attempt while attempts remain
```

Reservation occurs before execution. A bounded `ThreadPoolExecutor` claims
SQLite rows with a new token for every attempt. Progress, heartbeat, result,
error, and an append-only event stream are persisted. Only the current claim
may publish. A normal handler return is the publication boundary: success may
win from either `running` or `canceling`, while a cancellation observed at a
pre-publication checkpoint settles as `canceled`. Single-result Chat and Study
handlers checkpoint immediately before publication; chunked and pipeline
workflows checkpoint at safe boundaries and may retain completed
stages/chunks. Chat retries retain the original client request ID.

Automatic cards add a second, domain-level publication ledger. Each transcript
chunk publishes its cards, review items, and succeeded result in one immediate
SQLite transaction. A retried task reconstructs counters from that ledger and
invokes the model only for unfinished chunks. Persisting the reliable-task
reservation is the enqueue success boundary: an optional immediate worker
wakeup is best-effort, while a true pre-reservation enqueue failure closes the
already-created Source or card-generation record as visibly failed.

Quiescing stops new dispatch, requests cooperative cancellation, and waits for
active handlers to reach a checkpoint. A timeout is reported as a forced
shutdown condition; it is never described as safely checkpointed.

#### Trash and workspace lifecycle

Normal delete tombstones the root and creates a durable Trash item. Ordinary
queries filter tombstoned roots while dependent rows, managed files, canonical
Source projections, chunks, and embeddings remain available for restoration.
Restore keeps the same IDs and relationships.

Restore and purge claim a Trash item by compare-and-swap. Once purge starts,
restore is forbidden. Because existing entity stores and filesystem cleanup
cannot share one transaction, course-tree purge persists a monotonic phase
plan and resumes only unfinished irreversible steps. File-backed video and
document roots likewise retain their Trash item while projection, database,
and artifact phases advance; a file error becomes retryable `purge_failed`
instead of a false success. Enqueue, retry, delete, restore, purge, backup, and
restore preparation share the workspace lifecycle gate so an operation cannot
pass a stale resource check while a conflicting mutation is published.

#### Backup and restore

A `.vcc-backup` archive contains:

- a SQLite online-backup snapshot including committed WAL state;
- managed uploads, extracted audio, transcripts, and imported Source files;
- a versioned manifest with application/format/schema identity, relative
  paths, sizes, SHA-256 hashes, and archive entry/file counts.

Validation treats every archive as untrusted. It rejects traversal, absolute
or duplicate paths, Windows device names, symbolic links/special entries,
size and compression-ratio bombs, hash/size mismatch, invalid SQLite, missing
managed references, and unknown future schema.

Restore is a two-phase, restart-bound operation:

```text
queue one restore identity
-> restart owned backend
-> revalidate, extract, and rebase managed paths
-> create a pre-restore safety archive when a current database exists
-> checkpoint and isolate the live SQLite database family
-> write the receipt and swap the staged workspace before init_db
-> initialize, migrate, reconcile, and check workspace
-> finalize receipt as applied

initialization failure
-> rollback from retained pre-swap transaction paths
-> persist an explicit failed result
```

Queue identity and the last result are durable. The UI confirms that the same
restore ID reached `applied`; backend readiness alone is not success. A queued
restore can be canceled before restart and cannot be silently replaced by a
second operation.

#### Desktop ownership

Tauri generates a UUID instance token for every sidecar spawn and passes it in
the environment. `/health` returns exact JSON application, API, and instance
identity. Ready and quiesce checks parse that contract; substring matching is
not used. The host stores child handle, PID, and token, clears ownership on the
matching termination event, and never enumerates or kills an unknown process
because it occupies port 8001.

### Technology and responsibility map

| Concern | Technology | Responsibility |
| --- | --- | --- |
| Draft fallback | versioned browser `localStorage` | protect an edit before the network round trip |
| Durable draft copy | SQLite `workspace_drafts` + revision CAS | backup-visible recovery and conflict detection alongside the newer device draft |
| Task queue | SQLite task/event tables | durable reservation, lineage, progress, and safe public errors |
| Local worker | Python `ThreadPoolExecutor` + dispatcher | bounded execution without Redis/Celery |
| Lifecycle serialization | Python re-entrant workspace gate + SQLite transactions | close enqueue/delete/backup time-of-check races |
| Trash | tombstones + Trash intent journal | hide, restore, and separately purge user roots |
| DB backup | Python SQLite online Backup API | consistent committed database snapshot |
| Archive | ZIP + JSON manifest + SHA-256 | portable and independently validated workspace package |
| Restore | staging directories + atomic replacement + durable receipt | keep live DB handles out of replacement |
| Desktop identity | Tauri child handle/PID + UUID handshake | control only the backend instance this app owns |
| Product UI | React context/hooks + global utility drawer + operation epochs | cross-route drafts, task activity, Trash, recovery, and rejection of late responses after scope changes |

The implementation stays inside the existing Python 3.11, FastAPI, Pydantic,
SQLite, React 19, TypeScript 6, Vite 8, Vitest/Testing Library, Rust, and
Tauri 2 stack. `serde_json` and UUID v4 are direct Rust dependencies for the
strict desktop handshake; no broker, cloud SDK, or global frontend state
library is added.

Schema checkpoint: v4 -> v7. v5 adds workspace drafts, durable tasks,
tombstones, and Trash; v6 separates `restore_failed` from `purge_failed`; v7
adds the automatic-card per-chunk publication ledger.

### Problems encountered and resolutions

1. **Request-owned background work did not survive process lifetime.**
   FastAPI background callbacks could leave a domain row in a permanent
   in-between state and had no generic claim or cancellation lineage. The
   resolution is persist-first task reservation plus a bounded worker whose
   state is entirely reconstructable from SQLite.
2. **A cancellation arriving after publication could make the task history
   lie.** A handler could commit a Chat answer, Study document, completed video,
   ready Source index, or automatic-card run, receive a late cancellation
   before returning, and then be recorded as `canceled`. Domain handlers
   checkpoint before publication and never call a cancellation-aware progress
   callback after their commit boundary; a normal handler return can atomically
   win success from the same `running` or `canceling` claim while retaining the
   cancellation timestamp for audit. Automatic cards also reconcile a
   cancellation against the chunk ledger: when every selected chunk is already
   published, the service returns normally so task success wins.
3. **Chat retry initially defeated its own idempotency.** Appending a retry
   suffix to `client_request_id` could duplicate a completed turn if the
   process died between domain commit and task settlement. Every attempt now
   reuses the original request identity and replays the committed turn.
4. **Task tests leaked workers across isolated SQLite databases.** API tests
   swapped temporary database paths while a cached manager still owned
   threads. The fixture now waits for idle, shuts down and clears the cached
   manager before replacing the database.
5. **Soft delete initially removed the canonical Source projection.** That
   made a restored Source lose stable citation/index evidence. Tombstoned
   projections, chunks, and embeddings now survive until explicit purge;
   user-facing Source reads filter their deleted roots.
6. **A delete guard without a shared lifecycle boundary had a race.** An
   enqueue could occur after the guard query but before delete. Conflicting
   enqueue/retry/delete/restore/purge operations now use one lifecycle gate,
   and retry revalidates that its course/resource is active.
7. **Permanent course purge could fail after deleting only part of a
   subtree.** Blind Trash status updates could then allow restoration of an
   incomplete course. Trash operations now claim state with CAS, distinguish
   restore from purge failure, forbid restore once purge begins, and commit
   a durable phase after each idempotent purge step so retry resumes forward
   without exposing a partially purged course.
8. **A database-only or live restore was not a workspace restore.** SQLite
   paths refer to managed media and Windows prevents safe replacement through
   open handles. The archive includes managed files, and restore is queued,
   staged, and applied before the application opens the database.
9. **Restore readiness and restore success were conflated.** A failed apply
   could roll back and still expose a healthy API, causing a false success
   message. Queue/result identity is now durable and the renderer waits for
   the same restore ID to reach `applied`.
10. **Clean-install restore assumed an existing database.** The automatic
    pre-restore safety archive failed when no current DB existed. The restore
    path now skips that additional snapshot only for a genuinely empty
    workspace and still validates the imported archive.
11. **Device persistence failures were initially classified as protected.**
    `localStorage` can throw a `SecurityError`; a successful no-op mock can
    also leave no record. Protection now depends on a confirmed device record
    or server save, and regression tests retain the leave warning while
    neither exists.
12. **Restore could replay a draft from the replaced workspace.** Local draft
    keys originally used only the entity draft ID and preferred a newer
    device timestamp. A durable workspace generation now namespaces device
    drafts; a restored workspace never consumes the prior generation.
13. **Port identity was weaker than process ownership.** A string search in a
    health response could accept the wrong service or another VCC instance;
    an exited child handle could remain stale. Health is parsed as exact JSON,
    a per-spawn token protects ready/quiesce, and the matching termination
    event clears `{handle, pid, token}` ownership.
14. **The initial quiesce endpoint returned before running handlers stopped.**
    `ThreadPoolExecutor.shutdown(wait=False)` cannot stop already running
    work. Quiesce now stops dispatch, requests cooperative cancellation, waits
    for manager idle, and returns success only after that condition.
15. **The host and production bundle remain structurally large.** Reliability
    adds global orchestration to an already large `App.tsx` and keeps the
    Vite main-chunk warning. The stage records this honestly; feature slices,
    a shared API client, route-level splitting, and automated UI journeys are
    P1.4 work.
16. **A valid old SQLite WAL could overwrite a newly restored main database.**
    SQLite `quick_check` can still report `ok` after that replay. Restore and
    rollback now treat the database as a file family: old `-wal`, `-shm`, and
    rollback-journal sidecars are transactionally quarantined before the new
    main file is published, and interrupted rollback resumes from its durable
    receipt.
17. **A course purge plan could retain the old workspace's absolute paths
    after portable restore.** Restore now parses only the versioned,
    allow-listed course-purge metadata shape, rebases paths from the manifest's
    source data directory, rejects external or unknown fields before swap, and
    revalidates containment again before deletion.
18. **Single video and document purge removed its retry handle before file
    deletion.** A locked Windows file could leave an orphan, and Source cleanup
    previously swallowed its `OSError`. File-backed roots now persist
    `planned -> projection -> database -> artifacts`, retain the Trash item as
    `purge_failed`, and resume the unfinished artifact phase on retry.
19. **A process exit could leave Trash permanently claimed.** `restoring` and
    `purging` were neither terminal nor claimable after restart. Startup now
    releases those claims in one transaction as `restore_failed` and
    `purge_failed`, preserves every phase-plan field, and does so before any
    worker dispatch.
20. **A root whitelist alone did not prove file ownership.** A structurally
    valid entity plan could name another file inside `uploads` or `sources`.
    Entity plan v2 binds video/audio/transcript names to the job ID and Source
    paths to `<course_id>/<asset_id>.<supported extension>`; restore validates
    the same shared schema before swapping an imported workspace.
21. **A course purge plan could claim another course's flat video files.**
    Course plan v2 moves artifact deletion ahead of destructive row deletion,
    requires exact managed roots and course Source namespaces, and compares the
    pending artifact set with the still-present Job/Source records. Only after
    that file phase is durable may the database subtree be removed.
22. **Restore generation was published before its irreversible commit
    fence.** A crash could leave a `swapped` receipt with generation `N+1`,
    permit rollback, and then report a failed generation `N`. Finalization now
    first atomically writes a `finalizing` receipt with its commit timestamp;
    state, result, and receipt-last cleanup are idempotent consequences of that
    write-ahead fence, and no later startup may roll it back.
23. **A completed rollback could disappear between cleanup and failure
    publication.** Removing the pending marker or receipt before persisting the
    failed result made the next startup either loop on an incomplete marker or
    silently forget the restore failure. A `rollback_finalizing` write-ahead
    fence now records the stable failure identity, original generation, error,
    and completion time immediately after the filesystem rollback. Failure
    archive/result publication and marker, transaction, and receipt-last
    cleanup are idempotent, including failures raised inside the original
    apply process.
24. **Canonical file names alone did not exclude a second database owner.**
    A corrupted record could point at another job's otherwise valid
    `uploads/<job_id>.<ext>` path. Imported workspaces now require canonical
    Job, transcript, and Source paths and globally unique normalized managed
    paths. Runtime purge independently scans all remaining Job and Source
    records immediately before unlinking; a cross-entity or cross-course
    reference changes the operation to retryable `purge_failed` and preserves
    the file.
25. **An older device draft could falsely protect the newest edit.** Merely
    finding a local-storage key did not prove that the latest serialization
    succeeded, so a quota or security failure could suppress the leave warning
    while the newest text existed nowhere durable. The hook now writes and
    reads back the exact current payload and credits only that payload or the
    matching server revision.
26. **Backend restart and renderer lifetime were incorrectly assumed to be
    identical.** A restore completed after a manual backend restart could
    change the workspace generation without remounting React, leaving old
    course state and device drafts alive. The reliability provider polls the
    exact queued restore identity and reloads on the authoritative generation
    transition, independent of how the backend restarted.
27. **A durable reservation could be mistaken for an enqueue failure.**
    Immediate executor notification originally threw after the task row had
    committed; callers then marked their Source or automatic-card run failed
    even though the dispatcher could still execute it. Notification is now
    best-effort after reservation. Failures before reservation explicitly
    close the domain row, keep the uploaded Source recoverable, and expose a
    same-resource requeue path.
28. **Automatic-card retry could replay already published model output.** A
    process exit after card insertion but before run settlement caused a full
    rerun, and title-based deduplication was neither an atomic commit record nor
    a reliable identity. Schema v7 records each chunk outcome; cards, review
    items, and the succeeded ledger row commit in one transaction, IDs are
    deterministic within the run, and retry reconstructs progress before
    generating only unfinished chunks.
29. **Course purge could unlink twice across its irreversible boundary.**
    After the artifact phase had deleted the old files, database cleanup called
    entity purge helpers that touched the same paths again. If the process
    crashed and another entity acquired a canonical path before retry, the new
    file could be deleted. Post-artifact course cleanup is now projection- and
    records-only, with a crash/rebuild/retry regression test.
30. **String coercion made corrupted ownership records look valid.** Runtime
    purge converted arbitrary SQLite values with `str()`, allowing BLOB, empty,
    relative, non-canonical, or platform-colliding identifiers and paths to
    evade the intended checks. The ownership scan now validates raw types,
    canonical namespaces, managed containment, and normalized global
    uniqueness before any unlink.
31. **A parent purge could erase the only retry journal for an incomplete
    child purge.** Once the child database row was gone, the course subtree
    cleanup could no longer infer that the child's artifact phase had failed.
    A parent now checks every same-course child Trash journal first and stops
    until each child has durably reached its artifact boundary; the child
    handle remains retryable and the parent resumes only afterward.
32. **Late frontend responses crossed course and resource boundaries.**
    Cancel, retry, chunk generation, Study generation, Chat settlement, and
    card-generation responses could arrive after navigation and overwrite the
    newly selected scope. Each async family now carries an operation epoch plus
    its course, resource, or task identity; switching scope aborts supported
    requests and invalidates every older completion before state publication.
33. **Chunk isolation accidentally converted partial failure into overall
    success.** The per-chunk loop recorded a failed chunk but swallowed its
    exception, then unconditionally completed the run and reliable task. A
    final immediate transaction now derives counters, errors, and status from
    every selected ledger row. Failed or missing chunks fail the task and keep
    it retryable; retry preserves the same task payload and run identity while
    regenerating only unfinished chunks.
34. **A late executor could downgrade published success.** Failure upsert
    originally replaced an existing succeeded row, and stale in-memory
    counters could then overwrite the run despite physical cards already being
    committed. Succeeded chunk rows are monotonic, one compare-and-swap claim
    owns an active run attempt, and every progress/final state is reconstructed
    transactionally from the ledger. Concurrent invocation, stale
    reconciliation, and final-publication cancellation are regression-tested.

### Verification matrix

The final P0.5 checkpoint was validated from one tree after all audit fixes:

| Area | Coverage | Result |
| --- | --- | --- |
| Drafts | local-first save, server debounce, conflict, restore precedence, storage failure, leave warning, workspace generation isolation | Backend draft API: 3 passed; frontend reliability and stale-response checkpoint: 10 files / 57 passed |
| Durable task store | reservation idempotency, active key, claim fencing, progress/events, cancel, retry, startup recovery | 9 passed |
| Durable task manager | bounded dispatch, handler outcomes, post-handler cancel fence, quiesce, shutdown, recovery | 9 passed |
| Workflow integration | video, Source import/index, automatic cards, Chat, Study; course/resource lifecycle validation | 70 passed |
| Trash | six root types, same-ID restore, Source preservation, CAS/concurrency, failed purge safety, permanent purge | 35 passed |
| Backup/restore | WAL snapshot, files/manifest, adversarial archives, import, clean install, two-phase finalize/rollback, API status | 58 passed |
| Frontend reliability | Activity, cancel/retry, Trash/Undo, backup/import/restore, action/refresh error separation, stale polling, responsive drawer | 10 files / 57 passed |
| Full backend | complete pytest suite | 665 passed, 1 skipped, 1 existing deprecation warning |
| Full frontend | complete Vitest suite | 24 files / 154 passed |
| Static frontend | ESLint | Passed |
| Production frontend | TypeScript + Vite | Passed; existing 576.40 kB main-chunk warning retained for P1.4 |
| Desktop host | Rust unit tests/check and exact instance/quiesce contract | Format passed; 6 tests passed; check passed |
| Dependencies | high-severity npm audit | 0 vulnerabilities |
| Repository | whitespace, diff scope, tracked secret/build/cache review | Passed; no tracked secret-pattern or generated-artifact matches |
| Browser acceptance | desktop and narrow Sources / Chat / Studio plus Activity/Data & recovery smoke | Passed at 1280x720 and 360x640; backup created; no horizontal overflow or console warnings/errors |

### Known limitations

- Cancellation is cooperative. A native model/transcription call can delay
  confirmation until control returns to a checkpoint; the UI continues to say
  `Canceling` during that interval.
- The durable worker is deliberately single-process and local. It is not a
  distributed job system, and scaling it to multiple backend processes would
  require a stronger lease/coordination design.
- Trash records include a purge-after date, but automatic expiry is not
  enabled. Permanent purge remains a deliberate user action.
- Workspace archives are local and unencrypted. They may contain private
  course material and should be stored accordingly.
- Full backups can be large because they include managed videos and derived
  files. Streaming desktop export with a native save destination remains
  product-polish work; the current validated server archive is the recovery
  source of truth.
- If both restore initialization and its transaction rollback fail, the
  receipt and pre-swap transaction paths are deliberately retained for manual
  recovery instead of claiming that the workspace is safe.
- Portable backup and course purge intentionally support the standard managed
  roots under `VCC_DATA_DIR`. An out-of-tree `VCC_SOURCE_DIR` override fails
  closed during portability checks and is not a supported portable layout.
- P0.5 does not publish a new signed desktop installer. The productization
  branch remains ahead of the public `v0.1.1` release.
- `App.tsx` and the main JavaScript bundle remain above their P1.4 targets.

### Git checkpoint

Intended commit subject:

```text
feat(reliability): protect local work and recover long-running tasks
```

Final checkpoint policy:

```text
commit identity: the independent commit containing this P0.5 entry
remote verification: origin/codex/notebooklm-product-core must equal local HEAD
```

The immutable SHA is reported after commit and push; a commit cannot contain
its own hash without changing that hash.

### Next gate

P1.1 closes the knowledge-capture loop:

```text
free notebook note
-> save a grounded Chat answer as a note
-> edit and organize the note
-> promote selected note content into a retrievable Source
-> cite that Source in later Chat and Studio work
```

Notes must reuse P0.5 drafts, Trash, backup, and task/lifecycle boundaries
rather than creating another persistence path.

## P1.1 Notebook-level Notes — implementation plan and completion report

### Stage status

- **Status:** Complete
- **Completed:** 2026-07-28
- **Priority:** P1
- **User outcome:** capture an idea or grounded answer, refine it safely, and
  deliberately make one durable revision available as later evidence
- **Decision record:**
  [ADR-0007](decisions/ADR-0007-notebook-notes-and-derived-sources.md)

### Product evidence and interpretation

Official NotebookLM places Notes in Studio, supports free-form notes and
Chat-response capture, retains citations when saving an answer, and requires
an explicit conversion before note content becomes a Source. Its FAQ also
states that ordinary notes are not automatically used as evidence. Those
behaviors define the product boundary for this stage.

This project deliberately improves two parts of that contract:

1. a saved answer keeps an immutable origin/citation snapshot **and** an
   editable user working copy;
2. deleted notes use the existing local Trash/backup system instead of becoming
   immediately unrecoverable.

### Scope

P1.1 implements:

```text
Studio / Notes
  -> create a course-level free note
  -> recover unsaved editor text from device/server drafts
  -> save with optimistic revision checking
  -> soft-delete and restore through Trash

Chat
  -> save one completed grounded answer idempotently
  -> preserve its exact citation and model provenance
  -> open the resulting note in Studio

Notes -> Sources
  -> explicitly snapshot the current durable revision
  -> create or update one stable note-derived Source
  -> split Markdown into bounded, locatable sections
  -> select/search/cite it through the existing Chat pipeline
```

The accepted course-scoped HTTP boundary is:

```text
GET    /courses/{course_id}/notes
POST   /courses/{course_id}/notes
GET    /courses/{course_id}/notes/{note_id}
PATCH  /courses/{course_id}/notes/{note_id}
DELETE /courses/{course_id}/notes/{note_id}
POST   /courses/{course_id}/notes/from-chat/{message_id}
POST   /courses/{course_id}/notes/{note_id}/source
```

The explicit course segment is intentional: every list, detail, capture, edit,
delete, and promotion request validates the same notebook scope without
revealing whether an ID exists in another course.

P1.1 does not add collaborative notes, folders/tags, rich-text editing,
AI-generated note transformations, bulk note-to-source conversion, exports, or
automatic inclusion of drafts in retrieval. Those are later product slices.

### Implementation sequence

#### Gate A — durable domain contract

1. Add schema v8 tables for notebook notes and immutable Source snapshots.
2. Add typed note, origin snapshot, promotion snapshot, and note locator
   contracts.
3. Implement course-scoped stores with compare-and-swap revision updates,
   idempotent message capture, note-owned normalized citation snapshots, and
   transactional soft-delete/Trash behavior.
4. Implement atomic same-revision Source promotion and explicit later-revision
   refresh.
5. Extend Source reconciliation, active-root checks, restore/purge, course
   purge, backup schema validation, and test cleanup.

Gate A proof:

- migration, store, service, Source, Trash, and API tests pass;
- a note cannot leak across course scope or survive permanent purge as a
  selectable Source;
- concurrent stale edits and stale promotions return conflicts.

#### Gate B — bounded feature slice

1. Add `features/notes` types, API client, editor/list component, styles, and
   focused tests.
2. Add `notes` to Studio and `note` to the canonical route contract.
3. Wire only the route boundary in `App.tsx`; keep note state and requests out
   of the monolith.
4. Reuse the P0.5 draft hook for new and existing note editors.
5. Add Chat's **Save to notes** action, per-message pending/success/failure
   state, and direct navigation to the created note.
6. Render immutable Chat provenance and citation navigation in the note
   inspector.

Gate B proof:

- canonical URL/back-forward tests pass;
- course/note switches fence late requests;
- the newest editor text is protected locally or on the server;
- a repeated Chat save cannot duplicate a note;
- desktop and narrow layouts remain usable without horizontal overflow.

#### Gate C — end-to-end knowledge loop

1. Create and revise a free note.
2. Save a grounded Chat answer and verify its origin citations.
3. Publish a durable note revision as a Source.
4. Ask a later question with that Source selected and open the resulting note
   citation.
5. Delete/undo the note and verify Source visibility follows the root.
6. Run the full backend/frontend/desktop verification and record exact results,
   problems, and resolutions below this plan.

### Technology choices

| Concern | Choice | Reason |
| --- | --- | --- |
| Durable state | existing SQLite/WAL store | local-first, transactional with Trash and Source projection |
| API | existing FastAPI + Pydantic contracts | preserves validation/error conventions |
| Concurrency | monotonic note revision + compare-and-swap | rejects silent last-writer-wins data loss |
| Provenance | immutable JSON citation snapshot plus normalized promotion snapshot | preserves historical answer evidence while allowing editing |
| Retrieval | existing canonical Source/chunk/embedding pipeline | one evidence model and one citation path |
| Note Source identity | stable `note:<note_id>` with immutable revision snapshots | avoids Source-list duplication and makes refresh explicit |
| Editor recovery | P0.5 `useAutosavedDraft` | no parallel persistence mechanism |
| UI placement | Studio `notes` tool | retains Sources / Chat / Studio product structure |
| Frontend architecture | isolated `features/notes` slice | limits new `App.tsx` debt before P1.4 |

No rich-text framework, client state library, new vector database, cloud
service, or file-backed synthetic Source is added.

### Primary risks and planned controls

1. **Source reconciliation could delete note projections.** Reconciliation
   must rebuild all three root families: video jobs, imported assets, and
   published note snapshots.
2. **Editing could rewrite evidence already cited by Chat.** Promotion uses an
   immutable revision snapshot; note editing alone never mutates Source chunks.
3. **A lost POST response could duplicate a saved answer or snapshot.** The
   assistant message ID and `(note_id, note_revision)` are durable idempotency
   keys.
4. **Delete could leave a searchable Source.** Source resolution checks the
   active note root; permanent purge deletes projection, chunks, embeddings,
   snapshots, drafts, and the tombstone.
5. **Late frontend responses could overwrite another note.** Every load/save/
   publish family is fenced by course, note, revision, and operation epoch.
6. **A draft could overwrite a restored workspace or newer note.** Existing
   workspace generation isolation remains in force, and note domain updates
   require the loaded revision.
7. **The feature could worsen monolith debt.** Only route composition is added
   to `App.tsx`; implementation and tests live under `features/notes`.

### Planned verification matrix

| Area | Required proof |
| --- | --- |
| Schema | clean v8 install, v7 -> v8 migration, rollback on failed migration |
| Note domain | free create, validation, listing, revision update/conflict, course isolation |
| Chat capture | grounded-only validation, exact citation snapshot, repeated-save replay |
| Promotion | exact durable revision, same-revision replay, later-revision refresh, chunk locator |
| Sources | reconciliation preservation, active-root filtering, on-demand indexing/search |
| Lifecycle | delete, restore, purge, parent-course purge, draft cleanup |
| Routes | `tool=notes`, `note=<id>`, legacy/invalid canonicalization, back/forward |
| Frontend | empty/list/editor/provenance/error states, draft recovery, stale-response fences |
| Chat UI | pending/success/retry save action and direct note navigation |
| Full checkpoint | pytest, Vitest, lint, build, Rust check/tests, dependency and repository hygiene |
| Browser | desktop + 360 px free-note/capture/publish/cite/delete/restore journey |

### Git checkpoint

Intended independent commit subject:

```text
feat(notes): close the chat-to-source knowledge loop
```

The stage is committed and pushed only after Gate C passes. The sections below
are the evidence used to accept that gate.

### Delivered user workflow

The completed loop is:

```text
write a course Note
-> save it with revision protection
-> explicitly publish that exact revision as note:<note_id>
-> index the Note Source through the normal Source pipeline
-> select it as the only Chat evidence
-> receive a sentence-level cited answer
-> open the citation at its immutable Note section
-> save the grounded answer as a second Note
-> retain the original answer, model, and citations beside an editable copy
-> delete, undo, or restore the Note through the shared recovery system
```

This is deliberately a knowledge-capture loop, not merely a text editor.
Ordinary Notes remain private working material until the user chooses
**Publish as source**. A later edit marks the Source as outdated but does not
silently change evidence that Chat may already have cited.

### Final durable design

Schema v8 adds four normalized tables:

| Table | Responsibility |
| --- | --- |
| `notebook_notes` | Course-owned editable aggregate, origin, monotonic revision, and soft-delete state |
| `notebook_note_citations` | Note-owned immutable copies of the Sources cited by a saved Chat answer |
| `notebook_note_citation_spans` | Sentence offsets and citation ordering independent of Chat retention |
| `notebook_note_source_snapshots` | Immutable title/body/content hash for each explicitly published revision |

The separation is intentional:

- the editable `title` and `body_markdown` can advance from revision 1 to N;
- the captured Chat answer and its citations do not change when the working
  copy is edited;
- a published revision remains reconstructable even after a later revision is
  published;
- deletion visibility follows the active Note root, while permanent purge can
  remove the complete Note-owned lineage.

All seven HTTP operations are course scoped. List, create, capture, detail,
update, delete, and publish validate the course boundary before returning
domain information. Updates, deletes, and promotions use the loaded Note
revision as a compare-and-swap token; a stale client receives the current
record instead of silently overwriting it.

Chat capture accepts only a completed, grounded assistant message with at
least one canonical citation. The assistant message ID is the idempotency key:
a lost response can be retried without creating a duplicate Note. Citation
rows and sentence spans are copied into the Note transaction, so deleting a
conversation later cannot erase the research provenance of a saved answer.

### Source projection and retrieval

One Note has one stable Source identity:

```text
note:<note_id>
```

Publishing creates or reuses the immutable `(note_id, note_revision)` snapshot,
then transactionally replaces that Source's canonical chunks. Markdown is
split deterministically on paragraph boundaries with a hard 4,000-character
section limit. Each chunk receives a `note_section` locator containing the
Note ID, snapshot ID, section number, revision, and content hash.

The Source is intentionally not represented by a synthetic file. Citation
resolution reconstructs context from the immutable database snapshot and
returns no media URL. This keeps Note evidence inside the same Source,
embedding, search, Chat scope, sentence-citation, and citation-inspector
contracts used by videos and imported documents.

Publishing, reconciliation, restore, and permanent purge share the Source
projection lifecycle lock. Reconciliation re-reads canonical published Notes
inside that boundary and rebuilds projections alongside video and document
roots. This prevents a concurrent reconcile from deleting a newly published
Note Source or recreating one while it is being purged.

Soft-deleted Notes are excluded by the active-root query before Source listing,
search, Chat selection, and citation availability. Restore makes the existing
snapshot projection visible again; purge removes the projection, chunks,
embeddings, snapshots, Note-owned citation lineage, drafts, and tombstone.

### Frontend product boundary

The implementation stays inside the accepted three-part information
architecture:

```text
Sources  -> published Note Sources, preview, indexing, and Open note
Chat     -> grounded answers and Save to notes
Studio   -> Notes list, editor, origin inspector, and Source publishing
```

`features/notes` owns the API client, types, state machine, editor, provenance
view, styles, and tests. `App.tsx` owns only route composition and cross-feature
navigation. Canonical routes now preserve `tool=notes` and `note=<id>` through
reload, back/forward, and course changes.

The editor supports free Note creation, explicit save, optimistic conflicts,
Source publication/update, Source deep links, and Trash. Chat keeps
save-to-Note state per assistant message, validates the returned Note against
the active course/message, ignores stale responses after a course switch, and
opens the created Note directly. Sources classifies Note projections
separately from documents and videos and never exposes file deletion for a
Note-owned Source.

### Draft and navigation reliability completed with P1.1

P1.1 exercised P0.5 draft recovery more aggressively than card editing did.
The shared hook was therefore hardened as part of this stage:

- hydration is safe under React Strict Mode;
- text entered before server hydration completes is immediately protected in
  device storage and is never overwritten by a late response;
- distinct device and server versions remain a preferred/alternate recovery
  pair across remounts;
- base timestamps prevent an old Note draft from being silently applied to a
  newer durable Note revision;
- restoring one version quarantines the other instead of destroying it;
- **Keep current draft** retains the known server revision and synchronizes by
  CAS;
- an observed-absent server draft uses revision `0` as create-only CAS;
- a third editor that advances the draft remains visible as a conflict rather
  than being overwritten;
- pending synchronization is canceled when a successful domain save clears
  the draft.

Chat composer drafts are keyed by course and conversation. A send keeps the
submitted draft until the durable generation result is known. Success clears
only the submitted/resolved scope; failure restores only those scopes, even if
the user has already changed course or conversation.

The app-level navigation registry protects unpersisted work for primary route,
Studio tool, course, browser history, Chat conversation, Note, Study document,
and Study anchor changes. Child-owned transitions guard before changing local
state; their route synchronization then bypasses the app guard to avoid a
second prompt. Automatic canonicalization remains non-interactive.

### Problems encountered and resolutions

| Problem found during implementation or review | Resolution and retained proof |
| --- | --- |
| Reconciliation could race publication or purge and remove/recreate a Note Source | Publication, reconciliation, restore, and purge now use one re-entrant projection lifecycle lock; race regressions retain the invariant |
| A saved answer that referenced Chat-owned citation rows would lose provenance when Chat was purged | Notes copy canonical citations and spans into Note-owned tables in the capture transaction |
| A deleted Note projection could remain listable or citable | Source active-root checks include the live Note/course root; Trash and citation-target tests cover the boundary |
| One mutable Note body could not be both editable and historical evidence | The design keeps an editable aggregate plus immutable Chat-origin and published-revision snapshots |
| An old device draft could overwrite a newly saved Note revision | Recovery is base-aware and presents mismatched content as an explicit conflict |
| Aborting a request alone did not stop a late response from committing into another scope | Note loads/saves/publishes and Chat captures validate course, entity, revision, epoch, and returned response identity |
| Same-revision promotion replay could return a stale in-memory Source after reconciliation | Promotion reloads the canonical Source after the transaction |
| A Note citation has no file, page, or video media URL | `note_section` targets reconstruct immutable text context directly from the snapshot |
| Strict Mode and slow hydration exposed a window where the newest keystroke existed only in React state | Pending edits are synchronously written to device storage and an older value is quarantined before replacement |
| Chat cleared its composer before generation and could restore a failed question into the course/conversation opened later | Send returns `{ succeeded, conversationId }`; cleanup/recovery uses the captured submission scope |
| App navigation covered primary routes but child tools could mutate state before a rejected route | Shared internal navigation guards now run before Chat, Notes, and Study state transitions |
| Discarding a recovery candidate reset the revision to `null`, allowing a third editor to be overwritten | Keep-current is a dedicated CAS state machine with revision preservation, create-only revision `0`, re-read after failed conditional delete, and three-editor tests |
| Draft cleanup after a domain save or a manual return to the saved value could forget that this editor had observed no server draft, then overwrite a newly created third-party draft | Both cleanup paths retain create-only revision `0`, delete only a known positive revision, and surface a concurrent create as a conflict on the next edit |
| Successful-absence, failed-hydration, and empty-conflict paths could still leave the draft revision as unconditional `null` | The hook now treats every unknown or absent revision as create-only `0`; pending-hydration and offline races prove that a third-party create becomes a visible conflict instead of a silent overwrite |
| Sources changed its preview before the app route guard returned | `onSelectSource` returns a decision and the preview changes only after navigation succeeds |
| Study changed document/anchor state or sent a create request before checking unsaved work | Study guards document, anchor, and new-document actions before state or network effects |
| A restore test checked files but not semantic Note evidence | The backup suite now restores a Chat Note, its citation lineage, published snapshot, canonical chunks, and citation target after the newer revision was purged |

### Verification evidence

All release-blocking checks passed on Windows in the isolated project
environment:

| Gate | Command or journey | Result |
| --- | --- | --- |
| Note API/integration | Notes API file, including real async Chat-over-Note and backup/restore semantic round trip | 13 passed |
| Focused backend | Notes, migrations, and citation targets | 43 passed, 1 skipped |
| Full backend | `uv run pytest -q` | **681 passed, 1 skipped**, 1 upstream Starlette deprecation warning, 432.67 s |
| Python/lock | `compileall` plus `uv lock --check` | Passed; 120 packages resolved from the lock |
| Full frontend | `npm.cmd test` | **27 files, 214 tests passed**, 14.20 s |
| Frontend static | ESLint and TypeScript production build | Passed |
| Production bundle | Vite 8.0.16 build | Passed; Notes and citation inspector remain split chunks; main chunk 588.23 kB / 173.99 kB gzip |
| Dependency audit | `npm.cmd audit --audit-level=high` | 0 vulnerabilities |
| Desktop host | Cargo format, locked metadata, locked check, and locked tests | Passed; 6 Rust tests passed |
| Repository | `git diff --check`, generated-file and secret review | Passed before staging |

The environment-dependent skip is the existing Windows symlink-permission
test. The warning is the existing Starlette/TestClient `httpx` deprecation.
Neither was introduced by P1.1.

The real-browser acceptance used an isolated database, a cached local embedding
model, and an OpenAI-compatible deterministic fake LLM. It verified:

1. create a free Note in Studio;
2. publish revision 1 and see `note:<id>` in Sources;
3. index the Note and select it as the only Chat Source;
4. generate a grounded answer with a sentence-level `[1]` citation;
5. open the citation and see the exact immutable Note section highlighted;
6. save the answer as a Note and inspect the read-only provider, model, answer,
   and citation provenance;
7. move the published Note to Trash, restore once through **Undo**, delete
   again, and restore through **Recovery**;
8. verify the Note Source returns with its ready index;
9. inspect 1280×720 and 360×640 layouts with one `main`, one route `h1`, no
   horizontal overflow, a reachable mobile composer, and usable bottom
   navigation;
10. finish with zero application console warnings or errors.

### Known limitations and deferred work

- Notes are Markdown text, not collaborative rich text. Tags, folders,
  transformations, export, and bulk actions remain out of scope.
- Publishing is explicit and indexing remains a separate **Index ready**
  operation, consistent with all other Sources.
- Chat save success is intentionally request-local in the current UI. Reloading
  can show **Save to notes** again, but the backend message-id idempotency key
  returns the original Note rather than duplicating it.
- Browser acceptance is evidence recorded here, not yet a durable Playwright
  end-to-end suite. P1.4 owns the automated desktop/browser harness.
- A failed first Chat submission can temporarily retain the same question under
  both the pre-conversation and server-created conversation recovery scopes.
  Retrying from the resolved conversation is safe, but navigating back to the
  empty scope can reveal the stale local copy. Scope alias cleanup is retained
  as a P1.4 reliability follow-up.
- Automatic cleanup of an invalid or removed Source can clear the preview
  before an unsaved-change guard refuses the matching URL replacement. This is
  a transient route/preview mismatch with no Source mutation; guarded cleanup
  ordering is retained for P1.3 product polish.
- The 588.23 kB main chunk remains above Vite's 500 kB advisory threshold.
  Additional feature splitting belongs to P1.4.
- No new signed installer or public release is created by this checkpoint; the
  productization branch remains ahead of public `v0.1.1`.

### Git checkpoint

Independent commit subject:

```text
feat(notes): close the chat-to-source knowledge loop
```

The commit contains the schema/API, Source projection, Notes slice, reliability
hardening, tests, ADR, roadmap, and this completion record. Its immutable SHA
is reported after the commit is created and pushed; embedding that SHA in the
same commit would change the SHA. Acceptance requires local `HEAD` to equal
`origin/codex/notebooklm-product-core`.

### Next gate

P1.2 is now the next product slice: turn Study, Review, and Course Map into one
persistent Studio output library, then add Overview, FAQ, Study Guide, Quiz,
and Flashcards without creating parallel evidence or task systems.

## G1.1 - Grounded Concept graph candidate substrate

**Status:** Complete as a bounded G1 slice; full G1 remains in progress

**Date:** 2026-08-08

**Branch:** `codex/concept-graph-foundation`

### User outcome

The backend can now store and inspect human-proposed Concepts and typed Concept
relations without treating Cards, embeddings, or model output as graph truth.
Every candidate is course-scoped and carries an immutable evidence snapshot
that resolves to the canonical Source/Chunk/Locator backbone.

### Why this slice exists

Path algorithms are only defensible if their nodes and edges have stable
identity, explicit semantics, current locatable evidence, and auditable
revisions. Building BFS or a graph UI over the legacy similarity-oriented
`CardRelation` table would make a visually convincing but semantically
unreliable product. G1.1 therefore establishes the smallest vertical graph
aggregate before review workflows, graph publication, or traversal.

### Scope and non-goals

Delivered scope:

- additive Concept/relation identity heads plus immutable revision/evidence
  tables in schema v9;
- grounded human-candidate create, summary-list, and detail-read APIs;
- exact quote, current Chunk hash, typed Locator, Source/course ownership, and
  complete endpoint-evidence fingerprint validation;
- permanent canonical identity for symmetric relations and explicit directed
  relation semantics;
- strict `source_asserted` versus `pedagogical_inference` evidence-role
  contracts;
- one `BEGIN IMMEDIATE` transaction for each aggregate write and deferred
  current-revision foreign keys;
- bounded cursor pagination and safe HTTP error translation.

This slice does not accept or publish graph truth. It does not yet implement
review compare-and-swap, aliases, merges, retirement, stale transitions,
prerequisite-cycle validation, graph versions, model proposals, paths, or UI.

### Decisions and technology

SQLite remains the local source of truth. Stable head tables separate identity
from immutable revision tables so later reviews preserve history instead of
overwriting it. Evidence stores a server-resolved snapshot rather than trusting
client-provided hashes or Locators. Database constraints protect structural
invariants; the store rechecks semantic and course boundaries inside the same
serialized write transaction. FastAPI/Pydantic own transport validation and
do not infer graph facts.

Cards remain regenerable learning artifacts. A Card ID, cosine similarity, or
model confidence can propose future work but cannot satisfy evidence or create
an accepted relation. This avoids a second evidence system and preserves
compatibility with the existing Card, Topic, Explore, Chat, and citation paths.

### Problems encountered and resolutions

- A relation role check initially allowed evidence that was merely locatable
  but did not match the endpoint Concept revision. The store now compares the
  complete Source, Chunk, text-hash, quote, and canonical-Locator fingerprint.
- Symmetric edges could otherwise acquire two identities. Endpoints are
  canonicalized before the permanent uniqueness check.
- Broad SQLite integrity handling could misreport unexpected corruption as a
  duplicate. Only known conflicts map to `409`; unexpected integrity failures
  return a safe `500` and roll back.
- Clean-runner RAG tests exposed a separate database-snapshot configuration
  bug hidden by local state. The snapshot now hashes the configured database,
  and explicit mismatches are rejected rather than silently benchmarking a
  different corpus.

### Verification

- adversarial graph review: no remaining P0/P1 finding after the second pass;
- graph API/store/migration verification: 20 focused tests passed;
- combined graph and existing migration/citation compatibility selection:
  48 passed, 1 skipped, with one existing Starlette deprecation warning;
- full working-tree backend regression, including the pending evaluation
  harness: 718 passed, 2 skipped, with the same upstream warning;
- Python compilation and `git diff --check`: passed.

The skips cover environment-specific Windows symlink privileges and an
existing platform-bound path case; neither changes the graph contract.

### Git checkpoint

Independent commit subject:

```text
feat(graph): add grounded concept candidates
```

The immutable SHA is reported after the commit is created and pushed; it is
not embedded in its own content.

### Next gate

G1.2 adds review/currentness transitions, aliases and merge/retirement history,
prerequisite acyclicity, and immutable accepted graph publication. Only a
published graph version may become input to deterministic G3 traversal.

## G0.1 - License-aware public benchmark acquisition

**Status:** Complete as an acquisition slice; G0 evaluation freeze remains in progress

**Date:** 2026-08-08

**Branch:** `codex/concept-graph-foundation`

### User outcome

The repository now has a reproducible, reviewable way to identify and acquire
public course material for later graph, path, retrieval, citation, and refusal
evaluation. External bytes, development labels, and sealed labels are kept
separate, and the checkpoint makes no premature accuracy claim.

### Scope and non-goals

Delivered scope:

- a strict manifest for all eight CS336 Spring 2025 PDFs in the official
  lecture repository at one upstream commit, including URL, byte size, Git
  blob identity, SHA-256, attribution, license identity, and partition;
- authoring, development, and sealed-transfer partitions with sealed download
  disabled by default and physical directory separation;
- bounded HTTPS acquisition with exact host/redirect, header, size, count,
  aggregate, PDF-magic, deadline, content-hash, and no-overwrite checks;
- rejection of traversal, reserved names, symlinks, Windows reparse points,
  executable or mismatched existing files, and unsafe parent components;
- a self-authored CC0 counterfactual mini-course whose ingestible Source and
  gold labels are physically separated, independently hashed, and linked by
  exact Source identity;
- structured Concept/relation, citation, claim, and refusal contracts checked
  against the production graph ontology.

This slice does not parse or annotate CS336, open sealed labels, run a model,
or report quality. It is not evidence that public material was absent from a
foundation model's training data. CS61B is registered only as a future
external robustness source; no course-wide redistribution license was found,
so its slides, videos, assignments, and excerpts are not vendored.

### Decisions and technology

**Superseded by the G0.2a authority split below:** this checkpoint originally
described one later evaluation manifest as owning page ranges, labels, and run
configuration. The corrected design treats acquisition `ManifestAuthority` as
an upstream byte/rights prerequisite, then separates four downstream
authorities: protocol definition plus Source-slice freeze, a partition-bound
`GoldBundleSeal`, an automatic-proposal/Chat run family, and an append-only
access ledger. This preserves the original goal—preventing downloader changes
or labels from silently changing the ingestible corpus—without collapsing
distinct lifecycle responsibilities.

The implementation uses only Python standard-library networking, hashing,
filesystem, and JSON primitives plus typed immutable records. Downloads are
streamed into a same-directory temporary file, verified, and published without
overwrite. Hash equality establishes identity, not parser safety or license
coverage; downstream PDF parsing remains an untrusted-input boundary.

### Problems encountered and resolutions

- Resolving an explicit CLI output path before validation could erase evidence
  that it was a symlink. The CLI now passes the original path and validation
  walks existing components with `lstat` before and after creation.
- Windows junctions are directory reparse points rather than ordinary POSIX
  symlinks. File and directory checks now reject the reparse attribute at the
  pre-open, open-handle, and post-open boundaries.
- A copied relation enum could drift from production, and symmetric gold could
  disagree with stored canonical order. Cross-module tests close the ontology
  set, while the strict fixture loader enforces canonical endpoints.
- A first prerequisite label related checksum validation to the `Merin gate`
  entity rather than the atomic `Merin gate opening` concept. The gold concept
  and question were corrected, and the edge is explicitly a pedagogical
  inference with evidence matching both endpoint Concepts.
- Public availability was initially too easy to conflate with redistribution
  safety. PDFs remain gitignored; the manifest pins the upstream MIT license
  but conservatively marks every asset `redistribution_allowed=false` because
  individual slides may contain third-party figures.

### Verification

- offline benchmark acquisition/fixture slice: 21 passed, 2 skipped in
  22.93 seconds;
- the two skips are permission-dependent file/directory symlink creation on
  the current Windows account; independent simulated reparse-point checks pass;
- Python compilation, scoped diff checking, whitespace checks, strict fixture
  loading, and both SHA-256 sidecars pass;
- no test performs a network request and no downloaded PDF is committed.

### Git checkpoint

Independent commit subject:

```text
feat(eval): register public course benchmark
```

The immutable SHA is reported after the commit is created and pushed.

### Next gate

**Superseded by the G0.2a authority split below:** G0.2 does not build one
parser/chunker/label/runner manifest. It first freezes the protocol and bounded
Source slice; G2 later seals partition-bound gold; a separate future run family
owns `RunSpecSeal` and sealed predictions/results; and a future access ledger
must enforce opening and reproduction events.

## G1.2a - Non-reusable Source projection generations

**Status:** Complete as a bounded G1.2 slice; draft review lifecycle remains in progress

**Date:** 2026-08-08

**Branch:** `codex/concept-graph-foundation`

### User outcome

Concept and relation evidence can now distinguish one exact derived Source
projection from a later projection that happens to return to the same text.
Evidence reads report whether their saved generation is still eligible and
why it became stale, while preserving the historical quote and Locator.

### Why this slice exists

G1.1 saved Chunk text hash and Locator, but those fields did not encode a
monotonic publication event. A projection could move `A -> B -> A`, causing old
human-reviewed evidence to appear current again without re-review. Relation
review and immutable graph publication would be unsound if built on that
identity model, so Source generation is a prerequisite rather than a later
optimization.

### Delivered scope

- schema v10 stores an opaque projection generation and canonical manifest
  hash on every Source and snapshots the generation on new Concept/relation
  evidence;
- one canonical manifest covers contract version, stable Source ID/type, and
  ordered Chunk ID/type/ordinal/text hash/typed Locator/chunker version;
- identical consecutive publication retains its generation, while every
  changed publication receives a new UUID and `A -> B -> A` never reuses A's
  first generation;
- video, document, Note, and whole-course reconciliation share one projection
  store boundary; Note snapshot publication shares the caller's transaction;
- currentness checks active course and origin root, ready Source, generation,
  same-Source active Chunk, actual text hash, Source type, canonical Locator,
  and exact quote, returning stable bounded reason codes;
- migration validates historical text hashes and ordinal uniqueness, assigns
  Source identities, and intentionally leaves v9 graph evidence generation
  null and ineligible until regrounded;
- frontend Source contracts expose the new identity without adding a new user
  workflow.

This slice does not implement review/CAS, aliases, merge/retirement, relation
endpoint revision binding, prerequisite-cycle protection, graph publication,
paths, model proposals, or graph UI.

### Decisions and technology

The manifest is deterministic compact JSON with typed Pydantic Locator
normalization and SHA-256. The generation is random and non-reusable rather
than derived from the manifest hash; only an identical *consecutive* publish
keeps the existing UUID. This preserves drift history while still making
idempotent reconciliation cheap.

SQLite `BEGIN IMMEDIATE` serializes replacement. A partial unique index guards
active `(source_id, ordinal)` pairs, triggers require a valid Source identity,
and a unique generation index catches accidental ID reuse. Chunk replacement
uses a two-phase deactivate/delete/upsert sequence so retained IDs can swap
ordinals or shift after a front insertion without violating the unique index
in an intermediate state. Retained embeddings survive; removed Chunk
embeddings are deleted in the same transaction.

Whole-workspace restore intentionally restores the backed-up generation IDs
with the matching evidence because it swaps an exact historical database
snapshot. That is distinct from republishing a projection in the same
workspace. Video/document root creation and derived projection sync remain two
workflow steps; only projection publication itself is the atomic evidence
boundary.

### Problems encountered and resolutions

- Ready Source rows survive ordinary soft deletion. Currentness now verifies
  the active Job, SourceAsset, or NotebookNote root and course, so deleted
  material cannot ground a new candidate; unchanged restore can re-enable it.
- A caller-supplied 64-character hash was previously trusted. Replacement and
  migration now recompute SHA-256 from exact UTF-8 Chunk text and fail closed.
- `ON CONFLICT(id)` could reparent a globally stable Chunk ID to another
  Source. Ownership is checked before mutation and cross-Source replacement
  rolls back.
- Duplicate ordinals made ordering ambiguous. Store validation plus a partial
  unique index reject them.
- The first unique-index implementation rejected a valid ordinal swap because
  one old row temporarily occupied the target ordinal. Two-phase replacement
  frees the Source's active ordinal namespace inside the transaction before
  publishing the complete next projection.
- Migration and runtime initially normalized Locators differently. Both now
  pass through the same discriminated `SourceLocator` contract and canonical
  JSON serializer.

### Verification

- independent adversarial review: all P0/P1 findings closed; final two-phase
  replacement spot review approved the checkpoint;
- dedicated manifest/generation/migration/currentness focused suite: 23 passed;
- related backend compatibility selection: 198 passed, 1 skipped;
- full backend: **746 passed, 3 skipped**, one existing upstream Starlette
  deprecation warning, 297.85 seconds;
- full frontend: **27 files, 214 tests passed**, plus ESLint and production
  TypeScript/Vite build;
- Python compilation, `uv lock --check`, and `git diff --check`: passed.

The three skips are environment-dependent symlink/path cases. The production
build retains the already documented 588.23 kB main-chunk optimization warning.

### Git checkpoint

Independent commit subject:

```text
feat(evidence): version source projections
```

The immutable SHA is reported after the commit is created and pushed.

### Next gate

G1.2b adds idempotent operation records, append-only Concept/relation review
revisions, aliases, exact endpoint-revision bindings, and query-time
publication eligibility. The transactional prerequisite cycle guard was pulled
into G1.2b because accepting an unchecked prerequisite would violate draft
integrity; G1.2c then owns merge/retirement before G1.3 publishes immutable
graph versions.

## G1.2b - Revisioned Concept Graph review lifecycle

**Status:** Complete as a bounded G1.2 slice; G1.2c and initial-create reliability remain open

**Date:** 2026-08-09

**Branch:** `codex/concept-graph-foundation`

### User outcome

A grounded Concept or Relation candidate can now be edited, reviewed,
rejected, or marked stale without overwriting history. Every post-create
revision mutation is attributable, retry-safe, compare-and-swap protected, and
readable by historical revision. Accepted draft objects expose whether their
saved evidence and exact endpoint revisions are still eligible for later graph
publication.

This is a reliable draft lifecycle, not a published Concept Graph or a path
feature. Merge/retirement, initial-create receipts, immutable graph versions,
automatic candidate generation, G2 labels, G3 traversal, and G4 UI remain
separate gates.

### Delivered scope

- additive schema v11 adds revision-owned aliases, exact Relation endpoint
  revision bindings, a course-scoped operation ledger, incident-edge indexes,
  and guards against operation-receipt, endpoint-binding, or stable Relation
  identity mutation;
- Concept and Relation edit/review/mark-stale operations append complete
  revisions and child snapshots instead of updating the reviewed row;
- historical GET endpoints return the immutable revision while recomputing
  current eligibility against today's Source projection and entity heads;
- every post-create mutation carries an opaque `operation_id`, expected head
  revision, actor, reason, and canonical request hash;
- identical operation replay returns the stored result, changed reuse returns
  `409`, and concurrent different operations cannot both advance one head;
- new Relation candidates bind the exact current Concept revisions;
  acceptance additionally requires the request to name that same binding and
  both endpoints to remain active, accepted, current, and fully grounded;
- every Concept or Relation evidence item must still match its Source
  generation, Chunk hash, typed Locator, Source type, and exact quote before
  acceptance;
- every Concept head transition synchronously appends a stale revision for
  each incident current Relation in the same transaction;
- prerequisite acceptance performs the cycle check under the same serialized
  write transaction and conservatively includes stored accepted/current edges
  even when a Source deletion makes one dynamically ineligible;
- SQLite BUSY/LOCKED exhaustion maps to `503` with bounded retry guidance;
  malformed transitions map to `422`, conflicts to `409`, and unexpected
  persistence faults return a safe `500` without SQL or local paths.

### Decisions and technology

SQLite `BEGIN IMMEDIATE` is the correctness boundary, not only a performance
choice. It obtains the course workspace's single writer slot before replay,
head reads, evidence validation, cycle detection, revision inserts, incident
invalidation, head CAS, and operation receipt. This makes two racing graph
decisions serializable while WAL continues to permit readers.

The operation receipt is a bounded canonical JSON object. Its SHA-256 request
hash covers protocol version, actual route template, course, entity kind/ID,
operation kind, and the normalized Pydantic request. SQLite JSON1 triggers
validate exact receipt keys/types and their referenced revision; a separate
trigger prevents in-place receipt edits. Python reloads the historical result
and recomputes dynamic eligibility instead of caching a stale response DTO.

Relation endpoint binding belongs to the Relation revision, not its stable
identity. Stable IDs express continuity; bound revision IDs express exactly
what a reviewer saw. Rejection may preserve a stale or legacy candidate
without asserting current truth. Acceptance never silently rebinds: a legacy
candidate must first be edited/regrounded.

Unicode aliases use NFKC normalization, collapsed whitespace, and case-folding
for per-revision uniqueness while preserving display text. Aliases are not
globally unique because real course vocabulary can be ambiguous.

### Problems encountered and resolutions

- The first Relation reject path revalidated endpoint revisions as current.
  After a Concept changed, the synchronously stale candidate could therefore
  never be rejected. Rejection now verifies its stored binding but reserves
  current endpoint/evidence validation for acceptance.
- Initial receipt validation used `json_extract(...) != value`. SQLite's
  three-valued logic let missing keys produce `NULL` and bypass a trigger.
  The contract now validates JSON key types, uses fail-closed identity
  comparisons, requires exactly three keys, and has a direct `{}` regression
  test.
- Endpoint-binding rows and stable Relation endpoints were initially
  updateable. Immutable update triggers now prevent historical rebinding, and
  the read model independently reports `endpoint_binding_identity_mismatch`
  if storage is corrupted.
- The first concurrency test started two application lifespans, whose startup
  reconciliation changed shared Source state. The final test drives two
  service calls with independent SQLite connections, a start barrier, and a
  deliberately held first transaction; it proves that one request remains
  blocked until the serialized writer commits.
- A cycle test initially covered only sequential requests. The final suite
  races opposite prerequisite acceptances and requires exactly one accepted
  direction, one operation receipt, no extra revision, clean foreign keys, and
  a successful SQLite quick check.
- The first rollback assertion checked only stable heads. Fault injection now
  also proves that revisions, aliases, evidence, endpoint bindings, incident
  Relation revisions, and operation rows leave no partial records.
- The canonical request hash originally named fictional `/edit` routes. It now
  records the actual PATCH route templates so the audit protocol matches the
  public API.

### Verification

- independent adversarial review: no remaining P0/P1 findings;
- dedicated lifecycle and v11 migration suite: **18 passed**;
- graph API/store/migration/currentness compatibility selection: **42 passed**;
- full backend: **764 passed, 3 skipped**; one existing upstream Starlette
  deprecation warning;
- full frontend: **27 files, 214 tests passed**, plus ESLint and production
  TypeScript/Vite build;
- Python compilation and `git diff --check`: passed.

The three backend skips remain environment-dependent path/symlink cases. The
frontend build retains the already documented 588.23 kB main-chunk warning;
this backend slice did not increase that bundle.

### Known debt and claim boundary

The initial G1.1 Concept/Relation POST endpoints do not yet have a create
receipt. A lost Concept-create response can produce a semantic duplicate, and
a Relation retry currently receives the uniqueness conflict rather than the
first result. Before the G1/release reliability gate, create needs a separate
`client_request_id + canonical request hash + stable entity receipt` contract
because there is no prior revision to CAS. Until that lands, the project may
claim idempotent **post-create revisions**, not idempotent graph creation.

`concept_graph_store.py` has also grown beyond 2,300 lines. Its transactional
boundary is deliberate, but mutation orchestration, evidence validation, and
read-model decoration should be split behind the same store facade before or
around G1.3. Automatic incident-stale revisions are causally inferable from
the root Concept operation but do not yet carry an independent cause field;
that is an auditability enhancement, not a hidden correctness claim.

### Git checkpoint

Independent commit subject:

```text
feat(graph): add revisioned review lifecycle
```

The immutable SHA is reported after the commit is created and pushed.

### Next gate

G1.2c implements Concept merge/retirement with dual-head CAS, redirect and
dependency rules, and atomic incident invalidation. The initial-create receipt
debt must close before immutable G1.3 publication is treated as release-ready.

## G1.2c - Concept identity merge and retirement lifecycle

**Status:** Complete as a bounded G1.2 slice; initial-create reliability and G1.3 remain open

**Date:** 2026-08-09

**Branch:** `codex/concept-graph-foundation`

### User outcome

A reviewer can now normalize duplicate or out-of-scope Concepts without
overwriting history. Merging one Concept into an active survivor or retiring a
Concept creates a terminal revision, preserves the source's exact aliases and
evidence for audit, and makes every incident current Relation stale in the
same transaction. A retry after a lost response returns the original terminal
revision rather than applying the decision twice.

This slice does not silently consolidate survivor evidence, rewrite Relation
endpoints, publish an authoritative graph, or implement traversal. Those are
separate review and publication decisions.

### Delivered scope

- `POST /courses/{course_id}/concepts/{concept_id}/merge` requires the expected
  source and survivor revisions plus operation ID, actor, and reason;
- `POST /courses/{course_id}/concepts/{concept_id}/retire` requires the expected
  source revision and the same mutation metadata;
- both operations enter `BEGIN IMMEDIATE`, replay an existing receipt before
  reading mutable state, then compare-and-swap the relevant stable heads;
- merge appends a terminal `identity_status=merged` source revision with a
  same-course survivor redirect, while retirement appends
  `identity_status=retired` without a redirect;
- aliases and evidence are copied into the source's terminal revision for a
  complete historical snapshot, but never copied into or used to advance the
  survivor;
- incoming and outgoing current Relations are append-only transitioned to
  stale; non-incident and already-stale Relations are left untouched;
- many duplicate Concepts may point directly to one active survivor, but an
  identity with an incoming current redirect cannot itself merge or retire,
  which prevents redirect chains and cycles;
- terminal identities reject later edit, review, stale, merge, or retirement
  operations, while an identical previously committed operation remains
  replayable;
- schema v12 widens the immutable operation ledger for merge/retire and
  reserves create kinds for the next isolated slice;
- schema v12 also rejects in-place updates to revision-owned Concept/relation
  revisions, aliases, and evidence at the database boundary;
- the v11 ledger rebuild preserves existing receipt bytes and remains fully
  rollback-safe under the migration savepoint.

### Decisions and technology

Concept identity normalization is modeled as an append-only state transition,
not a destructive row rewrite. The stable source ID therefore remains a valid
historical address, and the redirect records identity resolution without
claiming that the survivor automatically inherits the source's reviewed
evidence. Consolidation into the survivor must be an explicit later revision
that passes the normal evidence and review contract.

The redirect invariant deliberately permits a star and forbids a chain. A
star gives every old identity one hop to an active survivor; forbidding a
survivor with incoming redirects from later merging or retiring avoids
recursive resolution, path compression, and ambiguous deletion behavior in
the first reliable implementation. SQLite's serialized writer boundary makes
opposite merges and merge-versus-retire races deterministic: at most one
transition can validate the original heads.

Revision-owned tables use `BEFORE UPDATE` abort triggers. `DELETE` is retained
for explicit aggregate cleanup and course purge, so immutability does not turn
normal workspace deletion into an impossible operation. A dedicated regression
test verifies cleanup with a live merge redirect and clean foreign keys.

Migration v12 rebuilds only `concept_graph_operations` because SQLite cannot
widen a table `CHECK` constraint in place. It copies every v11 column directly,
recreates receipt validation and immutability guards, and reserves
`concept_create` / `relation_create` kinds without claiming that initial-create
idempotency is already implemented.

### Problems encountered and resolutions

- The first dependency failure was classified as an invalid transition and
  would have returned `422`. A dedicated merge-dependency error now maps the
  concurrent/state conflict to `409`.
- The first self-merge check ran before source lookup, so a missing path ID
  equal to the submitted survivor ID returned `422`. Source lookup now wins
  and preserves the documented `404` boundary for missing entities.
- The first happy-path test covered only an outgoing Relation. The final test
  proves both endpoint directions, a non-incident edge, and an already-stale
  edge, matching the store's `source OR target` invalidation query.
- Merge rollback coverage initially stood in for retirement because both use
  the same transaction primitive. A symmetric fault-injection test now proves
  that retirement also rolls back the terminal revision, incident Relation
  revisions, stable heads, and receipt.
- A concurrency test initially treated only `409` as a legitimate loser.
  Depending on serialization order, endpoint validation can instead return a
  safe `422`; the final assertion tests the invariant that the edge is stale
  and unpublishable rather than overfitting one scheduling-dependent status.

### Verification

- independent adversarial review: no P0/P1 findings; all reported P2 items
  were closed before the checkpoint;
- focused identity/review/migration suite: **32 passed**;
- concurrency race tests repeated five times: passed;
- full backend final-tree result: **778 passed, 3 skipped**;
- full frontend: **27 files, 214 tests passed**, plus ESLint and production
  TypeScript/Vite build;
- Python compilation, `uv lock --check`, SQLite `foreign_key_check`, SQLite
  `quick_check`, and `git diff --check`: passed.

The three backend skips remain environment-dependent path/symlink cases. The
frontend build retains the already documented 588.23 kB main-chunk warning;
this backend lifecycle slice did not increase the bundle.

### Known debt and claim boundary

Initial Concept/relation POST requests still lack client-provided operation
metadata and stored create receipts. The v12 ledger accepts those kind labels
only as a schema reservation. Until G1.2d lands, the project may claim
idempotent graph **revision and identity transitions**, not idempotent graph
creation.

The graph store has grown beyond 2,500 lines. G1.3 publication should use a
separate publication module/store behind the existing graph read boundary
rather than extending this mutation file indefinitely. Incident stale
revisions remain causally inferable from the root identity operation but do not
yet store an explicit cause ID.

### Git checkpoint

Independent commit subject:

```text
feat(graph): add concept identity lifecycle
```

The immutable SHA is reported after the commit is created and pushed.

### Next gate

G1.2d makes initial Concept and Relation creation operation-ID based,
transactional, and replayable without weakening the existing request-hash
contract. Only after that reliability debt closes does G1.3 freeze immutable,
content-hashed graph versions for deterministic path consumers.

## G1.2d - Idempotent initial Concept Graph creation

**Status:** Complete as a bounded G1.2 slice; G1.3 immutable publication remains open

**Date:** 2026-08-09

**Branch:** `codex/concept-graph-foundation`

### User outcome

Initial Concept and Relation candidate creation now survives response loss,
client retries, and concurrent duplicate requests without creating accidental
duplicate aggregates. Its receipt is durable SQLite state rather than process
memory. A successful retry returns the same
stable entity and immutable revision even if its Source evidence or Relation
endpoints have since changed.

This closes the last client-visible graph-write idempotency gap. It does not
make a draft Concept authoritative, semantically deduplicate independently
submitted Concepts, or publish a graph version.

### Delivered scope

- Concept and Relation create requests now require a normalized
  `operation_id`, client-supplied `actor` label, and `reason`; they reject
  unknown top-level and nested evidence fields and do not accept an
  `expected_revision` for an entity that does not exist yet;
- `concept-graph-create-v1` hashes the exact route, course, entity type, create
  kind, and complete normalized request using canonical compact UTF-8 JSON and
  SHA-256;
- generated entity/evidence/alias IDs, timestamps, and dynamic currentness stay
  outside the request hash;
- both create stores enter `BEGIN IMMEDIATE` and look up the shared
  course-scoped operation ledger before Source, quote, endpoint, support, or
  relation-uniqueness validation;
- an identical receipt reloads its stored entity ID and revision; changed
  reuse of the same operation ID returns `409` without writing;
- identity, revision, aliases/evidence or endpoint binding, and operation
  receipt commit or roll back as one aggregate transaction;
- concurrent identical operations converge on one aggregate and receipt;
  concurrent different payloads using one operation ID produce one success
  and one conflict;
- different operation IDs may create two duplicate Concept candidates for
  later review/merge, while the canonical Relation identity constraint permits
  only one matching Relation and leaves no losing receipt;
- create, edit, review, stale, merge, and retire share one operation-ID
  namespace per course, while the same opaque ID may be reused in another
  course;
- create and revision workflows now share one `_run_graph_write` error boundary
  for `404`, `409`, `422`, retryable `503`, and safe `500` behavior;
- fixed Concept and Relation hash vectors protect the protocol from accidental
  serializer or model-default drift.

### Decisions and technology

Idempotency is keyed by client intent, not by generated ID or fuzzy semantic
equality. A server-generated request ID cannot recover from a response that
was committed but lost before the client received it. Requiring the caller to
reuse one opaque operation ID gives the database a stable name for the intent
and lets SQLite serialize competing writers before they inspect mutable
evidence.

The create receipt stores only entity type, stable entity ID, and immutable
revision. Replay reloads that historical revision and recomputes currentness
against today's Source and endpoint heads. Immediate replay therefore returns
the same response, while a later replay can truthfully report
`evidence_current=false` or `is_current_revision=false` without mutating the
original snapshot. Persisting a full cached response would make those fields
lie.

Symmetric Relation storage canonicalizes endpoints, but the idempotency hash
captures the normalized client request before domain canonicalization. Reusing
one operation ID with reversed endpoints and support roles is treated as a
changed request and returns `409`; using a new operation ID reaches the stable
Relation uniqueness rule. This preserves an exact audit record instead of
guessing that two differently expressed requests carried identical intent.

The shared v12 operation ledger was deliberately widened in G1.2c, so G1.2d
needs no schema migration or second ledger. `(course_id, operation_id)` is the
single namespace across create and revision operations, and its existing JSON
trigger verifies that every receipt refers to a real stored revision.

### Problems encountered and resolutions

- The initial model made only the top-level request strict. Pydantic model
  configuration does not recursively apply to nested evidence DTOs, so an
  unknown client locator could be silently discarded before hashing. Nested
  evidence requests now independently forbid extras, and a direct API test
  locks the boundary.
- Create and mutation hashes initially duplicated canonical JSON encoding.
  Both protocol constructors now use one `_canonical_hash` implementation,
  while retaining separate version labels and payload fields.
- Create and mutation replay initially duplicated receipt query and JSON
  consistency logic. A shared reader now validates kind, entity type, request
  hash, and result object; mutation replay additionally checks its
  client-addressed entity ID, while create replay learns the generated ID from
  the receipt.
- Concept and Relation create services initially duplicated BUSY and safe-500
  exception handling. All graph writes now use one wrapper so those paths
  cannot drift independently.
- A corrupted-receipt test first attempted invalid JSON, which SQLite's table
  constraint correctly rejected before the read-path test. The final fault
  uses valid but inconsistent JSON, restores the immutability trigger after
  controlled tampering, and separately tests a receipt pointing to a missing
  revision.
- Existing review tests counted every ledger row and began seeing the new
  create receipt. They now assert the intended review operation kind, making
  the causal expectation explicit rather than weakening the count.

### Verification

- independent read-only audit: no P0 or P1 code/security findings after the
  nested strictness issue was closed;
- focused create/review/identity/currentness/store regression: **76 passed**;
- same-operation and distinct-operation concurrency, Source/endpoint drift
  replay, cross-course namespace, create-versus-mutation reuse, Concept and
  Relation receipt-failure rollback/retry, BUSY retry, and corrupt/dangling
  receipt behavior: covered;
- full backend final-tree result: **800 passed, 3 skipped**; one existing
  Starlette `TestClient` / `httpx` deprecation warning;
- full frontend: **27 files, 214 tests passed**, plus ESLint and production
  TypeScript/Vite build;
- Python compilation, `uv lock --check`, and `git diff --check`: passed.

The three backend skips remain environment-dependent path/symlink cases. The
frontend build retains the documented 588.23 kB main-chunk warning; this
backend reliability slice did not increase that bundle.

### Known debt and claim boundary

The required operation metadata is an intentional breaking change to an
internal experimental API. No frontend call site uses these routes today;
untracked external clients must add the three fields. The supplied `actor` is
an audit label, not an authenticated principal, so it must not be described as
proof of user identity.

Workspace backup/restore already copies the complete SQLite ledger. A focused
packaged-workspace restore followed by same-operation replay remains a full-G1
release integration test. G1.2d itself proves durable database replay but does
not claim an immutable published graph, automatic Understanding, graph paths,
or a graph UI.

### Git checkpoint

Independent commit subject:

```text
feat(graph): make candidate creation idempotent
```

The immutable SHA is reported after the commit is created and pushed.

### Next gate

G1.3 moves publication into a separate bounded context. It will revalidate the
complete authoritative draft dependency set, compare-and-swap an expected
draft manifest and active version, and seal a content-hashed self-contained
snapshot without expanding the already large draft mutation store.

## G1.3 - Immutable Concept Graph publication

**Status:** Complete as the bounded G1 authority slice; G0.2/G2 evaluation
protocol work is next

### Problem and authority decision

An accepted draft revision is still mutable authoring state. Paths cannot use
it safely because a later Concept edit, merge, Source reprojection, missing
review receipt, or concurrent publisher could change its meaning between
validation and traversal. G1.3 therefore introduces an explicit publication
boundary: only the active sealed `GraphVersion` is authoritative.

The selected Concept set is exactly the current
`active + accepted + validity=current` heads. Merged, retired, rejected,
candidate, stale, and tombstoned heads are historical exclusions rather than
publication blockers. In contrast, every current `accepted + current`
Relation is in the validation dependency set. An invalid accepted Relation
blocks the whole graph; it is never silently filtered to make publication
succeed.

### Implementation and system design

- migration v13 adds normalized, self-contained version, head, Concept, alias,
  Concept evidence, Relation, Relation evidence, and publication-operation
  tables;
- deferred child foreign keys permit children-before-seal insertion, while the
  seal trigger verifies parent/head state, complete counts, per-owner evidence,
  and exact Relation endpoint revisions;
- update, late-insert, and direct-delete guards make sealed rows immutable;
  conditional delete guards still permit the one permanent course-purge
  cascade;
- the preview hashes the complete authoritative draft dependency set and
  streams blocking issue identity into a bounded digest/sample;
- `concept-graph-content-v1` identifies one exact immutable materialization,
  including runtime identities, evidence, endpoint bindings, model provenance,
  and immutable review receipt provenance;
- `concept-graph-draft-manifest-v1` additionally commits endpoint-head and
  validation observations, while intentionally ignoring non-authoritative
  candidate/rejected/stale rows;
- `concept-graph-publication-request-v1` binds course, route, strict request,
  actor label, reason, and both CAS expectations to a durable operation ID;
- `concept-graph-concept-aggregate-v1` and
  `concept-graph-relation-aggregate-v1` commit every published parent field and
  its canonically ordered aliases/evidence. They remain derived values outside
  the unchanged full `concept-graph-content-v1` payload;
- `BEGIN IMMEDIATE` serializes replay, active-head CAS, draft rebuild, Source
  authority checks, snapshot insertion, seal, head advance, and receipt;
- historical metadata and child pages read only sealed data; dynamic Source
  authority is recomputed in bulk, and the current endpoint fails with a
  structured conflict when generation/type/hash/Locator/quote/root checks no
  longer match;
- every multi-query read opens an explicit SQLite read transaction, and G3 is
  required to load authority plus adjacency in the same transaction to avoid a
  new TOCTOU window.

The API surface is isolated in publication model/store/service/router modules
instead of adding more branches to the already large mutable graph store. The
first correctness slice nevertheless leaves a 2.6k-line internal publication
store. G1.3b must split draft validation/hash construction, sealed snapshot
codec/read-write logic, and the shared Source-authority evaluator before G3,
preserving golden hashes and moving rather than duplicating predicates.

### Defects found during adversarial implementation

1. A valid Chunk longer than 65,536 characters was initially publishable but
   immediately reported stale by the historical authority evaluator. Preview
   and published-authority bounds now use the same fail-closed policy, so the
   version cannot be stale at the moment it is sealed.
2. Historical version lists initially loaded every evidence row in Python and
   rejected multiple valid large versions using a one-version limit. Authority
   now uses one bounded-result SQL/window scan with cached live Chunk hashing;
   it returns exact per-version stale counts and only the first 100 details.
3. Child pagination initially rehashed the complete graph for every page, then
   a count-only optimization could not detect same-count external corruption.
   Metadata/current/replay retain full canonical content verification; child
   pages verify seal/count plus the domain-separated aggregate hash of every
   returned Concept or Relation. Parent, alias, and evidence tampering therefore
   fails closed without loading unrelated graph rows.
4. The first 64 MiB estimate counted Unicode characters and omitted structural
   payload. Preflight now uses UTF-8 BLOB lengths plus conservative framing,
   and final canonical content/manifest bytes are checked directly.
5. Blocking issues were initially materialized without a bound. The final
   design keeps an exact count, a bounded user-visible sample, and a streaming
   length-prefixed SHA-256 commitment over every stable issue coordinate.
6. Draft and historical Source-currentness queries initially risked returning
   long live Chunk text/Locator values to Python. One 15-value SQLite predicate
   now returns only a boolean and shares a bounded exact-evidence LRU cache; the
   version-list path remains bounded while repeated observations reuse work.

### Verification and honest claim boundary

Dedicated tests cover strict request/hash behavior; first and second versions;
replay after loss; concurrent same/different publishers; publish-versus-edit
and publish-versus-Source-update serialization; rollback and same-operation
recovery at every write stage; Source drift; missing review receipts; invalid
accepted Relations; terminal-head exclusion; pagination and course isolation;
UTF-8/live-observation bounds and cache reuse; database mutation guards;
aggregate-only and same-count parent/child external corruption;
soft-delete/restore; migration backup and rollback; permanent course purge;
and inspection of a real workspace-backup archive containing the full sealed
graph ledger.

The implemented claim is now: reviewed, evidence-grounded draft graphs can be
published as immutable, idempotent, course-scoped authoritative versions, and
current authority fails closed after Source drift. G1.3 does **not** claim that
Concepts are automatically generated accurately, that a human golden graph
exists, that paths are implemented, or that graph use improves learning.

### Git checkpoint

Independent commit subject:

```text
feat(graph): publish immutable graph versions
```

The immutable SHA and remote CI result are recorded after commit and push.

### Next gate

Close G0.2 before automatic Understanding: freeze the strict, redacted golden
fixture protocol and CS336 Lecture 3 Source slice; then generate blinded
annotation packets and materialize a human-reviewed G2 graph through the G1
service/publication boundary. Lecture 3 is authoring data, not a held-out model
accuracy claim.

## G0.2a - Executable golden-graph protocol boundary

**Status:** Complete as strict protocol infrastructure; the real CS336 Source
slice and all human gold remain deliberately unfrozen

### Problem and authority decision

The acquisition manifest proves which upstream bytes are registered. A prose
annotation guide explains intended human behavior. Neither one proves that a
particular PDF page set, parser result, Chunk universe, metric, or claim is the
same input another run used. Conversely, a SHA-256 over an arbitrary file proves
only byte identity; it does not prove that the file is a valid Source catalog.

G0.2a therefore makes the boundary executable and separates four downstream
authorities that must not be collapsed:

1. the protocol definition plus Source-slice freeze registers ontology,
   procedure, metrics, thresholds, claim scope, one exact page partition,
   parser/chunker lineage, dependency environment, and redacted semantic
   Source/Chunk artifacts;
2. the later partition-bound `GoldBundleSeal` owns closed-world Concepts, the
   complete pair universe, delayed Pass A/B decisions, adjudication, and the
   frozen alias table for one exact partition;
3. the future automatic-proposal/Chat run family owns a pre-annotation
   `RunSpecSeal` and later sealed `PredictionBundle`/`ResultBundle` artifacts
   that reference that exact run spec;
4. the future append-only evaluation-access ledger owns sealed-opening and
   reproduction event history.

The acquisition `ManifestAuthority` remains an upstream byte/rights
prerequisite, not a fifth downstream authority. The required future
sealed-transfer lifecycle is:

```text
RunSpecSeal
-> source_annotation_open
-> transfer-specific GoldBundleSeal
-> sealed PredictionBundle / ResultBundle referencing RunSpecSeal
-> prediction_evaluation_open
-> explicitly labeled reproduction
```

`GoldBundleSeal` is the immutable artifact, while `gold_sealed` is the future
ledger event recording it. Lecture 3 authoring gold cannot be reused as the
gold authority for a sealed-transfer partition. The run-family and access-ledger
implementations are not part of G0.2a; future code must enforce this ordering.

`protocol_status=frozen` is therefore data rather than authority. A consumer
must hold a receipt returned after the persisted canonical artifact and every
bound leaf have been re-read and validated.

### Implementation and system design

- strict, frozen Pydantic models reject unknown fields and type coercion;
- canonical JSON uses sorted compact UTF-8 bytes, forbids duplicate keys and
  non-finite numbers, and requires an exact filename-bound SHA-256 sidecar;
- acquisition authority is reloaded from its repository file through the
  existing fail-closed manifest parser and cross-checks path, manifest hash,
  corpus, upstream commit, asset, partition, registered raw-byte hash, license,
  attribution, and redistribution policy;
- only `authoring` assets can enter this graph-authoring protocol;
- the page scope must classify every physical one-based PDF page exactly once,
  with a non-empty included set and explicit inclusion/exclusion reasons;
- dependency, parser/chunker configuration, semantic Source catalog, and
  semantic Chunk manifest use
  exact-key allowlist schemas rather than accepting an opaque file with the
  right hash;
- public Source rows contain only stable logical page IDs, physical page
  numbers, parse status, text hash, and UTF-8 byte length. Runtime UUIDs,
  database IDs, timestamps, PDF bytes, screenshots, page text, and exact quotes
  remain outside public artifacts;
- non-included Source pages carry an enumerated reason and no semantic bytes;
  Chunk locators use exact UTF-8 byte offsets, bind the Source catalog and tool
  identities, stay inside included page lengths, and their union covers every
  byte of every included page. Sliding overlap is explicitly allowed, while an
  exact locator occurrence cannot claim two different Chunk identities;
- parser/chunker code/config hashes, the exact parsed `uv.lock`, a dependency
  snapshot, and the installed distribution version are cross-checked at freeze
  and reload;
- persisted freeze derives one canonical path from `protocol_id`; it stages
  complete `fsync`ed bytes under a same-directory temporary name, atomically
  exposes them with a no-replace hard link, repairs only byte-identical
  JSON/sidecar crash remnants, and reloads the acquisition manifest,
  annotation guide, and all Source-slice leaves before returning authority;
- the review contract hashes the exact annotation guide and requires a later
  human attestation at the `GoldBundleSeal`, delayed blind Pass A/B, at least
  72 hours, adjudication, and temporal intra-rater terminology. Automation may create
  packets and validate them but may not impersonate the human reviewer;
- every reported metric has an exact unit, evidence scope, future authority,
  and interval policy; every gated target additionally fixes comparison and
  threshold. Relation proposal recall is report-only rather than silently
  lacking a contract. Lecture 3 is `confirmatory=false`; G0.2a only registers
  the diagnostic claim limit for too few lecture clusters, and the future
  run-bundle runner must enforce actual sample/cluster eligibility;
- Concept matching semantics are frozen now, while the actual alias-table hash
  belongs to the future gold-bundle seal. The 1k/10k latency targets require a
  separate seeded synthetic-graph performance authority owned by G3 rather
  than borrowing scientific authority from one lecture or blocking the G0.2
  Source-slice freeze on a future path implementation.

The semantic catalog identity is intentionally independent of product UUIDs.
The later private materializer will bind each logical page/Chunk to concrete
Source revisions and projection generations without changing the public
semantic hash.

### Defects found during adversarial design

1. The first design allowed an in-memory model to change from `draft` to
   `frozen`. It now derives one canonical path from the protocol identity,
   persists canonical bytes without replacement, and reloads them before
   issuing authority.
2. The first leaf check accepted any file whose raw SHA matched. Strict
   dependency/Source/Chunk schemas and lineage cross-checks now
   prevent a garbage JSON file from satisfying freeze.
3. The existing product parser and projections use runtime UUIDs. Public
   evaluation identity now uses logical page IDs and semantic hashes; runtime
   materialization is a separate later binding.
4. A lecture-cluster bootstrap over one authoring lecture or two sealed
   lectures can look quantitative without supporting a confirmatory interval.
   The protocol now records a minimum cluster count and the diagnostic-only
   claim contract below it; the future run-bundle runner must enforce the
   actual eligibility check.
5. Alias matching and 1k/10k latency initially risked becoming mutable hidden
   evaluation choices. Matching edge semantics are registered before
   prediction, gold must later seal the alias table, and latency is scoped to a
   separate reproducible synthetic protocol.
6. Dependency snapshots initially allowed the same case-folded package name
   with two versions, while a dictionary lookup silently chose one. Package
   names are now unique independent of case, and installed versions are
   cross-checked.
7. Repository-relative POSIX syntax alone allowed Windows NTFS alternate data
   streams and reserved/trailing names. Protocol paths now use a portable
   cross-platform subset, and public leaves/configs cannot live under the
   gitignored data root.
8. Requiring every Chunk content hash to be unique rejected legitimate repeated
   headers or content. Occurrence identity now includes locators: repeated
   content at distinct locations is valid, while an exact duplicate occurrence
   is rejected.
9. A direct write to the canonical name could leave partial immutable bytes if
   the process died mid-write; a crash between JSON and sidecar could also
   leave different orphan states. Publication now stages and `fsync`s complete
   bytes before an atomic no-replace hard link exposes each canonical name.
   Later identical calls repair an exact one-file remnant, concurrent identical
   publishers converge, and conflicting identities fail closed without
   overwriting the winner. Hard-link-unsupported filesystems fail safely; a
   hard kill may leave only an ignored staging name.
10. The first receipt trusted an already-created in-memory manifest object and
    a prefilled human-attestation statement. Freeze now reloads the manifest and
    hashed annotation guide from disk; the protocol records that a real human
    attestation is required later at the G2 `GoldBundleSeal`.
11. Relation recall appeared in the report list without a unit, authority, or
    interval rule, and canonical readers allocated files before checking their
    size. Recall now has a complete report-only contract, and protocol,
    sidecar, binding, manifest, and recovery reads are bounded regular-file
    operations.
12. The first page/Chunk contract accepted a page as “covered” when one small
    locator touched it, and non-included pages lacked a bounded reason. That
    permitted silent within-page cherry-picking. The v1 contract now records
    exact status/reason semantics and requires the union of locator intervals
    to cover `[0, semantic_utf8_bytes)` without gaps or tail omission; overlap
    remains explicit because sliding-window chunkers need it.
13. Pydantic's shallow `frozen` option still allowed nested collection
    mutation after an authority receipt was issued. All protocol/binding
    collections now materialize as immutable tuples while canonical JSON keeps
    array encoding, so in-memory authority cannot drift away from its hash.
14. A lock-file hash and installed package check did not prove that the lock
    actually contained the declared parser version, and opaque config JSON
    could hide mutable choices or Source text. Freeze now parses the lock and
    validates exact-key, redacted parser/chunker configuration envelopes.
15. A public-prefix string alone did not exclude symlink/reparse/hardlink
    aliases into private repository data. Bound repository leaves are checked
    component by component, must be single-link regular files, and—inside a Git
    worktree—must already be tracked in the index.
16. `model_copy(update=...)` can bypass Pydantic validation. Publication now
    rebuilds the complete protocol through the strict schema before deriving a
    canonical path or writing either leaf, so invalid in-memory data cannot
    reserve an immutable identity.
17. Git pathspec metacharacters, inherited `GIT_*` repository redirection, and
    case-folded Windows paths could make an untracked or non-portable spelling
    look authoritative. Tracked checks now use literal pathspecs, a sanitized
    subprocess environment, and exact component spelling.
18. A descriptor reader that checked only inode and size could accept bytes
    mixed across a same-size concurrent rewrite. Reads now compare descriptor
    and pathname metadata before and after the bounded read, including link
    count, size, modification/change timestamps, file type, and reparse state.
19. Windows path equality also accepted a differently cased frozen filename,
    while TOML's `true == 1` behavior could impersonate uv lock schema version
    1. Filename comparison is now exact and lock version requires an actual
    integer.
20. Early concurrent recovery could inspect the publisher's legitimate
    temporary hard-link window, and the first public loader cleaned staging-like
    names as a read side effect. Public reload now only performs a bounded wait;
    one internal publish-or-converge primitive handles both missing JSON and
    missing sidecar races and limits cleanup to exact same-inode staging names.
21. With `core.autocrlf=true`, a fresh Windows checkout could change a hashed
    guide, lock, manifest, canonical JSON, or sidecar. Repository
    `.gitattributes` now fixes text authority bytes to LF across platforms.

### Verification and honest claim boundary

| Gate | Result |
| --- | --- |
| G0.2a focused protocol suite | `76 passed, 2 skipped`; both skips require unavailable Windows directory-symlink privilege |
| Acquisition + protocol integration | `97 passed, 4 skipped` |
| Full backend regression | `915 passed, 5 skipped`; one upstream Starlette/httpx deprecation warning retained |
| Frontend regression | 27 files / `214 passed`; ESLint and TypeScript/Vite production build passed; the already-recorded 588.23 kB main-chunk advisory remains |
| Desktop host | Rust fmt/check passed; `6 passed` |
| Static/reproducibility checks | Python compileall, `uv lock --check`, `git diff --check`, and LF authority attributes passed |
| Adversarial concurrency | 288 fresh, JSON-only, and sidecar-only concurrent publications converged to one digest; independent red-team review found no remaining P0/P1 |
| Checked-in draft identity | guide `3aca0f16eb6c26b67bbb31dc36f9ded283f4f6d70ccf0bdcbb8945cd8f6970c9`; canonical draft `60448d44efaea9d109b847315e8b77cc39a6219feecffa2701c9ff58ca8c200e`; still draft/no accuracy claim |

Focused tests cover the checked-in incomplete CS336 draft, a complete synthetic
binding freeze/reload path backed by a real manifest file, exact acquisition
identity, partition isolation, page
coverage, strict binding schemas and lineage, code/config/dependency drift,
metric/claim semantics, human-review declarations, canonical JSON, sidecars,
idempotent crash recovery, atomic publish failure, concurrent identical and
conflicting publishers, immutable identity conflicts, resource bounds,
portable paths, and malformed/forbidden public data. Full repository
verification and remote CI are recorded at the Git checkpoint.

This stage makes a reproducible and leakage-aware protocol possible. It does
**not** choose the CS336 Lecture 3 pages, parse or redistribute its PDF, create
human Concepts/Relations, seal a gold graph, open sealed data, run a model, or
report accuracy. The checked-in Lecture 3 protocol leaves all unknowable
Source-slice fields empty and must fail freeze until the maintainer-owned page
decision and bounded catalog builder produce real artifacts.

### Git checkpoint

Independent commit subject:

```text
feat(eval): define golden graph fixture protocol
```

The immutable SHA and remote CI result are recorded after commit and push.

### Next gate

G0.2b implements the bounded PDF-to-semantic-Source catalog builder and private
materialization boundary. The maintainer then selects the Lecture 3 page scope;
the system records that decision without guessing it. Only after that Source
slice freezes may G2 generate empty blinded annotation packets.

## G0.2b - Deterministic redacted Source-slice derivation

**Status:** Complete; builder, independent whole-deck replay, redacted public
leaves, private materialization reload, and protocol freeze verified

### Architecture and ownership decision

The draft protocol, not a CLI flag or a model, is the sole Source-slice build
specification. For CS336 Lecture 3 v1 it now registers all 68 physical pages.
The deterministic parser already classified every page as successfully parsed
and non-blank, so this whole-deck rule avoids topical cherry-picking while
keeping human Concept/Relation judgments separate for G2.

```text
tracked ManifestAuthority + registered draft protocol
-> exact read-only authoring asset verification
-> clean project Git revision
-> isolated parser from captured verified bytes
-> normalized private page inventory
-> page-local UTF-8 Chunker from captured verified bytes
-> redacted catalog + Chunk manifest + derivation summary
-> production-compatible CourseSource/CourseSourceChunk projection
-> ignored, reloadable private materialization
```

The implementation uses Python 3.11, Pydantic strict/frozen DTOs, `pypdf`
6.14.2, Unicode 14.0.0 NFKC normalization, canonical JSON/SHA-256 sidecars,
subprocess isolation with a whole-worker timeout, UTF-8 byte locators, Git
revision/object verification, and the production Source ID/projection-manifest
functions. Public artifacts contain identities, statuses, hashes, and locators
only; licensed Source text remains under ignored `backend/data/`.

### Red-team defects closed before publication

1. Catalog and Chunk leaves were structurally self-consistent but lacked an
   orchestration receipt. `ProjectionIdentity` now binds a strict build-summary
   path/hash; freeze cross-checks acquisition, tools/configs/dependencies,
   catalog, Chunks, counts, and a reachable clean project commit.
2. The first evaluation Source ID and projection hash did not satisfy the
   product store. The adapter now uses `source_id_for_asset` and the production
   projection-manifest algorithm, while keeping the golden Chunk-manifest hash
   as separate evaluation metadata.
3. Runtime locators originally lost exact UTF-8 offsets after persistence.
   `PdfPageLocator.metadata` now carries logical page, half-open offsets, unit,
   and both golden catalog/manifest bindings, all revalidated before authority
   issuance and after private reload.
4. Private text could appear in Pydantic errors or object representations.
   Private models hide validation input, Source-bearing fields have redacted
   reprs, and boundary errors replace private validation details.
5. A verify-then-reopen parser race and stale imported Chunker could execute
   bytes different from the recorded hashes. The parser runs from an exclusive
   private snapshot of captured bytes; the Chunker is dynamically loaded from
   the same kind of verified byte receipt.
6. A process-local-only authority could not resume G2. A strict, bounded,
   atomic/convergent, no-overwrite private envelope now round-trips the canonical
   Source projection under a gitignored path without entering CLI receipts or
   the product database.
7. The first private loader trusted an internally self-consistent envelope and
   sidecar but had no external authority. The envelope now binds protocol ID
   and normalized build-spec hash; writer/loader require the exact protocol,
   deterministic filename, scope/tool/acquisition bindings, three public leaf
   identities, and Git-ignored/untracked storage.
8. A public-write flag initially retained licensed private text as an implicit
   side effect, and a clean check did not cover stale imported orchestration
   dependencies. Public/private writes are now independent default-off flags;
   builder and command capture/recheck one shared v1 source closure, while
   freeze compares every closure leaf to the recorded Git commit and fails
   closed outside a Git worktree.
9. Historical protocol loading originally reused the publication gate. Any
   later builder refactor, Python/pypdf upgrade, `uv.lock` change, or guide edit
   could therefore make an already frozen experiment unreadable. Historical
   `FrozenProtocolAuthority` now validates exact bounded blobs from the
   recorded commit without consulting current implementation/runtime bytes;
   the stronger `ReplayReadyFrozenProtocolAuthority` separately requires the
   current tracked closure, runtime, clean worktree, and exact private PDF.
   Freeze and crash recovery repeat the current-environment gate after durable
   publication. A C1-build/C2-publish/C3-code-evolution regression proves the
   old authority remains loadable while replay readiness fails closed.

### Verification and honest boundary

- protocol, builder, primitives, and command focused suite:
  `122 passed, 2 skipped`;
- full backend regression: `973 passed, 6 skipped`; one existing
  Starlette/httpx deprecation warning remains;
- exact-tool snapshot, dirty-revision, product compatibility, locator,
  redaction, private round-trip/tamper/path, triple-hash, and no-leakage cases
  are automated;
- compile/import, line-length, and `git diff --check` gates pass;
- the registered public PDF was previously dry-run as 68 pages / 68 Chunks,
  but that observation is not yet a frozen public benchmark result;
- no human gold, Concept/Relation accuracy, grounded-Chat accuracy, path
  quality, held-out generalization, or educational-effectiveness claim exists.

The derivation implementation was committed as `cd06516` and its GitHub
Change-level CI run `31287458081` passed Backend, Frontend, and Desktop/Rust.
The exact registered PDF was then built from that clean commit and replayed
without writes in a temporary detached worktree at the same commit. Both runs
produced 68 included pages, 68 Chunks, no blank/failed pages, and identical
catalog (`18c49f...8b50`), Chunk (`6e238c...6b09`), and summary
(`ae2876...33ff`) hashes. The temporary worktree and copied private input were
removed after comparison.

The bound draft (`f0816271...41a4`) then issued frozen protocol
`e09c9128...8174f`. Historical and strict loaders agreed, and the ignored
private materialization reloaded against the frozen protocol with the same
68-page/68-Chunk inventory. No PDF or Source text entered Git. This closes the
G0.2 evaluation-input freeze; it does not create human Concepts/Relations or
authorize any accuracy, path-quality, held-out, or educational-effect claim.

## G2.1 - Staged human Concept-inventory handoff

**Status:** Tooling implemented and locally revalidated; real reviewer-key
registration and human annotation not started

### Outcome and non-outcome

This checkpoint turns the frozen G0.2 Source slice into a human-owned Concept
annotation workflow without asking a model to generate or approve gold. It
implements strict private worksheet authoring, redacted Concept/alias
preparation, detached OpenSSH approval, immutable Concept-stage publication,
complete pair enumeration, and deep reload.

It deliberately stops before human authority. No real CS336 reviewer-key
policy has been committed, no policy-authorized worksheet exists, and there
are zero human labels. Consequently no `ConceptInventorySeal`,
`GoldBundleSeal`, Concept/Relation accuracy, agreement, graph-quality, or path
result exists.

### Artifact and trust design

```text
frozen protocol + private Source materialization
+ reviewer-key policy from a prior reachable Git commit
-> ignored policy-bound mutable worksheet
-> ignored redacted inventory / alias / seal-request candidates
-> external signature with the registered key
-> six immutable public Concept-stage leaves
-> complete pair manifest for later delayed Relation review
```

The public leaves are the Concept inventory, alias table, seal request,
detached attestation, Concept-only seal, and pair manifest. The seal carries an
explicit not-a-gold-bundle status. Exact Source quotes remain in ignored local
authoring data; public evidence contains logical identifiers, UTF-8 byte
spans, and hashes only.

The reviewer policy is a separate public trust root. Its canonical JSON and
sidecar must already exist as exact blobs at a named full Git commit that is an
ancestor of current HEAD. It binds the frozen protocol, reviewer ID, allowed
SSHSIG namespaces, one canonical Ed25519 allowed-signers line, its hash, and
the key fingerprint. Worksheet and Concept-stage artifacts then bind the
policy hash and registration commit.

New authoring additionally requires the same policy and sidecar at current
`HEAD`, the index, and the working tree. Committed removal revokes new use.
Historical verification reconstructs the prior bytes from the registration
commit, preserving auditability of old seals without reactivating the key.

This establishes that a repository-registered key approved exact bytes. It
does not authenticate reviewer humanity, real-world identity, prediction
blindness, timestamp truth, or semantic correctness. The single-maintainer
workflow is self-attested.

### Main implementation choices

- Python 3.11 and strict/frozen Pydantic DTOs keep worksheet, inventory, alias,
  attestation, seal, and pair shapes explicit and immutable after validation.
- Canonical UTF-8 JSON, SHA-256 sidecars, bounded stable-file reads, and atomic
  no-replace publication make identities replayable and conflicts fail closed.
- Mutable Source-bearing authoring stays below the verified Git-ignored data
  boundary; public output receives a recursive privacy scan before any leaf is
  exposed.
- Preparation resolves private exact quotes against the frozen private Source
  bytes. Repeated text requires an explicit page-global byte start.
- Sealing re-derives all prepared artifacts from the current worksheet before
  accepting a signature. Public publication preflights the whole leaf set,
  then reloads the full DAG after publication.
- OpenSSH verification uses fixed trusted system executable locations and a
  minimal subprocess environment; `PATH` and loader/Git/SSH injection do not
  select the production verifier.
- The complete unordered pair universe is derived deterministically from the
  fixed Concept keys before Relation decisions, preventing positive-pair
  cherry-picking.

### Adversarial findings that changed the design

1. The initial `allowed_signers` input was self-authorizing: a caller could
   generate a fresh key, supply its matching policy, and obtain a valid seal.
   G2.1 now requires a separately committed `ReviewerKeyPolicyAuthority` and
   matches signer, namespace, policy hash, and fingerprint during both sealing
   and reload.
2. A bare `ssh-keygen` lookup plus inherited environment allowed executable or
   dynamic-loader substitution. Production verification now selects a trusted
   absolute system path and sends a minimal allowlisted environment; explicit
   binary injection remains test-only and requires an absolute path.
3. Checking only the final private file did not prove every parent remained
   under the intended ignored/untracked boundary. Private artifact operations
   now validate lexical/resolved containment, repository ownership, link and
   file type, and Git ignore/tracking status at the boundary.
4. Publishing six leaves sequentially could discover an immutable conflict
   after earlier leaves had appeared. A complete preflight now validates every
   destination and public payload before the first publication; strict reload
   still owns convergence/recovery verification.
5. A shallow forbidden-key/string check could miss nested or fragmented
   Source copies. The public scanner now traverses the complete payload and
   rejects sensitive keys, local/private paths, long Source substrings, and
   token-window copies before publication.
6. Fields such as `proposal_origin=human` and a timestamp could be read as
   software-authenticated facts. Artifact naming and receipts now preserve
   them as reviewer declarations and explicitly retain
   `software_authenticated_reviewer_identity=false` and
   `software_authenticated_prediction_blindness=false`.
7. Raw CLI exception text could echo a private path or quote. The command
   boundary now maps failures to bounded error classes while detailed private
   validation remains inside tests/local debugging boundaries.
8. The old direct `O_EXCL` target write exposed an in-progress, potentially
   zero-byte target to concurrent readers. Publication now writes and syncs a
   complete same-directory staging file, then performs an atomic no-replace
   install. A bounded 0.5-second reconciliation applies only to the remaining
   identical-install race; a persistent unsafe or conflicting leaf still fails
   closed. The original concurrency regression passes again.
9. Treating any reachable historical key as active meant a committed policy
   removal did not revoke new signing. The workflow now separates active
   current-state authority from historical verification capability.
10. Reviewer-policy history checks still invoked bare `git` in an inherited
    executable environment. Git plumbing now uses a trusted absolute machine
    path and a minimal allowlisted environment, matching the SSH boundary.
11. Public-value privacy checks were tightened for default-ignorable Unicode,
    nested percent-encoding, POSIX system paths, and Concept keys, closing
    alternate spellings that could conceal a local path or copied Source text.
12. Decoding only two percent-encoding layers left reversible Source text and
    deeper encoded paths outside the copy scanner. Public Concept prose has no
    product requirement for URL escapes, so the boundary now rejects any
    `%HH` escape while preserving ordinary percentages such as `100%`.

### Verification and honest claim boundary

The first pre-hardening focused implementation run reported `25 passed, 1
skipped`. That number is historical, not the acceptance result: reviewer-policy
and security changes expanded the focused result to `55 passed, 1 skipped`
after the publication-race and percent-escape fixes. The complete local backend
regression reports `1028 passed, 7 skipped, 1 warning`; remote change-level CI
remains the push gate. Isolated follow-up evidence includes `12 passed` for the
reviewer-policy authority and `11 passed, 1 skipped` for the hardened OpenSSH
boundary; the skip remains environment-capability dependent. The final focused
and full-regression results are part of this Git checkpoint.

The maintained workflow contract and user-owned exercise are documented in
[Golden Graph Human Annotation Workflow](modules/golden-graph-human-annotation-workflow.md),
[ADR-0010](decisions/ADR-0010-staged-human-gold-and-key-control-attestation.md),
and the [G2 learning handoff](learning/g2-human-annotation-handoff.md). The
hash-bound [Graph Annotation Protocol](graph-annotation-protocol.md) was not
edited; a semantic change requires a new protocol identity.

## G2.2 - Shared annotation evidence and attestation primitives

**Status:** Software foundation implemented and locally accepted; Relation
Pass A and all human semantic work remain unstarted

### Outcome and non-outcome

This checkpoint extracts the security-sensitive operations that Concept,
Relation Pass A, Relation Pass B, and final GoldBundle sealing must share:

```text
loader-issued private Source receipt
-> strict annotation snapshot binder
-> exact-quote resolution / public span replay / aggregate privacy scan

typed stage request + current repository reviewer policy + detached SSHSIG
-> shared four-namespace verifier
-> portable Artifact / Reference + key-control-only capability

persisted request + historical reviewer policy + embedded Artifact
-> deep historical signature replay
```

G2.1 now consumes those primitives through thin Concept-specific adapters.
Future Relation stages have one audited owner for evidence, privacy, Git policy
currentness, and detached signature semantics rather than copying Concept code.

This is not Relation Pass A. No real reviewer-key policy has been registered,
no authorized worksheet exists, and there are still zero human Concept or
Relation labels. No `ConceptInventorySeal`, `GoldBundleSeal`, accuracy,
agreement, graph-quality, or path-quality result exists.

### Main implementation and technology choices

- Python 3.11 dataclass capabilities and strict/frozen Pydantic DTOs separate
  private in-memory authority from canonical public artifacts.
- Canonical UTF-8 JSON and SHA-256 bind the complete private materialization
  snapshot before annotation. The binder strictly reparses nested product
  models and exposes only frozen Chunk windows plus private privacy surfaces.
- Process-local keyed HMAC tags bind every Chunk's materialization digest,
  ordinal, locator, text bytes, and semantic hash. A keyed root binds the exact
  Chunk count and ordered tag sequence, so equal-length mutation and coordinated
  tail truncation fail closed.
- Contiguous same-page Chunk windows are reconstructed while declared overlap
  is removed. Public character and token scans therefore catch copies split
  across fields or Source Chunk boundaries without inventing continuity across
  real locator gaps.
- Public privacy validation covers Windows/UNC/POSIX/container paths, email-like
  values, file URIs, Unicode controls/default ignorables, NFKC disguises, and
  nested or cross-field percent encoding. A normal `100%` remains valid.
- One shared OpenSSH boundary supports typed Concept, Relation Pass A, Relation
  Pass B, and GoldBundle namespaces. Existing Concept request/seal schemas
  remain Concept-only, and old Concept-only policies remain usable for
  historical Concept replay.
- New authoring reloads the reviewer policy against current Git
  `HEAD`/index/worktree before cryptographic work and again before capability
  issuance. Historical replay deliberately does not reactivate a revoked key.
- Errors crossing the evidence, signature, and Concept adapters are bounded
  static messages; private quotes, Source text, local paths, and caller-owned
  values are not interpolated or retained through exception causes.

### Findings that changed the design

1. The first extraction enforced only the policy authority's cached
   `active_at_verified_head` bit. A later Git removal could leave a stale
   capability authorizing new work. Authoring now replays the repository policy
   at both sides of signature verification.
2. The first shared evidence API accepted a duck-typed materialization and the
   privacy API accepted caller-supplied Source strings. Both allowed a future
   stage to choose its own trust input. The only public entry is now a strict
   binder over the loader-issued receipt; privacy derives Source surfaces from
   that capability.
3. Per-field privacy scans allowed a path, percent escape, or long Source copy
   to be split across public fields. Semantic and compact aggregate surfaces,
   bounded decoding, and path/source rescans close those representations while
   preserving ordinary percentages.
4. Scanning private Chunks independently missed a twelve-token copy spanning
   two contiguous windows. Same-page locator reconstruction now forms exact
   continuous Source segments and validates overlap equality.
5. A frozen dataclass alone did not stop `object.__setattr__` from replacing
   equal-length bytes or coordinating text/bytes/hash changes. Per-Chunk keyed
   integrity tags now bind the snapshot consumed by resolution and replay.
6. Per-Chunk tags alone still allowed an attacker to truncate `chunks`, tags,
   and privacy surfaces consistently. The authority-level keyed root binds the
   exact count and ordered tag sequence.
7. The first deep-revalidation helper was placed in
   `source_slice_builder.py`. The real frozen-CS336 protocol regression correctly
   failed because that file belongs to the G0.2 hash-bound derivation closure at
   `cd06516`. Moving the consumer check into `annotation_evidence.py` preserved
   byte-for-byte replay readiness and clarified ownership: frozen production
   belongs to G0.2; stricter downstream consumption belongs to G2.
8. Structural selection getters and lower signature errors could preserve
   private caller text or paths in exception chains. Trust-boundary adapters now
   suppress those causes and emit static classifications.

### Compatibility and operational limits

- Existing Concept Artifact, Reference, seal, pair-manifest, CLI receipt field
  sets, canonical challenge vectors, and the graph annotation protocol are
  unchanged.
- New reviewer policies contain four sorted namespaces and therefore have a new
  policy hash by design. No historical policy or artifact is rewritten.
- Privacy semantics are intentionally stricter. No real CS336 Concept artifact
  exists yet, so this checkpoint has no data migration; after public artifacts
  exist, future privacy-semantics changes require an explicit version boundary.
- The current binder canonicalizes and reparses a materialization with a 512 MB
  hard ceiling, and privacy scanning is not yet indexed. Current CS336 scope is
  comfortably below that limit. Whole-course scale will require streaming
  replay and cached normalized character/token indexes before claiming the
  ceiling as an operational target.
- The keyed tags are process-local capability integrity, not persisted
  signatures and not a sandbox against arbitrary code already executing inside
  the Python process.

### Verification and honest claim boundary

- five focused suites: `117 passed` in 201.52 seconds;
- complete backend regression: `1094 passed, 7 skipped, 1 warning` in 576.58
  seconds;
- the warning remains the existing Starlette `httpx` deprecation warning;
- focused coverage includes real Ed25519 signing for all four namespaces,
  stale-policy revocation, historical Concept-only replay, canonical
  compatibility vectors, forged/changed/truncated Source capabilities,
  cross-field and cross-Chunk leakage, UTF-8 evidence replay, common local path
  families, and traceback redaction;
- the frozen CS336 protocol test passes with the G0.2 derivation module
  unchanged;
- independent post-implementation review reports no remaining P0/P1 in this
  checkpoint; remote change-level CI remains the push gate.

Passing these gates establishes a reusable software trust boundary only. It
does not create human semantic authority or any benchmark result. The next
software consumer is sealed Relation Pass A; the next human authority gate is
still maintainer-owned reviewer-key registration and Concept annotation.

## G2.3 - Embargoed Relation Pass A neutral commitment

**Status:** Software checkpoint implemented and locally accepted; no real
reviewer policy, Concept seal, Relation label, Pass A seal, GoldBundle,
agreement, accuracy, or path-quality result exists

### Outcome and non-outcome

This checkpoint implements the first Relation-review software state machine
without generating or reviewing semantic labels:

```text
canonical frozen protocol + private Source materialization
-> historical six-leaf Concept authority + exhaustive pair manifest
-> private mutable Pass A worksheet with random commitment nonce
-> private immutable redacted Relation decisions
-> public label-free request + detached SSHSIG + root seal
```

The public root is a neutral anti-rewrite commitment. It is not `R_gold` and
does not prove that a human reviewed the worksheet, stayed blind to system
proposals, or waited 72 hours. Those limitations remain explicit as
`software_authenticated_* = false`. There is deliberately no reveal command.

### Main design and technology choices

- Strict/frozen Pydantic models encode the complete 66--190 pair universe,
  directed versus symmetric Relation identity, exact evidence-role sets, and a
  global Kahn DAG gate for prerequisites.
- A private 256-bit nonce generated with `secrets.token_hex(32)` salts both the
  worksheet and Relation artifact commitments. SHA-256 therefore binds exact
  bytes without exposing a low-entropy deterministic label hash.
- `RelationPassAPublicCommitmentPaths` and `RelationPassAPrivatePaths` make the
  visibility boundary part of the API type. Public verification cannot be
  handed a path capability that names the hidden label artifact.
- Before every authority-changing transition, the workflow reloads canonical
  frozen-protocol JSON/sidecar, private Source materialization/sidecar,
  historical Concept policy from Git, and the complete signed Concept DAG.
  Later logic consumes those fresh replay objects instead of the caller-owned
  capability.
- Publication deep-reparses the full Signed graph into a detached local
  snapshot, replays the embedded OpenSSH signature, evidence, privacy,
  lineage, and Concept membership, and only then begins durable I/O.
- Four output paths must be pairwise distinct. Private and public leaves are
  batch-preflighted before the first write, immutable writes never overwrite,
  and the public seal is last so a crash can leave retryable orphans but no
  authoritative root.
- CLI preparation independently batch-preflights its two private candidates;
  public models and receipts have explicit field allowlists and reject path or
  label expansion in regression tests. CLI failures remain static and omit
  private quotes, paths, and tracebacks.

### Adversarial findings that changed the design

1. Publishing Pass A labels would break Pass B blindness, while leaving A
   private and mutable would permit rewriting after B. The neutral signed hash
   commitment supplies both embargo and binding.
2. A deterministic hash alone is not hiding for a finite label space. A
   private random nonce now salts the worksheet and immutable artifact.
3. One path DTO exposed the private artifact path to public-only consumers.
   Separate public/private capability types now make that handoff impossible
   without an explicit broader API.
4. Checking a caller-issued Concept receipt in memory left coordinated nested
   mutation and path substitution risks. Protocol, Source, Concept policy, and
   all six Concept leaves are now replayed from repository-derived locations
   and Git history.
5. Replaying a Concept DAG with a caller-mutated reviewer policy could trust a
   substituted signing key. The historical Concept policy is reconstructed
   from its exact reachable registration commit before signature replay.
6. Validating Signed data and later rereading the caller object created a
   validation-to-publication TOCTOU window. Publication now uses only a deep,
   hash-matched snapshot captured at entry.
7. Signature/evidence validation after writing the root could leave an
   immutable invalid seal. All cryptographic and private binding checks now run
   before the first preflight/write and are repeated by post-write reload.
8. Sequential preflight could discover a collision or later conflict after an
   earlier leaf appeared. Paths are unique and all destinations preflight as a
   batch; identical retry and seal-last recovery are tested explicitly.
9. Treating policy availability as permanent would let a stale capability
   authorize new work. Active revocation blocks authoring, while a separately
   loaded historical authority preserves verification of old commitments.
10. A receipt-format check did not bind
    `acquisition_manifest_sha256` to the canonical protocol. Relation replay
    now compares it with the protocol's acquisition binding before use.

### Verification and honest claim boundary

- Relation Pass A focused integration suite: `23 passed` in 292.38 seconds;
- shared annotation boundary suite: `145 passed, 1 skipped` in 468.65 seconds;
- complete backend regression: `1118 passed, 7 skipped, 1 warning` in 819.30
  seconds;
- the warning is the existing Starlette `httpx` deprecation warning;
- the frozen real CS336 Lecture 3 Source-slice protocol regression passes with
  the G0.2 derivation closure unchanged;
- Python compileall, changed-Python 88-column scan, and `git diff --check`
  pass;
- independent final code review found no remaining P0/P1 in this checkpoint;
- a separate documentation/claim audit found no label, nonce, private-path,
  humanity, blindness, delay, accuracy, or gold-result overclaim after its
  status-summary corrections;
- remote change-level CI remains the push gate.

The next software checkpoint is a Git-registered public Pass A commitment
authority plus a typed readiness check based on repository history and the
frozen 72-hour rule. Only after that boundary exists may Pass B initialization
be implemented, and its tests must prove that it receives only public Pass A
paths and never reads the hidden artifact. Real policy registration and every
semantic decision remain maintainer-owned.

## G3 backend - Deterministic Concept Graph paths

**Status:** Backend implementation, full local regression, commit `c06a75c`,
remote push, and GitHub CI complete; performance and release acceptance remain
pending, while the first G4 UI slice is recorded below

### Product-priority correction

After G2.3, the project had invested deeply in future gold-label integrity
while the user-visible Concept path did not exist. The next G2.4 checkpoint
(Git-derived commitment authority and 72-hour readiness) remains valid, but is
explicitly deferred. Work moved to the smallest useful vertical product slice:
read one reviewed GraphVersion and return reliable Local, Trace, and Learning
results. Human gold is still required before public-course quality claims; it
no longer blocks implementation of the product behavior it will later test.

### Outcome

```text
exact course + published graph version
-> same-read-snapshot integrity and Source-authority observation
-> active/current authority gate
-> deterministic adjacency
-> Local BFS | shortest Trace BFS | prerequisite closure + Kahn
-> evidence-bearing response + canonical result hash
```

The checkpoint adds three exact-version GET APIs. Local performs bounded N-hop
BFS and returns the allowed-type induced subgraph. Trace distinguishes a found
shortest path, a fully exhausted unreachable search, and a search stopped by
hop/node limits. Learning collects the complete incoming-prerequisite closure,
then returns stable topological layers and a stable linearization. It refuses
to present a partial closure as an authoritative learning order.

Direction is explicit, symmetric edges traverse both ways, and equal choices
use one frozen v1 relation priority plus stable IDs. Results bind the GraphVersion
content hash, normalized inputs, ordered identities, traversal orientation,
layers, and terminal state. Full published Concept/Relation evidence is carried
in the DTO, so no second evidence schema or N+1 metadata fetch was introduced.

### Failures and tests

The service separates missing resources, invalid bounds, oversized prerequisite
closure, inactive/stale authority, SQLite contention, and corrupt graph state.
Focused tests cover direction/symmetry, stable equal paths, unreachable versus
bounded search, node truncation, cycle/duplicate/missing-endpoint rejection,
evidence-bearing responses, exact-version lookup, and Source drift. An
independent focused run passed `7` tests in `12.52s`; the only warning was the
existing Starlette/httpx deprecation warning.

### Honest debt and non-claims

The current store first performs full snapshot validation and then hydrates the
graph, while the engine rebuilds adjacency on every request. No cross-request
normalized cache exists and no 1,000/10,000-node P95 result has been accepted.
The G4 Path View, server-owned Graph Evidence target/content resolver, browser
E2E, narrow/keyboard acceptance, real human CS336 gold, and public-course path
quality report remain absent. This checkpoint proves a deterministic backend
algorithm/API slice, not learning effectiveness, production-scale performance,
or superiority over NotebookLM.

### Final local verification

- focused G3 engine/API suite: `7 passed`;
- adjacent Concept graph store/API/publication/path regression: `54 passed`;
- complete backend suite: `1125 passed, 7 skipped` in `811.87s`;
- independent review: no P0/P1, plus 100 generated DAGs cross-checked for
  Trace shortest-hop distance and Learning ancestor closure;
- the sole warning remains the pre-existing Starlette/httpx deprecation.

## G4 product slice - Evidence-backed Concept paths in Studio

**Status:** User-visible vertical slice implemented and locally accepted;
full G4 quality/performance/release gate remains open

### Outcome

Studio Explore now consumes the authoritative published Concept Graph instead
of the CardRelation discovery prototype:

```text
course -> current GraphVersion -> Overview / Local / Trace / Learning
-> inspect Concept or Relation -> immutable graph evidence identity
-> server target/content resolver -> shared CitationInspector -> Source
```

The UI preserves the backend's exact path order and graph content hash. It
distinguishes no publication, empty, Source-stale, unreachable, bounded,
loading, and error outcomes. Every displayed Relation exposes its rationale and
immutable evidence. The resolver looks evidence up by course, graph version,
owner kind, owner ID, and evidence ID, then reuses the existing citation policy
for course isolation, projection and Chunk currentness, typed Locator, managed
root, file hash, no-follow opening, and Range responses. Source drift keeps the
saved quote but returns `snapshot_only`; live content fails with `409`.

`CitationInspector` was generalized to accept a Source evidence snapshot plus
an optional server resolver, so Chat and Concept Graph evidence share one
preview, degraded-state, context, keyboard-close, and focus-restoration path.
No new frontend dependency, graph database, router, or global state framework
was introduced.

### Findings that changed the implementation

1. The first graph effect cleanup aborted only publication loading. It now also
   aborts an in-flight Path request and advances its epoch, preventing state
   updates after course change or unmount.
2. A malformed current Chunk Locator originally raised `500`. Current Locator
   hydration now fails closed; target resolution preserves the historical
   snapshot and content resolution returns `409`.
3. The first integration duplicated the Studio heading and course selector.
   The host now owns both, while the nested feature starts at `h2`.
4. Replacing the product entry left the old `GraphView`, its tests and its sole
   `react-force-graph-2d` dependency unreachable. They were deleted instead of
   preserving a second graph product or adding a compatibility layer; Git
   history remains the rollback mechanism.

### Local acceptance

- focused backend path/publication/citation suites: `36 passed, 1 skipped`;
- complete post-removal frontend suite: `210 passed` across `27` files;
- ESLint, TypeScript/Vite build, Python compileall, production-dependency npm
  audit, and `git diff --check` pass;
- clean real-browser PDF journey: Explore -> two-hop Trace -> Relation ->
  evidence -> page 1, with the exact quote highlighted, Escape close, and
  trigger focus restoration;
- Local returned three Concepts/two Relations, Learning returned three
  layers/two Relations, and the clean cold load had one page heading, one
  course selector, and no console errors;
- independent final review found no P0/P1 in the G4 diff.

### Honest remaining boundary

This checkpoint proves the useful Path -> Relation -> Source loop. It does not
complete G4. An Obsidian-style Concept overview, richer stable path layout,
candidate review/edit, durable browser E2E, narrow-screen/accessibility gates,
public-course human gold and path/Locator metrics, 1k/10k cold/warm profiles,
full backend non-regression on the final commit, and desktop release acceptance
remain required.
