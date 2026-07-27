from __future__ import annotations

from pathlib import Path
from threading import Event, Thread

from fastapi.testclient import TestClient
import pytest

import app.main as main
import app.course_source_service as course_source_service
import app.source_index_service as source_index_service
import app.source_search_service as source_search_service
from app.course import Course, CourseCreate, DEFAULT_COURSE_ID
from app.course_service import create_video_course, delete_video_course
from app.course_source import VideoTimeLocator
from app.course_source_store import get_source
from app.job import VideoJob, VideoJobStatus
from app.job_store import create_job
from app.source_asset import SourceAsset, SourceUnit
from app.source_asset_store import create_source_asset, replace_source_units
from app.transcript_chunk import TranscriptChunk
from app.transcript_chunk_store import replace_chunks_for_job


client = TestClient(main.app)


class FakeEmbedder:
    model_name = "test/course-source-embedding-v1"
    embedding_dimension = 2

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed_texts(
        self,
        texts,
        *,
        batch_size=None,
    ) -> list[list[float]]:
        values = list(texts)
        self.calls.append(values)
        return [self._vector(text) for text in values]

    @staticmethod
    def _vector(text: str) -> list[float]:
        lowered = text.lower()
        if "both sources" in lowered:
            return [1.0, 1.0]
        if "learning rate" in lowered or "update size" in lowered:
            return [0.0, 1.0]
        if "gradient" in lowered:
            return [1.0, 0.0]
        return [0.5, 0.5]


def _create_course(title: str = "Optimization") -> Course:
    return create_video_course(CourseCreate(title=title))


def _create_video_with_chunk(
    tmp_path: Path,
    *,
    course_id: str,
    job_id: str,
    text: str = "Gradient descent follows the negative gradient.",
) -> VideoJob:
    job = VideoJob(
        id=job_id,
        course_id=course_id,
        video_path=tmp_path / f"{job_id}.mp4",
        status=VideoJobStatus.completed,
        original_filename=f"{job_id}.mp4",
        size_bytes=2048,
    )
    create_job(job)
    replace_chunks_for_job(
        job.id,
        [
            TranscriptChunk(
                id=f"{job.id}-chunk-1",
                course_id=course_id,
                job_id=job.id,
                chunk_index=0,
                start_seconds=12.5,
                end_seconds=42.0,
                text=text,
                segment_ids=[3, 4],
            )
        ],
    )
    course_source_service.sync_video_source(job.id)
    return job


def _create_document(
    tmp_path: Path,
    *,
    course_id: str,
    asset_id: str,
    asset_type: str,
    filename: str,
    unit_id: str,
    unit_type: str,
    text: str,
    locator: dict[str, object],
    ordinal: int = 0,
    job_id: str | None = None,
) -> SourceAsset:
    asset = SourceAsset(
        id=asset_id,
        course_id=course_id,
        job_id=job_id,
        asset_type=asset_type,
        original_filename=filename,
        stored_path=str(tmp_path / filename),
        mime_type="application/octet-stream",
        size_bytes=1024,
        sha256="a" * 64,
        extraction_status="ready",
    )
    create_source_asset(asset)
    replace_source_units(
        asset.id,
        [
            SourceUnit(
                id=unit_id,
                asset_id=asset.id,
                unit_type=unit_type,
                ordinal=ordinal,
                text=text,
                locator=locator,
            )
        ],
    )
    course_source_service.sync_source_asset(asset.id)
    return asset


def _patch_fake_embedders(monkeypatch) -> FakeEmbedder:
    fake = FakeEmbedder()
    monkeypatch.setattr(
        source_index_service,
        "SentenceTransformerEmbedder",
        lambda: fake,
    )
    monkeypatch.setattr(
        source_search_service,
        "SentenceTransformerEmbedder",
        lambda: fake,
    )
    return fake


