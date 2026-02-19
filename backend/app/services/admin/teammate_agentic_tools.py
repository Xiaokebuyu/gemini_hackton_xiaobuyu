"""Teammate agentic tool registry.

为队友提供受限工具集，确保队友只能操作“自己可操作”的行为。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)


class TeammateAgenticToolRegistry:
    """队友专属工具注册表（权限收敛版）。"""

    def __init__(
        self,
        *,
        member: Any,
        session: Any,
        flash_cpu: Optional[Any] = None,
        graph_store: Optional[Any] = None,
        recall_orchestrator: Optional[Any] = None,
        event_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> None:
        self.member = member
        self.session = session
        self.flash_cpu = flash_cpu
        self.graph_store = graph_store
        self.recall_orchestrator = recall_orchestrator
        self._event_callback = event_callback
        self._lock = asyncio.Lock()
        self.tool_calls: List[Dict[str, Any]] = []

    @property
    def world_id(self) -> str:
        return str(getattr(self.session, "world_id", "") or "")

    @property
    def character_id(self) -> str:
        return str(getattr(self.member, "character_id", "") or "")

    def _combat_active(self) -> bool:
        game_state = getattr(self.session, "game_state", None)
        return bool(game_state and getattr(game_state, "combat_id", None))

    def get_tools(self) -> List[Any]:
        """返回可用工具。"""
        tools: List[Any] = [
            self.update_my_disposition,
            self.recall_my_memory,
        ]
        if self._combat_active():
            tools.append(self.choose_my_combat_action)
        return tools

    async def _emit_event(self, event: Dict[str, Any]) -> None:
        if not self._event_callback:
            return
        maybe_coro = self._event_callback(event)
        if asyncio.iscoroutine(maybe_coro):
            await maybe_coro

    async def _record(
        self,
        *,
        name: str,
        args: Dict[str, Any],
        started_at: float,
        result: Dict[str, Any],
        success: bool,
        error: Optional[str],
    ) -> None:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        call = {
            "name": name,
            "args": args,
            "success": success,
            "error": error,
            "duration_ms": duration_ms,
            "result": result,
        }
        async with self._lock:
            self.tool_calls.append(call)
            tool_index = len(self.tool_calls)

        event = {
            "type": "teammate_tool_call",
            "character_id": self.character_id,
            "name": name,
            "tool_name": name,
            "success": success,
            "error": error,
            "duration_ms": duration_ms,
            "tool_index": tool_index,
        }
        if name == "update_my_disposition" and success:
            event["disposition_change"] = {
                "character_id": self.character_id,
                "target_id": result.get("target_id", args.get("target_id", "")),
                "deltas": result.get("applied_deltas", {}),
                "current": result.get("current", {}),
            }
        await self._emit_event(event)

    async def _timed(self, name: str, args: Dict[str, Any], coro: Awaitable[Dict[str, Any]]) -> Dict[str, Any]:
        started = time.perf_counter()
        try:
            payload = await asyncio.wait_for(
                coro,
                timeout=settings.admin_agentic_tool_timeout_seconds,
            )
        except asyncio.TimeoutError:
            payload = {"success": False, "error": f"tool timeout: {name}"}
            await self._record(
                name=name,
                args=args,
                started_at=started,
                result=payload,
                success=False,
                error=payload["error"],
            )
            return payload
        except Exception as exc:
            payload = {"success": False, "error": f"{name} error: {type(exc).__name__}: {str(exc)[:200]}"}
            await self._record(
                name=name,
                args=args,
                started_at=started,
                result=payload,
                success=False,
                error=payload["error"],
            )
            return payload

        if not isinstance(payload, dict):
            payload = {"success": True, "result": payload}
        payload.setdefault("success", not bool(payload.get("error")))
        await self._record(
            name=name,
            args=args,
            started_at=started,
            result=payload,
            success=bool(payload.get("success", False)),
            error=payload.get("error"),
        )
        return payload

    async def update_my_disposition(
        self,
        target_id: str,
        deltas: Dict[str, int],
        reason: str = "",
    ) -> Dict[str, Any]:
        """更新“我对目标”的好感度。"""
        args = {"target_id": target_id, "deltas": deltas, "reason": reason}

        async def _run() -> Dict[str, Any]:
            if not self.graph_store:
                return {"success": False, "error": "graph_store unavailable"}
            if not target_id:
                return {"success": False, "error": "missing target_id"}
            if target_id == self.character_id:
                return {"success": False, "error": "cannot update disposition toward self"}
            valid_dims = {"approval", "trust", "fear", "romance"}
            cleaned: Dict[str, int] = {}
            for key, value in (deltas or {}).items():
                if key not in valid_dims:
                    continue
                try:
                    ivalue = int(value)
                except Exception:
                    continue
                ivalue = max(-20, min(20, ivalue))
                if ivalue != 0:
                    cleaned[key] = ivalue
            if not cleaned:
                return {"success": False, "error": "no valid disposition deltas"}

            game_day = getattr(getattr(self.session, "time", None), "day", None)
            updated = await self.graph_store.update_disposition(
                world_id=self.world_id,
                character_id=self.character_id,
                target_id=target_id,
                deltas=cleaned,
                reason=reason,
                game_day=game_day,
            )
            current = {
                dim: updated.get(dim, 0)
                for dim in ("approval", "trust", "fear", "romance")
            }
            return {
                "success": True,
                "character_id": self.character_id,
                "target_id": target_id,
                "applied_deltas": cleaned,
                "current": current,
            }

        return await self._timed("update_my_disposition", args, _run())

    async def recall_my_memory(self, seeds: List[str]) -> Dict[str, Any]:
        """召回队友自己的记忆。"""
        args = {"seeds": seeds}

        async def _run() -> Dict[str, Any]:
            norm_seeds = [str(seed).strip() for seed in (seeds or []) if str(seed).strip()]
            if not norm_seeds:
                return {"success": False, "error": "missing seeds"}

            chapter_id = getattr(self.session, "chapter_id", None)
            area_id = getattr(self.session, "area_id", None)
            sub_location = getattr(self.session, "sub_location", None)

            if self.recall_orchestrator is not None:
                recall = await self.recall_orchestrator.recall(
                    world_id=self.world_id,
                    character_id=self.character_id,
                    seed_nodes=norm_seeds,
                    intent_type="roleplay",
                    chapter_id=chapter_id,
                    area_id=area_id,
                    location_id=sub_location,
                )
                payload = recall.model_dump() if hasattr(recall, "model_dump") else {"result": recall}
                payload["success"] = True
                payload["character_id"] = self.character_id
                return payload

            flash_service = getattr(self.flash_cpu, "flash_service", None) if self.flash_cpu else None
            if flash_service is None:
                return {"success": False, "error": "recall backend unavailable"}

            from app.models.flash import RecallRequest

            request = RecallRequest(
                seed_nodes=norm_seeds,
                include_subgraph=True,
                resolve_refs=True,
                use_subgraph=True,
                subgraph_depth=2,
            )
            recall = await flash_service.recall_memory(
                world_id=self.world_id,
                character_id=self.character_id,
                request=request,
            )
            payload = recall.model_dump() if hasattr(recall, "model_dump") else {"result": recall}
            payload["success"] = True
            payload["character_id"] = self.character_id
            return payload

        return await self._timed("recall_my_memory", args, _run())

    async def choose_my_combat_action(self, action_id: str) -> Dict[str, Any]:
        """战斗中由队友自己选择行动。"""
        args = {"action_id": action_id}

        async def _run() -> Dict[str, Any]:
            if not action_id:
                return {"success": False, "error": "missing action_id"}
            if not self.flash_cpu:
                return {"success": False, "error": "flash_cpu unavailable"}

            game_state = getattr(self.session, "game_state", None)
            combat_id = getattr(game_state, "combat_id", None) if game_state else None
            if not combat_id:
                return {"success": False, "error": "no active combat"}

            payload = await self.flash_cpu.call_combat_tool(
                "execute_action_for_actor",
                {
                    "combat_id": combat_id,
                    "actor_id": self.character_id,
                    "action_id": action_id,
                },
            )
            if isinstance(payload, dict) and payload.get("error"):
                payload = await self.flash_cpu.call_combat_tool(
                    "execute_action",
                    {
                        "combat_id": combat_id,
                        "action_id": action_id,
                    },
                )
            if not isinstance(payload, dict):
                payload = {"success": False, "error": "invalid combat response"}
            payload["actor_id"] = self.character_id
            payload["combat_id"] = combat_id
            payload["success"] = not bool(payload.get("error"))
            return payload

        return await self._timed("choose_my_combat_action", args, _run())

