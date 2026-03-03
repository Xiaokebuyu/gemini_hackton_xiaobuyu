import { create } from 'zustand'
import type { LocationOverview, PresentNpc, GameMode } from '../types/game'
import type { SceneChangeData } from '../types/sse'

interface SceneState {
  backgroundKey: string          // 资源 key，格式 "area_id" 或 "area_id/location_id"
  currentArea: string
  currentLocation: string | null
  presentNpcs: PresentNpc[]
  gameMode: GameMode
  isTransitioning: boolean       // 转场动画中

  updateFromOverview: (overview: LocationOverview) => void
  transitionTo: (data: SceneChangeData) => void
  setGameMode: (mode: GameMode) => void
  setTransitioning: (v: boolean) => void
}

export const useSceneStore = create<SceneState>((set) => ({
  backgroundKey: '',
  currentArea: '',
  currentLocation: null,
  presentNpcs: [],
  gameMode: 'explore',
  isTransitioning: false,

  updateFromOverview: (overview) => {
    const key = overview.location_id
      ? `${overview.area_id}/${overview.location_id}`
      : overview.area_id
    set({
      currentArea: overview.area_id,
      currentLocation: overview.location_id,
      presentNpcs: overview.present_npcs,
      backgroundKey: key,
    })
  },

  transitionTo: (data) => {
    set({
      isTransitioning: true,
      currentLocation: data.location_id,
      // backgroundKey 和 currentArea 在 updateFromOverview 里由 location_overview 更新
    })
  },

  setGameMode: (mode) => set({ gameMode: mode }),
  setTransitioning: (v) => set({ isTransitioning: v }),
}))
