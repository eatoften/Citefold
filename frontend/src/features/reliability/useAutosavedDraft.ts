import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react'
import {
  deleteWorkspaceDraft,
  DraftConflictError,
  getWorkspaceDraft,
  putWorkspaceDraft,
} from './draftApi'
import type {
  DeviceDraft,
  DraftSaveState,
  WorkspaceDraft,
} from './draftTypes'
import { useReliabilityContext } from './reliabilityContextValue'
import {
  DEVICE_DRAFT_PREFIX,
  quarantineDeviceDraft,
} from './workspaceGeneration'

const SERVER_SAVE_DELAY_MS = 800

type UseAutosavedDraftOptions<T extends object> = {
  apiBaseUrl: string
  draftId: string
  courseId: string | null
  draftType: string
  entityId?: string | null
  baseUpdatedAt?: string | null
  enabled: boolean
  value: T
  initialValue: T
  onRestore: (payload: T) => void
  requireMatchingBaseUpdatedAt?: boolean
}

type UseAutosavedDraftResult<T extends object> = {
  state: DraftSaveState
  message: string
  restored: boolean
  recoveryConflict: T | null
  restoreRecoveryDraft: () => void
  discardRecoveryDraft: () => Promise<void>
  clearDraft: () => Promise<void>
}

function stableJson(value: object): string {
  return JSON.stringify(value)
}

function storageKey(draftId: string): string {
  return `${DEVICE_DRAFT_PREFIX}${draftId}`
}

function writeDeviceDraft<T extends object>(
  draftId: string,
  draft: DeviceDraft<T>,
): boolean {
  try {
    const serialized = JSON.stringify(draft)
    window.localStorage.setItem(storageKey(draftId), serialized)
    return window.localStorage.getItem(storageKey(draftId)) === serialized
  } catch {
    return false
  }
}

function readDeviceDraft<T extends object>(
  draftId: string,
  workspaceGeneration: number,
): DeviceDraft<T> | null {
  try {
    const key = storageKey(draftId)
    const raw = window.localStorage.getItem(key)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Omit<
      Partial<DeviceDraft<T>>,
      'schema_version' | 'workspace_generation'
    > & {
      schema_version?: number
      workspace_generation?: number
    }
    if (
      ![1, 2].includes(parsed.schema_version ?? 0) ||
      parsed.draft_id !== draftId ||
      !parsed.payload ||
      typeof parsed.payload !== 'object' ||
      typeof parsed.updated_at !== 'string'
    ) {
      window.localStorage.removeItem(key)
      return null
    }
    if (
      parsed.schema_version === 2 &&
      parsed.workspace_generation !== workspaceGeneration
    ) {
      quarantineDeviceDraft(
        key,
        raw,
        `workspace-generation:${String(
          parsed.workspace_generation,
        )}->${workspaceGeneration}`,
      )
      return null
    }
    const current: DeviceDraft<T> = {
      ...(parsed as Omit<
        DeviceDraft<T>,
        'schema_version' | 'workspace_generation'
      >),
      schema_version: 2,
      workspace_generation: workspaceGeneration,
    }
    if (parsed.schema_version === 1) {
      window.localStorage.setItem(key, JSON.stringify(current))
    }
    return current
  } catch {
    return null
  }
}

type RecoveryCandidate<T extends object> = {
  payload: T
  baseUpdatedAt: string | null
  updatedAt: string
  conflictsBase: boolean
}

type RecoverySelection<T extends object> = {
  preferred: RecoveryCandidate<T> | null
  alternate: RecoveryCandidate<T> | null
}

function newestCandidate<T extends object>(
  candidates: RecoveryCandidate<T>[],
): RecoveryCandidate<T> | null {
  return (
    candidates.sort(
      (left, right) =>
        Date.parse(right.updatedAt) - Date.parse(left.updatedAt),
    )[0] ?? null
  )
}

