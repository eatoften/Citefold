export const PRIMARY_VIEWS = ['sources', 'chat', 'studio'] as const

export type PrimaryView = (typeof PRIMARY_VIEWS)[number]

export const STUDIO_TOOLS = [
  'notes',
  'cards',
  'study',
  'review',
  'map',
  'explore',
] as const

export type StudioTool = (typeof STUDIO_TOOLS)[number]

export type AppRoute = {
  view: PrimaryView
  tool: StudioTool | null
  courseId: string | null
  cardId: string | null
  noteId: string | null
  documentId: string | null
  conversationId: string | null
  sourceId: string | null
  jobId: string | null
}

export type AppRouteDestination = {
  view: PrimaryView
  tool?: StudioTool
  courseId?: string | null
  cardId?: string | null
  noteId?: string | null
  documentId?: string | null
  conversationId?: string | null
  sourceId?: string | null
  jobId?: string | null
}

export type CanonicalAppRoute = {
  route: AppRoute
  url: URL
  shouldReplace: boolean
}

const RELATIVE_URL_BASE = 'http://localhost/'

function copyUrl(input: URL | string): URL {
  return input instanceof URL
    ? new URL(input.toString())
    : new URL(input, RELATIVE_URL_BASE)
}

function isPrimaryView(value: unknown): value is PrimaryView {
  return (
    typeof value === 'string' &&
    (PRIMARY_VIEWS as readonly string[]).includes(value)
  )
}

function isStudioTool(value: unknown): value is StudioTool {
  return (
    typeof value === 'string' &&
    (STUDIO_TOOLS as readonly string[]).includes(value)
  )
}

function queryId(
  parameters: URLSearchParams,
  name: string,
): string | null {
  const value = parameters.get(name)
  return value !== null && value.trim() ? value : null
}

function normalizedId(value: string | null | undefined): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

function resolveView(
  parameters: URLSearchParams,
): Pick<AppRoute, 'view' | 'tool'> {
  const view = parameters.get('view')
  const cardId = queryId(parameters, 'card')

  if (view === null && cardId) {
    return { view: 'studio', tool: 'cards' }
  }

  if (isPrimaryView(view)) {
    if (view !== 'studio') {
      return { view, tool: null }
    }
    const tool = parameters.get('tool')
    return {
      view: 'studio',
      tool: isStudioTool(tool) ? tool : 'cards',
    }
  }

  switch (view) {
    case 'workspace':
      return cardId
        ? { view: 'studio', tool: 'cards' }
        : { view: 'sources', tool: null }
    case 'course-map':
      return { view: 'studio', tool: 'map' }
    case 'study':
      return { view: 'studio', tool: 'study' }
    case 'review':
      return { view: 'studio', tool: 'review' }
    case 'graph':
      return { view: 'studio', tool: 'explore' }
    default:
      return { view: 'sources', tool: null }
  }
}

function normalizeRoute(route: AppRoute): AppRoute {
  const view = isPrimaryView(route.view) ? route.view : 'sources'
  const courseId = normalizedId(route.courseId)

  if (view === 'sources') {
    return {
      view,
      tool: null,
      courseId,
      cardId: null,
      noteId: null,
      documentId: null,
      conversationId: null,
      sourceId: normalizedId(route.sourceId),
      jobId: normalizedId(route.jobId),
    }
  }

  if (view === 'chat') {
    return {
      view,
      tool: null,
      courseId,
      cardId: null,
      noteId: null,
      documentId: null,
      conversationId: normalizedId(route.conversationId),
      sourceId: null,
      jobId: null,
    }
  }

  const tool = isStudioTool(route.tool) ? route.tool : 'cards'
  const supportsCard =
    tool === 'cards' ||
    tool === 'study' ||
    tool === 'map' ||
    tool === 'explore'

  return {
    view,
    tool,
    courseId,
    cardId: supportsCard ? normalizedId(route.cardId) : null,
    noteId:
      tool === 'notes' ? normalizedId(route.noteId) : null,
    documentId:
      tool === 'study' ? normalizedId(route.documentId) : null,
    conversationId: null,
    sourceId: null,
    jobId: null,
  }
}

