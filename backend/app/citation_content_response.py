from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO
from urllib.parse import quote

from starlette.background import BackgroundTask
from starlette.responses import Response, StreamingResponse

from .citation_target_service import ManagedCitationFile


STREAM_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class _RequestedRange:
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1


class _UnsatisfiableRange(ValueError):
    pass


def build_citation_content_response(
    managed_file: ManagedCitationFile,
    *,
    method: str,
    range_header: str | None,
) -> Response:
    """Stream the exact file handle that passed citation integrity checks."""

    handle = managed_file.handle
    if handle is None:
        raise RuntimeError("Citation content requires an open managed file.")

    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
        "Content-Disposition": _content_disposition(managed_file.filename),
        "X-Content-Type-Options": "nosniff",
    }
    try:
        requested_range = _parse_range(
            range_header,
            managed_file.size_bytes,
        )
    except _UnsatisfiableRange:
        managed_file.close()
        headers["Content-Range"] = f"bytes */{managed_file.size_bytes}"
        headers["Content-Length"] = "0"
        return Response(
            status_code=416,
            headers=headers,
            media_type=managed_file.mime_type,
        )

    if requested_range is None:
        start = 0
        length = managed_file.size_bytes
        status_code = 200
    else:
        start = requested_range.start
        length = requested_range.length
        status_code = 206
        headers["Content-Range"] = (
            f"bytes {requested_range.start}-{requested_range.end}/"
            f"{managed_file.size_bytes}"
        )
    headers["Content-Length"] = str(length)

    if method.upper() == "HEAD":
        managed_file.close()
        return Response(
            status_code=status_code,
            headers=headers,
            media_type=managed_file.mime_type,
        )

    try:
        iterator = _iter_open_file(
            handle,
            start=start,
            length=length,
        )
        return StreamingResponse(
            iterator,
            status_code=status_code,
            headers=headers,
            media_type=managed_file.mime_type,
            background=BackgroundTask(managed_file.close),
        )
    except Exception:
        managed_file.close()
        raise


def _parse_range(
    range_header: str | None,
    size: int,
) -> _RequestedRange | None:
    if range_header is None:
        return None
    unit, separator, value = range_header.partition("=")
    if separator != "=" or unit.strip().lower() != "bytes":
        raise _UnsatisfiableRange
    value = value.strip()
    if not value or "," in value:
        raise _UnsatisfiableRange
    start_text, dash, end_text = value.partition("-")
    if dash != "-":
        raise _UnsatisfiableRange
    try:
        if start_text:
            start = int(start_text)
            if start < 0 or start >= size:
                raise _UnsatisfiableRange
            if end_text:
                end = int(end_text)
                if end < start:
                    raise _UnsatisfiableRange
                end = min(end, size - 1)
            else:
                end = size - 1
        else:
            suffix_length = int(end_text)
            if suffix_length <= 0 or size <= 0:
                raise _UnsatisfiableRange
            start = max(size - suffix_length, 0)
            end = size - 1
    except ValueError as exc:
        raise _UnsatisfiableRange from exc
    return _RequestedRange(start=start, end=end)


def _iter_open_file(
    handle: BinaryIO,
    *,
    start: int,
    length: int,
) -> Iterator[bytes]:
    try:
        handle.seek(start)
        remaining = length
        while remaining > 0:
            chunk = handle.read(min(STREAM_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        handle.close()


def _content_disposition(filename: str) -> str:
    encoded = quote(filename, safe="")
    if encoded == filename:
        return f'inline; filename="{filename}"'
    return f"inline; filename*=utf-8''{encoded}"
