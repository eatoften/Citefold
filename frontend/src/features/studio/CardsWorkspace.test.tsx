import {
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import {
  CardsWorkspace,
  type CardIndexItem,
  type CardsWorkspaceProps,
} from './CardsWorkspace'

const CARDS: CardIndexItem[] = [
  {
    id: 'gradient-descent',
    title: 'Gradient descent',
    summary: 'How iterative optimization updates model weights.',
    card_kind: 'core_concept',
    tags: ['Optimization', 'Foundations'],
    content_status: 'reviewed',
    source_video: 'Lecture 03.mp4',
    source_start_seconds: 754,
    note_count: 2,
    review_item_count: 3,
    learning_document_count: 1,
  },
  {
    id: 'learning-rate',
    title: 'Learning rate',
    summary: 'The step size used by an optimizer.',
    card_kind: 'definition',
    tags: ['Optimization'],
    content_status: 'draft',
    source_video: 'Lecture 03.mp4',
    source_start_seconds: 925,
    note_count: 0,
    review_item_count: 1,
    learning_document_count: 0,
  },
  {
    id: 'regularization',
    title: 'Regularization',
    summary: 'Constraints that reduce overfitting.',
    card_kind: 'technique',
    tags: ['Generalization'],
    content_status: 'needs_fix',
    source_video: null,
    source_start_seconds: 0,
    note_count: 1,
    review_item_count: 0,
    learning_document_count: 2,
  },
]

function createProps(
  overrides: Partial<CardsWorkspaceProps> = {},
): CardsWorkspaceProps {
  return {
    courseTitle: 'Machine Learning',
    cards: CARDS,
    loading: false,
    searchValue: '',
    statusFilter: 'all',
    notesFilter: 'all',
    tagFilter: '',
    onSearchChange: vi.fn(),
    onStatusFilterChange: vi.fn(),
    onNotesFilterChange: vi.fn(),
    onTagFilterChange: vi.fn(),
    onRefresh: vi.fn(),
    onOpenCard: vi.fn(),
    onGoToSources: vi.fn(),
    ...overrides,
  }
}

describe('CardsWorkspace', () => {
  it('renders course statistics and opens cards without owning navigation', async () => {
    const user = userEvent.setup()
    const onRefresh = vi.fn()
    const onOpenCard = vi.fn()
    const onGoToSources = vi.fn()
    const { container } = render(
      <CardsWorkspace
        {...createProps({
          onRefresh,
          onOpenCard,
          onGoToSources,
        })}
      />,
    )

    expect(
      screen.getByRole('heading', {
        name: 'Machine Learning cards',
      }),
    ).toBeInTheDocument()

    const summary = screen.getByRole('group', {
      name: 'Card library summary',
    })
    expect(
      within(summary).getByText('Cards').nextSibling,
    ).toHaveTextContent('3')
    expect(
      within(summary).getByText('Reviewed').nextSibling,
    ).toHaveTextContent('1')
    expect(
      within(summary).getByText('Notes').nextSibling,
    ).toHaveTextContent('3')
    expect(
      within(summary).getByText('Review prompts').nextSibling,
    ).toHaveTextContent('4')
    expect(
      within(summary).getByText('Study documents').nextSibling,
    ).toHaveTextContent('3')

    await user.click(
      screen.getByRole('button', {
        name: 'Open Gradient descent',
      }),
    )
    await user.click(
      screen.getByRole('button', { name: 'Refresh' }),
    )
    await user.click(
      screen.getByRole('button', { name: 'Go to Sources' }),
    )

    expect(onOpenCard).toHaveBeenCalledWith('gradient-descent')
    expect(onRefresh).toHaveBeenCalledOnce()
    expect(onGoToSources).toHaveBeenCalledOnce()
    expect(container.querySelector('main')).not.toBeInTheDocument()
    expect(container.querySelector('a')).not.toBeInTheDocument()
  })

  it('reports controlled search and filter changes', () => {
    const onSearchChange = vi.fn()
    const onStatusFilterChange = vi.fn()
    const onNotesFilterChange = vi.fn()
    const onTagFilterChange = vi.fn()

    render(
      <CardsWorkspace
        {...createProps({
          onSearchChange,
          onStatusFilterChange,
          onNotesFilterChange,
          onTagFilterChange,
        })}
      />,
    )

    fireEvent.change(screen.getByLabelText('Search cards'), {
      target: { value: 'gradient' },
    })
    fireEvent.change(screen.getByLabelText('Status'), {
      target: { value: 'reviewed' },
    })
    fireEvent.change(screen.getByLabelText('Notes'), {
      target: { value: 'without_notes' },
    })
    fireEvent.change(screen.getByLabelText('Tag'), {
      target: { value: 'Optimization' },
    })

    expect(onSearchChange).toHaveBeenCalledWith('gradient')
    expect(onStatusFilterChange).toHaveBeenCalledWith('reviewed')
    expect(onNotesFilterChange).toHaveBeenCalledWith(
      'without_notes',
    )
    expect(onTagFilterChange).toHaveBeenCalledWith(
      'Optimization',
    )
  })

  it('filters the visible grid using every controlled value', () => {
    render(
      <CardsWorkspace
        {...createProps({
          searchValue: 'lecture 03',
          statusFilter: 'reviewed',
          notesFilter: 'with_notes',
          tagFilter: 'optimization',
        })}
      />,
    )

    expect(
      screen.getByRole('button', {
        name: 'Open Gradient descent',
      }),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('button', {
        name: 'Open Learning rate',
      }),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', {
        name: 'Open Regularization',
      }),
    ).not.toBeInTheDocument()
    expect(screen.getByText('Showing 1 of 3 cards')).toBeInTheDocument()
  })

  it('clears all controlled values after an empty filtered result', async () => {
    const user = userEvent.setup()
    const onSearchChange = vi.fn()
    const onStatusFilterChange = vi.fn()
    const onNotesFilterChange = vi.fn()
    const onTagFilterChange = vi.fn()

    render(
      <CardsWorkspace
        {...createProps({
          searchValue: 'missing topic',
          statusFilter: 'draft',
          notesFilter: 'without_notes',
          tagFilter: 'Optimization',
          onSearchChange,
          onStatusFilterChange,
          onNotesFilterChange,
          onTagFilterChange,
        })}
      />,
    )

    expect(
      screen.getByRole('heading', {
        name: 'No cards match these filters',
      }),
    ).toBeInTheDocument()

    await user.click(
      screen.getByRole('button', { name: 'Clear filters' }),
    )

    expect(onSearchChange).toHaveBeenCalledWith('')
    expect(onStatusFilterChange).toHaveBeenCalledWith('all')
    expect(onNotesFilterChange).toHaveBeenCalledWith('all')
    expect(onTagFilterChange).toHaveBeenCalledWith('')
  })

  it('distinguishes loading, empty-library, and no-match states', () => {
    const { rerender } = render(
      <CardsWorkspace {...createProps({ loading: true })} />,
    )

    expect(
      screen.getByRole('status'),
    ).toHaveTextContent('Loading course cards')
    expect(
      screen.getByRole('button', { name: 'Refreshing…' }),
    ).toBeDisabled()
    expect(
      screen.queryByText('No cards yet'),
    ).not.toBeInTheDocument()

    rerender(
      <CardsWorkspace
        {...createProps({ cards: [], loading: false })}
      />,
    )

    expect(
      screen.getByRole('heading', { name: 'No cards yet' }),
    ).toBeInTheDocument()
    expect(
      screen.queryByText('No cards match these filters'),
    ).not.toBeInTheDocument()

    rerender(
      <CardsWorkspace
        {...createProps({ searchValue: 'not present' })}
      />,
    )

    expect(screen.getByRole('status')).toHaveTextContent(
      'No cards match these filters',
    )
    expect(
      screen.queryByText('No cards yet'),
    ).not.toBeInTheDocument()
  })
})
