// SSE 事件分发 hook，职责：连接 sse-client ↔ stores
// 唯一有网络副作用的层，各 store 自身不发请求
import { useCallback, useEffect, useRef } from 'react'
import { audio } from '../lib/audio'
import { streamSSE } from '../lib/sse-client'
import { urls } from '../lib/api'
import { useSessionStore } from '../stores/sessionStore'
import { useDialogueStore } from '../stores/dialogueStore'
import { useOptionStore } from '../stores/optionStore'
import { useSceneStore } from '../stores/sceneStore'
import { useOverlayStore } from '../stores/overlayStore'
import { useStreamStore } from '../stores/streamStore'
import { useNotificationStore } from '../stores/notificationStore'
import { usePlayerStore } from '../stores/playerStore'
import { usePartyStore } from '../stores/partyStore'
import { useVnStore } from '../stores/vnStore'
import type {
  CharacterEnterData,
  GmCommentData,
  GmNarrationData,
  NpcResponseData,
  NpcEmoteData,
  TeammateResponseData,
  TextChunkData,
  DialogueOptionsData,
  DialogueOptionItem,
  DialogueOptionsUnavailableData,
  CompanionRecruitedData,
  CompanionDismissedData,
  ActionResultData,
  SceneChangeData,
  TimeAdvancedData,
  RelationshipStageChangedData,
  NpcWantsToChatData,
  StreamErrorData,
  DiscoveryRevealData,
  EncounterSpottedData,
  StealthResultData,
  DiceRollData,
  CombatStartData,
  CombatUpdateData,
  CombatEndData,
  StatusUpdateData,
  EffectAppliedData,
  EffectRemovedData,
  LootDisplayData,
  TalkSnapshotData,
  MilestoneUnlockedData,
  MilestoneFailedData,
  InputRejectedData,
  InteractionRejectedData,
  NpcErrorData,
  HookErrorData,
  QuestBriefData,
  QuestProgressData,
  QuestLocationData,
  QuestRequirementsData,
  QuestRewardData,
  CampfireDialogueData,
  EventStateChangedData,
  HiddenObjectRevealedData,
  TrapDetectedData,
  GenericErrorEventData,
  AiProcessingData,
  PlayerLevelUpData,
  QuestCreatedData,
} from '../types/sse'
import type { GameMode, GameOption, LocationOverview } from '../types/game'
import type {
  InteractRequest,
  NavigateRequest,
  StructuredActionRequest,
  PrivateChatRequest,
  TextInputRequest,
} from '../types/api'
import type { OverviewHandlers } from '../stores/optionStore'
import { useCombatStore } from '../stores/combatStore'

function cast<T>(data: unknown): T {
  return data as T
}

function formatActionSuccess(actionType: string): string {
  const labels: Record<string, string> = {
    buy: '购买成功',
    sell: '出售成功',
    donate: '奉献完成',
    accept: '任务接受',
    accept_quest: '任务接受',
    complete: '任务完成',
    retire: '任务撤销',
    equip: '装备完成',
    drop: '物品丢弃',
    rest_short: '休息完成',
    rest_long: '长休完成',
    set_camp: '扎营完成',
    night_watch: '值守完成',
    enter: '进入成功',
    surprise_attack: '突袭成功',
    sneak_through: '潜行通过',
    retreat: '已撤退',
    browse_board: '查看委托板',
    board_accept_quest: '接受委托',
    board_complete_quest: '完成委托',
    board_retire_quest: '撤销委托',
  }
  return labels[actionType] ?? '操作成功'
}

function actionExecuted(data: ActionResultData): boolean {
  return data.executed
}

function actionOutcomePassed(data: ActionResultData): boolean | null {
  const outcome = data.outcome
  if (!outcome) return null
  if (typeof outcome.passed === 'boolean') return outcome.passed
  if (typeof outcome.winner === 'string') return outcome.winner === 'actor'
  return null
}

function questName(title: string | undefined, questId: string): string {
  return title?.trim() || questId
}

function canFallbackPrivateDialogue(item: DialogueOptionItem): boolean {
  const intent = String(item.intent ?? '').trim()
  if (item.check?.skill && typeof item.check?.dc === 'number') return true
  if (!intent) return true
  return [
    'talk',
    'greet',
    'ask',
    'chat',
    'ask_quest',
    'ask_progress',
    'ask_location',
    'ask_requirements',
    'ask_reward',
  ].includes(intent)
}

type GameDebugApi = {
  runFunctionalOption: (item: DialogueOptionItem) => void
}

function readFunctionalParam(item: DialogueOptionItem, key: string): string | null {
  const raw = item.functional?.params?.[key]
  if (typeof raw !== 'string') return null
  const normalized = raw.trim()
  return normalized || null
}

function formatQuestBriefMessage(data: QuestBriefData): string {
  const name = questName(data.quest.title, data.quest.quest_id)
  return `任务线索：${name}。${data.quest.summary}`
}

function formatQuestProgressMessage(data: QuestProgressData): string {
  const name = questName(data.quest.title, data.quest.quest_id)
  return `任务进展：${name}（${data.quest.status}）。${data.quest.summary}`
}

function formatQuestLocationMessage(data: QuestLocationData): string {
  if (!data.quest.location_known) {
    return `任务地点：${data.quest.quest_id} 的地点仍未知`
  }
  const area = data.quest.area_id ?? '未知区域'
  const location = data.quest.location_id ? ` / ${data.quest.location_id}` : ''
  return `任务地点：${data.quest.quest_id} 位于 ${area}${location}`
}

function formatQuestRequirementsMessage(data: QuestRequirementsData): string {
  if (!data.quest.requirements_known) {
    return `任务要求：${data.quest.quest_id} 的条件仍未知`
  }
  if (data.quest.requirements.length > 0) {
    return `任务要求：${data.quest.requirements.join('；')}`
  }
  if (data.quest.gating_reason?.trim()) {
    return `任务要求：${data.quest.gating_reason.trim()}`
  }
  return `任务要求：${data.quest.quest_id} 当前没有额外条件`
}

