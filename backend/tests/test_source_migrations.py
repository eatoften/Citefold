from __future__ import annotations

import builtins
import json
import sqlite3
from pathlib import Path

import pytest

from app.migrations import (
    MIGRATIONS,
    Migration,
    apply_migrations,
    latest_schema_version,
    prepare_migration_backup,
)


NOW = "2026-07-27T00:00:00+00:00"
SOURCE_MIGRATIONS = MIGRATIONS[:1]


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _create_legacy_database(path: Path) -> None:
    with _connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,
                course_id TEXT NOT NULL,
                video_path TEXT NOT NULL,
                status TEXT NOT NULL,
                original_filename TEXT,
                stored_name TEXT,
                size_bytes INTEGER,
                metadata TEXT,
                error_message TEXT,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE source_assets (
                id TEXT PRIMARY KEY,
                course_id TEXT NOT NULL,
                asset_type TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                mime_type TEXT,
                size_bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                extraction_status TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE transcript_chunks (
                id TEXT PRIMARY KEY,
                course_id TEXT NOT NULL,
                job_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                start_seconds REAL NOT NULL,
                end_seconds REAL NOT NULL,
                text TEXT NOT NULL,
                segment_ids TEXT NOT NULL,
                chunker_version TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE source_units (
                id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL,
                unit_type TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                text TEXT NOT NULL,
                locator_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE knowledge_cards (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL
            );

            CREATE TABLE card_embeddings (
                card_id TEXT PRIMARY KEY,
                vector BLOB NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO jobs (
                id, course_id, video_path, status, original_filename,
                stored_name, size_bytes, metadata, error_message,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "video-1",
                "course-a",
                str(path.parent / "must-not-be-opened.mp4"),
                "completed",
                "lecture.mp4",
                "stored-video.mp4",
                2_000_000_000,
                json.dumps({"duration_seconds": 120}),
                None,
                NOW,
                NOW,
            ),
        )
        conn.execute(
            """
            INSERT INTO source_assets (
                id, course_id, asset_type, original_filename, stored_path,
                mime_type, size_bytes, sha256, extraction_status,
                metadata_json, error_message, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "pdf-1",
                "course-a",
                "pdf",
                "notes.pdf",
                str(path.parent / "notes.pdf"),
                "application/pdf",
                1234,
                "a" * 64,
                "ready",
                "{}",
                None,
                NOW,
                NOW,
            ),
        )
        conn.execute(
            """
            INSERT INTO transcript_chunks (
                id, course_id, job_id, chunk_index, start_seconds,
                end_seconds, text, segment_ids, chunker_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "transcript-1",
                "course-a",
                "video-1",
                0,
                12.5,
                42.0,
                "Gradient descent follows the negative gradient.",
                "[3, 4]",
                "semantic-v1",
                NOW,
            ),
        )
        conn.execute(
            """
            INSERT INTO source_units (
                id, asset_id, unit_type, ordinal, text, locator_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "page-1",
                "pdf-1",
                "page",
                0,
                "The learning rate controls update size.",
                '{"page_number": 7}',
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO knowledge_cards (id, title) VALUES (?, ?)",
            ("card-1", "Gradient descent"),
        )
        conn.execute(
            "INSERT INTO card_embeddings (card_id, vector) VALUES (?, ?)",
            ("card-1", b"legacy-vector"),
        )


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


def test_unified_source_migration_backfills_legacy_rows_without_reading_video(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy.db"
    _create_legacy_database(db_path)
    real_open = builtins.open

    def reject_video_reads(file, *args, **kwargs):
        if str(file).endswith(".mp4"):
            raise AssertionError("Migration must not read or hash video files.")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", reject_video_reads)

    with _connect(db_path) as conn:
        completed = apply_migrations(conn, migrations=SOURCE_MIGRATIONS)

        assert completed == [latest_schema_version(SOURCE_MIGRATIONS)]
        assert {
            "sources",
            "source_chunks",
            "source_chunk_embeddings",
            "schema_migrations",
        }.issubset(_table_names(conn))

        sources = conn.execute(
            "SELECT * FROM sources ORDER BY id"
        ).fetchall()
        assert [row["id"] for row in sources] == [
            "asset:pdf-1",
            "job:video-1",
        ]
        assert sources[0]["origin_type"] == "source_asset"
        assert sources[0]["index_generation"] is None
        assert sources[0]["source_type"] == "pdf"
        assert sources[1]["origin_type"] == "video_job"
        assert sources[1]["source_type"] == "video"
        assert sources[1]["size_bytes"] == 2_000_000_000

        chunks = conn.execute(
            """
            SELECT source_id, origin_type, text, text_hash, locator_json
            FROM source_chunks
            ORDER BY source_id
            """
        ).fetchall()
        assert [row["source_id"] for row in chunks] == [
            "asset:pdf-1",
            "job:video-1",
        ]
        assert len(chunks[0]["text_hash"]) == 64
        assert json.loads(chunks[0]["locator_json"]) == {
            "schema_version": 1,
            "kind": "pdf_page",
            "asset_id": "pdf-1",
            "page_number": 7,
            "metadata": {},
        }
        assert json.loads(chunks[1]["locator_json"]) == {
            "schema_version": 1,
            "kind": "video_time",
            "job_id": "video-1",
            "start_seconds": 12.5,
            "end_seconds": 42.0,
            "segment_ids": [3, 4],
            "metadata": {},
        }

        assert conn.execute(
            "SELECT COUNT(*) FROM source_chunk_embeddings"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT title FROM knowledge_cards WHERE id = 'card-1'"
        ).fetchone()[0] == "Gradient descent"
        assert conn.execute(
            "SELECT vector FROM card_embeddings WHERE card_id = 'card-1'"
        ).fetchone()[0] == b"legacy-vector"


def test_unified_source_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _create_legacy_database(db_path)

    with _connect(db_path) as conn:
        assert apply_migrations(
            conn,
            migrations=SOURCE_MIGRATIONS,
        ) == [latest_schema_version(SOURCE_MIGRATIONS)]
        first_counts = (
            conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM source_chunks").fetchone()[0],
        )

        assert apply_migrations(conn, migrations=SOURCE_MIGRATIONS) == []
        second_counts = (
            conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM source_chunks").fetchone()[0],
        )

        assert second_counts == first_counts == (2, 2)
        assert conn.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0] == 1


def test_migration_preserves_linked_video_asset_locator(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy.db"
    _create_legacy_database(db_path)
    with _connect(db_path) as conn:
        conn.execute("ALTER TABLE source_assets ADD COLUMN job_id TEXT")
        conn.execute(
            """
            INSERT INTO source_assets (
                id, course_id, job_id, asset_type, original_filename,
                stored_path, mime_type, size_bytes, sha256,
                extraction_status, metadata_json, error_message,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "frames-1",
                "course-a",
                "video-1",
                "video",
                "frames.json",
                str(tmp_path / "frames.json"),
                "application/json",
                42,
                "b" * 64,
                "ready",
                "{}",
                None,
                NOW,
                NOW,
            ),
        )
        conn.execute(
            """
            INSERT INTO source_units (
                id, asset_id, unit_type, ordinal, text, locator_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "frame-1",
                "frames-1",
                "video_frame",
                0,
                "A slide frame with an equation.",
                '{"start_seconds": 18.0, "end_seconds": 19.5}',
                NOW,
            ),
        )
        conn.execute(
            """
            INSERT INTO source_units (
                id, asset_id, unit_type, ordinal, text, locator_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "segment-1",
                "frames-1",
                "transcript_segment",
                1,
                "The instructor explains the equation.",
                '{"start_seconds": 20.0, "end_seconds": 24.0}',
                NOW,
            ),
        )

        apply_migrations(conn, migrations=SOURCE_MIGRATIONS)

        source_metadata = json.loads(
            conn.execute(
                "SELECT metadata_json FROM sources WHERE id = ?",
                ("asset:frames-1",),
            ).fetchone()[0]
        )
        locator = json.loads(
            conn.execute(
                """
                SELECT locator_json
                FROM source_chunks
                WHERE id = ?
                """,
                ("source_unit:frame-1",),
            ).fetchone()[0]
        )
        transcript_chunk = conn.execute(
            """
            SELECT chunk_type, locator_json
            FROM source_chunks
            WHERE id = ?
            """,
            ("source_unit:segment-1",),
        ).fetchone()

    assert source_metadata["job_id"] == "video-1"
    assert locator["kind"] == "video_time"
    assert locator["job_id"] == "video-1"
    assert locator["asset_id"] is None
    assert locator["start_seconds"] == 18.0
    assert locator["end_seconds"] == 19.5
    assert transcript_chunk["chunk_type"] == "transcript"
    assert json.loads(transcript_chunk["locator_json"])["kind"] == "video_time"


def test_prepare_migration_backup_is_consistent_and_only_created_when_needed(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy.db"
    _create_legacy_database(db_path)

    backup_path = prepare_migration_backup(db_path)

    assert backup_path is not None
    assert backup_path.parent == tmp_path / "backups"
    assert backup_path.is_file()
    with _connect(backup_path) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert "sources" not in _table_names(backup)

    with _connect(db_path) as conn:
        apply_migrations(conn)

    assert prepare_migration_backup(db_path) is None


def test_failed_migration_rolls_back_schema_data_and_version(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "failure.db"

    def create_then_fail(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE migration_partial_write (id INTEGER)")
        conn.execute("INSERT INTO migration_partial_write VALUES (1)")
        raise RuntimeError("injected migration failure")

    failing = Migration(
        version=1,
        name="injected_failure",
        apply=create_then_fail,
    )

    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(RuntimeError, match="injected migration failure"):
            apply_migrations(conn, migrations=(failing,))
        conn.rollback()

        assert "migration_partial_write" not in _table_names(conn)
        assert "schema_migrations" not in _table_names(conn)


def test_apply_migrations_rolls_back_its_own_failed_savepoint(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "failure.db"

    def create_then_fail(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE migration_partial_write (id INTEGER)")
        raise RuntimeError("injected migration failure")

    with _connect(db_path) as conn:
        with pytest.raises(RuntimeError, match="injected migration failure"):
            apply_migrations(
                conn,
                migrations=(
                    Migration(
                        version=1,
                        name="injected_failure",
                        apply=create_then_fail,
                    ),
                ),
            )

    with _connect(db_path) as conn:
        assert "migration_partial_write" not in _table_names(conn)
        assert "schema_migrations" not in _table_names(conn)


def test_migration_versions_are_strictly_increasing_and_unique() -> None:
    versions = [migration.version for migration in MIGRATIONS]

    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))
    assert all(version > 0 for version in versions)
