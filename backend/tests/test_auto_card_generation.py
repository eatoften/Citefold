from threading import Event, Thread

from fastapi.testclient import TestClient
import pytest

import app.auto_card_generation_service as auto_card_generation_service
import app.main as main
import app.transcript_chunk_service as transcript_chunk_service
from app.card_generation_chunk_store import (
    list_chunk_results,
    record_chunk_failure,
)
from app.card_generation_run import AutoCardGenerationRequest
from app.job import VideoJob, VideoJobStatus, utc_now
from app.job_store import create_job
from app.knowledge_card_store import list_cards_for_job
from app.llm_client import LLMMessage
from app.reliable_task import ReliableTaskStatus
from app.settings import LLMSettings
from app.transcript_chunk import TranscriptChunkGenerationRequest
from app.transcript_chunk_store import list_chunks_for_job
from app.transcript_store import save_transcription
from app.transcription import TranscriptSegment, TranscriptionResult


client = TestClient(main.app)


class FakeEmbedder:
    def embed_texts(
        self,
        texts,
        *,
        batch_size=None,
    ) -> list[list[float]]:
        return [
            [1.0, 0.0],
            [0.99, 0.01],
            [0.0, 1.0],
            [0.01, 0.99],
        ][:len(texts)]


class FakeLLMClient:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.calls: list[list[LLMMessage]] = []
        self.settings = LLMSettings(
            provider="ollama",
            base_url="http://localhost:11434/v1",
            model="qwen3:4b",
            api_key="local",
        )

    def create_chat_completion(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, object] | None = None,
    ) -> str:
        self.calls.append(messages)

        return self.outputs.pop(0)


def create_completed_job_with_transcript(tmp_path) -> VideoJob:
    transcript = TranscriptionResult(
        language="en",
        language_probability=0.99,
        duration_seconds=120,
        segments=[
            TranscriptSegment(
                start_seconds=0,
                end_seconds=30,
                text="Image classification assigns labels to images.",
            ),
            TranscriptSegment(
                start_seconds=30,
                end_seconds=60,
                text="Nearest neighbor compares a test image to examples.",
            ),
            TranscriptSegment(
                start_seconds=60,
                end_seconds=90,
                text="Loss functions measure prediction mistakes.",
            ),
            TranscriptSegment(
                start_seconds=90,
                end_seconds=120,
                text="Optimization updates parameters using gradients.",
            ),
        ],
    )
    transcript_path = tmp_path / "transcripts" / "lecture.json"
    save_transcription(transcript, transcript_path)
    job = VideoJob(
        id="job-1",
        video_path=tmp_path / "lecture.mp4",
        status=VideoJobStatus.completed,
        original_filename="lecture.mp4",
        transcript_path=transcript_path,
    )
    create_job(job)

    return job


def card_output(
    *,
    title: str,
    summary: str,
    claim: str,
    quote: str,
    question: str,
    answer: str,
) -> str:
    return f"""
    {{
      "cards": [
        {{
          "title": "{title}",
          "summary": "{summary}",
          "key_points": ["{summary}"],
          "claims": [
            {{
              "text": "{claim}",
              "evidence_quotes": ["{quote}"]
            }}
          ],
          "question": "{question}",
          "answer": "{answer}",
          "difficulty": "easy"
        }}
      ]
    }}
    """


def auto_request() -> AutoCardGenerationRequest:
    return AutoCardGenerationRequest(
        card_count_per_chunk=1,
        chunking=TranscriptChunkGenerationRequest(
            context_radius=0,
            min_chunk_seconds=30,
            max_chunk_seconds=300,
            boundary_percentile=80,
        ),
    )


