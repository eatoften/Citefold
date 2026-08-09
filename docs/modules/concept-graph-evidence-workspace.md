# Concept Graph Evidence Workspace

- **Status:** G4 user-visible vertical slice implemented; public-course quality,
  performance, accessibility, and durable browser-E2E acceptance remain open
- **Architecture owner:**
  [ADR-0008](../decisions/ADR-0008-evidence-grounded-concept-graph-and-deterministic-paths.md)
- **Frontend:** `frontend/src/features/concept-graph/`
- **Evidence resolver:** `backend/app/concept_graph_publication_store.py`,
  `backend/app/concept_graph_publication_service.py`,
  `backend/app/citation_target_service.py`, and `backend/app/main.py`

## User outcome and data flow

Studio `Explore` no longer opens the legacy CardRelation discovery graph. It
opens one published Concept Graph workspace where a user can inspect Concepts,
request deterministic paths, select an edge, and return to its original Source.

```text
selected course
-> active, Source-current GraphVersion + all published Concepts
-> Overview | Local | Trace | Learning
-> exact-version backend path API
-> server-ordered Concepts and Relations
-> relation inspector and immutable evidence snapshot
-> server-owned graph evidence target
-> existing CitationInspector
-> managed video/PDF/PPTX/document Source content
```

The path engine remains the authority for graph order and reachability. The
React client does not recompute BFS, shortest paths, prerequisite closure, or
topological layers.

## Product behavior

| View | Current behavior |
| --- | --- |
| Overview | Lists all Concepts in published server order and opens Concept evidence |
| Local | Requests the bounded two-hop, bidirectional neighborhood and shows distance plus induced Relations |
| Trace | Requests an outgoing shortest trace and distinguishes `found`, `unreachable`, and `limits_reached` |
| Learning | Requests the complete prerequisite closure and renders backend-owned topological layers |

Every returned Concept or Relation can be inspected. Relation inspection shows
its type, support basis, rationale, immutable quote, and an evidence button.
The workspace also distinguishes no course, no publication, an empty
publication, stale Source authority, request limits, and general failure.

## Evidence trust boundary

The browser carries only an identity tuple to a server route:

```text
course_id + graph_version + owner_kind + owner_id + evidence_id
```

The backend reloads that evidence from the immutable GraphVersion and verifies
the owner aggregate. It does not trust a browser-supplied asset path, Source
hash, Locator, quote, or currentness claim. The route is loopback-only and
course-scoped. The existing citation policy then validates projection
generation, Source root, Chunk hash, typed Locator, managed-file root, file
hash, no-follow opening, MIME type, and byte ranges.

```text
GET /courses/{course}/concept-graph/versions/{version}/
    {concepts|relations}/{owner}/evidence/{evidence}/target

GET|HEAD .../content
```

If the Source projection or managed file has drifted, `/target` preserves the
saved quotation with `snapshot_only`; `/content` fails with `409`. A malformed
current Locator is treated as drift rather than becoming an unhandled `500`.

## Frontend integration choices

- `CitationInspector` now consumes a small `SourceEvidenceSnapshot` plus an
  optional server path resolver. Chat still uses its original default route;
  graph evidence supplies the composite immutable-version route. This keeps
  one source-preview, focus-restoration, PDF/video, context, and degraded-state
  implementation.
- Graph and path requests use `AbortController` plus request epochs. Course
  changes, refreshes, and unmounts cancel both request families, so a late
  response cannot overwrite the visible course/version.
- The Concept Graph feature is lazy-loaded inside the existing Studio shell.
  It reuses the host course selector and heading hierarchy instead of adding a
  second application shell.
- No graph database, global state library, router replacement, or new frontend
  dependency was added.

## Findings corrected during browser acceptance

1. Path requests were not aborted when Explore unmounted. The graph effect now
   cancels the active path controller and invalidates its epoch during cleanup.
2. A corrupt live `locator_json` could escape as `500`. Current Locator parsing
   now fails closed, preserves the immutable snapshot, and refuses live content.
3. The first integration rendered duplicate page headings and course selectors.
   Ownership now stays in `StudioWorkspace`; the feature renders one nested
   `h2` and no second course selector.

## Verification and honest boundary

Local acceptance for this slice:

- `36 passed, 1 skipped` across the Concept path, publication, and citation
  target integration suites;
- all `210` frontend tests passed; ESLint, TypeScript, Vite production build,
  Python compileall, and `git diff --check` passed;
- a real browser journey over a seeded one-page PDF completed
  `Explore -> Trace (2 hops) -> relation -> evidence -> PDF page 1`, preserved
  the exact highlighted quote, closed with Escape, and restored focus;
- Local returned three Concepts and two Relations; Learning returned three
  layers and two prerequisite Relations; the clean cold load had one page
  heading, one course selector, and no console errors.

This proves a working evidence-backed path workspace. It does **not** yet prove
the full G4 quality claim. The following remain explicit work:

- an Obsidian-style force overview and richer stable path layout;
- candidate review/edit in this workspace;
- one durable automated browser E2E plus narrow-screen and accessibility gates;
- public-course human gold, path/Locator accuracy, and graph integrity reports;
- cold/warm 1k/10k performance and release acceptance.
