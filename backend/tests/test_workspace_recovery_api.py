import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import reliable_task_store


client = TestClient(main.app)


def test_backup_api_create_list_validate_download_and_queue_restore():
    created_response = client.post("/workspace/backups")

    assert created_response.status_code == 201
    created = created_response.json()
    assert created["valid"] is True
    assert created["id"].endswith(".vcc-backup")
    assert created["archive_sha256"]

    listed_response = client.get("/workspace/backups")
    assert listed_response.status_code == 200
    assert [item["id"] for item in listed_response.json()] == [
        created["id"]
    ]

    validated_response = client.get(
        f"/workspace/backups/{created['id']}/validate"
    )
    assert validated_response.status_code == 200
    assert (
        validated_response.json()["archive_sha256"]
        == created["archive_sha256"]
    )

    download_response = client.get(
        f"/workspace/backups/{created['id']}/download"
    )
    assert download_response.status_code == 200
    assert download_response.content.startswith(b"PK")

    queued_response = client.post(
        f"/workspace/backups/{created['id']}/restore"
    )
    assert queued_response.status_code == 202
    queued = queued_response.json()
    assert queued["backup_sha256"] == created["archive_sha256"]
    assert queued["restore_id"]
    assert queued["phase"] == "queued"
    assert queued["workspace_generation"] == 1

    conflicting_response = client.post(
        f"/workspace/backups/{created['id']}/restore"
    )
    assert conflicting_response.status_code == 409

    status_response = client.get("/workspace/restore-status")
    assert status_response.status_code == 200
    restore_status = status_response.json()
    assert restore_status["workspace_generation"] == 1
    assert restore_status["pending"]["restore_id"] == queued["restore_id"]

    canceled_response = client.delete(
        f"/workspace/restore-pending/{queued['restore_id']}"
    )
    assert canceled_response.status_code == 200
    assert canceled_response.json()["status"] == "canceled"

    final_status = client.get("/workspace/restore-status").json()
    assert final_status["pending"] is None
    assert final_status["last_result"]["restore_id"] == queued["restore_id"]
    assert final_status["last_result"]["status"] == "canceled"


def test_backup_api_imports_only_valid_vcc_archives():
    created = client.post("/workspace/backups").json()
    exported = client.get(
        f"/workspace/backups/{created['id']}/download"
    ).content

    imported_response = client.post(
        "/workspace/backups/import",
        files={
            "backup": (
                "portable.vcc-backup",
                exported,
                "application/zip",
            )
        },
    )

    assert imported_response.status_code == 201
    imported = imported_response.json()
    assert imported["valid"] is True
    assert imported["id"] != created["id"]

    invalid_response = client.post(
        "/workspace/backups/import",
        files={
            "backup": (
                "broken.vcc-backup",
                b"not-a-zip",
                "application/zip",
            )
        },
    )
    assert invalid_response.status_code == 400


def test_backup_api_rejects_path_traversal_ids():
    response = client.get(
        "/workspace/backups/..%2Foutside.vcc-backup/validate"
    )

    assert response.status_code in {400, 404}


def test_workspace_mutations_reject_active_durable_tasks():
    created = client.post("/workspace/backups").json()
    exported = client.get(
        f"/workspace/backups/{created['id']}/download"
    ).content
    reliable_task_store.reserve_task(
        kind="chat_generation",
        payload={"conversation_id": "conversation-1"},
        course_id="course-1",
        resource_type="chat_conversation",
        resource_id="conversation-1",
    )

    create_response = client.post("/workspace/backups")
    assert create_response.status_code == 409
    assert "background activity" in create_response.json()["detail"]

    import_response = client.post(
        "/workspace/backups/import",
        files={
            "backup": (
                "portable.vcc-backup",
                exported,
                "application/zip",
            )
        },
    )
    assert import_response.status_code == 409

    restore_response = client.post(
        f"/workspace/backups/{created['id']}/restore"
    )
    assert restore_response.status_code == 409


def test_lifespan_rolls_back_when_restored_workspace_fails_initialization(
    monkeypatch: pytest.MonkeyPatch,
):
    staged = SimpleNamespace(
        restore_id="restore-1",
        status="staged",
    )
    failed = SimpleNamespace(
        restore_id="restore-1",
        status="failed",
    )
    initialize = Mock(
        side_effect=[RuntimeError("bad restored schema"), None]
    )
    rollback = Mock(return_value=failed)
    finalize = Mock()
    manager = Mock()
    get_manager = Mock(return_value=manager)
    get_manager.cache_clear = Mock()
    monkeypatch.setattr(
        main.workspace_backup,
        "apply_pending_workspace_restore",
        Mock(return_value=staged),
    )
    monkeypatch.setattr(
        main,
        "_initialize_workspace_before_task_dispatch",
        initialize,
    )
    monkeypatch.setattr(
        main.workspace_backup,
        "rollback_pending_workspace_restore",
        rollback,
    )
    monkeypatch.setattr(
        main.workspace_backup,
        "finalize_pending_workspace_restore",
        finalize,
    )
    monkeypatch.setattr(main, "get_reliable_task_manager", get_manager)

    async def run_lifespan() -> None:
        async with main.lifespan(main.app):
            assert main.app.state.workspace_restore_result is failed

    asyncio.run(run_lifespan())

    assert initialize.call_count == 2
    rollback.assert_called_once()
    finalize.assert_not_called()
    manager.start.assert_called_once_with(recover=False)
