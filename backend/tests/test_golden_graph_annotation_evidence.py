from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from golden_graph.annotation_evidence import (
    AnnotationEvidenceError,
    AnnotationEvidenceSourceAuthority,
    bind_annotation_evidence_source,
    evidence_span_sort_key,
    reject_public_source_copy,
    resolve_evidence_selection,
    validate_public_evidence_span,
)
import golden_graph.annotation_evidence as evidence_module
from golden_graph.source_slice_builder import PrivateSourceSliceMaterializationReceipt
from golden_graph.annotation_models import EvidenceSelectionDraft, EvidenceSpan


def test_exact_unicode_quote_resolves_to_redacted_utf8_span() -> None:
    page_start = 113
    text = "前缀内容 · α private evidence phrase · 后缀内容"
    quote_text = "α private evidence phrase"
    source, chunk_sha = _source_materialization(text, page_start=page_start)
    expected_start = page_start + text.encode("utf-8").index(
        quote_text.encode("utf-8")
    )
    selection = _selection(
        quote_text,
        chunk_sha=chunk_sha,
        page_global_utf8_start=expected_start,
    )

    span = resolve_evidence_selection(
        selection,
        source_authority=source,
    )

    assert span.page_utf8_start == expected_start
    assert span.page_utf8_end == expected_start + len(quote_text.encode("utf-8"))
    assert span.semantic_span_sha256 == hashlib.sha256(
        quote_text.encode("utf-8")
    ).hexdigest()
    assert span.offset_unit == "utf8_bytes"
    assert "exact_quote" not in span.model_dump(mode="json")
    assert quote_text not in repr(span)
    assert quote_text not in span.model_dump_json()
    validate_public_evidence_span(span, source_authority=source)


def test_repeated_quote_requires_explicit_start_without_leaking_quote() -> None:
    private_quote = "private Ω quote"
    text = f"prefix {private_quote}; middle {private_quote}; suffix"
    source, chunk_sha = _source_materialization(text, page_start=41)
    ambiguous = _selection(private_quote, chunk_sha=chunk_sha)

    with pytest.raises(AnnotationEvidenceError, match="unambiguous") as captured:
        resolve_evidence_selection(ambiguous, source_authority=source)

    assert private_quote not in str(captured.value)
    assert captured.value.__cause__ is None

    second_local_start = text.encode("utf-8").rindex(private_quote.encode("utf-8"))
    explicit = _selection(
        private_quote,
        chunk_sha=chunk_sha,
        page_global_utf8_start=41 + second_local_start,
    )
    resolved = resolve_evidence_selection(explicit, source_authority=source)
    assert resolved.page_utf8_start == 41 + second_local_start

    wrong = _selection(
        private_quote,
        chunk_sha=chunk_sha,
        page_global_utf8_start=42 + second_local_start,
    )
    with pytest.raises(AnnotationEvidenceError, match="does not resolve") as wrong_exc:
        resolve_evidence_selection(wrong, source_authority=source)
    assert private_quote not in str(wrong_exc.value)
    assert wrong_exc.value.__cause__ is None


def test_invalid_private_unicode_fails_with_bounded_error() -> None:
    source, chunk_sha = _source_materialization("ordinary frozen source")
    private_quote = "secret\ud800quote"
    selection = SimpleNamespace(
        chunk_ordinal=0,
        logical_page_id="page-0001",
        semantic_chunk_sha256=chunk_sha,
        page_global_utf8_start=None,
        exact_quote=private_quote,
    )

    with pytest.raises(AnnotationEvidenceError, match="valid UTF-8") as captured:
        resolve_evidence_selection(selection, source_authority=source)

    assert "secret" not in str(captured.value)
    assert captured.value.__cause__ is None


def test_public_span_replay_rejects_hash_bounds_and_utf8_split() -> None:
    text = "prefix α evidence suffix"
    source, chunk_sha = _source_materialization(text, page_start=29)
    selection = _selection("α evidence", chunk_sha=chunk_sha)
    valid = resolve_evidence_selection(selection, source_authority=source)
    validate_public_evidence_span(valid, source_authority=source)

    bad_hash = valid.model_copy(update={"semantic_span_sha256": "0" * 64})
    with pytest.raises(AnnotationEvidenceError, match="span hash"):
        validate_public_evidence_span(bad_hash, source_authority=source)

    out_of_bounds = valid.model_copy(update={"page_utf8_end": 10_000})
    with pytest.raises(AnnotationEvidenceError, match="outside"):
        validate_public_evidence_span(out_of_bounds, source_authority=source)

    alpha_start = 29 + text.encode("utf-8").index("α".encode("utf-8"))
    continuation_byte = text.encode("utf-8")[
        alpha_start - 29 + 1 : alpha_start - 29 + 2
    ]
    split = valid.model_copy(
        update={
            "page_utf8_start": alpha_start + 1,
            "page_utf8_end": alpha_start + 2,
            "semantic_span_sha256": hashlib.sha256(continuation_byte).hexdigest(),
        }
    )
    with pytest.raises(AnnotationEvidenceError, match="UTF-8 code point") as captured:
        validate_public_evidence_span(split, source_authority=source)
    assert captured.value.__cause__ is None


