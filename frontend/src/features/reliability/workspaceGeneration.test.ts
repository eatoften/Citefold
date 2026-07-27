import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  DEVICE_DRAFT_PREFIX,
  QUARANTINED_DRAFT_PREFIX,
  WORKSPACE_GENERATION_KEY,
  synchronizeWorkspaceGeneration,
} from './workspaceGeneration'

function storageKeys(): string[] {
  return Array.from(
    { length: window.localStorage.length },
    (_, index) => window.localStorage.key(index),
  ).filter((key): key is string => key !== null)
}

describe('workspace generation draft isolation', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    window.localStorage.clear()
    vi.spyOn(Date, 'now').mockReturnValue(1234)
  })

  it('migrates legacy drafts when generation tracking is introduced', () => {
    const key = `${DEVICE_DRAFT_PREFIX}chat:course-1`
    window.localStorage.setItem(
      key,
      JSON.stringify({
        schema_version: 1,
        draft_id: 'chat:course-1',
        payload: { body: 'keep me' },
        updated_at: '2026-07-27T10:00:00Z',
      }),
    )

    expect(synchronizeWorkspaceGeneration(3)).toBe(0)

    expect(JSON.parse(window.localStorage.getItem(key) ?? 'null')).toEqual(
      expect.objectContaining({
        schema_version: 2,
        workspace_generation: 3,
        payload: { body: 'keep me' },
      }),
    )
    expect(
      window.localStorage.getItem(WORKSPACE_GENERATION_KEY),
    ).toBe('3')
  })

  it('quarantines drafts from the workspace that was replaced', () => {
    const key = `${DEVICE_DRAFT_PREFIX}chat:course-1`
    window.localStorage.setItem(WORKSPACE_GENERATION_KEY, '1')
    window.localStorage.setItem(
      key,
      JSON.stringify({
        schema_version: 2,
        workspace_generation: 1,
        draft_id: 'chat:course-1',
        payload: { body: 'belongs to old workspace' },
        updated_at: '2026-07-27T10:00:00Z',
      }),
    )

    expect(synchronizeWorkspaceGeneration(2)).toBe(1)

    expect(window.localStorage.getItem(key)).toBeNull()
    const quarantineKey = storageKeys().find((candidate) =>
      candidate.startsWith(QUARANTINED_DRAFT_PREFIX),
    )
    expect(quarantineKey).toBeDefined()
    expect(
      JSON.parse(
        window.localStorage.getItem(quarantineKey ?? '') ?? 'null',
      ),
    ).toEqual(
      expect.objectContaining({
        reason: 'workspace-generation:1->2',
        original_key: key,
        draft: expect.objectContaining({
          workspace_generation: 1,
        }),
      }),
    )
  })

  it('isolates malformed drafts instead of silently deleting them', () => {
    const key = `${DEVICE_DRAFT_PREFIX}notes:course-1`
    window.localStorage.setItem(WORKSPACE_GENERATION_KEY, '1')
    window.localStorage.setItem(key, '{malformed')

    expect(synchronizeWorkspaceGeneration(2)).toBe(1)

    expect(window.localStorage.getItem(key)).toBeNull()
    const quarantineKey = storageKeys().find((candidate) =>
      candidate.startsWith(QUARANTINED_DRAFT_PREFIX),
    )
    const quarantined = JSON.parse(
      window.localStorage.getItem(quarantineKey ?? '') ?? 'null',
    )
    expect(quarantined.draft).toEqual({
      unparsed_raw: '{malformed',
    })
  })
})
