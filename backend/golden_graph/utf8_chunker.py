"""Deterministic page-local UTF-8 sliding windows.

This module is intentionally self-contained because its exact file hash is a
tool identity in the public golden-graph protocol.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Utf8ChunkWindow:
    start_offset: int
    end_offset: int
    text: str
    semantic_sha256: str


def chunk_utf8_text(
    text: str,
    *,
    max_chunk_utf8_bytes: int,
    overlap_utf8_bytes: int,
) -> tuple[Utf8ChunkWindow, ...]:
    """Cover non-empty UTF-8 text with stable, code-point-safe windows."""

    if max_chunk_utf8_bytes < 4:
        raise ValueError("max_chunk_utf8_bytes must be at least four")
    if not 0 <= overlap_utf8_bytes < max_chunk_utf8_bytes:
        raise ValueError("overlap_utf8_bytes must be smaller than the chunk limit")
    encoded = text.encode("utf-8")
    if not encoded:
        return ()

    windows: list[Utf8ChunkWindow] = []
    start = 0
    while start < len(encoded):
        end = min(start + max_chunk_utf8_bytes, len(encoded))
        if end < len(encoded):
            while end > start and _is_utf8_continuation(encoded[end]):
                end -= 1
        if end <= start:
            raise ValueError("chunk limit cannot contain one UTF-8 code point")
        payload = encoded[start:end]
        windows.append(
            Utf8ChunkWindow(
                start_offset=start,
                end_offset=end,
                text=payload.decode("utf-8"),
                semantic_sha256=hashlib.sha256(payload).hexdigest(),
            )
        )
        if end == len(encoded):
            break
        next_start = max(start + 1, end - overlap_utf8_bytes)
        while next_start < end and _is_utf8_continuation(encoded[next_start]):
            next_start += 1
        if next_start >= end:
            next_start = end
        start = next_start
    return tuple(windows)


def _is_utf8_continuation(value: int) -> bool:
    return value & 0b1100_0000 == 0b1000_0000
