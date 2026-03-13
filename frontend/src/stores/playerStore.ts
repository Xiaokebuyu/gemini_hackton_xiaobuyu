import { create } from 'zustand'

interface PlayerState {
  name: string
  characterClass: string
  level: number
  hp: number
  maxHp: number
  gold: number
  asiAvailable: boolean
  area: string
  location: string | null
  day: number
  slot: number
  period: string

  updateFromPanel: (data: unknown) => void
  updateFromStatus: (data: Record<string, unknown>) => void
}

export const usePlayerStore = create<PlayerState>((set) => ({
  name: '',
  characterClass: '',
  level: 1,
  hp: 0,
  maxHp: 0,
  gold: 0,
  asiAvailable: false,
  area: '',
  location: null,
  day: 1,
  slot: 0,
  period: 'dawn',

  updateFromPanel: (data) => {
    const payload = (typeof data === 'object' && data !== null)
      ? data as Record<string, unknown>
      : {}
    const nestedPlayer = payload.player
    const player = (
      nestedPlayer && typeof nestedPlayer === 'object'
        ? nestedPlayer
        : payload
    ) as Record<string, unknown>
    set((state) => ({
      name: String(player.character_name ?? player.name ?? ''),
      characterClass: String(player.character_class ?? ''),
      level: Number(player.level ?? 1),
      hp: Number(player.current_hp ?? player.hp ?? 0),
      maxHp: Number(player.max_hp ?? player.maxHp ?? 0),
      gold: Number(player.gold ?? 0),
      asiAvailable: Boolean(
        player.asi_available ?? (
          typeof player.asi_points_remaining === 'number'
            ? Number(player.asi_points_remaining) > 0
            : state.asiAvailable
        )
      ),
      area: String(player.current_area ?? ''),
      location: player.current_location != null ? String(player.current_location) : null,
      day: Number(player.day ?? state.day),
      slot: Number(player.slot ?? state.slot),
      period: String(player.period ?? state.period),
    }))
  },

  updateFromStatus: (data) =>
    set((state) => ({
      hp: Number(data.hp ?? state.hp),
      maxHp: Number(data.max_hp ?? state.maxHp),
      gold: Number(data.gold ?? state.gold),
      asiAvailable: data.asi_available != null
        ? Boolean(data.asi_available)
        : data.asi_points_remaining != null
          ? Number(data.asi_points_remaining) > 0
          : state.asiAvailable,
      day: Number(data.day ?? state.day),
      slot: Number(data.slot ?? state.slot),
      period: String(data.period ?? state.period),
      level: data.level != null ? Number(data.level) : state.level,
    })),
}))
