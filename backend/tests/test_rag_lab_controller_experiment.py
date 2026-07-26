from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from rag_lab.benchmark import benchmark_payload_sha256
from rag_lab.controller_memory import FrozenControllerMemory
from rag_lab.controller_runner import (
    DeterministicEvidenceVerifier,
    ExtractiveEvidenceAnswerer,
    run_controller_episode,
)
from rag_lab.controller_schemas import (
    ControllerBudget,
    ControllerKnowledgeNeed,
    ControllerProtocol,
    ControllerTrace,
    SearchConceptAction,
    controller_memory_payload_sha256,
    controller_protocol_payload_sha256,
    controller_trace_payload_sha256,
)
from rag_lab.io import (
    sha256_value,
    write_json_atomic,
    write_model_atomic,
)
from rag_lab.reviews import review_payload_sha256
from rag_lab.run_controller_experiment import (
    _exclusive_run_lock,
    _new_run_id,
    _validate_cli_args,
    _validate_trace_against_protocol,
    build_parser,
    build_controller_protocol,
    run_controller_experiment,
    validate_controller_preflight,
)
from rag_lab.schemas import (
    RagAnnotationReview,
    RagBenchmarkDataset,
    RagBenchmarkItem,
    RagCorpusCard,
    RagCorpusClaim,
    RagCorpusEvidence,
    RagCorpusSnapshot,
    RagEmbeddingRecord,
    RagEmbeddingSnapshot,
    RagClaimDecision,
)


class _FakeEmbedder:
    def embed_texts(self, texts, **_):
        return [[1.0, 0.0] for _ in texts]


QUERY_ENCODER_SHA256 = "d" * 64


@dataclass
class _StartedRun:
    benchmark: RagBenchmarkDataset
    memory: FrozenControllerMemory
    protocol: ControllerProtocol
    paths: dict[str, Path]
    run_dir: Path

    def resume(
        self,
        *,
        protocol: ControllerProtocol | None = None,
    ) -> Path:
        return run_controller_experiment(
            corpus_path=self.paths["corpus"],
            benchmark_path=self.paths["benchmark"],
            review_path=self.paths["review"],
            embeddings_path=self.paths["embeddings"],
            benchmark=self.benchmark,
            memory=self.memory,
            protocol=protocol or self.protocol,
            query_encoder_sha256=QUERY_ENCODER_SHA256,
            output_dir=None,
            resume_run_dir=self.run_dir,
        )


