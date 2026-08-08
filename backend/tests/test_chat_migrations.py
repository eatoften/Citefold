from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from app.migrations import (
    MIGRATIONS,
    Migration,
    apply_migrations,
    latest_schema_version,
    prepare_migration_backup,
)
from app.db import get_db_path


NOW = "2026-07-27T00:00:00+00:00"
CHAT_TABLES = {
    "chat_conversations",
    "chat_turns",
    "chat_messages",
    "chat_citations",
    "chat_citation_spans",
}
NOTEBOOK_NOTE_TABLES = {
    "notebook_notes",
    "notebook_note_citations",
    "notebook_note_citation_spans",
    "notebook_note_source_snapshots",
}


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _create_v1_database(path: Path) -> None:
    with _connect(path) as conn:
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
                index_status TEXT NOT NULL DEFAULT 'not_indexed',
                index_generation TEXT,
                index_model TEXT,
                index_dimension INTEGER,
                enabled INTEGER NOT NULL DEFAULT 1,
                size_bytes INTEGER,
                mime_type TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                error_message TEXT,
                index_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                indexed_at TEXT,
                UNIQUE(origin_type, origin_id)
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
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(origin_type, origin_id)
            );

            CREATE TABLE source_chunk_embeddings (
                chunk_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                model TEXT NOT NULL,
                dimension INTEGER NOT NULL,
                text_hash TEXT NOT NULL,
                vector BLOB NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(chunk_id, model)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO schema_migrations (version, name, applied_at)
            VALUES (1, 'unified_source_index', ?)
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
                'job:video-1', 'course-a', 'video_job', 'video-1', 'video',
                'lecture.mp4', 'ready', 'ready', 1, '{}', ?, ?
            )
            """,
            (NOW, NOW),
        )
        conn.execute(
            """
            INSERT INTO source_chunks (
                id, source_id, origin_type, origin_id, chunk_type, ordinal,
                text, text_hash, locator_json, chunker_version, is_active,
                created_at, updated_at
                ) VALUES (
                    'transcript_chunk:chunk-1', 'job:video-1',
                    'transcript_chunk', 'chunk-1', 'transcript', 0,
                    'Gradient descent follows the negative gradient.', ?,
                    ?, 'semantic-v1', 1, ?, ?
                )
                """,
                (
                    hashlib.sha256(
                        b"Gradient descent follows the negative gradient."
                    ).hexdigest(),
                    json.dumps(
                        {
                            "schema_version": 1,
                            "kind": "video_time",
                            "job_id": "video-1",
                            "start_seconds": 1,
                            "end_seconds": 4,
                            "segment_ids": [],
                            "metadata": {},
                        }
                    ),
                    NOW,
                    NOW,
                ),
            )


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


def _insert_conversation(
    conn: sqlite3.Connection,
    *,
    conversation_id: str = "conversation-1",
    source_ids: list[str] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO chat_conversations (
            id, course_id, title, status, selected_source_ids_json,
            next_sequence, created_at, updated_at
        ) VALUES (?, 'course-a', 'Gradient descent', 'active', ?, 1, ?, ?)
        """,
        (
            conversation_id,
            json.dumps(
                ["job:video-1"] if source_ids is None else source_ids
            ),
            NOW,
            NOW,
        ),
    )


def _insert_turn(
    conn: sqlite3.Connection,
    *,
    turn_id: str,
    request_id: str,
    status: str,
    conversation_id: str = "conversation-1",
) -> None:
    conn.execute(
        """
        INSERT INTO chat_turns (
            id, conversation_id, client_request_id, user_message_id,
            assistant_message_id, status, source_ids_json, generation_token,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, '["job:video-1"]', ?, ?, ?)
        """,
        (
            turn_id,
            conversation_id,
            request_id,
            f"{turn_id}-user",
            f"{turn_id}-assistant",
            status,
            f"{turn_id}-generation",
            NOW,
            NOW,
        ),
    )


