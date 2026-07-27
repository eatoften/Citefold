import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import {
  ReliabilityContext,
  type DraftRegistryEntry,
  type ReliabilityContextValue,
} from './reliabilityContextValue'
import { getWorkspaceRestoreStatus } from './workspaceApi'
import {
  INITIAL_WORKSPACE_GENERATION,
  pendingWorkspaceRestoreIdentityExists,
  readStoredWorkspaceGeneration,
  synchronizeWorkspaceGeneration,
} from './workspaceGeneration'

const reloadWorkspaceWindow = () => window.location.reload()

export function ReliabilityProvider({
  children,
  apiBaseUrl,
  reloadWindow = reloadWorkspaceWindow,
}: {
  children: ReactNode
  apiBaseUrl?: string
  reloadWindow?: () => void
}) {
  const [draftStates, setDraftStates] = useState(
    () => new Map<string, DraftRegistryEntry>(),
  )
  const [workspaceGeneration, setWorkspaceGeneration] = useState(
    () =>
      readStoredWorkspaceGeneration() ??
      INITIAL_WORKSPACE_GENERATION,
  )
  const [
    workspaceGenerationResolved,
    setWorkspaceGenerationResolved,
  ] = useState(() => !apiBaseUrl)
  const workspaceGenerationRef = useRef(workspaceGeneration)

  useEffect(() => {
    if (!apiBaseUrl) {
      const fallback =
        readStoredWorkspaceGeneration() ??
        INITIAL_WORKSPACE_GENERATION
      synchronizeWorkspaceGeneration(fallback)
      workspaceGenerationRef.current = fallback
      setWorkspaceGeneration(fallback)
      setWorkspaceGenerationResolved(true)
      return
    }
    const workspaceApiBaseUrl = apiBaseUrl
    const controller = new AbortController()
    let retryTimer: number | undefined
    function scheduleRetry() {
      retryTimer = window.setTimeout(resolveGeneration, 1500)
    }
    function resolveGeneration() {
      void getWorkspaceRestoreStatus(
        workspaceApiBaseUrl,
        controller.signal,
      )
        .then((status) => {
          if (controller.signal.aborted) return
          const generationChanged =
            workspaceGenerationRef.current !==
            status.workspace_generation
          synchronizeWorkspaceGeneration(status.workspace_generation)
          workspaceGenerationRef.current = status.workspace_generation
          setWorkspaceGeneration(status.workspace_generation)
          setWorkspaceGenerationResolved(true)
          if (generationChanged) {
            reloadWindow()
            return
          }
          if (
            pendingWorkspaceRestoreIdentityExists() &&
            status.pending
          ) {
            scheduleRetry()
          }
        })
        .catch(() => {
          if (controller.signal.aborted) return
          if (pendingWorkspaceRestoreIdentityExists()) {
            // A queued restore may already have replaced the backend
            // workspace. Keep old device drafts isolated until its durable
            // generation can be checked.
            setWorkspaceGenerationResolved(false)
            scheduleRetry()
            return
          }
          const fallback =
            readStoredWorkspaceGeneration() ??
            INITIAL_WORKSPACE_GENERATION
          synchronizeWorkspaceGeneration(fallback)
          setWorkspaceGeneration(fallback)
          setWorkspaceGenerationResolved(true)
        })
    }
    resolveGeneration()
    return () => {
      controller.abort()
      if (retryTimer !== undefined) window.clearTimeout(retryTimer)
    }
  }, [apiBaseUrl, reloadWindow])

  const registerDraftState = useCallback(
    (draftId: string, entry: DraftRegistryEntry | null) => {
      setDraftStates((current) => {
        const next = new Map(current)
        if (entry === null) next.delete(draftId)
        else next.set(draftId, entry)
        return next
      })
    },
    [],
  )

  const contextValue = useMemo<ReliabilityContextValue>(
    () => ({
      draftStates,
      registerDraftState,
      hasUnprotectedChanges: [...draftStates.values()].some(
        (entry) =>
          entry.state !== 'clean' && !entry.persistedLocally,
      ),
      workspaceGeneration,
      workspaceGenerationResolved,
    }),
    [
      draftStates,
      registerDraftState,
      workspaceGeneration,
      workspaceGenerationResolved,
    ],
  )

  useEffect(() => {
    if (!contextValue.hasUnprotectedChanges) return
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', handleBeforeUnload)
    return () =>
      window.removeEventListener('beforeunload', handleBeforeUnload)
  }, [contextValue.hasUnprotectedChanges])

  return (
    <ReliabilityContext.Provider value={contextValue}>
      {children}
    </ReliabilityContext.Provider>
  )
}
