# ADR-0008: Build an Evidence-Grounded Concept Graph Before Expanding Studio

- **Status:** Accepted; implementation pending G0-G4
- **Date:** 2026-08-08
- **Decision owners:** Project maintainer and Codex implementation agent

## Context

P0.0-P0.5 and P1.1 established a Source-first course notebook with:

- canonical `Source / Chunk / Locator` evidence;
- persistent grounded Chat and server-owned citation snapshots;
- Notes that can be deliberately published as immutable Sources;
- durable tasks, drafts, Trash, backup/restore, and desktop recovery;
- cards, Topic hierarchy, Study, Review, Course Map, and Explore.

The next stage was originally P1.2, a unified Studio. That work remains useful,
but it would package the current graph model before the model can support the
product direction the maintainer now wants: following a concept through
explainable relationships and constructing a reliable prerequisite path.

The existing graph is a Card-level discovery prototype, not a canonical
Concept graph:

- the frozen research corpus has 118 Cards and 20 model-assisted candidate
  accepted edges, not an independently reviewed production graph;
- those edges cover 32 of 118 Cards (`27.1%`), leaving 86 isolated Cards;
- only two edges have type `prerequisite`;
- there is no stable Concept identity, Concept-level evidence, edge-level
  evidence bundle, Source-change invalidation, prerequisite cycle check, or
  deterministic path engine.

The retrieval experiment also does not justify using this graph for every Chat
question. Dense retrieval achieved `0.924` nDCG@5, while Dense plus the
candidate graph achieved `0.852`. On one eight-question multi-hop development
slice, graph expansion improved joint Recall@3 from `0.750` to `0.875`, but the
generation experiment did not establish a reliable downstream multi-hop gain.
These are exploratory results with candidate labels.

Therefore the graph's immediate product responsibilities are knowledge
organization, relationship tracing, and prerequisite ordering. Dense Source
retrieval remains the default Grounded Chat mechanism. A future graph-assisted
Chat policy requires a separate experiment and decision.

ADR-0001's Source-first rule still applies. A Concept, relation, or path is a
derived interpretation. Factual support must return to an original Source
Chunk or an immutable Source snapshot.

## Decision

### 1. Reorder delivery around G0-G4

P1.2-P1.4 are deferred, not cancelled. The active program is G0-G4: establish
an evidence-grounded Concept model, a human-reviewed reference graph,
deterministic graph algorithms, and a stable evidence-first path experience.

This sequencing does not defer release-blocking reliability or security fixes.
Every G stage still requires scoped design, tests, manual acceptance,
documentation, an independent commit, and a confirmed remote push.

### 2. Add a canonical Concept layer

`Concept` is a stable, course-scoped knowledge identity across Sources, Chunks,
Cards, and Topics. It is not a renamed Card or Topic:

- one Card may contain several Concepts;
- one Concept may be associated with several Cards and supported by several
  versioned Source Chunks or immutable Source snapshots; Card associations
  never satisfy the evidence invariant;
- Topic represents curriculum organization, not knowledge identity;
- changing a Concept's preferred name does not change its ID;
- previous or alternate names are retained as aliases.

The logical model adds:

```text
Concept
ConceptAlias
ConceptEvidence
ConceptRelation
RelationEvidence
```

The initial SQLite implementation is additive. Existing Card, Topic, and
CardRelation data remains readable while the new model is evaluated.

### 3. Store typed, evidence-grounded relations with explicit directionality

The first relation vocabulary is deliberately small and has explicit
semantics:

| Type | Shape | Meaning of `A -> B` |
| --- | --- | --- |
| `prerequisite` | directed | understand A before studying B |
| `part_of` | directed | A is a component or sub-concept of B |
| `example_of` | directed | A is an example or instance of B |
| `related` | symmetric | A and B have a reviewed, non-directional association |
| `contrast_with` | symmetric | A and B form a reviewed comparison or opposition |

Symmetric relations are stored once with canonical endpoint ordering. In
particular, the prerequisite rule is:

```text
A --prerequisite--> B
```

means that a learner should understand A before studying B.

