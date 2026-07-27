import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import ReactMarkdown from 'react-markdown'
import {
  BookOpenText,
  Eye,
  FilePlus2,
  History,
  Link2,
  Pencil,
  Save,
  Sparkles,
  Trash2,
} from 'lucide-react'
import type {
  LearningDocument,
  LearningDocumentDetail,
  LearningDocumentGenerationResult,
  LearningDocumentSource,
  SourceAsset,
  StudyCard,
  StudyCourse,
} from './studyTypes'
import {
  announceTrashCreated,
  cancelReliableTask,
  enqueueLearningDocumentGeneration,
  retryReliableTask,
  SaveStatus,
  useAutosavedDraft,
  waitForReliableTask,
  type ReliableTask,
} from './features/reliability'


type StudyEditorDraft = {
  title: string
  summary: string
  body_markdown: string
  status: LearningDocument['status']
  focus: string
  selected_asset_ids: string[]
  supporting_card_ids: string[]
}

type LearningDocumentGenerationTaskResult = {
  generation: LearningDocumentGenerationResult
}

type FailedGenerationTask = {
  taskId: string
  documentId: string
  courseId: string
}

type StudyViewProps = {
  apiBaseUrl: string
  courses: StudyCourse[]
  selectedCourseId: string | null
  selectedModel: string
  showCourseSelector?: boolean
  initialCardId: string | null
  initialDocumentId: string | null
  onSelectCourse: (courseId: string) => void
  onManageSources?: () => void
  onDocumentRouteChange?: (
    documentId: string | null,
    cardId: string | null,
    mode: 'push' | 'replace',
  ) => void
}


async function fetchJson<T>(
  apiBaseUrl: string,
  path: string,
  options?: RequestInit,
): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, options)
  if (!response.ok) {
    let message = `HTTP ${response.status}`
    try {
      const payload = await response.json()
      if (typeof payload.detail === 'string') message = payload.detail
    } catch {
      // Keep status fallback.
    }
    throw new Error(message)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}


function sourceLocation(source: LearningDocumentSource): string {
  const locator = source.locator
  if (typeof locator.slide_number === 'number') return `Slide ${locator.slide_number}`
  if (typeof locator.page_number === 'number') return `Page ${locator.page_number}`
  if (typeof locator.paragraph_number === 'number') return `Paragraph ${locator.paragraph_number}`
  if (typeof locator.start_seconds === 'number') {
    const minutes = Math.floor(locator.start_seconds / 60)
    const seconds = Math.floor(locator.start_seconds % 60).toString().padStart(2, '0')
    return `${minutes}:${seconds}`
  }
  return source.source_type === 'card_claim' ? 'Card claim' : 'Source excerpt'
}


function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError'
}


const EMPTY_STUDY_CARDS: StudyCard[] = []
const EMPTY_STUDY_DOCUMENTS: LearningDocument[] = []
const EMPTY_SOURCE_ASSETS: SourceAsset[] = []


