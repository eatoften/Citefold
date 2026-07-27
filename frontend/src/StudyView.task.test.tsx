import {
  act,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReliableTask } from './features/reliability'
import type {
  LearningDocumentDetail,
  LearningDocumentGenerationResult,
  StudyCard,
} from './studyTypes'

const taskApi = vi.hoisted(() => ({
  enqueueLearningDocumentGeneration: vi.fn(),
  waitForReliableTask: vi.fn(),
  retryReliableTask: vi.fn(),
  cancelReliableTask: vi.fn(),
  clearDraft: vi.fn(),
}))

vi.mock('./features/reliability', () => ({
  SaveStatus: () => null,
  useAutosavedDraft: () => ({
    state: 'saved',
    message: 'Saved',
    clearDraft: taskApi.clearDraft,
  }),
  enqueueLearningDocumentGeneration:
    taskApi.enqueueLearningDocumentGeneration,
  waitForReliableTask: taskApi.waitForReliableTask,
  retryReliableTask: taskApi.retryReliableTask,
  cancelReliableTask: taskApi.cancelReliableTask,
}))

import { StudyView } from './StudyView'

const TIMESTAMP = '2026-07-27T10:00:00Z'

const card: StudyCard = {
  id: 'card-1',
  job_id: 'job-1',
  title: 'Retrieval',
  summary: 'Retrieval summary',
  card_kind: 'concept',
  tags: [],
  content_status: 'reviewed',
  review_item_count: 0,
  learning_document_count: 1,
  source_video: 'lecture.mp4',
  source_start_seconds: 0,
  source_end_seconds: 10,
  note_count: 0,
}

function documentDetail(
  values: Partial<LearningDocumentDetail> = {},
): LearningDocumentDetail {
  return {
    id: 'document-1',
    course_id: 'course-1',
    title: 'Retrieval notes',
    summary: 'Initial summary',
    body_markdown: 'Initial body',
    status: 'draft',
    generation_mode: 'manual',
    provider: null,
    model: null,
    created_at: TIMESTAMP,
    updated_at: TIMESTAMP,
    card_links: [
      {
        id: 'link-1',
        document_id: 'document-1',
        card_id: card.id,
        role: 'primary_anchor',
        position: 0,
        created_at: TIMESTAMP,
      },
    ],
    sources: [],
    versions: [],
    ...values,
  }
}

function task<TResult extends object>(
  values: Partial<ReliableTask<TResult>> = {},
): ReliableTask<TResult> {
  return {
    id: 'generation-task-1',
    kind: 'learning_document_generation',
    course_id: 'course-1',
    resource_type: 'learning_document',
    resource_id: 'document-1',
    status: 'queued',
    payload: {},
    result: null,
    idempotency_key: null,
    active_key: null,
    priority: 0,
    attempt: 1,
    max_attempts: 3,
    recovery_count: 0,
    progress: {
      current: 0,
      total: 2,
      stage: 'queued',
      message: 'Queued',
      details: {},
    },
    cancel_requested_at: null,
    worker_id: null,
    error_code: null,
    error_message: null,
    retryable: true,
    available_at: TIMESTAMP,
    created_at: TIMESTAMP,
    updated_at: TIMESTAMP,
    started_at: null,
    completed_at: null,
    heartbeat_at: null,
    ...values,
  }
}

