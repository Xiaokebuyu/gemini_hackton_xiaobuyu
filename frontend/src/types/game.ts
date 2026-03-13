// 游戏状态类型，对齐后端 location_overview 结构

export interface PresentNpc {
  character_id: string
  name: string
  role: 'main' | 'secondary' | 'passerby' | 'companion'
  is_companion?: boolean
  recruitable?: boolean
  disposition_hint: 'friendly' | 'neutral' | 'wary' | 'hostile'
  has_shop: boolean
  relationship_stage: string | null
  tags: string[]
  presence_source?: 'resident' | 'schedule' | 'planner' | 'companion' | 'event'
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

export interface Room {
  id: string
  name: string
  discoverable: boolean
  discovered: boolean
}

export interface PrimaryAction {
  action_type: string
  params?: Record<string, unknown>
}

export interface DonationTarget {
  targetKind: 'npc' | 'interactable'
  targetId: string
  sourceName: string
}

export interface Interactable {
  id: string
  name: string
  description_hint: string
  requires_check: boolean
  container_status: string | null
  trapped_hint: boolean
  tags?: string[]
  interaction_kind?: 'board' | 'donation' | 'container' | 'generic'
  primary_action?: PrimaryAction | null
}

export interface Exit {
  target_area_id: string
  name: string
  travel_slots: number
  blocked: boolean
}

export interface LocationOverview {
  area_id: string
  area_name?: string           // 区域显示名（后端补充）
  location_id: string | null
  location_name?: string       // 当前子地点显示名（后端补充）
  present_npcs: PresentNpc[]
  sub_locations: SubLocation[]
  interactables: Interactable[]
  exits: Exit[]
  rooms: Room[]
  current_room: string | null
}

export type GameMode = 'explore' | 'dialogue' | 'private_chat' | 'combat' | 'encounter'

export type MessageType =
  | 'gm'
  | 'gm_comment'
  | 'npc'
  | 'emote'
  | 'teammate'
  | 'teammate_emote'
  | 'system'
  | 'player'
  | 'stream'

export interface DialogueEntry {
  id: string
  type: MessageType
  speaker?: string        // character_id
  speakerName?: string    // 显示名称
  content: string
  tone?: string
  timestamp: number
}

export interface GameOption {
  id: string
  label: string
  icon?: string
  action: () => void
  disabled?: boolean
  category?: 'talk' | 'room' | 'location' | 'action' | 'gear' | 'leave'
}

export interface PortraitSlot {
  position: 'left' | 'center' | 'right'
  characterId: string
  isActive: boolean
  isCompanion?: boolean
}

// Visual novel mode message — separate from DialogueEntry to avoid polluting exploration log
export interface VnMessage {
  id: string
  type: 'npc' | 'gm' | 'gm_comment' | 'teammate' | 'player'
  speaker?: string        // character_id
  speakerName?: string    // display name
  content: string
  tone?: string
}