function parseUrl(input: URL | string): AppRoute {
  const url = copyUrl(input)
  const parameters = url.searchParams
  const resolvedView = resolveView(parameters)

  return normalizeRoute({
    ...resolvedView,
    courseId: queryId(parameters, 'course'),
    cardId: queryId(parameters, 'card'),
    noteId: queryId(parameters, 'note'),
    documentId: queryId(parameters, 'document'),
    conversationId: queryId(parameters, 'conversation'),
    sourceId: queryId(parameters, 'source'),
    jobId: queryId(parameters, 'job'),
  })
}

function setQueryValue(
  parameters: URLSearchParams,
  name: string,
  value: string | null,
): void {
  const existing = parameters.getAll(name)
  if (value === null) {
    if (existing.length) parameters.delete(name)
    return
  }
  if (existing.length === 1 && existing[0] === value) return
  parameters.delete(name)
  parameters.append(name, value)
}

/**
 * Parse any supported current or legacy URL into canonical route state.
 * This function is pure and never changes the supplied URL or browser history.
 */
export function parseAppRoute(input: URL | string): AppRoute {
  return parseUrl(input)
}

/**
 * Write canonical route state onto a copy of a URL. Query parameters not owned
 * by the route contract are retained.
 */
export function serializeAppRoute(
  input: URL | string,
  route: AppRoute,
): URL {
  const url = copyUrl(input)
  const canonical = normalizeRoute(route)
  const parameters = url.searchParams

  setQueryValue(parameters, 'view', canonical.view)
  setQueryValue(parameters, 'tool', canonical.tool)
  setQueryValue(parameters, 'course', canonical.courseId)
  setQueryValue(parameters, 'card', canonical.cardId)
  setQueryValue(parameters, 'note', canonical.noteId)
  setQueryValue(parameters, 'document', canonical.documentId)
  setQueryValue(
    parameters,
    'conversation',
    canonical.conversationId,
  )
  setQueryValue(parameters, 'source', canonical.sourceId)
  setQueryValue(parameters, 'job', canonical.jobId)

  return url
}

/**
 * Return both parsed route state and its canonical URL. Callers may use
 * shouldReplace to perform one replaceState after startup or popstate.
 */
export function canonicalizeAppRoute(
  input: URL | string,
): CanonicalAppRoute {
  const original = copyUrl(input)
  const route = parseUrl(original)
  const url = serializeAppRoute(original, route)

  return {
    route,
    url,
    shouldReplace: url.toString() !== original.toString(),
  }
}

function destinationValue(
  destination: AppRouteDestination,
  key:
    | 'cardId'
    | 'noteId'
    | 'documentId'
    | 'conversationId'
    | 'sourceId'
    | 'jobId',
  fallback: string | null,
): string | null {
  const value = destination[key]
  return value === undefined ? fallback : normalizedId(value)
}

/**
 * Construct an explicit navigation URL without calling pushState,
 * replaceState, or mutating the supplied URL. Changing course scope clears
 * entity selections unless the destination explicitly supplies replacements.
 */
export function buildAppRouteUrl(
  input: URL | string,
  destination: AppRouteDestination,
): URL {
  const current = parseUrl(input)
  const courseId =
    destination.courseId === undefined
      ? current.courseId
      : normalizedId(destination.courseId)
  const courseChanged =
    destination.courseId !== undefined &&
    courseId !== current.courseId
  const fallback = (value: string | null): string | null =>
    courseChanged ? null : value
  const tool =
    destination.view === 'studio'
      ? destination.tool ??
        (current.view === 'studio' ? current.tool : 'cards')
      : null

  return serializeAppRoute(input, {
    view: destination.view,
    tool,
    courseId,
    cardId: destinationValue(
      destination,
      'cardId',
      fallback(current.cardId),
    ),
    noteId: destinationValue(
      destination,
      'noteId',
      fallback(current.noteId),
    ),
    documentId: destinationValue(
      destination,
      'documentId',
      fallback(current.documentId),
    ),
    conversationId: destinationValue(
      destination,
      'conversationId',
      fallback(current.conversationId),
    ),
    sourceId: destinationValue(
      destination,
      'sourceId',
      fallback(current.sourceId),
    ),
    jobId: destinationValue(
      destination,
      'jobId',
      fallback(current.jobId),
    ),
  })
}
