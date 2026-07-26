# Reasoning-Guided Adaptive Retrieval Research Plan

Last updated: 2026-07-26

## 中文执行摘要

本项目的论文主线不是“做一个更完整的课程问答产品”，也不是把
GraphRAG、多模态或教育场景本身包装成创新。核心研究命题是：

> 检索应被建模为推理时、受当前知识缺口驱动的序贯决策，而不是在
> 生成前只对原始问题做一次固定 top-k 检索。

因此 controller 必须显式决定：是否需要检索、当前缺少哪个概念、应
搜索概念还是证据、是否沿有类型且有方向的关系扩展、证据是否真的
支持当前知识需求，以及何时回答或拒答。实验必须在相同检索次数、
上下文字符、模型调用、token 和延迟预算下比较，不能把“多检索了几
次”误写成“推理更好”。

现有代码不是推倒重来。产品管线已经提供带时间戳的 card、claim 和
evidence；R1-R4 已提供冻结语料、Dense/BM25/RRF/graph 基线、grounded
answer、指标、hash、resume 和 test gate。新的 controller 研究轨道只
读取这些产物，不修改旧 schema，也不打开现有 60 题 test split。

当前已完成第一轮研究基础设施：

1. 严格分类型的 state/action/observation/trace/protocol；
2. card-proxy concept、claim evidence 和 typed directed edge memory；
3. fixed Dense、fixed Dense + typed graph、rule evidence-gap 三条策略；
4. 统一的预算、重复动作、no-progress、引用和失败边界；
5. 可恢复的 JSONL episode runner 与 artifact manifest；
6. 独立 benchmark v2 contract、泄漏审计、双人复核、仲裁和 seal；
7. 三个真实 development smoke run，每条策略运行 2 题。

这些 smoke run 只证明管线真实可运行，不证明方法有效。它们使用旧的
候选题、候选 graph 和确定性词法 verifier，因此所有结果都被强制标记
为 `paper_claim_eligible=false`。下一研究瓶颈不是继续堆模型，而是独立
构建和双人复核 36 题 controller-sensitive development pilot，并补足
方向明确的 prerequisite relation。

当前 benchmark evaluator 也只允许输出 development diagnostic：它能审计
声明的 memory 成员和 trace 元数据，但还不能从 canonical source 重建
memory，也不能重放 Dense/BM25 与 runner 来证明一次检索确实发生。因此
即使 exact match 为 1，报告仍在类型层固定
`paper_claim_eligible=false`。这是 Phase 6 前必须完成的硬门槛。

## Status

This document is the master research and implementation plan for extending
Video Course Cards from fixed retrieval into a controlled study of
reasoning-guided adaptive retrieval.

It is a research plan, not a claim that the proposed method is novel or that the
current system is publication-ready. Every claim below is either:

1. an observation from the current repository;
2. a hypothesis to be tested;
3. an implementation decision that can be revised before its protocol is
   frozen.

The existing R1-R4 corpus, benchmark, review, protocols, development results,
and unopened test split remain immutable. New controller work starts under a new
versioned experiment family and must not silently change old artifacts.

## 1. Thesis

The central research claim is not that a graph, multimodal memory, or an
educational assistant is new.

The central formulation is:

> Retrieval should be modeled as an inference-time decision process conditioned
> on the current reasoning state, rather than as one query-only preprocessing
> step before generation.

The controller must decide:

1. whether more information is needed;
2. which unresolved knowledge need should be targeted;
3. whether to search for a concept, retrieve supporting evidence, or follow a
   typed relation;
4. whether the retrieved evidence actually resolves the knowledge need;
5. whether to continue, answer, or abstain under a fixed budget.

The intended contribution is only supported if controller decisions improve
required-concept and evidence coverage, downstream grounded answers, or the
quality-cost frontier under controlled comparisons.

## 2. Research Questions

### RQ1: Query-only retrieval

Does retrieval based only on the original question fail to recover concepts
that are necessary for answering but are not stated explicitly in the question?

### RQ2: Structured memory

Under which task types does typed concept structure improve over flat dense
retrieval, and when does graph expansion introduce distractors?

### RQ3: Reasoning-state control

Does a controller conditioned on resolved and unresolved knowledge needs
outperform fixed retrieval and query-only iterative retrieval under equal
retrieval, context, model, token, and latency budgets?

### RQ4: Evidence granularity

Is it useful to separate concept discovery from evidence retrieval instead of
retrieving complete cards for every action?

### RQ5: Multimodal evidence

On questions deliberately selected so that transcript evidence is incomplete,
does aligned slide, equation, code, or visual evidence improve retrieval and
grounded answer quality?

### RQ6: Learned policy

After collecting sealed trajectories, can a learned cost-sensitive policy
choose retrieval actions more accurately or efficiently than the structured
rule/LLM controller?

## 3. Hypotheses And Falsification

### H1: Implicit-concept gap

Original-query Dense retrieval will have lower required-concept recall on
questions with implicit prerequisites than on direct questions.

Falsification:

- Dense retrieval already recovers all required concepts under the same budget;
- the apparent gap disappears after independent question review;
- the gap is explained by poor card or concept labels rather than query-only
  retrieval.

### H2: Task-conditional graph value

Typed, directed graph actions will help prerequisite and path tasks more than
direct factual tasks.

Falsification:

- typed neighbors remain inside the Dense top-k neighborhood;
- path recall does not improve under equal budgets;
- graph noise, coverage, or maintenance cost dominates any gain;
- diversity-aware flat retrieval matches graph performance.

### H3: Reasoning-state value

