import { create } from 'zustand'
import type { LocationOverview, PresentNpc, GameMode, PortraitSlot } from '../types/game'
import type { SceneChangeData } from '../types/sse'

const POSITION_MAP: Array<'left' | 'center' | 'right'> = ['center', 'left', 'right']
const POSITIONS_2: Array<'left' | 'right'> = ['left', 'right']

function buildPortraits(npcs: PresentNpc[]): PortraitSlot[] {
  // Companion 排序靠前
  const sorted = [...npcs].sort((a, b) =>
    (a.is_companion ? 0 : 1) - (b.is_companion ? 0 : 1)
  )
  const capped = sorted.slice(0, 3)
  if (capped.length === 0) return []
  if (capped.length === 1) {
    return [{ position: 'center', characterId: capped[0].character_id, isActive: false, isCompanion: capped[0].is_companion ?? false }]
  }
  if (capped.length === 2) {
    return POSITIONS_2.map((pos, i) => ({
      position: pos,
      characterId: capped[i].character_id,
      isActive: false,
      isCompanion: capped[i].is_companion ?? false,
    }))
  }
  return capped.map((npc, i) => ({
    position: POSITION_MAP[i],
    characterId: npc.character_id,
    isActive: false,
    isCompanion: npc.is_companion ?? false,
  }))
}

interface SceneState {
  backgroundKey: string          // 资源 key，格式 "area_id" 或 "area_id/location_id" 或 "area_id/location_id/room_id"
  dynamicBackgroundUrl: string | null  // data URL for dynamic sub-location backgrounds (from scene_change SSE)
  currentArea: string
  currentLocation: string | null
  currentRoom: string | null     // 当前所在 room（三层嵌套）
  presentNpcs: PresentNpc[]
  gameMode: GameMode
  openingInProgress: boolean
  isTransitioning: boolean       // 转场动画中
  portraits: PortraitSlot[]      // Layer 1 立绘槽位
  activePortraitId: string | null
  locationName: string           // 转场时显示的地点名
  transitionKey: number          // 每次 transitionTo() 自增，触发动画
  lastOverview: LocationOverview | null  // 最近一次 overview 快照（用于退出对话重建选项）
  activeNpcId: string | null             // 当前对话 NPC ID（对话上下文指示器）
  vnSpeakerId: string | null             // VN 模式当前发言角色 ID（用于立绘高亮）

  updateFromOverview: (overview: LocationOverview) => void
  transitionTo: (data: SceneChangeData) => void
  setGameMode: (mode: GameMode) => void
  setOpeningInProgress: (v: boolean) => void
  setTransitioning: (v: boolean) => void
  setActivePortrait: (characterId: string | null) => void
  addOpeningPortrait: (characterId: string, position: PortraitSlot['position']) => void
  clearPortraits: () => void
  setActiveNpc: (npcId: string | null) => void
  setVnSpeaker: (id: string | null) => void
}

export const useSceneStore = create<SceneState>((set) => ({
  backgroundKey: '',
  dynamicBackgroundUrl: null,
  currentArea: '',
  currentLocation: null,
  currentRoom: null,
  presentNpcs: [],
  gameMode: 'explore',
  openingInProgress: false,
  isTransitioning: false,
  portraits: [],
  activePortraitId: null,
  locationName: '',
  transitionKey: 0,
  lastOverview: null,
  activeNpcId: null,
  vnSpeakerId: null,

  updateFromOverview: (overview) => {
    const currentRoom = overview.current_room ?? null
    const key = overview.location_id
      ? currentRoom
        ? `${overview.area_id}/${overview.location_id}/${currentRoom}`
        : `${overview.area_id}/${overview.location_id}`
      : overview.area_id
    set((s) => ({
      currentArea: overview.area_id,
      currentLocation: overview.location_id,
      currentRoom,
      presentNpcs: overview.present_npcs,
      backgroundKey: key,
      // Keep dynamicBackgroundUrl if it was set by transitionTo for this location;
      // clear only if we're moving to a non-dynamic location (no dynamic url expected)
      dynamicBackgroundUrl: s.dynamicBackgroundUrl,
      portraits: s.openingInProgress ? s.portraits : buildPortraits(overview.present_npcs),
      activePortraitId: s.openingInProgress ? s.activePortraitId : null,
      lastOverview: overview,
    }))
  },

  transitionTo: (data) =>
    set((s) => ({
      isTransitioning: true,
      currentLocation: data.location_id,
      locationName: data.location_name,
      transitionKey: s.transitionKey + 1,
      // dynamicBackgroundUrl: use SSE-supplied data URL if present, else clear
      dynamicBackgroundUrl: data.background_url ?? null,
      // backgroundKey 和 currentArea 在 updateFromOverview 里由 location_overview 更新
    })),

  setGameMode: (mode) => set({ gameMode: mode }),
  setOpeningInProgress: (v) => set({ openingInProgress: v }),
  setTransitioning: (v) => set({ isTransitioning: v }),

  setActivePortrait: (characterId) =>
    set((s) => ({
      activePortraitId: characterId,
      portraits: s.portraits.map((p) => ({
        ...p,
        isActive: p.characterId === characterId,
      })),
    })),

  addOpeningPortrait: (characterId, position) =>
    set((s) => {
      const next = s.portraits.filter((p) => p.characterId !== characterId && p.position !== position)
      next.push({ position, characterId, isActive: false, isCompanion: false })
      return { portraits: next }
    }),

  clearPortraits: () => set({ portraits: [], activePortraitId: null }),
  setActiveNpc: (npcId) => set({ activeNpcId: npcId }),
  setVnSpeaker: (id) => set({ vnSpeakerId: id }),
}))
