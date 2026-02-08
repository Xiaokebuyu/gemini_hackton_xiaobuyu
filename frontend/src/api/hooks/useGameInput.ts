/**
 * Core game input mutation hook
 *
 * 对应后端 POST /api/gm/{world_id}/sessions/{session_id}/input_v2
 */
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { sendGameInputV2 } from '../gameApi';
import { useGameStore } from '../../stores/gameStore';
import { useChatStore } from '../../stores/chatStore';
import type { PlayerInputRequest, CoordinatorResponse, GameAction } from '../../types';

interface UseGameInputOptions {
  onSuccess?: (response: CoordinatorResponse) => void;
  onError?: (error: Error) => void;
}

export function useGameInput(options?: UseGameInputOptions) {
  const queryClient = useQueryClient();
  const { worldId, sessionId, setAvailableActions, updateFromStateDelta } =
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

      // Invalidate location, time, and party queries to refresh sidebar
      queryClient.invalidateQueries({ queryKey: ['location'] });
      queryClient.invalidateQueries({ queryKey: ['gameTime'] });
      queryClient.invalidateQueries({ queryKey: ['party'] });

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
