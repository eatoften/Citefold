from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.citation_target_service as citation_target_service
import app.main as main
from app.chat import ChatCitation, ChatConversation
from app.chat_store import (
    complete_turn,
    create_conversation,
    get_conversation_detail,
    reserve_turn,
    transition_turn,
)
from app.course import DEFAULT_COURSE_ID, Course, CourseCreate
from app.course_service import (
    create_video_course,
    delete_video_course,
    restore_video_course,
)
from app.course_source import (
    CourseSource,
    CourseSourceChunk,
    DocxParagraphLocator,
    PdfPageLocator,
    PptSlideLocator,
    TextSectionLocator,
    VideoTimeLocator,
    hash_source_chunk_text,
)
from app.course_source_store import (
    delete_source_projection,
    replace_source_projection,
    set_source_enabled,
)
from app.db import connect
from app.job import VideoJob, VideoJobStatus, utc_now
from app.job_store import (
    create_job,
    get_job_video_sha256,
)
from app.migrations import MIGRATIONS, apply_migrations, prepare_migration_backup
from app.source_asset import SourceAsset
from app.source_asset_store import create_source_asset


LOOPBACK_CLIENT = ("127.0.0.1", 50000)


def _client() -> TestClient:
    return TestClient(main.app, client=LOOPBACK_CLIENT)


def _course(title: str = "Citation targets") -> Course:
    return create_video_course(CourseCreate(title=title))


def _managed_paths(tmp_path: Path, monkeypatch) -> SimpleNamespace:
    upload_dir = tmp_path / "managed" / "uploads"
    source_dir = tmp_path / "managed" / "sources"
    upload_dir.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    paths = SimpleNamespace(upload_dir=upload_dir, source_dir=source_dir)
    monkeypatch.setattr(
        citation_target_service,
        "get_app_path_settings",
        lambda: paths,
    )
    monkeypatch.setattr(
        main.course_source_service,
        "reconcile_course_sources",
        lambda _course_id: [],
    )
    return paths


