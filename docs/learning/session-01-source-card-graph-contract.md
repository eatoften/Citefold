# Session 1: Source, Card, and Graph Contracts

- **Product stage:** G0
- **Session status:** Started; maintainer exercise not yet accepted
- **Mastery target:** M0 -> M1
- **Expected time:** 90-120 minutes
- **User-owned artifacts:** two data-flow drawings, four annotation decisions,
  five closed-book answers
- **Product protocol:** [Draft graph annotation protocol](../graph-annotation-protocol.md)

## Why this is the first session

Writing G1 tables before understanding current identity and evidence flow would
make it easy to rename `Card` to `Concept` and preserve the existing weakness.
Session 1 establishes the semantic boundary first:

```text
original Source != canonical Source projection != Chunk != Card != Topic != Concept
```

The most important current-code finding is that the repository has two useful
but not yet unified pipelines.

## Current pipeline A: canonical evidence and Chat

```text
VideoJob + TranscriptChunk
SourceAsset + SourceUnit
published NotebookNote snapshot
        |
        v
course_source_service.reconcile_course_sources()
        |
        v
CourseSource + CourseSourceChunk + typed SourceLocator
        |
        +-> SourceChunk embedding index
        +-> source-scoped retrieval
        +-> Grounded Chat
        +-> sentence citation -> server resolver -> original location
```

Important distinction:

- in product language, **Source** means the user's original or explicitly
  published material;
- in code, `CourseSource` is the canonical, replaceable reading projection over
  video jobs, imported assets, and published Note snapshots;
- `CourseSourceChunk` is the bounded retrievable evidence unit;
- `SourceLocator` is the typed address back to a video time, PDF page, PPT
  slide, DOCX paragraph, text section, or immutable Note section.

Code path:

| Responsibility | Symbols/files |
| --- | --- |
| Locator and Source contracts | `VideoTimeLocator`, document locators, `CourseSource`, `CourseSourceChunk` in [course_source.py](../../backend/app/course_source.py) |
| Reconcile all projections | `reconcile_course_sources()` in [course_source_service.py](../../backend/app/course_source_service.py) |
| Build video/document chunks | `_chunk_from_transcript()`, `_chunk_from_source_unit()` in [course_source_service.py](../../backend/app/course_source_service.py) |
| Replace projection transactionally | `replace_course_source_projection()` in [course_source_store.py](../../backend/app/course_source_store.py) |
| Incremental embedding projection | `index_course_sources()` in [source_index_service.py](../../backend/app/source_index_service.py) |
| Source APIs | Source routes around `list_course_sources()` in [main.py](../../backend/app/main.py) |
| Source UI | [SourcesLibrary.tsx](../../frontend/src/features/sources/SourcesLibrary.tsx) |

## Legacy pipeline B: video Cards and CardRelation Explore

> G4 已将这个前端 Explore 入口替换为 published Concept Graph workspace，旧的
> `GraphView.tsx`、测试和 force-graph 依赖已经删除。下面保留的是历史数据流，
> 用于理解为什么 CardRelation 不能作为权威 Concept Graph；CardRelation 后端
> 数据/API 仍兼容，但不再是 Studio Explore 的输入。

```text
Video transcript
   |
   v
TranscriptChunk / TranscriptContext
   |
   v
local LLM card draft
   |
   +-> exact quote matching to video timestamps
   v
KnowledgeCard + claims + KnowledgeCardEvidence
   |
   v
CardEmbedding
   |
   +-> cosine candidate -------------------------------+
                                                         |
selected Card pair -> manual relation -------------------+
                                                         |
saved relation + endpoint Cards -> LLM classification ---+
                                                         v
                                               CardRelation rows
                                                         |
                                                         v
                                   GET /courses/{id}/card-relations
                                                         |
                                                         v
                                      React ForceGraph2D Explore view
```

The automatic Card path still consumes `TranscriptChunk`; it does not consume
the unified `CourseSourceChunk` contract. `KnowledgeCardEvidence` stores an
exact quote and video start/end time, but not canonical `source_id`, `chunk_id`,
typed locator, Source revision, or content hash. Therefore imported PDF/PPTX/
DOCX Sources already work in retrieval/Chat but do not yet naturally enter the
same Card/Concept generation path.

Code path:

