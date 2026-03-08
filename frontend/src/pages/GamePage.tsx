import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { audio } from '../lib/audio'
import { getCharacter, resumeSession } from '../lib/api'
import { useDialogueStore } from '../stores/dialogueStore'
import { usePlayerStore } from '../stores/playerStore'
import { useSessionStore } from '../stores/sessionStore'
import { useSceneStore } from '../stores/sceneStore'
import { useOverlayStore } from '../stores/overlayStore'
import { useOptionStore } from '../stores/optionStore'
import { usePartyStore } from '../stores/partyStore'
import { useGameStream } from '../hooks/useGameStream'
import SceneBackground from '../game/SceneBackground'
import PortraitLayer from '../game/PortraitLayer'
import DialogueArea from '../game/DialogueArea'
import NotificationLayer from '../game/NotificationLayer'
import PlayerHud from '../game/PlayerHud'
import SceneTransitionOverlay from '../game/SceneTransitionOverlay'
import DiceRollOverlay from '../game/combat/DiceRollOverlay'
import type { OverviewHandlers } from '../stores/optionStore'
import type { InteractRequest, NavigateRequest, StructuredActionRequest } from '../types/api'

// 懒加载：覆盖层 + 战斗层（按需分包，减小初始 bundle）
const LogOverlay        = lazy(() => import('../game/overlays/LogOverlay'))
const MenuOverlay       = lazy(() => import('../game/overlays/MenuOverlay'))
const CharacterPanel    = lazy(() => import('../game/overlays/CharacterPanel'))
const MapPanel          = lazy(() => import('../game/overlays/MapPanel'))
const QuestPanel        = lazy(() => import('../game/overlays/QuestPanel'))
const InventoryPanel    = lazy(() => import('../game/overlays/InventoryPanel'))
const ShopOverlay       = lazy(() => import('../game/overlays/ShopOverlay'))
const ItemDetailOverlay = lazy(() => import('../game/overlays/ItemDetailOverlay'))
const PartyPanel        = lazy(() => import('../game/overlays/PartyPanel'))
const ChatInviteModal   = lazy(() => import('../game/overlays/ChatInviteModal'))
const BoardOverlay      = lazy(() => import('../game/overlays/BoardOverlay'))
const CombatLayer       = lazy(() => import('../game/combat/CombatLayer'))
const EncounterPanel    = lazy(() => import('../game/combat/EncounterPanel'))

