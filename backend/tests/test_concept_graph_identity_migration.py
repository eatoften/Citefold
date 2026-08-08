from __future__ import annotations

import json
import sqlite3

import pytest

from app.migrations import (
    Migration,
    _add_concept_graph_identity_lifecycle,
    apply_migrations,
)


def _v11_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE courses (id TEXT PRIMARY KEY);
        CREATE TABLE concept_revisions (
            concept_id TEXT NOT NULL,
            course_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            preferred_name TEXT NOT NULL,
            merged_into_concept_id TEXT,
            PRIMARY KEY (concept_id, revision)
        );
        CREATE TABLE concept_evidence (
            id TEXT PRIMARY KEY,
            concept_id TEXT NOT NULL,
            quote TEXT NOT NULL
        );
        CREATE TABLE concept_aliases (
            id TEXT PRIMARY KEY,
            concept_id TEXT NOT NULL,
            display_text TEXT NOT NULL
        );
        CREATE TABLE concept_relation_revisions (
            relation_id TEXT NOT NULL,
            course_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            rationale TEXT NOT NULL,
            PRIMARY KEY (relation_id, revision)
        );
        CREATE TABLE relation_evidence (
            id TEXT PRIMARY KEY,
            relation_id TEXT NOT NULL,
            quote TEXT NOT NULL
        );
        CREATE TABLE concept_graph_operations (
            course_id TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            actor TEXT NOT NULL,
            reason TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            result_revision INTEGER NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (course_id, operation_id),
            FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE,
            CHECK (kind IN (
                'concept_edit', 'concept_review', 'concept_mark_stale',
                'relation_edit', 'relation_review', 'relation_mark_stale'
            )),
            CHECK (
                (entity_type = 'concept' AND kind IN (
                    'concept_edit', 'concept_review', 'concept_mark_stale'
                )) OR
                (entity_type = 'relation' AND kind IN (
                    'relation_edit', 'relation_review',
                    'relation_mark_stale'
                ))
            )
        );
        CREATE INDEX idx_concept_graph_operations_entity
        ON concept_graph_operations (
            course_id, entity_type, entity_id, result_revision
        );
        CREATE TRIGGER concept_graph_operation_result_insert
        BEFORE INSERT ON concept_graph_operations
        BEGIN SELECT 1; END;
        CREATE TRIGGER concept_graph_operation_immutable_update
        BEFORE UPDATE ON concept_graph_operations
        BEGIN
            SELECT RAISE(ABORT, 'Concept graph operation is immutable');
        END;
        """
    )
    conn.execute("INSERT INTO courses (id) VALUES ('course')")
    conn.execute(
        "INSERT INTO concept_revisions VALUES "
        "('concept', 'course', 1, 'Alpha', NULL)"
    )
    conn.execute(
        "INSERT INTO concept_relation_revisions VALUES "
        "('relation', 'course', 1, 'Rationale')"
    )
    receipt = '{ "revision": 1, "entity_id": "concept", "entity_type": "concept" }'
    conn.execute(
        """
        INSERT INTO concept_graph_operations (
            course_id, operation_id, kind, request_hash, actor, reason,
            entity_type, entity_id, result_revision, result_json, created_at
        ) VALUES (
            'course', 'existing', 'concept_review', ?, 'reviewer', 'reason',
            'concept', 'concept', 1, ?, '2026-08-09T00:00:00+00:00'
        )
        """,
        ("a" * 64, receipt),
    )
    conn.commit()
    return conn


def _receipt(conn: sqlite3.Connection) -> tuple[object, ...]:
    row = conn.execute(
        "SELECT * FROM concept_graph_operations "
        "WHERE course_id = 'course' AND operation_id = 'existing'"
    ).fetchone()
    assert row is not None
    return tuple(row)


def test_v11_to_v12_preserves_receipt_and_adds_kinds_guards_and_index() -> None:
    conn = _v11_connection()
    try:
        before = _receipt(conn)
        _add_concept_graph_identity_lifecycle(conn)
        assert _receipt(conn) == before

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
        assert "idx_concept_revisions_merge_target" in indexes
        assert {
            "concept_revisions_immutable_update",
            "concept_evidence_immutable_update",
            "concept_aliases_immutable_update",
            "concept_relation_revisions_immutable_update",
            "relation_evidence_immutable_update",
            "concept_graph_operation_result_insert",
            "concept_graph_operation_immutable_update",
        } <= triggers

        for operation_id, kind, entity_type, entity_id in (
            ("merge", "concept_merge", "concept", "concept"),
            ("retire", "concept_retire", "concept", "concept"),
            ("reserved-concept", "concept_create", "concept", "concept"),
            ("reserved-relation", "relation_create", "relation", "relation"),
        ):
            result = json.dumps(
                {
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "revision": 1,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            conn.execute(
                """
                INSERT INTO concept_graph_operations (
                    course_id, operation_id, kind, request_hash, actor, reason,
                    entity_type, entity_id, result_revision, result_json,
                    created_at
                ) VALUES ('course', ?, ?, ?, 'actor', 'reason', ?, ?, 1, ?, ?)
                """,
                (
                    operation_id,
                    kind,
                    "b" * 64,
                    entity_type,
                    entity_id,
                    result,
                    "2026-08-09T00:00:00+00:00",
                ),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE concept_revisions SET preferred_name = 'Changed'"
            )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_v12_migration_failure_rolls_back_rebuild_and_preserves_v11() -> None:
    conn = _v11_connection()
    try:
        before = _receipt(conn)
        conn.execute(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO schema_migrations VALUES "
            "(11, 'concept_graph_review_lifecycle', 'now')"
        )
        conn.commit()

        v11 = Migration(
            version=11,
            name="concept_graph_review_lifecycle",
            apply=lambda target: None,
        )

        def fail(target: sqlite3.Connection) -> None:
            _add_concept_graph_identity_lifecycle(target)
            raise RuntimeError("injected v12 failure")

        v12 = Migration(
            version=12,
            name="concept_graph_identity_lifecycle",
            apply=fail,
        )
        with pytest.raises(RuntimeError, match="injected v12 failure"):
            apply_migrations(conn, migrations=(v11, v12))

        assert _receipt(conn) == before
        sql = str(
            conn.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'concept_graph_operations'"
            ).fetchone()[0]
        )
        assert "concept_merge" not in sql
        assert conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 12"
        ).fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        conn.close()
