# Golden Graph Evaluation Protocol

- **Program:** G0.2a
- **Status:** G0.2a infrastructure implemented; CS336 instance draft; Source
  scope, human labels, and sealed evaluation remain unfrozen
- **Depends on:**
  [public-course acquisition](public-course-benchmark-acquisition.md),
  [graph annotation rules](../graph-annotation-protocol.md), and
  [G1 immutable publication](concept-graph-publication.md)
- **Primary fixture:** `cs336-sp25-lecture-03-golden-graph-v1`
- **Claim class:** authoring/engineering fixture, not held-out model accuracy

## Responsibility

G0.2 turns the evaluation plan into a machine-enforced reproducibility and
leakage boundary. It does not annotate the course, generate Concepts, publish a
graph, or run a sealed benchmark.

```text
pinned acquisition manifest + exact asset identity
-> strict evaluation protocol
-> canonical JSON + sidecar SHA-256
-> acquisition / partition / rights / parser cross-check
-> draft or frozen decision
-> later annotation packets and materialization
```

The implementation lives in `backend/golden_graph/`: `schemas.py` registers
the protocol contract, `bindings.py` defines exact public leaf envelopes,
`canonical_io.py` owns byte identity, and `protocol.py` resolves authority and
publishes/reloads a freeze. The checked-in Lecture 3 draft and its sidecar live
under `backend/golden_graph/protocols/`.

The acquisition manifest and evaluation protocol are deliberately separate.
The acquisition manifest answers **which external bytes may be fetched**. The
evaluation protocol answers **which verified bytes, pages, parser, chunker,
human procedure, metrics, and claim rules may be used**. Neither artifact is a
substitute for the other.

The acquisition `ManifestAuthority` is an upstream byte/rights prerequisite.
It is not a fifth member of the following four downstream authorities, which
remain separate:

| Authority | Owns | Does not prove |
| --- | --- | --- |
| protocol definition / Source-slice freeze | ontology, page scope, tools, semantic Source/Chunk identity, metrics, claim rules | that human gold exists |
| partition-bound `GoldBundleSeal` | closed-world Concepts, full pair universe, Pass A/B, adjudication, alias table for one exact partition | held-out model quality or gold for another partition |
| automatic-proposal/Chat run family | a pre-annotation `RunSpecSeal` for model, prompt, retrieval/index, runner and seeds; later sealed `PredictionBundle`/`ResultBundle` artifacts that reference it | that sealed access happened only once |
| append-only evaluation-access ledger | future opening, `gold_sealed`, evaluation, and reproduction event history for sealed assets | scientific validity by itself |

This separation prevents one `frozen` flag from impersonating the entire
evaluation lifecycle. The future sealed-transfer order is:

```text
RunSpecSeal
-> source_annotation_open
-> transfer-specific GoldBundleSeal
-> sealed PredictionBundle / ResultBundle referencing RunSpecSeal
-> prediction_evaluation_open
-> explicitly labeled reproduction
```

`RunSpecSeal` must therefore exist before annotation opens. `GoldBundleSeal` is
the immutable artifact; `gold_sealed` is the future ledger event that records
its publication. The access-ledger and run-family implementations do not exist
in G0.2a, so later code must enforce this ordering rather than documentation
claiming that it already does.

## Draft and frozen states

A strict schema is not automatically a frozen benchmark. Both states reject
unknown fields and malformed identities, but they permit different work:

| State | Permitted work | Forbidden claim |
| --- | --- | --- |
| `draft` | schema/runner tests, authoring-tool development, CC0 smoke tests | any frozen, held-out, accuracy, or golden-graph result |
| `frozen` | create empty blinded packets and collect the registered human decisions toward a separate G2 seal | claiming gold exists, or materializing/publishing a final gold graph before `GoldBundleSeal` |

The CS336 Lecture 3 protocol remains `draft`, but its Source scope is now an
explicit whole-deck anti-cherry-picking contract: all 68 physical pages are
included after the deterministic v1 parse verified that each was successful
and non-blank. No model or ad hoc CLI choice defines that scope. A freeze
attempt must still fail until the three derived public leaves and their hashes
are bound; human Concept and Relation decisions remain outside this protocol.

Changing a frozen asset, page range, parser, chunker, annotation rule, metric,
threshold, or artifact lineage creates a new protocol version. It never edits
the meaning of an existing hash.

`protocol_status="frozen"` alone is not authority. `freeze_protocol` accepts
only a draft, validates every registered leaf plus the current tracked
derivation/runtime identities, creates the JSON and sidecar without replacing
existing bytes, then calls `load_historical_frozen_protocol` to re-read the
persisted bytes and historical leaves. It repeats that live-leaf/runtime gate
after publication so a race cannot return authority after code or dependency
drift. This publication gate intentionally does not claim a clean worktree or
currently available private PDF; those belong to explicit replay readiness.
Each file is fully
written and `fsync`ed under
a same-directory temporary name before an atomic, no-replace hard link exposes
the canonical name. A process kill therefore leaves either no canonical file
or complete bytes. An identical retry can repair an exact JSON/sidecar pair
interruption; conflicting bytes fail closed. The canonical output path is
derived from `protocol_id`, so one identity cannot be silently published under
multiple names. Only the resulting `FrozenProtocolAuthority` receipt may cross
a frozen-input boundary, and consumers must reload it before use if any public
leaf could have drifted.

