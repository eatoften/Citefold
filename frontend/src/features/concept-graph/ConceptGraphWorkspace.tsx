import {
  useEffect,
  useId,
  useRef,
  useState,
} from 'react'
import {
  ConceptGraphApiError,
  getCurrentGraphVersion,
  getLearningPath,
  getLocalGraph,
  getRelationshipTrace,
  listAllPublishedConcepts,
} from './conceptGraphApi'
import { ConceptGraphDraftReview } from './ConceptGraphDraftReview'
import type {
  GraphEvidenceSelection,
  GraphVersionMetadata,
  LearningPathResult,
  LocalGraphResult,
  PublishedConcept,
  PublishedEvidence,
  PublishedRelation,
  RelationshipTraceResult,
} from './conceptGraphTypes'
import './ConceptGraphWorkspace.css'

export type ConceptGraphWorkspaceProps = {
  apiBaseUrl: string
  selectedCourseId: string | null
  onOpenEvidence?: (
    selection: GraphEvidenceSelection,
    trigger: HTMLButtonElement,
  ) => void
}

type View = 'overview' | 'local' | 'trace' | 'learning'
type WorkspaceMode = 'published' | 'draft'
type PathResult =
  | LocalGraphResult
  | RelationshipTraceResult
  | LearningPathResult
type InspectorSelection =
  | { kind: 'concept'; value: PublishedConcept }
  | { kind: 'relation'; value: PublishedRelation }

type GraphState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'empty'; message: string }
  | { kind: 'stale'; message: string; version: GraphVersionMetadata | null }
  | { kind: 'error'; message: string }
  | {
      kind: 'ready'
      courseId: string
      version: GraphVersionMetadata
      concepts: PublishedConcept[]
    }

type PathState =
  | { kind: 'idle' }
  | { kind: 'loading'; view: Exclude<View, 'overview'> }
  | { kind: 'limits'; message: string }
  | { kind: 'stale'; message: string }
  | { kind: 'error'; message: string }

const VIEWS: Array<{ id: View; label: string }> = [
  { id: 'overview', label: 'Overview' },
  { id: 'local', label: 'Local' },
  { id: 'trace', label: 'Trace' },
  { id: 'learning', label: 'Learning' },
]

function relationLabel(value: string): string {
  return value.replaceAll('_', ' ')
}

function conceptName(
  concepts: PublishedConcept[],
  conceptId: string,
): string {
  return concepts.find((item) => item.concept_id === conceptId)
    ?.preferred_name ?? conceptId
}

function VersionIdentity({ version }: { version: GraphVersionMetadata }) {
  return (
    <dl className="cg-version" aria-label="Published graph identity">
      <div>
        <dt>Version</dt>
        <dd>v{version.version_number}</dd>
      </div>
      <div>
        <dt>Content hash</dt>
        <dd><code>{version.content_hash}</code></dd>
      </div>
    </dl>
  )
}

function ConceptButton({
  concept,
  detail,
  onSelect,
}: {
  concept: PublishedConcept
  detail?: string
  onSelect: (concept: PublishedConcept) => void
}) {
  return (
    <button
      type="button"
      className="cg-concept-button"
      onClick={() => onSelect(concept)}
    >
      <strong>{concept.preferred_name}</strong>
      {detail && <span>{detail}</span>}
      <small>{concept.short_definition}</small>
    </button>
  )
}

function ConceptSelect({
  label,
  value,
  concepts,
  onChange,
}: {
  label: string
  value: string
  concepts: PublishedConcept[]
  onChange: (conceptId: string) => void
}) {
  return (
    <label>
      {label}
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {concepts.map((concept) => (
          <option key={concept.concept_id} value={concept.concept_id}>
            {concept.preferred_name}
          </option>
        ))}
      </select>
    </label>
  )
}

