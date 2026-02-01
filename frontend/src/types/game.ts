/**
 * Game Types - 对应后端 Pydantic 模型
 */

// =============================================================================
// 意图类型 (对应 app/models/admin_protocol.py)
// =============================================================================

export type IntentType =
  | 'navigation'
  | 'enter_sub_location'
  | 'leave_sub_location'
  | 'look_around'
  | 'npc_interaction'
  | 'team_interaction'
  | 'end_dialogue'
  | 'start_combat'
  | 'combat_action'
  | 'wait'
  | 'rest'
  | 'system_command'
  | 'roleplay'
  | 'unknown';

// =============================================================================
// Flash 操作类型 (对应 app/models/admin_protocol.py)
// =============================================================================

export type FlashOperation =
  | 'spawn_passerby'
  | 'npc_dialogue'
  | 'broadcast_event'
  | 'graphize_event'
  | 'recall_memory'
  | 'navigate'
  | 'update_time'
  | 'enter_sublocation'
  | 'start_combat'
  | 'trigger_narrative_event';

// =============================================================================
// 游戏状态 (对应 app/models/state_delta.py)
// =============================================================================

export interface GameTimeState {
  day: number;
  hour: number;
  minute: number;
  period: 'dawn' | 'day' | 'dusk' | 'night' | null;
  formatted: string | null;
}

export interface StateDelta {
  delta_id: string;
  timestamp: string;
  operation: string;
  changes: Record<string, unknown>;
  previous_values: Record<string, unknown>;
}

export interface GameState {
  session_id: string;
  world_id: string;
  player_location: string | null;
  sub_location: string | null;
  game_time: GameTimeState;
  chat_mode: 'think' | 'say';
  active_dialogue_npc: string | null;
  combat_id: string | null;
  narrative_progress: Record<string, unknown>;
  metadata: Record<string, unknown>;
  party_id: string | null;
}

// =============================================================================
// 操作类型 (对应 app/models/game_action.py)
// =============================================================================

export type ActionCategory =
  | 'movement'
  | 'interaction'
  | 'observation'
  | 'combat'
  | 'party'
  | 'inventory'
  | 'system';

export interface GameAction {
  action_id: string;
  category: ActionCategory;
  display_name: string;
  description: string;
  enabled: boolean;
  requires: string | null;
  hotkey: string | null;
  parameters: Record<string, unknown>;
  icon: string | null;
  priority: number;
}

export interface ActionGroup {
  category: ActionCategory;
  display_name: string;
  actions: GameAction[];
}

export interface AvailableActions {
  context_description: string;
  groups: ActionGroup[];
  quick_actions: GameAction[];
}

// =============================================================================
// 队伍类型 (对应 app/models/party.py)
// =============================================================================

export type TeammateRole =
  | 'warrior'
  | 'healer'
  | 'mage'
  | 'rogue'
  | 'support'
  | 'scout'
  | 'scholar';

export interface PartyMember {
  character_id: string;
  name: string;
  role: TeammateRole;
  personality: string;
  response_tendency: number; // 0-1
  joined_at: string;
  is_active: boolean;
  current_mood: string;
  graph_ref: string;
}

export interface Party {
  party_id: string;
  world_id: string;
  session_id: string;
  leader_id: string;
  members: PartyMember[];
  formed_at: string;
  max_size: number;
  auto_follow: boolean;
  share_events: boolean;
  current_location: string | null;
  current_sub_location: string | null;
}

export interface TeammateResponseResult {
  character_id: string;
  name: string;
  response: string | null; // null = 选择不回复
  reaction: string;
  model_used: string;
  thinking_level: string | null;
  latency_ms: number;
}

// =============================================================================
// 协调器响应 (对应 app/models/admin_protocol.py)
// =============================================================================

export interface CoordinatorResponse {
  narration: string;
  speaker: string;
  teammate_responses: TeammateResponseResult[];
  available_actions: GameAction[];
  state_delta: StateDelta | null;
  metadata: Record<string, unknown>;
}

// =============================================================================
// 位置相关
// =============================================================================

