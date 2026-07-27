import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { UseChatResult } from './useChat'
import { ChatPanel } from './ChatPanel'

const { useChatMock } = vi.hoisted(() => ({
  useChatMock: vi.fn(),
}))

vi.mock('./useChat', () => ({
  useChat: useChatMock,
}))

const TIMESTAMP = '2026-07-27T10:00:00Z'

function chatResult(): UseChatResult {
  return {
    conversations: [
      {
        id: 'conversation-1',
        course_id: 'course-1',
        title: 'Grounded chat',
        status: 'active',
        selected_source_ids: [],
        message_count: 1,
        last_message_at: TIMESTAMP,
        created_at: TIMESTAMP,
        updated_at: TIMESTAMP,
      },
    ],
    conversation: {
      id: 'conversation-1',
      course_id: 'course-1',
      title: 'Grounded chat',
      status: 'active',
      selected_source_ids: [],
      message_count: 1,
      last_message_at: TIMESTAMP,
      created_at: TIMESTAMP,
      updated_at: TIMESTAMP,
      messages: [
        {
          id: 'message-1',
          conversation_id: 'conversation-1',
          turn_id: 'turn-1',
          sequence: 1,
          role: 'user',
          content: 'What is grounded retrieval?',
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
      ],
    },
    activeConversationId: 'conversation-1',
    sources: [],
    selectedSourceIds: [],
    selectedReadySourceCount: 0,
    pendingQuestion: null,
    error: null,
    isLoadingWorkspace: false,
    isLoadingConversation: false,
    isCreatingConversation: false,
    isUpdatingSources: false,
    isSending: false,
    generationTask: null,
    generationPollingExhausted: false,
    selectConversation: vi.fn(),
    createConversation: vi.fn(async () => null),
    toggleSource: vi.fn(async () => undefined),
    selectAllReadySources: vi.fn(async () => undefined),
    clearSelectedSources: vi.fn(async () => undefined),
    sendMessage: vi.fn(async () => true),
    startNewAttemptForMessage: vi.fn(async () => true),
    retryLastRequest: vi.fn(async () => true),
    cancelGeneration: vi.fn(async () => undefined),
    clearError: vi.fn(),
    refresh: vi.fn(),
    refreshConversation: vi.fn(),
  }
}

describe('ChatPanel message stream', () => {
  beforeEach(() => {
    useChatMock.mockReturnValue(chatResult())
  })

  it('uses a named polite log instead of a nested main landmark', () => {
    const { container } = render(
      <ChatPanel
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-1"
        courseTitle="Course One"
      />,
    )

    const messageLog = screen.getByRole('log', {
      name: 'Conversation messages',
    })

    expect(messageLog).toHaveAttribute('aria-live', 'polite')
    expect(messageLog).toHaveAttribute(
      'aria-relevant',
      'additions text',
    )
    expect(messageLog).toHaveTextContent(
      'What is grounded retrieval?',
    )
    expect(container.querySelector('main')).not.toBeInTheDocument()
  })

  it('keeps the composer focusable while a message is sending', () => {
    useChatMock.mockReturnValue({
      ...chatResult(),
      isSending: true,
    })

    render(
      <ChatPanel
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-1"
        courseTitle="Course One"
      />,
    )

    const composer = screen.getByRole('textbox', {
      name: 'Ask about this course',
    })
    expect(composer).not.toBeDisabled()
    expect(composer).toHaveAttribute('readonly')
    expect(composer).toHaveAttribute('aria-busy', 'true')
  })
})
