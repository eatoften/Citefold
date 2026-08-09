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

export type GraphReviewStatus = 'candidate' | 'accepted' | 'rejected'

export type GraphValidityStatus = 'current' | 'stale' | 'tombstoned'

export type ConceptIdentityStatus = 'active' | 'merged' | 'retired'

export type ProposalOrigin = 'human' | 'model' | 'import'

export type RelationSupportBasis =
  | 'source_asserted'
  | 'pedagogical_inference'

export type RelationEvidenceSupportRole =
  | 'relation_assertion'
  | 'source_endpoint'
  | 'target_endpoint'

export type GraphReviewDecision = 'accept' | 'reject'

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

type DraftCandidateRecord = {
  revision: number
  review_status: GraphReviewStatus
  validity_status: GraphValidityStatus
  proposal_origin: ProposalOrigin
  provider: string | null
  model: string | null
  prompt_protocol: string | null
  output_version: string | null
  review_actor: string | null
  reviewed_at: string | null
  review_revision: number | null
  created_at: string
  updated_at: string
}

export type DraftEvidenceReference = {
  chunk_id: string
  quote: string
}

export type DraftRelationEvidenceReference = DraftEvidenceReference & {
  support_role: RelationEvidenceSupportRole
}

export type DraftEvidence = {
  id: string
  course_id: string
  source_id: string
  chunk_id: string
  chunk_text_hash: string
  projection_generation_id: string | null
  projection_is_current: boolean
  projection_currentness_reasons: string[]
  source_title: string
  source_type: SourceType
  quote: string
  locator: SourceLocator
  ordinal: number
  created_at: string
}

export type DraftConceptEvidence = DraftEvidence & {
  concept_id: string
  concept_revision: number
}

export type DraftRelationEvidence = DraftEvidence & {
  relation_id: string
  relation_revision: number
  support_role: RelationEvidenceSupportRole
}

export type DraftConceptAlias = {
  id: string
  course_id: string
  concept_id: string
  concept_revision: number
  display_text: string
  normalized_text: string
  ordinal: number
  created_at: string
}

type DraftConceptRecord = DraftCandidateRecord & {
  id: string
  course_id: string
  preferred_name: string
  short_definition: string
  identity_status: ConceptIdentityStatus
  merged_into_concept_id: string | null
}

export type DraftConceptSummary = DraftConceptRecord & {
  evidence_count: number
}

export type DraftConcept = DraftConceptRecord & {
  evidence: DraftConceptEvidence[]
  aliases: DraftConceptAlias[]
  is_current_revision: boolean
  evidence_current: boolean
  eligible_for_publication: boolean
  currentness_reasons: string[]
}

export type DraftConceptPage = {
  items: DraftConceptSummary[]
  next_cursor: string | null
}

export type DraftRelationEndpointBinding = {
  relation_id: string
  course_id: string
  relation_revision: number
  source_concept_id: string
  source_concept_revision: number
  target_concept_id: string
  target_concept_revision: number
  created_at: string
}

type DraftRelationRecord = DraftCandidateRecord & {
  id: string
  course_id: string
  source_concept_id: string
  target_concept_id: string
  relation_type: ConceptRelationType
  support_basis: RelationSupportBasis
  rationale: string
}

export type DraftRelationSummary = DraftRelationRecord & {
  evidence_count: number
}

export type DraftRelation = DraftRelationRecord & {
  evidence: DraftRelationEvidence[]
  endpoint_binding: DraftRelationEndpointBinding | null
  is_current_revision: boolean
  evidence_current: boolean
  endpoint_revisions_current: boolean
  eligible_for_publication: boolean
  currentness_reasons: string[]
}

export type DraftRelationPage = {
  items: DraftRelationSummary[]
  next_cursor: string | null
}

export type GraphOperationRequest = {
  operation_id: string
  actor: string
  reason: string
}

export type DraftConceptEditRequest = GraphOperationRequest & {
  expected_revision: number
  preferred_name: string
  short_definition: string
  aliases: string[]
  evidence: DraftEvidenceReference[]
}

export type DraftConceptReviewRequest = GraphOperationRequest & {
  expected_revision: number
  decision: GraphReviewDecision
}

export type DraftRelationEditRequest = GraphOperationRequest & {
  expected_revision: number
  support_basis: RelationSupportBasis
  rationale: string
  expected_source_concept_revision: number
  expected_target_concept_revision: number
  evidence: DraftRelationEvidenceReference[]
}

export type DraftRelationReviewRequest = DraftConceptReviewRequest & {
  expected_source_concept_revision: number
  expected_target_concept_revision: number
}

export type GraphPublicationPreview = {
  active_version: number | null
  draft_manifest_hash: string
  content_hash: string
  publishable: boolean
  has_changes: boolean
  issues: GraphPublicationIssue[]
  issue_count: number
  issues_truncated: boolean
  counts: GraphPublicationCounts
  computed_at: string
}

export type GraphPublicationRequest = GraphOperationRequest & {
  expected_active_version: number | null
  expected_draft_manifest_hash: string
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
