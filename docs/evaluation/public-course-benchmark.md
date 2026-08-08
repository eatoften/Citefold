# Public Course Benchmark Contract

- **Status:** G0 protocol draft; no held-out result has been opened
- **Date registered:** 2026-08-08
- **Decision:** evaluate the unified Evidence/Understanding architecture on
  pinned public course materials without assuming that public access grants
  redistribution rights
- **Acquisition implementation:** [Public Course Benchmark Acquisition](../modules/public-course-benchmark-acquisition.md)

## Decision and claim boundary

The benchmark supports two decisions:

1. whether Concept/Relation proposals are useful enough to reduce review work;
2. whether the reviewed graph, deterministic paths, citations, refusal, and
   runtime behavior are reliable enough for a public SDE/applied-MLE project.

It does not claim improved exam scores, universal educational quality,
foundation-model generalization, or superiority over NotebookLM. Public course
content may already occur in model training data. Results remain scoped to the
registered sources, labels, software, prompts, models, and hardware.

## Course sources and licensing policy

### Primary benchmark: `cs336-sp25-v1`

The official
[Stanford CS336 Spring 2025 lecture repository](https://github.com/stanford-cs336/spring2025-lectures)
identifies `nonexecutable/` as its PDF directory and includes an
[MIT license pinned to the registered commit](https://raw.githubusercontent.com/stanford-cs336/spring2025-lectures/b98b08a98d9d47a69bbdcb4e96a58aa48ee4d13b/LICENSE)
with Stanford University copyright. The benchmark pins upstream commit
`b98b08a98d9d47a69bbdcb4e96a58aa48ee4d13b`.

The eight registered PDFs are lectures 3 (architecture), 4 (MoEs), 5 (GPUs),
7 (parallelism), 9 (scaling-law basics), 11 (scaling details), 15 (RLHF), and
16 (RLVR). Their roles are frozen as:

| Partition | Lectures | Allowed use |
| --- | --- | --- |
| authoring | 3, 5, 9, 15 | schema, annotation-guide, and runner development |
| development | 4, 7 | prompt, threshold, and error-taxonomy calibration |
| sealed transfer | 11, 16 | one final model/threshold evaluation after freeze |

Lecture 3 is the first golden graph slice, targeting 12-20 Concepts and 20-35
adjudicated relations.

Even with the repository license, this project does not vendor the PDFs by
default because individual slides may reproduce third-party paper figures or
images whose status is not enumerated file by file. It stores a pinned
manifest, attribution, expected hash, and fail-closed acquisition procedure.

### External robustness: `cs61b-sp25-external-v1`

The official [Berkeley CS61B organization](https://github.com/Berkeley-CS61B)
states that selected historical offerings are available to public auditors,
and the [Spring 2025 course site](https://sp25.datastructur.es/) publishes a
schedule with slides and recordings. No course-wide content license has been
identified as of the registration date, and course policies restrict public
posting of assignment solutions.

This is therefore an external, no-redistribution robustness track:

- use conceptual lecture material such as linked lists, asymptotics, search
  trees/maps, hashing, and graph traversal;
- store URLs, upstream identity, checksums, attribution, and locally authored
  labels only;
- never commit slides, videos, assignment skeletons, solutions, exams, or long
  excerpts;
- require explicit source-term acceptance and fail closed on content changes.

CS336 Spring 2026 is public but its current lecture repository does not declare
a root license. It remains external-only until that rights boundary changes.

### Counterfactual trust fixture

The repository includes a small self-authored CC0 mini-course using fictional
terms and facts. Its ingestible Source and gold labels/questions are separate,
sidecar-hashed artifacts; gold binds the exact Source hash. A strict loader
rejects label leakage, dangling evidence, wrong span hashes, non-production
relations, and invalid claim/citation/refusal contracts.

Its four questions are a trust/schema smoke test only. They do not constitute
a closed-world Concept inventory and must never be used to report Concept
proposal precision/recall or pass a public-course quality gate.

## Two-manifest reproducibility boundary

The implemented acquisition manifest owns external byte and rights identity:

```text
corpus_id and asset_id
course, term, and attribution
canonical HTTPS URL
repository, upstream commit, and relative path when applicable
expected SHA-256, byte size, and media type
license SPDX/status and license URL
redistribution policy and acquisition safety limits
```

The independent evaluation protocol manifest must own selected page/slide
ranges, exclusions, parser version, chunker version, Source/Chunk artifact
hashes, label lineage, prompt/model identity, and runner identity. That strict
G0.2 protocol loader is **pending**; until it exists and is frozen, no sealed or
resume-quality benchmark result may be claimed. The two manifests together,
not either one alone, form the reproducibility contract.

The downloader uses HTTPS and redirect allowlists, count/per-file/aggregate
limits, socket timeout plus a monotonic whole-asset deadline, exact content
headers, PDF magic, SHA-256, and Git blob identity. It stores verified bytes in
gitignored physical partition directories and never executes upstream code or
updates a manifest. Evaluation runners must consume exact registered asset IDs,
never recursively glob the download root.

A verified hash proves byte identity, not PDF safety. Downstream parsers must
still treat PDFs as untrusted and enforce resource/feature limits. No-overwrite
hard-link publication is atomic but not crash-durable; an abrupt crash may
leave a hidden `.part` file for later quarantine. Gold artifacts contain only
short maintainer-authored labels, exact evidence spans/hashes, and structured
contracts rather than slide bodies.

## Evaluation partitions and leakage controls

| Partition | Purpose | May tune after viewing? |
| --- | --- | --- |
| authoring | develop schemas, examples, and runner behavior | yes; never reported as held out |
| development | calibrate prompts, thresholds, and error categories | yes; every change is logged |
| golden graph | delayed two-pass annotation and deterministic path fixture | labels freeze before path evaluation |
| sealed transfer | final extraction and Chat measurement | no; open once for final evaluation after selection and runner/targets freeze |
| external robustness | domain/modality transfer without redistribution | no flagship claim if others cannot reproduce the local source |

No item may cross partitions through duplicated text, derived Cards,
paraphrased questions, or reused labels. Before sealed execution, freeze the
source and label manifests, annotation schema, prompt/model identity, chunker,
index, runner commit, seeds, thresholds, and numeric tolerances. A changed
protocol creates a new benchmark version instead of silently rerunning v1.

`R_gold` is annotated without showing system proposals: every pair in the
frozen `C_gold` inventory receives none or one/more typed/directed judgments.
System proposals are scored separately. A human-edited published graph is
never compared with itself and reported as model accuracy.

## KPI framework

“Accuracy” is split into three primary outcomes because one aggregate would
hide incompatible failure modes.

| Primary KPI | Definition | Decision supported |
| --- | --- | --- |
| Semantic proposal quality | alias-aware Concept precision/recall/F1 and exact typed/directed Relation micro/macro precision/recall/F1 | whether Understanding reduces review work |
| Published path trust | path validity/minimality, prerequisite-constraint pass rate, deterministic result-hash rate, per-edge evidence completeness, and Locator-open rate | whether paths are safe product output |
| Grounded answer trust | supported-claim precision/recall, citation precision/recall, Locator exact match, answerable coverage, and abstention precision/recall/F1 | whether Source-first Chat answers or refuses reliably |

Useful drivers are proposal acceptance yield, Concept atomicity/duplicate rate,
evidence precision/coverage, retrieval Recall@5/MRR@10/nDCG@5, review time per
accepted object, graph coverage/isolate rate, and candidate latency. Systems
diagnostics report build/query P50/P95, peak RAM, database size, incremental
recomputation ratio, retries, cancellations, restarts, and failures.

Guardrails are:

- zero cross-course, orphan, duplicate, self-loop, or accepted prerequisite
  cycle violations;
- 100% of accepted/current Concepts and relations satisfy their typed evidence
  contract and resolve through the server-owned Source navigator;
- repeated graph/path runs over identical manifests produce identical canonical
  hashes;
- the frozen Dense Source retrieval baseline does not lose more than `0.01`
  absolute nDCG@5 without a separately accepted experiment;
- partition disjointness is verified by content hash and label lineage;
- quality is never reported without latency, failures, abstention, and sample
  counts.

## Registered release gates

These initial gates are fixed before any sealed result is viewed:

| Gate | Target |
| --- | ---: |
| Chunk lineage and accepted/current evidence validity | `100%` |
| cross-course/orphan/duplicate/self-loop/prerequisite-cycle count | `0` |
| deterministic path/result-hash repeatability | `100%` |
| golden path validity, evidence completeness, and Locator-open rate | `100%` |
| Concept inventory coverage | at least `80%` |
| accepted/current isolate rate | at most `15%` |
| held-out retrieval Recall@5 | at least `0.85` |
| citation precision / recall | at least `0.95 / 0.85` |
| abstention F1 | at least `0.85` |
| Concept proposal F1 / evidence precision | at least `0.80 / 0.95` |
| Relation proposal precision | at least `0.80`; recall is always reported |
| path API P95 at 1,000 nodes | at most `200 ms`, excluding first materialization |
| path API P95 at 10,000 nodes | at most `1,000 ms`, excluding first materialization |

These numerical quality gates are registration targets, not active pass
claims. G0.2 must freeze and validate the evaluation bundle and the minimum
sample/interval rules below before any sealed run. The four-question
counterfactual fixture is excluded from every minimum.

### Minimum samples and scoring rules to freeze in G0.2

The v1 evaluation protocol may increase these values before opening sealed
data, but may not reduce them afterward:

- retrieval and grounded-answer reporting needs at least 40 answerable and 20
  unanswerable questions, with at least 80 independently scored atomic claim
  units and exact citation opportunities;
- Concept proposal reporting needs at least 50 closed-world gold Concepts;
- Relation proposal reporting needs at least 50 gold relations. A relation
  type enters a per-type or macro gate only with at least 10 gold instances;
  a flagship macro claim needs at least three supported production types;
- a zero-support slice is `N/A`, never zero or one. It is excluded from the
  macro mean but its missing support is reported, and a required slice with
  insufficient support prevents the corresponding gate from passing;
- Concept matching uses one-to-one maximum bipartite matching over a frozen
  alias table after Unicode NFKC, case-folding, and whitespace normalization.
  Aliases added after seeing sealed predictions create a new protocol version;
- answer scoring first segments output into atomic subject-predicate-object
  claims. Conjunctions become separate claims; negation, modality, quantity,
  and prerequisite direction remain part of the claim. Each required gold
  claim can match at most one predicted claim under the frozen normalization
  and alias rules. Extra supported claims are allowed; unsupported claims are
  precision errors;
- uncertainty uses a frozen resampling unit and seed. The default is 10,000
  paired bootstrap samples, clustered by lecture when multiple observations
  share one lecture. Every statistical gate reports a 95% interval. A pass
  requires both its registered point threshold and the separately frozen
  lower-confidence-bound floor; if sample size cannot support the registered
  interval, the result is diagnostic rather than confirmatory.

Development results may cause a documented v2 target proposal, but v1 targets
are not moved to rescue a sealed result. Low proposal quality does not negate
deterministic graph correctness; it prevents automation/efficiency claims.

Multiple valid equal-length paths are scored against an allowed path set or by
validity plus minimality. Exact ordered-path matching applies only after the
deterministic tie-break contract is frozen.

## Baselines and ablations

Registered comparisons are:

- BM25 vs Dense vs Hybrid/RRF retrieval;
- no graph vs graph-assisted retrieval/routing where separately justified;
- cosine edge vs LLM proposal vs human gold;
- ungrounded proposal vs evidence-bound candidate;
- deterministic BFS/Kahn baseline vs evidence/constraint-aware path serving.

Report the G0.2-frozen 95% intervals and results by question/relation type. At
least one negative or rejected ablation remains in the final report.

## Required outputs

Each run emits raw machine-readable results and a concise report containing:

- manifest, label, environment, dependency, model, prompt, and artifact hashes;
- exact formulas, matching rules, tolerances, and zero-denominator policy;
- per-course and per-relation-type metrics and sample counts;
- latency distribution, query count, memory, timeouts, retries, and failures;
- false merge, missed Concept, wrong type/direction, unsupported edge, stale
  evidence, citation, refusal, and path error categories;
- baseline, ablation, confidence interval, negative result, and limitations;
- a claim ledger mapping every README/resume statement to its evidence.

The sealed test is opened once for final evaluation after selection and
protocol freeze. Later executions are explicitly labeled reproductions, not
new tuning opportunities.
