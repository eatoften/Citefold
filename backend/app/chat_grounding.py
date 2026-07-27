from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
)

from .course_source import SourceLocator, SourceSearchResult
from .source_asset import SourceAssetType


INSUFFICIENT_EVIDENCE_MESSAGE = (
    "I don't have enough evidence in the selected sources to answer that."
)

EvidenceId = Annotated[str, Field(pattern=r"^E[1-9][0-9]*$")]
_STRONG_SENTENCE_BOUNDARY = re.compile(
    r'[!?\u3002\uff01\uff1f]+["\'\u201d\u2019)\]]*\s*(?=\S)'
)
_PERIOD_SENTENCE_BOUNDARY = re.compile(
    r'\.["\'\u201d\u2019)\]]*\s+(?=\S)'
)
_COMMON_ABBREVIATIONS = frozenset(
    {
        "dr.",
        "e.g.",
        "eq.",
        "etc.",
        "fig.",
        "i.e.",
        "mr.",
        "mrs.",
        "ms.",
        "no.",
        "prof.",
        "vs.",
    }
)


class GroundedChatOutputError(ValueError):
    """The model output cannot be safely grounded in retrieved evidence."""


class GroundingEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: EvidenceId
    chunk_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_title: str = Field(min_length=1)
    source_type: SourceAssetType
    quote: str = Field(min_length=1)
    score: float = Field(ge=-1.0, le=1.0)
    locator: SourceLocator


class _LLMGroundedSentence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    evidence_ids: list[EvidenceId] = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def clean_text(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if not cleaned:
            raise ValueError("Grounded sentence text is required.")
        if _contains_multiple_sentences(cleaned):
            raise ValueError(
                "Each grounded sentence item must contain exactly one sentence."
            )
        return cleaned

    @field_validator("evidence_ids")
    @classmethod
    def reject_duplicate_evidence_ids(
        cls,
        values: list[str],
    ) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError(
                "A grounded sentence cannot repeat an evidence id."
            )
        return values


class _LLMAnsweredPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["answered"]
    sentences: list[_LLMGroundedSentence] = Field(min_length=1)


class _LLMInsufficientEvidencePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["insufficient_evidence"]
    sentences: list[_LLMGroundedSentence] = Field(
        default_factory=list,
        max_length=0,
    )


_LLM_PAYLOAD_ADAPTER = TypeAdapter(
    Annotated[
        _LLMAnsweredPayload | _LLMInsufficientEvidencePayload,
        Field(discriminator="status"),
    ]
)


class GroundedChatSentence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    evidence_ids: list[EvidenceId] = Field(min_length=1)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=1)


class GroundedChatCitationSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: EvidenceId
    sentence_index: int = Field(ge=0)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=1)
    source_id: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    source_title: str = Field(min_length=1)
    source_type: SourceAssetType
    quote: str = Field(min_length=1)
    score: float = Field(ge=-1.0, le=1.0)
    locator: SourceLocator


class GroundedChatAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["answered", "insufficient_evidence"]
    content: str = Field(min_length=1)
    sentences: list[GroundedChatSentence] = Field(default_factory=list)
    citations: list[GroundedChatCitationSnapshot] = Field(
        default_factory=list
    )


def _contains_multiple_sentences(text: str) -> bool:
    if _STRONG_SENTENCE_BOUNDARY.search(text):
        return True
    for match in _PERIOD_SENTENCE_BOUNDARY.finditer(text):
        prefix = text[: match.start() + 1]
        token = prefix.rsplit(maxsplit=1)[-1].lower()
        if token in _COMMON_ABBREVIATIONS:
            continue
        if prefix.endswith("..."):
            continue
        if re.search(r"(?:\b[A-Za-z]\.){2,}$", prefix):
            continue
        return True
    return False


def build_grounding_evidence(
    results: Sequence[SourceSearchResult],
) -> list[GroundingEvidence]:
    """Assign model-visible labels without exposing label creation to the LLM."""

    evidence: list[GroundingEvidence] = []
    seen_chunk_ids: set[str] = set()
    for result in results:
        if result.chunk_id in seen_chunk_ids:
            continue
        seen_chunk_ids.add(result.chunk_id)
        evidence.append(
            GroundingEvidence(
                evidence_id=f"E{len(evidence) + 1}",
                chunk_id=result.chunk_id,
                source_id=result.source_id,
                source_title=result.source_title,
                source_type=result.source_type,
                quote=result.quote,
                score=result.score,
                locator=result.locator,
            )
        )
    return evidence


def insufficient_evidence_answer() -> GroundedChatAnswer:
    """Return a deterministic abstention without invoking a language model."""

    return GroundedChatAnswer(
        status="insufficient_evidence",
        content=INSUFFICIENT_EVIDENCE_MESSAGE,
    )


def parse_grounded_chat_output(
    raw_output: str,
    evidence: Sequence[GroundingEvidence],
) -> GroundedChatAnswer:
    """Parse strict model JSON and replace labels with server-owned evidence."""

    try:
        raw_payload = json.loads(raw_output)
        payload = _LLM_PAYLOAD_ADAPTER.validate_python(raw_payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise GroundedChatOutputError(
            "Local LLM output was not valid grounded-chat JSON."
        ) from exc

    if isinstance(payload, _LLMInsufficientEvidencePayload):
        return insufficient_evidence_answer()

    evidence_by_id = {item.evidence_id: item for item in evidence}
    if len(evidence_by_id) != len(evidence):
        raise GroundedChatOutputError(
            "Grounding evidence ids must be unique."
        )
    if not evidence_by_id:
        raise GroundedChatOutputError(
            "An answered response requires retrieved evidence."
        )

    unknown_ids = [
        evidence_id
        for sentence in payload.sentences
        for evidence_id in sentence.evidence_ids
        if evidence_id not in evidence_by_id
    ]
    if unknown_ids:
        raise GroundedChatOutputError(
            "Local LLM cited evidence outside the supplied context."
        )

    content_parts: list[str] = []
    sentences: list[GroundedChatSentence] = []
    citations: list[GroundedChatCitationSnapshot] = []
    next_offset = 0
    for sentence_index, sentence in enumerate(payload.sentences):
        if content_parts:
            next_offset += 1
        start_offset = next_offset
        end_offset = start_offset + len(sentence.text)
        content_parts.append(sentence.text)
        sentences.append(
            GroundedChatSentence(
                text=sentence.text,
                evidence_ids=sentence.evidence_ids,
                start_offset=start_offset,
                end_offset=end_offset,
            )
        )
        for evidence_id in sentence.evidence_ids:
            item = evidence_by_id[evidence_id]
            citations.append(
                GroundedChatCitationSnapshot(
                    evidence_id=evidence_id,
                    sentence_index=sentence_index,
                    start_offset=start_offset,
                    end_offset=end_offset,
                    source_id=item.source_id,
                    chunk_id=item.chunk_id,
                    source_title=item.source_title,
                    source_type=item.source_type,
                    quote=item.quote,
                    score=item.score,
                    locator=item.locator,
                )
            )
        next_offset = end_offset

    return GroundedChatAnswer(
        status="answered",
        content=" ".join(content_parts),
        sentences=sentences,
        citations=citations,
    )
