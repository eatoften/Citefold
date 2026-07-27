import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  cancelPendingWorkspaceRestore,
  createWorkspaceBackup,
  queueWorkspaceRestore,
  restoreTrashItem,
  type PendingWorkspaceRestore,
  type TrashItem,
  type WorkspaceBackupRecord,
} from './workspaceApi'

const API_BASE_URL = 'http://api.test/'
const TIMESTAMP = '2026-07-27T10:00:00Z'

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('workspace reliability API mutations', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('restores a Trash item through its encoded identifier', async () => {
    const restored: TrashItem = {
      id: 'trash/item 1',
      entity_type: 'source_asset',
      entity_id: 'source-1',
      course_id: 'course-1',
      display_name: 'Lecture notes.pdf',
      deleted_at: TIMESTAMP,
      purge_after: null,
      status: 'trashed',
      metadata: {},
      restored_at: TIMESTAMP,
    }
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(restored))
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      restoreTrashItem(API_BASE_URL, restored.id),
    ).resolves.toEqual(restored)
    expect(fetchMock).toHaveBeenCalledWith(
      'http://api.test/trash/trash%2Fitem%201/restore',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Accept: 'application/json',
        }),
      }),
    )
  })

  it('creates a complete workspace backup with POST', async () => {
    const backup: WorkspaceBackupRecord = {
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
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(backup))
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      createWorkspaceBackup(API_BASE_URL),
    ).resolves.toEqual(backup)
    expect(fetchMock).toHaveBeenCalledWith(
      'http://api.test/workspace/backups',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Accept: 'application/json',
        }),
      }),
    )
  })

  it('queues a validated backup restore through its encoded id', async () => {
    const pending: PendingWorkspaceRestore = {
      restore_id: 'restore-1',
      backup_id: 'manual/backup 1.vcc-backup',
      backup_sha256: 'b'.repeat(64),
      queued_at: TIMESTAMP,
      schema_version: 6,
      phase: 'queued',
      workspace_generation: 2,
    }
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(pending))
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      queueWorkspaceRestore(API_BASE_URL, pending.backup_id),
    ).resolves.toEqual(pending)
    expect(fetchMock).toHaveBeenCalledWith(
      'http://api.test/workspace/backups/manual%2Fbackup%201.vcc-backup/restore',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Accept: 'application/json',
        }),
      }),
    )
  })

  it('cancels only the exact encoded pending restore identity', async () => {
    const result = {
      restore_id: 'restore/id 1',
      backup_id: 'backup.vcc-backup',
      status: 'canceled' as const,
      applied_at: null,
      pre_restore_backup_id: null,
      error: 'Restore canceled before restart.',
      workspace_generation: 2,
    }
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(result))
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      cancelPendingWorkspaceRestore(
        API_BASE_URL,
        result.restore_id,
      ),
    ).resolves.toEqual(result)
    expect(fetchMock).toHaveBeenCalledWith(
      'http://api.test/workspace/restore-pending/restore%2Fid%201',
      expect.objectContaining({
        method: 'DELETE',
        headers: expect.objectContaining({
          Accept: 'application/json',
        }),
      }),
    )
  })
})