Historical validity and replay readiness are separate capabilities.
`load_historical_frozen_protocol` issues `FrozenProtocolAuthority` after
validating canonical public leaves, stable protocol
semantics, and exact blobs in the recorded Git commit without requiring the
current checkout, installed `pypdf`, Python, Unicode database, or annotation
guide to equal that old environment. `require_current_replay_readiness` first
reloads that historical authority, then requires the current tracked closure,
tool/config/lock/runtime identities, a clean Git worktree, and the exact private
authoring asset. It returns a token-gated, process-local
`ReplayReadyFrozenProtocolAuthority`. The older exported
`load_frozen_protocol` name remains a strict compatibility wrapper that also
runs the current tracked-leaf/runtime gate, so existing callers are not
silently weakened. Only a completed rebuild proves output equality; the
readiness receipt can become stale immediately after issuance. Installed
third-party distribution bytes are not supply-chain attested: v1 binds the
exact lockfile, declared/installed version, parser adapter bytes, Python, and
Unicode version. An isolated environment built from the lock is still required
for stronger artifact-level dependency attestation.

A public reload is read-only: it boundedly waits for an active publisher to
drop its temporary hard-link name, then requires each JSON/sidecar leaf to have
exactly one link. Only the internal recovery path may remove an exact
same-inode `.*.publish-tmp` crash remnant; an unknown persistent hard link
withholds authority. This keeps concurrent identical publishers convergent
without turning a read API into filesystem cleanup.

This publication contract requires a filesystem with same-directory hard-link
support. Unsupported FAT/network filesystems fail closed instead of degrading
to an overwrite-prone path. It guarantees process-crash atomic visibility, not
directory-metadata survival under every power-loss/storage-controller failure.
A hard kill before publication may leave only an ignored
`.*.publish-tmp` staging name; maintainers may remove such stale files after
confirming that no freeze process is active.

## Authority and identity checks

Before a protocol may freeze, the validator resolves its registered asset by
exact `corpus_id` and `asset_id` through the strict acquisition loader and
checks:

- the acquisition manifest path and SHA-256;
- the upstream repository commit and asset SHA-256;
- `partition=authoring`; development and sealed-transfer assets fail closed;
- the registered SPDX license, attribution, and conservative
  `redistribution_allowed=false` policy;
- the parser implementation label, code path/hash, exact-key resource-bounded
  config, dependency snapshot, installed version, and the matching version
  actually present in `uv.lock` (G0.2b resolves and executes the callable
  contract);
- the dependency-lock path and hash;
- exact-key dependency, semantic Source catalog, semantic Chunk manifest, and
  Source-slice build summary
  envelopes plus their sidecars;
- parser/chunker code and config hashes, dependency snapshot, lock,
  and installed distribution versions;
- page parse status plus bounded exclusion/failure reason, semantic page
  hashes/UTF-8 lengths, contiguous locator-ordered Chunk ordinals, and bounded
  UTF-8 locator offsets whose union covers every byte of every included page;
- an explicit `complete_union_overlap_allowed-v1` policy: bounded sliding
  overlap is allowed, gaps and tail omission are not, and the same exact
  locator cannot claim two Chunk identities;
- the exact graph annotation guide path and hash;
- a non-empty exact page scope with documented inclusion and exclusion rules;
- the production relation ontology, direction, symmetry, support basis, and
  support roles;
- delayed blinded human review, adjudication, and path-result embargo;
- all registered quality targets, evidence scopes, required future protocols,
  minimum sample/cluster rules, and zero-denominator behavior.

The v1 `DependencySnapshot` is a tool-only exact set: it contains exactly the
parser and chunker distributions registered by the protocol, not an arbitrary
environment inventory. G0.2b closes repository-local code lineage by requiring
a clean Git `HEAD`, recording that project commit in the redacted build
summary, and executing parser/Chunker code from the exact captured bytes whose
hashes were registered. Historical loading validates the exact input blobs and
v1 orchestration closure inside that recorded commit. Publication/replay gates
additionally require the current tracked leaves and runtime to remain
compatible. Normal later code evolution therefore cannot erase historical
authority, while a requested rerun still fails closed. Hashing only an
entrypoint label is never sufficient.

A matching asset hash proves byte identity, not PDF safety. The G0.2b Source
catalog builder enforces parser resource limits and records exclusions for
image-only or failed pages. It cannot turn an unverified OCR transcription
into ordinary PDF text evidence. G0.2a validates structural lineage and
registered hashes; the real G0.2b raw-PDF-to-page and page-to-Chunk replay must
still publish its receipt before the CS336 Source slice freezes.

