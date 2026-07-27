from __future__ import annotations

import math
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from .settings import get_embedding_settings
from .transcription import TranscriptSegment


DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_EMBEDDING_BATCH_SIZE = 32
SENTENCE_TRANSFORMER_REQUIRED_FILES = {"modules.json"}
MODEL_WEIGHT_FILES = {
    "model.safetensors",
    "pytorch_model.bin",
    "tf_model.h5",
    "flax_model.msgpack",
}

EmbeddingVector = list[float]


class EmbeddingError(RuntimeError):
    pass


class TextEmbedder(Protocol):
    def embed_texts(
        self,
        texts: Sequence[str],
        *,
        batch_size: int | None = None,
    ) -> list[EmbeddingVector]:
        pass


class SentenceTransformerEmbedder:
    def __init__(
        self,
        model_name: str | None = None,
        *,
        model_path: str | Path | None = None,
        batch_size: int | None = None,
        normalize_embeddings: bool = True,
        local_files_only: bool | None = None,
    ) -> None:
        settings = get_embedding_settings()
        self.model_name = model_name or settings.model
        configured_model_path = model_path or settings.model_path
        self.model_path = (
            Path(configured_model_path).expanduser()
            if configured_model_path
            else None
        )
        self.batch_size = batch_size or settings.batch_size
        self.normalize_embeddings = normalize_embeddings
        self.local_files_only = (
            settings.local_files_only
            if local_files_only is None
            else local_files_only
        )
        self._model: object | None = None

    def embed_texts(
        self,
        texts: Sequence[str],
        *,
        batch_size: int | None = None,
    ) -> list[EmbeddingVector]:
        cleaned_texts = [text.strip() for text in texts]

        if not cleaned_texts:
            return []

        if any(not text for text in cleaned_texts):
            raise EmbeddingError("Embedding text cannot be empty.")

        model = self._get_model()
        encoded = model.encode(
            cleaned_texts,
            batch_size=batch_size or self.batch_size,
            normalize_embeddings=self.normalize_embeddings,
            convert_to_numpy=True,
        )

        return [
            [float(value) for value in vector]
            for vector in encoded
        ]

    def get_embedding_dimension(self) -> int:
        model = self._get_model()
        dimension_getter = getattr(
            model,
            "get_sentence_embedding_dimension",
            None,
        )
        if not callable(dimension_getter):
            raise EmbeddingError(
                "The embedding model does not report its vector dimension."
            )
        dimension = dimension_getter()
        if not isinstance(dimension, int) or dimension < 1:
            raise EmbeddingError(
                "The embedding model reported an invalid vector dimension."
            )
        return dimension

    def _get_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise EmbeddingError(
                    "sentence-transformers is not installed. "
                    "Run `uv add sentence-transformers` in the backend "
                    "directory."
                ) from exc

            model_source = resolve_sentence_transformer_model_source(
                self.model_name,
                model_path=self.model_path,
                local_files_only=self.local_files_only,
            )

            try:
                self._model = SentenceTransformer(
                    str(model_source),
                    local_files_only=self.local_files_only,
                )
            except TypeError:
                self._model = SentenceTransformer(str(model_source))

        return self._model


def resolve_sentence_transformer_model_source(
    model_name: str,
    *,
    model_path: str | Path | None = None,
    local_files_only: bool = True,
) -> str | Path:
    explicit_model_path = Path(model_path).expanduser() if model_path else None

    if explicit_model_path is not None:
        return _validate_local_model_path(explicit_model_path)

    model_name_as_path = Path(model_name).expanduser()

    if model_name_as_path.exists():
        return _validate_local_model_path(model_name_as_path)

    cached_snapshot = _find_complete_hf_snapshot(model_name)

    if cached_snapshot is not None:
        return cached_snapshot

    if local_files_only:
        raise EmbeddingError(
            "Local SentenceTransformer model was not found. "
            "Set VCC_EMBEDDING_MODEL_PATH to a complete local model "
            "snapshot, or set VCC_EMBEDDING_LOCAL_FILES_ONLY=false to allow "
            "downloads."
        )

    return model_name


