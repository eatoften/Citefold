import type { CitationTarget } from './citationTypes'

export class CitationTargetRequestError extends Error {
  status: number | null

  constructor(message: string, status: number | null = null) {
    super(message)
    this.name = 'CitationTargetRequestError'
    this.status = status
  }
}

function apiUrl(apiBaseUrl: string, path: string): string {
  return `${apiBaseUrl.replace(/\/+$/, '')}${path}`
}

export async function fetchCitationTarget(
  apiBaseUrl: string,
  courseId: string,
  citationId: string,
  signal?: AbortSignal,
): Promise<CitationTarget> {
  return fetchCitationTargetAtPath(
    apiBaseUrl,
    `/courses/${encodeURIComponent(courseId)}/chat/citations/${encodeURIComponent(citationId)}/target`,
    signal,
  )
}

export async function fetchCitationTargetAtPath(
  apiBaseUrl: string,
  targetPath: string,
  signal?: AbortSignal,
): Promise<CitationTarget> {
  const response = await fetch(
    apiUrl(apiBaseUrl, targetPath),
    { signal },
  )

  if (!response.ok) {
    let message = `Could not open this source (HTTP ${response.status}).`
    try {
      const payload = (await response.json()) as {
        detail?: unknown
      }
      if (typeof payload.detail === 'string' && payload.detail.trim()) {
        message = payload.detail
      }
    } catch {
      // Keep the user-friendly status fallback.
    }
    throw new CitationTargetRequestError(message, response.status)
  }

  return response.json() as Promise<CitationTarget>
}
