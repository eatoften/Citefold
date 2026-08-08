"""Authority binding and freeze gates for golden-graph protocols."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import stat
import subprocess
import tempfile
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Mapping, get_args

from pydantic import ValidationError

from app.concept_graph import (
    ConceptRelationType,
    RelationEvidenceSupportRole,
    RelationSupportBasis,
)
from benchmark_acquisition.manifest import CorpusManifest, ManifestError, parse_manifest

from .bindings import (
    ChunkManifest,
    DependencySnapshot,
    PdfParserConfigV1,
    SemanticSourceCatalog,
    Utf8ChunkerConfigV1,
)
from .canonical_io import (
    CanonicalArtifactError,
    MAX_PROTOCOL_BYTES,
    MAX_SIDECAR_BYTES,
    canonical_json_bytes,
    load_hashed_canonical_json,
    read_bounded_regular_bytes,
)
from .schemas import (
    GoldenGraphProtocol,
    MetricId,
    MetricTarget,
    ToolIdentity,
    V1ConceptRelationType,
    V1RelationEvidenceSupportRole,
    V1RelationSupportBasis,
)


FORBIDDEN_PUBLIC_CONTENT_KEYS = frozenset(
    {
        "text",
        "quote",
        "exact_quote",
        "source_text",
        "chunk_text",
        "slide_text",
        "page_text",
        "transcript",
        "transcript_text",
    }
)

# These are the already-registered v1 point targets. Relation recall remains a
# mandatory reported metric but deliberately has no pass threshold.
EXPECTED_V1_TARGETS: Mapping[str, tuple[str, float, str]] = MappingProxyType({
    "accepted_current_evidence_validity": ("eq", 1.0, "proportion"),
    "graph_integrity_violation_count": ("eq", 0.0, "count"),
    "deterministic_path_hash_rate": ("eq", 1.0, "proportion"),
    "golden_path_validity": ("eq", 1.0, "proportion"),
    "edge_evidence_completeness": ("eq", 1.0, "proportion"),
    "locator_open_rate": ("eq", 1.0, "proportion"),
    "concept_inventory_coverage": ("gte", 0.80, "proportion"),
    "accepted_current_isolate_rate": ("lte", 0.15, "proportion"),
    "retrieval_recall_at_5": ("gte", 0.85, "proportion"),
    "citation_precision": ("gte", 0.95, "proportion"),
    "citation_recall": ("gte", 0.85, "proportion"),
    "abstention_f1": ("gte", 0.85, "proportion"),
    "concept_proposal_f1": ("gte", 0.80, "proportion"),
    "concept_evidence_precision": ("gte", 0.95, "proportion"),
    "relation_proposal_precision": ("gte", 0.80, "proportion"),
    "path_api_p95_1000_nodes_ms": ("lte", 200.0, "milliseconds"),
    "path_api_p95_10000_nodes_ms": ("lte", 1_000.0, "milliseconds"),
})

EXPECTED_METRIC_PROTOCOLS: Mapping[str, tuple[str, str]] = MappingProxyType({
    "accepted_current_evidence_validity": (
        "authoring_graph_invariants",
        "gold_bundle_seal",
    ),
    "graph_integrity_violation_count": (
        "authoring_graph_invariants",
        "gold_bundle_seal",
    ),
    "deterministic_path_hash_rate": (
        "authoring_graph_invariants",
        "gold_bundle_seal",
    ),
    "golden_path_validity": ("authoring_graph_invariants", "gold_bundle_seal"),
    "edge_evidence_completeness": (
        "authoring_graph_invariants",
        "gold_bundle_seal",
    ),
    "locator_open_rate": ("authoring_graph_invariants", "gold_bundle_seal"),
    "concept_inventory_coverage": (
        "future_confirmatory_claim_gate",
        "gold_bundle_seal",
    ),
    "accepted_current_isolate_rate": (
        "future_confirmatory_claim_gate",
        "gold_bundle_seal",
    ),
    "retrieval_recall_at_5": (
        "future_confirmatory_claim_gate",
        "grounded_answer_evaluation_bundle",
    ),
    "citation_precision": (
        "future_confirmatory_claim_gate",
        "grounded_answer_evaluation_bundle",
    ),
    "citation_recall": (
        "future_confirmatory_claim_gate",
        "grounded_answer_evaluation_bundle",
    ),
    "abstention_f1": (
        "future_confirmatory_claim_gate",
        "grounded_answer_evaluation_bundle",
    ),
    "concept_proposal_f1": (
        "future_confirmatory_claim_gate",
        "gold_bundle_seal",
    ),
    "concept_evidence_precision": (
        "future_confirmatory_claim_gate",
        "gold_bundle_seal",
    ),
    "relation_proposal_precision": (
        "future_confirmatory_claim_gate",
        "gold_bundle_seal",
    ),
    "path_api_p95_1000_nodes_ms": (
        "synthetic_graph_performance",
        "synthetic_graph_performance_v1",
    ),
    "path_api_p95_10000_nodes_ms": (
        "synthetic_graph_performance",
        "synthetic_graph_performance_v1",
    ),
})

_STATISTICAL_TARGETS = frozenset(
    {
        "concept_inventory_coverage",
        "accepted_current_isolate_rate",
        "retrieval_recall_at_5",
        "citation_precision",
        "citation_recall",
        "abstention_f1",
        "concept_proposal_f1",
        "concept_evidence_precision",
        "relation_proposal_precision",
    }
)
_ZERO_SHA256 = "0" * 64
_MAX_BOUND_ARTIFACT_BYTES = 128 * 1024 * 1024
_FROZEN_PROTOCOL_DIRECTORY = Path("backend/golden_graph/protocols")
_PUBLIC_BINDING_PREFIX = ("backend", "golden_graph", "artifacts")
_FROZEN_AUTHORITY_TOKEN = object()
_V1_RELATION_TYPES = get_args(V1ConceptRelationType)
_V1_SUPPORT_BASES = get_args(V1RelationSupportBasis)
_V1_SUPPORT_ROLES = get_args(V1RelationEvidenceSupportRole)


class GoldenGraphProtocolError(ValueError):
    """Raised when a protocol cannot be loaded or promoted to frozen."""


@dataclass(frozen=True, slots=True)
class ManifestAuthority:
    """A validated acquisition manifest plus its exact raw byte identity."""

    manifest: CorpusManifest
    manifest_sha256: str
    manifest_path: str

    def __post_init__(self) -> None:
        if (
            len(self.manifest_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.manifest_sha256)
        ):
            raise ValueError("manifest_sha256 must be lowercase SHA-256")
        if not self.manifest_path or "\\" in self.manifest_path:
            raise ValueError("manifest_path must be a repository-relative POSIX path")


@dataclass(frozen=True, slots=True)
class FrozenProtocolAuthority:
    """Receipt proving full validation of one persisted protocol artifact.

    A bare ``GoldenGraphProtocol(protocol_status='frozen')`` is data, not this
    authority. It is also not a later gold-bundle seal receipt. Consumers that
    require a frozen protocol must re-load its canonical artifact and leaves.
    """

    protocol: GoldenGraphProtocol
    protocol_sha256: str
    acquisition_manifest_sha256: str
    artifact_path: Path
    _validation_token: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._validation_token is not _FROZEN_AUTHORITY_TOKEN:
            raise ValueError(
                "FrozenProtocolAuthority must come from load_frozen_protocol"
            )
        if self.protocol.protocol_status != "frozen":
            raise ValueError("Frozen authority cannot contain a draft protocol")
        for label, digest in (
            ("protocol_sha256", self.protocol_sha256),
            ("acquisition_manifest_sha256", self.acquisition_manifest_sha256),
        ):
            if len(digest) != 64 or any(
                char not in "0123456789abcdef" for char in digest
            ):
                raise ValueError(f"{label} must be lowercase SHA-256")
        if not self.artifact_path.is_absolute():
            raise ValueError("Frozen authority artifact_path must be absolute")


def load_manifest_authority(
    path: Path,
    *,
    repository_root: Path | None = None,
) -> ManifestAuthority:
    """Parse one acquisition manifest using its existing strict authority."""

    root = (repository_root or Path(__file__).resolve().parents[2]).resolve()
    try:
        resolved_path = path.resolve()
        logical_path = resolved_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise GoldenGraphProtocolError(
            "Acquisition manifest must be inside the repository root"
        ) from exc
    resolved_path = _resolve_bound_repository_file(
        root,
        logical_path,
        require_tracked=True,
    )
    try:
        payload = read_bounded_regular_bytes(
            resolved_path,
            max_bytes=MAX_PROTOCOL_BYTES,
            label="acquisition manifest",
        )
        decoded = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
        )
        manifest = parse_manifest(decoded)
    except (
        CanonicalArtifactError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ManifestError,
        RecursionError,
        TypeError,
        ValueError,
        OverflowError,
    ) as exc:
        raise GoldenGraphProtocolError(
            f"Cannot load acquisition authority {path}: {exc}"
        ) from exc
    return ManifestAuthority(
        manifest=manifest,
        manifest_sha256=hashlib.sha256(payload).hexdigest(),
        manifest_path=logical_path,
    )


def load_protocol(path: Path) -> GoldenGraphProtocol:
    """Load a draft or frozen protocol without weakening freeze gates."""

    try:
        payload, _digest = load_hashed_canonical_json(path)
        _reject_public_content_fields(payload)
        return GoldenGraphProtocol.model_validate(payload)
    except (CanonicalArtifactError, ValidationError) as exc:
        raise GoldenGraphProtocolError(
            f"Invalid golden-graph protocol {path.name}: {exc}"
        ) from exc


def freeze_protocol(
    protocol: GoldenGraphProtocol,
    output_path: Path,
    authority: ManifestAuthority,
    *,
    repository_root: Path | None = None,
) -> FrozenProtocolAuthority:
    """Publish or recover one immutable canonical artifact, then revalidate it."""

    try:
        # ``model_copy(update=...)`` deliberately skips Pydantic validation.
        # Rebuild from plain data before deriving a path or publishing bytes so
        # an invalid in-memory model cannot permanently squat on an identity.
        protocol = GoldenGraphProtocol.model_validate(
            protocol.model_dump(mode="python", exclude_none=False)
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise GoldenGraphProtocolError(
            "Protocol must pass canonical schema validation before publication"
        ) from exc
    if protocol.protocol_status != "draft":
        raise GoldenGraphProtocolError("Only a draft protocol can be published")
    root = (repository_root or Path(__file__).resolve().parents[2]).resolve()
    expected_path = _expected_frozen_protocol_path(root, protocol.protocol_id)
    if not _is_exact_frozen_protocol_location(
        output_path,
        repository_root=root,
        protocol_id=protocol.protocol_id,
    ):
        raise GoldenGraphProtocolError(
            f"Frozen protocol path must be {expected_path}"
        )
    validate_protocol_for_freeze(
        protocol,
        authority,
        repository_root=root,
    )
    frozen = protocol.model_copy(update={"protocol_status": "frozen"})
    payload = canonical_json_bytes(frozen)
    protocol_sha256 = hashlib.sha256(payload).hexdigest()
    sidecar = f"{protocol_sha256}  {output_path.name}\n".encode("utf-8")
    sidecar_path = output_path.with_suffix(".sha256")
    if output_path.suffix != ".json":
        raise GoldenGraphProtocolError("Frozen protocol artifact must end in .json")
    if not output_path.parent.is_dir():
        raise GoldenGraphProtocolError("Frozen protocol output directory must exist")
    if output_path.exists() or sidecar_path.exists():
        return _recover_frozen_protocol_publication(
            output_path=output_path,
            payload=payload,
            sidecar=sidecar,
            authority=authority,
            repository_root=root,
        )

    try:
        _write_exclusive_durable(output_path, payload)
    except GoldenGraphProtocolError:
        if output_path.exists() or sidecar_path.exists():
            return _recover_frozen_protocol_publication(
                output_path=output_path,
                payload=payload,
                sidecar=sidecar,
                authority=authority,
                repository_root=root,
            )
        raise
    try:
        _write_exclusive_durable(sidecar_path, sidecar)
    except GoldenGraphProtocolError:
        if sidecar_path.exists():
            return _recover_frozen_protocol_publication(
                output_path=output_path,
                payload=payload,
                sidecar=sidecar,
                authority=authority,
                repository_root=root,
            )
        # A process crash or durable sidecar failure may leave the exact JSON.
        # The next identical call repairs it after byte-for-byte verification.
        raise
    return load_frozen_protocol(
        output_path,
        authority,
        repository_root=root,
    )


def load_frozen_protocol(
    path: Path,
    authority: ManifestAuthority,
    *,
    repository_root: Path | None = None,
) -> FrozenProtocolAuthority:
    """Re-read exact frozen bytes and all bound leaves into an authority receipt."""

    root = (repository_root or Path(__file__).resolve().parents[2]).resolve()
    _require_frozen_protocol_directory(path, repository_root=root)
    _require_stable_single_link(path)
    _require_stable_single_link(path.with_suffix(".sha256"))
    try:
        payload, protocol_sha256 = load_hashed_canonical_json(path)
        _reject_public_content_fields(payload)
        protocol = GoldenGraphProtocol.model_validate(payload)
    except (CanonicalArtifactError, ValidationError) as exc:
        raise GoldenGraphProtocolError(
            f"Invalid frozen golden-graph protocol {path.name}: {exc}"
        ) from exc
    if protocol.protocol_status != "frozen":
        raise GoldenGraphProtocolError("Protocol artifact is not marked frozen")
    if not _is_exact_frozen_protocol_location(
        path,
        repository_root=root,
        protocol_id=protocol.protocol_id,
    ):
        raise GoldenGraphProtocolError(
            "Frozen protocol artifact path differs from its protocol identity"
        )
    validate_protocol_for_freeze(
        protocol,
        authority,
        repository_root=root,
    )
    return FrozenProtocolAuthority(
        protocol=protocol,
        protocol_sha256=protocol_sha256,
        acquisition_manifest_sha256=authority.manifest_sha256,
        artifact_path=path.resolve(),
        _validation_token=_FROZEN_AUTHORITY_TOKEN,
    )


def validate_protocol_for_freeze(
    protocol: GoldenGraphProtocol,
    authority: ManifestAuthority,
    *,
    repository_root: Path | None = None,
) -> None:
    """Fail closed unless the protocol is reproducible and embargo-safe."""

    root = (repository_root or Path(__file__).resolve().parents[2]).resolve()
    reloaded_authority = load_manifest_authority(
        root / authority.manifest_path,
        repository_root=root,
    )
    if reloaded_authority != authority:
        raise GoldenGraphProtocolError(
            "Acquisition authority differs from its current repository artifact"
        )
    manifest = reloaded_authority.manifest
    acquisition = protocol.acquisition
    if acquisition.manifest_path != authority.manifest_path:
        raise GoldenGraphProtocolError("Acquisition manifest path mismatch")
    if acquisition.manifest_sha256 != authority.manifest_sha256:
        raise GoldenGraphProtocolError("Acquisition manifest SHA-256 mismatch")
    if acquisition.corpus_id != manifest.corpus_id:
        raise GoldenGraphProtocolError("Acquisition corpus identity mismatch")
    if acquisition.repository_commit_sha != manifest.commit_sha:
        raise GoldenGraphProtocolError("Acquisition repository commit mismatch")

    matching_assets = [
        asset for asset in manifest.assets if asset.asset_id == acquisition.asset_id
    ]
    if len(matching_assets) != 1:
        raise GoldenGraphProtocolError(
            "Protocol must bind exactly one registered acquisition asset"
        )
    asset = matching_assets[0]
    if asset.media_type != "application/pdf":
        raise GoldenGraphProtocolError(
            "The v1 golden-graph projection requires an application/pdf asset"
        )
    if acquisition.partition != asset.partition:
        raise GoldenGraphProtocolError("Acquisition asset partition mismatch")
    if asset.partition != "authoring":
        raise GoldenGraphProtocolError(
            "Golden-graph authoring must not use development or sealed-transfer assets"
        )
    if acquisition.raw_sha256 != asset.sha256:
        raise GoldenGraphProtocolError("Acquisition asset raw SHA-256 mismatch")
    if acquisition.license_spdx != asset.license_spdx:
        raise GoldenGraphProtocolError("Acquisition asset license mismatch")
    if acquisition.redistribution_allowed != asset.redistribution_allowed:
        raise GoldenGraphProtocolError(
            "Acquisition asset redistribution policy mismatch"
        )

    _validate_page_scope(protocol)
    _validate_projection(
        protocol,
        repository_root=root,
        registered_asset_bytes=asset.byte_size,
    )
    _validate_ontology(protocol)
    _validate_review(protocol, repository_root=root)
    _validate_evaluation(protocol)

    rights = protocol.rights
    if rights.attribution != manifest.attribution:
        raise GoldenGraphProtocolError("Rights attribution differs from manifest")
    if rights.license_spdx != asset.license_spdx:
        raise GoldenGraphProtocolError("Rights license differs from acquisition asset")
    if rights.redistribution_allowed != asset.redistribution_allowed:
        raise GoldenGraphProtocolError(
            "Rights redistribution policy differs from acquisition asset"
        )
    if not rights.public_artifacts_redacted or rights.public_source_text_included:
        raise GoldenGraphProtocolError("Public benchmark artifacts must remain redacted")
    if not protocol.release.path_evaluation_embargoed_until_gold_freeze:
        raise GoldenGraphProtocolError(
            "Path evaluation must remain embargoed until the gold fixture freezes"
        )


def _validate_page_scope(protocol: GoldenGraphProtocol) -> None:
    scope = protocol.page_scope
    if (
        scope.asset_page_count is None
        or scope.included_pages is None
        or scope.excluded_pages is None
        or scope.inclusion_reason is None
        or scope.exclusion_reason is None
    ):
        raise GoldenGraphProtocolError(
            "Frozen protocol requires an exact non-empty page scope and reasons"
        )
    if not scope.included_pages:
        raise GoldenGraphProtocolError("Frozen protocol page scope cannot be empty")
    included = set(scope.included_pages)
    excluded = set(scope.excluded_pages)
    if any(page > scope.asset_page_count for page in included | excluded):
        raise GoldenGraphProtocolError("Page scope contains an out-of-range page")
    if included & excluded:
        raise GoldenGraphProtocolError("Included and excluded pages overlap")
    expected = set(range(1, scope.asset_page_count + 1))
    if included | excluded != expected:
        raise GoldenGraphProtocolError(
            "Page scope must classify every page exactly once"
        )


def _validate_projection(
    protocol: GoldenGraphProtocol,
    *,
    repository_root: Path | None,
    registered_asset_bytes: int,
) -> None:
    projection = protocol.projection
    for logical_path in (
        projection.dependency_snapshot_path,
        projection.source_catalog_path,
        projection.chunk_manifest_path,
    ):
        if PurePosixPath(logical_path).parts[:3] != _PUBLIC_BINDING_PREFIX:
            raise GoldenGraphProtocolError(
                "Redacted evaluation leaves must stay under "
                "backend/golden_graph/artifacts"
            )
    required_hashes = {
        "dependency snapshot": projection.dependency_snapshot_sha256,
        "semantic Source catalog": projection.semantic_source_catalog_sha256,
        "Chunk manifest": projection.chunk_manifest_sha256,
    }
    if projection.parser is None or projection.chunker is None:
        raise GoldenGraphProtocolError(
            "Frozen protocol requires parser and chunker identities"
        )
    for label, tool in (("parser", projection.parser), ("chunker", projection.chunker)):
        implementation_parts = PurePosixPath(tool.implementation_path).parts
        config_parts = PurePosixPath(tool.config_path).parts
        if (
            not implementation_parts
            or implementation_parts[0] != "backend"
            or implementation_parts[:2] == ("backend", "data")
        ):
            raise GoldenGraphProtocolError(
                f"{label} implementation must be tracked backend source"
            )
        if config_parts[:3] != _PUBLIC_BINDING_PREFIX:
            raise GoldenGraphProtocolError(
                f"{label} config must be a tracked evaluation artifact"
            )
    for label, digest in required_hashes.items():
        if digest is None or digest == _ZERO_SHA256:
            raise GoldenGraphProtocolError(
                f"Frozen protocol requires a non-placeholder {label} SHA-256"
            )
    for label, tool in (("parser", projection.parser), ("chunker", projection.chunker)):
        if (
            tool.config_sha256 == _ZERO_SHA256
            or tool.implementation_sha256 == _ZERO_SHA256
        ):
            raise GoldenGraphProtocolError(
                f"Frozen protocol requires non-placeholder {label} code/config hashes"
            )

    root = (repository_root or Path(__file__).resolve().parents[2]).resolve()
    locked_versions = _load_uv_lock_versions(
        root,
        projection.uv_lock_path,
        projection.uv_lock_sha256,
    )
    dependencies = _load_binding(
        root,
        projection.dependency_snapshot_path,
        projection.dependency_snapshot_sha256,
        DependencySnapshot,
    )
    catalog = _load_binding(
        root,
        projection.source_catalog_path,
        projection.semantic_source_catalog_sha256,
        SemanticSourceCatalog,
    )
    chunks = _load_binding(
        root,
        projection.chunk_manifest_path,
        projection.chunk_manifest_sha256,
        ChunkManifest,
    )
    parser_config = _load_binding(
        root,
        projection.parser.config_path,
        projection.parser.config_sha256,
        PdfParserConfigV1,
    )
    chunker_config = _load_binding(
        root,
        projection.chunker.config_path,
        projection.chunker.config_sha256,
        Utf8ChunkerConfigV1,
    )
    tools = (projection.parser, projection.chunker)
    expected_dependency_names = {
        tool.distribution_name.casefold() for tool in tools
    }
    actual_dependency_names = {
        package.name.casefold() for package in dependencies.packages
    }
    if actual_dependency_names != expected_dependency_names:
        raise GoldenGraphProtocolError(
            "Dependency snapshot must contain exactly the v1 parser and chunker "
            "tool distributions"
        )
    _validate_tool_lock_versions(tools, locked_versions)
    for label, tool in (("parser", projection.parser), ("chunker", projection.chunker)):
        if _sha256_repository_file(root, tool.implementation_path) != tool.implementation_sha256:
            raise GoldenGraphProtocolError(f"{label} implementation SHA-256 mismatch")
        if _sha256_repository_file(root, tool.config_path) != tool.config_sha256:
            raise GoldenGraphProtocolError(f"{label} config SHA-256 mismatch")
        _validate_tool_distribution(tool, dependencies)
    if projection.parser.distribution_name != "pypdf":
        raise GoldenGraphProtocolError(
            "The v1 PDF parser must bind the installed pypdf distribution"
        )
    if projection.chunker.distribution_name != "project-source":
        raise GoldenGraphProtocolError(
            "The v1 UTF-8 chunker must bind tracked project source"
        )
    if parser_config.max_pdf_bytes < registered_asset_bytes:
        raise GoldenGraphProtocolError(
            "Parser byte limit is smaller than the registered PDF"
        )

    if dependencies.python_version != platform.python_version():
        raise GoldenGraphProtocolError(
            "Dependency snapshot Python version differs from the freeze runtime"
        )
    acquisition = protocol.acquisition
    catalog_identity = (catalog.corpus_id, catalog.asset_id, catalog.raw_asset_sha256)
    expected_identity = (
        acquisition.corpus_id,
        acquisition.asset_id,
        acquisition.raw_sha256,
    )
    if catalog_identity != expected_identity:
        raise GoldenGraphProtocolError("Semantic Source catalog asset identity mismatch")
    scope = protocol.page_scope
    if catalog.page_count != scope.asset_page_count:
        raise GoldenGraphProtocolError("Source catalog page_count differs from page scope")
    if catalog.page_count > parser_config.max_pages:
        raise GoldenGraphProtocolError("Source catalog exceeds parser page limit")
    if any(
        page.semantic_utf8_bytes > parser_config.max_page_utf8_bytes
        for page in catalog.pages
    ):
        raise GoldenGraphProtocolError("Source page exceeds parser semantic-byte limit")
    if (
        sum(page.semantic_utf8_bytes for page in catalog.pages)
        > parser_config.max_total_utf8_bytes
    ):
        raise GoldenGraphProtocolError("Source catalog exceeds parser total-byte limit")
    if any(page.semantic_page_sha256 == _ZERO_SHA256 for page in catalog.pages):
        raise GoldenGraphProtocolError("Source catalog contains a placeholder page hash")
    included_pages = set(scope.included_pages or [])
    for page in catalog.pages:
        if page.page_number in included_pages and page.status != "included":
            raise GoldenGraphProtocolError(
                "Included page scope must have included Source catalog status"
            )
        if page.page_number not in included_pages and page.status == "included":
            raise GoldenGraphProtocolError(
                "Excluded page scope cannot have included Source catalog status"
            )

    if (
        chunks.corpus_id,
        chunks.asset_id,
        chunks.raw_asset_sha256,
    ) != expected_identity:
        raise GoldenGraphProtocolError("Chunk manifest asset identity mismatch")
    if chunks.semantic_source_catalog_sha256 != projection.semantic_source_catalog_sha256:
        raise GoldenGraphProtocolError("Chunk manifest Source catalog binding mismatch")
    if chunks.parser != projection.parser or chunks.chunker != projection.chunker:
        raise GoldenGraphProtocolError("Chunk manifest tool identity mismatch")
    if chunks.page_coverage_policy != chunker_config.page_coverage_policy:
        raise GoldenGraphProtocolError("Chunk manifest coverage policy mismatch")
    page_ids = {
        page.logical_page_id: (page.page_number, page.semantic_utf8_bytes)
        for page in catalog.pages
    }
    page_intervals: dict[str, list[tuple[int, int]]] = {
        page.logical_page_id: []
        for page in catalog.pages
        if page.page_number in included_pages
    }
    chunk_order: list[tuple[int, int, int]] = []
    for chunk in chunks.chunks:
        if chunk.semantic_chunk_sha256 == _ZERO_SHA256:
            raise GoldenGraphProtocolError("Chunk manifest contains a placeholder hash")
        if len(chunk.locators) != 1:
            raise GoldenGraphProtocolError(
                "The v1 page-local chunker requires one locator per Chunk"
            )
        chunk_bytes = sum(
            locator.end_offset - locator.start_offset
            for locator in chunk.locators
        )
        if chunk_bytes > chunker_config.max_chunk_utf8_bytes:
            raise GoldenGraphProtocolError("Chunk exceeds configured UTF-8 byte limit")
        for locator in chunk.locators:
            page_binding = page_ids.get(locator.logical_page_id)
            if page_binding is None:
                raise GoldenGraphProtocolError("Chunk locator is absent from Source catalog")
            page_number, page_utf8_bytes = page_binding
            if locator.end_offset > page_utf8_bytes:
                raise GoldenGraphProtocolError("Chunk locator exceeds semantic page length")
            if page_number not in included_pages:
                raise GoldenGraphProtocolError("Chunk locator points outside included page scope")
            page_intervals[locator.logical_page_id].append(
                (locator.start_offset, locator.end_offset)
            )
            chunk_order.append(
                (page_number, locator.start_offset, locator.end_offset)
            )
    if chunk_order != sorted(chunk_order):
        raise GoldenGraphProtocolError(
            "Chunk ordinals must follow page and locator order"
        )
    for logical_page_id, intervals in page_intervals.items():
        _validate_complete_page_coverage(
            logical_page_id,
            page_ids[logical_page_id][1],
            intervals,
            maximum_overlap=chunker_config.overlap_utf8_bytes,
        )


def _validate_ontology(protocol: GoldenGraphProtocol) -> None:
    ontology = protocol.ontology
    if tuple(ontology.relation_types) != _V1_RELATION_TYPES:
        raise GoldenGraphProtocolError(
            "Relation ontology differs from the production Concept graph"
        )
    if tuple(ontology.support_bases) != _V1_SUPPORT_BASES:
        raise GoldenGraphProtocolError(
            "Support bases differ from the production Concept graph"
        )
    if tuple(ontology.support_roles) != _V1_SUPPORT_ROLES:
        raise GoldenGraphProtocolError(
            "Support roles differ from the production Concept graph"
        )
    runtime_contracts = (
        ("relation type", _V1_RELATION_TYPES, get_args(ConceptRelationType)),
        ("support basis", _V1_SUPPORT_BASES, get_args(RelationSupportBasis)),
        (
            "support role",
            _V1_SUPPORT_ROLES,
            get_args(RelationEvidenceSupportRole),
        ),
    )
    for label, registered, runtime in runtime_contracts:
        if not set(registered).issubset(runtime):
            raise GoldenGraphProtocolError(
                f"Production runtime no longer supports a v1 {label}"
            )


def _validate_complete_page_coverage(
    logical_page_id: str,
    semantic_utf8_bytes: int,
    intervals: list[tuple[int, int]],
    *,
    maximum_overlap: int,
) -> None:
    """Require complete union coverage while permitting bounded sliding overlap."""

    ordered = sorted(intervals)
    if not ordered or ordered[0][0] != 0:
        raise GoldenGraphProtocolError(
            f"Chunk coverage must start at byte zero: {logical_page_id}"
        )
    covered_end = 0
    for start_offset, end_offset in ordered:
        if start_offset > covered_end:
            raise GoldenGraphProtocolError(
                f"Chunk coverage contains a gap: {logical_page_id}"
            )
        if covered_end - start_offset > maximum_overlap:
            raise GoldenGraphProtocolError(
                f"Chunk overlap exceeds its frozen configuration: {logical_page_id}"
            )
        covered_end = max(covered_end, end_offset)
    if covered_end != semantic_utf8_bytes:
        raise GoldenGraphProtocolError(
            f"Chunk coverage omits the page tail: {logical_page_id}"
        )


def _validate_review(
    protocol: GoldenGraphProtocol,
    *,
    repository_root: Path,
) -> None:
    review = protocol.review
    if review.annotation_guide_sha256 == _ZERO_SHA256:
        raise GoldenGraphProtocolError(
            "Frozen protocol requires a non-placeholder annotation guide hash"
        )
    if _sha256_repository_file(
        repository_root,
        review.annotation_guide_path,
    ) != review.annotation_guide_sha256:
        raise GoldenGraphProtocolError("Annotation guide SHA-256 mismatch")


def _validate_evaluation(protocol: GoldenGraphProtocol) -> None:
    evaluation = protocol.evaluation
    if tuple(evaluation.reported_metrics) != get_args(MetricId):
        raise GoldenGraphProtocolError(
            "Frozen protocol must report the complete ordered v1 metric set"
        )
    targets = {target.metric_id: target for target in evaluation.targets}
    if set(targets) != set(EXPECTED_V1_TARGETS):
        raise GoldenGraphProtocolError("Frozen protocol v1 target set mismatch")
    for metric_id, expected in EXPECTED_V1_TARGETS.items():
        target = targets[metric_id]
        actual = (target.comparison, target.threshold, target.unit)
        if actual != expected:
            raise GoldenGraphProtocolError(
                f"Frozen protocol target differs for {metric_id}"
            )
        expected_scope = EXPECTED_METRIC_PROTOCOLS[metric_id]
        if (target.evidence_scope, target.required_protocol) != expected_scope:
            raise GoldenGraphProtocolError(
                f"Frozen protocol evidence scope differs for {metric_id}"
            )
        _validate_confidence_bound(target)
    report_only = {item.metric_id: item for item in evaluation.report_only_metrics}
    if set(report_only) != {"relation_proposal_recall"}:
        raise GoldenGraphProtocolError("Frozen report-only metric set mismatch")


def _validate_confidence_bound(target: MetricTarget) -> None:
    statistical = target.metric_id in _STATISTICAL_TARGETS
    if statistical != target.confidence_interval_required:
        raise GoldenGraphProtocolError(
            f"Confidence-interval policy differs for {target.metric_id}"
        )
    if not statistical:
        if target.confidence_bound is not None or target.confidence_bound_side != "none":
            raise GoldenGraphProtocolError(
                f"Non-statistical target has a confidence bound: {target.metric_id}"
            )
        return
    expected_side = "lower" if target.comparison == "gte" else "upper"
    if target.confidence_bound_side != expected_side or target.confidence_bound is None:
        raise GoldenGraphProtocolError(
            f"Frozen statistical target lacks its {expected_side} confidence bound: "
            f"{target.metric_id}"
        )
    if target.unit == "proportion" and not 0 <= target.confidence_bound <= 1:
        raise GoldenGraphProtocolError(
            f"Confidence bound is outside [0, 1]: {target.metric_id}"
        )


def _reject_public_content_fields(value: object, *, path: str = "protocol") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key.casefold() in FORBIDDEN_PUBLIC_CONTENT_KEYS:
                raise GoldenGraphProtocolError(
                    f"Forbidden public text/quote field at {path}.{key}"
                )
            _reject_public_content_fields(nested, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_public_content_fields(nested, path=f"{path}[{index}]")


def _sha256_repository_file(repository_root: Path, logical_path: str) -> str:
    root = repository_root.resolve()
    path = _resolve_bound_repository_file(
        root,
        logical_path,
        require_tracked=True,
    )
    try:
        payload = read_bounded_regular_bytes(
            path,
            max_bytes=_MAX_BOUND_ARTIFACT_BYTES,
            label="bound repository artifact",
        )
    except CanonicalArtifactError as exc:
        raise GoldenGraphProtocolError(
            f"Cannot read bound repository artifact: {logical_path}"
        ) from exc
    return hashlib.sha256(payload).hexdigest()


def _load_binding(root: Path, logical_path: str, expected_sha256: str | None, model):
    if expected_sha256 is None or expected_sha256 == _ZERO_SHA256:
        raise GoldenGraphProtocolError(
            f"Frozen protocol requires a non-placeholder hash for {logical_path}"
        )
    path = _resolve_bound_repository_file(
        root,
        logical_path,
        require_tracked=True,
    )
    sidecar_path = _resolve_bound_repository_file(
        root,
        PurePosixPath(logical_path).with_suffix(".sha256").as_posix(),
        require_tracked=True,
    )
    try:
        if sidecar_path != path.with_suffix(".sha256"):
            raise GoldenGraphProtocolError(
                f"Bound sidecar path mismatch: {logical_path}"
            )
        payload, actual_sha256 = load_hashed_canonical_json(path)
        if actual_sha256 != expected_sha256:
            raise GoldenGraphProtocolError(
                f"Bound artifact SHA-256 mismatch: {logical_path}"
            )
        return model.model_validate(payload)
    except (CanonicalArtifactError, ValidationError, OSError, ValueError) as exc:
        if isinstance(exc, GoldenGraphProtocolError):
            raise
        raise GoldenGraphProtocolError(
            f"Invalid bound protocol artifact {logical_path}: {exc}"
        ) from exc


def _load_uv_lock_versions(
    root: Path,
    logical_path: str,
    expected_sha256: str,
) -> Mapping[str, frozenset[str]]:
    """Parse the pinned uv lock instead of treating its hash as semantics."""

    path = _resolve_bound_repository_file(
        root,
        logical_path,
        require_tracked=True,
    )
    try:
        payload = read_bounded_regular_bytes(
            path,
            max_bytes=_MAX_BOUND_ARTIFACT_BYTES,
            label="uv lock",
        )
        if hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise GoldenGraphProtocolError("uv.lock SHA-256 mismatch")
        decoded = tomllib.loads(payload.decode("utf-8"))
        packages = decoded.get("package")
        lock_version = decoded.get("version")
        if type(lock_version) is not int or lock_version != 1 or not isinstance(
            packages, list
        ):
            raise GoldenGraphProtocolError("uv.lock has an unsupported structure")
        versions: dict[str, set[str]] = {}
        for package in packages:
            if not isinstance(package, dict):
                raise GoldenGraphProtocolError("uv.lock contains an invalid package")
            name = package.get("name")
            version = package.get("version")
            if not isinstance(name, str) or not isinstance(version, str):
                raise GoldenGraphProtocolError(
                    "uv.lock package identity must contain name and version"
                )
            versions.setdefault(name.casefold(), set()).add(version)
    except (CanonicalArtifactError, OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        if isinstance(exc, GoldenGraphProtocolError):
            raise
        raise GoldenGraphProtocolError(f"Cannot parse uv.lock: {exc}") from exc
    return {name: frozenset(values) for name, values in versions.items()}


def _validate_tool_lock_versions(
    tools: tuple[ToolIdentity, ...],
    locked_versions: Mapping[str, frozenset[str]],
) -> None:
    for tool in tools:
        if tool.distribution_name == "project-source":
            continue
        versions = locked_versions.get(tool.distribution_name.casefold(), frozenset())
        if tool.version not in versions:
            raise GoldenGraphProtocolError(
                "Tool version is absent from uv.lock: "
                f"{tool.distribution_name}=={tool.version}"
            )


def _resolve_bound_repository_file(
    repository_root: Path,
    logical_path: str,
    *,
    require_tracked: bool,
) -> Path:
    """Resolve one non-aliased, single-link repository authority leaf."""

    root = repository_root.resolve()
    relative = PurePosixPath(logical_path)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or "\\" in logical_path
    ):
        raise GoldenGraphProtocolError(
            f"Invalid bound repository artifact path: {logical_path}"
        )
    current = root
    try:
        for index, part in enumerate(relative.parts):
            _require_exact_child_spelling(
                current,
                part,
                logical_path=logical_path,
            )
            current = current / part
            metadata = current.lstat()
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            if stat.S_ISLNK(metadata.st_mode) or (
                reparse_flag
                and getattr(metadata, "st_file_attributes", 0) & reparse_flag
            ):
                raise GoldenGraphProtocolError(
                    f"Bound repository artifact cannot use links: {logical_path}"
                )
            if index < len(relative.parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
                raise GoldenGraphProtocolError(
                    f"Bound repository parent is not a directory: {logical_path}"
                )
        if not stat.S_ISREG(metadata.st_mode):
            raise GoldenGraphProtocolError(
                f"Bound repository artifact must be a regular file: {logical_path}"
            )
        if metadata.st_nlink != 1:
            raise GoldenGraphProtocolError(
                f"Bound repository artifact cannot be hard-linked: {logical_path}"
            )
        current.resolve(strict=True).relative_to(root)
    except GoldenGraphProtocolError:
        raise
    except (OSError, ValueError) as exc:
        raise GoldenGraphProtocolError(
            f"Cannot resolve bound repository artifact: {logical_path}"
        ) from exc

    if require_tracked and (root / ".git").exists():
        try:
            git_environment = {
                key: value
                for key, value in os.environ.items()
                if not key.upper().startswith("GIT_")
            }
            git_environment["GIT_LITERAL_PATHSPECS"] = "1"
            tracked = subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "ls-files",
                    "--error-unmatch",
                    "--",
                    relative.as_posix(),
                ],
                check=False,
                env=git_environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GoldenGraphProtocolError(
                "Cannot verify tracked repository artifact: "
                f"{logical_path}"
            ) from exc
        if tracked.returncode != 0:
            raise GoldenGraphProtocolError(
                f"Bound repository artifact must be tracked: {logical_path}"
            )
    return current


def _require_exact_child_spelling(
    parent: Path,
    child_name: str,
    *,
    logical_path: str,
) -> None:
    """Reject case-fold aliases and duplicate portable names on any platform."""

    try:
        matches = [
            entry.name
            for entry in os.scandir(parent)
            if entry.name.casefold() == child_name.casefold()
        ]
    except OSError as exc:
        raise GoldenGraphProtocolError(
            f"Cannot inspect repository artifact spelling: {logical_path}"
        ) from exc
    if matches != [child_name]:
        raise GoldenGraphProtocolError(
            f"Repository artifact path must use exact portable spelling: {logical_path}"
        )


def _validate_tool_distribution(tool, dependencies: DependencySnapshot) -> None:
    packages = {item.name.casefold(): item.version for item in dependencies.packages}
    package_version = packages.get(tool.distribution_name.casefold())
    if package_version != tool.version:
        raise GoldenGraphProtocolError(
            f"Tool version differs from dependency snapshot: {tool.implementation}"
        )
    if tool.distribution_name == "project-source":
        return
    try:
        installed_version = importlib.metadata.version(tool.distribution_name)
    except importlib.metadata.PackageNotFoundError as exc:
        raise GoldenGraphProtocolError(
            f"Tool distribution is not installed: {tool.distribution_name}"
        ) from exc
    if installed_version != tool.version:
        raise GoldenGraphProtocolError(
            f"Installed tool version differs: {tool.distribution_name}"
        )


def _object_without_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise GoldenGraphProtocolError(
                f"Duplicate acquisition manifest key: {key}"
            )
        result[key] = value
    return result


def _write_exclusive_durable(path: Path, payload: bytes) -> None:
    """Stage complete bytes, then atomically publish without replacement.

    Writing the canonical path directly would let a process kill leave a
    partial artifact that every later retry must treat as an immutable
    conflict.  A same-directory hard link makes publication both atomic and
    exclusive: an interrupted stage can leave only a disposable temporary
    name, while an existing canonical name is never replaced.
    """

    descriptor = -1
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".publish-tmp",
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary_path, path)
    except FileExistsError as exc:
        raise GoldenGraphProtocolError(
            f"Frozen protocol publication never overwrites: {path.name}"
        ) from exc
    except OSError as exc:
        raise GoldenGraphProtocolError(
            f"Cannot publish frozen protocol artifact: {path}"
        ) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _published_leaf_metadata(path: Path) -> os.stat_result:
    """Inspect one published leaf without following link-like objects."""

    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise GoldenGraphProtocolError(
                f"Published protocol leaf must be a regular file: {path.name}"
            )
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if stat.S_ISLNK(metadata.st_mode) or (
            reparse_flag
            and getattr(metadata, "st_file_attributes", 0) & reparse_flag
        ):
            raise GoldenGraphProtocolError(
                f"Published protocol leaf cannot use links: {path.name}"
            )
        return metadata
    except GoldenGraphProtocolError:
        raise
    except OSError as exc:
        raise GoldenGraphProtocolError(
            f"Cannot inspect published protocol leaf: {path.name}"
        ) from exc


def _wait_for_single_link(path: Path) -> bool:
    """Boundedly wait for the publisher to unlink its temporary alias."""

    for attempt in range(50):
        if _published_leaf_metadata(path).st_nlink == 1:
            return True
        if attempt < 49:
            time.sleep(0.002)
    return False


def _require_stable_single_link(path: Path) -> None:
    """Read-only public gate for a stable, non-aliased published leaf."""

    if not _wait_for_single_link(path):
        raise GoldenGraphProtocolError(
            f"Published protocol leaf cannot be hard-linked: {path.name}"
        )


def _remove_owned_publication_aliases(path: Path) -> None:
    """Recovery-only cleanup for this publisher's same-inode temp names."""

    metadata = _published_leaf_metadata(path)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    try:
        temporary_prefix = f".{path.name}."
        for candidate in path.parent.iterdir():
            if not (
                candidate.name.startswith(temporary_prefix)
                and candidate.name.endswith(".publish-tmp")
            ):
                continue
            try:
                candidate_metadata = candidate.lstat()
            except FileNotFoundError:
                continue
            if (
                stat.S_ISREG(candidate_metadata.st_mode)
                and not stat.S_ISLNK(candidate_metadata.st_mode)
                and not (
                    reparse_flag
                    and getattr(candidate_metadata, "st_file_attributes", 0)
                    & reparse_flag
                )
                and (candidate_metadata.st_dev, candidate_metadata.st_ino)
                == (metadata.st_dev, metadata.st_ino)
            ):
                candidate.unlink(missing_ok=True)
    except OSError as exc:
        raise GoldenGraphProtocolError(
            f"Cannot clean publication staging alias: {path.name}"
        ) from exc


