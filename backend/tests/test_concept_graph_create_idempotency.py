from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

import app.main as main
import app.concept_graph_service as graph_service
import app.concept_graph_store as graph_store
import app.db as app_db
from app.concept_graph import ConceptCreate, ConceptRelationCreate
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


def _tamper_operation(
    conn: sqlite3.Connection,
    statement: str,
    parameters: tuple[object, ...],
) -> None:
    trigger_sql = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'trigger' "
        "AND name = 'concept_graph_operation_immutable_update'"
    ).fetchone()[0]
    conn.execute("DROP TRIGGER concept_graph_operation_immutable_update")
    try:
        conn.execute(statement, parameters)
    finally:
        conn.execute(str(trigger_sql))


def _course_chunk(suffix: str) -> tuple[str, CourseSource, CourseSourceChunk]:
    course = create_video_course(CourseCreate(title=f"Create {suffix}"))
    asset_id = f"create-{suffix}"
    source = CourseSource(
        id=f"asset:{asset_id}",
        course_id=course.id,
        origin_type="source_asset",
        origin_id=asset_id,
        source_type="pdf",
        title=f"{suffix}.pdf",
        content_status="ready",
    )
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
    text = "Alpha precedes Beta. Gamma is related to Alpha."
    chunk = CourseSourceChunk(
        id=f"source_unit:{asset_id}-page-1",
        source_id=source.id,
        origin_type="source_unit",
        origin_id=f"{asset_id}-page-1",
        chunk_type="page",
        ordinal=0,
        text=text,
        text_hash=hash_source_chunk_text(text),
        locator=PdfPageLocator(asset_id=asset_id, page_number=1),
        chunker_version="create-idempotency-v1",
    )
    replace_source_projection(source, [chunk])
    return course.id, source, chunk


def _concept_payload(
    chunk: CourseSourceChunk,
    name: str,
    *,
    operation_id: str | None = None,
) -> dict[str, object]:
    return {
        "operation_id": operation_id or uuid4().hex,
        "actor": "creator@example.test",
        "reason": "Create a grounded Concept candidate.",
        "preferred_name": name,
        "short_definition": f"Definition for {name}.",
        "aliases": [],
        "evidence": [{"chunk_id": chunk.id, "quote": name}],
    }


def _relation_payload(
    chunk: CourseSourceChunk,
    source_id: str,
    target_id: str,
    *,
    operation_id: str | None = None,
) -> dict[str, object]:
    return {
        "operation_id": operation_id or uuid4().hex,
        "actor": "creator@example.test",
        "reason": "Create a grounded relation candidate.",
        "source_concept_id": source_id,
        "target_concept_id": target_id,
        "relation_type": "prerequisite",
        "support_basis": "source_asserted",
        "rationale": "The source explicitly states the ordering.",
        "evidence": [{
            "chunk_id": chunk.id,
            "quote": "Alpha precedes Beta",
            "support_role": "relation_assertion",
        }],
    }


