# Video Course Cards Roadmap

Last updated: 2026-08-09

## Product Direction

Video Course Cards is a local-first learning workspace that turns long course
videos into grounded, reviewable knowledge. SQLite is the source of truth;
Markdown is a portable export snapshot.

The active product direction is a source-first course notebook:

```text
Course notebook
-> Sources
-> citation-grounded Chat
-> Notes and Studio outputs
-> Review, Course Map, and advanced graph exploration
```

The project is not attempting to copy every NotebookLM feature. Its intended
position is a local-first, video-course-native notebook where every factual
answer can return to a video timestamp, PDF page, slide, paragraph, or text
section. Existing timestamped cards, FSRS review, Course Map, and graph tools
remain differentiators rather than the primary navigation model.

Research on reasoning-guided retrieval is paused while this product loop is
built and hardened. The current product priority is an evidence-grounded
Concept graph and deterministic relationship/learning paths, not a claim that
graph retrieval already outperforms the Dense Chat baseline. Existing research
artifacts remain immutable and may be reused only when they satisfy a product
requirement and pass product tests.

## Active Productization Program

The program is split into independently verifiable stages. A stage is complete
only when its implementation, tests, decision record, product log entry, Git
commit, and remote push are all complete.

| Stage | User outcome | Status |
| --- | --- | --- |
| P0.0 Product contract | One product direction, acceptance gates, and engineering journal | Complete |
| P0.1 Unified Sources | Videos and local documents expose one Source/Chunk/Locator contract and index | Complete |
| P0.2 Grounded Chat | Persistent multi-turn answers with abstention and source-scoped retrieval | Complete |
| P0.3 Verifiable citations | Sentence-level citations open the exact video time or document location | Complete |
| P0.4 Source-first workspace | A course opens as Sources / Chat / Studio, with advanced tools secondary | Complete |
| P0.5 Reliability | Autosave, recoverable tasks, backup/restore, safe desktop lifecycle | Complete |
| P1.1 Notebook Notes | Free notes, save-answer-to-note, and note-to-source workflows | Complete |
| G0 Graph contract and baseline | Relation semantics, evidence rules, evaluation scope, and non-goals are frozen | In progress |
| G1 Concept graph substrate | Concepts have stable identity and accepted/current relations have current locatable evidence | In progress - G1.1 candidates plus G1.2a-G1.2c projection, review, and identity lifecycles implemented |
| G2 Golden course graph | One bounded course slice has a human-reviewed, versioned reference graph | Planned |
| G3 Deterministic paths | Users can inspect N-hop neighborhoods, A-to-B traces, and prerequisite learning order | Planned |
| G4 Evidence-first graph experience | Stable path views explain every node and edge and pass a graph-quality gate | Planned |
| P1.2 Studio | Study, Review, and Course Map become a coherent output library | Deferred until G4 |
| P1.3 Product polish | Onboarding, previews, search, empty/error states, accessibility, localization | Deferred until G4 |
| P1.4 Structural hardening | Large frontend slice extraction, shared API-client consolidation, and remaining release optimization | Deferred until G4 except release-blocking work |

### Current sequencing decision

P1.2-P1.4 remain accepted product work, but they are not the active delivery
sequence. The next program is G0-G4: make the Concept graph evidence-backed,
deterministic, and measurable before building more Studio outputs on top of it.

This deferral applies to the large structural refactor, not to quality work.
Every G stage must still include scoped architecture, automated unit/integration
tests, relevant UI/E2E checks, manual acceptance, documentation, an independent
commit, and a confirmed remote push. Reliability, maintainability, performance,
or security work that blocks a G stage or safe release is handled when discovered.

The architecture and alternatives are recorded in
[ADR-0008](decisions/ADR-0008-evidence-grounded-concept-graph-and-deterministic-paths.md).
The maintainer's parallel learning requirements are tracked in the
[project mastery plan](project-mastery-plan.md).

### Active Graph Reliability Program

The existing `CardRelation` force graph is a discovery prototype. It is not a
canonical Concept graph and must not be presented as a prerequisite planner.
G0-G4 upgrade the model without discarding the completed Source, Chunk,
Locator, citation, task, and recovery foundations.

```text
current evidence foundation
-> G0 freeze meaning and measurement
-> G1 persist Concept / Evidence / Relation
-> G2 build one reviewed golden graph
-> G3 compute deterministic traces and learning order
-> G4 ship stable path views with per-edge evidence
-> resume P1.2-P1.4
```

