/**
 * Core game input mutation hook
 *
 * 对应后端 POST /api/gm/{world_id}/sessions/{session_id}/input_v2
 */
import { useMutation } from '@tanstack/react-query';
import { sendGameInputV2 } from '../gameApi';
import { useGameStore } from '../../stores/gameStore';
import { useChatStore } from '../../stores/chatStore';
import type { PlayerInputRequest, CoordinatorResponse, GameAction } from '../../types';

interface UseGameInputOptions {
  onSuccess?: (response: CoordinatorResponse) => void;
  onError?: (error: Error) => void;
}

export function useGameInput(options?: UseGameInputOptions) {
  const { worldId, sessionId, setAvailableActions, updateFromStateDelta } =
    useGameStore();
  const { addPlayerMessage, addGMResponseV2, setLoading } = useChatStore();

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
