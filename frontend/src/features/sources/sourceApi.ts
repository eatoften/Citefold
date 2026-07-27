import type {
  CourseSource,
  CourseSourceChunk,
  SourceAssetImportResult,
  SourceIndexResult,
} from './sourceTypes'
import {
  enqueueSourceImport,
  enqueueSourceIndex,
  type ReliableTask,
  type SourceAssetTaskResponse,
} from '../reliability'

export type SourceImportTaskResult = {
  import: SourceAssetImportResult
}

export type SourceIndexTaskResult = {
  index: SourceIndexResult
}

export type StagedSourceAsset =
  SourceAssetImportResult['asset']

export class SourceApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'SourceApiError'
    this.status = status
  }
}

type RequestOptions = {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'
  body?: unknown
  formData?: FormData
  signal?: AbortSignal
}

function pathId(value: string): string {
  return encodeURIComponent(value)
}

async function request<T>(
  apiBaseUrl: string,
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const hasJsonBody = options.body !== undefined
  const response = await fetch(
    `${apiBaseUrl.replace(/\/$/, '')}${path}`,
    {
      method: options.method ?? 'GET',
      headers: options.formData
        ? { Accept: 'application/json' }
        : hasJsonBody
          ? {
              Accept: 'application/json',
              'Content-Type': 'application/json',
            }
          : { Accept: 'application/json' },
      body: options.formData
        ? options.formData
        : hasJsonBody
          ? JSON.stringify(options.body)
          : undefined,
      signal: options.signal,
    },
  )

  if (!response.ok) {
    let message = `Request failed with HTTP ${response.status}.`
    try {
      const payload: unknown = await response.json()
      if (
        typeof payload === 'object' &&
        payload !== null &&
        'detail' in payload &&
        typeof payload.detail === 'string'
      ) {
        message = payload.detail
      }
    } catch {
      // Keep the status-based fallback for non-JSON failures.
    }
    throw new SourceApiError(message, response.status)
  }

  if (response.status === 204) {
    return undefined as T
  }
  return response.json() as Promise<T>
}

export function listCourseSources(
  apiBaseUrl: string,
  courseId: string,
  signal?: AbortSignal,
): Promise<CourseSource[]> {
  return request(
    apiBaseUrl,
    `/courses/${pathId(courseId)}/sources`,
    { signal },
  )
}

export function listSourceChunks(
  apiBaseUrl: string,
  sourceId: string,
  options: {
    limit?: number
    offset?: number
    signal?: AbortSignal
  } = {},
): Promise<CourseSourceChunk[]> {
  const params = new URLSearchParams({
    limit: String(options.limit ?? 50),
    offset: String(options.offset ?? 0),
  })
  return request(
    apiBaseUrl,
    `/sources/${pathId(sourceId)}/chunks?${params}`,
    { signal: options.signal },
  )
}

export function updateSourceEnabled(
  apiBaseUrl: string,
  sourceId: string,
  enabled: boolean,
): Promise<CourseSource> {
  return request(
    apiBaseUrl,
    `/sources/${pathId(sourceId)}`,
    {
      method: 'PATCH',
      body: { enabled },
    },
  )
}

export function indexCourseSources(
  apiBaseUrl: string,
  courseId: string,
  sourceIds: string[],
): Promise<SourceIndexResult> {
  return request(
    apiBaseUrl,
    `/courses/${pathId(courseId)}/sources/index`,
    {
      method: 'POST',
      body: { source_ids: sourceIds },
    },
  )
}

export function startCourseSourceIndexTask(
  apiBaseUrl: string,
  courseId: string,
  sourceIds: string[],
): Promise<ReliableTask<SourceIndexTaskResult>> {
  return enqueueSourceIndex(
    apiBaseUrl,
    courseId,
    sourceIds,
  ) as Promise<ReliableTask<SourceIndexTaskResult>>
}

export function importCourseSource(
  apiBaseUrl: string,
  courseId: string,
  file: File,
): Promise<SourceAssetImportResult> {
  const formData = new FormData()
  formData.set('file', file)
  return request(
    apiBaseUrl,
    `/courses/${pathId(courseId)}/source-assets`,
    {
      method: 'POST',
      formData,
    },
  )
}

export function startCourseSourceImportTask(
  apiBaseUrl: string,
  courseId: string,
  file: File,
): Promise<SourceAssetTaskResponse<StagedSourceAsset>> {
  return enqueueSourceImport<StagedSourceAsset>(
    apiBaseUrl,
    courseId,
    file,
  )
}

export function deleteSourceAsset(
  apiBaseUrl: string,
  assetId: string,
): Promise<void> {
  return request(
    apiBaseUrl,
    `/source-assets/${pathId(assetId)}`,
    { method: 'DELETE' },
  )
}
