import { useEffect, useState } from 'react'
import { useOverlayStore } from '../../stores/overlayStore'
import { useSessionStore } from '../../stores/sessionStore'
import { getQuests } from '../../lib/api'
import type { QuestPanelData, NpcInvite } from '../../types/api'

type QuestStatus = 'active' | 'available' | 'completed' | 'closed'

interface QuestObjective {
  description: string
  completed: boolean
}

function questStatus(v: Record<string, unknown>): QuestStatus {
  const status = String(v.status ?? '').toLowerCase()
  if (status === 'completed') return 'completed'
  if (status === 'failed' || status === 'retired') return 'closed'
  if (status === 'available' || status === 'discovered') return 'available'
  return 'active'
}

function questSummary(v: Record<string, unknown>): string {
  const summary = typeof v.summary === 'string' ? v.summary.trim() : ''
  if (summary) return summary
  const description = typeof v.description === 'string' ? v.description.trim() : ''
  return description
}

function questObjectives(v: Record<string, unknown>): QuestObjective[] {
  const raw = v.objectives
  if (!Array.isArray(raw)) return []
  return raw
    .filter((obj): obj is Record<string, unknown> => obj !== null && typeof obj === 'object')
    .map((obj) => ({
      description: typeof obj.description === 'string' ? obj.description : '',
      completed: Boolean(obj.completed),
    }))
    .filter((obj) => obj.description.length > 0)
}

function questCurrentStep(v: Record<string, unknown>): string | null {
  const step = v.current_step
  if (typeof step === 'string' && step.trim()) return step.trim()
  return null
}

function questHints(v: Record<string, unknown>): string[] {
  const raw = v.hints
  if (!Array.isArray(raw)) return []
  return raw
    .filter((h): h is string => typeof h === 'string' && h.trim().length > 0)
    .map((h) => h.trim())
}

const STATUS_ICONS: Record<QuestStatus, string> = {
  active: '⚔',
  available: '📋',
  completed: '✅',
  closed: '✖',
}

const STATUS_LABELS: Record<QuestStatus, string> = {
  active: '进行中',
  available: '可接取',
  completed: '已完成',
  closed: '已结束',
}

