from fastapi.testclient import TestClient

import app.main as main
from app.course import DEFAULT_COURSE_ID
from app.job import VideoJob, VideoJobStatus
from app.job_store import create_job
from app.card_embedding import CardEmbedding
from app.card_embedding_store import upsert_card_embeddings


client = TestClient(main.app)


def create_job_and_card(tmp_path) -> dict:
    job = VideoJob(
        id="topic-job",
        video_path=tmp_path / "lecture.mp4",
        status=VideoJobStatus.completed,
        original_filename="lecture.mp4",
    )
    create_job(job)
    response = client.post(
        f"/jobs/{job.id}/cards",
        json={
            "card_kind": "concept",
            "title": "Backpropagation",
            "summary": "Backpropagation computes gradients.",
            "key_points": ["It applies the chain rule."],
            "claims": [
                {
                    "text": "Backpropagation applies the chain rule.",
                    "evidence": [
                        {
                            "quote": "we recursively apply the chain rule",
                            "segment_start_seconds": 10.0,
                            "segment_end_seconds": 15.0,
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


def test_course_map_creates_unsorted_and_assigns_cards(tmp_path):
    card = create_job_and_card(tmp_path)

    response = client.get(f"/courses/{DEFAULT_COURSE_ID}/map")

    assert response.status_code == 200
    payload = response.json()
    unsorted = next(topic for topic in payload["topics"] if topic["is_system"])
    assert unsorted["title"] == "Unsorted"
    membership = next(
        item for item in payload["memberships"] if item["card_id"] == card["id"]
    )
    assert membership["topic_id"] == unsorted["id"]
    assert membership["role"] == "primary"


def test_create_nested_topics_and_move_card(tmp_path):
    card = create_job_and_card(tmp_path)
    root = client.post(
        f"/courses/{DEFAULT_COURSE_ID}/topics",
        json={"title": "Neural Networks"},
    ).json()
    child_response = client.post(
        f"/courses/{DEFAULT_COURSE_ID}/topics",
        json={
            "title": "Optimization",
            "parent_topic_id": root["id"],
        },
    )
    assert child_response.status_code == 201
    child = child_response.json()
    assert child["depth"] == 1

    move_response = client.put(
        f"/cards/{card['id']}/primary-topic",
        json={"topic_id": child["id"]},
    )
    assert move_response.status_code == 200
    assert move_response.json()["topic_id"] == child["id"]

    course_map = client.get(f"/courses/{DEFAULT_COURSE_ID}/map").json()
    primary = [
        item
        for item in course_map["memberships"]
        if item["card_id"] == card["id"] and item["role"] == "primary"
    ]
    assert len(primary) == 1
    assert primary[0]["topic_id"] == child["id"]


def test_topic_cycle_is_rejected(tmp_path):
    create_job_and_card(tmp_path)
    root = client.post(
        f"/courses/{DEFAULT_COURSE_ID}/topics",
        json={"title": "Root"},
    ).json()
    child = client.post(
        f"/courses/{DEFAULT_COURSE_ID}/topics",
        json={"title": "Child", "parent_topic_id": root["id"]},
    ).json()

    response = client.patch(
        f"/topics/{root['id']}",
        json={"parent_topic_id": child["id"]},
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Topic hierarchy cannot contain a cycle."
    }


def test_create_topic_relation_and_delete_topic(tmp_path):
    card = create_job_and_card(tmp_path)
    first = client.post(
        f"/courses/{DEFAULT_COURSE_ID}/topics",
        json={"title": "Calculus"},
    ).json()
    second = client.post(
        f"/courses/{DEFAULT_COURSE_ID}/topics",
        json={"title": "Backpropagation"},
    ).json()
    relation_response = client.post(
        f"/courses/{DEFAULT_COURSE_ID}/topic-relations",
        json={
            "source_topic_id": first["id"],
            "target_topic_id": second["id"],
            "relation_type": "prerequisite",
            "explanation": "Calculus introduces the chain rule.",
        },
    )
    assert relation_response.status_code == 201

    client.put(
        f"/cards/{card['id']}/primary-topic",
        json={"topic_id": second["id"]},
    )
    delete_response = client.delete(f"/topics/{second['id']}")
    assert delete_response.status_code == 204

    course_map = client.get(f"/courses/{DEFAULT_COURSE_ID}/map").json()
    unsorted = next(topic for topic in course_map["topics"] if topic["is_system"])
    membership = next(
        item
        for item in course_map["memberships"]
        if item["card_id"] == card["id"]
    )
    assert membership["topic_id"] == unsorted["id"]
    assert course_map["topic_relations"] == []


def test_suggest_topics_from_unsorted_embeddings_and_accept(tmp_path):
    job = VideoJob(
        id="cluster-job",
        video_path=tmp_path / "lecture.mp4",
        status=VideoJobStatus.completed,
    )
    create_job(job)
    cards = []
    for index, (title, tag) in enumerate(
        [
            ("Gradient Descent", "optimization"),
            ("Momentum", "optimization"),
            ("Convolution", "cnn"),
            ("Pooling", "cnn"),
        ]
    ):
        response = client.post(
            f"/jobs/{job.id}/cards",
            json={
                "title": title,
                "summary": f"{title} summary.",
                "claims": [
                    {
                        "text": f"{title} claim.",
                        "evidence": [
                            {
                                "quote": f"{title} evidence.",
                                "segment_start_seconds": index + 1.0,
                                "segment_end_seconds": index + 2.0,
                            }
                        ],
                    }
                ],
                "tags": [tag],
                "source_start_seconds": index + 1.0,
                "source_end_seconds": index + 3.0,
            },
        )
        cards.append(response.json())
    upsert_card_embeddings(
        [
            CardEmbedding(
                card_id=card["id"],
                model="sentence-transformers/all-MiniLM-L6-v2",
                dimension=2,
                text_hash=f"hash-{index}",
                vector=vector,
            )
            for index, (card, vector) in enumerate(
                zip(
                    cards,
                    ([1.0, 0.0], [0.95, 0.05], [0.0, 1.0], [0.05, 0.95]),
                    strict=True,
                )
            )
        ]
    )

    response = client.post(
        f"/courses/{DEFAULT_COURSE_ID}/topics/suggest",
        json={"target_topic_count": 2, "use_local_llm": False},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["eligible_cards"] == 4
    assert len(result["suggested_topics"]) == 2
    assert result["suggested_memberships"] == 4
    assert result["singleton_topic_count"] == 0
    assert result["largest_topic_size"] == 2
    assert result["cluster_sizes"] == [2, 2]
    assert result["mean_coherence"] > 0.9
    suggested_topic = result["suggested_topics"][0]

    course_map = client.get(f"/courses/{DEFAULT_COURSE_ID}/map").json()
    suggested_memberships = [
        item
        for item in course_map["memberships"]
        if item["topic_id"] == suggested_topic["id"]
    ]
    assert suggested_memberships
    assert all(item["status"] == "suggested" for item in suggested_memberships)

    accept_response = client.post(f"/topics/{suggested_topic['id']}/accept")
    assert accept_response.status_code == 200
    assert accept_response.json()["status"] == "accepted"


def test_delete_course_hides_and_restores_topic_subtree(tmp_path):
    course = client.post(
        "/courses",
        json={"title": "Temporary course"},
    ).json()
    job = VideoJob(
        id="moved-course-job",
        course_id=course["id"],
        video_path=tmp_path / "lecture.mp4",
        status=VideoJobStatus.completed,
    )
    create_job(job)
    card = client.post(
        f"/jobs/{job.id}/cards",
        json={
            "title": "Preserved card",
            "summary": "This card survives course deletion.",
            "claims": [
                {
                    "text": "The card remains available.",
                    "evidence": [
                        {
                            "quote": "the card remains available",
                            "segment_start_seconds": 1.0,
                            "segment_end_seconds": 2.0,
                        }
                    ],
                }
            ],
            "source_start_seconds": 1.0,
            "source_end_seconds": 3.0,
        },
    ).json()
    topic = client.post(
        f"/courses/{course['id']}/topics",
        json={"title": "Temporary topic"},
    ).json()
    client.put(
        f"/cards/{card['id']}/primary-topic",
        json={"topic_id": topic["id"]},
    )

    response = client.delete(f"/courses/{course['id']}")

    assert response.status_code == 204
    assert client.get(f"/courses/{course['id']}/map").status_code == 404
    assert client.get(f"/jobs/{job.id}").status_code == 404
    default_map = client.get(f"/courses/{DEFAULT_COURSE_ID}/map").json()
    assert topic["id"] not in {item["id"] for item in default_map["topics"]}
    assert card["id"] not in {
        item["card_id"] for item in default_map["memberships"]
    }

    trash_item = next(
        item
        for item in client.get("/trash").json()
        if item["entity_type"] == "course"
        and item["entity_id"] == course["id"]
    )
    assert client.post(
        f"/trash/{trash_item['id']}/restore"
    ).status_code == 200
    restored_map = client.get(f"/courses/{course['id']}/map").json()
    assert topic["id"] in {
        item["id"] for item in restored_map["topics"]
    }
    membership = next(
        item
        for item in restored_map["memberships"]
        if item["card_id"] == card["id"]
    )
    assert membership["topic_id"] == topic["id"]


def test_split_and_merge_topics(tmp_path):
    first_card = create_job_and_card(tmp_path)
    second_job = VideoJob(
        id="second-topic-job",
        video_path=tmp_path / "second.mp4",
        status=VideoJobStatus.completed,
    )
    create_job(second_job)
    second_card = client.post(
        f"/jobs/{second_job.id}/cards",
        json={
            "title": "Optimization",
            "summary": "Optimization updates parameters.",
            "claims": [
                {
                    "text": "Optimization updates parameters.",
                    "evidence": [
                        {
                            "quote": "update the parameters",
                            "segment_start_seconds": 4.0,
                            "segment_end_seconds": 5.0,
                        }
                    ],
                }
            ],
            "source_start_seconds": 4.0,
            "source_end_seconds": 8.0,
        },
    ).json()
    source_topic = client.post(
        f"/courses/{DEFAULT_COURSE_ID}/topics",
        json={"title": "Neural Networks"},
    ).json()
    for card in (first_card, second_card):
        client.put(
            f"/cards/{card['id']}/primary-topic",
            json={"topic_id": source_topic["id"]},
        )

    split_response = client.post(
        f"/topics/{source_topic['id']}/split",
        json={"title": "Optimization", "card_ids": [second_card["id"]]},
    )
    assert split_response.status_code == 201
    split_topic = split_response.json()
    course_map = client.get(f"/courses/{DEFAULT_COURSE_ID}/map").json()
    second_membership = next(
        item
        for item in course_map["memberships"]
        if item["card_id"] == second_card["id"] and item["status"] == "accepted"
    )
    assert second_membership["topic_id"] == split_topic["id"]

    merge_response = client.post(
        f"/topics/{source_topic['id']}/merge",
        json={"source_topic_ids": [split_topic["id"]]},
    )
    assert merge_response.status_code == 200
    merged_map = client.get(f"/courses/{DEFAULT_COURSE_ID}/map").json()
    assert split_topic["id"] not in {topic["id"] for topic in merged_map["topics"]}
    assert {
        item["card_id"]
        for item in merged_map["memberships"]
        if item["topic_id"] == source_topic["id"] and item["status"] == "accepted"
    } == {first_card["id"], second_card["id"]}
