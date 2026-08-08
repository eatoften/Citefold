from __future__ import annotations

import hashlib
import io
import json
import stat
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import pytest

import benchmark_acquisition.fetch as fetch_module
from app.concept_graph import (
    ConceptRelationType,
    RelationEvidenceSupportRole,
    RelationSupportBasis,
    SYMMETRIC_RELATION_TYPES as APP_SYMMETRIC_RELATION_TYPES,
)
from benchmark_acquisition.fetch import (
    AcquisitionError,
    AcquisitionResult,
    acquire_asset,
    acquire_manifest,
)
from benchmark_acquisition.counterfactual_fixture import (
    CounterfactualFixtureError,
    PRODUCTION_RELATION_TYPES,
    SUPPORT_BASES,
    SUPPORT_ROLES,
    SYMMETRIC_RELATION_TYPES,
    load_counterfactual_fixture,
)
from benchmark_acquisition.manifest import ManifestError, load_manifest, parse_manifest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
MANIFEST_PATH = (
    BACKEND_ROOT
    / "benchmark_acquisition"
    / "manifests"
    / "cs336-sp25-v1.json"
)
SOURCE_FIXTURE_PATH = (
    BACKEND_ROOT
    / "benchmark_acquisition"
    / "fixtures"
    / "counterfactual-mini-course-v1.source.json"
)
GOLD_FIXTURE_PATH = (
    BACKEND_ROOT
    / "benchmark_acquisition"
    / "fixtures"
    / "counterfactual-mini-course-v1.gold.json"
)


class _FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, url: str, *, content_type: str) -> None:
        super().__init__(body)
        self.status = 200
        self.headers = {
            "Content-Length": str(len(body)),
            "Content-Type": content_type,
            "Content-Encoding": "identity",
        }
        self._url = url

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.close()


def test_canonical_cs336_manifest_freezes_all_upstream_identities() -> None:
    manifest = load_manifest(MANIFEST_PATH)

    assert manifest.corpus_id == "cs336-sp25-v1"
    assert manifest.course.institution == "Stanford University"
    assert manifest.course.course_code == "CS336"
    assert manifest.course.term == "Spring 2025"
    assert "Stanford University" in manifest.attribution
    assert manifest.commit_sha == "b98b08a98d9d47a69bbdcb4e96a58aa48ee4d13b"
    assert manifest.license_spdx == "MIT"
    assert manifest.license_blob_sha1 == "49d9c744c8de093674f7b12bb5f532fdc08f00a2"
    assert len(manifest.assets) == 8
    assert manifest.max_assets == 8
    assert sum(asset.byte_size for asset in manifest.assets) <= manifest.max_total_bytes
    assert all(not asset.redistribution_allowed for asset in manifest.assets)
    assert all(asset.git_blob_sha1 != "0" * 40 for asset in manifest.assets)
    assert all(asset.sha256 != "0" * 64 for asset in manifest.assets)
    assert {
        partition: sum(asset.partition == partition for asset in manifest.assets)
        for partition in ("authoring", "development", "sealed_transfer")
    } == {"authoring": 4, "development": 2, "sealed_transfer": 2}
    assert {
        asset.asset_id
        for asset in manifest.assets
        if asset.partition == "sealed_transfer"
    } == {"lecture-11-scaling-details", "lecture-16-rlvr"}
    assert manifest.default_output_directory.startswith("backend/data/")
    assert "backend/data/" in (REPOSITORY_ROOT / ".gitignore").read_text(
        encoding="utf-8"
    ).splitlines()


