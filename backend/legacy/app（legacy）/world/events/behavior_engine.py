"""
BehaviorEngine -- Step C4

行为评估引擎：tick + 条件评估 + action 执行。
替代 AreaRuntime.check_events() 的硬编码事件状态机。

设计文档: 架构与设计/世界底层重构与战斗系统设计专项/世界活图详细设计.md §6
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from app.world.events.condition_evaluator import ConditionEvaluator  # noqa: F401
from app.world.events.action_executor import ActionExecutor, _ActionResult  # noqa: F401
from app.world.events.propagation import EventPropagator
from app.world.graph.models import (
    Behavior,
    BehaviorResult,
    EventStatus,
    TickContext,
    TickResult,
    TriggerType,
    WorldEvent,
    WorldNode,
    WorldNodeType,
)

# 避免循环导入
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.world.graph.world_graph import WorldGraph

logger = logging.getLogger(__name__)


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
        self._fired_behaviors: Set[Tuple[str, str]] = set()  # 2.4: 回合内去重

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
            if self.wg.has_node(nid):
                self.wg.tick_cooldowns(nid)

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
        # 2.4: 回合内去重 — 同一 (node_id, behavior_id) 在同一轮只执行一次
        dedup_key = (node.id, behavior.id)
        if dedup_key in self._fired_behaviors:
            return None

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

        # 标记 once / cooldown（通过 WorldGraph 包装方法，自动标脏）
        if behavior.once:
            self.wg.mark_behavior_fired(node.id, behavior.id)
        if behavior.cooldown_ticks > 0:
            # +1 补偿: 下次 tick 开始时 tick_cooldowns() 会立即减 1，
            # 所以设置 N+1 确保实际阻塞 N 个 tick
            self.wg.set_behavior_cooldown(node.id, behavior.id, behavior.cooldown_ticks + 1)

        # 2.4: 记录已执行（回合内去重）
        self._fired_behaviors.add((node.id, behavior.id))

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
