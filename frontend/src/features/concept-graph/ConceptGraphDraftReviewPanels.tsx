import type {
  DraftConcept,
  DraftConceptEvidence,
  DraftRelation,
  DraftRelationEvidence,
  GraphPublicationPreview,
  GraphReviewDecision,
} from './conceptGraphTypes'
import { formatSourceLocator } from '../citations/citationFormat'

type EvidenceItem = DraftConceptEvidence | DraftRelationEvidence

function draftLabel(value: string): string {
  return value.replaceAll('_', ' ')
}

function relationConnector(relationType: string): string {
  return relationType === 'related' || relationType === 'contrast_with'
    ? '<->'
    : '->'
}

function EvidenceList({ evidence }: { evidence: readonly EvidenceItem[] }) {
  if (evidence.length === 0) {
    return <p className="cg-draft-muted">No evidence is attached.</p>
  }

  return (
    <ol className="cg-draft-evidence-list">
      {evidence.map((item) => (
        <li className="cg-draft-evidence-item" key={item.id}>
          <div className="cg-draft-evidence-heading">
            <strong>{item.source_title}</strong>
            {'support_role' in item && <span>{draftLabel(item.support_role)}</span>}
          </div>
          <blockquote>{item.quote}</blockquote>
          <small>
            {formatSourceLocator(item.locator)} - chunk <code>{item.chunk_id}</code>
            {' - '}
            {item.projection_is_current ? 'current' : 'stale projection'}
          </small>
        </li>
      ))}
    </ol>
  )
}

type ConceptEditorProps = {
  concept: DraftConcept
  name: string
  definition: string
  aliases: string
  editReason: string
  reviewReason: string
  saveDisabled: boolean
  acceptDisabled: boolean
  rejectDisabled: boolean
  onNameChange: (value: string) => void
  onDefinitionChange: (value: string) => void
  onAliasesChange: (value: string) => void
  onEditReasonChange: (value: string) => void
  onReviewReasonChange: (value: string) => void
  onSave: () => void
  onReview: (decision: GraphReviewDecision) => void
}

export function ConceptDraftEditor({
  concept,
  name,
  definition,
  aliases,
  editReason,
  reviewReason,
  saveDisabled,
  acceptDisabled,
  rejectDisabled,
  onNameChange,
  onDefinitionChange,
  onAliasesChange,
  onEditReasonChange,
  onReviewReasonChange,
  onSave,
  onReview,
}: ConceptEditorProps) {
  return (
    <article className="cg-draft-editor">
      <div className="cg-draft-editor-heading">
        <div>
          <p className="cg-draft-kicker">Concept candidate</p>
          <h3>{concept.preferred_name}</h3>
        </div>
        <span>{draftLabel(concept.review_status)}</span>
      </div>
      <p className="cg-draft-entity-summary">
        Revision {concept.revision} - {concept.evidence.length} evidence item
        {concept.evidence.length === 1 ? '' : 's'}
      </p>
      {concept.currentness_reasons.length > 0 && (
        <ul className="cg-draft-currentness-list">
          {concept.currentness_reasons.map((reason) => <li key={reason}>{reason}</li>)}
        </ul>
      )}

      <form
        className="cg-draft-form"
        onSubmit={(event) => {
          event.preventDefault()
          onSave()
        }}
      >
        <label className="cg-draft-field">
          Preferred name
          <input value={name} onChange={(event) => onNameChange(event.target.value)} />
        </label>
        <label className="cg-draft-field">
          Short definition
          <textarea
            rows={5}
            value={definition}
            onChange={(event) => onDefinitionChange(event.target.value)}
          />
        </label>
        <label className="cg-draft-field">
          Aliases (one per line)
          <textarea
            rows={3}
            value={aliases}
            onChange={(event) => onAliasesChange(event.target.value)}
          />
        </label>
        <label className="cg-draft-field">
          Edit reason
          <input
            value={editReason}
            onChange={(event) => onEditReasonChange(event.target.value)}
            placeholder="Why are you changing this candidate?"
          />
        </label>
        <button type="submit" className="cg-draft-save-button" disabled={saveDisabled}>
          Save Concept revision
        </button>
      </form>

      <section className="cg-draft-evidence" aria-label="Concept evidence">
        <h4>Evidence (read-only)</h4>
        <EvidenceList evidence={concept.evidence} />
      </section>

      <section className="cg-draft-review" aria-label="Concept review decision">
        <h4>Review decision</h4>
        <label className="cg-draft-field">
          Decision reason
          <textarea
            rows={3}
            value={reviewReason}
            onChange={(event) => onReviewReasonChange(event.target.value)}
            placeholder="Evidence-based reason for accepting or rejecting"
          />
        </label>
        {concept.review_status !== 'candidate' && (
          <p className="cg-draft-muted">
            This revision has already been {concept.review_status}.
          </p>
        )}
        <div className="cg-draft-review-actions">
          <button
            type="button"
            className="cg-draft-accept-button"
            disabled={acceptDisabled}
            onClick={() => onReview('accept')}
          >
            Accept Concept
          </button>
          <button
            type="button"
            className="cg-draft-reject-button"
            disabled={rejectDisabled}
            onClick={() => onReview('reject')}
          >
            Reject Concept
          </button>
        </div>
      </section>
    </article>
  )
}

