from __future__ import annotations

from dataclasses import replace
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path
import subprocess
import unicodedata

import pytest
from pydantic import ValidationError

from benchmark_acquisition.fetch import _issue_verified_asset_receipt
from benchmark_acquisition.manifest import (
    AssetSpec,
    CorpusManifest,
    CourseRegistration,
)
from golden_graph.bindings import (
    DependencyPackage,
    DependencySnapshot,
    PdfParserConfigV1,
    SemanticSourceCatalog,
    SourceCatalogPage,
    Utf8ChunkerConfigV1,
)
from golden_graph.canonical_io import canonical_json_bytes
from golden_graph.private_projection import (
    PrivatePdfPageProjection,
    PrivatePdfProjection,
)
from golden_graph.protocol import (
    ManifestAuthority,
    load_protocol,
    source_slice_build_spec_sha256,
)
from golden_graph.schemas import GoldenGraphProtocol
from golden_graph.schemas import ToolIdentity
from golden_graph import source_slice_builder
from golden_graph.source_slice_builder import (
    SourceSliceBuildError,
    build_source_slice,
    load_private_source_slice_materialization,
    public_artifact_bytes,
    write_private_source_slice_materialization,
    write_public_artifact,
)
from app.source_projection_identity import (
    ProjectionManifestChunk,
    build_projection_manifest_hash,
)


