from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import threading
import unicodedata
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

import golden_graph.canonical_io as canonical_io_module
import golden_graph.protocol as protocol_module
from golden_graph.bindings import (
    ChunkManifest,
    DependencySnapshot,
    PdfParserConfigV1,
    SemanticSourceCatalog,
    SourceCatalogPage,
    SourceSliceBuildSummary,
    Utf8ChunkerConfigV1,
)
from golden_graph.canonical_io import (
    CanonicalArtifactError,
    MAX_PROTOCOL_BYTES,
    MAX_SIDECAR_BYTES,
    canonical_json_bytes,
    load_hashed_canonical_json,
    read_bounded_regular_bytes,
    write_draft_hashed_canonical_json,
)
from golden_graph.protocol import (
    EXPECTED_V1_TARGETS,
    FrozenProtocolAuthority,
    GoldenGraphProtocolError,
    ManifestAuthority,
    ReplayReadyFrozenProtocolAuthority,
    freeze_protocol,
    load_frozen_protocol,
    load_historical_frozen_protocol,
    load_manifest_authority,
    load_protocol,
    require_current_replay_readiness,
    source_slice_build_spec_sha256,
    validate_protocol_for_freeze,
)
from golden_graph.schemas import GoldenGraphProtocol, MetricId


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
CS336_MANIFEST = (
    BACKEND_ROOT
    / "benchmark_acquisition"
    / "manifests"
    / "cs336-sp25-v1.json"
)
CS336_DRAFT = (
    BACKEND_ROOT
    / "golden_graph"
    / "protocols"
    / "cs336-sp25-lecture-03-v1.draft.json"
)

