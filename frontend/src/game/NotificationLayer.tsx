import { AnimatePresence, motion } from 'framer-motion'
import { useNotificationStore } from '../stores/notificationStore'
import type { NotificationItem, NotificationCategory } from '../stores/notificationStore'

const TYPE_STYLE: Record<NotificationItem['type'], string> = {
  success: 'panel-fantasy border-l-4 border-l-emerald-500 text-parchment-200 shadow-fantasy',
  error: 'panel-fantasy border-l-4 border-l-red-500 text-parchment-200 shadow-fantasy',
  info: 'panel-fantasy border-l-4 border-l-parchment-400 text-parchment-200 shadow-fantasy',
}

const CATEGORY_STYLE: Record<NotificationCategory, string> = {
  milestone: 'panel-fantasy border-l-4 border-l-gold-400 text-parchment-200 shadow-fantasy',
  relationship: 'panel-fantasy border-l-4 border-l-purple-400 text-parchment-200 shadow-fantasy',
  item: 'panel-fantasy border-l-4 border-l-parchment-400 text-parchment-200 shadow-fantasy',
  quest: 'panel-fantasy border-l-4 border-l-amber-400 text-parchment-200 shadow-fantasy',
  companion: 'panel-fantasy border-l-4 border-l-emerald-400 text-parchment-200 shadow-fantasy',
  default: 'panel-fantasy border-l-4 border-l-parchment-400 text-parchment-200 shadow-fantasy',
}

const CATEGORY_ICON: Record<NotificationCategory, string> = {
  milestone: '★',
  relationship: '♥',
  item: '◆',
  quest: '📜',
  companion: '⚔',
  default: 'ℹ',
}

const TYPE_ICON: Record<NotificationItem['type'], string> = {
  success: '✓',
  error: '✗',
  info: 'ℹ',
}

function getStyle(item: NotificationItem): string {
  if (item.category && item.category !== 'default') {
    return CATEGORY_STYLE[item.category]
  }
  return TYPE_STYLE[item.type]
}

function getIcon(item: NotificationItem): string {
  if (item.category && item.category !== 'default') {
    return CATEGORY_ICON[item.category]
  }
  return TYPE_ICON[item.type]
}

export default function NotificationLayer() {
  const { items, dismiss } = useNotificationStore()

  return (
    <div className="fixed top-24 right-4 z-[15] flex flex-col gap-2 pointer-events-none">
      <AnimatePresence initial={false}>
        {items.slice(0, 4).map((item) => (
          <motion.button
            key={item.id}
            layout
            initial={{ opacity: 0, x: 100 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 100 }}
            transition={{ duration: 0.3, ease: 'easeOut' }}
            onClick={() => dismiss(item.id)}
            className={`flex items-center gap-2 px-3 py-2 text-sm pointer-events-auto hover:opacity-80 transition-opacity ${getStyle(item)}`}
          >
            <span className="font-bold flex-shrink-0">{getIcon(item)}</span>
            <span>{item.content}</span>
          </motion.button>
        ))}
      </AnimatePresence>
    </div>
  )
}
