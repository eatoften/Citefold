# Golden Graph Source-Slice Builder

- **Program:** G0.2b
- **Status:** complete for the deterministic CS336 Source slice, independent
  replay, and protocol freeze; human gold remains a separate G2 gate
- **Depends on:**
  [public-course acquisition](public-course-benchmark-acquisition.md) and
  [golden-graph evaluation protocol](golden-graph-evaluation-protocol.md)
- **Decision:**
  [ADR-0009](../decisions/ADR-0009-deterministic-redacted-source-slice.md)
- **Implementation:** `backend/golden_graph/source_slice_builder.py`

## Responsibility

G0.2b turns one exact, locally verified authoring PDF into a deterministic,
evidence-addressable Source slice without publishing the PDF or its text. It
closes the derivation gap between the acquisition manifest and the redacted
semantic Source/Chunk leaves required by the evaluation protocol.

```text
ManifestAuthority + exact authoring asset ID
  -> read-only VerifiedAssetReceipt
  -> isolated, bounded PDF worker
  -> private NFKC/LF page projection
  -> protocol-owned whole-deck page scope
  -> page-local, code-point-safe UTF-8 windows
  -> redacted Source catalog + Chunk manifest + build summary
  -> canonical CourseSource/CourseSourceChunk product projection
  -> optional durable private materialization under ignored backend/data
  -> SourceSliceBuildAuthority
```

The builder does not choose the page scope, annotate Concepts, evaluate model
quality, write the product database, or build an embedding index. The draft
protocol is the sole build specification. For CS336 Lecture 3 it binds an
objective anti-cherry-picking rule: all 68 successfully parsed, non-blank
physical pages are included and none are excluded. Concept and Relation labels
remain separate human-review decisions.

## Boundary-by-boundary contract

### 1. Resolve one registered authoring asset

The caller supplies a previously loaded `ManifestAuthority` and an exact
`asset_id`. The builder reloads the tracked manifest and rejects drift before
calling `verify_registered_asset`. That verifier derives the private path from
the manifest; it accepts no caller path, glob, or output override. Only an
asset registered in the `authoring` partition is eligible.

The acquisition receipt binds corpus/asset fields, byte count, PDF magic,
SHA-256, Git-blob identity, canonical local path, and stable file metadata; the
surrounding build summary separately binds the manifest hash. The worker
independently rechecks the byte count, PDF signature, SHA-256,
regular-file/single-link status, and pre/open/post file identity. A receipt is
evidence for one completed verification window, not a lease on a mutable path.

### 2. Parse in an isolated, bounded worker

The builder first requires a clean Git `HEAD` and records that 40-character
project revision in the build summary. `pdf_projection_worker.py` runs as
`python -I` with a reduced environment,
closed stdin, discarded stdout, private stderr, and one whole-asset wall-clock
timeout. Its tracked implementation hash, canonical parser config, Python
version, Unicode database version, exact `pypdf` version, dependency snapshot,
and `uv.lock` identity are all checked before execution. The parent executes a
private exclusive snapshot of the exact parser bytes it hashed. It also loads
the Chunker callable from captured verified bytes, so a stale import or a file
change between verification and execution cannot silently change semantics.

The v1 parser uses `pypdf` plain-text extraction with `strict=False`. It rejects
encrypted PDFs and does not run OCR. Normalization is exactly
`unicode_nfkc_lf_v1`:

1. Unicode NFKC normalization;
2. CRLF to LF;
3. remaining CR to LF.

It deliberately does not trim lines, collapse whitespace, remove NULs, infer
layout, or repair reading order. Any future normalization change requires a
new implementation/config identity.

The checked-in v1 limits are 16 MiB of raw PDF bytes, 500 pages, 1 MiB of
semantic UTF-8 per page, 64 MiB total semantic UTF-8, and a 30-second worker
timeout. The parent boundedly reads strict canonical JSON, rejects duplicate
keys, non-finite numbers, schema extras, and oversized output, then deletes the
temporary projection directory.

### 3. Preserve exhaustive page status

The private worker inventories every PDF page. The public Source catalog also
covers the exact range `1..page_count`.

