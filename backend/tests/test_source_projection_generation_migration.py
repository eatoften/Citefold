from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from app.migrations import MIGRATIONS, Migration, apply_migrations
from app.source_projection_identity import (
    ProjectionManifestChunk,
    build_projection_manifest_hash,
)


NOW = "2026-08-08T00:00:00+00:00"
V9_AND_V10 = MIGRATIONS[8:]


def _create_v9_projection_database(
    path: Path,
    *,
    text_hash: str | None = None,
    duplicate_ordinal: bool = False,
) -> None:
    text = "A legacy projection needs an explicit generation."
    expected_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    locator = json.dumps(
        {
            "schema_version": 1,
            "kind": "pdf_page",
            "asset_id": "legacy-pdf",
            "page_number": 1,
            "metadata": {},
        }
    )
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE sources (
                id TEXT PRIMARY KEY,
                course_id TEXT NOT NULL,
                origin_type TEXT NOT NULL,
                origin_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                title TEXT NOT NULL,
                content_status TEXT NOT NULL,
                index_status TEXT NOT NULL,
                index_generation TEXT,
                index_model TEXT,
                index_dimension INTEGER,
                enabled INTEGER NOT NULL,
                size_bytes INTEGER,
                mime_type TEXT,
                metadata_json TEXT NOT NULL,
                error_message TEXT,
                index_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                indexed_at TEXT
            );
            CREATE TABLE source_chunks (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                origin_type TEXT NOT NULL,
                origin_id TEXT NOT NULL,
                chunk_type TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                text TEXT NOT NULL,
                text_hash TEXT NOT NULL,
                locator_json TEXT NOT NULL,
                chunker_version TEXT NOT NULL,
                is_active INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE concept_evidence (
                id TEXT PRIMARY KEY,
                course_id TEXT NOT NULL,
                source_id TEXT NOT NULL
            );
            CREATE TABLE relation_evidence (
                id TEXT PRIMARY KEY,
                course_id TEXT NOT NULL,
                source_id TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO schema_migrations (version, name, applied_at)
            VALUES (9, 'evidence_grounded_concept_graph', ?)
            """,
            (NOW,),
        )
        conn.execute(
            """
            INSERT INTO sources (
                id, course_id, origin_type, origin_id, source_type, title,
                content_status, index_status, enabled, metadata_json,
                created_at, updated_at
            ) VALUES (
                'asset:legacy-pdf', 'course-a', 'source_asset', 'legacy-pdf',
                'pdf', 'Legacy.pdf', 'ready', 'not_indexed', 1, '{}', ?, ?
            )
            """,
            (NOW, NOW),
        )
        rows = [
            (
                "source_unit:legacy-page",
                "legacy-page",
                0,
                text_hash or expected_hash,
            )
        ]
        if duplicate_ordinal:
            rows.append(
                (
                    "source_unit:legacy-page-copy",
                    "legacy-page-copy",
                    0,
                    expected_hash,
                )
            )
        conn.executemany(
            """
            INSERT INTO source_chunks (
                id, source_id, origin_type, origin_id, chunk_type, ordinal,
                text, text_hash, locator_json, chunker_version, is_active,
                created_at, updated_at
            ) VALUES (
                ?, 'asset:legacy-pdf', 'source_unit', ?, 'page', ?, ?, ?, ?,
                'legacy-chunker-v1', 1, ?, ?
            )
            """,
            [
                (chunk_id, origin_id, ordinal, text, hash_value, locator, NOW, NOW)
                for chunk_id, origin_id, ordinal, hash_value in rows
            ],
        )
        conn.execute(
            """
            INSERT INTO concept_evidence (id, course_id, source_id)
            VALUES ('legacy-concept-evidence', 'course-a', 'asset:legacy-pdf')
            """
        )
        conn.execute(
            """
            INSERT INTO relation_evidence (id, course_id, source_id)
            VALUES ('legacy-relation-evidence', 'course-a', 'asset:legacy-pdf')
            """
        )


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def test_v10_backfills_source_identity_but_not_legacy_graph_evidence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v9.db"
    _create_v9_projection_database(path)

    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        assert apply_migrations(conn, migrations=V9_AND_V10) == [10]
        assert apply_migrations(conn, migrations=V9_AND_V10) == []
        source = conn.execute(
            """
            SELECT projection_generation_id, projection_manifest_hash
            FROM sources WHERE id = 'asset:legacy-pdf'
            """
        ).fetchone()
        expected_manifest = build_projection_manifest_hash(
            source_id="asset:legacy-pdf",
            source_type="pdf",
            chunks=[
                ProjectionManifestChunk(
                    id="source_unit:legacy-page",
                    chunk_type="page",
                    ordinal=0,
                    text_hash=hashlib.sha256(
                        b"A legacy projection needs an explicit generation."
                    ).hexdigest(),
                    locator={
                        "schema_version": 1,
                        "kind": "pdf_page",
                        "asset_id": "legacy-pdf",
                        "page_number": 1,
                        "metadata": {},
                    },
                    chunker_version="legacy-chunker-v1",
                )
            ],
        )
        assert source["projection_generation_id"]
        assert source["projection_manifest_hash"] == expected_manifest
        assert conn.execute(
            "SELECT projection_generation_id FROM concept_evidence"
        ).fetchone()[0] is None
        assert conn.execute(
            "SELECT projection_generation_id FROM relation_evidence"
        ).fetchone()[0] is None
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                UPDATE sources
                SET projection_generation_id = NULL,
                    projection_manifest_hash = NULL
                WHERE id = 'asset:legacy-pdf'
                """
            )
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"


@pytest.mark.parametrize("invalid_kind", ["hash", "ordinal"])
def test_v10_rejects_untrustworthy_legacy_projection_and_rolls_back(
    tmp_path: Path,
    invalid_kind: str,
) -> None:
    path = tmp_path / f"invalid-{invalid_kind}.db"
    _create_v9_projection_database(
        path,
        text_hash="f" * 64 if invalid_kind == "hash" else None,
        duplicate_ordinal=invalid_kind == "ordinal",
    )

    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        with pytest.raises(RuntimeError):
            apply_migrations(conn, migrations=V9_AND_V10)
        assert "projection_generation_id" not in _columns(conn, "sources")
        assert conn.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0] == 9


def test_v10_injected_failure_rolls_back_columns_indexes_and_ledger(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rollback.db"
    _create_v9_projection_database(path)

    def apply_then_fail(conn: sqlite3.Connection) -> None:
        MIGRATIONS[9].apply(conn)
        raise RuntimeError("injected v10 failure")

    failing = (
        MIGRATIONS[8],
        Migration(
            version=10,
            name="source_projection_generation",
            apply=apply_then_fail,
        ),
    )
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        with pytest.raises(RuntimeError, match="injected v10 failure"):
            apply_migrations(conn, migrations=failing)
        assert "projection_generation_id" not in _columns(conn, "sources")
        objects = {
            str(row[0])
            for row in conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type IN ('index', 'trigger')
                """
            ).fetchall()
        }
        assert "idx_sources_projection_generation" not in objects
        assert "sources_projection_identity_insert" not in objects
        assert conn.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0] == 9
