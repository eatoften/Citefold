import { useId, useMemo } from 'react'
import './CardsWorkspace.css'

export type CardContentStatus =
  | 'draft'
  | 'reviewed'
  | 'needs_fix'

export type CardNotesFilter =
  | 'all'
  | 'with_notes'
  | 'without_notes'

export type CardIndexItem = {
  id: string
  title: string
  summary: string
  card_kind: string
  tags: string[]
  content_status: CardContentStatus
  source_video: string | null
  source_start_seconds: number
  note_count: number
  review_item_count: number
  learning_document_count: number
}

export type CardsWorkspaceProps = {
  courseTitle?: string | null
  cards: CardIndexItem[]
  loading: boolean
  searchValue: string
  statusFilter: 'all' | CardContentStatus
  notesFilter: CardNotesFilter
  tagFilter: string
  onSearchChange: (value: string) => void
  onStatusFilterChange: (
    value: 'all' | CardContentStatus,
  ) => void
  onNotesFilterChange: (value: CardNotesFilter) => void
  onTagFilterChange: (value: string) => void
  onRefresh: () => void
  onOpenCard: (cardId: string) => void
  onGoToSources: () => void
}

const STATUS_LABELS: Record<CardContentStatus, string> = {
  draft: 'Draft',
  reviewed: 'Reviewed',
  needs_fix: 'Needs fix',
}

