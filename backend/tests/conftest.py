import pytest

from app.db import configure_db, init_db
from app.card_generation_run_store import clear_runs
from app.card_relation_store import clear_card_relations
from app.chat_store import clear_chat
from app.job_store import clear_jobs
from app.knowledge_card_store import clear_cards
from app.knowledge_card_note_store import clear_notes
from app.transcript_chunk_store import clear_chunks
from app.topic_store import clear_topics
from app.learning_document_store import clear_learning_documents
from app.course_source_store import clear_course_sources
from app.source_asset_store import clear_source_assets

@pytest.fixture(autouse=True)
def isolated_job_db(tmp_path_factory):
    db_dir = tmp_path_factory.mktemp("job-db")

    configure_db(db_dir / "jobs.db")
    init_db()
    clear_card_relations()
    clear_chat()
    clear_notes()
    clear_runs()
    clear_cards()
    clear_learning_documents()
    clear_course_sources()
    clear_source_assets()
    clear_topics()
    clear_chunks()
    clear_jobs()

    yield

    clear_card_relations()
    clear_chat()
    clear_notes()
    clear_runs()
    clear_cards()
    clear_learning_documents()
    clear_course_sources()
    clear_source_assets()
    clear_topics()
    clear_chunks()
    clear_jobs()
