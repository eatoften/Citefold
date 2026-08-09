"""Build one evidence-bound, redacted PDF Source slice reproducibly.

The builder is deliberately side-effect-light: it verifies one registered
authoring PDF, runs the exact isolated parser in a gitignored temporary
directory, constructs canonical in-memory ``CourseSource`` objects, and
returns a token-gated receipt.  It does not write to the product database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
import platform
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tempfile
import tomllib
from types import ModuleType
from typing import Callable, Iterable, Literal, Protocol
import unicodedata
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.course_source import (
    CourseSource,
    CourseSourceChunk,
    PdfPageLocator,
    source_id_for_asset,
)
from app.source_projection_identity import (
    ProjectionManifestChunk,
    build_projection_manifest_hash,
)
from benchmark_acquisition.fetch import (
    AcquisitionError,
    VerifiedAssetReceipt,
    verify_registered_asset,
)

from .bindings import (
    ChunkBinding,
    ChunkLocatorBinding,
    ChunkManifest,
    DependencySnapshot,
    PdfParserConfigV1,
    SemanticSourceCatalog,
    SourceSliceBuildSummary,
    SourceCatalogPage,
    Utf8ChunkerConfigV1,
)
from .canonical_io import (
    CanonicalArtifactError,
    canonical_json_bytes,
    read_bounded_regular_bytes,
)
from .private_projection import PrivatePdfProjection
from .protocol import (
    ManifestAuthority,
    V1_SOURCE_SLICE_ORCHESTRATION_PATHS,
    load_manifest_authority,
    source_slice_build_spec_sha256,
)
from .schemas import (
    GoldenGraphProtocol,
    JsonArrayTuple,
    SAFE_ID_PATTERN,
    SHA256_PATTERN,
    ToolIdentity,
)


_SOURCE_SLICE_AUTHORITY_TOKEN = object()
_PRIVATE_MATERIALIZATION_TOKEN = object()
_DETERMINISTIC_TIMESTAMP = datetime(1970, 1, 1, tzinfo=timezone.utc)
_PRIVATE_WORK_ROOT = Path("backend/data/golden_graph/source_slice_work")
_PUBLIC_ARTIFACT_ROOT = Path("backend/golden_graph/artifacts")
_PARSER_IMPLEMENTATION = Path(__file__).with_name("pdf_projection_worker.py")
_CHUNKER_IMPLEMENTATION = Path(__file__).with_name("utf8_chunker.py")
_MAX_TOOL_BYTES = 4 * 1024 * 1024
_MAX_WORKER_STDERR_BYTES = 16 * 1024
_MAX_PRIVATE_MATERIALIZATION_BYTES = 512 * 1024 * 1024
_PRIVATE_MATERIALIZATION_ROOT = Path(
    "backend/data/golden_graph/source_slice_materializations"
)
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_FORBIDDEN_PUBLIC_KEYS = frozenset(
    {
        "content",
        "private_materialization",
        "private_path",
        "quote",
        "source_text",
        "text",
    }
)
_BUILDER_IMPORT_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
try:
    _BUILDER_IMPORT_SOURCE_CLOSURE: tuple[tuple[str, str], ...] | None = tuple(
        (
            logical_path,
            hashlib.sha256(
                read_bounded_regular_bytes(
                    _BUILDER_IMPORT_REPOSITORY_ROOT / logical_path,
                    max_bytes=_MAX_TOOL_BYTES,
                    label=f"imported orchestration source {logical_path}",
                )
            ).hexdigest(),
        )
        for logical_path in V1_SOURCE_SLICE_ORCHESTRATION_PATHS
    )
except (CanonicalArtifactError, OSError):
    _BUILDER_IMPORT_SOURCE_CLOSURE = None


class SourceSliceBuildError(RuntimeError):
    """Raised before an unbound or partially materialized slice is returned."""


class StrictSourceSlicePublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _ChunkWindow(Protocol):
    start_offset: int
    end_offset: int
    text: str
    semantic_sha256: str


_Chunker = Callable[..., tuple[_ChunkWindow, ...]]


@dataclass(frozen=True, slots=True)
class _VerifiedToolSource:
    """Stable bytes that, rather than a mutable path, define executed code."""

    path: Path
    sha256: str
    payload: bytes = field(repr=False)


class SourceSliceChunkOffset(StrictSourceSlicePublicModel):
    """Private receipt mapping one product Chunk to its page-local byte span."""

    chunk_id: str = Field(min_length=1, max_length=500)
    logical_page_id: str = Field(pattern=r"^page-[0-9]{4,5}$")
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    offset_unit: Literal["utf8_bytes"]

    @model_validator(mode="after")
    def validate_offsets(self) -> "SourceSliceChunkOffset":
        if self.end_offset <= self.start_offset:
            raise ValueError("Chunk end_offset must exceed start_offset")
        return self


class PrivateSourceSliceMaterialization(BaseModel):
    """Durable, gitignored materialization of the canonical product projection."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        hide_input_in_errors=True,
    )

    schema_version: Literal[1]
    artifact_role: Literal["golden_graph_private_source_slice_materialization"]
    protocol_id: str = Field(pattern=SAFE_ID_PATTERN)
    build_spec_protocol_sha256: str = Field(pattern=SHA256_PATTERN)
    summary_sha256: str = Field(pattern=SHA256_PATTERN)
    source_catalog_sha256: str = Field(pattern=SHA256_PATTERN)
    chunk_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    product_projection_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    summary: SourceSliceBuildSummary
    source_catalog: SemanticSourceCatalog
    chunk_manifest: ChunkManifest
    course_source: CourseSource = Field(repr=False)
    course_source_chunks: JsonArrayTuple[CourseSourceChunk] = Field(
        min_length=1,
        max_length=1_000,
        repr=False,
    )
    chunk_offsets: JsonArrayTuple[SourceSliceChunkOffset] = Field(
        min_length=1,
        max_length=1_000,
        repr=False,
    )

    @model_validator(mode="after")
    def validate_bindings(self) -> "PrivateSourceSliceMaterialization":
        _validate_private_materialization(self)
        return self


@dataclass(frozen=True, slots=True, init=False)
class PrivateSourceSliceMaterializationReceipt:
    """Token-gated receipt for one validated private materialization leaf."""

    artifact_path: Path
    artifact_sha256: str
    materialization: PrivateSourceSliceMaterialization = field(
        repr=False,
        compare=False,
    )
    _validation_token: object = field(repr=False, compare=False)

    def __init__(self, *_args, **_kwargs) -> None:
        raise TypeError(
            "PrivateSourceSliceMaterializationReceipt must come from its writer/loader"
        )

    def __post_init__(self) -> None:
        if self._validation_token is not _PRIVATE_MATERIALIZATION_TOKEN:
            raise ValueError("Invalid private materialization receipt token")
        if not self.artifact_path.is_absolute():
            raise ValueError("Private materialization path must be absolute")
        if not _is_lower_sha256(self.artifact_sha256):
            raise ValueError("Private materialization SHA-256 is invalid")


@dataclass(frozen=True, slots=True, init=False)
class SourceSliceBuildAuthority:
    """Receipt proving that private product data matches redacted artifacts."""

    summary: SourceSliceBuildSummary
    source_catalog: SemanticSourceCatalog
    chunk_manifest: ChunkManifest
    course_source: CourseSource
    course_source_chunks: tuple[CourseSourceChunk, ...] = field(repr=False)
    chunk_offsets: tuple[SourceSliceChunkOffset, ...]
    verified_asset: VerifiedAssetReceipt = field(repr=False, compare=False)
    _validation_token: object = field(repr=False, compare=False)

    def __init__(self, *_args, **_kwargs) -> None:
        raise TypeError("SourceSliceBuildAuthority must come from build_source_slice")

    def __post_init__(self) -> None:
        if self._validation_token is not _SOURCE_SLICE_AUTHORITY_TOKEN:
            raise ValueError("Invalid SourceSliceBuildAuthority validation token")
        if self.summary.raw_asset_sha256 != self.verified_asset.sha256:
            raise ValueError("Source-slice authority raw asset binding mismatch")
        if self.summary.chunk_count != len(self.course_source_chunks):
            raise ValueError("Source-slice authority Chunk count mismatch")
        if len(self.chunk_offsets) != len(self.course_source_chunks):
            raise ValueError("Source-slice authority offset count mismatch")
        if len(self.chunk_manifest.chunks) != len(self.course_source_chunks):
            raise ValueError("Chunk manifest and product Chunk counts differ")
        if (
            _sha256_model(self.source_catalog)
            != self.summary.semantic_source_catalog_sha256
        ):
            raise ValueError("Source catalog does not match authority hash")
        if _sha256_model(self.chunk_manifest) != self.summary.chunk_manifest_sha256:
            raise ValueError("Chunk manifest does not match authority hash")
        _validate_authority_product_projection(self)


