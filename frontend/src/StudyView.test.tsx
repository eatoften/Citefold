import {
  act,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { StudyView } from './StudyView'
import type {
  SourceAsset,
  StudyCard,
  StudyCourse,
} from './studyTypes'

type Deferred<T> = {
  promise: Promise<T>
  resolve: (value: T) => void
}

type PendingRequest = {
  url: string
  init?: RequestInit
  deferred: Deferred<Response>
}

const COURSES: StudyCourse[] = [
  { id: 'course-a', title: 'Course A', card_count: 1 },
  { id: 'course-b', title: 'Course B', card_count: 1 },
]

function createDeferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function createCard(course: 'A' | 'B'): StudyCard {
  return {
    id: `card-${course.toLocaleLowerCase()}`,
    job_id: `job-${course.toLocaleLowerCase()}`,
    title: `${course} card`,
    summary: `${course} summary`,
    card_kind: 'concept',
    tags: [course],
    content_status: 'reviewed',
    review_item_count: 0,
    learning_document_count: 0,
    source_video: `${course}.mp4`,
    source_start_seconds: 0,
    source_end_seconds: 10,
    note_count: 0,
  }
}

function payloadForStudyRequest(
  url: string,
  course: 'A' | 'B',
): unknown {
  if (url.endsWith('/card-index')) {
    return [createCard(course)]
  }
  if (url.endsWith('/learning-documents')) {
    return []
  }
  if (url.endsWith('/source-assets')) {
    return [] satisfies SourceAsset[]
  }
  throw new Error(`Unexpected Study request: ${url}`)
}

function renderStudy(selectedCourseId: string | null) {
  return (
    <StudyView
      apiBaseUrl="http://api.test"
      courses={COURSES}
      selectedCourseId={selectedCourseId}
      selectedModel="test-model"
      showCourseSelector={false}
      initialCardId={null}
      initialDocumentId={null}
      onSelectCourse={vi.fn()}
    />
  )
}

describe('StudyView course isolation', () => {
  it('aborts A and ignores its late library response after switching to B', async () => {
    const pendingA: PendingRequest[] = []
    const fetchMock = vi.fn(
      (
        input: RequestInfo | URL,
        init?: RequestInit,
      ): Promise<Response> => {
        const url = String(input)
        if (url.includes('/courses/course-a/')) {
          const deferred = createDeferred<Response>()
          pendingA.push({ url, init, deferred })
          return deferred.promise
        }
        if (url.includes('/courses/course-b/')) {
          return Promise.resolve(
            jsonResponse(payloadForStudyRequest(url, 'B')),
          )
        }
        throw new Error(`Unexpected fetch: ${url}`)
      },
    )
    vi.stubGlobal('fetch', fetchMock)

    const { rerender } = render(renderStudy('course-a'))

    await waitFor(() => {
      expect(pendingA).toHaveLength(3)
    })

    rerender(renderStudy('course-b'))

    expect(
      screen.queryByRole('option', { name: 'A card' }),
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole('region', {
        name: 'Study document workspace',
      }),
    ).toBeInTheDocument()
    expect(
      screen.queryByLabelText('Course'),
    ).not.toBeInTheDocument()

    expect(
      await screen.findByRole('option', { name: 'B card' }),
    ).toBeInTheDocument()
    expect(
      pendingA.every(
        ({ init }) => init?.signal?.aborted === true,
      ),
    ).toBe(true)

    await act(async () => {
      for (const request of pendingA) {
        request.deferred.resolve(
          jsonResponse(
            payloadForStudyRequest(request.url, 'A'),
          ),
        )
      }
      await Promise.resolve()
    })

    expect(
      screen.getByRole('option', { name: 'B card' }),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('option', { name: 'A card' }),
    ).not.toBeInTheDocument()
  })

  it('aborts active library requests when unmounted', async () => {
    const signals: AbortSignal[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (
          _input: RequestInfo | URL,
          init?: RequestInit,
        ): Promise<Response> => {
          if (init?.signal) {
            signals.push(init.signal)
          }
          return new Promise<Response>(() => undefined)
        },
      ),
    )

    const { unmount } = render(renderStudy('course-a'))

    await waitFor(() => {
      expect(signals).toHaveLength(3)
    })
    unmount()

    expect(signals.every((signal) => signal.aborted)).toBe(true)
  })

  it('announces loader failures as alerts', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          jsonResponse({ detail: 'Study unavailable' }, 503),
        ),
      ),
    )

    render(renderStudy('course-a'))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Study unavailable',
    )
  })
})
