export type ReliableTaskStatus =
  | 'queued'
  | 'running'
  | 'canceling'
  | 'succeeded'
  | 'failed'
  | 'canceled'

export type TaskProgress = {
  current: number
  total: number | null
  stage: string | null
  message: string | null
  details: Record<string, unknown>
  fraction?: number | null
}

export type ReliableTask<TResult extends object = Record<string, unknown>> = {
  id: string
  kind: string
  course_id: string | null
  resource_type: string | null
  resource_id: string | null
  status: ReliableTaskStatus
  payload: Record<string, unknown>
  result: TResult | null
  idempotency_key: string | null
  active_key: string | null
  priority: number
  attempt: number
  max_attempts: number
  recovery_count: number
  progress: TaskProgress
  cancel_requested_at: string | null
  worker_id: string | null
  error_code: string | null
  error_message: string | null
  retryable: boolean
  available_at: string
  created_at: string
  updated_at: string
  started_at: string | null
  completed_at: string | null
  heartbeat_at: string | null
}

export type SourceAssetTaskResponse<TAsset extends object> = {
  asset: TAsset
  task: ReliableTask
}
