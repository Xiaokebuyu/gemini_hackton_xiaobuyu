import { create } from 'zustand'
import type { PartySnapshot } from '../types/api'
import type { PresentNpc } from '../types/game'

interface PartyMember {
  id: string
  name: string
  classId: string
  approval: number
}

interface PartyState {
  members: Record<string, PartyMember>

  initFromSnapshot: (snapshot: PartySnapshot) => void
  addMember: (npcId: string, name?: string) => void
  removeMember: (npcId: string) => void
  syncMembers: (memberIds: string[]) => void
  enrichFromPresentNpcs: (npcs: PresentNpc[]) => void
  isMember: (npcId: string) => boolean
  clear: () => void
}

export const usePartyStore = create<PartyState>((set, get) => ({
  members: {},

  initFromSnapshot: (snapshot) => {
    const members: Record<string, PartyMember> = {}
    for (const [id, data] of Object.entries(snapshot.members ?? {})) {
      members[id] = {
        id,
        name: data.name ?? '',
        classId: data.class_id ?? '',
        approval: snapshot.companion_approval?.[id] ?? 0,
      }
    }
    set({ members })
  },

  addMember: (npcId, name) => {
    set((s) => ({
      members: {
        ...s.members,
        [npcId]: {
          id: npcId,
          name: name ?? s.members[npcId]?.name ?? '',
          classId: s.members[npcId]?.classId ?? '',
          approval: s.members[npcId]?.approval ?? 0,
        },
      },
    }))
  },

  removeMember: (npcId) => {
    set((s) => {
      const { [npcId]: _, ...rest } = s.members
      return { members: rest }
    })
  },

  syncMembers: (memberIds) => {
    if (!memberIds) return
    const current = get().members
    const synced: Record<string, PartyMember> = {}
    for (const id of memberIds) {
      synced[id] = current[id] ?? { id, name: '', classId: '', approval: 0 }
    }
    set({ members: synced })
  },

  enrichFromPresentNpcs: (npcs) => {
    const companions = npcs.filter((n) => n.is_companion || n.role === 'companion')
    if (companions.length === 0) return
    set((s) => {
      const updated = { ...s.members }
      for (const npc of companions) {
        if (updated[npc.character_id]) {
          updated[npc.character_id] = {
            ...updated[npc.character_id],
            name: npc.name,
          }
        }
      }
      return { members: updated }
    })
  },

  isMember: (npcId) => npcId in get().members,

  clear: () => set({ members: {} }),
}))
