"""Snapshot 单元测试 -- Step C6

测试 WorldGraph 追踪机制、capture/restore、序列化 roundtrip。
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from app.models.party import Party, PartyMember, TeammateRole
from app.runtime.models.area_state import AreaConnection, AreaDefinition, SubLocationDef
from app.runtime.models.world_constants import WorldConstants
from app.world.graph_builder import GraphBuilder
from app.world.models import (
    Behavior,
    TriggerType,
    WorldEdgeType,
    WorldNode,
    WorldNodeType,
)
from app.world.snapshot import (
    EdgeChangeRecord,
    WorldSnapshot,
    capture_snapshot,
    dict_to_snapshot,
    restore_snapshot,
    snapshot_to_dict,
)
from app.world.world_graph import EdgeChange, WorldGraph


# =============================================================================
# Fixtures: 复用 test_graph_builder 的 mock 数据工厂
# =============================================================================


def _make_world_constants() -> WorldConstants:
    return WorldConstants(
        world_id="test_world",
        name="Test World",
        description="A test world.",
        setting="Fantasy",
        tone="Dark",
    )


def _make_area_registry() -> dict:
    return {
        "town_square": AreaDefinition(
            area_id="town_square",
            name="Town Square",
            description="The central square.",
            danger_level=1,
            area_type="settlement",
            region="Frontier",
            tags=["town"],
            sub_locations=[
                SubLocationDef(
                    id="tavern",
                    name="The Rusty Goblet",
                    description="A cozy tavern.",
                    interaction_type="social",
                ),
            ],
            connections=[
                AreaConnection(
                    target_area_id="dark_forest",
                    connection_type="road",
                    travel_time="1 hour",
                ),
            ],
        ),
        "dark_forest": AreaDefinition(
            area_id="dark_forest",
            name="Dark Forest",
            description="A foreboding forest.",
            danger_level=3,
            area_type="wilderness",
            region="Frontier",
            connections=[
                AreaConnection(
                    target_area_id="town_square",
                    connection_type="road",
                    travel_time="1 hour",
                ),
            ],
        ),
    }


def _make_chapter_registry() -> dict:
    return {
        "ch_01": {
            "id": "ch_01",
            "name": "Chapter 1",
            "description": "The first chapter.",
            "mainline_id": "main_01",
            "status": "active",
            "available_maps": ["town_square"],
            "events": [
                {
                    "id": "evt_test",
                    "name": "Test Event",
                    "trigger_conditions": {
                        "operator": "and",
                        "conditions": [
                            {"type": "location", "params": {"area_id": "town_square"}},
                        ],
                    },
                    "is_required": True,
                    "narrative_directive": "Test.",
                },
            ],
            "transitions": [],
            "tags": ["intro"],
        },
    }


def _make_character_registry() -> dict:
    return {
        "npc_smith": {
            "id": "npc_smith",
            "profile": {
                "name": "Grom",
                "metadata": {
                    "default_map": "town_square",
                    "default_sub_location": "tavern",
                    "tier": "secondary",
                },
            },
        },
    }


def _make_session(with_party: bool = False) -> MagicMock:
    session = MagicMock(spec=["party"])
    if with_party:
        session.party = Party(
            party_id="p_01",
            world_id="test_world",
            session_id="s_01",
            leader_id="player_01",
            members=[
                PartyMember(
                    character_id="npc_smith",
                    name="Grom",
                    role=TeammateRole.WARRIOR,
                    is_active=True,
                ),
            ],
        )
    else:
        session.party = None
    return session


def _make_world() -> MagicMock:
    world = MagicMock(spec=[
        "world_id", "world_constants",
        "area_registry", "chapter_registry", "character_registry",
    ])
    world.world_id = "test_world"
    world.world_constants = _make_world_constants()
    world.area_registry = _make_area_registry()
    world.chapter_registry = _make_chapter_registry()
    world.character_registry = _make_character_registry()
    return world


def _build_graph() -> WorldGraph:
    """构建一个标准测试图（已 seal）。"""
    return GraphBuilder.build(_make_world(), _make_session())


def _make_bare_graph() -> WorldGraph:
    """手动构建最小图（不经 GraphBuilder），方便精确控制 seal 时机。"""
    wg = WorldGraph()
    wg.add_node(WorldNode(id="a", type=WorldNodeType.AREA, name="Area A"))
    wg.add_node(WorldNode(id="b", type=WorldNodeType.AREA, name="Area B"))
    wg.add_edge("a", "b", WorldEdgeType.CONNECTS.value, key="conn_a_b")
    return wg


# =============================================================================
# 追踪机制测试 (8)
# =============================================================================


class TestTrackingMechanism:
    """WorldGraph seal/tracking 基础设施。"""

    def test_pre_seal_no_tracking(self):
        """构建期 add_node 不进 _spawned_nodes。"""
        wg = WorldGraph()
        wg.add_node(WorldNode(id="x", type=WorldNodeType.NPC, name="X"))
        assert len(wg._spawned_nodes) == 0
        assert len(wg._edge_changes) == 0

    def test_post_seal_spawn_tracked(self):
        """seal 后 add_node 记入 _spawned_nodes。"""
        wg = _make_bare_graph()
        wg.seal()
        wg.add_node(WorldNode(id="new", type=WorldNodeType.NPC, name="New"))
        assert "new" in wg._spawned_nodes
        assert wg._spawned_nodes["new"].name == "New"

    def test_post_seal_remove_tracked(self):
        """seal 后 remove_node 记入 _removed_node_ids。"""
        wg = _make_bare_graph()
        wg.seal()
        wg.remove_node("b")
        assert "b" in wg._removed_node_ids

    def test_post_seal_edge_add_tracked(self):
        """seal 后 add_edge 记入 _edge_changes。"""
        wg = _make_bare_graph()
        wg.seal()
        wg.add_edge("a", "b", WorldEdgeType.HOSTS.value, key="hosts_test")
        add_changes = [c for c in wg._edge_changes if c.operation == "add"]
        assert len(add_changes) >= 1
        assert add_changes[0].key == "hosts_test"

    def test_post_seal_edge_update_tracked(self):
        """seal 后 update_edge 记入 _edge_changes。"""
        wg = _make_bare_graph()
        wg.seal()
        wg.update_edge("a", "b", "conn_a_b", {"weight": 5})
        update_changes = [c for c in wg._edge_changes if c.operation == "update"]
        assert len(update_changes) == 1
        assert update_changes[0].attrs == {"weight": 5}

    def test_post_seal_edge_remove_tracked(self):
        """seal 后 remove_edge 记入 _edge_changes。"""
        wg = _make_bare_graph()
        wg.seal()
        wg.remove_edge("a", "b", "conn_a_b")
        remove_changes = [c for c in wg._edge_changes if c.operation == "remove"]
        assert len(remove_changes) >= 1
        assert remove_changes[0].key == "conn_a_b"

    def test_clear_dirty_resets_all(self):
        """clear_dirty 清空所有追踪集合。"""
        wg = _make_bare_graph()
        wg.seal()
        wg.add_node(WorldNode(id="c", type=WorldNodeType.NPC, name="C"))
        wg.set_state("a", "key", "val")
        assert len(wg._spawned_nodes) > 0
        assert len(wg._dirty_nodes) > 0

        wg.clear_dirty()
        assert len(wg._spawned_nodes) == 0
        assert len(wg._removed_node_ids) == 0
        assert len(wg._edge_changes) == 0
        assert len(wg._dirty_nodes) == 0
        assert len(wg._dirty_edges) == 0

    def test_replace_node_not_spawn(self):
        """替换已有节点不算 spawn。"""
        wg = _make_bare_graph()
        wg.seal()
        # 替换已有节点 "a"
        wg.add_node(WorldNode(id="a", type=WorldNodeType.AREA, name="Area A v2"))
        assert "a" not in wg._spawned_nodes


# =============================================================================
# capture_snapshot 测试 (5)
# =============================================================================


class TestCaptureSnapshot:
    """capture_snapshot() 功能测试。"""

    def test_captures_dirty_states(self):
        """dirty 节点 state 被捕获。"""
        wg = _build_graph()
        wg.set_state("town_square", "visited", True)
        wg.set_state("town_square", "visit_count", 3)

        snap = capture_snapshot(wg, "w1", "s1", game_day=2, game_hour=14)
        assert "town_square" in snap.node_states
        assert snap.node_states["town_square"]["visited"] is True
        assert snap.node_states["town_square"]["visit_count"] == 3
        assert snap.game_day == 2
        assert snap.game_hour == 14

    def test_captures_spawned_full_data(self):
        """spawned 节点完整序列化（取当前状态）。"""
        wg = _build_graph()
        new_node = WorldNode(
            id="npc_bandit",
            type=WorldNodeType.NPC,
            name="Bandit",
            properties={"tier": "passerby"},
            state={"hp": 20},
        )
        wg.add_node(new_node)
        # spawn 后修改 state
        wg.set_state("npc_bandit", "hp", 15)

        snap = capture_snapshot(wg, "w1", "s1")
        assert len(snap.spawned_nodes) == 1
        spawned = snap.spawned_nodes[0]
        assert spawned["id"] == "npc_bandit"
        # 应捕获当前状态（hp=15），而非 spawn 时的状态（hp=20）
        assert spawned["state"]["hp"] == 15

    def test_captures_removed_ids(self):
        """build 期节点被 remove 记录。"""
        wg = _build_graph()
        wg.remove_node("npc_smith")

        snap = capture_snapshot(wg, "w1", "s1")
        assert "npc_smith" in snap.removed_node_ids

    def test_spawned_then_removed_excluded(self):
        """spawn → remove 净零排除。"""
        wg = _build_graph()
        wg.add_node(WorldNode(id="temp", type=WorldNodeType.NPC, name="Temp"))
        wg.remove_node("temp")

        snap = capture_snapshot(wg, "w1", "s1")
        # temp 不应出现在 spawned 或 removed 中
        assert all(s["id"] != "temp" for s in snap.spawned_nodes)
        assert "temp" not in snap.removed_node_ids

    def test_edge_compaction(self):
        """同 key 多次操作压缩。"""
        wg = _build_graph()
        # add → update → 应合并为 add with merged attrs
        wg.add_edge("town_square", "dark_forest", WorldEdgeType.HOSTS.value,
                     key="test_edge", one_way=True, weight=1)
        wg.update_edge("town_square", "dark_forest", "test_edge", {"weight": 5})

        snap = capture_snapshot(wg, "w1", "s1")
        test_edges = [e for e in snap.modified_edges
                      if e.key == "test_edge"]
        assert len(test_edges) == 1
        assert test_edges[0].operation == "add"
        assert test_edges[0].attrs.get("weight") == 5


# =============================================================================
# restore_snapshot 测试 (7)
# =============================================================================


class TestRestoreSnapshot:
    """restore_snapshot() 功能测试。"""

    def test_restore_states(self):
        """节点 state 恢复。"""
        wg = _build_graph()
        snap = WorldSnapshot(
            world_id="w1", session_id="s1",
            node_states={
                "town_square": {"visited": True, "visit_count": 5},
            },
        )
        restore_snapshot(wg, snap)

        node = wg.get_node("town_square")
        assert node.state["visited"] is True
        assert node.state["visit_count"] == 5

    def test_restore_spawned(self):
        """spawned 节点重建。"""
        wg = _build_graph()
        snap = WorldSnapshot(
            world_id="w1", session_id="s1",
            spawned_nodes=[{
                "id": "npc_goblin",
                "type": "npc",
                "name": "Goblin",
                "properties": {"tier": "passerby"},
                "state": {"hp": 10, "hostile": True},
            }],
        )
        restore_snapshot(wg, snap)

        goblin = wg.get_node("npc_goblin")
        assert goblin is not None
        assert goblin.name == "Goblin"
        assert goblin.state["hp"] == 10
        assert goblin.state["hostile"] is True

    def test_restore_removed(self):
        """节点删除重现。"""
        wg = _build_graph()
        assert wg.has_node("npc_smith")

        snap = WorldSnapshot(
            world_id="w1", session_id="s1",
            removed_node_ids=["npc_smith"],
        )
        restore_snapshot(wg, snap)
        assert not wg.has_node("npc_smith")

    def test_restore_edge_add(self):
        """边添加重放。"""
        wg = _build_graph()
        snap = WorldSnapshot(
            world_id="w1", session_id="s1",
            modified_edges=[
                EdgeChangeRecord(
                    operation="add",
                    source="town_square",
                    target="npc_smith",
                    key="custom_edge",
                    relation=WorldEdgeType.HOSTS.value,
                    attrs={"custom": True},
                ),
            ],
        )
        restore_snapshot(wg, snap)

        edge = wg.get_edge("town_square", "npc_smith", "custom_edge")
        assert edge is not None
        assert edge["custom"] is True

    def test_restore_edge_update(self):
        """边更新重放。"""
        wg = _build_graph()
        # 验证原始边存在
        original = wg.get_edge(
            "loc_town_square_tavern", "npc_smith", "hosts_npc_smith"
        )
        assert original is not None

        snap = WorldSnapshot(
            world_id="w1", session_id="s1",
            modified_edges=[
                EdgeChangeRecord(
                    operation="update",
                    source="loc_town_square_tavern",
                    target="npc_smith",
                    key="hosts_npc_smith",
                    relation=WorldEdgeType.HOSTS.value,
                    attrs={"reputation": 50},
                ),
            ],
        )
        restore_snapshot(wg, snap)

        edge = wg.get_edge(
            "loc_town_square_tavern", "npc_smith", "hosts_npc_smith"
        )
        assert edge["reputation"] == 50

    def test_restore_edge_remove(self):
        """边删除重放。"""
        wg = _build_graph()
        # 有一条 CONNECTS 边
        assert wg.get_edge("town_square", "dark_forest") is not None

        # 获取实际 key
        edges = wg.get_edges_between("town_square", "dark_forest")
        connects = [(k, d) for k, d in edges
                    if d.get("relation") == WorldEdgeType.CONNECTS.value]
        assert len(connects) == 1
        edge_key = connects[0][0]

        snap = WorldSnapshot(
            world_id="w1", session_id="s1",
            modified_edges=[
                EdgeChangeRecord(
                    operation="remove",
                    source="town_square",
                    target="dark_forest",
                    key=edge_key,
                    relation=WorldEdgeType.CONNECTS.value,
                ),
            ],
        )
        restore_snapshot(wg, snap)

        # 该边应已删除
        assert wg.get_edge("town_square", "dark_forest", edge_key) is None

    def test_restore_clears_dirty(self):
        """恢复后 dirty 清空。"""
        wg = _build_graph()
        snap = WorldSnapshot(
            world_id="w1", session_id="s1",
            node_states={"town_square": {"visited": True}},
        )
        restore_snapshot(wg, snap)

        assert len(wg._dirty_nodes) == 0
        assert len(wg._dirty_edges) == 0
        assert len(wg._spawned_nodes) == 0
        assert len(wg._removed_node_ids) == 0
        assert len(wg._edge_changes) == 0
        assert wg._sealed is True


# =============================================================================
# 端到端 roundtrip 测试 (3)
# =============================================================================


class TestRoundtrip:
    """capture → serialize → rebuild → restore → 验证一致。"""

    def test_full_roundtrip(self):
        """build → mutate → capture → dict → rebuild → restore → 验证。"""
        # 1. Build + mutate
        wg1 = _build_graph()
        wg1.set_state("town_square", "visited", True)
        wg1.set_state("town_square", "visit_count", 3)
        wg1.add_node(WorldNode(
            id="npc_bandit", type=WorldNodeType.NPC, name="Bandit",
            state={"hp": 20, "hostile": True},
        ))
        wg1.add_edge("dark_forest", "npc_bandit", WorldEdgeType.HOSTS.value,
                      key="hosts_bandit")
        wg1.remove_node("evt_test")

        # 2. Capture + serialize
        snap = capture_snapshot(wg1, "w1", "s1", game_day=3, game_hour=20)
        data = snapshot_to_dict(snap)

        # 3. Rebuild + restore
        snap2 = dict_to_snapshot(data)
        assert snap2 is not None

        wg2 = _build_graph()
        restore_snapshot(wg2, snap2)

        # 4. Verify
        ts = wg2.get_node("town_square")
        assert ts.state["visited"] is True
        assert ts.state["visit_count"] == 3

        bandit = wg2.get_node("npc_bandit")
        assert bandit is not None
        assert bandit.state["hp"] == 20
        assert bandit.state["hostile"] is True

        assert not wg2.has_node("evt_test")

        hosts_edge = wg2.get_edge("dark_forest", "npc_bandit", "hosts_bandit")
        assert hosts_edge is not None

    def test_empty_session(self):
        """无变更 roundtrip。"""
        wg1 = _build_graph()
        snap = capture_snapshot(wg1, "w1", "s1")
        data = snapshot_to_dict(snap)
        snap2 = dict_to_snapshot(data)
        assert snap2 is not None

        wg2 = _build_graph()
        node_count_before = wg2.node_count()
        restore_snapshot(wg2, snap2)
        assert wg2.node_count() == node_count_before

    def test_behavior_state_survives(self):
        """behavior_fired/cooldowns 通过 node.state roundtrip。"""
        wg1 = _build_graph()
        # 模拟 behavior 执行后的 state
        evt = wg1.get_node("evt_test")
        evt.mark_behavior_fired("bh_unlock_evt_test")
        evt.set_behavior_cooldown("bh_complete_evt_test", 3)
        wg1._dirty_nodes.add("evt_test")

        snap = capture_snapshot(wg1, "w1", "s1")
        data = snapshot_to_dict(snap)
        snap2 = dict_to_snapshot(data)

        wg2 = _build_graph()
        restore_snapshot(wg2, snap2)

        evt2 = wg2.get_node("evt_test")
        assert "bh_unlock_evt_test" in evt2.state.get("behavior_fired", [])
        assert evt2.state.get("behavior_cooldowns", {}).get("bh_complete_evt_test") == 3


# =============================================================================
# 序列化测试 (3)
# =============================================================================


class TestSerialization:
    """snapshot_to_dict / dict_to_snapshot。"""

    def test_snapshot_to_dict(self):
        """datetime 转 ISO string。"""
        snap = WorldSnapshot(
            world_id="w1", session_id="s1",
            created_at=datetime(2026, 2, 15, 12, 0, 0),
        )
        data = snapshot_to_dict(snap)
        assert isinstance(data["created_at"], str)
        assert "2026-02-15" in data["created_at"]

    def test_dict_to_snapshot_roundtrip(self):
        """dict ↔ snapshot 往返。"""
        snap = WorldSnapshot(
            world_id="w1", session_id="s1",
            game_day=5, game_hour=16,
            node_states={"a": {"x": 1}},
            spawned_nodes=[{"id": "b", "type": "npc", "name": "B"}],
            removed_node_ids=["c"],
            modified_edges=[
                EdgeChangeRecord(
                    operation="add", source="a", target="b",
                    key="k", relation="hosts",
                ),
            ],
        )
        data = snapshot_to_dict(snap)
        snap2 = dict_to_snapshot(data)
        assert snap2 is not None
        assert snap2.world_id == "w1"
        assert snap2.game_day == 5
        assert snap2.node_states == {"a": {"x": 1}}
        assert len(snap2.spawned_nodes) == 1
        assert snap2.removed_node_ids == ["c"]
        assert len(snap2.modified_edges) == 1

    def test_dict_to_snapshot_invalid(self):
        """无效数据返回 None。"""
        assert dict_to_snapshot(None) is None
        assert dict_to_snapshot({}) is None
        assert dict_to_snapshot({"invalid": True}) is None
        assert dict_to_snapshot("not a dict") is None


# =============================================================================
# GraphBuilder seal 回归
# =============================================================================


class TestGraphBuilderSeal:
    """验证 GraphBuilder.build() 后图已 sealed。"""

    def test_build_produces_sealed_graph(self):
        wg = _build_graph()
        assert wg._sealed is True

    def test_build_clean_tracking(self):
        """build 后追踪集合为空（seal 时 clear_dirty）。"""
        wg = _build_graph()
        assert len(wg._spawned_nodes) == 0
        assert len(wg._removed_node_ids) == 0
        assert len(wg._edge_changes) == 0
        assert len(wg._dirty_nodes) == 0

    def test_stats_includes_tracking(self):
        """stats() 包含追踪统计。"""
        wg = _build_graph()
        stats = wg.stats()
        assert "sealed" in stats
        assert stats["sealed"] is True
        assert stats["spawned_count"] == 0
        assert stats["removed_count"] == 0
        assert stats["edge_change_count"] == 0


# =============================================================================
# Codex 审查修复验证
# =============================================================================


class TestConnectsReverseEdgeTracking:
    """Codex 审查: CONNECTS 反向边删除须被追踪。"""

    def test_connects_add_remove_roundtrip(self):
        """CONNECTS add → remove → roundtrip 不应残留幽灵反向边。"""
        wg = _build_graph()

        # 添加新 CONNECTS 边（自动生成反向边）
        wg.add_edge("town_square", "dark_forest", WorldEdgeType.CONNECTS.value,
                     key="new_conn")
        # 删除正向边（应同时追踪反向边删除）
        wg.remove_edge("town_square", "dark_forest", "new_conn")

        snap = capture_snapshot(wg, "w1", "s1")
        data = snapshot_to_dict(snap)
        snap2 = dict_to_snapshot(data)

        wg2 = _build_graph()
        restore_snapshot(wg2, snap2)

        # 不应有残留的反向边
        assert wg2.get_edge("dark_forest", "town_square", "new_conn_rev") is None

    def test_connects_reverse_tracked_in_edge_changes(self):
        """remove_edge(CONNECTS) 应在 _edge_changes 中记录反向边删除。"""
        wg = _make_bare_graph()
        wg.seal()
        # conn_a_b 和 conn_a_b_rev 已存在
        wg.remove_edge("a", "b", "conn_a_b")

        # 应有正向 + 反向两条 remove 记录
        removes = [c for c in wg._edge_changes if c.operation == "remove"]
        assert len(removes) == 2
        sources = {(r.source, r.target, r.key) for r in removes}
        assert ("a", "b", "conn_a_b") in sources
        assert ("b", "a", "conn_a_b_rev") in sources


class TestRemoveSpawnSameId:
    """Codex 审查: remove → spawn 同 ID 不应被误判为净零。"""

    def test_remove_then_spawn_same_id(self):
        """先删除构建期节点，再 spawn 同 ID 新节点。"""
        wg = _build_graph()
        assert wg.has_node("npc_smith")

        # 删除构建期节点
        wg.remove_node("npc_smith")
        assert not wg.has_node("npc_smith")

        # 重新 spawn 同 ID（不同数据）
        wg.add_node(WorldNode(
            id="npc_smith", type=WorldNodeType.NPC, name="New Smith",
            state={"hp": 100, "is_new": True},
        ))

        snap = capture_snapshot(wg, "w1", "s1")

        # 应出现在 spawned_nodes，不应出现在 removed_node_ids
        assert any(s["id"] == "npc_smith" for s in snap.spawned_nodes)
        assert "npc_smith" not in snap.removed_node_ids

    def test_remove_spawn_roundtrip(self):
        """remove → spawn 同 ID 完整 roundtrip 验证。"""
        wg1 = _build_graph()
        wg1.remove_node("npc_smith")
        wg1.add_node(WorldNode(
            id="npc_smith", type=WorldNodeType.NPC, name="New Smith v2",
            state={"hp": 200},
        ))

        snap = capture_snapshot(wg1, "w1", "s1")
        data = snapshot_to_dict(snap)
        snap2 = dict_to_snapshot(data)

        wg2 = _build_graph()
        restore_snapshot(wg2, snap2)

        node = wg2.get_node("npc_smith")
        assert node is not None
        assert node.name == "New Smith v2"
        assert node.state["hp"] == 200


class TestRestoreEdgeNoOneWayPollution:
    """Codex 审查: restore 非 CONNECTS 边不应带 one_way 属性。"""

    def test_hosts_edge_no_one_way(self):
        """恢复 HOSTS 边时不应有 one_way 属性。"""
        wg = _build_graph()
        snap = WorldSnapshot(
            world_id="w1", session_id="s1",
            modified_edges=[
                EdgeChangeRecord(
                    operation="add",
                    source="dark_forest",
                    target="npc_smith",
                    key="hosts_new",
                    relation=WorldEdgeType.HOSTS.value,
                    attrs={"weight": 1},
                ),
            ],
        )
        restore_snapshot(wg, snap)

        edge = wg.get_edge("dark_forest", "npc_smith", "hosts_new")
        assert edge is not None
        assert "one_way" not in edge


class TestNestedDatetimeSerialization:
    """Codex 审查: 嵌套 datetime 也应被序列化。"""

    def test_spawned_node_datetime_serialized(self):
        """spawned_nodes 中的 created_at/updated_at 应转 ISO string。"""
        wg = _build_graph()
        wg.add_node(WorldNode(
            id="npc_test", type=WorldNodeType.NPC, name="Test",
        ))

        snap = capture_snapshot(wg, "w1", "s1")
        data = snapshot_to_dict(snap)

        # spawned_nodes 中的 datetime 应为 string
        for sn in data.get("spawned_nodes", []):
            if "created_at" in sn:
                assert isinstance(sn["created_at"], str)
            if "updated_at" in sn:
                assert isinstance(sn["updated_at"], str)


class TestRestoreIndexConsistency:
    """Codex 审查: 恢复后索引应保持一致。"""

    def test_spawned_node_indexed_after_restore(self):
        """恢复的 spawned 节点应出现在 type_index 中。"""
        wg = _build_graph()
        snap = WorldSnapshot(
            world_id="w1", session_id="s1",
            spawned_nodes=[{
                "id": "npc_new",
                "type": "npc",
                "name": "New NPC",
                "state": {},
            }],
        )
        restore_snapshot(wg, snap)

        assert "npc_new" in wg.get_by_type(WorldNodeType.NPC)

    def test_spawned_node_with_edge_indexed(self):
        """恢复 spawned 节点 + HOSTS 边后 _entities_at 索引正确。"""
        wg = _build_graph()
        snap = WorldSnapshot(
            world_id="w1", session_id="s1",
            spawned_nodes=[{
                "id": "npc_guard",
                "type": "npc",
                "name": "Guard",
                "state": {},
            }],
            modified_edges=[
                EdgeChangeRecord(
                    operation="add",
                    source="town_square",
                    target="npc_guard",
                    key="hosts_guard",
                    relation=WorldEdgeType.HOSTS.value,
                ),
            ],
        )
        restore_snapshot(wg, snap)

        entities = wg.get_entities_at("town_square")
        assert "npc_guard" in entities
