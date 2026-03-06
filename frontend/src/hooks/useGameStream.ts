// SSE 事件分发 hook，职责：连接 sse-client ↔ stores
// 唯一有网络副作用的层，各 store 自身不发请求
import { useCallback, useRef } from 'react'
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
import type {
  CharacterEnterData,
  GmCommentData,
  GmNarrationData,
  NpcResponseData,
  NpcEmoteData,
  TeammateResponseData,
  TextChunkData,
  DialogueOptionsData,
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
    accept: '任务接受',
    accept_quest: '任务接受',
    complete: '任务完成',
    retire: '任务撤销',
    equip: '装备完成',
    drop: '物品丢弃',
    rest_short: '休息完成',
    rest_long: '长休完成',
    enter: '进入成功',
    surprise_attack: '突袭成功',
    sneak_through: '潜行通过',
    retreat: '已撤退',
  }
  return labels[actionType] ?? '操作成功'
}

function questName(title: string | undefined, questId: string): string {
  return title?.trim() || questId
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

function formatCompanionMessage(action: 'join' | 'leave', npcId: string, reason?: string): string {
  const suffix = reason?.trim() ? `（${reason.trim()}）` : ''
  return action === 'join'
    ? `${npcId} 加入了队伍${suffix}`
    : `${npcId} 离开了队伍${suffix}`
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

    const leaveDialogue = () => {
      const sceneState = useSceneStore.getState()
      const optionState = useOptionStore.getState()
      scene.setActiveNpc(null)
      scene.setGameMode('explore')
      optionState.clearOptions()
      if (sceneState.lastOverview) {
        optionState.buildFromOverview(sceneState.lastOverview, overviewHandlers)
      }
    }

    switch (event.event) {
      // ── 对话流消息 ───────────────────────────────────────────────────────
      case 'gm_narration': {
        const d = cast<GmNarrationData>(event.data)
        dialogue.resolveStreamMessage({ type: 'gm', content: d.content })
        break
      }

      case 'gm_narration_added':
        break

      case 'gm_comment': {
        const d = cast<GmCommentData>(event.data)
        if (d.content) {
          dialogue.resolveStreamMessage({ type: 'gm_comment', content: d.content })
        }
        break
      }

      case 'npc_response': {
        const d = cast<NpcResponseData>(event.data)
        dialogue.resolveStreamMessage({
          type: d.type === 'emote' ? 'emote' : 'npc',
          speaker: d.npc_id,
          content: d.content,
        })
        scene.setActivePortrait(d.npc_id)
        scene.setActiveNpc(d.npc_id)
        resolveDialogueMode()
        break
      }

      case 'npc_emote': {
        const d = cast<NpcEmoteData>(event.data)
        dialogue.resolveStreamMessage({ type: 'emote', speaker: d.npc_id, content: d.action })
        scene.setActivePortrait(d.npc_id)
        scene.setActiveNpc(d.npc_id)
        resolveDialogueMode()
        break
      }

      case 'teammate_response': {
        const d = cast<TeammateResponseData>(event.data)
        dialogue.resolveStreamMessage({
          type: 'teammate',
          speaker: d.character_id,
          content: d.content ?? d.action ?? '',
        })
        break
      }

      case 'text_chunk': {
        const d = cast<TextChunkData>(event.data)
        dialogue.appendStreamChunk(d.text)
        break
      }

      case 'character_enter': {
        const d = cast<CharacterEnterData>(event.data)
        scene.addOpeningPortrait(d.character_id, d.position)
        break
      }

      // ── 选项 ─────────────────────────────────────────────────────────────
      case 'dialogue_options': {
        const d = cast<DialogueOptionsData>(event.data)
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

          const activeNpcId = useSceneStore.getState().activeNpcId
          if (activeNpcId && label) {
            void sendInteract({
              intent: 'talk',
              target_kind: 'npc',
              target_id: activeNpcId,
              message: label,
            })
          }
        })
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

      case 'board_snapshot':
        overlay.open('board', event.data)
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
        options.setOptions(nextOptions, true)
        break
      }

      case 'inspect_item':
        overlay.open('item_detail', event.data)
        break

      case 'quest_brief': {
        const d = cast<QuestBriefData>(event.data)
        addSystemMessage(formatQuestBriefMessage(d))
        break
      }

      case 'quest_progress': {
        const d = cast<QuestProgressData>(event.data)
        addSystemMessage(formatQuestProgressMessage(d))
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
        if (d.success) {
          notif.add(formatActionSuccess(d.action_type), 'success')
          audio.playChime()
          if (d.action_type === 'retreat' || d.action_type === 'sneak_through') {
            combat.resetCombat()
          }
        } else if (d.errors.length > 0) {
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
        const periodMap: Record<string, string> = {
          dawn: '黎明', morning: '清晨', noon: '正午',
          afternoon: '午后', dusk: '黄昏', evening: '傍晚',
          night: '深夜', midnight: '午夜',
        }
        const period = periodMap[d.period] ?? d.period
        addSystemMessage(`── ${period}降临 ──`)
        break
      }

      case 'relationship_stage_changed': {
        const d = cast<RelationshipStageChangedData>(event.data)
        const name = d.npc_name ?? d.npc_id
        addSystemMessage(`与${name}的关系提升为「${d.new_stage}」`)
        break
      }

      case 'npc_wants_to_chat': {
        const d = cast<NpcWantsToChatData>(event.data)
        overlay.open('chat_invite', d)
        break
      }

      case 'discovery_reveal': {
        const d = cast<DiscoveryRevealData>(event.data)
        addSystemMessage(`发现了${d.name}！`)
        break
      }

      // ── 场景切换（导航时）────────────────────────────────────────────────
      case 'scene_change': {
        const d = cast<SceneChangeData>(event.data)
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
        break
      }

      // ── 场景数据（所有流末尾都有）────────────────────────────────────────
      case 'location_overview': {
        const d = cast<LocationOverview>(event.data)
        scene.updateFromOverview(d)
        const sceneState = useSceneStore.getState()
        if (!useOptionStore.getState().hasDialogueOptions && !sceneState.activeNpcId) {
          options.buildFromOverview(d, overviewHandlers)
        }
        break
      }

      case 'milestone_unlocked': {
        const d = cast<MilestoneUnlockedData>(event.data)
        useNotificationStore.getState().add(`里程碑解锁：${d.milestone_id}`, 'info')
        break
      }

      case 'milestone_failed': {
        const d = cast<MilestoneFailedData>(event.data)
        useNotificationStore.getState().add(`里程碑失败：${d.milestone_id}`, 'error')
        break
      }


      case 'companion_recruited': {
        const d = cast<CompanionRecruitedData>(event.data)
        const message = formatCompanionMessage('join', d.npc_id, d.reason)
        addSystemMessage(message)
        useNotificationStore.getState().add(message, 'success')
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
        useNotificationStore.getState().add(message, 'info')
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
        if (d.kind === 'hud') {
          usePlayerStore.getState().updateFromStatus(d as Record<string, unknown>)
          break
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
        break
      }

      // ── 流控制 ───────────────────────────────────────────────────────────
      case 'stream_end': {
        dialogue.clearPendingStream()
        stream.setStreaming(false)
        options.unlock()
        scene.setOpeningInProgress(false)
        scene.setTransitioning(false)
        const combatState = useCombatStore.getState()
        if (combatState.combatActive) {
          scene.setGameMode('combat')
          break
        }
        if (combatState.encounterActive) {
          scene.setGameMode('encounter')
          break
        }
        const sceneState = useSceneStore.getState()
        const nextMode: GameMode = sceneState.activeNpcId
          ? sceneState.gameMode === 'private_chat' ? 'private_chat' : 'dialogue'
          : 'explore'
        scene.setGameMode(nextMode)
        break
      }

      case 'stream_error': {
        const d = cast<StreamErrorData>(event.data)
        const detail = d.message || d.code || '流处理失败'
        addErrorMessage(`错误：${detail}`)
        stream.setStreaming(false)
        options.unlock()
        scene.setOpeningInProgress(false)
        scene.setTransitioning(false)
        break
      }

      default:
        // 未知事件类型，静默忽略
        break
    }
  }, [dialogue, options, scene, overlay, stream, combat, overviewHandlers]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── 发请求的通用逻辑 ──────────────────────────────────────────────────────

  const startStream = useCallback((url: string, body: object) => {
    if (!worldId || !sessionId) return
    dialogue.clearPendingStream()
    abortRef.current?.abort()
    abortRef.current = new AbortController()
    stream.setStreaming(true)
    options.lock()

    void streamSSE(
      url,
      body,
      handleEvent,
      () => { /* onDone：stream_end 事件会处理解锁 */ },
      (err) => {
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
    if (req.message) {
      dialogue.addMessage({ type: 'player', content: req.message })
    }
    startStream(urls.interact(worldId, sessionId), req)
  }, [worldId, sessionId, dialogue, startStream])

  const sendNavigate = useCallback((req: NavigateRequest) => {
    if (!worldId || !sessionId) return
    startStream(urls.navigate(worldId, sessionId), req)
  }, [worldId, sessionId, startStream])

  const sendAction = useCallback((req: StructuredActionRequest) => {
    if (!worldId || !sessionId) return
    startStream(urls.action(worldId, sessionId), req)
  }, [worldId, sessionId, startStream])

  const sendInput = useCallback((req: TextInputRequest) => {
    if (!worldId || !sessionId) return
    dialogue.addMessage({ type: 'player', content: req.text })
    startStream(urls.input(worldId, sessionId), req)
  }, [worldId, sessionId, dialogue, startStream])

  const sendPrivateChat = useCallback((req: PrivateChatRequest) => {
    if (!worldId || !sessionId) return
    scene.setActiveNpc(req.npc_id)
    scene.setGameMode('private_chat')
    if (req.message.trim()) {
      dialogue.addMessage({ type: 'player', content: req.message })
    }
    startStream(urls.privateChat(worldId, sessionId), req)
  }, [worldId, sessionId, dialogue, scene, startStream])

  const abort = useCallback(() => {
    abortRef.current?.abort()
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
    sendCombatAction,
    sendEncounterAction,
    sendOpening,
  }
}
