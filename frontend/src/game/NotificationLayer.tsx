import { useNotificationStore } from '../stores/notificationStore'
import type { NotificationItem } from '../stores/notificationStore'

const TYPE_STYLE: Record<NotificationItem['type'], string> = {
  success: 'bg-emerald-900/90 border border-emerald-600/50 text-emerald-200',
  error: 'bg-red-900/90 border border-red-600/50 text-red-200',
  info: 'bg-gray-800/90 border border-gray-600/50 text-gray-200',
}

const TYPE_ICON: Record<NotificationItem['type'], string> = {
  success: '✓',
  error: '✗',
  info: 'ℹ',
}

export default function NotificationLayer() {
  const { items, dismiss } = useNotificationStore()

  if (items.length === 0) return null

  return (
    <div className="fixed top-24 right-4 z-[15] flex flex-col gap-2 pointer-events-none">
      {items.map((item) => (
        <button
          key={item.id}
          onClick={() => dismiss(item.id)}
          className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm shadow-lg pointer-events-auto transition-opacity hover:opacity-80 ${TYPE_STYLE[item.type]}`}
        >
          <span className="font-bold flex-shrink-0">{TYPE_ICON[item.type]}</span>
          <span>{item.content}</span>
        </button>
      ))}
    </div>
  )
}
