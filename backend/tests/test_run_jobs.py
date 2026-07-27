from pathlib import Path
from threading import Event

from fastapi.testclient import TestClient

import app.job_service as job_service
import app.main as main
from app.job import (
    VideoJob,
    VideoJobStatus,
)
from app.job_store import create_job as save_job
from app.job_store import get_job
from app.media_metadata import VideoMetadata
from app.reliable_task import ReliableTaskStatus


client = TestClient(main.app)


def wait_for_response_task(response):
    task_id = response.headers["X-Task-ID"]
    return main.get_reliable_task_manager().wait_for_task(
        task_id,
        {
            ReliableTaskStatus.succeeded,
            ReliableTaskStatus.failed,
            ReliableTaskStatus.canceled,
        },
        timeout_seconds=5.0,
    )


def create_job(
    tmp_path: Path,
    status: VideoJobStatus = VideoJobStatus.uploaded,
) -> VideoJob:
    video_path = tmp_path / "lecture.mp4"
    video_path.write_bytes(b"fake video")

    job = VideoJob(
        id="job-123",
        video_path=video_path,
        status=status,
    )

    save_job(job)

    return job


def test_run_job_completes_uploaded_job(
    monkeypatch,
    tmp_path,
):
    job = create_job(tmp_path)

    metadata = VideoMetadata(
        duration_seconds=10.0,
        width=1920,
        height=1080,
        video_codec="h264",
        has_audio=True,
    )

    transcript_path = (
        tmp_path
        / "transcripts"
        / "lecture.json"
    )

    calls = []

    class FakePipeline:
        def process(
            self,
            video_path,
            artifact_root,
            job,
            on_job_update=None,
        ):
            calls.append("process")

            assert video_path == job.video_path
            assert artifact_root == main.DATA_DIR

            job.status = VideoJobStatus.probing
            job.metadata = metadata
            job.transcript_path = transcript_path
            job.status = VideoJobStatus.completed

    fake_pipeline = FakePipeline()

    monkeypatch.setattr(
        main,
        "get_video_pipeline",
        lambda: fake_pipeline,
    )

    response = client.post(
        f"/jobs/{job.id}/run"
    )

    assert response.status_code == 202

    data = response.json()

    assert data["id"] == job.id
    assert data["status"] == "probing"
    assert data["metadata"] is None
    assert data["transcript_path"] is None
    assert data["error_message"] is None

    assert wait_for_response_task(response).status == (
        ReliableTaskStatus.succeeded
    )
    assert calls == ["process"]

    stored_job = get_job(job.id)

    assert stored_job is not None
    assert stored_job.status == VideoJobStatus.completed
    assert stored_job.metadata == metadata
    assert stored_job.transcript_path == transcript_path
    assert stored_job.started_at is not None
    assert stored_job.completed_at is not None
    assert stored_job.updated_at >= stored_job.started_at


def test_cancel_after_video_completion_keeps_task_and_job_completed(
    monkeypatch,
    tmp_path,
):
    job = create_job(tmp_path)
    domain_committed = Event()
    return_from_commit = Event()
    original_save_job_progress = job_service.save_job_progress

    def block_after_completed_commit(updated_job):
        original_save_job_progress(updated_job)
        if updated_job.status == VideoJobStatus.completed:
            domain_committed.set()
            assert return_from_commit.wait(5)

    class PublishingPipeline:
        def process(
            self,
            video_path,
            artifact_root,
            job,
            on_job_update=None,
        ):
            job.transcript_path = tmp_path / "transcript.json"
            job.status = VideoJobStatus.completed
            assert on_job_update is not None
            on_job_update(job)

    monkeypatch.setattr(
        job_service,
        "save_job_progress",
        block_after_completed_commit,
    )
    monkeypatch.setattr(
        main,
        "get_video_pipeline",
        lambda: PublishingPipeline(),
    )

    response = client.post(f"/jobs/{job.id}/run")
    manager = main.get_reliable_task_manager()
    task_id = response.headers["X-Task-ID"]
    try:
        assert domain_committed.wait(5)
        canceling = manager.request_cancel(task_id)
        assert canceling.status == ReliableTaskStatus.canceling
        return_from_commit.set()
        completed = manager.wait_for_task(
            task_id,
            {ReliableTaskStatus.succeeded},
            timeout_seconds=5,
        )
    finally:
        return_from_commit.set()

    stored_job = get_job(job.id)
    assert completed.cancel_requested_at is not None
    assert stored_job is not None
    assert stored_job.status == VideoJobStatus.completed


