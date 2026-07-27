import type { TrashItem } from './workspaceApi'

export const TRASH_CREATED_EVENT = 'vcc:trash-created'

export type TrashCreatedDetail = Pick<
  TrashItem,
  'entity_type' | 'entity_id'
>

export function announceTrashCreated(
  detail: TrashCreatedDetail,
): void {
  window.dispatchEvent(
    new CustomEvent<TrashCreatedDetail>(
      TRASH_CREATED_EVENT,
      { detail },
    ),
  )
}
