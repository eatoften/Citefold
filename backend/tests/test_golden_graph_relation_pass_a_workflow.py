from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import golden_graph.relation_annotation_command as relation_command
import golden_graph.relation_pass_a_workflow as relation_workflow
import golden_graph.protocol as protocol_module
from golden_graph.annotation_artifacts import (
    AnnotationArtifactError,
    write_new_private_worksheet,
)
from golden_graph.annotation_models import (
    CONCEPT_ATTESTATION_NAMESPACE,
    G2_ATTESTATION_NAMESPACES,
    RELATION_PASS_A_ATTESTATION_NAMESPACE,
)
from golden_graph.annotation_workflow import (
    CONCEPT_REVIEWER_ATTESTATION,
    default_concept_stage_paths,
    new_concept_annotation_worksheet,
    prepare_concept_inventory,
    publish_concept_inventory_stage,
    signoff_prepared_concept_inventory,
)
from golden_graph.canonical_io import canonical_json_bytes
from golden_graph.protocol import FrozenProtocolAuthority
from golden_graph.relation_annotation_models import (
    RELATION_PASS_A_REVIEWER_ATTESTATION,
    RelationPassAArtifact,
    RelationPassASeal,
    RelationPassASealRequest,
    RelationPassAWorksheet,
)
from golden_graph.relation_pass_a_workflow import (
    RelationPassACommitmentAuthority,
    RelationPassAPrivatePaths,
    RelationPassAPublicCommitmentPaths,
    RelationPassAStagePaths,
    RelationPassAWorkflowError,
    SealedRelationPassAAuthority,
    default_relation_pass_a_stage_paths,
    load_relation_pass_a_commitment,
    load_sealed_relation_pass_a,
    new_relation_pass_a_worksheet,
    parse_relation_pass_a_worksheet,
    prepare_relation_pass_a,
    publish_relation_pass_a_stage,
    signoff_prepared_relation_pass_a,
)
from golden_graph.reviewer_policy import (
    build_reviewer_key_policy,
    load_repository_reviewer_key_policy,
    publish_reviewer_key_policy,
    reviewer_key_policy_path,
)
from golden_graph.source_slice_builder import (
    write_private_source_slice_materialization,
)
from test_golden_graph_annotation_workflow import (
    _registered_reviewer_key_policy,
    _sign_request as sign_concept_request,
    annotation_fixture,
)
from test_golden_graph_source_slice_builder import (
    _build as build_source_slice_fixture,
    build_fixture as source_slice_fixture,
)


