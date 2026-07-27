import {
  AlertCircle,
  BookOpenText,
  CheckCircle2,
  Database,
  File,
  FileText,
  LoaderCircle,
  Plus,
  RefreshCw,
  SearchCheck,
  Trash2,
  Video,
} from 'lucide-react'
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
} from 'react'
import { formatSourceLocator } from '../citations/citationFormat'
import {
  deleteSourceAsset,
  importCourseSource,
  indexCourseSources,
  listCourseSources,
  listSourceChunks,
  updateSourceEnabled,
} from './sourceApi'
import type {
  CourseSource,
  CourseSourceChunk,
} from './sourceTypes'
import './SourcesLibrary.css'

type CourseOption = {
  id: string
  title: string
}

export type SourcesLibraryProps = {
  apiBaseUrl: string
  courses: CourseOption[]
  selectedCourseId: string | null
  initialSourceId?: string | null
  refreshKey?: string | number
  onSelectCourse: (courseId: string) => void
  onSelectSource?: (
    sourceId: string | null,
    mode: 'push' | 'replace',
  ) => void
  onAddVideo?: () => void
  onOpenVideo?: (jobId: string) => void
  onOpenChat?: () => void
}

type SourceOperation =
  | 'import'
  | 'index'
  | `toggle:${string}`
  | `delete:${string}`
  | null

const SOURCE_ACCEPT =
  '.pdf,.pptx,.docx,.txt,.md,.markdown,application/pdf,application/vnd.openxmlformats-officedocument.presentationml.presentation,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain,text/markdown'

function sourceIcon(source: CourseSource) {
  if (source.source_type === 'video' || source.source_type === 'audio') {
    return <Video aria-hidden="true" size={19} />
  }
  if (source.source_type === 'pdf') {
    return <FileText aria-hidden="true" size={19} />
  }
  return <File aria-hidden="true" size={19} />
}

function formatBytes(value: number | null): string {
  if (value === null) return 'Size unavailable'
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 ** 3) {
    return `${(value / 1024 ** 2).toFixed(1)} MB`
  }
  return `${(value / 1024 ** 3).toFixed(1)} GB`
}

function statusLabel(value: string): string {
  return value.replaceAll('_', ' ')
}

function sourceAvailabilityLabel(source: CourseSource): string {
  if (!source.enabled) return 'Disabled'
  if (source.content_status !== 'ready') {
    return statusLabel(source.content_status)
  }
  if (source.index_status === 'ready') return 'Ready for chat'
  if (source.index_status === 'indexing') return 'Indexing'
  if (source.index_status === 'failed') return 'Index failed'
  return 'Needs indexing'
}