def test_cancel_before_video_completion_still_cancels_task_and_job(
    monkeypatch,
    tmp_path,
):
    job = create_job(tmp_path)
    interim_progress_committed = Event()
    return_from_progress = Event()
    original_save_job_progress = job_service.save_job_progress

    def block_during_transcription(updated_job):
        original_save_job_progress(updated_job)
        if updated_job.status == VideoJobStatus.transcribing:
            interim_progress_committed.set()
            assert return_from_progress.wait(5)

    class InProgressPipeline:
        def process(
            self,
            video_path,
            artifact_root,
            job,
            on_job_update=None,
        ):
            job.status = VideoJobStatus.transcribing
            assert on_job_update is not None
            on_job_update(job)
            raise AssertionError("Cancellation must stop before publication.")

    monkeypatch.setattr(
        job_service,
        "save_job_progress",
        block_during_transcription,
    )
    monkeypatch.setattr(
        main,
        "get_video_pipeline",
        lambda: InProgressPipeline(),
    )

    response = client.post(f"/jobs/{job.id}/run")
    manager = main.get_reliable_task_manager()
    task_id = response.headers["X-Task-ID"]
    try:
        assert interim_progress_committed.wait(5)
        manager.request_cancel(task_id)
        return_from_progress.set()
        canceled = manager.wait_for_task(
            task_id,
            {ReliableTaskStatus.canceled},
            timeout_seconds=5,
        )
    finally:
        return_from_progress.set()

    stored_job = get_job(job.id)
    assert canceled.cancel_requested_at is not None
    assert stored_job is not None
    assert stored_job.status == VideoJobStatus.canceled


def test_run_job_returns_404_for_missing_job():
    response = client.post(
        "/jobs/missing-job/run"
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Job not found."
    }


def test_run_job_returns_409_when_job_is_not_uploaded(
    monkeypatch,
    tmp_path,
):
    job = create_job(
        tmp_path,
        status=VideoJobStatus.completed,
    )

    def fail_if_pipeline_is_requested():
        raise AssertionError(
            "Pipeline must not load for a completed job"
        )

    monkeypatch.setattr(
        main,
        "get_video_pipeline",
        fail_if_pipeline_is_requested,
    )

    response = client.post(
        f"/jobs/{job.id}/run"
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": (
            "Job cannot run from status: completed"
        )
    }

    assert job.status == VideoJobStatus.completed


def test_run_job_marks_job_failed_when_pipeline_fails(
    monkeypatch,
    tmp_path,
):
    job = create_job(tmp_path)

    class FailingPipeline:
        def process(
            self,
            video_path,
            artifact_root,
            job,
            on_job_update=None,
        ):
            job.status = VideoJobStatus.transcribing

            raise RuntimeError(
                "Whisper inference failed"
            )

    monkeypatch.setattr(
        main,
        "get_video_pipeline",
        lambda: FailingPipeline(),
    )

    response = client.post(
        f"/jobs/{job.id}/run"
    )

    assert response.status_code == 202

    assert wait_for_response_task(response).status == (
        ReliableTaskStatus.failed
    )
    stored_job = get_job(job.id)

    assert stored_job is not None
    assert stored_job.status == VideoJobStatus.failed
    assert (
        stored_job.error_message
        == "Whisper inference failed"
    )
    assert stored_job.completed_at is not None


def test_retry_job_completes_failed_job(
    monkeypatch,
    tmp_path,
):
    job = create_job(
        tmp_path,
        status=VideoJobStatus.failed,
    )

    metadata = VideoMetadata(
        duration_seconds=10.0,
        width=1280,
        height=720,
        video_codec="h264",
        has_audio=True,
    )
    transcript_path = tmp_path / "retry-transcript.json"

    class FakePipeline:
        def process(
            self,
            video_path,
            artifact_root,
            job,
            on_job_update=None,
        ):
            job.metadata = metadata
            job.transcript_path = transcript_path
            job.status = VideoJobStatus.completed

    monkeypatch.setattr(
        main,
        "get_video_pipeline",
        lambda: FakePipeline(),
    )

    response = client.post(
        f"/jobs/{job.id}/retry"
    )

    assert response.status_code == 202
    assert response.json()["status"] == "probing"

    assert wait_for_response_task(response).status == (
        ReliableTaskStatus.succeeded
    )
    stored_job = get_job(job.id)

    assert stored_job is not None
    assert stored_job.status == VideoJobStatus.completed
    assert stored_job.error_message is None
    assert stored_job.metadata == metadata
    assert stored_job.transcript_path == transcript_path


def test_retry_job_returns_409_when_job_is_not_failed(
    monkeypatch,
    tmp_path,
):
    job = create_job(tmp_path)

    def fail_if_pipeline_is_requested():
        raise AssertionError(
            "Pipeline must not load for a non-failed job"
        )

    monkeypatch.setattr(
        main,
        "get_video_pipeline",
        fail_if_pipeline_is_requested,
    )

    response = client.post(
        f"/jobs/{job.id}/retry"
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Job cannot retry from status: uploaded"
    }
