"""Strict redacted envelopes bound by a golden-graph protocol freeze."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .schemas import JsonArrayTuple, SHA256_PATTERN, SAFE_ID_PATTERN, ToolIdentity


_EMPTY_SEMANTIC_SHA256 = hashlib.sha256(b"").hexdigest()
_PARSE_FAILURE_REASONS = frozenset(
    {"parser_error", "resource_limit", "unsupported_content"}
)

SourceCatalogPageReasonCode = Literal[
    "no_semantic_text",
    "parser_error",
    "resource_limit",
    "unsupported_content",
    "out_of_scope",
]


class StrictBindingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class PdfParserConfigV1(StrictBindingModel):
    """Exact, resource-bounded configuration for the v1 PDF projection."""

    schema_version: Literal[1]
    artifact_role: Literal["golden_graph_pdf_parser_config"]
    extraction_mode: Literal["pypdf_plain_text_v1"]
    normalization: Literal["unicode_nfkc_lf_v1"]
    reader_strict: Literal[False]
    ocr_policy: Literal["disabled"]
    blank_detection: Literal["unicode_whitespace_only_v1"]
    page_failure_policy: Literal["record_and_continue_v1"]
    encrypted_pdf_policy: Literal["reject"]
    timeout_scope: Literal["whole_asset_worker_wall_clock_v1"]
    max_pdf_bytes: int = Field(gt=0, le=512 * 1024 * 1024)
    max_pages: int = Field(gt=0, le=10_000)
    max_page_utf8_bytes: int = Field(gt=0, le=64 * 1024 * 1024)
    max_total_utf8_bytes: int = Field(gt=0, le=512 * 1024 * 1024)
    timeout_seconds: int = Field(gt=0, le=3_600)

    @model_validator(mode="after")
    def total_limit_covers_page_limit(self) -> "PdfParserConfigV1":
        if self.max_page_utf8_bytes > self.max_total_utf8_bytes:
            raise ValueError(
                "max_page_utf8_bytes cannot exceed max_total_utf8_bytes"
            )
        return self


class Utf8ChunkerConfigV1(StrictBindingModel):
    """Exact configuration for deterministic, page-local UTF-8 chunking."""

    schema_version: Literal[1]
    artifact_role: Literal["golden_graph_utf8_chunker_config"]
    algorithm: Literal["utf8_sliding_window_v1"]
    utf8_boundary_policy: Literal["codepoint_safe_max_end_forward_start_v1"]
    max_chunk_utf8_bytes: int = Field(ge=4, le=2 * 1024 * 1024)
    overlap_utf8_bytes: int = Field(ge=0, le=2 * 1024 * 1024)
    max_chunks: int = Field(gt=0, le=1_000)
    cross_page_chunks: Literal[False]
    page_coverage_policy: Literal["complete_union_overlap_allowed-v1"]

    @model_validator(mode="after")
    def overlap_is_smaller_than_chunk(self) -> "Utf8ChunkerConfigV1":
        if self.overlap_utf8_bytes >= self.max_chunk_utf8_bytes:
            raise ValueError(
                "overlap_utf8_bytes must be smaller than max_chunk_utf8_bytes"
            )
        return self


class DependencyPackage(StrictBindingModel):
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=200)

    @field_validator("name", "version")
    @classmethod
    def trimmed(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("Dependency identity must be trimmed")
        return value


class DependencySnapshot(StrictBindingModel):
    schema_version: Literal[1]
    artifact_role: Literal["golden_graph_dependency_snapshot"]
    python_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    unicode_database_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    packages: JsonArrayTuple[DependencyPackage] = Field(
        min_length=1,
        max_length=2_000,
    )

    @model_validator(mode="after")
    def sorted_unique_packages(self) -> "DependencySnapshot":
        names = [item.name.casefold() for item in self.packages]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("Dependency packages must be sorted and unique")
        return self


class SourceCatalogPage(StrictBindingModel):
    logical_page_id: str = Field(pattern=r"^page-[0-9]{4,5}$")
    page_number: int = Field(ge=1, le=10_000)
    semantic_page_sha256: str = Field(pattern=SHA256_PATTERN)
    semantic_utf8_bytes: int = Field(ge=0, le=64 * 1024 * 1024)
    status: Literal["included", "blank", "parse_failed", "excluded"]
    reason_code: SourceCatalogPageReasonCode | None

    @model_validator(mode="after")
    def logical_id_matches_number(self) -> "SourceCatalogPage":
        width = max(4, len(str(self.page_number)))
        if self.logical_page_id != f"page-{self.page_number:0{width}d}":
            raise ValueError("logical_page_id must be derived only from page number")
        if self.status == "included":
            if self.reason_code is not None:
                raise ValueError("Included Source pages cannot have a reason_code")
            if self.semantic_utf8_bytes == 0:
                raise ValueError("Included Source pages must contain semantic bytes")
            if self.semantic_page_sha256 == _EMPTY_SEMANTIC_SHA256:
                raise ValueError("Included Source pages cannot use the empty SHA-256")
            return self

        if self.semantic_utf8_bytes != 0:
            raise ValueError("Non-included Source pages must have zero semantic bytes")
        if self.semantic_page_sha256 != _EMPTY_SEMANTIC_SHA256:
            raise ValueError("Non-included Source pages must use the empty SHA-256")
        if self.status == "blank" and self.reason_code != "no_semantic_text":
            raise ValueError("Blank Source pages require reason_code=no_semantic_text")
        if (
            self.status == "parse_failed"
            and self.reason_code not in _PARSE_FAILURE_REASONS
        ):
            raise ValueError(
                "Parse-failed Source pages require a parse failure reason_code"
            )
        if self.status == "excluded" and self.reason_code != "out_of_scope":
            raise ValueError("Excluded Source pages require reason_code=out_of_scope")
        return self


class SemanticSourceCatalog(StrictBindingModel):
    schema_version: Literal[1]
    artifact_role: Literal["semantic_source_catalog"]
    hash_protocol: Literal["semantic-id-independent-v1"]
    corpus_id: str = Field(pattern=SAFE_ID_PATTERN)
    asset_id: str = Field(pattern=SAFE_ID_PATTERN)
    raw_asset_sha256: str = Field(pattern=SHA256_PATTERN)
    page_count: int = Field(ge=1, le=10_000)
    pages: JsonArrayTuple[SourceCatalogPage] = Field(
        min_length=1,
        max_length=10_000,
    )

    @model_validator(mode="after")
    def exact_page_inventory(self) -> "SemanticSourceCatalog":
        numbers = [page.page_number for page in self.pages]
        if numbers != list(range(1, self.page_count + 1)):
            raise ValueError("Source catalog pages must exactly cover 1..page_count")
        return self


class ChunkLocatorBinding(StrictBindingModel):
    logical_page_id: str = Field(pattern=r"^page-[0-9]{4,5}$")
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    offset_unit: Literal["utf8_bytes"]

    @model_validator(mode="after")
    def ordered_offsets(self) -> "ChunkLocatorBinding":
        if self.end_offset <= self.start_offset:
            raise ValueError("Chunk locator end_offset must exceed start_offset")
        return self


class ChunkBinding(StrictBindingModel):
    ordinal: int = Field(ge=0)
    semantic_chunk_sha256: str = Field(pattern=SHA256_PATTERN)
    locators: JsonArrayTuple[ChunkLocatorBinding] = Field(
        min_length=1,
        max_length=8,
    )

    @model_validator(mode="after")
    def ordered_unique_locators(self) -> "ChunkBinding":
        identities = [
            (item.logical_page_id, item.start_offset, item.end_offset)
            for item in self.locators
        ]
        if identities != sorted(set(identities)):
            raise ValueError("Chunk locators must be sorted and unique")
        return self


class ChunkManifest(StrictBindingModel):
    schema_version: Literal[1]
    artifact_role: Literal["semantic_chunk_manifest"]
    corpus_id: str = Field(pattern=SAFE_ID_PATTERN)
    asset_id: str = Field(pattern=SAFE_ID_PATTERN)
    raw_asset_sha256: str = Field(pattern=SHA256_PATTERN)
    semantic_source_catalog_sha256: str = Field(pattern=SHA256_PATTERN)
    parser: ToolIdentity
    chunker: ToolIdentity
    page_coverage_policy: Literal["complete_union_overlap_allowed-v1"]
    chunks: JsonArrayTuple[ChunkBinding] = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def ordered_unique_chunks(self) -> "ChunkManifest":
        ordinals = [chunk.ordinal for chunk in self.chunks]
        if ordinals != list(range(len(self.chunks))):
            raise ValueError("Chunk ordinals must be contiguous from zero")
        occurrences = [
            (
                locator.logical_page_id,
                locator.start_offset,
                locator.end_offset,
            )
            for chunk in self.chunks
            for locator in chunk.locators
        ]
        if len(occurrences) != len(set(occurrences)):
            raise ValueError(
                "Exact locator occurrences must be unique across different Chunks"
            )
        return self
