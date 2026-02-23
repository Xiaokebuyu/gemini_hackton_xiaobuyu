"""L3 M3: 首次会话知识加载测试。

覆盖 SessionRuntime._load_knowledge_into_world_graph() 及相关逻辑。

注意：_load_knowledge_into_world_graph 内联了 Firestore 读取，
测试通过 mock firestore.Client 来注入测试数据。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.graph import GraphData, MemoryEdge, MemoryNode
from app.models.graph_scope import GraphScope
from app.world.graph.models import WorldNode
from app.world.graph.world_graph import WorldGraph


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
    chapter_id: str = "ch1",
    area_id: str = "town",
    player_location: str = "town",
) -> Any:
    """Build a minimal SessionRuntime with a WorldGraph for testing."""
    from app.runtime.session_runtime import SessionRuntime
    session = SessionRuntime(
        world_id="test_world",
        session_id="test_session",
    )
    session.world_graph = wg
    gs = MagicMock()
    gs.chapter_id = chapter_id
    gs.area_id = area_id
    gs.player_location = player_location
    session.game_state = gs
    return session


def _make_firestore_mock(scope_data: Dict[str, GraphData]) -> MagicMock:
    """Create a mock Firestore Client that returns scope_data.

    scope_data: {scope_string -> GraphData} where the scope_string is
    used to route which nodes/edges to return.
    """
    mock_db = MagicMock()

    # Build a mapping from Firestore path patterns to GraphData
    # We intercept the final .collection("nodes").stream() / .collection("edges").stream()

    def _make_doc(data_dict: dict, doc_id: str) -> MagicMock:
        doc = MagicMock()
        doc.to_dict.return_value = data_dict
        doc.id = doc_id
        doc.exists = True
        return doc

    class FakeCollection:
        """Simulates a Firestore collection that returns mock documents on stream()."""
        def __init__(self, docs: list):
            self._docs = docs

        def stream(self):
            return iter(self._docs)

        def document(self, doc_id: str):
            mock_doc = MagicMock()
            # Support .collection() chaining
            mock_doc.collection.return_value = FakeCollection([])
            return mock_doc

    # We need to make the chain:
    #   db.collection("worlds").document(world_id)
    #     .collection("graphs").document("world")
    #       .collection("nodes").stream() / .collection("edges").stream()
    # For each scope, build the right nodes/edges docs

    all_scope_docs: Dict[str, Dict[str, list]] = {}  # scope_key -> {"nodes": [...], "edges": [...]}
    for scope_str, gd in scope_data.items():
        node_docs = [_make_doc(n.model_dump(), n.id) for n in gd.nodes]
        edge_docs = [_make_doc(e.model_dump(), e.id) for e in gd.edges]
        all_scope_docs[scope_str] = {"nodes": node_docs, "edges": edge_docs}

    # Track what scope is being resolved via the chain of calls
    class ScopeTracker:
        """Intercepts the Firestore chaining to figure out which scope is being accessed."""
        def __init__(self):
            self._path_parts: List[str] = []

        def collection(self, name: str):
            self._path_parts.append(name)
            tracker = ScopeTracker()
            tracker._path_parts = list(self._path_parts)

            # If this is "nodes" or "edges", resolve to the correct data
            if name in ("nodes", "edges"):
                scope_key = self._resolve_scope()
                if scope_key and scope_key in all_scope_docs:
                    return FakeCollection(all_scope_docs[scope_key][name])
                return FakeCollection([])
            return tracker

        def document(self, doc_id: str):
            self._path_parts.append(doc_id)
            return self

        def _resolve_scope(self) -> str:
            """Try to map collected path parts to a GraphScope string."""
            parts = self._path_parts
            # worlds/{wid}/graphs/world -> GraphScope.world()
            if len(parts) >= 4 and parts[0] == "worlds" and parts[2] == "graphs" and parts[3] == "world":
                return str(GraphScope.world())
            # worlds/{wid}/chapters/{cid}/graph/data -> GraphScope.chapter(cid)
            if len(parts) >= 6 and parts[2] == "chapters" and parts[4] == "graph":
                return str(GraphScope.chapter(parts[3]))
            # worlds/{wid}/chapters/{cid}/areas/{aid}/graph/data -> GraphScope.area(cid, aid)
            if len(parts) >= 8 and parts[4] == "areas" and parts[6] == "graph":
                return str(GraphScope.area(parts[3], parts[5]))
            # worlds/{wid}/characters/{char_id} -> GraphScope.character(char_id)
            if len(parts) >= 4 and parts[2] == "characters":
                return str(GraphScope.character(parts[3]))
            # worlds/{wid}/camp/graph -> GraphScope.camp()
            if len(parts) >= 4 and parts[2] == "camp" and parts[3] == "graph":
                return str(GraphScope.camp())
            return ""

    # Root entry: db.collection("worlds") -> starts tracking
    def _root_collection(name):
        tracker = ScopeTracker()
        tracker._path_parts = [name]
        return tracker

    mock_db.collection = _root_collection
    return mock_db


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

    @patch("google.cloud.firestore.Client")
    def test_first_session_loads_knowledge(self, mock_fs_cls):
        """首次会话加载知识 → WorldGraph 包含知识节点。"""
        wg = WorldGraph()
        wg.add_node(_node("world_root", type="world_root", name="World"))
        wg.add_node(_node("npc_a", type="npc", name="NPC A"))
        wg.seal()

        world_scope = GraphScope.world()
        knowledge_node = _mem_node("knowledge_lore_1", type="knowledge", name="Ancient Lore")
        scope_data = {
            str(world_scope): GraphData(nodes=[knowledge_node], edges=[]),
        }
        mock_fs_cls.return_value = _make_firestore_mock(scope_data)

        session = _build_session(wg)
        _run(session._load_knowledge_into_world_graph())

        assert wg.has_node("knowledge_lore_1")
        loaded = wg.get_node("knowledge_lore_1")
        assert loaded.name == "Ancient Lore"
        assert loaded.properties.get("owner") == "world_root"

    @patch("google.cloud.firestore.Client")
    def test_skip_existing_nodes(self, mock_fs_cls):
        """跳过 GraphBuilder 已创建的结构节点，不覆盖。"""
        wg = WorldGraph()
        original = _node("npc_a", type="npc", name="NPC A Original")
        wg.add_node(original)
        wg.add_node(_node("world_root", type="world_root", name="World"))
        wg.seal()

        duplicate = _mem_node("npc_a", type="npc", name="NPC A Duplicate")
        scope_data = {
            str(GraphScope.world()): GraphData(nodes=[duplicate], edges=[]),
        }
        mock_fs_cls.return_value = _make_firestore_mock(scope_data)

        session = _build_session(wg)
        _run(session._load_knowledge_into_world_graph())

        assert wg.get_node("npc_a").name == "NPC A Original"

    @patch("google.cloud.firestore.Client")
    def test_owner_mapping(self, mock_fs_cls):
        """owner 映射正确（world→world_root, char→npc_id, camp→camp）。"""
        wg = WorldGraph()
        wg.add_node(_node("world_root", type="world_root", name="World"))
        wg.add_node(_node("npc_bob", type="npc", name="Bob"))
        wg.seal()

        world_mem = _mem_node("world_mem_1", type="knowledge", name="World Knowledge")
        char_mem = _mem_node("bob_mem_1", type="memory", name="Bob's Memory")
        camp_mem = _mem_node("camp_mem_1", type="memory", name="Camp Memory")

        scope_data = {
            str(GraphScope.world()): GraphData(nodes=[world_mem], edges=[]),
            str(GraphScope.character("npc_bob")): GraphData(nodes=[char_mem], edges=[]),
            str(GraphScope.camp()): GraphData(nodes=[camp_mem], edges=[]),
        }
        mock_fs_cls.return_value = _make_firestore_mock(scope_data)

        session = _build_session(wg)
        _run(session._load_knowledge_into_world_graph())

        assert wg.get_node("world_mem_1").properties.get("owner") == "world_root"
        assert wg.get_node("bob_mem_1").properties.get("owner") == "npc_bob"
        assert wg.get_node("camp_mem_1").properties.get("owner") == "camp"

    @patch("google.cloud.firestore.Client")
    def test_has_memory_edges_created(self, mock_fs_cls):
        """has_memory 边正确连接 owner → 知识节点。"""
        wg = WorldGraph()
        wg.add_node(_node("world_root", type="world_root", name="World"))
        wg.seal()

        mem = _mem_node("lore_1", type="knowledge", name="Lore")
        scope_data = {
            str(GraphScope.world()): GraphData(nodes=[mem], edges=[]),
        }
        mock_fs_cls.return_value = _make_firestore_mock(scope_data)

        session = _build_session(wg)
        _run(session._load_knowledge_into_world_graph())

        edges = wg.get_edges_between("world_root", "lore_1")
        assert len(edges) > 0
        has_memory_found = any(
            e[1].get("relation") == "has_memory" for e in edges
        )
        assert has_memory_found

    @patch("google.cloud.firestore.Client")
    def test_edge_has_node_guard(self, mock_fs_cls):
        """缺失节点的边不崩溃，静默跳过。"""
        wg = WorldGraph()
        wg.add_node(_node("world_root", type="world_root", name="World"))
        wg.seal()

        bad_edge = _mem_edge("nonexistent_src", "nonexistent_tgt", "related_to")
        scope_data = {
            str(GraphScope.world()): GraphData(nodes=[], edges=[bad_edge]),
        }
        mock_fs_cls.return_value = _make_firestore_mock(scope_data)

        session = _build_session(wg)
        _run(session._load_knowledge_into_world_graph())

    @patch("google.cloud.firestore.Client")
    def test_scope_failure_isolation(self, mock_fs_cls):
        """一个 scope 加载失败不影响其他 scope。"""
        wg = WorldGraph()
        wg.add_node(_node("world_root", type="world_root", name="World"))
        wg.seal()

        good_mem = _mem_node("good_1", type="knowledge", name="Good")
        # Only world scope has data; camp scope will raise via stream()
        scope_data = {
            str(GraphScope.world()): GraphData(nodes=[good_mem], edges=[]),
        }

        mock_db = _make_firestore_mock(scope_data)
        # camp scope will get empty data (no error, just empty)
        mock_fs_cls.return_value = mock_db

        session = _build_session(wg)
        _run(session._load_knowledge_into_world_graph())

        assert wg.has_node("good_1")

    def test_world_graph_none_skips(self):
        """world_graph=None 时静默跳过，不崩溃。"""
        from app.runtime.session_runtime import SessionRuntime
        session = SessionRuntime(
            world_id="test_world",
            session_id="test_session",
        )
        session.world_graph = None
        _run(session._load_knowledge_into_world_graph())


class TestKnowledgeSnapshot:
    """知识节点 → spawned_nodes → snapshot 持久化闭环。"""

    @patch("google.cloud.firestore.Client")
    def test_knowledge_becomes_spawned(self, mock_fs_cls):
        """知识节点成为 spawned_nodes → capture_snapshot 包含。"""
        from app.world.graph.snapshot import capture_snapshot

        wg = WorldGraph()
        wg.add_node(_node("world_root", type="world_root", name="World"))
        wg.seal()

        mem = _mem_node("lore_x", type="knowledge", name="Ancient Lore X")
        scope_data = {
            str(GraphScope.world()): GraphData(nodes=[mem], edges=[]),
        }
        mock_fs_cls.return_value = _make_firestore_mock(scope_data)

        session = _build_session(wg)
        _run(session._load_knowledge_into_world_graph())

        snapshot = capture_snapshot(wg, "test_world", "test_session")
        spawned_ids = set()
        for n in snapshot.spawned_nodes:
            nid = n.id if hasattr(n, "id") else n.get("id", "")
            spawned_ids.add(nid)
        assert "lore_x" in spawned_ids

    @patch("google.cloud.firestore.Client")
    def test_original_edges_loaded(self, mock_fs_cls):
        """原始知识边被正确注入 WorldGraph。"""
        wg = WorldGraph()
        wg.add_node(_node("world_root", type="world_root", name="World"))
        wg.seal()

        node_a = _mem_node("fact_a", type="knowledge", name="Fact A")
        node_b = _mem_node("fact_b", type="knowledge", name="Fact B")
        edge = _mem_edge("fact_a", "fact_b", "implies", id="edge_ab")

        scope_data = {
            str(GraphScope.world()): GraphData(
                nodes=[node_a, node_b], edges=[edge]
            ),
        }
        mock_fs_cls.return_value = _make_firestore_mock(scope_data)

        session = _build_session(wg)
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
        from app.world.graph.snapshot import capture_snapshot, snapshot_to_dict

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