def _create_concept(
    course_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    response = client.post(f"/courses/{course_id}/concepts", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_metadata_is_strict_normalized_and_part_of_replay_hash() -> None:
    course_id, _, chunk = _course_chunk("metadata")
    route = f"/courses/{course_id}/concepts"
    missing = client.post(
        route,
        json={
            "preferred_name": "Alpha",
            "short_definition": "Definition.",
            "evidence": [{"chunk_id": chunk.id, "quote": "Alpha"}],
        },
    )
    unexpected = client.post(
        route,
        json={
            **_concept_payload(chunk, "Alpha"),
            "expected_revision": 1,
        },
    )
    nested_unknown = client.post(
        route,
        json={
            **_concept_payload(chunk, "Alpha"),
            "evidence": [{
                "chunk_id": chunk.id,
                "quote": "Alpha",
                "client_locator": {"page": 1},
            }],
        },
    )
    assert missing.status_code == 422
    assert unexpected.status_code == 422
    assert nested_unknown.status_code == 422

    payload = _concept_payload(chunk, "Alpha", operation_id="  create-op  ")
    payload["actor"] = "  creator@example.test  "
    payload["reason"] = "  Create   a grounded Concept candidate.  "
    created = client.post(route, json=payload)
    normalized = client.post(
        route,
        json={
            **payload,
            "operation_id": "create-op",
            "actor": "creator@example.test",
            "reason": "Create a grounded Concept candidate.",
        },
    )
    assert created.status_code == normalized.status_code == 201
    assert created.json() == normalized.json()
    with connect() as conn:
        row = conn.execute(
            "SELECT operation_id, actor, reason FROM concept_graph_operations "
            "WHERE course_id = ? AND kind = 'concept_create'",
            (course_id,),
        ).fetchone()
        assert tuple(row) == (
            "create-op",
            "creator@example.test",
            "Create a grounded Concept candidate.",
        )


def test_create_hash_protocol_has_fixed_concept_and_relation_vectors() -> None:
    concept = ConceptCreate(
        operation_id="op-001",
        actor="alice",
        reason="ground concept",
        preferred_name="Gradient Descent",
        short_definition="An optimization method.",
        aliases=["GD"],
        evidence=[{"chunk_id": "chunk-1", "quote": "gradient descent"}],
    )
    relation = ConceptRelationCreate(
        operation_id="op-002",
        actor="alice",
        reason="ground relation",
        source_concept_id="concept-a",
        target_concept_id="concept-b",
        relation_type="prerequisite",
        support_basis="source_asserted",
        rationale="A before B.",
        evidence=[{
            "chunk_id": "chunk-1",
            "quote": "A before B",
            "support_role": "relation_assertion",
        }],
    )
    assert graph_service._create_hash(
        course_id="course-1",
        entity_type="concept",
        kind="concept_create",
        path="/courses/{course_id}/concepts",
        request=concept,
    ) == "fc3da1564c6c0da55882a192807ff366ca339b4a302fd828add811d523d160b6"
    assert graph_service._create_hash(
        course_id="course-1",
        entity_type="relation",
        kind="relation_create",
        path="/courses/{course_id}/concept-relations",
        request=relation,
    ) == "4abc98399cf3ce5b80b7dcf1d705c38ece0c3eb5c2e835d7dd00d0435049684f"


def test_concept_create_replay_precedes_source_validation_and_reuse_conflicts() -> None:
    course_id, source, chunk = _course_chunk("concept-replay")
    operation_id = uuid4().hex
    payload = _concept_payload(chunk, "Alpha", operation_id=operation_id)
    created = _create_concept(course_id, payload)
    assert created["evidence_current"] is True

    drifted_text = "The original evidence quote no longer exists."
    drifted = chunk.model_copy(
        update={
            "text": drifted_text,
            "text_hash": hash_source_chunk_text(drifted_text),
            "locator": PdfPageLocator(
                asset_id=source.origin_id,
                page_number=2,
            ),
        }
    )
    replace_source_projection(source, [drifted])
    replay = client.post(f"/courses/{course_id}/concepts", json=payload)
    changed = client.post(
        f"/courses/{course_id}/concepts",
        json={
            **payload,
            "preferred_name": "Changed",
            "evidence": [{"chunk_id": "missing", "quote": "missing"}],
        },
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == created["id"]
    assert replay.json()["evidence_current"] is False
    assert changed.status_code == 409
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM concepts WHERE course_id = ?",
            (course_id,),
        ).fetchone()[0] == 1

        assert conn.execute(
            "SELECT COUNT(*) FROM concept_graph_operations "
            "WHERE course_id = ? AND kind = 'concept_create'",
            (course_id,),
        ).fetchone()[0] == 1


def test_same_concept_create_operation_converges_under_concurrency() -> None:
    course_id, _, chunk = _course_chunk("concept-concurrent")
    request = ConceptCreate(
        **_concept_payload(chunk, "Alpha", operation_id=uuid4().hex)
    )
    barrier = Barrier(2)

    def create() -> str:
        barrier.wait()
        return graph_service.create_grounded_concept_candidate(
            course_id, request
        ).id

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(lambda _: create(), range(2)))
    assert ids[0] == ids[1]
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM concepts WHERE course_id = ?",
            (course_id,),
        ).fetchone()[0] == 1