def _artifacts(tmp_path: Path):
    card = RagCorpusCard(
        card_id="concept-gradient",
        job_id="job-1",
        lecture_name="lecture.mp4",
        title="Gradient",
        summary="A gradient points uphill.",
        document_text="gradient derivative direction",
        content_status="reviewed",
        source_start_seconds=1,
        source_end_seconds=3,
        claims=[
            RagCorpusClaim(
                claim_id="claim-gradient",
                text="A gradient gives the direction of steepest increase.",
                evidence=[
                    RagCorpusEvidence(
                        evidence_id="evidence-gradient",
                        quote="The gradient points in the steepest direction.",
                        start_seconds=1,
                        end_seconds=2,
                    )
                ],
            )
        ],
    )
    corpus = RagCorpusSnapshot(
        snapshot_id="corpus-1",
        course_id="course-1",
        source_database_sha256="a" * 64,
        snapshot_sha256="0" * 64,
        cards=[card],
    )
    corpus.snapshot_sha256 = sha256_value(
        {
            "course_id": corpus.course_id,
            "cards": [
                item.model_dump(mode="json") for item in corpus.cards
            ],
            "relations": [],
        }
    )
    review = RagAnnotationReview(
        review_id="review-1",
        corpus_sha256=corpus.snapshot_sha256,
        review_status="candidate",
        review_sha256="0" * 64,
    )
    review.review_sha256 = review_payload_sha256(review)
    benchmark = RagBenchmarkDataset(
        benchmark_id="benchmark-1",
        course_id=corpus.course_id,
        corpus_sha256=corpus.snapshot_sha256,
        annotation_method="manual development fixture",
        confirmatory_status="pending_human_review",
        dataset_sha256="0" * 64,
        items=[
            RagBenchmarkItem(
                question_id="question-development",
                category="factual",
                split="development",
                question="What direction does a gradient give?",
                answerable=True,
                reference_answer="The direction of steepest increase.",
                gold_card_ids=["concept-gradient"],
                gold_claim_ids=["claim-gradient"],
                evidence=[
                    {
                        "card_id": "concept-gradient",
                        "claim_id": "claim-gradient",
                        "evidence_id": "evidence-gradient",
                        "quote": (
                            "The gradient points in the steepest direction."
                        ),
                        "start_seconds": 1,
                        "end_seconds": 2,
                    }
                ],
                authoring_method="manual",
                review_status="pending",
            ),
            RagBenchmarkItem(
                question_id="question-test",
                category="unanswerable",
                split="test",
                question="What is absent?",
                answerable=False,
                authoring_method="manual",
                review_status="pending",
            ),
        ],
    )
    benchmark.dataset_sha256 = benchmark_payload_sha256(benchmark)
    records = [
        RagEmbeddingRecord(
            card_id="concept-gradient",
            vector=[1.0, 0.0],
        )
    ]
    embeddings = RagEmbeddingSnapshot(
        corpus_sha256=corpus.snapshot_sha256,
        model="fake-embedding",
        dimension=2,
        normalized=True,
        indexing_milliseconds=1,
        records=records,
        embeddings_sha256=sha256_value(
            [record.model_dump(mode="json") for record in records]
        ),
    )
    paths = {
        "corpus": tmp_path / "corpus.json",
        "benchmark": tmp_path / "benchmark.json",
        "review": tmp_path / "review.json",
        "embeddings": tmp_path / "embeddings.json",
    }
    write_model_atomic(paths["corpus"], corpus)
    write_model_atomic(paths["benchmark"], benchmark)
    write_model_atomic(paths["review"], review)
    write_model_atomic(paths["embeddings"], embeddings)
    memory = FrozenControllerMemory(
        corpus,
        review,
        embeddings,
        _FakeEmbedder(),
        memory_id="memory-1",
        query_encoder_sha256=QUERY_ENCODER_SHA256,
    )
    return corpus, benchmark, review, embeddings, memory, paths


def _started_run(tmp_path: Path) -> _StartedRun:
    corpus, benchmark, review, embeddings, memory, paths = _artifacts(
        tmp_path
    )
    protocol = build_controller_protocol(
        corpus=corpus,
        benchmark=benchmark,
        review=review,
        embeddings=embeddings,
        embeddings_path=paths["embeddings"],
        query_encoder_sha256=QUERY_ENCODER_SHA256,
        memory=memory,
        policy_name="fixed_dense",
        split="development",
        top_k=1,
    )
    run_dir = run_controller_experiment(
        corpus_path=paths["corpus"],
        benchmark_path=paths["benchmark"],
        review_path=paths["review"],
        embeddings_path=paths["embeddings"],
        benchmark=benchmark,
        memory=memory,
        protocol=protocol,
        query_encoder_sha256=QUERY_ENCODER_SHA256,
        output_dir=tmp_path / "runs",
    )
    return _StartedRun(
        benchmark=benchmark,
        memory=memory,
        protocol=protocol,
        paths=paths,
        run_dir=run_dir,
    )


def test_generated_development_protocol_binds_every_input_hash(
    tmp_path: Path,
) -> None:
    corpus, benchmark, review, embeddings, memory, paths = _artifacts(
        tmp_path
    )
    protocol = build_controller_protocol(
        corpus=corpus,
        benchmark=benchmark,
        review=review,
        embeddings=embeddings,
        embeddings_path=paths["embeddings"],
        query_encoder_sha256=QUERY_ENCODER_SHA256,
        memory=memory,
        policy_name="fixed_dense",
        split="development",
        top_k=1,
    )
    assert protocol.protocol_sha256 == controller_protocol_payload_sha256(
        protocol
    )
    validate_controller_preflight(
        corpus=corpus,
        benchmark=benchmark,
        review=review,
        embeddings=embeddings,
        embeddings_path=paths["embeddings"],
        query_encoder_sha256=QUERY_ENCODER_SHA256,
        memory=memory,
        protocol=protocol,
        requested_split="development",
    )


