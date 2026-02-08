/**
 * Game State Store
 */
import { create } from 'zustand';
import { devtools, persist } from 'zustand/middleware';
import type {
  GameState,
  GameTimeState,
  GameTimeResponse,
  LocationResponse,
  GameAction,
  StateDelta,
  Party,
} from '../types';

interface GameStoreState {
  // Session info
  worldId: string | null;
  sessionId: string | null;

  // Location
  location: LocationResponse | null;
  subLocation: string | null;

  // Time
  gameTime: GameTimeState;

  // Dialogue
  activeDialogueNpc: string | null;

  // Combat
  combatId: string | null;

  // Party
  party: Party | null;

  // Actions
  availableActions: GameAction[];

  // Actions
  setSession: (worldId: string, sessionId: string) => void;
  clearSession: () => void;
  setLocation: (location: LocationResponse) => void;
  setSubLocation: (subLocation: string | null) => void;
  setGameTime: (time: GameTimeState | GameTimeResponse) => void;
  setActiveDialogueNpc: (npcId: string | null) => void;
  setCombatId: (combatId: string | null) => void;
  setParty: (party: Party | null) => void;
  setAvailableActions: (actions: GameAction[]) => void;
  updateFromStateDelta: (delta: StateDelta) => void;
  updateFromGameState: (state: GameState) => void;
}

const initialGameTime: GameTimeState = {
  day: 1,
  hour: 8,
  minute: 0,
  period: 'day',
  formatted: '第1天 08:00',
};

export const useGameStore = create<GameStoreState>()(
  devtools(
    persist(
      (set) => ({
        // Initial state
        worldId: null,
        sessionId: null,
        location: null,
        subLocation: null,
        gameTime: initialGameTime,
        activeDialogueNpc: null,
        combatId: null,
        party: null,
        availableActions: [],

        // Actions
        setSession: (worldId: string, sessionId: string) => {
          // Switching session must clear volatile runtime state to avoid leaking
          // previous session data into the new session before fresh data arrives.
          set({
            worldId,
            sessionId,
            location: null,
            subLocation: null,
            gameTime: initialGameTime,
            activeDialogueNpc: null,
            combatId: null,
            party: null,
            availableActions: [],
          });
        },

        clearSession: () => {
          set({
            worldId: null,
            sessionId: null,
            location: null,
            subLocation: null,
            gameTime: initialGameTime,
            activeDialogueNpc: null,
            combatId: null,
            party: null,
            availableActions: [],
          });
        },

        setLocation: (location: LocationResponse) => {
          set({ location });
        },

        setSubLocation: (subLocation: string | null) => {
          set({ subLocation });
        },

        setGameTime: (time: GameTimeState | GameTimeResponse) => {
          // Normalize to GameTimeState format
          const gameTime: GameTimeState = {
            day: time.day,
            hour: time.hour,
            minute: time.minute,
            period: time.period as 'dawn' | 'day' | 'dusk' | 'night' | null,
            formatted: time.formatted,
          };
          set({ gameTime });
        },

        setActiveDialogueNpc: (activeDialogueNpc: string | null) => {
          set({ activeDialogueNpc });
        },

        setCombatId: (combatId: string | null) => {
          set({ combatId });
        },

        setParty: (party: Party | null) => {
          set({ party });
        },

        setAvailableActions: (availableActions: GameAction[]) => {
          set({ availableActions });
        },

        updateFromStateDelta: (delta: StateDelta) => {
          const changes = delta.changes;

          set((state) => ({
            ...state,
            ...(changes.player_location !== undefined && {
              location: changes.player_location as LocationResponse | null,
            }),
            ...(changes.sub_location !== undefined && {
              subLocation: changes.sub_location as string | null,
            }),
            ...(changes.game_time !== undefined && {
              gameTime: changes.game_time as GameTimeState,
            }),
            ...(changes.active_dialogue_npc !== undefined && {
              activeDialogueNpc: changes.active_dialogue_npc as string | null,
            }),
            ...(changes.combat_id !== undefined && {
              combatId: changes.combat_id as string | null,
            }),
          }));
        },

        updateFromGameState: (gameState: GameState) => {
          set({
            worldId: gameState.world_id,
            sessionId: gameState.session_id,
            subLocation: gameState.sub_location,
            gameTime: gameState.game_time,
            activeDialogueNpc: gameState.active_dialogue_npc,
            combatId: gameState.combat_id,
          });
        },
      }),
      {
        name: 'game-storage',
        partialize: (state) => ({
          worldId: state.worldId,
          sessionId: state.sessionId,
        }),
      }
    ),
    { name: 'GameStore' }
  )
);