#### G0 - Graph contract, baseline, and evaluation freeze

Current checkpoint: the
[public-course benchmark contract](evaluation/public-course-benchmark.md) and
[acquisition module](modules/public-course-benchmark-acquisition.md) register
eight commit/hash-pinned CS336 Spring 2025 slide decks, physical
authoring/development/sealed partitions, a fail-closed downloader, and a
separate CC0 counterfactual trust fixture. No slide PDF is committed and no
quality result is claimed. CS61B remains an external-only robustness track
because a course-wide redistribution license has not been established.

G0 is still open: G0.2 must freeze the exact page ranges, parser/chunker and
Source/Chunk artifact identities, human label lineage, runner, prompts/models,
seeds, tolerances, and sealed-use ledger before any held-out result is opened.

Deliverables:

- freeze `Concept != Card != Topic` and define stable identity plus aliases;
- fix the direction rule: `A -> B` means A is a prerequisite of B;
- define relation types, direction/symmetry, evidence support roles, independent
  provenance axes, `candidate / accepted / rejected` review status, and
  `current / stale / tombstoned` validity status;
- register the golden-course scope, annotation protocol, graph metrics,
  correctness tests, latency budget, artifact hashes, and claim boundaries;
- preserve the 118-Card/20-edge research audit as a baseline, not a product result;
- keep learner modeling, Graph-for-all-Chat routing, a database replacement,
  and learning-outcome claims out of scope.

G0 is complete only when its contract is reviewed, documentation checks pass,
the seven already verified product-core commits are safely integrated into the
intended base, and the checkpoint is available on the remote. Local branch
creation alone is not completion.

The working rules and maintainer-owned examples live in the
[draft graph annotation protocol](graph-annotation-protocol.md). It remains
Draft until the Session 1 review and the remaining G0 freeze conditions pass.

#### G1 - Evidence-grounded graph substrate

Current checkpoint: G1.1 implements additive stable Concept/relation identities,
immutable candidate revisions, server-snapshotted current Chunk evidence,
course isolation, canonical symmetric edges, strict support-role bundles,
atomic SQLite writes, and bounded course-scoped read APIs. It accepts only
grounded human candidates and deliberately publishes no authoritative graph.
The implementation and boundary are recorded in the
[Concept Graph substrate module contract](modules/concept-graph-substrate.md).

G1.2a adds a non-reusable identity for each consecutive Source projection and
snapshots it on Concept/relation evidence. The manifest covers Source/Chunk
identity, type, order, exact text hash, typed Locator, and chunker contract;
`A -> B -> A` receives three generations. Currentness also checks the active
course/origin root and exact current Chunk fingerprint. Legacy graph evidence
remains auditable but requires regrounding. See the
[Source projection generation module](modules/source-projection-generation.md)
and [draft lifecycle contract](modules/concept-graph-draft-lifecycle.md).

G1.2b adds append-only Concept/relation edit, review, reject, and stale
revisions; normalized revision-owned aliases; exact endpoint-revision
bindings; historical reads with dynamic eligibility; and course-scoped
idempotent post-create operations. `BEGIN IMMEDIATE` plus head CAS serializes
review races. Concept head changes stale every incident current relation in the
same transaction, while prerequisite acceptance rejects both existing and
concurrently proposed cycles. Busy locks return a bounded `503`; unexpected
persistence errors fail as a safe `500`.

G1.2c adds append-only Concept merge and retirement revisions. A merge uses
compare-and-swap on both source and survivor heads, preserves the source's
historical aliases/evidence, never silently rewrites the survivor, and
atomically marks every incident current Relation stale. Current-head redirect
dependencies permit a star of duplicates around one active survivor while
forbidding redirect chains and cycles. Merge/retire retries use the same
course-scoped immutable operation ledger as other revision mutations.

G1.1/G1.2a-G1.2c do **not** satisfy the full G1 gate. Idempotent initial
Concept/relation creation, immutable graph publication, and the remaining
full-G1 release checks are still required. Initial create uses a separate
contract because it has no prior head revision; before release it needs a
client request ID, canonical request hash, and stored entity receipt. Until
then, the project must not claim that every graph write is idempotent.

Deliverables:

- additive `Concept`, alias, ConceptEvidence, typed ConceptRelation, and
  RelationEvidence schema, store, service, and API layers;