def _asset_source(
    *,
    course: Course,
    paths: SimpleNamespace,
    asset_id: str,
    source_type: str,
    extension: str,
    locator,
    text: str,
    content: bytes | None = None,
    stored_path: Path | None = None,
    sha256: str | None = None,
) -> tuple[CourseSource, CourseSourceChunk, Path]:
    payload = content or f"{asset_id} source bytes".encode()
    path = stored_path or (
        paths.source_dir / course.id / f"{asset_id}{extension}"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if content is not None or stored_path is None:
        path.write_bytes(payload)
    asset = SourceAsset(
        id=asset_id,
        course_id=course.id,
        asset_type=source_type,
        original_filename=f"{asset_id}{extension}",
        stored_path=str(path),
        size_bytes=len(payload),
        sha256=sha256 or hashlib.sha256(payload).hexdigest(),
        extraction_status="ready",
    )
    create_source_asset(asset)
    source = CourseSource(
        id=f"asset:{asset_id}",
        course_id=course.id,
        origin_type="source_asset",
        origin_id=asset_id,
        source_type=source_type,
        title=asset.original_filename,
        content_status="ready",
    )
    chunk = CourseSourceChunk(
        id=f"source_unit:{asset_id}-unit",
        source_id=source.id,
        origin_type="source_unit",
        origin_id=f"{asset_id}-unit",
        chunk_type={
            "pdf": "page",
            "pptx": "slide",
            "docx": "paragraph",
            "text": "text",
            "audio": "transcript",
            "video": "transcript",
        }[source_type],
        ordinal=1,
        text=text,
        text_hash=hash_source_chunk_text(text),
        locator=locator,
        chunker_version="citation-target-test-v1",
    )
    replace_source_projection(source, [chunk])
    return source, chunk, path


def _video_source(
    *,
    course: Course,
    paths: SimpleNamespace,
    job_id: str = "video-job",
    payload: bytes = b"0123456789abcdefghijklmnopqrstuvwxyz",
    fingerprint: bool = True,
) -> tuple[CourseSource, CourseSourceChunk, Path]:
    path = paths.upload_dir / f"{job_id}.mp4"
    path.write_bytes(payload)
    job = VideoJob(
        id=job_id,
        course_id=course.id,
        video_path=path,
        status=VideoJobStatus.completed,
        original_filename="lecture.mp4",
        stored_name=path.name,
        size_bytes=len(payload),
    )
    create_job(
        job,
        video_sha256=(
            hashlib.sha256(payload).hexdigest()
            if fingerprint
            else None
        ),
    )
    locator = VideoTimeLocator(
        job_id=job.id,
        start_seconds=2.5,
        end_seconds=8.0,
        segment_ids=[1],
    )
    source = CourseSource(
        id=f"job:{job.id}",
        course_id=course.id,
        origin_type="video_job",
        origin_id=job.id,
        source_type="video",
        title="lecture.mp4",
        content_status="ready",
    )
    text = "The lecture explains the cited optimization result."
    chunk = CourseSourceChunk(
        id=f"transcript_chunk:{job.id}-1",
        source_id=source.id,
        origin_type="transcript_chunk",
        origin_id=f"{job.id}-1",
        chunk_type="transcript",
        ordinal=0,
        text=text,
        text_hash=hash_source_chunk_text(text),
        locator=locator,
        chunker_version="citation-target-test-v1",
    )
    replace_source_projection(source, [chunk])
    return source, chunk, path


def _citation(
    *,
    course: Course,
    source: CourseSource,
    chunk: CourseSourceChunk,
    citation_id: str,
) -> str:
    conversation_id = f"conversation-{citation_id}"
    create_conversation(
        ChatConversation(
            id=conversation_id,
            course_id=course.id,
            title="Citation target",
            selected_source_ids=[source.id],
        )
    )
    reservation = reserve_turn(
        conversation_id,
        turn_id=f"turn-{citation_id}",
        user_message_id=f"user-{citation_id}",
        assistant_message_id=f"assistant-{citation_id}",
        client_request_id=f"request-{citation_id}",
        content="What does the source say?",
        source_ids=[source.id],
    )
    assert reservation.generation_token is not None
    transition_turn(
        reservation.turn_id,
        generation_token=reservation.generation_token,
        expected_status="pending",
        status="retrieving",
    )
    transition_turn(
        reservation.turn_id,
        generation_token=reservation.generation_token,
        expected_status="retrieving",
        status="generating",
    )
    transition_turn(
        reservation.turn_id,
        generation_token=reservation.generation_token,
        expected_status="generating",
        status="validating",
    )
    answer = "The source supports this grounded answer."
    complete_turn(
        reservation.assistant_message.id,
        generation_token=reservation.generation_token,
        content=answer,
        citations=[
            ChatCitation(
                id=citation_id,
                message_id=reservation.assistant_message.id,
                ordinal=1,
                sentence_index=0,
                start_offset=0,
                end_offset=len(answer),
                source_id=source.id,
                chunk_id=chunk.id,
                chunk_text_hash=chunk.text_hash,
                source_title=source.title,
                source_type=source.source_type,
                quote=chunk.text,
                score=0.94,
                locator=chunk.locator,
            )
        ],
    )
    return conversation_id


@pytest.mark.parametrize(
    (
        "source_type",
        "extension",
        "locator_factory",
        "media_kind",
        "mime_type",
    ),
    [
        (
            "pdf",
            ".pdf",
            lambda asset_id: PdfPageLocator(
                asset_id=asset_id,
                page_number=3,
            ),
            "pdf",
            "application/pdf",
        ),
        (
            "pptx",
            ".pptx",
            lambda asset_id: PptSlideLocator(
                asset_id=asset_id,
                slide_number=4,
            ),
            "document",
            "application/vnd.openxmlformats-officedocument."
            "presentationml.presentation",
        ),
        (
            "docx",
            ".docx",
            lambda asset_id: DocxParagraphLocator(
                asset_id=asset_id,
                paragraph_number=5,
            ),
            "document",
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document",
        ),
        (
            "text",
            ".md",
            lambda asset_id: TextSectionLocator(
                asset_id=asset_id,
                section_number=6,
            ),
            "text",
            "text/plain; charset=utf-8",
        ),
    ],
)
def test_document_locator_targets_are_server_authoritative(
    tmp_path,
    monkeypatch,
    source_type,
    extension,
    locator_factory,
    media_kind,
    mime_type,
) -> None:
    paths = _managed_paths(tmp_path, monkeypatch)
    course = _course()
    asset_id = f"asset-{source_type}"
    source, chunk, payload_path = _asset_source(
        course=course,
        paths=paths,
        asset_id=asset_id,
        source_type=source_type,
        extension=extension,
        locator=locator_factory(asset_id),
        text=f"Canonical {source_type} citation text.",
    )
    citation_id = f"citation-{source_type}"
    _citation(
        course=course,
        source=source,
        chunk=chunk,
        citation_id=citation_id,
    )

    with _client() as client:
        response = client.get(
            f"/courses/{course.id}/chat/citations/{citation_id}/target"
        )
        content = client.get(
            f"/courses/{course.id}/chat/citations/{citation_id}/content"
        )

    assert response.status_code == 200, response.text
    target = response.json()
    assert target["availability"] == "available"
    assert target["locator"] == chunk.locator.model_dump(mode="json")
    assert target["media_kind"] == media_kind
    assert target["mime_type"] == mime_type
    assert target["media_url"].endswith(
        f"/courses/{course.id}/chat/citations/{citation_id}/content"
    )
    assert target["target_chunk_id"] == chunk.id
    assert any(item["is_target"] for item in target["context"])
    assert content.status_code == 200
    assert content.content == payload_path.read_bytes()
    assert content.headers["x-content-type-options"] == "nosniff"
    assert content.headers["cache-control"] == "private, no-store"
    assert str(payload_path) not in response.text


def test_video_range_head_headers_and_startup_fingerprint(
    tmp_path,
    monkeypatch,
) -> None:
    paths = _managed_paths(tmp_path, monkeypatch)
    course = _course()
    source, chunk, video_path = _video_source(
        course=course,
        paths=paths,
        fingerprint=False,
    )
    citation_id = "citation-video"
    _citation(
        course=course,
        source=source,
        chunk=chunk,
        citation_id=citation_id,
    )
    content_url = (
        f"/courses/{course.id}/chat/citations/{citation_id}/content"
    )
    assert get_job_video_sha256(source.origin_id) is None

    with _client() as client:
        report = (
            client.app.state.legacy_video_fingerprint_backfill_report
        )
        target = client.get(
            f"/courses/{course.id}/chat/citations/{citation_id}/target"
        )
        full = client.get(content_url)
        partial = client.get(content_url, headers={"Range": "bytes=2-7"})
        open_ended = client.get(
            content_url,
            headers={"Range": "bytes=8-"},
        )
        suffix = client.get(content_url, headers={"Range": "bytes=-4"})
        invalid = client.get(
            content_url,
            headers={"Range": "bytes=999-1000"},
        )
        head = client.head(content_url)

    assert target.status_code == 200
    assert target.json()["locator"]["start_seconds"] == 2.5
    assert target.json()["media_kind"] == "video"
    assert target.json()["mime_type"] == "video/mp4"
    assert target.headers["cache-control"] == "private, no-store"
    assert full.status_code == 200
    assert full.content == video_path.read_bytes()
    assert full.headers["accept-ranges"] == "bytes"
    assert full.headers["x-content-type-options"] == "nosniff"
    assert partial.status_code == 206
    assert partial.content == video_path.read_bytes()[2:8]
    assert partial.headers["content-range"].startswith("bytes 2-7/")
    assert open_ended.status_code == 206
    assert open_ended.content == video_path.read_bytes()[8:]
    assert open_ended.headers["content-range"].startswith("bytes 8-")
    assert suffix.status_code == 206
    assert suffix.content == video_path.read_bytes()[-4:]
    assert invalid.status_code == 416
    assert head.status_code == 200
    assert head.content == b""
    assert int(head.headers["content-length"]) == video_path.stat().st_size
    assert get_job_video_sha256(source.origin_id) == hashlib.sha256(
        video_path.read_bytes()
    ).hexdigest()
    assert report.scanned == 1
    assert report.backfilled == 1
    assert report.refused == 0
    assert report.failures == ()


def test_citation_read_never_fingerprints_a_post_startup_legacy_video(
    tmp_path,
    monkeypatch,
) -> None:
    paths = _managed_paths(tmp_path, monkeypatch)
    with _client() as client:
        course = _course()
        source, chunk, _ = _video_source(
            course=course,
            paths=paths,
            fingerprint=False,
        )
        citation_id = "citation-post-startup-legacy"
        _citation(
            course=course,
            source=source,
            chunk=chunk,
            citation_id=citation_id,
        )
        target = client.get(
            f"/courses/{course.id}/chat/citations/{citation_id}/target"
        )
        content = client.get(
            f"/courses/{course.id}/chat/citations/{citation_id}/content"
        )

    assert get_job_video_sha256(source.origin_id) is None
    assert target.status_code == 200
    assert target.json()["availability"] == "snapshot_only"
    assert target.json()["reason"] == "legacy_fingerprint_unverified"
    assert content.status_code == 410


def test_startup_backfill_reports_and_refuses_unsafe_legacy_video(
    tmp_path,
    monkeypatch,
) -> None:
    paths = _managed_paths(tmp_path, monkeypatch)
    course = _course()
    safe_source, _, safe_path = _video_source(
        course=course,
        paths=paths,
        job_id="legacy-safe",
        fingerprint=False,
    )
    unsafe_source, unsafe_chunk, unsafe_path = _video_source(
        course=course,
        paths=paths,
        job_id="legacy-unsafe",
        fingerprint=False,
    )
    identity_source, _, _ = _video_source(
        course=course,
        paths=paths,
        job_id="legacy-bad-identity",
        fingerprint=False,
    )
    citation_id = "citation-unsafe-legacy"
    _citation(
        course=course,
        source=unsafe_source,
        chunk=unsafe_chunk,
        citation_id=citation_id,
    )
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET size_bytes = ? WHERE id = ?",
            (
                unsafe_path.stat().st_size + 1,
                unsafe_source.origin_id,
            ),
        )
        conn.execute(
            "UPDATE jobs SET stored_name = ? WHERE id = ?",
            ("../legacy-bad-identity.mp4", identity_source.origin_id),
        )

    with _client() as client:
        report = (
            client.app.state.legacy_video_fingerprint_backfill_report
        )
        unsafe_target = client.get(
            f"/courses/{course.id}/chat/citations/{citation_id}/target"
        )

    assert report.scanned == 3
    assert report.backfilled == 1
    assert report.refused == 2
    assert {
        (failure.job_id, failure.reason)
        for failure in report.failures
    } == {
        (
            unsafe_source.origin_id,
            "file_integrity_mismatch",
        ),
        (
            identity_source.origin_id,
            "file_integrity_mismatch",
        ),
    }
    assert get_job_video_sha256(safe_source.origin_id) == hashlib.sha256(
        safe_path.read_bytes()
    ).hexdigest()
    assert get_job_video_sha256(unsafe_source.origin_id) is None
    assert get_job_video_sha256(identity_source.origin_id) is None
    assert unsafe_target.json()["availability"] == "snapshot_only"
    assert (
        unsafe_target.json()["reason"]
        == "legacy_fingerprint_unverified"
    )