A controller that targets unresolved knowledge needs will outperform an
otherwise identical controller that repeatedly uses only the original query.

Falsification:

- state-aware query reformulation does not improve required-concept or evidence
  recall;
- gains come only from extra calls or a larger context;
- gains disappear under equal token, latency, and retrieval-call budgets;
- stopping errors offset retrieval improvements.

### H4: Controlled retrieval efficiency

Adaptive retrieval can obtain a better quality-cost frontier than always
retrieving a fixed maximum amount of context.

Falsification:

- the controller always consumes its full budget;
- fixed retrieval dominates at every budget point;
- controller overhead exceeds the retrieval savings.

### H5: Multimodal conditional value

Multimodal evidence will help on a prespecified modality-required subset, not
uniformly across ordinary lecture QA.

Falsification:

- transcript-only evidence answers the modality-required items;
- multimodal evidence cannot be cited or aligned reliably;
- improvements vanish after controlling for additional text or context size.

## 4. Current Repository Baseline

### 4.1 Product pipeline

The current application already implements:

```text
video
-> ffprobe validation
-> FFmpeg audio extraction
-> faster-whisper timestamped transcript
-> semantic transcript chunks
-> local Qwen grounded card generation
-> SQLite cards, claims, evidence, topics, relations, and review items
-> Course Map / Study / Review / Explore / Retrieve / Markdown export
```

Knowledge cards preserve claim-level transcript evidence and timestamps. This
is a strong provenance foundation and should be reused.

### 4.2 Product RAG

The product `/rag/retrieve` path currently performs:

```text
question
-> MiniLM query embedding
-> card cosine similarity
-> top-k cards
```

The frontend Ask panel displays five retrieved cards. It does not generate a
grounded answer and has no graph traversal, transcript fallback, reasoning
state, iterative retrieval, controller trace, or learning-path generation.

### 4.3 RAG research baseline

`backend/rag_lab` already contains:

- BM25, Dense, RRF, and Dense-plus-one-hop-graph baselines;
- versioned corpus, benchmark, annotation, protocol, and result schemas;
- exact card/claim/evidence citation generation;
- a Dense confidence gate for abstention;
- Recall, MRR, nDCG, joint recall, citation, abstention, latency, and bootstrap
  metrics;
- immutable hashes, resumable artifacts, and a blocked confirmatory test path.

These components become controller actions and evaluators; they should not be
rewritten.

### 4.4 Current data limits

The local research snapshot contains:

- 1 course and 5 lectures;
- 118 cards, 140 claims, and 150 evidence spans;
- 100 candidate questions, with 40 development and 60 unopened test items;
- only 8 development multi-hop questions;
- 20 model-assisted candidate graph edges, including only 2 prerequisite
  edges;
- graph coverage of 32/118 cards.

The current questions and graph are pending independent human review. The
existing multi-hop slice mostly combines two cards; it is not yet a benchmark
of iterative missing-prerequisite discovery.

### 4.5 Multimodal baseline

`backend/multimodal_lab` already contains controlled work for:

- transition and stable-page detection;
- native page text and RapidOCR;
- handwritten CNN-CTC and ViT-CTC readers;
- an OCR-to-card cascade.

The visual research pipeline is not integrated into product RAG. Current RAG
snapshots do not contain source units, slide images, equations, code regions, or
generic multimodal evidence nodes. OCR output is still reduced to text before
card generation.

## 5. Research Code Architecture Lessons

The repository architecture was reviewed against official code releases whose
award status is documented by their conferences.

### 5.1 VGGT, CVPR 2025 Best Paper

Official sources:

- CVPR award announcement:
  https://cvpr.thecvf.com/Conferences/2025/News/Awards_Press
- official repository:
  https://github.com/facebookresearch/vggt
- training tree:
  https://github.com/facebookresearch/vggt/tree/main/training

Observed organization:

```text
vggt/                 installable inference/model package
training/
  config/
  data/
  train_utils/
  launch.py
  loss.py
  trainer.py
examples/             direct use cases
docs/
```

Applicable lesson:

- keep reusable model/inference contracts separate from training orchestration;
- give data, configuration, trainer, and launcher explicit boundaries;
- document one short path from environment setup to a real run.

Not adopted:

- multi-dataset distributed-training infrastructure before this project has a
  measured need for it.

### 5.2 DINOv2 and Vision Transformers Need Registers

Official sources:

- ICLR 2024 Outstanding Paper announcement:
  https://blog.iclr.cc/2024/05/06/iclr-2024-outstanding-paper-awards/
- official DINOv2 repository used for the register models:
  https://github.com/facebookresearch/dinov2

Observed organization:

```text
dinov2/
  configs/
  data/
  eval/
  layers/
  loss/
  models/
  run/
  train/
  utils/
MODEL_CARD.md
```

The README provides separate training and evaluation commands, explicit dataset
layouts, output directories, checkpoint locations, and environment
requirements.

Applicable lesson:

- train and evaluation entry points must be separate;
- evaluation must identify one frozen checkpoint and cannot select it;
- configuration, data layout, and output artifacts are part of the scientific
  contract;
- limitations and model behavior deserve a durable model/result card.

Not adopted:

- FSDP, SLURM, or a large configuration framework at the current scale.

### 5.3 VAR, NeurIPS 2024 Best Paper

Official sources:

- NeurIPS award announcement:
  https://blog.neurips.cc/2024/12/10/announcing-the-neurips-2024-best-paper-awards/
- official repository:
  https://github.com/FoundationVision/VAR

Observed organization:

```text
models/
utils/
train.py
trainer.py
demo notebooks
```

VAR is much smaller than DINOv2 but records exact training commands, a known
output directory, logs, checkpoints, automatic resume behavior, sampling
settings, and an independent evaluation recipe.

Applicable lesson:

- research code should be no larger than the experiment requires;
- a small flat package can be rigorous when commands and artifacts are exact;
- checkpoint, log, and resume behavior must be explicit.

### 5.4 Rules adopted for this project

1. `backend/app` remains product code and must not import `rag_lab`.
2. Controller research begins in `backend/rag_lab`.
3. Frozen R1-R4 artifacts are never rewritten.
4. Data contracts, policies, runners, and metrics are separate modules.
5. Development and confirmatory test access remain separate.
6. Every run records protocol, input hashes, code revision, dirty state,
   environment, model identity, actions, costs, outputs, and artifact hashes.
7. Full traces and embeddings remain under ignored `backend/data/rag_lab/`;
   compact reviewed results and protocols are tracked in `docs/experiments/`.
8. Add infrastructure only after a concrete limitation is measured.

## 6. Target Memory Contract

The controller must not depend directly on SQLite or one particular retriever.
It consumes a frozen memory snapshot through a small environment interface.

### 6.1 Development memory v0

For the first controlled experiment:

```text
KnowledgeCard     -> concept proxy
Card claim        -> semantic assertion
Claim evidence    -> evidence node
Accepted relation -> typed concept relation
```

This permits a controller experiment without first changing the product
database.

### 6.2 Formal concept/evidence memory

Later snapshots should introduce explicit objects:

```text
ConceptNode
  concept_id
  canonical_name
  aliases
  summary
  source_card_ids

EvidenceNode
  evidence_id
  source_type: transcript | slide | video_frame | document | equation | code
  source_id
  text
  locator
  start_seconds/end_seconds
  modality
  extraction_method/version
  confidence

ConceptEvidenceEdge
  concept_id
  evidence_id
  support_type
  review_status

ConceptRelationEdge
  source_concept_id
  target_concept_id
  relation_type
  direction
  confidence
  review_status
```

Concept normalization and multimodal extraction are evaluated separately from
the controller so their errors do not become hidden controller gains or losses.

## 7. Controller Contract

### 7.1 Structured reasoning state

The state contains externally inspectable research variables, not unrestricted
hidden chain-of-thought:

```text
ReasoningState
  question
  knowledge_needs[]
    need_id
    description
    status: unresolved | partially_supported | supported
            | contradicted | unresolvable
    support_concept_ids[]
    support_evidence_ids[]
    confidence
  retrieved_concept_ids[]
  retrieved_evidence_ids[]
  attempted_action_fingerprints[]
  answerability_confidence
  budget
  step_index
```

### 7.2 Action space

```text
SEARCH_CONCEPT(query, top_k)
SEARCH_EVIDENCE(query, concept_ids, top_k)
EXPAND_TYPED_NEIGHBOR(concept_ids, relation_types, top_k)
VERIFY_SUPPORT(need_ids)
ANSWER
ABSTAIN
```

Actions must use validated typed arguments. Unknown actions, repeated action
keys, invalid IDs, empty queries, and budget violations fail closed.

### 7.3 Observation

Every environment call returns:

- stable concept/evidence IDs;
- ranked scores and retrieval source;
- relation type and direction where applicable;
- elapsed time;
- the charged retrieval and context cost;
- no untracked mutation of the memory snapshot.

### 7.4 Trace

Every step records:

- state before the action;
- selected action and normalized action key;
- observation IDs and scores;
- state after the action;
- retrieval, token, character, and latency costs;
- controller and environment errors;
- stop reason.

A completed trace must be replayable against the same snapshot and protocol.

### 7.5 Budget

Initial controlled budgets:

- maximum controller steps;
- maximum retrieval calls;
- maximum concepts and evidence items;
- maximum accumulated context characters;
- optional input/output token limits;
- maximum wall-clock time.

Budget exhaustion produces an explicit stop reason. It must never silently
convert into an ordinary answer.

## 8. Controller Versions

### C0: Fixed baselines

Required baselines:

1. no retrieval / long context where feasible;
2. one-shot Dense;
3. one-shot Dense plus unconditional graph expansion;
4. query-only iterative Dense;
5. fixed Dense plus typed graph;
6. oracle action policy for an upper-bound diagnostic.

### C1: Evidence-Gap Controller

C1 is the first proposed method.

Flow:

```text
question
-> identify atomic knowledge needs
-> retrieve initial concepts
-> mark supported and unresolved needs
-> target one unresolved need
-> choose concept, evidence, or typed-neighbor action
-> verify whether new evidence closes the gap
-> continue, answer, or abstain under budget
```

The first implementation may use deterministic rules plus a constrained local
LLM decision payload. It is more than a single YES/NO retrieve prompt because
it maintains multiple needs, evidence bindings, action history, and a stopping
contract.

### C2: Learned cost-sensitive controller

C2 starts only after C1 and oracle trajectories are sealed.

Candidate state features:

- unresolved and partially supported need counts;
- Dense top score and score margin;
- evidence-support coverage;
- graph degree, relation type, and path features;
- previous action type and marginal gain;
- remaining budget;
- answerability confidence.

The first learned method should be supervised imitation or cost-sensitive
classification. Contextual bandit or reinforcement learning is justified only
after the reward, transition, and off-policy evaluation contracts are stable.

## 9. Controller-Sensitive Benchmark

### 9.1 Main QA tasks

