import { create } from 'zustand'

interface SessionState {
  worldId: string | null
  sessionId: string | null
  phase: string | null
  setSession: (worldId: string, sessionId: string, phase: string) => void
  clearSession: () => void
}

export const useSessionStore = create<SessionState>((set) => ({
  worldId: null,
  sessionId: null,
  phase: null,
  setSession: (worldId, sessionId, phase) => set({ worldId, sessionId, phase }),
  clearSession: () => set({ worldId: null, sessionId: null, phase: null }),
}))
