# ADR-0006: Local Workspace Lifecycle and Recovery Contract

- **Status:** Accepted and implemented in P0.5
- **Date:** 2026-07-27
- **Decision owners:** Project maintainer and Codex implementation agent

## Context

P0.1-P0.4 established one Source model, grounded multi-turn Chat, exact
citation navigation, and the Sources / Chat / Studio product structure. The
remaining P0 risk is no longer retrieval correctness. It is whether a local
notebook survives ordinary failure:

```text
typing -> route change or window close
long task -> cancel, process exit, or retry
delete -> immediate regret
database or device failure -> restore
desktop restart -> recover a coherent workspace
```

Before P0.5, the implementation did not provide that contract. Important
editor state existed only in React memory. Video processing and automatic card
generation were owned by FastAPI `BackgroundTasks`; Source indexing and Study
generation were synchronous requests. A process exit could therefore leave
domain rows in an active state without a live worker. Most delete operations
hard-deleted rows and files. The migration backup protected schema changes,
but it was neither a user backup nor a complete workspace snapshot. The Tauri
host killed its sidecar directly and could terminate an unrelated process that
happened to occupy port 8001.

P0.5 treats these as one lifecycle problem rather than five unrelated UI
features.

## Failure model

The stage is designed against these explicit failures:

1. The web view, backend, desktop shell, or machine exits at any instruction
   boundary.
2. A request is repeated because of a double click, timeout, or network retry.
3. An old worker reports progress after its attempt was canceled or retried.
4. Cancellation arrives while a subprocess, embedding batch, transcription,
   or LLM call cannot stop immediately.
5. A database transaction succeeds while a related file operation fails, or
   the reverse.
6. SQLite receives concurrent task progress, Chat, and autosave writes.
7. A backup is truncated, tampered with, from a newer schema, contains unsafe
   paths, or is restored into a different application-data directory.
8. Windows temporarily retains a database or media file handle.
9. An unrelated application owns the configured backend port.
10. A late course-A response arrives after the user has moved to course B.

## Decision

### Make persistence precede execution

Every long parsing or generation operation is represented by a durable task
row before a worker starts. HTTP requests enqueue or inspect work; they do not
own its lifetime.

The shared task state machine is:

```text
queued -> running -> succeeded
queued -> canceled
running -> canceling -> canceled
running -> failed
canceling -> succeeded when the handler finished before cancellation took effect
running at process start -> failed(error_code = interrupted)
canceling at process start -> canceled
retryable failed/canceled -> queued as the next attempt while attempts remain
```

Tasks record kind, course, subject, public phase and progress, payload, result,
attempt lineage, idempotency and active keys, worker identity, heartbeat, claim
token, safe error, and an append-only transition event stream. Claim,
progress, and terminal writes use compare-and-swap guards. A stale or
superseded claim cannot publish over a newer attempt.

The first worker is deliberately local and bounded: a SQLite queue plus a
small `ThreadPoolExecutor`. Redis, Celery, and distributed execution would add
deployment and failure modes without helping a single-user local desktop
application.

The task contract covers video processing, Source import/indexing, automatic
card generation, grounded Chat generation, and Study document generation.
Existing domain statuses remain during compatibility and are projections of
task progress, not an independent scheduler.

Cancellation is a durable intention. A queued task cancels immediately. A
running task changes to `canceling`, and the worker stops at the next safe
checkpoint. If the handler has already committed and returns normally,
completion wins and the retained cancellation timestamp explains that the
request arrived too late. The interface must never claim that a
non-interruptible Whisper or LLM call stopped before the worker confirms
`canceled`.

### Recover by policy, not by pretending every task can resume

On startup, a task that was `canceling` becomes `canceled`; a task left
`running` becomes a visible, retryable `failed` row with
`error_code = interrupted`. Retry keeps the same task identity, increments
its attempt and recovery lineage, and receives a new claim token. Completed
upstream artifacts may be reused, but the product does not call a
restart-from-zero operation "resume."

