import type {
  DraftConcept,
  DraftConceptEditRequest,
  DraftConceptPage,
  DraftConceptReviewRequest,
  DraftConceptSummary,
  DraftRelation,
  DraftRelationEditRequest,
  DraftRelationPage,
  DraftRelationReviewRequest,
  DraftRelationSummary,
  GraphDirectionMode,
  GraphPublicationPreview,
  GraphPublicationRequest,
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

type MutationOptions = {
  method: 'POST' | 'PATCH'
  body: unknown
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
  mutation?: MutationOptions,
): Promise<T> {
  const init: RequestInit = {
    headers: mutation
      ? {
          Accept: 'application/json',
          'Content-Type': 'application/json',
        }
      : { Accept: 'application/json' },
    signal,
  }
  if (mutation) {
    init.method = mutation.method
    init.body = JSON.stringify(mutation.body)
  }
  const response = await fetch(
    `${apiBaseUrl.replace(/\/$/, '')}${path}`,
    init,
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

export async function listAllDraftConcepts(
  apiBaseUrl: string,
  courseId: string,
  signal?: AbortSignal,
): Promise<DraftConceptSummary[]> {
  const concepts: DraftConceptSummary[] = []
  const seenCursors = new Set<string>()
  let cursor: string | null = null
  do {
    const parameters = new URLSearchParams({ limit: '20' })
    if (cursor) parameters.set('cursor', cursor)
    const page = await request<DraftConceptPage>(
      apiBaseUrl,
      `/courses/${pathId(courseId)}/concepts?${parameters}`,
      signal,
    )
    concepts.push(...page.items)
    if (page.next_cursor && seenCursors.has(page.next_cursor)) {
      throw new ConceptGraphApiError(
        'Draft Concept pagination returned a repeated cursor.',
        500,
      )
    }
    if (page.next_cursor) seenCursors.add(page.next_cursor)
    cursor = page.next_cursor
  } while (cursor)
  return concepts
}

export function getDraftConcept(
  apiBaseUrl: string,
  courseId: string,
  conceptId: string,
  signal?: AbortSignal,
): Promise<DraftConcept> {
  return request(
    apiBaseUrl,
    `/courses/${pathId(courseId)}/concepts/${pathId(conceptId)}`,
    signal,
  )
}

export function editDraftConcept(
  apiBaseUrl: string,
  courseId: string,
  conceptId: string,
  payload: DraftConceptEditRequest,
  signal?: AbortSignal,
): Promise<DraftConcept> {
  return request(
    apiBaseUrl,
    `/courses/${pathId(courseId)}/concepts/${pathId(conceptId)}`,
    signal,
    { method: 'PATCH', body: payload },
  )
}

export function reviewDraftConcept(
  apiBaseUrl: string,
  courseId: string,
  conceptId: string,
  payload: DraftConceptReviewRequest,
  signal?: AbortSignal,
): Promise<DraftConcept> {
  return request(
    apiBaseUrl,
    `/courses/${pathId(courseId)}/concepts/${pathId(conceptId)}/review`,
    signal,
    { method: 'POST', body: payload },
  )
}

export async function listAllDraftRelations(
  apiBaseUrl: string,
  courseId: string,
  signal?: AbortSignal,
): Promise<DraftRelationSummary[]> {
  const relations: DraftRelationSummary[] = []
  const seenCursors = new Set<string>()
  let cursor: string | null = null
  do {
    const parameters = new URLSearchParams({ limit: '20' })
    if (cursor) parameters.set('cursor', cursor)
    const page = await request<DraftRelationPage>(
      apiBaseUrl,
      `/courses/${pathId(courseId)}/concept-relations?${parameters}`,
      signal,
    )
    relations.push(...page.items)
    if (page.next_cursor && seenCursors.has(page.next_cursor)) {
      throw new ConceptGraphApiError(
        'Draft relation pagination returned a repeated cursor.',
        500,
      )
    }
    if (page.next_cursor) seenCursors.add(page.next_cursor)
    cursor = page.next_cursor
  } while (cursor)
  return relations
}

export function getDraftRelation(
  apiBaseUrl: string,
  courseId: string,
  relationId: string,
  signal?: AbortSignal,
): Promise<DraftRelation> {
  return request(
    apiBaseUrl,
    `/courses/${pathId(courseId)}/concept-relations/${pathId(relationId)}`,
    signal,
  )
}

export function editDraftRelation(
  apiBaseUrl: string,
  courseId: string,
  relationId: string,
  payload: DraftRelationEditRequest,
  signal?: AbortSignal,
): Promise<DraftRelation> {
  return request(
    apiBaseUrl,
    `/courses/${pathId(courseId)}/concept-relations/${pathId(relationId)}`,
    signal,
    { method: 'PATCH', body: payload },
  )
}

export function reviewDraftRelation(
  apiBaseUrl: string,
  courseId: string,
  relationId: string,
  payload: DraftRelationReviewRequest,
  signal?: AbortSignal,
): Promise<DraftRelation> {
  return request(
    apiBaseUrl,
    `/courses/${pathId(courseId)}/concept-relations/${pathId(relationId)}/review`,
    signal,
    { method: 'POST', body: payload },
  )
}

export function getGraphPublicationPreview(
  apiBaseUrl: string,
  courseId: string,
  signal?: AbortSignal,
): Promise<GraphPublicationPreview> {
  return request(
    apiBaseUrl,
    `/courses/${pathId(courseId)}/concept-graph/publication-preview`,
    signal,
  )
}

export function publishGraphVersion(
  apiBaseUrl: string,
  courseId: string,
  payload: GraphPublicationRequest,
  signal?: AbortSignal,
): Promise<GraphVersionMetadata> {
  return request(
    apiBaseUrl,
    `/courses/${pathId(courseId)}/concept-graph/versions`,
    signal,
    { method: 'POST', body: payload },
  )
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
