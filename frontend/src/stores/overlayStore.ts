import { create } from 'zustand'

export type OverlayType =
  | 'none'
  | 'log'
  | 'menu'
  | 'character'
  | 'inventory'
  | 'map'
  | 'quests'
  | 'shop'
  | 'item_detail'
  | 'board'
  | 'chat_invite'
  | 'party'

interface OverlayState {
  current: OverlayType
  data: unknown                  // 覆盖层所需数据（各层自行类型断言）

  open: (type: OverlayType, data?: unknown) => void
  close: () => void
}

export const useOverlayStore = create<OverlayState>((set) => ({
  current: 'none',
  data: null,

  open: (type, data = null) => set({ current: type, data }),
  close: () => set({ current: 'none', data: null }),
}))
