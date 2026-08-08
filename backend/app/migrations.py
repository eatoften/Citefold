from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .source_projection_identity import (
    ProjectionManifestChunk,
    build_projection_manifest_hash,
    select_projection_generation_id,
)


MigrationCallable = Callable[[sqlite3.Connection], None]


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: MigrationCallable


def _create_unified_source_index(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
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
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_sources_course
        ON sources (course_id, updated_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_sources_index_status
        ON sources (index_status)
        """
    )
    conn.execute(
        """
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
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_source_chunks_source
        ON source_chunks (source_id, is_active, ordinal)
        """
    )
    conn.execute(
        """
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
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_source_chunk_embeddings_source
        ON source_chunk_embeddings (source_id, model)
        """
    )
    _backfill_sources(conn)
    _backfill_source_chunks(conn)
    _validate_source_backfill(conn)


def _create_grounded_chat(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE chat_conversations (
            id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            selected_source_ids_json TEXT NOT NULL DEFAULT '[]',
            next_sequence INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (status IN ('active', 'archived')),
            CHECK (next_sequence >= 1)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_chat_conversations_course_updated
        ON chat_conversations (course_id, updated_at DESC)
        """
    )
    conn.execute(
        """
        CREATE TABLE chat_turns (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            client_request_id TEXT NOT NULL,
            user_message_id TEXT NOT NULL UNIQUE,
            assistant_message_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            source_ids_json TEXT NOT NULL,
            retrieval_query TEXT,
            provider TEXT,
            model TEXT,
            generation_token TEXT,
            refusal_reason TEXT,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            UNIQUE (conversation_id, client_request_id),
            CHECK (
                status IN (
                    'pending',
                    'retrieving',
                    'generating',
                    'validating',
                    'completed',
                    'refused',
                    'abstained',
                    'failed'
                )
            )
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_chat_turns_conversation_created
        ON chat_turns (conversation_id, created_at)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX idx_chat_turns_one_active_per_conversation
        ON chat_turns (conversation_id)
        WHERE status IN (
            'pending',
            'retrieving',
            'generating',
            'validating'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE chat_messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            turn_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            status TEXT NOT NULL,
            answer_status TEXT,
            reply_to_message_id TEXT,
            error_message TEXT,
            provider TEXT,
            model TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (conversation_id, sequence),
            UNIQUE (turn_id, role),
            CHECK (sequence >= 1),
            CHECK (role IN ('user', 'assistant')),
            CHECK (status IN ('generating', 'complete', 'failed')),
            CHECK (
                answer_status IS NULL
                OR answer_status IN ('answered', 'abstained')
            )
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_chat_messages_conversation_sequence
        ON chat_messages (conversation_id, sequence)
        """
    )
    conn.execute(
        """
        CREATE TABLE chat_citations (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            source_id TEXT NOT NULL,
            chunk_id TEXT NOT NULL,
            chunk_text_hash TEXT NOT NULL,
            source_title TEXT NOT NULL,
            source_type TEXT NOT NULL,
            quote TEXT NOT NULL,
            score REAL NOT NULL,
            locator_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (message_id, ordinal),
            UNIQUE (message_id, chunk_id),
            CHECK (ordinal >= 1),
            CHECK (score >= -1.0 AND score <= 1.0)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_chat_citations_message
        ON chat_citations (message_id, ordinal)
        """
    )
    conn.execute(
        """
        CREATE TABLE chat_citation_spans (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            citation_id TEXT NOT NULL,
            sentence_index INTEGER NOT NULL,
            start_offset INTEGER NOT NULL,
            end_offset INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (
                message_id,
                citation_id,
                sentence_index,
                start_offset,
                end_offset
            ),
            CHECK (sentence_index >= 0),
            CHECK (start_offset >= 0),
            CHECK (end_offset > start_offset)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_chat_citation_spans_message_sentence
        ON chat_citation_spans (message_id, sentence_index, start_offset)
        """
    )


def _align_grounded_chat_turn_contract(conn: sqlite3.Connection) -> None:
    """Align already-applied v2 databases with the runtime turn contract."""

    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(chat_turns)").fetchall()
    }
    source_scope_expression = (
        (
            "CASE WHEN source_scope_mode IN ('conversation', 'explicit') "
            "THEN source_scope_mode ELSE 'explicit' END"
        )
        if "source_scope_mode" in columns
        else "'explicit'"
    )
    conn.execute("DROP INDEX IF EXISTS idx_chat_turns_conversation_created")
    conn.execute(
        "DROP INDEX IF EXISTS idx_chat_turns_one_active_per_conversation"
    )
    conn.execute(
        "ALTER TABLE chat_turns RENAME TO chat_turns_v2_contract"
    )
    conn.execute(
        """
        CREATE TABLE chat_turns (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            client_request_id TEXT NOT NULL,
            user_message_id TEXT NOT NULL UNIQUE,
            assistant_message_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            source_ids_json TEXT NOT NULL,
            source_scope_mode TEXT NOT NULL DEFAULT 'explicit',
            retrieval_query TEXT,
            provider TEXT,
            model TEXT,
            generation_token TEXT,
            refusal_reason TEXT,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            UNIQUE (conversation_id, client_request_id),
            CHECK (
                status IN (
                    'pending',
                    'retrieving',
                    'generating',
                    'validating',
                    'completed',
                    'refused',
                    'failed'
                )
            ),
            CHECK (source_scope_mode IN ('conversation', 'explicit'))
        )
        """
    )
    conn.execute(
        f"""
        INSERT INTO chat_turns (
            id, conversation_id, client_request_id, user_message_id,
            assistant_message_id, status, source_ids_json, source_scope_mode,
            retrieval_query, provider, model, generation_token,
            refusal_reason, error_code, error_message, created_at, updated_at,
            started_at, completed_at
        )
        SELECT
            id, conversation_id, client_request_id, user_message_id,
            assistant_message_id,
            CASE WHEN status = 'abstained' THEN 'refused' ELSE status END,
            source_ids_json, {source_scope_expression}, retrieval_query,
            provider, model, generation_token, refusal_reason, error_code,
            error_message, created_at, updated_at, started_at, completed_at
        FROM chat_turns_v2_contract
        """
    )
    conn.execute("DROP TABLE chat_turns_v2_contract")
    conn.execute(
        """
        CREATE INDEX idx_chat_turns_conversation_created
        ON chat_turns (conversation_id, created_at)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX idx_chat_turns_one_active_per_conversation
        ON chat_turns (conversation_id)
        WHERE status IN (
            'pending',
            'retrieving',
            'generating',
            'validating'
        )
        """
    )


