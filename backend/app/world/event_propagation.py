"""
EventPropagator -- Step C5

事件沿图边传播 — 带衰减的 BFS。
只负责传播路径计算和强度衰减，不评估 behaviors（由 BehaviorEngine 负责）。

设计文档: 架构与设计/世界底层重构与战斗系统设计专项/世界活图详细设计.md §7

传播规则:
  - 从 event.origin_node 出发
  - visibility 控制传播范围: local / scope / global
  - 每经过一条边 strength *= decay_factor
  - strength < min_strength 时停止
  - 最多传播 MAX_DEPTH 层
  - visited 集合防止重访
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Dict, List, Tuple

from app.world.models import WorldEdgeType, WorldEvent

logger = logging.getLogger(__name__)

# 避免循环导入，运行时才引用
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.world.world_graph import WorldGraph


# 默认衰减系数
DEFAULT_DECAY: Dict[str, float] = {
    "up": 0.8,          # 子→父: 子地点的事，上级区域大概率知道
    "down": 0.6,        # 父→子: 区域的事，子地点不一定都知道
    "horizontal": 0.5,  # CONNECTS: 相邻区域弱传播
}

# 传播深度
MAX_DEPTH_SCOPE = 3     # scope: 沿 CONTAINS 垂直传播（不水平扩散）
MAX_DEPTH_GLOBAL = 8    # global: 覆盖完整路径 location→area→region→world→region2→area2→location2 (6跳+余量)


class EventPropagator:
    """事件沿图边传播 — 带衰减的 BFS。

    只负责传播路径计算和强度衰减。
    不评估 behaviors — 返回到达节点列表，由 BehaviorEngine 处理。

    Usage::

        propagator = EventPropagator(world_graph)
        reached = propagator.propagate(event)
        # reached: [(node_id, weakened_event), ...]
    """

    def __init__(self, wg: WorldGraph) -> None:
        self.wg = wg

    def propagate(self, event: WorldEvent) -> List[Tuple[str, WorldEvent]]:
        """BFS 传播事件。

        Args:
            event: 要传播的事件

        Returns:
            [(node_id, weakened_event), ...]
            weakened_event 是原事件的副本，strength 已衰减。
            不包含 origin_node 自身。
        """
        if event.visibility == "local":
            return []

        origin = event.origin_node
        if not self.wg.has_node(origin):
            logger.warning(
                "[EventPropagator] origin_node '%s' 不存在，跳过传播",
                origin,
            )
            return []

        result: List[Tuple[str, WorldEvent]] = []
        visited = {origin}
        # (node_id, current_strength, depth)
        queue: deque[Tuple[str, float, int]] = deque()

        # 种子: origin 的邻居
        for neighbor_id, direction in self._get_propagation_targets(
            origin, event.visibility
        ):
            if neighbor_id in visited:
                continue
            decay = self._get_decay(origin, neighbor_id, direction)
            new_strength = event.strength * decay
            if new_strength >= event.min_strength:
                queue.append((neighbor_id, new_strength, 1))

        while queue:
            node_id, strength, depth = queue.popleft()
            if node_id in visited:
                continue
            max_depth = MAX_DEPTH_GLOBAL if event.visibility == "global" else MAX_DEPTH_SCOPE
            if depth > max_depth:
                continue
            visited.add(node_id)

            # 创建衰减后的事件副本
            weakened = event.model_copy(update={"strength": strength})
            result.append((node_id, weakened))

            # 继续向邻居传播
            for next_id, direction in self._get_propagation_targets(
                node_id, event.visibility
            ):
                if next_id in visited:
                    continue
                decay = self._get_decay(node_id, next_id, direction)
                next_strength = strength * decay
                if next_strength >= event.min_strength:
                    queue.append((next_id, next_strength, depth + 1))

        return result

    def _get_propagation_targets(
        self, node_id: str, visibility: str
    ) -> List[Tuple[str, str]]:
        """获取传播目标和方向。

        Returns:
            [(neighbor_id, direction), ...]
            direction: "up" | "down" | "horizontal"
        """
        targets: List[Tuple[str, str]] = []

        # 向上: CONTAINS 父节点（所有 visibility 都可以）
        parent = self.wg.get_parent(node_id)
        if parent:
            targets.append((parent, "up"))
        else:
            # 无 CONTAINS 父节点的实体（event_def, npc, item 通过 HAS_EVENT/HOSTS/HAS_ITEM 挂载）
            # 查找宿主节点作为"向上"传播目标
            for host_id in self._find_host_nodes(node_id):
                targets.append((host_id, "up"))

        # 向下: CONTAINS 子节点
        for child in self.wg.get_children(node_id):
            targets.append((child, "down"))

        # 向下: 实体节点（通过 HAS_EVENT/HOSTS/HAS_ITEM 挂载的）
        for entity_id in self.wg.get_entities_at(node_id):
            targets.append((entity_id, "down"))

        # 水平: CONNECTS 邻居（仅 global 传播，scope 只走垂直 CONTAINS）
        if visibility == "global":
            for neighbor_id, _ in self.wg.get_neighbors(
                node_id, WorldEdgeType.CONNECTS.value
            ):
                targets.append((neighbor_id, "horizontal"))

        return targets

    def _find_host_nodes(self, entity_id: str) -> List[str]:
        """查找实体节点的宿主节点（通过 HOSTS/HAS_EVENT/HAS_ITEM 的反向查找）。"""
        hosts = []
        # 遍历所有入边，查找实体归属关系
        if entity_id not in self.wg.graph:
            return hosts
        for source, _, data in self.wg.graph.in_edges(entity_id, data=True):
            rel = data.get("relation", "")
            if rel in (
                WorldEdgeType.HOSTS.value,
                WorldEdgeType.HAS_EVENT.value,
                WorldEdgeType.HAS_ITEM.value,
            ):
                hosts.append(source)
        return hosts

    def _get_decay(
        self, source: str, target: str, direction: str
    ) -> float:
        """获取边的衰减系数。

        优先使用边上自定义的 propagation 属性，否则用默认值。

        边自定义示例:
          edge_data = {"propagation": {"up": 0.9, "down": 0.7}}
        """
        edge = self.wg.get_edge(source, target)
        if edge:
            propagation = edge.get("propagation", {})
            if isinstance(propagation, dict) and direction in propagation:
                return float(propagation[direction])
        return DEFAULT_DECAY.get(direction, 0.5)
