from __future__ import annotations

from sqlite3 import IntegrityError

from fastapi.testclient import TestClient
import pytest

import app.main as main
import app.concept_graph_service as concept_graph_service
from app.course import Course, CourseCreate
from app.course_service import create_video_course
from app.course_source import (
    CourseSource,
    CourseSourceChunk,
    PdfPageLocator,
    hash_source_chunk_text,
)
from app.course_source_store import replace_source_projection
from app.db import connect


client = TestClient(main.app)


def _course_with_pdf_chunk(
    suffix: str,
    *,
    text: str = "Gradient descent uses a learning rate to update parameters.",
) -> tuple[Course, CourseSourceChunk]:
    course = create_video_course(CourseCreate(title=f"Course {suffix}"))
    source_id = f"asset:graph-{suffix}"
    chunk = CourseSourceChunk(
        id=f"source_unit:graph-{suffix}-page-1",
        source_id=source_id,
        origin_type="source_unit",
        origin_id=f"graph-{suffix}-page-1",
        chunk_type="page",
        ordinal=0,
        text=text,
        text_hash=hash_source_chunk_text(text),
        locator=PdfPageLocator(
            asset_id=f"graph-{suffix}",
            page_number=1,
        ),
        chunker_version="test-graph-v1",
    )
    replace_source_projection(
        CourseSource(
            id=source_id,
            course_id=course.id,
            origin_type="source_asset",
            origin_id=f"graph-{suffix}",
            source_type="pdf",
            title=f"Graph {suffix}.pdf",
            content_status="ready",
        ),
        [chunk],
    )
    return course, chunk


