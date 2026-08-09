import {
  act,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { UseChatResult } from './useChat'
import { ChatPanel } from './ChatPanel'

const { useChatMock } = vi.hoisted(() => ({
  useChatMock: vi.fn(),
}))

const { saveChatAnswerAsNoteMock } = vi.hoisted(() => ({
  saveChatAnswerAsNoteMock: vi.fn(),
}))

const {
  useAutosavedDraftMock,
  useInternalNavigationGuardMock,
} = vi.hoisted(() => ({
  useAutosavedDraftMock: vi.fn(),
  useInternalNavigationGuardMock: vi.fn(),
}))

vi.mock('./useChat', () => ({
  useChat: useChatMock,
}))

vi.mock('../notes/noteApi', () => ({
  saveChatAnswerAsNote: saveChatAnswerAsNoteMock,
}))

vi.mock('../reliability', async () => {
  const actual =
    await vi.importActual<typeof import('../reliability')>(
      '../reliability',
    )
  return {
    ...actual,
    useAutosavedDraft: useAutosavedDraftMock,
    useInternalNavigationGuard: useInternalNavigationGuardMock,
  }
})

const TIMESTAMP = '2026-07-27T10:00:00Z'

function autosavedDraftResult(
  overrides: Record<string, unknown> = {},
) {
  return {
    state: 'clean',
    message: '',
    restored: false,
    recoveryConflict: null,
    restoreRecoveryDraft: vi.fn(),
    discardRecoveryDraft: vi.fn(async () => undefined),
    clearDraft: vi.fn(async () => undefined),
    ...overrides,
  }
}

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
    sendMessage: vi.fn(async () => ({
      succeeded: true,
      conversationId: 'conversation-1',
    })),
    startNewAttemptForMessage: vi.fn(async () => true),
    retryLastRequest: vi.fn(async () => true),
    cancelGeneration: vi.fn(async () => undefined),
    clearError: vi.fn(),
    refresh: vi.fn(),
    refreshConversation: vi.fn(),
  }
}

function groundedChatResult(): UseChatResult {
  const base = chatResult()
  return {
    ...base,
    conversation: {
      ...base.conversation!,
      message_count: 2,
      messages: [
        ...base.conversation!.messages,
        {
          id: 'answer-1',
          conversation_id: 'conversation-1',
          turn_id: 'turn-1',
          sequence: 2,
          role: 'assistant',
          content: 'Grounded retrieval uses verified evidence.',
          status: 'complete',
          answer_status: 'answered',
          reply_to_message_id: 'message-1',
          error_message: null,
          provider: 'local',
          model: 'model-1',
          metadata: {},
          citations: [
            {
              id: 'citation-1',
              message_id: 'answer-1',
              ordinal: 1,
              sentence_index: 0,
              start_offset: 0,
              end_offset: 40,
              source_id: 'source-1',
              chunk_id: 'chunk-1',
              chunk_text_hash: 'hash',
              source_title: 'Lecture',
              source_type: 'video',
              quote: 'verified evidence',
              score: 0.9,
              locator: {
                kind: 'video_time',
                schema_version: 1,
                metadata: {},
                job_id: 'job-1',
                asset_id: null,
                start_seconds: 10,
                end_seconds: 20,
                segment_ids: [1],
              },
              created_at: TIMESTAMP,
            },
          ],
          created_at: TIMESTAMP,
          updated_at: TIMESTAMP,
        },
      ],
    },
  }
}

function savedAnswerNote(courseId = 'course-1') {
  return {
    id: 'note-1',
    course_id: courseId,
    title: 'Grounded retrieval',
    body_markdown: 'Grounded retrieval uses verified evidence.',
    revision: 1,
    origin_type: 'chat_answer',
    origin_snapshot: {
      origin_type: 'chat_answer',
      conversation_id: 'conversation-1',
      message_id: 'answer-1',
      answer_text: 'Grounded retrieval uses verified evidence.',
      provider: 'local',
      model: 'model-1',
      citations: [],
    },
    published_snapshot_id: null,
    published_revision: null,
    is_source_outdated: false,
    created_at: TIMESTAMP,
    updated_at: TIMESTAMP,
  }
}