def test_same_operation_different_valid_payload_has_one_concurrent_winner() -> None:
    course_id, _, chunk = _course_chunk("concept-concurrent-conflict")
    operation_id = uuid4().hex
    requests = (
        ConceptCreate(
            **_concept_payload(chunk, "Alpha", operation_id=operation_id)
        ),
        ConceptCreate(
            **_concept_payload(chunk, "Gamma", operation_id=operation_id)
        ),
    )
    barrier = Barrier(2)

    def create(request: ConceptCreate) -> str:
        barrier.wait()
        try:
            graph_service.create_grounded_concept_candidate(
                course_id, request
            )
            return "created"
        except graph_service.ConceptGraphConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(create, requests))
    assert sorted(outcomes) == ["conflict", "created"]
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM concepts WHERE course_id = ?",
            (course_id,),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_graph_operations "
            "WHERE course_id = ? AND operation_id = ?",
            (course_id, operation_id),
        ).fetchone()[0] == 1


def test_operation_id_namespace_is_course_scoped() -> None:
    first_course, _, first_chunk = _course_chunk("course-scope-one")
    second_course, _, second_chunk = _course_chunk("course-scope-two")
    operation_id = uuid4().hex
    first = _create_concept(
        first_course,
        _concept_payload(first_chunk, "Alpha", operation_id=operation_id),
    )
    second = _create_concept(
        second_course,
        _concept_payload(second_chunk, "Alpha", operation_id=operation_id),
    )
    assert first["id"] != second["id"]
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_graph_operations "
            "WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()[0] == 2


def test_distinct_operations_may_create_duplicate_concept_candidates() -> None:
    course_id, _, chunk = _course_chunk("concept-duplicates")
    first = _create_concept(course_id, _concept_payload(chunk, "Alpha"))
    second = _create_concept(course_id, _concept_payload(chunk, "Alpha"))
    assert first["id"] != second["id"]
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM concepts WHERE course_id = ?",
            (course_id,),
        ).fetchone()[0] == 2


