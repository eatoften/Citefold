import {
  fireEvent,
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
import type { ChatCitation } from '../chat/chatTypes'
import type { CitationTarget } from './citationTypes'
import { CitationInspector } from './CitationInspector'

const videoLocator = {
  schema_version: 1 as const,
  kind: 'video_time' as const,
  job_id: 'job-1',
  asset_id: null,
  start_seconds: 37.5,
  end_seconds: 43,
  segment_ids: [4],
  metadata: {},
}

function citation(
  overrides: Partial<ChatCitation> = {},
): ChatCitation {
  return {
    id: 'citation-1',
    message_id: 'message-1',
    ordinal: 1,
    sentence_index: 0,
    start_offset: 0,
    end_offset: 20,
    source_id: 'job:job-1',
    chunk_id: 'transcript_chunk:chunk-1',
    chunk_text_hash: 'a'.repeat(64),
    source_title: 'Lecture 1.mp4',
    source_type: 'video',
    quote: 'the exact cited evidence',
    score: 0.91,
    locator: videoLocator,
    created_at: '2026-07-27T00:00:00Z',
    ...overrides,
  }
}

function availableTarget(
  overrides: Partial<CitationTarget> = {},
): CitationTarget {
  return {
    citation_id: 'citation-1',
    availability: 'available',
    reason: null,
    reason_message: null,
    source_id: 'job:job-1',
    source_title: 'Lecture 1.mp4',
    source_type: 'video',
    quote: 'the exact cited evidence',
    locator: videoLocator,
    media_kind: 'video',
    media_url:
      '/courses/course-1/chat/citations/citation-1/content',
    mime_type: 'video/mp4',
    target_chunk_id: 'transcript_chunk:chunk-1',
    context: [
      {
        chunk_id: 'transcript_chunk:chunk-0',
        ordinal: 0,
        text: 'Earlier transcript context.',
        locator: {
          ...videoLocator,
          start_seconds: 30,
          end_seconds: 35,
        },
        is_target: false,
      },
      {
        chunk_id: 'transcript_chunk:chunk-1',
        ordinal: 1,
        text: 'Before the exact cited evidence and after.',
        locator: videoLocator,
        is_target: true,
      },
    ],
    ...overrides,
  }
}

function mockTarget(target: CitationTarget) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify(target), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  )
}

describe('CitationInspector', () => {
  beforeEach(() => {
    mockTarget(availableTarget())
  })

  it('loads a course-scoped target, marks the quote, and focuses it', async () => {
    const onClose = vi.fn()
    render(
      <CitationInspector
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-1"
        citation={citation()}
        onClose={onClose}
      />,
    )

    const mark = await screen.findByText(
      'the exact cited evidence',
      { selector: 'mark' },
    )
    const targetArticle = mark.closest('article')
    expect(targetArticle).toHaveAttribute('aria-current', 'location')
    await waitFor(() => expect(targetArticle).toHaveFocus())
    expect(fetch).toHaveBeenCalledWith(
      'http://127.0.0.1:8001/courses/course-1/chat/citations/citation-1/target',
      expect.objectContaining({
        signal: expect.any(AbortSignal),
      }),
    )
  })

  it('seeks video after metadata loads', async () => {
    render(
      <CitationInspector
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-1"
        citation={citation()}
        onClose={vi.fn()}
      />,
    )

    const media = await screen.findByLabelText(
      'Lecture 1.mp4 at 0:37–0:43',
    )
    expect(media).toHaveAttribute(
      'src',
      'http://127.0.0.1:8001/courses/course-1/chat/citations/citation-1/content',
    )
    fireEvent.loadedMetadata(media)
    expect((media as HTMLVideoElement).currentTime).toBe(37.5)
  })

  it('keeps the saved quote when the request fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new Error('Source file is missing.')),
    )
    render(
      <CitationInspector
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-1"
        citation={citation()}
        onClose={vi.fn()}
      />,
    )

    expect(
      screen.getByText('the exact cited evidence', {
        selector: 'blockquote',
      }),
    ).toBeInTheDocument()
    const errorMessage = await screen.findByText(
      'Source file is missing.',
    )
    expect(errorMessage.closest('[role="alert"]')).toBeInTheDocument()
  })

  it('shows an unknown locator snapshot without crashing', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new Error('Unsupported target.')),
    )
    render(
      <CitationInspector
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-1"
        citation={citation({
          locator: {
            schema_version: 5,
            kind: 'epub_chapter',
            chapter: 2,
            metadata: {},
          },
        })}
        onClose={vi.fn()}
      />,
    )

    expect(
      screen.getByText('Unsupported locator v5'),
    ).toBeInTheDocument()
    expect(
      screen.getByText('the exact cited evidence', {
        selector: 'blockquote',
      }),
    ).toBeInTheDocument()
    const errorMessage = await screen.findByText('Unsupported target.')
    expect(errorMessage.closest('[role="alert"]')).toBeInTheDocument()
  })

  it('closes on Escape', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(
      <CitationInspector
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-1"
        citation={citation()}
        onClose={onClose}
      />,
    )
    await screen.findByText('Source context')
    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalled()
  })

  it('aborts and closes when the course changes', async () => {
    let requestSignal: AbortSignal | undefined
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_input: RequestInfo | URL, init?: RequestInit) => {
          requestSignal = init?.signal ?? undefined
          return new Promise<Response>(() => undefined)
        },
      ),
    )
    const onClose = vi.fn()
    const view = render(
      <CitationInspector
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-1"
        citation={citation()}
        onClose={onClose}
      />,
    )
    await waitFor(() => expect(requestSignal).toBeDefined())

    view.rerender(
      <CitationInspector
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-2"
        citation={citation()}
        onClose={onClose}
      />,
    )

    await waitFor(() => {
      expect(requestSignal?.aborted).toBe(true)
      expect(onClose).toHaveBeenCalled()
    })
  })

  it('explains snapshot-only availability while retaining evidence', async () => {
    mockTarget(
      availableTarget({
        availability: 'snapshot_only',
        reason: 'source_deleted',
        reason_message: 'This source was deleted after the answer.',
        media_kind: null,
        media_url: null,
        context: [],
      }),
    )
    render(
      <CitationInspector
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-1"
        citation={citation()}
        onClose={vi.fn()}
      />,
    )

    expect(
      screen.getByText('the exact cited evidence', {
        selector: 'blockquote',
      }),
    ).toBeInTheDocument()
    expect(
      await screen.findByText(
        'This source was deleted after the answer.',
      ),
    ).toBeInTheDocument()
  })

  it('keeps verified extracted context when only the original file is unavailable', async () => {
    mockTarget(
      availableTarget({
        availability: 'snapshot_only',
        reason: 'file_integrity_mismatch',
        reason_message:
          'The original file no longer matches the imported source.',
        media_kind: 'video',
        media_url: null,
      }),
    )
    render(
      <CitationInspector
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-1"
        citation={citation()}
        onClose={vi.fn()}
      />,
    )

    expect(await screen.findByText('Source context')).toBeInTheDocument()
    expect(
      screen.getByText(
        'Showing the last verified extracted context; the original file is unavailable.',
      ),
    ).toBeInTheDocument()
    expect(
      screen.getByText('the exact cited evidence', {
        selector: 'mark',
      }),
    ).toBeInTheDocument()
  })
})
