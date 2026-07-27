# ADR-0004: Resolve Citation Navigation on the Server

- **Status:** Accepted
- **Date:** 2026-07-27
- **Decision owners:** Project maintainer and Codex implementation agent

## Context

P0.2 persists sentence-level citation snapshots containing the Source, chunk,
quote, text hash, score, and typed locator used to answer a question. The
frontend can display that evidence, but it cannot safely open the original
material. A browser-supplied locator or local path is not a trustworthy file
capability, and the existing video player depends on a temporary object URL
created only when the user selects a file in the current session.

Opening a citation also has two different truth conditions:

1. the immutable snapshot records what supported the answer at generation
   time;
2. the current local Source may still be exact, may have changed, or may have
   been removed.

Conflating these states would either turn old citations into dead links or
silently redirect them to evidence that no longer matches the answer.
Desktop-specific file opening does not solve the problem: a system PDF or
Office application cannot provide one portable, testable deep-link contract
across the browser and Tauri builds.

## Decision

### Citation ID is the navigation capability

The client opens a citation by sending only the current course ID and the
server-issued citation ID. It does not send a locator, Source ID, job ID,
asset ID, chunk ID, or filesystem path for the server to trust.

The server joins:

```text
citation
-> assistant message
-> conversation
-> course
-> persisted Source/chunk snapshot
-> current Source owner and managed file
```

An unknown citation and a citation from another course both return the same
not-found response. This avoids disclosing cross-course ownership and makes a
forged client locator irrelevant.

The resolver returns one of two user-facing states:

```text
available      current evidence and managed file are still verifiable
snapshot_only  the saved quote remains readable, but the current Source
               cannot be opened as the evidence used for the answer
```

The response never exposes `video_path`, `stored_path`, or any other absolute
local path.

### Immutable snapshot and current health are separate

The citation snapshot is never rewritten when a Source is disabled, changed,
re-indexed, moved, or deleted. A disabled Source remains openable for a
historical citation because disabling controls future retrieval, not past
evidence.

The current target is exact only when the server can still verify the Source
owner, active chunk ID, text hash, quote containment, Source type, and locator.
Missing Sources, missing files, path escapes, owner mismatches, changed chunks,
and unsupported locator versions become explicit `snapshot_only` reasons.
They do not become generic request failures and do not hide the saved quote.

Historical locator snapshots are read tolerantly. Newly generated citations
remain restricted to the supported version-1 locator models, while an unknown
future kind or schema version degrades only that citation to an unsupported
target instead of preventing the whole conversation from loading.

### Use one built-in Source inspector

The React application owns one modal Source inspector shared by Chat surfaces.
The citation control is a native button. Opening it shows the saved quote
immediately, then resolves and displays the current target:

- video and audio seek to the canonical start time in a controlled media
  player;
- PDF opens at the canonical page when the embedded viewer is available and
  always retains the extracted page text as a deterministic fallback;
- PPTX opens the canonical extracted slide;
- DOCX opens the canonical extracted paragraph;
- text and Markdown open the canonical extracted section.

PPTX and DOCX are not claimed to be pixel-perfect Office rendering. Their P0
contract is exact navigation to the normalized extracted unit used by
retrieval. The paragraph number is the ordinal among non-empty extracted DOCX
paragraphs. A text section is the version-1 extraction chunk, not necessarily
an author-defined heading.

The inspector supports keyboard activation, Escape to close, focus return to
the triggering citation, an announced loading/result state, and an inline
fallback that never removes the quote.

### Stream only managed citation content

Media bytes use a course- and citation-scoped endpoint. Every request resolves
the citation again and verifies that the owner belongs to the course, the
resolved file is a regular file inside the configured upload or Source root,
and the file still satisfies its recorded integrity metadata. Symbolic-link or
reparse-point escapes are rejected after path resolution.

Document assets use their persisted SHA-256 fingerprint. New video uploads
record SHA-256 during the existing copy pass. Legacy videos cross one explicit
upgrade trust boundary: immediately after schema initialization and before
Chat is served, startup backfill verifies the managed root, immutable stored
name, recorded size, regular-file identity, and stable digest before persisting
a fingerprint. A legacy row that cannot pass remains unfingerprinted and every
later citation read returns `legacy_fingerprint_unverified`; the read path
never establishes trust on first use.

