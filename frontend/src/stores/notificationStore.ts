import { create } from 'zustand'

let _nextId = 0

export type NotificationCategory =
  | 'milestone'
  | 'relationship'
  | 'item'
  | 'quest'
  | 'companion'
  | 'default'

export interface NotificationItem {
  id: string
  content: string
  type: 'success' | 'error' | 'info'
  category?: NotificationCategory
}

interface NotificationState {
  items: NotificationItem[]
  add: (content: string, type?: NotificationItem['type'], duration?: number, category?: NotificationCategory) => void
  dismiss: (id: string) => void
}

export const useNotificationStore = create<NotificationState>((set, get) => ({
  items: [],

  add: (content, type = 'info', duration = 3000, category) => {
    const id = `notif-${++_nextId}`
    set((s) => ({
      items: [...s.items.slice(-3), { id, content, type, category }],
    }))
    if (duration > 0) {
      setTimeout(() => get().dismiss(id), duration)
    }
  },

  dismiss: (id) => set((s) => ({ items: s.items.filter((n) => n.id !== id) })),
}))
