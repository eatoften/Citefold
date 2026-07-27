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
-> cancellable/retryable/resumable processing tasks
-> validated backup and restore
-> safe desktop shutdown and restart recovery
```

P0.5 must preserve the Source/Chat/Studio route and course-isolation contracts
rather than creating another task-specific navigation surface.
