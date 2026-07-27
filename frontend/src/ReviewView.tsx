import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowRight,
  BookOpenCheck,
  Clock3,
  Eye,
  RefreshCw,
  RotateCcw,
} from 'lucide-react'
import type { CourseMapPayload } from './courseMapTypes'
import type {
  ReviewCourse,
  ReviewQueue,
  ReviewQueueItem,
  ReviewRating,
} from './reviewTypes'


type ReviewViewProps = {
  apiBaseUrl: string
  courses: ReviewCourse[]
  selectedCourseId: string | null
  showCourseSelector?: boolean
  onSelectCourse: (courseId: string) => void
  onOpenWorkspaceCard: (cardId: string) => void
}


async function fetchJson<T>(apiBaseUrl: string, path: string, options?: RequestInit) {
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
  return response.json() as Promise<T>
}


function formatTime(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  return `${Math.floor(total / 60)}:${(total % 60).toString().padStart(2, '0')}`
}


function formatDue(value: string): string {
  return new Date(value).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}


function readClockMilliseconds(): number {
  return Date.now()
}


function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError'
}


export function ReviewView({
  apiBaseUrl,
  courses,
  selectedCourseId,
  showCourseSelector = true,
  onSelectCourse,
  onOpenWorkspaceCard,
}: ReviewViewProps) {
  const [queue, setQueue] = useState<ReviewQueue | null>(null)
  const [courseMap, setCourseMap] = useState<CourseMapPayload | null>(null)
  const [topicId, setTopicId] = useState('')
  const [currentIndex, setCurrentIndex] = useState(0)
  const [revealed, setRevealed] = useState(false)
  const [selfAnswer, setSelfAnswer] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [isRating, setIsRating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const startedAtRef = useRef(0)
  const activeCourseIdRef = useRef(selectedCourseId)
  const queueRequestEpochRef = useRef(0)
  const queueRequestControllerRef =
    useRef<AbortController | null>(null)

  useEffect(() => {
    activeCourseIdRef.current = selectedCourseId
  }, [selectedCourseId])

  const loadQueue = useCallback(async () => {
    const courseId = selectedCourseId
    queueRequestControllerRef.current?.abort()
    const requestEpoch = ++queueRequestEpochRef.current

    if (
      !courseId ||
      activeCourseIdRef.current !== courseId
    ) {
      setIsLoading(false)
      return
    }

    const controller = new AbortController()
    queueRequestControllerRef.current = controller
    setIsLoading(true)
    setError(null)
    const params = new URLSearchParams({ limit: '100' })
    if (topicId) params.set('topic_id', topicId)
    try {
      const [nextQueue, nextMap] = await Promise.all([
        fetchJson<ReviewQueue>(
          apiBaseUrl,
          `/courses/${courseId}/review/queue?${params}`,
          { signal: controller.signal },
        ),
        fetchJson<CourseMapPayload>(
          apiBaseUrl,
          `/courses/${courseId}/map`,
          { signal: controller.signal },
        ),
      ])
      if (
        controller.signal.aborted ||
        requestEpoch !== queueRequestEpochRef.current ||
        activeCourseIdRef.current !== courseId ||
        nextQueue.course_id !== courseId ||
        nextMap.course_id !== courseId
      ) {
        return
      }
      setQueue(nextQueue)
      setCourseMap(nextMap)
      setCurrentIndex(0)
      setRevealed(false)
      setSelfAnswer('')
      startedAtRef.current = readClockMilliseconds()
    } catch (loadError) {
      if (
        controller.signal.aborted ||
        requestEpoch !== queueRequestEpochRef.current ||
        activeCourseIdRef.current !== courseId ||
        isAbortError(loadError)
      ) {
        return
      }
      setError(loadError instanceof Error ? loadError.message : 'Review queue failed.')
    } finally {
      if (
        requestEpoch === queueRequestEpochRef.current &&
        activeCourseIdRef.current === courseId
      ) {
        if (queueRequestControllerRef.current === controller) {
          queueRequestControllerRef.current = null
        }
        setIsLoading(false)
      }
    }
  }, [apiBaseUrl, selectedCourseId, topicId])

  useEffect(() => {
    queueRequestEpochRef.current += 1
    queueRequestControllerRef.current?.abort()
    queueRequestControllerRef.current = null
    setQueue(null)
    setCourseMap(null)
    setTopicId('')
    setCurrentIndex(0)
    setRevealed(false)
    setSelfAnswer('')
    setError(null)
    setMessage(null)
    setIsLoading(Boolean(selectedCourseId))
    setIsRating(false)
    startedAtRef.current = 0
  }, [apiBaseUrl, selectedCourseId])

  useEffect(() => {
    void loadQueue()

    return () => {
      queueRequestEpochRef.current += 1
      queueRequestControllerRef.current?.abort()
      queueRequestControllerRef.current = null
    }
  }, [loadQueue])

  const scopedQueue =
    queue?.course_id === selectedCourseId ? queue : null
  const scopedCourseMap =
    courseMap?.course_id === selectedCourseId
      ? courseMap
      : null
  const currentItem = scopedQueue?.items[currentIndex] ?? null

  const topicDueCounts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const item of scopedQueue?.items ?? []) {
      if (item.topic_id) counts.set(item.topic_id, (counts.get(item.topic_id) ?? 0) + 1)
    }
    return [...counts.entries()]
      .map(([id, count]) => ({
        id,
        title: scopedCourseMap?.topics.find((topic) => topic.id === id)?.title ?? 'Unknown',
        count,
      }))
      .sort((left, right) => right.count - left.count)
  }, [scopedCourseMap, scopedQueue])

  async function rateCurrent(rating: ReviewRating) {
    if (!currentItem) return
    const courseId = selectedCourseId
    setIsRating(true)
    setError(null)
    try {
      await fetchJson(
        apiBaseUrl,
        `/review-items/${currentItem.review_item.id}/rate`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            rating,
            response_time_ms: Math.max(
              0,
              readClockMilliseconds() - startedAtRef.current,
            ),
          }),
        },
      )
      if (activeCourseIdRef.current !== courseId) {
        return
      }
      const remainingItems = scopedQueue?.items.filter(
        (item) => item.review_item.id !== currentItem.review_item.id,
      ) ?? []
      setQueue((current) => current ? {
        ...current,
        due_count: Math.max(0, current.due_count - 1),
        items: remainingItems,
      } : current)
      setCurrentIndex(0)
      setRevealed(false)
      setSelfAnswer('')
      setMessage(`Rated ${rating}.`)
      startedAtRef.current = readClockMilliseconds()
    } catch (ratingError) {
      if (activeCourseIdRef.current === courseId) {
        setError(ratingError instanceof Error ? ratingError.message : 'Rating failed.')
      }
    } finally {
      if (activeCourseIdRef.current === courseId) {
        setIsRating(false)
      }
    }
  }

  function renderEvidence(item: ReviewQueueItem) {
    const selectedClaimIds = new Set(item.review_item.source_claim_ids)
    const claims = selectedClaimIds.size > 0
      ? item.claims.filter((claim) => selectedClaimIds.has(claim.id))
      : item.claims
    return (
      <div className="review-evidence">
        <div className="review-section-title">Grounded evidence</div>
        {claims.map((claim) => (
          <div key={claim.id}>
            <strong>{claim.text}</strong>
            {claim.evidence.map((evidence) => (
              <blockquote key={evidence.id}>
                <span>
                  {formatTime(evidence.segment_start_seconds)} -{' '}
                  {formatTime(evidence.segment_end_seconds)}
                </span>
                {evidence.quote}
              </blockquote>
            ))}
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className="review-view">
      <header className="review-toolbar">
        <div>
          <div className="panel-title">Spaced repetition</div>
          <h2>Review</h2>
          <p>Recall first, reveal evidence, then rate honestly.</p>
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
          <span>Topic</span>
          <select value={topicId} onChange={(event) => setTopicId(event.target.value)}>
            <option value="">All topics</option>
            {(scopedCourseMap?.topics ?? []).filter((topic) => topic.status !== 'hidden').map((topic) => (
              <option key={topic.id} value={topic.id}>{topic.title}</option>
            ))}
          </select>
        </label>
        <button type="button" disabled={isLoading} onClick={() => void loadQueue()}>
          <RefreshCw size={16} /> Refresh
        </button>
      </header>

      {(error || message) && (
        <div
          className={error ? 'review-notice error' : 'review-notice success'}
          role={error ? 'alert' : 'status'}
        >
          {error ?? message}
        </div>
      )}

      <div className="review-layout">
        <aside className="review-overview">
          <div className="review-summary-grid">
            <div><strong>{scopedQueue?.due_count ?? 0}</strong><span>Due now</span></div>
            <div><strong>{scopedQueue?.new_count ?? 0}</strong><span>New</span></div>
            <div><strong>{scopedQueue?.learning_count ?? 0}</strong><span>Learning</span></div>
            <div><strong>{scopedQueue?.review_count ?? 0}</strong><span>Review</span></div>
          </div>
          <section>
            <div className="review-section-title">Due by topic</div>
            <div className="review-topic-list">
              {topicDueCounts.length ? topicDueCounts.map((topic) => (
                <button key={topic.id} type="button" onClick={() => setTopicId(topic.id)}>
                  <span>{topic.title}</span><strong>{topic.count}</strong>
                </button>
              )) : <div className="review-empty-small">No due topics</div>}
            </div>
          </section>
          <section>
            <div className="review-section-title">Session queue</div>
            <div className="review-session-list">
              {(scopedQueue?.items ?? []).map((item, index) => (
                <button
                  key={item.review_item.id}
                  type="button"
                  className={index === currentIndex ? 'selected' : ''}
                  onClick={() => {
                    setCurrentIndex(index)
                    setRevealed(false)
                    setSelfAnswer('')
                    startedAtRef.current = Date.now()
                  }}
                >
                  <strong>{item.card_title}</strong>
                  <span>{item.topic_title ?? 'Unsorted'} · {item.phase}</span>
                </button>
              ))}
            </div>
          </section>
        </aside>

        <section className="review-session" aria-label="Review session">
          {currentItem ? (
            <div className="review-card-session">
              <div className="review-card-context">
                <div>
                  <span>{currentItem.topic_title ?? 'Unsorted'}</span>
                  <span>{currentItem.card_kind}</span>
                  <span>{currentItem.phase}</span>
                </div>
                <button type="button" onClick={() => onOpenWorkspaceCard(currentItem.card_id)}>
                  Open card <ArrowRight size={15} />
                </button>
              </div>
              <div className="review-prompt">
                <BookOpenCheck size={24} />
                <h2>{currentItem.review_item.prompt}</h2>
                <p>{currentItem.card_title}</p>
              </div>
              <textarea
                className="review-self-answer"
                value={selfAnswer}
                onChange={(event) => setSelfAnswer(event.target.value)}
                placeholder="Optional: type what you remember before revealing"
                disabled={revealed}
              />
              {!revealed ? (
                <button className="review-reveal-button" type="button" onClick={() => setRevealed(true)}>
                  <Eye size={17} /> Reveal answer
                </button>
              ) : (
                <>
                  <section className="review-answer">
                    <div className="review-section-title">Expected answer</div>
                    <p>{currentItem.review_item.expected_answer}</p>
                  </section>
                  {renderEvidence(currentItem)}
                  <div className="review-rating-grid">
                    {(['again', 'hard', 'good', 'easy'] as ReviewRating[]).map((rating) => (
                      <button
                        key={rating}
                        type="button"
                        className={`rating-${rating}`}
                        disabled={isRating}
                        onClick={() => void rateCurrent(rating)}
                      >
                        {rating === 'again' && <RotateCcw size={15} />}
                        {rating}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
          ) : (
            <div className="review-complete">
              <BookOpenCheck size={32} />
              <h2>{isLoading ? 'Loading review queue' : 'Review complete'}</h2>
              <p>No review items are due under this filter.</p>
            </div>
          )}
        </section>

        <aside className="review-details">
          {currentItem ? (
            <>
              <div className="review-section-title">Card context</div>
              <h3>{currentItem.card_title}</h3>
              <p>{currentItem.card_summary}</p>
              <dl>
                <div><dt>Due</dt><dd>{formatDue(currentItem.progress.due_at)}</dd></div>
                <div><dt>Reviews</dt><dd>{currentItem.progress.review_count}</dd></div>
                <div><dt>Lapses</dt><dd>{currentItem.progress.lapse_count}</dd></div>
                <div><dt>Source</dt><dd>{formatTime(currentItem.source_start_seconds)} - {formatTime(currentItem.source_end_seconds)}</dd></div>
              </dl>
              <div className="review-tip">
                <Clock3 size={16} />
                Rate the quality of recall, not how familiar the answer looks.
              </div>
            </>
          ) : null}
        </aside>
      </div>
    </div>
  )
}
