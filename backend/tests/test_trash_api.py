from fastapi.testclient import TestClient

import app.main as main


client = TestClient(main.app)


def _create_course(title: str) -> dict:
    response = client.post(
        "/courses",
        json={"title": title, "description": "Recoverable workspace"},
    )
    assert response.status_code == 201
    return response.json()


def test_course_delete_restore_and_permanent_purge_round_trip():
    course = _create_course("Recover me")

    assert client.delete(f"/courses/{course['id']}").status_code == 204
    assert all(
        item["id"] != course["id"]
        for item in client.get("/courses").json()
    )

    trash = client.get("/trash").json()
    item = next(
        entry
        for entry in trash
        if entry["entity_type"] == "course"
        and entry["entity_id"] == course["id"]
    )
    restored_response = client.post(f"/trash/{item['id']}/restore")
    assert restored_response.status_code == 200
    assert any(
        entry["id"] == course["id"]
        for entry in client.get("/courses").json()
    )

    assert client.delete(f"/courses/{course['id']}").status_code == 204
    second_item = next(
        entry
        for entry in client.get("/trash").json()
        if entry["entity_type"] == "course"
        and entry["entity_id"] == course["id"]
    )
    purge_response = client.delete(f"/trash/{second_item['id']}")
    assert purge_response.status_code == 200
    assert all(
        entry["entity_id"] != course["id"]
        for entry in client.get("/trash").json()
    )