1. Direct controls.
2. Multi-hop concept reasoning.
3. Prerequisite reasoning.
4. Unanswerable questions.

Learning-path generation is a separate benchmark because its labels and
evaluation are not equivalent to QA.

### 9.2 Required labels

Every answerable item includes:

- required concept IDs;
- implicit required concept IDs not directly named in the question;
- required claim/evidence IDs;
- one or more valid typed paths where a path is semantically required;
- minimum reasoning hops;
- modality requirement;
- difficulty and authoring method;
- reference answer;
- independent review status and notes.

The benchmark should not prescribe one unique action sequence when multiple
retrieval plans can be valid.

### 9.3 Pilot

The first pilot stays on the existing five lectures and is development-only:

- direct controls to detect ranking regressions;
- independently authored multi-hop questions;
- independently authored prerequisite questions;
- hard negatives from neighboring concepts;
- unanswerable questions;
- a small, separately reported learning-path set.

The pilot is a method-debugging instrument. It is not used for a final paper
claim.

### 9.4 Formal expansion

Only after the pilot shows a controller-sensitive signal:

- add courses and lectures;
- split by lecture or course, not only by random question;
- target 500-2,000 independently reviewed questions;
- freeze authoring, review, and adjudication procedures;
- seal development and test artifacts before final controller tuning.

### 9.5 Circular-bias controls

- graph edges must not be created from test questions;
- test questions must not be authored directly from the exact edge pairs used
  as gold paths without an independent source;
- graph review occurs before test question inspection;
- question author, edge reviewer, and final adjudicator identities/methods are
  recorded;
- graph-free gold evidence remains available for auditing.

### 9.6 Audit of the current benchmark

The existing R1 benchmark remains useful for regression and development smoke
tests, but it cannot validate the controller thesis:

- only 8 of 40 development questions are labeled multi-hop;
- `graph_path_card_ids` contains card IDs but no typed edge, direction,
  alternative path, or per-hop evidence;
- the same paired seed was used to generate some multi-hop questions and
  accepted graph decisions, creating graph-pair circular bias;
- development and test share six gold claims and their six owner cards;
- the candidate graph has only two prerequisite decisions;
- most unanswerable questions are obvious out-of-domain questions rather than
  in-domain missing-bridge or insufficient-evidence cases;
- the existing review status does not independently adjudicate required
  concepts, implicitness, paths, modality, difficulty, and answerability;
- the existing test gate is a state check, not a one-use access ledger;
- a confidence threshold must never be selected again on the tested split.

These findings do not invalidate the earlier R1-R4 development report. They
limit which claims those artifacts can support.

### 9.7 Controller benchmark v2 contract

The new contract is isolated under `backend/rag_lab/controller_benchmark/`.
It adds:

- required concepts with anchor/target/bridge/prerequisite roles and
  explicit/implicit mention status;
- evidence requirements in disjunctive-normal form: every requirement must be
  satisfied, while each requirement can accept multiple equivalent all-of
  evidence sets;
- multiple typed, directed, concept-level valid paths;
- prerequisite invariants requiring an implicit prerequisite and a directed
  prerequisite-to-dependent edge;
- structured unanswerable certificates with negative-search audits;
- modality requirements and reproducible difficulty axes;
- family, evidence bundle, concept structure, evidence structure, and path
  leakage audits across splits;
- a canonical concept registry and evidence catalog whose contents, not only
  declared hashes, are sealed and checked;
- split-manifest timing that must precede question authoring and annotation;
- role-ordered paths from anchor through bridge/prerequisite nodes to target;
- graph/question authoring independence manifests;
- two independent reviewers per item, third-party adjudication on
  disagreement, and a hash-bound seal.

Runtime graph IDs are intentionally not embedded in the benchmark. A protocol
binds the runtime graph separately so graph-free, graph-based, and learned
systems can be compared against the same task labels.

The current evaluator is deliberately limited to
`development_diagnostic`. It checks the sealed annotation structure and audits
trace IDs, directions, ranks, and metadata against the declared frozen memory.
It does not yet reconstruct that memory from canonical corpus/source artifacts
or replay and attest the Dense/BM25 retrieval and runner execution. Therefore
both report-level and exact-match paper eligibility are fixed to `false`.
Canonical source reconstruction and deterministic execution replay are hard
gates before Phase 6 or any formal claim.

### 9.8 Development pilot specification

The first new dataset contains 36 development-only questions:

- 12 true multi-hop questions: six two-relation-hop and six
  three-relation-hop, with at least eight implicit bridges;
- 12 prerequisite questions: six within-lecture and six cross-lecture, with
  the prerequisite absent from the question wording;
- 12 unanswerable questions: four corpus-absent, four in-domain
  missing-bridge/insufficient-evidence, and four high-overlap
  hard-negative/ambiguous cases.

Before authoring the prerequisite slice, independently review at least 12-20
directionally clear prerequisite relations. The pilot remains text/card-only
so controller behavior is not confounded with unfinished multimodal
alignment. Learning-path generation remains a separate benchmark.

## 10. Evaluation

### 10.1 Retrieval

- required-concept Recall@k;
- implicit-concept Recall@k;
- evidence Recall@k;
- joint concept coverage;
- typed path precision and recall;
- nonlocal useful discovery;
- distractor rate.

### 10.2 Controller

- retrieval-necessity accuracy;
- action validity and duplicate-action rate;
- marginal concept/evidence gain per action;
- stop precision and stop recall;
- supported-answer rate at stop;
- budget exhaustion rate;
- loop/error rate.

### 10.3 Answer

