import { useState } from 'react'
import { useOverlayStore } from '../stores/overlayStore'
import { useStreamStore } from '../stores/streamStore'
import { useAudioStore } from '../stores/audioStore'
import { saveSession } from '../lib/api'
import { audio } from '../lib/audio'

interface Props {
  worldId: string
  sessionId: string
}

export default function QuickBar({ worldId, sessionId }: Props) {
  const overlay = useOverlayStore()
  const isStreaming = useStreamStore((s) => s.isStreaming)
  const { muted, toggleMute } = useAudioStore()
  const [saveMsg, setSaveMsg] = useState<string | null>(null)

  const handleSave = async () => {
    try {
      await saveSession(worldId, sessionId)
      setSaveMsg('✓ 已保存')
      setTimeout(() => setSaveMsg(null), 2000)
      audio.playCoin()
    } catch {
      setSaveMsg('保存失败')
      setTimeout(() => setSaveMsg(null), 2000)
      audio.playError()
    }
  }

  return (
    <div className="flex-shrink-0 flex items-center justify-between pt-1.5 border-t border-stone-700/30">
      <div className="flex gap-2">
        <button
          onClick={() => { audio.playClick(); overlay.open('log') }}
          className="text-gray-400 hover:text-gray-200 text-xs px-3 py-1 rounded border border-stone-700 hover:border-stone-500 transition-colors"
        >
          LOG
        </button>
        <button
          onClick={() => { audio.playClick(); overlay.open('menu') }}
          className="text-gray-400 hover:text-gray-200 text-xs px-3 py-1 rounded border border-stone-700 hover:border-stone-500 transition-colors"
        >
          MENU
        </button>
      </div>

      <div className="flex items-center gap-2">
        {saveMsg && <span className="text-xs text-amber-400">{saveMsg}</span>}
        <button
          onClick={toggleMute}
          title={muted ? '取消静音' : '静音'}
          className="text-gray-400 hover:text-gray-200 text-xs px-2 py-1 rounded border border-stone-700 hover:border-stone-500 transition-colors"
        >
          {muted ? '🔇' : '🔊'}
        </button>
        <button
          onClick={handleSave}
          disabled={isStreaming}
          className="text-gray-400 hover:text-gray-200 disabled:opacity-40 text-xs px-3 py-1 rounded border border-stone-700 hover:border-stone-500 transition-colors"
        >
          SAVE
        </button>
      </div>
    </div>
  )
}
