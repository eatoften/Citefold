# Public-course benchmark acquisition

This research-only package acquires externally hosted benchmark material by a
frozen manifest. It does not parse Sources, generate Concepts, run evaluation,
or import anything into the product database.

The canonical `cs336-sp25-v1` manifest pins Stanford CS336 Spring 2025 at
commit `b98b08a98d9d47a69bbdcb4e96a58aa48ee4d13b`. At registration, each of its
eight PDFs was checked against the Git tree blob SHA-1 and byte size, then
hashed independently with SHA-256. The manifest records the repository's MIT
[license pinned to the same commit](https://raw.githubusercontent.com/stanford-cs336/spring2025-lectures/b98b08a98d9d47a69bbdcb4e96a58aa48ee4d13b/LICENSE)
but conservatively sets `redistribution_allowed=false` for every PDF; the files
are never committed to this repository.

## Safety boundary

- only HTTPS URLs on the code-owned `raw.githubusercontent.com` allowlist;
- redirects are checked before following and the final URL must equal the
  registered URL;
- socket timeout plus a monotonic whole-asset deadline, exact
  `Content-Length`, media-type allowlist, PDF signature, per-file/count/total
  byte ceilings, SHA-256, and Git blob SHA-1;
- temporary file in the destination directory followed by no-overwrite atomic
  publication;
- explicit output paths are normalized without resolving through links; every
  existing path component and existing asset is rejected if it is a symlink or
  Windows reparse point;
- existing assets must be regular single-link files whose descriptor identity,
  size, link count, modification time, and change time remain stable across the
  complete read window;
- no execution, no executable permission, no archive expansion, no manifest
  mutation, and no automatic acceptance of upstream changes;
- existing mismatched files cause a failure instead of silent replacement.

The default destination is under the repository's existing ignored
`backend/data/` root. Assets are physically separated as
`<root>/<partition>/<filename>` and default acquisition excludes
`sealed_transfer`. Downstream runners must select registered manifest asset
IDs; the acquisition API rejects unknown IDs, duplicates, and IDs outside the
caller's authorized partitions before creating the output directory.
Recursively globbing the download root is forbidden because it can mix sealed
and development inputs.

A matching hash establishes byte identity, not PDF safety. The downstream
parser must still treat every PDF as untrusted input and run with its own
resource and feature limits. The no-overwrite hard link makes publication
atomic, but does not make it crash-durable; a process or machine crash can
leave a hidden `.part` file that a later maintenance pass may quarantine.

From `backend/`:

```powershell
uv run python -m benchmark_acquisition.fetch `
  --manifest benchmark_acquisition/manifests/cs336-sp25-v1.json
```

Use repeatable `--asset-id` arguments for a least-privilege exact selection.
Request order is preserved:

```powershell
uv run python -m benchmark_acquisition.fetch `
  --manifest benchmark_acquisition/manifests/cs336-sp25-v1.json `
  --asset-id lecture-03-architecture `
  --asset-id lecture-05-gpus
```

An exact ID is not an authorization bypass. For example, naming a sealed ID
without the sealed flag fails before any directory or network side effect.

The sealed assets require an explicit flag:

```powershell
uv run python -m benchmark_acquisition.fetch `
  --manifest benchmark_acquisition/manifests/cs336-sp25-v1.json `
  --include-sealed-transfer
```

Acquisition does not authorize opening or tuning on a sealed result. The
sealed partition is opened once for final evaluation after selection and
freeze; the evaluation protocol owns that access ledger.

Downstream authoring code must not accept a bare PDF path. The read-only public
entry point derives the sole local path from the manifest and repository root,
defaults to `authoring`, performs the complete local verification again, and
returns a frozen receipt:

```python
from pathlib import Path

from benchmark_acquisition import load_manifest, verify_registered_asset

manifest = load_manifest(
    Path("benchmark_acquisition/manifests/cs336-sp25-v1.json")
)
receipt = verify_registered_asset(
    manifest,
    "lecture-03-architecture",
    repository_root=Path.cwd().parent,
)
```

`verify_registered_asset` never creates a missing directory, accepts no output
path or glob, and only recognizes the canonical ignored path
`backend/data/public_course_benchmarks/<corpus_id>/<partition>/<filename>`.
The receipt captures the registered identities plus the verified file identity
and mtime/ctime snapshot. It proves one completed verification, not that the
path can never change afterward; a privileged consumer should keep its own
open/revalidation boundary appropriately narrow.

The command is a maintainer-run local tool. Its output root must not be writable
by an untrusted process concurrently with acquisition. Component-by-component
symlink/reparse checks and file-handle identity checks fail closed on ordinary
redirection and replacement, but they are not a filesystem sandbox against a
same-user adversary racing directory entries.

## Redistributable trust fixture

The short, original, fictional CC0-1.0 fixture is deliberately split:

- `counterfactual-mini-course-v1.source.json` contains only ingestible Source
  text and license metadata;
- `counterfactual-mini-course-v1.gold.json` contains labels, evidence spans,
  structured claims, citation contracts, and refusal contracts.

The exact sidecars are
[`counterfactual-mini-course-v1.source.sha256`](fixtures/counterfactual-mini-course-v1.source.sha256)
and
[`counterfactual-mini-course-v1.gold.sha256`](fixtures/counterfactual-mini-course-v1.gold.sha256).
The gold artifact also binds the Source artifact hash. The strict loader
rejects labels in the Source artifact, dangling locators or quotes, wrong span
hashes, non-production relation types, noncanonical symmetric endpoints,
pedagogical evidence that does not match its endpoint Concept, and invalid
support/refusal contracts.

This four-question fixture is only a trust/schema smoke test. It must not be
used to report Concept proposal precision/recall or to satisfy public-course
quality gates; it is not a closed-world Concept inventory.

## Tests

Focused tests are offline and inject byte streams; they never contact GitHub.
They include exact-ID authorization, canonical-path/no-side-effect verification,
hard-link/symlink rejection, and simulated read-window metadata drift:

```powershell
uv run pytest tests/test_benchmark_acquisition.py -q
```

See [the module design](../../docs/modules/public-course-benchmark-acquisition.md)
and [the evaluation contract](../../docs/evaluation/public-course-benchmark.md).
