import { AnimatePresence, motion } from 'framer-motion'
import { useNotificationStore } from '../stores/notificationStore'
import type { NotificationItem, NotificationCategory } from '../stores/notificationStore'

const TYPE_STYLE: Record<NotificationItem['type'], string> = {
  success: 'bg-stone-900/90 border-emerald-600/50 text-emerald-200',
  error: 'bg-stone-900/90 border-red-600/50 text-red-200',
  info: 'bg-stone-900/90 border-stone-600/50 text-stone-200',
}

const CATEGORY_STYLE: Record<NotificationCategory, string> = {
  milestone: 'bg-amber-950/90 border-amber-500/50 text-amber-200 shadow-[0_0_12px_rgba(245,158,11,0.2)]',
  relationship: 'bg-purple-950/90 border-purple-500/40 text-purple-200',
  item: 'bg-stone-900/90 border-stone-600/50 text-stone-200',
  quest: 'bg-amber-900/90 border-amber-600/40 text-amber-100',
  companion: 'bg-emerald-950/90 border-emerald-500/40 text-emerald-200',
  default: 'bg-stone-900/90 border-stone-600/50 text-stone-200',
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
            className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm shadow-lg pointer-events-auto border hover:opacity-80 transition-opacity ${getStyle(item)}`}
          >
            <span className="font-bold flex-shrink-0">{getIcon(item)}</span>
            <span>{item.content}</span>
          </motion.button>
        ))}
      </AnimatePresence>
    </div>
  )
}
