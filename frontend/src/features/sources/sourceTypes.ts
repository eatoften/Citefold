export type SourceType =
  | 'video'
  | 'audio'
  | 'pptx'
  | 'pdf'
  | 'docx'
  | 'text'

export type SourceContentStatus =
  | 'pending'
  | 'processing'
  | 'ready'
  | 'failed'

export type SourceIndexStatus =
  | 'not_indexed'
  | 'indexing'
  | 'ready'
  | 'stale'
  | 'failed'

type LocatorBase = {
  schema_version: 1
  metadata: Record<string, unknown>
}

export type VideoTimeLocator = LocatorBase & {
  kind: 'video_time'
  job_id: string | null
  asset_id: string | null
  start_seconds: number
  end_seconds: number
  segment_ids: number[]
}

export type PdfPageLocator = LocatorBase & {
  kind: 'pdf_page'
  asset_id: string
  page_number: number
}

export type PptSlideLocator = LocatorBase & {
  kind: 'ppt_slide'
  asset_id: string
  slide_number: number
}

export type DocxParagraphLocator = LocatorBase & {
  kind: 'docx_paragraph'
  asset_id: string
  paragraph_number: number
}

export type TextSectionLocator = LocatorBase & {
  kind: 'text_section'
  asset_id: string
  section_number: number
}

export type NoteSectionLocator = LocatorBase & {
  kind: 'note_section'
  note_id: string
  snapshot_id: string
  section_number: number
}

export type KnownSourceLocator =
  | VideoTimeLocator
  | PdfPageLocator
  | PptSlideLocator
  | DocxParagraphLocator
  | TextSectionLocator
  | NoteSectionLocator

export type UnknownSourceLocator = {
  kind: string
  schema_version: number
  metadata: Record<string, unknown>
  [key: string]: unknown
}

export type SourceLocator =
  | KnownSourceLocator
  | UnknownSourceLocator

export type CourseSource = {
  id: string
  course_id: string
  origin_type: 'video_job' | 'source_asset' | 'notebook_note'
  origin_id: string
  source_type: SourceType
  title: string
  content_status: SourceContentStatus
  index_status: SourceIndexStatus
  index_model: string | null
  index_dimension: number | null
  enabled: boolean
  chunk_count: number
  indexed_chunk_count: number
  projection_generation_id: string | null
  projection_manifest_hash: string | null
  size_bytes: number | null
  mime_type: string | null
  metadata: Record<string, unknown>
  error_message: string | null
  index_error: string | null
  created_at: string
  updated_at: string
  indexed_at: string | null
}

export type CourseSourceChunk = {
  id: string
  source_id: string
  origin_type:
    | 'transcript_chunk'
    | 'source_unit'
    | 'notebook_note_snapshot'
  origin_id: string
  chunk_type:
    | 'transcript'
    | 'slide'
    | 'page'
    | 'paragraph'
    | 'text'
    | 'video_frame'
  ordinal: number
  text: string
  text_hash: string
  locator: SourceLocator
  chunker_version: string
  is_active: boolean
  created_at: string
  updated_at: string
}

export type SourceIndexResult = {
  source_ids: string[]
  total_sources: number
  unavailable_source_ids: string[]
  total_chunks: number
  embedded_chunks: number
  skipped_chunks: number
  model: string
  dimension: number | null
}

export type SourceAssetImportResult = {
  asset: {
    id: string
    course_id: string
    original_filename: string
    extraction_status: 'pending' | 'ready' | 'failed'
    unit_count: number
  }
  units: Array<{
    id: string
  }>
}
