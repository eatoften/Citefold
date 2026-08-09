from __future__ import annotations

import hashlib
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace

import pytest

from golden_graph.bindings import (
    ChunkBinding,
    ChunkLocatorBinding,
    ChunkManifest,
    DependencySnapshot,
    SemanticSourceCatalog,
    SourceSliceBuildSummary,
    SourceCatalogPage,
)
from golden_graph.canonical_io import (
    canonical_json_bytes,
    load_hashed_canonical_json,
)
from golden_graph.protocol import (
    load_manifest_authority,
    load_protocol,
    source_slice_build_spec_sha256,
)
from golden_graph.schemas import GoldenGraphProtocol, ToolIdentity
from golden_graph import source_slice_command
from golden_graph.source_slice_builder import public_artifact_bytes
from golden_graph.source_slice_command import (
    SourceSliceCommandError,
    bootstrap_protocol_source_slice,
)


_ROOT = Path(__file__).resolve().parents[2]
_DRAFT = (
    _ROOT
    / "backend/golden_graph/protocols/cs336-sp25-lecture-03-v1.draft.json"
)
_PARSER_CONFIG = (
    "backend/golden_graph/artifacts/config/pdf-parser-v1.json"
)
_CHUNKER_CONFIG = (
    "backend/golden_graph/artifacts/config/utf8-chunker-v1.json"
)
_DEPENDENCIES = (
    "backend/golden_graph/artifacts/cs336-sp25-v1/"
    "lecture-03-architecture.dependency-snapshot.json"
)
def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _ready_fixture() -> tuple[GoldenGraphProtocol, object, SimpleNamespace]:
    template = load_protocol(_DRAFT)
    authority = load_manifest_authority(
        _ROOT / template.acquisition.manifest_path,
        repository_root=_ROOT,
    )
    _, parser_config_hash = load_hashed_canonical_json(_ROOT / _PARSER_CONFIG)
    _, chunker_config_hash = load_hashed_canonical_json(_ROOT / _CHUNKER_CONFIG)
    dependency_value, dependency_hash = load_hashed_canonical_json(
        _ROOT / _DEPENDENCIES
    )
    dependency = DependencySnapshot.model_validate(dependency_value)
    versions = {item.name: item.version for item in dependency.packages}
    parser_path = _ROOT / "backend/golden_graph/pdf_projection_worker.py"
    chunker_path = _ROOT / "backend/golden_graph/utf8_chunker.py"
    parser = ToolIdentity(
        implementation="golden_graph.pdf_projection_worker",
        distribution_name="pypdf",
        implementation_path="backend/golden_graph/pdf_projection_worker.py",
        implementation_sha256=_sha256(parser_path.read_bytes()),
        version=metadata.version("pypdf"),
        config_path=_PARSER_CONFIG,
        config_sha256=parser_config_hash,
    )
    chunker = ToolIdentity(
        implementation="golden_graph.utf8_chunker",
        distribution_name="project-source",
        implementation_path="backend/golden_graph/utf8_chunker.py",
        implementation_sha256=_sha256(chunker_path.read_bytes()),
        version=versions["project-source"],
        config_path=_CHUNKER_CONFIG,
        config_sha256=chunker_config_hash,
    )
    protocol = GoldenGraphProtocol.model_validate(
        template.model_copy(
            update={
                "page_scope": template.page_scope.model_copy(
                    update={
                        "asset_page_count": 1,
                        "included_pages": (1,),
                        "excluded_pages": (),
                        "inclusion_reason": "Synthetic adapter contract page.",
                        "exclusion_reason": "No pages excluded.",
                    }
                ),
                "projection": template.projection.model_copy(
                    update={
                        "parser": parser,
                        "chunker": chunker,
                        "dependency_snapshot_path": _DEPENDENCIES,
                        "dependency_snapshot_sha256": dependency_hash,
                        "semantic_source_catalog_sha256": None,
                        "chunk_manifest_sha256": None,
                        "source_slice_build_summary_sha256": None,
                    }
                ),
            }
        ).model_dump(mode="python", exclude_none=False)
    )

    semantic_payload = b"synthetic private material"
    semantic_hash = _sha256(semantic_payload)
    catalog = SemanticSourceCatalog(
        schema_version=1,
        artifact_role="semantic_source_catalog",
        hash_protocol="semantic-id-independent-v1",
        corpus_id=protocol.acquisition.corpus_id,
        asset_id=protocol.acquisition.asset_id,
        raw_asset_sha256=protocol.acquisition.raw_sha256,
        page_count=1,
        pages=[
            SourceCatalogPage(
                logical_page_id="page-0001",
                page_number=1,
                semantic_page_sha256=semantic_hash,
                semantic_utf8_bytes=len(semantic_payload),
                status="included",
                reason_code=None,
            )
        ],
    )
    catalog_hash = _sha256(canonical_json_bytes(catalog))
    chunks = ChunkManifest(
        schema_version=1,
        artifact_role="semantic_chunk_manifest",
        corpus_id=protocol.acquisition.corpus_id,
        asset_id=protocol.acquisition.asset_id,
        raw_asset_sha256=protocol.acquisition.raw_sha256,
        semantic_source_catalog_sha256=catalog_hash,
        parser=parser,
        chunker=chunker,
        page_coverage_policy="complete_union_overlap_allowed-v1",
        chunks=[
            ChunkBinding(
                ordinal=0,
                semantic_chunk_sha256=semantic_hash,
                locators=[
                    ChunkLocatorBinding(
                        logical_page_id="page-0001",
                        start_offset=0,
                        end_offset=len(semantic_payload),
                        offset_unit="utf8_bytes",
                    )
                ],
            )
        ],
    )
    chunks_hash = _sha256(canonical_json_bytes(chunks))
    summary = SourceSliceBuildSummary(
        schema_version=1,
        artifact_role="golden_graph_source_slice_build_summary",
        project_repository_commit_sha="a" * 40,
        build_spec_protocol_sha256=source_slice_build_spec_sha256(protocol),
        corpus_id=protocol.acquisition.corpus_id,
        asset_id=protocol.acquisition.asset_id,
        manifest_sha256=authority.manifest_sha256,
        raw_asset_sha256=protocol.acquisition.raw_sha256,
        parser_config_sha256=parser.config_sha256,
        chunker_config_sha256=chunker.config_sha256,
        parser_implementation_sha256=parser.implementation_sha256,
        chunker_implementation_sha256=chunker.implementation_sha256,
        dependency_snapshot_sha256=dependency_hash,
        uv_lock_sha256=protocol.projection.uv_lock_sha256,
        semantic_source_catalog_sha256=catalog_hash,
        chunk_manifest_sha256=chunks_hash,
        page_count=1,
        included_page_count=1,
        excluded_page_count=0,
        blank_page_count=0,
        parse_failed_page_count=0,
        chunk_count=1,
    )
    return protocol, authority, SimpleNamespace(
        summary=summary,
        source_catalog=catalog,
        chunk_manifest=chunks,
    )


