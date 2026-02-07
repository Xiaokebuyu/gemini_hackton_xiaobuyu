/**
 * Application constants
 */

// API
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
export const API_TIMEOUT = 60000; // 60 seconds

// UI
export const LEFT_PANEL_WIDTH = 280;
export const RIGHT_PANEL_WIDTH = 320;
export const TYPEWRITER_SPEED = 20; // ms per character

// Game
export const DEFAULT_WORLD_ID = 'goblin_slayer';
export const MAX_PARTY_SIZE = 4;
export const MESSAGE_HISTORY_LIMIT = 100;

// Time periods
export const TIME_PERIODS = {
  dawn: { start: 5, end: 8, label: 'Dawn' },
  day: { start: 8, end: 18, label: 'Day' },
  dusk: { start: 18, end: 21, label: 'Dusk' },
  night: { start: 21, end: 5, label: 'Night' },
};

// Combat
export const DISTANCE_BANDS = ['ENGAGED', 'CLOSE', 'NEAR', 'FAR', 'DISTANT'] as const;
export const COMBAT_ACTION_TYPES = [
  'ATTACK',
  'OFFHAND',
  'THROW',
  'SHOVE',
  'SPELL',
  'DEFEND',
  'MOVE',
  'DASH',
  'DISENGAGE',
  'USE_ITEM',
  'FLEE',
  'END_TURN',
] as const;

// Danger levels
export const DANGER_LEVELS = {
  low: { color: 'text-g-danger-low', label: 'Safe' },
  medium: { color: 'text-g-danger-medium', label: 'Moderate' },
  high: { color: 'text-g-danger-high', label: 'Dangerous' },
  extreme: { color: 'text-g-danger-extreme', label: 'Deadly' },
};

// Keyboard shortcuts
export const KEYBOARD_SHORTCUTS = {
  sendMessage: { key: 'Enter', description: 'Send message' },
  newLine: { key: 'Shift+Enter', description: 'New line' },
  toggleLeftPanel: { key: '[', ctrl: true, description: 'Toggle left panel' },
  toggleRightPanel: { key: ']', ctrl: true, description: 'Toggle right panel' },
};

// Local storage keys
export const STORAGE_KEYS = {
  gameSession: 'game-storage',
  uiPreferences: 'ui-storage',
  chatHistory: 'chat-history',
};