def test_relation_create_replays_after_endpoint_change_and_converges() -> None:
    course_id, _, chunk = _course_chunk("relation-replay")
    left = _create_concept(course_id, _concept_payload(chunk, "Alpha"))
    right = _create_concept(course_id, _concept_payload(chunk, "Beta"))
    operation_id = uuid4().hex
    payload = _relation_payload(
        chunk, str(left["id"]), str(right["id"]), operation_id=operation_id
    )
    request = ConceptRelationCreate(**payload)
    barrier = Barrier(2)

    def create() -> str:
        barrier.wait()
        return graph_service.create_grounded_relation_candidate(
            course_id, request
        ).id

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(lambda _: create(), range(2)))
    assert ids[0] == ids[1]

    retired = client.post(
        f"/courses/{course_id}/concepts/{left['id']}/retire",
        json={
            "operation_id": uuid4().hex,
            "expected_revision": 1,
            "actor": "reviewer@example.test",
            "reason": "Change an endpoint after relation creation.",
        },
    )
    assert retired.status_code == 200, retired.text
    replay = client.post(
        f"/courses/{course_id}/concept-relations", json=payload
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == ids[0]
    assert replay.json()["revision"] == 1
    assert replay.json()["is_current_revision"] is False
    assert replay.json()["eligible_for_publication"] is False


def test_distinct_operation_duplicate_relation_conflicts_without_receipt() -> None:
    course_id, _, chunk = _course_chunk("relation-duplicate")
    left = _create_concept(course_id, _concept_payload(chunk, "Alpha"))
    right = _create_concept(course_id, _concept_payload(chunk, "Beta"))
    first_payload = _relation_payload(
        chunk, str(left["id"]), str(right["id"])
    )
    first = client.post(
        f"/courses/{course_id}/concept-relations", json=first_payload
    )
    second_operation = uuid4().hex
    second = client.post(
        f"/courses/{course_id}/concept-relations",
        json={**first_payload, "operation_id": second_operation},
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 409, second.text
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_relations WHERE course_id = ?",
            (course_id,),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_graph_operations "
            "WHERE course_id = ? AND operation_id = ?",
            (course_id, second_operation),
        ).fetchone()[0] == 0


def test_distinct_operation_duplicate_relation_concurrency_has_one_receipt() -> None:
    course_id, _, chunk = _course_chunk("relation-duplicate-concurrent")
    left = _create_concept(course_id, _concept_payload(chunk, "Alpha"))
    right = _create_concept(course_id, _concept_payload(chunk, "Beta"))
    operation_ids = (uuid4().hex, uuid4().hex)
    requests = tuple(
        ConceptRelationCreate(
            **_relation_payload(
                chunk,
                str(left["id"]),
                str(right["id"]),
                operation_id=operation_id,
            )
        )
        for operation_id in operation_ids
    )
    barrier = Barrier(2)

    def create(request: ConceptRelationCreate) -> str:
        barrier.wait()
        try:
            graph_service.create_grounded_relation_candidate(
                course_id, request
            )
            return "created"
        except graph_service.ConceptGraphConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(create, requests))
    assert sorted(outcomes) == ["conflict", "created"]
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_relations WHERE course_id = ?",
            (course_id,),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_graph_operations "
            "WHERE course_id = ? AND kind = 'relation_create'",
            (course_id,),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_graph_operations "
            "WHERE course_id = ? AND operation_id IN (?, ?)",
            (course_id, *operation_ids),
        ).fetchone()[0] == 1


def test_same_symmetric_relation_operation_rejects_reversed_client_request() -> None:
    course_id, _, chunk = _course_chunk("symmetric-request-hash")
    left = _create_concept(course_id, _concept_payload(chunk, "Alpha"))
    right = _create_concept(course_id, _concept_payload(chunk, "Beta"))
    operation_id = uuid4().hex
    payload = {
        **_relation_payload(
            chunk,
            str(left["id"]),
            str(right["id"]),
            operation_id=operation_id,
        ),
        "relation_type": "related",
        "support_basis": "pedagogical_inference",
        "evidence": [
            {
                "chunk_id": chunk.id,
                "quote": "Alpha",
                "support_role": "source_endpoint",
            },
            {
                "chunk_id": chunk.id,
                "quote": "Beta",
                "support_role": "target_endpoint",
            },
        ],
    }
    first = client.post(
        f"/courses/{course_id}/concept-relations", json=payload
    )
    reversed_request = client.post(
        f"/courses/{course_id}/concept-relations",
        json={
            **payload,
            "source_concept_id": payload["target_concept_id"],
            "target_concept_id": payload["source_concept_id"],
            "evidence": [
                {
                    "chunk_id": chunk.id,
                    "quote": "Beta",
                    "support_role": "source_endpoint",
                },
                {
                    "chunk_id": chunk.id,
                    "quote": "Alpha",
                    "support_role": "target_endpoint",
                },
            ],
        },
    )
    assert first.status_code == 201, first.text
    assert reversed_request.status_code == 409, reversed_request.text


def test_create_operation_reuse_is_checked_before_endpoint_validation() -> None:
    course_id, _, chunk = _course_chunk("cross-kind-reuse")
    operation_id = uuid4().hex
    _create_concept(
        course_id,
        _concept_payload(chunk, "Alpha", operation_id=operation_id),
    )
    response = client.post(
        f"/courses/{course_id}/concept-relations",
        json=_relation_payload(
            chunk,
            "missing-source",
            "missing-target",
            operation_id=operation_id,
        ),
    )
    assert response.status_code == 409, response.text


