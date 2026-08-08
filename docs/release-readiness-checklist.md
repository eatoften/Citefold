# Release and Resume Readiness Checklist

Last updated: 2026-08-08

Status: gate definition only; no gate in this document is currently marked as
passed.

## Purpose

This checklist turns the subjective phrases "critical hardening", "product
essentials", "release ready", and "reproducible MLE evidence" into reviewable
evidence. It expands the Resume Readiness Contract in the
[project mastery plan](project-mastery-plan.md); it does not replace the G0-G4
acceptance criteria in the [product roadmap](roadmap.md).

A checkbox is evidence, not intent. It may be checked only when the linked
artifact exists, the recorded command or journey has passed, and the exact
commit or release asset has been identified. Creating this document does not
advance product or mastery status.

The hard-gate boundary is:

```text
SDE flagship candidate         -> R1-R7 and R9 pass
applied-MLE flagship candidate -> R1-R9 pass
```

R8 is conditional on making an MLE or ML-systems claim. The optional additions
at the end are not silently promoted into blockers.

## Evidence Record

Every completed gate must append a row. Do not rewrite earlier failed runs.

| Date | Gate/item | Commit or release asset | Command/environment | Result artifact | Reviewer | Status |
| --- | --- | --- | --- | --- | --- | --- |

Allowed status values are `failed`, `blocked`, and `passed`. A local result
cannot establish a release-asset gate, and a passing development-server test
cannot establish a packaged-build gate.

## R4 Maintainability Freeze

Line-count reduction alone does not pass this gate. The goal is to make the
resume-defining call paths independently changeable and testable. Equivalent
file names are allowed only if an ADR or implementation-log entry preserves
the same responsibility boundaries.

### Frozen frontend boundary

- [ ] `frontend/src/App.tsx` owns application boot, selected-course context,
  canonical routing, global overlays, and feature composition. Network calls
  and domain-specific async state for Cards, Study, Review, Course Map,
  Explore, and the Concept graph live in feature-owned API/hooks/workspaces.
- [ ] UI components do not call raw `fetch` directly. An explicit allowlist is
  limited to backend bootstrap and feature API adapters, and a repository check
  fails when a new call appears outside it.
- [ ] Extracted API adapters share one typed JSON/error/abort contract rather
  than each inventing incompatible status parsing. Domain request and response
  types remain feature-owned.
- [ ] The Concept graph separates request/version orchestration from Overview,
  Local, Trace, Learning Path, review, and evidence presentation. Switching
  courses or graph versions cannot publish a late response into the active UI.
- [ ] Focused tests cover route ownership, request cancellation or epoch
  protection, typed errors, loading, empty, stale, unreachable, and retry
  states for the two flagship paths.

### Frozen backend boundary

- [ ] `backend/app/main.py` is the application/lifespan/router composition
  boundary for the resume-defining APIs. Sources, Chat, citations, Notes,
  reliability, and Concept-graph HTTP handlers are mounted from bounded API
  modules rather than implemented as new domain workflows in `main.py`.
- [ ] HTTP modules validate and translate transport contracts; services own
  lifecycle and transaction decisions; stores and migrations own SQL; graph
  algorithm modules own traversal. SQL and traversal do not leak into route
  handlers.
- [ ] Concept-graph service/store/algorithm tests can import their subject
  without importing the FastAPI application or starting model/media runtimes.
- [ ] One documented error taxonomy covers validation, not found, conflict,
  stale evidence/version, unavailable dependency, cancellation, and internal
  failure without returning local paths or private content accidentally.
- [ ] Remaining legacy concentration is listed with a named follow-up owner and
  is prevented from growing. No unresolved item may sit on either flagship
  journey or inside a release-blocking failure path.

### R4 acceptance evidence

- [ ] A before/after module diagram and responsibility table are checked in.
- [ ] An automated architecture check enforces the raw-request and layer
  allowlists described above.
- [ ] Backend tests, frontend tests/lint/build, Python compile/lock checks,
  Cargo fmt/check/test, and the two packaged-build journeys in this document
  pass on the same commit.
- [ ] The implementation log records what was deliberately not refactored and
  why it is outside the critical path.

## R5 Product-Finish Freeze

P1.2/P1.3 "essentials" mean the following bounded outcomes. Additional
NotebookLM-like generators are optional and cannot delay this gate merely to
increase the feature count.

### Required Studio and onboarding outcomes

