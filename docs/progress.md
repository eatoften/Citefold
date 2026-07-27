# Video Course Cards — Project Plan and Status

Last updated: 2026-06-20

> **Historical archive.** This file records the earliest MVP learning plan and
> is no longer the current project status. The active product roadmap is
> [`roadmap.md`](roadmap.md), and verified implementation history is recorded in
> [`productization-log.md`](productization-log.md). Statements below such as
> planned SQLite/Electron work and early test counts are intentionally preserved
> as history rather than silently rewritten.

## Product Goal

Build a local-first multimodal learning assistant that converts videos into
timestamp-grounded, reusable personal knowledge.

The long-term workflow is:

Video
→ transcript and visual context
→ concept extraction
→ structured knowledge notes
→ Obsidian knowledge base
→ retrieval and personal learning agent
→ user feedback and model improvement

## First MVP

The first complete version should allow a user to:

1. Select and upload a local video.
2. Validate and inspect the video.
3. Generate timestamped transcript segments.
4. Click or select a concept from the transcript.
5. Generate a note grounded in the surrounding video context.
6. Save the note as Markdown in an Obsidian vault.
7. Preserve the source video and timestamp.

## Explicitly Deferred

These are valuable, but should not be added before the first MVP works:

- Capturing live system audio.
- Electron desktop packaging.
- Knowledge graphs and GraphRAG.
- Autonomous or multi-agent workflows.
- LoRA, DPO, and other post-training.
- Personalized retriever fine-tuning.
- Real-time streaming transcription.
- Advanced multimodal frame retrieval.

## Learning Goals

This project is also a learning project.

### Backend engineering

- Python project and package structure.
- FastAPI routes, request handling, response models, and errors.
- HTTP semantics and API design.
- Automated testing with pytest.
- Dependency injection and configuration.
- SQLite and data modeling.
- Background jobs and pipeline state.
- File-system and process management.

### Machine learning and algorithms

- Build simple baselines before adding frameworks.
- Understand embeddings and similarity search.
- Implement and evaluate retrieval pipelines.
- Work with timestamped ASR output.
- Design measurable experiments and ablations.
- Record latency, accuracy, retrieval, and faithfulness metrics.

### Frontend

React is used only as much as required to build the product interface:

- Video playback.
- Transcript display.
- Concept selection.
- Upload and processing status.
- Knowledge-card preview.

The main technical focus remains Python, backend systems, and ML.

## Current Technology Stack

- Frontend: React, TypeScript, Vite
- Backend: Python, FastAPI
- Python environment and dependency management: uv
- Testing: pytest and FastAPI TestClient
- Local files: pathlib and the file system
- Planned metadata storage: SQLite
- Planned media inspection: FFmpeg / ffprobe
- Planned ML stack: PyTorch and local model tooling
- Planned note output: Obsidian-compatible Markdown
- Planned desktop packaging: Electron, after the web-based workflow works

## Repository Structure

Current expected structure:

Video_Course_Cards/
├── README.md
├── frontend/
│   ├── src/
│   ├── package.json
│   └── package-lock.json
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   └── main.py
│   ├── tests/
│   │   ├── test_health.py
│   │   └── test_videos.py
│   ├── data/
│   │   └── uploads/
│   ├── pyproject.toml
│   └── uv.lock
└── docs/
    ├── first-milestone.md
    └── progress.md

The `data/`, `.venv/`, `node_modules/`, `__pycache__/`, and pytest cache
directories should not be committed.

## Development Roadmap

### Milestone 0 — Project Foundation

Status: Complete

Completed:

- Initialized a Git repository.
- Created a monorepo with frontend, backend, and docs.
- Initialized the Python backend with uv.
- Installed FastAPI.
- Initialized React and TypeScript with Vite.
- Verified that frontend and backend can run locally.
- Connected React to the FastAPI health endpoint.

### Milestone 1 — Video Ingestion

Status: In progress

Completed:

- Local video selection and playback in React.
- FastAPI video upload endpoint.
- Unique stored filenames using UUIDs.
- Allowed-extension validation.
- MIME-type validation.
- Structured upload response using a Pydantic model.
- `201 Created` response for successful uploads.
- `415 Unsupported Media Type` for rejected uploads.
- Upload files stored outside Git.
- Backend refactored into the `app` Python package.

Automated tests completed:

