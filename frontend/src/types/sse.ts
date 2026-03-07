// SSE 事件类型，对齐设计文档 §9.3 事件列表

export interface SSEEvent {
  event: string
  data: unknown
}

export interface GmNarrationData {
  content: string
}

export interface GmCommentData {
  content: string
  tone?: string
}

export interface CharacterEnterData {
  character_id: string
  position: 'left' | 'center' | 'right'
  emotion?: string
  animation?: string
}

export interface NpcResponseData {
  npc_id: string
  content: string
  type: 'speech' | 'refuse' | 'emote'
  passive?: boolean
}

export interface NpcEmoteData {
  npc_id: string
  action: string
  passive?: boolean
}

export interface TeammateResponseData {
  character_id: string
  content?: string
  action?: string
}

export interface TextChunkData {
  text: string
}

export interface DialogueOptionDispatch {
  kind: 'navigate' | 'interact' | 'input' | 'action' | 'local'
  payload: Record<string, unknown>
}

export interface DialogueOptionCheck {
  skill?: string
  type?: string
  dc?: number
}

export interface DialogueOptionItem {
  id?: string | number
  text?: string
  label?: string
  icon?: string
  intent?: string
  action?: string
  check?: DialogueOptionCheck | null
  quest_id?: string
  item_id?: string
  count?: number
  dispatch?: DialogueOptionDispatch
}

export interface DialogueOptionsData {
  npc_id?: string
  options: DialogueOptionItem[]
}

export interface DialogueOptionsUnavailableData {
  npc_id?: string | null
  code: string
  message: string
  recoverable: boolean
}

export interface ActionResultData {
  success: boolean
  action_type: string
  time_cost: number
  errors: string[]
  metadata: Record<string, unknown>
  narrative_hints?: string[]
  rolls?: Array<Record<string, unknown>>
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

export interface CompanionRecruitedData {
  npc_id: string
  reason: string
  party_members: string[]
}

export interface CompanionDismissedData {
  npc_id: string
  reason: string
  party_members: string[]
}

export interface StreamEndData {
  reason: string
  success: boolean
}

export interface StreamErrorData {
  message: string
  code?: string
}

export interface InputRejectedData {
  text: string
  normalized_text: string
  code: string
  message: string
}

export interface InteractionRejectedData {
  target_kind?: string | null
  target_id?: string | null
  intent?: string
  item_id?: string | null
  quest_id?: string | null
  count?: number
  code: string
  message: string
}

export interface NpcErrorData {
  npc_id: string
  code?: string
  error?: string
}

export interface HookErrorData {
  hook: string
  error_type: string
  message: string
}

export interface ShopSnapshotData {
  npc_id: string
  player_gold: number
  stock: Array<{ item_id: string; name: string; count: number; base_price: number; [k: string]: unknown }>
  player_sellable_items: Array<{ item_id: string; name: string; count: number; base_price: number }>
}

export interface TalkSnapshotData {
  target_id: string
  profile?: {
    npc_id?: string
    name?: string
  }
  available_intents?: string[]
}

export interface DiscoveryRevealData {
  name: string
  description?: string
}

export interface CampfireDialogueData {
  teammate_id: string
  content: string
  memory_type?: string
  memory_summary?: string
}

export interface EventStateChangedData {
  event_id: string
  from_state: string
  to_state: string
  reason: string
  source?: string
  title?: string
}

export interface HiddenObjectRevealedData {
  area_id: string
  interactable_id: string
  name: string
}

export interface TrapDetectedData {
  area_id: string
  interactable_id: string
}

export interface GenericErrorEventData {
  message?: string
  error?: string
  code?: string
}

export interface QuestBriefData {
  target_kind: string
  target_id: string
  quest: {
    quest_id: string
    status: string
    title: string
    summary: string
    source_milestone?: string | null
  }
}

export interface QuestProgressData {
  target_kind: string
  target_id: string
  quest: {
    quest_id: string
    status: string
    title: string
    summary: string
    can_accept: boolean
    is_active: boolean
    is_closed: boolean
    source_milestone?: string | null
    source_milestone_state?: string | null
  }
}

export interface QuestLocationData {
  target_kind: string
  target_id: string
  quest: {
    quest_id: string
    status: string
    location_known: boolean
    area_id?: string | null
    location_id?: string | null
    source_milestone?: string | null
  }
}

export interface QuestRequirementsData {
  target_kind: string
  target_id: string
  quest: {
    quest_id: string
    status: string
    requirements_known: boolean
    requirements: string[]
    can_accept: boolean
    gating_reason?: string | null
    source_milestone?: string | null
  }
}

export interface QuestRewardData {
  target_kind: string
  target_id: string
  quest: {
    quest_id: string
    status: string
    reward_known: boolean
    gold?: number | null
    items: Array<{ item_id: string; count: number }>
    reward_summary?: string | null
  }
}

// ── 战斗 SSE 事件 ─────────────────────────────────────────────────────────────

export interface EncounterSpottedData {
  sub_area_id: string
  area_id: string
  name: string
  description: string
  blocking: boolean
  threat_level: 'easy' | 'moderate' | 'hard' | 'deadly'
  monster_count: number
  options: { action: string; label: string }[]
  source: string
}

export interface StealthResultData {
  success: boolean
  roll: number
  dc: number
  modifier: number
  advantage: boolean
  disadvantage: boolean
  narrative: string
  options: { action: string; label: string }[]
  surprise_state: string
}

export interface DiceRollData {
  type: string
  result: number
  modifier: number
  total: number
  dc: number
  success: boolean
  skill: string
  roller: string
  roller_name: string
}

export interface CombatStartData {
  sub_area_id: string
  round: number
  surprise_state: string
  blocking: boolean
  participants: {
    id: string
    name: string
    hp: number
    max_hp: number
    ac: number
    is_player: boolean
    status_effects: string[]
  }[]
  player: {
    hp: number
    max_hp: number
    ac: number
    active_effects: string[]
  }
}

export interface CombatUpdateData {
  sub_area_id: string
  round: number
  participants: {
    id: string
    name: string
    hp: number
    max_hp: number
    is_dead: boolean
    status_effects: string[]
  }[]
  player: {
    hp: number
    max_hp: number
    ac: number
    active_effects: string[]
  }
  combat_cleared: boolean
  fled: boolean
}

export interface CombatEndData {
  result: 'victory' | 'defeat' | 'fled'
  sub_area_id: string
  xp_gained: number
  gold_gained: number
  rounds_fought: number
}

export interface VfxData {
  effect: string
  target_id: string
}

export interface StatusUpdateData {
  kind?: string
  target_id?: string
  hp_delta?: number
  new_hp?: number
  max_hp?: number
  cause?: string
  hp?: number
  gold?: number
  day?: number
  slot?: number
  period?: string
}

export interface EffectAppliedData {
  target_id: string
  effect_id: string
  effect_name: string
  duration: number
}

export interface EffectRemovedData {
  target_id: string
  effect_id: string
  effect_name: string
}

export interface EffectTickData {
  target_id: string
  effect_id: string
  hp_delta: number
}

export interface LootDisplayData {
  items: { item_id: string; name: string; count: number; rarity?: string }[]
  gold: number
}

export interface MilestoneUnlockedData {
  milestone_id: string
  unlocked_by: string
}

export interface MilestoneFailedData {
  milestone_id: string
  failure_fallback: string
}
