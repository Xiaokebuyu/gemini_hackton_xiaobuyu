"""
Tests for EventPropagator (C5).

测试事件沿图边传播的 BFS 算法:
  - 传播方向 (local/scope/global)
  - 衰减计算
  - 深度限制
  - visited 防重访
  - 边自定义衰减
"""
import pytest

from app.world.event_propagation import EventPropagator, DEFAULT_DECAY, MAX_DEPTH_SCOPE, MAX_DEPTH_GLOBAL
from app.world.models import (
    WorldEdgeType,
    WorldEvent,
    WorldNode,
    WorldNodeType,
)
from app.world.world_graph import WorldGraph


# =============================================================================
# Fixtures
# =============================================================================


def _make_linear_graph() -> WorldGraph:
    """创建线性图: world_root → region → area → location。"""
    wg = WorldGraph()
    wg.add_node(WorldNode(id="world_root", type=WorldNodeType.WORLD, name="World"))
    wg.add_node(WorldNode(id="region_a", type=WorldNodeType.REGION, name="Region A"))
    wg.add_node(WorldNode(id="area_1", type=WorldNodeType.AREA, name="Area 1"))
    wg.add_node(WorldNode(id="loc_1", type=WorldNodeType.LOCATION, name="Loc 1"))

    wg.add_edge("world_root", "region_a", WorldEdgeType.CONTAINS.value)
    wg.add_edge("region_a", "area_1", WorldEdgeType.CONTAINS.value)
    wg.add_edge("area_1", "loc_1", WorldEdgeType.CONTAINS.value)
    return wg


def _make_branching_graph() -> WorldGraph:
    """创建分支图:
    world_root → region → area_1 → loc_1
                        → area_2 → loc_2
    area_1 ←CONNECTS→ area_2
    """
    wg = WorldGraph()
    wg.add_node(WorldNode(id="world_root", type=WorldNodeType.WORLD, name="World"))
    wg.add_node(WorldNode(id="region_a", type=WorldNodeType.REGION, name="Region A"))
    wg.add_node(WorldNode(id="area_1", type=WorldNodeType.AREA, name="Area 1"))
    wg.add_node(WorldNode(id="area_2", type=WorldNodeType.AREA, name="Area 2"))
    wg.add_node(WorldNode(id="loc_1", type=WorldNodeType.LOCATION, name="Loc 1"))
    wg.add_node(WorldNode(id="loc_2", type=WorldNodeType.LOCATION, name="Loc 2"))

    wg.add_edge("world_root", "region_a", WorldEdgeType.CONTAINS.value)
    wg.add_edge("region_a", "area_1", WorldEdgeType.CONTAINS.value)
    wg.add_edge("region_a", "area_2", WorldEdgeType.CONTAINS.value)
    wg.add_edge("area_1", "loc_1", WorldEdgeType.CONTAINS.value)
    wg.add_edge("area_2", "loc_2", WorldEdgeType.CONTAINS.value)
    wg.add_edge("area_1", "area_2", WorldEdgeType.CONNECTS.value, key="conn_1_2")
    return wg


def _make_event(**kwargs) -> WorldEvent:
    return WorldEvent(
        event_type=kwargs.get("event_type", "test_event"),
        origin_node=kwargs.get("origin_node", "loc_1"),
        visibility=kwargs.get("visibility", "scope"),
        strength=kwargs.get("strength", 1.0),
        min_strength=kwargs.get("min_strength", 0.1),
    )


# =============================================================================
# Tests
# =============================================================================


class TestPropagateLocal:
    def test_local_returns_empty(self):
        wg = _make_linear_graph()
        prop = EventPropagator(wg)
        event = _make_event(visibility="local")
        reached = prop.propagate(event)
        assert reached == []