def test_counterfactual_source_and_gold_are_separated_and_strictly_valid() -> None:
    source_payload = json.loads(SOURCE_FIXTURE_PATH.read_text(encoding="utf-8"))
    fixture = load_counterfactual_fixture(
        SOURCE_FIXTURE_PATH,
        GOLD_FIXTURE_PATH,
    )

    assert source_payload["license_spdx"] == "CC0-1.0"
    assert not any(key.startswith("gold_") for key in _walk_keys(source_payload))
    assert len(fixture.sections) == 4
    assert {relation.relation_type for relation in fixture.relations}.issubset(
        PRODUCTION_RELATION_TYPES
    )
    assert PRODUCTION_RELATION_TYPES == set(get_args(ConceptRelationType))
    assert SUPPORT_BASES == set(get_args(RelationSupportBasis))
    assert SUPPORT_ROLES == set(get_args(RelationEvidenceSupportRole))
    assert SYMMETRIC_RELATION_TYPES == set(APP_SYMMETRIC_RELATION_TYPES)
    assert all(concept.short_definition for concept in fixture.concepts)
    assert "merin-gate-opening" in {
        concept.concept_id for concept in fixture.concepts
    }
    assert "merin-gate" not in {concept.concept_id for concept in fixture.concepts}
    for relation in fixture.relations:
        if relation.relation_type in SYMMETRIC_RELATION_TYPES:
            assert relation.source_concept_id < relation.target_concept_id
        expected_roles = (
            {"relation_assertion"}
            if relation.support_basis == "source_asserted"
            else {"source_endpoint", "target_endpoint"}
        )
        assert {span.support_role for span in relation.evidence} == expected_roles
    prerequisite = next(
        relation
        for relation in fixture.relations
        if relation.relation_type == "prerequisite"
    )
    assert prerequisite.target_concept_id == "merin-gate-opening"
    assert prerequisite.support_basis == "pedagogical_inference"
    second_question = next(
        question
        for question in fixture.questions
        if question.question_id == "cf-answerable-2"
    )
    assert {
        (span.source_id, span.locator)
        for span in second_question.required_evidence
    } == {("orin-relay-safety", "section-1")}
    assert all(
        question.expected_claims and question.refusal_reason is None
        if question.answerable
        else not question.expected_claims and question.refusal_reason
        for question in fixture.questions
    )


def test_counterfactual_loader_rejects_dangling_gold_locator(tmp_path: Path) -> None:
    source_payload = json.loads(SOURCE_FIXTURE_PATH.read_text(encoding="utf-8"))
    gold_payload = json.loads(GOLD_FIXTURE_PATH.read_text(encoding="utf-8"))
    source_path = tmp_path / SOURCE_FIXTURE_PATH.name
    gold_path = tmp_path / GOLD_FIXTURE_PATH.name
    _write_hashed_json(source_path, source_payload)
    gold_payload["source_artifact"]["sha256"] = hashlib.sha256(
        source_path.read_bytes()
    ).hexdigest()
    gold_payload["gold_concepts"][0]["evidence"][0]["locator"] = "missing"
    _write_hashed_json(gold_path, gold_payload)

    with pytest.raises(CounterfactualFixtureError, match="Dangling evidence"):
        load_counterfactual_fixture(source_path, gold_path)


def test_counterfactual_loader_rejects_any_gold_key_in_source(tmp_path: Path) -> None:
    source_payload = json.loads(SOURCE_FIXTURE_PATH.read_text(encoding="utf-8"))
    gold_payload = json.loads(GOLD_FIXTURE_PATH.read_text(encoding="utf-8"))
    source_payload["sources"][0]["gold_hidden_label"] = "leak"
    source_path = tmp_path / SOURCE_FIXTURE_PATH.name
    gold_path = tmp_path / GOLD_FIXTURE_PATH.name
    _write_hashed_json(source_path, source_payload)
    gold_payload["source_artifact"]["sha256"] = hashlib.sha256(
        source_path.read_bytes()
    ).hexdigest()
    _write_hashed_json(gold_path, gold_payload)

    with pytest.raises(CounterfactualFixtureError, match="must not contain gold"):
        load_counterfactual_fixture(source_path, gold_path)


def test_counterfactual_loader_rejects_noncanonical_symmetric_order(
    tmp_path: Path,
) -> None:
    source_payload = json.loads(SOURCE_FIXTURE_PATH.read_text(encoding="utf-8"))
    gold_payload = json.loads(GOLD_FIXTURE_PATH.read_text(encoding="utf-8"))
    relation = gold_payload["gold_relations"][0]
    relation["source_concept_id"], relation["target_concept_id"] = (
        relation["target_concept_id"],
        relation["source_concept_id"],
    )
    source_path, gold_path = _write_fixture_pair(
        tmp_path,
        source_payload,
        gold_payload,
    )

    with pytest.raises(CounterfactualFixtureError, match="canonical order"):
        load_counterfactual_fixture(source_path, gold_path)


