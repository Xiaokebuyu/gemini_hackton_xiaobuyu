"""
WorldGraph Container -- Step C2

世界活图容器：全量内存加载的三维世界图。
提供节点/边 CRUD、自建索引查询、状态修改与脏标记、事件日志、基础快照。

设计文档: 架构与设计/世界底层重构与战斗系统设计专项/WorldGraph容器设计.md

=== 与 MemoryGraph 的区别 ===

  MemoryGraph (记忆图谱)          WorldGraph (世界图)
  ──────────────────────────────────────────────────
  主观记忆网络                    客观世界结构
  nx.MultiDiGraph                 nx.MultiDiGraph
  model_dump() 打散存储            WorldNode 对象引用
  9 个索引                        4 个索引
  混合查询 (索引 + NX 原生)        纯自建索引查询
  按需加载/卸载                    全量内存加载 (会话级)

⚠️ 重要: 禁止在本类之外直接操作 self.graph。
所有图操作必须通过 WorldGraph 的公开方法，以保证索引一致性。
如需 NetworkX 原生查询能力，使用 query_raw() 方法。

⚠️ 注意: get_node() 和 get_node_state() 返回的是内部对象的直接引用。
通过返回值直接修改 state 不会自动标记 dirty。
如需 dirty tracking，请使用 set_state() / merge_state()。
"""
from __future__ import annotations

import copy
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import networkx as nx

from app.world.models import (
    ChapterStatus,
    WorldEdgeType,
    WorldEvent,
    WorldNode,
    WorldNodeType,
)

logger = logging.getLogger(__name__)

# 层级边类型 — 驱动 _children / _parents 索引
_HIERARCHY_RELATION = WorldEdgeType.CONTAINS.value

# 实体归属边类型 — 驱动 _entities_at 索引
_ENTITY_RELATIONS = frozenset({
    WorldEdgeType.HOSTS.value,
    WorldEdgeType.HAS_EVENT.value,
    WorldEdgeType.HAS_ITEM.value,
})


@dataclass
class EdgeChange:
    """运行时边变更记录（内部用，序列化由 snapshot.py 负责）。"""
    operation: str      # "add" | "update" | "remove"
    source: str = ""
    target: str = ""
    key: str = ""
    relation: str = ""
    attrs: Dict[str, Any] = field(default_factory=dict)


