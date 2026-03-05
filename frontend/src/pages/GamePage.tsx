import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { audio } from '../lib/audio'
import { getCharacter, getScene } from '../lib/api'
import { usePlayerStore } from '../stores/playerStore'
import { useSessionStore } from '../stores/sessionStore'
import { useSceneStore } from '../stores/sceneStore'
import { useOverlayStore } from '../stores/overlayStore'
import { useOptionStore } from '../stores/optionStore'
import { useGameStream } from '../hooks/useGameStream'
import SceneBackground from '../game/SceneBackground'
import PortraitLayer from '../game/PortraitLayer'
import DialogueArea from '../game/DialogueArea'
import NotificationLayer from '../game/NotificationLayer'
import PlayerHud from '../game/PlayerHud'
import SceneTransitionOverlay from '../game/SceneTransitionOverlay'
import type { OverviewHandlers } from '../stores/optionStore'
import type { InteractRequest, NavigateRequest } from '../types/api'

// 懒加载：覆盖层 + 战斗层（按需分包，减小初始 bundle）
const LogOverlay        = lazy(() => import('../game/overlays/LogOverlay'))
const MenuOverlay       = lazy(() => import('../game/overlays/MenuOverlay'))
const CharacterPanel    = lazy(() => import('../game/overlays/CharacterPanel'))
const MapPanel          = lazy(() => import('../game/overlays/MapPanel'))
const QuestPanel        = lazy(() => import('../game/overlays/QuestPanel'))
const InventoryPanel    = lazy(() => import('../game/overlays/InventoryPanel'))
const ShopOverlay       = lazy(() => import('../game/overlays/ShopOverlay'))
const BoardOverlay      = lazy(() => import('../game/overlays/BoardOverlay'))
const ItemDetailOverlay = lazy(() => import('../game/overlays/ItemDetailOverlay'))
const ChatInviteModal   = lazy(() => import('../game/overlays/ChatInviteModal'))
const CombatLayer       = lazy(() => import('../game/combat/CombatLayer'))
const EncounterPanel    = lazy(() => import('../game/combat/EncounterPanel'))

