import {
  render,
  screen,
  within,
  waitFor,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest'
import type { ReliableTask } from './taskTypes'
import type {
  TrashItem,
  WorkspaceBackupRecord,
} from './workspaceApi'
import { ReliabilityContext } from './reliabilityContextValue'
import { announceTrashCreated } from './trashEvents'

const taskApi = vi.hoisted(() => ({
  listReliableTasks: vi.fn(),
  cancelReliableTask: vi.fn(),
  retryReliableTask: vi.fn(),
}))

const workspaceApi = vi.hoisted(() => ({
  listTrash: vi.fn(),
  restoreTrashItem: vi.fn(),
  purgeTrashItem: vi.fn(),
  listWorkspaceBackups: vi.fn(),
  createWorkspaceBackup: vi.fn(),
  importWorkspaceBackup: vi.fn(),
  queueWorkspaceRestore: vi.fn(),
  cancelPendingWorkspaceRestore: vi.fn(),
  getWorkspaceRestoreStatus: vi.fn(),
}))

const tauriApi = vi.hoisted(() => ({
  invoke: vi.fn(),
}))

vi.mock('./taskApi', () => taskApi)
vi.mock('./workspaceApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./workspaceApi')>()
  return {
    ...actual,
    ...workspaceApi,
  }
})
vi.mock('@tauri-apps/api/core', () => ({
  invoke: tauriApi.invoke,
}))

import { ReliabilityCenter } from './ReliabilityCenter'

const TIMESTAMP = '2026-07-27T10:00:00Z'

function task(
  values: Partial<ReliableTask> = {},
): ReliableTask {
  return {
    id: 'task-1',
    kind: 'chat_generation',
    course_id: 'course-1',
    resource_type: 'chat_conversation',
    resource_id: 'conversation-1',
    status: 'running',
    payload: {},
    result: null,
    idempotency_key: null,
    active_key: null,
    priority: 0,
    attempt: 1,
    max_attempts: 3,
    recovery_count: 0,
    progress: {
      current: 1,
      total: 2,
      stage: 'generating',
      message: 'Generating a grounded answer',
      details: {},
    },
    cancel_requested_at: null,
    worker_id: null,
    error_code: null,
    error_message: null,
    retryable: true,
    available_at: TIMESTAMP,
    created_at: TIMESTAMP,
    updated_at: TIMESTAMP,
    started_at: TIMESTAMP,
    completed_at: null,
    heartbeat_at: TIMESTAMP,
    ...values,
  }
}

function trashItem(): TrashItem {
  return {
    id: 'trash-1',
    entity_type: 'source_asset',
    entity_id: 'source-1',
    course_id: 'course-1',
    display_name: 'Lecture notes.pdf',
    deleted_at: TIMESTAMP,
    purge_after: '2026-08-26T10:00:00Z',
    status: 'trashed',
    metadata: {},
    restored_at: null,
  }
}

function backup(): WorkspaceBackupRecord {
  return {
    id: 'vcc-workspace-20260727.vcc-backup',
    valid: true,
    archive_size_bytes: 4096,
    modified_at: TIMESTAMP,
    archive_sha256: 'a'.repeat(64),
    created_at: TIMESTAMP,
    app_version: '0.1.1',
    backup_kind: 'manual',
    schema_version: 6,
    entry_count: 8,
    managed_file_count: 7,
    total_uncompressed_bytes: 8192,
    error: null,
  }
}

function renderCenter(
  initialTab: 'activity' | 'data',
  onWorkspaceChanged = vi.fn(),
  hasUnprotectedChanges = false,
) {
  return {
    onWorkspaceChanged,
    ...render(
      <ReliabilityContext.Provider
        value={{
          draftStates: new Map(),
          registerDraftState: vi.fn(),
          hasUnprotectedChanges,
          workspaceGeneration: 1,
          workspaceGenerationResolved: true,
        }}
      >
        <ReliabilityCenter
          apiBaseUrl="http://api.test"
          isOpen
          initialTab={initialTab}
          onClose={vi.fn()}
          onWorkspaceChanged={onWorkspaceChanged}
        />
      </ReliabilityContext.Provider>,
    ),
  }
}