class WorldGraph:
    """三维世界图容器。

    职责:
      - 全量内存加载的世界图结构
      - 节点/边 CRUD + 自动索引维护
      - Z 轴层级查询（祖先链、后代、作用域链）
      - X/Y 平面查询（邻居、连接区域、地点实体）
      - 状态修改 + 脏标记 (dirty tracking)
      - 事件日志 + 基础快照

    不负责:
      - 构建 (C3 GraphBuilder)
      - 行为评估 (C4 BehaviorEngine)
      - 事件传播 (C5 EventPropagator)
      - 完整快照持久化 (C6 Snapshot)
    """

    def __init__(self) -> None:
        self.graph: nx.MultiDiGraph = nx.MultiDiGraph()

        # ===== 索引 =====
        self._type_index: Dict[str, Set[str]] = defaultdict(set)
        self._children: Dict[str, Set[str]] = defaultdict(set)
        self._parents: Dict[str, Set[str]] = defaultdict(set)
        self._entities_at: Dict[str, Set[str]] = defaultdict(set)

        # ===== 脏标记 =====
        self._dirty_nodes: Set[str] = set()
        self._dirty_edges: Set[Tuple[str, str]] = set()

        # ===== 事件日志 =====
        self._event_log: List[WorldEvent] = []

        # ===== C6: 运行时变更追踪 =====
        self._sealed: bool = False
        self._spawned_nodes: Dict[str, WorldNode] = {}   # {node_id: 深拷贝}
        self._removed_node_ids: Set[str] = set()
        self._edge_changes: List[EdgeChange] = []        # 有序变更日志

    # =========================================================================
    # Seal — 构建期 → 运行期切换
    # =========================================================================

    def seal(self) -> None:
        """标记构建期完成，开始运行时追踪。"""
        self._sealed = True
        self.clear_dirty()

    # =========================================================================
    # 节点 CRUD
    # =========================================================================

    def add_node(self, node: WorldNode) -> None:
        """添加节点到图中，更新类型索引。

        如果节点已存在，替换节点数据但保留现有边和边驱动的索引。
        不处理边关系 — 边需要单独通过 add_edge() 添加。
        """
        existed_before = node.id in self.graph
        if existed_before:
            # 只移除类型索引（边驱动的索引由边本身维护，不受节点替换影响）
            old_node = self.get_node(node.id)
            if old_node:
                self._type_index.get(old_node.type, set()).discard(node.id)
        self.graph.add_node(node.id, _node=node)
        self._type_index[node.type].add(node.id)

        # C6: 追踪运行期新增
        if self._sealed and not existed_before:
            self._spawned_nodes[node.id] = node.model_copy(deep=True)
            # remove → spawn 同 ID：从 removed 中移除，spawn 会替代原节点
            self._removed_node_ids.discard(node.id)

    def remove_node(self, node_id: str) -> None:
        """移除节点及其所有 CONTAINS 后代和关联边。

        级联删除: 通过 _children 索引递归收集后代，从叶子到根逐一删除。

        Raises:
            KeyError: node_id 不存在。
        """
        if node_id not in self.graph:
            raise KeyError(f"Node not found: {node_id}")

        # BFS 收集所有后代 (含自身)
        to_remove = []
        queue = deque([node_id])
        visited = {node_id}
        while queue:
            nid = queue.popleft()
            to_remove.append(nid)
            for child in self._children.get(nid, set()).copy():
                if child not in visited:
                    visited.add(child)
                    queue.append(child)

        # C6: 追踪运行期删除
        if self._sealed:
            for nid in to_remove:
                self._removed_node_ids.add(nid)

        # 反序删除 (先叶子后父节点)
        for nid in reversed(to_remove):
            self._deindex_node(nid)
            if nid in self.graph:
                self.graph.remove_node(nid)

    def get_node(self, node_id: str) -> Optional[WorldNode]:
        """获取节点对象。不存在返回 None。

        ⚠️ 返回内部对象引用。直接修改 state 不会自动标记 dirty。
        """
        if node_id not in self.graph:
            return None
        return self.graph.nodes[node_id].get("_node")

    def has_node(self, node_id: str) -> bool:
        """检查节点是否存在。"""
        return node_id in self.graph

    def get_node_state(self, node_id: str) -> Dict[str, Any]:
        """只获取节点的可变 state dict。

        ⚠️ 返回内部 dict 引用。直接修改不会自动标记 dirty。

        Raises:
            KeyError: node_id 不存在。
        """
        node = self.get_node(node_id)
        if node is None:
            raise KeyError(f"Node not found: {node_id}")
        return node.state

    def node_count(self) -> int:
        """返回图中节点总数。"""
        return self.graph.number_of_nodes()

    # =========================================================================
    # 状态修改（自动标记 dirty）
    # =========================================================================

    def set_state(self, node_id: str, key: str, value: Any) -> None:
        """修改节点的单个 state 字段。

        委托给 WorldNode.set_state()（自动更新 updated_at）。

        Raises:
            KeyError: node_id 不存在。
        """
        node = self.get_node(node_id)
        if node is None:
            raise KeyError(f"Node not found: {node_id}")
        node.set_state(key, value)
        self._dirty_nodes.add(node_id)

    def merge_state(self, node_id: str, updates: Dict[str, Any]) -> None:
        """批量合并 state 字段。

        委托给 WorldNode.merge_state()。

        Raises:
            KeyError: node_id 不存在。
        """
        node = self.get_node(node_id)
        if node is None:
            raise KeyError(f"Node not found: {node_id}")
        node.merge_state(updates)
        self._dirty_nodes.add(node_id)

    # =========================================================================
    # 边操作
    # =========================================================================

    def add_edge(
        self,
        source: str,
        target: str,
        relation: str,
        key: str = None,
        **attrs,
    ) -> None:
        """添加边。

        - CONTAINS: 自动更新 _children + _parents 索引
        - HOSTS / HAS_EVENT / HAS_ITEM: 自动更新 _entities_at 索引
        - CONNECTS: 默认自动加反向边（除非 one_way=True）
        - key: MultiDiGraph 边标识。不指定则用 relation 值。
          RELATES_TO 多边场景应显式提供 key（如 "mentor", "rival"）。

        Raises:
            KeyError: source 或 target 节点不存在。
        """
        if source not in self.graph:
            raise KeyError(f"Edge source not found: {source}")
        if target not in self.graph:
            raise KeyError(f"Edge target not found: {target}")

        edge_key = key or relation
        self.graph.add_edge(source, target, key=edge_key,
                            relation=relation, **attrs)
        self._dirty_edges.add((source, target))

        # 索引驱动
        if relation == _HIERARCHY_RELATION:
            self._children[source].add(target)
            self._parents[target].add(source)
        elif relation in _ENTITY_RELATIONS:
            self._entities_at[source].add(target)

        # C6: 追踪运行期边添加
        if self._sealed:
            self._edge_changes.append(EdgeChange(
                operation="add", source=source, target=target,
                key=edge_key, relation=relation, attrs=dict(attrs),
            ))

        # CONNECTS 自动反向边
        if relation == WorldEdgeType.CONNECTS.value and not attrs.get("one_way"):
            reverse_key = f"{edge_key}_rev" if key else f"{relation}_rev"
            if not self.graph.has_edge(target, source, key=reverse_key):
                self.graph.add_edge(target, source, key=reverse_key,
                                    relation=relation, **attrs)
                self._dirty_edges.add((target, source))
                # C6: 追踪自动反向边
                if self._sealed:
                    self._edge_changes.append(EdgeChange(
                        operation="add", source=target, target=source,
                        key=reverse_key, relation=relation, attrs=dict(attrs),
                    ))

    def get_edge(
        self, source: str, target: str, key: str = None
    ) -> Optional[Dict[str, Any]]:
        """获取边数据。

        如不指定 key，返回两节点间第一条边。
        """
        if not self.graph.has_node(source) or not self.graph.has_node(target):
            return None
        if key:
            return self.graph.get_edge_data(source, target, key)
        # 返回第一条边
        edges = self.graph.get_edge_data(source, target)
        if not edges:
            return None
        first_key = next(iter(edges))
        return edges[first_key]

    def get_edges_between(
        self, source: str, target: str
    ) -> List[Tuple[str, Dict[str, Any]]]:
        """获取两节点间所有边。

        返回: [(key, edge_data), ...]
        """
        if not self.graph.has_node(source) or not self.graph.has_node(target):
            return []
        edges = self.graph.get_edge_data(source, target)
        if not edges:
            return []
        return [(k, dict(v)) for k, v in edges.items()]

    def update_edge(
        self, source: str, target: str, key: str, updates: Dict[str, Any]
    ) -> None:
        """更新边属性。

        Raises:
            KeyError: 边不存在。
        """
        data = self.graph.get_edge_data(source, target, key)
        if data is None:
            raise KeyError(f"Edge not found: ({source}, {target}, {key})")
        data.update(updates)
        self._dirty_edges.add((source, target))

        # C6: 追踪运行期边更新
        if self._sealed:
            self._edge_changes.append(EdgeChange(
                operation="update", source=source, target=target,
                key=key, relation=data.get("relation", ""),
                attrs=dict(updates),
            ))

    def remove_edge(
        self, source: str, target: str, key: str = None
    ) -> None:
        """移除边。同步清理相关索引和自动反向边。

        如不指定 key，移除两节点间所有边。
        CONNECTS 边会自动移除对应的反向边。
        """
        if not self.graph.has_node(source) or not self.graph.has_node(target):
            return

        if key:
            edge_data = self.graph.get_edge_data(source, target, key)
            if edge_data is None:
                return
            relation = edge_data.get("relation", "")
            # C6: 追踪运行期边删除（在实际删除之前记录）
            if self._sealed:
                self._edge_changes.append(EdgeChange(
                    operation="remove", source=source, target=target,
                    key=key, relation=relation, attrs={},
                ))
            self.graph.remove_edge(source, target, key)
            self._dirty_edges.add((source, target))
            # 安全地更新索引：检查是否还有同类型的剩余边
            self._safe_deindex_edge(source, target, relation)
            # CONNECTS: 同步删除反向边
            if relation == WorldEdgeType.CONNECTS.value:
                self._remove_reverse_connects(target, source, key)
        else:
            # 收集所有边信息后再删除
            all_edges = self.graph.get_edge_data(source, target)
            if not all_edges:
                return
            edge_list = [(k, dict(v)) for k, v in all_edges.items()]
            # C6: 追踪运行期边删除（在实际删除之前记录）
            if self._sealed:
                for k, edata in edge_list:
                    self._edge_changes.append(EdgeChange(
                        operation="remove", source=source, target=target,
                        key=k, relation=edata.get("relation", ""), attrs={},
                    ))
            # 删除所有边
            while self.graph.has_edge(source, target):
                keys = list(self.graph[source][target].keys())
                if not keys:
                    break
                self.graph.remove_edge(source, target, keys[0])
            self._dirty_edges.add((source, target))
            # 对每条已删除的边更新索引
            for k, edata in edge_list:
                relation = edata.get("relation", "")
                self._safe_deindex_edge(source, target, relation)
                if relation == WorldEdgeType.CONNECTS.value:
                    self._remove_reverse_connects(target, source, k)

    # =========================================================================
    # 查询 — Z 轴（层级）
    # =========================================================================

    def get_children(
        self, node_id: str, type_filter: str = None
    ) -> List[str]:
        """获取 CONTAINS 子节点 ID 列表，可按 type 过滤。"""
        children = self._children.get(node_id, set())
        if not type_filter:
            return list(children)
        return [
            cid for cid in children
            if (n := self.get_node(cid)) and n.type == type_filter
        ]

    def get_parent(self, node_id: str) -> Optional[str]:
        """获取 CONTAINS 父节点 ID。

        大多数节点只有一个父，直接返回。
        多父时（region 被多 chapter 共享），选 active chapter 路径。
        """
        parents = self._parents.get(node_id)
        if not parents:
            return None
        if len(parents) == 1:
            return next(iter(parents))
        return self._resolve_active_parent(node_id, parents)

    def get_all_parents(self, node_id: str) -> Set[str]:
        """获取所有 CONTAINS 父节点 ID（多父场景）。"""
        return set(self._parents.get(node_id, set()))

    def get_ancestors(self, node_id: str) -> List[str]:
        """获取从当前节点到 world_root 的完整祖先链。

        多父分支时选 active chapter 路径（设计决策 A6）。

        返回: [parent, grandparent, ..., world_root]
        """
        result = []
        current = node_id
        visited = {node_id}
        while True:
            parent = self.get_parent(current)
            if parent is None or parent in visited:
                break
            result.append(parent)
            visited.add(parent)
            current = parent
        return result

    def get_descendants(
        self, node_id: str, type_filter: str = None
    ) -> List[str]:
        """获取所有 CONTAINS 后代节点（递归 BFS）。

        可按 type 过滤结果。
        用途: BehaviorEngine.tick() 确定活跃范围。
        """
        result = []
        queue = deque()
        # 种子: 直接子节点
        for child in self._children.get(node_id, set()):
            queue.append(child)
        visited = {node_id}
        while queue:
            nid = queue.popleft()
            if nid in visited:
                continue
            visited.add(nid)
            if type_filter:
                node = self.get_node(nid)
                if node and node.type == type_filter:
                    result.append(nid)
            else:
                result.append(nid)
            for child in self._children.get(nid, set()):
                if child not in visited:
                    queue.append(child)
        return result

    def get_scope_chain(self, location_id: str) -> List[str]:
        """获取玩家当前位置的完整作用域链。

        返回: [location_id, area_id, region_id, chapter_id, world_root]
        """
        return [location_id] + self.get_ancestors(location_id)

    # =========================================================================
    # 查询 — X/Y 平面（水平关系）
    # =========================================================================

    def get_neighbors(
        self, node_id: str, relation: str = None
    ) -> List[Tuple[str, Dict[str, Any]]]:
        """获取邻居节点 ID + 边数据。

        可按 relation 过滤。
        由于 CONNECTS 已自动加反向边，只查 successors 即可覆盖双向。

        返回: [(neighbor_id, edge_data), ...]
        """
        if node_id not in self.graph:
            return []
        result = []
        for _, target, data in self.graph.out_edges(node_id, data=True):
            if relation is None or data.get("relation") == relation:
                result.append((target, dict(data)))
        return result

    def get_connected_areas(self, area_id: str) -> List[str]:
        """获取与指定区域相连的所有区域 ID。

        便捷方法 = get_neighbors(area_id, "connects") 的去重 ID 列表。
        """
        return list({
            nid for nid, _ in self.get_neighbors(
                area_id, WorldEdgeType.CONNECTS.value
            )
        })

    def get_entities_at(self, location_id: str) -> List[str]:
        """获取某地点/区域的所有实体 ID（NPC、物品、事件）。

        使用 _entities_at 索引，O(1)。
        """
        return list(self._entities_at.get(location_id, set()))

    # =========================================================================
    # 查询 — 按类型
    # =========================================================================

    def get_by_type(self, node_type: str) -> List[str]:
        """按类型获取所有节点 ID。使用 _type_index 索引。"""
        return list(self._type_index.get(node_type, set()))

    def find_events_in_scope(self, scope_node_id: str) -> List[str]:
        """获取指定节点及其后代中的所有 event_def 节点。

        event_def 通过 HAS_EVENT 边挂在 area/location 上（不是 CONTAINS），
        因此需要同时检查 _entities_at 索引中的 event_def。

        用途: BehaviorEngine 确定当前作用域下的活跃事件。
        """
        result_set: Set[str] = set()

        # 收集自身 + 所有 CONTAINS 后代
        scope_nodes = [scope_node_id] + self.get_descendants(scope_node_id)

        for nid in scope_nodes:
            node = self.get_node(nid)
            if not node:
                continue
            # 节点自身是 event_def
            if node.type == WorldNodeType.EVENT_DEF:
                result_set.add(nid)
            # 检查通过 HAS_EVENT 挂载的实体
            for entity_id in self._entities_at.get(nid, set()):
                entity = self.get_node(entity_id)
                if entity and entity.type == WorldNodeType.EVENT_DEF:
                    result_set.add(entity_id)

        return list(result_set)

    # =========================================================================
    # 查询 — 原生逃生通道
    # =========================================================================

    def query_raw(
        self, predicate: Callable[[WorldNode], bool]
    ) -> List[WorldNode]:
        """原生全扫描查询 — 当自建索引无法满足时使用。

        ⚠️ O(n) 操作，n 为图中节点总数。慎用。
        ⚠️ 返回内部对象引用。直接修改不会自动标记 dirty。
        """
        return [
            data["_node"]
            for _, data in self.graph.nodes(data=True)
            if "_node" in data and predicate(data["_node"])
        ]

    # =========================================================================
    # 事件日志
    # =========================================================================

    def log_event(self, event: WorldEvent) -> None:
        """记录事件到本回合日志。"""
        self._event_log.append(event)

    def flush_event_log(self) -> List[WorldEvent]:
        """取出并清空本回合事件日志。"""
        events = list(self._event_log)
        self._event_log.clear()
        return events

    # =========================================================================
    # 快照（C2 基础版 — 仅 state）
    # =========================================================================

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        """生成增量快照 — 仅 dirty 节点的 state（深拷贝）。

        返回: {node_id: state_dict}

        C2 基础版。后续 C4/C6 扩展:
        spawned_nodes, removed_node_ids, modified_edges, behavior_states。
        """
        result = {}
        for nid in self._dirty_nodes:
            node = self.get_node(nid)
            if node:
                result[nid] = copy.deepcopy(node.state)
        return result

    def snapshot_full(self) -> Dict[str, Dict[str, Any]]:
        """生成完整快照 — 所有节点的 state（深拷贝）。

        用途: 首次保存、调试。
        """
        result = {}
        for nid, data in self.graph.nodes(data=True):
            node = data.get("_node")
            if node:
                result[nid] = copy.deepcopy(node.state)
        return result

    def restore_snapshot(
        self, node_states: Dict[str, Dict[str, Any]]
    ) -> None:
        """从快照恢复节点 state（深拷贝输入，防止外部修改污染）。

        不存在的 node_id 静默跳过（可能是被删除的节点）。
        """
        for nid, state in node_states.items():
            node = self.get_node(nid)
            if node:
                node.state = copy.deepcopy(state)

    def clear_dirty(self) -> None:
        """清除脏标记和追踪集合（快照保存后调用）。"""
        self._dirty_nodes.clear()
        self._dirty_edges.clear()
        self._spawned_nodes.clear()
        self._removed_node_ids.clear()
        self._edge_changes.clear()

    # =========================================================================
    # 统计与调试
    # =========================================================================

    def stats(self) -> Dict[str, Any]:
        """返回图的统计信息。"""
        type_dist = {
            t: len(ids) for t, ids in self._type_index.items() if ids
        }
        return {
            "node_count": self.graph.number_of_nodes(),
            "edge_count": self.graph.number_of_edges(),
            "dirty_node_count": len(self._dirty_nodes),
            "dirty_edge_count": len(self._dirty_edges),
            "type_distribution": type_dist,
            "event_log_size": len(self._event_log),
            "sealed": self._sealed,
            "spawned_count": len(self._spawned_nodes),
            "removed_count": len(self._removed_node_ids),
            "edge_change_count": len(self._edge_changes),
        }

    # =========================================================================
    # 内部方法 — 索引维护
    # =========================================================================

    def _deindex_node(self, node_id: str) -> None:
        """从所有索引中移除节点。"""
        node = self.get_node(node_id)
        if node:
            self._type_index.get(node.type, set()).discard(node_id)

        # 从父节点的 _children 中移除自己
        for parent in self._parents.get(node_id, set()).copy():
            self._children.get(parent, set()).discard(node_id)
        self._parents.pop(node_id, None)

        # 从 _children 中移除自己的条目
        self._children.pop(node_id, None)

        # 从 _entities_at 中移除
        for loc, entities in list(self._entities_at.items()):
            entities.discard(node_id)
        # 如果自己是 location/area，移除整个条目
        self._entities_at.pop(node_id, None)

        # 清除脏标记
        self._dirty_nodes.discard(node_id)

    def _safe_deindex_edge(
        self, source: str, target: str, relation: str
    ) -> None:
        """安全地从索引中移除边的影响。

        检查是否还有同类型的剩余边，只在没有剩余时才从索引中移除。
        解决 MultiDiGraph 多边场景下的索引误删问题。
        """
        if relation == _HIERARCHY_RELATION:
            # 检查是否还有其他 CONTAINS 边
            if not self._has_remaining_edge(source, target, relation):
                self._children.get(source, set()).discard(target)
                self._parents.get(target, set()).discard(source)
        elif relation in _ENTITY_RELATIONS:
            # 检查是否还有其他实体归属边
            if not self._has_remaining_entity_edge(source, target):
                self._entities_at.get(source, set()).discard(target)

    def _has_remaining_edge(
        self, source: str, target: str, relation: str
    ) -> bool:
        """检查两节点间是否还有指定 relation 的边。"""
        if not self.graph.has_node(source) or not self.graph.has_node(target):
            return False
        edges = self.graph.get_edge_data(source, target)
        if not edges:
            return False
        return any(
            edata.get("relation") == relation for edata in edges.values()
        )

    def _has_remaining_entity_edge(
        self, source: str, target: str
    ) -> bool:
        """检查两节点间是否还有任何实体归属类型的边。"""
        if not self.graph.has_node(source) or not self.graph.has_node(target):
            return False
        edges = self.graph.get_edge_data(source, target)
        if not edges:
            return False
        return any(
            edata.get("relation") in _ENTITY_RELATIONS
            for edata in edges.values()
        )

    def _remove_reverse_connects(
        self, source: str, target: str, original_key: str
    ) -> None:
        """删除 CONNECTS 的自动反向边。"""
        if not self.graph.has_node(source) or not self.graph.has_node(target):
            return
        # 尝试两种可能的反向 key
        for rev_key in [f"{original_key}_rev", f"{WorldEdgeType.CONNECTS.value}_rev"]:
            if self.graph.has_edge(source, target, key=rev_key):
                # C6: 追踪反向边删除
                if self._sealed:
                    self._edge_changes.append(EdgeChange(
                        operation="remove", source=source, target=target,
                        key=rev_key, relation=WorldEdgeType.CONNECTS.value,
                        attrs={},
                    ))
                self.graph.remove_edge(source, target, rev_key)
                self._dirty_edges.add((source, target))
                return
        # Fallback: 扫描所有边找 CONNECTS 类型的反向边
        edges = self.graph.get_edge_data(source, target)
        if edges:
            for k, edata in list(edges.items()):
                if edata.get("relation") == WorldEdgeType.CONNECTS.value:
                    # C6: 追踪反向边删除
                    if self._sealed:
                        self._edge_changes.append(EdgeChange(
                            operation="remove", source=source, target=target,
                            key=k, relation=WorldEdgeType.CONNECTS.value,
                            attrs={},
                        ))
                    self.graph.remove_edge(source, target, k)
                    self._dirty_edges.add((source, target))
                    return

    def _resolve_active_parent(
        self, node_id: str, parents: Set[str]
    ) -> str:
        """多父时选择 active chapter 路径上的父节点。

        策略:
        1. 如果某个 parent 就是 active chapter → 直接返回
        2. 否则沿每个 parent 向上追溯（BFS 穷举），找到通往 active chapter 的分支
        3. Fallback: 返回排序后第一个 parent（确定性）
        """
        # 排序保证确定性
        sorted_parents = sorted(parents)

        # 直接检查: 父节点是否为 active chapter
        for pid in sorted_parents:
            p_node = self.get_node(pid)
            if (p_node
                    and p_node.type == WorldNodeType.CHAPTER
                    and p_node.state.get("status") == ChapterStatus.ACTIVE):
                return pid

        # 间接追溯 (BFS 穷举): 从每个 parent 向上走，看是否到达 active chapter
        for pid in sorted_parents:
            if self._traces_to_active_chapter(pid):
                return pid

        # Fallback: 排序后第一个
        return sorted_parents[0]

    def _traces_to_active_chapter(self, node_id: str) -> bool:
        """BFS 穷举检查从 node_id 向上是否能到达 active chapter。"""
        queue = deque([node_id])
        visited = set()
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            node = self.get_node(current)
            if (node
                    and node.type == WorldNodeType.CHAPTER
                    and node.state.get("status") == ChapterStatus.ACTIVE):
                return True
            # 所有父节点都入队（穷举，不是只走一条路径）
            for parent in self._parents.get(current, set()):
                if parent not in visited:
                    queue.append(parent)
        return False