def test_course_sources_unify_video_and_documents_with_typed_locators(
    tmp_path: Path,
) -> None:
    course = _create_course()
    job = _create_video_with_chunk(
        tmp_path,
        course_id=course.id,
        job_id="lecture-1",
    )
    assets = [
        _create_document(
            tmp_path,
            course_id=course.id,
            asset_id="pdf-1",
            asset_type="pdf",
            filename="notes.pdf",
            unit_id="pdf-page-1",
            unit_type="page",
            text="The learning rate controls update size.",
            locator={"page_number": 7},
        ),
        _create_document(
            tmp_path,
            course_id=course.id,
            asset_id="slides-1",
            asset_type="pptx",
            filename="slides.pptx",
            unit_id="slide-1",
            unit_type="slide",
            text="Momentum accumulates a velocity vector.",
            locator={"slide_number": 2},
        ),
        _create_document(
            tmp_path,
            course_id=course.id,
            asset_id="handout-1",
            asset_type="docx",
            filename="handout.docx",
            unit_id="paragraph-1",
            unit_type="paragraph",
            text="Weight decay regularizes model parameters.",
            locator={"paragraph_number": 4},
        ),
        _create_document(
            tmp_path,
            course_id=course.id,
            asset_id="readme-1",
            asset_type="text",
            filename="lesson.md",
            unit_id="section-1",
            unit_type="text",
            text="Early stopping uses validation performance.",
            locator={"section_number": 3, "heading": "Regularization"},
        ),
    ]

    response = client.get(f"/courses/{course.id}/sources")

    assert response.status_code == 200
    sources = {source["id"]: source for source in response.json()}
    assert set(sources) == {
        f"job:{job.id}",
        *(f"asset:{asset.id}" for asset in assets),
    }
    assert sources[f"job:{job.id}"]["source_type"] == "video"
    assert sources[f"job:{job.id}"]["chunk_count"] == 1
    assert sources["asset:pdf-1"]["source_type"] == "pdf"
    assert sources["asset:readme-1"]["title"] == "lesson.md"
    assert all(source["content_status"] == "ready" for source in sources.values())

    expected_locators = {
        f"job:{job.id}": {
            "kind": "video_time",
            "job_id": job.id,
            "start_seconds": 12.5,
            "end_seconds": 42.0,
            "segment_ids": [3, 4],
        },
        "asset:pdf-1": {
            "kind": "pdf_page",
            "asset_id": "pdf-1",
            "page_number": 7,
        },
        "asset:slides-1": {
            "kind": "ppt_slide",
            "asset_id": "slides-1",
            "slide_number": 2,
        },
        "asset:handout-1": {
            "kind": "docx_paragraph",
            "asset_id": "handout-1",
            "paragraph_number": 4,
        },
        "asset:readme-1": {
            "kind": "text_section",
            "asset_id": "readme-1",
            "section_number": 3,
            "metadata": {"heading": "Regularization"},
        },
    }
    for source_id, expected in expected_locators.items():
        chunks_response = client.get(f"/sources/{source_id}/chunks")

        assert chunks_response.status_code == 200
        chunks = chunks_response.json()
        assert len(chunks) == 1
        for key, value in expected.items():
            assert chunks[0]["locator"][key] == value


def test_media_source_asset_locator_uses_linked_video_job(
    tmp_path: Path,
) -> None:
    course = _create_course()
    job = _create_video_with_chunk(
        tmp_path,
        course_id=course.id,
        job_id="lecture-1",
    )
    asset = _create_document(
        tmp_path,
        course_id=course.id,
        asset_id="frames-1",
        asset_type="video",
        filename="frames.json",
        unit_id="frame-1",
        unit_type="video_frame",
        text="The slide shows the gradient update equation.",
        locator={"timestamp_seconds": 18.0},
        job_id=job.id,
    )

    response = client.get(f"/sources/asset:{asset.id}/chunks")

    assert response.status_code == 200
    locator = response.json()[0]["locator"]
    assert locator["kind"] == "video_time"
    assert locator["job_id"] == job.id
    assert locator["asset_id"] is None
    assert locator["start_seconds"] == 18.0
    assert locator["end_seconds"] == 18.0


