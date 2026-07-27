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
import {
  StrictMode,
  useCallback,
  useState,
} from 'react'
import type { WorkspaceDraft } from './draftTypes'
import { DraftConflictError } from './draftApi'
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
  baseUpdatedAt: string | null = null,
  revision = 2,
): WorkspaceDraft<DraftValue> {
  return {
    id: DRAFT_ID,
    course_id: 'course-1',
    draft_type: 'chat_composer',
    entity_id: null,
    payload,
    revision,
    base_updated_at: baseUpdatedAt,
    created_at: '2026-07-27T10:00:00.000Z',
    updated_at: updatedAt,
  }
}

function storeDeviceDraft({
  payload,
  updatedAt,
  baseUpdatedAt = null,
}: {
  payload: DraftValue
  updatedAt: string
  baseUpdatedAt?: string | null
}) {
  window.localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      schema_version: 2,
      workspace_generation: 1,
      draft_id: DRAFT_ID,
      course_id: 'course-1',
      draft_type: 'notebook_note',
      entity_id: 'note-1',
      payload,
      base_updated_at: baseUpdatedAt,
      updated_at: updatedAt,
    }),
  )
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
      <button type="button" onClick={() => setValue(INITIAL_VALUE)}>
        Reset to saved value
      </button>
      <button
        type="button"
        onClick={() => void draft.clearDraft()}
      >
        Clear draft
      </button>
      <button
        type="button"
        disabled={!draft.recoveryConflict}
        onClick={draft.restoreRecoveryDraft}
      >
        Restore conflict
      </button>
      <button
        type="button"
        disabled={!draft.recoveryConflict}
        onClick={() => void draft.discardRecoveryDraft()}
      >
        Discard conflict
      </button>
      <output data-testid="draft-state">{draft.state}</output>
      <output data-testid="draft-value">{value.body}</output>
      <output data-testid="draft-recovery">
        {draft.recoveryConflict?.body ?? ''}
      </output>
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

const CURRENT_NOTE_VALUE = { body: 'Current saved note' }
const CURRENT_NOTE_BASE = '2026-07-27T11:00:00.000Z'

function StrictBaseDraftHarness() {
  const [value, setValue] =
    useState<DraftValue>(CURRENT_NOTE_VALUE)
  const restore = useCallback((payload: DraftValue) => {
    setValue(payload)
  }, [])
  const draft = useAutosavedDraft({
    apiBaseUrl: API_BASE_URL,
    draftId: DRAFT_ID,
    courseId: 'course-1',
    draftType: 'notebook_note',
    entityId: 'note-1',
    baseUpdatedAt: CURRENT_NOTE_BASE,
    enabled: true,
    value,
    initialValue: CURRENT_NOTE_VALUE,
    onRestore: restore,
    requireMatchingBaseUpdatedAt: true,
  })

  return (
    <div>
      <output data-testid="draft-state">{draft.state}</output>
      <output data-testid="draft-value">{value.body}</output>
      <output data-testid="recovery-value">
        {draft.recoveryConflict?.body ?? ''}
      </output>
      <button
        type="button"
        disabled={!draft.recoveryConflict}
        onClick={draft.restoreRecoveryDraft}
      >
        Restore recovery
      </button>
      <button
        type="button"
        disabled={!draft.recoveryConflict}
        onClick={() => void draft.discardRecoveryDraft()}
      >
        Discard recovery
      </button>
    </div>
  )
}