describe('ChatPanel message stream', () => {
  beforeEach(() => {
    useChatMock.mockReturnValue(chatResult())
    useAutosavedDraftMock.mockReset()
    useAutosavedDraftMock.mockReturnValue(autosavedDraftResult())
    useInternalNavigationGuardMock.mockReset()
    useInternalNavigationGuardMock.mockReturnValue(() => true)
    saveChatAnswerAsNoteMock.mockReset()
    saveChatAnswerAsNoteMock.mockResolvedValue(savedAnswerNote())
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

  it('shows the immutable graph route used for a grounded answer', async () => {
    const result = groundedChatResult()
    result.conversation!.messages[1].metadata = {
      graph_context: {
        schema_version: 1,
        course_id: 'course-1',
        graph_version: 2,
        graph_content_hash: 'a'.repeat(64),
        result_hash: 'b'.repeat(64),
        strategy: 'relationship_trace',
        concepts: [
          {
            concept_id: 'full',
            concept_revision: 4,
            preferred_name: 'Full Attention',
          },
          {
            concept_id: 'sparse',
            concept_revision: 2,
            preferred_name: 'Sparse Attention',
          },
          {
            concept_id: 'sliding',
            concept_revision: 2,
            preferred_name: 'Sliding-window Attention',
          },
        ],
        steps: [
          {
            ordinal: 0,
            relation_id: 'full-sparse',
            relation_revision: 5,
            relation_type: 'prerequisite',
            support_basis: 'pedagogical_inference',
            from_concept_id: 'full',
            to_concept_id: 'sparse',
            traversed_against_relation_direction: false,
          },
          {
            ordinal: 1,
            relation_id: 'sparse-sliding',
            relation_revision: 2,
            relation_type: 'prerequisite',
            support_basis: 'pedagogical_inference',
            from_concept_id: 'sparse',
            to_concept_id: 'sliding',
            traversed_against_relation_direction: false,
          },
        ],
      },
    }
    useChatMock.mockReturnValue(result)
    const user = userEvent.setup()

    render(
      <ChatPanel
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-1"
        courseTitle="Course One"
      />,
    )

    expect(screen.getByText('Published v2 - 2 hops')).toBeInTheDocument()
    await user.click(screen.getByText('Graph-guided context'))
    const route = document.querySelector(
      '[aria-label="Graph route used for this answer"]',
    )
    expect(route).toBeInTheDocument()
    if (!route) throw new Error('Graph route was not rendered.')
    expect(route.querySelectorAll('li')).toHaveLength(2)
    expect(route).toHaveTextContent('Full Attention')
    expect(route).toHaveTextContent('Sparse Attention')
    expect(route).toHaveTextContent('Sliding-window Attention')
    expect(route).toHaveTextContent('pedagogical inference')
    expect(screen.getByText(
      'source citations above remain the only factual evidence',
      { exact: false },
    )).toBeInTheDocument()
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

  it('keeps composer drafts isolated when switching A to B and back', async () => {
    const user = userEvent.setup()
    let currentChat = chatResult()
    useChatMock.mockImplementation(() => currentChat)
    const { rerender } = render(
      <ChatPanel
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-1"
      />,
    )
    const composer = screen.getByRole('textbox', {
      name: 'Ask about this course',
    })
    await user.type(composer, 'draft for A')

    currentChat = {
      ...currentChat,
      activeConversationId: 'conversation-2',
      conversation: currentChat.conversation
        ? {
            ...currentChat.conversation,
            id: 'conversation-2',
          }
        : null,
    }
    rerender(
      <ChatPanel
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-1"
      />,
    )
    expect(composer).toHaveValue('')
    await user.type(composer, 'draft for B')

    currentChat = {
      ...currentChat,
      activeConversationId: 'conversation-1',
      conversation: currentChat.conversation
        ? {
            ...currentChat.conversation,
            id: 'conversation-1',
          }
        : null,
    }
    rerender(
      <ChatPanel
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-1"
      />,
    )
    expect(composer).toHaveValue('draft for A')

    currentChat = {
      ...currentChat,
      activeConversationId: 'conversation-2',
      conversation: currentChat.conversation
        ? {
            ...currentChat.conversation,
            id: 'conversation-2',
          }
        : null,
    }
    rerender(
      <ChatPanel
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-1"
      />,
    )
    expect(composer).toHaveValue('draft for B')
  })

  it('keeps a failed send in its submitted scope after navigation', async () => {
    const user = userEvent.setup()
    let resolveSend:
      | ((value: {
          succeeded: boolean
          conversationId: string | null
        }) => void)
      | undefined
    const sendMessage = vi.fn(
      () =>
        new Promise<{
          succeeded: boolean
          conversationId: string | null
        }>((resolve) => {
          resolveSend = resolve
        }),
    )
    let currentChat = {
      ...chatResult(),
      selectedReadySourceCount: 1,
      sendMessage,
    }
    useChatMock.mockImplementation(() => currentChat)
    const { rerender } = render(
      <ChatPanel
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-1"
      />,
    )
    const composer = screen.getByRole('textbox', {
      name: 'Ask about this course',
    })
    await user.type(composer, 'question for A')
    await user.click(
      screen.getByRole('button', { name: 'Send question' }),
    )

    currentChat = {
      ...currentChat,
      activeConversationId: 'conversation-2',
      conversation: currentChat.conversation
        ? {
            ...currentChat.conversation,
            id: 'conversation-2',
          }
        : null,
    }
    rerender(
      <ChatPanel
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-1"
      />,
    )
    expect(composer).toHaveValue('')

    await act(async () => {
      resolveSend?.({
        succeeded: false,
        conversationId: 'conversation-1',
      })
      await Promise.resolve()
    })
    expect(composer).toHaveValue('')

    currentChat = {
      ...currentChat,
      activeConversationId: 'conversation-1',
      conversation: currentChat.conversation
        ? {
            ...currentChat.conversation,
            id: 'conversation-1',
          }
        : null,
    }
    rerender(
      <ChatPanel
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-1"
      />,
    )
    expect(composer).toHaveValue('question for A')
  })

  it('does not move a failed send into another course', async () => {
    const user = userEvent.setup()
    let resolveSend:
      | ((value: {
          succeeded: boolean
          conversationId: string | null
        }) => void)
      | undefined
    const sendMessage = vi.fn(
      () =>
        new Promise<{
          succeeded: boolean
          conversationId: string | null
        }>((resolve) => {
          resolveSend = resolve
        }),
    )
    useChatMock.mockReturnValue({
      ...chatResult(),
      selectedReadySourceCount: 1,
      sendMessage,
    })
    const { rerender } = render(
      <ChatPanel
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-1"
      />,
    )
    const composer = screen.getByRole('textbox', {
      name: 'Ask about this course',
    })
    await user.type(composer, 'question for course A')
    await user.click(
      screen.getByRole('button', { name: 'Send question' }),
    )

    rerender(
      <ChatPanel
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-2"
      />,
    )
    expect(composer).toHaveValue('')

    await act(async () => {
      resolveSend?.({
        succeeded: false,
        conversationId: 'conversation-1',
      })
      await Promise.resolve()
    })
    expect(composer).toHaveValue('')

    rerender(
      <ChatPanel
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-1"
      />,
    )
    expect(composer).toHaveValue('question for course A')
  })

  it('locks a conflicted composer until the user chooses a draft', async () => {
    const user = userEvent.setup()
    const restoreRecoveryDraft = vi.fn()
    const discardRecoveryDraft = vi.fn(async () => undefined)
    useAutosavedDraftMock.mockReturnValue(
      autosavedDraftResult({
        state: 'conflict',
        message: 'Review another saved draft',
        recoveryConflict: { content: 'saved elsewhere' },
        restoreRecoveryDraft,
        discardRecoveryDraft,
      }),
    )

    render(
      <ChatPanel
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-1"
      />,
    )

    expect(
      screen.getByRole('textbox', {
        name: 'Ask about this course',
      }),
    ).toHaveAttribute('readonly')
    expect(
      screen.getByRole('button', { name: 'Send question' }),
    ).toBeDisabled()

    await user.click(
      screen.getByRole('button', { name: 'Restore saved draft' }),
    )
    expect(restoreRecoveryDraft).toHaveBeenCalledTimes(1)

    await user.click(
      screen.getByRole('button', { name: 'Keep current draft' }),
    )
    expect(discardRecoveryDraft).toHaveBeenCalledTimes(1)
  })

  it('does not switch conversations when an unprotected draft stays', async () => {
    const user = userEvent.setup()
    const result = chatResult()
    result.conversations = [
      ...result.conversations,
      {
        ...result.conversations[0],
        id: 'conversation-2',
        title: 'Second chat',
      },
    ]
    const stay = vi.fn().mockReturnValue(false)
    useChatMock.mockReturnValue(result)
    useInternalNavigationGuardMock.mockReturnValue(stay)

    render(
      <ChatPanel
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-1"
      />,
    )

    await user.click(
      screen.getByRole('button', { name: /Second chat/ }),
    )
    expect(result.selectConversation).not.toHaveBeenCalled()

    await user.click(
      screen.getByRole('button', { name: 'New conversation' }),
    )
    expect(result.createConversation).not.toHaveBeenCalled()
    expect(stay).toHaveBeenCalledTimes(2)
  })

  it('saves a completed grounded answer with per-message state and opens the note', async () => {
    const user = userEvent.setup()
    useChatMock.mockReturnValue(groundedChatResult())
    const onOpenNote = vi.fn()

    render(
      <ChatPanel
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-1"
        courseTitle="Course One"
        onOpenNote={onOpenNote}
      />,
    )

    await user.click(
      screen.getByText('Save to notes', { selector: 'button' }),
    )

    await waitFor(() =>
      expect(saveChatAnswerAsNoteMock).toHaveBeenCalledWith(
        'http://127.0.0.1:8001',
        'course-1',
        'answer-1',
        undefined,
        expect.any(AbortSignal),
      ),
    )
    expect(screen.getByText('Saved to notes')).toBeInTheDocument()

    await user.click(
      screen.getByText('Open note', { selector: 'button' }),
    )
    expect(onOpenNote).toHaveBeenCalledWith('note-1')
  })

  it('keeps a save failure attached to its message and offers retry', async () => {
    const user = userEvent.setup()
    useChatMock.mockReturnValue(groundedChatResult())
    saveChatAnswerAsNoteMock.mockRejectedValue(
      new Error('Notes are temporarily unavailable.'),
    )

    render(
      <ChatPanel
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-1"
      />,
    )

    await user.click(
      screen.getByText('Save to notes', { selector: 'button' }),
    )

    expect(
      await screen.findByText('Notes are temporarily unavailable.'),
    ).toBeInTheDocument()
    expect(
      screen.getByText('Retry save to notes', {
        selector: 'button',
      }),
    ).toBeInTheDocument()
  })

  it('ignores a save response that settles after the course changes', async () => {
    const user = userEvent.setup()
    useChatMock.mockReturnValue(groundedChatResult())
    let resolveSave:
      | ((value: ReturnType<typeof savedAnswerNote>) => void)
      | undefined
    saveChatAnswerAsNoteMock.mockReturnValue(
      new Promise((resolve) => {
        resolveSave = resolve
      }),
    )

    const { rerender } = render(
      <ChatPanel
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-1"
      />,
    )
    await user.click(
      screen.getByText('Save to notes', { selector: 'button' }),
    )

    rerender(
      <ChatPanel
        apiBaseUrl="http://127.0.0.1:8001"
        courseId="course-2"
      />,
    )
    await act(async () => {
      resolveSave?.(savedAnswerNote('course-1'))
      await Promise.resolve()
    })

    expect(screen.queryByText('Saved to notes')).not.toBeInTheDocument()
    expect(
      screen.getByText('Save to notes', { selector: 'button' }),
    ).toBeInTheDocument()
  })
})