- [ ] One Studio shell makes Cards, Notes, Study, Review, Course Map, and
  Explore/Concept Paths discoverable, preserves deep links, and exposes an
  honest empty state and next action. FAQ, Quiz, and new automatic output types
  are not R5 blockers.
- [ ] A first-time user can install the application, see FFmpeg/model/runtime
  readiness, load a license-clear sample course, observe processing state, and
  reach one recommended question without reading repository source code.
- [ ] The sample contains enough original, locatable material and a frozen
  Concept graph to run both flagship journeys without private local data.
- [ ] Missing model/runtime, malformed Source, canceled or failed task, stale
  graph evidence, unreachable path, and offline/local-file-change states tell
  the user what happened and provide a valid recovery or exit action.
- [ ] Source preview, processing progress, global or course-scoped discovery,
  narrow-screen layout, keyboard navigation, focus movement, and critical
  accessibility checks pass for the two flagship journeys.
- [ ] The public README leads with current product status, screenshots or a
  short demo, architecture, reproducible setup, measured results, and explicit
  limitations. Backend and frontend developer entry documents no longer remain
  empty or framework-template text.

### R5 acceptance evidence

- [ ] A new-profile production-build run completes both flagship journeys.
- [ ] A clean-install release run and an upgrade run from the last supported
  public schema preserve user data and complete both flagship journeys.
- [ ] A 3-5 minute recruiter demo uses only the documented sample and names the
  exact release version, model, corpus size, graph size, and known limits.
- [ ] Release notes enumerate shipped P0/P1/G stages and do not describe
  planned work as implemented.

## Required Packaged-Build Journeys

Both journeys are release gates. CI uses deterministic test adapters where
needed, but it must exercise the production frontend and packaged backend
artifacts. The clean-Windows run additionally exercises the documented real
local-model configuration.

### Journey A - Source to grounded citation

1. Start from a new workspace and import the license-clear sample Source.
2. Complete or resume indexing and select the Source in a new conversation.
3. Ask the registered answerable question and reach an answered terminal state
   with sentence-level citation IDs. Also run the registered unsupported
   question and reach the insufficient-evidence terminal state.
4. Open a citation through the server resolver and verify the expected video
   time, page, slide, paragraph, section, or immutable snapshot plus saved quote.
5. Restart the packaged application and verify that the conversation,
   selection, answer, and saved citation remain readable.

Required assertions include course isolation, no unhandled console/backend
error, no private filesystem path in the client contract, and exact expected
citation/locator IDs from the frozen fixture.

### Journey B - Concept path to edge evidence

1. Open the sample's published graph version and select the registered target
   Concepts and relation filters.
2. Run Local, A-to-B Trace, and prerequisite Learning Path requests. Repeat
   each request and verify identical ordered node/edge IDs and canonical hash.
3. Verify the registered unreachable case, bounded/truncated case, and stale
   evidence/version case produce their documented non-success states.
4. Open one node and every edge in the registered golden path, inspect its
   rationale/provenance, and resolve its original locator through the server.
5. Switch course or graph version during one delayed request and verify that
   the late response cannot replace the active result.

Required assertions include the exact graph version, accepted/current-only
membership, prerequisite ordering, locator IDs, keyboard reachability, and no
unhandled console/backend error at desktop and narrow widths.

### CI publication rule

- [ ] Pull requests and protected-branch pushes run backend tests, frontend
  tests/lint/build, Python compile/lock checks, Cargo fmt/check/test, and both
  production-artifact journeys without private assets.
- [ ] Required checks are branch-protection gates; a skipped, canceled, or
  allowed-to-fail job is not a pass.
- [ ] The workflow uploads the test reports, packaged artifact identity, and
  fixture/result hashes used by the journeys.

## Clean Windows GitHub-Release Acceptance

The tested installer must be the exact asset downloaded from a GitHub draft or
prerelease, not a separately built local executable. Promotion to a final
release occurs only after this section passes.

- [ ] Record the prerelease URL, tag/commit, filename, byte size, and SHA-256.
- [ ] Use a clean supported Windows VM or new Windows user profile with no
  repository checkout, prior application data, backend process, or model cache.
- [ ] Install only the documented prerequisites. First verify the missing-model
  and missing-runtime guidance, then configure the documented real local model.
- [ ] Download/import the license-clear sample and complete Journeys A and B
  against the installed release asset.
- [ ] Restart Windows or the application as registered and verify state,
  citation, task, graph-version, and managed-file recovery.
- [ ] Separately install the last supported public version with a fixture
  workspace, upgrade using the candidate asset, and verify schema/data
  preservation plus both journeys.
