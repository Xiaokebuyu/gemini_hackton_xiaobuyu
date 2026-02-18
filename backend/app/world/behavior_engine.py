"""
BehaviorEngine -- Step C4

行为评估引擎：tick + 条件评估 + action 执行。
替代 AreaRuntime.check_events() 的硬编码事件状态机。

设计文档: 架构与设计/世界底层重构与战斗系统设计专项/世界活图详细设计.md §6

包含三个类:
  - ConditionEvaluator: 条件评估（从 AreaRuntime._evaluate_conditions 提取）
  - ActionExecutor: Action 执行（CHANGE_STATE / EMIT_EVENT / SPAWN / ...）
  - BehaviorEngine: 主引擎（tick / handle_event / handle_enter / handle_exit）
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from app.models.narrative import Condition, ConditionGroup, ConditionType
from app.world.event_propagation import EventPropagator
from app.world.models import (
    Action,
    ActionType,
    Behavior,
    BehaviorResult,
    EvalResult,
    EventStatus,
    TickContext,
    TickResult,
    TriggerType,
    WorldEdgeType,
    WorldEvent,
    WorldNode,
    WorldNodeType,
)

# 避免循环导入
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.world.world_graph import WorldGraph

logger = logging.getLogger(__name__)


# =============================================================================
# ConditionEvaluator
# =============================================================================


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
                except Exception:
                    return EvalResult(satisfied=True)
            else:
                return EvalResult(satisfied=True)

        # 分发到处理器
        handler = _CONDITION_HANDLERS.get(cond.type)
        if handler is None:
            logger.warning(
                "[ConditionEvaluator] 未知条件类型: %s", cond.type
            )
            return EvalResult(satisfied=True)

        return handler(cond, ctx)


# --- 9 个条件处理器 (模块级函数) ---


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


# =============================================================================
# ActionExecutor
# =============================================================================


class ActionExecutor:
    """Action 执行器 — 将 Action 应用到 WorldGraph。

    6 种 ActionType:
      CHANGE_STATE:   修改目标节点 state（立即生效）
      EMIT_EVENT:     创建 WorldEvent（返回给调用方传播）
      NARRATIVE_HINT: 收集叙事指令文本
      SPAWN:          创建新节点 + 可选 CONTAINS 边
      REMOVE:         删除节点（级联删除后代）
      CHANGE_EDGE:    修改/添加/删除边
    """

    def __init__(self, wg: WorldGraph) -> None:
        self.wg = wg

    def execute(
        self,
        action: Action,
        source_node_id: str,
        ctx: TickContext,
    ) -> _ActionResult:
        """执行单个 Action。

        Args:
            action: 要执行的动作
            source_node_id: behavior 所属节点 ID（用于解析 "self" target）
            ctx: 当前 tick 上下文

        Returns:
            _ActionResult 包含执行结果
        """
        target_id = self._resolve_target(action.target, source_node_id)

        if action.type == ActionType.CHANGE_STATE:
            return self._exec_change_state(action, target_id)
        elif action.type == ActionType.EMIT_EVENT:
            return self._exec_emit_event(action, source_node_id, ctx)
        elif action.type == ActionType.NARRATIVE_HINT:
            return self._exec_narrative_hint(action)
        elif action.type == ActionType.SPAWN:
            return self._exec_spawn(action)
        elif action.type == ActionType.REMOVE:
            return self._exec_remove(action, target_id)
        elif action.type == ActionType.CHANGE_EDGE:
            return self._exec_change_edge(action)
        else:
            logger.warning(
                "[ActionExecutor] 未知 ActionType: %s", action.type
            )
            return _ActionResult()

    def _resolve_target(self, target: str, source_node_id: str) -> str:
        """解析 target 特殊值。"""
        if target == "self":
            return source_node_id
        elif target == "parent":
            parent = self.wg.get_parent(source_node_id)
            return parent or source_node_id
        elif target == "player":
            return "player"
        return target

    def _exec_change_state(
        self, action: Action, target_id: str
    ) -> _ActionResult:
        """CHANGE_STATE: 修改目标节点 state。"""
        updates = action.params.get("updates", {})
        merge = action.params.get("merge", True)

        if not self.wg.has_node(target_id):
            logger.warning(
                "[ActionExecutor] CHANGE_STATE 目标节点 '%s' 不存在",
                target_id,
            )
            return _ActionResult()

        if merge:
            self.wg.merge_state(target_id, updates)
        else:
            # 完全替换 state
            node = self.wg.get_node(target_id)
            if node:
                node.state = dict(updates)
                from datetime import datetime
                node.updated_at = datetime.now()
                self.wg._dirty_nodes.add(target_id)

        return _ActionResult(
            state_changes={target_id: dict(updates)},
        )

    def _exec_emit_event(
        self, action: Action, source_node_id: str, ctx: TickContext
    ) -> _ActionResult:
        """EMIT_EVENT: 创建 WorldEvent（不传播，返回给调用方）。"""
        event = WorldEvent(
            event_type=action.params.get("event_type", "unknown"),
            origin_node=source_node_id,
            game_day=ctx.game_day,
            game_hour=ctx.game_hour,
            data=action.params.get("data", {}),
            visibility=action.params.get("visibility", "scope"),
        )
        return _ActionResult(emitted_event=event)

    def _exec_narrative_hint(self, action: Action) -> _ActionResult:
        """NARRATIVE_HINT: 收集叙事指令。"""
        text = action.params.get("text", "")
        return _ActionResult(narrative_hint=text) if text else _ActionResult()

    def _exec_spawn(self, action: Action) -> _ActionResult:
        """SPAWN: 创建新节点。"""
        node_data = action.params.get("node", {})
        parent_id = action.params.get("parent", "")

        if not isinstance(node_data, dict) or "id" not in node_data:
            logger.warning("[ActionExecutor] SPAWN 缺少 node.id")
            return _ActionResult()

        node = WorldNode(
            id=node_data["id"],
            type=node_data.get("type", "npc"),
            name=node_data.get("name", node_data["id"]),
            properties=node_data.get("properties", {}),
            state=node_data.get("state", {}),
        )
        self.wg.add_node(node)

        if parent_id and self.wg.has_node(parent_id):
            self.wg.add_edge(
                parent_id,
                node.id,
                WorldEdgeType.CONTAINS.value,
            )

        return _ActionResult()

    def _exec_remove(self, action: Action, target_id: str) -> _ActionResult:
        """REMOVE: 删除节点（级联后代）。"""
        if not self.wg.has_node(target_id):
            logger.warning(
                "[ActionExecutor] REMOVE 目标节点 '%s' 不存在",
                target_id,
            )
            return _ActionResult()

        try:
            self.wg.remove_node(target_id)
        except KeyError:
            pass
        return _ActionResult()

    def _exec_change_edge(self, action: Action) -> _ActionResult:
        """CHANGE_EDGE: 修改/添加/删除边。

        params.operation:
          "update" (默认): 更新已有边属性
          "add": 添加新边
          "remove": 删除边
        """
        operation = action.params.get("operation", "update")
        source = action.params.get("source", "")
        target = action.params.get("target", "")
        key = action.params.get("key")

        if not source or not target:
            logger.warning("[ActionExecutor] CHANGE_EDGE 缺少 source/target")
            return _ActionResult()

        if operation == "add":
            # 添加新边
            relation = action.params.get("relation", key or "")
            if not relation:
                logger.warning("[ActionExecutor] CHANGE_EDGE add 缺少 relation")
                return _ActionResult()
            if not self.wg.has_node(source) or not self.wg.has_node(target):
                logger.warning(
                    "[ActionExecutor] CHANGE_EDGE add 节点不存在: %s → %s",
                    source, target,
                )
                return _ActionResult()
            attrs = action.params.get("attrs", {})
            self.wg.add_edge(source, target, relation, key=key, **attrs)
            return _ActionResult()

        elif operation == "remove":
            # 删除边
            self.wg.remove_edge(source, target, key=key)
            return _ActionResult()

        else:
            # update: 更新已有边属性
            updates = action.params.get("updates", {})
            if not self.wg.has_node(source) or not self.wg.has_node(target):
                logger.warning(
                    "[ActionExecutor] CHANGE_EDGE 节点不存在: %s → %s",
                    source, target,
                )
                return _ActionResult()

            if key:
                try:
                    self.wg.update_edge(source, target, key, updates)
                except KeyError:
                    logger.warning(
                        "[ActionExecutor] CHANGE_EDGE 边不存在: (%s, %s, %s)",
                        source, target, key,
                    )
            else:
                # 无 key 时尝试更新第一条边
                edge = self.wg.get_edge(source, target)
                if edge:
                    edge.update(updates)
                    self.wg._dirty_edges.add((source, target))

            return _ActionResult()


class _ActionResult:
    """ActionExecutor.execute() 的内部返回值。"""
    __slots__ = ("state_changes", "emitted_event", "narrative_hint")

    def __init__(
        self,
        state_changes: Optional[Dict[str, Dict[str, Any]]] = None,
        emitted_event: Optional[WorldEvent] = None,
        narrative_hint: str = "",
    ) -> None:
        self.state_changes = state_changes or {}
        self.emitted_event = emitted_event
        self.narrative_hint = narrative_hint


# =============================================================================
# BehaviorEngine
# =============================================================================


class BehaviorEngine:
    """行为评估引擎 — 世界活图的心跳。

    替代 AreaRuntime.check_events()。

    核心方法:
      tick()          — 每回合主循环
      handle_event()  — 处理外部事件（工具调用后）
      handle_enter()  — 实体进入位置
      handle_exit()   — 实体离开位置

    Usage::

        engine = BehaviorEngine(world_graph)
        result = engine.tick(ctx)
        # result.narrative_hints → 注入 LLM
        # result.all_events → 传给前端
    """

    # 级联限制
    MAX_CASCADING_ROUNDS = 5
    MAX_EVENTS_PER_TICK = 20

    def __init__(self, wg: WorldGraph) -> None:
        self.wg = wg
        self._evaluator = ConditionEvaluator()
        self._executor = ActionExecutor(wg)
        self._propagator = EventPropagator(wg)

    # -----------------------------------------------------------------
    # 公开方法
    # -----------------------------------------------------------------

    def tick(self, ctx: TickContext) -> TickResult:
        """每回合主循环。

        1. 递减活跃节点 behavior 冷却
        2. 确定活跃范围
        3. 收集 ON_TICK / ON_TIME / ON_DISPOSITION behaviors
        4. 按 priority 降序排序
        5. 逐个评估 conditions + 执行 actions
        6. EMIT_EVENT → 传播 + 级联
        7. 返回 TickResult
        """
        active_nodes = self._get_active_nodes(ctx)

        # 1. 递减冷却（在评估前）
        for nid in active_nodes:
            node = self.wg.get_node(nid)
            if node:
                node.tick_cooldowns()

        # 2. 收集 tick 阶段触发的 behaviors
        tick_triggers = {TriggerType.ON_TICK, TriggerType.ON_TIME, TriggerType.ON_DISPOSITION}
        behaviors_to_eval: List[Tuple[WorldNode, Behavior]] = []

        for nid in active_nodes:
            node = self.wg.get_node(nid)
            if not node:
                continue
            for trigger in tick_triggers:
                for bh in node.get_active_behaviors(trigger):
                    # ON_TIME: 检查 time_condition 过滤器
                    if trigger == TriggerType.ON_TIME:
                        if not self._matches_time_filter(bh, ctx):
                            continue
                    # ON_DISPOSITION: 检查 disposition_filter
                    if trigger == TriggerType.ON_DISPOSITION:
                        if not self._matches_disposition_filter(bh, node):
                            continue
                    behaviors_to_eval.append((node, bh))

        # 3. 按 priority 降序排序
        behaviors_to_eval.sort(key=lambda x: x[1].priority, reverse=True)

        # 4. 逐个评估 + 执行
        all_results: List[BehaviorResult] = []
        all_events: List[WorldEvent] = []
        all_hints: List[str] = []
        all_pending: List[Any] = []
        all_state_changes: Dict[str, Dict[str, Any]] = {}

        for node, bh in behaviors_to_eval:
            result = self._evaluate_and_execute(node, bh, ctx)
            if result is None:
                continue

            all_results.append(result)
            all_hints.extend(result.narrative_hints)
            all_pending.extend(result.pending_flash)

            # 收集 state_changes
            for nid, changes in result.state_changes.items():
                all_state_changes.setdefault(nid, {}).update(changes)

            # 收集待传播的事件
            if result.events_emitted:
                all_events.extend(result.events_emitted)

        # 5. 传播事件 + 级联
        if all_events:
            cascade_results, cascade_events = self._propagate_with_cascade(
                all_events, ctx
            )
            all_results.extend(cascade_results)
            all_events.extend(cascade_events)

            # 收集级联产生的 hints / state_changes / pending_flash
            for r in cascade_results:
                all_hints.extend(r.narrative_hints)
                all_pending.extend(r.pending_flash)
                for nid, changes in r.state_changes.items():
                    all_state_changes.setdefault(nid, {}).update(changes)

        # 6. ON_STATE_CHANGE: 处理本 tick 内（含 B 阶段工具调用）积累的 state 变更
        state_changes_map = self.wg.get_and_clear_state_changes()
        for nid, changed_keys in state_changes_map.items():
            node = self.wg.get_node(nid)
            if not node:
                continue
            for bh in node.get_active_behaviors(TriggerType.ON_STATE_CHANGE):
                if not bh.watch_key or bh.watch_key not in changed_keys:
                    continue
                result = self._evaluate_and_execute(node, bh, ctx)
                if result is None:
                    continue
                all_results.append(result)
                all_hints.extend(result.narrative_hints)
                all_pending.extend(result.pending_flash)
                for nid2, changes in result.state_changes.items():
                    all_state_changes.setdefault(nid2, {}).update(changes)
                if result.events_emitted:
                    all_events.extend(result.events_emitted)

        # 7. 记录事件日志
        for event in all_events:
            self.wg.log_event(event)

        return TickResult(
            results=all_results,
            all_events=all_events,
            narrative_hints=all_hints,
            pending_flash=all_pending,
            state_changes=all_state_changes,
        )

    def handle_event(
        self, event: WorldEvent, ctx: TickContext
    ) -> TickResult:
        """处理单个外部事件（工具调用后触发）。

        1. 记录事件日志
        2. 传播事件 + 评估 ON_EVENT behaviors
        3. 处理级联
        """
        self.wg.log_event(event)

        all_results: List[BehaviorResult] = []
        all_events: List[WorldEvent] = [event]
        all_hints: List[str] = []
        all_pending: List[Any] = []
        all_state_changes: Dict[str, Dict[str, Any]] = {}

        # 先评估 origin 节点自身的 ON_EVENT behaviors
        origin_emitted: List[WorldEvent] = []
        origin_node = self.wg.get_node(event.origin_node)
        if origin_node:
            for bh in origin_node.get_active_behaviors(TriggerType.ON_EVENT):
                if not self._matches_event_filter(bh, event):
                    continue
                result = self._evaluate_and_execute(origin_node, bh, ctx)
                if result:
                    all_results.append(result)
                    all_hints.extend(result.narrative_hints)
                    all_pending.extend(result.pending_flash)
                    for nid, changes in result.state_changes.items():
                        all_state_changes.setdefault(nid, {}).update(changes)
                    if result.events_emitted:
                        all_events.extend(result.events_emitted)
                        origin_emitted.extend(result.events_emitted)

        # 传播 + 级联（包含原始事件 + origin 产生的新事件）
        events_to_propagate = [event] + origin_emitted
        cascade_results, cascade_events = self._propagate_with_cascade(
            events_to_propagate, ctx
        )
        all_results.extend(cascade_results)
        all_events.extend(cascade_events)

        for r in cascade_results:
            all_hints.extend(r.narrative_hints)
            all_pending.extend(r.pending_flash)
            for nid, changes in r.state_changes.items():
                all_state_changes.setdefault(nid, {}).update(changes)

        # 记录级联事件
        for evt in cascade_events:
            self.wg.log_event(evt)

        return TickResult(
            results=all_results,
            all_events=all_events,
            narrative_hints=all_hints,
            pending_flash=all_pending,
            state_changes=all_state_changes,
        )

    def handle_enter(
        self, entity_id: str, location_id: str, ctx: TickContext
    ) -> TickResult:
        """实体进入位置 → ON_ENTER behaviors。"""
        return self._handle_location_trigger(
            TriggerType.ON_ENTER, entity_id, location_id, ctx
        )

    def handle_exit(
        self, entity_id: str, location_id: str, ctx: TickContext
    ) -> TickResult:
        """实体离开位置 → ON_EXIT behaviors。"""
        return self._handle_location_trigger(
            TriggerType.ON_EXIT, entity_id, location_id, ctx
        )

    # -----------------------------------------------------------------
    # 内部方法 — 活跃范围
    # -----------------------------------------------------------------

    def _get_active_nodes(self, ctx: TickContext) -> List[str]:
        """确定本次 tick 需要评估的节点集合。

        活跃范围:
          1. 玩家位置的作用域链 (location → area → region → world_root)
          2. 当前位置 / 区域的所有实体 (NPC, 事件, 物品)
          3. camp + 队友（始终活跃）
          4. 所有 chapter 节点（GATE 解锁需要每 tick 检查）
        """
        active: Set[str] = set()

        # 1. 玩家位置的作用域链
        if ctx.player_location and self.wg.has_node(ctx.player_location):
            active.update(self.wg.get_scope_chain(ctx.player_location))

            # 2. 当前位置和区域的实体
            active.update(self.wg.get_entities_at(ctx.player_location))
            area_id = self.wg.get_parent(ctx.player_location)
            if area_id:
                active.update(self.wg.get_entities_at(area_id))
                active.update(self.wg.find_events_in_scope(area_id))

        # 3. camp + 队友
        if self.wg.has_node("camp"):
            active.add("camp")
        for mid in ctx.party_members:
            if self.wg.has_node(mid):
                active.add(mid)

        # 4. 所有 chapter 节点
        active.update(self.wg.get_by_type(WorldNodeType.CHAPTER))

        return list(active)

    # -----------------------------------------------------------------
    # 内部方法 — 评估 + 执行
    # -----------------------------------------------------------------

    def _evaluate_and_execute(
        self,
        node: WorldNode,
        behavior: Behavior,
        ctx: TickContext,
    ) -> Optional[BehaviorResult]:
        """评估单个 behavior 的 conditions，满足则执行 actions。

        Returns:
            BehaviorResult 如果触发，None 如果条件不满足。
        """
        # 评估条件
        eval_result = self._evaluator.evaluate(behavior.conditions, ctx)
        if not eval_result.satisfied:
            # E3: 即使条件不满足，若有 pending_flash（FLASH_EVALUATE 等待 LLM 评估），
            # 也需要向上传播，让 pipeline 注入 context_dict。行为本身不触发。
            if eval_result.pending_flash:
                return BehaviorResult(
                    behavior_id=behavior.id,
                    node_id=node.id,
                    trigger=behavior.trigger,
                    actions_executed=[],
                    events_emitted=[],
                    state_changes={},
                    narrative_hints=[],
                    pending_flash=eval_result.pending_flash,
                )
            return None

        # 执行 actions
        events_emitted: List[WorldEvent] = []
        state_changes: Dict[str, Dict[str, Any]] = {}
        narrative_hints: List[str] = []

        for action in behavior.actions:
            ar = self._executor.execute(action, node.id, ctx)

            if ar.state_changes:
                for nid, changes in ar.state_changes.items():
                    state_changes.setdefault(nid, {}).update(changes)

            if ar.emitted_event:
                events_emitted.append(ar.emitted_event)

            if ar.narrative_hint:
                narrative_hints.append(ar.narrative_hint)

        # 标记 once / cooldown
        if behavior.once:
            node.mark_behavior_fired(behavior.id)
        if behavior.cooldown_ticks > 0:
            # +1 补偿: 下次 tick 开始时 tick_cooldowns() 会立即减 1，
            # 所以设置 N+1 确保实际阻塞 N 个 tick
            node.set_behavior_cooldown(behavior.id, behavior.cooldown_ticks + 1)

        return BehaviorResult(
            behavior_id=behavior.id,
            node_id=node.id,
            trigger=behavior.trigger,
            actions_executed=list(behavior.actions),
            events_emitted=events_emitted,
            state_changes=state_changes,
            narrative_hints=narrative_hints,
            pending_flash=eval_result.pending_flash,
        )

    # -----------------------------------------------------------------
    # 内部方法 — 传播 + 级联
    # -----------------------------------------------------------------

    def _propagate_with_cascade(
        self,
        initial_events: List[WorldEvent],
        ctx: TickContext,
    ) -> Tuple[List[BehaviorResult], List[WorldEvent]]:
        """传播事件 + 级联处理（有深度限制）。

        Returns:
            (cascade_results, cascade_events)
            cascade_events 不包含 initial_events。
        """
        all_results: List[BehaviorResult] = []
        all_new_events: List[WorldEvent] = []
        pending = list(initial_events)
        cascade_round = 0
        total_events = len(initial_events)

        while pending and cascade_round < self.MAX_CASCADING_ROUNDS:
            if total_events >= self.MAX_EVENTS_PER_TICK:
                logger.info(
                    "[BehaviorEngine] MAX_EVENTS_PER_TICK (%d) 已达上限",
                    self.MAX_EVENTS_PER_TICK,
                )
                break
            cascade_round += 1
            next_pending: List[WorldEvent] = []

            for event in pending:
                if total_events >= self.MAX_EVENTS_PER_TICK:
                    break

                # 传播: 获取到达节点列表
                reached = self._propagator.propagate(event)

                # 评估到达节点的 ON_EVENT behaviors
                for node_id, weakened_event in reached:
                    node = self.wg.get_node(node_id)
                    if not node:
                        continue
                    for bh in node.get_active_behaviors(TriggerType.ON_EVENT):
                        if not self._matches_event_filter(bh, weakened_event):
                            continue
                        result = self._evaluate_and_execute(node, bh, ctx)
                        if result:
                            all_results.append(result)
                            # 新产生的事件加入下轮级联
                            for emitted in result.events_emitted:
                                next_pending.append(emitted)
                                all_new_events.append(emitted)
                                total_events += 1
                                if total_events >= self.MAX_EVENTS_PER_TICK:
                                    break
                        if total_events >= self.MAX_EVENTS_PER_TICK:
                            break
                    if total_events >= self.MAX_EVENTS_PER_TICK:
                        break

            pending = next_pending

        return all_results, all_new_events

    # -----------------------------------------------------------------
    # 内部方法 — ON_ENTER / ON_EXIT
    # -----------------------------------------------------------------

    def _handle_location_trigger(
        self,
        trigger: TriggerType,
        entity_id: str,
        location_id: str,
        ctx: TickContext,
    ) -> TickResult:
        """处理 ON_ENTER / ON_EXIT 触发。

        评估 location 节点及其作用域链上所有节点的对应 behaviors。
        """
        all_results: List[BehaviorResult] = []
        all_events: List[WorldEvent] = []
        all_hints: List[str] = []
        all_pending: List[Any] = []
        all_state_changes: Dict[str, Dict[str, Any]] = {}

        # 收集 location + 作用域链上的节点
        scope_nodes = [location_id] + self.wg.get_ancestors(location_id)
        # 也加上 location 的实体（NPC 等）
        scope_nodes.extend(self.wg.get_entities_at(location_id))

        for nid in scope_nodes:
            node = self.wg.get_node(nid)
            if not node:
                continue
            for bh in node.get_active_behaviors(trigger):
                result = self._evaluate_and_execute(node, bh, ctx)
                if result:
                    all_results.append(result)
                    all_hints.extend(result.narrative_hints)
                    all_pending.extend(result.pending_flash)
                    for nid2, changes in result.state_changes.items():
                        all_state_changes.setdefault(nid2, {}).update(changes)
                    all_events.extend(result.events_emitted)

        # 传播产生的事件
        if all_events:
            cascade_results, cascade_events = self._propagate_with_cascade(
                all_events, ctx
            )
            all_results.extend(cascade_results)
            all_events.extend(cascade_events)

            for r in cascade_results:
                all_hints.extend(r.narrative_hints)
                all_pending.extend(r.pending_flash)
                for nid, changes in r.state_changes.items():
                    all_state_changes.setdefault(nid, {}).update(changes)

        # 记录事件
        for event in all_events:
            self.wg.log_event(event)

        return TickResult(
            results=all_results,
            all_events=all_events,
            narrative_hints=all_hints,
            pending_flash=all_pending,
            state_changes=all_state_changes,
        )

    # -----------------------------------------------------------------
    # 内部方法 — 过滤器匹配
    # -----------------------------------------------------------------

    @staticmethod
    def _matches_event_filter(
        behavior: Behavior, event: WorldEvent
    ) -> bool:
        """检查 ON_EVENT behavior 的 event_filter 是否匹配事件类型。

        event_filter 支持通配符: "combat_*" 匹配 "combat_started" 等。
        event_filter 为 None 表示匹配所有事件。
        """
        if not behavior.event_filter:
            return True

        ef = behavior.event_filter
        if ef.endswith("*"):
            return event.event_type.startswith(ef[:-1])
        return event.event_type == ef

    @staticmethod
    def _matches_time_filter(
        behavior: Behavior, ctx: TickContext
    ) -> bool:
        """检查 ON_TIME behavior 的 time_condition 是否满足。

        time_condition 示例:
          {"hour_gte": 18}         — 18 点之后
          {"hour_gte": 18, "hour_lte": 6}  — 夜间（18~6 点）
          {"day_gte": 3}           — 第 3 天之后
        """
        tc = behavior.time_condition
        if not tc or not isinstance(tc, dict):
            return True

        # day 检查
        if "day_gte" in tc and ctx.game_day < tc["day_gte"]:
            return False
        if "day_lte" in tc and ctx.game_day > tc["day_lte"]:
            return False

        # hour 检查（支持跨午夜）
        hour_gte = tc.get("hour_gte")
        hour_lte = tc.get("hour_lte")

        if hour_gte is not None and hour_lte is not None:
            if hour_gte > hour_lte:
                # 跨午夜: 如 18~6 → hour >= 18 OR hour <= 6
                if not (ctx.game_hour >= hour_gte or ctx.game_hour <= hour_lte):
                    return False
            else:
                if not (hour_gte <= ctx.game_hour <= hour_lte):
                    return False
        elif hour_gte is not None:
            if ctx.game_hour < hour_gte:
                return False
        elif hour_lte is not None:
            if ctx.game_hour > hour_lte:
                return False

        return True

    @staticmethod
    def _matches_disposition_filter(
        behavior: Behavior, node: WorldNode
    ) -> bool:
        """检查 ON_DISPOSITION behavior 的 disposition_filter 是否满足。

        disposition_filter 示例:
          {"dimension": "trust", "gte": 50}
          {"dimension": "fear", "lte": -20}

        好感度数据存储在 node.state["dispositions"]["player"]。
        """
        df = behavior.disposition_filter
        if not df or not isinstance(df, dict):
            return True

        dimension = df.get("dimension", "")
        if not dimension:
            return True

        # 获取好感度值
        dispositions = node.state.get("dispositions", {})
        player_disp = dispositions.get("player", {})
        if not isinstance(player_disp, dict):
            return False

        value = player_disp.get(dimension, 0)

        # 检查阈值
        if "gte" in df and value < df["gte"]:
            return False
        if "lte" in df and value > df["lte"]:
            return False
        if "gt" in df and value <= df["gt"]:
            return False
        if "lt" in df and value >= df["lt"]:
            return False

        return True