def test_auto_card_generation_run_creates_saved_cards(monkeypatch, tmp_path):
    job = create_completed_job_with_transcript(tmp_path)
    fake_llm = FakeLLMClient(
        [
            card_output(
                title="Image Classification",
                summary="Image classification assigns labels.",
                claim="Image classification assigns labels to images.",
                quote="Image classification assigns labels to images.",
                question="What does image classification do?",
                answer="It assigns labels to images.",
            ),
            card_output(
                title="Loss Functions",
                summary="Loss functions measure mistakes.",
                claim="Loss functions measure prediction mistakes.",
                quote="Loss functions measure prediction mistakes.",
                question="What do loss functions measure?",
                answer="They measure prediction mistakes.",
            ),
        ]
    )
    monkeypatch.setattr(
        transcript_chunk_service,
        "_create_default_embedder",
        lambda: FakeEmbedder(),
    )

    run = auto_card_generation_service.start_auto_card_generation(
        job.id,
        auto_request(),
    )
    auto_card_generation_service.run_auto_card_generation(
        run.id,
        lambda: fake_llm,
    )
    completed_run = auto_card_generation_service.get_card_generation_run(
        run.id
    )
    cards = list_cards_for_job(job.id)

    assert completed_run.status == "completed"
    assert completed_run.total_chunks == 2
    assert completed_run.completed_chunks == 2
    assert completed_run.succeeded_chunks == 2
    assert completed_run.failed_chunks == 0
    assert completed_run.cards_created == 2
    assert [
        card.title
        for card in cards
    ] == [
        "Image Classification",
        "Loss Functions",
    ]


def test_auto_card_retry_resumes_after_published_chunk_without_duplicates(
    monkeypatch,
    tmp_path,
):
    job = create_completed_job_with_transcript(tmp_path)
    first_llm = FakeLLMClient(
        [
            card_output(
                title="Image Classification",
                summary="Image classification assigns labels.",
                claim="Image classification assigns labels to images.",
                quote="Image classification assigns labels to images.",
                question="What does image classification do?",
                answer="It assigns labels to images.",
            )
        ]
    )
    retry_llm = FakeLLMClient(
        [
            card_output(
                title="Loss Functions",
                summary="Loss functions measure mistakes.",
                claim="Loss functions measure prediction mistakes.",
                quote="Loss functions measure prediction mistakes.",
                question="What do loss functions measure?",
                answer="They measure prediction mistakes.",
            )
        ]
    )
    monkeypatch.setattr(
        transcript_chunk_service,
        "_create_default_embedder",
        lambda: FakeEmbedder(),
    )
    run = auto_card_generation_service.start_auto_card_generation(
        job.id,
        auto_request(),
    )
    real_reconcile = (
        auto_card_generation_service.reconcile_run_from_chunk_results
    )
    injected = False

    class SimulatedProcessCrash(BaseException):
        pass

    def crash_after_first_chunk_publication(*args, **kwargs):
        nonlocal injected
        if (
            not injected
            and kwargs.get("phase") == "running"
            and len(list_cards_for_job(job.id)) == 1
        ):
            injected = True
            raise SimulatedProcessCrash()
        return real_reconcile(*args, **kwargs)

    monkeypatch.setattr(
        auto_card_generation_service,
        "reconcile_run_from_chunk_results",
        crash_after_first_chunk_publication,
    )
    with pytest.raises(SimulatedProcessCrash):
        auto_card_generation_service.run_auto_card_generation(
            run.id,
            lambda: first_llm,
        )
    assert len(first_llm.calls) == 1
    assert [
        card.title for card in list_cards_for_job(job.id)
    ] == ["Image Classification"]

    monkeypatch.setattr(
        auto_card_generation_service,
        "reconcile_run_from_chunk_results",
        real_reconcile,
    )
    assert (
        auto_card_generation_service.recover_interrupted_card_generation_runs()
        == 1
    )
    auto_card_generation_service.run_auto_card_generation(
        run.id,
        lambda: retry_llm,
    )

    completed = auto_card_generation_service.get_card_generation_run(run.id)
    assert completed.status == "completed"
    assert completed.total_chunks == 2
    assert completed.completed_chunks == 2
    assert completed.succeeded_chunks == 2
    assert completed.failed_chunks == 0
    assert completed.cards_created == 2
    assert len(retry_llm.calls) == 1
    assert [
        card.title for card in list_cards_for_job(job.id)
    ] == ["Image Classification", "Loss Functions"]