def _validate_local_model_path(model_path: Path) -> Path:
    if not model_path.is_dir():
        raise EmbeddingError(
            f"SentenceTransformer model path does not exist: {model_path}"
        )

    if not _is_complete_sentence_transformer_model(model_path):
        raise EmbeddingError(
            "SentenceTransformer model path is incomplete. Expected "
            "`modules.json` and a model weight file such as "
            "`model.safetensors` or `pytorch_model.bin`: "
            f"{model_path}"
        )

    return model_path


def _find_complete_hf_snapshot(model_name: str) -> Path | None:
    repo_cache_name = _model_name_to_hf_cache_dir_name(model_name)

    if repo_cache_name is None:
        return None

    snapshots_root = _hf_hub_cache_dir() / repo_cache_name / "snapshots"

    if not snapshots_root.is_dir():
        return None

    snapshots = [
        snapshot
        for snapshot in snapshots_root.iterdir()
        if snapshot.is_dir()
        and _is_complete_sentence_transformer_model(snapshot)
    ]

    if not snapshots:
        return None

    return max(snapshots, key=_snapshot_modified_time)


def _hf_hub_cache_dir() -> Path:
    explicit_hub_cache = os.getenv("HUGGINGFACE_HUB_CACHE")

    if explicit_hub_cache:
        return Path(explicit_hub_cache).expanduser()

    hf_home = os.getenv("HF_HOME")

    if hf_home:
        return Path(hf_home).expanduser() / "hub"

    return Path.home() / ".cache" / "huggingface" / "hub"


def _model_name_to_hf_cache_dir_name(model_name: str) -> str | None:
    if "/" not in model_name or "\\" in model_name:
        return None

    return f"models--{model_name.replace('/', '--')}"


def _is_complete_sentence_transformer_model(model_path: Path) -> bool:
    direct_file_names = {
        path.name
        for path in model_path.iterdir()
        if path.is_file()
    }

    if not SENTENCE_TRANSFORMER_REQUIRED_FILES.issubset(direct_file_names):
        return False

    return any(
        path.is_file() and path.name in MODEL_WEIGHT_FILES
        for path in model_path.rglob("*")
    )


def _snapshot_modified_time(snapshot: Path) -> float:
    return max(
        (
            path.stat().st_mtime
            for path in snapshot.rglob("*")
            if path.is_file()
        ),
        default=snapshot.stat().st_mtime,
    )


def build_segment_context_texts(
    segments: Sequence[TranscriptSegment],
    *,
    radius: int = 1,
) -> list[str]:
    if radius < 0:
        raise ValueError("radius must be greater than or equal to 0.")

    context_texts: list[str] = []

    for index in range(len(segments)):
        start_index = max(0, index - radius)
        end_index = min(len(segments), index + radius + 1)
        text = " ".join(
            segment.text.strip()
            for segment in segments[start_index:end_index]
            if segment.text.strip()
        )
        context_texts.append(text)

    return context_texts


def cosine_similarity(
    left: Sequence[float],
    right: Sequence[float],
) -> float:
    if len(left) != len(right):
        raise ValueError("Vectors must have the same dimension.")

    if not left:
        raise ValueError("Vectors cannot be empty.")

    dot_product = sum(
        left_value * right_value
        for left_value, right_value in zip(left, right)
    )
    left_norm = math.sqrt(
        sum(value * value for value in left)
    )
    right_norm = math.sqrt(
        sum(value * value for value in right)
    )

    if left_norm == 0 or right_norm == 0:
        return 0.0

    similarity = dot_product / (left_norm * right_norm)

    return max(-1.0, min(1.0, similarity))


def cosine_distance(
    left: Sequence[float],
    right: Sequence[float],
) -> float:
    return 1.0 - cosine_similarity(left, right)


def adjacent_cosine_distances(
    embeddings: Sequence[Sequence[float]],
) -> list[float]:
    return [
        cosine_distance(embeddings[index], embeddings[index + 1])
        for index in range(len(embeddings) - 1)
    ]
