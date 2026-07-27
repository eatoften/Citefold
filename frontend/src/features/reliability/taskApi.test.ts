import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  cancelReliableTask,
  retryReliableTask,
} from './taskApi'
import type { ReliableTask } from './taskTypes'

const API_BASE_URL = 'http://api.test/'
const TIMESTAMP = '2026-07-27T10:00:00Z'

function task(
  values: Partial<ReliableTask> = {},
): ReliableTask {
  return {
    id: 'task/answer 1',
    kind: 'chat_generation',
    course_id: 'course-1',
    resource_type: 'chat_conversation',
    resource_id: 'conversation-1',
    status: 'running',
    payload: {},
    result: null,
    idempotency_key: null,
    active_key: null,
    priority: 0,
    attempt: 1,
    max_attempts: 3,
    recovery_count: 0,
    progress: {
      current: 1,
      total: 2,
      stage: 'generating',
      message: 'Generating',
      details: {},
    },
    cancel_requested_at: null,
    worker_id: null,
    error_code: null,
    error_message: null,
    retryable: true,
    available_at: TIMESTAMP,
    created_at: TIMESTAMP,
    updated_at: TIMESTAMP,
    started_at: TIMESTAMP,
    completed_at: null,
    heartbeat_at: TIMESTAMP,
    ...values,
  }
}

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('reliable task mutations', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('requests cooperative cancellation through the encoded task id', async () => {
    const canceled = task({ status: 'canceling' })
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(canceled))
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      cancelReliableTask(API_BASE_URL, canceled.id),
    ).resolves.toEqual(canceled)
    expect(fetchMock).toHaveBeenCalledWith(
      'http://api.test/tasks/task%2Fanswer%201/cancel',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Accept: 'application/json',
        }),
      }),
    )
  })

  it('retries a failed durable task through the existing task id', async () => {
    const retried = task({ status: 'queued', attempt: 2 })
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(retried))
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      retryReliableTask(API_BASE_URL, retried.id),
    ).resolves.toEqual(retried)
    expect(fetchMock).toHaveBeenCalledWith(
      'http://api.test/tasks/task%2Fanswer%201/retry',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Accept: 'application/json',
        }),
      }),
    )
  })
})
