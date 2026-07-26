from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.embedding import (
    SentenceTransformerEmbedder,
    TextEmbedder,
    resolve_sentence_transformer_model_source,
)

from .benchmark import audit_benchmark
from .controller_memory import FrozenControllerMemory
from .controller_metrics import (
    aggregate_controller_metrics,
    evaluate_controller_trace,
    target_from_legacy_item,
)
from .controller_policy import (
    ControllerPolicy,
    EvidenceGapController,
    FixedDenseController,
    FixedDenseTypedGraphController,
)
from .controller_runner import (
    DeterministicEvidenceVerifier,
    ExtractiveEvidenceAnswerer,
    _audit_observation,
    _audit_policy_cost,
    _guard_action,
    run_controller_episode,
)
from .controller_schemas import (
    ControllerBudget,
    ControllerCost,
    ControllerMemorySnapshot,
    ControllerProtocol,
    ControllerTrace,
    ConceptSearchObservation,
    EvidenceSearchObservation,
    GraphExpansionObservation,
    controller_minimum_action_cost,
    controller_memory_payload_sha256,
    controller_protocol_payload_sha256,
)
from .io import (
    load_model,
    sha256_file,
    sha256_value,
    write_json_atomic,
    write_model_atomic,
)
from .reviews import audit_annotation_review, require_formal_human_review
from .schemas import (
    RagAnnotationReview,
    RagBenchmarkDataset,
    RagBenchmarkItem,
    RagCorpusSnapshot,
    RagEmbeddingSnapshot,
)


