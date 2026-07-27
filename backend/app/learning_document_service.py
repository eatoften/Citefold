from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Protocol
from uuid import uuid4

from . import course_service, job_service
from .embedding import (
    EmbeddingError,
    SentenceTransformerEmbedder,
    TextEmbedder,
    cosine_similarity,
)
from .job import utc_now
from .knowledge_card import KnowledgeCard
from .knowledge_card_store import get_card
from .learning_document import (
    LearningDocument,
    LearningDocumentCardLink,
    LearningDocumentCardLinkCreate,
    LearningDocumentCreate,
    LearningDocumentDetail,
    LearningDocumentGenerateRequest,
    LearningDocumentGenerationResult,
    LearningDocumentRestoreRequest,
    LearningDocumentSource,
    LearningDocumentUpdate,
    LearningDocumentVersion,
)
from .learning_document_store import (
    create_document_version,
    create_learning_document,
    delete_document_card_link,
    delete_learning_document,
    get_document_version_by_id,
    get_learning_document_detail,
    list_document_card_links,
    list_document_versions,
    list_learning_documents_for_card,
    list_learning_documents_for_course,
    next_document_version_number,
    purge_learning_document,
    publish_generated_learning_document,
    restore_learning_document,
    update_learning_document,
    upsert_document_card_link,
)
from .llm_client import LLMClientError, LLMMessage, LLMTimeoutError
from .source_asset import SourceAssetDetail, SourceUnit
from .source_asset_store import (
    get_source_asset,
    list_source_units_for_assets,
)


MAX_SELECTED_SOURCE_UNITS = 16
MAX_SOURCE_CONTEXT_CHARACTERS = 24000


class LearningDocumentLLMClient(Protocol):
    settings: object

    def create_chat_completion(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, object] | None = None,
    ) -> str: ...


class LearningDocumentServiceError(Exception):
    pass


class LearningDocumentNotFoundError(LearningDocumentServiceError):
    pass


class LearningDocumentCardNotFoundError(LearningDocumentServiceError):
    pass


class InvalidLearningDocumentError(LearningDocumentServiceError):
    pass


class LearningDocumentGenerationError(LearningDocumentServiceError):
    pass


def create_card_learning_document(
    card_id: str,
    request: LearningDocumentCreate,
) -> LearningDocumentDetail:
    card = _require_card(card_id)
    course_id = _card_course_id(card)
    now = utc_now()
    title = (request.title or f"Understanding {card.title}").strip()
    body = request.body_markdown.strip() or _default_document_body(card)
    document = LearningDocument(
        id=uuid4().hex,
        course_id=course_id,
        title=title,
        summary=request.summary.strip() or card.summary,
        body_markdown=body,
        status="draft",
        generation_mode="manual",
        created_at=now,
        updated_at=now,
    )
    create_learning_document(document)
    upsert_document_card_link(
        LearningDocumentCardLink(
            id=uuid4().hex,
            document_id=document.id,
            card_id=card.id,
            role="primary_anchor",
            position=0,
            created_at=now,
        )
    )
    _save_version(document, change_source="manual")
    return _require_document_detail(document.id)


def list_course_learning_documents(course_id: str) -> list[LearningDocument]:
    course = course_service.get_video_course(course_id)
    return list_learning_documents_for_course(course.id)


def list_card_learning_documents(card_id: str) -> list[LearningDocument]:
    _require_card(card_id)
    return list_learning_documents_for_card(card_id)


def get_saved_learning_document(document_id: str) -> LearningDocumentDetail:
    return _require_document_detail(document_id)


