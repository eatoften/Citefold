import {
  useEffect,
  lazy,
  useMemo,
  useRef,
  useState,
  Suspense,
  type ChangeEvent,
  type MouseEvent,
} from 'react'
import { invoke } from '@tauri-apps/api/core'
import { AppSidebar, type AppView } from './AppSidebar'
import { CourseMapView } from './CourseMapView'
import { ChatPanel } from './features/chat'
import { GraphView } from './GraphView'
import { ReviewView } from './ReviewView'
import './App.css'

const StudyView = lazy(() =>
  import('./StudyView').then((module) => ({ default: module.StudyView })),
)

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8001'
const BACKEND_HEALTH_TIMEOUT_MS = 1000
const BACKEND_STARTUP_TIMEOUT_MS = 45000
const BACKEND_POLL_INTERVAL_MS = 500

const DEFAULT_COURSE_ID = 'uncategorized'
const TAURI_INTERNALS_KEY = '__TAURI_INTERNALS__'

declare global {
  interface Window {
    [TAURI_INTERNALS_KEY]?: unknown
  }
}

type JobStatus =
  | 'uploaded'
  | 'probing'
  | 'extracting_audio'
  | 'transcribing'
  | 'completed'
  | 'failed'

type VideoMetadata = {
  duration_seconds: number
  width: number
  height: number
  video_codec: string
  has_audio: boolean
}

type VideoJob = {
  id: string
  course_id: string
  video_path: string
  status: JobStatus
  original_filename: string | null
  stored_name: string | null
  size_bytes: number | null
  metadata: VideoMetadata | null
  transcript_path: string | null
  error_message: string | null
  created_at: string
  updated_at: string
  started_at: string | null
  completed_at: string | null
}

type Course = {
  id: string
  title: string
  description: string | null
  created_at: string
  updated_at: string
  job_count: number
  card_count: number
}

type TranscriptSegment = {
  start_seconds: number
  end_seconds: number
  text: string
}

type TranscriptionResult = {
  language: string
  language_probability: number
  duration_seconds: number
  segments: TranscriptSegment[]
}

type TranscriptContext = {
  job_id: string
  source_video: string
  start_seconds: number
  end_seconds: number
  segments: TranscriptSegment[]
  text: string
}

type LlmStatus = {
  provider: string
  base_url: string
  model: string
  available: boolean
  error_message: string | null
}

type LlmModelList = {
  provider: string
  base_url: string
  default_model: string
  models: string[]
  available: boolean
  error_message: string | null
}

type RuntimeDependencyStatus = {
  name: string
  available: boolean
  version: string | null
  detail: string | null
  install_hint: string | null
  required_for: string[]
}

type RuntimeStatus = {
  ready: boolean
  dependencies: RuntimeDependencyStatus[]
}

type KnowledgeCardEvidence = {
  id: string
  quote: string
  segment_start_seconds: number
  segment_end_seconds: number
}

type KnowledgeCardClaim = {
  id: string
  text: string
  evidence: KnowledgeCardEvidence[]
}

type KnowledgeCardDraft = {
  title: string
  summary: string
  key_points: string[]
  claims: KnowledgeCardClaim[]
  unsupported_terms: string[]
  question: string
  answer: string
  source_start_seconds: number
  source_end_seconds: number
}

type CardContentStatus = 'draft' | 'reviewed' | 'needs_fix'
type CardKind =
  | 'concept'
  | 'definition'
  | 'process'
  | 'comparison'
  | 'example'
  | 'formula'

type ReviewItem = {
  id: string
  card_id: string
  item_type: 'basic' | 'cloze' | 'explain' | 'compare' | 'apply'
  prompt: string
  expected_answer: string
  source_claim_ids: string[]
  source: 'generated' | 'manual' | 'local_llm'
  status: 'active' | 'disabled'
  created_at: string
  updated_at: string
}

type CardGenerationMetadata = {
  provider: string
  model: string
  elapsed_seconds: number
  input_characters: number
  selected_context_characters: number
  selected_segments_count: number
  requested_card_count: number
  raw_card_count: number
  returned_card_count: number
  raw_claim_count: number
  grounded_claim_count: number
  dropped_claim_count: number
  unsupported_terms_count: number
  max_context_characters: number
  max_selected_segments: number
}

type CardDraftResponse = {
  job_id: string
  source_video: string
  start_seconds: number
  end_seconds: number
  provider: string
  model: string
  generation_metadata: CardGenerationMetadata
  cards: KnowledgeCardDraft[]
}

type CardGenerationMode = 'manual' | 'auto'
type CardRailTab = 'cards' | 'ask'
type CardGenerationRunStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'canceled'

type CardGenerationRunError = {
  chunk_id: string | null
  chunk_index: number | null
  message: string
}

type CardGenerationRun = {
  id: string
  job_id: string
  mode: 'auto'
  status: CardGenerationRunStatus
  model: string | null
  card_count_per_chunk: number
  total_chunks: number
  completed_chunks: number
  succeeded_chunks: number
  failed_chunks: number
  cards_created: number
  error_message: string | null
  errors: CardGenerationRunError[]
  created_at: string
  updated_at: string
  started_at: string | null
  completed_at: string | null
}

type KnowledgeCard = Omit<KnowledgeCardDraft, 'question' | 'answer'> & {
  id: string
  job_id: string
  card_kind: CardKind
  tags: string[]
  content_status: CardContentStatus
  review_items: ReviewItem[]
  provider: string | null
  model: string | null
  created_at: string
  updated_at: string
}

type KnowledgeCardIndexItem = {
  id: string
  job_id: string
  title: string
  summary: string
  card_kind: CardKind
  tags: string[]
  content_status: CardContentStatus
  review_item_count: number
  source_video: string | null
  source_start_seconds: number
  source_end_seconds: number
  note_count: number
  learning_document_count: number
  created_at: string
  updated_at: string
}

type KnowledgeCardNoteType =
  | 'user_note'
  | 'llm_explanation'
  | 'web_tutorial'
  | 'practice_question'

type KnowledgeCardNoteSource = 'user' | 'local_llm' | 'web_llm'

type KnowledgeCardNoteReference = {
  title: string | null
  url: string | null
  accessed_at: string | null
}

type KnowledgeCardNote = {
  id: string
  card_id: string
  note_type: KnowledgeCardNoteType
  title: string | null
  body: string
  source: KnowledgeCardNoteSource
  sources: KnowledgeCardNoteReference[]
  created_at: string
  updated_at: string
}

type CardEditForm = {
  card_kind: CardKind
  title: string
  summary: string
  key_points: string
  tags: string
  content_status: CardContentStatus
}

type ReviewItemForm = {
  item_type: ReviewItem['item_type']
  prompt: string
  expected_answer: string
}

type NoteForm = {
  note_type: KnowledgeCardNoteType
  title: string
  body: string
}

type UploadResponse = {
  id: string
  course_id: string
  filename: string
  stored_name: string
  size_bytes: number
  status: JobStatus
}

type SavedFolderResponse = {
  root_path: string
  file_count: number
  files: string[]
}

type SegmentRange = {
  anchorIndex: number
  startIndex: number
  endIndex: number
}

type BackendProcessStatus = {
  ready: boolean
  mode: string
  message: string
}

type BackendBootPhase = 'checking' | 'starting' | 'ready' | 'failed'

type BackendBootState = {
  phase: BackendBootPhase
  message: string
  mode: string
}

const runningStatuses: JobStatus[] = [
  'probing',
  'extracting_audio',
  'transcribing',
]

function isTauriRuntime(): boolean {
  return Boolean(window[TAURI_INTERNALS_KEY])
}

function getViewFromUrl(): AppView {
  const view = new URL(window.location.href).searchParams.get('view')
  if (
    view === 'course-map' ||
    view === 'study' ||
    view === 'review' ||
    view === 'graph'
  ) {
    return view
  }
  return 'workspace'
}

function sleep(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}

async function checkBackendHealth(
  timeoutMs = BACKEND_HEALTH_TIMEOUT_MS,
): Promise<boolean> {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs)

  try {
    const response = await fetch(`${API_BASE_URL}/health`, {
      cache: 'no-store',
      signal: controller.signal,
    })

    return response.ok
  } catch {
    return false
  } finally {
    window.clearTimeout(timeoutId)
  }
}

async function waitForBackendHealth(
  timeoutMs = BACKEND_STARTUP_TIMEOUT_MS,
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs

  while (Date.now() < deadline) {
    if (await checkBackendHealth()) {
      return true
    }

    await sleep(BACKEND_POLL_INTERVAL_MS)
  }

  return false
}

async function ensureBackendReady(): Promise<BackendBootState> {
  if (await checkBackendHealth()) {
    return {
      phase: 'ready',
      mode: 'external',
      message: `Backend ready at ${API_BASE_URL}.`,
    }
  }

  if (isTauriRuntime()) {
    try {
      const status = await invoke<BackendProcessStatus>('ensure_backend')

      if (status.ready || (await waitForBackendHealth())) {
        return {
          phase: 'ready',
          mode: status.mode || 'sidecar',
          message: status.message || `Backend ready at ${API_BASE_URL}.`,
        }
      }

      return {
        phase: 'failed',
        mode: status.mode || 'sidecar',
        message:
          status.message || 'Local backend did not become ready in time.',
      }
    } catch (error) {
      return {
        phase: 'failed',
        mode: 'sidecar',
        message:
          error instanceof Error
            ? error.message
            : 'Failed to start local backend sidecar.',
      }
    }
  }

  if (await waitForBackendHealth(5000)) {
    return {
      phase: 'ready',
      mode: 'external',
      message: `Backend ready at ${API_BASE_URL}.`,
    }
  }

  return {
    phase: 'failed',
    mode: 'manual',
    message:
      'Backend is not running. Start FastAPI manually, then retry.',
  }
}

function createDefaultNoteForm(): NoteForm {
  return {
    note_type: 'user_note',
    title: '',
    body: '',
  }
}

function createDefaultReviewItemForm(): ReviewItemForm {
  return {
    item_type: 'basic',
    prompt: '',
    expected_answer: '',
  }
}

async function fetchJson<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, options)

  if (!response.ok) {
    let message = `HTTP ${response.status}`

    try {
      const payload = await response.json()
      if (typeof payload.detail === 'string') {
        message = payload.detail
      }
    } catch {
      // Keep the HTTP status message.
    }

    throw new Error(message)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return response.json() as Promise<T>
}

async function exportMarkdownFolder(path: string): Promise<SavedFolderResponse> {
  const response = await fetch(
    `${API_BASE_URL}${path}`,
    {
      method: 'POST',
    },
  )

  if (!response.ok) {
    let message = `HTTP ${response.status}`

    try {
      const payload = await response.json()
      if (typeof payload.detail === 'string') {
        message = payload.detail
      }
    } catch {
      // Keep the HTTP status message.
    }

    throw new Error(message)
  }

  return response.json() as Promise<SavedFolderResponse>
}

