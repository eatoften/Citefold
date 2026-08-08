from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest

import app.concept_graph_publication_store as publication_store
from app.concept_graph import ConceptRevisionEdit
from app.concept_graph_publication import GraphPublicationRequest
from app.concept_graph_publication_service import (
    ConceptGraphPublicationConflictError,
    ConceptGraphPublicationPersistenceError,
    preview_course_publication,
    publish_course_version,
)
from app.concept_graph_service import edit_course_concept
from app.course_source import CourseSourceChunk, PdfPageLocator, hash_source_chunk_text
from app.course_source_store import replace_source_projection
from app.db import connect
from tests.concept_graph_publication_support import (
    accepted_concept,
    make_course_source,
)


def _request(preview, operation_id: str) -> GraphPublicationRequest:
    return GraphPublicationRequest(
        operation_id=operation_id,
        expected_active_version=preview.active_version,
        expected_draft_manifest_hash=preview.draft_manifest_hash,
        actor="publisher@example.test",
        reason="Publish one atomic immutable graph snapshot.",
    )


def test_distinct_concurrent_publishers_cannot_both_advance_same_head() -> None:
    course, _, chunk = make_course_source("concurrent-cas")
    accepted_concept(course.id, chunk, "Alpha", "Alpha")
    preview = preview_course_publication(course.id)
    requests = [
        _request(preview, uuid4().hex),
        _request(preview, uuid4().hex),
    ]

    def publish(request: GraphPublicationRequest):
        try:
            return publish_course_version(course.id, request)
        except ConceptGraphPublicationConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, requests))

    successes = [
        item for item in results if not isinstance(item, Exception)
    ]
    conflicts = [
        item
        for item in results
        if isinstance(item, ConceptGraphPublicationConflictError)
    ]
    assert len(successes) == 1
    assert successes[0].version_number == 1
    assert len(conflicts) == 1
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_graph_versions"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_graph_publication_operations"
        ).fetchone()[0] == 1


def test_same_concurrent_operation_replays_one_sealed_version() -> None:
    course, _, chunk = make_course_source("concurrent-replay")
    accepted_concept(course.id, chunk, "Alpha", "Alpha")
    preview = preview_course_publication(course.id)
    request = _request(preview, uuid4().hex)

    with ThreadPoolExecutor(max_workers=2) as executor:
        versions = list(
            executor.map(
                lambda _: publish_course_version(course.id, request),
                range(2),
            )
        )

    assert [item.version_number for item in versions] == [1, 1]
    assert versions[0].content_hash == versions[1].content_hash
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_graph_versions"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_graph_publication_operations"
        ).fetchone()[0] == 1


@pytest.mark.parametrize(
    "stage",
    ["after_children", "after_version_seal", "after_head", "after_receipt"],
)
def test_fault_injection_rolls_back_every_publication_stage(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    course, _, chunk = make_course_source(f"rollback-{stage}")
    accepted_concept(course.id, chunk, "Alpha", "Alpha")
    preview = preview_course_publication(course.id)

    def fail_at_stage(current_stage: str) -> None:
        if current_stage == stage:
            raise publication_store.PublicationIntegrityError(
                "Injected publication failure."
            )

    monkeypatch.setattr(
        publication_store, "_publication_fault_hook", fail_at_stage
    )
    request = _request(preview, uuid4().hex)
    with pytest.raises(ConceptGraphPublicationPersistenceError):
        publish_course_version(course.id, request)

    with connect() as conn:
        for table in (
            "concept_graph_versions",
            "concept_graph_version_heads",
            "concept_graph_version_concepts",
            "concept_graph_version_concept_aliases",
            "concept_graph_version_concept_evidence",
            "concept_graph_version_relations",
            "concept_graph_version_relation_evidence",
            "concept_graph_publication_operations",
        ):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0

    monkeypatch.setattr(publication_store, "_publication_fault_hook", None)
    recovered = publish_course_version(course.id, request)
    assert recovered.version_number == 1
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_graph_versions"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_graph_version_heads"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM concept_graph_publication_operations"
        ).fetchone()[0] == 1


def test_publish_and_draft_edit_serialize_to_whole_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course, _, chunk = make_course_source("publish-vs-edit")
    concept = accepted_concept(course.id, chunk, "Alpha", "Alpha")
    preview = preview_course_publication(course.id)
    entered = Event()
    release = Event()

    def pause_after_children(stage: str) -> None:
        if stage == "after_children":
            entered.set()
            assert release.wait(5)

    monkeypatch.setattr(
        publication_store, "_publication_fault_hook", pause_after_children
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        publishing = executor.submit(
            publish_course_version,
            course.id,
            _request(preview, uuid4().hex),
        )
        assert entered.wait(5)
        editing = executor.submit(
            edit_course_concept,
            course.id,
            concept.id,
            ConceptRevisionEdit(
                operation_id=uuid4().hex,
                expected_revision=concept.revision,
                actor="author@example.test",
                reason="Edit immediately after the publication snapshot.",
                preferred_name="Alpha",
                short_definition="A post-publication draft revision.",
                evidence=[{"chunk_id": chunk.id, "quote": "Alpha"}],
            ),
        )
        release.set()
        version = publishing.result(timeout=10)
        edited = editing.result(timeout=10)

    assert version.version_number == 1
    assert edited.revision > concept.revision
    with connect() as conn:
        stored_revision = conn.execute(
            """
            SELECT concept_revision
            FROM concept_graph_version_concepts
            WHERE course_id = ? AND version_number = 1 AND concept_id = ?
            """,
            (course.id, concept.id),
        ).fetchone()[0]
    assert stored_revision == concept.revision


def test_publish_and_source_update_never_mix_authority_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course, source, chunk = make_course_source("publish-vs-source")
    accepted_concept(course.id, chunk, "Alpha", "Alpha")
    preview = preview_course_publication(course.id)
    entered = Event()
    release = Event()

    def pause_after_children(stage: str) -> None:
        if stage == "after_children":
            entered.set()
            assert release.wait(5)

    changed_text = "A replacement projection without the reviewed quote."
    changed_chunk = CourseSourceChunk(
        id=f"{chunk.id}-new",
        source_id=source.id,
        origin_type="source_unit",
        origin_id=f"{chunk.origin_id}-new",
        chunk_type="page",
        ordinal=0,
        text=changed_text,
        text_hash=hash_source_chunk_text(changed_text),
        locator=PdfPageLocator(asset_id=source.origin_id, page_number=1),
        chunker_version="publication-test-v2",
    )
    monkeypatch.setattr(
        publication_store, "_publication_fault_hook", pause_after_children
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        publishing = executor.submit(
            publish_course_version,
            course.id,
            _request(preview, uuid4().hex),
        )
        assert entered.wait(5)
        replacing = executor.submit(
            replace_source_projection, source, [changed_chunk]
        )
        release.set()
        version = publishing.result(timeout=10)
        replacing.result(timeout=10)

    assert version.source_authority_current is True
    with connect() as conn:
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM concept_graph_version_concept_evidence
            WHERE course_id = ? AND version_number = 1
              AND chunk_id = ?
            """,
            (course.id, chunk.id),
        ).fetchone()[0] == 1
