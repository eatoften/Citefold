from __future__ import annotations

from .course_source import (
    CourseSource,
    CourseSourceChunk,
    NotebookNoteSectionLocator,
    hash_source_chunk_text,
    source_chunk_id,
    source_id_for_note,
)
from .notebook_note import NotebookNote, NotebookNoteSourceSnapshot


NOTE_CHUNKER_VERSION = "notebook-note-markdown-v1"
MAX_NOTE_SECTION_CHARACTERS = 4000


def split_note_markdown(
    body_markdown: str,
    *,
    max_characters: int = MAX_NOTE_SECTION_CHARACTERS,
) -> list[str]:
    """Split Markdown on paragraph boundaries with a deterministic hard limit."""

    if max_characters < 1:
        raise ValueError("Note section size must be positive.")
    normalized = body_markdown.replace("\r\n", "\n").replace("\r", "\n")
    blocks = [part.strip() for part in normalized.split("\n\n") if part.strip()]
    sections: list[str] = []
    current = ""
    for block in blocks:
        pieces = [
            block[start : start + max_characters]
            for start in range(0, len(block), max_characters)
        ]
        for piece in pieces:
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) <= max_characters:
                current = candidate
                continue
            if current:
                sections.append(current)
            current = piece
    if current:
        sections.append(current)
    return sections


def build_note_source_projection(
    note: NotebookNote,
    snapshot: NotebookNoteSourceSnapshot,
) -> tuple[CourseSource, list[CourseSourceChunk]]:
    if snapshot.note_id != note.id or snapshot.course_id != note.course_id:
        raise ValueError("Note Source snapshot belongs to a different note.")
    if snapshot.note_revision > note.revision:
        raise ValueError("Note Source snapshot revision is invalid.")

    source_id = source_id_for_note(note.id)
    sections = split_note_markdown(snapshot.body_markdown)
    if not sections:
        raise ValueError("A blank note cannot be published as a Source.")
    source = CourseSource(
        id=source_id,
        course_id=note.course_id,
        origin_type="notebook_note",
        origin_id=note.id,
        source_type="text",
        title=snapshot.title,
        content_status="ready",
        size_bytes=len(snapshot.body_markdown.encode("utf-8")),
        mime_type="text/markdown",
        metadata={
            "note_id": note.id,
            "note_revision": snapshot.note_revision,
            "snapshot_id": snapshot.id,
            "content_hash": snapshot.content_hash,
        },
        created_at=note.created_at,
        updated_at=snapshot.created_at,
    )
    chunks = [
        CourseSourceChunk(
            id=source_chunk_id(
                "notebook_note_snapshot",
                f"{snapshot.id}:{section_number}",
            ),
            source_id=source_id,
            origin_type="notebook_note_snapshot",
            origin_id=f"{snapshot.id}:{section_number}",
            chunk_type="text",
            ordinal=section_number - 1,
            text=section,
            text_hash=hash_source_chunk_text(section),
            locator=NotebookNoteSectionLocator(
                note_id=note.id,
                snapshot_id=snapshot.id,
                section_number=section_number,
                metadata={
                    "note_revision": snapshot.note_revision,
                    "content_hash": snapshot.content_hash,
                },
            ),
            chunker_version=NOTE_CHUNKER_VERSION,
            created_at=snapshot.created_at,
            updated_at=snapshot.created_at,
        )
        for section_number, section in enumerate(sections, start=1)
    ]
    return source, chunks
