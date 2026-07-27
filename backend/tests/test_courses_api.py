from fastapi.testclient import TestClient

import app.job_service as job_service
import app.main as main
import app.course_source_service as course_source_service
from app.course import DEFAULT_COURSE_ID
from app.course_source_store import get_source
from app.job import VideoJob, VideoJobStatus
from app.job_store import create_job, get_job
from app.knowledge_card import KnowledgeCard
from app.knowledge_card_store import create_card, list_cards_for_job


client = TestClient(main.app)


def create_course(title: str = "Linear Algebra") -> dict:
    response = client.post(
        "/courses",
        json={
            "title": title,
            "description": "Course notes",
        },
    )

    assert response.status_code == 201

    return response.json()


def create_uploaded_job(
    tmp_path,
    *,
    job_id: str,
    course_id: str = DEFAULT_COURSE_ID,
) -> VideoJob:
    job = VideoJob(
        id=job_id,
        course_id=course_id,
        video_path=tmp_path / f"{job_id}.mp4",
        status=VideoJobStatus.completed,
    )
    create_job(job)

    return job


def save_card(job_id: str, card_id: str) -> KnowledgeCard:
    card = KnowledgeCard(
        id=card_id,
        job_id=job_id,
        title="Matrix Factorization",
        summary="SVD factors a matrix into structured pieces.",
        claims=[
            {
                "text": "SVD is a matrix factorization.",
                "evidence": [
                    {
                        "quote": "singular value decomposition",
                        "segment_start_seconds": 1.0,
                        "segment_end_seconds": 2.0,
                    }
                ],
            }
        ],
        source_start_seconds=1.0,
        source_end_seconds=2.0,
    )
    create_card(card)

    return card


def test_default_course_is_created():
    response = client.get("/courses")

    assert response.status_code == 200

    courses = response.json()

    assert any(course["id"] == DEFAULT_COURSE_ID for course in courses)


def test_create_and_update_course():
    course = create_course()

    update_response = client.patch(
        f"/courses/{course['id']}",
        json={
            "title": "MIT 18.065",
            "description": "Matrix methods and deep learning",
        },
    )

    assert update_response.status_code == 200

    updated = update_response.json()

    assert updated["title"] == "MIT 18.065"
    assert updated["description"] == "Matrix methods and deep learning"


def test_upload_video_assigns_course(monkeypatch, tmp_path):
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

    course = create_course()
    response = client.post(
        "/videos",
        data={
            "course_id": course["id"],
        },
        files={
            "video": (
                "lecture.mp4",
                b"fake video content",
                "video/mp4",
            )
        },
    )

    assert response.status_code == 201

    data = response.json()
    job = get_job(data["id"])

    assert data["course_id"] == course["id"]
    assert job is not None
    assert job.course_id == course["id"]


def test_list_course_jobs(tmp_path):
    course = create_course()
    create_uploaded_job(
        tmp_path,
        job_id="course-job",
        course_id=course["id"],
    )
    create_uploaded_job(
        tmp_path,
        job_id="default-job",
    )

    response = client.get(f"/courses/{course['id']}/jobs")

    assert response.status_code == 200
    assert [
        job["id"]
        for job in response.json()
    ] == ["course-job"]


def test_list_course_card_index_includes_note_counts(tmp_path):
    course = create_course()
    course_job = create_uploaded_job(
        tmp_path,
        job_id="course-job",
        course_id=course["id"],
    )
    default_job = create_uploaded_job(tmp_path, job_id="default-job")
    course_card = save_card(course_job.id, "course-card")
    save_card(default_job.id, "default-card")

    note_response = client.post(
        f"/cards/{course_card.id}/notes",
        json={
            "note_type": "user_note",
            "title": "My note",
            "body": "This card is important.",
            "source": "user",
            "sources": [],
        },
    )

    assert note_response.status_code == 201

    response = client.get(f"/courses/{course['id']}/card-index")

    assert response.status_code == 200

    data = response.json()

    assert len(data) == 1

    item = data[0]

    assert item["id"] == course_card.id
    assert item["job_id"] == course_job.id
    assert item["title"] == "Matrix Factorization"
    assert item["summary"] == "SVD factors a matrix into structured pieces."
    assert item["card_kind"] == "concept"
    assert item["tags"] == []
    assert item["content_status"] == "draft"
    assert item["review_item_count"] == 0
    assert item["source_video"] == course_job.id
    assert item["source_start_seconds"] == 1.0
    assert item["source_end_seconds"] == 2.0
    assert item["note_count"] == 1
    assert item["created_at"]
    assert item["updated_at"]


def test_delete_course_moves_jobs_to_default(tmp_path):
    course = create_course()
    job = create_uploaded_job(
        tmp_path,
        job_id="course-job",
        course_id=course["id"],
    )
    course_source_service.sync_video_source(job.id)
    source_response = client.get(f"/courses/{course['id']}/sources")
    assert source_response.status_code == 200

    response = client.delete(f"/courses/{course['id']}")

    assert response.status_code == 204
    assert get_job(job.id).course_id == DEFAULT_COURSE_ID
    source = get_source(f"job:{job.id}")
    assert source is not None
    assert source.course_id == DEFAULT_COURSE_ID


def test_delete_default_course_is_rejected():
    response = client.delete(f"/courses/{DEFAULT_COURSE_ID}")

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Default course cannot be deleted."
    }


def test_delete_all_job_cards(tmp_path):
    job = create_uploaded_job(tmp_path, job_id="job-1")
    save_card(job.id, "card-1")
    save_card(job.id, "card-2")

    response = client.delete(f"/jobs/{job.id}/cards")

    assert response.status_code == 204
    assert list_cards_for_job(job.id) == []


def test_delete_all_course_cards_only_clears_that_course(tmp_path):
    course = create_course()
    course_job = create_uploaded_job(
        tmp_path,
        job_id="course-job",
        course_id=course["id"],
    )
    default_job = create_uploaded_job(tmp_path, job_id="default-job")
    save_card(course_job.id, "course-card")
    save_card(default_job.id, "default-card")

    response = client.delete(f"/courses/{course['id']}/cards")

    assert response.status_code == 204
    assert list_cards_for_job(course_job.id) == []
    assert [
        card.id
        for card in list_cards_for_job(default_job.id)
    ] == ["default-card"]
