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
