/**
 * Combat state and action hooks
 */
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  getCombatState,
  executeCombatAction,
  startCombat,
  endCombat,
} from '../combatApi';
import { useGameStore } from '../../stores/gameStore';
import { useCombatStore } from '../../stores/combatStore';
import type {
  CombatState,
  CombatActionRequest,
  CombatActionResponse,
} from '../../types';

export function useCombatState() {
  const { worldId, sessionId, combatId } = useGameStore();

  const combatQuery = useQuery<CombatState>({
    queryKey: ['combat', worldId, sessionId, combatId],
    queryFn: () => {
      if (!worldId || !sessionId || !combatId) {
        throw new Error('No active combat');
      }
      return getCombatState(worldId, sessionId, combatId);
    },
    enabled: !!worldId && !!sessionId && !!combatId,
    staleTime: 0, // Always refetch
    refetchInterval: false,
  });

  return {
    combatState: combatQuery.data,
    isLoading: combatQuery.isLoading,
    error: combatQuery.error,
    refetch: combatQuery.refetch,
  };
}

export function useCombatAction() {
  const queryClient = useQueryClient();
  const { worldId, sessionId, combatId } = useGameStore();
  const { updateCombatState } = useCombatStore();

  const mutation = useMutation<CombatActionResponse, Error, CombatActionRequest>({
    mutationFn: async (action: CombatActionRequest) => {
      if (!worldId || !sessionId || !combatId) {
        throw new Error('No active combat');
      }
      return executeCombatAction(worldId, sessionId, combatId, action);
    },

    onSuccess: (response: CombatActionResponse) => {
      // Update combat state in store
      updateCombatState(response.combat_state);

      // Invalidate combat query
      queryClient.invalidateQueries({
        queryKey: ['combat', worldId, sessionId, combatId],
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

  const mutation = useMutation({
    mutationFn: async (targetIds: string[]) => {
      if (!worldId || !sessionId) {
        throw new Error('No active session');
      }
      return startCombat(worldId, sessionId, targetIds);
    },

    onSuccess: (response) => {
      setCombatId(response.combat_id);
      setCombatState(response.combat_state);

      queryClient.invalidateQueries({
        queryKey: ['combat', worldId, sessionId],
      });
    },
  });

  return {
    startCombat: mutation.mutate,
    isLoading: mutation.isPending,
    error: mutation.error,
  };
}

export function useEndCombat() {
  const queryClient = useQueryClient();
  const { worldId, sessionId, combatId, setCombatId } = useGameStore();
  const { clearCombat } = useCombatStore();

  const mutation = useMutation({
    mutationFn: async (flee: boolean = false) => {
      if (!worldId || !sessionId || !combatId) {
        throw new Error('No active combat');
      }
      return endCombat(worldId, sessionId, combatId, flee);
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
    isLoading: mutation.isPending,
    error: mutation.error,
  };
}