POLICY_NAMES = (
    "fixed_dense",
    "fixed_dense_typed_graph",
    "evidence_gap",
)
RUNTIME_DEPENDENCIES = (
    "numpy",
    "pydantic",
    "sentence-transformers",
    "torch",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a versioned development controller experiment against frozen "
            "RAG artifacts."
        )
    )
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--benchmark", required=True, type=Path)
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--embeddings", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--resume-run-dir", type=Path)
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--policy", choices=POLICY_NAMES)
    parser.add_argument(
        "--split",
        choices=["development", "test"],
        default="development",
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-items", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _validate_cli_args(args)

    corpus_path = args.corpus.resolve()
    benchmark_path = args.benchmark.resolve()
    review_path = args.review.resolve()
    embeddings_path = args.embeddings.resolve()
    corpus = load_model(corpus_path, RagCorpusSnapshot)
    benchmark = load_model(benchmark_path, RagBenchmarkDataset)
    review = load_model(review_path, RagAnnotationReview)
    embeddings = load_model(embeddings_path, RagEmbeddingSnapshot)
    query_embedder_template = SentenceTransformerEmbedder(
        model_name=embeddings.model,
        normalize_embeddings=embeddings.normalized,
    )
    query_encoder_source = resolve_sentence_transformer_model_source(
        embeddings.model,
        model_path=query_embedder_template.model_path,
        local_files_only=True,
    )
    if not isinstance(query_encoder_source, Path):
        raise ValueError(
            "Controller experiments require a frozen local query encoder."
        )
    query_encoder_hash = query_encoder_sha256(query_encoder_source)

    memory = FrozenControllerMemory(
        corpus,
        review,
        embeddings,
        _DeferredEmbedder(),
        query_encoder_sha256=query_encoder_hash,
    )
    if args.protocol is not None:
        protocol = load_model(
            args.protocol.resolve(),
            ControllerProtocol,
        )
    elif args.resume_run_dir is not None:
        protocol = load_model(
            args.resume_run_dir.resolve() / "resolved_protocol.json",
            ControllerProtocol,
        )
    else:
        protocol = build_controller_protocol(
            corpus=corpus,
            benchmark=benchmark,
            review=review,
            embeddings=embeddings,
            embeddings_path=embeddings_path,
            query_encoder_sha256=query_encoder_hash,
            memory=memory,
            policy_name=args.policy,
            split=args.split,
            top_k=args.top_k,
            max_items=args.max_items,
        )

    validate_controller_preflight(
        corpus=corpus,
        benchmark=benchmark,
        review=review,
        embeddings=embeddings,
        embeddings_path=embeddings_path,
        query_encoder_sha256=query_encoder_hash,
        memory=memory,
        protocol=protocol,
        requested_split=args.split,
    )
    query_embedder = SentenceTransformerEmbedder(
        model_name=embeddings.model,
        model_path=query_encoder_source,
        normalize_embeddings=embeddings.normalized,
        local_files_only=True,
    )
    live_memory = FrozenControllerMemory(
        corpus,
        review,
        embeddings,
        query_embedder,
        memory_id=memory.snapshot.memory_id,
        query_encoder_sha256=query_encoder_hash,
    )
    run_controller_experiment(
        corpus_path=corpus_path,
        benchmark_path=benchmark_path,
        review_path=review_path,
        embeddings_path=embeddings_path,
        benchmark=benchmark,
        memory=live_memory,
        protocol=protocol,
        query_encoder_sha256=query_encoder_hash,
        output_dir=args.output_dir.resolve() if args.output_dir else None,
        resume_run_dir=(
            args.resume_run_dir.resolve()
            if args.resume_run_dir
            else None
        ),
    )
    return 0


def _validate_cli_args(args: argparse.Namespace) -> None:
    if args.resume_run_dir is None and args.output_dir is None:
        raise ValueError(
            "--output-dir is required unless --resume-run-dir is used."
        )
    if (
        args.protocol is None
        and args.policy is None
        and args.resume_run_dir is None
    ):
        raise ValueError(
            "--policy is required when no frozen --protocol is supplied."
        )
    if args.max_items is not None and args.max_items < 1:
        raise ValueError("--max-items must be positive.")
    if args.split == "test" and args.max_items is not None:
        raise ValueError("A confirmatory test run cannot use --max-items.")


class _DeferredEmbedder:
    """Constructor-only placeholder; no query is executed during preflight."""

    def embed_texts(
        self,
        texts: Sequence[str],
        *,
        batch_size: int | None = None,
    ) -> list[list[float]]:
        raise RuntimeError("Deferred embedder cannot execute a query.")


def build_controller_protocol(
    *,
    corpus: RagCorpusSnapshot,
    benchmark: RagBenchmarkDataset,
    review: RagAnnotationReview,
    embeddings: RagEmbeddingSnapshot,
    embeddings_path: Path,
    query_encoder_sha256: str,
    memory: FrozenControllerMemory,
    policy_name: str,
    split: str,
    top_k: int,
    max_items: int | None = None,
    budget: ControllerBudget | None = None,
) -> ControllerProtocol:
    if policy_name not in POLICY_NAMES:
        raise ValueError(f"Unknown controller policy: {policy_name}")
    if split not in {"development", "test"}:
        raise ValueError(f"Unknown benchmark split: {split}")
    if split == "test":
        raise ValueError(
            "Test protocols must be preregistered and supplied with --protocol."
        )
    if top_k < 1 or top_k > 100:
        raise ValueError("top_k must be between 1 and 100.")
    if max_items is not None and max_items < 1:
        raise ValueError("max_items must be positive.")
    resolved_budget = budget or ControllerBudget(max_top_k=max(10, top_k))
    values = {
        "protocol_id": (
            f"controller-v1-{policy_name}-{split}-k{top_k}"
        ),
        "corpus_sha256": corpus.snapshot_sha256,
        "review_sha256": review.review_sha256,
        "memory_sha256": memory.snapshot.memory_sha256,
        "benchmark_sha256": benchmark.dataset_sha256,
        "embedding_snapshot_file_sha256": sha256_file(embeddings_path),
        "embedding_records_sha256": embeddings.embeddings_sha256,
        "query_encoder_sha256": query_encoder_sha256,
        "retrieval_config_sha256": sha256_value(
            memory.snapshot.retrieval_config.model_dump(mode="json")
        ),
        "split": split,
        "policy_name": policy_name,
        "concept_granularity": memory.snapshot.concept_granularity,
        "code_sha256": controller_code_sha256(),
        "evidence_retrieval": "bm25",
        "budget": resolved_budget,
        "policy_config": {
            "top_k": top_k,
            "max_items": max_items,
            "verifier": "deterministic_lexical_smoke_v1",
            "answerer": "extractive_evidence_smoke_v1",
            "claim_scope": "development_debug_only",
        },
    }
    provisional = ControllerProtocol(
        **values,
        protocol_sha256="0" * 64,
    )
    return ControllerProtocol(
        **values,
        protocol_sha256=controller_protocol_payload_sha256(provisional),
    )


def validate_controller_preflight(
    *,
    corpus: RagCorpusSnapshot,
    benchmark: RagBenchmarkDataset,
    review: RagAnnotationReview,
    embeddings: RagEmbeddingSnapshot,
    embeddings_path: Path,
    query_encoder_sha256: str,
    memory: FrozenControllerMemory,
    protocol: ControllerProtocol,
    requested_split: str,
) -> None:
    audit_benchmark(benchmark, corpus, require_accepted=False)
    audit_annotation_review(review, corpus)
    if embeddings.corpus_sha256 != corpus.snapshot_sha256:
        raise ValueError("Embedding snapshot is bound to a different corpus.")
    if memory.snapshot.corpus_sha256 != corpus.snapshot_sha256:
        raise ValueError("Controller memory is bound to a different corpus.")
    if memory.snapshot.review_sha256 != review.review_sha256:
        raise ValueError("Controller memory is bound to a different review.")
    if memory.query_encoder_sha256 != query_encoder_sha256:
        raise ValueError(
            "Controller memory is bound to a different query encoder."
        )
    if protocol.protocol_sha256 != controller_protocol_payload_sha256(
        protocol
    ):
        raise ValueError("Controller protocol hash is not canonical.")
    if (
        protocol.split == "test"
        and protocol.policy_config.max_items is not None
    ):
        raise ValueError("A confirmatory test protocol cannot use max_items.")
    expected = {
        "corpus_sha256": corpus.snapshot_sha256,
        "review_sha256": review.review_sha256,
        "memory_sha256": memory.snapshot.memory_sha256,
        "benchmark_sha256": benchmark.dataset_sha256,
        "embedding_snapshot_file_sha256": sha256_file(embeddings_path),
        "embedding_records_sha256": embeddings.embeddings_sha256,
        "query_encoder_sha256": query_encoder_sha256,
        "retrieval_config_sha256": sha256_value(
            memory.snapshot.retrieval_config.model_dump(mode="json")
        ),
        "code_sha256": controller_code_sha256(),
        "split": requested_split,
        "concept_granularity": memory.snapshot.concept_granularity,
    }
    mismatches = [
        name
        for name, value in expected.items()
        if getattr(protocol, name) != value
    ]
    if mismatches:
        raise ValueError(
            "Controller protocol does not match frozen inputs: "
            + ", ".join(mismatches)
        )
    if not any(item.split == requested_split for item in benchmark.items):
        raise ValueError(f"Benchmark has no {requested_split} items.")
    if requested_split == "test":
        require_formal_human_review(benchmark, review)
        raise ValueError(
            "This controller runner is development-only until the v2 "
            "one-use test-access ledger and separated test gold loader are "
            "implemented."
        )


def run_controller_experiment(
    *,
    corpus_path: Path,
    benchmark_path: Path,
    review_path: Path,
    embeddings_path: Path,
    benchmark: RagBenchmarkDataset,
    memory: FrozenControllerMemory,
    protocol: ControllerProtocol,
    query_encoder_sha256: str,
    output_dir: Path | None,
    resume_run_dir: Path | None = None,
) -> Path:
    if resume_run_dir is not None:
        resolved_run_dir = resume_run_dir.resolve()
        with _exclusive_run_lock(
            resolved_run_dir,
            require_existing=True,
        ):
            return _run_controller_experiment_unlocked(
                corpus_path=corpus_path,
                benchmark_path=benchmark_path,
                review_path=review_path,
                embeddings_path=embeddings_path,
                benchmark=benchmark,
                memory=memory,
                protocol=protocol,
                query_encoder_sha256=query_encoder_sha256,
                output_dir=output_dir,
                resume_run_dir=resolved_run_dir,
            )
    if output_dir is None:
        raise ValueError("A new run requires output_dir.")
    run_id = _new_run_id(protocol.policy_name)
    new_run_dir = output_dir.resolve() / run_id
    with _exclusive_run_lock(
        new_run_dir,
        require_existing=False,
    ):
        return _run_controller_experiment_unlocked(
            corpus_path=corpus_path,
            benchmark_path=benchmark_path,
            review_path=review_path,
            embeddings_path=embeddings_path,
            benchmark=benchmark,
            memory=memory,
            protocol=protocol,
            query_encoder_sha256=query_encoder_sha256,
            output_dir=output_dir,
            new_run_dir=new_run_dir,
        )


def _new_run_id(policy_name: str) -> str:
    """Readable UTC identity with collision-resistant process entropy."""

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"controller-{policy_name}-{timestamp}-{uuid4().hex[:12]}"


def _run_controller_experiment_unlocked(
    *,
    corpus_path: Path,
    benchmark_path: Path,
    review_path: Path,
    embeddings_path: Path,
    benchmark: RagBenchmarkDataset,
    memory: FrozenControllerMemory,
    protocol: ControllerProtocol,
    query_encoder_sha256: str,
    output_dir: Path | None,
    resume_run_dir: Path | None = None,
    new_run_dir: Path | None = None,
) -> Path:
    source_corpus = load_model(corpus_path, RagCorpusSnapshot)
    source_benchmark = load_model(benchmark_path, RagBenchmarkDataset)
    source_review = load_model(review_path, RagAnnotationReview)
    source_embeddings = load_model(
        embeddings_path,
        RagEmbeddingSnapshot,
    )
    if source_benchmark != benchmark:
        raise ValueError(
            "Parsed benchmark differs from the recorded benchmark source."
        )
    if source_embeddings != memory.embedding_snapshot:
        raise ValueError(
            "Controller memory embeddings differ from the recorded source."
        )
    validate_controller_preflight(
        corpus=source_corpus,
        benchmark=source_benchmark,
        review=source_review,
        embeddings=source_embeddings,
        embeddings_path=embeddings_path,
        query_encoder_sha256=query_encoder_sha256,
        memory=memory,
        protocol=protocol,
        requested_split=protocol.split,
    )
    policy = _build_policy(protocol)
    verifier = DeterministicEvidenceVerifier(memory.snapshot.evidence)
    answerer = ExtractiveEvidenceAnswerer(memory.snapshot.evidence)
    source_paths = {
        "corpus": corpus_path.resolve(),
        "benchmark": benchmark_path.resolve(),
        "review": review_path.resolve(),
        "embeddings": embeddings_path.resolve(),
    }
    source_file_hashes = {
        name: sha256_file(path)
        for name, path in source_paths.items()
    }
    items = sorted(
        (
            item
            for item in benchmark.items
            if item.split == protocol.split
        ),
        key=lambda item: item.question_id,
    )
    max_items = protocol.policy_config.max_items
    if max_items is not None:
        items = items[:max_items]
    if not items:
        raise ValueError("No benchmark items remain for this run.")
    expected_questions = {
        item.question_id: item.question for item in items
    }

    if resume_run_dir is None:
        if output_dir is None or new_run_dir is None:
            raise ValueError("A new run requires a prepared output directory.")
        run_dir = new_run_dir
        run_id = run_dir.name
        run_dir.mkdir(parents=True, exist_ok=False)
        write_model_atomic(
            run_dir / "resolved_protocol.json",
            protocol,
        )
        write_model_atomic(
            run_dir / "memory_snapshot.json",
            memory.snapshot,
        )
        manifest = _new_manifest(
            run_id=run_id,
            protocol=protocol,
            source_paths=source_paths,
            source_file_hashes=source_file_hashes,
        )
        write_json_atomic(run_dir / "manifest.json", manifest)
        completed: dict[str, ControllerTrace] = {}
    else:
        run_dir = resume_run_dir
        if not run_dir.is_dir():
            raise FileNotFoundError(f"Resume run does not exist: {run_dir}")
        frozen_protocol = load_model(
            run_dir / "resolved_protocol.json",
            ControllerProtocol,
        )
        if frozen_protocol != protocol:
            raise ValueError("Resume protocol differs from the frozen run.")
        frozen_memory = load_model(
            run_dir / "memory_snapshot.json",
            ControllerMemorySnapshot,
        )
        if (
            controller_memory_payload_sha256(frozen_memory)
            != frozen_memory.memory_sha256
        ):
            raise ValueError("Frozen resume memory hash is not canonical.")
        if (
            frozen_memory.memory_id != memory.snapshot.memory_id
            or frozen_memory.memory_sha256 != memory.snapshot.memory_sha256
            or controller_memory_payload_sha256(memory.snapshot)
            != memory.snapshot.memory_sha256
        ):
            raise ValueError("Resume memory differs from the frozen run.")
        manifest = json.loads(
            (run_dir / "manifest.json").read_text(encoding="utf-8")
        )
        _validate_resume_manifest(
            manifest,
            run_dir=run_dir,
            protocol=protocol,
            source_paths=source_paths,
            source_file_hashes=source_file_hashes,
        )
        resume_status = manifest.get("status")
        _validate_resume_artifact_hashes(
            manifest,
            run_dir=run_dir,
        )
        (
            completed,
            truncated_bytes,
            truncated_tail_sha256,
        ) = _load_completed_traces(
            run_dir / "episodes.jsonl",
            protocol=protocol,
            memory_id=memory.snapshot.memory_id,
            memory_sha256=memory.snapshot.memory_sha256,
            memory_snapshot=memory.snapshot,
            expected_questions=expected_questions,
        )
        if resume_status == "completed":
            expected_ids = sorted(expected_questions)
            if sorted(completed) != expected_ids or manifest.get(
                "completed_question_ids"
            ) != expected_ids:
                raise ValueError(
                    "Completed run does not contain the frozen question set."
                )
            recorded_metrics = json.loads(
                (run_dir / "metrics.json").read_text(encoding="utf-8")
            )
            expected_metrics = _build_metrics_payload(items, completed)
            if recorded_metrics != expected_metrics:
                raise ValueError(
                    "Completed run metrics differ from canonical trace "
                    "evaluation."
                )
            return run_dir
        manifest["status"] = "running"
        if truncated_bytes:
            recovery_events = manifest.setdefault("recovery_events", [])
            if not isinstance(recovery_events, list):
                raise ValueError("Resume manifest recovery_events is invalid.")
            recovery_events.append(
                {
                    "type": "truncated_jsonl_tail",
                    "removed_bytes": truncated_bytes,
                    "removed_tail_sha256": truncated_tail_sha256,
                    "recovered_at": datetime.now(UTC).isoformat(),
                }
            )
        manifest["updated_at"] = datetime.now(UTC).isoformat()
        write_json_atomic(run_dir / "manifest.json", manifest)

    episodes_path = run_dir / "episodes.jsonl"
    try:
        for item in items:
            if item.question_id in completed:
                continue
            trace = run_controller_episode(
                question_id=item.question_id,
                question=item.question,
                policy=policy,
                memory=memory,
                verifier=verifier,
                answerer=answerer,
                protocol=protocol,
                memory_id=memory.snapshot.memory_id,
                trace_id=f"{run_dir.name}-{item.question_id}",
            )
            _validate_trace_against_protocol(
                trace,
                protocol,
                memory.snapshot,
            )
            with episodes_path.open("a", encoding="utf-8") as handle:
                handle.write(trace.model_dump_json() + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            completed[item.question_id] = trace
            manifest["completed_question_ids"] = sorted(completed)
            manifest["updated_at"] = datetime.now(UTC).isoformat()
            write_json_atomic(run_dir / "manifest.json", manifest)

        metrics_payload = _build_metrics_payload(items, completed)
        write_json_atomic(run_dir / "metrics.json", metrics_payload)
        manifest["status"] = "completed"
        manifest["completed_question_ids"] = sorted(completed)
        manifest["artifact_sha256s"] = {
            "resolved_protocol.json": sha256_file(
                run_dir / "resolved_protocol.json"
            ),
            "memory_snapshot.json": sha256_file(
                run_dir / "memory_snapshot.json"
            ),
            "episodes.jsonl": sha256_file(episodes_path),
            "metrics.json": sha256_file(run_dir / "metrics.json"),
        }
        manifest["updated_at"] = datetime.now(UTC).isoformat()
        write_json_atomic(run_dir / "manifest.json", manifest)
    except Exception:
        manifest["status"] = "failed"
        manifest["completed_question_ids"] = sorted(completed)
        manifest["updated_at"] = datetime.now(UTC).isoformat()
        write_json_atomic(run_dir / "manifest.json", manifest)
        raise
    return run_dir


def _build_metrics_payload(
    items: Sequence[RagBenchmarkItem],
    completed: dict[str, ControllerTrace],
) -> dict[str, object]:
    target_items = {item.question_id: item for item in items}
    traces = [completed[item.question_id] for item in items]
    per_item = [
        evaluate_controller_trace(
            trace,
            target_from_legacy_item(target_items[trace.question_id]),
        )
        for trace in traces
    ]
    aggregate = aggregate_controller_metrics(per_item)
    return {
        "schema_version": "1.0",
        "evaluation_scope": "legacy_card_proxy_development_debug",
        "paper_claim_eligible": False,
        "aggregate": aggregate.model_dump(mode="json"),
        "by_item": [
            metric.model_dump(mode="json") for metric in per_item
        ],
        "failed_question_ids": [
            trace.question_id
            for trace in traces
            if trace.status == "failed"
        ],
    }


@contextmanager
def _exclusive_run_lock(
    run_dir: Path,
    *,
    require_existing: bool = True,
):
    """Hold one sibling lock across a new run or resume lifecycle."""

    run_dir.parent.mkdir(parents=True, exist_ok=True)
    lock_path = (
        run_dir.parent / f".{run_dir.name}.controller-run.lock"
    )
    try:
        descriptor = os.open(
            lock_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        )
    except FileExistsError as exc:
        raise RuntimeError(
            "Controller run is already locked: "
            f"{run_dir}. If no process is active, inspect and remove the "
            f"stale {lock_path.name} file."
        ) from exc
    try:
        if require_existing and not run_dir.is_dir():
            raise FileNotFoundError(
                f"Resume run does not exist: {run_dir}"
            )
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "acquired_at": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=True,
        ).encode("utf-8")
        os.write(descriptor, payload)
        os.fsync(descriptor)
        yield
    finally:
        os.close(descriptor)
        lock_path.unlink(missing_ok=True)


def _build_policy(protocol: ControllerProtocol) -> ControllerPolicy:
    if protocol.policy_name not in POLICY_NAMES:
        raise ValueError(f"Unknown protocol policy: {protocol.policy_name}")
    top_k = getattr(protocol.policy_config, "top_k")
    if protocol.policy_name == "fixed_dense":
        return FixedDenseController(top_k=top_k)
    if protocol.policy_name == "fixed_dense_typed_graph":
        return FixedDenseTypedGraphController(top_k=top_k)
    if protocol.policy_name == "evidence_gap":
        return EvidenceGapController(top_k=top_k)
    raise AssertionError("Validated policy dispatch is incomplete.")


def _load_completed_traces(
    path: Path,
    *,
    protocol: ControllerProtocol,
    memory_id: str,
    memory_sha256: str,
    memory_snapshot: ControllerMemorySnapshot,
    expected_questions: dict[str, str],
) -> tuple[dict[str, ControllerTrace], int, str | None]:
    if not path.exists():
        return {}, 0, None
    raw = path.read_bytes()
    truncated_bytes = 0
    truncated_tail_sha256: str | None = None
    if raw and not raw.endswith(b"\n"):
        final_newline = raw.rfind(b"\n")
        prefix_end = final_newline + 1
        tail = raw[prefix_end:]
        try:
            parsed_tail = json.loads(tail.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            truncated_bytes = len(tail)
            truncated_tail_sha256 = hashlib.sha256(tail).hexdigest()
            with path.open("r+b") as handle:
                handle.truncate(prefix_end)
            raw = raw[:prefix_end]
        else:
            ControllerTrace.model_validate(parsed_tail)
            with path.open("ab") as handle:
                handle.write(b"\n")
            raw += b"\n"
    completed: dict[str, ControllerTrace] = {}
    for line_number, line in enumerate(
        raw.decode("utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        trace = ControllerTrace.model_validate_json(line)
        expected_question = expected_questions.get(trace.question_id)
        if expected_question is None:
            raise ValueError(
                f"Episode at line {line_number} is outside the frozen run: "
                f"{trace.question_id}"
            )
        mismatches = []
        if trace.protocol_id != protocol.protocol_id:
            mismatches.append("protocol_id")
        if trace.protocol_sha256 != protocol.protocol_sha256:
            mismatches.append("protocol_sha256")
        if trace.memory_id != memory_id:
            mismatches.append("memory_id")
        if trace.memory_sha256 != memory_sha256:
            mismatches.append("memory_sha256")
        if trace.policy_name != protocol.policy_name:
            mismatches.append("policy_name")
        if trace.initial_state.question_id != trace.question_id:
            mismatches.append("initial_question_id")
        if trace.initial_state.question != expected_question:
            mismatches.append("initial_question")
        if mismatches:
            raise ValueError(
                f"Episode at line {line_number} does not belong to this run: "
                + ", ".join(mismatches)
            )
        _validate_trace_against_protocol(
            trace,
            protocol,
            memory_snapshot,
        )
        if trace.question_id in completed:
            raise ValueError(
                f"Duplicate episode at line {line_number}: "
                f"{trace.question_id}"
            )
        completed[trace.question_id] = trace
    return completed, truncated_bytes, truncated_tail_sha256


def _validate_trace_against_protocol(
    trace: ControllerTrace,
    protocol: ControllerProtocol,
    memory: ControllerMemorySnapshot,
) -> None:
    concepts = {item.concept_id: item for item in memory.concepts}
    evidence = {item.evidence_id: item for item in memory.evidence}
    relations = {item.relation_id: item for item in memory.relations}
    if (
        trace.created_at.utcoffset() is None
        or trace.completed_at.utcoffset() is None
    ):
        raise ValueError("Episode trace timestamps must be timezone-aware.")
    _audit_policy_cost(
        trace.terminal_decision_cost,
        protocol.budget,
        require_metered_completion=trace.stop_reason != "policy_error",
    )
    try:
        ControllerCost.model_validate(
            trace.terminal_environment_cost.model_dump()
        )
    except Exception as exc:
        raise ValueError(
            f"Episode terminal environment cost is invalid: {exc}"
        ) from exc
    if (
        trace.terminal_environment_cost != ControllerCost()
        and trace.stop_reason != "environment_error"
    ):
        raise ValueError(
            "Episode has terminal environment cost without an "
            "environment error."
        )
    if trace.stop_reason in {"max_steps", "no_progress"} and (
        trace.terminal_decision_cost != ControllerCost()
        or trace.terminal_environment_cost != ControllerCost()
    ):
        raise ValueError(
            f"Episode {trace.stop_reason} stop has fake terminal cost."
        )
    if trace.terminal_environment_cost.steps > 1:
        raise ValueError(
            "Episode terminal environment cost spans multiple actions."
        )
    for step in trace.steps:
        if step.state_before.cost.exceeded_limits(protocol.budget):
            raise ValueError(
                "Episode executes a step after exhausting its budget."
            )
        _audit_policy_cost(step.decision_cost, protocol.budget)
        projected = (
            step.state_before.cost.plus(step.decision_cost).plus(
                controller_minimum_action_cost(step.action)
            )
        )
        if projected.exceeded_limits(protocol.budget):
            raise ValueError(
                "Episode executes an action after its decision exhausted "
                "the budget."
            )
        _guard_action(step.action, step.state_before, protocol)
        _audit_observation(
            step.action,
            step.observation,
            step.state_before,
            protocol.budget,
        )
        observation = step.observation
        if isinstance(observation, ConceptSearchObservation):
            for hit in observation.hits:
                if hit.concept_id not in concepts:
                    raise ValueError(
                        "Episode contains a concept hit outside memory."
                    )
                if hit.retrieval_source != "dense_card_proxy":
                    raise ValueError(
                        "Episode concept hit has the wrong retrieval source."
                    )
        elif isinstance(observation, EvidenceSearchObservation):
            for hit in observation.hits:
                node = evidence.get(hit.evidence_id)
                if node is None or (
                    hit.concept_id != node.concept_id
                    or hit.claim_id != node.claim_id
                ):
                    raise ValueError(
                        "Episode evidence hit conflicts with frozen memory."
                    )
                if hit.retrieval_source != "bm25_evidence":
                    raise ValueError(
                        "Episode evidence hit has the wrong retrieval source."
                    )
        elif isinstance(observation, GraphExpansionObservation):
            for hit in observation.hits:
                edge = relations.get(hit.relation_id)
                if edge is None or (
                    hit.source_concept_id != edge.source_concept_id
                    or hit.target_concept_id != edge.target_concept_id
                    or hit.relation_type != edge.relation_type
                    or hit.score != edge.score
                ):
                    raise ValueError(
                        "Episode graph hit conflicts with frozen memory."
                    )
    total_cost = (
        trace.final_state.cost.plus(trace.terminal_decision_cost).plus(
            trace.terminal_environment_cost
        )
    )
    if trace.stop_reason == "budget_exhausted":
        projected_cost = total_cost
        if trace.terminal_proposed_action is not None:
            _guard_action(
                trace.terminal_proposed_action,
                trace.final_state,
                protocol,
                check_budget=False,
            )
            expected_minimum = controller_minimum_action_cost(
                trace.terminal_proposed_action
            )
            if trace.terminal_minimum_action_cost != expected_minimum:
                raise ValueError(
                    "Episode terminal minimum action cost is not canonical."
                )
            projected_cost = projected_cost.plus(expected_minimum)
        elif trace.terminal_minimum_action_cost != ControllerCost():
            raise ValueError(
                "Episode terminal minimum cost has no proposed action."
            )
        expected_exhausted = projected_cost.exceeded_limits(
            protocol.budget
        )
        if not expected_exhausted:
            raise ValueError(
                "Episode claims budget exhaustion without exhausting a "
                "limit."
            )
        if trace.budget_exhausted_fields != expected_exhausted:
            raise ValueError(
                "Episode budget-exhausted fields are not canonical."
            )
    elif (
        trace.terminal_proposed_action is not None
        or trace.terminal_minimum_action_cost != ControllerCost()
        or trace.budget_exhausted_fields
    ):
        raise ValueError(
            "Episode has budget-exhaustion metadata for another stop."
        )
    if (
        trace.stop_reason == "max_steps"
        and trace.final_state.step_index < protocol.budget.max_steps
    ):
        raise ValueError(
            "Episode claims max_steps before reaching the step limit."
        )
    if (
        trace.stop_reason == "no_progress"
        and trace.final_state.consecutive_no_progress
        < protocol.budget.max_consecutive_no_progress
    ):
        raise ValueError(
            "Episode claims no_progress before reaching its threshold."
        )


def _new_manifest(
    *,
    run_id: str,
    protocol: ControllerProtocol,
    source_paths: dict[str, Path],
    source_file_hashes: dict[str, str],
) -> dict[str, object]:
    git_commit, git_dirty = _git_provenance()
    now = datetime.now(UTC).isoformat()
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "status": "running",
        "paper_claim_eligible": False,
        "protocol_id": protocol.protocol_id,
        "protocol_sha256": protocol.protocol_sha256,
        "policy_name": protocol.policy_name,
        "split": protocol.split,
        "source_paths": {
            name: str(path) for name, path in source_paths.items()
        },
        "source_file_sha256s": source_file_hashes,
        "completed_question_ids": [],
        "artifact_sha256s": {},
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "code_sha256": controller_code_sha256(),
        "query_encoder_sha256": protocol.query_encoder_sha256,
        "python": sys.version,
        "platform": platform.platform(),
        "dependency_versions": _dependency_versions(),
        "started_at": now,
        "updated_at": now,
    }


def _validate_resume_manifest(
    manifest: dict[str, object],
    *,
    run_dir: Path,
    protocol: ControllerProtocol,
    source_paths: dict[str, Path],
    source_file_hashes: dict[str, str],
) -> None:
    git_commit, git_dirty = _git_provenance()
    expected = {
        "schema_version": "1.0",
        "run_id": run_dir.name,
        "paper_claim_eligible": False,
        "protocol_id": protocol.protocol_id,
        "protocol_sha256": protocol.protocol_sha256,
        "policy_name": protocol.policy_name,
        "split": protocol.split,
        "source_paths": {
            name: str(path) for name, path in source_paths.items()
        },
        "source_file_sha256s": source_file_hashes,
        "code_sha256": controller_code_sha256(),
        "query_encoder_sha256": protocol.query_encoder_sha256,
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "python": sys.version,
        "platform": platform.platform(),
        "dependency_versions": _dependency_versions(),
    }
    mismatches = [
        field
        for field, value in expected.items()
        if manifest.get(field) != value
    ]
    if mismatches:
        raise ValueError(
            "Resume manifest differs from the frozen run: "
            + ", ".join(mismatches)
        )
    if manifest.get("status") not in {"running", "failed", "completed"}:
        raise ValueError("Resume manifest has an invalid status.")


def _validate_resume_artifact_hashes(
    manifest: dict[str, object],
    *,
    run_dir: Path,
) -> None:
    required_names = {
        "resolved_protocol.json",
        "memory_snapshot.json",
        "episodes.jsonl",
        "metrics.json",
    }
    raw_hashes = manifest.get("artifact_sha256s")
    if not isinstance(raw_hashes, dict):
        raise ValueError("Resume manifest artifact_sha256s is invalid.")
    unknown_names = sorted(set(raw_hashes).difference(required_names))
    if unknown_names:
        raise ValueError(
            "Resume manifest declares unknown artifacts: "
            + ", ".join(unknown_names)
        )
    if manifest.get("status") == "completed" and set(
        raw_hashes
    ) != required_names:
        missing = sorted(required_names.difference(raw_hashes))
        raise ValueError(
            "Completed run is missing artifact hashes: "
            + ", ".join(missing)
        )
    for name, expected_hash in raw_hashes.items():
        if not isinstance(expected_hash, str) or len(expected_hash) != 64:
            raise ValueError(
                f"Resume manifest has an invalid artifact hash: {name}"
            )
        path = run_dir / name
        if not path.is_file():
            raise ValueError(f"Resume artifact is missing: {name}")
        if sha256_file(path) != expected_hash:
            raise ValueError(
                f"Resume artifact hash mismatch: {name}"
            )


def _git_provenance() -> tuple[str | None, bool | None]:
    repository = Path(__file__).resolve().parents[2]
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        return commit, dirty
    except (OSError, subprocess.CalledProcessError):
        return None, None


def _dependency_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for package in RUNTIME_DEPENDENCIES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def controller_code_sha256() -> str:
    repository_root = Path(__file__).resolve().parents[2]
    package_root = repository_root / "backend" / "rag_lab"
    source_paths = sorted(
        [
            *package_root.glob("controller_*.py"),
            package_root / "run_controller_experiment.py",
            package_root / "benchmark.py",
            package_root / "io.py",
            package_root / "retrievers.py",
            package_root / "reviews.py",
            package_root / "schemas.py",
            *(
                package_root / "controller_benchmark"
            ).glob("*.py"),
            repository_root / "backend" / "app" / "embedding.py",
            repository_root / "backend" / "pyproject.toml",
            repository_root / "backend" / "uv.lock",
        ],
        key=lambda path: path.relative_to(repository_root).as_posix(),
    )
    payload = [
        {
            "path": path.relative_to(repository_root).as_posix(),
            "sha256": sha256_file(path),
        }
        for path in source_paths
        if path.is_file()
    ]
    return sha256_value(payload)


def query_encoder_sha256(model_source: Path) -> str:
    """Hash exact local encoder files, independent of cache timestamps."""

    source = model_source.resolve()
    if not source.is_dir():
        raise ValueError(f"Query encoder source is not a directory: {source}")
    files = sorted(
        (path for path in source.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(source).as_posix(),
    )
    if not files:
        raise ValueError("Query encoder source has no files.")
    return sha256_value(
        [
            {
                "path": path.relative_to(source).as_posix(),
                "sha256": sha256_file(path),
            }
            for path in files
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
