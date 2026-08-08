from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

import app.concept_graph_service as graph_service
import app.concept_graph_store as graph_store
import app.main as main
from app.concept_graph import (
    ConceptMergeRequest,
    ConceptRetireRequest,
    RelationReviewRequest,
)
from app.course import CourseCreate
from app.course_service import create_video_course
from app.course_source import (
    CourseSource,
    CourseSourceChunk,
    PdfPageLocator,
    hash_source_chunk_text,
)
from app.course_source_store import replace_source_projection
from app.db import connect
from app.source_asset import SourceAsset
from app.source_asset_store import create_source_asset


client = TestClient(main.app, raise_server_exceptions=False)


def _course_chunk(suffix: str) -> tuple[str, CourseSourceChunk]:
    course = create_video_course(CourseCreate(title=f"Identity {suffix}"))
    asset_id = f"identity-{suffix}"
    source_id = f"asset:{asset_id}"
    create_source_asset(
        SourceAsset(
            id=asset_id,
            course_id=course.id,
            asset_type="pdf",
            original_filename=f"{suffix}.pdf",
            stored_path=f"{suffix}.pdf",
            size_bytes=1,
            sha256="a" * 64,
            extraction_status="ready",
        )
    )
    text = "Alpha precedes Beta. Gamma is another name for Alpha."
    source = CourseSource(
        id=source_id,
        course_id=course.id,
        origin_type="source_asset",
        origin_id=asset_id,
        source_type="pdf",
        title=f"{suffix}.pdf",
        content_status="ready",
    )
    chunk = CourseSourceChunk(
        id=f"source_unit:{asset_id}-page-1",
        source_id=source_id,
        origin_type="source_unit",
        origin_id=f"{asset_id}-page-1",
        chunk_type="page",
        ordinal=0,
        text=text,
        text_hash=hash_source_chunk_text(text),
        locator=PdfPageLocator(asset_id=asset_id, page_number=1),
        chunker_version="identity-test-v1",
    )
    replace_source_projection(source, [chunk])
    return course.id, chunk


