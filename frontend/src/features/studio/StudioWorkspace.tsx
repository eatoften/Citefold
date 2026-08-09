import {
  useId,
  type MouseEvent,
  type ReactNode,
} from 'react'
import {
  STUDIO_TOOLS,
  type StudioTool,
} from '../navigation/appRoute'
import './StudioWorkspace.css'

type StudioCourseOption = {
  id: string
  title: string
}

type StudioToolDetails = {
  label: string
  description: string
}

const STUDIO_TOOL_DETAILS: Record<StudioTool, StudioToolDetails> = {
  notes: {
    label: 'Notes',
    description:
      'Capture ideas and grounded answers, then publish exact revisions as sources.',
  },
  cards: {
    label: 'Cards',
    description:
      'Create, edit, and organize grounded knowledge cards.',
  },
  study: {
    label: 'Study',
    description:
      'Turn course evidence into editable, source-backed study documents.',
  },
  review: {
    label: 'Review',
    description:
      'Practice due prompts with spaced repetition and grounded evidence.',
  },
  map: {
    label: 'Course map',
    description:
      'Organize course concepts into topics and a learning sequence.',
  },
  explore: {
    label: 'Explore',
    description:
      'Trace published Concepts and verify every relationship against its Source.',
  },
}

export type StudioWorkspaceProps = {
  activeTool: StudioTool
  courses: StudioCourseOption[]
  selectedCourseId: string | null
  getToolHref: (tool: StudioTool) => string
  onSelectCourse: (courseId: string) => void
  onSelectTool?: (tool: StudioTool) => void
  children: ReactNode
}

function shouldHandleToolNavigation(
  event: MouseEvent<HTMLAnchorElement>,
): boolean {
  return (
    event.button === 0 &&
    !event.metaKey &&
    !event.ctrlKey &&
    !event.shiftKey &&
    !event.altKey
  )
}

export function StudioWorkspace({
  activeTool,
  courses,
  selectedCourseId,
  getToolHref,
  onSelectCourse,
  onSelectTool,
  children,
}: StudioWorkspaceProps) {
  const courseSelectId = useId()
  const activeToolDetails = STUDIO_TOOL_DETAILS[activeTool]

  return (
    <div
      className="studio-workspace"
      data-studio-tool={activeTool}
    >
      <header className="studio-workspace-header">
        <div className="studio-workspace-heading">
          <p className="studio-workspace-kicker">Studio</p>
          <h1>{activeToolDetails.label}</h1>
          <p className="studio-workspace-description">
            {activeToolDetails.description}
          </p>
        </div>

        <label
          className="studio-workspace-course"
          htmlFor={courseSelectId}
        >
          <span>Course</span>
          <select
            id={courseSelectId}
            value={selectedCourseId ?? ''}
            disabled={courses.length === 0}
            onChange={(event) => {
              if (event.target.value) {
                onSelectCourse(event.target.value)
              }
            }}
          >
            <option value="" disabled>
              {courses.length ? 'Select a course' : 'No courses available'}
            </option>
            {courses.map((course) => (
              <option key={course.id} value={course.id}>
                {course.title}
              </option>
            ))}
          </select>
        </label>
      </header>

      <nav
        className="studio-workspace-nav"
        aria-label="Studio tools"
      >
        {STUDIO_TOOLS.map((tool) => {
          const details = STUDIO_TOOL_DETAILS[tool]
          const isActive = activeTool === tool

          return (
            <a
              key={tool}
              href={getToolHref(tool)}
              className={isActive ? 'active' : undefined}
              aria-current={isActive ? 'page' : undefined}
              onClick={(event) => {
                if (
                  !onSelectTool ||
                  !shouldHandleToolNavigation(event)
                ) {
                  return
                }
                event.preventDefault()
                onSelectTool(tool)
              }}
            >
              {details.label}
            </a>
          )
        })}
      </nav>

      <div className="studio-workspace-outlet">{children}</div>
    </div>
  )
}