function evidenceSelection(
  version: GraphVersionMetadata,
  owner: InspectorSelection,
  evidence: PublishedEvidence,
): GraphEvidenceSelection {
  return {
    identity: {
      course_id: version.course_id,
      graph_version: version.version_number,
      graph_content_hash: version.content_hash,
      owner: owner.kind === 'concept'
        ? {
            kind: 'concept',
            concept_id: owner.value.concept_id,
            concept_revision: owner.value.concept_revision,
          }
        : {
            kind: 'relation',
            relation_id: owner.value.relation_id,
            relation_revision: owner.value.relation_revision,
          },
      evidence_id: evidence.evidence_id,
      source_id: evidence.source_id,
      chunk_id: evidence.chunk_id,
      chunk_text_hash: evidence.chunk_text_hash,
      projection_generation_id: evidence.projection_generation_id,
    },
    evidence,
  }
}

function EvidenceList({
  owner,
  version,
  onOpenEvidence,
}: {
  owner: InspectorSelection
  version: GraphVersionMetadata
  onOpenEvidence?: ConceptGraphWorkspaceProps['onOpenEvidence']
}) {
  const evidence = owner.value.evidence
  if (evidence.length === 0) {
    return <p className="cg-muted">No evidence is attached.</p>
  }
  return (
    <ol className="cg-evidence-list">
      {evidence.map((item) => (
        <li key={item.evidence_id}>
          <blockquote>{item.quote}</blockquote>
          <button
            type="button"
            disabled={!onOpenEvidence}
            onClick={(event) => onOpenEvidence?.(
              evidenceSelection(version, owner, item),
              event.currentTarget,
            )}
          >
            Open evidence {item.ordinal + 1} from {item.source_title}
          </button>
        </li>
      ))}
    </ol>
  )
}

function RelationCard({
  relation,
  concepts,
  onSelect,
  prefix,
  displayFromId,
  displayToId,
}: {
  relation: PublishedRelation
  concepts: PublishedConcept[]
  onSelect: (relation: PublishedRelation) => void
  prefix?: string
  displayFromId?: string
  displayToId?: string
}) {
  const from = conceptName(
    concepts,
    displayFromId ?? relation.source_concept_id,
  )
  const to = conceptName(
    concepts,
    displayToId ?? relation.target_concept_id,
  )
  return (
    <article
      className="cg-relation-card"
      aria-label={`${prefix ? `${prefix}: ` : ''}${from} to ${to}`}
    >
      <button type="button" onClick={() => onSelect(relation)}>
        <strong>{from} -&gt; {to}</strong>
        <span>{relationLabel(relation.relation_type)}</span>
      </button>
    </article>
  )
}

function Inspector({
  selection,
  version,
  concepts,
  onOpenEvidence,
}: {
  selection: InspectorSelection | null
  version: GraphVersionMetadata
  concepts: PublishedConcept[]
  onOpenEvidence?: ConceptGraphWorkspaceProps['onOpenEvidence']
}) {
  if (!selection) {
    return (
      <aside className="cg-inspector" aria-label="Graph inspector">
        <h2>Inspector</h2>
        <p className="cg-muted">Select a Concept or relation.</p>
      </aside>
    )
  }
  const title = selection.kind === 'concept'
    ? selection.value.preferred_name
    : `${conceptName(concepts, selection.value.source_concept_id)} -> ${conceptName(concepts, selection.value.target_concept_id)}`
  const description = selection.kind === 'concept'
    ? selection.value.short_definition
    : selection.value.rationale
  return (
    <aside className="cg-inspector" aria-label="Graph inspector">
      <p className="cg-kicker">{selection.kind}</p>
      <h2>{title}</h2>
      <p>{description}</p>
      {selection.kind === 'relation' && (
        <p className="cg-chip">
          {relationLabel(selection.value.relation_type)} -{' '}
          {relationLabel(selection.value.support_basis)}
        </p>
      )}
      <h3>Evidence</h3>
      <EvidenceList
        owner={selection}
        version={version}
        onOpenEvidence={onOpenEvidence}
      />
    </aside>
  )
}