- course isolation, stable IDs, uniqueness, endpoint integrity, no self-loop,
  optimistic/idempotent review, and prerequisite cycle protection;
- immutable graph-version publication, Concept merge/retirement history, and
  Source/Chunk revision or hash checks that make old evidence ineligible immediately;
- acceptance rechecks current evidence, uniqueness, revision, and acyclicity in
  one course-scoped serialized transaction;
- old Card, Topic, CardRelation, and Explore behavior remains compatible;
- migration, transaction, concurrency, invalidation, recovery, and API tests.

#### G2 - Human-reviewed golden course graph

Deliverables:

- select one bounded course slice and normalize 12-20 Concepts plus aliases;
- map supporting Chunks/Cards and annotate 20-35 typed directed/symmetric relations;
- attach locatable evidence, rationale, and proposal/review provenance to every
  accepted/current entity and edge;
- complete a delayed blinded second pass, adjudicate disagreement, version,
  hash, and freeze the fixture; report a second human review separately if available;
- freeze a key-Concept inventory and bounded pair judgments, then report the
  ADR-0008-defined coverage, isolate, exact edge precision/recall, prerequisite
  direction, agreement, and per-type error measures before making graph claims.

Unsupported edges are removed even when doing so lowers coverage.

#### G3 - Deterministic traversal and path engine

Deliverables:

- filtered N-hop BFS for Local Graph;
- deterministic BFS for an explainable A-to-B Relationship Trace;
- prerequisite ancestor closure, cycle detection, topological layers, and one
  stable Kahn linearization for Learning Path;
- evidence-bearing DTOs and APIs for every path step;
- enforce relation/direction filters plus bounded hops/nodes and deterministic truncation;
- tests for unreachable nodes, multiple equal paths, filters, cycles, duplicate
  edges, missing endpoints, stable ties, adjacency materialization cost, and
  `O(V + E)` traversal over a normalized immutable graph version.

Similarity may propose candidates but is not a learning-order edge.

#### G4 - Evidence-first product integration and quality gate

Deliverables:

- retain force layout for overview exploration, while Local, Trace, and Path
  use stable left-to-right layered layouts;
- make every node and edge open its rationale and original Source locator;
- provide candidate review/edit, empty, unreachable, stale, loading, and error states;
- pass browser E2E, desktop, narrow-screen, keyboard, accessibility,
  performance, graph-integrity, citation, and complete non-regression checks.

G4 may support the bounded claim that paths on the frozen course graph are
correct, reproducible, and traceable. It does not establish that the product
improves learning outcomes or outperforms NotebookLM.

#### Graph quality gates

- 100% of accepted/current Concepts and typed relations have current locatable evidence;
- 100% of accepted/current relations preserve provenance and rationale;
- accepted-but-stale revisions remain auditable but never enter an authoritative path;
- zero self-loop, duplicate, cross-course, missing-endpoint, or accepted
  prerequisite-cycle violations;
- identical inputs on one graph version return identical ordered node/edge IDs
  and canonical result hash, excluding volatile transport metadata;
- every golden-fixture path step opens its recorded Source location;
- candidate-generation quality is measured separately and no model proposal
  becomes accepted truth automatically;
- Dense Chat retrieval, citations, and abstention remain a protected baseline.

### P0.4 completion summary

The P0.4 checkpoint makes the Source-first shell the product's canonical
information architecture:

```text
Primary navigation
-> Sources
-> Chat
-> Studio
   -> Cards
   -> Study
   -> Review
   -> Course map
   -> Explore
```

Delivered scope:

- typed query-route parsing, serialization, canonicalization, destination-
  owned entity parameters, and legacy URL migration;
- host-owned push/replace/popstate behavior with course/entity validation;
- one mixed video/audio/document Sources catalog with import, enable/index,
  delete, canonical chunk preview, and video handoff;
- full course Chat with persistent conversation deep links and
  per-conversation Source scope;
- one Studio shell around the five existing learning workflows;
- immediate course-state invalidation plus abort/epoch/sequence protection
  against late cross-course responses;
- responsive labeled bottom navigation, route-heading focus, one main
  landmark, and improved closed-rail semantics;
- no backend schema migration or new routing dependency.

