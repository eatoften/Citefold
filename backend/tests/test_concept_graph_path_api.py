from __future__ import annotations

import hashlib
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main
import app.citation_target_service as citation_target_service
from app.concept_graph_publication import GraphPublicationRequest
from app.course_source import (
    CourseSourceChunk,
    PdfPageLocator,
    hash_source_chunk_text,
)
from app.course_source_store import replace_source_projection
from app.db import connect
from tests.concept_graph_publication_support import (
    accepted_concept,
    accepted_relation,
    make_course_source,
)


client = TestClient(main.app, client=("127.0.0.1", 50000))


def _publish(course_id: str) -> int:
    preview = client.get(
        f"/courses/{course_id}/concept-graph/publication-preview"
    ).json()
    response = client.post(
        f"/courses/{course_id}/concept-graph/versions",
        json=GraphPublicationRequest(
            operation_id=uuid4().hex,
            expected_active_version=preview["active_version"],
            expected_draft_manifest_hash=preview["draft_manifest_hash"],
            actor="publisher@example.test",
            reason="Publish the path API fixture.",
        ).model_dump(mode="json"),
    )
    assert response.status_code == 201, response.text
    return response.json()["version_number"]


def _published_chain():
    course, source, chunk = make_course_source(
        "path-api",
        text="Alpha precedes Beta. Beta precedes Gamma.",
    )
    alpha = accepted_concept(course.id, chunk, "Alpha", "Alpha")
    beta = accepted_concept(course.id, chunk, "Beta", "Beta")
    gamma = accepted_concept(course.id, chunk, "Gamma", "Gamma")
    accepted_relation(course.id, chunk, alpha.id, beta.id)
    accepted_relation(
        course.id,
        chunk,
        beta.id,
        gamma.id,
        evidence_quote="Beta precedes Gamma",
    )
    version = _publish(course.id)
    return course, source, chunk, alpha, beta, gamma, version


def _managed_pdf(tmp_path, monkeypatch, course, source) -> bytes:
    payload = b"%PDF-1.4 published graph evidence"
    source_dir = tmp_path / "managed" / "sources"
    upload_dir = tmp_path / "managed" / "uploads"
    path = source_dir / course.id / f"{source.origin_id}.pdf"
    path.parent.mkdir(parents=True)
    upload_dir.mkdir(parents=True)
    path.write_bytes(payload)
    with connect() as conn:
        conn.execute(
            """
            UPDATE source_assets
            SET stored_path = ?, size_bytes = ?, sha256 = ?
            WHERE id = ?
            """,
            (
                str(path),
                len(payload),
                hashlib.sha256(payload).hexdigest(),
                source.origin_id,
            ),
        )
    paths = SimpleNamespace(source_dir=source_dir, upload_dir=upload_dir)
    monkeypatch.setattr(
        citation_target_service,
        "get_app_path_settings",
        lambda: paths,
    )
    return payload


def _evidence_route(
    course_id: str,
    version: int,
    owner_type: str,
    owner_id: str,
    evidence_id: str,
) -> str:
    return (
        f"/courses/{course_id}/concept-graph/versions/{version}/"
        f"{owner_type}/{owner_id}/evidence/{evidence_id}"
    )


def test_path_apis_use_one_named_version_and_return_edge_evidence() -> None:
    course, _, _, alpha, beta, gamma, version = _published_chain()
    base = (
        f"/courses/{course.id}/concept-graph/versions/{version}/paths"
    )

    trace = client.get(
        f"{base}/trace",
        params={
            "source_concept_id": alpha.id,
            "target_concept_id": gamma.id,
            "relation_types": "prerequisite",
        },
    )
    learning = client.get(
        f"{base}/learning",
        params={"target_concept_id": gamma.id},
    )
    local = client.get(
        f"{base}/local",
        params={"root_concept_id": beta.id},
    )

    assert trace.status_code == 200, trace.text
    payload = trace.json()
    assert payload["graph_version"] == version
    assert payload["status"] == "found"
    assert payload["hop_count"] == 2
    assert [item["concept_id"] for item in payload["nodes"]] == [
        alpha.id,
        beta.id,
        gamma.id,
    ]
    assert payload["steps"][0]["relation"]["evidence"][0]["quote"] == (
        "Alpha precedes Beta"
    )
    assert payload["steps"][0]["relation"]["evidence"][0]["locator"][
        "kind"
    ] == "pdf_page"
    assert learning.status_code == 200, learning.text
    assert learning.json()["linearization"] == [alpha.id, beta.id, gamma.id]
    assert local.status_code == 200, local.text
    assert len(local.json()["nodes"]) == 3