| Status | Meaning | Scope behavior | Public semantic identity |
| --- | --- | --- | --- |
| `included` | non-blank text parsed, encoded, and stayed within the page limit | eligible only when included by the registered protocol | SHA-256 and UTF-8 byte count retained |
| `blank` | extracted text contains Unicode whitespace only | selecting it fails the build | empty SHA-256 and zero bytes |
| `parse_failed` | page lookup/extraction, UTF-8 encoding, or page-size check failed | selecting it fails the build | typed reason, empty SHA-256, zero bytes |
| `excluded` | page parsed successfully but is outside the supplied scope | never chunked | `out_of_scope`, empty SHA-256, zero bytes |

The protocol adapter must provide the complete registered page scope; arbitrary
CLI page overrides are not accepted. The builder sorts that non-empty set but
never invents it. A selected blank or failed page is an error rather than
silent omission. Reader initialization,
encryption, raw-size/page-count/total-text limits, timeout, file drift, invalid
worker output, or a Chunk-count overrun fails the whole build and returns no
authority.

### 4. Chunk selected pages deterministically

`utf8_chunker.py` constructs page-local sliding windows over normalized UTF-8
bytes. Windows end and restart only at code-point boundaries. V1 permits at
most 4,096 bytes per Chunk, up to 512 bytes of overlap, no cross-page Chunk,
and at most 1,000 Chunks per slice.

The builder independently verifies that every selected page begins at byte
zero, ends at its exact semantic byte length, has no gap, and that every window
text/hash matches its locator span. Overlap is permitted; tail omission is
not. Each public binding contains a contiguous ordinal, semantic Chunk
SHA-256, logical page ID, and half-open UTF-8 byte offsets.

### 5. Reuse the canonical product model

The adapter constructs one deterministic `CourseSource` and ordered
`CourseSourceChunk` tuple directly from the selected private windows. Each
Chunk carries its normalized text and hash plus a typed `PdfPageLocator`. The
locator metadata durably carries its logical page, half-open UTF-8 offsets,
offset unit, redacted Source-catalog hash, and golden Chunk-manifest hash.
The product's projection-manifest hash is computed with the production
`build_projection_manifest_hash`; it is deliberately not confused with the
evaluation Chunk-manifest hash. Source/Chunk IDs use the production
`source_id_for_asset` contract, and all projection bindings, ordinals,
locators, timestamps, and hashes are deterministic for identical inputs.

The resulting Source is marked `ready` but `not_indexed`. This is a compatibility
projection for downstream G2 code, not a persisted product Source. A strict
canonical private envelope can be atomically written and reloaded under the
gitignored Source-slice materialization boundary so G2 can resume without
reparsing. Runtime database publication must still go through the canonical
Source projection store and its transactional generation/currentness contract;
direct inserts from this builder are not authorized.

### 6. Issue one cross-boundary authority

`SourceSliceBuildAuthority` is constructor-guarded and can be issued only by a
successful build. Before issuance, the pipeline verifies the asset, manifest,
tools, configs, dependencies, and lock; authority issuance then cross-checks:

- the carried raw-asset and derivation identities;
- Source catalog and Chunk manifest hashes;
- public ordinals, page locators, byte offsets, and semantic hashes;
- private Chunk text hashes, deterministic IDs, and Source ownership;
- page and Chunk counts in the redacted summary.

The summary binds the canonical pre-output build-spec hash. The protocol
separately binds the summary path and SHA-256, then cross-checks
the summary against acquisition, tool/config/dependency identities, both
redacted leaves, page-status counts, Chunk count, and the clean project commit.

The authority is a private process-local receipt. It contains Source text and
must not be logged, serialized, or returned by a public API. The durable
private envelope is likewise never part of a public receipt. Public writers
accept only the three redacted DTO types.

## Public/private data boundary