def build_source_slice(
    *,
    manifest_authority: ManifestAuthority,
    repository_root: Path,
    asset_id: str,
    included_pages: Iterable[int],
    parser_config: PdfParserConfigV1,
    chunker_config: Utf8ChunkerConfigV1,
    parser_identity: ToolIdentity,
    chunker_identity: ToolIdentity,
    dependency_snapshot: DependencySnapshot,
    dependency_snapshot_path: str,
    dependency_snapshot_sha256: str,
    uv_lock_path: str,
    uv_lock_sha256: str,
    build_spec_protocol_sha256: str,
) -> SourceSliceBuildAuthority:
    """Materialize one explicit page slice from an exact authoring PDF.

    Non-empty pages outside ``included_pages`` are represented publicly only
    as ``excluded/out_of_scope``.  Blank and failed pages retain only their
    redacted classification.  Selecting a blank or failed page fails closed.
    """

    root = Path(repository_root).resolve(strict=True)
    selected_pages = _normalize_included_pages(included_pages)
    try:
        project_repository_commit_sha = verified_clean_git_head(root)
        orchestration_source_closure = _verify_builder_module_currentness(root)
        current_manifest_authority = load_manifest_authority(
            root / manifest_authority.manifest_path,
            repository_root=root,
        )
        if current_manifest_authority != manifest_authority:
            raise SourceSliceBuildError(
                "ManifestAuthority no longer matches its tracked manifest leaf"
            )
        verified_asset = verify_registered_asset(
            manifest_authority.manifest,
            asset_id,
            root,
            allowed_partitions=("authoring",),
        )
        if verified_asset.partition != "authoring":
            raise SourceSliceBuildError("Only authoring assets can build this slice")

        parser_source = _verify_tool_binding(
            root,
            parser_identity,
            parser_config,
            expected_implementation=_PARSER_IMPLEMENTATION,
            label="parser",
        )
        chunker_source = _verify_tool_binding(
            root,
            chunker_identity,
            chunker_config,
            expected_implementation=_CHUNKER_IMPLEMENTATION,
            label="chunker",
        )
        _verify_derivation_environment(
            root=root,
            parser_identity=parser_identity,
            chunker_identity=chunker_identity,
            dependency_snapshot=dependency_snapshot,
            dependency_snapshot_path=dependency_snapshot_path,
            dependency_snapshot_sha256=dependency_snapshot_sha256,
            uv_lock_path=uv_lock_path,
            uv_lock_sha256=uv_lock_sha256,
        )
        projection = _run_private_pdf_worker(
            root=root,
            parser_source=parser_source,
            receipt=verified_asset,
            config=parser_config,
        )
        chunker = _load_verified_chunker(chunker_source)
        authority = _materialize_source_slice(
            manifest_authority=manifest_authority,
            verified_asset=verified_asset,
            selected_pages=selected_pages,
            projection=projection,
            parser_config=parser_config,
            chunker_config=chunker_config,
            parser_identity=parser_identity,
            chunker_identity=chunker_identity,
            dependency_snapshot_sha256=dependency_snapshot_sha256,
            uv_lock_sha256=uv_lock_sha256,
            chunker=chunker,
            project_repository_commit_sha=project_repository_commit_sha,
            build_spec_protocol_sha256=build_spec_protocol_sha256,
        )
        if verified_clean_git_head(root) != project_repository_commit_sha:
            raise SourceSliceBuildError("Project Git HEAD changed during the build")
        if _verify_builder_module_currentness(root) != orchestration_source_closure:
            raise SourceSliceBuildError(
                "Orchestration source closure changed during the build"
            )
        return authority
    except SourceSliceBuildError:
        raise
    except ValidationError:
        raise SourceSliceBuildError(
            "Private Source projection failed strict validation"
        ) from None
    except (
        AcquisitionError,
        CanonicalArtifactError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ) as exc:
        raise SourceSliceBuildError(f"Source-slice build failed safely: {exc}") from exc


def public_artifact_bytes(
    artifact: SemanticSourceCatalog | ChunkManifest | SourceSliceBuildSummary,
) -> bytes:
    """Serialize only one of the strict, source-text-free public DTOs."""

    if type(artifact) not in (
        SemanticSourceCatalog,
        ChunkManifest,
        SourceSliceBuildSummary,
    ):
        raise TypeError("Public writer accepts only strict redacted artifacts")
    decoded = artifact.model_dump(mode="json", exclude_none=False)
    _reject_forbidden_public_keys(decoded)
    return canonical_json_bytes(decoded)


def write_public_artifact(
    *,
    repository_root: Path,
    output_path: Path,
    artifact: SemanticSourceCatalog | ChunkManifest | SourceSliceBuildSummary,
) -> str:
    """Publish a redacted artifact and sidecar without replacing conflicts."""

    root = Path(repository_root).resolve(strict=True)
    path = _resolve_public_output_path(root, output_path)
    payload = public_artifact_bytes(artifact)
    digest = hashlib.sha256(payload).hexdigest()
    sidecar = f"{digest}  {path.name}\n".encode("utf-8")
    _require_public_leaf_compatible(path, payload)
    _require_public_leaf_compatible(path.with_suffix(".sha256"), sidecar)
    _write_or_converge(path, payload)
    _write_or_converge(path.with_suffix(".sha256"), sidecar)
    return digest


def write_private_source_slice_materialization(
    *,
    repository_root: Path,
    output_path: Path,
    authority: SourceSliceBuildAuthority,
    protocol: GoldenGraphProtocol,
) -> PrivateSourceSliceMaterializationReceipt:
    """Durably publish private Source text only inside the gitignored boundary."""

    root = Path(repository_root).resolve(strict=True)
    path = _resolve_private_materialization_path(
        root,
        output_path,
        create_parent=True,
    )
    _validate_private_expected_protocol(
        protocol=protocol,
        summary=authority.summary,
        catalog=authority.source_catalog,
        chunks=authority.chunk_manifest,
        artifact_path=path,
    )
    _verify_private_git_boundary(root, path)
    _verify_private_git_boundary(root, path.with_suffix(".sha256"))
    materialization = PrivateSourceSliceMaterialization(
        schema_version=1,
        artifact_role="golden_graph_private_source_slice_materialization",
        protocol_id=protocol.protocol_id,
        build_spec_protocol_sha256=authority.summary.build_spec_protocol_sha256,
        summary_sha256=_sha256_model(authority.summary),
        source_catalog_sha256=authority.summary.semantic_source_catalog_sha256,
        chunk_manifest_sha256=authority.summary.chunk_manifest_sha256,
        product_projection_manifest_sha256=(
            authority.course_source.projection_manifest_hash
        ),
        summary=authority.summary,
        source_catalog=authority.source_catalog,
        chunk_manifest=authority.chunk_manifest,
        course_source=authority.course_source,
        course_source_chunks=authority.course_source_chunks,
        chunk_offsets=authority.chunk_offsets,
    )
    payload = canonical_json_bytes(materialization)
    if len(payload) > _MAX_PRIVATE_MATERIALIZATION_BYTES:
        raise SourceSliceBuildError("Private materialization exceeds its byte limit")
    digest = hashlib.sha256(payload).hexdigest()
    sidecar_path = path.with_suffix(".sha256")
    sidecar = f"{digest}  {path.name}\n".encode("utf-8")
    _require_private_leaf_compatible(path, payload)
    _require_private_leaf_compatible(sidecar_path, sidecar)
    _write_private_or_converge(path, payload)
    _write_private_or_converge(sidecar_path, sidecar)
    _verify_private_git_boundary(root, path)
    _verify_private_git_boundary(root, sidecar_path)
    return _issue_private_materialization_receipt(
        path=path,
        digest=digest,
        materialization=materialization,
    )


