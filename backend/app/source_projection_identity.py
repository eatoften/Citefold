from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from pydantic import TypeAdapter

from .course_source import SourceLocator


SOURCE_PROJECTION_CONTRACT_VERSION = "course-source-projection-v1"
_SOURCE_LOCATOR_ADAPTER = TypeAdapter(SourceLocator)


@dataclass(frozen=True)
class ProjectionManifestChunk:
    """The projection fields that can change an evidence address."""

    id: str
    chunk_type: str
    ordinal: int
    text_hash: str
    locator: object
    chunker_version: str


def build_projection_manifest_hash(
    *,
    source_id: str,
    source_type: str,
    chunks: Iterable[ProjectionManifestChunk],
) -> str:
    """Hash one canonical, complete active Source projection manifest."""

    ordered_chunks = sorted(chunks, key=lambda item: (item.ordinal, item.id))
    payload = {
        "projection_contract_version": SOURCE_PROJECTION_CONTRACT_VERSION,
        "source_id": source_id,
        "source_type": source_type,
        "chunks": [
            {
                "id": item.id,
                "chunk_type": item.chunk_type,
                "ordinal": item.ordinal,
                "text_hash": item.text_hash,
                "locator": _canonical_locator_value(item.locator),
                "chunker_version": item.chunker_version,
            }
            for item in ordered_chunks
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def select_projection_generation_id(
    *,
    current_generation_id: str | None,
    current_manifest_hash: str | None,
    next_manifest_hash: str,
) -> str:
    """Retain an ID only for an identical consecutive projection."""

    if (
        current_generation_id
        and current_manifest_hash == next_manifest_hash
    ):
        return current_generation_id
    return uuid4().hex


def canonical_source_locator_json(locator: object) -> str:
    """Return the typed Locator's canonical JSON representation."""

    return json.dumps(
        _canonical_locator_value(locator),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _canonical_locator_value(value: object) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        value = model_dump(mode="json")
    elif isinstance(value, str):
        value = json.loads(value)
    locator = _SOURCE_LOCATOR_ADAPTER.validate_python(value)
    return _canonical_json_value(locator.model_dump(mode="json"))


def _canonical_json_value(value: object) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Projection locators require finite numbers.")
        if value == 0:
            return 0
        if value.is_integer():
            return int(value)
    return value