Video and Source index retain their existing domain checkpoints. Source
indexing keeps its generation-token fence. Chat keeps request idempotency and
turn-generation tokens. Automatic card generation checks cancellation between
chunks and adds a domain publication ledger: one immediate transaction commits
the chunk's cards, review items, and succeeded result. Retry reconstructs
progress from that ledger and skips every published chunk. One compare-and-swap
claim owns a run attempt, succeeded ledger rows are monotonic, and the final
transaction derives all counters, errors, and status from the selected ledger.
A failed or missing chunk therefore fails the reliable task and remains
retryable instead of being reported as partial success.

The durable reservation is the enqueue success boundary. Waking the in-process
executor after that commit is best-effort because the dispatcher can discover
the queued row. A failure before reservation closes any already-created domain
run or Source as visibly failed and preserves a user-visible retry path.

### Use device-first drafts with a revisioned workspace copy

Editable product surfaces use one versioned draft contract:

```text
entity type + entity id/draft id + course
base entity revision
draft revision
payload
updated time
```

Input is written to a small local recovery record first and debounced to the
local backend. Writes use optimistic revisions so an older response cannot
overwrite newer input. A successful domain save clears the recovery draft.

P0.5 integrates this contract first into the highest-loss surfaces: Chat
composer, Study document editor, both card editors, note/review forms, and
unsaved generated-card output. The reusable hook and registry are retained
for P1 Notes.

The user-facing states are `Saving`, `Saved`, `Saved on this device`, and
`Couldn't sync — changes remain on this device`. A browser/window leave
warning is registered only while data has not been persisted anywhere or a
local persistence write has failed. A draft that is safely stored locally
must not create permanent leave-warning fatigue.

### Delete into a recoverable trash journal

Normal delete means soft delete. User-facing root entities receive a
`deleted_at` tombstone and disappear from ordinary reads while their dependent
rows and managed files remain intact. A durable trash entry records type,
identity, course, label, deletion time, restore metadata, and purge state.

The first supported roots are courses, video jobs, document Sources,
knowledge cards, Study documents, and Chat conversations. Restore keeps the
same IDs, relationships, files, canonical Source projection, chunks, and
embeddings. Ordinary Source queries filter tombstoned roots; projection data
is physically removed only during permanent purge. Permanent purge is a
separate, explicit action available only from Data & recovery.

Database and filesystem changes cannot share one atomic transaction. The
trash row therefore acts as an intent journal. Ordinary deletion never
unlinks a managed source file; physical deletion occurs only during purge and
is retryable. File-backed video and document roots retain a versioned
projection/database/artifact phase plan until every managed file is gone.
Entity plans bind every relative file name to the deleted entity's ID and
course namespace. Course plans delete their verified artifact set while the
owning Job/Source rows still exist, then remove the database subtree; they are
strictly rebased during portable restore and compared with those owning rows
before deletion. Canonical naming is not treated as sufficient ownership:
portable restore rejects duplicate normalized managed paths, and permanent
purge scans every remaining Job and Source record immediately before unlinking
so another entity or course cannot retain a dangling file reference. Startup
transactionally releases interrupted `restoring` and `purging` claims to
retryable failure states without discarding phase metadata.

### Define three different backup concepts

The implementation keeps these separate:

1. **Migration backup** protects an automatic schema upgrade.
2. **User workspace backup** is a portable, validated snapshot.
3. **Pre-restore safety archive** is an additional snapshot made when an
   existing workspace is about to be replaced.

A workspace backup contains a SQLite-native online backup plus managed
uploads, audio, transcripts, and imported Sources. It excludes logs, model
caches, and reproducible exports. A versioned manifest records application,
format, and schema identity; database and file hashes/sizes; relative entry
paths; and archive entry/file counts. The archive is written to a temporary
path, atomically published, then reopened and independently validated; an
invalid final archive is removed.

