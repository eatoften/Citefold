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
from app.trash_store import clear_trash_items


def _stop_api_task_manager() -> None:
    # API tests share the imported FastAPI module while each test gets a
    # different SQLite database. Never let a worker from one isolated
    # workspace write into the next test's database.
    import app.main as main

    if main.get_reliable_task_manager.cache_info().currsize == 0:
        return
    manager = main.get_reliable_task_manager()
    try:
        manager.wait_for_idle(timeout_seconds=30.0)
    finally:
        manager.shutdown(wait=True, cancel_futures=True)
        main.get_reliable_task_manager.cache_clear()

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
    clear_trash_items()

    yield

    _stop_api_task_manager()
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
    clear_trash_items()
