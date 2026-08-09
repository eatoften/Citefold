# ADR-0009: Build Deterministic, Redacted Source Slices for Evaluation

- **Status:** Accepted; G0.2b builder and whole-deck specification implemented,
  real CS336 slice/freeze pending
- **Date:** 2026-08-08
- **Decision owners:** Project maintainer and Codex implementation agent

## Context

The acquisition layer can prove that a local PDF is the exact registered
public-course asset. The G0.2a protocol can bind parser, Chunk, annotation, and
evaluation identities. Neither layer previously performed the derivation from
verified PDF bytes to the semantic pages and Chunks that human annotation and
later grounded evaluation will consume.

Using ad hoc `pypdf` calls or the product's ordinary ingestion path would leave
four unresolved problems:

- untrusted PDFs could be parsed in the main backend without evaluation-specific
  resource bounds;
- a page-selection or normalization change could silently redefine evidence;
- committing extracted slide text would violate the project's conservative
  redistribution policy;
- an evaluation-only parser could create a second Source model disconnected
  from the product's canonical `CourseSource/CourseSourceChunk/Locator` model.

The project therefore needs a reproducible derivation boundary that preserves
private text for local annotation while publishing only identities and
locators.

## Decision

### 1. Start from exact manifest authority, never a caller path

The builder accepts `ManifestAuthority` plus one exact asset ID, reloads the
tracked manifest, and obtains a read-only `VerifiedAssetReceipt` from the
acquisition module. Only `authoring` assets are accepted. Paths, globs,
development assets, and sealed-transfer assets cannot enter this workflow.

The verifier and parser both check byte identity and stable regular-file,
single-link, non-reparse metadata. This duplicated check is intentional: the
asset may change between acquisition verification and parser execution.

### 2. Treat PDF parsing as an isolated, resource-bounded derivation

Run a self-contained `pypdf` plain-text worker under `python -I` with a reduced
environment, private IPC files, bounded output, and a whole-asset timeout.
Reject encrypted PDFs. Bound raw bytes, page count, semantic bytes per page,
total semantic bytes, and resulting Chunk count. Page-local extraction failures
are typed and recorded; asset-level identity, initialization, encryption,
timeout, or aggregate-limit failures abort the entire authority.

Bind parser and Chunker implementation hashes, canonical configs, exact
dependency versions, Python and Unicode database versions, and `uv.lock`.
Hashing only the raw PDF is insufficient to reproduce semantic evidence.

### 3. Define semantic page bytes explicitly

V1 applies Unicode NFKC followed by CRLF/CR-to-LF conversion. It performs no
OCR, whitespace trimming, layout repair, or reading-order correction. The
normalized UTF-8 bytes are the sole basis for page and Chunk hashes and
offsets. Any semantic normalization change creates a new tool/config identity.

### 4. Keep page scope protocol-owned, objective, and status-exhaustive

The draft protocol supplies a non-empty exact page set; the builder sorts and
validates it but never selects pages. The CS336 Lecture 3 v1 protocol uses a
whole-deck anti-cherry-picking rule: include all 68 physical pages after the
deterministic parse verified that each was successful and non-blank. Every
page remains represented as
`included`, `blank`, `parse_failed`, or `excluded`. Selected pages must have
successfully parsed, non-blank text. A selected blank or failed page aborts
instead of disappearing from the denominator.

Successfully parsed pages outside the scope become `excluded/out_of_scope`.
Their public hash and byte count are replaced by the empty identity so the
redacted artifact does not fingerprint unselected page text.

### 5. Use deterministic, page-local UTF-8 Chunks

Chunk normalized page bytes with code-point-safe sliding windows. V1 permits
bounded overlap but requires the union to cover every selected byte, including
the tail. Cross-page Chunks are forbidden. Each public Chunk binding records a
contiguous ordinal, semantic hash, logical page ID, and half-open UTF-8 byte
offsets.

### 6. Separate public lineage from private materialization

The only publishable DTOs are:

- `SourceSliceBuildSummary`;
- `SemanticSourceCatalog`;
- `ChunkManifest`.

They contain identities, page statuses, hashes, counts, tool lineage, and
locators, but no PDF/page/Chunk text, quote, local path, product ID, or
materialization plan. Canonical JSON plus SHA-256 sidecars is written under
`backend/golden_graph/artifacts/` with no conflicting overwrite.

PDF bytes and temporary page projections stay under gitignored
`backend/data/`. Selected text is exposed through a private process-local
authority and may be durably materialized only as a strict canonical envelope
under the ignored Source-slice boundary. That envelope is atomically
published, conflict-safe, hash-bound, reloadable, and absent from public CLI
receipts. Public serializers are type-gated, exact-schema-gated, and reject
source-bearing field names.

Public artifact publication and licensed private retention are separate,
default-off command flags. The private envelope binds the protocol ID and the
canonical pre-output build-spec hash. Its writer/loader require the exact
protocol, deterministic filename, expected public leaf identities, and a Git
ignored/untracked target; a sidecar is an integrity check, not a trust root.

### 7. Reuse product Source DTOs without writing the runtime database

Build deterministic `CourseSource` and `CourseSourceChunk` objects with typed
`PdfPageLocator` values and production-compatible Source IDs. Persist page-local
UTF-8 offsets and golden catalog/manifest hashes in locator metadata. Compute
the runtime projection hash with the production projection-manifest function;
keep it separate from the evaluation Chunk-manifest hash. This demonstrates
that G2 can consume the canonical product shape without inventing an
evaluation-only Source model.