@pytest.mark.parametrize(
    ("write_public", "write_private"),
    [(True, False), (False, True), (True, True), (False, False)],
    ids=["public-only", "private-only", "both", "dry-run"],
)
def test_write_modes_are_independent_and_receipt_never_leaks(
    monkeypatch: pytest.MonkeyPatch,
    write_public: bool,
    write_private: bool,
) -> None:
    protocol, authority, built = _ready_fixture()
    captured: dict[str, object] = {}
    writes: list[tuple[Path, object]] = []
    private_writes: list[tuple[Path, object, GoldenGraphProtocol]] = []
    currentness_checks: list[Path] = []

    monkeypatch.setattr(source_slice_command, "load_protocol", lambda _path: protocol)
    monkeypatch.setattr(
        source_slice_command,
        "load_manifest_authority",
        lambda *_args, **_kwargs: authority,
    )

    def fake_build(**kwargs):
        captured.update(kwargs)
        return built

    def fake_write(*, output_path, artifact, **_kwargs):
        writes.append((output_path, artifact))
        return _sha256(public_artifact_bytes(artifact))

    def fake_private_write(*, output_path, authority, protocol, **_kwargs):
        private_writes.append((output_path, authority, protocol))
        return SimpleNamespace(
            artifact_path=output_path,
            artifact_sha256="PRIVATE-HASH-MUST-NOT-LEAK",
        )

    monkeypatch.setattr(source_slice_command, "build_source_slice", fake_build)
    monkeypatch.setattr(source_slice_command, "write_public_artifact", fake_write)
    monkeypatch.setattr(
        source_slice_command,
        "_verify_command_module_currentness",
        lambda root: currentness_checks.append(root)
        or (("fixture.py", "c" * 64),),
    )
    monkeypatch.setattr(
        source_slice_command,
        "write_private_source_slice_materialization",
        fake_private_write,
    )

    receipt = bootstrap_protocol_source_slice(
        repository_root=_ROOT,
        protocol_path=_DRAFT,
        write_public_artifacts=write_public,
        write_private_materialization=write_private,
    )

    assert captured["included_pages"] == (1,)
    assert captured["asset_id"] == protocol.acquisition.asset_id
    assert captured["parser_identity"] == protocol.projection.parser
    assert captured["chunker_identity"] == protocol.projection.chunker
    assert captured["build_spec_protocol_sha256"] == (
        source_slice_build_spec_sha256(protocol)
    )
    expected_public_names = (
        [
            "lecture-03-architecture.semantic-source-catalog.json",
            "lecture-03-architecture.semantic-chunks.json",
            "lecture-03-architecture.source-slice-build-summary.json",
        ]
        if write_public
        else []
    )
    assert [path.name for path, _artifact in writes] == expected_public_names
    expected_private_writes = (
        [
            (
                _ROOT
                / "backend/data/golden_graph/source_slice_materializations"
                / f"{protocol.protocol_id}.private.json",
                built,
                protocol,
            )
        ]
        if write_private
        else []
    )
    assert private_writes == expected_private_writes
    serialized = canonical_json_bytes(receipt)
    assert receipt.public_artifacts_written is write_public
    assert receipt.private_materialization_written is write_private
    assert len(currentness_checks) == (
        2 + 2 * int(write_public) + 2 * int(write_private)
    )
    assert b"synthetic private material" not in serialized
    assert b"PRIVATE-HASH-MUST-NOT-LEAK" not in serialized
    assert b"source_slice_materializations" not in serialized
    assert b'"text"' not in serialized


