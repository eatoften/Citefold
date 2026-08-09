# Golden Graph Human Annotation Workflow

- **Program:** G2.1 Concept inventory handoff
- **Status:** Tooling implemented; no repository-registered reviewer-key
  policy, authorized real worksheet, human labels, or seal yet
- **Primary fixture:** `cs336-sp25-lecture-03-golden-graph-v1`
- **Decision:** [ADR-0010](../decisions/ADR-0010-staged-human-gold-and-key-control-attestation.md)
- **Upstream authority:**
  [Golden Graph Evaluation Protocol](golden-graph-evaluation-protocol.md)
- **Annotation rules:**
  [hash-bound Graph Annotation Protocol](../graph-annotation-protocol.md)

## Responsibility

G2.1 creates the handoff between the reproducible G0.2 Source slice and the
maintainer's first real human annotation pass. It initializes a private,
Source-bearing worksheet, validates a completed worksheet against the frozen
Source bytes, prepares a redacted commitment, verifies an external OpenSSH
signature, and publishes a deterministic Concept-inventory artifact DAG.
Before initialization, it also requires an **active**
`ReviewerKeyPolicyAuthority`: exact public policy bytes and sidecar that were
committed in reachable Git history before the annotation worksheet is created
and still match current `HEAD`, the index, and the working tree.

It does **not** generate labels, authenticate that the reviewer is a human,
prove that the reviewer remained blind to system proposals, annotate
Relations, create a `GoldBundleSeal`, import a graph into the product, or run
an accuracy/path experiment.

## Implemented command surface

The command module is `golden_graph.annotation_command`. Global options must
appear before the subcommand. The defaults select the repository's frozen
CS336 Lecture 3 protocol and its ignored private Source materialization.

```powershell
uv run --directory backend python -m golden_graph.annotation_command prepare-reviewer-key-policy --allowed-signers <PUBLIC_ALLOWED_SIGNERS_FILE>
uv run --directory backend python -m golden_graph.annotation_command verify-reviewer-key-policy --reviewer-key-policy-commit <FULL_REGISTRATION_COMMIT_SHA>
uv run --directory backend python -m golden_graph.annotation_command init-concepts --reviewer-key-policy-commit <FULL_REGISTRATION_COMMIT_SHA>
uv run --directory backend python -m golden_graph.annotation_command prepare-concept-seal --reviewer-key-policy-commit <FULL_REGISTRATION_COMMIT_SHA>
uv run --directory backend python -m golden_graph.annotation_command seal-concepts --reviewer-key-policy-commit <FULL_REGISTRATION_COMMIT_SHA> --signature <DETACHED_SIGNATURE_FILE>
uv run --directory backend python -m golden_graph.annotation_command verify-concepts --reviewer-key-policy-commit <FULL_REGISTRATION_COMMIT_SHA>
```

These are the only implemented G2.1 workflow commands:

| Command | Input state | Effect | What it does not prove |
| --- | --- | --- | --- |
| `prepare-reviewer-key-policy` | one canonical public Ed25519 allowed-signers line | publishes a candidate policy and sidecar at the fixed tracked path | issues no authority; publication is not registration |
| `verify-reviewer-key-policy` | full commit SHA containing the exact policy and sidecar | proves current `HEAD`, index, and working-tree bytes equal exact historical blobs at a reachable ancestor commit | no private-key control, humanity, or annotation work |
| `init-concepts` | frozen protocol, matching private Source materialization, and repository-registered reviewer-key authority | creates one policy-bound empty, ignored worksheet without overwriting an existing file | no labels or reviewer attestation |
| `prepare-concept-seal` | the same reviewer-key authority and a strictly valid `complete` worksheet | resolves private quotes to redacted byte spans and writes three ignored candidate artifacts | no external approval and no public authority |
| `seal-concepts` | the same reviewer-key authority, unchanged candidates, and a detached signature over the seal request | loads the registered allowed-signers bytes internally, verifies registered-key control, derives the complete pair universe, publishes six immutable public leaves, and reloads them | no proof of reviewer humanity/blindness and no Relation gold |
| `verify-concepts` | all six public leaves plus the exact protocol, private Source, and historical reviewer-key authority | deeply reloads hashes, derivations, signature, Source/policy bindings, and pair completeness, including after policy revocation | no permission for new signing, no `GoldBundleSeal`, and no quality result |