def update_saved_learning_document(
    document_id: str,
    request: LearningDocumentUpdate,
) -> LearningDocumentDetail:
    detail = _require_document_detail(document_id)
    data = request.model_dump(exclude_unset=True)
    if not data:
        raise InvalidLearningDocumentError("At least one document field is required.")
    content_changed = False
    if request.title is not None:
        detail.title = request.title.strip()
        content_changed = True
    if request.summary is not None:
        detail.summary = request.summary.strip()
        content_changed = True
    if request.body_markdown is not None:
        detail.body_markdown = request.body_markdown.strip()
        content_changed = True
    if request.status is not None:
        detail.status = request.status
    detail.updated_at = utc_now()
    update_learning_document(detail)
    if content_changed:
        _save_version(detail, change_source="manual")
    return _require_document_detail(detail.id)


def add_learning_document_card(
    document_id: str,
    request: LearningDocumentCardLinkCreate,
) -> LearningDocumentDetail:
    document = _require_document_detail(document_id)
    card = _require_card(request.card_id)
    if _card_course_id(card) != document.course_id:
        raise InvalidLearningDocumentError(
            "Learning document and card must belong to the same course."
        )
    if request.role == "primary_anchor" and any(
        link.role == "primary_anchor" and link.card_id != card.id
        for link in document.card_links
    ):
        raise InvalidLearningDocumentError(
            "A learning document can have only one primary anchor card."
        )
    upsert_document_card_link(
        LearningDocumentCardLink(
            id=uuid4().hex,
            document_id=document.id,
            card_id=card.id,
            role=request.role,
            position=request.position,
            created_at=utc_now(),
        )
    )
    return _require_document_detail(document.id)


def remove_learning_document_card(document_id: str, card_id: str) -> None:
    detail = _require_document_detail(document_id)
    link = next((item for item in detail.card_links if item.card_id == card_id), None)
    if link is None:
        raise LearningDocumentCardNotFoundError("Document card link not found.")
    if link.role == "primary_anchor":
        raise InvalidLearningDocumentError("The primary anchor cannot be removed.")
    delete_document_card_link(document_id, card_id)


def delete_saved_learning_document(document_id: str) -> None:
    _require_document_detail(document_id)
    delete_learning_document(document_id)


def restore_saved_learning_document(
    document_id: str,
) -> LearningDocumentDetail:
    if not restore_learning_document(document_id):
        raise LearningDocumentNotFoundError(
            "Deleted learning document not found or its course is still in trash."
        )
    return _require_document_detail(document_id)


def purge_saved_learning_document(
    document_id: str,
    *,
    allow_parent_deleted: bool = False,
) -> None:
    if not purge_learning_document(
        document_id,
        allow_parent_deleted=allow_parent_deleted,
    ):
        raise LearningDocumentNotFoundError(
            "Deleted learning document not found."
        )


def restore_learning_document_version(
    document_id: str,
    request: LearningDocumentRestoreRequest,
) -> LearningDocumentDetail:
    document = _require_document_detail(document_id)
    version = next(
        (
            item
            for item in list_document_versions(document.id)
            if item.version_number == request.version_number
        ),
        None,
    )
    if version is None:
        raise InvalidLearningDocumentError("Document version not found.")
    document.title = version.title
    document.summary = version.summary
    document.body_markdown = version.body_markdown
    document.updated_at = utc_now()
    update_learning_document(document)
    _save_version(document, change_source="manual")
    return _require_document_detail(document.id)


