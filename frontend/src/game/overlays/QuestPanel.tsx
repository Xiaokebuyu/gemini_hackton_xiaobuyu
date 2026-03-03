import { useEffect, useState } from 'react'
import { useOverlayStore } from '../../stores/overlayStore'
import { useSessionStore } from '../../stores/sessionStore'
import { getQuests } from '../../lib/api'
import type { QuestPanelData } from '../../types/api'

type QuestStatus = 'active' | 'available' | 'completed'

function questStatus(v: Record<string, unknown>): QuestStatus {
  if (v.status === 'completed') return 'completed'
  if (v.status === 'available') return 'available'
  return 'active'
}

const STATUS_ICONS: Record<QuestStatus, string> = {
  active: '⚔',
  available: '📋',
  completed: '✅',
}

const STATUS_LABELS: Record<QuestStatus, string> = {
  active: '进行中',
  available: '可接取',
  completed: '已完成',
}

export default function QuestPanel() {
  const { close } = useOverlayStore()
  const { worldId, sessionId } = useSessionStore()
  const [data, setData] = useState<QuestPanelData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!worldId || !sessionId) return
    getQuests(worldId, sessionId)
      .then(setData)
      .catch((err: Error) => setError(err.message))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const grouped: Record<QuestStatus, Array<[string, Record<string, unknown>]>> = {
    active: [],
    available: [],
    completed: [],
  }
  if (data) {
    for (const [key, val] of Object.entries(data.dynamic_quests)) {
      const q = val as Record<string, unknown>
      grouped[questStatus(q)].push([key, q])
    }
  }

  const chapters = data ? Object.entries(data.chapter_completion) : []
  const hasContent =
    chapters.length > 0 || (data && Object.values(grouped).some((g) => g.length > 0))

  return (
    <div className="fixed inset-0 z-20 bg-gray-950/90 flex items-center justify-center">
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-80 max-h-[80vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-700">
          <h2 className="text-amber-400 font-bold">任务日志</h2>
          <button
            onClick={close}
            className="text-gray-500 hover:text-gray-300 text-lg leading-none"
          >
            ×
          </button>
        </div>

        {error ? (
          <p className="p-5 text-red-400 text-sm">{error}</p>
        ) : !data ? (
          <p className="p-5 text-gray-500 text-sm text-center">加载中…</p>
        ) : (
          <div className="p-5 space-y-4">
            {/* 主线进度 */}
            {chapters.length > 0 && (
              <div>
                <p className="text-gray-500 text-xs mb-1.5">── 主线进度 ──</p>
                {chapters.map(([ch, pct]) => (
                  <div key={ch} className="mb-1.5">
                    <div className="flex justify-between text-xs text-gray-400 mb-0.5">
                      <span>{ch}</span>
                      <span>{Math.round((pct as number) * 100)}%</span>
                    </div>
                    <div className="h-1.5 bg-gray-700 rounded-full overflow-hidden">
                      <div
                        className="h-full bg-amber-500 rounded-full"
                        style={{ width: `${Math.round((pct as number) * 100)}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* 动态任务分组 */}
            {(['active', 'available', 'completed'] as const).map((status) => {
              const quests = grouped[status]
              if (quests.length === 0) return null
              return (
                <div key={status}>
                  <p className="text-gray-500 text-xs mb-1.5">── {STATUS_LABELS[status]} ──</p>
                  {quests.map(([key, q]) => (
                    <div key={key} className="mb-2">
                      <p className="text-gray-200 text-sm">
                        {STATUS_ICONS[status]}{' '}
                        {typeof q.title === 'string' && q.title ? q.title : key}
                      </p>
                      {typeof q.description === 'string' && q.description && (
                        <p className="text-gray-400 text-xs ml-5 mt-0.5">{q.description}</p>
                      )}
                    </div>
                  ))}
                </div>
              )
            })}

            {!hasContent && (
              <p className="text-gray-500 text-sm text-center">暂无任务记录</p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