function recoverySelection<T extends object>(
  local: DeviceDraft<T> | null,
  server: WorkspaceDraft<T> | null,
  initialJson: string,
  currentBaseUpdatedAt: string | null,
  requireMatchingBaseUpdatedAt: boolean,
): RecoverySelection<T> {
  const candidates: RecoveryCandidate<T>[] = []
  if (local && stableJson(local.payload) !== initialJson) {
    candidates.push({
      payload: local.payload,
      baseUpdatedAt: local.base_updated_at,
      updatedAt: local.updated_at,
      conflictsBase: false,
    })
  }
  if (server && stableJson(server.payload) !== initialJson) {
    candidates.push({
      payload: server.payload,
      baseUpdatedAt: server.base_updated_at,
      updatedAt: server.updated_at,
      conflictsBase: false,
    })
  }
  const matching = requireMatchingBaseUpdatedAt
    ? candidates.filter((candidate) =>
        sameBaseUpdatedAt(
          candidate.baseUpdatedAt,
          currentBaseUpdatedAt,
        ),
      )
    : candidates
  const selected =
    newestCandidate(matching) ?? newestCandidate(candidates)
  if (!selected) {
    return { preferred: null, alternate: null }
  }
  const preferred = {
    ...selected,
    conflictsBase:
      requireMatchingBaseUpdatedAt && matching.length === 0,
  }
  const preferredJson = stableJson(preferred.payload)
  const alternate = newestCandidate(
    candidates.filter(
      (candidate) => stableJson(candidate.payload) !== preferredJson,
    ),
  )
  return { preferred, alternate }
}

function sameBaseUpdatedAt(
  draftBase: string | null,
  currentBase: string | null,
): boolean {
  if (draftBase === currentBase) return true
  if (!draftBase || !currentBase) return false
  const draftTime = Date.parse(draftBase)
  const currentTime = Date.parse(currentBase)
  return (
    Number.isFinite(draftTime) &&
    Number.isFinite(currentTime) &&
    draftTime === currentTime
  )
}

