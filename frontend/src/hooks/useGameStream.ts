// SSE 事件分发 hook，职责：连接 sse-client ↔ stores
// 唯一有网络副作用的层，各 store 自身不发请求
import { useCallback, useRef } from 'react'
import { streamSSE } from '../lib/sse-client'
import { urls } from '../lib/api'
import { useSessionStore } from '../stores/sessionStore'
import { useDialogueStore } from '../stores/dialogueStore'
import { useOptionStore } from '../stores/optionStore'
import { useSceneStore } from '../stores/sceneStore'
import { useOverlayStore } from '../stores/overlayStore'
import { useStreamStore } from '../stores/streamStore'
import type {
  GmNarrationData,
  NpcResponseData,
  NpcEmoteData,
  TeammateResponseData,
  TextChunkData,
  DialogueOptionsData,
  ActionResultData,
  SceneChangeData,
  TimeAdvancedData,
  RelationshipStageChangedData,
  NpcWantsToChatData,
  StreamErrorData,
} from '../types/sse'
import type { LocationOverview } from '../types/game'
import type {
  InteractRequest,
  NavigateRequest,
  StructuredActionRequest,
  PrivateChatRequest,
  TextInputRequest,
} from '../types/api'
import type { OverviewHandlers } from '../stores/optionStore'

function cast<T>(data: unknown): T {
  return data as T
}

export function useGameStream(overviewHandlers: OverviewHandlers) {
  const { worldId, sessionId } = useSessionStore()
  const dialogue = useDialogueStore()
  const options = useOptionStore()
  const scene = useSceneStore()
  const overlay = useOverlayStore()
  const stream = useStreamStore()

  // AbortController：每次发新请求前 abort 上一个流
  const abortRef = useRef<AbortController | null>(null)

  const handleEvent = useCallback((event: { event: string; data: unknown }) => {
    switch (event.event) {
      // ── 对话流消息 ───────────────────────────────────────────────────────
      case 'gm_narration':
      case 'gm_narration_added': {
        const d = cast<GmNarrationData>(event.data)
        dialogue.addMessage({ type: 'gm', content: d.content })
        break
      }

      case 'npc_response': {
        const d = cast<NpcResponseData>(event.data)
        dialogue.addMessage({
          type: d.type === 'emote' ? 'emote' : 'npc',
          speaker: d.npc_id,
          content: d.content,
        })
        break
      }

      case 'npc_emote': {
        const d = cast<NpcEmoteData>(event.data)
        dialogue.addMessage({ type: 'emote', speaker: d.npc_id, content: d.action })
        break
      }

      case 'teammate_response': {
        const d = cast<TeammateResponseData>(event.data)
        dialogue.addMessage({
          type: 'teammate',
          speaker: d.character_id,
          content: d.content ?? d.action ?? '',
        })
        break
      }

      case 'text_chunk': {
        const d = cast<TextChunkData>(event.data)
        dialogue.appendToLast(d.text)
        break
      }

      // ── 选项 ─────────────────────────────────────────────────────────────
      case 'dialogue_options': {
        const d = cast<DialogueOptionsData>(event.data)
        options.setFromDialogueOptions(d.options, (label) => {
          // 玩家选择对话选项 → 以 message 形式发送 interact
          const { worldId: wid, sessionId: sid } = useSessionStore.getState()
          const talkTarget = useSceneStore.getState()
          if (wid && sid) {
            void sendInteract({
              intent: 'talk',
              target_kind: 'npc',
              target_id: talkTarget.presentNpcs[0]?.character_id,
              message: label,
            })
          }
        })
        break
      }

      // ── 快照触发的覆盖层 ─────────────────────────────────────────────────
      case 'shop_snapshot':
        overlay.open('shop', event.data)
        break

      case 'board_snapshot':
        overlay.open('board', event.data)
        break

      case 'inspect_item':
        overlay.open('item_detail', event.data)
        break

      // ── 行动结果 / 系统通知 ───────────────────────────────────────────────
      case 'action_result': {
        const d = cast<ActionResultData>(event.data)
        if (!d.success && d.errors.length > 0) {
          dialogue.addMessage({ type: 'system', content: `操作失败：${d.errors[0]}` })
        }
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
        dialogue.addMessage({ type: 'system', content: `── ${period}降临 ──` })
        break
      }

      case 'relationship_stage_changed': {
        const d = cast<RelationshipStageChangedData>(event.data)
        const name = d.npc_name ?? d.npc_id
        dialogue.addMessage({
          type: 'system',
          content: `与${name}的关系提升为「${d.new_stage}」`,
        })
        break
      }

      case 'npc_wants_to_chat': {
        const d = cast<NpcWantsToChatData>(event.data)
        overlay.open('chat_invite', d)
        break
      }

      // ── 场景切换（导航时）────────────────────────────────────────────────
      case 'scene_change': {
        const d = cast<SceneChangeData>(event.data)
        scene.transitionTo(d)
        dialogue.clearForSceneChange()
        break
      }

      // ── 场景数据（所有流末尾都有）────────────────────────────────────────
      case 'location_overview': {
        const d = cast<LocationOverview>(event.data)
        scene.updateFromOverview(d)
        // 若无 LLM 对话选项，用 overview 组装探索选项
        if (!useOptionStore.getState().hasDialogueOptions) {
          options.buildFromOverview(d, overviewHandlers)
        }
        break
      }

      // ── 流控制 ───────────────────────────────────────────────────────────
      case 'stream_end':
        stream.setStreaming(false)
        options.unlock()
        scene.setTransitioning(false)
        break

      case 'stream_error': {
        const d = cast<StreamErrorData>(event.data)
        dialogue.addMessage({ type: 'system', content: `错误：${d.message}` })
        stream.setStreaming(false)
        options.unlock()
        break
      }

      default:
        // 未知事件类型，静默忽略
        break
    }
  }, [dialogue, options, scene, overlay, stream, overviewHandlers]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── 发请求的通用逻辑 ──────────────────────────────────────────────────────

  const startStream = useCallback((url: string, body: object) => {
    if (!worldId || !sessionId) return
    abortRef.current?.abort()
    abortRef.current = new AbortController()
    stream.setStreaming(true)
    options.lock()

    streamSSE(
      url,
      body,
      handleEvent,
      () => { /* onDone：stream_end 事件会处理解锁 */ },
      (err) => {
        dialogue.addMessage({ type: 'system', content: `连接错误：${err.message}` })
        stream.setStreaming(false)
        options.unlock()
      },
      abortRef.current.signal,
    )
  }, [worldId, sessionId, handleEvent, stream, options, dialogue])

  // ── 各操作入口 ────────────────────────────────────────────────────────────

  const sendInteract = useCallback((req: InteractRequest) => {
    if (!worldId || !sessionId) return
    // 玩家发言先加入对话流
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
    dialogue.addMessage({ type: 'player', content: req.message })
    startStream(urls.privateChat(worldId, sessionId), req)
  }, [worldId, sessionId, dialogue, startStream])

  const abort = useCallback(() => {
    abortRef.current?.abort()
    stream.setStreaming(false)
    options.unlock()
  }, [stream, options])

  return { sendInteract, sendNavigate, sendAction, sendInput, sendPrivateChat, abort }
}
