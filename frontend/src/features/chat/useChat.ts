import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import type { CourseSource } from '../sources/sourceTypes'
import {
  cancelReliableTask,
  enqueueChatGeneration,
  retryReliableTask,
  waitForReliableTask,
  type ReliableTask,
} from '../reliability'
import {
  createChatConversation as createConversationRequest,
  getChatConversation,
  isAbortError,
  listChatConversations,
  listCourseSources,
  updateChatConversation,
} from './chatApi'
import type {
  ChatConversation,
  ChatConversationDetail,
  ChatMessage,
  ChatTurnResponse,
} from './chatTypes'

type RetryIntent =
  | { kind: 'workspace' }
  | { kind: 'conversation' }
  | { kind: 'create' }
  | { kind: 'sources'; sourceIds: string[] }
  | {
      kind: 'send'
      content: string
      clientRequestId: string
      sourceIds: string[]
      model?: string
    }
  | {
      kind: 'message-task'
      taskId: string
      conversationId: string
      content: string
    }

export type ChatPanelError = {
  message: string
  retry: RetryIntent
}

type UseChatOptions = {
  apiBaseUrl: string
  courseId: string | null
  model?: string | null
  initialConversationId?: string | null
  onConversationChange?: (
    conversationId: string | null,
    mode: 'push' | 'replace',
  ) => void
}

export type UseChatResult = {
  conversations: ChatConversation[]
  conversation: ChatConversationDetail | null
  activeConversationId: string | null
  sources: CourseSource[]
  selectedSourceIds: string[]
  selectedReadySourceCount: number
  pendingQuestion: string | null
  error: ChatPanelError | null
  isLoadingWorkspace: boolean
  isLoadingConversation: boolean
  isCreatingConversation: boolean
  isUpdatingSources: boolean
  isSending: boolean
  generationTask: ReliableTask<ChatGenerationTaskResult> | null
  generationPollingExhausted: boolean
  selectConversation: (conversationId: string) => void
  createConversation: () => Promise<ChatConversation | null>
  toggleSource: (sourceId: string) => Promise<void>
  selectAllReadySources: () => Promise<void>
  clearSelectedSources: () => Promise<void>
  sendMessage: (content: string) => Promise<boolean>
  startNewAttemptForMessage: (message: ChatMessage) => Promise<boolean>
  retryLastRequest: () => Promise<boolean>
  cancelGeneration: () => Promise<void>
  clearError: () => void
  refresh: () => void
  refreshConversation: () => void
}

type SendAttemptSnapshot = {
  clientRequestId: string
  sourceIds: string[]
  model?: string
}

type ChatGenerationTaskResult = {
  turn: ChatTurnResponse
}

const GENERATION_POLL_INTERVAL_MS = 1500
const GENERATION_POLL_MAX_ATTEMPTS = 40
const GENERATION_POLL_MAX_DURATION_MS = 60_000
const GENERATION_POLL_REQUEST_TIMEOUT_MS = 5_000

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}

