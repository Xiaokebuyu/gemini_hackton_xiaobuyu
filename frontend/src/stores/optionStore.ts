import { create } from 'zustand'
import { describeInteractableAction } from '../game/sceneActionAdapters'
import type { GameOption, Interactable, LocationOverview, Room } from '../types/game'
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
  onInteractWith: (interactable: Interactable) => void
  onMoveTo: (areaId: string) => void
  onLeaveSubLocation: () => void
  onEnterRoom: (roomId: string) => void
  onLeaveRoom: () => void
  onRestShort: () => void
  onRestLong: () => void
  onSetCamp: () => void
  onNightWatch: () => void
  onOpenInventory: () => void
  onUseItem: (itemId: string) => void
}

const NPC_ICON: Record<string, string> = {
  main: '👤',
  secondary: '👤',
  passerby: '🧑',
}

// Icon map for functional dialogue options (2-4 GM Functional Options)
const FUNCTIONAL_ICON: Record<string, string> = {
  trade_browse: '🛒',
  quest_accept: '📋',
  board_browse: '📜',
  navigate: '🚶',
  inspect_item: '🔍',
  rest: '🛏️',
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
        // Functional options get a type-specific icon so players can identify
        // them as UI-triggering actions, not just plain dialogue lines
        const functionalIcon = item.functional?.type
          ? FUNCTIONAL_ICON[item.functional.type]
          : undefined
        return {
          id: String(item.id ?? label),
          label,
          icon: functionalIcon ?? item.icon,
          action: () => onSelect(item),
        }
      })
      .filter((item): item is GameOption => item !== null)
    set({ options: opts, hasDialogueOptions: true })
  },

  // 从 location_overview 组装探索选项
  buildFromOverview: (overview, handlers) => {
    const opts: GameOption[] = []

    // NPC 交互 → talk
    for (const npc of overview.present_npcs) {
      const isCompanion = npc.is_companion ?? npc.role === 'companion'
      const roleIcon = NPC_ICON[npc.role] ?? '👤'
      opts.push({
        id: `talk-${npc.character_id}`,
        label: isCompanion ? `与${npc.name}交谈` : `和${npc.name}说话`,
        icon: isCompanion ? '⚔' : roleIcon,
        action: () => handlers.onTalkToNpc(npc.character_id, npc.name),
        category: 'talk',
      })
    }

    // 子地点（过滤掉当前所在位置）→ location
    for (const loc of overview.sub_locations) {
      if (!loc.available || loc.id === overview.location_id) continue
      opts.push({
        id: `enter-${loc.id}`,
        label: `进入${loc.name}`,
        icon: SUB_LOCATION_TYPE_ICON[loc.type] ?? '🚪',
        action: () => handlers.onEnterSubLocation(loc.id),
        category: 'location',
      })
    }

    // Room 导航（在 sub_location 内才有 rooms）→ room
    const rooms: Room[] = overview.rooms ?? []
    if (overview.location_id && rooms.length > 0) {
      for (const room of rooms) {
        if (room.id === overview.current_room) {
          continue
        } else if (room.discovered || !room.discoverable) {
          opts.push({
            id: `enter-room-${room.id}`,
            label: `前往${room.name}`,
            icon: '🚪',
            action: () => handlers.onEnterRoom(room.id),
            category: 'room',
          })
        } else {
          opts.push({
            id: `room-locked-${room.id}`,
            label: '??? (未发现)',
            icon: '🔒',
            action: () => {},
            disabled: true,
            category: 'room',
          })
        }
      }
    }

    // 可交互物 → action
    for (const iact of overview.interactables) {
      const actionMeta = describeInteractableAction(iact)
      opts.push({
        id: `interact-${iact.id}`,
        label: actionMeta.label,
        icon: actionMeta.icon,
        action: () => handlers.onInteractWith(iact),
        category: 'action',
      })
    }

    // 休息 / 扎营动作 + 背包 → gear
    opts.push(
      {
        id: 'rest-short',
        label: '短休',
        icon: '🛏️',
        action: () => handlers.onRestShort(),
        category: 'gear',
      },
      {
        id: 'rest-long',
        label: '长休',
        icon: '🌙',
        action: () => handlers.onRestLong(),
        category: 'gear',
      },
      {
        id: 'set-camp',
        label: '扎营',
        icon: '⛺',
        action: () => handlers.onSetCamp(),
        category: 'gear',
      },
      {
        id: 'night-watch',
        label: '值守',
        icon: '👁️',
        action: () => handlers.onNightWatch(),
        category: 'gear',
      },
    )
    opts.push({
      id: 'open-inventory',
      label: '打开背包',
      icon: '🎒',
      action: () => handlers.onOpenInventory(),
      category: 'gear',
    })

    // 在 room 中：离开房间 → room
    if (overview.current_room) {
      opts.push({
        id: 'leave-room',
        label: '离开房间',
        icon: '🚪',
        action: () => handlers.onLeaveRoom(),
        category: 'room',
      })
    }

    // 如果在子地点内，显示"离开" → leave
    if (overview.location_id) {
      opts.push({
        id: 'leave-sub-location',
        label: '离开，回到外面',
        icon: '🚪',
        action: () => handlers.onLeaveSubLocation(),
        category: 'leave',
      })
    }

    // 区域出口 → leave
    for (const exit of overview.exits) {
      if (exit.blocked) continue
      opts.push({
        id: `move-${exit.target_area_id}`,
        label: `前往${exit.name}`,
        icon: '🚶',
        action: () => handlers.onMoveTo(exit.target_area_id),
        category: 'leave',
      })
    }

    set({ options: opts, hasDialogueOptions: false })
  },

  lock: () => set({ isLocked: true }),
  unlock: () => set({ isLocked: false }),
  clearOptions: () => set({ options: [], hasDialogueOptions: false }),
}))
