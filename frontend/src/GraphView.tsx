import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import {
  ArrowRight,
  Check,
  EyeOff,
  Network,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  X,
} from 'lucide-react'
import ForceGraph2D from 'react-force-graph-2d'
import type {
  CardGraphEdge,
  CardGraphNode,
  CardRelationClassificationResult,
  CardRelationRecomputeResult,
  CardRelationStatus,
  CardRelationType,
  CardSemanticRelationType,
  CourseCardRelationsGraph,
  GraphCourse,
} from './graphTypes'


type GraphViewProps = {
  apiBaseUrl: string
  courses: GraphCourse[]
  selectedCourseId: string | null
  selectedModel: string
  showCourseSelector?: boolean
  initialCardId: string | null
  onSelectCourse: (courseId: string) => void
  onOpenWorkspaceCard: (cardId: string) => void
  onCardRouteChange?: (
    cardId: string | null,
    mode: 'push' | 'replace',
  ) => void
}

type RelationStatusFilter = 'visible' | 'all' | CardRelationStatus

type ManualRelationForm = {
  targetCardId: string
  relationType: CardSemanticRelationType
  explanation: string
}

type RelationEditForm = {
  relationType: CardRelationType
  explanation: string
}

type RelationWithCard = {
  relation: CardGraphEdge
  card: CardGraphNode
  direction: 'outgoing' | 'incoming'
}

type MutationScope = {
  courseId: string
  epoch: number
}


const SEMANTIC_RELATION_TYPES: CardSemanticRelationType[] = [
  'prerequisite',
  'related',
  'example_of',
  'contrast_with',
  'part_of',
]
const RELATION_TYPES: CardRelationType[] = [
  'semantic_similarity',
  ...SEMANTIC_RELATION_TYPES,
]

const STATUS_OPTIONS: Array<{
  value: RelationStatusFilter
  label: string
}> = [
  { value: 'visible', label: 'Suggested + accepted' },
  { value: 'all', label: 'All statuses' },
  { value: 'suggested', label: 'Suggested' },
  { value: 'accepted', label: 'Accepted' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'hidden', label: 'Hidden' },
]


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
      if (typeof payload.detail === 'string') {
        message = payload.detail
      }
    } catch {
      // Keep the HTTP status when the body is not JSON.
    }

    throw new Error(message)
  }

  return response.json() as Promise<T>
}


function relationNodeId(value: string | CardGraphNode): string {
  return typeof value === 'string' ? value : value.id
}


function formatRelationType(value: string): string {
  return value.replaceAll('_', ' ')
}