export function useAutosavedDraft<T extends object>({
  apiBaseUrl,
  draftId,
  courseId,
  draftType,
  entityId = null,
  baseUpdatedAt = null,
  enabled,
  value,
  initialValue,
  onRestore,
  requireMatchingBaseUpdatedAt = false,
}: UseAutosavedDraftOptions<T>): UseAutosavedDraftResult<T> {
  const {
    registerDraftState,
    workspaceGeneration,
    workspaceGenerationResolved,
  } = useReliabilityContext()
  const [state, setState] = useState<DraftSaveState>('clean')
  const [restored, setRestored] = useState(false)
  const [recoveryConflict, setRecoveryConflict] =
    useState<T | null>(null)
  const [hydratedKey, setHydratedKey] = useState<string | null>(null)
  // A client without a known positive revision is always create-only. The
  // backend treats `null` as an unconditional update for legacy callers, so
  // this hook must never send it after an absent or failed hydration.
  const revisionRef = useRef<number>(0)
  const syncSequenceRef = useRef(0)
  const hydrationSequenceRef = useRef(0)
  const completedHydrationKeyRef = useRef<string | null>(null)
  const pendingProtectionKeyRef = useRef<string | null>(null)
  const activeSyncCancelRef = useRef<(() => void) | null>(null)
  const onRestoreRef = useRef(onRestore)
  const persistedDeviceJsonRef = useRef<string | null>(null)
  const [persistedDeviceJson, setPersistedDeviceJson] = useState<
    string | null
  >(null)
  const [persistedServerJson, setPersistedServerJson] = useState<
    string | null
  >(null)
  const initialJson = stableJson(initialValue)
  const valueJson = stableJson(value)
  const hydrationKey = `${workspaceGeneration}:${draftId}:${
    requireMatchingBaseUpdatedAt ? baseUpdatedAt ?? 'new' : 'any'
  }`
  const valueRef = useRef(value)
  const valueJsonRef = useRef(valueJson)
  const recordDevicePersistence = useCallback(
    (payloadJson: string | null) => {
      persistedDeviceJsonRef.current = payloadJson
      setPersistedDeviceJson(payloadJson)
    },
    [],
  )

  useEffect(() => {
    valueRef.current = value
    valueJsonRef.current = valueJson
  }, [value, valueJson])

  useEffect(() => {
    onRestoreRef.current = onRestore
  }, [onRestore])

  useEffect(() => {
    const pendingHydrationEdit =
      enabled &&
      Boolean(courseId) &&
      hydratedKey !== hydrationKey &&
      valueJson !== initialJson
    const registeredState = pendingHydrationEdit
      ? 'sync_failed'
      : state
    registerDraftState(draftId, {
      state: registeredState,
      persistedLocally:
        valueJson === initialJson ||
        persistedDeviceJson === valueJson ||
        persistedServerJson === valueJson,
    })
    return () => registerDraftState(draftId, null)
  }, [
    courseId,
    draftId,
    enabled,
    hydratedKey,
    hydrationKey,
    initialJson,
    persistedDeviceJson,
    persistedServerJson,
    registerDraftState,
    state,
    valueJson,
  ])

  useEffect(() => {
    if (
      !enabled ||
      !courseId ||
      !workspaceGenerationResolved ||
      completedHydrationKeyRef.current === hydrationKey
    ) {
      return
    }
    const sequence = ++hydrationSequenceRef.current
    setHydratedKey(null)
    revisionRef.current = 0
    setPersistedServerJson(null)
    setRestored(false)
    setRecoveryConflict(null)
    const controller = new AbortController()
    const local = readDeviceDraft<T>(
      draftId,
      workspaceGeneration,
    )
    recordDevicePersistence(
      local ? stableJson(local.payload) : null,
    )
    const isCurrentRequest = (): boolean =>
      !controller.signal.aborted &&
      sequence === hydrationSequenceRef.current
    const completeHydration = (): void => {
      if (!isCurrentRequest()) return
      completedHydrationKeyRef.current = hydrationKey
      setHydratedKey(hydrationKey)
    }
    void getWorkspaceDraft<T>(
      apiBaseUrl,
      draftId,
      controller.signal,
    )
      .then((server) => {
        if (!isCurrentRequest()) return
        revisionRef.current = server?.revision ?? 0
        setPersistedServerJson(
          server ? stableJson(server.payload) : null,
        )
        const selection = recoverySelection(
          local,
          server,
          initialJson,
          baseUpdatedAt,
          requireMatchingBaseUpdatedAt,
        )
        const recovered = selection.preferred
        if (valueJsonRef.current !== initialJson) {
          // The editor stayed interactive while generation/server state was
          // resolving. Preserve that text and keep any distinct recovery
          // candidate separate instead of overwriting either side.
          if (
            recovered &&
            stableJson(recovered.payload) !== valueJsonRef.current
          ) {
            setRecoveryConflict(recovered.payload)
            setState('conflict')
          } else {
            setState(
              persistedDeviceJsonRef.current === valueJsonRef.current
                ? 'saved_local'
                : 'sync_failed',
            )
          }
          completeHydration()
          return
        }
        if (
          recovered &&
          stableJson(recovered.payload) !== initialJson
        ) {
          if (
            recovered.conflictsBase
          ) {
            setRecoveryConflict(recovered.payload)
            setState('conflict')
            completeHydration()
            return
          }
          onRestoreRef.current(recovered.payload)
          setRestored(true)
          if (selection.alternate) {
            setRecoveryConflict(selection.alternate.payload)
            setState('conflict')
          } else {
            setState(
              server &&
                stableJson(server.payload) ===
                  stableJson(recovered.payload)
                ? 'saved'
                : 'saved_local',
            )
          }
        } else {
          setState('clean')
        }
        completeHydration()
      })
      .catch(() => {
        if (!isCurrentRequest()) return
        if (valueJsonRef.current !== initialJson) {
          const recovered = recoverySelection(
            local,
            null,
            initialJson,
            baseUpdatedAt,
            requireMatchingBaseUpdatedAt,
          ).preferred
          if (
            recovered &&
            stableJson(recovered.payload) !== valueJsonRef.current
          ) {
            setRecoveryConflict(recovered.payload)
            setState('conflict')
          } else {
            setState(
              persistedDeviceJsonRef.current === valueJsonRef.current
                ? 'saved_local'
                : 'sync_failed',
            )
          }
          completeHydration()
          return
        }
        if (local && stableJson(local.payload) !== initialJson) {
          if (
            requireMatchingBaseUpdatedAt &&
            !sameBaseUpdatedAt(
              local.base_updated_at,
              baseUpdatedAt,
            )
          ) {
            setRecoveryConflict(local.payload)
            setState('conflict')
          } else {
            onRestoreRef.current(local.payload)
            setRestored(true)
            setState('saved_local')
          }
        }
        completeHydration()
      })
    return () => {
      controller.abort()
      if (hydrationSequenceRef.current === sequence) {
        hydrationSequenceRef.current += 1
      }
    }
  }, [
    apiBaseUrl,
    baseUpdatedAt,
    courseId,
    draftId,
    enabled,
    hydrationKey,
    initialJson,
    recordDevicePersistence,
    requireMatchingBaseUpdatedAt,
    workspaceGeneration,
    workspaceGenerationResolved,
  ])

  useEffect(() => {
    if (
      !enabled ||
      !courseId ||
      hydratedKey === hydrationKey ||
      valueJson === initialJson
    ) {
      return
    }

    const key = storageKey(draftId)
    if (pendingProtectionKeyRef.current !== hydrationKey) {
      try {
        const existingRaw = window.localStorage.getItem(key)
        if (existingRaw) {
          let existingPayloadJson: string | null = null
          try {
            const existing = JSON.parse(existingRaw) as {
              payload?: object
            }
            if (existing.payload) {
              existingPayloadJson = stableJson(existing.payload)
            }
          } catch {
            // Invalid data is quarantined before the current edit is stored.
          }
          if (existingPayloadJson !== valueJson) {
            quarantineDeviceDraft(
              key,
              existingRaw,
              `superseded-during-hydration:${hydrationKey}`,
            )
            if (window.localStorage.getItem(key) !== null) {
              recordDevicePersistence(null)
              setState('sync_failed')
              return
            }
          }
        }
      } catch {
        recordDevicePersistence(null)
        setState('sync_failed')
        return
      }
    }

    const deviceDraft: DeviceDraft<T> = {
      schema_version: 2,
      workspace_generation: workspaceGeneration,
      draft_id: draftId,
      course_id: courseId,
      draft_type: draftType,
      entity_id: entityId,
      payload: value,
      base_updated_at: baseUpdatedAt,
      updated_at: new Date().toISOString(),
    }
    if (writeDeviceDraft(draftId, deviceDraft)) {
      pendingProtectionKeyRef.current = hydrationKey
      recordDevicePersistence(valueJson)
      setState('saved_local')
    } else {
      recordDevicePersistence(null)
      setState('sync_failed')
    }
  }, [
    baseUpdatedAt,
    courseId,
    draftId,
    draftType,
    enabled,
    entityId,
    hydratedKey,
    hydrationKey,
    initialJson,
    recordDevicePersistence,
    value,
    valueJson,
    workspaceGeneration,
  ])

  useEffect(() => {
    if (
      !enabled ||
      !courseId ||
      hydratedKey !== hydrationKey ||
      recoveryConflict !== null
    ) {
      return
    }
    if (valueJson === initialJson) {
      try {
        window.localStorage.removeItem(storageKey(draftId))
      } catch {
        // There is no unsaved payload, so a stale recovery copy is harmless.
      }
      recordDevicePersistence(null)
      setState('clean')
      const revision = revisionRef.current
      // Returning to the durable value must not turn a failed conditional
      // cleanup into an unconditional next write. Revision 0 keeps any future
      // edit create-only if another editor advanced or recreated the draft.
      revisionRef.current = 0
      setPersistedServerJson(null)
      if (revision > 0) {
        void deleteWorkspaceDraft(
          apiBaseUrl,
          draftId,
          revision,
        ).catch(() => undefined)
      }
      return
    }

    const deviceDraft: DeviceDraft<T> = {
      schema_version: 2,
      workspace_generation: workspaceGeneration,
      draft_id: draftId,
      course_id: courseId,
      draft_type: draftType,
      entity_id: entityId,
      payload: value,
      base_updated_at: baseUpdatedAt,
      updated_at: new Date().toISOString(),
    }
    if (writeDeviceDraft(draftId, deviceDraft)) {
      recordDevicePersistence(valueJson)
      setState('saving')
    } else {
      recordDevicePersistence(null)
      setState('sync_failed')
    }

    const sequence = ++syncSequenceRef.current
    const controller = new AbortController()
    const timerId = window.setTimeout(() => {
      void putWorkspaceDraft<T>(
        apiBaseUrl,
        draftId,
        {
          course_id: courseId,
          draft_type: draftType,
          entity_id: entityId,
          payload: value,
          expected_revision: revisionRef.current,
          base_updated_at: baseUpdatedAt,
        },
        controller.signal,
      )
        .then((saved) => {
          if (sequence !== syncSequenceRef.current) return
          revisionRef.current = saved.revision
          setPersistedServerJson(stableJson(saved.payload))
          const savedDeviceDraft = {
            ...deviceDraft,
            payload: saved.payload,
            updated_at: saved.updated_at,
          } satisfies DeviceDraft<T>
          if (
            writeDeviceDraft(
              draftId,
              savedDeviceDraft,
            )
          ) {
            recordDevicePersistence(stableJson(saved.payload))
          } else {
            recordDevicePersistence(null)
            // The server revision is durable even when this device cannot
            // refresh its local recovery copy.
          }
          setState('saved')
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted) return
          if (sequence !== syncSequenceRef.current) return
          if (error instanceof DraftConflictError) {
            revisionRef.current = error.current?.revision ?? 0
            setPersistedServerJson(
              error.current
                ? stableJson(error.current.payload)
                : null,
            )
            if (
              error.current &&
              stableJson(error.current.payload) !== valueJson
            ) {
              setRecoveryConflict(error.current.payload as T)
            }
            setState('conflict')
          } else {
            setState(
              persistedDeviceJsonRef.current === valueJson
                ? 'saved_local'
                : 'sync_failed',
            )
          }
        })
    }, SERVER_SAVE_DELAY_MS)

    const cancelSync = () => {
      window.clearTimeout(timerId)
      controller.abort()
    }
    activeSyncCancelRef.current = cancelSync
    return () => {
      if (activeSyncCancelRef.current === cancelSync) {
        activeSyncCancelRef.current = null
      }
      cancelSync()
    }
  }, [
    apiBaseUrl,
    baseUpdatedAt,
    courseId,
    draftId,
    draftType,
    enabled,
    entityId,
    initialJson,
    hydratedKey,
    hydrationKey,
    recordDevicePersistence,
    recoveryConflict,
    value,
    valueJson,
    workspaceGeneration,
  ])

  const clearDraft = useCallback(async (): Promise<void> => {
    syncSequenceRef.current += 1
    activeSyncCancelRef.current?.()
    activeSyncCancelRef.current = null
    pendingProtectionKeyRef.current = null
    try {
      window.localStorage.removeItem(storageKey(draftId))
    } catch {
      // The domain save remains authoritative even if device cleanup fails.
    }
    recordDevicePersistence(null)
    const revision = revisionRef.current
    // Clearing follows a successful domain save, but it must not use an
    // unconditional DELETE when this editor never observed a server draft.
    // Revision 0 keeps the next write create-only if another editor creates a
    // draft between hydration, cleanup, and the user's next edit.
    revisionRef.current = 0
    setPersistedServerJson(null)
    setState('clean')
    setRestored(false)
    setRecoveryConflict(null)
    if (revision === null || revision === 0) {
      return
    }
    try {
      await deleteWorkspaceDraft(
        apiBaseUrl,
        draftId,
        revision,
      )
    } catch {
      // A stale server draft is safer than making a successful domain save
      // appear to fail. Revision 0 makes the next write surface it as a
      // conflict instead of overwriting or deleting another editor's work.
    }
  }, [apiBaseUrl, draftId, recordDevicePersistence])

  const restoreRecoveryDraft = useCallback((): void => {
    if (!recoveryConflict) return
    const payload = recoveryConflict
    const payloadJson = stableJson(payload)
    const currentPayload = valueRef.current
    const currentPayloadJson = stableJson(currentPayload)
    try {
      const key = storageKey(draftId)
      if (
        currentPayloadJson !== initialJson &&
        currentPayloadJson !== payloadJson
      ) {
        const currentDraft: DeviceDraft<T> = {
          schema_version: 2,
          workspace_generation: workspaceGeneration,
          draft_id: draftId,
          course_id: courseId ?? '',
          draft_type: draftType,
          entity_id: entityId,
          payload: currentPayload,
          base_updated_at: baseUpdatedAt,
          updated_at: new Date().toISOString(),
        }
        if (!courseId || !writeDeviceDraft(draftId, currentDraft)) {
          recordDevicePersistence(null)
          setState('sync_failed')
          return
        }
        recordDevicePersistence(currentPayloadJson)
        const currentRaw = window.localStorage.getItem(key)
        if (!currentRaw) {
          recordDevicePersistence(null)
          setState('sync_failed')
          return
        }
        quarantineDeviceDraft(
          key,
          currentRaw,
          `replaced-by-recovery:${hydrationKey}`,
        )
        if (window.localStorage.getItem(key) !== null) {
          setState('sync_failed')
          return
        }
        recordDevicePersistence(null)
      }
    } catch {
      setState('sync_failed')
      return
    }
    onRestoreRef.current(payload)
    setRecoveryConflict(null)
    setRestored(true)
    setState('saved_local')
  }, [
    draftId,
    draftType,
    entityId,
    baseUpdatedAt,
    courseId,
    hydrationKey,
    initialJson,
    recordDevicePersistence,
    recoveryConflict,
    workspaceGeneration,
  ])

  const discardRecoveryDraft =
    useCallback(async (): Promise<void> => {
      if (!recoveryConflict) return

      const sequence = ++syncSequenceRef.current
      activeSyncCancelRef.current?.()
      activeSyncCancelRef.current = null
      pendingProtectionKeyRef.current = null

      const currentPayload = valueRef.current
      const currentPayloadJson = valueJsonRef.current
      const knownRevision = revisionRef.current
      const removeDeviceDraft = (): void => {
        try {
          window.localStorage.removeItem(storageKey(draftId))
        } catch {
          // The current value equals the domain value, so a stale recovery
          // copy is harmless if this device cannot remove it immediately.
        }
        recordDevicePersistence(null)
      }

      if (currentPayloadJson === initialJson) {
        if (knownRevision === 0) {
          removeDeviceDraft()
          // Zero is a create-only CAS token. Keeping it avoids a later edit
          // using the unconditional `null` write after absence was observed.
          revisionRef.current = 0
          setPersistedServerJson(null)
          setRecoveryConflict(null)
          setRestored(false)
          setState('clean')
          return
        }

        try {
          await deleteWorkspaceDraft(
            apiBaseUrl,
            draftId,
            knownRevision,
          )
          if (sequence !== syncSequenceRef.current) return
          removeDeviceDraft()
          revisionRef.current = 0
          setPersistedServerJson(null)
          setRecoveryConflict(null)
          setRestored(false)
          setState('clean')
          return
        } catch {
          // A failed conditional delete may mean a third editor advanced the
          // draft. Re-read it before making any further write decision.
        }

        try {
          const current = await getWorkspaceDraft<T>(
            apiBaseUrl,
            draftId,
          )
          if (sequence !== syncSequenceRef.current) return
          if (!current) {
            removeDeviceDraft()
            revisionRef.current = 0
            setPersistedServerJson(null)
            setRecoveryConflict(null)
            setRestored(false)
            setState('clean')
            return
          }
          revisionRef.current = current.revision
          setPersistedServerJson(stableJson(current.payload))
          if (
            stableJson(current.payload) !== currentPayloadJson
          ) {
            setRecoveryConflict(current.payload)
            setState('conflict')
          } else {
            removeDeviceDraft()
            setRecoveryConflict(null)
            setState('saved')
          }
        } catch {
          if (sequence !== syncSequenceRef.current) return
          // Preserve both the known revision and the visible alternate until
          // the server can be checked again.
          revisionRef.current = knownRevision
          setState('conflict')
        }
        return
      }

      const deviceDraft: DeviceDraft<T> = {
        schema_version: 2,
        workspace_generation: workspaceGeneration,
        draft_id: draftId,
        course_id: courseId ?? '',
        draft_type: draftType,
        entity_id: entityId,
        payload: currentPayload,
        base_updated_at: baseUpdatedAt,
        updated_at: new Date().toISOString(),
      }
      if (!courseId || !writeDeviceDraft(draftId, deviceDraft)) {
        recordDevicePersistence(null)
        // Keep the alternate visible because the preferred value is not yet
        // protected on this device.
        setState('sync_failed')
        return
      }

      recordDevicePersistence(currentPayloadJson)
      setRecoveryConflict(null)
      setRestored(false)
      setState('saved_local')
    }, [
      apiBaseUrl,
      baseUpdatedAt,
      courseId,
      draftId,
      draftType,
      entityId,
      initialJson,
      recordDevicePersistence,
      recoveryConflict,
      workspaceGeneration,
    ])

  const message = {
    clean: '',
    saving: 'Saving…',
    saved: 'Saved',
    saved_local: 'Saved on this device',
    sync_failed: "Couldn't save — keep this window open",
    conflict: 'Saved on this device — review another editor’s changes',
  }[state]

  return {
    state,
    message,
    restored,
    recoveryConflict,
    restoreRecoveryDraft,
    discardRecoveryDraft,
    clearDraft,
  }
}