STATISTICAL_TARGETS = {
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


@pytest.fixture(autouse=True)
def _allow_explicit_non_git_synthetic_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unit fixtures use an explicit seam; production validation fails closed."""

    original = protocol_module._validate_derivation_project_commit

    def validate(**kwargs) -> None:
        repository_root = kwargs["repository_root"]
        commit_sha = kwargs["commit_sha"]
        if not (repository_root / ".git").exists() and commit_sha == "1" * 40:
            return
        original(**kwargs)

    monkeypatch.setattr(
        protocol_module,
        "_validate_derivation_project_commit",
        validate,
    )


def test_cs336_lecture_3_build_ready_draft_waits_for_derived_leaves() -> None:
    protocol = load_protocol(CS336_DRAFT)
    authority = load_manifest_authority(CS336_MANIFEST)

    assert protocol.protocol_status == "draft"
    assert protocol.claim_scope == "authoring_engineering_fixture"
    assert protocol.acquisition.asset_id == "lecture-03-architecture"
    assert protocol.acquisition.partition == "authoring"
    assert protocol.page_scope.asset_page_count == 68
    assert protocol.page_scope.included_pages == tuple(range(1, 69))
    assert protocol.page_scope.excluded_pages == ()
    assert protocol.projection.parser is not None
    assert protocol.projection.chunker is not None
    assert protocol.projection.semantic_source_catalog_sha256 is None
    assert protocol.projection.chunk_manifest_sha256 is None
    assert protocol.projection.source_slice_build_summary_sha256 is None
    assert protocol.review.annotation_guide_sha256 == hashlib.sha256(
        (REPOSITORY_ROOT / protocol.review.annotation_guide_path).read_bytes()
    ).hexdigest()
    assert protocol.release.flagship_automatic_proposal_claim_eligible is False
    assert protocol.release.held_out_claim_eligible is False
    assert (
        protocol.evaluation.future_claim_minimum_samples
        .proposal_gate_closed_world_concepts
        == 50
    )

    with pytest.raises(GoldenGraphProtocolError, match="semantic Source catalog"):
        validate_protocol_for_freeze(
            protocol, authority, repository_root=REPOSITORY_ROOT
        )

    # A serialized status label cannot bypass the authority service.
    bare_frozen = protocol.model_copy(update={"protocol_status": "frozen"})
    assert not isinstance(bare_frozen, FrozenProtocolAuthority)
    with pytest.raises(GoldenGraphProtocolError, match="semantic Source catalog"):
        validate_protocol_for_freeze(
            bare_frozen, authority, repository_root=REPOSITORY_ROOT
        )


def test_protocol_envelopes_require_explicit_fixed_and_nullable_keys(
    tmp_path: Path,
) -> None:
    _authority, payload = _synthetic_protocol(tmp_path)
    required_protocol_keys = (
        ((), "artifact_role"),
        (("acquisition",), "partition"),
        (("page_scope",), "numbering"),
        (("projection",), "parser"),
        (("ontology",), "prerequisite_cycles_forbidden"),
        (("review",), "human_decisions_required"),
        (("evaluation",), "confirmatory"),
        (("evaluation", "future_claim_minimum_samples"), "applies_to"),
        (("evaluation", "confidence_interval"), "zero_support_policy"),
        (("evaluation", "targets", 0), "confidence_bound"),
        (("evaluation", "report_only_metrics", 0), "pass_threshold_registered"),
        (("rights",), "public_source_text_included"),
        (("release",), "held_out_claim_eligible"),
    )
    for parent_path, required_key in required_protocol_keys:
        changed = _deep_copy(payload)
        parent = changed
        for component in parent_path:
            parent = parent[component]
        parent.pop(required_key)
        with pytest.raises(ValidationError, match="Field required"):
            GoldenGraphProtocol.model_validate(changed)

    projection = payload["projection"]
    bound_cases = []
    for model, logical_path, parent_path, required_key in (
        (
            PdfParserConfigV1,
            projection["parser"]["config_path"],
            (),
            "schema_version",
        ),
        (
            Utf8ChunkerConfigV1,
            projection["chunker"]["config_path"],
            (),
            "page_coverage_policy",
        ),
        (
            DependencySnapshot,
            projection["dependency_snapshot_path"],
            (),
            "artifact_role",
        ),
        (
            SemanticSourceCatalog,
            projection["source_catalog_path"],
            (),
            "hash_protocol",
        ),
        (
            SemanticSourceCatalog,
            projection["source_catalog_path"],
            ("pages", 0),
            "reason_code",
        ),
        (
            ChunkManifest,
            projection["chunk_manifest_path"],
            (),
            "artifact_role",
        ),
        (
            ChunkManifest,
            projection["chunk_manifest_path"],
            ("chunks", 0, "locators", 0),
            "offset_unit",
        ),
        (
            SourceSliceBuildSummary,
            projection["source_slice_build_summary_path"],
            (),
            "project_repository_commit_sha",
        ),
        (
            SourceSliceBuildSummary,
            projection["source_slice_build_summary_path"],
            (),
            "build_spec_protocol_sha256",
        ),
    ):
        decoded, _digest_value = load_hashed_canonical_json(tmp_path / logical_path)
        bound_cases.append((model, decoded, parent_path, required_key))

    for model, decoded, parent_path, required_key in bound_cases:
        changed = _deep_copy(decoded)
        parent = changed
        for component in parent_path:
            parent = parent[component]
        parent.pop(required_key)
        with pytest.raises(ValidationError, match="Field required"):
            model.model_validate(changed)


def test_synthetic_binding_protocol_can_receive_protocol_freeze_authority(
    tmp_path: Path,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    protocol = GoldenGraphProtocol.model_validate(payload)

    output_path = _frozen_output_path(tmp_path, protocol.protocol_id)
    receipt = freeze_protocol(
        protocol,
        output_path,
        authority,
        repository_root=tmp_path,
    )

    assert isinstance(receipt, FrozenProtocolAuthority)
    assert receipt.protocol.protocol_status == "frozen"
    assert receipt.protocol.claim_scope == "authoring_engineering_fixture"
    assert receipt.protocol_sha256 == hashlib.sha256(
        canonical_json_bytes(receipt.protocol)
    ).hexdigest()
    assert receipt.acquisition_manifest_sha256 == authority.manifest_sha256
    assert receipt.artifact_path == output_path.resolve()
    assert output_path.exists()
    assert output_path.with_suffix(".sha256").exists()
    assert (
        receipt.protocol.release.counterfactual_fixture_role
        == "schema_trust_smoke_only"
    )
    assert receipt.protocol.release.counterfactual_fixture_is_closed_world_gold is False
    assert isinstance(receipt.protocol.ontology.relation_types, tuple)
    with pytest.raises(AttributeError):
        receipt.protocol.ontology.relation_types.append("mutated")

    replay = freeze_protocol(
        protocol,
        output_path,
        authority,
        repository_root=tmp_path,
    )
    assert replay.protocol_sha256 == receipt.protocol_sha256

    (tmp_path / protocol.projection.parser.implementation_path).write_text(
        "changed",
        encoding="utf-8",
    )
    historical = load_historical_frozen_protocol(
        output_path,
        authority,
        repository_root=tmp_path,
    )
    assert historical.protocol_sha256 == receipt.protocol_sha256
    with pytest.raises(GoldenGraphProtocolError, match="parser implementation"):
        validate_protocol_for_freeze(
            historical.protocol,
            authority,
            repository_root=tmp_path,
        )
    with pytest.raises(GoldenGraphProtocolError, match="parser implementation"):
        load_frozen_protocol(
            output_path,
            authority,
            repository_root=tmp_path,
        )


def test_registered_metric_contracts_are_process_immutable() -> None:
    with pytest.raises(TypeError):
        protocol_module.EXPECTED_V1_TARGETS["citation_precision"] = (
            "gte",
            0.0,
            "proportion",
        )
    with pytest.raises(TypeError):
        protocol_module.EXPECTED_METRIC_PROTOCOLS["citation_precision"] = (
            "changed",
            "changed",
        )


def test_complete_freeze_succeeds_with_every_bound_leaf_git_tracked(
    tmp_path: Path,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    subprocess.run(
        ["git", "init", "--quiet", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "--all"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Protocol Test",
            "-c",
            "user.email=protocol-test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "synthetic derivation revision",
        ],
        check=True,
        capture_output=True,
    )
    project_commit = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _rebind_source_slice_summary(
        tmp_path,
        payload,
        project_repository_commit_sha=project_commit,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "--all"],
        check=True,
        capture_output=True,
    )
    protocol = GoldenGraphProtocol.model_validate(payload)

    receipt = freeze_protocol(
        protocol,
        _frozen_output_path(tmp_path, protocol.protocol_id),
        authority,
        repository_root=tmp_path,
    )

    assert receipt.protocol.protocol_status == "frozen"


def test_historical_authority_survives_later_orchestration_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    subprocess.run(
        ["git", "init", "--quiet", str(tmp_path)],
        check=True,
        capture_output=True,
    )

    def commit(message: str) -> str:
        subprocess.run(
            ["git", "-C", str(tmp_path), "add", "--all"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(tmp_path),
                "-c",
                "user.name=Protocol Test",
                "-c",
                "user.email=protocol-test@example.invalid",
                "commit",
                "--quiet",
                "-m",
                message,
            ],
            check=True,
            capture_output=True,
        )
        return subprocess.run(
            ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    build_commit = commit("synthetic derivation revision")
    _rebind_source_slice_summary(
        tmp_path,
        payload,
        project_repository_commit_sha=build_commit,
    )
    protocol = GoldenGraphProtocol.model_validate(payload)
    output_path = _frozen_output_path(tmp_path, protocol.protocol_id)
    frozen = freeze_protocol(
        protocol,
        output_path,
        authority,
        repository_root=tmp_path,
    )
    publication_commit = commit("publish synthetic frozen protocol")

    forged_authority = ManifestAuthority(
        manifest=replace(authority.manifest, registered_at="2099-01-01"),
        manifest_sha256=authority.manifest_sha256,
        manifest_path=authority.manifest_path,
    )
    with pytest.raises(
        GoldenGraphProtocolError,
        match="recorded manifest blob",
    ):
        load_historical_frozen_protocol(
            output_path,
            forged_authority,
            repository_root=tmp_path,
        )

    import benchmark_acquisition.fetch as fetch_module

    def missing_asset(*_args, **_kwargs):
        raise fetch_module.AcquisitionError("synthetic asset unavailable")

    monkeypatch.setattr(fetch_module, "verify_registered_asset", missing_asset)
    with pytest.raises(GoldenGraphProtocolError, match="asset is not replay-ready"):
        require_current_replay_readiness(
            frozen,
            authority,
            repository_root=tmp_path,
        )
    monkeypatch.setattr(
        fetch_module,
        "verify_registered_asset",
        lambda *_args, **_kwargs: object(),
    )
    replay_ready = require_current_replay_readiness(
        frozen,
        authority,
        repository_root=tmp_path,
    )
    assert isinstance(replay_ready, ReplayReadyFrozenProtocolAuthority)
    assert replay_ready.recorded_project_commit_sha == build_commit
    assert replay_ready.current_project_commit_sha == publication_commit

    unrelated_leaf = tmp_path / "unrelated-evolution.txt"
    unrelated_leaf.write_text("unrelated", encoding="utf-8")
    alternate_commit = commit("create unrelated descendant")
    subprocess.run(
        ["git", "-C", str(tmp_path), "checkout", "--quiet", "--detach", publication_commit],
        check=True,
        capture_output=True,
    )
    original_validate = protocol_module.validate_protocol_for_freeze

    def validate_then_switch_head(*args, **kwargs) -> None:
        original_validate(*args, **kwargs)
        subprocess.run(
            ["git", "-C", str(tmp_path), "checkout", "--quiet", "--detach", alternate_commit],
            check=True,
            capture_output=True,
        )

    monkeypatch.setattr(
        protocol_module,
        "validate_protocol_for_freeze",
        validate_then_switch_head,
    )
    with pytest.raises(GoldenGraphProtocolError, match="HEAD changed"):
        require_current_replay_readiness(
            frozen,
            authority,
            repository_root=tmp_path,
        )
    monkeypatch.setattr(
        protocol_module,
        "validate_protocol_for_freeze",
        original_validate,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "checkout", "--quiet", "--detach", publication_commit],
        check=True,
        capture_output=True,
    )

    dirty_leaf = tmp_path / "untracked-replay-drift.txt"
    dirty_leaf.write_text("dirty", encoding="utf-8")
    with pytest.raises(GoldenGraphProtocolError, match="clean Git worktree"):
        require_current_replay_readiness(
            frozen,
            authority,
            repository_root=tmp_path,
        )
    dirty_leaf.unlink()

    orchestration_path = tmp_path / protocol_module.V1_SOURCE_SLICE_ORCHESTRATION_PATHS[0]
    orchestration_path.write_bytes(orchestration_path.read_bytes() + b"# evolved\n")
    commit("evolve source-slice orchestration")

    historical = load_historical_frozen_protocol(
        output_path,
        authority,
        repository_root=tmp_path,
    )
    assert historical.protocol_sha256 == frozen.protocol_sha256
    with pytest.raises(
        GoldenGraphProtocolError,
        match="differs from the recorded project commit",
    ):
        require_current_replay_readiness(
            historical,
            authority,
            repository_root=tmp_path,
        )


def test_freeze_binds_manifest_path_and_every_registered_asset_identity(
    tmp_path: Path,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    mutations = (
        ("manifest_path", "backend/benchmark_acquisition/manifests/wrong.json", "path"),
        ("manifest_sha256", "e" * 64, "manifest SHA"),
        ("corpus_id", "wrong-corpus", "corpus"),
        ("repository_commit_sha", "e" * 40, "commit"),
        ("asset_id", "wrong-asset", "exactly one"),
        ("raw_sha256", "e" * 64, "raw SHA"),
        ("license_spdx", "CC0-1.0", "license"),
        ("redistribution_allowed", True, "redistribution"),
    )
    for field, value, message in mutations:
        changed = _deep_copy(payload)
        changed["acquisition"][field] = value
        protocol = GoldenGraphProtocol.model_validate(changed)
        with pytest.raises(GoldenGraphProtocolError, match=message):
            validate_protocol_for_freeze(
                protocol, authority, repository_root=tmp_path
            )

    changed = _deep_copy(payload)
    changed["rights"]["license_spdx"] = "CC0-1.0"
    with pytest.raises(GoldenGraphProtocolError, match="Rights license"):
        validate_protocol_for_freeze(
            GoldenGraphProtocol.model_validate(changed),
            authority,
            repository_root=tmp_path,
        )

    changed = _deep_copy(payload)
    changed["rights"]["redistribution_allowed"] = True
    with pytest.raises(GoldenGraphProtocolError, match="Rights redistribution"):
        validate_protocol_for_freeze(
            GoldenGraphProtocol.model_validate(changed),
            authority,
            repository_root=tmp_path,
        )


@pytest.mark.parametrize("partition", ["development", "sealed_transfer"])
def test_freeze_rejects_non_authoring_assets(
    tmp_path: Path,
    partition: str,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    manifest_path = tmp_path / authority.manifest_path
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_payload["assets"][0]["partition"] = partition
    manifest_path.write_text(
        json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    authority = load_manifest_authority(manifest_path, repository_root=tmp_path)
    payload["acquisition"]["partition"] = partition
    payload["acquisition"]["manifest_sha256"] = authority.manifest_sha256

    with pytest.raises(GoldenGraphProtocolError, match="must not use development"):
        validate_protocol_for_freeze(
            GoldenGraphProtocol.model_validate(payload),
            authority,
            repository_root=tmp_path,
        )


@pytest.mark.parametrize(
    ("included", "excluded", "message"),
    [
        ([], [1, 2], "cannot be empty"),
        ([1], [1, 2], "overlap"),
        ([1], [], "classify every page"),
        ([1, 3], [2], "out-of-range"),
    ],
)
def test_freeze_requires_an_exact_page_partition(
    tmp_path: Path,
    included: list[int],
    excluded: list[int],
    message: str,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    payload["page_scope"]["included_pages"] = included
    payload["page_scope"]["excluded_pages"] = excluded
    protocol = GoldenGraphProtocol.model_validate(payload)

    with pytest.raises(GoldenGraphProtocolError, match=message):
        validate_protocol_for_freeze(
            protocol, authority, repository_root=tmp_path
        )


def test_draft_page_scope_must_be_wholly_empty_or_precise(
    tmp_path: Path,
) -> None:
    _authority, payload = _synthetic_protocol(tmp_path)
    payload["page_scope"]["excluded_pages"] = None

    with pytest.raises(ValidationError, match="entirely empty or fully specified"):
        GoldenGraphProtocol.model_validate(payload)


def test_freeze_verifies_tool_config_dependency_and_projection_files(
    tmp_path: Path,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    protocol = GoldenGraphProtocol.model_validate(payload)
    (tmp_path / protocol.projection.parser.implementation_path).write_text(
        "changed",
        encoding="utf-8",
    )

    with pytest.raises(GoldenGraphProtocolError, match="parser implementation"):
        validate_protocol_for_freeze(
            protocol, authority, repository_root=tmp_path
        )


@pytest.mark.parametrize(
    ("field_name", "replacement", "message"),
    [
        ("build_spec_protocol_sha256", "e" * 64, "derivation identity"),
        ("manifest_sha256", "f" * 64, "derivation identity"),
        ("chunk_count", 2, "inventory"),
        (
            "project_repository_commit_sha",
            "0" * 40,
            "commit cannot be a placeholder",
        ),
    ],
)
def test_freeze_cross_checks_source_slice_build_summary(
    tmp_path: Path,
    field_name: str,
    replacement: object,
    message: str,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    summary_path = (
        tmp_path / payload["projection"]["source_slice_build_summary_path"]
    )
    summary, _digest_value = load_hashed_canonical_json(summary_path)
    summary[field_name] = replacement
    payload["projection"]["source_slice_build_summary_sha256"] = (
        write_draft_hashed_canonical_json(summary_path, summary)
    )

    with pytest.raises(GoldenGraphProtocolError, match=message):
        validate_protocol_for_freeze(
            GoldenGraphProtocol.model_validate(payload),
            authority,
            repository_root=tmp_path,
        )


def test_build_spec_hash_normalizes_only_derived_outputs_and_status(
    tmp_path: Path,
) -> None:
    _authority, payload = _synthetic_protocol(tmp_path)
    protocol = GoldenGraphProtocol.model_validate(payload)
    baseline = source_slice_build_spec_sha256(protocol)
    rebound = GoldenGraphProtocol.model_validate(
        protocol.model_copy(
            update={
                "protocol_status": "frozen",
                "projection": protocol.projection.model_copy(
                    update={
                        "semantic_source_catalog_sha256": "a" * 64,
                        "chunk_manifest_sha256": "b" * 64,
                        "source_slice_build_summary_sha256": "c" * 64,
                    }
                ),
            }
        ).model_dump(mode="python", exclude_none=False)
    )
    changed_scope = GoldenGraphProtocol.model_validate(
        protocol.model_copy(
            update={
                "page_scope": protocol.page_scope.model_copy(
                    update={"inclusion_reason": "Different registered scope."}
                )
            }
        ).model_dump(mode="python", exclude_none=False)
    )

    assert source_slice_build_spec_sha256(rebound) == baseline
    assert source_slice_build_spec_sha256(changed_scope) != baseline


def test_derivation_commit_validation_fails_closed_without_git(
    tmp_path: Path,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    summary_path = (
        tmp_path / payload["projection"]["source_slice_build_summary_path"]
    )
    summary, _digest_value = load_hashed_canonical_json(summary_path)
    summary["project_repository_commit_sha"] = "2" * 40
    payload["projection"]["source_slice_build_summary_sha256"] = (
        write_draft_hashed_canonical_json(summary_path, summary)
    )

    with pytest.raises(GoldenGraphProtocolError, match="Git repository"):
        validate_protocol_for_freeze(
            GoldenGraphProtocol.model_validate(payload),
            authority,
            repository_root=tmp_path,
        )


def test_freeze_reloads_manifest_and_annotation_guide_leaves(tmp_path: Path) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    protocol = GoldenGraphProtocol.model_validate(payload)

    manifest_path = tmp_path / authority.manifest_path
    manifest_path.write_bytes(manifest_path.read_bytes() + b"\n")
    with pytest.raises(GoldenGraphProtocolError, match="authority differs"):
        validate_protocol_for_freeze(
            protocol,
            authority,
            repository_root=tmp_path,
        )

    authority, payload = _synthetic_protocol(tmp_path)
    protocol = GoldenGraphProtocol.model_validate(payload)
    guide_path = tmp_path / protocol.review.annotation_guide_path
    guide_path.write_bytes(guide_path.read_bytes() + b"changed\n")
    with pytest.raises(GoldenGraphProtocolError, match="Annotation guide"):
        validate_protocol_for_freeze(
            protocol,
            authority,
            repository_root=tmp_path,
        )

    authority, payload = _synthetic_protocol(tmp_path)
    payload["projection"]["uv_lock_sha256"] = "e" * 64
    with pytest.raises(GoldenGraphProtocolError, match="uv.lock"):
        validate_protocol_for_freeze(
            GoldenGraphProtocol.model_validate(payload),
            authority,
            repository_root=tmp_path,
        )


def test_freeze_requires_exact_production_ontology_order(tmp_path: Path) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    payload["ontology"]["relation_types"] = list(
        reversed(payload["ontology"]["relation_types"])
    )

    with pytest.raises(GoldenGraphProtocolError, match="production Concept graph"):
        validate_protocol_for_freeze(
            GoldenGraphProtocol.model_validate(payload),
            authority,
            repository_root=tmp_path,
        )


def test_freeze_requires_registered_metric_semantics_and_confidence_bounds(
    tmp_path: Path,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    target = next(
        target
        for target in payload["evaluation"]["targets"]
        if target["metric_id"] == "citation_precision"
    )
    target["confidence_bound"] = None
    with pytest.raises(GoldenGraphProtocolError, match="lacks its lower"):
        validate_protocol_for_freeze(
            GoldenGraphProtocol.model_validate(payload),
            authority,
            repository_root=tmp_path,
        )


def test_relation_recall_has_a_complete_report_only_contract(tmp_path: Path) -> None:
    _authority, payload = _synthetic_protocol(tmp_path)
    contract = payload["evaluation"]["report_only_metrics"][0]
    assert contract == {
        "metric_id": "relation_proposal_recall",
        "unit": "proportion",
        "evidence_scope": "future_confirmatory_claim_gate",
        "required_protocol": "gold_bundle_seal",
        "interval_reporting": "two_sided_95_percent_when_cluster_eligible",
        "pass_threshold_registered": False,
    }

    contract["unit"] = "count"
    with pytest.raises(ValidationError):
        GoldenGraphProtocol.model_validate(payload)

    authority, payload = _synthetic_protocol(tmp_path)
    target = next(
        target
        for target in payload["evaluation"]["targets"]
        if target["metric_id"] == "citation_precision"
    )
    target["threshold"] = 0.94
    with pytest.raises(GoldenGraphProtocolError, match="differs for citation_precision"):
        validate_protocol_for_freeze(
            GoldenGraphProtocol.model_validate(payload),
            authority,
            repository_root=tmp_path,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        {"unit": "proportion", "threshold": 1.01},
        {"unit": "count", "threshold": 0.5},
        {"unit": "milliseconds", "threshold": 0.0},
    ],
)
def test_metric_threshold_units_have_safe_ranges(
    tmp_path: Path,
    mutation: dict[str, object],
) -> None:
    _authority, payload = _synthetic_protocol(tmp_path)
    target = payload["evaluation"]["targets"][0]
    target.update(mutation)

    with pytest.raises(ValidationError):
        GoldenGraphProtocol.model_validate(payload)


def test_false_path_embargo_and_unknown_fields_are_rejected(tmp_path: Path) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    payload["release"]["path_evaluation_embargoed_until_gold_freeze"] = False
    with pytest.raises(ValidationError):
        GoldenGraphProtocol.model_validate(payload)

    authority, payload = _synthetic_protocol(tmp_path)
    payload["unexpected"] = "not allowed"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        GoldenGraphProtocol.model_validate(payload)


def test_registered_date_is_a_real_calendar_date(tmp_path: Path) -> None:
    _authority, payload = _synthetic_protocol(tmp_path)
    payload["registered_date"] = "2026-02-30"

    with pytest.raises(ValidationError, match="real ISO calendar date"):
        GoldenGraphProtocol.model_validate(payload)


@pytest.mark.parametrize("forbidden_key", ["text", "page_text", "quote", "exact_quote"])
def test_loader_rejects_forbidden_public_content_fields(
    tmp_path: Path,
    forbidden_key: str,
) -> None:
    _authority, payload = _synthetic_protocol(tmp_path)
    payload["release"]["unexpected_container"] = {forbidden_key: "leaked source"}
    path = tmp_path / "forbidden.json"
    write_draft_hashed_canonical_json(path, payload)

    with pytest.raises(GoldenGraphProtocolError, match="Forbidden public"):
        load_protocol(path)


def test_canonical_io_rejects_noncanonical_duplicate_nan_and_bad_sidecars(
    tmp_path: Path,
) -> None:
    path = tmp_path / "artifact.json"
    digest = write_draft_hashed_canonical_json(path, {"b": 2, "a": "雪"})
    loaded, loaded_digest = load_hashed_canonical_json(path)
    assert loaded == {"a": "雪", "b": 2}
    assert loaded_digest == digest
    assert path.read_bytes() == b'{"a":"\xe9\x9b\xaa","b":2}\n'

    path.write_bytes(b'{"b":2,"a":1}')
    _write_sidecar(path)
    with pytest.raises(CanonicalArtifactError, match="not canonical"):
        load_hashed_canonical_json(path)

    path.write_bytes(b'{"a":1,"a":2}')
    _write_sidecar(path)
    with pytest.raises(CanonicalArtifactError, match="Duplicate"):
        load_hashed_canonical_json(path)

    path.write_bytes(b'{"value":NaN}')
    _write_sidecar(path)
    with pytest.raises(CanonicalArtifactError, match="Non-finite"):
        load_hashed_canonical_json(path)

    path.write_bytes(b'{"a":1}')
    path.with_suffix(".sha256").write_text(
        f"{'0' * 64} *{path.name}\n", encoding="ascii"
    )
    with pytest.raises(CanonicalArtifactError, match="Invalid SHA-256 sidecar"):
        load_hashed_canonical_json(path)

    path.with_suffix(".sha256").write_bytes(
        f"{'0' * 64}  {path.name}\n".encode("ascii")
    )
    with pytest.raises(CanonicalArtifactError, match="SHA-256 mismatch"):
        load_hashed_canonical_json(path)


def test_canonical_io_bounds_payload_and_sidecar_before_parsing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bounded.json"
    path.write_bytes(b"x" * (MAX_PROTOCOL_BYTES + 1))
    path.with_suffix(".sha256").write_bytes(b"0" * 64)
    with pytest.raises(CanonicalArtifactError, match="1.."):
        load_hashed_canonical_json(path)

    path.write_bytes(b"{}\n")
    path.with_suffix(".sha256").write_bytes(b"x" * (MAX_SIDECAR_BYTES + 1))
    with pytest.raises(CanonicalArtifactError, match="1.."):
        load_hashed_canonical_json(path)


def test_draft_writer_cannot_overwrite_a_frozen_protocol_name(
    tmp_path: Path,
) -> None:
    path = tmp_path / "immutable.frozen.json"

    with pytest.raises(CanonicalArtifactError, match="cannot write a frozen"):
        write_draft_hashed_canonical_json(path, {"status": "frozen"})

    assert not path.exists()
    assert not path.with_suffix(".sha256").exists()


def test_canonical_loader_wraps_excessive_json_depth(
    tmp_path: Path,
) -> None:
    path = tmp_path / "too-deep.json"
    payload = b"[" * 2_000 + b"0" + b"]" * 2_000 + b"\n"
    path.write_bytes(payload)
    _write_sidecar(path)

    with pytest.raises(CanonicalArtifactError, match="Invalid UTF-8 JSON"):
        load_hashed_canonical_json(path)


def test_public_config_schema_rejects_source_content_fields() -> None:
    payload = {
        "schema_version": 1,
        "artifact_role": "golden_graph_pdf_parser_config",
        "extraction_mode": "pypdf_plain_text_v1",
        "normalization": "unicode_nfkc_lf_v1",
        "reader_strict": False,
        "ocr_policy": "disabled",
        "blank_detection": "unicode_whitespace_only_v1",
        "page_failure_policy": "record_and_continue_v1",
        "encrypted_pdf_policy": "reject",
        "timeout_scope": "whole_asset_worker_wall_clock_v1",
        "max_pdf_bytes": 1,
        "max_pages": 1,
        "max_page_utf8_bytes": 1,
        "max_total_utf8_bytes": 1,
        "timeout_seconds": 1,
        "source_text": "must never enter a public config",
    }

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PdfParserConfigV1.model_validate(payload)


def test_schema_is_strict_and_does_not_coerce_booleans(tmp_path: Path) -> None:
    _authority, payload = _synthetic_protocol(tmp_path)
    payload["acquisition"]["redistribution_allowed"] = "true"

    with pytest.raises(ValidationError):
        GoldenGraphProtocol.model_validate(payload)


def test_source_catalog_exact_schema_blocks_public_content_fields(
    tmp_path: Path,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    catalog_path = tmp_path / payload["projection"]["source_catalog_path"]
    catalog, _digest_value = load_hashed_canonical_json(catalog_path)
    catalog["pages"][0]["content"] = "source bytes must remain private"
    new_digest = write_draft_hashed_canonical_json(catalog_path, catalog)
    payload["projection"]["semantic_source_catalog_sha256"] = new_digest

    with pytest.raises(GoldenGraphProtocolError, match="Invalid bound protocol artifact"):
        validate_protocol_for_freeze(
            GoldenGraphProtocol.model_validate(payload),
            authority,
            repository_root=tmp_path,
        )


@pytest.mark.parametrize(
    ("status", "reason_code", "semantic_bytes", "semantic_hash", "message"),
    [
        ("blank", None, 0, hashlib.sha256(b"").hexdigest(), "no_semantic_text"),
        (
            "parse_failed",
            None,
            0,
            hashlib.sha256(b"").hexdigest(),
            "parse failure reason_code",
        ),
        ("excluded", None, 0, hashlib.sha256(b"").hexdigest(), "out_of_scope"),
        (
            "blank",
            "no_semantic_text",
            1,
            hashlib.sha256(b"x").hexdigest(),
            "zero semantic bytes",
        ),
    ],
)
def test_source_page_status_requires_a_bounded_reason_and_empty_projection(
    status: str,
    reason_code: str | None,
    semantic_bytes: int,
    semantic_hash: str,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        SourceCatalogPage.model_validate(
            {
                "logical_page_id": "page-0001",
                "page_number": 1,
                "semantic_page_sha256": semantic_hash,
                "semantic_utf8_bytes": semantic_bytes,
                "status": status,
                "reason_code": reason_code,
            }
        )


def test_source_catalog_status_and_chunk_locator_must_match_page_scope(
    tmp_path: Path,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    catalog_path = tmp_path / payload["projection"]["source_catalog_path"]
    catalog, _digest_value = load_hashed_canonical_json(catalog_path)
    catalog["pages"][0]["status"] = "excluded"
    catalog["pages"][0]["reason_code"] = "out_of_scope"
    catalog["pages"][0]["semantic_utf8_bytes"] = 0
    catalog["pages"][0]["semantic_page_sha256"] = _digest(b"")
    new_catalog_digest = write_draft_hashed_canonical_json(catalog_path, catalog)
    payload["projection"]["semantic_source_catalog_sha256"] = new_catalog_digest
    chunk_path = tmp_path / payload["projection"]["chunk_manifest_path"]
    chunks, _chunk_digest = load_hashed_canonical_json(chunk_path)
    chunks["semantic_source_catalog_sha256"] = new_catalog_digest
    payload["projection"]["chunk_manifest_sha256"] = write_draft_hashed_canonical_json(
        chunk_path, chunks
    )
    with pytest.raises(GoldenGraphProtocolError, match="included Source catalog status"):
        validate_protocol_for_freeze(
            GoldenGraphProtocol.model_validate(payload),
            authority,
            repository_root=tmp_path,
        )

    authority, payload = _synthetic_protocol(tmp_path)
    chunk_path = tmp_path / payload["projection"]["chunk_manifest_path"]
    chunks, _chunk_digest = load_hashed_canonical_json(chunk_path)
    chunks["chunks"][0]["locators"][0]["end_offset"] = 101
    payload["projection"]["chunk_manifest_sha256"] = write_draft_hashed_canonical_json(
        chunk_path, chunks
    )
    with pytest.raises(GoldenGraphProtocolError, match="exceeds semantic page length"):
        validate_protocol_for_freeze(
            GoldenGraphProtocol.model_validate(payload),
            authority,
            repository_root=tmp_path,
        )


def test_dependency_snapshot_cannot_lie_about_pypdf_version(tmp_path: Path) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    dependency_path = tmp_path / payload["projection"]["dependency_snapshot_path"]
    dependencies, _digest_value = load_hashed_canonical_json(dependency_path)
    next(item for item in dependencies["packages"] if item["name"] == "pypdf")[
        "version"
    ] = "0.0.0"
    payload["projection"]["dependency_snapshot_sha256"] = (
        write_draft_hashed_canonical_json(dependency_path, dependencies)
    )

    with pytest.raises(GoldenGraphProtocolError, match="dependency snapshot"):
        validate_protocol_for_freeze(
            GoldenGraphProtocol.model_validate(payload),
            authority,
            repository_root=tmp_path,
        )


@pytest.mark.parametrize(
    "lock_body",
    [
        'version = 1\n\n[[package]]\nname = "other"\nversion = "1.0"\n',
        'version = 1\n\n[[package]]\nname = "pypdf"\nversion = "0.0.0"\n',
    ],
)
def test_uv_lock_must_contain_the_declared_parser_version(
    tmp_path: Path,
    lock_body: str,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    lock_path = tmp_path / payload["projection"]["uv_lock_path"]
    lock_bytes = lock_body.encode("utf-8")
    lock_path.write_bytes(lock_bytes)
    payload["projection"]["uv_lock_sha256"] = _digest(lock_bytes)

    with pytest.raises(GoldenGraphProtocolError, match="absent from uv.lock"):
        validate_protocol_for_freeze(
            GoldenGraphProtocol.model_validate(payload),
            authority,
            repository_root=tmp_path,
        )


def test_uv_lock_rejects_boolean_schema_version(tmp_path: Path) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    lock_path = tmp_path / payload["projection"]["uv_lock_path"]
    parser_version = payload["projection"]["parser"]["version"]
    lock_bytes = (
        "version = true\n\n"
        "[[package]]\n"
        'name = "pypdf"\n'
        f'version = "{parser_version}"\n'
    ).encode("utf-8")
    lock_path.write_bytes(lock_bytes)
    payload["projection"]["uv_lock_sha256"] = _digest(lock_bytes)

    with pytest.raises(GoldenGraphProtocolError, match="unsupported structure"):
        validate_protocol_for_freeze(
            GoldenGraphProtocol.model_validate(payload),
            authority,
            repository_root=tmp_path,
        )


def test_acquisition_authority_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    manifest_path = tmp_path / "duplicate-manifest.json"
    original = CS336_MANIFEST.read_text(encoding="utf-8")
    manifest_path.write_text(
        original.replace(
            '"schema_version": 1,',
            '"schema_version": 1, "schema_version": 1,',
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(GoldenGraphProtocolError, match="Duplicate acquisition"):
        load_manifest_authority(manifest_path, repository_root=tmp_path)


def test_dependency_snapshot_rejects_casefold_duplicate_packages() -> None:
    with pytest.raises(ValidationError, match="sorted and unique"):
        DependencySnapshot.model_validate(
            {
                "schema_version": 1,
                "artifact_role": "golden_graph_dependency_snapshot",
                "python_version": platform.python_version(),
                "unicode_database_version": unicodedata.unidata_version,
                "packages": [
                    {"name": "pypdf", "version": "6.14.2"},
                    {"name": "PyPDF", "version": "999.0.0"},
                ],
            }
        )


def test_dependency_snapshot_contains_only_bound_tool_distributions(
    tmp_path: Path,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    dependency_path = tmp_path / payload["projection"]["dependency_snapshot_path"]
    dependencies, _digest_value = load_hashed_canonical_json(dependency_path)
    dependencies["packages"].insert(
        0,
        {"name": "extra-runtime", "version": "1.0.0"},
    )
    payload["projection"]["dependency_snapshot_sha256"] = (
        write_draft_hashed_canonical_json(dependency_path, dependencies)
    )

    with pytest.raises(GoldenGraphProtocolError, match="exactly the v1 parser"):
        validate_protocol_for_freeze(
            GoldenGraphProtocol.model_validate(payload),
            authority,
            repository_root=tmp_path,
        )


def test_chunk_content_hash_can_repeat_at_distinct_occurrences(tmp_path: Path) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    catalog_path = tmp_path / payload["projection"]["source_catalog_path"]
    catalog, _catalog_digest = load_hashed_canonical_json(catalog_path)
    catalog["pages"][0]["semantic_utf8_bytes"] = 190
    catalog_digest = write_draft_hashed_canonical_json(catalog_path, catalog)
    payload["projection"]["semantic_source_catalog_sha256"] = catalog_digest

    chunk_path = tmp_path / payload["projection"]["chunk_manifest_path"]
    chunks, _digest_value = load_hashed_canonical_json(chunk_path)
    chunks["semantic_source_catalog_sha256"] = catalog_digest
    repeated = _deep_copy(chunks["chunks"][0])
    repeated["ordinal"] = 1
    repeated["locators"][0]["start_offset"] = 90
    repeated["locators"][0]["end_offset"] = 190
    chunks["chunks"].append(repeated)

    parsed = ChunkManifest.model_validate(chunks)
    assert len(parsed.chunks) == 2
    payload["projection"]["chunk_manifest_sha256"] = (
        write_draft_hashed_canonical_json(chunk_path, chunks)
    )
    _rebind_source_slice_summary(tmp_path, payload)
    validate_protocol_for_freeze(
        GoldenGraphProtocol.model_validate(payload),
        authority,
        repository_root=tmp_path,
    )

    duplicate_occurrence = _deep_copy(chunks)
    duplicate_occurrence["chunks"][1]["locators"] = _deep_copy(
        duplicate_occurrence["chunks"][0]["locators"]
    )
    with pytest.raises(ValidationError, match="occurrences must be unique"):
        ChunkManifest.model_validate(duplicate_occurrence)

    conflicting_hash = _deep_copy(chunks)
    conflicting_hash["chunks"][1]["locators"] = _deep_copy(
        conflicting_hash["chunks"][0]["locators"]
    )
    conflicting_hash["chunks"][1]["semantic_chunk_sha256"] = _digest(b"different")
    with pytest.raises(ValidationError, match="occurrences must be unique"):
        ChunkManifest.model_validate(conflicting_hash)


@pytest.mark.parametrize(
    ("intervals", "message"),
    [
        ([(1, 100)], "start at byte zero"),
        ([(0, 50), (51, 100)], "contains a gap"),
        ([(0, 99)], "omits the page tail"),
        ([(0, 60), (40, 100)], "overlap exceeds"),
        ([(0, 60), (50, 100)], None),
    ],
)
def test_chunk_union_must_cover_every_included_page_byte(
    tmp_path: Path,
    intervals: list[tuple[int, int]],
    message: str | None,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    chunk_path = tmp_path / payload["projection"]["chunk_manifest_path"]
    chunks, _digest_value = load_hashed_canonical_json(chunk_path)
    chunks["chunks"] = [
        {
            "ordinal": ordinal,
            "semantic_chunk_sha256": _digest(f"chunk-{ordinal}".encode("ascii")),
            "locators": [
                {
                    "logical_page_id": "page-0001",
                    "start_offset": start_offset,
                    "end_offset": end_offset,
                    "offset_unit": "utf8_bytes",
                }
            ],
        }
        for ordinal, (start_offset, end_offset) in enumerate(intervals)
    ]
    payload["projection"]["chunk_manifest_sha256"] = (
        write_draft_hashed_canonical_json(chunk_path, chunks)
    )
    _rebind_source_slice_summary(tmp_path, payload)
    protocol = GoldenGraphProtocol.model_validate(payload)

    if message is None:
        validate_protocol_for_freeze(
            protocol,
            authority,
            repository_root=tmp_path,
        )
    else:
        with pytest.raises(GoldenGraphProtocolError, match=message):
            validate_protocol_for_freeze(
                protocol,
                authority,
                repository_root=tmp_path,
            )


def test_public_binding_cannot_alias_a_private_hardlink(tmp_path: Path) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    catalog_path = tmp_path / payload["projection"]["source_catalog_path"]
    private_alias = tmp_path / "backend" / "data" / "catalog-alias.json"
    private_alias.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(catalog_path, private_alias)
    except OSError as exc:
        pytest.skip(f"filesystem does not support hard links: {exc}")

    with pytest.raises(GoldenGraphProtocolError, match="cannot be hard-linked"):
        validate_protocol_for_freeze(
            GoldenGraphProtocol.model_validate(payload),
            authority,
            repository_root=tmp_path,
        )


def test_bound_repository_authority_leaf_must_be_git_tracked(
    tmp_path: Path,
) -> None:
    subprocess.run(
        ["git", "init", "--quiet", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    leaf = tmp_path / "authority.json"
    leaf.write_bytes(b"{}\n")

    with pytest.raises(GoldenGraphProtocolError, match="must be tracked"):
        protocol_module._resolve_bound_repository_file(
            tmp_path,
            "authority.json",
            require_tracked=True,
        )

    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "--", "authority.json"],
        check=True,
        capture_output=True,
    )
    assert protocol_module._resolve_bound_repository_file(
        tmp_path,
        "authority.json",
        require_tracked=True,
    ) == leaf


def test_git_tracked_check_treats_metacharacters_as_literal_path(
    tmp_path: Path,
) -> None:
    subprocess.run(
        ["git", "init", "--quiet", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    tracked = artifacts / "authority.json"
    tracked.write_bytes(b"{}\n")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "--", "artifacts/authority.json"],
        check=True,
        capture_output=True,
    )
    untracked_pathspec_lookalike = artifacts / "authority[.]json"
    untracked_pathspec_lookalike.write_bytes(b"{}\n")

    with pytest.raises(GoldenGraphProtocolError, match="must be tracked"):
        protocol_module._resolve_bound_repository_file(
            tmp_path,
            "artifacts/authority[.]json",
            require_tracked=True,
        )


def test_git_tracked_check_ignores_redirecting_git_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subprocess.run(
        ["git", "init", "--quiet", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    leaf = tmp_path / "authority.json"
    leaf.write_bytes(b"{}\n")
    alternate_index = tmp_path / "alternate-index"
    redirected_environment = os.environ.copy()
    redirected_environment["GIT_INDEX_FILE"] = str(alternate_index)
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "--", "authority.json"],
        check=True,
        capture_output=True,
        env=redirected_environment,
    )
    monkeypatch.setenv("GIT_INDEX_FILE", str(alternate_index))

    with pytest.raises(GoldenGraphProtocolError, match="must be tracked"):
        protocol_module._resolve_bound_repository_file(
            tmp_path,
            "authority.json",
            require_tracked=True,
        )


def test_bound_repository_path_requires_exact_portable_spelling(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "Artifacts"
    directory.mkdir()
    (directory / "authority.json").write_bytes(b"{}\n")

    with pytest.raises(GoldenGraphProtocolError, match="exact portable spelling"):
        protocol_module._resolve_bound_repository_file(
            tmp_path,
            "artifacts/authority.json",
            require_tracked=False,
        )


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "artifacts/catalog.json:hidden",
        "artifacts/CON.json",
        "artifacts/trailing-dot.",
    ],
)
def test_protocol_paths_are_cross_platform_portable(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    _authority, payload = _synthetic_protocol(tmp_path)
    payload["projection"]["source_catalog_path"] = unsafe_path

    with pytest.raises(ValidationError, match="cross-platform"):
        GoldenGraphProtocol.model_validate(payload)


def test_frozen_redacted_leaves_cannot_live_under_gitignored_data(
    tmp_path: Path,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    payload["projection"]["source_catalog_path"] = (
        "backend/data/private-source-catalog.json"
    )

    with pytest.raises(GoldenGraphProtocolError, match="Redacted evaluation leaves"):
        validate_protocol_for_freeze(
            GoldenGraphProtocol.model_validate(payload),
            authority,
            repository_root=tmp_path,
        )

    authority, payload = _synthetic_protocol(tmp_path)
    payload["projection"]["parser"]["config_path"] = "backend/data/parser.json"
    with pytest.raises(GoldenGraphProtocolError, match="tracked evaluation artifact"):
        validate_protocol_for_freeze(
            GoldenGraphProtocol.model_validate(payload),
            authority,
            repository_root=tmp_path,
        )


def test_freeze_cleans_up_artifact_when_durable_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    protocol = GoldenGraphProtocol.model_validate(payload)
    output_path = _frozen_output_path(tmp_path, protocol.protocol_id)

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("simulated durable-write failure")

    monkeypatch.setattr(protocol_module.os, "fsync", fail_fsync)
    with pytest.raises(GoldenGraphProtocolError, match="Cannot publish"):
        freeze_protocol(
            protocol,
            output_path,
            authority,
            repository_root=tmp_path,
        )

    assert not output_path.exists()
    assert not output_path.with_suffix(".sha256").exists()


def test_bounded_reader_rejects_same_size_write_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "changing.bin"
    size = 1024 * 1024 + 64
    path.write_bytes(b"a" * size)
    original_read = canonical_io_module.os.read
    read_count = 0

    def read_then_rewrite(descriptor: int, requested: int) -> bytes:
        nonlocal read_count
        chunk = original_read(descriptor, requested)
        read_count += 1
        if read_count == 1:
            path.write_bytes(b"b" * size)
        return chunk

    monkeypatch.setattr(canonical_io_module.os, "read", read_then_rewrite)

    with pytest.raises(CanonicalArtifactError, match="must contain"):
        read_bounded_regular_bytes(
            path,
            max_bytes=size,
            label="changing fixture",
        )


def test_freeze_never_exposes_partial_artifact_when_atomic_publish_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    protocol = GoldenGraphProtocol.model_validate(payload)
    output_path = _frozen_output_path(tmp_path, protocol.protocol_id)

    def fail_link(_source: Path, _destination: Path) -> None:
        raise OSError("simulated process interruption before atomic publish")

    monkeypatch.setattr(protocol_module.os, "link", fail_link)
    with pytest.raises(GoldenGraphProtocolError, match="Cannot publish"):
        freeze_protocol(
            protocol,
            output_path,
            authority,
            repository_root=tmp_path,
        )

    assert not output_path.exists()
    assert not output_path.with_suffix(".sha256").exists()
    assert not list(output_path.parent.glob("*.publish-tmp"))


def test_concurrent_identical_freezes_converge_on_one_immutable_identity(
    tmp_path: Path,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    protocol = GoldenGraphProtocol.model_validate(payload)
    output_path = _frozen_output_path(tmp_path, protocol.protocol_id)

    def publish() -> FrozenProtocolAuthority:
        return freeze_protocol(
            protocol,
            output_path,
            authority,
            repository_root=tmp_path,
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        receipts = list(executor.map(lambda _index: publish(), range(4)))

    assert len({receipt.protocol_sha256 for receipt in receipts}) == 1
    assert not list(output_path.parent.glob("*.publish-tmp"))


def test_concurrent_conflicting_freezes_never_overwrite_the_winner(
    tmp_path: Path,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    changed = _deep_copy(payload)
    changed["page_scope"]["inclusion_reason"] = "A competing immutable meaning."
    _fork_source_slice_summary(
        tmp_path,
        changed,
        "source-slice-build-summary-competing.json",
    )
    protocols = [
        GoldenGraphProtocol.model_validate(payload),
        GoldenGraphProtocol.model_validate(changed),
    ]
    output_path = _frozen_output_path(tmp_path, protocols[0].protocol_id)

    def publish(protocol: GoldenGraphProtocol) -> FrozenProtocolAuthority | Exception:
        try:
            return freeze_protocol(
                protocol,
                output_path,
                authority,
                repository_root=tmp_path,
            )
        except Exception as exc:  # Asserted precisely below.
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(publish, protocols))

    receipts = [item for item in outcomes if isinstance(item, FrozenProtocolAuthority)]
    failures = [item for item in outcomes if isinstance(item, Exception)]
    assert len(receipts) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], GoldenGraphProtocolError)
    assert "conflicts" in str(failures[0])
    persisted = load_frozen_protocol(
        output_path,
        authority,
        repository_root=tmp_path,
    )
    assert persisted.protocol_sha256 == receipts[0].protocol_sha256
    assert not list(output_path.parent.glob("*.publish-tmp"))


def test_freeze_repairs_exact_crash_remnant_and_rejects_conflicting_identity(
    tmp_path: Path,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    protocol = GoldenGraphProtocol.model_validate(payload)
    output_path = _frozen_output_path(tmp_path, protocol.protocol_id)
    frozen = protocol.model_copy(update={"protocol_status": "frozen"})
    output_path.write_bytes(canonical_json_bytes(frozen))

    receipt = freeze_protocol(
        protocol,
        output_path,
        authority,
        repository_root=tmp_path,
    )
    assert output_path.with_suffix(".sha256").exists()
    assert receipt.protocol_sha256 == hashlib.sha256(
        canonical_json_bytes(frozen)
    ).hexdigest()

    changed = _deep_copy(payload)
    changed["page_scope"]["inclusion_reason"] = "A different immutable meaning."
    _fork_source_slice_summary(
        tmp_path,
        changed,
        "source-slice-build-summary-different.json",
    )
    with pytest.raises(GoldenGraphProtocolError, match="conflicts"):
        freeze_protocol(
            GoldenGraphProtocol.model_validate(changed),
            output_path,
            authority,
            repository_root=tmp_path,
        )


def test_invalid_model_copy_cannot_publish_or_reserve_identity(
    tmp_path: Path,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    valid = GoldenGraphProtocol.model_validate(payload)
    invalid = valid.model_copy(update={"registered_date": "not-a-date"})
    output_path = _frozen_output_path(tmp_path, valid.protocol_id)

    with pytest.raises(GoldenGraphProtocolError, match="schema validation"):
        freeze_protocol(
            invalid,
            output_path,
            authority,
            repository_root=tmp_path,
        )
    assert not output_path.exists()
    assert not output_path.with_suffix(".sha256").exists()

    receipt = freeze_protocol(
        valid,
        output_path,
        authority,
        repository_root=tmp_path,
    )
    assert receipt.protocol.protocol_id == valid.protocol_id


def test_freeze_repairs_an_exact_sidecar_only_crash_remnant(
    tmp_path: Path,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    protocol = GoldenGraphProtocol.model_validate(payload)
    output_path = _frozen_output_path(tmp_path, protocol.protocol_id)
    frozen_payload = canonical_json_bytes(
        protocol.model_copy(update={"protocol_status": "frozen"})
    )
    digest = hashlib.sha256(frozen_payload).hexdigest()
    output_path.with_suffix(".sha256").write_bytes(
        f"{digest}  {output_path.name}\n".encode("ascii")
    )

    receipt = freeze_protocol(
        protocol,
        output_path,
        authority,
        repository_root=tmp_path,
    )

    assert output_path.read_bytes() == frozen_payload
    assert receipt.protocol_sha256 == digest


@pytest.mark.parametrize("initial_remnant", ["json", "sidecar"])
def test_recovery_converges_during_publish_link_cleanup_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    initial_remnant: str,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    protocol = GoldenGraphProtocol.model_validate(payload)
    output_path = _frozen_output_path(tmp_path, protocol.protocol_id)
    frozen_payload = canonical_json_bytes(
        protocol.model_copy(update={"protocol_status": "frozen"})
    )
    digest = hashlib.sha256(frozen_payload).hexdigest()
    sidecar_path = output_path.with_suffix(".sha256")
    sidecar_payload = f"{digest}  {output_path.name}\n".encode("ascii")
    if initial_remnant == "json":
        output_path.write_bytes(frozen_payload)
    else:
        sidecar_path.write_bytes(sidecar_payload)

    cleanup_threads: list[threading.Timer] = []

    def lose_to_identical_active_publisher(path: Path, body: bytes) -> None:
        temporary = path.parent / f".{path.name}.winner.publish-tmp"
        temporary.write_bytes(body)
        os.link(temporary, path)
        cleanup = threading.Timer(0.01, temporary.unlink)
        cleanup.start()
        cleanup_threads.append(cleanup)
        raise GoldenGraphProtocolError("simulated identical publication winner")

    monkeypatch.setattr(
        protocol_module,
        "_write_exclusive_durable",
        lose_to_identical_active_publisher,
    )
    receipt = freeze_protocol(
        protocol,
        output_path,
        authority,
        repository_root=tmp_path,
    )
    for cleanup in cleanup_threads:
        cleanup.join(timeout=1)

    assert receipt.protocol_sha256 == digest
    assert output_path.read_bytes() == frozen_payload
    assert sidecar_path.read_bytes() == sidecar_payload


def test_leaf_drift_after_publish_withholds_authority_but_exact_retry_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    protocol = GoldenGraphProtocol.model_validate(payload)
    output_path = _frozen_output_path(tmp_path, protocol.protocol_id)
    guide_path = tmp_path / protocol.review.annotation_guide_path
    original_guide = guide_path.read_bytes()
    original_publish = protocol_module._write_exclusive_durable
    publication_count = 0

    def publish_then_drift(path: Path, body: bytes) -> None:
        nonlocal publication_count
        original_publish(path, body)
        publication_count += 1
        if publication_count == 1:
            guide_path.write_bytes(original_guide + b"drift")

    monkeypatch.setattr(
        protocol_module,
        "_write_exclusive_durable",
        publish_then_drift,
    )
    with pytest.raises(GoldenGraphProtocolError, match="Annotation guide"):
        freeze_protocol(
            protocol,
            output_path,
            authority,
            repository_root=tmp_path,
        )
    assert output_path.exists()
    assert output_path.with_suffix(".sha256").exists()

    guide_path.write_bytes(original_guide)
    monkeypatch.setattr(
        protocol_module,
        "_write_exclusive_durable",
        original_publish,
    )
    receipt = freeze_protocol(
        protocol,
        output_path,
        authority,
        repository_root=tmp_path,
    )
    assert receipt.protocol.protocol_status == "frozen"


def test_freeze_path_is_derived_from_protocol_identity(tmp_path: Path) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    protocol = GoldenGraphProtocol.model_validate(payload)
    _frozen_output_path(tmp_path, protocol.protocol_id)

    with pytest.raises(GoldenGraphProtocolError, match="path must be"):
        freeze_protocol(
            protocol,
            tmp_path / "wrong-name.json",
            authority,
            repository_root=tmp_path,
        )


def test_frozen_protocol_filename_comparison_is_case_sensitive(tmp_path: Path) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    protocol = GoldenGraphProtocol.model_validate(payload)
    canonical = _frozen_output_path(tmp_path, protocol.protocol_id)
    wrong_case = canonical.with_name(
        f"{protocol.protocol_id.upper()}.frozen.json"
    )

    with pytest.raises(GoldenGraphProtocolError, match="path must be"):
        freeze_protocol(
            protocol,
            wrong_case,
            authority,
            repository_root=tmp_path,
        )
    assert not wrong_case.exists()
    assert not wrong_case.with_suffix(".sha256").exists()


@pytest.mark.parametrize("linked_leaf", ["artifact", "sidecar"])
def test_frozen_protocol_authority_rejects_hardlinked_leaves(
    tmp_path: Path,
    linked_leaf: str,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    protocol = GoldenGraphProtocol.model_validate(payload)
    output_path = _frozen_output_path(tmp_path, protocol.protocol_id)
    freeze_protocol(
        protocol,
        output_path,
        authority,
        repository_root=tmp_path,
    )
    leaf = output_path if linked_leaf == "artifact" else output_path.with_suffix(
        ".sha256"
    )
    private_alias = leaf.parent / f".{leaf.name}.external.publish-tmp"
    try:
        os.link(leaf, private_alias)
    except OSError as exc:
        pytest.skip(f"filesystem does not support hard links: {exc}")

    with pytest.raises(GoldenGraphProtocolError, match="hard-linked"):
        load_frozen_protocol(
            output_path,
            authority,
            repository_root=tmp_path,
        )
    assert private_alias.exists()


def test_alias_directory_pointing_to_canonical_protocols_is_rejected(
    tmp_path: Path,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    protocol = GoldenGraphProtocol.model_validate(payload)
    canonical = _frozen_output_path(tmp_path, protocol.protocol_id)
    alias_directory = tmp_path / "protocol-alias"
    try:
        alias_directory.symlink_to(canonical.parent, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")

    with pytest.raises(
        GoldenGraphProtocolError,
        match="path must be|canonical protocol directory",
    ):
        freeze_protocol(
            protocol,
            alias_directory / canonical.name,
            authority,
            repository_root=tmp_path,
        )


def test_frozen_protocol_directory_cannot_escape_through_a_link(
    tmp_path: Path,
) -> None:
    authority, payload = _synthetic_protocol(tmp_path)
    protocol = GoldenGraphProtocol.model_validate(payload)
    protocols_path = tmp_path / "backend" / "golden_graph" / "protocols"
    protocols_path.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        protocols_path.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")
    output_path = protocols_path / f"{protocol.protocol_id}.frozen.json"

    with pytest.raises(GoldenGraphProtocolError, match="directory cannot use links"):
        freeze_protocol(
            protocol,
            output_path,
            authority,
            repository_root=tmp_path,
        )


def _synthetic_protocol(
    repository_root: Path,
) -> tuple[ManifestAuthority, dict[str, object]]:
    manifest_path = repository_root / "testdata" / "cs336-sp25-v1.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_bytes(CS336_MANIFEST.read_bytes())
    authority = load_manifest_authority(
        manifest_path,
        repository_root=repository_root,
    )
    manifest = authority.manifest
    asset = manifest.assets[0]
    pypdf_version = importlib.metadata.version("pypdf")
    parser_config = {
        "schema_version": 1,
        "artifact_role": "golden_graph_pdf_parser_config",
        "extraction_mode": "pypdf_plain_text_v1",
        "normalization": "unicode_nfkc_lf_v1",
        "reader_strict": False,
        "ocr_policy": "disabled",
        "blank_detection": "unicode_whitespace_only_v1",
        "page_failure_policy": "record_and_continue_v1",
        "encrypted_pdf_policy": "reject",
        "timeout_scope": "whole_asset_worker_wall_clock_v1",
        "max_pdf_bytes": 64 * 1024 * 1024,
        "max_pages": 10_000,
        "max_page_utf8_bytes": 64 * 1024 * 1024,
        "max_total_utf8_bytes": 512 * 1024 * 1024,
        "timeout_seconds": 300,
    }
    chunker_config = {
        "schema_version": 1,
        "artifact_role": "golden_graph_utf8_chunker_config",
        "algorithm": "utf8_sliding_window_v1",
        "utf8_boundary_policy": "codepoint_safe_max_end_forward_start_v1",
        "max_chunk_utf8_bytes": 200,
        "overlap_utf8_bytes": 10,
        "max_chunks": 1_000,
        "cross_page_chunks": False,
        "page_coverage_policy": "complete_union_overlap_allowed-v1",
    }

    files = {
        "backend/uv.lock": (
            "version = 1\n\n"
            "[[package]]\n"
            'name = "pypdf"\n'
            f'version = "{pypdf_version}"\n'
        ).encode("utf-8"),
        "backend/golden_graph/artifacts/synthetic/parser.py": b"def parse(value): return value\n",
        "backend/golden_graph/artifacts/synthetic/parser-config.json": (
            canonical_json_bytes(parser_config)
        ),
        "backend/golden_graph/artifacts/synthetic/chunker.py": (
            b"def chunk(value): return [value]\n"
        ),
        "backend/golden_graph/artifacts/synthetic/chunker-config.json": (
            canonical_json_bytes(chunker_config)
        ),
        "backend/golden_graph/source_slice_builder.py": b"# synthetic builder\n",
        "backend/golden_graph/source_slice_command.py": b"# synthetic command\n",
        "docs/graph-annotation-protocol.md": b"# Synthetic annotation guide\n",
    }
    for logical_path in protocol_module.V1_SOURCE_SLICE_ORCHESTRATION_PATHS:
        files.setdefault(
            logical_path,
            f"# synthetic orchestration leaf: {logical_path}\n".encode("utf-8"),
        )
    for logical_path, body in files.items():
        path = repository_root / logical_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    for config_path in (
        "backend/golden_graph/artifacts/synthetic/parser-config.json",
        "backend/golden_graph/artifacts/synthetic/chunker-config.json",
    ):
        _write_sidecar(repository_root / config_path)

    parser_tool = _tool_payload(
        files,
        "parser",
        pypdf_version,
        distribution_name="pypdf",
    )
    chunker_tool = _tool_payload(
        files,
        "chunker",
        "repository-v1",
        distribution_name="project-source",
    )
    dependency_payload = {
        "schema_version": 1,
        "artifact_role": "golden_graph_dependency_snapshot",
        "python_version": platform.python_version(),
        "unicode_database_version": unicodedata.unidata_version,
        "packages": [
            {"name": "project-source", "version": "repository-v1"},
            {"name": "pypdf", "version": pypdf_version},
        ],
    }
    source_catalog_payload = {
        "schema_version": 1,
        "artifact_role": "semantic_source_catalog",
        "hash_protocol": "semantic-id-independent-v1",
        "corpus_id": manifest.corpus_id,
        "asset_id": asset.asset_id,
        "raw_asset_sha256": asset.sha256,
        "page_count": 2,
        "pages": [
            {
                "logical_page_id": "page-0001",
                "page_number": 1,
                "semantic_page_sha256": _digest(b"synthetic page one"),
                "semantic_utf8_bytes": 100,
                "status": "included",
                "reason_code": None,
            },
            {
                "logical_page_id": "page-0002",
                "page_number": 2,
                "semantic_page_sha256": _digest(b""),
                "semantic_utf8_bytes": 0,
                "status": "excluded",
                "reason_code": "out_of_scope",
            },
        ],
    }
    dependency_digest = write_draft_hashed_canonical_json(
        repository_root
        / "backend"
        / "golden_graph"
        / "artifacts"
        / "synthetic"
        / "dependencies.json",
        dependency_payload,
    )
    catalog_digest = write_draft_hashed_canonical_json(
        repository_root
        / "backend"
        / "golden_graph"
        / "artifacts"
        / "synthetic"
        / "source-catalog.json",
        source_catalog_payload,
    )
    chunk_payload = {
        "schema_version": 1,
        "artifact_role": "semantic_chunk_manifest",
        "corpus_id": manifest.corpus_id,
        "asset_id": asset.asset_id,
        "raw_asset_sha256": asset.sha256,
        "semantic_source_catalog_sha256": catalog_digest,
        "parser": parser_tool,
        "chunker": chunker_tool,
        "page_coverage_policy": "complete_union_overlap_allowed-v1",
        "chunks": [
            {
                "ordinal": 0,
                "semantic_chunk_sha256": _digest(b"synthetic chunk"),
                "locators": [
                    {
                        "logical_page_id": "page-0001",
                        "start_offset": 0,
                        "end_offset": 100,
                        "offset_unit": "utf8_bytes",
                    }
                ],
            }
        ],
    }
    chunk_digest = write_draft_hashed_canonical_json(
        repository_root
        / "backend"
        / "golden_graph"
        / "artifacts"
        / "synthetic"
        / "chunks.json",
        chunk_payload,
    )
    summary_payload = {
        "schema_version": 1,
        "artifact_role": "golden_graph_source_slice_build_summary",
        "project_repository_commit_sha": "1" * 40,
        "build_spec_protocol_sha256": "0" * 64,
        "corpus_id": manifest.corpus_id,
        "asset_id": asset.asset_id,
        "manifest_sha256": authority.manifest_sha256,
        "raw_asset_sha256": asset.sha256,
        "parser_config_sha256": parser_tool["config_sha256"],
        "chunker_config_sha256": chunker_tool["config_sha256"],
        "parser_implementation_sha256": parser_tool["implementation_sha256"],
        "chunker_implementation_sha256": chunker_tool["implementation_sha256"],
        "dependency_snapshot_sha256": dependency_digest,
        "uv_lock_sha256": _digest(files["backend/uv.lock"]),
        "semantic_source_catalog_sha256": catalog_digest,
        "chunk_manifest_sha256": chunk_digest,
        "page_count": 2,
        "included_page_count": 1,
        "excluded_page_count": 1,
        "blank_page_count": 0,
        "parse_failed_page_count": 0,
        "chunk_count": 1,
    }
    summary_digest = write_draft_hashed_canonical_json(
        repository_root
        / "backend"
        / "golden_graph"
        / "artifacts"
        / "synthetic"
        / "source-slice-build-summary.json",
        summary_payload,
    )
    payload: dict[str, object] = {
        "schema_version": 1,
        "artifact_role": "golden_graph_protocol",
        "claim_scope": "authoring_engineering_fixture",
        "protocol_id": "synthetic-binding-golden-graph-v1",
        "protocol_status": "draft",
        "registered_date": "2026-08-09",
        "acquisition": {
            "manifest_path": authority.manifest_path,
            "manifest_sha256": authority.manifest_sha256,
            "corpus_id": manifest.corpus_id,
            "repository_commit_sha": manifest.commit_sha,
            "asset_id": asset.asset_id,
            "partition": asset.partition,
            "raw_sha256": asset.sha256,
            "license_spdx": asset.license_spdx,
            "redistribution_allowed": asset.redistribution_allowed,
        },
        "page_scope": {
            "numbering": "pdf_page_1_based",
            "asset_page_count": 2,
            "included_pages": [1],
            "excluded_pages": [2],
            "inclusion_reason": "Page 1 contains the bounded engineering fixture.",
            "exclusion_reason": "Page 2 is reserved as an explicit negative scope example.",
        },
        "projection": {
            "parser": parser_tool,
            "chunker": chunker_tool,
            "dependency_snapshot_path": (
                "backend/golden_graph/artifacts/synthetic/dependencies.json"
            ),
            "dependency_snapshot_sha256": dependency_digest,
            "uv_lock_path": "backend/uv.lock",
            "uv_lock_sha256": _digest(files["backend/uv.lock"]),
            "source_catalog_path": "backend/golden_graph/artifacts/synthetic/source-catalog.json",
            "source_catalog_hash_protocol": "semantic-id-independent-v1",
            "semantic_source_catalog_sha256": catalog_digest,
            "chunk_manifest_path": "backend/golden_graph/artifacts/synthetic/chunks.json",
            "chunk_manifest_sha256": chunk_digest,
            "source_slice_build_summary_path": (
                "backend/golden_graph/artifacts/synthetic/"
                "source-slice-build-summary.json"
            ),
            "source_slice_build_summary_sha256": summary_digest,
        },
        "ontology": {
            "relation_types": [
                "prerequisite",
                "part_of",
                "example_of",
                "related",
                "contrast_with",
            ],
            "support_bases": ["source_asserted", "pedagogical_inference"],
            "support_roles": [
                "relation_assertion",
                "source_endpoint",
                "target_endpoint",
            ],
            "symmetric_endpoints_canonicalized": True,
            "prerequisite_cycles_forbidden": True,
            "negative_pairs_are_evaluation_labels_only": True,
        },
        "review": {
            "reviewer_id": "maintainer-01",
            "required_reviewer_actor_kind": "human",
            "human_attestation_required_at_gold_seal": True,
            "annotation_guide_path": "docs/graph-annotation-protocol.md",
            "annotation_guide_sha256": _digest(
                files["docs/graph-annotation-protocol.md"]
            ),
            "review_mode": "solo_delayed_two_pass",
            "minimum_delay_hours": 72,
            "pass_b_blind_to_pass_a_labels": True,
            "both_passes_blind_to_system_proposals": True,
            "adjudication_required_before_gold_freeze": True,
            "agreement_measure": "temporal_intra_rater",
            "inter_rater_claim_allowed": False,
            "human_decisions_required": True,
        },
        "evaluation": {
            "threshold_owner_id": "maintainer-01",
            "confirmatory": False,
            "reported_metrics": list(get_args(MetricId)),
            "targets": _target_payloads(frozen=True),
            "report_only_metrics": [
                {
                    "metric_id": "relation_proposal_recall",
                    "unit": "proportion",
                    "evidence_scope": "future_confirmatory_claim_gate",
                    "required_protocol": "gold_bundle_seal",
                    "interval_reporting": "two_sided_95_percent_when_cluster_eligible",
                    "pass_threshold_registered": False,
                }
            ],
            "future_claim_minimum_samples": {
                "applies_to": "automatic_proposal_and_grounded_answer_claim_gates",
                "answerable_questions": 40,
                "unanswerable_questions": 20,
                "atomic_claim_units": 80,
                "exact_citation_opportunities": 80,
                "proposal_gate_closed_world_concepts": 50,
                "proposal_gate_gold_relations": 50,
                "gold_instances_per_relation_type": 10,
                "supported_relation_types_for_macro_claim": 3,
            },
            "confidence_interval": {
                "method": "paired_cluster_bootstrap",
                "confidence_level": 0.95,
                "resamples": 10000,
                "seed": 3362025,
                "resampling_unit": "lecture",
                "minimum_resampling_clusters": 5,
                "insufficient_clusters_policy": "diagnostic_only_no_confirmatory_ci",
                "zero_support_policy": "not_applicable_excluded_from_macro",
            },
            "alias_matching_edge_semantics": "normalized_preferred_name_or_alias_exact_equality",
            "future_alias_table_binding_policy": (
                "gold_bundle_seal_requires_frozen_alias_table_hash"
            ),
            "future_performance_binding_policy": (
                "path_latency_requires_separate_frozen_performance_authority"
            ),
            "concept_matching": "one_to_one_maximum_bipartite_nfkc_casefold_whitespace",
            "relation_matching": "exact_type_direction_and_normalized_endpoints",
            "zero_denominator_result": "not_applicable",
        },
        "rights": {
            "attribution": manifest.attribution,
            "license_spdx": asset.license_spdx,
            "redistribution_allowed": asset.redistribution_allowed,
            "source_bytes_committed": False,
            "public_artifacts_redacted": True,
            "public_source_text_included": False,
            "public_locator_policy": "logical_page_ids_hashes_and_offsets_only",
        },
        "release": {
            "path_evaluation_embargoed_until_gold_freeze": True,
            "golden_graph_partition_policy": "authoring_only",
            "sealed_transfer_access_policy": "append_only_access_ledger_required",
            "flagship_automatic_proposal_claim_eligible": False,
            "held_out_claim_eligible": False,
            "counterfactual_fixture_role": "schema_trust_smoke_only",
            "counterfactual_fixture_is_closed_world_gold": False,
        },
    }
    summary_payload["build_spec_protocol_sha256"] = (
        source_slice_build_spec_sha256(
            GoldenGraphProtocol.model_validate(payload)
        )
    )
    payload["projection"]["source_slice_build_summary_sha256"] = (
        write_draft_hashed_canonical_json(
            repository_root
            / "backend"
            / "golden_graph"
            / "artifacts"
            / "synthetic"
            / "source-slice-build-summary.json",
            summary_payload,
        )
    )
    return authority, payload


def _tool_payload(
    files: dict[str, bytes],
    name: str,
    version: str,
    *,
    distribution_name: str,
) -> dict[str, object]:
    implementation_path = f"backend/golden_graph/artifacts/synthetic/{name}.py"
    config_path = (
        f"backend/golden_graph/artifacts/synthetic/{name}-config.json"
    )
    return {
        "implementation": f"synthetic_{name}",
        "distribution_name": distribution_name,
        "implementation_path": implementation_path,
        "implementation_sha256": _digest(files[implementation_path]),
        "version": version,
        "config_path": config_path,
        "config_sha256": _digest(files[config_path]),
    }


def _rebind_source_slice_summary(
    repository_root: Path,
    payload: dict[str, object],
    *,
    project_repository_commit_sha: str | None = None,
) -> None:
    """Keep a synthetic derivation receipt aligned after a deliberate mutation."""

    projection = payload["projection"]
    summary_path = repository_root / projection["source_slice_build_summary_path"]
    summary, _digest_value = load_hashed_canonical_json(summary_path)
    catalog, _catalog_digest = load_hashed_canonical_json(
        repository_root / projection["source_catalog_path"]
    )
    chunks, _chunks_digest = load_hashed_canonical_json(
        repository_root / projection["chunk_manifest_path"]
    )
    status_counts = {
        status: sum(page["status"] == status for page in catalog["pages"])
        for status in ("included", "excluded", "blank", "parse_failed")
    }
    if project_repository_commit_sha is not None:
        summary["project_repository_commit_sha"] = project_repository_commit_sha
    summary["build_spec_protocol_sha256"] = source_slice_build_spec_sha256(
        GoldenGraphProtocol.model_validate(payload)
    )
    summary.update(
        {
            "semantic_source_catalog_sha256": projection[
                "semantic_source_catalog_sha256"
            ],
            "chunk_manifest_sha256": projection["chunk_manifest_sha256"],
            "page_count": catalog["page_count"],
            "included_page_count": status_counts["included"],
            "excluded_page_count": status_counts["excluded"],
            "blank_page_count": status_counts["blank"],
            "parse_failed_page_count": status_counts["parse_failed"],
            "chunk_count": len(chunks["chunks"]),
        }
    )
    projection["source_slice_build_summary_sha256"] = (
        write_draft_hashed_canonical_json(summary_path, summary)
    )


def _fork_source_slice_summary(
    repository_root: Path,
    payload: dict[str, object],
    filename: str,
) -> None:
    projection = payload["projection"]
    current_path = repository_root / projection["source_slice_build_summary_path"]
    summary, _digest_value = load_hashed_canonical_json(current_path)
    logical_path = (
        Path(projection["source_slice_build_summary_path"])
        .with_name(filename)
        .as_posix()
    )
    projection["source_slice_build_summary_path"] = logical_path
    projection["source_slice_build_summary_sha256"] = (
        write_draft_hashed_canonical_json(
            repository_root / logical_path,
            summary,
        )
    )
    _rebind_source_slice_summary(repository_root, payload)


def _target_payloads(*, frozen: bool) -> list[dict[str, object]]:
    targets = []
    for metric_id, (comparison, threshold, unit) in EXPECTED_V1_TARGETS.items():
        statistical = metric_id in STATISTICAL_TARGETS
        targets.append(
            {
                "metric_id": metric_id,
                "comparison": comparison,
                "threshold": threshold,
                "unit": unit,
                "confidence_interval_required": statistical,
                "confidence_bound_side": (
                    "lower" if statistical and comparison == "gte"
                    else "upper" if statistical
                    else "none"
                ),
                "confidence_bound": threshold if statistical and frozen else None,
                "evidence_scope": (
                    "synthetic_graph_performance"
                    if metric_id.startswith("path_api_p95_")
                    else "authoring_graph_invariants"
                    if metric_id
                    in {
                        "accepted_current_evidence_validity",
                        "graph_integrity_violation_count",
                        "deterministic_path_hash_rate",
                        "golden_path_validity",
                        "edge_evidence_completeness",
                        "locator_open_rate",
                    }
                    else "future_confirmatory_claim_gate"
                ),
                "required_protocol": (
                    "synthetic_graph_performance_v1"
                    if metric_id.startswith("path_api_p95_")
                    else "grounded_answer_evaluation_bundle"
                    if metric_id
                    in {
                        "retrieval_recall_at_5",
                        "citation_precision",
                        "citation_recall",
                        "abstention_f1",
                    }
                    else "gold_bundle_seal"
                ),
            }
        )
    return targets


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _deep_copy(value: dict[str, object]) -> dict[str, object]:
    return json.loads(json.dumps(value))


def _write_sidecar(path: Path) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix(".sha256").write_bytes(
        f"{digest}  {path.name}\n".encode("ascii")
    )


def _frozen_output_path(repository_root: Path, protocol_id: str) -> Path:
    path = (
        repository_root
        / "backend"
        / "golden_graph"
        / "protocols"
        / f"{protocol_id}.frozen.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
