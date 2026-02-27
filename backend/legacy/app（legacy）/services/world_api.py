"""L2 WorldAPI — unified boundary between L1 Agent tools and L3 SessionRuntime.

All immersive tools call ctx.api.xxx() instead of ctx.session.xxx().
WorldAPI delegates every call to SessionRuntime with no extra logic.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class WorldAPI:
    """Thin L2 facade over SessionRuntime for agent tool consumption."""

    def __init__(self, session: Any, role: str = "", agent_id: str = ""):
        self._session = session
        self.role = role
        self.agent_id = agent_id

    # -- Stat Ops ----------------------------------------------------------

    def heal(self, amount: int) -> Dict[str, Any]:
        return self._session.heal(amount)

    def damage(self, amount: int) -> Dict[str, Any]:
        return self._session.damage(amount)

    def add_xp(self, amount: int) -> Dict[str, Any]:
        return self._session.add_xp(amount)

    def add_item(self, item_id: str, item_name: str, quantity: int = 1) -> Dict[str, Any]:
        return self._session.add_item(item_id, item_name, quantity)

    def remove_item(self, item_id: str, quantity: int = 1) -> Dict[str, Any]:
        return self._session.remove_item(item_id, quantity)

    # -- Event Ops ---------------------------------------------------------

    def activate_event(self, event_id: str) -> Dict[str, Any]:
        return self._session.activate_event(event_id)

    def complete_event(self, event_id: str, outcome_key: str = "") -> Dict[str, Any]:
        return self._session.complete_event(event_id, outcome_key)

    def fail_event(self, event_id: str, reason: str = "") -> Dict[str, Any]:
        return self._session.fail_event(event_id, reason)

    def advance_stage(self, event_id: str, stage_id: str = "") -> Dict[str, Any]:
        return self._session.advance_stage(event_id, stage_id)

    def complete_event_objective(self, event_id: str, objective_id: str) -> Dict[str, Any]:
        return self._session.complete_event_objective(event_id, objective_id)

    # -- Narrative Ops -----------------------------------------------------

    def advance_chapter(self, target_chapter_id: str, transition_type: str = "normal") -> Dict[str, Any]:
        return self._session.advance_chapter(target_chapter_id, transition_type)

    def complete_objective(self, objective_id: str) -> Dict[str, Any]:
        return self._session.complete_objective(objective_id)

    def update_disposition(self, npc_id: str, deltas: Dict[str, int], reason: str = "") -> Dict[str, Any]:
        return self._session.update_disposition(npc_id, deltas, reason)

    # -- Memory Ops --------------------------------------------------------

    async def recall(self, role: str, actor_id: str, seeds: List[str],
                     intent_type: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        return await self._session.recall(role, actor_id, seeds, intent_type, limit)

    def record_memory(self, owner_id: str, memory_type: str, name: str, summary: str,
                      importance: float, role: str, **props: Any) -> str:
        return self._session.record_memory(owner_id, memory_type, name, summary, importance, role, **props)

    # -- Flash Results -----------------------------------------------------

    def set_flash_result(self, prompt: str, result: bool) -> None:
        self._session.flash_results[prompt] = bool(result)
