import {
  act,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ReviewView } from './ReviewView'
import type { CourseMapPayload } from './courseMapTypes'
import type {
  ReviewCourse,
  ReviewQueue,
  ReviewQueueItem,
} from './reviewTypes'

type Deferred<T> = {
  promise: Promise<T>
  resolve: (value: T) => void
}

type PendingRequest = {
  url: string
  init?: RequestInit
  deferred: Deferred<Response>
}

const COURSES: ReviewCourse[] = [
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

function createQueueItem(course: 'A' | 'B'): ReviewQueueItem {
  const lowerCourse = course.toLocaleLowerCase()
  return {
    review_item: {
      id: `review-${lowerCourse}`,
      card_id: `card-${lowerCourse}`,
      item_type: 'short_answer',
      prompt: `${course} prompt`,
      expected_answer: `${course} answer`,
      source_claim_ids: [],
      source: 'generated',
      status: 'active',
    },
    progress: {
      due_at: '2026-07-27T00:00:00.000Z',
      review_count: 0,
      lapse_count: 0,
    },
    phase: 'new',
    card_id: `card-${lowerCourse}`,
    card_title: `${course} card`,
    card_summary: `${course} summary`,
    card_kind: 'concept',
    claims: [],
    topic_id: null,
    topic_title: null,
    source_start_seconds: 0,
    source_end_seconds: 10,
  }
}

function createQueue(course: 'A' | 'B'): ReviewQueue {
  const item = createQueueItem(course)
  return {
    course_id: `course-${course.toLocaleLowerCase()}`,
    topic_id: null,
    due_count: 1,
    new_count: 1,
    learning_count: 0,
    review_count: 0,
    relearning_count: 0,
    items: [item],
  }
}

function createCourseMap(course: 'A' | 'B'): CourseMapPayload {
  return {
    course_id: `course-${course.toLocaleLowerCase()}`,
    topics: [],
    memberships: [],
    topic_relations: [],
    cards: [],
    coverage: {
      total_cards: 1,
      cards_with_review_items: 1,
      review_item_count: 1,
      due_review_item_count: 1,
      cards_with_learning_documents: 0,
      learning_document_count: 0,
      source_asset_count: 0,
      unsorted_card_count: 1,
      topic_coverage: [],
    },
  }
}

function payloadForReviewRequest(
  url: string,
  course: 'A' | 'B',
): unknown {
  if (url.includes('/review/queue?')) {
    return createQueue(course)
  }
  if (url.endsWith('/map')) {
    return createCourseMap(course)
  }
  throw new Error(`Unexpected Review request: ${url}`)
}

function renderReview(selectedCourseId: string | null) {
  return (
    <ReviewView
      apiBaseUrl="http://api.test"
      courses={COURSES}
      selectedCourseId={selectedCourseId}
      showCourseSelector={false}
      onSelectCourse={vi.fn()}
      onOpenWorkspaceCard={vi.fn()}
    />
  )
}

describe('ReviewView course isolation', () => {
  it('aborts A and ignores its late queue response after switching to B', async () => {
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
            jsonResponse(payloadForReviewRequest(url, 'B')),
          )
        }
        throw new Error(`Unexpected fetch: ${url}`)
      },
    )
    vi.stubGlobal('fetch', fetchMock)

    const { rerender } = render(renderReview('course-a'))

    await waitFor(() => {
      expect(pendingA).toHaveLength(2)
    })

    rerender(renderReview('course-b'))

    expect(
      screen.queryByRole('heading', { name: 'A prompt' }),
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole('region', { name: 'Review session' }),
    ).toBeInTheDocument()
    expect(
      screen.queryByLabelText('Course'),
    ).not.toBeInTheDocument()

    expect(
      await screen.findByRole('heading', { name: 'B prompt' }),
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
            payloadForReviewRequest(request.url, 'A'),
          ),
        )
      }
      await Promise.resolve()
    })

    expect(
      screen.getByRole('heading', { name: 'B prompt' }),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('heading', { name: 'A prompt' }),
    ).not.toBeInTheDocument()
  })

  it('aborts active queue requests when unmounted', async () => {
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

    const { unmount } = render(renderReview('course-a'))

    await waitFor(() => {
      expect(signals).toHaveLength(2)
    })
    unmount()

    expect(signals.every((signal) => signal.aborted)).toBe(true)
  })

  it('announces failures as alerts and successful ratings as status', async () => {
    const fetchMock = vi.fn(
      (
        input: RequestInfo | URL,
        init?: RequestInit,
      ): Promise<Response> => {
        const url = String(input)
        if (init?.method === 'POST') {
          return Promise.resolve(jsonResponse({}))
        }
        if (url.includes('/review/queue?')) {
          return Promise.resolve(jsonResponse(createQueue('B')))
        }
        if (url.endsWith('/map')) {
          return Promise.resolve(
            jsonResponse(createCourseMap('B')),
          )
        }
        throw new Error(`Unexpected fetch: ${url}`)
      },
    )
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    const { unmount } = render(renderReview('course-b'))

    await screen.findByRole('heading', { name: 'B prompt' })
    await user.click(
      screen.getByRole('button', { name: 'Reveal answer' }),
    )
    await user.click(
      screen.getByRole('button', { name: 'good' }),
    )

    expect(await screen.findByRole('status')).toHaveTextContent(
      'Rated good.',
    )
    unmount()

    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          jsonResponse({ detail: 'Review unavailable' }, 503),
        ),
      ),
    )
    render(renderReview('course-a'))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Review unavailable',
    )
  })
})