Every relation revision stores its type, endpoints, rationale, evidence bundle,
and review metadata. Provenance is not one enum because several facts may be
true at once. It uses independent axes:

```text
support_basis   = source_asserted | pedagogical_inference
proposal_origin = human | model | import
review_actor + reviewed_at + review_revision
```

Model-generated proposals also retain provider, model, prompt/protocol, and
output version. RelationEvidence records `support_role` as
`relation_assertion`, `source_endpoint`, or `target_endpoint`.

A `source_asserted` relation requires at least one current
`relation_assertion` locator. A `pedagogical_inference` requires current
evidence for both endpoints plus a reviewer-authored rationale. The latter is
visibly labeled as inference and is never presented as a quotation from a
Source.

Concept-to-Card associations are derivation or membership links, not factual
authority. Every evidence record used by an accepted/current Concept or
relation ultimately references a versioned original Source Chunk or immutable
Source snapshot. A Card ID may remain only as secondary provenance.

### 4. Keep proposal and truth separate

Embeddings and an LLM may suggest Concepts, aliases, relation types, and
rationales. Review and evidence validity are orthogonal:

```text
review_status   = candidate | accepted | rejected
validity_status = current | stale | tombstoned
```

These state axes apply independently to Concept revisions and ConceptRelation
revisions. Aliases inherit the owning Concept revision unless an alias is
explicitly retired during merge or normalization.

Only an `accepted + current` revision enters an authoritative graph. Changes
are revisioned so becoming stale does not erase prior acceptance. Rejected
proposals remain recorded so the same low-quality edge is not suggested
repeatedly.

One current relation is unique by `(course_id, relation_type,
normalized_source_id, normalized_target_id)`. Symmetric endpoints are
canonicalized; different relation types may connect the same pair. A proposal
fingerprint also includes support basis and evidence revision, so retries
deduplicate while genuinely changed evidence can create a new candidate.

Source currentness is checked against immutable Source/Chunk revision IDs and
content hashes at acceptance and query time. Advancing the active Source
revision therefore makes dependent evidence ineligible immediately. A
background job may materialize stale labels, but path correctness never waits
for that job.

Concepts have `active`, `merged`, and `retired` identity states plus revision
compare-and-swap. Merging a duplicate records a redirect to the surviving ID
and revalidates aliases, evidence, and incident relations; it never silently
reuses or hard-deletes an ID. Splitting creates new IDs and requires explicit
evidence/relation reassignment. Cross-course Concept identity resolution is
outside G0-G4.

Authoritative reads use immutable, course-scoped published graph versions.
Review changes are made against a draft revision; publication atomically
captures the accepted/current Concept, relation, and evidence revisions as the
next `graph_version`. Every path request and response names one version, and
cached adjacency is keyed by it. If its Source evidence is no longer current,
the version remains historically auditable but is not served as the current
authoritative graph; the product requests re-review and publication rather
than silently pruning a path.

Before publication, the complete proposed snapshot is revalidated inside one
SQLite write transaction: Concept review/validity state, active Source
revisions, support-role evidence, relation uniqueness, endpoint integrity, and
prerequisite acyclicity. The same transaction records the immutable graph
version. No accepted draft revision is authoritative before publication
succeeds.

### 5. Generate authoritative paths with deterministic algorithms

The LLM may explain a computed path in natural language, but it does not invent
the authoritative path. The first engine provides:

- **Local Graph:** type- and status-filtered N-hop breadth-first traversal;
- **Relationship Trace:** an explainable A-to-B shortest-hop path using
  breadth-first search, with deterministic tie-breaking for equal-length paths;
- **Learning Path:** reverse prerequisite ancestor closure, including the
  target, followed by topological layers and one stable Kahn linearization.

For the same graph version, inputs, and filters, ordered node/edge IDs must be
identical. Stable IDs or explicit positions break ties. Adjacency and tie ranks
are normalized once when a graph version is materialized, with its sorting
cost reported separately; traversal over that immutable representation targets
`O(V + E)` time.

Trace requests select relation types and `direction_mode = outgoing | incoming
| both`; the default is `outgoing`. Symmetric edges behave identically in both
directions. APIs enforce registered `max_hops`, `max_nodes`, and latency budgets
with deterministic truncation. Equal-hop paths use a documented relation-type
priority and stable IDs. BFS finds a shortest-hop trace, not necessarily the
semantically strongest explanation.