The policy candidate has the fixed path
`backend/golden_graph/attestations/<protocol_id>.<reviewer_id>.reviewer-key-policy.json`
with a sibling `.sha256`. The maintainer must inspect, commit, and push both
files before the named commit can issue `ReviewerKeyPolicyAuthority`.

`init-concepts` deliberately fails when the target worksheet already exists.
There is no reset or overwrite command. The pre-hardening local worksheet has
zero candidates and zero human labels, but its schema lacks the prior-policy
binding. The incompatible default file is
`backend/data/golden_graph/annotations/cs336-sp25-lecture-03-golden-graph-v1/concepts.worksheet.private.json`.
It is unauthorized; the maintainer must manually remove it, after confirming
the zero-candidate state, and reinitialize only after policy registration.

## State machine

```text
frozen G0.2 protocol + matching ignored Source materialization
maintainer-selected public Ed25519 allowed-signers line
                            |
                            v
       prepare-reviewer-key-policy (candidate only)
                            |
             MAINTAINER REVIEW / COMMIT / PUSH
                            |
       verify-reviewer-key-policy (authority)
                            |
                 init-concepts (once)
                            |
                            v
            private worksheet: draft, 0 candidates
                            |
                    HUMAN ANNOTATION
                            |
                            v
             private worksheet: complete
                            |
                            v
               prepare-concept-seal
                            |
                            v
       3 private redacted candidate artifacts
                            |
       MAINTAINER INSPECTION + EXTERNAL SSH SIGNING
                            |
                            v
                   seal-concepts
                            |
                            v
       6 immutable public leaves + SHA-256 sidecars
                            |
                            v
                  verify-concepts
                            |
                            v
        Concept-inventory authority only, not gold bundle
```

Every transition fails closed. Preparation requires a complete worksheet.
Sealing re-derives the candidates from the current worksheet and rejects a
change after preparation. Publication is no-overwrite; an identical retry may
converge, but a conflicting artifact cannot replace an existing leaf.

## Artifact DAG

The public DAG is intentionally smaller than the eventual G2 gold bundle:

```text
FrozenProtocolAuthority -----------+
                                    |
PrivateSourceSliceAuthority -------+------> ConceptInventory
                                    |              |
ReviewerKeyPolicyAuthority --------+              +--> GoldAliasTable
                                    |              |
Private complete worksheet --------+--> ConceptInventorySealRequest
                                               |
registered allowed-signers bytes + SSH signature
                                               +--> DetachedKeyAttestation
                                               |
ConceptInventory + AliasTable + Request + Attestation
                                               |
                                               v
                                    ConceptInventorySeal
                                               |
ConceptInventory -------------------------------+--> RelationPairManifest
```

The six public JSON leaves are:

1. `ConceptInventory`;
2. `GoldAliasTable`;
3. `ConceptInventorySealRequest`;
4. `DetachedKeyAttestationArtifact`;
5. `ConceptInventorySeal`;
6. `RelationPairManifest`.

The inventory seal is explicitly tagged
`concept_inventory_only_not_gold_bundle`. The pair manifest enumerates the
complete unordered pair universe derived from the accepted Concept keys. It
is an input to delayed Relation passes, not a Relation label set.

## Public/private boundary

| Boundary | Contents | Repository policy |
| --- | --- | --- |
| private original | exact course PDF and normalized Chunk text | ignored under `backend/data/`; never copied into public artifacts |
| private mutable worksheet | candidate decisions, exact evidence quotes, optional explicit byte starts, reviewer declarations | ignored, bounded, strict JSON; mutable until preparation |
| private prepared candidates | redacted inventory, aliases, and exact seal request awaiting inspection/signature | ignored; canonical bytes and sidecars; must match a fresh derivation at sealing |
| private signing secret | maintainer's OpenSSH private key | outside the repository and never read by this application |
| public reviewer trust root | protocol/reviewer/four-stage-namespace-bound Ed25519 public key policy plus hash sidecar | new work requires exact equality at registration commit, current `HEAD`, index, and working tree; historical verification reads the ancestor blobs after revocation |
| public Concept stage | redacted evidence coordinates/hashes, Concept semantics, alias mapping, reviewer-policy hash/commit bindings, detached attestation verification material, seal, and complete pair IDs | six immutable canonical JSON leaves plus `.sha256` sidecars under `backend/golden_graph/artifacts/` |

