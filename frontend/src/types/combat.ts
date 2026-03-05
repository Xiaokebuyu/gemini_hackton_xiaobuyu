// 战斗模式专用类型

export interface CombatParticipant {
  id: string
  name: string
  hp: number
  max_hp: number
  ac: number
  is_player: boolean
  status_effects: string[]
  is_dead: boolean
}

export interface DiceRollEntry {
  id: string
  type: string        // e.g. "d20"
  result: number
  modifier: number
  total: number
  dc: number
  success: boolean
  skill: string
  roller: string
  roller_name: string
}

export interface DamageNumber {
  id: string
  value: number                    // 正=治疗，负=伤害
  kind: 'damage' | 'heal' | 'miss'
  target_id: string
  timestamp: number
}
