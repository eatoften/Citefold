import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { GraphView } from './GraphView'
import type {
  CardRelationRecomputeResult,
  CourseCardRelationsGraph,
  GraphCourse,
} from './graphTypes'


vi.mock('react-force-graph-2d', () => ({
  default: ({
    graphData,
    onNodeClick,
  }: {
    graphData: { nodes: unknown[] }
    onNodeClick?: (node: unknown) => void
  }) => (
    <button
      type="button"
      data-testid="force-graph-node"
      onClick={() => {
        const node = graphData.nodes[1]
        if (node) onNodeClick?.(node)
      }}
    >
      Select graph node
    </button>
  ),
}))


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


function createGraph(courseId: string, cardTitle: string): CourseCardRelationsGraph {
  return {
    course_id: courseId,
    nodes: [
      {
        id: `${courseId}-card`,
        job_id: `${courseId}-job`,
        title: cardTitle,
        summary: `${cardTitle} summary`,
        tags: [],
        content_status: 'reviewed',
        source_start_seconds: 0,
        source_end_seconds: 30,
      },
    ],
    edges: [],
  }
}

function createTwoCardGraph(
  courseId: string,
): CourseCardRelationsGraph {
  const first = createGraph(courseId, `${courseId} first card`).nodes[0]
  const second = {
    ...first,
    id: `${courseId}-card-2`,
    title: `${courseId} second card`,
    summary: `${courseId} second card summary`,
  }
  return {
    course_id: courseId,
    nodes: [first, second],
    edges: [
      {
        id: `${courseId}-relation`,
        source: first.id,
        target: second.id,
        relation_type: 'related',
        score: 0.9,
        method: 'manual',
        status: 'accepted',
        explanation: 'Connected concepts',
      },
    ],
  }
}


class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}


const courses: GraphCourse[] = [
  { id: 'course-a', title: 'Course A', card_count: 1 },
  { id: 'course-b', title: 'Course B', card_count: 1 },
]

const baseProps = {
  apiBaseUrl: 'http://api.test',
  courses,
  selectedModel: 'test-model',
  showCourseSelector: false,
  initialCardId: null,
  onSelectCourse: vi.fn(),
  onOpenWorkspaceCard: vi.fn(),
}


beforeEach(() => {
  vi.stubGlobal('ResizeObserver', ResizeObserverMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})


describe('GraphView course loading', () => {
  it('uses the two-column embedded toolbar when the host owns course selection', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockResolvedValue(
        jsonResponse(createGraph('course-a', 'Course A graph card')),
      ),
    )

    render(<GraphView {...baseProps} selectedCourseId="course-a" />)

    const toolbar = screen.getByRole('heading', { name: 'Course graph' })
      .closest('header')
    expect(toolbar).toHaveClass('graph-toolbar-embedded')
    expect(screen.queryByLabelText('Course')).not.toBeInTheDocument()
  })

  it('clears course A, ignores its late reload, and aborts loads on switch and unmount', async () => {
    const courseA = createGraph('course-a', 'Course A graph card')
    const courseB = createGraph('course-b', 'Course B graph card')
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
      <GraphView {...baseProps} selectedCourseId="course-a" />,
    )

    await waitFor(() => {
      expect(screen.getAllByText('Course A graph card')).not.toHaveLength(0)
    })

    fireEvent.click(screen.getByRole('button', { name: 'Refresh graph' }))
    expect(fetchMock).toHaveBeenCalledTimes(2)
    const courseAReloadSignal = (
      fetchMock.mock.calls[1]?.[1] as RequestInit | undefined
    )?.signal

    rerender(<GraphView {...baseProps} selectedCourseId="course-b" />)

    expect(screen.queryAllByText('Course A graph card')).toHaveLength(0)
    expect(courseAReloadSignal?.aborted).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(3)

    await act(async () => {
      courseBLoad.resolve(jsonResponse(courseB))
      await courseBLoad.promise
    })
    await waitFor(() => {
      expect(screen.getAllByText('Course B graph card')).not.toHaveLength(0)
    })

    await act(async () => {
      lateCourseAReload.resolve(jsonResponse(courseA))
      await lateCourseAReload.promise
    })
    expect(screen.queryAllByText('Course A graph card')).toHaveLength(0)
    expect(screen.getAllByText('Course B graph card')).not.toHaveLength(0)

    fireEvent.click(screen.getByRole('button', { name: 'Refresh graph' }))
    expect(fetchMock).toHaveBeenCalledTimes(4)
    const courseBReloadSignal = (
      fetchMock.mock.calls[3]?.[1] as RequestInit | undefined
    )?.signal

    unmount()
    expect(courseBReloadSignal?.aborted).toBe(true)
  })

  it('announces load errors as alerts and successful recomputes as status messages', async () => {
    const graph = createGraph('course-a', 'Course A graph card')
    const recomputeResult: CardRelationRecomputeResult = {
      course_id: 'course-a',
      total_cards: 1,
      embedded_cards: 1,
      skipped_cards: 0,
      relations_written: 0,
      threshold: 0.72,
      top_k: 5,
    }
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(errorResponse('Graph unavailable'))
    vi.stubGlobal('fetch', fetchMock)

    const { unmount } = render(
      <GraphView {...baseProps} selectedCourseId="course-a" />,
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('Graph unavailable')
    unmount()

    fetchMock
      .mockResolvedValueOnce(jsonResponse(graph))
      .mockResolvedValueOnce(jsonResponse(recomputeResult))
      .mockResolvedValueOnce(jsonResponse(graph))

    render(<GraphView {...baseProps} selectedCourseId="course-a" />)
    await waitFor(() => {
      expect(screen.getAllByText('Course A graph card')).not.toHaveLength(0)
    })

    fireEvent.click(screen.getByRole('button', { name: 'Recompute' }))

    expect(await screen.findByRole('status')).toHaveTextContent(
      '0 relations from 1 embedded cards.',
    )
  })
})

