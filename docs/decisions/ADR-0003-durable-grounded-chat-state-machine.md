# ADR-0003: Persist Grounded Chat as a Recoverable State Machine

- **Status:** Accepted
- **Date:** 2026-07-27
- **Decision owners:** Project maintainer and Codex implementation agent

## Context

The original Ask surface returned semantically similar knowledge cards. It did
not generate an answer, remember earlier turns, preserve a conversation across
restart, distinguish missing evidence from model failure, or record citations
that could later open the original material.

P0.1 introduced canonical Sources and source chunks, but retrieval alone is
not a trustworthy chat product. A local model call can take long enough for a
request to be retried, the desktop window to change course, or the process to
restart. Sources can also be edited, disabled, moved, or deleted while an
answer is being generated. Treating one HTTP request as the durable unit would
therefore allow duplicate turns, indefinitely generating messages, or
citations to evidence that is no longer the evidence retrieved for that turn.

The application is currently a single-process, local-first desktop product.
SQLite remains the source of truth, while embedding and language-model
inference must happen outside long database transactions.

## Decision

### Durable conversation model

Grounded Chat uses five normalized SQLite tables:

1. `chat_conversations` stores course ownership, title, archive state, and the
   currently selected Source snapshot.
2. `chat_turns` stores the client request ID, selected Source snapshot,
   lifecycle state, generation token, retrieval query, and safe failure code.
3. `chat_messages` stores an immutable user message and a reserved assistant
   placeholder for every accepted turn.
4. `chat_citations` stores one immutable evidence snapshot per cited source
   chunk and assistant message.
5. `chat_citation_spans` maps that snapshot to every answer sentence it
   supports.

Migration history is forward-only. The development database had already
applied an earlier v2 before review added turn Source-scope provenance and
removed a redundant terminal state. Migration v3 therefore preserves and
rebuilds existing turns instead of changing the meaning of an already-recorded
version. It maps legacy `abstained` turns to `refused`, adds
`source_scope_mode`, and recreates the turn indexes.

A turn follows this explicit state machine:

```text
pending
-> retrieving
-> generating
-> validating
-> completed | refused | failed
```

`refused` means the selected Sources do not provide enough evidence. `failed`
means retrieval, generation, validation, or recovery did not complete. The two
states are intentionally different in storage, API behavior, and UI copy.

### Source scope and idempotency

- A new conversation snapshots all currently enabled Sources when
  `source_ids` is omitted. An explicit empty list remains an empty notebook.
- Every turn persists its own selected Source IDs. Later conversation changes
  do not retroactively change the evidence scope of an existing answer.
- `client_request_id` is unique within a conversation. Replaying the same
  payload returns the existing result; reusing the ID with a different payload
  is rejected.
- The browser retains the request ID while delivery is uncertain, so a lost
  response cannot silently create a second user turn.
- A partial unique index allows only one active turn per conversation.

### Transaction boundaries and recovery

Accepting a turn atomically reserves:

- the turn and its generation token;
- the complete user message;
- the generating assistant placeholder;
- monotonically increasing message sequence numbers.

Embedding and language-model work then runs outside a database transaction.
Every lifecycle transition uses the generation token as a compare-and-swap.
Final answer text, citation snapshots, citation spans, and terminal turn state
commit in one short transaction.

That final transaction revalidates:

- conversation and course ownership;
- the citation Source is in the turn's persisted Source allow-list;
- Source existence, enabled state, and course membership;
- chunk identity, active state, text hash, quote, and typed locator.

If any check changes, the answer is not published. A startup recovery pass
moves interrupted active turns to a safe failed state. Deleting or moving a
course explicitly moves its conversations and interrupts active turns rather
than relying on SQLite foreign-key behavior that the legacy database does not
globally enable.

Every pending file migration runs `PRAGMA quick_check`, creates and validates a
SQLite-native backup, and closes all backup connections explicitly. The latter
is required on Windows so a backup can be renamed, restored, or removed in the
same long-running process.

### Bounded multi-turn context

Conversation history is context, not evidence:

- retrieval uses the current question plus at most the two most recent user
  questions, bounded to 1,500 characters;
- generation uses at most six complete messages, bounded to 6,000 characters;
- evidence uses at most eight chunks, 3,000 characters per chunk and 16,000
  characters total;
- generation is capped at 2,048 output tokens.

Failed and generating assistant messages never enter generation history.
Assistant prose never enters the retrieval query. These limits keep local
latency and context growth predictable.