function formatQuestRewardMessage(data: QuestRewardData): string {
  if (!data.quest.reward_known) {
    return `任务奖励：${data.quest.quest_id} 的奖励仍未知`
  }
  const summary = data.quest.reward_summary?.trim()
  if (summary) {
    return `任务奖励：${summary}`
  }

  const parts: string[] = []
  if (typeof data.quest.gold === 'number' && data.quest.gold > 0) {
    parts.push(`${data.quest.gold} 金币`)
  }
  if (data.quest.items.length > 0) {
    parts.push(
      data.quest.items.map((item) => `${item.item_id} x${item.count}`).join('、')
    )
  }
  const detail = parts.join('，') || '奖励已揭示'
  return `任务奖励：${detail}`
}

function formatNpcError(prefix: string, data: NpcErrorData): string {
  const detail = data.code ?? data.error ?? 'unknown_error'
  return `${prefix}：${data.npc_id} (${detail})`
}

function formatHookError(prefix: string, data: HookErrorData): string {
  return `${prefix}：${data.hook} - ${data.message}`
}

function formatPeriod(period: string): string {
  const periodMap: Record<string, string> = {
    dawn: '黎明',
    morning: '清晨',
    noon: '正午',
    afternoon: '午后',
    dusk: '黄昏',
    evening: '傍晚',
    night: '深夜',
    midnight: '午夜',
  }
  return periodMap[period] ?? period
}

function formatTimeAdvancedMessage(data: TimeAdvancedData): string {
  const period = formatPeriod(data.period)
  return `第 ${data.day} 天 · 第 ${data.slot} 格 · ${period}`
}

function formatEventStateChangedMessage(data: EventStateChangedData): string {
  const title = data.title?.trim()
  if (title) {
    return `${title}：${data.from_state} → ${data.to_state}`
  }
  return `世界事件状态变化：${data.to_state}`
}

function formatGenericError(prefix: string, data: GenericErrorEventData): string {
  const detail = (data.message ?? data.error ?? data.code ?? 'unknown_error').toString().trim() || 'unknown_error'
  return `${prefix}：${detail}`
}

function formatCompanionMessage(action: 'join' | 'leave', npcId: string, reason?: string): string {
  const suffix = reason?.trim() ? `（${reason.trim()}）` : ''
  return action === 'join'
    ? `${npcId} 加入了队伍${suffix}`
    : `${npcId} 离开了队伍${suffix}`
}

const OPENING_TEXT_STEP_MS = 22
const OPENING_TEXT_HOLD_MS = 260
const OPENING_SCENE_DELAY_MS = 900
const OPENING_PORTRAIT_DELAY_MS = 400
const OPENING_STATUS_DELAY_MS = 240
const OPENING_OPTIONS_DELAY_MS = 180

function sleep(ms: number) {
  return new Promise<void>((resolve) => window.setTimeout(resolve, ms))
}

function revealChunks(text: string): string[] {
  return Array.from(text ?? '')
}

function isLocalDevEnvironment(): boolean {
  if (typeof window === 'undefined') return false
  return window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
}

interface SessionOverride {
  worldId?: string
  sessionId?: string
}

