import { useEffect, useState } from 'react'
import { useOptionStore } from '../stores/optionStore'
import { useSceneStore } from '../stores/sceneStore'
import { usePartyStore } from '../stores/partyStore'
import { audio } from '../lib/audio'
import type { InteractRequest } from '../types/api'
import type { OverviewHandlers } from '../stores/optionStore'

interface Props {
  sendInteract: (req: InteractRequest) => void
  overviewHandlers: OverviewHandlers
}

export default function OptionPanel({ sendInteract, overviewHandlers }: Props) {
  const [text, setText] = useState('')
  const [channelScope, setChannelScope] = useState<'public' | 'party'>('public')
  const { options, isLocked } = useOptionStore()
  const activeNpcId = useSceneStore((s) => s.activeNpcId)
  const gameMode = useSceneStore((s) => s.gameMode)
  const openingInProgress = useSceneStore((s) => s.openingInProgress)
  const hasParty = usePartyStore((s) => Object.keys(s.members).length > 0)
  const isPrivateMode = gameMode === 'private_chat' && !!activeNpcId

  useEffect(() => {
    if (!hasParty || activeNpcId || gameMode === 'private_chat') {
      setChannelScope('public')
    }
  }, [hasParty, activeNpcId, gameMode])

  const handleSend = () => {
    const trimmed = text.trim()
    if (!trimmed || isLocked || openingInProgress) return
    audio.playClick()
    if (isPrivateMode && activeNpcId) {
      sendInteract({
        scope: 'private',
        intent: 'talk',
        target_kind: 'npc',
        target_id: activeNpcId,
        message: trimmed,
      })
    } else if (activeNpcId) {
      sendInteract({
        scope: 'public',
        intent: 'talk',
        target_kind: 'npc',
        target_id: activeNpcId,
        message: trimmed,
      })
    } else if (channelScope === 'party' && hasParty) {
      sendInteract({ scope: 'party', intent: 'chat', message: trimmed })
    } else {
      sendInteract({ scope: 'public', intent: 'talk', message: trimmed })
    }
    setText('')
  }

  const handleLeaveDialogue = () => {
    audio.playClick()
    const sceneStore = useSceneStore.getState()
    const optStore = useOptionStore.getState()
    sceneStore.setActiveNpc(null)
    sceneStore.setGameMode('explore')
    optStore.clearOptions()
    const lastOverview = sceneStore.lastOverview
    if (lastOverview) {
      optStore.buildFromOverview(lastOverview, overviewHandlers)
    }
  }

  return (
    <div className="flex-shrink-0 border-t border-gray-700/50 pt-2">
      {/* 选项按钮列表 */}
      {isLocked ? (
        <div className="text-gray-500 text-sm py-1 px-1">思考中...</div>
      ) : openingInProgress ? (
        <div className="text-amber-200/80 text-sm py-1 px-1">开场演出中...</div>
      ) : options.length > 0 || activeNpcId ? (
        <div className="flex flex-wrap gap-1.5 mb-2 max-h-28 overflow-y-auto">
          {options.map((opt) => (
            <button
              key={opt.id}
              onClick={() => { audio.playClick(); opt.action() }}
              disabled={opt.disabled ?? false}
              className="text-left px-3 py-1.5 rounded-lg bg-gray-800/80 hover:bg-gray-700 border border-gray-600/50 hover:border-gray-500 text-gray-200 text-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {opt.icon && <span className="mr-1.5">{opt.icon}</span>}
              {opt.label}
            </button>
          ))}
          {activeNpcId && (
            <button
              onClick={handleLeaveDialogue}
              className="text-left px-3 py-1.5 rounded-lg bg-gray-700/60 hover:bg-gray-600 border border-gray-500/50 text-gray-400 hover:text-gray-200 text-sm transition-colors"
            >
              <span className="mr-1.5">🚪</span>结束对话
            </button>
          )}
        </div>
      ) : null}

      {/* 自由输入框 */}
      <div className="flex gap-2">
        {hasParty && !activeNpcId && gameMode !== 'private_chat' && (
          <div className="flex gap-1">
            <button
              onClick={() => { audio.playClick(); setChannelScope('public') }}
              className={`px-2.5 py-1.5 rounded-lg text-xs border transition-colors ${
                channelScope === 'public'
                  ? 'bg-amber-600/90 border-amber-500 text-white'
                  : 'bg-gray-800/60 border-gray-600/50 text-gray-300 hover:bg-gray-700'
              }`}
            >
              公开
            </button>
            <button
              onClick={() => { audio.playClick(); setChannelScope('party') }}
              className={`px-2.5 py-1.5 rounded-lg text-xs border transition-colors ${
                channelScope === 'party'
                  ? 'bg-amber-600/90 border-amber-500 text-white'
                  : 'bg-gray-800/60 border-gray-600/50 text-gray-300 hover:bg-gray-700'
              }`}
            >
              队友
            </button>
          </div>
        )}
        <input
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSend()}
          disabled={isLocked || openingInProgress}
          placeholder={
            openingInProgress
              ? '开场演出中...'
              : isLocked
                ? '请等待...'
                : isPrivateMode
                  ? '悄悄对TA说...'
                  : activeNpcId
                  ? '输入你想说的话...'
                  : channelScope === 'party' && hasParty
                    ? '只让队友听见...'
                    : '对周围的人说点什么...'
          }
          className="flex-1 bg-gray-800/60 border border-gray-600/50 text-gray-100 placeholder-gray-500 rounded-lg px-3 py-1.5 text-sm focus:border-amber-500/70 outline-none disabled:opacity-50"
        />
        <button
          onClick={handleSend}
          disabled={isLocked || openingInProgress || !text.trim()}
          className="bg-amber-600 hover:bg-amber-500 disabled:opacity-40 text-white px-4 py-1.5 rounded-lg text-sm transition-colors"
        >
          发送
        </button>
      </div>
    </div>
  )
}