export default function QuestPanel() {
  const { close } = useOverlayStore()
  const { worldId, sessionId } = useSessionStore()
  const [data, setData] = useState<QuestPanelData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  useEffect(() => {
    if (!worldId || !sessionId) return
    getQuests(worldId, sessionId)
      .then(setData)
      .catch((err: Error) => setError(err.message))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const toggleCollapse = (key: string) => {
    setCollapsed((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  const grouped: Record<QuestStatus, Array<[string, Record<string, unknown>]>> = {
    active: [],
    available: [],
    completed: [],
    closed: [],
  }
  if (data) {
    for (const [key, val] of Object.entries(data.dynamic_quests)) {
      const q = val as Record<string, unknown>
      grouped[questStatus(q)].push([key, q])
    }
  }

  const invites: NpcInvite[] = data?.pending_npc_invites ?? []
  const chatter: NpcInvite[] = data?.ambient_chatter ?? []

  const chapters = data ? Object.entries(data.chapter_completion) : []
  const hasContent =
    chapters.length > 0 ||
    invites.length > 0 ||
    chatter.length > 0 ||
    Boolean(data && Object.values(grouped).some((g) => g.length > 0))

  return (
    <div className="fixed inset-0 z-20 bg-black/80 backdrop-blur-[2px] flex items-center justify-center">
      <div className="panel-ornate texture-noise w-96 max-h-[80vh] overflow-y-auto">
        <div className="panel-header flex items-center justify-between">
          <h2 className="panel-title">任务日志</h2>
          <button
            onClick={close}
            className="text-parchment-500 hover:text-gold-400 transition-colors text-lg leading-none"
          >
            ×
          </button>
        </div>

        {error ? (
          <p className="p-5 text-red-400 text-sm">{error}</p>
        ) : !data ? (
          <p className="p-5 text-parchment-500 text-sm text-center">加载中…</p>
        ) : (
          <div className="p-5 space-y-4">
            {/* NPC 邀约（可折叠） */}
            {invites.length > 0 && (
              <div>
                <button
                  onClick={() => toggleCollapse('section_invites')}
                  className="w-full flex items-center justify-between mb-1.5 group"
                >
                  <p className="font-display text-xs text-gold-400/70 tracking-wider uppercase">
                    💬 有人想找你 ({invites.length})
                  </p>
                  <span className="text-parchment-500 text-xs group-hover:text-gold-400 transition-colors">
                    {collapsed['section_invites'] ? '▸' : '▾'}
                  </span>
                </button>
                <hr className="divider-subtle" />
                {!collapsed['section_invites'] && invites.map((inv, i) => (
                  <div key={i} className="mb-1.5 mt-1.5 flex items-start gap-2">
                    <span className="text-gold-300 text-sm font-display flex-shrink-0">{inv.npc_name}</span>
                    {inv.topic && (
                      <span className="text-parchment-400 text-xs italic leading-relaxed">— {inv.topic}</span>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* 街头巷议（默认折叠） */}
            {chatter.length > 0 && (
              <div>
                <button
                  onClick={() => toggleCollapse('section_chatter')}
                  className="w-full flex items-center justify-between mb-1.5 group"
                >
                  <p className="font-display text-xs text-gold-400/70 tracking-wider uppercase">
                    🗣 街头巷议 ({chatter.length})
                  </p>
                  <span className="text-parchment-500 text-xs group-hover:text-gold-400 transition-colors">
                    {(collapsed['section_chatter'] !== false) ? '▸' : '▾'}
                  </span>
                </button>
                <hr className="divider-subtle" />
                {collapsed['section_chatter'] === false && chatter.map((ch, i) => (
                  <div key={i} className="mb-1 mt-1 flex items-start gap-2">
                    <span className="text-parchment-400 text-xs font-display flex-shrink-0">{ch.npc_name}</span>
                    {ch.topic && (
                      <span className="text-parchment-500 text-xs italic leading-relaxed">— {ch.topic}</span>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* 主线进度 */}
            {chapters.length > 0 && (
              <div>
                <p className="font-display text-xs text-gold-400/70 tracking-wider uppercase mb-1.5">主线进度</p>
                <hr className="divider-subtle" />
                {chapters.map(([ch, pct]) => (
                  <div key={ch} className="mb-1.5 mt-1.5">
                    <div className="flex justify-between text-xs text-parchment-400 mb-0.5">
                      <span>{ch}</span>
                      <span>{Math.round((pct as number) * 100)}%</span>
                    </div>
                    <div className="h-1.5 bg-gray-700 rounded-full overflow-hidden">
                      <div
                        className="h-full bg-gold-500 rounded-full"
                        style={{ width: `${Math.round((pct as number) * 100)}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* 任务列表 */}
            {(['active', 'available', 'completed', 'closed'] as const).map((status) => {
              const quests = grouped[status]
              if (quests.length === 0) return null
              const sectionCollapsed = status === 'completed' || status === 'closed'
                ? collapsed[`section_${status}`] !== false  // 默认折叠
                : collapsed[`section_${status}`] === true   // 默认展开
              return (
                <div key={status}>
                  <button
                    onClick={() => toggleCollapse(`section_${status}`)}
                    className="w-full flex items-center justify-between mb-1.5 group"
                  >
                    <p className="font-display text-xs text-gold-400/70 tracking-wider uppercase">
                      {STATUS_LABELS[status]} ({quests.length})
                    </p>
                    <span className="text-parchment-500 text-xs group-hover:text-gold-400 transition-colors">
                      {sectionCollapsed ? '▸' : '▾'}
                    </span>
                  </button>
                  <hr className="divider-subtle" />
                  {!sectionCollapsed && quests.map(([key, q]) => {
                    const summary = questSummary(q)
                    const objectives = questObjectives(q)
                    const currentStep = questCurrentStep(q)
                    const hints = questHints(q)
                    const completedCount = objectives.filter((o) => o.completed).length
                    const questCollapsed = collapsed[key] === true

                    return (
                      <div key={key} className="mb-3 mt-1.5">
                        {/* 标题（可折叠） */}
                        <button
                          onClick={() => toggleCollapse(key)}
                          className="w-full text-left"
                        >
                          <p className="font-display text-gold-300 text-sm flex items-center gap-1.5">
                            <span>{STATUS_ICONS[status]}</span>
                            <span className="flex-1">{typeof q.title === 'string' && q.title ? q.title : key}</span>
                            <span className="badge-fantasy ml-auto text-xs">{STATUS_LABELS[status]}</span>
                            <span className="text-parchment-500 text-xs ml-1">{questCollapsed ? '▸' : '▾'}</span>
                          </p>
                        </button>

                        {!questCollapsed && (
                          <div className="mt-1">
                            {/* 摘要 */}
                            {summary && (
                              <p className="text-parchment-300 text-sm ml-5 border-l-2 border-gold-700/30 pl-2">
                                {summary}
                              </p>
                            )}

                            {/* 当前步骤 */}
                            {currentStep && (
                              <div className="ml-5 mt-1.5 flex items-start gap-1.5 bg-gold-900/20 rounded px-2 py-1.5">
                                <span className="text-gold-400 text-xs mt-px">▸</span>
                                <p className="text-parchment-200 text-xs">{currentStep}</p>
                              </div>
                            )}

                            {/* 目标 */}
                            {objectives.length > 0 && (
                              <div className="ml-5 mt-2 space-y-1">
                                <p className="text-xs text-gold-400/60 mb-1">
                                  目标 ({completedCount}/{objectives.length})
                                </p>
                                {objectives.map((obj, i) => (
                                  <div key={i} className="flex items-start gap-1.5">
                                    <span className={`text-xs mt-px ${obj.completed ? 'text-green-400' : 'text-parchment-600'}`}>
                                      {obj.completed ? '✓' : '○'}
                                    </span>
                                    <span className={`text-xs ${obj.completed ? 'text-parchment-500 line-through' : 'text-parchment-200'}`}>
                                      {obj.description}
                                    </span>
                                  </div>
                                ))}
                              </div>
                            )}

                            {/* 提示 */}
                            {hints.length > 0 && (
                              <div className="ml-5 mt-1.5 space-y-0.5">
                                {hints.map((hint, i) => (
                                  <p key={i} className="text-xs text-parchment-500 italic flex items-start gap-1">
                                    <span className="text-gold-600 mt-px">💡</span>
                                    {hint}
                                  </p>
                                ))}
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )
            })}

            {!hasContent && (
              <p className="text-parchment-500 text-sm text-center">暂无任务记录</p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
