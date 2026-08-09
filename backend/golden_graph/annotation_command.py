"""Command-line boundary for the G2.1 Concept annotation workflow."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Sequence

from .annotation_artifacts import (
    AnnotationArtifactError,
    load_private_canonical_artifact,
    publish_private_canonical_artifact,
    read_private_worksheet_bytes,
    write_new_private_worksheet,
)
from .annotation_models import (
    ConceptInventory,
    ConceptInventorySealRequest,
    GoldAliasTable,
)
from .annotation_workflow import (
    CONCEPT_ATTESTATION_NAMESPACE,
    ConceptAnnotationWorkflowError,
    ConceptStagePaths,
    default_concept_stage_paths,
    load_sealed_concept_inventory,
    new_concept_annotation_worksheet,
    parse_concept_annotation_worksheet,
    prepare_concept_inventory,
    publish_concept_inventory_stage,
    signoff_prepared_concept_inventory,
)
from .canonical_io import canonical_json_bytes, read_bounded_regular_bytes
from .protocol import (
    FrozenProtocolAuthority,
    GoldenGraphProtocolError,
    load_historical_frozen_protocol,
    load_manifest_authority,
    load_protocol,
)
from .source_slice_builder import (
    PrivateSourceSliceMaterializationReceipt,
    SourceSliceBuildError,
    load_private_source_slice_materialization,
)
from .reviewer_policy import (
    ReviewerKeyPolicyAuthority,
    ReviewerKeyPolicyError,
    build_reviewer_key_policy,
    load_historical_reviewer_key_policy,
    load_repository_reviewer_key_policy,
    publish_reviewer_key_policy,
    reviewer_key_policy_path,
)


_DEFAULT_PROTOCOL = Path(
    "backend/golden_graph/protocols/"
    "cs336-sp25-lecture-03-golden-graph-v1.frozen.json"
)
_DEFAULT_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class _Context:
    repository_root: Path
    frozen_protocol: FrozenProtocolAuthority
    source_materialization: PrivateSourceSliceMaterializationReceipt
    reviewer_key_policy: ReviewerKeyPolicyAuthority
    worksheet_path: Path
    private_stage_directory: Path
    public_paths: ConceptStagePaths


@dataclass(frozen=True, slots=True)
class _PrivateCandidatePaths:
    inventory: Path
    alias_table: Path
    seal_request: Path


@dataclass(frozen=True, slots=True)
class _ProtocolContext:
    repository_root: Path
    frozen_protocol: FrozenProtocolAuthority


def _prepare_reviewer_key_policy(args: argparse.Namespace) -> dict[str, object]:
    context = _load_protocol_context(args)
    allowed_signers_path = _resolve_input_path(
        context.repository_root,
        args.allowed_signers,
    )
    try:
        allowed_signers_text = read_bounded_regular_bytes(
            allowed_signers_path,
            max_bytes=4_096,
            label="reviewer public-key policy",
        ).decode("ascii")
    except (OSError, UnicodeError, ValueError) as exc:
        raise ReviewerKeyPolicyError(
            "Reviewer public-key policy must be bounded ASCII"
        ) from exc
    protocol = context.frozen_protocol.protocol
    policy = build_reviewer_key_policy(
        protocol_id=protocol.protocol_id,
        frozen_protocol_sha256=context.frozen_protocol.protocol_sha256,
        reviewer_id=protocol.review.reviewer_id,
        allowed_signers_policy_utf8=allowed_signers_text,
        allowed_namespaces=(CONCEPT_ATTESTATION_NAMESPACE,),
    )
    digest = publish_reviewer_key_policy(
        repository_root=context.repository_root,
        policy=policy,
    )
    path = reviewer_key_policy_path(
        context.repository_root,
        protocol_id=protocol.protocol_id,
        reviewer_id=protocol.review.reviewer_id,
    )
    return {
        "artifact_role": "golden_graph_reviewer_key_policy_preparation_receipt",
        "authority_issued": False,
        "key_control_only_not_proof_of_humanity": True,
        "maintainer_action_required": True,
        "policy_path": path.relative_to(context.repository_root).as_posix(),
        "policy_sha256": digest,
        "private_key_read_or_generated": False,
        "protocol_id": protocol.protocol_id,
        "schema_version": 1,
        "status": "commit_and_push_policy_before_annotation",
    }


def _verify_reviewer_key_policy(args: argparse.Namespace) -> dict[str, object]:
    context = _load_protocol_context(args)
    protocol = context.frozen_protocol.protocol
    authority = load_repository_reviewer_key_policy(
        repository_root=context.repository_root,
        protocol_id=protocol.protocol_id,
        frozen_protocol_sha256=context.frozen_protocol.protocol_sha256,
        reviewer_id=protocol.review.reviewer_id,
        registration_commit_sha=args.reviewer_key_policy_commit,
    )
    return {
        "artifact_role": "golden_graph_reviewer_key_policy_verification_receipt",
        "authority_issued": True,
        "key_control_only_not_proof_of_humanity": True,
        "policy_sha256": authority.policy_sha256,
        "protocol_id": authority.policy.protocol_id,
        "registration_commit_sha": authority.registration_commit_sha,
        "reviewer_id": authority.policy.reviewer_id,
        "schema_version": 1,
        "status": "active_repository_policy_verified",
        "active_for_new_work": authority.active_at_verified_head,
    }


def _init_concepts(args: argparse.Namespace) -> dict[str, object]:
    context = _load_context(args)
    worksheet = new_concept_annotation_worksheet(
        frozen_protocol=context.frozen_protocol,
        source_materialization=context.source_materialization,
        reviewer_key_policy=context.reviewer_key_policy,
        worksheet_id=(
            args.worksheet_id
            or f"{context.frozen_protocol.protocol.protocol_id}-concepts-v1"
        ),
    )
    digest = write_new_private_worksheet(
        context.worksheet_path,
        worksheet,
        repository_root=context.repository_root,
        human_readable=True,
    )
    return {
        "artifact_role": "golden_graph_concept_worksheet_init_receipt",
        "candidate_count": 0,
        "gold_bundle_sealed": False,
        "human_labels_present": False,
        "maintainer_action_required": True,
        "protocol_id": worksheet.protocol_id,
        "reviewer_key_policy_git_commit": (
            worksheet.reviewer_key_policy_git_commit
        ),
        "reviewer_key_policy_sha256": worksheet.reviewer_key_policy_sha256,
        "schema_version": 1,
        "software_authenticated_prediction_blindness": False,
        "worksheet_id": worksheet.worksheet_id,
        "worksheet_initial_bytes_sha256": digest,
    }


def _prepare_concept_seal(args: argparse.Namespace) -> dict[str, object]:
    context = _load_context(args)
    prepared = _prepare_from_persisted_worksheet(context)
    candidates = _private_candidate_paths(context)
    for path, artifact, expected in (
        (candidates.inventory, prepared.inventory, prepared.inventory_sha256),
        (
            candidates.alias_table,
            prepared.alias_table,
            prepared.alias_table_sha256,
        ),
        (
            candidates.seal_request,
            prepared.seal_request,
            prepared.seal_request_sha256,
        ),
    ):
        actual = publish_private_canonical_artifact(
            path,
            artifact,
            repository_root=context.repository_root,
        )
        if actual != expected:
            raise ConceptAnnotationWorkflowError(
                "Private Concept candidate hash changed during publication"
            )
    return {
        "artifact_role": "golden_graph_concept_seal_preparation_receipt",
        "concept_count": prepared.inventory.concept_count,
        "concept_inventory_sha256": prepared.inventory_sha256,
        "excluded_candidate_count": prepared.excluded_candidate_count,
        "external_signature_required": True,
        "gold_alias_table_sha256": prepared.alias_table_sha256,
        "gold_bundle_sealed": False,
        "namespace": prepared.seal_request.namespace,
        "protocol_id": prepared.inventory.protocol_id,
        "reviewer_id": prepared.inventory.reviewer_id,
        "reviewer_key_policy_git_commit": (
            prepared.inventory.reviewer_key_policy_git_commit
        ),
        "reviewer_key_policy_sha256": (
            prepared.inventory.reviewer_key_policy_sha256
        ),
        "schema_version": 1,
        "seal_request_sha256": prepared.seal_request_sha256,
        "signature_command_template": (
            "ssh-keygen -Y sign -f <YOUR_PRIVATE_KEY> -n "
            "video-course-cards-g2-concepts-v1 <SEAL_REQUEST_FILE>"
        ),
        "status": "maintainer_signature_pending",
        "software_authenticated_prediction_blindness": False,
        "total_candidate_count": prepared.total_candidate_count,
    }


def _seal_concepts(args: argparse.Namespace) -> dict[str, object]:
    if args.signature is None:
        raise ConceptAnnotationWorkflowError(
            "seal-concepts requires a detached signature"
        )
    context = _load_context(args)
    prepared = _prepare_from_persisted_worksheet(context)
    _require_private_candidates_match(context, prepared)
    signed = signoff_prepared_concept_inventory(
        prepared=prepared,
        reviewer_key_policy=context.reviewer_key_policy,
        signature_path=_resolve_input_path(
            context.repository_root,
            args.signature,
        ),
    )
    authority = publish_concept_inventory_stage(
        signed=signed,
        paths=context.public_paths,
        public_artifact_root=_public_artifact_root(context.repository_root),
        frozen_protocol=context.frozen_protocol,
        source_materialization=context.source_materialization,
        reviewer_key_policy=context.reviewer_key_policy,
    )
    return _sealed_receipt(authority, role="golden_graph_concept_seal_receipt")


def _verify_concepts(args: argparse.Namespace) -> dict[str, object]:
    context = _load_context(args, require_active_policy=False)
    authority = load_sealed_concept_inventory(
        paths=context.public_paths,
        public_artifact_root=_public_artifact_root(context.repository_root),
        frozen_protocol=context.frozen_protocol,
        source_materialization=context.source_materialization,
        reviewer_key_policy=context.reviewer_key_policy,
    )
    return _sealed_receipt(
        authority,
        role="golden_graph_concept_seal_verification_receipt",
    )


def _sealed_receipt(
    authority,
    *,
    role: str,
) -> dict[str, object]:
    return {
        "artifact_role": role,
        "concept_count": authority.inventory.artifact.concept_count,
        "concept_inventory_seal_sha256": authority.seal.artifact_sha256,
        "concept_inventory_sha256": authority.inventory.artifact_sha256,
        "gold_alias_table_sha256": authority.alias_table.artifact_sha256,
        "gold_bundle_sealed": False,
        "key_control_attestation_verified": True,
        "key_control_only_not_proof_of_humanity": True,
        "reviewer_key_policy_git_commit": (
            authority.reviewer_key_policy.registration_commit_sha
        ),
        "reviewer_key_policy_sha256": (
            authority.reviewer_key_policy.policy_sha256
        ),
        "reviewer_key_policy_active_for_new_work": (
            authority.reviewer_key_policy.active_at_verified_head
        ),
        "pair_count": authority.pair_manifest.artifact.pair_count,
        "pair_manifest_sha256": authority.pair_manifest.artifact_sha256,
        "protocol_id": authority.inventory.artifact.protocol_id,
        "schema_version": 1,
        "software_authenticated_reviewer_identity": False,
        "software_authenticated_prediction_blindness": False,
        "status": "concept_inventory_only_not_gold_bundle",
    }


def _prepare_from_persisted_worksheet(context: _Context):
    payload = read_private_worksheet_bytes(
        context.worksheet_path,
        repository_root=context.repository_root,
    )
    worksheet = parse_concept_annotation_worksheet(payload)
    return prepare_concept_inventory(
        frozen_protocol=context.frozen_protocol,
        source_materialization=context.source_materialization,
        reviewer_key_policy=context.reviewer_key_policy,
        worksheet=worksheet,
    )


def _require_private_candidates_match(context: _Context, prepared) -> None:
    candidates = _private_candidate_paths(context)
    inventory = load_private_canonical_artifact(
        candidates.inventory,
        ConceptInventory,
        repository_root=context.repository_root,
    )
    alias_table = load_private_canonical_artifact(
        candidates.alias_table,
        GoldAliasTable,
        repository_root=context.repository_root,
    )
    seal_request = load_private_canonical_artifact(
        candidates.seal_request,
        ConceptInventorySealRequest,
        repository_root=context.repository_root,
    )
    if (
        inventory.artifact != prepared.inventory
        or inventory.artifact_sha256 != prepared.inventory_sha256
        or alias_table.artifact != prepared.alias_table
        or alias_table.artifact_sha256 != prepared.alias_table_sha256
        or seal_request.artifact != prepared.seal_request
        or seal_request.artifact_sha256 != prepared.seal_request_sha256
    ):
        raise ConceptAnnotationWorkflowError(
            "Concept worksheet changed after its signing commitment was prepared"
        )


def _load_context(
    args: argparse.Namespace,
    *,
    require_active_policy: bool = True,
) -> _Context:
    protocol_context = _load_protocol_context(args)
    root = protocol_context.repository_root
    frozen = protocol_context.frozen_protocol
    registration_commit = getattr(
        args,
        "reviewer_key_policy_commit",
        None,
    )
    if registration_commit is None:
        raise ConceptAnnotationWorkflowError(
            "A reviewer-key policy registration commit is required"
        )
    policy_loader = (
        load_repository_reviewer_key_policy
        if require_active_policy
        else load_historical_reviewer_key_policy
    )
    reviewer_key_policy = policy_loader(
        repository_root=root,
        protocol_id=frozen.protocol.protocol_id,
        frozen_protocol_sha256=frozen.protocol_sha256,
        reviewer_id=frozen.protocol.review.reviewer_id,
        registration_commit_sha=registration_commit,
    )
    materialization_path = (
        _repository_path(root, args.materialization)
        if args.materialization is not None
        else root
        / "backend/data/golden_graph/source_slice_materializations"
        / f"{frozen.protocol.protocol_id}.private.json"
    )
    materialization = load_private_source_slice_materialization(
        repository_root=root,
        artifact_path=materialization_path,
        expected_protocol=frozen.protocol,
    )
    private_stage_directory = (
        root
        / "backend/data/golden_graph/annotations"
        / frozen.protocol.protocol_id
    )
    private_stage_directory.mkdir(parents=True, exist_ok=True)
    worksheet_path = (
        _repository_path(root, args.worksheet)
        if args.worksheet is not None
        else private_stage_directory / "concepts.worksheet.private.json"
    )
    return _Context(
        repository_root=root,
        frozen_protocol=frozen,
        source_materialization=materialization,
        reviewer_key_policy=reviewer_key_policy,
        worksheet_path=worksheet_path,
        private_stage_directory=private_stage_directory,
        public_paths=default_concept_stage_paths(root, frozen),
    )


def _load_protocol_context(args: argparse.Namespace) -> _ProtocolContext:
    root = Path(args.repository_root).resolve(strict=True)
    protocol_path = _repository_file(root, args.protocol, "frozen protocol")
    protocol_preview = load_protocol(protocol_path)
    manifest_path = _repository_file(
        root,
        Path(protocol_preview.acquisition.manifest_path),
        "acquisition manifest",
    )
    manifest = load_manifest_authority(
        manifest_path,
        repository_root=root,
    )
    frozen = load_historical_frozen_protocol(
        protocol_path,
        manifest,
        repository_root=root,
    )
    return _ProtocolContext(
        repository_root=root,
        frozen_protocol=frozen,
    )


def _private_candidate_paths(context: _Context) -> _PrivateCandidatePaths:
    return _PrivateCandidatePaths(
        inventory=(
            context.private_stage_directory
            / "concept-inventory.candidate.private.json"
        ),
        alias_table=(
            context.private_stage_directory / "alias-table.candidate.private.json"
        ),
        seal_request=(
            context.private_stage_directory
            / "concept-seal-request.candidate.private.json"
        ),
    )


def _public_artifact_root(repository_root: Path) -> Path:
    return (repository_root / "backend/golden_graph/artifacts").resolve(
        strict=True
    )


def _repository_file(root: Path, supplied: Path, label: str) -> Path:
    candidate = _repository_path(root, supplied)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ConceptAnnotationWorkflowError(
            f"{label} must be an existing repository file"
        ) from exc
    if not resolved.is_file():
        raise ConceptAnnotationWorkflowError(
            f"{label} must be an existing repository file"
        )
    return resolved


def _repository_path(root: Path, supplied: Path) -> Path:
    return supplied if supplied.is_absolute() else root / supplied


def _resolve_input_path(root: Path, supplied: Path) -> Path:
    candidate = _repository_path(root, supplied)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ConceptAnnotationWorkflowError(
            "Attestation input file does not exist"
        ) from exc
    if not resolved.is_file():
        raise ConceptAnnotationWorkflowError(
            "Attestation input must be a regular file"
        )
    return resolved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare and seal the G2.1 Concept inventory without generating "
            "human labels."
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

    prepare_policy = subparsers.add_parser("prepare-reviewer-key-policy")
    prepare_policy.add_argument("--allowed-signers", type=Path, required=True)
    prepare_policy.set_defaults(handler=_prepare_reviewer_key_policy)

    verify_policy = subparsers.add_parser("verify-reviewer-key-policy")
    _add_policy_commit_argument(verify_policy)
    verify_policy.set_defaults(handler=_verify_reviewer_key_policy)

    initialize = subparsers.add_parser("init-concepts")
    _add_policy_commit_argument(initialize)
    initialize.add_argument("--worksheet-id")
    initialize.set_defaults(handler=_init_concepts)

    prepare = subparsers.add_parser("prepare-concept-seal")
    _add_policy_commit_argument(prepare)
    prepare.set_defaults(handler=_prepare_concept_seal)

    seal = subparsers.add_parser("seal-concepts")
    _add_policy_commit_argument(seal)
    seal.add_argument("--signature", type=Path, required=True)
    seal.set_defaults(handler=_seal_concepts)

    verify = subparsers.add_parser("verify-concepts")
    _add_policy_commit_argument(verify)
    verify.set_defaults(handler=_verify_concepts)
    return parser


def _add_policy_commit_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--reviewer-key-policy-commit",
        required=True,
        help="full Git commit that first/previously records the reviewer key policy",
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        receipt = args.handler(args)
    except (
        AnnotationArtifactError,
        ConceptAnnotationWorkflowError,
        GoldenGraphProtocolError,
        ReviewerKeyPolicyError,
        SourceSliceBuildError,
        OSError,
        ValueError,
    ) as exc:
        message = _safe_cli_error_message(exc)
        print(f"annotation command failed: {message}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(canonical_json_bytes(receipt))
    return 0


def _safe_cli_error_message(exc: BaseException) -> str:
    """Return a useful error class without echoing private paths or quotes."""

    if isinstance(exc, AnnotationArtifactError):
        return "annotation artifact validation failed"
    if isinstance(exc, ConceptAnnotationWorkflowError):
        return "annotation workflow validation failed"
    if isinstance(exc, GoldenGraphProtocolError):
        return "frozen protocol validation failed"
    if isinstance(exc, SourceSliceBuildError):
        return "source materialization validation failed"
    if isinstance(exc, ReviewerKeyPolicyError):
        return "reviewer-key policy validation failed"
    if isinstance(exc, OSError):
        return "filesystem operation failed"
    return "input validation failed"


if __name__ == "__main__":
    raise SystemExit(main())
