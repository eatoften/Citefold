import {
  Database,
  MessageSquareText,
  Shapes,
} from 'lucide-react'
import type { MouseEvent } from 'react'
import type { PrimaryView } from './features/navigation/appRoute'

type AppSidebarProps = {
  activeView: PrimaryView
  getViewHref: (view: PrimaryView) => string
  onChange: (view: PrimaryView) => void
}

const NAV_ITEMS: Array<{
  id: PrimaryView
  label: string
  icon: typeof Database
}> = [
  { id: 'sources', label: 'Sources', icon: Database },
  { id: 'chat', label: 'Chat', icon: MessageSquareText },
  { id: 'studio', label: 'Studio', icon: Shapes },
]

function shouldHandleNavigation(
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

export function AppSidebar({
  activeView,
  getViewHref,
  onChange,
}: AppSidebarProps) {
  return (
    <nav className="app-sidebar" aria-label="Primary navigation">
      <div
        className="app-sidebar-brand"
        aria-label="Video Course Cards"
        title="Video Course Cards"
      >
        VC
      </div>
      <div className="app-sidebar-nav">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon
          const isActive = activeView === item.id

          return (
            <a
              key={item.id}
              href={getViewHref(item.id)}
              className={isActive ? 'active' : undefined}
              aria-current={isActive ? 'page' : undefined}
              title={item.label}
              onClick={(event) => {
                if (!shouldHandleNavigation(event)) return
                event.preventDefault()
                onChange(item.id)
              }}
            >
              <Icon aria-hidden="true" size={20} strokeWidth={1.8} />
              <span>{item.label}</span>
            </a>
          )
        })}
      </div>
    </nav>
  )
}
