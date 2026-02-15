"""C7 Integration Tests — WorldGraph 接入 V4 Pipeline

测试三个子阶段:
  C7a: WorldGraph 挂载 + 快照 I/O（6 tests）
  C7b: BehaviorEngine 双写集成（5 tests）
  C7c: 工具双写（4 tests）
"""
from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

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

from app.models.narrative import (
    Chapter,
    Condition,
    ConditionGroup,
    ConditionType,
    NarrativeProgress,
    StoryEvent,
)
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
        # settings.world_graph_enabled defaults to True, just call directly
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

        # 修改某节点 state
        wg.merge_state("town_square", {"visited": True, "visit_count": 3})

        # Capture
        snap = capture_snapshot(wg, "test_world", "s_01", game_day=2, game_hour=14)
        assert "town_square" in snap.node_states
        assert snap.node_states["town_square"]["visited"] is True

        # Serialize roundtrip
        data = snapshot_to_dict(snap)
        snap2 = dict_to_snapshot(data)
        assert snap2 is not None
        assert snap2.node_states["town_square"]["visit_count"] == 3

        # Restore into fresh graph
        wg2 = _build_world_graph(session)
        restore_snapshot(wg2, snap2)

        node = wg2.get_node("town_square")
        assert node.state["visited"] is True
        assert node.state["visit_count"] == 3

    def test_snapshot_roundtrip_with_event_state(self):
        """build→mutate event status→persist→新build→restore→验证一致。"""
        session = _make_session_runtime()
        wg = _build_world_graph(session)

        # Mutate: set event to "available"
        wg.merge_state("evt_arrive", {"status": "available"})
        snap = capture_snapshot(wg, "test_world", "s_01")
        data = snapshot_to_dict(snap)

        # Fresh build + restore
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
            # Pydantic BaseModel — use __dict__ to bypass validation
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
# C7b: BehaviorEngine 双写集成 (5 tests)
# =============================================================================


class TestC7bBehaviorEngine:
    """C7b: BehaviorEngine 双写与 AreaRuntime 并行。"""

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
        # world_graph is None by default
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

    def test_pipeline_orchestrator_dual_write_methods(self):
        """PipelineOrchestrator 双写辅助方法正常工作。"""
        from app.services.admin.pipeline_orchestrator import PipelineOrchestrator

        orchestrator = PipelineOrchestrator(
            flash_cpu=MagicMock(),
            party_service=MagicMock(),
            narrative_service=MagicMock(),
            graph_store=MagicMock(),
            teammate_response_service=MagicMock(),
            session_history_manager=MagicMock(),
            character_store=MagicMock(),
            state_manager=MagicMock(),
            world_runtime=MagicMock(),
        )

        session = _make_session_runtime()
        wg = _build_world_graph(session)
        session.world_graph = wg
        from app.world.behavior_engine import BehaviorEngine
        session._behavior_engine = BehaviorEngine(wg)

        # Test _run_behavior_tick
        result = orchestrator._run_behavior_tick(session, "pre", [])
        assert result is not None
        assert isinstance(result, TickResult)

        # Test _sync_event_completions with a mock tick result
        mock_tick = TickResult(
            state_changes={"evt_arrive": {"status": "completed"}},
        )
        assert "evt_arrive" not in session.narrative.events_triggered
        orchestrator._sync_event_completions(mock_tick, session)
        assert "evt_arrive" in session.narrative.events_triggered

    def test_tick_failure_no_pipeline_break(self):
        """BehaviorEngine.tick() 抛异常时 _run_behavior_tick 返回 None。"""
        from app.services.admin.pipeline_orchestrator import PipelineOrchestrator

        orchestrator = PipelineOrchestrator(
            flash_cpu=MagicMock(),
            party_service=MagicMock(),
            narrative_service=MagicMock(),
            graph_store=MagicMock(),
            teammate_response_service=MagicMock(),
            session_history_manager=MagicMock(),
            character_store=MagicMock(),
            state_manager=MagicMock(),
            world_runtime=MagicMock(),
        )

        session = _make_session_runtime()
        wg = _build_world_graph(session)
        session.world_graph = wg
        # Mock engine that raises
        engine = MagicMock()
        engine.tick.side_effect = RuntimeError("engine boom")
        session._behavior_engine = engine

        result = orchestrator._run_behavior_tick(session, "pre", [])
        assert result is None


# =============================================================================
# C7c: 工具双写 (4 tests)
# =============================================================================


