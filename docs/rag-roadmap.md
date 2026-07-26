# RAG Research Roadmap

Last updated: 2026-07-25

## Objective

Build a measured card-level RAG baseline before introducing learned graph
routing or agentic policies. Retrieval, grounding, refusal, and downstream
answer quality must be evaluated separately.

## Completed Development Work

### R0: Product Dense Retrieval

- Card embeddings in SQLite
- Query embedding with local MiniLM
- Cosine top-k retrieval API
- Frontend Ask tab showing retrieved cards

### R1: Candidate Evaluation Dataset

- Frozen 118-card corpus with claim/evidence/timestamp provenance
- 100 questions: factual, concept, comparison, multi-hop, unanswerable
- 40-question development and unopened 60-question test split
- Structural and wording-quality audit
- Human review sheet

Status: **candidate only; independent review pending**.

### R2: Retrieval Baselines

- BM25
- Dense MiniLM retrieval
- BM25 + dense reciprocal-rank fusion
- Dense + noisy one-hop graph
- Dense + candidate reviewed one-hop graph
- Recall@1/3/5, MRR, nDCG, latency, refusal calibration
- Paired bootstrap confidence intervals

Development finding: Dense is the strongest default. Graph expansion improves
one small multi-hop retrieval slice but damages overall and single-card ranking.

### R3: Grounded Generation

- Fixed Qwen model, prompt, top-k, and character budget
- Claim-only structured generation
- Exact card/claim/evidence citations
- Dense-anchor confidence gate
- Resumable JSONL experiment artifacts
- Human answer-review sheet

Development finding: prompt-only refusal fails; calibrated pre-generation gating
is necessary.

### R4: Graph RAG Comparison

- Same top-5 and generation budget for Dense and Graph
- Per-question win/tie/loss analysis
- Bootstrap comparison of citation metrics

Development finding: one graph win, 39 ties, and no multi-hop generation gain.

See `docs/RAG retrieval and graph study.md` for methods and results.

### Controller Foundation: New Versioned Track

The repository now has an independent controller experiment foundation:

- closed, discriminated concept/evidence/graph/verify/answer/abstain actions;
- structured evidence-gap state without unrestricted chain-of-thought;
- frozen card-proxy concepts, claim evidence nodes, and directed typed edges;
- fixed Dense, fixed Dense-plus-typed-graph, and rule evidence-gap policies;
- replayable hashed traces, per-action budgets, duplicate/loop guards, and
  resumable JSONL runs;
- controller-specific recall, stopping, path, and cost metrics;
- an independent benchmark v2 contract with evidence alternatives, typed
  paths, graph-authoring independence, double review, adjudication, and seal
  audits.

Three two-question development smoke runs exercise every policy entry point.
The typed-graph baseline always expands the graph; the evidence-gap baseline
does so only when its public-text need heuristic classifies a question as
prerequisite or relational. They are plumbing checks only: the old benchmark
and graph are candidate annotations, and the deterministic verifier/answerer
is not a semantic answer model. Every artifact is therefore marked
`paper_claim_eligible=false`.

The v2 evaluator is also development-diagnostic only. It audits the declared
memory membership and trace metadata, but formal use remains blocked until the
canonical source artifacts can reconstruct the memory and the exact
retriever/runner execution can be replayed.

See `docs/Reasoning-guided adaptive retrieval research plan.md` for the thesis,
falsification criteria, experiment gates, and implementation ledger.

## Required Before Formal Test

1. Independently review all benchmark questions, gold claims, evidence spans,
   timestamps, and answerability labels.
2. Independently curate the accepted graph without looking at test questions.
3. Mark accepted items and graph review as human verified.
4. Freeze every protocol, threshold, model digest, and artifact hash.
5. Open the 60-question test split once.
6. Report confidence intervals and every deviation from protocol.

## After The Baseline Is Valid

### Parallel Track: Graph As Knowledge Substrate

Do not require one retriever to serve every task:

```text
direct question -> Dense -> evidence gate -> answer
explore/review  -> Dense anchor -> typed Graph -> concept trail
```

The first structural audit records 27.1% graph coverage and finds that 45% of
candidate edges contain at least one association outside Dense top-5. This is a
hypothesis-generating result, not evidence of large-scale associative memory.

Measure graph value using relation precision, nonlocal useful discovery, path
quality, community stability, prerequisite violations, and learning outcomes.
See `docs/Graph as associative knowledge structure.md` and the compact artifact
`docs/experiments/rag_graph_organization_audit_v1.json`.

### R5: Harder Multi-hop Benchmark

- Use the independent controller benchmark v2 contract.
- Begin with a 36-question, development-only pilot: 12 true multi-hop, 12
  implicit-prerequisite, and 12 structured unanswerable items.
- Separate graph construction from question authoring and path review.
- Add two- and three-relation paths, evidence alternatives, in-domain missing
  bridges, and high-overlap hard negatives.
- Require double review and adjudication before any seal.

### R6: Stronger Graph Retrieval

- Type-aware expansion
- Relation-specific weights
- Personalized PageRank baseline
- Budgeted path search
- Graph pruning and noise ablation

### R7: Transcript Fallback

- Retrieve transcript evidence only when card coverage is insufficient
- Preserve timestamp provenance
- Compare card-only against card-plus-transcript retrieval

### R8: Learned Router

Only after independently reviewed C1 and oracle trajectories are frozen:

```text
query representation
-> choose dense anchor / graph expansion / transcript fallback / abstain
-> collect reward from retrieval, grounding, and human feedback
```

This is the bridge to the longer-term agentic RL research direction.
