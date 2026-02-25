"""Combat coordination — extracted from AdminCoordinator."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Dict, Optional

from app.config import settings
from app.models.game import (
    CombatResolveRequest,
    CombatResolveResponse,
    CombatStartRequest,
    CombatStartResponse,
    GameSessionState,
)

logger = logging.getLogger(__name__)


class CombatCoordinator:
    """战斗相关协调逻辑。"""

    def __init__(self, state_manager: Any) -> None:
        self._state_manager = state_manager

    async def start_combat(
        self,
        world_id: str,
        session_id: str,
        request: CombatStartRequest,
    ) -> CombatStartResponse:
        payload = await self._call_combat_tool(
            "start_combat_v3",
            {
                "world_id": world_id,
                "session_id": session_id,
                "enemies": request.enemies,
                "player_state": request.player_state,
                "environment": request.environment,
                "allies": request.allies,
                "combat_context": request.combat_context.model_dump(),
            },
        )
        if payload.get("error"):
            raise ValueError(payload["error"])

        session_data = payload.get("session")
        if session_data:
            session_state = GameSessionState(**session_data)
        else:
            from app.runtime.session_runtime import SessionRuntime
            session_state = await SessionRuntime.get_session_meta(world_id, session_id)
        combat_state = payload.get("combat_state", {})
        return CombatStartResponse(
            combat_id=payload.get("combat_id", ""),
            combat_state=combat_state,
            session=session_state,
        )

    async def resolve_combat(
        self,
        world_id: str,
        session_id: str,
        request: CombatResolveRequest,
    ) -> CombatResolveResponse:
        payload = await self._call_combat_tool(
            "resolve_combat_session_v3",
            {
                "world_id": world_id,
                "session_id": session_id,
                "combat_id": request.combat_id,
                "use_engine": request.use_engine,
                "result_override": request.result_override,
                "summary_override": request.summary_override,
                "dispatch": request.dispatch,
                "recipients": request.recipients,
                "per_character": request.per_character,
                "write_indexes": request.write_indexes,
                "validate": request.validate_input,
                "strict": request.strict,
            },
        )
        if payload.get("error"):
            raise ValueError(payload["error"])
        return CombatResolveResponse(
            combat_id=payload.get("combat_id", ""),
            event_id=payload.get("event_id"),
            dispatched=payload.get("dispatched", False),
        )

    async def trigger_combat(
        self,
        world_id: str,
        session_id: str,
        enemies: list,
        player_state: dict,
        combat_description: str = "",
        environment: Optional[dict] = None,
    ) -> Dict[str, Any]:
        payload = await self._call_combat_tool(
            "start_combat_v3",
            {
                "world_id": world_id,
                "session_id": session_id,
                "enemies": enemies,
                "player_state": player_state,
                "environment": environment,
                "combat_context": CombatStartRequest(player_state=player_state, enemies=enemies).combat_context.model_dump(),
            },
        )
        if payload.get("error"):
            return {"type": "error", "response": payload["error"]}

        combat_id = payload.get("combat_id", "")
        await self._apply_delta(world_id, session_id, self._build_state_delta("start_combat", {"combat_id": combat_id}))
        actions_payload = await self._call_combat_tool(
            "get_available_actions_v3",
            {"combat_id": combat_id},
        )
        actions = actions_payload.get("actions", [])
        narration = combat_description or "战斗开始！"
        return {
            "type": "combat",
            "phase": "start",
            "combat_id": combat_id,
            "narration": narration,
            "combat_state": payload.get("combat_state", {}),
            "available_actions": actions,
        }

    async def execute_combat_action(self, world_id: str, session_id: str, action_id: str) -> Dict[str, Any]:
        from app.runtime.session_runtime import SessionRuntime
        session_state = await SessionRuntime.get_session_meta(world_id, session_id)
        if not session_state or not session_state.active_combat_id:
            return {"type": "error", "response": "没有活跃的战斗"}

        combat_id = session_state.active_combat_id
        payload = await self._call_combat_tool(
            "execute_action_v3",
            {"combat_id": combat_id, "action_id": action_id},
        )
        if payload.get("error"):
            return {"type": "error", "response": payload["error"]}

        combat_state = payload.get("combat_state", {})
        if combat_state.get("is_ended"):
            await self.resolve_combat(
                world_id,
                session_id,
                CombatResolveRequest(combat_id=combat_id, use_engine=True, dispatch=True),
            )
            final_result = payload.get("final_result") or {}
            await self._sync_combat_result_to_character(
                world_id, session_id, {"final_result": final_result},
            )
            await self._apply_delta(world_id, session_id, self._build_state_delta("end_combat", {"combat_id": None}))
            return {
                "type": "combat",
                "phase": "end",
                "result": payload.get("final_result"),
                "narration": payload.get("final_result", {}).get("summary", "战斗结束。"),
            }

        actions_payload = await self._call_combat_tool(
            "get_available_actions_v3",
            {"combat_id": combat_id},
        )
        return {
            "type": "combat",
            "phase": "action",
            "action_result": payload.get("action_result"),
            "narration": payload.get("action_result", {}).get("display_text", ""),
            "available_actions": actions_payload.get("actions", []),
        }

    # ── Internal helpers ──

    @staticmethod
    async def _call_combat_tool(tool_name: str, arguments: dict):
        """MCP 战斗工具调用。"""
        from app.services.mcp_client_pool import MCPClientPool
        pool = await MCPClientPool.get_instance()
        return await pool.call_tool(MCPClientPool.COMBAT, tool_name, arguments)

    @staticmethod
    def _build_state_delta(operation: str, changes: dict):
        """构建 StateDelta。"""
        import uuid as _uuid
        from app.models.state_delta import StateDelta
        return StateDelta(
            delta_id=_uuid.uuid4().hex,
            timestamp=datetime.now(UTC),
            operation=operation,
            changes=changes,
        )

    async def _apply_delta(self, world_id: str, session_id: str, delta) -> None:
        """应用 StateDelta。"""
        await self._state_manager.apply_delta(world_id, session_id, delta)

    @staticmethod
    async def _sync_combat_result_to_character(
        world_id: str, session_id: str, combat_payload: dict,
        *, session=None,
    ) -> None:
        """同步战斗结果到角色。"""
        from app.world.player import stats as stats_manager
        if session is None:
            return
        player = session.player
        if player:
            try:
                result = stats_manager.sync_combat_rewards(player, combat_payload)
                if any(v for v in result.values()):
                    session.mark_player_dirty()
                logger.info("[combat_sync] Synced via graph node: %s", result)
            except (KeyError, ValueError, AttributeError) as exc:
                logger.error("[combat_sync] Graph sync failed: %s", exc, exc_info=True)