describe('GraphView route synchronization', () => {
  it('emits push for user selection and restores later route prop changes', async () => {
    const graph = createTwoCardGraph('course-a')
    const onCardRouteChange = vi.fn()
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(graph)),
    )

    const view = render(
      <GraphView
        {...baseProps}
        selectedCourseId="course-a"
        initialCardId="course-a-card"
        onCardRouteChange={onCardRouteChange}
      />,
    )

    expect(
      await screen.findByRole('heading', {
        name: 'course-a first card',
      }),
    ).toBeInTheDocument()
    onCardRouteChange.mockClear()

    const cardList = document.querySelector('.graph-card-list')
    const relatedCards = document.querySelector('.graph-related-section')
    expect(cardList).not.toBeNull()
    expect(relatedCards).not.toBeNull()

    fireEvent.click(
      within(cardList as HTMLElement).getByRole('button', {
        name: /course-a second cardcourse-a second card summary/i,
      }),
    )
    expect(onCardRouteChange).toHaveBeenCalledWith(
      'course-a-card-2',
      'push',
    )
    expect(
      screen.getByRole('heading', {
        name: 'course-a second card',
      }),
    ).toBeInTheDocument()

    onCardRouteChange.mockClear()
    fireEvent.click(
      within(cardList as HTMLElement).getByRole('button', {
        name: /course-a first cardcourse-a first card summary/i,
      }),
    )
    onCardRouteChange.mockClear()
    fireEvent.click(
      within(relatedCards as HTMLElement).getByRole('button', {
        name: /course-a second cardcourse-a second card summary/i,
      }),
    )
    expect(onCardRouteChange).toHaveBeenCalledWith(
      'course-a-card-2',
      'push',
    )

    onCardRouteChange.mockClear()
    fireEvent.click(
      within(cardList as HTMLElement).getByRole('button', {
        name: /course-a first cardcourse-a first card summary/i,
      }),
    )
    onCardRouteChange.mockClear()
    fireEvent.click(screen.getByTestId('force-graph-node'))
    expect(onCardRouteChange).toHaveBeenCalledWith(
      'course-a-card-2',
      'push',
    )

    view.rerender(
      <GraphView
        {...baseProps}
        selectedCourseId="course-a"
        initialCardId="course-a-card-2"
        onCardRouteChange={onCardRouteChange}
      />,
    )
    onCardRouteChange.mockClear()
    view.rerender(
      <GraphView
        {...baseProps}
        selectedCourseId="course-a"
        initialCardId="course-a-card"
        onCardRouteChange={onCardRouteChange}
      />,
    )
    expect(
      screen.getByRole('heading', {
        name: 'course-a first card',
      }),
    ).toBeInTheDocument()
    expect(onCardRouteChange).not.toHaveBeenCalled()

    view.rerender(
      <GraphView
        {...baseProps}
        selectedCourseId="course-a"
        initialCardId={null}
        onCardRouteChange={onCardRouteChange}
      />,
    )
    expect(
      screen.getByText('Select a card to inspect relations'),
    ).toBeInTheDocument()

    view.rerender(
      <GraphView
        {...baseProps}
        selectedCourseId="course-a"
        initialCardId="course-a-card-2"
        onCardRouteChange={onCardRouteChange}
      />,
    )
    expect(
      screen.getByRole('heading', {
        name: 'course-a second card',
      }),
    ).toBeInTheDocument()
    expect(onCardRouteChange).not.toHaveBeenCalled()
  })

  it.each(['course-b-card', 'missing-card'])(
    'replace-clears invalid route card %s only after the course graph loads',
    async (initialCardId) => {
      const graphLoad = deferred<Response>()
      const onCardRouteChange = vi.fn()
      vi.stubGlobal(
        'fetch',
        vi.fn<typeof fetch>().mockImplementation(() => graphLoad.promise),
      )

      render(
        <GraphView
          {...baseProps}
          selectedCourseId="course-a"
          initialCardId={initialCardId}
          onCardRouteChange={onCardRouteChange}
        />,
      )

      expect(onCardRouteChange).not.toHaveBeenCalled()

      await act(async () => {
        graphLoad.resolve(
          jsonResponse(createGraph('course-a', 'Course A card')),
        )
        await graphLoad.promise
      })

      await waitFor(() => {
        expect(onCardRouteChange).toHaveBeenCalledWith(null, 'replace')
      })
      expect(
        screen.getByText('Select a card to inspect relations'),
      ).toBeInTheDocument()
    },
  )
})

