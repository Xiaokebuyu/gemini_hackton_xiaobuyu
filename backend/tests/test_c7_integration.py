"""C7/C8 Integration Tests — WorldGraph 接入 V4 Pipeline

测试:
  C7a: WorldGraph 挂载 + 快照 I/O（6 tests）
  C7b: BehaviorEngine 集成（4 tests — C8 移除双写）
  C7c: 工具 WorldGraph 操作（2 tests — C8 移除双写辅助）
  C7 配置（1 test — C8 移除 dual_write）
"""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

# Stub out 'mcp' package if not installed (needed to import admin services)
if "mcp" not in sys.modules:
    _mcp_stub = ModuleType("mcp")
    _mcp_client = ModuleType("mcp.client")
    _mcp_session = ModuleType("mcp.client.session")
    _mcp_session.ClientSession = MagicMock  # type: ignore
    _mcp_stdio = ModuleType("mcp.client.stdio")
    _mcp_stdio.stdio_client = MagicMock  # type: ignore
    _mcp_stdio.StdioServerParameters = MagicMock  # type: ignore
    _mcp_sse = ModuleType("mcp.client.sse")
    _mcp_sse.sse_client = MagicMock  # type: ignore
    _mcp_http = ModuleType("mcp.client.streamable_http")
    _mcp_http.streamable_http_client = MagicMock  # type: ignore
    _mcp_types = ModuleType("mcp.types")
    _mcp_types.Tool = MagicMock  # type: ignore

    sys.modules["mcp"] = _mcp_stub
    sys.modules["mcp.client"] = _mcp_client
    sys.modules["mcp.client.session"] = _mcp_session
    sys.modules["mcp.client.stdio"] = _mcp_stdio
    sys.modules["mcp.client.sse"] = _mcp_sse
    sys.modules["mcp.client.streamable_http"] = _mcp_http
    sys.modules["mcp.types"] = _mcp_types

from app.models.narrative import NarrativeProgress
from app.models.party import Party, PartyMember, TeammateRole
from app.models.state_delta import GameState, GameTimeState
from app.runtime.models.area_state import AreaConnection, AreaDefinition, SubLocationDef
from app.runtime.models.world_constants import WorldConstants
from app.runtime.session_runtime import SessionRuntime
from app.world.graph_builder import GraphBuilder
from app.world.models import (
    EventStatus,
    TickContext,
    TickResult,
    WorldEdgeType,
    WorldNodeType,
)
from app.world.snapshot import (
    capture_snapshot,
    dict_to_snapshot,
    restore_snapshot,
    snapshot_to_dict,
)
from app.world.world_graph import WorldGraph


