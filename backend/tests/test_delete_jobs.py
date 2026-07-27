from fastapi.testclient import TestClient

import app.main as main
from app.job import VideoJob, VideoJobStatus
from app.job_store import create_job, get_job
from app.knowledge_card import KnowledgeCard
from app.knowledge_card_store import (
    create_card,
    get_card,
    list_cards_for_job,
)


client = TestClient(main.app)


def test_delete_job_preserves_cards_and_artifacts_until_purge(
    monkeypatch,
    tmp_path,
):
    data_dir = tmp_path / "data"
    upload_dir = data_dir / "uploads"
    audio_dir = data_dir / "audio"
    transcript_dir = data_dir / "transcripts"
    upload_dir.mkdir(parents=True)
    audio_dir.mkdir(parents=True)
    transcript_dir.mkdir(parents=True)

    video_path = upload_dir / "job-123.mp4"
    transcript_path = transcript_dir / "job-123.json"
    audio_path = audio_dir / "job-123.wav"
    video_path.write_bytes(b"video")
    transcript_path.write_text("{}", encoding="utf-8")
    audio_path.write_bytes(b"audio")

    job = VideoJob(
        id="job-123",
        video_path=video_path,
        status=VideoJobStatus.completed,
        transcript_path=transcript_path,
    )
    create_job(job)
    create_card(
        KnowledgeCard(
            id="card-123",
            job_id=job.id,
            title="Linear Algebra",
            summary="A saved card.",
            claims=[
                {
                    "text": "Linear algebra is important.",
                    "evidence": [
                        {
                            "quote": "Linear algebra is important.",
                            "segment_start_seconds": 0.0,
                            "segment_end_seconds": 5.0,
                        }
                    ],
                }
            ],
            source_start_seconds=0.0,
            source_end_seconds=5.0,
        )
    )

    monkeypatch.setattr(main, "DATA_DIR", data_dir)

    response = client.delete(f"/jobs/{job.id}")

    assert response.status_code == 204
    assert get_job(job.id) is None
    assert list_cards_for_job(job.id) == []
    assert get_job(job.id, include_deleted=True) is not None
    assert get_card("card-123", include_deleted=True) is not None
    assert video_path.exists()
    assert transcript_path.exists()
    assert audio_path.exists()

    trash_item = next(
        item
        for item in client.get("/trash").json()
        if item["entity_type"] == "video_job"
        and item["entity_id"] == job.id
    )
    purge_response = client.delete(f"/trash/{trash_item['id']}")
    assert purge_response.status_code == 200
    assert get_job(job.id, include_deleted=True) is None
    assert get_card("card-123", include_deleted=True) is None
    assert not video_path.exists()
    assert not transcript_path.exists()
    assert not audio_path.exists()


def test_delete_job_returns_404_for_missing_job():
    response = client.delete("/jobs/missing-job")

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Job not found."
    }