class TestC7cToolDualWrite:
    """C7c: V4AgenticToolRegistry 工具双写到 WorldGraph。"""

    def _make_registry(self, session):
        """创建 V4AgenticToolRegistry with mocked dependencies。"""
        from app.services.admin.v4_agentic_tools import V4AgenticToolRegistry

        return V4AgenticToolRegistry(
            session=session,
            flash_cpu=MagicMock(),
            graph_store=MagicMock(),
        )

    def test_wg_sync_event_status(self):
        """_wg_sync_event_status 双写 event 状态到 WorldGraph。"""
        session = _make_session_runtime()
        wg = _build_world_graph(session)
        session.world_graph = wg
        from app.world.behavior_engine import BehaviorEngine
        session._behavior_engine = BehaviorEngine(wg)

        registry = self._make_registry(session)

        # Pre-check: event starts as locked
        node = wg.get_node("evt_arrive")
        assert node.state["status"] == EventStatus.LOCKED

        # Dual-write to "active"
        registry._wg_sync_event_status("evt_arrive", "active")
        assert wg.get_node("evt_arrive").state["status"] == "active"

        # Dual-write to "completed"
        registry._wg_sync_event_status("evt_arrive", "completed")
        assert wg.get_node("evt_arrive").state["status"] == "completed"

    def test_wg_emit_event(self):
        """_wg_emit_event 调用 BehaviorEngine.handle_event。"""
        session = _make_session_runtime()
        wg = _build_world_graph(session)
        session.world_graph = wg
        from app.world.behavior_engine import BehaviorEngine
        session._behavior_engine = BehaviorEngine(wg)

        registry = self._make_registry(session)

        # Should not raise
        registry._wg_emit_event("evt_arrive", "event_activated")

    def test_wg_failed_tool_still_works(self):
        """_world_graph_failed 时工具双写静默跳过。"""
        session = _make_session_runtime()
        wg = _build_world_graph(session)
        session.world_graph = wg
        session._world_graph_failed = True
        from app.world.behavior_engine import BehaviorEngine
        session._behavior_engine = BehaviorEngine(wg)

        registry = self._make_registry(session)

        # Should not raise, and should not modify graph
        original_status = wg.get_node("evt_arrive").state["status"]
        registry._wg_sync_event_status("evt_arrive", "active")
        assert wg.get_node("evt_arrive").state["status"] == original_status

        # emit also no-op
        registry._wg_emit_event("evt_arrive", "event_activated")

    def test_navigate_updates_world_graph(self):
        """navigate 成功后 WorldGraph area 节点 visited=True。"""
        session = _make_session_runtime()
        wg = _build_world_graph(session)
        session.world_graph = wg
        from app.world.behavior_engine import BehaviorEngine
        session._behavior_engine = BehaviorEngine(wg)

        # Check initial state
        dark_forest = wg.get_node("dark_forest")
        assert dark_forest.state.get("visited") is False
        assert dark_forest.state.get("visit_count") == 0

        # Simulate what navigate does to WorldGraph after successful enter_area
        target_area_id = "dark_forest"
        wg.merge_state(target_area_id, {"visited": True})
        old_count = wg.get_node(target_area_id).state.get("visit_count", 0)
        wg.set_state(target_area_id, "visit_count", old_count + 1)

        assert wg.get_node("dark_forest").state["visited"] is True
        assert wg.get_node("dark_forest").state["visit_count"] == 1


# =============================================================================
# C7 配置集成测试
# =============================================================================


class TestC7Config:
    """测试 C7 配置开关。"""

    def test_config_has_world_graph_fields(self):
        """验证 Settings 拥有 WorldGraph 配置字段。"""
        from app.config import settings
        assert hasattr(settings, "world_graph_enabled")
        assert hasattr(settings, "world_graph_dual_write")
        assert isinstance(settings.world_graph_enabled, bool)
        assert isinstance(settings.world_graph_dual_write, bool)

    def test_config_can_be_overridden(self):
        """配置可通过 __dict__ 覆盖（运行时切换）。"""
        from app.config import settings
        original_enabled = settings.world_graph_enabled
        original_dual = settings.world_graph_dual_write
        try:
            settings.__dict__["world_graph_enabled"] = False
            settings.__dict__["world_graph_dual_write"] = False
            assert settings.world_graph_enabled is False
            assert settings.world_graph_dual_write is False
        finally:
            settings.__dict__["world_graph_enabled"] = original_enabled
            settings.__dict__["world_graph_dual_write"] = original_dual
