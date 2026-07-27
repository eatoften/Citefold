import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowRight,
  BookOpenText,
  ChevronDown,
  ChevronRight,
  Link2,
  Plus,
  RefreshCw,
  Save,
  Trash2,
} from 'lucide-react'
import type {
  CourseMapCard,
  CourseMapCourse,
  CourseMapPayload,
  CourseMapTopic,
  TopicRelationType,
} from './courseMapTypes'


type CourseMapViewProps = {
  apiBaseUrl: string
  courses: CourseMapCourse[]
  selectedCourseId: string | null
  showCourseSelector?: boolean
  initialCardId: string | null
  selectedModel: string
  onSelectCourse: (courseId: string) => void
  onOpenWorkspaceCard: (cardId: string) => void
  onOpenStudyCard: (cardId: string) => void
  onCardRouteChange?: (
    cardId: string | null,
    mode: 'push' | 'replace',
  ) => void
}

type TopicEditForm = {
  title: string
  summary: string
  parentTopicId: string
}

type CourseMutationScope = {
  courseId: string
  epoch: number
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


function formatTime(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  const minutes = Math.floor(total / 60)
  return `${minutes}:${(total % 60).toString().padStart(2, '0')}`
}


function TopicTreeNode({
  topic,
  topics,
  cardCount,
  selectedTopicId,
  expandedTopicIds,
  onSelect,
  onToggle,
}: {
  topic: CourseMapTopic
  topics: CourseMapTopic[]
  cardCount: (topicId: string) => number
  selectedTopicId: string | null
  expandedTopicIds: Set<string>
  onSelect: (topicId: string) => void
  onToggle: (topicId: string) => void
}) {
  const children = topics
    .filter((item) => item.parent_topic_id === topic.id && item.status !== 'hidden')
    .sort((left, right) => left.position - right.position)
  const expanded = expandedTopicIds.has(topic.id)

  return (
    <div className="course-map-tree-node">
      <div className={selectedTopicId === topic.id ? 'selected' : ''}>
        <button
          type="button"
          className="course-map-tree-toggle"
          aria-label={expanded ? 'Collapse topic' : 'Expand topic'}
          disabled={children.length === 0}
          onClick={() => onToggle(topic.id)}
        >
          {children.length > 0 ? (
            expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />
          ) : (
            <span />
          )}
        </button>
        <button
          type="button"
          className="course-map-tree-label"
          onClick={() => onSelect(topic.id)}
        >
          <span>{topic.title}</span>
          <small>{cardCount(topic.id)}</small>
        </button>
      </div>
      {expanded && children.length > 0 && (
        <div className="course-map-tree-children">
          {children.map((child) => (
            <TopicTreeNode
              key={child.id}
              topic={child}
              topics={topics}
              cardCount={cardCount}
              selectedTopicId={selectedTopicId}
              expandedTopicIds={expandedTopicIds}
              onSelect={onSelect}
              onToggle={onToggle}
            />
          ))}
        </div>
      )}
    </div>
  )
}


export function CourseMapView({
  apiBaseUrl,
  courses,
  selectedCourseId,
  showCourseSelector = true,
  initialCardId,
  selectedModel,
  onSelectCourse,
  onOpenWorkspaceCard,
  onOpenStudyCard,
  onCardRouteChange,
}: CourseMapViewProps) {
  const [courseMapResult, setCourseMapResult] = useState<{
    courseId: string
    payload: CourseMapPayload
  } | null>(null)
  const [selectedTopicId, setSelectedTopicId] = useState<string | null>(null)
  const [expandedTopicIds, setExpandedTopicIds] = useState<Set<string>>(new Set())
  const [newTopicTitle, setNewTopicTitle] = useState('')
  const [newTopicParentId, setNewTopicParentId] = useState('')
  const [editForm, setEditForm] = useState<TopicEditForm | null>(null)
  const [relationTargetId, setRelationTargetId] = useState('')
  const [relationType, setRelationType] =
    useState<TopicRelationType>('prerequisite')
  const [relationExplanation, setRelationExplanation] = useState('')
  const [suggestedTopicCount, setSuggestedTopicCount] = useState(6)
  const [useLlmNames, setUseLlmNames] = useState(true)
  const [isLoading, setIsLoading] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [suggestionMetrics, setSuggestionMetrics] = useState<{
    meanCoherence: number | null
    singletonCount: number
    largestSize: number
    clusterSizes: number[]
  } | null>(null)
  const [mergeSourceTopicId, setMergeSourceTopicId] = useState('')
  const [splitTopicTitle, setSplitTopicTitle] = useState('')
  const [splitCardIds, setSplitCardIds] = useState<Set<string>>(new Set())
  const [selectedCardId, setSelectedCardId] = useState<string | null>(
    initialCardId,
  )
  const selectedCourseIdRef = useRef(selectedCourseId)
  const courseScopeEpochRef = useRef(0)
  const loadEpochRef = useRef(0)
  const activeLoadControllerRef = useRef<AbortController | null>(null)
  const onCardRouteChangeRef = useRef(onCardRouteChange)

  const courseMap = courseMapResult?.courseId === selectedCourseId
    ? courseMapResult.payload
    : null

  useEffect(() => {
    selectedCourseIdRef.current = selectedCourseId
    courseScopeEpochRef.current += 1

    return () => {
      courseScopeEpochRef.current += 1
    }
  }, [selectedCourseId])

  useEffect(() => {
    onCardRouteChangeRef.current = onCardRouteChange
  }, [onCardRouteChange])

  function getActiveCourseScope(
    courseId: string | null,
  ): CourseMutationScope | null {
    if (
      !courseId ||
      selectedCourseIdRef.current !== courseId
    ) {
      return null
    }
    return {
      courseId,
      epoch: courseScopeEpochRef.current,
    }
  }

  function isCourseScopeActive(
    scope: CourseMutationScope,
  ): boolean {
    return (
      selectedCourseIdRef.current === scope.courseId &&
      courseScopeEpochRef.current === scope.epoch
    )
  }

  const loadCourseMap = useCallback(async () => {
    const courseId = selectedCourseId
    if (courseId !== selectedCourseIdRef.current) return

    const loadEpoch = loadEpochRef.current + 1
    loadEpochRef.current = loadEpoch
    activeLoadControllerRef.current?.abort()

    if (!courseId) {
      activeLoadControllerRef.current = null
      setCourseMapResult(null)
      setIsLoading(false)
      return
    }

    const controller = new AbortController()
    activeLoadControllerRef.current = controller
    setIsLoading(true)
    setError(null)
    try {
      const payload = await fetchJson<CourseMapPayload>(
        apiBaseUrl,
        `/courses/${courseId}/map`,
        { signal: controller.signal },
      )
      if (
        controller.signal.aborted
        || loadEpoch !== loadEpochRef.current
        || courseId !== selectedCourseIdRef.current
      ) {
        return
      }

      setCourseMapResult({ courseId, payload })
      const initialCard = initialCardId
        ? payload.cards.find((card) => card.id === initialCardId)
        : null
      const initialMembership = initialCard
        ? payload.memberships.find((item) => item.card_id === initialCardId)
        : null
      if (initialCardId && !initialCard) {
        onCardRouteChangeRef.current?.(null, 'replace')
      }
      setSelectedCardId((current) => {
        if (initialCard) return initialCard.id
        if (initialCardId) return null
        if (current && payload.cards.some((card) => card.id === current)) {
          return current
        }
        return null
      })
      setSelectedTopicId((current) => {
        if (initialMembership) return initialMembership.topic_id
        if (current && payload.topics.some((topic) => topic.id === current)) {
          return current
        }
        return payload.topics.find((topic) => !topic.is_system)?.id
          ?? payload.topics[0]?.id
          ?? null
      })
      setExpandedTopicIds(
        new Set(payload.topics.filter((topic) => topic.depth < 2).map((topic) => topic.id)),
      )
    } catch (loadError) {
      if (
        controller.signal.aborted
        || loadEpoch !== loadEpochRef.current
        || courseId !== selectedCourseIdRef.current
      ) {
        return
      }
      setError(loadError instanceof Error ? loadError.message : 'Course map failed.')
    } finally {
      if (
        loadEpoch === loadEpochRef.current
        && courseId === selectedCourseIdRef.current
      ) {
        activeLoadControllerRef.current = null
        setIsLoading(false)
      }
    }
  }, [apiBaseUrl, initialCardId, selectedCourseId])

  useEffect(() => {
    setCourseMapResult(null)
    setSelectedTopicId(null)
    setSelectedCardId(null)
    setExpandedTopicIds(new Set())
    setNewTopicTitle('')
    setNewTopicParentId('')
    setRelationTargetId('')
    setRelationExplanation('')
    setSuggestionMetrics(null)
    setMergeSourceTopicId('')
    setSplitTopicTitle('')
    setSplitCardIds(new Set())
    setError(null)
    setMessage(null)
    setIsSaving(false)
    void loadCourseMap()

    return () => {
      loadEpochRef.current += 1
      activeLoadControllerRef.current?.abort()
      activeLoadControllerRef.current = null
    }
  }, [loadCourseMap])

  const selectedTopic = courseMap?.topics.find(
    (topic) => topic.id === selectedTopicId,
  ) ?? null
  const selectedCoverage = courseMap?.coverage.topic_coverage.find(
    (item) => item.topic_id === selectedTopicId,
  ) ?? null

  useEffect(() => {
    if (!selectedTopic) {
      setEditForm(null)
      return
    }
    setEditForm({
      title: selectedTopic.title,
      summary: selectedTopic.summary ?? '',
      parentTopicId: selectedTopic.parent_topic_id ?? '',
    })
    setSplitCardIds(new Set())
    setSplitTopicTitle('')
    setMergeSourceTopicId('')
  }, [selectedTopic])

  const membershipByCardId = useMemo(() => {
    return new Map(
      (courseMap?.memberships ?? [])
        .filter((item) => item.role === 'primary' && item.status === 'accepted')
        .map((item) => [item.card_id, item]),
    )
  }, [courseMap])

  const selectedCards = useMemo(() => {
    if (!courseMap || !selectedTopicId) return []
    if (selectedTopic?.status === 'suggested') {
      const suggestedCardIds = new Set(
        courseMap.memberships
          .filter(
            (item) =>
              item.topic_id === selectedTopicId && item.status === 'suggested',
          )
          .map((item) => item.card_id),
      )
      return courseMap.cards.filter((card) => suggestedCardIds.has(card.id))
    }
    return courseMap.cards.filter(
      (card) => membershipByCardId.get(card.id)?.topic_id === selectedTopicId,
    )
  }, [courseMap, membershipByCardId, selectedTopic, selectedTopicId])

  const rootTopics = useMemo(() => {
    return (courseMap?.topics ?? [])
      .filter((topic) => topic.parent_topic_id === null && topic.status !== 'hidden')
      .sort((left, right) => left.position - right.position)
  }, [courseMap])

  const cardCount = useCallback(
    (topicId: string) => {
      if (!courseMap) return 0
      const topic = courseMap.topics.find((item) => item.id === topicId)
      if (topic?.status === 'suggested') {
        return courseMap.memberships.filter(
          (item) => item.topic_id === topicId && item.status === 'suggested',
        ).length
      }
      return courseMap.cards.filter(
        (card) => membershipByCardId.get(card.id)?.topic_id === topicId,
      ).length
    },
    [courseMap, membershipByCardId],
  )

  async function createTopic() {
    const scope = getActiveCourseScope(selectedCourseId)
    if (!scope || !newTopicTitle.trim()) return
    setIsSaving(true)
    setError(null)
    try {
      await fetchJson(
        apiBaseUrl,
        `/courses/${scope.courseId}/topics`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: newTopicTitle,
            parent_topic_id: newTopicParentId || null,
          }),
        },
      )
      if (!isCourseScopeActive(scope)) return
      setNewTopicTitle('')
      setMessage('Topic created.')
      await loadCourseMap()
    } catch (saveError) {
      if (isCourseScopeActive(scope)) {
        setError(saveError instanceof Error ? saveError.message : 'Topic create failed.')
      }
    } finally {
      if (isCourseScopeActive(scope)) {
        setIsSaving(false)
      }
    }
  }

  async function saveTopic() {
    if (!selectedTopic || !editForm || selectedTopic.is_system) return
    const scope = getActiveCourseScope(selectedTopic.course_id)
    if (!scope) return
    setIsSaving(true)
    setError(null)
    try {
      await fetchJson(apiBaseUrl, `/topics/${selectedTopic.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: editForm.title,
          summary: editForm.summary || null,
          parent_topic_id: editForm.parentTopicId || null,
        }),
      })
      if (!isCourseScopeActive(scope)) return
      setMessage('Topic updated.')
      await loadCourseMap()
    } catch (saveError) {
      if (isCourseScopeActive(scope)) {
        setError(saveError instanceof Error ? saveError.message : 'Topic update failed.')
      }
    } finally {
      if (isCourseScopeActive(scope)) {
        setIsSaving(false)
      }
    }
  }

  async function deleteTopic() {
    if (!selectedTopic || selectedTopic.is_system) return
    const scope = getActiveCourseScope(selectedTopic.course_id)
    if (!scope) return
    if (!window.confirm(`Delete topic "${selectedTopic.title}"? Cards will move to Unsorted.`)) {
      return
    }
    setIsSaving(true)
    try {
      await fetchJson<void>(apiBaseUrl, `/topics/${selectedTopic.id}`, {
        method: 'DELETE',
      })
      if (!isCourseScopeActive(scope)) return
      setSelectedTopicId(null)
      setMessage('Topic deleted; its cards moved to Unsorted.')
      await loadCourseMap()
    } catch (deleteError) {
      if (isCourseScopeActive(scope)) {
        setError(deleteError instanceof Error ? deleteError.message : 'Topic delete failed.')
      }
    } finally {
      if (isCourseScopeActive(scope)) {
        setIsSaving(false)
      }
    }
  }

  async function moveCard(card: CourseMapCard, topicId: string) {
    const scope = getActiveCourseScope(selectedCourseId)
    if (!scope || !courseMap?.cards.some((item) => item.id === card.id)) return
    setIsSaving(true)
    setError(null)
    try {
      await fetchJson(apiBaseUrl, `/cards/${card.id}/primary-topic`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ topic_id: topicId }),
      })
      if (!isCourseScopeActive(scope)) return
      setMessage(`Moved ${card.title}.`)
      await loadCourseMap()
    } catch (moveError) {
      if (isCourseScopeActive(scope)) {
        setError(moveError instanceof Error ? moveError.message : 'Card move failed.')
      }
    } finally {
      if (isCourseScopeActive(scope)) {
        setIsSaving(false)
      }
    }
  }

  async function addTopicRelation() {
    if (!selectedTopic || !relationTargetId) return
    const scope = getActiveCourseScope(selectedTopic.course_id)
    if (!scope) return
    setIsSaving(true)
    setError(null)
    try {
      await fetchJson(apiBaseUrl, `/courses/${scope.courseId}/topic-relations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source_topic_id: selectedTopic.id,
          target_topic_id: relationTargetId,
          relation_type: relationType,
          explanation: relationExplanation || null,
        }),
      })
      if (!isCourseScopeActive(scope)) return
      setRelationTargetId('')
      setRelationExplanation('')
      setMessage('Topic relation added.')
      await loadCourseMap()
    } catch (relationError) {
      if (isCourseScopeActive(scope)) {
        setError(relationError instanceof Error ? relationError.message : 'Relation failed.')
      }
    } finally {
      if (isCourseScopeActive(scope)) {
        setIsSaving(false)
      }
    }
  }

  async function suggestTopics() {
    const scope = getActiveCourseScope(selectedCourseId)
    if (!scope) return
    setIsSaving(true)
    setError(null)
    setMessage(null)
    try {
      const result = await fetchJson<{
        suggested_topics: CourseMapTopic[]
        suggested_memberships: number
        warning: string | null
        mean_coherence: number | null
        singleton_topic_count: number
        largest_topic_size: number
        cluster_sizes: number[]
      }>(apiBaseUrl, `/courses/${scope.courseId}/topics/suggest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target_topic_count: suggestedTopicCount,
          use_local_llm: useLlmNames,
          model: selectedModel || null,
        }),
      })
      if (!isCourseScopeActive(scope)) return
      setSuggestionMetrics({
        meanCoherence: result.mean_coherence,
        singletonCount: result.singleton_topic_count,
        largestSize: result.largest_topic_size,
        clusterSizes: result.cluster_sizes,
      })
      setMessage(
        result.warning
          ? `${result.suggested_topics.length} suggestions. ${result.warning}`
          : `${result.suggested_topics.length} topic suggestions for ${result.suggested_memberships} cards.`,
      )
      await loadCourseMap()
      if (
        isCourseScopeActive(scope) &&
        result.suggested_topics[0]
      ) {
        setSelectedTopicId(result.suggested_topics[0].id)
      }
    } catch (suggestionError) {
      if (isCourseScopeActive(scope)) {
        setError(
          suggestionError instanceof Error
            ? suggestionError.message
            : 'Topic suggestion failed.',
        )
      }
    } finally {
      if (isCourseScopeActive(scope)) {
        setIsSaving(false)
      }
    }
  }

  async function acceptSuggestion() {
    if (!selectedTopic || selectedTopic.status !== 'suggested') return
    const scope = getActiveCourseScope(selectedTopic.course_id)
    if (!scope) return
    setIsSaving(true)
    setError(null)
    try {
      await fetchJson(apiBaseUrl, `/topics/${selectedTopic.id}/accept`, {
        method: 'POST',
      })
      if (!isCourseScopeActive(scope)) return
      setMessage(`Accepted ${selectedTopic.title}.`)
      await loadCourseMap()
    } catch (acceptError) {
      if (isCourseScopeActive(scope)) {
        setError(acceptError instanceof Error ? acceptError.message : 'Accept failed.')
      }
    } finally {
      if (isCourseScopeActive(scope)) {
        setIsSaving(false)
      }
    }
  }

  async function mergeTopic() {
    if (!selectedTopic || !mergeSourceTopicId) return
    const scope = getActiveCourseScope(selectedTopic.course_id)
    if (!scope) return
    setIsSaving(true)
    setError(null)
    try {
      await fetchJson(apiBaseUrl, `/topics/${selectedTopic.id}/merge`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_topic_ids: [mergeSourceTopicId] }),
      })
      if (!isCourseScopeActive(scope)) return
      setMessage('Topics merged.')
      await loadCourseMap()
    } catch (mergeError) {
      if (isCourseScopeActive(scope)) {
        setError(mergeError instanceof Error ? mergeError.message : 'Topic merge failed.')
      }
    } finally {
      if (isCourseScopeActive(scope)) {
        setIsSaving(false)
      }
    }
  }

  async function splitTopic() {
    if (!selectedTopic || !splitTopicTitle.trim() || !splitCardIds.size) return
    const scope = getActiveCourseScope(selectedTopic.course_id)
    if (!scope) return
    setIsSaving(true)
    setError(null)
    try {
      const created = await fetchJson<CourseMapTopic>(
        apiBaseUrl,
        `/topics/${selectedTopic.id}/split`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: splitTopicTitle,
            card_ids: [...splitCardIds],
          }),
        },
      )
      if (
        !isCourseScopeActive(scope) ||
        created.course_id !== scope.courseId
      ) {
        return
      }
      setMessage(`Created ${created.title} with ${splitCardIds.size} cards.`)
      await loadCourseMap()
      if (isCourseScopeActive(scope)) {
        setSelectedTopicId(created.id)
      }
    } catch (splitError) {
      if (isCourseScopeActive(scope)) {
        setError(splitError instanceof Error ? splitError.message : 'Topic split failed.')
      }
    } finally {
      if (isCourseScopeActive(scope)) {
        setIsSaving(false)
      }
    }
  }

  return (
    <div className="course-map-view">
      <header className="course-map-toolbar">
        <div>
          <div className="panel-title">Learning structure</div>
          <h2>Course map</h2>
          <p>Organize grounded cards into a stable topic hierarchy.</p>
        </div>
        {showCourseSelector && (
          <label>
            <span>Course</span>
            <select
              value={selectedCourseId ?? ''}
              onChange={(event) => onSelectCourse(event.target.value)}
            >
              {courses.map((course) => (
                <option key={course.id} value={course.id}>
                  {course.title} ({course.card_count})
                </option>
              ))}
            </select>
          </label>
        )}
        <button type="button" disabled={isLoading} onClick={() => void loadCourseMap()}>
          <RefreshCw size={16} /> Refresh
        </button>
      </header>

      {(error || message) && (
        <div
          className={error ? 'course-map-notice error' : 'course-map-notice success'}
          role={error ? 'alert' : 'status'}
        >
          {error ?? message}
        </div>
      )}

      <div className="course-map-layout">
        <aside className="course-map-tree-panel">
          <div className="course-map-coverage-grid">
            <div><strong>{courseMap?.coverage.total_cards ?? 0}</strong><span>Cards</span></div>
            <div><strong>{courseMap?.coverage.learning_document_count ?? 0}</strong><span>Study docs</span></div>
            <div><strong>{courseMap?.coverage.due_review_item_count ?? 0}</strong><span>Due</span></div>
            <div><strong>{courseMap?.coverage.unsorted_card_count ?? 0}</strong><span>Unsorted</span></div>
          </div>
          <div className="course-map-section-heading">
            <div><strong>Topics</strong><span>{courseMap?.topics.length ?? 0}</span></div>
          </div>
          <div className="course-map-tree">
            {rootTopics.map((topic) => (
              <TopicTreeNode
                key={topic.id}
                topic={topic}
                topics={courseMap?.topics ?? []}
                cardCount={cardCount}
                selectedTopicId={selectedTopicId}
                expandedTopicIds={expandedTopicIds}
                onSelect={setSelectedTopicId}
                onToggle={(topicId) => {
                  setExpandedTopicIds((current) => {
                    const next = new Set(current)
                    if (next.has(topicId)) next.delete(topicId)
                    else next.add(topicId)
                    return next
                  })
                }}
              />
            ))}
          </div>
          <div className="course-map-suggestions">
            <div className="course-map-section-heading">
              <div><strong>Auto organize</strong><span>Unsorted cards only</span></div>
            </div>
            <label>
              <span>Suggested topics</span>
              <input
                type="number"
                min="2"
                max="20"
                value={suggestedTopicCount}
                onChange={(event) => setSuggestedTopicCount(Number(event.target.value))}
              />
            </label>
            <label className="course-map-llm-toggle">
              <input
                type="checkbox"
                checked={useLlmNames}
                onChange={(event) => setUseLlmNames(event.target.checked)}
              />
              Name clusters with local Qwen
            </label>
            <button type="button" disabled={isSaving} onClick={() => void suggestTopics()}>
              Suggest structure
            </button>
            {suggestionMetrics && (
              <div className="course-map-cluster-metrics">
                <span>coherence {suggestionMetrics.meanCoherence?.toFixed(2) ?? '-'}</span>
                <span>{suggestionMetrics.singletonCount} singletons</span>
                <span>largest {suggestionMetrics.largestSize}</span>
                <span>{suggestionMetrics.clusterSizes.join(' / ')}</span>
              </div>
            )}
          </div>
          <div className="course-map-create-topic">
            <input
              value={newTopicTitle}
              onChange={(event) => setNewTopicTitle(event.target.value)}
              placeholder="New topic"
            />
            <select
              value={newTopicParentId}
              onChange={(event) => setNewTopicParentId(event.target.value)}
            >
              <option value="">Top level</option>
              {(courseMap?.topics ?? []).filter((topic) => topic.depth < 3).map((topic) => (
                <option key={topic.id} value={topic.id}>{topic.title}</option>
              ))}
            </select>
            <button
              type="button"
              disabled={isSaving || !newTopicTitle.trim()}
              onClick={() => void createTopic()}
            >
              <Plus size={15} /> Add topic
            </button>
          </div>
        </aside>

        <section
          className="course-map-content"
          aria-label="Course map workspace"
        >
          {selectedTopic ? (
            <>
              <div className="course-map-topic-header">
                <div>
                  <div className="panel-title">Topic</div>
                  <h2>{selectedTopic.title}</h2>
                  <p>{selectedTopic.summary ?? 'No topic summary yet.'}</p>
                </div>
                <div>
                  <span>{selectedCards.length} cards</span>
                  <span>{selectedCards.reduce((sum, card) => sum + card.review_item_count, 0)} recall items</span>
                  <span>{selectedCoverage?.due_review_item_count ?? 0} due</span>
                  <span>{selectedCoverage?.learning_document_count ?? 0} study docs</span>
                  {selectedTopic.status === 'suggested' && <span>suggested</span>}
                </div>
              </div>
              <div className="course-map-card-list">
                {selectedCards.length ? selectedCards.map((card) => (
                  <article key={card.id} className="course-map-card">
                    <div>
                      {selectedTopic.status === 'accepted' && !selectedTopic.is_system && (
                        <label className="course-map-card-select">
                          <input
                            type="checkbox"
                            checked={splitCardIds.has(card.id)}
                            onChange={(event) => setSplitCardIds((current) => {
                              const next = new Set(current)
                              if (event.target.checked) next.add(card.id)
                              else next.delete(card.id)
                              return next
                            })}
                          />
                          <span>split</span>
                        </label>
                      )}
                      <span>{card.card_kind}</span>
                      <span>{card.content_status}</span>
                    </div>
                    <h3>{card.title}</h3>
                    <p>{card.summary}</p>
                    <small>
                      {card.source_video ?? 'video'} · {formatTime(card.source_start_seconds)} ·{' '}
                      {card.review_item_count} recall
                      {' '}· {card.learning_document_count} docs
                    </small>
                    <div className="course-map-card-actions">
                      <button
                        type="button"
                        aria-label={
                          selectedCardId === card.id
                            ? `${card.title} selected`
                            : `Select ${card.title}`
                        }
                        aria-pressed={selectedCardId === card.id}
                        onClick={() => {
                          if (selectedCardId === card.id) return
                          setSelectedCardId(card.id)
                          onCardRouteChangeRef.current?.(
                            card.id,
                            'push',
                          )
                        }}
                      >
                        {selectedCardId === card.id
                          ? 'Selected card'
                          : 'Select card'}
                      </button>
                      <select
                        value={membershipByCardId.get(card.id)?.topic_id ?? ''}
                        disabled={isSaving}
                        onChange={(event) => void moveCard(card, event.target.value)}
                      >
                        {(courseMap?.topics ?? []).filter((topic) => topic.status !== 'hidden').map((topic) => (
                          <option key={topic.id} value={topic.id}>{topic.title}</option>
                        ))}
                      </select>
                      <button type="button" onClick={() => onOpenWorkspaceCard(card.id)}>
                        Open <ArrowRight size={15} />
                      </button>
                      <button type="button" onClick={() => onOpenStudyCard(card.id)}>
                        Study <BookOpenText size={15} />
                      </button>
                    </div>
                  </article>
                )) : <div className="course-map-empty">No cards in this topic.</div>}
              </div>
            </>
          ) : (
            <div className="course-map-empty">Select a topic.</div>
          )}
        </section>

        <aside className="course-map-inspector">
          {selectedTopic && editForm ? (
            <>
              <section>
                <div className="course-map-section-heading">
                  <div><strong>Restructure</strong><span>Manual corrections</span></div>
                </div>
                <label className="course-map-field-label">Merge another topic into this one</label>
                <select value={mergeSourceTopicId} onChange={(event) => setMergeSourceTopicId(event.target.value)}>
                  <option value="">Select source topic</option>
                  {(courseMap?.topics ?? [])
                    .filter((topic) => topic.id !== selectedTopic.id && !topic.is_system && topic.status === 'accepted')
                    .map((topic) => <option key={topic.id} value={topic.id}>{topic.title}</option>)}
                </select>
                <button type="button" disabled={isSaving || !mergeSourceTopicId || selectedTopic.status !== 'accepted'} onClick={() => void mergeTopic()}>
                  Merge into {selectedTopic.title}
                </button>
                <label className="course-map-field-label">Split selected cards into a sibling topic</label>
                <input value={splitTopicTitle} onChange={(event) => setSplitTopicTitle(event.target.value)} placeholder="New topic title" />
                <button type="button" disabled={isSaving || !splitTopicTitle.trim() || splitCardIds.size === 0 || selectedTopic.is_system} onClick={() => void splitTopic()}>
                  Split {splitCardIds.size} selected cards
                </button>
              </section>

              <section>
                <div className="course-map-section-heading">
                  <div><strong>Topic details</strong><span>{selectedTopic.method}</span></div>
                </div>
                <input
                  value={editForm.title}
                  disabled={selectedTopic.is_system}
                  onChange={(event) => setEditForm({ ...editForm, title: event.target.value })}
                />
                <textarea
                  value={editForm.summary}
                  onChange={(event) => setEditForm({ ...editForm, summary: event.target.value })}
                  placeholder="Topic summary"
                />
                <select
                  value={editForm.parentTopicId}
                  disabled={selectedTopic.is_system}
                  onChange={(event) => setEditForm({ ...editForm, parentTopicId: event.target.value })}
                >
                  <option value="">Top level</option>
                  {(courseMap?.topics ?? [])
                    .filter((topic) => topic.id !== selectedTopic.id && topic.depth < 3)
                    .map((topic) => (
                      <option key={topic.id} value={topic.id}>{topic.title}</option>
                    ))}
                </select>
                <div className="course-map-inspector-actions">
                  {selectedTopic.status === 'suggested' && (
                    <button type="button" disabled={isSaving} onClick={() => void acceptSuggestion()}>
                      Accept suggestion
                    </button>
                  )}
                  <button type="button" disabled={isSaving || selectedTopic.is_system} onClick={() => void saveTopic()}>
                    <Save size={15} /> Save
                  </button>
                  <button className="danger-button" type="button" disabled={isSaving || selectedTopic.is_system} onClick={() => void deleteTopic()}>
                    <Trash2 size={15} /> Delete
                  </button>
                </div>
              </section>

              <section>
                <div className="course-map-section-heading">
                  <div><strong>Topic relations</strong><span>Directed</span></div>
                  <Link2 size={16} />
                </div>
                <div className="course-map-topic-relations">
                  {(courseMap?.topic_relations ?? [])
                    .filter((relation) => relation.source_topic_id === selectedTopic.id || relation.target_topic_id === selectedTopic.id)
                    .map((relation) => {
                      const otherId = relation.source_topic_id === selectedTopic.id
                        ? relation.target_topic_id
                        : relation.source_topic_id
                      const other = courseMap?.topics.find((topic) => topic.id === otherId)
                      return (
                        <div key={relation.id}>
                          <strong>{relation.relation_type.replaceAll('_', ' ')}</strong>
                          <span>{other?.title ?? 'Unknown topic'}</span>
                          {relation.explanation && <p>{relation.explanation}</p>}
                        </div>
                      )
                    })}
                </div>
                <select value={relationTargetId} onChange={(event) => setRelationTargetId(event.target.value)}>
                  <option value="">Select target topic</option>
                  {(courseMap?.topics ?? []).filter((topic) => topic.id !== selectedTopic.id).map((topic) => (
                    <option key={topic.id} value={topic.id}>{topic.title}</option>
                  ))}
                </select>
                <select value={relationType} onChange={(event) => setRelationType(event.target.value as TopicRelationType)}>
                  <option value="prerequisite">prerequisite</option>
                  <option value="related">related</option>
                  <option value="contrast_with">contrast with</option>
                </select>
                <textarea
                  value={relationExplanation}
                  onChange={(event) => setRelationExplanation(event.target.value)}
                  placeholder="Why are these topics connected?"
                />
                <button type="button" disabled={isSaving || !relationTargetId} onClick={() => void addTopicRelation()}>
                  <Plus size={15} /> Add relation
                </button>
              </section>
            </>
          ) : <div className="course-map-empty">Select a topic to edit.</div>}
        </aside>
      </div>
    </div>
  )
}
