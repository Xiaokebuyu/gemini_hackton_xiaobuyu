/**
 * Combat State Store
 */
import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { v4 as uuidv4 } from 'uuid';
import type {
  CombatState,
  Combatant,
  CombatActionType,
  CombatLogEntry,
  DiceRoll,
} from '../types';

interface CombatStoreState {
  // Combat state
  combatState: CombatState | null;
  isActive: boolean;

  // UI state
  selectedAction: CombatActionType | null;
  selectedTarget: string | null;
  isAnimating: boolean;
  lastRoll: DiceRoll | null;

  // Actions
  setCombatState: (state: CombatState) => void;
  updateCombatState: (state: Partial<CombatState>) => void;
  clearCombat: () => void;

  // Selection
  selectAction: (action: CombatActionType | null) => void;
  selectTarget: (targetId: string | null) => void;
  clearSelection: () => void;

  // Animation
  setAnimating: (animating: boolean) => void;
  setLastRoll: (roll: DiceRoll | null) => void;

  // Log
  addLogEntry: (entry: Omit<CombatLogEntry, 'id' | 'timestamp'>) => void;

  // Helpers
  getPlayer: () => Combatant | undefined;
  getAllies: () => Combatant[];
  getEnemies: () => Combatant[];
  getActiveCombatant: () => Combatant | undefined;
  canAct: () => boolean;
}

export const useCombatStore = create<CombatStoreState>()(
  devtools(
    (set, get) => ({
      // Initial state
      combatState: null,
      isActive: false,
      selectedAction: null,
      selectedTarget: null,
      isAnimating: false,
      lastRoll: null,

      // Actions
      setCombatState: (combatState: CombatState) => {
        set({
          combatState,
          isActive: combatState.is_active,
        });
      },

      updateCombatState: (partial: Partial<CombatState>) => {
        const current = get().combatState;
        if (current) {
          set({
            combatState: { ...current, ...partial },
            isActive: partial.is_active ?? current.is_active,
          });
        }
      },

      clearCombat: () => {
        set({
          combatState: null,
          isActive: false,
          selectedAction: null,
          selectedTarget: null,
          isAnimating: false,
          lastRoll: null,
        });
      },

      // Selection
      selectAction: (action: CombatActionType | null) => {
        set({ selectedAction: action });
      },

      selectTarget: (targetId: string | null) => {
        set({ selectedTarget: targetId });
      },

      clearSelection: () => {
        set({
          selectedAction: null,
          selectedTarget: null,
        });
      },

      // Animation
      setAnimating: (isAnimating: boolean) => {
        set({ isAnimating });
      },

      setLastRoll: (lastRoll: DiceRoll | null) => {
        set({ lastRoll });
      },

      // Log
      addLogEntry: (entry) => {
        const combatState = get().combatState;
        if (combatState) {
          const newEntry: CombatLogEntry = {
            ...entry,
            id: uuidv4(),
            timestamp: new Date(),
          };
          set({
            combatState: {
              ...combatState,
              combat_log: [...combatState.combat_log, newEntry],
            },
          });
        }
      },

      // Helpers
      getPlayer: () => {
        const state = get().combatState;
        return state?.combatants.find((c) => c.is_player);
      },

      getAllies: () => {
        const state = get().combatState;
        return state?.combatants.filter((c) => c.is_ally && !c.is_player) || [];
      },

      getEnemies: () => {
        const state = get().combatState;
        return state?.combatants.filter((c) => !c.is_ally) || [];
      },

      getActiveCombatant: () => {
        const state = get().combatState;
        if (!state?.active_combatant_id) return undefined;
        return state.combatants.find((c) => c.id === state.active_combatant_id);
      },

      canAct: () => {
        const state = get().combatState;
        const player = get().getPlayer();
        return (
          state?.is_active &&
          state?.active_combatant_id === player?.id &&
          !get().isAnimating
        ) ?? false;
      },
    }),
    { name: 'CombatStore' }
  )
);
