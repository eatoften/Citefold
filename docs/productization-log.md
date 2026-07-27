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