function formatTime(seconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(seconds))
  const minutes = Math.floor(totalSeconds / 60)
  const remainingSeconds = totalSeconds % 60

  return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`
}

function formatSize(bytes: number | null): string {
  if (!bytes) {
    return '-'
  }

  const megabytes = bytes / 1024 / 1024
  return `${megabytes.toFixed(1)} MB`
}

function formatElapsed(seconds: number): string {
  if (seconds < 1) {
    return `${Math.round(seconds * 1000)} ms`
  }

  return `${seconds.toFixed(1)} s`
}

function parseTags(value: string): string[] {
  const tags: string[] = []

  for (const tag of value.split(',')) {
    const normalizedTag = tag.trim().toLowerCase()

    if (normalizedTag && !tags.includes(normalizedTag)) {
      tags.push(normalizedTag)
    }
  }

  return tags
}

type CardSignatureSource = Pick<
  KnowledgeCardDraft,
  'title' | 'source_start_seconds' | 'source_end_seconds' | 'claims'
>

type SourceSortableCard = {
  title: string
  source_start_seconds: number
  source_end_seconds: number
}

function cardSignature(card: CardSignatureSource): string {
  return JSON.stringify({
    title: card.title.trim(),
    source_start_seconds: card.source_start_seconds,
    source_end_seconds: card.source_end_seconds,
    claims: card.claims.map((claim) => ({
      text: claim.text.trim(),
      evidence: claim.evidence.map((evidence) => ({
        quote: evidence.quote.trim(),
        segment_start_seconds: evidence.segment_start_seconds,
        segment_end_seconds: evidence.segment_end_seconds,
      })),
    })),
  })
}

function sortCardsBySource<T extends SourceSortableCard>(cards: T[]): T[] {
  return [...cards].sort((left, right) => {
    if (left.source_start_seconds !== right.source_start_seconds) {
      return left.source_start_seconds - right.source_start_seconds
    }

    return left.title.localeCompare(right.title)
  })
}

function isAutoGenerationActive(
  run: CardGenerationRun | null,
): boolean {
  return run?.status === 'pending' || run?.status === 'running'
}

function autoGenerationStatusText(run: CardGenerationRun): string {
  if (run.status === 'pending') {
    return 'Queued'
  }

  if (run.status === 'running') {
    return `Processing ${run.completed_chunks} / ${run.total_chunks} chunks`
  }

  if (run.status === 'completed') {
    return `Created ${run.cards_created} cards`
  }

  if (run.status === 'failed') {
    return run.error_message || 'Auto generation failed'
  }

  return 'Canceled'
}

function ClaimsBlock({
  claims,
  unsupportedTerms,
  onJumpToTime,
}: {
  claims: KnowledgeCardClaim[]
  unsupportedTerms: string[]
  onJumpToTime: (seconds: number) => void
}) {
  return (
    <div className="claims-block">
      <div className="claims-title">Claims</div>
      {claims.map((claim, claimIndex) => (
        <div
          className="claim-item"
          key={`${claim.text}-${claimIndex}`}
        >
          <p>{claim.text}</p>
          {claim.evidence.map((evidence, evidenceIndex) => (
            <blockquote
              key={`${evidence.quote}-${evidenceIndex}`}
            >
              <button
                type="button"
                className="evidence-time"
                onClick={() =>
                  onJumpToTime(evidence.segment_start_seconds)
                }
              >
                {formatTime(evidence.segment_start_seconds)} -{' '}
                {formatTime(evidence.segment_end_seconds)}
              </button>
              {evidence.quote}
            </blockquote>
          ))}
        </div>
      ))}
      {unsupportedTerms.length > 0 && (
        <div className="unsupported-terms">
          Review terms: {unsupportedTerms.join(', ')}
        </div>
      )}
    </div>
  )
}

function App() {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const cardGenerationAbortRef = useRef<AbortController | null>(null)
  const [appView, setAppView] = useState<AppView>(getViewFromUrl)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [videoUrl, setVideoUrl] = useState<string | null>(null)
  const [courses, setCourses] = useState<Course[]>([])
  const [selectedCourseId, setSelectedCourseId] = useState<string | null>(null)
  const [newCourseTitle, setNewCourseTitle] = useState('')
  const [renamingCourseId, setRenamingCourseId] = useState<string | null>(null)
  const [courseRenameTitle, setCourseRenameTitle] = useState('')
  const [jobs, setJobs] = useState<VideoJob[]>([])
  const [job, setJob] = useState<VideoJob | null>(null)
  const [transcript, setTranscript] = useState<TranscriptionResult | null>(null)
  const [selectedRange, setSelectedRange] = useState<SegmentRange | null>(null)
  const [transcriptContext, setTranscriptContext] =
    useState<TranscriptContext | null>(null)
  const [llmStatus, setLlmStatus] = useState<LlmStatus | null>(null)
  const [availableModels, setAvailableModels] = useState<string[]>([])
  const [selectedModel, setSelectedModel] = useState('')
  const [generationMode, setGenerationMode] =
    useState<CardGenerationMode>('manual')
  const [cardDraft, setCardDraft] = useState<CardDraftResponse | null>(null)
  const [autoGenerationRun, setAutoGenerationRun] =
    useState<CardGenerationRun | null>(null)
  const [savedCards, setSavedCards] = useState<KnowledgeCard[]>([])
  const [courseCardIndex, setCourseCardIndex] = useState<
    KnowledgeCardIndexItem[]
  >([])
  const [cardRailSearch, setCardRailSearch] = useState('')
  const [cardRailReviewFilter, setCardRailReviewFilter] = useState<
    'all' | CardContentStatus
  >('all')
  const [cardRailTagFilter, setCardRailTagFilter] = useState('')
  const [cardRailNoteFilter, setCardRailNoteFilter] = useState<
    'all' | 'has_notes' | 'no_notes'
  >('all')
  const [isCardRailOpen, setIsCardRailOpen] = useState(false)
  const [cardRailTab, setCardRailTab] = useState<CardRailTab>('cards')
  const [selectedRailCard, setSelectedRailCard] =
    useState<KnowledgeCard | null>(null)
  const [isLoadingCourseCards, setIsLoadingCourseCards] = useState(false)
  const [isLoadingRailCard, setIsLoadingRailCard] = useState(false)
  const [railCardEditForm, setRailCardEditForm] =
    useState<CardEditForm | null>(null)
  const [cardNotes, setCardNotes] = useState<
    Record<string, KnowledgeCardNote[]>
  >({})
  const [editingCardId, setEditingCardId] = useState<string | null>(null)
  const [cardEditForm, setCardEditForm] = useState<CardEditForm | null>(null)
  const [noteForms, setNoteForms] = useState<Record<string, NoteForm>>({})
  const [reviewItemForms, setReviewItemForms] = useState<
    Record<string, ReviewItemForm>
  >({})
  const [editingNoteId, setEditingNoteId] = useState<string | null>(null)
  const [noteEditForm, setNoteEditForm] = useState<NoteForm | null>(null)
  const [cardFocus, setCardFocus] = useState('')
  const [currentTime, setCurrentTime] = useState(0)
  const [isUploading, setIsUploading] = useState(false)
  const [isStarting, setIsStarting] = useState(false)
  const [isLoadingContext, setIsLoadingContext] = useState(false)
  const [isLoadingCourses, setIsLoadingCourses] = useState(false)
  const [isLoadingJobs, setIsLoadingJobs] = useState(false)
  const [isSavingCourse, setIsSavingCourse] = useState(false)
  const [isSavingCard, setIsSavingCard] = useState(false)
  const [isSavingNote, setIsSavingNote] = useState(false)
  const [isSavingReviewItem, setIsSavingReviewItem] = useState(false)
  const [isExportingCards, setIsExportingCards] = useState(false)
  const [isDeletingJob, setIsDeletingJob] = useState(false)
  const [isCheckingLlm, setIsCheckingLlm] = useState(false)
  const [isCheckingRuntime, setIsCheckingRuntime] = useState(false)
  const [isDraftingCards, setIsDraftingCards] = useState(false)
  const [isStartingAutoGeneration, setIsStartingAutoGeneration] =
    useState(false)
  const [generationStatus, setGenerationStatus] = useState<string | null>(null)
  const [exportMessage, setExportMessage] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [runtimeStatus, setRuntimeStatus] = useState<RuntimeStatus | null>(null)
  const [isRuntimePanelOpen, setIsRuntimePanelOpen] = useState(false)
  const [backendBoot, setBackendBoot] = useState<BackendBootState>({
    phase: 'checking',
    mode: isTauriRuntime() ? 'sidecar' : 'manual',
    message: 'Checking local backend.',
  })

  const activeSegmentIndex = useMemo(() => {
    if (!transcript) {
      return -1
    }

    return transcript.segments.findIndex((segment) => {
      return (
        currentTime >= segment.start_seconds &&
        currentTime < segment.end_seconds
      )
    })
  }, [currentTime, transcript])

  const selectedCourse = useMemo(() => {
    return courses.find((course) => course.id === selectedCourseId) ?? null
  }, [courses, selectedCourseId])

  const totalSavedCardCount = useMemo(() => {
    return courses.reduce((total, course) => total + course.card_count, 0)
  }, [courses])

  const selectedSegments = useMemo(() => {
    if (!transcript || !selectedRange) {
      return []
    }

    return transcript.segments.slice(
      selectedRange.startIndex,
      selectedRange.endIndex + 1,
    )
  }, [selectedRange, transcript])

  const missingRuntimeDependencies = useMemo(() => {
    return runtimeStatus?.dependencies.filter(
      (dependency) => !dependency.available,
    ) ?? []
  }, [runtimeStatus])

  const filteredCourseCardIndex = useMemo(() => {
    const query = cardRailSearch.trim().toLowerCase()
    const tagQuery = cardRailTagFilter.trim().toLowerCase()

    return courseCardIndex.filter((card) => {
      if (
        cardRailReviewFilter !== 'all' &&
        card.content_status !== cardRailReviewFilter
      ) {
        return false
      }

      if (
        cardRailNoteFilter === 'has_notes' &&
        card.note_count === 0
      ) {
        return false
      }

      if (
        cardRailNoteFilter === 'no_notes' &&
        card.note_count > 0
      ) {
        return false
      }

      if (
        tagQuery &&
        !card.tags.some((tag) => tag.includes(tagQuery))
      ) {
        return false
      }

      if (!query) {
        return true
      }

      return [
        card.title,
        card.summary,
        card.source_video ?? '',
        ...card.tags,
      ].some((value) => value.toLowerCase().includes(query))
    })
  }, [
    cardRailNoteFilter,
    cardRailReviewFilter,
    cardRailSearch,
    cardRailTagFilter,
    courseCardIndex,
  ])

  const savedCardSignatures = useMemo(() => {
    return new Set(savedCards.map((card) => cardSignature(card)))
  }, [savedCards])

  const unsavedDraftCards = useMemo(() => {
    if (!cardDraft) {
      return []
    }

    return cardDraft.cards.filter(
      (card) => !savedCardSignatures.has(cardSignature(card)),
    )
  }, [cardDraft, savedCardSignatures])

  function clearTranscriptSelection() {
    setSelectedRange(null)
    setTranscriptContext(null)
    setCardDraft(null)
    setGenerationStatus(null)
    setExportMessage(null)
    setEditingCardId(null)
    setCardEditForm(null)
    setEditingNoteId(null)
    setNoteEditForm(null)
  }

  function handleVideoChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]

    if (!file) {
      return
    }

    const objectUrl = URL.createObjectURL(file)

    setSelectedFile(file)
    setVideoUrl((previousUrl) => {
      if (previousUrl) {
        URL.revokeObjectURL(previousUrl)
      }

      return objectUrl
    })
    setJob(null)
    setTranscript(null)
    setSavedCards([])
    setCardNotes({})
    setNoteForms({})
    clearTranscriptSelection()
    setAutoGenerationRun(null)
    setErrorMessage(null)
    setExportMessage(null)
  }

  async function refreshJob(jobId: string): Promise<VideoJob> {
    const nextJob = await fetchJson<VideoJob>(`/jobs/${jobId}`)
    setJob(nextJob)

    return nextJob
  }

  async function loadCourses(preferredCourseId?: string): Promise<Course[]> {
    setIsLoadingCourses(true)

    try {
      const nextCourses = await fetchJson<Course[]>('/courses')
      const urlCourseId = new URL(window.location.href).searchParams.get(
        'course',
      )
      const fallbackCourseId =
        nextCourses.find((course) => course.id === DEFAULT_COURSE_ID)?.id ??
        nextCourses[0]?.id ??
        null

      setCourses(nextCourses)
      setSelectedCourseId((previousCourseId) => {
        if (
          preferredCourseId &&
          nextCourses.some((course) => course.id === preferredCourseId)
        ) {
          return preferredCourseId
        }

        if (
          urlCourseId &&
          nextCourses.some((course) => course.id === urlCourseId)
        ) {
          return urlCourseId
        }

        if (
          previousCourseId &&
          nextCourses.some((course) => course.id === previousCourseId)
        ) {
          return previousCourseId
        }

        return fallbackCourseId
      })

      return nextCourses
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Course list failed.',
      )
      return []
    } finally {
      setIsLoadingCourses(false)
    }
  }

  async function loadJobs(courseId: string | null = selectedCourseId) {
    setIsLoadingJobs(true)

    try {
      const nextJobs = await fetchJson<VideoJob[]>(
        courseId ? `/courses/${courseId}/jobs` : '/jobs',
      )
      setJobs(nextJobs)
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Job list failed.',
      )
    } finally {
      setIsLoadingJobs(false)
    }
  }

  async function loadCourseCardIndex(
    courseId: string | null = selectedCourseId,
  ) {
    if (!courseId) {
      setCourseCardIndex([])
      return
    }

    setIsLoadingCourseCards(true)

    try {
      const cards = await fetchJson<KnowledgeCardIndexItem[]>(
        `/courses/${courseId}/card-index`,
      )
      setCourseCardIndex(cards)
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : 'Course cards failed.',
      )
    } finally {
      setIsLoadingCourseCards(false)
    }
  }

  async function openRailCard(cardId: string) {
    setIsCardRailOpen(true)
    setIsLoadingRailCard(true)
    setErrorMessage(null)

    try {
      const [card, notes] = await Promise.all([
        fetchJson<KnowledgeCard>(`/cards/${cardId}`),
        fetchJson<KnowledgeCardNote[]>(`/cards/${cardId}/notes`),
      ])

      setSelectedRailCard(card)
      setRailCardEditForm({
        title: card.title,
        summary: card.summary,
        key_points: card.key_points.join('\n'),
        tags: card.tags.join(', '),
        card_kind: card.card_kind,
        content_status: card.content_status,
      })
      setCardNotes((previousNotes) => ({
        ...previousNotes,
        [card.id]: notes,
      }))

      const url = new URL(window.location.href)
      if (selectedCourseId) {
        url.searchParams.set('course', selectedCourseId)
      }
      url.searchParams.set('card', card.id)
      window.history.pushState({}, '', url)
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Card loading failed.',
      )
    } finally {
      setIsLoadingRailCard(false)
    }
  }

  function closeRailCard() {
    setSelectedRailCard(null)
    setRailCardEditForm(null)

    const url = new URL(window.location.href)
    url.searchParams.delete('card')
    window.history.pushState({}, '', url)
  }

  function clearActiveJob() {
    setJob(null)
    setSelectedFile(null)
    setVideoUrl((previousUrl) => {
      if (previousUrl) {
        URL.revokeObjectURL(previousUrl)
      }

      return null
    })
    setTranscript(null)
    setSavedCards([])
    setCardNotes({})
    setNoteForms({})
    clearTranscriptSelection()
    setAutoGenerationRun(null)
    setExportMessage(null)
  }

  function selectCourse(courseId: string) {
    setSelectedCourseId(courseId)
    setJobs([])
    setCourseCardIndex([])
    setSelectedRailCard(null)
    setRailCardEditForm(null)
    clearActiveJob()
    setErrorMessage(null)

    const url = new URL(window.location.href)
    url.searchParams.set('course', courseId)
    url.searchParams.delete('card')
    window.history.pushState({}, '', url)
  }

  function changeAppView(view: AppView) {
    setAppView(view)
    const url = new URL(window.location.href)
    url.searchParams.set('view', view)
    window.history.pushState({}, '', url)
  }

  function openWorkspaceCard(cardId: string) {
    changeAppView('workspace')
    void openRailCard(cardId)
  }

  function openStudyCard(cardId: string) {
    setAppView('study')
    const url = new URL(window.location.href)
    url.searchParams.set('view', 'study')
    url.searchParams.set('card', cardId)
    url.searchParams.delete('document')
    if (selectedCourseId) url.searchParams.set('course', selectedCourseId)
    window.history.pushState({}, '', url)
  }

  async function createCourse() {
    const title = newCourseTitle.trim()

    if (!title) {
      return
    }

    setIsSavingCourse(true)
    setErrorMessage(null)

    try {
      const createdCourse = await fetchJson<Course>('/courses', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          title,
        }),
      })

      setNewCourseTitle('')
      setSelectedCourseId(createdCourse.id)
      await loadCourses(createdCourse.id)
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Course create failed.',
      )
    } finally {
      setIsSavingCourse(false)
    }
  }

  function startRenamingCourse(course: Course) {
    setRenamingCourseId(course.id)
    setCourseRenameTitle(course.title)
    setErrorMessage(null)
    setExportMessage(null)
  }

  function cancelRenamingCourse() {
    setRenamingCourseId(null)
    setCourseRenameTitle('')
  }

  async function renameCourse(course: Course) {
    const title = courseRenameTitle.trim()

    if (!title) {
      setErrorMessage('Course title is required.')
      return
    }

    if (title === course.title) {
      cancelRenamingCourse()
      return
    }

    setIsSavingCourse(true)
    setErrorMessage(null)

    try {
      await fetchJson<Course>(
        `/courses/${course.id}`,
        {
          method: 'PATCH',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            title,
          }),
        },
      )
      await loadCourses(course.id)
      cancelRenamingCourse()
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Course rename failed.',
      )
    } finally {
      setIsSavingCourse(false)
    }
  }

  async function deleteCourse(course: Course) {
    if (course.id === DEFAULT_COURSE_ID) {
      return
    }

    const confirmed = window.confirm(
      `Delete course "${course.title}"? Its videos will move to Uncategorized.`,
    )

    if (!confirmed) {
      return
    }

    setIsSavingCourse(true)
    setErrorMessage(null)

    try {
      await fetchJson<void>(
        `/courses/${course.id}`,
        {
          method: 'DELETE',
        },
      )

      if (selectedCourseId === course.id) {
        clearActiveJob()
        setSelectedCourseId(DEFAULT_COURSE_ID)
      }

      await loadCourses(DEFAULT_COURSE_ID)
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Course delete failed.',
      )
    } finally {
      setIsSavingCourse(false)
    }
  }

  async function loadTranscript(jobId: string) {
    const nextTranscript = await fetchJson<TranscriptionResult>(
      `/jobs/${jobId}/transcript`,
    )
    setTranscript(nextTranscript)
    setCardDraft(null)
  }

  async function loadSavedCards(jobId: string) {
    const cards = await fetchJson<KnowledgeCard[]>(
      `/jobs/${jobId}/cards`,
    )
    const sortedCards = sortCardsBySource(cards)

    setSavedCards(sortedCards)
    await loadNotesForCards(sortedCards)
  }

  async function loadLatestAutoGenerationRun(jobId: string) {
    const runs = await fetchJson<CardGenerationRun[]>(
      `/jobs/${jobId}/card-generation-runs`,
    )

    setAutoGenerationRun(runs[0] ?? null)
  }

  async function refreshAutoGenerationRun(
    runId: string,
  ): Promise<CardGenerationRun> {
    const nextRun = await fetchJson<CardGenerationRun>(
      `/card-generation-runs/${runId}`,
    )

    setAutoGenerationRun(nextRun)

    return nextRun
  }

  async function loadNotesForCards(cards: KnowledgeCard[]) {
    if (cards.length === 0) {
      setCardNotes({})
      return
    }

    const entries = await Promise.all(
      cards.map(async (card) => {
        const notes = await fetchJson<KnowledgeCardNote[]>(
          `/cards/${card.id}/notes`,
        )

        return [card.id, notes] as const
      }),
    )

    setCardNotes(Object.fromEntries(entries))
  }

  async function openJob(nextJob: VideoJob) {
    setJob(nextJob)
    setSelectedFile(null)
    setVideoUrl((previousUrl) => {
      if (previousUrl) {
        URL.revokeObjectURL(previousUrl)
      }

      return null
    })
    setTranscript(null)
    setSavedCards([])
    setCardNotes({})
    setNoteForms({})
    clearTranscriptSelection()
    setErrorMessage(null)

    try {
      await loadSavedCards(nextJob.id)
      await loadLatestAutoGenerationRun(nextJob.id)

      if (nextJob.transcript_path) {
        await loadTranscript(nextJob.id)
      }
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Job loading failed.',
      )
    }
  }

  async function deleteVideoJob(jobToDelete: VideoJob) {
    setIsDeletingJob(true)
    setErrorMessage(null)

    try {
      await fetchJson<void>(
        `/jobs/${jobToDelete.id}`,
        {
          method: 'DELETE',
        },
      )

      setJobs((previousJobs) =>
        previousJobs.filter((item) => item.id !== jobToDelete.id),
      )
      await loadCourses(jobToDelete.course_id)
      await loadCourseCardIndex(jobToDelete.course_id)

      if (selectedRailCard?.job_id === jobToDelete.id) {
        closeRailCard()
      }

      if (job?.id === jobToDelete.id) {
        setJob(null)
        setSelectedFile(null)
        setVideoUrl((previousUrl) => {
          if (previousUrl) {
            URL.revokeObjectURL(previousUrl)
          }

          return null
        })
        setTranscript(null)
        setSavedCards([])
        setCardNotes({})
        setNoteForms({})
        clearTranscriptSelection()
      }
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Video delete failed.',
      )
    } finally {
      setIsDeletingJob(false)
    }
  }

  async function checkLlmStatus() {
    setIsCheckingLlm(true)

    try {
      const [status, modelList] = await Promise.all([
        fetchJson<LlmStatus>('/llm/status'),
        fetchJson<LlmModelList>('/llm/models'),
      ])
      setLlmStatus(status)
      setAvailableModels(modelList.models)
      setSelectedModel((previousModel) => {
        if (previousModel) {
          return previousModel
        }

        if (modelList.models.includes(status.model)) {
          return status.model
        }

        return modelList.models[0] ?? status.model
      })
    } catch (error) {
      setLlmStatus(null)
      setAvailableModels([])
      setErrorMessage(
        error instanceof Error
          ? error.message
          : 'Local model status check failed.',
      )
    } finally {
      setIsCheckingLlm(false)
    }
  }

  async function checkRuntimeStatus() {
    setIsCheckingRuntime(true)

    try {
      const status = await fetchJson<RuntimeStatus>('/runtime/check', {
        method: 'POST',
      })
      setRuntimeStatus(status)

      if (status.dependencies.some((dependency) => !dependency.available)) {
        setIsRuntimePanelOpen(true)
      }
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : 'Runtime status check failed.',
      )
    } finally {
      setIsCheckingRuntime(false)
    }
  }

  async function uploadSelectedVideo() {
    if (!selectedFile) {
      return
    }

    setIsUploading(true)
    setErrorMessage(null)
    setTranscript(null)
    clearTranscriptSelection()

    try {
      const formData = new FormData()
      formData.append('video', selectedFile)
      formData.append('course_id', selectedCourseId ?? DEFAULT_COURSE_ID)

      const upload = await fetchJson<UploadResponse>('/videos', {
        method: 'POST',
        body: formData,
      })

      const uploadedJob = await refreshJob(upload.id)
      setSelectedCourseId(uploadedJob.course_id)
      await loadCourses(uploadedJob.course_id)
      await loadJobs(uploadedJob.course_id)
      await loadSavedCards(upload.id)
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Upload failed.',
      )
    } finally {
      setIsUploading(false)
    }
  }

  async function startProcessing(path: 'run' | 'retry') {
    if (!job) {
      return
    }

    setIsStarting(true)
    setErrorMessage(null)
    setTranscript(null)
    clearTranscriptSelection()

    try {
      const nextJob = await fetchJson<VideoJob>(
        `/jobs/${job.id}/${path}`,
        { method: 'POST' },
      )
      setJob(nextJob)
      await loadJobs(nextJob.course_id)
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Processing failed.',
      )
    } finally {
      setIsStarting(false)
    }
  }

  function selectSegment(
    segment: TranscriptSegment,
    index: number,
    event: MouseEvent<HTMLButtonElement>,
  ) {
    setCardDraft(null)

    setSelectedRange((previousRange) => {
      if (event.shiftKey && previousRange) {
        return {
          anchorIndex: previousRange.anchorIndex,
          startIndex: Math.min(previousRange.anchorIndex, index),
          endIndex: Math.max(previousRange.anchorIndex, index),
        }
      }

      return {
        anchorIndex: index,
        startIndex: index,
        endIndex: index,
      }
    })

    if (videoRef.current) {
      videoRef.current.currentTime = segment.start_seconds
      void videoRef.current.play()
    }
  }

  function jumpToTime(seconds: number) {
    if (!videoRef.current) {
      setErrorMessage(
        'Video preview is not loaded. Choose the local video file to jump playback.',
      )
      return
    }

    setErrorMessage(null)
    videoRef.current.currentTime = seconds
    void videoRef.current.play()
  }

  async function generateCards() {
    if (!job || selectedSegments.length === 0) {
      return
    }

    const firstSegment = selectedSegments[0]
    const lastSegment = selectedSegments[selectedSegments.length - 1]

    setIsDraftingCards(true)
    setErrorMessage(null)
    setCardDraft(null)
    setGenerationStatus(
      `Preparing ${selectedSegments.length} transcript segments`,
    )

    const controller = new AbortController()
    cardGenerationAbortRef.current = controller

    try {
      setGenerationStatus(
        `Calling ${selectedModel || llmStatus?.model || 'local model'}`,
      )

      const draft = await fetchJson<CardDraftResponse>('/cards/draft', {
        method: 'POST',
        signal: controller.signal,
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          job_id: job.id,
          start_seconds: firstSegment.start_seconds,
          end_seconds: lastSegment.end_seconds,
          card_count: 3,
          focus: cardFocus.trim() || null,
          model: selectedModel || null,
        }),
      })

      setCardDraft(draft)
      setGenerationStatus(
        `Grounded ${draft.generation_metadata.grounded_claim_count} claims in ${formatElapsed(draft.generation_metadata.elapsed_seconds)}`,
      )
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        setGenerationStatus('Generation canceled')
        return
      }

      setErrorMessage(
        error instanceof Error
          ? error.message
          : 'Card generation failed.',
      )
      setGenerationStatus(null)
    } finally {
      if (cardGenerationAbortRef.current === controller) {
        cardGenerationAbortRef.current = null
      }

      setIsDraftingCards(false)
    }
  }

  async function startAutoGeneration() {
    if (!job) {
      return
    }

    setIsStartingAutoGeneration(true)
    setErrorMessage(null)
    setExportMessage(null)
    setCardDraft(null)

    try {
      const run = await fetchJson<CardGenerationRun>(
        `/jobs/${job.id}/cards/auto-generate`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            model: selectedModel || null,
            focus: cardFocus.trim() || null,
            card_count_per_chunk: 2,
            regenerate_chunks: false,
            chunking: {
              context_radius: 1,
              min_chunk_seconds: 120,
              max_chunk_seconds: 360,
              boundary_percentile: 90,
              replace_existing: true,
            },
          }),
        },
      )

      setAutoGenerationRun(run)

      if (!isAutoGenerationActive(run)) {
        await loadSavedCards(job.id)
        await loadCourses(job.course_id)
        await loadCourseCardIndex(job.course_id)
      }
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : 'Auto generation failed.',
      )
    } finally {
      setIsStartingAutoGeneration(false)
    }
  }

  function cancelCardGeneration() {
    cardGenerationAbortRef.current?.abort()
    setGenerationStatus('Generation canceled')
  }

  async function createSavedCardFromDraft(
    card: KnowledgeCardDraft,
  ): Promise<KnowledgeCard> {
    if (!job || !cardDraft) {
      throw new Error('No active job or draft cards.')
    }

    const { question, answer, ...cardContent } = card

    return fetchJson<KnowledgeCard>(
      `/jobs/${job.id}/cards`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          ...cardContent,
          card_kind: 'concept',
          content_status: 'draft',
          provider: cardDraft.provider,
          model: cardDraft.model,
          review_items: [
            {
              item_type: 'basic',
              prompt: question,
              expected_answer: answer,
              source_claim_ids: card.claims.map((claim) => claim.id),
              source: 'generated',
            },
          ],
        }),
      },
    )
  }

  async function saveDraftCard(card: KnowledgeCardDraft) {
    const activeJob = job

    if (!activeJob) {
      return
    }

    if (savedCardSignatures.has(cardSignature(card))) {
      return
    }

    setIsSavingCard(true)
    setErrorMessage(null)

    try {
      const savedCard = await createSavedCardFromDraft(card)

      setSavedCards((previousCards) =>
        sortCardsBySource([...previousCards, savedCard]),
      )
      setCardNotes((previousNotes) => ({
        ...previousNotes,
        [savedCard.id]: [],
      }))
      await loadCourses(activeJob.course_id)
      await loadCourseCardIndex(activeJob.course_id)
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Card save failed.',
      )
    } finally {
      setIsSavingCard(false)
    }
  }

  async function saveAllDraftCards() {
    const activeJob = job

    if (!activeJob) {
      return
    }

    if (unsavedDraftCards.length === 0) {
      return
    }

    setIsSavingCard(true)
    setErrorMessage(null)

    const savedCardsBatch: KnowledgeCard[] = []

    try {
      for (const card of unsavedDraftCards) {
        savedCardsBatch.push(await createSavedCardFromDraft(card))
      }

      setSavedCards((previousCards) =>
        sortCardsBySource([...previousCards, ...savedCardsBatch]),
      )
      setCardNotes((previousNotes) => {
        const nextNotes = { ...previousNotes }

        for (const savedCard of savedCardsBatch) {
          nextNotes[savedCard.id] = []
        }

        return nextNotes
      })
      await loadCourses(activeJob.course_id)
      await loadCourseCardIndex(activeJob.course_id)
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Save all failed.',
      )

      if (savedCardsBatch.length > 0) {
        setSavedCards((previousCards) =>
          sortCardsBySource([...previousCards, ...savedCardsBatch]),
        )
        setCardNotes((previousNotes) => {
          const nextNotes = { ...previousNotes }

          for (const savedCard of savedCardsBatch) {
            nextNotes[savedCard.id] = []
          }

          return nextNotes
        })
      }
    } finally {
      setIsSavingCard(false)
    }
  }

  function startEditingCard(card: KnowledgeCard) {
    setEditingCardId(card.id)
    setCardEditForm({
      title: card.title,
      summary: card.summary,
      key_points: card.key_points.join('\n'),
      tags: card.tags.join(', '),
      card_kind: card.card_kind,
      content_status: card.content_status,
    })
  }

  async function saveEditedCard(cardId: string) {
    if (!cardEditForm) {
      return
    }

    setIsSavingCard(true)
    setErrorMessage(null)

    try {
      const updatedCard = await fetchJson<KnowledgeCard>(
        `/cards/${cardId}`,
        {
          method: 'PATCH',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            title: cardEditForm.title,
            summary: cardEditForm.summary,
            key_points: cardEditForm.key_points
              .split('\n')
              .map((point) => point.trim())
              .filter(Boolean),
            card_kind: cardEditForm.card_kind,
            tags: parseTags(cardEditForm.tags),
            content_status: cardEditForm.content_status,
          }),
        },
      )

      setSavedCards((previousCards) =>
        sortCardsBySource(
          previousCards.map((card) =>
            card.id === updatedCard.id ? updatedCard : card,
          ),
        ),
      )
      setEditingCardId(null)
      setCardEditForm(null)
      if (selectedRailCard?.id === updatedCard.id) {
        setSelectedRailCard(updatedCard)
      }
      if (job) {
        await loadCourseCardIndex(job.course_id)
      }
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Card update failed.',
      )
    } finally {
      setIsSavingCard(false)
    }
  }

  async function saveRailCard() {
    if (!selectedRailCard || !railCardEditForm) {
      return
    }

    setIsSavingCard(true)
    setErrorMessage(null)

    try {
      const updatedCard = await fetchJson<KnowledgeCard>(
        `/cards/${selectedRailCard.id}`,
        {
          method: 'PATCH',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            title: railCardEditForm.title,
            summary: railCardEditForm.summary,
            key_points: railCardEditForm.key_points
              .split('\n')
              .map((point) => point.trim())
              .filter(Boolean),
            card_kind: railCardEditForm.card_kind,
            tags: parseTags(railCardEditForm.tags),
            content_status: railCardEditForm.content_status,
          }),
        },
      )

      setSelectedRailCard(updatedCard)
      setRailCardEditForm({
        title: updatedCard.title,
        summary: updatedCard.summary,
        key_points: updatedCard.key_points.join('\n'),
        card_kind: updatedCard.card_kind,
        tags: updatedCard.tags.join(', '),
        content_status: updatedCard.content_status,
      })
      setSavedCards((previousCards) =>
        sortCardsBySource(
          previousCards.map((card) =>
            card.id === updatedCard.id ? updatedCard : card,
          ),
        ),
      )
      await loadCourseCardIndex(selectedCourseId)
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Card update failed.',
      )
    } finally {
      setIsSavingCard(false)
    }
  }

  async function deleteSavedCard(cardId: string) {
    setIsSavingCard(true)
    setErrorMessage(null)

    try {
      await fetchJson<void>(
        `/cards/${cardId}`,
        {
          method: 'DELETE',
        },
      )
      setSavedCards((previousCards) =>
        previousCards.filter((card) => card.id !== cardId),
      )
      setCardNotes((previousNotes) => {
        const nextNotes = { ...previousNotes }
        delete nextNotes[cardId]

        return nextNotes
      })
      setNoteForms((previousForms) => {
        const nextForms = { ...previousForms }
        delete nextForms[cardId]

        return nextForms
      })
      if (selectedRailCard?.id === cardId) {
        closeRailCard()
      }
      if (job) {
        await loadCourses(job.course_id)
        await loadCourseCardIndex(job.course_id)
      }
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Card delete failed.',
      )
    } finally {
      setIsSavingCard(false)
    }
  }

  async function deleteAllSavedCardsForJob() {
    if (!job || savedCards.length === 0) {
      return
    }

    const confirmed = window.confirm(
      'Delete all saved cards for this video?',
    )

    if (!confirmed) {
      return
    }

    setIsSavingCard(true)
    setErrorMessage(null)

    try {
      await fetchJson<void>(
        `/jobs/${job.id}/cards`,
        {
          method: 'DELETE',
        },
      )
      setSavedCards([])
      setCardNotes({})
      setNoteForms({})
      if (selectedRailCard?.job_id === job.id) {
        closeRailCard()
      }
      await loadCourses(job.course_id)
      await loadCourseCardIndex(job.course_id)
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : 'Saved cards delete failed.',
      )
    } finally {
      setIsSavingCard(false)
    }
  }

  async function deleteAllSavedCardsForCourse() {
    if (!selectedCourse || selectedCourse.card_count === 0) {
      return
    }

    const confirmed = window.confirm(
      `Delete all saved cards in "${selectedCourse.title}"?`,
    )

    if (!confirmed) {
      return
    }

    setIsSavingCard(true)
    setErrorMessage(null)

    try {
      await fetchJson<void>(
        `/courses/${selectedCourse.id}/cards`,
        {
          method: 'DELETE',
        },
      )

      if (job?.course_id === selectedCourse.id) {
        setSavedCards([])
        setCardNotes({})
        setNoteForms({})
      }
      if (selectedRailCard) {
        closeRailCard()
      }

      await loadCourses(selectedCourse.id)
      await loadCourseCardIndex(selectedCourse.id)
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : 'Course cards delete failed.',
      )
    } finally {
      setIsSavingCard(false)
    }
  }

  async function exportJobCards() {
    if (!job || savedCards.length === 0) {
      return
    }

    setIsExportingCards(true)
    setErrorMessage(null)
    setExportMessage(null)

    try {
      const folder = await exportMarkdownFolder(
        `/jobs/${job.id}/cards/export/markdown/folder`,
      )
      setExportMessage(
        `Exported ${folder.file_count} Markdown files to ${folder.root_path}`,
      )
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Job card export failed.',
      )
    } finally {
      setIsExportingCards(false)
    }
  }

  async function exportAllCards() {
    if (totalSavedCardCount === 0) {
      return
    }

    setIsExportingCards(true)
    setErrorMessage(null)
    setExportMessage(null)

    try {
      const folder = await exportMarkdownFolder(
        '/cards/export/markdown/folder',
      )
      setExportMessage(
        `Exported ${folder.file_count} Markdown files to ${folder.root_path}`,
      )
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Card vault export failed.',
      )
    } finally {
      setIsExportingCards(false)
    }
  }

  function getNoteForm(cardId: string): NoteForm {
    return noteForms[cardId] ?? createDefaultNoteForm()
  }

  function updateNoteForm(
    cardId: string,
    changes: Partial<NoteForm>,
  ) {
    setNoteForms((previousForms) => ({
      ...previousForms,
      [cardId]: {
        ...createDefaultNoteForm(),
        ...previousForms[cardId],
        ...changes,
      },
    }))
  }

  async function saveCardNote(cardId: string) {
    const form = getNoteForm(cardId)

    if (!form.body.trim()) {
      return
    }

    setIsSavingNote(true)
    setErrorMessage(null)

    try {
      const savedNote = await fetchJson<KnowledgeCardNote>(
        `/cards/${cardId}/notes`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            note_type: form.note_type,
            title: form.title || null,
            body: form.body,
            source: 'user',
            sources: [],
          }),
        },
      )

      setCardNotes((previousNotes) => ({
        ...previousNotes,
        [cardId]: [...(previousNotes[cardId] ?? []), savedNote],
      }))
      setNoteForms((previousForms) => ({
        ...previousForms,
        [cardId]: createDefaultNoteForm(),
      }))
      await loadCourseCardIndex(selectedCourseId)
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Note save failed.',
      )
    } finally {
      setIsSavingNote(false)
    }
  }

  function startEditingNote(note: KnowledgeCardNote) {
    setEditingNoteId(note.id)
    setNoteEditForm({
      note_type: note.note_type,
      title: note.title ?? '',
      body: note.body,
    })
  }

  async function saveEditedNote(note: KnowledgeCardNote) {
    if (!noteEditForm || !noteEditForm.body.trim()) {
      return
    }

    setIsSavingNote(true)
    setErrorMessage(null)

    try {
      const updatedNote = await fetchJson<KnowledgeCardNote>(
        `/card-notes/${note.id}`,
        {
          method: 'PATCH',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            note_type: noteEditForm.note_type,
            title: noteEditForm.title || null,
            body: noteEditForm.body,
          }),
        },
      )

      setCardNotes((previousNotes) => ({
        ...previousNotes,
        [note.card_id]: (previousNotes[note.card_id] ?? []).map(
          (existingNote) =>
            existingNote.id === updatedNote.id
              ? updatedNote
              : existingNote,
        ),
      }))
      setEditingNoteId(null)
      setNoteEditForm(null)
      await loadCourseCardIndex(selectedCourseId)
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Note update failed.',
      )
    } finally {
      setIsSavingNote(false)
    }
  }

  async function deleteCardNote(note: KnowledgeCardNote) {
    setIsSavingNote(true)
    setErrorMessage(null)

    try {
      await fetchJson<void>(
        `/card-notes/${note.id}`,
        {
          method: 'DELETE',
        },
      )

      setCardNotes((previousNotes) => ({
        ...previousNotes,
        [note.card_id]: (previousNotes[note.card_id] ?? []).filter(
          (existingNote) => existingNote.id !== note.id,
        ),
      }))
      await loadCourseCardIndex(selectedCourseId)
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Note delete failed.',
      )
    } finally {
      setIsSavingNote(false)
    }
  }

  function updateReviewItemForm(
    cardId: string,
    updates: Partial<ReviewItemForm>,
  ) {
    setReviewItemForms((previousForms) => ({
      ...previousForms,
      [cardId]: {
        ...(previousForms[cardId] ?? createDefaultReviewItemForm()),
        ...updates,
      },
    }))
  }

  function replaceCardReviewItems(cardId: string, items: ReviewItem[]) {
    setSavedCards((previousCards) =>
      previousCards.map((card) =>
        card.id === cardId ? { ...card, review_items: items } : card,
      ),
    )
    setSelectedRailCard((currentCard) =>
      currentCard?.id === cardId
        ? { ...currentCard, review_items: items }
        : currentCard,
    )
  }

  async function saveReviewItem(card: KnowledgeCard) {
    const form = reviewItemForms[card.id] ?? createDefaultReviewItemForm()
    if (!form.prompt.trim() || !form.expected_answer.trim()) {
      return
    }

    setIsSavingReviewItem(true)
    setErrorMessage(null)
    try {
      const item = await fetchJson<ReviewItem>(
        `/cards/${card.id}/review-items`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            item_type: form.item_type,
            prompt: form.prompt,
            expected_answer: form.expected_answer,
            source_claim_ids: card.claims.map((claim) => claim.id),
            source: 'manual',
          }),
        },
      )
      replaceCardReviewItems(card.id, [...card.review_items, item])
      setReviewItemForms((previousForms) => ({
        ...previousForms,
        [card.id]: createDefaultReviewItemForm(),
      }))
      await loadCourseCardIndex(selectedCourseId)
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Review item save failed.',
      )
    } finally {
      setIsSavingReviewItem(false)
    }
  }

  async function deleteReviewItem(card: KnowledgeCard, itemId: string) {
    setIsSavingReviewItem(true)
    setErrorMessage(null)
    try {
      await fetchJson<void>(`/review-items/${itemId}`, { method: 'DELETE' })
      replaceCardReviewItems(
        card.id,
        card.review_items.filter((item) => item.id !== itemId),
      )
      await loadCourseCardIndex(selectedCourseId)
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Review item delete failed.',
      )
    } finally {
      setIsSavingReviewItem(false)
    }
  }

  function renderReviewItems(card: KnowledgeCard) {
    const form = reviewItemForms[card.id] ?? createDefaultReviewItemForm()
    return (
      <section className="review-items-section">
        <div className="notes-heading">
          <strong>Active recall</strong>
          <span>{card.review_items.length}</span>
        </div>
        <div className="review-item-list">
          {card.review_items.map((item) => (
            <article key={item.id} className="review-item">
              <div>
                <span>{item.item_type}</span>
                <span>{item.status}</span>
              </div>
              <strong>{item.prompt}</strong>
              <p>{item.expected_answer}</p>
              <button
                type="button"
                disabled={isSavingReviewItem}
                onClick={() => void deleteReviewItem(card, item.id)}
              >
                Delete
              </button>
            </article>
          ))}
        </div>
        <div className="review-item-form">
          <select
            value={form.item_type}
            onChange={(event) =>
              updateReviewItemForm(card.id, {
                item_type: event.target.value as ReviewItem['item_type'],
              })
            }
          >
            <option value="basic">basic</option>
            <option value="cloze">cloze</option>
            <option value="explain">explain</option>
            <option value="compare">compare</option>
            <option value="apply">apply</option>
          </select>
          <textarea
            value={form.prompt}
            onChange={(event) =>
              updateReviewItemForm(card.id, { prompt: event.target.value })
            }
            placeholder="Recall prompt"
          />
          <textarea
            value={form.expected_answer}
            onChange={(event) =>
              updateReviewItemForm(card.id, {
                expected_answer: event.target.value,
              })
            }
            placeholder="Expected answer"
          />
          <button
            type="button"
            disabled={
              isSavingReviewItem ||
              !form.prompt.trim() ||
              !form.expected_answer.trim()
            }
            onClick={() => void saveReviewItem(card)}
          >
            Add recall item
          </button>
        </div>
      </section>
    )
  }

  function renderCardNotes(card: KnowledgeCard) {
    const notes = cardNotes[card.id] ?? []
    const form = getNoteForm(card.id)

    return (
      <section className="notes-block">
        <div className="notes-heading">
          <div className="claims-title">Notes</div>
          <span>{notes.length}</span>
        </div>
        {notes.length > 0 && (
          <div className="note-list">
            {notes.map((note) => {
              const isEditingNote = editingNoteId === note.id

              return (
                <article className="note-item" key={note.id}>
                  {isEditingNote && noteEditForm ? (
                    <div className="note-form">
                      <input
                        value={noteEditForm.title}
                        onChange={(event) =>
                          setNoteEditForm({
                            ...noteEditForm,
                            title: event.target.value,
                          })
                        }
                      />
                      <select
                        value={noteEditForm.note_type}
                        onChange={(event) =>
                          setNoteEditForm({
                            ...noteEditForm,
                            note_type: event.target.value as
                              KnowledgeCardNoteType,
                          })
                        }
                      >
                        <option value="user_note">user note</option>
                        <option value="llm_explanation">
                          llm explanation
                        </option>
                        <option value="web_tutorial">web tutorial</option>
                        <option value="practice_question">
                          practice question
                        </option>
                      </select>
                      <textarea
                        value={noteEditForm.body}
                        onChange={(event) =>
                          setNoteEditForm({
                            ...noteEditForm,
                            body: event.target.value,
                          })
                        }
                      />
                      <div className="card-actions">
                        <button
                          type="button"
                          disabled={
                            isSavingNote || !noteEditForm.body.trim()
                          }
                          onClick={() => void saveEditedNote(note)}
                        >
                          Save note
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setEditingNoteId(null)
                            setNoteEditForm(null)
                          }}
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className="note-heading">
                        <strong>{note.title ?? note.note_type}</strong>
                        <span>{note.source}</span>
                      </div>
                      <p>{note.body}</p>
                      {note.sources.length > 0 && (
                        <div className="note-sources">
                          {note.sources.map((source, index) => (
                            <span key={`${source.url ?? source.title}-${index}`}>
                              {source.title ?? source.url}
                            </span>
                          ))}
                        </div>
                      )}
                      <div className="card-actions">
                        <button
                          type="button"
                          disabled={isSavingNote}
                          onClick={() => startEditingNote(note)}
                        >
                          Edit note
                        </button>
                        <button
                          type="button"
                          className="danger-button"
                          disabled={isSavingNote}
                          onClick={() => void deleteCardNote(note)}
                        >
                          Delete note
                        </button>
                      </div>
                    </>
                  )}
                </article>
              )
            })}
          </div>
        )}
        <div className="note-form">
          <input
            value={form.title}
            onChange={(event) =>
              updateNoteForm(card.id, {
                title: event.target.value,
              })
            }
            placeholder="Note title"
          />
          <select
            value={form.note_type}
            onChange={(event) =>
              updateNoteForm(card.id, {
                note_type: event.target.value as KnowledgeCardNoteType,
              })
            }
          >
            <option value="user_note">user note</option>
            <option value="llm_explanation">llm explanation</option>
            <option value="web_tutorial">web tutorial</option>
            <option value="practice_question">practice question</option>
          </select>
          <textarea
            value={form.body}
            onChange={(event) =>
              updateNoteForm(card.id, {
                body: event.target.value,
              })
            }
            placeholder="Add a note"
          />
          <div className="card-actions">
            <button
              type="button"
              disabled={isSavingNote || !form.body.trim()}
              onClick={() => void saveCardNote(card.id)}
            >
              Add note
            </button>
          </div>
        </div>
      </section>
    )
  }

  function renderCourseCardRail() {
    const relatedJob = selectedRailCard
      ? jobs.find((item) => item.id === selectedRailCard.job_id)
      : null

    return (
      <aside
        className={[
          'card-rail',
          isCardRailOpen ? 'open' : '',
        ]
          .filter(Boolean)
          .join(' ')}
      >
        <button
          type="button"
          className="card-rail-tab"
          onClick={() => setIsCardRailOpen((isOpen) => !isOpen)}
        >
          Cards {courseCardIndex.length}
        </button>
        <div className="card-rail-content">
          {cardRailTab === 'cards' && (
            <div className="card-rail-header">
              <div>
                <div className="panel-title">Course cards</div>
                <h2>{selectedCourse?.title ?? 'No course'}</h2>
                <p>
                  {filteredCourseCardIndex.length} shown /{' '}
                  {courseCardIndex.length} total
                </p>
              </div>
              <button
                type="button"
                onClick={() => void loadCourseCardIndex(selectedCourseId)}
              >
                {isLoadingCourseCards ? 'Loading' : 'Refresh'}
              </button>
            </div>
          )}
          <div className="card-rail-tabs">
            <button
              type="button"
              className={cardRailTab === 'cards' ? 'selected' : ''}
              onClick={() => setCardRailTab('cards')}
            >
              Cards
            </button>
            <button
              type="button"
              className={cardRailTab === 'ask' ? 'selected' : ''}
              onClick={() => setCardRailTab('ask')}
            >
              Ask
            </button>
          </div>
          {cardRailTab === 'cards' ? (
            <>
              <input
                className="card-rail-search"
                value={cardRailSearch}
                onChange={(event) => setCardRailSearch(event.target.value)}
                placeholder="Search cards"
              />
          <div className="card-rail-filters">
            <select
              value={cardRailReviewFilter}
              onChange={(event) =>
                setCardRailReviewFilter(
                  event.target.value as 'all' | CardContentStatus,
                )
              }
            >
              <option value="all">all states</option>
              <option value="draft">draft</option>
              <option value="reviewed">reviewed</option>
              <option value="needs_fix">needs fix</option>
            </select>
            <select
              value={cardRailNoteFilter}
              onChange={(event) =>
                setCardRailNoteFilter(
                  event.target.value as
                    'all' | 'has_notes' | 'no_notes',
                )
              }
            >
              <option value="all">all notes</option>
              <option value="has_notes">has notes</option>
              <option value="no_notes">no notes</option>
            </select>
            <input
              value={cardRailTagFilter}
              onChange={(event) =>
                setCardRailTagFilter(event.target.value)
              }
              placeholder="Tag filter"
            />
          </div>
          <div className="card-rail-list">
            {filteredCourseCardIndex.length ? (
              filteredCourseCardIndex.map((card) => (
                <button
                  type="button"
                  key={card.id}
                  className={[
                    'card-rail-list-item',
                    selectedRailCard?.id === card.id ? 'selected' : '',
                  ]
                    .filter(Boolean)
                    .join(' ')}
                  onClick={() => void openRailCard(card.id)}
                >
                  <strong className="card-rail-title">
                    {card.title}
                  </strong>
                  <span className="card-rail-summary">
                    {card.summary}
                  </span>
                  <small className="card-rail-meta-line">
                    {card.content_status} · {card.card_kind} ·{' '}
                    {card.source_video ?? 'video'} ·{' '}
                    {formatTime(card.source_start_seconds)} ·{' '}
                    {card.note_count} notes
                  </small>
                  {card.tags.length > 0 && (
                    <div className="tag-row">
                      {card.tags.map((tag) => (
                        <span key={tag}>{tag}</span>
                      ))}
                    </div>
                  )}
                </button>
              ))
            ) : (
              <div className="empty-list">No course cards</div>
            )}
          </div>

          <section className="rail-card-detail">
            <div className="card-rail-header">
              <div>
                <div className="panel-title">Opened card</div>
                <h2>{selectedRailCard?.title ?? 'No card selected'}</h2>
              </div>
              {selectedRailCard && (
                <button type="button" onClick={closeRailCard}>
                  Close
                </button>
              )}
            </div>
            {isLoadingRailCard && (
              <div className="empty-list">Loading card</div>
            )}
            {selectedRailCard && railCardEditForm && (
              <div className="rail-card-file">
                <div className="rail-card-meta">
                  <span>{selectedRailCard.content_status}</span>
                  <span>{selectedRailCard.card_kind}</span>
                  <span>
                    {formatTime(selectedRailCard.source_start_seconds)} -{' '}
                    {formatTime(selectedRailCard.source_end_seconds)}
                  </span>
                </div>
                {selectedRailCard.tags.length > 0 && (
                  <div className="tag-row">
                    {selectedRailCard.tags.map((tag) => (
                      <span key={tag}>{tag}</span>
                    ))}
                  </div>
                )}
                <div className="edit-card-form">
                  <input
                    value={railCardEditForm.title}
                    onChange={(event) =>
                      setRailCardEditForm({
                        ...railCardEditForm,
                        title: event.target.value,
                      })
                    }
                  />
                  <textarea
                    value={railCardEditForm.summary}
                    onChange={(event) =>
                      setRailCardEditForm({
                        ...railCardEditForm,
                        summary: event.target.value,
                      })
                    }
                  />
                  <textarea
                    value={railCardEditForm.key_points}
                    onChange={(event) =>
                      setRailCardEditForm({
                        ...railCardEditForm,
                        key_points: event.target.value,
                      })
                    }
                  />
                  <select
                    value={railCardEditForm.card_kind}
                    onChange={(event) =>
                      setRailCardEditForm({
                        ...railCardEditForm,
                        card_kind: event.target.value as CardKind,
                      })
                    }
                  >
                    <option value="concept">concept</option>
                    <option value="definition">definition</option>
                    <option value="process">process</option>
                    <option value="comparison">comparison</option>
                    <option value="example">example</option>
                    <option value="formula">formula</option>
                  </select>
                  <input
                    value={railCardEditForm.tags}
                    onChange={(event) =>
                      setRailCardEditForm({
                        ...railCardEditForm,
                        tags: event.target.value,
                      })
                    }
                    placeholder="Tags, comma separated"
                  />
                  <select
                    value={railCardEditForm.content_status}
                    onChange={(event) =>
                      setRailCardEditForm({
                        ...railCardEditForm,
                        content_status: event.target.value as
                          CardContentStatus,
                      })
                    }
                  >
                    <option value="draft">draft</option>
                    <option value="reviewed">reviewed</option>
                    <option value="needs_fix">needs fix</option>
                  </select>
                  <div className="card-actions">
                    <button
                      type="button"
                      disabled={isSavingCard}
                      onClick={() => void saveRailCard()}
                    >
                      {isSavingCard ? 'Saving' : 'Save card'}
                    </button>
                    {relatedJob && (
                      <button
                        type="button"
                        onClick={() => void openJob(relatedJob)}
                      >
                        Open video
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => openStudyCard(selectedRailCard.id)}
                    >
                      Study concept
                    </button>
                  </div>
                </div>
                <ClaimsBlock
                  claims={selectedRailCard.claims}
                  unsupportedTerms={selectedRailCard.unsupported_terms}
                  onJumpToTime={jumpToTime}
                />
                {renderReviewItems(selectedRailCard)}
                {renderCardNotes(selectedRailCard)}
              </div>
            )}
          </section>
            </>
          ) : (
            <div className="rail-chat-panel">
              <ChatPanel
                apiBaseUrl={API_BASE_URL}
                courseId={selectedCourseId}
                courseTitle={selectedCourse?.title}
                model={selectedModel}
                compact
              />
            </div>
          )}
        </div>
      </aside>
    )
  }

  useEffect(() => {
    let cancelled = false

    async function bootBackend() {
      setBackendBoot({
        phase: isTauriRuntime() ? 'starting' : 'checking',
        mode: isTauriRuntime() ? 'sidecar' : 'manual',
        message: isTauriRuntime()
          ? 'Starting local backend.'
          : 'Checking local backend.',
      })

      const nextBootState = await ensureBackendReady()

      if (!cancelled) {
        setBackendBoot(nextBootState)
      }
    }

    void bootBackend()

    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    const handlePopState = () => {
      setAppView(getViewFromUrl())
    }

    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [])

  useEffect(() => {
    if (backendBoot.phase !== 'ready') {
      return
    }

    void checkLlmStatus()
    void checkRuntimeStatus()
    void loadCourses()
  }, [backendBoot.phase])

  useEffect(() => {
    if (!selectedCourseId) {
      return
    }

    void loadJobs(selectedCourseId)
    void loadCourseCardIndex(selectedCourseId)
    // Course selection is the workflow trigger; loader identities are not.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedCourseId])

  useEffect(() => {
    const cardId = new URL(window.location.href).searchParams.get('card')

    if (!selectedCourseId || !cardId || selectedRailCard?.id === cardId) {
      return
    }

    void openRailCard(cardId)
    // URL card synchronization intentionally follows course changes only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedCourseId])

  useEffect(() => {
    if (!job || !transcript || !selectedRange) {
      setTranscriptContext(null)
      return
    }

    const firstSegment = transcript.segments[selectedRange.startIndex]
    const lastSegment = transcript.segments[selectedRange.endIndex]

    if (!firstSegment || !lastSegment) {
      setTranscriptContext(null)
      return
    }

    const controller = new AbortController()
    const params = new URLSearchParams({
      start_seconds: String(firstSegment.start_seconds),
      end_seconds: String(lastSegment.end_seconds),
    })

    setIsLoadingContext(true)

    void fetchJson<TranscriptContext>(
      `/jobs/${job.id}/context?${params.toString()}`,
      { signal: controller.signal },
    )
      .then((context) => {
        setTranscriptContext(context)
      })
      .catch((error) => {
        if (error instanceof DOMException && error.name === 'AbortError') {
          return
        }

        setErrorMessage(
          error instanceof Error
            ? error.message
            : 'Context loading failed.',
        )
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setIsLoadingContext(false)
        }
      })

    return () => {
      controller.abort()
    }
  }, [job, selectedRange, transcript])

  useEffect(() => {
    if (!job || !autoGenerationRun || !isAutoGenerationActive(autoGenerationRun)) {
      return
    }

    const activeRun = autoGenerationRun
    const intervalId = window.setInterval(() => {
      void refreshAutoGenerationRun(activeRun.id)
        .then((nextRun) => {
          if (!isAutoGenerationActive(nextRun)) {
            void loadSavedCards(job.id)
            void loadCourses(job.course_id)
            void loadCourseCardIndex(job.course_id)
          }
        })
        .catch((error) => {
          setErrorMessage(
            error instanceof Error
              ? error.message
              : 'Auto generation polling failed.',
          )
        })
    }, 1500)

    return () => window.clearInterval(intervalId)
    // Polling is recreated only when the active run or job changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoGenerationRun, job])

  useEffect(() => {
    if (!job || !runningStatuses.includes(job.status)) {
      return
    }

    const intervalId = window.setInterval(() => {
      void refreshJob(job.id)
        .then((nextJob) => {
          if (nextJob.status === 'completed') {
            void loadJobs(nextJob.course_id)
            void loadCourses(nextJob.course_id)
            void loadCourseCardIndex(nextJob.course_id)
            void loadSavedCards(nextJob.id)
            return loadTranscript(nextJob.id)
          }

          return undefined
        })
        .catch((error) => {
          setErrorMessage(
            error instanceof Error ? error.message : 'Polling failed.',
          )
        })
    }, 1500)

    return () => window.clearInterval(intervalId)
    // Job polling is keyed by the current job and owns its interval cleanup.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job])

  useEffect(() => {
    return () => {
      if (videoUrl) {
        URL.revokeObjectURL(videoUrl)
      }
    }
  }, [videoUrl])

  const canUpload = selectedFile && !isUploading
  const canRun = job?.status === 'uploaded' && !isStarting
  const canRetry = job?.status === 'failed' && !isStarting
  const canGenerateCards =
    selectedSegments.length > 0 && !isDraftingCards && !isLoadingContext
  const canCancelCards = isDraftingCards
  const isAutoGenerating =
    isStartingAutoGeneration || isAutoGenerationActive(autoGenerationRun)
  const canAutoGenerate =
    job?.status === 'completed' &&
    !isAutoGenerating &&
    !isDraftingCards

  if (backendBoot.phase !== 'ready') {
    return (
      <main className="app-shell backend-startup">
        <section className="backend-startup-panel">
          <div>
            <p className="subtle">Video Course Cards</p>
            <h1>
              {backendBoot.phase === 'failed'
                ? 'Local backend unavailable'
                : 'Starting local backend'}
            </h1>
          </div>
          <p>{backendBoot.message}</p>
          <div className="backend-startup-meta">
            <span>mode: {backendBoot.mode}</span>
            <span>api: {API_BASE_URL}</span>
          </div>
          {backendBoot.phase === 'failed' && (
            <button
              type="button"
              onClick={() => {
                setBackendBoot({
                  phase: 'checking',
                  mode: isTauriRuntime() ? 'sidecar' : 'manual',
                  message: 'Retrying local backend.',
                })
                void ensureBackendReady().then(setBackendBoot)
              }}
            >
              Retry
            </button>
          )}
        </section>
      </main>
    )
  }

  return (
    <div className="app-frame">
      <AppSidebar activeView={appView} onChange={changeAppView} />
      {appView === 'workspace' ? (
    <main className="app-shell">
      <header className="top-bar">
        <div>
          <h1>Video Course Cards</h1>
          <p className="subtle">Local video transcription workspace</p>
        </div>
        <div className="top-statuses">
          <button
            type="button"
            className={`llm-pill ${
              llmStatus?.available ? 'llm-ready' : 'llm-offline'
            }`}
            onClick={() => void checkLlmStatus()}
          >
            {isCheckingLlm
                ? 'checking model'
                : llmStatus
                ? `${selectedModel || llmStatus.model} ${
                    llmStatus.available ? 'ready' : 'offline'
                  }`
                : 'model unknown'}
          </button>
          <select
            className="model-select"
            value={selectedModel}
            onChange={(event) => setSelectedModel(event.target.value)}
          >
            {availableModels.length > 0 ? (
              availableModels.map((model) => (
                <option key={model} value={model}>
                  {model}
                </option>
              ))
            ) : (
              <option value={selectedModel || llmStatus?.model || ''}>
                {selectedModel || llmStatus?.model || 'No model loaded'}
              </option>
            )}
          </select>
          <button
            type="button"
            className={`runtime-pill ${
              missingRuntimeDependencies.length === 0
                ? 'runtime-ready'
                : 'runtime-needs-setup'
            }`}
            onClick={() => {
              setIsRuntimePanelOpen((isOpen) => !isOpen)
              if (!runtimeStatus) {
                void checkRuntimeStatus()
              }
            }}
          >
            {isCheckingRuntime
              ? 'checking runtime'
              : missingRuntimeDependencies.length === 0
              ? 'runtime ready'
              : `${missingRuntimeDependencies.length} setup items`}
          </button>
          <div className={`status-pill status-${job?.status ?? 'idle'}`}>
            {job?.status ?? 'idle'}
          </div>
        </div>
      </header>

      <section className="workspace">
        <div className="media-pane">
          {isRuntimePanelOpen && runtimeStatus && (
            <section className="runtime-panel">
              <div className="runtime-panel-header">
                <div>
                  <div className="panel-title">Local runtime</div>
                  <p>
                    Checks local tools used by transcription, card generation,
                    semantic search, and RAG.
                  </p>
                </div>
                <button
                  type="button"
                  disabled={isCheckingRuntime}
                  onClick={() => void checkRuntimeStatus()}
                >
                  {isCheckingRuntime ? 'Checking' : 'Re-check'}
                </button>
              </div>
              <div className="runtime-checklist">
                {runtimeStatus.dependencies.map((dependency) => (
                  <div
                    key={dependency.name}
                    className={[
                      'runtime-check',
                      dependency.available ? 'available' : 'missing',
                    ].join(' ')}
                  >
                    <div>
                      <strong>{dependency.name}</strong>
                      <span>
                        {dependency.available ? 'available' : 'missing'}
                      </span>
                    </div>
                    {dependency.version && (
                      <p>{dependency.version}</p>
                    )}
                    {dependency.detail && (
                      <p>{dependency.detail}</p>
                    )}
                    {dependency.required_for.length > 0 && (
                      <small>
                        Used for {dependency.required_for.join(', ')}
                      </small>
                    )}
                    {dependency.install_hint && (
                      <code>{dependency.install_hint}</code>
                    )}
                  </div>
                ))}
              </div>
            </section>
          )}
          <div className="toolbar">
            <div className="current-course-label">
              {selectedCourse?.title ?? 'No course'}
            </div>
            <label className="file-picker">
              <input
                type="file"
                accept="video/*"
                onChange={handleVideoChange}
              />
              Choose video
            </label>
            <button
              type="button"
              disabled={!canUpload}
              onClick={uploadSelectedVideo}
            >
              {isUploading ? 'Uploading' : 'Upload'}
            </button>
            <button
              type="button"
              disabled={!canRun}
              onClick={() => void startProcessing('run')}
            >
              {isStarting ? 'Starting' : 'Run'}
            </button>
            <button
              type="button"
              disabled={!canRetry}
              onClick={() => void startProcessing('retry')}
            >
              Retry
            </button>
          </div>

          <div className="video-stage">
            {videoUrl ? (
              <video
                ref={videoRef}
                src={videoUrl}
                controls
                preload="metadata"
                onTimeUpdate={(event) => {
                  setCurrentTime(event.currentTarget.currentTime)
                }}
              />
            ) : (
              <div className="empty-state">No video selected</div>
            )}
          </div>

          {errorMessage && (
            <div className="error-banner">{errorMessage}</div>
          )}
          {exportMessage && (
            <div className="success-banner">{exportMessage}</div>
          )}

          {job?.status === 'completed' && transcript && (
            <div className="selection-panel">
              <div className="generation-mode-header">
                <div className="panel-title">Card generation</div>
                <div className="generation-mode-toggle">
                  <button
                    type="button"
                    className={
                      generationMode === 'manual' ? 'selected' : ''
                    }
                    onClick={() => setGenerationMode('manual')}
                  >
                    Manual
                  </button>
                  <button
                    type="button"
                    className={generationMode === 'auto' ? 'selected' : ''}
                    onClick={() => setGenerationMode('auto')}
                  >
                    Auto
                  </button>
                </div>
              </div>
              <div className="card-controls">
                <input
                  className="focus-input"
                  value={cardFocus}
                  onChange={(event) => setCardFocus(event.target.value)}
                  placeholder="Optional focus, e.g. exam review or core concept"
                />
                {generationMode === 'manual' ? (
                  <>
                    <button
                      type="button"
                      disabled={!canGenerateCards}
                      onClick={() => void generateCards()}
                    >
                      {isDraftingCards ? 'Generating' : 'Generate from selection'}
                    </button>
                    {isDraftingCards && (
                      <button
                        type="button"
                        disabled={!canCancelCards}
                        onClick={cancelCardGeneration}
                      >
                        Cancel
                      </button>
                    )}
                  </>
                ) : (
                  <button
                    type="button"
                    disabled={!canAutoGenerate}
                    onClick={() => void startAutoGeneration()}
                  >
                    {isAutoGenerating ? 'Generating' : 'Auto generate'}
                  </button>
                )}
              </div>

              {generationMode === 'manual' && (
                selectedRange && selectedSegments.length > 0 ? (
                  <>
                    <p>
                      {formatTime(selectedSegments[0].start_seconds)} -{' '}
                      {formatTime(
                        selectedSegments[
                          selectedSegments.length - 1
                        ].end_seconds,
                      )}
                    </p>
                    <p className="context-text">
                      {isLoadingContext
                        ? 'Loading context'
                        : transcriptContext?.text ||
                          selectedSegments
                            .map((segment) => segment.text)
                            .join('\n')}
                    </p>
                  </>
                ) : (
                  <div className="empty-list">No transcript selection</div>
                )
              )}

              {generationMode === 'manual' && generationStatus && (
                <div className="generation-status">
                  <span>{generationStatus}</span>
                  {isDraftingCards && (
                    <small>Local models can take a while on CPU.</small>
                  )}
                </div>
              )}

              {generationMode === 'auto' && autoGenerationRun && (
                <div className="generation-status">
                  <span>{autoGenerationStatusText(autoGenerationRun)}</span>
                  <small>
                    {autoGenerationRun.completed_chunks} /{' '}
                    {autoGenerationRun.total_chunks} chunks ·{' '}
                    {autoGenerationRun.succeeded_chunks} succeeded ·{' '}
                    {autoGenerationRun.failed_chunks} failed
                  </small>
                  {autoGenerationRun.errors.length > 0 && (
                    <small>
                      Latest issue:{' '}
                      {
                        autoGenerationRun.errors[
                          autoGenerationRun.errors.length - 1
                        ].message
                      }
                    </small>
                  )}
                </div>
              )}
            </div>
          )}

          {cardDraft && (
            <section className="cards-panel">
              <div className="card-panel-heading">
                <div>
                  <div className="panel-title">
                    Draft cards - {cardDraft.model}
                  </div>
                  <div className="panel-subtitle">
                    {unsavedDraftCards.length} unsaved /{' '}
                    {cardDraft.cards.length} total
                  </div>
                </div>
                <button
                  type="button"
                  disabled={
                    isSavingCard || unsavedDraftCards.length === 0
                  }
                  onClick={() => void saveAllDraftCards()}
                >
                  {isSavingCard ? 'Saving' : 'Save all'}
                </button>
              </div>
              <div className="generation-metadata">
                <div>
                  <span>Elapsed</span>
                  <strong>
                    {formatElapsed(
                      cardDraft.generation_metadata.elapsed_seconds,
                    )}
                  </strong>
                </div>
                <div>
                  <span>Context</span>
                  <strong>
                    {cardDraft.generation_metadata.selected_segments_count}{' '}
                    segments /{' '}
                    {
                      cardDraft.generation_metadata
                        .selected_context_characters
                    }{' '}
                    chars
                  </strong>
                </div>
                <div>
                  <span>Cards</span>
                  <strong>
                    {cardDraft.generation_metadata.returned_card_count} /{' '}
                    {cardDraft.generation_metadata.requested_card_count}
                  </strong>
                </div>
                <div>
                  <span>Claims</span>
                  <strong>
                    {cardDraft.generation_metadata.grounded_claim_count}{' '}
                    grounded
                  </strong>
                </div>
                <div>
                  <span>Dropped</span>
                  <strong>
                    {cardDraft.generation_metadata.dropped_claim_count}
                  </strong>
                </div>
              </div>
              <div className="card-list">
                {cardDraft.cards.map((card, index) => {
                  const isSaved = savedCardSignatures.has(
                    cardSignature(card),
                  )

                  return (
                    <article
                      className="knowledge-card"
                      key={`${card.title}-${index}`}
                    >
                      <div className="card-heading">
                        <h3>{card.title}</h3>
                        <div className="card-badges">
                          <span className={`card-status ${
                            isSaved ? 'saved' : 'unsaved'
                          }`}
                          >
                            {isSaved ? 'Saved' : 'Unsaved'}
                          </span>
                        </div>
                      </div>
                      <p>{card.summary}</p>
                      <ul>
                        {card.key_points.map((point) => (
                          <li key={point}>{point}</li>
                        ))}
                      </ul>
                      <ClaimsBlock
                        claims={card.claims}
                        unsupportedTerms={card.unsupported_terms}
                        onJumpToTime={jumpToTime}
                      />
                      <div className="qa-block">
                        <strong>{card.question}</strong>
                        <p>{card.answer}</p>
                      </div>
                      <button
                        type="button"
                        className="source-range source-jump"
                        onClick={() => jumpToTime(card.source_start_seconds)}
                      >
                        {formatTime(card.source_start_seconds)} -{' '}
                        {formatTime(card.source_end_seconds)}
                      </button>
                      <div className="card-actions">
                        <button
                          type="button"
                          disabled={isSavingCard || isSaved}
                          onClick={() => void saveDraftCard(card)}
                        >
                          {isSaved ? 'Saved' : 'Save'}
                        </button>
                      </div>
                    </article>
                  )
                })}
              </div>
            </section>
          )}

          <section className="cards-panel">
            <div className="card-panel-heading">
              <div>
                <div className="panel-title">Saved cards</div>
                <div className="panel-subtitle">
                  {savedCards.length} cards sorted by source time
                </div>
              </div>
              <div className="panel-actions">
                <button
                  type="button"
                  disabled={
                    isExportingCards || savedCards.length === 0
                  }
                  onClick={() => void exportJobCards()}
                >
                  {isExportingCards ? 'Exporting' : 'Export job'}
                </button>
                <button
                  type="button"
                  className="danger-button"
                  disabled={isSavingCard || savedCards.length === 0}
                  onClick={() => void deleteAllSavedCardsForJob()}
                >
                  Delete all
                </button>
              </div>
            </div>
            <div className="card-list">
              {savedCards.length ? (
                savedCards.map((card) => {
                  const isEditing = editingCardId === card.id

                  return (
                    <article className="knowledge-card" key={card.id}>
                      {isEditing && cardEditForm ? (
                        <div className="edit-card-form">
                          <input
                            value={cardEditForm.title}
                            onChange={(event) =>
                              setCardEditForm({
                                ...cardEditForm,
                                title: event.target.value,
                              })
                            }
                          />
                          <textarea
                            value={cardEditForm.summary}
                            onChange={(event) =>
                              setCardEditForm({
                                ...cardEditForm,
                                summary: event.target.value,
                              })
                            }
                          />
                          <textarea
                            value={cardEditForm.key_points}
                            onChange={(event) =>
                              setCardEditForm({
                                ...cardEditForm,
                                key_points: event.target.value,
                              })
                            }
                          />
                          <select
                            value={cardEditForm.card_kind}
                            onChange={(event) =>
                              setCardEditForm({
                                ...cardEditForm,
                                card_kind: event.target.value as CardKind,
                              })
                            }
                          >
                            <option value="concept">concept</option>
                            <option value="definition">definition</option>
                            <option value="process">process</option>
                            <option value="comparison">comparison</option>
                            <option value="example">example</option>
                            <option value="formula">formula</option>
                          </select>
                          <input
                            value={cardEditForm.tags}
                            onChange={(event) =>
                              setCardEditForm({
                                ...cardEditForm,
                                tags: event.target.value,
                              })
                            }
                            placeholder="Tags, comma separated"
                          />
                          <select
                            value={cardEditForm.content_status}
                            onChange={(event) =>
                              setCardEditForm({
                                ...cardEditForm,
                                content_status: event.target.value as
                                  CardContentStatus,
                              })
                            }
                          >
                            <option value="draft">draft</option>
                            <option value="reviewed">reviewed</option>
                            <option value="needs_fix">needs fix</option>
                          </select>
                          <div className="card-actions">
                            <button
                              type="button"
                              onClick={() => openStudyCard(card.id)}
                            >
                              Study concept
                            </button>
                            <button
                              type="button"
                              disabled={isSavingCard}
                              onClick={() => void saveEditedCard(card.id)}
                            >
                              Save edit
                            </button>
                            <button
                              type="button"
                              onClick={() => {
                                setEditingCardId(null)
                                setCardEditForm(null)
                              }}
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : (
                        <>
                          <div className="card-heading">
                            <h3>{card.title}</h3>
                            <div className="card-badges">
                              <span className="card-status saved">
                                Saved
                              </span>
                              <span>{card.content_status}</span>
                              <span>{card.card_kind}</span>
                            </div>
                          </div>
                          {card.tags.length > 0 && (
                            <div className="tag-row">
                              {card.tags.map((tag) => (
                                <span key={tag}>{tag}</span>
                              ))}
                            </div>
                          )}
                          <p>{card.summary}</p>
                          <ul>
                            {card.key_points.map((point) => (
                              <li key={point}>{point}</li>
                            ))}
                          </ul>
                          <ClaimsBlock
                            claims={card.claims}
                            unsupportedTerms={card.unsupported_terms}
                            onJumpToTime={jumpToTime}
                          />
                          {renderReviewItems(card)}
                          <button
                            type="button"
                            className="source-range source-jump"
                            onClick={() =>
                              jumpToTime(card.source_start_seconds)
                            }
                          >
                            {formatTime(card.source_start_seconds)} -{' '}
                            {formatTime(card.source_end_seconds)}
                          </button>
                          {renderCardNotes(card)}
                          <div className="card-actions">
                            <button
                              type="button"
                              onClick={() => startEditingCard(card)}
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              disabled={isSavingCard}
                              onClick={() => void deleteSavedCard(card.id)}
                            >
                              Delete
                            </button>
                          </div>
                        </>
                      )}
                    </article>
                  )
                })
              ) : (
                <div className="empty-list">No saved cards</div>
              )}
            </div>
          </section>
        </div>

        <aside className="side-pane">
          <section className="courses-panel">
            <div className="panel-heading-row">
              <h2>Courses</h2>
              <button type="button" onClick={() => void loadCourses()}>
                {isLoadingCourses ? 'Loading' : 'Refresh'}
              </button>
            </div>
            <div className="course-create-row">
              <input
                value={newCourseTitle}
                onChange={(event) => setNewCourseTitle(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    void createCourse()
                  }
                }}
                placeholder="New course"
              />
              <button
                type="button"
                disabled={isSavingCourse || !newCourseTitle.trim()}
                onClick={() => void createCourse()}
              >
                New
              </button>
            </div>
            <div className="course-list">
              {courses.length ? (
                courses.map((course) => {
                  const isRenaming = renamingCourseId === course.id

                  return (
                    <div
                      key={course.id}
                      className={[
                        'course-list-row',
                        course.id === selectedCourseId ? 'selected' : '',
                        isRenaming ? 'renaming' : '',
                      ]
                        .filter(Boolean)
                        .join(' ')}
                    >
                      {isRenaming ? (
                        <div className="course-rename-form">
                          <input
                            value={courseRenameTitle}
                            autoFocus
                            disabled={isSavingCourse}
                            aria-label="Course name"
                            onChange={(event) =>
                              setCourseRenameTitle(event.target.value)
                            }
                            onKeyDown={(event) => {
                              if (event.key === 'Enter') {
                                void renameCourse(course)
                              }

                              if (event.key === 'Escape') {
                                cancelRenamingCourse()
                              }
                            }}
                          />
                          <div className="course-rename-actions">
                            <button
                              type="button"
                              disabled={
                                isSavingCourse || !courseRenameTitle.trim()
                              }
                              onClick={() => void renameCourse(course)}
                            >
                              Save
                            </button>
                            <button
                              type="button"
                              disabled={isSavingCourse}
                              onClick={cancelRenamingCourse}
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : (
                        <>
                          <button
                            type="button"
                            className="course-list-item"
                            onClick={() => selectCourse(course.id)}
                          >
                            <span>{course.title}</span>
                            <small>
                              {course.job_count} videos / {course.card_count}{' '}
                              cards
                            </small>
                          </button>
                          <div className="course-row-actions">
                            <button
                              type="button"
                              disabled={isSavingCourse}
                              onClick={() => startRenamingCourse(course)}
                            >
                              Rename
                            </button>
                            {course.id !== DEFAULT_COURSE_ID && (
                              <button
                                type="button"
                                className="danger-button"
                                disabled={isSavingCourse}
                                onClick={() => void deleteCourse(course)}
                              >
                                Delete
                              </button>
                            )}
                          </div>
                        </>
                      )}
                    </div>
                  )
                })
              ) : (
                <div className="empty-list">No courses</div>
              )}
            </div>
            <div className="course-card-actions">
              <button
                type="button"
                className="course-clear-button"
                disabled={
                  isExportingCards || totalSavedCardCount === 0
                }
                onClick={() => void exportAllCards()}
              >
                {isExportingCards ? 'Exporting' : 'Export all cards'}
              </button>
              <button
                type="button"
                className="danger-button course-clear-button"
                disabled={
                  isSavingCard ||
                  !selectedCourse ||
                  selectedCourse.card_count === 0
                }
                onClick={() => void deleteAllSavedCardsForCourse()}
              >
                Delete course cards
              </button>
            </div>
          </section>
          <section className="jobs-panel">
            <div className="panel-heading-row">
              <h2>Videos</h2>
              <button
                type="button"
                onClick={() => void loadJobs(selectedCourseId)}
              >
                {isLoadingJobs ? 'Loading' : 'Refresh'}
              </button>
            </div>
            <div className="job-list">
              {jobs.length ? (
                jobs.map((item) => (
                  <div
                    key={item.id}
                    className={[
                      'job-list-row',
                      item.id === job?.id ? 'selected' : '',
                    ]
                      .filter(Boolean)
                      .join(' ')}
                  >
                    <button
                      type="button"
                      className="job-list-item"
                      onClick={() => void openJob(item)}
                    >
                      <span>{item.original_filename ?? item.stored_name ?? item.id}</span>
                      <small>{item.status}</small>
                    </button>
                    <button
                      type="button"
                      className="danger-button"
                      disabled={isDeletingJob}
                      onClick={() => void deleteVideoJob(item)}
                    >
                      Delete
                    </button>
                  </div>
                ))
              ) : (
                <div className="empty-list">No uploaded videos</div>
              )}
            </div>
          </section>
          <section className="job-panel">
            <h2>Job</h2>
            <dl>
              <div>
                <dt>ID</dt>
                <dd>{job?.id ?? '-'}</dd>
              </div>
              <div>
                <dt>File</dt>
                <dd>{job?.original_filename ?? selectedFile?.name ?? '-'}</dd>
              </div>
              <div>
                <dt>Size</dt>
                <dd>{formatSize(job?.size_bytes ?? selectedFile?.size ?? null)}</dd>
              </div>
              <div>
                <dt>Updated</dt>
                <dd>{job?.updated_at ? new Date(job.updated_at).toLocaleTimeString() : '-'}</dd>
              </div>
            </dl>
            {job?.metadata && (
              <div className="metadata-row">
                {job.metadata.width}x{job.metadata.height} |{' '}
                {formatTime(job.metadata.duration_seconds)} |{' '}
                {job.metadata.video_codec}
              </div>
            )}
            {job?.error_message && (
              <div className="error-text">{job.error_message}</div>
            )}
          </section>

          <section className="transcript-panel">
            <h2>Transcript</h2>
            <div className="segment-list">
              {transcript?.segments.length ? (
                transcript.segments.map((segment, index) => {
                  const isInSelectedRange =
                    selectedRange !== null &&
                    index >= selectedRange.startIndex &&
                    index <= selectedRange.endIndex
                  const className = [
                    'segment',
                    index === activeSegmentIndex ? 'active' : '',
                    isInSelectedRange ? 'selected' : '',
                  ]
                    .filter(Boolean)
                    .join(' ')

                  return (
                    <button
                      type="button"
                      key={`${segment.start_seconds}-${index}`}
                      className={className}
                      onClick={(event) => selectSegment(segment, index, event)}
                    >
                      <span>{formatTime(segment.start_seconds)}</span>
                      <p>{segment.text}</p>
                    </button>
                  )
                })
              ) : (
                <div className="empty-list">Transcript unavailable</div>
              )}
            </div>
          </section>
        </aside>
      </section>
      {renderCourseCardRail()}
    </main>
      ) : appView === 'course-map' ? (
        <main className="course-map-shell">
          <CourseMapView
            apiBaseUrl={API_BASE_URL}
            courses={courses}
            selectedCourseId={selectedCourseId}
            selectedModel={selectedModel}
            initialCardId={
              selectedRailCard?.id ??
              new URL(window.location.href).searchParams.get('card')
            }
            onSelectCourse={selectCourse}
            onOpenWorkspaceCard={openWorkspaceCard}
            onOpenStudyCard={openStudyCard}
          />
        </main>
      ) : appView === 'study' ? (
        <main className="study-shell">
          <Suspense fallback={<div className="study-empty">Loading study workspace</div>}>
            <StudyView
              apiBaseUrl={API_BASE_URL}
              courses={courses}
              selectedCourseId={selectedCourseId}
              selectedModel={selectedModel}
              initialCardId={new URL(window.location.href).searchParams.get('card')}
              initialDocumentId={new URL(window.location.href).searchParams.get('document')}
              onSelectCourse={selectCourse}
            />
          </Suspense>
        </main>
      ) : appView === 'review' ? (
        <main className="review-shell">
          <ReviewView
            apiBaseUrl={API_BASE_URL}
            courses={courses}
            selectedCourseId={selectedCourseId}
            onSelectCourse={selectCourse}
            onOpenWorkspaceCard={openWorkspaceCard}
          />
        </main>
      ) : (
        <main className="graph-shell">
          <GraphView
            apiBaseUrl={API_BASE_URL}
            courses={courses}
            selectedCourseId={selectedCourseId}
            selectedModel={selectedModel}
            initialCardId={
              selectedRailCard?.id ??
              new URL(window.location.href).searchParams.get('card')
            }
            onSelectCourse={selectCourse}
            onOpenWorkspaceCard={openWorkspaceCard}
          />
        </main>
      )}
    </div>
  )
}

export default App
