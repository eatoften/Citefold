import type {
  WorkspaceDraft,
  WorkspaceDraftPut,
} from './draftTypes'

function draftUrl(apiBaseUrl: string, draftId: string): string {
  return `${apiBaseUrl}/workspace/drafts/${encodeURIComponent(draftId)}`
}

async function errorMessage(response: Response): Promise<string> {
  const fallback = `Draft request failed (${response.status}).`
  try {
    const body = (await response.json()) as {
      detail?: string | { message?: string }
    }
    if (typeof body.detail === 'string') return body.detail
    return body.detail?.message ?? fallback
  } catch {
    return fallback
  }
}

export class DraftConflictError<T extends object> extends Error {
  readonly current: WorkspaceDraft<T> | null

  constructor(
    message: string,
    current: WorkspaceDraft<T> | null,
  ) {
    super(message)
    this.name = 'DraftConflictError'
    this.current = current
  }
}

export async function getWorkspaceDraft<T extends object>(
  apiBaseUrl: string,
  draftId: string,
  signal?: AbortSignal,
): Promise<WorkspaceDraft<T> | null> {
  const response = await fetch(draftUrl(apiBaseUrl, draftId), {
    signal,
    headers: { Accept: 'application/json' },
  })
  if (response.status === 404) return null
  if (!response.ok) throw new Error(await errorMessage(response))
  return (await response.json()) as WorkspaceDraft<T>
}

export async function putWorkspaceDraft<T extends object>(
  apiBaseUrl: string,
  draftId: string,
  request: WorkspaceDraftPut<T>,
  signal?: AbortSignal,
): Promise<WorkspaceDraft<T>> {
  const response = await fetch(draftUrl(apiBaseUrl, draftId), {
    method: 'PUT',
    signal,
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  })
  if (response.status === 409) {
    let current: WorkspaceDraft<T> | null = null
    let message = 'This draft changed in another editor.'
    try {
      const body = (await response.json()) as {
        detail?: {
          message?: string
          current?: WorkspaceDraft<T> | null
        }
      }
      current = body.detail?.current ?? null
      message = body.detail?.message ?? message
    } catch {
      // The local recovery copy remains authoritative for this editor.
    }
    throw new DraftConflictError(message, current)
  }
  if (!response.ok) throw new Error(await errorMessage(response))
  return (await response.json()) as WorkspaceDraft<T>
}

export async function deleteWorkspaceDraft(
  apiBaseUrl: string,
  draftId: string,
  revision?: number,
): Promise<void> {
  const parameters = new URLSearchParams()
  if (revision !== undefined) {
    parameters.set('expected_revision', String(revision))
  }
  const suffix = parameters.size ? `?${parameters.toString()}` : ''
  const response = await fetch(
    `${draftUrl(apiBaseUrl, draftId)}${suffix}`,
    { method: 'DELETE' },
  )
  if (response.status === 404) return
  if (!response.ok) throw new Error(await errorMessage(response))
}
