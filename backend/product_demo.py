"""Seed one real public-course Source -> Graph -> Path -> Citation demo.

The command deliberately orchestrates production services instead of owning a
second ingestion or graph implementation.  The bundled labels are an
engineering fixture for product acceptance, not human gold annotations and
not an accuracy benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any
from uuid import uuid4

from benchmark_acquisition import load_manifest, verify_registered_asset
from benchmark_acquisition.fetch import AcquisitionError


DEMO_ID = "cs336-lecture-03-attention-path-v1"
ASSET_ID = "lecture-03-architecture"
MANIFEST_PATH = (
    Path(__file__).resolve().parent
    / "benchmark_acquisition"
    / "manifests"
    / "cs336-sp25-v1.json"
)
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

CONCEPT_SPECS: tuple[dict[str, Any], ...] = (
    {
        "key": "full_attention",
        "preferred_name": "Full Attention",
        "short_definition": "Attention over the entire available context.",
        "page_number": 65,
        "chunk_ordinal": 64,
        "chunk_text_sha256": (
            "79121991c2f0c0e666c782b3db4da93d9365326d1cd47d1b71c562b3db9d3364"
        ),
        "span_start": 34,
        "span_end": 95,
        "span_sha256": (
            "2259641dda96072591c543a18d3577d86345d955f4b9b4daef79e46cb99588f3"
        ),
    },
    {
        "key": "sparse_attention",
        "preferred_name": "Sparse Attention",
        "short_definition": (
            "A structured attention pattern that trades expressiveness for "
            "lower runtime cost."
        ),
        "page_number": 65,
        "chunk_ordinal": 64,
        "chunk_text_sha256": (
            "79121991c2f0c0e666c782b3db4da93d9365326d1cd47d1b71c562b3db9d3364"
        ),
        "span_start": 97,
        "span_end": 174,
        "span_sha256": (
            "df1f576600f5abf984ffe607dd352a8fe618d54f5de2b9dec62034b6e7db9458"
        ),
    },
    {
        "key": "sliding_window_attention",
        "preferred_name": "Sliding-window Attention",
        "short_definition": (
            "A sparse attention variant restricted to a moving local window."
        ),
        "page_number": 66,
        "chunk_ordinal": 65,
        "chunk_text_sha256": (
            "9c88d2e6a30b36654fd5a56b9180b2332b6eff2426f81cd884d7a5cc4796e2d3"
        ),
        "span_start": 0,
        "span_end": 24,
        "span_sha256": (
            "e4956586cb835110567bada4f985fd706c47428f8487e19352849ff9f609127e"
        ),
    },
)

RELATION_SPECS: tuple[dict[str, str], ...] = (
    {
        "source": "full_attention",
        "target": "sparse_attention",
        "rationale": (
            "Study full attention before comparing its sparse alternatives."
        ),
    },
    {
        "source": "sparse_attention",
        "target": "sliding_window_attention",
        "rationale": (
            "Study sparse attention before the sliding-window specialization."
        ),
    },
)


class ProductDemoError(RuntimeError):
    """Raised when the demo cannot prove its production acceptance path."""


def _prepare_workspace(workspace: Path) -> dict[str, str]:
    resolved = workspace.expanduser().resolve()
    repository_root = REPOSITORY_ROOT.resolve()
    ignored_data_root = (repository_root / "backend" / "data").resolve()
    if repository_root in resolved.parents and not (
        resolved == ignored_data_root or ignored_data_root in resolved.parents
    ):
        raise ProductDemoError(
            "A repository-local demo workspace must stay under the gitignored "
            "backend/data directory."
        )
    if resolved.exists():
        if not resolved.is_dir():
            raise ProductDemoError("The demo workspace path is not a directory.")
        if any(resolved.iterdir()):
            raise ProductDemoError(
                "The demo workspace must be new or empty so existing user data "
                "cannot be changed."
            )
    else:
        resolved.mkdir(parents=True)

    values = {
        "VCC_DATA_DIR": str(resolved),
        "VCC_DB_PATH": str(resolved / "data" / "jobs.db"),
        "VCC_UPLOAD_DIR": str(resolved / "uploads"),
        "VCC_TRANSCRIPT_DIR": str(resolved / "transcripts"),
        "VCC_EXPORT_DIR": str(resolved / "exports"),
        "VCC_LOG_DIR": str(resolved / "logs"),
        "VCC_SOURCE_DIR": str(resolved / "sources"),
        "VCC_DESKTOP": "0",
    }
    os.environ.update(values)
    return values


def _resolve_evidence_span(
    chunks,
    *,
    page_number: int,
    chunk_ordinal: int,
    chunk_text_sha256: str,
    span_start: int,
    span_end: int,
    span_sha256: str,
):
    matches = [
        chunk
        for chunk in chunks
        if getattr(chunk.locator, "page_number", None) == page_number
    ]
    if len(matches) != 1:
        raise ProductDemoError(
            f"Expected exactly one current Source Chunk for page {page_number}; "
            f"found {len(matches)}."
        )
    chunk = matches[0]
    if (
        chunk.ordinal != chunk_ordinal
        or chunk.text_hash != chunk_text_sha256
    ):
        raise ProductDemoError(
            f"The current Source Chunk identity changed on page {page_number}."
        )
    encoded = chunk.text.encode("utf-8")
    if span_start < 0 or span_end <= span_start or span_end > len(encoded):
        raise ProductDemoError(
            f"The configured private evidence span is invalid on page {page_number}."
        )
    quote_bytes = encoded[span_start:span_end]
    actual_hash = hashlib.sha256(quote_bytes).hexdigest()
    if actual_hash != span_sha256:
        raise ProductDemoError(
            f"The private evidence span changed on page {page_number}."
        )
    try:
        quote = quote_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ProductDemoError(
            f"The configured evidence span breaks UTF-8 on page {page_number}."
        ) from exc
    return chunk, quote


def _fixture_spec_hash() -> str:
    payload = {
        "demo_id": DEMO_ID,
        "concepts": CONCEPT_SPECS,
        "relations": RELATION_SPECS,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_demo(workspace: Path) -> dict[str, Any]:
    manifest = load_manifest(MANIFEST_PATH)
    try:
        verified_asset = verify_registered_asset(
            manifest,
            ASSET_ID,
            REPOSITORY_ROOT,
        )
    except (AcquisitionError, OSError, ValueError) as exc:
        raise ProductDemoError(
            "The registered CS336 PDF is missing or failed verification. Run "
            "`uv run --frozen python -m benchmark_acquisition.fetch "
            "--manifest benchmark_acquisition/manifests/cs336-sp25-v1.json "
            "--asset-id lecture-03-architecture` from backend first."
        ) from exc

    workspace_env = _prepare_workspace(workspace)

    # Import app modules only after the isolated workspace environment is set.
    from app.citation_target_service import (  # noqa: PLC0415
        resolve_source_evidence_content,
        resolve_source_evidence_target,
    )
    from app.concept_graph import (  # noqa: PLC0415
        ConceptCreate,
        ConceptRelationCreate,
        GraphReviewRequest,
        RelationReviewRequest,
    )
    from app.concept_graph_path_service import (  # noqa: PLC0415
        get_learning_path,
        get_relationship_trace,
    )
    from app.concept_graph_publication import (  # noqa: PLC0415
        GraphPublicationRequest,
    )
    from app.concept_graph_publication_service import (  # noqa: PLC0415
        get_course_version_evidence_snapshot,
        preview_course_publication,
        publish_course_version,
    )
    from app.concept_graph_service import (  # noqa: PLC0415
        create_grounded_concept_candidate,
        create_grounded_relation_candidate,
        review_course_concept,
        review_course_relation,
    )
    from app.course import CourseCreate  # noqa: PLC0415
    from app.course_service import create_video_course  # noqa: PLC0415
    from app.course_source_service import (  # noqa: PLC0415
        get_course_source,
        list_source_chunks,
    )
    from app.db import configure_db, init_db  # noqa: PLC0415
    from app.settings import get_app_path_settings  # noqa: PLC0415
    from app.source_asset_service import (  # noqa: PLC0415
        import_course_source_asset,
    )

    get_app_path_settings.cache_clear()
    paths = get_app_path_settings()
    configure_db(paths.db_path)
    init_db()

    course = create_video_course(
        CourseCreate(
            title="CS336 Lecture 3: Attention Architecture",
            description=(
                f"{manifest.attribution} Engineering fixture {DEMO_ID}; "
                "maintainer review pending; not human gold."
            ),
        )
    )
    imported = import_course_source_asset(
        course.id,
        filename=verified_asset.path.name,
        content_type=verified_asset.media_type,
        content=verified_asset.path.read_bytes(),
    )
    if imported.asset.sha256 != verified_asset.sha256:
        raise ProductDemoError(
            "The imported Source no longer matches the verified benchmark asset."
        )

    source_id = f"asset:{imported.asset.id}"
    source = get_course_source(source_id)
    chunks = list_source_chunks(source_id)
    if len(chunks) != 68:
        raise ProductDemoError(
            f"Expected 68 PDF page Chunks after production import; found {len(chunks)}."
        )

    actor = "engineering-fixture@local"
    reviewer = "engineering-fixture-automation@local"
    concept_by_key = {}
    evidence_by_key = {}
    for spec in CONCEPT_SPECS:
        chunk, quote = _resolve_evidence_span(
            chunks,
            page_number=spec["page_number"],
            chunk_ordinal=spec["chunk_ordinal"],
            chunk_text_sha256=spec["chunk_text_sha256"],
            span_start=spec["span_start"],
            span_end=spec["span_end"],
            span_sha256=spec["span_sha256"],
        )
        evidence_by_key[spec["key"]] = (chunk, quote)
        candidate = create_grounded_concept_candidate(
            course.id,
            ConceptCreate(
                operation_id=uuid4().hex,
                actor=actor,
                reason="Seed an evidence-grounded public-course product demo.",
                preferred_name=spec["preferred_name"],
                short_definition=spec["short_definition"],
                evidence=[{"chunk_id": chunk.id, "quote": quote}],
            ),
            proposal_origin="import",
        )
        concept_by_key[spec["key"]] = review_course_concept(
            course.id,
            candidate.id,
            GraphReviewRequest(
                operation_id=uuid4().hex,
                actor=reviewer,
                reason=(
                    "Accept for engineering-path demonstration only; this is "
                    "not a gold annotation."
                ),
                expected_revision=candidate.revision,
                decision="accept",
            ),
        )

    for spec in RELATION_SPECS:
        source_evidence = evidence_by_key[spec["source"]]
        target_evidence = evidence_by_key[spec["target"]]
        candidate = create_grounded_relation_candidate(
            course.id,
            ConceptRelationCreate(
                operation_id=uuid4().hex,
                actor=actor,
                reason="Seed an evidence-backed path for product acceptance.",
                source_concept_id=concept_by_key[spec["source"]].id,
                target_concept_id=concept_by_key[spec["target"]].id,
                relation_type="prerequisite",
                support_basis="pedagogical_inference",
                rationale=spec["rationale"],
                evidence=[
                    {
                        "chunk_id": source_evidence[0].id,
                        "quote": source_evidence[1],
                        "support_role": "source_endpoint",
                    },
                    {
                        "chunk_id": target_evidence[0].id,
                        "quote": target_evidence[1],
                        "support_role": "target_endpoint",
                    },
                ],
            ),
            proposal_origin="import",
        )
        if candidate.endpoint_binding is None:
            raise ProductDemoError("The relation endpoint revision binding is missing.")
        review_course_relation(
            course.id,
            candidate.id,
            RelationReviewRequest(
                operation_id=uuid4().hex,
                actor=reviewer,
                reason=(
                    "Accept for engineering-path demonstration only; this is "
                    "not a gold annotation."
                ),
                expected_revision=candidate.revision,
                expected_source_concept_revision=(
                    candidate.endpoint_binding.source_concept_revision
                ),
                expected_target_concept_revision=(
                    candidate.endpoint_binding.target_concept_revision
                ),
                decision="accept",
            ),
        )

    preview = preview_course_publication(course.id)
    if not preview.publishable:
        issue_codes = ", ".join(issue.code for issue in preview.issues)
        raise ProductDemoError(
            f"The production publication preview refused the fixture: {issue_codes}."
        )
    version = publish_course_version(
        course.id,
        GraphPublicationRequest(
            operation_id=uuid4().hex,
            expected_active_version=preview.active_version,
            expected_draft_manifest_hash=preview.draft_manifest_hash,
            actor="engineering-fixture-publisher@local",
            reason="Publish a non-gold public-course engineering fixture.",
        ),
    )

    source_concept = concept_by_key["full_attention"]
    target_concept = concept_by_key["sliding_window_attention"]
    trace = get_relationship_trace(
        course.id,
        version.version_number,
        source_concept_id=source_concept.id,
        target_concept_id=target_concept.id,
    )
    learning_path = get_learning_path(
        course.id,
        version.version_number,
        target_concept_id=target_concept.id,
    )
    expected_linearization = [
        concept_by_key[spec["key"]].id for spec in CONCEPT_SPECS
    ]
    if trace.status != "found" or trace.hop_count != 2:
        raise ProductDemoError(
            "The production path service did not return the 2-hop trace."
        )
    if learning_path.linearization != expected_linearization:
        raise ProductDemoError(
            "The production learning-path order does not match the fixture."
        )
    if any(node.proposal_origin != "import" for node in trace.nodes) or any(
        step.relation.proposal_origin != "import" for step in trace.steps
    ):
        raise ProductDemoError(
            "The published engineering fixture lost its import provenance."
        )

    citation_pages: list[int] = []
    allowed_evidence = {
        (spec["page_number"], spec["span_sha256"])
        for spec in CONCEPT_SPECS
    }
    for step in trace.steps:
        for evidence in step.relation.evidence:
            snapshot = get_course_version_evidence_snapshot(
                course.id,
                version.version_number,
                owner_type="relation",
                owner_id=step.relation.relation_id,
                evidence_id=evidence.evidence_id,
            )
            target = resolve_source_evidence_target(course.id, snapshot)
            managed_file = resolve_source_evidence_content(course.id, snapshot)
            try:
                if (
                    target.availability != "available"
                    or managed_file.sha256 != verified_asset.sha256
                    or managed_file.size_bytes != verified_asset.byte_size
                    or snapshot.projection_generation_id
                    != source.projection_generation_id
                ):
                    raise ProductDemoError(
                        "A published relation citation did not resolve to the "
                        "verified Source file."
                    )
            finally:
                managed_file.close()
            page_number = target.locator.get("page_number")
            if not isinstance(page_number, int):
                raise ProductDemoError("A relation citation lost its PDF page Locator.")
            span_hash = hashlib.sha256(snapshot.quote.encode("utf-8")).hexdigest()
            if (page_number, span_hash) not in allowed_evidence:
                raise ProductDemoError(
                    "A relation citation no longer matches the redacted fixture span."
                )
            citation_pages.append(page_number)

    registered_asset = next(
        asset for asset in manifest.assets if asset.asset_id == ASSET_ID
    )
    result = {
        "demo_id": DEMO_ID,
        "fixture_spec_sha256": _fixture_spec_hash(),
        "status": "ready",
        "annotation_status": "engineering_fixture_maintainer_review_pending",
        "human_reviewed": False,
        "gold_authority": False,
        "accuracy_evaluated": False,
        "automated_product_acceptance": True,
        "claims": [
            "production_service_source_graph_path_citation_smoke",
            "not_human_gold",
            "not_an_accuracy_result",
        ],
        "workspace": str(paths.data_dir),
        "workspace_env": workspace_env,
        "course": {
            "id": course.id,
            "title": course.title,
        },
        "source": {
            "asset_id": imported.asset.id,
            "source_id": source_id,
            "sha256": imported.asset.sha256,
            "chunk_count": len(chunks),
            "projection_generation_id": source.projection_generation_id,
            "projection_manifest_hash": source.projection_manifest_hash,
            "upstream_commit": manifest.commit_sha,
            "license_spdx": manifest.license_spdx,
            "local_managed_copy_created": True,
            "source_bytes_committed": False,
            "redistribution_allowed": registered_asset.redistribution_allowed,
        },
        "graph": {
            "version": version.version_number,
            "content_hash": version.content_hash,
            "counts": version.counts.model_dump(mode="json"),
            "concept_ids": {
                key: concept.id for key, concept in concept_by_key.items()
            },
        },
        "acceptance": {
            "trace_status": trace.status,
            "trace_hops": trace.hop_count,
            "trace_result_hash": trace.result_hash,
            "learning_path_linearization": learning_path.linearization,
            "learning_path_result_hash": learning_path.result_hash,
            "citation_pages": citation_pages,
            "citation_file_sha256": verified_asset.sha256,
        },
    }
    receipt_path = paths.data_dir / "product-demo-receipt.json"
    result["receipt_path"] = str(receipt_path)
    receipt_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Seed and verify the CS336 Source -> Graph -> Path -> Citation "
            "engineering demo in a new isolated workspace."
        )
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        required=True,
        help="A new or empty directory; existing application data is refused.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = build_demo(args.workspace)
    except ProductDemoError as exc:
        print(f"product demo failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