def _concept(
    course_id: str,
    chunk: CourseSourceChunk,
    name: str,
    *,
    aliases: list[str] | None = None,
) -> dict[str, object]:
    response = client.post(
        f"/courses/{course_id}/concepts",
        json={
            "preferred_name": name,
            "short_definition": f"Definition for {name}.",
            "aliases": aliases or [],
            "evidence": [{"chunk_id": chunk.id, "quote": name}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _relation(
    course_id: str,
    chunk: CourseSourceChunk,
    source_id: str,
    target_id: str,
    *,
    relation_type: str = "prerequisite",
) -> dict[str, object]:
    response = client.post(
        f"/courses/{course_id}/concept-relations",
        json={
            "source_concept_id": source_id,
            "target_concept_id": target_id,
            "relation_type": relation_type,
            "support_basis": "source_asserted",
            "rationale": "The source explicitly supports the relation.",
            "evidence": [{
                "chunk_id": chunk.id,
                "quote": "Alpha precedes Beta",
                "support_role": "relation_assertion",
            }],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _accept_concept(
    course_id: str, concept: dict[str, object]
) -> dict[str, object]:
    response = client.post(
        f"/courses/{course_id}/concepts/{concept['id']}/review",
        json={
            "operation_id": uuid4().hex,
            "expected_revision": concept["revision"],
            "actor": "reviewer@example.test",
            "reason": "The Concept is grounded.",
            "decision": "accept",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _merge_payload(
    source: dict[str, object],
    survivor: dict[str, object],
    *,
    operation_id: str | None = None,
) -> dict[str, object]:
    return {
        "operation_id": operation_id or uuid4().hex,
        "expected_revision": source["revision"],
        "survivor_concept_id": survivor["id"],
        "expected_survivor_revision": survivor["revision"],
        "actor": "reviewer@example.test",
        "reason": "The source duplicates the survivor.",
    }


def _retire_payload(
    concept: dict[str, object],
    *,
    operation_id: str | None = None,
) -> dict[str, object]:
    return {
        "operation_id": operation_id or uuid4().hex,
        "expected_revision": concept["revision"],
        "actor": "reviewer@example.test",
        "reason": "The Concept is outside the maintained scope.",
    }


def test_merge_appends_terminal_revision_and_stales_incident_relations() -> None:
    course_id, chunk = _course_chunk("merge-happy")
    source = _concept(course_id, chunk, "Alpha", aliases=["First"])
    survivor = _concept(course_id, chunk, "Gamma", aliases=["Third"])
    other = _concept(course_id, chunk, "Beta")
    relation = _relation(
        course_id, chunk, str(source["id"]), str(other["id"])
    )
    inbound = _relation(
        course_id,
        chunk,
        str(other["id"]),
        str(source["id"]),
        relation_type="part_of",
    )
    nonincident = _relation(
        course_id, chunk, str(survivor["id"]), str(other["id"])
    )
    already_stale = _relation(
        course_id, chunk, str(source["id"]), str(survivor["id"])
    )
    stale_response = client.post(
        f"/courses/{course_id}/concept-relations/"
        f"{already_stale['id']}/mark-stale",
        json={
            "operation_id": uuid4().hex,
            "expected_revision": 1,
            "actor": "reviewer@example.test",
            "reason": "This edge was already known to be stale.",
        },
    )
    assert stale_response.status_code == 200, stale_response.text

    response = client.post(
        f"/courses/{course_id}/concepts/{source['id']}/merge",
        json=_merge_payload(source, survivor),
    )
    assert response.status_code == 200, response.text
    merged = response.json()
    assert merged["revision"] == 2
    assert merged["identity_status"] == "merged"
    assert merged["merged_into_concept_id"] == survivor["id"]
    assert merged["review_status"] == source["review_status"]
    assert merged["validity_status"] == source["validity_status"]
    assert [item["display_text"] for item in merged["aliases"]] == ["First"]
    assert len(merged["evidence"]) == len(source["evidence"])
    assert merged["eligible_for_publication"] is False
    assert "identity_not_active" in merged["currentness_reasons"]

    stale_relation = client.get(
        f"/courses/{course_id}/concept-relations/{relation['id']}"
    ).json()
    assert stale_relation["revision"] == 2
    assert stale_relation["validity_status"] == "stale"
    stale_inbound = client.get(
        f"/courses/{course_id}/concept-relations/{inbound['id']}"
    ).json()
    assert stale_inbound["revision"] == 2
    assert stale_inbound["validity_status"] == "stale"
    untouched = client.get(
        f"/courses/{course_id}/concept-relations/{nonincident['id']}"
    ).json()
    assert untouched["revision"] == 1
    assert untouched["validity_status"] == "current"
    preserved_stale = client.get(
        f"/courses/{course_id}/concept-relations/{already_stale['id']}"
    ).json()
    assert preserved_stale["revision"] == 2
    assert preserved_stale["validity_status"] == "stale"
    unchanged_survivor = client.get(
        f"/courses/{course_id}/concepts/{survivor['id']}"
    ).json()
    assert unchanged_survivor["revision"] == survivor["revision"]
    assert [item["display_text"] for item in unchanged_survivor["aliases"]] == [
        "Third"
    ]

    historical = client.get(
        f"/courses/{course_id}/concepts/{source['id']}/revisions/1"
    ).json()
    assert historical["identity_status"] == "active"
    with connect() as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_retire_is_replay_safe_and_terminal_identities_reject_mutations() -> None:
    course_id, chunk = _course_chunk("retire-replay")
    concept = _concept(course_id, chunk, "Alpha", aliases=["First"])
    operation_id = uuid4().hex
    payload = _retire_payload(concept, operation_id=operation_id)
    route = f"/courses/{course_id}/concepts/{concept['id']}/retire"

    first = client.post(route, json=payload)
    replay = client.post(route, json=payload)
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    retired = first.json()
    assert retired["revision"] == 2
    assert retired["identity_status"] == "retired"
    assert retired["merged_into_concept_id"] is None
    assert [item["display_text"] for item in retired["aliases"]] == ["First"]

    changed = dict(payload, reason="A different operation body.")
    assert client.post(route, json=changed).status_code == 409
    for suffix, body in (
        ("review", dict(payload, decision="accept")),
        ("mark-stale", payload),
    ):
        terminal_request = dict(body, operation_id=uuid4().hex, expected_revision=2)
        response = client.post(
            f"/courses/{course_id}/concepts/{concept['id']}/{suffix}",
            json=terminal_request,
        )
        assert response.status_code == 422, response.text
    edit = client.patch(
        f"/courses/{course_id}/concepts/{concept['id']}",
        json={
            **dict(payload, operation_id=uuid4().hex, expected_revision=2),
            "preferred_name": "Changed",
            "short_definition": "Changed definition.",
            "aliases": [],
            "evidence": [{"chunk_id": chunk.id, "quote": "Alpha"}],
        },
    )
    assert edit.status_code == 422, edit.text
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_graph_operations "
            "WHERE course_id = ? AND operation_id = ?",
            (course_id, operation_id),
        ).fetchone()[0] == 1


def test_merge_validates_both_heads_course_self_and_redirect_dependency() -> None:
    course_id, chunk = _course_chunk("merge-validation")
    source = _concept(course_id, chunk, "Alpha")
    survivor = _concept(course_id, chunk, "Gamma")
    route = f"/courses/{course_id}/concepts/{source['id']}/merge"

    self_payload = _merge_payload(source, source)
    assert client.post(route, json=self_payload).status_code == 422
    missing_id = uuid4().hex
    missing = client.post(
        f"/courses/{course_id}/concepts/{missing_id}/merge",
        json={
            **_merge_payload(source, survivor),
            "expected_revision": 1,
            "survivor_concept_id": missing_id,
        },
    )
    assert missing.status_code == 404
    stale_source = dict(_merge_payload(source, survivor), expected_revision=99)
    assert client.post(route, json=stale_source).status_code == 409
    stale_survivor = dict(
        _merge_payload(source, survivor), expected_survivor_revision=99
    )
    assert client.post(route, json=stale_survivor).status_code == 409

    other_course_id, other_chunk = _course_chunk("merge-other-course")
    other = _concept(other_course_id, other_chunk, "Alpha")
    cross_course = _merge_payload(source, other)
    assert client.post(route, json=cross_course).status_code == 404

    first = client.post(route, json=_merge_payload(source, survivor))
    assert first.status_code == 200, first.text
    second_source = _concept(course_id, chunk, "Beta")
    star = client.post(
        f"/courses/{course_id}/concepts/{second_source['id']}/merge",
        json=_merge_payload(second_source, survivor),
    )
    assert star.status_code == 200, star.text
    target = _concept(course_id, chunk, "Alpha precedes Beta")
    dependency = client.post(
        f"/courses/{course_id}/concepts/{survivor['id']}/merge",
        json=_merge_payload(survivor, target),
    )
    assert dependency.status_code == 409, dependency.text
    retire_dependency = client.post(
        f"/courses/{course_id}/concepts/{survivor['id']}/retire",
        json=_retire_payload(survivor),
    )
    assert retire_dependency.status_code == 409, retire_dependency.text


def test_concurrent_merge_cas_has_one_winner_and_replay_converges() -> None:
    course_id, chunk = _course_chunk("merge-concurrent")
    source = _concept(course_id, chunk, "Alpha")
    survivor = _concept(course_id, chunk, "Gamma")
    barrier = Barrier(2)

    def merge(operation_id: str) -> tuple[str, int | None]:
        request = ConceptMergeRequest(
            **_merge_payload(source, survivor, operation_id=operation_id)
        )
        barrier.wait()
        try:
            result = graph_service.merge_course_concept(
                course_id, str(source["id"]), request
            )
            return "ok", result.revision
        except graph_service.ConceptGraphConflictError:
            return "conflict", None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(merge, [uuid4().hex, uuid4().hex]))
    assert sorted(item[0] for item in results) == ["conflict", "ok"]
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_revisions WHERE concept_id = ?",
            (source["id"],),
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_graph_operations "
            "WHERE course_id = ? AND kind = 'concept_merge'",
            (course_id,),
        ).fetchone()[0] == 1

    replay_source = _concept(course_id, chunk, "Beta")
    operation_id = uuid4().hex
    request = ConceptMergeRequest(
        **_merge_payload(replay_source, survivor, operation_id=operation_id)
    )
    barrier = Barrier(2)

    def replay() -> int:
        barrier.wait()
        return graph_service.merge_course_concept(
            course_id, str(replay_source["id"]), request
        ).revision

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(lambda _: replay(), range(2))) == [2, 2]


def test_merge_rolls_back_revision_incident_edges_and_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course_id, chunk = _course_chunk("merge-rollback")
    source = _concept(course_id, chunk, "Alpha")
    survivor = _concept(course_id, chunk, "Gamma")
    other = _concept(course_id, chunk, "Beta")
    relation = _relation(
        course_id, chunk, str(source["id"]), str(other["id"])
    )
    original = graph_store._record_operation

    def fail_receipt(*args: object, **kwargs: object) -> None:
        raise sqlite3.IntegrityError("injected ledger failure")

    monkeypatch.setattr(graph_store, "_record_operation", fail_receipt)
    request = ConceptMergeRequest(**_merge_payload(source, survivor))
    with pytest.raises(graph_service.ConceptGraphPersistenceError):
        graph_service.merge_course_concept(
            course_id, str(source["id"]), request
        )
    monkeypatch.setattr(graph_store, "_record_operation", original)

    assert client.get(
        f"/courses/{course_id}/concepts/{source['id']}"
    ).json()["revision"] == 1
    assert client.get(
        f"/courses/{course_id}/concept-relations/{relation['id']}"
    ).json()["revision"] == 1
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_graph_operations "
            "WHERE course_id = ? AND kind = 'concept_merge'",
            (course_id,),
        ).fetchone()[0] == 0


def test_retire_rolls_back_revision_incident_edges_and_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course_id, chunk = _course_chunk("retire-rollback")
    source = _concept(course_id, chunk, "Alpha")
    other = _concept(course_id, chunk, "Beta")
    relation = _relation(
        course_id, chunk, str(source["id"]), str(other["id"])
    )

    def fail_receipt(*args: object, **kwargs: object) -> None:
        raise sqlite3.IntegrityError("injected ledger failure")

    monkeypatch.setattr(graph_store, "_record_operation", fail_receipt)
    request = ConceptRetireRequest(**_retire_payload(source))
    with pytest.raises(graph_service.ConceptGraphPersistenceError):
        graph_service.retire_course_concept(
            course_id, str(source["id"]), request
        )

    assert client.get(
        f"/courses/{course_id}/concepts/{source['id']}"
    ).json()["revision"] == 1
    assert client.get(
        f"/courses/{course_id}/concept-relations/{relation['id']}"
    ).json()["revision"] == 1
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_graph_operations "
            "WHERE course_id = ? AND kind = 'concept_retire'",
            (course_id,),
        ).fetchone()[0] == 0


def test_v12_revision_owned_rows_are_update_immutable() -> None:
    course_id, chunk = _course_chunk("immutable")
    concept = _concept(course_id, chunk, "Alpha", aliases=["First"])
    other = _concept(course_id, chunk, "Beta")
    relation = _relation(
        course_id, chunk, str(concept["id"]), str(other["id"])
    )
    statements = (
        ("UPDATE concept_revisions SET preferred_name = 'x' WHERE concept_id = ?", concept["id"]),
        ("UPDATE concept_evidence SET quote = 'x' WHERE concept_id = ?", concept["id"]),
        ("UPDATE concept_aliases SET display_text = 'x' WHERE concept_id = ?", concept["id"]),
        ("UPDATE concept_relation_revisions SET rationale = 'x' WHERE relation_id = ?", relation["id"]),
        ("UPDATE relation_evidence SET quote = 'x' WHERE relation_id = ?", relation["id"]),
        ("UPDATE relation_endpoint_revisions SET source_concept_revision = 99 WHERE relation_id = ?", relation["id"]),
    )
    with connect() as conn:
        for statement, entity_id in statements:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(statement, (entity_id,))
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_opposite_merges_cannot_create_a_redirect_chain_or_cycle() -> None:
    course_id, chunk = _course_chunk("opposite-merge")
    left = _concept(course_id, chunk, "Alpha")
    right = _concept(course_id, chunk, "Gamma")
    barrier = Barrier(2)

    def merge(
        source: dict[str, object], survivor: dict[str, object]
    ) -> str:
        request = ConceptMergeRequest(**_merge_payload(source, survivor))
        barrier.wait()
        try:
            graph_service.merge_course_concept(
                course_id, str(source["id"]), request
            )
            return "ok"
        except (
            graph_service.ConceptGraphConflictError,
            graph_service.InvalidConceptGraphRequestError,
        ):
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = [
            executor.submit(merge, left, right),
            executor.submit(merge, right, left),
        ]
        assert sorted(item.result() for item in outcomes) == ["conflict", "ok"]

    states = {
        concept["id"]: client.get(
            f"/courses/{course_id}/concepts/{concept['id']}"
        ).json()
        for concept in (left, right)
    }
    merged = [item for item in states.values() if item["identity_status"] == "merged"]
    active = [item for item in states.values() if item["identity_status"] == "active"]
    assert len(merged) == len(active) == 1
    assert merged[0]["merged_into_concept_id"] == active[0]["id"]


def test_merge_and_survivor_retire_race_has_one_winner() -> None:
    course_id, chunk = _course_chunk("merge-retire-race")
    source = _concept(course_id, chunk, "Alpha")
    survivor = _concept(course_id, chunk, "Gamma")
    merge_request = ConceptMergeRequest(**_merge_payload(source, survivor))
    retire_request = ConceptRetireRequest(**_retire_payload(survivor))
    barrier = Barrier(2)

    def merge() -> str:
        barrier.wait()
        try:
            graph_service.merge_course_concept(
                course_id, str(source["id"]), merge_request
            )
            return "merge"
        except (
            graph_service.ConceptGraphConflictError,
            graph_service.InvalidConceptGraphRequestError,
        ):
            return "conflict"

    def retire() -> str:
        barrier.wait()
        try:
            graph_service.retire_course_concept(
                course_id, str(survivor["id"]), retire_request
            )
            return "retire"
        except graph_service.ConceptGraphConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = [executor.submit(merge), executor.submit(retire)]
        values = [item.result() for item in outcomes]
    assert values.count("conflict") == 1
    assert ("merge" in values) != ("retire" in values)

    source_state = client.get(
        f"/courses/{course_id}/concepts/{source['id']}"
    ).json()
    survivor_state = client.get(
        f"/courses/{course_id}/concepts/{survivor['id']}"
    ).json()
    if "merge" in values:
        assert source_state["identity_status"] == "merged"
        assert survivor_state["identity_status"] == "active"
    else:
        assert source_state["identity_status"] == "active"
        assert survivor_state["identity_status"] == "retired"


def test_merge_and_relation_accept_race_always_leaves_edge_stale() -> None:
    course_id, chunk = _course_chunk("merge-relation-race")
    source = _accept_concept(
        course_id, _concept(course_id, chunk, "Alpha")
    )
    target = _accept_concept(
        course_id, _concept(course_id, chunk, "Beta")
    )
    survivor = _concept(course_id, chunk, "Gamma")
    relation = _relation(
        course_id, chunk, str(source["id"]), str(target["id"])
    )
    merge_request = ConceptMergeRequest(**_merge_payload(source, survivor))
    binding = relation["endpoint_binding"]
    assert isinstance(binding, dict)
    review_request = RelationReviewRequest(
        operation_id=uuid4().hex,
        expected_revision=relation["revision"],
        expected_source_concept_revision=binding["source_concept_revision"],
        expected_target_concept_revision=binding["target_concept_revision"],
        actor="reviewer@example.test",
        reason="The relation is grounded.",
        decision="accept",
    )
    barrier = Barrier(2)

    def merge() -> str:
        barrier.wait()
        try:
            graph_service.merge_course_concept(
                course_id, str(source["id"]), merge_request
            )
            return "ok"
        except graph_service.ConceptGraphConflictError:
            return "conflict"

    def review() -> str:
        barrier.wait()
        try:
            graph_service.review_course_relation(
                course_id, str(relation["id"]), review_request
            )
            return "ok"
        except (
            graph_service.ConceptGraphConflictError,
            graph_service.InvalidConceptGraphRequestError,
        ):
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [executor.submit(merge), executor.submit(review)]
        assert [item.result() for item in results].count("ok") >= 1
    relation_state = client.get(
        f"/courses/{course_id}/concept-relations/{relation['id']}"
    ).json()
    assert relation_state["validity_status"] == "stale"
    assert relation_state["eligible_for_publication"] is False


def test_normalization_allows_stale_evidence_but_terminal_is_not_publishable() -> None:
    course_id, chunk = _course_chunk("merge-drift")
    source = _concept(course_id, chunk, "Alpha")
    survivor = _concept(course_id, chunk, "Gamma")
    asset_id = str(chunk.source_id).split(":", 1)[1]
    with connect() as conn:
        conn.execute(
            "UPDATE source_assets SET deleted_at = updated_at WHERE id = ?",
            (asset_id,),
        )

    stale_before = client.get(
        f"/courses/{course_id}/concepts/{source['id']}"
    ).json()
    assert stale_before["evidence_current"] is False
    response = client.post(
        f"/courses/{course_id}/concepts/{source['id']}/merge",
        json=_merge_payload(source, survivor),
    )
    assert response.status_code == 200, response.text
    merged = response.json()
    assert merged["identity_status"] == "merged"
    assert merged["eligible_for_publication"] is False
    assert {"identity_not_active", "evidence_not_current"} <= set(
        merged["currentness_reasons"]
    )


def test_clear_graph_handles_current_merge_redirect_foreign_keys() -> None:
    course_id, chunk = _course_chunk("merge-clear")
    source = _concept(course_id, chunk, "Alpha")
    survivor = _concept(course_id, chunk, "Gamma")
    response = client.post(
        f"/courses/{course_id}/concepts/{source['id']}/merge",
        json=_merge_payload(source, survivor),
    )
    assert response.status_code == 200, response.text

    graph_store.clear_concept_graph()
    with connect() as conn:
        for table in (
            "concept_graph_operations",
            "concept_relations",
            "concept_revisions",
            "concepts",
        ):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
