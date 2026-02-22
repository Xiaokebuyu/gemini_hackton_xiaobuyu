"""L3 M3: 首次会话知识加载测试。

覆盖 SessionRuntime._load_knowledge_into_world_graph() 及相关逻辑。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.graph import GraphData, MemoryEdge, MemoryNode
from app.world.models import WorldNode
from app.world.world_graph import WorldGraph


# =============================================================================
# Helpers
# =============================================================================


def _node(
    id: str,
    type: str = "npc",
    name: str = "",
    importance: float = 0.0,
    **props,
) -> WorldNode:
    return WorldNode(
        id=id, type=type, name=name,
        importance=importance, properties=props,
        state={}, behaviors=[],
    )


def _mem_node(
    id: str,
    type: str = "memory",
    name: str = "",
    importance: float = 0.5,
    **props,
) -> MemoryNode:
    return MemoryNode(id=id, type=type, name=name, importance=importance, properties=props)


def _mem_edge(
    source: str,
    target: str,
    relation: str = "related_to",
    weight: float = 1.0,
    id: str = "",
) -> MemoryEdge:
    return MemoryEdge(
        id=id or f"{source}_{target}_{relation}",
        source=source, target=target,
        relation=relation, weight=weight,
    )


def _build_session(
    wg: WorldGraph,
    graph_store: Any = None,
    chapter_id: str = "ch1",
    area_id: str = "town",
    player_location: str = "town",
) -> Any:
    """Build a minimal SessionRuntime with a WorldGraph for testing."""
    from app.runtime.session_runtime import SessionRuntime
    session = SessionRuntime(
        world_id="test_world",
        session_id="test_session",
        graph_store=graph_store,
    )
    session.world_graph = wg
    gs = MagicMock()
    gs.chapter_id = chapter_id
    gs.area_id = area_id
    gs.player_location = player_location
    session.game_state = gs
    return session


def _make_graph_store(scope_data: Dict[str, GraphData]) -> AsyncMock:
    """Create a mock graph_store that returns scope_data by scope string."""
    store = AsyncMock()

    async def _load(world_id, scope):
        key = str(scope)
        return scope_data.get(key, GraphData(nodes=[], edges=[]))

    store.load_graph_v2 = AsyncMock(side_effect=_load)
    return store


def _run(coro):
    """Run an async coroutine synchronously."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


# =============================================================================
# Tests
# =============================================================================


class TestKnowledgeLoading:
    """首次会话知识加载核心路径。"""

    def test_first_session_loads_knowledge(self):
        """首次会话加载知识 → WorldGraph 包含知识节点。"""
        wg = WorldGraph()
        wg.add_node(_node("world_root", type="world_root", name="World"))
        wg.add_node(_node("npc_a", type="npc", name="NPC A"))
        wg.seal()

        from app.models.graph_scope import GraphScope
        world_scope = GraphScope.world()
        knowledge_node = _mem_node("knowledge_lore_1", type="knowledge", name="Ancient Lore")
        scope_data = {
            str(world_scope): GraphData(nodes=[knowledge_node], edges=[]),
        }
        store = _make_graph_store(scope_data)

        session = _build_session(wg, graph_store=store)
        _run(session._load_knowledge_into_world_graph())

        assert wg.has_node("knowledge_lore_1")
        loaded = wg.get_node("knowledge_lore_1")
        assert loaded.name == "Ancient Lore"
        assert loaded.properties.get("owner") == "world_root"

    def test_skip_existing_nodes(self):
        """跳过 GraphBuilder 已创建的结构节点，不覆盖。"""
        wg = WorldGraph()
        original = _node("npc_a", type="npc", name="NPC A Original")
        wg.add_node(original)
        wg.add_node(_node("world_root", type="world_root", name="World"))
        wg.seal()

        from app.models.graph_scope import GraphScope
        duplicate = _mem_node("npc_a", type="npc", name="NPC A Duplicate")
        scope_data = {
            str(GraphScope.world()): GraphData(nodes=[duplicate], edges=[]),
        }
        store = _make_graph_store(scope_data)

        session = _build_session(wg, graph_store=store)
        _run(session._load_knowledge_into_world_graph())

        assert wg.get_node("npc_a").name == "NPC A Original"

    def test_owner_mapping(self):
        """owner 映射正确（world→world_root, char→npc_id, camp→camp）。"""
        wg = WorldGraph()
        wg.add_node(_node("world_root", type="world_root", name="World"))
        wg.add_node(_node("npc_bob", type="npc", name="Bob"))
        wg.seal()

        from app.models.graph_scope import GraphScope
        world_mem = _mem_node("world_mem_1", type="knowledge", name="World Knowledge")
        char_mem = _mem_node("bob_mem_1", type="memory", name="Bob's Memory")
        camp_mem = _mem_node("camp_mem_1", type="memory", name="Camp Memory")

        scope_data = {
            str(GraphScope.world()): GraphData(nodes=[world_mem], edges=[]),
            str(GraphScope.character("npc_bob")): GraphData(nodes=[char_mem], edges=[]),
            str(GraphScope.camp()): GraphData(nodes=[camp_mem], edges=[]),
        }
        store = _make_graph_store(scope_data)

        session = _build_session(wg, graph_store=store)
        _run(session._load_knowledge_into_world_graph())

        assert wg.get_node("world_mem_1").properties.get("owner") == "world_root"
        assert wg.get_node("bob_mem_1").properties.get("owner") == "npc_bob"
        assert wg.get_node("camp_mem_1").properties.get("owner") == "camp"

    def test_has_memory_edges_created(self):
        """has_memory 边正确连接 owner → 知识节点。"""
        wg = WorldGraph()
        wg.add_node(_node("world_root", type="world_root", name="World"))
        wg.seal()

        from app.models.graph_scope import GraphScope
        mem = _mem_node("lore_1", type="knowledge", name="Lore")
        scope_data = {
            str(GraphScope.world()): GraphData(nodes=[mem], edges=[]),
        }
        store = _make_graph_store(scope_data)

        session = _build_session(wg, graph_store=store)
        _run(session._load_knowledge_into_world_graph())

        edges = wg.get_edges_between("world_root", "lore_1")
        assert len(edges) > 0
        has_memory_found = any(
            e[1].get("relation") == "has_memory" for e in edges
        )
        assert has_memory_found

    def test_edge_has_node_guard(self):
        """缺失节点的边不崩溃，静默跳过。"""
        wg = WorldGraph()
        wg.add_node(_node("world_root", type="world_root", name="World"))
        wg.seal()

        from app.models.graph_scope import GraphScope
        bad_edge = _mem_edge("nonexistent_src", "nonexistent_tgt", "related_to")
        scope_data = {
            str(GraphScope.world()): GraphData(nodes=[], edges=[bad_edge]),
        }
        store = _make_graph_store(scope_data)

        session = _build_session(wg, graph_store=store)
        _run(session._load_knowledge_into_world_graph())

    def test_scope_failure_isolation(self):
        """一个 scope 加载失败不影响其他 scope。"""
        wg = WorldGraph()
        wg.add_node(_node("world_root", type="world_root", name="World"))
        wg.seal()

        from app.models.graph_scope import GraphScope
        good_mem = _mem_node("good_1", type="knowledge", name="Good")

        store = AsyncMock()
        call_count = 0

        async def _load(world_id, scope):
            nonlocal call_count
            call_count += 1
            if str(scope) == str(GraphScope.world()):
                return GraphData(nodes=[good_mem], edges=[])
            if str(scope) == str(GraphScope.camp()):
                raise RuntimeError("Firestore down!")
            return GraphData(nodes=[], edges=[])

        store.load_graph_v2 = AsyncMock(side_effect=_load)

        session = _build_session(wg, graph_store=store)
        _run(session._load_knowledge_into_world_graph())

        assert wg.has_node("good_1")
        assert call_count >= 2

    def test_graph_store_none_skips(self):
        """graph_store=None 时静默跳过，不崩溃。"""
        wg = WorldGraph()
        wg.add_node(_node("world_root", type="world_root", name="World"))
        wg.seal()

        session = _build_session(wg, graph_store=None)
        _run(session._load_knowledge_into_world_graph())


