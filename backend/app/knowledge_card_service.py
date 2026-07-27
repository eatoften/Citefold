from uuid import uuid4

from .job import utc_now
from .job_service import get_video_job
from .knowledge_card import (
    KnowledgeCard,
    KnowledgeCardClaim,
    KnowledgeCardCreate,
    KnowledgeCardDetail,
    KnowledgeCardEvidence,
    KnowledgeCardUpdate,
)
from .review_item_service import save_initial_review_items
from .review_item_store import list_review_items_for_card
from .knowledge_card_store import (
    create_card,
    delete_card,
    delete_cards_for_job,
    get_card,
    list_cards_for_job,
    purge_card,
    restore_card,
    update_card,
)


class KnowledgeCardServiceError(Exception):
    pass


class KnowledgeCardNotFoundError(KnowledgeCardServiceError):
    pass


class InvalidKnowledgeCardError(KnowledgeCardServiceError):
    pass


def list_job_cards(job_id: str) -> list[KnowledgeCardDetail]:
    get_video_job(job_id)

    return [_to_detail(card) for card in list_cards_for_job(job_id)]


def get_saved_card(card_id: str) -> KnowledgeCardDetail:
    card = get_card(card_id)

    if card is None:
        raise KnowledgeCardNotFoundError("Knowledge card not found.")

    return _to_detail(card)


def save_job_card(
    job_id: str,
    request: KnowledgeCardCreate,
) -> KnowledgeCardDetail:
    card = build_job_card(job_id, request)
    create_card(card)
    save_initial_review_items(card.id, request.review_items)

    return _to_detail(card)


def build_job_card(
    job_id: str,
    request: KnowledgeCardCreate,
    *,
    card_id: str | None = None,
) -> KnowledgeCard:
    """Build a validated card record for a caller-owned transaction."""

    get_video_job(job_id)
    _validate_time_range(
        request.source_start_seconds,
        request.source_end_seconds,
    )

    now = utc_now()
    card = KnowledgeCard(
        id=card_id or uuid4().hex,
        job_id=job_id,
        card_kind=request.card_kind,
        title=request.title.strip(),
        summary=request.summary.strip(),
        key_points=_clean_key_points(request.key_points),
        claims=_clean_claims(request.claims),
        unsupported_terms=_clean_terms(request.unsupported_terms),
        tags=_clean_tags(request.tags),
        content_status=request.content_status,
        source_start_seconds=request.source_start_seconds,
        source_end_seconds=request.source_end_seconds,
        provider=_clean_optional_text(request.provider),
        model=_clean_optional_text(request.model),
        created_at=now,
        updated_at=now,
    )
    return card


def update_saved_card(
    card_id: str,
    request: KnowledgeCardUpdate,
) -> KnowledgeCardDetail:
    card = get_card(card_id)

    if card is None:
        raise KnowledgeCardNotFoundError("Knowledge card not found.")

    update_data = request.model_dump(exclude_unset=True)

    if "card_kind" in update_data and request.card_kind is not None:
        card.card_kind = request.card_kind

    if "title" in update_data and request.title is not None:
        card.title = request.title.strip()

    if "summary" in update_data and request.summary is not None:
        card.summary = request.summary.strip()

    if "key_points" in update_data and request.key_points is not None:
        card.key_points = _clean_key_points(request.key_points)

    if "claims" in update_data and request.claims is not None:
        card.claims = _clean_claims(request.claims)

    if (
        "unsupported_terms" in update_data
        and request.unsupported_terms is not None
    ):
        card.unsupported_terms = _clean_terms(request.unsupported_terms)

    if "tags" in update_data and request.tags is not None:
        card.tags = _clean_tags(request.tags)

    if "content_status" in update_data and request.content_status is not None:
        card.content_status = request.content_status

    if (
        "source_start_seconds" in update_data
        and request.source_start_seconds is not None
    ):
        card.source_start_seconds = request.source_start_seconds

    if (
        "source_end_seconds" in update_data
        and request.source_end_seconds is not None
    ):
        card.source_end_seconds = request.source_end_seconds

    if "provider" in update_data:
        card.provider = _clean_optional_text(request.provider)

    if "model" in update_data:
        card.model = _clean_optional_text(request.model)

    _validate_time_range(
        card.source_start_seconds,
        card.source_end_seconds,
    )

    card.updated_at = utc_now()
    update_card(card)

    return _to_detail(card)


def _to_detail(card: KnowledgeCard) -> KnowledgeCardDetail:
    return KnowledgeCardDetail(
        **card.model_dump(),
        review_items=list_review_items_for_card(card.id),
    )


def delete_saved_card(card_id: str) -> None:
    card = get_card(card_id)

    if card is None:
        raise KnowledgeCardNotFoundError("Knowledge card not found.")

    delete_card(card_id)


def restore_saved_card(card_id: str) -> KnowledgeCardDetail:
    if not restore_card(card_id):
        raise KnowledgeCardNotFoundError(
            "Deleted knowledge card not found or its parent is still in trash."
        )
    return get_saved_card(card_id)


def purge_saved_card(card_id: str) -> None:
    if not purge_card(card_id):
        raise KnowledgeCardNotFoundError("Deleted knowledge card not found.")


def delete_all_job_cards(job_id: str) -> None:
    get_video_job(job_id)

    delete_cards_for_job(job_id)


def _validate_time_range(
    start_seconds: float,
    end_seconds: float,
) -> None:
    if end_seconds <= start_seconds:
        raise InvalidKnowledgeCardError(
            "Card source end must be greater than start."
        )


def _clean_key_points(key_points: list[str]) -> list[str]:
    return [
        point.strip()
        for point in key_points
        if point.strip()
    ]


def _clean_claims(
    claims: list[KnowledgeCardClaim],
) -> list[KnowledgeCardClaim]:
    cleaned_claims: list[KnowledgeCardClaim] = []

    for claim in claims:
        text = claim.text.strip()
        evidence = _clean_evidence(claim.evidence)

        if text and evidence:
            cleaned_claims.append(
                KnowledgeCardClaim(
                    id=claim.id,
                    text=text,
                    evidence=evidence,
                )
            )

    if not cleaned_claims:
        raise InvalidKnowledgeCardError(
            "Knowledge card must include at least one grounded claim."
        )

    return cleaned_claims


def _clean_evidence(
    evidence_items: list[KnowledgeCardEvidence],
) -> list[KnowledgeCardEvidence]:
    cleaned_evidence: list[KnowledgeCardEvidence] = []

    for evidence in evidence_items:
        if evidence.segment_end_seconds <= evidence.segment_start_seconds:
            raise InvalidKnowledgeCardError(
                "Evidence source end must be greater than start."
            )

        quote = evidence.quote.strip()

        if quote:
            cleaned_evidence.append(
                KnowledgeCardEvidence(
                    id=evidence.id,
                    quote=quote,
                    segment_start_seconds=evidence.segment_start_seconds,
                    segment_end_seconds=evidence.segment_end_seconds,
                )
            )

    return cleaned_evidence


def _clean_terms(terms: list[str]) -> list[str]:
    cleaned_terms: list[str] = []

    for term in terms:
        stripped = term.strip()

        if stripped and stripped not in cleaned_terms:
            cleaned_terms.append(stripped)

    return cleaned_terms


def _clean_tags(tags: list[str]) -> list[str]:
    cleaned_tags: list[str] = []

    for tag in tags:
        stripped = tag.strip().lower()

        if stripped and stripped not in cleaned_tags:
            cleaned_tags.append(stripped)

    return cleaned_tags


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None

    stripped = value.strip()

    return stripped or None
