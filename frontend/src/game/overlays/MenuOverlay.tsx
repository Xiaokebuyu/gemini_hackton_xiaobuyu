import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useOverlayStore } from '../../stores/overlayStore'
import { useSessionStore } from '../../stores/sessionStore'
import { saveSession } from '../../lib/api'

export default function MenuOverlay() {
  const overlay = useOverlayStore()
  const { worldId, sessionId, clearSession } = useSessionStore()
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
    { label: '继续游戏', action: overlay.close, style: 'text-gray-200' },
    { label: '📊 角色面板', action: () => overlay.open('character'), style: 'text-gray-400' },
    { label: '🎒 背包', action: () => overlay.open('inventory'), style: 'text-gray-400' },
    { label: '🗺 地图', action: () => overlay.open('map'), style: 'text-gray-400' },
    { label: '📜 任务日志', action: () => overlay.open('quests'), style: 'text-gray-400' },
  ]

  return (
    <div className="fixed inset-0 z-20 bg-gray-950/90 flex items-center justify-center">
      <div className="bg-gray-900 border border-gray-700 rounded-xl overflow-hidden min-w-56 w-64">
        <div className="border-b border-gray-700 px-6 py-3">
          <h2 className="text-amber-400 font-bold text-center">菜单</h2>
        </div>
        <div className="flex flex-col">
          {menuItems.map(({ label, action, style }) => (
            <button
              key={label}
              onClick={action}
              className={`px-6 py-3 text-left hover:bg-gray-800 transition-colors border-b border-gray-800 ${style}`}
            >
              {label}
            </button>
          ))}
          <button
            onClick={handleSave}
            className="px-6 py-3 text-left hover:bg-gray-800 transition-colors border-b border-gray-800 text-gray-300"
          >
            💾 保存游戏
            {saveMsg && <span className="text-amber-400 text-sm ml-2">{saveMsg}</span>}
          </button>
          <button
            onClick={handleReturnHome}
            className="px-6 py-3 text-left hover:bg-gray-800 transition-colors text-red-400"
          >
            🚪 返回标题
          </button>
        </div>
      </div>
    </div>
  )
}