export function ConceptGraphWorkspace({
  apiBaseUrl,
  selectedCourseId,
  onOpenEvidence,
}: ConceptGraphWorkspaceProps) {
  const id = useId()
  const [workspaceMode, setWorkspaceMode] = useState<WorkspaceMode>('published')
  const [view, setView] = useState<View>('overview')
  const [graphState, setGraphState] = useState<GraphState>({ kind: 'idle' })
  const [pathState, setPathState] = useState<PathState>({ kind: 'idle' })
  const [pathResult, setPathResult] = useState<PathResult | null>(null)
  const [selection, setSelection] = useState<InspectorSelection | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [localRoot, setLocalRoot] = useState('')
  const [traceSource, setTraceSource] = useState('')
  const [traceTarget, setTraceTarget] = useState('')
  const [learningTarget, setLearningTarget] = useState('')
  const graphEpoch = useRef(0)
  const pathEpoch = useRef(0)
  const pathController = useRef<AbortController | null>(null)

  useEffect(() => {
    const epoch = graphEpoch.current + 1
    graphEpoch.current = epoch
    pathEpoch.current += 1
    pathController.current?.abort()
    pathController.current = null
    setPathState({ kind: 'idle' })
    setPathResult(null)
    setSelection(null)
    setLocalRoot('')
    setTraceSource('')
    setTraceTarget('')
    setLearningTarget('')

    if (workspaceMode !== 'published' || !selectedCourseId) {
      setGraphState({ kind: 'idle' })
      return
    }

    const courseId = selectedCourseId
    const controller = new AbortController()
    setGraphState({ kind: 'loading' })
    void (async () => {
      try {
        const version = await getCurrentGraphVersion(
          apiBaseUrl,
          courseId,
          controller.signal,
        )
        const concepts = await listAllPublishedConcepts(
          apiBaseUrl,
          courseId,
          version.version_number,
          controller.signal,
        )
        if (controller.signal.aborted || epoch !== graphEpoch.current) return
        if (version.course_id !== courseId) {
          throw new Error('The graph response belongs to another course.')
        }
        setGraphState({ kind: 'ready', courseId, version, concepts })
        const first = concepts[0]?.concept_id ?? ''
        const second = concepts[1]?.concept_id ?? first
        setLocalRoot(first)
        setTraceSource(first)
        setTraceTarget(second)
        setLearningTarget(second)
      } catch (error: unknown) {
        if (controller.signal.aborted || epoch !== graphEpoch.current) return
        if (
          error instanceof ConceptGraphApiError
          && error.code === 'concept_graph_source_authority_stale'
        ) {
          setGraphState({
            kind: 'stale',
            message: error.message,
            version: error.version,
          })
        } else if (
          error instanceof ConceptGraphApiError
          && error.status === 404
        ) {
          setGraphState({
            kind: 'empty',
            message: 'No published Concept graph is available for this course.',
          })
        } else {
          setGraphState({
            kind: 'error',
            message: error instanceof Error
              ? error.message
              : 'The Concept graph failed to load.',
          })
        }
      }
    })()
    return () => {
      controller.abort()
      pathEpoch.current += 1
      pathController.current?.abort()
      pathController.current = null
    }
  }, [apiBaseUrl, refreshKey, selectedCourseId, workspaceMode])

  const ready = graphState.kind === 'ready' ? graphState : null
  const concepts = ready?.concepts ?? []

  async function runPath(
    requestedView: Exclude<View, 'overview'>,
    load: (signal: AbortSignal) => Promise<PathResult>,
  ) {
    if (!ready) return
    const epoch = pathEpoch.current + 1
    pathEpoch.current = epoch
    pathController.current?.abort()
    const controller = new AbortController()
    pathController.current = controller
    setPathResult(null)
    setPathState({ kind: 'loading', view: requestedView })
    try {
      const result = await load(controller.signal)
      if (controller.signal.aborted || epoch !== pathEpoch.current) return
      if (
        result.course_id !== ready.courseId
        || result.graph_version !== ready.version.version_number
        || result.graph_content_hash !== ready.version.content_hash
      ) {
        throw new Error('The path response does not match the visible graph version.')
      }
      setPathResult(result)
      setPathState({ kind: 'idle' })
    } catch (error: unknown) {
      if (controller.signal.aborted || epoch !== pathEpoch.current) return
      if (error instanceof ConceptGraphApiError && error.status === 413) {
        setPathState({ kind: 'limits', message: error.message })
      } else if (
        error instanceof ConceptGraphApiError
        && error.status === 409
      ) {
        setPathState({ kind: 'stale', message: error.message })
      } else {
        setPathState({
          kind: 'error',
          message: error instanceof Error
            ? error.message
            : 'The graph path request failed.',
        })
      }
    } finally {
      if (epoch === pathEpoch.current) pathController.current = null
    }
  }

  function selectConcept(concept: PublishedConcept) {
    setSelection({ kind: 'concept', value: concept })
  }

  function selectRelation(relation: PublishedRelation) {
    setSelection({ kind: 'relation', value: relation })
  }

  return (
    <div className="cg-workspace">
      <header className="cg-header">
        <div>
          <p className="cg-kicker">
            {workspaceMode === 'published'
              ? 'Evidence-grounded understanding'
              : 'Grounded authoring'}
          </p>
          <h2>
            {workspaceMode === 'published' ? 'Concept graph' : 'Review draft'}
          </h2>
          <p>
            {workspaceMode === 'published'
              ? "Inspect Concepts, trace relationships, and open every edge's evidence."
              : 'Review grounded candidates before they enter the published graph.'}
          </p>
        </div>
        <div className="cg-header-actions">
          <div className="cg-mode-switch" role="group" aria-label="Graph workspace">
            <button
              type="button"
              aria-pressed={workspaceMode === 'published'}
              onClick={() => setWorkspaceMode('published')}
            >
              Published
            </button>
            <button
              type="button"
              aria-pressed={workspaceMode === 'draft'}
              onClick={() => setWorkspaceMode('draft')}
            >
              Review draft
            </button>
          </div>
          {workspaceMode === 'published' && (
            <button
              type="button"
              disabled={!selectedCourseId || graphState.kind === 'loading'}
              onClick={() => setRefreshKey((value) => value + 1)}
            >
              Refresh
            </button>
          )}
        </div>
      </header>

      {workspaceMode === 'draft' ? (
        selectedCourseId ? (
          <ConceptGraphDraftReview
            apiBaseUrl={apiBaseUrl}
            courseId={selectedCourseId}
            onPublished={() => {
              setWorkspaceMode('published')
              setRefreshKey((value) => value + 1)
            }}
          />
        ) : (
          <section className="cg-state" role="status">
            <h2>Select a course</h2>
            <p>Choose a course to review its Concept graph draft.</p>
          </section>
        )
      ) : (
        <>
      {ready && <VersionIdentity version={ready.version} />}
      {graphState.kind === 'stale' && graphState.version && (
        <VersionIdentity version={graphState.version} />
      )}

      {graphState.kind === 'idle' && (
        <section className="cg-state" role="status">
          <h2>Select a course</h2>
          <p>Choose a course to load its active published graph.</p>
        </section>
      )}
      {graphState.kind === 'loading' && (
        <p className="cg-state" role="status">Loading the published graph...</p>
      )}
      {graphState.kind === 'empty' && (
        <section className="cg-state cg-state-empty" role="status">
          <h2>Nothing published yet</h2><p>{graphState.message}</p>
        </section>
      )}
      {graphState.kind === 'stale' && (
        <section className="cg-state cg-state-stale" role="alert">
          <h2>Source evidence is stale</h2><p>{graphState.message}</p>
          <p>Republish the graph against the current Source projection before tracing it.</p>
        </section>
      )}
      {graphState.kind === 'error' && (
        <section className="cg-state cg-state-error" role="alert">
          <h2>Graph unavailable</h2><p>{graphState.message}</p>
        </section>
      )}

      {ready && concepts.length === 0 && (
        <section className="cg-state cg-state-empty" role="status">
          <h2>Published graph is empty</h2>
          <p>Version v{ready.version.version_number} contains no Concepts.</p>
        </section>
      )}

      {ready && concepts.length > 0 && (
        <>
          <div className="cg-tabs" role="tablist" aria-label="Graph views">
            {VIEWS.map((item) => (
              <button
                type="button"
                role="tab"
                id={`${id}-${item.id}-tab`}
                aria-controls={`${id}-${item.id}-panel`}
                aria-selected={view === item.id}
                key={item.id}
                onClick={() => setView(item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>

          <div className="cg-layout">
            <section
              className="cg-main"
              role="tabpanel"
              id={`${id}-${view}-panel`}
              aria-labelledby={`${id}-${view}-tab`}
            >
              {view === 'overview' && (
                <>
                  <div className="cg-section-heading">
                    <div>
                      <h2>Published Concepts</h2>
                      <p>Server order</p>
                    </div>
                    <strong>{ready.version.counts.concepts}</strong>
                  </div>
                  <ol className="cg-concept-list">
                    {concepts.map((concept) => (
                      <li key={concept.concept_id}>
                        <ConceptButton
                          concept={concept}
                          onSelect={selectConcept}
                        />
                      </li>
                    ))}
                  </ol>
                </>
              )}

              {view === 'local' && (
                <>
                  <form
                    className="cg-controls"
                    onSubmit={(event) => {
                      event.preventDefault()
                      void runPath('local', (signal) => getLocalGraph(
                        apiBaseUrl,
                        ready.courseId,
                        ready.version.version_number,
                        {
                          rootConceptId: localRoot,
                          directionMode: 'both',
                          maxHops: 2,
                          signal,
                        },
                      ))
                    }}
                  >
                    <ConceptSelect
                      label="Root Concept"
                      value={localRoot}
                      concepts={concepts}
                      onChange={setLocalRoot}
                    />
                    <button type="submit">Explore neighborhood</button>
                  </form>
                  {pathResult?.kind === 'local_graph' && (
                    <div className="cg-result">
                      {pathResult.truncated_by_max_nodes && (
                        <p className="cg-limit" role="status">
                          Node limit reached; this neighborhood is partial.
                        </p>
                      )}
                      <ol className="cg-concept-list">
                        {pathResult.nodes.map((node) => (
                          <li key={node.concept.concept_id}>
                            <ConceptButton
                              concept={node.concept}
                              detail={`Distance ${node.distance}`}
                              onSelect={selectConcept}
                            />
                          </li>
                        ))}
                      </ol>
                      <h3>Relations</h3>
                      {pathResult.relations.map((relation) => (
                        <RelationCard
                          key={relation.relation_id}
                          relation={relation}
                          concepts={concepts}
                          onSelect={selectRelation}
                        />
                      ))}
                    </div>
                  )}
                </>
              )}

              {view === 'trace' && (
                <>
                  <form
                    className="cg-controls"
                    onSubmit={(event) => {
                      event.preventDefault()
                      void runPath('trace', (signal) => getRelationshipTrace(
                        apiBaseUrl,
                        ready.courseId,
                        ready.version.version_number,
                        {
                          sourceConceptId: traceSource,
                          targetConceptId: traceTarget,
                          directionMode: 'outgoing',
                          maxHops: 6,
                          signal,
                        },
                      ))
                    }}
                  >
                    <ConceptSelect
                      label="From"
                      value={traceSource}
                      concepts={concepts}
                      onChange={setTraceSource}
                    />
                    <ConceptSelect
                      label="To"
                      value={traceTarget}
                      concepts={concepts}
                      onChange={setTraceTarget}
                    />
                    <button type="submit">Find trace</button>
                  </form>
                  {pathResult?.kind === 'relationship_trace'
                    && pathResult.status === 'unreachable' && (
                    <section
                      className="cg-path-state cg-unreachable"
                      role="status"
                    >
                      <h3>No relationship path</h3>
                      <p>
                        The target is unreachable with these relation and
                        direction settings.
                      </p>
                    </section>
                  )}
                  {pathResult?.kind === 'relationship_trace'
                    && pathResult.status === 'limits_reached' && (
                    <section className="cg-path-state cg-limit" role="status">
                      <h3>Search limits reached</h3>
                      <p>
                        No path was found before the hop or node limit. This
                        is not proof that the Concepts are disconnected.
                      </p>
                    </section>
                  )}
                  {pathResult?.kind === 'relationship_trace'
                    && pathResult.status === 'found' && (
                    <div className="cg-result">
                      <p className="cg-found" role="status">
                        Shortest trace: {pathResult.hop_count}{' '}
                        {pathResult.hop_count === 1 ? 'hop' : 'hops'}.
                      </p>
                      {pathResult.steps.length === 0 ? (
                        <p>Source and target are the same Concept.</p>
                      ) : (
                        <ol className="cg-trace-list">
                          {pathResult.steps.map((step) => (
                            <li
                              key={`${step.ordinal}-${step.relation.relation_id}`}
                            >
                              <RelationCard
                                relation={step.relation}
                                concepts={pathResult.nodes}
                                onSelect={selectRelation}
                                prefix={`Step ${step.ordinal + 1}`}
                                displayFromId={step.from_concept_id}
                                displayToId={step.to_concept_id}
                              />
                              {step.traversed_against_relation_direction && (
                                <small>
                                  Traversed against the stored edge direction.
                                </small>
                              )}
                            </li>
                          ))}
                        </ol>
                      )}
                    </div>
                  )}
                </>
              )}

              {view === 'learning' && (
                <>
                  <form
                    className="cg-controls"
                    onSubmit={(event) => {
                      event.preventDefault()
                      void runPath('learning', (signal) => getLearningPath(
                        apiBaseUrl,
                        ready.courseId,
                        ready.version.version_number,
                        learningTarget,
                        signal,
                      ))
                    }}
                  >
                    <ConceptSelect
                      label="Learning target"
                      value={learningTarget}
                      concepts={concepts}
                      onChange={setLearningTarget}
                    />
                    <button type="submit">Build learning path</button>
                  </form>
                  {pathResult?.kind === 'learning_path' && (
                    <div className="cg-result">
                      <div className="cg-layers">
                        {pathResult.layers.map((layer) => (
                          <section
                            key={layer.index}
                            aria-label={`Layer ${layer.index + 1}`}
                          >
                            <h3>Layer {layer.index + 1}</h3>
                            {layer.concept_ids.map((conceptId) => {
                              const concept = pathResult.nodes.find(
                                (item) => item.concept_id === conceptId,
                              )
                              return concept ? (
                                <ConceptButton
                                  key={conceptId}
                                  concept={concept}
                                  onSelect={selectConcept}
                                />
                              ) : <code key={conceptId}>{conceptId}</code>
                            })}
                          </section>
                        ))}
                      </div>
                      <h3>Prerequisite edges</h3>
                      {pathResult.relations.map((relation) => (
                        <RelationCard
                          key={relation.relation_id}
                          relation={relation}
                          concepts={pathResult.nodes}
                          onSelect={selectRelation}
                        />
                      ))}
                    </div>
                  )}
                </>
              )}

              {pathState.kind === 'loading' && (
                <p className="cg-path-state" role="status">
                  Loading {pathState.view} path...
                </p>
              )}
              {pathState.kind === 'limits' && (
                <section className="cg-path-state cg-limit" role="status">
                  <h3>Request limit reached</h3>
                  <p>{pathState.message}</p>
                </section>
              )}
              {pathState.kind === 'stale' && (
                <section
                  className="cg-path-state cg-state-stale"
                  role="alert"
                >
                  <h3>Graph version changed</h3>
                  <p>{pathState.message}</p>
                  <button
                    type="button"
                    onClick={() => setRefreshKey((value) => value + 1)}
                  >
                    Load current version
                  </button>
                </section>
              )}
              {pathState.kind === 'error' && (
                <p className="cg-path-state cg-state-error" role="alert">
                  {pathState.message}
                </p>
              )}
            </section>

            <Inspector
              selection={selection}
              version={ready.version}
              concepts={concepts}
              onOpenEvidence={onOpenEvidence}
            />
          </div>
        </>
      )}
        </>
      )}
    </div>
  )
}
