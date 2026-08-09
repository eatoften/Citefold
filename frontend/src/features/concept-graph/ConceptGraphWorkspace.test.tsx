import {
  act,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ConceptGraphWorkspace } from './ConceptGraphWorkspace'
import type {
  GraphVersionMetadata,
  PublishedConcept,
  PublishedEvidence,
  PublishedRelation,
  RelationshipTraceResult,
} from './conceptGraphTypes'

type Deferred<T> = {
  promise: Promise<T>
  resolve: (value: T) => void
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => { resolve = next })
  return { promise, resolve }
}

function jsonResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(payload),
  } as unknown as Response
}

function version(
  courseId: string,
  contentHash = 'a'.repeat(64),
): GraphVersionMetadata {
  return {
    course_id: courseId,
    version_number: 3,
    parent_version_number: 2,
    draft_manifest_hash: 'd'.repeat(64),
    content_hash: contentHash,
    counts: {
      concepts: 3,
      relations: 2,
      concept_aliases: 0,
      concept_evidence: 3,
      relation_evidence: 2,
    },
    published_by: 'maintainer',
    publication_reason: 'test fixture',
    published_at: '2026-08-09T00:00:00Z',
    is_active_version: true,
    source_authority_current: true,
    source_authority_issues: [],
    source_authority_issue_count: 0,
    source_authority_issues_truncated: false,
  }
}

function evidence(id: string): PublishedEvidence {
  return {
    evidence_id: id,
    source_id: 'source-slides',
    chunk_id: `chunk-${id}`,
    chunk_text_hash: 'c'.repeat(64),
    projection_generation_id: 'projection-7',
    source_title: 'Lecture slides',
    source_type: 'pdf',
    quote: `Evidence for ${id}`,
    locator: {
      schema_version: 1,
      kind: 'pdf_page',
      asset_id: 'asset-slides',
      page_number: 4,
      metadata: {},
    },
    ordinal: 0,
    created_at: '2026-08-09T00:00:00Z',
  }
}

function concept(id: string, name: string): PublishedConcept {
  return {
    concept_id: id,
    concept_revision: 1,
    preferred_name: name,
    short_definition: `${name} definition`,
    identity_status: 'active',
    review_status: 'accepted',
    validity_status: 'current',
    proposal_origin: 'human',
    aggregate_hash: id.padEnd(64, '0'),
    ordinal: 0,
    aliases: [],
    evidence: [evidence(`concept-${id}`)],
  }
}

const CONCEPTS = [
  concept('alpha', 'Alpha'),
  concept('beta', 'Beta'),
  concept('gamma', 'Gamma'),
]

function relation(
  id: string,
  source: string,
  target: string,
): PublishedRelation {
  return {
    relation_id: id,
    relation_revision: 2,
    source_concept_id: source,
    source_concept_revision: 1,
    target_concept_id: target,
    target_concept_revision: 1,
    relation_type: 'prerequisite',
    support_basis: 'source_asserted',
    rationale: `${source} prepares ${target}`,
    review_status: 'accepted',
    validity_status: 'current',
    proposal_origin: 'human',
    aggregate_hash: id.padEnd(64, '1'),
    ordinal: 0,
    evidence: [{ ...evidence(`edge-${id}`), support_role: 'relation_assertion' }],
  }
}

const RELATION_ALPHA_BETA = relation('relation-ab', 'alpha', 'beta')
const RELATION_BETA_GAMMA = relation('relation-bg', 'beta', 'gamma')

function trace(
  status: RelationshipTraceResult['status'],
): RelationshipTraceResult {
  const found = status === 'found'
  return {
    kind: 'relationship_trace',
    course_id: 'course-a',
    graph_version: 3,
    graph_content_hash: 'a'.repeat(64),
    source_concept_id: 'alpha',
    target_concept_id: found ? 'gamma' : 'beta',
    relation_types: ['prerequisite'],
    direction_mode: 'outgoing',
    max_hops: 6,
    max_nodes: 200,
    status,
    truncated_by_max_hops: status === 'limits_reached',
    truncated_by_max_nodes: false,
    hop_count: found ? 2 : null,
    nodes: found ? CONCEPTS : [],
    steps: found ? [
      {
        ordinal: 0,
        from_concept_id: 'alpha',
        to_concept_id: 'beta',
        traversed_against_relation_direction: false,
        relation: RELATION_ALPHA_BETA,
      },
      {
        ordinal: 1,
        from_concept_id: 'beta',
        to_concept_id: 'gamma',
        traversed_against_relation_direction: false,
        relation: RELATION_BETA_GAMMA,
      },
    ] : [],
    result_hash: 'f'.repeat(64),
  }
}

function graphFetch(pathResult: RelationshipTraceResult) {
  return vi.fn<typeof fetch>().mockImplementation((input) => {
    const url = String(input)
    if (url.endsWith('/versions/current')) {
      return Promise.resolve(jsonResponse(version('course-a')))
    }
    if (url.includes('/concepts?')) {
      return Promise.resolve(jsonResponse({ items: CONCEPTS, next_cursor: null }))
    }
    if (url.includes('/paths/trace?')) {
      return Promise.resolve(jsonResponse(pathResult))
    }
    throw new Error(`Unexpected request: ${url}`)
  })
}

