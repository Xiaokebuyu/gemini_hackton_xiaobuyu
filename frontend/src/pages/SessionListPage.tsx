import { useEffect, useState, useCallback } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { getSessions, createSession, resumeSession, deleteSession } from '../lib/api'
import { useSessionStore } from '../stores/sessionStore'
import type { SessionSummary } from '../types/api'

export default function SessionListPage() {
  const { worldId } = useParams<{ worldId: string }>()
  const navigate = useNavigate()
  const setSession = useSessionStore((s) => s.setSession)

  const [sessions, setSessions] = useState<SessionSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const loadSessions = useCallback(() => {
    if (!worldId) return
    setError(null)
    getSessions(worldId)
      .then(setSessions)
      .catch((err: Error) => setError(err.message ?? '加载失败'))
  }, [worldId])

  useEffect(() => {
    loadSessions()
  }, [loadSessions])

  const handleNew = async () => {
    if (!worldId) return
    setLoading(true)
    setError(null)
    try {
      const res = await createSession(worldId)
      setSession(worldId, res.session_id, res.phase)
      navigate(`/${worldId}/sessions/${res.session_id}/create`)
    } catch (err: unknown) {
      setError((err as Error).message ?? '创建失败')
    } finally {
      setLoading(false)
    }
  }

  const handleResume = async (sid: string) => {
    if (!worldId) return
    setLoading(true)
    setError(null)
    try {
      const res = await resumeSession(worldId, sid)
      setSession(worldId, res.session_id, res.phase)
      navigate(`/${worldId}/sessions/${sid}/play`)
    } catch (err: unknown) {
      setError((err as Error).message ?? '加载存档失败')
    } finally {
      setLoading(false)
    }
  }

  const handleDelete = async (sid: string) => {
    if (!worldId) return
    setError(null)
    try {
      await deleteSession(worldId, sid)
      setDeletingId(null)
      loadSessions()
    } catch (err: unknown) {
      setError((err as Error).message ?? '删除失败')
    }
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-8">
      <div className="max-w-3xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <button
            onClick={() => navigate('/')}
            className="text-gray-400 hover:text-gray-200 transition-colors text-sm"
          >
            ← 返回
          </button>
          <h1 className="text-2xl font-bold text-amber-400">存档管理</h1>
          <button
            onClick={handleNew}
            disabled={loading}
            className="bg-amber-600 hover:bg-amber-500 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm transition-colors"
          >
            新建游戏
          </button>
        </div>

        {error && <div className="text-red-400 text-sm mb-4">{error}</div>}

        {/* Session list */}
        {sessions === null ? (
          <div className="text-gray-400 text-center py-16">加载中...</div>
        ) : sessions.length === 0 ? (
          <div className="text-gray-500 text-center py-16">
            尚无存档，点击「新建游戏」开始冒险
          </div>
        ) : (
          <div className="space-y-4">
            {sessions.map((session) => (
              <div key={session.session_id} className="bg-gray-800 rounded-lg p-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-baseline gap-2 mb-1">
                      <span className="font-bold text-amber-300 truncate">
                        {session.summary.player_name || '无名冒险者'}
                      </span>
                      <span className="text-gray-400 text-sm flex-shrink-0">
                        {session.summary.player_class} · Lv.{session.summary.level}
                      </span>
                    </div>
                    <div className="text-gray-500 text-xs space-y-0.5">
                      <div>位置：{session.summary.location || '未知'}</div>
                      {session.summary.day != null && (
                        <div>第 {session.summary.day} 天</div>
                      )}
                      {session.summary.play_time_hours != null && (
                        <div>游戏时间：{session.summary.play_time_hours.toFixed(1)}h</div>
                      )}
                    </div>
                  </div>

                  <div className="flex gap-2 flex-shrink-0">
                    <button
                      onClick={() => handleResume(session.session_id)}
                      disabled={loading}
                      className="bg-amber-600 hover:bg-amber-500 disabled:opacity-50 text-white text-sm px-3 py-1.5 rounded transition-colors"
                    >
                      继续
                    </button>
                    {deletingId === session.session_id ? (
                      <>
                        <button
                          onClick={() => handleDelete(session.session_id)}
                          className="bg-red-700 hover:bg-red-600 text-white text-sm px-3 py-1.5 rounded transition-colors"
                        >
                          确认删除
                        </button>
                        <button
                          onClick={() => setDeletingId(null)}
                          className="bg-gray-600 hover:bg-gray-500 text-white text-sm px-3 py-1.5 rounded transition-colors"
                        >
                          取消
                        </button>
                      </>
                    ) : (
                      <button
                        onClick={() => setDeletingId(session.session_id)}
                        className="bg-gray-600 hover:bg-gray-500 text-white text-sm px-3 py-1.5 rounded transition-colors"
                      >
                        删除
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
