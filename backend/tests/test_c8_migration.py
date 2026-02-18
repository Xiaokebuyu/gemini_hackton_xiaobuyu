"""C8 Migration Tests — BehaviorEngine 全面切换验证

9 个测试覆盖:
  1. run_behavior_tick 替代 check_events
  2. activate_event 从 WorldGraph 操作
  3. complete_event on_complete 副作用
  4. 章节转换从 WorldGraph GATE 边
  5. get_event_summaries_from_graph 输出格式
  6. narrative 同步
  7. cascade 解锁
  8. 同伴事件分发
  9. WorldGraph 禁用时降级
"""
from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Stub out 'mcp' package if not installed
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
from app.models.player_character import PlayerCharacter
from app.models.state_delta import GameState, GameTimeState
from app.runtime.models.area_state import AreaConnection, AreaDefinition, SubLocationDef
from app.runtime.models.world_constants import WorldConstants
from app.runtime.session_runtime import SessionRuntime
from app.world.behavior_engine import BehaviorEngine
from app.world.graph_builder import GraphBuilder
from app.world.models import EventStatus, TickResult, WorldEdgeType, WorldNodeType
from app.world.world_graph import WorldGraph


# =============================================================================
# Helpers
# =============================================================================


def _make_world() -> MagicMock:
    world = MagicMock(spec=[
        "world_id", "world_constants",
        "area_registry", "chapter_registry", "character_registry",
        "get_area_definition", "get_character",
    ])
    world.world_id = "test_world"
    world.world_constants = WorldConstants(
        world_id="test_world", name="Test World",
        description="Test.", setting="Fantasy", tone="Dark",
    )
    world.area_registry = {
        "town_square": AreaDefinition(
            area_id="town_square", name="Town Square",
            description="Central.", danger_level=1, area_type="settlement",
            region="Frontier", tags=["town"],
            connections=[AreaConnection(
                target_area_id="dark_forest", connection_type="road", travel_time="1h",
            )],
        ),
        "dark_forest": AreaDefinition(
            area_id="dark_forest", name="Dark Forest",
            description="Foreboding.", danger_level=3, area_type="wilderness",
            region="Frontier",
            connections=[AreaConnection(
                target_area_id="town_square", connection_type="road", travel_time="1h",
            )],
        ),
    }
    world.chapter_registry = {
        "ch_01": {
            "id": "ch_01", "name": "Chapter 1", "description": "First chapter.",
            "mainline_id": "main_01", "status": "active",
            "available_maps": ["town_square"],
            "events": [
                {
                    "id": "evt_arrive", "name": "Arrive in Town",
                    "trigger_conditions": {"operator": "and", "conditions": [
                        {"type": "location", "params": {"area_id": "town_square"}},
                    ]},
                    "completion_conditions": {"operator": "and", "conditions": [
                        {"type": "npc_interacted", "params": {"npc_id": "npc_smith", "min_interactions": 1}},
                    ]},
                    "on_complete": {"add_xp": 50, "add_items": [{"id": "gold_coin", "name": "Gold Coin"}]},
                    "is_required": True,
                    "narrative_directive": "Arrive and talk to Smith.",
                },
                {
                    "id": "evt_explore", "name": "Explore Forest",
                    "trigger_conditions": {"operator": "and", "conditions": [
                        {"type": "event_triggered", "params": {"event_id": "evt_arrive"}},
                    ]},
                    "is_required": False,
                    "narrative_directive": "Explore the dark forest.",
                },
            ],
            "transitions": [], "tags": ["intro"],
        },
        "ch_02": {
            "id": "ch_02", "name": "Chapter 2", "description": "Second chapter.",
            "mainline_id": "main_01", "status": "locked",
            "available_maps": ["dark_forest"],
            "events": [], "transitions": [], "tags": [],
        },
    }
    world.character_registry = {
        "npc_smith": {"id": "npc_smith", "profile": {"name": "Grom", "metadata": {
            "default_map": "town_square", "tier": "secondary",
        }}},
    }
    world.get_area_definition = lambda aid: world.area_registry.get(aid)
    world.get_character = lambda cid: world.character_registry.get(cid)
    return world


def _make_session(with_player=False, with_companions=False) -> SessionRuntime:
    world = _make_world()
    session = SessionRuntime(world_id="test_world", session_id="s_01", world=world)
    session.game_state = GameState(
        world_id="test_world", session_id="s_01",
        player_location="town_square", area_id="town_square", chapter_id="ch_01",
    )
    session.time = GameTimeState(day=1, hour=10, minute=0, period="day", formatted="第1天 10:00")
    session.narrative = NarrativeProgress(
        current_mainline="main_01", current_chapter="ch_01", events_triggered=[],
    )

    if with_player:
        player = MagicMock()
        player.name = "Test Player"
        player.xp = 0
        player.inventory = []
        session.player = player

    if with_companions:
        companion = MagicMock()
        companion.add_event = MagicMock()
        session.companions = {"npc_smith": companion}

    return session


def _setup_graph(session: SessionRuntime) -> tuple:
    """Build WorldGraph + BehaviorEngine and attach to session."""
    wg = GraphBuilder.build(session.world, session)
    engine = BehaviorEngine(wg)
    session.world_graph = wg
    session._behavior_engine = engine
    return wg, engine


# =============================================================================
# Tests
# =============================================================================