## Canonical artifact contract

Protocol artifacts use SHA-256 over sorted-key, compact,
non-ASCII-preserving UTF-8 JSON plus one terminal LF, with non-finite numbers
forbidden. The LF is part of the hash. A strict sidecar binds the exact
artifact filename and bytes. Loaders reject:

- a missing or mismatched sidecar;
- an unsupported schema/protocol version;
- duplicate, overlapping, unsorted, zero, or negative page ranges;
- unknown fields or type coercion;
- uppercase or malformed content hashes;
- a non-canonical frozen artifact;
- acquisition or dependency identities that differ from the registered files.

The repository `.gitattributes` fixes text files to LF so the raw manifest,
lock, guide, implementation/config, canonical JSON, and sidecar identities do
not change between Windows and Linux checkouts.

All repository authority leaves and their sidecars are resolved component by
component. Symlink/junction/reparse aliases and multi-link files fail closed;
inside a Git worktree, a leaf must already be tracked in the index. This makes
the documented public/private path boundary an executable property rather
than a string-prefix convention.

The public protocol, redacted dependency/Source/Chunk leaves, and later public
annotation artifacts live under tracked `backend/golden_graph/` paths and
contain identities, page numbers, offsets, hashes, maintainer-authored labels,
and rationales. They must not contain slide bodies, page text, screenshots,
exact quotes, locally resolved runtime Chunk IDs, or a private materialization
plan. Only those private values remain under the gitignored `backend/data/`
boundary.

Exact-key schemas mechanically prevent a source-body field from being added to
public leaves. They cannot prove that a human did not paste a quote into an
otherwise permitted reason or attribution string; the later export step still
requires an explicit redaction review.

The public semantic catalog deliberately excludes product UUIDs, database IDs,
projection generations, and timestamps. A later private materialization
artifact maps logical page/Chunk identities to concrete Source revisions; it
does not redefine the public semantic hash.

## Leakage and human-review boundary

Lecture 3 is authoring material and the first engineering golden-graph fixture.
Its authoring `GoldBundleSeal` must never be reused as the gold authority for a
sealed-transfer run. Lecture 4 and 7 are development-only; Lecture 11 and 16
remain sealed and inaccessible to G2. Each future sealed-transfer partition
requires its own partition-bound `GoldBundleSeal` after its registered
`RunSpecSeal` and `source_annotation_open` event.

For the first solo-maintained graph, two delayed passes measure temporal
intra-rater agreement. They are not described as independent reviewers. The
protocol requires:

1. one frozen Source scope;
2. a human-authored, evidence-bound Concept inventory finalized before any
   Relation packet is generated; this inventory does not receive a fabricated
   two-reviewer or temporal-agreement claim;
3. every unordered Concept pair in that fixed inventory to receive an explicit
   relation-set or `none` judgment;
4. Relation Pass A to be hidden before Relation Pass B is generated;
5. the registered minimum delay before Relation Pass B;
6. disagreement reveal only after Relation Pass B is sealed;
7. a human adjudication reason for every relation disagreement;
8. `GoldBundleSeal` before final graph materialization/publication and before
   path results or system proposals are viewed.

Both relation passes are blind to system output. Here, a pair packet or human
annotation candidate is not a model “proposal”; automatic Understanding
proposals belong only to a later run family whose sealed predictions/results
reference the already frozen `RunSpecSeal`.

The system can automate schema checks, packet ordering, pair enumeration,
hashing, diffs, metrics, materialization, and redacted export. The maintainer
must own page selection, Concept granularity, relation existence/type/direction,
support basis, evidence spans, rationales, both passes, adjudication, and final
rights/claim approval.

## Test fixture policy

A small synthetic structural packet bound through a real on-disk acquisition
manifest may exercise the complete protocol-freeze state in CI. It proves
schema, deep in-memory immutability, canonicalization, historical authority
reload, replay-readiness separation across later Git commits,
crash/concurrency recovery, full page coverage, lock/config lineage, and guard
behavior. It does not become the public-course golden graph and does not
establish Concept or Relation proposal accuracy. The existing counterfactual
four-question fixture remains a separate trust/schema smoke test and is not
relabeled as a closed-world graph benchmark.

## Next gates

G0.2 closes only after the maintainer selects the exact Lecture 3 pages and the
bounded builder emits canonical dependency, Source, and Chunk leaves; the
statistical confidence floors must also be complete. G2 then creates the
Concept inventory, complete pair universe,
blinded Pass A/B artifacts, adjudication, alias table, and semantic gold export.
Only after that separate gold freeze may G3 path evaluation use the fixture;
G3 owns the independently frozen synthetic performance authority required by
the 1k/10k latency metrics. The currently registered sealed-transfer partition
contains only two lecture clusters, below the registered minimum of five; it
is registered for a diagnostic transfer case study only. A future run-bundle
runner must enforce actual cluster/sample eligibility. A confirmatory interval
requires a new pre-registered protocol with at least five independent sealed
lecture clusters before any of them are opened.