Public Concept evidence contains logical page/Chunk identity, half-open UTF-8
byte offsets, and a span hash. It does not contain the selected quote. Concept
names, definitions, aliases, rationales, public key material, and signatures
are public after sealing; the maintainer must inspect them before signing.

## Signature semantics and trust limit

The workflow uses OpenSSH `ssh-keygen -Y sign/verify` with an Ed25519 key and
the namespace `video-course-cards-g2-concepts-v1`. The signature binds the
exact canonical `ConceptInventorySealRequest`. It is accepted only when the
allowed-signers bytes, reviewer ID, namespace, and public-key fingerprint match
an active `ReviewerKeyPolicyAuthority` whose exact canonical artifact and
sidecar already exist in reachable Git history and current repository state. A
signer cannot authorize a fresh key by supplying a new allowed-signers file at
seal time. Removing the policy from current `HEAD` revokes new authoring, while
the historical loader keeps already sealed artifacts verifiable.

New policy preparation registers the Concept, Relation Pass A, Relation Pass
B, and GoldBundle namespaces up front. Each typed request is still restricted
to its own stage namespace, so a valid signature cannot be replayed across
stages. Historical Concept-only policies remain valid for Concept replay but
cannot authorize future Relation or GoldBundle work.

This proves only that the holder of an allowed private key approved those
bytes. It does not prove:

- that the key holder is a human;
- that the declared reviewer identity maps to a real-world identity;
- that the worksheet was authored without model proposals;
- that the declaration or timestamp is true;
- that a second reviewer participated.

The local single-maintainer workflow is therefore **self-attested**. The
schemas preserve `software_authenticated_reviewer_identity=false`, and public
artifacts/receipts preserve
`software_authenticated_prediction_blindness=false`; receipts also say
`key_control_only_not_proof_of_humanity=true`. A future claim of
independent or inter-rater review needs a real external process and separate
evidence; this CLI cannot manufacture it.

## Maintainer-owned work

The following steps cannot be delegated to the software agent and cannot be
inferred from a passing test suite:

1. Generate or select the maintainer-owned Ed25519 key outside the repository;
   run `prepare-reviewer-key-policy` using only its public allowed-signers
   line, inspect the fixed-path candidate, commit and push the policy plus
   sidecar as a separate checkpoint, and record that full registration commit.
   Registration proves repository governance of a key, not the owner's
   humanity.
2. Run `verify-reviewer-key-policy` with the full commit SHA. Confirm that it
   issues repository registration authority, not human identity authority.
3. Confirm the pre-hardening worksheet still has zero candidates, manually
   remove that incompatible empty file, and run policy-bound `init-concepts`.
4. Keep system Concept/Relation proposals unavailable while authoring this
   prediction-blind worksheet.
5. Read the frozen CS336 Source slice and apply the exact ontology, evidence,
   direction, and review rules in the
   [hash-bound annotation guide](../graph-annotation-protocol.md). Do not edit
   that guide in place; a semantic change requires a new protocol identity.
6. Enter a closed-world set of candidate decisions, including the required
   accepted Concepts and explicit exclusions, with canonical keys, aliases,
   definitions, evidence selections, and rationales.
7. Resolve ambiguous repeated quotes with the correct page-global UTF-8 byte
   start. Confirm every evidence selection against the original slide.
8. Mark the worksheet complete and personally make the required reviewer,
   blindness, attestation, and UTC-time declarations.
9. Run preparation, inspect the redacted inventory/alias/request bytes and
   their reported hashes, and do not sign if any semantic content is wrong.
10. Sign the exact prepared seal-request file with the maintainer-controlled
   OpenSSH key using the command template emitted by
   `prepare-concept-seal`. Keep the private key outside the repository.
11. Run sealing and verification, inspect the public diff for Source text or
   personal data, and only then commit the six public leaves and sidecars.
12. Later complete delayed Relation Pass A/B and adjudication over the entire
   pair manifest before creating a separate `GoldBundleSeal`.

The tool enforces shape, lineage, deterministic derivation, and key control.
It cannot decide whether a Concept boundary, definition, alias, or evidence
selection is intellectually correct.

## Technical stack and design choices

