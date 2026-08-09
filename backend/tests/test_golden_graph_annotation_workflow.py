from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import subprocess
import traceback
from urllib.parse import quote

import pytest
from pydantic import ValidationError

import golden_graph.annotation_command as annotation_command
import golden_graph.annotation_evidence as annotation_evidence
import golden_graph.annotation_workflow as workflow
import golden_graph.reviewer_policy as reviewer_policy
from golden_graph.annotation_artifacts import AnnotationArtifactError
from golden_graph.annotation_models import (
    ConceptAnnotationWorksheet,
    G2_ATTESTATION_NAMESPACES,
    RelationPairManifest,
)
from golden_graph.annotation_workflow import (
    CONCEPT_ATTESTATION_NAMESPACE,
    CONCEPT_REVIEWER_ATTESTATION,
    ConceptAnnotationWorkflowError,
    ConceptStagePaths,
    PreparedConceptInventory,
    SealedConceptInventoryAuthority,
    SignedConceptInventory,
    load_sealed_concept_inventory,
    new_concept_annotation_worksheet,
    parse_concept_annotation_worksheet,
    prepare_concept_inventory,
    publish_concept_inventory_stage,
    signoff_prepared_concept_inventory,
)
from golden_graph.canonical_io import canonical_json_bytes
from golden_graph.reviewer_policy import (
    build_reviewer_key_policy,
    load_repository_reviewer_key_policy,
    publish_reviewer_key_policy,
)


@pytest.fixture
def annotation_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> SimpleNamespace:
    monkeypatch.setattr(workflow, "_validate_upstream", lambda *_args, **_kwargs: None)
    source_text = "\n".join(
        f"Evidence sentence for concept {index:02d}."
        for index in range(20)
    )
    source_bytes = source_text.encode("utf-8")
    chunk_sha = hashlib.sha256(source_bytes).hexdigest()
    protocol_sha = "1" * 64
    catalog_sha = "2" * 64
    manifest_sha = "3" * 64
    private_sha = "4" * 64
    protocol_id = "fixture-g2-v1"
    chunk = SimpleNamespace(
        ordinal=0,
        text=source_text,
        text_hash=chunk_sha,
        locator=SimpleNamespace(
            metadata={
                "logical_page_id": "page-0001",
                "start_offset": 0,
                "end_offset": len(source_bytes),
            }
        ),
    )
    source = SimpleNamespace(
        artifact_sha256=private_sha,
        materialization=SimpleNamespace(
            protocol_id=protocol_id,
            source_catalog_sha256=catalog_sha,
            chunk_manifest_sha256=manifest_sha,
            course_source_chunks=(chunk,),
            chunk_manifest=SimpleNamespace(
                chunks=(
                    SimpleNamespace(
                        ordinal=0,
                        semantic_chunk_sha256=chunk_sha,
                    ),
                )
            ),
        ),
    )
    evidence_source = annotation_evidence._issue_annotation_evidence_source_authority(
        private_materialization_sha256=private_sha,
        chunks=(
            annotation_evidence._FrozenEvidenceChunk(
                ordinal=0,
                logical_page_id="page-0001",
                window_start=0,
                window_end=len(source_bytes),
                semantic_chunk_sha256=chunk_sha,
                text=source_text,
                utf8_bytes=source_bytes,
            ),
        ),
    )
    monkeypatch.setattr(
        workflow,
        "_bind_annotation_source",
        lambda _source_materialization: evidence_source,
    )
    frozen = SimpleNamespace(
        protocol_sha256=protocol_sha,
        protocol=SimpleNamespace(
            protocol_id=protocol_id,
            protocol_status="frozen",
            projection=SimpleNamespace(
                semantic_source_catalog_sha256=catalog_sha,
                chunk_manifest_sha256=manifest_sha,
            ),
            review=SimpleNamespace(
                reviewer_id="maintainer-01",
                annotation_guide_sha256="5" * 64,
                both_passes_blind_to_system_proposals=True,
                pass_b_blind_to_pass_a_labels=True,
                minimum_delay_hours=72,
            ),
            acquisition=SimpleNamespace(corpus_id="fixture-corpus"),
        ),
    )
    reviewer_key_policy, signing_key = _registered_reviewer_key_policy(
        tmp_path,
        protocol_id=protocol_id,
        frozen_protocol_sha256=protocol_sha,
        reviewer_id="maintainer-01",
    )
    draft = new_concept_annotation_worksheet(
        frozen_protocol=frozen,
        source_materialization=source,
        reviewer_key_policy=reviewer_key_policy,
        worksheet_id="fixture-concepts-v1",
    )
    candidates = []
    for index in range(12):
        quote = f"Evidence sentence for concept {index:02d}."
        candidates.append({
            "candidate_key": f"concept-{index:02d}",
            "candidate_label": f"Candidate {index:02d}",
            "decision": "include",
            "preferred_name": f"Concept {index:02d}",
            "short_definition": f"A concise definition for item {index:02d}.",
            "aliases": [f"C{index:02d}"],
            "evidence": [
                {
                    "chunk_ordinal": 0,
                    "logical_page_id": "page-0001",
                    "semantic_chunk_sha256": chunk_sha,
                    "page_global_utf8_start": source_bytes.index(
                        quote.encode("utf-8")
                    ),
                    "exact_quote": quote,
                }
            ],
            "decision_rationale": f"This is independently teachable item {index:02d}.",
        })
    payload = draft.model_dump(mode="json", exclude_none=False)
    payload.update({
        "worksheet_status": "complete",
        "reviewer_actor_kind_declaration": "human",
        "reviewer_attestation_statement": CONCEPT_REVIEWER_ATTESTATION,
        "reviewer_attested_at_utc": "2020-08-08T12:00:00Z",
        "candidates": candidates,
    })
    complete = ConceptAnnotationWorksheet.model_validate(payload)
    return SimpleNamespace(
        frozen=frozen,
        source=source,
        evidence_source=evidence_source,
        source_text=source_text,
        chunk_sha=chunk_sha,
        reviewer_key_policy=reviewer_key_policy,
        signing_key=signing_key,
        worksheet=complete,
    )


