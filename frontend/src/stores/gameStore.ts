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
  MapGraphState,
  GameAction,
  StateDelta,
  CoordinatorChapterInfo,
  Party,
} from '../types';

interface GameStoreState {
  // Session info
  worldId: string | null;
  sessionId: string | null;

  // Location
  location: LocationResponse | null;
  subLocation: string | null;
  mapGraph: MapGraphState;

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
  latestChapterInfo: CoordinatorChapterInfo | null;
  latestStoryEvents: string[];
  latestPacingAction: string | null;

  // Actions
  setSession: (worldId: string, sessionId: string) => void;
  clearSession: () => void;
  setLocation: (location: LocationResponse) => void;
  mergeLocationIntoMapGraph: (
    location: LocationResponse,
    availableMapIds?: string[],
    allUnlocked?: boolean,
  ) => void;
  resetMapGraph: () => void;
  setSubLocation: (subLocation: string | null) => void;
  setGameTime: (time: GameTimeState | GameTimeResponse) => void;
  setActiveDialogueNpc: (npcId: string | null) => void;
  setCombatId: (combatId: string | null) => void;
  setParty: (party: Party | null) => void;
  setAvailableActions: (actions: GameAction[]) => void;
  setNarrativeSnapshot: (
    chapterInfo: CoordinatorChapterInfo | null,
    storyEvents?: string[],
    pacingAction?: string | null,
  ) => void;
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

const MAX_MAP_GRAPH_NODES = 40;
const initialMapGraph: MapGraphState = {
  nodes: {},
  edges: {},
};

function isLocationResponse(value: unknown): value is LocationResponse {
  if (!value || typeof value !== 'object') return false;
  return typeof (value as { location_id?: unknown }).location_id === 'string';
}

function isDangerLevel(value: unknown): value is 'low' | 'medium' | 'high' | 'extreme' {
  return value === 'low' || value === 'medium' || value === 'high' || value === 'extreme';
}

function resolveNodeUnlocked(
  nodeId: string,
  availableSet: Set<string> | null,
  allUnlocked: boolean,
  fallback: boolean,
): boolean {
  if (allUnlocked) return true;
  if (!availableSet) return fallback;
  return availableSet.has(nodeId);
}

function mergeMapGraphFromLocation(
  previousGraph: MapGraphState,
  location: LocationResponse,
  availableMapIds?: string[],
  allUnlocked?: boolean,
): MapGraphState {
  const nodes: MapGraphState['nodes'] = { ...previousGraph.nodes };
  const edges: MapGraphState['edges'] = {};
  const now = Date.now();
  const currentId = location.location_id;
  const currentName = location.location_name || location.location_id;

  const normalizedMapIds = Array.isArray(availableMapIds)
    ? availableMapIds.filter((id) => typeof id === 'string' && id.length > 0)
    : [];
  const availableSet = normalizedMapIds.length > 0 ? new Set(normalizedMapIds) : null;
  const allMapsUnlocked = Boolean(allUnlocked) || Boolean(availableSet?.has('*'));
  const reachableIds = new Set<string>();

  for (const edge of Object.values(previousGraph.edges)) {
    edges[edge.id] = { ...edge, is_active: false };
  }

  const currentNodePrev = nodes[currentId];
  nodes[currentId] = {
    id: currentId,
    name: currentName || currentNodePrev?.name || currentId,
    danger_level: isDangerLevel(location.danger_level)
      ? location.danger_level
      : currentNodePrev?.danger_level || 'low',
    is_current: true,
    is_unlocked: true,
    is_reachable_from_current: false,
    last_seen_at: now,
  };

  const destinations = Array.isArray(location.available_destinations)
    ? location.available_destinations
    : [];
  for (const dest of destinations) {
    const destId = dest.location_id || dest.name;
    if (!destId) continue;
    reachableIds.add(destId);

    const destPrev = nodes[destId];
    const fallbackUnlocked = Boolean(destPrev?.is_unlocked) || Boolean(dest.is_accessible);
    nodes[destId] = {
      id: destId,
      name: dest.name || destPrev?.name || destId,
      danger_level: isDangerLevel(dest.danger_level)
        ? dest.danger_level
        : destPrev?.danger_level || 'low',
      is_current: false,
      is_unlocked: resolveNodeUnlocked(destId, availableSet, allMapsUnlocked, fallbackUnlocked),
      is_reachable_from_current: true,
      last_seen_at: now,
    };

    const edgeId = `${currentId}->${destId}`;
    const previousEdge = edges[edgeId];
    edges[edgeId] = {
      id: edgeId,
      from: currentId,
      to: destId,
      travel_time: dest.distance || previousEdge?.travel_time,
      is_active: true,
    };
  }

  for (const [nodeId, node] of Object.entries(nodes)) {
    const isCurrent = nodeId === currentId;
    const isReachable = reachableIds.has(nodeId);
    const fallbackUnlocked = isCurrent || isReachable || node.is_unlocked;
    nodes[nodeId] = {
      ...node,
      is_current: isCurrent,
      is_reachable_from_current: isReachable,
      is_unlocked: isCurrent
        ? true
        : resolveNodeUnlocked(nodeId, availableSet, allMapsUnlocked, fallbackUnlocked),
    };
  }

  const nodeEntries = Object.values(nodes);
  if (nodeEntries.length > MAX_MAP_GRAPH_NODES) {
    const removable = nodeEntries
      .filter((node) => !node.is_current)
      .sort((a, b) => a.last_seen_at - b.last_seen_at);
    const overflowCount = nodeEntries.length - MAX_MAP_GRAPH_NODES;
    for (const node of removable.slice(0, overflowCount)) {
      delete nodes[node.id];
    }
  }

  for (const [edgeId, edge] of Object.entries(edges)) {
    if (!nodes[edge.from] || !nodes[edge.to]) {
      delete edges[edgeId];
    }
  }

  return { nodes, edges };
}

export const useGameStore = create<GameStoreState>()(
  devtools(
    persist(
      (set) => ({
        // Initial state
        worldId: null,
        sessionId: null,
        location: null,
        subLocation: null,
        mapGraph: initialMapGraph,
        gameTime: initialGameTime,
        activeDialogueNpc: null,
        combatId: null,
        party: null,
        availableActions: [],
        latestChapterInfo: null,
        latestStoryEvents: [],
        latestPacingAction: null,

        // Actions
        setSession: (worldId: string, sessionId: string) => {
          // Switching session must clear volatile runtime state to avoid leaking
          // previous session data into the new session before fresh data arrives.
          set({
            worldId,
            sessionId,
            location: null,
            subLocation: null,
            mapGraph: initialMapGraph,
            gameTime: initialGameTime,
            activeDialogueNpc: null,
            combatId: null,
            party: null,
            availableActions: [],
            latestChapterInfo: null,
            latestStoryEvents: [],
            latestPacingAction: null,
          });
        },

        clearSession: () => {
          set({
            worldId: null,
            sessionId: null,
            location: null,
            subLocation: null,
            mapGraph: initialMapGraph,
            gameTime: initialGameTime,
            activeDialogueNpc: null,
            combatId: null,
            party: null,
            availableActions: [],
            latestChapterInfo: null,
            latestStoryEvents: [],
            latestPacingAction: null,
          });
        },

        setLocation: (location: LocationResponse) => {
          set((state) => ({
            location,
            mapGraph: mergeMapGraphFromLocation(state.mapGraph, location),
          }));
        },

        mergeLocationIntoMapGraph: (
          location: LocationResponse,
          availableMapIds?: string[],
          allUnlocked?: boolean,
        ) => {
          set((state) => ({
            location,
            mapGraph: mergeMapGraphFromLocation(
              state.mapGraph,
              location,
              availableMapIds,
              allUnlocked,
            ),
          }));
        },

        resetMapGraph: () => {
          set({ mapGraph: initialMapGraph });
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

        setNarrativeSnapshot: (
          chapterInfo: CoordinatorChapterInfo | null,
          storyEvents: string[] = [],
          pacingAction: string | null = null,
        ) => {
          set({
            latestChapterInfo: chapterInfo,
            latestStoryEvents: storyEvents,
            latestPacingAction: pacingAction,
          });
        },

        updateFromStateDelta: (delta: StateDelta) => {
          const changes = delta.changes;
          const locationChange = changes.player_location;
          const locationFromDelta = isLocationResponse(locationChange) ? locationChange : null;

          set((state) => ({
            ...state,
            ...(changes.player_location !== undefined && {
              location:
                locationChange === null
                  ? null
                  : locationFromDelta
                  ? locationFromDelta
                  : state.location,
            }),
            ...(locationFromDelta && {
              mapGraph: mergeMapGraphFromLocation(state.mapGraph, locationFromDelta),
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
