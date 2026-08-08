from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main
import app.concept_graph_service as concept_graph_service
import app.concept_graph_store as concept_graph_store
import app.concept_graph_publication_service as publication_service
import app.concept_graph_publication_store as publication_store
import app.course_service as course_service
import app.source_projection_identity as source_projection_identity
import tests.concept_graph_publication_support as publication_support
from app.concept_graph import (
    ConceptCreate,
    ConceptRevisionEdit,
    GraphReviewRequest,
)
from app.concept_graph_service import (
    create_grounded_concept_candidate,
    edit_course_concept,
    review_course_concept,
)
from app.concept_graph_publication import GraphPublicationRequest
from app.concept_graph_publication_service import _publication_request_hash
from app.concept_graph_publication_store import connect
from app.course_source import CourseSourceChunk, PdfPageLocator, hash_source_chunk_text
from app.course_source_store import replace_source_projection
from tests.concept_graph_publication_support import (
    accepted_concept,
    accepted_relation,
    make_course_source,
)


client = TestClient(main.app)


def _publication_payload(preview: dict[str, object], operation_id: str):
    return {
        "operation_id": operation_id,
        "expected_active_version": preview["active_version"],
        "expected_draft_manifest_hash": preview["draft_manifest_hash"],
        "actor": "publisher@example.test",
        "reason": "Publish the reviewed, grounded graph snapshot.",
    }


