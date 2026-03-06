// API 请求/响应类型，对齐后端 Pydantic 模型

export interface WorldSummary {
  world_id: string
  name: string
  description: string
  cover_image: string
  player_count: number
}

export interface SessionSummaryBody {
  player_name: string
  player_class: string
  level: number
  location: string
  day: number | null
  play_time_hours: number | null
}

export interface SessionSummary {
  session_id: string
  created_at: number
  last_played: number
  phase: string
  summary: SessionSummaryBody
}

export interface SessionLifecycleResponse {
  world_id: string
  session_id: string
  phase: string
}

export interface CharacterOption {
  id: string
  name: string
  description: string
  [k: string]: unknown
}

export interface CharacterCreationOptions {
  races: CharacterOption[]
  classes: CharacterOption[]
  backgrounds: CharacterOption[]
}

export interface CharacterCreationRequest {
  name: string
  race: string
  character_class: string
  background: string
  ability_scores: Record<string, number>
  skill_proficiencies?: string[]
  backstory?: string
}

export interface InteractRequest {
  target_kind?: string
  target_id?: string
  npc_id?: string
  intent: string
  item_id?: string
  quest_id?: string
  count?: number
  message?: string
}

export interface NavigateRequest {
  action: string
  area_id?: string
  location_id?: string
}

export interface StructuredActionRequest {
  action_type: string
  params?: Record<string, unknown>
  context?: Record<string, unknown>
}

export interface PrivateChatRequest {
  npc_id: string
  message: string
}

export interface TextInputRequest {
  text: string
}

export interface ApiError {
  code: string
  message: string
}

// ── 面板 ────────────────────────────────────────────────────────────────────

export interface CharacterPanelData {
  character_name: string
  character_class: string
  level: number
  xp: number
  hp: number
  max_hp: number
  ac: number
  stats: Record<string, number>
  proficiency_bonus: number
  gold: number
  class_features: Array<string | { name: string; description?: string }>
  equipment: Record<string, unknown>
  [k: string]: unknown
}

export interface CharacterPanelResponse {
  phase: string
  player: CharacterPanelData
}

export interface MapSubLocation {
  id: string
  name: string
}

export interface MapAreaSummary {
  id: string
  name: string
  danger_level: number | null
  exploration: string
  tags: string[]
  sub_locations: MapSubLocation[]
}

export interface MapPanelData {
  current_area: string
  current_location: string | null
  discovered_area_ids: string[]
  areas: MapAreaSummary[]
}

export interface QuestPanelData {
  milestone_states: Record<string, unknown>
  dynamic_quests: Record<string, unknown>
  chapter_completion: Record<string, number>
}

export interface InventoryItem {
  item_id: string
  name: string
  count: number
  type?: string
  base_price?: number
  [k: string]: unknown
}

export interface InventoryPanelData {
  gold: number
  inventory: InventoryItem[]
  equipment: Record<string, InventoryItem | null>
}

// ── 图片生成 ──────────────────────────────────────────────────────────────────

export interface ImageResponse {
  image_url: string | null
  source: 'cached' | 'generated' | 'fallback'
  error?: string | null
}
