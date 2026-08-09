# RAG Lab

This package contains controlled retrieval and grounded-answer experiments for
Video Course Cards. It is intentionally separate from `backend/app`:

```text
app     -> product APIs, SQLite workflows, and user-facing retrieval
rag_lab -> frozen corpora, benchmarks, baselines, metrics, and run artifacts
```

The first study compares BM25, dense retrieval, reciprocal-rank fusion, and
one-hop graph reranking under the same card budget. Answer generation is not
added until the retrieval benchmark is frozen.

Generated corpora, questions, embeddings, predictions, and logs belong under
`backend/data/rag_lab/` and remain ignored by Git. Compact protocols, hashes,
metrics, and limitations are tracked under `docs/experiments/`.

## Development Reproduction

Run commands from `backend/`.

```powershell
uv run python -B -m rag_lab.snapshot_corpus `
  --course-id uncategorized `
  --snapshot-id cs231n-lectures-1-5-v1 `
  --database data/jobs.db `
  --output data/rag_lab/r1/corpus-v1.json

uv run python -B -m rag_lab.author_benchmark `
  --corpus data/rag_lab/r1/corpus-v1.json `
  --seed ../docs/experiments/rag_r1_seed_v1.json `
  --output data/rag_lab/r1/benchmark-candidate-v1.json `
  --review-output data/rag_lab/r1/annotation-review-candidate-v1.json `
  --model qwen3:4b

uv run python -B -m rag_lab.revise_benchmark `
  --corpus data/rag_lab/r1/corpus-v1.json `
  --benchmark data/rag_lab/r1/benchmark-candidate-v1.json `
  --review data/rag_lab/r1/annotation-review-candidate-v1.json `
  --revisions ../docs/experiments/rag_r1_question_revisions_v2.json `
  --output data/rag_lab/r1/benchmark-candidate-v2.json `
  --review-output data/rag_lab/r1/annotation-review-candidate-v2.json

uv run python -B -m rag_lab.render_review_sheet `
  --corpus data/rag_lab/r1/corpus-v1.json `
  --benchmark data/rag_lab/r1/benchmark-candidate-v2.json `
  --output data/rag_lab/r1/human-review-sheet-v2.md `
  --quality-output data/rag_lab/r1/quality-report-v2.json

uv run python -B -m rag_lab.run_retrieval_experiment `
  --corpus data/rag_lab/r1/corpus-v1.json `
  --benchmark data/rag_lab/r1/benchmark-candidate-v2.json `
  --review data/rag_lab/r1/annotation-review-candidate-v2.json `
  --protocol ../docs/experiments/rag_r2_protocol_v3.json `
  --output-dir data/rag_lab/r2 `
  --split development
```

Record the new R2 run directory and the SHA-256 of its `retrieval_report.json`.
Create a new R3 protocol version from `docs/experiments/rag_r3_protocol_v4.json`
with that path and hash; never overwrite a completed protocol or result. Then
run grounded generation and the graph comparison:

```powershell
uv run python -B -m rag_lab.run_grounded_answer_experiment `
  --corpus data/rag_lab/r1/corpus-v1.json `
  --benchmark data/rag_lab/r1/benchmark-candidate-v2.json `
  --review data/rag_lab/r1/annotation-review-candidate-v2.json `
  --protocol ../docs/experiments/<NEW_R3_PROTOCOL>.json `
  --retrieval-run-dir data/rag_lab/r2/<R2_RUN_ID> `
  --output-dir data/rag_lab/r3

uv run python -B -m rag_lab.compare_graph_rag `
  --benchmark data/rag_lab/r1/benchmark-candidate-v2.json `
  --retrieval-run-dir data/rag_lab/r2/<R2_RUN_ID> `
  --answer-run-dir data/rag_lab/r3/<R3_RUN_ID> `
  --output data/rag_lab/r4/dense-vs-trusted-graph.json

