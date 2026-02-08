"""
ConditionEngine - 纯机械条件评估引擎。

无 LLM 调用、无异步、O(1) 查表。
FLASH_EVALUATE 类型条件标记为 pending，由调用方注入 Flash prompt。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.models.narrative import (
    Condition,
    ConditionGroup,
    ConditionType,
)

logger = logging.getLogger(__name__)


@dataclass
class GameContext:
    """每回合不可变快照，由 AdminCoordinator 构建。"""
    session_id: str
    area_id: str
    sub_location: Optional[str]
    game_day: int
    game_hour: int
    game_minute: int
    game_state: str  # "exploring" | "in_dialogue" | "combat"
    active_npc: Optional[str]
    party_member_ids: List[str]
    events_triggered: List[str]
    objectives_completed: List[str]
    rounds_in_chapter: int
    npc_interactions: Dict[str, int]
    event_cooldowns: Dict[str, int] = field(default_factory=dict)
    rounds_since_last_progress: int = 0
    player_input: str = ""
    execution_summary: str = ""
    conversation_history: str = ""


@dataclass
class ConditionResult:
    """条件评估结果。"""
    satisfied: bool  # 结构化条件是否全部满足
    pending_flash: List[Condition] = field(default_factory=list)
    details: Dict[str, bool] = field(default_factory=dict)


class ConditionEngine:
    """纯机械条件引擎，同步评估。"""

    def evaluate(self, group: ConditionGroup, ctx: GameContext) -> ConditionResult:
        """递归评估条件组。"""
        if not group.conditions:
            return ConditionResult(satisfied=True)

        if group.operator == "not":
            if not group.conditions:
                return ConditionResult(satisfied=True)
            inner = self._evaluate_single(group.conditions[0], ctx)
            # NOT 不影响 pending_flash 传递
            return ConditionResult(
                satisfied=not inner.satisfied if not inner.pending_flash else True,
                pending_flash=inner.pending_flash,
                details=inner.details,
            )

        results: List[ConditionResult] = []
        for cond in group.conditions:
            results.append(self._evaluate_single(cond, ctx))

        all_pending_flash: List[Condition] = []
        all_details: Dict[str, bool] = {}
        structural_results: List[bool] = []

        for r in results:
            all_pending_flash.extend(r.pending_flash)
            all_details.update(r.details)
            structural_results.append(r.satisfied)

        if group.operator == "and":
            satisfied = all(structural_results)
        else:  # "or"
            satisfied = any(structural_results)

        return ConditionResult(
            satisfied=satisfied,
            pending_flash=all_pending_flash,
            details=all_details,
        )

    def _evaluate_single(
        self,
        cond: Any,  # Condition | ConditionGroup
        ctx: GameContext,
    ) -> ConditionResult:
        """评估单个条件或嵌套条件组。"""
        if isinstance(cond, ConditionGroup):
            return self.evaluate(cond, ctx)

        if not isinstance(cond, Condition):
            # 鲁棒处理：dict → Condition
            if isinstance(cond, dict):
                try:
                    cond = Condition(**cond)
                except Exception:
                    return ConditionResult(satisfied=True)
            else:
                return ConditionResult(satisfied=True)

        handler = self._HANDLERS.get(cond.type)
        if handler is None:
            logger.warning("[ConditionEngine] 未知条件类型: %s", cond.type)
            return ConditionResult(satisfied=True)
        return handler(self, cond, ctx)

    # ----- 8 种结构化处理器 + FLASH_EVALUATE -----

    def _eval_location(self, cond: Condition, ctx: GameContext) -> ConditionResult:
        params = cond.params
        area_match = True
        sub_match = True
        if "area_id" in params:
            area_match = ctx.area_id == params["area_id"]
        if "sub_location" in params:
            sub_match = ctx.sub_location == params["sub_location"]
        satisfied = area_match and sub_match
        return ConditionResult(
            satisfied=satisfied,
            details={"location": satisfied},
        )

    def _eval_npc_interacted(self, cond: Condition, ctx: GameContext) -> ConditionResult:
        npc_id = cond.params.get("npc_id", "")
        min_interactions = cond.params.get("min_interactions", 1)
        actual = ctx.npc_interactions.get(npc_id, 0)
        satisfied = actual >= min_interactions
        return ConditionResult(
            satisfied=satisfied,
            details={f"npc_interacted:{npc_id}": satisfied},
        )

    def _eval_time_passed(self, cond: Condition, ctx: GameContext) -> ConditionResult:
        min_day = cond.params.get("min_day", 0)
        min_hour = cond.params.get("min_hour", 0)
        satisfied = (ctx.game_day > min_day) or (
            ctx.game_day == min_day and ctx.game_hour >= min_hour
        )
        return ConditionResult(
            satisfied=satisfied,
            details={"time_passed": satisfied},
        )

    def _eval_rounds_elapsed(self, cond: Condition, ctx: GameContext) -> ConditionResult:
        min_rounds = cond.params.get("min_rounds", 0)
        max_rounds = cond.params.get("max_rounds", float("inf"))
        satisfied = min_rounds <= ctx.rounds_in_chapter <= max_rounds
        return ConditionResult(
            satisfied=satisfied,
            details={"rounds_elapsed": satisfied},
        )

    def _eval_party_contains(self, cond: Condition, ctx: GameContext) -> ConditionResult:
        character_id = cond.params.get("character_id", "")
        satisfied = character_id in ctx.party_member_ids
        return ConditionResult(
            satisfied=satisfied,
            details={f"party_contains:{character_id}": satisfied},
        )

    def _eval_event_triggered(self, cond: Condition, ctx: GameContext) -> ConditionResult:
        event_id = cond.params.get("event_id", "")
        satisfied = event_id in ctx.events_triggered
        return ConditionResult(
            satisfied=satisfied,
            details={f"event_triggered:{event_id}": satisfied},
        )

    def _eval_objective_completed(self, cond: Condition, ctx: GameContext) -> ConditionResult:
        objective_id = cond.params.get("objective_id", "")
        satisfied = objective_id in ctx.objectives_completed
        return ConditionResult(
            satisfied=satisfied,
            details={f"objective_completed:{objective_id}": satisfied},
        )

    def _eval_game_state(self, cond: Condition, ctx: GameContext) -> ConditionResult:
        required_state = cond.params.get("state", "")
        satisfied = ctx.game_state == required_state
        return ConditionResult(
            satisfied=satisfied,
            details={f"game_state:{required_state}": satisfied},
        )

    def _eval_flash_evaluate(self, cond: Condition, ctx: GameContext) -> ConditionResult:
        """语义条件不在此处理，标记为 pending。"""
        return ConditionResult(
            satisfied=True,  # 结构化部分视为满足
            pending_flash=[cond],
            details={"flash_evaluate": True},
        )

    _HANDLERS = {
        ConditionType.LOCATION: _eval_location,
        ConditionType.NPC_INTERACTED: _eval_npc_interacted,
        ConditionType.TIME_PASSED: _eval_time_passed,
        ConditionType.ROUNDS_ELAPSED: _eval_rounds_elapsed,
        ConditionType.PARTY_CONTAINS: _eval_party_contains,
        ConditionType.EVENT_TRIGGERED: _eval_event_triggered,
        ConditionType.OBJECTIVE_COMPLETED: _eval_objective_completed,
        ConditionType.GAME_STATE: _eval_game_state,
        ConditionType.FLASH_EVALUATE: _eval_flash_evaluate,
    }