def _add_video_content_fingerprint(conn: sqlite3.Connection) -> None:
    jobs_table = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'jobs'
        """
    ).fetchone()
    if jobs_table is None:
        return
    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
    }
    if "video_sha256" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN video_sha256 TEXT")


def _add_local_workspace_lifecycle(conn: sqlite3.Connection) -> None:
    """Add durable drafts, tasks, and recoverable deletion metadata."""

    for table_name in (
        "courses",
        "jobs",
        "source_assets",
        "knowledge_cards",
        "learning_documents",
        "chat_conversations",
    ):
        table = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = ?
            """,
            (table_name,),
        ).fetchone()
        if table is None:
            continue
        columns = {
            str(row["name"])
            for row in conn.execute(
                f"PRAGMA table_info({table_name})"
            ).fetchall()
        }
        if "deleted_at" not in columns:
            conn.execute(
                f"ALTER TABLE {table_name} ADD COLUMN deleted_at TEXT"
            )

    conn.execute(
        """
        CREATE TABLE workspace_drafts (
            id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            draft_type TEXT NOT NULL,
            entity_id TEXT,
            payload_json TEXT NOT NULL,
            revision INTEGER NOT NULL,
            base_updated_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (revision >= 1)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_workspace_drafts_course_updated
        ON workspace_drafts (course_id, updated_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_workspace_drafts_entity
        ON workspace_drafts (draft_type, entity_id)
        """
    )

    # The task schema is isolated from the runtime store so migration imports
    # do not create a db -> migrations -> store -> db cycle.
    from .reliable_task_schema import create_reliable_task_tables

    create_reliable_task_tables(conn)

    conn.execute(
        """
        CREATE TABLE trash_items (
            id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            course_id TEXT,
            display_name TEXT NOT NULL,
            deleted_at TEXT NOT NULL,
            purge_after TEXT,
            status TEXT NOT NULL DEFAULT 'trashed',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            restored_at TEXT,
            UNIQUE(entity_type, entity_id),
            CHECK (status IN ('trashed', 'restoring', 'purging', 'failed'))
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_trash_items_deleted
        ON trash_items (deleted_at DESC, id)
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_trash_items_course
        ON trash_items (course_id, deleted_at DESC)
        """
    )