describe('GraphView mutation isolation', () => {
  it('does not apply a late course A recompute result after switching to course B', async () => {
    const courseA = createGraph('course-a', 'Course A graph card')
    const courseB = createGraph('course-b', 'Course B graph card')
    const lateCourseARecompute = deferred<Response>()
    const fetchMock = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(courseA))
      .mockImplementationOnce(() => lateCourseARecompute.promise)
      .mockResolvedValueOnce(jsonResponse(courseB))
    vi.stubGlobal('fetch', fetchMock)

    const view = render(
      <GraphView {...baseProps} selectedCourseId="course-a" />,
    )
    await waitFor(() => {
      expect(screen.getAllByText('Course A graph card')).not.toHaveLength(0)
    })

    fireEvent.click(screen.getByRole('button', { name: 'Recompute' }))
    expect(screen.getByRole('button', { name: 'Computing' })).toBeDisabled()

    view.rerender(
      <GraphView {...baseProps} selectedCourseId="course-b" />,
    )
    await waitFor(() => {
      expect(screen.getAllByText('Course B graph card')).not.toHaveLength(0)
    })
    expect(screen.getByRole('button', { name: 'Recompute' })).toBeEnabled()

    await act(async () => {
      lateCourseARecompute.resolve(
        jsonResponse<CardRelationRecomputeResult>({
          course_id: 'course-a',
          total_cards: 1,
          embedded_cards: 1,
          skipped_cards: 0,
          relations_written: 7,
          threshold: 0.72,
          top_k: 5,
        }),
      )
      await lateCourseARecompute.promise
    })

    expect(
      screen.queryByText('7 relations from 1 embedded cards.'),
    ).not.toBeInTheDocument()
    expect(screen.queryAllByText('Course A graph card')).toHaveLength(0)
    expect(screen.getAllByText('Course B graph card')).not.toHaveLength(0)
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })
})
