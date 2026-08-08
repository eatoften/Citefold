from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pypdf import PdfWriter

from golden_graph import pdf_projection_worker
from golden_graph.pdf_projection_worker import (
    ProjectionWorkerError,
    extract_pdf_pages,
    normalize_pdf_text,
)
from golden_graph.utf8_chunker import chunk_utf8_text


class _FakePage:
    def __init__(self, text: str | None = None, *, error: Exception | None = None):
        self._text = text
        self._error = error

    def extract_text(self, *, extraction_mode: str) -> str | None:
        assert extraction_mode == "plain"
        if self._error is not None:
            raise self._error
        return self._text


class _FakePages:
    def __init__(self, pages: list[_FakePage | Exception]):
        self._pages = pages

    def __len__(self) -> int:
        return len(self._pages)

    def __getitem__(self, index: int) -> _FakePage:
        value = self._pages[index]
        if isinstance(value, Exception):
            raise value
        return value


class _FakeReader:
    def __init__(
        self,
        pages: list[_FakePage | Exception],
        *,
        encrypted: bool = False,
    ):
        self.pages = _FakePages(pages)
        self.is_encrypted = encrypted


def _write_pdf_shaped_asset(path: Path) -> tuple[int, str]:
    payload = b"%PDF-1.7\nsource-slice-test\n%%EOF\n"
    path.write_bytes(payload)
    return len(payload), hashlib.sha256(payload).hexdigest()


def _extract_fake_pdf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pages: list[_FakePage | Exception],
    *,
    encrypted: bool = False,
    max_pdf_bytes: int = 1024,
    max_pages: int = 100,
    max_page_utf8_bytes: int = 1024,
    max_total_utf8_bytes: int = 4096,
) -> dict[str, object]:
    input_path = tmp_path / "fixture.pdf"
    expected_bytes, expected_sha256 = _write_pdf_shaped_asset(input_path)
    monkeypatch.setattr(
        pdf_projection_worker,
        "PdfReader",
        lambda _stream, strict: _FakeReader(pages, encrypted=encrypted),
    )
    return extract_pdf_pages(
        input_path,
        expected_sha256=expected_sha256,
        expected_bytes=expected_bytes,
        max_pdf_bytes=max_pdf_bytes,
        max_pages=max_pages,
        max_page_utf8_bytes=max_page_utf8_bytes,
        max_total_utf8_bytes=max_total_utf8_bytes,
    )


def test_unicode_nfkc_lf_v1_preserves_non_normalized_whitespace_and_nul() -> None:
    source = "Ａ\r\n e\u0301 \t\r\r\n\u00a0\x00"

    assert normalize_pdf_text(source) == "A\n é \t\n\n \x00"


def test_chunker_is_codepoint_safe_deterministic_and_covers_all_utf8_bytes() -> None:
    text = "ab中🙂e\u0301XYZ"
    encoded = text.encode("utf-8")

    first = chunk_utf8_text(
        text,
        max_chunk_utf8_bytes=7,
        overlap_utf8_bytes=3,
    )
    second = chunk_utf8_text(
        text,
        max_chunk_utf8_bytes=7,
        overlap_utf8_bytes=3,
    )

    assert first == second
    assert first[0].start_offset == 0
    assert first[-1].end_offset == len(encoded)
    covered = [False] * len(encoded)
    for index, window in enumerate(first):
        payload = encoded[window.start_offset : window.end_offset]
        assert 0 < len(payload) <= 7
        assert payload.decode("utf-8") == window.text
        assert hashlib.sha256(payload).hexdigest() == window.semantic_sha256
        for byte_index in range(window.start_offset, window.end_offset):
            covered[byte_index] = True
        if index:
            previous = first[index - 1]
            assert window.start_offset > previous.start_offset
            assert window.start_offset <= previous.end_offset
            assert previous.end_offset - window.start_offset <= 3
    assert all(covered)


def test_chunker_supports_four_byte_codepoints_at_the_minimum_limit() -> None:
    windows = chunk_utf8_text(
        "🙂🙂",
        max_chunk_utf8_bytes=4,
        overlap_utf8_bytes=3,
    )

    assert [window.text for window in windows] == ["🙂", "🙂"]
    assert [(window.start_offset, window.end_offset) for window in windows] == [
        (0, 4),
        (4, 8),
    ]