_REAL_RUN_PRIVATE_PDF_WORKER = source_slice_builder._run_private_pdf_worker


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@pytest.fixture
def build_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    root = tmp_path / "repo"
    tool_root = root / "backend" / "golden_graph"
    config_root = tool_root / "artifacts" / "fixture"
    config_root.mkdir(parents=True)
    (root / "backend" / "data").mkdir()

    parser_path = tool_root / "pdf_projection_worker.py"
    chunker_path = tool_root / "utf8_chunker.py"
    parser_payload = b"# synthetic parser identity\n"
    chunker_payload = (
        Path(source_slice_builder.__file__).with_name("utf8_chunker.py").read_bytes()
    )
    parser_path.write_bytes(parser_payload)
    chunker_path.write_bytes(chunker_payload)
    monkeypatch.setattr(source_slice_builder, "_PARSER_IMPLEMENTATION", parser_path)
    monkeypatch.setattr(source_slice_builder, "_CHUNKER_IMPLEMENTATION", chunker_path)

    parser_config = PdfParserConfigV1(
        schema_version=1,
        artifact_role="golden_graph_pdf_parser_config",
        extraction_mode="pypdf_plain_text_v1",
        normalization="unicode_nfkc_lf_v1",
        reader_strict=False,
        ocr_policy="disabled",
        blank_detection="unicode_whitespace_only_v1",
        page_failure_policy="record_and_continue_v1",
        encrypted_pdf_policy="reject",
        timeout_scope="whole_asset_worker_wall_clock_v1",
        max_pdf_bytes=4096,
        max_pages=10,
        max_page_utf8_bytes=1024,
        max_total_utf8_bytes=4096,
        timeout_seconds=5,
    )
    chunker_config = Utf8ChunkerConfigV1(
        schema_version=1,
        artifact_role="golden_graph_utf8_chunker_config",
        algorithm="utf8_sliding_window_v1",
        utf8_boundary_policy="codepoint_safe_max_end_forward_start_v1",
        max_chunk_utf8_bytes=8,
        overlap_utf8_bytes=2,
        max_chunks=100,
        cross_page_chunks=False,
        page_coverage_policy="complete_union_overlap_allowed-v1",
    )
    parser_config_path = config_root / "parser-config.json"
    chunker_config_path = config_root / "chunker-config.json"
    parser_config_payload = canonical_json_bytes(parser_config)
    chunker_config_payload = canonical_json_bytes(chunker_config)
    parser_config_path.write_bytes(parser_config_payload)
    chunker_config_path.write_bytes(chunker_config_payload)

    pypdf_version = importlib.metadata.version("pypdf")
    parser_identity = ToolIdentity(
        implementation="fixture.pdf_projection_worker",
        distribution_name="pypdf",
        implementation_path="backend/golden_graph/pdf_projection_worker.py",
        implementation_sha256=_sha256(parser_payload),
        version=pypdf_version,
        config_path="backend/golden_graph/artifacts/fixture/parser-config.json",
        config_sha256=_sha256(parser_config_payload),
    )
    chunker_identity = ToolIdentity(
        implementation="fixture.utf8_chunker",
        distribution_name="project-source",
        implementation_path="backend/golden_graph/utf8_chunker.py",
        implementation_sha256=_sha256(chunker_payload),
        version="source-slice-test-v1",
        config_path="backend/golden_graph/artifacts/fixture/chunker-config.json",
        config_sha256=_sha256(chunker_config_payload),
    )
    dependency_snapshot = DependencySnapshot(
        schema_version=1,
        artifact_role="golden_graph_dependency_snapshot",
        python_version=platform.python_version(),
        unicode_database_version=unicodedata.unidata_version,
        packages=[
            DependencyPackage(name="project-source", version="source-slice-test-v1"),
            DependencyPackage(name="pypdf", version=pypdf_version),
        ],
    )
    dependency_path = config_root / "dependency-snapshot.json"
    dependency_payload = canonical_json_bytes(dependency_snapshot)
    dependency_path.write_bytes(dependency_payload)
    uv_lock_payload = (
        "version = 1\n"
        "\n[[package]]\n"
        'name = "pypdf"\n'
        f'version = "{pypdf_version}"\n'
    ).encode("utf-8")
    (root / "backend" / "uv.lock").write_bytes(uv_lock_payload)

    raw_path = root / "backend" / "data" / "fixture.pdf"
    raw_payload = b"%PDF-1.7\nfixture\n%%EOF\n"
    raw_path.write_bytes(raw_payload)
    raw_sha256 = _sha256(raw_payload)
    asset = AssetSpec(
        asset_id="lecture-fixture",
        title="Fixture lecture",
        partition="authoring",
        upstream_path="lectures/fixture.pdf",
        canonical_url="https://raw.githubusercontent.com/test/course/a/fixture.pdf",
        output_filename="fixture.pdf",
        byte_size=len(raw_payload),
        media_type="application/pdf",
        accepted_content_types=("application/pdf",),
        git_blob_sha1="1" * 40,
        sha256=raw_sha256,
        license_spdx="MIT",
        redistribution_allowed=False,
    )
    manifest = CorpusManifest(
        schema_version=1,
        corpus_id="fixture-corpus",
        registered_at="2026-08-09",
        course=CourseRegistration(
            institution="Fixture University",
            course_code="TEST-1",
            title="Fixture Course",
            term="2026",
        ),
        attribution="Fixture",
        repository_url="https://github.com/test/course",
        repository_slug="test/course",
        commit_sha="2" * 40,
        license_spdx="MIT",
        license_url="https://raw.githubusercontent.com/test/course/a/LICENSE",
        license_blob_sha1="3" * 40,
        license_sha256="4" * 64,
        default_output_directory="backend/data/public_course_benchmarks/fixture-corpus",
        allowed_hosts=("raw.githubusercontent.com",),
        timeout_seconds=5,
        asset_deadline_seconds=10,
        max_asset_bytes=4096,
        max_assets=1,
        max_total_bytes=4096,
        assets=(asset,),
    )
    manifest_authority = ManifestAuthority(
        manifest=manifest,
        manifest_sha256="5" * 64,
        manifest_path="backend/benchmark_acquisition/manifests/fixture.json",
    )
    template = load_protocol(
        Path(__file__).resolve().parents[2]
        / "backend/golden_graph/protocols/cs336-sp25-lecture-03-v1.draft.json"
    )
    protocol = GoldenGraphProtocol.model_validate(
        template.model_copy(
            update={
                "protocol_id": "fixture-source-slice-v1",
                "acquisition": template.acquisition.model_copy(
                    update={
                        "manifest_path": manifest_authority.manifest_path,
                        "manifest_sha256": manifest_authority.manifest_sha256,
                        "corpus_id": manifest.corpus_id,
                        "asset_id": asset.asset_id,
                        "partition": "authoring",
                        "raw_sha256": asset.sha256,
                    }
                ),
                "page_scope": template.page_scope.model_copy(
                    update={
                        "asset_page_count": 4,
                        "included_pages": (1,),
                        "excluded_pages": (2, 3, 4),
                        "inclusion_reason": "Synthetic included page.",
                        "exclusion_reason": "Synthetic excluded pages.",
                    }
                ),
                "projection": template.projection.model_copy(
                    update={
                        "parser": parser_identity,
                        "chunker": chunker_identity,
                        "dependency_snapshot_path": (
                            "backend/golden_graph/artifacts/fixture/"
                            "dependency-snapshot.json"
                        ),
                        "dependency_snapshot_sha256": _sha256(dependency_payload),
                        "uv_lock_path": "backend/uv.lock",
                        "uv_lock_sha256": _sha256(uv_lock_payload),
                        "semantic_source_catalog_sha256": None,
                        "chunk_manifest_sha256": None,
                        "source_slice_build_summary_sha256": None,
                    }
                ),
            }
        ).model_dump(mode="python", exclude_none=False)
    )
    receipt = _issue_verified_asset_receipt(
        manifest=manifest,
        asset=asset,
        path=raw_path.resolve(),
        verified=raw_path.stat(),
    )
    projection = PrivatePdfProjection(
        schema_version=1,
        artifact_role="golden_graph_private_pdf_pages",
        raw_asset_sha256=raw_sha256,
        normalization="unicode_nfkc_lf_v1",
        page_count=4,
        total_semantic_utf8_bytes=len(b"alpha beta") + len(b"gamma"),
        pages=[
            _private_page(1, "alpha beta"),
            _private_empty_page(2, "blank", "no_semantic_text"),
            _private_empty_page(3, "parse_failed", "parser_error"),
            _private_page(4, "gamma"),
        ],
    )
    monkeypatch.setattr(
        source_slice_builder,
        "load_manifest_authority",
        lambda *_args, **_kwargs: manifest_authority,
    )
    monkeypatch.setattr(
        source_slice_builder,
        "verified_clean_git_head",
        lambda _root: "a" * 40,
    )
    monkeypatch.setattr(
        source_slice_builder,
        "_verify_builder_module_currentness",
        lambda _root: "b" * 64,
    )
    monkeypatch.setattr(
        source_slice_builder,
        "_verify_private_git_boundary",
        lambda _root, _path: None,
    )
    monkeypatch.setattr(
        source_slice_builder,
        "verify_registered_asset",
        lambda *_args, **_kwargs: receipt,
    )
    monkeypatch.setattr(
        source_slice_builder,
        "_run_private_pdf_worker",
        lambda **_kwargs: projection,
    )
    return {
        "root": root,
        "manifest_authority": manifest_authority,
        "parser_config": parser_config,
        "chunker_config": chunker_config,
        "parser_identity": parser_identity,
        "chunker_identity": chunker_identity,
        "dependency_snapshot": dependency_snapshot,
        "dependency_snapshot_path": "backend/golden_graph/artifacts/fixture/dependency-snapshot.json",
        "dependency_snapshot_sha256": _sha256(dependency_payload),
        "uv_lock_path": "backend/uv.lock",
        "uv_lock_sha256": _sha256(uv_lock_payload),
        "projection": projection,
        "protocol": protocol,
        "build_spec_protocol_sha256": source_slice_build_spec_sha256(protocol),
        "receipt": receipt,
        "parser_path": parser_path,
        "parser_source": source_slice_builder._VerifiedToolSource(
            path=parser_path,
            sha256=_sha256(parser_payload),
            payload=parser_payload,
        ),
        "parser_config_path": parser_config_path,
    }


