from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.learning_document_service as learning_document_service
import app.main as main
import app.source_asset_service as source_asset_service
from app.course import DEFAULT_COURSE_ID
from app.job import VideoJob, VideoJobStatus
from app.job_store import create_job
from app.learning_document import LearningDocumentGenerateRequest


client = TestClient(main.app)


class FakeStudyLLM:
    settings = SimpleNamespace(provider="ollama", model="qwen-test")

    def create_chat_completion(self, messages, **kwargs) -> str:
        assert "[C1]" in messages[-1].content
        assert "[S1]" in messages[-1].content
        return """# Backpropagation Study

## Overview

Backpropagation applies the chain rule [C1].

## Examples

The imported notes provide a worked explanation [S1].
"""


class CardOnlyStudyLLM:
    settings = SimpleNamespace(provider="ollama", model="qwen-test")

    def __init__(self) -> None:
        self.calls = 0

    def create_chat_completion(self, messages, **kwargs) -> str:
        self.calls += 1
        assert "[C1]" in messages[-1].content
        return "# Backpropagation Study\n\nThe chain rule applies [C1]."


def create_card(tmp_path) -> dict:
    job = VideoJob(
        id="study-job",
        video_path=tmp_path / "lecture.mp4",
        status=VideoJobStatus.completed,
        original_filename="lecture.mp4",
    )
    create_job(job)
    response = client.post(
        f"/jobs/{job.id}/cards",
        json={
            "title": "Backpropagation",
            "summary": "Backpropagation computes gradients.",
            "claims": [
                {
                    "text": "Backpropagation applies the chain rule.",
                    "evidence": [
                        {
                            "quote": "apply the chain rule backward",
                            "segment_start_seconds": 10.0,
                            "segment_end_seconds": 14.0,
                        }
                    ],
                }
            ],
            "source_start_seconds": 10.0,
            "source_end_seconds": 20.0,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_learning_document_crud_and_versions(tmp_path):
    card = create_card(tmp_path)

    create_response = client.post(
        f"/cards/{card['id']}/learning-documents",
        json={},
    )

    assert create_response.status_code == 201
    document = create_response.json()
    assert document["title"] == "Understanding Backpropagation"
    assert document["card_links"][0]["role"] == "primary_anchor"
    assert len(document["versions"]) == 1

    update_response = client.patch(
        f"/learning-documents/{document['id']}",
        json={"body_markdown": "# Edited\n\nManual explanation."},
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert len(updated["versions"]) == 2
    assert updated["versions"][0]["version_number"] == 2

    restore_response = client.post(
        f"/learning-documents/{document['id']}/restore",
        json={"version_number": 1},
    )
    assert restore_response.status_code == 200
    assert "Start writing" in restore_response.json()["body_markdown"]
    assert len(restore_response.json()["versions"]) == 3


def test_import_text_and_generate_grounded_document(tmp_path, monkeypatch):
    card = create_card(tmp_path)
    paths = main.get_app_path_settings().model_copy(
        update={"source_dir": tmp_path / "sources"}
    )
    monkeypatch.setattr(
        source_asset_service,
        "get_app_path_settings",
        lambda: paths,
    )
    upload_response = client.post(
        f"/courses/{DEFAULT_COURSE_ID}/source-assets",
        files={
            "file": (
                "backprop-notes.md",
                b"Backpropagation uses local derivatives.\n\nA worked graph example.",
                "text/markdown",
            )
        },
    )
    assert upload_response.status_code == 201
    upload = upload_response.json()
    assert upload["asset"]["extraction_status"] == "ready"
    assert upload["asset"]["unit_count"] == 1

    document = client.post(
        f"/cards/{card['id']}/learning-documents",
        json={"title": "Backpropagation Study"},
    ).json()
    monkeypatch.setattr(main, "get_llm_client", lambda: FakeStudyLLM())
    generation_response = client.post(
        f"/learning-documents/{document['id']}/generate",
        json={"source_asset_ids": [upload["asset"]["id"]]},
    )

    assert generation_response.status_code == 200
    result = generation_response.json()
    assert result["selected_source_units"] == 1
    assert "[C1]" in result["document"]["body_markdown"]
    assert "[S1]" in result["document"]["body_markdown"]
    assert {source["source_type"] for source in result["document"]["sources"]} == {
        "card_claim",
        "source_unit",
    }


def test_generation_checkpoint_and_operation_id_prevent_partial_or_duplicate_publish(
    tmp_path,
):
    card = create_card(tmp_path)
    supporting_response = client.post(
        "/jobs/study-job/cards",
        json={
            "title": "Chain rule",
            "summary": "The chain rule composes local derivatives.",
            "claims": [
                {
                    "text": "Local derivatives are composed.",
                    "evidence": [
                        {
                            "quote": "compose local derivatives",
                            "segment_start_seconds": 22.0,
                            "segment_end_seconds": 25.0,
                        }
                    ],
                }
            ],
            "source_start_seconds": 20.0,
            "source_end_seconds": 30.0,
        },
    )
    assert supporting_response.status_code == 201
    supporting_card = supporting_response.json()
    document = client.post(
        f"/cards/{card['id']}/learning-documents",
        json={"title": "Atomic generation"},
    ).json()
    request = LearningDocumentGenerateRequest(
        supporting_card_ids=[supporting_card["id"]],
    )
    initial_body = document["body_markdown"]
    initial_version_count = len(document["versions"])
    canceled_llm = CardOnlyStudyLLM()

    class CanceledBeforePublish(RuntimeError):
        pass

    def cancel_before_publish() -> None:
        raise CanceledBeforePublish()

    with pytest.raises(CanceledBeforePublish):
        learning_document_service.generate_learning_document(
            document["id"],
            request,
            llm_client=canceled_llm,
            checkpoint=cancel_before_publish,
            operation_id="study-task-canceled",
        )

    after_cancel = client.get(
        f"/learning-documents/{document['id']}"
    ).json()
    assert after_cancel["body_markdown"] == initial_body
    assert len(after_cancel["versions"]) == initial_version_count
    assert [link["role"] for link in after_cancel["card_links"]] == [
        "primary_anchor"
    ]

    successful_llm = CardOnlyStudyLLM()
    first = learning_document_service.generate_learning_document(
        document["id"],
        request,
        llm_client=successful_llm,
        checkpoint=lambda: None,
        operation_id="study-task-stable",
    )

    class MustNotRunLLM:
        settings = SimpleNamespace(provider="ollama", model="qwen-test")

        def create_chat_completion(self, messages, **kwargs) -> str:
            raise AssertionError("An applied operation must replay without LLM.")

    replay = learning_document_service.generate_learning_document(
        document["id"],
        request,
        llm_client=MustNotRunLLM(),
        checkpoint=lambda: None,
        operation_id="study-task-stable",
    )

    assert successful_llm.calls == 1
    assert replay.document.body_markdown == first.document.body_markdown
    assert {link.card_id for link in replay.document.card_links} == {
        card["id"],
        supporting_card["id"],
    }
    assert len(replay.document.versions) == initial_version_count + 1
    assert {
        version.id
        for version in replay.document.versions
        if version.id == "study-task-stable"
    } == {"study-task-stable"}


def test_course_map_reports_learning_coverage(tmp_path):
    card = create_card(tmp_path)
    client.post(f"/cards/{card['id']}/learning-documents", json={})

    response = client.get(f"/courses/{DEFAULT_COURSE_ID}/map")

    assert response.status_code == 200
    coverage = response.json()["coverage"]
    assert coverage["total_cards"] == 1
    assert coverage["cards_with_learning_documents"] == 1
    assert coverage["learning_document_count"] == 1
    assert coverage["unsorted_card_count"] == 1