def test_empty_packet_contains_no_labels_or_attestation(
    annotation_fixture: SimpleNamespace,
) -> None:
    draft = new_concept_annotation_worksheet(
        frozen_protocol=annotation_fixture.frozen,
        source_materialization=annotation_fixture.source,
        reviewer_key_policy=annotation_fixture.reviewer_key_policy,
        worksheet_id="another-concept-session",
    )

    assert draft.worksheet_status == "draft"
    assert draft.candidates == ()
    assert draft.reviewer_actor_kind_declaration is None
    assert draft.reviewer_attestation_statement is None
    assert draft.software_authenticated_reviewer_identity is False
    assert draft.blind_to_system_proposals_declaration is True
    assert draft.software_authenticated_prediction_blindness is False


def test_prepare_resolves_quotes_but_public_leaves_never_contain_them(
    annotation_fixture: SimpleNamespace,
) -> None:
    prepared = prepare_concept_inventory(
        frozen_protocol=annotation_fixture.frozen,
        source_materialization=annotation_fixture.source,
        reviewer_key_policy=annotation_fixture.reviewer_key_policy,
        worksheet=annotation_fixture.worksheet,
    )

    assert prepared.inventory.concept_count == 12
    assert prepared.alias_table.concept_count == 12
    assert prepared.seal_request.concept_count == 12
    assert prepared.seal_request.total_candidate_count == 12
    assert prepared.seal_request.excluded_candidate_count == 0
    public_bytes = b"".join(
        canonical_json_bytes(artifact)
        for artifact in (
            prepared.inventory,
            prepared.alias_table,
            prepared.seal_request,
        )
    )
    assert b"Evidence sentence for concept" not in public_bytes
    assert b'"exact_quote"' not in public_bytes
    assert prepared.inventory.concepts[0].evidence[0].semantic_span_sha256 == (
        hashlib.sha256(b"Evidence sentence for concept 00.").hexdigest()
    )


def test_twelve_concepts_sign_and_generate_all_sixty_six_pairs(
    annotation_fixture: SimpleNamespace,
    tmp_path: Path,
) -> None:
    prepared = prepare_concept_inventory(
        frozen_protocol=annotation_fixture.frozen,
        source_materialization=annotation_fixture.source,
        reviewer_key_policy=annotation_fixture.reviewer_key_policy,
        worksheet=annotation_fixture.worksheet,
    )
    signature = _sign_request(
        tmp_path,
        prepared.seal_request,
        signing_key=annotation_fixture.signing_key,
    )

    signed = signoff_prepared_concept_inventory(
        prepared=prepared,
        reviewer_key_policy=annotation_fixture.reviewer_key_policy,
        signature_path=signature,
    )

    assert signed.seal.status == "concept_inventory_only_not_gold_bundle"
    assert signed.seal.software_authenticated_reviewer_identity is False
    assert signed.seal.blind_to_system_proposals_declaration is True
    assert signed.seal.software_authenticated_prediction_blindness is False
    assert signed.seal.detached_attestation.key_control_only_not_proof_of_humanity
    assert signed.pair_manifest.pair_count == 66
    assert len(signed.pair_manifest.pairs) == 66
    assert signed.pair_manifest.pairs[0].left_concept_key == "concept-00"
    assert signed.pair_manifest.pairs[-1].right_concept_key == "concept-11"


