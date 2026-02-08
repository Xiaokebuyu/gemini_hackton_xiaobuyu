/**
 * Game API endpoints
 *
 * 对应后端 app/routers/game_v2.py
 * 所有端点前缀: /api/game/
 */
import apiClient from './client';
import type {
  PlayerInputRequest,
  PlayerInputResponse,
  CoordinatorResponse,
  LocationResponse,
  NavigateRequest,
  NavigateResponse,
  GameTimeResponse,
  Party,
  GameSessionState,
  CreateGameSessionRequest,
  CreateGameSessionResponse,
  RecoverableSessionsResponse,
  GameContextResponse,
  EnterSceneRequest,
  EnterSceneResponse,
  StartDialogueRequest,
  StartDialogueResponse,
  EnterSubLocationRequest,
  SubLocationsResponse,
  AdvanceTimeRequest,
  PasserbyDialogueRequest,
  TriggerEventRequest,
  CreatePartyRequest,
  AddTeammateRequest,
  LoadTeammatesRequest,
} from '../types';

// =============================================================================
// Worlds
// =============================================================================

/**
 * 获取可用世界列表
 *
 * GET /api/game/worlds
 */
export async function listWorlds(): Promise<{ worlds: { id: string; name: string; description: string }[] }> {
  const response = await apiClient.get<{ worlds: { id: string; name: string; description: string }[] }>(
    '/api/game/worlds'
  );
  return response.data;
}

// =============================================================================
// Session Management
// =============================================================================

/**
 * 创建新会话 (v2)
 *
 * POST /api/game/{world_id}/sessions
 */
export async function createSession(
  worldId: string,
  request: CreateGameSessionRequest
): Promise<CreateGameSessionResponse> {
  const response = await apiClient.post<CreateGameSessionResponse>(
    `/api/game/${worldId}/sessions`,
    request
  );
  return response.data;
}

/**
 * 列出用户可恢复会话
 *
 * GET /api/game/{world_id}/sessions?user_id=...&limit=...
 */
export async function listRecoverableSessions(
  worldId: string,
  userId: string,
  limit = 20
): Promise<RecoverableSessionsResponse> {
  const response = await apiClient.get<RecoverableSessionsResponse>(
    `/api/game/${worldId}/sessions`,
    {
      params: {
        user_id: userId,
        limit,
      },
    }
  );
  return response.data;
}

/**
 * 获取会话状态
 *
 * GET /api/game/{world_id}/sessions/{session_id}
 */
export async function getGameState(
  worldId: string,
  sessionId: string
): Promise<GameSessionState> {
  const response = await apiClient.get<GameSessionState>(
    `/api/game/${worldId}/sessions/${sessionId}`
  );
  return response.data;
}

/**
 * 获取游戏上下文
 *
 * GET /api/game/{world_id}/sessions/{session_id}/context
 */
export async function getGameContext(
  worldId: string,
  sessionId: string
): Promise<GameContextResponse> {
  const response = await apiClient.get<GameContextResponse>(
    `/api/game/${worldId}/sessions/${sessionId}/context`
  );
  return response.data;
}

// =============================================================================
// Core Game Loop
// =============================================================================

/**
 * 发送玩家输入 (Pro-First v2)
 *
 * POST /api/game/{world_id}/sessions/{session_id}/input
 */
export async function sendGameInputV2(
  worldId: string,
  sessionId: string,
  input: PlayerInputRequest
): Promise<CoordinatorResponse> {
  const response = await apiClient.post<CoordinatorResponse>(
    `/api/game/${worldId}/sessions/${sessionId}/input`,
    input
  );
  return response.data;
}

// =============================================================================
// Scene
// =============================================================================

/**
 * 进入场景
 *
 * POST /api/game/{world_id}/sessions/{session_id}/scene
 */
export async function enterScene(
  worldId: string,
  sessionId: string,
  request: EnterSceneRequest
): Promise<EnterSceneResponse> {
  const response = await apiClient.post<EnterSceneResponse>(
    `/api/game/${worldId}/sessions/${sessionId}/scene`,
    request
  );
  return response.data;
}

// =============================================================================
// Location & Navigation
// =============================================================================

/**
 * 获取当前位置信息
 *
 * GET /api/game/{world_id}/sessions/{session_id}/location
 */
export async function getLocation(
  worldId: string,
  sessionId: string
): Promise<LocationResponse> {
  const response = await apiClient.get<LocationResponse>(
    `/api/game/${worldId}/sessions/${sessionId}/location`
  );
  return response.data;
}

/**
 * 导航到新位置
 *
 * POST /api/game/{world_id}/sessions/{session_id}/navigate
 */
export async function navigate(
  worldId: string,
  sessionId: string,
  request: NavigateRequest
): Promise<NavigateResponse> {
  const response = await apiClient.post<NavigateResponse>(
    `/api/game/${worldId}/sessions/${sessionId}/navigate`,
    request
  );
  return response.data;
}

// =============================================================================
// Sub-Locations
// =============================================================================

/**
 * 获取子地点列表
 *
 * GET /api/game/{world_id}/sessions/{session_id}/sub-locations
 */
