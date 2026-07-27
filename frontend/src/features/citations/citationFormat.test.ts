import { describe, expect, it } from 'vitest'
import type { SourceLocator } from '../sources/sourceTypes'
import {
  formatCitationTime,
  formatSourceLocator,
  pdfUrlAtPage,
  resolveCitationMediaUrl,
  restoreCitationFocus,
  segmentCitationQuote,
  seekMediaToLocator,
} from './citationFormat'

const metadata = {}

describe('citation locator formatting', () => {
  it('formats every supported locator kind', () => {
    const cases: Array<[SourceLocator, string]> = [
      [
        {
          schema_version: 1,
          kind: 'video_time',
          job_id: 'job-1',
          asset_id: null,
          start_seconds: 65,
          end_seconds: 70,
          segment_ids: [1],
          metadata,
        },
        '1:05–1:10',
      ],
      [
        {
          schema_version: 1,
          kind: 'pdf_page',
          asset_id: 'pdf-1',
          page_number: 7,
          metadata,
        },
        'Page 7',
      ],
      [
        {
          schema_version: 1,
          kind: 'ppt_slide',
          asset_id: 'slides-1',
          slide_number: 4,
          metadata,
        },
        'Slide 4',
      ],
      [
        {
          schema_version: 1,
          kind: 'docx_paragraph',
          asset_id: 'doc-1',
          paragraph_number: 12,
          metadata,
        },
        'Paragraph 12',
      ],
      [
        {
          schema_version: 1,
          kind: 'text_section',
          asset_id: 'text-1',
          section_number: 3,
          metadata,
        },
        'Section 3',
      ],
    ]

    for (const [locator, expected] of cases) {
      expect(formatSourceLocator(locator)).toBe(expected)
    }
    expect(formatCitationTime(3661)).toBe('1:01:01')
  })

  it('falls back safely for an unknown locator schema', () => {
    expect(
      formatSourceLocator({
        schema_version: 3,
        kind: 'epub_chapter',
        chapter_number: 9,
        metadata,
      }),
    ).toBe('Unsupported locator v3')
    expect(
      formatSourceLocator({
        schema_version: 2,
        kind: 'pdf_page',
        asset_id: 'future-pdf',
        page_number: 9,
        metadata,
      }),
    ).toBe('Unsupported locator v2')
  })

  it('resolves citation-scoped media URLs and PDF pages', () => {
    const resolved = resolveCitationMediaUrl(
      'http://127.0.0.1:8001',
      '/courses/course-1/chat/citations/citation-1/content',
      'course-1',
      'citation-1',
    )
    expect(resolved).toBe(
      'http://127.0.0.1:8001/courses/course-1/chat/citations/citation-1/content',
    )
    expect(
      pdfUrlAtPage(resolved!, {
        schema_version: 1,
        kind: 'pdf_page',
        asset_id: 'pdf-1',
        page_number: 8,
        metadata,
      }),
    ).toBe(
      'http://127.0.0.1:8001/courses/course-1/chat/citations/citation-1/content#page=8',
    )
    expect(
      resolveCitationMediaUrl(
        'http://127.0.0.1:8001',
        'javascript:alert(1)',
        'course-1',
        'citation-1',
      ),
    ).toBeNull()
    expect(
      resolveCitationMediaUrl(
        'http://127.0.0.1:8001',
        'https://example.com/courses/course-1/chat/citations/citation-1/content',
        'course-1',
        'citation-1',
      ),
    ).toBeNull()
    expect(
      resolveCitationMediaUrl(
        'http://127.0.0.1:8001',
        '/courses/course-2/chat/citations/citation-1/content',
        'course-1',
        'citation-1',
      ),
    ).toBeNull()
    expect(
      pdfUrlAtPage(resolved!, {
        schema_version: 2,
        kind: 'pdf_page',
        asset_id: 'future-pdf',
        page_number: 8,
        metadata,
      }),
    ).toBe(resolved)
  })

  it('seeks only valid time locators', () => {
    const media = { currentTime: 0 }
    expect(
      seekMediaToLocator(media, {
        schema_version: 1,
        kind: 'video_time',
        job_id: 'job-1',
        asset_id: null,
        start_seconds: 42.5,
        end_seconds: 48,
        segment_ids: [],
        metadata,
      }),
    ).toBe(true)
    expect(media.currentTime).toBe(42.5)
    expect(
      seekMediaToLocator(media, {
        schema_version: 2,
        kind: 'video_time',
        job_id: 'future-job',
        asset_id: null,
        start_seconds: 99,
        end_seconds: 100,
        segment_ids: [],
        metadata,
      }),
    ).toBe(false)
    expect(media.currentTime).toBe(42.5)
  })
})

describe('citation text and focus helpers', () => {
  it('segments the exact quote without creating HTML', () => {
    expect(
      segmentCitationQuote(
        'Before the cited evidence and after.',
        'the cited evidence',
      ),
    ).toEqual([
      { text: 'Before ', isMatch: false },
      { text: 'the cited evidence', isMatch: true },
      { text: ' and after.', isMatch: false },
    ])
  })

  it('restores focus only to a connected element', () => {
    const button = document.createElement('button')
    document.body.append(button)
    expect(restoreCitationFocus(button)).toBe(true)
    expect(button).toHaveFocus()
    button.remove()
    expect(restoreCitationFocus(button)).toBe(false)
  })
})