P0.4 passed 104 frontend tests across 16 files, frontend lint, the production
build, a zero-vulnerability high-severity dependency audit, 526 passing
backend tests with one skip, repository hygiene checks, and real-browser
acceptance at 1280x720 and 360x640. The independent stage commit is the
checkpoint boundary; its immutable identity and remote equality are reported
in the delivery summary. Exact commands, bundle sizes, browser fixtures,
tradeoffs, and remaining risks are recorded in the
[productization log](productization-log.md); the architectural contract is recorded in
[ADR-0005](decisions/ADR-0005-source-first-workspace-and-route-contract.md).

### P0.5 completion summary

P0.5 makes local work recoverable instead of treating reliability as a set of
UI error messages. The checkpoint adds four connected guarantees:

```text
editing
-> immediate device draft
-> revisioned workspace draft
-> explicit domain save

long-running work
-> durable task reservation
-> bounded worker + persisted progress
-> cooperative cancel / retry / restart recovery

ordinary deletion
-> tombstone + trash journal
-> same-identity restore
-> namespace-bound, globally exclusive, restart-recoverable permanent purge

workspace recovery
-> online SQLite snapshot + managed files
-> manifest/hash/integrity validation
-> restart-bound restore + pre-restore safety point
-> write-ahead commit/rollback fences + receipt-last cleanup
```

The global Activity and Data & recovery utilities expose task progress,
cancel/retry, Trash restore/purge, and workspace backup/import/restore without
adding another primary product destination. Draft protection is integrated
into Chat, Study, cards, card notes, review input, and generated-card work. Video
processing, Source import/indexing, card generation, Chat generation, and
Study generation now share one persisted task protocol. Automatic cards also
publish cards, review items, and a per-chunk completion record atomically, so a
restart retries only unfinished model work. Frontend operation epochs prevent
late responses from a previous course, Source, conversation, or job from
publishing into the newly selected scope.

The desktop boundary now identifies a specific backend instance, stops only
its owned sidecar, asks the worker to quiesce before termination, and never
kills an unrelated process because it happens to own the configured port.
Restore is queued and applied before the database is opened; its result is
validated and reported separately from the restart itself. Interrupted Trash
claims recover before worker dispatch, and an imported purge plan must prove
the same entity/course ownership and global path exclusivity before it can
remove a managed file. Parent purge also preserves an incomplete child purge
journal, and database cleanup cannot touch files again after the artifact
phase. A completed restore rollback is also a durable, restart-resumable
publication phase rather than a best-effort error path.

The implementation and verification record is in the
[productization log](productization-log.md). The durable task, draft, Trash,
backup/restore, and desktop ownership contracts are recorded in
[ADR-0006](decisions/ADR-0006-local-workspace-lifecycle-and-recovery.md).

### P1.1 completion summary

P1.1 closes the course notebook's capture-to-retrieval loop without making
private working notes implicit evidence:

```text
write or capture a Note in Studio
-> revise with compare-and-swap protection
-> explicitly publish one durable revision
-> retrieve the stable note:<note_id> Source in Chat
-> open a sentence citation at its immutable note snapshot
```

Schema v8 adds a course-scoped Notebook Note aggregate, note-owned citations
and sentence spans, and immutable Source snapshots. Notes support free writing
and idempotent capture of a completed, grounded Chat answer. The editable title
and Markdown body advance through revision compare-and-swap, while the original
answer, model metadata, citation quotes, hashes, locators, and sentence spans
remain immutable and independently readable even after the originating Chat is
purged.

Publishing is an explicit action. It projects one exact note revision onto the
stable canonical Source `note:<note_id>` and deterministic, bounded Markdown
chunks; republishing a later revision appends a snapshot rather than rewriting
historical evidence. Publication, reconciliation, restore, and permanent purge
share the same Source lifecycle lock. Soft deletion hides the Note and its
projection, Undo/Recovery restores their identities, permanent purge removes
the complete subtree, and full-workspace backup/restore preserves the Note,
provenance, snapshots, chunks, and citation targets.

Studio now includes a dedicated Notes workspace with list, editor, provenance
panel, publish/update-Source actions, deep links, and recoverable deletion.
Grounded Chat answers expose **Save to notes**, and a note-derived Source can be
opened from Sources. Draft hydration is safe under React Strict Mode; recovery
is base-aware, server writes use revision compare-and-swap, and internal
navigation guards protect unsaved changes across Notes, Chat, Sources, Study,
and application routes.

