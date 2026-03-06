import { create } from 'zustand'
import type { ResumeSessionResponse } from '../types/api'

interface SessionState {
  worldId: string | null
  sessionId: string | null
  phase: string | null
  resumeBootstrap: ResumeSessionResponse | null
  setSession: (worldId: string, sessionId: string, phase: string) => void
  setPhase: (phase: string | null) => void
  setResumeBootstrap: (payload: ResumeSessionResponse | null) => void
  consumeResumeBootstrap: (worldId: string, sessionId: string) => ResumeSessionResponse | null
  clearSession: () => void
}

export const useSessionStore = create<SessionState>((set, get) => ({
  worldId: null,
  sessionId: null,
  phase: null,
  resumeBootstrap: null,
  setSession: (worldId, sessionId, phase) => set({ worldId, sessionId, phase }),
  setPhase: (phase) => set({ phase }),
  setResumeBootstrap: (payload) => set({ resumeBootstrap: payload }),
  consumeResumeBootstrap: (worldId, sessionId): ResumeSessionResponse | null => {
    const state = get()
    const payload = state.resumeBootstrap
    if (!payload) return null
    if (payload.world_id !== worldId || payload.session_id !== sessionId) {
      return null
    }
    set({ resumeBootstrap: null })
    return payload
  },
  clearSession: () => set({ worldId: null, sessionId: null, phase: null, resumeBootstrap: null }),
}))