export interface SubLocation {
  id: string;
  name: string;
  description: string;
  is_accessible: boolean;
}

export interface Destination {
  location_id: string;
  name: string;
  description: string;
  distance: string;
  danger_level: 'low' | 'medium' | 'high' | 'extreme';
  is_accessible: boolean;
}

export interface LocationData {
  location_id: string;
  name: string;
  description: string;
  sub_locations: SubLocation[];
  destinations: Destination[];
  npcs_present: string[];
}

// =============================================================================
// 消息类型 (前端专用)
// =============================================================================

export type MessageType =
  | 'gm'
  | 'npc'
  | 'player'
  | 'teammate'
  | 'system'
  | 'combat';

export interface NarrativeMessage {
  id: string;
  speaker: string;
  content: string;
  type: MessageType;
  timestamp: Date;
  metadata?: {
    reaction?: string;
    npc_id?: string;
    intent_type?: IntentType;
    character_id?: string;
    role?: TeammateRole;
  };
}

// =============================================================================
// API 请求/响应类型 (对应 app/models/game.py)
// =============================================================================

/**
 * 玩家输入请求 (对应 PlayerInputRequest)
 */
export interface PlayerInputRequest {
  input: string;
  input_type?: 'narration' | 'dialogue' | 'combat' | 'system' | null;
  mode?: 'think' | 'say' | null;
}

/**
 * 玩家输入响应 (对应 PlayerInputResponse)
 */
export interface PlayerInputResponse {
  type: 'narration' | 'dialogue' | 'combat' | 'system' | 'error';
  response: string;
  speaker: string;
  npc_id?: string | null;
  event_recorded: boolean;
  tool_called: boolean;
  recalled_memory?: string | null;
  available_actions: GameAction[] | Record<string, unknown>[];
  state_changes: Record<string, unknown>;
  responses: TeammateResponseResult[] | Record<string, unknown>[];  // 聊天室模式额外回应
}

/**
 * 位置响应 (对应 LocationResponse)
 */
export interface LocationResponse {
  location_id: string;
  location_name: string;
  description: string;
  atmosphere: string;
  danger_level: 'low' | 'medium' | 'high' | 'extreme';
  available_destinations: Destination[];
  npcs_present: string[];
  available_actions: string[];
  time: GameTimeResponse;
}

/**
 * 游戏时间响应 (对应 GameTimeResponse)
 */
export interface GameTimeResponse {
  day: number;
  hour: number;
  minute: number;
  period: 'dawn' | 'day' | 'dusk' | 'night';
  formatted: string;
}

/**
 * 导航请求 (对应 NavigateRequest)
 */
export interface NavigateRequest {
  destination?: string;
  direction?: 'north' | 'south' | 'east' | 'west';
}

/**
 * 旅途分段 (对应 TravelSegment)
 */
export interface TravelSegment {
  from_id: string;
  from_name: string;
  to_id: string;
  to_name: string;
  travel_time: string;
  time_minutes: number;
  danger_level: string;
  narration: string;
  event?: Record<string, unknown>;
}

/**
 * 导航响应 (对应 NavigateResponse)
 */
export interface NavigateResponse {
  success: boolean;
  narration: string;
  segments: TravelSegment[];
  new_location?: LocationResponse;
  time_elapsed_minutes: number;
  events: Record<string, unknown>[];
  time?: GameTimeResponse;
  error?: string;
}

/**
 * 创建会话请求
 */
export interface CreateSessionRequest {
  session_id?: string;
  participants?: string[];
}

/**
 * 创建会话响应
 */
export interface CreateSessionResponse {
  session: GameSessionState;
}

/**
 * 游戏会话状态
 */
export interface GameSessionState {
  session_id: string;
  world_id: string;
  status: string;
  current_scene?: SceneState;
  participants: string[];
  active_combat_id?: string;
  updated_at: string;
  metadata: Record<string, unknown>;
}

/**
 * 场景状态
 */
export interface SceneState {
  scene_id?: string;
  description: string;
  location?: string;
  atmosphere?: string;
  participants: string[];
}

// 保留旧的别名以兼容
export type GameInputRequest = PlayerInputRequest;
