import type {
  ReliableTask,
  ReliableTaskStatus,
  SourceAssetTaskResponse,
} from './taskTypes'

export class ReliableTaskApiError extends Error {
  readonly status: number
  readonly code: string | null

  constructor(
    message: string,
    status: number,
    code: string | null = null,
  ) {
    super(message)
    this.name = 'ReliableTaskApiError'
    this.status = status
    this.code = code
  }
}

async function request<T>(
  apiBaseUrl: string,
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(
    `${apiBaseUrl.replace(/\/$/, '')}${path}`,
    {
      ...options,
      headers: {
        Accept: 'application/json',
        ...options.headers,
      },
    },
  )
  if (!response.ok) {
    let message = `Task request failed (${response.status}).`
    let code: string | null = null
    try {
      const payload = (await response.json()) as {
        detail?: string
        error_code?: string
      }
      if (typeof payload.detail === 'string') message = payload.detail
      code = payload.error_code ?? null
    } catch {
      // Keep the status fallback.
    }
    throw new ReliableTaskApiError(message, response.status, code)
  }
  return (await response.json()) as T
}

export function listReliableTasks(
  apiBaseUrl: string,
  options: {
    courseId?: string | null
    statuses?: ReliableTaskStatus[]
    limit?: number
    signal?: AbortSignal
  } = {},
): Promise<ReliableTask[]> {
  const parameters = new URLSearchParams()
  if (options.courseId) {
    parameters.set('course_id', options.courseId)
  }
  for (const status of options.statuses ?? []) {
    parameters.append('task_status', status)
  }
  parameters.set('limit', String(options.limit ?? 100))
  return request(
    apiBaseUrl,
    `/tasks?${parameters.toString()}`,
    { signal: options.signal },
  )
}

export function getReliableTask<TResult extends object>(
  apiBaseUrl: string,
  taskId: string,
  signal?: AbortSignal,
): Promise<ReliableTask<TResult>> {
  return request(
    apiBaseUrl,
    `/tasks/${encodeURIComponent(taskId)}`,
    { signal },
  )
}

export function cancelReliableTask(
  apiBaseUrl: string,
  taskId: string,
): Promise<ReliableTask> {
  return request(
    apiBaseUrl,
    `/tasks/${encodeURIComponent(taskId)}/cancel`,
    { method: 'POST' },
  )
}

export function retryReliableTask(
  apiBaseUrl: string,
  taskId: string,
): Promise<ReliableTask> {
  return request(
    apiBaseUrl,
    `/tasks/${encodeURIComponent(taskId)}/retry`,
    { method: 'POST' },
  )
}

export async function waitForReliableTask<TResult extends object>(
  apiBaseUrl: string,
  taskId: string,
  options: {
    signal?: AbortSignal
    intervalMs?: number
    timeoutMs?: number
    onProgress?: (task: ReliableTask<TResult>) => void
  } = {},
): Promise<ReliableTask<TResult>> {
  const startedAt = Date.now()
  const intervalMs = options.intervalMs ?? 600
  const timeoutMs = options.timeoutMs ?? 10 * 60_000
  while (true) {
    if (options.signal?.aborted) {
      throw new DOMException('Task status request aborted.', 'AbortError')
    }
    const task = await getReliableTask<TResult>(
      apiBaseUrl,
      taskId,
      options.signal,
    )
    options.onProgress?.(task)
    if (task.status === 'succeeded') return task
    if (task.status === 'failed' || task.status === 'canceled') {
      throw new ReliableTaskApiError(
        task.error_message ??
          (task.status === 'canceled'
            ? 'Task canceled.'
            : 'Task failed.'),
        409,
        task.error_code,
      )
    }
    if (Date.now() - startedAt >= timeoutMs) {
      throw new ReliableTaskApiError(
        'The task is still running. Its progress remains available in Activity.',
        408,
      )
    }
    await new Promise<void>((resolve, reject) => {
      const timer = window.setTimeout(resolve, intervalMs)
      options.signal?.addEventListener(
        'abort',
        () => {
          window.clearTimeout(timer)
          reject(
            new DOMException(
              'Task status request aborted.',
              'AbortError',
            ),
          )
        },
        { once: true },
      )
    })
  }
}

export function enqueueSourceIndex(
  apiBaseUrl: string,
  courseId: string,
  sourceIds: string[],
): Promise<ReliableTask> {
  return request(
    apiBaseUrl,
    `/courses/${encodeURIComponent(courseId)}/source-index-tasks`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_ids: sourceIds }),
    },
  )
}
export function enqueueSourceImport<TAsset extends object>(
  apiBaseUrl: string,
  courseId: string,
  file: File,
): Promise<SourceAssetTaskResponse<TAsset>> {
  const formData = new FormData()
  formData.set('file', file)
  return request(
    apiBaseUrl,
    `/courses/${encodeURIComponent(courseId)}/source-asset-tasks`,
    { method: 'POST', body: formData },
  )
}

export function enqueueChatGeneration(
  apiBaseUrl: string,
  conversationId: string,
  body: object,
  signal?: AbortSignal,
): Promise<ReliableTask> {
  return request(
    apiBaseUrl,
    `/chat/conversations/${encodeURIComponent(
      conversationId,
    )}/message-tasks`,
    {
      method: 'POST',
      signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  )
}

export function enqueueLearningDocumentGeneration(
  apiBaseUrl: string,
  documentId: string,
  body: object,
  signal?: AbortSignal,
): Promise<ReliableTask> {
  return request(
    apiBaseUrl,
    `/learning-documents/${encodeURIComponent(
      documentId,
    )}/generation-tasks`,
    {
      method: 'POST',
      signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  )
}
