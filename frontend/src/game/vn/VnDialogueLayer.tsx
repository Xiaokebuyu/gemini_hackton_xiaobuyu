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

  const isPrivateChat = gameMode === 'private_chat'

  return (
    <div className="absolute inset-0 z-10 flex flex-col items-center justify-end pointer-events-none">
      {/* Floating emote — positioned above the relevant portrait */}
      <VnEmoteFloat />

      {/* GM comment — flex item, naturally sits above options/panel */}
      <VnGmComment />

      {/* Options row — above panel, centered */}
      {isShowingOptions && (
        <div className="pointer-events-auto mb-3 w-[56%] max-w-[640px] flex flex-col items-stretch gap-2">
          <VnOptionList sendInteract={sendInteract} />
        </div>
      )}

      {/* ── Galgame-style dialogue box ── */}
      <div
        className={`
          pointer-events-auto
          w-full
          flex flex-col
          ${isPrivateChat ? 'vn-box-private' : ''}
        `}
        style={{
          background: isPrivateChat
            ? 'linear-gradient(180deg, rgba(60, 20, 80, 0.75) 0%, rgba(20, 8, 30, 0.88) 100%)'
            : 'linear-gradient(180deg, rgba(10, 12, 20, 0.7) 0%, rgba(6, 8, 16, 0.92) 100%)',
          backdropFilter: 'blur(12px)',
          WebkitBackdropFilter: 'blur(12px)',
          borderTop: isPrivateChat
            ? '1px solid rgba(168, 85, 247, 0.3)'
            : '1px solid rgba(180, 160, 120, 0.15)',
          boxShadow: isPrivateChat
            ? '0 -4px 24px rgba(120, 50, 180, 0.15), inset 0 1px 0 rgba(168, 85, 247, 0.1)'
            : '0 -4px 24px rgba(0, 0, 0, 0.4), inset 0 1px 0 rgba(200, 180, 120, 0.06)',
        }}
      >
        {/* Text area — click to advance */}
        <div
          className="flex-1 px-[12%] pt-5 pb-2 cursor-pointer min-h-[7rem]"
          onClick={advance}
        >
          {queue.length > 0 ? (
            <VnTextBox onAdvance={advance} />
          ) : (
            <p className="text-parchment-500 text-sm italic select-none animate-breathe">
              {isStreaming ? '对方正在思考...' : '对话已开始，等待回应...'}
            </p>
          )}
        </div>

        {/* Input row */}
        <div
          className="flex items-center gap-2 px-[12%] py-2.5"
          style={{
            borderTop: '1px solid rgba(180, 160, 120, 0.08)',
          }}
        >
          <input
            ref={inputRef}
            type="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSend()}
            disabled={isStreaming}
            placeholder={isStreaming ? '对方正在回应...' : '输入你想说的话...'}
            className="input-fantasy flex-1 disabled:opacity-50"
          />
          <button
            onClick={handleSend}
            disabled={isStreaming || !text.trim()}
            className="btn-fantasy !bg-gold-700/40 !border-gold-500/40 hover:!bg-gold-600/50 !text-gold-300 disabled:opacity-40"
          >
            发送
          </button>
          <button
            onClick={handleLeave}
            className="btn-subtle text-parchment-500"
          >
            结束对话
          </button>
        </div>
      </div>
    </div>
  )
}
