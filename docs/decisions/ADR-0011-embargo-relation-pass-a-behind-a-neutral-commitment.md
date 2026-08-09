# ADR-0011: Embargo Relation Pass A Behind a Neutral Commitment

- **Status:** Accepted; implemented for the Pass A software boundary
- **Date:** 2026-08-09
- **Decision owners:** Project maintainer and Codex implementation agent
- **Scope:** Relation Pass A authoring, sealing, and pre-Pass-B visibility

## Context

The frozen golden-graph protocol requires Relation Pass B to remain blind to
Pass A labels and requires at least 72 hours between the two passes. It also
requires every Pass to judge the same exhaustive pair universe derived from a
previously sealed Concept inventory.

Publishing Pass A decisions immediately would reveal them to Pass B. Keeping
everything mutable and private until after Pass B would create the opposite
failure: the maintainer could replace Pass A after seeing Pass B and then sign
the replacement. A simple `complete=true` field cannot solve either problem.

The software also cannot prove that the signer is human, did not inspect model
output, or actually waited 72 hours. OpenSSH verifies control of a registered
key over exact bytes; it does not verify those process claims.

## Decision

### 1. Split hidden labels from the public commitment

Pass A uses this artifact DAG:

```text
ignored mutable RelationPassAWorksheet (contains exact quotes)
    -> ignored immutable RelationPassAArtifact (redacted labels)
        -> tracked neutral RelationPassASealRequest (artifact hash only)
            -> tracked DetachedKeyAttestationArtifact
                -> tracked RelationPassASeal (published last)
```

The private artifact contains every `none` or typed Relation judgment,
rationale, and redacted evidence span. It remains under
`backend/data/golden_graph/annotations/` until Pass B has been sealed.
The worksheet receives a cryptographically random 256-bit private nonce, and
the immutable private artifact retains it. Because both public hashes bind
that nonce without publishing it, low-entropy label combinations cannot be
tested against an unsalted public commitment. The nonce is binding/hiding
material, not proof of human review.

The three tracked leaves expose the Pass A artifact hash, worksheet hash,
upstream Concept/pair/protocol/Source hashes, registered reviewer-policy
identity, reviewer-declared completion time, the frozen 72-hour delay, and the
release policy. They do not expose positive/negative counts, Relation types,
directions, endpoint keys, evidence, or rationale.

### 2. Treat commitment fields as historical facts

Immutable commitment artifacts record
`labels_embargoed_at_commitment=true` and
`labels_unreleased_at_commitment=true`. They do not contain a mutable-looking
`labels_publicly_released=false` field that would become false after reveal.
Later release state belongs to a separate Pass B/GoldBundle artifact and
append-only history.

### 3. Require exact exhaustive decisions and G1-compatible evidence

The worksheet is initialized from `SealedConceptInventoryAuthority`, not from
caller-supplied hashes or a model-selected pair list. It contains all
`N * (N - 1) / 2` pairs in pair-manifest order. A complete worksheet has no
pending row.

Each positive judgment uses one registered Relation type and an unambiguous
direction. Symmetric endpoints are canonicalized, one pair has at most one
judgment per Relation type, and the complete prerequisite subgraph is acyclic.

Evidence follows the existing G1 contract exactly:

- `source_asserted` accepts only `relation_assertion` evidence;
- `pedagogical_inference` accepts both and only `source_endpoint` and
  `target_endpoint` evidence;
- inferred endpoint spans must equal evidence already sealed on the
  corresponding Concept.

When a future importer maps Concept keys to runtime Concept IDs, it must swap
`source_endpoint` and `target_endpoint` roles if canonicalization of a
symmetric Relation reverses the endpoint order. Otherwise the G1 evidence
fingerprint gate will correctly reject the import.

### 4. Reuse shared trust boundaries

Pass A directly consumes the G2 shared Source binder, exact-quote resolver,
public-span replay, aggregate privacy scanner, detached-attestation verifier,
reviewer-policy authority, canonical JSON writer, hash sidecars, immutable
publication, and crash-recovery rules. It does not implement parallel evidence
or cryptographic logic.