def test_create_and_existing_mutation_share_operation_namespace() -> None:
    course_id, _, chunk = _course_chunk("create-mutation-namespace")
    operation_id = uuid4().hex
    concept = _create_concept(
        course_id,
        _concept_payload(chunk, "Alpha", operation_id=operation_id),
    )
    response = client.post(
        f"/courses/{course_id}/concepts/{concept['id']}/review",
        json={
            "operation_id": operation_id,
            "expected_revision": 1,
            "actor": "reviewer@example.test",
            "reason": "Attempt to reuse a create operation ID.",
            "decision": "accept",
        },
    )
    assert response.status_code == 409, response.text
    assert client.get(
        f"/courses/{course_id}/concepts/{concept['id']}"
    ).json()["revision"] == 1


def test_failed_create_rolls_back_aggregate_children_and_receipt() -> None:
    course_id, _, chunk = _course_chunk("atomic-failure")
    operation_id = uuid4().hex
    response = client.post(
        f"/courses/{course_id}/concepts",
        json={
            **_concept_payload(chunk, "Alpha", operation_id=operation_id),
            "evidence": [{"chunk_id": chunk.id, "quote": "absent quote"}],
        },
    )
    assert response.status_code == 422
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM concepts WHERE course_id = ?",
            (course_id,),
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_graph_operations "
            "WHERE course_id = ? AND operation_id = ?",
            (course_id, operation_id),
        ).fetchone()[0] == 0


def test_receipt_failure_rolls_back_full_create_and_same_operation_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course_id, _, chunk = _course_chunk("receipt-rollback")
    operation_id = uuid4().hex
    payload = _concept_payload(chunk, "Alpha", operation_id=operation_id)
    payload["aliases"] = ["First"]
    original = graph_store._record_operation

    def fail_receipt(*args: object, **kwargs: object) -> None:
        raise sqlite3.IntegrityError("injected create receipt failure")

    monkeypatch.setattr(graph_store, "_record_operation", fail_receipt)
    failed = client.post(f"/courses/{course_id}/concepts", json=payload)
    assert failed.status_code == 500
    with connect() as conn:
        for table in (
            "concepts",
            "concept_revisions",
            "concept_aliases",
            "concept_evidence",
        ):
            assert conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE course_id = ?",
                (course_id,),
            ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_graph_operations "
            "WHERE course_id = ? AND operation_id = ?",
            (course_id, operation_id),
        ).fetchone()[0] == 0

    monkeypatch.setattr(graph_store, "_record_operation", original)
    retried = client.post(f"/courses/{course_id}/concepts", json=payload)
    assert retried.status_code == 201, retried.text


def test_relation_receipt_failure_rolls_back_binding_evidence_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course_id, _, chunk = _course_chunk("relation-receipt-rollback")
    left = _create_concept(course_id, _concept_payload(chunk, "Alpha"))
    right = _create_concept(course_id, _concept_payload(chunk, "Beta"))
    operation_id = uuid4().hex
    payload = _relation_payload(
        chunk,
        str(left["id"]),
        str(right["id"]),
        operation_id=operation_id,
    )
    original = graph_store._record_operation

    def fail_relation_receipt(*args: object, **kwargs: object) -> None:
        if kwargs.get("kind") == "relation_create":
            raise sqlite3.IntegrityError("injected relation receipt failure")
        original(*args, **kwargs)

    monkeypatch.setattr(
        graph_store, "_record_operation", fail_relation_receipt
    )
    failed = client.post(
        f"/courses/{course_id}/concept-relations", json=payload
    )
    assert failed.status_code == 500
    with connect() as conn:
        for table in (
            "concept_relations",
            "concept_relation_revisions",
            "relation_endpoint_revisions",
            "relation_evidence",
        ):
            assert conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE course_id = ?",
                (course_id,),
            ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_graph_operations "
            "WHERE course_id = ? AND operation_id = ?",
            (course_id, operation_id),
        ).fetchone()[0] == 0

    monkeypatch.setattr(graph_store, "_record_operation", original)
    retried = client.post(
        f"/courses/{course_id}/concept-relations", json=payload
    )
    assert retried.status_code == 201, retried.text