# =============================================================================
# Fixtures: mock data factories
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
                    "id": "evt_arrive",
                    "name": "Arrive in Town",
                    "trigger_conditions": {
                        "operator": "and",
                        "conditions": [
                            {"type": "location", "params": {"area_id": "town_square"}},
                        ],
                    },
                    "completion_conditions": {
                        "operator": "and",
                        "conditions": [
                            {"type": "npc_interacted", "params": {"npc_id": "npc_smith", "min_interactions": 1}},
                        ],
                    },
                    "is_required": True,
                    "narrative_directive": "Arrive in town and talk to Smith.",
                },
                {
                    "id": "evt_explore",
                    "name": "Explore Forest",
                    "trigger_conditions": {
                        "operator": "and",
                        "conditions": [
                            {"type": "event_triggered", "params": {"event_id": "evt_arrive"}},
                        ],
                    },
                    "is_required": False,
                    "narrative_directive": "Explore the dark forest.",
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


def _make_world() -> MagicMock:
    world = MagicMock(spec=[
        "world_id", "world_constants",
        "area_registry", "chapter_registry", "character_registry",
        "get_area_definition", "get_character",
    ])
    world.world_id = "test_world"
    world.world_constants = _make_world_constants()
    world.area_registry = _make_area_registry()
    world.chapter_registry = _make_chapter_registry()
    world.character_registry = _make_character_registry()
    world.get_area_definition = lambda aid: world.area_registry.get(aid)
    world.get_character = lambda cid: world.character_registry.get(cid)
    return world


def _make_session_runtime(world=None, with_party=False) -> SessionRuntime:
    """创建轻量 SessionRuntime（不调用 restore，手动填充状态）。"""
    world = world or _make_world()
    session = SessionRuntime(
        world_id="test_world",
        session_id="s_01",
        world=world,
    )
    session.game_state = GameState(
        world_id="test_world",
        session_id="s_01",
        player_location="town_square",
        area_id="town_square",
        chapter_id="ch_01",
    )
    session.time = GameTimeState(day=1, hour=10, minute=0, period="day", formatted="第1天 10:00")
    session.narrative = NarrativeProgress(
        current_mainline="main_01",
        current_chapter="ch_01",
        events_triggered=[],
    )
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
    return session


def _build_world_graph(session: SessionRuntime) -> WorldGraph:
    """构建并返回 WorldGraph。"""
    return GraphBuilder.build(session.world, session)


# =============================================================================
# C7a: WorldGraph 挂载 + 快照 I/O (6 tests)
# =============================================================================


class TestC7aMounting:
    """C7a: WorldGraph 挂载到 SessionRuntime + 快照 I/O。"""

    def test_build_world_graph_on_session(self):
        """_build_world_graph() 后 session.world_graph 不为 None。"""
        session = _make_session_runtime()
        session._build_world_graph()

        assert session.world_graph is not None
        assert session._behavior_engine is not None
        assert not session._world_graph_failed

        stats = session.world_graph.stats()
        assert stats["node_count"] > 0

    def test_build_failure_graceful(self):
        """GraphBuilder 抛异常时 _world_graph_failed=True，不影响管线。"""
        session = _make_session_runtime()
        with patch("app.world.graph_builder.GraphBuilder.build", side_effect=RuntimeError("boom")):
            session._build_world_graph()

        assert session.world_graph is None
        assert session._behavior_engine is None
        assert session._world_graph_failed is True

    def test_snapshot_persist_and_restore(self):
        """快照捕获 → 序列化 → 反序列化 → 恢复 roundtrip。"""
        session = _make_session_runtime()
        wg = _build_world_graph(session)
        session.world_graph = wg

        wg.merge_state("town_square", {"visited": True, "visit_count": 3})

        snap = capture_snapshot(wg, "test_world", "s_01", game_day=2, game_hour=14)
        assert "town_square" in snap.node_states
        assert snap.node_states["town_square"]["visited"] is True

        data = snapshot_to_dict(snap)
        snap2 = dict_to_snapshot(data)
        assert snap2 is not None
        assert snap2.node_states["town_square"]["visit_count"] == 3

        wg2 = _build_world_graph(session)
        restore_snapshot(wg2, snap2)

        node = wg2.get_node("town_square")
        assert node.state["visited"] is True
        assert node.state["visit_count"] == 3

    def test_snapshot_roundtrip_with_event_state(self):
        """build→mutate event status→persist→新build→restore→验证一致。"""
        session = _make_session_runtime()
        wg = _build_world_graph(session)

        wg.merge_state("evt_arrive", {"status": "available"})
        snap = capture_snapshot(wg, "test_world", "s_01")
        data = snapshot_to_dict(snap)

        wg2 = _build_world_graph(session)
        snap2 = dict_to_snapshot(data)
        restore_snapshot(wg2, snap2)

        node = wg2.get_node("evt_arrive")
        assert node.state["status"] == "available"

    def test_feature_flag_disabled(self):
        """WORLD_GRAPH_ENABLED=false 时不构建。"""
        from app.config import settings
        session = _make_session_runtime()
        original = settings.world_graph_enabled
        try:
            settings.__dict__["world_graph_enabled"] = False
            session._build_world_graph()
        finally:
            settings.__dict__["world_graph_enabled"] = original

        assert session.world_graph is None
        assert session._behavior_engine is None
        assert not session._world_graph_failed

    def test_no_world_no_build(self):
        """world=None 时不构建。"""
        session = SessionRuntime(world_id="w", session_id="s", world=None)
        session._build_world_graph()

        assert session.world_graph is None


# =============================================================================
# C7b/C8: BehaviorEngine 集成 (4 tests)
# =============================================================================


class TestC7bBehaviorEngine:
    """C7b/C8: BehaviorEngine 作为唯一事件系统。"""

    def test_tick_context_from_session(self):
        """build_tick_context 字段正确映射 SessionRuntime 状态。"""
        session = _make_session_runtime(with_party=True)
        wg = _build_world_graph(session)
        session.world_graph = wg

        ctx = session.build_tick_context("pre")
        assert ctx is not None
        assert ctx.phase == "pre"
        assert ctx.player_location == "town_square"
        assert ctx.game_day == 1
        assert ctx.game_hour == 10
        assert ctx.active_chapter == "ch_01"
        assert "npc_smith" in ctx.party_members
        assert ctx.events_triggered == []
        assert ctx.round_count == 0

    def test_tick_context_none_when_no_graph(self):
        """无 WorldGraph 时返回 None。"""
        session = _make_session_runtime()
        assert session.world_graph is None
        assert session.build_tick_context("pre") is None

    def test_tick_context_none_when_failed(self):
        """_world_graph_failed 时返回 None。"""
        session = _make_session_runtime()
        wg = _build_world_graph(session)
        session.world_graph = wg
        session._world_graph_failed = True
        assert session.build_tick_context("pre") is None

    def test_pre_tick_runs_behavior_engine(self):
        """BehaviorEngine.tick(pre) 成功执行并返回 TickResult。"""
        session = _make_session_runtime()
        wg = _build_world_graph(session)
        session.world_graph = wg
        from app.world.behavior_engine import BehaviorEngine
        engine = BehaviorEngine(wg)
        session._behavior_engine = engine

        ctx = session.build_tick_context("pre")
        result = engine.tick(ctx)

        assert isinstance(result, TickResult)
        assert isinstance(result.results, list)
        assert isinstance(result.narrative_hints, list)
        assert isinstance(result.state_changes, dict)


# =============================================================================
# C7c/C8: 工具 WorldGraph 操作 (2 tests)
# =============================================================================


class TestC7cToolWorldGraph:
    """C7c/C8: 工具直接操作 WorldGraph（非双写）。"""

    def test_navigate_updates_world_graph(self):
        """navigate 成功后 WorldGraph area 节点 visited=True。"""
        session = _make_session_runtime()
        wg = _build_world_graph(session)
        session.world_graph = wg
        from app.world.behavior_engine import BehaviorEngine
        session._behavior_engine = BehaviorEngine(wg)

        dark_forest = wg.get_node("dark_forest")
        assert dark_forest.state.get("visited") is False
        assert dark_forest.state.get("visit_count") == 0

        target_area_id = "dark_forest"
        wg.merge_state(target_area_id, {"visited": True})
        old_count = wg.get_node(target_area_id).state.get("visit_count", 0)
        wg.set_state(target_area_id, "visit_count", old_count + 1)

        assert wg.get_node("dark_forest").state["visited"] is True
        assert wg.get_node("dark_forest").state["visit_count"] == 1

    def test_world_graph_event_status_mutation(self):
        """WorldGraph event 状态可直接通过 merge_state 修改。"""
        session = _make_session_runtime()
        wg = _build_world_graph(session)

        node = wg.get_node("evt_arrive")
        assert node.state["status"] == EventStatus.LOCKED

        wg.merge_state("evt_arrive", {"status": "active"})
        assert wg.get_node("evt_arrive").state["status"] == "active"

        wg.merge_state("evt_arrive", {"status": "completed"})
        assert wg.get_node("evt_arrive").state["status"] == "completed"


# =============================================================================
# C7/C8 配置集成测试
# =============================================================================


class TestC8Config:
    """测试 C8 配置（dual_write 已移除）。"""

    def test_config_has_world_graph_enabled(self):
        """验证 Settings 拥有 world_graph_enabled 字段。"""
        from app.config import settings
        assert hasattr(settings, "world_graph_enabled")
        assert isinstance(settings.world_graph_enabled, bool)

    def test_config_no_dual_write(self):
        """验证 dual_write 配置已移除。"""
        from app.config import settings
        assert not hasattr(settings, "world_graph_dual_write")


# =============================================================================
# U2: Player 入图集成测试
# =============================================================================


def _make_player_character():
    """创建测试用 PlayerCharacter。"""
    from app.models.player_character import PlayerCharacter, CharacterRace, CharacterClass
    return PlayerCharacter(
        name="TestPlayer",
        race=CharacterRace.HUMAN,
        character_class=CharacterClass.FIGHTER,
        background="soldier",
        level=3,
        xp=500,
        abilities={"str": 16, "dex": 14, "con": 12, "int": 10, "wis": 8, "cha": 13},
        max_hp=30,
        current_hp=25,
        ac=15,
        initiative_bonus=2,
        gold=50,
        spell_slots={1: 2},
        inventory=[{"item_id": "sword", "name": "Iron Sword", "quantity": 1}],
    )


def _make_session_with_player(world=None, with_party=False) -> SessionRuntime:
    """创建带 PlayerCharacter 的 SessionRuntime。"""
    session = _make_session_runtime(world=world, with_party=with_party)
    session._player_character = _make_player_character()
    return session


class TestU2PlayerNodeInGraph:
    """U2: Player 节点在 WorldGraph 中的构建与集成。"""

    def test_player_node_exists(self):
        """GraphBuilder 构建后存在 player 节点且类型正确。"""
        session = _make_session_with_player()
        wg = _build_world_graph(session)

        node = wg.get_node("player")
        assert node is not None
        assert node.type == WorldNodeType.PLAYER.value
        assert node.name == "TestPlayer"

    def test_player_member_of_camp(self):
        """player → camp MEMBER_OF 边存在。"""
        session = _make_session_with_player()
        wg = _build_world_graph(session)

        edges = wg.get_edges_between("player", "camp")
        member_edges = [(k, d) for k, d in edges if d.get("relation") == "member_of"]
        assert len(member_edges) >= 1

    def test_player_hosted_at_location(self):
        """location → player HOSTS 边存在。"""
        session = _make_session_with_player()
        wg = _build_world_graph(session)

        # player_location = "town_square"
        edges = wg.get_edges_between("town_square", "player")
        hosts_edges = [(k, d) for k, d in edges if d.get("relation") == "hosts"]
        assert len(hosts_edges) >= 1

    def test_player_state_from_character(self):
        """player 节点 state 从 PlayerCharacter 正确翻译。"""
        session = _make_session_with_player()
        wg = _build_world_graph(session)

        node = wg.get_node("player")
        assert node.state["hp"] == 25  # current_hp → hp
        assert node.state["max_hp"] == 30
        assert node.state["level"] == 3
        assert node.state["xp"] == 500
        assert node.state["ac"] == 15
        assert node.state["gold"] == 50
        assert node.state["spell_slots_max"] == {"1": 2}

    def test_player_properties(self):
        """player 节点 properties 包含身份字段。"""
        session = _make_session_with_player()
        wg = _build_world_graph(session)

        node = wg.get_node("player")
        assert node.properties["race"] == "human"
        assert node.properties["character_class"] == "fighter"
        assert node.properties["background"] == "soldier"

    def test_no_player_without_character(self):
        """无 PlayerCharacter 时不创建 player 节点。"""
        session = _make_session_runtime()
        wg = _build_world_graph(session)

        assert wg.get_node("player") is None


class TestU2PlayerNodeSnapshot:
    """U2: Player 节点快照捕获/恢复。"""

    def test_snapshot_captures_player(self):
        """snapshot 包含 player 节点 state。"""
        session = _make_session_with_player()
        wg = _build_world_graph(session)

        # 修改 player state
        wg.merge_state("player", {"hp": 15, "gold": 200})

        snap = capture_snapshot(wg, "test_world", "s_01", game_day=1, game_hour=10)
        assert "player" in snap.node_states
        assert snap.node_states["player"]["hp"] == 15
        assert snap.node_states["player"]["gold"] == 200

    def test_snapshot_restore_player(self):
        """snapshot 恢复后 player 节点 state 正确。"""
        session = _make_session_with_player()
        wg = _build_world_graph(session)

        wg.merge_state("player", {"hp": 10, "xp": 999})
        snap = capture_snapshot(wg, "test_world", "s_01")
        data = snapshot_to_dict(snap)

        # 新构建 → 恢复
        wg2 = _build_world_graph(session)
        snap2 = dict_to_snapshot(data)
        restore_snapshot(wg2, snap2)

        node = wg2.get_node("player")
        assert node.state["hp"] == 10
        assert node.state["xp"] == 999


class TestU2SessionPlayerProperty:
    """U2: SessionRuntime.player property 行为。"""

    def test_player_returns_view_when_graph_available(self):
        """有 WorldGraph 时 session.player 返回 PlayerNodeView。"""
        from app.world.player_node import PlayerNodeView

        session = _make_session_with_player()
        session._build_world_graph()

        assert session.world_graph is not None
        player = session.player
        assert isinstance(player, PlayerNodeView)
        assert player.name == "TestPlayer"
        assert player.current_hp == 25

    def test_player_returns_character_when_no_graph(self):
        """无 WorldGraph 时 session.player 降级返回 _player_character。"""
        from app.models.player_character import PlayerCharacter

        session = _make_session_with_player()
        # 不构建 world_graph

        player = session.player
        assert isinstance(player, PlayerCharacter)
        assert player.name == "TestPlayer"

    def test_player_view_writes_to_graph(self):
        """通过 PlayerNodeView 修改的值反映在图节点中。"""
        session = _make_session_with_player()
        session._build_world_graph()

        session.player.current_hp = 5
        session.player.gold = 300

        node = session.world_graph.get_node("player")
        assert node.state["hp"] == 5
        assert node.state["gold"] == 300

    def test_mark_player_dirty_marks_graph(self):
        """mark_player_dirty() 同时标记 wg._dirty_nodes。"""
        session = _make_session_with_player()
        session._build_world_graph()

        assert "player" not in session.world_graph._dirty_nodes
        session.mark_player_dirty()
        assert "player" in session.world_graph._dirty_nodes