- [ ] Save the environment record, screenshots/video, logs with private paths
  redacted, and final acceptance result next to the release record.

Code signing and multiple desktop operating systems are useful release
improvements but are not hard blockers for the current Windows-only internship
portfolio. An unsigned installer must remain disclosed.

## R8 Reproducible Applied-MLE Evidence

This gate is required only when the resume claims applied MLE or ML-systems
evidence. Product completion alone does not pass it.

### Sealed evaluation

- [ ] The license-clear corpus, answerable/unanswerable queries, relevance and
  citation labels, graph judgments, annotation protocol, reviewer history,
  split manifest, and hashes are frozen before test results are viewed.
- [ ] Development data owns threshold, prompt, routing, and ablation choices.
  The sealed test is opened once by a runner that records the opening event;
  later changes create a new protocol rather than silently retuning the result.
- [ ] BM25, Dense, and the preregistered hybrid or graph-routing variant use
  the same corpus, filters, top-k, context budget, generation model, and timing
  policy except for the variable named by the ablation.
- [ ] The report includes Recall@k, MRR/nDCG, citation precision/recall,
  abstention measures, latency, uncertainty where valid, per-query outputs,
  failure taxonomy, negative results, and bounded claim language.

### Clean replay

- [ ] One documented command in a clean pinned environment rebuilds or fetches
  only license-clear inputs, runs the evaluation, and writes a manifest with
  code, environment, model, prompt, data, label, and result hashes.
- [ ] Deterministic IDs/rankings match exactly where promised. Floating metrics
  use a preregistered numeric tolerance; latency is reported as a new
  environment measurement rather than expected to hash identically.
- [ ] A second run from the published instructions reproduces the registered
  result within those rules, and its artifact is retained.

### Maintainer M3 evidence

- [ ] Without a generated solution, the maintainer implements or repairs one
  retrieval/evaluation slice, explains tokenization/chunking, embeddings,
  cosine/top-k behavior, leakage, ranking metrics, abstention, and one observed
  failure, and passes its focused tests.
- [ ] The maintainer completes one bounded PyTorch train/validation exercise or
  a substantive change to the existing reader experiment, explains tensor
  shapes, objective, optimizer, overfitting, and split discipline, and debugs
  one failed run. Merely rerunning a prepared script is not M3.
- [ ] The learning log links the maintainer-owned diff, tests, oral design
  defense, and error-analysis artifact. Codex-authored reports alone do not
  satisfy this gate.

## R9 Public-Release Security and Integrity

This is a scoped engineering gate, not a certification that the application is
secure against every adversary.

- [ ] Check in a threat/trust-boundary note covering the Tauri host, local HTTP
  sidecar, browser origins, untrusted Source files, model output, locator
  resolution, managed files, Trash, backup/import/restore, logs, and release
  artifact.
- [ ] The packaged backend binds only to loopback and accepts only registered
  production origins. Development origins are not enabled silently in the
  release configuration.
- [ ] Server-owned locator resolution, upload/import names, archive or backup
  entries, symlinks/reparse points, and managed-file deletion are canonicalized
  and constrained to their declared ownership roots. Traversal and arbitrary
  local-file tests fail closed.
- [ ] Malformed/oversized documents, hostile model strings, corrupt manifests,
  hash mismatch, schema mismatch, interrupted restore, and duplicate/replayed
  requests cannot partially publish trusted state.
- [ ] Logs, errors, demo fixtures, CI artifacts, and release bundles contain no
  secrets, raw private course data, authorization material, or unnecessary
  absolute user paths.
- [ ] High-severity dependency audit, secret scan, and the registered focused
  security/fault suites pass, or a dated waiver names the exact finding,
  exploitability assessment, mitigation, owner, and expiry. A blanket waiver
  is invalid.
- [ ] The release record binds the installer SHA-256, source commit, lockfiles,
  workflow run, test reports, and known security limitations.

## Additions, Not Hard Blockers

The following improve the portfolio but do not block an honest SDE or
applied-MLE flagship claim when unavailable:

- a partial second-human graph review beyond the registered solo two-pass
  protocol;
- observations from 3-5 real users, reported without inventing adoption or
  learning-effect claims;
- Windows code signing;
- macOS or Linux desktop packaging;
- cloud deployment, Kubernetes, microservices, Neo4j, or a distributed rewrite;
- stars, downloads, external contributors, or other popularity signals.

None may be claimed unless it actually occurred and has retained evidence.
