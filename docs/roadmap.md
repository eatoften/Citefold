# Video Course Cards Roadmap

Last updated: 2026-07-27

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
built and hardened. Existing research artifacts remain immutable and may be
reused only when they satisfy a product requirement and pass product tests.

## Active Productization Program

The program is split into independently verifiable stages. A stage is complete
only when its implementation, tests, decision record, product log entry, Git
commit, and remote push are all complete.

| Stage | User outcome | Status |
| --- | --- | --- |
| P0.0 Product contract | One product direction, acceptance gates, and engineering journal | Complete |
| P0.1 Unified Sources | Videos and local documents expose one Source/Chunk/Locator contract and index | Complete |
| P0.2 Grounded Chat | Persistent multi-turn answers with abstention and source-scoped retrieval | Complete |
| P0.3 Verifiable citations | Sentence-level citations open the exact video time or document location | In progress |
| P0.4 Source-first workspace | A course opens as Sources / Chat / Studio, with advanced tools secondary | Planned |
| P0.5 Reliability | Autosave, recoverable tasks, backup/restore, safe desktop lifecycle | Planned |
| P1.1 Notebook Notes | Free notes, save-answer-to-note, and note-to-source workflows | Planned |
| P1.2 Studio | Study, Review, and Course Map become a coherent output library | Planned |
| P1.3 Product polish | Onboarding, previews, search, empty/error states, accessibility, localization | Planned |
| P1.4 Engineering hardening | Frontend feature slices, shared API client, automated UI tests, performance | Planned |

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
hard-to-reverse decisions live under [`docs/decisions`](decisions).

The product separates complementary structures that serve different learning needs:

```text
KnowledgeCard   = one grounded unit of understanding
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
P0.1-P0.3. P0.1 and P0.2 now provide unified original Sources and durable
grounded Chat; P0.3 is connecting each stored citation to its exact original
location. The old dense-retrieval endpoint remains compatible:

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
