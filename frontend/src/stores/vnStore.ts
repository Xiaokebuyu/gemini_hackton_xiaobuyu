import { create } from 'zustand'
import type { VnMessage, DialogueEntry } from '../types/game'

let _nextId = 0
const genId = () => `vn-${++_nextId}`

interface VnState {
  queue: VnMessage[]
  currentIndex: number
  displayedCharCount: number
  isTypewriterComplete: boolean
  activeGmComment: { content: string; id: string } | null
  activeEmote: { speaker: string; content: string; id: string } | null
  isShowingOptions: boolean
  // Track whether the last queue entry is an unresolved streaming placeholder
  _pendingStreamId: string | null

  // Actions
  enqueueMessage: (msg: Omit<VnMessage, 'id'>) => void
  /** Resolve a streaming placeholder into a typed message (like npc_response after text_chunk) */
  resolveStreamMessage: (msg: Omit<VnMessage, 'id'>) => void
  appendToCurrentContent: (text: string) => void
  advance: () => void
  tickTypewriter: () => void
  completeTypewriter: () => void
  showGmComment: (content: string) => void
  dismissGmComment: () => void
  showEmote: (speaker: string, content: string) => void
  dismissEmote: () => void
  setShowingOptions: (v: boolean) => void
  reset: () => void
  drainToLog: () => DialogueEntry[]
}

const INITIAL_STATE = {
  queue: [] as VnMessage[],
  currentIndex: 0,
  displayedCharCount: 0,
  isTypewriterComplete: false,
  activeGmComment: null as { content: string; id: string } | null,
  activeEmote: null as { speaker: string; content: string; id: string } | null,
  isShowingOptions: false,
  _pendingStreamId: null as string | null,
}

export const useVnStore = create<VnState>((set, get) => ({
  ...INITIAL_STATE,

  enqueueMessage: (msg) => {
    const full: VnMessage = { id: genId(), ...msg }
    set((s) => {
      const queue = [...s.queue, full]
      const isFirst = s.queue.length === 0
      return {
        queue,
        _pendingStreamId: null, // clear any pending stream
        displayedCharCount: isFirst ? 0 : s.displayedCharCount,
        isTypewriterComplete: isFirst ? false : s.isTypewriterComplete,
      }
    })
  },

  // Append streaming text to the last message in the queue (or create a placeholder)
  appendToCurrentContent: (text) => {
    set((s) => {
      if (s.queue.length === 0 || s._pendingStreamId === null) {
        // No pending stream — create a new streaming placeholder
        const id = genId()
        const placeholder: VnMessage = { id, type: 'gm', content: text }
        return {
          queue: [...s.queue, placeholder],
          _pendingStreamId: id,
          // If this is the very first message, reset typewriter
          ...(s.queue.length === 0
            ? { displayedCharCount: 0, isTypewriterComplete: false }
            : {}),
        }
      }
      // Append to the existing pending stream entry
      const idx = s.queue.findIndex((m) => m.id === s._pendingStreamId)
      if (idx === -1) {
        // Fallback: pending ID stale, create new placeholder
        const id = genId()
        const placeholder: VnMessage = { id, type: 'gm', content: text }
        return { queue: [...s.queue, placeholder], _pendingStreamId: id }
      }
      const entry = s.queue[idx]
      const updated: VnMessage = { ...entry, content: entry.content + text }
      const newQueue = [...s.queue]
      newQueue[idx] = updated
      return { queue: newQueue }
    })
  },

  // Resolve a streaming placeholder into a typed message
  // (e.g., text_chunk built up content, then npc_response arrives with type/speaker)
  resolveStreamMessage: (msg) => {
    set((s) => {
      if (s._pendingStreamId !== null) {
        // Replace the streaming placeholder with the resolved message
        const idx = s.queue.findIndex((m) => m.id === s._pendingStreamId)
        if (idx !== -1) {
          const resolved: VnMessage = {
            id: s._pendingStreamId,
            type: msg.type,
            speaker: msg.speaker,
            speakerName: msg.speakerName,
            content: msg.content,
            tone: msg.tone,
          }
          const newQueue = [...s.queue]
          newQueue[idx] = resolved
          return {
            queue: newQueue,
            _pendingStreamId: null,
            // Reset typewriter if this is the current message being displayed
            ...(s.currentIndex === idx
              ? { displayedCharCount: 0, isTypewriterComplete: false }
              : {}),
          }
        }
      }
      // No pending stream — just enqueue as new
      const full: VnMessage = { id: genId(), ...msg }
      const queue = [...s.queue, full]
      const isFirst = s.queue.length === 0
      return {
        queue,
        _pendingStreamId: null,
        displayedCharCount: isFirst ? 0 : s.displayedCharCount,
        isTypewriterComplete: isFirst ? false : s.isTypewriterComplete,
      }
    })
  },

  advance: () => {
    set((s) => {
      const current = s.queue[s.currentIndex]
      if (!current) return s

      // If typewriter is still running, complete it instantly
      if (!s.isTypewriterComplete) {
        return {
          displayedCharCount: current.content.length,
          isTypewriterComplete: true,
        }
      }

      // Advance to next message if available
      const nextIndex = s.currentIndex + 1
      if (nextIndex < s.queue.length) {
        return {
          currentIndex: nextIndex,
          displayedCharCount: 0,
          isTypewriterComplete: false,
          // Clear floating overlays on advance
          activeGmComment: null,
          activeEmote: null,
        }
      }

      // No more messages — stay at end (options or wait)
      return s
    })
  },

  tickTypewriter: () => {
    set((s) => {
      const current = s.queue[s.currentIndex]
      if (!current || s.isTypewriterComplete) return s
      const next = s.displayedCharCount + 1
      if (next >= current.content.length) {
        return { displayedCharCount: current.content.length, isTypewriterComplete: true }
      }
      return { displayedCharCount: next }
    })
  },

  completeTypewriter: () => {
    set((s) => {
      const current = s.queue[s.currentIndex]
      if (!current) return s
      return { displayedCharCount: current.content.length, isTypewriterComplete: true }
    })
  },

  showGmComment: (content) => {
    set({ activeGmComment: { content, id: genId() } })
  },

  dismissGmComment: () => {
    set({ activeGmComment: null })
  },

  showEmote: (speaker, content) => {
    set({ activeEmote: { speaker, content, id: genId() } })
  },

  dismissEmote: () => {
    set({ activeEmote: null })
  },

  setShowingOptions: (v) => {
    set({ isShowingOptions: v })
  },

  reset: () => {
    set(INITIAL_STATE)
  },

  drainToLog: () => {
    const { queue } = get()
    const now = Date.now()
    const entries: DialogueEntry[] = queue.map((msg, i) => ({
      id: msg.id,
      type: msg.type === 'gm_comment' ? 'gm_comment' : msg.type,
      speaker: msg.speaker,
      speakerName: msg.speakerName,
      content: msg.content,
      tone: msg.tone,
      timestamp: now + i,
    }))
    return entries
  },
}))