Verification completed with `681 passed, 1 skipped, 1 warning` in the full
backend suite and `214 passed` across 27 frontend test files, plus Python
bytecode compilation, uv lock validation, frontend lint and production build,
Cargo formatting, locked check, and 6 locked tests, and a zero-vulnerability
high-severity npm audit. The build retains one documented optimization warning
for a `588.23 kB` main JavaScript chunk. A real-browser journey covered Note
creation, publication, indexing, grounded Chat, citation navigation,
answer-to-Note capture, deletion, Undo, and Recovery restore at desktop and
narrow widths with no horizontal overflow or console errors.

The complete decision and invariants are recorded in
[ADR-0007](decisions/ADR-0007-notebook-notes-and-derived-sources.md). At the
time of P1.1 completion, P1.2 was the next planned gate. ADR-0008 now defers
P1.2 until G4 is accepted.

### Stage gates

Every stage records:

1. the user-visible outcome and explicit non-goals;
2. alternatives considered and the reason for the chosen design;
3. schema, API, UI, and technology changes;
4. problems encountered, root causes, and remaining risks;
5. exact automated checks and manual acceptance scenarios;
6. a conventional commit subject and confirmed remote push.

The append-only implementation record is
[`docs/productization-log.md`](productization-log.md). Cross-stage,
hard-to-reverse decisions live under [`docs/decisions`](decisions). Product
delivery and the maintainer's personal ownership are tracked independently in
the [`project mastery plan`](project-mastery-plan.md).

The product separates complementary structures that serve different learning needs:

```text
KnowledgeCard   = one grounded unit of understanding
NotebookNote    = an editable synthesis with optional immutable Chat provenance
Topic           = the course's hierarchical curriculum structure
CardRelation    = a lateral semantic or logical connection between cards
ReviewItem      = one independently scheduled recall task
SourceUnit      = a locatable excerpt from video, slide, page, or document
LearningDocument = a versioned deep explanation grown around card anchors
```

This separation fixes the main weakness of a similarity-only graph. A force
graph is useful for discovery, but it does not tell a learner what to study
first or what to review today. The product therefore has three complementary
learning views:

```text
Course Map  -> understand the curriculum and choose a topic
Study       -> expand a concept with local documents and grounded citations
Review      -> act on due recall tasks
Explore     -> discover lateral card relationships
```

## Current End-to-End Flow

```text
local video
-> ffprobe validation
-> FFmpeg audio extraction
-> faster-whisper transcript with timestamps
-> sentence-transformer semantic chunks
-> local Qwen grounded card generation
-> SQLite cards + claims + evidence + review items
-> local PPTX / PDF / DOCX / text source units
-> canonical course Sources + typed locatable chunks
-> persistent incremental source-chunk embeddings
-> Studio Notes + explicit immutable note-Source snapshots
-> versioned concept study documents
-> card embeddings and persistent relations
-> Course Map / Study / Review / Explore / RAG baseline
-> Markdown or Obsidian export
```

The application runs as a React + FastAPI project and as a Tauri Windows
desktop application with a packaged backend sidecar.

## Architecture Rules

- `main.py` owns HTTP concerns only.
- Service modules own business workflows and validation.
- Store modules own SQLite CRUD and transactions.
- Pipeline modules own media and model computation.
- Claims must be grounded in timestamped transcript evidence.
- A card's learning content is independent from its review schedule.
- Suggested machine structure must be distinguishable from accepted user
  structure.
- User data must survive schema upgrades.

## Completed Foundation: Milestones 0-15

- Python 3.11, uv, FastAPI, React, TypeScript, Vite, pytest monorepo.
- Local upload, MIME/extension checks, ffprobe validation and CORS.
- FFmpeg 16 kHz mono PCM extraction and faster-whisper transcription.
- SQLite job lifecycle with retry, timestamps and failure handling.
- Transcript API, video player, timeline selection and polling UI.
- Local Ollama/Qwen integration and selectable models.
- Claim-level grounding with verified quotes and timestamps.
- Card persistence, editing, deletion, tags and decoupled user notes.
- Course/video/card workspace and automatic chunk-based card generation.
- Obsidian-friendly Markdown folder and zip export.

## Completed Knowledge Graph Baseline: Milestones 16-22

- Card embeddings stored in `card_embeddings`.
- Cosine similarity and top-k relation generation.
- Persistent `card_relations` with suggested/accepted/rejected states.
- Related-card and course-graph APIs.
- Obsidian-like left navigation and multi-view frontend.
- Ranked related-card view and force-directed Explore graph.
- Manual relation editing and local-Qwen relation typing.