class TestPropagateScope:
    def test_scope_propagates_up(self):
        """从 loc_1 向上传播: area_1 → region_a → world_root。"""
        wg = _make_linear_graph()
        prop = EventPropagator(wg)
        event = _make_event(origin_node="loc_1", visibility="scope")
        reached = prop.propagate(event)

        reached_ids = [nid for nid, _ in reached]
        assert "area_1" in reached_ids
        assert "region_a" in reached_ids
        assert "world_root" in reached_ids

    def test_scope_propagates_down(self):
        """从 area_1 向下传播: loc_1。"""
        wg = _make_linear_graph()
        prop = EventPropagator(wg)
        event = _make_event(origin_node="area_1", visibility="scope")
        reached = prop.propagate(event)

        reached_ids = [nid for nid, _ in reached]
        assert "loc_1" in reached_ids

    def test_scope_no_connects_edge(self):
        """P4: scope 不走 CONNECTS 水平边，但可通过 CONTAINS 垂直路径到达同级节点。"""
        # 创建两个区域，仅通过 CONNECTS 连接（不共享父节点）
        wg = WorldGraph()
        wg.add_node(WorldNode(id="root", type=WorldNodeType.WORLD, name="Root"))
        wg.add_node(WorldNode(id="r1", type=WorldNodeType.REGION, name="R1"))
        wg.add_node(WorldNode(id="r2", type=WorldNodeType.REGION, name="R2"))
        wg.add_node(WorldNode(id="a1", type=WorldNodeType.AREA, name="A1"))
        wg.add_node(WorldNode(id="a2", type=WorldNodeType.AREA, name="A2"))
        wg.add_edge("root", "r1", WorldEdgeType.CONTAINS.value)
        wg.add_edge("root", "r2", WorldEdgeType.CONTAINS.value)
        wg.add_edge("r1", "a1", WorldEdgeType.CONTAINS.value)
        wg.add_edge("r2", "a2", WorldEdgeType.CONTAINS.value)
        wg.add_edge("a1", "a2", WorldEdgeType.CONNECTS.value, key="conn")

        prop = EventPropagator(wg)
        # scope 从 a1: 垂直上到 r1 → root（但 MAX_DEPTH_SCOPE=3，不够到 r2 → a2）
        event = _make_event(origin_node="a1", visibility="scope", strength=1.0, min_strength=0.001)
        reached = prop.propagate(event)
        reached_ids = [nid for nid, _ in reached]
        # scope 不走 CONNECTS → a2 不可达（垂直路径太深也到不了）
        assert "a2" not in reached_ids
        assert "r1" in reached_ids  # 垂直父节点可达

    def test_scope_reaches_sibling_via_parent(self):
        """scope 通过共同父节点可达同级节点（垂直路径）。"""
        wg = _make_branching_graph()
        prop = EventPropagator(wg)
        event = _make_event(origin_node="area_1", visibility="scope")
        reached = prop.propagate(event)
        reached_ids = [nid for nid, _ in reached]
        # area_2 通过 area_1→region_a→area_2 垂直路径可达
        assert "area_2" in reached_ids
        assert "region_a" in reached_ids


class TestPropagateGlobal:
    def test_global_includes_horizontal(self):
        """global 走 CONNECTS 边。"""
        wg = _make_branching_graph()
        prop = EventPropagator(wg)
        event = _make_event(origin_node="area_1", visibility="global")
        reached = prop.propagate(event)

        reached_ids = [nid for nid, _ in reached]
        assert "area_2" in reached_ids


class TestDecay:
    def test_up_decay(self):
        """向上传播衰减 0.8。"""
        wg = _make_linear_graph()
        prop = EventPropagator(wg)
        event = _make_event(origin_node="loc_1", visibility="scope", strength=1.0)
        reached = prop.propagate(event)

        for nid, weakened in reached:
            if nid == "area_1":
                assert abs(weakened.strength - 0.8) < 0.01

    def test_down_decay(self):
        """向下传播衰减 0.6。"""
        wg = _make_linear_graph()
        prop = EventPropagator(wg)
        event = _make_event(origin_node="area_1", visibility="scope", strength=1.0)
        reached = prop.propagate(event)

        for nid, weakened in reached:
            if nid == "loc_1":
                assert abs(weakened.strength - 0.6) < 0.01

    def test_cascading_decay(self):
        """多层传播衰减叠加: loc_1 → area_1 (0.8) → region_a (0.64)。"""
        wg = _make_linear_graph()
        prop = EventPropagator(wg)
        event = _make_event(origin_node="loc_1", visibility="scope", strength=1.0)
        reached = prop.propagate(event)

        for nid, weakened in reached:
            if nid == "region_a":
                # 0.8 * 0.8 = 0.64
                assert abs(weakened.strength - 0.64) < 0.01

    def test_horizontal_decay(self):
        """P4: 水平传播衰减 0.5（仅 global 走 CONNECTS）。"""
        wg = _make_branching_graph()
        prop = EventPropagator(wg)
        # 使用 global 才走 CONNECTS 水平边
        event = _make_event(origin_node="area_1", visibility="global", strength=1.0)
        reached = prop.propagate(event)

        for nid, weakened in reached:
            if nid == "area_2":
                # area_2 可能通过两条路径到达:
                # 1. CONNECTS 水平: 0.5 (depth 1)
                # 2. CONTAINS 垂直: up(0.8) * down(0.6) = 0.48 (depth 2)
                # BFS 先到的路径生效（depth 1 先入队）
                assert abs(weakened.strength - 0.5) < 0.01


