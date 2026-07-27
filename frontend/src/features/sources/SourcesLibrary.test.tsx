import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ReliableTask } from '../reliability'
import type { CourseSource } from './sourceTypes'
import { SourcesLibrary } from './SourcesLibrary'

const TIMESTAMP = '2026-07-27T10:00:00Z'

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function source(
  values: Partial<CourseSource> & Pick<CourseSource, 'id' | 'title'>,
): CourseSource {
  const { id, title, ...overrides } = values
  return {
    id,
    course_id: 'course-a',
    origin_type: 'video_job',
    origin_id: 'job-a',
    source_type: 'video',
    title,
    content_status: 'ready',
    index_status: 'ready',
    index_model: 'local',
    index_dimension: 3,
    enabled: true,
    chunk_count: 1,
    indexed_chunk_count: 1,
    size_bytes: 1024,
    mime_type: 'video/mp4',
    metadata: {},
    error_message: null,
    index_error: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    indexed_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

const mixedSources = [
  source({ id: 'job:job-a', title: 'Lecture one.mp4' }),
  source({
    id: 'asset:notes-a',
    title: 'Reading notes.pdf',
    origin_type: 'source_asset',
    origin_id: 'notes-a',
    source_type: 'pdf',
    mime_type: 'application/pdf',
    size_bytes: 2048,
    index_status: 'not_indexed',
    indexed_chunk_count: 0,
  }),
]

function task<TResult extends object>(
  values: Partial<ReliableTask<TResult>> & Pick<ReliableTask, 'id' | 'kind'>,
): ReliableTask<TResult> {
  const { id, kind, ...overrides } = values
  return {
    id,
    kind,
    course_id: 'course-a',
    resource_type: null,
    resource_id: null,
    status: 'queued',
    payload: {},
    result: null,
    idempotency_key: null,
    active_key: null,
    priority: 0,
    attempt: 1,
    max_attempts: 3,
    recovery_count: 0,
    progress: {
      current: 0,
      total: null,
      stage: null,
      message: null,
      details: {},
    },
    cancel_requested_at: null,
    worker_id: null,
    error_code: null,
    error_message: null,
    retryable: true,
    available_at: '2026-01-01T00:00:00Z',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    started_at: null,
    completed_at: null,
    heartbeat_at: null,
    ...overrides,
  }
}

describe('SourcesLibrary', () => {
  it('shows video and document sources from one catalog and previews exact chunks', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(mixedSources))
      .mockResolvedValueOnce(
        jsonResponse([
          {
            id: 'source-unit:page-1',
            source_id: 'asset:notes-a',
            origin_type: 'source_unit',
            origin_id: 'page-1',
            chunk_type: 'page',
            ordinal: 0,
            text: 'Gradient descent follows the negative gradient.',
            text_hash: 'a'.repeat(64),
            locator: {
              schema_version: 1,
              kind: 'pdf_page',
              asset_id: 'notes-a',
              page_number: 7,
              metadata: {},
            },
            chunker_version: 'source-unit-v1',
            is_active: true,
            created_at: '2026-01-01T00:00:00Z',
            updated_at: '2026-01-01T00:00:00Z',
          },
        ]),
      )
    vi.stubGlobal('fetch', fetchMock)
    const onSelectSource = vi.fn()

    render(
      <SourcesLibrary
        apiBaseUrl="http://127.0.0.1:8001"
        courses={[{ id: 'course-a', title: 'ML course' }]}
        selectedCourseId="course-a"
        onSelectCourse={vi.fn()}
        onSelectSource={onSelectSource}
      />,
    )

    expect(await screen.findByText('Lecture one.mp4')).toBeVisible()
    expect(screen.getByText('Reading notes.pdf')).toBeVisible()
    expect(screen.getByText('Documents').nextSibling).toHaveTextContent(
      '1',
    )

    await userEvent.click(
      screen.getByText('Reading notes.pdf').closest('button')!,
    )

    expect(onSelectSource).toHaveBeenCalledWith(
      'asset:notes-a',
      'push',
    )
    expect(
      await screen.findByText(
        'Gradient descent follows the negative gradient.',
      ),
    ).toBeVisible()
    expect(screen.getByText('Page 7')).toBeVisible()
  })

  it('imports a document once and refreshes the canonical source list', async () => {
    const importedSource = source({
      id: 'asset:new-notes',
      title: 'new-notes.md',
      origin_type: 'source_asset',
      origin_id: 'new-notes',
      source_type: 'text',
    })
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(
        jsonResponse(
          {
            asset: {
              id: 'new-notes',
              course_id: 'course-a',
              original_filename: 'new-notes.md',
              extraction_status: 'pending',
              unit_count: 0,
            },
            task: task({
              id: 'import-task',
              kind: 'source_import',
            }),
          },
          202,
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          task({
            id: 'import-task',
            kind: 'source_import',
            status: 'succeeded',
            result: {
              import: {
                asset: {
                  id: 'new-notes',
                  course_id: 'course-a',
                  original_filename: 'new-notes.md',
                  extraction_status: 'ready',
                  unit_count: 2,
                },
                units: [{ id: 'one' }, { id: 'two' }],
              },
            },
          }),
        ),
      )
      .mockResolvedValueOnce(jsonResponse([importedSource]))
      .mockResolvedValueOnce(jsonResponse([]))
    vi.stubGlobal('fetch', fetchMock)

    render(
      <SourcesLibrary
        apiBaseUrl="http://127.0.0.1:8001"
        courses={[{ id: 'course-a', title: 'ML course' }]}
        selectedCourseId="course-a"
        onSelectCourse={vi.fn()}
      />,
    )
    await screen.findByText('No sources in this notebook yet')

    const file = new File(['# Notes'], 'new-notes.md', {
      type: 'text/markdown',
    })
    fireEvent.change(screen.getByLabelText('Import source file'), {
      target: { files: [file] },
    })

    await waitFor(() => {
      expect(screen.getAllByText('new-notes.md').length).toBeGreaterThan(0)
    })
    expect(
      screen.getByText(/Added new-notes\.md with 2 extracted sections/),
    ).toBeVisible()
    const importCall = fetchMock.mock.calls.find(
      ([url]) =>
        String(url) ===
        'http://127.0.0.1:8001/courses/course-a/source-asset-tasks',
    )
    expect(importCall?.[1]?.method).toBe('POST')
    expect(importCall?.[1]?.body).toBeInstanceOf(FormData)
  })

  it('shows a failed index task and retries the same durable task', async () => {
    let retried = false
    const readySource = source({
      id: 'asset:notes-a',
      title: 'Reading notes.pdf',
      origin_type: 'source_asset',
      origin_id: 'notes-a',
      source_type: 'pdf',
    })
    const indexResult = {
      source_ids: [readySource.id],
      total_sources: 1,
      unavailable_source_ids: [],
      total_chunks: 2,
      embedded_chunks: 2,
      skipped_chunks: 0,
      model: 'local',
      dimension: 3,
    }
    const fetchMock = vi.fn<typeof fetch>(
      (
        input: RequestInfo | URL,
        init?: RequestInit,
      ): Promise<Response> => {
        const url = String(input)
        if (url.endsWith('/courses/course-a/sources')) {
          return Promise.resolve(jsonResponse([readySource]))
        }
        if (
          url.endsWith('/courses/course-a/source-index-tasks') &&
          init?.method === 'POST'
        ) {
          return Promise.resolve(
            jsonResponse(
              task({
                id: 'index-task',
                kind: 'source_index',
              }),
              202,
            ),
          )
        }
        if (
          url.endsWith('/tasks/index-task/retry') &&
          init?.method === 'POST'
        ) {
          retried = true
          return Promise.resolve(
            jsonResponse(
              task({
                id: 'index-task',
                kind: 'source_index',
                attempt: 2,
              }),
              202,
            ),
          )
        }
        if (url.endsWith('/tasks/index-task')) {
          return Promise.resolve(
            jsonResponse(
              retried
                ? task({
                    id: 'index-task',
                    kind: 'source_index',
                    status: 'succeeded',
                    attempt: 2,
                    result: { index: indexResult },
                    completed_at: TIMESTAMP,
                  })
                : task({
                    id: 'index-task',
                    kind: 'source_index',
                    status: 'failed',
                    error_code: 'source_index_failed',
                    error_message: 'Embedding model unavailable.',
                    completed_at: TIMESTAMP,
                  }),
            ),
          )
        }
        throw new Error(`Unexpected request: ${url}`)
      },
    )
    vi.stubGlobal('fetch', fetchMock)

    render(
      <SourcesLibrary
        apiBaseUrl="http://127.0.0.1:8001"
        courses={[{ id: 'course-a', title: 'ML course' }]}
        selectedCourseId="course-a"
        onSelectCourse={vi.fn()}
      />,
    )

    await userEvent.click(
      await screen.findByRole('button', { name: 'Index ready' }),
    )
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Embedding model unavailable.',
    )

    await userEvent.click(
      screen.getByRole('button', { name: 'Retry task' }),
    )

    expect(
      await screen.findByText(/Indexed 1 source: 2 new or changed chunks/),
    ).toBeVisible()
    expect(
      fetchMock.mock.calls.filter(([request, init]) =>
        String(request).endsWith('/source-index-tasks') &&
        init?.method === 'POST',
      ),
    ).toHaveLength(1)
    expect(
      fetchMock.mock.calls.some(([request, init]) =>
        String(request).endsWith('/tasks/index-task/retry') &&
        init?.method === 'POST',
      ),
    ).toBe(true)
  })

  it('drops late source responses after the course changes', async () => {
    let resolveCourseA:
      | ((response: Response) => void)
      | undefined
    const courseAResponse = new Promise<Response>((resolve) => {
      resolveCourseA = resolve
    })
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockReturnValueOnce(courseAResponse)
      .mockResolvedValueOnce(
        jsonResponse([
          source({
            id: 'job:course-b',
            title: 'Course B lecture',
            course_id: 'course-b',
            origin_id: 'course-b',
          }),
        ]),
      )
    vi.stubGlobal('fetch', fetchMock)

    const view = render(
      <SourcesLibrary
        apiBaseUrl="http://127.0.0.1:8001"
        courses={[
          { id: 'course-a', title: 'Course A' },
          { id: 'course-b', title: 'Course B' },
        ]}
        selectedCourseId="course-a"
        onSelectCourse={vi.fn()}
      />,
    )
    view.rerender(
      <SourcesLibrary
        apiBaseUrl="http://127.0.0.1:8001"
        courses={[
          { id: 'course-a', title: 'Course A' },
          { id: 'course-b', title: 'Course B' },
        ]}
        selectedCourseId="course-b"
        onSelectCourse={vi.fn()}
      />,
    )

    expect(await screen.findByText('Course B lecture')).toBeVisible()
    resolveCourseA?.(
      jsonResponse([
        source({
          id: 'job:course-a',
          title: 'Stale Course A lecture',
        }),
      ]),
    )

    await waitFor(() => {
      expect(
        screen.queryByText('Stale Course A lecture'),
      ).not.toBeInTheDocument()
    })
  })

  it('does not reload a previous course when its import finishes late', async () => {
    let resolveImport:
      | ((response: Response) => void)
      | undefined
    const importResponse = new Promise<Response>((resolve) => {
      resolveImport = resolve
    })
    const courseBSource = source({
      id: 'job:course-b',
      title: 'Course B lecture',
      course_id: 'course-b',
      origin_id: 'course-b',
    })
    const fetchMock = vi.fn<typeof fetch>(
      (
        input: RequestInfo | URL,
        init?: RequestInit,
      ): Promise<Response> => {
        const url = String(input)
        if (
          url.endsWith('/courses/course-a/source-asset-tasks') &&
          init?.method === 'POST'
        ) {
          return importResponse
        }
        if (url.endsWith('/courses/course-a/sources')) {
          return Promise.resolve(jsonResponse([]))
        }
        if (url.endsWith('/courses/course-b/sources')) {
          return Promise.resolve(jsonResponse([courseBSource]))
        }
        throw new Error(`Unexpected request: ${url}`)
      },
    )
    vi.stubGlobal('fetch', fetchMock)

    const view = render(
      <SourcesLibrary
        apiBaseUrl="http://127.0.0.1:8001"
        courses={[
          { id: 'course-a', title: 'Course A' },
          { id: 'course-b', title: 'Course B' },
        ]}
        selectedCourseId="course-a"
        onSelectCourse={vi.fn()}
      />,
    )
    await screen.findByText('No sources in this notebook yet')

    fireEvent.change(screen.getByLabelText('Import source file'), {
      target: {
        files: [
          new File(['old'], 'course-a.md', {
            type: 'text/markdown',
          }),
        ],
      },
    })

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([request, init]) =>
            String(request).endsWith(
              '/courses/course-a/source-asset-tasks',
            ) && init?.method === 'POST',
        ),
      ).toBe(true)
    })

    view.rerender(
      <SourcesLibrary
        apiBaseUrl="http://127.0.0.1:8001"
        courses={[
          { id: 'course-a', title: 'Course A' },
          { id: 'course-b', title: 'Course B' },
        ]}
        selectedCourseId="course-b"
        onSelectCourse={vi.fn()}
      />,
    )
    expect(await screen.findByText('Course B lecture')).toBeVisible()

    resolveImport?.(
      jsonResponse({
        asset: {
          id: 'course-a-asset',
          course_id: 'course-a',
          original_filename: 'course-a.md',
          extraction_status: 'pending',
          unit_count: 0,
        },
        task: task({
          id: 'course-a-import',
          kind: 'source_import',
        }),
      }, 202),
    )

    await waitFor(() => {
      expect(screen.getByText('Course B lecture')).toBeVisible()
      expect(
        screen.queryByText(/Added course-a\.md/),
      ).not.toBeInTheDocument()
    })
    expect(
      fetchMock.mock.calls.filter(([request]) =>
        String(request).endsWith('/courses/course-a/sources'),
      ),
    ).toHaveLength(1)
  })

  it('ignores a late chunk preview after selecting another source', async () => {
    const sourceA = source({
      id: 'source-a',
      title: 'Source A',
    })
    const sourceB = source({
      id: 'source-b',
      title: 'Source B',
    })
    let resolveSourceA:
      | ((response: Response) => void)
      | undefined
    const sourceAChunks = new Promise<Response>((resolve) => {
      resolveSourceA = resolve
    })
    const chunk = (sourceId: string, text: string) => ({
      id: `${sourceId}:chunk-1`,
      source_id: sourceId,
      origin_type: 'source_unit',
      origin_id: `${sourceId}:unit-1`,
      chunk_type: 'page',
      ordinal: 0,
      text,
      text_hash: sourceId.repeat(32).slice(0, 64),
      locator: {
        schema_version: 1,
        kind: 'pdf_page',
        asset_id: sourceId,
        page_number: 1,
        metadata: {},
      },
      chunker_version: 'source-unit-v1',
      is_active: true,
      created_at: TIMESTAMP,
      updated_at: TIMESTAMP,
    })
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>((input) => {
        const url = String(input)
        if (url.endsWith('/courses/course-a/sources')) {
          return Promise.resolve(jsonResponse([sourceA, sourceB]))
        }
        if (url.includes('/sources/source-a/chunks?')) {
          return sourceAChunks
        }
        if (url.includes('/sources/source-b/chunks?')) {
          return Promise.resolve(
            jsonResponse([chunk('source-b', 'Current B chunk')]),
          )
        }
        throw new Error(`Unexpected request: ${url}`)
      }),
    )

    render(
      <SourcesLibrary
        apiBaseUrl="http://127.0.0.1:8001"
        courses={[{ id: 'course-a', title: 'Course A' }]}
        selectedCourseId="course-a"
        onSelectCourse={vi.fn()}
      />,
    )

    await userEvent.click(
      (await screen.findByText('Source A')).closest('button')!,
    )
    await waitFor(() => {
      expect(
        vi.mocked(fetch).mock.calls.some(([request]) =>
          String(request).includes('/sources/source-a/chunks?'),
        ),
      ).toBe(true)
    })
    await userEvent.click(
      screen.getByText('Source B').closest('button')!,
    )
    expect(await screen.findByText('Current B chunk')).toBeVisible()

    await act(async () => {
      resolveSourceA?.(
        jsonResponse([chunk('source-a', 'Stale A chunk')]),
      )
      await Promise.resolve()
    })

    expect(screen.getByText('Current B chunk')).toBeVisible()
    expect(screen.queryByText('Stale A chunk')).not.toBeInTheDocument()
  })

  it('ignores a late task cancellation after the course changes', async () => {
    const sourceA = source({
      id: 'source-a',
      title: 'Source A',
      index_status: 'not_indexed',
      indexed_chunk_count: 0,
    })
    const sourceB = source({
      id: 'source-b',
      title: 'Source B',
      course_id: 'course-b',
      origin_id: 'job-b',
    })
    let resolveCancel:
      | ((response: Response) => void)
      | undefined
    const cancelResponse = new Promise<Response>((resolve) => {
      resolveCancel = resolve
    })
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const url = String(input)
      if (url.endsWith('/courses/course-a/sources')) {
        return Promise.resolve(jsonResponse([sourceA]))
      }
      if (url.endsWith('/courses/course-b/sources')) {
        return Promise.resolve(jsonResponse([sourceB]))
      }
      if (
        url.endsWith('/courses/course-a/source-index-tasks') &&
        init?.method === 'POST'
      ) {
        return Promise.resolve(
          jsonResponse(
            task({
              id: 'course-a-index',
              kind: 'source_index',
              progress: {
                current: 0,
                total: null,
                stage: 'queued',
                message: 'Indexing Course A',
                details: {},
              },
            }),
            202,
          ),
        )
      }
      if (
        url.endsWith('/tasks/course-a-index/cancel') &&
        init?.method === 'POST'
      ) {
        return cancelResponse
      }
      if (url.endsWith('/tasks/course-a-index')) {
        return Promise.resolve(
          jsonResponse(
            task({
              id: 'course-a-index',
              kind: 'source_index',
              status: 'running',
              progress: {
                current: 0,
                total: null,
                stage: 'indexing',
                message: 'Indexing Course A',
                details: {},
              },
            }),
          ),
        )
      }
      throw new Error(`Unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    const courses = [
      { id: 'course-a', title: 'Course A' },
      { id: 'course-b', title: 'Course B' },
    ]
    const view = render(
      <SourcesLibrary
        apiBaseUrl="http://127.0.0.1:8001"
        courses={courses}
        selectedCourseId="course-a"
        onSelectCourse={vi.fn()}
      />,
    )

    await userEvent.click(
      await screen.findByRole('button', { name: 'Index ready' }),
    )
    await userEvent.click(
      await screen.findByRole('button', { name: 'Cancel' }),
    )

    view.rerender(
      <SourcesLibrary
        apiBaseUrl="http://127.0.0.1:8001"
        courses={courses}
        selectedCourseId="course-b"
        onSelectCourse={vi.fn()}
      />,
    )
    expect(await screen.findByText('Source B')).toBeVisible()

    await act(async () => {
      resolveCancel?.(
        jsonResponse(
          task({
            id: 'course-a-index',
            kind: 'source_index',
            status: 'canceling',
            progress: {
              current: 0,
              total: null,
              stage: 'canceling',
              message: 'Stale Course A cancellation',
              details: {},
            },
          }),
        ),
      )
      await Promise.resolve()
    })

    expect(screen.getByText('Source B')).toBeVisible()
    expect(
      screen.queryByText('Stale Course A cancellation'),
    ).not.toBeInTheDocument()
  })
})
