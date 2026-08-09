import {
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import {
  ConceptGraphApiError,
  editDraftConcept,
  editDraftRelation,
  getDraftConcept,
  getDraftRelation,
  getGraphPublicationPreview,
  listAllDraftConcepts,
  listAllDraftRelations,
  publishGraphVersion,
  reviewDraftConcept,
  reviewDraftRelation,
} from './conceptGraphApi'
import {
  ConceptDraftEditor,
  DraftPublicationPreview,
  RelationDraftEditor,
} from './ConceptGraphDraftReviewPanels'
import type {
  DraftConcept,
  DraftConceptSummary,
  DraftRelation,
  DraftRelationSummary,
  GraphPublicationPreview,
  GraphReviewDecision,
  GraphVersionMetadata,
} from './conceptGraphTypes'

export type ConceptGraphDraftReviewProps = {
  apiBaseUrl: string
  courseId: string
  onPublished: (version: GraphVersionMetadata) => void
}

type Selection =
  | { kind: 'concept'; id: string }
  | { kind: 'relation'; id: string }

type DetailState =
  | { kind: 'idle' }
  | { kind: 'loading'; selection: Selection }
  | { kind: 'concept'; value: DraftConcept }
  | { kind: 'relation'; value: DraftRelation }
  | { kind: 'error'; selection: Selection; message: string }

const REVIEW_ACTOR = 'local-maintainer'

function operationId(prefix: string): string {
  const randomPart = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
  return `${prefix}-${randomPart}`
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError'
}

function draftLabel(value: string): string {
  return value.replaceAll('_', ' ')
}

function parseAliases(value: string): string[] {
  return value
    .split('\n')
    .map((alias) => alias.trim())
    .filter(Boolean)
}

function relationConnector(relationType: string): string {
  return relationType === 'related' || relationType === 'contrast_with'
    ? '<->'
    : '->'
}

function equalStrings(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length
    && left.every((value, index) => value === right[index])
}

function isAcceptedCurrentConcept(concept: DraftConceptSummary | undefined): boolean {
  return Boolean(
    concept
    && concept.identity_status === 'active'
    && concept.validity_status === 'current'
    && concept.review_status === 'accepted',
  )
}

function chooseSelection(
  current: Selection | null,
  concepts: DraftConceptSummary[],
  relations: DraftRelationSummary[],
): Selection | null {
  if (
    current?.kind === 'concept'
    && concepts.some((concept) => concept.id === current.id)
  ) {
    return current
  }
  if (
    current?.kind === 'relation'
    && relations.some((relation) => relation.id === current.id)
  ) {
    return current
  }

  const candidateConcept = concepts.find(
    (concept) => concept.identity_status === 'active'
      && concept.review_status === 'candidate',
  )
  if (candidateConcept) return { kind: 'concept', id: candidateConcept.id }
  const candidateRelation = relations.find(
    (relation) => relation.review_status === 'candidate',
  )
  if (candidateRelation) return { kind: 'relation', id: candidateRelation.id }
  if (concepts[0]) return { kind: 'concept', id: concepts[0].id }
  if (relations[0]) return { kind: 'relation', id: relations[0].id }
  return null
}

export function ConceptGraphDraftReview({
  apiBaseUrl,
  courseId,
  onPublished,
}: ConceptGraphDraftReviewProps) {
  const [concepts, setConcepts] = useState<DraftConceptSummary[]>([])
  const [relations, setRelations] = useState<DraftRelationSummary[]>([])
  const [preview, setPreview] = useState<GraphPublicationPreview | null>(null)
  const [selection, setSelection] = useState<Selection | null>(null)
  const [detailState, setDetailState] = useState<DetailState>({ kind: 'idle' })
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [mutationError, setMutationError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [mutating, setMutating] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)

  const [conceptName, setConceptName] = useState('')
  const [conceptDefinition, setConceptDefinition] = useState('')
  const [conceptAliases, setConceptAliases] = useState('')
  const [relationRationale, setRelationRationale] = useState('')
  const [editReason, setEditReason] = useState('')
  const [reviewReason, setReviewReason] = useState('')
  const [publishReason, setPublishReason] = useState('')

  const loadEpoch = useRef(0)
  const detailEpoch = useRef(0)
  const mutationEpoch = useRef(0)
  const mutationController = useRef<AbortController | null>(null)
  const loadedCourseKey = useRef<string | null>(null)
  const detailSelectionKey = useRef<string | null>(null)

  const pendingConceptCount = concepts.filter(
    (concept) => concept.identity_status === 'active'
      && concept.review_status === 'candidate',
  ).length
  const pendingRelationCount = relations.filter(
    (relation) => relation.review_status === 'candidate',
  ).length
  const conceptsById = useMemo(
    () => new Map(concepts.map((concept) => [concept.id, concept])),
    [concepts],
  )
  const staleConceptCount = concepts.filter(
    (concept) => concept.identity_status === 'active'
      && concept.review_status === 'accepted'
      && concept.validity_status !== 'current',
  ).length
  const repairableStaleRelationCount = relations.filter((relation) => (
    relation.review_status === 'accepted'
    && relation.validity_status !== 'current'
    && isAcceptedCurrentConcept(conceptsById.get(relation.source_concept_id))
    && isAcceptedCurrentConcept(conceptsById.get(relation.target_concept_id))
  )).length
  const unresolvedDraftCount = pendingConceptCount
    + pendingRelationCount
    + staleConceptCount
    + repairableStaleRelationCount

  useEffect(() => {
    mutationEpoch.current += 1
    mutationController.current?.abort()
    mutationController.current = null
    setMutating(false)
    setMutationError(null)
    setNotice(null)
    setPublishReason('')
    return () => {
      mutationEpoch.current += 1
      mutationController.current?.abort()
      mutationController.current = null
    }
  }, [apiBaseUrl, courseId])

  useEffect(() => {
    const epoch = loadEpoch.current + 1
    loadEpoch.current = epoch
    const controller = new AbortController()
    const courseKey = `${apiBaseUrl}\u0000${courseId}`
    const courseChanged = loadedCourseKey.current !== courseKey
    loadedCourseKey.current = courseKey

    if (courseChanged) {
      setConcepts([])
      setRelations([])
      setPreview(null)
      setSelection(null)
      setDetailState({ kind: 'idle' })
    }
    setLoading(true)
    setLoadError(null)

    void (async () => {
      try {
        const [nextConcepts, nextRelations, nextPreview] = await Promise.all([
          listAllDraftConcepts(apiBaseUrl, courseId, controller.signal),
          listAllDraftRelations(apiBaseUrl, courseId, controller.signal),
          getGraphPublicationPreview(apiBaseUrl, courseId, controller.signal),
        ])
        if (controller.signal.aborted || epoch !== loadEpoch.current) return
        if (
          nextConcepts.some((concept) => concept.course_id !== courseId)
          || nextRelations.some((relation) => relation.course_id !== courseId)
        ) {
          throw new Error('The draft response belongs to another course.')
        }
        setConcepts(nextConcepts)
        setRelations(nextRelations)
        setPreview(nextPreview)
        setSelection((current) => chooseSelection(
          current,
          nextConcepts,
          nextRelations,
        ))
      } catch (error: unknown) {
        if (
          controller.signal.aborted
          || epoch !== loadEpoch.current
          || isAbortError(error)
        ) return
        setLoadError(errorMessage(error, 'The graph draft failed to load.'))
      } finally {
        if (!controller.signal.aborted && epoch === loadEpoch.current) {
          setLoading(false)
        }
      }
    })()

    return () => controller.abort()
  }, [apiBaseUrl, courseId, refreshKey])

  useEffect(() => {
    const epoch = detailEpoch.current + 1
    detailEpoch.current = epoch
    const selectionKey = selection
      ? `${selection.kind}:${selection.id}`
      : null
    const selectionChanged = detailSelectionKey.current !== selectionKey
    detailSelectionKey.current = selectionKey
    if (!selection) {
      setDetailState({ kind: 'idle' })
      return
    }

    const controller = new AbortController()
    const requestedSelection = selection
    setDetailState({ kind: 'loading', selection: requestedSelection })
    setMutationError(null)
    if (selectionChanged) {
      setEditReason('')
      setReviewReason('')
    }

    void (async () => {
      try {
        if (requestedSelection.kind === 'concept') {
          const concept = await getDraftConcept(
            apiBaseUrl,
            courseId,
            requestedSelection.id,
            controller.signal,
          )
          if (controller.signal.aborted || epoch !== detailEpoch.current) return
          if (concept.course_id !== courseId || concept.id !== requestedSelection.id) {
            throw new Error('The Concept detail does not match this selection.')
          }
          setConceptName(concept.preferred_name)
          setConceptDefinition(concept.short_definition)
          setConceptAliases(
            concept.aliases.map((alias) => alias.display_text).join('\n'),
          )
          setDetailState({ kind: 'concept', value: concept })
        } else {
          const relation = await getDraftRelation(
            apiBaseUrl,
            courseId,
            requestedSelection.id,
            controller.signal,
          )
          if (controller.signal.aborted || epoch !== detailEpoch.current) return
          if (relation.course_id !== courseId || relation.id !== requestedSelection.id) {
            throw new Error('The relation detail does not match this selection.')
          }
          setRelationRationale(relation.rationale)
          setDetailState({ kind: 'relation', value: relation })
        }
      } catch (error: unknown) {
        if (
          controller.signal.aborted
          || epoch !== detailEpoch.current
          || isAbortError(error)
        ) return
        setDetailState({
          kind: 'error',
          selection: requestedSelection,
          message: errorMessage(error, 'The draft detail failed to load.'),
        })
      }
    })()

    return () => controller.abort()
  }, [apiBaseUrl, courseId, refreshKey, selection])

  async function runMutation<T>(
    request: (signal: AbortSignal) => Promise<T>,
    successMessage: string,
    afterSuccess?: (value: T) => void,
  ) {
    const epoch = mutationEpoch.current + 1
    mutationEpoch.current = epoch
    mutationController.current?.abort()
    const controller = new AbortController()
    mutationController.current = controller
    setMutating(true)
    setMutationError(null)
    setNotice(null)

    try {
      const result = await request(controller.signal)
      if (controller.signal.aborted || epoch !== mutationEpoch.current) return
      setNotice(successMessage)
      setRefreshKey((value) => value + 1)
      afterSuccess?.(result)
    } catch (error: unknown) {
      if (
        controller.signal.aborted
        || epoch !== mutationEpoch.current
        || isAbortError(error)
      ) return
      if (error instanceof ConceptGraphApiError && error.status === 409) {
        setEditReason('')
        setReviewReason('')
        setNotice(
          `${error.message} Latest server state loaded; review and reapply your change.`,
        )
        setRefreshKey((value) => value + 1)
      } else {
        setMutationError(errorMessage(error, 'The draft operation failed.'))
      }
    } finally {
      if (epoch === mutationEpoch.current) {
        setMutating(false)
        mutationController.current = null
      }
    }
  }

  function saveConcept(concept: DraftConcept) {
    const preferredName = conceptName.trim()
    const shortDefinition = conceptDefinition.trim()
    const reason = editReason.trim()
    if (!preferredName || !shortDefinition || !reason) {
      setMutationError('Name, definition, and edit reason are required.')
      return
    }
    const aliases = parseAliases(conceptAliases)
    const currentAliases = concept.aliases.map((alias) => alias.display_text)
    const needsReground = concept.validity_status !== 'current'
      || !concept.evidence_current
    if (
      preferredName === concept.preferred_name
      && shortDefinition === concept.short_definition
      && equalStrings(aliases, currentAliases)
      && !needsReground
    ) {
      setMutationError('Change at least one Concept field before saving.')
      return
    }
    if (concept.course_id !== courseId || concept.identity_status !== 'active') return

    void runMutation(
      (signal) => editDraftConcept(
        apiBaseUrl,
        courseId,
        concept.id,
        {
          operation_id: operationId('edit-concept'),
          actor: REVIEW_ACTOR,
          reason,
          expected_revision: concept.revision,
          preferred_name: preferredName,
          short_definition: shortDefinition,
          aliases,
          evidence: concept.evidence.map((item) => ({
            chunk_id: item.chunk_id,
            quote: item.quote,
          })),
        },
        signal,
      ),
      'Concept changes saved.',
    )
  }

  function reviewConcept(
    concept: DraftConcept,
    decision: GraphReviewDecision,
  ) {
    const reason = reviewReason.trim()
    if (!reason) {
      setMutationError('A review reason is required.')
      return
    }
    if (concept.course_id !== courseId) return

    void runMutation(
      (signal) => reviewDraftConcept(
        apiBaseUrl,
        courseId,
        concept.id,
        {
          operation_id: operationId(`review-concept-${decision}`),
          actor: REVIEW_ACTOR,
          reason,
          expected_revision: concept.revision,
          decision,
        },
        signal,
      ),
      decision === 'accept' ? 'Concept accepted.' : 'Concept rejected.',
    )
  }

  function saveRelation(relation: DraftRelation) {
    const reason = editReason.trim()
    const rationale = relationRationale.trim()
    if (!reason || !rationale) {
      setMutationError('Rationale and edit reason are required.')
      return
    }
    if (relation.course_id !== courseId) return
    const sourceConcept = conceptsById.get(relation.source_concept_id)
    const targetConcept = conceptsById.get(relation.target_concept_id)
    if (!sourceConcept || !targetConcept) {
      setMutationError('The current relation endpoints are unavailable.')
      return
    }
    const endpointsReady = isAcceptedCurrentConcept(sourceConcept)
      && isAcceptedCurrentConcept(targetConcept)
    if (!endpointsReady) {
      setMutationError('Accept both current endpoint Concepts before editing this relation.')
      return
    }
    const needsRebind = !relation.endpoint_binding
      || !relation.endpoint_revisions_current
    if (rationale === relation.rationale && !needsRebind) {
      setMutationError('Change the rationale or select a relation that needs rebinding.')
      return
    }

    void runMutation(
      (signal) => editDraftRelation(
        apiBaseUrl,
        courseId,
        relation.id,
        {
          operation_id: operationId('edit-relation'),
          actor: REVIEW_ACTOR,
          reason,
          expected_revision: relation.revision,
          support_basis: relation.support_basis,
          rationale,
          expected_source_concept_revision: sourceConcept.revision,
          expected_target_concept_revision: targetConcept.revision,
          evidence: relation.evidence.map((item) => ({
            chunk_id: item.chunk_id,
            quote: item.quote,
            support_role: item.support_role,
          })),
        },
        signal,
      ),
      'Relation changes saved.',
    )
  }

  function reviewRelation(
    relation: DraftRelation,
    decision: GraphReviewDecision,
  ) {
    const reason = reviewReason.trim()
    if (!reason) {
      setMutationError('A review reason is required.')
      return
    }
    if (relation.course_id !== courseId) return
    const sourceConcept = conceptsById.get(relation.source_concept_id)
    const targetConcept = conceptsById.get(relation.target_concept_id)
    const binding = relation.endpoint_binding
    if (!binding && (!sourceConcept || !targetConcept)) {
      setMutationError('The current relation endpoints are unavailable.')
      return
    }

    void runMutation(
      (signal) => reviewDraftRelation(
        apiBaseUrl,
        courseId,
        relation.id,
        {
          operation_id: operationId(`review-relation-${decision}`),
          actor: REVIEW_ACTOR,
          reason,
          expected_revision: relation.revision,
          decision,
          expected_source_concept_revision: binding?.source_concept_revision
            ?? sourceConcept!.revision,
          expected_target_concept_revision: binding?.target_concept_revision
            ?? targetConcept!.revision,
        },
        signal,
      ),
      decision === 'accept' ? 'Relation accepted.' : 'Relation rejected.',
    )
  }

  function publish() {
    const reason = publishReason.trim()
    if (!preview || !preview.publishable || !preview.has_changes || !reason) {
      return
    }
    const publicationPreview = preview
    void runMutation(
      (signal) => publishGraphVersion(
        apiBaseUrl,
        courseId,
        {
          operation_id: operationId('publish-graph'),
          actor: REVIEW_ACTOR,
          reason,
          expected_active_version: publicationPreview.active_version,
          expected_draft_manifest_hash: publicationPreview.draft_manifest_hash,
        },
        signal,
      ),
      'Graph version published.',
      (version) => {
        if (version.course_id === courseId) onPublished(version)
      },
    )
  }

  const selectedConcept = detailState.kind === 'concept'
    ? detailState.value
    : null
  const selectedRelation = detailState.kind === 'relation'
    ? detailState.value
    : null
  const selectedConceptAliases = selectedConcept
    ? selectedConcept.aliases.map((alias) => alias.display_text)
    : []
  const conceptNeedsReground = Boolean(selectedConcept) && (
    selectedConcept?.validity_status !== 'current'
    || !selectedConcept?.evidence_current
  )
  const conceptHasChanges = Boolean(selectedConcept) && (
    conceptName.trim() !== selectedConcept?.preferred_name
    || conceptDefinition.trim() !== selectedConcept?.short_definition
    || !equalStrings(parseAliases(conceptAliases), selectedConceptAliases)
    || conceptNeedsReground
  )
  const conceptReviewDisabled = !selectedConcept
    || selectedConcept.review_status !== 'candidate'
    || selectedConcept.identity_status !== 'active'
    || reviewReason.trim().length === 0
    || mutating
  const conceptAcceptDisabled = conceptReviewDisabled
    || selectedConcept?.validity_status !== 'current'
    || !selectedConcept?.evidence_current
  const selectedSourceConcept = selectedRelation
    ? conceptsById.get(selectedRelation.source_concept_id)
    : undefined
  const selectedTargetConcept = selectedRelation
    ? conceptsById.get(selectedRelation.target_concept_id)
    : undefined
  const relationEndpointsReady = isAcceptedCurrentConcept(selectedSourceConcept)
    && isAcceptedCurrentConcept(selectedTargetConcept)
  const relationRejectDisabled = !selectedRelation
    || selectedRelation.review_status !== 'candidate'
    || (!selectedRelation.endpoint_binding
      && (!selectedSourceConcept || !selectedTargetConcept))
    || reviewReason.trim().length === 0
    || mutating
  const relationAcceptDisabled = relationRejectDisabled
    || !selectedRelation?.endpoint_binding
    || !relationEndpointsReady
    || selectedRelation?.validity_status !== 'current'
    || !selectedRelation?.evidence_current
    || !selectedRelation?.endpoint_revisions_current
  const relationNeedsRebind = Boolean(selectedRelation) && (
    !selectedRelation?.endpoint_binding
    || !selectedRelation?.endpoint_revisions_current
  )
  const relationHasChanges = Boolean(selectedRelation) && (
    relationRationale.trim() !== selectedRelation?.rationale
    || relationNeedsRebind
  )

  return (
    <section className="cg-draft-root" aria-label="Concept graph draft review">
      <header className="cg-draft-header">
        <strong>Draft candidates</strong>
        <button
          type="button"
          className="cg-draft-refresh-button"
          disabled={loading || mutating}
          onClick={() => setRefreshKey((value) => value + 1)}
        >
          Refresh draft
        </button>
      </header>

      {notice && (
        <p className="cg-draft-notice" role="status">{notice}</p>
      )}
      {mutationError && (
        <p className="cg-draft-error" role="alert">{mutationError}</p>
      )}
      {loadError && (
        <section className="cg-draft-error" role="alert">
          <h3>Draft unavailable</h3>
          <p>{loadError}</p>
          <button
            type="button"
            className="cg-draft-retry-button"
            onClick={() => setRefreshKey((value) => value + 1)}
          >
            Try again
          </button>
        </section>
      )}
      {loading && (
        <p className="cg-draft-loading" role="status">Refreshing draft...</p>
      )}

      {!loadError && (
        <div className="cg-draft-layout">
          <nav className="cg-draft-queues" aria-label="Candidate queues">
            <section className="cg-draft-queue" aria-labelledby="cg-draft-concept-queue">
              <div className="cg-draft-queue-heading">
                <h3 id="cg-draft-concept-queue">Concept queue</h3>
                <span>{pendingConceptCount} pending</span>
              </div>
              {concepts.length === 0 ? (
                <p className="cg-draft-muted">No Concept candidates.</p>
              ) : (
                <ul className="cg-draft-queue-list">
                  {concepts.map((concept) => {
                    const selected = selection?.kind === 'concept'
                      && selection.id === concept.id
                    return (
                      <li className="cg-draft-queue-item" key={concept.id}>
                        <button
                          type="button"
                          className={selected
                            ? 'cg-draft-queue-button cg-draft-queue-button-selected'
                            : 'cg-draft-queue-button'}
                          aria-pressed={selected}
                          onClick={() => setSelection({
                            kind: 'concept',
                            id: concept.id,
                          })}
                        >
                          <strong>{concept.preferred_name}</strong>
                          <span>{draftLabel(concept.review_status)}</span>
                          <small>
                            Revision {concept.revision} - {concept.evidence_count} evidence
                          </small>
                        </button>
                      </li>
                    )
                  })}
                </ul>
              )}
            </section>

            <section className="cg-draft-queue" aria-labelledby="cg-draft-relation-queue">
              <div className="cg-draft-queue-heading">
                <h3 id="cg-draft-relation-queue">Relation queue</h3>
                <span>
                  {pendingRelationCount} pending
                </span>
              </div>
              {relations.length === 0 ? (
                <p className="cg-draft-muted">No relation candidates.</p>
              ) : (
                <ul className="cg-draft-queue-list">
                  {relations.map((relation) => {
                    const selected = selection?.kind === 'relation'
                      && selection.id === relation.id
                    const sourceName = conceptsById.get(
                      relation.source_concept_id,
                    )?.preferred_name ?? relation.source_concept_id
                    const targetName = conceptsById.get(
                      relation.target_concept_id,
                    )?.preferred_name ?? relation.target_concept_id
                    return (
                      <li className="cg-draft-queue-item" key={relation.id}>
                        <button
                          type="button"
                          className={selected
                            ? 'cg-draft-queue-button cg-draft-queue-button-selected'
                            : 'cg-draft-queue-button'}
                          aria-pressed={selected}
                          onClick={() => setSelection({
                            kind: 'relation',
                            id: relation.id,
                          })}
                        >
                          <strong>
                            {sourceName} {relationConnector(relation.relation_type)}{' '}
                            {targetName}
                          </strong>
                          <span>{draftLabel(relation.relation_type)}</span>
                          <small>
                            {draftLabel(relation.review_status)} - revision{' '}
                            {relation.revision} - {relation.evidence_count} evidence
                          </small>
                        </button>
                      </li>
                    )
                  })}
                </ul>
              )}
            </section>
          </nav>

          <section className="cg-draft-detail" aria-label="Candidate details">
            {detailState.kind === 'idle' && (
              <p className="cg-draft-muted">Select a candidate to review.</p>
            )}
            {detailState.kind === 'loading' && (
              <p className="cg-draft-loading" role="status">
                Loading {detailState.selection.kind}...
              </p>
            )}
            {detailState.kind === 'error' && (
              <section className="cg-draft-error" role="alert">
                <h3>Candidate unavailable</h3>
                <p>{detailState.message}</p>
                <button
                  type="button"
                  className="cg-draft-retry-button"
                  onClick={() => setRefreshKey((value) => value + 1)}
                >
                  Reload candidate
                </button>
              </section>
            )}

            {selectedConcept && (
              <ConceptDraftEditor
                concept={selectedConcept}
                name={conceptName}
                definition={conceptDefinition}
                aliases={conceptAliases}
                editReason={editReason}
                reviewReason={reviewReason}
                saveDisabled={
                  mutating
                  || !conceptHasChanges
                  || selectedConcept.identity_status !== 'active'
                  || !conceptName.trim()
                  || !conceptDefinition.trim()
                  || !editReason.trim()
                }
                acceptDisabled={conceptAcceptDisabled}
                rejectDisabled={conceptReviewDisabled}
                onNameChange={setConceptName}
                onDefinitionChange={setConceptDefinition}
                onAliasesChange={setConceptAliases}
                onEditReasonChange={setEditReason}
                onReviewReasonChange={setReviewReason}
                onSave={() => saveConcept(selectedConcept)}
                onReview={(decision) => reviewConcept(selectedConcept, decision)}
              />
            )}

            {selectedRelation && (
              <RelationDraftEditor
                relation={selectedRelation}
                sourceName={conceptsById.get(selectedRelation.source_concept_id)
                  ?.preferred_name ?? selectedRelation.source_concept_id}
                targetName={conceptsById.get(selectedRelation.target_concept_id)
                  ?.preferred_name ?? selectedRelation.target_concept_id}
                rationale={relationRationale}
                editReason={editReason}
                reviewReason={reviewReason}
                endpointsReady={relationEndpointsReady}
                saveDisabled={
                  mutating
                  || !relationEndpointsReady
                  || !relationHasChanges
                  || !relationRationale.trim()
                  || !editReason.trim()
                }
                acceptDisabled={relationAcceptDisabled}
                rejectDisabled={relationRejectDisabled}
                onRationaleChange={setRelationRationale}
                onEditReasonChange={setEditReason}
                onReviewReasonChange={setReviewReason}
                onSave={() => saveRelation(selectedRelation)}
                onReview={(decision) => reviewRelation(selectedRelation, decision)}
              />
            )}
          </section>

          <DraftPublicationPreview
            preview={preview}
            publishReason={publishReason}
            disabled={
              loading
              || mutating
              || unresolvedDraftCount > 0
            }
            unresolvedCount={unresolvedDraftCount}
            onReasonChange={setPublishReason}
            onPublish={publish}
          />
        </div>
      )}
    </section>
  )
}
