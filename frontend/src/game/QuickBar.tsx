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
    <div className="flex-shrink-0">
      <hr className="divider-ornate" />
      <div className="flex items-center justify-between pt-2">
        <div className="flex gap-2.5">
          <button
            onClick={() => { audio.playClick(); overlay.open('log') }}
            className="btn-subtle"
          >
            LOG
          </button>
          <button
            onClick={() => { audio.playClick(); overlay.open('menu') }}
            className="btn-subtle"
          >
            MENU
          </button>
        </div>

        <div className="flex items-center gap-2.5">
          {saveMsg && <span className="text-xs text-gold-400">{saveMsg}</span>}
          <button
            onClick={toggleMute}
            title={muted ? '取消静音' : '静音'}
            className="btn-subtle"
          >
            {muted ? '🔇' : '🔊'}
          </button>
          <button
            onClick={handleSave}
            disabled={isStreaming}
            className="btn-subtle disabled:opacity-40"
          >
            SAVE
          </button>
        </div>
      </div>
    </div>
  )
}
