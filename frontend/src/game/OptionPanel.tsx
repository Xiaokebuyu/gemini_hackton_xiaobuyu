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

// Left-border accent color per category (3px left border as subtle color coding)
const CATEGORY_LEFT_BORDER_STYLE: Record<string, React.CSSProperties> = {
  talk:     { borderLeft: '3px solid rgba(14, 165, 233, 0.5)' },
  room:     { borderLeft: '3px solid rgba(20, 184, 166, 0.5)' },
  location: { borderLeft: '3px solid rgba(245, 158, 11, 0.5)' },
  action:   { borderLeft: '3px solid rgba(16, 185, 129, 0.5)' },
  gear:     { borderLeft: '3px solid rgba(168, 85, 247, 0.5)' },
  leave:    { borderLeft: '3px solid rgba(156, 163, 175, 0.5)' },
}

// Category ordering for display
const CATEGORY_ORDER: GameOption['category'][] = ['talk', 'room', 'location', 'action', 'gear', 'leave']

interface CategorySectionProps {
  label: string
  opts: GameOption[]
  category: string
}

function CategorySection({ label, opts, category }: CategorySectionProps) {
  if (opts.length === 0) return null
  return (
    <div className="mb-1.5">
      <div className="text-xs text-parchment-500 px-0.5 mb-1">{label}</div>
      <div className="grid grid-cols-2 gap-2">
        {opts.map((opt) => (
          <button
            key={opt.id}
            onClick={() => { audio.playClick(); opt.action() }}
            disabled={opt.disabled ?? false}
            className="btn-fantasy text-left"
            style={CATEGORY_LEFT_BORDER_STYLE[category]}
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
      return <div className="text-parchment-500 animate-breathe text-sm py-1 px-1">思考中...</div>
    }
    if (openingInProgress) {
      return <div className="text-gold-400/80 text-sm py-1 px-1">开场演出中...</div>
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
              className="btn-fantasy text-left"
            >
              {opt.icon && <span className="mr-1.5">{opt.icon}</span>}
              {opt.label}
            </button>
          ))}
          <button
            onClick={handleLeaveDialogue}
            className="btn-subtle text-parchment-500 text-left"
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
          <div className="flex gap-1 mb-2 pb-1">
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
                      ? 'btn-subtle !border-gold-500/50 !text-gold-300 !bg-gold-900/30'
                      : isEmpty
                        ? 'btn-subtle opacity-25 cursor-not-allowed'
                        : 'btn-subtle'
                  }
                >
                  {getCategoryLabel(catKey, lastOverview)}
                </button>
              )
            })}
          </div>
          <hr className="divider-ornate" />

          {/* Active tab content */}
          <div className="max-h-32 overflow-y-auto mt-2">
            <CategorySection
              label=""
              opts={grouped[activeTab as string] ?? []}
              category={activeTab as string}
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
              className={`btn-subtle ${
                channelScope === 'public'
                  ? '!bg-gold-800/50 !border-gold-500/40 !text-gold-300'
                  : ''
              }`}
            >
              公开
            </button>
            <button
              onClick={() => { audio.playClick(); setChannelScope('party') }}
              className={`btn-subtle ${
                channelScope === 'party'
                  ? '!bg-gold-800/50 !border-gold-500/40 !text-gold-300'
                  : ''
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
          className="input-fantasy flex-1 disabled:opacity-50"
        />
        <button
          onClick={handleSend}
          disabled={isLocked || openingInProgress || !text.trim()}
          className="btn-fantasy !bg-gold-700/40 !border-gold-500/40 hover:!bg-gold-600/50 !text-gold-300"
        >
          发送
        </button>
      </div>
    </div>
  )
}
