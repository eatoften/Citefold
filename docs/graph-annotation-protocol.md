# Graph Annotation Protocol

- **Status:** Draft for G0.2; human protocol not frozen
- **Date:** 2026-08-09
- **Engineering/schema owners:** Project maintainer and Codex implementation agent
- **Human annotation authority:** Project maintainer
- **Architecture:** [ADR-0008](decisions/ADR-0008-evidence-grounded-concept-graph-and-deterministic-paths.md)

## Purpose

This protocol turns original course evidence into one bounded, human-reviewed
Concept graph. It exists to make Concept identity, relation direction,
evidence, review, and evaluation repeatable before G2-G4 treat a bounded graph
as an evaluated product authority. G1 already provides the storage, lifecycle,
current-evidence, and immutable-publication substrate.

The human annotation protocol is not yet frozen. The executable G0.2
[evaluation protocol boundary](modules/golden-graph-evaluation-protocol.md)
now freezes machine-checkable schemas, numeric targets, claim scope, and Source
lineage rules, but it intentionally cannot choose the Lecture 3 page scope or
write human labels. The maintainer still owns that Source decision, the example
worksheet, both delayed passes, and adjudication.

## Claim boundary

The first golden graph is an engineering fixture. It may establish that a
bounded graph is internally consistent, traceable, and suitable for testing
deterministic algorithms. It does not establish:

- improved learning outcomes;
- superiority over NotebookLM;
- a large-scale production knowledge graph;
- that graph expansion improves every Chat query;
- that model suggestions are human truth.

## Sealed evaluation access

The G2 Lecture 3 graph is authoring material, not a held-out model benchmark.
Any later sealed-transfer protocol must record these events in order:

1. freeze the runner, model, prompt, thresholds, and evaluation rules;
2. record `source_annotation_open` when human annotators first read the Source,
   while system predictions and outcomes remain unavailable;
3. record `gold_sealed` after prediction-blind labels and adjudication freeze;
4. permit exactly one `prediction_evaluation_open` for the registered final run;
5. label every later execution as a reproduction, never a tuning opportunity.

In this contract, held out means that predictions and outcomes are not exposed
before the registered evaluation opening. It does not pretend that a human can
create Source-grounded gold labels without reading the Source. The future
append-only ledger implementation must reject an evaluation opening before
`gold_sealed`, a gold seal after predictions were exposed, and a second final
opening.

## Annotation unit and frozen input

Before G2 annotation begins, freeze:

- one `course_id`;
- the included public logical Source-page/Chunk identities, semantic content
  hashes, and byte-offset Locators;
- the exact Source locations available to annotators;
- the selected course slice and exclusion rules;
- an artifact manifest hash.

The public annotation bundle stays independent of product UUIDs and database
state. Before any accepted graph is materialized through G1, a separate private
receipt must seal the mapping from those logical identities to the concrete
runtime Source revision, projection generation, and Chunk IDs. That mapping is
validated against current Source authority but is never allowed to redefine
the public semantic hash.

Annotations made against an earlier Source revision remain historical but
become `stale` when their referenced revision is no longer active. Correctness
does not depend on an asynchronous stale-marking job: acceptance and graph
publication compare the referenced revision/hash with current Source state.

## Entity contract

### Source, Chunk, and Locator

`Source` identifies original user material or an explicitly published authority.
`CourseSource`, `Chunk`, and `Locator` are validated derived projections or
addresses over one specific Source revision; they are not independent facts.
Their role is to preserve a stable, verifiable route back to the authority.

### Card

A Card is a derived learning artifact that may compress several Concepts. It
can help locate a candidate Concept but cannot satisfy ConceptEvidence or
RelationEvidence by itself.

### Topic

A Topic is course navigation/curriculum structure. It can contain many
Concepts. It is not automatically a Concept and does not imply prerequisite
direction.

### Concept

A Concept is a stable, course-scoped, teachable knowledge identity. A valid
candidate should:

- name one idea a learner could explain or misunderstand independently;
- have a concise preferred name and optional aliases;
- be neither a full Card/question nor an arbitrary isolated phrase;
- have at least one locatable Source evidence item before becoming
  `accepted + current`;
