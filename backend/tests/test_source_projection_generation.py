from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

import app.course_source_store as course_source_store
import pytest

from app.course import CourseCreate
from app.course_service import create_video_course
from app.course_source import (
    CourseSource,
    CourseSourceChunk,
    PdfPageLocator,
    hash_source_chunk_text,
)
from app.course_source_store import get_source, replace_source_projection
from app.source_asset import SourceAsset
from app.source_asset_store import create_source_asset
from app.source_projection_identity import (
    ProjectionManifestChunk,
    build_projection_manifest_hash,
)


def _manifest_chunk(**updates: object) -> ProjectionManifestChunk:
    values: dict[str, object] = {
        "id": "chunk-a",
        "chunk_type": "page",
        "ordinal": 0,
        "text_hash": "a" * 64,
        "locator": {
            "schema_version": 1,
            "kind": "pdf_page",
            "asset_id": "asset-a",
            "page_number": 1,
            "metadata": {},
        },
        "chunker_version": "chunker-v1",
    }
    values.update(updates)
    return ProjectionManifestChunk(**values)  # type: ignore[arg-type]


def test_manifest_is_canonical_and_covers_every_address_field() -> None:
    first = _manifest_chunk()
    second = _manifest_chunk(
        id="chunk-b",
        ordinal=1,
        text_hash="b" * 64,
        locator={
            "kind": "pdf_page",
            "schema_version": 1,
            "metadata": {},
            "page_number": 2,
            "asset_id": "asset-a",
        },
    )

    baseline = build_projection_manifest_hash(
        source_id="asset:asset-a",
        source_type="pdf",
        chunks=[first, second],
    )
    assert baseline == build_projection_manifest_hash(
        source_id="asset:asset-a",
        source_type="pdf",
        chunks=[second, first],
    )
    assert baseline == build_projection_manifest_hash(
        source_id="asset:asset-a",
        source_type="pdf",
        chunks=[
            _manifest_chunk(
                locator={
                    "metadata": {},
                    "page_number": 1,
                    "asset_id": "asset-a",
                    "kind": "pdf_page",
                    "schema_version": 1,
                }
            ),
            second,
        ],
    )

    variants = [
        ("asset:asset-b", "pdf", [first, second]),
        ("asset:asset-a", "text", [first, second]),
        ("asset:asset-a", "pdf", [_manifest_chunk(id="changed"), second]),
        (
            "asset:asset-a",
            "pdf",
            [_manifest_chunk(chunk_type="paragraph"), second],
        ),
        ("asset:asset-a", "pdf", [_manifest_chunk(ordinal=2), second]),
        (
            "asset:asset-a",
            "pdf",
            [_manifest_chunk(text_hash="c" * 64), second],
        ),
        (
            "asset:asset-a",
            "pdf",
            [
                _manifest_chunk(
                    locator={
                        "schema_version": 1,
                        "kind": "pdf_page",
                        "asset_id": "asset-a",
                        "page_number": 3,
                        "metadata": {},
                    }
                ),
                second,
            ],
        ),
        (
            "asset:asset-a",
            "pdf",
            [_manifest_chunk(chunker_version="chunker-v2"), second],
        ),
    ]
    assert all(
        build_projection_manifest_hash(
            source_id=source_id,
            source_type=source_type,
            chunks=chunks,
        )
        != baseline
        for source_id, source_type, chunks in variants
    )

    with pytest.raises(ValueError):
        build_projection_manifest_hash(
            source_id="job:video-a",
            source_type="video",
            chunks=[
                _manifest_chunk(
                    chunk_type="transcript",
                    locator={
                        "schema_version": 1,
                        "kind": "video_time",
                        "job_id": "video-a",
                        "start_seconds": float("nan"),
                        "end_seconds": 3,
                        "segment_ids": [],
                        "metadata": {},
                    },
                )
            ],
        )


def _projection(
    suffix: str,
    *,
    page_number: int = 1,
    text: str = "Projection generations bind evidence to one address.",
) -> tuple[CourseSource, CourseSourceChunk]:
    course = create_video_course(CourseCreate(title=f"Projection {suffix}"))
    asset_id = f"projection-{suffix}"
    create_source_asset(
        SourceAsset(
            id=asset_id,
            course_id=course.id,
            asset_type="pdf",
            original_filename=f"{suffix}.pdf",
            stored_path=f"{suffix}.pdf",
            size_bytes=1,
            sha256="a" * 64,
            extraction_status="ready",
        )
    )
    source = CourseSource(
        id=f"asset:{asset_id}",
        course_id=course.id,
        origin_type="source_asset",
        origin_id=asset_id,
        source_type="pdf",
        title=f"{suffix}.pdf",
        content_status="ready",
    )
    chunk = CourseSourceChunk(
        id=f"source_unit:{asset_id}-page",
        source_id=source.id,
        origin_type="source_unit",
        origin_id=f"{asset_id}-page",
        chunk_type="page",
        ordinal=0,
        text=text,
        text_hash=hash_source_chunk_text(text),
        locator=PdfPageLocator(
            asset_id=asset_id,
            page_number=page_number,
        ),
        chunker_version="projection-test-v1",
    )
    return source, chunk


