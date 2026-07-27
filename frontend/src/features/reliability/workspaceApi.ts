export type TrashEntityType =
  | 'course'
  | 'video_job'
  | 'source_asset'
  | 'knowledge_card'
  | 'learning_document'
  | 'chat_conversation'

export type TrashItem = {
  id: string
  entity_type: TrashEntityType
  entity_id: string
  course_id: string | null
  display_name: string
  deleted_at: string
  purge_after: string | null
  status:
    | 'trashed'
    | 'restoring'
    | 'restore_failed'
    | 'purging'
    | 'purge_failed'
  metadata: Record<string, unknown>
  restored_at: string | null
}

export type WorkspaceBackupRecord = {
  id: string
  valid: boolean
  archive_size_bytes: number
  modified_at: string
  archive_sha256: string | null
  created_at: string | null
  app_version: string | null
  backup_kind: string | null
  schema_version: number | null
  entry_count: number | null
  managed_file_count: number | null
  total_uncompressed_bytes: number | null
  error: string | null
}

export type PendingWorkspaceRestore = {
  restore_id: string
  backup_id: string
  backup_sha256: string
  queued_at: string
  schema_version: number
  phase: 'queued' | 'swapping' | 'swapped'
  workspace_generation: number
}

export type WorkspaceRestoreResult = {
  restore_id: string
  backup_id: string
  status: 'applied' | 'failed' | 'canceled'
  applied_at: string | null
  pre_restore_backup_id: string | null
  error: string | null
  workspace_generation: number
}

export type WorkspaceRestoreStatus = {
  workspace_generation: number
  pending: PendingWorkspaceRestore | null
  last_result: WorkspaceRestoreResult | null
}

export class WorkspaceReliabilityApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'WorkspaceReliabilityApiError'
    this.status = status
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
    let message = `Workspace request failed (${response.status}).`
    try {
      const body = (await response.json()) as {
        detail?: string | { message?: string }
      }
      if (typeof body.detail === 'string') {
        message = body.detail
      } else if (body.detail?.message) {
        message = body.detail.message
      }
    } catch {
      // Keep the HTTP fallback when the backend did not return JSON.
    }
    throw new WorkspaceReliabilityApiError(message, response.status)
  }
  return (await response.json()) as T
}

export function listTrash(
  apiBaseUrl: string,
  signal?: AbortSignal,
): Promise<TrashItem[]> {
  return request(apiBaseUrl, '/trash', { signal })
}

export function restoreTrashItem(
  apiBaseUrl: string,
  itemId: string,
): Promise<TrashItem> {
  return request(
    apiBaseUrl,
    `/trash/${encodeURIComponent(itemId)}/restore`,
    { method: 'POST' },
  )
}

export function purgeTrashItem(
  apiBaseUrl: string,
  itemId: string,
): Promise<TrashItem> {
  return request(
    apiBaseUrl,
    `/trash/${encodeURIComponent(itemId)}`,
    { method: 'DELETE' },
  )
}

export function listWorkspaceBackups(
  apiBaseUrl: string,
  signal?: AbortSignal,
): Promise<WorkspaceBackupRecord[]> {
  return request(apiBaseUrl, '/workspace/backups', { signal })
}

export function createWorkspaceBackup(
  apiBaseUrl: string,
): Promise<WorkspaceBackupRecord> {
  return request(apiBaseUrl, '/workspace/backups', { method: 'POST' })
}

export function importWorkspaceBackup(
  apiBaseUrl: string,
  file: File,
): Promise<WorkspaceBackupRecord> {
  const formData = new FormData()
  formData.set('backup', file)
  return request(apiBaseUrl, '/workspace/backups/import', {
    method: 'POST',
    body: formData,
  })
}

export function queueWorkspaceRestore(
  apiBaseUrl: string,
  backupId: string,
): Promise<PendingWorkspaceRestore> {
  return request(
    apiBaseUrl,
    `/workspace/backups/${encodeURIComponent(backupId)}/restore`,
    { method: 'POST' },
  )
}

export function getWorkspaceRestoreStatus(
  apiBaseUrl: string,
  signal?: AbortSignal,
): Promise<WorkspaceRestoreStatus> {
  return request(apiBaseUrl, '/workspace/restore-status', { signal })
}

export function cancelPendingWorkspaceRestore(
  apiBaseUrl: string,
  restoreId: string,
): Promise<WorkspaceRestoreResult> {
  return request(
    apiBaseUrl,
    `/workspace/restore-pending/${encodeURIComponent(restoreId)}`,
    { method: 'DELETE' },
  )
}

export function workspaceBackupDownloadUrl(
  apiBaseUrl: string,
  backupId: string,
): string {
  return `${apiBaseUrl.replace(
    /\/$/,
    '',
  )}/workspace/backups/${encodeURIComponent(backupId)}/download`
}