@pytest.fixture
def relation_fixture(
    annotation_fixture: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> SimpleNamespace:
    prepared_concepts = prepare_concept_inventory(
        frozen_protocol=annotation_fixture.frozen,
        source_materialization=annotation_fixture.source,
        reviewer_key_policy=annotation_fixture.reviewer_key_policy,
        worksheet=annotation_fixture.worksheet,
    )
    concept_signature = sign_concept_request(
        tmp_path,
        prepared_concepts.seal_request,
        signing_key=annotation_fixture.signing_key,
    )
    signed_concepts = signoff_prepared_concept_inventory(
        prepared=prepared_concepts,
        reviewer_key_policy=annotation_fixture.reviewer_key_policy,
        signature_path=concept_signature,
    )
    public_root = tmp_path / "backend/golden_graph/artifacts"
    concept_paths = default_concept_stage_paths(
        tmp_path,
        annotation_fixture.frozen,
    )
    concept_paths.inventory.parent.mkdir(parents=True)
    concepts = publish_concept_inventory_stage(
        signed=signed_concepts,
        paths=concept_paths,
        public_artifact_root=public_root,
        frozen_protocol=annotation_fixture.frozen,
        source_materialization=annotation_fixture.source,
        reviewer_key_policy=annotation_fixture.reviewer_key_policy,
    )
    relation_policy = _register_relation_policy(
        tmp_path,
        concept_policy=annotation_fixture.reviewer_key_policy,
    )
    monkeypatch.setattr(
        relation_workflow,
        "_require_relation_context",
        lambda **_kwargs: SimpleNamespace(
            sealed_concepts=concepts,
            reviewer_key_policy=relation_policy,
            source_authority=annotation_fixture.evidence_source,
        ),
    )
    monkeypatch.setattr(
        relation_workflow,
        "bind_annotation_evidence_source",
        lambda _source: annotation_fixture.evidence_source,
    )
    worksheet = new_relation_pass_a_worksheet(
        sealed_concepts=concepts,
        reviewer_key_policy=relation_policy,
        worksheet_id="fixture-relation-pass-a-v1",
    )
    complete = _complete_worksheet(
        worksheet,
        source_text=annotation_fixture.source_text,
        chunk_sha=annotation_fixture.chunk_sha,
    )
    private_directory = (
        tmp_path
        / "backend/data/golden_graph/annotations"
        / "fixture-g2-v1"
    )
    private_directory.mkdir(parents=True)
    paths = RelationPassAStagePaths(
        private=RelationPassAPrivatePaths(
            artifact=(
                private_directory / "relation-pass-a.artifact.private.json"
            ),
        ),
        public=RelationPassAPublicCommitmentPaths(
            seal_request=public_root / "relation-pass-a.request.json",
            attestation=public_root / "relation-pass-a.attestation.json",
            seal=public_root / "relation-pass-a.seal.json",
        ),
    )
    return SimpleNamespace(
        concepts=concepts,
        policy=relation_policy,
        signing_key=annotation_fixture.signing_key,
        source_text=annotation_fixture.source_text,
        chunk_sha=annotation_fixture.chunk_sha,
        evidence_source=annotation_fixture.evidence_source,
        worksheet=worksheet,
        complete=complete,
        paths=paths,
        public_root=public_root,
        repository_root=tmp_path,
    )


@pytest.fixture
def real_relation_context_fixture(
    source_slice_fixture: dict[str, object],
) -> SimpleNamespace:
    """Build real protocol, Source, Git-policy, and Concept capabilities."""

    root = source_slice_fixture["root"]
    assert isinstance(root, Path)
    build = build_source_slice_fixture(source_slice_fixture)
    draft_protocol = source_slice_fixture["protocol"]
    projection = draft_protocol.projection.model_copy(
        update={
            "semantic_source_catalog_sha256": (
                build.summary.semantic_source_catalog_sha256
            ),
            "chunk_manifest_sha256": build.summary.chunk_manifest_sha256,
            "source_slice_build_summary_sha256": hashlib.sha256(
                canonical_json_bytes(build.summary)
            ).hexdigest(),
        }
    )
    frozen_model = draft_protocol.__class__.model_validate(
        draft_protocol.model_copy(
            update={
                "protocol_status": "frozen",
                "projection": projection,
            }
        ).model_dump(mode="python", exclude_none=False)
    )
    protocol_payload = canonical_json_bytes(frozen_model)
    protocol_sha256 = hashlib.sha256(protocol_payload).hexdigest()
    protocol_path = (
        root
        / "backend/golden_graph/protocols"
        / f"{frozen_model.protocol_id}.frozen.json"
    )
    protocol_path.parent.mkdir(parents=True, exist_ok=True)
    protocol_path.write_bytes(protocol_payload)
    protocol_path.with_suffix(".sha256").write_text(
        f"{protocol_sha256}  {protocol_path.name}\n",
        encoding="utf-8",
        newline="\n",
    )
    frozen = FrozenProtocolAuthority(
        protocol=frozen_model,
        protocol_sha256=protocol_sha256,
        acquisition_manifest_sha256=(
            source_slice_fixture["manifest_authority"].manifest_sha256
        ),
        artifact_path=protocol_path.resolve(strict=True),
        _validation_token=protocol_module._FROZEN_AUTHORITY_TOKEN,
    )
    concept_policy, signing_key = _registered_reviewer_key_policy(
        root,
        protocol_id=frozen_model.protocol_id,
        frozen_protocol_sha256=protocol_sha256,
        reviewer_id=frozen_model.review.reviewer_id,
    )
    materialization_path = (
        root
        / "backend/data/golden_graph/source_slice_materializations"
        / f"{frozen_model.protocol_id}.private.json"
    )
    source = write_private_source_slice_materialization(
        repository_root=root,
        output_path=materialization_path,
        authority=build,
        protocol=frozen_model,
    )
    draft = new_concept_annotation_worksheet(
        frozen_protocol=frozen,
        source_materialization=source,
        reviewer_key_policy=concept_policy,
        worksheet_id="real-relation-context-concepts-v1",
    )
    first_chunk = source.materialization.course_source_chunks[0]
    first_binding = source.materialization.chunk_manifest.chunks[0]
    chunk_start = first_chunk.locator.metadata["start_offset"]
    candidates = tuple(
        {
            "candidate_key": f"real-concept-{index:02d}",
            "candidate_label": f"Real candidate {index:02d}",
            "decision": "include",
            "preferred_name": f"Real Concept {index:02d}",
            "short_definition": (
                f"Synthetic integration definition {index:02d}."
            ),
            "aliases": [f"RC{index:02d}"],
            "evidence": [{
                "chunk_ordinal": first_chunk.ordinal,
                "logical_page_id": (
                    first_chunk.locator.metadata["logical_page_id"]
                ),
                "semantic_chunk_sha256": (
                    first_binding.semantic_chunk_sha256
                ),
                "page_global_utf8_start": chunk_start,
                "exact_quote": first_chunk.text,
            }],
            "decision_rationale": (
                f"Independent integration Concept {index:02d}."
            ),
        }
        for index in range(12)
    )
    concept_payload = draft.model_dump(mode="json", exclude_none=False)
    concept_payload.update({
        "worksheet_status": "complete",
        "reviewer_actor_kind_declaration": "human",
        "reviewer_attestation_statement": CONCEPT_REVIEWER_ATTESTATION,
        "reviewer_attested_at_utc": "2020-08-08T12:00:00Z",
        "candidates": candidates,
    })
    concept_worksheet = draft.__class__.model_validate(concept_payload)
    prepared_concepts = prepare_concept_inventory(
        frozen_protocol=frozen,
        source_materialization=source,
        reviewer_key_policy=concept_policy,
        worksheet=concept_worksheet,
    )
    concept_signature = sign_concept_request(
        root,
        prepared_concepts.seal_request,
        signing_key=signing_key,
    )
    signed_concepts = signoff_prepared_concept_inventory(
        prepared=prepared_concepts,
        reviewer_key_policy=concept_policy,
        signature_path=concept_signature,
    )
    public_root = root / "backend/golden_graph/artifacts"
    concept_paths = default_concept_stage_paths(root, frozen)
    concept_paths.inventory.parent.mkdir(parents=True, exist_ok=True)
    concepts = publish_concept_inventory_stage(
        signed=signed_concepts,
        paths=concept_paths,
        public_artifact_root=public_root,
        frozen_protocol=frozen,
        source_materialization=source,
        reviewer_key_policy=concept_policy,
    )
    relation_policy = _register_relation_policy(
        root,
        concept_policy=concept_policy,
    )
    relation_worksheet = new_relation_pass_a_worksheet(
        sealed_concepts=concepts,
        reviewer_key_policy=relation_policy,
        worksheet_id="real-relation-pass-a-v1",
    )
    complete_payload = relation_worksheet.model_dump(
        mode="json",
        exclude_none=False,
    )
    for decision in complete_payload["pair_decisions"]:
        decision.update({
            "outcome": "none",
            "none_rationale": "No supported edge in this integration pair.",
            "relations": [],
        })
    complete_payload.update({
        "worksheet_status": "complete",
        "reviewer_actor_kind_declaration": "human",
        "reviewer_attestation_statement": RELATION_PASS_A_REVIEWER_ATTESTATION,
        "reviewer_attested_at_utc": "2020-08-08T12:00:00Z",
    })
    return SimpleNamespace(
        root=root,
        frozen=frozen,
        source=source,
        concept_policy=concept_policy,
        relation_policy=relation_policy,
        concepts=concepts,
        signing_key=signing_key,
        public_root=public_root,
        worksheet=RelationPassAWorksheet.model_validate(complete_payload),
    )


def test_blank_pass_a_packet_is_exhaustive_and_contains_no_labels(
    relation_fixture: SimpleNamespace,
) -> None:
    worksheet = relation_fixture.worksheet

    assert worksheet.worksheet_status == "draft"
    assert worksheet.concept_count == 12
    assert worksheet.pair_count == 66
    assert len(worksheet.pair_decisions) == 66
    assert {item.outcome for item in worksheet.pair_decisions} == {"pending"}
    assert all(not item.relations for item in worksheet.pair_decisions)
    assert len(worksheet.commitment_nonce_hex) == 64
    assert set(worksheet.commitment_nonce_hex) <= set("0123456789abcdef")
    payload = canonical_json_bytes(worksheet)
    assert b'"exact_quote"' not in payload
    assert b'"prerequisite"' not in payload
    assert b'"source_asserted"' not in payload


def test_real_context_replays_historical_concept_dag_before_prepare(
    real_relation_context_fixture: SimpleNamespace,
) -> None:
    prepared = prepare_relation_pass_a(
        sealed_concepts=real_relation_context_fixture.concepts,
        reviewer_key_policy=real_relation_context_fixture.relation_policy,
        worksheet=real_relation_context_fixture.worksheet,
    )

    assert prepared.artifact.pair_count == 66
    assert prepared.artifact.none_pair_count == 66
    assert prepared.artifact.positive_pair_count == 0
    assert prepared.artifact.relation_count == 0
    assert prepared.artifact.commitment_nonce_hex == (
        real_relation_context_fixture.worksheet.commitment_nonce_hex
    )


def test_real_context_revocation_blocks_authoring_but_not_historical_replay(
    real_relation_context_fixture: SimpleNamespace,
) -> None:
    fixture = real_relation_context_fixture
    prepared = prepare_relation_pass_a(
        sealed_concepts=fixture.concepts,
        reviewer_key_policy=fixture.relation_policy,
        worksheet=fixture.worksheet,
    )
    signature = _sign_relation_request(
        fixture.root,
        prepared.seal_request,
        signing_key=fixture.signing_key,
    )
    signed = signoff_prepared_relation_pass_a(
        prepared=prepared,
        reviewer_key_policy=fixture.relation_policy,
        signature_path=signature,
    )
    paths = default_relation_pass_a_stage_paths(
        fixture.root,
        fixture.concepts,
    )
    paths.private.artifact.parent.mkdir(parents=True, exist_ok=True)
    sealed = publish_relation_pass_a_stage(
        signed=signed,
        paths=paths,
        repository_root=fixture.root,
        public_artifact_root=fixture.public_root,
        sealed_concepts=fixture.concepts,
        reviewer_key_policy=fixture.relation_policy,
    )
    policy_path = reviewer_key_policy_path(
        fixture.root,
        protocol_id=fixture.relation_policy.policy.protocol_id,
        reviewer_id=fixture.relation_policy.policy.reviewer_id,
    )
    policy_path.unlink()
    policy_path.with_suffix(".sha256").unlink()

    with pytest.raises(RelationPassAWorkflowError, match="deep validation"):
        new_relation_pass_a_worksheet(
            sealed_concepts=fixture.concepts,
            reviewer_key_policy=fixture.relation_policy,
            worksheet_id="must-not-author-after-revocation",
        )

    commitment = load_relation_pass_a_commitment(
        paths=paths.public,
        public_artifact_root=fixture.public_root,
        sealed_concepts=fixture.concepts,
        reviewer_key_policy=fixture.relation_policy,
    )
    replay = load_sealed_relation_pass_a(
        paths=paths,
        repository_root=fixture.root,
        public_artifact_root=fixture.public_root,
        sealed_concepts=fixture.concepts,
        reviewer_key_policy=fixture.relation_policy,
    )

    assert commitment.seal.artifact_sha256 == sealed.commitment.seal.artifact_sha256
    assert replay.private_artifact.artifact_sha256 == prepared.artifact_sha256


def test_real_context_rejects_nested_protocol_mutation(
    real_relation_context_fixture: SimpleNamespace,
) -> None:
    fixture = real_relation_context_fixture
    mutated_review = fixture.frozen.protocol.review.model_copy(
        update={"minimum_delay_hours": 1}
    )
    mutated_protocol_model = fixture.frozen.protocol.model_copy(
        update={"review": mutated_review}
    )
    mutated_protocol = copy.copy(fixture.frozen)
    object.__setattr__(mutated_protocol, "protocol", mutated_protocol_model)
    mutated_concepts = copy.copy(fixture.concepts)
    object.__setattr__(mutated_concepts, "protocol", mutated_protocol)

    with pytest.raises(RelationPassAWorkflowError, match="deep validation"):
        prepare_relation_pass_a(
            sealed_concepts=mutated_concepts,
            reviewer_key_policy=fixture.relation_policy,
            worksheet=fixture.worksheet,
        )

    mutated_manifest_receipt = copy.copy(fixture.frozen)
    object.__setattr__(
        mutated_manifest_receipt,
        "acquisition_manifest_sha256",
        "0" * 64,
    )
    concepts_with_mutated_manifest = copy.copy(fixture.concepts)
    object.__setattr__(
        concepts_with_mutated_manifest,
        "protocol",
        mutated_manifest_receipt,
    )
    with pytest.raises(RelationPassAWorkflowError, match="deep validation"):
        new_relation_pass_a_worksheet(
            sealed_concepts=concepts_with_mutated_manifest,
            reviewer_key_policy=fixture.relation_policy,
            worksheet_id="reject-forged-manifest-receipt",
        )

    mutated_policy_model = fixture.concept_policy.policy.model_copy(
        update={"allowed_namespaces": G2_ATTESTATION_NAMESPACES}
    )
    mutated_concept_policy = copy.copy(fixture.concept_policy)
    object.__setattr__(
        mutated_concept_policy,
        "policy",
        mutated_policy_model,
    )
    object.__setattr__(
        mutated_concept_policy,
        "policy_sha256",
        hashlib.sha256(canonical_json_bytes(mutated_policy_model)).hexdigest(),
    )
    concepts_with_mutated_policy = copy.copy(fixture.concepts)
    object.__setattr__(
        concepts_with_mutated_policy,
        "reviewer_key_policy",
        mutated_concept_policy,
    )
    with pytest.raises(RelationPassAWorkflowError, match="deep validation"):
        new_relation_pass_a_worksheet(
            sealed_concepts=concepts_with_mutated_policy,
            reviewer_key_policy=fixture.relation_policy,
            worksheet_id="reject-forged-concept-policy",
        )


def test_real_context_rejects_copied_concept_leaf_path_substitution(
    real_relation_context_fixture: SimpleNamespace,
) -> None:
    fixture = real_relation_context_fixture
    alternate = fixture.root / "alternate-concept-dag"
    alternate.mkdir()
    copied_inventory_path = alternate / fixture.concepts.inventory.artifact_path.name
    copied_sidecar_path = copied_inventory_path.with_suffix(".sha256")
    shutil.copy2(fixture.concepts.inventory.artifact_path, copied_inventory_path)
    shutil.copy2(
        fixture.concepts.inventory.artifact_path.with_suffix(".sha256"),
        copied_sidecar_path,
    )
    copied_inventory = copy.copy(fixture.concepts.inventory)
    object.__setattr__(
        copied_inventory,
        "artifact_path",
        copied_inventory_path.resolve(strict=True),
    )
    substituted = copy.copy(fixture.concepts)
    object.__setattr__(substituted, "inventory", copied_inventory)

    with pytest.raises(RelationPassAWorkflowError, match="deep validation"):
        new_relation_pass_a_worksheet(
            sealed_concepts=substituted,
            reviewer_key_policy=fixture.relation_policy,
            worksheet_id="reject-path-substitution",
        )

    fixture.source.artifact_path.write_bytes(
        fixture.source.artifact_path.read_bytes() + b" "
    )
    with pytest.raises(RelationPassAWorkflowError, match="deep validation"):
        new_relation_pass_a_worksheet(
            sealed_concepts=fixture.concepts,
            reviewer_key_policy=fixture.relation_policy,
            worksheet_id="reject-private-source-tamper",
        )


def test_prepare_resolves_two_relations_without_putting_labels_in_commitment(
    relation_fixture: SimpleNamespace,
) -> None:
    prepared = prepare_relation_pass_a(
        sealed_concepts=relation_fixture.concepts,
        reviewer_key_policy=relation_fixture.policy,
        worksheet=relation_fixture.complete,
    )

    assert prepared.artifact.pair_count == 66
    assert prepared.artifact.positive_pair_count == 2
    assert prepared.artifact.none_pair_count == 64
    assert prepared.artifact.relation_count == 2
    private_bytes = canonical_json_bytes(prepared.artifact)
    public_bytes = canonical_json_bytes(prepared.seal_request)
    assert prepared.artifact.commitment_nonce_hex == (
        relation_fixture.complete.commitment_nonce_hex
    )
    assert b'"exact_quote"' not in private_bytes
    assert b"Evidence sentence for concept" not in private_bytes
    for private_label in (
        b"concept-00",
        b"prerequisite",
        b"pedagogical_inference",
        b"positive_pair_count",
        b"No supported relation",
        b"commitment_nonce_hex",
        relation_fixture.complete.commitment_nonce_hex.encode("ascii"),
    ):
        assert private_label not in public_bytes
    assert prepared.seal_request.minimum_delay_hours_before_pass_b == 72
    assert prepared.seal_request.labels_embargoed_at_commitment is True


def test_sign_publish_and_reload_keep_labels_private_and_commitment_label_free(
    relation_fixture: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = prepare_relation_pass_a(
        sealed_concepts=relation_fixture.concepts,
        reviewer_key_policy=relation_fixture.policy,
        worksheet=relation_fixture.complete,
    )
    signature = _sign_relation_request(
        tmp_path,
        prepared.seal_request,
        signing_key=relation_fixture.signing_key,
    )
    signed = signoff_prepared_relation_pass_a(
        prepared=prepared,
        reviewer_key_policy=relation_fixture.policy,
        signature_path=signature,
    )
    authority = publish_relation_pass_a_stage(
        signed=signed,
        paths=relation_fixture.paths,
        repository_root=relation_fixture.repository_root,
        public_artifact_root=relation_fixture.public_root,
        sealed_concepts=relation_fixture.concepts,
        reviewer_key_policy=relation_fixture.policy,
    )
    with monkeypatch.context() as isolated:
        isolated.setattr(
            relation_workflow,
            "load_private_canonical_artifact",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("commitment loader read hidden Pass A labels")
            ),
        )
        commitment = load_relation_pass_a_commitment(
            paths=relation_fixture.paths.public,
            public_artifact_root=relation_fixture.public_root,
            sealed_concepts=relation_fixture.concepts,
            reviewer_key_policy=relation_fixture.policy,
        )
    replay = load_sealed_relation_pass_a(
        paths=relation_fixture.paths,
        repository_root=relation_fixture.repository_root,
        public_artifact_root=relation_fixture.public_root,
        sealed_concepts=relation_fixture.concepts,
        reviewer_key_policy=relation_fixture.policy,
    )

    assert isinstance(authority, SealedRelationPassAAuthority)
    assert isinstance(commitment, RelationPassACommitmentAuthority)
    assert not hasattr(commitment, "private_artifact")
    assert replay.private_artifact.artifact_sha256 == prepared.artifact_sha256
    assert commitment.seal.artifact.status == (
        "relation_pass_a_commitment_only_not_gold_bundle"
    )
    assert commitment.seal.artifact.labels_unreleased_at_commitment is True
    assert commitment.seal.artifact.detached_attestation.namespace == (
        RELATION_PASS_A_ATTESTATION_NAMESPACE
    )
    receipt = relation_command._commitment_receipt(
        commitment,
        role="golden_graph_relation_pass_a_test_receipt",
        status="test_only",
    )
    assert set(receipt) == {
        "artifact_role",
        "gold_bundle_sealed",
        "key_control_attestation_verified",
        "key_control_only_not_proof_of_humanity",
        "label_release_policy",
        "labels_embargoed_at_commitment",
        "minimum_delay_hours_before_pass_b",
        "pair_count",
        "protocol_id",
        "relation_pass_a_artifact_sha256",
        "relation_pass_a_seal_request_sha256",
        "relation_pass_a_seal_sha256",
        "reviewer_attested_at_utc",
        "reviewer_key_policy_git_commit",
        "reviewer_key_policy_sha256",
        "schema_version",
        "software_authenticated_minimum_delay",
        "software_authenticated_prediction_blindness",
        "software_authenticated_reviewer_identity",
        "status",
    }
    assert not any(key.endswith("_path") for key in _recursive_keys(receipt))
    public_bytes = b"".join(
        path.read_bytes()
        for path in (
            relation_fixture.paths.public.seal_request,
            relation_fixture.paths.public.attestation,
            relation_fixture.paths.public.seal,
        )
    )
    for private_label in (
        b"concept-00",
        b"prerequisite",
        b"pedagogical_inference",
        b"positive_pair_count",
        b"No supported relation",
    ):
        assert private_label not in public_bytes
    assert subprocess.run(
        [
            "git",
            "-C",
            str(relation_fixture.repository_root),
            "check-ignore",
            "--quiet",
            str(relation_fixture.paths.private.artifact),
        ],
        check=False,
    ).returncode == 0
    assert subprocess.run(
        [
            "git",
            "-C",
            str(relation_fixture.repository_root),
            "ls-files",
            "--error-unmatch",
            str(relation_fixture.paths.private.artifact),
        ],
        check=False,
        capture_output=True,
    ).returncode != 0


def test_public_commitment_api_and_payloads_cannot_carry_private_paths_or_labels(
    relation_fixture: SimpleNamespace,
    tmp_path: Path,
) -> None:
    prepared = prepare_relation_pass_a(
        sealed_concepts=relation_fixture.concepts,
        reviewer_key_policy=relation_fixture.policy,
        worksheet=relation_fixture.complete,
    )
    signature = _sign_relation_request(
        tmp_path,
        prepared.seal_request,
        signing_key=relation_fixture.signing_key,
    )
    signed = signoff_prepared_relation_pass_a(
        prepared=prepared,
        reviewer_key_policy=relation_fixture.policy,
        signature_path=signature,
    )
    public_models = (
        (
            signed.prepared.seal_request,
            {
                "approval_statement",
                "artifact_role",
                "blind_to_system_proposals_declaration",
                "chunk_manifest_sha256",
                "concept_inventory_seal_sha256",
                "concept_inventory_sha256",
                "frozen_protocol_sha256",
                "label_release_policy",
                "labels_embargoed_at_commitment",
                "minimum_delay_hours_before_pass_b",
                "namespace",
                "pair_count",
                "pass_role",
                "protocol_id",
                "relation_pair_manifest_sha256",
                "relation_pass_a_artifact_sha256",
                "relation_pass_a_worksheet_sha256",
                "reviewer_actor_kind_declaration",
                "reviewer_attested_at_utc",
                "reviewer_id",
                "reviewer_key_policy_git_commit",
                "reviewer_key_policy_sha256",
                "schema_version",
                "semantic_source_catalog_sha256",
                "software_authenticated_minimum_delay",
                "software_authenticated_prediction_blindness",
                "software_authenticated_reviewer_identity",
            },
        ),
        (
            signed.attestation_artifact,
            {
                "allowed_signers_policy_utf8",
                "allowed_signers_sha256",
                "artifact_role",
                "key_control_only_not_proof_of_humanity",
                "namespace",
                "public_key_fingerprint",
                "schema_version",
                "signature_armored",
                "signature_sha256",
                "signed_payload_sha256",
                "signer_identity",
            },
        ),
        (
            signed.seal,
            {
                "artifact_role",
                "blind_to_system_proposals_declaration",
                "concept_inventory_seal_sha256",
                "concept_inventory_sha256",
                "detached_attestation",
                "detached_attestation_artifact_sha256",
                "frozen_protocol_sha256",
                "label_release_policy",
                "labels_embargoed_at_commitment",
                "labels_unreleased_at_commitment",
                "minimum_delay_hours_before_pass_b",
                "pair_count",
                "pass_role",
                "protocol_id",
                "relation_pair_manifest_sha256",
                "relation_pass_a_artifact_sha256",
                "relation_pass_a_seal_request_sha256",
                "reviewer_actor_kind_declaration",
                "reviewer_attested_at_utc",
                "reviewer_id",
                "reviewer_key_policy_git_commit",
                "reviewer_key_policy_sha256",
                "schema_version",
                "software_authenticated_minimum_delay",
                "software_authenticated_prediction_blindness",
                "software_authenticated_reviewer_identity",
                "status",
            },
        ),
    )
    forbidden_keys = {
        "commitment_nonce_hex",
        "concept_keys",
        "evidence",
        "exact_quote",
        "none_pair_count",
        "none_rationale",
        "pair_decisions",
        "positive_pair_count",
        "private_artifact",
        "private_path",
        "relation_count",
        "relations",
        "review_rationale",
    }
    for model, expected_fields in public_models:
        payload = model.model_dump(mode="json", exclude_none=False)
        recursive_keys = _recursive_keys(payload)
        assert set(payload) == expected_fields
        assert not forbidden_keys.intersection(recursive_keys)
        assert not any(key.endswith("_path") for key in recursive_keys)

    with pytest.raises(RelationPassAWorkflowError, match="public-only"):
        load_relation_pass_a_commitment(
            paths=relation_fixture.paths,  # type: ignore[arg-type]
            public_artifact_root=relation_fixture.public_root,
            sealed_concepts=relation_fixture.concepts,
            reviewer_key_policy=relation_fixture.policy,
        )


def test_publication_rejects_path_collision_before_writing_any_leaf(
    relation_fixture: SimpleNamespace,
    tmp_path: Path,
) -> None:
    prepared = prepare_relation_pass_a(
        sealed_concepts=relation_fixture.concepts,
        reviewer_key_policy=relation_fixture.policy,
        worksheet=relation_fixture.complete,
    )
    signature = _sign_relation_request(
        tmp_path,
        prepared.seal_request,
        signing_key=relation_fixture.signing_key,
    )
    signed = signoff_prepared_relation_pass_a(
        prepared=prepared,
        reviewer_key_policy=relation_fixture.policy,
        signature_path=signature,
    )
    collision = RelationPassAStagePaths(
        private=relation_fixture.paths.private,
        public=RelationPassAPublicCommitmentPaths(
            seal_request=relation_fixture.paths.public.seal_request,
            attestation=relation_fixture.paths.public.attestation,
            seal=relation_fixture.paths.public.attestation,
        ),
    )

    with pytest.raises(RelationPassAWorkflowError, match="pairwise distinct"):
        publish_relation_pass_a_stage(
            signed=signed,
            paths=collision,
            repository_root=relation_fixture.repository_root,
            public_artifact_root=relation_fixture.public_root,
            sealed_concepts=relation_fixture.concepts,
            reviewer_key_policy=relation_fixture.policy,
        )

    assert not relation_fixture.paths.private.artifact.exists()
    assert not relation_fixture.paths.public.seal_request.exists()
    assert not relation_fixture.paths.public.attestation.exists()


def test_publication_replays_signature_before_writing_any_leaf(
    relation_fixture: SimpleNamespace,
    tmp_path: Path,
) -> None:
    prepared = prepare_relation_pass_a(
        sealed_concepts=relation_fixture.concepts,
        reviewer_key_policy=relation_fixture.policy,
        worksheet=relation_fixture.complete,
    )
    signature = _sign_relation_request(
        tmp_path,
        prepared.seal_request,
        signing_key=relation_fixture.signing_key,
    )
    signed = signoff_prepared_relation_pass_a(
        prepared=prepared,
        reviewer_key_policy=relation_fixture.policy,
        signature_path=signature,
    )
    bad_reference = signed.seal.detached_attestation.model_copy(
        update={"allowed_signers_sha256": "0" * 64}
    )
    bad_seal = RelationPassASeal.model_validate({
        **signed.seal.model_dump(mode="json", exclude_none=False),
        "detached_attestation": bad_reference.model_dump(mode="json"),
    })
    forged = copy.copy(signed)
    object.__setattr__(forged, "seal", bad_seal)
    object.__setattr__(
        forged,
        "seal_sha256",
        hashlib.sha256(canonical_json_bytes(bad_seal)).hexdigest(),
    )

    with pytest.raises(RelationPassAWorkflowError, match="attestation"):
        publish_relation_pass_a_stage(
            signed=forged,
            paths=relation_fixture.paths,
            repository_root=relation_fixture.repository_root,
            public_artifact_root=relation_fixture.public_root,
            sealed_concepts=relation_fixture.concepts,
            reviewer_key_policy=relation_fixture.policy,
        )

    assert not relation_fixture.paths.private.artifact.exists()
    assert not any(
        path.exists()
        for path in (
            relation_fixture.paths.public.seal_request,
            relation_fixture.paths.public.attestation,
            relation_fixture.paths.public.seal,
        )
    )


def test_publication_uses_detached_snapshot_after_validation_hook_mutation(
    relation_fixture: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = prepare_relation_pass_a(
        sealed_concepts=relation_fixture.concepts,
        reviewer_key_policy=relation_fixture.policy,
        worksheet=relation_fixture.complete,
    )
    signature = _sign_relation_request(
        tmp_path,
        prepared.seal_request,
        signing_key=relation_fixture.signing_key,
    )
    signed = signoff_prepared_relation_pass_a(
        prepared=prepared,
        reviewer_key_policy=relation_fixture.policy,
        signature_path=signature,
    )
    valid_seal_sha256 = signed.seal_sha256
    bad_reference = signed.seal.detached_attestation.model_copy(
        update={"allowed_signers_sha256": "0" * 64}
    )
    bad_seal = RelationPassASeal.model_validate({
        **signed.seal.model_dump(mode="json", exclude_none=False),
        "detached_attestation": bad_reference.model_dump(mode="json"),
    })
    real_validate = relation_workflow._validate_signed_for_publication

    def validate_then_mutate_caller(**kwargs):
        real_validate(**kwargs)
        object.__setattr__(signed, "seal", bad_seal)
        object.__setattr__(
            signed,
            "seal_sha256",
            hashlib.sha256(canonical_json_bytes(bad_seal)).hexdigest(),
        )

    monkeypatch.setattr(
        relation_workflow,
        "_validate_signed_for_publication",
        validate_then_mutate_caller,
    )
    authority = publish_relation_pass_a_stage(
        signed=signed,
        paths=relation_fixture.paths,
        repository_root=relation_fixture.repository_root,
        public_artifact_root=relation_fixture.public_root,
        sealed_concepts=relation_fixture.concepts,
        reviewer_key_policy=relation_fixture.policy,
    )

    assert authority.commitment.seal.artifact_sha256 == valid_seal_sha256
    assert signed.seal != authority.commitment.seal.artifact


def test_concept_namespace_signature_cannot_authorize_relation_pass_a(
    relation_fixture: SimpleNamespace,
    tmp_path: Path,
) -> None:
    prepared = prepare_relation_pass_a(
        sealed_concepts=relation_fixture.concepts,
        reviewer_key_policy=relation_fixture.policy,
        worksheet=relation_fixture.complete,
    )
    wrong_namespace_signature = _sign_relation_request(
        tmp_path,
        prepared.seal_request,
        signing_key=relation_fixture.signing_key,
        namespace=CONCEPT_ATTESTATION_NAMESPACE,
    )

    with pytest.raises(RelationPassAWorkflowError, match="could not be verified"):
        signoff_prepared_relation_pass_a(
            prepared=prepared,
            reviewer_key_policy=relation_fixture.policy,
            signature_path=wrong_namespace_signature,
        )


def test_seal_last_crash_leaves_no_root_and_identical_retry_recovers(
    relation_fixture: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = prepare_relation_pass_a(
        sealed_concepts=relation_fixture.concepts,
        reviewer_key_policy=relation_fixture.policy,
        worksheet=relation_fixture.complete,
    )
    signature = _sign_relation_request(
        tmp_path,
        prepared.seal_request,
        signing_key=relation_fixture.signing_key,
    )
    signed = signoff_prepared_relation_pass_a(
        prepared=prepared,
        reviewer_key_policy=relation_fixture.policy,
        signature_path=signature,
    )
    real_publish = relation_workflow.publish_canonical_artifact
    crashed = False

    def crash_before_root(path, artifact, **kwargs):
        nonlocal crashed
        if path == relation_fixture.paths.public.seal and not crashed:
            crashed = True
            raise RuntimeError("simulated crash before Relation root")
        return real_publish(path, artifact, **kwargs)

    monkeypatch.setattr(
        relation_workflow,
        "publish_canonical_artifact",
        crash_before_root,
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        publish_relation_pass_a_stage(
            signed=signed,
            paths=relation_fixture.paths,
            repository_root=relation_fixture.repository_root,
            public_artifact_root=relation_fixture.public_root,
            sealed_concepts=relation_fixture.concepts,
            reviewer_key_policy=relation_fixture.policy,
        )

    assert relation_fixture.paths.private.artifact.exists()
    assert relation_fixture.paths.public.seal_request.exists()
    assert relation_fixture.paths.public.attestation.exists()
    assert not relation_fixture.paths.public.seal.exists()
    monkeypatch.setattr(
        relation_workflow,
        "publish_canonical_artifact",
        real_publish,
    )
    recovered = publish_relation_pass_a_stage(
        signed=signed,
        paths=relation_fixture.paths,
        repository_root=relation_fixture.repository_root,
        public_artifact_root=relation_fixture.public_root,
        sealed_concepts=relation_fixture.concepts,
        reviewer_key_policy=relation_fixture.policy,
    )

    assert isinstance(recovered, SealedRelationPassAAuthority)
    assert relation_fixture.paths.public.seal.exists()
    relation_fixture.paths.public.seal.write_bytes(
        relation_fixture.paths.public.seal.read_bytes() + b" "
    )
    with pytest.raises(AnnotationArtifactError):
        load_relation_pass_a_commitment(
            paths=relation_fixture.paths.public,
            public_artifact_root=relation_fixture.public_root,
            sealed_concepts=relation_fixture.concepts,
            reviewer_key_policy=relation_fixture.policy,
        )


def test_inference_evidence_must_match_each_sealed_concept_endpoint(
    relation_fixture: SimpleNamespace,
) -> None:
    payload = relation_fixture.complete.model_dump(mode="json", exclude_none=False)
    target = next(
        item
        for item in payload["pair_decisions"]
        if item["left_concept_key"] == "concept-00"
        and item["right_concept_key"] == "concept-02"
    )
    wrong = _selection(
        3,
        role="target_endpoint",
        source_text=relation_fixture.source_text,
        chunk_sha=relation_fixture.chunk_sha,
    )
    target["relations"][0]["evidence"][1] = wrong
    worksheet = RelationPassAWorksheet.model_validate(payload)

    with pytest.raises(
        RelationPassAWorkflowError,
        match="must match its sealed Concept evidence",
    ):
        prepare_relation_pass_a(
            sealed_concepts=relation_fixture.concepts,
            reviewer_key_policy=relation_fixture.policy,
            worksheet=worksheet,
        )


def test_complete_packet_rejects_reordering_invalid_roles_and_direction_conflict(
    relation_fixture: SimpleNamespace,
) -> None:
    reordered = relation_fixture.complete.model_dump(
        mode="json",
        exclude_none=False,
    )
    reordered["pair_decisions"][0], reordered["pair_decisions"][1] = (
        reordered["pair_decisions"][1],
        reordered["pair_decisions"][0],
    )
    with pytest.raises(ValidationError, match="complete pair universe"):
        RelationPassAWorksheet.model_validate(reordered)

    invalid_role = relation_fixture.complete.model_dump(
        mode="json",
        exclude_none=False,
    )
    first_positive = next(
        item for item in invalid_role["pair_decisions"] if item["relations"]
    )
    first_positive["relations"][0]["evidence"].append(
        _selection(
            1,
            role="source_endpoint",
            source_text=relation_fixture.source_text,
            chunk_sha=relation_fixture.chunk_sha,
        )
    )
    with pytest.raises(ValidationError, match="only relation_assertion"):
        RelationPassAWorksheet.model_validate(invalid_role)

    conflict = relation_fixture.complete.model_dump(
        mode="json",
        exclude_none=False,
    )
    first_positive = next(
        item for item in conflict["pair_decisions"] if item["relations"]
    )
    reversed_relation = dict(first_positive["relations"][0])
    reversed_relation["source_concept_key"] = first_positive["right_concept_key"]
    reversed_relation["target_concept_key"] = first_positive["left_concept_key"]
    first_positive["relations"].append(reversed_relation)
    with pytest.raises(ValidationError, match="at most one Relation"):
        RelationPassAWorksheet.model_validate(conflict)


def test_artifact_rejects_prerequisite_cycle(
    relation_fixture: SimpleNamespace,
) -> None:
    prepared = prepare_relation_pass_a(
        sealed_concepts=relation_fixture.concepts,
        reviewer_key_policy=relation_fixture.policy,
        worksheet=relation_fixture.complete,
    )
    payload = prepared.artifact.model_dump(mode="json")
    evidence = payload["pair_decisions"][0]["relations"][0]["evidence"]
    for left, right, source, target in (
        ("concept-00", "concept-01", "concept-00", "concept-01"),
        ("concept-00", "concept-02", "concept-02", "concept-00"),
        ("concept-01", "concept-02", "concept-01", "concept-02"),
    ):
        decision = next(
            item
            for item in payload["pair_decisions"]
            if item["left_concept_key"] == left
            and item["right_concept_key"] == right
        )
        decision.update({
            "outcome": "relations",
            "none_rationale": None,
            "relations": [{
                "relation_type": "prerequisite",
                "source_concept_key": source,
                "target_concept_key": target,
                "support_basis": "source_asserted",
                "proposal_origin_declaration": "human",
                "evidence": evidence,
                "review_rationale": "Synthetic cycle edge for validation.",
            }],
        })
    payload["none_pair_count"] = 63
    payload["positive_pair_count"] = 3
    payload["relation_count"] = 3

    with pytest.raises(ValidationError, match="must form a DAG"):
        RelationPassAArtifact.model_validate(payload)


def test_parser_and_capability_boundaries_fail_closed(
    relation_fixture: SimpleNamespace,
) -> None:
    pretty = relation_fixture.complete.model_dump_json(indent=2).encode("utf-8")
    assert parse_relation_pass_a_worksheet(pretty) == relation_fixture.complete
    with pytest.raises(RelationPassAWorkflowError, match="duplicate object key"):
        parse_relation_pass_a_worksheet(b'{"schema_version":1,"schema_version":1}')
    with pytest.raises(TypeError, match="strict loader"):
        RelationPassACommitmentAuthority()
    with pytest.raises(TypeError, match="strict loader"):
        SealedRelationPassAAuthority()


def test_unsealed_or_untyped_concept_input_cannot_initialize_pass_a() -> None:
    with pytest.raises(
        RelationPassAWorkflowError,
        match="sealed Concept-inventory authority",
    ):
        new_relation_pass_a_worksheet(
            sealed_concepts=object(),  # type: ignore[arg-type]
            reviewer_key_policy=object(),  # type: ignore[arg-type]
            worksheet_id="invalid",
        )


def test_cli_prepare_receipt_is_label_free_and_worksheet_drift_blocks_seal(
    relation_fixture: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    worksheet_path = (
        relation_fixture.paths.private.artifact.parent
        / "relation-pass-a.worksheet.private.json"
    )
    write_new_private_worksheet(
        worksheet_path,
        relation_fixture.complete,
        repository_root=relation_fixture.repository_root,
        human_readable=True,
    )
    context = SimpleNamespace(
        repository_root=relation_fixture.repository_root,
        frozen_protocol=relation_fixture.concepts.protocol,
        source_materialization=relation_fixture.concepts.source,
        concept_reviewer_key_policy=(
            relation_fixture.concepts.reviewer_key_policy
        ),
        relation_reviewer_key_policy=relation_fixture.policy,
        sealed_concepts=relation_fixture.concepts,
        worksheet_path=worksheet_path,
        private_stage_directory=relation_fixture.paths.private.artifact.parent,
        stage_paths=relation_fixture.paths,
    )
    monkeypatch.setattr(relation_command, "_load_context", lambda *_a, **_k: context)
    receipt = relation_command._prepare_relation_pass_a_seal(
        SimpleNamespace()
    )
    assert set(receipt) == {
        "artifact_role",
        "external_signature_required",
        "gold_bundle_sealed",
        "label_release_policy",
        "labels_embargoed_at_commitment",
        "namespace",
        "pair_count",
        "protocol_id",
        "relation_pass_a_artifact_sha256",
        "relation_pass_a_seal_request_sha256",
        "reviewer_id",
        "schema_version",
        "signature_command_template",
        "software_authenticated_minimum_delay",
        "software_authenticated_prediction_blindness",
        "status",
    }
    assert not any(key.endswith("_path") for key in _recursive_keys(receipt))
    receipt_bytes = canonical_json_bytes(receipt)
    for private_label in (
        b"concept-00",
        b"prerequisite",
        b"pedagogical_inference",
        b"positive_pair_count",
        b"No supported relation",
    ):
        assert private_label not in receipt_bytes

    prepared = prepare_relation_pass_a(
        sealed_concepts=relation_fixture.concepts,
        reviewer_key_policy=relation_fixture.policy,
        worksheet=relation_fixture.complete,
    )
    signature = _sign_relation_request(
        tmp_path,
        prepared.seal_request,
        signing_key=relation_fixture.signing_key,
    )
    changed_payload = relation_fixture.complete.model_dump(
        mode="json",
        exclude_none=False,
    )
    negative = next(
        item for item in changed_payload["pair_decisions"] if not item["relations"]
    )
    negative["none_rationale"] = "A changed synthetic negative rationale."
    changed = RelationPassAWorksheet.model_validate(changed_payload)
    worksheet_path.write_text(
        changed.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(RelationPassAWorkflowError, match="changed after"):
        relation_command._seal_relation_pass_a(
            SimpleNamespace(signature=signature)
        )


def test_cli_prepare_preflights_all_private_candidates_before_first_write(
    relation_fixture: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worksheet_path = (
        relation_fixture.paths.private.artifact.parent
        / "relation-pass-a.worksheet.private.json"
    )
    write_new_private_worksheet(
        worksheet_path,
        relation_fixture.complete,
        repository_root=relation_fixture.repository_root,
        human_readable=True,
    )
    context = SimpleNamespace(
        repository_root=relation_fixture.repository_root,
        frozen_protocol=relation_fixture.concepts.protocol,
        source_materialization=relation_fixture.concepts.source,
        concept_reviewer_key_policy=(
            relation_fixture.concepts.reviewer_key_policy
        ),
        relation_reviewer_key_policy=relation_fixture.policy,
        sealed_concepts=relation_fixture.concepts,
        worksheet_path=worksheet_path,
        private_stage_directory=relation_fixture.paths.private.artifact.parent,
        stage_paths=relation_fixture.paths,
    )
    monkeypatch.setattr(
        relation_command,
        "_load_context",
        lambda *_args, **_kwargs: context,
    )
    conflicting_request = (
        context.private_stage_directory
        / "relation-pass-a.seal-request.candidate.private.json"
    )
    conflicting_request.write_bytes(b"conflicting private candidate\n")

    with pytest.raises(AnnotationArtifactError, match="Conflicting"):
        relation_command._prepare_relation_pass_a_seal(SimpleNamespace())

    assert not relation_fixture.paths.private.artifact.exists()


def test_cli_parser_requires_separate_concept_and_relation_policy_commits() -> None:
    parser = relation_command.build_parser()
    args = parser.parse_args([
        "init-relation-pass-a",
        "--concept-reviewer-key-policy-commit",
        "a" * 40,
        "--relation-reviewer-key-policy-commit",
        "b" * 40,
    ])

    assert args.concept_reviewer_key_policy_commit == "a" * 40
    assert args.relation_reviewer_key_policy_commit == "b" * 40


def test_cli_static_error_does_not_print_private_detail_or_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "PRIVATE-QUOTE-AND-C:/private/worksheet.json"

    def fail(_args):
        raise RelationPassAWorkflowError(secret)

    parser = SimpleNamespace(
        parse_args=lambda _argv: SimpleNamespace(handler=fail)
    )
    monkeypatch.setattr(relation_command, "build_parser", lambda: parser)

    assert relation_command.main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert secret not in captured.err
    assert "Traceback" not in captured.err
    assert captured.err == (
        "relation annotation command failed: "
        "Relation Pass A workflow validation failed\n"
    )


def test_neutral_seal_request_canonical_vector_is_stable() -> None:
    request = RelationPassASealRequest(
        schema_version=1,
        artifact_role="golden_graph_relation_pass_a_seal_request",
        namespace=RELATION_PASS_A_ATTESTATION_NAMESPACE,
        protocol_id="fixture-g2-v1",
        frozen_protocol_sha256="1" * 64,
        semantic_source_catalog_sha256="2" * 64,
        chunk_manifest_sha256="3" * 64,
        concept_inventory_sha256="4" * 64,
        concept_inventory_seal_sha256="5" * 64,
        relation_pair_manifest_sha256="6" * 64,
        relation_pass_a_artifact_sha256="7" * 64,
        relation_pass_a_worksheet_sha256="8" * 64,
        reviewer_key_policy_sha256="9" * 64,
        reviewer_key_policy_git_commit="a" * 40,
        reviewer_id="maintainer-01",
        reviewer_actor_kind_declaration="human",
        pass_role="A",
        blind_to_system_proposals_declaration=True,
        software_authenticated_prediction_blindness=False,
        software_authenticated_reviewer_identity=False,
        reviewer_attested_at_utc="2020-08-08T12:00:00Z",
        minimum_delay_hours_before_pass_b=72,
        software_authenticated_minimum_delay=False,
        pair_count=66,
        labels_embargoed_at_commitment=True,
        label_release_policy="after_relation_pass_b_seal",
        approval_statement=(
            "key_control_approval_only_not_proof_of_humanity_or_blindness"
        ),
    )

    assert hashlib.sha256(canonical_json_bytes(request)).hexdigest() == (
        "79d225a6639e788ce29b220ff99f88fd7b690d8a24cd40592c2ee7a7248cb105"
    )
    shortened = request.model_dump(mode="json")
    shortened["minimum_delay_hours_before_pass_b"] = 1
    with pytest.raises(ValidationError):
        RelationPassASealRequest.model_validate(shortened)


def _complete_worksheet(
    worksheet: RelationPassAWorksheet,
    *,
    source_text: str,
    chunk_sha: str,
) -> RelationPassAWorksheet:
    payload = worksheet.model_dump(mode="json", exclude_none=False)
    for decision in payload["pair_decisions"]:
        decision.update({
            "outcome": "none",
            "none_rationale": "No supported relation for this synthetic pair.",
            "relations": [],
        })

    asserted = next(
        item
        for item in payload["pair_decisions"]
        if item["left_concept_key"] == "concept-00"
        and item["right_concept_key"] == "concept-01"
    )
    asserted.update({
        "outcome": "relations",
        "none_rationale": None,
        "relations": [{
            "relation_type": "prerequisite",
            "source_concept_key": "concept-00",
            "target_concept_key": "concept-01",
            "support_basis": "source_asserted",
            "proposal_origin_declaration": "human",
            "evidence": [
                _selection(
                    0,
                    role="relation_assertion",
                    source_text=source_text,
                    chunk_sha=chunk_sha,
                )
            ],
            "review_rationale": "The synthetic source asserts this ordering.",
        }],
    })
    inferred = next(
        item
        for item in payload["pair_decisions"]
        if item["left_concept_key"] == "concept-00"
        and item["right_concept_key"] == "concept-02"
    )
    inferred.update({
        "outcome": "relations",
        "none_rationale": None,
        "relations": [{
            "relation_type": "part_of",
            "source_concept_key": "concept-00",
            "target_concept_key": "concept-02",
            "support_basis": "pedagogical_inference",
            "proposal_origin_declaration": "human",
            "evidence": [
                _selection(
                    0,
                    role="source_endpoint",
                    source_text=source_text,
                    chunk_sha=chunk_sha,
                ),
                _selection(
                    2,
                    role="target_endpoint",
                    source_text=source_text,
                    chunk_sha=chunk_sha,
                ),
            ],
            "review_rationale": "The two supported endpoints justify this edge.",
        }],
    })
    payload.update({
        "worksheet_status": "complete",
        "reviewer_actor_kind_declaration": "human",
        "reviewer_attestation_statement": (
            RELATION_PASS_A_REVIEWER_ATTESTATION
        ),
        "reviewer_attested_at_utc": "2020-08-08T12:00:00Z",
    })
    return RelationPassAWorksheet.model_validate(payload)


def _recursive_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(
            *(_recursive_keys(item) for item in value.values()),
        )
    if isinstance(value, (list, tuple)):
        return set().union(*(_recursive_keys(item) for item in value))
    return set()


def _selection(
    concept_index: int,
    *,
    role: str,
    source_text: str,
    chunk_sha: str,
) -> dict[str, object]:
    quote = f"Evidence sentence for concept {concept_index:02d}."
    source_bytes = source_text.encode("utf-8")
    return {
        "support_role": role,
        "selection": {
            "chunk_ordinal": 0,
            "logical_page_id": "page-0001",
            "semantic_chunk_sha256": chunk_sha,
            "page_global_utf8_start": source_bytes.index(quote.encode("utf-8")),
            "exact_quote": quote,
        },
    }


def _sign_relation_request(
    directory: Path,
    request: object,
    *,
    signing_key: Path,
    namespace: str = RELATION_PASS_A_ATTESTATION_NAMESPACE,
) -> Path:
    request_path = directory / "relation-pass-a-seal-request.json"
    request_path.write_bytes(canonical_json_bytes(request))
    subprocess.run(
        [
            "ssh-keygen",
            "-Y",
            "sign",
            "-f",
            str(signing_key),
            "-n",
            namespace,
            str(request_path),
        ],
        check=True,
        capture_output=True,
    )
    return Path(f"{request_path}.sig")


def _register_relation_policy(
    repository_root: Path,
    *,
    concept_policy: object,
):
    policy = concept_policy.policy
    policy_path = reviewer_key_policy_path(
        repository_root,
        protocol_id=policy.protocol_id,
        reviewer_id=policy.reviewer_id,
    )
    sidecar_path = policy_path.with_suffix(".sha256")
    policy_path.unlink()
    sidecar_path.unlink()
    subprocess.run(
        [
            "git",
            "-C",
            str(repository_root),
            "add",
            "--",
            str(policy_path),
            str(sidecar_path),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository_root),
            "commit",
            "-q",
            "-m",
            "revoke concept-only policy",
        ],
        check=True,
        capture_output=True,
    )
    relation_policy = build_reviewer_key_policy(
        protocol_id=policy.protocol_id,
        frozen_protocol_sha256=policy.frozen_protocol_sha256,
        reviewer_id=policy.reviewer_id,
        allowed_signers_policy_utf8=policy.allowed_signers_policy_utf8,
        allowed_namespaces=G2_ATTESTATION_NAMESPACES,
    )
    publish_reviewer_key_policy(
        repository_root=repository_root,
        policy=relation_policy,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository_root),
            "add",
            "--",
            str(policy_path),
            str(sidecar_path),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository_root),
            "commit",
            "-q",
            "-m",
            "register relation policy",
        ],
        check=True,
        capture_output=True,
    )
    registration_commit = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return load_repository_reviewer_key_policy(
        repository_root=repository_root,
        protocol_id=relation_policy.protocol_id,
        frozen_protocol_sha256=relation_policy.frozen_protocol_sha256,
        reviewer_id=relation_policy.reviewer_id,
        registration_commit_sha=registration_commit,
    )
