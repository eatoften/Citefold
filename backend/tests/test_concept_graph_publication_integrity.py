from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest

import app.main as main
import app.concept_graph_publication_store as publication_store
from app.concept_graph import ConceptMergeRequest, ConceptRetireRequest
from app.concept_graph_service import (
    merge_course_concept,
    retire_course_concept,
)
from app.course_service import delete_video_course, restore_video_course
from app.course_source import hash_source_chunk_text
from app.concept_graph_publication_service import (
    ConceptGraphPublicationTooLargeError,
    preview_course_publication,
)
from app.db import connect, get_db_path
from app.workspace_backup import DATABASE_ARCHIVE_PATH, create_workspace_backup
from tests.concept_graph_publication_support import (
    accepted_concept,
    accepted_relation,
    make_course_source,
)


client = TestClient(main.app, raise_server_exceptions=False)


def _preview(course_id: str) -> dict[str, object]:
    response = client.get(
        f"/courses/{course_id}/concept-graph/publication-preview"
    )
    assert response.status_code == 200, response.text
    return response.json()


def _publish(course_id: str, preview: dict[str, object]):
    response = client.post(
        f"/courses/{course_id}/concept-graph/versions",
        json={
            "operation_id": uuid4().hex,
            "expected_active_version": preview["active_version"],
            "expected_draft_manifest_hash": preview[
                "draft_manifest_hash"
            ],
            "actor": "publisher@example.test",
            "reason": "Seal a graph for integrity testing.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_missing_review_receipt_blocks_selected_concept() -> None:
    course, _, chunk = make_course_source("receipt-missing")
    concept = accepted_concept(course.id, chunk, "Alpha", "Alpha")
    with connect() as conn:
        conn.execute(
            """
            DELETE FROM concept_graph_operations
            WHERE course_id = ? AND entity_type = 'concept'
              AND entity_id = ? AND kind = 'concept_review'
            """,
            (course.id, concept.id),
        )

    preview = _preview(course.id)
    assert preview["publishable"] is False
    assert preview["issue_count"] == 1
    assert preview["issues"][0]["code"] == "review_receipt_missing"


def test_invalid_accepted_relation_blocks_instead_of_being_pruned() -> None:
    course, _, chunk = make_course_source("invalid-relation")
    alpha = accepted_concept(course.id, chunk, "Alpha", "Alpha")
    beta = accepted_concept(course.id, chunk, "Beta", "Beta")
    relation = accepted_relation(course.id, chunk, alpha.id, beta.id)
    with connect() as conn:
        conn.execute(
            """
            DELETE FROM relation_endpoint_revisions
            WHERE course_id = ? AND relation_id = ?
              AND relation_revision = ?
            """,
            (course.id, relation.id, relation.revision),
        )

    preview = _preview(course.id)
    assert preview["counts"]["relations"] == 1
    assert preview["publishable"] is False
    assert "relation_endpoint_binding_missing" in {
        item["code"] for item in preview["issues"]
    }
    blocked = client.post(
        f"/courses/{course.id}/concept-graph/versions",
        json={
            "operation_id": uuid4().hex,
            "expected_active_version": None,
            "expected_draft_manifest_hash": preview[
                "draft_manifest_hash"
            ],
            "actor": "publisher@example.test",
            "reason": "An invalid accepted relation must block.",
        },
    )
    assert blocked.status_code == 409
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_graph_versions"
        ).fetchone()[0] == 0


def test_snapshot_child_deletion_is_detected_and_fails_closed() -> None:
    course, _, chunk = make_course_source("tamper")
    concept = accepted_concept(course.id, chunk, "Alpha", "Alpha")
    _publish(course.id, _preview(course.id))
    with connect() as conn:
        with pytest.raises(
            sqlite3.IntegrityError, match="permanent course purge"
        ):
            conn.execute(
                """
                DELETE FROM concept_graph_version_concept_evidence
                WHERE course_id = ? AND version_number = 1
                  AND concept_id = ?
                """,
                (course.id, concept.id),
            )
        conn.execute(
            """
            DROP TRIGGER
                concept_graph_version_concept_evidence_guard_delete
            """
        )
        conn.execute(
            """
            DELETE FROM concept_graph_version_concept_evidence
            WHERE course_id = ? AND version_number = 1
              AND concept_id = ?
            """,
            (course.id, concept.id),
        )

    metadata = client.get(
        f"/courses/{course.id}/concept-graph/versions/1"
    )
    concepts = client.get(
        f"/courses/{course.id}/concept-graph/versions/1/concepts"
    )
    assert metadata.status_code == 500
    assert metadata.json()["detail"] == (
        "Unexpected Concept graph publication service error."
    )
    assert concepts.status_code == 500


def test_course_isolation_applies_to_version_and_children() -> None:
    course, _, chunk = make_course_source("scope-a")
    other, _, _ = make_course_source("scope-b")
    accepted_concept(course.id, chunk, "Alpha", "Alpha")
    _publish(course.id, _preview(course.id))

    assert client.get(
        f"/courses/{other.id}/concept-graph/versions/1"
    ).status_code == 404
    assert client.get(
        f"/courses/{other.id}/concept-graph/versions/1/concepts"
    ).status_code == 404


def test_utf8_byte_budget_is_enforced_before_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "知" * 400
    course, _, chunk = make_course_source("utf8-budget", text=text)
    accepted_concept(course.id, chunk, "知识", text)
    monkeypatch.setattr(
        publication_store, "MAX_DRAFT_SERIALIZED_BYTES", 7_000
    )

    with pytest.raises(ConceptGraphPublicationTooLargeError):
        preview_course_publication(course.id)


def test_oversized_live_chunk_blocks_before_publish() -> None:
    text = "Alpha " + ("x" * 70_000)
    course, _, chunk = make_course_source("authority-bound", text=text)
    accepted_concept(course.id, chunk, "Alpha", "Alpha")

    preview = _preview(course.id)
    assert preview["publishable"] is False
    assert preview["issues"][0]["code"] == (
        "concept_evidence_not_current"
    )


def test_merged_and_retired_terminal_heads_are_excluded_not_blockers() -> None:
    course, _, chunk = make_course_source("terminal-heads")
    survivor = accepted_concept(course.id, chunk, "Alpha", "Alpha")
    merged = accepted_concept(course.id, chunk, "Beta", "Beta")
    retired = accepted_concept(course.id, chunk, "Gamma", "Gamma")

    merge_course_concept(
        course.id,
        merged.id,
        ConceptMergeRequest(
            operation_id=uuid4().hex,
            expected_revision=merged.revision,
            survivor_concept_id=survivor.id,
            expected_survivor_revision=survivor.revision,
            actor="reviewer@example.test",
            reason="Merge the duplicate identity into Alpha.",
        ),
    )
    retire_course_concept(
        course.id,
        retired.id,
        ConceptRetireRequest(
            operation_id=uuid4().hex,
            expected_revision=retired.revision,
            actor="reviewer@example.test",
            reason="Retire the obsolete Concept identity.",
        ),
    )

    preview = _preview(course.id)
    assert preview["publishable"] is True
    assert preview["issue_count"] == 0
    assert preview["counts"]["concepts"] == 1


def test_course_trash_hides_and_restore_reveals_published_history() -> None:
    course, _, chunk = make_course_source("course-trash")
    accepted_concept(course.id, chunk, "Alpha", "Alpha")
    _publish(course.id, _preview(course.id))
    route = f"/courses/{course.id}/concept-graph/versions/1"

    delete_video_course(course.id)
    assert client.get(route).status_code == 404
    assert client.get(f"{route}/concepts").status_code == 404

    restore_video_course(course.id)
    restored = client.get(route)
    assert restored.status_code == 200
    assert restored.json()["version_number"] == 1
    assert client.get(f"{route}/concepts").status_code == 200


def test_workspace_backup_contains_complete_sealed_graph(
    tmp_path: Path,
) -> None:
    course, source, chunk = make_course_source("workspace-backup")
    accepted_concept(course.id, chunk, "Alpha", "Alpha")
    published = _publish(course.id, _preview(course.id))

    workspace_dir = tmp_path / "workspace"
    asset_path = (
        workspace_dir
        / "sources"
        / course.id
        / f"{source.origin_id}.pdf"
    )
    asset_path.parent.mkdir(parents=True)
    payload = b"%PDF-1.4\n% graph backup fixture\n"
    asset_path.write_bytes(payload)
    with connect() as conn:
        conn.execute(
            """
            UPDATE source_assets
            SET stored_path = ?, size_bytes = ?, sha256 = ?
            WHERE id = ? AND course_id = ?
            """,
            (
                str(asset_path),
                len(payload),
                hashlib.sha256(payload).hexdigest(),
                source.origin_id,
                course.id,
            ),
        )

    backup = create_workspace_backup(
        db_path=get_db_path(),
        data_dir=workspace_dir,
        backup_dir=tmp_path / "backups",
    )
    extracted_db = tmp_path / "sealed-graph.sqlite3"
    with ZipFile(backup.path) as archive:
        extracted_db.write_bytes(archive.read(DATABASE_ARCHIVE_PATH))
    with sqlite3.connect(extracted_db) as conn:
        version = conn.execute(
            """
            SELECT content_hash FROM concept_graph_versions
            WHERE course_id = ? AND version_number = 1
            """,
            (course.id,),
        ).fetchone()
        assert version is not None
        assert version[0] == published["content_hash"]
        assert conn.execute(
            """
            SELECT COUNT(*) FROM concept_graph_version_heads
            WHERE course_id = ? AND active_version_number = 1
            """,
            (course.id,),
        ).fetchone()[0] == 1
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM concept_graph_version_concept_evidence
            WHERE course_id = ? AND version_number = 1
            """,
            (course.id,),
        ).fetchone()[0] == 1
        assert conn.execute(
            """
            SELECT COUNT(*) FROM concept_graph_publication_operations
            WHERE course_id = ? AND result_version_number = 1
            """,
            (course.id,),
        ).fetchone()[0] == 1


@pytest.mark.parametrize(
    ("table", "update_sql", "child_kind"),
    [
        (
            "concept_graph_version_concepts",
            "UPDATE concept_graph_version_concepts "
            "SET preferred_name = 'Tampered Alpha' "
            "WHERE course_id = ? AND version_number = 1",
            "concepts",
        ),
        (
            "concept_graph_version_concept_aliases",
            "UPDATE concept_graph_version_concept_aliases "
            "SET display_text = 'Tampered alias' "
            "WHERE course_id = ? AND version_number = 1",
            "concepts",
        ),
        (
            "concept_graph_version_concept_evidence",
            "UPDATE concept_graph_version_concept_evidence "
            "SET quote = 'Tampered concept evidence' "
            "WHERE course_id = ? AND version_number = 1",
            "concepts",
        ),
        (
            "concept_graph_version_relations",
            "UPDATE concept_graph_version_relations "
            "SET rationale = 'Tampered relation rationale' "
            "WHERE course_id = ? AND version_number = 1",
            "relations",
        ),
        (
            "concept_graph_version_relation_evidence",
            "UPDATE concept_graph_version_relation_evidence "
            "SET quote = 'Tampered relation evidence' "
            "WHERE course_id = ? AND version_number = 1",
            "relations",
        ),
    ],
)
def test_child_pages_detect_same_count_aggregate_tampering(
    table: str,
    update_sql: str,
    child_kind: str,
) -> None:
    course, _, chunk = make_course_source(f"aggregate-{table}")
    alpha = accepted_concept(course.id, chunk, "Alpha", "Alpha")
    beta = accepted_concept(course.id, chunk, "Beta", "Beta")
    accepted_relation(course.id, chunk, alpha.id, beta.id)
    _publish(course.id, _preview(course.id))
    with connect() as conn:
        conn.execute(f"DROP TRIGGER {table}_immutable_update")
        conn.execute(update_sql, (course.id,))

    response = client.get(
        f"/courses/{course.id}/concept-graph/versions/1/{child_kind}"
    )
    assert response.status_code == 500


def test_aggregate_hash_only_corruption_blocks_full_reads_and_replay() -> None:
    course, _, chunk = make_course_source("aggregate-hash-only")
    accepted_concept(course.id, chunk, "Alpha", "Alpha")
    preview = _preview(course.id)
    payload = {
        "operation_id": uuid4().hex,
        "expected_active_version": preview["active_version"],
        "expected_draft_manifest_hash": preview["draft_manifest_hash"],
        "actor": "publisher@example.test",
        "reason": "Publish for aggregate hash corruption testing.",
    }
    route = f"/courses/{course.id}/concept-graph/versions"
    assert client.post(route, json=payload).status_code == 201
    with connect() as conn:
        conn.execute(
            "DROP TRIGGER concept_graph_version_concepts_immutable_update"
        )
        conn.execute(
            """
            UPDATE concept_graph_version_concepts
            SET aggregate_hash = ?
            WHERE course_id = ? AND version_number = 1
            """,
            ("0" * 64, course.id),
        )

    assert client.get(f"{route}/1").status_code == 500
    assert client.get(f"{route}/current").status_code == 500
    assert client.post(route, json=payload).status_code == 500


def test_currentness_predicate_reuses_exact_evidence_cache() -> None:
    text = "Alpha is grounded."
    text_hash = hash_source_chunk_text(text)
    locator = (
        '{"asset_id":"source","kind":"pdf_page","metadata":{},'
        '"page_number":1,"schema_version":1}'
    )
    arguments = (
        "generation-1",
        "pdf",
        text_hash,
        locator,
        "Alpha",
        "course",
        "asset:source",
        "ready",
        "generation-1",
        "pdf",
        "source_unit:page-1",
        text,
        text_hash,
        locator,
        1,
    )
    publication_store._evidence_values_are_current.cache_clear()
    assert publication_store._evidence_values_are_current(*arguments) is True
    assert publication_store._evidence_values_are_current(*arguments) is True
    cache = publication_store._evidence_values_are_current.cache_info()
    assert cache.hits == 1
    assert cache.misses == 1