def _private_page(page_number: int, text: str) -> PrivatePdfPageProjection:
    payload = text.encode("utf-8")
    return PrivatePdfPageProjection(
        logical_page_id=f"page-{page_number:04d}",
        page_number=page_number,
        semantic_page_sha256=_sha256(payload),
        semantic_utf8_bytes=len(payload),
        status="included",
        reason_code=None,
        text=text,
    )


def _private_empty_page(
    page_number: int,
    status: str,
    reason_code: str,
) -> PrivatePdfPageProjection:
    return PrivatePdfPageProjection.model_validate(
        {
            "logical_page_id": f"page-{page_number:04d}",
            "page_number": page_number,
            "semantic_page_sha256": _sha256(b""),
            "semantic_utf8_bytes": 0,
            "status": status,
            "reason_code": reason_code,
            "text": None,
        }
    )


def _build(fixture: dict[str, object], *, included_pages: tuple[int, ...] = (1,)):
    return build_source_slice(
        manifest_authority=fixture["manifest_authority"],
        repository_root=fixture["root"],
        asset_id="lecture-fixture",
        included_pages=included_pages,
        parser_config=fixture["parser_config"],
        chunker_config=fixture["chunker_config"],
        parser_identity=fixture["parser_identity"],
        chunker_identity=fixture["chunker_identity"],
        dependency_snapshot=fixture["dependency_snapshot"],
        dependency_snapshot_path=fixture["dependency_snapshot_path"],
        dependency_snapshot_sha256=fixture["dependency_snapshot_sha256"],
        uv_lock_path=fixture["uv_lock_path"],
        uv_lock_sha256=fixture["uv_lock_sha256"],
        build_spec_protocol_sha256=fixture["build_spec_protocol_sha256"],
    )