export function useGameStream(overviewHandlers: OverviewHandlers, sessionOverride?: SessionOverride) {
  const { worldId: storeWorldId, sessionId: storeSessionId } = useSessionStore()
  const worldId = sessionOverride?.worldId ?? storeWorldId
  const sessionId = sessionOverride?.sessionId ?? storeSessionId
  const dialogue = useDialogueStore()
  const options = useOptionStore()
  const scene = useSceneStore()
  const overlay = useOverlayStore()
  const stream = useStreamStore()
  const combat = useCombatStore()

  // AbortController：每次发新请求前 abort 上一个流
  const abortRef = useRef<AbortController | null>(null)
  const openingQueueRef = useRef<Promise<void>>(Promise.resolve())
  // QF-3: buffer chat invites received while in dialogue/private_chat mode
  const pendingChatInvitesRef = useRef<NpcWantsToChatData[]>([])
  const runFunctionalOptionRef = useRef<(item: DialogueOptionItem) => boolean>(() => false)

  const handleEvent = useCallback((event: { event: string; data: unknown }) => {
    const addSystemMessage = (content: string) => {
      dialogue.addMessage({ type: 'system', content })
    }

    const addErrorMessage = (content: string) => {
      dialogue.clearPendingStream()
      dialogue.addMessage({ type: 'system', content })
      useNotificationStore.getState().add(content, 'error')
      audio.playError()
    }

    const resolveDialogueMode = () => {
      const sceneState = useSceneStore.getState()
      const nextMode: GameMode = sceneState.gameMode === 'private_chat' ? 'private_chat' : 'dialogue'
      scene.setGameMode(nextMode)
    }

    // True when NPC dialogue is active (VN mode renders instead of scrollable chat)
    const isVnMode = () => {
      const { activeNpcId, gameMode } = useSceneStore.getState()
      return !!activeNpcId || gameMode === 'private_chat'
    }

    const leaveDialogue = () => {
      const sceneState = useSceneStore.getState()
      const optionState = useOptionStore.getState()

      // Drain VN queue → scrollable log before leaving
      const vnState = useVnStore.getState()
      const vnEntries = vnState.drainToLog()
      if (vnEntries.length > 0) {
        useDialogueStore.getState().batchAddToLog(vnEntries)
      }
      vnState.reset()

      scene.setActiveNpc(null)
      scene.setVnSpeaker(null)
      scene.setGameMode('explore')
      optionState.clearOptions()
      if (sceneState.lastOverview) {
        optionState.buildFromOverview(sceneState.lastOverview, overviewHandlers)
      }
    }

    const queueOpeningStep = (step: () => Promise<void>) => {
      openingQueueRef.current = openingQueueRef.current
        .then(step)
        .catch((err) => {
          console.error('opening choreography failed', err)
        })
    }

    const revealOpeningMessage = async (
      type: 'gm' | 'gm_comment',
      content: string,
      tone?: string,
    ) => {
      const trimmed = content.trim()
      if (!trimmed) return
      dialogue.clearPendingStream()
      for (const chunk of revealChunks(trimmed)) {
        dialogue.appendStreamChunk(chunk)
        await sleep(OPENING_TEXT_STEP_MS)
      }
      dialogue.resolveStreamMessage({ type, content: trimmed, tone })
      await sleep(OPENING_TEXT_HOLD_MS)
    }

    const applyDialogueOptions = (d: DialogueOptionsData) => {
      if (d.npc_id) {
        scene.setActiveNpc(d.npc_id)
        resolveDialogueMode()
      }
      options.setFromDialogueOptions(d.options, (item) => {
        const label = item.label ?? item.text ?? String(item.id ?? '').trim()
        if (item.dispatch) {
          if (item.dispatch.kind === 'navigate') {
            void sendNavigate(item.dispatch.payload as unknown as NavigateRequest)
            return
          }
          if (item.dispatch.kind === 'interact') {
            void sendInteract(item.dispatch.payload as unknown as InteractRequest)
            return
          }
          if (item.dispatch.kind === 'input') {
            void sendInput(item.dispatch.payload as unknown as TextInputRequest)
            return
          }
          if (item.dispatch.kind === 'action') {
            void sendAction(item.dispatch.payload as unknown as StructuredActionRequest)
            return
          }
          if (item.dispatch.kind === 'local') {
            const payload = item.dispatch.payload as Record<string, unknown>
            if (payload.action === 'leave_dialogue') {
              leaveDialogue()
              return
            }
          }
        }

        if (runFunctionalOptionRef.current(item)) {
          return
        }

        const activeNpcId = useSceneStore.getState().activeNpcId
        const gameMode = useSceneStore.getState().gameMode
        if (gameMode === 'private_chat' && !canFallbackPrivateDialogue(item)) {
          return
        }
        if (activeNpcId && label) {
          const payload: InteractRequest = {
            scope: gameMode === 'private_chat' ? 'private' : 'public',
            intent: 'talk',
            target_kind: 'npc',
            target_id: activeNpcId,
            message: label,
          }
          if (item.check?.skill && item.check?.dc) {
            payload.check_skill = item.check.skill
            payload.check_dc = item.check.dc
          }
          void sendInteract(payload)
        }
      })
    }

    const applySceneChange = (d: SceneChangeData) => {
      scene.transitionTo(d)
      scene.clearPortraits()
      dialogue.clearForSceneChange()
      if (d.background === 'private') {
        scene.setGameMode('private_chat')
      } else {
        scene.setActiveNpc(null)
        scene.setGameMode('explore')
      }
      audio.playTransition()
    }

    const applyLocationOverview = (d: LocationOverview) => {
      scene.updateFromOverview(d)
      const sceneState = useSceneStore.getState()
      if (!useOptionStore.getState().hasDialogueOptions && !sceneState.activeNpcId) {
        options.buildFromOverview(d, overviewHandlers)
      }
      usePartyStore.getState().enrichFromPresentNpcs(d.present_npcs)
    }

    const applyStatusUpdate = (d: StatusUpdateData) => {
      if (d.kind === 'hud') {
        usePlayerStore.getState().updateFromStatus(d as Record<string, unknown>)
        return
      }
      if (typeof d.hp_delta === 'number' && d.hp_delta !== 0) {
        const kind = d.hp_delta < 0 ? 'damage' : 'heal'
        const targetId = d.target_id ?? 'unknown'
        combat.addDamageNumber(Math.abs(d.hp_delta), kind, targetId)
        combat.addLogEntry(
          d.hp_delta < 0
            ? `${targetId} 受到 ${-d.hp_delta} 点伤害`
            : `${targetId} 恢复 ${d.hp_delta} 点生命`
        )
        if (d.hp_delta < 0) audio.playHit()
        else audio.playHeal()
      }
    }

    const finalizeStream = (d: { completed?: boolean }) => {
      const wasOpening = useSceneStore.getState().openingInProgress
      dialogue.clearPendingStream()
      stream.setStreaming(false)
      options.unlock()
      scene.setOpeningInProgress(false)
      scene.setTransitioning(false)
      if (wasOpening && d.completed !== false && worldId && sessionId) {
        useSessionStore.getState().setSession(worldId, sessionId, 'active')
      }
      const combatState = useCombatStore.getState()
      if (combatState.combatActive) {
        scene.setGameMode('combat')
        return
      }
      if (combatState.encounterActive) {
        scene.setGameMode('encounter')
        return
      }
      const sceneState = useSceneStore.getState()
      const nextMode: GameMode = sceneState.activeNpcId
        ? sceneState.gameMode === 'private_chat' ? 'private_chat' : 'dialogue'
        : 'explore'
      scene.setGameMode(nextMode)
      // QF-3: flush any buffered chat invites now that dialogue has ended
      const pending = pendingChatInvitesRef.current.splice(0)
      for (const invite of pending) {
        overlay.open('chat_invite', invite)
      }
    }

    const applyStreamError = (d: StreamErrorData) => {
      const detail = d.message || d.code || '流处理失败'
      addErrorMessage(`错误：${detail}`)
      stream.setStreaming(false)
      options.unlock()
      scene.setOpeningInProgress(false)
      scene.setTransitioning(false)
    }

    switch (event.event) {
      // ── 对话流消息 ───────────────────────────────────────────────────────
      case 'gm_narration': {
        const d = cast<GmNarrationData>(event.data)
        if (useSceneStore.getState().openingInProgress) {
          queueOpeningStep(async () => {
            await revealOpeningMessage('gm', d.content)
          })
          break
        }
        if (isVnMode()) {
          useVnStore.getState().resolveStreamMessage({ type: 'gm', content: d.content })
        } else {
          dialogue.resolveStreamMessage({ type: 'gm', content: d.content })
        }
        break
      }

      case 'gm_narration_added':
        break

      case 'gm_comment': {
        const d = cast<GmCommentData>(event.data)
        if (d.content) {
          if (useSceneStore.getState().openingInProgress) {
            queueOpeningStep(async () => {
              await revealOpeningMessage('gm_comment', d.content, d.tone)
            })
            break
          }
          if (isVnMode()) {
            useVnStore.getState().showGmComment(d.content)
          } else {
            dialogue.resolveStreamMessage({ type: 'gm_comment', content: d.content, tone: d.tone })
          }
        }
        break
      }

      case 'npc_response': {
        const d = cast<NpcResponseData>(event.data)
        if (!d.passive) {
          scene.setActivePortrait(d.npc_id)
          scene.setActiveNpc(d.npc_id)
          resolveDialogueMode()
        }
        if (isVnMode()) {
          if (d.type === 'emote') {
            useVnStore.getState().showEmote(d.npc_id, d.content)
          } else {
            scene.setVnSpeaker(d.npc_id)
            useVnStore.getState().resolveStreamMessage({ type: 'npc', speaker: d.npc_id, speakerName: d.npc_id, content: d.content })
          }
        } else {
          dialogue.resolveStreamMessage({
            type: d.type === 'emote' ? 'emote' : 'npc',
            speaker: d.npc_id,
            content: d.content,
          })
        }
        break
      }

      case 'npc_emote': {
        const d = cast<NpcEmoteData>(event.data)
        if (!d.passive) {
          scene.setActivePortrait(d.npc_id)
          scene.setActiveNpc(d.npc_id)
          resolveDialogueMode()
        }
        if (isVnMode()) {
          useVnStore.getState().showEmote(d.npc_id, d.action)
        } else {
          dialogue.resolveStreamMessage({ type: 'emote', speaker: d.npc_id, content: d.action })
        }
        break
      }

      case 'teammate_response': {
        const d = cast<TeammateResponseData>(event.data)
        if (isVnMode()) {
          if (d.type === 'emote') {
            useVnStore.getState().showEmote(d.character_id, d.content ?? d.action ?? '')
          } else {
            scene.setVnSpeaker(d.character_id)
            useVnStore.getState().resolveStreamMessage({
              type: 'teammate',
              speaker: d.character_id,
              speakerName: d.character_id,
              content: d.content ?? d.action ?? '',
            })
          }
        } else {
          dialogue.resolveStreamMessage({
            type: d.type === 'emote' ? 'teammate_emote' : 'teammate',
            speaker: d.character_id,
            content: d.content ?? d.action ?? '',
          })
        }
        break
      }

      case 'text_chunk': {
        const d = cast<TextChunkData>(event.data)
        if (isVnMode()) {
          useVnStore.getState().appendToCurrentContent(d.text)
        } else {
          dialogue.appendStreamChunk(d.text)
        }
        break
      }

      case 'character_enter': {
        const d = cast<CharacterEnterData>(event.data)
        if (useSceneStore.getState().openingInProgress) {
          queueOpeningStep(async () => {
            scene.addOpeningPortrait(d.character_id, d.position)
            await sleep(OPENING_PORTRAIT_DELAY_MS)
          })
          break
        }
        scene.addOpeningPortrait(d.character_id, d.position)
        break
      }

      // ── 选项 ─────────────────────────────────────────────────────────────
      case 'dialogue_options': {
        const d = cast<DialogueOptionsData>(event.data)
        if (useSceneStore.getState().openingInProgress) {
          queueOpeningStep(async () => {
            applyDialogueOptions(d)
            await sleep(OPENING_OPTIONS_DELAY_MS)
          })
          break
        }
        applyDialogueOptions(d)
        if (isVnMode()) {
          useVnStore.getState().setShowingOptions(true)
        }
        break
      }


      case 'dialogue_options_unavailable': {
        const d = cast<DialogueOptionsUnavailableData>(event.data)
        if (d.npc_id) {
          scene.setActiveNpc(d.npc_id)
          resolveDialogueMode()
        }
        options.clearOptions()
        addErrorMessage(d.message || '下一轮对话选项生成失败')
        break
      }

      // ── 快照触发的覆盖层 ─────────────────────────────────────────────────
      case 'shop_snapshot':
        overlay.open('shop', event.data)
        break

      case 'offer_trade':
        overlay.open('shop', event.data)
        break

      case 'talk_snapshot': {
        const d = cast<TalkSnapshotData>(event.data)
        const npcId = d.profile?.npc_id ?? d.target_id
        const npcName = d.profile?.name ?? npcId ?? '某人'
        addSystemMessage(`你走近了${npcName}`)
        if (npcId) {
          scene.setActiveNpc(npcId)
          scene.setActivePortrait(npcId)
        }
        scene.setGameMode('dialogue')

        const availableIntents = Array.isArray(d.available_intents) ? d.available_intents : []
        const nextOptions: GameOption[] = []
        if (npcId && availableIntents.some((intent) => intent === 'browse' || intent === 'buy' || intent === 'sell')) {
          nextOptions.push({
            id: `browse-${npcId}`,
            label: '看看货物',
            icon: '🛒',
            action: () => {
              void sendInteract({
                intent: 'browse',
                target_kind: 'npc',
                target_id: npcId,
              })
            },
          })
        }
        if (npcId && availableIntents.includes('donate')) {
          nextOptions.push({
            id: `donate-${npcId}`,
            label: '奉献',
            icon: '🪙',
            action: () => {
              overlay.open('donation', {
                targetKind: 'npc',
                targetId: npcId,
                sourceName: npcName,
              })
            },
          })
        }
        options.setOptions(nextOptions, true)
        break
      }

      case 'inspect_item':
        overlay.open('item_detail', event.data)
        break

      case 'quest_brief': {
        const d = cast<QuestBriefData>(event.data)
        const questBriefMsg = formatQuestBriefMessage(d)
        addSystemMessage(questBriefMsg)
        useNotificationStore.getState().add(questBriefMsg, 'info', 4000, 'quest')
        break
      }

      case 'quest_created': {
        const d = cast<QuestCreatedData>(event.data)
        const title = questName(d.title, d.quest_id)
        const msg = `新任务：${title}`
        addSystemMessage(msg)
        useNotificationStore.getState().add(msg, 'info', 4000, 'quest')
        break
      }

      case 'quest_progress': {
        const d = cast<QuestProgressData>(event.data)
        const questProgressMsg = formatQuestProgressMessage(d)
        addSystemMessage(questProgressMsg)
        useNotificationStore.getState().add(questProgressMsg, 'info', 4000, 'quest')
        break
      }

      case 'quest_location': {
        const d = cast<QuestLocationData>(event.data)
        addSystemMessage(formatQuestLocationMessage(d))
        break
      }

      case 'quest_requirements': {
        const d = cast<QuestRequirementsData>(event.data)
        addSystemMessage(formatQuestRequirementsMessage(d))
        break
      }

      case 'quest_reward': {
        const d = cast<QuestRewardData>(event.data)
        addSystemMessage(formatQuestRewardMessage(d))
        break
      }

      // ── 行动结果 / 系统通知 ───────────────────────────────────────────────
      case 'action_result': {
        const d = cast<ActionResultData>(event.data)
        const notif = useNotificationStore.getState()
        const executed = actionExecuted(d)
        const passed = actionOutcomePassed(d)
        if (executed && passed !== false) {
          // browse_board: 打开公告板面板
          if (d.action_type === 'browse_board' && d.metadata?.entries) {
            overlay.open('board', d.metadata)
            break
          }
          if (d.action_type === 'donate') {
            const amount = Number(d.metadata?.amount ?? 0)
            const remainingGold = Number(d.metadata?.remaining_gold)
            const sourceEntry = String(d.metadata?.source_entry ?? '供奉对象').trim() || '供奉对象'
            const message = Array.isArray(d.narrative_hints) && typeof d.narrative_hints[0] === 'string'
              ? d.narrative_hints[0]
              : amount > 0
                ? `向${sourceEntry}奉献了${amount}G`
                : formatActionSuccess(d.action_type)
            if (Number.isFinite(remainingGold)) {
              usePlayerStore.getState().updateFromStatus({ gold: remainingGold })
            }
            dialogue.addMessage({ type: 'system', content: message })
            notif.add(message, 'success')
            audio.playCoin()
            break
          }
          notif.add(formatActionSuccess(d.action_type), 'success')
          audio.playChime()
          if (d.action_type === 'retreat' || d.action_type === 'sneak_through') {
            combat.resetCombat()
          }
        } else if (!executed && d.errors.length > 0) {
          const message = `操作失败：${d.errors[0]}`
          dialogue.addMessage({ type: 'system', content: message })
          notif.add(message, 'error')
          audio.playError()
        }
        break
      }

      case 'interaction_rejected': {
        const d = cast<InteractionRejectedData>(event.data)
        addErrorMessage(`交互失败：${d.message}`)
        break
      }

      case 'input_rejected': {
        const d = cast<InputRejectedData>(event.data)
        addErrorMessage(`输入未被接受：${d.message}`)
        break
      }

      case 'npc_error': {
        const d = cast<NpcErrorData>(event.data)
        addErrorMessage(formatNpcError('对话失败', d))
        break
      }

      case 'npc_response_error': {
        const d = cast<NpcErrorData>(event.data)
        addErrorMessage(formatNpcError('对话生成失败', d))
        break
      }

      case 'agent_hook_error': {
        const d = cast<HookErrorData>(event.data)
        addErrorMessage(formatHookError('对话钩子异常', d))
        break
      }

      case 'hook_error': {
        const d = cast<HookErrorData>(event.data)
        addErrorMessage(formatHookError('结算钩子异常', d))
        break
      }

      case 'command_error': {
        const d = cast<HookErrorData>(event.data)
        addErrorMessage(formatHookError('钩子命令失败', d))
        break
      }

      case 'time_advanced': {
        const d = cast<TimeAdvancedData>(event.data)
        usePlayerStore.getState().updateFromStatus(d as unknown as Record<string, unknown>)

        if (d.rest_info) {
          // 休息中：只在最后一个 slot 显示总结，安静中间 slot 静默更新 HUD
          if (d.rest_info.is_final) {
            addSystemMessage(`长休完成（${d.rest_info.total_slots} 小时）`)
          }
        } else {
          addSystemMessage(formatTimeAdvancedMessage(d))
        }
        break
      }

      case 'relationship_stage_changed': {
        const d = cast<RelationshipStageChangedData>(event.data)
        const name = d.npc_name ?? d.npc_id
        const relMsg = `与${name}的关系提升为「${d.new_stage}」`
        addSystemMessage(relMsg)
        useNotificationStore.getState().add(relMsg, 'info', 3000, 'relationship')
        break
      }

      case 'npc_wants_to_chat': {
        const d = cast<NpcWantsToChatData>(event.data)
        const name = d.npc_name ?? d.npc_id
        if (d.colocated === false) {
          // Non-colocated: soft hint in notification + system log
          const hint = d.npc_location
            ? `${name}似乎有话想对你说（在${d.npc_location}）`
            : `${name}似乎有话想对你说`
          addSystemMessage(hint)
          useNotificationStore.getState().add(hint, 'info', 5000, 'quest')
        } else {
          // Colocated: direct chat invite modal
          // QF-3: buffer the invite if player is already in a dialogue/private chat session
          const currentMode = useSceneStore.getState().gameMode
          if (currentMode === 'dialogue' || currentMode === 'private_chat') {
            pendingChatInvitesRef.current.push(d)
          } else {
            overlay.open('chat_invite', d)
          }
        }
        break
      }

      case 'discovery_found':
      case 'discovery_reveal': {
        const d = cast<DiscoveryRevealData>(event.data)
        addSystemMessage(`发现了${d.name}！`)
        break
      }

      case 'hidden_object_revealed': {
        const d = cast<HiddenObjectRevealedData>(event.data)
        const name = d.name?.trim() || d.interactable_id
        addSystemMessage(`发现隐藏物：${name}`)
        break
      }

      case 'trap_detected': {
        const d = cast<TrapDetectedData>(event.data)
        addSystemMessage(`察觉到陷阱：${d.interactable_id}`)
        break
      }

      case 'campfire_dialogue': {
        const d = cast<CampfireDialogueData>(event.data)
        dialogue.resolveStreamMessage({
          type: 'teammate',
          speaker: d.teammate_id,
          content: d.content,
        })
        break
      }

      case 'event_state_changed': {
        const d = cast<EventStateChangedData>(event.data)
        addSystemMessage(formatEventStateChangedMessage(d))
        break
      }

      // ── 场景切换（导航时）────────────────────────────────────────────────
      case 'scene_change': {
        const d = cast<SceneChangeData>(event.data)
        if (useSceneStore.getState().openingInProgress) {
          queueOpeningStep(async () => {
            applySceneChange(d)
            await sleep(OPENING_SCENE_DELAY_MS)
          })
          break
        }
        applySceneChange(d)
        break
      }

      // ── 场景数据（所有流末尾都有）────────────────────────────────────────
      case 'location_overview': {
        const d = cast<LocationOverview>(event.data)
        if (useSceneStore.getState().openingInProgress) {
          queueOpeningStep(async () => {
            applyLocationOverview(d)
          })
          break
        }
        applyLocationOverview(d)
        break
      }

      case 'milestone_unlocked': {
        const d = cast<MilestoneUnlockedData>(event.data)
        useNotificationStore.getState().add(`里程碑解锁：${d.milestone_id}`, 'info', 3000, 'milestone')
        break
      }

      case 'milestone_failed': {
        const d = cast<MilestoneFailedData>(event.data)
        useNotificationStore.getState().add(`里程碑失败：${d.milestone_id}`, 'error', 3000, 'milestone')
        break
      }


      case 'ai_osiris_applied':
      case 'narrative_plan_updated':
      case 'npc_schedule_updated':
      case 'status_effects_ticked':
      case 'combat_effects_ticked':
      case 'dynamic_quest_expired':
      case 'dynamic_sub_areas_expired':
      case 'quest_status_changed':
        break

      case 'ai_osiris_error':
      case 'event_condition_error':
      case 'narrative_planner_error':
      case 'npc_schedule_error':
      case 'gm_narration_error':
      case 'encounter_error': {
        const d = cast<GenericErrorEventData>(event.data)
        const prefixMap: Record<string, string> = {
          ai_osiris_error: '奥西里斯异常',
          event_condition_error: '事件条件异常',
          narrative_planner_error: '叙事规划异常',
          npc_schedule_error: 'NPC日程异常',
          gm_narration_error: 'GM叙事异常',
          encounter_error: '遭遇异常',
        }
        addErrorMessage(formatGenericError(prefixMap[event.event] ?? '系统异常', d))
        break
      }

      case 'companion_recruited': {
        const d = cast<CompanionRecruitedData>(event.data)
        const message = formatCompanionMessage('join', d.npc_id, d.reason)
        addSystemMessage(message)
        useNotificationStore.getState().add(message, 'success', 3000, 'companion')
        const npcName = useSceneStore.getState().presentNpcs.find((npc) => npc.character_id === d.npc_id)?.name
        usePartyStore.getState().addMember(d.npc_id, npcName)
        usePartyStore.getState().syncMembers(d.party_members)
        break
      }

      case 'companion_dismissed': {
        const d = cast<CompanionDismissedData>(event.data)
        const activeNpcId = useSceneStore.getState().activeNpcId
        if (activeNpcId === d.npc_id) {
          leaveDialogue()
        }
        const message = formatCompanionMessage('leave', d.npc_id, d.reason)
        addSystemMessage(message)
        useNotificationStore.getState().add(message, 'info', 3000, 'companion')
        usePartyStore.getState().removeMember(d.npc_id)
        usePartyStore.getState().syncMembers(d.party_members)
        break
      }

      // ── 战斗事件 ─────────────────────────────────────────────────────────
      case 'encounter_spotted': {
        const d = cast<EncounterSpottedData>(event.data)
        combat.startEncounter(d)
        scene.setGameMode('encounter')
        audio.playAlert()
        break
      }

      case 'stealth_result': {
        const d = cast<StealthResultData>(event.data)
        combat.setStealthResult(d)
        break
      }

      case 'dice_roll': {
        const d = cast<DiceRollData>(event.data)
        combat.addDiceRoll(d)
        audio.playDiceRoll()
        break
      }

      case 'combat_start': {
        const d = cast<CombatStartData>(event.data)
        combat.startCombat(d)
        scene.setGameMode('combat')
        break
      }

      case 'combat_update': {
        const d = cast<CombatUpdateData>(event.data)
        combat.updateParticipants(d)
        const deadCount = d.participants.filter((p) => p.is_dead).length
        if (deadCount > 0) combat.addLogEntry(`${deadCount} 名敌人阵亡`)
        break
      }

      case 'vfx':
        // 保留扩展点，暂时静默忽略
        break

      case 'status_update': {
        const d = cast<StatusUpdateData>(event.data)
        if (useSceneStore.getState().openingInProgress && d.kind === 'hud') {
          queueOpeningStep(async () => {
            applyStatusUpdate(d)
            await sleep(OPENING_STATUS_DELAY_MS)
          })
          break
        }
        applyStatusUpdate(d)
        break
      }

      case 'combat_end': {
        const d = cast<CombatEndData>(event.data)
        combat.endCombat(d)
        if (d.result === 'victory') audio.playVictory()
        else audio.playDefeat()
        break
      }

      case 'effect_applied': {
        const d = cast<EffectAppliedData>(event.data)
        combat.addLogEntry(`${d.target_id} 获得效果：${d.effect_name}`)
        break
      }

      case 'effect_removed': {
        const d = cast<EffectRemovedData>(event.data)
        combat.addLogEntry(`${d.target_id} 效果消失：${d.effect_name}`)
        break
      }

      case 'loot_display': {
        const d = cast<LootDisplayData>(event.data)
        combat.setLoot(d)
        audio.playCoin()
        const parts: string[] = []
        if (d.gold > 0) parts.push(`${d.gold}G`)
        if (d.items?.length) parts.push(d.items.map((i) => i.name).join('、'))
        if (parts.length) {
          useNotificationStore.getState().add(`获得: ${parts.join(' + ')}`, 'success', 4000, 'item')
        }
        break
      }

      // ── 流控制 ───────────────────────────────────────────────────────────
      case 'stream_end': {
        const d = cast<{ completed?: boolean }>(event.data)
        if (useSceneStore.getState().openingInProgress) {
          queueOpeningStep(async () => {
            finalizeStream(d)
          })
          break
        }
        finalizeStream(d)
        break
      }

      case 'stream_error': {
        const d = cast<StreamErrorData>(event.data)
        if (useSceneStore.getState().openingInProgress) {
          queueOpeningStep(async () => {
            applyStreamError(d)
          })
          break
        }
        applyStreamError(d)
        break
      }

      case 'ai_processing': {
        const d = cast<AiProcessingData>(event.data)
        stream.setAiProcessing(d.status === 'start')
        break
      }

      case 'player_level_up': {
        const d = cast<PlayerLevelUpData>(event.data)
        const newLevel = typeof d.to_level === 'number' ? d.to_level : d.new_level
        const newFeatures = Array.isArray(d.features)
          ? d.features
          : Array.isArray(d.new_features)
            ? d.new_features
            : []
        if (typeof newLevel !== 'number') break
        const lvlMsg = `升级！达到 Lv.${newLevel}`
        useNotificationStore.getState().add(lvlMsg, 'success', 6000, 'milestone')
        usePlayerStore.getState().updateFromStatus({
          level: newLevel,
          asi_available: d.asi_available,
        } as Record<string, unknown>)
        if (d.asi_available) {
          useNotificationStore.getState().add('可分配属性点（ASI）', 'info', 5000, 'milestone')
        }
        if (newFeatures.length > 0) {
          useNotificationStore.getState().add(`新特性：${newFeatures.join('、')}`, 'info', 5000, 'milestone')
        }
        break
      }

      default:
        // 未知事件类型，静默忽略
        break
    }
  }, [dialogue, options, scene, overlay, stream, combat, overviewHandlers]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── 发请求的通用逻辑 ──────────────────────────────────────────────────────

  const startStream = useCallback((url: string, body: object): Promise<void> => {
    if (!worldId || !sessionId) return Promise.resolve()
    dialogue.clearPendingStream()
    abortRef.current?.abort()
    abortRef.current = new AbortController()
    openingQueueRef.current = Promise.resolve()
    stream.setStreaming(true)
    options.lock()

    return streamSSE(
      url,
      body,
      handleEvent,
      () => { /* onDone：stream_end 事件会处理解锁 */ },
      (err) => {
        openingQueueRef.current = Promise.resolve()
        const message = `连接错误：${err.message}`
        dialogue.clearPendingStream()
        dialogue.addMessage({ type: 'system', content: message })
        useNotificationStore.getState().add(message, 'error')
        stream.setStreaming(false)
        options.unlock()
        scene.setOpeningInProgress(false)
        scene.setTransitioning(false)
        audio.playError()
      },
      abortRef.current.signal,
    )
  }, [worldId, sessionId, handleEvent, stream, options, dialogue, scene])

  // ── 各操作入口 ────────────────────────────────────────────────────────────

  const sendInteract = useCallback((req: InteractRequest) => {
    if (!worldId || !sessionId) return
    const message = req.message?.trim() ?? ''
    if (req.scope === 'private') {
      const npcId = req.target_id ?? req.npc_id
      if (!npcId) return
      if (message) {
        dialogue.addMessage({ type: 'player', content: message })
      }
      scene.setActiveNpc(npcId)
      scene.setGameMode('private_chat')
      startStream(urls.privateChat(worldId, sessionId), {
        npc_id: npcId,
        message: req.message ?? '',
      })
      return
    }
    if (message) {
      dialogue.addMessage({ type: 'player', content: message })
    }
    startStream(urls.interact(worldId, sessionId), req)
  }, [worldId, sessionId, dialogue, scene, startStream])

  const sendNavigate = useCallback((req: NavigateRequest) => {
    if (!worldId || !sessionId) return
    return startStream(urls.navigate(worldId, sessionId), req)
  }, [worldId, sessionId, startStream])

  const sendAction = useCallback((req: StructuredActionRequest) => {
    if (!worldId || !sessionId) return Promise.resolve()
    return startStream(urls.action(worldId, sessionId), req)
  }, [worldId, sessionId, startStream])

  const runFunctionalOption = (item: DialogueOptionItem): boolean => {
    const functionalType = String(item.functional?.type ?? '').trim()
    if (!functionalType) return false

    const notifyError = (message: string) => {
      useNotificationStore.getState().add(message, 'error')
    }
    const activeNpcId = useSceneStore.getState().activeNpcId?.trim() || null

    switch (functionalType) {
      case 'trade_browse': {
        const npcId = readFunctionalParam(item, 'npc_id') ?? activeNpcId
        if (!npcId) {
          notifyError('交易选项缺少目标 NPC')
          return true
        }
        void sendInteract({
          intent: 'browse',
          target_kind: 'npc',
          target_id: npcId,
        })
        return true
      }

      case 'board_browse': {
        const boardId = readFunctionalParam(item, 'board_id')
        if (!boardId) {
          notifyError('任务板选项缺少 board_id')
          return true
        }
        void sendAction({
          action_type: 'browse_board',
          params: { board_id: boardId },
        })
        return true
      }

      case 'donate':
      case 'donation': {
        const targetKind = readFunctionalParam(item, 'target_kind') === 'interactable'
          ? 'interactable'
          : 'npc'
        const targetId = readFunctionalParam(item, 'target_id') ?? activeNpcId
        if (!targetId) {
          notifyError('奉献选项缺少目标')
          return true
        }
        overlay.open('donation', {
          targetKind,
          targetId,
          sourceName: item.label ?? item.text ?? targetId,
        })
        return true
      }

      case 'quest_accept': {
        const questId = readFunctionalParam(item, 'quest_id')
        if (!questId) {
          notifyError('接任务选项缺少 quest_id')
          return true
        }
        const npcId = readFunctionalParam(item, 'npc_id') ?? activeNpcId
        if (!npcId) {
          notifyError('接任务选项缺少目标 NPC')
          return true
        }
        void sendInteract({
          intent: 'accept_quest',
          target_kind: 'npc',
          target_id: npcId,
          quest_id: questId,
        })
        return true
      }

      case 'navigate': {
        const locationId = readFunctionalParam(item, 'location_id')
        if (locationId) {
          void sendNavigate({ action: 'enter_sub_location', location_id: locationId })
          return true
        }
        const areaId = readFunctionalParam(item, 'area_id')
        if (areaId) {
          void sendNavigate({ action: 'move_area', area_id: areaId })
          return true
        }
        notifyError('导航选项缺少 area_id 或 location_id')
        return true
      }

      case 'inspect_item':
      case 'rest':
        notifyError(`当前入口暂未接通 ${functionalType}`)
        return true

      default:
        notifyError(`未知功能选项：${functionalType}`)
        return true
    }
  }
  runFunctionalOptionRef.current = runFunctionalOption

  const sendInput = useCallback((req: TextInputRequest) => {
    if (!worldId || !sessionId) return
    dialogue.addMessage({ type: 'player', content: req.text })
    startStream(urls.input(worldId, sessionId), req)
  }, [worldId, sessionId, dialogue, startStream])

  useEffect(() => {
    if (!isLocalDevEnvironment()) return

    const debugTarget = globalThis as typeof globalThis & { __GAME_DEBUG__?: GameDebugApi }
    debugTarget.__GAME_DEBUG__ = {
      runFunctionalOption: (item: DialogueOptionItem) => {
        runFunctionalOptionRef.current(item)
      },
    }

    return () => {
      delete debugTarget.__GAME_DEBUG__
    }
  }, [])

  const sendPrivateChat = useCallback((req: PrivateChatRequest) => {
    void sendInteract({
      scope: 'private',
      intent: 'talk',
      target_kind: 'npc',
      target_id: req.npc_id,
      npc_id: req.npc_id,
      message: req.message,
    })
  }, [sendInteract])

  const abort = useCallback(() => {
    abortRef.current?.abort()
    openingQueueRef.current = Promise.resolve()
    dialogue.clearPendingStream()
    stream.setStreaming(false)
    options.unlock()
    scene.setOpeningInProgress(false)
    scene.setTransitioning(false)
  }, [dialogue, stream, options, scene])

  const sendCombatAction = useCallback((actionType: string, params: Record<string, unknown> = {}) => {
    if (!worldId || !sessionId) return
    startStream(urls.combatAction(worldId, sessionId), { action_type: actionType, params })
  }, [worldId, sessionId, startStream])

  const sendEncounterAction = useCallback((choice: string, subAreaId: string) => {
    if (!worldId || !sessionId) return
    startStream(urls.encounterAction(worldId, sessionId), { choice, sub_area_id: subAreaId })
  }, [worldId, sessionId, startStream])

  const sendCompanionRecruit = useCallback((npcId: string) => {
    if (!worldId || !sessionId) return
    startStream(urls.companionRecruit(worldId, sessionId), { npc_id: npcId })
  }, [worldId, sessionId, startStream])

  const sendCompanionDismiss = useCallback((npcId: string) => {
    if (!worldId || !sessionId) return
    startStream(urls.companionDismiss(worldId, sessionId), { npc_id: npcId })
  }, [worldId, sessionId, startStream])

  const sendOpening = useCallback(() => {
    if (!worldId || !sessionId) return
    startStream(urls.opening(worldId, sessionId), {})
  }, [worldId, sessionId, startStream])

  return {
    sendInteract,
    sendNavigate,
    sendAction,
    sendInput,
    sendPrivateChat,
    abort,
    sendCompanionRecruit,
    sendCompanionDismiss,
    sendCombatAction,
    sendEncounterAction,
    sendOpening,
  }
}
