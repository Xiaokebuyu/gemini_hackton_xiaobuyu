import { create } from 'zustand'
import type { GameOption, LocationOverview } from '../types/game'
import type { DialogueOptionItem } from '../types/sse'

interface OptionState {
  options: GameOption[]
  isLocked: boolean
  hasDialogueOptions: boolean   // 是否已有 LLM 生成的对话选项（用于判断是否跳过 buildFromOverview）

  setOptions: (opts: GameOption[], hasDialogueOptions?: boolean) => void
  setFromDialogueOptions: (items: DialogueOptionItem[], onSelect: (item: DialogueOptionItem) => void) => void
  buildFromOverview: (overview: LocationOverview, handlers: OverviewHandlers) => void
  lock: () => void
  unlock: () => void
  clearOptions: () => void
}

export interface OverviewHandlers {
  onTalkToNpc: (npcId: string, npcName: string) => void
  onEnterSubLocation: (locationId: string) => void
  onInteractWith: (interactableId: string, interactableName: string) => void
  onMoveTo: (areaId: string) => void
  onLeaveSubLocation: () => void
}

const NPC_ICON: Record<string, string> = {
  main: '👤',
  secondary: '👤',
  passerby: '🧑',
}

const SUB_LOCATION_TYPE_ICON: Record<string, string> = {
  shop: '🛒',
  quest: '📋',
  rest: '⛺',
  worship: '⛪',
  dungeon: '⚔',
  encounter: '⚔',
  visit: '🚪',
  discovery: '🔍',
}

const CHECK_LABELS: Record<string, string> = {
  persuasion: '说服',
  deception: '欺瞒',
  intimidation: '威吓',
  performance: '表演',
  insight: '洞察',
  perception: '感知',
  investigation: '调查',
  survival: '求生',
  nature: '自然',
  history: '历史',
  arcana: '奥秘',
  religion: '宗教',
  athletics: '运动',
  acrobatics: '杂技',
  stealth: '潜行',
}

function formatCheckLabel(skill: string) {
  return CHECK_LABELS[skill] ?? skill.replace(/_/g, ' ')
}

function formatDialogueOptionLabel(item: DialogueOptionItem): string {
  const base = item.label ?? item.text ?? String(item.id ?? '').trim()
  if (!base) return ''
  if (base.startsWith('[')) return base

  const check = item.check
  const rawSkill = check?.skill ?? check?.type
  if (!rawSkill) return base

  const skill = formatCheckLabel(rawSkill)
  const dc = typeof check?.dc === 'number' ? ` DC${check.dc}` : ''
  return `[${skill}${dc}] ${base}`
}

export const useOptionStore = create<OptionState>((set) => ({
  options: [],
  isLocked: false,
  hasDialogueOptions: false,

  setOptions: (opts, hasDialogueOptions = false) => set({ options: opts, hasDialogueOptions }),

  // LLM 生成的对话选项（dialogue_options 事件）
  setFromDialogueOptions: (items, onSelect) => {
    const opts = items
      .map<GameOption | null>((item) => {
        const label = formatDialogueOptionLabel(item)
        if (!label) return null
        return {
          id: String(item.id ?? label),
          label,
          icon: item.icon,
          action: () => onSelect(item),
        }
      })
      .filter((item): item is GameOption => item !== null)
    set({ options: opts, hasDialogueOptions: true })
  },

  // 从 location_overview 组装探索选项
  buildFromOverview: (overview, handlers) => {
    const opts: GameOption[] = []

    // NPC 交互
    for (const npc of overview.present_npcs) {
      opts.push({
        id: `talk-${npc.character_id}`,
        label: `和${npc.name}说话`,
        icon: NPC_ICON[npc.role] ?? '👤',
        action: () => handlers.onTalkToNpc(npc.character_id, npc.name),
      })
    }

    // 子地点（过滤掉当前所在位置）
    for (const loc of overview.sub_locations) {
      if (!loc.available || loc.id === overview.location_id) continue
      opts.push({
        id: `enter-${loc.id}`,
        label: `进入${loc.name}`,
        icon: SUB_LOCATION_TYPE_ICON[loc.type] ?? '🚪',
        action: () => handlers.onEnterSubLocation(loc.id),
      })
    }

    // 可交互物
    for (const iact of overview.interactables) {
      opts.push({
        id: `interact-${iact.id}`,
        label: iact.name,
        icon: '📋',
        action: () => handlers.onInteractWith(iact.id, iact.name),
      })
    }

    // 如果在子地点内，显示"离开"
    if (overview.location_id) {
      opts.push({
        id: 'leave-sub-location',
        label: '离开，回到外面',
        icon: '🚪',
        action: () => handlers.onLeaveSubLocation(),
      })
    }

    // 区域出口
    for (const exit of overview.exits) {
      if (exit.blocked) continue
      opts.push({
        id: `move-${exit.target_area_id}`,
        label: `前往${exit.name}`,
        icon: '🚶',
        action: () => handlers.onMoveTo(exit.target_area_id),
      })
    }

    set({ options: opts, hasDialogueOptions: false })
  },

  lock: () => set({ isLocked: true }),
  unlock: () => set({ isLocked: false }),
  clearOptions: () => set({ options: [], hasDialogueOptions: false }),
}))
