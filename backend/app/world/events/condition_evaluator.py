"""condition_evaluator.py — 条件评估器 + 13 个条件处理器。

从 behavior_engine.py 拆出。纯函数式，所有上下文通过 TickContext 传入。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from app.exceptions import EventConditionError
from app.models.narrative import Condition, ConditionGroup, ConditionType
from app.world.graph.models import EvalResult, TickContext

logger = logging.getLogger(__name__)


class ConditionEvaluator:
    """条件评估器 — 从 AreaRuntime._evaluate_conditions 提取。

    纯函数式：所有上下文通过 TickContext 传入，不依赖 AreaRuntime 实例。
    完全复用 ConditionGroup/Condition 数据模型 (app.models.narrative)。

    9 种条件类型:
      8 种结构化（确定性）: LOCATION, NPC_INTERACTED, TIME_PASSED,
        ROUNDS_ELAPSED, PARTY_CONTAINS, EVENT_TRIGGERED,
        OBJECTIVE_COMPLETED, GAME_STATE
      1 种语义化（LLM）: FLASH_EVALUATE → 标记 pending，不在此评估
    """

    def evaluate(
        self,
        group: Optional[ConditionGroup],
        ctx: TickContext,
    ) -> EvalResult:
        """递归评估条件组。

        conditions=None → 永真（满足）。
        空条件列表 → 永真（满足）。
        """
        if group is None or not group.conditions:
            return EvalResult(satisfied=True)
        return self._evaluate_group(group, ctx)

    def _evaluate_group(
        self, group: ConditionGroup, ctx: TickContext
    ) -> EvalResult:
        """递归评估条件组。"""
        if not group.conditions:
            return EvalResult(satisfied=True)

        # NOT 运算符: 取反单个条件
        if group.operator == "not":
            inner = self._eval_single(group.conditions[0], ctx)
            return EvalResult(
                satisfied=not inner.satisfied if not inner.pending_flash else True,
                pending_flash=inner.pending_flash,
                details=inner.details,
            )

        # 评估所有子条件
        results = [self._eval_single(cond, ctx) for cond in group.conditions]

        # 聚合
        all_pending: List[Any] = []
        all_details: Dict[str, Any] = {}
        structural: List[bool] = []

        for r in results:
            all_pending.extend(r.pending_flash)
            all_details.update(r.details)
            structural.append(r.satisfied)

        if group.operator == "and":
            satisfied = all(structural)
        else:  # "or"
            satisfied = any(structural)

        return EvalResult(
            satisfied=satisfied,
            pending_flash=all_pending,
            details=all_details,
        )

    def _eval_single(
        self, cond: Any, ctx: TickContext
    ) -> EvalResult:
        """评估单个条件或嵌套组。"""
        # 递归: 嵌套 ConditionGroup
        if isinstance(cond, ConditionGroup):
            return self._evaluate_group(cond, ctx)

        # 解析 dict → Condition
        if not isinstance(cond, Condition):
            if isinstance(cond, dict):
                try:
                    cond = Condition(**cond)
                except (TypeError, ValidationError) as exc:
                    raise EventConditionError(f"畸形条件 dict: {exc}") from exc
            else:
                return EvalResult(satisfied=True)

        # 分发到处理器
        handler = _CONDITION_HANDLERS.get(cond.type)
        if handler is None:
            raise EventConditionError(f"未知条件类型: {cond.type}")

        return handler(cond, ctx)


# --- 13 个条件处理器 (模块级函数) ---


def _eval_location(cond: Condition, ctx: TickContext) -> EvalResult:
    """玩家是否在指定 area/sub_location。"""
    params = cond.params
    area_match = True
    sub_match = True

    if "area_id" in params:
        area_match = ctx.player_location == params["area_id"]
    if "sub_location" in params:
        sub_match = ctx.player_sub_location == params["sub_location"]

    satisfied = area_match and sub_match
    return EvalResult(
        satisfied=satisfied,
        details={"location": satisfied},
    )


def _eval_npc_interacted(cond: Condition, ctx: TickContext) -> EvalResult:
    """玩家是否与指定 NPC 交互了足够次数。"""
    npc_id = cond.params.get("npc_id", "")
    min_interactions = cond.params.get("min_interactions", 1)
    actual = ctx.npc_interactions.get(npc_id, 0)
    satisfied = actual >= min_interactions
    return EvalResult(
        satisfied=satisfied,
        details={f"npc_interacted:{npc_id}": satisfied},
    )


def _eval_time_passed(cond: Condition, ctx: TickContext) -> EvalResult:
    """游戏时间是否达到指定 day/hour。"""
    min_day = cond.params.get("min_day", 0)
    min_hour = cond.params.get("min_hour", 0)
    satisfied = (ctx.game_day > min_day) or (
        ctx.game_day == min_day and ctx.game_hour >= min_hour
    )
    return EvalResult(
        satisfied=satisfied,
        details={"time_passed": satisfied},
    )


def _eval_rounds_elapsed(cond: Condition, ctx: TickContext) -> EvalResult:
    """本章节回合数是否在指定范围内。"""
    min_rounds = cond.params.get("min_rounds", 0)
    max_rounds = cond.params.get("max_rounds", float("inf"))
    satisfied = min_rounds <= ctx.round_count <= max_rounds
    return EvalResult(
        satisfied=satisfied,
        details={"rounds_elapsed": satisfied},
    )


def _eval_party_contains(cond: Condition, ctx: TickContext) -> EvalResult:
    """指定角色是否在队伍中。"""
    character_id = cond.params.get("character_id", "")
    satisfied = character_id in ctx.party_members
    return EvalResult(
        satisfied=satisfied,
        details={f"party_contains:{character_id}": satisfied},
    )


def _eval_event_triggered(cond: Condition, ctx: TickContext) -> EvalResult:
    """指定事件是否已触发。"""
    event_id = cond.params.get("event_id", "")
    satisfied = event_id in ctx.events_triggered
    return EvalResult(
        satisfied=satisfied,
        details={f"event_triggered:{event_id}": satisfied},
    )


def _eval_objective_completed(cond: Condition, ctx: TickContext) -> EvalResult:
    """指定目标是否已完成。"""
    objective_id = cond.params.get("objective_id", "")
    satisfied = objective_id in ctx.objectives_completed
    return EvalResult(
        satisfied=satisfied,
        details={f"objective_completed:{objective_id}": satisfied},
    )


def _eval_game_state(cond: Condition, ctx: TickContext) -> EvalResult:
    """游戏状态是否匹配。"""
    required_state = cond.params.get("state", "")
    satisfied = ctx.game_state == required_state
    return EvalResult(
        satisfied=satisfied,
        details={f"game_state:{required_state}": satisfied},
    )


def _eval_flash_evaluate(cond: Condition, ctx: TickContext) -> EvalResult:
    """语义条件 — 查缓存或标记 pending（E3 pending_flash 闭环）。

    pre-tick: 无缓存 → satisfied=False + pending_flash，行为暂不触发，等待 LLM 评估。
    post-tick: LLM 已调用 report_flash_evaluation 写入缓存 → 用真实结果判定。
    """
    prompt = cond.params.get("prompt", "")

    # 有 LLM 回报结果时直接使用（post-tick 路径）
    if prompt and prompt in ctx.flash_results:
        result = ctx.flash_results[prompt]
        return EvalResult(
            satisfied=result,
            details={"flash_evaluate": True, "from_cache": True, "result": result},
        )

    # 无缓存：标记 pending，行为不触发（pre-tick 路径）
    return EvalResult(
        satisfied=False,
        pending_flash=[cond],
        details={"flash_evaluate": True, "pending": True},
    )


def _eval_world_flag(cond: Condition, ctx: TickContext) -> EvalResult:
    """检查 world_root.state.world_flags 中的标记值。

    params:
        key:   标记名（如 "goblin_nest_cleared"）
        value: 期望值（任意类型，使用 == 比较）

    若 key 不存在，视为 None（与 value=None 的条件匹配）。
    """
    key = cond.params.get("key", "")
    expected = cond.params.get("value")
    actual = ctx.world_flags.get(key)
    satisfied = actual == expected
    return EvalResult(
        satisfied=satisfied,
        details={f"world_flag:{key}": satisfied, "actual": actual, "expected": expected},
    )


def _eval_faction_reputation(cond: Condition, ctx: TickContext) -> EvalResult:
    """检查阵营声望阈值，从 TickContext.faction_reputations 读取。

    params:
        faction: 阵营 ID（如 "adventurer_guild"）
        gte:     声望 >= 该值（inclusive），可选
        lte:     声望 <= 该值（inclusive），可选
        gt:      声望 > 该值，可选
        lt:      声望 < 该值，可选

    支持多个阈值组合（如 gte=10 且 lte=80 表示声望在 10-80 区间内）。
    faction 不存在时视为 0。
    """
    faction = cond.params.get("faction", "")
    value = ctx.faction_reputations.get(faction, 0)

    satisfied = True
    if "gte" in cond.params and value < cond.params["gte"]:
        satisfied = False
    if "lte" in cond.params and value > cond.params["lte"]:
        satisfied = False
    if "gt" in cond.params and value <= cond.params["gt"]:
        satisfied = False
    if "lt" in cond.params and value >= cond.params["lt"]:
        satisfied = False

    return EvalResult(
        satisfied=satisfied,
        details={f"faction_reputation:{faction}": satisfied, "value": value},
    )


def _eval_event_rounds_elapsed(cond: Condition, ctx: TickContext) -> EvalResult:
    """检查事件自激活（或进入 COOLDOWN）后经过的回合数 (E4/U9)。

    params:
        event_id:   事件节点 ID
        min_rounds: 最少经过回合数（inclusive）

    计算：ctx.round_count - event_node.state.get("activated_at_round", 0)
    若 activated_at_round 为 None，视为 0（防御性处理）。
    """
    event_id = cond.params.get("event_id", "")
    min_rounds = cond.params.get("min_rounds", 0)
    wg = getattr(ctx.session, "world_graph", None) if ctx.session else None
    if not wg:
        return EvalResult(satisfied=False, details={"error": "no_wg"})
    node = wg.get_node(event_id)
    if not node:
        return EvalResult(satisfied=False, details={"error": "node_not_found"})
    activated_at = node.state.get("activated_at_round") or 0
    elapsed = ctx.round_count - activated_at
    satisfied = elapsed >= min_rounds
    return EvalResult(satisfied=satisfied, details={"elapsed": elapsed, "min": min_rounds})


def _eval_event_state(cond: Condition, ctx: TickContext) -> EvalResult:
    """检查事件节点的运行时 state 字段 (U4)。

    params:
        event_id: 事件节点 ID
        key: state 中的字段名 (如 "status", "current_stage")
        value: 期望值
    """
    event_id = cond.params.get("event_id", "")
    key = cond.params.get("key", "")
    expected = cond.params.get("value")

    wg = getattr(ctx.session, "world_graph", None) if ctx.session else None
    if not wg:
        return EvalResult(satisfied=False, details={"event_state": "no_wg"})

    node = wg.get_node(event_id)
    if not node:
        return EvalResult(satisfied=False, details={"event_state": "not_found"})

    actual = node.state.get(key)
    satisfied = actual == expected
    return EvalResult(
        satisfied=satisfied,
        details={f"event_state:{event_id}.{key}": satisfied},
    )


# 条件类型 → 处理器映射
_CONDITION_HANDLERS = {
    ConditionType.LOCATION: _eval_location,
    ConditionType.NPC_INTERACTED: _eval_npc_interacted,
    ConditionType.TIME_PASSED: _eval_time_passed,
    ConditionType.ROUNDS_ELAPSED: _eval_rounds_elapsed,
    ConditionType.PARTY_CONTAINS: _eval_party_contains,
    ConditionType.EVENT_TRIGGERED: _eval_event_triggered,
    ConditionType.OBJECTIVE_COMPLETED: _eval_objective_completed,
    ConditionType.GAME_STATE: _eval_game_state,
    ConditionType.EVENT_STATE: _eval_event_state,
    ConditionType.EVENT_ROUNDS_ELAPSED: _eval_event_rounds_elapsed,
    ConditionType.WORLD_FLAG: _eval_world_flag,
    ConditionType.FACTION_REPUTATION: _eval_faction_reputation,
    ConditionType.FLASH_EVALUATE: _eval_flash_evaluate,
}
