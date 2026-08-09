# Golden Graph Annotation Security Primitives

- **Program:** G2.2 foundation
- **Status:** Implemented and integrated with the G2.1 Concept workflow
- **Owns:** redacted evidence resolution, public annotation privacy checks, and
  policy-bound detached SSH attestations
- **Does not own:** Concept or Relation semantics, human-review truth, elapsed
  time, graph import, model evaluation, or path serving

## Why this boundary exists

Concept sealing, Relation Pass A, Relation Pass B, and GoldBundle sealing all
need the same two sensitive operations:

1. turn a private exact Source selection into a public locator/hash without
   publishing the quote; and
2. prove that a previously registered key signed the exact canonical request
   for the intended annotation stage.

Implementing those operations inside every stage would create four security
implementations whose path rules, hash checks, error redaction, or signature
semantics could drift. This module boundary makes each rule single-owner while
leaving stage state machines and semantic validation in their own workflows.

```text
writer/loader Source receipt
  -> deep canonical revalidation + immutable annotation Source snapshot

private exact quote + annotation Source authority
  -> shared evidence resolver
  -> EvidenceSpan(page/chunk/UTF-8 byte range/span hash)
  -> shared public replay and privacy scan

typed stage seal request + active reviewer-key authority + signature
  -> shared detached-attestation verifier
  -> Artifact + Reference + key-control-only receipt
  -> immutable stage publication

persisted request + Artifact + historical reviewer-key authority
  -> shared embedded verifier
  -> historical key-control-only receipt
```

## Module ownership

| Boundary | Implementation | Responsibility |
| --- | --- | --- |
| shared evidence | `backend/golden_graph/annotation_evidence.py` | receipt snapshot revalidation, nested projection replay, exact quote resolution, public span replay, Source-copy/path/encoding/Unicode rejection, canonical span ordering |
| Source receipt issuer | `backend/golden_graph/source_slice_builder.py` | unchanged G0.2 writer/loader and token-gated private materialization receipt |
| shared attestation | `backend/golden_graph/annotation_attestation.py` | canonical challenge binding, active-policy signing, embedded historical verification, portable artifact/reference construction |
| strict shared DTOs and namespaces | `backend/golden_graph/annotation_models.py` | four-value namespace type plus immutable attestation Artifact and Reference shapes |
| stage adapter | `backend/golden_graph/annotation_workflow.py` | translate shared bounded errors into the existing Concept workflow error boundary |
| reviewer trust root | `backend/golden_graph/reviewer_policy.py` | repository-history authority and namespace/key membership; remains stage-agnostic |
| cryptographic subprocess | `backend/golden_graph/ssh_attestation.py` | trusted OpenSSH verification over stable file snapshots |

## Evidence contract

The public binder accepts only a writer/loader-issued
`PrivateSourceSliceMaterializationReceipt`. It canonicalizes and strictly
reparses the complete nested materialization, replays its product bindings,
checks its canonical digest, and returns a fresh immutable
`AnnotationEvidenceSourceAuthority`. Concept preparation and reload bind once
per workflow transition and reuse that snapshot. Evidence APIs do not accept a
duck-typed receipt, and the privacy API does not let a stage supply an
unrelated collection of supposed Source text.

This validates the already-loaded snapshot; it does not turn the receipt into
a lease on mutable disk bytes. A caller that needs disk currentness must invoke
the existing private materialization loader again before binding.

The binder deliberately lives in the annotation layer. The G0.2 Source-slice
builder belongs to the frozen derivation closure recorded at commit
`cd06516`; changing that module would correctly invalidate current replay
readiness. Keeping snapshot validation in `annotation_evidence.py` preserves
the frozen builder bytes while adding a stricter downstream consumer boundary.

Each frozen Chunk also receives a process-local keyed integrity tag over the
private materialization digest, locator window, text bytes, and semantic hash.
An authority-level keyed root binds the exact count and order of all Chunk
tags, preventing coordinated prefix/tail truncation. Evidence replay verifies
the root and referenced Chunk tag; privacy validation verifies every tag and
reconstructs contiguous same-page Source segments while removing declared
Chunk overlap. This catches post-issuance mutation and Source copies that cross
a Chunk boundary. The tags protect this in-process capability; they are not a
persisted signature or a sandbox against arbitrary code running in the Python
process.

`resolve_evidence_selection` then consumes a structural private selection
containing Chunk ordinal, logical page, frozen semantic Chunk hash, optional
page-global UTF-8 byte start, and exact quote. It returns only `EvidenceSpan`:

- logical page and Chunk identity;
- half-open page-global UTF-8 byte offsets;
- semantic Chunk and selected-span SHA-256 hashes;
- no Source quote, filesystem path, or user identifier.

Binding fails closed when the Chunk or manifest index is empty, duplicated,
malformed, or hash-inconsistent, or when the locator byte window does not equal
the actual UTF-8 bytes. Resolution fails when an explicit offset is wrong or a
repeated quote has no explicit offset. `validate_public_evidence_span` replays
the public coordinates against the same frozen private Source and rechecks the
UTF-8 boundary and span hash.