def load_private_source_slice_materialization(
    *,
    repository_root: Path,
    artifact_path: Path,
    expected_protocol: GoldenGraphProtocol,
) -> PrivateSourceSliceMaterializationReceipt:
    """Reload and fully revalidate one durable private materialization."""

    root = Path(repository_root).resolve(strict=True)
    path = _resolve_private_materialization_path(
        root,
        artifact_path,
        create_parent=False,
    )
    _verify_private_git_boundary(root, path)
    _verify_private_git_boundary(root, path.with_suffix(".sha256"))
    try:
        payload = read_bounded_regular_bytes(
            path,
            max_bytes=_MAX_PRIVATE_MATERIALIZATION_BYTES,
            label="private Source-slice materialization",
        )
        digest = hashlib.sha256(payload).hexdigest()
        expected_sidecar = f"{digest}  {path.name}\n".encode("utf-8")
        sidecar = read_bounded_regular_bytes(
            path.with_suffix(".sha256"),
            max_bytes=1024,
            label="private Source-slice materialization sidecar",
        )
        if sidecar != expected_sidecar:
            raise SourceSliceBuildError("Private materialization sidecar mismatch")
        decoded = _decode_strict_canonical_json(payload)
        try:
            materialization = PrivateSourceSliceMaterialization.model_validate(decoded)
        except ValidationError:
            raise SourceSliceBuildError(
                "Private materialization failed strict validation"
            ) from None
        if canonical_json_bytes(materialization) != payload:
            raise SourceSliceBuildError(
                "Private materialization contains non-canonical nested fields"
            )
        _validate_private_expected_protocol(
            protocol=expected_protocol,
            summary=materialization.summary,
            catalog=materialization.source_catalog,
            chunks=materialization.chunk_manifest,
            artifact_path=path,
        )
        if (
            materialization.protocol_id != expected_protocol.protocol_id
            or materialization.build_spec_protocol_sha256
            != materialization.summary.build_spec_protocol_sha256
        ):
            raise SourceSliceBuildError(
                "Private materialization protocol binding mismatch"
            )
        _verify_private_git_boundary(root, path)
        _verify_private_git_boundary(root, path.with_suffix(".sha256"))
        return _issue_private_materialization_receipt(
            path=path,
            digest=digest,
            materialization=materialization,
        )
    except SourceSliceBuildError:
        raise
    except (CanonicalArtifactError, OSError, ValueError) as exc:
        raise SourceSliceBuildError(
            "Private materialization could not be loaded safely"
        ) from exc


def verified_clean_git_head(repository_root: Path) -> str:
    """Return the exact project revision only for a clean repository state."""

    root = Path(repository_root).resolve(strict=True)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        head = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "rev-parse",
                "--verify",
                "HEAD^{commit}",
            ],
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
            timeout=5,
        ).stdout.decode("ascii").strip()
        dirty = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
            timeout=10,
        ).stdout
    except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
        raise SourceSliceBuildError("Cannot verify project Git revision") from exc
    if len(head) != 40 or any(value not in "0123456789abcdef" for value in head):
        raise SourceSliceBuildError("Project Git HEAD is not a full commit identity")
    if dirty:
        raise SourceSliceBuildError("Project Git revision is not clean")
    return head


def _verify_builder_module_currentness(
    repository_root: Path,
) -> tuple[tuple[str, str], ...]:
    """Reject stale imports across the complete v1 orchestration closure."""

    root = Path(repository_root).resolve(strict=True)
    if (
        _BUILDER_IMPORT_SOURCE_CLOSURE is None
        or root != _BUILDER_IMPORT_REPOSITORY_ROOT
    ):
        raise SourceSliceBuildError(
            "Imported orchestration does not belong to the selected repository"
        )
    expected_hashes = dict(_BUILDER_IMPORT_SOURCE_CLOSURE)
    current: list[tuple[str, str]] = []
    for logical_path in V1_SOURCE_SLICE_ORCHESTRATION_PATHS:
        candidate = root / logical_path
        try:
            candidate.resolve(strict=True).relative_to(root)
            current_sha256 = hashlib.sha256(
                read_bounded_regular_bytes(
                    candidate,
                    max_bytes=_MAX_TOOL_BYTES,
                    label=f"current orchestration source {logical_path}",
                )
            ).hexdigest()
        except (CanonicalArtifactError, OSError, ValueError) as exc:
            raise SourceSliceBuildError(
                f"Cannot verify orchestration source: {logical_path}"
            ) from exc
        if current_sha256 != expected_hashes.get(logical_path):
            raise SourceSliceBuildError(
                f"Imported orchestration source is stale: {logical_path}"
            )
        current.append((logical_path, current_sha256))
    return tuple(current)