def test_twenty_concepts_generate_all_one_hundred_ninety_pairs(
    annotation_fixture: SimpleNamespace,
) -> None:
    payload = annotation_fixture.worksheet.model_dump(mode="json")
    source_bytes = annotation_fixture.source_text.encode("utf-8")
    for index in range(12, 20):
        quote = f"Evidence sentence for concept {index:02d}."
        payload["candidates"].append({
            "candidate_key": f"concept-{index:02d}",
            "candidate_label": f"Candidate {index:02d}",
            "decision": "include",
            "preferred_name": f"Concept {index:02d}",
            "short_definition": f"A concise definition for item {index:02d}.",
            "aliases": [f"C{index:02d}"],
            "evidence": [{
                "chunk_ordinal": 0,
                "logical_page_id": "page-0001",
                "semantic_chunk_sha256": annotation_fixture.chunk_sha,
                "page_global_utf8_start": source_bytes.index(
                    quote.encode("utf-8")
                ),
                "exact_quote": quote,
            }],
            "decision_rationale": (
                f"This is independently teachable item {index:02d}."
            ),
        })
    worksheet = ConceptAnnotationWorksheet.model_validate(payload)
    prepared = prepare_concept_inventory(
        frozen_protocol=annotation_fixture.frozen,
        source_materialization=annotation_fixture.source,
        reviewer_key_policy=annotation_fixture.reviewer_key_policy,
        worksheet=worksheet,
    )
    pairs = workflow._build_pair_manifest(
        prepared.inventory,
        concept_inventory_sha256=prepared.inventory_sha256,
        seal_sha256="9" * 64,
    )

    assert prepared.inventory.concept_count == 20
    assert pairs.pair_count == 190
    assert len(pairs.pairs) == 190


def test_publish_then_deep_reload_verifies_source_signature_aliases_and_pairs(
    annotation_fixture: SimpleNamespace,
    tmp_path: Path,
) -> None:
    prepared = prepare_concept_inventory(
        frozen_protocol=annotation_fixture.frozen,
        source_materialization=annotation_fixture.source,
        reviewer_key_policy=annotation_fixture.reviewer_key_policy,
        worksheet=annotation_fixture.worksheet,
    )
    signature = _sign_request(
        tmp_path,
        prepared.seal_request,
        signing_key=annotation_fixture.signing_key,
    )
    signed = signoff_prepared_concept_inventory(
        prepared=prepared,
        reviewer_key_policy=annotation_fixture.reviewer_key_policy,
        signature_path=signature,
    )
    public_root = tmp_path / "public"
    public_root.mkdir()
    paths = _paths(public_root)

    authority = publish_concept_inventory_stage(
        signed=signed,
        paths=paths,
        public_artifact_root=public_root,
        frozen_protocol=annotation_fixture.frozen,
        source_materialization=annotation_fixture.source,
        reviewer_key_policy=annotation_fixture.reviewer_key_policy,
    )
    replay = load_sealed_concept_inventory(
        paths=paths,
        public_artifact_root=public_root,
        frozen_protocol=annotation_fixture.frozen,
        source_materialization=annotation_fixture.source,
        reviewer_key_policy=annotation_fixture.reviewer_key_policy,
    )

    assert authority.seal.artifact_sha256 == replay.seal.artifact_sha256
    assert authority.pair_manifest.artifact.pair_count == 66
    assert authority.key_control_receipt.signer_identity == "maintainer-01"
    with pytest.raises(TypeError, match="strict loader"):
        SealedConceptInventoryAuthority()


def test_actor_string_or_unsigned_prepared_value_cannot_issue_sealed_authority(
    annotation_fixture: SimpleNamespace,
) -> None:
    prepared = prepare_concept_inventory(
        frozen_protocol=annotation_fixture.frozen,
        source_materialization=annotation_fixture.source,
        reviewer_key_policy=annotation_fixture.reviewer_key_policy,
        worksheet=annotation_fixture.worksheet,
    )

    assert prepared.inventory.reviewer_actor_kind_declaration == "human"
    with pytest.raises(TypeError):
        PreparedConceptInventory()
    with pytest.raises(TypeError):
        SignedConceptInventory()


def test_wrong_evidence_offset_and_long_source_copy_fail_closed(
    annotation_fixture: SimpleNamespace,
) -> None:
    payload = annotation_fixture.worksheet.model_dump(mode="json")
    payload["candidates"][0]["evidence"][0]["page_global_utf8_start"] = 1
    wrong_offset = ConceptAnnotationWorksheet.model_validate(payload)
    with pytest.raises(ConceptAnnotationWorkflowError, match="does not resolve"):
        prepare_concept_inventory(
            frozen_protocol=annotation_fixture.frozen,
            source_materialization=annotation_fixture.source,
            reviewer_key_policy=annotation_fixture.reviewer_key_policy,
            worksheet=wrong_offset,
        )

    copied_payload = annotation_fixture.worksheet.model_dump(mode="json")
    copied_payload["candidates"][0]["short_definition"] = (
        " ".join(annotation_fixture.source_text[:120].split())
    )
    copied = ConceptAnnotationWorksheet.model_validate(copied_payload)
    with pytest.raises(ConceptAnnotationWorkflowError, match="verbatim"):
        prepare_concept_inventory(
            frozen_protocol=annotation_fixture.frozen,
            source_materialization=annotation_fixture.source,
            reviewer_key_policy=annotation_fixture.reviewer_key_policy,
            worksheet=copied,
        )


def test_unicode_alias_collision_and_invalid_calendar_time_are_rejected(
    annotation_fixture: SimpleNamespace,
) -> None:
    collision = annotation_fixture.worksheet.model_dump(mode="json")
    collision["candidates"][1]["aliases"] = ["c00"]
    with pytest.raises(ValidationError, match="globally collision-free"):
        ConceptAnnotationWorksheet.model_validate(collision)

    invalid_time = annotation_fixture.worksheet.model_dump(mode="json")
    invalid_time["reviewer_attested_at_utc"] = "2026-02-31T12:00:00Z"
    with pytest.raises(ValidationError, match="real UTC time"):
        ConceptAnnotationWorksheet.model_validate(invalid_time)


