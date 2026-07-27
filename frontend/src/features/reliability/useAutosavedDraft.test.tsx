import {
  act,
  fireEvent,
  render,
  screen,
} from '@testing-library/react'
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest'
import { useCallback, useState } from 'react'
import type { WorkspaceDraft } from './draftTypes'
import { ReliabilityProvider } from './ReliabilityContext'
import { useAutosavedDraft } from './useAutosavedDraft'

const draftApi = vi.hoisted(() => ({
  getWorkspaceDraft: vi.fn(),
  putWorkspaceDraft: vi.fn(),
  deleteWorkspaceDraft: vi.fn(),
}))

vi.mock('./draftApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./draftApi')>()
  return {
    ...actual,
    getWorkspaceDraft: draftApi.getWorkspaceDraft,
    putWorkspaceDraft: draftApi.putWorkspaceDraft,
    deleteWorkspaceDraft: draftApi.deleteWorkspaceDraft,
  }
})

type DraftValue = {
  body: string
}

const API_BASE_URL = 'http://api.test'
const DRAFT_ID = 'chat:course-1'
const STORAGE_KEY = `vcc:workspace-draft:${DRAFT_ID}`
const INITIAL_VALUE: DraftValue = { body: '' }
const DIRTY_VALUE: DraftValue = { body: 'Local explanation' }
const SERVER_TIME = '2026-07-27T10:10:00.000Z'
const NOOP_RESTORE = () => undefined

function serverDraft(
  payload: DraftValue,
  updatedAt = SERVER_TIME,
): WorkspaceDraft<DraftValue> {
  return {
    id: DRAFT_ID,
    course_id: 'course-1',
    draft_type: 'chat_composer',
    entity_id: null,
    payload,
    revision: 2,
    base_updated_at: null,
    created_at: '2026-07-27T10:00:00.000Z',
    updated_at: updatedAt,
  }
}

function DraftHarness({
  onRestore = NOOP_RESTORE,
}: {
  onRestore?: (value: DraftValue) => void
}) {
  const [value, setValue] = useState<DraftValue>(INITIAL_VALUE)
  const restore = useCallback(
    (payload: DraftValue) => {
      setValue(payload)
      onRestore(payload)
    },
    [onRestore],
  )
  const draft = useAutosavedDraft({
    apiBaseUrl: API_BASE_URL,
    draftId: DRAFT_ID,
    courseId: 'course-1',
    draftType: 'chat_composer',
    enabled: true,
    value,
    initialValue: INITIAL_VALUE,
    onRestore: restore,
  })

  return (
    <div>
      <button type="button" onClick={() => setValue(DIRTY_VALUE)}>
        Edit draft
      </button>
      <output data-testid="draft-state">{draft.state}</output>
      <output data-testid="draft-value">{value.body}</output>
    </div>
  )
}

function renderDraft(onRestore?: (value: DraftValue) => void) {
  return render(
    <ReliabilityProvider>
      <DraftHarness onRestore={onRestore} />
    </ReliabilityProvider>,
  )
}