The graph remains a discovery tool. It is not used as the curriculum hierarchy
or as the review scheduler.

This completed baseline is the input to G0-G4. It is not evidence that
canonical Concept identity, evidence-grounded relations, or deterministic
learning paths already exist.

## Milestone 23: Knowledge Card V2 (Completed)

### Problem

The old card mixed knowledge content, a single question/answer pair and a vague
`review_state`. That made it difficult to support multiple recall prompts or a
real spaced-repetition scheduler.

### Card structure

```text
knowledge_cards
  id
  job_id
  card_kind
  title
  summary
  key_points[]
  claims[]
    claim.id
    claim.text
    evidence[]
      evidence.id
      quote
      segment_start_seconds
      segment_end_seconds
  unsupported_terms[]
  tags[]
  content_status
  source_start_seconds
  source_end_seconds
  provider / model
  created_at / updated_at
```

`card_kind` describes the shape of knowledge:

```text
concept | definition | process | comparison | example | formula
```

`content_status` describes editorial quality, not memory state:

```text
draft | reviewed | needs_fix
```

Claims and evidence now have stable IDs so review prompts and future citations
can point to specific grounded facts.

### Review item structure

```text
review_items
  id
  card_id
  item_type
  prompt
  expected_answer
  source_claim_ids[]
  source
  status
  created_at / updated_at
```

One card can own multiple independent prompts:

```text
basic | cloze | explain | compare | apply
```

### Migration

- Existing card rows are migrated in-place to Card V2.
- Existing question/answer pairs become `review_items`.
- Existing cards receive stable claim/evidence IDs.
- The migration preserves user cards rather than resetting the database.

## Milestone 24: Topic Hierarchy (Completed)

### Problem

Card similarity does not express a readable course outline. Topics provide the
hierarchical structure needed for navigation and review planning.

### Tables

```text
topics
  id, course_id, parent_topic_id
  title, summary, position, depth
  method, status, is_system
  created_at, updated_at

topic_card_memberships
  id, topic_id, card_id
  role, position, method, confidence, status
  created_at, updated_at

topic_relations
  id, course_id, source_topic_id, target_topic_id
  relation_type, explanation, method, status
  created_at, updated_at
```

Every course has a system `Unsorted` topic. Cards without an accepted primary
topic are placed there without changing the card record itself.

Manual operations support:

- create, rename, nest, move and delete a topic;
- move a card to a primary topic;
- add or remove topic-level prerequisite/related relations;
- preserve cards when topics or courses are deleted.

## Milestone 25: Course Map (Completed)

The left navigation now provides a dedicated Course Map view.

Course Map supports:

- course selection;
- expandable topic tree;
- nested topic creation and editing;
- card counts and card previews;
- moving cards between topics;
- topic relation creation;
- suggested-topic preview and acceptance.

The Course Map is intentionally tree-first. It answers:

```text
What is this course about?
How is it organized?
Which cards belong to this concept?
```

## Milestone 26: FSRS Review Engine (Completed)

### Scheduling tables

```text
review_progress
  review_item_id
  fsrs_card_id, fsrs_state, step
  due_at, stability, fsrs_difficulty
  last_reviewed_at
  review_count, lapse_count
  created_at, updated_at

review_events
  id, review_item_id, rating, reviewed_at
  response_time_ms
  previous_phase, next_phase
  due_before, due_after, scheduled_days
```

The scheduler uses the official `fsrs` Python package. Each `review_item` is an
independent scheduling unit, so one weak recall prompt does not incorrectly
mark the whole card as mastered.

Ratings:

```text
Again | Hard | Good | Easy
```

Phases:

```text
new | learning | review | relearning
```

APIs:

```text
GET  /courses/{course_id}/review/queue
POST /review-items/{review_item_id}/rate
```

The queue can be filtered by course and topic and returns grounded claims and
evidence for answer verification.

## Milestone 27: Review Workspace (Completed)

The Review view supports a complete active-recall loop:

```text
choose course/topic
-> read prompt
-> optionally write a self-answer
-> reveal expected answer and source evidence
-> rate recall quality
-> FSRS schedules the next review
```

The UI also shows due/new/learning/review counts, due counts by topic, source
timestamps and a link back to the full card.