def test_build_maps_deterministically_and_keeps_public_artifacts_redacted(
    build_fixture: dict[str, object],
) -> None:
    first = _build(build_fixture)
    second = _build(build_fixture)

    assert first.summary == second.summary
    assert first.summary.project_repository_commit_sha == "a" * 40
    assert first.source_catalog == second.source_catalog
    assert first.chunk_manifest == second.chunk_manifest
    assert first.course_source == second.course_source
    assert first.course_source_chunks == second.course_source_chunks
    assert [page.status for page in first.source_catalog.pages] == [
        "included",
        "blank",
        "parse_failed",
        "excluded",
    ]
    assert all(chunk.locator.kind == "pdf_page" for chunk in first.course_source_chunks)
    assert first.course_source_chunks[0].text.startswith("alpha")
    assert first.course_source_chunks[-1].text.endswith("beta")
    assert "gamma" not in "".join(chunk.text for chunk in first.course_source_chunks)
    assert first.course_source.origin_id == "benchmark:fixture-corpus:lecture-fixture"
    assert first.course_source.id == (
        "asset:benchmark:fixture-corpus:lecture-fixture"
    )
    expected_product_hash = build_projection_manifest_hash(
        source_id=first.course_source.id,
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
            for chunk in first.course_source_chunks
        ),
    )
    assert first.course_source.projection_manifest_hash == expected_product_hash
    assert first.course_source.metadata["golden_chunk_manifest_sha256"] == (
        first.summary.chunk_manifest_sha256
    )
    for chunk, offset in zip(
        first.course_source_chunks,
        first.chunk_offsets,
        strict=True,
    ):
        assert chunk.locator.asset_id == first.course_source.origin_id
        assert chunk.locator.metadata == {
            "logical_page_id": offset.logical_page_id,
            "start_offset": offset.start_offset,
            "end_offset": offset.end_offset,
            "offset_unit": "utf8_bytes",
            "golden_source_catalog_sha256": (
                first.summary.semantic_source_catalog_sha256
            ),
            "golden_chunk_manifest_sha256": first.summary.chunk_manifest_sha256,
        }
    for artifact in (first.summary, first.source_catalog, first.chunk_manifest):
        payload = public_artifact_bytes(artifact)
        assert b"alpha beta" not in payload
        assert b"gamma" not in payload
        assert b'"text"' not in payload


@pytest.mark.parametrize("page_number", [2, 3])
def test_selected_blank_or_failed_page_rejects_authority(
    build_fixture: dict[str, object],
    page_number: int,
) -> None:
    with pytest.raises(SourceSliceBuildError, match="successfully parsed"):
        _build(build_fixture, included_pages=(page_number,))