function createClientRequestId(): string {
  if (typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `chat-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function upsertConversation(
  conversations: ChatConversation[],
  conversation: ChatConversation,
): ChatConversation[] {
  return [
    conversation,
    ...conversations.filter((item) => item.id !== conversation.id),
  ].sort((left, right) => {
    const leftTime = left.last_message_at ?? left.updated_at
    const rightTime = right.last_message_at ?? right.updated_at
    return rightTime.localeCompare(leftTime)
  })
}

function mergeTurn(
  current: ChatConversationDetail | null,
  turn: ChatTurnResponse,
): ChatConversationDetail {
  const existing =
    current?.id === turn.conversation.id ? current.messages : []
  const incoming = [turn.user_message, turn.assistant_message]
  const incomingIds = new Set(incoming.map((message) => message.id))
  const messages = [
    ...existing.filter((message) => !incomingIds.has(message.id)),
    ...incoming,
  ].sort((left, right) => left.sequence - right.sequence)
  return {
    ...turn.conversation,
    messages,
  }
}

export function useChat({
  apiBaseUrl,
  courseId,
  model,
  initialConversationId = null,
  onConversationChange,
}: UseChatOptions): UseChatResult {
  const [conversations, setConversations] = useState<ChatConversation[]>([])
  const [conversation, setConversation] =
    useState<ChatConversationDetail | null>(null)
  const [activeConversationId, setActiveConversationId] =
    useState<string | null>(null)
  const [sources, setSources] = useState<CourseSource[]>([])
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([])
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null)
  const [error, setError] = useState<ChatPanelError | null>(null)
  const [isLoadingWorkspace, setIsLoadingWorkspace] = useState(false)
  const [isLoadingConversation, setIsLoadingConversation] = useState(false)
  const [isCreatingConversation, setIsCreatingConversation] = useState(false)
  const [isUpdatingSources, setIsUpdatingSources] = useState(false)
  const [isSending, setIsSending] = useState(false)
  const [generationTask, setGenerationTask] =
    useState<ReliableTask<ChatGenerationTaskResult> | null>(null)
  const [
    generationPollingExhausted,
    setGenerationPollingExhausted,
  ] = useState(false)
  const [workspaceCourseId, setWorkspaceCourseId] =
    useState<string | null>(null)
  const [workspaceReloadKey, setWorkspaceReloadKey] = useState(0)
  const [conversationReloadKey, setConversationReloadKey] = useState(0)

  const epochRef = useRef(0)
  const controllersRef = useRef(new Set<AbortController>())
  const detailControllerRef = useRef<AbortController | null>(null)
  const activeConversationIdRef = useRef<string | null>(null)
  const conversationRef = useRef<ChatConversationDetail | null>(null)
  const generationTaskIdRef = useRef<string | null>(null)
  const initialConversationIdRef = useRef(initialConversationId)
  const previousRouteConversationIdRef =
    useRef(initialConversationId)
  const onConversationChangeRef = useRef(onConversationChange)

  const commitGenerationTask = useCallback(
    (
      task: ReliableTask<ChatGenerationTaskResult> | null,
    ): void => {
      generationTaskIdRef.current = task?.id ?? null
      setGenerationTask(task)
    },
    [],
  )

  useEffect(() => {
    initialConversationIdRef.current = initialConversationId
  }, [initialConversationId])

  useEffect(() => {
    onConversationChangeRef.current = onConversationChange
  }, [onConversationChange])

  const activateConversation = useCallback(
    (
      conversationId: string | null,
      mode: 'push' | 'replace',
      notify = true,
    ): void => {
      setActiveConversationId(conversationId)
      activeConversationIdRef.current = conversationId
      if (notify) {
        onConversationChangeRef.current?.(conversationId, mode)
      }
    },
    [],
  )

  useEffect(() => {
    activeConversationIdRef.current = activeConversationId
  }, [activeConversationId])

  useEffect(() => {
    conversationRef.current = conversation
  }, [conversation])

  const generatingMessageKey = useMemo(
    () =>
      conversation?.messages
        .filter(
          (message) =>
            message.role === 'assistant' &&
            message.status === 'generating',
        )
        .map((message) => message.id)
        .sort()
        .join('|') ?? '',
    [conversation],
  )

  const startRequest = useCallback((): AbortController => {
    const controller = new AbortController()
    controllersRef.current.add(controller)
    return controller
  }, [])

  const finishRequest = useCallback((controller: AbortController): void => {
    controllersRef.current.delete(controller)
  }, [])

  const isCurrentEpoch = useCallback((epoch: number): boolean => {
    return epochRef.current === epoch
  }, [])

  const abortDetailRequest = useCallback((): void => {
    detailControllerRef.current?.abort()
    detailControllerRef.current = null
  }, [])

  useEffect(() => {
    epochRef.current += 1
    const epoch = epochRef.current
    const activeControllers = controllersRef.current
    for (const controller of activeControllers) {
      controller.abort()
    }
    activeControllers.clear()
    detailControllerRef.current = null

    setConversations([])
    setConversation(null)
    conversationRef.current = null
    setActiveConversationId(null)
    activeConversationIdRef.current = null
    setSources([])
    setSelectedSourceIds([])
    setWorkspaceCourseId(null)
    setPendingQuestion(null)
    setError(null)
    setIsLoadingConversation(false)
    setIsCreatingConversation(false)
    setIsUpdatingSources(false)
    setIsSending(false)
    commitGenerationTask(null)
    setGenerationPollingExhausted(false)

    if (!courseId) {
      setIsLoadingWorkspace(false)
      return
    }

    const controller = startRequest()
    setIsLoadingWorkspace(true)
    void Promise.all([
      listChatConversations(apiBaseUrl, courseId, controller.signal),
      listCourseSources(apiBaseUrl, courseId, controller.signal),
    ])
      .then(([nextConversations, nextSources]) => {
        if (!isCurrentEpoch(epoch)) return
        setConversations(nextConversations)
        setSources(nextSources)
        setWorkspaceCourseId(courseId)
        const requestedConversation = nextConversations.find(
          (item) => item.id === initialConversationIdRef.current,
        )
        const firstConversation =
          requestedConversation ?? nextConversations[0] ?? null
        const defaultSourceIds = nextSources
          .filter(
            (source) =>
              source.enabled && source.content_status === 'ready',
          )
          .map((source) => source.id)
        activateConversation(
          firstConversation?.id ?? null,
          'replace',
          firstConversation?.id !== initialConversationIdRef.current,
        )
        setSelectedSourceIds(
          firstConversation?.selected_source_ids ?? defaultSourceIds,
        )
      })
      .catch((requestError: unknown) => {
        if (
          isAbortError(requestError) ||
          !isCurrentEpoch(epoch)
        ) {
          return
        }
        setError({
          message: errorMessage(
            requestError,
            'Could not load course chat.',
          ),
          retry: { kind: 'workspace' },
        })
      })
      .finally(() => {
        finishRequest(controller)
        if (isCurrentEpoch(epoch)) {
          setIsLoadingWorkspace(false)
        }
      })

    return () => {
      for (const activeController of activeControllers) {
        activeController.abort()
      }
      activeControllers.clear()
    }
  }, [
    apiBaseUrl,
    activateConversation,
    commitGenerationTask,
    courseId,
    finishRequest,
    isCurrentEpoch,
    startRequest,
    workspaceReloadKey,
  ])

  useEffect(() => {
    detailControllerRef.current?.abort()
    detailControllerRef.current = null

    if (
      !courseId ||
      workspaceCourseId !== courseId ||
      !activeConversationId
    ) {
      setConversation(null)
      conversationRef.current = null
      setIsLoadingConversation(false)
      return
    }

    const epoch = epochRef.current
    const expectedConversationId = activeConversationId
    const controller = startRequest()
    detailControllerRef.current = controller
    setIsLoadingConversation(true)

    void getChatConversation(
      apiBaseUrl,
      expectedConversationId,
      controller.signal,
    )
      .then((detail) => {
        if (
          !isCurrentEpoch(epoch) ||
          activeConversationIdRef.current !== expectedConversationId
        ) {
          return
        }
        if (detail.course_id !== courseId) {
          setError({
            message: 'This conversation belongs to another course.',
            retry: { kind: 'workspace' },
          })
          return
        }
        setConversation(detail)
        conversationRef.current = detail
        setSelectedSourceIds(detail.selected_source_ids)
        setConversations((current) =>
          upsertConversation(current, detail),
        )
      })
      .catch((requestError: unknown) => {
        if (
          isAbortError(requestError) ||
          !isCurrentEpoch(epoch) ||
          activeConversationIdRef.current !== expectedConversationId
        ) {
          return
        }
        setError({
          message: errorMessage(
            requestError,
            'Could not load this conversation.',
          ),
          retry: { kind: 'conversation' },
        })
      })
      .finally(() => {
        finishRequest(controller)
        if (detailControllerRef.current === controller) {
          detailControllerRef.current = null
        }
        if (
          isCurrentEpoch(epoch) &&
          activeConversationIdRef.current === expectedConversationId
        ) {
          setIsLoadingConversation(false)
        }
      })

    return () => {
      controller.abort()
    }
  }, [
    activeConversationId,
    apiBaseUrl,
    conversationReloadKey,
    courseId,
    finishRequest,
    isCurrentEpoch,
    startRequest,
    workspaceCourseId,
  ])

  useEffect(() => {
    setGenerationPollingExhausted(false)
    if (
      !courseId ||
      workspaceCourseId !== courseId ||
      !activeConversationId ||
      !generatingMessageKey
    ) {
      return
    }

    const epoch = epochRef.current
    const expectedConversationId = activeConversationId
    const startedAt = Date.now()
    let attempts = 0
    let cancelled = false
    let timerId: number | null = null
    let pollController: AbortController | null = null

    const scheduleNextPoll = (): void => {
      timerId = window.setTimeout(() => {
        void pollConversation()
      }, GENERATION_POLL_INTERVAL_MS)
    }

    const pollConversation = async (): Promise<void> => {
      if (
        cancelled ||
        !isCurrentEpoch(epoch) ||
        activeConversationIdRef.current !== expectedConversationId
      ) {
        return
      }

      attempts += 1
      const controller = startRequest()
      let requestTimedOut = false
      const requestTimeoutId = window.setTimeout(() => {
        requestTimedOut = true
        controller.abort()
      }, GENERATION_POLL_REQUEST_TIMEOUT_MS)
      pollController = controller
      try {
        const detail = await getChatConversation(
          apiBaseUrl,
          expectedConversationId,
          controller.signal,
        )
        if (
          cancelled ||
          !isCurrentEpoch(epoch) ||
          activeConversationIdRef.current !== expectedConversationId ||
          detail.course_id !== courseId
        ) {
          return
        }

        setConversation(detail)
        conversationRef.current = detail
        setSelectedSourceIds(detail.selected_source_ids)
        setConversations((current) =>
          upsertConversation(current, detail),
        )

        const isStillGenerating = detail.messages.some(
          (message) =>
            message.role === 'assistant' &&
            message.status === 'generating',
        )
        if (!isStillGenerating) {
          setGenerationPollingExhausted(false)
          return
        }
      } catch (requestError: unknown) {
        if (
          cancelled ||
          !isCurrentEpoch(epoch) ||
          activeConversationIdRef.current !== expectedConversationId
        ) {
          return
        }
        if (isAbortError(requestError) && !requestTimedOut) {
          return
        }
      } finally {
        window.clearTimeout(requestTimeoutId)
        finishRequest(controller)
        if (pollController === controller) {
          pollController = null
        }
      }

      if (
        attempts >= GENERATION_POLL_MAX_ATTEMPTS ||
        Date.now() - startedAt >= GENERATION_POLL_MAX_DURATION_MS
      ) {
        if (
          !cancelled &&
          isCurrentEpoch(epoch) &&
          activeConversationIdRef.current === expectedConversationId
        ) {
          setGenerationPollingExhausted(true)
        }
        return
      }
      scheduleNextPoll()
    }

    scheduleNextPoll()
    return () => {
      cancelled = true
      if (timerId !== null) {
        window.clearTimeout(timerId)
      }
      pollController?.abort()
    }
  }, [
    activeConversationId,
    apiBaseUrl,
    conversationReloadKey,
    courseId,
    finishRequest,
    generatingMessageKey,
    isCurrentEpoch,
    startRequest,
    workspaceCourseId,
  ])

  const selectConversation = useCallback(
    (conversationId: string): void => {
      if (
        workspaceCourseId !== courseId ||
        isSending ||
        isUpdatingSources
      ) {
        return
      }
      const nextConversation = conversations.find(
        (item) => item.id === conversationId,
      )
      if (!nextConversation) return
      setError(null)
      setConversation(null)
      conversationRef.current = null
      activateConversation(conversationId, 'push')
      setSelectedSourceIds(nextConversation.selected_source_ids)
    },
    [
      conversations,
      activateConversation,
      courseId,
      isSending,
      isUpdatingSources,
      workspaceCourseId,
    ],
  )

  useEffect(() => {
    const previousConversationId =
      previousRouteConversationIdRef.current
    previousRouteConversationIdRef.current =
      initialConversationId

    if (
      workspaceCourseId !== courseId
    ) {
      return
    }
    if (!initialConversationId) {
      if (
        previousConversationId &&
        activeConversationId
      ) {
        setError(null)
        setConversation(null)
        conversationRef.current = null
        activateConversation(null, 'replace', false)
        setSelectedSourceIds(
          sources
            .filter(
              (source) =>
                source.enabled &&
                source.content_status === 'ready',
            )
            .map((source) => source.id),
        )
      }
      return
    }
    if (initialConversationId === activeConversationId) {
      return
    }
    const nextConversation = conversations.find(
      (item) => item.id === initialConversationId,
    )
    if (!nextConversation) return

    setError(null)
    setConversation(null)
    conversationRef.current = null
    activateConversation(initialConversationId, 'replace', false)
    setSelectedSourceIds(nextConversation.selected_source_ids)
  }, [
    activeConversationId,
    activateConversation,
    conversations,
    courseId,
    initialConversationId,
    sources,
    workspaceCourseId,
  ])

  const createConversation = useCallback(async (): Promise<
    ChatConversation | null
  > => {
    if (
      !courseId ||
      workspaceCourseId !== courseId ||
      isCreatingConversation ||
      isSending
    ) {
      return null
    }
    const epoch = epochRef.current
    const controller = startRequest()
    setError(null)
    setIsCreatingConversation(true)
    try {
      const created = await createConversationRequest(
        apiBaseUrl,
        courseId,
        { source_ids: selectedSourceIds },
        controller.signal,
      )
      if (!isCurrentEpoch(epoch)) return null
      setConversations((current) =>
        upsertConversation(current, created),
      )
      activateConversation(created.id, 'push')
      setConversation({ ...created, messages: [] })
      conversationRef.current = { ...created, messages: [] }
      setSelectedSourceIds(created.selected_source_ids)
      return created
    } catch (requestError: unknown) {
      if (isAbortError(requestError) || !isCurrentEpoch(epoch)) {
        return null
      }
      setError({
        message: errorMessage(
          requestError,
          'Could not create a conversation.',
        ),
        retry: { kind: 'create' },
      })
      return null
    } finally {
      finishRequest(controller)
      if (isCurrentEpoch(epoch)) {
        setIsCreatingConversation(false)
      }
    }
  }, [
    apiBaseUrl,
    activateConversation,
    courseId,
    finishRequest,
    isCreatingConversation,
    isCurrentEpoch,
    isSending,
    selectedSourceIds,
    startRequest,
    workspaceCourseId,
  ])

  const saveSourceSelection = useCallback(
    async (nextSourceIds: string[]): Promise<void> => {
      if (
        workspaceCourseId !== courseId ||
        isSending ||
        isUpdatingSources
      ) {
        return
      }
      const previousSourceIds = selectedSourceIds
      const conversationId = activeConversationIdRef.current
      setSelectedSourceIds(nextSourceIds)
      setError(null)
      if (!conversationId) return

      const epoch = epochRef.current
      const controller = startRequest()
      setIsUpdatingSources(true)
      try {
        const updated = await updateChatConversation(
          apiBaseUrl,
          conversationId,
          { source_ids: nextSourceIds },
          controller.signal,
        )
        if (
          !isCurrentEpoch(epoch) ||
          activeConversationIdRef.current !== conversationId
        ) {
          return
        }
        setSelectedSourceIds(updated.selected_source_ids)
        setConversations((current) =>
          upsertConversation(current, updated),
        )
        setConversation((current) => {
          if (!current || current.id !== updated.id) return current
          const next = {
            ...current,
            ...updated,
            messages: current.messages,
          }
          conversationRef.current = next
          return next
        })
      } catch (requestError: unknown) {
        if (
          isAbortError(requestError) ||
          !isCurrentEpoch(epoch) ||
          activeConversationIdRef.current !== conversationId
        ) {
          return
        }
        setSelectedSourceIds(previousSourceIds)
        setError({
          message: errorMessage(
            requestError,
            'Could not update selected sources.',
          ),
          retry: {
            kind: 'sources',
            sourceIds: nextSourceIds,
          },
        })
      } finally {
        finishRequest(controller)
        if (
          isCurrentEpoch(epoch) &&
          activeConversationIdRef.current === conversationId
        ) {
          setIsUpdatingSources(false)
        }
      }
    },
    [
      apiBaseUrl,
      courseId,
      finishRequest,
      isCurrentEpoch,
      isSending,
      isUpdatingSources,
      selectedSourceIds,
      startRequest,
      workspaceCourseId,
    ],
  )

  const toggleSource = useCallback(
    async (sourceId: string): Promise<void> => {
      const next = selectedSourceIds.includes(sourceId)
        ? selectedSourceIds.filter((id) => id !== sourceId)
        : [...selectedSourceIds, sourceId]
      await saveSourceSelection(next)
    },
    [saveSourceSelection, selectedSourceIds],
  )

  const selectAllReadySources = useCallback(async (): Promise<void> => {
    await saveSourceSelection(
      sources
        .filter(
          (source) =>
            source.enabled && source.content_status === 'ready',
        )
        .map((source) => source.id),
    )
  }, [saveSourceSelection, sources])

  const clearSelectedSources = useCallback(async (): Promise<void> => {
    await saveSourceSelection([])
  }, [saveSourceSelection])

  const waitForMessageTask = useCallback(
    async (
      taskId: string,
      conversationId: string,
      controller: AbortController,
      epoch: number,
    ): Promise<boolean> => {
      const completed =
        await waitForReliableTask<ChatGenerationTaskResult>(
          apiBaseUrl,
          taskId,
          {
            signal: controller.signal,
            onProgress: (task) => {
              if (
                isCurrentEpoch(epoch) &&
                activeConversationIdRef.current === conversationId &&
                task.id === taskId
              ) {
                commitGenerationTask(task)
              }
            },
          },
        )
      if (
        !isCurrentEpoch(epoch) ||
        activeConversationIdRef.current !== conversationId
      ) {
        return false
      }
      const turn = completed.result?.turn
      if (
        !turn ||
        turn.conversation.id !== conversationId ||
        turn.conversation.course_id !== courseId
      ) {
        throw new Error(
          'The answer task completed without a valid conversation result.',
        )
      }
      abortDetailRequest()
      const nextDetail = mergeTurn(conversationRef.current, turn)
      setConversation(nextDetail)
      conversationRef.current = nextDetail
      setConversations((current) =>
        upsertConversation(current, turn.conversation),
      )
      setSelectedSourceIds(turn.conversation.selected_source_ids)
      if (generationTaskIdRef.current === taskId) {
        commitGenerationTask(null)
      }
      return true
    },
    [
      apiBaseUrl,
      abortDetailRequest,
      commitGenerationTask,
      courseId,
      isCurrentEpoch,
    ],
  )

  const sendMessage = useCallback(
    async (
      rawContent: string,
      retryAttempt?: SendAttemptSnapshot,
    ): Promise<boolean> => {
      const content = rawContent.trim()
      if (
        !courseId ||
        workspaceCourseId !== courseId ||
        !content ||
        isCreatingConversation ||
        isSending ||
        isUpdatingSources
      ) {
        return false
      }
      const readySourceIds = new Set(
        sources
          .filter(
            (source) =>
              source.course_id === courseId &&
              source.enabled &&
              source.content_status === 'ready',
          )
          .map((source) => source.id),
      )
      const sourceSnapshot = retryAttempt
        ? [...retryAttempt.sourceIds]
        : selectedSourceIds.filter((sourceId) =>
            readySourceIds.has(sourceId),
          )
      if (!sourceSnapshot.length) {
        setError({
          message:
            'Select at least one enabled, ready source before sending.',
          retry: { kind: 'workspace' },
        })
        return false
      }

      const epoch = epochRef.current
      const controller = startRequest()
      const clientRequestId =
        retryAttempt?.clientRequestId ?? createClientRequestId()
      const requestModel = retryAttempt
        ? retryAttempt.model
        : model?.trim() || undefined
      abortDetailRequest()
      setError(null)
      setIsSending(true)
      setPendingQuestion(content)
      commitGenerationTask(null)

      let conversationId = activeConversationIdRef.current
      let didStartMessageRequest = false
      let taskId: string | null = null
      try {
        if (!conversationId) {
          setIsCreatingConversation(true)
          const created = await createConversationRequest(
            apiBaseUrl,
            courseId,
            { source_ids: sourceSnapshot },
            controller.signal,
          )
          if (!isCurrentEpoch(epoch)) return false
          conversationId = created.id
          setConversations((current) =>
            upsertConversation(current, created),
          )
          activateConversation(created.id, 'push')
          const detail = { ...created, messages: [] }
          setConversation(detail)
          conversationRef.current = detail
          setSelectedSourceIds(created.selected_source_ids)
        }

        didStartMessageRequest = true
        const task = await enqueueChatGeneration(
          apiBaseUrl,
          conversationId,
          {
            content,
            client_request_id: clientRequestId,
            source_ids: sourceSnapshot,
            model: requestModel,
          },
          controller.signal,
        )
        taskId = task.id
        if (
          isCurrentEpoch(epoch) &&
          activeConversationIdRef.current === conversationId
        ) {
          commitGenerationTask(
            task as ReliableTask<ChatGenerationTaskResult>,
          )
        }
        return await waitForMessageTask(
          task.id,
          conversationId,
          controller,
          epoch,
        )
      } catch (requestError: unknown) {
        if (isAbortError(requestError) || !isCurrentEpoch(epoch)) {
          return false
        }
        const retry: RetryIntent = !didStartMessageRequest
          ? { kind: 'create' }
          : taskId
            ? {
                kind: 'message-task',
                taskId,
                conversationId: conversationId!,
                content,
              }
            : {
                kind: 'send',
                content,
                clientRequestId,
                sourceIds: sourceSnapshot,
                model: requestModel,
              }
        setError({
          message: errorMessage(
            requestError,
            'Could not generate a grounded answer.',
          ),
          retry,
        })
        if (conversationId) {
          setConversationReloadKey((value) => value + 1)
        }
        return false
      } finally {
        finishRequest(controller)
        if (isCurrentEpoch(epoch)) {
          setPendingQuestion(null)
          setIsSending(false)
          setIsCreatingConversation(false)
        }
      }
    },
    [
      apiBaseUrl,
      activateConversation,
      abortDetailRequest,
      commitGenerationTask,
      courseId,
      finishRequest,
      isCreatingConversation,
      isCurrentEpoch,
      isSending,
      isUpdatingSources,
      model,
      selectedSourceIds,
      sources,
      startRequest,
      waitForMessageTask,
      workspaceCourseId,
    ],
  )

  const startNewAttemptForMessage = useCallback(
    async (message: ChatMessage): Promise<boolean> => {
      const current = conversationRef.current
      if (!current || message.role !== 'assistant') return false
      const userMessage = current.messages.find(
        (candidate) =>
          candidate.role === 'user' &&
          (candidate.id === message.reply_to_message_id ||
            candidate.turn_id === message.turn_id),
      )
      return userMessage
        ? sendMessage(userMessage.content)
        : false
    },
    [sendMessage],
  )

  const retryLastRequest = useCallback(async (): Promise<boolean> => {
    if (!error) return false
    const retry = error.retry
    setError(null)
    switch (retry.kind) {
      case 'workspace':
        setWorkspaceReloadKey((value) => value + 1)
        return true
      case 'conversation':
        setConversationReloadKey((value) => value + 1)
        return true
      case 'create':
        return (await createConversation()) !== null
      case 'sources':
        await saveSourceSelection(retry.sourceIds)
        return true
      case 'send':
        return sendMessage(retry.content, {
          clientRequestId: retry.clientRequestId,
          sourceIds: retry.sourceIds,
          model: retry.model,
        })
      case 'message-task': {
        if (
          activeConversationIdRef.current !== retry.conversationId
        ) {
          setConversationReloadKey((value) => value + 1)
          return false
        }
        const epoch = epochRef.current
        const controller = startRequest()
        setIsSending(true)
        setPendingQuestion(retry.content)
        try {
          const retried = await retryReliableTask(
            apiBaseUrl,
            retry.taskId,
          )
          if (
            isCurrentEpoch(epoch) &&
            activeConversationIdRef.current === retry.conversationId
          ) {
            commitGenerationTask(
              retried as ReliableTask<ChatGenerationTaskResult>,
            )
          }
          return await waitForMessageTask(
            retry.taskId,
            retry.conversationId,
            controller,
            epoch,
          )
        } catch (requestError: unknown) {
          if (isAbortError(requestError) || !isCurrentEpoch(epoch)) {
            return false
          }
          setError({
            message: errorMessage(
              requestError,
              'Could not retry the grounded answer.',
            ),
            retry,
          })
          setConversationReloadKey((value) => value + 1)
          return false
        } finally {
          finishRequest(controller)
          if (isCurrentEpoch(epoch)) {
            setPendingQuestion(null)
            setIsSending(false)
          }
        }
      }
    }
  }, [
    apiBaseUrl,
    commitGenerationTask,
    createConversation,
    error,
    finishRequest,
    isCurrentEpoch,
    saveSourceSelection,
    sendMessage,
    startRequest,
    waitForMessageTask,
  ])

  const cancelGeneration = useCallback(async (): Promise<void> => {
    const task = generationTask
    const epoch = epochRef.current
    const conversationId = activeConversationIdRef.current
    if (
      !task ||
      !conversationId ||
      !['queued', 'running', 'canceling'].includes(task.status)
    ) {
      return
    }
    try {
      const canceled = await cancelReliableTask(apiBaseUrl, task.id)
      if (
        !isCurrentEpoch(epoch) ||
        activeConversationIdRef.current !== conversationId ||
        generationTaskIdRef.current !== task.id ||
        canceled.id !== task.id
      ) {
        return
      }
      commitGenerationTask(
        canceled as ReliableTask<ChatGenerationTaskResult>,
      )
    } catch (requestError: unknown) {
      if (
        !isCurrentEpoch(epoch) ||
        activeConversationIdRef.current !== conversationId ||
        generationTaskIdRef.current !== task.id
      ) {
        return
      }
      setError({
        message: errorMessage(
          requestError,
          'Could not cancel this answer.',
        ),
        retry: { kind: 'conversation' },
      })
    }
  }, [
    apiBaseUrl,
    commitGenerationTask,
    generationTask,
    isCurrentEpoch,
  ])

  const clearError = useCallback((): void => {
    setError(null)
  }, [])

  const refresh = useCallback((): void => {
    setWorkspaceReloadKey((value) => value + 1)
  }, [])

  const refreshConversation = useCallback((): void => {
    setGenerationPollingExhausted(false)
    setConversationReloadKey((value) => value + 1)
  }, [])

  const selectedReadySourceCount = useMemo(() => {
    const readyIds = new Set(
      sources
        .filter(
          (source) =>
            source.enabled && source.content_status === 'ready',
        )
        .map((source) => source.id),
    )
    return selectedSourceIds.filter((sourceId) =>
      readyIds.has(sourceId),
    ).length
  }, [selectedSourceIds, sources])

  return {
    conversations,
    conversation,
    activeConversationId,
    sources,
    selectedSourceIds,
    selectedReadySourceCount,
    pendingQuestion,
    error,
    isLoadingWorkspace:
      isLoadingWorkspace ||
      (courseId !== null && workspaceCourseId !== courseId),
    isLoadingConversation,
    isCreatingConversation,
    isUpdatingSources,
    isSending,
    generationTask,
    generationPollingExhausted,
    selectConversation,
    createConversation,
    toggleSource,
    selectAllReadySources,
    clearSelectedSources,
    sendMessage,
    startNewAttemptForMessage,
    retryLastRequest,
    cancelGeneration,
    clearError,
    refresh,
    refreshConversation,
  }
}