- retain stable identity across renaming; merge/split uses explicit history.

Required annotation fields:

```text
preferred_name
short_definition
aliases[]
review_status
validity_status
evidence[]
annotation_notes
```

## Relation vocabulary and direction

| Type | Shape | Stored meaning |
| --- | --- | --- |
| `prerequisite` | directed | `A -> B`: understand A before studying B |
| `part_of` | directed | `A -> B`: A is a component/sub-concept of B |
| `example_of` | directed | `A -> B`: A is an example or instance of B |
| `related` | symmetric | A and B have a specific reviewed association that is not better represented by another type |
| `contrast_with` | symmetric | A and B form a meaningful comparison or opposition |

Symmetric relations are stored once using canonical endpoint ordering.
Different relation types may connect the same pair when each is independently
supported. A generic `related` edge must not be added only because cosine
similarity is high, two items occur nearby, or an LLM proposed it.

## Independent annotation axes

These fields answer different questions and must not be collapsed into one
`status` or `provenance` value.

### Review status

```text
candidate | accepted | rejected
```

Question answered: what did the reviewer decide about this revision?

### Validity status

```text
current | stale | tombstoned
```

Question answered: is its evidence/revision eligible now?

An accepted-but-stale revision remains auditable but cannot enter an
authoritative graph version.

### Support basis

```text
source_asserted | pedagogical_inference
```

Question answered: did a Source assert the relationship, or did a reviewer
infer a justified teaching dependency from supported endpoints?

### Proposal origin

```text
human | model | import
```

Question answered: who or what first proposed the record? Acceptance never
rewrites this history.

### Review record

```text
review_actor
reviewed_at
review_revision
review_rationale
```

Model proposals additionally retain provider/model identity and prompt/protocol
version.

## Evidence contract

Every evidence item ultimately references a versioned original Source Chunk or
immutable Source snapshot and records a quote/hash/locator suitable for the
server-owned resolver.

Relation evidence has one `support_role`:

```text
relation_assertion
source_endpoint
target_endpoint
```

Acceptance requirements:

- an accepted/current Concept has at least one current locatable ConceptEvidence;
- a `source_asserted` relation has at least one `relation_assertion` evidence item;
- a `pedagogical_inference` has current `source_endpoint` and `target_endpoint`
  evidence plus a reviewer-authored rationale;
- inferred relations are labeled as inference in the UI and never presented as
  quotations from the Source;
- a Card ID may be secondary provenance but is not authoritative evidence.

## Decision procedure

For every proposed Concept:

1. Read the surrounding original Source context, not only a generated Card.
2. Decide whether the phrase is one stable teachable idea at the selected scope.
3. Search the draft inventory for an existing Concept or alias.
4. Attach exact current Source evidence.
5. Record candidate/accepted/rejected review status separately from validity.

For every human relation judgment under review:

1. Normalize the endpoint Concepts first.
2. Ask whether one of the five typed relations has a precise meaning here.
3. Fix direction before reading model explanations or path results.
4. Choose `source_asserted` only when evidence asserts the relation itself.
5. Otherwise require both endpoint evidence and a pedagogical rationale.
6. Reject self-loops, duplicate relation keys, missing endpoints, cross-course
   edges, unsupported generic relatedness, and ambiguous direction.
7. Reject a prerequisite candidate that creates a cycle.
8. Keep rejected human judgments/candidates and rationale for
   deduplication/audit.

“Candidate” in this annotation section means a human-authored decision being
reviewed. Both gold passes are blind to automatic Understanding/system
proposals; those predictions are generated and scored only after the gold
bundle is sealed.

## Two-pass review

The minimum solo-project protocol is:

1. **Concept inventory:** author and normalize evidence-bound `C_gold`, resolve
   aliases/granularity, then freeze its hash before generating relation work.
   Do not report temporal or inter-reviewer Concept agreement from this one
   maintainer-owned authoring step.
2. Enumerate the complete unordered `N * (N - 1) / 2` pair universe from that
   fixed inventory; no system output is included in the packet.
