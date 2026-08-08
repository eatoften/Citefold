from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.migrations import MIGRATIONS, Migration, apply_migrations


GRAPH_MIGRATION = tuple(
    migration for migration in MIGRATIONS if migration.version == 9
)
NOW = "2026-08-08T00:00:00+00:00"


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _insert_concept(
    conn: sqlite3.Connection,
    concept_id: str,
    course_id: str,
) -> None:
    conn.execute(
        """
        INSERT INTO concepts (
            id, course_id, current_revision, created_at, updated_at
        ) VALUES (?, ?, 1, ?, ?)
        """,
        (concept_id, course_id, NOW, NOW),
    )
    conn.execute(
        """
        INSERT INTO concept_revisions (
            concept_id, course_id, revision, preferred_name,
            short_definition, created_at, updated_at
        ) VALUES (?, ?, 1, ?, 'Definition', ?, ?)
        """,
        (concept_id, course_id, concept_id, NOW, NOW),
    )


def _insert_relation(
    conn: sqlite3.Connection,
    relation_id: str,
) -> None:
    conn.execute(
        """
        INSERT INTO concept_relations (
            id, course_id, source_concept_id, target_concept_id,
            relation_type, current_revision, created_at, updated_at
        ) VALUES (?, 'course-1', 'concept-a', 'concept-b',
                  'related', 1, ?, ?)
        """,
        (relation_id, NOW, NOW),
    )
    conn.execute(
        """
        INSERT INTO concept_relation_revisions (
            relation_id, course_id, revision, support_basis, rationale,
            created_at, updated_at
        ) VALUES (?, 'course-1', 1, 'source_asserted',
                  'The source states this relation.', ?, ?)
        """,
        (relation_id, NOW, NOW),
    )


def test_v9_migration_is_additive_and_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "v8.db"
    with _connect(db_path) as conn:
        conn.execute("CREATE TABLE courses (id TEXT PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE card_relations (id TEXT PRIMARY KEY, status TEXT)"
        )
        conn.execute("INSERT INTO courses (id) VALUES ('course-1')")
        conn.execute(
            "INSERT INTO card_relations (id, status) "
            "VALUES ('legacy-edge', 'accepted')"
        )

        assert apply_migrations(conn, migrations=GRAPH_MIGRATION) == [9]
        assert apply_migrations(conn, migrations=GRAPH_MIGRATION) == []

        table_names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {
            "concepts",
            "concept_revisions",
            "concept_evidence",
            "concept_relations",
            "concept_relation_revisions",
            "relation_evidence",
        }.issubset(table_names)
        assert conn.execute(
            "SELECT status FROM card_relations WHERE id = 'legacy-edge'"
        ).fetchone()[0] == "accepted"
        assert tuple(
            conn.execute(
                "SELECT version, name FROM schema_migrations"
            ).fetchone()
        ) == (9, "evidence_grounded_concept_graph")
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v9_migration_failure_rolls_back_schema_and_ledger(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "v9-failure.db"
    with _connect(db_path) as conn:
        conn.execute("CREATE TABLE courses (id TEXT PRIMARY KEY)")

        def create_graph_then_fail(active_conn: sqlite3.Connection) -> None:
            GRAPH_MIGRATION[0].apply(active_conn)
            active_conn.execute("CREATE TABLE graph_partial_write (id TEXT)")
            raise RuntimeError("injected v9 failure")

        failing = (
            Migration(
                version=9,
                name="evidence_grounded_concept_graph",
                apply=create_graph_then_fail,
            ),
        )
        with pytest.raises(RuntimeError, match="injected v9 failure"):
            apply_migrations(conn, migrations=failing)

        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "concepts" not in names
        assert "graph_partial_write" not in names
        assert "schema_migrations" not in names


def test_v9_schema_enforces_stable_canonical_relation_identity(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "graph.db"
    with _connect(db_path) as conn:
        conn.execute("CREATE TABLE courses (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO courses (id) VALUES ('course-1')")
        apply_migrations(conn, migrations=GRAPH_MIGRATION)
        _insert_concept(conn, "concept-a", "course-1")
        _insert_concept(conn, "concept-b", "course-1")
        _insert_relation(conn, "relation-1")

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO concept_relations (
                    id, course_id, source_concept_id, target_concept_id,
                    relation_type, current_revision, created_at, updated_at
                ) VALUES ('relation-2', 'course-1', 'concept-a', 'concept-b',
                          'related', 1, ?, ?)
                """,
                (NOW, NOW),
            )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO concept_relations (
                    id, course_id, source_concept_id, target_concept_id,
                    relation_type, current_revision, created_at, updated_at
                ) VALUES (
                    'relation-reversed', 'course-1', 'concept-b', 'concept-a',
                    'related', 1, ?, ?
                )
                """,
                (NOW, NOW),
            )


def test_v9_schema_rejects_cross_course_and_self_merge_targets(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "merge.db"
    with _connect(db_path) as conn:
        conn.execute("CREATE TABLE courses (id TEXT PRIMARY KEY)")
        conn.executemany(
            "INSERT INTO courses (id) VALUES (?)",
            [("course-a",), ("course-b",)],
        )
        apply_migrations(conn, migrations=GRAPH_MIGRATION)
        _insert_concept(conn, "concept-a", "course-a")
        _insert_concept(conn, "concept-b", "course-b")

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO concept_revisions (
                    concept_id, course_id, revision, preferred_name,
                    short_definition, identity_status,
                    merged_into_concept_id, created_at, updated_at
                ) VALUES (
                    'concept-a', 'course-a', 2, 'A', 'A', 'merged',
                    'concept-b', ?, ?
                )
                """,
                (NOW, NOW),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO concept_revisions (
                    concept_id, course_id, revision, preferred_name,
                    short_definition, identity_status,
                    merged_into_concept_id, created_at, updated_at
                ) VALUES (
                    'concept-a', 'course-a', 2, 'A', 'A', 'merged',
                    'concept-a', ?, ?
                )
                """,
                (NOW, NOW),
            )


def test_v9_current_pointer_cannot_commit_without_its_revision(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "missing-revision.db"
    conn = _connect(db_path)
    try:
        conn.execute("CREATE TABLE courses (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO courses (id) VALUES ('course-1')")
        apply_migrations(conn, migrations=GRAPH_MIGRATION)
        conn.commit()

        conn.execute(
            """
            INSERT INTO concepts (
                id, course_id, current_revision, created_at, updated_at
            ) VALUES ('missing-revision', 'course-1', 1, ?, ?)
            """,
            (NOW, NOW),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.commit()
        conn.rollback()
        assert conn.execute(
            "SELECT COUNT(*) FROM concepts WHERE id = 'missing-revision'"
        ).fetchone()[0] == 0
    finally:
        conn.close()