def test_adapter_rejects_incomplete_scope_before_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template = load_protocol(_DRAFT)
    protocol = template.model_copy(
        update={
            "page_scope": template.page_scope.model_copy(
                update={
                    "asset_page_count": None,
                    "included_pages": None,
                    "excluded_pages": None,
                    "inclusion_reason": None,
                    "exclusion_reason": None,
                }
            )
        }
    )
    monkeypatch.setattr(source_slice_command, "load_protocol", lambda _path: protocol)
    monkeypatch.setattr(
        source_slice_command,
        "build_source_slice",
        lambda **_kwargs: pytest.fail("builder must not run"),
    )

    with pytest.raises(SourceSliceCommandError, match="complete page_scope"):
        bootstrap_protocol_source_slice(
            repository_root=_ROOT,
            protocol_path=_DRAFT,
        )


def test_bound_projection_hash_drift_blocks_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol, authority, built = _ready_fixture()
    summary_hash = _sha256(public_artifact_bytes(built.summary))
    assert summary_hash != "f" * 64
    protocol = GoldenGraphProtocol.model_validate(
        protocol.model_copy(
            update={
                "projection": protocol.projection.model_copy(
                    update={
                        "semantic_source_catalog_sha256": (
                            built.summary.semantic_source_catalog_sha256
                        ),
                        "chunk_manifest_sha256": built.summary.chunk_manifest_sha256,
                        "source_slice_build_summary_sha256": "f" * 64,
                    }
                )
            }
        ).model_dump(mode="python", exclude_none=False)
    )
    monkeypatch.setattr(source_slice_command, "load_protocol", lambda _path: protocol)
    monkeypatch.setattr(
        source_slice_command,
        "load_manifest_authority",
        lambda *_args, **_kwargs: authority,
    )
    monkeypatch.setattr(
        source_slice_command, "build_source_slice", lambda **_kwargs: built
    )
    monkeypatch.setattr(
        source_slice_command,
        "write_public_artifact",
        lambda **_kwargs: pytest.fail("writer must not run"),
    )

    with pytest.raises(SourceSliceCommandError, match="projection hashes"):
        bootstrap_protocol_source_slice(
            repository_root=_ROOT,
            protocol_path=_DRAFT,
            write_public_artifacts=True,
        )


def test_partial_projection_hash_triple_is_rejected_before_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol, _authority, _built = _ready_fixture()
    protocol = GoldenGraphProtocol.model_validate(
        protocol.model_copy(
            update={
                "projection": protocol.projection.model_copy(
                    update={"source_slice_build_summary_sha256": "d" * 64}
                )
            }
        ).model_dump(mode="python", exclude_none=False)
    )
    monkeypatch.setattr(source_slice_command, "load_protocol", lambda _path: protocol)
    monkeypatch.setattr(
        source_slice_command,
        "build_source_slice",
        lambda **_kwargs: pytest.fail("builder must not run"),
    )

    with pytest.raises(SourceSliceCommandError, match="all-null or all-bound"):
        bootstrap_protocol_source_slice(
            repository_root=_ROOT,
            protocol_path=_DRAFT,
        )


def test_command_import_source_must_still_match_selected_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    closure: list[tuple[str, str]] = []
    for logical_path in source_slice_command.V1_SOURCE_SLICE_ORCHESTRATION_PATHS:
        path = root / logical_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = f"# captured {logical_path}\n".encode("utf-8")
        path.write_bytes(payload)
        closure.append((logical_path, _sha256(payload)))
    monkeypatch.setattr(
        source_slice_command,
        "_COMMAND_IMPORT_REPOSITORY_ROOT",
        root.resolve(),
    )
    monkeypatch.setattr(
        source_slice_command,
        "_COMMAND_IMPORT_SOURCE_CLOSURE",
        tuple(closure),
    )

    assert source_slice_command._verify_command_module_currentness(root) == tuple(
        closure
    )
    drifted = root / "backend/golden_graph/protocol.py"
    drifted.write_bytes(b"# changed protocol dependency\n")
    with pytest.raises(SourceSliceCommandError, match="stale.*protocol.py"):
        source_slice_command._verify_command_module_currentness(root)
