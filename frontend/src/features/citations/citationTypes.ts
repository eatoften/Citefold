import type {
  SourceLocator,
  SourceType,
} from '../sources/sourceTypes'

export type CitationTargetAvailability =
  | 'available'
  | 'snapshot_only'

export type CitationMediaKind =
  | 'video'
  | 'audio'
  | 'pdf'
  | 'document'
  | 'text'

export type CitationTargetContextChunk = {
  chunk_id: string
  ordinal: number
  text: string
  locator: SourceLocator
  is_target: boolean
}

export type CitationTarget = {
  citation_id: string
  availability: CitationTargetAvailability
  reason: string | null
  reason_message: string | null
  source_id: string
  source_title: string
  source_type: SourceType
  quote: string
  locator: SourceLocator
  media_kind: CitationMediaKind | null
  media_url: string | null
  mime_type: string | null
  target_chunk_id: string | null
  context: CitationTargetContextChunk[]
}
