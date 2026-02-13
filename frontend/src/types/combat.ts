/**
 * Combat Types - 对应后端战斗系统模型
 */

// =============================================================================
// 战斗行动类型
// =============================================================================

export type CombatActionType =
  | 'ATTACK'
  | 'OFFHAND'
  | 'THROW'
  | 'SHOVE'
  | 'SPELL'
  | 'DEFEND'
  | 'MOVE'
  | 'DASH'
  | 'DISENGAGE'
  | 'USE_ITEM'
  | 'FLEE'
  | 'END_TURN';

// =============================================================================
// 距离段位
// =============================================================================

export type DistanceBand =
  | 'ENGAGED'    // 近战接触
  | 'CLOSE'      // 1步
  | 'NEAR'       // 2步
  | 'FAR'        // 3步
  | 'DISTANT';   // 4步+

export const DISTANCE_BAND_ORDER: DistanceBand[] = [
  'ENGAGED',
  'CLOSE',
  'NEAR',
  'FAR',
  'DISTANT',
];

export const DISTANCE_BAND_LABELS: Record<DistanceBand, string> = {
  ENGAGED: '接触',
  CLOSE: '近距',
  NEAR: '中距',
  FAR: '远距',
  DISTANT: '极远',
};

// =============================================================================
// 状态效果
// =============================================================================

export type StatusEffect =
  | 'POISONED'
  | 'STUNNED'
  | 'DEFENDING'
  | 'BURNING'
  | 'PRONE'
  | 'FRIGHTENED'
  | 'BLINDED'
  | 'RESTRAINED'
  | 'DISENGAGED'
  | 'HIDDEN';

export const STATUS_EFFECT_ICONS: Record<StatusEffect, string> = {
  POISONED: '🤢',
  STUNNED: '💫',
  DEFENDING: '🛡️',
  BURNING: '🔥',
  PRONE: '⬇️',
  FRIGHTENED: '😨',
  BLINDED: '👁️',
  RESTRAINED: '🔗',
  DISENGAGED: '↩️',
  HIDDEN: '👻',
};

export const STATUS_EFFECT_LABELS: Record<StatusEffect, string> = {
  POISONED: '中毒',
  STUNNED: '眩晕',
  DEFENDING: '防御',
  BURNING: '燃烧',
  PRONE: '倒地',
  FRIGHTENED: '恐惧',
  BLINDED: '致盲',
  RESTRAINED: '束缚',
  DISENGAGED: '脱战',
  HIDDEN: '隐身',
};

// =============================================================================
// 战斗参与者
// =============================================================================

export interface Combatant {
  id: string;
  name: string;
  is_player: boolean;
  is_ally: boolean;

  // 属性
  hp: number;
  max_hp: number;
  ac: number;

  // 位置
  distance_band: DistanceBand;

  // 状态
  status_effects: StatusEffect[];
  is_dead: boolean;

  // 回合相关
  initiative: number;
  has_acted: boolean;

  // 显示
  portrait?: string;
  description?: string;
}

// =============================================================================
// 战斗行动选项
// =============================================================================

export interface CombatActionOption {
  action_type: CombatActionType;
  display_name: string;
  description: string;
  enabled: boolean;
  requires?: string;

  // 目标
  requires_target: boolean;
  valid_targets?: string[];

  // 消耗
  costs?: {
    action?: boolean;
    bonus_action?: boolean;
    movement?: number;
  };
}

// =============================================================================
// 骰子结果
// =============================================================================

export interface DiceRoll {
  roll_type: 'd4' | 'd6' | 'd8' | 'd10' | 'd12' | 'd20' | 'd100';
  result: number;
  modifier: number;
  total: number;
  is_critical: boolean;
  is_fumble: boolean;
  // 检定扩展（仅 ability_check 时有值）
  ability?: string;
  skill?: string;
  proficiency?: number;
  dc?: number;
  success?: boolean;
  description?: string;
}

// =============================================================================
// 战斗日志
// =============================================================================

