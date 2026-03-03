// SSE 事件类型，对齐设计文档 §9.3 事件列表

export interface SSEEvent {
  event: string
  data: unknown
}

export interface GmNarrationData {
  content: string
}

export interface NpcResponseData {
  npc_id: string
  content: string
  type: 'speech' | 'refuse' | 'emote'
}

export interface NpcEmoteData {
  npc_id: string
  action: string
}

export interface TeammateResponseData {
  character_id: string
  content?: string
  action?: string
}

export interface TextChunkData {
  text: string
}

export interface DialogueOptionItem {
  id: string
  label: string
  icon?: string
}

export interface DialogueOptionsData {
  options: DialogueOptionItem[]
}

export interface ActionResultData {
  success: boolean
  action_type: string
  time_cost: number
  errors: string[]
  metadata: Record<string, unknown>
}

export interface SceneChangeData {
  location_id: string | null
  location_name: string
  background: string
  transition: string
  ambient_preset: string | null
  ambient_override: string | null
}

export interface TimeAdvancedData {
  day: number
  slot: number
  period: string
  description?: string
}

export interface RelationshipStageChangedData {
  npc_id: string
  npc_name?: string
  new_stage: string
  old_stage?: string
}

export interface NpcWantsToChatData {
  npc_id: string
  npc_name?: string
}

export interface StreamEndData {
  reason: string
  success: boolean
}

export interface StreamErrorData {
  message: string
  code?: string
}

export interface ShopSnapshotData {
  npc_id: string
  player_gold: number
  stock: Array<{ item_id: string; name: string; count: number; base_price: number; [k: string]: unknown }>
  player_sellable_items: Array<{ item_id: string; name: string; count: number; base_price: number }>
}

export interface BoardSnapshotData {
  target_id: string
  entries: Array<{ board_id: string; quest_id: string | null; title: string; content: string; quest_status: string | null }>
}