type RelationEditorProps = {
  relation: DraftRelation
  sourceName: string
  targetName: string
  rationale: string
  editReason: string
  reviewReason: string
  endpointsReady: boolean
  saveDisabled: boolean
  acceptDisabled: boolean
  rejectDisabled: boolean
  onRationaleChange: (value: string) => void
  onEditReasonChange: (value: string) => void
  onReviewReasonChange: (value: string) => void
  onSave: () => void
  onReview: (decision: GraphReviewDecision) => void
}

export function RelationDraftEditor({
  relation,
  sourceName,
  targetName,
  rationale,
  editReason,
  reviewReason,
  endpointsReady,
  saveDisabled,
  acceptDisabled,
  rejectDisabled,
  onRationaleChange,
  onEditReasonChange,
  onReviewReasonChange,
  onSave,
  onReview,
}: RelationEditorProps) {
  return (
    <article className="cg-draft-editor">
      <div className="cg-draft-editor-heading">
        <div>
          <p className="cg-draft-kicker">Relation candidate</p>
          <h3>
            {sourceName} {relationConnector(relation.relation_type)} {targetName}
          </h3>
        </div>
        <span>{draftLabel(relation.review_status)}</span>
      </div>
      <p className="cg-draft-entity-summary">
        {draftLabel(relation.relation_type)} - revision {relation.revision}
      </p>
      {relation.endpoint_binding ? (
        <p className="cg-draft-entity-summary">
          Bound to endpoint revisions{' '}
          {relation.endpoint_binding.source_concept_revision} and{' '}
          {relation.endpoint_binding.target_concept_revision}.
        </p>
      ) : (
        <p className="cg-draft-gate-note">
          This legacy relation has no endpoint binding. Saving creates one from
          the current Concept revisions.
        </p>
      )}
      {relation.currentness_reasons.length > 0 && (
        <ul className="cg-draft-currentness-list">
          {relation.currentness_reasons.map((reason) => <li key={reason}>{reason}</li>)}
        </ul>
      )}

      <form
        className="cg-draft-form"
        onSubmit={(event) => {
          event.preventDefault()
          onSave()
        }}
      >
        <p className="cg-draft-entity-summary">
          Support basis: <strong>{draftLabel(relation.support_basis)}</strong>
        </p>
        <label className="cg-draft-field">
          Rationale
          <textarea
            rows={5}
            value={rationale}
            onChange={(event) => onRationaleChange(event.target.value)}
          />
        </label>
        <label className="cg-draft-field">
          Edit reason
          <input
            value={editReason}
            onChange={(event) => onEditReasonChange(event.target.value)}
            placeholder="Why are you changing this candidate?"
          />
        </label>
        <button type="submit" className="cg-draft-save-button" disabled={saveDisabled}>
          Save relation revision
        </button>
      </form>

      <section className="cg-draft-evidence" aria-label="Relation evidence">
        <h4>Evidence (read-only)</h4>
        <EvidenceList evidence={relation.evidence} />
      </section>

      <section className="cg-draft-review" aria-label="Relation review decision">
        <h4>Review decision</h4>
        {!endpointsReady && (
          <p className="cg-draft-gate-note">
            Accept both current endpoint Concepts before editing or accepting
            this relation. Rejection remains available for candidate cleanup.
          </p>
        )}
        <label className="cg-draft-field">
          Decision reason
          <textarea
            rows={3}
            value={reviewReason}
            onChange={(event) => onReviewReasonChange(event.target.value)}
            placeholder="Evidence-based reason for accepting or rejecting"
          />
        </label>
        {relation.review_status !== 'candidate' && (
          <p className="cg-draft-muted">
            This revision has already been {relation.review_status}.
          </p>
        )}
        <div className="cg-draft-review-actions">
          <button
            type="button"
            className="cg-draft-accept-button"
            disabled={acceptDisabled}
            onClick={() => onReview('accept')}
          >
            Accept relation
          </button>
          <button
            type="button"
            className="cg-draft-reject-button"
            disabled={rejectDisabled}
            onClick={() => onReview('reject')}
          >
            Reject relation
          </button>
        </div>
      </section>
    </article>
  )
}

