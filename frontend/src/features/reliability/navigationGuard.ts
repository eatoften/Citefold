import { useCallback } from 'react'
import { useReliabilityContext } from './reliabilityContextValue'

export const UNPROTECTED_NAVIGATION_MESSAGE =
  "Some changes couldn't be saved yet. Leave this view and discard them?"

export function confirmInternalNavigation(
  hasUnprotectedChanges: boolean,
  confirmLeave: (message: string) => boolean = (message) =>
    window.confirm(message),
): boolean {
  return (
    !hasUnprotectedChanges ||
    confirmLeave(UNPROTECTED_NAVIGATION_MESSAGE)
  )
}

export function useInternalNavigationGuard(): () => boolean {
  const { hasUnprotectedChanges } = useReliabilityContext()
  return useCallback(
    () => confirmInternalNavigation(hasUnprotectedChanges),
    [hasUnprotectedChanges],
  )
}
