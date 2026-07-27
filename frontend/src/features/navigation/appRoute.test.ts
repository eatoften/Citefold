import { describe, expect, it, vi } from 'vitest'
import {
  buildAppRouteUrl,
  canonicalizeAppRoute,
  parseAppRoute,
  serializeAppRoute,
  type AppRoute,
  type PrimaryView,
  type StudioTool,
} from './appRoute'

const origin = 'https://course.test/app'

function url(query: string): URL {
  return new URL(`${origin}${query}`)
}

describe('parseAppRoute', () => {
  it('parses every canonical primary view', () => {
    expect(
      parseAppRoute(
        url(
          '?view=sources&course=course-1&source=source-1&job=job-1',
        ),
      ),
    ).toEqual({
      view: 'sources',
      tool: null,
      courseId: 'course-1',
      cardId: null,
      noteId: null,
      documentId: null,
      conversationId: null,
      sourceId: 'source-1',
      jobId: 'job-1',
    })
    expect(
      parseAppRoute(
        url(
          '?view=chat&course=course-1&conversation=conversation-1',
        ),
      ),
    ).toEqual({
      view: 'chat',
      tool: null,
      courseId: 'course-1',
      cardId: null,
      noteId: null,
      documentId: null,
      conversationId: 'conversation-1',
      sourceId: null,
      jobId: null,
    })
    expect(
      parseAppRoute(
        url(
          '?view=studio&tool=study&course=course-1&card=card-1&document=document-1',
        ),
      ),
    ).toEqual({
      view: 'studio',
      tool: 'study',
      courseId: 'course-1',
      cardId: 'card-1',
      noteId: null,
      documentId: 'document-1',
      conversationId: null,
      sourceId: null,
      jobId: null,
    })
    expect(
      parseAppRoute(
        url(
          '?view=studio&tool=notes&course=course-1&note=note-1',
        ),
      ),
    ).toEqual({
      view: 'studio',
      tool: 'notes',
      courseId: 'course-1',
      cardId: null,
      noteId: 'note-1',
      documentId: null,
      conversationId: null,
      sourceId: null,
      jobId: null,
    })
  })

  it.each<{
    query: string
    view: PrimaryView
    tool: StudioTool | null
    cardId: string | null
    documentId: string | null
  }>([
    {
      query: '?view=workspace&course=course-1',
      view: 'sources',
      tool: null,
      cardId: null,
      documentId: null,
    },
    {
      query: '?view=workspace&course=course-1&card=card-1',
      view: 'studio',
      tool: 'cards',
      cardId: 'card-1',
      documentId: null,
    },
    {
      query: '?view=course-map&course=course-1&card=card-1',
      view: 'studio',
      tool: 'map',
      cardId: 'card-1',
      documentId: null,
    },
    {
      query:
        '?view=study&course=course-1&card=card-1&document=document-1',
      view: 'studio',
      tool: 'study',
      cardId: 'card-1',
      documentId: 'document-1',
    },
    {
      query: '?view=review&course=course-1&card=card-1',
      view: 'studio',
      tool: 'review',
      cardId: null,
      documentId: null,
    },
    {
      query: '?view=graph&course=course-1&card=card-1',
      view: 'studio',
      tool: 'explore',
      cardId: 'card-1',
      documentId: null,
    },
  ])(
    'maps legacy $query to $view/$tool',
    ({ query, view, tool, cardId, documentId }) => {
      const route = parseAppRoute(url(query))
      expect(route).toMatchObject({
        view,
        tool,
        cardId,
        documentId,
        courseId: 'course-1',
      })
    },
  )

  it('treats an empty legacy workspace card as Sources', () => {
    expect(parseAppRoute(url('?view=workspace&card='))).toMatchObject({
      view: 'sources',
      tool: null,
      cardId: null,
    })
  })

  it('preserves card bookmarks created before view was explicit', () => {
    expect(
      parseAppRoute(
        url('?course=course-1&card=card-1'),
      ),
    ).toMatchObject({
      view: 'studio',
      tool: 'cards',
      courseId: 'course-1',
      cardId: 'card-1',
    })
  })

  it.each<{
    tool: StudioTool
    cardId: string | null
    noteId: string | null
    documentId: string | null
  }>([
    {
      tool: 'notes',
      cardId: null,
      noteId: 'note-1',
      documentId: null,
    },
    {
      tool: 'cards',
      cardId: 'card-1',
      noteId: null,
      documentId: null,
    },
    {
      tool: 'study',
      cardId: 'card-1',
      noteId: null,
      documentId: 'document-1',
    },
    {
      tool: 'review',
      cardId: null,
      noteId: null,
      documentId: null,
    },
    {
      tool: 'map',
      cardId: 'card-1',
      noteId: null,
      documentId: null,
    },
    {
      tool: 'explore',
      cardId: 'card-1',
      noteId: null,
      documentId: null,
    },
  ])(
    'keeps only the entity parameters supported by Studio/$tool',
    ({ tool, cardId, noteId, documentId }) => {
      const route = parseAppRoute(
        url(
          `?view=studio&tool=${tool}&course=course-1&card=card-1&note=note-1&document=document-1&conversation=conversation-1&source=source-1&job=job-1`,
        ),
      )
      expect(route).toMatchObject({
        view: 'studio',
        tool,
        courseId: 'course-1',
        cardId,
        noteId,
        documentId,
        conversationId: null,
        sourceId: null,
        jobId: null,
      })
    },
  )

  it('defaults missing and invalid views to Sources', () => {
    expect(parseAppRoute(url('?course=course-1'))).toMatchObject({
      view: 'sources',
      tool: null,
      courseId: 'course-1',
    })
    expect(
      parseAppRoute(
        url(
          '?view=unknown&course=course-1&source=source-1&card=card-1',
        ),
      ),
    ).toMatchObject({
      view: 'sources',
      tool: null,
      courseId: 'course-1',
      sourceId: 'source-1',
      cardId: null,
    })
  })

  it('defaults a missing or invalid Studio tool to Cards', () => {
    expect(
      parseAppRoute(url('?view=studio&course=course-1&card=card-1')),
    ).toMatchObject({
      view: 'studio',
      tool: 'cards',
      cardId: 'card-1',
    })
    expect(
      parseAppRoute(
        url(
          '?view=studio&tool=unknown&course=course-1&card=card-1',
        ),
      ),
    ).toMatchObject({
      view: 'studio',
      tool: 'cards',
      cardId: 'card-1',
    })
  })

  it('removes empty entity identifiers from route state', () => {
    expect(
      parseAppRoute(
        url('?view=chat&course=%20%20&conversation=%20'),
      ),
    ).toMatchObject({
      view: 'chat',
      courseId: null,
      conversationId: null,
    })
  })
})

