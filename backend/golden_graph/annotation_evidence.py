"""Shared fail-closed evidence and privacy primitives for graph annotation.

The private side accepts an exact Source quote only long enough to resolve it
against a frozen semantic Chunk.  The only returned value is a redacted
``EvidenceSpan`` containing logical coordinates and hashes.  The public side
replays those coordinates and rejects Source copies, private paths, reversible
percent escapes, and Unicode controls that could conceal either.

These helpers establish Source binding and publication hygiene.  They do not
establish that an annotation is semantically correct or human-authored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
from collections.abc import Mapping, Sequence
import json
import re
import secrets
from typing import Protocol
import unicodedata
from urllib.parse import unquote_to_bytes

from pydantic import ValidationError

from .annotation_models import EvidenceSpan
from .canonical_io import CanonicalArtifactError, canonical_json_bytes
from .source_slice_builder import (
    PrivateSourceSliceMaterialization,
    PrivateSourceSliceMaterializationReceipt,
)


PUBLIC_SOURCE_COPY_WINDOW = 80
PUBLIC_SOURCE_TOKEN_WINDOW = 12
MAX_ANNOTATION_SOURCE_MATERIALIZATION_BYTES = 512 * 1024 * 1024

_LOGICAL_PAGE_ID = re.compile(r"^page-[0-9]{4,5}$")
_LOWER_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PERCENT_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")
_LOCAL_PATH_OR_EMAIL = re.compile(
    r"(?:(?<![A-Za-z0-9])[A-Za-z]:[\\/]|\\\\[^\\/\s]+[\\/][^\\/\s]+|"
    r"(?<![A-Za-z0-9:/])/(?:Applications|Users|Volumes|dev|etc|home|mnt|opt|"
    r"private|proc|"
    r"root|run|srv|sys|tmp|var|app|bin|boot|code|data|datasets?|lib|lib64|"
    r"outputs?|sbin|src|storage|usr|workspace|workspaces)(?:[\\/]|$)|"
    r"backend[\\/]data[\\/]|"
    r"file(?:://|%3a%2f%2f)|~[\\/]|(?:\.\.[\\/])+|"
    r"(?:%2e){2}(?:%2f|%5c)|[A-Za-z]%3a(?:%2f|%5c)|"
    r"[^\s@]+@[^\s@]+\.[^\s@]+)",
    re.IGNORECASE,
)

_ANNOTATION_EVIDENCE_SOURCE_TOKEN = object()
_ANNOTATION_EVIDENCE_INTEGRITY_KEY = secrets.token_bytes(32)
_CHUNK_INTEGRITY_DOMAIN = b"vcc-g2-annotation-source-chunk-v1\x00"
_SOURCE_INTEGRITY_DOMAIN = b"vcc-g2-annotation-source-root-v1\x00"


class AnnotationEvidenceError(ValueError):
    """Raised when evidence cannot be resolved or published safely.

    Messages are deliberately bounded and never interpolate Source text,
    exact quotes, local paths, or caller-owned public values.
    """


@dataclass(frozen=True, slots=True)
class _FrozenEvidenceChunk:
    ordinal: int
    logical_page_id: str
    window_start: int
    window_end: int
    semantic_chunk_sha256: str
    text: str = field(repr=False, compare=False)
    utf8_bytes: bytes = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True, init=False)
class AnnotationEvidenceSourceAuthority:
    """Immutable, token-gated Source snapshot for annotation primitives.

    It is intentionally narrower than the private materialization receipt: the
    annotation layer receives only frozen semantic Chunk windows and the exact
    Source text needed for aggregate publication scanning.  The public factory
    performs the expensive deep materialization validation once per workflow
    transition, rather than once per evidence span.
    """

    private_materialization_sha256: str
    chunks: tuple[_FrozenEvidenceChunk, ...] = field(repr=False)
    chunk_integrity_tags: tuple[bytes, ...] = field(repr=False, compare=False)
    source_integrity_tag: bytes = field(repr=False, compare=False)
    private_source_texts: tuple[str, ...] = field(repr=False, compare=False)
    _validation_token: object = field(repr=False, compare=False)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError(
            "AnnotationEvidenceSourceAuthority must come from its binder"
        )

    def __post_init__(self) -> None:
        try:
            _validate_source_authority_shape(self)
            _validate_all_source_chunks(self)
        except AnnotationEvidenceError:
            raise ValueError("Invalid frozen annotation Source snapshot") from None


class ExactQuoteEvidenceSelection(Protocol):
    """Structural private input shared by Concept and Relation authoring."""

    chunk_ordinal: int
    logical_page_id: str
    semantic_chunk_sha256: str
    page_global_utf8_start: int | None
    exact_quote: str


def bind_annotation_evidence_source(
    source_materialization: PrivateSourceSliceMaterializationReceipt,
) -> AnnotationEvidenceSourceAuthority:
    """Deeply validate and freeze one private materialization for annotation.

    The returned capability is the only Source input accepted by evidence and
    privacy primitives.  This prevents later stages from substituting a
    duck-typed materialization or an unrelated collection of Source strings.
    """

    message = "A validated private Source authority is required for annotation"
    try:
        if type(source_materialization) is not PrivateSourceSliceMaterializationReceipt:
            raise AnnotationEvidenceError(message)
        source_materialization.__post_init__()
        if (
            type(source_materialization.materialization)
            is not PrivateSourceSliceMaterialization
        ):
            raise AnnotationEvidenceError(message)
        payload = canonical_json_bytes(source_materialization.materialization)
        if len(payload) > MAX_ANNOTATION_SOURCE_MATERIALIZATION_BYTES:
            raise AnnotationEvidenceError(message)
        decoded = json.loads(payload.decode("utf-8"))
        materialization = PrivateSourceSliceMaterialization.model_validate(decoded)
        if (
            canonical_json_bytes(materialization) != payload
            or hashlib.sha256(payload).hexdigest()
            != source_materialization.artifact_sha256
        ):
            raise AnnotationEvidenceError(message)
        chunk_index = _unique_ordinal_index(
            tuple(materialization.course_source_chunks)
        )
        manifest_index = _unique_ordinal_index(
            tuple(materialization.chunk_manifest.chunks)
        )
        if set(chunk_index) != set(manifest_index) or tuple(sorted(chunk_index)) != (
            tuple(range(len(chunk_index)))
        ):
            raise AnnotationEvidenceError(message)
        frozen: list[_FrozenEvidenceChunk] = []
        for ordinal in sorted(chunk_index):
            chunk = chunk_index[ordinal]
            (
                chunk_bytes,
                logical_page_id,
                window_start,
                window_end,
                semantic_chunk_sha256,
            ) = _validated_chunk_identity(chunk, manifest_index[ordinal])
            frozen.append(
                _FrozenEvidenceChunk(
                    ordinal=ordinal,
                    logical_page_id=logical_page_id,
                    window_start=window_start,
                    window_end=window_end,
                    semantic_chunk_sha256=semantic_chunk_sha256,
                    text=chunk.text,
                    utf8_bytes=chunk_bytes,
                )
            )
        return _issue_annotation_evidence_source_authority(
            private_materialization_sha256=(
                source_materialization.artifact_sha256
            ),
            chunks=tuple(frozen),
        )
    except AnnotationEvidenceError:
        raise AnnotationEvidenceError(message) from None
    except (
        AttributeError,
        CanonicalArtifactError,
        json.JSONDecodeError,
        RecursionError,
        RuntimeError,
        TypeError,
        UnicodeError,
        ValidationError,
        ValueError,
    ):
        raise AnnotationEvidenceError(message) from None


def resolve_evidence_selection(
    selection: ExactQuoteEvidenceSelection,
    *,
    source_authority: AnnotationEvidenceSourceAuthority,
) -> EvidenceSpan:
    """Resolve one exact private quote into redacted frozen-Source evidence.

    Repeated quotes require ``page_global_utf8_start``.  Neither the returned
    object nor any raised ``AnnotationEvidenceError`` contains the quote.
    """

    source_authority = _require_source_authority(source_authority)
    (
        chunk_ordinal,
        logical_page_id,
        semantic_chunk_sha256,
        explicit_page_start,
        exact_quote,
    ) = _selection_fields(selection)
    chunk = _lookup_frozen_chunk(
        source_authority,
        chunk_ordinal=chunk_ordinal,
    )
    chunk_bytes = chunk.utf8_bytes
    frozen_page_id = chunk.logical_page_id
    window_start = chunk.window_start
    window_end = chunk.window_end
    frozen_chunk_sha256 = chunk.semantic_chunk_sha256
    if (
        frozen_page_id != logical_page_id
        or frozen_chunk_sha256 != semantic_chunk_sha256
    ):
        raise AnnotationEvidenceError(
            "Evidence differs from the frozen semantic Chunk"
        )

    quote_bytes = _encode_private_text(exact_quote)
    if explicit_page_start is None:
        matches = _all_byte_matches(chunk_bytes, quote_bytes)
        if len(matches) != 1:
            raise AnnotationEvidenceError(
                "Evidence quote requires one explicit unambiguous UTF-8 byte start"
            )
        local_start = matches[0]
        page_start = window_start + local_start
    else:
        page_start = explicit_page_start
        local_start = page_start - window_start

    local_end = local_start + len(quote_bytes)
    page_end = page_start + len(quote_bytes)
    if (
        local_start < 0
        or local_end > len(chunk_bytes)
        or page_end > window_end
        or chunk_bytes[local_start:local_end] != quote_bytes
    ):
        raise AnnotationEvidenceError(
            "Evidence span does not resolve against frozen Source bytes"
        )

    try:
        return EvidenceSpan(
            chunk_ordinal=chunk_ordinal,
            logical_page_id=logical_page_id,
            semantic_chunk_sha256=semantic_chunk_sha256,
            page_utf8_start=page_start,
            page_utf8_end=page_end,
            offset_unit="utf8_bytes",
            semantic_span_sha256=hashlib.sha256(quote_bytes).hexdigest(),
        )
    except (TypeError, ValueError):
        raise AnnotationEvidenceError(
            "Resolved evidence cannot form a canonical public span"
        ) from None


def validate_public_evidence_span(
    evidence: EvidenceSpan,
    *,
    source_authority: AnnotationEvidenceSourceAuthority,
) -> None:
    """Replay one redacted public span against the frozen private Source."""

    source_authority = _require_source_authority(source_authority)
    (
        chunk_ordinal,
        logical_page_id,
        semantic_chunk_sha256,
        page_start,
        page_end,
        offset_unit,
        semantic_span_sha256,
    ) = _public_span_fields(evidence)
    chunk = _lookup_frozen_chunk(
        source_authority,
        chunk_ordinal=chunk_ordinal,
        public=True,
    )
    chunk_bytes = chunk.utf8_bytes
    frozen_page_id = chunk.logical_page_id
    window_start = chunk.window_start
    window_end = chunk.window_end
    frozen_chunk_sha256 = chunk.semantic_chunk_sha256
    if (
        frozen_page_id != logical_page_id
        or frozen_chunk_sha256 != semantic_chunk_sha256
        or offset_unit != "utf8_bytes"
    ):
        raise AnnotationEvidenceError(
            "Published evidence differs from frozen Chunk identity"
        )

    local_start = page_start - window_start
    local_end = page_end - window_start
    if (
        local_start < 0
        or local_end > len(chunk_bytes)
        or local_end <= local_start
        or page_end > window_end
    ):
        raise AnnotationEvidenceError(
            "Published evidence is outside its frozen Chunk"
        )
    span = chunk_bytes[local_start:local_end]
    try:
        decoded = span.decode("utf-8", errors="strict")
    except UnicodeError:
        raise AnnotationEvidenceError(
            "Published evidence splits a UTF-8 code point"
        ) from None
    if (
        not decoded.strip()
        or hashlib.sha256(span).hexdigest() != semantic_span_sha256
    ):
        raise AnnotationEvidenceError(
            "Published evidence span hash is invalid"
        )


def reject_public_source_copy(
    public_values: Sequence[str],
    *,
    source_authority: AnnotationEvidenceSourceAuthority,
) -> None:
    """Reject public prose that leaks Source text or private location data.

    Values are scanned both individually and as aggregate surfaces so splitting
    a path or Source excerpt across fields does not bypass the boundary.
    Ordinary percentages such as ``100%`` remain valid.  Reversible escapes in
    one field are rejected directly; escapes assembled only across field
    boundaries are decoded and rejected when they reconstruct Source or a
    private location.  A harmless ``("100%", "20 samples")`` boundary remains
    valid.
    """

    source_authority = _require_source_authority(source_authority)
    _validate_all_source_chunks(source_authority)
    public_snapshot = _snapshot_texts(
        public_values,
        label="Public annotation text",
        allow_empty=True,
    )
    source_snapshot = source_authority.private_source_texts
    try:
        if any(_contains_unsafe_unicode(value) for value in public_snapshot):
            raise AnnotationEvidenceError(
                "Public annotation text contains an invisible Unicode control"
            )

        normalized_public = tuple(
            unicodedata.normalize("NFKC", value) for value in public_snapshot
        )
        if any(_PERCENT_ESCAPE.search(value) for value in normalized_public):
            raise AnnotationEvidenceError(
                "Public annotation text contains a percent escape that may hide "
                "Source text or a private path"
            )

        cleaned_values = tuple(
            _remove_default_ignorables(value) for value in normalized_public
        )
        joined_surfaces = _unique_text_surfaces(
            "\n".join(cleaned_values),
            "".join(cleaned_values),
        )
        if any(_LOCAL_PATH_OR_EMAIL.search(surface) for surface in joined_surfaces):
            raise AnnotationEvidenceError(
                "Public annotation text contains a private path or email-like value"
            )

        normalized_sources = tuple(
            _normalize_copy_scan(text) for text in source_snapshot
        )
        copy_surfaces = _unique_text_surfaces(
            _normalize_copy_scan(" ".join(cleaned_values)),
            _normalize_copy_scan("".join(cleaned_values)),
        )
        _reject_source_copy_surfaces(copy_surfaces, normalized_sources)

        compact_surface = "".join(normalized_public)
        if _PERCENT_ESCAPE.search(compact_surface):
            _reject_dangerous_cross_field_percent_encoding(
                compact_surface,
                normalized_sources=normalized_sources,
            )
    except AnnotationEvidenceError:
        raise
    except (RuntimeError, TypeError, ValueError, UnicodeError):
        raise AnnotationEvidenceError(
            "Public annotation privacy validation failed"
        ) from None


def evidence_span_sort_key(evidence: EvidenceSpan) -> tuple[object, ...]:
    """Return the canonical ordering key shared by annotation stages."""

    return (
        evidence.chunk_ordinal,
        evidence.logical_page_id,
        evidence.page_utf8_start,
        evidence.page_utf8_end,
        evidence.semantic_chunk_sha256,
        evidence.semantic_span_sha256,
    )


def _selection_fields(
    selection: ExactQuoteEvidenceSelection,
) -> tuple[int, str, str, int | None, str]:
    try:
        chunk_ordinal = selection.chunk_ordinal
        logical_page_id = selection.logical_page_id
        semantic_chunk_sha256 = selection.semantic_chunk_sha256
        page_start = selection.page_global_utf8_start
        exact_quote = selection.exact_quote
    except Exception:
        raise AnnotationEvidenceError(
            "Private evidence selection has an invalid shape"
        ) from None
    if (
        type(chunk_ordinal) is not int
        or not 0 <= chunk_ordinal <= 999
        or type(logical_page_id) is not str
        or _LOGICAL_PAGE_ID.fullmatch(logical_page_id) is None
        or type(semantic_chunk_sha256) is not str
        or _LOWER_SHA256.fullmatch(semantic_chunk_sha256) is None
        or (
            page_start is not None
            and (type(page_start) is not int or page_start < 0)
        )
        or type(exact_quote) is not str
        or not 1 <= len(exact_quote) <= 16_000
        or not exact_quote.strip()
    ):
        raise AnnotationEvidenceError(
            "Private evidence selection has an invalid shape"
        )
    return (
        chunk_ordinal,
        logical_page_id,
        semantic_chunk_sha256,
        page_start,
        exact_quote,
    )


def _require_source_authority(
    authority: AnnotationEvidenceSourceAuthority,
) -> AnnotationEvidenceSourceAuthority:
    try:
        _validate_source_authority_shape(authority)
        return authority
    except AnnotationEvidenceError:
        raise AnnotationEvidenceError(
            "A validated private Source authority is required for annotation"
        ) from None


def _validate_source_authority_shape(authority: object) -> None:
    message = "Frozen annotation Source authority is invalid"
    try:
        if (
            type(authority) is not AnnotationEvidenceSourceAuthority
            or authority._validation_token
            is not _ANNOTATION_EVIDENCE_SOURCE_TOKEN
            or type(authority.private_materialization_sha256) is not str
            or _LOWER_SHA256.fullmatch(
                authority.private_materialization_sha256
            )
            is None
            or type(authority.chunks) is not tuple
            or not authority.chunks
            or type(authority.chunk_integrity_tags) is not tuple
            or len(authority.chunk_integrity_tags) != len(authority.chunks)
            or type(authority.source_integrity_tag) is not bytes
            or len(authority.source_integrity_tag)
            != hashlib.sha256().digest_size
            or type(authority.private_source_texts) is not tuple
            or not authority.private_source_texts
            or any(
                type(value) is not str or not value
                for value in authority.private_source_texts
            )
            or tuple(chunk.ordinal for chunk in authority.chunks)
            != tuple(range(len(authority.chunks)))
            or any(
                type(tag) is not bytes or len(tag) != hashlib.sha256().digest_size
                for tag in authority.chunk_integrity_tags
            )
        ):
            raise AnnotationEvidenceError(message)
        expected_root = _source_integrity_tag(
            authority.private_materialization_sha256,
            authority.chunk_integrity_tags,
        )
        if not hmac.compare_digest(
            expected_root,
            authority.source_integrity_tag,
        ):
            raise AnnotationEvidenceError(message)
    except AnnotationEvidenceError:
        raise
    except (AttributeError, RuntimeError, TypeError, ValueError):
        raise AnnotationEvidenceError(message) from None


def _chunk_integrity_tag(
    private_materialization_sha256: str,
    chunk: _FrozenEvidenceChunk,
) -> bytes:
    message = "Frozen annotation Chunk integrity is invalid"
    try:
        if (
            type(private_materialization_sha256) is not str
            or _LOWER_SHA256.fullmatch(private_materialization_sha256) is None
            or type(chunk) is not _FrozenEvidenceChunk
            or type(chunk.ordinal) is not int
            or not 0 <= chunk.ordinal <= 999
            or type(chunk.logical_page_id) is not str
            or _LOGICAL_PAGE_ID.fullmatch(chunk.logical_page_id) is None
            or type(chunk.window_start) is not int
            or type(chunk.window_end) is not int
            or chunk.window_start < 0
            or chunk.window_end <= chunk.window_start
            or type(chunk.semantic_chunk_sha256) is not str
            or _LOWER_SHA256.fullmatch(chunk.semantic_chunk_sha256) is None
            or type(chunk.text) is not str
            or type(chunk.utf8_bytes) is not bytes
        ):
            raise AnnotationEvidenceError(message)
        encoded = chunk.text.encode("utf-8", errors="strict")
        if (
            encoded != chunk.utf8_bytes
            or chunk.window_end - chunk.window_start != len(encoded)
            or hashlib.sha256(encoded).hexdigest()
            != chunk.semantic_chunk_sha256
        ):
            raise AnnotationEvidenceError(message)
        digest = hmac.new(
            _ANNOTATION_EVIDENCE_INTEGRITY_KEY,
            digestmod=hashlib.sha256,
        )
        for value in (
            _CHUNK_INTEGRITY_DOMAIN,
            private_materialization_sha256.encode("ascii"),
            chunk.ordinal.to_bytes(4, "big"),
            chunk.window_start.to_bytes(8, "big"),
            chunk.window_end.to_bytes(8, "big"),
            len(chunk.logical_page_id).to_bytes(2, "big"),
            chunk.logical_page_id.encode("ascii"),
            chunk.semantic_chunk_sha256.encode("ascii"),
            encoded,
        ):
            digest.update(value)
        return digest.digest()
    except AnnotationEvidenceError:
        raise
    except (AttributeError, OverflowError, RuntimeError, TypeError, UnicodeError):
        raise AnnotationEvidenceError(message) from None


def _source_integrity_tag(
    private_materialization_sha256: str,
    chunk_integrity_tags: tuple[bytes, ...],
) -> bytes:
    message = "Frozen annotation Source root integrity is invalid"
    try:
        if (
            type(private_materialization_sha256) is not str
            or _LOWER_SHA256.fullmatch(private_materialization_sha256) is None
            or type(chunk_integrity_tags) is not tuple
            or not chunk_integrity_tags
            or any(
                type(tag) is not bytes or len(tag) != hashlib.sha256().digest_size
                for tag in chunk_integrity_tags
            )
        ):
            raise AnnotationEvidenceError(message)
        digest = hmac.new(
            _ANNOTATION_EVIDENCE_INTEGRITY_KEY,
            digestmod=hashlib.sha256,
        )
        digest.update(_SOURCE_INTEGRITY_DOMAIN)
        digest.update(private_materialization_sha256.encode("ascii"))
        digest.update(len(chunk_integrity_tags).to_bytes(4, "big"))
        for tag in chunk_integrity_tags:
            digest.update(tag)
        return digest.digest()
    except AnnotationEvidenceError:
        raise
    except (OverflowError, RuntimeError, TypeError, UnicodeError, ValueError):
        raise AnnotationEvidenceError(message) from None


def _validate_source_chunk(
    authority: AnnotationEvidenceSourceAuthority,
    chunk_ordinal: int,
) -> _FrozenEvidenceChunk:
    message = "Frozen annotation Chunk integrity is invalid"
    try:
        chunk = authority.chunks[chunk_ordinal]
        expected_tag = authority.chunk_integrity_tags[chunk_ordinal]
        actual_tag = _chunk_integrity_tag(
            authority.private_materialization_sha256,
            chunk,
        )
        if not hmac.compare_digest(actual_tag, expected_tag):
            raise AnnotationEvidenceError(message)
        return chunk
    except AnnotationEvidenceError:
        raise
    except (IndexError, RuntimeError, TypeError, ValueError):
        raise AnnotationEvidenceError(message) from None


def _validate_all_source_chunks(
    authority: AnnotationEvidenceSourceAuthority,
) -> None:
    for ordinal in range(len(authority.chunks)):
        _validate_source_chunk(authority, ordinal)
    if _reconstruct_private_source_texts(authority.chunks) != (
        authority.private_source_texts
    ):
        raise AnnotationEvidenceError(
            "Frozen annotation Source surfaces are invalid"
        )


def _reconstruct_private_source_texts(
    chunks: tuple[_FrozenEvidenceChunk, ...],
) -> tuple[str, ...]:
    """Rebuild contiguous page segments without duplicating Chunk overlap."""

    by_page: dict[str, list[_FrozenEvidenceChunk]] = {}
    for chunk in chunks:
        by_page.setdefault(chunk.logical_page_id, []).append(chunk)
    surfaces: list[str] = []
    try:
        for logical_page_id in sorted(by_page):
            ordered = sorted(
                by_page[logical_page_id],
                key=lambda value: (value.window_start, value.window_end),
            )
            segment_start = ordered[0].window_start
            segment_end = ordered[0].window_end
            segment = bytearray(ordered[0].utf8_bytes)
            for chunk in ordered[1:]:
                if chunk.window_start > segment_end:
                    surfaces.append(segment.decode("utf-8", errors="strict"))
                    segment_start = chunk.window_start
                    segment_end = chunk.window_end
                    segment = bytearray(chunk.utf8_bytes)
                    continue
                local_start = chunk.window_start - segment_start
                shared_end = min(chunk.window_end, segment_end)
                shared_bytes = shared_end - chunk.window_start
                if (
                    local_start < 0
                    or bytes(
                        segment[local_start : local_start + shared_bytes]
                    )
                    != chunk.utf8_bytes[:shared_bytes]
                ):
                    raise AnnotationEvidenceError(
                        "Frozen annotation Chunk overlap is inconsistent"
                    )
                if chunk.window_end > segment_end:
                    segment.extend(chunk.utf8_bytes[shared_bytes:])
                    segment_end = chunk.window_end
            surfaces.append(segment.decode("utf-8", errors="strict"))
    except AnnotationEvidenceError:
        raise
    except (
        AttributeError,
        IndexError,
        OverflowError,
        RuntimeError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        raise AnnotationEvidenceError(
            "Frozen annotation Source surfaces are invalid"
        ) from None
    if not surfaces or any(not value for value in surfaces):
        raise AnnotationEvidenceError(
            "Frozen annotation Source surfaces are invalid"
        )
    return tuple(surfaces)


def _issue_annotation_evidence_source_authority(
    *,
    private_materialization_sha256: str,
    chunks: tuple[_FrozenEvidenceChunk, ...],
) -> AnnotationEvidenceSourceAuthority:
    tags = tuple(
        _chunk_integrity_tag(
            private_materialization_sha256,
            chunk,
        )
        for chunk in chunks
    )
    authority = object.__new__(AnnotationEvidenceSourceAuthority)
    for name, value in {
        "private_materialization_sha256": private_materialization_sha256,
        "chunks": chunks,
        "chunk_integrity_tags": tags,
        "source_integrity_tag": _source_integrity_tag(
            private_materialization_sha256,
            tags,
        ),
        "private_source_texts": _reconstruct_private_source_texts(chunks),
        "_validation_token": _ANNOTATION_EVIDENCE_SOURCE_TOKEN,
    }.items():
        object.__setattr__(authority, name, value)
    authority.__post_init__()
    return authority


def _public_span_fields(
    evidence: EvidenceSpan,
) -> tuple[int, str, str, int, int, str, str]:
    try:
        values = (
            evidence.chunk_ordinal,
            evidence.logical_page_id,
            evidence.semantic_chunk_sha256,
            evidence.page_utf8_start,
            evidence.page_utf8_end,
            evidence.offset_unit,
            evidence.semantic_span_sha256,
        )
    except Exception:
        raise AnnotationEvidenceError(
            "Published evidence has an invalid shape"
        ) from None
    if (
        type(values[0]) is not int
        or type(values[1]) is not str
        or type(values[2]) is not str
        or type(values[3]) is not int
        or type(values[4]) is not int
        or type(values[5]) is not str
        or type(values[6]) is not str
    ):
        raise AnnotationEvidenceError(
            "Published evidence has an invalid shape"
        )
    return values


def _lookup_frozen_chunk(
    source_authority: AnnotationEvidenceSourceAuthority,
    *,
    chunk_ordinal: int,
    public: bool = False,
) -> _FrozenEvidenceChunk:
    message = (
        "Published evidence references an unknown frozen Chunk"
        if public
        else "Evidence references an unknown frozen Chunk ordinal"
    )
    if not 0 <= chunk_ordinal < len(source_authority.chunks):
        raise AnnotationEvidenceError(message)
    chunk = _validate_source_chunk(source_authority, chunk_ordinal)
    if chunk.ordinal != chunk_ordinal:
        raise AnnotationEvidenceError(message)
    return chunk


def _unique_ordinal_index(values: tuple[object, ...]) -> dict[int, object]:
    if not values:
        raise AnnotationEvidenceError(
            "Frozen Source materialization has an invalid evidence index"
        )
    result: dict[int, object] = {}
    for value in values:
        try:
            ordinal = value.ordinal
        except (AttributeError, RuntimeError, TypeError, ValueError):
            raise AnnotationEvidenceError(
                "Frozen Source materialization has an invalid evidence index"
            ) from None
        if type(ordinal) is not int or ordinal < 0 or ordinal in result:
            raise AnnotationEvidenceError(
                "Frozen Source materialization has an invalid evidence index"
            )
        result[ordinal] = value
    return result


def _validated_chunk_identity(
    chunk: object,
    manifest_chunk: object,
    *,
    public: bool = False,
) -> tuple[bytes, str, int, int, str]:
    identity_message = (
        "Published evidence differs from frozen Chunk identity"
        if public
        else "Evidence differs from the frozen semantic Chunk"
    )
    try:
        text = chunk.text
        text_hash = chunk.text_hash
        metadata = chunk.locator.metadata
        manifest_hash = manifest_chunk.semantic_chunk_sha256
    except (AttributeError, RuntimeError, TypeError, ValueError):
        raise AnnotationEvidenceError(identity_message) from None
    if (
        type(text) is not str
        or type(text_hash) is not str
        or type(manifest_hash) is not str
        or not isinstance(metadata, Mapping)
    ):
        raise AnnotationEvidenceError(identity_message)
    try:
        logical_page_id = metadata.get("logical_page_id")
        window_start = metadata.get("start_offset")
        window_end = metadata.get("end_offset")
    except (AttributeError, RuntimeError, TypeError, ValueError):
        raise AnnotationEvidenceError(identity_message) from None
    if (
        type(logical_page_id) is not str
        or _LOGICAL_PAGE_ID.fullmatch(logical_page_id) is None
        or type(window_start) is not int
        or type(window_end) is not int
        or window_start < 0
        or window_end <= window_start
        or _LOWER_SHA256.fullmatch(text_hash) is None
        or manifest_hash != text_hash
    ):
        raise AnnotationEvidenceError(identity_message)
    chunk_bytes = _encode_private_text(text)
    if (
        window_end - window_start != len(chunk_bytes)
        or hashlib.sha256(chunk_bytes).hexdigest() != text_hash
    ):
        raise AnnotationEvidenceError(
            "Frozen Chunk byte window is internally inconsistent"
        )
    return (
        chunk_bytes,
        logical_page_id,
        window_start,
        window_end,
        text_hash,
    )


def _encode_private_text(value: str) -> bytes:
    try:
        return value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise AnnotationEvidenceError(
            "Private Source evidence is not valid UTF-8 text"
        ) from None


def _snapshot_texts(
    values: Sequence[str],
    *,
    label: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)):
        raise AnnotationEvidenceError(f"{label} collection is invalid")
    try:
        snapshot = tuple(values)
    except Exception:
        raise AnnotationEvidenceError(f"{label} collection is invalid") from None
    if (not allow_empty and not snapshot) or any(
        type(value) is not str for value in snapshot
    ):
        raise AnnotationEvidenceError(f"{label} collection is invalid")
    if not allow_empty and any(not value for value in snapshot):
        raise AnnotationEvidenceError(f"{label} collection is invalid")
    return snapshot


def _unique_text_surfaces(*values: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _reject_source_copy_surfaces(
    surfaces: tuple[str, ...],
    normalized_sources: tuple[str, ...],
) -> None:
    for surface in surfaces:
        _reject_character_source_copy(surface, normalized_sources)
        _reject_token_source_copy(surface, normalized_sources)


def _reject_dangerous_cross_field_percent_encoding(
    compact_surface: str,
    *,
    normalized_sources: tuple[str, ...],
) -> None:
    """Fail closed on escapes reconstructed only after joining public fields."""

    message = (
        "Public annotation text contains a percent escape that may hide "
        "Source text or a private path"
    )
    current = compact_surface
    try:
        for _depth in range(8):
            matches = tuple(_PERCENT_ESCAPE.finditer(current))
            if not matches:
                return
            # A cross-boundary ``%20`` can arise naturally from adjacent prose;
            # all other reversible byte escapes have no annotation use case.
            if any(match.group(0).casefold() != "%20" for match in matches):
                raise AnnotationEvidenceError(message)
            decoded = unquote_to_bytes(current).decode("utf-8", errors="strict")
            if decoded == current:
                raise AnnotationEvidenceError(message)
            if _contains_unsafe_unicode(decoded):
                raise AnnotationEvidenceError(message)
            cleaned = _remove_default_ignorables(decoded)
            if _LOCAL_PATH_OR_EMAIL.search(cleaned):
                raise AnnotationEvidenceError(message)
            try:
                _reject_source_copy_surfaces(
                    (_normalize_copy_scan(cleaned),),
                    normalized_sources,
                )
            except AnnotationEvidenceError:
                raise AnnotationEvidenceError(message) from None
            current = decoded
        if _PERCENT_ESCAPE.search(current):
            raise AnnotationEvidenceError(message)
    except AnnotationEvidenceError:
        raise
    except (UnicodeError, ValueError):
        raise AnnotationEvidenceError(message) from None


def _reject_character_source_copy(
    normalized_public_text: str,
    normalized_sources: tuple[str, ...],
) -> None:
    if len(normalized_public_text) < PUBLIC_SOURCE_COPY_WINDOW:
        return
    for offset in range(
        len(normalized_public_text) - PUBLIC_SOURCE_COPY_WINDOW + 1
    ):
        window = normalized_public_text[
            offset : offset + PUBLIC_SOURCE_COPY_WINDOW
        ]
        if any(window in source for source in normalized_sources):
            raise AnnotationEvidenceError(
                "Public annotation text contains a long verbatim Source fragment"
            )


def _reject_token_source_copy(
    normalized_public_text: str,
    normalized_sources: tuple[str, ...],
) -> None:
    public_tokens = _copy_scan_tokens(normalized_public_text)
    if len(public_tokens) < PUBLIC_SOURCE_TOKEN_WINDOW:
        return
    source_token_streams = tuple(
        _copy_scan_tokens(source) for source in normalized_sources
    )
    for offset in range(len(public_tokens) - PUBLIC_SOURCE_TOKEN_WINDOW + 1):
        token_window = public_tokens[
            offset : offset + PUBLIC_SOURCE_TOKEN_WINDOW
        ]
        if any(
            _contains_token_window(source_tokens, token_window)
            for source_tokens in source_token_streams
        ):
            raise AnnotationEvidenceError(
                "Public annotation text contains a verbatim Source token sequence"
            )


def _normalize_copy_scan(value: str) -> str:
    return " ".join(_remove_default_ignorables(value).casefold().split())


def _remove_default_ignorables(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return "".join(
        character
        for character in normalized
        if not _is_default_ignorable(character)
    )


def _contains_unsafe_unicode(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value)
    return any(
        _is_default_ignorable(character)
        or unicodedata.category(character) in {"Cc", "Cs"}
        for character in normalized
    )


def _is_default_ignorable(character: str) -> bool:
    codepoint = ord(character)
    return (
        unicodedata.category(character) == "Cf"
        or codepoint == 0x034F
        or 0x115F <= codepoint <= 0x1160
        or 0x17B4 <= codepoint <= 0x17B5
        or 0x180B <= codepoint <= 0x180F
        or 0xFE00 <= codepoint <= 0xFE0F
        or codepoint == 0x3164
        or codepoint == 0xFFA0
        or 0xFFF0 <= codepoint <= 0xFFF8
        or 0x1BCA0 <= codepoint <= 0x1BCA3
        or 0x1D173 <= codepoint <= 0x1D17A
        or 0xE0000 <= codepoint <= 0xE0FFF
    )


def _copy_scan_tokens(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[^\W_]+", value, flags=re.UNICODE))


def _contains_token_window(
    haystack: tuple[str, ...],
    needle: tuple[str, ...],
) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    return any(
        haystack[offset : offset + len(needle)] == needle
        for offset in range(len(haystack) - len(needle) + 1)
    )


def _all_byte_matches(haystack: bytes, needle: bytes) -> tuple[int, ...]:
    matches: list[int] = []
    cursor = 0
    while True:
        found = haystack.find(needle, cursor)
        if found < 0:
            return tuple(matches)
        matches.append(found)
        cursor = found + 1


__all__ = [
    "AnnotationEvidenceError",
    "AnnotationEvidenceSourceAuthority",
    "ExactQuoteEvidenceSelection",
    "PUBLIC_SOURCE_COPY_WINDOW",
    "PUBLIC_SOURCE_TOKEN_WINDOW",
    "bind_annotation_evidence_source",
    "evidence_span_sort_key",
    "reject_public_source_copy",
    "resolve_evidence_selection",
    "validate_public_evidence_span",
]