def test_source_selection_rejects_cross_course_ids(
    monkeypatch,
    tmp_path: Path,
) -> None:
    first_course = _create_course("Course A")
    second_course = _create_course("Course B")
    first_job = _create_video_with_chunk(
        tmp_path,
        course_id=first_course.id,
        job_id="course-a-video",
    )
    second_job = _create_video_with_chunk(
        tmp_path,
        course_id=second_course.id,
        job_id="course-b-video",
    )
    _patch_fake_embedders(monkeypatch)

    list_response = client.get(f"/courses/{first_course.id}/sources")
    assert list_response.status_code == 200
    assert [source["id"] for source in list_response.json()] == [
        f"job:{first_job.id}"
    ]

    index_response = client.post(
        f"/courses/{first_course.id}/sources/index",
        json={"source_ids": [f"job:{second_job.id}"]},
    )
    search_response = client.post(
        f"/courses/{first_course.id}/sources/search",
        json={
            "question": "What is gradient descent?",
            "source_ids": [f"job:{second_job.id}"],
        },
    )

    assert index_response.status_code == 400
    assert "do not belong to this course" in index_response.json()["detail"]
    assert search_response.status_code == 400
    assert "do not belong to this course" in search_response.json()["detail"]


def test_enabled_state_survives_source_reads_and_controls_default_selection(
    monkeypatch,
    tmp_path: Path,
) -> None:
    course = _create_course()
    job = _create_video_with_chunk(
        tmp_path,
        course_id=course.id,
        job_id="lecture-1",
    )
    _patch_fake_embedders(monkeypatch)

    update_response = client.patch(
        f"/sources/job:{job.id}",
        json={"enabled": False},
    )

    assert update_response.status_code == 200
    assert update_response.json()["enabled"] is False
    refreshed = client.get(f"/courses/{course.id}/sources")
    assert refreshed.status_code == 200
    assert refreshed.json()[0]["enabled"] is False

    search_response = client.post(
        f"/courses/{course.id}/sources/search",
        json={"question": "What is gradient descent?"},
    )

    assert search_response.status_code == 200
    assert search_response.json() == {
        "question": "What is gradient descent?",
        "results": [],
    }


def test_source_reads_do_not_rebuild_the_course_projection(
    monkeypatch,
    tmp_path: Path,
) -> None:
    course = _create_course()
    job = _create_video_with_chunk(
        tmp_path,
        course_id=course.id,
        job_id="lecture-1",
    )

    def reject_reconciliation(*args, **kwargs):
        raise AssertionError("Read endpoints must not rebuild source data.")

    monkeypatch.setattr(
        course_source_service,
        "reconcile_course_sources",
        reject_reconciliation,
    )

    assert client.get(f"/courses/{course.id}/sources").status_code == 200
    assert client.get(f"/sources/job:{job.id}").status_code == 200
    assert client.get(f"/sources/job:{job.id}/chunks").status_code == 200