Backup archives are untrusted input. Validation rejects path traversal,
absolute paths, symbolic links, oversized members/archives, duplicate paths,
hash or size mismatches, invalid SQLite integrity, and unknown future schema.

Restore is restart-bound:

```text
select archive
-> isolate and validate
-> queue a pending restore
-> restart the owned worker/backend
-> revalidate, extract, and rebase managed paths
-> create a pre-restore safety archive when a current database exists
-> checkpoint and isolate the live SQLite database family
-> write the receipt and swap the staged workspace before opening it
-> initialize, migrate, reconcile, and check the workspace
-> atomically publish a finalizing commit fence for the same restore identity
-> idempotently publish generation/result and remove the receipt last
```

The running FastAPI process never overwrites its own database. The external
result distinguishes `queued`, `staged`, `applied`, and `failed`, while the
internal receipt distinguishes `swapping`, `swapped`, `rolling_back`, and the
rollback-complete `rollback_finalizing` fence, plus the irreversible
`finalizing` commit fence; backend readiness alone does not imply restore
success. Before the commit fence, apply or initialization failure uses retained
pre-swap transaction paths to roll back. Once rollback succeeds,
`rollback_finalizing` makes the failed result and all cleanup receipt-driven
and restart-safe. After `finalizing`, restart may only complete
generation/result publication and receipt-last cleanup. The pre-restore safety
archive remains an additional manual recovery point, and a second queue request
cannot silently replace a pending restore.

### Harden the local database and desktop boundary

SQLite connections use a consistent busy timeout, foreign-key checking where
the legacy data permits it, and WAL-backed concurrency. The application schema
checkpoint is v7: v5 adds workspace drafts, durable tasks, tombstones, and
Trash; v6 separates `restore_failed` from `purge_failed`; v7 adds the
automatic-card per-chunk publication ledger. Schema evolution from this stage
onward is versioned in migrations; command-style `init_db` changes do not
silently bypass migration backups.

The backend health response carries exact application, protocol, and optional
instance identity. Tauri generates a UUID token for each spawn and retains the
child handle, PID, and token. Ready and quiesce parse JSON and require the same
token for an owned sidecar; substring matching is not an identity check.
Tauri may reuse a compatible external backend but may stop only the child
process it owns. It must never enumerate and kill an unknown process merely
because that process owns port 8001.

Shutdown first stops new dispatch, requests cooperative cancellation, and
waits for active handlers to reach a checkpoint. Only an idle worker returns
`quiesced`; a timeout is a forced-recovery condition, not a successful safe
shutdown. The host then terminates its owned child. Crash recovery remains the
correctness boundary; graceful shutdown reduces recovery work.

Data & recovery and task activity are global utilities in the existing shell.
They do not become a fourth primary destination. Sources / Chat / Studio and
their canonical route/course-isolation contracts remain unchanged.

## Technology choices

| Concern | Choice | Reason |
| --- | --- | --- |
| Durable state | SQLite schema and compare-and-swap writes | Already local, transactional, inspectable, and included in backups |
| Worker | Bounded Python thread executor over a durable queue | Fits one local process without a broker |
| Draft fallback | Versioned `localStorage` record | Survives a renderer exit when the backend is briefly unavailable |
| Backup | SQLite backup API + ZIP container + SHA-256 manifest | Produces a consistent DB and portable, independently verifiable archive |
| Restore publication | Staging and restart-time replacement | Avoids replacing an open SQLite database |
| Desktop ownership | Tauri child handle + identity handshake | Prevents killing unrelated processes |
| UI | React context/hooks plus a global utility drawer | Cross-route lifetime without adding a primary product area |

No remote service, account, analytics collector, or cloud storage is added.

## Alternatives considered

### Keep FastAPI `BackgroundTasks` and mark interrupted rows failed

Rejected. It provides a retry button but no durable queue, idempotent claim,
cancel intention, progress lineage, or protection from an old worker.