- independently reviewed correctness;
- claim entailment;
- gold claim/evidence citation recall;
- citation precision;
- abstention F1;
- LLM judge only as a secondary, calibrated measure.

### 10.4 Efficiency

- controller steps;
- retrieval calls;
- retrieved concepts/evidence;
- context characters;
- input/output tokens when available;
- controller, retrieval, verification, and generation latency;
- memory and indexing cost.

The primary comparison is a quality-cost frontier under equal budgets, not a
single arbitrarily weighted utility number.

### 10.5 Learning path

- prerequisite correctness;
- path completeness;
- prerequisite violations;
- unnecessary detours;
- explanation quality;
- expert and learner usefulness ratings.

## 11. Ablations

1. Remove the controller and use fixed retrieval.
2. Preserve the loop but remove reasoning state; reuse the original question.
3. Remove graph actions.
4. Use untyped or undirected graph actions.
5. Remove evidence verification.
6. Remove explicit stopping and consume the full budget.
7. Remove multimodal evidence.
8. Replace explicit concepts with raw card retrieval.
9. Equalize only top-k but not cost, as a documented invalid comparison
   diagnostic.

## 12. Implementation Map

The initial research package should remain small:

```text
backend/rag_lab/
  controller_schemas.py
  controller_memory.py
  controller_policy.py
  controller_runner.py
  controller_metrics.py
  run_controller_experiment.py
  controller_benchmark/
    schemas.py
    audit.py
    metrics.py

backend/tests/
  test_rag_controller_benchmark.py
  test_rag_lab_controller_schemas.py
  test_rag_lab_controller_memory.py
  test_rag_lab_controller_policy.py
  test_rag_lab_controller_runner.py
  test_rag_lab_controller_metrics.py
  test_rag_lab_controller_experiment.py

docs/experiments/
  rag_controller_protocol_v*.json
  rag_controller_*_results.json
```

Generated snapshots, embeddings, traces, prompts, predictions, and large review
files remain under ignored `backend/data/rag_lab/controller/`.

## 13. Execution Plan And Gates

### Phase 0: Research contract

Work:

- freeze terminology, RQs, hypotheses, actions, budgets, metrics, and
  falsification conditions;
- record lessons from official award-paper repositories;
- state what is intentionally deferred.

Deliverables:

- this document;
- one compact controller protocol template.

Pass gate:

- every claimed contribution maps to a measurable comparison;
- no metric requires access to the unopened R1 test set;
- controller, graph, and multimodal effects can be isolated.

### Phase 1: Schema and replay foundation

Work:

- implement state, need, action, observation, trace, cost, budget, and protocol
  schemas;
- implement canonical hashes;
- implement trace invariants and replay identity.

Required tests:

- invalid IDs and action arguments fail;
- terminal actions cannot be followed by another step;
- costs are nonnegative and cumulative;
- state and step indices are monotonic;
- canonical hashes are stable;
- protocol mutation changes its hash.

Pass gate:

- a synthetic trace can be serialized, loaded, validated, and replayed
  deterministically.

### Phase 2: Frozen memory environment

Work:

- adapt the existing card corpus into concept proxies and evidence nodes;
- expose Dense concept search, evidence retrieval, and typed directed expansion;
- preserve all source IDs and scores;
- charge deterministic costs.

Required tests:

- unknown concepts and evidence fail closed;
- typed direction is respected;
- top-k and score ordering are deterministic;
- no environment action mutates the snapshot;
- repeated retrieval has a stable normalized action key.

Pass gate:

- all action types work on a synthetic corpus and a local frozen development
  snapshot.

### Phase 3: C0 baselines and oracle

Work:

- implement fixed Dense, fixed graph, query-only iterative, and oracle policies;
- run every policy through the same runner and budgets;
- record traces for baselines, not only the proposed method.

Required tests:

- all policies receive the same memory and protocol;
- oracle cannot read reference answers or test-only data;
- budget accounting is identical;
- a loop and max-step condition terminate explicitly.

Pass gate:

- baseline output reproduces existing one-shot Dense ordering on shared cases;
- equal-budget comparison artifacts are generated.

### Phase 4: C1 Evidence-Gap Controller

Work:

- implement structured knowledge-need initialization;
- implement constrained action decisions;
- bind evidence to needs;
- implement support verification and stopping;
- add malformed-output repair and deterministic fallback.

Required tests:

- unresolved needs cause targeted retrieval;
- supported needs are not retrieved repeatedly;
- irrelevant observations do not mark a need supported;
- answer requires support or an explicit development-only override;
- malformed controller output does not corrupt the trace.

Pass gate:

- C1 completes synthetic multi-hop and prerequisite fixtures;
- every decision is traceable;
- no infinite loops or silent budget overruns occur.

### Phase 5: Controller-sensitive pilot

Work:

- create a new versioned benchmark contract;
- author development-only pilot items independently of graph construction;
- render human review sheets;
- audit required concepts, evidence, paths, difficulty, and answerability.

Pass gate:

- all pilot gold IDs resolve to the frozen memory;
- leakage and circular-bias audits pass;
- main slices contain enough items for informative confidence intervals;
- formal claims remain blocked until independent review.

### Phase 6: Controlled experiment

Work:

- run C0, C1, and ablations under multiple fixed budgets;
- evaluate retrieval, controller, answer, and efficiency metrics;
- bootstrap paired differences;
- write failure taxonomy and validity threats.

Pass gate:

- no method receives additional memory, model capacity, or hidden budget;
- any reported gain survives the relevant equal-budget comparison;
- negative results are retained.

Decision:

