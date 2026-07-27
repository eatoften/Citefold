import {
  AlertTriangle,
  Check,
  CloudOff,
  LoaderCircle,
} from 'lucide-react'
import type { DraftSaveState } from './draftTypes'

export function SaveStatus({
  state,
  message,
}: {
  state: DraftSaveState
  message: string
}) {
  if (state === 'clean' || !message) return null
  const Icon =
    state === 'saving'
      ? LoaderCircle
      : state === 'saved'
        ? Check
        : state === 'conflict' || state === 'sync_failed'
          ? AlertTriangle
          : CloudOff
  return (
    <span
      className={`draft-save-status ${state}`}
      role={
        state === 'sync_failed' || state === 'conflict'
          ? 'alert'
          : 'status'
      }
    >
      <Icon
        aria-hidden="true"
        className={state === 'saving' ? 'draft-save-spin' : undefined}
        size={14}
      />
      {message}
    </span>
  )
}