### Store drafts only in `localStorage`

Rejected as the sole source of truth. It cannot participate in workspace
backup/restore, conflict detection, cross-window inspection, or backend
maintenance. It remains a renderer-failure fallback.

### Autosave every keystroke directly into domain versions

Rejected. Study documents create immutable versions and card saves have
domain side effects. Draft persistence and domain commits have different
semantics; debounce and explicit publication preserve useful version history.

### Snapshot only the database

Rejected. The database contains paths to videos, transcripts, audio, and
imported files. A DB-only restore can pass `quick_check` while the actual
workspace is unusable.

### Restore inside a live API request

Rejected. Open SQLite and media handles make this unsafe, especially on
Windows, and a mid-request failure can publish a half-restored workspace.

### Immediately hard-delete and reconstruct from a serialized payload

Rejected. The entity graph is large and evolving. Keeping tombstoned rows and
files preserves IDs and relationships and makes undo substantially safer.

### Kill any process occupying port 8001

Rejected. Port ownership is not application identity. The desktop host owns a
specific child process, not an arbitrary machine resource.

## Consequences

Positive:

- in-progress work is visible after navigation and restart;
- cancellation and retry have honest, persisted semantics;
- a stale worker cannot overwrite a newer attempt;
- common editing and deletion mistakes are recoverable;
- backups prove both database integrity and managed-file integrity;
- restore is staged and rollback-oriented;
- the reliability model is reusable by P1 Notes and Studio outputs;
- reliability remains local-first and keeps the three-part product structure.

Costs and risks:

- domain status and generic task status coexist temporarily and must be kept
  consistent;
- non-interruptible model calls can delay confirmed cancellation;
- soft-delete filtering must be applied to every ordinary lookup;
- complete backups can be large and require free space for staging and a
  pre-restore snapshot;
- filesystem intent recovery is compensating, not a true cross-resource
  transaction;
- if both restore initialization and its transaction rollback fail, the
  receipt and pre-swap paths must be recovered manually;
- Tauri lifecycle behavior needs Windows-specific acceptance in addition to
  Python and React tests.

## Acceptance matrix

P0.5 is complete only when the following are demonstrated:

1. a Study/card/card-note/Chat draft survives refresh and application restart;
2. an older autosave response cannot overwrite a newer draft or another
   course;
3. leave warning appears only while persistence is genuinely pending/failed;
4. each supported long operation is represented by a durable task;
5. duplicate enqueue/claim does not start duplicate active work;
6. queued and running cancellation are persisted and reported honestly,
   including a late request that loses to already-published success;
7. failed/canceled/interrupted work can be retried with attempt lineage;
8. backend restart leaves no unowned permanent `running` task;
9. ordinary deletion hides an entity and offers server-backed Undo;
10. trash restore retains identity, relationships, files, and Source
    projection;
11. permanent purge is explicit, preserves a retry handle across file
    failures, recovers interrupted claims at startup, binds files to their
    entity/course namespace, refuses globally shared managed paths both during
    backup import and immediately before unlinking, and is tested separately;
12. a workspace archive validates DB integrity and every manifest hash;
13. tampered, traversing, oversized, or future-schema archives are rejected
    without changing current data;
14. restore creates an additional safety archive when replacing an existing
    workspace, isolates stale SQLite sidecars, and is applied only before DB
    startup; its write-ahead finalizing fence survives crashes before state,
    result, and every cleanup boundary without permitting rollback, while a
    separate rollback-finalizing fence survives failure-publication crashes
    without losing the original generation or result;
15. managed absolute paths are rebased after restore;
16. Tauri never kills an unknown port owner and stops only its owned sidecar;
17. Sources / Chat / Studio routes, citations, and course isolation regressions
    remain green;
18. backend, frontend, lint, build, dependency audit, database integrity, and
    desktop/narrow browser acceptance all pass before commit and push.