def test_chunk_identity_and_window_tampering_fail_closed() -> None:
    source, chunk_sha = _source_materialization("frozen evidence text")
    selection = _selection("evidence", chunk_sha=chunk_sha)

    wrong_identity = selection.model_copy(
        update={"semantic_chunk_sha256": "f" * 64}
    )
    with pytest.raises(AnnotationEvidenceError, match="frozen semantic Chunk"):
        resolve_evidence_selection(wrong_identity, source_authority=source)

    object.__setattr__(source.chunks[0], "window_end", source.chunks[0].window_end + 1)
    with pytest.raises(AnnotationEvidenceError, match="Chunk integrity"):
        resolve_evidence_selection(selection, source_authority=source)


@pytest.mark.parametrize(
    "mutation",
    ("bytes", "coordinated_chunk", "materialization_binding"),
)
def test_source_authority_integrity_rejects_equal_length_mutation(
    mutation: str,
) -> None:
    source, chunk_sha = _source_materialization("original private phrase")
    chunk = source.chunks[0]
    sentinel = "forged!! private phrase"
    assert len(sentinel.encode("utf-8")) == len(chunk.utf8_bytes)
    if mutation == "bytes":
        object.__setattr__(chunk, "utf8_bytes", sentinel.encode("utf-8"))
    elif mutation == "coordinated_chunk":
        forged_bytes = sentinel.encode("utf-8")
        object.__setattr__(chunk, "text", sentinel)
        object.__setattr__(chunk, "utf8_bytes", forged_bytes)
        object.__setattr__(
            chunk,
            "semantic_chunk_sha256",
            hashlib.sha256(forged_bytes).hexdigest(),
        )
    else:
        object.__setattr__(source, "private_materialization_sha256", "f" * 64)

    selection = _selection("private", chunk_sha=chunk_sha)
    with pytest.raises(
        AnnotationEvidenceError,
        match="Chunk integrity|validated private Source",
    ) as captured:
        resolve_evidence_selection(selection, source_authority=source)
    assert sentinel not in str(captured.value)


def test_selection_runtime_error_is_mapped_without_private_text() -> None:
    source, _chunk_sha = _source_materialization("ordinary frozen source")
    sentinel = "PRIVATE-QUOTE-SENTINEL"

    class ExplodingSelection:
        @property
        def chunk_ordinal(self) -> int:
            raise RuntimeError(sentinel)

    with pytest.raises(AnnotationEvidenceError, match="invalid shape") as captured:
        resolve_evidence_selection(
            ExplodingSelection(),
            source_authority=source,
        )
    assert sentinel not in str(captured.value)
    assert captured.value.__cause__ is None


@pytest.mark.parametrize(
    "value",
    (
        r"C:\Users\reviewer\private.pdf",
        r"\\server\share\private.txt",
        "/Applications/private/course.pdf",
        "/Users/reviewer/private.pdf",
        "/Volumes/private/course.pdf",
        "/tmp/private.txt",
        "/mnt/private/source.txt",
        "/root/private/course.pdf",
        "/etc/private/course.conf",
        "/opt/private/model.bin",
        "file:///Users/reviewer/private.pdf",
        "../private/source.txt",
        "~/private/source.txt",
        "reviewer@example.invalid",
    ),
)
def test_public_privacy_rejects_path_uri_and_email_families(value: str) -> None:
    with pytest.raises(AnnotationEvidenceError, match="private path|email-like"):
        reject_public_source_copy(
            (value,),
            source_authority=_privacy_source("unrelated private Source"),
        )


def test_path_fragments_cannot_be_distributed_across_public_fields() -> None:
    with pytest.raises(AnnotationEvidenceError, match="private path"):
        reject_public_source_copy(
            ("C:\\Users\\", "reviewer\\secret.pdf"),
            source_authority=_privacy_source("unrelated private Source"),
        )