def test_future_reviewer_attestation_and_policy_rebinding_fail_closed(
    annotation_fixture: SimpleNamespace,
) -> None:
    future_payload = annotation_fixture.worksheet.model_dump(mode="json")
    future_payload["reviewer_attested_at_utc"] = "2999-01-01T00:00:00Z"
    future = ConceptAnnotationWorksheet.model_validate(future_payload)
    with pytest.raises(ConceptAnnotationWorkflowError, match="future"):
        prepare_concept_inventory(
            frozen_protocol=annotation_fixture.frozen,
            source_materialization=annotation_fixture.source,
            reviewer_key_policy=annotation_fixture.reviewer_key_policy,
            worksheet=future,
        )

    rebound_payload = annotation_fixture.worksheet.model_dump(mode="json")
    rebound_payload["reviewer_key_policy_sha256"] = "9" * 64
    rebound = ConceptAnnotationWorksheet.model_validate(rebound_payload)
    with pytest.raises(ConceptAnnotationWorkflowError, match="binding"):
        prepare_concept_inventory(
            frozen_protocol=annotation_fixture.frozen,
            source_materialization=annotation_fixture.source,
            reviewer_key_policy=annotation_fixture.reviewer_key_policy,
            worksheet=rebound,
        )


def test_aggregate_and_format_control_source_copy_scans_fail_closed(
    annotation_fixture: SimpleNamespace,
) -> None:
    fragment = " ".join(annotation_fixture.source_text[:180].split())
    split_at = fragment.find(" ", 70)
    split_payload = annotation_fixture.worksheet.model_dump(mode="json")
    split_payload["candidates"][0]["preferred_name"] = fragment[:split_at]
    split_payload["candidates"][0]["short_definition"] = fragment[split_at + 1 :]
    split_copy = ConceptAnnotationWorksheet.model_validate(split_payload)
    with pytest.raises(ConceptAnnotationWorkflowError, match="verbatim"):
        prepare_concept_inventory(
            frozen_protocol=annotation_fixture.frozen,
            source_materialization=annotation_fixture.source,
            reviewer_key_policy=annotation_fixture.reviewer_key_policy,
            worksheet=split_copy,
        )

    hidden_payload = annotation_fixture.worksheet.model_dump(mode="json")
    hidden_payload["candidates"][0]["short_definition"] = "\u200b".join(
        fragment
    )
    hidden_copy = ConceptAnnotationWorksheet.model_validate(hidden_payload)
    with pytest.raises(ConceptAnnotationWorkflowError, match="invisible"):
        prepare_concept_inventory(
            frozen_protocol=annotation_fixture.frozen,
            source_materialization=annotation_fixture.source,
            reviewer_key_policy=annotation_fixture.reviewer_key_policy,
            worksheet=hidden_copy,
        )


def test_public_privacy_scanner_rejects_path_and_uri_families() -> None:
    for value in (
        r"\\server\share\private.txt",
        "/tmp/private.txt",
        "/mnt/private/source.txt",
        "/root/private/course.pdf",
        "/etc/private/course.conf",
        "/opt/private/model.bin",
        "file:///Users/reviewer/private.pdf",
        "file%3A%2F%2F%2Froot%2Fprivate.pdf",
        "../private/source.txt",
        "%2e%2e%2fprivate%2fsource.txt",
        "~/private/source.txt",
    ):
        with pytest.raises(ConceptAnnotationWorkflowError, match="private path"):
            workflow._reject_public_source_copy(
                (value,),
                source_authority=_privacy_authority("unrelated source"),
            )


def test_variation_selectors_cannot_hide_public_source_copy() -> None:
    source = "twelve exact source tokens must remain visible to this privacy scanner"
    hidden = "\ufe0f".join(source)

    with pytest.raises(ConceptAnnotationWorkflowError, match="invisible"):
        workflow._reject_public_source_copy(
            (hidden,),
            source_authority=_privacy_authority(source),
        )


def test_percent_escapes_cannot_hide_source_or_private_paths() -> None:
    source = (
        "twelve exact private source tokens must never become a reversible "
        "public representation in Concept prose"
    )
    hidden_with_variation_selectors = "\ufe0f".join(source)
    for value in (
        quote(source, safe=""),
        quote(hidden_with_variation_selectors, safe=""),
        "%2525252Froot%2525252Fprivate.pdf",
        "%2525252e%2525252e%2525252fsecret.txt",
    ):
        with pytest.raises(ConceptAnnotationWorkflowError, match="percent escape"):
            workflow._reject_public_source_copy(
                (value,),
                source_authority=_privacy_authority(source),
            )

    workflow._reject_public_source_copy(
        ("A measured accuracy can be 100% after rounding.",),
        source_authority=_privacy_authority(source),
    )