export default function GamePage() {
  const { worldId: routeWorldId, sid: routeSessionId } = useParams<{ worldId: string; sid: string }>()
  const navigate = useNavigate()
  const {
    worldId: storedWorldId,
    sessionId: storedSessionId,
    phase,
    setSession,
    setPhase,
  } = useSessionStore()
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

  // ── OverviewHandlers 循环依赖解决 ──────────────────────────────────────────
  // sendNavigate/sendInteract 来自 useGameStream，但 useGameStream 需要 handlers，
  // 用 ref 作为间接层打破循环。
  const sendRef = useRef<{
    sendInteract: (req: InteractRequest) => void
    sendNavigate: (req: NavigateRequest) => void
    sendAction: (req: StructuredActionRequest) => void
  }>({
    sendInteract: () => {},
    sendNavigate: () => {},
    sendAction: () => {},
  })
  const sendOpeningRef = useRef<() => void>(() => {})
  const openingRequestRef = useRef<string | null>(null)

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
    onBrowseBoard: (boardId) =>
      sendRef.current.sendAction({
        action_type: 'browse_board',
        params: { board_id: boardId },
      }),
    onLeaveSubLocation: () =>
      sendRef.current.sendNavigate({ action: 'leave_sub_location' }),
    onRestShort: () =>
      sendRef.current.sendAction({ action_type: 'rest_short', params: {} }),
    onRestLong: () =>
      sendRef.current.sendAction({ action_type: 'rest_long', params: {} }),
    onSetCamp: () => {
      const { currentArea, currentLocation } = useSceneStore.getState()
      if (!currentArea) return
      sendRef.current.sendAction({
        action_type: 'set_camp',
        params: currentLocation
          ? { area_id: currentArea, location_id: currentLocation }
          : { area_id: currentArea },
      })
    },
    onNightWatch: () => {
      const { currentArea } = useSceneStore.getState()
      if (!currentArea) return
      sendRef.current.sendAction({
        action_type: 'night_watch',
        params: { area_id: currentArea },
      })
    },
  }).current

  const {
    sendInteract,
    sendNavigate,
    sendAction,
    sendPrivateChat,
    sendCompanionRecruit,
    sendCompanionDismiss,
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
  sendRef.current.sendAction = sendAction

  useEffect(() => {
    sendOpeningRef.current = sendOpening
  }, [sendOpening])

  useEffect(() => {
    if (!worldId || !sessionId || phase !== 'opening_ready' || initError) {
      openingRequestRef.current = null
    }
  }, [worldId, sessionId, phase, initError])

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
    const optionStore = useOptionStore.getState()
    const dialogueStore = useDialogueStore.getState()
    const sessionStore = useSessionStore.getState()

    const hydrateOverview = (overview: Parameters<typeof updateFromOverview>[0]) => {
      updateFromOverview(overview)
      optionStore.buildFromOverview(overview, overviewHandlers)
    }

    const startOpeningFlow = () => {
      const sessionKey = `${worldId}:${sessionId}`
      if (openingRequestRef.current === sessionKey) {
        return
      }
      openingRequestRef.current = sessionKey
      setInitError(null)
      optionStore.clearOptions()
      dialogueStore.resetMessages()
      clearPortraits()
      setOpeningInProgress(true)
      sendOpeningRef.current()
    }

    const bootstrap = sessionStore.consumeResumeBootstrap(worldId, sessionId)
    if (bootstrap) {
      dialogueStore.resetMessages()
      updatePlayer({ phase: bootstrap.phase, player: bootstrap.player })
      setPhase(bootstrap.phase)
      if (bootstrap.party?.members) {
        usePartyStore.getState().initFromSnapshot(bootstrap.party)
      }
      usePartyStore.getState().enrichFromPresentNpcs(bootstrap.scene.present_npcs)
      if (bootstrap.phase === 'opening_ready') {
        startOpeningFlow()
        return () => {
          cancelled = true
        }
      }
      hydrateOverview(bootstrap.scene)
      if (bootstrap.resume_narration?.trim()) {
        dialogueStore.addMessage({ type: 'gm', content: bootstrap.resume_narration.trim() })
      }
      return () => {
        cancelled = true
      }
    }

    const loadCharacter = getCharacter(worldId, sessionId)
      .then((panel) => {
        if (!cancelled) {
          updatePlayer(panel)
          setPhase(panel.phase)
        }
        return panel
      })

    if (phase === 'opening_ready') {
      loadCharacter
        .then(() => {
          if (!cancelled) {
            startOpeningFlow()
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

    resumeSession(worldId, sessionId)
      .then((res) => {
        if (cancelled) return
        if (res.phase === 'opening_ready') {
          startOpeningFlow()
          return
        }
        updatePlayer({ phase: res.phase, player: res.player })
        setPhase(res.phase)
        if (res.party?.members) {
          usePartyStore.getState().initFromSnapshot(res.party)
        }
        usePartyStore.getState().enrichFromPresentNpcs(res.scene.present_npcs)
        dialogueStore.resetMessages()
        hydrateOverview(res.scene)
        if (res.resume_narration?.trim()) {
          dialogueStore.addMessage({ type: 'gm', content: res.resume_narration.trim() })
        }
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
    phase,
    navigate,
    overviewHandlers,
    clearPortraits,
    setOpeningInProgress,
    updateFromOverview,
    updatePlayer,
    setPhase
  ])

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
    if (openingInProgress) {
      return null
    }
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
      case 'item_detail':
        return <ItemDetailOverlay />
      case 'board':
        return <BoardOverlay sendAction={sendAction} />
      case 'party':
        return (
          <PartyPanel
            sendCompanionRecruit={sendCompanionRecruit}
            sendCompanionDismiss={sendCompanionDismiss}
          />
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

      <PlayerHud />

      {/* Layer 1: 角色立绘（战斗模式下隐藏） */}
      {gameMode !== 'combat' && <PortraitLayer />}

      {/* Layer 2: 对话区域（战斗/遭遇模式下隐藏） */}
      {gameMode !== 'combat' && gameMode !== 'encounter' && (
        <DialogueArea
          worldId={worldId!}
          sessionId={sessionId!}
          sendInteract={sendInteract}
          overviewHandlers={overviewHandlers}
        />
      )}

      {/* Layer 3: 浮动通知 */}
      <NotificationLayer />

      <DiceRollOverlay />

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