def test_deep_percent_encoding_and_nfkc_disguises_are_rejected() -> None:
    source = (
        "twelve exact private source tokens must never become a reversible "
        "public representation in annotation prose"
    )
    variation_hidden = "\ufe0f".join(source)
    for value in (
        quote(source, safe=""),
        quote(variation_hidden, safe=""),
        "%2525252Froot%2525252Fprivate.pdf",
        "%2525252e%2525252e%2525252fsecret.txt",
        "％２５２５２５２Ｆroot％２５２５２５２Ｆprivate.pdf",
    ):
        with pytest.raises(AnnotationEvidenceError, match="percent escape"):
            reject_public_source_copy(
                (value,),
                source_authority=_privacy_source(source),
            )

    reject_public_source_copy(
        ("A measured accuracy can be 100% after rounding.",),
        source_authority=_privacy_source(source),
    )


@pytest.mark.parametrize("control", ("\u200b", "\ufe0f", "\x00"))
def test_unicode_controls_cannot_hide_public_values(control: str) -> None:
    source = "twelve exact source tokens must remain visible to this scanner"
    hidden = control.join(source)

    with pytest.raises(AnnotationEvidenceError, match="invisible Unicode"):
        reject_public_source_copy(
            (hidden,),
            source_authority=_privacy_source(source),
        )


def test_source_copy_scans_aggregate_fields_and_token_windows() -> None:
    long_source = (
        "This deliberately long private Source sentence contains enough exact "
        "characters to cross the configured public copy detection boundary."
    )
    split = long_source.index(" ", 50)
    with pytest.raises(
        AnnotationEvidenceError,
        match="long verbatim",
    ) as copied:
        reject_public_source_copy(
            (long_source[:split], long_source[split + 1 :]),
            source_authority=_privacy_source(long_source),
        )
    assert long_source not in str(copied.value)
    assert copied.value.__cause__ is None

    short_token_source = "a b c d e f g h i j k l"
    assert len(short_token_source) < 80
    with pytest.raises(AnnotationEvidenceError, match="token sequence"):
        reject_public_source_copy(
            (short_token_source.upper(),),
            source_authority=_privacy_source(short_token_source),
        )


def test_source_copy_scan_reconstructs_contiguous_chunk_boundaries() -> None:
    first = "zero one two three four five "
    second = "six seven eight nine ten eleven"
    authority = _multi_chunk_source_authority((first, second))
    copied = first + second
    assert len(copied.split()) == 12

    with pytest.raises(AnnotationEvidenceError, match="token sequence"):
        reject_public_source_copy(
            (copied,),
            source_authority=authority,
        )


def test_source_root_integrity_rejects_coordinated_tail_truncation() -> None:
    first = "retained source segment with enough harmless context "
    omitted = "one two three four five six seven eight nine ten eleven twelve"
    authority = _multi_chunk_source_authority((first, omitted))
    object.__setattr__(authority, "chunks", authority.chunks[:-1])
    object.__setattr__(
        authority,
        "chunk_integrity_tags",
        authority.chunk_integrity_tags[:-1],
    )
    object.__setattr__(authority, "private_source_texts", (first,))

    with pytest.raises(
        AnnotationEvidenceError,
        match="validated private Source",
    ) as captured:
        reject_public_source_copy(
            (omitted,),
            source_authority=authority,
        )
    assert omitted not in str(captured.value)


def test_privacy_scanner_fails_closed_without_private_source_authority() -> None:
    with pytest.raises(AnnotationEvidenceError, match="validated private Source"):
        reject_public_source_copy(
            ("safe summary",),
            source_authority=SimpleNamespace(),
        )
    with pytest.raises(AnnotationEvidenceError, match="collection is invalid"):
        reject_public_source_copy(
            "safe summary",
            source_authority=_privacy_source("private source"),
        )


def test_binder_and_evidence_reject_forged_source_capabilities(tmp_path: Path) -> None:
    source, chunk_sha = _source_materialization("self consistent evidence text")
    selection = _selection("evidence", chunk_sha=chunk_sha)
    fake_authority = SimpleNamespace(chunks=source.chunks)
    with pytest.raises(AnnotationEvidenceError, match="validated private Source"):
        resolve_evidence_selection(
            selection,
            source_authority=fake_authority,
        )

    fake_receipt = SimpleNamespace(materialization=SimpleNamespace())
    with pytest.raises(AnnotationEvidenceError, match="validated private Source"):
        bind_annotation_evidence_source(fake_receipt)

    tokenless = object.__new__(PrivateSourceSliceMaterializationReceipt)
    object.__setattr__(
        tokenless,
        "artifact_path",
        (tmp_path / "private.json").resolve(),
    )
    object.__setattr__(tokenless, "artifact_sha256", "b" * 64)
    object.__setattr__(tokenless, "materialization", SimpleNamespace())
    object.__setattr__(tokenless, "_validation_token", object())
    with pytest.raises(AnnotationEvidenceError, match="validated private Source"):
        bind_annotation_evidence_source(tokenless)