- if reasoning state does not help, stop controller scaling and diagnose the
  benchmark/memory assumptions;
- if it helps only because of extra retrieval, weaken H3;
- if it improves the frontier, proceed to multimodal and learned-policy work.

### Phase 7: Multimodal evidence

Work:

- version generic evidence nodes and locators;
- align existing page/slide/frame artifacts with transcript/card memory;
- add modality-aware evidence actions;
- create a prespecified modality-required subset;
- compare transcript-only and multimodal memory.

Pass gate:

- evidence is directly inspectable and citeable;
- modality labels are independently reviewed;
- OCR/VLM extraction errors are not counted as controller errors;
- multimodal gains survive equal-context controls.

### Phase 8: C2 learned controller

Work:

- freeze C1/oracle trajectories;
- define training/dev/test splits by course or lecture;
- train an interpretable cost-sensitive policy first;
- add bandit/RL only if sequential credit assignment remains a measured
  limitation.

Pass gate:

- training never reads confirmatory test labels;
- state features are available at inference time;
- learned gains hold on held-out courses or lectures;
- reward choices and off-policy limitations are explicit.

### Phase 9: Product integration

Work:

- promote only validated inference contracts into `backend/app`;
- add grounded answer and citation APIs;
- add optional trace/diagnostic rendering;
- keep research-only labels and test access out of the product.

Pass gate:

- product behavior has integration and migration tests;
- research and user data remain separate;
- desktop packaging and runtime checks remain healthy.

## 14. Artifact And Reproducibility Contract

Every completed run records:

- protocol ID and SHA-256;
- memory, benchmark, review, graph, and embedding hashes;
- model name and immutable digest where available;
- prompt and policy versions;
- Git commit and dirty state;
- Python, OS, package, and hardware information;
- random seeds;
- per-step actions, observations, state deltas, and costs;
- final answer and citations;
- run status, errors, and resumability metadata;
- hashes of every produced artifact.

Development thresholds are never described as test results. A confirmatory test
runner must reject:

- pending questions;
- non-human-verified gold evidence;
- a changed protocol hash;
- a changed memory or graph;
- a checkpoint selected using test results;
- incomplete or reused output directories.

## 15. Risks And Mitigations

### Benchmark does not require adaptive retrieval

Mitigation:

- label implicit required concepts;
- include query-only hard cases and direct controls;
- use oracle coverage to test whether the memory can answer the item at all.

### Graph quality confounds controller quality

Mitigation:

- freeze one graph across policy comparisons;
- report graph coverage and edge precision;
- include graph-free and oracle-path conditions.

### Small local LLM cannot control reliably

Mitigation:

- constrain JSON actions;
- validate every argument;
- use deterministic fallbacks;
- report controller parse/repair/error rates separately.

### Extra retrieval masquerades as reasoning

Mitigation:

- equalize calls, characters, tokens, and latency;
- compare query-only iterative retrieval;
- report marginal gain per action.

### Multimodal extraction confounds retrieval

Mitigation:

- evaluate extraction and alignment separately;
- preserve raw and normalized evidence with provenance;
- report oracle/native/OCR/VLM evidence conditions separately.

### Learned policy overfits generated questions

Mitigation:

- split by course/lecture;
- independently author and review questions;
- retain simple non-learned baselines;
- delay RL until trajectories and rewards are stable.

### Data licensing prevents redistribution

Mitigation:

- track hashes and construction scripts;
- distribute compact annotations only when permitted;
- document which source assets users must obtain themselves.

## 16. Progress Ledger

### 2026-07-25

Completed:

- proposal interpreted against the current codebase;
- product, RAG-lab, graph, benchmark, and multimodal gaps audited;
- official VGGT, DINOv2/register, and VAR repository organization reviewed;
- research direction and staged gates written;
- closed controller schemas, canonical state/protocol/trace hashes, and replay
  invariants implemented;
- card-proxy concept memory, claim-level evidence BM25, frozen Dense concept
  search, and typed directed expansion implemented;
- fixed Dense, fixed Dense plus typed graph, and deterministic rule
  evidence-gap policies implemented behind a gold-free decision boundary;
- action guards, cumulative budgets, duplicate/no-progress termination,
  evidence verification, citation guards, infrastructure-failure separation,
  and resumable JSONL runs implemented;
- legacy debug metrics for required concepts, evidence requirements, stopping,
  first-hit step, calls, context, and latency implemented;
- independent controller benchmark v2 schemas, DNF evidence alternatives,
  typed directed paths, leakage audit, graph-independence manifest, double
  review, adjudication, seal audit, and development-diagnostic metrics
  implemented;
- benchmark concept/evidence catalogs, split chronology, path-role order,
  runtime-memory membership checks, and explicit paper-ineligible evaluator
  boundary implemented;
- three real development smoke runs completed over two existing candidate
  questions each: fixed Dense used 2 retrieval calls/4 steps, fixed typed graph
  used 3 calls/5 steps, and rule evidence-gap used 3 calls/5 steps;
- every smoke artifact marked `paper_claim_eligible=false`;
- every current run artifact hash checked against its manifest;
- full backend verified at 415 passed with one existing dependency warning;
- frontend ESLint and TypeScript checks verified.

In progress:

- Phase 3 oracle diagnostic and query-only iterative control;
- Phase 4 knowledge-need initialization with a constrained semantic planner and
  semantic verifier;
- Phase 5 independent 36-question development pilot authoring and review.

Blocked from formal claims:

- current benchmark and graph remain model-assisted candidates;
- the existing 60-question test split remains unopened;
- the development smoke runner rejects all test access until a separate
  test-gold loader and one-use access ledger are implemented;