describe('StudyView durable generation tasks', () => {
  let generated: LearningDocumentDetail
  let generationResult: LearningDocumentGenerationResult
  let generationFinished: boolean

  beforeEach(() => {
    vi.clearAllMocks()
    generated = documentDetail({
      title: 'Generated retrieval guide',
      summary: 'Grounded generated summary',
      body_markdown: '## Generated explanation',
      generation_mode: 'local_llm',
    })
    generationResult = {
      document: generated,
      selected_source_units: 2,
      selected_cards: 1,
      warning: null,
    }
    generationFinished = false
    taskApi.enqueueLearningDocumentGeneration.mockResolvedValue(task())
    taskApi.retryReliableTask.mockResolvedValue(task({ attempt: 2 }))

    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL): Promise<Response> => {
        const url = String(input)
        let payload: unknown
        if (url.endsWith('/courses/course-1/card-index')) {
          payload = [card]
        } else if (url.endsWith('/courses/course-1/learning-documents')) {
          payload = [
            generationFinished ? generated : documentDetail(),
          ]
        } else if (url.endsWith('/courses/course-1/source-assets')) {
          payload = []
        } else if (url.endsWith('/learning-documents/document-1')) {
          payload = generationFinished ? generated : documentDetail()
        } else {
          throw new Error(`Unexpected Study request: ${url}`)
        }
        return Promise.resolve(
          new Response(JSON.stringify(payload), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
      }),
    )
  })

  function renderStudy() {
    return render(
      <StudyView
        apiBaseUrl="http://api.test"
        courses={[
          { id: 'course-1', title: 'Course One', card_count: 1 },
        ]}
        selectedCourseId="course-1"
        selectedModel="test-model"
        showCourseSelector={false}
        initialCardId={card.id}
        initialDocumentId="document-1"
        onSelectCourse={vi.fn()}
      />,
    )
  }

  it('enqueues and polls document generation before applying the result', async () => {
    taskApi.waitForReliableTask.mockImplementation(async () => {
      generationFinished = true
      return task({
        status: 'succeeded',
        result: { generation: generationResult },
        completed_at: TIMESTAMP,
      })
    })
    renderStudy()

    await userEvent.click(
      await screen.findByRole('button', {
        name: /Generate grounded draft/,
      }, { timeout: 3_000 }),
    )

    await screen.findByText('Generated explanation')
    expect(
      taskApi.enqueueLearningDocumentGeneration,
    ).toHaveBeenCalledWith(
      'http://api.test',
      'document-1',
      expect.objectContaining({
        source_asset_ids: [],
        supporting_card_ids: [],
        model: 'test-model',
      }),
      expect.any(AbortSignal),
    )
    expect(taskApi.waitForReliableTask).toHaveBeenCalledWith(
      'http://api.test',
      'generation-task-1',
      expect.objectContaining({
        signal: expect.any(AbortSignal),
      }),
    )
    expect(taskApi.clearDraft).toHaveBeenCalled()
  })

  it('retries a failed generation through the existing task id', async () => {
    taskApi.waitForReliableTask
      .mockRejectedValueOnce(new Error('Model temporarily unavailable.'))
      .mockImplementationOnce(async () => {
        generationFinished = true
        return task({
          status: 'succeeded',
          result: { generation: generationResult },
          attempt: 2,
          completed_at: TIMESTAMP,
        })
      })
    renderStudy()

    await userEvent.click(
      await screen.findByRole('button', {
        name: /Generate grounded draft/,
      }),
    )
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Model temporarily unavailable.',
    )

    await userEvent.click(
      screen.getByRole('button', { name: 'Retry generation' }),
    )

    await waitFor(() => {
      expect(taskApi.retryReliableTask).toHaveBeenCalledWith(
        'http://api.test',
        'generation-task-1',
      )
    })
    expect(
      taskApi.enqueueLearningDocumentGeneration,
    ).toHaveBeenCalledTimes(1)
    expect(await screen.findByText('Generated explanation')).toBeVisible()
  })

  it('ignores a late cancellation after selecting another document', async () => {
    const firstDocument = documentDetail()
    const secondDocument = documentDetail({
      id: 'document-2',
      title: 'Second document',
      body_markdown: 'Second document body',
      card_links: [
        {
          id: 'link-2',
          document_id: 'document-2',
          card_id: card.id,
          role: 'primary_anchor',
          position: 0,
          created_at: TIMESTAMP,
        },
      ],
    })
    let resolveCancel:
      | ((value: ReliableTask) => void)
      | undefined
    const cancelResponse = new Promise<ReliableTask>((resolve) => {
      resolveCancel = resolve
    })
    taskApi.cancelReliableTask.mockReturnValue(cancelResponse)
    taskApi.waitForReliableTask.mockImplementation(
      (
        _apiBaseUrl: string,
        _taskId: string,
        options: { signal?: AbortSignal },
      ) =>
        new Promise((_, reject) => {
          options.signal?.addEventListener(
            'abort',
            () =>
              reject(
                new DOMException(
                  'Generation aborted.',
                  'AbortError',
                ),
              ),
            { once: true },
          )
        }),
    )
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL): Promise<Response> => {
        const url = String(input)
        let payload: unknown
        if (url.endsWith('/courses/course-1/card-index')) {
          payload = [card]
        } else if (
          url.endsWith('/courses/course-1/learning-documents')
        ) {
          payload = [firstDocument, secondDocument]
        } else if (
          url.endsWith('/courses/course-1/source-assets')
        ) {
          payload = []
        } else if (
          url.endsWith('/learning-documents/document-1')
        ) {
          payload = firstDocument
        } else if (
          url.endsWith('/learning-documents/document-2')
        ) {
          payload = secondDocument
        } else {
          throw new Error(`Unexpected Study request: ${url}`)
        }
        return Promise.resolve(
          new Response(JSON.stringify(payload), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
      }),
    )
    renderStudy()

    await userEvent.click(
      await screen.findByRole('button', {
        name: /Generate grounded draft/,
      }),
    )
    await userEvent.click(
      await screen.findByRole('button', { name: 'Cancel' }),
    )
    await userEvent.click(
      screen.getByRole('button', {
        name: /Second document/,
      }),
    )
    expect(await screen.findByText('Second document body')).toBeVisible()

    await act(async () => {
      resolveCancel?.(
        task({
          status: 'canceling',
          progress: {
            current: 0,
            total: 2,
            stage: 'canceling',
            message: 'Stale first-document cancellation',
            details: {},
          },
        }),
      )
      await Promise.resolve()
    })

    expect(screen.getByText('Second document body')).toBeVisible()
    expect(
      screen.queryByText('Stale first-document cancellation'),
    ).not.toBeInTheDocument()
  })
})
