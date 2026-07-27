import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .course import DEFAULT_COURSE_ID, DEFAULT_COURSE_TITLE
from .migrations import apply_migrations, prepare_migration_backup
from .settings import get_app_path_settings

DEFAULT_DB_PATH = get_app_path_settings().db_path

_db_path = DEFAULT_DB_PATH
_initialized_paths: set[Path] = set()

JOB_COLUMNS: dict[str, str] = {
    "course_id": "TEXT",
    "original_filename": "TEXT",
    "stored_name": "TEXT",
    "size_bytes": "INTEGER",
    "created_at": "TEXT",
    "updated_at": "TEXT",
    "started_at": "TEXT",
    "completed_at": "TEXT",
}

KNOWLEDGE_CARD_CORE_COLUMNS = {
    "id",
    "job_id",
    "title",
    "summary",
    "key_points",
    "claims",
    "unsupported_terms",
    "card_kind",
    "source_start_seconds",
    "source_end_seconds",
    "provider",
    "model",
    "created_at",
    "updated_at",
    "tags",
    "content_status",
}


def _create_knowledge_cards_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_cards (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            card_kind TEXT NOT NULL DEFAULT 'concept',
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            key_points TEXT NOT NULL,
            claims TEXT NOT NULL,
            unsupported_terms TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '[]',
            content_status TEXT NOT NULL DEFAULT 'draft',
            source_start_seconds REAL NOT NULL,
            source_end_seconds REAL NOT NULL,
            provider TEXT,
            model TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def _create_review_items_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS review_items (
            id TEXT PRIMARY KEY,
            card_id TEXT NOT NULL,
            item_type TEXT NOT NULL,
            prompt TEXT NOT NULL,
            expected_answer TEXT NOT NULL,
            source_claim_ids TEXT NOT NULL DEFAULT '[]',
            source TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_review_items_card_id
        ON review_items (card_id)
        """
    )


def _create_review_schedule_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS review_progress (
            review_item_id TEXT PRIMARY KEY,
            fsrs_card_id INTEGER NOT NULL,
            fsrs_state INTEGER NOT NULL,
            step INTEGER,
            due_at TEXT NOT NULL,
            stability REAL,
            fsrs_difficulty REAL,
            last_reviewed_at TEXT,
            review_count INTEGER NOT NULL,
            lapse_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_review_progress_due_at
        ON review_progress (due_at)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS review_events (
            id TEXT PRIMARY KEY,
            review_item_id TEXT NOT NULL,
            rating TEXT NOT NULL,
            reviewed_at TEXT NOT NULL,
            response_time_ms INTEGER,
            previous_phase TEXT NOT NULL,
            next_phase TEXT NOT NULL,
            due_before TEXT NOT NULL,
            due_after TEXT NOT NULL,
            scheduled_days REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_review_events_item_time
        ON review_events (review_item_id, reviewed_at)
        """
    )
def _migrate_knowledge_cards_to_v2(
    conn: sqlite3.Connection,
    existing_columns: set[str],
) -> None:
    required_legacy_columns = {
        "id",
        "job_id",
        "title",
        "summary",
        "key_points",
        "claims",
        "unsupported_terms",
        "source_start_seconds",
        "source_end_seconds",
        "created_at",
        "updated_at",
    }

    if not required_legacy_columns.issubset(existing_columns):
        conn.execute("DROP TABLE knowledge_cards")
        _create_knowledge_cards_table(conn)
        return

    conn.execute("DROP INDEX IF EXISTS idx_knowledge_cards_job_id")
    conn.execute("ALTER TABLE knowledge_cards RENAME TO knowledge_cards_legacy")
    _create_knowledge_cards_table(conn)

    card_kind_expr = "card_kind" if "card_kind" in existing_columns else "'concept'"
    tags_expr = "tags" if "tags" in existing_columns else "'[]'"
    provider_expr = "provider" if "provider" in existing_columns else "NULL"
    model_expr = "model" if "model" in existing_columns else "NULL"
    if "content_status" in existing_columns:
        content_status_expr = "content_status"
    elif "review_state" in existing_columns:
        content_status_expr = "review_state"
    else:
        content_status_expr = "'draft'"

    conn.execute(
        f"""
        INSERT INTO knowledge_cards (
            id, job_id, card_kind, title, summary, key_points, claims,
            unsupported_terms, tags, content_status, source_start_seconds,
            source_end_seconds, provider, model, created_at, updated_at
        )
        SELECT
            id, job_id, {card_kind_expr}, title, summary, key_points, claims,
            unsupported_terms, {tags_expr}, {content_status_expr},
            source_start_seconds, source_end_seconds, {provider_expr},
            {model_expr},
            created_at, updated_at
        FROM knowledge_cards_legacy
        """
    )

    if {"question", "answer"}.issubset(existing_columns):
        conn.execute(
            """
            INSERT INTO review_items (
                id, card_id, item_type, prompt, expected_answer,
                source_claim_ids, source, status, created_at, updated_at
            )
            SELECT
                lower(hex(randomblob(16))), id, 'basic', trim(question),
                trim(answer), '[]', 'generated', 'active', created_at,
                updated_at
            FROM knowledge_cards_legacy
            WHERE question IS NOT NULL AND trim(question) != ''
              AND answer IS NOT NULL AND trim(answer) != ''
            """
        )

    conn.execute("DROP TABLE knowledge_cards_legacy")


def _ensure_claim_and_evidence_ids(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT id, claims FROM knowledge_cards").fetchall()

    for row in rows:
        try:
            claims = json.loads(row["claims"])
        except (TypeError, json.JSONDecodeError):
            continue

        if not isinstance(claims, list):
            continue

        changed = False
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            if not str(claim.get("id", "")).strip():
                claim["id"] = uuid4().hex
                changed = True
            evidence_items = claim.get("evidence")
            if not isinstance(evidence_items, list):
                continue
            for evidence in evidence_items:
                if not isinstance(evidence, dict):
                    continue
                if not str(evidence.get("id", "")).strip():
                    evidence["id"] = uuid4().hex
                    changed = True

        if changed:
            conn.execute(
                "UPDATE knowledge_cards SET claims = ? WHERE id = ?",
                (json.dumps(claims, ensure_ascii=False), row["id"]),
            )


def configure_db(db_path: Path) -> None:
    global _db_path

    _db_path = db_path


def get_db_path() -> Path:
    return _db_path


def get_conn() -> sqlite3.Connection:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = get_conn()

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    db_path = get_db_path()
    prepare_migration_backup(db_path)

    with connect() as conn:
        # WAL lets short autosave and task-progress writes coexist with reads
        # without turning a long local operation into a global UI lock.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS courses (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            INSERT INTO courses (
                id,
                title,
                description,
                created_at,
                updated_at
            ) VALUES (
                ?,
                ?,
                NULL,
                datetime('now'),
                datetime('now')
            )
            ON CONFLICT(id) DO NOTHING
            """,
            (
                DEFAULT_COURSE_ID,
                DEFAULT_COURSE_TITLE,
            ),
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                course_id TEXT,
                video_path TEXT NOT NULL,
                status TEXT NOT NULL,
                original_filename TEXT,
                stored_name TEXT,
                size_bytes INTEGER,
                metadata TEXT,
                transcript_path TEXT,
                error_message TEXT,
                created_at TEXT,
                updated_at TEXT,
                started_at TEXT,
                completed_at TEXT
            )
            """
        )

        existing_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(jobs)")
        }

        for column_name, column_type in JOB_COLUMNS.items():
            if column_name not in existing_columns:
                conn.execute(
                    f"ALTER TABLE jobs ADD COLUMN {column_name} {column_type}"
                )

        conn.execute(
            """
            UPDATE jobs
            SET course_id = ?
            WHERE course_id IS NULL OR trim(course_id) = ''
            """,
            (DEFAULT_COURSE_ID,),
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_jobs_course_id
            ON jobs (course_id)
            """
        )

        _create_review_items_table(conn)
        _create_review_schedule_tables(conn)

        existing_card_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(knowledge_cards)")
        }

        if existing_card_columns and not KNOWLEDGE_CARD_CORE_COLUMNS.issubset(
            existing_card_columns
        ):
            _migrate_knowledge_cards_to_v2(conn, existing_card_columns)
        else:
            _create_knowledge_cards_table(conn)

        conn.execute(
            """
            UPDATE knowledge_cards
            SET tags = '[]'
            WHERE tags IS NULL OR trim(tags) = ''
            """
        )

        conn.execute(
            """
            UPDATE knowledge_cards
            SET content_status = 'draft'
            WHERE content_status IS NULL OR trim(content_status) = ''
            """
        )

        _ensure_claim_and_evidence_ids(conn)

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_knowledge_cards_job_id
            ON knowledge_cards (job_id)
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_card_notes (
                id TEXT PRIMARY KEY,
                card_id TEXT NOT NULL,
                note_type TEXT NOT NULL,
                title TEXT,
                body TEXT NOT NULL,
                source TEXT NOT NULL,
                sources_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_knowledge_card_notes_card_id
            ON knowledge_card_notes (card_id)
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS card_embeddings (
                card_id TEXT PRIMARY KEY,
                model TEXT NOT NULL,
                dimension INTEGER NOT NULL,
                text_hash TEXT NOT NULL,
                vector BLOB NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS card_relations (
                id TEXT PRIMARY KEY,
                course_id TEXT NOT NULL,
                source_card_id TEXT NOT NULL,
                target_card_id TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                score REAL NOT NULL,
                method TEXT NOT NULL,
                model TEXT,
                explanation TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS topics (
                id TEXT PRIMARY KEY,
                course_id TEXT NOT NULL,
                parent_topic_id TEXT,
                title TEXT NOT NULL,
                summary TEXT,
                position INTEGER NOT NULL,
                depth INTEGER NOT NULL,
                method TEXT NOT NULL,
                status TEXT NOT NULL,
                is_system INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_topics_course_parent
            ON topics (course_id, parent_topic_id, position)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS topic_card_memberships (
                id TEXT PRIMARY KEY,
                topic_id TEXT NOT NULL,
                card_id TEXT NOT NULL,
                role TEXT NOT NULL,
                position INTEGER NOT NULL,
                method TEXT NOT NULL,
                confidence REAL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_topic_card_unique
            ON topic_card_memberships (topic_id, card_id)
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_topic_card_primary
            ON topic_card_memberships (card_id)
            WHERE role = 'primary' AND status = 'accepted'
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS topic_relations (
                id TEXT PRIMARY KEY,
                course_id TEXT NOT NULL,
                source_topic_id TEXT NOT NULL,
                target_topic_id TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                explanation TEXT,
                method TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_topic_relation_unique
            ON topic_relations (
                source_topic_id, target_topic_id, relation_type, method
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_topic_relations_course
            ON topic_relations (course_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_card_relations_course_id
            ON card_relations (course_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_card_relations_source_card_id
            ON card_relations (source_card_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_card_relations_target_card_id
            ON card_relations (target_card_id)
            """
        )

        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                idx_card_relations_unique_pair_type
            ON card_relations (
                source_card_id,
                target_card_id,
                relation_type,
                method
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS source_assets (
                id TEXT PRIMARY KEY,
                course_id TEXT NOT NULL,
                job_id TEXT,
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
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_source_assets_course
            ON source_assets (course_id, created_at)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS source_units (
                id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL,
                unit_type TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                text TEXT NOT NULL,
                locator_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_source_units_asset_ordinal
            ON source_units (asset_id, ordinal)
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_documents (
                id TEXT PRIMARY KEY,
                course_id TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                body_markdown TEXT NOT NULL,
                status TEXT NOT NULL,
                generation_mode TEXT NOT NULL,
                provider TEXT,
                model TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_learning_documents_course
            ON learning_documents (course_id, updated_at)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_document_cards (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                card_id TEXT NOT NULL,
                role TEXT NOT NULL,
                position INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_learning_document_card_unique
            ON learning_document_cards (document_id, card_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_learning_document_cards_card
            ON learning_document_cards (card_id, document_id)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_document_sources (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                card_id TEXT,
                label TEXT NOT NULL,
                quote TEXT NOT NULL,
                locator_json TEXT NOT NULL,
                position INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_learning_document_sources_document
            ON learning_document_sources (document_id, position)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_document_versions (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                version_number INTEGER NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                body_markdown TEXT NOT NULL,
                change_source TEXT NOT NULL,
                provider TEXT,
                model TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_learning_document_version
            ON learning_document_versions (document_id, version_number)
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transcript_chunks (
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
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_transcript_chunks_job_id
            ON transcript_chunks (job_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_transcript_chunks_course_id
            ON transcript_chunks (course_id)
            """
        )

        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                idx_transcript_chunks_job_chunk_index
            ON transcript_chunks (job_id, chunk_index)
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS card_generation_runs (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                model TEXT,
                card_count_per_chunk INTEGER NOT NULL,
                total_chunks INTEGER NOT NULL,
                completed_chunks INTEGER NOT NULL,
                succeeded_chunks INTEGER NOT NULL,
                failed_chunks INTEGER NOT NULL,
                cards_created INTEGER NOT NULL,
                error_message TEXT,
                errors_json TEXT NOT NULL,
                request_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_card_generation_runs_job_id
            ON card_generation_runs (job_id)
            """
        )

        apply_migrations(conn)

    _initialized_paths.add(db_path)


def ensure_db() -> None:
    if get_db_path() not in _initialized_paths:
        init_db()