async function flushHydration(): Promise<void> {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

describe('useAutosavedDraft', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.useFakeTimers()
    vi.clearAllMocks()
    window.localStorage.clear()
    draftApi.getWorkspaceDraft.mockResolvedValue(null)
    draftApi.deleteWorkspaceDraft.mockResolvedValue(undefined)
  })

  afterEach(() => {
    vi.useRealTimers()
    window.localStorage.clear()
  })

  it('protects the edit in localStorage before starting the server save', async () => {
    draftApi.putWorkspaceDraft.mockResolvedValue(
      serverDraft(DIRTY_VALUE),
    )
    renderDraft()
    await flushHydration()

    fireEvent.click(screen.getByRole('button', { name: 'Edit draft' }))

    const local = JSON.parse(
      window.localStorage.getItem(STORAGE_KEY) ?? 'null',
    ) as { payload: DraftValue }
    expect(local.payload).toEqual(DIRTY_VALUE)
    expect(screen.getByTestId('draft-state')).toHaveTextContent('saving')
    expect(draftApi.putWorkspaceDraft).not.toHaveBeenCalled()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })

    expect(draftApi.putWorkspaceDraft).toHaveBeenCalledWith(
      API_BASE_URL,
      DRAFT_ID,
      expect.objectContaining({
        course_id: 'course-1',
        draft_type: 'chat_composer',
        payload: DIRTY_VALUE,
        expected_revision: null,
      }),
      expect.any(AbortSignal),
    )
    expect(screen.getByTestId('draft-state')).toHaveTextContent('saved')
  })

  it('does not overwrite text entered while draft recovery is loading', async () => {
    let resolveServer:
      | ((value: WorkspaceDraft<DraftValue> | null) => void)
      | undefined
    draftApi.getWorkspaceDraft.mockReturnValue(
      new Promise<WorkspaceDraft<DraftValue> | null>((resolve) => {
        resolveServer = resolve
      }),
    )
    draftApi.putWorkspaceDraft.mockResolvedValue(
      serverDraft(DIRTY_VALUE),
    )
    const onRestore = vi.fn()
    renderDraft(onRestore)

    fireEvent.click(screen.getByRole('button', { name: 'Edit draft' }))
    await act(async () => {
      resolveServer?.(
        serverDraft({ body: 'Recovered server explanation' }),
      )
      await Promise.resolve()
    })

    expect(onRestore).not.toHaveBeenCalled()
    expect(screen.getByTestId('draft-value')).toHaveTextContent(
      DIRTY_VALUE.body,
    )
    expect(
      JSON.parse(
        window.localStorage.getItem(STORAGE_KEY) ?? 'null',
      ).payload,
    ).toEqual(DIRTY_VALUE)
  })

  it('keeps the device copy when the server save fails', async () => {
    draftApi.putWorkspaceDraft.mockRejectedValue(
      new Error('backend offline'),
    )
    renderDraft()
    await flushHydration()

    fireEvent.click(screen.getByRole('button', { name: 'Edit draft' }))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })

    expect(screen.getByTestId('draft-state')).toHaveTextContent(
      'saved_local',
    )
    const local = JSON.parse(
      window.localStorage.getItem(STORAGE_KEY) ?? 'null',
    ) as { payload: DraftValue }
    expect(local.payload).toEqual(DIRTY_VALUE)
  })

  it.each([
    {
      name: 'device copy',
      localUpdatedAt: '2026-07-27T10:20:00.000Z',
      serverUpdatedAt: '2026-07-27T10:10:00.000Z',
      serverValue: { body: 'Older server explanation' },
      expected: { body: 'Newer device explanation' },
    },
    {
      name: 'server copy',
      localUpdatedAt: '2026-07-27T10:10:00.000Z',
      serverUpdatedAt: '2026-07-27T10:20:00.000Z',
      serverValue: { body: 'Newer server explanation' },
      expected: { body: 'Newer server explanation' },
    },
  ])('restores the newest $name', async ({
    localUpdatedAt,
    serverUpdatedAt,
    serverValue,
    expected,
  }) => {
    const localPayload = { body: 'Newer device explanation' }
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        schema_version: 1,
        draft_id: DRAFT_ID,
        course_id: 'course-1',
        draft_type: 'chat_composer',
        entity_id: null,
        payload: localPayload,
        base_updated_at: null,
        updated_at: localUpdatedAt,
      }),
    )
    draftApi.getWorkspaceDraft.mockResolvedValue(
      serverDraft(serverValue, serverUpdatedAt),
    )
    draftApi.putWorkspaceDraft.mockResolvedValue(
      serverDraft(expected, serverUpdatedAt),
    )
    const onRestore = vi.fn()

    renderDraft(onRestore)
    await flushHydration()

    expect(onRestore).toHaveBeenCalledWith(expected)
    expect(screen.getByTestId('draft-value')).toHaveTextContent(
      expected.body,
    )
  })

  it('does not install a leave warning once localStorage protects the edit', async () => {
    const addListener = vi.spyOn(window, 'addEventListener')
    draftApi.putWorkspaceDraft.mockReturnValue(new Promise(() => undefined))
    renderDraft()
    await flushHydration()

    fireEvent.click(screen.getByRole('button', { name: 'Edit draft' }))
    await act(async () => {
      await Promise.resolve()
    })

    expect(window.localStorage.getItem(STORAGE_KEY)).not.toBeNull()
    expect(
      addListener.mock.calls.some(([type]) => type === 'beforeunload'),
    ).toBe(false)
  })

  it('installs a leave warning only while the edit has no local protection', async () => {
    const addListener = vi.spyOn(window, 'addEventListener')
    const removeListener = vi.spyOn(window, 'removeEventListener')
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(
      () => undefined,
    )
    draftApi.putWorkspaceDraft.mockResolvedValue(
      serverDraft(DIRTY_VALUE),
    )
    renderDraft()
    await flushHydration()

    fireEvent.click(screen.getByRole('button', { name: 'Edit draft' }))
    await act(async () => {
      await Promise.resolve()
    })

    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull()
    expect(
      addListener.mock.calls.some(([type]) => type === 'beforeunload'),
    ).toBe(true)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })

    expect(screen.getByTestId('draft-state')).toHaveTextContent('saved')
    expect(
      removeListener.mock.calls.some(([type]) => type === 'beforeunload'),
    ).toBe(true)
  })

  it('keeps the leave warning when localStorage throws', async () => {
    const addListener = vi.spyOn(window, 'addEventListener')
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('Storage unavailable.', 'SecurityError')
    })
    draftApi.putWorkspaceDraft.mockRejectedValue(
      new Error('backend offline'),
    )
    renderDraft()
    await flushHydration()

    fireEvent.click(screen.getByRole('button', { name: 'Edit draft' }))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })

    expect(screen.getByTestId('draft-state')).toHaveTextContent(
      'sync_failed',
    )
    expect(
      addListener.mock.calls.some(([type]) => type === 'beforeunload'),
    ).toBe(true)
  })

  it('does not mistake an older device draft for the latest edit', async () => {
    const olderValue = { body: 'Older device explanation' }
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        schema_version: 2,
        workspace_generation: 1,
        draft_id: DRAFT_ID,
        course_id: 'course-1',
        draft_type: 'chat_composer',
        entity_id: null,
        payload: olderValue,
        base_updated_at: null,
        updated_at: '2026-07-27T10:20:00.000Z',
      }),
    )
    const addListener = vi.spyOn(window, 'addEventListener')
    draftApi.putWorkspaceDraft.mockRejectedValue(
      new Error('backend offline'),
    )
    renderDraft()
    await flushHydration()
    expect(screen.getByTestId('draft-value')).toHaveTextContent(
      olderValue.body,
    )

    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('Storage full.', 'QuotaExceededError')
    })
    fireEvent.click(screen.getByRole('button', { name: 'Edit draft' }))
    await act(async () => {
      await Promise.resolve()
    })

    expect(screen.getByTestId('draft-state')).toHaveTextContent(
      'sync_failed',
    )
    expect(
      JSON.parse(
        window.localStorage.getItem(STORAGE_KEY) ?? 'null',
      ).payload,
    ).toEqual(olderValue)
    expect(
      addListener.mock.calls.some(([type]) => type === 'beforeunload'),
    ).toBe(true)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })
    expect(screen.getByTestId('draft-state')).toHaveTextContent(
      'sync_failed',
    )
  })

  it('does not restore a device draft from an older workspace generation', async () => {
    window.localStorage.setItem('vcc:workspace-generation', '2')
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        schema_version: 2,
        workspace_generation: 1,
        draft_id: DRAFT_ID,
        course_id: 'course-1',
        draft_type: 'chat_composer',
        entity_id: null,
        payload: { body: 'Draft from replaced workspace' },
        base_updated_at: null,
        updated_at: '2026-07-27T10:20:00.000Z',
      }),
    )
    const onRestore = vi.fn()

    renderDraft(onRestore)
    await flushHydration()

    expect(onRestore).not.toHaveBeenCalled()
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull()
    const keys = Array.from(
      { length: window.localStorage.length },
      (_, index) => window.localStorage.key(index),
    )
    expect(
      keys.some((key) =>
        key?.startsWith('vcc:workspace-draft-quarantine:'),
      ),
    ).toBe(true)
  })
})