`reject_public_source_copy` scans public values individually and after
aggregation. It rejects:

- long character or twelve-token Source copies;
- Windows, UNC, POSIX system, relative, home, and file-URI paths;
- email-like values;
- default-ignorable characters, variation selectors, control/surrogate
  characters, and NFKC disguises;
- every `%HH` escape contained in one field, including nested encodings;
- cross-field escape/source/path reconstruction, while allowing a harmless
  boundary such as `("100%", "20 samples")`.

Ordinary prose percentages such as `100%` remain valid. All evidence errors are
bounded static text and never interpolate the quote, Source, path, or caller
value.

The current binder deliberately favors fail-closed validation over peak-memory
optimization: it canonicalizes and strictly reparses a materialization whose
hard ceiling is 512 MB. Current CS336 slices are comfortably below that bound.
Before whole-course materializations approach the ceiling, this boundary needs
a streaming canonical digest/replay path plus cached normalized privacy
indexes; that scaling work is not claimed by this checkpoint.

## Attestation contract

The only supported SSHSIG namespaces are:

| Stage | Namespace |
| --- | --- |
| Concept inventory | `video-course-cards-g2-concepts-v1` |
| Relation Pass A | `video-course-cards-g2-relation-pass-a-v1` |
| Relation Pass B | `video-course-cards-g2-relation-pass-b-v1` |
| final GoldBundle | `video-course-cards-g2-gold-bundle-v1` |

The canonical policy tuple is stored in lexicographic order because
`ReviewerKeyPolicy` rejects unordered or duplicate namespaces. A typed request
must expose a matching `namespace` and `reviewer_id` before it can become
canonical challenge bytes.

`verify_and_build_detached_key_attestation` is an authoring transition. It
requires a token-gated policy authority, reloads the policy from the current
Git `HEAD`/index/worktree before cryptographic work, reads allowed-signers bytes
only from that refreshed policy, verifies the external signature, detects
signature replacement between verification and the second stable read, and
revalidates Git policy state again before issuing a token-gated
`VerifiedAnnotationAttestation`. Concept worksheet initialization,
preparation, and publication use the same current-policy revalidation. This is
a point-in-time authorization check, not a long-lived lease; the final
post-verification check closes the useful revocation race while a change after
that checkpoint remains a new repository state for the next transition.

`verify_embedded_detached_key_attestation` is a replay transition. It accepts a
historical policy authority so committed key removal can revoke new work
without invalidating old seals. It still re-runs OpenSSH verification and
compares request hash, namespace, signer, exact allowed-signers bytes, signature
hash, and public-key fingerprint.

Both transitions prove registered-key control over exact bytes only. They do
not prove reviewer humanity, real-world identity, prediction blindness,
elapsed-time truth, semantic correctness, or benchmark quality.

## Compatibility decisions

- `ConceptInventorySealRequest` and `ConceptInventorySeal` remain strictly
  Concept-namespace-only.
- Existing Concept-only policies remain valid for historical Concept replay;
  the policy schema does not require exactly four namespaces.
- A Concept-only policy cannot authorize Relation Pass A/B or GoldBundle work.
- New policy preparation registers all four namespaces because this repository
  has no real registered CS336 reviewer policy yet. That new policy has a new
  canonical hash by design; no historical artifact is rewritten.
- Existing Concept Artifact, Reference, seal, pair-manifest, and CLI receipt
  field sets remain unchanged.
- The hash-bound G0.2 Source-slice builder and its frozen derivation closure
  remain byte-for-byte unchanged.
- The hash-bound graph annotation protocol is not edited by this internal
  refactor.

## Verification

Dedicated suites cover real Ed25519 sign/verify for every namespace, active
versus historical policy behavior, cross-stage and cross-payload replay,
signer/key mismatch, capability construction, exact canonical challenge bytes,
multibyte UTF-8 offsets, repeated quotes, Source/Chunk drift, malformed
indexes, forged Source capabilities, nested materialization mutation, public
span tampering, aggregate leakage, path families, invisible Unicode,
NFKC/percent disguises, ordinary percentages, and policy revocation after an
authority was issued.

Final local acceptance is **117 passed** for the five focused evidence,
attestation, reviewer-policy, Source-materialization integration, and Concept
workflow suites. The complete backend regression is **1094 passed, 7 skipped,
1 existing dependency deprecation warning**. Remote change-level CI remains
the push gate.

The acceptance test counts and full-backend result are recorded with the
checkpoint in [`productization-log.md`](../productization-log.md). Passing them
establishes the software boundary only; it creates no human label, Relation
pass, `GoldBundleSeal`, accuracy result, or path-quality result.

## Current consumer

G2.3 Relation Pass A now consumes these APIs directly through its separate
[commit--reveal workflow](golden-graph-relation-pass-a-workflow.md). It adds
Relation schemas and a Relation-specific state machine without copying
evidence, privacy, reviewer-policy, or detached-signature logic. Pass B and
GoldBundle must continue through the same shared boundary.