def _create_concept(
    course_id: str,
    chunk_id: str,
    *,
    name: str,
    quote: str,
) -> dict[str, object]:
    response = client.post(
        f"/courses/{course_id}/concepts",
        json={
            "preferred_name": name,
            "short_definition": f"Definition of {name}.",
            "evidence": [{"chunk_id": chunk_id, "quote": quote}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_concept_api_snapshots_current_chunk_and_isolates_courses() -> None:
    course, chunk = _course_with_pdf_chunk("concept")
    other_course, _ = _course_with_pdf_chunk("other")
    quote = "Gradient descent uses a learning rate"

    created = _create_concept(
        course.id,
        chunk.id,
        name="Gradient descent",
        quote=quote,
    )

    assert created["course_id"] == course.id
    assert created["revision"] == 1
    assert created["identity_status"] == "active"
    assert created["review_status"] == "candidate"
    assert created["validity_status"] == "current"
    assert created["proposal_origin"] == "human"
    assert created["review_actor"] is None
    assert created["provider"] is None
    assert len(created["evidence"]) == 1
    evidence = created["evidence"][0]
    assert evidence["source_id"] == chunk.source_id
    assert evidence["chunk_id"] == chunk.id
    assert evidence["chunk_text_hash"] == chunk.text_hash
    assert evidence["quote"] == quote
    assert evidence["locator"]["kind"] == "pdf_page"
    assert evidence["locator"]["page_number"] == 1

    listed = client.get(f"/courses/{course.id}/concepts")
    fetched = client.get(
        f"/courses/{course.id}/concepts/{created['id']}"
    )
    wrong_course = client.get(
        f"/courses/{other_course.id}/concepts/{created['id']}"
    )

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [created["id"]]
    assert listed.json()["items"][0]["evidence_count"] == 1
    assert listed.json()["next_cursor"] is None
    assert fetched.status_code == 200
    assert fetched.json() == created
    assert wrong_course.status_code == 404


def test_concept_creation_rolls_back_when_grounding_is_invalid() -> None:
    course, chunk = _course_with_pdf_chunk("rollback")

    response = client.post(
        f"/courses/{course.id}/concepts",
        json={
            "preferred_name": "Invented concept",
            "short_definition": "This candidate must not survive.",
            "evidence": [
                {
                    "chunk_id": chunk.id,
                    "quote": "This exact quote is absent from the Source.",
                }
            ],
        },
    )

    assert response.status_code == 422
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == 0
        assert (
            conn.execute("SELECT COUNT(*) FROM concept_evidence").fetchone()[0]
            == 0
        )


def test_concept_creation_rejects_an_out_of_course_chunk() -> None:
    course, _ = _course_with_pdf_chunk("scope-a")
    _, other_chunk = _course_with_pdf_chunk("scope-b")

    response = client.post(
        f"/courses/{course.id}/concepts",
        json={
            "preferred_name": "Cross-course concept",
            "short_definition": "This must be rejected.",
            "evidence": [
                {
                    "chunk_id": other_chunk.id,
                    "quote": "Gradient descent",
                }
            ],
        },
    )

    assert response.status_code == 404
    assert client.get(f"/courses/{course.id}/concepts").json() == {
        "items": [],
        "next_cursor": None,
    }


def test_symmetric_relation_is_canonical_and_duplicate_safe() -> None:
    course, chunk = _course_with_pdf_chunk("relation")
    left = _create_concept(
        course.id,
        chunk.id,
        name="Gradient descent",
        quote="Gradient descent",
    )
    right = _create_concept(
        course.id,
        chunk.id,
        name="Learning rate",
        quote="learning rate",
    )
    descending = sorted((left["id"], right["id"]), reverse=True)
    payload = {
        "source_concept_id": descending[0],
        "target_concept_id": descending[1],
        "relation_type": "related",
        "support_basis": "source_asserted",
        "rationale": "The source discusses both concepts in one statement.",
        "evidence": [
            {
                "chunk_id": chunk.id,
                "quote": "Gradient descent uses a learning rate",
                "support_role": "relation_assertion",
            }
        ],
    }

    created_response = client.post(
        f"/courses/{course.id}/concept-relations",
        json=payload,
    )

    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    assert (
        created["source_concept_id"],
        created["target_concept_id"],
    ) == tuple(sorted((left["id"], right["id"])))
    assert created["review_status"] == "candidate"
    assert created["validity_status"] == "current"
    assert created["proposal_origin"] == "human"
    assert created["evidence"][0]["support_role"] == "relation_assertion"

    reversed_payload = {
        **payload,
        "source_concept_id": payload["target_concept_id"],
        "target_concept_id": payload["source_concept_id"],
    }
    duplicate = client.post(
        f"/courses/{course.id}/concept-relations",
        json=reversed_payload,
    )
    listed = client.get(f"/courses/{course.id}/concept-relations")
    oversized_page = client.get(
        f"/courses/{course.id}/concept-relations?limit=21"
    )
    fetched = client.get(
        f"/courses/{course.id}/concept-relations/{created['id']}"
    )

    assert duplicate.status_code == 409
    assert oversized_page.status_code == 422
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [created["id"]]
    assert listed.json()["items"][0]["evidence_count"] == 1
    assert fetched.status_code == 200
    assert fetched.json() == created


def test_pedagogical_inference_requires_both_endpoint_evidence() -> None:
    course, chunk = _course_with_pdf_chunk("inference")
    left = _create_concept(
        course.id,
        chunk.id,
        name="Gradient descent",
        quote="Gradient descent",
    )
    right = _create_concept(
        course.id,
        chunk.id,
        name="Learning rate",
        quote="learning rate",
    )
    payload = {
        "source_concept_id": left["id"],
        "target_concept_id": right["id"],
        "relation_type": "prerequisite",
        "support_basis": "pedagogical_inference",
        "rationale": "The learner should understand the update rule first.",
        "evidence": [
            {
                "chunk_id": chunk.id,
                "quote": "Gradient descent",
                "support_role": "source_endpoint",
            }
        ],
    }

    incomplete = client.post(
        f"/courses/{course.id}/concept-relations",
        json=payload,
    )
    mismatched = client.post(
        f"/courses/{course.id}/concept-relations",
        json={
            **payload,
            "evidence": [
                {
                    "chunk_id": chunk.id,
                    "quote": "learning rate",
                    "support_role": "source_endpoint",
                },
                {
                    "chunk_id": chunk.id,
                    "quote": "Gradient descent",
                    "support_role": "target_endpoint",
                },
            ],
        },
    )
    with connect() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM concept_relations").fetchone()[0]
            == 0
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM relation_evidence").fetchone()[0]
            == 0
        )
    complete = client.post(
        f"/courses/{course.id}/concept-relations",
        json={
            **payload,
            "evidence": [
                *payload["evidence"],
                {
                    "chunk_id": chunk.id,
                    "quote": "learning rate",
                    "support_role": "target_endpoint",
                },
            ],
        },
    )

    assert incomplete.status_code == 422
    assert mismatched.status_code == 422
    assert complete.status_code == 201, complete.text


def test_relation_rejects_cross_course_endpoints_and_evidence() -> None:
    course, chunk = _course_with_pdf_chunk("relation-scope-a")
    other_course, other_chunk = _course_with_pdf_chunk("relation-scope-b")
    left = _create_concept(
        course.id,
        chunk.id,
        name="Gradient descent",
        quote="Gradient descent",
    )
    right = _create_concept(
        course.id,
        chunk.id,
        name="Learning rate",
        quote="learning rate",
    )
    other = _create_concept(
        other_course.id,
        other_chunk.id,
        name="Other course concept",
        quote="Gradient descent",
    )
    base_payload = {
        "source_concept_id": left["id"],
        "target_concept_id": right["id"],
        "relation_type": "prerequisite",
        "support_basis": "source_asserted",
        "rationale": "The source explicitly orders these concepts.",
        "evidence": [
            {
                "chunk_id": chunk.id,
                "quote": "Gradient descent uses a learning rate",
                "support_role": "relation_assertion",
            }
        ],
    }

    wrong_endpoint = client.post(
        f"/courses/{course.id}/concept-relations",
        json={**base_payload, "target_concept_id": other["id"]},
    )
    wrong_evidence = client.post(
        f"/courses/{course.id}/concept-relations",
        json={
            **base_payload,
            "evidence": [
                {
                    "chunk_id": other_chunk.id,
                    "quote": "Gradient descent",
                    "support_role": "relation_assertion",
                }
            ],
        },
    )

    assert wrong_endpoint.status_code == 404
    assert wrong_evidence.status_code == 404
    with connect() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM concept_relations").fetchone()[0]
            == 0
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM relation_evidence").fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("drift_kind", ["hash", "locator"])
def test_inference_rejects_endpoint_snapshot_drift_atomically(
    drift_kind: str,
) -> None:
    course, chunk = _course_with_pdf_chunk(f"drift-{drift_kind}")
    left = _create_concept(
        course.id,
        chunk.id,
        name="Gradient descent",
        quote="Gradient descent",
    )
    right = _create_concept(
        course.id,
        chunk.id,
        name="Learning rate",
        quote="learning rate",
    )
    next_text = (
        chunk.text + " This preserves both evidence quotes."
        if drift_kind == "hash"
        else chunk.text
    )
    next_locator = (
        chunk.locator
        if drift_kind == "hash"
        else PdfPageLocator(
            asset_id=chunk.locator.asset_id,
            page_number=2,
        )
    )
    next_chunk = chunk.model_copy(
        update={
            "text": next_text,
            "text_hash": hash_source_chunk_text(next_text),
            "locator": next_locator,
        }
    )
    replace_source_projection(
        CourseSource(
            id=chunk.source_id,
            course_id=course.id,
            origin_type="source_asset",
            origin_id=chunk.locator.asset_id,
            source_type="pdf",
            title="Drifted graph.pdf",
            content_status="ready",
        ),
        [next_chunk],
    )

    response = client.post(
        f"/courses/{course.id}/concept-relations",
        json={
            "source_concept_id": left["id"],
            "target_concept_id": right["id"],
            "relation_type": "prerequisite",
            "support_basis": "pedagogical_inference",
            "rationale": "This is a reviewed pedagogical inference.",
            "evidence": [
                {
                    "chunk_id": next_chunk.id,
                    "quote": "Gradient descent",
                    "support_role": "source_endpoint",
                },
                {
                    "chunk_id": next_chunk.id,
                    "quote": "learning rate",
                    "support_role": "target_endpoint",
                },
            ],
        },
    )

    assert response.status_code == 409
    with connect() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM concept_relations").fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM concept_relation_revisions"
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM relation_evidence").fetchone()[0]
            == 0
        )


def test_source_asserted_relation_rejects_endpoint_roles() -> None:
    course, chunk = _course_with_pdf_chunk("source-role")
    left = _create_concept(
        course.id,
        chunk.id,
        name="Gradient descent",
        quote="Gradient descent",
    )
    right = _create_concept(
        course.id,
        chunk.id,
        name="Learning rate",
        quote="learning rate",
    )

    response = client.post(
        f"/courses/{course.id}/concept-relations",
        json={
            "source_concept_id": left["id"],
            "target_concept_id": right["id"],
            "relation_type": "prerequisite",
            "support_basis": "source_asserted",
            "rationale": "Invalid role bundle.",
            "evidence": [
                {
                    "chunk_id": chunk.id,
                    "quote": "Gradient descent",
                    "support_role": "source_endpoint",
                }
            ],
        },
    )

    assert response.status_code == 422
    with connect() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM concept_relations").fetchone()[0]
            == 0
        )