describe('canonicalizeAppRoute', () => {
  it('does not replace an already canonical route', () => {
    const current = url(
      '?view=studio&tool=study&course=course-1&card=card-1&document=document-1&mode=preview#evidence',
    )
    const result = canonicalizeAppRoute(current)

    expect(result.shouldReplace).toBe(false)
    expect(result.url.toString()).toBe(current.toString())
  })

  it.each([
    '?view=workspace&course=course-1',
    '?view=course-map&course=course-1',
    '?view=study&course=course-1',
    '?view=review&course=course-1',
    '?view=graph&course=course-1',
    '?course=course-1',
    '?view=invalid&course=course-1',
    '?view=studio&course=course-1',
    '?view=studio&tool=invalid&course=course-1',
  ])('marks legacy, missing, or invalid route %s for replace', (query) => {
    expect(canonicalizeAppRoute(url(query)).shouldReplace).toBe(true)
  })

  it('canonicalizes every legacy alias and retains applicable IDs', () => {
    const workspace = canonicalizeAppRoute(
      url(
        '?view=workspace&course=course-1&card=card-1&debug=1#card',
      ),
    )
    expect(workspace.url.searchParams.get('view')).toBe('studio')
    expect(workspace.url.searchParams.get('tool')).toBe('cards')
    expect(workspace.url.searchParams.get('course')).toBe('course-1')
    expect(workspace.url.searchParams.get('card')).toBe('card-1')
    expect(workspace.url.searchParams.get('debug')).toBe('1')
    expect(workspace.url.hash).toBe('#card')

    const study = canonicalizeAppRoute(
      url(
        '?view=study&course=course-1&card=card-1&document=document-1',
      ),
    )
    expect(study.url.searchParams.get('view')).toBe('studio')
    expect(study.url.searchParams.get('tool')).toBe('study')
    expect(study.url.searchParams.get('document')).toBe('document-1')
  })

  it('removes route-inapplicable entity parameters', () => {
    const chat = canonicalizeAppRoute(
      url(
        '?view=chat&tool=study&course=course-1&conversation=conversation-1&card=card-1&note=note-1&document=document-1&source=source-1&job=job-1',
      ),
    )
    expect(chat.shouldReplace).toBe(true)
    expect(chat.url.searchParams.get('conversation')).toBe(
      'conversation-1',
    )
    expect(chat.url.searchParams.has('tool')).toBe(false)
    expect(chat.url.searchParams.has('card')).toBe(false)
    expect(chat.url.searchParams.has('note')).toBe(false)
    expect(chat.url.searchParams.has('document')).toBe(false)
    expect(chat.url.searchParams.has('source')).toBe(false)
    expect(chat.url.searchParams.has('job')).toBe(false)

    const sources = canonicalizeAppRoute(
      url(
        '?view=sources&tool=cards&course=course-1&source=source-1&job=job-1&conversation=conversation-1&card=card-1&note=note-1',
      ),
    )
    expect(sources.url.searchParams.get('source')).toBe('source-1')
    expect(sources.url.searchParams.get('job')).toBe('job-1')
    expect(sources.url.searchParams.has('conversation')).toBe(false)
    expect(sources.url.searchParams.has('card')).toBe(false)
    expect(sources.url.searchParams.has('note')).toBe(false)
  })

  it('collapses duplicate owned parameters', () => {
    const result = canonicalizeAppRoute(
      url(
        '?view=chat&view=sources&conversation=first&conversation=second&course=course-1',
      ),
    )
    expect(result.shouldReplace).toBe(true)
    expect(result.url.searchParams.getAll('view')).toEqual(['chat'])
    expect(result.url.searchParams.getAll('conversation')).toEqual([
      'first',
    ])
  })

  it('preserves pathname, hash, and unowned query parameters', () => {
    const result = canonicalizeAppRoute(
      new URL(
        'https://course.test/notebook?theme=dark&view=graph&course=course-1&card=card-1&theme=contrast#selected',
      ),
    )
    expect(result.url.pathname).toBe('/notebook')
    expect(result.url.hash).toBe('#selected')
    expect(result.url.searchParams.getAll('theme')).toEqual([
      'dark',
      'contrast',
    ])
    expect(result.url.searchParams.get('view')).toBe('studio')
    expect(result.url.searchParams.get('tool')).toBe('explore')
  })

  it('accepts a relative URL without relying on window.location', () => {
    const result = canonicalizeAppRoute(
      '/notebook?view=review&course=course-1',
    )
    expect(result.route).toMatchObject({
      view: 'studio',
      tool: 'review',
      courseId: 'course-1',
    })
    expect(result.url.pathname).toBe('/notebook')
  })
})

