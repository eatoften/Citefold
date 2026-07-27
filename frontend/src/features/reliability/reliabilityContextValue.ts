import { createContext, useContext } from 'react'
import type { DraftSaveState } from './draftTypes'

export type DraftRegistryEntry = {
  state: DraftSaveState
  persistedLocally: boolean
}

export type ReliabilityContextValue = {
  draftStates: ReadonlyMap<string, DraftRegistryEntry>
  registerDraftState: (
    draftId: string,
    entry: DraftRegistryEntry | null,
  ) => void
  hasUnprotectedChanges: boolean
  workspaceGeneration: number
  workspaceGenerationResolved: boolean
}

const UNMANAGED_RELIABILITY_CONTEXT: ReliabilityContextValue = {
  draftStates: new Map(),
  registerDraftState: () => undefined,
  hasUnprotectedChanges: false,
  workspaceGeneration: 1,
  workspaceGenerationResolved: true,
}

export const ReliabilityContext =
  createContext<ReliabilityContextValue>(
    UNMANAGED_RELIABILITY_CONTEXT,
  )

export function useReliabilityContext(): ReliabilityContextValue {
  return useContext(ReliabilityContext)
}
