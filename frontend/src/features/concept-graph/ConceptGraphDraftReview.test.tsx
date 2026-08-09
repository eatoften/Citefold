import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ConceptGraphApiError } from './conceptGraphApi'
import { ConceptGraphDraftReview } from './ConceptGraphDraftReview'
import type {
  DraftConcept,
  DraftConceptSummary,
  DraftRelationSummary,
  GraphPublicationPreview,
  GraphVersionMetadata,
} from './conceptGraphTypes'

const apiMocks = vi.hoisted(() => ({
  editDraftConcept: vi.fn(),
  editDraftRelation: vi.fn(),
  getDraftConcept: vi.fn(),
  getDraftRelation: vi.fn(),
  getGraphPublicationPreview: vi.fn(),
  listAllDraftConcepts: vi.fn(),
  listAllDraftRelations: vi.fn(),
  publishGraphVersion: vi.fn(),
  reviewDraftConcept: vi.fn(),
  reviewDraftRelation: vi.fn(),
}))

vi.mock('./conceptGraphApi', async (importOriginal) => ({
  ...await importOriginal<typeof import('./conceptGraphApi')>(),
  ...apiMocks,
}))

const TIMESTAMP = '2026-08-09T00:00:00Z'

function preview(overrides: Partial<GraphPublicationPreview> = {}): GraphPublicationPreview {
  return {
    active_version: 3,
    draft_manifest_hash: 'd'.repeat(64),
    content_hash: 'c'.repeat(64),
    publishable: true,
    has_changes: true,
    issues: [],
    issue_count: 0,
    issues_truncated: false,
    counts: {
      concepts: 1,
      relations: 0,
      concept_aliases: 0,
      concept_evidence: 1,
      relation_evidence: 0,
    },
    computed_at: TIMESTAMP,
    ...overrides,
  }
}

function publishedVersion(): GraphVersionMetadata {
  return {
    course_id: 'course-a',
    version_number: 4,
    parent_version_number: 3,
    draft_manifest_hash: 'd'.repeat(64),
    content_hash: 'e'.repeat(64),
    counts: preview().counts,
    published_by: 'local-maintainer',
    publication_reason: 'Ship reviewed graph',
    published_at: TIMESTAMP,
    is_active_version: true,
    source_authority_current: true,
    source_authority_issues: [],
    source_authority_issue_count: 0,
    source_authority_issues_truncated: false,
  }
}

function conceptSummary(revision: number, preferredName: string): DraftConceptSummary {
  return {
    id: 'concept-a',
    course_id: 'course-a',
    revision,
    preferred_name: preferredName,
    short_definition: `Definition at revision ${revision}.`,
    identity_status: 'active',
    merged_into_concept_id: null,
    review_status: 'accepted',
    validity_status: 'current',
    proposal_origin: 'human',
    provider: null,
    model: null,
    prompt_protocol: null,
    output_version: null,
    review_actor: 'maintainer',
    reviewed_at: TIMESTAMP,
    review_revision: revision - 1,
    created_at: TIMESTAMP,
    updated_at: TIMESTAMP,
    evidence_count: 1,
  }
}

function conceptDetail(revision: number, preferredName: string): DraftConcept {
  return {
    ...conceptSummary(revision, preferredName),
    aliases: [],
    evidence: [{
      id: `evidence-${revision}`,
      course_id: 'course-a',
      concept_id: 'concept-a',
      concept_revision: revision,
      source_id: 'source-a',
      chunk_id: 'chunk-a',
      chunk_text_hash: 'a'.repeat(64),
      projection_generation_id: 'projection-a',
      projection_is_current: true,
      projection_currentness_reasons: [],
      source_title: 'Lecture slides',
      source_type: 'pdf',
      quote: 'Grounded evidence.',
      locator: {
        schema_version: 1,
        kind: 'pdf_page',
        asset_id: 'asset-a',
        page_number: 7,
        metadata: {},
      },
      ordinal: 0,
      created_at: TIMESTAMP,
    }],
    is_current_revision: true,
    evidence_current: true,
    eligible_for_publication: true,
    currentness_reasons: [],
  }
}

function relationSummary(validity: 'current' | 'stale'): DraftRelationSummary {
  return {
    id: 'relation-ab',
    course_id: 'course-a',
    revision: validity === 'current' ? 4 : 3,
    source_concept_id: 'concept-a',
    target_concept_id: 'concept-b',
    relation_type: 'prerequisite',
    support_basis: 'source_asserted',
    rationale: 'Endpoint A prepares Endpoint B.',
    review_status: 'accepted',
    validity_status: validity,
    proposal_origin: 'human',
    provider: null,
    model: null,
    prompt_protocol: null,
    output_version: null,
    review_actor: 'maintainer',
    reviewed_at: TIMESTAMP,
    review_revision: 2,
    created_at: TIMESTAMP,
    updated_at: TIMESTAMP,
    evidence_count: 1,
  }
}

