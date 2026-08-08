from __future__ import annotations

import json
import sqlite3
from uuid import uuid4

import pytest

from app.db import connect
from app.migrations import (
    MIGRATIONS,
    Migration,
    _add_concept_graph_review_lifecycle,
    apply_migrations,
)


V11_OBJECTS = {
    "concept_aliases",
    "relation_endpoint_revisions",
    "concept_graph_operations",
}


def _drop_v11(conn: sqlite3.Connection) -> None:
    for trigger in (
        "concept_revisions_immutable_update",
        "concept_evidence_immutable_update",
        "concept_aliases_immutable_update",
        "concept_relation_revisions_immutable_update",
        "relation_evidence_immutable_update",
        "concept_graph_operation_immutable_update",
        "concept_graph_operation_result_insert",
        "concept_relation_identity_immutable_update",
        "relation_endpoint_identity_update",
        "relation_endpoint_identity_insert",
    ):
        conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for table in (
        "concept_graph_operations",
        "relation_endpoint_revisions",
        "concept_aliases",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    for index in (
        "idx_concept_revisions_merge_target",
        "idx_concept_relations_source_incident",
        "idx_concept_relations_target_incident",
    ):
        conn.execute(f"DROP INDEX IF EXISTS {index}")
    conn.execute("DELETE FROM schema_migrations WHERE version IN (11, 12)")


def test_v11_clean_schema_has_review_tables_indexes_and_triggers() -> None:
    with connect() as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        indexes = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        triggers = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        assert V11_OBJECTS <= tables
        assert {
            "idx_concept_aliases_lookup",
            "idx_relation_endpoint_source_revision",
            "idx_relation_endpoint_target_revision",
            "idx_concept_graph_operations_entity",
            "idx_concept_relations_source_incident",
            "idx_concept_relations_target_incident",
        } <= indexes
        assert {
            "relation_endpoint_identity_insert",
            "relation_endpoint_identity_update",
            "concept_graph_operation_result_insert",
            "concept_graph_operation_immutable_update",
            "concept_relation_identity_immutable_update",
        } <= triggers
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_v10_upgrade_to_v11_is_additive_and_idempotent() -> None:
    with connect() as conn:
        _drop_v11(conn)
        before = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        completed = apply_migrations(conn, migrations=MIGRATIONS[:11])
        after = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert completed == [11]
        assert before <= after
        assert V11_OBJECTS <= after
        assert apply_migrations(conn, migrations=MIGRATIONS[:11]) == []


def test_v11_failure_rolls_back_all_schema_objects() -> None:
    with connect() as conn:
        _drop_v11(conn)

        def fail_after_v11(target: sqlite3.Connection) -> None:
            _add_concept_graph_review_lifecycle(target)
            raise RuntimeError("injected migration failure")

        failing = Migration(
            version=11,
            name="concept_graph_review_lifecycle",
            apply=fail_after_v11,
        )
        with pytest.raises(RuntimeError, match="injected migration failure"):
            apply_migrations(conn, migrations=(*MIGRATIONS[:10], failing))
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert V11_OBJECTS.isdisjoint(tables)
        assert conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 11"
        ).fetchone()[0] == 0
        assert apply_migrations(conn, migrations=MIGRATIONS[:11]) == [11]


def test_operation_ledger_rejects_invalid_result_and_is_immutable() -> None:
    with connect() as conn:
        course_id = conn.execute("SELECT id FROM courses LIMIT 1").fetchone()[0]
        now = "2026-08-09T00:00:00+00:00"
        concept_id = uuid4().hex
        conn.execute(
            """
            INSERT INTO concepts (
                id, course_id, current_revision, created_at, updated_at
            ) VALUES (?, ?, 1, ?, ?)
            """,
            (concept_id, course_id, now, now),
        )
        conn.execute(
            """
            INSERT INTO concept_revisions (
                concept_id, course_id, revision, preferred_name,
                short_definition, identity_status, review_status,
                validity_status, proposal_origin, created_at, updated_at
            ) VALUES (?, ?, 1, 'Alpha', 'Alpha definition', 'active',
                      'candidate', 'current', 'human', ?, ?)
            """,
            (concept_id, course_id, now, now),
        )
        operation_id = uuid4().hex
        receipt = json.dumps(
            {
                "entity_type": "concept",
                "entity_id": concept_id,
                "revision": 1,
            }
        )
        conn.execute(
            """
            INSERT INTO concept_graph_operations (
                course_id, operation_id, kind, request_hash, actor, reason,
                entity_type, entity_id, result_revision, result_json,
                created_at
            ) VALUES (?, ?, 'concept_review', ?, 'reviewer', 'reason',
                      'concept', ?, 1, ?, ?)
            """,
            (course_id, operation_id, "a" * 64, concept_id, receipt, now),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE concept_graph_operations SET reason = 'tampered' "
                "WHERE course_id = ? AND operation_id = ?",
                (course_id, operation_id),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO concept_graph_operations (
                    course_id, operation_id, kind, request_hash, actor, reason,
                    entity_type, entity_id, result_revision, result_json,
                    created_at
                ) VALUES (?, ?, 'relation_review', ?, 'reviewer', 'reason',
                          'concept', ?, 1, ?, ?)
                """,
                (
                    course_id,
                    uuid4().hex,
                    "b" * 64,
                    concept_id,
                    receipt,
                    now,
                ),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO concept_graph_operations (
                    course_id, operation_id, kind, request_hash, actor, reason,
                    entity_type, entity_id, result_revision, result_json,
                    created_at
                ) VALUES (?, ?, 'concept_review', ?, 'reviewer', 'reason',
                          'concept', ?, 1, '{}', ?)
                """,
                (course_id, uuid4().hex, "c" * 64, concept_id, now),
            )