type PublicationPreviewProps = {
  preview: GraphPublicationPreview | null
  publishReason: string
  disabled: boolean
  unresolvedCount: number
  onReasonChange: (value: string) => void
  onPublish: () => void
}

export function DraftPublicationPreview({
  preview,
  publishReason,
  disabled,
  unresolvedCount,
  onReasonChange,
  onPublish,
}: PublicationPreviewProps) {
  if (!preview) {
    return (
      <aside className="cg-draft-preview" aria-label="Publication preview">
        <h3>Publication preview</h3>
        <p className="cg-draft-muted">Preview unavailable.</p>
      </aside>
    )
  }

  const canPublish = preview.publishable
    && preview.has_changes
    && publishReason.trim().length > 0
    && !disabled

  return (
    <aside className="cg-draft-preview" aria-label="Publication preview">
      <div className="cg-draft-preview-heading">
        <div>
          <h3>Publication preview</h3>
          <p>
            Active version: {preview.active_version === null
              ? 'none'
              : `v${preview.active_version}`}
          </p>
        </div>
        <span className="cg-draft-preview-status">
          {preview.publishable ? 'Publishable' : 'Blocked'}
        </span>
      </div>

      <dl className="cg-draft-preview-counts">
        <div><dt>Concepts</dt><dd>{preview.counts.concepts}</dd></div>
        <div><dt>Relations</dt><dd>{preview.counts.relations}</dd></div>
      </dl>

      <section className="cg-draft-preview-issues" aria-label="Publication issues">
        <div className="cg-draft-preview-issues-heading">
          <h4>Issues</h4>
          <span>{preview.issue_count}</span>
        </div>
        {preview.issues.length === 0 ? (
          <p className="cg-draft-muted">No publication issues.</p>
        ) : (
          <ol className="cg-draft-issue-list">
            {preview.issues.map((issue, index) => (
              <li
                className="cg-draft-issue"
                key={`${issue.code}-${issue.entity_id ?? 'graph'}-${issue.revision ?? 'none'}-${index}`}
              >
                <strong>{issue.code}</strong>
                <p>{issue.message}</p>
              </li>
            ))}
          </ol>
        )}
      </section>

      <label className="cg-draft-field">
        Publication reason
        <textarea
          rows={3}
          value={publishReason}
          onChange={(event) => onReasonChange(event.target.value)}
          placeholder="Why is this graph ready to publish?"
        />
      </label>
      <button
        type="button"
        className="cg-draft-publish-button"
        disabled={!canPublish}
        onClick={onPublish}
      >
        Publish graph version
      </button>
      {!preview.has_changes && (
        <p className="cg-draft-muted">The draft matches the active version.</p>
      )}
      {preview.has_changes && !preview.publishable && (
        <p className="cg-draft-muted">Resolve every publication issue first.</p>
      )}
      {unresolvedCount > 0 && (
        <p className="cg-draft-gate-note">
          Review, reject, or repair {unresolvedCount} unresolved draft head
          {unresolvedCount === 1 ? '' : 's'} before publishing. Unresolved heads
          are not included in the immutable snapshot.
        </p>
      )}
    </aside>
  )
}