def test_path_api_rejects_unknown_version_concept_and_invalid_bounds() -> None:
    course, _, chunk, _, _, gamma, version = _published_chain()
    base = (
        f"/courses/{course.id}/concept-graph/versions/{version}/paths"
    )

    missing_version = client.get(
        f"/courses/{course.id}/concept-graph/versions/999/paths/learning",
        params={"target_concept_id": gamma.id},
    )
    missing_concept = client.get(
        f"{base}/learning",
        params={"target_concept_id": "another-course-concept"},
    )
    invalid_hops = client.get(
        f"{base}/trace",
        params={
            "source_concept_id": gamma.id,
            "target_concept_id": gamma.id,
            "max_hops": 11,
        },
    )
    incomplete_learning_path = client.get(
        f"{base}/learning",
        params={"target_concept_id": gamma.id, "max_nodes": 1},
    )

    assert missing_version.status_code == 404
    assert missing_concept.status_code == 404
    assert invalid_hops.status_code == 422
    assert incomplete_learning_path.status_code == 413

    accepted_concept(course.id, chunk, "Alpha extension", "Alpha")
    assert _publish(course.id) == 2
    inactive_version = client.get(
        f"{base}/learning",
        params={"target_concept_id": gamma.id},
    )
    assert inactive_version.status_code == 409
    assert inactive_version.json()["detail"]["code"] == (
        "concept_graph_version_not_active"
    )

    other_course, _, other_chunk = make_course_source("path-api-isolation")
    accepted_concept(other_course.id, other_chunk, "Alpha", "Alpha")
    other_version = _publish(other_course.id)
    cross_course = client.get(
        (
            f"/courses/{other_course.id}/concept-graph/versions/"
            f"{other_version}/paths/learning"
        ),
        params={"target_concept_id": gamma.id},
    )
    assert cross_course.status_code == 404