3. **Relation Pass A:** independently label every pair with `none` or the
   supported typed/directed relation set and record rationale.
4. Wait for the registered delay without studying the original decisions.
5. **Relation Pass B:** re-label the same frozen pair universe while blind to
   Pass A and to all system proposals.
6. Seal Pass B, record temporal intra-rater agreement/disagreements, and only
   then reveal Pass A.
7. Adjudicate each relation disagreement and record the final reason.
8. Seal the gold bundle before final graph materialization/publication, path
   evaluation, or automatic proposal generation.

If a second human reviewer is available, report inter-reviewer agreement
separately. A second human is a strong portfolio addition but is not fabricated
or silently replaced by an LLM review.

## Quality measures

G0.2 freezes the exact formulas, point thresholds, interval rules, and
lower-confidence-bound floors before evaluation. The contract uses:

- `C_gold`: frozen key-Concept inventory;
- `R_gold`: adjudicated normalized relation tuples;
- `C_live`: accepted/current Concepts in the evaluated published graph;
- inventory coverage: `|C_live intersect C_gold| / |C_gold|`;
- isolate rate: `|{c in C_live: accepted/current degree(c) = 0}| / |C_live|`;
- `R_live`: accepted/current normalized `(type, source, target)` tuples in the
  evaluated published graph;
- exact edge precision: `|R_live intersect R_gold| / |R_live|`;
- exact edge recall: `|R_live intersect R_gold| / |R_gold|`;
- prerequisite direction accuracy reported alongside exact prerequisite precision;
- Concept/edge evidence-locator validity;
- Relation Pass A/Pass B temporal agreement and disagreement categories;
- proposal-generation quality reported separately from curated graph quality.

When G2 materializes the same sealed gold bundle through the G1 publication
boundary, the `C_live`/`R_live` comparisons above measure import fidelity and
graph integrity. They are not model accuracy. A later automatic Understanding
run must preserve its unedited `C_pred`/`R_pred` outputs in a separately frozen
run bundle and score those predictions against gold before any human editing.

## Session 1 maintainer worksheet

Do not ask Codex to fill these rows before the first attempt. Use Concepts from
one course area you understand well. Evidence may be described provisionally in
Session 1; exact logical Source/Chunk hashes and Locators are added when G2
scope is selected, while runtime revision/projection IDs remain in the later
private materialization receipt.

| ID | Intended decision | Concept A | Relation | Concept B | Direction explanation | Support basis | Required evidence roles | Reviewer rationale |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S1-01 | Accept | _maintainer input_ | _maintainer input_ | _maintainer input_ | _maintainer input_ | _maintainer input_ | _maintainer input_ | _maintainer input_ |
| S1-02 | Accept | _maintainer input_ | _maintainer input_ | _maintainer input_ | _maintainer input_ | _maintainer input_ | _maintainer input_ | _maintainer input_ |
| S1-03 | Accept | _maintainer input_ | _maintainer input_ | _maintainer input_ | _maintainer input_ | _maintainer input_ | _maintainer input_ | _maintainer input_ |
| S1-04 | Reject as ambiguous/unsupported | _maintainer input_ | _proposed type_ | _maintainer input_ | _why direction/type is unsafe_ | _what was claimed_ | _what is missing_ | _rejection rationale_ |

### Session 1 acceptance questions

Use the canonical `S1-Q1` through `S1-Q5` question set in the
[Session 1 lesson](learning/session-01-source-card-graph-contract.md#closed-book-questions).
The worksheet is accepted only after the maintainer can answer those questions
and correct the examples without relying on a prewritten solution.

## Freeze checklist

G0.2 may change this document from Draft to Frozen only when:

- the maintainer has completed and defended the Session 1 worksheet;
- the selected course slice and Source revision manifest are explicit;
- Concept granularity and relation edge cases have adjudicated examples;
- formulas, point thresholds, interval rules, lower-confidence-bound floors,
  delay, reviewers, artifact paths, and latency budget are registered;
- no path/evaluation result has been viewed before thresholds are frozen;
- the change has tests/document checks, a stage commit, and confirmed remote push.
