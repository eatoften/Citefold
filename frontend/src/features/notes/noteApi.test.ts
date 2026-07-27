import { describe, expect, it, vi } from 'vitest'
import {
  createNotebookNote,
  deleteNotebookNote,
  getNotebookNote,
  listNotebookNotes,
  NotebookNoteApiError,
  publishNotebookNoteAsSource,
  saveChatAnswerAsNote,
  updateNotebookNote,
} from './noteApi'
import type { NotebookNote } from './noteTypes'

const API_BASE_URL = 'http://api.test/'
const TIMESTAMP = '2026-07-28T10:00:00Z'

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function note(overrides: Partial<NotebookNote> = {}): NotebookNote {
  return {
    id: 'note/one',
    course_id: 'course one',
    title: 'Grounded note',
    body_markdown: 'A durable note.',
    revision: 2,
    origin_type: 'free',
    origin_snapshot: { origin_type: 'free' },
    published_snapshot_id: null,
    published_revision: null,
    is_source_outdated: false,
    created_at: TIMESTAMP,
    updated_at: TIMESTAMP,
    ...overrides,
  }
}

describe('noteApi', () => {
  it('uses course-scoped list, create, and detail paths', async () => {
    const saved = note()
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse(saved, 201))
      .mockResolvedValueOnce(jsonResponse(saved))
    vi.stubGlobal('fetch', fetchMock)

    await listNotebookNotes(API_BASE_URL, 'course one')
    await createNotebookNote(API_BASE_URL, 'course one', {
      title: 'Grounded note',
      body_markdown: 'A durable note.',
    })
    await getNotebookNote(API_BASE_URL, 'course one', 'note/one')

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      'http://api.test/courses/course%20one/notes?limit=1000&offset=0',
    )
    expect(fetchMock.mock.calls[1]).toEqual([
      'http://api.test/courses/course%20one/notes',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          title: 'Grounded note',
          body_markdown: 'A durable note.',
        }),
      }),
    ])
    expect(fetchMock.mock.calls[2]?.[0]).toBe(
      'http://api.test/courses/course%20one/notes/note%2Fone',
    )
  })

  it('sends revision fences for update, delete, and publication', async () => {
    const saved = note({ revision: 3 })
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(saved))
      .mockResolvedValueOnce(jsonResponse({ id: 'trash-1' }))
      .mockResolvedValueOnce(
        jsonResponse({
          note: saved,
          snapshot: {
            id: 'snapshot-1',
            note_id: saved.id,
            course_id: saved.course_id,
            note_revision: saved.revision,
            title: saved.title,
            body_markdown: saved.body_markdown,
            content_hash: 'hash',
            created_at: TIMESTAMP,
          },
          source: { id: `note:${saved.id}` },
          replayed: false,
        }),
      )
    vi.stubGlobal('fetch', fetchMock)

    await updateNotebookNote(
      API_BASE_URL,
      'course one',
      'note/one',
      {
        body_markdown: 'Changed',
        expected_revision: 2,
      },
    )
    await deleteNotebookNote(
      API_BASE_URL,
      'course one',
      'note/one',
      3,
    )
    await publishNotebookNoteAsSource(
      API_BASE_URL,
      'course one',
      'note/one',
      3,
    )

    expect(fetchMock.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({
          body_markdown: 'Changed',
          expected_revision: 2,
        }),
      }),
    )
    expect(fetchMock.mock.calls[1]).toEqual([
      'http://api.test/courses/course%20one/notes/note%2Fone?expected_revision=3',
      expect.objectContaining({ method: 'DELETE' }),
    ])
    expect(fetchMock.mock.calls[2]?.[1]).toEqual(
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ expected_revision: 3 }),
      }),
    )
  })

  it('captures a Chat message idempotently through the note boundary', async () => {
    const saved = note({
      origin_type: 'chat_answer',
      origin_snapshot: {
        origin_type: 'chat_answer',
        conversation_id: 'conversation-1',
        message_id: 'message/one',
        answer_text: 'Grounded answer.',
        provider: 'local',
        model: 'model',
        citations: [],
      },
    })
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse(saved, 201))
    vi.stubGlobal('fetch', fetchMock)

    await saveChatAnswerAsNote(
      API_BASE_URL,
      'course one',
      'message/one',
    )

    expect(fetchMock).toHaveBeenCalledWith(
      'http://api.test/courses/course%20one/notes/from-chat/message%2Fone',
      expect.objectContaining({
        method: 'POST',
        body: '{}',
      }),
    )
  })

  it('keeps the current note attached to a revision conflict', async () => {
    const current = note({ revision: 4 })
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockResolvedValue(
        jsonResponse(
          {
            detail: {
              message: 'The note changed.',
              current,
            },
          },
          409,
        ),
      ),
    )

    await expect(
      updateNotebookNote(
        API_BASE_URL,
        current.course_id,
        current.id,
        {
          body_markdown: 'Stale edit',
          expected_revision: 2,
        },
      ),
    ).rejects.toEqual(
      expect.objectContaining<Partial<NotebookNoteApiError>>({
        status: 409,
        message: 'The note changed.',
        current,
      }),
    )
  })

  it('accepts an empty successful delete body', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(new Response(null, { status: 204 })),
    )

    await expect(
      deleteNotebookNote(
        API_BASE_URL,
        'course one',
        'note/one',
        2,
      ),
    ).resolves.toBeUndefined()
  })
})
