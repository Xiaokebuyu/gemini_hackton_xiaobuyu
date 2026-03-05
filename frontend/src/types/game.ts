// 游戏状态类型，对齐后端 location_overview 结构

export interface PresentNpc {
  character_id: string
  name: string
  role: 'main' | 'secondary' | 'passerby'
  disposition_hint: 'friendly' | 'neutral' | 'wary' | 'hostile'
  has_shop: boolean
  relationship_stage: string | null
  tags: string[]
}

export interface SubLocation {
  id: string
  name: string
  type: string
  available: boolean
  hostile?: boolean
  threat_level?: 'easy' | 'moderate' | 'hard' | 'deadly'
  blocking?: boolean
  temporary?: boolean
  source?: string
}

export interface Interactable {
  id: string
  name: string
  description_hint: string
  requires_check: boolean
  container_status: string | null
  trapped_hint: boolean
}

export interface Exit {
  target_area_id: string
  name: string
  travel_slots: number
  blocked: boolean
}

export interface LocationOverview {
  area_id: string
  location_id: string | null
  present_npcs: PresentNpc[]
  sub_locations: SubLocation[]
  interactables: Interactable[]
  exits: Exit[]
}

export type GameMode = 'explore' | 'dialogue' | 'private_chat' | 'combat' | 'encounter'

export type MessageType =
  | 'gm'
  | 'gm_comment'
  | 'npc'
  | 'emote'
  | 'teammate'
  | 'system'
  | 'player'
  | 'stream'

export interface DialogueEntry {
  id: string
  type: MessageType
  speaker?: string        // character_id
  speakerName?: string    // 显示名称
  content: string
  timestamp: number
}

export interface GameOption {
  id: string
  label: string
  icon?: string
  action: () => void
  disabled?: boolean
}

export interface PortraitSlot {
  position: 'left' | 'center' | 'right'
  characterId: string
  isActive: boolean
}