| Technology | Responsibility in G2.1 | Chosen limit |
| --- | --- | --- |
| Python 3.11 | command orchestration and pure workflow functions | local CLI, not a hosted annotation service |
| Pydantic strict/frozen models | reject missing/extra/wrongly typed fields and mutable authority DTOs | schema correctness is not semantic correctness |
| canonical UTF-8 JSON + SHA-256 | stable commitments, sidecars, derivation comparison | a self-hash is integrity evidence, not an independent trust root |
| bounded regular-file reads | reject oversized, symlinked/reparse, hard-linked, or unstable inputs at trust boundaries | resource hardening, not a hostile-input sandbox |
| atomic no-replace publication | converge identical retries and reject conflicting immutable leaves | six leaves are transactional by verification/recovery, not one filesystem transaction |
| ignored private artifact I/O | retain Source quotes locally without redistributing course text | the maintainer still owns local backup and secret hygiene |
| Git commit/blob authority | prove the reviewer public-key policy existed as exact tracked bytes before annotation | repository history is governance evidence, not a real-world identity provider |
| OpenSSH Ed25519 signatures | portable external key-control evidence over exact request bytes | no human/identity/blindness authentication |
| deterministic pair enumeration | freeze all Concept pairs before Relation judgments | quadratic size is bounded to 12-20 Concepts (66-190 pairs) |

Implementation map:

| Boundary | Code |
| --- | --- |
| strict worksheet, Concept, alias, attestation, seal, and pair DTOs | `backend/golden_graph/annotation_models.py` |
| bounded private reads and immutable public artifact I/O | `backend/golden_graph/annotation_artifacts.py` |
| Concept preparation, stage adaptation, and DAG publication/reload | `backend/golden_graph/annotation_workflow.py` |
| shared exact-quote resolution, span replay, and public privacy checks | `backend/golden_graph/annotation_evidence.py` |
| shared four-stage canonical challenge, signing, and embedded verification | `backend/golden_graph/annotation_attestation.py` |
| detached OpenSSH verification boundary | `backend/golden_graph/ssh_attestation.py` |
| repository-registered reviewer-key trust root | `backend/golden_graph/reviewer_policy.py` |
| six-command orchestration and redacted receipts | `backend/golden_graph/annotation_command.py` |
| adversarial regression suites | `backend/tests/test_golden_graph_annotation_artifacts.py`, `test_golden_graph_annotation_attestation.py`, `test_golden_graph_annotation_evidence.py`, `test_golden_graph_annotation_workflow.py`, `test_golden_graph_reviewer_policy.py`, and `test_golden_graph_ssh_attestation.py` |

The shared boundary and its compatibility rules are specified separately in
[Golden Graph Annotation Security Primitives](golden-graph-annotation-security-primitives.md).

## Validation status

At the G2.1 checkpoint, the expanded focused suite reported **55 passed, 1
skipped** and the complete backend regression reported **1028 passed, 7
skipped, 1 dependency deprecation warning**. After extracting and hardening the
shared G2.2 evidence/attestation boundary, the current focused result is **117
passed** and the complete local backend regression is **1094 passed, 7 skipped,
1 dependency deprecation warning**; remote change-level CI remains the push
gate.
Environment-capability skips are reported rather than converted into passes.
Coverage includes strict worksheet/artifact validation, Source binding and
quote resolution, redaction, canonical/no-overwrite publication, concurrent
convergence, active-policy revocation and historical verification, trusted Git
and detached-signature subprocess boundaries, tamper rejection, complete pair
enumeration, public reload, and malformed-authority failures.

Passing these tests establishes the software boundary only. It contributes no
human label, Concept/Relation accuracy, agreement, graph coverage, path
correctness, or held-out result.

## Current real status and next gate

As of 2026-08-09:

- the two reviewer-policy commands and four Concept-workflow commands are
  implemented;
- no CS336 reviewer-key policy has been registered in an ancestor Git commit;
- the pre-policy local worksheet contains zero candidates/labels but is schema
  incompatible and unauthorized; it awaits maintainer-confirmed manual removal
  and policy-bound reinitialization;
- zero human Concept labels exist;
- no public `ConceptInventorySeal` exists;
- no `GoldBundleSeal` exists;
- no Concept/Relation accuracy, agreement, graph-quality, or path result
  exists.

The next authority-changing action belongs to the maintainer: prepare, inspect,
commit, push, and verify the reviewer-key policy; confirm and remove the empty
legacy worksheet; then run policy-bound initialization. Only after that gate
may the maintainer author real Concept decisions. Software implementation may
continue around that boundary, but G2 cannot be declared complete until the
human Concept inventory, delayed Relation passes, adjudication, and final gold
bundle are genuinely sealed.