| Boundary | Allowed | Forbidden |
| --- | --- | --- |
| tracked `backend/golden_graph/artifacts/` | parser/chunker configs, dependency snapshot, build summary, full page-status inventory, selected page/Chunk hashes, page numbers, UTF-8 offsets, tool lineage, sidecar hashes | PDF bytes, page or Chunk text, screenshots, quotes, local paths, product IDs, private materialization instructions |
| ignored `backend/data/public_course_benchmarks/` | exact acquired PDFs partitioned by corpus and role | Git/release publication |
| ignored temporary worker directory | normalized page text, page errors, private stderr, verified parser snapshot | retention after the build window |
| ignored private materialization directory | canonical `CourseSource`, Source text, runtime Chunk-to-offset mapping, binding hashes | Git/release publication, public receipt fields |
| process-local authority | verified path receipt, `CourseSource`, `CourseSourceChunk` text, runtime Chunk-to-offset mapping | public serialization or logging |

`public_artifact_bytes` is both type-gated and exact-schema-gated. It also
recursively rejects source-bearing key names. `write_public_artifact` writes
canonical UTF-8 JSON and a SHA-256 sidecar without overwriting conflicting
bytes; identical retries converge. The command exposes public publication and
private materialization as two independent, default-off flags. A public-only
run cannot retain Source text as an undocumented side effect.

The private writer and loader require the exact protocol ID and normalized
build-spec hash, enforce the deterministic `{protocol_id}.private.json`
filename, cross-check acquisition/scope/tool/dependency and all three public
leaf identities, and verify through Git that both private leaves are ignored
and untracked. A self-consistent JSON file plus its own sidecar is not treated
as external authority.

## Deterministic identity

Reproducibility depends on the complete derivation tuple, not only the raw PDF
hash:

```text
manifest + raw asset
+ explicit page scope
+ clean project Git commit
+ canonical pre-output build-spec hash
+ parser code/config/distribution
+ Python + Unicode database + dependency lock
+ normalization and page statuses
+ chunker code/config
= semantic Source catalog + semantic Chunk manifest
```

Canonical artifacts use sorted compact UTF-8 JSON with one terminal LF. The
LF participates in SHA-256. Deterministic epoch timestamps in the in-memory
product projection prevent wall-clock time from changing equality; they are
not audit-event timestamps.

## Historical authority and current replay readiness

These are intentionally different capabilities:

| Capability | Proves | Does not prove |
| --- | --- | --- |
| `FrozenProtocolAuthority` | frozen protocol/public sidecars, redacted leaves, semantic cross-bindings, and exact derivation blobs in the recorded Git commit remain valid | today's checkout or private PDF can rerun the build |
| `ReplayReadyFrozenProtocolAuthority` | the historical authority reloads; current tracked closure, tool/config/lock/runtime identities and clean worktree match; the exact authoring PDF is locally verified | the future build has completed or produced equal output hashes |
| completed replay comparison | rebuilt catalog and Chunk hashes match the frozen identities; a new summary records the replay commit | human Concept/Relation accuracy |

This split lets a later builder refactor preserve old experimental evidence.
`load_historical_frozen_protocol` reads bounded Git blobs by recorded commit and does not
compare ordinary orchestration files to the live worktree. Replay readiness is
ephemeral and fail-closed; the builder still repeats its own pre/post revision,
closure, asset, and tool checks before issuing output authority.

## Acceptance matrix

| Gate | Expected evidence | Current automated coverage |
| --- | --- | --- |
| exact asset and partition | no path/glob input; authoring-only receipt; manifest drift rejected | acquisition tests plus builder authority-drift test |
| parser safety | byte identity, encrypted/oversized/page-count/total-text rejection, typed page failures | acquisition tests plus `test_golden_graph_source_slice_primitives.py` |
| deterministic semantics | exact NFKC/LF behavior; multibyte UTF-8 boundaries; stable overlap and complete coverage | primitive tests |
| private/public separation | no Source text or `text` key in exported DTO bytes | builder redaction test |
| scope integrity | exhaustive status inventory; selected blank/failed pages rejected; unselected text absent | builder mapping/scope tests |
| derivation lineage | historical commit blobs remain verifiable while current code/runtime drift blocks only replay readiness | builder/command stale-import tests, protocol Git-blob validation, and C1-build/C2-publish/C3-evolution regression |
| authority integrity | private/public Chunk text, offsets, ordinals, counts, and hashes must agree | constructor guard plus tampered-private-text issuer test; broader mutation matrix remains a useful extension |
| failure atomicity | worker timeout cleans private temp; max-Chunk failure emits no partial authority; public conflict never overwrites | builder timeout/limit/writer tests |
| real CS336 replay | identical redacted leaf hashes from the exact registered whole Lecture 3 deck | initial clean build and detached clean-worktree replay produced identical catalog, Chunk, and summary hashes |
| protocol freeze | redacted leaves and confidence floors accepted by historical and strict loaders | complete; frozen protocol SHA-256 `e09c9128...8174f` |

