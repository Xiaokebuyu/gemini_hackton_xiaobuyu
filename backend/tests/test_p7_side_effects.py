"""P7 Integration Tests — 副作用 6 类型全覆盖 + 去重修复

测试:
  路径 A（自动管线）: BehaviorEngine tick → WorldEvent → _apply_tick_side_effects (7 tests)
  路径 B（手动工具）: _apply_on_complete_from_graph (3 tests)
  graph_builder: EMIT_EVENT 动作生成 (2 tests)
"""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock

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
from app.models.player_character import CharacterClass, CharacterRace, PlayerCharacter
from app.models.state_delta import GameState, GameTimeState
from app.runtime.models.area_state import AreaConnection, AreaDefinition, SubLocationDef
from app.runtime.models.world_constants import WorldConstants
from app.runtime.session_runtime import SessionRuntime
from app.world.behavior_engine import BehaviorEngine
from app.world.graph_builder import GraphBuilder, _event_to_behaviors
from app.world.models import (
    Action,
    ActionType,
    EventStatus,
    TickContext,
    TickResult,
    WorldEvent,
    WorldNodeType,
)
from app.world.world_graph import WorldGraph


# =============================================================================
# Fixtures
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


def _make_chapter_registry(*, on_complete: dict | None = None) -> dict:
    """构建章节注册表，支持自定义 on_complete。"""
    default_on_complete = {"add_xp": 100}
    return {
        "ch_01": {
            "id": "ch_01",
            "name": "Chapter 1",
            "description": "The first chapter.",
            "mainline_id": "main_01",
            "status": "active",
            "available_maps": ["town_square", "dark_forest"],
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
                    "completion_conditions": {
                        "operator": "and",
                        "conditions": [
                            {"type": "npc_interacted", "params": {"npc_id": "npc_smith", "min_interactions": 1}},
                        ],
                    },
                    "is_required": True,
                    "narrative_directive": "Complete the test event.",
                    "on_complete": on_complete or default_on_complete,
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


def _make_world(on_complete: dict | None = None) -> MagicMock:
    world = MagicMock(spec=[
        "world_id", "world_constants",
        "area_registry", "chapter_registry", "character_registry",
        "get_area_definition", "get_character",
    ])
    world.world_id = "test_world"
    world.world_constants = _make_world_constants()
    world.area_registry = _make_area_registry()
    world.chapter_registry = _make_chapter_registry(on_complete=on_complete)
    world.character_registry = _make_character_registry()
    world.get_area_definition = lambda aid: world.area_registry.get(aid)
    world.get_character = lambda cid: world.character_registry.get(cid)
    return world


def _make_player_character():
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


def _make_session_runtime(on_complete: dict | None = None) -> SessionRuntime:
    world = _make_world(on_complete=on_complete)
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
    session._player_character = _make_player_character()
    return session


def _build_graph_and_engine(session: SessionRuntime):
    """构建 WorldGraph + BehaviorEngine 并挂载到 session。"""
    wg = GraphBuilder.build(session.world, session)
    session.world_graph = wg
    engine = BehaviorEngine(wg)
    session._behavior_engine = engine
    return wg, engine


def _force_complete_event(wg: WorldGraph, engine: BehaviorEngine, session: SessionRuntime) -> TickResult:
    """手动将 evt_test 推进到 COMPLETED 并跑 tick 以产出 WorldEvent。"""
    # 先解锁
    evt = wg.get_node("evt_test")
    wg.merge_state("evt_test", {"status": EventStatus.AVAILABLE})
    wg.merge_state("evt_test", {"status": EventStatus.ACTIVE})

    # 构造满足 completion_conditions 的 context
    ctx = session.build_tick_context("post")
    ctx.npc_interactions = {"npc_smith": 1}

    tick_result = engine.tick(ctx)
    return tick_result


# =============================================================================
# graph_builder 测试 (2 tests)
# =============================================================================


class TestGraphBuilderEmitEvents:
    """graph_builder: EMIT_EVENT 动作生成。"""

    def test_builder_emits_gold_event(self):
        """on_complete.add_gold → EMIT_EVENT action 生成。"""
        from app.models.narrative import StoryEvent

        event = StoryEvent(
            id="evt_gold",
            name="Gold Event",
            trigger_conditions={
                "operator": "and",
                "conditions": [{"type": "location", "params": {"area_id": "town"}}],
            },
            completion_conditions={
                "operator": "and",
                "conditions": [{"type": "npc_interacted", "params": {"npc_id": "x", "min_interactions": 1}}],
            },
            on_complete={"add_gold": 200},
        )

        behaviors = _event_to_behaviors(event, "evt_gold", "ch_01")
        # 找到 complete behavior
        complete_bh = [b for b in behaviors if b.id.startswith("bh_complete_")]
        assert len(complete_bh) == 1

        actions = complete_bh[0].actions
        gold_actions = [a for a in actions if a.type == ActionType.EMIT_EVENT and a.params.get("event_type") == "gold_awarded"]
        assert len(gold_actions) == 1
        assert gold_actions[0].params["data"]["amount"] == 200

    def test_builder_emits_reputation_and_flags(self):
        """reputation_changes + world_flags → 多个 EMIT_EVENT。"""
        from app.models.narrative import StoryEvent

        event = StoryEvent(
            id="evt_rep_flag",
            name="Rep+Flag Event",
            trigger_conditions={
                "operator": "and",
                "conditions": [{"type": "location", "params": {"area_id": "town"}}],
            },
            completion_conditions={
                "operator": "and",
                "conditions": [{"type": "npc_interacted", "params": {"npc_id": "x", "min_interactions": 1}}],
            },
            on_complete={
                "reputation_changes": {"guild_a": 10, "guild_b": -5},
                "world_flags": {"portal_opened": True, "boss_defeated": True},
            },
        )

        behaviors = _event_to_behaviors(event, "evt_rep_flag", "ch_01")
        complete_bh = [b for b in behaviors if b.id.startswith("bh_complete_")]
        assert len(complete_bh) == 1

        actions = complete_bh[0].actions
        rep_actions = [a for a in actions if a.type == ActionType.EMIT_EVENT and a.params.get("event_type") == "reputation_changed"]
        flag_actions = [a for a in actions if a.type == ActionType.EMIT_EVENT and a.params.get("event_type") == "world_flag_set"]

        assert len(rep_actions) == 2
        assert len(flag_actions) == 2

        # 验证具体数据
        rep_data = {a.params["data"]["faction"]: a.params["data"]["delta"] for a in rep_actions}
        assert rep_data == {"guild_a": 10, "guild_b": -5}

        flag_data = {a.params["data"]["key"]: a.params["data"]["value"] for a in flag_actions}
        assert flag_data == {"portal_opened": True, "boss_defeated": True}


# =============================================================================
# 路径 A 测试（自动管线）(7 tests)
# =============================================================================


class TestPathASideEffects:
    """路径 A: BehaviorEngine tick → WorldEvent → _apply_tick_side_effects。"""

    def test_gold_awarded_via_tick(self):
        """on_complete.add_gold → player.gold 增加。"""
        session = _make_session_runtime(on_complete={"add_gold": 100})
        wg, engine = _build_graph_and_engine(session)

        initial_gold = session.player.gold
        tick_result = _force_complete_event(wg, engine, session)
        session._apply_tick_side_effects(tick_result)

        assert session.player.gold == initial_gold + 100

    def test_reputation_changed_via_tick(self):
        """on_complete.reputation_changes → world_root.faction_reputations 更新。"""
        session = _make_session_runtime(on_complete={"reputation_changes": {"adventurers_guild": 15}})
        wg, engine = _build_graph_and_engine(session)

        tick_result = _force_complete_event(wg, engine, session)
        session._apply_tick_side_effects(tick_result)

        root = wg.get_node("world_root")
        reps = root.state.get("faction_reputations", {})
        assert reps.get("adventurers_guild") == 15

    def test_world_flag_set_via_tick(self):
        """on_complete.world_flags → world_root.world_flags 更新。"""
        session = _make_session_runtime(on_complete={"world_flags": {"dragon_awakened": True}})
        wg, engine = _build_graph_and_engine(session)

        tick_result = _force_complete_event(wg, engine, session)
        session._apply_tick_side_effects(tick_result)

        root = wg.get_node("world_root")
        flags = root.state.get("world_flags", {})
        assert flags.get("dragon_awakened") is True

    def test_multiple_side_effects_combined(self):
        """一个事件同时产生 xp+gold+reputation+flag。"""
        on_complete = {
            "add_xp": 50,
            "add_gold": 200,
            "reputation_changes": {"town_guard": 5},
            "world_flags": {"quest_done": True},
        }
        session = _make_session_runtime(on_complete=on_complete)
        wg, engine = _build_graph_and_engine(session)

        initial_xp = session.player.xp
        initial_gold = session.player.gold

        tick_result = _force_complete_event(wg, engine, session)
        session._apply_tick_side_effects(tick_result)

        assert session.player.xp == initial_xp + 50
        assert session.player.gold == initial_gold + 200

        root = wg.get_node("world_root")
        assert root.state.get("faction_reputations", {}).get("town_guard") == 5
        assert root.state.get("world_flags", {}).get("quest_done") is True

    def test_dedup_prevents_double_apply(self):
        """路径 B 先标记 → 路径 A 跳过（blanket_key 去重）。"""
        session = _make_session_runtime(on_complete={"add_gold": 100})
        wg, engine = _build_graph_and_engine(session)

        # 模拟路径 B 已标记（blanket_key 格式）
        session._applied_side_effect_events.add("gold_awarded:evt_test")

        initial_gold = session.player.gold
        tick_result = _force_complete_event(wg, engine, session)
        session._apply_tick_side_effects(tick_result)

        # 不应再次发放
        assert session.player.gold == initial_gold

    def test_multi_instance_not_deduped(self):
        """同源 3 个物品全部发放（验证去重修复）。"""
        items = [
            {"item_id": "potion", "name": "Health Potion", "quantity": 1},
            {"item_id": "scroll", "name": "Magic Scroll", "quantity": 1},
            {"item_id": "gem", "name": "Ruby Gem", "quantity": 1},
        ]
        session = _make_session_runtime(on_complete={"add_items": items})
        wg, engine = _build_graph_and_engine(session)

        initial_inventory_len = len(session.player.inventory)

        tick_result = _force_complete_event(wg, engine, session)
        session._apply_tick_side_effects(tick_result)

        # 3 个物品全部发放
        assert len(session.player.inventory) == initial_inventory_len + 3

    def test_side_effects_without_world_graph(self):
        """无图时退化不崩溃。"""
        session = _make_session_runtime(on_complete={"add_gold": 100, "reputation_changes": {"guild": 10}})
        # 不构建图
        session.world_graph = None

        # 构造一个包含 gold_awarded 和 reputation_changed 事件的 mock TickResult
        gold_event = WorldEvent(
            event_type="gold_awarded",
            origin_node="evt_test",
            data={"amount": 100},
        )
        rep_event = WorldEvent(
            event_type="reputation_changed",
            origin_node="evt_test",
            data={"faction": "guild", "delta": 10},
        )
        tick_result = MagicMock()
        tick_result.all_events = [gold_event, rep_event]
        tick_result.state_changes = {}

        initial_gold = session.player.gold
        session._apply_tick_side_effects(tick_result)

        # gold 应正常发放（不依赖图）
        assert session.player.gold == initial_gold + 100
        # reputation 跳过（无图 → guard 跳过）


# =============================================================================
# 路径 B 测试（手动工具）(3 tests)
# =============================================================================


class TestPathBManualComplete:
    """路径 B: _apply_on_complete_from_graph 手动完成事件。"""

    def test_manual_complete_gold(self):
        """_apply_on_complete_from_graph 处理 add_gold。"""
        session = _make_session_runtime()
        wg, engine = _build_graph_and_engine(session)

        initial_gold = session.player.gold
        on_complete = {"add_gold": 300}
        node = wg.get_node("evt_test")

        session._apply_on_complete_from_graph(on_complete, "evt_test", node)

        assert session.player.gold == initial_gold + 300
        assert "gold_awarded:evt_test" in session._applied_side_effect_events

    def test_manual_complete_reputation(self):
        """_apply_on_complete_from_graph 处理 reputation_changes。"""
        session = _make_session_runtime()
        wg, engine = _build_graph_and_engine(session)

        on_complete = {"reputation_changes": {"thieves_guild": -10, "merchants": 5}}
        node = wg.get_node("evt_test")

        session._apply_on_complete_from_graph(on_complete, "evt_test", node)

        root = wg.get_node("world_root")
        reps = root.state.get("faction_reputations", {})
        assert reps.get("thieves_guild") == -10
        assert reps.get("merchants") == 5
        assert "reputation_changed:evt_test" in session._applied_side_effect_events

    def test_manual_complete_world_flags(self):
        """_apply_on_complete_from_graph 处理 world_flags。"""
        session = _make_session_runtime()
        wg, engine = _build_graph_and_engine(session)

        on_complete = {"world_flags": {"bridge_repaired": True, "tax_rate": 0.15}}
        node = wg.get_node("evt_test")

        session._apply_on_complete_from_graph(on_complete, "evt_test", node)

        root = wg.get_node("world_root")
        flags = root.state.get("world_flags", {})
        assert flags.get("bridge_repaired") is True
        assert flags.get("tax_rate") == 0.15
        assert "world_flag_set:evt_test" in session._applied_side_effect_events
