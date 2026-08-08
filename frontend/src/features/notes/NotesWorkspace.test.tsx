import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest'
import type { ChatCitation } from '../chat'
import type { CourseSource } from '../sources/sourceTypes'
import { NotebookNoteApiError } from './noteApi'
import type {
  NotebookNote,
  NotebookNotePromotion,
  NotebookNoteSummary,
} from './noteTypes'
import { NotesWorkspace } from './NotesWorkspace'

const noteApi = vi.hoisted(() => ({
  listNotebookNotes: vi.fn(),
  getNotebookNote: vi.fn(),
  createNotebookNote: vi.fn(),
  updateNotebookNote: vi.fn(),
  deleteNotebookNote: vi.fn(),
  publishNotebookNoteAsSource: vi.fn(),
}))

const reliability = vi.hoisted(() => ({
  clearDraft: vi.fn(async () => undefined),
  announceTrashCreated: vi.fn(),
  useAutosavedDraft: vi.fn(),
  useInternalNavigationGuard: vi.fn(),
}))

vi.mock('./noteApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./noteApi')>()
  return {
    ...actual,
    listNotebookNotes: noteApi.listNotebookNotes,
    getNotebookNote: noteApi.getNotebookNote,
    createNotebookNote: noteApi.createNotebookNote,
    updateNotebookNote: noteApi.updateNotebookNote,
    deleteNotebookNote: noteApi.deleteNotebookNote,
    publishNotebookNoteAsSource:
      noteApi.publishNotebookNoteAsSource,
  }
})

vi.mock('../reliability', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../reliability')>()
  return {
    ...actual,
    announceTrashCreated: reliability.announceTrashCreated,
    useAutosavedDraft: reliability.useAutosavedDraft,
    useInternalNavigationGuard:
      reliability.useInternalNavigationGuard,
  }
})

const TIMESTAMP = '2026-07-28T10:00:00Z'
const API_BASE_URL = 'http://api.test'

