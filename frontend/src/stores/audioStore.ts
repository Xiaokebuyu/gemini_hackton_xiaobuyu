import { create } from 'zustand'
import { audio } from '../lib/audio'

interface AudioState {
  muted: boolean
  toggleMute: () => void
}

const initialMuted = localStorage.getItem('audioMuted') === 'true'
// 初始化 audio 单例的静音状态
audio.setMuted(initialMuted)

export const useAudioStore = create<AudioState>((set, get) => ({
  muted: initialMuted,

  toggleMute: () => {
    const next = !get().muted
    audio.setMuted(next)
    localStorage.setItem('audioMuted', String(next))
    set({ muted: next })
  },
}))
