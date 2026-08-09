# Project Mastery Plan

Last updated: 2026-08-08

## Purpose

This document tracks two goals that must not be confused:

1. ship Video Course Cards as a credible, non-naive information-understanding
   product; and
2. make the maintainer capable of explaining, modifying, debugging, and
   eventually rebuilding its important systems without depending on generated
   code.

The first goal produces a portfolio. The second makes the portfolio defensible
in MLE and SDE internship interviews.

> A feature being implemented does not mean the owner has mastered it. Product
> status and mastery status are tracked independently.

This is a gate-driven plan, not a promise that one repository alone guarantees
an internship. Data structures, algorithms, operating systems, networking, and
general interview practice continue outside the repository.

## What "I Can Build This System" Means

The maintainer reaches project ownership when they can:

- draw the end-to-end architecture and explain why each stateful boundary exists;
- trace a request from React through FastAPI, service/store code, SQLite, local
  model/retrieval components, and back to the UI;
- change one vertical slice without guessing which layers must change;
- write the important tests and debug a failure that crosses two layers;
- explain data ownership, identity, versioning, trust boundaries, concurrency,
  recovery, performance, and failure states;
- implement the core graph algorithms and tests without copying project code;
- compare credible alternatives and defend the chosen trade-offs;
- state measured results and limitations without turning an experiment into a
  marketing claim.

It does **not** mean memorizing every one of the repository's tens of thousands
of lines or hand-writing every adapter and UI component from scratch.

## Market Calibration

Current official internship descriptions reinforce the same core profile:

- Google's Software Engineering Intern posting calls out programming,
  algorithms/data structures, software design, and areas such as distributed
  systems and concurrency, with reliability, performance, and debugging as
  valuable experience.
- Amazon SDE internship postings emphasize a programming language,
  object-oriented design, data structures and algorithms, project experience,
  databases, version control, cloud systems, debugging, and communication.
- Apple's ML/AI software internship asks for Python, ML fundamentals,
  PyTorch/training workflows, and evidence of experimental or research work.

Primary references, checked on 2026-08-08:

- [Google Software Engineering Intern, Summer 2027](https://www.google.com/about/careers/applications/jobs/results/120997883141857990-software-engineering-intern-summer-2027)
- [Amazon AWS Data Services SDE Intern, Fall 2026](https://amazon.jobs/en/jobs/10412530/software-development-engineer-intern-aws-data-services-fall-2026-us)
- [Amazon Shenzhen Seed Engineer / SDE Intern, 2026](https://www.amazon.jobs/pt/jobs/3159987/seed-engineer-program-software-development-engineer-intern-2026-shenzhen)
- [Apple Machine Learning Engineer Intern, Shanghai](https://jobs.apple.com/en-us/details/200609538/machine-learning-engineer-intern-shanghai)
- [Apple ML/AI Undergrad Internships](https://jobs.apple.com/en-us/details/200664780-3810/machine-learning-and-artificial-intelligence-undergrad-internships)

The practical conclusion is that this project should demonstrate both tracks:

```text
SDE credibility
-> contracts, data modeling, reliability, tests, debugging, performance

MLE credibility
-> retrieval, grounded generation, human-reviewed labels, evaluation,
   reproducible experiments, model/product boundaries

Shared proof
-> evidence-grounded Concept graph and deterministic path system
```

## Resume Readiness Contract

Completing the accepted sequence can make this a strong flagship project for
large-company SDE and applied-MLE internship applications. It cannot guarantee
an interview or offer. Hiring also depends on role fit, degree/location rules,
resume quality, application timing, coding interviews, CS fundamentals, and
the maintainer's ability to defend the work without assistance.

The project has three honest resume states:

| State | Allowed claim |
| --- | --- |
| Portfolio in progress | The Source-first notebook, Grounded Chat, citations, Notes, reliability foundation, G1 Concept-graph substrate, frozen G0.2 Source slice, G2.1-G2.3 annotation software, and G3 deterministic backend paths may be listed as implemented; no real human gold, G4 Path View/resolver, public-course path result, or accepted performance result exists |
| SDE flagship ready | The product, graph program, critical polish/hardening, release, measurements, and maintainer ownership gates below all pass |
| Applied-MLE flagship ready | The SDE flagship gates pass and MLE-E1 adds a frozen evaluation set, baselines, ablations, metrics, latency, and error analysis |

Research-heavy MLE roles may additionally expect graduate study, publications,
or deeper model-training research. This project is strongest for software,
ML-systems, retrieval/RAG, applied MLE, and AI product-engineering roles; it is
not a substitute for role-specific qualifications.

### Required flagship gates

| Gate | Required evidence |
| --- | --- |
| R1 differentiated product | Source -> Chat -> sentence citation and Source -> Concept -> relation/path -> edge evidence both work end to end; the graph is more than a force-layout similarity demo |
| R2 engineering correctness | additive migrations, course isolation, graph/version invariants, reliable tasks, recovery, deterministic algorithms, and focused fault tests pass |
| R3 measurable quality | frozen golden graph, locator validity, path correctness, graph coverage/error report, latency budget, and preserved Dense Chat baseline |
| R4 maintainability | the [frozen R4 checklist](release-readiness-checklist.md#r4-maintainability-freeze) passes: root composition, feature-owned state/API, backend layer boundaries, architecture checks, named residual debt, commands, and retained artifacts |
| R5 product finish | the [frozen R5 checklist](release-readiness-checklist.md#r5-product-finish-freeze) passes: bounded Studio essentials, onboarding/recovery, license-clear sample, recruiter-first documentation, clean install/upgrade, commands, and retained artifacts |
| R6 maintainer ownership | M3 on all five critical vertical slices, two or three M4 design defenses, user-owned diffs/commits, and reproducible bug/design stories |
| R7 interview readiness | separate DSA, OS, networking, database, behavioral, and timed coding preparation appropriate to the target role |
| R8 MLE evidence when claimed | the [R8 applied-MLE checklist](release-readiness-checklist.md#r8-reproducible-applied-mle-evidence) passes: independently frozen sealed labels, fair baselines/ablations, pinned clean replay, metrics/error analysis, and maintainer M3 on retrieval/evaluation/PyTorch |
| R9 security and release hygiene | the [R9 release-security checklist](release-readiness-checklist.md#r9-public-release-security-and-integrity) passes for untrusted imports, local HTTP/process/file access, model output, path/resource controls, private data, dependencies, and artifact provenance |

Passing R1-R7 and R9 makes the repository defensible as an SDE flagship. Passing
R1-R9 makes it defensible as an applied-MLE/ML-systems flagship. A recruiter
may see only a few lines and a short demo, so every resume sentence must map to
a public artifact, test, commit, measurement, or video timestamp in the demo.

Current checkpoint: the P0/P1.1 foundation is strong, G1 has an immutable
authority boundary, and G3 now has tested exact-version backend Local, Trace,
and Learning queries, but the final flagship gate is **not yet passed**. G0.2
has frozen and independently replayed the real 68-page CS336
Source slice. G2.1 implements the Concept annotation/sealing workflow and has
added a prior-commit reviewer-key trust root, but no real policy has been
registered and no authorized worksheet or human label exists. No Concept seal,
gold bundle, accuracy, or path result has been produced. The shared G2.2
evidence/privacy and four-stage attestation boundary is implemented, but
G2.3 now implements Relation Pass A schemas, exhaustive worksheet tooling,
private label sealing, a nonce-salted label-free public commitment, canonical
upstream replay, and failure-safe publication. No real Pass A
label or seal exists because the maintainer has not completed Concept or
Relation annotation. G2.4 delay/readiness tooling is explicitly deferred after
a product-priority correction. G3 does not yet have normalized cross-request
caching or accepted 1k/10k latency evidence. Human G2 work, G4 UI and graph-
evidence resolution, public-course evaluation, critical product finish, the
next public release, and maintainer mastery remain.
The visible maintainability baseline also includes a 5,169-line `App.tsx`, a
3,809-line `main.py`, a tag/manual-only Windows release workflow, an empty
backend README, and an unchanged Vite-template frontend README. These are
recorded debts, not hidden from the portfolio plan.

### Portfolio artifacts required before resume freeze

The feature stages alone are insufficient. Before calling the project a
finished flagship, also produce:

- PR/push CI for backend tests, frontend tests/lint/build, Python compile/lock
  checks, Cargo fmt/check/test, and both bounded resume-claim journeys on the
  release artifact: sample Source -> index -> Chat -> sentence citation -> exact
  locator, and sample Source -> Concept -> deterministic path -> per-edge
  evidence -> exact locator; the
  current GitHub workflow primarily builds a Windows release and is not yet a
  normal change-level quality gate;
- a graph performance report that separates materialization/sorting from
  traversal and records P50/P95 latency, query count, and memory on the frozen
  course plus registered synthetic scales such as 100/1,000/10,000 nodes;
- one license-clear sample course/evaluation corpus that another person can
  run without private local materials;
- a current architecture diagram, five vertical-slice diagrams, a 3-5 minute
  demo, and a recruiter-first README;
- a versioned golden graph, annotation guide, integrity/quality/error reports,
  and canonical artifact hashes;
- a desktop release containing the claimed P0/P1/G features and release notes,
  validated from the downloaded GitHub Release artifact in a clean Windows VM
  or user profile; record artifact hash and environment while checking initial
  setup, missing-model recovery, sample import, restart, and both resume paths;
- a resume-claim ledger mapping every sentence to code, test, metric, commit,
  or demo evidence;
- an explicit repository-license decision and repaired backend/frontend
  developer entry documentation;
- frozen P1.2/P1.3/P1.4 scope sheets that map every required journey or debt to
  an owner module, acceptance command, and retained artifact;
- for MLE claims, a sealed test manifest and label hash created before the final
  run, a prediction-blind delayed human review record, pinned
  dependency/model/runner identity, numeric tolerances, raw run outputs, and an
  error-analysis ledger; solo review is reported as temporal intra-rater, while
  inter-rater review is claimed only when a real second human participates;
- a lightweight security/release review covering import limits, path trust,
  local API/process boundaries, dependency/secrets checks, model-output
  validation, binary provenance, and the tests that enforce the chosen limits.

The exact hard checks, two packaged-build journeys, GitHub-Release
install/upgrade procedure, and evidence-record format are frozen in the
[release and resume readiness checklist](release-readiness-checklist.md). That
document is currently a contract, not evidence that any readiness gate passed.

A partial second-human graph review and observations from 3-5 real users are
strong additions, but they are not fabricated or treated as hard blockers when
external participants are unavailable.

## Mastery Levels

Every knowledge area and completed feature uses the same scale:

| Level | Meaning | Required evidence |
| --- | --- | --- |
| M0 | Not assessed | The code may exist, but ownership has not been demonstrated |
| M1 | Explain | Draw the data flow and explain responsibilities and one failure path |
| M2 | Modify | Complete a bounded change or test with guidance and explain the diff |
| M3 | Implement and debug | Independently implement a small vertical slice and diagnose a cross-layer fault |
| M4 | Design and defend | Compare alternatives, quantify trade-offs, and defend the design in a system-design interview |

Product stages use `Planned`, `In progress`, `Locally verified`, and
`Published`. Mastery uses M0-M4. Neither status implies the other.

## Knowledge Map

The target is not equal depth everywhere. M3 is the minimum for the project's
five interview-critical vertical slices; two or three role-aligned areas should
reach M4. M1/M2 is enough for peripheral tools.

The current runtime should first be learned as responsibilities, not as a list
of fashionable libraries:

| Layer | Main technology | Responsibility to understand |
| --- | --- | --- |
| Desktop host | Tauri 2 / Rust | package the application and own one local backend lifecycle |
| UI | React 19 / TypeScript / Vite | route, display, edit, and protect asynchronous user state |
| HTTP contract | FastAPI / Pydantic | validate course-scoped requests and return explicit typed failures |
| Domain workflows | Python services/stores | enforce lifecycle, provenance, idempotency, and graph rules |
| Source of truth | SQLite | persist transactional product state, migrations, indexes, and recovery metadata |
| Retrieval | sentence-transformers / BM25 experiments | rank locatable evidence and expose measurable diagnostics |
| Generation | Ollama-compatible local LLM | generate only inside bounded evidence and structured-output contracts |
| Media/document ingestion | FFmpeg, faster-whisper, document parsers | project raw local materials into canonical locatable Sources |
| Multimodal research | NumPy/Pillow, RapidOCR/ONNX Runtime, PyTorch | run isolated visual-reading experiments without turning them into product claims |
| Verification | pytest, Vitest, ESLint, build, Cargo tests, browser acceptance | turn invariants into repeatable delivery evidence |

### SDE foundation

| Area | What must be understood in this project | Minimum / stretch |
| --- | --- | --- |
| Git and delivery | branch/commit/merge, clean diffs, remote equality, rollback, CI/release gates | M3 / M4 |
| Python | types, dataclasses/Pydantic, exceptions, context managers, dependency boundaries | M3 / M4 |
| HTTP and REST | methods/status codes, validation, idempotency, pagination, error contracts | M3 / M4 |
| SQL and relational modeling | keys, constraints, indexes, joins, normalization, migrations, query plans | M3 / M4 |
| Transactions and concurrency | atomicity, compare-and-swap, locks, stale writes, crash boundaries | M3 / M4 |
| Layered backend design | route -> schema -> service -> store -> SQLite/model adapter | M3 / M4 |
| React and TypeScript | components, props/types, effects, reducers/state, async races, routing | M3 / M4 |
| Media ingestion | FFmpeg/ffprobe, ASR segmentation, timestamps, timeouts, partial artifacts | M2 / M3 |
| Document ingestion | PDF/PPTX/DOCX/text parsing, locator correctness, malformed-file failures | M2 / M3 |
| OCR/multimodal pipeline | detection/crops, RapidOCR baseline, CTC readers, dataset and artifact boundaries | M1 / M2 |
| Local model runtime | Ollama/Qwen availability, model identity, timeout/cancel, resource limits | M2 / M3 |
| Local desktop lifecycle | Tauri process ownership, startup/shutdown, filesystem boundaries | M2 / M3 |
| Packaging and release | locked builds, managed files, release artifacts, CI and rollback checks | M2 / M3 |
| Reliable background work | reservation, state machine, idempotency, retry, cancel, recovery | M3 / M4 |
| Testing | unit/integration/contract/UI/E2E boundaries, fixtures, fault injection | M3 / M4 |
| Performance and observability | latency budgets, indexes, profiling, structured failures, useful logs | M2 / M3 |
| Security and trust | course isolation, untrusted locators/paths, validation, local data exposure | M2 / M3 |

### MLE foundation

| Area | What must be understood in this project | Minimum / stretch |
| --- | --- | --- |
| Text representation | tokenization, chunking, embedding vectors, cosine similarity | M3 / M4 |
| Information retrieval | BM25 vs dense, top-k, reranking, filters, recall/ranking trade-offs | M3 / M4 |
| RAG | retrieval, context budgeting, grounded generation, citation alignment, abstention | M3 / M4 |
| LLM engineering | prompts, structured outputs, temperature, validation, retries, model limits | M2 / M3 |
| Human-in-the-loop ML | suggestion vs accepted truth, review lifecycle, provenance, label quality | M3 / M4 |
| Concept/relationship extraction | entity identity, aliases, relation types/directions, false positives | M2 / M3 |
| Evaluation | gold sets, splits, precision/recall/MRR/nDCG, confidence, leakage, error analysis | M3 / M4 |
| Experiment discipline | preregistration, artifact hashes, frozen tests, reproducibility, honest claims | M3 / M4 |
| PyTorch/ML fundamentals | tensors, loss, optimization, train/validation/test, overfitting | M3 / M4 for MLE; M1 / M2 for SDE |

### Algorithms and system-design intersection

| Area | Project use | Minimum / stretch |
| --- | --- | --- |
| Adjacency lists | build a course graph from accepted/current relation revisions | M3 / M4 |
| BFS | N-hop Local Graph and shortest unweighted relationship trace | M3 / M4 |
| DFS and cycle detection | validate accepted prerequisite edges | M3 / M4 |
| Kahn topological sort | prerequisite layers and stable presentation order | M3 / M4 |
| Complexity | separate graph materialization/sorting cost from `O(V + E)` traversal | M3 / M4 |
| Stable identity and versioning | Concept aliases, snapshots, graph versions, stale evidence | M3 / M4 |
| Cache invalidation | Source/Chunk changes and dependent embeddings/evidence | M2 / M3 |
| State machines | Chat, tasks, relation review, publication, recovery | M3 / M4 |
| Failure-domain design | keep model, database, filesystem, API, and UI failures explicit | M3 / M4 |

The graduation minimum is M3 on the five critical vertical slices, plus M3 on
the graph algorithms. M4 is a stretch target for two or three areas chosen to
match the role, not a claim that an internship candidate has mastered every
subfield at system-design depth.

### Required study outside this repository

The project creates examples, but these areas need separate structured study:

- data structures and algorithms: arrays, strings, hash maps, stacks/queues,
  linked lists, trees, heaps, graphs, sorting, binary search, recursion, dynamic
  programming, and complexity analysis;
- computer systems: processes/threads, memory, files, synchronization, deadlock,
  TCP/IP, DNS, HTTP/TLS, caching, load balancing, and database fundamentals;
- one interview language used fluently without AI assistance;
- MLE math: probability, statistics, linear algebra, optimization, common ML
  objectives, bias/variance, and offline/online evaluation;
- timed coding, behavioral stories, resume review, and mock interviews.

Kubernetes, microservice decomposition, distributed graph databases, training a
foundation model from scratch, and advanced GNN research are deliberately not
required for the current product. They are learned only if a measured need or
target role justifies them.

### Role-specific emphasis

For an **SDE internship**, the strongest project stories are API and schema
design, transactions, concurrency, state machines, recovery, React async
correctness, debugging, test strategy, performance, and clear trade-offs. The
separate DSA/CS-fundamentals track is non-negotiable.

For an **MLE internship**, the SDE foundation still matters. Add deeper
ownership of chunking and embeddings, dense/hybrid retrieval, grounded
generation and abstention, label quality, gold-set construction, ranking and
generation metrics, reproducible experiments, PyTorch fundamentals, and error
analysis. One rigorous negative result is more credible than several
unmeasured model demos.

For both roles, the Concept graph is valuable because it joins model-assisted
candidate generation with deterministic validation, relational storage, graph
algorithms, evidence UX, and measurable human review. The resume claim is the
system and its measured properties, not merely that it calls an LLM.

### Learning dependency order and project proofs

| Order | Learn | Project proof before moving on |
| --- | --- | --- |
| 1 | Git, Python/TypeScript reading, HTTP, SQL basics | trace one Source list request and explain its query/result types |
| 2 | layered backend, migrations, React async state | add one contract test and one bounded UI/API change |
| 3 | transactions, CAS, task state machines, recovery | inject a stale write or crash and explain why partial state cannot publish |
| 4 | chunking, embeddings, retrieval, Grounded Chat | reproduce a baseline query and explain one ranking/refusal error |
| 5 | Concept modeling, graph versions, BFS/DFS/Kahn | implement the graph slice and hand-write its core algorithms/tests |
| 6 | evaluation, performance, packaging/release | freeze artifacts, profile one path, run E2E, and build a releasable demo |

Media/document ingestion and local-model runtime are learned at M2 through two
fault exercises: diagnose one malformed/unsupported Source locator and one
missing-model or generation-timeout path. Desktop/release reaches M2 by tracing
backend ownership and producing one locked local build with recorded checks.

An adjustable weekly baseline keeps interview fundamentals from being crowded
out by product work:

```text
project delivery and ownership  5-7 hours
DSA and CS fundamentals         4-5 hours
MLE math/model/evaluation       3-4 hours for MLE targets
oral or timed mock              1 hour
```

Every two weeks, run one timed coding set, one oral system-design question, and
one reproduction or error-analysis exercise. Hours may change; all four proof
types should remain present.

## Current Product Reality

Seven product-core checkpoints are implemented, fully regressed, and merged to
`main`. They represent six runtime slices plus the P0.0 contract:

| Commit | Product checkpoint | What the maintainer must eventually own | Mastery |
| --- | --- | --- | --- |
| `9ae26ac` | P0.0 product contract | scope, ADRs, stage gates, truthful claims | M0 |
| `d4ab00c` | P0.1 unified Sources | canonical projection, additive migration, incremental indexing | M0 |
| `a27ec19` | P0.2 grounded Chat | multi-turn state machine, idempotency, bounded context, refusal | M0 |
| `f9085e0` | P0.3 citations | immutable snapshots, offsets, server trust boundary, degradation | M0 |
| `96c6110` | P0.4 workspace | route contract, async state isolation, Source-first information architecture | M0 |
| `7be0e1c` | P0.5 reliability | drafts, durable tasks, recovery, Trash, backup/restore, Tauri ownership | M0 |
| `eaf9274` | P1.1 Notes | optimistic revisions, immutable provenance, explicit Source publication | M0 |

The full local regression at `eaf9274` passed 681 backend tests with one skip,
214 frontend tests, lint, production build, Python compilation, lock checks,
Cargo formatting/check/tests, and the existing repository gates.

They are now integrated into local and remote `main` at `eaf9274`, but are not
in a new public desktop release. At the start of the 2026-08-08 G0 session,
GitHub connectivity failed during fetch, so the validated commit was first used
to create local branch `codex/concept-graph-foundation` without pretending that
a merge or push had succeeded. Connectivity later recovered; remote `main` was
confirmed to be the direct ancestor with divergence `0/7`, then fast-forwarded
and pushed. The graph documentation branch was also published without rewriting
its history.

The visible Explore graph is still a sparse CardRelation discovery prototype.
Behind it, G1 has implemented the Concept/Alias/Evidence/Relation lifecycle,
current-evidence enforcement, and immutable graph publication boundary.
G2.1's human-annotation handoff tooling is implemented, while real reviewer-key
registration and the authorized CS336 worksheet have not started. The shared
G2.2 evidence/privacy and detached-attestation primitives are implemented;
G2.3's Pass A commit--reveal software is implemented and covered by synthetic
plus real-capability integration tests,
but no real Pass A semantic artifact exists. G3's deterministic Local,
Relationship Trace, and prerequisite Learning Path backend is implemented over
one exact active/current published graph version. Automatic Understanding,
actual Concept/Relation gold, G4 Path View and evidence resolver, measured
performance, and public-course path quality remain planned or human-owned work.

## Delivery Sequence

The accepted sequence is:

```text
seven verified product-core commits integrated into main at eaf9274
-> establish the graph branch and G0 contract
-> G1 Concept / Evidence / Relation foundation
-> G2.1 tooling -> shared G2.2 security primitives -> G2.3 Pass A tooling
-> G3 BFS / relationship trace / prerequisite topological backend [implemented]
-> G4 stable Path View + server-owned per-edge evidence navigation
-> resume deferred G2.4 Git/readiness authority and maintainer-owned annotation
-> Pass A -> delay -> Pass B -> adjudication -> gold bundle
-> graph performance and public-course quality gate
-> resume Studio consolidation, product polish, hardening, and public release

G4 -> MLE-E1 reproducible portfolio evaluation for MLE-targeted applications
      (a mastery/evidence checkpoint that may run beside the product program)
```

[ADR-0008](decisions/ADR-0008-evidence-grounded-concept-graph-and-deterministic-paths.md)
owns the architecture and non-goals. The [roadmap](roadmap.md) owns stage
status. This document owns the learning contract. The
[technical-stack notebook](learning/README.md) owns review notes and session
evidence.

### Indicative schedule, not a deadline promise

| Period | Product focus | Maintainer proof |
| --- | --- | --- |
| Week 0 | G0 contract, baseline, annotation and quality gates | draw current vs target graph data flow; explain `Concept != Card != Topic` |
| Week 1 | G1 schema and stores | write one migration/constraint test and explain keys/indexes |
| Week 2 | G1 service/API and invalidation | trace one API call; fix one lifecycle or course-isolation case |
| Week 3 | G2 human Concept/Relation annotation and gold bundle | manually label and defend a Concept/edge/evidence slice; explain self-attestation limits |
| Week 4 | G3 BFS, cycle checks, topological path | hand-write algorithms and adversarial unit tests |
| Week 5 | G3 APIs and evidence DTOs | explain deterministic ordering and unreachable/error contracts |
| Week 6 | G4 Local/Trace/Path UI | fix one React async/layout issue and demo evidence navigation |
| Week 7 | G4 quality, E2E, performance | run error analysis, profile one path, defend measured limits |
| Week 8 | MLE-E1 evaluation when targeting MLE | reproduce baselines, one ablation, metrics, and error analysis |
| Week 9+ | P1.2-P1.4 and release | own one cross-stack polish/hardening slice; prepare interview package |

If a gate takes longer, the schedule moves. Passing evidence matters more than
calendar speed.

### MLE-E1 - Reproducible MLE portfolio evaluation

G0-G4 alone primarily demonstrates systems, IR, graph algorithms, and
human-in-the-loop product engineering. Before making strong MLE resume claims,
run a separate reproducible evaluation checkpoint:

- independently review or rebuild a frozen answerable/unanswerable query set;
- compare BM25, Dense, and a justified hybrid/graph-routing variant without
  forcing graph expansion onto every query;
- report Recall@k, MRR/nDCG, citation/abstention measures, latency, and confidence;
- preregister one or more ablations, preserve artifact/model/data hashes, and
  keep a sealed evaluation split;
- publish per-query outputs, failure taxonomy, and honest error analysis;
- state whether the graph helps a bounded task and where it hurts, without
  converting a development-set observation into a general claim.

Existing RAG research artifacts are useful baselines, but their candidate
labels do not become final portfolio evidence until the relevant review and
freeze gates are satisfied. MLE-E1 is a personal mastery/interview-evidence
checkpoint, not a product stage owned by `roadmap.md`. It is required before an
MLE-focused resume makes strong evaluation claims and does not block an
SDE-focused product release. Its sealed-test opening, clean replay, numeric
tolerances, and maintainer M3 proof must satisfy the
[R8 applied-MLE checklist](release-readiness-checklist.md#r8-reproducible-applied-mle-evidence);
a generated report or development-set table alone is not acceptance.

## Daily Collaboration Loop

A normal 60-120 minute learning session uses this loop:

1. Codex states the user outcome, current code path, data flow, risks, and exact
   acceptance conditions.
2. The maintainer predicts the affected layers and redraws or restates the
   flow in their own words.
3. The maintainer attempts one bounded 30-90 minute task before seeing the
   completed answer: a small feature, test, migration constraint, UI state, or
   real bug fix. Getting stuck is valid evidence and is recorded.
4. Codex reviews that attempt, explains the gap, and implements the remaining
   production slice, tests, and documentation.
5. The maintainer revises their change and explains the call chain, one failure
   path, complexity where relevant, and one trade-off.
6. Both run acceptance. Only then is the session record appended. Each
   rollback-safe logical slice receives a commit; the stage is complete only
   after all of its accepted commits are pushed and remote equality is checked.

Suggested time split:

```text
10 min  recap yesterday and state today's invariant
20 min  inspect one real call chain or data model
45 min  implement the bounded change
15 min  tests, debugging, and failure-path review
10 min  learning log and verbal explanation
```

The maintainer task must affect real project behavior or verification. Renaming
a variable or copying a prewritten solution does not demonstrate M2.

### Next three ownership sessions

This rolling section is updated after each accepted session.

#### Session 1 - Understand and operationalize G0

- **Lesson:** [Session 1: Source, Card, and graph contracts](learning/session-01-source-card-graph-contract.md).
- **Read first:** ADR-0008; `backend/app/course_source.py`;
  `backend/app/card_relation.py`; graph assembly in
  `backend/app/card_relation_service.py`.
- **Draw:** current `Source -> Card -> CardRelation -> Explore` beside target
  `Source revision -> ConceptEvidence -> reviewed Relation -> graph version -> path`.
- **Maintainer task:** after the annotation-protocol skeleton is created, add
  three accepted relation examples and one deliberately rejected ambiguous
  example to the [draft annotation protocol](graph-annotation-protocol.md), each
  with direction, support basis, evidence role, and rationale.
- **Acceptance:** `git diff --check`, local Markdown-link validation, and a
  line-by-line review of the examples.
- **Oral questions:** Why is a Card not a Concept? Why are proposal origin,
  support basis, review status, and evidence validity separate fields?
- **Budget:** 60-90 minutes.

#### Session 2 - Design the additive graph migration with TDD

- **Read first:** schema setup in `backend/app/db.py`; migration patterns in
  `backend/app/migrations.py`; `backend/app/card_relation_store.py`.
- **Draw:** the Concept/evidence/relation/version ER diagram, uniqueness keys,
  revision links, and the transaction boundary for prerequisite acceptance.
- **Maintainer task:** write the first failing migration/constraint test in
  `backend/tests/test_concept_graph_migration.py` before the migration exists.
  It must protect an existing Source/Card row and assert one graph invariant.
- **Acceptance:** from `backend`, run
  `uv run pytest -q tests/test_concept_graph_migration.py`, then the focused
  legacy migration/source/relation suites.
- **Oral questions:** Why additive migration? Which constraint belongs in SQL,
  which in a transaction, and which in the service layer?
- **Budget:** 90-120 minutes.

#### Session 3 - Implement and test one Concept vertical slice

- **Read first:** the new migration plus the existing schema/model/store/service
  separation used by Sources and CardRelations.
- **Draw:** `POST request -> Pydantic contract -> service invariant -> store
  transaction -> response`, including a cross-course failure.
- **Maintainer task:** independently add one course-isolation, duplicate-key,
  stale-revision, or missing-evidence test before Codex completes the service.
- **Acceptance:** run the focused Concept graph model/store/service/API suites,
  Python compilation, and `git diff --check`.
- **Oral questions:** What is the stable identity? What makes evidence current?
  Where can a concurrent write race, and how is conflict reported?
- **Budget:** 90-120 minutes.

### Weekly rhythm

| Day | Primary emphasis |
| --- | --- |
| Monday | contract, architecture, data-flow drawing, alternatives |
| Tuesday | schema, persistence, migration, constraints |
| Wednesday | service, API, retrieval, or algorithm |
| Thursday | React/TypeScript integration and async state |
| Friday | tests, faults, observability, performance |
| Saturday | end-to-end acceptance, documentation, integration/push, demo |
| Sunday | review, interview retelling, catch-up, or rest |

This rhythm is a default. A product stage may span several sessions.

## Stage Ownership Contract

Codex is responsible for the main implementation, tests, documentation, and
delivery checks. For each stage, the maintainer must produce three artifacts:

1. a data-flow, state-machine, or schema drawing;
2. a bounded code/test/bug-fix change they can explain line by line;
3. a short design defense covering one rejected alternative and one failure mode.

A stage can be `Published` while personal mastery is still below target, but it
cannot be marked mastered. Interview preparation revisits weak stages rather
than pretending completion.

When the maintainer's change is a coherent rollback-safe slice, it receives a
separate user-owned commit after review. Otherwise the session log records the
exact file/diff, the maintainer's first attempt, review corrections, and their
explanation inside the stage commit. Commit attribution is evidence, not a
substitute for being able to reproduce and defend the work.

## Five Interview-Critical Vertical Slices

These are learned deeply after the product is credible. They provide the
highest-value project stories and span both MLE and SDE work.

### 1. Source projection and incremental indexing

The maintainer must explain:

```text
video/document/note
-> canonical Source
-> locatable Chunk
-> content hash/version
-> embedding projection
-> incremental refresh and stale cleanup
```

Key topics: canonical identity, additive migration, hashing, indexes,
idempotency, cache invalidation, and why Card is not the original Source.

### 2. Grounded Chat and abstention

The maintainer must explain:

```text
conversation + selected Sources + history
-> retrieval
-> bounded evidence context
-> local model generation
-> claim/citation validation
-> answered or insufficient-evidence terminal state
```

Key topics: persistent state machine, retries, idempotency, context budgeting,
retrieval metrics, hallucination boundaries, and refusal evaluation.

### 3. Citation snapshots and trust boundary

The maintainer must explain why a saved citation needs quote/hash/locator and
server-owned resolution, what happens when a local file changes, and why the
frontend cannot navigate an arbitrary stored filesystem path.

### 4. Reliable local tasks and recovery

The maintainer must explain reservation, bounded workers, persisted progress,
cancel/retry/restart, atomic publication, Trash, backups, and Tauri sidecar
ownership using one explicit state machine and crash scenario.

### 5. Evidence-grounded Concept graph and deterministic paths

The maintainer must explain:

```text
Source Chunk
-> Concept evidence
-> reviewed typed relation
-> accepted versioned graph
-> BFS / prerequisite closure / topological order
-> stable path DTO
-> node/edge evidence navigation
```

Key topics: human-in-the-loop proposals, graph integrity, BFS/DFS/Kahn,
normalized-graph `O(V + E)` traversal, deterministic ties, stale evidence,
evaluation, and why an LLM does not directly author the authoritative path.

## Progress Matrices

### Product and mastery overview

Update this table only with evidence:

| Area | Product status | Mastery | Can explain | Can modify | Next proof |
| --- | --- | --- | --- | --- | --- |
| Product contract | Locally verified | M0 | Not assessed | Not assessed | explain ADR/stage gate and edit one acceptance criterion |
| Unified Sources | Locally verified | M0 | Not assessed | Not assessed | draw projection and add one parser/index test |
| Grounded Chat | Locally verified | M0 | Not assessed | Not assessed | trace state machine and fix one failure case |
| Citations | Locally verified | M0 | Not assessed | Not assessed | explain snapshot trust and add one degradation test |
| Workspace | Locally verified | M0 | Not assessed | Not assessed | trace route/async isolation and fix one UI state |
| Reliability | Locally verified | M0 | Not assessed | Not assessed | draw task state machine and inject one crash case |
| Notes | Locally verified | M0 | Not assessed | Not assessed | explain publication/versioning and add one conflict case |
| Concept graph G0 | In progress | M0 | Not assessed | Not assessed | restate ADR-0008 and review the first contract change |
| Concept graph G1-G4 | G1, G2.1-G2.3 software, and the G3 deterministic backend implemented; real human gold, G4 UI/resolver, performance acceptance, and public-course path results absent | M0 | Not assessed | Not assessed | draw the exact-version path flow, hand-simulate BFS/Kahn and fix one bounded-path test before returning to maintainer-owned gold |

### Daily checkpoint template

| Date | Stage | Product evidence | Maintainer-owned artifact | Tests run | Mastery before/after | Remaining confusion | Next step |
| --- | --- | --- | --- | --- | --- | --- | --- |

Do not rewrite old rows. Append one row per accepted work session so progress,
confusion, and ownership remain visible. The first row is added only after the
maintainer completes and explains Session 1; a Codex-authored document commit
alone does not create personal mastery evidence.

## Definition of Graduation

Before presenting the project as a primary internship project, the maintainer
should be able to complete all of the following:

- give accurate two-minute and ten-minute project explanations;
- draw the current architecture, Source-to-Chat-to-Citation flow, reliable-task
  state machine, and Graph-path flow from memory;
- implement BFS, prerequisite cycle detection, and stable topological sorting
  with tests without copying the repository implementation;
- complete a 30-minute bounded vertical feature and a 30-minute cross-stack bug
  diagnosis in the repository;
- defend at least five decisions: Source projection, Chat state, citation
  snapshots, task recovery, and evidence-grounded graph modeling;
- explain one failed/negative experiment and how it changed the product design;
- map every resume claim to a commit, test, measurement, screenshot/demo, or
  published artifact;
- run a reproducible demo and clearly state scale, latency, model, data, and
  known limitations;
- pass a separate DSA and CS-fundamentals interview plan appropriate to the
  target roles.

## Interview Evidence Package

The final repository and preparation folder should contain:

- one current architecture diagram and five vertical-slice diagrams;
- a concise README with an honest feature/status table and reproducible setup;
- ADR-0001 through ADR-0008 and the append-only implementation log;
- a versioned golden graph, annotation protocol, quality report, and error analysis;
- deterministic algorithm tests and one performance profile;
- one real-browser desktop/narrow-screen demo of Source -> Chat -> citation and
  Concept -> path -> edge evidence;
- clean GitHub-Release install/upgrade evidence, release hashes, and the scoped
  security review required by R9;
- known limitations and explicitly rejected claims;
- STAR stories for a hard bug, a design trade-off, an experiment that failed,
  a reliability improvement, and a user-facing improvement.

## Related Documents

- [Active product roadmap](roadmap.md)
- [Append-only productization log](productization-log.md)
- [Release and resume readiness checklist](release-readiness-checklist.md)
- [Technical-stack learning notebook](learning/README.md)
- [Session 1 lesson](learning/session-01-source-card-graph-contract.md)
- [G2 human-annotation handoff lesson](learning/g2-human-annotation-handoff.md)
- [G3 deterministic path engine](modules/concept-graph-path-engine.md)
- [G3 deterministic path learning handoff](learning/g3-deterministic-path-engine-handoff.md)
- [Draft graph annotation protocol](graph-annotation-protocol.md)
- [G2.1 human annotation workflow](modules/golden-graph-human-annotation-workflow.md)
- [Shared G2 annotation security primitives](modules/golden-graph-annotation-security-primitives.md)
- [Architecture decisions](decisions/)
- [ADR-0008: evidence-grounded Concept graph](decisions/ADR-0008-evidence-grounded-concept-graph-and-deterministic-paths.md)
- [Graph as associative knowledge structure](Graph%20as%20associative%20knowledge%20structure.md)
- [RAG retrieval and graph study](RAG%20retrieval%20and%20graph%20study.md)
- [Paused reasoning-guided retrieval research plan](Reasoning-guided%20adaptive%20retrieval%20research%20plan.md)