- the controller benchmark contract exists, but no independently authored and
  double-reviewed controller-sensitive items exist yet;
- the evaluator currently performs declarative membership/metadata auditing,
  not canonical source reconstruction or deterministic retriever/runner
  replay, so it is type-level paper-ineligible;
- only two current candidate graph edges are prerequisite relations;
- the rule evidence-gap controller currently initializes one public-question
  need whose type is selected by deterministic public-text cues; it is a
  runtime substrate, not yet the proposed semantic C1 method;
- the deterministic lexical verifier and extractive answerer are smoke
  components, not answer-quality evaluators;
- multimodal evidence is not integrated into RAG memory;
- no learned-controller training data has been sealed.

## 17. 下一执行周期 / Next Execution Cycle

本周期的目标不是立即训练更复杂的 controller，而是先建立一条能够支撑
可信 development 结论的完整证据链：

```text
canonical sources
-> frozen memory reconstruction
-> deterministic action replay
-> controller-sensitive human-reviewed pilot
-> equal-budget C0/C1 experiment
-> paper-readiness decision
```

执行顺序由依赖关系决定。任何后续步骤都不能以打开现有 test split、
复用 test gold 或把 smoke 指标写成方法效果为代价。

### N1. 关闭 source 与 execution provenance 缺口

目的：

- 使 evaluator 不再只相信声明的 corpus/memory hash；
- 证明 trace 中的 Dense、BM25 和 typed-graph observation 确实可以由冻结
  source、index、query encoder、action 和 state 重放得到；
- 在完成该闭环前继续保持所有报告
  `paper_claim_eligible=false`。

实现：

1. 定义 canonical evaluation bundle，直接携带并校验 corpus、annotation
   review、embedding snapshot、query-encoder digest、retrieval config、
   memory snapshot 和 protocol。
2. 从 canonical sources 重新构建 memory，并要求重建后的
   `memory_sha256` 与 protocol 完全一致。
3. 增加 deterministic retrieval replay：
   - `SearchConcept` 重算 query embedding、cosine score、排序和 top-k；
   - `SearchEvidence` 重算 tokenizer、BM25 score、scope、exclude 和排序；
   - `ExpandTypedNeighbor` 重算方向、relation type、anchor、exclude 和
     neighbor order；
   - 比较 hit ID、rank、score、novel/duplicate ID 和除真实 latency 外的
     cost。
4. 使用同一 pure reducer 重放 action/observation/state transition，并
   核对 terminal action、citation 和累计预算。
5. 增加 ghost corpus、已知 gold ID 注入、伪造 score/rank、错误 encoder、
   memory mismatch 和 runner-bypass 回归测试。

计划产物：

```text
backend/rag_lab/controller_replay.py
backend/rag_lab/run_controller_replay_audit.py
backend/tests/test_rag_lab_controller_replay.py
docs/experiments/rag_controller_replay_audit_v1.json
```

通过门槛：

- canonical source 的任意内容变化都会改变绑定 hash；
- source 无法重建 protocol 指定的 memory 时评测失败；
- 人工构造但不可重放的 canonical trace 必须失败；
- 最新三个 smoke trace 可以用各自冻结产物完整重放；
- development report 才可把 execution provenance 从
  `declarative_membership_audit_only` 升级为
  `deterministic_replay_verified`；
- test evaluation 仍保持 fail-closed。

### N2. 补齐 C0 controls 与 oracle diagnostic

目的：

- 区分“adaptive retrieval 有价值”与“只要重复搜索原问题就够了”；
- 在训练或引入 LLM controller 前确认 memory 本身是否能够回答 pilot
  item；
- 为 RQ1、RQ2 和 RQ3 建立最小充分对照。

实现策略：

1. `fixed_dense`：一次 concept search 后检索 evidence。
2. `fixed_dense_typed_graph`：固定执行一次 typed expansion。
3. `query_only_iterative`：多轮检索，但每轮只使用原始问题，不读取
   knowledge-need state。
4. `rule_evidence_gap`：保留当前 public-cue 单 need 基线。
5. `oracle_diagnostic`：只在 development 诊断中读取 required concept/path
   标签，用来测 memory coverage 上界；不能作为可比较方法。

所有策略必须共享：

- 同一 frozen memory、query encoder、retriever 和 answerer；
- 同一 action schema、runner、verifier 和 trace contract；
- 同一 retrieval-call、context-character、prompt-token 和 latency budget；
- 同一失败、拒答和 no-progress 规则。

通过门槛：

- oracle 在宽松 development budget 下仍无法满足证据要求的 item，必须
  标记为 memory-unanswerable 并进入数据修订，而不能计为 controller
  失败；
- query-only iterative 与 stateful controller 的信息边界可以由测试
  明确区分；
- 每个 baseline 都通过相同 runner 生成 trace，而不是使用独立评测捷径；
- 不根据 confirmatory test 结果选择 budget 或停止阈值。

### N3. 构建独立的 36 题 development pilot

在 N1/N2 的代码工作进行时，可以并行准备数据，但 graph author、question
author、reviewer 和 adjudicator 必须保持角色独立。

数据规模：

- 12 个 true multi-hop item；
- 12 个 implicit-prerequisite item；
- 12 个 structured-unanswerable item；
- 在 prerequisite 题目编写前，先独立确认 12--20 条方向明确的
  prerequisite relations。

每个 item 必须包含：

