// Zustand 战斗状态 store
import { create } from 'zustand'
import type { CombatParticipant, DiceRollEntry, DamageNumber } from '../types/combat'
import type {
  EncounterSpottedData,
  StealthResultData,
  CombatStartData,
  CombatUpdateData,
  CombatEndData,
  LootDisplayData,
  DiceRollData,
} from '../types/sse'

interface CombatStore {
  // 遭遇阶段
  encounterActive: boolean
  encounterData: EncounterSpottedData | null
  stealthResultData: StealthResultData | null

  // 战斗阶段
  combatActive: boolean
  subAreaId: string | null
  combatRound: number
  surpriseState: string
  participants: CombatParticipant[]
  playerHp: number
  playerMaxHp: number
  playerAc: number
  playerEffects: string[]

  // 视觉反馈
  diceRollQueue: DiceRollEntry[]
  damageNumbers: DamageNumber[]
  combatLog: string[]      // 最近 20 条

  // 结束状态
  combatEndData: CombatEndData | null
  lootData: LootDisplayData | null

  // 动作
  startEncounter: (data: EncounterSpottedData) => void
  setStealthResult: (data: StealthResultData) => void
  startCombat: (data: CombatStartData) => void
  updateParticipants: (data: CombatUpdateData) => void
  addDiceRoll: (data: DiceRollData) => void
  dismissDiceRoll: () => void
  addDamageNumber: (value: number, kind: 'damage' | 'heal' | 'miss', targetId: string) => void
  removeDamageNumber: (id: string) => void
  addLogEntry: (text: string) => void
  endCombat: (data: CombatEndData) => void
  setLoot: (data: LootDisplayData) => void
  resetCombat: () => void
}

let _uid = 0
const nextId = () => String(++_uid)

export const useCombatStore = create<CombatStore>((set) => ({
  encounterActive: false,
  encounterData: null,
  stealthResultData: null,

  combatActive: false,
  subAreaId: null,
  combatRound: 1,
  surpriseState: 'none',
  participants: [],
  playerHp: 0,
  playerMaxHp: 0,
  playerAc: 10,
  playerEffects: [],

  diceRollQueue: [],
  damageNumbers: [],
  combatLog: [],

  combatEndData: null,
  lootData: null,

  startEncounter: (data) =>
    set({ encounterActive: true, encounterData: data, stealthResultData: null }),

  setStealthResult: (data) =>
    set({ stealthResultData: data }),

  startCombat: (data) =>
    set((s) => ({
      combatActive: true,
      encounterActive: false,
      subAreaId: data.sub_area_id,
      combatRound: data.round,
      surpriseState: data.surprise_state,
      participants: data.participants.map((p) => ({ ...p, is_dead: p.hp <= 0 })),
      playerHp: data.player.hp,
      playerMaxHp: data.player.max_hp,
      playerAc: data.player.ac,
      playerEffects: data.player.active_effects,
      combatLog: [
        ...s.combatLog.slice(-19),
        `第 ${data.round} 轮战斗开始（${data.surprise_state}）`,
      ],
    })),

  updateParticipants: (data) =>
    set((s) => ({
      combatRound: data.round,
      // 保留已有 ac（combat_update 不含 ac 字段）
      participants: data.participants.map((p) => ({
        ...p,
        ac: s.participants.find((x) => x.id === p.id)?.ac ?? 10,
        is_player: s.participants.find((x) => x.id === p.id)?.is_player ?? false,
      })),
      playerHp: data.player.hp,
      playerMaxHp: data.player.max_hp,
      playerAc: data.player.ac,
      playerEffects: data.player.active_effects,
      combatLog: [...s.combatLog.slice(-19), `第 ${data.round} 轮结束`],
    })),

  addDiceRoll: (data) =>
    set((s) => ({
      diceRollQueue: [...s.diceRollQueue, { id: nextId(), ...data }],
    })),

  dismissDiceRoll: () =>
    set((s) => ({ diceRollQueue: s.diceRollQueue.slice(1) })),

  addDamageNumber: (value, kind, targetId) =>
    set((s) => ({
      damageNumbers: [
        ...s.damageNumbers,
        { id: nextId(), value, kind, target_id: targetId, timestamp: Date.now() },
      ],
    })),

  removeDamageNumber: (id) =>
    set((s) => ({ damageNumbers: s.damageNumbers.filter((d) => d.id !== id) })),

  addLogEntry: (text) =>
    set((s) => ({ combatLog: [...s.combatLog.slice(-19), text] })),

  endCombat: (data) =>
    set({ combatEndData: data, combatActive: false }),

  setLoot: (data) =>
    set({ lootData: data }),

  resetCombat: () =>
    set({
      encounterActive: false,
      encounterData: null,
      stealthResultData: null,
      combatActive: false,
      subAreaId: null,
      combatRound: 1,
      surpriseState: 'none',
      participants: [],
      playerHp: 0,
      playerMaxHp: 0,
      playerAc: 10,
      playerEffects: [],
      diceRollQueue: [],
      damageNumbers: [],
      combatLog: [],
      combatEndData: null,
      lootData: null,
    }),
}))
