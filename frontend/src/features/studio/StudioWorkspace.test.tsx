import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { StudioTool } from '../navigation/appRoute'
import { StudioWorkspace } from './StudioWorkspace'

const COURSES = [
  { id: 'course-a', title: 'Course A' },
  { id: 'course-b', title: 'Course B' },
]

function toolHref(tool: StudioTool): string {
  return `/?view=studio&tool=${tool}`
}

describe('StudioWorkspace', () => {
  it('renders ordinary links for every Studio tool', () => {
    const { container } = render(
      <StudioWorkspace
        activeTool="study"
        courses={COURSES}
        selectedCourseId="course-a"
        getToolHref={toolHref}
        onSelectCourse={vi.fn()}
      >
        <div>Study outlet</div>
      </StudioWorkspace>,
    )

    const navigation = screen.getByRole('navigation', {
      name: 'Studio tools',
    })
    const expectedLinks = [
      ['Cards', 'cards'],
      ['Study', 'study'],
      ['Review', 'review'],
      ['Course map', 'map'],
      ['Explore', 'explore'],
    ] as const

    for (const [label, tool] of expectedLinks) {
      expect(
        screen.getByRole('link', { name: label }),
      ).toHaveAttribute('href', toolHref(tool))
    }

    expect(navigation.querySelectorAll('[aria-current="page"]')).toHaveLength(
      1,
    )
    expect(screen.getByRole('link', { name: 'Study' })).toHaveAttribute(
      'aria-current',
      'page',
    )
    expect(
      screen.getByText(
        'Turn course evidence into editable, source-backed study documents.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('Study outlet')).toBeInTheDocument()
    expect(container.querySelector('main')).not.toBeInTheDocument()
  })

  it('reports course and unmodified tool navigation choices', async () => {
    const user = userEvent.setup()
    const onSelectCourse = vi.fn()
    const onSelectTool = vi.fn()

    render(
      <StudioWorkspace
        activeTool="cards"
        courses={COURSES}
        selectedCourseId="course-a"
        getToolHref={toolHref}
        onSelectCourse={onSelectCourse}
        onSelectTool={onSelectTool}
      >
        <div>Cards outlet</div>
      </StudioWorkspace>,
    )

    await user.selectOptions(
      screen.getByLabelText('Course'),
      'course-b',
    )
    await user.click(screen.getByRole('link', { name: 'Review' }))

    expect(onSelectCourse).toHaveBeenCalledWith('course-b')
    expect(onSelectTool).toHaveBeenCalledWith('review')
  })

})
