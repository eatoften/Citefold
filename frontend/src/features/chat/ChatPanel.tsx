import {
  AlertCircle,
  ArrowUpRight,
  BookOpenText,
  Check,
  ChevronDown,
  FileText,
  LoaderCircle,
  MessageSquareText,
  Plus,
  RefreshCw,
  RotateCcw,
  Send,
  Sparkles,
  Video,
  X,
} from 'lucide-react'
import {
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { formatSourceLocator } from '../citations/citationFormat'
import type { CourseSource } from '../sources/sourceTypes'
import type { ChatCitation, ChatMessage } from './chatTypes'
import { useChat } from './useChat'
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
  onOpenCitation?: (
    citation: ChatCitation,
    trigger: HTMLButtonElement,
  ) => void
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
  onOpenCitation,
}: ChatPanelProps) {
  const chat = useChat({ apiBaseUrl, courseId, model })
  const [draft, setDraft] = useState('')
  const [isSourcePickerOpen, setIsSourcePickerOpen] = useState(!compact)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setDraft('')
    setIsSourcePickerOpen(!compact)
  }, [compact, courseId])

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
    !isBusy
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
    if (!question) return
    setDraft('')
    const sent = await chat.sendMessage(question)
    if (!sent) setDraft(question)
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
          <div>
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
              onClick={() => void chat.createConversation()}
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
              onClick={() => chat.selectConversation(item.id)}
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
            <h1>{courseTitle ?? 'Course notebook'}</h1>
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
                  chat.error?.retry.kind === 'send'
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

        <main
          className="chat-message-stream"
          aria-live="polite"
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
                  Retrieving and validating evidence…
                </div>
              </div>
            </>
          )}
          <div ref={messagesEndRef} />
        </main>

        <footer className="chat-composer">
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
              disabled={isBusy}
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