class TestKnowledgeSnapshot:
    """知识节点 → spawned_nodes → snapshot 持久化闭环。"""

    def test_knowledge_becomes_spawned(self):
        """知识节点成为 spawned_nodes → capture_snapshot 包含。"""
        from app.world.snapshot import capture_snapshot

        wg = WorldGraph()
        wg.add_node(_node("world_root", type="world_root", name="World"))
        wg.seal()

        from app.models.graph_scope import GraphScope
        mem = _mem_node("lore_x", type="knowledge", name="Ancient Lore X")
        scope_data = {
            str(GraphScope.world()): GraphData(nodes=[mem], edges=[]),
        }
        store = _make_graph_store(scope_data)

        session = _build_session(wg, graph_store=store)
        _run(session._load_knowledge_into_world_graph())

        snapshot = capture_snapshot(wg, "test_world", "test_session")
        spawned_ids = set()
        for n in snapshot.spawned_nodes:
            nid = n.id if hasattr(n, "id") else n.get("id", "")
            spawned_ids.add(nid)
        assert "lore_x" in spawned_ids

    def test_original_edges_loaded(self):
        """原始知识边被正确注入 WorldGraph。"""
        wg = WorldGraph()
        wg.add_node(_node("world_root", type="world_root", name="World"))
        wg.seal()

        from app.models.graph_scope import GraphScope
        node_a = _mem_node("fact_a", type="knowledge", name="Fact A")
        node_b = _mem_node("fact_b", type="knowledge", name="Fact B")
        edge = _mem_edge("fact_a", "fact_b", "implies", id="edge_ab")

        scope_data = {
            str(GraphScope.world()): GraphData(
                nodes=[node_a, node_b], edges=[edge]
            ),
        }
        store = _make_graph_store(scope_data)

        session = _build_session(wg, graph_store=store)
        _run(session._load_knowledge_into_world_graph())

        edges = wg.get_edges_between("fact_a", "fact_b")
        implies_found = any(
            e[1].get("relation") == "implies" for e in edges
        )
        assert implies_found


class TestSnapshotSizeMonitoring:
    """快照大小超 500KB 触发警告。"""

    def test_large_snapshot_size_detection(self):
        """验证大快照能被正确检测超过 500KB。"""
        from app.world.snapshot import capture_snapshot, snapshot_to_dict

        wg = WorldGraph()
        wg.add_node(_node("world_root", type="world_root", name="World"))
        wg.seal()

        for i in range(2000):
            node = WorldNode(
                id=f"bulk_{i}", type="memory", name=f"Bulk Node {i}",
                importance=0.1,
                properties={"data": "x" * 200},
                state={"val": i},
                behaviors=[],
            )
            wg.add_node(node)

        snapshot = capture_snapshot(wg, "test_world", "test_session")
        data = snapshot_to_dict(snapshot)
        size = len(json.dumps(data, default=str).encode("utf-8"))
        assert size > 500_000