def test_identical_republish_retains_generation_but_drift_revert_does_not() -> None:
    source, original = _projection("drift-revert")
    replace_source_projection(source, [original])
    first = get_source(source.id)
    assert first is not None
    assert first.projection_generation_id
    assert first.projection_manifest_hash

    replace_source_projection(source, [original])
    identical = get_source(source.id)
    assert identical is not None
    assert identical.projection_generation_id == first.projection_generation_id
    assert identical.projection_manifest_hash == first.projection_manifest_hash

    drifted_chunk = original.model_copy(
        update={
            "locator": PdfPageLocator(
                asset_id=source.origin_id,
                page_number=2,
            )
        }
    )
    replace_source_projection(source, [drifted_chunk])
    drifted = get_source(source.id)
    assert drifted is not None
    assert drifted.projection_generation_id != first.projection_generation_id
    assert drifted.projection_manifest_hash != first.projection_manifest_hash

    replace_source_projection(source, [original])
    reverted = get_source(source.id)
    assert reverted is not None
    assert reverted.projection_manifest_hash == first.projection_manifest_hash
    assert reverted.projection_generation_id not in {
        first.projection_generation_id,
        drifted.projection_generation_id,
    }


@pytest.mark.parametrize(
    "drift_kind",
    ["ordinal", "chunker", "chunk_type", "text", "source_type"],
)
def test_every_projection_address_change_rotates_generation(
    drift_kind: str,
) -> None:
    source, chunk = _projection(f"field-{drift_kind}")
    replace_source_projection(source, [chunk])
    before = get_source(source.id)
    assert before is not None

    next_source = source
    next_chunk = chunk
    if drift_kind == "ordinal":
        next_chunk = chunk.model_copy(update={"ordinal": 1})
    elif drift_kind == "chunker":
        next_chunk = chunk.model_copy(update={"chunker_version": "v2"})
    elif drift_kind == "chunk_type":
        next_chunk = chunk.model_copy(update={"chunk_type": "paragraph"})
    elif drift_kind == "text":
        next_text = f"{chunk.text} Changed."
        next_chunk = chunk.model_copy(
            update={
                "text": next_text,
                "text_hash": hash_source_chunk_text(next_text),
            }
        )
    else:
        next_source = source.model_copy(update={"source_type": "text"})

    replace_source_projection(next_source, [next_chunk])
    after = get_source(source.id)
    assert after is not None
    assert after.projection_generation_id != before.projection_generation_id
    assert after.projection_manifest_hash != before.projection_manifest_hash


def test_input_order_is_irrelevant_and_existing_ordinals_can_swap() -> None:
    source, first = _projection("ordinal-swap")
    second_text = "A second stable Chunk address."
    second = first.model_copy(
        update={
            "id": f"{first.id}-second",
            "origin_id": f"{first.origin_id}-second",
            "ordinal": 1,
            "text": second_text,
            "text_hash": hash_source_chunk_text(second_text),
            "locator": PdfPageLocator(
                asset_id=source.origin_id,
                page_number=2,
            ),
        }
    )
    replace_source_projection(source, [first, second])
    initial = get_source(source.id)
    assert initial is not None

    replace_source_projection(source, [second, first])
    reordered_input = get_source(source.id)
    assert reordered_input is not None
    assert reordered_input.projection_generation_id == (
        initial.projection_generation_id
    )

    replace_source_projection(
        source,
        [
            first.model_copy(update={"ordinal": 1}),
            second.model_copy(update={"ordinal": 0}),
        ],
    )
    swapped = get_source(source.id)
    assert swapped is not None
    assert swapped.projection_generation_id != initial.projection_generation_id
    persisted = course_source_store.list_source_chunks(source.id)
    assert [(item.id, item.ordinal) for item in persisted] == [
        (second.id, 0),
        (first.id, 1),
    ]

    front_text = "A newly inserted front Chunk."
    front = first.model_copy(
        update={
            "id": f"{first.id}-front",
            "origin_id": f"{first.origin_id}-front",
            "ordinal": 0,
            "text": front_text,
            "text_hash": hash_source_chunk_text(front_text),
            "locator": PdfPageLocator(
                asset_id=source.origin_id,
                page_number=3,
            ),
        }
    )
    replace_source_projection(
        source,
        [
            front,
            second.model_copy(update={"ordinal": 1}),
            first.model_copy(update={"ordinal": 2}),
        ],
    )
    shifted = course_source_store.list_source_chunks(source.id)
    assert [(item.id, item.ordinal) for item in shifted] == [
        (front.id, 0),
        (second.id, 1),
        (first.id, 2),
    ]


