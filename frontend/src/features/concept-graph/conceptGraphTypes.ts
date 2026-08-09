import type {
  SourceLocator,
  SourceType,
} from '../sources/sourceTypes'

export type ConceptRelationType =
  | 'prerequisite'
  | 'part_of'
  | 'example_of'
  | 'related'
  | 'contrast_with'

export type GraphDirectionMode = 'outgoing' | 'incoming' | 'both'

export type GraphPublicationCounts = {
  concepts: number
  relations: number
  concept_aliases: number
  concept_evidence: number
  relation_evidence: number
}

export type GraphPublicationIssue = {
  code: string
  entity_type: 'concept' | 'relation' | 'graph' | 'source'
  entity_id: string | null
  revision: number | null
  message: string
}

export type GraphVersionMetadata = {
  course_id: string
  version_number: number
  parent_version_number: number | null
  draft_manifest_hash: string
  content_hash: string
  counts: GraphPublicationCounts
  published_by: string
  publication_reason: string
  published_at: string
  is_active_version: boolean
  source_authority_current: boolean
  source_authority_issues: GraphPublicationIssue[]
  source_authority_issue_count: number
  source_authority_issues_truncated: boolean
}

export type PublishedEvidence = {
  evidence_id: string
  source_id: string
  chunk_id: string
  chunk_text_hash: string
  projection_generation_id: string
  source_title: string
  source_type: SourceType
  quote: string
  locator: SourceLocator
  ordinal: number
  created_at: string
}

export type PublishedConceptAlias = {
  alias_id: string
  display_text: string
  normalized_text: string
  ordinal: number
  created_at: string
}

export type PublishedConcept = {
  concept_id: string
  concept_revision: number
  preferred_name: string
  short_definition: string
  identity_status: 'active' | 'merged' | 'retired'
  review_status: 'candidate' | 'accepted' | 'rejected'
  validity_status: 'current' | 'stale' | 'tombstoned'
  proposal_origin: 'human' | 'model' | 'import'
  aggregate_hash: string
  ordinal: number
  aliases: PublishedConceptAlias[]
  evidence: PublishedEvidence[]
}

export type PublishedRelationEvidence = PublishedEvidence & {
  support_role:
    | 'relation_assertion'
    | 'source_endpoint'
    | 'target_endpoint'
}

export type PublishedRelation = {
  relation_id: string
  relation_revision: number
  source_concept_id: string
  source_concept_revision: number
  target_concept_id: string
  target_concept_revision: number
  relation_type: ConceptRelationType
  support_basis: 'source_asserted' | 'pedagogical_inference'
  rationale: string
  review_status: 'candidate' | 'accepted' | 'rejected'
  validity_status: 'current' | 'stale' | 'tombstoned'
  proposal_origin: 'human' | 'model' | 'import'
  aggregate_hash: string
  ordinal: number
  evidence: PublishedRelationEvidence[]
}

export type PublishedConceptPage = {
  items: PublishedConcept[]
  next_cursor: string | null
}

export type LocalGraphResult = {
  kind: 'local_graph'
  course_id: string
  graph_version: number
  graph_content_hash: string
  root_concept_id: string
  relation_types: ConceptRelationType[]
  direction_mode: GraphDirectionMode
  max_hops: number
  max_nodes: number
  truncated_by_max_nodes: boolean
  nodes: Array<{ distance: number; concept: PublishedConcept }>
  relations: PublishedRelation[]
  result_hash: string
}

export type RelationshipTraceResult = {
  kind: 'relationship_trace'
  course_id: string
  graph_version: number
  graph_content_hash: string
  source_concept_id: string
  target_concept_id: string
  relation_types: ConceptRelationType[]
  direction_mode: GraphDirectionMode
  max_hops: number
  max_nodes: number
  status: 'found' | 'unreachable' | 'limits_reached'
  truncated_by_max_hops: boolean
  truncated_by_max_nodes: boolean
  hop_count: number | null
  nodes: PublishedConcept[]
  steps: Array<{
    ordinal: number
    from_concept_id: string
    to_concept_id: string
    traversed_against_relation_direction: boolean
    relation: PublishedRelation
  }>
  result_hash: string
}

export type LearningPathResult = {
  kind: 'learning_path'
  course_id: string
  graph_version: number
  graph_content_hash: string
  target_concept_id: string
  max_nodes: number
  nodes: PublishedConcept[]
  relations: PublishedRelation[]
  layers: Array<{ index: number; concept_ids: string[] }>
  linearization: string[]
  result_hash: string
}

export type GraphEvidenceIdentity = {
  course_id: string
  graph_version: number
  graph_content_hash: string
  owner:
    | {
        kind: 'concept'
        concept_id: string
        concept_revision: number
      }
    | {
        kind: 'relation'
        relation_id: string
        relation_revision: number
      }
  evidence_id: string
  source_id: string
  chunk_id: string
  chunk_text_hash: string
  projection_generation_id: string
}

export type GraphEvidenceSelection = {
  identity: GraphEvidenceIdentity
  evidence: PublishedEvidence
}
