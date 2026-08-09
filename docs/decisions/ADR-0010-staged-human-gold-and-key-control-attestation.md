# ADR-0010: Stage Human Gold and Treat SSH Signatures as Key Control Only

- **Status:** Accepted and implemented for G2.1
- **Date:** 2026-08-09
- **Decision owners:** Project maintainer and Codex implementation agent
- **Scope:** Concept-inventory handoff only; Relation annotation and
  `GoldBundleSeal` remain future G2 work

## Context

G0.2 froze a reproducible CS336 Lecture 3 Source slice, but a deterministic
Source does not create human semantic labels. G2 still needs a closed-world
Concept inventory, complete Relation pair universe, delayed Relation passes,
adjudication, and a final gold bundle.

Publishing only one final artifact after every Relation decision would leave
no durable boundary between Concept normalization and Relation annotation.
Conversely, treating an in-process `human=true` field or an SSH signature as
proof of human review would create authority that the software cannot verify.
Committing the private worksheet would also redistribute Source quotes.

## Decision

### 1. Freeze Concepts before Relation judgments

G2 is split into at least two authorities. G2.1 may publish a
`ConceptInventorySeal` and the complete deterministic pair manifest. A later
stage owns Relation Pass A/B, adjudication, semantic graph export, and the
separate `GoldBundleSeal`.

The G2.1 seal carries the machine-readable status
`concept_inventory_only_not_gold_bundle`. No consumer may interpret it as a
completed gold graph or as permission to report graph/path accuracy.

### 2. Keep Source-bearing authoring private and publish redacted commitments

The mutable worksheet and prepared candidates live under ignored
`backend/data/`. Exact quotes are resolved against the frozen private Source
materialization, then replaced by logical Chunk/page identities, half-open
UTF-8 byte offsets, and hashes. Only the six redacted Concept-stage leaves and
their sidecars may enter the public artifact tree.

### 3. Register the reviewer key before annotation

An allowed-signers file supplied together with its own signature is
self-authorizing: any fresh key could satisfy it. Before Concept initialization,
the reviewer public-key policy and sidecar must therefore be committed as
canonical tracked files. A strict active loader proves that the exact policy
bytes match a named reachable ancestor commit, current `HEAD`, the index, and
the working tree, then binds protocol, reviewer, namespace, allowed-signers
hash, and Ed25519 fingerprint into a `ReviewerKeyPolicyAuthority`.

The worksheet, inventory, seal request, and seal bind both the policy hash and
registration commit. Signing and reload compare verified signature metadata
to this prior trust root. Policy registration is repository governance; it is
still not real-world identity proof.

Removing the policy from current `HEAD` revokes authority for new annotation
and signing. A separate historical loader reconstructs the policy directly
from the reachable registration commit so an already sealed artifact remains
verifiable without reactivating the key.

### 4. Require external approval without overstating identity

The application does not generate or hold the signing key. It verifies a
detached OpenSSH Ed25519 signature over the exact canonical seal request and
publishes the public verification material. This attests control of an allowed
key. It does not authenticate human actor kind, real-world identity,
prediction blindness, or truthfulness of a declaration.

The artifact schemas and CLI receipts preserve that limitation explicitly.
Prediction blindness is likewise stored as a reviewer declaration alongside
`software_authenticated_prediction_blindness=false`, never as a fact inferred
by the software.
The first single-maintainer workflow is reported as self-attested. A future
second-human or independent-review claim requires external evidence rather
than a new Boolean field.

### 5. Re-derive before publication and reload after publication

Preparation writes redacted private candidates for inspection. Sealing
re-parses the current worksheet, re-derives all three candidates, and compares
their exact values/hashes before accepting a signature. It then derives the
attestation, seal, and complete pair universe, publishes with no-overwrite
semantics, and reloads the entire DAG against the frozen protocol and private
Source authority.

## Consequences

Positive consequences:

- Relation reviewers receive a fixed, exhaustive pair universe rather than a
  model-selected list of likely edges.
- Concept aliases and identities cannot silently drift during Relation passes.
- Public artifacts remain inspectable and reproducible without publishing
  course text.
- The signing key stays outside the application and repository.
- A signer cannot authorize an arbitrary fresh key at sealing time; the public
  key policy must pre-exist in reachable repository history.
- Claims can distinguish software integrity, key control, self-attested human
  work, final gold, and evaluation results.

Costs and limitations:

- The maintainer must complete a real private worksheet and manage a signing
  key/policy safely, with a separate registration commit before annotation.
- Six public leaves require deep binding and recovery checks rather than one
  filesystem transaction.
- The complete pair universe is quadratic, intentionally bounded here to
  12-20 Concepts.
- Solo self-attestation cannot support inter-rater or independently verified
  human-label claims.
- A Concept seal is useful intermediate authority but cannot unblock G3
  quality claims until Relation gold is separately sealed.

## Rejected alternatives

- **Let the LLM create and approve Concepts.** This collapses proposal and
  authority, leaks predictions into gold, and cannot support honest accuracy.
- **Commit the annotation worksheet.** It would publish exact Source quotes
  and mix mutable authoring data with immutable public evidence.
- **Use a checkbox or typed name as the seal.** It records a declaration but
  supplies no external key-control evidence over exact bytes.
- **Accept an allowed-signers file supplied with the signature.** Without a
  prior trust root this only proves that an arbitrary newly generated key
  signed its own policy.
- **Call a valid SSH signature proof of a human reviewer.** Cryptography proves
  possession of a key, not actor kind, process compliance, or semantic quality.
- **Wait for one monolithic final gold artifact.** It obscures the fixed
  Concept/pair boundary needed for delayed exhaustive Relation review.

## Verification

The expanded focused implementation suite reports `55 passed, 1 skipped`
after reviewer-policy, security, privacy, and publication-race hardening. The
complete local backend regression reports `1028 passed, 7 skipped, 1 warning`;
remote change-level CI remains the push gate. No CS336 reviewer-key policy has
been registered, no authorized human worksheet exists, and no Concept seal,
gold bundle, or accuracy/path result has been produced.
