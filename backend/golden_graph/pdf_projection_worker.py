"""Isolated, bounded PDF-to-page-text worker for golden-graph fixtures.

The parent process executes this file directly with Python isolated mode and a
wall-clock timeout. It is intentionally self-contained because its exact file
hash is part of the parser identity. Output contains Source text and therefore
must stay under the private ``backend/data`` boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import unicodedata

from pypdf import PdfReader


_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_READ_SIZE = 128 * 1024


class ProjectionWorkerError(RuntimeError):
    pass


def normalize_pdf_text(value: str) -> str:
    """Apply the exact ``unicode_nfkc_lf_v1`` normalization contract."""

    normalized = unicodedata.normalize("NFKC", value)
    return normalized.replace("\r\n", "\n").replace("\r", "\n")


def extract_pdf_pages(
    input_path: Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
    max_pdf_bytes: int,
    max_pages: int,
    max_page_utf8_bytes: int,
    max_total_utf8_bytes: int,
) -> dict[str, object]:
    """Verify one descriptor and return bounded private page projections."""

    _validate_limits(
        expected_sha256=expected_sha256,
        expected_bytes=expected_bytes,
        max_pdf_bytes=max_pdf_bytes,
        max_pages=max_pages,
        max_page_utf8_bytes=max_page_utf8_bytes,
        max_total_utf8_bytes=max_total_utf8_bytes,
    )
    if expected_bytes <= 0 or expected_bytes > max_pdf_bytes:
        raise ProjectionWorkerError("Registered PDF size exceeds the parser limit")
    descriptor = -1
    try:
        before = input_path.lstat()
        _require_plain_single_link_file(before, input_path)
        if before.st_size != expected_bytes:
            raise ProjectionWorkerError("Registered PDF size mismatch")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(input_path, flags)
        opened = os.fstat(descriptor)
        _require_plain_single_link_file(opened, input_path)
        if _stable_identity(opened) != _stable_identity(before):
            raise ProjectionWorkerError("Registered PDF changed while opening")

        digest = hashlib.sha256()
        leading = b""
        while chunk := os.read(descriptor, _READ_SIZE):
            if not leading:
                leading = chunk[:5]
            digest.update(chunk)
        if leading != b"%PDF-" or digest.hexdigest() != expected_sha256:
            raise ProjectionWorkerError("Registered PDF byte identity mismatch")
        os.lseek(descriptor, 0, os.SEEK_SET)
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            try:
                reader = PdfReader(stream, strict=False)
                if reader.is_encrypted:
                    raise ProjectionWorkerError("Encrypted PDFs are rejected")
                page_count = len(reader.pages)
                if not 1 <= page_count <= max_pages:
                    raise ProjectionWorkerError(
                        "PDF page count exceeds the parser limit"
                    )
            except ProjectionWorkerError:
                raise
            except Exception as exc:
                raise ProjectionWorkerError(
                    "PDF parser initialization failed safely"
                ) from exc

            pages: list[dict[str, object]] = []
            total_semantic_bytes = 0
            for page_number in range(1, page_count + 1):
                status = "included"
                reason_code: str | None = None
                text: str | None
                try:
                    page = reader.pages[page_number - 1]
                    text = normalize_pdf_text(
                        page.extract_text(extraction_mode="plain") or ""
                    )
                except Exception:
                    status = "parse_failed"
                    reason_code = "parser_error"
                    text = None

                semantic_bytes = b""
                if text is not None:
                    try:
                        semantic_bytes = text.encode("utf-8", errors="strict")
                    except UnicodeEncodeError:
                        status = "parse_failed"
                        reason_code = "unsupported_content"
                        text = None

                if text is not None and not any(
                    not character.isspace() for character in text
                ):
                    status = "blank"
                    reason_code = "no_semantic_text"
                    text = None
                    semantic_bytes = b""
                elif text is not None and len(semantic_bytes) > max_page_utf8_bytes:
                    status = "parse_failed"
                    reason_code = "resource_limit"
                    text = None
                    semantic_bytes = b""
                if text is not None:
                    if (
                        total_semantic_bytes + len(semantic_bytes)
                        > max_total_utf8_bytes
                    ):
                        raise ProjectionWorkerError(
                            "PDF semantic text exceeds the total parser limit"
                        )
                    total_semantic_bytes += len(semantic_bytes)

                pages.append(
                    {
                        "logical_page_id": f"page-{page_number:04d}",
                        "page_number": page_number,
                        "semantic_page_sha256": hashlib.sha256(
                            semantic_bytes
                        ).hexdigest(),
                        "semantic_utf8_bytes": len(semantic_bytes),
                        "status": status,
                        "reason_code": reason_code,
                        "text": text,
                    }
                )

        final_opened = os.fstat(descriptor)
        after = input_path.lstat()
        if (
            _stable_identity(final_opened) != _stable_identity(opened)
            or _stable_identity(after) != _stable_identity(opened)
        ):
            raise ProjectionWorkerError("Registered PDF changed during parsing")
        return {
            "schema_version": 1,
            "artifact_role": "golden_graph_private_pdf_pages",
            "raw_asset_sha256": expected_sha256,
            "normalization": "unicode_nfkc_lf_v1",
            "page_count": len(pages),
            "total_semantic_utf8_bytes": total_semantic_bytes,
            "pages": pages,
        }
    except ProjectionWorkerError:
        raise
    except Exception as exc:
        raise ProjectionWorkerError("PDF projection failed safely") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _require_plain_single_link_file(metadata: os.stat_result, path: Path) -> None:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or (
            reparse_flag
            and getattr(metadata, "st_file_attributes", 0) & reparse_flag
        )
    ):
        raise ProjectionWorkerError(f"PDF input must be a plain single-link file: {path}")


def _validate_limits(
    *,
    expected_sha256: str,
    expected_bytes: int,
    max_pdf_bytes: int,
    max_pages: int,
    max_page_utf8_bytes: int,
    max_total_utf8_bytes: int,
) -> None:
    if (
        len(expected_sha256) != 64
        or expected_sha256 != expected_sha256.lower()
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise ProjectionWorkerError("Expected SHA-256 must be lowercase hexadecimal")
    if expected_bytes <= 0:
        raise ProjectionWorkerError("Registered PDF size must be positive")
    limits = {
        "max_pdf_bytes": max_pdf_bytes,
        "max_pages": max_pages,
        "max_page_utf8_bytes": max_page_utf8_bytes,
        "max_total_utf8_bytes": max_total_utf8_bytes,
    }
    if any(value <= 0 for value in limits.values()):
        raise ProjectionWorkerError("Parser limits must be positive")


def _stable_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        getattr(metadata, "st_file_attributes", 0),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--expected-bytes", required=True, type=int)
    parser.add_argument("--max-pdf-bytes", required=True, type=int)
    parser.add_argument("--max-pages", required=True, type=int)
    parser.add_argument("--max-page-utf8-bytes", required=True, type=int)
    parser.add_argument("--max-total-utf8-bytes", required=True, type=int)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        payload = extract_pdf_pages(
            args.input,
            expected_sha256=args.expected_sha256,
            expected_bytes=args.expected_bytes,
            max_pdf_bytes=args.max_pdf_bytes,
            max_pages=args.max_pages,
            max_page_utf8_bytes=args.max_page_utf8_bytes,
            max_total_utf8_bytes=args.max_total_utf8_bytes,
        )
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        with args.output.open("xb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        return 0
    except (OSError, ProjectionWorkerError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