def generate_learning_document(
    document_id: str,
    request: LearningDocumentGenerateRequest,
    *,
    llm_client: LearningDocumentLLMClient,
    embedder: TextEmbedder | None = None,
    checkpoint: Callable[[], None] | None = None,
    operation_id: str | None = None,
) -> LearningDocumentGenerationResult:
    document = _require_document_detail(document_id)
    cleaned_operation_id = (
        operation_id.strip() if operation_id is not None else None
    )
    if operation_id is not None and not cleaned_operation_id:
        raise InvalidLearningDocumentError(
            "Generation operation id cannot be empty."
        )
    if cleaned_operation_id is not None:
        existing_version = get_document_version_by_id(cleaned_operation_id)
        if existing_version is not None:
            if existing_version.document_id != document.id:
                raise InvalidLearningDocumentError(
                    "Generation operation belongs to another document."
                )
            return _replayed_generation_result(document.id)
    primary_link = next(
        (link for link in document.card_links if link.role == "primary_anchor"),
        None,
    )
    if primary_link is None:
        raise InvalidLearningDocumentError("Document has no primary anchor card.")
    primary_card = _require_card(primary_link.card_id)
    supporting_cards = _validated_supporting_cards(
        document.course_id,
        request.supporting_card_ids,
        exclude_card_id=primary_card.id,
    )
    supporting_links = [
        LearningDocumentCardLink(
            id=uuid4().hex,
            document_id=document.id,
            card_id=card.id,
            role="supporting",
            position=position,
            created_at=utc_now(),
        )
        for position, card in enumerate(supporting_cards, start=1)
    ]
    assets = _validated_assets(document.course_id, request.source_asset_ids)
    units = list_source_units_for_assets([asset.id for asset in assets])
    selected_units, warning = _select_units(
        primary_card,
        supporting_cards,
        units,
        embedder=embedder,
    )
    sources = _build_document_sources(
        document.id,
        primary_card,
        supporting_cards,
        selected_units,
        {asset.id: asset for asset in assets},
    )
    prompt = _build_generation_prompt(
        document,
        primary_card,
        supporting_cards,
        sources,
        focus=request.focus,
    )
    try:
        output = llm_client.create_chat_completion(
            [
                LLMMessage(
                    role="system",
                    content=(
                        "You create rigorous study documents from supplied local evidence. "
                        "Never invent citations or unsupported course facts."
                    ),
                ),
                LLMMessage(role="user", content=prompt),
            ],
            model=request.model,
            temperature=0.1,
            max_tokens=8192,
        )
    except LLMTimeoutError as exc:
        raise LearningDocumentGenerationError(
            "Local LLM timed out while generating the study document."
        ) from exc
    except LLMClientError as exc:
        raise LearningDocumentGenerationError(str(exc)) from exc

    body = _clean_generated_markdown(output, sources)
    settings = getattr(llm_client, "settings", None)
    provider = getattr(settings, "provider", "local_llm")
    model = request.model or getattr(settings, "model", None)
    _checkpoint(checkpoint)
    document.body_markdown = body
    document.generation_mode = "local_llm"
    document.provider = provider
    document.model = model
    document.status = "draft"
    document.updated_at = utc_now()
    publish_generated_learning_document(
        document,
        supporting_links,
        sources,
        operation_id=cleaned_operation_id,
        provider=provider,
        model=model,
    )
    return LearningDocumentGenerationResult(
        document=_require_document_detail(document.id),
        selected_source_units=len(selected_units),
        selected_cards=1 + len(supporting_cards),
        warning=warning,
    )


def _replayed_generation_result(
    document_id: str,
) -> LearningDocumentGenerationResult:
    document = _require_document_detail(document_id)
    return LearningDocumentGenerationResult(
        document=document,
        selected_source_units=sum(
            source.source_type == "source_unit"
            for source in document.sources
        ),
        selected_cards=len(document.card_links),
        warning=None,
    )


def _checkpoint(checkpoint: Callable[[], None] | None) -> None:
    if checkpoint is not None:
        checkpoint()


def _validated_supporting_cards(
    course_id: str,
    card_ids: list[str],
    *,
    exclude_card_id: str,
) -> list[KnowledgeCard]:
    cards: list[KnowledgeCard] = []
    for card_id in dict.fromkeys(card_ids):
        if card_id == exclude_card_id:
            continue
        card = _require_card(card_id)
        if _card_course_id(card) != course_id:
            raise InvalidLearningDocumentError(
                "Supporting cards must belong to the document course."
            )
        cards.append(card)
    return cards[:12]


