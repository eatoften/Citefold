import hashlib

from fastapi.testclient import TestClient

import app.main as main
import app.job_service as job_service
from app.job import (
    VideoJob,
    VideoJobStatus,
)
from app.job_store import create_job, get_job, get_job_video_sha256


client = TestClient(main.app)


def test_upload_video_creates_job(monkeypatch, tmp_path):
    monkeypatch.setattr(
        main,
        "UPLOAD_DIR",
        tmp_path,
    )

    def fake_probe_video(file_path):
        assert file_path.is_file()

        return {
            "streams": [
                {
                    "codec_type": "video",
                }
            ],
            "format": {},
        }

    monkeypatch.setattr(job_service, "probe_video", fake_probe_video)

    video_content = b"fake video content"

    files = {
        "video": (
            "lecture.mp4",
            video_content,
            "video/mp4",
        )
    }

    response = client.post(
        "/videos",
        files=files,
    )

    assert response.status_code == 201

    data = response.json()
    job_id = data["id"]

    assert data["status"] == "uploaded"

    job = get_job(job_id)

    assert job is not None
    assert job.id == job_id
    assert job.status == VideoJobStatus.uploaded
    assert job.original_filename == "lecture.mp4"
    assert job.stored_name == data["stored_name"]
    assert job.size_bytes == len(video_content)
    assert job.video_path == (
        tmp_path / data["stored_name"]
    )
    assert job.metadata is None
    assert job.created_at is not None
    assert job.updated_at is not None
    assert get_job_video_sha256(job.id) == hashlib.sha256(
        video_content
    ).hexdigest()




def test_get_job_returns_stored_job(tmp_path):
    video_path = tmp_path / "lecture.mp4"

    job = VideoJob(
        id="job-123",
        video_path=video_path,
        status=VideoJobStatus.uploaded,
    )

    create_job(job)

    response = client.get(
        "/jobs/job-123"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["id"] == "job-123"
    assert data["video_path"] == str(video_path)
    assert data["status"] == "uploaded"
    assert data["metadata"] is None


def test_list_jobs_returns_stored_jobs(tmp_path):
    first_job = VideoJob(
        id="job-1",
        video_path=tmp_path / "first.mp4",
        status=VideoJobStatus.uploaded,
    )
    second_job = VideoJob(
        id="job-2",
        video_path=tmp_path / "second.mp4",
        status=VideoJobStatus.failed,
    )

    create_job(first_job)
    create_job(second_job)

    response = client.get("/jobs")

    assert response.status_code == 200

    job_ids = {
        item["id"]
        for item in response.json()
    }

    assert job_ids == {"job-1", "job-2"}



def test_get_job_returns_404_when_job_does_not_exist():
    response = client.get(
        "/jobs/missing-job"
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Job not found."
    }