class TestMinStrength:
    def test_stops_below_min_strength(self):
        """strength 低于 min_strength 时停止传播。"""
        wg = _make_linear_graph()
        prop = EventPropagator(wg)
        # 初始 0.5，min 0.45。向上第一层 0.5*0.8=0.4 < 0.45 → 第一跳就被截断
        event = _make_event(
            origin_node="loc_1", visibility="scope",
            strength=0.5, min_strength=0.45,
        )
        reached = prop.propagate(event)
        reached_ids = [nid for nid, _ in reached]
        assert "area_1" not in reached_ids


class TestDepthLimit:
    def test_max_depth_respected(self):
        """传播不超过 MAX_DEPTH 层。"""
        # 创建深度 6 的线性图
        wg = WorldGraph()
        nodes = [f"n{i}" for i in range(7)]
        for i, nid in enumerate(nodes):
            t = WorldNodeType.WORLD if i == 0 else WorldNodeType.AREA
            wg.add_node(WorldNode(id=nid, type=t, name=nid))
        for i in range(len(nodes) - 1):
            wg.add_edge(nodes[i], nodes[i + 1], WorldEdgeType.CONTAINS.value)

        prop = EventPropagator(wg)
        event = _make_event(
            origin_node=nodes[0], visibility="scope",
            strength=1.0, min_strength=0.001,
        )
        reached = prop.propagate(event)
        reached_ids = [nid for nid, _ in reached]

        # MAX_DEPTH_SCOPE=3，从 n0 开始: n1(depth=1), n2(2), n3(3)
        assert "n1" in reached_ids
        assert "n2" in reached_ids
        assert "n3" in reached_ids
        # n4 在 depth=4 → 超出限制
        assert "n4" not in reached_ids


class TestNoRevisit:
    def test_visited_prevents_cycles(self):
        """visited 集合防止重访（图中有环不会无限循环）。"""
        wg = _make_branching_graph()
        prop = EventPropagator(wg)
        event = _make_event(origin_node="area_1", visibility="global", strength=1.0)
        reached = prop.propagate(event)

        # 每个节点最多出现一次
        reached_ids = [nid for nid, _ in reached]
        assert len(reached_ids) == len(set(reached_ids))


class TestCustomEdgeDecay:
    def test_edge_custom_propagation(self):
        """边可以自定义衰减系数。"""
        wg = _make_linear_graph()
        # 给 area_1 → loc_1 边设置自定义衰减
        wg.update_edge(
            "area_1", "loc_1",
            WorldEdgeType.CONTAINS.value,
            {"propagation": {"down": 0.9}},
        )

        prop = EventPropagator(wg)
        event = _make_event(origin_node="area_1", visibility="scope", strength=1.0)
        reached = prop.propagate(event)

        for nid, weakened in reached:
            if nid == "loc_1":
                assert abs(weakened.strength - 0.9) < 0.01


class TestOriginNotInGraph:
    def test_missing_origin_returns_empty(self):
        wg = _make_linear_graph()
        prop = EventPropagator(wg)
        event = _make_event(origin_node="nonexistent", visibility="scope")
        reached = prop.propagate(event)
        assert reached == []


class TestOriginNotInResult:
    def test_origin_excluded(self):
        """origin_node 不在返回结果中。"""
        wg = _make_linear_graph()
        prop = EventPropagator(wg)
        event = _make_event(origin_node="area_1", visibility="scope")
        reached = prop.propagate(event)
        reached_ids = [nid for nid, _ in reached]
        assert "area_1" not in reached_ids