def test_pedagogical_evidence_must_match_its_endpoint_concept(
    tmp_path: Path,
) -> None:
    source_payload = json.loads(SOURCE_FIXTURE_PATH.read_text(encoding="utf-8"))
    gold_payload = json.loads(GOLD_FIXTURE_PATH.read_text(encoding="utf-8"))
    prerequisite = next(
        relation
        for relation in gold_payload["gold_relations"]
        if relation["relation_type"] == "prerequisite"
    )
    source_endpoint = next(
        evidence
        for evidence in prerequisite["evidence"]
        if evidence["support_role"] == "source_endpoint"
    )
    source_endpoint.update(
        source_id="orin-relay-overview",
        locator="section-1",
        exact_quote="Exactly three pulses place the relay in the amber state.",
        span_sha256="3d348806141bbe49c78ed866e3ab24b8290b8d3e9c72d316a4374ad666b5518f",
    )
    source_path, gold_path = _write_fixture_pair(
        tmp_path,
        source_payload,
        gold_payload,
    )

    with pytest.raises(CounterfactualFixtureError, match="does not match its Concept"):
        load_counterfactual_fixture(source_path, gold_path)


def test_manifest_rejects_unallowlisted_asset_url() -> None:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    payload["assets"][0]["canonical_url"] = "https://example.com/lecture.pdf"

    with pytest.raises(ManifestError, match="allowlisted"):
        parse_manifest(payload)


def test_manifest_rejects_duplicate_content_identity_across_partitions() -> None:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    payload["assets"][1]["sha256"] = payload["assets"][0]["sha256"]

    with pytest.raises(ManifestError, match="Duplicate asset sha256"):
        parse_manifest(payload)

    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    payload["assets"][1]["git_blob_sha1"] = payload["assets"][0]["git_blob_sha1"]
    with pytest.raises(ManifestError, match="Duplicate asset git_blob_sha1"):
        parse_manifest(payload)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload["acquisition"].update(max_assets=7), "max_assets"),
        (
            lambda payload: payload["acquisition"].update(max_total_bytes=1),
            "max_total_bytes",
        ),
        (
            lambda payload: payload["assets"][0].update(output_filename="nul.pdf"),
            "reserved Windows name",
        ),
        (
            lambda payload: payload["assets"][0].update(
                output_filename=f"{'a' * 125}.pdf"
            ),
            "exceeds 128 characters",
        ),
    ],
)
def test_manifest_rejects_aggregate_limits_and_reserved_names(
    mutation,
    message: str,
) -> None:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    mutation(payload)

    with pytest.raises(ManifestError, match=message):
        parse_manifest(payload)


def test_asset_acquisition_verifies_bytes_and_reuses_existing_file(tmp_path: Path) -> None:
    manifest = load_manifest(MANIFEST_PATH)
    body = b"%PDF-1.7\nself-authored offline test bytes\n%%EOF\n"
    asset = _asset_for_body(manifest.assets[0], body)
    open_count = 0

    def open_url(_request, *, timeout):
        nonlocal open_count
        open_count += 1
        assert timeout == manifest.timeout_seconds
        return _FakeResponse(
            body,
            asset.canonical_url,
            content_type="application/pdf; charset=binary",
        )

    result = acquire_asset(
        manifest,
        asset,
        tmp_path,
        open_url=open_url,
    )
    assert result.status == "downloaded"
    assert Path(result.path).read_bytes() == body
    assert Path(result.path).parent.name == asset.partition
    assert open_count == 1

    def reject_network(*_args, **_kwargs):
        raise AssertionError("verified local content must not trigger network access")

    repeated = acquire_asset(
        manifest,
        asset,
        tmp_path,
        open_url=reject_network,
    )
    assert repeated.status == "already_verified"
    assert open_count == 1


def test_asset_acquisition_rejects_existing_symlink(
    tmp_path: Path,
) -> None:
    manifest = load_manifest(MANIFEST_PATH)
    body = b"%PDF-1.7\nsymlink target\n%%EOF\n"
    asset = _asset_for_body(manifest.assets[0], body)
    partition = tmp_path / asset.partition
    partition.mkdir()
    target = tmp_path / "target.pdf"
    target.write_bytes(body)
    destination = partition / asset.output_filename
    try:
        destination.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable on this platform: {exc}")

    with pytest.raises(AcquisitionError, match="does not match"):
        acquire_asset(
            manifest,
            asset,
            tmp_path,
            open_url=lambda *_args, **_kwargs: pytest.fail("network was attempted"),
        )