export async function getSubLocations(
  worldId: string,
  sessionId: string
): Promise<SubLocationsResponse> {
  const response = await apiClient.get<SubLocationsResponse>(
    `/api/game/${worldId}/sessions/${sessionId}/sub-locations`
  );
  return response.data;
}

/**
 * 进入子地点
 *
 * POST /api/game/{world_id}/sessions/{session_id}/sub-location/enter
 */
export async function enterSubLocation(
  worldId: string,
  sessionId: string,
  request: EnterSubLocationRequest
): Promise<{ success: boolean; sub_location: string; narration: string }> {
  const response = await apiClient.post(
    `/api/game/${worldId}/sessions/${sessionId}/sub-location/enter`,
    request
  );
  return response.data;
}

/**
 * 离开子地点
 *
 * POST /api/game/{world_id}/sessions/{session_id}/sub-location/leave
 */
export async function leaveSubLocation(
  worldId: string,
  sessionId: string
): Promise<{ success: boolean; error?: string }> {
  const response = await apiClient.post(
    `/api/game/${worldId}/sessions/${sessionId}/sub-location/leave`
  );
  return response.data;
}

// =============================================================================
// Time
// =============================================================================

/**
 * 获取游戏时间
 *
 * GET /api/game/{world_id}/sessions/{session_id}/time
 */
export async function getGameTime(
  worldId: string,
  sessionId: string
): Promise<GameTimeResponse> {
  const response = await apiClient.get<GameTimeResponse>(
    `/api/game/${worldId}/sessions/${sessionId}/time`
  );
  return response.data;
}

/**
 * 推进时间
 *
 * POST /api/game/{world_id}/sessions/{session_id}/time/advance
 */
export async function advanceTime(
  worldId: string,
  sessionId: string,
  request?: AdvanceTimeRequest
): Promise<Record<string, unknown>> {
  const response = await apiClient.post(
    `/api/game/${worldId}/sessions/${sessionId}/time/advance`,
    request || {}
  );
  return response.data;
}

/**
 * 推进到下一天
 *
 * POST /api/game/{world_id}/sessions/{session_id}/advance-day
 */
export async function advanceDay(
  worldId: string,
  sessionId: string
): Promise<PlayerInputResponse> {
  const response = await apiClient.post<PlayerInputResponse>(
    `/api/game/${worldId}/sessions/${sessionId}/advance-day`
  );
  return response.data;
}

// =============================================================================
// Dialogue
// =============================================================================

/**
 * 开始对话
 *
 * POST /api/game/{world_id}/sessions/{session_id}/dialogue/start
 */
export async function startDialogue(
  worldId: string,
  sessionId: string,
  request: StartDialogueRequest
): Promise<StartDialogueResponse> {
  const response = await apiClient.post<StartDialogueResponse>(
    `/api/game/${worldId}/sessions/${sessionId}/dialogue/start`,
    request
  );
  return response.data;
}

/**
 * 结束对话
 *
 * POST /api/game/{world_id}/sessions/{session_id}/dialogue/end
 */
export async function endDialogue(
  worldId: string,
  sessionId: string
): Promise<PlayerInputResponse> {
  const response = await apiClient.post<PlayerInputResponse>(
    `/api/game/${worldId}/sessions/${sessionId}/dialogue/end`
  );
  return response.data;
}

// =============================================================================
// Party Management
// =============================================================================

/**
 * 获取队伍信息
 *
 * GET /api/game/{world_id}/sessions/{session_id}/party
 */
export async function getParty(
  worldId: string,
  sessionId: string
): Promise<Party | null> {
  const response = await apiClient.get<Party | null>(
    `/api/game/${worldId}/sessions/${sessionId}/party`
  );
  return response.data;
}

/**
 * 创建队伍
 *
 * POST /api/game/{world_id}/sessions/{session_id}/party
 */
export async function createParty(
  worldId: string,
  sessionId: string,
  request?: CreatePartyRequest
): Promise<Record<string, unknown>> {
  const response = await apiClient.post(
    `/api/game/${worldId}/sessions/${sessionId}/party`,
    request || {}
  );
  return response.data;
}

/**
 * 添加队友
 *
 * POST /api/game/{world_id}/sessions/{session_id}/party/add
 */
export async function addTeammate(
  worldId: string,
  sessionId: string,
  request: AddTeammateRequest
): Promise<Record<string, unknown>> {
  const response = await apiClient.post(
    `/api/game/${worldId}/sessions/${sessionId}/party/add`,
    request
  );
  return response.data;
}

/**
 * 移除队友
 *
 * DELETE /api/game/{world_id}/sessions/{session_id}/party/{character_id}
 */
export async function removeTeammate(
  worldId: string,
  sessionId: string,
  characterId: string
): Promise<Record<string, unknown>> {
  const response = await apiClient.delete(
    `/api/game/${worldId}/sessions/${sessionId}/party/${characterId}`
  );
  return response.data;
}

/**
 * 加载预设队友
 *
 * POST /api/game/{world_id}/sessions/{session_id}/party/load
 */
