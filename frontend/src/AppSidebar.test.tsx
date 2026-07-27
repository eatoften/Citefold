import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { AppSidebar } from './AppSidebar'

describe('AppSidebar', () => {
  it('exposes only the three primary product destinations', () => {
    render(
      <AppSidebar
        activeView="sources"
        getViewHref={(view) => `?view=${view}`}
        onChange={vi.fn()}
      />,
    )

    const navigation = screen.getByRole('navigation', {
      name: 'Primary navigation',
    })
    expect(navigation).toHaveTextContent('Sources')
    expect(navigation).toHaveTextContent('Chat')
    expect(navigation).toHaveTextContent('Studio')
    expect(navigation).not.toHaveTextContent('Workspace')
    expect(screen.getByRole('link', { name: 'Sources' })).toHaveAttribute(
      'aria-current',
      'page',
    )
  })

  it('uses client navigation for an ordinary click', async () => {
    const onChange = vi.fn()
    render(
      <AppSidebar
        activeView="sources"
        getViewHref={(view) => `/app?view=${view}&course=course-a`}
        onChange={onChange}
      />,
    )

    await userEvent.click(screen.getByRole('link', { name: 'Chat' }))

    expect(onChange).toHaveBeenCalledWith('chat')
    expect(screen.getByRole('link', { name: 'Chat' })).toHaveAttribute(
      'href',
      '/app?view=chat&course=course-a',
    )
  })
})