def test_same_size_replacement_with_restored_mtime_is_detected(
    tmp_path,
    monkeypatch,
) -> None:
    paths = _managed_paths(tmp_path, monkeypatch)
    course = _course()
    source, chunk, video_path = _video_source(
        course=course,
        paths=paths,
        payload=b"original-video-bytes",
    )
    citation_id = "citation-same-size-replacement"
    _citation(
        course=course,
        source=source,
        chunk=chunk,
        citation_id=citation_id,
    )
    target_url = (
        f"/courses/{course.id}/chat/citations/{citation_id}/target"
    )
    content_url = (
        f"/courses/{course.id}/chat/citations/{citation_id}/content"
    )

    with _client() as client:
        original = client.get(target_url)
        original_stat = video_path.stat()
        replacement = b"replacement-video!!!"
        assert len(replacement) == original_stat.st_size
        video_path.write_bytes(replacement)
        os.utime(
            video_path,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        changed = client.get(target_url)
        changed_content = client.get(content_url)

    assert original.json()["availability"] == "available"
    assert video_path.stat().st_size == original_stat.st_size
    assert video_path.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert changed.json()["availability"] == "snapshot_only"
    assert changed.json()["reason"] == "file_integrity_mismatch"
    assert changed_content.status_code == 409


def test_content_stream_uses_the_single_verified_open_handle(
    tmp_path,
    monkeypatch,
) -> None:
    paths = _managed_paths(tmp_path, monkeypatch)
    course = _course()
    source, chunk, video_path = _video_source(
        course=course,
        paths=paths,
    )
    citation_id = "citation-single-open-handle"
    _citation(
        course=course,
        source=source,
        chunk=chunk,
        citation_id=citation_id,
    )
    opens: list[Path] = []
    original_open = citation_target_service._open_binary_no_follow

    def recording_open(path: Path):
        opens.append(path)
        return original_open(path)

    monkeypatch.setattr(
        citation_target_service,
        "_open_binary_no_follow",
        recording_open,
    )
    with _client() as client:
        response = client.get(
            f"/courses/{course.id}/chat/citations/{citation_id}/content"
        )

    assert response.status_code == 200
    assert response.content == video_path.read_bytes()
    assert opens == [video_path.resolve()]


def test_file_open_lifecycle_error_is_stable_snapshot_and_conflict(
    tmp_path,
    monkeypatch,
) -> None:
    paths = _managed_paths(tmp_path, monkeypatch)
    course = _course()
    source, chunk, video_path = _video_source(
        course=course,
        paths=paths,
    )
    citation_id = "citation-open-lifecycle"
    _citation(
        course=course,
        source=source,
        chunk=chunk,
        citation_id=citation_id,
    )
    original_open = citation_target_service._open_binary_no_follow

    def failing_open(path: Path):
        if path == video_path.resolve():
            raise PermissionError("simulated lifecycle race")
        return original_open(path)

    monkeypatch.setattr(
        citation_target_service,
        "_open_binary_no_follow",
        failing_open,
    )
    with _client() as client:
        target = client.get(
            f"/courses/{course.id}/chat/citations/{citation_id}/target"
        )
        content = client.get(
            f"/courses/{course.id}/chat/citations/{citation_id}/content"
        )

    assert target.status_code == 200
    assert target.json()["availability"] == "snapshot_only"
    assert target.json()["reason"] == "file_lifecycle_error"
    assert content.status_code == 409


def test_disabled_source_remains_openable_and_survives_restart(
    tmp_path,
    monkeypatch,
) -> None:
    paths = _managed_paths(tmp_path, monkeypatch)
    course = _course()
    source, chunk, _ = _video_source(course=course, paths=paths)
    citation_id = "citation-disabled"
    _citation(
        course=course,
        source=source,
        chunk=chunk,
        citation_id=citation_id,
    )
    assert set_source_enabled(source.id, False)

    with _client() as first:
        first_response = first.get(
            f"/courses/{course.id}/chat/citations/{citation_id}/target"
        )
    with _client() as restarted:
        restarted_response = restarted.get(
            f"/courses/{course.id}/chat/citations/{citation_id}/target"
        )

    assert first_response.status_code == 200
    assert first_response.json()["availability"] == "available"
    assert restarted_response.json()["availability"] == "available"


def test_deleted_or_changed_sources_return_saved_snapshot(
    tmp_path,
    monkeypatch,
) -> None:
    paths = _managed_paths(tmp_path, monkeypatch)
    course = _course()
    source, chunk, _ = _video_source(course=course, paths=paths)
    removed_id = "citation-removed"
    _citation(
        course=course,
        source=source,
        chunk=chunk,
        citation_id=removed_id,
    )
    delete_source_projection(source.id)

    with _client() as client:
        removed = client.get(
            f"/courses/{course.id}/chat/citations/{removed_id}/target"
        )
        removed_content = client.get(
            f"/courses/{course.id}/chat/citations/{removed_id}/content"
        )

    assert removed.status_code == 200
    assert removed.json()["availability"] == "snapshot_only"
    assert removed.json()["reason"] == "source_removed"
    assert removed.json()["quote"] == chunk.text
    assert removed_content.status_code == 410

    source2, chunk2, _ = _video_source(
        course=course,
        paths=paths,
        job_id="changed-job",
    )
    changed_id = "citation-changed"
    _citation(
        course=course,
        source=source2,
        chunk=chunk2,
        citation_id=changed_id,
    )
    changed_chunk = chunk2.model_copy(
        update={
            "text": "The canonical text changed.",
            "text_hash": hash_source_chunk_text(
                "The canonical text changed."
            ),
        }
    )
    replace_source_projection(source2, [changed_chunk])

    with _client() as client:
        changed = client.get(
            f"/courses/{course.id}/chat/citations/{changed_id}/target"
        )
        changed_content = client.get(
            f"/courses/{course.id}/chat/citations/{changed_id}/content"
        )

    assert changed.status_code == 200
    assert changed.json()["availability"] == "snapshot_only"
    assert changed.json()["reason"] == "source_changed"
    assert changed.json()["quote"] == chunk2.text
    assert changed_content.status_code == 409


def test_missing_file_keeps_exact_context_but_not_media(
    tmp_path,
    monkeypatch,
) -> None:
    paths = _managed_paths(tmp_path, monkeypatch)
    course = _course()
    asset_id = "missing-pdf"
    source, chunk, path = _asset_source(
        course=course,
        paths=paths,
        asset_id=asset_id,
        source_type="pdf",
        extension=".pdf",
        locator=PdfPageLocator(asset_id=asset_id, page_number=2),
        text="The persisted page remains available as extracted text.",
    )
    citation_id = "citation-missing-file"
    _citation(
        course=course,
        source=source,
        chunk=chunk,
        citation_id=citation_id,
    )
    path.unlink()

    with _client() as client:
        response = client.get(
            f"/courses/{course.id}/chat/citations/{citation_id}/target"
        )

    assert response.status_code == 200
    target = response.json()
    assert target["availability"] == "snapshot_only"
    assert target["reason"] == "file_missing"
    assert target["media_url"] is None
    assert target["target_chunk_id"] == chunk.id
    assert target["context"][0]["text"] == chunk.text
    assert target["context"][0]["is_target"] is True


def test_course_isolation_uses_indistinguishable_404(
    tmp_path,
    monkeypatch,
) -> None:
    paths = _managed_paths(tmp_path, monkeypatch)
    course = _course("Owner")
    other = _course("Other")
    source, chunk, _ = _video_source(course=course, paths=paths)
    citation_id = "citation-private"
    _citation(
        course=course,
        source=source,
        chunk=chunk,
        citation_id=citation_id,
    )

    with _client() as client:
        wrong_course = client.get(
            f"/courses/{other.id}/chat/citations/{citation_id}/target"
        )
        missing = client.get(
            f"/courses/{other.id}/chat/citations/not-a-citation/target"
        )

    assert wrong_course.status_code == 404
    assert missing.status_code == 404
    assert wrong_course.json() == missing.json() == {
        "detail": "Citation not found."
    }
    assert wrong_course.headers["cache-control"] == "private, no-store"
    assert missing.headers["cache-control"] == "private, no-store"


def test_outside_root_and_hash_mismatch_never_expose_paths(
    tmp_path,
    monkeypatch,
) -> None:
    paths = _managed_paths(tmp_path, monkeypatch)
    course = _course()
    outside = tmp_path / "private.pdf"
    outside.write_bytes(b"private bytes")
    asset_id = "escaped"
    source, chunk, _ = _asset_source(
        course=course,
        paths=paths,
        asset_id=asset_id,
        source_type="pdf",
        extension=".pdf",
        locator=PdfPageLocator(asset_id=asset_id, page_number=1),
        text="Escaped source text.",
        content=outside.read_bytes(),
        stored_path=outside,
    )
    citation_id = "citation-escaped"
    _citation(
        course=course,
        source=source,
        chunk=chunk,
        citation_id=citation_id,
    )

    with _client() as client:
        escaped = client.get(
            f"/courses/{course.id}/chat/citations/{citation_id}/target"
        )
        escaped_content = client.get(
            f"/courses/{course.id}/chat/citations/{citation_id}/content"
        )

    assert escaped.json()["availability"] == "snapshot_only"
    assert escaped.json()["reason"] == "file_missing"
    assert str(outside) not in escaped.text
    assert str(outside) not in escaped_content.text

    mismatch_id = "hash-mismatch"
    source2, chunk2, path2 = _asset_source(
        course=course,
        paths=paths,
        asset_id=mismatch_id,
        source_type="pdf",
        extension=".pdf",
        locator=PdfPageLocator(asset_id=mismatch_id, page_number=1),
        text="Hash protected source.",
    )
    citation2 = "citation-hash-mismatch"
    _citation(
        course=course,
        source=source2,
        chunk=chunk2,
        citation_id=citation2,
    )
    path2.write_bytes(b"different bytes with a new length")
    os.utime(path2, None)

    with _client() as client:
        mismatch = client.get(
            f"/courses/{course.id}/chat/citations/{citation2}/target"
        )
        mismatch_content = client.get(
            f"/courses/{course.id}/chat/citations/{citation2}/content"
        )

    assert mismatch.json()["reason"] == "file_integrity_mismatch"
    assert mismatch_content.status_code == 409
    assert str(path2) not in mismatch.text
    assert str(path2) not in mismatch_content.text


def test_symlink_escape_is_rejected_when_supported(
    tmp_path,
    monkeypatch,
) -> None:
    paths = _managed_paths(tmp_path, monkeypatch)
    course = _course()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside")
    link = paths.source_dir / course.id / "linked.pdf"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("File symlinks are not available on this Windows host.")
    asset_id = "linked"
    source, chunk, _ = _asset_source(
        course=course,
        paths=paths,
        asset_id=asset_id,
        source_type="pdf",
        extension=".pdf",
        locator=PdfPageLocator(asset_id=asset_id, page_number=1),
        text="Symlink source.",
        content=outside.read_bytes(),
        stored_path=link,
    )
    citation_id = "citation-symlink"
    _citation(
        course=course,
        source=source,
        chunk=chunk,
        citation_id=citation_id,
    )

    with _client() as client:
        response = client.get(
            f"/courses/{course.id}/chat/citations/{citation_id}/target"
        )

    assert response.json()["availability"] == "snapshot_only"
    assert response.json()["reason"] == "file_missing"
    assert str(outside) not in response.text


def test_unknown_historical_locator_does_not_break_conversation(
    tmp_path,
    monkeypatch,
) -> None:
    paths = _managed_paths(tmp_path, monkeypatch)
    course = _course()
    asset_id = "future-locator"
    source, chunk, _ = _asset_source(
        course=course,
        paths=paths,
        asset_id=asset_id,
        source_type="pdf",
        extension=".pdf",
        locator=PdfPageLocator(asset_id=asset_id, page_number=1),
        text="Historical citation.",
    )
    citation_id = "citation-future"
    conversation_id = _citation(
        course=course,
        source=source,
        chunk=chunk,
        citation_id=citation_id,
    )
    future_locator = {
        "schema_version": 99,
        "kind": "future_anchor",
        "opaque": "kept",
    }
    with connect() as conn:
        conn.execute(
            "UPDATE chat_citations SET locator_json = ? WHERE id = ?",
            (json.dumps(future_locator), citation_id),
        )

    detail = get_conversation_detail(conversation_id)
    assert detail is not None
    assert detail.messages[-1].citations[0].locator == future_locator
    with _client() as client:
        conversation_response = client.get(
            f"/chat/conversations/{conversation_id}"
        )
        target_response = client.get(
            f"/courses/{course.id}/chat/citations/{citation_id}/target"
        )

    assert conversation_response.status_code == 200
    assert target_response.status_code == 200
    target = target_response.json()
    assert target["availability"] == "snapshot_only"
    assert target["reason"] == "unsupported_locator"
    assert target["locator"] == future_locator


def test_non_loopback_clients_cannot_resolve_or_read_content(
    tmp_path,
    monkeypatch,
) -> None:
    paths = _managed_paths(tmp_path, monkeypatch)
    course = _course()
    source, chunk, _ = _video_source(course=course, paths=paths)
    citation_id = "citation-loopback"
    _citation(
        course=course,
        source=source,
        chunk=chunk,
        citation_id=citation_id,
    )
    remote_client = TestClient(
        main.app,
        client=("192.0.2.40", 50000),
    )
    target_path = (
        f"/courses/{course.id}/chat/citations/{citation_id}/target"
    )
    content_path = (
        f"/courses/{course.id}/chat/citations/{citation_id}/content"
    )

    with remote_client:
        target = remote_client.get(target_path)
        content = remote_client.get(content_path)
    mapped_client = TestClient(
        main.app,
        client=("::ffff:127.0.0.1", 50001),
    )
    with mapped_client:
        mapped = mapped_client.get(target_path)

    assert target.status_code == 403
    assert content.status_code == 403
    assert mapped.status_code == 200


def test_video_time_asset_has_explicit_snapshot_fallback(
    tmp_path,
    monkeypatch,
) -> None:
    paths = _managed_paths(tmp_path, monkeypatch)
    course = _course()
    asset_id = "audio-asset"
    source, chunk, path = _asset_source(
        course=course,
        paths=paths,
        asset_id=asset_id,
        source_type="audio",
        extension=".wav",
        locator=VideoTimeLocator(
            asset_id=asset_id,
            start_seconds=1,
            end_seconds=2,
        ),
        text="Audio transcript citation.",
        content=b"not-a-real-wave-but-served-as-managed-bytes",
    )
    citation_id = "citation-audio-asset"
    _citation(
        course=course,
        source=source,
        chunk=chunk,
        citation_id=citation_id,
    )

    with _client() as client:
        available = client.get(
            f"/courses/{course.id}/chat/citations/{citation_id}/target"
        )
    assert available.json()["availability"] == "available"
    assert available.json()["media_kind"] == "audio"
    path.unlink()
    with _client() as client:
        unavailable = client.get(
            f"/courses/{course.id}/chat/citations/{citation_id}/target"
        )
    assert unavailable.json()["availability"] == "snapshot_only"
    assert unavailable.json()["reason"] == "asset_media_unavailable"


def test_course_trash_hides_citation_until_original_scope_is_restored(
    tmp_path,
    monkeypatch,
) -> None:
    paths = _managed_paths(tmp_path, monkeypatch)
    course = _course("Temporary course")
    asset_id = "moved-course-pdf"
    source, chunk, original_path = _asset_source(
        course=course,
        paths=paths,
        asset_id=asset_id,
        source_type="pdf",
        extension=".pdf",
        locator=PdfPageLocator(asset_id=asset_id, page_number=7),
        text="The citation follows its course move.",
    )
    citation_id = "citation-course-move"
    _citation(
        course=course,
        source=source,
        chunk=chunk,
        citation_id=citation_id,
    )

    delete_video_course(course.id)

    with _client() as client:
        old_course = client.get(
            f"/courses/{course.id}/chat/citations/{citation_id}/target"
        )
        default_course = client.get(
            f"/courses/{DEFAULT_COURSE_ID}/chat/citations/"
            f"{citation_id}/target"
        )

    assert old_course.status_code == 404
    assert default_course.status_code == 404
    assert original_path.is_file()
    assert original_path.parent.name == course.id

    restore_video_course(course.id)
    with _client() as client:
        restored = client.get(
            f"/courses/{course.id}/chat/citations/{citation_id}/target"
        )
    assert restored.status_code == 200
    assert restored.json()["availability"] == "available"
    assert restored.json()["locator"]["page_number"] == 7


def test_managed_root_relocation_uses_stable_names_and_hashes(
    tmp_path,
    monkeypatch,
) -> None:
    paths = _managed_paths(tmp_path, monkeypatch)
    course = _course()
    asset_id = "relocated-pdf"
    source, chunk, original_asset_path = _asset_source(
        course=course,
        paths=paths,
        asset_id=asset_id,
        source_type="pdf",
        extension=".pdf",
        locator=PdfPageLocator(asset_id=asset_id, page_number=1),
        text="Relocated document.",
    )
    asset_citation = "citation-relocated-asset"
    _citation(
        course=course,
        source=source,
        chunk=chunk,
        citation_id=asset_citation,
    )
    relocated_dir = paths.source_dir / "relocated"
    relocated_dir.mkdir()
    relocated_asset_path = relocated_dir / original_asset_path.name
    original_asset_path.replace(relocated_asset_path)

    video_source, video_chunk, video_path = _video_source(
        course=course,
        paths=paths,
        job_id="relocated-video",
    )
    video_citation = "citation-relocated-video"
    _citation(
        course=course,
        source=video_source,
        chunk=video_chunk,
        citation_id=video_citation,
    )
    stale_root = tmp_path / "old-data-root"
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET video_path = ? WHERE id = ?",
            (
                str(stale_root / video_path.name),
                video_source.origin_id,
            ),
        )

    with _client() as client:
        asset_target = client.get(
            f"/courses/{course.id}/chat/citations/"
            f"{asset_citation}/target"
        )
        video_target = client.get(
            f"/courses/{course.id}/chat/citations/"
            f"{video_citation}/target"
        )

    assert asset_target.json()["availability"] == "available"
    assert video_target.json()["availability"] == "available"
    assert str(relocated_asset_path) not in asset_target.text
    assert str(video_path) not in video_target.text