def _prepare_recovery_leaf(path: Path) -> None:
    """Converge an active/stale publication, but reject unknown aliases."""

    if _wait_for_single_link(path):
        return
    _remove_owned_publication_aliases(path)
    _require_stable_single_link(path)


def _expected_frozen_protocol_path(repository_root: Path, protocol_id: str) -> Path:
    root = repository_root.resolve()
    directory = _resolve_repository_directory(
        root,
        _FROZEN_PROTOCOL_DIRECTORY.as_posix(),
    )
    path = (directory / f"{protocol_id}.frozen.json").resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise GoldenGraphProtocolError(
            "Frozen protocol directory resolves outside the repository"
        ) from exc
    return path


def _require_frozen_protocol_directory(
    path: Path,
    *,
    repository_root: Path,
) -> Path:
    """Require the exact canonical parent spelling before reading a leaf."""

    root = repository_root.resolve()
    if not path.is_absolute():
        raise GoldenGraphProtocolError(
            "Frozen protocol artifact path must be absolute"
        )
    try:
        supplied_relative = path.parent.relative_to(root).as_posix()
    except ValueError as exc:
        raise GoldenGraphProtocolError(
            "Frozen protocol artifact must stay in the canonical protocol directory"
        ) from exc
    if supplied_relative != _FROZEN_PROTOCOL_DIRECTORY.as_posix():
        raise GoldenGraphProtocolError(
            "Frozen protocol artifact must stay in the canonical protocol directory"
        )
    return _resolve_repository_directory(
        root,
        _FROZEN_PROTOCOL_DIRECTORY.as_posix(),
    )


