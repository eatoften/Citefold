import { act, render, screen } from '@testing-library/react'
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest'
import { ReliabilityProvider } from './ReliabilityContext'
import { useReliabilityContext } from './reliabilityContextValue'
import {
  DEVICE_DRAFT_PREFIX,
  PENDING_WORKSPACE_RESTORE_IDENTITY_KEY,
  WORKSPACE_GENERATION_KEY,
} from './workspaceGeneration'

const workspaceApi = vi.hoisted(() => ({
  getWorkspaceRestoreStatus: vi.fn(),
}))

vi.mock('./workspaceApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./workspaceApi')>()
  return {
    ...actual,
    getWorkspaceRestoreStatus:
      workspaceApi.getWorkspaceRestoreStatus,
  }
})

function GenerationHarness() {
  const reliability = useReliabilityContext()
  return (
    <>
      <output data-testid="resolved">
        {String(reliability.workspaceGenerationResolved)}
      </output>
      <output data-testid="generation">
        {reliability.workspaceGeneration}
      </output>
    </>
  )
}

describe('ReliabilityProvider workspace generation', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.useFakeTimers()
    vi.clearAllMocks()
    window.localStorage.clear()
  })

  afterEach(() => {
    vi.useRealTimers()
    window.localStorage.clear()
  })

  it('waits for authoritative generation after a queued restore', async () => {
    const reloadWindow = vi.fn()
    const draftKey = `${DEVICE_DRAFT_PREFIX}chat:course-1`
    window.localStorage.setItem(WORKSPACE_GENERATION_KEY, '1')
    window.localStorage.setItem(
      PENDING_WORKSPACE_RESTORE_IDENTITY_KEY,
      JSON.stringify({
        restore_id: 'restore-1',
        backup_id: 'backup.vcc-backup',
        queued_at: '2026-07-27T10:00:00Z',
      }),
    )
    window.localStorage.setItem(
      draftKey,
      JSON.stringify({
        schema_version: 2,
        workspace_generation: 1,
        draft_id: 'chat:course-1',
        payload: { body: 'old workspace draft' },
        updated_at: '2026-07-27T10:00:00Z',
      }),
    )
    workspaceApi.getWorkspaceRestoreStatus
      .mockRejectedValueOnce(new Error('backend restarting'))
      .mockResolvedValueOnce({
        workspace_generation: 2,
        pending: null,
        last_result: {
          restore_id: 'restore-1',
          backup_id: 'backup.vcc-backup',
          status: 'applied',
          applied_at: '2026-07-27T10:01:00Z',
          pre_restore_backup_id: 'pre-restore.vcc-backup',
          error: null,
          workspace_generation: 2,
        },
      })

    render(
      <ReliabilityProvider
        apiBaseUrl="http://api.test"
        reloadWindow={reloadWindow}
      >
        <GenerationHarness />
      </ReliabilityProvider>,
    )
    await act(async () => {
      await Promise.resolve()
    })

    expect(screen.getByTestId('resolved')).toHaveTextContent('false')
    expect(window.localStorage.getItem(draftKey)).not.toBeNull()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500)
    })

    expect(screen.getByTestId('resolved')).toHaveTextContent('true')
    expect(screen.getByTestId('generation')).toHaveTextContent('2')
    expect(window.localStorage.getItem(draftKey)).toBeNull()
    expect(reloadWindow).toHaveBeenCalledOnce()
  })

  it('keeps watching a queued restore until a manual restart changes generation', async () => {
    const reloadWindow = vi.fn()
    window.localStorage.setItem(WORKSPACE_GENERATION_KEY, '1')
    window.localStorage.setItem(
      PENDING_WORKSPACE_RESTORE_IDENTITY_KEY,
      JSON.stringify({
        restore_id: 'restore-manual',
        backup_id: 'manual.vcc-backup',
        queued_at: '2026-07-27T10:00:00Z',
      }),
    )
    workspaceApi.getWorkspaceRestoreStatus
      .mockResolvedValueOnce({
        workspace_generation: 1,
        pending: {
          restore_id: 'restore-manual',
          backup_id: 'manual.vcc-backup',
          backup_sha256: 'a'.repeat(64),
          queued_at: '2026-07-27T10:00:00Z',
          schema_version: 7,
          phase: 'queued',
          workspace_generation: 1,
        },
        last_result: null,
      })
      .mockResolvedValueOnce({
        workspace_generation: 2,
        pending: null,
        last_result: {
          restore_id: 'restore-manual',
          backup_id: 'manual.vcc-backup',
          status: 'applied',
          applied_at: '2026-07-27T10:01:00Z',
          pre_restore_backup_id: null,
          error: null,
          workspace_generation: 2,
        },
      })

    render(
      <ReliabilityProvider
        apiBaseUrl="http://api.test"
        reloadWindow={reloadWindow}
      >
        <GenerationHarness />
      </ReliabilityProvider>,
    )
    await act(async () => {
      await Promise.resolve()
    })
    expect(workspaceApi.getWorkspaceRestoreStatus).toHaveBeenCalledTimes(1)
    expect(reloadWindow).not.toHaveBeenCalled()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500)
    })
    expect(workspaceApi.getWorkspaceRestoreStatus).toHaveBeenCalledTimes(2)
    expect(reloadWindow).toHaveBeenCalledOnce()
    expect(window.localStorage.getItem(WORKSPACE_GENERATION_KEY)).toBe('2')
  })
})
