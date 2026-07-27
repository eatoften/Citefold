import {
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest'
import App from './App'

const COURSE = {
  id: 'course-1',
  title: 'Machine Learning',
  description: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  job_count: 0,
  card_count: 1,
}

const CARD_INDEX = {
  id: 'card-1',
  job_id: 'job-1',
  title: 'Gradient descent',
  summary: 'An iterative optimization method.',
  card_kind: 'concept',
  tags: ['optimization'],
  content_status: 'reviewed',
  review_item_count: 0,
  source_video: 'lecture.mp4',
  source_start_seconds: 30,
  source_end_seconds: 60,
  note_count: 0,
  learning_document_count: 0,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

const CARD_DETAIL = {
  ...CARD_INDEX,
  key_points: ['Follow the negative gradient.'],
  claims: [],
  unsupported_terms: [],
  review_items: [],
  provider: 'local',
  model: 'test-model',
}

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function installBackendMock(
  override?: (
    path: string,
  ) => Promise<Response> | undefined,
) {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      (
        input: RequestInfo | URL,
      ): Promise<Response> => {
        const url = new URL(String(input))
        const path = url.pathname
        const overridden = override?.(path)
        if (overridden) return overridden

        if (path === '/health') {
          return Promise.resolve(jsonResponse({ status: 'ok' }))
        }
        if (path === '/llm/status') {
          return Promise.resolve(
            jsonResponse({
              provider: 'local',
              base_url: 'http://model.test',
              model: 'test-model',
              available: true,
              error_message: null,
            }),
          )
        }
        if (path === '/llm/models') {
          return Promise.resolve(
            jsonResponse({
              provider: 'local',
              base_url: 'http://model.test',
              default_model: 'test-model',
              models: ['test-model'],
              available: true,
              error_message: null,
            }),
          )
        }
        if (path === '/runtime/check') {
          return Promise.resolve(
            jsonResponse({
              ready: true,
              dependencies: [],
            }),
          )
        }
        if (path === '/courses') {
          return Promise.resolve(jsonResponse([COURSE]))
        }
        if (path === '/courses/course-1/jobs') {
          return Promise.resolve(jsonResponse([]))
        }
        if (path === '/courses/course-1/card-index') {
          return Promise.resolve(jsonResponse([CARD_INDEX]))
        }
        if (path === '/courses/course-1/sources') {
          return Promise.resolve(jsonResponse([]))
        }
        if (path === '/cards/card-1') {
          return Promise.resolve(jsonResponse(CARD_DETAIL))
        }
        if (path === '/cards/card-1/notes') {
          return Promise.resolve(jsonResponse([]))
        }

        throw new Error(`Unexpected App request: ${path}`)
      },
    ),
  )
}