def _is_exact_frozen_protocol_location(
    path: Path,
    *,
    repository_root: Path,
    protocol_id: str,
) -> bool:
    """Compare path spellings explicitly; WindowsPath equality folds case."""

    try:
        expected_directory = _require_frozen_protocol_directory(
            path,
            repository_root=repository_root,
        )
    except GoldenGraphProtocolError:
        return False
    expected_name = f"{protocol_id}.frozen.json"
    return path.name == expected_name and path.parent == expected_directory


def _resolve_repository_directory(repository_root: Path, logical_path: str) -> Path:
    """Reject symlink/junction aliases in an authority directory path."""

    root = repository_root.resolve()
    relative = PurePosixPath(logical_path)
    current = root
    try:
        for part in relative.parts:
            _require_exact_child_spelling(
                current,
                part,
                logical_path=logical_path,
            )
            current = current / part
            metadata = current.lstat()
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            if stat.S_ISLNK(metadata.st_mode) or (
                reparse_flag
                and getattr(metadata, "st_file_attributes", 0) & reparse_flag
            ):
                raise GoldenGraphProtocolError(
                    f"Repository authority directory cannot use links: {logical_path}"
                )
            if not stat.S_ISDIR(metadata.st_mode):
                raise GoldenGraphProtocolError(
                    f"Repository authority path is not a directory: {logical_path}"
                )
        current.resolve(strict=True).relative_to(root)
    except GoldenGraphProtocolError:
        raise
    except (OSError, ValueError) as exc:
        raise GoldenGraphProtocolError(
            f"Cannot resolve repository authority directory: {logical_path}"
        ) from exc
    return current