def test_pair_manifest_rejects_missing_extra_or_reordered_pairs(
    annotation_fixture: SimpleNamespace,
) -> None:
    prepared = prepare_concept_inventory(
        frozen_protocol=annotation_fixture.frozen,
        source_materialization=annotation_fixture.source,
        reviewer_key_policy=annotation_fixture.reviewer_key_policy,
        worksheet=annotation_fixture.worksheet,
    )
    keys = tuple(concept.concept_key for concept in prepared.inventory.concepts)
    valid = workflow._build_pair_manifest(
        prepared.inventory,
        concept_inventory_sha256=prepared.inventory_sha256,
        seal_sha256="9" * 64,
    )
    assert valid.pair_count == len(keys) * (len(keys) - 1) // 2
    for changed_pairs in (valid.pairs[:-1], tuple(reversed(valid.pairs))):
        payload = valid.model_dump(mode="json")
        payload["pairs"] = [pair.model_dump(mode="json") for pair in changed_pairs]
        payload["pair_count"] = len(changed_pairs)
        with pytest.raises(
            ValidationError,
            match=r"at least 66|greater than or equal to 66|complete universe",
        ):
            RelationPairManifest.model_validate(payload)


def test_human_edited_pretty_json_parses_but_duplicate_keys_do_not(
    annotation_fixture: SimpleNamespace,
) -> None:
    pretty = json.dumps(
        annotation_fixture.worksheet.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    parsed = parse_concept_annotation_worksheet(pretty)
    assert parsed == annotation_fixture.worksheet

    with pytest.raises(ConceptAnnotationWorkflowError, match="duplicate"):
        parse_concept_annotation_worksheet(
            b'{"schema_version":1,"schema_version":1}'
        )


def test_loaded_evidence_hash_tamper_fails_even_with_rewritten_sidecar(
    annotation_fixture: SimpleNamespace,
    tmp_path: Path,
) -> None:
    prepared = prepare_concept_inventory(
        frozen_protocol=annotation_fixture.frozen,
        source_materialization=annotation_fixture.source,
        reviewer_key_policy=annotation_fixture.reviewer_key_policy,
        worksheet=annotation_fixture.worksheet,
    )
    signature = _sign_request(
        tmp_path,
        prepared.seal_request,
        signing_key=annotation_fixture.signing_key,
    )
    signed = signoff_prepared_concept_inventory(
        prepared=prepared,
        reviewer_key_policy=annotation_fixture.reviewer_key_policy,
        signature_path=signature,
    )
    public_root = tmp_path / "public"
    public_root.mkdir()
    paths = _paths(public_root)
    publish_concept_inventory_stage(
        signed=signed,
        paths=paths,
        public_artifact_root=public_root,
        frozen_protocol=annotation_fixture.frozen,
        source_materialization=annotation_fixture.source,
        reviewer_key_policy=annotation_fixture.reviewer_key_policy,
    )
    payload = json.loads(paths.inventory.read_text(encoding="utf-8"))
    payload["concepts"][0]["evidence"][0]["semantic_span_sha256"] = "0" * 64
    changed = canonical_json_bytes(payload)
    paths.inventory.write_bytes(changed)
    paths.inventory.with_suffix(".sha256").write_text(
        f"{hashlib.sha256(changed).hexdigest()}  {paths.inventory.name}\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ConceptAnnotationWorkflowError, match="span hash"):
        load_sealed_concept_inventory(
            paths=paths,
            public_artifact_root=public_root,
            frozen_protocol=annotation_fixture.frozen,
            source_materialization=annotation_fixture.source,
            reviewer_key_policy=annotation_fixture.reviewer_key_policy,
        )


def test_unregistered_fresh_key_cannot_self_authorize_concept_seal(
    annotation_fixture: SimpleNamespace,
    tmp_path: Path,
) -> None:
    prepared = prepare_concept_inventory(
        frozen_protocol=annotation_fixture.frozen,
        source_materialization=annotation_fixture.source,
        reviewer_key_policy=annotation_fixture.reviewer_key_policy,
        worksheet=annotation_fixture.worksheet,
    )
    unregistered_key = tmp_path / "unregistered-key"
    _generate_signing_key(unregistered_key)
    signature = _sign_request(
        tmp_path,
        prepared.seal_request,
        signing_key=unregistered_key,
    )

    with pytest.raises(ConceptAnnotationWorkflowError, match="could not be verified"):
        signoff_prepared_concept_inventory(
            prepared=prepared,
            reviewer_key_policy=annotation_fixture.reviewer_key_policy,
            signature_path=signature,
        )


def test_stale_active_policy_cannot_authorize_after_git_revocation(
    annotation_fixture: SimpleNamespace,
    tmp_path: Path,
) -> None:
    prepared = prepare_concept_inventory(
        frozen_protocol=annotation_fixture.frozen,
        source_materialization=annotation_fixture.source,
        reviewer_key_policy=annotation_fixture.reviewer_key_policy,
        worksheet=annotation_fixture.worksheet,
    )
    stale_policy = annotation_fixture.reviewer_key_policy
    root = stale_policy.repository_root
    relative_policy = stale_policy.artifact_path.relative_to(root).as_posix()
    relative_sidecar = stale_policy.artifact_path.with_suffix(".sha256").relative_to(
        root
    ).as_posix()
    subprocess.run(
        ["git", "-C", str(root), "rm", "-q", "--", relative_policy, relative_sidecar],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "-m", "revoke reviewer key"],
        check=True,
        capture_output=True,
    )
    current_head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert current_head != stale_policy.verified_head_sha
    signature = _sign_request(
        tmp_path,
        prepared.seal_request,
        signing_key=annotation_fixture.signing_key,
    )

    with pytest.raises(
        ConceptAnnotationWorkflowError,
        match="repository-issued reviewer-key policy",
    ):
        signoff_prepared_concept_inventory(
            prepared=prepared,
            reviewer_key_policy=stale_policy,
            signature_path=signature,
        )


def test_signoff_traceback_does_not_leak_private_signature_path(
    annotation_fixture: SimpleNamespace,
    tmp_path: Path,
) -> None:
    prepared = prepare_concept_inventory(
        frozen_protocol=annotation_fixture.frozen,
        source_materialization=annotation_fixture.source,
        reviewer_key_policy=annotation_fixture.reviewer_key_policy,
        worksheet=annotation_fixture.worksheet,
    )
    marker = "PRIVATE-SIGNATURE-PATH-SENTINEL"
    private_signature_path = tmp_path / marker / "missing.sig"

    with pytest.raises(
        ConceptAnnotationWorkflowError,
        match="key attestation could not be verified",
    ) as captured:
        signoff_prepared_concept_inventory(
            prepared=prepared,
            reviewer_key_policy=annotation_fixture.reviewer_key_policy,
            signature_path=private_signature_path,
        )

    rendered = "".join(
        traceback.format_exception(
            captured.type,
            captured.value,
            captured.tb,
        )
    )
    assert marker not in rendered
    assert str(private_signature_path) not in rendered


def test_revoked_concept_only_policy_still_deep_reloads_its_historical_seal(
    annotation_fixture: SimpleNamespace,
    tmp_path: Path,
) -> None:
    active_policy = annotation_fixture.reviewer_key_policy
    assert active_policy.policy.allowed_namespaces == (
        CONCEPT_ATTESTATION_NAMESPACE,
    )
    prepared = prepare_concept_inventory(
        frozen_protocol=annotation_fixture.frozen,
        source_materialization=annotation_fixture.source,
        reviewer_key_policy=active_policy,
        worksheet=annotation_fixture.worksheet,
    )
    signature = _sign_request(
        tmp_path,
        prepared.seal_request,
        signing_key=annotation_fixture.signing_key,
    )
    signed = signoff_prepared_concept_inventory(
        prepared=prepared,
        reviewer_key_policy=active_policy,
        signature_path=signature,
    )
    public_root = tmp_path / "historical-public"
    public_root.mkdir()
    paths = _paths(public_root)
    publish_concept_inventory_stage(
        signed=signed,
        paths=paths,
        public_artifact_root=public_root,
        frozen_protocol=annotation_fixture.frozen,
        source_materialization=annotation_fixture.source,
        reviewer_key_policy=active_policy,
    )
    historical_policy = reviewer_policy._issue_policy_authority(
        policy=active_policy.policy,
        repository_root=active_policy.repository_root,
        artifact_path=active_policy.artifact_path,
        policy_sha256=active_policy.policy_sha256,
        registration_commit_sha=active_policy.registration_commit_sha,
        verified_head_sha=active_policy.verified_head_sha,
        policy_blob_oid=active_policy.policy_blob_oid,
        active_at_verified_head=False,
    )

    reloaded = load_sealed_concept_inventory(
        paths=paths,
        public_artifact_root=public_root,
        frozen_protocol=annotation_fixture.frozen,
        source_materialization=annotation_fixture.source,
        reviewer_key_policy=historical_policy,
    )

    assert reloaded.seal.artifact == signed.seal
    assert reloaded.reviewer_key_policy.active_at_verified_head is False
    assert reloaded.key_control_receipt.namespace == CONCEPT_ATTESTATION_NAMESPACE


def test_publication_preflights_all_leaves_and_publishes_seal_last(
    annotation_fixture: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = prepare_concept_inventory(
        frozen_protocol=annotation_fixture.frozen,
        source_materialization=annotation_fixture.source,
        reviewer_key_policy=annotation_fixture.reviewer_key_policy,
        worksheet=annotation_fixture.worksheet,
    )
    signature = _sign_request(
        tmp_path,
        prepared.seal_request,
        signing_key=annotation_fixture.signing_key,
    )
    signed = signoff_prepared_concept_inventory(
        prepared=prepared,
        reviewer_key_policy=annotation_fixture.reviewer_key_policy,
        signature_path=signature,
    )
    public_root = tmp_path / "preflight-public"
    public_root.mkdir()
    paths = _paths(public_root)
    paths.seal.write_text("{}", encoding="utf-8", newline="\n")

    with pytest.raises(AnnotationArtifactError, match="Conflicting immutable"):
        publish_concept_inventory_stage(
            signed=signed,
            paths=paths,
            public_artifact_root=public_root,
            frozen_protocol=annotation_fixture.frozen,
            source_materialization=annotation_fixture.source,
            reviewer_key_policy=annotation_fixture.reviewer_key_policy,
        )
    for path in (
        paths.inventory,
        paths.alias_table,
        paths.seal_request,
        paths.attestation,
        paths.pair_manifest,
    ):
        assert not path.exists()
        assert not path.with_suffix(".sha256").exists()

    paths.seal.unlink()
    real_publish = workflow.publish_canonical_artifact

    def crash_before_root(path, artifact, **kwargs):
        if Path(path) == paths.seal:
            raise AnnotationArtifactError("simulated crash before DAG root")
        return real_publish(path, artifact, **kwargs)

    monkeypatch.setattr(workflow, "publish_canonical_artifact", crash_before_root)
    with pytest.raises(AnnotationArtifactError, match="simulated crash"):
        publish_concept_inventory_stage(
            signed=signed,
            paths=paths,
            public_artifact_root=public_root,
            frozen_protocol=annotation_fixture.frozen,
            source_materialization=annotation_fixture.source,
            reviewer_key_policy=annotation_fixture.reviewer_key_policy,
        )
    assert not paths.seal.exists()
    with pytest.raises(AnnotationArtifactError):
        load_sealed_concept_inventory(
            paths=paths,
            public_artifact_root=public_root,
            frozen_protocol=annotation_fixture.frozen,
            source_materialization=annotation_fixture.source,
            reviewer_key_policy=annotation_fixture.reviewer_key_policy,
        )

    monkeypatch.setattr(workflow, "publish_canonical_artifact", real_publish)
    authority = publish_concept_inventory_stage(
        signed=signed,
        paths=paths,
        public_artifact_root=public_root,
        frozen_protocol=annotation_fixture.frozen,
        source_materialization=annotation_fixture.source,
        reviewer_key_policy=annotation_fixture.reviewer_key_policy,
    )
    assert authority.seal.artifact_path == paths.seal.resolve()


def test_cli_error_boundary_never_echoes_private_values(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    sensitive = f"{tmp_path} PRIVATE SOURCE QUOTE"

    def fail(_args):
        raise ConceptAnnotationWorkflowError(sensitive)

    parser = SimpleNamespace(
        parse_args=lambda _argv: SimpleNamespace(handler=fail)
    )
    monkeypatch.setattr(annotation_command, "build_parser", lambda: parser)

    assert annotation_command.main([]) == 2
    stderr = capsys.readouterr().err
    assert "annotation workflow validation failed" in stderr
    assert str(tmp_path) not in stderr
    assert "PRIVATE SOURCE QUOTE" not in stderr


def test_cli_requires_historical_policy_commit_and_not_caller_owned_policy() -> None:
    commit = "a" * 40
    seal = annotation_command.build_parser().parse_args([
        "seal-concepts",
        "--reviewer-key-policy-commit",
        commit,
        "--signature",
        "request.sig",
    ])
    prepare = annotation_command.build_parser().parse_args([
        "prepare-reviewer-key-policy",
        "--allowed-signers",
        "reviewer.pub.policy",
    ])

    assert seal.reviewer_key_policy_commit == commit
    assert seal.signature == Path("request.sig")
    assert not hasattr(seal, "allowed_signers")
    assert prepare.allowed_signers == Path("reviewer.pub.policy")


def test_policy_preparation_registers_every_g2_attestation_namespace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    allowed_signers = tmp_path / "reviewer.pub.policy"
    allowed_signers.write_text("public reviewer policy\n", encoding="ascii")
    frozen = SimpleNamespace(
        protocol_sha256="1" * 64,
        protocol=SimpleNamespace(
            protocol_id="fixture-g2-v1",
            review=SimpleNamespace(reviewer_id="maintainer-01"),
        ),
    )
    context = SimpleNamespace(repository_root=tmp_path, frozen_protocol=frozen)
    observed: dict[str, object] = {}
    candidate_policy = object()

    def build_policy(**kwargs):
        observed.update(kwargs)
        return candidate_policy

    monkeypatch.setattr(
        annotation_command,
        "_load_protocol_context",
        lambda _args: context,
    )
    monkeypatch.setattr(annotation_command, "build_reviewer_key_policy", build_policy)
    monkeypatch.setattr(
        annotation_command,
        "publish_reviewer_key_policy",
        lambda **_kwargs: "2" * 64,
    )
    monkeypatch.setattr(
        annotation_command,
        "reviewer_key_policy_path",
        lambda *_args, **_kwargs: tmp_path / "policy.json",
    )

    receipt = annotation_command._prepare_reviewer_key_policy(
        SimpleNamespace(allowed_signers=allowed_signers)
    )

    assert observed["allowed_namespaces"] == G2_ATTESTATION_NAMESPACES
    assert G2_ATTESTATION_NAMESPACES == tuple(sorted(G2_ATTESTATION_NAMESPACES))
    assert len(G2_ATTESTATION_NAMESPACES) == 4
    assert receipt["status"] == "commit_and_push_policy_before_annotation"


def test_cli_verify_concepts_uses_historical_policy_capability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = SimpleNamespace(
        repository_root=tmp_path,
        public_paths=object(),
        frozen_protocol=object(),
        source_materialization=object(),
        reviewer_key_policy=SimpleNamespace(active_at_verified_head=False),
    )
    observed: dict[str, object] = {}

    def load_context(_args, *, require_active_policy=True):
        observed["require_active_policy"] = require_active_policy
        return context

    sealed_authority = object()
    monkeypatch.setattr(annotation_command, "_load_context", load_context)
    monkeypatch.setattr(
        annotation_command,
        "_public_artifact_root",
        lambda _repository_root: tmp_path,
    )
    monkeypatch.setattr(
        annotation_command,
        "load_sealed_concept_inventory",
        lambda **_kwargs: sealed_authority,
    )
    monkeypatch.setattr(
        annotation_command,
        "_sealed_receipt",
        lambda authority, *, role: {
            "authority": authority,
            "role": role,
        },
    )

    receipt = annotation_command._verify_concepts(SimpleNamespace())

    assert observed["require_active_policy"] is False
    assert receipt == {
        "authority": sealed_authority,
        "role": "golden_graph_concept_seal_verification_receipt",
    }


def _privacy_authority(source_text: str):
    source_bytes = source_text.encode("utf-8")
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    return annotation_evidence._issue_annotation_evidence_source_authority(
        private_materialization_sha256="d" * 64,
        chunks=(
            annotation_evidence._FrozenEvidenceChunk(
                ordinal=0,
                logical_page_id="page-0001",
                window_start=0,
                window_end=len(source_bytes),
                semantic_chunk_sha256=source_sha256,
                text=source_text,
                utf8_bytes=source_bytes,
            ),
        ),
    )


def _generate_signing_key(key: Path) -> None:
    ssh_keygen = shutil.which("ssh-keygen")
    if ssh_keygen is None:
        pytest.skip("OpenSSH ssh-keygen is unavailable")
    subprocess.run(
        [
            ssh_keygen,
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            "g2-test",
            "-f",
            str(key),
        ],
        check=True,
        capture_output=True,
    )


def _registered_reviewer_key_policy(
    repository_root: Path,
    *,
    protocol_id: str,
    frozen_protocol_sha256: str,
    reviewer_id: str,
):
    subprocess.run(
        ["git", "init", "--quiet", str(repository_root)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repository_root), "config", "user.name", "G2 Test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository_root),
            "config",
            "user.email",
            "g2-test@example.invalid",
        ],
        check=True,
        capture_output=True,
    )
    seed = repository_root / "seed.txt"
    seed.write_text("seed\n", encoding="utf-8", newline="\n")
    gitignore = repository_root / ".gitignore"
    gitignore.write_text("backend/data/\n", encoding="utf-8", newline="\n")
    subprocess.run(
        [
            "git",
            "-C",
            str(repository_root),
            "add",
            "--",
            "seed.txt",
            ".gitignore",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repository_root), "commit", "-q", "-m", "seed"],
        check=True,
        capture_output=True,
    )
    key = repository_root / "maintainer-key"
    _generate_signing_key(key)
    public_parts = key.with_suffix(".pub").read_text(encoding="ascii").split()
    policy = build_reviewer_key_policy(
        protocol_id=protocol_id,
        frozen_protocol_sha256=frozen_protocol_sha256,
        reviewer_id=reviewer_id,
        allowed_signers_policy_utf8=(
            f"{reviewer_id} {public_parts[0]} {public_parts[1]}\n"
        ),
        allowed_namespaces=(CONCEPT_ATTESTATION_NAMESPACE,),
    )
    (repository_root / "backend/golden_graph").mkdir(
        parents=True,
        exist_ok=True,
    )
    publish_reviewer_key_policy(
        repository_root=repository_root,
        policy=policy,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository_root),
            "add",
            "--",
            "backend/golden_graph/attestations",
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
            "register reviewer key",
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
    authority = load_repository_reviewer_key_policy(
        repository_root=repository_root,
        protocol_id=protocol_id,
        frozen_protocol_sha256=frozen_protocol_sha256,
        reviewer_id=reviewer_id,
        registration_commit_sha=registration_commit,
    )
    return authority, key


def _sign_request(
    directory: Path,
    request: object,
    *,
    signing_key: Path,
) -> Path:
    ssh_keygen = shutil.which("ssh-keygen")
    if ssh_keygen is None:
        pytest.skip("OpenSSH ssh-keygen is unavailable")
    request_path = directory / "concept-seal-request.json"
    request_path.write_bytes(canonical_json_bytes(request))
    subprocess.run(
        [
            ssh_keygen,
            "-Y",
            "sign",
            "-f",
            str(signing_key),
            "-n",
            CONCEPT_ATTESTATION_NAMESPACE,
            str(request_path),
        ],
        check=True,
        capture_output=True,
    )
    return Path(f"{request_path}.sig")


def _paths(root: Path) -> ConceptStagePaths:
    return ConceptStagePaths(
        inventory=root / "inventory.json",
        alias_table=root / "aliases.json",
        seal_request=root / "request.json",
        attestation=root / "attestation.json",
        seal=root / "seal.json",
        pair_manifest=root / "pairs.json",
    )
