import type { CourseSource } from '../sources/sourceTypes'
import type {
  ChatConversation,
  ChatConversationCreate,
  ChatConversationDetail,
  ChatConversationUpdate,
  ChatMessageCreate,
  ChatTurnResponse,
} from './chatTypes'

export class ChatApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ChatApiError'
    this.status = status
  }
}

async function requestJson<T>(
  apiBaseUrl: string,
  path: string,
  options: {
    method?: 'GET' | 'POST' | 'PATCH'
    body?: unknown
    signal?: AbortSignal
  } = {},
): Promise<T> {
  const response = await fetch(
    `${apiBaseUrl.replace(/\/$/, '')}${path}`,
    {
      method: options.method ?? 'GET',
      headers:
        options.body === undefined
          ? { Accept: 'application/json' }
          : {
              Accept: 'application/json',
              'Content-Type': 'application/json',
            },
      body:
        options.body === undefined
          ? undefined
          : JSON.stringify(options.body),
      signal: options.signal,
    },
  )

  if (!response.ok) {
    let message = `Request failed with HTTP ${response.status}.`
    try {
      const payload: unknown = await response.json()
      if (
        typeof payload === 'object' &&
        payload !== null &&
        'detail' in payload &&
        typeof payload.detail === 'string'
      ) {
        message = payload.detail
      }
    } catch {
      // Keep the status-based fallback for non-JSON failures.
    }
    throw new ChatApiError(message, response.status)
  }

  return response.json() as Promise<T>
}

function pathId(value: string): string {
  return encodeURIComponent(value)
}

export function listCourseSources(
  apiBaseUrl: string,
  courseId: string,
  signal?: AbortSignal,
): Promise<CourseSource[]> {
  return requestJson(
    apiBaseUrl,
    `/courses/${pathId(courseId)}/sources`,
    { signal },
  )
}

export function listChatConversations(
  apiBaseUrl: string,
  courseId: string,
  signal?: AbortSignal,
): Promise<ChatConversation[]> {
  return requestJson(
    apiBaseUrl,
    `/courses/${pathId(courseId)}/chat/conversations`,
    { signal },
  )
}

export function createChatConversation(
  apiBaseUrl: string,
  courseId: string,
  request: ChatConversationCreate,
  signal?: AbortSignal,
): Promise<ChatConversation> {
  return requestJson(
    apiBaseUrl,
    `/courses/${pathId(courseId)}/chat/conversations`,
    {
      method: 'POST',
      body: request,
      signal,
    },
  )
}

export function getChatConversation(
  apiBaseUrl: string,
  conversationId: string,
  signal?: AbortSignal,
): Promise<ChatConversationDetail> {
  return requestJson(
    apiBaseUrl,
    `/chat/conversations/${pathId(conversationId)}`,
    { signal },
  )
}

export function updateChatConversation(
  apiBaseUrl: string,
  conversationId: string,
  request: ChatConversationUpdate,
  signal?: AbortSignal,
): Promise<ChatConversation> {
  return requestJson(
    apiBaseUrl,
    `/chat/conversations/${pathId(conversationId)}`,
    {
      method: 'PATCH',
      body: request,
      signal,
    },
  )
}

export function sendChatMessage(
  apiBaseUrl: string,
  conversationId: string,
  request: ChatMessageCreate,
  signal?: AbortSignal,
): Promise<ChatTurnResponse> {
  return requestJson(
    apiBaseUrl,
    `/chat/conversations/${pathId(conversationId)}/messages`,
    {
      method: 'POST',
      body: request,
      signal,
    },
  )
}

export function isAbortError(error: unknown): boolean {
  return (
    error instanceof DOMException &&
    error.name === 'AbortError'
  )
}