function formatTime(seconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(seconds))
  const minutes = Math.floor(totalSeconds / 60)
  const remainingSeconds = totalSeconds % 60
  return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`
}


function useContainerWidth() {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [width, setWidth] = useState(720)

  useEffect(() => {
    const element = containerRef.current

    if (!element) {
      return
    }

    const updateWidth = () => {
      setWidth(Math.max(320, Math.floor(element.clientWidth)))
    }
    const observer = new ResizeObserver(updateWidth)
    observer.observe(element)
    updateWidth()

    return () => observer.disconnect()
  }, [])

  return { containerRef, width }
}


export function GraphView({
  apiBaseUrl,
  courses,
  selectedCourseId,
  selectedModel,
  showCourseSelector = true,
  initialCardId,
  onSelectCourse,
  onOpenWorkspaceCard,
  onCardRouteChange,
}: GraphViewProps) {
  const [graphResult, setGraphResult] = useState<{
    courseId: string
    graph: CourseCardRelationsGraph
  } | null>(null)
  const [selectedCardId, setSelectedCardId] = useState<string | null>(
    initialCardId,
  )
  const [searchQuery, setSearchQuery] = useState('')
  const [tagFilter, setTagFilter] = useState('')
  const [statusFilter, setStatusFilter] =
    useState<RelationStatusFilter>('visible')
  const [threshold, setThreshold] = useState(0.72)
  const [topK, setTopK] = useState(5)
  const [isLoading, setIsLoading] = useState(false)
  const [isRecomputing, setIsRecomputing] = useState(false)
  const [busyRelationId, setBusyRelationId] = useState<string | null>(null)
  const [isCreatingRelation, setIsCreatingRelation] = useState(false)
  const [editingRelationId, setEditingRelationId] = useState<string | null>(
    null,
  )
  const [relationEditForm, setRelationEditForm] =
    useState<RelationEditForm | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [manualForm, setManualForm] = useState<ManualRelationForm>({
    targetCardId: '',
    relationType: 'related',
    explanation: '',
  })
  const selectedCourseIdRef = useRef(selectedCourseId)
  const previousInitialCardIdRef = useRef(initialCardId)
  const onCardRouteChangeRef = useRef(onCardRouteChange)
  const loadEpochRef = useRef(0)
  const mutationEpochRef = useRef(0)
  const activeLoadControllerRef = useRef<AbortController | null>(null)
  const { containerRef, width } = useContainerWidth()

  const graph = graphResult?.courseId === selectedCourseId
    ? graphResult.graph
    : null

  useEffect(() => {
    selectedCourseIdRef.current = selectedCourseId
  }, [selectedCourseId])

  useEffect(() => {
    onCardRouteChangeRef.current = onCardRouteChange
  }, [onCardRouteChange])

  const loadGraph = useCallback(async () => {
    const courseId = selectedCourseId
    if (courseId !== selectedCourseIdRef.current) return

    const loadEpoch = loadEpochRef.current + 1
    loadEpochRef.current = loadEpoch
    activeLoadControllerRef.current?.abort()

    if (!courseId) {
      activeLoadControllerRef.current = null
      setGraphResult(null)
      setIsLoading(false)
      return
    }

    const controller = new AbortController()
    activeLoadControllerRef.current = controller
    setIsLoading(true)
    setError(null)

    try {
      const nextGraph = await fetchJson<CourseCardRelationsGraph>(
        apiBaseUrl,
        `/courses/${courseId}/card-relations`,
        { signal: controller.signal },
      )
      if (
        controller.signal.aborted
        || loadEpoch !== loadEpochRef.current
        || courseId !== selectedCourseIdRef.current
      ) {
        return
      }

      setGraphResult({ courseId, graph: nextGraph })
      setSelectedCardId((currentCardId) => {
        if (
          currentCardId &&
          nextGraph.nodes.some((node) => node.id === currentCardId)
        ) {
          return currentCardId
        }

        return null
      })
    } catch (loadError) {
      if (
        controller.signal.aborted
        || loadEpoch !== loadEpochRef.current
        || courseId !== selectedCourseIdRef.current
      ) {
        return
      }
      setError(
        loadError instanceof Error
          ? loadError.message
          : 'Knowledge graph failed to load.',
      )
    } finally {
      if (
        loadEpoch === loadEpochRef.current
        && courseId === selectedCourseIdRef.current
      ) {
        activeLoadControllerRef.current = null
        setIsLoading(false)
      }
    }
  }, [apiBaseUrl, selectedCourseId])

  useEffect(() => {
    mutationEpochRef.current += 1
    setGraphResult(null)
    setSelectedCardId(null)
    setIsRecomputing(false)
    setBusyRelationId(null)
    setIsCreatingRelation(false)
    setEditingRelationId(null)
    setRelationEditForm(null)
    setManualForm({
      targetCardId: '',
      relationType: 'related',
      explanation: '',
    })
    setError(null)
    setMessage(null)
    void loadGraph()

    return () => {
      mutationEpochRef.current += 1
      loadEpochRef.current += 1
      activeLoadControllerRef.current?.abort()
      activeLoadControllerRef.current = null
    }
  }, [loadGraph])

  useEffect(() => {
    const routeCardChanged =
      previousInitialCardIdRef.current !== initialCardId
    previousInitialCardIdRef.current = initialCardId

    if (!graph) {
      return
    }

    if (initialCardId) {
      if (graph.nodes.some((node) => node.id === initialCardId)) {
        setSelectedCardId(initialCardId)
        return
      }

      setSelectedCardId(null)
      onCardRouteChangeRef.current?.(null, 'replace')
      return
    }

    if (routeCardChanged) {
      setSelectedCardId(null)
      return
    }

    setSelectedCardId((currentCardId) => {
      if (
        currentCardId &&
        graph.nodes.some((node) => node.id === currentCardId)
      ) {
        return currentCardId
      }

      return null
    })
  }, [graph, initialCardId])

  const selectCard = useCallback(
    (cardId: string) => {
      if (
        selectedCardId === cardId ||
        !graph?.nodes.some((node) => node.id === cardId)
      ) {
        return
      }

      setSelectedCardId(cardId)
      onCardRouteChangeRef.current?.(cardId, 'push')
    },
    [graph, selectedCardId],
  )

  function captureMutationScope(): MutationScope | null {
    const courseId = selectedCourseId
    if (
      !courseId ||
      courseId !== selectedCourseIdRef.current
    ) {
      return null
    }

    return {
      courseId,
      epoch: mutationEpochRef.current,
    }
  }

  function isMutationScopeCurrent(scope: MutationScope): boolean {
    return (
      scope.epoch === mutationEpochRef.current &&
      scope.courseId === selectedCourseIdRef.current
    )
  }

  const nodesById = useMemo(() => {
    return new Map(graph?.nodes.map((node) => [node.id, node]) ?? [])
  }, [graph])

  const selectedCard = selectedCardId
    ? nodesById.get(selectedCardId) ?? null
    : null

  const filteredEdges = useMemo(() => {
    if (!graph) {
      return []
    }

    return graph.edges.filter((edge) => {
      if (edge.score < threshold) {
        return false
      }

      if (statusFilter === 'all') {
        return true
      }

      if (statusFilter === 'visible') {
        return edge.status === 'suggested' || edge.status === 'accepted'
      }

      return edge.status === statusFilter
    })
  }, [graph, statusFilter, threshold])

  const canvasNodes = useMemo(() => {
    if (!graph) {
      return []
    }

    const normalizedTag = tagFilter.trim().toLowerCase()
    if (!normalizedTag) {
      return graph.nodes
    }

    return graph.nodes.filter((node) =>
      node.tags.some((tag) => tag.toLowerCase().includes(normalizedTag)),
    )
  }, [graph, tagFilter])

  const canvasNodeIds = useMemo(
    () => new Set(canvasNodes.map((node) => node.id)),
    [canvasNodes],
  )

  const canvasEdges = useMemo(() => {
    return filteredEdges.filter(
      (edge) =>
        canvasNodeIds.has(relationNodeId(edge.source)) &&
        canvasNodeIds.has(relationNodeId(edge.target)),
    )
  }, [canvasNodeIds, filteredEdges])

  const cardList = useMemo(() => {
    const query = searchQuery.trim().toLowerCase()
    const normalizedTag = tagFilter.trim().toLowerCase()

    return [...(graph?.nodes ?? [])]
      .filter((node) => {
        const matchesTag =
          !normalizedTag ||
          node.tags.some((tag) => tag.toLowerCase().includes(normalizedTag))
        const matchesQuery =
          !query ||
          [node.title, node.summary, ...node.tags].some((value) =>
            value.toLowerCase().includes(query),
          )
        return matchesTag && matchesQuery
      })
      .sort((left, right) => left.title.localeCompare(right.title))
  }, [graph, searchQuery, tagFilter])

  const selectedRelations = useMemo<RelationWithCard[]>(() => {
    if (!selectedCardId) {
      return []
    }

    const relations = filteredEdges.reduce<RelationWithCard[]>(
      (items, relation) => {
        const sourceId = relationNodeId(relation.source)
        const targetId = relationNodeId(relation.target)

        if (sourceId === selectedCardId) {
          const target = nodesById.get(targetId)
          if (target) {
            items.push({ relation, card: target, direction: 'outgoing' })
          }
        }

        if (targetId === selectedCardId) {
          const source = nodesById.get(sourceId)
          if (source) {
            items.push({ relation, card: source, direction: 'incoming' })
          }
        }

        return items
      },
      [],
    )

    return relations.sort(
      (left, right) => right.relation.score - left.relation.score,
    )
  }, [filteredEdges, nodesById, selectedCardId])

  async function recomputeRelations() {
    const scope = captureMutationScope()
    if (!scope) {
      return
    }

    setIsRecomputing(true)
    setError(null)
    setMessage(null)

    try {
      const result = await fetchJson<CardRelationRecomputeResult>(
        apiBaseUrl,
        `/courses/${scope.courseId}/card-relations/recompute`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ threshold, top_k: topK }),
        },
      )
      if (!isMutationScopeCurrent(scope)) return

      setMessage(
        `${result.relations_written} relations from ${result.embedded_cards} embedded cards.`,
      )
      await loadGraph()
    } catch (recomputeError) {
      if (!isMutationScopeCurrent(scope)) return

      setError(
        recomputeError instanceof Error
          ? recomputeError.message
          : 'Relation recomputation failed.',
      )
    } finally {
      if (isMutationScopeCurrent(scope)) {
        setIsRecomputing(false)
      }
    }
  }

  async function updateRelationStatus(
    relationId: string,
    status: CardRelationStatus,
  ) {
    const scope = captureMutationScope()
    if (!scope) return

    setBusyRelationId(relationId)
    setError(null)
    setMessage(null)

    try {
      await fetchJson<CardGraphEdge>(
        apiBaseUrl,
        `/card-relations/${relationId}`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status }),
        },
      )
      if (!isMutationScopeCurrent(scope)) return

      setMessage(`Relation marked ${status}.`)
      await loadGraph()
    } catch (updateError) {
      if (!isMutationScopeCurrent(scope)) return

      setError(
        updateError instanceof Error
          ? updateError.message
          : 'Relation update failed.',
      )
    } finally {
      if (isMutationScopeCurrent(scope)) {
        setBusyRelationId(null)
      }
    }
  }

  function startEditingRelation(relation: CardGraphEdge) {
    setEditingRelationId(relation.id)
    setRelationEditForm({
      relationType: relation.relation_type,
      explanation: relation.explanation ?? '',
    })
  }

  async function saveRelationEdit(relationId: string) {
    if (!relationEditForm) {
      return
    }
    const scope = captureMutationScope()
    if (!scope) return

    setBusyRelationId(relationId)
    setError(null)
    setMessage(null)

    try {
      await fetchJson<CardGraphEdge>(
        apiBaseUrl,
        `/card-relations/${relationId}`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            relation_type: relationEditForm.relationType,
            explanation: relationEditForm.explanation || null,
          }),
        },
      )
      if (!isMutationScopeCurrent(scope)) return

      setEditingRelationId(null)
      setRelationEditForm(null)
      setMessage('Relation details updated.')
      await loadGraph()
    } catch (updateError) {
      if (!isMutationScopeCurrent(scope)) return

      setError(
        updateError instanceof Error
          ? updateError.message
          : 'Relation edit failed.',
      )
    } finally {
      if (isMutationScopeCurrent(scope)) {
        setBusyRelationId(null)
      }
    }
  }

  async function classifyRelation(relationId: string) {
    const scope = captureMutationScope()
    if (!scope) return

    setBusyRelationId(relationId)
    setError(null)
    setMessage(null)

    try {
      const result = await fetchJson<CardRelationClassificationResult>(
        apiBaseUrl,
        `/card-relations/${relationId}/classify`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ model: selectedModel || null }),
        },
      )
      if (!isMutationScopeCurrent(scope)) return

      setMessage(
        result.classification === 'unclear'
          ? `Qwen marked this relation unclear: ${result.explanation}`
          : `Qwen suggested ${formatRelationType(result.classification)}.`,
      )
      await loadGraph()
    } catch (classificationError) {
      if (!isMutationScopeCurrent(scope)) return

      setError(
        classificationError instanceof Error
          ? classificationError.message
          : 'Relation classification failed.',
      )
    } finally {
      if (isMutationScopeCurrent(scope)) {
        setBusyRelationId(null)
      }
    }
  }

  async function createManualRelation() {
    const scope = captureMutationScope()
    const sourceCardId = selectedCardId
    const targetCardId = manualForm.targetCardId
    if (!scope || !sourceCardId || !targetCardId) {
      return
    }

    setIsCreatingRelation(true)
    setError(null)
    setMessage(null)

    try {
      await fetchJson<CardGraphEdge>(
        apiBaseUrl,
        `/courses/${scope.courseId}/card-relations`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            source_card_id: sourceCardId,
            target_card_id: targetCardId,
            relation_type: manualForm.relationType,
            explanation: manualForm.explanation || null,
          }),
        },
      )
      if (!isMutationScopeCurrent(scope)) return

      setMessage('Manual relation added and accepted.')
      setManualForm({
        targetCardId: '',
        relationType: 'related',
        explanation: '',
      })
      await loadGraph()
    } catch (createError) {
      if (!isMutationScopeCurrent(scope)) return

      setError(
        createError instanceof Error
          ? createError.message
          : 'Manual relation creation failed.',
      )
    } finally {
      if (isMutationScopeCurrent(scope)) {
        setIsCreatingRelation(false)
      }
    }
  }

  const graphData = useMemo(
    () => ({ nodes: canvasNodes, links: canvasEdges }),
    [canvasEdges, canvasNodes],
  )

  return (
    <div className="graph-view">
      <header
        className={`graph-toolbar${
          showCourseSelector ? '' : ' graph-toolbar-embedded'
        }`}
      >
        <div className="graph-title-block">
          <div className="panel-title">Knowledge structure</div>
          <h2>Course graph</h2>
          <p>
            {graph?.nodes.length ?? 0} cards · {graph?.edges.length ?? 0}{' '}
            persisted relations
          </p>
        </div>
        {showCourseSelector && (
          <div className="graph-course-control">
            <label htmlFor="graph-course">Course</label>
            <select
              id="graph-course"
              value={selectedCourseId ?? ''}
              onChange={(event) => onSelectCourse(event.target.value)}
            >
              {courses.map((course) => (
                <option key={course.id} value={course.id}>
                  {course.title} ({course.card_count})
                </option>
              ))}
            </select>
          </div>
        )}
        <div className="graph-recompute-controls">
          <label>
            <span>Threshold {threshold.toFixed(2)}</span>
            <input
              type="range"
              min="0"
              max="1"
              step="0.01"
              value={threshold}
              onChange={(event) => setThreshold(Number(event.target.value))}
            />
          </label>
          <label>
            <span>Top-k</span>
            <input
              type="number"
              min="1"
              max="50"
              value={topK}
              onChange={(event) => setTopK(Number(event.target.value))}
            />
          </label>
          <button
            type="button"
            disabled={!selectedCourseId || isRecomputing}
            onClick={() => void recomputeRelations()}
          >
            <RefreshCw size={16} aria-hidden="true" />
            {isRecomputing ? 'Computing' : 'Recompute'}
          </button>
        </div>
      </header>

      {(error || message) && (
        <div
          className={error ? 'graph-notice error' : 'graph-notice success'}
          role={error ? 'alert' : 'status'}
        >
          <span>{error ?? message}</span>
          <button
            type="button"
            aria-label="Dismiss message"
            title="Dismiss"
            onClick={() => {
              setError(null)
              setMessage(null)
            }}
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>
      )}

      <div className="graph-layout">
        <aside className="graph-card-browser">
          <div className="graph-section-heading">
            <div>
              <strong>Cards</strong>
              <span>{cardList.length} shown</span>
            </div>
            <button
              type="button"
              aria-label="Refresh graph"
              title="Refresh graph"
              disabled={isLoading}
              onClick={() => void loadGraph()}
            >
              <RefreshCw size={16} aria-hidden="true" />
            </button>
          </div>
          <label className="graph-search">
            <Search size={16} aria-hidden="true" />
            <input
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder="Search cards"
            />
          </label>
          <input
            className="graph-tag-filter"
            value={tagFilter}
            onChange={(event) => setTagFilter(event.target.value)}
            placeholder="Filter by tag"
          />
          <select
            className="graph-status-filter"
            value={statusFilter}
            onChange={(event) =>
              setStatusFilter(event.target.value as RelationStatusFilter)
            }
          >
            {STATUS_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <div className="graph-card-list">
            {isLoading ? (
              <div className="graph-empty">Loading graph</div>
            ) : cardList.length ? (
              cardList.map((card) => (
                <button
                  type="button"
                  key={card.id}
                  className={selectedCardId === card.id ? 'selected' : ''}
                  onClick={() => selectCard(card.id)}
                >
                  <strong>{card.title}</strong>
                  <span>{card.summary}</span>
                  <small>
                    {formatTime(card.source_start_seconds)} · {card.content_status}
                  </small>
                </button>
              ))
            ) : (
              <div className="graph-empty">No matching cards</div>
            )}
          </div>
        </aside>

        <section className="graph-canvas-section">
          <div className="graph-section-heading">
            <div>
              <strong>Network</strong>
              <span>
                {canvasNodes.length} nodes · {canvasEdges.length} edges
              </span>
            </div>
            <Network size={18} aria-hidden="true" />
          </div>
          <div className="graph-canvas" ref={containerRef}>
            {canvasNodes.length ? (
              <ForceGraph2D
                graphData={graphData}
                width={width}
                height={520}
                backgroundColor="#f8f8f5"
                nodeLabel={(node) => {
                  const card = node as CardGraphNode
                  return `${card.title}\n${card.summary}`
                }}
                nodeColor={(node) => {
                  const card = node as CardGraphNode
                  if (card.id === selectedCardId) return '#c44b2d'
                  if (card.content_status === 'reviewed') return '#2f6f62'
                  if (card.content_status === 'needs_fix') return '#b87923'
                  return '#4c6173'
                }}
                nodeVal={(node) =>
                  (node as CardGraphNode).id === selectedCardId ? 8 : 4
                }
                linkColor={(link) => {
                  const edge = link as CardGraphEdge
                  if (edge.status === 'accepted') return '#2f6f62'
                  if (edge.status === 'rejected') return '#b7b7b0'
                  return '#7f8f98'
                }}
                linkWidth={(link) => {
                  const edge = link as CardGraphEdge
                  return edge.status === 'accepted'
                    ? 1.5 + edge.score * 2
                    : 0.5 + edge.score * 1.5
                }}
                linkDirectionalArrowLength={3.5}
                linkDirectionalArrowRelPos={0.92}
                cooldownTicks={120}
                onNodeClick={(node) =>
                  selectCard((node as CardGraphNode).id)
                }
              />
            ) : (
              <div className="graph-empty graph-canvas-empty">
                Add cards and embeddings, then recompute relations.
              </div>
            )}
          </div>
          <div className="graph-legend">
            <span><i className="reviewed" /> reviewed</span>
            <span><i className="draft" /> draft</span>
            <span><i className="needs-fix" /> needs fix</span>
            <span><b className="accepted" /> accepted edge</span>
          </div>
        </section>

        <aside className="graph-inspector">
          {selectedCard ? (
            <>
              <div className="graph-selected-card">
                <div className="panel-title">Selected card</div>
                <h2>{selectedCard.title}</h2>
                <p>{selectedCard.summary}</p>
                <div className="graph-card-meta">
                  <span>{selectedCard.content_status}</span>
                  <span>
                    {formatTime(selectedCard.source_start_seconds)} -{' '}
                    {formatTime(selectedCard.source_end_seconds)}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => onOpenWorkspaceCard(selectedCard.id)}
                >
                  Open in workspace
                  <ArrowRight size={16} aria-hidden="true" />
                </button>
              </div>

              <section className="graph-related-section">
                <div className="graph-section-heading">
                  <div>
                    <strong>Related cards</strong>
                    <span>{selectedRelations.length} relations</span>
                  </div>
                </div>
                <div className="graph-relation-list">
                  {selectedRelations.length ? (
                    selectedRelations.map(({ relation, card, direction }) => (
                      <article key={relation.id} className="graph-relation-item">
                        <button
                          type="button"
                          className="graph-related-card"
                          onClick={() => selectCard(card.id)}
                        >
                          <strong>{card.title}</strong>
                          <span>{card.summary}</span>
                        </button>
                        <div className="graph-relation-meta">
                          <span>{formatRelationType(relation.relation_type)}</span>
                          <span>{relation.score.toFixed(3)}</span>
                          <span>{direction}</span>
                          <span className={`relation-status ${relation.status}`}>
                            {relation.status}
                          </span>
                        </div>
                        {editingRelationId === relation.id && relationEditForm ? (
                          <div className="graph-relation-edit-form">
                            <select
                              value={relationEditForm.relationType}
                              onChange={(event) =>
                                setRelationEditForm({
                                  ...relationEditForm,
                                  relationType: event.target.value as CardRelationType,
                                })
                              }
                            >
                              {RELATION_TYPES.map((relationType) => (
                                <option key={relationType} value={relationType}>
                                  {formatRelationType(relationType)}
                                </option>
                              ))}
                            </select>
                            <textarea
                              value={relationEditForm.explanation}
                              onChange={(event) =>
                                setRelationEditForm({
                                  ...relationEditForm,
                                  explanation: event.target.value,
                                })
                              }
                              placeholder="Relation explanation"
                            />
                            <div>
                              <button
                                type="button"
                                disabled={busyRelationId === relation.id}
                                onClick={() => void saveRelationEdit(relation.id)}
                              >
                                Save
                              </button>
                              <button
                                type="button"
                                onClick={() => {
                                  setEditingRelationId(null)
                                  setRelationEditForm(null)
                                }}
                              >
                                Cancel
                              </button>
                            </div>
                          </div>
                        ) : (
                          relation.explanation && <p>{relation.explanation}</p>
                        )}
                        <small>{formatRelationType(relation.method)}</small>
                        <div className="graph-relation-actions">
                          <button
                            type="button"
                            title="Edit relation"
                            disabled={busyRelationId === relation.id}
                            onClick={() => startEditingRelation(relation)}
                          >
                            <Pencil size={14} aria-hidden="true" />
                            Edit
                          </button>
                          <button
                            type="button"
                            title="Accept relation"
                            disabled={busyRelationId === relation.id}
                            onClick={() =>
                              void updateRelationStatus(relation.id, 'accepted')
                            }
                          >
                            <Check size={14} aria-hidden="true" />
                            Accept
                          </button>
                          <button
                            type="button"
                            title="Reject relation"
                            disabled={busyRelationId === relation.id}
                            onClick={() =>
                              void updateRelationStatus(relation.id, 'rejected')
                            }
                          >
                            <X size={14} aria-hidden="true" />
                            Reject
                          </button>
                          <button
                            type="button"
                            title="Hide relation"
                            disabled={busyRelationId === relation.id}
                            onClick={() =>
                              void updateRelationStatus(relation.id, 'hidden')
                            }
                          >
                            <EyeOff size={14} aria-hidden="true" />
                            Hide
                          </button>
                          <button
                            type="button"
                            title="Classify with local Qwen"
                            disabled={busyRelationId === relation.id}
                            onClick={() => void classifyRelation(relation.id)}
                          >
                            <Sparkles size={14} aria-hidden="true" />
                            Classify
                          </button>
                        </div>
                      </article>
                    ))
                  ) : (
                    <div className="graph-empty">No relations under this filter</div>
                  )}
                </div>
              </section>

              <section className="manual-relation-section">
                <div className="graph-section-heading">
                  <div>
                    <strong>Add relation</strong>
                    <span>Source: {selectedCard.title}</span>
                  </div>
                </div>
                <select
                  value={manualForm.targetCardId}
                  onChange={(event) =>
                    setManualForm({
                      ...manualForm,
                      targetCardId: event.target.value,
                    })
                  }
                >
                  <option value="">Select target card</option>
                  {(graph?.nodes ?? [])
                    .filter((card) => card.id !== selectedCard.id)
                    .sort((left, right) => left.title.localeCompare(right.title))
                    .map((card) => (
                      <option key={card.id} value={card.id}>
                        {card.title}
                      </option>
                    ))}
                </select>
                <select
                  value={manualForm.relationType}
                  onChange={(event) =>
                    setManualForm({
                      ...manualForm,
                      relationType: event.target.value as CardSemanticRelationType,
                    })
                  }
                >
                  {SEMANTIC_RELATION_TYPES.map((relationType) => (
                    <option key={relationType} value={relationType}>
                      {formatRelationType(relationType)}
                    </option>
                  ))}
                </select>
                <textarea
                  value={manualForm.explanation}
                  onChange={(event) =>
                    setManualForm({
                      ...manualForm,
                      explanation: event.target.value,
                    })
                  }
                  placeholder="Why are these concepts connected?"
                />
                <button
                  type="button"
                  disabled={isCreatingRelation || !manualForm.targetCardId}
                  onClick={() => void createManualRelation()}
                >
                  <Plus size={16} aria-hidden="true" />
                  {isCreatingRelation ? 'Adding' : 'Add relation'}
                </button>
              </section>
            </>
          ) : (
            <div className="graph-empty">Select a card to inspect relations</div>
          )}
        </aside>
      </div>
    </div>
  )
}