Learning Path always follows accepted/current prerequisite direction.
Topological layers communicate actual precedence; the linearization is only a
stable presentation order for otherwise incomparable Concepts, not a claim of
one uniquely optimal pedagogy.

### 6. Keep the accepted prerequisite subgraph acyclic

Accepting a prerequisite edge and publishing a graph version acquire SQLite
write serialization with `BEGIN IMMEDIATE`. A course-scoped service lock may
reduce contention but cannot replace the database transaction. Immediately
before publishing the accepted revision, the transaction rechecks endpoint activity,
evidence currentness, uniqueness, expected revisions, and acyclicity. A stale
concurrent write returns a conflict and never partially publishes. An edge that
would introduce a cycle cannot enter the accepted prerequisite graph. Other
relations such as `related` and `contrast_with` may form cycles, but they do not
participate in the topological Learning Path.

### 7. Build and freeze one human-reviewed golden course graph first

Before automatic expansion, G2 creates one bounded reference graph. The
initial target is 12-20 canonical Concepts and 20-35 reviewed relations from
one course slice. Every accepted/current Concept and relation receives locatable
evidence and a rationale. The annotation guide, adjudication decisions, data
hash, and graph version are frozen before path evaluation.

For a solo-maintained project, the minimum review protocol is two-pass blinded
review: initial annotation, a delayed second pass without seeing the first
decision, then logged adjudication. If a second human reviewer is available,
inter-reviewer agreement is reported separately. A single immediate pass is not
described as independent human review.

This graph is an engineering and evaluation fixture. It does not prove a
large-scale knowledge graph or improved learning outcomes.

### 8. Use different views for exploration and paths

The force-directed Explore view remains useful for open-ended overview. Local,
Trace, and Learning Path views require a stable, left-to-right layered layout
so the same result does not visually rearrange between visits.

Selecting any node or edge opens its Concept metadata, relation rationale, and
evidence. Source navigation continues through ADR-0004's server-authoritative
resolver; the frontend does not trust a stored local path or reconstruct a
durable locator itself.

### 9. Keep SQLite until measured evidence requires another database

At the current course scale, normalized SQLite tables plus an in-memory
adjacency representation provide transactions, constraints, backup/restore,
and `O(V + E)` traversal without a second database runtime. Neo4j or another
graph database requires a future ADR backed by measured query or scale limits.

## Alternatives considered

### Continue with P1.2 Studio first

Deferred. Studio would improve presentation but would not solve Concept
identity, edge evidence, direction, invalidation, or path correctness. Existing
Studio tools remain available during G0-G4.

### Treat Cards as Concepts

Rejected. Cards are compressed learning artifacts with unstable granularity;
Card and Concept have a many-to-many relationship.

### Treat Topics as Concepts

Rejected. Topics express course organization. A Concept may occur under
multiple Topics and across multiple Sources.

### Use semantic similarity as the graph

Rejected. Similarity does not provide a stable relation type, direction, or
prerequisite constraint and therefore cannot imply learning order.

### Let an LLM generate the graph and path end to end

Rejected as an authoritative workflow. It is non-deterministic, can invent
relations, and cannot guarantee an acyclic prerequisite graph. The LLM remains
useful for candidate generation and explanations.

### Replace the graph renderer first

Rejected. The blocking issue is semantic and evidential reliability, not how
nodes are drawn. Rendering choices follow the Local/Trace/Path requirements.

### Adopt Neo4j immediately

Deferred. It would add deployment, backup, packaging, and consistency
complexity before the project has measured a SQLite bottleneck.

### Use graph expansion for every Chat query

Rejected. The recorded development experiment reduced overall ranking quality,
and no downstream answer benefit has been established.

### Build a personalized learner model first

Deferred. Learner-conditioned ordering depends on a correct, directed,
traceable base graph. G0-G4 builds the static reliable baseline first.

### Build the production graph entirely by hand

