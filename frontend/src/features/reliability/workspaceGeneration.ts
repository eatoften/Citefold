export const DEVICE_DRAFT_PREFIX = 'vcc:workspace-draft:'
export const QUARANTINED_DRAFT_PREFIX =
  'vcc:workspace-draft-quarantine:'
export const WORKSPACE_GENERATION_KEY = 'vcc:workspace-generation'
export const PENDING_WORKSPACE_RESTORE_IDENTITY_KEY =
  'vcc:pending-workspace-restore'
export const INITIAL_WORKSPACE_GENERATION = 1

export function pendingWorkspaceRestoreIdentityExists(): boolean {
  try {
    return (
      window.localStorage.getItem(
        PENDING_WORKSPACE_RESTORE_IDENTITY_KEY,
      ) !== null
    )
  } catch {
    return false
  }
}

export function readStoredWorkspaceGeneration(): number | null {
  try {
    const parsed = Number(
      window.localStorage.getItem(WORKSPACE_GENERATION_KEY),
    )
    return Number.isInteger(parsed) && parsed >= 1 ? parsed : null
  } catch {
    return null
  }
}

export function quarantineDeviceDraft(
  storageKey: string,
  raw: string,
  reason: string,
): void {
  try {
    const suffix = storageKey.slice(DEVICE_DRAFT_PREFIX.length)
    const quarantineKey = `${QUARANTINED_DRAFT_PREFIX}${Date.now()}:${suffix}`
    let draft: unknown
    try {
      draft = JSON.parse(raw) as unknown
    } catch {
      draft = { unparsed_raw: raw }
    }
    window.localStorage.setItem(
      quarantineKey,
      JSON.stringify({
        schema_version: 1,
        quarantined_at: new Date().toISOString(),
        reason,
        original_key: storageKey,
        draft,
      }),
    )
    window.localStorage.removeItem(storageKey)
  } catch {
    // If quarantine storage itself is unavailable, leave the original copy in
    // place. useAutosavedDraft still rejects a mismatched generation.
  }
}

export function synchronizeWorkspaceGeneration(
  workspaceGeneration: number,
): number {
  if (
    !Number.isInteger(workspaceGeneration) ||
    workspaceGeneration < INITIAL_WORKSPACE_GENERATION
  ) {
    throw new Error('Workspace generation is invalid.')
  }
  let quarantined = 0
  try {
    const previous = readStoredWorkspaceGeneration()
    const draftKeys: string[] = []
    for (let index = 0; index < window.localStorage.length; index += 1) {
      const key = window.localStorage.key(index)
      if (key?.startsWith(DEVICE_DRAFT_PREFIX)) draftKeys.push(key)
    }
    for (const key of draftKeys) {
      const raw = window.localStorage.getItem(key)
      if (!raw) continue
      try {
        const parsed = JSON.parse(raw) as {
          schema_version?: number
          workspace_generation?: number
        }
        if (previous === null && parsed.schema_version === 1) {
          window.localStorage.setItem(
            key,
            JSON.stringify({
              ...parsed,
              schema_version: 2,
              workspace_generation: workspaceGeneration,
            }),
          )
          continue
        }
        if (
          parsed.schema_version === 2 &&
          parsed.workspace_generation === workspaceGeneration
        ) {
          continue
        }
      } catch {
        // Invalid drafts are isolated by the same recoverable path.
      }
      quarantineDeviceDraft(
        key,
        raw,
        `workspace-generation:${previous ?? 'unknown'}->${workspaceGeneration}`,
      )
      quarantined += 1
    }
    window.localStorage.setItem(
      WORKSPACE_GENERATION_KEY,
      String(workspaceGeneration),
    )
  } catch {
    // The caller still keeps the server generation in memory. Draft reads
    // reject mismatched generations even when localStorage cannot be swept.
  }
  return quarantined
}
