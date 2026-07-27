import {
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { CourseSource } from './sourceTypes'
import { SourcesLibrary } from './SourcesLibrary'

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
              extraction_status: 'ready',
              unit_count: 2,
            },
            units: [{ id: 'one' }, { id: 'two' }],
          },
          201,
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
        'http://127.0.0.1:8001/courses/course-a/source-assets',
    )
    expect(importCall?.[1]?.method).toBe('POST')
    expect(importCall?.[1]?.body).toBeInstanceOf(FormData)
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
          url.endsWith('/courses/course-a/source-assets') &&
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
              '/courses/course-a/source-assets',
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
      jsonResponse(
        {
          asset: {
            id: 'course-a-asset',
            course_id: 'course-a',
            original_filename: 'course-a.md',
            extraction_status: 'ready',
            unit_count: 1,
          },
          units: [{ id: 'unit-a' }],
        },
        201,
      ),
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
})
