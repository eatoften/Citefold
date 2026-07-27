import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChatPanelProps } from './ChatPanel'
import { ChatWorkspace } from './ChatWorkspace'

const { chatPanelPropsMock } = vi.hoisted(() => ({
  chatPanelPropsMock: vi.fn(),
}))

vi.mock('./ChatPanel', () => ({
  ChatPanel: (props: ChatPanelProps) => {
    chatPanelPropsMock(props)
    return (
      <section
        aria-label="Mock course chat"
        data-compact={String(props.compact)}
        data-course-id={props.courseId ?? ''}
      >
        {props.courseTitle ?? 'No selected course'}
      </section>
    )
  },
}))

const COURSES = [
  { id: 'course-a', title: 'Course A' },
  { id: 'course-b', title: 'Course B' },
]

describe('ChatWorkspace', () => {
  beforeEach(() => {
    chatPanelPropsMock.mockClear()
  })

  it('wraps the full ChatPanel without creating a main landmark', () => {
    const onOpenCitation = vi.fn()
    const { container } = render(
      <ChatWorkspace
        apiBaseUrl="http://127.0.0.1:8001"
        courses={COURSES}
        selectedCourseId="course-b"
        selectedModel="local-model"
        onSelectCourse={vi.fn()}
        onOpenCitation={onOpenCitation}
      />,
    )

    expect(
      screen.getByRole('heading', { level: 1, name: 'Chat' }),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Mock course chat')).toHaveAttribute(
      'data-compact',
      'false',
    )
    expect(screen.getByLabelText('Mock course chat')).toHaveAttribute(
      'data-course-id',
      'course-b',
    )
    expect(screen.getByLabelText('Mock course chat')).toHaveTextContent(
      'Course B',
    )
    expect(container.querySelector('main')).not.toBeInTheDocument()

    expect(chatPanelPropsMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        apiBaseUrl: 'http://127.0.0.1:8001',
        courseId: 'course-b',
        courseTitle: 'Course B',
        model: 'local-model',
        compact: false,
        onOpenCitation,
      }),
    )
  })

  it('reports course changes through the shared selector', async () => {
    const user = userEvent.setup()
    const onSelectCourse = vi.fn()

    render(
      <ChatWorkspace
        apiBaseUrl="http://127.0.0.1:8001"
        courses={COURSES}
        selectedCourseId="course-a"
        onSelectCourse={onSelectCourse}
      />,
    )

    await user.selectOptions(
      screen.getByLabelText('Course'),
      'course-b',
    )

    expect(onSelectCourse).toHaveBeenCalledWith('course-b')
  })

  it('keeps the course selector named when no course exists', () => {
    render(
      <ChatWorkspace
        apiBaseUrl="http://127.0.0.1:8001"
        courses={[]}
        selectedCourseId={null}
        onSelectCourse={vi.fn()}
      />,
    )

    expect(screen.getByLabelText('Course')).toBeDisabled()
    expect(screen.getByText('No courses available')).toBeInTheDocument()
  })
})