describe('App source-first shell', () => {
  beforeEach(() => {
    window.history.replaceState(
      {},
      '',
      '/?view=sources',
    )
    installBackendMock()
  })

  it('renders one main landmark and focuses each primary route heading', async () => {
    const user = userEvent.setup()
    const { container } = render(<App />)

    const sourcesHeading = await screen.findByRole('heading', {
      level: 1,
      name: 'Sources',
    })
    await waitFor(() => {
      expect(document.activeElement).toBe(sourcesHeading)
    })
    expect(container.querySelectorAll('main')).toHaveLength(1)

    await user.click(screen.getByRole('link', { name: 'Chat' }))
    const chatHeading = await screen.findByRole('heading', {
      level: 1,
      name: 'Chat',
    })
    await waitFor(() => {
      expect(document.activeElement).toBe(chatHeading)
    })
    expect(container.querySelectorAll('main')).toHaveLength(1)

    await user.click(screen.getByRole('link', { name: 'Studio' }))
    const studioHeading = await screen.findByRole('heading', {
      level: 1,
      name: 'Cards',
    })
    await waitFor(() => {
      expect(document.activeElement).toBe(studioHeading)
    })
    expect(container.querySelectorAll('main')).toHaveLength(1)
  })

  it('keeps video processing behind an explicit Sources action', async () => {
    const user = userEvent.setup()
    render(<App />)

    await screen.findByRole('heading', {
      level: 1,
      name: 'Sources',
    })
    expect(
      screen.queryByRole('heading', {
        level: 2,
        name: 'Video workspace',
      }),
    ).not.toBeInTheDocument()

    await user.click(
      screen.getAllByRole('button', {
        name: 'Add video',
      })[0]!,
    )
    expect(
      await screen.findByRole('heading', {
        level: 2,
        name: 'Video workspace',
      }),
    ).toBeVisible()

    await user.click(
      document.querySelector<HTMLButtonElement>(
        '.video-workspace-close',
      )!,
    )
    await waitFor(() => {
      expect(
        screen.queryByRole('heading', {
          level: 2,
          name: 'Video workspace',
        }),
      ).not.toBeInTheDocument()
    })
  })

  it('opens card details with focus and makes a closed drawer inert', async () => {
    const user = userEvent.setup()
    render(<App />)

    await screen.findByRole('heading', {
      level: 1,
      name: 'Sources',
    })
    await user.click(screen.getByRole('link', { name: 'Studio' }))
    await user.click(
      await screen.findByRole('button', {
        name: 'Open Gradient descent',
      }),
    )

    const closeButton = await waitFor(() => {
      const button =
        document.querySelector<HTMLButtonElement>(
          '.rail-card-detail .card-rail-header button',
        )
      expect(button).toHaveTextContent('Close')
      return button!
    })
    await waitFor(() => {
      expect(document.activeElement).toBe(closeButton)
    })
    const drawer = document.getElementById(
      'course-card-rail-content',
    )
    expect(drawer).toHaveAttribute('aria-hidden', 'false')
    expect(drawer).not.toHaveAttribute('inert')

    await user.keyboard('{Escape}')

    await waitFor(() => {
      expect(drawer).toHaveAttribute('aria-hidden', 'true')
      expect(drawer).toHaveAttribute('inert')
      expect(document.activeElement).toBe(
        document.querySelector<HTMLButtonElement>(
          '.card-rail-tab',
        ),
      )
    })
  })

  it('ignores a late card index after the active course changes', async () => {
    const courseA = {
      ...COURSE,
      id: 'course-a',
      title: 'Course A',
    }
    const courseB = {
      ...COURSE,
      id: 'course-b',
      title: 'Course B',
    }
    const cardA = {
      ...CARD_INDEX,
      id: 'card-a',
      title: 'Stale A card',
      job_id: 'job-a',
    }
    const cardB = {
      ...CARD_INDEX,
      id: 'card-b',
      title: 'Current B card',
      job_id: 'job-b',
    }
    let resolveCourseA:
      | ((response: Response) => void)
      | undefined
    const courseAResponse = new Promise<Response>((resolve) => {
      resolveCourseA = resolve
    })

    window.history.replaceState(
      {},
      '',
      '/?view=studio&tool=cards&course=course-a',
    )
    installBackendMock((path) => {
      if (path === '/courses') {
        return Promise.resolve(
          jsonResponse([courseA, courseB]),
        )
      }
      if (path === '/courses/course-a/jobs') {
        return Promise.resolve(jsonResponse([]))
      }
      if (path === '/courses/course-a/card-index') {
        return courseAResponse
      }
      if (path === '/courses/course-b/jobs') {
        return Promise.resolve(jsonResponse([]))
      }
      if (path === '/courses/course-b/card-index') {
        return Promise.resolve(jsonResponse([cardB]))
      }
      return undefined
    })

    const user = userEvent.setup()
    render(<App />)
    const courseSelect =
      (await screen.findByLabelText('Course')) as HTMLSelectElement
    await waitFor(() => {
      expect(
        Array.from(courseSelect.options).some(
          (option) => option.value === 'course-b',
        ),
      ).toBe(true)
    })
    await user.selectOptions(courseSelect, 'course-b')

    expect(
      await screen.findByText('Current B card'),
    ).toBeInTheDocument()

    resolveCourseA?.(jsonResponse([cardA]))

    await waitFor(() => {
      expect(
        screen.getByText('Current B card'),
      ).toBeInTheDocument()
      expect(
        screen.queryByText('Stale A card'),
      ).not.toBeInTheDocument()
    })
    expect(courseSelect).toHaveValue('course-b')
    expect(window.location.search).toContain('course=course-b')
  })
})
