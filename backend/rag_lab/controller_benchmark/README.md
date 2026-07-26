# Controller Benchmark v2

This package defines the independent evaluation contract for
reasoning-guided retrieval. It does not modify or replace the R1 benchmark.

## What This Benchmark Measures

The benchmark is designed to distinguish:

- retrieving concepts explicitly named in a question;
- discovering implicit bridge or prerequisite concepts;
- satisfying one or more evidence requirements;
- completing a typed, directed reasoning path;
- abstaining when the corpus is missing a bridge or sufficient evidence.

It does not prescribe one controller action sequence. Multiple evidence sets
and reasoning paths can be valid.

## Annotation Order

1. Freeze the course evidence catalog and concept registry.
2. Freeze the runtime graph without seeing benchmark questions.
3. Assign question families, source evidence bundles, and learning-objective
   clusters to one split before dataset authoring or annotation begins. The
   split-manifest timestamp must be strictly earlier than both.
4. Author questions from learning objectives and evidence bundles, not graph
   edge pairs or retriever failures.
5. Annotate required concepts, evidence alternatives, valid typed paths,
   modality, difficulty axes, hard negatives, and unanswerable certificates.
6. Obtain two independent field-level reviews per item.
7. Send every disagreement to an independent adjudicator.
8. Run dataset, split-leakage, graph-independence, and review audits.
9. Bind the accepted artifacts with a seal.
10. Only then run a preregistered confirmatory protocol.

Question authors, graph reviewers, benchmark reviewers, and adjudicators must
be declared in the independence manifest. The audit rejects overlapping roles
and rejects the runtime graph as a question-authoring input.

The dataset carries the full canonical concept registry, not only an external
hash. The audit recomputes that hash and requires every required concept,
reasoning-path endpoint, and closest-supported certificate concept to exist in
the frozen registry. Every valid path is role-ordered:
`anchor -> bridge/prerequisite ... -> target`.

## Evidence Semantics

All `EvidenceRequirement` objects are mandatory. Within one requirement,
`alternatives` are interchangeable. Within one alternative, every evidence
reference is required:

```text
requirement 1 AND requirement 2 AND ...

requirement 1:
  (evidence A AND evidence B)
  OR
  (evidence C)
```

This prevents an equivalent source from being scored as wrong merely because
it is not one exact evidence ID.

## Development-Diagnostic Binding

An `EvaluationRunBinding` contains the complete canonical
`ControllerMemorySnapshot`, in addition to its ID and hash. The protocol
memory hash and concept granularity must match that snapshot. The
graph-independence manifest's `runtime_graph_sha256` is recomputed from the
snapshot's exact typed, directed relations.

Before scoring, the evaluator revalidates the sealed dataset and memory, then
audits every trace state and observation against the frozen concept, evidence,
and relation maps. Invented IDs, conflicting evidence metadata, non-runtime
retrieval sources, invalid graph directions, and relation metadata not found
in memory are rejected rather than scored.

This is a declarative membership and metadata audit only. It does **not**
reconstruct controller memory from canonical corpus/source artifacts, replay
the frozen dense/BM25 retrievers and controller runner, or attest that recorded
scores, ordering, latency, and actions came from actual execution. A trace can
therefore use real catalog IDs while fabricating otherwise schema-valid
retrieval outcomes. Every current aggregate `ControllerMetricReport` and
single-item `ItemControllerMetrics` result is consequently fixed to:

```text
evaluation_scope = development_diagnostic
execution_provenance_status = declarative_membership_audit_only
paper_claim_eligible = false
answer_correctness_paper_eligible = false
retrieval_metrics_diagnostic_only = true  # item results
```

The evaluator code identifier is a composite hash over all Python modules in
this package plus the controller trace schemas and canonical hashing utility;
that hash improves diagnostic reproducibility but does not establish execution
provenance.

Test evaluation remains fail-closed until the one-use ledger and separated
test-gold loader exist. Formal or paper-eligible reporting additionally
requires canonical source reconstruction and deterministic retrieval/runner
replay.

## Pilot

The first artifact is development-only and contains 36 questions:

- 12 true multi-hop items;
- 12 implicit-prerequisite items;
- 12 structured unanswerable items.

Do not create a test split or make paper claims from this pilot. Before the
prerequisite slice is authored, independently review at least 12-20
directionally clear prerequisite relations.

## Modules

```text
schemas.py   closed Pydantic annotation/review/seal contracts
audit.py     canonical hashes, leakage, independence, review, and seal audits
metrics.py   exact concept, DNF evidence, and typed-path metrics
```

Large annotations and review records belong under ignored
`backend/data/rag_lab/controller_v2/`. Compact sealed summaries may be tracked
under `docs/experiments/` after the review is complete.