def test_preview_publish_replay_and_immutable_snapshot_reads() -> None:
    course, _, chunk = make_course_source("functional")
    alpha = accepted_concept(course.id, chunk, "Alpha", "Alpha")
    beta = accepted_concept(course.id, chunk, "Beta", "Beta")
    relation = accepted_relation(
        course.id, chunk, alpha.id, beta.id
    )

    preview_response = client.get(
        f"/courses/{course.id}/concept-graph/publication-preview"
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["publishable"] is True
    assert preview["has_changes"] is True
    assert preview["counts"] == {
        "concepts": 2,
        "relations": 1,
        "concept_aliases": 2,
        "concept_evidence": 2,
        "relation_evidence": 1,
    }

    operation_id = uuid4().hex
    payload = _publication_payload(preview, operation_id)
    route = f"/courses/{course.id}/concept-graph/versions"
    published_response = client.post(route, json=payload)
    replay_response = client.post(route, json=payload)
    changed_reuse = client.post(
        route,
        json={**payload, "reason": "A changed request must conflict."},
    )

    assert published_response.status_code == 201, published_response.text
    published = published_response.json()
    assert replay_response.status_code == 201
    assert replay_response.json() == published
    assert changed_reuse.status_code == 409
    assert published["version_number"] == 1
    assert published["parent_version_number"] is None
    assert published["is_active_version"] is True
    assert published["content_hash"] == preview["content_hash"]
    assert published["source_authority_current"] is True

    current = client.get(f"{route}/current")
    historical = client.get(f"{route}/1")
    concepts = client.get(f"{route}/1/concepts?limit=1")
    relations = client.get(f"{route}/1/relations")
    versions = client.get(route)
    assert current.status_code == 200
    assert historical.status_code == 200
    assert versions.json()["items"][0]["version_number"] == 1
    assert concepts.status_code == 200
    assert len(concepts.json()["items"]) == 1
    assert concepts.json()["next_cursor"] is not None
    second_page = client.get(
        f"{route}/1/concepts",
        params={"cursor": concepts.json()["next_cursor"]},
    )
    assert second_page.status_code == 200
    assert len(second_page.json()["items"]) == 1
    assert relations.status_code == 200
    relation_snapshot = relations.json()["items"][0]
    assert relation_snapshot["relation_id"] == relation.id
    assert relation_snapshot["source_concept_revision"] == alpha.revision
    assert relation_snapshot["review_operation_request_hash"]
    assert len(relation_snapshot["aggregate_hash"]) == 64
    assert relation_snapshot["evidence"][0]["locator"]["kind"] == "pdf_page"

    no_change_preview = client.get(
        f"/courses/{course.id}/concept-graph/publication-preview"
    ).json()
    assert no_change_preview["publishable"] is True
    assert no_change_preview["has_changes"] is False
    no_change = client.post(
        route,
        json=_publication_payload(no_change_preview, uuid4().hex),
    )
    assert no_change.status_code == 409


def test_excluded_candidate_is_manifest_invisible_and_missing_course_is_404() -> None:
    course, _, chunk = make_course_source("excluded")
    accepted_concept(course.id, chunk, "Alpha", "Alpha")
    before = client.get(
        f"/courses/{course.id}/concept-graph/publication-preview"
    ).json()
    candidate = create_grounded_concept_candidate(
        course.id,
        ConceptCreate(
            operation_id=uuid4().hex,
            actor="author@example.test",
            reason="Leave this candidate outside publication authority.",
            preferred_name="Candidate",
            short_definition="An unreviewed candidate.",
            evidence=[{"chunk_id": chunk.id, "quote": "Gamma"}],
        ),
    )
    after = client.get(
        f"/courses/{course.id}/concept-graph/publication-preview"
    ).json()
    assert after["draft_manifest_hash"] == before["draft_manifest_hash"]
    assert after["content_hash"] == before["content_hash"]
    assert after["counts"] == before["counts"]
    edit_course_concept(
        course.id,
        candidate.id,
        ConceptRevisionEdit(
            operation_id=uuid4().hex,
            expected_revision=candidate.revision,
            actor="author@example.test",
            reason="Revise an excluded candidate head.",
            preferred_name="Candidate revised",
            short_definition="Still outside publication authority.",
            evidence=[{"chunk_id": chunk.id, "quote": "Gamma"}],
        ),
    )
    after_candidate_edit = client.get(
        f"/courses/{course.id}/concept-graph/publication-preview"
    ).json()
    assert after_candidate_edit["draft_manifest_hash"] == (
        before["draft_manifest_hash"]
    )

    missing = client.get(
        "/courses/not-a-course/concept-graph/publication-preview"
    )
    assert missing.status_code == 404


def test_source_drift_blocks_current_but_history_and_snapshot_stay_readable() -> None:
    course, source, chunk = make_course_source("drift")
    accepted_concept(course.id, chunk, "Alpha", "Alpha")
    preview = client.get(
        f"/courses/{course.id}/concept-graph/publication-preview"
    ).json()
    route = f"/courses/{course.id}/concept-graph/versions"
    assert client.post(
        route, json=_publication_payload(preview, uuid4().hex)
    ).status_code == 201
    stable_preview = client.get(
        f"/courses/{course.id}/concept-graph/publication-preview"
    ).json()

    changed_text = "A replacement projection no longer contains the quote."
    changed_chunk = CourseSourceChunk(
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
        chunker_version="publication-test-v2",
    )
    replace_source_projection(source, [changed_chunk])

    stale_publish = client.post(
        route,
        json=_publication_payload(stable_preview, uuid4().hex),
    )
    assert stale_publish.status_code == 409

    current = client.get(f"{route}/current")
    historical = client.get(f"{route}/1")
    concepts = client.get(f"{route}/1/concepts")
    assert current.status_code == 409
    assert current.json()["detail"]["code"] == (
        "concept_graph_source_authority_stale"
    )
    assert historical.status_code == 200
    assert historical.json()["source_authority_current"] is False
    assert historical.json()["source_authority_issue_count"] == 1
    assert concepts.status_code == 200
    assert len(concepts.json()["items"][0]["aggregate_hash"]) == 64
    assert concepts.json()["items"][0]["evidence"][0]["quote"] == "Alpha"


def test_publication_request_is_strict_and_expected_active_is_required() -> None:
    course, _, chunk = make_course_source("strict")
    accepted_concept(course.id, chunk, "Alpha", "Alpha")
    preview = client.get(
        f"/courses/{course.id}/concept-graph/publication-preview"
    ).json()
    payload = _publication_payload(preview, uuid4().hex)
    payload.pop("expected_active_version")
    missing = client.post(
        f"/courses/{course.id}/concept-graph/versions", json=payload
    )
    unknown = client.post(
        f"/courses/{course.id}/concept-graph/versions",
        json={
            **_publication_payload(preview, uuid4().hex),
            "unknown": True,
        },
    )
    uppercase = client.post(
        f"/courses/{course.id}/concept-graph/versions",
        json={
            **_publication_payload(preview, uuid4().hex),
            "expected_draft_manifest_hash": "A" * 64,
        },
    )
    assert missing.status_code == 422
    assert unknown.status_code == 422
    assert uppercase.status_code == 422


def test_publication_request_hash_has_a_fixed_golden_vector() -> None:
    request = GraphPublicationRequest(
        operation_id="publish-001",
        expected_active_version=None,
        expected_draft_manifest_hash="a" * 64,
        actor="Ada Lovelace",
        reason="Publish reviewed graph.",
    )
    assert _publication_request_hash("course-001", request) == (
        "5c39c052633b4219969cdb67ca04d3a3621e5c0039d979b3402bb0baa929cf9c"
    )


def test_production_preview_publish_hashes_have_fixed_golden_vectors(
    monkeypatch,
) -> None:
    """Lock complete production payloads, not a hand-written JSON subset."""

    def fixed_uuid_sequence(start: int, count: int):
        values = iter(
            SimpleNamespace(hex=f"{value:032x}")
            for value in range(start, start + count)
        )
        return lambda: next(values)

    fixed_time = datetime(2026, 8, 9, tzinfo=timezone.utc)
    monkeypatch.setattr(course_service, "uuid4", fixed_uuid_sequence(1, 1))
    monkeypatch.setattr(
        publication_support, "uuid4", fixed_uuid_sequence(100, 7)
    )
    monkeypatch.setattr(
        concept_graph_service, "uuid4", fixed_uuid_sequence(200, 8)
    )
    monkeypatch.setattr(
        concept_graph_store, "uuid4", fixed_uuid_sequence(300, 5)
    )
    monkeypatch.setattr(
        source_projection_identity, "uuid4", fixed_uuid_sequence(400, 1)
    )
    monkeypatch.setattr(concept_graph_service, "utc_now", lambda: fixed_time)
    monkeypatch.setattr(concept_graph_store, "utc_now", lambda: fixed_time)
    monkeypatch.setattr(publication_store, "utc_now", lambda: fixed_time)

    course, _, chunk = make_course_source("golden-production")
    alpha = accepted_concept(course.id, chunk, "Alpha", "Alpha")
    beta = accepted_concept(course.id, chunk, "Beta", "Beta")
    accepted_relation(course.id, chunk, alpha.id, beta.id)
    route = f"/courses/{course.id}/concept-graph/versions"

    preview_response = client.get(
        f"/courses/{course.id}/concept-graph/publication-preview"
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    publish_response = client.post(
        route,
        json=_publication_payload(preview, "publish-golden-001"),
    )
    assert publish_response.status_code == 201, publish_response.text
    concepts_response = client.get(f"{route}/1/concepts?limit=50")
    relations_response = client.get(f"{route}/1/relations?limit=50")
    assert concepts_response.status_code == 200, concepts_response.text
    assert relations_response.status_code == 200, relations_response.text
    concept_hashes = {
        item["preferred_name"]: item["aggregate_hash"]
        for item in concepts_response.json()["items"]
    }
    relation_hash = relations_response.json()["items"][0]["aggregate_hash"]
    actual = {
        "content_hash": preview["content_hash"],
        "draft_manifest_hash": preview["draft_manifest_hash"],
        "alpha_aggregate_hash": concept_hashes["Alpha"],
        "beta_aggregate_hash": concept_hashes["Beta"],
        "relation_aggregate_hash": relation_hash,
    }
    assert actual == {
        "content_hash": (
            "a059584a571dc4ab88dc21b0361212f0ac8d1d74be62feb7799d36cadecc0370"
        ),
        "draft_manifest_hash": (
            "ee882eaf45464355112a081b2bb404e32cfbdf0acb6165374b7a38378649eb52"
        ),
        "alpha_aggregate_hash": (
            "98c9d7013c0cd80cdcafae9b1210fc2aeb8ae83fc8cd5ec29897e235c77a84f1"
        ),
        "beta_aggregate_hash": (
            "1dac2bb7007272bfb37c031c86fc762ca7e97cd404d75a766dd187791c8ac7ec"
        ),
        "relation_aggregate_hash": (
            "82b34a6e6fc174a71fd2c473b373280c27bdb07f1d48bae6b64f43fe5c655350"
        ),
    }
    with connect() as conn:
        stored_concepts = {
            str(row["preferred_name"]): str(row["aggregate_hash"])
            for row in conn.execute(
                """
                SELECT preferred_name, aggregate_hash
                FROM concept_graph_version_concepts
                WHERE course_id = ? AND version_number = 1
                """,
                (course.id,),
            ).fetchall()
        }
        stored_relation = conn.execute(
            """
            SELECT aggregate_hash FROM concept_graph_version_relations
            WHERE course_id = ? AND version_number = 1
            """,
            (course.id,),
        ).fetchone()
    assert stored_concepts == concept_hashes
    assert stored_relation is not None
    assert stored_relation["aggregate_hash"] == relation_hash


def test_second_version_advances_parent_and_preserves_first_snapshot() -> None:
    course, _, chunk = make_course_source("version-two")
    concept = accepted_concept(course.id, chunk, "Alpha", "Alpha")
    route = f"/courses/{course.id}/concept-graph/versions"
    first_preview = client.get(
        f"/courses/{course.id}/concept-graph/publication-preview"
    ).json()
    first = client.post(
        route, json=_publication_payload(first_preview, uuid4().hex)
    )
    assert first.status_code == 201

    candidate = edit_course_concept(
        course.id,
        concept.id,
        ConceptRevisionEdit(
            operation_id=uuid4().hex,
            expected_revision=concept.revision,
            actor="author@example.test",
            reason="Improve the Concept definition for version two.",
            preferred_name="Alpha",
            short_definition="A revised definition for Alpha.",
            aliases=["First"],
            evidence=[{"chunk_id": chunk.id, "quote": "Alpha"}],
        ),
    )
    accepted = review_course_concept(
        course.id,
        concept.id,
        GraphReviewRequest(
            operation_id=uuid4().hex,
            expected_revision=candidate.revision,
            actor="reviewer@example.test",
            reason="The revised definition remains supported.",
            decision="accept",
        ),
    )
    second_preview = client.get(
        f"/courses/{course.id}/concept-graph/publication-preview"
    ).json()
    assert second_preview["active_version"] == 1
    assert second_preview["has_changes"] is True
    second = client.post(
        route, json=_publication_payload(second_preview, uuid4().hex)
    )
    assert second.status_code == 201, second.text
    assert second.json()["version_number"] == 2
    assert second.json()["parent_version_number"] == 1

    version_one = client.get(f"{route}/1").json()
    version_two_concepts = client.get(f"{route}/2/concepts").json()
    assert version_one["is_active_version"] is False
    assert version_one["content_hash"] == first.json()["content_hash"]
    assert version_two_concepts["items"][0]["concept_revision"] == (
        accepted.revision
    )
    assert [
        item["version_number"]
        for item in client.get(route).json()["items"]
    ] == [2, 1]


def test_sqlite_busy_maps_to_retryable_503(
    monkeypatch,
) -> None:
    course, _, _ = make_course_source("busy")

    def raise_busy(_: str):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(publication_service, "preview_publication", raise_busy)
    response = client.get(
        f"/courses/{course.id}/concept-graph/publication-preview"
    )
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