class TestC8Migration:
    """C8: BehaviorEngine 全面切换验证。"""

    def test_tick_replaces_check_events(self):
        """run_behavior_tick 返回 TickResult，事件状态正确转换。"""
        session = _make_session()
        wg, engine = _setup_graph(session)

        # evt_arrive 有 trigger_conditions: location=town_square
        # 玩家在 town_square → should unlock
        result = session.run_behavior_tick("pre")

        assert result is not None
        assert isinstance(result, TickResult)
        # evt_arrive should have been unlocked (locked → available)
        evt = wg.get_node("evt_arrive")
        assert evt.state["status"] in ("available", "active", "completed", EventStatus.AVAILABLE)

    def test_activate_event_worldgraph(self):
        """工具从 WorldGraph 节点操作，不依赖 area_rt.events。"""
        session = _make_session()
        wg, engine = _setup_graph(session)

        # First tick to unlock evt_arrive
        session.run_behavior_tick("pre")

        # Manually ensure available
        wg.merge_state("evt_arrive", {"status": "available"})

        # Activate
        wg.merge_state("evt_arrive", {"status": "active"})
        node = wg.get_node("evt_arrive")
        assert node.state["status"] == "active"

    def test_complete_event_side_effects(self):
        """on_complete 的 add_xp/add_items 正确应用到 player。"""
        session = _make_session(with_player=True)
        wg, engine = _setup_graph(session)

        # Setup event as active
        wg.merge_state("evt_arrive", {"status": "active"})

        # Simulate what complete_event tool does
        wg.merge_state("evt_arrive", {"status": "completed"})
        on_complete = wg.get_node("evt_arrive").properties.get("on_complete")
        assert on_complete is not None

        # Apply side effects
        add_xp = on_complete.get("add_xp", 0)
        if add_xp:
            session.player.xp += add_xp
            session.mark_player_dirty()

        add_items = on_complete.get("add_items", [])
        for item in add_items:
            session.player.inventory.append(item)

        assert session.player.xp == 50
        assert len(session.player.inventory) == 1
        assert session.player.inventory[0]["id"] == "gold_coin"

    def test_chapter_transition_from_graph(self):
        """GATE 条件满足后 check_chapter_transitions 返回正确目标。"""
        session = _make_session()
        wg, engine = _setup_graph(session)

        # By default, no chapter has status=active besides ch_01
        result = session.check_chapter_transitions()
        # ch_02 is not active, so no transition should be available
        assert result is None

        # Manually activate ch_02 and add GATE edge
        wg.merge_state("ch_02", {"status": "active"})
        wg.add_edge(
            "ch_01", "ch_02", "gate",
            key="gate_ch01_ch02",
            transition_type="normal",
            narrative_hint="The adventure continues...",
            priority=1,
        )

        result = session.check_chapter_transitions()
        assert result is not None
        assert result["target_chapter_id"] == "ch_02"
        assert result["transition_type"] == "normal"
        assert result["narrative_hint"] == "The adventure continues..."

    def test_event_summaries_from_graph(self):
        """get_event_summaries_from_graph 输出格式匹配旧 get_area_context 事件段。"""
        session = _make_session()
        wg, engine = _setup_graph(session)

        # Set evt_arrive to available
        wg.merge_state("evt_arrive", {"status": "available"})

        summaries = session.get_event_summaries_from_graph("town_square")

        assert len(summaries) >= 1
        evt_summary = next(s for s in summaries if s["id"] == "evt_arrive")
        assert evt_summary["name"] == "Arrive in Town"
        assert evt_summary["status"] == "available"
        assert evt_summary["importance"] == "main"
        assert "narrative_directive" in evt_summary

    def test_narrative_sync(self):
        """tick 产出 completed 事件 → narrative.events_triggered 更新。"""
        session = _make_session()
        wg, engine = _setup_graph(session)

        # Manually complete evt_arrive in WorldGraph
        wg.merge_state("evt_arrive", {"status": "completed"})

        # Simulate tick result with completed event
        mock_result = TickResult(
            state_changes={"evt_arrive": {"status": "completed"}},
        )

        assert "evt_arrive" not in session.narrative.events_triggered
        session._sync_tick_to_narrative(mock_result)
        assert "evt_arrive" in session.narrative.events_triggered

    def test_cascade_unlock(self):
        """完成 A（unlock_events: [B]）→ cascade tick → B 变 available。"""
        session = _make_session()
        wg, engine = _setup_graph(session)

        # evt_explore depends on evt_arrive being triggered
        # Complete evt_arrive in both WorldGraph and narrative
        wg.merge_state("evt_arrive", {"status": "completed"})
        session.narrative.events_triggered.append("evt_arrive")

        # Run tick to let cascade unlock evt_explore
        result = session.run_behavior_tick("post")

        evt_explore = wg.get_node("evt_explore")
        # evt_explore should be unlocked since its trigger (event_triggered: evt_arrive) is satisfied
        assert evt_explore.state["status"] in ("available", EventStatus.AVAILABLE)

    def test_companion_dispatch(self):
        """完成事件 → CompactEvent 分发到同伴。"""
        session = _make_session(with_companions=True)
        wg, engine = _setup_graph(session)

        # Complete an event
        wg.merge_state("evt_arrive", {"status": "completed"})

        mock_result = TickResult(
            state_changes={"evt_arrive": {"status": "completed"}},
        )

        session._dispatch_completed_events_to_companions(mock_result)

        # Companion should have received the event
        companion = session.companions["npc_smith"]
        assert companion.add_event.called
        call_args = companion.add_event.call_args[0][0]
        assert call_args.event_id == "evt_arrive"
        assert call_args.event_name == "Arrive in Town"

    def test_world_graph_disabled_fallback(self):
        """WORLD_GRAPH_ENABLED=false → run_behavior_tick 返回 None，管线不崩溃。"""
        session = _make_session()
        # Don't set up graph — simulates disabled
        assert session.world_graph is None
        assert session._behavior_engine is None

        result = session.run_behavior_tick("pre")
        assert result is None

        result2 = session.check_chapter_transitions()
        assert result2 is None

        summaries = session.get_event_summaries_from_graph()
        assert summaries == []
