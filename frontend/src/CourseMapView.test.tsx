import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CourseMapView } from './CourseMapView'
import type {
  CourseMapCourse,
  CourseMapPayload,
} from './courseMapTypes'


type Deferred<T> = {
  promise: Promise<T>
  resolve: (value: T) => void
}


function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}


function jsonResponse<T>(payload: T): Response {
  return {
    ok: true,
    status: 200,
    json: vi.fn().mockResolvedValue(payload),
  } as unknown as Response
}


function errorResponse(detail: string): Response {
  return {
    ok: false,
    status: 500,
    json: vi.fn().mockResolvedValue({ detail }),
  } as unknown as Response
}


function createCourseMap(courseId: string, topicTitle: string): CourseMapPayload {
  const topicId = `${courseId}-topic`
  return {
    course_id: courseId,
    topics: [
      {
        id: topicId,
        course_id: courseId,
        parent_topic_id: null,
        title: topicTitle,
        summary: `${topicTitle} summary`,
        position: 0,
        depth: 0,
        method: 'manual',
        status: 'accepted',
        is_system: false,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
    ],
    memberships: [],
    topic_relations: [],
    cards: [],
    coverage: {
      total_cards: 0,
      cards_with_review_items: 0,
      review_item_count: 0,
      due_review_item_count: 0,
      cards_with_learning_documents: 0,
      learning_document_count: 0,
      source_asset_count: 0,
      unsorted_card_count: 0,
      topic_coverage: [],
    },
  }
}

function createRoutableCourseMap(courseId: string): CourseMapPayload {
  const firstTopicId = `${courseId}-topic-a`
  const secondTopicId = `${courseId}-topic-b`
  const firstCardId = `${courseId}-card-a1`
  const secondCardId = `${courseId}-card-a2`
  const thirdCardId = `${courseId}-card-b`
  const payload = createCourseMap(courseId, 'Topic A')

  return {
    ...payload,
    topics: [
      {
        ...payload.topics[0],
        id: firstTopicId,
        title: 'Topic A',
      },
      {
        ...payload.topics[0],
        id: secondTopicId,
        title: 'Topic B',
        position: 1,
      },
    ],
    memberships: [
      {
        id: `${courseId}-membership-a1`,
        topic_id: firstTopicId,
        card_id: firstCardId,
        role: 'primary',
        position: 0,
        method: 'manual',
        confidence: 1,
        status: 'accepted',
      },
      {
        id: `${courseId}-membership-a2`,
        topic_id: firstTopicId,
        card_id: secondCardId,
        role: 'primary',
        position: 1,
        method: 'manual',
        confidence: 1,
        status: 'accepted',
      },
      {
        id: `${courseId}-membership-b`,
        topic_id: secondTopicId,
        card_id: thirdCardId,
        role: 'primary',
        position: 0,
        method: 'manual',
        confidence: 1,
        status: 'accepted',
      },
    ],
    cards: [
      {
        id: firstCardId,
        job_id: `${courseId}-job`,
        title: 'Card A1',
        summary: 'First card in topic A',
        card_kind: 'concept',
        tags: [],
        content_status: 'reviewed',
        review_item_count: 0,
        source_video: 'video.mp4',
        source_start_seconds: 0,
        source_end_seconds: 30,
        note_count: 0,
        learning_document_count: 0,
      },
      {
        id: secondCardId,
        job_id: `${courseId}-job`,
        title: 'Card A2',
        summary: 'Second card in topic A',
        card_kind: 'concept',
        tags: [],
        content_status: 'reviewed',
        review_item_count: 0,
        source_video: 'video.mp4',
        source_start_seconds: 30,
        source_end_seconds: 60,
        note_count: 0,
        learning_document_count: 0,
      },
      {
        id: thirdCardId,
        job_id: `${courseId}-job`,
        title: 'Card B',
        summary: 'Card in topic B',
        card_kind: 'concept',
        tags: [],
        content_status: 'reviewed',
        review_item_count: 0,
        source_video: 'video.mp4',
        source_start_seconds: 60,
        source_end_seconds: 90,
        note_count: 0,
        learning_document_count: 0,
      },
    ],
    coverage: {
      ...payload.coverage,
      total_cards: 3,
    },
  }
}


const courses: CourseMapCourse[] = [
  { id: 'course-a', title: 'Course A', card_count: 0 },
  { id: 'course-b', title: 'Course B', card_count: 0 },
]

const baseProps = {
  apiBaseUrl: 'http://api.test',
  courses,
  showCourseSelector: false,
  initialCardId: null,
  selectedModel: 'test-model',
  onSelectCourse: vi.fn(),
  onOpenWorkspaceCard: vi.fn(),
  onOpenStudyCard: vi.fn(),
}


afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})


