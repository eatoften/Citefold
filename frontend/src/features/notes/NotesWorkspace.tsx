import {
  AlertCircle,
  ArrowUpRight,
  BookOpenText,
  Check,
  FilePlus2,
  FileText,
  History,
  LoaderCircle,
  MessageSquareText,
  NotebookPen,
  RefreshCw,
  Save,
  SendToBack,
  Trash2,
} from 'lucide-react'
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import type { ChatCitation } from '../chat'
import { formatSourceLocator } from '../citations/citationFormat'
import {
  announceTrashCreated,
  SaveStatus,
  useAutosavedDraft,
  useInternalNavigationGuard,
} from '../reliability'
import {
  createNotebookNote,
  deleteNotebookNote,
  getNotebookNote,
  listNotebookNotes,
  NotebookNoteApiError,
  publishNotebookNoteAsSource,
  updateNotebookNote,
} from './noteApi'
import type {
  ChatAnswerNoteOriginSnapshot,
  NotebookNote,
  NotebookNoteCitation,
  NotebookNoteSummary,
} from './noteTypes'
import './NotesWorkspace.css'

export type NotesWorkspaceProps = {
  apiBaseUrl: string
  courseId: string | null
  initialNoteId?: string | null
  onNoteRouteChange?: (
    noteId: string | null,
    mode: 'push' | 'replace',
  ) => void
  onOpenCitation?: (
    citation: ChatCitation,
    trigger: HTMLButtonElement,
  ) => void
  onGoToSources?: (sourceId: string) => void
}

type NoteEditorProps = {
  apiBaseUrl: string
  courseId: string
  note: NotebookNote | null
  onSaved: (note: NotebookNote) => void
  onDeleted: (noteId: string) => void
  onCancelNew: () => void
  onOpenCitation?: NotesWorkspaceProps['onOpenCitation']
  onGoToSources?: NotesWorkspaceProps['onGoToSources']
}

type Scope = {
  courseId: string
  noteId: string | null
  revision: number | null
  epoch: number
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function readableDate(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    year:
      parsed.getFullYear() === new Date().getFullYear()
        ? undefined
        : 'numeric',
  }).format(parsed)
}