def _materialize_source_slice(
    *,
    manifest_authority: ManifestAuthority,
    verified_asset: VerifiedAssetReceipt,
    selected_pages: tuple[int, ...],
    projection: PrivatePdfProjection,
    parser_config: PdfParserConfigV1,
    chunker_config: Utf8ChunkerConfigV1,
    parser_identity: ToolIdentity,
    chunker_identity: ToolIdentity,
    dependency_snapshot_sha256: str,
    uv_lock_sha256: str,
    chunker: _Chunker,
    project_repository_commit_sha: str,
    build_spec_protocol_sha256: str,
) -> SourceSliceBuildAuthority:
    if projection.raw_asset_sha256 != verified_asset.sha256:
        raise SourceSliceBuildError("Worker output has the wrong raw asset identity")
    if projection.normalization != parser_config.normalization:
        raise SourceSliceBuildError("Worker normalization differs from parser config")
    if selected_pages[-1] > projection.page_count:
        raise SourceSliceBuildError("Included page is outside the PDF page range")

    selected = frozenset(selected_pages)
    by_number = {page.page_number: page for page in projection.pages}
    invalid_selected = [
        page_number
        for page_number in selected_pages
        if by_number[page_number].status != "included"
    ]
    if invalid_selected:
        raise SourceSliceBuildError(
            "Selected pages must all be successfully parsed and non-blank: "
            + ", ".join(str(value) for value in invalid_selected)
        )

    total_private_bytes = sum(
        page.semantic_utf8_bytes for page in projection.pages
    )
    if total_private_bytes > parser_config.max_total_utf8_bytes:
        raise SourceSliceBuildError("Worker output exceeds total semantic-byte limit")
    if any(
        page.semantic_utf8_bytes > parser_config.max_page_utf8_bytes
        for page in projection.pages
    ):
        raise SourceSliceBuildError("Worker output exceeds page semantic-byte limit")

    public_pages: list[SourceCatalogPage] = []
    for private_page in projection.pages:
        if private_page.page_number in selected:
            public_pages.append(
                SourceCatalogPage(
                    logical_page_id=private_page.logical_page_id,
                    page_number=private_page.page_number,
                    semantic_page_sha256=private_page.semantic_page_sha256,
                    semantic_utf8_bytes=private_page.semantic_utf8_bytes,
                    status="included",
                    reason_code=None,
                )
            )
        elif private_page.status == "included":
            public_pages.append(
                SourceCatalogPage(
                    logical_page_id=private_page.logical_page_id,
                    page_number=private_page.page_number,
                    semantic_page_sha256=_EMPTY_SHA256,
                    semantic_utf8_bytes=0,
                    status="excluded",
                    reason_code="out_of_scope",
                )
            )
        else:
            public_pages.append(
                SourceCatalogPage(
                    logical_page_id=private_page.logical_page_id,
                    page_number=private_page.page_number,
                    semantic_page_sha256=_EMPTY_SHA256,
                    semantic_utf8_bytes=0,
                    status=private_page.status,
                    reason_code=private_page.reason_code,
                )
            )

    catalog = SemanticSourceCatalog(
        schema_version=1,
        artifact_role="semantic_source_catalog",
        hash_protocol="semantic-id-independent-v1",
        corpus_id=verified_asset.corpus_id,
        asset_id=verified_asset.asset_id,
        raw_asset_sha256=verified_asset.sha256,
        page_count=projection.page_count,
        pages=public_pages,
    )
    catalog_sha256 = _sha256_model(catalog)

    chunk_bindings: list[ChunkBinding] = []
    private_chunks: list[tuple[_ChunkWindow, int, str]] = []
    for page_number in selected_pages:
        page = by_number[page_number]
        if page.text is None:
            raise SourceSliceBuildError("Included private page unexpectedly lacks text")
        page_windows = chunker(
            page.text,
            max_chunk_utf8_bytes=chunker_config.max_chunk_utf8_bytes,
            overlap_utf8_bytes=chunker_config.overlap_utf8_bytes,
        )
        _validate_page_window_coverage(page.text, page_windows)
        for window in page_windows:
            if len(chunk_bindings) >= chunker_config.max_chunks:
                raise SourceSliceBuildError("Source slice exceeds max_chunks")
            ordinal = len(chunk_bindings)
            locator = ChunkLocatorBinding(
                logical_page_id=page.logical_page_id,
                start_offset=window.start_offset,
                end_offset=window.end_offset,
                offset_unit="utf8_bytes",
            )
            chunk_bindings.append(
                ChunkBinding(
                    ordinal=ordinal,
                    semantic_chunk_sha256=window.semantic_sha256,
                    locators=[locator],
                )
            )
            private_chunks.append((window, page_number, page.logical_page_id))
    if not chunk_bindings:
        raise SourceSliceBuildError("Selected pages produced no semantic Chunks")

    chunk_manifest = ChunkManifest(
        schema_version=1,
        artifact_role="semantic_chunk_manifest",
        corpus_id=verified_asset.corpus_id,
        asset_id=verified_asset.asset_id,
        raw_asset_sha256=verified_asset.sha256,
        semantic_source_catalog_sha256=catalog_sha256,
        parser=parser_identity,
        chunker=chunker_identity,
        page_coverage_policy=chunker_config.page_coverage_policy,
        chunks=chunk_bindings,
    )
    chunk_manifest_sha256 = _sha256_model(chunk_manifest)
    source, chunks, offsets = _build_product_projection(
        manifest_authority=manifest_authority,
        receipt=verified_asset,
        catalog_sha256=catalog_sha256,
        manifest_sha256=chunk_manifest_sha256,
        private_chunks=private_chunks,
    )

    counts = {
        status: sum(page.status == status for page in public_pages)
        for status in ("included", "excluded", "blank", "parse_failed")
    }
    summary = SourceSliceBuildSummary(
        schema_version=1,
        artifact_role="golden_graph_source_slice_build_summary",
        project_repository_commit_sha=project_repository_commit_sha,
        build_spec_protocol_sha256=build_spec_protocol_sha256,
        corpus_id=verified_asset.corpus_id,
        asset_id=verified_asset.asset_id,
        manifest_sha256=manifest_authority.manifest_sha256,
        raw_asset_sha256=verified_asset.sha256,
        parser_config_sha256=parser_identity.config_sha256,
        chunker_config_sha256=chunker_identity.config_sha256,
        parser_implementation_sha256=parser_identity.implementation_sha256,
        chunker_implementation_sha256=chunker_identity.implementation_sha256,
        dependency_snapshot_sha256=dependency_snapshot_sha256,
        uv_lock_sha256=uv_lock_sha256,
        semantic_source_catalog_sha256=catalog_sha256,
        chunk_manifest_sha256=chunk_manifest_sha256,
        page_count=projection.page_count,
        included_page_count=counts["included"],
        excluded_page_count=counts["excluded"],
        blank_page_count=counts["blank"],
        parse_failed_page_count=counts["parse_failed"],
        chunk_count=len(chunks),
    )
    return _issue_source_slice_authority(
        summary=summary,
        catalog=catalog,
        chunk_manifest=chunk_manifest,
        source=source,
        chunks=chunks,
        offsets=offsets,
        verified_asset=verified_asset,
    )


def _build_product_projection(
    *,
    manifest_authority: ManifestAuthority,
    receipt: VerifiedAssetReceipt,
    catalog_sha256: str,
    manifest_sha256: str,
    private_chunks: list[tuple[_ChunkWindow, int, str]],
) -> tuple[
    CourseSource,
    tuple[CourseSourceChunk, ...],
    tuple[SourceSliceChunkOffset, ...],
]:
    asset = next(
        item
        for item in manifest_authority.manifest.assets
        if item.asset_id == receipt.asset_id
    )
    source_origin_id = f"benchmark:{receipt.corpus_id}:{receipt.asset_id}"
    source_id = source_id_for_asset(source_origin_id)
    chunks: list[CourseSourceChunk] = []
    offsets: list[SourceSliceChunkOffset] = []
    for ordinal, (window, page_number, logical_page_id) in enumerate(private_chunks):
        origin_id = (
            f"{source_origin_id}:{logical_page_id}:"
            f"{window.start_offset}-{window.end_offset}:{window.semantic_sha256}"
        )
        chunk_id = f"source_unit:{origin_id}"
        chunks.append(
            CourseSourceChunk(
                id=chunk_id,
                source_id=source_id,
                origin_type="source_unit",
                origin_id=origin_id,
                chunk_type="page",
                ordinal=ordinal,
                text=window.text,
                text_hash=window.semantic_sha256,
                locator=PdfPageLocator(
                    asset_id=source_origin_id,
                    page_number=page_number,
                    metadata={
                        "logical_page_id": logical_page_id,
                        "start_offset": window.start_offset,
                        "end_offset": window.end_offset,
                        "offset_unit": "utf8_bytes",
                        "golden_source_catalog_sha256": catalog_sha256,
                        "golden_chunk_manifest_sha256": manifest_sha256,
                    },
                ),
                chunker_version="utf8_sliding_window_v1",
                created_at=_DETERMINISTIC_TIMESTAMP,
                updated_at=_DETERMINISTIC_TIMESTAMP,
            )
        )
        offsets.append(
            SourceSliceChunkOffset(
                chunk_id=chunk_id,
                logical_page_id=logical_page_id,
                start_offset=window.start_offset,
                end_offset=window.end_offset,
                offset_unit="utf8_bytes",
            )
        )
    product_manifest_sha256 = build_projection_manifest_hash(
        source_id=source_id,
        source_type="pdf",
        chunks=(
            ProjectionManifestChunk(
                id=chunk.id,
                chunk_type=chunk.chunk_type,
                ordinal=chunk.ordinal,
                text_hash=chunk.text_hash,
                locator=chunk.locator,
                chunker_version=chunk.chunker_version,
            )
            for chunk in chunks
        ),
    )
    source = CourseSource(
        id=source_id,
        course_id=f"benchmark:{receipt.corpus_id}",
        origin_type="source_asset",
        origin_id=source_origin_id,
        source_type="pdf",
        title=asset.title,
        content_status="ready",
        index_status="not_indexed",
        chunk_count=len(chunks),
        projection_generation_id=(
            f"golden-source-slice:{product_manifest_sha256[:32]}"
        ),
        projection_manifest_hash=product_manifest_sha256,
        size_bytes=receipt.byte_size,
        mime_type=receipt.media_type,
        metadata={
            "benchmark_corpus_id": receipt.corpus_id,
            "acquisition_manifest_sha256": manifest_authority.manifest_sha256,
            "raw_asset_sha256": receipt.sha256,
            "golden_source_catalog_sha256": catalog_sha256,
            "golden_chunk_manifest_sha256": manifest_sha256,
        },
        created_at=_DETERMINISTIC_TIMESTAMP,
        updated_at=_DETERMINISTIC_TIMESTAMP,
    )
    return source, tuple(chunks), tuple(offsets)