def test_indexing_skips_unchanged_chunks_and_reembeds_stale_text(
    monkeypatch,
    tmp_path: Path,
) -> None:
    course = _create_course()
    job = _create_video_with_chunk(
        tmp_path,
        course_id=course.id,
        job_id="lecture-1",
    )
    asset = _create_document(
        tmp_path,
        course_id=course.id,
        asset_id="pdf-1",
        asset_type="pdf",
        filename="notes.pdf",
        unit_id="pdf-page-1",
        unit_type="page",
        text="The learning rate controls update size.",
        locator={"page_number": 7},
    )
    fake = _patch_fake_embedders(monkeypatch)

    first = client.post(
        f"/courses/{course.id}/sources/index",
        json={"source_ids": [f"job:{job.id}", f"asset:{asset.id}"]},
    )
    second = client.post(
        f"/courses/{course.id}/sources/index",
        json={"source_ids": [f"job:{job.id}", f"asset:{asset.id}"]},
    )

    assert first.status_code == 200
    assert first.json()["embedded_chunks"] == 2
    assert first.json()["skipped_chunks"] == 0
    assert first.json()["dimension"] == 2
    assert second.status_code == 200
    assert second.json()["embedded_chunks"] == 0
    assert second.json()["skipped_chunks"] == 2
    assert fake.calls == [
        [
            "Gradient descent follows the negative gradient.",
            "The learning rate controls update size.",
        ]
    ]

    replace_chunks_for_job(
        job.id,
        [
            TranscriptChunk(
                id=f"{job.id}-chunk-1",
                course_id=course.id,
                job_id=job.id,
                chunk_index=0,
                start_seconds=12.5,
                end_seconds=42.0,
                text="The gradient is now explained with updated wording.",
                segment_ids=[3, 4],
            )
        ],
    )
    course_source_service.sync_video_source(job.id)
    stale = client.get(f"/sources/job:{job.id}")

    assert stale.status_code == 200
    assert stale.json()["index_status"] == "stale"

    reindexed = client.post(
        f"/courses/{course.id}/sources/index",
        json={"source_ids": [f"job:{job.id}"]},
    )

    assert reindexed.status_code == 200
    assert reindexed.json()["embedded_chunks"] == 1
    assert reindexed.json()["skipped_chunks"] == 0
    assert fake.calls[-1] == [
        "The gradient is now explained with updated wording."
    ]
    ready = client.get(f"/sources/job:{job.id}")
    assert ready.json()["index_status"] == "ready"
    assert ready.json()["index_model"] == FakeEmbedder.model_name
    assert ready.json()["index_dimension"] == 2
    assert ready.json()["indexed_chunk_count"] == 1


def test_search_combines_video_and_document_chunks_and_honors_selection(
    monkeypatch,
    tmp_path: Path,
) -> None:
    course = _create_course()
    job = _create_video_with_chunk(
        tmp_path,
        course_id=course.id,
        job_id="lecture-1",
    )
    asset = _create_document(
        tmp_path,
        course_id=course.id,
        asset_id="pdf-1",
        asset_type="pdf",
        filename="notes.pdf",
        unit_id="pdf-page-1",
        unit_type="page",
        text="The learning rate controls update size.",
        locator={"page_number": 7},
    )
    _patch_fake_embedders(monkeypatch)

    mixed_response = client.post(
        f"/courses/{course.id}/sources/search",
        json={
            "question": "Compare both sources",
            "top_k": 5,
        },
    )

    assert mixed_response.status_code == 200
    mixed = mixed_response.json()
    assert mixed["question"] == "Compare both sources"
    assert {result["source_id"] for result in mixed["results"]} == {
        f"job:{job.id}",
        f"asset:{asset.id}",
    }
    result_by_source = {
        result["source_id"]: result
        for result in mixed["results"]
    }
    assert (
        result_by_source[f"job:{job.id}"]["locator"]["kind"]
        == "video_time"
    )
    assert (
        result_by_source[f"asset:{asset.id}"]["locator"]["kind"]
        == "pdf_page"
    )

    selected_response = client.post(
        f"/courses/{course.id}/sources/search",
        json={
            "question": "Compare both sources",
            "source_ids": [f"asset:{asset.id}"],
            "top_k": 5,
        },
    )

    assert selected_response.status_code == 200
    assert [
        result["source_id"]
        for result in selected_response.json()["results"]
    ] == [f"asset:{asset.id}"]


def test_search_reindexes_when_same_model_name_changes_dimension(
    monkeypatch,
    tmp_path: Path,
) -> None:
    course = _create_course()
    job = _create_video_with_chunk(
        tmp_path,
        course_id=course.id,
        job_id="lecture-1",
    )
    _patch_fake_embedders(monkeypatch)
    first = client.post(
        f"/courses/{course.id}/sources/index",
        json={"source_ids": [f"job:{job.id}"]},
    )
    assert first.status_code == 200
    assert first.json()["dimension"] == 2

    class ThreeDimensionEmbedder(FakeEmbedder):
        embedding_dimension = 3

        @staticmethod
        def _vector(text: str) -> list[float]:
            if "gradient" in text.lower():
                return [1.0, 0.0, 0.0]
            return [0.5, 0.5, 0.5]

    monkeypatch.setattr(
        source_index_service,
        "SentenceTransformerEmbedder",
        ThreeDimensionEmbedder,
    )
    monkeypatch.setattr(
        source_search_service,
        "SentenceTransformerEmbedder",
        ThreeDimensionEmbedder,
    )

    response = client.post(
        f"/courses/{course.id}/sources/search",
        json={
            "question": "What is gradient descent?",
            "source_ids": [f"job:{job.id}"],
        },
    )

    assert response.status_code == 200
    assert len(response.json()["results"]) == 1
    source = client.get(f"/sources/job:{job.id}").json()
    assert source["index_status"] == "ready"
    assert source["index_dimension"] == 3
    assert source["indexed_chunk_count"] == 1


