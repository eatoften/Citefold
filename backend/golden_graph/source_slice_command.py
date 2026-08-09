"""Protocol adapter and CLI for one redacted golden-graph Source slice.

The draft protocol is the build specification.  This module loads its bound
configuration and calls ``build_source_slice``.  Public artifact publication
and private materialization are separate, explicit opt-ins.  Neither mode
writes product state, and the CLI never serializes private Source content.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys
from typing import Literal, Sequence, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .bindings import (
    DependencySnapshot,
    PdfParserConfigV1,
    SourceSliceBuildSummary,
    Utf8ChunkerConfigV1,
)
from .canonical_io import (
    CanonicalArtifactError,
    canonical_json_bytes,
    load_hashed_canonical_json,
    read_bounded_regular_bytes,
)
from .protocol import (
    GoldenGraphProtocolError,
    ManifestAuthority,
    V1_SOURCE_SLICE_ORCHESTRATION_PATHS,
    load_manifest_authority,
    load_protocol,
    source_slice_build_spec_sha256,
)
from .schemas import GoldenGraphProtocol, SAFE_ID_PATTERN, SHA256_PATTERN
from .source_slice_builder import (
    SourceSliceBuildAuthority,
    SourceSliceBuildError,
    build_source_slice,
    public_artifact_bytes,
    write_private_source_slice_materialization,
    write_public_artifact,
)


_ZERO_SHA256 = "0" * 64
_PUBLIC_PREFIX = ("backend", "golden_graph", "artifacts")
_MAX_COMMAND_SOURCE_BYTES = 4 * 1024 * 1024
_BindingT = TypeVar("_BindingT", bound=BaseModel)
_COMMAND_IMPORT_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
try:
    _COMMAND_IMPORT_SOURCE_CLOSURE: tuple[tuple[str, str], ...] | None = tuple(
        (
            logical_path,
            hashlib.sha256(
                read_bounded_regular_bytes(
                    _COMMAND_IMPORT_REPOSITORY_ROOT / logical_path,
                    max_bytes=_MAX_COMMAND_SOURCE_BYTES,
                    label=f"imported orchestration source {logical_path}",
                )
            ).hexdigest(),
        )
        for logical_path in V1_SOURCE_SLICE_ORCHESTRATION_PATHS
    )
except (CanonicalArtifactError, OSError):
    _COMMAND_IMPORT_SOURCE_CLOSURE = None


class SourceSliceCommandError(RuntimeError):
    """Raised before a protocol-driven build is reported as successful."""


class _StrictPublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class PublishedArtifactIdentity(_StrictPublicModel):
    path: str = Field(min_length=1, max_length=500)
    sha256: str = Field(pattern=SHA256_PATTERN)


class SourceSliceCommandReceipt(_StrictPublicModel):
    """The only CLI DTO; every field is safe to print or commit."""

    schema_version: Literal[1]
    artifact_role: Literal["golden_graph_source_slice_command_receipt"]
    protocol_id: str = Field(pattern=SAFE_ID_PATTERN)
    protocol_sha256: str = Field(pattern=SHA256_PATTERN)
    public_artifacts_written: bool
    private_materialization_written: bool
    source_catalog: PublishedArtifactIdentity
    chunk_manifest: PublishedArtifactIdentity
    build_summary: PublishedArtifactIdentity
    summary: SourceSliceBuildSummary


def bootstrap_protocol_source_slice(
    *,
    repository_root: Path,
    protocol_path: Path,
    write_public_artifacts: bool = False,
    write_private_materialization: bool = False,
) -> SourceSliceCommandReceipt:
    """Build a complete draft protocol with independent persistence opt-ins.

    Catalog, Chunk, and summary hashes may all be null for the first bootstrap.
    The receipt then supplies their exact values for the next protocol
    revision.  If the three hashes are already bound, a rebuild must match all
    of them exactly.

    Both write flags default to false.  Public writes contain only redacted
    leaves; private writes stay in the fixed gitignored materialization root.
    """

    try:
        root = Path(repository_root).resolve(strict=True)
        if not root.is_dir():
            raise SourceSliceCommandError("repository_root must be a directory")
        command_source_closure = _verify_command_module_currentness(root)
        draft_path = _repository_file(root, protocol_path, "draft protocol")
        protocol = load_protocol(draft_path)
        _require_build_ready(protocol)
        build_spec_hash = source_slice_build_spec_sha256(protocol)

        projection = protocol.projection
        parser = projection.parser
        chunker = projection.chunker
        included_pages = protocol.page_scope.included_pages
        dependency_hash = projection.dependency_snapshot_sha256
        if (
            parser is None
            or chunker is None
            or included_pages is None
            or dependency_hash is None
        ):
            raise SourceSliceCommandError("Draft protocol is not build-ready")

        manifest_authority = load_manifest_authority(
            root / protocol.acquisition.manifest_path,
            repository_root=root,
        )
        _require_matching_acquisition(protocol, manifest_authority)
        parser_config = _load_binding(
            root,
            parser.config_path,
            parser.config_sha256,
            PdfParserConfigV1,
            "parser config",
        )
        chunker_config = _load_binding(
            root,
            chunker.config_path,
            chunker.config_sha256,
            Utf8ChunkerConfigV1,
            "chunker config",
        )
        dependency_snapshot = _load_binding(
            root,
            projection.dependency_snapshot_path,
            dependency_hash,
            DependencySnapshot,
            "dependency snapshot",
        )

        catalog_path, catalog_logical = _public_output(
            root, Path(projection.source_catalog_path), "Source catalog"
        )
        chunks_path, chunks_logical = _public_output(
            root, Path(projection.chunk_manifest_path), "Chunk manifest"
        )
        summary_path, summary_logical = _public_output(
            root,
            Path(projection.source_slice_build_summary_path),
            "build summary",
        )
        if len({catalog_path, chunks_path, summary_path}) != 3:
            raise SourceSliceCommandError("Public output paths must be distinct")

        built = build_source_slice(
            manifest_authority=manifest_authority,
            repository_root=root,
            asset_id=protocol.acquisition.asset_id,
            included_pages=included_pages,
            parser_config=parser_config,
            chunker_config=chunker_config,
            parser_identity=parser,
            chunker_identity=chunker,
            dependency_snapshot=dependency_snapshot,
            dependency_snapshot_path=projection.dependency_snapshot_path,
            dependency_snapshot_sha256=dependency_hash,
            uv_lock_path=projection.uv_lock_path,
            uv_lock_sha256=projection.uv_lock_sha256,
            build_spec_protocol_sha256=build_spec_hash,
        )
        _require_matching_result(protocol, manifest_authority, built)
        _require_command_source_unchanged(root, command_source_closure)

        catalog_hash = built.summary.semantic_source_catalog_sha256
        chunks_hash = built.summary.chunk_manifest_sha256
        summary_hash = _public_hash(built.summary)
        public_artifacts_written = False
        private_materialization_written = False
        if write_public_artifacts:
            _require_command_source_unchanged(root, command_source_closure)
            written = (
                write_public_artifact(
                    repository_root=root,
                    output_path=catalog_path,
                    artifact=built.source_catalog,
                ),
                write_public_artifact(
                    repository_root=root,
                    output_path=chunks_path,
                    artifact=built.chunk_manifest,
                ),
                write_public_artifact(
                    repository_root=root,
                    output_path=summary_path,
                    artifact=built.summary,
                ),
            )
            if written != (catalog_hash, chunks_hash, summary_hash):
                raise SourceSliceCommandError("Published artifact digest drift")
            _require_command_source_unchanged(root, command_source_closure)
            public_artifacts_written = True
        if write_private_materialization:
            _require_command_source_unchanged(root, command_source_closure)
            private_path = (
                root
                / "backend/data/golden_graph/source_slice_materializations"
                / f"{protocol.protocol_id}.private.json"
            )
            write_private_source_slice_materialization(
                repository_root=root,
                output_path=private_path,
                authority=built,
                protocol=protocol,
            )
            _require_command_source_unchanged(root, command_source_closure)
            private_materialization_written = True

        return SourceSliceCommandReceipt(
            schema_version=1,
            artifact_role="golden_graph_source_slice_command_receipt",
            protocol_id=protocol.protocol_id,
            protocol_sha256=hashlib.sha256(
                canonical_json_bytes(protocol)
            ).hexdigest(),
            public_artifacts_written=public_artifacts_written,
            private_materialization_written=private_materialization_written,
            source_catalog=PublishedArtifactIdentity(
                path=catalog_logical, sha256=catalog_hash
            ),
            chunk_manifest=PublishedArtifactIdentity(
                path=chunks_logical, sha256=chunks_hash
            ),
            build_summary=PublishedArtifactIdentity(
                path=summary_logical, sha256=summary_hash
            ),
            summary=built.summary,
        )
    except SourceSliceCommandError:
        raise
    except (
        CanonicalArtifactError,
        GoldenGraphProtocolError,
        OSError,
        SourceSliceBuildError,
        ValidationError,
        ValueError,
    ) as exc:
        raise SourceSliceCommandError(
            f"Protocol-driven Source-slice build failed safely: {exc}"
        ) from exc


def _require_build_ready(protocol: GoldenGraphProtocol) -> None:
    if protocol.protocol_status != "draft":
        raise SourceSliceCommandError("Only a draft protocol can bootstrap a slice")
    scope = protocol.page_scope
    if (
        scope.asset_page_count is None
        or scope.included_pages is None
        or scope.excluded_pages is None
        or scope.inclusion_reason is None
        or scope.exclusion_reason is None
    ):
        raise SourceSliceCommandError("Draft protocol requires a complete page_scope")
    included = set(scope.included_pages)
    excluded = set(scope.excluded_pages)
    if (
        not included
        or included & excluded
        or included | excluded != set(range(1, scope.asset_page_count + 1))
    ):
        raise SourceSliceCommandError(
            "page_scope must classify every page exactly once"
        )

    projection = protocol.projection
    if projection.parser is None or projection.chunker is None:
        raise SourceSliceCommandError("Draft protocol requires parser and chunker")
    required_hashes = (
        projection.parser.implementation_sha256,
        projection.parser.config_sha256,
        projection.chunker.implementation_sha256,
        projection.chunker.config_sha256,
        projection.dependency_snapshot_sha256,
        projection.uv_lock_sha256,
    )
    if any(value is None or value == _ZERO_SHA256 for value in required_hashes):
        raise SourceSliceCommandError("Draft protocol has an unbound derivation leaf")
    projection_hashes = (
        projection.semantic_source_catalog_sha256,
        projection.chunk_manifest_sha256,
        projection.source_slice_build_summary_sha256,
    )
    populated = sum(value is not None for value in projection_hashes)
    if populated not in (0, len(projection_hashes)):
        raise SourceSliceCommandError(
            "Catalog, Chunk, and summary hashes must be all-null or all-bound"
        )
    if any(value == _ZERO_SHA256 for value in projection_hashes):
        raise SourceSliceCommandError("Projection hashes cannot be placeholders")


def _require_matching_acquisition(
    protocol: GoldenGraphProtocol,
    authority: ManifestAuthority,
) -> None:
    acquisition = protocol.acquisition
    manifest = authority.manifest
    if (
        authority.manifest_path,
        authority.manifest_sha256,
        manifest.corpus_id,
        manifest.commit_sha,
    ) != (
        acquisition.manifest_path,
        acquisition.manifest_sha256,
        acquisition.corpus_id,
        acquisition.repository_commit_sha,
    ):
        raise SourceSliceCommandError("Protocol differs from ManifestAuthority")
    assets = [
        asset
        for asset in manifest.assets
        if asset.asset_id == acquisition.asset_id
    ]
    if len(assets) != 1:
        raise SourceSliceCommandError("Protocol must bind one registered asset")
    asset = assets[0]
    if (
        asset.partition,
        asset.sha256,
        asset.license_spdx,
        asset.redistribution_allowed,
        asset.media_type,
    ) != (
        acquisition.partition,
        acquisition.raw_sha256,
        acquisition.license_spdx,
        acquisition.redistribution_allowed,
        "application/pdf",
    ) or asset.partition != "authoring":
        raise SourceSliceCommandError("Protocol asset binding is not an authoring PDF")


def _require_matching_result(
    protocol: GoldenGraphProtocol,
    authority: ManifestAuthority,
    built: SourceSliceBuildAuthority,
) -> None:
    projection = protocol.projection
    parser = projection.parser
    chunker = projection.chunker
    dependency_hash = projection.dependency_snapshot_sha256
    if parser is None or chunker is None or dependency_hash is None:
        raise SourceSliceCommandError("Protocol lost its derivation identity")
    summary = built.summary
    acquisition = protocol.acquisition
    if (
        summary.corpus_id,
        summary.asset_id,
        summary.manifest_sha256,
        summary.raw_asset_sha256,
        summary.build_spec_protocol_sha256,
        summary.parser_config_sha256,
        summary.chunker_config_sha256,
        summary.parser_implementation_sha256,
        summary.chunker_implementation_sha256,
        summary.dependency_snapshot_sha256,
        summary.uv_lock_sha256,
    ) != (
        acquisition.corpus_id,
        acquisition.asset_id,
        authority.manifest_sha256,
        acquisition.raw_sha256,
        source_slice_build_spec_sha256(protocol),
        parser.config_sha256,
        chunker.config_sha256,
        parser.implementation_sha256,
        chunker.implementation_sha256,
        dependency_hash,
        projection.uv_lock_sha256,
    ):
        raise SourceSliceCommandError("Build summary differs from protocol identity")

    scope = protocol.page_scope
    if scope.asset_page_count != summary.page_count:
        raise SourceSliceCommandError("Build page_count differs from page_scope")
    included = set(scope.included_pages or ())
    excluded = set(scope.excluded_pages or ())
    statuses = {page.page_number: page.status for page in built.source_catalog.pages}
    if (
        set(statuses) != included | excluded
        or any(statuses[page] != "included" for page in included)
        or any(statuses[page] == "included" for page in excluded)
    ):
        raise SourceSliceCommandError("Build catalog differs from page_scope")

    catalog_hash = _public_hash(built.source_catalog)
    chunks_hash = _public_hash(built.chunk_manifest)
    summary_hash = _public_hash(summary)
    if (
        summary.semantic_source_catalog_sha256 != catalog_hash
        or summary.chunk_manifest_sha256 != chunks_hash
        or built.chunk_manifest.semantic_source_catalog_sha256 != catalog_hash
        or built.chunk_manifest.parser != parser
        or built.chunk_manifest.chunker != chunker
    ):
        raise SourceSliceCommandError("Build artifacts have inconsistent identities")
    if (
        built.source_catalog.corpus_id,
        built.source_catalog.asset_id,
        built.source_catalog.raw_asset_sha256,
        built.chunk_manifest.corpus_id,
        built.chunk_manifest.asset_id,
        built.chunk_manifest.raw_asset_sha256,
    ) != (
        acquisition.corpus_id,
        acquisition.asset_id,
        acquisition.raw_sha256,
        acquisition.corpus_id,
        acquisition.asset_id,
        acquisition.raw_sha256,
    ):
        raise SourceSliceCommandError("Build artifacts differ from acquisition")
    if (
        projection.semantic_source_catalog_sha256 not in (None, catalog_hash)
        or projection.chunk_manifest_sha256 not in (None, chunks_hash)
        or projection.source_slice_build_summary_sha256
        not in (None, summary_hash)
    ):
        raise SourceSliceCommandError("Rebuild differs from protocol projection hashes")


def _verify_command_module_currentness(
    repository_root: Path,
) -> tuple[tuple[str, str], ...]:
    """Verify the complete imported v1 orchestration source closure."""

    root = Path(repository_root).resolve(strict=True)
    if (
        _COMMAND_IMPORT_SOURCE_CLOSURE is None
        or root != _COMMAND_IMPORT_REPOSITORY_ROOT
    ):
        raise SourceSliceCommandError(
            "Imported Source-slice orchestration does not belong to the repository"
        )
    expected_hashes = dict(_COMMAND_IMPORT_SOURCE_CLOSURE)
    current: list[tuple[str, str]] = []
    for logical_path in V1_SOURCE_SLICE_ORCHESTRATION_PATHS:
        candidate = root / logical_path
        try:
            candidate.resolve(strict=True).relative_to(root)
            current_hash = hashlib.sha256(
                read_bounded_regular_bytes(
                    candidate,
                    max_bytes=_MAX_COMMAND_SOURCE_BYTES,
                    label=f"current orchestration source {logical_path}",
                )
            ).hexdigest()
        except (CanonicalArtifactError, OSError, ValueError) as exc:
            raise SourceSliceCommandError(
                f"Cannot verify orchestration source: {logical_path}"
            ) from exc
        if current_hash != expected_hashes.get(logical_path):
            raise SourceSliceCommandError(
                f"Imported orchestration source is stale: {logical_path}"
            )
        current.append((logical_path, current_hash))
    return tuple(current)


def _require_command_source_unchanged(
    root: Path,
    expected_closure: tuple[tuple[str, str], ...],
) -> None:
    if _verify_command_module_currentness(root) != expected_closure:
        raise SourceSliceCommandError(
            "Source-slice orchestration closure changed during execution"
        )


def _load_binding(
    root: Path,
    logical_path: str,
    expected_hash: str,
    model_type: type[_BindingT],
    label: str,
) -> _BindingT:
    path = _repository_file(root, Path(logical_path), label)
    value, digest = load_hashed_canonical_json(path)
    if digest != expected_hash:
        raise SourceSliceCommandError(f"{label} hash differs from protocol")
    try:
        return model_type.model_validate(value)
    except ValidationError as exc:
        raise SourceSliceCommandError(f"Invalid {label}: {exc}") from exc


def _repository_file(root: Path, supplied: Path, label: str) -> Path:
    candidate = supplied if supplied.is_absolute() else root / supplied
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise SourceSliceCommandError(f"{label} must be inside the repository") from exc
    if not resolved.is_file():
        raise SourceSliceCommandError(f"{label} must be a file")
    return resolved


def _public_output(root: Path, supplied: Path, label: str) -> tuple[Path, str]:
    candidate = supplied if supplied.is_absolute() else root / supplied
    if candidate.suffix != ".json" or candidate.name.casefold().endswith(
        ".frozen.json"
    ):
        raise SourceSliceCommandError(f"{label} must be a non-frozen JSON file")
    try:
        parent = candidate.parent.resolve(strict=True)
        relative_parent = parent.relative_to(root)
    except (OSError, ValueError) as exc:
        raise SourceSliceCommandError(
            f"{label} parent must be in the repository"
        ) from exc
    relative = relative_parent / candidate.name
    if relative.parts[:3] != _PUBLIC_PREFIX:
        raise SourceSliceCommandError(
            f"{label} must stay under backend/golden_graph/artifacts"
        )
    return parent / candidate.name, relative.as_posix()


def _public_hash(artifact: BaseModel) -> str:
    return hashlib.sha256(public_artifact_bytes(artifact)).hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build one protocol-bound, redacted PDF Source slice."
    )
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--write-public-artifacts", action="store_true")
    parser.add_argument("--write-private-materialization", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        receipt = bootstrap_protocol_source_slice(
            repository_root=args.repository_root,
            protocol_path=args.protocol,
            write_public_artifacts=args.write_public_artifacts,
            write_private_materialization=args.write_private_materialization,
        )
    except SourceSliceCommandError as exc:
        print(f"source-slice command failed: {exc}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(canonical_json_bytes(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