Do not persist these DTOs into the runtime database, publish embeddings, or
mutate Source revisions in G0.2b. Future runtime materialization must use the product's transactional
projection store, generation/currentness rules, retry behavior, and indexing
workflow. That integration requires a separate implementation checkpoint.

### 8. Bind exact executed code and make authority issuance fail closed

Only `build_source_slice` may issue `SourceSliceBuildAuthority`. Issuance
revalidates asset, tool, dependency, public-artifact, private-text, ordinal,
locator, and count bindings. A timeout, drift, malformed worker response, or
partial Chunk set yields no authority. The separate public writer never
overwrites an existing conflicting leaf.

The build requires a clean Git `HEAD`, records that project commit and the
pre-output build-spec hash in the redacted summary, executes the parser from an
exclusive snapshot of its already-verified bytes, and loads the Chunker from
its captured verified bytes. Builder and command also capture and recheck the
fixed v1 orchestration source closure before and after derivation. The freeze
gate binds the summary path/hash and cross-checks every derivation identity,
inventory count, Git object, and recorded commit. This closes stale-import and
verify-then-reopen gaps without pretending Python process isolation is a
hostile-input sandbox.

Historical authority and current replay readiness use different token-gated
receipts. A historical loader verifies the protocol, redacted public leaves,
and exact input/orchestration blobs from the recorded commit; it does not
require today's checkout or installed runtime to equal that commit. A replay
readiness gate additionally verifies current tracked leaves, Python/Unicode and
distribution identities, a clean worktree, and the exact local authoring PDF.
Freeze and recovery run the current tracked-leaf/runtime gate both before and
after publication; they do not claim the worktree is clean or the private PDF
is present. The separate replay-readiness gate adds those checks. A readiness
receipt is momentary and does not claim output equality; the builder must still
complete and compare the rebuilt hashes. V1 verifies the exact lockfile and
declared/installed dependency versions, not a cryptographic attestation of all
installed third-party package bytes.

## Alternatives considered

### Parse inside the backend process

Rejected. It would share memory, imports, and failure state with the API
process and make whole-asset timeout/cleanup less reliable.

### Reuse ordinary document ingestion unchanged

Rejected for the evaluation authority. Product ingestion optimizes interactive
workflow and persistence; the benchmark needs exact tool/config/lock lineage,
exhaustive page status, explicit scope, and redacted public artifacts. The two
paths still converge on the canonical product DTO model.

### Commit extracted text for easy reproduction

Rejected. It would broaden redistribution of slide bodies and embedded
third-party material. Registered byte hashes, semantic hashes, and locators
are sufficient public lineage; maintainers reacquire the exact upstream PDF.

### Let the parser or an LLM choose relevant pages

Rejected. Page scope changes the closed world presented to the annotator and
therefore must be an explicit protocol input owned by the maintainer.

### Chunk by characters or tokens

Rejected for v1. Character counts vary in encoded size, while tokenizer
versions would add model-specific dependencies. UTF-8 byte windows give exact,
language-independent hashing and coverage.

### Persist the slice immediately

Deferred. Direct persistence would bypass the existing Source projection
generation/currentness transaction and couple evaluation construction to a
mutable user database before the semantic contract is frozen.

## Consequences

Positive:

- the raw-PDF-to-Chunk lineage is replayable and content-addressed;
- public artifacts remain useful for review without redistributing Source
  bodies;
- page omissions and extraction failures are visible rather than silently
  changing evaluation scope;
- G2 can reuse the product's canonical Source and Locator types;
- historical frozen authority remains readable after normal implementation
  evolution;
- malformed input, code/config drift, and partial output fail before authority
  crosses the boundary.

Costs and limitations:

- NFKC and plain PDF extraction can lose presentation distinctions or reading
  order, and OCR is unavailable in v1;
- subprocess isolation is not a hostile-input OS sandbox or hard memory quota;
- exact Python, Unicode, package, implementation, config, and lock bindings
  make intentional upgrades require new artifacts;
- consumers must distinguish historical authority from the stronger,
  short-lived replay-ready receipt;
- the in-memory authority cannot yet be resumed from the product database;
- a redacted deterministic slice proves engineering reproducibility, not
  Concept accuracy, graph quality, learning value, or held-out generalization.

## Validation gates

- exact authoring asset selection and manifest/file drift rejection;
- encrypted, malformed, oversized, excessive-page, excessive-total-text, and
  timeout rejection;
- typed blank/page-failure semantics with an exhaustive page inventory;
- exact NFKC/LF normalization and deterministic multibyte UTF-8 windows;
- complete selected-page byte coverage with bounded overlap and no cross-page
  Chunks;
- parser/chunker/config/dependency/lock drift rejection;
- public Source-text redaction and no-overwrite canonical publication;
- cross-binding validation between redacted leaves and private product DTOs;
- cleanup and no partial authority on every handled failure;
- one reproducible real-CS336 whole-deck replay from the registered protocol;
- protocol freeze only after the real redacted leaves and remaining confidence
  floors validate.

The implementation gates through failure cleanup have automated coverage. The
real-CS336 replay and protocol freeze remain G0.2 acceptance work. Human
Concept/Relation gold and model metrics remain G2 and later work.

## Related records

- [Golden Graph Source-Slice Builder](../modules/golden-graph-source-slice-builder.md)
- [Golden Graph Evaluation Protocol](../modules/golden-graph-evaluation-protocol.md)
- [Public Course Benchmark Acquisition](../modules/public-course-benchmark-acquisition.md)
- [Source Projection Generation](../modules/source-projection-generation.md)
- [ADR-0008: Evidence-Grounded Concept Graph](ADR-0008-evidence-grounded-concept-graph-and-deterministic-paths.md)