const baseProps = {
  apiBaseUrl: 'http://api.test',
  selectedCourseId: 'course-a',
}

describe('ConceptGraphWorkspace', () => {
  it('keeps the server trace order and opens evidence with graph identity', async () => {
    const user = userEvent.setup()
    const onOpenEvidence = vi.fn()
    vi.stubGlobal('fetch', graphFetch(trace('found')))

    render(
      <ConceptGraphWorkspace
        {...baseProps}
        onOpenEvidence={onOpenEvidence}
      />,
    )
    await screen.findByText('Alpha definition')
    await user.click(screen.getByRole('tab', { name: 'Trace' }))
    await user.selectOptions(screen.getByLabelText('To'), 'gamma')
    await user.click(screen.getByRole('button', { name: 'Find trace' }))

    expect(await screen.findByText('Shortest trace: 2 hops.')).toBeInTheDocument()
    expect(screen.getAllByRole('article').map((item) => (
      item.getAttribute('aria-label')
    ))).toEqual([
      'Step 1: Alpha to Beta',
      'Step 2: Beta to Gamma',
    ])

    await user.click(within(screen.getAllByRole('article')[0]).getByRole(
      'button',
      { name: /Alpha -> Beta/ },
    ))
    await user.click(screen.getByRole('button', {
      name: 'Open evidence 1 from Lecture slides',
    }))
    expect(onOpenEvidence).toHaveBeenCalledWith(
      expect.objectContaining({
        identity: expect.objectContaining({
          course_id: 'course-a',
          graph_version: 3,
          graph_content_hash: 'a'.repeat(64),
          owner: {
            kind: 'relation',
            relation_id: 'relation-ab',
            relation_revision: 2,
          },
          evidence_id: 'edge-relation-ab',
          source_id: 'source-slides',
          chunk_id: 'chunk-edge-relation-ab',
          chunk_text_hash: 'c'.repeat(64),
          projection_generation_id: 'projection-7',
        }),
      }),
      expect.any(HTMLButtonElement),
    )
  })

  it('distinguishes unreachable from an inconclusive limit result', async () => {
    const user = userEvent.setup()
    const fetchMock = graphFetch(trace('unreachable'))
    fetchMock.mockImplementationOnce(() => (
      Promise.resolve(jsonResponse(version('course-a')))
    ))
    fetchMock.mockImplementationOnce(() => (
      Promise.resolve(jsonResponse({ items: CONCEPTS, next_cursor: null }))
    ))
    fetchMock.mockImplementationOnce(() => (
      Promise.resolve(jsonResponse(trace('unreachable')))
    ))
    fetchMock.mockImplementationOnce(() => (
      Promise.resolve(jsonResponse(trace('limits_reached')))
    ))
    vi.stubGlobal('fetch', fetchMock)

    render(<ConceptGraphWorkspace {...baseProps} />)
    await screen.findByText('Alpha definition')
    await user.click(screen.getByRole('tab', { name: 'Trace' }))
    await user.click(screen.getByRole('button', { name: 'Find trace' }))
    expect(await screen.findByText('No relationship path')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Find trace' }))
    expect(await screen.findByText('Search limits reached')).toBeInTheDocument()
    expect(screen.getByText(/not proof that the Concepts are disconnected/i))
      .toBeInTheDocument()
  })

  it('aborts a prior course and ignores its late response while showing stale authority', async () => {
    const courseA = deferred<Response>()
    const staleVersion = {
      ...version('course-b', 'b'.repeat(64)),
      source_authority_current: false,
    }
    const fetchMock = vi.fn<typeof fetch>().mockImplementation((input) => {
      const url = String(input)
      if (url.includes('/course-a/')) {
        return courseA.promise
      }
      if (url.includes('/course-b/')) {
        return Promise.resolve(jsonResponse({
          detail: {
            code: 'concept_graph_source_authority_stale',
            message: 'One Source projection changed.',
            version: staleVersion,
          },
        }, 409))
      }
      throw new Error(`Unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    const view = render(<ConceptGraphWorkspace {...baseProps} />)
    view.rerender(
      <ConceptGraphWorkspace
        {...baseProps}
        selectedCourseId="course-b"
      />,
    )

    expect(await screen.findByText('Source evidence is stale')).toBeInTheDocument()
    expect(screen.getByText('One Source projection changed.')).toBeInTheDocument()
    expect(screen.getByText(`b`.repeat(64))).toBeInTheDocument()
    const courseASignal = (
      fetchMock.mock.calls[0]?.[1] as RequestInit | undefined
    )?.signal
    expect(courseASignal?.aborted).toBe(true)

    await act(async () => {
      courseA.resolve(jsonResponse(version('course-a')))
      await courseA.promise
    })
    await waitFor(() => {
      expect(screen.getByText('Source evidence is stale')).toBeInTheDocument()
    })
    expect(screen.queryByText('Alpha definition')).not.toBeInTheDocument()
  })
})