function formatCardKind(cardKind: string): string {
  return cardKind
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function formatSourceTime(totalSeconds: number): string {
  const safeSeconds = Math.max(0, Math.floor(totalSeconds))
  const hours = Math.floor(safeSeconds / 3600)
  const minutes = Math.floor((safeSeconds % 3600) / 60)
  const seconds = safeSeconds % 60

  if (hours > 0) {
    return [hours, minutes, seconds]
      .map((part) => String(part).padStart(2, '0'))
      .join(':')
  }

  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

function matchesSearch(
  card: CardIndexItem,
  normalizedSearch: string,
): boolean {
  if (!normalizedSearch) {
    return true
  }

  return [
    card.title,
    card.summary,
    card.card_kind,
    card.source_video ?? '',
    ...card.tags,
  ].some((value) =>
    value.toLocaleLowerCase().includes(normalizedSearch),
  )
}

export function CardsWorkspace({
  courseTitle,
  cards,
  loading,
  searchValue,
  statusFilter,
  notesFilter,
  tagFilter,
  onSearchChange,
  onStatusFilterChange,
  onNotesFilterChange,
  onTagFilterChange,
  onRefresh,
  onOpenCard,
  onGoToSources,
}: CardsWorkspaceProps) {
  const headingId = useId()
  const searchId = useId()
  const statusId = useId()
  const notesId = useId()
  const tagId = useId()

  const availableTags = useMemo(
    () =>
      Array.from(
        new Set(cards.flatMap((card) => card.tags)),
      ).sort((left, right) =>
        left.localeCompare(right, undefined, {
          sensitivity: 'base',
        }),
      ),
    [cards],
  )

  const filteredCards = useMemo(() => {
    const normalizedSearch = searchValue
      .trim()
      .toLocaleLowerCase()
    const normalizedTag = tagFilter.toLocaleLowerCase()

    return cards.filter((card) => {
      const matchesStatus =
        statusFilter === 'all' ||
        card.content_status === statusFilter
      const matchesNotes =
        notesFilter === 'all' ||
        (notesFilter === 'with_notes'
          ? card.note_count > 0
          : card.note_count === 0)
      const matchesTag =
        !normalizedTag ||
        card.tags.some(
          (tag) => tag.toLocaleLowerCase() === normalizedTag,
        )

      return (
        matchesStatus &&
        matchesNotes &&
        matchesTag &&
        matchesSearch(card, normalizedSearch)
      )
    })
  }, [
    cards,
    notesFilter,
    searchValue,
    statusFilter,
    tagFilter,
  ])

  const statistics = useMemo(
    () => [
      {
        label: 'Cards',
        value: cards.length,
      },
      {
        label: 'Reviewed',
        value: cards.filter(
          (card) => card.content_status === 'reviewed',
        ).length,
      },
      {
        label: 'Notes',
        value: cards.reduce(
          (total, card) => total + card.note_count,
          0,
        ),
      },
      {
        label: 'Review prompts',
        value: cards.reduce(
          (total, card) => total + card.review_item_count,
          0,
        ),
      },
      {
        label: 'Study documents',
        value: cards.reduce(
          (total, card) =>
            total + card.learning_document_count,
          0,
        ),
      },
    ],
    [cards],
  )

  const hasActiveFilters =
    searchValue.trim().length > 0 ||
    statusFilter !== 'all' ||
    notesFilter !== 'all' ||
    tagFilter !== ''

  function clearFilters() {
    onSearchChange('')
    onStatusFilterChange('all')
    onNotesFilterChange('all')
    onTagFilterChange('')
  }

  return (
    <section
      className="cards-workspace"
      aria-labelledby={headingId}
    >
      <header className="cards-workspace-header">
        <div>
          <p className="cards-workspace-kicker">
            Card library
          </p>
          <h2 id={headingId}>
            {courseTitle
              ? `${courseTitle} cards`
              : 'Course cards'}
          </h2>
          <p className="cards-workspace-intro">
            Review grounded course concepts and turn them into
            notes, practice prompts, and study documents.
          </p>
        </div>

        <div className="cards-workspace-actions">
          <button
            type="button"
            className="cards-workspace-button secondary"
            onClick={onRefresh}
            disabled={loading}
          >
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
          <button
            type="button"
            className="cards-workspace-button primary"
            onClick={onGoToSources}
          >
            Go to Sources
          </button>
        </div>
      </header>

      <dl
        className="cards-workspace-statistics"
        role="group"
        aria-label="Card library summary"
      >
        {statistics.map((statistic) => (
          <div key={statistic.label}>
            <dt>{statistic.label}</dt>
            <dd>{statistic.value}</dd>
          </div>
        ))}
      </dl>

      <div
        className="cards-workspace-filters"
        aria-label="Filter cards"
      >
        <label
          className="cards-workspace-search"
          htmlFor={searchId}
        >
          <span>Search cards</span>
          <input
            id={searchId}
            type="search"
            value={searchValue}
            placeholder="Title, summary, tag, or source"
            onChange={(event) =>
              onSearchChange(event.target.value)
            }
          />
        </label>

        <label htmlFor={statusId}>
          <span>Status</span>
          <select
            id={statusId}
            value={statusFilter}
            onChange={(event) =>
              onStatusFilterChange(
                event.target.value as
                  | 'all'
                  | CardContentStatus,
              )
            }
          >
            <option value="all">All statuses</option>
            <option value="reviewed">Reviewed</option>
            <option value="draft">Draft</option>
            <option value="needs_fix">Needs fix</option>
          </select>
        </label>

        <label htmlFor={notesId}>
          <span>Notes</span>
          <select
            id={notesId}
            value={notesFilter}
            onChange={(event) =>
              onNotesFilterChange(
                event.target.value as CardNotesFilter,
              )
            }
          >
            <option value="all">All cards</option>
            <option value="with_notes">With notes</option>
            <option value="without_notes">
              Without notes
            </option>
          </select>
        </label>

        <label htmlFor={tagId}>
          <span>Tag</span>
          <select
            id={tagId}
            value={tagFilter}
            onChange={(event) =>
              onTagFilterChange(event.target.value)
            }
          >
            <option value="">All tags</option>
            {availableTags.map((tag) => (
              <option key={tag} value={tag}>
                {tag}
              </option>
            ))}
          </select>
        </label>
      </div>

      {!loading && cards.length > 0 && (
        <div className="cards-workspace-results-heading">
          <p aria-live="polite">
            Showing {filteredCards.length} of {cards.length}{' '}
            cards
          </p>
          {hasActiveFilters && (
            <button type="button" onClick={clearFilters}>
              Clear filters
            </button>
          )}
        </div>
      )}

      {loading ? (
        <div
          className="cards-workspace-state"
          role="status"
          aria-live="polite"
        >
          <span
            className="cards-workspace-loader"
            aria-hidden="true"
          />
          <h3>Loading course cards</h3>
          <p>
            Gathering card details, notes, and learning
            activity.
          </p>
        </div>
      ) : cards.length === 0 ? (
        <div className="cards-workspace-state">
          <span
            className="cards-workspace-state-mark"
            aria-hidden="true"
          >
            +
          </span>
          <h3>No cards yet</h3>
          <p>
            Open Sources to add course material and create the
            first grounded card.
          </p>
        </div>
      ) : filteredCards.length === 0 ? (
        <div className="cards-workspace-state" role="status">
          <span
            className="cards-workspace-state-mark"
            aria-hidden="true"
          >
            0
          </span>
          <h3>No cards match these filters</h3>
          <p>
            Clear the filters or try a broader search term.
          </p>
        </div>
      ) : (
        <div
          className="cards-workspace-grid"
          aria-label="Course cards"
        >
          {filteredCards.map((card) => (
            <article
              key={card.id}
              className="cards-workspace-card"
              data-content-status={card.content_status}
            >
              <button
                type="button"
                aria-label={`Open ${card.title}`}
                onClick={() => onOpenCard(card.id)}
              >
                <span className="cards-workspace-card-topline">
                  <span className="cards-workspace-card-kind">
                    {formatCardKind(card.card_kind)}
                  </span>
                  <span className="cards-workspace-card-status">
                    {STATUS_LABELS[card.content_status]}
                  </span>
                </span>

                <span className="cards-workspace-card-title">
                  {card.title}
                </span>
                <span className="cards-workspace-card-summary">
                  {card.summary || 'No summary yet.'}
                </span>

                {card.tags.length > 0 && (
                  <span
                    className="cards-workspace-card-tags"
                    aria-label="Tags"
                  >
                    {card.tags.slice(0, 3).map((tag, index) => (
                      <span key={`${tag}-${index}`}>{tag}</span>
                    ))}
                    {card.tags.length > 3 && (
                      <span>+{card.tags.length - 3}</span>
                    )}
                  </span>
                )}

                <span className="cards-workspace-card-source">
                  {card.source_video ? (
                    <>
                      <span>{card.source_video}</span>
                      <span aria-label="Source start time">
                        {formatSourceTime(
                          card.source_start_seconds,
                        )}
                      </span>
                    </>
                  ) : (
                    <span>Source details unavailable</span>
                  )}
                </span>

                <span className="cards-workspace-card-activity">
                  <span>{card.note_count} notes</span>
                  <span>{card.review_item_count} prompts</span>
                  <span>
                    {card.learning_document_count} documents
                  </span>
                </span>
              </button>
            </article>
          ))}
        </div>
      )}
    </section>
  )
}