- learning objective 与 question family；
- anchor、bridge/prerequisite、target concept roles；
- DNF evidence requirements；
- 一个或多个 typed directed valid paths；
- hard negatives 或 unanswerable failure certificate；
- modality 与 deterministic difficulty axes；
- authoring provenance、两个独立 field-level reviews 和必要时的第三方
  adjudication。

计划产物：

```text
docs/annotations/controller_pilot_authoring_guide.md
docs/annotations/controller_pilot_review_guide.md
backend/data/rag_lab/controller_v2/pilot-development-v1.json
backend/data/rag_lab/controller_v2/pilot-review-v1.json
docs/experiments/rag_controller_pilot_audit_v1.json
```

其中完整题目与 review 数据默认放在 ignored research-data 路径；只有不
泄露未来 test gold 的 compact audit summary 进入 Git。

通过门槛：

- 所有 gold concept/evidence/path ID 都解析到 N1 验证过的 frozen memory；
- split、family、evidence bundle、concept structure 和 path leakage audit
  全部通过；
- 每个字段获得两份独立 review，所有分歧均完成 adjudication；
- graph construction 不读取题目，question authoring 不以 retriever failure
  或现有 edge pair 为模板；
- 不让同一个 AI 或同一个人冒充两个独立 reviewer。

### N4. 实现真正的 C1 semantic Evidence-Gap Controller

只有 N2 证明 query-only control 存在可测缺口、N3 提供可信 pilot 后，才
进入 semantic C1。

方法边界：

- planner 只输出结构化 knowledge needs，不保存或评测自由文本
  chain-of-thought；
- planner 只能使用 question 和当前公开 state，不能读取 gold concept、
  path、reference answer 或评测标签；
- verifier 判断 evidence 是否支持某个 need，不能直接把“已检索”当成
  “已解决”；
- controller 在预算内选择 concept/evidence/graph/verify/answer/abstain。

首先实现：

1. constrained need planner；
2. schema-valid action selector；
3. need-specific semantic verifier；
4. deterministic repair/fallback；
5. prompt、model digest、temperature、seed、parse/repair/error 记录；
6. synthetic multi-hop、prerequisite、irrelevant-evidence、loop、budget 和
   abstention fixtures。

通过门槛：

- planner 输出只能通过 closed schema；
- gold-label access 测试明确失败；
- irrelevant evidence 不能把 need 标记为 supported；
- required needs 未满足时不能 answer；
- 所有 decision 都可由可见 state 与冻结 prompt/model provenance 复核；
- synthetic fixtures 全部通过后才运行 36 题 pilot。

### N5. 预注册并执行 equal-budget development experiment

比较系统：

```text
fixed_dense
fixed_dense_typed_graph
query_only_iterative
rule_evidence_gap
semantic_evidence_gap
oracle_diagnostic  # upper bound only
```

主指标：

- required-concept recall；
- DNF evidence-requirement recall；
- typed valid-path success；
- grounded answer correctness 与 citation validity；
- stop correctness；
- retrieval calls、context characters、prompt/completion tokens、latency；
- quality-cost frontier，而不是一个任意加权总分。

主要比较必须在运行前冻结：

- C1 vs fixed Dense；
- C1 vs query-only iterative；
- typed graph vs graph-free under equal budget；
- concept/evidence separation ablation；
- verifier、need state 和 stopping ablations。

统计与报告：

- 使用 paired item-level differences；
- 报告 bootstrap confidence intervals 和每个 slice 的原始分母；
- 保留 negative result、失败 trace 和 protocol deviation；
- 把 benchmark error、memory coverage、retriever error、controller error、
  verifier error 和 answerer error 分开归因；
- 不在 development 上反复选择多个版本后只汇报最好的一次。

通过门槛：

- N1 deterministic replay audit 通过；
- 36 题均完成独立 review/adjudication；
- 所有系统共享冻结 memory、model capacity 和 budget；
- 结果足以支持或反驳 H1--H4，而不是只展示案例；
- 未满足上述任一条件时，结果仍只作为 development diagnostic。

### N6. Paper-readiness decision

完成 N5 后只做一次明确决策：

1. 如果 adaptive controller 在等预算下对 implicit prerequisite 或 true
   multi-hop slice 有稳定增益，则扩展课程/lecture、建立独立 test，并
   进入 Phase 7 multimodal。
2. 如果只增加检索成本而没有 stateful gain，则停止扩展 C1，报告负结果，
   优先修订 benchmark、memory 或 verifier。
3. 如果 graph 只在 oracle path 下有效，则把贡献定位为 memory/graph
   coverage，而不是 controller reasoning。
4. 如果 rule controller 与 semantic controller 相当，则不进入 RL；
   先保留更简单、可解释的规则方法。

在这一决策之前明确不做：

- 不打开现有 60 题 test split；
- 不训练 C2/RL controller；
- 不把 multimodal、product UI 和 controller effect 混入同一主实验；
- 不把两个问题的 smoke 指标、oracle 指标或未复核 candidate labels
  写进论文结论。

### Immediate next coding batch

下一批实际代码按以下顺序执行：

1. N1 canonical evaluation bundle 与 memory reconstruction；
2. N1 Dense/BM25/graph observation replay 与 adversarial tests；
3. N2 `query_only_iterative` 和 `oracle_diagnostic` policies；
4. N2 shared-budget regression suite 与 development-only diagnostics；
5. N3 authoring/review templates 和 prerequisite-edge review sheet。

这批代码完成后再决定 semantic planner 使用哪一个本地/开源模型。模型
选择必须由结构化输出可靠性、可冻结性、推理成本和 license 决定，不能
先选模型再修改研究问题。