New authoring requires an active policy that authorizes the Pass A namespace.
Historical replay accepts a historical policy authority. The Concept seal may
reference an older Concept-only policy; the Pass A workflow separately binds
the active Relation policy so old Concept artifacts remain replayable.

The public commitment loader accepts a
`RelationPassAPublicCommitmentPaths` value, which has no private artifact path.
Full local replay requires the separate `RelationPassAStagePaths` capability.
Before a Relation transition consumes Concepts, it reloads the historical
Concept reviewer policy from its Git registration commit and replays the six
Concept leaves from repository-derived canonical paths. It consumes the fresh
replay, not the caller-owned in-memory authority. The frozen protocol JSON and
sidecar and the ignored private Source materialization and sidecar are also
reloaded from canonical repository paths; an in-memory Source receipt alone is
not sufficient authority for a new transition.

Publication first creates a detached, schema-reparsed snapshot of the signed
private/public object graph. It then replays the embedded SSH signature,
upstream lineage, Source privacy, evidence spans, and Concept membership before
the first durable write. All destinations must be pairwise distinct and all
leaves must preflight before publication; the neutral seal remains last.

### 5. Do not reveal Pass A in this checkpoint

There is deliberately no `reveal-pass-a` command. A future Pass B workflow
must first verify a reachable Git commit containing the neutral Pass A seal,
enforce the registered delay as far as software can, initialize B without
reading the private A artifact, and seal B. Only then may it publish the exact
canonical A bytes whose hash was committed earlier.

## Consequences

Benefits:

- normal Pass B tooling cannot read Pass A labels;
- Pass A cannot be changed after Pass B without breaking the earlier public
  hash and signature;
- Source quotes remain private while evidence stays replayable;
- a random private nonce makes the public commitment hiding as well as binding
  for the finite Relation label space;
- the public history clearly distinguishes Concept seal, Pass A commitment,
  Pass B, adjudication, and final gold;
- existing Concept-only historical policies remain valid for replay.

Costs and limitations:

- the maintainer must commit and push the neutral seal before beginning the
  delay;
- the private immutable artifact must be backed up until reveal;
- Git history and a self-declared timestamp do not cryptographically prove
  real elapsed time or reviewer behavior;
- the current short-source-copy thresholds still require a human rights and
  privacy diff review;
- this software boundary creates no real CS336 label, agreement score, gold
  graph, model accuracy, or path-quality result.

## Rejected alternatives

- **Publish Pass A labels immediately.** This violates Pass B blindness.
- **Keep all Pass A state private and unsigned.** Labels can be rewritten after
  seeing Pass B.
- **Encrypt labels in the repository.** Key handling adds another security
  system while a hash commitment already supplies binding without disclosure.
- **Let the application generate or review labels.** This contaminates human
  gold with system proposals.
- **Report signature/delay fields as proof of human blindness.** The software
  cannot establish those facts.

## Verification

The implementation gate requires synthetic tests for complete pair coverage,
direction and symmetry, evidence roles, Concept-evidence membership,
prerequisite cycles, Source/privacy replay, cross-stage signatures, policy
revocation/history, no-overwrite publication, seal-last recovery, worksheet
drift, and public/CLI label leakage. The frozen CS336 protocol, hash-bound
annotation guide, G0.2 derivation closure, Concept six-leaf DAG, pair-ID
algorithm, and existing canonical vectors remain unchanged.

The gate also includes real-capability integration tests for canonical frozen
protocol bytes, ignored Source materialization, Git-historical Concept policy,
active/revoked Relation policy, OpenSSH replay, canonical Concept paths,
caller-object mutation after validation, pairwise path collisions, batch
preflight, seal-last crash recovery, historical verification after revocation,
and static CLI error redaction. These tests establish process/software
integrity only; they do not manufacture semantic labels or a 72-hour fact.