def _insert_message(
    conn: sqlite3.Connection,
    *,
    message_id: str,
    turn_id: str,
    sequence: int,
    role: str,
    status: str,
    answer_status: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO chat_messages (
            id, conversation_id, turn_id, sequence, role, content, status,
            answer_status, metadata_json, created_at, updated_at
        ) VALUES (?, 'conversation-1', ?, ?, ?, ?, ?, ?, '{}', ?, ?)
        """,
        (
            message_id,
            turn_id,
            sequence,
            role,
            "What is gradient descent?"
            if role == "user"
            else "It follows the negative gradient.",
            status,
            answer_status,
            NOW,
            NOW,
        ),
    )


def test_grounded_chat_migration_preserves_v1_data_and_has_no_fk_dependency(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "v1.db"
    _create_v1_database(db_path)

    with _connect(db_path) as conn:
        completed = apply_migrations(conn)

        assert completed == [2, 3, 4, 5, 6, 7, 8, 9, 10]
        assert latest_schema_version() == 10
        assert CHAT_TABLES.issubset(_table_names(conn))
        assert conn.execute(
            "SELECT title FROM sources WHERE id = 'job:video-1'"
        ).fetchone()[0] == "lecture.mp4"
        assert conn.execute(
            """
            SELECT text
            FROM source_chunks
            WHERE id = 'transcript_chunk:chunk-1'
            """
        ).fetchone()[0] == (
            "Gradient descent follows the negative gradient."
        )
        assert [
            tuple(row)
            for row in conn.execute(
                """
                SELECT version, name
                FROM schema_migrations
                ORDER BY version
                """
            ).fetchall()
        ] == [
            (1, "unified_source_index"),
            (2, "grounded_chat"),
            (3, "align_grounded_chat_turn_contract"),
            (4, "video_content_fingerprint"),
            (5, "local_workspace_lifecycle"),
            (6, "strengthen_trash_operation_states"),
            (7, "card_generation_chunk_ledger"),
            (8, "notebook_notes"),
            (9, "evidence_grounded_concept_graph"),
            (10, "source_projection_generation"),
        ]
        _insert_conversation(
            conn,
            conversation_id="conversation-empty-scope",
            source_ids=[],
        )
        empty_scope = conn.execute(
            """
            SELECT selected_source_ids_json
            FROM chat_conversations
            WHERE id = 'conversation-empty-scope'
            """
        ).fetchone()[0]
        assert json.loads(empty_scope) == []
        for table in CHAT_TABLES:
            assert conn.execute(
                f"PRAGMA foreign_key_list({table})"
            ).fetchall() == []


def test_v7_to_v8_notebook_migration_is_atomic_and_idempotent(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "v7.db"
    _create_v1_database(db_path)

    with _connect(db_path) as conn:
        assert apply_migrations(conn, migrations=MIGRATIONS[:7]) == [
            2,
            3,
            4,
            5,
            6,
            7,
        ]
        assert NOTEBOOK_NOTE_TABLES.isdisjoint(_table_names(conn))
        assert apply_migrations(conn, migrations=MIGRATIONS[:8]) == [8]
        assert NOTEBOOK_NOTE_TABLES.issubset(_table_names(conn))
        assert apply_migrations(conn, migrations=MIGRATIONS[:8]) == []


def test_v8_migration_failure_rolls_back_schema_and_version(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "v7-failure.db"
    _create_v1_database(db_path)

    def fail_after_creating_table(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE migration_should_rollback (id TEXT)")
        raise RuntimeError("injected migration failure")

    failing_migrations = (
        *MIGRATIONS[:7],
        Migration(
            version=8,
            name="injected_failure",
            apply=fail_after_creating_table,
        ),
    )
    with _connect(db_path) as conn:
        assert apply_migrations(conn, migrations=MIGRATIONS[:7]) == [
            2,
            3,
            4,
            5,
            6,
            7,
        ]
        with pytest.raises(RuntimeError, match="injected migration failure"):
            apply_migrations(conn, migrations=failing_migrations)
        assert NOTEBOOK_NOTE_TABLES.isdisjoint(_table_names(conn))
        assert "migration_should_rollback" not in _table_names(conn)
        assert conn.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0] == 7
        assert apply_migrations(conn, migrations=MIGRATIONS[:8]) == [8]


def test_clean_install_enforces_v8_notebook_sqlite_contract() -> None:
    with _connect(get_db_path()) as conn:
        assert NOTEBOOK_NOTE_TABLES.issubset(_table_names(conn))
        for table in NOTEBOOK_NOTE_TABLES:
            assert conn.execute(
                f"PRAGMA foreign_key_list({table})"
            ).fetchall() == []

        conn.execute(
            """
            INSERT INTO notebook_notes (
                id, course_id, title, body_markdown, revision, origin_type,
                origin_message_id, origin_conversation_id,
                origin_snapshot_json, created_at, updated_at, deleted_at
            ) VALUES (
                'free-note', 'course-a', 'Free', 'Body', 1, 'free',
                NULL, NULL, '{"origin_type":"free"}', ?, ?, NULL
            )
            """,
            (NOW, NOW),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO notebook_notes (
                    id, course_id, title, body_markdown, revision, origin_type,
                    origin_message_id, origin_conversation_id,
                    origin_snapshot_json, created_at, updated_at
                ) VALUES (
                    'revision-zero', 'course-a', 'Invalid', 'Body', 0, 'free',
                    NULL, NULL, '{"origin_type":"free"}', ?, ?
                )
                """,
                (NOW, NOW),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO notebook_notes (
                    id, course_id, title, body_markdown, revision, origin_type,
                    origin_message_id, origin_conversation_id,
                    origin_snapshot_json, created_at, updated_at
                ) VALUES (
                    'bad-origin', 'course-a', 'Invalid', 'Body', 1,
                    'chat_answer', NULL, NULL,
                    '{"origin_type":"chat_answer"}', ?, ?
                )
                """,
                (NOW, NOW),
            )

        for note_id in ("chat-note-one", "chat-note-two"):
            statement = """
                INSERT INTO notebook_notes (
                    id, course_id, title, body_markdown, revision, origin_type,
                    origin_message_id, origin_conversation_id,
                    origin_snapshot_json, created_at, updated_at
                ) VALUES (
                    ?, 'course-a', 'Chat', 'Answer', 1, 'chat_answer',
                    'assistant-message', 'conversation',
                    '{"origin_type":"chat_answer"}', ?, ?
                )
            """
            if note_id == "chat-note-one":
                conn.execute(statement, (note_id, NOW, NOW))
            else:
                with pytest.raises(sqlite3.IntegrityError):
                    conn.execute(statement, (note_id, NOW, NOW))

        citation_values = (
            "citation-one",
            "chat-note-one",
            1,
            "origin-citation-one",
            1,
            "source",
            "chunk",
            "hash",
            "Source",
            "text",
            "quote",
            0.9,
            "{}",
            NOW,
        )
        conn.execute(
            """
            INSERT INTO notebook_note_citations (
                id, note_id, note_revision, origin_citation_id, ordinal,
                source_id, chunk_id, chunk_text_hash, source_title,
                source_type, quote, score, locator_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            citation_values,
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO notebook_note_citations (
                    id, note_id, note_revision, origin_citation_id, ordinal,
                    source_id, chunk_id, chunk_text_hash, source_title,
                    source_type, quote, score, locator_json, created_at
                ) VALUES (
                    'ordinal-zero', 'chat-note-one', 1, 'origin-zero', 0,
                    'source', 'chunk', 'hash', 'Source', 'text',
                    'quote', 0.9, '{}', ?
                )
                """,
                (NOW,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO notebook_note_citation_spans (
                    id, note_id, citation_id, sentence_index,
                    start_offset, end_offset, created_at
                ) VALUES (
                    'invalid-span', 'chat-note-one', 'citation-one',
                    0, 3, 3, ?
                )
                """,
                (NOW,),
            )


def test_grounded_chat_migration_is_idempotent_and_backed_up(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "v1.db"
    _create_v1_database(db_path)

    backup_path = prepare_migration_backup(db_path)

    assert backup_path is not None
    assert ".pre-migration-v10-" in backup_path.name
    with _connect(backup_path) as backup:
        assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert CHAT_TABLES.isdisjoint(_table_names(backup))
        assert backup.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1

    with _connect(db_path) as conn:
        assert apply_migrations(conn) == [2, 3, 4, 5, 6, 7, 8, 9, 10]
        _insert_conversation(conn)
        assert apply_migrations(conn) == []
        assert conn.execute(
            "SELECT COUNT(*) FROM chat_conversations"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0] == len(MIGRATIONS)

    assert prepare_migration_backup(db_path) is None


def test_migration_backup_releases_windows_file_handles(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "v1-lock-check.db"
    _create_v1_database(db_path)

    backup_path = prepare_migration_backup(db_path)

    assert backup_path is not None
    with closing(_connect(backup_path)) as backup:
        assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"

    renamed_backup = backup_path.with_name(
        f"{backup_path.stem}.renamed{backup_path.suffix}"
    )
    backup_path.rename(renamed_backup)
    assert renamed_backup.is_file()
    renamed_backup.unlink()
    assert not renamed_backup.exists()


def test_v3_aligns_an_already_applied_v2_without_losing_turns(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "v2.db"
    _create_v1_database(db_path)

    with _connect(db_path) as conn:
        assert apply_migrations(
            conn,
            migrations=MIGRATIONS[:2],
        ) == [2]
        _insert_conversation(conn)
        _insert_turn(
            conn,
            turn_id="turn-v2-abstained",
            request_id="request-v2-abstained",
            status="abstained",
        )

        assert apply_migrations(conn) == [3, 4, 5, 6, 7, 8, 9, 10]

        turn = conn.execute(
            """
            SELECT id, status, source_scope_mode, source_ids_json
            FROM chat_turns
            WHERE id = 'turn-v2-abstained'
            """
        ).fetchone()
        assert tuple(turn) == (
            "turn-v2-abstained",
            "refused",
            "explicit",
            '["job:video-1"]',
        )
        table_sql = conn.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'table' AND name = 'chat_turns'
            """
        ).fetchone()[0]
        assert "'abstained'" not in table_sql
        assert {
            row[0]
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'index' AND tbl_name = 'chat_turns'
                """
            ).fetchall()
        }.issuperset(
            {
                "idx_chat_turns_conversation_created",
                "idx_chat_turns_one_active_per_conversation",
            }
        )


def test_chat_turns_enforce_idempotency_and_one_active_turn(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "v1.db"
    _create_v1_database(db_path)

    with _connect(db_path) as conn:
        apply_migrations(conn)
        _insert_conversation(conn)
        _insert_turn(
            conn,
            turn_id="turn-1",
            request_id="request-1",
            status="pending",
        )

        with pytest.raises(
            sqlite3.IntegrityError,
            match="chat_turns.conversation_id, chat_turns.client_request_id",
        ):
            _insert_turn(
                conn,
                turn_id="turn-duplicate-request",
                request_id="request-1",
                status="failed",
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_turn(
                conn,
                turn_id="turn-second-active",
                request_id="request-2",
                status="generating",
            )

        conn.execute(
            """
            UPDATE chat_turns
            SET status = 'completed', completed_at = ?, updated_at = ?
            WHERE id = 'turn-1'
            """,
            (NOW, NOW),
        )
        _insert_turn(
            conn,
            turn_id="turn-refused",
            request_id="request-3",
            status="refused",
        )
        _insert_turn(
            conn,
            turn_id="turn-new-active",
            request_id="request-4",
            status="validating",
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_turn(
                conn,
                turn_id="turn-third-active",
                request_id="request-5",
                status="retrieving",
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_turn(
                conn,
                turn_id="turn-status-drift",
                request_id="request-6",
                status="abstained",
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                UPDATE chat_turns
                SET source_scope_mode = 'unknown'
                WHERE id = 'turn-refused'
                """
            )

        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM chat_turns
            WHERE conversation_id = 'conversation-1'
            """
        ).fetchone()[0] == 3


def test_chat_messages_enforce_sequence_turn_role_and_state_values(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "v1.db"
    _create_v1_database(db_path)

    with _connect(db_path) as conn:
        apply_migrations(conn)
        _insert_conversation(conn)
        _insert_turn(
            conn,
            turn_id="turn-1",
            request_id="request-1",
            status="generating",
        )
        _insert_message(
            conn,
            message_id="message-user",
            turn_id="turn-1",
            sequence=1,
            role="user",
            status="complete",
        )
        _insert_message(
            conn,
            message_id="message-assistant",
            turn_id="turn-1",
            sequence=2,
            role="assistant",
            status="generating",
        )

        with pytest.raises(sqlite3.IntegrityError):
            _insert_message(
                conn,
                message_id="message-duplicate-sequence",
                turn_id="turn-2",
                sequence=2,
                role="assistant",
                status="failed",
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_message(
                conn,
                message_id="message-duplicate-role",
                turn_id="turn-1",
                sequence=3,
                role="assistant",
                status="failed",
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_message(
                conn,
                message_id="message-invalid-answer",
                turn_id="turn-3",
                sequence=3,
                role="assistant",
                status="complete",
                answer_status="unsupported",
            )


def test_chat_citations_store_evidence_snapshots_and_valid_spans(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "v1.db"
    _create_v1_database(db_path)

    with _connect(db_path) as conn:
        apply_migrations(conn)
        _insert_conversation(conn)
        _insert_turn(
            conn,
            turn_id="turn-1",
            request_id="request-1",
            status="completed",
        )
        _insert_message(
            conn,
            message_id="message-assistant",
            turn_id="turn-1",
            sequence=2,
            role="assistant",
            status="complete",
            answer_status="answered",
        )
        conn.execute(
            """
            INSERT INTO chat_citations (
                id, message_id, ordinal, source_id, chunk_id, chunk_text_hash,
                source_title, source_type, quote, score, locator_json,
                created_at
            ) VALUES (
                'citation-1', 'message-assistant', 1, 'job:video-1',
                'transcript_chunk:chunk-1', ?, 'lecture.mp4', 'video',
                'follows the negative gradient', 0.82, ?, ?
            )
            """,
            (
                "a" * 64,
                json.dumps(
                    {
                        "schema_version": 1,
                        "kind": "video_time",
                        "job_id": "video-1",
                        "start_seconds": 1,
                        "end_seconds": 4,
                        "segment_ids": [],
                        "metadata": {},
                    }
                ),
                NOW,
            ),
        )
        conn.execute(
            """
            INSERT INTO chat_citation_spans (
                id, message_id, citation_id, sentence_index, start_offset,
                end_offset, created_at
            ) VALUES (
                'span-1', 'message-assistant', 'citation-1', 0, 0, 33, ?
            )
            """,
            (NOW,),
        )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO chat_citations (
                    id, message_id, ordinal, source_id, chunk_id,
                    chunk_text_hash, source_title, source_type, quote, score,
                    locator_json, created_at
                ) VALUES (
                    'citation-same-ordinal', 'message-assistant', 1,
                    'asset:pdf-1', 'source_unit:page-1', ?, 'notes.pdf', 'pdf',
                    'another quote', 0.7, '{}', ?
                )
                """,
                ("b" * 64, NOW),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO chat_citation_spans (
                    id, message_id, citation_id, sentence_index, start_offset,
                    end_offset, created_at
                ) VALUES (
                    'span-invalid', 'message-assistant', 'citation-1',
                    0, 10, 10, ?
                )
                """,
                (NOW,),
            )

        conn.execute(
            "DELETE FROM source_chunks WHERE source_id = 'job:video-1'"
        )
        conn.execute("DELETE FROM sources WHERE id = 'job:video-1'")
        citation = conn.execute(
            """
            SELECT source_title, quote, locator_json
            FROM chat_citations
            WHERE id = 'citation-1'
            """
        ).fetchone()
        span = conn.execute(
            """
            SELECT sentence_index, start_offset, end_offset
            FROM chat_citation_spans
            WHERE id = 'span-1'
            """
        ).fetchone()

        assert tuple(citation[:2]) == (
            "lecture.mp4",
            "follows the negative gradient",
        )
        assert json.loads(citation["locator_json"])["kind"] == "video_time"
        assert tuple(span) == (0, 0, 33)