def test_projection_rejects_untrusted_or_ambiguous_chunks_atomically() -> None:
    source, chunk = _projection("invalid")
    replace_source_projection(source, [chunk])
    before = get_source(source.id)
    assert before is not None

    invalid_hash = chunk.model_copy(update={"text_hash": "f" * 64})
    with pytest.raises(ValueError, match="text hash"):
        replace_source_projection(source, [invalid_hash])

    with pytest.raises(ValueError, match="chunk ids"):
        replace_source_projection(source, [chunk, chunk])

    duplicate_ordinal = chunk.model_copy(
        update={
            "id": f"{chunk.id}-two",
            "origin_id": f"{chunk.origin_id}-two",
        }
    )
    with pytest.raises(ValueError, match="ordinals"):
        replace_source_projection(source, [chunk, duplicate_ordinal])

    with pytest.raises(ValueError, match="active Source chunks"):
        replace_source_projection(
            source,
            [chunk.model_copy(update={"is_active": False})],
        )
    with pytest.raises(ValueError, match="origin identity"):
        replace_source_projection(
            source.model_copy(update={"origin_id": "different-root"}),
            [chunk],
        )

    after = get_source(source.id)
    assert after is not None
    assert after.projection_generation_id == before.projection_generation_id
    assert after.projection_manifest_hash == before.projection_manifest_hash


def test_chunk_identity_cannot_be_reparented_between_sources() -> None:
    source, chunk = _projection("owner-a")
    replace_source_projection(source, [chunk])
    other_source, other_chunk = _projection("owner-b")
    stolen = other_chunk.model_copy(
        update={
            "id": chunk.id,
            "origin_id": chunk.origin_id,
        }
    )

    with pytest.raises(ValueError, match="cannot move between Sources"):
        replace_source_projection(other_source, [stolen])

    assert get_source(other_source.id) is None
    owner = course_source_store.list_source_chunks(source.id)
    assert [item.id for item in owner] == [chunk.id]


def test_projection_generation_and_chunks_roll_back_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, chunk = _projection("rollback")
    replace_source_projection(source, [chunk])
    before = get_source(source.id)
    assert before is not None
    drifted = chunk.model_copy(
        update={
            "locator": PdfPageLocator(
                asset_id=source.origin_id,
                page_number=2,
            )
        }
    )

    def fail_chunk_publish(*_args, **_kwargs) -> None:
        raise RuntimeError("injected chunk publication failure")

    monkeypatch.setattr(
        course_source_store,
        "_replace_source_chunks",
        fail_chunk_publish,
    )
    with pytest.raises(RuntimeError, match="injected chunk"):
        replace_source_projection(source, [drifted])

    after = get_source(source.id)
    assert after is not None
    assert after.projection_generation_id == before.projection_generation_id
    assert after.projection_manifest_hash == before.projection_manifest_hash
    persisted = course_source_store.list_source_chunks(source.id)
    assert persisted[0].locator.page_number == 1


def test_concurrent_replacements_serialize_to_one_coherent_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, chunk = _projection("concurrent")
    replace_source_projection(source, [chunk])
    original = get_source(source.id)
    assert original is not None
    ready = Barrier(2)
    generations: list[str] = []
    generations_lock = Lock()
    real_select = course_source_store.select_projection_generation_id

    def record_generation(**kwargs: str | None) -> str:
        generation = real_select(**kwargs)
        with generations_lock:
            generations.append(generation)
        return generation

    monkeypatch.setattr(
        course_source_store,
        "select_projection_generation_id",
        record_generation,
    )

    def publish(page_number: int) -> None:
        replacement = chunk.model_copy(
            update={
                "locator": PdfPageLocator(
                    asset_id=source.origin_id,
                    page_number=page_number,
                )
            }
        )
        ready.wait()
        replace_source_projection(source, [replacement])

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(publish, page) for page in (2, 3)]
        for future in futures:
            future.result(timeout=10)

    final = get_source(source.id)
    assert final is not None
    assert len(generations) == 2
    assert len(set(generations)) == 2
    assert original.projection_generation_id not in generations
    assert final.projection_generation_id == generations[-1]
    persisted = course_source_store.list_source_chunks(source.id)
    assert len(persisted) == 1
    assert persisted[0].locator.page_number in {2, 3}
