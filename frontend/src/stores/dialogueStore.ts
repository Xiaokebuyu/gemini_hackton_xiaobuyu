import { create } from 'zustand'
import type { DialogueEntry } from '../types/game'

let _nextId = 0
const genId = () => `msg-${++_nextId}`

interface DialogueState {
  messages: DialogueEntry[]   // 当前场景的对话流
  log: DialogueEntry[]        // 全量历史（跨场景）

  addMessage: (entry: Omit<DialogueEntry, 'id' | 'timestamp'>) => void
  appendToLast: (text: string) => void
  clearForSceneChange: () => void
}

export const useDialogueStore = create<DialogueState>((set) => ({
  messages: [],
  log: [],

  addMessage: (entry) => {
    const full: DialogueEntry = { id: genId(), timestamp: Date.now(), ...entry }
    set((s) => ({ messages: [...s.messages, full] }))
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

  // 场景切换：当前消息流追加到全局 log，清空当前流
  clearForSceneChange: () => {
    set((s) => ({ log: [...s.log, ...s.messages], messages: [] }))
  },
}))
