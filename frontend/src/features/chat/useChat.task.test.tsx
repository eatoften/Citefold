import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReliableTask } from '../reliability'
import type { CourseSource } from '../sources/sourceTypes'
import type {
  ChatConversation,
  ChatConversationDetail,
  ChatTurnResponse,
} from './chatTypes'

const chatApi = vi.hoisted(() => ({
  listChatConversations: vi.fn(),
  listCourseSources: vi.fn(),
  getChatConversation: vi.fn(),
  createChatConversation: vi.fn(),
  updateChatConversation: vi.fn(),
}))

const taskApi = vi.hoisted(() => ({
  enqueueChatGeneration: vi.fn(),
  waitForReliableTask: vi.fn(),
  retryReliableTask: vi.fn(),
  cancelReliableTask: vi.fn(),
}))

vi.mock('./chatApi', () => ({
  isAbortError: (error: unknown) =>
    error instanceof DOMException && error.name === 'AbortError',
  listChatConversations: chatApi.listChatConversations,
  listCourseSources: chatApi.listCourseSources,
  getChatConversation: chatApi.getChatConversation,
  createChatConversation: chatApi.createChatConversation,
  updateChatConversation: chatApi.updateChatConversation,
}))

vi.mock('../reliability', () => ({
  enqueueChatGeneration: taskApi.enqueueChatGeneration,
  waitForReliableTask: taskApi.waitForReliableTask,
  retryReliableTask: taskApi.retryReliableTask,
  cancelReliableTask: taskApi.cancelReliableTask,
}))

import { useChat } from './useChat'

const TIMESTAMP = '2026-07-27T10:00:00Z'

const conversation: ChatConversation = {
  id: 'conversation-1',
  course_id: 'course-1',
  title: 'Grounded chat',
  status: 'active',
  selected_source_ids: ['source-1'],
  message_count: 0,
  last_message_at: null,
  created_at: TIMESTAMP,
  updated_at: TIMESTAMP,
}

const source: CourseSource = {
  id: 'source-1',
  course_id: 'course-1',
  origin_type: 'source_asset',
  origin_id: 'asset-1',
  source_type: 'text',
  title: 'Notes',
  content_status: 'ready',
  index_status: 'ready',
  index_model: 'local',
  index_dimension: 3,
  enabled: true,
  chunk_count: 1,
  indexed_chunk_count: 1,
  size_bytes: 10,
  mime_type: 'text/plain',
  metadata: {},
  error_message: null,
  index_error: null,
  created_at: TIMESTAMP,
  updated_at: TIMESTAMP,
  indexed_at: TIMESTAMP,
}