export async function loadTeammates(
  worldId: string,
  sessionId: string,
  request: LoadTeammatesRequest
): Promise<Record<string, unknown>> {
  const response = await apiClient.post(
    `/api/game/${worldId}/sessions/${sessionId}/party/load`,
    request
  );
  return response.data;
}

// =============================================================================
// Narrative
// =============================================================================

/**
 * 获取叙事进度
 *
 * GET /api/game/{world_id}/sessions/{session_id}/narrative/progress
 */
export async function getNarrativeProgress(
  worldId: string,
  sessionId: string
): Promise<Record<string, unknown>> {
  const response = await apiClient.get(
    `/api/game/${worldId}/sessions/${sessionId}/narrative/progress`
  );
  return response.data;
}

/**
 * 获取流程面板数据
 *
 * GET /api/game/{world_id}/sessions/{session_id}/narrative/flow-board
 */
export async function getFlowBoard(
  worldId: string,
  sessionId: string
): Promise<Record<string, unknown>> {
  const response = await apiClient.get(
    `/api/game/${worldId}/sessions/${sessionId}/narrative/flow-board`
  );
  return response.data;
}

/**
 * 获取当前计划
 *
 * GET /api/game/{world_id}/sessions/{session_id}/narrative/current-plan
 */
export async function getCurrentPlan(
  worldId: string,
  sessionId: string
): Promise<Record<string, unknown>> {
  const response = await apiClient.get(
    `/api/game/${worldId}/sessions/${sessionId}/narrative/current-plan`
  );
  return response.data;
}

/**
 * 获取可用地图
 *
 * GET /api/game/{world_id}/sessions/{session_id}/narrative/available-maps
 */
export async function getAvailableMaps(
  worldId: string,
  sessionId: string
): Promise<{ available_maps: Record<string, unknown>[]; all_unlocked: boolean }> {
  const response = await apiClient.get(
    `/api/game/${worldId}/sessions/${sessionId}/narrative/available-maps`
  );
  return response.data;
}

/**
 * 触发叙事事件
 *
 * POST /api/game/{world_id}/sessions/{session_id}/narrative/trigger-event
 */
export async function triggerNarrativeEvent(
  worldId: string,
  sessionId: string,
  request: TriggerEventRequest
): Promise<Record<string, unknown>> {
  const response = await apiClient.post(
    `/api/game/${worldId}/sessions/${sessionId}/narrative/trigger-event`,
    request
  );
  return response.data;
}

// =============================================================================
// Passersby
// =============================================================================

/**
 * 获取路人列表
 *
 * GET /api/game/{world_id}/sessions/{session_id}/passersby
 */
export async function getPassersby(
  worldId: string,
  sessionId: string
): Promise<{ location_id: string; sub_location_id: string | null; passersby: Record<string, unknown>[] }> {
  const response = await apiClient.get(
    `/api/game/${worldId}/sessions/${sessionId}/passersby`
  );
  return response.data;
}

/**
 * 生成路人
 *
 * POST /api/game/{world_id}/sessions/{session_id}/passersby/spawn
 */
export async function spawnPasserby(
  worldId: string,
  sessionId: string
): Promise<{ success: boolean; passerby: Record<string, unknown> }> {
  const response = await apiClient.post(
    `/api/game/${worldId}/sessions/${sessionId}/passersby/spawn`
  );
  return response.data;
}

/**
 * 路人对话
 *
 * POST /api/game/{world_id}/sessions/{session_id}/passersby/dialogue
 */
export async function passerbyDialogue(
  worldId: string,
  sessionId: string,
  request: PasserbyDialogueRequest
): Promise<Record<string, unknown>> {
  const response = await apiClient.post(
    `/api/game/${worldId}/sessions/${sessionId}/passersby/dialogue`,
    request
  );
  return response.data;
}

// =============================================================================
// Events
// =============================================================================

/**
 * 事件摄入 (结构化)
 *
 * POST /api/game/{world_id}/events/ingest
 */
export async function ingestEvent(
  worldId: string,
  request: Record<string, unknown>
): Promise<Record<string, unknown>> {
  const response = await apiClient.post(
    `/api/game/${worldId}/events/ingest`,
    request
  );
  return response.data;
}

/**
 * 事件摄入 (自然语言)
 *
 * POST /api/game/{world_id}/events/ingest-natural
 */
export async function ingestNaturalEvent(
  worldId: string,
  request: Record<string, unknown>
): Promise<Record<string, unknown>> {
  const response = await apiClient.post(
    `/api/game/${worldId}/events/ingest-natural`,
    request
  );
  return response.data;
}

// =============================================================================
// Session History
// =============================================================================

export interface HistoryMessage {
  role: string;
  content: string;
  timestamp: string | null;
  metadata: Record<string, unknown>;
}

/**
 * 获取会话聊天历史
 *
 * GET /api/game/{world_id}/sessions/{session_id}/history
 */
export async function getSessionHistory(
  worldId: string,
  sessionId: string,
  limit = 50
): Promise<{ messages: HistoryMessage[] }> {
  const response = await apiClient.get<{ messages: HistoryMessage[] }>(
    `/api/game/${worldId}/sessions/${sessionId}/history`,
    { params: { limit } }
  );
  return response.data;
}