### Grounding contract

The server, not the model, assigns evidence labels such as `E1`. The model sees
only an untrusted JSON payload containing the question, bounded history, and
current evidence. It must return one of two strict JSON shapes:

```json
{"status":"answered","sentences":[
  {"text":"One supported sentence.","evidence_ids":["E1"]}
]}
```

```json
{"status":"insufficient_evidence","sentences":[]}
```

Every answer item must be exactly one sentence and cite at least one supplied
label. Unknown labels, extra fields, malformed JSON, multi-sentence items, and
uncited answer text fail validation. One constrained repair call is allowed;
a second invalid result becomes a safe infrastructure failure rather than
unverified prose.

The server replaces labels with immutable Source/chunk/quote/locator snapshots
and computes sentence offsets itself. History, source titles, source text, and
candidate model output are all treated as prompt-injection-capable untrusted
data. A deterministic refusal is returned without calling the model when no
Sources or no qualifying evidence exist.

### Product boundary

P0.2 exposes a reusable Chat feature slice inside the existing Ask rail:

- persistent conversation selection and creation;
- per-conversation Source selection;
- multi-turn messages and recommended starter questions;
- explicit generating, refused, and failed states;
- expandable sentence-level evidence previews.

The citation callback is deliberately a seam. Opening a video timestamp, PDF
page, slide, or document paragraph belongs to P0.3. Streaming, cancellation,
durable background execution, and user-triggered retry of a server-confirmed
failed attempt belong to P0.5.

## Alternatives considered

### Extend `/rag/retrieve`

Rejected. That endpoint returns derived cards and has no conversation,
idempotency, state-machine, refusal, or original-Source citation contract.
It remains compatible for existing consumers.

### Store each conversation as one JSON blob

Rejected. A blob makes atomic reservation, one-active-turn enforcement,
message ordering, citation reuse across sentence spans, recovery, and indexed
conversation listing unnecessarily fragile.

### Trust model-written citation metadata

Rejected. A model can invent IDs, alter quotes, or emit a plausible locator.
Only server-owned retrieval results may become persisted citations.

### Hold one transaction through retrieval and generation

Rejected. Local embedding and LLM inference can take seconds or minutes.
Holding a SQLite write lock across inference would block unrelated saves and
make desktop shutdown recovery worse.

### Add streaming and a task queue immediately

Rejected for this stage. Streaming would add another recovery protocol before
the durable terminal-state contract was proven. P0.2 first establishes the
state and idempotency semantics that later streaming and cancellation must
preserve.

### Resolve conversation Sources dynamically for every turn

Rejected. Historical answers need to state exactly which Source scope was used.
Dynamic resolution would also make a replay produce different evidence after
a selection change.

## Consequences

Positive:

- conversations, turns, failures, and citations survive application restart;
- retries cannot duplicate a turn when the browser reuses its request ID;
- every published factual sentence has at least one server-owned evidence
  snapshot;
- missing evidence is a normal, inspectable outcome rather than a model error;
- concurrent Source changes cannot publish stale or out-of-scope citations;
- the frontend slice can move into the future Chat workspace without rewriting
  its API and state model.

Costs and risks:

- the API is synchronous until P0.5, even though state is persisted;
- the current dense retrieval baseline has no lexical recall or reranker;
- citation coverage and allow-list validation do not prove semantic entailment;
- all-MiniLM-L6-v2 is effective on the current English CS231n corpus but weak
  for Chinese questions against English Sources;
- conversation detail currently loads all messages rather than paginating;
- the single-process compare-and-swap design will need broader coordination if
  the backend becomes multi-worker.

## Validation

This decision is successful when:

1. conversation history and terminal states survive a backend restart;
2. the same client request and payload cannot generate twice, including after
   a lost response;
3. a different payload cannot reuse an existing request ID;
4. only one active turn exists per conversation;
5. no reserved turn can remain active after a handled failure window;
6. startup recovery safely terminates interrupted work;
7. every answered sentence has a persisted citation span;
8. citation labels, Sources, chunks, hashes, quotes, and locators are
   revalidated inside the final transaction;
9. cross-course and out-of-turn-scope evidence cannot be persisted;
10. no evidence produces a refusal without an LLM call;
11. invalid structured output is repaired at most once and never falls back to
    uncited prose;
12. course switching and request cancellation cannot publish stale UI state;
13. the old card retrieval endpoint and all existing tests remain compatible.
