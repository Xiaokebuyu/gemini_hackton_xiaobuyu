/**
 * Game State Store
 */
import { create } from 'zustand';
import { devtools, persist } from 'zustand/middleware';
import type {
  AgenticTracePayload,
  CoordinatorImageData,
  XPStateDelta,
  PlayerHPStateDelta,
  GameState,
  GameTimeState,
  GameTimeResponse,
  LocationResponse,
  MapGraphState,
  GameAction,
  StateDelta,
  CoordinatorChapterInfo,
  Party,
  DispositionsMap,
  DiceRoll,
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
  latestStoryEventId: string | null;
  latestPacingAction: string | null;
  latestImageData: CoordinatorImageData | null;
  latestAgenticTrace: AgenticTracePayload | null;
  latestPartyUpdate: Record<string, unknown> | null;
  latestInventoryUpdate: Record<string, unknown> | null;
  playerHp: PlayerHPStateDelta | null;
  xpSnapshot: XPStateDelta | null;
  inventoryItems: Record<string, unknown>[];
  inventoryItemCount: number;
  dispositions: DispositionsMap;

  // Dice
  pendingDiceRoll: DiceRoll | null;

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
  setImageData: (imageData: CoordinatorImageData | null) => void;
  setAgenticTrace: (trace: AgenticTracePayload | null) => void;
  appendAgenticToolCall: (call: AgenticTracePayload['tool_calls'][0]) => void;
  setDispositions: (d: DispositionsMap) => void;
  setDiceRoll: (roll: DiceRoll | null) => void;
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

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : null;
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function asString(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

const teammateRoles = new Set([
  'warrior',
  'healer',
  'mage',
  'rogue',
  'support',
  'scout',
  'scholar',
]);

function normalizeTeammateRole(value: unknown): 'warrior' | 'healer' | 'mage' | 'rogue' | 'support' | 'scout' | 'scholar' {
  return typeof value === 'string' && teammateRoles.has(value)
    ? (value as 'warrior' | 'healer' | 'mage' | 'rogue' | 'support' | 'scout' | 'scholar')
    : 'support';
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
        latestStoryEventId: null,
        latestPacingAction: null,
        latestImageData: null,
        latestAgenticTrace: null,
        latestPartyUpdate: null,
        latestInventoryUpdate: null,
        playerHp: null,
        xpSnapshot: null,
        inventoryItems: [],
        inventoryItemCount: 0,
        dispositions: {},
        pendingDiceRoll: null,

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
            latestStoryEventId: null,
            latestPacingAction: null,
            latestImageData: null,
            latestAgenticTrace: null,
            latestPartyUpdate: null,
            latestInventoryUpdate: null,
            playerHp: null,
            xpSnapshot: null,
            inventoryItems: [],
            inventoryItemCount: 0,
            dispositions: {},
            pendingDiceRoll: null,
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
            latestStoryEventId: null,
            latestPacingAction: null,
            latestImageData: null,
            latestAgenticTrace: null,
            latestPartyUpdate: null,
            latestInventoryUpdate: null,
            playerHp: null,
            xpSnapshot: null,
            inventoryItems: [],
            inventoryItemCount: 0,
            dispositions: {},
            pendingDiceRoll: null,
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
            latestStoryEventId: storyEvents.length > 0 ? storyEvents[storyEvents.length - 1] : null,
            latestPacingAction: pacingAction,
          });
        },

        setImageData: (imageData: CoordinatorImageData | null) => {
          set({ latestImageData: imageData });
        },

        setAgenticTrace: (trace: AgenticTracePayload | null) => {
          set({ latestAgenticTrace: trace });
        },

        setDispositions: (d: DispositionsMap) => {
          set({ dispositions: d });
        },

        setDiceRoll: (roll: DiceRoll | null) => {
          set({ pendingDiceRoll: roll });
        },

        appendAgenticToolCall: (call) => {
          set((state) => {
            const prev = state.latestAgenticTrace ?? { tool_calls: [], stats: {} };
            const newCalls = [...(prev.tool_calls ?? []), call];
            return {
              latestAgenticTrace: {
                ...prev,
                tool_calls: newCalls,
                stats: {
                  count: newCalls.length,
                  success: newCalls.filter((c) => c?.success !== false).length,
                  failed: newCalls.filter((c) => c?.success === false).length,
                },
              },
            };
          });
        },

        updateFromStateDelta: (delta: StateDelta) => {
          const changes = delta.changes;
          const locationChange = changes.player_location;
          const locationFromDelta = isLocationResponse(locationChange) ? locationChange : null;
          const hpRaw = asRecord(changes.player_hp);
          const xpRaw = asRecord(changes.xp);
          const partyUpdateRaw = asRecord(changes.party_update);
          const storyEventUpdateRaw = asRecord(changes.story_event_update);
          const eventTriggeredRaw = asRecord(changes.event_triggered);
          const storyEventsRaw = changes.story_events;
          const inventoryUpdateRaw = asRecord(changes.inventory_update);
          const inventoryRaw = changes.inventory;

          set((state) => {
            const next: Partial<GameStoreState> = {};

            if (changes.player_location !== undefined) {
              next.location = locationChange === null
                ? null
                : locationFromDelta
                ? locationFromDelta
                : state.location;
            }
            if (locationFromDelta) {
              next.mapGraph = mergeMapGraphFromLocation(state.mapGraph, locationFromDelta);
            }
            if (changes.sub_location !== undefined) {
              next.subLocation = (changes.sub_location as string | null);
            }
            if (changes.game_time !== undefined) {
              next.gameTime = (changes.game_time as GameTimeState);
            }
            if (changes.active_dialogue_npc !== undefined) {
              next.activeDialogueNpc = (changes.active_dialogue_npc as string | null);
            }
            if (changes.combat_id !== undefined) {
              next.combatId = (changes.combat_id as string | null);
            }

            if (hpRaw) {
              const current = asNumber(hpRaw.current);
              const max = asNumber(hpRaw.max);
              const deltaValue = asNumber(hpRaw.delta);
              if (current !== null && max !== null) {
                next.playerHp = {
                  current,
                  max,
                  ...(deltaValue !== null ? { delta: deltaValue } : {}),
                };
              }
            }

            if (xpRaw) {
              const gained = asNumber(xpRaw.gained);
              const newXp = asNumber(xpRaw.new_xp);
              const newLevel = asNumber(xpRaw.new_level);
              const leveledUp = typeof xpRaw.leveled_up === 'boolean' ? xpRaw.leveled_up : undefined;
              const snapshot: XPStateDelta = {
                ...(gained !== null ? { gained } : {}),
                ...(newXp !== null ? { new_xp: newXp } : {}),
                ...(newLevel !== null ? { new_level: newLevel } : {}),
                ...(leveledUp !== undefined ? { leveled_up: leveledUp } : {}),
              };
              if (Object.keys(snapshot).length > 0) {
                next.xpSnapshot = snapshot;
              }
            }

            const inventoryCount = asNumber(changes.inventory_item_count);
            if (inventoryCount !== null) {
              next.inventoryItemCount = inventoryCount;
            }
            if (Array.isArray(inventoryRaw)) {
              next.inventoryItems = inventoryRaw
                .map((item) => asRecord(item))
                .filter((item): item is Record<string, unknown> => item !== null);
            }
            if (inventoryUpdateRaw) {
              next.latestInventoryUpdate = inventoryUpdateRaw;
            }

            if (partyUpdateRaw) {
              next.latestPartyUpdate = partyUpdateRaw;
            }

            const hasParty = typeof changes.has_party === 'boolean' ? changes.has_party : undefined;
            const partyId = asString(changes.party_id);
            const membersRaw = Array.isArray(changes.party_members) ? changes.party_members : null;
            if (hasParty === false) {
              next.party = null;
            } else if (membersRaw) {
              const previousMembers = state.party?.members || [];
              const normalizedMembers = membersRaw
                .map((member, index) => {
                  const raw = asRecord(member);
                  const fallback = previousMembers[index];
                  const characterId = asString(raw?.character_id) || fallback?.character_id || '';
                  if (!characterId) return null;
                  const role = normalizeTeammateRole(raw?.role ?? fallback?.role);
                  const responseTendencyRaw = asNumber(raw?.response_tendency);
                  return {
                    character_id: characterId,
                    name: asString(raw?.name) || fallback?.name || characterId,
                    role,
                    personality: asString(raw?.personality) || fallback?.personality || '',
                    response_tendency: responseTendencyRaw !== null
                      ? responseTendencyRaw
                      : (fallback?.response_tendency ?? 0.5),
                    joined_at: asString(raw?.joined_at) || fallback?.joined_at || new Date().toISOString(),
                    is_active: typeof raw?.is_active === 'boolean'
                      ? raw.is_active
                      : (fallback?.is_active ?? true),
                    current_mood: asString(raw?.current_mood) || fallback?.current_mood || 'neutral',
                    graph_ref: asString(raw?.graph_ref) || fallback?.graph_ref || '',
                  };
                })
                .filter((member): member is Party['members'][number] => member !== null);

              next.party = {
                party_id: partyId || state.party?.party_id || 'party_live',
                world_id: state.party?.world_id || state.worldId || '',
                session_id: state.party?.session_id || state.sessionId || '',
                leader_id: state.party?.leader_id || 'player',
                members: normalizedMembers,
                formed_at: state.party?.formed_at || new Date().toISOString(),
                max_size: state.party?.max_size || 4,
                auto_follow: state.party?.auto_follow ?? true,
                share_events: state.party?.share_events ?? true,
                current_location: state.party?.current_location || state.location?.location_id || null,
                current_sub_location: state.party?.current_sub_location || state.subLocation || null,
              };
            }

            if (Array.isArray(storyEventsRaw)) {
              const storyEvents = storyEventsRaw
                .filter((value): value is string => typeof value === 'string')
                .map((value) => value.trim())
                .filter((value) => value.length > 0);
              next.latestStoryEvents = storyEvents;
              next.latestStoryEventId = storyEvents.length > 0 ? storyEvents[storyEvents.length - 1] : null;
            }

            // 好感度快照
            const dispositionsRaw = changes.dispositions;
            if (dispositionsRaw && typeof dispositionsRaw === 'object' && !Array.isArray(dispositionsRaw)) {
              next.dispositions = dispositionsRaw as DispositionsMap;
            }

            const latestEventFromUpdate = asString(storyEventUpdateRaw?.event_id)
              || asString(eventTriggeredRaw?.event_id);
            if (latestEventFromUpdate) {
              next.latestStoryEventId = latestEventFromUpdate;
              if (!Array.isArray(storyEventsRaw)) {
                const currentEvents = Array.isArray(state.latestStoryEvents) ? state.latestStoryEvents : [];
                if (!currentEvents.includes(latestEventFromUpdate)) {
                  next.latestStoryEvents = [...currentEvents, latestEventFromUpdate];
                }
              }
            }

            return { ...state, ...next };
          });
        },

        updateFromGameState: (gameState: GameState) => {
          set({
            worldId: gameState.world_id,
            sessionId: gameState.session_id,
            subLocation: gameState.sub_location,
            gameTime: gameState.game_time,
            activeDialogueNpc: gameState.active_dialogue_npc,
            combatId: gameState.combat_id,
            playerHp: gameState.player_hp ?? null,
            xpSnapshot: gameState.xp ?? null,
            inventoryItems: Array.isArray(gameState.inventory) ? gameState.inventory : [],
            inventoryItemCount: typeof gameState.inventory_item_count === 'number'
              ? gameState.inventory_item_count
              : (Array.isArray(gameState.inventory) ? gameState.inventory.length : 0),
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