export type CombatLogEntryType =
  | 'attack'
  | 'damage'
  | 'heal'
  | 'spell'
  | 'status'
  | 'movement'
  | 'turn_start'
  | 'turn_end'
  | 'combat_start'
  | 'combat_end';

export interface CombatLogEntry {
  id: string;
  timestamp: Date;
  type: CombatLogEntryType;
  actor: string;
  target?: string;
  action?: string;
  roll?: DiceRoll;
  result?: {
    success: boolean;
    damage?: number;
    healing?: number;
    status_applied?: StatusEffect;
    status_removed?: StatusEffect;
    message: string;
  };
}

// =============================================================================
// 战斗状态
// =============================================================================

export interface CombatState {
  combat_id: string;
  is_active: boolean;

  // 参与者
  combatants: Combatant[];

  // 回合
  current_round: number;
  current_turn: number;
  active_combatant_id: string | null;
  turn_order: string[]; // combatant ids

  // 玩家行动
  player_actions: CombatActionOption[];
  selected_action: CombatActionType | null;
  selected_target: string | null;

  // 日志
  combat_log: CombatLogEntry[];

  // 结果
  is_victory: boolean | null;
  rewards?: {
    experience: number;
    gold: number;
    items: string[];
  };
}

// =============================================================================
// 战斗 API - 对应后端 game_v2 路由
// =============================================================================

/**
 * 触发战斗请求 (对应 TriggerCombatRequest)
 * POST /api/game/{world_id}/sessions/{session_id}/combat/trigger
 */
export interface TriggerCombatRequest {
  enemies: Record<string, unknown>[];
  player_state: Record<string, unknown>;
  combat_description?: string;
  environment?: Record<string, unknown>;
}

/**
 * 触发战斗响应 (对应 TriggerCombatResponse)
 */
export interface TriggerCombatResponse {
  combat_id: string;
  narration: string;
  combat_state: Record<string, unknown>;
  available_actions: Record<string, unknown>[];
}

/**
 * 战斗行动请求 (对应 CombatActionRequest)
 * POST /api/game/{world_id}/sessions/{session_id}/combat/action
 */
export interface CombatActionRequest {
  action_id: string;
}

/**
 * 战斗行动响应 (对应 CombatActionResponse)
 */
export interface CombatActionResponse {
  phase: string;
  narration: string;
  action_result?: Record<string, unknown>;
  combat_result?: Record<string, unknown>;
  available_actions: Record<string, unknown>[];
}

/**
 * 战斗开始请求 (Legacy, 对应 CombatStartRequest)
 * POST /api/game/{world_id}/sessions/{session_id}/combat/start
 */
export interface CombatStartRequest {
  player_state: Record<string, unknown>;
  enemies: Record<string, unknown>[];
  allies?: Record<string, unknown>[];
  environment?: Record<string, unknown>;
  combat_context?: {
    location?: string;
    participants?: string[];
    witnesses?: string[];
    visibility_public?: boolean;
    known_characters?: string[];
    character_locations?: Record<string, string>;
  };
}

/**
 * 战斗开始响应 (Legacy, 对应 CombatStartResponse)
 */
export interface CombatStartResponse {
  combat_id: string;
  combat_state: Record<string, unknown>;
  session: Record<string, unknown>;
}

/**
 * 战斗结算请求 (对应 CombatResolveRequest)
 * POST /api/game/{world_id}/sessions/{session_id}/combat/resolve
 */
export interface CombatResolveRequest {
  combat_id?: string;
  use_engine?: boolean;
  result_override?: Record<string, unknown>;
  summary_override?: string;
  dispatch?: boolean;
  recipients?: string[];
  per_character?: Record<string, unknown>;
  write_indexes?: boolean;
}

/**
 * 战斗结算响应 (对应 CombatResolveResponse)
 */
export interface CombatResolveResponse {
  combat_id: string;
  event_id?: string;
  dispatched: boolean;
}

// Legacy aliases
/** @deprecated 使用 TriggerCombatResponse */
export type StartCombatResponse = TriggerCombatResponse;
/** @deprecated 使用 CombatResolveResponse */
export type EndCombatResponse = CombatResolveResponse;