describe('ConceptGraphDraftReview', () => {
  beforeEach(() => {
    for (const mock of Object.values(apiMocks)) mock.mockReset()
    apiMocks.listAllDraftConcepts.mockResolvedValue([])
    apiMocks.listAllDraftRelations.mockResolvedValue([])
    apiMocks.getGraphPublicationPreview.mockResolvedValue(preview())
  })

  it('publishes the exact preview snapshot and reports the new version', async () => {
    const user = userEvent.setup()
    const version = publishedVersion()
    const onPublished = vi.fn()
    const endpointA = conceptSummary(3, 'Endpoint A')
    const endpointB = {
      ...conceptSummary(2, 'Endpoint B'),
      id: 'concept-b',
    }
    const terminalCandidate = {
      ...conceptSummary(5, 'Merged duplicate'),
      id: 'concept-merged',
      identity_status: 'merged' as const,
      merged_into_concept_id: 'concept-a',
      review_status: 'candidate' as const,
    }
    apiMocks.listAllDraftConcepts.mockResolvedValue([
      endpointA,
      endpointB,
      terminalCandidate,
    ])
    apiMocks.listAllDraftRelations
      .mockResolvedValueOnce([relationSummary('stale')])
      .mockResolvedValue([relationSummary('current')])
    apiMocks.getDraftConcept.mockResolvedValue(conceptDetail(3, 'Endpoint A'))
    apiMocks.publishGraphVersion.mockResolvedValue(version)

    render(
      <ConceptGraphDraftReview
        apiBaseUrl="http://api.test"
        courseId="course-a"
        onPublished={onPublished}
      />,
    )

    const reasonInput = await screen.findByRole('textbox', {
      name: 'Publication reason',
    })
    await user.type(
      reasonInput,
      'Ship reviewed graph',
    )
    const publishButton = screen.getByRole('button', {
      name: 'Publish graph version',
    })
    expect(publishButton).toBeDisabled()
    expect(screen.getByText(/repair 1 unresolved draft head/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Refresh draft' }))
    await waitFor(() => expect(apiMocks.listAllDraftRelations).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(publishButton).toBeEnabled())
    await user.click(publishButton)

    await waitFor(() => expect(apiMocks.publishGraphVersion).toHaveBeenCalledOnce())
    expect(apiMocks.publishGraphVersion).toHaveBeenCalledWith(
      'http://api.test',
      'course-a',
      expect.objectContaining({
        actor: 'local-maintainer',
        reason: 'Ship reviewed graph',
        expected_active_version: 3,
        expected_draft_manifest_hash: 'd'.repeat(64),
        operation_id: expect.stringMatching(/^publish-graph-/),
      }),
      expect.anything(),
    )
    expect(onPublished).toHaveBeenCalledWith(version)
  })

  it('loads the server revision after a conflict instead of bypassing CAS', async () => {
    const user = userEvent.setup()
    const revisionOne = conceptDetail(1, 'Server name v1')
    const revisionTwo = conceptDetail(2, 'Server name v2')
    apiMocks.listAllDraftConcepts
      .mockResolvedValueOnce([conceptSummary(1, 'Server name v1')])
      .mockResolvedValue([conceptSummary(2, 'Server name v2')])
    apiMocks.getDraftConcept
      .mockResolvedValueOnce(revisionOne)
      .mockResolvedValue(revisionTwo)
    apiMocks.editDraftConcept.mockRejectedValue(
      new ConceptGraphApiError('Concept revision conflict.', 409, 'revision_conflict'),
    )

    render(
      <ConceptGraphDraftReview
        apiBaseUrl="http://api.test"
        courseId="course-a"
        onPublished={vi.fn()}
      />,
    )

    const nameInput = await screen.findByRole('textbox', { name: 'Preferred name' })
    await user.clear(nameInput)
    await user.type(nameInput, 'My unsaved overwrite')
    await user.type(
      screen.getByRole('textbox', { name: 'Edit reason' }),
      'Attempt an edit',
    )
    await user.click(screen.getByRole('button', { name: 'Save Concept revision' }))

    expect(await screen.findByText(/Concept revision conflict/)).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByRole('textbox', { name: 'Preferred name' })).toHaveValue(
        'Server name v2',
      )
    })
    expect(screen.getByRole('textbox', { name: 'Edit reason' })).toHaveValue('')
    expect(apiMocks.getDraftConcept).toHaveBeenCalledTimes(2)
    expect(apiMocks.editDraftConcept).toHaveBeenCalledOnce()
  })
})
