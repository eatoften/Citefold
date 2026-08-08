import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { CourseSource } from '../sources/sourceTypes'
import type {
  ChatConversation,
  ChatConversationDetail,
} from './chatTypes'

const api = vi.hoisted(() => ({
  listChatConversations: vi.fn(),
  listCourseSources: vi.fn(),
  getChatConversation: vi.fn(),
  createChatConversation: vi.fn(),
  sendChatMessage: vi.fn(),
  updateChatConversation: vi.fn(),
}))

vi.mock('./chatApi', () => ({
  ChatApiError: class ChatApiError extends Error {},
  isAbortError: (error: unknown) =>
    error instanceof DOMException && error.name === 'AbortError',
  listChatConversations: api.listChatConversations,
  listCourseSources: api.listCourseSources,
  getChatConversation: api.getChatConversation,
  createChatConversation: api.createChatConversation,
  sendChatMessage: api.sendChatMessage,
  updateChatConversation: api.updateChatConversation,
}))

import { useChat } from './useChat'

function conversation(id: string): ChatConversation {
  return {
    id,
    course_id: 'course-a',
    title: `Conversation ${id}`,
    status: 'active',
    selected_source_ids: ['job:lecture'],
    message_count: 0,
    last_message_at: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
}

const source: CourseSource = {
  id: 'job:lecture',
  course_id: 'course-a',
  origin_type: 'video_job',
  origin_id: 'lecture',
  source_type: 'video',
  title: 'Lecture',
  content_status: 'ready',
  index_status: 'ready',
  index_model: 'local',
  index_dimension: 3,
  enabled: true,
  chunk_count: 1,
  indexed_chunk_count: 1,
  projection_generation_id: 'generation-1',
  projection_manifest_hash: 'a'.repeat(64),
  size_bytes: 100,
  mime_type: 'video/mp4',
  metadata: {},
  error_message: null,
  index_error: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  indexed_at: '2026-01-01T00:00:00Z',
}

function detail(item: ChatConversation): ChatConversationDetail {
  return { ...item, messages: [] }
}

describe('useChat conversation route control', () => {
  const first = conversation('conversation-a')
  const second = conversation('conversation-b')

  beforeEach(() => {
    api.listChatConversations.mockResolvedValue([first, second])
    api.listCourseSources.mockResolvedValue([source])
    api.getChatConversation.mockImplementation(
      async (_base: string, id: string) =>
        detail(id === second.id ? second : first),
    )
  })

  it('opens the linked conversation and follows history changes without pushing', async () => {
    const onConversationChange = vi.fn()
    const view = renderHook(
      ({ initialConversationId }) =>
        useChat({
          apiBaseUrl: 'http://127.0.0.1:8001',
          courseId: 'course-a',
          initialConversationId,
          onConversationChange,
        }),
      {
        initialProps: {
          initialConversationId: second.id as string | null,
        },
      },
    )

    await waitFor(() => {
      expect(view.result.current.activeConversationId).toBe(second.id)
    })
    expect(onConversationChange).not.toHaveBeenCalled()

    view.rerender({ initialConversationId: first.id })
    await waitFor(() => {
      expect(view.result.current.activeConversationId).toBe(first.id)
    })
    expect(onConversationChange).not.toHaveBeenCalled()

    act(() => view.result.current.selectConversation(second.id))
    expect(onConversationChange).toHaveBeenCalledWith(
      second.id,
      'push',
    )
  })

  it('canonicalizes an unlinked chat page to the default conversation', async () => {
    const onConversationChange = vi.fn()
    const view = renderHook(() =>
      useChat({
        apiBaseUrl: 'http://127.0.0.1:8001',
        courseId: 'course-a',
        onConversationChange,
      }),
    )

    await waitFor(() => {
      expect(view.result.current.activeConversationId).toBe(first.id)
    })
    expect(onConversationChange).toHaveBeenCalledWith(
      first.id,
      'replace',
    )
  })

  it('clears a conversation when browser history returns to a new-chat URL', async () => {
    const onConversationChange = vi.fn()
    const view = renderHook(
      ({ initialConversationId }) =>
        useChat({
          apiBaseUrl: 'http://127.0.0.1:8001',
          courseId: 'course-a',
          initialConversationId,
          onConversationChange,
        }),
      {
        initialProps: {
          initialConversationId: second.id as string | null,
        },
      },
    )

    await waitFor(() => {
      expect(view.result.current.activeConversationId).toBe(second.id)
    })

    view.rerender({ initialConversationId: null })

    await waitFor(() => {
      expect(view.result.current.activeConversationId).toBeNull()
      expect(view.result.current.conversation).toBeNull()
    })
    expect(onConversationChange).not.toHaveBeenCalled()
  })
})