def test_unexpected_integrity_error_is_not_reported_as_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course, chunk = _course_with_pdf_chunk("unexpected-integrity")

    def fail_persistence(*args, **kwargs):
        raise IntegrityError("internal constraint detail")

    monkeypatch.setattr(
        concept_graph_service,
        "create_relation_candidate",
        fail_persistence,
    )
    response = client.post(
        f"/courses/{course.id}/concept-relations",
        json={
            "source_concept_id": "concept-a",
            "target_concept_id": "concept-b",
            "relation_type": "prerequisite",
            "support_basis": "source_asserted",
            "rationale": "A valid transport request reaches persistence.",
            "evidence": [
                {
                    "chunk_id": chunk.id,
                    "quote": "Gradient descent",
                    "support_role": "relation_assertion",
                }
            ],
        },
    )

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Unexpected Concept graph service error."
    }


def test_concept_request_bounds_are_enforced_before_writes() -> None:
    course, chunk = _course_with_pdf_chunk("request-bounds")
    base = {
        "preferred_name": "Bounded concept",
        "short_definition": "The API must bound evidence input.",
    }

    too_many = client.post(
        f"/courses/{course.id}/concepts",
        json={
            **base,
            "evidence": [
                {"chunk_id": chunk.id, "quote": f"quote-{index}"}
                for index in range(33)
            ],
        },
    )
    overlong_quote = client.post(
        f"/courses/{course.id}/concepts",
        json={
            **base,
            "evidence": [{"chunk_id": chunk.id, "quote": "x" * 16_001}],
        },
    )

    assert too_many.status_code == 422
    assert overlong_quote.status_code == 422
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == 0


def test_concept_list_uses_bounded_stable_cursor_pages() -> None:
    course, chunk = _course_with_pdf_chunk("pagination")
    created_ids = {
        _create_concept(
            course.id,
            chunk.id,
            name=f"Concept {index:02d}",
            quote="Gradient descent",
        )["id"]
        for index in range(21)
    }

    oversized = client.get(f"/courses/{course.id}/concepts?limit=21")
    first = client.get(f"/courses/{course.id}/concepts?limit=20")

    assert oversized.status_code == 422
    assert first.status_code == 200
    first_body = first.json()
    assert len(first_body["items"]) == 20
    assert first_body["next_cursor"] == first_body["items"][-1]["id"]
    assert [item["id"] for item in first_body["items"]] == sorted(
        item["id"] for item in first_body["items"]
    )

    second = client.get(
        f"/courses/{course.id}/concepts",
        params={"limit": 20, "cursor": first_body["next_cursor"]},
    )
    assert second.status_code == 200
    second_body = second.json()
    assert len(second_body["items"]) == 1
    assert second_body["next_cursor"] is None
    returned_ids = {
        item["id"]
        for item in [*first_body["items"], *second_body["items"]]
    }
    assert returned_ids == created_ids