def _validated_assets(
    course_id: str,
    asset_ids: list[str],
) -> list[SourceAssetDetail]:
    assets: list[SourceAssetDetail] = []
    for asset_id in dict.fromkeys(asset_ids):
        asset = get_source_asset(asset_id)
        if asset is None:
            raise InvalidLearningDocumentError("Selected source asset not found.")
        if asset.course_id != course_id:
            raise InvalidLearningDocumentError(
                "Source asset must belong to the document course."
            )
        if asset.extraction_status != "ready":
            raise InvalidLearningDocumentError(
                f"Source asset is not ready: {asset.original_filename}."
            )
        assets.append(asset)
    return assets


def _select_units(
    primary_card: KnowledgeCard,
    supporting_cards: Sequence[KnowledgeCard],
    units: list[SourceUnit],
    *,
    embedder: TextEmbedder | None,
) -> tuple[list[SourceUnit], str | None]:
    if len(units) <= MAX_SELECTED_SOURCE_UNITS:
        return _within_character_budget(units), None
    query = "\n".join(
        [primary_card.title, primary_card.summary, *primary_card.key_points]
        + [f"{card.title}: {card.summary}" for card in supporting_cards]
    )
    active_embedder = embedder or SentenceTransformerEmbedder()
    try:
        vectors = active_embedder.embed_texts([query, *[unit.text for unit in units]])
        query_vector = vectors[0]
        ranked = sorted(
            zip(units, vectors[1:], strict=True),
            key=lambda pair: cosine_similarity(query_vector, pair[1]),
            reverse=True,
        )
        return _within_character_budget(
            [unit for unit, _ in ranked[:MAX_SELECTED_SOURCE_UNITS]]
        ), None
    except (EmbeddingError, ValueError) as exc:
        ranked = sorted(
            units,
            key=lambda unit: _lexical_score(query, unit.text),
            reverse=True,
        )
        return _within_character_budget(ranked[:MAX_SELECTED_SOURCE_UNITS]), (
            f"Used lexical source selection because embeddings were unavailable: {exc}"
        )


def _within_character_budget(units: Sequence[SourceUnit]) -> list[SourceUnit]:
    selected: list[SourceUnit] = []
    total = 0
    for unit in units:
        if selected and total + len(unit.text) > MAX_SOURCE_CONTEXT_CHARACTERS:
            continue
        selected.append(unit)
        total += len(unit.text)
    return selected


def _lexical_score(query: str, text: str) -> int:
    query_terms = set(re.findall(r"[\w-]{3,}", query.lower()))
    text_terms = set(re.findall(r"[\w-]{3,}", text.lower()))
    return len(query_terms & text_terms)


def _build_document_sources(
    document_id: str,
    primary_card: KnowledgeCard,
    supporting_cards: Sequence[KnowledgeCard],
    units: Sequence[SourceUnit],
    assets: dict[str, SourceAssetDetail],
) -> list[LearningDocumentSource]:
    now = utc_now()
    sources: list[LearningDocumentSource] = []
    for card in [primary_card, *supporting_cards]:
        for claim in card.claims[:10]:
            evidence = claim.evidence[0]
            sources.append(
                LearningDocumentSource(
                    id=uuid4().hex,
                    document_id=document_id,
                    source_type="card_claim",
                    source_id=claim.id,
                    card_id=card.id,
                    label=f"C{len(sources) + 1}",
                    quote=f"{claim.text}\nEvidence: {evidence.quote}",
                    locator={
                        "start_seconds": evidence.segment_start_seconds,
                        "end_seconds": evidence.segment_end_seconds,
                        "card_title": card.title,
                    },
                    position=len(sources),
                    created_at=now,
                )
            )
    source_unit_index = 1
    for unit in units:
        asset = assets.get(unit.asset_id)
        label = f"S{source_unit_index}"
        source_unit_index += 1
        sources.append(
            LearningDocumentSource(
                id=uuid4().hex,
                document_id=document_id,
                source_type="source_unit",
                source_id=unit.id,
                label=label,
                quote=unit.text[:3000],
                locator={
                    **unit.locator,
                    "asset_id": unit.asset_id,
                    "filename": asset.original_filename if asset else None,
                    "unit_type": unit.unit_type,
                },
                position=len(sources),
                created_at=now,
            )
        )
    return sources


