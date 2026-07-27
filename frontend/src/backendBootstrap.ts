import { invoke } from '@tauri-apps/api/core'

const BACKEND_APPLICATION_ID = 'video-course-cards'
const BACKEND_API_VERSION = 1
const BACKEND_HEALTH_TIMEOUT_MS = 1000
const BACKEND_STARTUP_TIMEOUT_MS = 45000
const BACKEND_POLL_INTERVAL_MS = 500

type BackendProcessStatus = {
  ready: boolean
  mode: string
  message: string
  application_id: string
  api_version: number
  identity_verified: boolean
}

export type BackendBootPhase =
  | 'checking'
  | 'starting'
  | 'ready'
  | 'failed'

export type BackendBootState = {
  phase: BackendBootPhase
  message: string
  mode: string
}

type BackendHealthPayload = {
  status?: unknown
  application_id?: unknown
  api_version?: unknown
}

export function isTauriRuntime(): boolean {
  return Boolean(
    (window as Window & { __TAURI_INTERNALS__?: unknown })
      .__TAURI_INTERNALS__,
  )
}

export function hasExpectedBackendHealthIdentity(
  payload: unknown,
): payload is BackendHealthPayload {
  if (!payload || typeof payload !== 'object') return false
  const health = payload as BackendHealthPayload
  return (
    health.status === 'ok' &&
    health.application_id === BACKEND_APPLICATION_ID &&
    health.api_version === BACKEND_API_VERSION
  )
}

function hasVerifiedBackendProcessStatus(
  status: BackendProcessStatus,
): boolean {
  return (
    status.ready &&
    status.identity_verified &&
    status.application_id === BACKEND_APPLICATION_ID &&
    status.api_version === BACKEND_API_VERSION
  )
}

function sleep(milliseconds: number): Promise<void> {
  return new Promise((resolve) =>
    window.setTimeout(resolve, milliseconds),
  )
}

export async function checkBackendHealth(
  apiBaseUrl: string,
  timeoutMs = BACKEND_HEALTH_TIMEOUT_MS,
): Promise<boolean> {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs)

  try {
    const response = await fetch(`${apiBaseUrl}/health`, {
      cache: 'no-store',
      signal: controller.signal,
    })
    if (!response.ok) return false
    return hasExpectedBackendHealthIdentity(await response.json())
  } catch {
    return false
  } finally {
    window.clearTimeout(timeoutId)
  }
}

async function waitForBackendHealth(
  apiBaseUrl: string,
  timeoutMs = BACKEND_STARTUP_TIMEOUT_MS,
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs

  while (Date.now() < deadline) {
    if (await checkBackendHealth(apiBaseUrl)) return true
    await sleep(BACKEND_POLL_INTERVAL_MS)
  }
  return false
}

export async function ensureBackendReady(
  apiBaseUrl: string,
): Promise<BackendBootState> {
  if (isTauriRuntime()) {
    try {
      const status =
        await invoke<BackendProcessStatus>('ensure_backend')
      if (hasVerifiedBackendProcessStatus(status)) {
        return {
          phase: 'ready',
          mode: status.mode || 'sidecar',
          message: status.message || `Backend ready at ${apiBaseUrl}.`,
        }
      }
      return {
        phase: 'failed',
        mode: status.mode || 'sidecar',
        message:
          status.message ||
          'Local backend identity could not be verified.',
      }
    } catch (error) {
      return {
        phase: 'failed',
        mode: 'sidecar',
        message:
          error instanceof Error
            ? error.message
            : typeof error === 'string'
              ? error
              : 'Failed to start local backend sidecar.',
      }
    }
  }

  if (await checkBackendHealth(apiBaseUrl)) {
    return {
      phase: 'ready',
      mode: 'external',
      message: `Backend ready at ${apiBaseUrl}.`,
    }
  }

  if (await waitForBackendHealth(apiBaseUrl, 5000)) {
    return {
      phase: 'ready',
      mode: 'external',
      message: `Backend ready at ${apiBaseUrl}.`,
    }
  }

  return {
    phase: 'failed',
    mode: 'manual',
    message:
      'Backend is not running. Start FastAPI manually, then retry.',
  }
}
