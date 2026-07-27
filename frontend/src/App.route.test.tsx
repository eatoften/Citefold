import {
  act,
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

const tauriApi = vi.hoisted(() => ({
  invoke: vi.fn(),
}))

vi.mock('@tauri-apps/api/core', () => ({
  invoke: tauriApi.invoke,
}))

import {
  checkBackendHealth,
  ensureBackendReady,
} from './backendBootstrap'
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

const VIDEO_JOBS = [
  {
    id: 'job-a',
    course_id: 'course-1',
    video_path: 'A.mp4',
    status: 'completed',
    original_filename: 'A.mp4',
    stored_name: 'A.mp4',
    size_bytes: 1024,
    metadata: null,
    transcript_path: 'A.json',
    error_message: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    started_at: '2026-01-01T00:00:00Z',
    completed_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 'job-b',
    course_id: 'course-1',
    video_path: 'B.mp4',
    status: 'completed',
    original_filename: 'B.mp4',
    stored_name: 'B.mp4',
    size_bytes: 2048,
    metadata: null,
    transcript_path: 'B.json',
    error_message: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    started_at: '2026-01-01T00:00:00Z',
    completed_at: '2026-01-01T00:00:00Z',
  },
]

function transcript(jobId: 'job-a' | 'job-b') {
  const label = jobId === 'job-a' ? 'A' : 'B'
  return {
    language: 'en',
    language_probability: 1,
    duration_seconds: 10,
    segments: [
      {
        start_seconds: 0,
        end_seconds: 10,
        text: `${label} segment`,
      },
    ],
  }
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
    init?: RequestInit,
  ) => Promise<Response> | undefined,
) {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      (
        input: RequestInfo | URL,
        init?: RequestInit,
      ): Promise<Response> => {
        const url = new URL(String(input))
        const path = url.pathname
        const overridden = override?.(path, init)
        if (overridden) return overridden

        if (path === '/health') {
          return Promise.resolve(
            jsonResponse({
              status: 'ok',
              application_id: 'video-course-cards',
              api_version: 1,
              instance_token: null,
            }),
          )
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

function installCardGenerationBackend(
  override: (
    path: string,
    init?: RequestInit,
  ) => Promise<Response> | undefined,
) {
  installBackendMock((path, init) => {
    const overridden = override(path, init)
    if (overridden) return overridden
    if (path === '/courses/course-1/jobs') {
      return Promise.resolve(jsonResponse(VIDEO_JOBS))
    }
    if (path === '/courses/course-1/sources') {
      return Promise.resolve(
        jsonResponse(
          VIDEO_JOBS.map((item) => ({
            id: `job:${item.id}`,
            course_id: item.course_id,
            origin_type: 'video_job',
            origin_id: item.id,
            source_type: 'video',
            title: item.original_filename,
            content_status: 'ready',
            index_status: 'ready',
            index_model: 'local',
            index_dimension: 3,
            enabled: true,
            chunk_count: 1,
            indexed_chunk_count: 1,
            size_bytes: item.size_bytes,
            mime_type: 'video/mp4',
            metadata: {},
            error_message: null,
            index_error: null,
            created_at: item.created_at,
            updated_at: item.updated_at,
            indexed_at: item.updated_at,
          })),
        ),
      )
    }
    if (
      decodeURIComponent(path).startsWith('/sources/job:') &&
      path.endsWith('/chunks')
    ) {
      return Promise.resolve(jsonResponse([]))
    }
    if (
      path === '/jobs/job-a/cards' ||
      path === '/jobs/job-b/cards' ||
      path === '/jobs/job-a/card-generation-runs' ||
      path === '/jobs/job-b/card-generation-runs'
    ) {
      return Promise.resolve(jsonResponse([]))
    }
    if (path === '/jobs/job-a/transcript') {
      return Promise.resolve(jsonResponse(transcript('job-a')))
    }
    if (path === '/jobs/job-b/transcript') {
      return Promise.resolve(jsonResponse(transcript('job-b')))
    }
    if (
      path === '/jobs/job-a/context' ||
      path === '/jobs/job-b/context'
    ) {
      const jobId = path.includes('job-a') ? 'job-a' : 'job-b'
      const nextTranscript = transcript(jobId)
      return Promise.resolve(
        jsonResponse({
          job_id: jobId,
          source_video: `${jobId}.mp4`,
          start_seconds: 0,
          end_seconds: 10,
          segments: nextTranscript.segments,
          text: nextTranscript.segments[0].text,
        }),
      )
    }
    return undefined
  })
}

describe('App source-first shell', () => {
  beforeEach(() => {
    Reflect.deleteProperty(window, '__TAURI_INTERNALS__')
    tauriApi.invoke.mockReset()
    window.history.replaceState(
      {},
      '',
      '/?view=sources',
    )
    installBackendMock()
  })

  it('rejects an unrelated HTTP 200 health response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse({
          status: 'ok',
          application_id: 'another-service',
          api_version: 1,
        }),
      ),
    )

    await expect(
      checkBackendHealth('http://127.0.0.1:8001'),
    ).resolves.toBe(false)
  })

  it('lets Rust verify Tauri ownership before trusting port 8001', async () => {
    Object.defineProperty(window, '__TAURI_INTERNALS__', {
      configurable: true,
      value: {},
    })
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        status: 'ok',
        application_id: 'another-service',
        api_version: 1,
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    tauriApi.invoke.mockRejectedValue(
      'Port 8001 is in use by another service.',
    )

    await expect(
      ensureBackendReady('http://127.0.0.1:8001'),
    ).resolves.toEqual(
      expect.objectContaining({
        phase: 'failed',
        mode: 'sidecar',
        message: 'Port 8001 is in use by another service.',
      }),
    )
    expect(tauriApi.invoke).toHaveBeenCalledWith('ensure_backend')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('accepts only a Rust-verified Tauri backend status contract', async () => {
    Object.defineProperty(window, '__TAURI_INTERNALS__', {
      configurable: true,
      value: {},
    })
    tauriApi.invoke.mockResolvedValue({
      ready: true,
      mode: 'sidecar',
      message: 'Backend ready.',
      application_id: 'video-course-cards',
      api_version: 1,
      identity_verified: true,
    })

    await expect(
      ensureBackendReady('http://127.0.0.1:8001'),
    ).resolves.toEqual({
      phase: 'ready',
      mode: 'sidecar',
      message: 'Backend ready.',
    })
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

  it('aborts and ignores a manual card draft after switching jobs', async () => {
    let resolveDraft:
      | ((response: Response) => void)
      | undefined
    const draftResponse = new Promise<Response>((resolve) => {
      resolveDraft = resolve
    })
    let draftSignal: AbortSignal | undefined
    window.history.replaceState(
      {},
      '',
      '/?view=sources&course=course-1',
    )
    installCardGenerationBackend((path, init) => {
      if (path === '/cards/draft') {
        draftSignal = init?.signal ?? undefined
        return draftResponse
      }
      return undefined
    })

    const user = userEvent.setup()
    render(<App />)
    await user.click(
      (await screen.findAllByRole('button', {
        name: 'Open video',
      }))[0]!,
    )
    await user.click(
      await screen.findByRole('button', { name: /A segment/ }),
    )
    const generateButton = screen.getByRole('button', {
      name: 'Generate from selection',
    })
    await waitFor(() => expect(generateButton).toBeEnabled())
    await user.click(generateButton)
    await waitFor(() => {
      expect(draftSignal).toBeDefined()
    })

    await user.click(
      screen.getByRole('button', { name: /B\.mp4.*completed/ }),
    )
    expect(await screen.findByText('B segment')).toBeVisible()
    expect(draftSignal?.aborted).toBe(true)

    await act(async () => {
      resolveDraft?.(
        jsonResponse({
          job_id: 'job-a',
          source_video: 'A.mp4',
          start_seconds: 0,
          end_seconds: 10,
          provider: 'local',
          model: 'test-model',
          generation_metadata: {
            provider: 'local',
            model: 'test-model',
            elapsed_seconds: 1,
            input_characters: 10,
            selected_context_characters: 10,
            selected_segments_count: 1,
            requested_card_count: 1,
            raw_card_count: 1,
            returned_card_count: 1,
            raw_claim_count: 0,
            grounded_claim_count: 0,
            dropped_claim_count: 0,
            unsupported_terms_count: 0,
            max_context_characters: 100,
            max_selected_segments: 10,
          },
          cards: [
            {
              title: 'Stale A draft',
              summary: 'Must never appear under Job B.',
              key_points: [],
              claims: [],
              unsupported_terms: [],
              question: 'Stale?',
              answer: 'Yes.',
              source_start_seconds: 0,
              source_end_seconds: 10,
            },
          ],
        }),
      )
      await Promise.resolve()
    })

    expect(screen.getByText('B segment')).toBeVisible()
    expect(screen.queryByText('Stale A draft')).not.toBeInTheDocument()
    expect(
      screen.queryByText(/Card generation failed/),
    ).not.toBeInTheDocument()
  })

  it('aborts and ignores an auto-generation start after switching jobs', async () => {
    let resolveAutoStart:
      | ((response: Response) => void)
      | undefined
    const autoStartResponse = new Promise<Response>((resolve) => {
      resolveAutoStart = resolve
    })
    let autoStartSignal: AbortSignal | undefined
    window.history.replaceState(
      {},
      '',
      '/?view=sources&course=course-1',
    )
    installCardGenerationBackend((path, init) => {
      if (path === '/jobs/job-a/cards/auto-generate') {
        autoStartSignal = init?.signal ?? undefined
        return autoStartResponse
      }
      return undefined
    })

    const user = userEvent.setup()
    render(<App />)
    await user.click(
      (await screen.findAllByRole('button', {
        name: 'Open video',
      }))[0]!,
    )
    await screen.findByText('A segment')
    await user.click(screen.getByRole('button', { name: 'Auto' }))
    await user.click(
      screen.getByRole('button', { name: 'Auto generate' }),
    )
    await waitFor(() => {
      expect(autoStartSignal).toBeDefined()
    })

    await user.click(
      screen.getByRole('button', { name: /B\.mp4.*completed/ }),
    )
    expect(await screen.findByText('B segment')).toBeVisible()
    expect(autoStartSignal?.aborted).toBe(true)

    await act(async () => {
      resolveAutoStart?.(
        jsonResponse({
          id: 'run-a',
          job_id: 'job-a',
          mode: 'auto',
          status: 'running',
          model: 'test-model',
          card_count_per_chunk: 2,
          total_chunks: 3,
          completed_chunks: 1,
          succeeded_chunks: 0,
          failed_chunks: 1,
          cards_created: 0,
          error_message: null,
          errors: [
            {
              chunk_id: 'chunk-a',
              chunk_index: 0,
              message: 'Stale auto A issue',
            },
          ],
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
          started_at: '2026-01-01T00:00:00Z',
          completed_at: null,
        }),
      )
      await Promise.resolve()
    })

    expect(screen.getByText('B segment')).toBeVisible()
    expect(
      screen.queryByText('Stale auto A issue'),
    ).not.toBeInTheDocument()
  })
})
