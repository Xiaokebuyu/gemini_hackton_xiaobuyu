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
  CombatState,
  DiceRoll,
  TriggerCombatRequest,
  TriggerCombatResponse,
  CombatResolveRequest,
  CombatResolveResponse,
} from '../../types';

/** Try to extract a DiceRoll from combat action_result */
function extractCombatDice(response: CombatActionResponse): DiceRoll | null {
  const ar = response.action_result;
  if (!ar || typeof ar !== 'object') return null;
  const roll = (ar as Record<string, unknown>).roll;
  const diceRoll = (ar as Record<string, unknown>).dice_roll;
  const source = diceRoll ?? roll;
  if (!source || typeof source !== 'object') return null;
  const dr = source as Record<string, unknown>;
  // Support both { roll: number } and { result: number } shapes
  const rawResult = typeof dr.roll === 'number' ? dr.roll : (typeof dr.result === 'number' ? dr.result : null);
  if (rawResult === null) return null;
  return {
    roll_type: typeof dr.roll_type === 'string' ? dr.roll_type as DiceRoll['roll_type'] : 'd20',
    result: rawResult,
    modifier: typeof dr.modifier === 'number' ? dr.modifier : 0,
    total: typeof dr.total === 'number' ? dr.total : rawResult,
    is_critical: typeof dr.is_critical === 'boolean' ? dr.is_critical : false,
    is_fumble: typeof dr.is_fumble === 'boolean' ? dr.is_fumble : false,
  };
}

export function useCombatAction() {
  const queryClient = useQueryClient();
  const { worldId, sessionId, setDiceRoll } = useGameStore();

  const mutation = useMutation<CombatActionResponse, Error, CombatActionRequest>({
    mutationFn: async (action: CombatActionRequest) => {
      if (!worldId || !sessionId) {
        throw new Error('No active session');
      }
      return executeCombatAction(worldId, sessionId, action);
    },

    onSuccess: (response) => {
      // Extract and display dice roll if present
      const dice = extractCombatDice(response);
      if (dice) {
        setDiceRoll(dice);
      }

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
      setCombatState(response.combat_state as unknown as CombatState);

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