describe('CourseMapView course loading', () => {
  it('clears course A, ignores its late reload, and aborts loads on switch and unmount', async () => {
    const courseA = createCourseMap('course-a', 'Course A topic')
    const courseB = createCourseMap('course-b', 'Course B topic')
    const lateCourseAReload = deferred<Response>()
    const courseBLoad = deferred<Response>()
    const pendingCourseBReload = deferred<Response>()
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(courseA))
      .mockImplementationOnce(() => lateCourseAReload.promise)
      .mockImplementationOnce(() => courseBLoad.promise)
      .mockImplementationOnce(() => pendingCourseBReload.promise)
    vi.stubGlobal('fetch', fetchMock)

    const { rerender, unmount } = render(
      <CourseMapView {...baseProps} selectedCourseId="course-a" />,
    )

    await waitFor(() => {
      expect(screen.getAllByText('Course A topic')).not.toHaveLength(0)
    })

    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    expect(fetchMock).toHaveBeenCalledTimes(2)
    const courseAReloadSignal = (
      fetchMock.mock.calls[1]?.[1] as RequestInit | undefined
    )?.signal

    rerender(<CourseMapView {...baseProps} selectedCourseId="course-b" />)

    expect(screen.queryAllByText('Course A topic')).toHaveLength(0)
    expect(courseAReloadSignal?.aborted).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(3)

    await act(async () => {
      courseBLoad.resolve(jsonResponse(courseB))
      await courseBLoad.promise
    })
    await waitFor(() => {
      expect(screen.getAllByText('Course B topic')).not.toHaveLength(0)
    })

    await act(async () => {
      lateCourseAReload.resolve(jsonResponse(courseA))
      await lateCourseAReload.promise
    })
    expect(screen.queryAllByText('Course A topic')).toHaveLength(0)
    expect(screen.getAllByText('Course B topic')).not.toHaveLength(0)

    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    expect(fetchMock).toHaveBeenCalledTimes(4)
    const courseBReloadSignal = (
      fetchMock.mock.calls[3]?.[1] as RequestInit | undefined
    )?.signal

    unmount()
    expect(courseBReloadSignal?.aborted).toBe(true)
  })

  it('announces load errors as alerts and successful changes as status messages', async () => {
    const courseMap = createCourseMap('course-a', 'Course A topic')
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(errorResponse('Map unavailable'))
    vi.stubGlobal('fetch', fetchMock)

    const { unmount } = render(
      <CourseMapView {...baseProps} selectedCourseId="course-a" />,
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('Map unavailable')
    unmount()

    fetchMock
      .mockResolvedValueOnce(jsonResponse(courseMap))
      .mockResolvedValueOnce(jsonResponse({}))
      .mockResolvedValueOnce(jsonResponse(courseMap))

    render(<CourseMapView {...baseProps} selectedCourseId="course-a" />)
    await waitFor(() => {
      expect(screen.getAllByText('Course A topic')).not.toHaveLength(0)
    })

    fireEvent.change(screen.getByPlaceholderText('New topic'), {
      target: { value: 'New topic title' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Add topic' }))

    expect(await screen.findByRole('status')).toHaveTextContent('Topic created.')
  })

  it('pushes user card selection and restores selection from route prop changes', async () => {
    const courseMap = createRoutableCourseMap('course-a')
    const onCardRouteChange = vi.fn()
    const fetchMock = vi.fn<typeof fetch>()
      .mockImplementation(() =>
        Promise.resolve(jsonResponse(courseMap)),
      )
    vi.stubGlobal('fetch', fetchMock)

    const { rerender } = render(
      <CourseMapView
        {...baseProps}
        selectedCourseId="course-a"
        initialCardId="course-a-card-a1"
        onCardRouteChange={onCardRouteChange}
      />,
    )

    expect(
      await screen.findByRole('button', {
        name: 'Card A1 selected',
      }),
    ).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(
      screen.getByRole('button', { name: 'Select Card A2' }),
    )
    expect(onCardRouteChange).toHaveBeenCalledWith(
      'course-a-card-a2',
      'push',
    )
    expect(
      screen.getByRole('button', { name: 'Card A2 selected' }),
    ).toHaveAttribute('aria-pressed', 'true')

    onCardRouteChange.mockClear()
    rerender(
      <CourseMapView
        {...baseProps}
        selectedCourseId="course-a"
        initialCardId="course-a-card-b"
        onCardRouteChange={onCardRouteChange}
      />,
    )

    expect(
      await screen.findByRole('button', {
        name: 'Card B selected',
      }),
    ).toHaveAttribute('aria-pressed', 'true')
    expect(onCardRouteChange).not.toHaveBeenCalled()

    rerender(
      <CourseMapView
        {...baseProps}
        selectedCourseId="course-a"
        initialCardId={null}
        onCardRouteChange={onCardRouteChange}
      />,
    )

    await waitFor(() => {
      expect(
        screen.queryAllByRole('button', { pressed: true }),
      ).toHaveLength(0)
    })
  })

  it.each([
    ['missing card', 'missing-card'],
    ['cross-course card', 'course-b-card-a1'],
  ])(
    'replace-clears a %s initial route selection',
    async (_label, initialCardId) => {
      const courseMap = createRoutableCourseMap('course-a')
      const onCardRouteChange = vi.fn()
      vi.stubGlobal(
        'fetch',
        vi.fn<typeof fetch>().mockResolvedValue(
          jsonResponse(courseMap),
        ),
      )

      render(
        <CourseMapView
          {...baseProps}
          selectedCourseId="course-a"
          initialCardId={initialCardId}
          onCardRouteChange={onCardRouteChange}
        />,
      )

      await waitFor(() => {
        expect(onCardRouteChange).toHaveBeenCalledWith(
          null,
          'replace',
        )
      })
      expect(
        screen.queryAllByRole('button', { pressed: true }),
      ).toHaveLength(0)
    },
  )

  it('does not apply a late course A suggestion continuation to course B', async () => {
    const courseA = createCourseMap('course-a', 'Course A topic')
    const courseB = createCourseMap('course-b', 'Course B topic')
    const lateSuggestion = deferred<Response>()
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(courseA))
      .mockImplementationOnce(() => lateSuggestion.promise)
      .mockResolvedValueOnce(jsonResponse(courseB))
    vi.stubGlobal('fetch', fetchMock)

    const { rerender } = render(
      <CourseMapView {...baseProps} selectedCourseId="course-a" />,
    )
    await waitFor(() => {
      expect(screen.getAllByText('Course A topic')).not.toHaveLength(0)
    })

    fireEvent.click(
      screen.getByRole('button', { name: 'Suggest structure' }),
    )
    expect(fetchMock).toHaveBeenCalledTimes(2)

    rerender(
      <CourseMapView {...baseProps} selectedCourseId="course-b" />,
    )
    await waitFor(() => {
      expect(screen.getAllByText('Course B topic')).not.toHaveLength(0)
    })

    await act(async () => {
      lateSuggestion.resolve(
        jsonResponse({
          suggested_topics: [
            {
              ...courseA.topics[0],
              id: 'course-a-suggestion',
              title: 'Late A suggestion',
              status: 'suggested',
            },
          ],
          suggested_memberships: 2,
          warning: null,
          mean_coherence: 0.92,
          singleton_topic_count: 0,
          largest_topic_size: 2,
          cluster_sizes: [2],
        }),
      )
      await lateSuggestion.promise
    })

    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(screen.queryByText('Late A suggestion')).not.toBeInTheDocument()
    expect(screen.queryByText(/coherence 0.92/)).not.toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.getAllByText('Course B topic')).not.toHaveLength(0)
    expect(
      screen.getByRole('button', { name: 'Suggest structure' }),
    ).toBeEnabled()
  })

  it('does not apply a late course A create continuation to course B', async () => {
    const courseA = createCourseMap('course-a', 'Course A topic')
    const courseB = createCourseMap('course-b', 'Course B topic')
    const lateCreate = deferred<Response>()
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(courseA))
      .mockImplementationOnce(() => lateCreate.promise)
      .mockResolvedValueOnce(jsonResponse(courseB))
    vi.stubGlobal('fetch', fetchMock)

    const { rerender } = render(
      <CourseMapView {...baseProps} selectedCourseId="course-a" />,
    )
    await waitFor(() => {
      expect(screen.getAllByText('Course A topic')).not.toHaveLength(0)
    })

    fireEvent.change(screen.getByPlaceholderText('New topic'), {
      target: { value: 'Late A topic' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Add topic' }))
    expect(fetchMock).toHaveBeenCalledTimes(2)

    rerender(
      <CourseMapView {...baseProps} selectedCourseId="course-b" />,
    )
    await waitFor(() => {
      expect(screen.getAllByText('Course B topic')).not.toHaveLength(0)
    })

    await act(async () => {
      lateCreate.resolve(jsonResponse({}))
      await lateCreate.promise
    })

    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(screen.queryByText('Topic created.')).not.toBeInTheDocument()
    expect(screen.getByPlaceholderText('New topic')).toHaveValue('')
    fireEvent.change(screen.getByPlaceholderText('New topic'), {
      target: { value: 'Course B topic draft' },
    })
    expect(screen.getAllByText('Course B topic')).not.toHaveLength(0)
    expect(
      screen.getByRole('button', { name: 'Add topic' }),
    ).toBeEnabled()
  })
})