## Milestone 28: Embedding-Based Topic Suggestions (Completed)

### Goal

Help organize a large `Unsorted` collection while keeping the user in control.

### Algorithm

```text
accepted Unsorted cards
-> load compatible card embeddings
-> combine semantic vector + tag/source features
-> agglomerative clustering with cosine distance
-> deterministic fallback topic names
-> optional one-call local Qwen naming
-> persist suggested topics and memberships
-> user previews and accepts selected suggestions
```

Current feature weights:

```text
semantic embedding: 0.85
tags:               0.25
source job/time:    0.15
```

Only embeddings with the same model and dimension are clustered together.
Suggestions are stored with `status = suggested`; they do not overwrite manual
topics until the user accepts them.

APIs:

```text
POST /courses/{course_id}/topics/suggest
POST /topics/{topic_id}/accept
```

## Milestone 29: Local Source Assets And Units (Completed)

- Added local `source_assets` and `source_units` tables.
- Supports PPTX, PDF, DOCX, TXT, and Markdown extraction.
- Preserves slide, page, paragraph, and section locators.
- Stores SHA-256, extraction status, metadata, and local paths.
- Reserves `video_frame` units with timestamp/frame metadata for future vision.
- Keeps imported material local under `VCC_SOURCE_DIR`.

## Milestone 30: Concept Study Documents (Completed)

- Added versioned `learning_documents` independent from quick user notes.
- A document has one primary anchor card and multiple supporting card roles.
- Local Qwen generation combines card claims and selected source units.
- Course claims use `[C*]`; supplementary files use `[S*]` citations.
- Invalid citation labels are removed and source metadata is persisted.
- Manual edits, LLM generations, and restores create immutable versions.
- Added a lazy-loaded Study workspace with Markdown edit/preview, local upload,
  source selection, supporting-card selection, references, and version restore.

## Milestone 31: Learning Coverage And Topic Correction (Completed)

- Course Map shows card, Study document, due-review, source, and Unsorted counts.
- Each Topic exposes review and Study document coverage.
- Topic suggestions return mean embedding coherence, singleton count, largest
  cluster size, and all cluster sizes.
- Users can merge accepted Topics or split selected cards into a sibling Topic.
- Every Course Map card can open its Study workspace directly.

## Milestone 32: Card-Based Grounded RAG (Superseded)

This deferred card-only plan has been superseded by productization stages
P0.1-P0.3. Unified original Sources, durable grounded Chat, and exact
server-authoritative citation navigation are now complete. The old
dense-retrieval endpoint remains compatible:

```text
question
-> query embedding
-> retrieve accepted cards
-> optionally expand through trusted card relations/topics
-> build bounded grounded context
-> local Qwen answer
-> cite card, evidence quote and video timestamp
```

The assistant must say `not enough evidence` when retrieval does not support an
answer. Course/topic filters and retrieval diagnostics should be visible.

## Milestone 33: Evaluation Layer (Deferred)

Planned measurements:

- grounding pass and unsupported-claim rates;
- card generation latency and failure rate;
- duplicate-card rate;
- retrieval hit rate and citation correctness;
- relation precision and graph noise;
- topic coherence and orphan-card rate;
- review retention and lapse rate;
- user edit distance.

Evaluation should use a small versioned benchmark course plus structured user
feedback rather than relying on visual demos alone.

G4 includes a bounded graph-quality gate only. It does not complete this
broader product-evaluation program or establish educational effectiveness.

## Milestone 34: Feedback Dataset And Agentic Learning Loop (Deferred)

Planned records:

- generated card to edited card diffs;
- save/delete decisions;
- accepted/rejected relation and topic suggestions;
- review outcomes and response time;
- evidence clicks and citation corrections;
- RAG answer feedback.

These records can later support prompt optimization, reranking, preference
learning, reward modeling and an agentic retrieval policy. The feedback schema
should be built only after the baseline workflows and metrics are stable.

## Final Product Shape

```text
upload a course locally
-> obtain timestamped transcripts
-> generate grounded knowledge cards
-> organize them into an editable course map
-> expand anchor cards into source-backed Study documents
-> review with evidence-backed FSRS prompts
-> explore semantic and logical relationships
-> ask citation-grounded questions
-> export portable Markdown
-> learn from human corrections and review outcomes
```

The graph helps the learner discover, the map explains course structure, Study
documents support deep understanding, and the review queue helps the learner
remember.
