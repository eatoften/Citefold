from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main
from app.concept_graph_publication import GraphPublicationRequest
from app.course_source import (
    CourseSourceChunk,
    PdfPageLocator,
    hash_source_chunk_text,
)
from app.course_source_store import replace_source_projection
from tests.concept_graph_publication_support import (
    accepted_concept,
    accepted_relation,
    make_course_source,
)


client = TestClient(main.app)


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