def test_asset_acquisition_rejects_symlink_in_parent_chain(tmp_path: Path) -> None:
    manifest = load_manifest(MANIFEST_PATH)
    body = b"%PDF-1.7\nparent link\n%%EOF\n"
    asset = _asset_for_body(manifest.assets[0], body)
    real_directory = tmp_path / "real"
    real_directory.mkdir()
    linked_directory = tmp_path / "linked"
    try:
        linked_directory.symlink_to(real_directory, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlinks unavailable on this platform: {exc}")

    with pytest.raises(AcquisitionError, match="link or reparse point"):
        acquire_asset(
            manifest,
            asset,
            linked_directory / "nested",
            open_url=lambda *_args, **_kwargs: pytest.fail("network was attempted"),
        )
    assert not (real_directory / "nested").exists()


def test_existing_file_reparse_metadata_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = load_manifest(MANIFEST_PATH)
    body = b"%PDF-1.7\nreparse simulation\n%%EOF\n"
    asset = _asset_for_body(manifest.assets[0], body)
    partition = tmp_path / asset.partition
    partition.mkdir()
    (partition / asset.output_filename).write_bytes(body)
    monkeypatch.setattr(
        fetch_module,
        "_is_link_or_reparse",
        lambda metadata: stat.S_ISREG(metadata.st_mode),
    )

    with pytest.raises(AcquisitionError, match="does not match"):
        acquire_asset(
            manifest,
            asset,
            tmp_path,
            open_url=lambda *_args, **_kwargs: pytest.fail("network was attempted"),
        )


def test_windows_reparse_attribute_is_recognized() -> None:
    metadata = SimpleNamespace(
        st_mode=stat.S_IFDIR,
        st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
    )
    assert fetch_module._is_link_or_reparse(metadata)


def test_asset_acquisition_enforces_total_wall_clock_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = load_manifest(MANIFEST_PATH)
    body = b"%PDF-1.7\ndeadline bytes\n%%EOF\n"
    asset = _asset_for_body(manifest.assets[0], body)
    short_manifest = replace(manifest, asset_deadline_seconds=1.0)
    clock = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(
        "benchmark_acquisition.fetch.time.monotonic",
        lambda: next(clock),
    )

    def open_url(_request, *, timeout):
        assert timeout == 1.0
        return _FakeResponse(body, asset.canonical_url, content_type="application/pdf")

    with pytest.raises(AcquisitionError, match="deadline exceeded"):
        acquire_asset(
            short_manifest,
            asset,
            tmp_path,
            open_url=open_url,
        )
    assert not (tmp_path / asset.partition / asset.output_filename).exists()


@pytest.mark.parametrize(
    ("content_type", "body_mutator", "message"),
    [
        ("text/html", lambda body: body, "Content-Type"),
        (
            "application/octet-stream",
            lambda body: body[:-2] + b"X\n",
            "SHA-256",
        ),
    ],
)
def test_asset_acquisition_fails_closed_and_removes_partial_files(
    tmp_path: Path,
    content_type: str,
    body_mutator,
    message: str,
) -> None:
    manifest = load_manifest(MANIFEST_PATH)
    registered_body = b"%PDF-1.7\nregistered bytes\n%%EOF\n"
    response_body = body_mutator(registered_body)
    asset = _asset_for_body(manifest.assets[0], registered_body)

    def open_url(_request, *, timeout):
        assert timeout == manifest.timeout_seconds
        return _FakeResponse(
            response_body,
            asset.canonical_url,
            content_type=content_type,
        )

    with pytest.raises(AcquisitionError, match=message):
        acquire_asset(
            manifest,
            asset,
            tmp_path,
            open_url=open_url,
        )

    partition = tmp_path / asset.partition
    assert not (partition / asset.output_filename).exists()
    assert list(partition.glob("*.part")) == []


def test_manifest_acquisition_excludes_sealed_partition_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = load_manifest(MANIFEST_PATH)
    acquired: list[str] = []

    def fake_acquire(_manifest, asset, output_directory, *, open_url):
        assert output_directory == tmp_path.resolve()
        assert open_url is not None
        acquired.append(asset.asset_id)
        return AcquisitionResult(
            asset_id=asset.asset_id,
            partition=asset.partition,
            path=str(output_directory / asset.output_filename),
            byte_size=asset.byte_size,
            sha256=asset.sha256,
            status="offline-test",
        )

    monkeypatch.setattr(
        "benchmark_acquisition.fetch.acquire_asset",
        fake_acquire,
    )
    results = acquire_manifest(
        manifest,
        tmp_path,
        open_url=lambda *_args, **_kwargs: None,
    )

    expected = [
        asset.asset_id
        for asset in manifest.assets
        if asset.partition in {"authoring", "development"}
    ]
    assert acquired == expected
    assert len(results) == 6
    assert not any(
        asset.asset_id in acquired
        for asset in manifest.assets
        if asset.partition == "sealed_transfer"
    )


def test_acquisition_physically_separates_selected_partitions(tmp_path: Path) -> None:
    manifest = load_manifest(MANIFEST_PATH)
    authoring_body = b"%PDF-1.7\nauthoring\n%%EOF\n"
    sealed_body = b"%PDF-1.7\nsealed\n%%EOF\n"
    authoring = _asset_for_body(manifest.assets[0], authoring_body)
    sealed = _asset_for_body(manifest.assets[5], sealed_body)
    bodies = {
        authoring.canonical_url: authoring_body,
        sealed.canonical_url: sealed_body,
    }
    focused_manifest = replace(
        manifest,
        assets=(authoring, sealed),
        max_assets=2,
        max_total_bytes=len(authoring_body) + len(sealed_body),
    )

    def open_url(request, *, timeout):
        assert timeout == manifest.timeout_seconds
        body = bodies[request.full_url]
        return _FakeResponse(body, request.full_url, content_type="application/pdf")

    results = acquire_manifest(
        focused_manifest,
        tmp_path,
        partitions={"authoring", "sealed_transfer"},
        open_url=open_url,
    )

    assert {Path(result.path).parent.name for result in results} == {
        "authoring",
        "sealed_transfer",
    }
    assert (tmp_path / "authoring" / authoring.output_filename).is_file()
    assert (tmp_path / "sealed_transfer" / sealed.output_filename).is_file()


def test_cli_does_not_resolve_explicit_output_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_output = tmp_path / "not-created" / ".." / "chosen"
    captured: dict[str, Path] = {}

    def fake_acquire(_manifest, output_directory, *, partitions):
        captured["output"] = output_directory
        assert partitions == {"authoring", "development"}
        return ()

    monkeypatch.setattr(fetch_module, "acquire_manifest", fake_acquire)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark-acquisition",
            "--manifest",
            str(MANIFEST_PATH),
            "--output-directory",
            str(raw_output),
        ],
    )
    fetch_module.main()

    assert str(captured["output"]) == str(raw_output)
    assert ".." in captured["output"].parts


