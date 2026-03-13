import { useEffect, useState } from 'react'
import { useOptionStore } from '../stores/optionStore'
import { useSceneStore } from '../stores/sceneStore'
import { usePartyStore } from '../stores/partyStore'
import { audio } from '../lib/audio'
import type { GameOption, LocationOverview } from '../types/game'
import type { InteractRequest } from '../types/api'
import type { OverviewHandlers } from '../stores/optionStore'

interface Props {
  sendInteract: (req: InteractRequest) => void
  overviewHandlers: OverviewHandlers
}

// Dynamic category label — 'location' tab title changes based on player position
function getCategoryLabel(cat: string, overview: LocationOverview | null): string {
  switch (cat) {
    case 'talk': return '交谈'
    case 'room': return '房间'
    case 'location':
      if (!overview) return '地点'
      if (!overview.location_id) return overview.area_name ?? '地点'
      return overview.location_name ?? '地点'
    case 'action': return '行动'
    case 'gear': return '装备'
    case 'leave': return '离开'
    default: return cat
  }
}

// Border/hover colors per category
const CATEGORY_BUTTON_CLASS: Record<string, string> = {
  talk: 'border-sky-600/30 hover:border-sky-500/50',
  room: 'border-teal-600/30 hover:border-teal-500/50',
  location: 'border-amber-600/30 hover:border-amber-500/50',
  action: 'border-emerald-600/30 hover:border-emerald-500/50',
  gear: 'border-purple-600/30 hover:border-purple-500/50',
  leave: 'border-gray-500/30 hover:border-gray-400/50',
}

// Category ordering for display
const CATEGORY_ORDER: GameOption['category'][] = ['talk', 'room', 'location', 'action', 'gear', 'leave']

interface CategorySectionProps {
  label: string
  opts: GameOption[]
  buttonClass: string
}

function CategorySection({ label, opts, buttonClass }: CategorySectionProps) {
  if (opts.length === 0) return null
  return (
    <div className="mb-1.5">
      <div className="text-xs text-gray-500 px-0.5 mb-1">{label}</div>
      <div className="grid grid-cols-2 gap-2">
        {opts.map((opt) => (
          <button
            key={opt.id}
            onClick={() => { audio.playClick(); opt.action() }}
            disabled={opt.disabled ?? false}
            className={`text-left bg-stone-900/60 hover:bg-stone-800/60 rounded-lg px-3 py-2 text-sm transition-all border ${buttonClass} text-gray-200 disabled:opacity-50 disabled:cursor-not-allowed`}
          >
            {opt.icon && <span className="mr-1.5">{opt.icon}</span>}
            {opt.label}
          </button>
        ))}
      </div>
    </div>
  )
}

export default function OptionPanel({ sendInteract, overviewHandlers }: Props) {
  const [text, setText] = useState('')
  const [channelScope, setChannelScope] = useState<'public' | 'party'>('public')
  const [activeTab, setActiveTab] = useState<GameOption['category']>('talk')
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

  useEffect(() => {
    // When options change, auto-select the first non-empty category tab
    const grouped: Record<string, GameOption[]> = { talk: [], room: [], location: [], action: [], gear: [], leave: [] }
    for (const opt of options) {
      const cat = opt.category ?? 'action'
      grouped[cat].push(opt)
    }
    const nonEmptyCategories = CATEGORY_ORDER.filter(cat => (grouped[cat as string]?.length ?? 0) > 0)
    if (nonEmptyCategories.length > 0 && !nonEmptyCategories.includes(activeTab)) {
      setActiveTab(nonEmptyCategories[0])
    }
  }, [options])

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

  // ── Options rendering ──────────────────────────────────────────────────────

  const renderOptions = () => {
    if (isLocked) {
      return <div className="text-gray-500 text-sm py-1 px-1">思考中...</div>
    }
    if (openingInProgress) {
      return <div className="text-amber-200/80 text-sm py-1 px-1">开场演出中...</div>
    }

    // VN mode / dialogue mode: flat list with "end dialogue" button (unchanged UX)
    if (activeNpcId) {
      return (
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
          <button
            onClick={handleLeaveDialogue}
            className="text-left px-3 py-1.5 rounded-lg bg-gray-700/60 hover:bg-gray-600 border border-gray-500/50 text-gray-400 hover:text-gray-200 text-sm transition-colors"
          >
            <span className="mr-1.5">🚪</span>结束对话
          </button>
        </div>
      )
    }

    // Exploration mode: tabbed categorized grid
    if (options.length > 0) {
      // Group by category; options without category fall into 'action'
      const grouped: Record<string, GameOption[]> = {
        talk: [],
        room: [],
        location: [],
        action: [],
        gear: [],
        leave: [],
      }
      for (const opt of options) {
        const cat = opt.category ?? 'action'
        grouped[cat].push(opt)
      }

      const lastOverview = useSceneStore.getState().lastOverview

      return (
        <div className="mb-2">
          {/* Tab row */}
          <div className="flex gap-1 mb-2 border-b border-gray-700/40 pb-1">
            {CATEGORY_ORDER.map((cat) => {
              const catKey = cat as string
              const isEmpty = (grouped[catKey]?.length ?? 0) === 0
              const isActive = activeTab === cat
              return (
                <button
                  key={catKey}
                  onClick={() => setActiveTab(cat)}
                  disabled={isEmpty}
                  className={
                    isActive
                      ? 'text-amber-300 border-b-2 border-amber-500 px-3 py-1 text-sm font-medium'
                      : 'text-gray-500 hover:text-gray-300 px-3 py-1 text-sm disabled:opacity-30 disabled:cursor-not-allowed'
                  }
                >
                  {getCategoryLabel(catKey, lastOverview)}
                </button>
              )
            })}
          </div>

          {/* Active tab content */}
          <div className="max-h-32 overflow-y-auto">
            <CategorySection
              label=""
              opts={grouped[activeTab as string] ?? []}
              buttonClass={CATEGORY_BUTTON_CLASS[activeTab as string]}
            />
          </div>
        </div>
      )
    }

    return null
  }

  return (
    <div className="flex-shrink-0 border-t border-gray-700/50 pt-2">
      {/* 选项区域 */}
      {renderOptions()}

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