def test_worker_timeout_is_fail_closed_and_cleans_private_temp(
    build_fixture: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser_source = build_fixture["parser_source"]

    def _timeout(command, *_args, **_kwargs):
        snapshot = Path(command[2])
        assert snapshot != parser_source.path
        assert snapshot.read_bytes() == parser_source.payload
        raise subprocess.TimeoutExpired(["python", "worker"], timeout=1)

    monkeypatch.setattr(source_slice_builder.subprocess, "run", _timeout)
    with pytest.raises(SourceSliceBuildError, match="wall-clock"):
        _REAL_RUN_PRIVATE_PDF_WORKER(
            root=build_fixture["root"],
            parser_source=build_fixture["parser_source"],
            receipt=build_fixture["receipt"],
            config=build_fixture["parser_config"],
        )
    work_root = (
        build_fixture["root"]
        / "backend/data/golden_graph/source_slice_work"
    )
    assert isinstance(work_root, Path)
    assert list(work_root.iterdir()) == []


def test_verified_chunker_executes_captured_bytes_not_a_mutated_path(
    tmp_path: Path,
) -> None:
    implementation_path = tmp_path / "chunker.py"
    verified_payload = b"def chunk_utf8_text(text, **kwargs):\n    return ('verified',)\n"
    implementation_path.write_bytes(verified_payload)
    source = source_slice_builder._VerifiedToolSource(
        path=implementation_path,
        sha256=_sha256(verified_payload),
        payload=verified_payload,
    )
    implementation_path.write_bytes(
        b"def chunk_utf8_text(text, **kwargs):\n    return ('mutated',)\n"
    )

    chunker = source_slice_builder._load_verified_chunker(source)

    assert chunker("source", max_chunk_utf8_bytes=8, overlap_utf8_bytes=0) == (
        "verified",
    )


def test_project_revision_helper_requires_a_clean_full_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            subprocess.CompletedProcess([], 0, stdout=("b" * 40 + "\n").encode()),
            subprocess.CompletedProcess([], 0, stdout=b""),
        ]
    )
    monkeypatch.setattr(
        source_slice_builder.subprocess,
        "run",
        lambda *_args, **_kwargs: next(responses),
    )
    assert source_slice_builder.verified_clean_git_head(tmp_path) == "b" * 40

    dirty_responses = iter(
        [
            subprocess.CompletedProcess([], 0, stdout=("b" * 40 + "\n").encode()),
            subprocess.CompletedProcess([], 0, stdout=b"?? untracked\n"),
        ]
    )
    monkeypatch.setattr(
        source_slice_builder.subprocess,
        "run",
        lambda *_args, **_kwargs: next(dirty_responses),
    )
    with pytest.raises(SourceSliceBuildError, match="not clean"):
        source_slice_builder.verified_clean_git_head(tmp_path)