def test_direct_index_reindexes_when_same_model_name_changes_dimension(
    monkeypatch,
    tmp_path: Path,
) -> None:
    course = _create_course()
    job = _create_video_with_chunk(
        tmp_path,
        course_id=course.id,
        job_id="lecture-1",
    )
    _patch_fake_embedders(monkeypatch)
    first = client.post(
        f"/courses/{course.id}/sources/index",
        json={"source_ids": [f"job:{job.id}"]},
    )
    assert first.status_code == 200
    assert first.json()["dimension"] == 2

    class ThreeDimensionEmbedder(FakeEmbedder):
        embedding_dimension = 3

        @staticmethod
        def _vector(text: str) -> list[float]:
            return [1.0, 0.0, 0.0]

    replacement = ThreeDimensionEmbedder()
    monkeypatch.setattr(
        source_index_service,
        "SentenceTransformerEmbedder",
        lambda: replacement,
    )

    second = client.post(
        f"/courses/{course.id}/sources/index",
        json={"source_ids": [f"job:{job.id}"]},
    )

    assert second.status_code == 200
    assert second.json()["embedded_chunks"] == 1
    assert second.json()["dimension"] == 3
    source = client.get(f"/sources/job:{job.id}").json()
    assert source["index_dimension"] == 3
    assert source["indexed_chunk_count"] == 1


def test_direct_index_probes_undeclared_model_dimension_changes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    course = _create_course()
    job = _create_video_with_chunk(
        tmp_path,
        course_id=course.id,
        job_id="lecture-1",
    )

    class UndeclaredDimensionEmbedder:
        model_name = "same-model"

        def __init__(self, dimension: int) -> None:
            self.dimension = dimension
            self.calls: list[list[str]] = []

        def embed_texts(self, texts, *, batch_size=None):
            values = list(texts)
            self.calls.append(values)
            return [
                [1.0, *([0.0] * (self.dimension - 1))]
                for _ in values
            ]

    first_embedder = UndeclaredDimensionEmbedder(2)
    monkeypatch.setattr(
        source_index_service,
        "SentenceTransformerEmbedder",
        lambda: first_embedder,
    )
    first = client.post(
        f"/courses/{course.id}/sources/index",
        json={"source_ids": [f"job:{job.id}"]},
    )
    assert first.status_code == 200
    assert first.json()["dimension"] == 2

    second_embedder = UndeclaredDimensionEmbedder(3)
    monkeypatch.setattr(
        source_index_service,
        "SentenceTransformerEmbedder",
        lambda: second_embedder,
    )
    second = client.post(
        f"/courses/{course.id}/sources/index",
        json={"source_ids": [f"job:{job.id}"]},
    )

    assert second.status_code == 200
    assert second.json()["embedded_chunks"] == 1
    assert second.json()["dimension"] == 3
    assert len(second_embedder.calls) == 1


