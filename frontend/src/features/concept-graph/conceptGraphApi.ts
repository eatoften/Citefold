import type {
  GraphDirectionMode,
  GraphVersionMetadata,
  LearningPathResult,
  LocalGraphResult,
  PublishedConcept,
  PublishedConceptPage,
  RelationshipTraceResult,
} from './conceptGraphTypes'

type ApiErrorDetail = {
  code?: unknown
  message?: unknown
  version?: unknown
}

export class ConceptGraphApiError extends Error {
  readonly status: number
  readonly code: string | null
  readonly version: GraphVersionMetadata | null

  constructor(
    message: string,
    status: number,
    code: string | null = null,
    version: GraphVersionMetadata | null = null,
  ) {
    super(message)
    this.name = 'ConceptGraphApiError'
    this.status = status
    this.code = code
    this.version = version
  }
}

function pathId(value: string): string {
  return encodeURIComponent(value)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

async function request<T>(
  apiBaseUrl: string,
  path: string,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(
    `${apiBaseUrl.replace(/\/$/, '')}${path}`,
    { headers: { Accept: 'application/json' }, signal },
  )
  if (response.ok) return response.json() as Promise<T>

  let message = `Request failed with HTTP ${response.status}.`
  let code: string | null = null
  let version: GraphVersionMetadata | null = null
  try {
    const payload: unknown = await response.json()
    if (isRecord(payload) && 'detail' in payload) {
      if (typeof payload.detail === 'string') {
        message = payload.detail
      } else if (isRecord(payload.detail)) {
        const detail: ApiErrorDetail = payload.detail
        if (typeof detail.message === 'string') message = detail.message
        if (typeof detail.code === 'string') code = detail.code
        if (isRecord(detail.version)) {
          version = detail.version as GraphVersionMetadata
        }
      }
    }
  } catch {
    // Retain the status fallback when the server did not return JSON.
  }
  throw new ConceptGraphApiError(message, response.status, code, version)
}

export function getCurrentGraphVersion(
  apiBaseUrl: string,
  courseId: string,
  signal?: AbortSignal,
): Promise<GraphVersionMetadata> {
  return request(
    apiBaseUrl,
    `/courses/${pathId(courseId)}/concept-graph/versions/current`,
    signal,
  )
}

export async function listAllPublishedConcepts(
  apiBaseUrl: string,
  courseId: string,
  versionNumber: number,
  signal?: AbortSignal,
): Promise<PublishedConcept[]> {
  const concepts: PublishedConcept[] = []
  const seenCursors = new Set<string>()
  let cursor: string | null = null
  do {
    const parameters = new URLSearchParams({ limit: '50' })
    if (cursor) parameters.set('cursor', cursor)
    const page = await request<PublishedConceptPage>(
      apiBaseUrl,
      `/courses/${pathId(courseId)}/concept-graph/versions/${versionNumber}/concepts?${parameters}`,
      signal,
    )
    concepts.push(...page.items)
    if (page.next_cursor && seenCursors.has(page.next_cursor)) {
      throw new ConceptGraphApiError(
        'Concept pagination returned a repeated cursor.',
        500,
      )
    }
    if (page.next_cursor) seenCursors.add(page.next_cursor)
    cursor = page.next_cursor
  } while (cursor)
  return concepts
}

function pathParameters(
  values: Record<string, string | number>,
): URLSearchParams {
  return new URLSearchParams(
    Object.entries(values).map(([key, value]) => [key, String(value)]),
  )
}

export function getLocalGraph(
  apiBaseUrl: string,
  courseId: string,
  versionNumber: number,
  options: {
    rootConceptId: string
    directionMode: GraphDirectionMode
    maxHops: number
    signal?: AbortSignal
  },
): Promise<LocalGraphResult> {
  const parameters = pathParameters({
    root_concept_id: options.rootConceptId,
    direction_mode: options.directionMode,
    max_hops: options.maxHops,
  })
  return request(
    apiBaseUrl,
    `/courses/${pathId(courseId)}/concept-graph/versions/${versionNumber}/paths/local?${parameters}`,
    options.signal,
  )
}

export function getRelationshipTrace(
  apiBaseUrl: string,
  courseId: string,
  versionNumber: number,
  options: {
    sourceConceptId: string
    targetConceptId: string
    directionMode: GraphDirectionMode
    maxHops: number
    signal?: AbortSignal
  },
): Promise<RelationshipTraceResult> {
  const parameters = pathParameters({
    source_concept_id: options.sourceConceptId,
    target_concept_id: options.targetConceptId,
    direction_mode: options.directionMode,
    max_hops: options.maxHops,
  })
  return request(
    apiBaseUrl,
    `/courses/${pathId(courseId)}/concept-graph/versions/${versionNumber}/paths/trace?${parameters}`,
    options.signal,
  )
}

export function getLearningPath(
  apiBaseUrl: string,
  courseId: string,
  versionNumber: number,
  targetConceptId: string,
  signal?: AbortSignal,
): Promise<LearningPathResult> {
  const parameters = pathParameters({ target_concept_id: targetConceptId })
  return request(
    apiBaseUrl,
    `/courses/${pathId(courseId)}/concept-graph/versions/${versionNumber}/paths/learning?${parameters}`,
    signal,
  )
}
