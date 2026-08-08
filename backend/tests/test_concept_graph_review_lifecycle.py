from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3
from threading import Barrier, Event
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

import app.concept_graph_store as graph_store
import app.concept_graph_service as graph_service
import app.db as app_db
import app.main as main
from app.concept_graph import (
    ConceptCreate,
    GraphReviewRequest,
    RelationReviewRequest,
)
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
from app.source_asset import SourceAsset
from app.source_asset_store import create_source_asset


client = TestClient(main.app, raise_server_exceptions=False)


def _course_source(
    suffix: str,
) -> tuple[Course, CourseSource, CourseSourceChunk]:
    course = create_video_course(CourseCreate(title=f"Review {suffix}"))
    asset_id = f"review-{suffix}"
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
    text = "Alpha precedes Beta. Gamma contrasts with Alpha."
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
        chunker_version="review-test-v1",
    )
    replace_source_projection(source, [chunk])
    return course, source, chunk


def _create_concept(
    course_id: str,
    chunk: CourseSourceChunk,
    name: str,
    quote: str,
    *,
    aliases: list[str] | None = None,
) -> dict[str, object]:
    response = client.post(
        f"/courses/{course_id}/concepts",
        json={
            "preferred_name": name,
            "short_definition": f"Definition for {name}.",
            "aliases": aliases or [],
            "evidence": [{"chunk_id": chunk.id, "quote": quote}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _review_concept(
    course_id: str,
    concept: dict[str, object],
    *,
    operation_id: str | None = None,
) -> dict[str, object]:
    response = client.post(
        f"/courses/{course_id}/concepts/{concept['id']}/review",
        json={
            "operation_id": operation_id or uuid4().hex,
            "expected_revision": concept["revision"],
            "actor": "reviewer@example.test",
            "reason": "Evidence supports this concept.",
            "decision": "accept",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _create_relation(
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
            "evidence": [
                {
                    "chunk_id": chunk.id,
                    "quote": "Alpha precedes Beta",
                    "support_role": "relation_assertion",
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _review_relation_payload(
    relation: dict[str, object],
    *,
    operation_id: str | None = None,
    decision: str = "accept",
) -> dict[str, object]:
    binding = relation["endpoint_binding"]
    assert isinstance(binding, dict)
    return {
        "operation_id": operation_id or uuid4().hex,
        "expected_revision": relation["revision"],
        "expected_source_concept_revision": binding[
            "source_concept_revision"
        ],
        "expected_target_concept_revision": binding[
            "target_concept_revision"
        ],
        "actor": "reviewer@example.test",
        "reason": "Reviewed against exact endpoint snapshots.",
        "decision": decision,
    }


def test_alias_contract_rejects_preferred_name_and_unknown_mutation_fields() -> None:
    with pytest.raises(ValueError):
        ConceptCreate(
            preferred_name="Straße",
            short_definition="A name.",
            aliases=["STRASSE"],
            evidence=[{"chunk_id": "chunk", "quote": "quote"}],
        )

    course, _, chunk = _course_source("alias-validation")
    concept = _create_concept(course.id, chunk, "Alpha", "Alpha")
    response = client.post(
        f"/courses/{course.id}/concepts/{concept['id']}/review",
        json={
            "operation_id": uuid4().hex,
            "expected_revision": 1,
            "actor": "reviewer",
            "reason": "valid reason",
            "decision": "accept",
            "silently_ignored": True,
        },
    )
    assert response.status_code == 422


def test_concept_review_is_append_only_idempotent_and_historical() -> None:
    course, _, chunk = _course_source("concept-review")
    candidate = _create_concept(
        course.id,
        chunk,
        "Alpha",
        "Alpha",
        aliases=["First concept"],
    )
    operation_id = uuid4().hex
    payload = {
        "operation_id": operation_id,
        "expected_revision": 1,
        "actor": "reviewer",
        "reason": "The cited sentence is direct.",
        "decision": "accept",
    }
    route = f"/courses/{course.id}/concepts/{candidate['id']}/review"

    accepted_response = client.post(route, json=payload)
    replay_response = client.post(route, json=payload)
    reused_response = client.post(
        route,
        json={**payload, "reason": "A changed request must conflict."},
    )

    assert accepted_response.status_code == 200, accepted_response.text
    accepted = accepted_response.json()
    assert replay_response.status_code == 200
    assert replay_response.json() == accepted
    assert reused_response.status_code == 409
    assert accepted["revision"] == 2
    assert accepted["review_revision"] == 1
    assert accepted["review_status"] == "accepted"
    assert accepted["evidence_current"] is True
    assert accepted["eligible_for_publication"] is True
    assert accepted["aliases"][0]["id"] != candidate["aliases"][0]["id"]
    assert accepted["evidence"][0]["id"] != candidate["evidence"][0]["id"]
    assert accepted["evidence"][0]["created_at"] == (
        candidate["evidence"][0]["created_at"]
    )

    historical = client.get(
        f"/courses/{course.id}/concepts/{candidate['id']}/revisions/1"
    )
    assert historical.status_code == 200
    assert historical.json()["review_status"] == "candidate"
    assert historical.json()["is_current_revision"] is False
    assert "not_current_revision" in historical.json()["currentness_reasons"]
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_revisions WHERE concept_id = ?",
            (candidate["id"],),
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_graph_operations"
        ).fetchone()[0] == 1


def test_concurrent_reviews_use_cas_and_only_one_operation_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course, _, chunk = _course_source("concurrent-review")
    candidate = _create_concept(course.id, chunk, "Alpha", "Alpha")
    start = Barrier(2)
    transaction_entered = Event()
    release_transaction = Event()
    original_insert = graph_store._insert_concept_revision

    def hold_first_transaction(conn, concept):
        original_insert(conn, concept)
        transaction_entered.set()
        assert release_transaction.wait(5)

    monkeypatch.setattr(
        graph_store, "_insert_concept_revision", hold_first_transaction
    )

    def review(operation_id: str):
        request = GraphReviewRequest(
            operation_id=operation_id,
            expected_revision=1,
            actor="reviewer",
            reason="Concurrent independent review.",
            decision="accept",
        )
        start.wait()
        try:
            result = graph_service.review_course_concept(
                course.id, str(candidate["id"]), request
            )
            return 200, result.revision
        except graph_service.ConceptGraphConflictError:
            return 409, None

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(review, operation_id)
            for operation_id in (uuid4().hex, uuid4().hex)
        ]
        assert transaction_entered.wait(5)
        assert not any(future.done() for future in futures)
        release_transaction.set()
        responses = [future.result(timeout=10) for future in futures]
    assert sorted(status for status, _ in responses) == [200, 409]
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_revisions WHERE concept_id = ?",
            (candidate["id"],),
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_graph_operations"
        ).fetchone()[0] == 1


def test_same_operation_concurrent_and_later_replay_returns_one_revision() -> None:
    course, _, chunk = _course_source("same-operation")
    candidate = _create_concept(course.id, chunk, "Alpha", "Alpha")
    operation_id = uuid4().hex
    request = GraphReviewRequest(
        operation_id=operation_id,
        expected_revision=1,
        actor="reviewer",
        reason="One logical review retried concurrently.",
        decision="accept",
    )
    start = Barrier(2)

    def review() -> int:
        start.wait()
        return graph_service.review_course_concept(
            course.id, str(candidate["id"]), request
        ).revision

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(review), executor.submit(review)]
        assert [future.result(timeout=15) for future in futures] == [2, 2]

    # The ledger, not an in-memory receipt cache, is the replay authority. A
    # later service call opens another DB connection and returns revision 2.
    assert graph_service.review_course_concept(
        course.id, str(candidate["id"]), request
    ).revision == 2
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_revisions WHERE concept_id = ?",
            (candidate["id"],),
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_graph_operations"
        ).fetchone()[0] == 1


def test_held_sqlite_write_lock_maps_to_503_without_partial_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course, _, chunk = _course_source("busy-mapping")
    candidate = _create_concept(course.id, chunk, "Alpha", "Alpha")
    holder = app_db.get_conn()
    holder.execute("BEGIN IMMEDIATE")
    original_path = app_db.get_db_path()

    def fast_timeout_connection() -> sqlite3.Connection:
        conn = sqlite3.connect(original_path, timeout=0.02)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 20")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    monkeypatch.setattr(app_db, "get_conn", fast_timeout_connection)
    try:
        response = client.post(
            f"/courses/{course.id}/concepts/{candidate['id']}/review",
            json={
                "operation_id": uuid4().hex,
                "expected_revision": 1,
                "actor": "reviewer",
                "reason": "Exercise bounded busy handling.",
                "decision": "accept",
            },
        )
    finally:
        holder.rollback()
        holder.close()

    assert response.status_code == 503, response.text
    assert response.headers["retry-after"] == "1"
    with connect() as conn:
        assert conn.execute(
            "SELECT current_revision FROM concepts WHERE id = ?",
            (candidate["id"],),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_graph_operations"
        ).fetchone()[0] == 0


def test_unknown_sqlite_error_and_corrupt_receipt_map_to_safe_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course, _, chunk = _course_source("safe-five-hundred")
    candidate = _create_concept(course.id, chunk, "Alpha", "Alpha")
    route = f"/courses/{course.id}/concepts/{candidate['id']}/review"
    payload = {
        "operation_id": uuid4().hex,
        "expected_revision": 1,
        "actor": "reviewer",
        "reason": "Exercise persistence error mapping.",
        "decision": "accept",
    }

    original_review = graph_service.review_concept_revision

    def fail_with_io_error(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(
        graph_service, "review_concept_revision", fail_with_io_error
    )
    unknown = client.post(route, json=payload)
    assert unknown.status_code == 500
    assert unknown.json() == {"detail": "Unexpected Concept graph service error."}

    monkeypatch.setattr(
        graph_service, "review_concept_revision", original_review
    )
    accepted = client.post(route, json=payload)
    assert accepted.status_code == 200, accepted.text
    with connect() as conn:
        conn.execute(
            "DROP TRIGGER concept_graph_operation_immutable_update"
        )
        conn.execute(
            """
            UPDATE concept_graph_operations
            SET result_json = '{"entity_type":"concept"}'
            WHERE course_id = ? AND operation_id = ?
            """,
            (course.id, payload["operation_id"]),
        )

    corrupt = client.post(route, json=payload)
    assert corrupt.status_code == 500
    assert corrupt.json() == {
        "detail": "Unexpected Concept graph service error."
    }


def test_source_drift_blocks_acceptance_and_never_rewrites_history() -> None:
    course, source, chunk = _course_source("source-drift")
    candidate = _create_concept(course.id, chunk, "Alpha", "Alpha")
    drifted = chunk.model_copy(
        update={
            "locator": PdfPageLocator(
                asset_id=source.origin_id,
                page_number=2,
            )
        }
    )
    replace_source_projection(source, [drifted])

    response = client.post(
        f"/courses/{course.id}/concepts/{candidate['id']}/review",
        json={
            "operation_id": uuid4().hex,
            "expected_revision": 1,
            "actor": "reviewer",
            "reason": "Attempt after source drift.",
            "decision": "accept",
        },
    )
    assert response.status_code == 409
    fetched = client.get(
        f"/courses/{course.id}/concepts/{candidate['id']}"
    ).json()
    assert fetched["revision"] == 1
    assert fetched["validity_status"] == "current"
    assert fetched["evidence_current"] is False
    assert "evidence_not_current" in fetched["currentness_reasons"]


def test_relation_acceptance_binds_endpoints_and_guards_only_prereq_cycles() -> None:
    course, _, chunk = _course_source("relation-review")
    alpha = _review_concept(
        course.id, _create_concept(course.id, chunk, "Alpha", "Alpha")
    )
    beta = _review_concept(
        course.id, _create_concept(course.id, chunk, "Beta", "Beta")
    )
    forward = _create_relation(
        course.id, chunk, str(alpha["id"]), str(beta["id"])
    )
    assert forward["endpoint_binding"]["source_concept_revision"] == 2
    route = (
        f"/courses/{course.id}/concept-relations/{forward['id']}/review"
    )
    accepted_response = client.post(
        route, json=_review_relation_payload(forward)
    )
    assert accepted_response.status_code == 200, accepted_response.text
    accepted = accepted_response.json()
    assert accepted["eligible_for_publication"] is True
    assert accepted["endpoint_revisions_current"] is True
    with connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                UPDATE relation_endpoint_revisions
                SET source_concept_revision = source_concept_revision + 1
                WHERE relation_id = ? AND relation_revision = ?
                """,
                (forward["id"], accepted["revision"]),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE concept_relations SET relation_type = 'related' "
                "WHERE id = ?",
                (forward["id"],),
            )

    reverse = _create_relation(
        course.id, chunk, str(beta["id"]), str(alpha["id"])
    )
    rejected_cycle = client.post(
        f"/courses/{course.id}/concept-relations/{reverse['id']}/review",
        json=_review_relation_payload(reverse),
    )
    assert rejected_cycle.status_code == 409
    assert client.get(
        f"/courses/{course.id}/concept-relations/{reverse['id']}"
    ).json()["revision"] == 1

    related = _create_relation(
        course.id,
        chunk,
        str(beta["id"]),
        str(alpha["id"]),
        relation_type="related",
    )
    non_prereq = client.post(
        f"/courses/{course.id}/concept-relations/{related['id']}/review",
        json=_review_relation_payload(related),
    )
    assert non_prereq.status_code == 200, non_prereq.text


def test_concurrent_opposite_prerequisite_accepts_allow_only_one_direction() -> None:
    course, _, chunk = _course_source("concurrent-cycle")
    alpha = _review_concept(
        course.id, _create_concept(course.id, chunk, "Alpha", "Alpha")
    )
    beta = _review_concept(
        course.id, _create_concept(course.id, chunk, "Beta", "Beta")
    )
    forward = _create_relation(
        course.id, chunk, str(alpha["id"]), str(beta["id"])
    )
    reverse = _create_relation(
        course.id, chunk, str(beta["id"]), str(alpha["id"])
    )
    start = Barrier(2)

    def accept(relation: dict[str, object]) -> int:
        payload = _review_relation_payload(relation)
        request = RelationReviewRequest(**payload)
        start.wait()
        try:
            graph_service.review_course_relation(
                course.id, str(relation["id"]), request
            )
            return 200
        except graph_service.ConceptGraphConflictError:
            return 409

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(accept, forward),
            executor.submit(accept, reverse),
        ]
        statuses = [future.result(timeout=15) for future in futures]
    assert sorted(statuses) == [200, 409]
    with connect() as conn:
        accepted = conn.execute(
            """
            SELECT COUNT(*)
            FROM concept_relations AS identities
            INNER JOIN concept_relation_revisions AS revisions
                ON revisions.relation_id = identities.id
               AND revisions.revision = identities.current_revision
            WHERE identities.course_id = ?
              AND identities.relation_type = 'prerequisite'
              AND revisions.review_status = 'accepted'
              AND revisions.validity_status = 'current'
            """,
            (course.id,),
        ).fetchone()[0]
        assert accepted == 1
        assert conn.execute(
            """
            SELECT COUNT(*) FROM concept_graph_operations
            WHERE course_id = ? AND kind = 'relation_review'
            """,
            (course.id,),
        ).fetchone()[0] == 1
        assert conn.execute(
            """
            SELECT COUNT(*) FROM concept_relation_revisions
            WHERE relation_id IN (?, ?)
            """,
            (forward["id"], reverse["id"]),
        ).fetchone()[0] == 3
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    states = [
        client.get(
            f"/courses/{course.id}/concept-relations/{relation['id']}"
        ).json()
        for relation in (forward, reverse)
    ]
    failed = [item for item in states if item["review_status"] == "candidate"]
    assert len(failed) == 1
    assert failed[0]["revision"] == 1


def test_cycle_guard_keeps_dynamically_stale_accepted_edges() -> None:
    course, _, concept_chunk = _course_source("conservative-cycle")
    alpha = _review_concept(
        course.id,
        _create_concept(course.id, concept_chunk, "Alpha", "Alpha"),
    )
    beta = _review_concept(
        course.id,
        _create_concept(course.id, concept_chunk, "Beta", "Beta"),
    )

    relation_asset_id = "review-conservative-cycle-relation"
    relation_source = CourseSource(
        id=f"asset:{relation_asset_id}",
        course_id=course.id,
        origin_type="source_asset",
        origin_id=relation_asset_id,
        source_type="pdf",
        title="relation-evidence.pdf",
        content_status="ready",
    )
    create_source_asset(
        SourceAsset(
            id=relation_asset_id,
            course_id=course.id,
            asset_type="pdf",
            original_filename="relation-evidence.pdf",
            stored_path="relation-evidence.pdf",
            size_bytes=1,
            sha256="b" * 64,
            extraction_status="ready",
        )
    )
    relation_text = "Alpha precedes Beta."
    relation_chunk = CourseSourceChunk(
        id=f"source_unit:{relation_asset_id}-page-1",
        source_id=relation_source.id,
        origin_type="source_unit",
        origin_id=f"{relation_asset_id}-page-1",
        chunk_type="page",
        ordinal=0,
        text=relation_text,
        text_hash=hash_source_chunk_text(relation_text),
        locator=PdfPageLocator(asset_id=relation_asset_id, page_number=1),
        chunker_version="review-test-v1",
    )
    replace_source_projection(relation_source, [relation_chunk])
    forward = _create_relation(
        course.id,
        relation_chunk,
        str(alpha["id"]),
        str(beta["id"]),
    )
    forward_accepted = client.post(
        f"/courses/{course.id}/concept-relations/{forward['id']}/review",
        json=_review_relation_payload(forward),
    )
    assert forward_accepted.status_code == 200

    replace_source_projection(
        relation_source,
        [
            relation_chunk.model_copy(
                update={
                    "locator": PdfPageLocator(
                        asset_id=relation_asset_id,
                        page_number=2,
                    )
                }
            )
        ],
    )
    dynamically_stale = client.get(
        f"/courses/{course.id}/concept-relations/{forward['id']}"
    ).json()
    assert dynamically_stale["review_status"] == "accepted"
    assert dynamically_stale["validity_status"] == "current"
    assert dynamically_stale["eligible_for_publication"] is False
    assert "relation_evidence_not_current" in dynamically_stale[
        "currentness_reasons"
    ]

    reverse = _create_relation(
        course.id,
        concept_chunk,
        str(beta["id"]),
        str(alpha["id"]),
    )
    blocked = client.post(
        f"/courses/{course.id}/concept-relations/{reverse['id']}/review",
        json=_review_relation_payload(reverse),
    )
    assert blocked.status_code == 409


def test_concept_edit_stales_incident_relation_atomically_and_copies_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course, _, chunk = _course_source("incident-stale")
    alpha = _review_concept(
        course.id, _create_concept(course.id, chunk, "Alpha", "Alpha")
    )
    beta = _review_concept(
        course.id, _create_concept(course.id, chunk, "Beta", "Beta")
    )
    relation = _create_relation(
        course.id, chunk, str(alpha["id"]), str(beta["id"])
    )
    accepted_relation = client.post(
        f"/courses/{course.id}/concept-relations/{relation['id']}/review",
        json=_review_relation_payload(relation),
    ).json()

    payload = {
        "operation_id": uuid4().hex,
        "expected_revision": alpha["revision"],
        "actor": "editor",
        "reason": "Use a clearer canonical label.",
        "preferred_name": "Alpha concept",
        "short_definition": "A clearer definition of Alpha.",
        "aliases": ["Alpha"],
        "evidence": [{"chunk_id": chunk.id, "quote": "Alpha"}],
    }
    response = client.patch(
        f"/courses/{course.id}/concepts/{alpha['id']}", json=payload
    )
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 3
    assert response.json()["review_status"] == "candidate"
    stale_relation = client.get(
        f"/courses/{course.id}/concept-relations/{relation['id']}"
    ).json()
    assert stale_relation["revision"] == accepted_relation["revision"] + 1
    assert stale_relation["review_status"] == "accepted"
    assert stale_relation["validity_status"] == "stale"
    assert "validity_not_current" in stale_relation["currentness_reasons"]
    assert stale_relation["evidence"][0]["id"] != (
        accepted_relation["evidence"][0]["id"]
    )

    # Build a second accepted/current edge so the injected failure exercises
    # incident invalidation, not only the Concept revision write.
    gamma = _review_concept(
        course.id,
        _create_concept(
            course.id,
            chunk,
            "Gamma",
            "Gamma",
            aliases=["Third concept"],
        ),
    )
    rollback_relation = _create_relation(
        course.id, chunk, str(gamma["id"]), str(beta["id"])
    )
    rollback_relation = client.post(
        (
            f"/courses/{course.id}/concept-relations/"
            f"{rollback_relation['id']}/review"
        ),
        json=_review_relation_payload(rollback_relation),
    ).json()
    with connect() as conn:
        before_counts = {
            "concept_revisions": conn.execute(
                "SELECT COUNT(*) FROM concept_revisions WHERE concept_id = ?",
                (gamma["id"],),
            ).fetchone()[0],
            "concept_aliases": conn.execute(
                "SELECT COUNT(*) FROM concept_aliases WHERE concept_id = ?",
                (gamma["id"],),
            ).fetchone()[0],
            "concept_evidence": conn.execute(
                "SELECT COUNT(*) FROM concept_evidence WHERE concept_id = ?",
                (gamma["id"],),
            ).fetchone()[0],
            "relation_revisions": conn.execute(
                "SELECT COUNT(*) FROM concept_relation_revisions "
                "WHERE relation_id = ?",
                (rollback_relation["id"],),
            ).fetchone()[0],
            "relation_evidence": conn.execute(
                "SELECT COUNT(*) FROM relation_evidence WHERE relation_id = ?",
                (rollback_relation["id"],),
            ).fetchone()[0],
            "endpoint_bindings": conn.execute(
                "SELECT COUNT(*) FROM relation_endpoint_revisions "
                "WHERE relation_id = ?",
                (rollback_relation["id"],),
            ).fetchone()[0],
            "operations": conn.execute(
                "SELECT COUNT(*) FROM concept_graph_operations"
            ).fetchone()[0],
        }

    def fail_record(*args, **kwargs):
        raise RuntimeError("injected ledger failure")

    monkeypatch.setattr(graph_store, "_record_operation", fail_record)
    failed = client.post(
        f"/courses/{course.id}/concepts/{gamma['id']}/mark-stale",
        json={
            "operation_id": uuid4().hex,
            "expected_revision": gamma["revision"],
            "actor": "editor",
            "reason": "Injected rollback test.",
        },
    )
    assert failed.status_code == 500
    with connect() as conn:
        assert conn.execute(
            "SELECT current_revision FROM concepts WHERE id = ?",
            (gamma["id"],),
        ).fetchone()[0] == gamma["revision"]
        assert conn.execute(
            "SELECT current_revision FROM concept_relations WHERE id = ?",
            (rollback_relation["id"],),
        ).fetchone()[0] == rollback_relation["revision"]
        after_counts = {
            "concept_revisions": conn.execute(
                "SELECT COUNT(*) FROM concept_revisions WHERE concept_id = ?",
                (gamma["id"],),
            ).fetchone()[0],
            "concept_aliases": conn.execute(
                "SELECT COUNT(*) FROM concept_aliases WHERE concept_id = ?",
                (gamma["id"],),
            ).fetchone()[0],
            "concept_evidence": conn.execute(
                "SELECT COUNT(*) FROM concept_evidence WHERE concept_id = ?",
                (gamma["id"],),
            ).fetchone()[0],
            "relation_revisions": conn.execute(
                "SELECT COUNT(*) FROM concept_relation_revisions "
                "WHERE relation_id = ?",
                (rollback_relation["id"],),
            ).fetchone()[0],
            "relation_evidence": conn.execute(
                "SELECT COUNT(*) FROM relation_evidence WHERE relation_id = ?",
                (rollback_relation["id"],),
            ).fetchone()[0],
            "endpoint_bindings": conn.execute(
                "SELECT COUNT(*) FROM relation_endpoint_revisions "
                "WHERE relation_id = ?",
                (rollback_relation["id"],),
            ).fetchone()[0],
            "operations": conn.execute(
                "SELECT COUNT(*) FROM concept_graph_operations"
            ).fetchone()[0],
        }
        assert after_counts == before_counts


def test_stale_bound_candidate_can_be_rejected_after_endpoint_change() -> None:
    course, _, chunk = _course_source("reject-stale")
    alpha = _review_concept(
        course.id, _create_concept(course.id, chunk, "Alpha", "Alpha")
    )
    beta = _review_concept(
        course.id, _create_concept(course.id, chunk, "Beta", "Beta")
    )
    relation = _create_relation(
        course.id, chunk, str(alpha["id"]), str(beta["id"])
    )
    edit = client.patch(
        f"/courses/{course.id}/concepts/{alpha['id']}",
        json={
            "operation_id": uuid4().hex,
            "expected_revision": 2,
            "actor": "editor",
            "reason": "Advance the endpoint.",
            "preferred_name": "Alpha revised",
            "short_definition": "Revised Alpha.",
            "aliases": ["Alpha"],
            "evidence": [{"chunk_id": chunk.id, "quote": "Alpha"}],
        },
    )
    assert edit.status_code == 200
    stale = client.get(
        f"/courses/{course.id}/concept-relations/{relation['id']}"
    ).json()
    assert stale["validity_status"] == "stale"
    rejected = client.post(
        f"/courses/{course.id}/concept-relations/{relation['id']}/review",
        json=_review_relation_payload(stale, decision="reject"),
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["review_status"] == "rejected"
    assert rejected.json()["validity_status"] == "stale"


def test_relation_edit_regrounds_candidate_with_exact_current_binding() -> None:
    course, _, chunk = _course_source("relation-edit")
    alpha = _review_concept(
        course.id, _create_concept(course.id, chunk, "Alpha", "Alpha")
    )
    beta = _review_concept(
        course.id, _create_concept(course.id, chunk, "Beta", "Beta")
    )
    relation = _create_relation(
        course.id, chunk, str(alpha["id"]), str(beta["id"])
    )
    edit_payload = {
        "operation_id": uuid4().hex,
        "expected_revision": 1,
        "expected_source_concept_revision": alpha["revision"],
        "expected_target_concept_revision": beta["revision"],
        "actor": "editor",
        "reason": "Clarify and reground the relation candidate.",
        "support_basis": "source_asserted",
        "rationale": "The source directly orders Alpha before Beta.",
        "evidence": [
            {
                "chunk_id": chunk.id,
                "quote": "Alpha precedes Beta",
                "support_role": "relation_assertion",
            }
        ],
    }
    route = f"/courses/{course.id}/concept-relations/{relation['id']}"
    edited_response = client.patch(route, json=edit_payload)
    replay = client.patch(route, json=edit_payload)
    assert edited_response.status_code == 200, edited_response.text
    assert replay.status_code == 200
    edited = edited_response.json()
    assert replay.json() == edited
    assert edited["revision"] == 2
    assert edited["review_status"] == "candidate"
    assert edited["endpoint_binding"]["source_concept_revision"] == 2
    assert edited["endpoint_binding"]["target_concept_revision"] == 2
    historical = client.get(f"{route}/revisions/1")
    assert historical.status_code == 200
    assert historical.json()["is_current_revision"] is False

    accepted = client.post(
        f"{route}/review",
        json=_review_relation_payload(edited),
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["revision"] == 3
    assert accepted.json()["eligible_for_publication"] is True


def test_legacy_relation_without_binding_can_reject_but_cannot_accept() -> None:
    course, _, chunk = _course_source("legacy-binding")
    alpha = _review_concept(
        course.id, _create_concept(course.id, chunk, "Alpha", "Alpha")
    )
    beta = _review_concept(
        course.id, _create_concept(course.id, chunk, "Beta", "Beta")
    )
    relation = _create_relation(
        course.id, chunk, str(alpha["id"]), str(beta["id"])
    )
    with connect() as conn:
        conn.execute(
            "DELETE FROM relation_endpoint_revisions WHERE relation_id = ?",
            (relation["id"],),
        )
    route = (
        f"/courses/{course.id}/concept-relations/{relation['id']}/review"
    )
    base = {
        "expected_revision": 1,
        "expected_source_concept_revision": alpha["revision"],
        "expected_target_concept_revision": beta["revision"],
        "actor": "reviewer",
        "reason": "Apply explicit legacy review policy.",
    }
    cannot_accept = client.post(
        route,
        json={
            **base,
            "operation_id": uuid4().hex,
            "decision": "accept",
        },
    )
    assert cannot_accept.status_code == 422
    rejected = client.post(
        route,
        json={
            **base,
            "operation_id": uuid4().hex,
            "decision": "reject",
        },
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["review_status"] == "rejected"
    assert rejected.json()["endpoint_binding"] is None
    assert "legacy_endpoint_binding" in rejected.json()[
        "currentness_reasons"
    ]