function note(
  id = 'note-1',
  overrides: Partial<NotebookNote> = {},
): NotebookNote {
  return {
    id,
    course_id: 'course-1',
    title: `Title ${id}`,
    body_markdown: `Body ${id}`,
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

function summary(value: NotebookNote): NotebookNoteSummary {
  return {
    id: value.id,
    course_id: value.course_id,
    title: value.title,
    body_preview: value.body_markdown,
    revision: value.revision,
    origin_type: value.origin_type,
    citation_count:
      value.origin_snapshot.origin_type === 'chat_answer'
        ? value.origin_snapshot.citations.length
        : 0,
    published_snapshot_id: value.published_snapshot_id,
    published_revision: value.published_revision,
    is_source_outdated: value.is_source_outdated,
    created_at: value.created_at,
    updated_at: value.updated_at,
  }
}

function sourceFor(value: NotebookNote): CourseSource {
  return {
    id: `note:${value.id}`,
    course_id: value.course_id,
    origin_type: 'notebook_note',
    origin_id: value.id,
    source_type: 'text',
    title: value.title,
    content_status: 'ready',
    index_status: 'not_indexed',
    index_model: null,
    index_dimension: null,
    enabled: true,
    chunk_count: 1,
    indexed_chunk_count: 0,
    projection_generation_id: 'generation-1',
    projection_manifest_hash: 'a'.repeat(64),
    size_bytes: null,
    mime_type: 'text/markdown',
    metadata: {},
    error_message: null,
    index_error: null,
    created_at: TIMESTAMP,
    updated_at: TIMESTAMP,
    indexed_at: null,
  }
}

function promotion(value: NotebookNote): NotebookNotePromotion {
  return {
    note: {
      ...value,
      published_snapshot_id: 'snapshot-1',
      published_revision: value.revision,
      is_source_outdated: false,
    },
    snapshot: {
      id: 'snapshot-1',
      note_id: value.id,
      course_id: value.course_id,
      note_revision: value.revision,
      title: value.title,
      body_markdown: value.body_markdown,
      content_hash: 'hash',
      created_at: TIMESTAMP,
    },
    source: sourceFor(value),
    replayed: false,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => {
    resolve = next
  })
  return { promise, resolve }
}

describe('NotesWorkspace', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    reliability.useAutosavedDraft.mockReturnValue({
      state: 'clean',
      message: '',
      restored: false,
      recoveryConflict: null,
      restoreRecoveryDraft: vi.fn(),
      discardRecoveryDraft: vi.fn(async () => undefined),
      clearDraft: reliability.clearDraft,
    })
    reliability.useInternalNavigationGuard.mockReturnValue(
      () => true,
    )
    noteApi.listNotebookNotes.mockResolvedValue([])
    noteApi.getNotebookNote.mockResolvedValue(note())
    noteApi.createNotebookNote.mockResolvedValue(note())
    noteApi.updateNotebookNote.mockResolvedValue(
      note('note-1', { revision: 3 }),
    )
    noteApi.deleteNotebookNote.mockResolvedValue(undefined)
    noteApi.publishNotebookNoteAsSource.mockResolvedValue(
      promotion(note()),
    )
  })

  it('creates a free note and replaces the draft route with its durable id', async () => {
    const user = userEvent.setup()
    const saved = note('created-note', {
      revision: 1,
      title: 'Retrieval idea',
      body_markdown: 'Use explicit evidence promotion.',
    })
    noteApi.createNotebookNote.mockResolvedValue(saved)
    const onNoteRouteChange = vi.fn()

    render(
      <NotesWorkspace
        apiBaseUrl={API_BASE_URL}
        courseId="course-1"
        onNoteRouteChange={onNoteRouteChange}
      />,
    )

    await user.click(
      screen.getAllByRole('button', { name: 'New note' })[0],
    )
    await user.type(screen.getByLabelText('Title'), saved.title)
    await user.type(
      screen.getByLabelText('Note'),
      saved.body_markdown,
    )
    await user.click(
      screen.getByRole('button', { name: 'Create note' }),
    )

    await waitFor(() =>
      expect(noteApi.createNotebookNote).toHaveBeenCalledWith(
        API_BASE_URL,
        'course-1',
        {
          title: saved.title,
          body_markdown: saved.body_markdown,
        },
        expect.any(AbortSignal),
      ),
    )
    expect(reliability.clearDraft).toHaveBeenCalled()
    expect(reliability.useAutosavedDraft).toHaveBeenCalledWith(
      expect.objectContaining({
        draftId: 'notebook-note:new:course-1',
        courseId: 'course-1',
        draftType: 'notebook_note',
        entityId: null,
      }),
    )
    expect(onNoteRouteChange).toHaveBeenLastCalledWith(
      saved.id,
      'replace',
    )
  })

  it('keeps a created note when an older list request finishes later', async () => {
    const user = userEvent.setup()
    const lateList = deferred<NotebookNoteSummary[]>()
    const saved = note('created-note', {
      revision: 1,
      title: 'Created during loading',
      body_markdown: 'This note must survive the older list response.',
    })
    const unrelated = note('existing-note', {
      title: 'Existing course note',
    })
    noteApi.listNotebookNotes
      .mockReturnValueOnce(lateList.promise)
      .mockResolvedValueOnce([
        summary(saved),
        summary(unrelated),
      ])
    noteApi.createNotebookNote.mockResolvedValue(saved)

    render(
      <NotesWorkspace
        apiBaseUrl={API_BASE_URL}
        courseId="course-1"
        onNoteRouteChange={vi.fn()}
      />,
    )

    await user.click(
      screen.getAllByRole('button', { name: 'New note' })[0],
    )
    await user.type(screen.getByLabelText('Title'), saved.title)
    await user.type(
      screen.getByLabelText('Note'),
      saved.body_markdown,
    )
    await user.click(
      screen.getByRole('button', { name: 'Create note' }),
    )
    expect(await screen.findByText(saved.title)).toBeInTheDocument()
    expect(
      await screen.findByText(unrelated.title),
    ).toBeInTheDocument()

    await act(async () => {
      lateList.resolve([])
      await Promise.resolve()
    })

    expect(screen.getByText(saved.title)).toBeInTheDocument()
    expect(screen.getByText(unrelated.title)).toBeInTheDocument()
  })

  it('normalizes title whitespace before deciding whether a note is dirty', async () => {
    const user = userEvent.setup()
    const opened = note()
    noteApi.listNotebookNotes.mockResolvedValue([summary(opened)])
    noteApi.getNotebookNote.mockResolvedValue(opened)

    render(
      <NotesWorkspace
        apiBaseUrl={API_BASE_URL}
        courseId="course-1"
        initialNoteId={opened.id}
        onNoteRouteChange={vi.fn()}
      />,
    )

    const title = await screen.findByLabelText('Title')
    await user.clear(title)
    await user.type(title, '  Title   note-1  ')

    expect(
      screen.getByRole('button', { name: 'Save changes' }),
    ).toBeDisabled()
    expect(noteApi.updateNotebookNote).not.toHaveBeenCalled()
  })

  it('keeps the current note open when an unprotected draft cannot leave', async () => {
    const user = userEvent.setup()
    const first = note('note-1')
    const second = note('note-2', {
      title: 'Second protected note',
    })
    const stay = vi.fn().mockReturnValue(false)
    reliability.useInternalNavigationGuard.mockReturnValue(stay)
    noteApi.listNotebookNotes.mockResolvedValue([
      summary(first),
      summary(second),
    ])
    noteApi.getNotebookNote.mockResolvedValue(first)
    const onNoteRouteChange = vi.fn()

    render(
      <NotesWorkspace
        apiBaseUrl={API_BASE_URL}
        courseId="course-1"
        initialNoteId={first.id}
        onNoteRouteChange={onNoteRouteChange}
      />,
    )

    expect(await screen.findByLabelText('Title')).toHaveValue(
      first.title,
    )
    await user.click(
      screen.getByRole('button', {
        name: /Second protected note/,
      }),
    )

    expect(stay).toHaveBeenCalledTimes(1)
    expect(onNoteRouteChange).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Title')).toHaveValue(first.title)
  })

  it('locks note mutations until an older recovery draft is resolved', async () => {
    const user = userEvent.setup()
    const opened = note()
    const restoreRecoveryDraft = vi.fn()
    const discardRecoveryDraft = vi.fn(async () => undefined)
    reliability.useAutosavedDraft.mockReturnValue({
      state: 'conflict',
      message: 'Review another editor’s changes',
      restored: false,
      recoveryConflict: {
        title: 'Older draft',
        body_markdown: 'Older unsaved body',
      },
      restoreRecoveryDraft,
      discardRecoveryDraft,
      clearDraft: reliability.clearDraft,
    })
    noteApi.listNotebookNotes.mockResolvedValue([summary(opened)])
    noteApi.getNotebookNote.mockResolvedValue(opened)

    render(
      <NotesWorkspace
        apiBaseUrl={API_BASE_URL}
        courseId="course-1"
        initialNoteId={opened.id}
        onNoteRouteChange={vi.fn()}
      />,
    )

    expect(await screen.findByLabelText('Title')).toBeDisabled()
    expect(screen.getByLabelText('Note')).toBeDisabled()
    expect(
      screen.getByRole('button', { name: 'Save changes' }),
    ).toBeDisabled()
    expect(
      screen.getByRole('button', { name: 'Publish as source' }),
    ).toBeDisabled()
    expect(
      screen.getByRole('button', { name: 'Move to Trash' }),
    ).toBeDisabled()

    const restoreButton = screen.getByRole('button', {
      name: 'Restore older draft',
    })
    const discardButton = screen.getByRole('button', {
      name: 'Discard',
    })
    expect(restoreButton).toBeEnabled()
    expect(discardButton).toBeEnabled()
    await user.click(restoreButton)
    await user.click(discardButton)
    expect(restoreRecoveryDraft).toHaveBeenCalledTimes(1)
    expect(discardRecoveryDraft).toHaveBeenCalledTimes(1)
  })

  it('keeps a stale edit until the user explicitly loads the server revision', async () => {
    const user = userEvent.setup()
    const opened = note()
    const current = note('note-1', {
      revision: 3,
      title: 'Server title',
      body_markdown: 'Server body',
    })
    noteApi.listNotebookNotes.mockResolvedValue([summary(opened)])
    noteApi.getNotebookNote.mockResolvedValue(opened)
    noteApi.updateNotebookNote.mockRejectedValue(
      new NotebookNoteApiError(
        'The note changed.',
        409,
        current,
      ),
    )

    render(
      <NotesWorkspace
        apiBaseUrl={API_BASE_URL}
        courseId="course-1"
        initialNoteId="note-1"
        onNoteRouteChange={vi.fn()}
      />,
    )

    const body = await screen.findByLabelText('Note')
    expect(screen.getByLabelText('Title')).toHaveAttribute(
      'maxlength',
      '200',
    )
    expect(reliability.useAutosavedDraft).toHaveBeenCalledWith(
      expect.objectContaining({
        draftId: 'notebook-note:note-1',
        courseId: 'course-1',
        draftType: 'notebook_note',
        entityId: 'note-1',
      }),
    )
    await user.clear(body)
    await user.type(body, 'My protected edit')
    await user.click(
      screen.getByRole('button', { name: 'Save changes' }),
    )

    expect(
      await screen.findByText(
        'This note changed after you opened it. Your draft is still safe.',
      ),
    ).toBeInTheDocument()
    expect(body).toHaveValue('My protected edit')

    await user.click(
      screen.getByRole('button', { name: 'Load saved revision' }),
    )
    await waitFor(() =>
      expect(screen.getByLabelText('Note')).toHaveValue('Server body'),
    )
    expect(reliability.clearDraft).toHaveBeenCalled()
  })

  it('publishes one exact revision and opens its stable Source id', async () => {
    const user = userEvent.setup()
    const opened = note()
    const published = promotion(opened)
    noteApi.listNotebookNotes.mockResolvedValue([summary(opened)])
    noteApi.getNotebookNote.mockResolvedValue(opened)
    noteApi.publishNotebookNoteAsSource.mockResolvedValue(published)
    const onGoToSources = vi.fn()

    render(
      <NotesWorkspace
        apiBaseUrl={API_BASE_URL}
        courseId="course-1"
        initialNoteId={opened.id}
        onNoteRouteChange={vi.fn()}
        onGoToSources={onGoToSources}
      />,
    )

    await user.click(
      await screen.findByRole('button', {
        name: 'Publish as source',
      }),
    )

    await waitFor(() =>
      expect(
        noteApi.publishNotebookNoteAsSource,
      ).toHaveBeenCalledWith(
        API_BASE_URL,
        opened.course_id,
        opened.id,
        opened.revision,
        expect.any(AbortSignal),
      ),
    )
    expect(
      screen.getByRole('button', { name: 'Published' }),
    ).toBeDisabled()

    await user.click(
      screen.getByRole('button', { name: 'Open Sources' }),
    )
    expect(onGoToSources).toHaveBeenCalledWith(`note:${opened.id}`)
  })

  it('rejects a publication response outside the active note scope', async () => {
    const user = userEvent.setup()
    const opened = note()
    const wrong = promotion(
      note('other-note', {
        course_id: 'course-2',
      }),
    )
    noteApi.listNotebookNotes.mockResolvedValue([summary(opened)])
    noteApi.getNotebookNote.mockResolvedValue(opened)
    noteApi.publishNotebookNoteAsSource.mockResolvedValue(wrong)

    render(
      <NotesWorkspace
        apiBaseUrl={API_BASE_URL}
        courseId="course-1"
        initialNoteId={opened.id}
        onNoteRouteChange={vi.fn()}
      />,
    )

    await user.click(
      await screen.findByRole('button', {
        name: 'Publish as source',
      }),
    )

    expect(
      await screen.findByText(
        'The server returned a publication outside the active note scope.',
      ),
    ).toBeInTheDocument()
  })

  it('keeps immutable Chat provenance separate and opens note-owned citations', async () => {
    const user = userEvent.setup()
    const opened = note('chat-note', {
      origin_type: 'chat_answer',
      origin_snapshot: {
        origin_type: 'chat_answer',
        conversation_id: 'conversation-1',
        message_id: 'message-1',
        answer_text: 'The original grounded answer.',
        provider: 'local',
        model: 'model-1',
        citations: [
          {
            id: 'note-citation-1',
            origin_citation_id: 'chat-citation-1',
            ordinal: 1,
            source_id: 'source-1',
            chunk_id: 'chunk-1',
            chunk_text_hash: 'hash',
            source_title: 'Lecture',
            source_type: 'video',
            quote: 'Grounded excerpt',
            score: 0.9,
            locator: {
              kind: 'video_time',
              schema_version: 1,
              metadata: {},
              job_id: 'job-1',
              asset_id: null,
              start_seconds: 10,
              end_seconds: 20,
              segment_ids: [1],
            },
            spans: [
              {
                sentence_index: 0,
                start_offset: 0,
                end_offset: 10,
              },
            ],
          },
        ],
      },
    })
    noteApi.listNotebookNotes.mockResolvedValue([summary(opened)])
    noteApi.getNotebookNote.mockResolvedValue(opened)
    const onOpenCitation = vi.fn()

    render(
      <NotesWorkspace
        apiBaseUrl={API_BASE_URL}
        courseId={opened.course_id}
        initialNoteId={opened.id}
        onNoteRouteChange={vi.fn()}
        onOpenCitation={onOpenCitation}
      />,
    )

    expect(
      await screen.findByRole('heading', {
        name: 'Original Chat answer',
      }),
    ).toBeInTheDocument()
    await user.click(screen.getByText('View original answer'))
    expect(
      screen.getByText('The original grounded answer.'),
    ).toBeInTheDocument()

    await user.click(
      screen.getByRole('button', {
        name: /Open source 1: Lecture, 0:10.*0:20/,
      }),
    )
    expect(onOpenCitation).toHaveBeenCalledWith(
      expect.objectContaining<Partial<ChatCitation>>({
        id: 'note-citation-1',
        message_id: 'message-1',
        source_id: 'source-1',
      }),
      expect.any(HTMLButtonElement),
    )
  })

  it('moves a note to Trash and reports the recoverable entity', async () => {
    const user = userEvent.setup()
    const opened = note()
    noteApi.listNotebookNotes.mockResolvedValue([summary(opened)])
    noteApi.getNotebookNote.mockResolvedValue(opened)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const onNoteRouteChange = vi.fn()

    render(
      <NotesWorkspace
        apiBaseUrl={API_BASE_URL}
        courseId={opened.course_id}
        initialNoteId={opened.id}
        onNoteRouteChange={onNoteRouteChange}
      />,
    )

    await user.click(
      await screen.findByRole('button', { name: 'Move to Trash' }),
    )

    await waitFor(() =>
      expect(noteApi.deleteNotebookNote).toHaveBeenCalledWith(
        API_BASE_URL,
        opened.course_id,
        opened.id,
        opened.revision,
        expect.any(AbortSignal),
      ),
    )
    expect(reliability.announceTrashCreated).toHaveBeenCalledWith({
      entity_type: 'notebook_note',
      entity_id: opened.id,
    })
    expect(onNoteRouteChange).toHaveBeenLastCalledWith(null, 'replace')
  })

  it('refreshes the authoritative list after deleting during an older list request', async () => {
    const user = userEvent.setup()
    const opened = note('note-1')
    const unrelated = note('note-2', {
      title: 'Unrelated surviving note',
    })
    const lateList = deferred<NotebookNoteSummary[]>()
    noteApi.listNotebookNotes
      .mockReturnValueOnce(lateList.promise)
      .mockResolvedValueOnce([summary(unrelated)])
    noteApi.getNotebookNote.mockResolvedValue(opened)
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    render(
      <NotesWorkspace
        apiBaseUrl={API_BASE_URL}
        courseId={opened.course_id}
        initialNoteId={opened.id}
        onNoteRouteChange={vi.fn()}
      />,
    )

    await user.click(
      await screen.findByRole('button', { name: 'Move to Trash' }),
    )
    expect(
      await screen.findByText(unrelated.title),
    ).toBeInTheDocument()

    await act(async () => {
      lateList.resolve([summary(opened), summary(unrelated)])
      await Promise.resolve()
    })

    expect(screen.getByText(unrelated.title)).toBeInTheDocument()
    expect(screen.queryByText(opened.title)).not.toBeInTheDocument()
  })

  it('does not let a late save overwrite a newly selected note', async () => {
    const user = userEvent.setup()
    const first = note('note-1')
    const second = note('note-2', {
      title: 'Second note',
      body_markdown: 'Second body',
    })
    const lateSave = deferred<NotebookNote>()
    noteApi.listNotebookNotes.mockResolvedValue([
      summary(first),
      summary(second),
    ])
    noteApi.getNotebookNote.mockImplementation(
      (_api: string, _course: string, noteId: string) =>
        Promise.resolve(noteId === first.id ? first : second),
    )
    noteApi.updateNotebookNote.mockReturnValue(lateSave.promise)
    const onNoteRouteChange = vi.fn()

    const { rerender } = render(
      <NotesWorkspace
        apiBaseUrl={API_BASE_URL}
        courseId="course-1"
        initialNoteId={first.id}
        onNoteRouteChange={onNoteRouteChange}
      />,
    )
    const body = await screen.findByLabelText('Note')
    await user.clear(body)
    await user.type(body, 'Pending first-note edit')
    await user.click(
      screen.getByRole('button', { name: 'Save changes' }),
    )

    rerender(
      <NotesWorkspace
        apiBaseUrl={API_BASE_URL}
        courseId="course-1"
        initialNoteId={second.id}
        onNoteRouteChange={onNoteRouteChange}
      />,
    )
    expect(await screen.findByLabelText('Title')).toHaveValue(
      second.title,
    )

    await act(async () => {
      lateSave.resolve(
        note(first.id, {
          revision: first.revision + 1,
          title: 'Late first note',
        }),
      )
      await Promise.resolve()
    })

    expect(screen.getByLabelText('Title')).toHaveValue(second.title)
    expect(onNoteRouteChange).not.toHaveBeenCalledWith(
      first.id,
      'replace',
    )
  })

  it('remounts the editor on refresh so an external revision becomes the baseline', async () => {
    const user = userEvent.setup()
    const firstRevision = note('note-1', {
      revision: 1,
      title: 'Revision one',
      body_markdown: 'Body from revision one',
      updated_at: '2026-07-28T09:00:00Z',
    })
    const secondRevision = note('note-1', {
      revision: 2,
      title: 'Revision two',
      body_markdown: 'Body from revision two',
      updated_at: '2026-07-28T10:00:00Z',
    })
    noteApi.listNotebookNotes.mockResolvedValue([
      summary(firstRevision),
    ])
    noteApi.getNotebookNote
      .mockResolvedValueOnce(firstRevision)
      .mockResolvedValueOnce(secondRevision)

    render(
      <NotesWorkspace
        apiBaseUrl={API_BASE_URL}
        courseId="course-1"
        initialNoteId={firstRevision.id}
        onNoteRouteChange={vi.fn()}
      />,
    )

    expect(await screen.findByLabelText('Title')).toHaveValue(
      firstRevision.title,
    )
    await user.click(
      screen.getByRole('button', { name: 'Refresh notes' }),
    )

    await waitFor(() =>
      expect(screen.getByLabelText('Title')).toHaveValue(
        secondRevision.title,
      ),
    )
    expect(screen.getByLabelText('Note')).toHaveValue(
      secondRevision.body_markdown,
    )
    expect(
      screen.getByRole('button', { name: 'Save changes' }),
    ).toBeDisabled()
    expect(noteApi.updateNotebookNote).not.toHaveBeenCalled()
  })

  it('does not let a pending detail reload overwrite a completed save', async () => {
    const user = userEvent.setup()
    const opened = note('note-1', {
      revision: 2,
      body_markdown: 'Revision two body',
    })
    const lateDetail = deferred<NotebookNote>()
    const saved = note('note-1', {
      revision: 3,
      body_markdown: 'Revision three body',
      updated_at: '2026-07-28T11:00:00Z',
    })
    noteApi.listNotebookNotes.mockResolvedValue([summary(opened)])
    noteApi.getNotebookNote
      .mockResolvedValueOnce(opened)
      .mockReturnValueOnce(lateDetail.promise)
    noteApi.updateNotebookNote.mockResolvedValue(saved)

    render(
      <NotesWorkspace
        apiBaseUrl={API_BASE_URL}
        courseId="course-1"
        initialNoteId={opened.id}
        onNoteRouteChange={vi.fn()}
      />,
    )

    const body = await screen.findByLabelText('Note')
    await user.click(
      screen.getByRole('button', { name: 'Refresh notes' }),
    )
    await user.clear(body)
    await user.type(body, saved.body_markdown)
    await user.click(
      screen.getByRole('button', { name: 'Save changes' }),
    )
    await waitFor(() =>
      expect(screen.getByLabelText('Note')).toHaveValue(
        saved.body_markdown,
      ),
    )

    await act(async () => {
      lateDetail.resolve(opened)
      await Promise.resolve()
    })

    expect(screen.getByLabelText('Note')).toHaveValue(
      saved.body_markdown,
    )
    expect(screen.getByText(saved.title)).toBeInTheDocument()
  })

  it('does not announce a late delete after switching course scope', async () => {
    const user = userEvent.setup()
    const first = note('note-1')
    const second = note('note-2', {
      course_id: 'course-2',
      title: 'Course two note',
    })
    const lateDelete = deferred<unknown>()
    noteApi.listNotebookNotes.mockImplementation(
      (_api: string, courseId: string) =>
        Promise.resolve([
          summary(courseId === first.course_id ? first : second),
        ]),
    )
    noteApi.getNotebookNote.mockImplementation(
      (_api: string, courseId: string) =>
        Promise.resolve(courseId === first.course_id ? first : second),
    )
    noteApi.deleteNotebookNote.mockReturnValue(lateDelete.promise)
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const { rerender } = render(
      <NotesWorkspace
        apiBaseUrl={API_BASE_URL}
        courseId={first.course_id}
        initialNoteId={first.id}
        onNoteRouteChange={vi.fn()}
      />,
    )
    await user.click(
      await screen.findByRole('button', { name: 'Move to Trash' }),
    )

    rerender(
      <NotesWorkspace
        apiBaseUrl={API_BASE_URL}
        courseId={second.course_id}
        initialNoteId={second.id}
        onNoteRouteChange={vi.fn()}
      />,
    )
    expect(await screen.findByLabelText('Title')).toHaveValue(
      second.title,
    )

    await act(async () => {
      lateDelete.resolve(undefined)
      await Promise.resolve()
    })

    expect(reliability.announceTrashCreated).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Title')).toHaveValue(second.title)
  })

  it('does not let a late course response replace the active note editor', async () => {
    const courseOne = deferred<NotebookNote>()
    const courseTwoNote = note('note-2', {
      course_id: 'course-2',
      title: 'Course two note',
      body_markdown: 'Only course two should be visible.',
    })
    noteApi.listNotebookNotes.mockImplementation(
      (_api: string, courseId: string) =>
        Promise.resolve(
          courseId === 'course-2' ? [summary(courseTwoNote)] : [],
        ),
    )
    noteApi.getNotebookNote.mockImplementation(
      (_api: string, courseId: string) =>
        courseId === 'course-1'
          ? courseOne.promise
          : Promise.resolve(courseTwoNote),
    )

    const { rerender } = render(
      <NotesWorkspace
        apiBaseUrl={API_BASE_URL}
        courseId="course-1"
        initialNoteId="note-1"
        onNoteRouteChange={vi.fn()}
      />,
    )
    rerender(
      <NotesWorkspace
        apiBaseUrl={API_BASE_URL}
        courseId="course-2"
        initialNoteId="note-2"
        onNoteRouteChange={vi.fn()}
      />,
    )

    expect(await screen.findByLabelText('Title')).toHaveValue(
      'Course two note',
    )

    await act(async () => {
      courseOne.resolve(
        note('note-1', {
          title: 'Late course one note',
        }),
      )
      await Promise.resolve()
    })

    expect(screen.getByLabelText('Title')).toHaveValue(
      'Course two note',
    )
    expect(
      screen.queryByDisplayValue('Late course one note'),
    ).not.toBeInTheDocument()
  })
})