def _build_generation_prompt(
    document: LearningDocumentDetail,
    primary_card: KnowledgeCard,
    supporting_cards: Sequence[KnowledgeCard],
    sources: Sequence[LearningDocumentSource],
    *,
    focus: str | None,
) -> str:
    evidence = "\n\n".join(
        f"[{source.label}] {source.quote}" for source in sources
    )
    supporting = "\n".join(
        f"- {card.title}: {card.summary}" for card in supporting_cards
    ) or "- None selected"
    return f"""
Write a self-contained Markdown study document for the anchor concept below.

Anchor card: {primary_card.title}
Anchor summary: {primary_card.summary}
Requested focus: {(focus or "Deep conceptual understanding").strip()}

Supporting cards:
{supporting}

Use these sections:
# {document.title}
## Overview
## Prerequisites
## Core Explanation
## Step-by-Step Reasoning
## Examples
## Connections to Other Concepts
## Common Misconceptions
## Self-Check Questions
## Source Evidence

Rules:
1. Cite supported statements using the exact labels below, such as [C1] or [S2].
2. Card claims [C*] are course-grounded evidence. File excerpts [S*] are supplementary.
3. Do not invent a label, source, equation, or course fact.
4. When evidence is insufficient, say so explicitly.
5. Return Markdown only.

Available evidence:
{evidence or "No supplementary evidence was selected."}
""".strip()


def _clean_generated_markdown(
    output: str,
    sources: Sequence[LearningDocumentSource],
) -> str:
    body = output.strip()
    if body.startswith("```"):
        body = re.sub(r"^```(?:markdown)?\s*", "", body, flags=re.IGNORECASE)
        body = re.sub(r"\s*```$", "", body)
    valid_labels = {source.label for source in sources}
    body = re.sub(
        r"\[((?:C|S)\d+)\]",
        lambda match: match.group(0) if match.group(1) in valid_labels else "",
        body,
    ).strip()
    if valid_labels and not any(f"[{label}]" in body for label in valid_labels):
        source_lines = "\n".join(
            f"- [{source.label}] {source.quote.splitlines()[0][:240]}"
            for source in sources
        )
        body = f"{body}\n\n## Source Evidence\n\n{source_lines}".strip()
    if not body:
        raise LearningDocumentGenerationError("Local LLM returned an empty document.")
    return body


def _save_version(
    document: LearningDocument,
    *,
    change_source,
    provider: str | None = None,
    model: str | None = None,
) -> None:
    create_document_version(
        LearningDocumentVersion(
            id=uuid4().hex,
            document_id=document.id,
            version_number=next_document_version_number(document.id),
            title=document.title,
            summary=document.summary,
            body_markdown=document.body_markdown,
            change_source=change_source,
            provider=provider,
            model=model,
            created_at=utc_now(),
        )
    )


def _default_document_body(card: KnowledgeCard) -> str:
    return f"""# Understanding {card.title}

## Overview

{card.summary}

## Core Explanation

Start writing your explanation here.

## Examples

## Connections to Other Concepts

## Common Misconceptions

## Self-Check Questions

## Source Evidence
"""


def _require_card(card_id: str) -> KnowledgeCard:
    card = get_card(card_id)
    if card is None:
        raise LearningDocumentCardNotFoundError("Knowledge card not found.")
    return card


def _card_course_id(card: KnowledgeCard) -> str:
    return job_service.get_video_job(card.job_id).course_id


def _require_document_detail(document_id: str) -> LearningDocumentDetail:
    detail = get_learning_document_detail(document_id)
    if detail is None:
        raise LearningDocumentNotFoundError("Learning document not found.")
    return detail