| Responsibility | Symbols/files |
| --- | --- |
| Prepare/process automatic chunks | `run_auto_card_generation()`, `_prepare_chunks()`, `_process_chunk()` in [auto_card_generation_service.py](../../backend/app/auto_card_generation_service.py) |
| Generate/ground Card drafts | `draft_knowledge_cards()`, `_ground_claims()`, `_match_evidence_quote()` in [card_service.py](../../backend/app/card_service.py) |
| Build and validate a Card domain object | `build_job_card()` in [knowledge_card_service.py](../../backend/app/knowledge_card_service.py) |
| Atomically publish Cards, ReviewItems, and the chunk ledger | `publish_chunk_success()` in [card_generation_chunk_store.py](../../backend/app/card_generation_chunk_store.py) |
| Embed Cards | `embed_course_cards()` in [card_embedding_service.py](../../backend/app/card_embedding_service.py) |
| Create similarity candidates | `recompute_course_card_relations()` in [card_relation_service.py](../../backend/app/card_relation_service.py) |
| Store relation lifecycle | [card_relation_store.py](../../backend/app/card_relation_store.py) and `card_relations` schema in [db.py](../../backend/app/db.py) |
| Expose graph API | `GET/POST /courses/{course_id}/card-relations` in [main.py](../../backend/app/main.py) |
| Historical frontend type and renderer | 已从工作树删除；参见 [G4 implementation log](../productization-log.md#g4-product-slice---evidence-backed-concept-paths-in-studio) |

Current CardRelation strengths:

- persistent `suggested / accepted / rejected / hidden` review state;
- explicit method: `cosine_similarity / local_llm / manual`;
- manual relation editing and course isolation;
- unique storage keys and a tested force-graph UI;
- abort/epoch protection against a stale course response reaching the UI.

Current limits:

- a graph node is a Card, not a stable Concept;
- one Card may compress several Concepts and one Concept may appear in several
  Cards/Sources;
- relation `explanation` is free text, not locatable relation evidence;
- similarity is stored in the same broad relation model as semantic relation
  types even though similarity is only a proposal signal;
- relation evidence has no Source/Chunk revision or stale contract;
- current manual relation creation defaults to accepted without the future
  evidence/publication gate;
- prerequisite acceptance has no graph-wide cycle/publication check;
- the force layout has no N-hop Local view, A-to-B trace, prerequisite closure,
  topological layers, or stable left-to-right Path view.

## Target G1-G4 flow

```text
original Source or immutable Note snapshot
        |
        v
versioned Source Chunk + typed Locator + content hash
        |
        v
ConceptEvidence --------------------------+
        |                                 |
        v                                 |
Concept revision + aliases                |
        |                                 |
        +-> human/model/import proposal   |
                          |               |
                          v               |
              ConceptRelation revision <--+
              + support basis
              + support-role evidence
              + proposal origin
              + review status
              + validity status
              + rationale
                          |
                          v
        complete draft graph validation in BEGIN IMMEDIATE
                          |
                          v
              immutable published GraphVersion
                          |
             +------------+-------------+
             v            v             v
         Local BFS    A-to-B trace   prerequisite layers
             |            |             + stable linearization
             +------------+-------------+
                          v
        stable DTO/UI -> node/edge -> server Source resolver
```

This target deliberately separates:

- **identity**: what knowledge entity is this?
- **evidence**: which original revision supports it?
- **proposal origin**: who first suggested it?
- **review decision**: did a reviewer accept it?
- **validity**: is the accepted evidence current now?
- **publication**: which immutable graph version can authoritative paths use?

## Why the entities are different

| Entity | Main responsibility | Why it cannot replace Concept |
| --- | --- | --- |
| Source/Chunk | factual material and locatable evidence | evidence can mention several Concepts and is not normalized identity |
| Card | compact derived learning artifact | one Card may compress multiple Concepts; generated wording/granularity changes |
| Topic | curriculum/navigation hierarchy | it is organizational and often broader than one teachable identity |
| Concept | stable teachable identity across evidence/artifacts | it needs aliases, merge/split history, evidence, reviewed typed relations, and versioning |

## Reading order

Do not read whole 1,000-line files. Follow this order and stop at the named
symbols:

1. [course_source.py](../../backend/app/course_source.py): locator classes,
   `CourseSource`, `CourseSourceChunk`, stable ID/hash helpers.
2. [course_source_service.py](../../backend/app/course_source_service.py):
   `reconcile_course_sources()`, source/chunk projection helpers.
3. [knowledge_card.py](../../backend/app/knowledge_card.py):
   `KnowledgeCardEvidence`, claim, and Card contracts; list missing Source IDs.
4. [auto_card_generation_service.py](../../backend/app/auto_card_generation_service.py):
   `run_auto_card_generation()`, `_prepare_chunks()`, `_process_chunk()`.
5. [card_relation.py](../../backend/app/card_relation.py): relation type,
   method, status, domain row, graph node/edge DTO.
6. [card_relation_service.py](../../backend/app/card_relation_service.py):
   recompute, manual create, LLM classify, and graph assembly.
7. [card_relation_store.py](../../backend/app/card_relation_store.py):
   upsert, candidate replacement, query, update; then inspect the SQL in
   [db.py](../../backend/app/db.py).
8. 从 Git 历史查看已删除的 `frontend/src/GraphView.tsx`：load with abort/epoch、
   filters/adjacency、mutations、force renderer 和 inspector；不要把它恢复为
   新 Concept Graph 的兼容层。
9. Read one proof at each boundary rather than trusting prose:
   [test_course_sources.py](../../backend/tests/test_course_sources.py),
   [test_card_relations_api.py](../../backend/tests/test_card_relations_api.py),
   以及 Git 历史中的 `frontend/src/GraphView.test.tsx`。

## Maintainer exercise A: draw the current system

Without copying the diagrams above, draw **one current-system diagram** that
contains pipeline A and pipeline B as parallel flows. Mark every node with one
category:

```text
[ORIGINAL] user material
[TRUTH]    durable product source of truth
[DERIVED]  replaceable projection/artifact
[MODEL]    model-generated proposal/output
[HUMAN]    explicit review/edit
[UI]       presentation only
```

Your drawing must make the missing connection between `CourseSourceChunk` and
Card/Concept generation visually obvious.

## Maintainer exercise B: draw the target flow

Redraw the G1-G4 target from Source revision to evidence navigation. Mark:

- the draft/published boundary;
- the SQLite transaction boundary;
- where model suggestions stop;
- where `accepted + current` is required;
- which outputs are deterministic;
- how a user returns from an edge to original evidence.

## Maintainer exercise C: annotation decisions

Fill the four `[USER-AUTHORED]` rows in the
[annotation worksheet](../graph-annotation-protocol.md#session-1-maintainer-worksheet).
You may use your own course Concepts or these prompts; the relation/direction/
evidence/reason must remain your work:

| Exercise | Concept pair | Maintainer responsibility |
| --- | --- | --- |
| A | Linear Algebra / Singular Value Decomposition | decide relation type/direction and required evidence |
| B | Loss Function / Gradient Descent | decide relation type/direction and required evidence |
| C | Convolution / Image Filtering | decide relation type/direction and required evidence |
| D | Backpropagation / Gradient Descent | decide whether evidence is too ambiguous and explain rejection if so |

Do not infer a relation from general knowledge alone. If the selected course
Source does not support a precise relation or justified inference, reject the
edge rather than manufacture a plausible graph.

## Closed-book questions

These stable IDs are the canonical Session 1 question set. Reply in your own
words:

1. **S1-Q1:** What does "Source is original material" mean when `CourseSource`
   itself is a derived canonical projection?
2. **S1-Q2:** Why is Card retrieval/generation currently not the same pipeline
   as unified Source retrieval?
3. **S1-Q3:** Why can neither cosine similarity nor an LLM classification
   automatically become an accepted prerequisite?
4. **S1-Q4:** Why must `review_status` and `validity_status` be separate?
5. **S1-Q5:** What must be rechecked inside the SQLite publication transaction,
   and what race would exist if cycle detection happened outside it?

## M1 acceptance rubric

Session 1 reaches M1 only when:

- both drawings have correct identities, directions, and truth/derived/model labels;
- the target drawing returns both nodes and edges to original Source evidence;
- the four annotation rows use the frozen relation direction semantics;
- ambiguous evidence is rejected instead of hidden behind `related`;
- the five answers explain at least one concrete failure path;
- the maintainer can point to one backend model, one service, one store, one
  API route, one frontend component, and one test without being given the path.

No G1 schema is implemented in Session 1. The purpose is to make the next
migration a design the maintainer understands, not a generated table dump.

## Durable maintainer artifacts

Create
`docs/learning/artifacts/session-01-maintainer-response.md` for the two drawings,
the five answers, corrections, and the final accepted version. Fill the four
user-owned rows directly in the annotation protocol worksheet. Only after the
live review, append the result to the single [learning review ledger](README.md#复习总账).

## Reply template to start the live review

```text
Current system (pipeline A and pipeline B in one diagram):

Target flow:

S1-Q1:
S1-Q2:
S1-Q3:
S1-Q4:
S1-Q5:

The part I am least sure about:
```
