/**
 * Combat state and action hooks
 */
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  triggerCombat,
  executeCombatAction,
  resolveCombat,
} from '../combatApi';
import { useGameStore } from '../../stores/gameStore';
import { useCombatStore } from '../../stores/combatStore';
import type {
  CombatActionRequest,
  CombatActionResponse,
  TriggerCombatRequest,
  TriggerCombatResponse,
  CombatResolveRequest,
  CombatResolveResponse,
} from '../../types';

export function useCombatAction() {
  const queryClient = useQueryClient();
  const { worldId, sessionId } = useGameStore();

  const mutation = useMutation<CombatActionResponse, Error, CombatActionRequest>({
    mutationFn: async (action: CombatActionRequest) => {
      if (!worldId || !sessionId) {
        throw new Error('No active session');
      }
      return executeCombatAction(worldId, sessionId, action);
    },

    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ['combat', worldId, sessionId],
      });
    },

    onError: (error: Error) => {
      console.error('Combat action error:', error);
    },
  });

  return {
    executeAction: mutation.mutate,
    executeActionAsync: mutation.mutateAsync,
    isLoading: mutation.isPending,
    error: mutation.error,
  };
}

export function useStartCombat() {
  const queryClient = useQueryClient();
  const { worldId, sessionId, setCombatId } = useGameStore();
  const { setCombatState } = useCombatStore();

  const mutation = useMutation<TriggerCombatResponse, Error, TriggerCombatRequest>({
    mutationFn: async (request: TriggerCombatRequest) => {
      if (!worldId || !sessionId) {
        throw new Error('No active session');
      }
      return triggerCombat(worldId, sessionId, request);
    },

    onSuccess: (response: TriggerCombatResponse) => {
      setCombatId(response.combat_id);
      // combat_state from trigger is a Dict, adapt to CombatState if needed
      setCombatState(response.combat_state as any);

      queryClient.invalidateQueries({
        queryKey: ['combat', worldId, sessionId],
      });
    },
  });

  return {
    startCombat: mutation.mutate,
    startCombatAsync: mutation.mutateAsync,
    isLoading: mutation.isPending,
    error: mutation.error,
  };
}

export function useEndCombat() {
  const queryClient = useQueryClient();
  const { worldId, sessionId, setCombatId } = useGameStore();
  const { clearCombat } = useCombatStore();

  const mutation = useMutation<CombatResolveResponse, Error, CombatResolveRequest | undefined>({
    mutationFn: async (request?: CombatResolveRequest) => {
      if (!worldId || !sessionId) {
        throw new Error('No active session');
      }
      return resolveCombat(worldId, sessionId, request);
    },

    onSuccess: () => {
      setCombatId(null);
      clearCombat();

      queryClient.invalidateQueries({
        queryKey: ['combat'],
      });
    },
  });

  return {
    endCombat: mutation.mutate,
    endCombatAsync: mutation.mutateAsync,
    isLoading: mutation.isPending,
    error: mutation.error,
  };
}