def test_failed_chunk_fails_task_and_retry_completes_same_run(
    monkeypatch,
    tmp_path,
):
    job = create_completed_job_with_transcript(tmp_path)
    fake_llm = FakeLLMClient(
        [
            "not-json",
            "still-not-json",
            card_output(
                title="Loss Functions",
                summary="Loss functions measure mistakes.",
                claim="Loss functions measure prediction mistakes.",
                quote="Loss functions measure prediction mistakes.",
                question="What do loss functions measure?",
                answer="They measure prediction mistakes.",
            ),
            card_output(
                title="Image Classification",
                summary="Image classification assigns labels.",
                claim="Image classification assigns labels to images.",
                quote="Image classification assigns labels to images.",
                question="What does image classification do?",
                answer="It assigns labels to images.",
            ),
        ]
    )
    monkeypatch.setattr(
        transcript_chunk_service,
        "_create_default_embedder",
        lambda: FakeEmbedder(),
    )
    monkeypatch.setattr(main, "get_llm_client", lambda: fake_llm)

    response = client.post(
        f"/jobs/{job.id}/cards/auto-generate",
        json=auto_request().model_dump(mode="json"),
    )
    assert response.status_code == 202
    task_id = response.headers["X-Task-ID"]
    run_id = response.json()["id"]
    manager = main.get_reliable_task_manager()
    failed_task = manager.wait_for_task(
        task_id,
        {ReliableTaskStatus.failed},
        timeout_seconds=5,
    )
    failed_run = (
        auto_card_generation_service.get_card_generation_run(run_id)
    )

    assert failed_task.retryable is True
    assert failed_run.status == "failed"
    assert failed_run.completed_chunks == 2
    assert failed_run.succeeded_chunks == 1
    assert failed_run.failed_chunks == 1
    assert failed_run.cards_created == 1
    assert len(list_cards_for_job(job.id)) == 1

    retry_response = client.post(f"/tasks/{task_id}/retry")

    assert retry_response.status_code == 202
    assert retry_response.json()["id"] == task_id
    completed_task = manager.wait_for_task(
        task_id,
        {ReliableTaskStatus.succeeded},
        timeout_seconds=5,
    )
    completed_run = (
        auto_card_generation_service.get_card_generation_run(run_id)
    )

    assert completed_task.payload["run_id"] == run_id
    assert completed_run.status == "completed"
    assert completed_run.completed_chunks == 2
    assert completed_run.succeeded_chunks == 2
    assert completed_run.failed_chunks == 0
    assert completed_run.cards_created == 2
    assert len(fake_llm.calls) == 4
    assert {
        card.title for card in list_cards_for_job(job.id)
    } == {"Image Classification", "Loss Functions"}


def test_late_chunk_failure_cannot_downgrade_published_success(
    monkeypatch,
    tmp_path,
):
    job = create_completed_job_with_transcript(tmp_path)
    fake_llm = FakeLLMClient(
        [
            card_output(
                title="Image Classification",
                summary="Image classification assigns labels.",
                claim="Image classification assigns labels to images.",
                quote="Image classification assigns labels to images.",
                question="What does image classification do?",
                answer="It assigns labels to images.",
            )
        ]
    )
    monkeypatch.setattr(
        transcript_chunk_service,
        "_create_default_embedder",
        lambda: FakeEmbedder(),
    )
    request = auto_request().model_copy(update={"max_chunks": 1})
    run = auto_card_generation_service.start_auto_card_generation(
        job.id,
        request,
    )
    auto_card_generation_service.run_auto_card_generation(
        run.id,
        lambda: fake_llm,
    )
    chunk = list_chunks_for_job(job.id)[0]

    record_chunk_failure(
        run.id,
        chunk,
        error_message="late concurrent failure",
        now=utc_now(),
    )
    reconciled = (
        auto_card_generation_service.reconcile_run_from_chunk_results(
            run.id,
            [(chunk.id, chunk.chunk_index)],
            phase="running",
        )
    )
    ledger = list_chunk_results(run.id)

    assert reconciled is not None
    assert reconciled.status == "completed"
    assert reconciled.completed_chunks == 1
    assert reconciled.succeeded_chunks == 1
    assert reconciled.failed_chunks == 0
    assert reconciled.cards_created == 1
    assert [(result.status, result.cards_created) for result in ledger] == [
        ("succeeded", 1)
    ]
    assert len(list_cards_for_job(job.id)) == 1