def test_index_runtime_failure_is_sanitized_and_marks_source_failed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    course = _create_course()
    job = _create_video_with_chunk(
        tmp_path,
        course_id=course.id,
        job_id="lecture-1",
    )

    class FailingEmbedder(FakeEmbedder):
        def embed_texts(self, texts, *, batch_size=None):
            raise RuntimeError(
                r"backend OOM at C:\Users\alice\private-model"
            )

    monkeypatch.setattr(
        source_index_service,
        "SentenceTransformerEmbedder",
        FailingEmbedder,
    )

    response = client.post(
        f"/courses/{course.id}/sources/index",
        json={"source_ids": [f"job:{job.id}"]},
    )

    assert response.status_code == 503
    source = client.get(f"/sources/job:{job.id}").json()
    assert source["index_status"] == "failed"
    assert source["index_error"] == source_index_service.SAFE_MODEL_FAILURE
    assert "alice" not in source["index_error"]


def test_older_failed_index_cannot_overwrite_newer_ready_state(
    tmp_path: Path,
) -> None:
    course = _create_course()
    job = _create_video_with_chunk(
        tmp_path,
        course_id=course.id,
        job_id="lecture-1",
    )
    started = Event()
    release = Event()
    failures: list[Exception] = []

    class BlockingFailingEmbedder(FakeEmbedder):
        def embed_texts(self, texts, *, batch_size=None):
            started.set()
            assert release.wait(timeout=5)
            raise RuntimeError("older index failed")

    def run_older_index() -> None:
        try:
            source_index_service.index_course_sources(
                course.id,
                embedder=BlockingFailingEmbedder(),
            )
        except Exception as exc:
            failures.append(exc)

    older = Thread(target=run_older_index)
    older.start()
    assert started.wait(timeout=5)

    newer = source_index_service.index_course_sources(
        course.id,
        embedder=FakeEmbedder(),
    )
    release.set()
    older.join(timeout=5)

    assert not older.is_alive()
    assert len(failures) == 1
    assert isinstance(
        failures[0],
        source_index_service.SourceIndexGenerationError,
    )
    assert newer.embedded_chunks == 1
    source = get_source(f"job:{job.id}")
    assert source is not None
    assert source.index_status == "ready"
    assert source.indexed_chunk_count == source.chunk_count == 1


def test_index_compare_and_swap_rejects_course_move_during_embedding(
    monkeypatch,
    tmp_path: Path,
) -> None:
    course = _create_course()
    job = _create_video_with_chunk(
        tmp_path,
        course_id=course.id,
        job_id="lecture-1",
    )

    class MovingEmbedder(FakeEmbedder):
        def embed_texts(self, texts, *, batch_size=None):
            vectors = super().embed_texts(
                texts,
                batch_size=batch_size,
            )
            delete_video_course(course.id)
            return vectors

    monkeypatch.setattr(
        source_index_service,
        "SentenceTransformerEmbedder",
        MovingEmbedder,
    )

    response = client.post(
        f"/courses/{course.id}/sources/index",
        json={"source_ids": [f"job:{job.id}"]},
    )

    assert response.status_code == 409
    source = client.get(f"/sources/job:{job.id}").json()
    assert source["course_id"] == DEFAULT_COURSE_ID
    assert source["index_status"] == "stale"
    assert source["indexed_chunk_count"] == 0


def test_search_final_reads_exclude_source_moved_to_another_course(
    monkeypatch,
    tmp_path: Path,
) -> None:
    course = _create_course()
    _create_video_with_chunk(
        tmp_path,
        course_id=course.id,
        job_id="lecture-1",
    )
    _patch_fake_embedders(monkeypatch)
    original_list = source_search_service.list_chunks_for_course_sources
    moved = False

    def move_course_before_final_read(course_id, source_ids):
        nonlocal moved
        if not moved:
            moved = True
            delete_video_course(course.id)
        return original_list(course_id, source_ids)

    monkeypatch.setattr(
        source_search_service,
        "list_chunks_for_course_sources",
        move_course_before_final_read,
    )

    response = client.post(
        f"/courses/{course.id}/sources/search",
        json={"question": "What is gradient descent?"},
    )

    assert moved is True
    assert response.status_code == 200
    assert response.json()["results"] == []


def test_search_validates_course_before_loading_embedding_model(
    monkeypatch,
) -> None:
    fake = FakeEmbedder()
    monkeypatch.setattr(
        source_search_service,
        "SentenceTransformerEmbedder",
        lambda: fake,
    )

    response = client.post(
        "/courses/missing/sources/search",
        json={"question": "What is gradient descent?"},
    )

    assert response.status_code == 404
    assert fake.calls == []