export function StudyView({
  apiBaseUrl,
  courses,
  selectedCourseId,
  selectedModel,
  showCourseSelector = true,
  initialCardId,
  initialDocumentId,
  onSelectCourse,
  onManageSources,
  onDocumentRouteChange,
}: StudyViewProps) {
  const [cards, setCards] = useState<StudyCard[]>([])
  const [documents, setDocuments] = useState<LearningDocument[]>([])
  const [assets, setAssets] = useState<SourceAsset[]>([])
  const [libraryCourseId, setLibraryCourseId] =
    useState<string | null>(null)
  const [selectedCardId, setSelectedCardId] = useState(initialCardId ?? '')
  const [selectedDocumentId, setSelectedDocumentId] = useState(initialDocumentId ?? '')
  const [document, setDocument] = useState<LearningDocumentDetail | null>(null)
  const [title, setTitle] = useState('')
  const [summary, setSummary] = useState('')
  const [body, setBody] = useState('')
  const [status, setStatus] = useState<LearningDocument['status']>('draft')
  const [mode, setMode] = useState<'edit' | 'preview'>('preview')
  const [selectedAssetIds, setSelectedAssetIds] = useState<Set<string>>(new Set())
  const [supportingCardIds, setSupportingCardIds] = useState<Set<string>>(new Set())
  const [focus, setFocus] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [isGenerating, setIsGenerating] = useState(false)
  const [generationTask, setGenerationTask] =
    useState<ReliableTask<LearningDocumentGenerationTaskResult> | null>(
      null,
    )
  const [failedGenerationTask, setFailedGenerationTask] =
    useState<FailedGenerationTask | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const activeCourseIdRef = useRef(selectedCourseId)
  const libraryRequestEpochRef = useRef(0)
  const libraryRequestControllerRef =
    useRef<AbortController | null>(null)
  const documentRequestEpochRef = useRef(0)
  const documentRequestControllerRef =
    useRef<AbortController | null>(null)
  const generationRequestControllerRef =
    useRef<AbortController | null>(null)
  const generationSequenceRef = useRef(0)
  const activeDocumentIdRef = useRef(selectedDocumentId)
  const onDocumentRouteChangeRef =
    useRef(onDocumentRouteChange)

  useEffect(() => {
    activeCourseIdRef.current = selectedCourseId
  }, [selectedCourseId])

  useEffect(() => {
    activeDocumentIdRef.current = selectedDocumentId
  }, [selectedDocumentId])

  useEffect(() => {
    onDocumentRouteChangeRef.current =
      onDocumentRouteChange
  }, [onDocumentRouteChange])

  useEffect(() => {
    generationSequenceRef.current += 1
    generationRequestControllerRef.current?.abort()
    generationRequestControllerRef.current = null
    setGenerationTask(null)
    setFailedGenerationTask(null)
    setIsGenerating(false)
  }, [selectedCourseId, selectedDocumentId])

  const studyDraftValue = useMemo<StudyEditorDraft>(
    () => ({
      title,
      summary,
      body_markdown: body,
      status,
      focus,
      selected_asset_ids: [...selectedAssetIds].sort(),
      supporting_card_ids: [...supportingCardIds].sort(),
    }),
    [
      body,
      focus,
      selectedAssetIds,
      status,
      summary,
      supportingCardIds,
      title,
    ],
  )
  const studyDraftInitialValue = useMemo<StudyEditorDraft>(
    () => ({
      title: document?.title ?? '',
      summary: document?.summary ?? '',
      body_markdown: document?.body_markdown ?? '',
      status: document?.status ?? 'draft',
      focus: '',
      selected_asset_ids: [],
      supporting_card_ids: (
        document?.card_links
          .filter((link) => link.role !== 'primary_anchor')
          .map((link) => link.card_id) ?? []
      ).sort(),
    }),
    [document],
  )
  const restoreStudyDraft = useCallback(
    (payload: StudyEditorDraft) => {
      setTitle(payload.title)
      setSummary(payload.summary)
      setBody(payload.body_markdown)
      setStatus(payload.status)
      setFocus(payload.focus)
      setSelectedAssetIds(new Set(payload.selected_asset_ids))
      setSupportingCardIds(new Set(payload.supporting_card_ids))
      setMode('edit')
      setMessage('Recovered an automatically saved draft.')
    },
    [],
  )
  const studyDraft = useAutosavedDraft({
    apiBaseUrl,
    draftId: `study-document:${selectedDocumentId || 'none'}`,
    courseId: document?.course_id ?? null,
    draftType: 'study_document',
    entityId: document?.id ?? null,
    baseUpdatedAt: document?.updated_at ?? null,
    enabled:
      Boolean(document) &&
      document?.id === selectedDocumentId &&
      document?.course_id === selectedCourseId,
    value: studyDraftValue,
    initialValue: studyDraftInitialValue,
    onRestore: restoreStudyDraft,
  })

  const loadLibrary = useCallback(async () => {
    const courseId = selectedCourseId
    libraryRequestControllerRef.current?.abort()
    const requestEpoch = ++libraryRequestEpochRef.current

    if (
      !courseId ||
      activeCourseIdRef.current !== courseId
    ) {
      setIsLoading(false)
      return
    }

    const controller = new AbortController()
    libraryRequestControllerRef.current = controller
    setIsLoading(true)
    setError(null)
    try {
      const [nextCards, nextDocuments, nextAssets] = await Promise.all([
        fetchJson<StudyCard[]>(
          apiBaseUrl,
          `/courses/${courseId}/card-index`,
          { signal: controller.signal },
        ),
        fetchJson<LearningDocument[]>(
          apiBaseUrl,
          `/courses/${courseId}/learning-documents`,
          { signal: controller.signal },
        ),
        fetchJson<SourceAsset[]>(
          apiBaseUrl,
          `/courses/${courseId}/source-assets`,
          { signal: controller.signal },
        ),
      ])
      if (
        controller.signal.aborted ||
        requestEpoch !== libraryRequestEpochRef.current ||
        activeCourseIdRef.current !== courseId
      ) {
        return
      }
      setLibraryCourseId(courseId)
      setCards(nextCards)
      setDocuments(nextDocuments)
      setAssets(nextAssets)
      const initialCardIsValid =
        Boolean(initialCardId) &&
        nextCards.some((card) => card.id === initialCardId)
      const initialDocumentIsValid =
        Boolean(initialDocumentId) &&
        nextDocuments.some(
          (item) => item.id === initialDocumentId,
        )
      if (initialCardId && !initialCardIsValid) {
        onDocumentRouteChangeRef.current?.(
          initialDocumentIsValid ? initialDocumentId : null,
          null,
          'replace',
        )
      } else if (
        initialDocumentId &&
        !initialDocumentIsValid
      ) {
        onDocumentRouteChangeRef.current?.(
          null,
          initialCardIsValid ? initialCardId : null,
          'replace',
        )
      }
      setSelectedCardId((current) => {
        if (initialCardId && nextCards.some((card) => card.id === initialCardId)) {
          return initialCardId
        }
        if (current && nextCards.some((card) => card.id === current)) return current
        return nextCards[0]?.id ?? ''
      })
      setSelectedDocumentId((current) => {
        if (
          initialDocumentId &&
          nextDocuments.some((item) => item.id === initialDocumentId)
        ) return initialDocumentId
        if (current && nextDocuments.some((item) => item.id === current)) return current
        return nextDocuments[0]?.id ?? ''
      })
    } catch (loadError) {
      if (
        controller.signal.aborted ||
        requestEpoch !== libraryRequestEpochRef.current ||
        activeCourseIdRef.current !== courseId ||
        isAbortError(loadError)
      ) {
        return
      }
      setError(loadError instanceof Error ? loadError.message : 'Study library failed.')
    } finally {
      if (
        requestEpoch === libraryRequestEpochRef.current &&
        activeCourseIdRef.current === courseId
      ) {
        if (libraryRequestControllerRef.current === controller) {
          libraryRequestControllerRef.current = null
        }
        setIsLoading(false)
      }
    }
  }, [
    apiBaseUrl,
    initialCardId,
    initialDocumentId,
    selectedCourseId,
  ])

  useEffect(() => {
    libraryRequestControllerRef.current?.abort()
    libraryRequestControllerRef.current = null
    libraryRequestEpochRef.current += 1
    documentRequestControllerRef.current?.abort()
    documentRequestControllerRef.current = null
    documentRequestEpochRef.current += 1
    generationSequenceRef.current += 1
    generationRequestControllerRef.current?.abort()
    generationRequestControllerRef.current = null

    setCards([])
    setDocuments([])
    setAssets([])
    setLibraryCourseId(null)
    setSelectedCardId('')
    setSelectedDocumentId('')
    setDocument(null)
    setTitle('')
    setSummary('')
    setBody('')
    setStatus('draft')
    setMode('preview')
    setSelectedAssetIds(new Set())
    setSupportingCardIds(new Set())
    setFocus('')
    setError(null)
    setMessage(null)
    setIsLoading(Boolean(selectedCourseId))
    setIsSaving(false)
    setIsGenerating(false)
    setGenerationTask(null)
    setFailedGenerationTask(null)

    void loadLibrary()

    return () => {
      libraryRequestEpochRef.current += 1
      libraryRequestControllerRef.current?.abort()
      libraryRequestControllerRef.current = null
      documentRequestEpochRef.current += 1
      documentRequestControllerRef.current?.abort()
      documentRequestControllerRef.current = null
      generationSequenceRef.current += 1
      generationRequestControllerRef.current?.abort()
      generationRequestControllerRef.current = null
    }
  }, [loadLibrary, selectedCourseId])

  useEffect(() => {
    documentRequestControllerRef.current?.abort()
    const requestEpoch = ++documentRequestEpochRef.current
    const courseId = selectedCourseId
    const belongsToCourse = documents.some(
      (item) =>
        item.id === selectedDocumentId &&
        item.course_id === courseId,
    )

    setDocument(null)
    setTitle('')
    setSummary('')
    setBody('')
    setStatus('draft')
    setSupportingCardIds(new Set())

    if (!courseId || !selectedDocumentId || !belongsToCourse) {
      documentRequestControllerRef.current = null
      setDocument(null)
      return
    }

    const controller = new AbortController()
    documentRequestControllerRef.current = controller
    void fetchJson<LearningDocumentDetail>(
      apiBaseUrl,
      `/learning-documents/${selectedDocumentId}`,
      { signal: controller.signal },
    ).then((detail) => {
      if (
        controller.signal.aborted ||
        requestEpoch !== documentRequestEpochRef.current ||
        activeCourseIdRef.current !== courseId ||
        detail.course_id !== courseId
      ) {
        return
      }
      setDocument(detail)
      setTitle(detail.title)
      setSummary(detail.summary)
      setBody(detail.body_markdown)
      setStatus(detail.status)
      const primary = detail.card_links.find((link) => link.role === 'primary_anchor')
      if (primary) setSelectedCardId(primary.card_id)
      setSupportingCardIds(
        new Set(
          detail.card_links
            .filter((link) => link.role !== 'primary_anchor')
            .map((link) => link.card_id),
        ),
      )
      onDocumentRouteChangeRef.current?.(
        detail.id,
        primary?.card_id ?? null,
        'replace',
      )
    }).catch((loadError) => {
      if (
        controller.signal.aborted ||
        requestEpoch !== documentRequestEpochRef.current ||
        activeCourseIdRef.current !== courseId ||
        isAbortError(loadError)
      ) {
        return
      }
      setError(loadError instanceof Error ? loadError.message : 'Document failed.')
    })
    return () => {
      if (requestEpoch === documentRequestEpochRef.current) {
        documentRequestEpochRef.current += 1
      }
      controller.abort()
      if (documentRequestControllerRef.current === controller) {
        documentRequestControllerRef.current = null
      }
    }
  }, [
    apiBaseUrl,
    documents,
    selectedCourseId,
    selectedDocumentId,
  ])

  const hasCurrentLibrary =
    Boolean(selectedCourseId) &&
    libraryCourseId === selectedCourseId
  const scopedCards = hasCurrentLibrary
    ? cards
    : EMPTY_STUDY_CARDS
  const scopedDocuments = hasCurrentLibrary
    ? documents
    : EMPTY_STUDY_DOCUMENTS
  const scopedAssets = hasCurrentLibrary
    ? assets
    : EMPTY_SOURCE_ASSETS
  const scopedDocument =
    document?.course_id === selectedCourseId ? document : null
  const primaryCard =
    scopedCards.find((card) => card.id === selectedCardId) ??
    null
  const readyAssets = scopedAssets.filter(
    (asset) => asset.extraction_status === 'ready',
  )
  const supportCandidates = useMemo(
    () =>
      scopedCards.filter(
        (card) => card.id !== selectedCardId,
      ),
    [scopedCards, selectedCardId],
  )

  async function createDocument() {
    const courseId = selectedCourseId
    if (!courseId || !selectedCardId) return
    setIsSaving(true)
    setError(null)
    try {
      const created = await fetchJson<LearningDocumentDetail>(
        apiBaseUrl,
        `/cards/${selectedCardId}/learning-documents`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        },
      )
      if (
        activeCourseIdRef.current !== courseId ||
        created.course_id !== courseId
      ) {
        return
      }
      setSelectedDocumentId(created.id)
      setMode('edit')
      setMessage('Study document created.')
      onDocumentRouteChangeRef.current?.(
        created.id,
        selectedCardId,
        'push',
      )
      await loadLibrary()
      if (activeCourseIdRef.current === courseId) {
        setSelectedDocumentId(created.id)
      }
    } catch (createError) {
      if (activeCourseIdRef.current === courseId) {
        setError(createError instanceof Error ? createError.message : 'Create failed.')
      }
    } finally {
      if (activeCourseIdRef.current === courseId) {
        setIsSaving(false)
      }
    }
  }

  async function saveDocument() {
    if (!document) return
    const courseId = document.course_id
    if (activeCourseIdRef.current !== courseId) return
    setIsSaving(true)
    setError(null)
    try {
      const updated = await fetchJson<LearningDocumentDetail>(
        apiBaseUrl,
        `/learning-documents/${document.id}`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title, summary, body_markdown: body, status }),
        },
      )
      if (
        activeCourseIdRef.current !== courseId ||
        updated.course_id !== courseId
      ) {
        return
      }
      setDocument(updated)
      await studyDraft.clearDraft()
      setMessage(`Saved version ${updated.versions[0]?.version_number ?? ''}.`)
      await loadLibrary()
      if (activeCourseIdRef.current === courseId) {
        setSelectedDocumentId(updated.id)
      }
    } catch (saveError) {
      if (activeCourseIdRef.current === courseId) {
        setError(saveError instanceof Error ? saveError.message : 'Save failed.')
      }
    } finally {
      if (activeCourseIdRef.current === courseId) {
        setIsSaving(false)
      }
    }
  }

  function isCurrentGeneration(
    sequence: number,
    courseId: string,
    documentId: string,
  ): boolean {
    return (
      sequence === generationSequenceRef.current &&
      activeCourseIdRef.current === courseId &&
      activeDocumentIdRef.current === documentId
    )
  }

  async function applyGenerationResult(
    result: LearningDocumentGenerationResult,
    sequence: number,
    courseId: string,
    documentId: string,
  ): Promise<void> {
    if (
      !isCurrentGeneration(sequence, courseId, documentId) ||
      result.document.course_id !== courseId ||
      result.document.id !== documentId
    ) {
      return
    }
    setDocument(result.document)
    setTitle(result.document.title)
    setSummary(result.document.summary)
    setBody(result.document.body_markdown)
    setStatus(result.document.status)
    setMode('preview')
    await studyDraft.clearDraft()
    if (!isCurrentGeneration(sequence, courseId, documentId)) return
    setMessage(
      `${result.selected_cards} cards and ${result.selected_source_units} source units used.${result.warning ? ` ${result.warning}` : ''}`,
    )
    await loadLibrary()
    if (isCurrentGeneration(sequence, courseId, documentId)) {
      setSelectedDocumentId(result.document.id)
      setGenerationTask(null)
      setFailedGenerationTask(null)
    }
  }

  async function waitForGenerationTask(
    taskId: string,
    sequence: number,
    courseId: string,
    documentId: string,
    controller: AbortController,
  ): Promise<void> {
    const completed =
      await waitForReliableTask<LearningDocumentGenerationTaskResult>(
        apiBaseUrl,
        taskId,
        {
          signal: controller.signal,
          onProgress: (task) => {
            if (
              isCurrentGeneration(sequence, courseId, documentId)
            ) {
              setGenerationTask(task)
            }
          },
        },
      )
    const result = completed.result?.generation
    if (!result) {
      throw new Error(
        'Document generation completed without a result.',
      )
    }
    await applyGenerationResult(
      result,
      sequence,
      courseId,
      documentId,
    )
  }

  async function generateDocument() {
    if (!document) return
    const courseId = document.course_id
    const documentId = document.id
    if (
      activeCourseIdRef.current !== courseId ||
      activeDocumentIdRef.current !== documentId
    ) {
      return
    }
    generationRequestControllerRef.current?.abort()
    const controller = new AbortController()
    generationRequestControllerRef.current = controller
    const sequence = ++generationSequenceRef.current
    setIsGenerating(true)
    setGenerationTask(null)
    setFailedGenerationTask(null)
    setError(null)
    setMessage(null)
    let taskId: string | null = null
    try {
      const task = await enqueueLearningDocumentGeneration(
        apiBaseUrl,
        documentId,
        {
          source_asset_ids: [...selectedAssetIds],
          supporting_card_ids: [...supportingCardIds],
          focus: focus.trim() || null,
          model: selectedModel || null,
        },
        controller.signal,
      ) as ReliableTask<LearningDocumentGenerationTaskResult>
      taskId = task.id
      if (!isCurrentGeneration(sequence, courseId, documentId)) {
        return
      }
      setGenerationTask(task)
      await waitForGenerationTask(
        task.id,
        sequence,
        courseId,
        documentId,
        controller,
      )
    } catch (generationError) {
      if (
        controller.signal.aborted ||
        !isCurrentGeneration(sequence, courseId, documentId)
      ) {
        return
      }
      setError(
        generationError instanceof Error
          ? generationError.message
          : 'Generation failed.',
      )
      if (taskId) {
        setFailedGenerationTask({ taskId, documentId, courseId })
      }
    } finally {
      if (isCurrentGeneration(sequence, courseId, documentId)) {
        setIsGenerating(false)
      }
      if (generationRequestControllerRef.current === controller) {
        generationRequestControllerRef.current = null
      }
    }
  }

  async function retryGeneration() {
    const failed = failedGenerationTask
    if (
      !failed ||
      activeCourseIdRef.current !== failed.courseId ||
      activeDocumentIdRef.current !== failed.documentId
    ) {
      return
    }
    generationRequestControllerRef.current?.abort()
    const controller = new AbortController()
    generationRequestControllerRef.current = controller
    const sequence = ++generationSequenceRef.current
    setIsGenerating(true)
    setFailedGenerationTask(null)
    setError(null)
    setMessage(null)
    try {
      const task = await retryReliableTask(
        apiBaseUrl,
        failed.taskId,
      ) as ReliableTask<LearningDocumentGenerationTaskResult>
      if (
        !isCurrentGeneration(
          sequence,
          failed.courseId,
          failed.documentId,
        )
      ) {
        return
      }
      setGenerationTask(task)
      await waitForGenerationTask(
        task.id,
        sequence,
        failed.courseId,
        failed.documentId,
        controller,
      )
    } catch (generationError) {
      if (
        controller.signal.aborted ||
        !isCurrentGeneration(
          sequence,
          failed.courseId,
          failed.documentId,
        )
      ) {
        return
      }
      setError(
        generationError instanceof Error
          ? generationError.message
          : 'Generation retry failed.',
      )
      setFailedGenerationTask(failed)
    } finally {
      if (
        isCurrentGeneration(
          sequence,
          failed.courseId,
          failed.documentId,
        )
      ) {
        setIsGenerating(false)
      }
      if (generationRequestControllerRef.current === controller) {
        generationRequestControllerRef.current = null
      }
    }
  }

  async function cancelGeneration() {
    const task = generationTask
    const sequence = generationSequenceRef.current
    const courseId = activeCourseIdRef.current
    const documentId = activeDocumentIdRef.current
    if (
      !task ||
      !courseId ||
      !documentId ||
      !['queued', 'running', 'canceling'].includes(task.status)
    ) {
      return
    }
    try {
      const canceled = await cancelReliableTask(apiBaseUrl, task.id)
      if (
        !isCurrentGeneration(sequence, courseId, documentId) ||
        canceled.id !== task.id
      ) {
        return
      }
      setGenerationTask(
        canceled as ReliableTask<LearningDocumentGenerationTaskResult>,
      )
    } catch (cancelError) {
      if (!isCurrentGeneration(sequence, courseId, documentId)) {
        return
      }
      setError(
        cancelError instanceof Error
          ? cancelError.message
          : 'Generation cancellation failed.',
      )
    }
  }

  async function deleteDocument() {
    if (!document || !window.confirm(`Delete "${document.title}"?`)) return
    const courseId = document.course_id
    if (activeCourseIdRef.current !== courseId) return
    try {
      await fetchJson<void>(apiBaseUrl, `/learning-documents/${document.id}`, {
        method: 'DELETE',
      })
      announceTrashCreated({
        entity_type: 'learning_document',
        entity_id: document.id,
      })
      if (activeCourseIdRef.current !== courseId) return
      setSelectedDocumentId('')
      setDocument(null)
      await studyDraft.clearDraft()
      setMessage('Study document deleted.')
      onDocumentRouteChangeRef.current?.(
        null,
        selectedCardId || null,
        'push',
      )
      await loadLibrary()
    } catch (deleteError) {
      if (activeCourseIdRef.current === courseId) {
        setError(deleteError instanceof Error ? deleteError.message : 'Delete failed.')
      }
    }
  }

  async function restoreVersion(versionNumber: number) {
    if (!document || !window.confirm(`Restore version ${versionNumber}?`)) return
    const courseId = document.course_id
    if (activeCourseIdRef.current !== courseId) return
    try {
      const restored = await fetchJson<LearningDocumentDetail>(
        apiBaseUrl,
        `/learning-documents/${document.id}/restore`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ version_number: versionNumber }),
        },
      )
      if (
        activeCourseIdRef.current !== courseId ||
        restored.course_id !== courseId
      ) {
        return
      }
      setDocument(restored)
      setTitle(restored.title)
      setSummary(restored.summary)
      setBody(restored.body_markdown)
      setMessage(`Version ${versionNumber} restored as a new version.`)
    } catch (restoreError) {
      if (activeCourseIdRef.current === courseId) {
        setError(restoreError instanceof Error ? restoreError.message : 'Restore failed.')
      }
    }
  }

  return (
    <div className="study-view">
      <header className="study-toolbar">
        <div>
          <div className="panel-title">Concept learning</div>
          <h2>Study documents</h2>
          <p>Grow grounded cards into editable, source-backed explanations.</p>
        </div>
        {showCourseSelector && (
          <label>
            <span>Course</span>
            <select value={selectedCourseId ?? ''} onChange={(event) => onSelectCourse(event.target.value)}>
              {courses.map((course) => (
                <option key={course.id} value={course.id}>{course.title}</option>
              ))}
            </select>
          </label>
        )}
        <label>
          <span>Anchor card</span>
          <select
            value={selectedCardId}
            onChange={(event) => {
              const cardId = event.target.value
              setSelectedCardId(cardId)
              onDocumentRouteChangeRef.current?.(
                selectedDocumentId || null,
                cardId || null,
                'push',
              )
            }}
          >
            {scopedCards.map((card) => (
              <option key={card.id} value={card.id}>{card.title}</option>
            ))}
          </select>
        </label>
        <button type="button" disabled={!selectedCardId || isSaving} onClick={() => void createDocument()}>
          <FilePlus2 size={16} /> New document
        </button>
      </header>

      {(error || message) && (
        <div
          className={error ? 'study-notice error' : 'study-notice success'}
          role={error ? 'alert' : 'status'}
        >
          <span>{error ?? message}</span>
          {error && failedGenerationTask && (
            <button
              type="button"
              onClick={() => void retryGeneration()}
            >
              Retry generation
            </button>
          )}
        </div>
      )}

      <div className="study-layout">
        <aside className="study-library">
          <section>
            <div className="study-section-heading">
              <div><strong>Documents</strong><span>{scopedDocuments.length}</span></div>
              <BookOpenText size={16} />
            </div>
            <div className="study-document-list">
              {scopedDocuments.map((item) => (
                <button
                  type="button"
                  key={item.id}
                  className={item.id === selectedDocumentId ? 'selected' : ''}
                  onClick={() => {
                    setSelectedDocumentId(item.id)
                    onDocumentRouteChangeRef.current?.(
                      item.id,
                      selectedCardId || null,
                      'push',
                    )
                  }}
                >
                  <strong>{item.title}</strong>
                  <span>{item.summary || 'No summary'}</span>
                  <small>{item.status} · {item.generation_mode}</small>
                </button>
              ))}
              {!scopedDocuments.length && !isLoading && <div className="study-empty-small">No study documents yet.</div>}
            </div>
          </section>

        </aside>

        <section
          className="study-document-workspace"
          aria-label="Study document workspace"
        >
          {scopedDocument ? (
            <>
              <div className="study-document-toolbar">
                <div className="study-mode-toggle">
                  <button type="button" className={mode === 'preview' ? 'active' : ''} onClick={() => setMode('preview')}>
                    <Eye size={15} /> Preview
                  </button>
                  <button type="button" className={mode === 'edit' ? 'active' : ''} onClick={() => setMode('edit')}>
                    <Pencil size={15} /> Edit
                  </button>
                </div>
                <select value={status} onChange={(event) => setStatus(event.target.value as LearningDocument['status'])}>
                  <option value="draft">draft</option>
                  <option value="reviewed">reviewed</option>
                  <option value="needs_fix">needs fix</option>
                </select>
                <SaveStatus
                  state={studyDraft.state}
                  message={studyDraft.message}
                />
                <button type="button" disabled={isSaving} onClick={() => void saveDocument()}>
                  <Save size={15} /> Save
                </button>
                <button className="danger-button" type="button" onClick={() => void deleteDocument()}>
                  <Trash2 size={15} />
                </button>
              </div>
              {mode === 'edit' ? (
                <div className="study-editor">
                  <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Document title" />
                  <textarea className="study-summary-input" value={summary} onChange={(event) => setSummary(event.target.value)} placeholder="Short summary" />
                  <textarea className="study-markdown-input" value={body} onChange={(event) => setBody(event.target.value)} placeholder="Write Markdown" />
                </div>
              ) : (
                <article className="study-markdown">
                  <ReactMarkdown>{body}</ReactMarkdown>
                </article>
              )}
            </>
          ) : (
            <div className="study-empty">
              <BookOpenText size={34} />
              <h2>Select or create a study document</h2>
              <p>{primaryCard ? `Use ${primaryCard.title} as the anchor.` : 'Choose an anchor card first.'}</p>
            </div>
          )}
        </section>

        <aside className="study-inspector">
          {scopedDocument ? (
            <>
              <section>
                <div className="study-section-heading">
                  <div><strong>Generate draft</strong><span>{selectedModel || 'default model'}</span></div>
                  <Sparkles size={16} />
                </div>
                <textarea value={focus} onChange={(event) => setFocus(event.target.value)} placeholder="Optional focus, e.g. mathematical intuition" />
                <div className="study-check-list">
                  <div className="study-list-label">Source files</div>
                  {readyAssets.map((asset) => (
                    <label key={asset.id}>
                      <input
                        type="checkbox"
                        checked={selectedAssetIds.has(asset.id)}
                        onChange={(event) => setSelectedAssetIds((current) => {
                          const next = new Set(current)
                          if (event.target.checked) next.add(asset.id)
                          else next.delete(asset.id)
                          return next
                        })}
                      />
                      <span>{asset.original_filename}</span>
                    </label>
                  ))}
                  {!readyAssets.length && (
                    <p className="study-empty-small">
                      No ready document sources.
                    </p>
                  )}
                </div>
                {onManageSources && (
                  <button
                    className="study-manage-sources"
                    type="button"
                    onClick={onManageSources}
                  >
                    Manage sources
                  </button>
                )}
                <div className="study-check-list supporting">
                  <div className="study-list-label">Supporting cards</div>
                  {supportCandidates.map((card) => (
                    <label key={card.id}>
                      <input
                        type="checkbox"
                        checked={supportingCardIds.has(card.id)}
                        onChange={(event) => setSupportingCardIds((current) => {
                          const next = new Set(current)
                          if (event.target.checked) next.add(card.id)
                          else next.delete(card.id)
                          return next
                        })}
                      />
                      <span>{card.title}</span>
                    </label>
                  ))}
                </div>
                <button className="study-generate-button" type="button" disabled={isGenerating} onClick={() => void generateDocument()}>
                  <Sparkles size={16} /> {isGenerating ? 'Generating locally' : 'Generate grounded draft'}
                </button>
                {generationTask &&
                  ['queued', 'running', 'canceling'].includes(
                    generationTask.status,
                  ) && (
                    <div className="study-empty-small" role="status">
                      <span>
                        {generationTask.progress.message ??
                          'Generating grounded study material…'}
                      </span>
                      <button
                        type="button"
                        disabled={generationTask.status === 'canceling'}
                        onClick={() => void cancelGeneration()}
                      >
                        {generationTask.status === 'canceling'
                          ? 'Canceling'
                          : 'Cancel'}
                      </button>
                    </div>
                  )}
              </section>

              <section>
                <div className="study-section-heading">
                  <div><strong>References</strong><span>{scopedDocument.sources.length}</span></div>
                  <Link2 size={16} />
                </div>
                <div className="study-reference-list">
                  {scopedDocument.sources.map((source) => (
                    <div key={source.id}>
                      <strong>[{source.label}] {sourceLocation(source)}</strong>
                      <span>{source.source_type === 'card_claim' ? 'course evidence' : 'supplementary source'}</span>
                      <p>{source.quote}</p>
                    </div>
                  ))}
                  {!scopedDocument.sources.length && <div className="study-empty-small">No generated references yet.</div>}
                </div>
              </section>

              <section>
                <div className="study-section-heading">
                  <div><strong>Versions</strong><span>{scopedDocument.versions.length}</span></div>
                  <History size={16} />
                </div>
                <div className="study-version-list">
                  {scopedDocument.versions.map((version) => (
                    <button key={version.id} type="button" onClick={() => void restoreVersion(version.version_number)}>
                      <strong>v{version.version_number}</strong>
                      <span>{version.change_source} · {new Date(version.created_at).toLocaleString()}</span>
                    </button>
                  ))}
                </div>
              </section>
            </>
          ) : <div className="study-empty-small">Document tools appear after selection.</div>}
        </aside>
      </div>
    </div>
  )
}