export default function GamePage() {
  const { worldId: routeWorldId, sid: routeSessionId } = useParams<{ worldId: string; sid: string }>()
  const location = useLocation()
  const navigate = useNavigate()
  const { worldId: storedWorldId, sessionId: storedSessionId, phase, setSession } = useSessionStore()
  const worldId = storedWorldId ?? routeWorldId ?? null
  const sessionId = storedSessionId ?? routeSessionId ?? null
  const gameMode = useSceneStore((s) => s.gameMode)
  const openingInProgress = useSceneStore((s) => s.openingInProgress)
  const clearPortraits = useSceneStore((s) => s.clearPortraits)
  const setOpeningInProgress = useSceneStore((s) => s.setOpeningInProgress)
  const updateFromOverview = useSceneStore((s) => s.updateFromOverview)
  const overlay = useOverlayStore()
  const updatePlayer = usePlayerStore((s) => s.updateFromPanel)
  const [initError, setInitError] = useState<string | null>(null)
  const openingRequested = new URLSearchParams(location.search).get('opening') === '1'

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
  const openingStartedRef = useRef(false)

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

  const {
    sendInteract,
    sendNavigate,
    sendInput,
    sendPrivateChat,
    sendCombatAction,
    sendEncounterAction,
    sendOpening,
  } = useGameStream(overviewHandlers, {
    worldId: worldId ?? undefined,
    sessionId: sessionId ?? undefined,
  })

  // 每次 render 同步 ref（sendInteract/sendNavigate 是稳定 useCallback）
  sendRef.current.sendInteract = sendInteract
  sendRef.current.sendNavigate = sendNavigate

  // ── BGM 随 gameMode 切换 ────────────────────────────────────────────────────
  useEffect(() => {
    const key =
      gameMode === 'combat' ? 'combat' :
      gameMode === 'encounter' ? 'encounter' :
      gameMode === 'private_chat' ? 'private_chat' : 'explore'
    audio.setBgm(key)
  }, [gameMode])

  // ── 初始场景加载 ───────────────────────────────────────────────────────────
  useEffect(() => {
    if (routeWorldId && routeSessionId && (storedWorldId !== routeWorldId || storedSessionId !== routeSessionId)) {
      setSession(routeWorldId, routeSessionId, phase ?? 'active')
    }
  }, [routeWorldId, routeSessionId, storedWorldId, storedSessionId, phase, setSession])

  useEffect(() => {
    if (!worldId || !sessionId) {
      navigate('/')
      return
    }
    let cancelled = false

    const loadCharacter = getCharacter(worldId, sessionId)
      .then((panel) => {
        if (!cancelled) {
          updatePlayer(panel)
        }
      })

    if (openingRequested) {
      setInitError(null)
      useOptionStore.getState().clearOptions()
      clearPortraits()
      setOpeningInProgress(true)
      loadCharacter
        .then(() => {
          if (!cancelled) {
            sendOpening()
          }
        })
        .catch((err: Error) => {
          if (!cancelled) {
            setInitError(err.message ?? '加载角色失败')
            setOpeningInProgress(false)
          }
        })
      return () => {
        cancelled = true
      }
    }

    Promise.all([getScene(worldId, sessionId), loadCharacter])
      .then(([overview]) => {
        if (cancelled) {
          return
        }
        updateFromOverview(overview)
        useOptionStore.getState().buildFromOverview(overview, overviewHandlers)
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setInitError(err.message ?? '加载场景失败')
        }
      })

    return () => {
      cancelled = true
    }
  }, [
    worldId,
    sessionId,
    openingRequested,
    navigate,
    overviewHandlers,
    clearPortraits,
    setOpeningInProgress,
    updateFromOverview,
    updatePlayer,
  ])

  useEffect(() => {
    if (!openingRequested) {
      openingStartedRef.current = false
      return
    }
    if (openingInProgress) {
      openingStartedRef.current = true
    }
  }, [openingRequested, openingInProgress])

  useEffect(() => {
    if (!openingRequested || openingInProgress || !openingStartedRef.current) {
      return
    }
    if (!worldId || !sessionId) {
      return
    }
    openingStartedRef.current = false
    navigate(`/${worldId}/sessions/${sessionId}/play`, { replace: true })
  }, [openingRequested, openingInProgress, worldId, sessionId, navigate])

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
        return <InventoryPanel />
      case 'shop':
        return <ShopOverlay sendInteract={sendInteract} />
      case 'board':
        return <BoardOverlay sendInteract={sendInteract} />
      case 'item_detail':
        return <ItemDetailOverlay />
      default:
        return null
    }
  }

  // ── 主渲染 ────────────────────────────────────────────────────────────────
  return (
    <div className="relative w-screen h-screen overflow-hidden bg-gray-950">
      {/* Layer 0: 场景背景 */}
      <SceneBackground />

      <PlayerHud />

      {/* Layer 1: 角色立绘（战斗模式下隐藏） */}
      {gameMode !== 'combat' && <PortraitLayer />}

      {/* Layer 2: 对话区域（战斗/遭遇模式下隐藏） */}
      {gameMode !== 'combat' && gameMode !== 'encounter' && (
        <DialogueArea
          worldId={worldId!}
          sessionId={sessionId!}
          sendInput={sendInput}
          sendInteract={sendInteract}
          overviewHandlers={overviewHandlers}
        />
      )}

      {/* Layer 3: 浮动通知 */}
      <NotificationLayer />

      {/* 懒加载层：遭遇/战斗/覆盖层/弹窗（Suspense fallback=null，组件缺失时静默） */}
      <Suspense fallback={null}>
        {/* Layer: 遭遇发现（encounter 模式） */}
        {gameMode === 'encounter' && (
          <EncounterPanel onEncounterAction={sendEncounterAction} />
        )}

        {/* Layer: 战斗界面（combat 模式） */}
        {gameMode === 'combat' && (
          <CombatLayer onCombatAction={sendCombatAction} onEncounterAction={sendEncounterAction} />
        )}

        {/* Layer 4: 全屏覆盖层 */}
        {renderOverlay()}

        {/* Layer 5: 模态弹窗 */}
        {overlay.current === 'chat_invite' && (
          <ChatInviteModal sendPrivateChat={sendPrivateChat} />
        )}
      </Suspense>

      {/* 转场覆盖（z-[25]，覆盖所有层） */}
      <SceneTransitionOverlay />
    </div>
  )
}