def test_path_api_fails_closed_when_published_source_evidence_becomes_stale() -> None:
    course, source, chunk, alpha, _, gamma, version = _published_chain()
    changed_text = "The original cited claims are no longer present."
    replace_source_projection(
        source,
        [
            CourseSourceChunk(
                id=f"{chunk.id}-replacement",
                source_id=source.id,
                origin_type="source_unit",
                origin_id=f"{chunk.origin_id}-replacement",
                chunk_type="page",
                ordinal=0,
                text=changed_text,
                text_hash=hash_source_chunk_text(changed_text),
                locator=PdfPageLocator(
                    asset_id=source.origin_id,
                    page_number=1,
                ),
                chunker_version="path-api-stale-v1",
            )
        ],
    )

    response = client.get(
        (
            f"/courses/{course.id}/concept-graph/versions/{version}/"
            "paths/trace"
        ),
        params={
            "source_concept_id": alpha.id,
            "target_concept_id": gamma.id,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "concept_graph_source_authority_stale"
    )


def test_published_graph_evidence_resolves_target_and_managed_content(
    tmp_path,
    monkeypatch,
) -> None:
    course, source, _, alpha, _, _, version = _published_chain()
    payload = _managed_pdf(tmp_path, monkeypatch, course, source)
    concept_base = _evidence_route(
        course.id,
        version,
        "concepts",
        alpha.id,
        alpha.evidence[0].id,
    )
    relation = client.get(
        f"/courses/{course.id}/concept-graph/versions/{version}/relations"
    ).json()["items"][0]
    relation_base = _evidence_route(
        course.id,
        version,
        "relations",
        relation["relation_id"],
        relation["evidence"][0]["evidence_id"],
    )

    concept_target = client.get(f"{concept_base}/target")
    relation_target = client.get(f"{relation_base}/target")
    content = client.get(f"{concept_base}/content")

    assert concept_target.status_code == 200, concept_target.text
    assert relation_target.status_code == 200, relation_target.text
    assert concept_target.json()["availability"] == "available"
    assert relation_target.json()["availability"] == "available"
    assert concept_target.json()["citation_id"] == alpha.evidence[0].id
    assert concept_target.json()["locator"]["page_number"] == 1
    assert concept_target.json()["media_url"].endswith("/content")
    assert "stored_path" not in concept_target.text
    assert str(tmp_path) not in concept_target.text
    assert content.status_code == 200
    assert content.content == payload
    assert content.headers["cache-control"] == "private, no-store"

    with connect() as conn:
        conn.execute(
            "UPDATE source_chunks SET locator_json = '{' WHERE id = ?",
            (alpha.evidence[0].chunk_id,),
        )
    corrupt_target = client.get(f"{concept_base}/target")
    corrupt_content = client.get(f"{concept_base}/content")
    assert corrupt_target.status_code == 200
    assert corrupt_target.json()["availability"] == "snapshot_only"
    assert corrupt_target.json()["reason"] == "source_changed"
    assert corrupt_content.status_code == 409


def test_published_graph_evidence_enforces_composite_and_source_isolation() -> None:
    course, source, _, alpha, beta, _, version = _published_chain()
    evidence_id = alpha.evidence[0].id
    other_course, _, other_chunk = make_course_source("evidence-isolation")
    other_alpha = accepted_concept(
        other_course.id,
        other_chunk,
        "Other Alpha",
        "Alpha",
    )
    other_version = _publish(other_course.id)

    wrong_owner_base = _evidence_route(
        course.id, version, "concepts", beta.id, evidence_id
    )
    wrong_kind_base = _evidence_route(
        course.id, version, "relations", alpha.id, evidence_id
    )
    cross_course_base = _evidence_route(
        other_course.id, other_version, "concepts", alpha.id, evidence_id
    )
    cross_evidence_base = _evidence_route(
        other_course.id,
        other_version,
        "concepts",
        other_alpha.id,
        evidence_id,
    )
    wrong_owner = client.get(f"{wrong_owner_base}/target")
    wrong_kind = client.get(f"{wrong_kind_base}/target")
    cross_course = client.get(f"{cross_course_base}/target")
    cross_evidence = client.get(f"{cross_evidence_base}/target")
    assert {wrong_owner.status_code, wrong_kind.status_code} == {404}
    assert {cross_course.status_code, cross_evidence.status_code} == {404}

    with connect() as conn:
        conn.execute(
            "UPDATE sources SET course_id = ? WHERE id = ?",
            (other_course.id, source.id),
        )
    original = _evidence_route(
        course.id,
        version,
        "concepts",
        alpha.id,
        evidence_id,
    )
    moved_target = client.get(f"{original}/target")
    moved_content = client.get(f"{original}/content")
    assert moved_target.status_code == 200
    assert moved_target.json()["availability"] == "snapshot_only"
    assert moved_target.json()["reason"] == "source_changed"
    assert moved_target.json()["media_url"] is None
    assert moved_content.status_code == 409


def test_published_graph_evidence_keeps_snapshot_after_projection_drift() -> None:
    course, source, chunk, alpha, _, _, version = _published_chain()
    changed_text = "This replacement projection omits the published evidence."
    replace_source_projection(
        source,
        [
            CourseSourceChunk(
                id=f"{chunk.id}-new",
                source_id=source.id,
                origin_type="source_unit",
                origin_id=f"{chunk.origin_id}-new",
                chunk_type="page",
                ordinal=0,
                text=changed_text,
                text_hash=hash_source_chunk_text(changed_text),
                locator=PdfPageLocator(
                    asset_id=source.origin_id,
                    page_number=1,
                ),
                chunker_version="evidence-target-drift-v1",
            )
        ],
    )
    base = _evidence_route(
        course.id,
        version,
        "concepts",
        alpha.id,
        alpha.evidence[0].id,
    )

    target = client.get(f"{base}/target")
    content = client.get(f"{base}/content")

    assert target.status_code == 200
    assert target.json()["availability"] == "snapshot_only"
    assert target.json()["reason"] == "source_changed"
    assert target.json()["quote"] == "Alpha"
    assert target.json()["locator"]["page_number"] == 1
    assert target.json()["media_url"] is None
    assert content.status_code == 409