def test_concurrent_run_invocation_has_single_active_owner(
    monkeypatch,
    tmp_path,
):
    job = create_completed_job_with_transcript(tmp_path)
    output = card_output(
        title="Image Classification",
        summary="Image classification assigns labels.",
        claim="Image classification assigns labels to images.",
        quote="Image classification assigns labels to images.",
        question="What does image classification do?",
        answer="It assigns labels to images.",
    )
    llm_started = Event()
    release_llm = Event()

    class BlockingLLMClient(FakeLLMClient):
        def create_chat_completion(self, messages, **kwargs):
            self.calls.append(messages)
            llm_started.set()
            assert release_llm.wait(5)
            return output

    fake_llm = BlockingLLMClient([])
    monkeypatch.setattr(
        transcript_chunk_service,
        "_create_default_embedder",
        lambda: FakeEmbedder(),
    )
    request = auto_request().model_copy(update={"max_chunks": 1})
    transcript_chunk_service.generate_job_chunks(job.id, request.chunking)
    run = auto_card_generation_service.start_auto_card_generation(
        job.id,
        request,
    )
    errors: list[BaseException] = []

    def execute() -> None:
        try:
            auto_card_generation_service.run_auto_card_generation(
                run.id,
                lambda: fake_llm,
            )
        except BaseException as exc:
            errors.append(exc)

    first = Thread(target=execute)
    second = Thread(target=execute)
    first.start()
    assert llm_started.wait(5)
    second.start()
    second.join(5)
    release_llm.set()
    first.join(5)

    stored = (
        auto_card_generation_service.get_card_generation_run(run.id)
    )
    ledger = list_chunk_results(run.id)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert len(fake_llm.calls) == 1
    assert stored.status == "completed"
    assert stored.succeeded_chunks == 1
    assert stored.failed_chunks == 0
    assert stored.cards_created == 1
    assert [(result.status, result.cards_created) for result in ledger] == [
        ("succeeded", 1)
    ]
    assert len(list_cards_for_job(job.id)) == 1


def test_auto_card_generation_api_starts_and_reports_run(
    monkeypatch,
    tmp_path,
):
    job = create_completed_job_with_transcript(tmp_path)
    fake_llm = FakeLLMClient(
        [
            card_output(
                title="Image Classification",
                summary="Image classification assigns labels.",
                claim="Image classification assigns labels to images.",
                quote="Image classification assigns labels to images.",
                question="What does image classification do?",
                answer="It assigns labels to images.",
            ),
            card_output(
                title="Loss Functions",
                summary="Loss functions measure mistakes.",
                claim="Loss functions measure prediction mistakes.",
                quote="Loss functions measure prediction mistakes.",
                question="What do loss functions measure?",
                answer="They measure prediction mistakes.",
            ),
        ]
    )
    monkeypatch.setattr(
        transcript_chunk_service,
        "_create_default_embedder",
        lambda: FakeEmbedder(),
    )
    monkeypatch.setattr(
        main,
        "get_llm_client",
        lambda: fake_llm,
    )

    response = client.post(
        f"/jobs/{job.id}/cards/auto-generate",
        json=auto_request().model_dump(mode="json"),
    )

    assert response.status_code == 202

    task = main.get_reliable_task_manager().wait_for_task(
        response.headers["X-Task-ID"],
        {
            ReliableTaskStatus.succeeded,
            ReliableTaskStatus.failed,
        },
        timeout_seconds=5.0,
    )
    assert task.status == ReliableTaskStatus.succeeded
    run_id = response.json()["id"]
    run_response = client.get(f"/card-generation-runs/{run_id}")

    assert run_response.status_code == 200
    assert run_response.json()["status"] == "completed"
    assert run_response.json()["cards_created"] == 2