def _recover_frozen_protocol_publication(
    *,
    output_path: Path,
    payload: bytes,
    sidecar: bytes,
    authority: ManifestAuthority,
    repository_root: Path,
) -> FrozenProtocolAuthority:
    """Repair only exact crash remnants; never replace conflicting bytes."""

    sidecar_path = output_path.with_suffix(".sha256")
    try:
        if not output_path.exists() and not sidecar_path.exists():
            raise GoldenGraphProtocolError(
                "Frozen protocol recovery found no publication artifact"
            )
        if not output_path.exists():
            _publish_or_converge_recovery_leaf(
                sidecar_path,
                sidecar,
                max_bytes=MAX_SIDECAR_BYTES,
                label="frozen protocol sidecar crash remnant",
                conflict_message=(
                    "Frozen protocol identity conflicts with existing sidecar"
                ),
            )
        _publish_or_converge_recovery_leaf(
            output_path,
            payload,
            max_bytes=MAX_PROTOCOL_BYTES,
            label="frozen protocol crash remnant",
            conflict_message=(
                "Frozen protocol identity conflicts with existing immutable JSON"
            ),
        )
        _publish_or_converge_recovery_leaf(
            sidecar_path,
            sidecar,
            max_bytes=MAX_SIDECAR_BYTES,
            label="frozen protocol sidecar crash remnant",
            conflict_message="Frozen protocol identity conflicts with existing sidecar",
        )
    except (CanonicalArtifactError, OSError) as exc:
        raise GoldenGraphProtocolError(
            "Cannot inspect or recover frozen protocol publication"
        ) from exc

    return load_frozen_protocol(
        output_path,
        authority,
        repository_root=repository_root,
    )


def _publish_or_converge_recovery_leaf(
    path: Path,
    expected_payload: bytes,
    *,
    max_bytes: int,
    label: str,
    conflict_message: str,
) -> None:
    """Publish a missing crash leaf or converge with an identical winner."""

    if not path.exists():
        try:
            _write_exclusive_durable(path, expected_payload)
        except GoldenGraphProtocolError:
            if not path.exists():
                raise
    _prepare_recovery_leaf(path)
    if read_bounded_regular_bytes(
        path,
        max_bytes=max_bytes,
        label=label,
    ) != expected_payload:
        raise GoldenGraphProtocolError(conflict_message)
