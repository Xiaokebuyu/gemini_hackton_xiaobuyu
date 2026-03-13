import { useState, useEffect, useCallback, useRef } from 'react'
import { useVnStore } from '../../stores/vnStore'
import { useDialogueStore } from '../../stores/dialogueStore'
import { useSceneStore } from '../../stores/sceneStore'
import { useOptionStore } from '../../stores/optionStore'
import { useStreamStore } from '../../stores/streamStore'
import { audio } from '../../lib/audio'
import VnTextBox from './VnTextBox'
import VnGmComment from './VnGmComment'
import VnEmoteFloat from './VnEmoteFloat'
import VnOptionList from './VnOptionList'
import type { OverviewHandlers } from '../../stores/optionStore'
import type { InteractRequest } from '../../types/api'

interface VnDialogueLayerProps {
  overviewHandlers: OverviewHandlers
  sendInteract: (req: InteractRequest) => void
}

export default function VnDialogueLayer({ overviewHandlers, sendInteract }: VnDialogueLayerProps) {
  const advance = useVnStore((s) => s.advance)
  const reset = useVnStore((s) => s.reset)
  const drainToLog = useVnStore((s) => s.drainToLog)
  const queue = useVnStore((s) => s.queue)
  const isShowingOptions = useVnStore((s) => s.isShowingOptions)

  const batchAddToLog = useDialogueStore((s) => s.batchAddToLog)
  const setActiveNpc = useSceneStore((s) => s.setActiveNpc)
  const setGameMode = useSceneStore((s) => s.setGameMode)
  const activeNpcId = useSceneStore((s) => s.activeNpcId)
  const gameMode = useSceneStore((s) => s.gameMode)
  const lastOverview = useSceneStore((s) => s.lastOverview)
  const isStreaming = useStreamStore((s) => s.isStreaming)

  const [text, setText] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  const handleLeave = useCallback(() => {
    const entries = drainToLog()
    if (entries.length > 0) {
      batchAddToLog(entries)
    }
    reset()
    setActiveNpc(null)
    setGameMode('explore')
    useOptionStore.getState().clearOptions()
    if (lastOverview) {
      useOptionStore.getState().buildFromOverview(lastOverview, overviewHandlers)
    }
  }, [drainToLog, batchAddToLog, reset, setActiveNpc, setGameMode, lastOverview, overviewHandlers])

  // Keyboard: Space/Enter = advance (only when NOT focused on input); Escape = leave
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Don't capture when typing in an input or textarea
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return

      if (e.key === ' ' || e.key === 'Enter') {
        e.preventDefault()
        advance()
      } else if (e.key === 'Escape') {
        e.preventDefault()
        handleLeave()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [advance, handleLeave])

  const handleSend = () => {
    const trimmed = text.trim()
    if (!trimmed || isStreaming) return
    audio.playClick()

    const isPrivateMode = gameMode === 'private_chat' && !!activeNpcId
    if (isPrivateMode && activeNpcId) {
      sendInteract({ scope: 'private', intent: 'talk', target_kind: 'npc', target_id: activeNpcId, message: trimmed })
    } else if (activeNpcId) {
      sendInteract({ scope: 'public', intent: 'talk', target_kind: 'npc', target_id: activeNpcId, message: trimmed })
    } else {
      sendInteract({ scope: 'public', intent: 'talk', message: trimmed })
    }

    useVnStore.getState().enqueueMessage({ type: 'player', content: trimmed })
    setText('')
  }

  return (
    <div className="absolute inset-0 z-10 flex flex-col items-center justify-end pointer-events-none">
      {/* Floating emote — positioned above the relevant portrait */}
      <VnEmoteFloat />

      {/* GM comment — flex item, naturally sits above options/panel */}
      <VnGmComment />

      {/* Options row — above panel, same width */}
      {isShowingOptions && (
        <div className="pointer-events-auto mb-2 w-[62%] max-w-[720px] flex flex-wrap gap-2 justify-center">
          <VnOptionList sendInteract={sendInteract} />
        </div>
      )}

      {/* ── Persistent centered panel ── */}
      <div
        className="
          pointer-events-auto
          w-[62%] max-w-[720px]
          mb-3 rounded-lg
          bg-stone-950/85 backdrop-blur-sm
          border border-amber-500/25
          shadow-[0_0_12px_rgba(245,158,11,0.12)]
          flex flex-col
          min-h-[8rem]
        "
      >
        {/* Text area — click to advance */}
        <div className="flex-1 px-5 pt-4 pb-2 cursor-pointer" onClick={advance}>
          {queue.length > 0 ? (
            <VnTextBox onAdvance={advance} />
          ) : (
            <p className="text-gray-500 text-sm italic select-none">
              {isStreaming ? '对方正在思考...' : '对话已开始，等待回应...'}
            </p>
          )}
        </div>

        {/* Input row — always visible */}
        <div className="flex items-center gap-2 px-4 py-2.5 border-t border-amber-500/10">
          <input
            ref={inputRef}
            type="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSend()}
            disabled={isStreaming}
            placeholder={isStreaming ? '对方正在回应...' : '输入你想说的话...'}
            className="
              flex-1
              bg-stone-900/60 border border-gray-600/50
              text-gray-100 placeholder-gray-500
              rounded-lg px-3 py-1.5 text-sm
              focus:border-amber-500/70 outline-none
              disabled:opacity-50
            "
          />
          <button
            onClick={handleSend}
            disabled={isStreaming || !text.trim()}
            className="bg-amber-600 hover:bg-amber-500 disabled:opacity-40 text-white px-4 py-1.5 rounded-lg text-sm transition-colors"
          >
            发送
          </button>
          <button
            onClick={handleLeave}
            className="
              text-xs text-gray-400 hover:text-gray-200
              bg-stone-950/60 hover:bg-stone-900/70
              border border-gray-700/50 hover:border-gray-500/50
              rounded px-3 py-1.5
              transition-colors duration-150
            "
          >
            结束对话
          </button>
        </div>
      </div>
    </div>
  )
}