@pytest.mark.parametrize(
    ("max_bytes", "overlap"),
    [(3, 0), (4, -1), (4, 4)],
)
def test_chunker_rejects_unsafe_limits(max_bytes: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        chunk_utf8_text(
            "content",
            max_chunk_utf8_bytes=max_bytes,
            overlap_utf8_bytes=overlap,
        )


def test_worker_emits_complete_page_inventory_with_typed_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _extract_fake_pdf(
        tmp_path,
        monkeypatch,
        [
            _FakePage("中"),
            _FakePage(" \t\r\n\u00a0"),
            _FakePage(error=RuntimeError("parser internals must not escape")),
            _FakePage("\ud800"),
            _FakePage("123456789"),
        ],
        max_page_utf8_bytes=8,
    )

    pages = payload["pages"]
    assert isinstance(pages, list)
    assert payload["page_count"] == 5
    assert payload["total_semantic_utf8_bytes"] == 3
    assert [page["page_number"] for page in pages] == [1, 2, 3, 4, 5]
    assert [page["logical_page_id"] for page in pages] == [
        "page-0001",
        "page-0002",
        "page-0003",
        "page-0004",
        "page-0005",
    ]
    assert [(page["status"], page["reason_code"]) for page in pages] == [
        ("included", None),
        ("blank", "no_semantic_text"),
        ("parse_failed", "parser_error"),
        ("parse_failed", "unsupported_content"),
        ("parse_failed", "resource_limit"),
    ]
    assert pages[0]["text"] == "中"
    assert pages[0]["semantic_utf8_bytes"] == 3
    assert all(page["text"] is None for page in pages[1:])


def test_worker_marks_page_lookup_failure_and_keeps_later_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _extract_fake_pdf(
        tmp_path,
        monkeypatch,
        [_FakePage("before"), RuntimeError("bad page object"), _FakePage("after")],
    )

    pages = payload["pages"]
    assert isinstance(pages, list)
    assert [(page["status"], page["text"]) for page in pages] == [
        ("included", "before"),
        ("parse_failed", None),
        ("included", "after"),
    ]
    assert pages[1]["reason_code"] == "parser_error"


def test_worker_fails_the_whole_build_at_the_total_text_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(
        ProjectionWorkerError,
        match="semantic text exceeds the total parser limit",
    ):
        _extract_fake_pdf(
            tmp_path,
            monkeypatch,
            [_FakePage("abcd"), _FakePage("efgh")],
            max_total_utf8_bytes=7,
        )


def test_worker_rejects_page_count_over_the_asset_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ProjectionWorkerError, match="page count"):
        _extract_fake_pdf(
            tmp_path,
            monkeypatch,
            [_FakePage("one"), _FakePage("two")],
            max_pages=1,
        )


def test_worker_rejects_asset_over_the_raw_byte_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ProjectionWorkerError, match="size exceeds"):
        _extract_fake_pdf(
            tmp_path,
            monkeypatch,
            [_FakePage("one")],
            max_pdf_bytes=8,
        )


def test_worker_wraps_pdf_reader_initialization_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "fixture.pdf"
    expected_bytes, expected_sha256 = _write_pdf_shaped_asset(input_path)

    def _raise_reader_error(_stream: object, *, strict: bool) -> object:
        assert strict is False
        raise RuntimeError("malformed cross-reference table")

    monkeypatch.setattr(pdf_projection_worker, "PdfReader", _raise_reader_error)

    with pytest.raises(ProjectionWorkerError, match="initialization failed safely"):
        extract_pdf_pages(
            input_path,
            expected_sha256=expected_sha256,
            expected_bytes=expected_bytes,
            max_pdf_bytes=1024,
            max_pages=10,
            max_page_utf8_bytes=1024,
            max_total_utf8_bytes=4096,
        )


def test_worker_rejects_a_real_encrypted_pdf(tmp_path: Path) -> None:
    input_path = tmp_path / "encrypted.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.encrypt("not-the-benchmark-password")
    with input_path.open("wb") as output:
        writer.write(output)
    raw_bytes = input_path.read_bytes()

    with pytest.raises(ProjectionWorkerError, match="Encrypted PDFs are rejected"):
        extract_pdf_pages(
            input_path,
            expected_sha256=hashlib.sha256(raw_bytes).hexdigest(),
            expected_bytes=len(raw_bytes),
            max_pdf_bytes=len(raw_bytes),
            max_pages=10,
            max_page_utf8_bytes=1024,
            max_total_utf8_bytes=4096,
        )