export function SourcesLibrary({
  apiBaseUrl,
  courses,
  selectedCourseId,
  initialSourceId = null,
  refreshKey = 0,
  onSelectCourse,
  onSelectSource,
  onAddVideo,
  onOpenVideo,
  onOpenChat,
}: SourcesLibraryProps) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const loadSequenceRef = useRef(0)
  const operationSequenceRef = useRef(0)
  const activeCourseIdRef = useRef(selectedCourseId)
  const [sources, setSources] = useState<CourseSource[]>([])
  const [loadedCourseId, setLoadedCourseId] =
    useState<string | null>(null)
  const [selectedSourceId, setSelectedSourceId] =
    useState<string | null>(initialSourceId)
  const [chunks, setChunks] = useState<CourseSourceChunk[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [isLoadingChunks, setIsLoadingChunks] = useState(false)
  const [operation, setOperation] = useState<SourceOperation>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const scopedSources = useMemo(
    () =>
      loadedCourseId === selectedCourseId
        ? sources
        : [],
    [loadedCourseId, selectedCourseId, sources],
  )
  const selectedSource = useMemo(
    () =>
      scopedSources.find(
        (source) => source.id === selectedSourceId,
      ) ?? null,
    [scopedSources, selectedSourceId],
  )
  const readySources = useMemo(
    () =>
      scopedSources.filter(
        (source) =>
          source.enabled && source.content_status === 'ready',
      ),
    [scopedSources],
  )
  const indexedCount = useMemo(
    () =>
      scopedSources.filter(
        (source) =>
          source.enabled &&
          source.content_status === 'ready' &&
          source.index_status === 'ready',
      ).length,
    [scopedSources],
  )
  const documentCount = useMemo(
    () =>
      scopedSources.filter(
        (source) => source.origin_type === 'source_asset',
      ).length,
    [scopedSources],
  )

  const loadSources = useCallback(
    async (signal?: AbortSignal): Promise<CourseSource[]> => {
      const courseId = selectedCourseId
      if (
        !courseId ||
        activeCourseIdRef.current !== courseId
      ) {
        setSources([])
        setLoadedCourseId(null)
        setIsLoading(false)
        return []
      }
      const sequence = ++loadSequenceRef.current
      setIsLoading(true)
      setError(null)
      try {
        const nextSources = await listCourseSources(
          apiBaseUrl,
          courseId,
          signal,
        )
        if (
          sequence === loadSequenceRef.current &&
          activeCourseIdRef.current === courseId
        ) {
          setSources(nextSources)
          setLoadedCourseId(courseId)
        }
        return nextSources
      } catch (requestError) {
        if (
          signal?.aborted ||
          (requestError instanceof DOMException &&
            requestError.name === 'AbortError')
        ) {
          return []
        }
        if (
          sequence === loadSequenceRef.current &&
          activeCourseIdRef.current === courseId
        ) {
          setSources([])
          setLoadedCourseId(null)
          setError(
            requestError instanceof Error
              ? requestError.message
              : 'Sources could not be loaded.',
          )
        }
        return []
      } finally {
        if (
          sequence === loadSequenceRef.current &&
          activeCourseIdRef.current === courseId
        ) {
          setIsLoading(false)
        }
      }
    },
    [apiBaseUrl, selectedCourseId],
  )

  useEffect(() => {
    activeCourseIdRef.current = selectedCourseId
    loadSequenceRef.current += 1
    operationSequenceRef.current += 1
    setOperation(null)
    setSources([])
    setLoadedCourseId(null)
    setChunks([])
    setNotice(null)
    setError(null)
    setIsLoading(Boolean(selectedCourseId))
  }, [selectedCourseId])

  useEffect(() => {
    setSelectedSourceId(initialSourceId)
  }, [initialSourceId, selectedCourseId])

  useEffect(() => {
    const controller = new AbortController()
    void loadSources(controller.signal)
    return () => controller.abort()
  }, [loadSources, refreshKey])

  useEffect(() => {
    if (!selectedSourceId) {
      setChunks([])
      return
    }
    if (isLoading || loadedCourseId !== selectedCourseId) {
      return
    }
    if (
      !scopedSources.some(
        (source) => source.id === selectedSourceId,
      )
    ) {
      setSelectedSourceId(null)
      onSelectSource?.(null, 'replace')
      setChunks([])
      return
    }

    const controller = new AbortController()
    setIsLoadingChunks(true)
    setChunks([])
    void listSourceChunks(apiBaseUrl, selectedSourceId, {
      limit: 50,
      signal: controller.signal,
    })
      .then(setChunks)
      .catch((requestError: unknown) => {
        if (!controller.signal.aborted) {
          setError(
            requestError instanceof Error
              ? requestError.message
              : 'Source preview could not be loaded.',
          )
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoadingChunks(false)
      })
    return () => controller.abort()
  }, [
    apiBaseUrl,
    isLoading,
    loadedCourseId,
    onSelectSource,
    selectedCourseId,
    selectedSourceId,
    scopedSources,
  ])

  function selectSource(sourceId: string) {
    setSelectedSourceId(sourceId)
    setError(null)
    onSelectSource?.(sourceId, 'push')
  }

  async function refreshSources() {
    setNotice(null)
    await loadSources()
  }

  async function importSource(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    const courseId = selectedCourseId
    if (!file || !courseId) return

    const operationSequence = ++operationSequenceRef.current
    setOperation('import')
    setError(null)
    setNotice(null)
    try {
      const result = await importCourseSource(
        apiBaseUrl,
        courseId,
        file,
      )
      if (
        operationSequence !== operationSequenceRef.current ||
        activeCourseIdRef.current !== courseId
      ) {
        return
      }
      setNotice(
        `Added ${result.asset.original_filename} with ${result.asset.unit_count} extracted section${result.asset.unit_count === 1 ? '' : 's'}.`,
      )
      const nextSources = await loadSources()
      const imported = nextSources.find(
        (source) =>
          source.origin_type === 'source_asset' &&
          source.origin_id === result.asset.id,
      )
      if (imported) selectSource(imported.id)
    } catch (requestError) {
      if (
        operationSequence !== operationSequenceRef.current ||
        activeCourseIdRef.current !== courseId
      ) {
        return
      }
      const message =
        requestError instanceof Error
          ? requestError.message
          : 'Source import failed.'
      await loadSources()
      setError(message)
    } finally {
      if (
        operationSequence === operationSequenceRef.current &&
        activeCourseIdRef.current === courseId
      ) {
        setOperation(null)
      }
    }
  }

  async function toggleSource(source: CourseSource) {
    const courseId = source.course_id
    const operationSequence = ++operationSequenceRef.current
    setOperation(`toggle:${source.id}`)
    setError(null)
    setNotice(null)
    try {
      const updated = await updateSourceEnabled(
        apiBaseUrl,
        source.id,
        !source.enabled,
      )
      if (
        operationSequence !== operationSequenceRef.current ||
        activeCourseIdRef.current !== courseId ||
        updated.course_id !== courseId
      ) {
        return
      }
      setSources((current) =>
        current.map((item) =>
          item.id === updated.id ? updated : item,
        ),
      )
      setNotice(
        `${updated.title} is now ${updated.enabled ? 'available' : 'excluded'} by default.`,
      )
    } catch (requestError) {
      if (
        operationSequence !== operationSequenceRef.current ||
        activeCourseIdRef.current !== courseId
      ) {
        return
      }
      setError(
        requestError instanceof Error
          ? requestError.message
          : 'Source availability could not be changed.',
      )
    } finally {
      if (
        operationSequence === operationSequenceRef.current &&
        activeCourseIdRef.current === courseId
      ) {
        setOperation(null)
      }
    }
  }

  async function indexSources() {
    const courseId = selectedCourseId
    if (!courseId || readySources.length === 0) return

    const operationSequence = ++operationSequenceRef.current
    setOperation('index')
    setError(null)
    setNotice(null)
    try {
      const result = await indexCourseSources(
        apiBaseUrl,
        courseId,
        readySources.map((source) => source.id),
      )
      if (
        operationSequence !== operationSequenceRef.current ||
        activeCourseIdRef.current !== courseId
      ) {
        return
      }
      setNotice(
        `Indexed ${result.total_sources} source${result.total_sources === 1 ? '' : 's'}: ${result.embedded_chunks} new or changed chunk${result.embedded_chunks === 1 ? '' : 's'}, ${result.skipped_chunks} unchanged.`,
      )
      await loadSources()
    } catch (requestError) {
      if (
        operationSequence !== operationSequenceRef.current ||
        activeCourseIdRef.current !== courseId
      ) {
        return
      }
      const message =
        requestError instanceof Error
          ? requestError.message
          : 'Source indexing failed.'
      await loadSources()
      setError(message)
    } finally {
      if (
        operationSequence === operationSequenceRef.current &&
        activeCourseIdRef.current === courseId
      ) {
        setOperation(null)
      }
    }
  }

  async function removeSource(source: CourseSource) {
    if (source.origin_type !== 'source_asset') return
    const confirmed = window.confirm(
      `Remove "${source.title}" from this course? The imported file and its extracted text will be deleted.`,
    )
    if (!confirmed) return

    const courseId = source.course_id
    const operationSequence = ++operationSequenceRef.current
    setOperation(`delete:${source.id}`)
    setError(null)
    setNotice(null)
    try {
      await deleteSourceAsset(apiBaseUrl, source.origin_id)
      if (
        operationSequence !== operationSequenceRef.current ||
        activeCourseIdRef.current !== courseId
      ) {
        return
      }
      if (selectedSourceId === source.id) {
        setSelectedSourceId(null)
        onSelectSource?.(null, 'replace')
      }
      setNotice(`Removed ${source.title}.`)
      await loadSources()
    } catch (requestError) {
      if (
        operationSequence !== operationSequenceRef.current ||
        activeCourseIdRef.current !== courseId
      ) {
        return
      }
      setError(
        requestError instanceof Error
          ? requestError.message
          : 'Source could not be removed.',
      )
    } finally {
      if (
        operationSequence === operationSequenceRef.current &&
        activeCourseIdRef.current === courseId
      ) {
        setOperation(null)
      }
    }
  }

  return (
    <section
      className="sources-library"
      aria-labelledby="sources-library-title"
    >
      <header className="sources-library-header">
        <div>
          <span className="sources-eyebrow">Notebook evidence</span>
          <h1 id="sources-library-title">Sources</h1>
          <p>
            Add, inspect, and index the original material that Chat and
            Studio may use.
          </p>
        </div>
        <div className="sources-library-controls">
          <label>
            <span>Course notebook</span>
            <select
              value={selectedCourseId ?? ''}
              disabled={!courses.length || operation !== null}
              onChange={(event) => onSelectCourse(event.target.value)}
            >
              {!courses.length && <option value="">No courses</option>}
              {courses.map((course) => (
                <option key={course.id} value={course.id}>
                  {course.title}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="sources-secondary-action"
            aria-label="Refresh sources"
            disabled={!selectedCourseId || isLoading || operation !== null}
            onClick={() => void refreshSources()}
          >
            <RefreshCw
              aria-hidden="true"
              className={isLoading ? 'sources-spin' : undefined}
              size={16}
            />
            Refresh
          </button>
          <button
            type="button"
            className="sources-secondary-action"
            disabled={
              !selectedCourseId ||
              readySources.length === 0 ||
              operation !== null
            }
            onClick={() => void indexSources()}
          >
            {operation === 'index' ? (
              <LoaderCircle
                aria-hidden="true"
                className="sources-spin"
                size={16}
              />
            ) : (
              <SearchCheck aria-hidden="true" size={16} />
            )}
            Index ready
          </button>
          <button
            type="button"
            className="sources-secondary-action"
            disabled={!selectedCourseId || operation !== null || !onAddVideo}
            onClick={onAddVideo}
          >
            <Video aria-hidden="true" size={16} />
            Add video
          </button>
          <button
            type="button"
            className="sources-primary-action"
            disabled={!selectedCourseId || operation !== null}
            onClick={() => fileInputRef.current?.click()}
          >
            {operation === 'import' ? (
              <LoaderCircle
                aria-hidden="true"
                className="sources-spin"
                size={16}
              />
            ) : (
              <Plus aria-hidden="true" size={16} />
            )}
            Add files
          </button>
          <input
            ref={fileInputRef}
            className="sources-file-input"
            type="file"
            aria-label="Import source file"
            accept={SOURCE_ACCEPT}
            onChange={(event) => void importSource(event)}
          />
        </div>
      </header>

      <div className="sources-summary" aria-label="Source status summary">
        <div>
          <Database aria-hidden="true" size={17} />
          <span>All sources</span>
          <strong>{scopedSources.length}</strong>
        </div>
        <div>
          <Video aria-hidden="true" size={17} />
          <span>Video or audio</span>
          <strong>{scopedSources.length - documentCount}</strong>
        </div>
        <div>
          <BookOpenText aria-hidden="true" size={17} />
          <span>Documents</span>
          <strong>{documentCount}</strong>
        </div>
        <div>
          <CheckCircle2 aria-hidden="true" size={17} />
          <span>Ready for chat</span>
          <strong>{indexedCount}</strong>
        </div>
      </div>

      {notice && (
        <div className="sources-notice success" role="status">
          <CheckCircle2 aria-hidden="true" size={17} />
          <span>{notice}</span>
        </div>
      )}
      {error && (
        <div className="sources-notice error" role="alert">
          <AlertCircle aria-hidden="true" size={17} />
          <span>{error}</span>
          <button type="button" onClick={() => void refreshSources()}>
            Retry
          </button>
        </div>
      )}

      <div className="sources-library-layout">
        <div
          className="sources-list"
          aria-busy={isLoading}
          aria-label="Course sources"
        >
          {isLoading && !scopedSources.length ? (
            <div className="sources-empty" role="status">
              <LoaderCircle
                aria-hidden="true"
                className="sources-spin"
                size={24}
              />
              <strong>Loading course sources</strong>
              <span>Reading the local source catalog.</span>
            </div>
          ) : scopedSources.length ? (
            scopedSources.map((source) => {
              const isSelected = source.id === selectedSourceId
              const isToggling = operation === `toggle:${source.id}`
              const isDeleting = operation === `delete:${source.id}`
              return (
                <article
                  key={source.id}
                  className={[
                    'source-list-item',
                    isSelected ? 'selected' : '',
                    source.enabled ? '' : 'disabled',
                  ]
                    .filter(Boolean)
                    .join(' ')}
                >
                  <button
                    type="button"
                    className="source-list-main"
                    aria-current={isSelected ? 'true' : undefined}
                    onClick={() => selectSource(source.id)}
                  >
                    <span className="source-list-icon">
                      {sourceIcon(source)}
                    </span>
                    <span className="source-list-copy">
                      <strong>{source.title}</strong>
                      <small>
                        {source.source_type.toUpperCase()} ·{' '}
                        {formatBytes(source.size_bytes)} ·{' '}
                        {source.chunk_count} chunk
                        {source.chunk_count === 1 ? '' : 's'}
                      </small>
                    </span>
                    <span
                      className={`source-status ${source.index_status}`}
                    >
                      {sourceAvailabilityLabel(source)}
                    </span>
                  </button>
                  <div className="source-list-actions">
                    <button
                      type="button"
                      role="switch"
                      aria-checked={source.enabled}
                      aria-label={`${source.enabled ? 'Exclude' : 'Include'} ${source.title}`}
                      disabled={operation !== null}
                      onClick={() => void toggleSource(source)}
                    >
                      {isToggling ? (
                        <LoaderCircle
                          aria-hidden="true"
                          className="sources-spin"
                          size={14}
                        />
                      ) : (
                        <span aria-hidden="true" />
                      )}
                      {source.enabled ? 'Included' : 'Excluded'}
                    </button>
                    {source.origin_type === 'video_job' ? (
                      <button
                        type="button"
                        disabled={!onOpenVideo}
                        onClick={() => onOpenVideo?.(source.origin_id)}
                      >
                        Open video
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="source-delete-action"
                        aria-label={`Remove ${source.title}`}
                        disabled={operation !== null}
                        onClick={() => void removeSource(source)}
                      >
                        {isDeleting ? (
                          <LoaderCircle
                            aria-hidden="true"
                            className="sources-spin"
                            size={14}
                          />
                        ) : (
                          <Trash2 aria-hidden="true" size={14} />
                        )}
                        Remove
                      </button>
                    )}
                  </div>
                </article>
              )
            })
          ) : (
            <div className="sources-empty">
              <BookOpenText aria-hidden="true" size={28} />
              <strong>No sources in this notebook yet</strong>
              <span>
                Add a document or video to create the notebook's
                evidence library.
              </span>
              <div className="sources-empty-actions">
                <button
                  type="button"
                  disabled={
                    !selectedCourseId ||
                    operation !== null ||
                    !onAddVideo
                  }
                  onClick={onAddVideo}
                >
                  <Video aria-hidden="true" size={15} />
                  Add video
                </button>
                <button
                  type="button"
                  disabled={!selectedCourseId || operation !== null}
                  onClick={() => fileInputRef.current?.click()}
                >
                  <Plus aria-hidden="true" size={15} />
                  Add document
                </button>
              </div>
            </div>
          )}
        </div>

        <aside className="source-preview" aria-label="Source preview">
          {selectedSource ? (
            <>
              <header>
                <span>{sourceIcon(selectedSource)}</span>
                <div>
                  <span className="sources-eyebrow">
                    {selectedSource.source_type} source
                  </span>
                  <h2>{selectedSource.title}</h2>
                  <p>
                    {statusLabel(selectedSource.content_status)} content ·{' '}
                    {statusLabel(selectedSource.index_status)} index
                  </p>
                </div>
              </header>

              {(selectedSource.error_message ||
                selectedSource.index_error) && (
                <div className="source-preview-error">
                  <AlertCircle aria-hidden="true" size={16} />
                  <span>
                    {selectedSource.error_message ??
                      selectedSource.index_error}
                  </span>
                </div>
              )}

              <div className="source-preview-meta">
                <span>
                  <strong>{selectedSource.chunk_count}</strong> extracted
                  chunks
                </span>
                <span>
                  <strong>{selectedSource.indexed_chunk_count}</strong>{' '}
                  indexed chunks
                </span>
                <span>
                  <strong>
                    {selectedSource.enabled ? 'Included' : 'Excluded'}
                  </strong>{' '}
                  by default
                </span>
              </div>

              <div className="source-chunks">
                <div className="source-chunks-heading">
                  <div>
                    <strong>Extracted evidence</strong>
                    <span>
                      Exact text used by retrieval and grounded answers
                    </span>
                  </div>
                  {selectedSource.chunk_count > 50 && (
                    <small>First 50</small>
                  )}
                </div>
                {isLoadingChunks ? (
                  <div className="source-preview-loading" role="status">
                    <LoaderCircle
                      aria-hidden="true"
                      className="sources-spin"
                      size={18}
                    />
                    Loading extracted evidence
                  </div>
                ) : chunks.length ? (
                  <ol>
                    {chunks.map((chunk) => (
                      <li key={chunk.id}>
                        <span>
                          {formatSourceLocator(chunk.locator)}
                        </span>
                        <p>{chunk.text}</p>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <div className="source-preview-empty">
                    No extracted text is available yet.
                  </div>
                )}
              </div>

              {onOpenChat &&
                selectedSource.enabled &&
                selectedSource.content_status === 'ready' && (
                  <button
                    type="button"
                    className="source-chat-action"
                    onClick={onOpenChat}
                  >
                    Ask Chat about this notebook
                  </button>
                )}
            </>
          ) : (
            <div className="source-preview-placeholder">
              <FileText aria-hidden="true" size={28} />
              <strong>Select a source to inspect it</strong>
              <span>
                Preview extracted evidence, location anchors, and index
                readiness before asking questions.
              </span>
            </div>
          )}
        </aside>
      </div>
    </section>
  )
}