def test_auto_card_enqueue_failure_closes_run_and_next_request_retries(
    monkeypatch,
    tmp_path,
):
    job = create_completed_job_with_transcript(tmp_path)
    fake_llm = FakeLLMClient(
        [
            card_output(
                title="Image Classification",
                summary="Image classification assigns labels.",
                claim="Image classification assigns labels to images.",
                quote="Image classification assigns labels to images.",
                question="What does image classification do?",
                answer="It assigns labels to images.",
            ),
            card_output(
                title="Loss Functions",
                summary="Loss functions measure mistakes.",
                claim="Loss functions measure prediction mistakes.",
                quote="Loss functions measure prediction mistakes.",
                question="What do loss functions measure?",
                answer="They measure prediction mistakes.",
            ),
        ]
    )
    monkeypatch.setattr(
        transcript_chunk_service,
        "_create_default_embedder",
        lambda: FakeEmbedder(),
    )
    monkeypatch.setattr(main, "get_llm_client", lambda: fake_llm)
    manager = main.get_reliable_task_manager()
    real_enqueue = manager.enqueue
    enqueue_attempts = 0

    def fail_first_enqueue(*args, **kwargs):
        nonlocal enqueue_attempts
        enqueue_attempts += 1
        if enqueue_attempts == 1:
            raise RuntimeError("simulated reservation failure")
        return real_enqueue(*args, **kwargs)

    monkeypatch.setattr(manager, "enqueue", fail_first_enqueue)

    failed_response = client.post(
        f"/jobs/{job.id}/cards/auto-generate",
        json=auto_request().model_dump(mode="json"),
    )

    assert failed_response.status_code == 500
    assert failed_response.json() == {"detail": "Task operation failed."}
    failed_runs = (
        auto_card_generation_service.list_job_card_generation_runs(job.id)
    )
    assert len(failed_runs) == 1
    failed_run = failed_runs[0]
    assert failed_run.status == "failed"
    assert failed_run.error_message == (
        "Card generation could not be queued. Retry the operation."
    )
    assert failed_run.completed_at is not None

    retry_response = client.post(
        f"/jobs/{job.id}/cards/auto-generate",
        json=auto_request().model_dump(mode="json"),
    )

    assert retry_response.status_code == 202
    task = manager.wait_for_task(
        retry_response.headers["X-Task-ID"],
        {
            ReliableTaskStatus.succeeded,
            ReliableTaskStatus.failed,
        },
        timeout_seconds=5.0,
    )
    assert task.status == ReliableTaskStatus.succeeded
    retry_run_id = retry_response.json()["id"]
    assert retry_run_id != failed_run.id
    assert (
        auto_card_generation_service.get_card_generation_run(
            retry_run_id
        ).status
        == "completed"
    )
    assert (
        auto_card_generation_service.get_card_generation_run(
            failed_run.id
        ).status
        == "failed"
    )
    assert len(list_cards_for_job(job.id)) == 2


def test_cancel_after_card_run_completion_keeps_task_and_run_completed(
    monkeypatch,
    tmp_path,
):
    job = create_completed_job_with_transcript(tmp_path)
    fake_llm = FakeLLMClient(
        [
            card_output(
                title="Image Classification",
                summary="Image classification assigns labels.",
                claim="Image classification assigns labels to images.",
                quote="Image classification assigns labels to images.",
                question="What does image classification do?",
                answer="It assigns labels to images.",
            ),
            card_output(
                title="Loss Functions",
                summary="Loss functions measure mistakes.",
                claim="Loss functions measure prediction mistakes.",
                quote="Loss functions measure prediction mistakes.",
                question="What do loss functions measure?",
                answer="They measure prediction mistakes.",
            ),
        ]
    )
    domain_committed = Event()
    return_from_commit = Event()
    original_reconcile = (
        auto_card_generation_service.reconcile_run_from_chunk_results
    )

    def block_after_completed_commit(*args, **kwargs):
        run = original_reconcile(*args, **kwargs)
        if (
            kwargs.get("phase") == "final"
            and run is not None
            and run.status == "completed"
        ):
            domain_committed.set()
            assert return_from_commit.wait(5)
        return run

    monkeypatch.setattr(
        transcript_chunk_service,
        "_create_default_embedder",
        lambda: FakeEmbedder(),
    )
    monkeypatch.setattr(
        auto_card_generation_service,
        "reconcile_run_from_chunk_results",
        block_after_completed_commit,
    )
    monkeypatch.setattr(main, "get_llm_client", lambda: fake_llm)

    response = client.post(
        f"/jobs/{job.id}/cards/auto-generate",
        json=auto_request().model_dump(mode="json"),
    )
    manager = main.get_reliable_task_manager()
    task_id = response.headers["X-Task-ID"]
    run_id = response.json()["id"]
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

    stored_run = (
        auto_card_generation_service.get_card_generation_run(run_id)
    )
    assert completed.cancel_requested_at is not None
    assert stored_run.status == "completed"
    assert stored_run.cards_created == 2