Rejected as the long-term workflow, although the G2 reference graph is manually
reviewed. Production growth will use model suggestions, deterministic checks,
and human acceptance.

## Invariants

1. Concept, evidence, and relation reads and writes are isolated by `course_id`.
2. A Concept's stable ID does not change when its preferred name or aliases change.
3. Concept, Card, Topic, and Source IDs are never implicitly interchangeable.
4. An `accepted + current` Concept has at least one current locatable
   `ConceptEvidence` record. Accepted-but-stale revisions retain audit history
   but are not authoritative.
5. Both endpoints of an `accepted + current` relation are accepted/current
   Concepts in the same course.
6. An `accepted + current` relation has a type, valid direction, support basis, proposal
   origin, review record, rationale, and evidence bundle.
7. Anything other than `review_status=accepted` and
   `validity_status=current` is excluded from authoritative paths.
8. Self-loops, duplicate relation keys, orphan edges, and cross-course edges are invalid.
9. The accepted prerequisite subgraph contains no directed cycle.
10. Source evidence changes trigger revalidation before affected entities re-enter a current path.
11. Every path step uses accepted, current relations matching the request filters.
12. A published graph version is an immutable set of specific Concept,
    relation, and evidence revisions; path requests and responses report it.
13. Identical published versions and inputs produce identical ordered node and
    edge IDs or canonical result hashes, excluding request timestamps and
    transport metadata.
14. Node and edge evidence navigation goes through the server-owned citation/source resolver.
15. Proposal and review provenance is retained after acceptance.
16. Grounded Chat keeps Dense Source retrieval by default until a separate gate changes it.
17. Learner state does not affect path ranking before G4 accepts the static path baseline.

## Validation gates

### Migration and compatibility

- clean installation and upgrade from the current schema both pass;
- Card, Topic, Source, Chat, Note, task, and recovery data is not reset;
- a failed migration leaves no partially published schema;
- the existing CardRelation and Explore features remain compatible until an
  explicit replacement gate.

### Data integrity

- 100% of accepted/current Concepts have current locatable evidence;
- 100% of accepted/current relations have all provenance axes, rationale, and the
  support-role evidence required by their support basis;
- accepted-but-stale revisions retain historical evidence and review history
  but are absent from authoritative graph versions;
- self-loop, duplicate, cross-course, missing-endpoint, and accepted
  prerequisite-cycle counts are all zero;
- stale evidence is excluded from current paths while historical snapshots
  remain auditable.

### Golden graph

- before annotation, freeze the selected Source revisions and a key-Concept
  inventory `C_gold`; every unordered Concept pair in the bounded inventory is
  judged as none or one/more typed relations, with direction where applicable,
  to produce adjudicated `R_gold`;
- inventory coverage is `accepted/current Concepts matched to C_gold / |C_gold|`;
- isolate rate is `accepted/current Concepts with zero accepted/current
  incident edges / accepted/current Concepts`;
- exact edge precision and recall compare normalized `(type, source, target)`
  tuples in the evaluated graph with `R_gold`; symmetric endpoints are
  canonicalized;
- prerequisite direction accuracy is reported conditionally for endpoint pairs
  adjudicated as prerequisite, alongside exact prerequisite precision so false
  prerequisite claims cannot be hidden;
- proposal precision is evaluated separately against a frozen labeled proposal
  set; it never describes the manually adjudicated reference graph itself;
- two-pass agreement, disagreements, and adjudication are recorded before the
  fixture hash is frozen; if two humans participate, inter-reviewer agreement
  is reported separately;
- formulas, thresholds, reviewer protocol, and threshold owner are registered
  before path results are viewed. The initial structural targets are at least
  80% inventory coverage and at most 15% isolates, without adding unsupported
  edges to hit either metric.

### Algorithms and traceability

- unit tests cover BFS filters, unreachable nodes, multiple equal paths,
  cycles, duplicate edges, missing edges, and stable tie-breaking;
- every Learning Path obeys all accepted prerequisite constraints;
- repeated execution against a frozen graph produces the same ordered node and
  edge IDs and canonical result hash; volatile transport metadata is excluded;
- every node and edge in the golden paths opens the recorded video time, page,
  slide, paragraph, section, or immutable snapshot.