def test_source_copy_and_percent_escape_cannot_be_split_mid_token() -> None:
    source = (
        "This private Source sentence contains twelve exact confidential tokens "
        "that must never be copied into any public annotation artifact surface."
    )
    authority = _privacy_source(source)
    parts = tuple(source[index : index + 17] for index in range(0, len(source), 17))
    assert "".join(parts) == source
    with pytest.raises(AnnotationEvidenceError, match="long verbatim"):
        reject_public_source_copy(parts, source_authority=authority)

    encoded_parts = tuple(quote(source, safe=""))
    assert not any(
        evidence_module._PERCENT_ESCAPE.search(part)
        for part in encoded_parts
    )
    with pytest.raises(AnnotationEvidenceError, match="percent escape"):
        reject_public_source_copy(encoded_parts, source_authority=authority)

    reject_public_source_copy(
        ("Accuracy reached 100%", "20 samples were evaluated."),
        source_authority=authority,
    )


@pytest.mark.parametrize(
    "value",
    (
        "/usr/local/share/course.pdf",
        "/workspace/secrets/source.pdf",
        "/app/data/source.pdf",
        "/data/source.pdf",
    ),
)
def test_common_posix_absolute_paths_are_rejected(value: str) -> None:
    with pytest.raises(AnnotationEvidenceError, match="private path"):
        reject_public_source_copy(
            (value,),
            source_authority=_privacy_source("unrelated private Source"),
        )


@pytest.mark.parametrize(
    "value",
    (
        "https://example.com/data/course.pdf",
        "A/B testing improved retention",
        "The ratio n/m is dimensionless",
        "/v1/users",
    ),
)
def test_nonlocal_slashes_are_not_misclassified_as_private_paths(value: str) -> None:
    reject_public_source_copy(
        (value,),
        source_authority=_privacy_source("unrelated private Source"),
    )


def test_evidence_sort_key_is_stable_and_contains_no_private_text() -> None:
    evidence = EvidenceSpan(
        chunk_ordinal=2,
        logical_page_id="page-0003",
        semantic_chunk_sha256="a" * 64,
        page_utf8_start=17,
        page_utf8_end=29,
        offset_unit="utf8_bytes",
        semantic_span_sha256="b" * 64,
    )

    assert evidence_span_sort_key(evidence) == (
        2,
        "page-0003",
        17,
        29,
        "a" * 64,
        "b" * 64,
    )


def _selection(
    quote_text: str,
    *,
    chunk_sha: str,
    page_global_utf8_start: int | None = None,
) -> EvidenceSelectionDraft:
    return EvidenceSelectionDraft(
        chunk_ordinal=0,
        logical_page_id="page-0001",
        semantic_chunk_sha256=chunk_sha,
        page_global_utf8_start=page_global_utf8_start,
        exact_quote=quote_text,
    )


def _source_materialization(
    text: str,
    *,
    page_start: int = 0,
) -> tuple[AnnotationEvidenceSourceAuthority, str]:
    encoded = text.encode("utf-8")
    chunk_sha = hashlib.sha256(encoded).hexdigest()
    chunk = evidence_module._FrozenEvidenceChunk(
        ordinal=0,
        logical_page_id="page-0001",
        window_start=page_start,
        window_end=page_start + len(encoded),
        semantic_chunk_sha256=chunk_sha,
        text=text,
        utf8_bytes=encoded,
    )
    return (
        evidence_module._issue_annotation_evidence_source_authority(
            private_materialization_sha256="a" * 64,
            chunks=(chunk,),
        ),
        chunk_sha,
    )


def _privacy_source(text: str) -> AnnotationEvidenceSourceAuthority:
    return _source_materialization(text)[0]


def _multi_chunk_source_authority(
    texts: tuple[str, ...],
) -> AnnotationEvidenceSourceAuthority:
    chunks = []
    page_offset = 0
    for ordinal, text in enumerate(texts):
        encoded = text.encode("utf-8")
        chunks.append(
            evidence_module._FrozenEvidenceChunk(
                ordinal=ordinal,
                logical_page_id="page-0001",
                window_start=page_offset,
                window_end=page_offset + len(encoded),
                semantic_chunk_sha256=hashlib.sha256(encoded).hexdigest(),
                text=text,
                utf8_bytes=encoded,
            )
        )
        page_offset += len(encoded)
    return evidence_module._issue_annotation_evidence_source_authority(
        private_materialization_sha256="c" * 64,
        chunks=tuple(chunks),
    )
