import type { ChatCitation } from '../chat'
import type { CourseSource } from '../sources/sourceTypes'

export type NotebookNoteOriginType = 'free' | 'chat_answer'

export type NotebookNoteCitationSpan = {
  sentence_index: number
  start_offset: number
  end_offset: number
}

export type NotebookNoteCitation = Omit<
  ChatCitation,
  | 'message_id'
  | 'sentence_index'
  | 'start_offset'
  | 'end_offset'
  | 'created_at'
> & {
  origin_citation_id: string
  spans: NotebookNoteCitationSpan[]
}

export type FreeNoteOriginSnapshot = {
  origin_type: 'free'
}

export type ChatAnswerNoteOriginSnapshot = {
  origin_type: 'chat_answer'
  conversation_id: string
  message_id: string
  answer_text: string
  provider: string | null
  model: string | null
  citations: NotebookNoteCitation[]
}

export type NotebookNoteOriginSnapshot =
  | FreeNoteOriginSnapshot
  | ChatAnswerNoteOriginSnapshot

export type NotebookNote = {
  id: string
  course_id: string
  title: string
  body_markdown: string
  revision: number
  origin_type: NotebookNoteOriginType
  origin_snapshot: NotebookNoteOriginSnapshot
  published_snapshot_id: string | null
  published_revision: number | null
  is_source_outdated: boolean
  created_at: string
  updated_at: string
}

export type NotebookNoteSummary = {
  id: string
  course_id: string
  title: string
  body_preview: string
  revision: number
  origin_type: NotebookNoteOriginType
  citation_count: number
  published_snapshot_id: string | null
  published_revision: number | null
  is_source_outdated: boolean
  created_at: string
  updated_at: string
}

export type NotebookNoteCreate = {
  title?: string
  body_markdown: string
}

export type NotebookNoteUpdate = {
  title?: string
  body_markdown?: string
  expected_revision: number
}

export type NotebookNoteSourceSnapshot = {
  id: string
  note_id: string
  course_id: string
  note_revision: number
  title: string
  body_markdown: string
  content_hash: string
  created_at: string
}

export type NotebookNotePromotion = {
  note: NotebookNote
  snapshot: NotebookNoteSourceSnapshot
  source: CourseSource
  replayed: boolean
}

export type NotebookNoteSaveState =
  | { status: 'idle' }
  | { status: 'pending' }
  | { status: 'saved'; noteId: string }
  | { status: 'error'; message: string }