function noteExcerpt(note: NotebookNoteSummary): string {
  const normalized = note.body_preview
    .replace(/[#>*_`[\]-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  return normalized || 'Empty note'
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message
    ? error.message
    : fallback
}

function normalizedNoteTitle(value: string): string {
  return value.trim().replace(/\s+/g, ' ')
}

function upsertNote(
  notes: NotebookNoteSummary[],
  note: NotebookNote,
): NotebookNoteSummary[] {
  const summary: NotebookNoteSummary = {
    id: note.id,
    course_id: note.course_id,
    title: note.title,
    body_preview: note.body_markdown.slice(0, 320),
    revision: note.revision,
    origin_type: note.origin_type,
    citation_count:
      note.origin_snapshot.origin_type === 'chat_answer'
        ? note.origin_snapshot.citations.length
        : 0,
    published_snapshot_id: note.published_snapshot_id,
    published_revision: note.published_revision,
    is_source_outdated: note.is_source_outdated,
    created_at: note.created_at,
    updated_at: note.updated_at,
  }
  return [
    summary,
    ...notes.filter((candidate) => candidate.id !== note.id),
  ].sort((left, right) =>
    right.updated_at.localeCompare(left.updated_at),
  )
}

function citationForInspector(
  citation: NotebookNoteCitation,
  origin: ChatAnswerNoteOriginSnapshot,
  note: NotebookNote,
): ChatCitation {
  const firstSpan = citation.spans[0]
  return {
    id: citation.id,
    message_id: origin.message_id,
    ordinal: citation.ordinal,
    sentence_index: firstSpan?.sentence_index ?? 0,
    start_offset: firstSpan?.start_offset ?? 0,
    end_offset: firstSpan?.end_offset ?? 0,
    source_id: citation.source_id,
    chunk_id: citation.chunk_id,
    chunk_text_hash: citation.chunk_text_hash,
    source_title: citation.source_title,
    source_type: citation.source_type,
    quote: citation.quote,
    score: citation.score,
    locator: citation.locator,
    created_at: note.created_at,
  }
}

function NoteProvenance({
  note,
  onOpenCitation,
}: {
  note: NotebookNote
  onOpenCitation?: NotesWorkspaceProps['onOpenCitation']
}) {
  if (
    note.origin_type !== 'chat_answer' ||
    note.origin_snapshot.origin_type !== 'chat_answer'
  ) {
    return (
      <aside className="notes-provenance notes-provenance-free">
        <div className="notes-provenance-heading">
          <NotebookPen aria-hidden="true" size={17} />
          <div>
            <h3>Your note</h3>
            <p>This note began as your own writing.</p>
          </div>
        </div>
      </aside>
    )
  }

  const origin = note.origin_snapshot
  return (
    <aside
      className="notes-provenance"
      aria-labelledby="note-provenance-title"
    >
      <div className="notes-provenance-heading">
        <History aria-hidden="true" size={17} />
        <div>
          <h3 id="note-provenance-title">Original Chat answer</h3>
          <p>
            Kept read-only so later edits never rewrite what the assistant
            actually answered or cited.
          </p>
        </div>
      </div>

      <dl className="notes-provenance-meta">
        {origin.provider && (
          <>
            <dt>Provider</dt>
            <dd>{origin.provider}</dd>
          </>
        )}
        {origin.model && (
          <>
            <dt>Model</dt>
            <dd>{origin.model}</dd>
          </>
        )}
        <dt>Citations</dt>
        <dd>{origin.citations.length}</dd>
      </dl>

      <details>
        <summary>View original answer</summary>
        <p className="notes-origin-answer">{origin.answer_text}</p>
      </details>

      {origin.citations.length > 0 && (
        <div
          className="notes-origin-citations"
          aria-label="Original answer citations"
        >
          {origin.citations.map((citation) => {
            const inspectorCitation = citationForInspector(
              citation,
              origin,
              note,
            )
            return (
              <button
                type="button"
                key={citation.id}
                disabled={!onOpenCitation}
                aria-label={`Open source ${citation.ordinal}: ${citation.source_title}, ${formatSourceLocator(citation.locator)}`}
                onClick={(event) =>
                  onOpenCitation?.(
                    inspectorCitation,
                    event.currentTarget,
                  )
                }
              >
                <span>[{citation.ordinal}]</span>
                <span>
                  <strong>{citation.source_title}</strong>
                  <small>
                    {formatSourceLocator(citation.locator)}
                  </small>
                </span>
                <ArrowUpRight aria-hidden="true" size={14} />
              </button>
            )
          })}
        </div>
      )}
    </aside>
  )
}

function NoteEditor({
  apiBaseUrl,
  courseId,
  note,
  onSaved,
  onDeleted,
  onCancelNew,
  onOpenCitation,
  onGoToSources,
}: NoteEditorProps) {
  const initialValue = useMemo(
    () => ({
      title: note?.title ?? '',
      body_markdown: note?.body_markdown ?? '',
    }),
    [note?.body_markdown, note?.title],
  )
  const [title, setTitle] = useState(initialValue.title)
  const [bodyMarkdown, setBodyMarkdown] = useState(
    initialValue.body_markdown,
  )
  const [isSaving, setIsSaving] = useState(false)
  const [isPublishing, setIsPublishing] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)
  const [feedback, setFeedback] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [conflict, setConflict] = useState<NotebookNote | null>(null)
  const epochRef = useRef(0)
  const requestRef = useRef<AbortController | null>(null)
  const scopeRef = useRef<Scope>({
    courseId,
    noteId: note?.id ?? null,
    revision: note?.revision ?? null,
    epoch: 0,
  })

  const restoreDraft = useCallback(
    (payload: { title: string; body_markdown: string }) => {
      setTitle(payload.title)
      setBodyMarkdown(payload.body_markdown)
    },
    [],
  )
  const draft = useAutosavedDraft({
    apiBaseUrl,
    draftId: note
      ? `notebook-note:${note.id}`
      : `notebook-note:new:${courseId}`,
    courseId,
    draftType: 'notebook_note',
    entityId: note?.id ?? null,
    baseUpdatedAt: note?.updated_at ?? null,
    enabled: true,
    value: { title, body_markdown: bodyMarkdown },
    initialValue,
    onRestore: restoreDraft,
    requireMatchingBaseUpdatedAt: true,
  })

  const isNew = note === null
  const normalizedTitle = normalizedNoteTitle(title)
  const isDirty =
    (isNew
      ? normalizedTitle.length > 0 ||
        bodyMarkdown !== initialValue.body_markdown
      : normalizedTitle !== note.title ||
        bodyMarkdown !== note.body_markdown)
  const isBusy = isSaving || isPublishing || isDeleting
  const isRecoveryBlocked = draft.recoveryConflict !== null
  const hasValidTitle = isNew || normalizedTitle.length > 0
  const canSave =
    bodyMarkdown.trim().length > 0 &&
    hasValidTitle &&
    isDirty &&
    !isBusy &&
    !isRecoveryBlocked
  const isPublishedCurrent =
    note !== null &&
    note.published_revision === note.revision &&
    !note.is_source_outdated

  useEffect(() => {
    epochRef.current += 1
    requestRef.current?.abort()
    scopeRef.current = {
      courseId,
      noteId: note?.id ?? null,
      revision: note?.revision ?? null,
      epoch: epochRef.current,
    }
    setError(null)
    setFeedback(null)
    setConflict(null)
    return () => requestRef.current?.abort()
  }, [courseId, note?.id, note?.revision])

  function startRequest(): {
    controller: AbortController
    scope: Scope
  } {
    requestRef.current?.abort()
    const controller = new AbortController()
    requestRef.current = controller
    const scope = {
      courseId,
      noteId: note?.id ?? null,
      revision: note?.revision ?? null,
      epoch: ++epochRef.current,
    }
    scopeRef.current = scope
    return { controller, scope }
  }

  function isCurrent(
    controller: AbortController,
    scope: Scope,
  ): boolean {
    const current = scopeRef.current
    return (
      !controller.signal.aborted &&
      requestRef.current === controller &&
      current.epoch === scope.epoch &&
      current.courseId === scope.courseId &&
      current.noteId === scope.noteId &&
      current.revision === scope.revision
    )
  }

  function finishRequest(controller: AbortController): void {
    if (requestRef.current === controller) {
      requestRef.current = null
    }
  }

  function handleConflict(requestError: unknown): boolean {
    if (
      requestError instanceof NotebookNoteApiError &&
      requestError.status === 409 &&
      requestError.current
    ) {
      setConflict(requestError.current)
      setError(
        'This note changed after you opened it. Your draft is still safe.',
      )
      return true
    }
    return false
  }

  async function saveNote(): Promise<void> {
    const body = bodyMarkdown.trim()
    if (!body || !canSave || isRecoveryBlocked) return
    const { controller, scope } = startRequest()
    setIsSaving(true)
    setError(null)
    setFeedback(null)
    setConflict(null)
    try {
      const saved = note
        ? await updateNotebookNote(
            apiBaseUrl,
            courseId,
            note.id,
            {
              title: normalizedTitle,
              body_markdown: bodyMarkdown,
              expected_revision: note.revision,
            },
            controller.signal,
          )
        : await createNotebookNote(
            apiBaseUrl,
            courseId,
            {
              title: normalizedTitle || undefined,
              body_markdown: bodyMarkdown,
            },
            controller.signal,
          )
      if (!isCurrent(controller, scope)) return
      if (
        saved.course_id !== courseId ||
        (note
          ? saved.id !== note.id ||
            saved.revision !== note.revision + 1
          : saved.revision < 1)
      ) {
        throw new Error(
          'The server returned a note outside the active editor scope.',
        )
      }
      await draft.clearDraft()
      if (!isCurrent(controller, scope)) return
      setFeedback(note ? 'Changes saved.' : 'Note created.')
      onSaved(saved)
    } catch (requestError: unknown) {
      if (
        isAbortError(requestError) ||
        !isCurrent(controller, scope)
      ) {
        return
      }
      if (!handleConflict(requestError)) {
        setError(errorMessage(requestError, 'Could not save this note.'))
      }
    } finally {
      if (isCurrent(controller, scope)) setIsSaving(false)
      finishRequest(controller)
    }
  }

  async function publishNote(): Promise<void> {
    if (!note || isDirty || isBusy || isRecoveryBlocked) return
    const { controller, scope } = startRequest()
    setIsPublishing(true)
    setError(null)
    setFeedback(null)
    setConflict(null)
    try {
      const promotion = await publishNotebookNoteAsSource(
        apiBaseUrl,
        courseId,
        note.id,
        note.revision,
        controller.signal,
      )
      if (!isCurrent(controller, scope)) return
      if (
        promotion.note.id !== note.id ||
        promotion.note.course_id !== courseId ||
        promotion.note.revision !== note.revision ||
        promotion.snapshot.note_id !== note.id ||
        promotion.snapshot.course_id !== courseId ||
        promotion.snapshot.note_revision !== note.revision ||
        promotion.source.id !== `note:${note.id}` ||
        promotion.source.course_id !== courseId ||
        promotion.source.origin_type !== 'notebook_note' ||
        promotion.source.origin_id !== note.id
      ) {
        throw new Error(
          'The server returned a publication outside the active note scope.',
        )
      }
      setFeedback(
        promotion.replayed
          ? `Revision ${promotion.snapshot.note_revision} is already available in Sources.`
          : `Published revision ${promotion.snapshot.note_revision} to Sources.`,
      )
      onSaved(promotion.note)
    } catch (requestError: unknown) {
      if (
        isAbortError(requestError) ||
        !isCurrent(controller, scope)
      ) {
        return
      }
      if (!handleConflict(requestError)) {
        setError(
          errorMessage(
            requestError,
            'Could not publish this note to Sources.',
          ),
        )
      }
    } finally {
      if (isCurrent(controller, scope)) setIsPublishing(false)
      finishRequest(controller)
    }
  }

  async function deleteNote(): Promise<void> {
    if (!note || isBusy || isRecoveryBlocked) return
    if (
      !window.confirm(
        `Move "${note.title || 'Untitled note'}" to Trash?`,
      )
    ) {
      return
    }
    const { controller, scope } = startRequest()
    setIsDeleting(true)
    setError(null)
    setFeedback(null)
    setConflict(null)
    try {
      await deleteNotebookNote(
        apiBaseUrl,
        courseId,
        note.id,
        note.revision,
        controller.signal,
      )
      if (!isCurrent(controller, scope)) return
      await draft.clearDraft()
      if (!isCurrent(controller, scope)) return
      announceTrashCreated({
        entity_type: 'notebook_note',
        entity_id: note.id,
      })
      onDeleted(note.id)
    } catch (requestError: unknown) {
      if (
        isAbortError(requestError) ||
        !isCurrent(controller, scope)
      ) {
        return
      }
      if (!handleConflict(requestError)) {
        setError(
          errorMessage(
            requestError,
            'Could not move this note to Trash.',
          ),
        )
      }
    } finally {
      if (isCurrent(controller, scope)) setIsDeleting(false)
      finishRequest(controller)
    }
  }

  async function loadConflictVersion(): Promise<void> {
    if (!conflict) return
    const savedConflict = conflict
    setIsSaving(true)
    await draft.clearDraft()
    setIsSaving(false)
    onSaved(savedConflict)
  }

  return (
    <div className="notes-editor-layout">
      <section
        className="notes-editor"
        aria-label={
          isNew
            ? 'New note editor'
            : `${note.title || 'Untitled note'} editor`
        }
      >
        <div className="notes-editor-toolbar">
          <div>
            <span>{isNew ? 'New note' : `Revision ${note.revision}`}</span>
            {!isNew && (
              <small>
                Updated {readableDate(note.updated_at)}
              </small>
            )}
          </div>
          <SaveStatus state={draft.state} message={draft.message} />
        </div>

        {draft.restored && (
          <div className="notes-recovered" role="status">
            <Check aria-hidden="true" size={15} />
            Recovered your newest unsaved draft.
          </div>
        )}
        {draft.recoveryConflict && (
          <div className="notes-draft-conflict" role="alert">
            <AlertCircle aria-hidden="true" size={16} />
            <div>
              <strong>Older draft kept separate</strong>
              <span>
                This draft was based on another saved revision. Review it
                explicitly so it cannot overwrite newer work by accident.
              </span>
            </div>
            <button
              type="button"
              disabled={isBusy}
              onClick={draft.restoreRecoveryDraft}
            >
              Restore older draft
            </button>
            <button
              type="button"
              disabled={isBusy}
              onClick={() => void draft.discardRecoveryDraft()}
            >
              Discard
            </button>
          </div>
        )}

        <label className="notes-title-field">
          <span>Title</span>
          <input
            id="notes-editor-title"
            value={title}
            maxLength={200}
            disabled={isBusy || isRecoveryBlocked}
            placeholder="Untitled note"
            aria-invalid={!hasValidTitle}
            onChange={(event) => setTitle(event.target.value)}
          />
          {!hasValidTitle && (
            <small className="notes-field-error">
              Saved notes need a title.
            </small>
          )}
        </label>
        <label className="notes-body-field">
          <span>Note</span>
          <textarea
            value={bodyMarkdown}
            maxLength={1_000_000}
            disabled={isBusy || isRecoveryBlocked}
            placeholder="Write an idea, paste an excerpt, or organize what you learned..."
            onChange={(event) => setBodyMarkdown(event.target.value)}
          />
        </label>

        <div className="notes-editor-status">
          <span>
            {bodyMarkdown.length.toLocaleString()} character
            {bodyMarkdown.length === 1 ? '' : 's'}
          </span>
          {isDirty && <span>Unsaved domain changes</span>}
        </div>

        {error && (
          <div className="notes-inline-error" role="alert">
            <AlertCircle aria-hidden="true" size={16} />
            <span>{error}</span>
            {conflict && (
              <button
                type="button"
                onClick={() => void loadConflictVersion()}
              >
                Load saved revision
              </button>
            )}
          </div>
        )}
        {feedback && (
          <div className="notes-inline-success" role="status">
            <Check aria-hidden="true" size={16} />
            <span>{feedback}</span>
          </div>
        )}

        <footer className="notes-editor-actions">
          <div>
            <button
              type="button"
              className="notes-primary-action"
              disabled={!canSave}
              onClick={() => void saveNote()}
            >
              {isSaving ? (
                <LoaderCircle
                  aria-hidden="true"
                  className="notes-spin"
                  size={16}
                />
              ) : (
                <Save aria-hidden="true" size={16} />
              )}
              {isNew ? 'Create note' : 'Save changes'}
            </button>
            {isNew && (
              <button
                type="button"
                disabled={isBusy || isRecoveryBlocked}
                onClick={onCancelNew}
              >
                Cancel
              </button>
            )}
          </div>

          {!isNew && (
            <div>
              <button
                type="button"
                className="notes-publish-action"
                disabled={
                  isBusy ||
                  isDirty ||
                  isPublishedCurrent ||
                  isRecoveryBlocked
                }
                title={
                  isRecoveryBlocked
                    ? 'Choose whether to restore or discard the older draft first.'
                    : isDirty
                    ? 'Save your changes before publishing this revision.'
                    : isPublishedCurrent
                      ? 'This exact revision is already available in Sources.'
                    : 'Snapshot this exact revision as retrievable evidence.'
                }
                onClick={() => void publishNote()}
              >
                {isPublishing ? (
                  <LoaderCircle
                    aria-hidden="true"
                    className="notes-spin"
                    size={16}
                  />
                ) : (
                  <SendToBack aria-hidden="true" size={16} />
                )}
                {note.published_revision === null
                  ? 'Publish as source'
                  : note.is_source_outdated
                    ? 'Update source'
                    : 'Published'}
              </button>
              {note.published_revision !== null && onGoToSources && (
                <button
                  type="button"
                  onClick={() => onGoToSources(`note:${note.id}`)}
                >
                  Open Sources
                  <ArrowUpRight aria-hidden="true" size={14} />
                </button>
              )}
              <button
                type="button"
                className="notes-delete-action"
                disabled={isBusy || isRecoveryBlocked}
                onClick={() => void deleteNote()}
              >
                {isDeleting ? (
                  <LoaderCircle
                    aria-hidden="true"
                    className="notes-spin"
                    size={16}
                  />
                ) : (
                  <Trash2 aria-hidden="true" size={16} />
                )}
                Move to Trash
              </button>
            </div>
          )}
        </footer>
      </section>

      {note && (
        <NoteProvenance
          note={note}
          onOpenCitation={onOpenCitation}
        />
      )}
    </div>
  )
}

export function NotesWorkspace({
  apiBaseUrl,
  courseId,
  initialNoteId = null,
  onNoteRouteChange,
  onOpenCitation,
  onGoToSources,
}: NotesWorkspaceProps) {
  const canLeaveCurrentDraft = useInternalNavigationGuard()
  const [notes, setNotes] = useState<NotebookNoteSummary[]>([])
  const [notesCourseId, setNotesCourseId] = useState<string | null>(null)
  const [selectedNote, setSelectedNote] =
    useState<NotebookNote | null>(null)
  const [isCreating, setIsCreating] = useState(false)
  const [isLoadingList, setIsLoadingList] = useState(false)
  const [isLoadingNote, setIsLoadingNote] = useState(false)
  const [listError, setListError] = useState<string | null>(null)
  const [noteError, setNoteError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [mutationListReloadKey, setMutationListReloadKey] =
    useState(0)
  const scopeEpochRef = useRef(0)
  const mutationSequenceRef = useRef(0)
  const listRequestRef = useRef<AbortController | null>(null)
  const noteRequestRef = useRef<AbortController | null>(null)
  const courseRef = useRef<string | null>(courseId)
  const onNoteRouteChangeRef = useRef(onNoteRouteChange)

  useEffect(() => {
    onNoteRouteChangeRef.current = onNoteRouteChange
  }, [onNoteRouteChange])

  useEffect(() => {
    scopeEpochRef.current += 1
    mutationSequenceRef.current += 1
    courseRef.current = courseId
    listRequestRef.current?.abort()
    noteRequestRef.current?.abort()
    setNotes([])
    setNotesCourseId(null)
    setSelectedNote(null)
    setIsCreating(false)
    setIsLoadingList(Boolean(courseId))
    setIsLoadingNote(false)
    setListError(null)
    setNoteError(null)
  }, [courseId])

  useEffect(() => {
    if (!courseId) return
    const controller = new AbortController()
    const epoch = scopeEpochRef.current
    const mutationSequence = mutationSequenceRef.current
    listRequestRef.current?.abort()
    listRequestRef.current = controller
    setIsLoadingList(true)
    setListError(null)
    void listNotebookNotes(apiBaseUrl, courseId, controller.signal)
      .then((nextNotes) => {
        if (
          controller.signal.aborted ||
          epoch !== scopeEpochRef.current ||
          mutationSequence !== mutationSequenceRef.current ||
          courseRef.current !== courseId
        ) {
          return
        }
        if (
          nextNotes.some((note) => note.course_id !== courseId)
        ) {
          throw new Error(
            'The server returned notes outside the active course scope.',
          )
        }
        setNotes(nextNotes)
        setNotesCourseId(courseId)
      })
      .catch((requestError: unknown) => {
        if (
          isAbortError(requestError) ||
          controller.signal.aborted ||
          epoch !== scopeEpochRef.current ||
          mutationSequence !== mutationSequenceRef.current ||
          courseRef.current !== courseId
        ) {
          return
        }
        setListError(
          errorMessage(requestError, 'Could not load notes.'),
        )
      })
      .finally(() => {
        if (
          !controller.signal.aborted &&
          epoch === scopeEpochRef.current &&
          mutationSequence === mutationSequenceRef.current &&
          courseRef.current === courseId
        ) {
          setIsLoadingList(false)
        }
      })
    return () => controller.abort()
  }, [apiBaseUrl, courseId, mutationListReloadKey, reloadKey])

  useEffect(() => {
    if (!courseId || !initialNoteId) {
      if (!isCreating) {
        setSelectedNote(null)
        setIsLoadingNote(false)
        setNoteError(null)
      }
      return
    }
    setIsCreating(false)
    const controller = new AbortController()
    const epoch = scopeEpochRef.current
    const mutationSequence = mutationSequenceRef.current
    const noteId = initialNoteId
    noteRequestRef.current?.abort()
    noteRequestRef.current = controller
    setSelectedNote((current) =>
      current?.id === noteId ? current : null,
    )
    setIsLoadingNote(true)
    setNoteError(null)
    void getNotebookNote(
      apiBaseUrl,
      courseId,
      noteId,
      controller.signal,
    )
      .then((nextNote) => {
        if (
          controller.signal.aborted ||
          epoch !== scopeEpochRef.current ||
          mutationSequence !== mutationSequenceRef.current ||
          courseRef.current !== courseId ||
          initialNoteId !== noteId
        ) {
          return
        }
        if (
          nextNote.course_id !== courseId ||
          nextNote.id !== noteId
        ) {
          throw new Error(
            'The server returned a note outside the active route scope.',
          )
        }
        setSelectedNote(nextNote)
        setNotes((current) => upsertNote(current, nextNote))
      })
      .catch((requestError: unknown) => {
        if (
          isAbortError(requestError) ||
          controller.signal.aborted ||
          epoch !== scopeEpochRef.current ||
          mutationSequence !== mutationSequenceRef.current ||
          courseRef.current !== courseId ||
          initialNoteId !== noteId
        ) {
          return
        }
        setSelectedNote(null)
        setNoteError(
          errorMessage(requestError, 'Could not open this note.'),
        )
        if (
          requestError instanceof NotebookNoteApiError &&
          requestError.status === 404
        ) {
          onNoteRouteChangeRef.current?.(null, 'replace')
        }
      })
      .finally(() => {
        if (
          !controller.signal.aborted &&
          epoch === scopeEpochRef.current &&
          mutationSequence === mutationSequenceRef.current &&
          courseRef.current === courseId &&
          initialNoteId === noteId
        ) {
          setIsLoadingNote(false)
        }
      })
    return () => controller.abort()
  }, [
    apiBaseUrl,
    courseId,
    initialNoteId,
    isCreating,
    reloadKey,
  ])

  function selectNote(noteId: string): void {
    if (noteId === initialNoteId && selectedNote?.id === noteId) {
      return
    }
    if (!canLeaveCurrentDraft()) return
    setIsCreating(false)
    setSelectedNote(null)
    setNoteError(null)
    onNoteRouteChange?.(noteId, 'push')
  }

  function startNewNote(): void {
    if (!canLeaveCurrentDraft()) return
    noteRequestRef.current?.abort()
    setIsCreating(true)
    setSelectedNote(null)
    setNoteError(null)
    onNoteRouteChange?.(null, initialNoteId ? 'push' : 'replace')
  }

  function handleSaved(saved: NotebookNote): void {
    if (
      courseRef.current !== saved.course_id ||
      saved.course_id !== courseId
    ) {
      return
    }
    mutationSequenceRef.current += 1
    listRequestRef.current?.abort()
    noteRequestRef.current?.abort()
    listRequestRef.current = null
    noteRequestRef.current = null
    setNotes((current) => upsertNote(current, saved))
    setNotesCourseId(courseId)
    setSelectedNote(saved)
    setIsCreating(false)
    setIsLoadingList(false)
    setIsLoadingNote(false)
    setListError(null)
    setNoteError(null)
    setMutationListReloadKey((value) => value + 1)
    if (initialNoteId !== saved.id) {
      onNoteRouteChange?.(saved.id, 'replace')
    }
  }

  function handleDeleted(noteId: string): void {
    if (selectedNote?.id !== noteId) return
    mutationSequenceRef.current += 1
    listRequestRef.current?.abort()
    noteRequestRef.current?.abort()
    listRequestRef.current = null
    noteRequestRef.current = null
    setNotes((current) =>
      current.filter((candidate) => candidate.id !== noteId),
    )
    setNotesCourseId(courseId)
    setSelectedNote(null)
    setIsCreating(false)
    setIsLoadingList(false)
    setIsLoadingNote(false)
    setListError(null)
    setNoteError(null)
    setMutationListReloadKey((value) => value + 1)
    onNoteRouteChange?.(null, 'replace')
  }

  if (!courseId) {
    return (
      <section className="notes-unavailable">
        <NotebookPen aria-hidden="true" size={36} />
        <h2>Select a course to start a notebook</h2>
        <p>Notes stay private to one course until you publish them.</p>
      </section>
    )
  }

  const listReady = notesCourseId === courseId
  const showEditor = isCreating || selectedNote !== null

  return (
    <section
      className="notes-workspace"
      aria-label="Course notes"
    >
      <aside className="notes-list-panel">
        <div className="notes-list-heading">
          <div>
            <span>Notebook</span>
            <strong>{listReady ? notes.length : '...'}</strong>
          </div>
          <div>
            <button
              type="button"
              aria-label="Refresh notes"
              title="Refresh notes"
              disabled={isLoadingList}
              onClick={() => setReloadKey((value) => value + 1)}
            >
              <RefreshCw
                aria-hidden="true"
                className={isLoadingList ? 'notes-spin' : undefined}
                size={16}
              />
            </button>
            <button
              type="button"
              className="notes-new-button"
              onClick={startNewNote}
            >
              <FilePlus2 aria-hidden="true" size={16} />
              New note
            </button>
          </div>
        </div>

        {listError && (
          <div className="notes-list-error" role="alert">
            <AlertCircle aria-hidden="true" size={16} />
            <span>{listError}</span>
            <button
              type="button"
              onClick={() => setReloadKey((value) => value + 1)}
            >
              Retry
            </button>
          </div>
        )}

        <div className="notes-list">
          {isLoadingList && !listReady ? (
            <div className="notes-list-loading" role="status">
              <LoaderCircle
                aria-hidden="true"
                className="notes-spin"
                size={18}
              />
              Loading notes...
            </div>
          ) : notes.length > 0 ? (
            notes.map((note) => (
              <button
                type="button"
                key={note.id}
                className={
                  selectedNote?.id === note.id ? 'selected' : undefined
                }
                aria-current={
                  selectedNote?.id === note.id ? 'true' : undefined
                }
                onClick={() => selectNote(note.id)}
              >
                <span className="notes-list-item-heading">
                  {note.origin_type === 'chat_answer' ? (
                    <MessageSquareText aria-hidden="true" size={15} />
                  ) : (
                    <FileText aria-hidden="true" size={15} />
                  )}
                  <strong>{note.title || 'Untitled note'}</strong>
                </span>
                <span className="notes-list-excerpt">
                  {noteExcerpt(note)}
                </span>
                <small>
                  {readableDate(note.updated_at)}
                  {note.published_revision !== null
                    ? note.is_source_outdated
                      ? ' - Source update available'
                      : ' - In Sources'
                    : ''}
                </small>
              </button>
            ))
          ) : (
            <div className="notes-list-empty">
              <BookOpenText aria-hidden="true" size={25} />
              <strong>No notes yet</strong>
              <span>
                Capture an idea here or save a grounded Chat answer.
              </span>
              <button type="button" onClick={startNewNote}>
                Write your first note
              </button>
            </div>
          )}
        </div>
      </aside>

      <div className="notes-main">
        {noteError && (
          <div className="notes-open-error" role="alert">
            <AlertCircle aria-hidden="true" size={18} />
            <div>
              <strong>Note unavailable</strong>
              <span>{noteError}</span>
            </div>
            <button
              type="button"
              onClick={() => setReloadKey((value) => value + 1)}
            >
              Retry
            </button>
          </div>
        )}

        {isLoadingNote && !selectedNote ? (
          <div className="notes-main-loading" role="status">
            <LoaderCircle
              aria-hidden="true"
              className="notes-spin"
              size={22}
            />
            Opening note...
          </div>
        ) : showEditor ? (
          <NoteEditor
            key={
              selectedNote
                ? `${courseId}:${selectedNote.id}:${selectedNote.revision}:${selectedNote.updated_at}`
                : `${courseId}:new`
            }
            apiBaseUrl={apiBaseUrl}
            courseId={courseId}
            note={selectedNote}
            onSaved={handleSaved}
            onDeleted={handleDeleted}
            onCancelNew={() => {
              if (!canLeaveCurrentDraft()) return
              setIsCreating(false)
              setSelectedNote(null)
            }}
            onOpenCitation={onOpenCitation}
            onGoToSources={onGoToSources}
          />
        ) : (
          <div className="notes-main-empty">
            <NotebookPen aria-hidden="true" size={34} />
            <h2>Build your course notebook</h2>
            <p>
              Draft privately, revise safely, then publish an exact revision
              only when you want Chat to use it as evidence.
            </p>
            <button
              type="button"
              className="notes-primary-action"
              onClick={startNewNote}
            >
              <FilePlus2 aria-hidden="true" size={16} />
              New note
            </button>
          </div>
        )}
      </div>
    </section>
  )
}
