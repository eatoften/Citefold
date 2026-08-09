import {
  AlertCircle,
  ArrowUpRight,
  BookOpenText,
  Check,
  ChevronDown,
  FileText,
  LoaderCircle,
  MessageSquareText,
  NotebookPen,
  Plus,
  RefreshCw,
  RotateCcw,
  Send,
  Sparkles,
  Video,
  X,
} from 'lucide-react'
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { formatSourceLocator } from '../citations/citationFormat'
import type { CourseSource } from '../sources/sourceTypes'
import { saveChatAnswerAsNote } from '../notes/noteApi'
import type { NotebookNoteSaveState } from '../notes/noteTypes'
import type { ChatCitation, ChatMessage } from './chatTypes'
import { ChatGraphContextDisclosure } from './ChatGraphContextDisclosure'
import { isAbortError } from './chatApi'
import { useChat } from './useChat'
import {
  SaveStatus,
  useAutosavedDraft,
  useInternalNavigationGuard,
} from '../reliability'
import './ChatPanel.css'

const DEFAULT_RECOMMENDED_QUESTIONS = [
  'Summarize the key ideas in the selected sources.',
  'What concepts should I review first?',
  'Compare the main approaches and their tradeoffs.',
] as const

export type ChatPanelProps = {
  apiBaseUrl: string
  courseId: string | null
  courseTitle?: string
  model?: string | null
  compact?: boolean
  recommendedQuestions?: readonly string[]
  initialConversationId?: string | null
  onConversationChange?: (
    conversationId: string | null,
    mode: 'push' | 'replace',
  ) => void
  onOpenCitation?: (
    citation: ChatCitation,
    trigger: HTMLButtonElement,
  ) => void
  onOpenNote?: (noteId: string) => void
}

type SentenceCitations = {
  sentenceIndex: number
  startOffset: number
  endOffset: number
  text: string
  citations: ChatCitation[]
}

function sourceIcon(source: CourseSource) {
  return source.source_type === 'video' ||
    source.source_type === 'audio' ? (
    <Video aria-hidden="true" size={15} />
  ) : (
    <FileText aria-hidden="true" size={15} />
  )
}

function sourceStatusLabel(source: CourseSource): string {
  if (!source.enabled) {
    return 'Disabled'
  }
  if (source.content_status === 'ready') {
    return `${source.chunk_count} excerpt${
      source.chunk_count === 1 ? '' : 's'
    }`
  }
  if (source.content_status === 'failed') {
    return source.error_message ?? 'Processing failed'
  }
  return source.content_status === 'processing'
    ? 'Processing'
    : 'Waiting to process'
}

function formatConversationTime(value: string | null): string {
  if (!value) return 'No messages'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return 'Recently'
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
  }).format(parsed)
}

function sentenceCitations(message: ChatMessage): SentenceCitations[] {
  const contentCharacters = [...message.content]
  const grouped = new Map<number, ChatCitation[]>()
  for (const citation of message.citations) {
    const current = grouped.get(citation.sentence_index) ?? []
    current.push(citation)
    grouped.set(citation.sentence_index, current)
  }

  return [...grouped.entries()]
    .sort(([left], [right]) => left - right)
    .flatMap(([sentenceIndex, citations]) => {
      const startOffset = Math.min(
        ...citations.map((citation) => citation.start_offset),
      )
      const endOffset = Math.max(
        ...citations.map((citation) => citation.end_offset),
      )
      if (
        startOffset < 0 ||
        endOffset <= startOffset ||
        endOffset > contentCharacters.length
      ) {
        return []
      }
      return [
        {
          sentenceIndex,
          startOffset,
          endOffset,
          text: contentCharacters.slice(startOffset, endOffset).join(''),
          citations: citations.sort(
            (left, right) => left.ordinal - right.ordinal,
          ),
        },
      ]
    })
}

function CitationDetails({
  citation,
  onOpenCitation,
}: {
  citation: ChatCitation
  onOpenCitation?: (
    citation: ChatCitation,
    trigger: HTMLButtonElement,
  ) => void
}) {
  return (
    <button
      type="button"
      className="chat-citation"
      disabled={!onOpenCitation}
      aria-label={`Open source ${citation.ordinal}: ${citation.source_title}, ${formatSourceLocator(citation.locator)}`}
      title="Open the exact source location"
      onClick={(event) =>
        onOpenCitation?.(citation, event.currentTarget)
      }
    >
      <span>[{citation.ordinal}]</span>
      <span>{citation.source_title}</span>
      <small>{formatSourceLocator(citation.locator)}</small>
      <ArrowUpRight aria-hidden="true" size={13} />
    </button>
  )
}