Integrity validation and content delivery use the same no-follow opened
regular-file handle. Returning a validated path and reopening it in the
response would leave a path-replacement or symbolic-link race. Metadata-only
digest caching is also excluded: size, timestamps, and file identity do not
prove that bytes are unchanged on supported platforms.

The endpoint:

- is available only to loopback clients in the current local product;
- implements full, bounded, open-ended, and suffix single-byte ranges around
  the verified handle, including HEAD and unsatisfied-range responses;
- uses server-controlled media types instead of uploaded MIME claims;
- sends inline, `nosniff`, and private/no-store headers;
- revalidates the target rather than accepting a filesystem path from a
  previous resolver response;
- normalizes file lifecycle failures to path-free snapshot/conflict states
  instead of leaking an exception or local path.

### Keep the stage additive

P0.3 adds citation navigation without reorganizing the product navigation,
introducing the P0.5 task system, or removing path fields from legacy API
responses. The new viewer does not depend on those legacy fields. Removing
them requires a separately versioned compatibility decision.

## Alternatives considered

### Let the frontend open the persisted locator directly

Rejected. A locator contains useful identity but is still client-controlled
when sent back over HTTP. It would also force file ownership, current chunk
integrity, and deletion behavior into several UI branches.

### Expose `/files?path=...`

Rejected. Even a local-only application must not turn an arbitrary path into
a file server. Root checks after a client-supplied path are a weaker contract
than resolving an opaque citation through persisted ownership.

### Open the original file in the operating system

Rejected as the primary path. It requires desktop-only permissions, behaves
differently in the browser build, and cannot reliably move PowerPoint or Word
to a precise slide or paragraph. It may become an optional convenience later.

### Render every Office source pixel-perfectly

Rejected for P0.3. It would add a large conversion/runtime dependency before
the evidence navigation contract is proven. Extracted units are already the
canonical retrieval units and provide a deterministic exact target.

### Return an error when the current file is gone

Rejected. The persisted snapshot is still part of the historical answer.
Treating expected local-file lifecycle changes as a dead request would discard
the most important available evidence.

## Consequences

Positive:

- citation clicks work after application restart without reselecting a file;
- client code cannot use citation navigation to request an arbitrary path;
- every supported locator has one product-level behavior in browser and Tauri;
- changed or deleted Sources are visible rather than silently substituted;
- HTTP Range makes large video seeking practical;
- the viewer is reusable when Chat becomes a top-level P0.4 workspace.

Costs and risks:

- PDF rendering still depends on the embedded browser, so extracted page text
  remains the required fallback;
- slide and document previews preserve extracted text, not original layout;
- hashing legacy videos adds a one-time controlled-startup cost, and strong
  per-open integrity validation adds latency for large media;
- target resolution and content delivery currently validate independently;
  an immutable or content-addressed managed store is the preferred future
  optimization, not a metadata-only digest cache;
- the legacy API still exposes local path fields until a compatibility stage
  replaces those response models;
- loopback restriction is not a replacement for a future per-launch API token
  if remote binding is ever supported.

## Validation

This decision is successful when:

1. all five locator kinds resolve from a persisted citation ID without a
   client-supplied locator;
2. another course and an unknown citation are indistinguishable not-found
   cases;
3. video opens at the cited time after a frontend and backend restart;
4. PDF, PPTX, DOCX, and text open the exact normalized unit and highlight the
   saved quote;
5. disabled Sources remain openable, while changed chunks and files produce a
   visible snapshot-only state;
6. deleted Sources and missing files preserve the quote and do not create a
   dead citation control;
7. managed-root escapes and symbolic-link escapes cannot return bytes;
8. video content supports full, open-ended, suffix, and bounded byte ranges,
   and invalid ranges return the standard unsatisfied-range response;
9. content responses use controlled MIME, inline, `nosniff`, and no-store
   headers;
10. an unsupported locator snapshot does not prevent its conversation from
    loading;
11. Enter or Space opens the citation, Escape closes the inspector, and focus
    returns to the triggering button;
12. focused citation tests, the full backend suite, frontend tests, lint, and
    the production build pass before the stage is committed.
