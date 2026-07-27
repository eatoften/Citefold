export type DraftSaveState =
  | 'clean'
  | 'saving'
  | 'saved'
  | 'saved_local'
  | 'sync_failed'
  | 'conflict'

export type WorkspaceDraft<T extends object = Record<string, unknown>> = {
  id: string
  course_id: string
  draft_type: string
  entity_id: string | null
  payload: T
  revision: number
  base_updated_at: string | null
  created_at: string
  updated_at: string
}

export type WorkspaceDraftPut<T extends object> = {
  course_id: string
  draft_type: string
  entity_id: string | null
  payload: T
  expected_revision: number | null
  base_updated_at: string | null
}

export type DeviceDraft<T extends object> = {
  schema_version: 2
  workspace_generation: number
  draft_id: string
  course_id: string
  draft_type: string
  entity_id: string | null
  payload: T
  base_updated_at: string | null
  updated_at: string
}
