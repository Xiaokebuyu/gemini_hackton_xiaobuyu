/**
 * Combat API endpoints
 *
 * 对应后端 app/routers/game_v2.py 中的战斗端点
 * 所有端点前缀: /api/game/
 */
import apiClient from './client';
import type {
  CombatActionRequest,
  CombatActionResponse,
  TriggerCombatRequest,
  TriggerCombatResponse,
  CombatStartRequest,
  CombatStartResponse,
  CombatResolveRequest,
  CombatResolveResponse,
} from '../types';

// =============================================================================
// Combat API
// =============================================================================

/**
 * 触发战斗 (v2)
 *
 * POST /api/game/{world_id}/sessions/{session_id}/combat/trigger
 */
export async function triggerCombat(
  worldId: string,
  sessionId: string,
  request: TriggerCombatRequest
): Promise<TriggerCombatResponse> {
  const response = await apiClient.post<TriggerCombatResponse>(
    `/api/game/${worldId}/sessions/${sessionId}/combat/trigger`,
    request
  );
  return response.data;
}

/**
 * 执行战斗行动
 *
 * POST /api/game/{world_id}/sessions/{session_id}/combat/action
 */
export async function executeCombatAction(
  worldId: string,
  sessionId: string,
  action: CombatActionRequest
): Promise<CombatActionResponse> {
  const response = await apiClient.post<CombatActionResponse>(
    `/api/game/${worldId}/sessions/${sessionId}/combat/action`,
    action
  );
  return response.data;
}

/**
 * 开始战斗 (Legacy)
 *
 * POST /api/game/{world_id}/sessions/{session_id}/combat/start
 */
export async function startCombat(
  worldId: string,
  sessionId: string,
  request: CombatStartRequest
): Promise<CombatStartResponse> {
  const response = await apiClient.post<CombatStartResponse>(
    `/api/game/${worldId}/sessions/${sessionId}/combat/start`,
    request
  );
  return response.data;
}

/**
 * 结算战斗
 *
 * POST /api/game/{world_id}/sessions/{session_id}/combat/resolve
 */
export async function resolveCombat(
  worldId: string,
  sessionId: string,
  request?: CombatResolveRequest
): Promise<CombatResolveResponse> {
  const response = await apiClient.post<CombatResolveResponse>(
    `/api/game/${worldId}/sessions/${sessionId}/combat/resolve`,
    request || {}
  );
  return response.data;
}