The synthetic tests prove contracts and failure behavior. They do not establish
CS336 Concept/Relation accuracy or benchmark validity.

## Recorded CS336 G0.2 outcome

| Identity | Frozen value |
| --- | --- |
| Derivation commit | `cd0651624a39edc1b932a12f1fe0f63c8d398ca3` |
| Whole-deck inventory | 68 pages included, 0 excluded, 0 blank, 0 parse failures |
| Chunk inventory | 68 page-local Chunks |
| Semantic Source catalog | `18c49f521502cc207f0342ad67c527db71cf695759c4fb7342eb40eaa3638b50` |
| Semantic Chunk manifest | `6e238c534f3d63fb49c495588b3bca37e9717b412ad6bf23560ed8afa8b66b09` |
| Source-slice build summary | `ae2876c4d4354810ea8cee482f52d5a3016531219f3897d2167d5a0bb56333ff` |
| Bound draft artifact | `f08162714e32b8b0e41a7d619e96da4bd7c2d473db07956442d9d59c3ace41a4` |
| Frozen protocol | `e09c91283a44e9cf2ebb6094a6ecbc6dec85d5f32c4dc82c1a5c65135838174f` |

The initial publication and a no-write replay in a temporary detached worktree
at the same derivation commit produced the same three redacted hashes. The
temporary worktree, copied private PDF, and isolated environment were removed
after comparison. The ignored private materialization then reloaded against
the frozen protocol with 68 pages and 68 Chunks. These are reproducibility and
lineage results, not model or graph-quality metrics.

## Decisions, issues, and learning notes

- **Why a subprocess:** PDF is untrusted structured input. Process isolation,
  a sanitized environment, bounded IPC, and a wall-clock deadline reduce the
  blast radius and make cleanup reliable. `python -I` is not an OS sandbox and
  does not impose a hard memory quota; stronger hostile-input isolation would
  require a container or platform resource controls.
- **Why NFKC and UTF-8 offsets:** normalization gives stable semantic bytes
  across equivalent Unicode forms, while byte offsets make hashes and Chunk
  coverage language-independent. NFKC can intentionally fold presentation
  distinctions, so its version is part of the evidence identity.
- **Why page-local windows:** a PDF page is the stable user-facing locator.
  Cross-page Chunks would make citation navigation and page-scope review less
  precise.
- **Why redacted leaves:** the upstream license permits use, but the project
  conservatively avoids redistributing slide bodies and third-party figures.
  Hashes and locators provide reproducibility without copying Source content.
- **Known extraction limit:** plain PDF text can have poor reading order and
  image-only pages provide no grounded text. OCR and layout reconstruction are
  explicitly outside v1 rather than silently treated as reliable evidence.
- **Known runtime gap:** the adapter and reloadable private envelope prove
  compatibility with canonical `CourseSource` DTOs. Transactional database
  publication, indexing, retries, and Source-revision invalidation remain
  future product work.
- **Known evaluation gap:** the CS336 human Concept inventory,
  delayed Relation passes, `GoldBundleSeal`, and all accuracy/path results are
  still pending. This module supports those steps; it does not pre-claim them.

## Related records

- [ADR-0009: deterministic redacted Source slices](../decisions/ADR-0009-deterministic-redacted-source-slice.md)
- [Golden Graph evaluation protocol](golden-graph-evaluation-protocol.md)
- [Public-course benchmark acquisition](public-course-benchmark-acquisition.md)
- [Source projection generation](source-projection-generation.md)
- [Graph annotation protocol](../graph-annotation-protocol.md)
- [ADR-0008: evidence-grounded Concept graph](../decisions/ADR-0008-evidence-grounded-concept-graph-and-deterministic-paths.md)
