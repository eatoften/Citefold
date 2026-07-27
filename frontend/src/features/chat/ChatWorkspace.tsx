import { useId } from 'react'
import {
  ChatPanel,
  type ChatPanelProps,
} from './ChatPanel'
import './ChatWorkspace.css'

type ChatCourseOption = {
  id: string
  title: string
}

export type ChatWorkspaceProps = {
  apiBaseUrl: string
  courses: ChatCourseOption[]
  selectedCourseId: string | null
  selectedModel?: string | null
  initialConversationId?: string | null
  recommendedQuestions?: ChatPanelProps['recommendedQuestions']
  onSelectCourse: (courseId: string) => void
  onConversationChange?: ChatPanelProps['onConversationChange']
  onOpenCitation?: ChatPanelProps['onOpenCitation']
  onOpenNote?: ChatPanelProps['onOpenNote']
}

export function ChatWorkspace({
  apiBaseUrl,
  courses,
  selectedCourseId,
  selectedModel,
  initialConversationId,
  recommendedQuestions,
  onSelectCourse,
  onConversationChange,
  onOpenCitation,
  onOpenNote,
}: ChatWorkspaceProps) {
  const courseSelectId = useId()
  const selectedCourse =
    courses.find((course) => course.id === selectedCourseId) ?? null

  return (
    <div className="chat-workspace-page">
      <header className="chat-workspace-page-header">
        <div>
          <p className="chat-workspace-page-kicker">Grounded workspace</p>
          <h1>Chat</h1>
          <p>
            Ask across selected course sources and verify every answer
            against its citations.
          </p>
        </div>

        <label
          className="chat-workspace-page-course"
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

      <div className="chat-workspace-page-content">
        <ChatPanel
          apiBaseUrl={apiBaseUrl}
          courseId={selectedCourseId}
          courseTitle={selectedCourse?.title}
          model={selectedModel}
          compact={false}
          initialConversationId={initialConversationId}
          recommendedQuestions={recommendedQuestions}
          onConversationChange={onConversationChange}
          onOpenCitation={onOpenCitation}
          onOpenNote={onOpenNote}
        />
      </div>
    </div>
  )
}