function reliableTask<TResult extends object>(
  values: Partial<ReliableTask<TResult>>,
): ReliableTask<TResult> {
  return {
    id: 'message-task-1',
    kind: 'chat_generation',
    course_id: 'course-1',
    resource_type: 'chat_conversation',
    resource_id: conversation.id,
    status: 'queued',
    payload: {},
    result: null,
    idempotency_key: null,
    active_key: null,
    priority: 0,
    attempt: 1,
    max_attempts: 3,
    recovery_count: 0,
    progress: {
      current: 0,
      total: 2,
      stage: 'queued',
      message: 'Queued',
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
    started_at: null,
    completed_at: null,
    heartbeat_at: null,
    ...values,
  }
}

function turn(): ChatTurnResponse {
  const nextConversation = {
    ...conversation,
    message_count: 2,
    last_message_at: TIMESTAMP,
  }
  return {
    turn_id: 'turn-1',
    client_request_id: 'request-1',
    status: 'completed',
    source_ids: ['source-1'],
    replayed: false,
    conversation: nextConversation,
    user_message: {
      id: 'user-1',
      conversation_id: conversation.id,
      turn_id: 'turn-1',
      sequence: 1,
      role: 'user',
      content: 'What is retrieval?',
      status: 'complete',
      answer_status: null,
      reply_to_message_id: null,
      error_message: null,
      provider: null,
      model: null,
      metadata: {},
      citations: [],
      created_at: TIMESTAMP,
      updated_at: TIMESTAMP,
    },
    assistant_message: {
      id: 'assistant-1',
      conversation_id: conversation.id,
      turn_id: 'turn-1',
      sequence: 2,
      role: 'assistant',
      content: 'Retrieval selects source evidence.',
      status: 'complete',
      answer_status: 'abstained',
      reply_to_message_id: 'user-1',
      error_message: null,
      provider: 'local',
      model: 'test-model',
      metadata: {},
      citations: [],
      created_at: TIMESTAMP,
      updated_at: TIMESTAMP,
    },
  }
}

describe('useChat durable message tasks', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    chatApi.listChatConversations.mockResolvedValue([conversation])
    chatApi.listCourseSources.mockResolvedValue([source])
    chatApi.getChatConversation.mockResolvedValue({
      ...conversation,
      messages: [],
    } satisfies ChatConversationDetail)
    taskApi.enqueueChatGeneration.mockResolvedValue(reliableTask({}))
  })

  async function renderReadyChat() {
    const view = renderHook(() =>
      useChat({
        apiBaseUrl: 'http://api.test',
        courseId: 'course-1',
        model: 'test-model',
      }),
    )
    await waitFor(() => {
      expect(view.result.current.conversation?.id).toBe(
        conversation.id,
      )
    })
    return view
  }

  it('enqueues, polls, and merges a completed answer task', async () => {
    const completedTurn = turn()
    taskApi.waitForReliableTask.mockImplementation(
      async (
        _base: string,
        _taskId: string,
        options: {
          onProgress?: (task: ReliableTask) => void
        },
      ) => {
        options.onProgress?.(
          reliableTask({
            status: 'running',
            progress: {
              current: 1,
              total: 2,
              stage: 'grounding',
              message: 'Checking evidence',
              details: {},
            },
          }),
        )
        return reliableTask({
          status: 'succeeded',
          result: { turn: completedTurn },
          completed_at: TIMESTAMP,
        })
      },
    )
    const view = await renderReadyChat()

    let sent = false
    await act(async () => {
      sent = await view.result.current.sendMessage(
        'What is retrieval?',
      )
    })

    expect(sent).toBe(true)
    expect(taskApi.enqueueChatGeneration).toHaveBeenCalledWith(
      'http://api.test',
      conversation.id,
      expect.objectContaining({
        content: 'What is retrieval?',
        source_ids: ['source-1'],
        model: 'test-model',
      }),
      expect.any(AbortSignal),
    )
    expect(taskApi.waitForReliableTask).toHaveBeenCalledWith(
      'http://api.test',
      'message-task-1',
      expect.objectContaining({
        signal: expect.any(AbortSignal),
      }),
    )
    expect(view.result.current.conversation?.messages).toHaveLength(2)
    expect(view.result.current.generationTask).toBeNull()
  })

  it('retries the same failed durable task instead of duplicating a send', async () => {
    taskApi.waitForReliableTask
      .mockRejectedValueOnce(new Error('Local model unavailable.'))
      .mockResolvedValueOnce(
        reliableTask({
          status: 'succeeded',
          result: { turn: turn() },
          attempt: 2,
          completed_at: TIMESTAMP,
        }),
      )
    taskApi.retryReliableTask.mockResolvedValue(
      reliableTask({ attempt: 2 }),
    )
    const view = await renderReadyChat()

    await act(async () => {
      expect(
        await view.result.current.sendMessage('What is retrieval?'),
      ).toBe(false)
    })
    expect(view.result.current.error?.retry.kind).toBe('message-task')

    await act(async () => {
      expect(await view.result.current.retryLastRequest()).toBe(true)
    })

    expect(taskApi.retryReliableTask).toHaveBeenCalledWith(
      'http://api.test',
      'message-task-1',
    )
    expect(taskApi.enqueueChatGeneration).toHaveBeenCalledTimes(1)
    expect(view.result.current.conversation?.messages).toHaveLength(2)
  })

  it('ignores a late cancellation after the chat epoch changes', async () => {
    const secondConversation: ChatConversation = {
      ...conversation,
      id: 'conversation-2',
      course_id: 'course-2',
      title: 'Second course chat',
      selected_source_ids: ['source-2'],
    }
    const secondSource: CourseSource = {
      ...source,
      id: 'source-2',
      course_id: 'course-2',
      origin_id: 'asset-2',
      title: 'Second course notes',
    }
    let resolveCancel:
      | ((value: ReliableTask) => void)
      | undefined
    const cancelResponse = new Promise<ReliableTask>((resolve) => {
      resolveCancel = resolve
    })
    taskApi.cancelReliableTask.mockReturnValue(cancelResponse)
    taskApi.waitForReliableTask.mockImplementation(
      (
        _apiBaseUrl: string,
        _taskId: string,
        options: { signal?: AbortSignal },
      ) =>
        new Promise((_, reject) => {
          options.signal?.addEventListener(
            'abort',
            () =>
              reject(
                new DOMException('Chat request aborted.', 'AbortError'),
              ),
            { once: true },
          )
        }),
    )
    chatApi.listChatConversations.mockImplementation(
      (_apiBaseUrl: string, courseId: string) =>
        Promise.resolve(
          courseId === 'course-2'
            ? [secondConversation]
            : [conversation],
        ),
    )
    chatApi.listCourseSources.mockImplementation(
      (_apiBaseUrl: string, courseId: string) =>
        Promise.resolve(
          courseId === 'course-2' ? [secondSource] : [source],
        ),
    )
    chatApi.getChatConversation.mockImplementation(
      (_apiBaseUrl: string, conversationId: string) =>
        Promise.resolve({
          ...(conversationId === secondConversation.id
            ? secondConversation
            : conversation),
          messages: [],
        }),
    )

    const view = renderHook(
      ({ courseId }: { courseId: string }) =>
        useChat({
          apiBaseUrl: 'http://api.test',
          courseId,
          model: 'test-model',
        }),
      { initialProps: { courseId: 'course-1' } },
    )
    await waitFor(() => {
      expect(view.result.current.conversation?.id).toBe(
        conversation.id,
      )
    })

    let sendPromise!: Promise<boolean>
    act(() => {
      sendPromise = view.result.current.sendMessage(
        'What is retrieval?',
      )
    })
    await waitFor(() => {
      expect(view.result.current.generationTask?.id).toBe(
        'message-task-1',
      )
    })
    let cancelPromise!: Promise<void>
    act(() => {
      cancelPromise = view.result.current.cancelGeneration()
    })

    view.rerender({ courseId: 'course-2' })
    await waitFor(() => {
      expect(view.result.current.conversation?.id).toBe(
        secondConversation.id,
      )
    })
    await expect(sendPromise).resolves.toBe(false)

    await act(async () => {
      resolveCancel?.(
        reliableTask({
          status: 'canceling',
          progress: {
            current: 0,
            total: 2,
            stage: 'canceling',
            message: 'Stale first-course cancellation',
            details: {},
          },
        }),
      )
      await cancelPromise
    })

    expect(view.result.current.conversation?.id).toBe(
      secondConversation.id,
    )
    expect(view.result.current.generationTask).toBeNull()
    expect(view.result.current.error).toBeNull()
  })
})
