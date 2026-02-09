/**
 * Core game input mutation hooks
 *
 * useGameInput      — legacy non-streaming (POST /input)
 * useStreamGameInput — SSE streaming   (POST /input/stream)
 */
import { useCallback, useRef } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { sendGameInputV2 } from '../gameApi';
import { streamGameInput } from '../sseClient';
import { useGameStore } from '../../stores/gameStore';
import { useChatStore } from '../../stores/chatStore';
import type {
  PlayerInputRequest,
  CoordinatorResponse,
  GameAction,
  StateDelta,
  CoordinatorChapterInfo,
} from '../../types';

// =============================================================================
// Legacy non-streaming hook (kept for backward-compat)
// =============================================================================

interface UseGameInputOptions {
  onSuccess?: (response: CoordinatorResponse) => void;
  onError?: (error: Error) => void;
}

export function useGameInput(options?: UseGameInputOptions) {
  const queryClient = useQueryClient();
  const {
    worldId,
    sessionId,
    setAvailableActions,
    setNarrativeSnapshot,
    updateFromStateDelta,
  } =
    useGameStore();
  const { addPlayerMessage, addGMResponseV2, setLoading, addSystemMessage } = useChatStore();

  const mutation = useMutation({
    mutationFn: async (content: string): Promise<CoordinatorResponse> => {
      if (!worldId || !sessionId) {
        throw new Error('No active session');
      }

      const request: PlayerInputRequest = {
        input: content,
      };

      return sendGameInputV2(worldId, sessionId, request);
    },

    onMutate: (content: string) => {
      // Optimistic update - add player message immediately
      addPlayerMessage(content);
      setLoading(true);
    },

    onSuccess: (response: CoordinatorResponse) => {
      // Add GM response and teammate responses to chat
      addGMResponseV2(response);

      // Update game state from state_delta
      if (response.state_delta) {
        updateFromStateDelta(response.state_delta);
      }
      setNarrativeSnapshot(
        response.chapter_info ?? null,
        response.story_events ?? [],
        response.pacing_action ?? null,
      );

      // Invalidate location, time, and party queries to refresh sidebar
      queryClient.invalidateQueries({ queryKey: ['location'] });
      queryClient.invalidateQueries({ queryKey: ['availableMaps', worldId, sessionId] });
      queryClient.invalidateQueries({ queryKey: ['gameTime'] });
      queryClient.invalidateQueries({ queryKey: ['party'] });
      queryClient.invalidateQueries({ queryKey: ['narrativeProgress', worldId, sessionId] });
      queryClient.invalidateQueries({ queryKey: ['flowBoard', worldId, sessionId] });
      queryClient.invalidateQueries({ queryKey: ['currentPlan', worldId, sessionId] });
      queryClient.invalidateQueries({ queryKey: ['sessionHistory', worldId, sessionId] });

      // Update available actions
      if (response.available_actions && response.available_actions.length > 0) {
        setAvailableActions(response.available_actions as GameAction[]);
      }

      setLoading(false);
      options?.onSuccess?.(response);
    },

    onError: (error: Error) => {
      setLoading(false);
      console.error('Game input error:', error);

      // 向聊天显示错误消息（区分超时/网络/HTTP三类错误）
      let errorMessage = '发生了一些问题，请重新尝试。';
      const axiosErr = error as { code?: string; response?: { data?: { detail?: string | unknown } }; message?: string };

      if (axiosErr.code === 'ECONNABORTED' || axiosErr.message?.includes('timeout')) {
        errorMessage = '请求超时了，服务器可能正在忙碌，请稍后再试。';
      } else if (axiosErr.code === 'ERR_NETWORK') {
        errorMessage = '无法连接到服务器，请检查后端是否正在运行。';
      } else if (axiosErr.response?.data?.detail) {
        const detail = axiosErr.response.data.detail;
        errorMessage = typeof detail === 'string'
          ? `系统出了点小问题：${detail}`
          : `系统出了点小问题：${JSON.stringify(detail)}`;
      }
      addSystemMessage(errorMessage);

      options?.onError?.(error);
    },
  });

  return {
    sendInput: mutation.mutate,
    sendInputAsync: mutation.mutateAsync,
    isLoading: mutation.isPending,
    error: mutation.error,
    reset: mutation.reset,
  };
}

// =============================================================================
// SSE streaming hook (preferred)
// =============================================================================

export function useStreamGameInput() {
  const queryClient = useQueryClient();
  const {
    worldId,
    sessionId,
    setAvailableActions,
    setNarrativeSnapshot,
    updateFromStateDelta,
  } =
    useGameStore();
  const {
    addPlayerMessage,
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
      abortRef.current = new AbortController();

      let currentGmId: string | null = null;
      const teammateIds: Record<string, string> = {};

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
                const charId = event.character_id as string;
                const name = event.name as string;
                teammateIds[charId] = startStreamingMessage(name, 'teammate', {
                  character_id: charId,
                });
                break;
              }

              case 'teammate_chunk': {
                const tmId = teammateIds[event.character_id as string];
                if (tmId) appendToStreamingMessage(tmId, event.text as string);
                break;
              }

              case 'teammate_end': {
                const endId = teammateIds[event.character_id as string];
                if (endId) {
                  finalizeStreamingMessage(endId, event.response as string);
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
      setLoading,
      addSystemMessage,
      startStreamingMessage,
      appendToStreamingMessage,
      finalizeStreamingMessage,
      updateFromStateDelta,
      setAvailableActions,
      setNarrativeSnapshot,
      queryClient,
    ],
  );

  const abort = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return { sendInput, isLoading, abort };
}