describe('serializeAppRoute', () => {
  const chatRoute: AppRoute = {
    view: 'chat',
    tool: null,
    courseId: 'course-2',
    cardId: 'ignored-card',
    noteId: 'ignored-note',
    documentId: 'ignored-document',
    conversationId: 'conversation-2',
    sourceId: 'ignored-source',
    jobId: 'ignored-job',
  }

  it('serializes normalized route state and preserves other parameters', () => {
    const base = url(
      '?view=studio&tool=study&course=course-1&card=card-1&document=document-1&theme=dark',
    )
    const result = serializeAppRoute(base, chatRoute)

    expect(result.searchParams.get('view')).toBe('chat')
    expect(result.searchParams.has('tool')).toBe(false)
    expect(result.searchParams.get('course')).toBe('course-2')
    expect(result.searchParams.get('conversation')).toBe(
      'conversation-2',
    )
    expect(result.searchParams.has('card')).toBe(false)
    expect(result.searchParams.has('note')).toBe(false)
    expect(result.searchParams.has('document')).toBe(false)
    expect(result.searchParams.has('source')).toBe(false)
    expect(result.searchParams.has('job')).toBe(false)
    expect(result.searchParams.get('theme')).toBe('dark')
  })

  it('does not mutate the base URL or input route', () => {
    const base = url('?view=sources&course=course-1&source=source-1')
    const originalUrl = base.toString()
    const originalRoute = { ...chatRoute }

    serializeAppRoute(base, chatRoute)

    expect(base.toString()).toBe(originalUrl)
    expect(chatRoute).toEqual(originalRoute)
  })
})

