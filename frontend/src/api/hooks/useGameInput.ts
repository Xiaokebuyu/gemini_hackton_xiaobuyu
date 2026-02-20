/**
 * Core game input streaming hook
 *
 * useStreamGameInput — SSE streaming (POST /input/stream)
 */
import { useCallback, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { streamGameInput } from '../sseClient';
import { useGameStore } from '../../stores/gameStore';
import { useChatStore } from '../../stores/chatStore';
import { toast } from '../../stores/uiStore';
import type {
  AgenticTracePayload,
  CoordinatorImageData,
  GameAction,
  StateDelta,
  CoordinatorChapterInfo,
  DiceRoll,
} from '../../types';

function normalizeImageData(value: unknown): CoordinatorImageData | null {
  if (!value || typeof value !== 'object') return null;
  const raw = value as Record<string, unknown>;
  if (typeof raw.base64 !== 'string' || raw.base64.length === 0) return null;
  return {
    base64: raw.base64,
    mime_type: typeof raw.mime_type === 'string' && raw.mime_type ? raw.mime_type : 'image/png',
    ...(typeof raw.style === 'string' ? { style: raw.style } : {}),
    ...(typeof raw.prompt === 'string' ? { prompt: raw.prompt } : {}),
    ...(typeof raw.model === 'string' ? { model: raw.model } : {}),
  };
}

function normalizeAgenticTrace(value: unknown): AgenticTracePayload | null {
  return value && typeof value === 'object' ? (value as AgenticTracePayload) : null;
}

function parseDiceResult(raw: unknown): DiceRoll | null {
  if (!raw || typeof raw !== 'object') return null;
  const dr = raw as Record<string, unknown>;
  if (typeof dr.roll !== 'number') return null;
  return {
    roll_type: 'd20',
    result: dr.roll,
    modifier: typeof dr.modifier === 'number' ? dr.modifier : 0,
    total: typeof dr.total === 'number' ? dr.total : 0,
    is_critical: typeof dr.is_critical === 'boolean' ? dr.is_critical : false,
    is_fumble: typeof dr.is_fumble === 'boolean' ? dr.is_fumble : false,
    ability: typeof dr.ability === 'string' ? dr.ability : undefined,
    skill: typeof dr.skill === 'string' ? dr.skill : undefined,
    proficiency: typeof dr.proficiency === 'number' ? dr.proficiency : undefined,
    dc: typeof dr.dc === 'number' ? dr.dc : undefined,
    success: typeof dr.success === 'boolean' ? dr.success : undefined,
    description: typeof dr.description === 'string' ? dr.description : undefined,
  };
}
// =============================================================================
// SSE streaming hook
// =============================================================================

export function useStreamGameInput() {
  const queryClient = useQueryClient();
  const {
    worldId,
    sessionId,
    setAvailableActions,
    setNarrativeSnapshot,
    updateFromStateDelta,
    setImageData,
    setAgenticTrace,
    appendAgenticToolCall,
    setDiceRoll,
  } =
    useGameStore();
  const {
    addPlayerMessage,
    addMessage,
    setLoading,
    addSystemMessage,
    isLoading,
    startStreamingMessage,
    appendToStreamingMessage,
    finalizeStreamingMessage,
  } = useChatStore();

  const abortRef = useRef<AbortController | null>(null);

  const sendInput = useCallback(
    async (content: string) => {
      if (!worldId || !sessionId) return;

      addPlayerMessage(content);
      setLoading(true);
      setImageData(null);
      setAgenticTrace(null);
      abortRef.current = new AbortController();

      let currentGmId: string | null = null;
      const teammateIds: Record<string, string> = {};
      let receivedTeammateStreamingEvent = false;
      let receivedNpcResponseEvent = false;

      try {
        await streamGameInput(
          worldId,
          sessionId,
          { input: content },
          (event) => {
            switch (event.type) {
              case 'gm_start':
                currentGmId = startStreamingMessage('GM', 'gm');
                break;

              case 'gm_chunk':
                if (currentGmId && event.chunk_type === 'answer') {
                  appendToStreamingMessage(currentGmId, event.text as string);
                }
                break;

              case 'gm_end':
                if (currentGmId) {
                  finalizeStreamingMessage(currentGmId, event.full_text as string);
                  currentGmId = null;
                }
                break;

              case 'teammate_start': {
                receivedTeammateStreamingEvent = true;
                const charId = event.character_id as string;
                const name = event.name as string;
                teammateIds[charId] = startStreamingMessage(name, 'teammate', {
                  character_id: charId,
                });
                break;
              }

              case 'teammate_chunk': {
                receivedTeammateStreamingEvent = true;
                const tmId = teammateIds[event.character_id as string];
                if (tmId) appendToStreamingMessage(tmId, event.text as string);
                break;
              }

              case 'teammate_end': {
                receivedTeammateStreamingEvent = true;
                const endId = teammateIds[event.character_id as string];
                if (endId) {
                  finalizeStreamingMessage(endId, event.response as string);
                }
                break;
              }

              case 'npc_response': {
                receivedNpcResponseEvent = true;
                const dialogue = typeof event.dialogue === 'string' ? event.dialogue : '';
                if (dialogue) {
                  addMessage({
                    speaker: (event.name as string) || 'NPC',
                    content: dialogue,
                    type: 'npc',
                    metadata: {
                      character_id: typeof event.character_id === 'string' ? event.character_id : undefined,
                    },
                  });
                }
                break;
              }

              case 'teammate_response': {
                receivedTeammateStreamingEvent = true;
                const responseText = typeof event.response === 'string' ? event.response : '';
                if (responseText) {
                  addMessage({
                    speaker: (event.name as string) || 'Teammate',
                    content: responseText,
                    type: 'teammate',
                    metadata: {
                      reaction: typeof event.reaction === 'string' ? event.reaction : undefined,
                      character_id: typeof event.character_id === 'string' ? event.character_id : undefined,
                    },
                  });
                }
                break;
              }

              case 'agentic_tool_call': {
                appendAgenticToolCall({
                  index: typeof event.tool_index === 'number' ? event.tool_index : undefined,
                  name: typeof event.name === 'string' ? event.name : undefined,
                  success: typeof event.success === 'boolean' ? event.success : true,
                  duration_ms: typeof event.duration_ms === 'number' ? event.duration_ms : undefined,
                  error: typeof event.error === 'string' ? event.error : undefined,
                });
                // 好感度变更实时 toast 通知
                const dc = (event as Record<string, unknown>).disposition_change;
                if (dc && typeof dc === 'object') {
                  const change = dc as Record<string, unknown>;
                  const npcId = change.npc_id as string;
                  const deltas = change.deltas as Record<string, number> | undefined;
                  if (npcId && deltas && typeof deltas === 'object') {
                    // 从 party members 查找 NPC 显示名
                    const party = useGameStore.getState().party;
                    const member = party?.members?.find((m) => m.character_id === npcId);
                    const displayName = member?.name || npcId;
                    const parts: string[] = [];
                    const dimLabels: Record<string, string> = {
                      approval: '好感',
                      trust: '信任',
                      fear: '畏惧',
                      romance: '浪漫',
                    };
                    for (const [dim, val] of Object.entries(deltas)) {
                      if (typeof val === 'number' && val !== 0) {
                        const label = dimLabels[dim] || dim;
                        parts.push(`${label} ${val > 0 ? '+' : ''}${val}`);
                      }
                    }
                    if (parts.length > 0) {
                      toast.info(`${displayName}: ${parts.join(', ')}`);
                    }
                  }
                }
                // 骰子结果 → 全局骰子动画
                const diceResult = (event as Record<string, unknown>).dice_result;
                const parsedDice = parseDiceResult(diceResult);
                if (parsedDice) {
                  setDiceRoll(parsedDice);
                }
                break;
              }

              case 'agentic_trace':
                setAgenticTrace(normalizeAgenticTrace(event.agentic_trace) ?? null);
                break;

              case 'dice_roll': {
                const parsed = parseDiceResult((event as Record<string, unknown>).result);
                if (parsed) {
                  setDiceRoll(parsed);
                }
                break;
              }

              case 'complete': {
                if (event.state_delta) {
                  updateFromStateDelta(event.state_delta as StateDelta);
                }
                const chapterInfo = event.chapter_info as CoordinatorChapterInfo | undefined;
                const storyEvents = Array.isArray(event.story_events)
                  ? (event.story_events as string[])
                  : [];
                setNarrativeSnapshot(
                  chapterInfo || null,
                  storyEvents,
                  (event.pacing_action as string | null) || null,
                );
                setImageData(normalizeImageData(event.image_data) ?? null);
                const metadata = event.metadata as Record<string, unknown> | undefined;
                const trace = normalizeAgenticTrace(event.agentic_trace)
                  ?? normalizeAgenticTrace(metadata?.agentic_trace);
                if (trace) {
                  setAgenticTrace(trace);
                }
                if (!receivedTeammateStreamingEvent && Array.isArray(event.teammate_responses)) {
                  for (const item of event.teammate_responses) {
                    if (!item || typeof item !== 'object') continue;
                    const row = item as Record<string, unknown>;
                    const contentText = typeof row.response === 'string' ? row.response : '';
                    if (!contentText) continue;
                    addMessage({
                      speaker: (typeof row.name === 'string' ? row.name : 'Teammate'),
                      content: contentText,
                      type: 'teammate',
                      metadata: {
                        reaction: typeof row.reaction === 'string' ? row.reaction : undefined,
                        character_id: typeof row.character_id === 'string' ? row.character_id : undefined,
                      },
                    });
                  }
                }
                // NPC 响应 fallback
                if (!receivedNpcResponseEvent && Array.isArray(event.npc_responses)) {
                  for (const item of event.npc_responses) {
                    if (!item || typeof item !== 'object') continue;
                    const row = item as Record<string, unknown>;
                    const dialogue = typeof row.dialogue === 'string' ? row.dialogue : '';
                    if (!dialogue) continue;
                    addMessage({
                      speaker: (typeof row.name === 'string' ? row.name : 'NPC'),
                      content: dialogue,
                      type: 'npc',
                      metadata: {
                        character_id: typeof row.character_id === 'string' ? row.character_id : undefined,
                      },
                    });
                  }
                }
                if (
                  event.available_actions &&
                  (event.available_actions as GameAction[]).length > 0
                ) {
                  setAvailableActions(event.available_actions as GameAction[]);
                }
                queryClient.invalidateQueries({ queryKey: ['location'] });
                queryClient.invalidateQueries({ queryKey: ['availableMaps', worldId, sessionId] });
                queryClient.invalidateQueries({ queryKey: ['gameTime'] });
                queryClient.invalidateQueries({ queryKey: ['party'] });
                queryClient.invalidateQueries({ queryKey: ['narrativeProgress', worldId, sessionId] });
                queryClient.invalidateQueries({ queryKey: ['flowBoard', worldId, sessionId] });
                queryClient.invalidateQueries({ queryKey: ['currentPlan', worldId, sessionId] });
                queryClient.invalidateQueries({ queryKey: ['sessionHistory', worldId, sessionId] });
                break;
              }

              case 'error':
                addSystemMessage(
                  (event.error as string) || '发生了未知错误',
                );
                break;
            }
          },
          abortRef.current.signal,
        );
      } catch (err) {
        if ((err as Error).name !== 'AbortError') {
          console.error('Stream game input error:', err);
          addSystemMessage('请求失败，请重试');
        }
      } finally {
        setLoading(false);
      }
    },
    [
      worldId,
      sessionId,
      addPlayerMessage,
      addMessage,
      setLoading,
      addSystemMessage,
      startStreamingMessage,
      appendToStreamingMessage,
      finalizeStreamingMessage,
      updateFromStateDelta,
      setAvailableActions,
      setNarrativeSnapshot,
      setImageData,
      setAgenticTrace,
      appendAgenticToolCall,
      setDiceRoll,
      queryClient,
    ],
  );

  const abort = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return { sendInput, isLoading, abort };
}