def test_search_runtime_failure_uses_sanitized_service_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    course = _create_course()
    _create_video_with_chunk(
        tmp_path,
        course_id=course.id,
        job_id="lecture-1",
    )

    class FailingEmbedder(FakeEmbedder):
        def embed_texts(self, texts, *, batch_size=None):
            raise RuntimeError(
                r"backend OOM at C:\Users\alice\private-model"
            )

    monkeypatch.setattr(
        source_search_service,
        "SentenceTransformerEmbedder",
        FailingEmbedder,
    )

    response = client.post(
        f"/courses/{course.id}/sources/search",
        json={"question": "What is gradient descent?"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": (
            "Source search failed. Check the local model settings "
            "and retry."
        )
    }


def test_empty_course_search_does_not_load_embedding_model(
    monkeypatch,
) -> None:
    course = _create_course()
    fake = FakeEmbedder()
    monkeypatch.setattr(
        source_search_service,
        "SentenceTransformerEmbedder",
        lambda: fake,
    )

    response = client.post(
        f"/courses/{course.id}/sources/search",
        json={"question": "What is gradient descent?"},
    )

    assert response.status_code == 200
    assert response.json()["results"] == []
    assert fake.calls == []


def test_source_get_miss_is_pure_and_does_not_repair_projection(
    tmp_path: Path,
) -> None:
    course = _create_course()
    job = VideoJob(
        id="origin-only",
        course_id=course.id,
        video_path=tmp_path / "origin-only.mp4",
        status=VideoJobStatus.uploaded,
    )
    create_job(job)
    assert get_source(f"job:{job.id}") is None

    response = client.get(f"/sources/job:{job.id}")

    assert response.status_code == 404
    assert get_source(f"job:{job.id}") is None


def test_video_time_locator_requires_exactly_one_owner() -> None:
    with pytest.raises(ValueError):
        VideoTimeLocator(
            start_seconds=1,
            end_seconds=2,
        )
    with pytest.raises(ValueError):
        VideoTimeLocator(
            job_id="job-1",
            asset_id="asset-1",
            start_seconds=1,
            end_seconds=2,
        )


def test_index_compare_and_swap_rejects_source_changed_during_embedding(
    monkeypatch,
    tmp_path: Path,
) -> None:
    course = _create_course()
    job = _create_video_with_chunk(
        tmp_path,
        course_id=course.id,
        job_id="lecture-1",
    )

    class MutatingEmbedder(FakeEmbedder):
        def embed_texts(self, texts, *, batch_size=None):
            values = list(texts)
            vectors = super().embed_texts(
                values,
                batch_size=batch_size,
            )
            if values and "negative gradient" in values[0]:
                replace_chunks_for_job(
                    job.id,
                    [
                        TranscriptChunk(
                            id=f"{job.id}-chunk-1",
                            course_id=course.id,
                            job_id=job.id,
                            chunk_index=0,
                            start_seconds=12.5,
                            end_seconds=42.0,
                            text="The source changed during embedding.",
                            segment_ids=[3, 4],
                        )
                    ],
                )
                course_source_service.sync_video_source(job.id)
            return vectors

    monkeypatch.setattr(
        source_index_service,
        "SentenceTransformerEmbedder",
        MutatingEmbedder,
    )

    response = client.post(
        f"/courses/{course.id}/sources/index",
        json={"source_ids": [f"job:{job.id}"]},
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Sources changed while indexing. Please retry."
    }
    source = client.get(f"/sources/job:{job.id}").json()
    assert source["index_status"] == "stale"
    assert source["indexed_chunk_count"] == 0


def test_missing_source_endpoints_return_404() -> None:
    assert client.get("/sources/job:missing").status_code == 404
    assert client.get("/sources/job:missing/chunks").status_code == 404
    assert client.patch(
        "/sources/job:missing",
        json={"enabled": False},
    ).status_code == 404
