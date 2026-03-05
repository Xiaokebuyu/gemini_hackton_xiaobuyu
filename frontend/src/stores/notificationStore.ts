import { create } from 'zustand'

let _nextId = 0

export interface NotificationItem {
  id: string
  content: string
  type: 'success' | 'error' | 'info'
}

interface NotificationState {
  items: NotificationItem[]
  add: (content: string, type?: NotificationItem['type'], duration?: number) => void
  dismiss: (id: string) => void
}

export const useNotificationStore = create<NotificationState>((set, get) => ({
  items: [],

  add: (content, type = 'info', duration = 3000) => {
    const id = `notif-${++_nextId}`
    set((s) => ({ items: [...s.items, { id, content, type }] }))
    if (duration > 0) {
      setTimeout(() => get().dismiss(id), duration)
    }
  },

  dismiss: (id) => set((s) => ({ items: s.items.filter((n) => n.id !== id) })),
}))
