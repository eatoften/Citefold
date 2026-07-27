from fastapi.testclient import TestClient

import app.main as main
from app.main import app

client = TestClient(app)


def test_health_check():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "application_id": "video-course-cards",
        "api_version": 1,
        "instance_token": None,
    }


def test_health_exposes_the_owned_instance_token(monkeypatch):
    monkeypatch.setenv("VCC_BACKEND_INSTANCE_TOKEN", "owned-token")

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["instance_token"] == "owned-token"


def test_quiesce_requires_the_owned_instance_token(monkeypatch):
    monkeypatch.setenv("VCC_BACKEND_INSTANCE_TOKEN", "owned-token")

    response = client.post("/runtime/quiesce")

    assert response.status_code == 403


def test_quiesce_reports_only_confirmed_idle(monkeypatch):
    class FakeManager:
        def __init__(self, result: bool) -> None:
            self.result = result

        def quiesce(self, *, timeout_seconds: float) -> bool:
            assert timeout_seconds == 4.0
            return self.result

    monkeypatch.setenv("VCC_BACKEND_INSTANCE_TOKEN", "owned-token")
    monkeypatch.setattr(
        main,
        "get_reliable_task_manager",
        lambda: FakeManager(True),
    )
    response = client.post(
        "/runtime/quiesce",
        headers={"X-VCC-Instance-Token": "owned-token"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "quiesced"
    assert response.json()["instance_token"] == "owned-token"

    monkeypatch.setattr(
        main,
        "get_reliable_task_manager",
        lambda: FakeManager(False),
    )
    response = client.post(
        "/runtime/quiesce",
        headers={"X-VCC-Instance-Token": "owned-token"},
    )
    assert response.status_code == 409
    assert response.json()["status"] == "timeout"
