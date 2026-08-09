import { ArrowRight, Network } from 'lucide-react'
import type {
  ConceptRelationType,
  RelationSupportBasis,
} from '../concept-graph/conceptGraphTypes'
import type {
  ChatGraphConcept,
  ChatGraphContext,
  ChatGraphStep,
} from './chatTypes'

const RELATION_TYPES = new Set<ConceptRelationType>([
  'prerequisite',
  'part_of',
  'example_of',
  'related',
  'contrast_with',
])

const SUPPORT_BASES = new Set<RelationSupportBasis>([
  'source_asserted',
  'pedagogical_inference',
])

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0
}

function isHash(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
}

function parseConcept(value: unknown): ChatGraphConcept | null {
  if (
    !isRecord(value) ||
    typeof value.concept_id !== 'string' ||
    !value.concept_id ||
    !isPositiveInteger(value.concept_revision) ||
    typeof value.preferred_name !== 'string' ||
    !value.preferred_name
  ) {
    return null
  }
  return {
    concept_id: value.concept_id,
    concept_revision: value.concept_revision,
    preferred_name: value.preferred_name,
  }
}

function parseStep(value: unknown): ChatGraphStep | null {
  if (
    !isRecord(value) ||
    typeof value.ordinal !== 'number' ||
    !Number.isInteger(value.ordinal) ||
    value.ordinal < 0 ||
    typeof value.relation_id !== 'string' ||
    !value.relation_id ||
    !isPositiveInteger(value.relation_revision) ||
    typeof value.relation_type !== 'string' ||
    !RELATION_TYPES.has(value.relation_type as ConceptRelationType) ||
    typeof value.support_basis !== 'string' ||
    !SUPPORT_BASES.has(value.support_basis as RelationSupportBasis) ||
    typeof value.from_concept_id !== 'string' ||
    !value.from_concept_id ||
    typeof value.to_concept_id !== 'string' ||
    !value.to_concept_id ||
    typeof value.traversed_against_relation_direction !== 'boolean'
  ) {
    return null
  }
  return {
    ordinal: value.ordinal as number,
    relation_id: value.relation_id,
    relation_revision: value.relation_revision,
    relation_type: value.relation_type as ConceptRelationType,
    support_basis: value.support_basis as RelationSupportBasis,
    from_concept_id: value.from_concept_id,
    to_concept_id: value.to_concept_id,
    traversed_against_relation_direction:
      value.traversed_against_relation_direction,
  }
}

function parseChatGraphContext(
  metadata: Record<string, unknown>,
): ChatGraphContext | null {
  const value = metadata.graph_context
  if (
    !isRecord(value) ||
    value.schema_version !== 1 ||
    typeof value.course_id !== 'string' ||
    !value.course_id ||
    !isPositiveInteger(value.graph_version) ||
    !isHash(value.graph_content_hash) ||
    !isHash(value.result_hash) ||
    value.strategy !== 'relationship_trace' ||
    !Array.isArray(value.concepts) ||
    value.concepts.length < 2 ||
    !Array.isArray(value.steps) ||
    value.steps.length === 0
  ) {
    return null
  }
  const concepts = value.concepts.map(parseConcept)
  const steps = value.steps.map(parseStep)
  if (
    concepts.some((item) => item === null) ||
    steps.some((item) => item === null)
  ) {
    return null
  }
  const parsedConcepts = concepts as ChatGraphConcept[]
  const parsedSteps = (steps as ChatGraphStep[]).sort(
    (left, right) => left.ordinal - right.ordinal,
  )
  const conceptIds = new Set(parsedConcepts.map((item) => item.concept_id))
  const relationIds = new Set(parsedSteps.map((item) => item.relation_id))
  if (
    conceptIds.size !== parsedConcepts.length ||
    relationIds.size !== parsedSteps.length ||
    parsedConcepts.length !== parsedSteps.length + 1 ||
    parsedSteps.some((step, index) => (
      step.ordinal !== index ||
      step.from_concept_id !== parsedConcepts[index].concept_id ||
      step.to_concept_id !== parsedConcepts[index + 1].concept_id
    ))
  ) {
    return null
  }
  return {
    schema_version: 1,
    course_id: value.course_id,
    graph_version: value.graph_version,
    graph_content_hash: value.graph_content_hash,
    result_hash: value.result_hash,
    strategy: 'relationship_trace',
    concepts: parsedConcepts,
    steps: parsedSteps,
  }
}

function relationLabel(value: ConceptRelationType): string {
  return value.replaceAll('_', ' ')
}

function supportBasisLabel(value: RelationSupportBasis): string {
  return value.replaceAll('_', ' ')
}

export function ChatGraphContextDisclosure({
  metadata,
}: {
  metadata: Record<string, unknown>
}) {
  const context = parseChatGraphContext(metadata)
  if (!context) return null

  const conceptsById = new Map(
    context.concepts.map((concept) => [concept.concept_id, concept]),
  )
  const pathLabel = `${context.steps.length} hop${context.steps.length === 1 ? '' : 's'}`

  return (
    <details className="chat-graph-context">
      <summary>
        <span>
          <Network aria-hidden="true" size={14} />
          Graph-guided context
        </span>
        <small>
          Published v{context.graph_version} - {pathLabel}
        </small>
      </summary>
      <div className="chat-graph-context-body">
        <p>
          This published graph snapshot supplied the route. The source
          citations above remain the only factual evidence.
        </p>
        <ol aria-label="Graph route used for this answer">
          {context.steps.map((step) => {
            const from = conceptsById.get(step.from_concept_id)
            const to = conceptsById.get(step.to_concept_id)
            if (!from || !to) return null
            return (
              <li key={step.relation_id}>
                <strong>{from.preferred_name}</strong>
                <span>
                  <ArrowRight aria-hidden="true" size={13} />
                  {relationLabel(step.relation_type)}
                  {' - '}
                  {supportBasisLabel(step.support_basis)}
                  {step.traversed_against_relation_direction
                    ? ' - reverse traversal'
                    : ''}
                </span>
                <strong>{to.preferred_name}</strong>
              </li>
            )
          })}
        </ol>
      </div>
    </details>
  )
}
