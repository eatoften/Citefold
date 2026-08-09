import {
  AlertCircle,
  BookOpenText,
  FileText,
  LoaderCircle,
  RefreshCw,
  Video,
  X,
} from 'lucide-react'
import {
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import type {
  SourceLocator,
  SourceType,
} from '../sources/sourceTypes'
import { fetchCitationTargetAtPath } from './citationApi'
import {
  formatSourceLocator,
  pdfUrlAtPage,
  resolveApiMediaUrl,
  segmentCitationQuote,
  seekMediaToLocator,
} from './citationFormat'
import type {
  CitationTarget,
  CitationTargetResolver,
  SourceEvidenceSnapshot,
} from './citationTypes'
import './CitationInspector.css'

export type CitationInspectorProps = {
  apiBaseUrl: string
  courseId: string | null
  citation: SourceEvidenceSnapshot | null
  resolver?: CitationTargetResolver | null
  onClose: () => void
}

type OpeningScope = {
  scopeKey: string
  courseId: string | null
}

function sourceIcon(sourceType: SourceType) {
  return sourceType === 'video' || sourceType === 'audio' ? (
    <Video aria-hidden="true" size={18} />
  ) : (
    <FileText aria-hidden="true" size={18} />
  )
}

function targetLocator(
  citation: SourceEvidenceSnapshot,
  target: CitationTarget | null,
): SourceLocator {
  return target?.locator ?? citation.locator
}

function targetContextIndex(target: CitationTarget): number {
  const explicitTarget = target.context.findIndex(
    (chunk) => chunk.is_target,
  )
  if (explicitTarget >= 0) return explicitTarget
  if (target.target_chunk_id) {
    return target.context.findIndex(
      (chunk) => chunk.chunk_id === target.target_chunk_id,
    )
  }
  return -1
}

export function CitationInspector({
  apiBaseUrl,
  courseId,
  citation,
  resolver,
  onClose,
}: CitationInspectorProps) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const targetChunkRef = useRef<HTMLElement>(null)
  const openingScopeRef = useRef<OpeningScope | null>(null)
  const [target, setTarget] = useState<CitationTarget | null>(null)
  const [requestError, setRequestError] = useState<string | null>(null)
  const [mediaError, setMediaError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [requestVersion, setRequestVersion] = useState(0)
  const targetResolver = useMemo<CitationTargetResolver | null>(() => {
    if (!citation || !courseId) return null
    if (resolver) return resolver
    const course = encodeURIComponent(courseId)
    const citationId = encodeURIComponent(citation.id)
    const basePath = `/courses/${course}/chat/citations/${citationId}`
    return {
      scopeKey: `chat:${citation.id}`,
      targetPath: `${basePath}/target`,
      contentPath: `${basePath}/content`,
    }
  }, [citation, courseId, resolver])

  useEffect(() => {
    if (!citation || !targetResolver) {
      openingScopeRef.current = null
      return
    }
    if (
      !openingScopeRef.current ||
      openingScopeRef.current.scopeKey !== targetResolver.scopeKey
    ) {
      openingScopeRef.current = {
        scopeKey: targetResolver.scopeKey,
        courseId,
      }
      return
    }
    if (openingScopeRef.current.courseId !== courseId) {
      onClose()
    }
  }, [citation, courseId, onClose, targetResolver])

  useEffect(() => {
    if (!citation) return
    const dialog = dialogRef.current
    if (!dialog) return
    if (!dialog.open) {
      if (typeof dialog.showModal === 'function') {
        dialog.showModal()
      } else {
        dialog.setAttribute('open', '')
      }
    }
    return () => {
      if (dialog.open && typeof dialog.close === 'function') {
        dialog.close()
      }
    }
  }, [citation])

  useEffect(() => {
    setTarget(null)
    setRequestError(null)
    setMediaError(null)
    if (!citation) {
      setIsLoading(false)
      return
    }
    if (!courseId) {
      setIsLoading(false)
      setRequestError(
        'The course is no longer selected. The saved citation is still available below.',
      )
      return
    }
    if (!targetResolver) {
      setIsLoading(false)
      setRequestError('The source resolver is unavailable.')
      return
    }
    if (
      openingScopeRef.current &&
      openingScopeRef.current.courseId !== courseId
    ) {
      setIsLoading(false)
      return
    }

    const controller = new AbortController()
    let cancelled = false
    setIsLoading(true)
    void fetchCitationTargetAtPath(
      apiBaseUrl,
      targetResolver.targetPath,
      controller.signal,
    )
      .then((nextTarget) => {
        if (cancelled) return
        setTarget(nextTarget)
      })
      .catch((error: unknown) => {
        if (cancelled || controller.signal.aborted) return
        setRequestError(
          error instanceof Error
            ? error.message
            : 'The live source could not be opened.',
        )
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false)
      })

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [apiBaseUrl, citation, courseId, requestVersion, targetResolver])

  const locator = citation
    ? targetLocator(citation, target)
    : null
  const mediaUrl = useMemo(
    () =>
      resolveApiMediaUrl(
        apiBaseUrl,
        target?.media_url ?? null,
        targetResolver?.contentPath ?? '',
      ),
    [apiBaseUrl, target?.media_url, targetResolver?.contentPath],
  )
  const pdfUrl = useMemo(
    () =>
      mediaUrl && locator
        ? pdfUrlAtPage(mediaUrl, locator)
        : null,
    [locator, mediaUrl],
  )
  const focusedContextIndex = target
    ? targetContextIndex(target)
    : -1

  useEffect(() => {
    if (!target || focusedContextIndex < 0) return
    const frame = window.requestAnimationFrame(() => {
      const element = targetChunkRef.current
      element?.scrollIntoView({
        block: 'center',
        behavior: 'auto',
      })
      element?.focus({ preventScroll: true })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [focusedContextIndex, target])

  if (!citation || !locator) return null

  const title = target?.source_title ?? citation.source_title
  const sourceType = target?.source_type ?? citation.source_type
  const quote = citation.quote
  const hasPlayableMedia =
    Boolean(mediaUrl) &&
    (target?.media_kind === 'video' ||
      target?.media_kind === 'audio')
  const hasPdf = Boolean(pdfUrl) && target?.media_kind === 'pdf'

  return (
    <dialog
      ref={dialogRef}
      className="citation-inspector"
      aria-labelledby="citation-inspector-title"
      aria-describedby="citation-inspector-location"
      aria-modal="true"
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.preventDefault()
          onClose()
        }
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div className="citation-inspector-surface">
        <header className="citation-inspector-header">
          <div className="citation-inspector-source-icon">
            {sourceIcon(sourceType)}
          </div>
          <div>
            <span>Source</span>
            <h2 id="citation-inspector-title">{title}</h2>
            <p id="citation-inspector-location">
              {formatSourceLocator(locator)}
            </p>
          </div>
          <button
            type="button"
            className="citation-inspector-close"
            aria-label="Close source"
            autoFocus
            onClick={onClose}
          >
            <X aria-hidden="true" size={18} />
          </button>
        </header>

        <div className="citation-inspector-body">
          <section
            className="citation-snapshot"
            aria-labelledby="citation-snapshot-title"
          >
            <div>
              <BookOpenText aria-hidden="true" size={16} />
              <h3 id="citation-snapshot-title">Cited evidence</h3>
            </div>
            <blockquote>{quote}</blockquote>
            <small>
              Saved with the grounded result so it remains verifiable even if the
              original file changes.
            </small>
          </section>

          {isLoading && (
            <div className="citation-inspector-status" role="status">
              <LoaderCircle
                aria-hidden="true"
                className="citation-inspector-spin"
                size={17}
              />
              Opening the exact source location…
            </div>
          )}

          {requestError && (
            <div className="citation-inspector-warning" role="alert">
              <AlertCircle aria-hidden="true" size={17} />
              <div>
                <strong>Live source unavailable</strong>
                <span>{requestError}</span>
              </div>
              <button
                type="button"
                onClick={() =>
                  setRequestVersion((version) => version + 1)
                }
              >
                <RefreshCw aria-hidden="true" size={14} />
                Retry
              </button>
            </div>
          )}

          {target?.availability === 'snapshot_only' && (
            <div className="citation-inspector-warning" role="status">
              <AlertCircle aria-hidden="true" size={17} />
              <div>
                <strong>Showing the saved citation</strong>
                <span>
                  {target.reason_message ??
                    'The original source is no longer available.'}
                </span>
              </div>
            </div>
          )}

          {hasPlayableMedia && (
            <section
              className="citation-media"
              aria-label={`${sourceType === 'audio' ? 'Audio' : 'Video'} source`}
            >
              {target?.media_kind === 'audio' ? (
                <audio
                  src={mediaUrl ?? undefined}
                  controls
                  preload="metadata"
                  aria-label={`${title} at ${formatSourceLocator(locator)}`}
                  onLoadedMetadata={(event) => {
                    seekMediaToLocator(event.currentTarget, locator)
                  }}
                  onError={() =>
                    setMediaError(
                      'The media could not be played. The saved evidence and transcript remain available.',
                    )
                  }
                />
              ) : (
                <video
                  src={mediaUrl ?? undefined}
                  controls
                  preload="metadata"
                  aria-label={`${title} at ${formatSourceLocator(locator)}`}
                  onLoadedMetadata={(event) => {
                    seekMediaToLocator(event.currentTarget, locator)
                  }}
                  onError={() =>
                    setMediaError(
                      'The media could not be played. The saved evidence and transcript remain available.',
                    )
                  }
                />
              )}
              <p>
                Ready at <strong>{formatSourceLocator(locator)}</strong>.
              </p>
            </section>
          )}

          {hasPdf && (
            <section
              className="citation-pdf"
              aria-labelledby="citation-pdf-title"
            >
              <div>
                <h3 id="citation-pdf-title">Original page</h3>
                <span>{formatSourceLocator(locator)}</span>
              </div>
              <iframe
                src={pdfUrl ?? undefined}
                title={`${title}, ${formatSourceLocator(locator)}`}
              />
              <small>
                Extracted page text is always shown below as a fallback.
              </small>
            </section>
          )}

          {mediaError && (
            <div className="citation-inspector-warning" role="alert">
              <AlertCircle aria-hidden="true" size={17} />
              <div>
                <strong>Media unavailable</strong>
                <span>{mediaError}</span>
              </div>
            </div>
          )}

          {target &&
            (target.availability === 'available' ||
              target.context.length > 0) && (
            <section
              className="citation-context-section"
              aria-labelledby="citation-context-title"
            >
              <div className="citation-context-heading">
                <div>
                  <h3 id="citation-context-title">Source context</h3>
                  <span>
                    {target.availability === 'available'
                      ? 'The cited location is highlighted below.'
                      : 'Showing the last verified extracted context; the original file is unavailable.'}
                  </span>
                </div>
                <strong>{formatSourceLocator(locator)}</strong>
              </div>
              {target.context.length ? (
                <div className="citation-context-list">
                  {target.context.map((chunk, index) => {
                    const isTarget =
                      chunk.is_target ||
                      (target.target_chunk_id !== null &&
                        chunk.chunk_id === target.target_chunk_id)
                    return (
                      <article
                        key={chunk.chunk_id}
                        ref={
                          index === focusedContextIndex
                            ? targetChunkRef
                            : undefined
                        }
                        className={isTarget ? 'target' : ''}
                        aria-current={
                          isTarget ? 'location' : undefined
                        }
                        tabIndex={isTarget ? -1 : undefined}
                      >
                        <span>
                          {formatSourceLocator(chunk.locator)}
                        </span>
                        <p>
                          {isTarget
                            ? segmentCitationQuote(
                                chunk.text,
                                citation.quote,
                              ).map((segment, segmentIndex) =>
                                segment.isMatch ? (
                                  <mark
                                    key={`${chunk.chunk_id}-match-${segmentIndex}`}
                                  >
                                    {segment.text}
                                  </mark>
                                ) : (
                                  <span
                                    key={`${chunk.chunk_id}-text-${segmentIndex}`}
                                  >
                                    {segment.text}
                                  </span>
                                ),
                              )
                            : chunk.text}
                        </p>
                      </article>
                    )
                  })}
                </div>
              ) : (
                <p className="citation-context-empty">
                  No additional extracted context is available. The saved
                  evidence above remains the source of record.
                </p>
              )}
            </section>
          )}
        </div>

        <p className="citation-inspector-announcement" aria-live="polite">
          {target
            ? `Opened ${title}, ${formatSourceLocator(locator)}.`
            : ''}
        </p>
      </div>
    </dialog>
  )
}