def test_pre_action_budget_exhaustion_is_persisted_and_resumable(
    tmp_path: Path,
) -> None:
    corpus, benchmark, review, embeddings, memory, paths = _artifacts(
        tmp_path
    )
    protocol = build_controller_protocol(
        corpus=corpus,
        benchmark=benchmark,
        review=review,
        embeddings=embeddings,
        embeddings_path=paths["embeddings"],
        query_encoder_sha256=QUERY_ENCODER_SHA256,
        memory=memory,
        policy_name="fixed_dense",
        split="development",
        top_k=1,
        budget=ControllerBudget(
            max_retrieval_calls=0,
            max_concept_searches=0,
            max_evidence_searches=0,
            max_graph_expansions=0,
        ),
    )
    class _AlwaysSearch:
        name = "fixed_dense"

        def initialize(self, *, question_id: str, question: str):
            return [
                ControllerKnowledgeNeed(
                    need_id="need-1",
                    description=question,
                )
            ]

        def decide(self, _context):
            return SearchConceptAction(
                need_id="need-1",
                query="gradient",
                top_k=1,
            )

    trace = run_controller_episode(
        question_id="question-development",
        question="What direction does a gradient give?",
        policy=_AlwaysSearch(),
        memory=memory,
        verifier=DeterministicEvidenceVerifier(memory.snapshot.evidence),
        answerer=ExtractiveEvidenceAnswerer(memory.snapshot.evidence),
        protocol=protocol,
        memory_id=memory.snapshot.memory_id,
    )
    persisted = ControllerTrace.model_validate_json(trace.model_dump_json())
    _validate_trace_against_protocol(
        persisted,
        protocol,
        memory.snapshot,
    )

    assert persisted.stop_reason == "budget_exhausted"
    assert persisted.steps == []
    assert persisted.terminal_proposed_action is not None
    assert persisted.budget_exhausted_fields == [
        "retrieval_calls",
        "concept_searches",
    ]


def test_test_protocol_generation_and_pending_test_access_are_blocked(
    tmp_path: Path,
) -> None:
    corpus, benchmark, review, embeddings, memory, paths = _artifacts(
        tmp_path
    )
    with pytest.raises(ValueError, match="preregistered"):
        build_controller_protocol(
            corpus=corpus,
            benchmark=benchmark,
            review=review,
            embeddings=embeddings,
            embeddings_path=paths["embeddings"],
            query_encoder_sha256=QUERY_ENCODER_SHA256,
            memory=memory,
            policy_name="fixed_dense",
            split="test",
            top_k=1,
        )

    development = build_controller_protocol(
        corpus=corpus,
        benchmark=benchmark,
        review=review,
        embeddings=embeddings,
        embeddings_path=paths["embeddings"],
        query_encoder_sha256=QUERY_ENCODER_SHA256,
        memory=memory,
        policy_name="fixed_dense",
        split="development",
        top_k=1,
    )
    provisional = development.model_copy(
        update={"split": "test", "protocol_sha256": "0" * 64}
    )
    test_protocol = provisional.model_copy(
        update={
            "protocol_sha256": controller_protocol_payload_sha256(
                provisional
            )
        }
    )
    with pytest.raises(ValueError, match="not sealed"):
        validate_controller_preflight(
            corpus=corpus,
            benchmark=benchmark,
            review=review,
            embeddings=embeddings,
            embeddings_path=paths["embeddings"],
            query_encoder_sha256=QUERY_ENCODER_SHA256,
            memory=memory,
            protocol=test_protocol,
            requested_split="test",
        )

    sealed = benchmark.model_copy(deep=True)
    sealed.confirmatory_status = "sealed"
    for item in sealed.items:
        item.review_status = "accepted"
    sealed.dataset_sha256 = benchmark_payload_sha256(sealed)
    verified_review = review.model_copy(
        update={
            "review_status": "human_verified",
            "claim_decisions": [
                RagClaimDecision(
                    card_id="concept-gradient",
                    claim_id="claim-gradient",
                    support="supported",
                    reviewer_id="independent-human",
                    review_method="manual",
                    review_notes="Verified against the source evidence.",
                )
            ],
            "review_sha256": "0" * 64,
        }
    )
    verified_review.review_sha256 = review_payload_sha256(verified_review)
    verified_memory = FrozenControllerMemory(
        corpus,
        verified_review,
        embeddings,
        _FakeEmbedder(),
        memory_id=memory.snapshot.memory_id,
        query_encoder_sha256=QUERY_ENCODER_SHA256,
    )
    sealed_development = build_controller_protocol(
        corpus=corpus,
        benchmark=sealed,
        review=verified_review,
        embeddings=embeddings,
        embeddings_path=paths["embeddings"],
        query_encoder_sha256=QUERY_ENCODER_SHA256,
        memory=verified_memory,
        policy_name="fixed_dense",
        split="development",
        top_k=1,
    )
    sealed_provisional = sealed_development.model_copy(
        update={"split": "test", "protocol_sha256": "0" * 64}
    )
    sealed_protocol = sealed_provisional.model_copy(
        update={
            "protocol_sha256": controller_protocol_payload_sha256(
                sealed_provisional
            )
        }
    )
    with pytest.raises(ValueError, match="development-only"):
        validate_controller_preflight(
            corpus=corpus,
            benchmark=sealed,
            review=verified_review,
            embeddings=embeddings,
            embeddings_path=paths["embeddings"],
            query_encoder_sha256=QUERY_ENCODER_SHA256,
            memory=verified_memory,
            protocol=sealed_protocol,
            requested_split="test",
        )


