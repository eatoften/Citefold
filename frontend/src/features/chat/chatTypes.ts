import type {
  SourceLocator,
  SourceType,
} from '../sources/sourceTypes'

export type ChatConversationStatus = 'active' | 'archived'

export type ChatConversation = {
  id: string
  course_id: string
  title: string
  status: ChatConversationStatus
  selected_source_ids: string[]
  message_count: number
  last_message_at: string | null
  created_at: string
  updated_at: string
}

export type ChatCitation = {
  id: string
  message_id: string
  ordinal: number
  sentence_index: number
  start_offset: number
  end_offset: number
  source_id: string
  chunk_id: string
  chunk_text_hash: string
  source_title: string
  source_type: SourceType
  quote: string
  score: number
  locator: SourceLocator
  created_at: string
}

export type ChatMessageStatus = 'generating' | 'complete' | 'failed'
export type ChatAnswerStatus = 'answered' | 'abstained'

export type ChatMessage = {
  id: string
  conversation_id: string
  turn_id: string
  sequence: number
  role: 'user' | 'assistant'
  content: string
  status: ChatMessageStatus
  answer_status: ChatAnswerStatus | null
  reply_to_message_id: string | null
  error_message: string | null
  provider: string | null
  model: string | null
  metadata: Record<string, unknown>
  citations: ChatCitation[]
  created_at: string
  updated_at: string
}

export type ChatConversationDetail = ChatConversation & {
  messages: ChatMessage[]
}

export type ChatConversationCreate = {
  title?: string
  source_ids?: string[]
}

export type ChatConversationUpdate = {
  title?: string
  source_ids?: string[]
}

export type ChatMessageCreate = {
  content: string
  client_request_id: string
  source_ids?: string[]
  model?: string
}

export type ChatTurnStatus =
  | 'pending'
  | 'retrieving'
  | 'generating'
  | 'validating'
  | 'completed'
  | 'refused'
  | 'failed'

export type ChatTurnResponse = {
  turn_id: string
  client_request_id: string
  status: ChatTurnStatus
  source_ids: string[]
  replayed: boolean
  conversation: ChatConversation
  user_message: ChatMessage
  assistant_message: ChatMessage
}
