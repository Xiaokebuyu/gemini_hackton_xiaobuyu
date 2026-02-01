/**
 * Combat API endpoints
 */
import apiClient from './client';
import type {
  CombatState,
  CombatActionRequest,
  CombatActionResponse,
  StartCombatResponse,
  EndCombatResponse,
} from '../types';

// =============================================================================
// Combat API
// =============================================================================

/**
 * 获取当前战斗状态
 */
export async function getCombatState(
  worldId: string,
  sessionId: string,
  combatId: string
): Promise<CombatState> {
  const response = await apiClient.get<CombatState>(
    `/api/combat/${worldId}/sessions/${sessionId}/combat/${combatId}`
  );
  return response.data;
}

/**
 * 执行战斗行动
 */
export async function executeCombatAction(
  worldId: string,
  sessionId: string,
  combatId: string,
  action: CombatActionRequest
): Promise<CombatActionResponse> {
  const response = await apiClient.post<CombatActionResponse>(
    `/api/combat/${worldId}/sessions/${sessionId}/combat/${combatId}/action`,
    action
  );
  return response.data;
}

/**
 * 开始战斗
 */
export async function startCombat(
  worldId: string,
  sessionId: string,
  targetIds: string[]
): Promise<StartCombatResponse> {
  const response = await apiClient.post<StartCombatResponse>(
    `/api/combat/${worldId}/sessions/${sessionId}/combat`,
    { target_ids: targetIds }
  );
  return response.data;
}

/**
 * 结束战斗（逃跑或胜利）
 */
export async function endCombat(
  worldId: string,
  sessionId: string,
  combatId: string,
  flee: boolean = false
): Promise<EndCombatResponse> {
  const response = await apiClient.post<EndCombatResponse>(
    `/api/combat/${worldId}/sessions/${sessionId}/combat/${combatId}/end`,
    { flee }
  );
  return response.data;
}

/**
 * 获取可用战斗行动
 */
export async function getAvailableActions(
  worldId: string,
  sessionId: string,
  combatId: string
): Promise<CombatActionRequest[]> {
  const response = await apiClient.get<CombatActionRequest[]>(
    `/api/combat/${worldId}/sessions/${sessionId}/combat/${combatId}/actions`
  );
  return response.data;
}
