# ADR-0005: Source-First Workspace and Canonical Route Contract

- **Status:** Accepted and verified in P0.4
- **Date:** 2026-07-27
- **Decision owners:** Project maintainer and Codex implementation agent

## Context

The application had accumulated five co-equal destinations—Workspace, Study,
Review, Course Map, and Explore—around features that were implemented at
different times. This exposed the internal history of the codebase rather than
the learner's workflow. Asking a question was also a tab inside the card rail,
while video ingestion and document upload appeared in different places.

P0.1-P0.3 established the missing product contracts underneath the interface:

```text
course
-> canonical Sources and locatable chunks
-> persistent source-scoped conversations
-> grounded answers
-> server-authoritative citation navigation
```

P0.4 must make that contract visible without discarding working card, Study,
Review, Course Map, or graph workflows. It must also preserve old deep links.
A label-only navigation change would leave URL state, course state, feature
ownership, and back/forward behavior ambiguous.

## Decision

### Use three primary destinations

Every course notebook has exactly three primary destinations:

```text
Sources  -> manage and inspect the evidence available to the course
Chat     -> ask multi-turn questions over an explicit evidence scope
Studio   -> create, edit, organize, and practise learning artifacts
```

`Sources`, `Chat`, and `Studio` are real links with canonical URLs, not local
tabs that disappear on refresh. The course remains the shared scope across all
three destinations.

The primary labels describe user intent:

- **Sources** answers “what material is in this notebook?”;
- **Chat** answers “what can I learn or verify from this material?”;
- **Studio** answers “what durable learning artifact or practice activity do I
  want to work on?”

### Keep current learning tools as Studio destinations

Studio has five secondary tools:

| Tool | Responsibility |
| --- | --- |
| `cards` | Create, edit, filter, and organize grounded knowledge cards |
| `study` | Build editable, versioned, source-backed learning documents |
| `review` | Practise due prompts through the FSRS workflow |
| `map` | Organize cards into a hierarchical course outline |
| `explore` | Inspect and curate lateral relations between concepts |

There is no placeholder Overview in P0.4. Existing, working capabilities are
grouped first; a later Studio output library belongs to P1.2.

### Make the URL the shareable navigation contract

The canonical query contract is:

| Destination | Required/owned query values | Optional entity values |
| --- | --- | --- |
| Sources | `view=sources`, `course` | `source`, `job` |
| Chat | `view=chat`, `course` | `conversation` |
| Studio | `view=studio`, `tool`, `course` | `card`; `document` only for Study |

Canonical Studio tools are:

```text
cards | study | review | map | explore
```

Entity parameters are destination-owned. Moving to another primary
destination removes irrelevant entity parameters rather than letting stale
card, document, conversation, Source, or job state leak into the new view.
Changing course clears entity selections unless the destination explicitly
provides a replacement in the new course.

Within Studio, `card` is supported by Cards, Study, Course Map, and Explore.
`document` is supported only by Study. Review intentionally has no card or
document deep-link contract in this stage. Query parameters not owned by the
application route contract are preserved.

Missing or invalid canonical values are repaired deterministically:

- an unknown primary view becomes Sources;
- a missing primary view becomes Sources unless a non-empty legacy `card`
  bookmark identifies the former Workspace card route, in which case it
  becomes Studio / Cards;
- Studio without a valid tool becomes Cards;
- empty IDs are removed;
- a missing or unknown course is replaced by the validated default/first
  course once the course catalog is available;
- a Source, job, card, document, or conversation is accepted only after the
  owning course's data has loaded, otherwise the stale entity parameter is
  removed with `replaceState`.

### Preserve legacy deep links through canonicalization

Legacy URLs map as follows:

| Legacy query | Canonical destination |
| --- | --- |
| `view=workspace` without `card` | `view=sources` |
| `view=workspace&card=...` | `view=studio&tool=cards&card=...` |
| `view=study` | `view=studio&tool=study` |
| `view=review` | `view=studio&tool=review` |
| `view=course-map` | `view=studio&tool=map` |
| `view=graph` | `view=studio&tool=explore` |
| missing `view` with non-empty `card` | `view=studio&tool=cards&card=...` |
| missing `view` without `card`, or unknown `view` | `view=sources` |

Startup and browser-history restoration parse both legacy and canonical URLs,
then replace a non-canonical URL in place. A legacy link therefore remains
useful without adding a duplicate history entry.

### Centralize browser-history writes

`features/navigation/appRoute.ts` is a pure module. It parses, normalizes,
serializes, canonicalizes, and builds URLs without changing browser history.
The application host is the only integrated writer:

- deliberate user navigation uses one `commitAppRoute` path and normally
  calls `pushState`;
- restoration or repair uses `replaceState`;
- navigation to the current canonical URL does not create a duplicate entry;
- `popstate` is parsed back into route state and, when needed, course state;
- feature slices emit navigation intent instead of writing history directly.

This separates three concerns that were previously interleaved: URL grammar,
history policy, and feature rendering.

### Treat course changes as isolation boundaries

Course selection is not a cosmetic filter. It is an ownership and concurrency
boundary. On a course change the host immediately clears course-scoped jobs,
cards, open card details, transcript state, and errors before loading the next
course.

Asynchronous feature slices use an abort controller, monotonic request
sequence, or request epoch as appropriate. A response is applied only when its
course and epoch still match the active workspace. Deep-linked entities are
validated against the current course after that course's collection has
loaded. A late response from course A must never overwrite course B.

