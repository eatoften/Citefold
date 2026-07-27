import type {
  NotebookNote,
  NotebookNoteCreate,
  NotebookNotePromotion,
  NotebookNoteSummary,
  NotebookNoteUpdate,
} from './noteTypes'

type NoteConflictDetail = {
  message?: string
  current?: NotebookNote | null
}

export class NotebookNoteApiError extends Error {
  readonly status: number
  readonly current: NotebookNote | null

  constructor(
    message: string,
    status: number,
    current: NotebookNote | null = null,
  ) {
    super(message)
    this.name = 'NotebookNoteApiError'
    this.status = status
    this.current = current
  }
}

type RequestOptions = {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'
  body?: unknown
  signal?: AbortSignal
}

async function requestJson<T>(
  apiBaseUrl: string,
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const response = await fetch(
    `${apiBaseUrl.replace(/\/$/, '')}${path}`,
    {
      method: options.method ?? 'GET',
      headers:
        options.body === undefined
          ? { Accept: 'application/json' }
          : {
              Accept: 'application/json',
              'Content-Type': 'application/json',
            },
      body:
        options.body === undefined
          ? undefined
          : JSON.stringify(options.body),
      signal: options.signal,
    },
  )

  const responseText = await response.text()
  let payload: unknown
  if (responseText) {
    try {
      payload = JSON.parse(responseText)
    } catch {
      payload = undefined
    }
  }

  if (!response.ok) {
    let message = `Notes request failed (${response.status}).`
    let current: NotebookNote | null = null
    if (
      typeof payload === 'object' &&
      payload !== null &&
      'detail' in payload
    ) {
      const detail = payload.detail
      if (typeof detail === 'string') {
        message = detail
      } else if (typeof detail === 'object' && detail !== null) {
        const conflict = detail as NoteConflictDetail
        if (typeof conflict.message === 'string') {
          message = conflict.message
        }
        current = conflict.current ?? null
      }
    }
    throw new NotebookNoteApiError(message, response.status, current)
  }

  return payload as T
}

function pathId(value: string): string {
  return encodeURIComponent(value)
}

function courseNotesPath(courseId: string): string {
  return `/courses/${pathId(courseId)}/notes`
}

export function listNotebookNotes(
  apiBaseUrl: string,
  courseId: string,
  signal?: AbortSignal,
): Promise<NotebookNoteSummary[]> {
  return requestJson<NotebookNoteSummary[]>(
    apiBaseUrl,
    `${courseNotesPath(courseId)}?limit=1000&offset=0`,
    { signal },
  )
}

export function createNotebookNote(
  apiBaseUrl: string,
  courseId: string,
  request: NotebookNoteCreate,
  signal?: AbortSignal,
): Promise<NotebookNote> {
  return requestJson(
    apiBaseUrl,
    courseNotesPath(courseId),
    {
      method: 'POST',
      body: request,
      signal,
    },
  )
}

export function getNotebookNote(
  apiBaseUrl: string,
  courseId: string,
  noteId: string,
  signal?: AbortSignal,
): Promise<NotebookNote> {
  return requestJson(
    apiBaseUrl,
    `${courseNotesPath(courseId)}/${pathId(noteId)}`,
    { signal },
  )
}

export function updateNotebookNote(
  apiBaseUrl: string,
  courseId: string,
  noteId: string,
  request: NotebookNoteUpdate,
  signal?: AbortSignal,
): Promise<NotebookNote> {
  return requestJson(
    apiBaseUrl,
    `${courseNotesPath(courseId)}/${pathId(noteId)}`,
    {
      method: 'PATCH',
      body: request,
      signal,
    },
  )
}

export function deleteNotebookNote(
  apiBaseUrl: string,
  courseId: string,
  noteId: string,
  expectedRevision: number,
  signal?: AbortSignal,
): Promise<unknown> {
  return requestJson(
    apiBaseUrl,
    `${courseNotesPath(courseId)}/${pathId(
      noteId,
    )}?expected_revision=${encodeURIComponent(expectedRevision)}`,
    {
      method: 'DELETE',
      signal,
    },
  )
}

export function saveChatAnswerAsNote(
  apiBaseUrl: string,
  courseId: string,
  messageId: string,
  title?: string,
  signal?: AbortSignal,
): Promise<NotebookNote> {
  return requestJson(
    apiBaseUrl,
    `${courseNotesPath(courseId)}/from-chat/${pathId(messageId)}`,
    {
      method: 'POST',
      body: title?.trim() ? { title: title.trim() } : {},
      signal,
    },
  )
}

export function publishNotebookNoteAsSource(
  apiBaseUrl: string,
  courseId: string,
  noteId: string,
  expectedRevision: number,
  signal?: AbortSignal,
): Promise<NotebookNotePromotion> {
  return requestJson(
    apiBaseUrl,
    `${courseNotesPath(courseId)}/${pathId(noteId)}/source`,
    {
      method: 'POST',
      body: { expected_revision: expectedRevision },
      signal,
    },
  )
}