describe('buildAppRouteUrl', () => {
  it('builds explicit navigation without touching browser history', () => {
    const pushState = vi.spyOn(window.history, 'pushState')
    const replaceState = vi.spyOn(window.history, 'replaceState')
    const current = url(
      '?view=sources&course=course-1&source=source-1&job=job-1&theme=dark',
    )
    const original = current.toString()

    const result = buildAppRouteUrl(current, {
      view: 'chat',
      conversationId: 'conversation-1',
    })

    expect(result.searchParams.get('view')).toBe('chat')
    expect(result.searchParams.get('course')).toBe('course-1')
    expect(result.searchParams.get('conversation')).toBe(
      'conversation-1',
    )
    expect(result.searchParams.has('source')).toBe(false)
    expect(result.searchParams.has('job')).toBe(false)
    expect(result.searchParams.get('theme')).toBe('dark')
    expect(current.toString()).toBe(original)
    expect(pushState).not.toHaveBeenCalled()
    expect(replaceState).not.toHaveBeenCalled()
  })

  it('preserves the current Studio tool when none is supplied', () => {
    const result = buildAppRouteUrl(
      url(
        '?view=studio&tool=study&course=course-1&card=card-1&document=document-1',
      ),
      { view: 'studio' },
    )
    expect(result.searchParams.get('tool')).toBe('study')
    expect(result.searchParams.get('card')).toBe('card-1')
    expect(result.searchParams.get('document')).toBe('document-1')
  })

  it('defaults a newly opened Studio route to Cards', () => {
    const result = buildAppRouteUrl(
      url('?view=sources&course=course-1&source=source-1'),
      { view: 'studio' },
    )
    expect(result.searchParams.get('view')).toBe('studio')
    expect(result.searchParams.get('tool')).toBe('cards')
    expect(result.searchParams.has('source')).toBe(false)
  })

  it('applies Studio entity semantics while changing tools', () => {
    const current = url(
      '?view=studio&tool=study&course=course-1&card=card-1&document=document-1',
    )
    const explore = buildAppRouteUrl(current, {
      view: 'studio',
      tool: 'explore',
    })
    expect(explore.searchParams.get('card')).toBe('card-1')
    expect(explore.searchParams.has('document')).toBe(false)

    const review = buildAppRouteUrl(current, {
      view: 'studio',
      tool: 'review',
    })
    expect(review.searchParams.has('card')).toBe(false)
    expect(review.searchParams.has('document')).toBe(false)

    const notes = buildAppRouteUrl(current, {
      view: 'studio',
      tool: 'notes',
      noteId: 'note-1',
    })
    expect(notes.searchParams.get('note')).toBe('note-1')
    expect(notes.searchParams.has('card')).toBe(false)
    expect(notes.searchParams.has('document')).toBe(false)
  })

  it('clears old entity scope when the course changes', () => {
    const result = buildAppRouteUrl(
      url(
        '?view=chat&course=course-1&conversation=conversation-1',
      ),
      { view: 'chat', courseId: 'course-2' },
    )
    expect(result.searchParams.get('course')).toBe('course-2')
    expect(result.searchParams.has('conversation')).toBe(false)
  })

  it('accepts an explicit entity for the new course scope', () => {
    const result = buildAppRouteUrl(
      url(
        '?view=studio&tool=study&course=course-1&card=old-card&document=old-document',
      ),
      {
        view: 'studio',
        tool: 'study',
        courseId: 'course-2',
        cardId: 'new-card',
        documentId: 'new-document',
      },
    )
    expect(result.searchParams.get('course')).toBe('course-2')
    expect(result.searchParams.get('card')).toBe('new-card')
    expect(result.searchParams.get('document')).toBe('new-document')
  })

  it('preserves an allowed entity when course scope is unchanged', () => {
    const result = buildAppRouteUrl(
      url(
        '?view=chat&course=course-1&conversation=conversation-1',
      ),
      { view: 'chat', courseId: 'course-1' },
    )
    expect(result.searchParams.get('conversation')).toBe(
      'conversation-1',
    )
  })

  it('clears a selected note when the course scope changes', () => {
    const result = buildAppRouteUrl(
      url(
        '?view=studio&tool=notes&course=course-1&note=note-1',
      ),
      {
        view: 'studio',
        tool: 'notes',
        courseId: 'course-2',
      },
    )
    expect(result.searchParams.get('course')).toBe('course-2')
    expect(result.searchParams.has('note')).toBe(false)
  })
})