def test_create_busy_maps_to_503_then_same_operation_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course_id, _, chunk = _course_chunk("busy")
    payload = _concept_payload(chunk, "Alpha", operation_id=uuid4().hex)
    holder = app_db.get_conn()
    holder.execute("BEGIN IMMEDIATE")
    database_path = app_db.get_db_path()

    def fast_timeout_connection() -> sqlite3.Connection:
        conn = sqlite3.connect(database_path, timeout=0.02)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 20")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    monkeypatch.setattr(app_db, "get_conn", fast_timeout_connection)
    try:
        busy = client.post(f"/courses/{course_id}/concepts", json=payload)
    finally:
        holder.rollback()
        holder.close()
    assert busy.status_code == 503, busy.text
    assert busy.headers["retry-after"] == "1"

    retried = client.post(f"/courses/{course_id}/concepts", json=payload)
    assert retried.status_code == 201, retried.text


@pytest.mark.parametrize(
    "change",
    ["actor", "reason", "aliases", "evidence_order"],
)
def test_create_hash_rejects_normalized_payload_changes(change: str) -> None:
    course_id, _, chunk = _course_chunk(f"hash-change-{change}")
    operation_id = uuid4().hex
    payload = _concept_payload(chunk, "Alpha", operation_id=operation_id)
    payload["aliases"] = ["First", "Primary"]
    payload["evidence"] = [
        {"chunk_id": chunk.id, "quote": "Alpha"},
        {"chunk_id": chunk.id, "quote": "Gamma"},
    ]
    _create_concept(course_id, payload)
    changed = dict(payload)
    if change == "actor":
        changed["actor"] = "other@example.test"
    elif change == "reason":
        changed["reason"] = "A different creation reason."
    elif change == "aliases":
        changed["aliases"] = list(reversed(payload["aliases"]))
    else:
        changed["evidence"] = list(reversed(payload["evidence"]))
    response = client.post(
        f"/courses/{course_id}/concepts", json=changed
    )
    assert response.status_code == 409, response.text


def test_corrupt_or_dangling_create_receipt_fails_safe() -> None:
    course_id, _, chunk = _course_chunk("receipt-corruption")
    route = f"/courses/{course_id}/concepts"
    corrupt_payload = _concept_payload(chunk, "Alpha")
    _create_concept(course_id, corrupt_payload)
    with connect() as conn:
        _tamper_operation(
            conn,
            "UPDATE concept_graph_operations SET result_json = '{}' "
            "WHERE course_id = ? AND operation_id = ?",
            (course_id, corrupt_payload["operation_id"]),
        )
    corrupt = client.post(route, json=corrupt_payload)
    assert corrupt.status_code == 500
    assert corrupt.json() == {
        "detail": "Unexpected Concept graph service error."
    }

    dangling_payload = _concept_payload(chunk, "Beta")
    _create_concept(course_id, dangling_payload)
    with connect() as conn:
        receipt = conn.execute(
            "SELECT result_json FROM concept_graph_operations "
            "WHERE course_id = ? AND operation_id = ?",
            (course_id, dangling_payload["operation_id"]),
        ).fetchone()[0]
        body = json.loads(receipt)
        body["revision"] = 99
        _tamper_operation(
            conn,
            "UPDATE concept_graph_operations "
            "SET result_revision = 99, result_json = ? "
            "WHERE course_id = ? AND operation_id = ?",
            (
                json.dumps(body, sort_keys=True, separators=(",", ":")),
                course_id,
                dangling_payload["operation_id"],
            ),
        )
    dangling = client.post(route, json=dangling_payload)
    assert dangling.status_code == 500
    assert dangling.json() == {
        "detail": "Unexpected Concept graph service error."
    }
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' "
            "AND name = 'concept_graph_operation_immutable_update'"
        ).fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE concept_graph_operations SET reason = 'tampered'"
            )
