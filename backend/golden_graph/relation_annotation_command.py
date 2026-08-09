"""CLI adapter for the embargoed Relation Pass A commit--reveal workflow."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Sequence

from .annotation_artifacts import (
    AnnotationArtifactError,
    load_private_canonical_artifact,
    preflight_private_canonical_artifact,
    publish_private_canonical_artifact,
    read_private_worksheet_bytes,
    write_new_private_worksheet,
)
from .annotation_command import (
    _DEFAULT_PROTOCOL,
    _DEFAULT_REPOSITORY_ROOT,
    _load_protocol_context,
    _public_artifact_root,
    _repository_path,
    _resolve_input_path,
)
from .annotation_workflow import (
    ConceptAnnotationWorkflowError,
    SealedConceptInventoryAuthority,
    default_concept_stage_paths,
    load_sealed_concept_inventory,
)
from .canonical_io import canonical_json_bytes
from .protocol import FrozenProtocolAuthority, GoldenGraphProtocolError
from .relation_annotation_models import (
    RelationPassAArtifact,
    RelationPassASealRequest,
)
from .relation_pass_a_workflow import (
    PreparedRelationPassA,
    RelationPassAStagePaths,
    RelationPassAWorkflowError,
    default_relation_pass_a_stage_paths,
    load_relation_pass_a_commitment,
    load_sealed_relation_pass_a,
    new_relation_pass_a_worksheet,
    parse_relation_pass_a_worksheet,
    prepare_relation_pass_a,
    publish_relation_pass_a_stage,
    signoff_prepared_relation_pass_a,
)
from .reviewer_policy import (
    ReviewerKeyPolicyAuthority,
    ReviewerKeyPolicyError,
    load_historical_reviewer_key_policy,
    load_repository_reviewer_key_policy,
)
from .source_slice_builder import (
    PrivateSourceSliceMaterializationReceipt,
    SourceSliceBuildError,
    load_private_source_slice_materialization,
)


@dataclass(frozen=True, slots=True)
class _RelationContext:
    repository_root: Path
    frozen_protocol: FrozenProtocolAuthority
    source_materialization: PrivateSourceSliceMaterializationReceipt
    concept_reviewer_key_policy: ReviewerKeyPolicyAuthority
    relation_reviewer_key_policy: ReviewerKeyPolicyAuthority
    sealed_concepts: SealedConceptInventoryAuthority
    worksheet_path: Path
    private_stage_directory: Path
    stage_paths: RelationPassAStagePaths


@dataclass(frozen=True, slots=True)
class _PrivateCandidatePaths:
    artifact: Path
    seal_request: Path


def _init_relation_pass_a(args: argparse.Namespace) -> dict[str, object]:
    context = _load_context(args)
    worksheet = new_relation_pass_a_worksheet(
        sealed_concepts=context.sealed_concepts,
        reviewer_key_policy=context.relation_reviewer_key_policy,
        worksheet_id=(
            args.worksheet_id
            or f"{context.frozen_protocol.protocol.protocol_id}-relation-pass-a-v1"
        ),
    )
    digest = write_new_private_worksheet(
        context.worksheet_path,
        worksheet,
        repository_root=context.repository_root,
        human_readable=True,
    )
    return {
        "artifact_role": "golden_graph_relation_pass_a_worksheet_init_receipt",
        "gold_bundle_sealed": False,
        "human_labels_present": False,
        "maintainer_action_required": True,
        "pair_count": worksheet.pair_count,
        "protocol_id": worksheet.protocol_id,
        "schema_version": 1,
        "software_authenticated_prediction_blindness": False,
        "status": "human_relation_pass_a_annotation_required",
        "worksheet_id": worksheet.worksheet_id,
        "worksheet_initial_bytes_sha256": digest,
    }


def _prepare_relation_pass_a_seal(
    args: argparse.Namespace,
) -> dict[str, object]:
    context = _load_context(args)
    prepared = _prepare_from_persisted_worksheet(context)
    candidates = _private_candidate_paths(context)
    leaves = (
        (candidates.artifact, prepared.artifact, prepared.artifact_sha256),
        (
            candidates.seal_request,
            prepared.seal_request,
            prepared.seal_request_sha256,
        ),
    )
    identities = tuple(
        path.parent.resolve(strict=True) / path.name
        for path, _artifact, _expected in leaves
    )
    if len(set(identities)) != len(identities):
        raise RelationPassAWorkflowError(
            "Private Relation Pass A candidate paths must be distinct"
        )
    preflights = tuple(
        preflight_private_canonical_artifact(
            path,
            artifact,
            repository_root=context.repository_root,
        )
        for path, artifact, _expected in leaves
    )
    expected_hashes = tuple(expected for _path, _artifact, expected in leaves)
    if preflights != expected_hashes:
        raise RelationPassAWorkflowError(
            "Private Relation Pass A candidate preflight changed hashes"
        )
    for path, artifact, expected in leaves:
        actual = publish_private_canonical_artifact(
            path,
            artifact,
            repository_root=context.repository_root,
        )
        if actual != expected:
            raise RelationPassAWorkflowError(
                "Private Relation Pass A candidate hash changed"
            )
    return {
        "artifact_role": "golden_graph_relation_pass_a_seal_preparation_receipt",
        "external_signature_required": True,
        "gold_bundle_sealed": False,
        "label_release_policy": prepared.seal_request.label_release_policy,
        "labels_embargoed_at_commitment": True,
        "namespace": prepared.seal_request.namespace,
        "pair_count": prepared.seal_request.pair_count,
        "protocol_id": prepared.seal_request.protocol_id,
        "relation_pass_a_artifact_sha256": prepared.artifact_sha256,
        "relation_pass_a_seal_request_sha256": prepared.seal_request_sha256,
        "reviewer_id": prepared.seal_request.reviewer_id,
        "schema_version": 1,
        "signature_command_template": (
            "ssh-keygen -Y sign -f <YOUR_PRIVATE_KEY> -n "
            "video-course-cards-g2-relation-pass-a-v1 <SEAL_REQUEST_FILE>"
        ),
        "software_authenticated_minimum_delay": False,
        "software_authenticated_prediction_blindness": False,
        "status": "maintainer_signature_pending_labels_embargoed",
    }


def _seal_relation_pass_a(args: argparse.Namespace) -> dict[str, object]:
    if args.signature is None:
        raise RelationPassAWorkflowError(
            "seal-relation-pass-a requires a detached signature"
        )
    context = _load_context(args)
    prepared = _prepare_from_persisted_worksheet(context)
    _require_private_candidates_match(context, prepared)
    signed = signoff_prepared_relation_pass_a(
        prepared=prepared,
        reviewer_key_policy=context.relation_reviewer_key_policy,
        signature_path=_resolve_input_path(
            context.repository_root,
            args.signature,
        ),
    )
    authority = publish_relation_pass_a_stage(
        signed=signed,
        paths=context.stage_paths,
        repository_root=context.repository_root,
        public_artifact_root=_public_artifact_root(context.repository_root),
        sealed_concepts=context.sealed_concepts,
        reviewer_key_policy=context.relation_reviewer_key_policy,
    )
    return _commitment_receipt(
        authority.commitment,
        role="golden_graph_relation_pass_a_seal_receipt",
        status="commit_and_push_relation_pass_a_commitment_before_delay",
    )


def _verify_relation_pass_a(args: argparse.Namespace) -> dict[str, object]:
    context = _load_context(args, require_active_relation_policy=False)
    authority = load_sealed_relation_pass_a(
        paths=context.stage_paths,
        repository_root=context.repository_root,
        public_artifact_root=_public_artifact_root(context.repository_root),
        sealed_concepts=context.sealed_concepts,
        reviewer_key_policy=context.relation_reviewer_key_policy,
    )
    return _commitment_receipt(
        authority.commitment,
        role="golden_graph_relation_pass_a_verification_receipt",
        status="sealed_relation_pass_a_local_artifact_verified",
    )


def _verify_relation_pass_a_commitment(
    args: argparse.Namespace,
) -> dict[str, object]:
    context = _load_context(args, require_active_relation_policy=False)
    authority = load_relation_pass_a_commitment(
        paths=context.stage_paths.public,
        public_artifact_root=_public_artifact_root(context.repository_root),
        sealed_concepts=context.sealed_concepts,
        reviewer_key_policy=context.relation_reviewer_key_policy,
    )
    return _commitment_receipt(
        authority,
        role="golden_graph_relation_pass_a_commitment_verification_receipt",
        status="public_commitment_verified_without_reading_pass_a_labels",
    )


def _commitment_receipt(
    authority,
    *,
    role: str,
    status: str,
) -> dict[str, object]:
    request = authority.seal_request.artifact
    seal = authority.seal.artifact
    return {
        "artifact_role": role,
        "gold_bundle_sealed": False,
        "key_control_attestation_verified": True,
        "key_control_only_not_proof_of_humanity": True,
        "label_release_policy": seal.label_release_policy,
        "labels_embargoed_at_commitment": True,
        "minimum_delay_hours_before_pass_b": (
            seal.minimum_delay_hours_before_pass_b
        ),
        "pair_count": seal.pair_count,
        "protocol_id": seal.protocol_id,
        "relation_pass_a_artifact_sha256": (
            seal.relation_pass_a_artifact_sha256
        ),
        "relation_pass_a_seal_request_sha256": (
            authority.seal_request.artifact_sha256
        ),
        "relation_pass_a_seal_sha256": authority.seal.artifact_sha256,
        "reviewer_attested_at_utc": request.reviewer_attested_at_utc,
        "reviewer_key_policy_git_commit": (
            seal.reviewer_key_policy_git_commit
        ),
        "reviewer_key_policy_sha256": seal.reviewer_key_policy_sha256,
        "schema_version": 1,
        "software_authenticated_minimum_delay": False,
        "software_authenticated_prediction_blindness": False,
        "software_authenticated_reviewer_identity": False,
        "status": status,
    }


def _prepare_from_persisted_worksheet(
    context: _RelationContext,
) -> PreparedRelationPassA:
    payload = read_private_worksheet_bytes(
        context.worksheet_path,
        repository_root=context.repository_root,
    )
    worksheet = parse_relation_pass_a_worksheet(payload)
    return prepare_relation_pass_a(
        sealed_concepts=context.sealed_concepts,
        reviewer_key_policy=context.relation_reviewer_key_policy,
        worksheet=worksheet,
    )


def _require_private_candidates_match(
    context: _RelationContext,
    prepared: PreparedRelationPassA,
) -> None:
    candidates = _private_candidate_paths(context)
    artifact = load_private_canonical_artifact(
        candidates.artifact,
        RelationPassAArtifact,
        repository_root=context.repository_root,
    )
    request = load_private_canonical_artifact(
        candidates.seal_request,
        RelationPassASealRequest,
        repository_root=context.repository_root,
    )
    if (
        artifact.artifact != prepared.artifact
        or artifact.artifact_sha256 != prepared.artifact_sha256
        or request.artifact != prepared.seal_request
        or request.artifact_sha256 != prepared.seal_request_sha256
    ):
        raise RelationPassAWorkflowError(
            "Relation Pass A worksheet changed after commitment preparation"
        )


def _load_context(
    args: argparse.Namespace,
    *,
    require_active_relation_policy: bool = True,
) -> _RelationContext:
    protocol_context = _load_protocol_context(args)
    root = protocol_context.repository_root
    frozen = protocol_context.frozen_protocol
    concept_policy_commit = args.concept_reviewer_key_policy_commit
    relation_policy_commit = args.relation_reviewer_key_policy_commit
    concept_policy = load_historical_reviewer_key_policy(
        repository_root=root,
        protocol_id=frozen.protocol.protocol_id,
        frozen_protocol_sha256=frozen.protocol_sha256,
        reviewer_id=frozen.protocol.review.reviewer_id,
        registration_commit_sha=concept_policy_commit,
    )
    relation_policy_loader = (
        load_repository_reviewer_key_policy
        if require_active_relation_policy
        else load_historical_reviewer_key_policy
    )
    relation_policy = relation_policy_loader(
        repository_root=root,
        protocol_id=frozen.protocol.protocol_id,
        frozen_protocol_sha256=frozen.protocol_sha256,
        reviewer_id=frozen.protocol.review.reviewer_id,
        registration_commit_sha=relation_policy_commit,
    )
    materialization_path = (
        _repository_path(root, args.materialization)
        if args.materialization is not None
        else root
        / "backend/data/golden_graph/source_slice_materializations"
        / f"{frozen.protocol.protocol_id}.private.json"
    )
    source = load_private_source_slice_materialization(
        repository_root=root,
        artifact_path=materialization_path,
        expected_protocol=frozen.protocol,
    )
    concepts = load_sealed_concept_inventory(
        paths=default_concept_stage_paths(root, frozen),
        public_artifact_root=_public_artifact_root(root),
        frozen_protocol=frozen,
        source_materialization=source,
        reviewer_key_policy=concept_policy,
    )
    private_stage_directory = (
        root
        / "backend/data/golden_graph/annotations"
        / frozen.protocol.protocol_id
    )
    if require_active_relation_policy:
        private_stage_directory.mkdir(parents=True, exist_ok=True)
    worksheet_path = (
        _repository_path(root, args.worksheet)
        if args.worksheet is not None
        else private_stage_directory / "relation-pass-a.worksheet.private.json"
    )
    return _RelationContext(
        repository_root=root,
        frozen_protocol=frozen,
        source_materialization=source,
        concept_reviewer_key_policy=concept_policy,
        relation_reviewer_key_policy=relation_policy,
        sealed_concepts=concepts,
        worksheet_path=worksheet_path,
        private_stage_directory=private_stage_directory,
        stage_paths=default_relation_pass_a_stage_paths(root, concepts),
    )


def _private_candidate_paths(
    context: _RelationContext,
) -> _PrivateCandidatePaths:
    return _PrivateCandidatePaths(
        artifact=context.stage_paths.private.artifact,
        seal_request=(
            context.private_stage_directory
            / "relation-pass-a.seal-request.candidate.private.json"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare an exhaustive Relation Pass A commitment without "
            "generating human labels or revealing them before Pass B."
        )
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=_DEFAULT_REPOSITORY_ROOT,
    )
    parser.add_argument("--protocol", type=Path, default=_DEFAULT_PROTOCOL)
    parser.add_argument("--materialization", type=Path)
    parser.add_argument("--worksheet", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)

    initialize = subparsers.add_parser("init-relation-pass-a")
    _add_policy_arguments(initialize)
    initialize.add_argument("--worksheet-id")
    initialize.set_defaults(handler=_init_relation_pass_a)

    prepare = subparsers.add_parser("prepare-relation-pass-a-seal")
    _add_policy_arguments(prepare)
    prepare.set_defaults(handler=_prepare_relation_pass_a_seal)

    seal = subparsers.add_parser("seal-relation-pass-a")
    _add_policy_arguments(seal)
    seal.add_argument("--signature", type=Path, required=True)
    seal.set_defaults(handler=_seal_relation_pass_a)

    verify = subparsers.add_parser("verify-relation-pass-a")
    _add_policy_arguments(verify)
    verify.set_defaults(handler=_verify_relation_pass_a)

    verify_commitment = subparsers.add_parser(
        "verify-relation-pass-a-commitment"
    )
    _add_policy_arguments(verify_commitment)
    verify_commitment.set_defaults(
        handler=_verify_relation_pass_a_commitment
    )
    return parser


def _add_policy_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--concept-reviewer-key-policy-commit",
        required=True,
        help="reachable commit containing the policy used by the Concept seal",
    )
    parser.add_argument(
        "--relation-reviewer-key-policy-commit",
        required=True,
        help="commit registering the policy that authorizes Relation Pass A",
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        receipt = args.handler(args)
    except (
        AnnotationArtifactError,
        ConceptAnnotationWorkflowError,
        GoldenGraphProtocolError,
        RelationPassAWorkflowError,
        ReviewerKeyPolicyError,
        SourceSliceBuildError,
        OSError,
        ValueError,
    ) as exc:
        print(
            f"relation annotation command failed: {_safe_cli_error_message(exc)}",
            file=sys.stderr,
        )
        return 2
    sys.stdout.buffer.write(canonical_json_bytes(receipt))
    return 0


def _safe_cli_error_message(exc: BaseException) -> str:
    if isinstance(exc, AnnotationArtifactError):
        return "annotation artifact validation failed"
    if isinstance(exc, ConceptAnnotationWorkflowError):
        return "sealed Concept validation failed"
    if isinstance(exc, GoldenGraphProtocolError):
        return "frozen protocol validation failed"
    if isinstance(exc, RelationPassAWorkflowError):
        return "Relation Pass A workflow validation failed"
    if isinstance(exc, ReviewerKeyPolicyError):
        return "reviewer-key policy validation failed"
    if isinstance(exc, SourceSliceBuildError):
        return "source materialization validation failed"
    if isinstance(exc, OSError):
        return "required private or repository file operation failed"
    return "Relation Pass A input validation failed"


if __name__ == "__main__":
    raise SystemExit(main())
