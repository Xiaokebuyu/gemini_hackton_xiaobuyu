/**
 * Game API endpoints
 *
 * 对应后端 app/routers/game_master.py 和 app/routers/game.py
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
  GameState,
  CreateSessionRequest,
  CreateSessionResponse,
} from '../types';

// =============================================================================
// Game Master API (/api/gm/...)
// =============================================================================

/**
 * 发送玩家输入到游戏协调器（核心游戏循环）
 *
 * POST /api/gm/{world_id}/sessions/{session_id}/input
 */
export async function sendGameInput(
  worldId: string,
  sessionId: string,
  input: PlayerInputRequest
): Promise<PlayerInputResponse> {
  const response = await apiClient.post<PlayerInputResponse>(
    `/api/gm/${worldId}/sessions/${sessionId}/input`,
    input
  );
  return response.data;
}

/**
 * 发送玩家输入到游戏协调器 (Pro-First v2 架构)
 *
 * POST /api/gm/{world_id}/sessions/{session_id}/input_v2
 *
 * 返回完整的 CoordinatorResponse，包含：
 * - narration: GM 叙述
 * - teammate_responses: 队友响应列表
 * - available_actions: 可用操作
 * - state_delta: 状态变更
 */
export async function sendGameInputV2(
  worldId: string,
  sessionId: string,
  input: PlayerInputRequest
): Promise<CoordinatorResponse> {
  const response = await apiClient.post<CoordinatorResponse>(
    `/api/gm/${worldId}/sessions/${sessionId}/input_v2`,
    input
  );
  return response.data;
}

/**
 * 获取当前游戏状态
 */
export async function getGameState(
  worldId: string,
  sessionId: string
): Promise<GameState> {
  const response = await apiClient.get<GameState>(
    `/api/gm/${worldId}/sessions/${sessionId}/state`
  );
  return response.data;
}

/**
 * 获取队伍信息
 */
export async function getParty(
  worldId: string,
  sessionId: string
): Promise<Party> {
  const response = await apiClient.get<Party>(
    `/api/gm/${worldId}/sessions/${sessionId}/party`
  );
  return response.data;
}

// =============================================================================
// Game API (/api/game/...)
// =============================================================================

/**
 * 获取当前位置信息
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

/**
 * 获取游戏时间
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

// =============================================================================
// Session Management
// =============================================================================

/**
 * 创建新会话
 */
export async function createSession(
  worldId: string,
  request?: CreateSessionRequest
): Promise<CreateSessionResponse> {
  const response = await apiClient.post<CreateSessionResponse>(
    `/api/gm/${worldId}/sessions`,
    request || {}
  );
  return response.data;
}

/**
 * 获取可用世界列表
 */
export async function getWorlds(): Promise<{ worlds: string[] }> {
  const response = await apiClient.get<{ worlds: string[] }>('/api/worlds');
  return response.data;
}