- Health endpoint returns `200`.
- Unsupported `.txt` upload returns `415`.
- Successful video upload returns `201`.
- Successful upload writes the expected bytes.
- Tests use a temporary directory rather than the real upload directory.

Current result:

- 3 tests passing.

Remaining work:

- Verify that the FastAPI CLI starts correctly after the package refactor.
- Inspect the actual file contents instead of trusting extension and MIME type.
- Extract video metadata.
- Add upload-size limits.
- Handle partially written files and failures.
- Store video records in SQLite.
- Connect the React upload UI to the real upload endpoint.

### Milestone 2 — Media Inspection

Status: In progress

Completed:

- Installed and verified FFmpeg and ffprobe.
- Called ffprobe safely from Python using subprocess.
- Separated stdout, stderr, and process exit codes.
- Parsed ffprobe JSON output.
- Added `MediaProbeError`.
- Verified that media contains at least one video stream.
- Integrated real media validation into the upload endpoint.
- Deleted invalid uploaded files after failed validation.
- Added unit tests for successful and failed probing.

Current test result:

- 8 tests passing.

Remaining:

- Normalize ffprobe output into typed video metadata.
- Extract duration, resolution, codec, and audio availability.
- Return metadata from the upload endpoint.
- Add an integration test using a real media fixture.

### Milestone 3 — Transcription Baseline

Status: Not started

Planned:

- Extract audio from the uploaded video.
- Run a non-streaming ASR baseline first.
- Store timestamped transcript segments.
- Measure transcription time and real-time factor.
- Define interfaces that allow ASR implementations to be replaced later.

### Milestone 4 — Interactive Transcript

Status: Not started

Planned:

- Display timestamped transcript segments in React.
- Keep transcript and video playback synchronized.
- Seek the video when a transcript segment is clicked.
- Allow the user to select a word, phrase, or concept.

### Milestone 5 — Knowledge-Card Generation

Status: Not started

Planned:

- Build a context window around the selected timestamp.
- Generate a structured concept note.
- Include source video, timestamp, and transcript evidence.
- Preview and edit the note before saving.
- Export Markdown to an Obsidian vault.

### Milestone 6 — Retrieval and Personal Knowledge

Status: Not started

Planned:

- Index Markdown notes.
- Implement lexical-search and embedding baselines.
- Add hybrid retrieval and reranking.
- Evaluate retrieval with Recall@K, MRR, and latency.
- Connect related concepts across videos.

### Milestone 7 — Agent, Knowledge Graph, and Personalization

Status: Not started

Planned only after the previous milestones are reliable:

- Tool-calling personal knowledge assistant.
- Human approval before modifying notes.
- Conflict and duplicate-note detection.
- Provenance-aware concept graph.
- Retriever fine-tuning from user interactions.
- LoRA or preference optimization only when enough training data exists.

## Known Limitations

The current upload validation is not secure media validation.

A client can rename a text file to `.mp4` and declare its MIME type as
`video/mp4`. The current backend will accept it because it does not inspect the
file contents.

Other current limitations:

- No database.
- No durable video processing status.
- No upload-size limit.
- File copying occurs inside the request.
- No cleanup strategy for interrupted writes.
- CORS origins are hard-coded.
- The frontend does not yet upload the selected video.
- The temporary `/videos/inspect` endpoint may still need to be removed.
- The test suite currently reports a Starlette/httpx deprecation warning.
- FastAPI CLI startup after the package refactor still needs explicit verification.

## Development Rules

1. Build one vertical slice at a time.
2. Do not add Agent, RAG, or training frameworks before the underlying pipeline exists.
3. Understand the basic implementation before introducing abstractions.
4. Add tests for both success and failure paths.
5. Keep generated data outside Git.
6. Make a Git commit whenever the project reaches a verified stable state.
7. Record important architectural decisions and experiment results in `docs/`.
8. Do not provide complete code by default.

## Teaching Format

For each new task, use:

1. Why this task matters.
2. Concepts required.
3. Assignment requirements.
4. Acceptance criteria.
5. Progressive hints.
6. Code review after the student attempts it.

Provide complete code only when explicitly requested.

## Current Next Action

Perform a runtime regression check after moving the backend into the `app`
package, then create a stable Git checkpoint.

After that, begin Milestone 2 by manually inspecting a real video with ffprobe.
