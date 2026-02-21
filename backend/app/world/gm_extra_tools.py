"""GM extra_tools 工厂 — 8 个 MCP/FlashCPU 依赖工具。

这些工具通过闭包捕获 FlashCPU 等外部依赖，以 extra_tools 方式
注入 AgenticExecutor.run()。

Usage::

    from app.world.gm_extra_tools import build_gm_extra_tools, ENGINE_TOOL_EXCLUSIONS

    extra_tools = build_gm_extra_tools(
        session=session, flash_cpu=flash_cpu,
        graph_store=graph_store, event_queue=event_queue,
        engine_executed=engine_executed,
    )
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)

# 引擎意图 → 排除工具名映射
ENGINE_TOOL_EXCLUSIONS: Dict[str, set] = {
    "talk": {"npc_dialogue"},
    "use_item": {"add_item", "remove_item", "heal_player"},
}


def build_gm_extra_tools(
    *,
    session: Any,
    flash_cpu: Any,
    graph_store: Any,
    event_queue: Optional[asyncio.Queue] = None,
    engine_executed: Optional[Dict[str, Any]] = None,
) -> List[Callable]:
    """构建 GM MCP 依赖工具列表。

    返回的 callable 签名干净（无 ctx），可直接传给 AgenticExecutor.run(extra_tools=...)。
    """
    # 预计算引擎排除集
    excluded = set()
    if engine_executed:
        exec_type = engine_executed.get("type", "")
        excluded = ENGINE_TOOL_EXCLUSIONS.get(exec_type, set())

    tools: List[Callable] = []

    # -- Helper: resolve world_id / session_id --
    def _world_id() -> str:
        return session.world_id

    def _session_id() -> str:
        return session.session_id

    timeout = settings.admin_agentic_tool_timeout_seconds

    # ================================================================
    # 1. npc_dialogue
    # ================================================================
    async def npc_dialogue(npc_id: str, message: str) -> Dict[str, Any]:
        """Talk to an NPC. npc_id from area_context.npcs."""
        from app.models.admin_protocol import FlashOperation, FlashRequest

        request = FlashRequest(
            operation=FlashOperation.NPC_DIALOGUE,
            parameters={"npc_id": npc_id, "message": message},
        )
        try:
            response = await asyncio.wait_for(
                flash_cpu.execute_request(
                    world_id=_world_id(), session_id=_session_id(),
                    request=request, generate_narration=False,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return {"success": False, "error": "timeout: npc_dialogue"}
        except Exception as exc:
            return {"success": False, "error": f"npc_dialogue: {type(exc).__name__}: {str(exc)[:200]}"}

        payload = response.result if isinstance(response.result, dict) else {"raw": response.result}
        payload["success"] = response.success
        if response.error:
            payload["error"] = response.error

        # NPC 显示名
        char_data = session.world.get_character(npc_id) if session.world else None
        npc_name = (char_data or {}).get("name", npc_id) if isinstance(char_data, dict) else npc_id
        payload["npc_name"] = npc_name

        # 交互计数
        if response.success and session.narrative:
            count = session.narrative.npc_interactions.get(npc_id, 0)
            session.narrative.npc_interactions[npc_id] = count + 1
            session.mark_narrative_dirty()

        # SSE: npc_response 即时推送
        if event_queue and response.success and isinstance(payload, dict) and payload.get("response"):
            try:
                event_queue.put_nowait({
                    "type": "npc_response",
                    "character_id": npc_id,
                    "name": npc_name,
                    "dialogue": payload["response"],
                    "message": message,
                })
            except Exception:
                pass

        return payload

    if "npc_dialogue" not in excluded:
        tools.append(npc_dialogue)

    # ================================================================
    # 2. start_combat
    # ================================================================
    async def start_combat(enemies: list) -> Dict[str, Any]:
        """Start combat with enemy specs. enemies: list of {enemy_id, count, level}."""
        from app.models.admin_protocol import FlashOperation, FlashRequest

        enemy_payload: List[Dict[str, Any]] = []
        for enemy in enemies or []:
            if isinstance(enemy, str) and enemy.strip():
                enemy_payload.append({"enemy_id": enemy.strip(), "count": 1, "level": 1})
                continue
            if isinstance(enemy, dict):
                enemy_id = str(enemy.get("enemy_id") or enemy.get("type") or "").strip()
                if not enemy_id:
                    continue
                try:
                    count = int(enemy.get("count", 1) or 1)
                except (TypeError, ValueError):
                    count = 1
                try:
                    level = int(enemy.get("level", 1) or 1)
                except (TypeError, ValueError):
                    level = 1
                enemy_payload.append({
                    "enemy_id": enemy_id,
                    "count": max(1, min(count, 20)),
                    "level": max(1, min(level, 20)),
                    "variant": enemy.get("variant"),
                    "template_version": enemy.get("template_version"),
                    "tags": list(enemy.get("tags", []) or []),
                    "overrides": dict(enemy.get("overrides", {}) or {}),
                })

        player_state = {}
        if session.player:
            try:
                player_state = session.player.to_combat_player_state()
            except Exception as exc:
                logger.warning("[start_combat] player_state fail: %s", exc)

        args = {"enemies": enemy_payload, "player_state": player_state}
        request = FlashRequest(operation=FlashOperation.START_COMBAT, parameters=args)
        return await _execute_flash(request)

    tools.append(start_combat)

    # ================================================================
    # 3. get_combat_options
    # ================================================================
    async def get_combat_options(actor_id: str = "player") -> Dict[str, Any]:
        """Get available combat actions for an actor."""
        combat_id = _resolve_combat_id()
        if not combat_id:
            return {"success": False, "error": "no active combat"}
        try:
            actions_raw = await flash_cpu.call_combat_tool(
                "get_available_actions_for_actor",
                {"combat_id": combat_id, "actor_id": actor_id},
            )
            if actions_raw.get("error"):
                actions_raw = await flash_cpu.call_combat_tool(
                    "get_available_actions", {"combat_id": combat_id},
                )
            state_payload = await flash_cpu.call_combat_tool(
                "get_combat_state", {"combat_id": combat_id},
            )
        except Exception as exc:
            return {"success": False, "error": f"combat options: {exc}"}

        actions = actions_raw.get("actions", []) if isinstance(actions_raw, dict) else []
        return {
            "success": True,
            "combat_id": combat_id,
            "actor_id": actor_id,
            "actions": actions,
            "combat_state": state_payload if isinstance(state_payload, dict) else {},
        }

    tools.append(get_combat_options)

    # ================================================================
    # 4. choose_combat_action
    # ================================================================
    async def choose_combat_action(action_id: str, actor_id: str = "player") -> Dict[str, Any]:
        """Execute a combat action for an actor."""
        combat_id = _resolve_combat_id()
        if not combat_id:
            return {"success": False, "error": "no active combat"}
        try:
            payload = await flash_cpu.call_combat_tool(
                "execute_action_for_actor",
                {"combat_id": combat_id, "actor_id": actor_id, "action_id": action_id},
            )
            if isinstance(payload, dict) and payload.get("error"):
                payload = await flash_cpu.call_combat_tool(
                    "execute_action",
                    {"combat_id": combat_id, "action_id": action_id},
                )
        except Exception as exc:
            return {"success": False, "error": f"combat execute: {exc}"}

        if not isinstance(payload, dict):
            payload = {"success": False, "error": "invalid combat response"}

        combat_state = payload.get("combat_state", {})
        if combat_state.get("is_ended"):
            try:
                resolve_payload = await flash_cpu.call_combat_tool(
                    "resolve_combat_session_v3",
                    {
                        "world_id": _world_id(),
                        "session_id": _session_id(),
                        "combat_id": combat_id,
                        "dispatch": True,
                    },
                )
                sync_data = dict(resolve_payload) if isinstance(resolve_payload, dict) else {}
                sync_data["final_result"] = payload.get("final_result")
                _sync_combat_to_graph(sync_data)
                if session.game_state:
                    session.game_state.combat_id = None
                    session.mark_game_state_dirty()
                payload["resolve"] = resolve_payload
            except Exception as exc:
                payload["resolve_error"] = str(exc)

            # P6: combat_ended WorldEvent
            try:
                wg = getattr(session, "world_graph", None)
                engine = getattr(session, "_behavior_engine", None)
                if wg and engine and not getattr(session, "_world_graph_failed", False):
                    from app.world.models import WorldEvent as WE
                    player_location = session.player_location or ""
                    ctx = session.build_tick_context("post")
                    if ctx and player_location:
                        final = payload.get("final_result") or {}
                        combat_event = WE(
                            event_type="combat_ended",
                            origin_node=player_location,
                            actor="player",
                            data={
                                "result": final.get("result", "unknown"),
                                "enemies": final.get("enemies_defeated", []),
                                "rewards": final.get("rewards", {}),
                                "combat_id": combat_id,
                            },
                            visibility="scope",
                            game_day=ctx.game_day,
                            game_hour=ctx.game_hour,
                        )
                        tick_result = engine.handle_event(combat_event, ctx)
                        if tick_result.narrative_hints:
                            payload["combat_event_hints"] = tick_result.narrative_hints
                        session._sync_tick_to_narrative(tick_result)
                        session._apply_tick_side_effects(tick_result)
            except Exception as exc:
                logger.warning("[combat] P6 combat_ended event failed: %s", exc)

        payload["success"] = not bool(payload.get("error"))
        return payload

    tools.append(choose_combat_action)

    # ================================================================
    # 5. add_teammate
    # ================================================================
    async def add_teammate(
        character_id: str,
        name: str,
        role: str = "support",
        personality: str = "",
        response_tendency: float = 0.65,
    ) -> Dict[str, Any]:
        """Add a teammate to the party."""
        from app.models.admin_protocol import FlashOperation, FlashRequest

        args = {
            "character_id": character_id, "name": name, "role": role,
            "personality": personality, "response_tendency": float(response_tendency),
        }
        request = FlashRequest(operation=FlashOperation.ADD_TEAMMATE, parameters=args)
        return await _execute_flash(request)

    tools.append(add_teammate)

    # ================================================================
    # 6. remove_teammate
    # ================================================================
    async def remove_teammate(character_id: str, reason: str = "") -> Dict[str, Any]:
        """Remove teammate from party."""
        from app.models.admin_protocol import FlashOperation, FlashRequest

        args = {"character_id": character_id, "reason": reason}
        request = FlashRequest(operation=FlashOperation.REMOVE_TEAMMATE, parameters=args)
        return await _execute_flash(request)

    tools.append(remove_teammate)

    # ================================================================
    # 7. disband_party
    # ================================================================
    async def disband_party(reason: str = "") -> Dict[str, Any]:
        """Disband the current party."""
        from app.models.admin_protocol import FlashOperation, FlashRequest

        args = {"reason": reason}
        request = FlashRequest(operation=FlashOperation.DISBAND_PARTY, parameters=args)
        return await _execute_flash(request)

    tools.append(disband_party)

    # ================================================================
    # 8. ability_check
    # ================================================================
    async def ability_check(ability: str = "", skill: str = "", dc: int = 10) -> Dict[str, Any]:
        """Perform an ability/skill check (d20). dc: easy 8-10, normal 12-15, hard 16-20."""
        from app.models.admin_protocol import FlashOperation, FlashRequest

        args = {"ability": ability, "skill": skill, "dc": int(dc)}
        request = FlashRequest(operation=FlashOperation.ABILITY_CHECK, parameters=args)
        payload = await _execute_flash(request)

        # SSE: dice_result
        if event_queue and isinstance(payload, dict) and "roll" in payload:
            try:
                event_queue.put_nowait({
                    "type": "dice_result",
                    "roll": payload.get("roll"),
                    "ability": payload.get("ability"),
                    "skill": payload.get("skill"),
                    "modifier": payload.get("modifier"),
                    "total": payload.get("total"),
                    "dc": payload.get("dc"),
                    "success": payload.get("success"),
                    "is_critical": payload.get("is_critical"),
                    "is_fumble": payload.get("is_fumble"),
                    "description": payload.get("description"),
                })
            except Exception:
                pass

        return payload

    tools.append(ability_check)

    # ================================================================
    # Internal helpers (closed over session/flash_cpu)
    # ================================================================

    def _resolve_combat_id() -> Optional[str]:
        if session.game_state:
            return getattr(session.game_state, "combat_id", None)
        return None

    def _sync_combat_to_graph(combat_payload: Dict[str, Any]) -> None:
        from app.world import stats_manager
        player = session.player
        if not player:
            return
        try:
            result = stats_manager.sync_combat_rewards(player, combat_payload)
            if any(v for v in result.values()):
                session.mark_player_dirty()
            logger.info("[combat_sync] Synced to graph: %s", result)
        except Exception as exc:
            logger.error("[combat_sync] Failed: %s", exc, exc_info=True)

    async def _execute_flash(request: Any) -> Dict[str, Any]:
        """Execute FlashRequest through FlashCPU with standard error handling."""
        try:
            response = await asyncio.wait_for(
                flash_cpu.execute_request(
                    world_id=_world_id(), session_id=_session_id(),
                    request=request, generate_narration=False,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return {"success": False, "error": "timeout"}
        except Exception as exc:
            return {"success": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}

        payload = response.result if isinstance(response.result, dict) else {"raw": response.result}
        payload["success"] = response.success
        if response.error:
            payload["error"] = response.error
        return payload

    return tools