def _issue_source_slice_authority(
    *,
    summary: SourceSliceBuildSummary,
    catalog: SemanticSourceCatalog,
    chunk_manifest: ChunkManifest,
    source: CourseSource,
    chunks: tuple[CourseSourceChunk, ...],
    offsets: tuple[SourceSliceChunkOffset, ...],
    verified_asset: VerifiedAssetReceipt,
) -> SourceSliceBuildAuthority:
    authority = object.__new__(SourceSliceBuildAuthority)
    values = {
        "summary": summary,
        "source_catalog": catalog,
        "chunk_manifest": chunk_manifest,
        "course_source": source,
        "course_source_chunks": chunks,
        "chunk_offsets": offsets,
        "verified_asset": verified_asset,
        "_validation_token": _SOURCE_SLICE_AUTHORITY_TOKEN,
    }
    for name, value in values.items():
        object.__setattr__(authority, name, value)
    authority.__post_init__()
    return authority


def _validate_authority_product_projection(
    authority: SourceSliceBuildAuthority,
) -> None:
    _validate_product_projection_values(
        summary=authority.summary,
        catalog=authority.source_catalog,
        manifest=authority.chunk_manifest,
        source=authority.course_source,
        chunks=authority.course_source_chunks,
        offsets=authority.chunk_offsets,
    )


def _validate_product_projection_values(
    *,
    summary: SourceSliceBuildSummary,
    catalog: SemanticSourceCatalog,
    manifest: ChunkManifest,
    source: CourseSource,
    chunks: tuple[CourseSourceChunk, ...],
    offsets: tuple[SourceSliceChunkOffset, ...],
) -> None:
    if (catalog.corpus_id, catalog.asset_id, catalog.raw_asset_sha256) != (
        summary.corpus_id,
        summary.asset_id,
        summary.raw_asset_sha256,
    ):
        raise ValueError("Source catalog identity differs from authority summary")
    if (manifest.corpus_id, manifest.asset_id, manifest.raw_asset_sha256) != (
        summary.corpus_id,
        summary.asset_id,
        summary.raw_asset_sha256,
    ):
        raise ValueError("Chunk manifest identity differs from authority summary")
    if (
        manifest.semantic_source_catalog_sha256
        != summary.semantic_source_catalog_sha256
        or manifest.parser.implementation_sha256
        != summary.parser_implementation_sha256
        or manifest.parser.config_sha256 != summary.parser_config_sha256
        or manifest.chunker.implementation_sha256
        != summary.chunker_implementation_sha256
        or manifest.chunker.config_sha256 != summary.chunker_config_sha256
    ):
        raise ValueError("Chunk manifest derivation differs from authority summary")
    product_manifest_sha256 = build_projection_manifest_hash(
        source_id=source.id,
        source_type=source.source_type,
        chunks=(
            ProjectionManifestChunk(
                id=chunk.id,
                chunk_type=chunk.chunk_type,
                ordinal=chunk.ordinal,
                text_hash=chunk.text_hash,
                locator=chunk.locator,
                chunker_version=chunk.chunker_version,
            )
            for chunk in chunks
        ),
    )
    if (
        source.projection_manifest_hash != product_manifest_sha256
        or source.chunk_count != len(chunks)
        or source.id != source_id_for_asset(source.origin_id)
        or source.origin_id
        != f"benchmark:{summary.corpus_id}:{summary.asset_id}"
    ):
        raise ValueError("CourseSource projection binding mismatch")
    if (
        source.metadata.get("golden_source_catalog_sha256")
        != summary.semantic_source_catalog_sha256
        or source.metadata.get("golden_chunk_manifest_sha256")
        != summary.chunk_manifest_sha256
    ):
        raise ValueError("CourseSource Source-catalog binding mismatch")

    identifiers = [chunk.id for chunk in chunks]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Product Chunk IDs must be unique")
    if [chunk.ordinal for chunk in chunks] != list(range(len(chunks))):
        raise ValueError("Product Chunk ordinals must be contiguous")
    page_numbers = {
        page.logical_page_id: page.page_number for page in catalog.pages
    }
    for chunk, binding, offset in zip(chunks, manifest.chunks, offsets, strict=True):
        if chunk.source_id != source.id or chunk.ordinal != binding.ordinal:
            raise ValueError("Product Chunk source or ordinal binding mismatch")
        text_hash = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
        if chunk.text_hash != text_hash or binding.semantic_chunk_sha256 != text_hash:
            raise ValueError("Product Chunk text hash binding mismatch")
        if offset.chunk_id != chunk.id or len(binding.locators) != 1:
            raise ValueError("Product Chunk offset binding mismatch")
        locator_binding = binding.locators[0]
        if (
            offset.logical_page_id != locator_binding.logical_page_id
            or offset.start_offset != locator_binding.start_offset
            or offset.end_offset != locator_binding.end_offset
            or offset.offset_unit != locator_binding.offset_unit
        ):
            raise ValueError("Product Chunk byte offsets differ from manifest")
        if not isinstance(chunk.locator, PdfPageLocator):
            raise ValueError("Product Chunk must use PdfPageLocator")
        expected_locator_metadata = {
            "logical_page_id": locator_binding.logical_page_id,
            "start_offset": locator_binding.start_offset,
            "end_offset": locator_binding.end_offset,
            "offset_unit": locator_binding.offset_unit,
            "golden_source_catalog_sha256": summary.semantic_source_catalog_sha256,
            "golden_chunk_manifest_sha256": summary.chunk_manifest_sha256,
        }
        if (
            chunk.locator.asset_id != source.origin_id
            or
            chunk.locator.page_number
            != page_numbers.get(locator_binding.logical_page_id)
            or chunk.locator.metadata != expected_locator_metadata
        ):
            raise ValueError("Product Chunk PDF locator binding mismatch")


def _validate_private_materialization(
    materialization: PrivateSourceSliceMaterialization,
) -> None:
    if (
        materialization.build_spec_protocol_sha256
        != materialization.summary.build_spec_protocol_sha256
    ):
        raise ValueError("Private materialization build-spec binding mismatch")
    if _sha256_model(materialization.summary) != materialization.summary_sha256:
        raise ValueError("Private materialization summary hash mismatch")
    if (
        _sha256_model(materialization.source_catalog)
        != materialization.source_catalog_sha256
        or materialization.source_catalog_sha256
        != materialization.summary.semantic_source_catalog_sha256
    ):
        raise ValueError("Private materialization Source catalog hash mismatch")
    if (
        _sha256_model(materialization.chunk_manifest)
        != materialization.chunk_manifest_sha256
        or materialization.chunk_manifest_sha256
        != materialization.summary.chunk_manifest_sha256
    ):
        raise ValueError("Private materialization Chunk manifest hash mismatch")
    if (
        materialization.course_source.projection_manifest_hash
        != materialization.product_projection_manifest_sha256
    ):
        raise ValueError("Private materialization product manifest hash mismatch")
    _validate_product_projection_values(
        summary=materialization.summary,
        catalog=materialization.source_catalog,
        manifest=materialization.chunk_manifest,
        source=materialization.course_source,
        chunks=tuple(materialization.course_source_chunks),
        offsets=tuple(materialization.chunk_offsets),
    )


