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
}

type UseAutosavedDraftResult = {
  state: DraftSaveState
  message: string
  restored: boolean
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

function newerDraft<T extends object>(
  local: DeviceDraft<T> | null,
  server: WorkspaceDraft<T> | null,
): T | null {
  if (!local) return server?.payload ?? null
  if (!server) return local.payload
  return Date.parse(local.updated_at) > Date.parse(server.updated_at)
    ? local.payload
    : server.payload
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
}: UseAutosavedDraftOptions<T>): UseAutosavedDraftResult {
  const {
    registerDraftState,
    workspaceGeneration,
    workspaceGenerationResolved,
  } = useReliabilityContext()
  const [state, setState] = useState<DraftSaveState>('clean')
  const [restored, setRestored] = useState(false)
  const [hydratedKey, setHydratedKey] = useState<string | null>(null)
  const revisionRef = useRef<number | null>(null)
  const syncSequenceRef = useRef(0)
  const restoredKeyRef = useRef<string | null>(null)
  const persistedDeviceJsonRef = useRef<string | null>(null)
  const [persistedDeviceJson, setPersistedDeviceJson] = useState<
    string | null
  >(null)
  const initialJson = stableJson(initialValue)
  const valueJson = stableJson(value)
  const hydrationKey = `${workspaceGeneration}:${draftId}`
  const valueJsonRef = useRef(valueJson)
  const recordDevicePersistence = useCallback(
    (payloadJson: string | null) => {
      persistedDeviceJsonRef.current = payloadJson
      setPersistedDeviceJson(payloadJson)
    },
    [],
  )

  useEffect(() => {
    valueJsonRef.current = valueJson
  }, [valueJson])

  useEffect(() => {
    registerDraftState(draftId, {
      state,
      persistedLocally:
        state === 'clean' ||
        state === 'saved' ||
        persistedDeviceJson === valueJson,
    })
    return () => registerDraftState(draftId, null)
  }, [
    draftId,
    persistedDeviceJson,
    registerDraftState,
    state,
    valueJson,
  ])

  useEffect(() => {
    if (
      !enabled ||
      !courseId ||
      !workspaceGenerationResolved ||
      restoredKeyRef.current === hydrationKey
    ) {
      return
    }
    restoredKeyRef.current = hydrationKey
    setHydratedKey(null)
    revisionRef.current = null
    setRestored(false)
    const controller = new AbortController()
    const local = readDeviceDraft<T>(
      draftId,
      workspaceGeneration,
    )
    recordDevicePersistence(
      local ? stableJson(local.payload) : null,
    )
    void getWorkspaceDraft<T>(
      apiBaseUrl,
      draftId,
      controller.signal,
    )
      .then((server) => {
        if (controller.signal.aborted) return
        revisionRef.current = server?.revision ?? null
        if (valueJsonRef.current !== initialJson) {
          // The editor stayed interactive while generation/server state was
          // resolving. Never overwrite text entered during that window.
          setState('sync_failed')
          setHydratedKey(hydrationKey)
          return
        }
        const recovered = newerDraft(local, server)
        if (
          recovered &&
          stableJson(recovered) !== initialJson
        ) {
          onRestore(recovered)
          setRestored(true)
          setState(
            server &&
              stableJson(server.payload) === stableJson(recovered)
              ? 'saved'
              : 'saved_local',
          )
        } else {
          setState('clean')
        }
        setHydratedKey(hydrationKey)
      })
      .catch(() => {
        if (controller.signal.aborted) return
        if (valueJsonRef.current !== initialJson) {
          setState('sync_failed')
          setHydratedKey(hydrationKey)
          return
        }
        if (local && stableJson(local.payload) !== initialJson) {
          onRestore(local.payload)
          setRestored(true)
          setState('saved_local')
        }
        setHydratedKey(hydrationKey)
      })
    return () => controller.abort()
  }, [
    apiBaseUrl,
    courseId,
    draftId,
    enabled,
    hydrationKey,
    initialJson,
    onRestore,
    recordDevicePersistence,
    workspaceGeneration,
    workspaceGenerationResolved,
  ])

  useEffect(() => {
    if (
      !enabled ||
      !courseId ||
      hydratedKey !== hydrationKey
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
      if (revision !== null) {
        revisionRef.current = null
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
            revisionRef.current = error.current?.revision ?? null
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

    return () => {
      window.clearTimeout(timerId)
      controller.abort()
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
    value,
    valueJson,
    workspaceGeneration,
  ])

  const clearDraft = useCallback(async (): Promise<void> => {
    syncSequenceRef.current += 1
    try {
      window.localStorage.removeItem(storageKey(draftId))
    } catch {
      // The domain save remains authoritative even if device cleanup fails.
    }
    recordDevicePersistence(null)
    const revision = revisionRef.current
    revisionRef.current = null
    setState('clean')
    setRestored(false)
    try {
      await deleteWorkspaceDraft(
        apiBaseUrl,
        draftId,
        revision ?? undefined,
      )
    } catch {
      // A stale server draft is safer than making a successful domain save
      // appear to fail. The next load will resolve it against base_updated_at.
    }
  }, [apiBaseUrl, draftId, recordDevicePersistence])

  const message = {
    clean: '',
    saving: 'Saving…',
    saved: 'Saved',
    saved_local: 'Saved on this device',
    sync_failed: "Couldn't save — keep this window open",
    conflict: 'Saved on this device — review another editor’s changes',
  }[state]

  return { state, message, restored, clearDraft }
}
