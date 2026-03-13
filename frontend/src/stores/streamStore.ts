import { create } from 'zustand'

interface StreamState {
  isStreaming: boolean
  setStreaming: (v: boolean) => void
  aiProcessing: boolean
  setAiProcessing: (v: boolean) => void
}

export const useStreamStore = create<StreamState>((set) => ({
  isStreaming: false,
  setStreaming: (v) => set({ isStreaming: v }),
  aiProcessing: false,
  setAiProcessing: (v) => set({ aiProcessing: v }),
}))