function AssistantMessage({
  message,
  onStartNewAttempt,
  onOpenCitation,
  statusPollingExhausted,
  onRefreshStatus,
  newAttemptDisabled,
  noteSaveState,
  onSaveToNotes,
  onOpenNote,
}: {
  message: ChatMessage
  onStartNewAttempt: (message: ChatMessage) => void
  onOpenCitation?: (
    citation: ChatCitation,
    trigger: HTMLButtonElement,
  ) => void
  statusPollingExhausted: boolean
  onRefreshStatus: () => void
  newAttemptDisabled: boolean
  noteSaveState: NotebookNoteSaveState
  onSaveToNotes: (message: ChatMessage) => void
  onOpenNote?: (noteId: string) => void
}) {
  if (message.status === 'generating') {
    if (statusPollingExhausted) {
      return (
        <div className="chat-message assistant stalled">
          <div className="chat-message-label">
            <AlertCircle aria-hidden="true" size={14} />
            Status check paused
          </div>
          <p>
            This answer may still be running. Refresh its status to check
            again.
          </p>
          <button type="button" onClick={onRefreshStatus}>
            <RefreshCw aria-hidden="true" size={14} />
            Refresh status
          </button>
        </div>
      )
    }
    return (
      <div className="chat-message assistant generating">
        <div className="chat-message-label">
          <Sparkles aria-hidden="true" size={14} />
          Course assistant
        </div>
        <div className="chat-generating" role="status">
          <LoaderCircle aria-hidden="true" size={16} />
          Grounding the answer in your sources…
        </div>
      </div>
    )
  }

  if (message.status === 'failed') {
    return (
      <div className="chat-message assistant failed">
        <div className="chat-message-label">
          <AlertCircle aria-hidden="true" size={14} />
          Answer failed
        </div>
        <p>
          {message.error_message ??
            'The local model could not finish this answer.'}
        </p>
        <button
          type="button"
          title="Starts a separate turn using the same question."
          disabled={newAttemptDisabled}
          onClick={() => onStartNewAttempt(message)}
        >
          <RotateCcw aria-hidden="true" size={14} />
          Ask again as a new turn
        </button>
      </div>
    )
  }

  if (message.answer_status === 'abstained') {
    return (
      <div className="chat-message assistant abstained">
        <div className="chat-message-label">
          <BookOpenText aria-hidden="true" size={14} />
          Not enough evidence
        </div>
        <p>{message.content}</p>
      </div>
    )
  }

  const sentences = sentenceCitations(message)
  const canSaveToNotes =
    message.status === 'complete' &&
    message.answer_status === 'answered' &&
    message.citations.length > 0
  return (
    <div className="chat-message assistant">
      <div className="chat-message-label">
        <Sparkles aria-hidden="true" size={14} />
        Course assistant
        {message.model && <small>{message.model}</small>}
      </div>
      {sentences.length ? (
        <div className="chat-grounded-answer">
          {sentences.map((sentence) => (
            <section key={sentence.sentenceIndex}>
              <p>{sentence.text}</p>
              <div
                className="chat-citation-list"
                aria-label={`Sources for sentence ${
                  sentence.sentenceIndex + 1
                }`}
              >
                {sentence.citations.map((citation) => (
                  <CitationDetails
                    key={citation.id}
                    citation={citation}
                    onOpenCitation={onOpenCitation}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      ) : (
        <p>{message.content}</p>
      )}
      <ChatGraphContextDisclosure metadata={message.metadata} />
      {canSaveToNotes && (
        <div className="chat-message-note-actions">
          {noteSaveState.status === 'saved' ? (
            <>
              <span role="status">
                <Check aria-hidden="true" size={14} />
                Saved to notes
              </span>
              {onOpenNote && (
                <button
                  type="button"
                  onClick={() => onOpenNote(noteSaveState.noteId)}
                >
                  Open note
                  <ArrowUpRight aria-hidden="true" size={14} />
                </button>
              )}
            </>
          ) : (
            <button
              type="button"
              disabled={noteSaveState.status === 'pending'}
              onClick={() => onSaveToNotes(message)}
            >
              {noteSaveState.status === 'pending' ? (
                <LoaderCircle
                  aria-hidden="true"
                  className="chat-icon-spin"
                  size={14}
                />
              ) : (
                <NotebookPen aria-hidden="true" size={14} />
              )}
              {noteSaveState.status === 'pending'
                ? 'Saving to notes'
                : noteSaveState.status === 'error'
                  ? 'Retry save to notes'
                  : 'Save to notes'}
            </button>
          )}
          {noteSaveState.status === 'error' && (
            <small role="alert">{noteSaveState.message}</small>
          )}
        </div>
      )}
    </div>
  )
}

export function ChatPanel({
  apiBaseUrl,
  courseId,
  courseTitle,
  model,
  compact = false,
  recommendedQuestions = DEFAULT_RECOMMENDED_QUESTIONS,
  initialConversationId = null,
  onConversationChange,
  onOpenCitation,
  onOpenNote,
}: ChatPanelProps) {
  const chat = useChat({
    apiBaseUrl,
    courseId,
    model,
    initialConversationId,
    onConversationChange,
  })
  const canLeaveCurrentDraft = useInternalNavigationGuard()
  const composerScope = `${courseId ?? 'none'}:${
    chat.activeConversationId ?? 'new'
  }`
  const composerScopeRef = useRef(composerScope)
  composerScopeRef.current = composerScope
  const [draftsByScope, setDraftsByScope] = useState<
    Record<string, string>
  >({})
  const draft = draftsByScope[composerScope] ?? ''
  const setDraftForScope = useCallback(
    (scope: string, content: string) => {
      setDraftsByScope((current) => {
        if ((current[scope] ?? '') === content) return current
        return { ...current, [scope]: content }
      })
    },
    [],
  )
  const setDraft = useCallback(
    (content: string) => {
      setDraftForScope(composerScopeRef.current, content)
    },
    [setDraftForScope],
  )
  const [isSourcePickerOpen, setIsSourcePickerOpen] = useState(!compact)
  const [noteSaveStates, setNoteSaveStates] = useState<
    Record<string, NotebookNoteSaveState>
  >({})
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const noteSaveEpochRef = useRef(0)
  const noteSaveOperationsRef = useRef<Record<string, number>>({})
  const noteSaveControllersRef = useRef<
    Map<string, AbortController>
  >(new Map())

  useEffect(() => {
    setIsSourcePickerOpen(!compact)
  }, [compact, courseId])

  useEffect(() => {
    const controllers = noteSaveControllersRef.current
    noteSaveEpochRef.current += 1
    for (const controller of controllers.values()) {
      controller.abort()
    }
    controllers.clear()
    noteSaveOperationsRef.current = {}
    setNoteSaveStates({})
    return () => {
      for (const controller of controllers.values()) {
        controller.abort()
      }
      controllers.clear()
    }
  }, [courseId])

  const chatDraft = useAutosavedDraft({
    apiBaseUrl,
    draftId: `chat-composer:${courseId ?? 'none'}:${
      chat.activeConversationId ?? 'new'
    }`,
    courseId,
    draftType: 'chat_composer',
    entityId: chat.activeConversationId,
    enabled: Boolean(courseId),
    value: { content: draft },
    initialValue: { content: '' },
    onRestore: (payload) =>
      setDraftForScope(composerScope, payload.content),
  })

  const messages = chat.conversation?.messages ?? []
  const hasGeneratingMessage = messages.some(
    (message) =>
      message.role === 'assistant' && message.status === 'generating',
  )
  const isBusy =
    chat.isLoadingWorkspace ||
    chat.isLoadingConversation ||
    chat.isCreatingConversation ||
    chat.isUpdatingSources ||
    chat.isSending
  const canSend =
    Boolean(courseId) &&
    Boolean(draft.trim()) &&
    chat.selectedReadySourceCount > 0 &&
    !hasGeneratingMessage &&
    !isBusy &&
    chatDraft.recoveryConflict === null
  const isSourceSelectionBusy = isBusy || hasGeneratingMessage

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({
      block: 'end',
      behavior: 'smooth',
    })
  }, [chat.isSending, messages.length])

  const sourceIds = useMemo(
    () => new Set(chat.selectedSourceIds),
    [chat.selectedSourceIds],
  )

  async function submitMessage(content = draft): Promise<void> {
    const question = content.trim()
    if (!question || !courseId) return
    const submittedScope = composerScope
    const submittedCourseId = courseId
    const result = await chat.sendMessage(question)
    const resolvedScope = result.conversationId
      ? `${submittedCourseId}:${result.conversationId}`
      : submittedScope
    if (result.succeeded) {
      setDraftForScope(submittedScope, '')
      if (resolvedScope !== submittedScope) {
        setDraftForScope(resolvedScope, '')
      }
      await chatDraft.clearDraft()
    } else {
      setDraftForScope(submittedScope, question)
      if (resolvedScope !== submittedScope) {
        setDraftForScope(resolvedScope, question)
      }
    }
  }

  async function saveAnswerToNotes(
    message: ChatMessage,
  ): Promise<void> {
    if (
      !courseId ||
      message.role !== 'assistant' ||
      message.status !== 'complete' ||
      message.answer_status !== 'answered' ||
      message.citations.length === 0
    ) {
      return
    }

    noteSaveControllersRef.current.get(message.id)?.abort()
    const controller = new AbortController()
    noteSaveControllersRef.current.set(message.id, controller)
    const courseEpoch = noteSaveEpochRef.current
    const operation =
      (noteSaveOperationsRef.current[message.id] ?? 0) + 1
    noteSaveOperationsRef.current[message.id] = operation
    setNoteSaveStates((current) => ({
      ...current,
      [message.id]: { status: 'pending' },
    }))

    try {
      const saved = await saveChatAnswerAsNote(
        apiBaseUrl,
        courseId,
        message.id,
        undefined,
        controller.signal,
      )
      if (
        controller.signal.aborted ||
        courseEpoch !== noteSaveEpochRef.current ||
        noteSaveOperationsRef.current[message.id] !== operation
      ) {
        return
      }
      if (
        saved.course_id !== courseId ||
        saved.origin_type !== 'chat_answer' ||
        saved.origin_snapshot.origin_type !== 'chat_answer' ||
        saved.origin_snapshot.message_id !== message.id
      ) {
        throw new Error(
          'The server returned a note outside the active Chat message scope.',
        )
      }
      setNoteSaveStates((current) => ({
        ...current,
        [message.id]: {
          status: 'saved',
          noteId: saved.id,
        },
      }))
    } catch (requestError: unknown) {
      if (
        isAbortError(requestError) ||
        controller.signal.aborted ||
        courseEpoch !== noteSaveEpochRef.current ||
        noteSaveOperationsRef.current[message.id] !== operation
      ) {
        return
      }
      setNoteSaveStates((current) => ({
        ...current,
        [message.id]: {
          status: 'error',
          message:
            requestError instanceof Error
              ? requestError.message
              : 'Could not save this answer to notes.',
        },
      }))
    } finally {
      if (
        noteSaveControllersRef.current.get(message.id) === controller
      ) {
        noteSaveControllersRef.current.delete(message.id)
      }
    }
  }

  if (!courseId) {
    return (
      <section className="chat-panel chat-panel-unavailable">
        <MessageSquareText aria-hidden="true" size={34} />
        <h2>Select a course to start chatting</h2>
        <p>Chat stays scoped to one course and its selected sources.</p>
      </section>
    )
  }

  return (
    <section
      className={compact ? 'chat-panel compact' : 'chat-panel'}
      aria-label="Course chat"
    >
      <aside className="chat-conversation-rail">
        <div className="chat-rail-heading">
          <div className="chat-composer-input">
            <span>Chat history</span>
            <strong>{chat.conversations.length}</strong>
          </div>
          <div>
            <button
              type="button"
              aria-label="Refresh conversations"
              title="Refresh conversations"
              disabled={isBusy || chat.isLoadingWorkspace}
              onClick={chat.refresh}
            >
              <RefreshCw
                aria-hidden="true"
                className={
                  chat.isLoadingWorkspace ? 'chat-icon-spin' : undefined
                }
                size={15}
              />
            </button>
            <button
              type="button"
              aria-label="New conversation"
              title="New conversation"
              disabled={isBusy}
              onClick={() => {
                if (canLeaveCurrentDraft()) {
                  void chat.createConversation()
                }
              }}
            >
              <Plus aria-hidden="true" size={16} />
            </button>
          </div>
        </div>

        <div className="chat-conversation-list">
          {chat.conversations.map((item) => (
            <button
              key={item.id}
              type="button"
              className={
                item.id === chat.activeConversationId ? 'selected' : ''
              }
              disabled={isBusy}
              aria-current={
                item.id === chat.activeConversationId
                  ? 'true'
                  : undefined
              }
              onClick={() => {
                if (
                  item.id === chat.activeConversationId ||
                  canLeaveCurrentDraft()
                ) {
                  chat.selectConversation(item.id)
                }
              }}
            >
              <span>{item.title}</span>
              <small>
                {item.message_count} message
                {item.message_count === 1 ? '' : 's'}
                {' · '}
                {formatConversationTime(item.last_message_at)}
              </small>
            </button>
          ))}
          {!chat.conversations.length && !chat.isLoadingWorkspace && (
            <div className="chat-rail-empty">
              <MessageSquareText aria-hidden="true" size={22} />
              <span>No conversations yet</span>
              <small>Start with one of the suggested questions.</small>
            </div>
          )}
        </div>
      </aside>

      <div className="chat-workspace">
        <header className="chat-header">
          <div>
            <span className="chat-eyebrow">Grounded course chat</span>
            <h2>{courseTitle ?? 'Course notebook'}</h2>
            <p>
              Answers use only the sources selected for this conversation.
            </p>
          </div>
          {model && <span className="chat-model-badge">{model}</span>}
        </header>

        <details
          className="chat-source-picker"
          open={isSourcePickerOpen}
          onToggle={(event) =>
            setIsSourcePickerOpen(event.currentTarget.open)
          }
        >
          <summary>
            <span>
              <BookOpenText aria-hidden="true" size={16} />
              Sources
            </span>
            <span>
              {chat.selectedReadySourceCount} selected
              <ChevronDown aria-hidden="true" size={15} />
            </span>
          </summary>
          <div className="chat-source-picker-body">
            <div className="chat-source-actions">
              <span>
                Choose exactly what this conversation may use as evidence.
              </span>
              <div>
                <button
                  type="button"
                  disabled={isSourceSelectionBusy}
                  onClick={() => void chat.selectAllReadySources()}
                >
                  Select ready
                </button>
                <button
                  type="button"
                  disabled={
                    isSourceSelectionBusy ||
                    !chat.selectedSourceIds.length
                  }
                  onClick={() => void chat.clearSelectedSources()}
                >
                  Clear
                </button>
              </div>
            </div>
            <div className="chat-source-list">
              {chat.sources.map((source) => {
                const isAvailable =
                  source.enabled && source.content_status === 'ready'
                const isSelected = sourceIds.has(source.id)
                return (
                  <label
                    key={source.id}
                    className={[
                      isSelected ? 'selected' : '',
                      isAvailable ? '' : 'unavailable',
                    ]
                      .filter(Boolean)
                      .join(' ')}
                  >
                    <input
                      type="checkbox"
                      checked={isSelected}
                      disabled={
                        (!isAvailable && !isSelected) ||
                        isSourceSelectionBusy
                      }
                      onChange={() => void chat.toggleSource(source.id)}
                    />
                    <span className="chat-source-icon">
                      {isSelected ? (
                        <Check aria-hidden="true" size={14} />
                      ) : (
                        sourceIcon(source)
                      )}
                    </span>
                    <span>
                      <strong>{source.title}</strong>
                      <small>{sourceStatusLabel(source)}</small>
                    </span>
                  </label>
                )
              })}
              {!chat.sources.length && !chat.isLoadingWorkspace && (
                <div className="chat-no-sources">
                  No processed sources are available for this course.
                </div>
              )}
            </div>
          </div>
        </details>

        {chat.error && (
          <div className="chat-error" role="alert">
            <AlertCircle aria-hidden="true" size={17} />
            <span>{chat.error.message}</span>
            <button
              type="button"
              onClick={() => {
                const isSendRetry =
                  chat.error?.retry.kind === 'send' ||
                  chat.error?.retry.kind === 'message-task'
                void chat.retryLastRequest().then((succeeded) => {
                  if (isSendRetry && succeeded) setDraft('')
                })
              }}
            >
              Retry
            </button>
            <button
              type="button"
              aria-label="Dismiss error"
              onClick={chat.clearError}
            >
              <X aria-hidden="true" size={15} />
            </button>
          </div>
        )}

        <div
          className="chat-message-stream"
          role="log"
          aria-label="Conversation messages"
          aria-live="polite"
          aria-relevant="additions text"
          aria-busy={chat.isSending}
        >
          {chat.isLoadingConversation && !messages.length ? (
            <div className="chat-loading-state" role="status">
              <LoaderCircle
                aria-hidden="true"
                className="chat-icon-spin"
                size={20}
              />
              Loading conversation…
            </div>
          ) : messages.length ? (
            messages.map((message) =>
              message.role === 'user' ? (
                <div className="chat-message user" key={message.id}>
                  <div className="chat-message-label">You</div>
                  <p>{message.content}</p>
                </div>
              ) : (
                <AssistantMessage
                  key={message.id}
                  message={message}
                  onStartNewAttempt={(failedMessage) =>
                    void chat.startNewAttemptForMessage(failedMessage)
                  }
                  onOpenCitation={onOpenCitation}
                  statusPollingExhausted={
                    chat.generationPollingExhausted
                  }
                  onRefreshStatus={chat.refreshConversation}
                  newAttemptDisabled={isBusy || hasGeneratingMessage}
                  noteSaveState={
                    noteSaveStates[message.id] ?? { status: 'idle' }
                  }
                  onSaveToNotes={(answer) =>
                    void saveAnswerToNotes(answer)
                  }
                  onOpenNote={onOpenNote}
                />
              ),
            )
          ) : (
            <div className="chat-welcome">
              <Sparkles aria-hidden="true" size={28} />
              <h2>Ask across your course sources</h2>
              <p>
                Every factual sentence must be supported by a source
                excerpt. If the evidence is missing, the assistant will say
                so.
              </p>
              <div className="chat-recommendations">
                {recommendedQuestions.map((question) => (
                  <button
                    key={question}
                    type="button"
                    disabled={isBusy}
                    onClick={() => setDraft(question)}
                  >
                    {question}
                  </button>
                ))}
              </div>
            </div>
          )}

          {chat.pendingQuestion && !hasGeneratingMessage && (
            <>
              <div className="chat-message user pending">
                <div className="chat-message-label">You</div>
                <p>{chat.pendingQuestion}</p>
              </div>
              <div className="chat-message assistant generating">
                <div className="chat-message-label">
                  <Sparkles aria-hidden="true" size={14} />
                  Course assistant
                </div>
                <div className="chat-generating" role="status">
                  <LoaderCircle aria-hidden="true" size={16} />
                  {chat.generationTask?.progress.message ??
                    'Retrieving and validating evidence…'}
                  {chat.generationTask &&
                    ['queued', 'running', 'canceling'].includes(
                      chat.generationTask.status,
                    ) && (
                      <button
                        type="button"
                        disabled={
                          chat.generationTask.status === 'canceling'
                        }
                        onClick={() => void chat.cancelGeneration()}
                      >
                        {chat.generationTask.status === 'canceling'
                          ? 'Canceling'
                          : 'Cancel'}
                      </button>
                    )}
                </div>
              </div>
            </>
          )}
          <div ref={messagesEndRef} />
        </div>

        <footer className="chat-composer">
          <SaveStatus
            state={chatDraft.state}
            message={chatDraft.message}
          />
          {chatDraft.recoveryConflict && (
            <div className="chat-draft-conflict" role="alert">
              <div>
                <strong>Another saved draft is available</strong>
                <span>
                  Choose which version to keep before continuing.
                </span>
              </div>
              <button
                type="button"
                onClick={chatDraft.restoreRecoveryDraft}
              >
                Restore saved draft
              </button>
              <button
                type="button"
                onClick={() => void chatDraft.discardRecoveryDraft()}
              >
                Keep current draft
              </button>
            </div>
          )}
          {chat.selectedReadySourceCount === 0 && (
            <div className="chat-source-warning">
              Select at least one ready source before asking a question.
            </div>
          )}
          <label className="chat-visually-hidden" htmlFor="course-chat-input">
            Ask about this course
          </label>
          <div>
            <textarea
              id="course-chat-input"
              rows={3}
              value={draft}
              maxLength={8000}
              readOnly={
                isBusy || chatDraft.recoveryConflict !== null
              }
              aria-busy={chat.isSending}
              placeholder="Ask a question grounded in your sources…"
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (
                  event.key === 'Enter' &&
                  !event.shiftKey &&
                  !event.nativeEvent.isComposing
                ) {
                  event.preventDefault()
                  if (canSend) void submitMessage()
                }
              }}
            />
            <button
              type="button"
              aria-label="Send question"
              disabled={!canSend}
              onClick={() => void submitMessage()}
            >
              {chat.isSending ? (
                <LoaderCircle
                  aria-hidden="true"
                  className="chat-icon-spin"
                  size={18}
                />
              ) : (
                <Send aria-hidden="true" size={18} />
              )}
            </button>
          </div>
          <small>
            Enter to send · Shift+Enter for a new line ·{' '}
            {chat.selectedReadySourceCount} source
            {chat.selectedReadySourceCount === 1 ? '' : 's'}
          </small>
        </footer>
      </div>
    </section>
  )
}
