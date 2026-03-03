import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useSessionStore } from '../stores/sessionStore'
import { useSceneStore } from '../stores/sceneStore'
import { useOverlayStore } from '../stores/overlayStore'
import { useOptionStore } from '../stores/optionStore'
import { useGameStream } from '../hooks/useGameStream'
import { getScene } from '../lib/api'
import SceneBackground from '../game/SceneBackground'
import DialogueArea from '../game/DialogueArea'
import LogOverlay from '../game/overlays/LogOverlay'
import MenuOverlay from '../game/overlays/MenuOverlay'
import CharacterPanel from '../game/overlays/CharacterPanel'
import MapPanel from '../game/overlays/MapPanel'
import QuestPanel from '../game/overlays/QuestPanel'
import ChatInviteModal from '../game/overlays/ChatInviteModal'
import type { OverviewHandlers } from '../stores/optionStore'
import type { InteractRequest, NavigateRequest } from '../types/api'

export default function GamePage() {
  const navigate = useNavigate()
  const { worldId, sessionId } = useSessionStore()
  const scene = useSceneStore()
  const overlay = useOverlayStore()
  const [initError, setInitError] = useState<string | null>(null)

  // ── OverviewHandlers 循环依赖解决 ──────────────────────────────────────────
  // sendNavigate/sendInteract 来自 useGameStream，但 useGameStream 需要 handlers，
  // 用 ref 作为间接层打破循环。
  const sendRef = useRef<{
    sendInteract: (req: InteractRequest) => void
    sendNavigate: (req: NavigateRequest) => void
  }>({
    sendInteract: () => {},
    sendNavigate: () => {},
  })

  // 稳定的 handlers 对象（useRef.current，整个生命周期不变）
  const overviewHandlers = useRef<OverviewHandlers>({
    onTalkToNpc: (npcId) =>
      sendRef.current.sendInteract({ intent: 'talk', target_kind: 'npc', target_id: npcId }),
    onEnterSubLocation: (locationId) =>
      sendRef.current.sendNavigate({ action: 'enter_sub_location', location_id: locationId }),
    onInteractWith: (interactableId) =>
      sendRef.current.sendInteract({
        intent: 'examine',
        target_kind: 'object',
        target_id: interactableId,
      }),
    onMoveTo: (areaId) =>
      sendRef.current.sendNavigate({ action: 'move_area', area_id: areaId }),
    onLeaveSubLocation: () =>
      sendRef.current.sendNavigate({ action: 'leave_sub_location' }),
  }).current

  const { sendInteract, sendNavigate, sendInput } = useGameStream(overviewHandlers)

  // 每次 render 同步 ref（sendInteract/sendNavigate 是稳定 useCallback）
  sendRef.current.sendInteract = sendInteract
  sendRef.current.sendNavigate = sendNavigate

  // ── 初始场景加载 ───────────────────────────────────────────────────────────
  useEffect(() => {
    if (!worldId || !sessionId) {
      navigate('/')
      return
    }
    getScene(worldId, sessionId)
      .then((overview) => {
        scene.updateFromOverview(overview)
        // 用 getState() 访问 store 方法，避免 useEffect 依赖问题
        useOptionStore.getState().buildFromOverview(overview, overviewHandlers)
      })
      .catch((err: Error) => setInitError(err.message ?? '加载场景失败'))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps — mount-only，worldId/sessionId 在 session 期间稳定

  // ── 错误态 ────────────────────────────────────────────────────────────────
  if (initError) {
    return (
      <div className="min-h-screen bg-gray-950 flex flex-col items-center justify-center gap-4 text-red-400">
        <p>{initError}</p>
        <button
          onClick={() => navigate('/')}
          className="text-gray-400 hover:text-gray-200 text-sm"
        >
          返回首页
        </button>
      </div>
    )
  }

  // ── 覆盖层路由 ────────────────────────────────────────────────────────────
  const renderOverlay = () => {
    switch (overlay.current) {
      case 'log':
        return <LogOverlay />
      case 'menu':
        return <MenuOverlay />
      case 'character':
        return <CharacterPanel />
      case 'map':
        return <MapPanel sendNavigate={sendNavigate} />
      case 'quests':
        return <QuestPanel />
      case 'inventory':
      case 'shop':
      case 'board':
      case 'item_detail':
        return (
          <div className="fixed inset-0 z-20 bg-gray-950/90 flex items-center justify-center">
            <div className="bg-gray-900 border border-gray-700 rounded-xl p-8 text-center">
              <p className="text-gray-400 mb-4">此功能即将推出（P2B）</p>
              <button
                onClick={overlay.close}
                className="text-amber-400 hover:text-amber-300 text-sm"
              >
                关闭
              </button>
            </div>
          </div>
        )
      default:
        return null
    }
  }

  // ── 主渲染 ────────────────────────────────────────────────────────────────
  return (
    <div className="relative w-screen h-screen overflow-hidden bg-gray-950">
      {/* Layer 0: 场景背景 */}
      <SceneBackground />

      {/* Layer 1: 角色立绘 — P3 */}

      {/* Layer 2: 对话区域 */}
      <DialogueArea
        worldId={worldId!}
        sessionId={sessionId!}
        sendInput={sendInput}
      />

      {/* Layer 4: 全屏覆盖层 */}
      {renderOverlay()}

      {/* Layer 5: 模态弹窗 */}
      {overlay.current === 'chat_invite' && <ChatInviteModal />}
    </div>
  )
}
