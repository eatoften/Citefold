from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.db import connect
from app.migrations import (
    Migration,
    _add_concept_graph_publication,
    apply_migrations,
    prepare_migration_backup,
)


PUBLICATION_TABLES = {
    "concept_graph_versions",
    "concept_graph_version_heads",
    "concept_graph_version_concepts",
    "concept_graph_version_concept_aliases",
    "concept_graph_version_concept_evidence",
    "concept_graph_version_relations",
    "concept_graph_version_relation_evidence",
    "concept_graph_publication_operations",
}


def _v12_database(path: Path | str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE courses (id TEXT PRIMARY KEY);
        CREATE TABLE concept_graph_operations (
            course_id TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            PRIMARY KEY (course_id, operation_id),
            FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
        );
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );
        INSERT INTO courses VALUES ('course');
        INSERT INTO concept_graph_operations
        VALUES ('course', 'draft-receipt', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
        INSERT INTO schema_migrations
        VALUES (12, 'concept_graph_identity_lifecycle', 'now');
        """
    )
    conn.commit()
    return conn


def _insert_concept(
    conn: sqlite3.Connection,
    *,
    course_id: str,
    version: int,
    ordinal: int,
    concept_id: str,
    revision: int,
    alias: bool,
    review_revision: int | None = None,
    aggregate_hash: str = "e" * 64,
) -> None:
    now = "2026-08-09T00:00:00+00:00"
    conn.execute(
        """
        INSERT INTO concept_graph_version_concepts (
            course_id, version_number, ordinal, concept_id,
            concept_revision, preferred_name, short_definition,
            identity_status, review_status, validity_status,
            proposal_origin, provider, model, prompt_protocol,
            output_version, review_operation_id,
            review_operation_request_hash, review_actor, review_reason,
            reviewed_at,
            review_revision,
            revision_created_at, revision_updated_at, aggregate_hash
        ) VALUES (?, ?, ?, ?, ?, ?, 'Definition', 'active', 'accepted',
                  'current', 'human', NULL, NULL, NULL, NULL, ?, ?,
                  'reviewer', 'Accepted after review', ?, ?, ?, ?, ?)
        """,
        (
            course_id,
            version,
            ordinal,
            concept_id,
            revision,
            concept_id.title(),
            f"review-{concept_id}-{revision}",
            "a" * 64,
            now,
            revision - 1 if review_revision is None else review_revision,
            now,
            now,
            aggregate_hash,
        ),
    )
    if alias:
        conn.execute(
            """
            INSERT INTO concept_graph_version_concept_aliases (
                course_id, version_number, concept_id, concept_revision,
                ordinal, alias_id, display_text, normalized_text, created_at
            ) VALUES (?, ?, ?, ?, 0, ?, 'Alias', 'alias', ?)
            """,
            (
                course_id,
                version,
                concept_id,
                revision,
                f"alias-{concept_id}-{revision}",
                now,
            ),
        )
    conn.execute(
        """
        INSERT INTO concept_graph_version_concept_evidence (
            course_id, version_number, concept_id, concept_revision,
            ordinal, evidence_id, source_id, chunk_id, chunk_text_hash,
            projection_generation_id, source_title, source_type, quote,
            locator_json, created_at
        ) VALUES (?, ?, ?, ?, 0, ?, 'asset:source', 'source_unit:page-1',
                  ?, 'generation-1', 'Source.pdf', 'pdf', 'evidence',
                  '{"schema_version":1,"kind":"pdf_page","asset_id":"source","page_number":1,"metadata":{}}', ?)
        """,
        (
            course_id,
            version,
            concept_id,
            revision,
            f"evidence-{concept_id}-{revision}",
            "b" * 64,
            now,
        ),
    )


def _insert_relation(
    conn: sqlite3.Connection,
    *,
    course_id: str,
    version: int,
    source_id: str,
    source_revision: int,
    target_id: str,
    target_revision: int,
    review_revision: int = 1,
    aggregate_hash: str = "f" * 64,
) -> None:
    now = "2026-08-09T00:00:00+00:00"
    conn.execute(
        """
        INSERT INTO concept_graph_version_relations (
            course_id, version_number, ordinal, relation_id,
            relation_revision, source_concept_id, source_concept_revision,
            target_concept_id, target_concept_revision, relation_type,
            support_basis, rationale, review_status, validity_status,
            proposal_origin, provider, model, prompt_protocol,
            output_version, review_operation_id,
            review_operation_request_hash, review_actor, review_reason,
            reviewed_at, review_revision,
            binding_created_at,
            revision_created_at, revision_updated_at, aggregate_hash
        ) VALUES (?, ?, 0, 'relation', 2, ?, ?, ?, ?, 'prerequisite',
                  'source_asserted', 'Source precedes target.', 'accepted',
                  'current', 'human', NULL, NULL, NULL, NULL,
                  'review-relation', ?, 'reviewer',
                  'Accepted after review', ?, ?, ?, ?, ?, ?)
        """,
        (
            course_id,
            version,
            source_id,
            source_revision,
            target_id,
            target_revision,
            "d" * 64,
            now,
            review_revision,
            now,
            now,
            now,
            aggregate_hash,
        ),
    )
    conn.execute(
        """
        INSERT INTO concept_graph_version_relation_evidence (
            course_id, version_number, relation_id, relation_revision,
            ordinal, evidence_id, support_role, source_id, chunk_id,
            chunk_text_hash, projection_generation_id, source_title,
            source_type, quote, locator_json, created_at
        ) VALUES (?, ?, 'relation', 2, 0, 'relation-evidence',
                  'relation_assertion', 'asset:source', 'source_unit:page-1',
                  ?, 'generation-1', 'Source.pdf', 'pdf', 'evidence',
                  '{"schema_version":1,"kind":"pdf_page","asset_id":"source","page_number":1,"metadata":{}}', ?)
        """,
        (course_id, version, "c" * 64, now),
    )


def _seal_version_one(conn: sqlite3.Connection, course_id: str) -> None:
    now = "2026-08-09T00:00:00+00:00"
    _insert_concept(
        conn,
        course_id=course_id,
        version=1,
        ordinal=0,
        concept_id="alpha",
        revision=2,
        alias=True,
    )
    _insert_concept(
        conn,
        course_id=course_id,
        version=1,
        ordinal=1,
        concept_id="beta",
        revision=2,
        alias=False,
    )
    _insert_relation(
        conn,
        course_id=course_id,
        version=1,
        source_id="alpha",
        source_revision=2,
        target_id="beta",
        target_revision=2,
    )
    conn.execute(
        """
        INSERT INTO concept_graph_versions (
            course_id, version_number, parent_version_number,
            draft_manifest_hash, content_hash, concept_count,
            concept_alias_count, relation_count, concept_evidence_count,
            relation_evidence_count, published_by, publication_reason,
            published_at
        ) VALUES (?, 1, NULL, ?, ?, 2, 1, 1, 2, 1,
                  'publisher', 'Initial publication', ?)
        """,
        (course_id, "d" * 64, "e" * 64, now),
    )
    conn.execute(
        """
        INSERT INTO concept_graph_version_heads (
            course_id, active_version_number, updated_at
        ) VALUES (?, 1, ?)
        """,
        (course_id, now),
    )
    conn.execute(
        """
        INSERT INTO concept_graph_publication_operations (
            course_id, operation_id, request_hash,
            expected_active_version_number, expected_draft_manifest_hash,
            actor, reason, result_version_number, result_content_hash,
            created_at
        ) VALUES (?, 'publish-1', ?, NULL, ?, 'publisher',
                  'Initial publication', 1, ?, ?)
        """,
        (course_id, "f" * 64, "d" * 64, "e" * 64, now),
    )


def _seal_version_two(conn: sqlite3.Connection, course_id: str) -> None:
    now = "2026-08-09T01:00:00+00:00"
    _insert_concept(
        conn,
        course_id=course_id,
        version=2,
        ordinal=0,
        concept_id="alpha",
        revision=3,
        alias=True,
    )
    _insert_concept(
        conn,
        course_id=course_id,
        version=2,
        ordinal=1,
        concept_id="beta",
        revision=3,
        alias=False,
    )
    _insert_relation(
        conn,
        course_id=course_id,
        version=2,
        source_id="alpha",
        source_revision=3,
        target_id="beta",
        target_revision=3,
    )
    conn.execute(
        """
        INSERT INTO concept_graph_versions (
            course_id, version_number, parent_version_number,
            draft_manifest_hash, content_hash, concept_count,
            concept_alias_count, relation_count, concept_evidence_count,
            relation_evidence_count, published_by, publication_reason,
            published_at
        ) VALUES (?, 2, 1, ?, ?, 2, 1, 1, 2, 1,
                  'publisher', 'Second publication', ?)
        """,
        (course_id, "1" * 64, "2" * 64, now),
    )
    conn.execute(
        """
        UPDATE concept_graph_version_heads
        SET active_version_number = 2, updated_at = ?
        WHERE course_id = ? AND active_version_number = 1
        """,
        (now, course_id),
    )
    conn.execute(
        """
        INSERT INTO concept_graph_publication_operations (
            course_id, operation_id, request_hash,
            expected_active_version_number, expected_draft_manifest_hash,
            actor, reason, result_version_number, result_content_hash,
            created_at
        ) VALUES (?, 'publish-2', ?, 1, ?, 'publisher',
                  'Second publication', 2, ?, ?)
        """,
        (course_id, "3" * 64, "1" * 64, "2" * 64, now),
    )


def test_v13_clean_schema_seals_snapshot_and_guards_immutability() -> None:
    with connect() as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert PUBLICATION_TABLES <= tables
        course_id = str(
            conn.execute("SELECT id FROM courses LIMIT 1").fetchone()[0]
        )
        _seal_version_one(conn, course_id)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE concept_graph_versions SET content_hash = ? "
                "WHERE course_id = ? AND version_number = 1",
                ("0" * 64, course_id),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE concept_graph_version_concepts "
                "SET preferred_name = 'tampered' WHERE course_id = ?",
                (course_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE concept_graph_publication_operations "
                "SET reason = 'tampered' WHERE course_id = ?",
                (course_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO concept_graph_version_concept_aliases (
                    course_id, version_number, concept_id, concept_revision,
                    ordinal, alias_id, display_text, normalized_text,
                    created_at
                ) VALUES (?, 1, 'alpha', 2, 1, 'late', 'Late', 'late', 'now')
                """,
                (course_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE concept_graph_version_heads "
                "SET active_version_number = 3 WHERE course_id = ?",
                (course_id,),
            )
        for table in PUBLICATION_TABLES:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    f"DELETE FROM {table} WHERE course_id = ?",
                    (course_id,),
                )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_v13_relation_requires_exact_published_endpoint_revisions() -> None:
    with connect() as conn:
        course_id = str(
            conn.execute("SELECT id FROM courses LIMIT 1").fetchone()[0]
        )
        conn.execute("SAVEPOINT invalid_relation_snapshot")
        with pytest.raises(sqlite3.IntegrityError):
            _insert_concept(
                conn,
                course_id=course_id,
                version=1,
                ordinal=0,
                concept_id="invalid-review-lineage",
                revision=2,
                alias=False,
                review_revision=2,
            )
        _insert_concept(
            conn,
            course_id=course_id,
            version=1,
            ordinal=0,
            concept_id="alpha",
            revision=2,
            alias=False,
        )
        _insert_concept(
            conn,
            course_id=course_id,
            version=1,
            ordinal=1,
            concept_id="beta",
            revision=2,
            alias=False,
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_relation(
                conn,
                course_id=course_id,
                version=1,
                source_id="alpha",
                source_revision=2,
                target_id="beta",
                target_revision=2,
                review_revision=2,
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_relation(
                conn,
                course_id=course_id,
                version=1,
                source_id="alpha",
                source_revision=99,
                target_id="beta",
                target_revision=2,
            )
        conn.execute("ROLLBACK TO SAVEPOINT invalid_relation_snapshot")
        conn.execute("RELEASE SAVEPOINT invalid_relation_snapshot")
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v13_aggregate_hash_and_child_ordinal_constraints() -> None:
    with connect() as conn:
        course_id = str(
            conn.execute("SELECT id FROM courses LIMIT 1").fetchone()[0]
        )
        conn.execute("SAVEPOINT aggregate_constraints")
        with pytest.raises(sqlite3.IntegrityError):
            _insert_concept(
                conn,
                course_id=course_id,
                version=1,
                ordinal=0,
                concept_id="bad-hash",
                revision=2,
                alias=False,
                aggregate_hash="E" * 64,
            )
        _insert_concept(
            conn,
            course_id=course_id,
            version=1,
            ordinal=0,
            concept_id="alpha",
            revision=2,
            alias=False,
        )
        _insert_concept(
            conn,
            course_id=course_id,
            version=1,
            ordinal=1,
            concept_id="beta",
            revision=2,
            alias=False,
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_relation(
                conn,
                course_id=course_id,
                version=1,
                source_id="alpha",
                source_revision=2,
                target_id="beta",
                target_revision=2,
                aggregate_hash="F" * 64,
            )
        _insert_relation(
            conn,
            course_id=course_id,
            version=1,
            source_id="alpha",
            source_revision=2,
            target_id="beta",
            target_revision=2,
        )
        invalid_children = (
            (
                """
                INSERT INTO concept_graph_version_concept_aliases (
                    course_id, version_number, concept_id, concept_revision,
                    ordinal, alias_id, display_text, normalized_text,
                    created_at
                ) VALUES (?, 1, 'alpha', 2, 32, 'alias-32',
                          'Alias 32', 'alias 32', 'now')
                """,
                (course_id,),
            ),
            (
                """
                INSERT INTO concept_graph_version_concept_evidence (
                    course_id, version_number, concept_id, concept_revision,
                    ordinal, evidence_id, source_id, chunk_id,
                    chunk_text_hash, projection_generation_id, source_title,
                    source_type, quote, locator_json, created_at
                ) VALUES (?, 1, 'alpha', 2, 32, 'concept-evidence-32',
                          'asset:source', 'source_unit:page-32', ?,
                          'generation-1', 'Source.pdf', 'pdf', 'evidence',
                          '{"kind":"pdf_page"}', 'now')
                """,
                (course_id, "a" * 64),
            ),
            (
                """
                INSERT INTO concept_graph_version_relation_evidence (
                    course_id, version_number, relation_id,
                    relation_revision, ordinal, evidence_id, support_role,
                    source_id, chunk_id, chunk_text_hash,
                    projection_generation_id, source_title, source_type,
                    quote, locator_json, created_at
                ) VALUES (?, 1, 'relation', 2, 32,
                          'relation-evidence-32', 'relation_assertion',
                          'asset:source', 'source_unit:page-32', ?,
                          'generation-1', 'Source.pdf', 'pdf', 'evidence',
                          '{"kind":"pdf_page"}', 'now')
                """,
                (course_id, "b" * 64),
            ),
        )
        for sql, parameters in invalid_children:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql, parameters)
        conn.execute("ROLLBACK TO SAVEPOINT aggregate_constraints")
        conn.execute("RELEASE SAVEPOINT aggregate_constraints")


def test_v13_seal_rejects_bad_counts_inactive_parent_and_bad_receipt() -> None:
    with connect() as conn:
        course_id = str(
            conn.execute("SELECT id FROM courses LIMIT 1").fetchone()[0]
        )
        _seal_version_one(conn, course_id)
        _insert_concept(
            conn,
            course_id=course_id,
            version=2,
            ordinal=0,
            concept_id="alpha",
            revision=3,
            alias=False,
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO concept_graph_versions (
                    course_id, version_number, parent_version_number,
                    draft_manifest_hash, content_hash, concept_count,
                    concept_alias_count, relation_count,
                    concept_evidence_count, relation_evidence_count,
                    published_by, publication_reason, published_at
                ) VALUES (?, 2, 1, ?, ?, 2, 0, 0, 1, 0,
                          'publisher', 'Bad count', 'now')
                """,
                (course_id, "1" * 64, "2" * 64),
            )
        conn.execute(
            """
            INSERT INTO concept_graph_versions (
                course_id, version_number, parent_version_number,
                draft_manifest_hash, content_hash, concept_count,
                concept_alias_count, relation_count,
                concept_evidence_count, relation_evidence_count,
                published_by, publication_reason, published_at
            ) VALUES (?, 2, 1, ?, ?, 1, 0, 0, 1, 0,
                      'publisher', 'Second publication', 'now')
            """,
            (course_id, "1" * 64, "2" * 64),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO concept_graph_publication_operations (
                    course_id, operation_id, request_hash,
                    expected_active_version_number,
                    expected_draft_manifest_hash, actor, reason,
                    result_version_number, result_content_hash, created_at
                ) VALUES (?, 'early-receipt', ?, 1, ?, 'publisher',
                          'Second publication', 2, ?, 'now')
                """,
                (course_id, "3" * 64, "1" * 64, "2" * 64),
            )

        conn.execute("SAVEPOINT inactive_branch")
        _insert_concept(
            conn,
            course_id=course_id,
            version=3,
            ordinal=0,
            concept_id="alpha",
            revision=4,
            alias=False,
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO concept_graph_versions (
                    course_id, version_number, parent_version_number,
                    draft_manifest_hash, content_hash, concept_count,
                    concept_alias_count, relation_count,
                    concept_evidence_count, relation_evidence_count,
                    published_by, publication_reason, published_at
                ) VALUES (?, 3, 2, ?, ?, 1, 0, 0, 1, 0,
                          'publisher', 'Inactive branch', 'now')
                """,
                (course_id, "4" * 64, "5" * 64),
            )
        conn.execute("ROLLBACK TO SAVEPOINT inactive_branch")
        conn.execute("RELEASE SAVEPOINT inactive_branch")
        conn.execute(
            "UPDATE concept_graph_version_heads "
            "SET active_version_number = 2, updated_at = 'now' "
            "WHERE course_id = ?",
            (course_id,),
        )
        conn.execute(
            """
            INSERT INTO concept_graph_publication_operations (
                course_id, operation_id, request_hash,
                expected_active_version_number, expected_draft_manifest_hash,
                actor, reason, result_version_number, result_content_hash,
                created_at
            ) VALUES (?, 'publish-2', ?, 1, ?, 'publisher',
                      'Second publication', 2, ?, 'now')
            """,
            (course_id, "6" * 64, "1" * 64, "2" * 64),
        )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v13_course_purge_cascades_versions_heads_snapshots_and_receipts() -> None:
    with connect() as conn:
        course_id = str(
            conn.execute("SELECT id FROM courses LIMIT 1").fetchone()[0]
        )
        _seal_version_one(conn, course_id)
        _seal_version_two(conn, course_id)
        conn.execute("DELETE FROM courses WHERE id = ?", (course_id,))
        for table in PUBLICATION_TABLES:
            assert conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE course_id = ?",
                (course_id,),
            ).fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_v12_to_v13_preserves_receipts_and_failed_upgrade_rolls_back() -> None:
    conn = _v12_database()
    try:
        before = tuple(
            conn.execute(
                "SELECT * FROM concept_graph_operations"
            ).fetchone()
        )
        v12 = Migration(
            version=12,
            name="concept_graph_identity_lifecycle",
            apply=lambda target: None,
        )

        def fail(target: sqlite3.Connection) -> None:
            _add_concept_graph_publication(target)
            raise RuntimeError("injected v13 failure")

        failing_v13 = Migration(
            version=13,
            name="concept_graph_publication",
            apply=fail,
        )
        with pytest.raises(RuntimeError, match="injected v13 failure"):
            apply_migrations(conn, migrations=(v12, failing_v13))
        assert tuple(
            conn.execute("SELECT * FROM concept_graph_operations").fetchone()
        ) == before
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert PUBLICATION_TABLES.isdisjoint(tables)
        assert conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = 13"
        ).fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_v13_pre_migration_backup_preserves_v12_receipt(tmp_path: Path) -> None:
    db_path = tmp_path / "v12.db"
    conn = _v12_database(db_path)
    conn.close()
    v12 = Migration(
        version=12,
        name="concept_graph_identity_lifecycle",
        apply=lambda target: None,
    )
    v13 = Migration(
        version=13,
        name="concept_graph_publication",
        apply=_add_concept_graph_publication,
    )

    backup = prepare_migration_backup(
        db_path,
        migrations=(v12, v13),
    )
    assert backup is not None
    assert ".pre-migration-v13-" in backup.name
    with sqlite3.connect(backup) as backup_conn:
        assert backup_conn.execute(
            "SELECT operation_id FROM concept_graph_operations"
        ).fetchone()[0] == "draft-receipt"
        assert backup_conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"

    with sqlite3.connect(db_path) as upgraded:
        upgraded.execute("PRAGMA foreign_keys = ON")
        assert apply_migrations(upgraded, migrations=(v12, v13)) == [13]
        assert upgraded.execute(
            "SELECT operation_id FROM concept_graph_operations"
        ).fetchone()[0] == "draft-receipt"
        assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []
        assert upgraded.execute("PRAGMA quick_check").fetchone()[0] == "ok"