def test_build_rejects_git_head_change_between_preflight_and_receipt(
    build_fixture: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    heads = iter(("a" * 40, "c" * 40))
    monkeypatch.setattr(
        source_slice_builder,
        "verified_clean_git_head",
        lambda _root: next(heads),
    )

    with pytest.raises(SourceSliceBuildError, match="HEAD changed"):
        _build(build_fixture)


@pytest.mark.parametrize(
    "drifted_path",
    [
        "backend/golden_graph/protocol.py",
        "backend/app/course_source.py",
    ],
)
def test_builder_import_closure_rejects_stale_dependency(
    monkeypatch: pytest.MonkeyPatch,
    drifted_path: str,
) -> None:
    root = Path(__file__).resolve().parents[2]
    baseline = source_slice_builder._verify_builder_module_currentness(root)
    stale_closure = tuple(
        (path, "0" * 64 if path == drifted_path else digest)
        for path, digest in baseline
    )
    monkeypatch.setattr(
        source_slice_builder,
        "_BUILDER_IMPORT_SOURCE_CLOSURE",
        stale_closure,
    )

    with pytest.raises(SourceSliceBuildError, match=drifted_path):
        source_slice_builder._verify_builder_module_currentness(root)


def test_private_git_boundary_requires_ignored_untracked_git_leaf(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(root)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (root / ".gitignore").write_text("backend/data/\n", encoding="utf-8")
    target = root / "backend/data/golden_graph/test.private.json"

    source_slice_builder._verify_private_git_boundary(root, target)
    target.parent.mkdir(parents=True)
    target.write_text("private", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(root), "add", "-f", "--", "backend/data/golden_graph/test.private.json"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with pytest.raises(SourceSliceBuildError, match="gitignored|tracked"):
        source_slice_builder._verify_private_git_boundary(root, target)

    outside_git = tmp_path / "not-a-repository"
    outside_git.mkdir()
    with pytest.raises(SourceSliceBuildError, match="Git worktree"):
        source_slice_builder._verify_private_git_boundary(
            outside_git,
            outside_git / "backend/data/test.private.json",
        )


@pytest.mark.parametrize("drift_target", ["code", "config"])
def test_bound_code_and_config_drift_are_rejected(
    build_fixture: dict[str, object],
    drift_target: str,
) -> None:
    target = (
        build_fixture["parser_path"]
        if drift_target == "code"
        else build_fixture["parser_config_path"]
    )
    assert isinstance(target, Path)
    target.write_bytes(target.read_bytes() + b"drift\n")

    with pytest.raises(SourceSliceBuildError, match="parser .*mismatch|differs"):
        _build(build_fixture)


def test_manifest_authority_must_still_match_its_leaf(
    build_fixture: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = build_fixture["manifest_authority"]
    assert isinstance(authority, ManifestAuthority)
    monkeypatch.setattr(
        source_slice_builder,
        "load_manifest_authority",
        lambda *_args, **_kwargs: replace(authority, manifest_sha256="6" * 64),
    )
    with pytest.raises(SourceSliceBuildError, match="no longer matches"):
        _build(build_fixture)


def test_authority_issuer_rejects_tampered_product_chunk_text(
    build_fixture: dict[str, object],
) -> None:
    authority = _build(build_fixture)
    tampered = authority.course_source_chunks[0].model_copy(
        update={"text": "tampered private text"}
    )
    chunks = (tampered, *authority.course_source_chunks[1:])

    with pytest.raises(ValueError, match="text hash binding"):
        source_slice_builder._issue_source_slice_authority(
            summary=authority.summary,
            catalog=authority.source_catalog,
            chunk_manifest=authority.chunk_manifest,
            source=authority.course_source,
            chunks=chunks,
            offsets=authority.chunk_offsets,
            verified_asset=authority.verified_asset,
        )


def test_max_chunks_rejects_partial_materialization(
    build_fixture: dict[str, object],
) -> None:
    config = build_fixture["chunker_config"]
    identity = build_fixture["chunker_identity"]
    assert isinstance(config, Utf8ChunkerConfigV1)
    assert isinstance(identity, ToolIdentity)
    limited = config.model_copy(update={"max_chunks": 1})
    payload = canonical_json_bytes(limited)
    path = (
        build_fixture["root"]
        / "backend/golden_graph/artifacts/fixture/chunker-config.json"
    )
    assert isinstance(path, Path)
    path.write_bytes(payload)
    build_fixture["chunker_config"] = limited
    build_fixture["chunker_identity"] = identity.model_copy(
        update={"config_sha256": _sha256(payload)}
    )

    with pytest.raises(SourceSliceBuildError, match="max_chunks"):
        _build(build_fixture)


def test_public_writer_converges_identical_bytes_and_never_overwrites_conflict(
    build_fixture: dict[str, object],
) -> None:
    authority = _build(build_fixture)
    root = build_fixture["root"]
    assert isinstance(root, Path)
    output = root / "backend/golden_graph/artifacts/fixture/catalog.json"
    first_hash = write_public_artifact(
        repository_root=root,
        output_path=output,
        artifact=authority.source_catalog,
    )
    original = output.read_bytes()
    assert write_public_artifact(
        repository_root=root,
        output_path=output,
        artifact=authority.source_catalog,
    ) == first_hash

    conflicting = SemanticSourceCatalog(
        schema_version=1,
        artifact_role="semantic_source_catalog",
        hash_protocol="semantic-id-independent-v1",
        corpus_id="fixture-corpus",
        asset_id="lecture-fixture",
        raw_asset_sha256="7" * 64,
        page_count=1,
        pages=[
            SourceCatalogPage(
                logical_page_id="page-0001",
                page_number=1,
                semantic_page_sha256=_sha256(b"different"),
                semantic_utf8_bytes=len(b"different"),
                status="included",
                reason_code=None,
            )
        ],
    )
    with pytest.raises(SourceSliceBuildError, match="conflict"):
        write_public_artifact(
            repository_root=root,
            output_path=output,
            artifact=conflicting,
        )
    assert output.read_bytes() == original

    second_output = output.with_name("second-catalog.json")
    conflicting_sidecar = second_output.with_suffix(".sha256")
    conflicting_sidecar.write_bytes(b"conflicting sidecar\n")
    with pytest.raises(SourceSliceBuildError, match="conflict"):
        write_public_artifact(
            repository_root=root,
            output_path=second_output,
            artifact=authority.source_catalog,
        )
    assert not second_output.exists()
    assert conflicting_sidecar.read_bytes() == b"conflicting sidecar\n"


def test_private_models_and_receipts_do_not_repr_or_error_source_text(
    build_fixture: dict[str, object],
) -> None:
    authority = _build(build_fixture)
    secret = "PRIVATE-SOURCE-TEXT-DO-NOT-LEAK"
    page = _private_page(1, secret)

    assert secret not in repr(page)
    assert "alpha beta" not in repr(authority)
    with pytest.raises(ValidationError) as captured:
        PrivatePdfPageProjection.model_validate(
            {
                **page.model_dump(mode="python"),
                "semantic_page_sha256": "0" * 64,
            }
        )
    assert secret not in str(captured.value)


def test_private_materialization_round_trip_conflict_tamper_and_boundary(
    build_fixture: dict[str, object],
) -> None:
    authority = _build(build_fixture)
    root = build_fixture["root"]
    assert isinstance(root, Path)
    path = (
        root
        / "backend/data/golden_graph/source_slice_materializations/"
        "fixture-source-slice-v1.private.json"
    )
    protocol = build_fixture["protocol"]
    assert isinstance(protocol, GoldenGraphProtocol)
    written = write_private_source_slice_materialization(
        repository_root=root,
        output_path=path,
        authority=authority,
        protocol=protocol,
    )
    original = path.read_bytes()
    converged = write_private_source_slice_materialization(
        repository_root=root,
        output_path=path,
        authority=authority,
        protocol=protocol,
    )
    loaded = load_private_source_slice_materialization(
        repository_root=root,
        artifact_path=path,
        expected_protocol=protocol,
    )
    assert converged.artifact_sha256 == written.artifact_sha256
    assert loaded.artifact_sha256 == written.artifact_sha256
    assert loaded.materialization.course_source_chunks == (
        authority.course_source_chunks
    )
    assert "alpha beta" not in repr(written)

    wrong_scope = GoldenGraphProtocol.model_validate(
        protocol.model_copy(
            update={
                "page_scope": protocol.page_scope.model_copy(
                    update={
                        "included_pages": (4,),
                        "excluded_pages": (1, 2, 3),
                    }
                )
            }
        ).model_dump(mode="python", exclude_none=False)
    )
    with pytest.raises(SourceSliceBuildError, match="expected protocol"):
        load_private_source_slice_materialization(
            repository_root=root,
            artifact_path=path,
            expected_protocol=wrong_scope,
        )

    wrong_bound_catalog = GoldenGraphProtocol.model_validate(
        protocol.model_copy(
            update={
                "projection": protocol.projection.model_copy(
                    update={"semantic_source_catalog_sha256": "f" * 64}
                )
            }
        ).model_dump(mode="python", exclude_none=False)
    )
    with pytest.raises(SourceSliceBuildError, match="Source catalog"):
        load_private_source_slice_materialization(
            repository_root=root,
            artifact_path=path,
            expected_protocol=wrong_bound_catalog,
        )

    wrong_filename = path.with_name("wrong-protocol.private.json")
    with pytest.raises(SourceSliceBuildError, match="filename"):
        write_private_source_slice_materialization(
            repository_root=root,
            output_path=wrong_filename,
            authority=authority,
            protocol=protocol,
        )

    path.write_bytes(b"existing conflicting private bytes\n")
    with pytest.raises(SourceSliceBuildError, match="conflict"):
        write_private_source_slice_materialization(
            repository_root=root,
            output_path=path,
            authority=authority,
            protocol=protocol,
        )
    assert path.read_bytes() == b"existing conflicting private bytes\n"

    decoded = json.loads(original.decode("utf-8"))
    decoded["course_source_chunks"][0]["text"] = "PRIVATE-TAMPERED-TEXT"
    tampered = canonical_json_bytes(decoded)
    path.write_bytes(tampered)
    digest = _sha256(tampered)
    path.with_suffix(".sha256").write_bytes(
        f"{digest}  {path.name}\n".encode("utf-8")
    )
    with pytest.raises(SourceSliceBuildError) as captured:
        load_private_source_slice_materialization(
            repository_root=root,
            artifact_path=path,
            expected_protocol=protocol,
        )
    assert "PRIVATE-TAMPERED-TEXT" not in str(captured.value)

    with pytest.raises(SourceSliceBuildError, match="gitignored boundary"):
        write_private_source_slice_materialization(
            repository_root=root,
            output_path=root / "outside.private.json",
            authority=authority,
            protocol=protocol,
        )
