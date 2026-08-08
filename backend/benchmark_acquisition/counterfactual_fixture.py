"""Strict loader for the separated counterfactual Source and gold artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


PRODUCTION_RELATION_TYPES = frozenset(
    {"prerequisite", "part_of", "example_of", "related", "contrast_with"}
)
SYMMETRIC_RELATION_TYPES = frozenset({"related", "contrast_with"})
SUPPORT_BASES = frozenset({"source_asserted", "pedagogical_inference"})
SUPPORT_ROLES = frozenset(
    {"relation_assertion", "source_endpoint", "target_endpoint"}
)
REFUSAL_REASONS = frozenset(
    {"explicitly_unsupported_by_source", "absent_from_registered_sources"}
)
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class CounterfactualFixtureError(ValueError):
    """Raised when Source/gold separation or label lineage is invalid."""


@dataclass(frozen=True, slots=True)
class SourceSection:
    source_id: str
    locator: str
    heading: str
    text: str


@dataclass(frozen=True, slots=True)
class EvidenceSpan:
    source_id: str
    locator: str
    exact_quote: str
    span_sha256: str
    support_role: str | None = None


@dataclass(frozen=True, slots=True)
class GoldConcept:
    concept_id: str
    preferred_name: str
    short_definition: str
    evidence: tuple[EvidenceSpan, ...]


@dataclass(frozen=True, slots=True)
class GoldRelation:
    relation_id: str
    source_concept_id: str
    relation_type: str
    target_concept_id: str
    support_basis: str
    rationale: str
    evidence: tuple[EvidenceSpan, ...]


@dataclass(frozen=True, slots=True)
class ExpectedClaim:
    claim_id: str
    required: bool
    subject: str
    predicate: str
    object: str
    allowed_subject_forms: tuple[str, ...]
    allowed_predicate_forms: tuple[str, ...]
    allowed_object_forms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GoldQuestion:
    question_id: str
    question: str
    answerable: bool
    response_contract: str
    refusal_reason: str | None
    expected_claims: tuple[ExpectedClaim, ...]
    citation_required: bool
    allow_additional_supported_citations: bool
    required_evidence: tuple[EvidenceSpan, ...]


@dataclass(frozen=True, slots=True)
class CounterfactualFixture:
    fixture_id: str
    source_sha256: str
    gold_sha256: str
    sections: tuple[SourceSection, ...]
    concepts: tuple[GoldConcept, ...]
    relations: tuple[GoldRelation, ...]
    questions: tuple[GoldQuestion, ...]


def load_counterfactual_fixture(
    source_path: Path,
    gold_path: Path,
) -> CounterfactualFixture:
    """Load two sidecar-hashed artifacts and validate every gold reference."""

    source_bytes, source_sha256 = _load_hashed_artifact(source_path)
    gold_bytes, gold_sha256 = _load_hashed_artifact(gold_path)
    source_payload = _decode_json(source_bytes, source_path)
    gold_payload = _decode_json(gold_bytes, gold_path)
    if any(key.startswith("gold_") for key in _walk_keys(source_payload)):
        raise CounterfactualFixtureError(
            "The ingestible Source artifact must not contain gold labels"
        )

    fixture_id, sections = _parse_source(source_payload)
    concepts, relations, questions = _parse_gold(
        gold_payload,
        fixture_id=fixture_id,
        source_filename=source_path.name,
        source_sha256=source_sha256,
        sections=sections,
    )
    return CounterfactualFixture(
        fixture_id=fixture_id,
        source_sha256=source_sha256,
        gold_sha256=gold_sha256,
        sections=tuple(sections.values()),
        concepts=concepts,
        relations=relations,
        questions=questions,
    )


def _load_hashed_artifact(path: Path) -> tuple[bytes, str]:
    try:
        payload = path.read_bytes()
        sidecar = path.with_suffix(".sha256").read_text(encoding="ascii")
    except OSError as exc:
        raise CounterfactualFixtureError(f"Cannot read fixture artifact: {path}") from exc
    parts = sidecar.strip().split("  ", 1)
    if len(parts) != 2 or parts[1] != path.name or not _HEX_64.fullmatch(parts[0]):
        raise CounterfactualFixtureError(f"Invalid SHA-256 sidecar for {path.name}")
    actual = hashlib.sha256(payload).hexdigest()
    if actual != parts[0]:
        raise CounterfactualFixtureError(f"SHA-256 mismatch for {path.name}")
    return payload, actual


def _decode_json(payload: bytes, path: Path) -> object:
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CounterfactualFixtureError(f"Invalid UTF-8 JSON: {path.name}") from exc


def _parse_source(payload: object) -> tuple[str, dict[tuple[str, str], SourceSection]]:
    root = _mapping(payload, "source fixture")
    _exact_keys(
        root,
        {
            "schema_version",
            "fixture_id",
            "artifact_role",
            "title",
            "authorship",
            "license_spdx",
            "license_url",
            "purpose",
            "sources",
        },
        "source fixture",
    )
    if root["schema_version"] != 1:
        raise CounterfactualFixtureError("Unsupported source fixture schema")
    if root["artifact_role"] != "ingestible_source":
        raise CounterfactualFixtureError("Incorrect source artifact_role")
    if root["license_spdx"] != "CC0-1.0":
        raise CounterfactualFixtureError("Counterfactual Source must be CC0-1.0")
    fixture_id = _text(root["fixture_id"], "fixture_id")
    for key in ("title", "authorship", "license_url", "purpose"):
        _text(root[key], key)

    sources = _list(root["sources"], "sources", require_nonempty=True)
    source_ids: set[str] = set()
    sections: dict[tuple[str, str], SourceSection] = {}
    for source_index, source_payload in enumerate(sources):
        label = f"sources[{source_index}]"
        source = _mapping(source_payload, label)
        _exact_keys(source, {"source_id", "title", "sections"}, label)
        source_id = _text(source["source_id"], f"{label}.source_id")
        if source_id in source_ids:
            raise CounterfactualFixtureError(f"Duplicate source_id: {source_id}")
        source_ids.add(source_id)
        _text(source["title"], f"{label}.title")
        raw_sections = _list(
            source["sections"], f"{label}.sections", require_nonempty=True
        )
        for section_index, section_payload in enumerate(raw_sections):
            section_label = f"{label}.sections[{section_index}]"
            section = _mapping(section_payload, section_label)
            _exact_keys(section, {"locator", "heading", "text"}, section_label)
            locator = _text(section["locator"], f"{section_label}.locator")
            key = (source_id, locator)
            if key in sections:
                raise CounterfactualFixtureError(
                    f"Duplicate Source locator: {source_id}#{locator}"
                )
            sections[key] = SourceSection(
                source_id=source_id,
                locator=locator,
                heading=_text(section["heading"], f"{section_label}.heading"),
                text=_text(section["text"], f"{section_label}.text"),
            )
    return fixture_id, sections


def _parse_gold(
    payload: object,
    *,
    fixture_id: str,
    source_filename: str,
    source_sha256: str,
    sections: Mapping[tuple[str, str], SourceSection],
) -> tuple[tuple[GoldConcept, ...], tuple[GoldRelation, ...], tuple[GoldQuestion, ...]]:
    root = _mapping(payload, "gold fixture")
    _exact_keys(
        root,
        {
            "schema_version",
            "fixture_id",
            "artifact_role",
            "source_artifact",
            "span_hash_contract",
            "claim_matching_contract",
            "relation_ontology",
            "gold_concepts",
            "gold_relations",
            "gold_questions",
        },
        "gold fixture",
    )
    if root["schema_version"] != 1 or root["fixture_id"] != fixture_id:
        raise CounterfactualFixtureError("Gold fixture identity mismatch")
    if root["artifact_role"] != "gold_labels_and_questions":
        raise CounterfactualFixtureError("Incorrect gold artifact_role")
    source_artifact = _mapping(root["source_artifact"], "source_artifact")
    _exact_keys(source_artifact, {"filename", "sha256"}, "source_artifact")
    if (
        source_artifact["filename"] != source_filename
        or source_artifact["sha256"] != source_sha256
    ):
        raise CounterfactualFixtureError("Gold fixture does not bind the Source bytes")
    if root["span_hash_contract"] != (
        "sha256 of the exact UTF-8 quote bytes with no normalization"
    ):
        raise CounterfactualFixtureError("Unsupported span hash contract")
    _validate_claim_matching_contract(root["claim_matching_contract"])
    ontology = _string_tuple(root["relation_ontology"], "relation_ontology")
    if set(ontology) != PRODUCTION_RELATION_TYPES:
        raise CounterfactualFixtureError("Gold relation ontology differs from production")

    concepts = _parse_concepts(root["gold_concepts"], sections)
    relations = _parse_relations(root["gold_relations"], sections, concepts)
    questions = _parse_questions(root["gold_questions"], sections)
    return concepts, relations, questions


def _parse_concepts(
    payload: object,
    sections: Mapping[tuple[str, str], SourceSection],
) -> tuple[GoldConcept, ...]:
    raw_concepts = _list(payload, "gold_concepts", require_nonempty=True)
    concepts: list[GoldConcept] = []
    ids: set[str] = set()
    for index, raw in enumerate(raw_concepts):
        label = f"gold_concepts[{index}]"
        item = _mapping(raw, label)
        _exact_keys(
            item,
            {"concept_id", "preferred_name", "short_definition", "evidence"},
            label,
        )
        concept_id = _unique_id(item["concept_id"], ids, f"{label}.concept_id")
        evidence = _parse_evidence_list(item["evidence"], sections, label=label)
        concepts.append(
            GoldConcept(
                concept_id=concept_id,
                preferred_name=_text(item["preferred_name"], f"{label}.preferred_name"),
                short_definition=_text(
                    item["short_definition"], f"{label}.short_definition"
                ),
                evidence=evidence,
            )
        )
    return tuple(concepts)


def _parse_relations(
    payload: object,
    sections: Mapping[tuple[str, str], SourceSection],
    concepts: tuple[GoldConcept, ...],
) -> tuple[GoldRelation, ...]:
    raw_relations = _list(payload, "gold_relations", require_nonempty=True)
    concepts_by_id = {concept.concept_id: concept for concept in concepts}
    concept_ids = set(concepts_by_id)
    ids: set[str] = set()
    relations: list[GoldRelation] = []
    for index, raw in enumerate(raw_relations):
        label = f"gold_relations[{index}]"
        item = _mapping(raw, label)
        _exact_keys(
            item,
            {
                "relation_id",
                "source_concept_id",
                "relation_type",
                "target_concept_id",
                "support_basis",
                "rationale",
                "evidence",
            },
            label,
        )
        relation_id = _unique_id(item["relation_id"], ids, f"{label}.relation_id")
        source_id = _text(item["source_concept_id"], f"{label}.source_concept_id")
        target_id = _text(item["target_concept_id"], f"{label}.target_concept_id")
        if source_id not in concept_ids or target_id not in concept_ids:
            raise CounterfactualFixtureError(f"Dangling relation endpoint in {relation_id}")
        if source_id == target_id:
            raise CounterfactualFixtureError(f"Relation self-loop in {relation_id}")
        relation_type = _text(item["relation_type"], f"{label}.relation_type")
        if relation_type not in PRODUCTION_RELATION_TYPES:
            raise CounterfactualFixtureError(f"Unsupported relation type: {relation_type}")
        if relation_type in SYMMETRIC_RELATION_TYPES and source_id > target_id:
            raise CounterfactualFixtureError(
                f"Symmetric relation endpoints are not in canonical order: {relation_id}"
            )
        support_basis = _text(item["support_basis"], f"{label}.support_basis")
        if support_basis not in SUPPORT_BASES:
            raise CounterfactualFixtureError(f"Unsupported support basis: {support_basis}")
        evidence = _parse_evidence_list(
            item["evidence"], sections, label=label, require_support_role=True
        )
        roles = {span.support_role for span in evidence}
        expected_roles = (
            {"relation_assertion"}
            if support_basis == "source_asserted"
            else {"source_endpoint", "target_endpoint"}
        )
        if roles != expected_roles:
            raise CounterfactualFixtureError(
                f"Evidence roles do not match support_basis for {relation_id}"
            )
        if support_basis == "pedagogical_inference":
            _validate_endpoint_evidence(
                relation_id,
                evidence,
                source_concept=concepts_by_id[source_id],
                target_concept=concepts_by_id[target_id],
            )
        relations.append(
            GoldRelation(
                relation_id=relation_id,
                source_concept_id=source_id,
                relation_type=relation_type,
                target_concept_id=target_id,
                support_basis=support_basis,
                rationale=_text(item["rationale"], f"{label}.rationale"),
                evidence=evidence,
            )
        )
    return tuple(relations)


def _validate_endpoint_evidence(
    relation_id: str,
    evidence: tuple[EvidenceSpan, ...],
    *,
    source_concept: GoldConcept,
    target_concept: GoldConcept,
) -> None:
    source_spans = {_span_identity(span) for span in source_concept.evidence}
    target_spans = {_span_identity(span) for span in target_concept.evidence}
    for span in evidence:
        expected = source_spans if span.support_role == "source_endpoint" else target_spans
        if _span_identity(span) not in expected:
            raise CounterfactualFixtureError(
                f"Pedagogical endpoint evidence does not match its Concept: {relation_id}"
            )


def _span_identity(span: EvidenceSpan) -> tuple[str, str, str, str]:
    return (
        span.source_id,
        span.locator,
        span.exact_quote,
        span.span_sha256,
    )


def _parse_questions(
    payload: object,
    sections: Mapping[tuple[str, str], SourceSection],
) -> tuple[GoldQuestion, ...]:
    raw_questions = _list(payload, "gold_questions", require_nonempty=True)
    ids: set[str] = set()
    claim_ids: set[str] = set()
    questions: list[GoldQuestion] = []
    for index, raw in enumerate(raw_questions):
        label = f"gold_questions[{index}]"
        item = _mapping(raw, label)
        _exact_keys(
            item,
            {
                "question_id",
                "question",
                "answerable",
                "response_contract",
                "refusal_reason",
                "expected_claims",
                "citation_contract",
            },
            label,
        )
        question_id = _unique_id(item["question_id"], ids, f"{label}.question_id")
        answerable = _boolean(item["answerable"], f"{label}.answerable")
        response_contract = _text(
            item["response_contract"], f"{label}.response_contract"
        )
        claims = _parse_claims(item["expected_claims"], claim_ids, label)
        refusal_reason = item["refusal_reason"]
        if answerable:
            if response_contract != "answer_with_supported_claims" or refusal_reason is not None:
                raise CounterfactualFixtureError(
                    f"Answerable question contract mismatch: {question_id}"
                )
            if not claims:
                raise CounterfactualFixtureError(
                    f"Answerable question lacks expected claims: {question_id}"
                )
        else:
            if response_contract != "refuse" or not isinstance(refusal_reason, str):
                raise CounterfactualFixtureError(
                    f"Refusal question contract mismatch: {question_id}"
                )
            _text(refusal_reason, f"{label}.refusal_reason")
            if refusal_reason not in REFUSAL_REASONS:
                raise CounterfactualFixtureError(
                    f"Unsupported refusal reason: {refusal_reason}"
                )
            if claims:
                raise CounterfactualFixtureError(
                    f"Refusal question must not contain expected claims: {question_id}"
                )
        citation = _mapping(item["citation_contract"], f"{label}.citation_contract")
        _exact_keys(
            citation,
            {
                "required",
                "allow_additional_supported_citations",
                "required_evidence",
            },
            f"{label}.citation_contract",
        )
        citation_required = _boolean(
            citation["required"], f"{label}.citation_contract.required"
        )
        evidence = _parse_evidence_list(
            citation["required_evidence"],
            sections,
            label=f"{label}.citation_contract",
            require_nonempty=citation_required,
        )
        if not citation_required and evidence:
            raise CounterfactualFixtureError(
                f"Optional citation contract has required evidence: {question_id}"
            )
        if answerable and not citation_required:
            raise CounterfactualFixtureError(
                f"Answerable question must require a citation: {question_id}"
            )
        if refusal_reason == "explicitly_unsupported_by_source" and not citation_required:
            raise CounterfactualFixtureError(
                f"Explicitly unsupported refusal must cite the Source: {question_id}"
            )
        if refusal_reason == "absent_from_registered_sources" and citation_required:
            raise CounterfactualFixtureError(
                f"Source-absent refusal cannot require evidence: {question_id}"
            )
        questions.append(
            GoldQuestion(
                question_id=question_id,
                question=_text(item["question"], f"{label}.question"),
                answerable=answerable,
                response_contract=response_contract,
                refusal_reason=refusal_reason,
                expected_claims=claims,
                citation_required=citation_required,
                allow_additional_supported_citations=_boolean(
                    citation["allow_additional_supported_citations"],
                    f"{label}.citation_contract.allow_additional_supported_citations",
                ),
                required_evidence=evidence,
            )
        )
    return tuple(questions)


def _parse_claims(
    payload: object, ids: set[str], question_label: str
) -> tuple[ExpectedClaim, ...]:
    raw_claims = _list(payload, f"{question_label}.expected_claims")
    claims: list[ExpectedClaim] = []
    for index, raw in enumerate(raw_claims):
        label = f"{question_label}.expected_claims[{index}]"
        item = _mapping(raw, label)
        _exact_keys(
            item,
            {
                "claim_id",
                "required",
                "subject",
                "predicate",
                "object",
                "allowed_subject_forms",
                "allowed_predicate_forms",
                "allowed_object_forms",
            },
            label,
        )
        claims.append(
            ExpectedClaim(
                claim_id=_unique_id(item["claim_id"], ids, f"{label}.claim_id"),
                required=_boolean(item["required"], f"{label}.required"),
                subject=_text(item["subject"], f"{label}.subject"),
                predicate=_text(item["predicate"], f"{label}.predicate"),
                object=_text(item["object"], f"{label}.object"),
                allowed_subject_forms=_string_tuple(
                    item["allowed_subject_forms"], f"{label}.allowed_subject_forms"
                ),
                allowed_predicate_forms=_string_tuple(
                    item["allowed_predicate_forms"], f"{label}.allowed_predicate_forms"
                ),
                allowed_object_forms=_string_tuple(
                    item["allowed_object_forms"], f"{label}.allowed_object_forms"
                ),
            )
        )
    return tuple(claims)


def _parse_evidence_list(
    payload: object,
    sections: Mapping[tuple[str, str], SourceSection],
    *,
    label: str,
    require_support_role: bool = False,
    require_nonempty: bool = True,
) -> tuple[EvidenceSpan, ...]:
    raw_spans = _list(payload, f"{label}.evidence", require_nonempty=require_nonempty)
    spans: list[EvidenceSpan] = []
    identities: set[tuple[str, str, str, str | None]] = set()
    for index, raw in enumerate(raw_spans):
        span_label = f"{label}.evidence[{index}]"
        item = _mapping(raw, span_label)
        expected_keys = {"source_id", "locator", "exact_quote", "span_sha256"}
        if require_support_role:
            expected_keys.add("support_role")
        _exact_keys(item, expected_keys, span_label)
        source_id = _text(item["source_id"], f"{span_label}.source_id")
        locator = _text(item["locator"], f"{span_label}.locator")
        quote = _text(item["exact_quote"], f"{span_label}.exact_quote")
        span_sha256 = _hex64(item["span_sha256"], f"{span_label}.span_sha256")
        section = sections.get((source_id, locator))
        if section is None:
            raise CounterfactualFixtureError(
                f"Dangling evidence locator: {source_id}#{locator}"
            )
        if quote not in section.text:
            raise CounterfactualFixtureError(f"Evidence quote is absent: {span_label}")
        if hashlib.sha256(quote.encode("utf-8")).hexdigest() != span_sha256:
            raise CounterfactualFixtureError(f"Evidence span hash mismatch: {span_label}")
        support_role = None
        if require_support_role:
            support_role = _text(item["support_role"], f"{span_label}.support_role")
            if support_role not in SUPPORT_ROLES:
                raise CounterfactualFixtureError(
                    f"Unsupported evidence role: {support_role}"
                )
        identity = (source_id, locator, quote, support_role)
        if identity in identities:
            raise CounterfactualFixtureError(f"Duplicate evidence span: {span_label}")
        identities.add(identity)
        spans.append(
            EvidenceSpan(
                source_id=source_id,
                locator=locator,
                exact_quote=quote,
                span_sha256=span_sha256,
                support_role=support_role,
            )
        )
    return tuple(spans)


def _validate_claim_matching_contract(payload: object) -> None:
    item = _mapping(payload, "claim_matching_contract")
    _exact_keys(
        item,
        {"mode", "normalization", "extra_supported_claims_allowed", "unsupported_claims_allowed"},
        "claim_matching_contract",
    )
    if item["mode"] != "structured_required_claims":
        raise CounterfactualFixtureError("Unsupported claim matching mode")
    normalization = _string_tuple(item["normalization"], "normalization")
    if normalization != (
        "unicode_nfkc",
        "casefold",
        "collapse_whitespace",
        "strip_terminal_punctuation",
    ):
        raise CounterfactualFixtureError("Unexpected claim normalization contract")
    if item["extra_supported_claims_allowed"] is not True:
        raise CounterfactualFixtureError("Supported extra claims must be allowed")
    if item["unsupported_claims_allowed"] is not False:
        raise CounterfactualFixtureError("Unsupported claims must be forbidden")


def _walk_keys(value: object):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key
            yield from _walk_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_keys(nested)


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise CounterfactualFixtureError(f"{label} must be an object")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise CounterfactualFixtureError(
            f"{label} keys mismatch; missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _list(value: object, label: str, *, require_nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list) or (require_nonempty and not value):
        raise CounterfactualFixtureError(f"{label} must be a list")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise CounterfactualFixtureError(f"{label} must be a non-empty trimmed string")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise CounterfactualFixtureError(f"{label} must be boolean")
    return value


def _hex64(value: object, label: str) -> str:
    text = _text(value, label)
    if not _HEX_64.fullmatch(text):
        raise CounterfactualFixtureError(f"{label} must be lowercase SHA-256")
    return text


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    raw = _list(value, label)
    result = tuple(_text(item, label) for item in raw)
    if len(result) != len(set(result)):
        raise CounterfactualFixtureError(f"{label} contains duplicates")
    return result


def _unique_id(value: object, seen: set[str], label: str) -> str:
    result = _text(value, label)
    if result in seen:
        raise CounterfactualFixtureError(f"Duplicate identifier: {result}")
    seen.add(result)
    return result
