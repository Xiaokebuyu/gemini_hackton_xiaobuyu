import { create } from 'zustand'

interface PlayerState {
  name: string
  characterClass: string
  level: number
  hp: number
  maxHp: number
  gold: number
  area: string
  location: string | null

  updateFromPanel: (data: Record<string, unknown>) => void
}

export const usePlayerStore = create<PlayerState>((set) => ({
  name: '',
  characterClass: '',
  level: 1,
  hp: 0,
  maxHp: 0,
  gold: 0,
  area: '',
  location: null,

  updateFromPanel: (data) => {
    const player = (data.player ?? data) as Record<string, unknown>
    set({
      name: String(player.character_name ?? player.name ?? ''),
      characterClass: String(player.character_class ?? ''),
      level: Number(player.level ?? 1),
      hp: Number(player.current_hp ?? player.hp ?? 0),
      maxHp: Number(player.max_hp ?? player.maxHp ?? 0),
      gold: Number(player.gold ?? 0),
      area: String(player.current_area ?? ''),
      location: player.current_location != null ? String(player.current_location) : null,
    })
  },
}))
