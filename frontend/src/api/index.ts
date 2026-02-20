/**
 * API exports
 */

export { default as apiClient } from './client';
export * from './gameApi';
export * from './combatApi';

// Hooks
export { useStreamGameInput } from './hooks/useGameInput';
export { streamGameInput, streamPrivateChat } from './sseClient';
export { usePrivateChat } from './hooks/usePrivateChat';
export { useLocation, useGameTime } from './hooks/useLocation';
export { useParty } from './hooks/useParty';
export {
  useCombatAction,
  useStartCombat,
  useEndCombat,
} from './hooks/useCombat';
