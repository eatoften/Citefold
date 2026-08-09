"""Strict private envelopes used while materializing a PDF Source slice.

These models may contain licensed Source text.  They are deliberately kept
separate from the redacted public binding models and must never be written
outside the gitignored ``backend/data`` boundary.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schemas import JsonArrayTuple, SHA256_PATTERN


_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_PARSE_FAILURE_REASONS = frozenset(
    {"parser_error", "resource_limit", "unsupported_content"}
)


class StrictPrivateProjectionModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        hide_input_in_errors=True,
    )


class PrivatePdfPageProjection(StrictPrivateProjectionModel):
    """One normalized page emitted by the isolated PDF worker."""

    logical_page_id: str = Field(pattern=r"^page-[0-9]{4,5}$")
    page_number: int = Field(ge=1, le=10_000)
    semantic_page_sha256: str = Field(pattern=SHA256_PATTERN)
    semantic_utf8_bytes: int = Field(ge=0, le=64 * 1024 * 1024)
    status: Literal["included", "blank", "parse_failed"]
    reason_code: Literal[
        "no_semantic_text",
        "parser_error",
        "resource_limit",
        "unsupported_content",
    ] | None
    text: str | None = Field(repr=False)

    @model_validator(mode="after")
    def validate_page_identity_and_payload(self) -> "PrivatePdfPageProjection":
        width = max(4, len(str(self.page_number)))
        if self.logical_page_id != f"page-{self.page_number:0{width}d}":
            raise ValueError("logical_page_id must be derived from page_number")

        if self.status == "included":
            if self.reason_code is not None or not self.text:
                raise ValueError("Included private pages require non-empty text")
            encoded = self.text.encode("utf-8")
            if len(encoded) != self.semantic_utf8_bytes:
                raise ValueError("Private page byte count does not match text")
            if hashlib.sha256(encoded).hexdigest() != self.semantic_page_sha256:
                raise ValueError("Private page digest does not match text")
            return self

        if self.text is not None or self.semantic_utf8_bytes != 0:
            raise ValueError("Blank or failed private pages cannot carry text")
        if self.semantic_page_sha256 != _EMPTY_SHA256:
            raise ValueError("Blank or failed private pages require the empty hash")
        if self.status == "blank" and self.reason_code != "no_semantic_text":
            raise ValueError("Blank private pages require no_semantic_text")
        if (
            self.status == "parse_failed"
            and self.reason_code not in _PARSE_FAILURE_REASONS
        ):
            raise ValueError("Failed private pages require a parse-failure reason")
        return self


class PrivatePdfProjection(StrictPrivateProjectionModel):
    """Complete private worker result for one exact registered PDF."""

    schema_version: Literal[1]
    artifact_role: Literal["golden_graph_private_pdf_pages"]
    raw_asset_sha256: str = Field(pattern=SHA256_PATTERN)
    normalization: Literal["unicode_nfkc_lf_v1"]
    page_count: int = Field(ge=1, le=10_000)
    total_semantic_utf8_bytes: int = Field(ge=0, le=512 * 1024 * 1024)
    pages: JsonArrayTuple[PrivatePdfPageProjection] = Field(
        min_length=1,
        max_length=10_000,
        repr=False,
    )

    @model_validator(mode="after")
    def validate_complete_page_inventory(self) -> "PrivatePdfProjection":
        if self.page_count != len(self.pages):
            raise ValueError("Private PDF page_count does not match pages")
        page_numbers = [page.page_number for page in self.pages]
        if page_numbers != list(range(1, self.page_count + 1)):
            raise ValueError("Private PDF pages must exactly cover 1..page_count")
        if self.total_semantic_utf8_bytes != sum(
            page.semantic_utf8_bytes for page in self.pages
        ):
            raise ValueError("Private PDF total semantic byte count is inconsistent")
        return self
