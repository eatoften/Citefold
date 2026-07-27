# ADR-0007: Notebook Notes and Explicit Source Promotion

- **Status:** Accepted and implemented in P1.1
- **Date:** 2026-07-27
- **Implementation date:** 2026-07-28
- **Decision owners:** Project maintainer and Codex implementation agent

## Context

P0 established a Source-first product with grounded multi-turn Chat, exact
citation navigation, a Sources / Chat / Studio information architecture, and
local workspace recovery. It still lacks the capture loop that makes a
notebook useful between questions:

```text
write an idea
-> save a useful Chat answer
-> revise and organize it
-> deliberately promote it to evidence
-> cite that evidence in later work
```

Existing `knowledge_card_notes` cannot provide this loop. They are children of
one generated card, have no course-level notebook identity, no optimistic
concurrency contract, no Chat provenance, no recoverable deletion, and no path
into the canonical Source index. Reusing them would preserve the old
card-first architecture inside the new Source-first product.

Official NotebookLM behavior establishes the useful product expectations:

- Notes live in Studio and can be written or pasted.
- A Chat response can be saved as a note while retaining its citations.
- Notes can be explicitly converted into Sources.
- Notes do not silently become retrieval evidence merely because they exist.

References:

- [Create or add notes in NotebookLM](https://support.google.com/notebooklm/answer/16262519)
- [Chat with your notebook in NotebookLM](https://support.google.com/notebooklm/answer/16179559)
- [Add or discover new sources for your notebook](https://support.google.com/notebooklm/answer/16215270)
- [NotebookLM FAQs](https://support.google.com/notebooklm/answer/16269187)

The project should reproduce that lifecycle, not every current NotebookLM
limitation. In particular, this local-first product already has Trash and
backup recovery, and should let a user edit the working copy of a saved answer
without erasing its immutable provenance.

## Decision

### 1. Notes are a course-level notebook entity

Add a first-class `notebook_notes` aggregate. A note belongs directly to one
course and is independent of cards and Study documents. Its working title and
Markdown body are editable. Each durable edit increments a monotonic revision,
and updates use compare-and-swap against the revision the editor loaded.

Two origins are supported in P1.1:

- `free`: written or pasted by the user;
- `chat_answer`: created from one completed, grounded assistant message.

The existing card-note feature remains intact for card-specific annotations.
Automatic migration between the two concepts is out of scope.

### 2. A saved Chat answer has immutable provenance and an editable copy

Saving a grounded answer records:

- the originating conversation and assistant message IDs;
- the original answer text;
- provider/model metadata;
- the complete citation snapshot, including sentence offsets, source and
  chunk identities, quote, score, content hash, and canonical locator.

The note body starts as the answer text and may then be edited. Editing never
mutates the origin snapshot. The immutable snapshot answers “what did the
assistant actually say and cite?” while the working body answers “how has the
user organized this knowledge?”

The citations are copied into note-owned, normalized
`notebook_note_citations` rows with new stable IDs; they are not merely foreign
keys to `chat_citations` and not only an opaque JSON blob. The shared citation
target reader resolves both Chat-owned and note-owned snapshots. Therefore a
note citation remains independently verifiable—or degrades explicitly to its
saved quotation—after the originating conversation is permanently purged.

One assistant message maps idempotently to one note. A repeated save after a
lost response returns the same active note instead of duplicating it.

### 3. Notes are not evidence until explicitly promoted

Creating or editing a note does not add it to Chat retrieval. The user must
choose **Publish as source**. Promotion records an immutable
`notebook_note_source_snapshots` row for the exact note revision and updates a
stable canonical Source:

```text
Source id       note:<note_id>
Source origin   notebook_note / <note_id>
Source type     text
Chunk origin    notebook_note_snapshot / <snapshot_id>
Locator         note_section(note_id, snapshot_id, section_number)
```

Repeating promotion for the same note revision is idempotent. Promoting a
later revision appends a new immutable snapshot and deliberately refreshes the
stable Source projection. Existing Chat citations remain historical snapshots
and retain their quote, hash, locator, and source title.

The promoted content is split into bounded Markdown sections so retrieval and
citations target useful excerpts rather than an entire long note. Chat's
existing search path indexes changed chunks on demand; no second retrieval
engine or note-specific embedding store is introduced.

### 4. Deletion hides the note-derived Source but preserves recoverability

Deleting a note is a soft delete in the same transaction as a
`notebook_note` Trash tombstone. The derived projection and immutable source
snapshots remain until permanent purge, preserving restore identity and
historical citation data. Active Source reads reject a note projection while
its note or course is tombstoned. Restore re-exposes the same note and Source;
permanent purge removes its Source chunks/embeddings, snapshots, note, drafts,
and tombstone.

Course purge treats notes as part of the course knowledge subtree and removes
them idempotently before deleting the final course record.

### 5. Notes stay inside the three-part information architecture

`notes` becomes a Studio tool, with canonical URLs:

```text
?view=studio&tool=notes&course=<course_id>
?view=studio&tool=notes&course=<course_id>&note=<note_id>
```

The P1.1 UI provides:

- a course-scoped note list;
- a free-note editor protected by the P0.5 draft layer;
- save, delete, restore-via-Trash, and publish/update-Source actions;
- an immutable origin-and-citations panel for Chat-derived notes;
- a **Save to notes** action on completed grounded Chat answers;
- a direct path from the saved-answer action to the selected Studio note.

Notes do not become a fourth primary navigation destination.

## Alternatives considered

### Reuse `knowledge_card_notes`

Rejected because it would make a notebook note depend on a generated card,
retain hard deletion, and force Chat provenance and Source publication into a
model built for small card annotations.

### Treat every note as a live Source

Rejected because private working thoughts would silently influence answers,
every keystroke could invalidate embeddings, and historical evidence could
change without an explicit user action.

### Copy a note into a `.md` Source asset

Rejected because a synthetic managed file adds filesystem cleanup, duplicate
truth, and parser work without improving the local SQLite notebook model.

### Make saved Chat answers immutable

Rejected as the only representation. Immutability is valuable for provenance,
but users need an editable synthesis. Keeping both an immutable origin
snapshot and an editable working body provides both guarantees.

### Create a new Notes primary view

Rejected because the accepted Source-first structure intentionally has only
Sources, Chat, and Studio. Capture and authoring belong in Studio.

## Implementation refinements

Implementation preserved the decision above and added the following concrete
contracts where integration testing exposed ambiguity:

1. Schema v8 uses four normalized tables: `notebook_notes`,
   `notebook_note_citations`, `notebook_note_citation_spans`, and
   `notebook_note_source_snapshots`. Seven course-scoped endpoints cover note
   list/create/read/update/delete, grounded-answer capture, and publication.
   Course ownership is checked at every boundary, and note updates compare the
   caller's expected revision with the current durable revision.
2. Captured citations and their sentence spans receive note-owned identities.
   Citation-target resolution reconstructs historical note-snapshot context
   from those rows, so purging the source conversation cannot remove the
   evidence contract of a saved answer.
3. The stable Source projection is derived from an immutable snapshot, not the
   mutable working row. Markdown is split deterministically into sections
   bounded at 4,000 characters and located by `note_section`. Publish,
   reconciliation, restore, and purge share one lifecycle lock so a concurrent
   reconciler cannot remove a projection while it is being replaced. Promotion
   rereads the canonical durable note before publishing rather than trusting a
   stale request payload.
4. Deletion and recovery reuse the P0.5 workspace lifecycle. Soft deletion
   hides both Note and Source; Undo/Recovery restores the same identities;
   purge removes citations, spans, snapshots, chunks, embeddings, drafts, and
   tombstone; validated workspace backup/restore round-trips the entire
   lineage.
5. Draft recovery is both device-local and revision-aware. Hydration is safe
   under React Strict Mode, recovery distinguishes the editor's base revision,
   workspace writes use compare-and-swap, observed absence uses create-only
   revision `0`, and cleanup never unconditionally deletes an unseen draft.
   A conflict remains explicit rather than silently overwriting another
   editor. Navigation guards cover note, conversation, Source, Study, and
   application route changes; Chat composer drafts are keyed by course and
   conversation so a late request cannot clear or populate a different scope.

## Consequences

Positive:

- closes the first complete capture-to-retrieval loop;
- preserves evidence lineage while allowing user synthesis;
- reuses canonical Sources, search, citations, drafts, Trash, backups, and
  lifecycle locking;
- provides a strong portfolio story around explicit knowledge provenance;
- improves on NotebookLM's current deleted-note recovery limitation.

Costs:

- schema version advances from v7 to v8;
- canonical Source reconciliation must include published note snapshots or it
  would delete note-derived projections;
- Source and locator unions gain one origin and one locator variant;
- optimistic note edits need an explicit conflict response and UI recovery;
- `App.tsx` receives only route wiring; the feature itself must stay in a
  separate slice so P1.1 does not worsen the P1.4 refactor debt.

## Invariants

1. A note is visible only while both the note and its course are active.
2. A note edit succeeds only from the expected revision.
3. A Chat answer save accepts only a complete, answered assistant message with
   at least one valid citation and an active conversation/course.
4. Chat provenance is immutable after note creation.
5. Note-owned citations remain readable after the originating Chat is purged.
6. A note is absent from Source resolution until its first explicit promotion.
7. Promotion snapshots exactly one durable note revision.
8. Repeating the same message save or same-revision promotion is idempotent.
9. A tombstoned note-derived Source cannot be selected or searched.
10. Restore preserves note, Source, snapshot, chunk, and citation identities.
11. Permanent purge cannot leave a selectable Source or its embeddings.

## Verification

The acceptance checklist was completed on 2026-07-28:

- migration and clean-install schema tests;
- note create/list/get/update conflict and course-isolation tests;
- Chat-answer validation, independent citation-snapshot, post-Chat-purge
  citation resolution, and idempotency tests;
- publish/re-publish/reconcile/search/source-scope tests;
- soft-delete/restore/purge/course-purge and draft-cleanup tests;
- API contract tests for all note endpoints;
- route canonicalization and Studio tool tests;
- editor draft recovery and stale-response isolation tests;
- Chat save-to-note and citation-origin UI tests;
- complete backend and frontend suites, lint, production build, and repository
  hygiene checks;
- desktop and narrow-browser journeys covering free note, saved answer,
  promotion, Source selection, later grounded citation, deletion, and restore.

The final automated evidence is:

- full backend: `681 passed, 1 skipped, 1 warning`;
- frontend: `214 passed` across 27 test files;
- Python bytecode compilation and uv lock validation;
- frontend ESLint and TypeScript/Vite production build;
- Cargo formatting, locked metadata, locked check, and 6 locked tests;
- high-severity npm audit: zero vulnerabilities.

The production build retains one non-blocking optimization warning for the
`588.23 kB` minified main JavaScript chunk. Real-browser acceptance completed
the full create -> publish -> index -> Chat -> citation -> save-to-Note ->
delete -> Undo/Recovery-restore journey at desktop and narrow widths with no
horizontal overflow or console errors.