def _validate_private_expected_protocol(
    *,
    protocol: GoldenGraphProtocol,
    summary: SourceSliceBuildSummary,
    catalog: SemanticSourceCatalog,
    chunks: ChunkManifest,
    artifact_path: Path,
) -> None:
    if type(protocol) is not GoldenGraphProtocol:
        raise SourceSliceBuildError("Expected protocol must be strictly validated")
    if artifact_path.name != f"{protocol.protocol_id}.private.json":
        raise SourceSliceBuildError(
            "Private materialization filename must match protocol_id"
        )
    projection = protocol.projection
    acquisition = protocol.acquisition
    parser = projection.parser
    chunker = projection.chunker
    dependency_sha256 = projection.dependency_snapshot_sha256
    if parser is None or chunker is None or dependency_sha256 is None:
        raise SourceSliceBuildError(
            "Expected protocol does not contain a build-ready projection"
        )
    expected_build_spec_sha256 = source_slice_build_spec_sha256(protocol)
    actual_identity = (
        summary.build_spec_protocol_sha256,
        summary.corpus_id,
        summary.asset_id,
        summary.manifest_sha256,
        summary.raw_asset_sha256,
        summary.parser_config_sha256,
        summary.chunker_config_sha256,
        summary.parser_implementation_sha256,
        summary.chunker_implementation_sha256,
        summary.dependency_snapshot_sha256,
        summary.uv_lock_sha256,
    )
    expected_identity = (
        expected_build_spec_sha256,
        acquisition.corpus_id,
        acquisition.asset_id,
        acquisition.manifest_sha256,
        acquisition.raw_sha256,
        parser.config_sha256,
        chunker.config_sha256,
        parser.implementation_sha256,
        chunker.implementation_sha256,
        dependency_sha256,
        projection.uv_lock_sha256,
    )
    if actual_identity != expected_identity:
        raise SourceSliceBuildError(
            "Private materialization differs from expected protocol authority"
        )
    scope = protocol.page_scope
    included_pages = set(scope.included_pages or ())
    if (
        scope.asset_page_count is None
        or scope.included_pages is None
        or catalog.page_count != scope.asset_page_count
        or any(
            (page.page_number in included_pages) != (page.status == "included")
            for page in catalog.pages
        )
    ):
        raise SourceSliceBuildError(
            "Private materialization differs from expected protocol page scope"
        )
    if (
        _sha256_model(catalog) != summary.semantic_source_catalog_sha256
        or _sha256_model(chunks) != summary.chunk_manifest_sha256
    ):
        raise SourceSliceBuildError("Private materialization public leaf mismatch")
    for expected, actual, label in (
        (
            projection.semantic_source_catalog_sha256,
            summary.semantic_source_catalog_sha256,
            "Source catalog",
        ),
        (
            projection.chunk_manifest_sha256,
            summary.chunk_manifest_sha256,
            "Chunk manifest",
        ),
        (
            projection.source_slice_build_summary_sha256,
            _sha256_model(summary),
            "build summary",
        ),
    ):
        if expected is not None and expected != actual:
            raise SourceSliceBuildError(
                f"Private materialization {label} differs from bound protocol"
            )


def _issue_private_materialization_receipt(
    *,
    path: Path,
    digest: str,
    materialization: PrivateSourceSliceMaterialization,
) -> PrivateSourceSliceMaterializationReceipt:
    receipt = object.__new__(PrivateSourceSliceMaterializationReceipt)
    for name, value in {
        "artifact_path": path,
        "artifact_sha256": digest,
        "materialization": materialization,
        "_validation_token": _PRIVATE_MATERIALIZATION_TOKEN,
    }.items():
        object.__setattr__(receipt, name, value)
    receipt.__post_init__()
    return receipt


def _resolve_private_materialization_path(
    root: Path,
    supplied: Path,
    *,
    create_parent: bool,
) -> Path:
    private_root = root / _PRIVATE_MATERIALIZATION_ROOT
    candidate = supplied if supplied.is_absolute() else root / supplied
    candidate = Path(os.path.abspath(candidate))
    try:
        relative = candidate.relative_to(private_root)
    except ValueError as exc:
        raise SourceSliceBuildError(
            "Private materialization must stay inside its gitignored boundary"
        ) from exc
    if (
        not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or not candidate.name.endswith(".private.json")
    ):
        raise SourceSliceBuildError("Invalid private materialization path")
    parent_relative = _PRIVATE_MATERIALIZATION_ROOT / relative.parent
    if create_parent:
        _ensure_plain_directory(root, parent_relative)
    else:
        _require_plain_directory(root, parent_relative)
    return candidate


def _verify_private_git_boundary(root: Path, path: Path) -> None:
    """Require a target to be ignored and absent from the Git index."""

    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise SourceSliceBuildError("Private Git target escaped repository") from exc
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment["GIT_OPTIONAL_LOCKS"] = "0"

    def run(
        arguments: list[str],
        *,
        capture_stdout: bool = False,
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                ["git", "-C", str(root), *arguments],
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=(subprocess.PIPE if capture_stdout else subprocess.DEVNULL),
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SourceSliceBuildError(
                "Cannot verify private Git boundary"
            ) from exc

    inside = run(["rev-parse", "--is-inside-work-tree"], capture_stdout=True)
    ignored = run(["check-ignore", "-q", "--", relative])
    tracked = run(["ls-files", "--error-unmatch", "--", relative])
    if inside.returncode != 0 or inside.stdout.strip() != b"true":
        raise SourceSliceBuildError("Private materialization requires a Git worktree")
    if ignored.returncode != 0:
        raise SourceSliceBuildError("Private materialization target is not gitignored")
    if tracked.returncode == 0:
        raise SourceSliceBuildError("Private materialization target is tracked by Git")
    if tracked.returncode != 1:
        raise SourceSliceBuildError("Cannot determine private Git tracking state")


def _ensure_plain_directory(root: Path, relative: Path) -> Path:
    current = root
    for part in relative.parts:
        current = current / part
        try:
            current.mkdir()
        except FileExistsError:
            pass
        metadata = current.lstat()
        _reject_link_like(metadata, current)
        if not stat.S_ISDIR(metadata.st_mode):
            raise SourceSliceBuildError("Private boundary contains a non-directory")
    current.resolve(strict=True).relative_to(root)
    return current


def _require_plain_directory(root: Path, relative: Path) -> Path:
    current = root
    try:
        for part in relative.parts:
            current = current / part
            metadata = current.lstat()
            _reject_link_like(metadata, current)
            if not stat.S_ISDIR(metadata.st_mode):
                raise SourceSliceBuildError(
                    "Private boundary contains a non-directory"
                )
        current.resolve(strict=True).relative_to(root)
        return current
    except SourceSliceBuildError:
        raise
    except (OSError, ValueError) as exc:
        raise SourceSliceBuildError("Private materialization directory is unavailable") from exc


def _require_private_leaf_compatible(path: Path, payload: bytes) -> None:
    if not os.path.lexists(path):
        return
    try:
        existing = read_bounded_regular_bytes(
            path,
            max_bytes=_MAX_PRIVATE_MATERIALIZATION_BYTES,
            label="existing private materialization leaf",
        )
    except CanonicalArtifactError as exc:
        raise SourceSliceBuildError(
            f"Private materialization conflict: {path.name}"
        ) from exc
    if existing != payload:
        raise SourceSliceBuildError(f"Private materialization conflict: {path.name}")


def _write_private_or_converge(path: Path, payload: bytes) -> None:
    _require_private_leaf_compatible(path, payload)
    if os.path.lexists(path):
        return
    temporary_path: Path | None = None
    descriptor = -1
    try:
        descriptor, name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".private-publish-tmp",
        )
        temporary_path = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            _require_private_leaf_compatible(path, payload)
    except SourceSliceBuildError:
        raise
    except OSError as exc:
        raise SourceSliceBuildError("Cannot publish private materialization") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _is_lower_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _run_private_pdf_worker(
    *,
    root: Path,
    parser_source: _VerifiedToolSource,
    receipt: VerifiedAssetReceipt,
    config: PdfParserConfigV1,
) -> PrivatePdfProjection:
    private_root = _ensure_private_work_root(root)
    with tempfile.TemporaryDirectory(
        prefix=f"{receipt.asset_id}.",
        dir=private_root,
    ) as temporary_name:
        temporary = Path(temporary_name)
        output_path = temporary / "private-projection.json"
        stderr_path = temporary / "worker.stderr"
        parser_snapshot = temporary / "verified-pdf-projection-worker.py"
        _write_exclusive_private_bytes(parser_snapshot, parser_source.payload)
        if (
            hashlib.sha256(
                read_bounded_regular_bytes(
                    parser_snapshot,
                    max_bytes=_MAX_TOOL_BYTES,
                    label="private parser snapshot",
                )
            ).hexdigest()
            != parser_source.sha256
        ):
            raise SourceSliceBuildError("Parser snapshot identity drift")
        command = [
            sys.executable,
            "-I",
            str(parser_snapshot),
            "--input",
            str(receipt.path),
            "--output",
            str(output_path),
            "--expected-sha256",
            receipt.sha256,
            "--expected-bytes",
            str(receipt.byte_size),
            "--max-pdf-bytes",
            str(config.max_pdf_bytes),
            "--max-pages",
            str(config.max_pages),
            "--max-page-utf8-bytes",
            str(config.max_page_utf8_bytes),
            "--max-total-utf8-bytes",
            str(config.max_total_utf8_bytes),
        ]
        try:
            with stderr_path.open("xb") as stderr:
                completed = subprocess.run(
                    command,
                    cwd=root,
                    env=_sanitized_worker_environment(),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=stderr,
                    check=False,
                    timeout=config.timeout_seconds,
                )
        except subprocess.TimeoutExpired as exc:
            raise SourceSliceBuildError("PDF worker exceeded its wall-clock limit") from exc
        if completed.returncode != 0:
            stderr_digest = _bounded_stderr_digest(stderr_path)
            raise SourceSliceBuildError(
                "PDF worker rejected the asset "
                f"(exit={completed.returncode}, stderr_sha256={stderr_digest})"
            )

        maximum_payload_bytes = (
            config.max_total_utf8_bytes + config.max_pages * 1024 + 64 * 1024
        )
        payload = read_bounded_regular_bytes(
            output_path,
            max_bytes=maximum_payload_bytes,
            label="private PDF projection",
        )
        decoded = _decode_strict_canonical_json(payload)
        try:
            projection = PrivatePdfProjection.model_validate(decoded)
        except ValidationError:
            raise SourceSliceBuildError(
                "Private PDF worker output failed strict validation"
            ) from None
        if projection.page_count > config.max_pages:
            raise SourceSliceBuildError("Worker output exceeds max_pages")
        return projection


