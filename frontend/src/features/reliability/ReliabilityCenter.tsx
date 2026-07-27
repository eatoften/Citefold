import { invoke } from '@tauri-apps/api/core'
import {
  AlertTriangle,
  ArchiveRestore,
  CheckCircle2,
  Clock3,
  DatabaseBackup,
  Download,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  Trash2,
  Upload,
  X,
} from 'lucide-react'
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
} from 'react'
import {
  cancelReliableTask,
  listReliableTasks,
  retryReliableTask,
} from './taskApi'
import type { ReliableTask } from './taskTypes'
import {
  cancelPendingWorkspaceRestore,
  createWorkspaceBackup,
  getWorkspaceRestoreStatus,
  importWorkspaceBackup,
  listTrash,
  listWorkspaceBackups,
  purgeTrashItem,
  queueWorkspaceRestore,
  restoreTrashItem,
  workspaceBackupDownloadUrl,
  type TrashItem,
  type WorkspaceBackupRecord,
  type WorkspaceRestoreStatus,
} from './workspaceApi'
import { useReliabilityContext } from './reliabilityContextValue'
import {
  TRASH_CREATED_EVENT,
  type TrashCreatedDetail,
} from './trashEvents'
import { PENDING_WORKSPACE_RESTORE_IDENTITY_KEY } from './workspaceGeneration'

type ReliabilityTab = 'activity' | 'data'

type ReliabilityCenterProps = {
  apiBaseUrl: string
  isOpen: boolean
  initialTab: ReliabilityTab
  onClose: () => void
  onWorkspaceChanged: () => void
}

const ACTIVE_TASK_STATUSES = new Set([
  'queued',
  'running',
  'canceling',
])
const RESTORABLE_TRASH_STATUSES = new Set<TrashItem['status']>([
  'trashed',
  'restore_failed',
])
const PURGEABLE_TRASH_STATUSES = new Set<TrashItem['status']>([
  'trashed',
  'restore_failed',
  'purge_failed',
])
const TRASH_STATUS_COPY: Partial<
  Record<TrashItem['status'], string>
> = {
  restoring: 'Restore in progress',
  restore_failed: 'Restore failed — retry restore or delete permanently',
  purging: 'Permanent deletion in progress',
  purge_failed: 'Permanent deletion failed — retry deletion',
}
type PendingRestoreIdentity = {
  restore_id: string
  backup_id: string
  queued_at: string
}

function readPendingRestoreIdentity(): PendingRestoreIdentity | null {
  try {
    const raw = window.localStorage.getItem(
      PENDING_WORKSPACE_RESTORE_IDENTITY_KEY,
    )
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<PendingRestoreIdentity>
    if (
      typeof parsed.restore_id !== 'string' ||
      typeof parsed.backup_id !== 'string' ||
      typeof parsed.queued_at !== 'string'
    ) {
      return null
    }
    return parsed as PendingRestoreIdentity
  } catch {
    return null
  }
}

function storePendingRestoreIdentity(
  identity: PendingRestoreIdentity,
): void {
  try {
    window.localStorage.setItem(
      PENDING_WORKSPACE_RESTORE_IDENTITY_KEY,
      JSON.stringify(identity),
    )
  } catch {
    // The backend marker remains authoritative when browser storage is full.
  }
}

function clearPendingRestoreIdentity(restoreId: string): void {
  const current = readPendingRestoreIdentity()
  if (current?.restore_id !== restoreId) return
  try {
    window.localStorage.removeItem(
      PENDING_WORKSPACE_RESTORE_IDENTITY_KEY,
    )
  } catch {
    // A stale UI hint is harmless because identity matching is mandatory.
  }
}

const TASK_LABELS: Record<string, string> = {
  video_processing: 'Process video',
  source_import: 'Import source',
  source_index: 'Index sources',
  auto_card_generation: 'Generate cards',
  chat_generation: 'Generate answer',
  learning_document_generation: 'Generate study document',
}

const ENTITY_LABELS: Record<TrashItem['entity_type'], string> = {
  course: 'Course',
  video_job: 'Video',
  source_asset: 'Source',
  knowledge_card: 'Card',
  learning_document: 'Studio document',
  chat_conversation: 'Conversation',
}

function readableDate(value: string | null): string {
  if (!value) return 'Unknown time'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(parsed)
}

function readableSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = bytes / 1024
  let unit = units[0]
  for (let index = 1; value >= 1024 && index < units.length; index += 1) {
    value /= 1024
    unit = units[index]
  }
  return `${value.toFixed(value >= 10 ? 1 : 2)} ${unit}`
}

