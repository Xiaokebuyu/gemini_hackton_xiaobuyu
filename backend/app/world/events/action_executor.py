"""action_executor.py — Action 执行器，将 Action 应用到 WorldGraph。

从 behavior_engine.py 拆出。6 种 ActionType 的执行逻辑。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.world.graph.models import (
    Action,
    ActionType,
    TickContext,
    WorldEdgeType,
    WorldEvent,
    WorldNode,
    WorldNodeType,
)

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.world.graph.world_graph import WorldGraph

logger = logging.getLogger(__name__)


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