def test_development_run_writes_artifacts_and_resumes_without_duplicates(
    tmp_path: Path,
) -> None:
    corpus, benchmark, review, embeddings, memory, paths = _artifacts(
        tmp_path
    )
    protocol = build_controller_protocol(
        corpus=corpus,
        benchmark=benchmark,
        review=review,
        embeddings=embeddings,
        embeddings_path=paths["embeddings"],
        query_encoder_sha256=QUERY_ENCODER_SHA256,
        memory=memory,
        policy_name="fixed_dense",
        split="development",
        top_k=1,
    )
    output_dir = tmp_path / "runs"
    run_dir = run_controller_experiment(
        corpus_path=paths["corpus"],
        benchmark_path=paths["benchmark"],
        review_path=paths["review"],
        embeddings_path=paths["embeddings"],
        benchmark=benchmark,
        memory=memory,
        protocol=protocol,
        query_encoder_sha256=QUERY_ENCODER_SHA256,
        output_dir=output_dir,
    )
    episode_lines = (
        run_dir / "episodes.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    assert len(episode_lines) == 1
    manifest = json.loads(
        (run_dir / "manifest.json").read_text(encoding="utf-8")
    )
    metrics = json.loads(
        (run_dir / "metrics.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "completed"
    assert manifest["completed_question_ids"] == [
        "question-development"
    ]
    assert not metrics["paper_claim_eligible"]

    resumed = run_controller_experiment(
        corpus_path=paths["corpus"],
        benchmark_path=paths["benchmark"],
        review_path=paths["review"],
        embeddings_path=paths["embeddings"],
        benchmark=benchmark,
        memory=memory,
        protocol=protocol,
        query_encoder_sha256=QUERY_ENCODER_SHA256,
        output_dir=None,
        resume_run_dir=run_dir,
    )
    assert resumed == run_dir
    assert len(
        (run_dir / "episodes.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ) == 1


def test_resume_rejects_a_different_canonical_protocol(
    tmp_path: Path,
) -> None:
    started = _started_run(tmp_path)
    provisional = started.protocol.model_copy(
        update={
            "protocol_id": "different-protocol",
            "protocol_sha256": "0" * 64,
        }
    )
    wrong_protocol = provisional.model_copy(
        update={
            "protocol_sha256": controller_protocol_payload_sha256(
                provisional
            )
        }
    )

    with pytest.raises(ValueError, match="Resume protocol differs"):
        started.resume(protocol=wrong_protocol)


def test_resume_rejects_a_different_frozen_memory(
    tmp_path: Path,
) -> None:
    started = _started_run(tmp_path)
    provisional = started.memory.snapshot.model_copy(
        update={
            "memory_id": "different-memory",
            "memory_sha256": "0" * 64,
        }
    )
    wrong_memory = provisional.model_copy(
        update={
            "memory_sha256": controller_memory_payload_sha256(
                provisional
            )
        }
    )
    write_model_atomic(
        started.run_dir / "memory_snapshot.json",
        wrong_memory,
    )

    with pytest.raises(ValueError, match="Resume memory differs"):
        started.resume()


def test_resume_rejects_manifest_provenance_drift(
    tmp_path: Path,
) -> None:
    started = _started_run(tmp_path)
    manifest_path = started.run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["query_encoder_sha256"] = "e" * 64
    write_json_atomic(manifest_path, manifest)

    with pytest.raises(ValueError, match="Resume manifest differs"):
        started.resume()


def test_resume_rejects_trace_from_a_different_run(
    tmp_path: Path,
) -> None:
    started = _started_run(tmp_path)
    manifest_path = started.run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "running"
    manifest["artifact_sha256s"] = {}
    write_json_atomic(manifest_path, manifest)
    episodes_path = started.run_dir / "episodes.jsonl"
    trace = ControllerTrace.model_validate_json(
        episodes_path.read_text(encoding="utf-8").strip()
    )
    provisional = trace.model_copy(
        update={
            "memory_id": "different-memory",
            "trace_sha256": "0" * 64,
        }
    )
    wrong_trace = provisional.model_copy(
        update={
            "trace_sha256": controller_trace_payload_sha256(
                provisional
            )
        }
    )
    episodes_path.write_text(
        wrong_trace.model_dump_json() + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match=r"does not belong to this run: memory_id",
    ):
        started.resume()


def test_resume_recovers_only_an_incomplete_jsonl_tail(
    tmp_path: Path,
) -> None:
    started = _started_run(tmp_path)
    manifest_path = started.run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "running"
    manifest["artifact_sha256s"] = {}
    write_json_atomic(manifest_path, manifest)
    episodes_path = started.run_dir / "episodes.jsonl"
    with episodes_path.open("ab") as handle:
        handle.write(b'{"trace_id":"interrupted')

    assert started.resume() == started.run_dir

    assert len(
        episodes_path.read_text(encoding="utf-8").splitlines()
    ) == 1
    manifest = json.loads(
        (started.run_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["recovery_events"][-1]["type"] == (
        "truncated_jsonl_tail"
    )
    assert manifest["recovery_events"][-1]["removed_bytes"] > 0


def test_completed_resume_rejects_tampered_metrics_without_rewriting(
    tmp_path: Path,
) -> None:
    started = _started_run(tmp_path)
    metrics_path = started.run_dir / "metrics.json"
    metrics_path.write_text('{"tampered":true}\n', encoding="utf-8")
    tampered = metrics_path.read_bytes()

    with pytest.raises(
        ValueError,
        match="artifact hash mismatch: metrics.json",
    ):
        started.resume()

    assert metrics_path.read_bytes() == tampered
    manifest = json.loads(
        (started.run_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "completed"


def test_run_ids_are_collision_resistant_within_one_timestamp_window() -> None:
    run_ids = {_new_run_id("fixed_dense") for _ in range(100)}

    assert len(run_ids) == 100
    assert all(run_id.startswith("controller-fixed_dense-") for run_id in run_ids)


def test_resume_lock_prevents_concurrent_writers(
    tmp_path: Path,
) -> None:
    started = _started_run(tmp_path)

    with _exclusive_run_lock(
        started.run_dir,
        require_existing=True,
    ):
        with pytest.raises(RuntimeError, match="already locked"):
            started.resume()

    assert not (started.run_dir / ".controller-run.lock").exists()