def _load_verified_chunker(source: _VerifiedToolSource) -> _Chunker:
    """Load the Chunker from the exact bytes already bound by its receipt."""

    module_name = f"_golden_graph_verified_chunker_{uuid4().hex}"
    module = ModuleType(module_name)
    module.__file__ = str(source.path)
    module.__package__ = ""
    try:
        code = compile(
            source.payload,
            str(source.path),
            "exec",
            dont_inherit=True,
        )
        sys.modules[module_name] = module
        exec(code, module.__dict__)
        candidate = module.__dict__.get("chunk_utf8_text")
        if not callable(candidate):
            raise SourceSliceBuildError(
                "Verified Chunker does not export chunk_utf8_text"
            )
        return candidate
    except SourceSliceBuildError:
        raise
    except Exception as exc:
        raise SourceSliceBuildError("Verified Chunker could not be loaded") from exc
    finally:
        sys.modules.pop(module_name, None)


def _write_exclusive_private_bytes(path: Path, payload: bytes) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0),
            stat.S_IRUSR | stat.S_IWUSR,
        )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise SourceSliceBuildError("Cannot create exclusive private snapshot") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _verify_tool_binding(
    root: Path,
    identity: ToolIdentity,
    config: PdfParserConfigV1 | Utf8ChunkerConfigV1,
    *,
    expected_implementation: Path,
    label: str,
) -> _VerifiedToolSource:
    implementation_path = _resolve_repository_leaf(
        root,
        identity.implementation_path,
    )
    if implementation_path != expected_implementation.resolve(strict=True):
        raise SourceSliceBuildError(f"{label} identity names the wrong implementation")
    implementation = read_bounded_regular_bytes(
        implementation_path,
        max_bytes=_MAX_TOOL_BYTES,
        label=f"{label} implementation",
    )
    implementation_sha256 = hashlib.sha256(implementation).hexdigest()
    if implementation_sha256 != identity.implementation_sha256:
        raise SourceSliceBuildError(f"{label} implementation SHA-256 mismatch")

    config_path = _resolve_repository_leaf(root, identity.config_path)
    config_payload = read_bounded_regular_bytes(
        config_path,
        max_bytes=_MAX_TOOL_BYTES,
        label=f"{label} config",
    )
    expected_config = canonical_json_bytes(config)
    if config_payload != expected_config:
        raise SourceSliceBuildError(f"{label} config object differs from bound file")
    if hashlib.sha256(config_payload).hexdigest() != identity.config_sha256:
        raise SourceSliceBuildError(f"{label} config SHA-256 mismatch")
    return _VerifiedToolSource(
        path=implementation_path,
        sha256=implementation_sha256,
        payload=implementation,
    )


def _verify_derivation_environment(
    *,
    root: Path,
    parser_identity: ToolIdentity,
    chunker_identity: ToolIdentity,
    dependency_snapshot: DependencySnapshot,
    dependency_snapshot_path: str,
    dependency_snapshot_sha256: str,
    uv_lock_path: str,
    uv_lock_sha256: str,
) -> None:
    dependency_path = _resolve_repository_leaf(root, dependency_snapshot_path)
    dependency_payload = read_bounded_regular_bytes(
        dependency_path,
        max_bytes=_MAX_TOOL_BYTES,
        label="dependency snapshot",
    )
    expected_dependency_payload = canonical_json_bytes(dependency_snapshot)
    if dependency_payload != expected_dependency_payload:
        raise SourceSliceBuildError(
            "Dependency snapshot object differs from its bound leaf"
        )
    if hashlib.sha256(dependency_payload).hexdigest() != dependency_snapshot_sha256:
        raise SourceSliceBuildError("Dependency snapshot SHA-256 mismatch")
    if dependency_snapshot.python_version != platform.python_version():
        raise SourceSliceBuildError("Dependency snapshot Python version drift")
    if dependency_snapshot.unicode_database_version != unicodedata.unidata_version:
        raise SourceSliceBuildError("Dependency snapshot Unicode database drift")

    tools = (parser_identity, chunker_identity)
    package_versions = {
        item.name.casefold(): item.version for item in dependency_snapshot.packages
    }
    expected_names = {tool.distribution_name.casefold() for tool in tools}
    if set(package_versions) != expected_names:
        raise SourceSliceBuildError(
            "Dependency snapshot must contain exactly parser and chunker distributions"
        )
    if parser_identity.distribution_name != "pypdf":
        raise SourceSliceBuildError("The PDF parser must bind the pypdf distribution")
    if chunker_identity.distribution_name != "project-source":
        raise SourceSliceBuildError(
            "The UTF-8 chunker must bind the project-source distribution"
        )
    for tool in tools:
        if package_versions.get(tool.distribution_name.casefold()) != tool.version:
            raise SourceSliceBuildError(
                f"Dependency snapshot version drift: {tool.distribution_name}"
            )
        if tool.distribution_name != "project-source":
            try:
                installed = importlib.metadata.version(tool.distribution_name)
            except importlib.metadata.PackageNotFoundError as exc:
                raise SourceSliceBuildError(
                    f"Required distribution is not installed: {tool.distribution_name}"
                ) from exc
            if installed != tool.version:
                raise SourceSliceBuildError(
                    f"Installed distribution version drift: {tool.distribution_name}"
                )

    lock_path = _resolve_repository_leaf(root, uv_lock_path)
    lock_payload = read_bounded_regular_bytes(
        lock_path,
        max_bytes=128 * 1024 * 1024,
        label="uv lock",
    )
    if hashlib.sha256(lock_payload).hexdigest() != uv_lock_sha256:
        raise SourceSliceBuildError("uv.lock SHA-256 mismatch")
    locked_versions = _parse_uv_lock_versions(lock_payload)
    for tool in tools:
        if tool.distribution_name == "project-source":
            continue
        if tool.version not in locked_versions.get(
            tool.distribution_name.casefold(), frozenset()
        ):
            raise SourceSliceBuildError(
                f"Tool version is absent from uv.lock: {tool.distribution_name}"
            )