uv run python -B -m rag_lab.run_graph_organization_audit `
  --corpus data/rag_lab/r1/corpus-v1.json `
  --review data/rag_lab/r1/annotation-review-candidate-v2.json `
  --embeddings data/rag_lab/r2/<R2_RUN_ID>/card_embeddings.json `
  --output ../docs/experiments/rag_graph_organization_audit_v1.json
```

The R3 run writes `answer_human_review.md` with randomized `Response A/B`
labels. Keep `answer_human_review_key.json` hidden from the reviewer until all
scores are frozen.

The runner blocks test access until the benchmark is sealed and the annotation
review is marked `human_verified`.

## Reasoning-Guided Controller Track

The controller track is a new experiment family. It reads the frozen R1/R2
artifacts but does not modify their schemas, hashes, protocols, or results.

Its implementation is separated into:

```text
controller_schemas.py             closed state/action/trace contracts
controller_memory.py              card-proxy and evidence memory adapter
controller_policy.py              fixed and public-cue evidence-gap baselines
controller_runner.py              action guard, pure reducer, budgets, replay
controller_metrics.py             retrieval, stopping, and cost metrics
controller_benchmark/             independent v2 annotation and seal contract
run_controller_experiment.py      frozen provenance, locked runs, and resume
```

The current executable verifier and answerer are deterministic smoke
components. Their outputs are always marked `paper_claim_eligible=false`; they
test the experiment plumbing and must not be reported as answer-quality
evidence.

Run a small development smoke experiment from `backend/`:

```powershell
uv run python -B -m rag_lab.run_controller_experiment `
  --corpus data/rag_lab/r1/corpus-v1.json `
  --benchmark data/rag_lab/r1/benchmark-candidate-v2.json `
  --review data/rag_lab/r1/annotation-review-candidate-v2.json `
  --embeddings data/rag_lab/r2/<R2_RUN_ID>/card_embeddings.json `
  --output-dir data/rag_lab/controller `
  --policy fixed_dense `
  --split development `
  --top-k 5 `
  --max-items 2
```

Replace `fixed_dense` with `fixed_dense_typed_graph` or `evidence_gap` to
exercise the other policy entry points. `evidence_gap` is still a deterministic
single-need baseline: public question cues select concept, prerequisite, or
relation need type; it is not the planned semantic need decomposer. A run
writes:

```text
resolved_protocol.json
memory_snapshot.json
episodes.jsonl
metrics.json
manifest.json
```

Resume without duplicating completed questions:

```powershell
uv run python -B -m rag_lab.run_controller_experiment `
  --corpus data/rag_lab/r1/corpus-v1.json `
  --benchmark data/rag_lab/r1/benchmark-candidate-v2.json `
  --review data/rag_lab/r1/annotation-review-candidate-v2.json `
  --embeddings data/rag_lab/r2/<R2_RUN_ID>/card_embeddings.json `
  --resume-run-dir data/rag_lab/controller/<CONTROLLER_RUN_ID> `
  --split development
```

The resolved protocol binds the exact local query-encoder files, corpus,
review, benchmark, embedding records, BM25 parameters, controller/evaluator
code, dependency lock, and runtime versions. State transitions and observation
costs are replayed before a trace is accepted. A sibling run lock prevents a
new run and a resume from writing the same directory concurrently; a
syntactically truncated final JSONL record is removed with its hash recorded,
while a complete but semantically invalid record fails closed.

The v2 benchmark evaluator is intentionally a development diagnostic. It
checks sealed annotation structure and verifies trace IDs and metadata against
the declared frozen memory, but it does not yet reconstruct that memory from
canonical source artifacts or replay the Dense/BM25 retrievers and runner.
Its report therefore fixes `paper_claim_eligible=false`, including for exact
match. Canonical source reconstruction and deterministic execution replay are
Phase 6 prerequisites, not current capabilities.

This smoke runner is development-only. It refuses the test split even after the
existing formal review gate; confirmatory access remains blocked until the v2
one-use access ledger and separated test-gold loader are implemented.

The retained historical findings and their limitations are summarized in
`docs/RAG retrieval and graph study.md` and
`docs/Graph as associative knowledge structure.md`. The controller smoke path
remains development-only and is not a current product milestone.