describe('ReliabilityCenter', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.clearAllMocks()
    window.localStorage.clear()
    Reflect.deleteProperty(window, '__TAURI_INTERNALS__')
    taskApi.listReliableTasks.mockResolvedValue([])
    taskApi.cancelReliableTask.mockImplementation(async (
      _apiBaseUrl: string,
      taskId: string,
    ) => task({ id: taskId, status: 'canceling' }))
    taskApi.retryReliableTask.mockImplementation(async (
      _apiBaseUrl: string,
      taskId: string,
    ) => task({ id: taskId, status: 'queued', attempt: 2 }))
    workspaceApi.listTrash.mockResolvedValue([])
    workspaceApi.restoreTrashItem.mockResolvedValue(trashItem())
    workspaceApi.purgeTrashItem.mockResolvedValue(trashItem())
    workspaceApi.listWorkspaceBackups.mockResolvedValue([])
    workspaceApi.createWorkspaceBackup.mockResolvedValue(backup())
    workspaceApi.importWorkspaceBackup.mockResolvedValue(backup())
    workspaceApi.queueWorkspaceRestore.mockResolvedValue({
      restore_id: 'restore-1',
      backup_id: backup().id,
      backup_sha256: 'a'.repeat(64),
      queued_at: TIMESTAMP,
      schema_version: 6,
      phase: 'queued',
      workspace_generation: 1,
    })
    workspaceApi.cancelPendingWorkspaceRestore.mockResolvedValue({
      restore_id: 'restore-1',
      backup_id: backup().id,
      status: 'canceled',
      applied_at: null,
      pre_restore_backup_id: null,
      error: 'Restore canceled before restart.',
      workspace_generation: 1,
    })
    workspaceApi.getWorkspaceRestoreStatus.mockResolvedValue({
      workspace_generation: 1,
      pending: null,
      last_result: null,
    })
  })

  it('offers cancel for active work and retry for retryable failures', async () => {
    const running = task()
    const failed = task({
      id: 'task-2',
      kind: 'auto_card_generation',
      status: 'failed',
      error_message: 'Model unavailable',
      error_code: 'provider_unavailable',
      completed_at: TIMESTAMP,
    })
    taskApi.listReliableTasks.mockResolvedValue([running, failed])
    const user = userEvent.setup()
    renderCenter('activity')

    await screen.findByText('Generate answer')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => {
      expect(taskApi.cancelReliableTask).toHaveBeenCalledWith(
        'http://api.test',
        'task-1',
      )
    })

    await user.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => {
      expect(taskApi.retryReliableTask).toHaveBeenCalledWith(
        'http://api.test',
        'task-2',
      )
    })
  })

  it('renders useful empty states and creates a validated backup', async () => {
    const user = userEvent.setup()
    renderCenter('data')

    expect(
      await screen.findByText('No workspace backups'),
    ).toBeInTheDocument()
    expect(screen.getByText('Trash is empty')).toBeInTheDocument()

    await user.click(
      screen.getByRole('button', { name: 'Back up now' }),
    )
    await waitFor(() => {
      expect(workspaceApi.createWorkspaceBackup).toHaveBeenCalledWith(
        'http://api.test',
      )
    })
    expect(
      await screen.findByText(
        'A validated workspace backup was created.',
      ),
    ).toBeInTheDocument()
  })

  it('restores a recoverable Trash item and refreshes the workspace', async () => {
    const item = trashItem()
    workspaceApi.listTrash.mockResolvedValue([item])
    workspaceApi.restoreTrashItem.mockResolvedValue({
      ...item,
      restored_at: TIMESTAMP,
    })
    const onWorkspaceChanged = vi.fn()
    const user = userEvent.setup()
    renderCenter('data', onWorkspaceChanged)

    await screen.findByText(item.display_name)
    await user.click(screen.getByRole('button', { name: 'Restore' }))

    await waitFor(() => {
      expect(workspaceApi.restoreTrashItem).toHaveBeenCalledWith(
        'http://api.test',
        item.id,
      )
      expect(onWorkspaceChanged).toHaveBeenCalledTimes(1)
    })
    expect(
      await screen.findByText(`${item.display_name} restored.`),
    ).toBeInTheDocument()
  })

  it.each([
    {
      status: 'restore_failed' as const,
      copy: 'Restore failed — retry restore or delete permanently',
      restoreEnabled: true,
      purgeEnabled: true,
    },
    {
      status: 'purge_failed' as const,
      copy: 'Permanent deletion failed — retry deletion',
      restoreEnabled: false,
      purgeEnabled: true,
    },
    {
      status: 'restoring' as const,
      copy: 'Restore in progress',
      restoreEnabled: false,
      purgeEnabled: false,
    },
    {
      status: 'purging' as const,
      copy: 'Permanent deletion in progress',
      restoreEnabled: false,
      purgeEnabled: false,
    },
  ])(
    'gates Trash actions while status is $status',
    async ({
      status,
      copy,
      restoreEnabled,
      purgeEnabled,
    }) => {
      const item = { ...trashItem(), status }
      workspaceApi.listTrash.mockResolvedValue([item])
      renderCenter('data')

      const name = await screen.findByText(item.display_name)
      const row = name.closest('article')
      expect(row).not.toBeNull()
      const actions = within(row as HTMLElement)
      expect(actions.getByText(copy)).toBeInTheDocument()
      const restore = actions.getByRole('button', { name: 'Restore' })
      const purge = actions.getByRole('button', {
        name: 'Delete permanently',
      })
      if (restoreEnabled) {
        expect(restore).toBeEnabled()
      } else {
        expect(restore).toBeDisabled()
      }
      if (purgeEnabled) {
        expect(purge).toBeEnabled()
      } else {
        expect(purge).toBeDisabled()
      }
    },
  )

  it('offers Undo only for local delete events and queues rapid deletes', async () => {
    const first = trashItem()
    const second = {
      ...trashItem(),
      id: 'trash-2',
      entity_id: 'source-2',
      display_name: 'Exercises.pdf',
    }
    workspaceApi.listTrash.mockResolvedValue([second, first])
    const user = userEvent.setup()
    renderCenter('activity')

    announceTrashCreated({
      entity_type: first.entity_type,
      entity_id: first.entity_id,
    })
    expect(await screen.findByText(first.display_name)).toBeInTheDocument()

    announceTrashCreated({
      entity_type: second.entity_type,
      entity_id: second.entity_id,
    })
    await waitFor(() => {
      expect(workspaceApi.listTrash).toHaveBeenCalled()
    })
    expect(screen.queryByText(second.display_name)).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Undo' }))
    await waitFor(() => {
      expect(workspaceApi.restoreTrashItem).toHaveBeenCalledWith(
        'http://api.test',
        first.id,
      )
    })
    expect(await screen.findByText(second.display_name)).toBeInTheDocument()
  })

  it('blocks restore while a draft is not protected locally', async () => {
    workspaceApi.listWorkspaceBackups.mockResolvedValue([backup()])
    const confirm = vi.spyOn(window, 'confirm')
    const user = userEvent.setup()
    renderCenter('data', vi.fn(), true)

    await user.click(
      await screen.findByRole('button', { name: 'Restore' }),
    )

    expect(
      await screen.findByText(/Restore is blocked because a draft/),
    ).toBeInTheDocument()
    expect(confirm).not.toHaveBeenCalled()
    expect(workspaceApi.queueWorkspaceRestore).not.toHaveBeenCalled()
  })

  it('keeps a queued restore identity when backend restart fails', async () => {
    workspaceApi.listWorkspaceBackups.mockResolvedValue([backup()])
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    Object.defineProperty(window, '__TAURI_INTERNALS__', {
      configurable: true,
      value: {},
    })
    tauriApi.invoke.mockRejectedValue(new Error('restart unavailable'))
    const user = userEvent.setup()
    renderCenter('data')

    await user.click(
      await screen.findByRole('button', { name: 'Restore' }),
    )

    expect(
      await screen.findByText(/remains queued/),
    ).toBeInTheDocument()
    expect(tauriApi.invoke).toHaveBeenCalledWith('restart_backend')
    expect(
      JSON.parse(
        window.localStorage.getItem(
          'vcc:pending-workspace-restore',
        ) ?? 'null',
      ),
    ).toEqual({
      restore_id: 'restore-1',
      backup_id: backup().id,
      queued_at: TIMESTAMP,
    })
  })

  it('reconciles the exact restore identity after restart', async () => {
    window.localStorage.setItem(
      'vcc:pending-workspace-restore',
      JSON.stringify({
        restore_id: 'restore-1',
        backup_id: backup().id,
        queued_at: TIMESTAMP,
      }),
    )
    workspaceApi.getWorkspaceRestoreStatus.mockResolvedValue({
      workspace_generation: 2,
      pending: null,
      last_result: {
        restore_id: 'restore-1',
        backup_id: backup().id,
        status: 'applied',
        applied_at: TIMESTAMP,
        pre_restore_backup_id: 'pre-restore.vcc-backup',
        error: null,
        workspace_generation: 2,
      },
    })

    renderCenter('activity')

    expect(
      await screen.findByText(/completed successfully/),
    ).toBeInTheDocument()
    expect(
      window.localStorage.getItem(
        'vcc:pending-workspace-restore',
      ),
    ).toBeNull()
  })

  it('cancels only the pending restore identity', async () => {
    const pending = {
      restore_id: 'restore-1',
      backup_id: backup().id,
      backup_sha256: 'a'.repeat(64),
      queued_at: TIMESTAMP,
      schema_version: 6,
      phase: 'queued' as const,
      workspace_generation: 1,
    }
    workspaceApi.getWorkspaceRestoreStatus.mockResolvedValue({
      workspace_generation: 1,
      pending,
      last_result: null,
    })
    window.localStorage.setItem(
      'vcc:pending-workspace-restore',
      JSON.stringify({
        restore_id: pending.restore_id,
        backup_id: pending.backup_id,
        queued_at: pending.queued_at,
      }),
    )
    const user = userEvent.setup()
    renderCenter('data')

    await user.click(
      await screen.findByRole('button', {
        name: 'Cancel queued restore',
      }),
    )

    await waitFor(() => {
      expect(
        workspaceApi.cancelPendingWorkspaceRestore,
      ).toHaveBeenCalledWith('http://api.test', pending.restore_id)
    })
    expect(
      window.localStorage.getItem(
        'vcc:pending-workspace-restore',
      ),
    ).toBeNull()
  })

  it('reports mutation success separately from a refresh failure', async () => {
    workspaceApi.listWorkspaceBackups
      .mockResolvedValueOnce([])
      .mockRejectedValueOnce(new Error('refresh offline'))
    const user = userEvent.setup()
    renderCenter('data')

    await user.click(
      await screen.findByRole('button', { name: 'Back up now' }),
    )

    expect(
      await screen.findByText(
        'A validated workspace backup was created.',
      ),
    ).toBeInTheDocument()
    expect(
      await screen.findByText(/refresh offline/),
    ).toBeInTheDocument()
  })

  it('uses one busy state across recovery mutations', async () => {
    workspaceApi.listWorkspaceBackups.mockResolvedValue([backup()])
    let resolveBackup: ((value: WorkspaceBackupRecord) => void) | undefined
    workspaceApi.createWorkspaceBackup.mockReturnValue(
      new Promise<WorkspaceBackupRecord>((resolve) => {
        resolveBackup = resolve
      }),
    )
    const user = userEvent.setup()
    renderCenter('data')

    await user.click(
      await screen.findByRole('button', { name: 'Back up now' }),
    )

    expect(
      screen.getByRole('button', { name: 'Import' }),
    ).toBeDisabled()
    expect(
      screen.getByRole('button', { name: 'Restore' }),
    ).toBeDisabled()

    resolveBackup?.(backup())
    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: 'Import' }),
      ).toBeEnabled()
    })
  })
})