def _parse_uv_lock_versions(payload: bytes) -> dict[str, frozenset[str]]:
    try:
        decoded = tomllib.loads(payload.decode("utf-8"))
        packages = decoded.get("package")
        if decoded.get("version") != 1 or not isinstance(packages, list):
            raise SourceSliceBuildError("uv.lock has an unsupported structure")
        versions: dict[str, set[str]] = {}
        for package in packages:
            if not isinstance(package, dict):
                raise SourceSliceBuildError("uv.lock contains an invalid package")
            name = package.get("name")
            version = package.get("version")
            if not isinstance(name, str) or not isinstance(version, str):
                raise SourceSliceBuildError("uv.lock package identity is invalid")
            versions.setdefault(name.casefold(), set()).add(version)
        return {name: frozenset(values) for name, values in versions.items()}
    except SourceSliceBuildError:
        raise
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise SourceSliceBuildError("Cannot parse uv.lock") from exc


def _normalize_included_pages(values: Iterable[int]) -> tuple[int, ...]:
    try:
        iterator = iter(values)
    except TypeError as exc:
        raise SourceSliceBuildError("included_pages must be an iterable") from exc
    supplied: list[int] = []
    for value in iterator:
        if len(supplied) >= 10_000:
            raise SourceSliceBuildError("included_pages exceeds the page limit")
        supplied.append(value)
    if not supplied:
        raise SourceSliceBuildError("included_pages must not be empty")
    if any(isinstance(value, bool) or type(value) is not int for value in supplied):
        raise SourceSliceBuildError("included_pages must contain strict integers")
    if any(value < 1 or value > 10_000 for value in supplied):
        raise SourceSliceBuildError("included_pages contains an invalid page number")
    if len(supplied) != len(set(supplied)):
        raise SourceSliceBuildError("included_pages cannot contain duplicates")
    return tuple(sorted(supplied))


def _validate_page_window_coverage(
    text: str,
    windows: tuple[_ChunkWindow, ...],
) -> None:
    semantic_bytes = text.encode("utf-8")
    if (
        not windows
        or windows[0].start_offset != 0
        or windows[-1].end_offset != len(semantic_bytes)
    ):
        raise SourceSliceBuildError("Chunker did not cover the complete page")
    previous_end = 0
    for window in windows:
        if window.start_offset > previous_end:
            raise SourceSliceBuildError("Chunker left an uncovered page byte range")
        payload = semantic_bytes[window.start_offset : window.end_offset]
        if (
            payload.decode("utf-8") != window.text
            or hashlib.sha256(payload).hexdigest() != window.semantic_sha256
        ):
            raise SourceSliceBuildError("Chunker emitted an invalid semantic window")
        previous_end = window.end_offset


def _resolve_repository_leaf(root: Path, logical_path: str) -> Path:
    relative = PurePosixPath(logical_path)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or "\\" in logical_path
    ):
        raise SourceSliceBuildError("Tool path must be repository-relative POSIX")
    current = root
    try:
        for index, part in enumerate(relative.parts):
            current = current / part
            metadata = current.lstat()
            _reject_link_like(metadata, current)
            if index < len(relative.parts) - 1:
                if not stat.S_ISDIR(metadata.st_mode):
                    raise SourceSliceBuildError("Tool parent is not a directory")
            elif not stat.S_ISREG(metadata.st_mode):
                raise SourceSliceBuildError("Tool leaf is not a regular file")
        resolved = current.resolve(strict=True)
        resolved.relative_to(root)
        return resolved
    except SourceSliceBuildError:
        raise
    except (OSError, ValueError) as exc:
        raise SourceSliceBuildError(f"Cannot resolve repository tool: {logical_path}") from exc


def _ensure_private_work_root(root: Path) -> Path:
    current = root
    for part in _PRIVATE_WORK_ROOT.parts:
        current = current / part
        try:
            current.mkdir()
        except FileExistsError:
            pass
        metadata = current.lstat()
        _reject_link_like(metadata, current)
        if not stat.S_ISDIR(metadata.st_mode):
            raise SourceSliceBuildError("Private work root contains a non-directory")
    current.resolve(strict=True).relative_to(root)
    return current


def _resolve_public_output_path(root: Path, supplied: Path) -> Path:
    path = supplied if supplied.is_absolute() else root / supplied
    public_root = root / _PUBLIC_ARTIFACT_ROOT
    if path.suffix != ".json" or path.name.casefold().endswith(".frozen.json"):
        raise SourceSliceBuildError("Public source-slice output must be a non-frozen JSON")
    try:
        relative_parent = path.parent.resolve(strict=True).relative_to(
            public_root.resolve(strict=True)
        )
    except (OSError, ValueError) as exc:
        raise SourceSliceBuildError(
            "Public output parent must already exist under golden_graph/artifacts"
        ) from exc
    current = public_root
    for part in relative_parent.parts:
        current = current / part
        metadata = current.lstat()
        _reject_link_like(metadata, current)
        if not stat.S_ISDIR(metadata.st_mode):
            raise SourceSliceBuildError("Public artifact parent is not a directory")
    return path


def _reject_link_like(metadata: os.stat_result, path: Path) -> None:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if stat.S_ISLNK(metadata.st_mode) or (
        reparse_flag
        and getattr(metadata, "st_file_attributes", 0) & reparse_flag
    ):
        raise SourceSliceBuildError(f"Link-like path is forbidden: {path}")


def _sanitized_worker_environment() -> dict[str, str]:
    environment = {
        "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8",
        "TZ": "UTC",
    }
    for key in ("COMSPEC", "PATHEXT", "SYSTEMROOT", "TEMP", "TMP", "WINDIR"):
        value = os.environ.get(key)
        if value:
            environment[key] = value
    environment["PATH"] = str(Path(sys.executable).resolve().parent)
    return environment


def _decode_strict_canonical_json(payload: bytes) -> object:
    try:
        decoded = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except SourceSliceBuildError:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise SourceSliceBuildError("Worker output is not strict UTF-8 JSON") from exc
    if canonical_json_bytes(decoded) != payload:
        raise SourceSliceBuildError("Worker output is not canonical JSON")
    return decoded


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SourceSliceBuildError(f"Duplicate worker JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise SourceSliceBuildError(f"Non-finite worker JSON number is forbidden: {value}")


def _bounded_stderr_digest(path: Path) -> str:
    try:
        payload = read_bounded_regular_bytes(
            path,
            max_bytes=_MAX_WORKER_STDERR_BYTES,
            label="PDF worker stderr",
        )
    except CanonicalArtifactError:
        return "unavailable"
    return hashlib.sha256(payload).hexdigest()


def _reject_forbidden_public_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() in _FORBIDDEN_PUBLIC_KEYS:
                raise SourceSliceBuildError(f"Forbidden public artifact key: {key}")
            _reject_forbidden_public_keys(child)
    elif isinstance(value, list):
        for child in value:
            _reject_forbidden_public_keys(child)


def _write_or_converge(path: Path, payload: bytes) -> None:
    if path.exists():
        existing = _read_existing_public_artifact(path, len(payload))
        if existing != payload:
            raise SourceSliceBuildError(f"Public artifact conflict: {path.name}")
        return
    temporary_path: Path | None = None
    descriptor = -1
    try:
        descriptor, name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".publish-tmp",
        )
        temporary_path = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            existing = _read_existing_public_artifact(path, len(payload))
            if existing != payload:
                raise SourceSliceBuildError(f"Public artifact conflict: {path.name}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _require_public_leaf_compatible(path: Path, payload: bytes) -> None:
    if not os.path.lexists(path):
        return
    existing = _read_existing_public_artifact(path, len(payload))
    if existing != payload:
        raise SourceSliceBuildError(f"Public artifact conflict: {path.name}")


def _read_existing_public_artifact(path: Path, expected_bytes: int) -> bytes:
    try:
        return read_bounded_regular_bytes(
            path,
            max_bytes=max(_MAX_TOOL_BYTES, expected_bytes),
            label="existing public artifact",
        )
    except CanonicalArtifactError as exc:
        raise SourceSliceBuildError(
            f"Public artifact conflict: {path.name}"
        ) from exc


def _sha256_model(model: BaseModel) -> str:
    return hashlib.sha256(canonical_json_bytes(model)).hexdigest()
