import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useOverlayStore } from '../../stores/overlayStore'
import { usePlayerStore } from '../../stores/playerStore'
import { useSessionStore } from '../../stores/sessionStore'
import { saveSession } from '../../lib/api'

export default function MenuOverlay() {
  const overlay = useOverlayStore()
  const { worldId, sessionId, clearSession } = useSessionStore()
  const asiAvailable = usePlayerStore((s) => s.asiAvailable)
  const navigate = useNavigate()
  const [saveMsg, setSaveMsg] = useState<string | null>(null)

  const handleSave = async () => {
    if (!worldId || !sessionId) return
    try {
      await saveSession(worldId, sessionId)
      setSaveMsg('✓ 已保存')
      setTimeout(() => setSaveMsg(null), 2000)
    } catch {
      setSaveMsg('保存失败')
      setTimeout(() => setSaveMsg(null), 2000)
    }
  }

  const handleReturnHome = () => {
    clearSession()
    navigate('/')
  }

  const menuItems = [
    { label: '继续游戏', action: overlay.close, style: '!text-gold-300' },
    {
      label: '📊 角色面板',
      action: () => overlay.open('character'),
      style: '',
      badge: asiAvailable ? 'ASI' : null,
    },
    { label: '👥 队伍', action: () => overlay.open('party'), style: '' },
    { label: '🎒 背包', action: () => overlay.open('inventory'), style: '' },
    { label: '🗺 地图', action: () => overlay.open('map'), style: '' },
    { label: '📜 任务日志', action: () => overlay.open('quests'), style: '' },
  ]

  return (
    <div className="fixed inset-0 z-20 bg-black/80 backdrop-blur-[2px] flex items-center justify-center">
      <div className="panel-ornate texture-noise overflow-hidden min-w-56 w-64">
        <div className="panel-header">
          <h2 className="panel-title text-center">菜单</h2>
        </div>
        <div className="flex flex-col">
          {menuItems.map(({ label, action, style, badge }) => (
            <button
              key={label}
              onClick={action}
              className={`btn-fantasy w-full text-left flex items-center justify-between rounded-none border-x-0 border-t-0 ${style}`}
            >
              <span>{label}</span>
              {badge && (
                <span className="badge-fantasy">{badge}</span>
              )}
            </button>
          ))}
          <hr className="divider-subtle" />
          <button
            onClick={handleSave}
            className="btn-fantasy w-full text-left rounded-none border-x-0 border-t-0"
          >
            💾 保存游戏
            {saveMsg && <span className="text-gold-400 text-sm ml-2">{saveMsg}</span>}
          </button>
          <button
            onClick={handleReturnHome}
            className="btn-fantasy w-full text-left rounded-none border-x-0 border-t-0 !text-red-400/80 hover:!text-red-300"
          >
            🚪 返回标题
          </button>
        </div>
      </div>
    </div>
  )
}