def test_cancel_after_final_chunk_publication_completes_task(
    monkeypatch,
    tmp_path,
):
    job = create_completed_job_with_transcript(tmp_path)
    fake_llm = FakeLLMClient(
        [
            card_output(
                title="Image Classification",
                summary="Image classification assigns labels.",
                claim="Image classification assigns labels to images.",
                quote="Image classification assigns labels to images.",
                question="What does image classification do?",
                answer="It assigns labels to images.",
            ),
            card_output(
                title="Loss Functions",
                summary="Loss functions measure mistakes.",
                claim="Loss functions measure prediction mistakes.",
                quote="Loss functions measure prediction mistakes.",
                question="What do loss functions measure?",
                answer="They measure prediction mistakes.",
            ),
        ]
    )
    before_final_checkpoint = Event()
    return_from_checkpoint = Event()
    checkpoint_calls = 0

    def block_after_final_chunk(checkpoint):
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        if checkpoint_calls == 4:
            before_final_checkpoint.set()
            assert return_from_checkpoint.wait(5)
        if checkpoint is not None:
            checkpoint()

    monkeypatch.setattr(
        transcript_chunk_service,
        "_create_default_embedder",
        lambda: FakeEmbedder(),
    )
    monkeypatch.setattr(
        auto_card_generation_service,
        "_checkpoint",
        block_after_final_chunk,
    )
    monkeypatch.setattr(main, "get_llm_client", lambda: fake_llm)

    response = client.post(
        f"/jobs/{job.id}/cards/auto-generate",
        json=auto_request().model_dump(mode="json"),
    )
    manager = main.get_reliable_task_manager()
    task_id = response.headers["X-Task-ID"]
    run_id = response.json()["id"]
    try:
        assert before_final_checkpoint.wait(5)
        assert len(list_cards_for_job(job.id)) == 2
        canceling = manager.request_cancel(task_id)
        assert canceling.status == ReliableTaskStatus.canceling
        return_from_checkpoint.set()
        completed = manager.wait_for_task(
            task_id,
            {ReliableTaskStatus.succeeded},
            timeout_seconds=5,
        )
    finally:
        return_from_checkpoint.set()

    stored_run = (
        auto_card_generation_service.get_card_generation_run(run_id)
    )
    assert completed.cancel_requested_at is not None
    assert stored_run.status == "completed"
    assert stored_run.succeeded_chunks == 2
    assert stored_run.failed_chunks == 0
    assert stored_run.cards_created == 2


def test_cancel_before_card_run_completion_still_cancels_without_cards(
    monkeypatch,
    tmp_path,
):
    job = create_completed_job_with_transcript(tmp_path)
    fake_llm = FakeLLMClient(
        [
            card_output(
                title="Unused",
                summary="This output must not be published.",
                claim="This output must not be published.",
                quote="This output must not be published.",
                question="Should this card exist?",
                answer="No.",
            )
        ]
    )
    before_first_card = Event()
    return_from_checkpoint = Event()
    checkpoint_calls = 0

    def block_at_second_checkpoint(checkpoint):
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        if checkpoint_calls == 2:
            before_first_card.set()
            assert return_from_checkpoint.wait(5)
        if checkpoint is not None:
            checkpoint()

    monkeypatch.setattr(
        transcript_chunk_service,
        "_create_default_embedder",
        lambda: FakeEmbedder(),
    )
    monkeypatch.setattr(
        auto_card_generation_service,
        "_checkpoint",
        block_at_second_checkpoint,
    )
    monkeypatch.setattr(main, "get_llm_client", lambda: fake_llm)

    response = client.post(
        f"/jobs/{job.id}/cards/auto-generate",
        json=auto_request().model_dump(mode="json"),
    )
    manager = main.get_reliable_task_manager()
    task_id = response.headers["X-Task-ID"]
    run_id = response.json()["id"]
    try:
        assert before_first_card.wait(5)
        manager.request_cancel(task_id)
        return_from_checkpoint.set()
        canceled = manager.wait_for_task(
            task_id,
            {ReliableTaskStatus.canceled},
            timeout_seconds=5,
        )
    finally:
        return_from_checkpoint.set()

    stored_run = (
        auto_card_generation_service.get_card_generation_run(run_id)
    )
    assert canceled.cancel_requested_at is not None
    assert stored_run.status == "canceled"
    assert list_cards_for_job(job.id) == []


def test_auto_card_generation_returns_409_when_transcript_not_ready(
    tmp_path,
):
    job = VideoJob(
        id="job-1",
        video_path=tmp_path / "lecture.mp4",
        status=VideoJobStatus.uploaded,
    )
    create_job(job)

    response = client.post(f"/jobs/{job.id}/cards/auto-generate")

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Transcript is not available for this job."
    }