The same rule applies to Sources, Chat, Study, Review, Course Map, Explore, the
legacy video workspace, and the card rail.

### Separate Source availability from conversation scope

`Source.enabled` is notebook-wide retrieval availability. It answers whether
a Source may participate in future retrieval at all. Sources owns that
lifecycle control together with import, indexing, deletion, and chunk
inspection.

`conversation.selected_source_ids` is a narrower, durable Chat choice. It
answers which enabled, ready Sources one conversation uses. Two conversations
in the same course may intentionally select different evidence sets. Changing
a conversation selection must not disable a Source globally; disabling a
Source globally makes it unavailable to every new retrieval scope while
preserving historical citation snapshots.

This distinction is exposed deliberately rather than collapsing both controls
into one checkbox with ambiguous consequences.

### Reuse the existing backend contracts

P0.4 is a frontend information-architecture and navigation stage. It reuses
the P0.1 Source APIs, P0.2 Chat APIs, P0.3 citation resolver, and existing
Cards/Study/Review/Map/Explore APIs.

It adds no SQLite migration, table, persisted enum, or backend response-model
change. This keeps navigation risk independent from storage risk and makes a
rollback possible without data conversion.

## Alternatives considered

### Rename the five existing sidebar items

Rejected. Labels would change while Ask remained card-scoped, Sources remained
split across upload surfaces, deep links remained feature-specific, and
back/forward behavior remained inconsistent. The result would look different
without becoming a coherent product.

### Keep all current tools as primary destinations

Rejected. It makes implementation modules compete with the core evidence loop
and gives a first-time user no clear starting sequence. The tools remain
available, but their product role is expressed through Studio.

### Add an empty Studio Overview immediately

Rejected. A placeholder would create a sixth workflow without a durable output
model. Cards is the useful default until P1.2 defines a real output library.

### Introduce a routing framework during P0.4

Rejected for this stage. The current application already uses query-based deep
links, and adding a routing dependency would combine an information-
architecture change with a broad host migration. A small typed, pure route
module provides deterministic parsing and testable compatibility now. A
framework may be reconsidered during P1.4 if feature extraction makes nested
routes materially valuable.

### Let each feature own URL and history state

Rejected. Independent `pushState` and `replaceState` calls produce dual truth,
duplicate entries, stale cross-view parameters, and course-switch races.
Features receive route values and return navigation events; the host owns the
commit policy.

### Treat global enabled Sources and Chat-selected Sources as one setting

Rejected. Global availability is a notebook policy; conversation selection is
an analytical scope. Combining them would let one conversation unexpectedly
change every other conversation and make a Source disappear from retrieval
without communicating that consequence.

### Remove legacy URLs

Rejected. Existing exported links, browser history, documentation, and user
bookmarks are part of the product's local data experience. Canonicalization is
small and testable, while breaking those links would provide no user benefit.

## Consequences

Positive:

- the first-level navigation now matches the evidence-to-answer-to-artifact
  product loop;
- Sources becomes the single visible catalog for videos and documents;
- Chat receives the full workspace, history, source picker, multi-turn state,
  abstention, and citations instead of living in a card-side tab;
- current learning capabilities remain reachable without pretending they are
  separate products;
- refresh, copied links, legacy links, and browser back/forward share one typed
  contract;
- invalid cross-course deep links fail closed and are repaired visibly;
- responsive navigation and one-main/one-heading semantics have an explicit
  shell rather than being reimplemented by every feature;
- no database migration is required.

Costs and risks:

- `App.tsx` remains the orchestration boundary and is still too large. P0.4
  extracts route and feature shells but does not complete the P1.4 feature-
  slice refactor;
- query routing is intentionally hand-written and must remain covered by a
  compatibility matrix;
- the existing video ingestion/card-generation implementation remains an
  on-demand compatibility workflow owned by the App host; it is hidden by
  default and opens from Sources only after **Add video** or a video/job
  selection, so Sources is unified at the product level before all underlying
  implementation paths are unified;
- Studio groups capabilities but is not yet a persistent output library;
- bundle-size growth, autosave, recoverable long-running tasks, backup/restore,
  Notes, onboarding, and full end-to-end automation remain later stages.

## Validation

This decision is successful when:

1. the primary navigation contains only Sources, Chat, and Studio;
2. Studio exposes Cards, Study, Review, Course Map, and Explore without losing
   existing workflows;
3. every canonical view survives refresh and copied-link navigation;
4. the complete legacy mapping replaces URLs without adding history entries;
5. deliberate navigation pushes one entry and back/forward restores both view
   and course;
6. route serialization removes destination-incompatible entity parameters;
7. invalid cross-course Source, job, card, document, and conversation links
   cannot display data from another course;
8. a late response from a previous course cannot overwrite the current
   course in every extracted workspace;
9. Sources shows mixed media and document Sources and can inspect canonical
   chunks and locators;
10. global Source availability and per-conversation Source selection remain
    independent;
11. every rendered destination has one main landmark, one route heading,
    keyboard-reachable navigation, visible focus, and no narrow-viewport
    horizontal overflow;
12. the focused route and feature suites, full frontend suite, frontend lint,
    production build, dependency audit, full backend suite, diff check, and
    manual desktop/narrow browser scenarios pass before P0.4 is committed and
    pushed.