function renderStrictBaseDraft() {
  return render(
    <ReliabilityProvider>
      <StrictBaseDraftHarness />
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
        expected_revision: 0,
      }),
      expect.any(AbortSignal),
    )
    expect(screen.getByTestId('draft-state')).toHaveTextContent('saved')
  })

  it('uses create-only CAS when pending hydration observes absence before another editor creates', async () => {
    let resolveServer:
      | ((value: WorkspaceDraft<DraftValue> | null) => void)
      | undefined
    const concurrent = serverDraft(
      { body: 'Created by another editor' },
      SERVER_TIME,
      null,
      1,
    )
    draftApi.getWorkspaceDraft.mockReturnValue(
      new Promise<WorkspaceDraft<DraftValue> | null>((resolve) => {
        resolveServer = resolve
      }),
    )
    draftApi.putWorkspaceDraft.mockRejectedValue(
      new DraftConflictError(
        'Draft revision conflict.',
        concurrent,
      ),
    )
    renderDraft()

    fireEvent.click(screen.getByRole('button', { name: 'Edit draft' }))
    await act(async () => {
      resolveServer?.(null)
      await Promise.resolve()
    })
    await flushHydration()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })

    expect(draftApi.putWorkspaceDraft).toHaveBeenCalledWith(
      API_BASE_URL,
      DRAFT_ID,
      expect.objectContaining({
        payload: DIRTY_VALUE,
        expected_revision: 0,
      }),
      expect.any(AbortSignal),
    )
    expect(screen.getByTestId('draft-state')).toHaveTextContent('conflict')
    expect(screen.getByTestId('draft-recovery')).toHaveTextContent(
      concurrent.payload.body,
    )
  })

  it('uses create-only CAS when hydration fails before another editor creates', async () => {
    let rejectServer: ((reason?: unknown) => void) | undefined
    const concurrent = serverDraft(
      { body: 'Created while hydration was offline' },
      SERVER_TIME,
      null,
      1,
    )
    draftApi.getWorkspaceDraft.mockReturnValue(
      new Promise<WorkspaceDraft<DraftValue> | null>((_resolve, reject) => {
        rejectServer = reject
      }),
    )
    draftApi.putWorkspaceDraft.mockRejectedValue(
      new DraftConflictError(
        'Draft revision conflict.',
        concurrent,
      ),
    )
    renderDraft()

    fireEvent.click(screen.getByRole('button', { name: 'Edit draft' }))
    await act(async () => {
      rejectServer?.(new Error('Hydration unavailable.'))
      await Promise.resolve()
    })
    await flushHydration()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })

    expect(draftApi.putWorkspaceDraft).toHaveBeenCalledWith(
      API_BASE_URL,
      DRAFT_ID,
      expect.objectContaining({
        payload: DIRTY_VALUE,
        expected_revision: 0,
      }),
      expect.any(AbortSignal),
    )
    expect(screen.getByTestId('draft-state')).toHaveTextContent('conflict')
    expect(screen.getByTestId('draft-recovery')).toHaveTextContent(
      concurrent.payload.body,
    )
  })

  it('hydrates and autosaves when mounted in React StrictMode', async () => {
    draftApi.putWorkspaceDraft.mockResolvedValue(
      serverDraft(DIRTY_VALUE),
    )

    render(
      <StrictMode>
        <ReliabilityProvider>
          <DraftHarness />
        </ReliabilityProvider>
      </StrictMode>,
    )
    await flushHydration()

    expect(draftApi.getWorkspaceDraft).toHaveBeenCalledTimes(2)
    fireEvent.click(screen.getByRole('button', { name: 'Edit draft' }))
    expect(
      JSON.parse(
        window.localStorage.getItem(STORAGE_KEY) ?? 'null',
      ).payload,
    ).toEqual(DIRTY_VALUE)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })

    expect(draftApi.putWorkspaceDraft).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('draft-state')).toHaveTextContent('saved')
  })

  it('protects an edit immediately while server hydration is still pending', async () => {
    let resolveFirst:
      | ((value: WorkspaceDraft<DraftValue> | null) => void)
      | undefined
    draftApi.getWorkspaceDraft.mockReturnValue(
      new Promise<WorkspaceDraft<DraftValue> | null>((resolve) => {
        resolveFirst = resolve
      }),
    )
    storeDeviceDraft({
      payload: { body: 'Older device recovery' },
      updatedAt: '2026-07-27T09:00:00.000Z',
    })
    const first = renderDraft()

    fireEvent.click(screen.getByRole('button', { name: 'Edit draft' }))

    expect(
      JSON.parse(
        window.localStorage.getItem(STORAGE_KEY) ?? 'null',
      ).payload,
    ).toEqual(DIRTY_VALUE)
    const quarantineKeys = Array.from(
      { length: window.localStorage.length },
      (_, index) => window.localStorage.key(index),
    ).filter((key) =>
      key?.startsWith('vcc:workspace-draft-quarantine:'),
    )
    expect(quarantineKeys).toHaveLength(1)
    expect(
      window.localStorage.getItem(quarantineKeys[0] ?? ''),
    ).toContain('Older device recovery')

    first.unmount()
    draftApi.getWorkspaceDraft.mockResolvedValue(null)
    const onRestore = vi.fn()
    renderDraft(onRestore)
    await flushHydration()

    expect(onRestore).toHaveBeenCalledWith(DIRTY_VALUE)
    expect(screen.getByTestId('draft-value')).toHaveTextContent(
      DIRTY_VALUE.body,
    )
    expect(resolveFirst).toBeTypeOf('function')
  })

  it('does not restart pending hydration when the restore callback changes identity', async () => {
    let resolveServer:
      | ((value: WorkspaceDraft<DraftValue> | null) => void)
      | undefined
    draftApi.getWorkspaceDraft.mockReturnValue(
      new Promise<WorkspaceDraft<DraftValue> | null>((resolve) => {
        resolveServer = resolve
      }),
    )
    const rendered = render(
      <ReliabilityProvider>
        <DraftHarness onRestore={() => undefined} />
      </ReliabilityProvider>,
    )

    rendered.rerender(
      <ReliabilityProvider>
        <DraftHarness onRestore={() => undefined} />
      </ReliabilityProvider>,
    )
    rendered.rerender(
      <ReliabilityProvider>
        <DraftHarness onRestore={() => undefined} />
      </ReliabilityProvider>,
    )
    expect(draftApi.getWorkspaceDraft).toHaveBeenCalledTimes(1)

    await act(async () => {
      resolveServer?.(null)
      await Promise.resolve()
    })
    fireEvent.click(screen.getByRole('button', { name: 'Edit draft' }))
    expect(
      JSON.parse(
        window.localStorage.getItem(STORAGE_KEY) ?? 'null',
      ).payload,
    ).toEqual(DIRTY_VALUE)
  })

  it('keeps a distinct server draft separate from text entered during hydration', async () => {
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
    renderDraft()

    fireEvent.click(screen.getByRole('button', { name: 'Edit draft' }))
    await act(async () => {
      resolveServer?.(
        serverDraft({ body: 'Distinct server recovery' }),
      )
      await Promise.resolve()
    })

    expect(screen.getByTestId('draft-value')).toHaveTextContent(
      DIRTY_VALUE.body,
    )
    expect(screen.getByTestId('draft-recovery')).toHaveTextContent(
      'Distinct server recovery',
    )
    expect(screen.getByTestId('draft-state')).toHaveTextContent(
      'conflict',
    )
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })
    expect(draftApi.putWorkspaceDraft).not.toHaveBeenCalled()
    expect(
      JSON.parse(
        window.localStorage.getItem(STORAGE_KEY) ?? 'null',
      ).payload,
    ).toEqual(DIRTY_VALUE)

    fireEvent.click(
      screen.getByRole('button', { name: 'Restore conflict' }),
    )

    expect(screen.getByTestId('draft-value')).toHaveTextContent(
      'Distinct server recovery',
    )
    const quarantineKeys = Array.from(
      { length: window.localStorage.length },
      (_, index) => window.localStorage.key(index),
    ).filter((key) =>
      key?.startsWith('vcc:workspace-draft-quarantine:'),
    )
    expect(quarantineKeys).toHaveLength(1)
    expect(
      window.localStorage.getItem(quarantineKeys[0] ?? ''),
    ).toContain(DIRTY_VALUE.body)
  })

  it('rebuilds a preferred device draft and alternate server conflict after remount', async () => {
    const preferred = { body: 'Preferred device draft' }
    const alternate = { body: 'Alternate server draft' }
    storeDeviceDraft({
      payload: preferred,
      updatedAt: '2026-07-27T12:00:00.000Z',
    })
    draftApi.getWorkspaceDraft.mockResolvedValue(
      serverDraft(
        alternate,
        '2026-07-27T10:00:00.000Z',
      ),
    )
    draftApi.putWorkspaceDraft.mockResolvedValue(
      serverDraft(preferred),
    )

    const first = renderDraft()
    await flushHydration()

    expect(screen.getByTestId('draft-value')).toHaveTextContent(
      preferred.body,
    )
    expect(screen.getByTestId('draft-recovery')).toHaveTextContent(
      alternate.body,
    )
    expect(screen.getByTestId('draft-state')).toHaveTextContent(
      'conflict',
    )
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })
    expect(draftApi.putWorkspaceDraft).not.toHaveBeenCalled()

    first.unmount()
    renderDraft()
    await flushHydration()

    expect(screen.getByTestId('draft-value')).toHaveTextContent(
      preferred.body,
    )
    expect(screen.getByTestId('draft-recovery')).toHaveTextContent(
      alternate.body,
    )
    expect(draftApi.putWorkspaceDraft).not.toHaveBeenCalled()
  })

  it('restores the alternate while quarantining the previous preferred payload', async () => {
    const preferred = { body: 'Preferred server draft' }
    const alternate = { body: 'Alternate device draft' }
    storeDeviceDraft({
      payload: alternate,
      updatedAt: '2026-07-27T10:00:00.000Z',
    })
    draftApi.getWorkspaceDraft.mockResolvedValue(
      serverDraft(
        preferred,
        '2026-07-27T12:00:00.000Z',
      ),
    )
    draftApi.putWorkspaceDraft.mockResolvedValue(
      serverDraft(alternate),
    )
    renderDraft()
    await flushHydration()

    expect(screen.getByTestId('draft-value')).toHaveTextContent(
      preferred.body,
    )
    expect(screen.getByTestId('draft-recovery')).toHaveTextContent(
      alternate.body,
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Restore conflict' }),
    )

    expect(screen.getByTestId('draft-value')).toHaveTextContent(
      alternate.body,
    )
    expect(screen.getByTestId('draft-recovery')).toBeEmptyDOMElement()
    const quarantineKeys = Array.from(
      { length: window.localStorage.length },
      (_, index) => window.localStorage.key(index),
    ).filter((key) =>
      key?.startsWith('vcc:workspace-draft-quarantine:'),
    )
    expect(quarantineKeys).toHaveLength(1)
    expect(
      window.localStorage.getItem(quarantineKeys[0] ?? ''),
    ).toContain(preferred.body)
  })

  it('discards only the alternate decision and resynchronizes the preferred payload', async () => {
    const preferred = { body: 'Preferred device draft' }
    const alternate = { body: 'Discarded server draft' }
    storeDeviceDraft({
      payload: preferred,
      updatedAt: '2026-07-27T12:00:00.000Z',
    })
    draftApi.getWorkspaceDraft.mockResolvedValue(
      serverDraft(
        alternate,
        '2026-07-27T10:00:00.000Z',
      ),
    )
    draftApi.putWorkspaceDraft.mockResolvedValue(
      serverDraft(preferred),
    )
    renderDraft()
    await flushHydration()

    fireEvent.click(
      screen.getByRole('button', { name: 'Discard conflict' }),
    )
    await flushHydration()

    expect(screen.getByTestId('draft-value')).toHaveTextContent(
      preferred.body,
    )
    expect(screen.getByTestId('draft-recovery')).toBeEmptyDOMElement()
    expect(
      JSON.parse(
        window.localStorage.getItem(STORAGE_KEY) ?? 'null',
      ).payload,
    ).toEqual(preferred)
    expect(draftApi.deleteWorkspaceDraft).not.toHaveBeenCalled()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })
    expect(draftApi.putWorkspaceDraft).toHaveBeenCalledWith(
      API_BASE_URL,
      DRAFT_ID,
      expect.objectContaining({
        payload: preferred,
        expected_revision: 2,
      }),
      expect.any(AbortSignal),
    )
  })

  it('keeps the latest third-editor payload as a conflict after discarding the alternate', async () => {
    const preferred = { body: 'Preferred device draft' }
    const alternate = { body: 'Discarded server draft' }
    const latest = { body: 'Latest third-editor draft' }
    storeDeviceDraft({
      payload: preferred,
      updatedAt: '2026-07-27T12:00:00.000Z',
    })
    draftApi.getWorkspaceDraft.mockResolvedValue(
      serverDraft(
        alternate,
        '2026-07-27T10:00:00.000Z',
      ),
    )
    draftApi.putWorkspaceDraft.mockRejectedValue(
      new DraftConflictError(
        'This draft changed in another editor.',
        serverDraft(
          latest,
          '2026-07-27T12:30:00.000Z',
          null,
          3,
        ),
      ),
    )
    renderDraft()
    await flushHydration()

    fireEvent.click(
      screen.getByRole('button', { name: 'Discard conflict' }),
    )
    await flushHydration()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })

    expect(draftApi.deleteWorkspaceDraft).not.toHaveBeenCalled()
    expect(draftApi.putWorkspaceDraft).toHaveBeenCalledWith(
      API_BASE_URL,
      DRAFT_ID,
      expect.objectContaining({
        payload: preferred,
        expected_revision: 2,
      }),
      expect.any(AbortSignal),
    )
    expect(screen.getByTestId('draft-value')).toHaveTextContent(
      preferred.body,
    )
    expect(screen.getByTestId('draft-recovery')).toHaveTextContent(
      latest.body,
    )
    expect(screen.getByTestId('draft-state')).toHaveTextContent(
      'conflict',
    )
  })

  it('re-reads a third-editor update after a conditional discard delete fails', async () => {
    const stale = { body: 'Stale server recovery' }
    const latest = { body: 'Latest third-editor recovery' }
    draftApi.getWorkspaceDraft
      .mockResolvedValueOnce(
        serverDraft(
          stale,
          '2026-07-27T10:00:00.000Z',
          '2026-07-27T09:00:00.000Z',
        ),
      )
      .mockResolvedValueOnce(
        serverDraft(
          latest,
          '2026-07-27T12:30:00.000Z',
          '2026-07-27T09:00:00.000Z',
          3,
        ),
      )
    draftApi.deleteWorkspaceDraft.mockRejectedValue(
      new Error('Draft revision conflict.'),
    )
    draftApi.putWorkspaceDraft.mockResolvedValue(
      serverDraft(
        latest,
        '2026-07-27T12:31:00.000Z',
        CURRENT_NOTE_BASE,
        4,
      ),
    )
    renderStrictBaseDraft()
    await flushHydration()

    fireEvent.click(
      screen.getByRole('button', { name: 'Discard recovery' }),
    )
    await flushHydration()

    expect(draftApi.deleteWorkspaceDraft).toHaveBeenCalledWith(
      API_BASE_URL,
      DRAFT_ID,
      2,
    )
    expect(screen.getByTestId('draft-value')).toHaveTextContent(
      CURRENT_NOTE_VALUE.body,
    )
    expect(screen.getByTestId('recovery-value')).toHaveTextContent(
      latest.body,
    )
    expect(screen.getByTestId('draft-state')).toHaveTextContent(
      'conflict',
    )

    fireEvent.click(
      screen.getByRole('button', { name: 'Restore recovery' }),
    )
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })

    expect(draftApi.putWorkspaceDraft).toHaveBeenCalledWith(
      API_BASE_URL,
      DRAFT_ID,
      expect.objectContaining({
        payload: latest,
        expected_revision: 3,
      }),
      expect.any(AbortSignal),
    )
  })

  it('uses a create-only revision after keeping a dirty value when no server draft exists', async () => {
    const alternate = { body: 'Older device recovery' }
    let resolveServer:
      | ((value: WorkspaceDraft<DraftValue> | null) => void)
      | undefined
    storeDeviceDraft({
      payload: alternate,
      updatedAt: '2026-07-27T10:00:00.000Z',
    })
    draftApi.getWorkspaceDraft.mockReturnValue(
      new Promise<WorkspaceDraft<DraftValue> | null>((resolve) => {
        resolveServer = resolve
      }),
    )
    draftApi.putWorkspaceDraft.mockResolvedValue(
      serverDraft(DIRTY_VALUE),
    )
    renderDraft()

    fireEvent.click(screen.getByRole('button', { name: 'Edit draft' }))
    await act(async () => {
      resolveServer?.(null)
      await Promise.resolve()
    })
    expect(screen.getByTestId('draft-recovery')).toHaveTextContent(
      alternate.body,
    )

    fireEvent.click(
      screen.getByRole('button', { name: 'Discard conflict' }),
    )
    await flushHydration()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })

    expect(draftApi.deleteWorkspaceDraft).not.toHaveBeenCalled()
    expect(draftApi.putWorkspaceDraft).toHaveBeenCalledWith(
      API_BASE_URL,
      DRAFT_ID,
      expect.objectContaining({
        payload: DIRTY_VALUE,
        expected_revision: 0,
      }),
      expect.any(AbortSignal),
    )
  })

  it('cancels a pending server sync when the domain save clears its draft', async () => {
    draftApi.putWorkspaceDraft.mockResolvedValue(
      serverDraft(DIRTY_VALUE),
    )
    renderDraft()
    await flushHydration()

    fireEvent.click(screen.getByRole('button', { name: 'Edit draft' }))
    fireEvent.click(
      screen.getByRole('button', { name: 'Clear draft' }),
    )
    await flushHydration()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })

    expect(draftApi.putWorkspaceDraft).not.toHaveBeenCalled()
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it('never deletes an unseen server draft after hydration observed absence', async () => {
    draftApi.getWorkspaceDraft.mockResolvedValue(null)
    draftApi.putWorkspaceDraft.mockResolvedValue(
      serverDraft(DIRTY_VALUE),
    )
    renderDraft()
    await flushHydration()

    fireEvent.click(
      screen.getByRole('button', { name: 'Clear draft' }),
    )
    await flushHydration()

    expect(draftApi.deleteWorkspaceDraft).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Edit draft' }))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })

    expect(draftApi.putWorkspaceDraft).toHaveBeenCalledWith(
      API_BASE_URL,
      DRAFT_ID,
      expect.objectContaining({
        payload: DIRTY_VALUE,
        expected_revision: 0,
      }),
      expect.any(AbortSignal),
    )
  })

  it('uses create-only CAS after returning to the saved value when cleanup conflicts', async () => {
    const serverValue = { body: 'Server draft at revision two' }
    draftApi.getWorkspaceDraft.mockResolvedValue(
      serverDraft(serverValue, SERVER_TIME, null, 2),
    )
    draftApi.deleteWorkspaceDraft.mockRejectedValue(
      new Error('Draft revision conflict.'),
    )
    draftApi.putWorkspaceDraft.mockResolvedValue(
      serverDraft(DIRTY_VALUE, SERVER_TIME, null, 4),
    )
    renderDraft()
    await flushHydration()

    expect(screen.getByTestId('draft-value')).toHaveTextContent(
      serverValue.body,
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Reset to saved value' }),
    )
    await flushHydration()

    expect(draftApi.deleteWorkspaceDraft).toHaveBeenCalledWith(
      API_BASE_URL,
      DRAFT_ID,
      2,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Edit draft' }))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })

    expect(draftApi.putWorkspaceDraft).toHaveBeenCalledWith(
      API_BASE_URL,
      DRAFT_ID,
      expect.objectContaining({
        payload: DIRTY_VALUE,
        expected_revision: 0,
      }),
      expect.any(AbortSignal),
    )
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

  it('prefers a matching server draft while preserving a stale device alternate', async () => {
    const matching = { body: 'Matching server draft' }
    storeDeviceDraft({
      payload: { body: 'Newer stale device draft' },
      baseUpdatedAt: '2026-07-27T09:00:00.000Z',
      updatedAt: '2026-07-27T12:00:00.000Z',
    })
    draftApi.getWorkspaceDraft.mockResolvedValue(
      serverDraft(
        matching,
        '2026-07-27T10:00:00.000Z',
        CURRENT_NOTE_BASE,
      ),
    )

    renderStrictBaseDraft()
    await flushHydration()

    expect(screen.getByTestId('draft-value')).toHaveTextContent(
      matching.body,
    )
    expect(screen.getByTestId('recovery-value')).toHaveTextContent(
      'Newer stale device draft',
    )
    expect(screen.getByTestId('draft-state')).toHaveTextContent(
      'conflict',
    )
  })

  it('prefers a matching device draft while preserving a stale server alternate', async () => {
    const matching = { body: 'Matching device draft' }
    storeDeviceDraft({
      payload: matching,
      baseUpdatedAt: CURRENT_NOTE_BASE,
      updatedAt: '2026-07-27T10:00:00.000Z',
    })
    draftApi.getWorkspaceDraft.mockResolvedValue(
      serverDraft(
        { body: 'Newer stale server draft' },
        '2026-07-27T12:00:00.000Z',
        '2026-07-27T09:00:00.000Z',
      ),
    )

    renderStrictBaseDraft()
    await flushHydration()

    expect(screen.getByTestId('draft-value')).toHaveTextContent(
      matching.body,
    )
    expect(screen.getByTestId('recovery-value')).toHaveTextContent(
      'Newer stale server draft',
    )
    expect(screen.getByTestId('draft-state')).toHaveTextContent(
      'conflict',
    )
  })

  it('does not let an unchanged matching device copy hide a stale server recovery', async () => {
    const stale = { body: 'Unsaved stale server draft' }
    storeDeviceDraft({
      payload: CURRENT_NOTE_VALUE,
      baseUpdatedAt: CURRENT_NOTE_BASE,
      updatedAt: '2026-07-27T12:00:00.000Z',
    })
    draftApi.getWorkspaceDraft.mockResolvedValue(
      serverDraft(
        stale,
        '2026-07-27T10:00:00.000Z',
        '2026-07-27T09:00:00.000Z',
      ),
    )

    renderStrictBaseDraft()
    await flushHydration()

    expect(screen.getByTestId('draft-value')).toHaveTextContent(
      CURRENT_NOTE_VALUE.body,
    )
    expect(screen.getByTestId('recovery-value')).toHaveTextContent(
      stale.body,
    )
    expect(screen.getByTestId('draft-state')).toHaveTextContent(
      'conflict',
    )
    expect(draftApi.deleteWorkspaceDraft).not.toHaveBeenCalled()
  })

  it('does not let an unchanged matching server copy hide a stale device recovery', async () => {
    const stale = { body: 'Unsaved stale device draft' }
    storeDeviceDraft({
      payload: stale,
      baseUpdatedAt: '2026-07-27T09:00:00.000Z',
      updatedAt: '2026-07-27T10:00:00.000Z',
    })
    draftApi.getWorkspaceDraft.mockResolvedValue(
      serverDraft(
        CURRENT_NOTE_VALUE,
        '2026-07-27T12:00:00.000Z',
        CURRENT_NOTE_BASE,
      ),
    )

    renderStrictBaseDraft()
    await flushHydration()

    expect(screen.getByTestId('draft-value')).toHaveTextContent(
      CURRENT_NOTE_VALUE.body,
    )
    expect(screen.getByTestId('recovery-value')).toHaveTextContent(
      stale.body,
    )
    expect(screen.getByTestId('draft-state')).toHaveTextContent(
      'conflict',
    )
    expect(
      JSON.parse(
        window.localStorage.getItem(STORAGE_KEY) ?? 'null',
      ).payload,
    ).toEqual(stale)
  })

  it('keeps a stale-only recovery separate until it is explicitly restored', async () => {
    const stale = { body: 'Older note revision draft' }
    storeDeviceDraft({
      payload: stale,
      baseUpdatedAt: '2026-07-27T09:00:00.000Z',
      updatedAt: '2026-07-27T12:00:00.000Z',
    })
    draftApi.getWorkspaceDraft.mockResolvedValue(null)

    renderStrictBaseDraft()
    await flushHydration()

    expect(screen.getByTestId('draft-value')).toHaveTextContent(
      CURRENT_NOTE_VALUE.body,
    )
    expect(screen.getByTestId('recovery-value')).toHaveTextContent(
      stale.body,
    )
    expect(screen.getByTestId('draft-state')).toHaveTextContent(
      'conflict',
    )
    expect(
      JSON.parse(
        window.localStorage.getItem(STORAGE_KEY) ?? 'null',
      ).payload,
    ).toEqual(stale)

    fireEvent.click(
      screen.getByRole('button', { name: 'Restore recovery' }),
    )

    expect(screen.getByTestId('draft-value')).toHaveTextContent(
      stale.body,
    )
    expect(screen.getByTestId('recovery-value')).toBeEmptyDOMElement()
  })

  it('discards a stale-only recovery only after explicit confirmation', async () => {
    storeDeviceDraft({
      payload: { body: 'Older note revision draft' },
      baseUpdatedAt: '2026-07-27T09:00:00.000Z',
      updatedAt: '2026-07-27T12:00:00.000Z',
    })
    draftApi.getWorkspaceDraft.mockResolvedValue(null)

    renderStrictBaseDraft()
    await flushHydration()

    fireEvent.click(
      screen.getByRole('button', { name: 'Discard recovery' }),
    )
    await flushHydration()

    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull()
    expect(draftApi.deleteWorkspaceDraft).not.toHaveBeenCalled()
    expect(screen.getByTestId('draft-value')).toHaveTextContent(
      CURRENT_NOTE_VALUE.body,
    )
    expect(screen.getByTestId('recovery-value')).toBeEmptyDOMElement()
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