def _asset_for_body(asset, body: bytes):
    blob_hasher = hashlib.sha1(usedforsecurity=False)
    blob_hasher.update(f"blob {len(body)}\0".encode("ascii"))
    blob_hasher.update(body)
    return replace(
        asset,
        byte_size=len(body),
        git_blob_sha1=blob_hasher.hexdigest(),
        sha256=hashlib.sha256(body).hexdigest(),
    )


def _walk_keys(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key
            yield from _walk_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_keys(nested)


def _write_hashed_json(path: Path, payload: object) -> None:
    encoded = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    path.write_bytes(encoded)
    digest = hashlib.sha256(encoded).hexdigest()
    path.with_suffix(".sha256").write_text(
        f"{digest}  {path.name}\n",
        encoding="ascii",
    )


def _write_fixture_pair(
    directory: Path,
    source_payload: object,
    gold_payload: dict,
) -> tuple[Path, Path]:
    source_path = directory / SOURCE_FIXTURE_PATH.name
    gold_path = directory / GOLD_FIXTURE_PATH.name
    _write_hashed_json(source_path, source_payload)
    gold_payload["source_artifact"]["sha256"] = hashlib.sha256(
        source_path.read_bytes()
    ).hexdigest()
    _write_hashed_json(gold_path, gold_payload)
    return source_path, gold_path