def _strengthen_trash_operation_states(conn: sqlite3.Connection) -> None:
    """Separate recoverable restore failures from irreversible purge failures."""

    table = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'trash_items'
        """
    ).fetchone()
    if table is None:
        return

    conn.execute("ALTER TABLE trash_items RENAME TO trash_items_legacy")
    conn.execute(
        """
        CREATE TABLE trash_items (
            id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            course_id TEXT,
            display_name TEXT NOT NULL,
            deleted_at TEXT NOT NULL,
            purge_after TEXT,
            status TEXT NOT NULL DEFAULT 'trashed',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            restored_at TEXT,
            UNIQUE(entity_type, entity_id),
            CHECK (
                status IN (
                    'trashed',
                    'restoring',
                    'restore_failed',
                    'purging',
                    'purge_failed'
                )
            )
        )
        """
    )
    conn.execute(
        """
        INSERT INTO trash_items (
            id, entity_type, entity_id, course_id, display_name,
            deleted_at, purge_after, status, metadata_json, restored_at
        )
        SELECT
            id, entity_type, entity_id, course_id, display_name,
            deleted_at, purge_after,
            CASE status
                WHEN 'restoring' THEN 'restore_failed'
                WHEN 'purging' THEN 'purge_failed'
                WHEN 'failed' THEN 'purge_failed'
                ELSE status
            END,
            metadata_json, restored_at
        FROM trash_items_legacy
        """
    )
    conn.execute("DROP TABLE trash_items_legacy")
    conn.execute(
        """
        CREATE INDEX idx_trash_items_deleted
        ON trash_items (deleted_at DESC, id)
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_trash_items_course
        ON trash_items (course_id, deleted_at DESC)
        """
    )


def _add_card_generation_chunk_ledger(conn: sqlite3.Connection) -> None:
    """Make automatic card publication resumable at the chunk boundary."""

    conn.execute(
        """
        CREATE TABLE card_generation_chunk_results (
            run_id TEXT NOT NULL,
            chunk_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            status TEXT NOT NULL,
            cards_created INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (run_id, chunk_id),
            CHECK (status IN ('succeeded', 'failed')),
            CHECK (chunk_index >= 0),
            CHECK (cards_created >= 0)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_card_generation_chunk_results_run
        ON card_generation_chunk_results (run_id, status, chunk_index)
        """
    )


def _add_notebook_notes(conn: sqlite3.Connection) -> None:
    """Add course-level notes, immutable Chat evidence, and Source snapshots."""

    conn.execute(
        """
        CREATE TABLE notebook_notes (
            id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            title TEXT NOT NULL,
            body_markdown TEXT NOT NULL,
            revision INTEGER NOT NULL,
            origin_type TEXT NOT NULL,
            origin_message_id TEXT,
            origin_conversation_id TEXT,
            origin_snapshot_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT,
            CHECK (revision >= 1),
            CHECK (origin_type IN ('free', 'chat_answer')),
            CHECK (
                (origin_type = 'free'
                    AND origin_message_id IS NULL
                    AND origin_conversation_id IS NULL)
                OR
                (origin_type = 'chat_answer'
                    AND origin_message_id IS NOT NULL
                    AND origin_conversation_id IS NOT NULL)
            )
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_notebook_notes_course_updated
        ON notebook_notes (course_id, deleted_at, updated_at DESC, id)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX idx_notebook_notes_origin_message
        ON notebook_notes (origin_message_id)
        WHERE origin_type = 'chat_answer'
        """
    )
    conn.execute(
        """
        CREATE TABLE notebook_note_citations (
            id TEXT PRIMARY KEY,
            note_id TEXT NOT NULL,
            note_revision INTEGER NOT NULL,
            origin_citation_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            source_id TEXT NOT NULL,
            chunk_id TEXT NOT NULL,
            chunk_text_hash TEXT NOT NULL,
            source_title TEXT NOT NULL,
            source_type TEXT NOT NULL,
            quote TEXT NOT NULL,
            score REAL NOT NULL,
            locator_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (note_id, origin_citation_id),
            UNIQUE (note_id, ordinal),
            CHECK (note_revision >= 1),
            CHECK (ordinal >= 1),
            CHECK (score >= -1.0 AND score <= 1.0)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_notebook_note_citations_note
        ON notebook_note_citations (note_id, ordinal)
        """
    )
    conn.execute(
        """
        CREATE TABLE notebook_note_citation_spans (
            id TEXT PRIMARY KEY,
            note_id TEXT NOT NULL,
            citation_id TEXT NOT NULL,
            sentence_index INTEGER NOT NULL,
            start_offset INTEGER NOT NULL,
            end_offset INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (
                note_id,
                citation_id,
                sentence_index,
                start_offset,
                end_offset
            ),
            CHECK (sentence_index >= 0),
            CHECK (start_offset >= 0),
            CHECK (end_offset > start_offset)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_notebook_note_citation_spans_note
        ON notebook_note_citation_spans (note_id, citation_id)
        """
    )
    conn.execute(
        """
        CREATE TABLE notebook_note_source_snapshots (
            id TEXT PRIMARY KEY,
            note_id TEXT NOT NULL,
            course_id TEXT NOT NULL,
            note_revision INTEGER NOT NULL,
            title TEXT NOT NULL,
            body_markdown TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (note_id, note_revision),
            CHECK (note_revision >= 1)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_notebook_note_source_snapshots_course
        ON notebook_note_source_snapshots (
            course_id,
            note_id,
            note_revision DESC
        )
        """
    )


def _add_evidence_grounded_concept_graph(
    conn: sqlite3.Connection,
) -> None:
    conn.execute(
        """
        CREATE TABLE concepts (
            id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            current_revision INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (id, course_id),
            FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE,
            FOREIGN KEY (id, course_id, current_revision)
                REFERENCES concept_revisions(concept_id, course_id, revision)
                DEFERRABLE INITIALLY DEFERRED,
            CHECK (length(id) BETWEEN 1 AND 200),
            CHECK (length(course_id) BETWEEN 1 AND 200),
            CHECK (current_revision >= 1)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE concept_revisions (
            concept_id TEXT NOT NULL,
            course_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            preferred_name TEXT NOT NULL,
            short_definition TEXT NOT NULL,
            identity_status TEXT NOT NULL DEFAULT 'active',
            merged_into_concept_id TEXT,
            review_status TEXT NOT NULL DEFAULT 'candidate',
            validity_status TEXT NOT NULL DEFAULT 'current',
            proposal_origin TEXT NOT NULL DEFAULT 'human',
            provider TEXT,
            model TEXT,
            prompt_protocol TEXT,
            output_version TEXT,
            review_actor TEXT,
            reviewed_at TEXT,
            review_revision INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (concept_id, revision),
            UNIQUE (concept_id, course_id, revision),
            FOREIGN KEY (concept_id, course_id)
                REFERENCES concepts(id, course_id) ON DELETE CASCADE
                DEFERRABLE INITIALLY DEFERRED,
            FOREIGN KEY (merged_into_concept_id, course_id)
                REFERENCES concepts(id, course_id),
            CHECK (length(concept_id) BETWEEN 1 AND 200),
            CHECK (length(course_id) BETWEEN 1 AND 200),
            CHECK (trim(preferred_name) != ''),
            CHECK (trim(short_definition) != ''),
            CHECK (identity_status IN ('active', 'merged', 'retired')),
            CHECK (
                (identity_status = 'merged' AND merged_into_concept_id IS NOT NULL)
                OR
                (identity_status != 'merged' AND merged_into_concept_id IS NULL)
            ),
            CHECK (
                merged_into_concept_id IS NULL
                OR merged_into_concept_id != concept_id
            ),
            CHECK (revision >= 1),
            CHECK (review_status IN ('candidate', 'accepted', 'rejected')),
            CHECK (validity_status IN ('current', 'stale', 'tombstoned')),
            CHECK (proposal_origin IN ('human', 'model', 'import')),
            CHECK (
                proposal_origin != 'model'
                OR (
                    provider IS NOT NULL AND trim(provider) != ''
                    AND model IS NOT NULL AND trim(model) != ''
                    AND prompt_protocol IS NOT NULL
                    AND trim(prompt_protocol) != ''
                    AND output_version IS NOT NULL
                    AND trim(output_version) != ''
                )
            ),
            CHECK (
                (review_status = 'candidate' AND review_actor IS NULL
                    AND reviewed_at IS NULL AND review_revision IS NULL)
                OR
                (review_status != 'candidate' AND review_actor IS NOT NULL
                    AND trim(review_actor) != '' AND reviewed_at IS NOT NULL
                    AND review_revision IS NOT NULL
                    AND review_revision >= 1)
            )
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_concepts_course_id
        ON concepts (course_id, id)
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_concept_revisions_course_status
        ON concept_revisions (
            course_id,
            identity_status,
            review_status,
            validity_status,
            concept_id,
            revision
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE concept_evidence (
            id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            concept_id TEXT NOT NULL,
            concept_revision INTEGER NOT NULL,
            source_id TEXT NOT NULL,
            chunk_id TEXT NOT NULL,
            chunk_text_hash TEXT NOT NULL,
            source_title TEXT NOT NULL,
            source_type TEXT NOT NULL,
            quote TEXT NOT NULL,
            locator_json TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (concept_id, concept_revision, ordinal),
            UNIQUE (
                concept_id,
                concept_revision,
                source_id,
                chunk_id,
                quote
            ),
            FOREIGN KEY (concept_id, course_id, concept_revision)
                REFERENCES concept_revisions(
                    concept_id, course_id, revision
                ) ON DELETE CASCADE,
            CHECK (length(id) BETWEEN 1 AND 200),
            CHECK (length(course_id) BETWEEN 1 AND 200),
            CHECK (length(source_id) BETWEEN 1 AND 200),
            CHECK (length(chunk_id) BETWEEN 1 AND 200),
            CHECK (concept_revision >= 1),
            CHECK (length(chunk_text_hash) = 64),
            CHECK (trim(source_title) != ''),
            CHECK (length(quote) BETWEEN 1 AND 16000),
            CHECK (trim(quote) != ''),
            CHECK (json_valid(locator_json)),
            CHECK (ordinal >= 0)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_concept_evidence_source_chunk
        ON concept_evidence (course_id, source_id, chunk_id, chunk_text_hash)
        """
    )
    conn.execute(
        """
        CREATE TABLE concept_relations (
            id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            source_concept_id TEXT NOT NULL,
            target_concept_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            current_revision INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (id, course_id),
            UNIQUE (
                course_id,
                relation_type,
                source_concept_id,
                target_concept_id
            ),
            FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE,
            FOREIGN KEY (source_concept_id, course_id)
                REFERENCES concepts(id, course_id),
            FOREIGN KEY (target_concept_id, course_id)
                REFERENCES concepts(id, course_id),
            FOREIGN KEY (id, course_id, current_revision)
                REFERENCES concept_relation_revisions(
                    relation_id, course_id, revision
                ) DEFERRABLE INITIALLY DEFERRED,
            CHECK (length(id) BETWEEN 1 AND 200),
            CHECK (length(course_id) BETWEEN 1 AND 200),
            CHECK (source_concept_id != target_concept_id),
            CHECK (relation_type IN (
                'prerequisite', 'part_of', 'example_of',
                'related', 'contrast_with'
            )),
            CHECK (
                relation_type NOT IN ('related', 'contrast_with')
                OR source_concept_id < target_concept_id
            ),
            CHECK (current_revision >= 1)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE concept_relation_revisions (
            relation_id TEXT NOT NULL,
            course_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            support_basis TEXT NOT NULL,
            rationale TEXT NOT NULL,
            review_status TEXT NOT NULL DEFAULT 'candidate',
            validity_status TEXT NOT NULL DEFAULT 'current',
            proposal_origin TEXT NOT NULL DEFAULT 'human',
            provider TEXT,
            model TEXT,
            prompt_protocol TEXT,
            output_version TEXT,
            review_actor TEXT,
            reviewed_at TEXT,
            review_revision INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (relation_id, revision),
            UNIQUE (relation_id, course_id, revision),
            FOREIGN KEY (relation_id, course_id)
                REFERENCES concept_relations(id, course_id) ON DELETE CASCADE
                DEFERRABLE INITIALLY DEFERRED,
            CHECK (length(relation_id) BETWEEN 1 AND 200),
            CHECK (length(course_id) BETWEEN 1 AND 200),
            CHECK (support_basis IN (
                'source_asserted', 'pedagogical_inference'
            )),
            CHECK (trim(rationale) != ''),
            CHECK (revision >= 1),
            CHECK (review_status IN ('candidate', 'accepted', 'rejected')),
            CHECK (validity_status IN ('current', 'stale', 'tombstoned')),
            CHECK (proposal_origin IN ('human', 'model', 'import')),
            CHECK (
                proposal_origin != 'model'
                OR (
                    provider IS NOT NULL AND trim(provider) != ''
                    AND model IS NOT NULL AND trim(model) != ''
                    AND prompt_protocol IS NOT NULL
                    AND trim(prompt_protocol) != ''
                    AND output_version IS NOT NULL
                    AND trim(output_version) != ''
                )
            ),
            CHECK (
                (review_status = 'candidate' AND review_actor IS NULL
                    AND reviewed_at IS NULL AND review_revision IS NULL)
                OR
                (review_status != 'candidate' AND review_actor IS NOT NULL
                    AND trim(review_actor) != '' AND reviewed_at IS NOT NULL
                    AND review_revision IS NOT NULL
                    AND review_revision >= 1)
            )
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_concept_relations_course_id
        ON concept_relations (course_id, id)
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_concept_relation_revisions_course_status
        ON concept_relation_revisions (
            course_id,
            review_status,
            validity_status,
            relation_id,
            revision
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE relation_evidence (
            id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            relation_id TEXT NOT NULL,
            relation_revision INTEGER NOT NULL,
            support_role TEXT NOT NULL,
            source_id TEXT NOT NULL,
            chunk_id TEXT NOT NULL,
            chunk_text_hash TEXT NOT NULL,
            source_title TEXT NOT NULL,
            source_type TEXT NOT NULL,
            quote TEXT NOT NULL,
            locator_json TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (relation_id, relation_revision, ordinal),
            UNIQUE (
                relation_id,
                relation_revision,
                support_role,
                source_id,
                chunk_id,
                quote
            ),
            FOREIGN KEY (relation_id, course_id, relation_revision)
                REFERENCES concept_relation_revisions(
                    relation_id, course_id, revision
                )
                ON DELETE CASCADE,
            CHECK (length(id) BETWEEN 1 AND 200),
            CHECK (length(course_id) BETWEEN 1 AND 200),
            CHECK (length(source_id) BETWEEN 1 AND 200),
            CHECK (length(chunk_id) BETWEEN 1 AND 200),
            CHECK (relation_revision >= 1),
            CHECK (support_role IN (
                'relation_assertion', 'source_endpoint', 'target_endpoint'
            )),
            CHECK (length(chunk_text_hash) = 64),
            CHECK (trim(source_title) != ''),
            CHECK (length(quote) BETWEEN 1 AND 16000),
            CHECK (trim(quote) != ''),
            CHECK (json_valid(locator_json)),
            CHECK (ordinal >= 0)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_relation_evidence_source_chunk
        ON relation_evidence (course_id, source_id, chunk_id, chunk_text_hash)
        """
    )


def _add_source_projection_generation(conn: sqlite3.Connection) -> None:
    chunk_rows = conn.execute(
        "SELECT id, text, text_hash FROM source_chunks ORDER BY id"
    ).fetchall()
    for chunk_row in chunk_rows:
        expected_hash = hashlib.sha256(
            str(chunk_row["text"]).encode("utf-8")
        ).hexdigest()
        if str(chunk_row["text_hash"]) != expected_hash:
            raise RuntimeError(
                "Source projection migration found a mismatched Chunk hash."
            )
    duplicate_ordinal = conn.execute(
        """
        SELECT source_id, ordinal
        FROM source_chunks
        WHERE is_active = 1
        GROUP BY source_id, ordinal
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    ).fetchone()
    if duplicate_ordinal is not None:
        raise RuntimeError(
            "Source projection migration found duplicate active ordinals."
        )

    conn.execute(
        "ALTER TABLE sources ADD COLUMN projection_generation_id TEXT"
    )
    conn.execute(
        "ALTER TABLE sources ADD COLUMN projection_manifest_hash TEXT"
    )
    conn.execute(
        "ALTER TABLE concept_evidence "
        "ADD COLUMN projection_generation_id TEXT"
    )
    conn.execute(
        "ALTER TABLE relation_evidence "
        "ADD COLUMN projection_generation_id TEXT"
    )

    source_rows = conn.execute(
        "SELECT id, source_type FROM sources ORDER BY id"
    ).fetchall()
    for source_row in source_rows:
        chunk_rows = conn.execute(
            """
            SELECT id, chunk_type, ordinal, text_hash, locator_json,
                   chunker_version
            FROM source_chunks
            WHERE source_id = ? AND is_active = 1
            ORDER BY ordinal, id
            """,
            (source_row["id"],),
        ).fetchall()
        manifest_hash = build_projection_manifest_hash(
            source_id=str(source_row["id"]),
            source_type=str(source_row["source_type"]),
            chunks=(
                ProjectionManifestChunk(
                    id=str(chunk_row["id"]),
                    chunk_type=str(chunk_row["chunk_type"]),
                    ordinal=int(chunk_row["ordinal"]),
                    text_hash=str(chunk_row["text_hash"]),
                    locator=str(chunk_row["locator_json"]),
                    chunker_version=str(chunk_row["chunker_version"]),
                )
                for chunk_row in chunk_rows
            ),
        )
        generation_id = select_projection_generation_id(
            current_generation_id=None,
            current_manifest_hash=None,
            next_manifest_hash=manifest_hash,
        )
        conn.execute(
            """
            UPDATE sources
            SET projection_generation_id = ?, projection_manifest_hash = ?
            WHERE id = ?
            """,
            (generation_id, manifest_hash, source_row["id"]),
        )

    conn.execute(
        """
        CREATE UNIQUE INDEX idx_sources_projection_generation
        ON sources (projection_generation_id)
        WHERE projection_generation_id IS NOT NULL
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX idx_source_chunks_active_ordinal
        ON source_chunks (source_id, ordinal)
        WHERE is_active = 1
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_concept_evidence_projection_generation
        ON concept_evidence (
            course_id, source_id, projection_generation_id
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_relation_evidence_projection_generation
        ON relation_evidence (
            course_id, source_id, projection_generation_id
        )
        """
    )

    incomplete_sources = conn.execute(
        """
        SELECT COUNT(*)
        FROM sources
        WHERE projection_generation_id IS NULL
           OR projection_manifest_hash IS NULL
           OR length(projection_manifest_hash) != 64
        """
    ).fetchone()[0]
    if incomplete_sources:
        raise RuntimeError(
            "Source projection generation migration left incomplete rows."
        )
    projection_contract = """
        NEW.projection_generation_id IS NULL
        OR length(trim(NEW.projection_generation_id)) NOT BETWEEN 1 AND 200
        OR NEW.projection_manifest_hash IS NULL
        OR length(NEW.projection_manifest_hash) != 64
        OR NEW.projection_manifest_hash GLOB '*[^0-9a-f]*'
    """
    conn.execute(
        f"""
        CREATE TRIGGER sources_projection_identity_insert
        BEFORE INSERT ON sources
        WHEN {projection_contract}
        BEGIN
            SELECT RAISE(ABORT, 'invalid Source projection identity');
        END
        """
    )
    conn.execute(
        f"""
        CREATE TRIGGER sources_projection_identity_update
        BEFORE UPDATE OF projection_generation_id, projection_manifest_hash
        ON sources
        WHEN {projection_contract}
        BEGIN
            SELECT RAISE(ABORT, 'invalid Source projection identity');
        END
        """
    )


def _create_concept_graph_operation_guards(
    conn: sqlite3.Connection,
) -> None:
    conn.execute(
        """
        CREATE INDEX idx_concept_graph_operations_entity
        ON concept_graph_operations (
            course_id, entity_type, entity_id, result_revision
        )
        """
    )
    operation_result_contract = """
        json_valid(NEW.result_json) != 1
        OR json_type(NEW.result_json) != 'object'
        OR json_type(NEW.result_json, '$.entity_type') != 'text'
        OR json_type(NEW.result_json, '$.entity_id') != 'text'
        OR json_type(NEW.result_json, '$.revision') != 'integer'
        OR json_extract(NEW.result_json, '$.entity_type') IS NOT NEW.entity_type
        OR json_extract(NEW.result_json, '$.entity_id') IS NOT NEW.entity_id
        OR json_extract(NEW.result_json, '$.revision') IS NOT NEW.result_revision
        OR (SELECT COUNT(*) FROM json_each(NEW.result_json)) != 3
        OR (
            NEW.entity_type = 'concept'
            AND NOT EXISTS (
                SELECT 1 FROM concept_revisions
                WHERE concept_revisions.course_id = NEW.course_id
                  AND concept_revisions.concept_id = NEW.entity_id
                  AND concept_revisions.revision = NEW.result_revision
            )
        )
        OR
        (
            NEW.entity_type = 'relation'
            AND NOT EXISTS (
                SELECT 1 FROM concept_relation_revisions
                WHERE concept_relation_revisions.course_id = NEW.course_id
                  AND concept_relation_revisions.relation_id = NEW.entity_id
                  AND concept_relation_revisions.revision = NEW.result_revision
            )
        )
    """
    conn.execute(
        f"""
        CREATE TRIGGER concept_graph_operation_result_insert
        BEFORE INSERT ON concept_graph_operations
        WHEN {operation_result_contract}
        BEGIN
            SELECT RAISE(ABORT, 'invalid Concept graph operation result');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER concept_graph_operation_immutable_update
        BEFORE UPDATE ON concept_graph_operations
        BEGIN
            SELECT RAISE(ABORT, 'Concept graph operation is immutable');
        END
        """
    )


def _add_concept_graph_review_lifecycle(
    conn: sqlite3.Connection,
) -> None:
    conn.execute(
        """
        CREATE TABLE concept_aliases (
            id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            concept_id TEXT NOT NULL,
            concept_revision INTEGER NOT NULL,
            display_text TEXT NOT NULL,
            normalized_text TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (concept_id, concept_revision, ordinal),
            UNIQUE (concept_id, concept_revision, normalized_text),
            FOREIGN KEY (concept_id, course_id, concept_revision)
                REFERENCES concept_revisions(
                    concept_id, course_id, revision
                ) ON DELETE CASCADE,
            CHECK (length(id) BETWEEN 1 AND 200),
            CHECK (length(course_id) BETWEEN 1 AND 200),
            CHECK (length(concept_id) BETWEEN 1 AND 200),
            CHECK (concept_revision >= 1),
            CHECK (length(display_text) BETWEEN 1 AND 200),
            CHECK (trim(display_text) != ''),
            CHECK (length(normalized_text) BETWEEN 1 AND 200),
            CHECK (trim(normalized_text) != ''),
            CHECK (ordinal >= 0)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_concept_aliases_lookup
        ON concept_aliases (
            course_id, normalized_text, concept_id, concept_revision
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE relation_endpoint_revisions (
            relation_id TEXT NOT NULL,
            course_id TEXT NOT NULL,
            relation_revision INTEGER NOT NULL,
            source_concept_id TEXT NOT NULL,
            source_concept_revision INTEGER NOT NULL,
            target_concept_id TEXT NOT NULL,
            target_concept_revision INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (relation_id, relation_revision),
            UNIQUE (relation_id, course_id, relation_revision),
            FOREIGN KEY (relation_id, course_id, relation_revision)
                REFERENCES concept_relation_revisions(
                    relation_id, course_id, revision
                ) ON DELETE CASCADE,
            FOREIGN KEY (
                source_concept_id, course_id, source_concept_revision
            )
                REFERENCES concept_revisions(
                    concept_id, course_id, revision
                ),
            FOREIGN KEY (
                target_concept_id, course_id, target_concept_revision
            )
                REFERENCES concept_revisions(
                    concept_id, course_id, revision
                ),
            CHECK (length(relation_id) BETWEEN 1 AND 200),
            CHECK (length(course_id) BETWEEN 1 AND 200),
            CHECK (relation_revision >= 1),
            CHECK (length(source_concept_id) BETWEEN 1 AND 200),
            CHECK (source_concept_revision >= 1),
            CHECK (length(target_concept_id) BETWEEN 1 AND 200),
            CHECK (target_concept_revision >= 1),
            CHECK (source_concept_id != target_concept_id)
        )
        """
    )
    endpoint_contract = """
        NOT EXISTS (
            SELECT 1
            FROM concept_relations AS relation_identity
            WHERE relation_identity.id = NEW.relation_id
              AND relation_identity.course_id = NEW.course_id
              AND relation_identity.source_concept_id = NEW.source_concept_id
              AND relation_identity.target_concept_id = NEW.target_concept_id
        )
    """
    conn.execute(
        f"""
        CREATE TRIGGER relation_endpoint_identity_insert
        BEFORE INSERT ON relation_endpoint_revisions
        WHEN {endpoint_contract}
        BEGIN
            SELECT RAISE(ABORT, 'relation endpoint identity mismatch');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER relation_endpoint_identity_update
        BEFORE UPDATE ON relation_endpoint_revisions
        BEGIN
            SELECT RAISE(ABORT, 'relation endpoint binding is immutable');
        END
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_relation_endpoint_source_revision
        ON relation_endpoint_revisions (
            course_id, source_concept_id, source_concept_revision,
            relation_id, relation_revision
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_relation_endpoint_target_revision
        ON relation_endpoint_revisions (
            course_id, target_concept_id, target_concept_revision,
            relation_id, relation_revision
        )
        """
    )
    conn.execute(
        """
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
            CHECK (length(course_id) BETWEEN 1 AND 200),
            CHECK (length(operation_id) BETWEEN 1 AND 100),
            CHECK (kind IN (
                'concept_edit', 'concept_review', 'concept_mark_stale',
                'relation_edit', 'relation_review', 'relation_mark_stale'
            )),
            CHECK (length(request_hash) = 64),
            CHECK (request_hash NOT GLOB '*[^0-9a-f]*'),
            CHECK (length(actor) BETWEEN 1 AND 200),
            CHECK (trim(actor) != ''),
            CHECK (length(reason) BETWEEN 1 AND 4000),
            CHECK (trim(reason) != ''),
            CHECK (entity_type IN ('concept', 'relation')),
            CHECK (
                (entity_type = 'concept' AND kind IN (
                    'concept_edit', 'concept_review', 'concept_mark_stale'
                ))
                OR
                (entity_type = 'relation' AND kind IN (
                    'relation_edit', 'relation_review',
                    'relation_mark_stale'
                ))
            ),
            CHECK (length(entity_id) BETWEEN 1 AND 200),
            CHECK (result_revision >= 1),
            CHECK (json_valid(result_json)),
            CHECK (length(result_json) BETWEEN 2 AND 4096)
        )
        """
    )
    _create_concept_graph_operation_guards(conn)
    conn.execute(
        """
        CREATE INDEX idx_concept_relations_source_incident
        ON concept_relations (course_id, source_concept_id, id)
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_concept_relations_target_incident
        ON concept_relations (course_id, target_concept_id, id)
        """
    )
    conn.execute(
        """
        CREATE TRIGGER concept_relation_identity_immutable_update
        BEFORE UPDATE OF course_id, source_concept_id,
            target_concept_id, relation_type
        ON concept_relations
        BEGIN
            SELECT RAISE(ABORT, 'Concept relation identity is immutable');
        END
        """
    )


def _add_concept_graph_identity_lifecycle(
    conn: sqlite3.Connection,
) -> None:
    # SQLite cannot widen a CHECK constraint in place. Rebuild only the
    # operation ledger, preserving every immutable v11 receipt byte-for-byte.
    conn.execute("DROP TRIGGER concept_graph_operation_immutable_update")
    conn.execute("DROP TRIGGER concept_graph_operation_result_insert")
    conn.execute("DROP INDEX idx_concept_graph_operations_entity")
    conn.execute(
        "ALTER TABLE concept_graph_operations "
        "RENAME TO concept_graph_operations_v11"
    )
    conn.execute(
        """
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
            CHECK (length(course_id) BETWEEN 1 AND 200),
            CHECK (length(operation_id) BETWEEN 1 AND 100),
            CHECK (kind IN (
                'concept_create', 'concept_edit', 'concept_review',
                'concept_mark_stale', 'concept_merge', 'concept_retire',
                'relation_create', 'relation_edit', 'relation_review',
                'relation_mark_stale'
            )),
            CHECK (length(request_hash) = 64),
            CHECK (request_hash NOT GLOB '*[^0-9a-f]*'),
            CHECK (length(actor) BETWEEN 1 AND 200),
            CHECK (trim(actor) != ''),
            CHECK (length(reason) BETWEEN 1 AND 4000),
            CHECK (trim(reason) != ''),
            CHECK (entity_type IN ('concept', 'relation')),
            CHECK (
                (entity_type = 'concept' AND kind IN (
                    'concept_create', 'concept_edit', 'concept_review',
                    'concept_mark_stale', 'concept_merge', 'concept_retire'
                ))
                OR
                (entity_type = 'relation' AND kind IN (
                    'relation_create', 'relation_edit', 'relation_review',
                    'relation_mark_stale'
                ))
            ),
            CHECK (length(entity_id) BETWEEN 1 AND 200),
            CHECK (result_revision >= 1),
            CHECK (json_valid(result_json)),
            CHECK (length(result_json) BETWEEN 2 AND 4096)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO concept_graph_operations (
            course_id, operation_id, kind, request_hash, actor, reason,
            entity_type, entity_id, result_revision, result_json, created_at
        )
        SELECT course_id, operation_id, kind, request_hash, actor, reason,
               entity_type, entity_id, result_revision, result_json,
               created_at
        FROM concept_graph_operations_v11
        """
    )
    conn.execute("DROP TABLE concept_graph_operations_v11")
    _create_concept_graph_operation_guards(conn)
    conn.execute(
        """
        CREATE INDEX idx_concept_revisions_merge_target
        ON concept_revisions (
            course_id, merged_into_concept_id, concept_id, revision
        )
        WHERE merged_into_concept_id IS NOT NULL
        """
    )

    immutable_revision_tables = (
        ("concept_revisions", "concept revision"),
        ("concept_evidence", "Concept evidence"),
        ("concept_aliases", "Concept alias"),
        ("concept_relation_revisions", "relation revision"),
        ("relation_evidence", "relation evidence"),
    )
    for table, label in immutable_revision_tables:
        conn.execute(
            f"""
            CREATE TRIGGER {table}_immutable_update
            BEFORE UPDATE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '{label} is immutable');
            END
            """
        )


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="unified_source_index",
        apply=_create_unified_source_index,
    ),
    Migration(
        version=2,
        name="grounded_chat",
        apply=_create_grounded_chat,
    ),
    Migration(
        version=3,
        name="align_grounded_chat_turn_contract",
        apply=_align_grounded_chat_turn_contract,
    ),
    Migration(
        version=4,
        name="video_content_fingerprint",
        apply=_add_video_content_fingerprint,
    ),
    Migration(
        version=5,
        name="local_workspace_lifecycle",
        apply=_add_local_workspace_lifecycle,
    ),
    Migration(
        version=6,
        name="strengthen_trash_operation_states",
        apply=_strengthen_trash_operation_states,
    ),
    Migration(
        version=7,
        name="card_generation_chunk_ledger",
        apply=_add_card_generation_chunk_ledger,
    ),
    Migration(
        version=8,
        name="notebook_notes",
        apply=_add_notebook_notes,
    ),
    Migration(
        version=9,
        name="evidence_grounded_concept_graph",
        apply=_add_evidence_grounded_concept_graph,
    ),
    Migration(
        version=10,
        name="source_projection_generation",
        apply=_add_source_projection_generation,
    ),
    Migration(
        version=11,
        name="concept_graph_review_lifecycle",
        apply=_add_concept_graph_review_lifecycle,
    ),
    Migration(
        version=12,
        name="concept_graph_identity_lifecycle",
        apply=_add_concept_graph_identity_lifecycle,
    ),
)


def latest_schema_version(
    migrations: Sequence[Migration] = MIGRATIONS,
) -> int:
    return max((migration.version for migration in migrations), default=0)


def prepare_migration_backup(
    db_path: Path,
    migrations: Sequence[Migration] = MIGRATIONS,
) -> Path | None:
    if not db_path.is_file() or db_path.stat().st_size == 0:
        return None

    with closing(sqlite3.connect(db_path)) as source:
        applied = _read_applied_versions(source)
        pending = [
            migration
            for migration in migrations
            if migration.version not in applied
        ]
        if not pending:
            return None

        quick_check = source.execute("PRAGMA quick_check").fetchone()
        if quick_check is None or quick_check[0] != "ok":
            detail = quick_check[0] if quick_check is not None else "no result"
            raise RuntimeError(
                f"Database quick_check failed before migration: {detail}"
            )

        backup_dir = db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target_version = max(migration.version for migration in pending)
        backup_path = (
            backup_dir
            / f"{db_path.stem}.pre-migration-v{target_version}-{stamp}.db"
        )
        with closing(sqlite3.connect(backup_path)) as destination:
            source.backup(destination)

    with closing(sqlite3.connect(backup_path)) as backup:
        backup_check = backup.execute("PRAGMA quick_check").fetchone()
        if backup_check is None or backup_check[0] != "ok":
            backup_path.unlink(missing_ok=True)
            detail = (
                backup_check[0]
                if backup_check is not None
                else "no result"
            )
            raise RuntimeError(
                f"Migration backup quick_check failed: {detail}"
            )

    return backup_path


def apply_migrations(
    conn: sqlite3.Connection,
    migrations: Sequence[Migration] = MIGRATIONS,
) -> list[int]:
    conn.row_factory = sqlite3.Row
    ordered = sorted(migrations, key=lambda item: item.version)
    versions = [migration.version for migration in ordered]
    if len(versions) != len(set(versions)) or any(
        version <= 0
        for version in versions
    ):
        raise RuntimeError("Migration versions must be unique and positive.")

    conn.execute("SAVEPOINT vcc_schema_migrations")
    completed: list[int] = []
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        applied_rows = {
            int(row["version"]): str(row["name"])
            for row in conn.execute(
                "SELECT version, name FROM schema_migrations"
            ).fetchall()
        }
        known = {migration.version: migration.name for migration in ordered}
        unknown_versions = sorted(set(applied_rows) - set(known))
        if unknown_versions:
            raise RuntimeError(
                "Database schema is newer than this application: "
                + ", ".join(str(version) for version in unknown_versions)
            )
        for version, name in applied_rows.items():
            if known[version] != name:
                raise RuntimeError(
                    f"Migration {version} name does not match the database."
                )

        for migration in ordered:
            if migration.version in applied_rows:
                continue
            migration.apply(conn)
            conn.execute(
                """
                INSERT INTO schema_migrations (version, name, applied_at)
                VALUES (?, ?, ?)
                """,
                (
                    migration.version,
                    migration.name,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            completed.append(migration.version)
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT vcc_schema_migrations")
        conn.execute("RELEASE SAVEPOINT vcc_schema_migrations")
        raise
    conn.execute("RELEASE SAVEPOINT vcc_schema_migrations")
    return completed


def _read_applied_versions(conn: sqlite3.Connection) -> set[int]:
    table = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'schema_migrations'
        """
    ).fetchone()
    if table is None:
        return set()
    return {
        int(row[0])
        for row in conn.execute(
            "SELECT version FROM schema_migrations"
        ).fetchall()
    }


def _backfill_sources(conn: sqlite3.Connection) -> None:
    now = datetime.now(timezone.utc).isoformat()
    job_rows = conn.execute(
        """
        SELECT
            id, course_id, status, original_filename, stored_name,
            size_bytes, metadata, error_message, created_at, updated_at
        FROM jobs
        """
    ).fetchall()
    for row in job_rows:
        origin_id = str(row["id"])
        status = str(row["status"])
        content_status = {
            "uploaded": "pending",
            "probing": "processing",
            "extracting_audio": "processing",
            "transcribing": "processing",
            "completed": "ready",
            "failed": "failed",
            "canceled": "failed",
        }.get(status, "pending")
        title = (
            row["original_filename"]
            or row["stored_name"]
            or f"Video {origin_id}"
        )
        metadata_json = row["metadata"] or "{}"
        _validate_json_object(metadata_json)
        conn.execute(
            """
            INSERT INTO sources (
                id, course_id, origin_type, origin_id, source_type, title,
                content_status, index_status, index_generation, index_model,
                index_dimension,
                enabled, size_bytes, mime_type, metadata_json, error_message,
                index_error, created_at, updated_at, indexed_at
            ) VALUES (?, ?, 'video_job', ?, 'video', ?, ?, 'not_indexed',
                      NULL, NULL, NULL, 1, ?, NULL, ?, ?, NULL, ?, ?, NULL)
            """,
            (
                f"job:{origin_id}",
                row["course_id"],
                origin_id,
                str(title),
                content_status,
                row["size_bytes"],
                metadata_json,
                row["error_message"],
                row["created_at"] or now,
                row["updated_at"] or row["created_at"] or now,
            ),
        )

    asset_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(source_assets)")
    }
    job_id_expression = (
        "job_id"
        if "job_id" in asset_columns
        else "NULL AS job_id"
    )
    asset_rows = conn.execute(
        f"""
        SELECT
            id, course_id, {job_id_expression}, asset_type,
            original_filename, mime_type,
            size_bytes, extraction_status, metadata_json, error_message,
            created_at, updated_at
        FROM source_assets
        """
    ).fetchall()
    for row in asset_rows:
        origin_id = str(row["id"])
        metadata_json = row["metadata_json"] or "{}"
        _validate_json_object(metadata_json)
        metadata = _json_object(metadata_json)
        if row["job_id"]:
            metadata["job_id"] = row["job_id"]
        metadata_json = json.dumps(metadata, ensure_ascii=False)
        conn.execute(
            """
            INSERT INTO sources (
                id, course_id, origin_type, origin_id, source_type, title,
                content_status, index_status, index_generation, index_model,
                index_dimension,
                enabled, size_bytes, mime_type, metadata_json, error_message,
                index_error, created_at, updated_at, indexed_at
            ) VALUES (?, ?, 'source_asset', ?, ?, ?, ?, 'not_indexed',
                      NULL, NULL, NULL, 1, ?, ?, ?, ?, NULL, ?, ?, NULL)
            """,
            (
                f"asset:{origin_id}",
                row["course_id"],
                origin_id,
                row["asset_type"],
                row["original_filename"],
                row["extraction_status"],
                row["size_bytes"],
                row["mime_type"],
                metadata_json,
                row["error_message"],
                row["created_at"] or now,
                row["updated_at"] or row["created_at"] or now,
            ),
        )


def _backfill_source_chunks(conn: sqlite3.Connection) -> None:
    transcript_rows = conn.execute(
        """
        SELECT
            id, job_id, chunk_index, start_seconds, end_seconds, text,
            segment_ids, chunker_version, created_at
        FROM transcript_chunks
        ORDER BY job_id, chunk_index
        """
    ).fetchall()
    for row in transcript_rows:
        origin_id = str(row["id"])
        segment_ids = _json_list(row["segment_ids"])
        locator = {
            "schema_version": 1,
            "kind": "video_time",
            "job_id": row["job_id"],
            "start_seconds": row["start_seconds"],
            "end_seconds": row["end_seconds"],
            "segment_ids": segment_ids,
            "metadata": {},
        }
        _insert_backfilled_chunk(
            conn,
            chunk_id=f"transcript_chunk:{origin_id}",
            source_id=f"job:{row['job_id']}",
            origin_type="transcript_chunk",
            origin_id=origin_id,
            chunk_type="transcript",
            ordinal=row["chunk_index"],
            text=row["text"],
            locator=locator,
            chunker_version=row["chunker_version"],
            created_at=row["created_at"],
        )

    asset_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(source_assets)")
    }
    job_id_expression = (
        "source_assets.job_id"
        if "job_id" in asset_columns
        else "NULL AS job_id"
    )
    unit_rows = conn.execute(
        f"""
        SELECT
            source_units.id, source_units.asset_id, source_units.unit_type,
            source_units.ordinal, source_units.text,
            source_units.locator_json, source_units.created_at,
            source_assets.asset_type, {job_id_expression}
        FROM source_units
        INNER JOIN source_assets
            ON source_assets.id = source_units.asset_id
        ORDER BY source_units.asset_id, source_units.ordinal
        """
    ).fetchall()
    for row in unit_rows:
        origin_id = str(row["id"])
        raw_locator = _json_object(row["locator_json"])
        locator = _canonical_asset_locator(
            asset_id=row["asset_id"],
            asset_type=row["asset_type"],
            unit_type=row["unit_type"],
            job_id=row["job_id"],
            ordinal=row["ordinal"],
            raw_locator=raw_locator,
        )
        _insert_backfilled_chunk(
            conn,
            chunk_id=f"source_unit:{origin_id}",
            source_id=f"asset:{row['asset_id']}",
            origin_type="source_unit",
            origin_id=origin_id,
            chunk_type=(
                "transcript"
                if row["unit_type"] == "transcript_segment"
                else row["unit_type"]
            ),
            ordinal=row["ordinal"],
            text=row["text"],
            locator=locator,
            chunker_version="source-unit-v1",
            created_at=row["created_at"],
        )


def _insert_backfilled_chunk(
    conn: sqlite3.Connection,
    *,
    chunk_id: str,
    source_id: str,
    origin_type: str,
    origin_id: str,
    chunk_type: str,
    ordinal: int,
    text: str,
    locator: dict[str, object],
    chunker_version: str,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO source_chunks (
            id, source_id, origin_type, origin_id, chunk_type, ordinal,
            text, text_hash, locator_json, chunker_version, is_active,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            chunk_id,
            source_id,
            origin_type,
            origin_id,
            chunk_type,
            ordinal,
            text,
            hashlib.sha256(str(text).encode("utf-8")).hexdigest(),
            json.dumps(locator, ensure_ascii=False),
            chunker_version,
            created_at,
            created_at,
        ),
    )


def _validate_source_backfill(conn: sqlite3.Connection) -> None:
    expected_sources = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM jobs)
            + (SELECT COUNT(*) FROM source_assets)
        """
    ).fetchone()[0]
    actual_sources = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    expected_chunks = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM transcript_chunks)
            + (SELECT COUNT(*) FROM source_units)
        """
    ).fetchone()[0]
    actual_chunks = conn.execute(
        "SELECT COUNT(*) FROM source_chunks"
    ).fetchone()[0]
    if actual_sources != expected_sources:
        raise RuntimeError(
            "Unified source migration count mismatch: "
            f"expected {expected_sources}, found {actual_sources}."
        )
    if actual_chunks != expected_chunks:
        raise RuntimeError(
            "Unified source chunk migration count mismatch: "
            f"expected {expected_chunks}, found {actual_chunks}."
        )


def _canonical_asset_locator(
    *,
    asset_id: str,
    asset_type: str,
    unit_type: str,
    job_id: str | None,
    ordinal: int,
    raw_locator: dict[str, object],
) -> dict[str, object]:
    start_seconds, end_seconds = _video_time_range(raw_locator)
    metadata = {
        key: value
        for key, value in raw_locator.items()
        if key
        not in {
            "start_seconds",
            "end_seconds",
            "timestamp_seconds",
            "segment_ids",
            "page_number",
            "slide_number",
            "paragraph_number",
            "section_number",
        }
    }
    if (
        asset_type in {"video", "audio"}
        or unit_type in {"transcript_segment", "video_frame"}
    ):
        return {
            "schema_version": 1,
            "kind": "video_time",
            "job_id": job_id,
            "asset_id": None if job_id else asset_id,
            "start_seconds": start_seconds,
            "end_seconds": end_seconds,
            "segment_ids": _int_list(
                raw_locator.get("segment_ids")
            ),
            "metadata": metadata,
        }
    if asset_type == "pdf":
        return {
            "schema_version": 1,
            "kind": "pdf_page",
            "asset_id": asset_id,
            "page_number": _positive_int(
                raw_locator.get("page_number"),
                ordinal + 1,
            ),
            "metadata": metadata,
        }
    if asset_type == "pptx":
        return {
            "schema_version": 1,
            "kind": "ppt_slide",
            "asset_id": asset_id,
            "slide_number": _positive_int(
                raw_locator.get("slide_number"),
                ordinal + 1,
            ),
            "metadata": metadata,
        }
    if asset_type == "docx":
        return {
            "schema_version": 1,
            "kind": "docx_paragraph",
            "asset_id": asset_id,
            "paragraph_number": _positive_int(
                raw_locator.get("paragraph_number"),
                ordinal + 1,
            ),
            "metadata": metadata,
        }
    return {
        "schema_version": 1,
        "kind": "text_section",
        "asset_id": asset_id,
        "section_number": _positive_int(
            raw_locator.get("section_number"),
            ordinal + 1,
        ),
        "metadata": metadata,
    }


def _positive_int(value: object, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed >= 1 else fallback


def _non_negative_float(value: object, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed >= 0 else fallback


def _video_time_range(
    raw: dict[str, object],
) -> tuple[float, float]:
    timestamp = _non_negative_float(raw.get("timestamp_seconds"), 0)
    start = _non_negative_float(raw.get("start_seconds"), timestamp)
    end = _non_negative_float(raw.get("end_seconds"), start)
    return start, max(start, end)


def _int_list(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    parsed: list[int] = []
    for item in value:
        try:
            parsed.append(int(item))
        except (TypeError, ValueError):
            continue
    return parsed


def _validate_json_object(value: str) -> None:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise RuntimeError("Source metadata must be a JSON object.")


def _json_object(value: str) -> dict[str, object]:
    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: str) -> list[object]:
    parsed = json.loads(value)
    return parsed if isinstance(parsed, list) else []
