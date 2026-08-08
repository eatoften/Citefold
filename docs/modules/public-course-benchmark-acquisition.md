# Public Course Benchmark Acquisition

- **Status:** acquisition boundary implemented; no quality result claimed
- **Package:** [`backend/benchmark_acquisition`](../../backend/benchmark_acquisition/README.md)
- **Evaluation owner:** [Public Course Benchmark Contract](../evaluation/public-course-benchmark.md)

## Responsibility

This module turns a reviewed external-source registration into verified local
bytes. It exists so later ingestion and evaluation never depend on a moving
URL, an unbounded downloader, or PDFs accidentally committed to the portfolio.

```text
reviewed canonical manifest
  -> HTTPS/domain/redirect policy
  -> count/aggregate/asset-deadline bounded temporary download
  -> type + size + PDF-magic checks
  -> SHA-256 + Git-blob identity checks
  -> no-overwrite publication under backend/data/<partition>/
  -> separate Source ingestion and benchmark runners
```

It deliberately does **not** own parsing, chunking, annotation, Concept
generation, metric calculation, or sealed-test authorization. Those are
separate modules so acquisition cannot silently influence labels or results.

## Canonical corpus registration

`cs336-sp25-v1.json` fixes:

- Stanford `stanford-cs336/spring2025-lectures` at commit
  `b98b08a98d9d47a69bbdcb4e96a58aa48ee4d13b`;
- all eight PDFs in that commit's `nonexecutable/` directory;
- course, term, attribution, source repository, and the
  [commit-pinned MIT license](https://raw.githubusercontent.com/stanford-cs336/spring2025-lectures/b98b08a98d9d47a69bbdcb4e96a58aa48ee4d13b/LICENSE);
- the authoring/development/sealed-transfer partition registered by the
  evaluation protocol;
- relative path, canonical raw URL, byte size, media type, Git blob SHA-1, and
  independently computed SHA-256 for every asset;
- the commit-pinned MIT license identity and conservative
  `redistribution_allowed=false` policy.

The PDF hashes were computed only after the downloaded byte count and Git blob
hash matched the upstream Git tree. Duplicate SHA-256 or Git blob identities
are rejected even across partitions. A changed upstream file cannot be
accepted under this manifest, even if its filename remains the same.

## Failure model

The downloader fails closed on an unknown manifest key, unpinned URL, path
traversal, unsupported partition, non-HTTPS or non-allowlisted endpoint,
unsafe redirect, missing/wrong response headers, overrun, wrong PDF signature,
either hash mismatch, aggregate/count overflow, whole-asset deadline, reserved
Windows name, symlink, executable existing file, or invalid existing output.
It leaves no published partial file during handled failures and never repairs
or deletes a mismatched existing asset automatically.

Directory validation walks every existing component without resolving the
caller path and rejects both symlinks and Windows reparse points. Existing
assets are checked before open, against the opened handle, and again afterward.

Hash equality proves identity, not that a PDF is safe. Parsing remains an
untrusted-input boundary with separate resource limits. Hard-link publication
is no-overwrite and atomic, but not a claim of crash durability; abrupt process
or machine failure may leave a hidden `.part` file for later quarantine.

The manifest is intentionally strict and code-reviewed. Expanding the host
allowlist or changing a checksum is a source-registration change, not an
ordinary runtime event.

This is a maintainer-operated local acquisition boundary, not a hostile
multi-tenant downloader. The output root must not be concurrently writable by
an untrusted process. Parent-component symlink/reparse rejection plus
pre-open/handle/post-open identity checks close ordinary redirection and file
replacement, but do not claim to sandbox a same-user attacker racing directory
entries.

## Rights and leakage boundary

The upstream repository declares MIT, but slides can contain third-party
figures whose file-level provenance is not enumerated. Therefore only the
manifest, short maintainer-authored labels, hashes, and attribution are
versioned here. Downloaded PDFs stay in the already gitignored `backend/data/`
tree and must not appear in release bundles.

The command omits `sealed_transfer` by default and stores every partition in a
different physical directory. Evaluation runners must accept exact manifest
asset IDs, not filesystem globs. Explicit acquisition is still not permission
to tune against sealed outputs; those outputs are opened once for final
evaluation after selection and protocol freeze.

## Counterfactual control

The checked-in CC0 mini-course separates ingestible Source bytes from gold
labels/questions, freezes each with its own sidecar, and binds gold to the
Source hash. A strict typed loader validates exact keys, unique IDs,
Source/locator/quote/span references, production relation ontology,
role-aware evidence, structured claims, citations, and refusal contracts.
Symmetric relations use the same canonical endpoint order as the product, and
pedagogical source/target evidence must match the corresponding gold Concept
evidence.

It is not a proxy for CS336 quality or a closed-world Concept inventory. It is
only a deterministic trust/schema smoke control for four failure modes:

1. answering from model priors instead of the supplied text;
2. losing exact section locators;
3. inventing an answer when the fixture requires refusal;
4. producing a Concept relation without its registered evidence.

It must never contribute to reported Concept proposal precision/recall. Any
edit requires a new fixture version and review, rather than updating either
hash in place after a reported experiment.

## Verification

The offline test slice validates canonical identities, content and partition
disjointness, aggregate limits, the separated CC0 fixture contract, manifest
tamper rejection, physical partitioning, deadline enforcement, symlink
rejection where the platform permits it, successful publication,
existing-file reuse, and cleanup after wrong type/hash. It uses injected
responses and never performs a network request.