def test_v4_video_fingerprint_migrates_v3_database_and_backup(
    tmp_path,
) -> None:
    db_path = tmp_path / "legacy-v3.db"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE jobs (id TEXT PRIMARY KEY, video_path TEXT NOT NULL)"
        )
        conn.executescript(
            """
            CREATE TABLE sources (
                id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL
            );
            CREATE TABLE source_chunks (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                chunk_type TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                text TEXT NOT NULL,
                text_hash TEXT NOT NULL,
                locator_json TEXT NOT NULL,
                chunker_version TEXT NOT NULL,
                is_active INTEGER NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        for migration in MIGRATIONS[:3]:
            conn.execute(
                """
                INSERT INTO schema_migrations (version, name, applied_at)
                VALUES (?, ?, ?)
                """,
                (migration.version, migration.name, utc_now().isoformat()),
            )
        conn.commit()

    backup = prepare_migration_backup(db_path)
    assert backup is not None
    assert ".pre-migration-v11-" in backup.name
    with sqlite3.connect(backup) as conn:
        backup_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(jobs)")
        }
    assert "video_sha256" not in backup_columns

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        assert apply_migrations(conn) == [4, 5, 6, 7, 8, 9, 10, 11]
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(jobs)")
        }
        assert "video_sha256" in columns
        assert apply_migrations(conn) == []


def test_fresh_schema_has_video_fingerprint_column() -> None:
    with connect() as conn:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(jobs)")
        }
        versions = {
            int(row["version"])
            for row in conn.execute("SELECT version FROM schema_migrations")
        }
    assert "video_sha256" in columns
    assert 4 in versions
    assert 5 in versions