function taskProgress(task: ReliableTask): number | null {
  const { current, total } = task.progress
  if (typeof total !== 'number' || total <= 0) return null
  return Math.max(0, Math.min(100, (current / total) * 100))
}

function isTauriRuntime(): boolean {
  return '__TAURI_INTERNALS__' in window
}

export function ReliabilityCenter({
  apiBaseUrl,
  isOpen,
  initialTab,
  onClose,
  onWorkspaceChanged,
}: ReliabilityCenterProps) {
  const { hasUnprotectedChanges } = useReliabilityContext()
  const [tab, setTab] = useState<ReliabilityTab>(initialTab)
  const [tasks, setTasks] = useState<ReliableTask[]>([])
  const [trash, setTrash] = useState<TrashItem[]>([])
  const [backups, setBackups] = useState<WorkspaceBackupRecord[]>([])
  const [restoreStatus, setRestoreStatus] =
    useState<WorkspaceRestoreStatus | null>(null)
  const [undoQueue, setUndoQueue] = useState<TrashItem[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [refreshWarning, setRefreshWarning] =
    useState<string | null>(null)
  const [busyAction, setBusyAction] = useState<string | null>(null)
  const mutationInFlight = useRef(false)
  const taskRefreshSequence = useRef(0)
  const trashRefreshSequence = useRef(0)
  const importInputRef = useRef<HTMLInputElement | null>(null)
  const mutationBusy = busyAction !== null

  const refreshTasks = useCallback(async (signal?: AbortSignal) => {
    const sequence = ++taskRefreshSequence.current
    const next = await listReliableTasks(apiBaseUrl, {
      limit: 100,
      signal,
    })
    if (sequence === taskRefreshSequence.current) setTasks(next)
    return next
  }, [apiBaseUrl])

  const refreshTrash = useCallback(async (signal?: AbortSignal) => {
    const sequence = ++trashRefreshSequence.current
    const next = await listTrash(apiBaseUrl, signal)
    if (sequence === trashRefreshSequence.current) setTrash(next)
    return next
  }, [apiBaseUrl])

  const refreshRecovery = useCallback(async (signal?: AbortSignal) => {
    const [nextBackups, nextStatus] = await Promise.all([
      listWorkspaceBackups(apiBaseUrl, signal),
      getWorkspaceRestoreStatus(apiBaseUrl, signal),
    ])
    setBackups(nextBackups)
    setRestoreStatus(nextStatus)
  }, [apiBaseUrl])

  useEffect(() => setTab(initialTab), [initialTab, isOpen])

  useEffect(() => {
    if (!isOpen) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, onClose])

  useEffect(() => {
    let disposed = false
    let controller: AbortController | null = null
    let timer: number | null = null
    const refresh = async () => {
      controller = new AbortController()
      try {
        await Promise.all([
          refreshTasks(controller.signal),
          refreshTrash(controller.signal),
        ])
      } catch {
        // The drawer exposes an explicit refresh/error path. Background
        // polling remains quiet while the backend restarts.
      } finally {
        if (!disposed) {
          timer = window.setTimeout(
            refresh,
            isOpen ? 1800 : 4000,
          )
        }
      }
    }
    void refresh()
    return () => {
      disposed = true
      controller?.abort()
      if (timer !== null) window.clearTimeout(timer)
    }
  }, [isOpen, refreshTasks, refreshTrash])

  useEffect(() => {
    const handleTrashCreated = (event: Event) => {
      const detail = (event as CustomEvent<TrashCreatedDetail>).detail
      if (!detail) return
      void refreshTrash()
        .then((next) => {
          const created = next.find(
            (item) =>
              item.entity_type === detail.entity_type &&
              item.entity_id === detail.entity_id,
          )
          if (!created) return
          setUndoQueue((current) =>
            current.some((item) => item.id === created.id)
              ? current
              : [...current, created],
          )
        })
        .catch(() => undefined)
    }
    window.addEventListener(
      TRASH_CREATED_EVENT,
      handleTrashCreated,
    )
    return () =>
      window.removeEventListener(
        TRASH_CREATED_EVENT,
        handleTrashCreated,
      )
  }, [refreshTrash])

  useEffect(() => {
    if (!isOpen || tab !== 'data') return
    const controller = new AbortController()
    void refreshRecovery(controller.signal).catch((caught) => {
      if (caught instanceof DOMException && caught.name === 'AbortError') return
      setError(caught instanceof Error ? caught.message : 'Recovery data failed.')
    })
    return () => controller.abort()
  }, [isOpen, refreshRecovery, tab])

  useEffect(() => {
    const identity = readPendingRestoreIdentity()
    if (!identity) return
    const controller = new AbortController()
    void getWorkspaceRestoreStatus(apiBaseUrl, controller.signal)
      .then((status) => {
        if (controller.signal.aborted) return
        setRestoreStatus(status)
        if (status.last_result?.restore_id === identity.restore_id) {
          clearPendingRestoreIdentity(identity.restore_id)
          if (status.last_result.status === 'applied') {
            setNotice(
              `Restore ${identity.backup_id} completed successfully.`,
            )
          } else {
            setError(
              status.last_result.error ??
                `Restore ${identity.backup_id} ${status.last_result.status}.`,
            )
          }
          return
        }
        if (status.pending?.restore_id === identity.restore_id) {
          setNotice(
            `Restore ${identity.backup_id} is still ${status.pending.phase}.`,
          )
          return
        }
        setRefreshWarning(
          `Restore ${identity.backup_id} could not be matched to the backend result. The saved restore identity was kept for recovery.`,
        )
      })
      .catch(() => {
        if (controller.signal.aborted) return
        setRefreshWarning(
          `Could not verify restore ${identity.backup_id}. Its identity remains saved and can be checked again.`,
        )
      })
    return () => controller.abort()
  }, [apiBaseUrl])

  async function refreshAfterMutation(): Promise<void> {
    try {
      await Promise.all([refreshTasks(), refreshTrash()])
      if (tab === 'data') await refreshRecovery()
    } catch (caught) {
      setRefreshWarning(
        `The change succeeded, but refreshed data could not be loaded: ${
          caught instanceof Error ? caught.message : 'refresh failed'
        }`,
      )
    }
  }

  async function runAction(
    actionId: string,
    action: () => Promise<unknown>,
    successMessage: string,
    options: { workspaceChanged?: boolean } = {},
  ) {
    if (mutationInFlight.current) return false
    mutationInFlight.current = true
    setBusyAction(actionId)
    setError(null)
    setNotice(null)
    setRefreshWarning(null)
    let succeeded = false
    try {
      await action()
      setNotice(successMessage)
      succeeded = true
      if (options.workspaceChanged) {
        try {
          onWorkspaceChanged()
        } catch (caught) {
          setRefreshWarning(
            `The change succeeded, but the workspace view could not reload: ${
              caught instanceof Error ? caught.message : 'reload failed'
            }`,
          )
        }
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Action failed.')
    }
    if (succeeded) await refreshAfterMutation()
    mutationInFlight.current = false
    setBusyAction(null)
    return succeeded
  }

  async function handleRestoreBackup(backup: WorkspaceBackupRecord) {
    if (mutationInFlight.current) return
    if (hasUnprotectedChanges) {
      setError(
        'Restore is blocked because a draft is not yet protected locally. Keep this window open until it is saved.',
      )
      return
    }
    const confirmed = window.confirm(
      `Restore “${backup.id}”? The current workspace will be replaced after restart. A pre-restore safety backup is created automatically.`,
    )
    if (!confirmed) return
    mutationInFlight.current = true
    setBusyAction(`restore-backup:${backup.id}`)
    setError(null)
    setRefreshWarning(null)
    try {
      const pending = await queueWorkspaceRestore(apiBaseUrl, backup.id)
      storePendingRestoreIdentity({
        restore_id: pending.restore_id,
        backup_id: pending.backup_id,
        queued_at: pending.queued_at,
      })
      setRestoreStatus((current) => ({
        workspace_generation:
          current?.workspace_generation ??
          pending.workspace_generation,
        pending,
        last_result: current?.last_result ?? null,
      }))
      setNotice('Restore validated and queued. Restarting the local backend.')
      await refreshAfterMutation()
      if (isTauriRuntime()) {
        try {
          await invoke('restart_backend')
        } catch (caught) {
          setError(
            `Backend restart failed; restore ${pending.backup_id} remains queued. ${
              caught instanceof Error ? caught.message : ''
            }`.trim(),
          )
          return
        }
        window.location.reload()
      } else {
        setNotice(
          'Restore is queued. Restart the backend to apply it safely.',
        )
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Restore failed.')
    } finally {
      mutationInFlight.current = false
      setBusyAction(null)
    }
  }

  function handleImport(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    void runAction(
      `import:${file.name}`,
      () => importWorkspaceBackup(apiBaseUrl, file),
      'Backup imported and validated.',
    )
  }

  const activeTaskCount = tasks.filter((task) =>
    ACTIVE_TASK_STATUSES.has(task.status),
  ).length
  const undoItem = undoQueue[0] ?? null

  return (
    <>
      {undoItem && (
        <section className="reliability-undo-toast" role="status">
          <div>
            <strong>Moved to Trash</strong>
            <span>{undoItem.display_name}</span>
          </div>
          <button
            type="button"
            disabled={mutationBusy}
            onClick={() => {
              const item = undoItem
              void runAction(
                `undo:${item.id}`,
                () => restoreTrashItem(apiBaseUrl, item.id),
                `${item.display_name} restored.`,
                { workspaceChanged: true },
              ).then((succeeded) => {
                if (succeeded) {
                  setUndoQueue((current) => current.slice(1))
                }
              })
            }}
          >
            <RotateCcw size={15} aria-hidden="true" />
            Undo
          </button>
          <button
            type="button"
            className="icon-button"
            aria-label="Dismiss"
            onClick={() =>
              setUndoQueue((current) => current.slice(1))
            }
          >
            <X size={15} aria-hidden="true" />
          </button>
        </section>
      )}

      {isOpen && (
        <div
          className="reliability-overlay"
          onMouseDown={(event) => {
            if (event.currentTarget === event.target) onClose()
          }}
        >
          <aside
            className="reliability-center"
            role="dialog"
            aria-modal="true"
            aria-labelledby="reliability-center-title"
          >
            <header>
              <div>
                <p className="eyebrow">Local workspace</p>
                <h2 id="reliability-center-title">
                  Activity &amp; recovery
                </h2>
              </div>
              <button
                type="button"
                className="icon-button"
                aria-label="Close activity and recovery"
                onClick={onClose}
              >
                <X size={19} aria-hidden="true" />
              </button>
            </header>

            <div className="reliability-tabs" role="tablist">
              <button
                type="button"
                role="tab"
                aria-selected={tab === 'activity'}
                className={tab === 'activity' ? 'active' : undefined}
                onClick={() => setTab('activity')}
              >
                Activity
                {activeTaskCount > 0 && <span>{activeTaskCount}</span>}
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={tab === 'data'}
                className={tab === 'data' ? 'active' : undefined}
                onClick={() => setTab('data')}
              >
                Data &amp; recovery
              </button>
            </div>

            {(notice || error) && (
              <div
                className={`reliability-notice ${error ? 'error' : 'success'}`}
                role={error ? 'alert' : 'status'}
              >
                {error ? (
                  <AlertTriangle size={16} aria-hidden="true" />
                ) : (
                  <CheckCircle2 size={16} aria-hidden="true" />
                )}
                <span>{error ?? notice}</span>
              </div>
            )}
            {refreshWarning && (
              <div className="reliability-notice error" role="status">
                <AlertTriangle size={16} aria-hidden="true" />
                <span>{refreshWarning}</span>
              </div>
            )}

            <div className="reliability-body">
              {tab === 'activity' ? (
                <section aria-label="Background activity">
                  <div className="reliability-section-heading">
                    <div>
                      <h3>Background activity</h3>
                      <p>
                        Closing this view does not stop durable work.
                      </p>
                    </div>
                    <button
                      type="button"
                      className="icon-button"
                      aria-label="Refresh activity"
                      onClick={() => void refreshTasks()}
                    >
                      <RefreshCw size={16} aria-hidden="true" />
                    </button>
                  </div>
                  <div className="reliability-list">
                    {tasks.length === 0 && (
                      <div className="reliability-empty">
                        <Clock3 size={23} aria-hidden="true" />
                        <strong>No background activity yet</strong>
                        <span>
                          Imports, indexing, answers, and Studio generation
                          will appear here.
                        </span>
                      </div>
                    )}
                    {tasks.map((task) => {
                      const progress = taskProgress(task)
                      const active = ACTIVE_TASK_STATUSES.has(task.status)
                      return (
                        <article className="reliability-task" key={task.id}>
                          <div className="reliability-item-title">
                            <div>
                              {active ? (
                                <LoaderCircle
                                  className="spin"
                                  size={16}
                                  aria-hidden="true"
                                />
                              ) : task.status === 'succeeded' ? (
                                <CheckCircle2
                                  size={16}
                                  aria-hidden="true"
                                />
                              ) : (
                                <AlertTriangle
                                  size={16}
                                  aria-hidden="true"
                                />
                              )}
                              <strong>
                                {TASK_LABELS[task.kind] ?? task.kind}
                              </strong>
                            </div>
                            <span className={`task-state ${task.status}`}>
                              {task.status}
                            </span>
                          </div>
                          <p>
                            {task.progress.message ??
                              task.error_message ??
                              `Attempt ${task.attempt} of ${task.max_attempts}`}
                          </p>
                          {progress !== null && (
                            <div
                              className="task-progress"
                              aria-label={`${Math.round(progress)}% complete`}
                            >
                              <span style={{ width: `${progress}%` }} />
                            </div>
                          )}
                          <div className="reliability-item-meta">
                            <span>{readableDate(task.updated_at)}</span>
                            <div>
                              {active && (
                                <button
                                  type="button"
                                  disabled={
                                    task.status === 'canceling' ||
                                    mutationBusy
                                  }
                                  onClick={() =>
                                    void runAction(
                                      `cancel:${task.id}`,
                                      () =>
                                        cancelReliableTask(
                                          apiBaseUrl,
                                          task.id,
                                        ),
                                      'Cancellation requested. The current safe checkpoint may need to finish.',
                                    )
                                  }
                                >
                                  Cancel
                                </button>
                              )}
                              {(task.status === 'failed' ||
                                task.status === 'canceled') &&
                                task.retryable && (
                                  <button
                                    type="button"
                                    disabled={mutationBusy}
                                    onClick={() =>
                                      void runAction(
                                        `retry:${task.id}`,
                                        () =>
                                          retryReliableTask(
                                            apiBaseUrl,
                                            task.id,
                                          ),
                                        'Task queued for retry.',
                                      )
                                    }
                                  >
                                    Retry
                                  </button>
                                )}
                            </div>
                          </div>
                        </article>
                      )
                    })}
                  </div>
                </section>
              ) : (
                <>
                  <section aria-label="Workspace backups">
                    <div className="reliability-section-heading">
                      <div>
                        <h3>Workspace backups</h3>
                        <p>
                          Database, videos, transcripts, and imported sources
                          are validated together.
                        </p>
                      </div>
                      <div className="reliability-heading-actions">
                        <input
                          ref={importInputRef}
                          type="file"
                          accept=".vcc-backup"
                          hidden
                          onChange={handleImport}
                        />
                        <button
                          type="button"
                          disabled={mutationBusy}
                          onClick={() => importInputRef.current?.click()}
                        >
                          <Upload size={15} aria-hidden="true" />
                          Import
                        </button>
                        <button
                          type="button"
                          disabled={mutationBusy}
                          onClick={() =>
                            void runAction(
                              'create-backup',
                              () => createWorkspaceBackup(apiBaseUrl),
                              'A validated workspace backup was created.',
                            )
                          }
                        >
                          <DatabaseBackup size={15} aria-hidden="true" />
                          Back up now
                        </button>
                      </div>
                    </div>
                    {restoreStatus?.last_result && (
                      <div
                        className={`restore-result ${restoreStatus.last_result.status}`}
                      >
                        <strong>
                          Last restore: {restoreStatus.last_result.status}
                        </strong>
                        <span>
                          {restoreStatus.last_result.error ??
                            readableDate(
                              restoreStatus.last_result.applied_at,
                            )}
                        </span>
                      </div>
                    )}
                    {restoreStatus?.pending && (
                      <div className="restore-result pending">
                        <div>
                          <strong>
                            Restore {restoreStatus.pending.phase}
                          </strong>
                          <span>
                            {restoreStatus.pending.backup_id} will be applied
                            on the next backend restart.
                          </span>
                        </div>
                        {restoreStatus.pending.phase === 'queued' && (
                          <button
                            type="button"
                            disabled={mutationBusy}
                            onClick={() => {
                              const pending = restoreStatus.pending
                              if (!pending) return
                              void runAction(
                                `cancel-restore:${pending.restore_id}`,
                                async () => {
                                  const result =
                                    await cancelPendingWorkspaceRestore(
                                      apiBaseUrl,
                                      pending.restore_id,
                                    )
                                  clearPendingRestoreIdentity(
                                    pending.restore_id,
                                  )
                                  setRestoreStatus((current) => ({
                                    workspace_generation:
                                      current?.workspace_generation ??
                                      pending.workspace_generation,
                                    pending: null,
                                    last_result: result,
                                  }))
                                  return result
                                },
                                `Restore ${pending.backup_id} canceled.`,
                              )
                            }}
                          >
                            Cancel queued restore
                          </button>
                        )}
                      </div>
                    )}
                    <div className="reliability-list compact">
                      {backups.length === 0 && (
                        <div className="reliability-empty">
                          <DatabaseBackup size={23} aria-hidden="true" />
                          <strong>No workspace backups</strong>
                          <span>
                            Create one before large imports or structural
                            changes.
                          </span>
                        </div>
                      )}
                      {backups.map((backup) => (
                        <article
                          className="reliability-backup"
                          key={backup.id}
                        >
                          <div>
                            <strong>{backup.id}</strong>
                            <span>
                              {readableSize(backup.archive_size_bytes)}
                              {' · '}
                              {readableDate(
                                backup.created_at ?? backup.modified_at,
                              )}
                            </span>
                            {!backup.valid && (
                              <small>{backup.error ?? 'Validation failed.'}</small>
                            )}
                          </div>
                          <div>
                            {backup.valid && (
                              <>
                                <a
                                  href={workspaceBackupDownloadUrl(
                                    apiBaseUrl,
                                    backup.id,
                                  )}
                                  download={backup.id}
                                  target="_blank"
                                  rel="noreferrer"
                                >
                                  <Download size={14} aria-hidden="true" />
                                  Export
                                </a>
                                <button
                                  type="button"
                                  disabled={
                                    mutationBusy ||
                                    Boolean(restoreStatus?.pending)
                                  }
                                  onClick={() =>
                                    void handleRestoreBackup(backup)
                                  }
                                >
                                  <ArchiveRestore
                                    size={14}
                                    aria-hidden="true"
                                  />
                                  Restore
                                </button>
                              </>
                            )}
                          </div>
                        </article>
                      ))}
                    </div>
                  </section>

                  <section aria-label="Trash">
                    <div className="reliability-section-heading">
                      <div>
                        <h3>Trash</h3>
                        <p>
                          Recoverable until you permanently delete it.
                        </p>
                      </div>
                    </div>
                    <div className="reliability-list compact">
                      {trash.length === 0 && (
                        <div className="reliability-empty">
                          <Trash2 size={23} aria-hidden="true" />
                          <strong>Trash is empty</strong>
                          <span>Deleted workspace items appear here.</span>
                        </div>
                      )}
                      {trash.map((item) => {
                        const canRestore =
                          RESTORABLE_TRASH_STATUSES.has(item.status)
                        const canPurge =
                          PURGEABLE_TRASH_STATUSES.has(item.status)
                        return (
                        <article
                          className="reliability-trash-item"
                          key={item.id}
                        >
                          <div>
                            <span>{ENTITY_LABELS[item.entity_type]}</span>
                            <strong>{item.display_name}</strong>
                            <small>
                              Deleted {readableDate(item.deleted_at)}
                            </small>
                            {TRASH_STATUS_COPY[item.status] && (
                              <small>{TRASH_STATUS_COPY[item.status]}</small>
                            )}
                          </div>
                          <div>
                            <button
                              type="button"
                              disabled={mutationBusy || !canRestore}
                              onClick={() =>
                                void runAction(
                                  `restore-trash:${item.id}`,
                                  () =>
                                    restoreTrashItem(apiBaseUrl, item.id),
                                  `${item.display_name} restored.`,
                                  { workspaceChanged: true },
                                )
                              }
                            >
                              <RotateCcw size={14} aria-hidden="true" />
                              Restore
                            </button>
                            <button
                              type="button"
                              className="danger"
                              disabled={mutationBusy || !canPurge}
                              onClick={() => {
                                if (
                                  !window.confirm(
                                    `Permanently delete “${item.display_name}”? This cannot be undone.`,
                                  )
                                ) {
                                  return
                                }
                                void runAction(
                                  `purge-trash:${item.id}`,
                                  () => purgeTrashItem(apiBaseUrl, item.id),
                                  `${item.display_name} permanently deleted.`,
                                  { workspaceChanged: true },
                                )
                              }}
                            >
                              <Trash2 size={14} aria-hidden="true" />
                              Delete permanently
                            </button>
                          </div>
                        </article>
                        )
                      })}
                    </div>
                  </section>
                </>
              )}
            </div>
          </aside>
        </div>
      )}
    </>
  )
}
