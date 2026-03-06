import { create } from 'zustand'
import type { DialogueEntry } from '../types/game'

let _nextId = 0
const genId = () => `msg-${++_nextId}`

interface DialogueState {
  messages: DialogueEntry[]   // 当前场景的对话流
  log: DialogueEntry[]        // 全量历史（跨场景）
  pendingStreamId: string | null

  addMessage: (entry: Omit<DialogueEntry, 'id' | 'timestamp'>) => void
  appendToLast: (text: string) => void
  appendStreamChunk: (text: string) => void
  resolveStreamMessage: (entry: Omit<DialogueEntry, 'id' | 'timestamp'>) => void
  clearPendingStream: () => void
  clearForSceneChange: () => void
  resetMessages: () => void
}

export const useDialogueStore = create<DialogueState>((set) => ({
  messages: [],
  log: [],
  pendingStreamId: null,

  addMessage: (entry) => {
    const full: DialogueEntry = { id: genId(), timestamp: Date.now(), ...entry }
    set((s) => {
      const messages = [...s.messages, full]
      return { messages: messages.length > 150 ? messages.slice(-100) : messages }
    })
  },

  // text_chunk 事件：逐 token 追加到最后一条消息末尾
  appendToLast: (text) => {
    set((s) => {
      if (s.messages.length === 0) return s
      const last = s.messages[s.messages.length - 1]
      const updated = { ...last, content: last.content + text }
      return { messages: [...s.messages.slice(0, -1), updated] }
    })
  },

  appendStreamChunk: (text) => {
    if (!text) return
    set((s) => {
      if (s.pendingStreamId && s.messages.length > 0) {
        const last = s.messages[s.messages.length - 1]
        if (last.id === s.pendingStreamId) {
          const updated = { ...last, content: last.content + text }
          return { messages: [...s.messages.slice(0, -1), updated] }
        }
      }

      const pending: DialogueEntry = {
        id: genId(),
        timestamp: Date.now(),
        type: 'stream',
        content: text,
      }
      const messages = [...s.messages, pending]
      return {
        messages: messages.length > 150 ? messages.slice(-100) : messages,
        pendingStreamId: pending.id,
      }
    })
  },

  resolveStreamMessage: (entry) => {
    const full: DialogueEntry = { id: genId(), timestamp: Date.now(), ...entry }
    set((s) => {
      if (s.pendingStreamId && s.messages.length > 0) {
        const last = s.messages[s.messages.length - 1]
        if (last.id === s.pendingStreamId) {
          const resolved: DialogueEntry = {
            id: last.id,
            timestamp: last.timestamp,
            ...entry,
          }
          return {
            messages: [...s.messages.slice(0, -1), resolved],
            pendingStreamId: null,
          }
        }
      }
      const messages = [...s.messages, full]
      return {
        messages: messages.length > 150 ? messages.slice(-100) : messages,
      }
    })
  },

  clearPendingStream: () => {
    set((s) => {
      if (!s.pendingStreamId || s.messages.length === 0) {
        return { pendingStreamId: null }
      }
      const last = s.messages[s.messages.length - 1]
      if (last.id !== s.pendingStreamId) {
        return { pendingStreamId: null }
      }
      return {
        messages: s.messages.slice(0, -1),
        pendingStreamId: null,
      }
    })
  },

  // 场景切换：当前消息流追加到全局 log，清空当前流
  clearForSceneChange: () => {
    set((s) => {
      const combined = [...s.log, ...s.messages]
      return {
        log: combined.length > 500 ? combined.slice(-400) : combined,
        messages: [],
        pendingStreamId: null,
      }
    })
  },

  resetMessages: () => set({ messages: [], pendingStreamId: null }),
}))