### Non-regression and claims

- the P0/P1.1 backend, frontend, build, and desktop gates continue to pass;
- Dense Chat retrieval, citations, and abstention do not regress because of G0-G4;
- G0 freezes a target corpus size and latency budget before performance is claimed;
- G4 may claim path correctness, reproducibility, and traceability only on the
  bounded golden course; it may not claim improved learning outcomes or
  superiority over NotebookLM without an independent user study.

## Delivery phases

### G0 - Contract, baseline, and evaluation freeze

- accept this ADR and resequence the roadmap;
- merge the seven verified product-core commits into `main` when the remote is reachable;
- freeze terminology, relation directions, lifecycle, annotation protocol,
  thresholds, performance budget, and non-goals;
- record the existing Card graph and retrieval baselines without upgrading
  their research status.

### G1 - Evidence-grounded graph substrate

- additive schema migration;
- Concept, Alias, ConceptEvidence, Relation, and RelationEvidence store/service/API;
- orthogonal review/validity lifecycle, relation-evidence support roles,
  Source-revision currentness checks, and Concept merge/retirement history;
- immutable published graph versions, draft revision CAS, and version-keyed adjacency;
- prerequisite cycle validation and acceptance occur under the same
  `BEGIN IMMEDIATE` transaction as complete-snapshot evidence/currentness/
  uniqueness/endpoint rechecks and atomic graph-version publication;
- isolation, constraint, idempotency, concurrency, migration, and recovery tests.

### G2 - Human-reviewed golden course graph

- normalize Concepts and aliases for one bounded course slice;
- map Chunks/Cards to Concepts;
- annotate typed directed/symmetric edges, support-role evidence, rationale,
  and provenance axes;
- complete two-pass blinded review, adjudicate, version, freeze, and report the graph.

### G3 - Deterministic traversal and path engine

- bounded N-hop Local Graph and typed/directional A-to-B Relationship Trace;
- prerequisite ancestor closure, cycle detection, topological layers, and one
  stable presentation linearization;
- versioned evidence-bearing path DTOs/APIs, deterministic truncation, and
  algorithm correctness/complexity tests.

### G4 - Product integration and graph quality gate

- separate Overview, Local, Trace, and Learning Path experiences;
- stable layered path layout and node/edge evidence navigation;
- candidate review/edit workflow;
- browser, desktop, narrow-screen, keyboard, accessibility, performance,
  integrity, graph-quality, and complete non-regression acceptance.

After G4, the project resumes P1.2 Studio and decides separately whether to add
learner-conditioned paths or graph-assisted Chat.

## Consequences

Positive:

- gives the project a distinct, verifiable path capability instead of another
  superficial Notebook clone feature;
- reuses the strongest Source, citation, task, and recovery foundations;
- makes graph behavior reproducible, testable, and debuggable;
- creates a coherent MLE/SDE portfolio story across data modeling, graph
  algorithms, human-in-the-loop ML, evaluation, APIs, and UI;
- provides a trustworthy substrate for later personalization research.

Costs and risks:

- Studio consolidation, onboarding, and public release are delayed;
- Concept normalization and relation review require human judgment;
- Source changes introduce invalidation and re-review work;
- the additive schema, review API, graph engine, and stable path UI expand scope;
- a small reference graph proves bounded correctness, not production scale or
  educational effectiveness.

The mitigation is to keep each phase small, additive, independently tested,
and reversible, and to retain the current Explore experience until G4 passes.

## Related records

- [Active product roadmap](../roadmap.md)
- [Project mastery plan](../project-mastery-plan.md)
- [Draft graph annotation protocol](../graph-annotation-protocol.md)
- [Technical-stack learning notebook](../learning/README.md)
- [Graph as associative knowledge structure](../Graph%20as%20associative%20knowledge%20structure.md)
- [RAG retrieval and graph study](../RAG%20retrieval%20and%20graph%20study.md)
- [ADR-0001: Source-first local course notebook](ADR-0001-source-first-local-course-notebook.md)
- [ADR-0004: Server-authoritative citation navigation](ADR-0004-server-authoritative-citation-navigation.md)
