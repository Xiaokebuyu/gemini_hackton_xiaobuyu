"""P1 + P6 Integration Tests — navigate ON_ENTER/ON_EXIT + HOSTS 边 + 战斗 WorldEvent

测试:
  P1: navigate() 集成 ON_ENTER/ON_EXIT + HOSTS 边更新 (8 tests)
  P6: 战斗结束发射 WorldEvent (4 tests)
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
from app.models.state_delta import GameState, GameTimeState
from app.runtime.models.area_state import AreaConnection, AreaDefinition, SubLocationDef
from app.runtime.models.world_constants import WorldConstants
from app.runtime.session_runtime import SessionRuntime
from app.services.admin.v4_agentic_tools import V4AgenticToolRegistry
from app.world.behavior_engine import BehaviorEngine
from app.world.graph_builder import GraphBuilder
from app.world.models import (
    Action,
    ActionType,
    Behavior,
    EventStatus,
    TickContext,
    TickResult,
    TriggerType,
    WorldEdgeType,
    WorldNodeType,
)
from app.world.world_graph import WorldGraph


# =============================================================================
# Fixtures: 复用 test_c7_integration 模式
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
            "available_maps": ["town_square", "dark_forest"],
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


def _make_player_character():
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


def _make_session_runtime(world=None, with_party=False) -> SessionRuntime:
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
    session._player_character = _make_player_character()
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


def _build_graph_and_engine(session: SessionRuntime):
    """构建 WorldGraph + BehaviorEngine 并挂载到 session。"""
    wg = GraphBuilder.build(session.world, session)
    session.world_graph = wg
    engine = BehaviorEngine(wg)
    session._behavior_engine = engine
    return wg, engine


def _make_registry(session: SessionRuntime) -> V4AgenticToolRegistry:
    """创建带 mock flash_cpu 和 graph_store 的 V4AgenticToolRegistry。"""
    flash_cpu = MagicMock()
    graph_store = MagicMock()
    return V4AgenticToolRegistry(
        session=session,
        flash_cpu=flash_cpu,
        graph_store=graph_store,
    )


# =============================================================================
# P1: navigate HOSTS 边更新 (8 tests)
# =============================================================================


class TestP1NavigateHosts:
    """P1: navigate() HOSTS 边 + ON_ENTER/ON_EXIT 行为触发。"""

    def test_navigate_updates_hosts_player(self):
        """导航后 HOSTS 边从旧区域移到新区域。"""
        session = _make_session_runtime()
        wg, engine = _build_graph_and_engine(session)
        registry = _make_registry(session)

        # 确认初始 HOSTS 边
        edges_before = wg.get_edges_between("town_square", "player")
        hosts_before = [k for k, d in edges_before if d.get("relation") == "hosts"]
        assert len(hosts_before) >= 1, "初始 player HOSTS 边应在 town_square"

        # 模拟导航成功
        registry._update_hosts_edges(wg, "town_square", "dark_forest")

        # 验证 HOSTS 边已移动
        edges_old = wg.get_edges_between("town_square", "player")
        hosts_old = [k for k, d in edges_old if d.get("relation") == "hosts"]
        assert len(hosts_old) == 0, "旧区域不应再有 player HOSTS 边"

        edges_new = wg.get_edges_between("dark_forest", "player")
        hosts_new = [k for k, d in edges_new if d.get("relation") == "hosts"]
        assert len(hosts_new) >= 1, "新区域应有 player HOSTS 边"

        # 验证 player node state
        node = wg.get_node("player")
        assert node.state["current_location"] == "dark_forest"

    def test_navigate_updates_hosts_party_npcs(self):
        """队友 HOSTS 边跟随同步。"""
        session = _make_session_runtime(with_party=True)
        wg, engine = _build_graph_and_engine(session)
        registry = _make_registry(session)

        # npc_smith 初始在 town_square（实际可能在子地点）
        npc_node = wg.get_node("npc_smith")
        assert npc_node is not None

        # 执行队友 HOSTS 更新
        registry._update_party_hosts_edges(wg, "dark_forest")

        # 验证 NPC node state
        npc_node = wg.get_node("npc_smith")
        assert npc_node.state["current_location"] == "dark_forest"

        # 验证新区域有 HOSTS 边
        edges_new = wg.get_edges_between("dark_forest", "npc_smith")
        hosts_new = [k for k, d in edges_new if d.get("relation") == "hosts"]
        assert len(hosts_new) >= 1

    def test_navigate_calls_handle_exit_enter(self):
        """ON_EXIT/ON_ENTER 行为触发验证。"""
        session = _make_session_runtime()
        wg, engine = _build_graph_and_engine(session)

        # 在 dark_forest 添加 ON_ENTER behavior
        dark_forest = wg.get_node("dark_forest")
        dark_forest.behaviors.append(Behavior(
            id="enter_forest",
            trigger=TriggerType.ON_ENTER,
            actions=[
                Action(
                    type=ActionType.NARRATIVE_HINT,
                    params={"text": "阴暗的森林散发着危险的气息", "priority": "high"},
                ),
            ],
            once=True,
        ))

        # 手动调用 handle_enter
        ctx = session.build_tick_context("post")
        ctx.player_location = "dark_forest"
        result = engine.handle_enter("player", "dark_forest", ctx)

        assert isinstance(result, TickResult)
        assert len(result.narrative_hints) > 0
        assert "阴暗的森林" in result.narrative_hints[0]

    def test_navigate_narrative_hints_in_result(self):
        """navigate() 返回 dict 中包含 narrative_hints。"""
        async def _run():
            session = _make_session_runtime()
            wg, engine = _build_graph_and_engine(session)
            registry = _make_registry(session)

            # 在 dark_forest 添加 ON_ENTER behavior
            dark_forest = wg.get_node("dark_forest")
            dark_forest.behaviors.append(Behavior(
                id="enter_forest_hint",
                trigger=TriggerType.ON_ENTER,
                actions=[
                    Action(
                        type=ActionType.NARRATIVE_HINT,
                        params={"text": "你踏入了黑暗森林", "priority": "normal"},
                    ),
                ],
                once=True,
            ))

            # Mock enter_area
            session.enter_area = AsyncMock(return_value={"success": True, "area_id": "dark_forest"})

            result = await registry.navigate("dark_forest")

            assert result.get("success") is True
            assert "narrative_hints" in result
            assert any("黑暗森林" in h for h in result["narrative_hints"])

        asyncio.run(_run())

    def test_navigate_first_area_no_old_hosts(self):
        """首次导航（无旧区域）不崩溃。"""
        session = _make_session_runtime()
        wg, engine = _build_graph_and_engine(session)
        registry = _make_registry(session)

        # 模拟无旧位置
        registry._update_hosts_edges(wg, None, "dark_forest")

        # 不崩溃即成功
        edges_new = wg.get_edges_between("dark_forest", "player")
        hosts_new = [k for k, d in edges_new if d.get("relation") == "hosts"]
        assert len(hosts_new) >= 1

    def test_navigate_same_area_skip(self):
        """同区域导航跳过 HOSTS 和 enter/exit（通过真实 navigate 调用）。"""
        async def _run():
            session = _make_session_runtime()
            wg, engine = _build_graph_and_engine(session)
            registry = _make_registry(session)

            # 添加自环 CONNECTS 边，让连接校验通过
            wg.add_edge(
                "town_square", "town_square",
                WorldEdgeType.CONNECTS.value,
                key="self_loop",
                travel_time="5 minutes",
                one_way=True,  # 避免自动反向边
            )

            # 在 town_square 添加 ON_ENTER behavior（不应被触发）
            town = wg.get_node("town_square")
            town.behaviors.append(Behavior(
                id="enter_town_again",
                trigger=TriggerType.ON_ENTER,
                actions=[
                    Action(
                        type=ActionType.NARRATIVE_HINT,
                        params={"text": "不应出现", "priority": "high"},
                    ),
                ],
                once=True,
            ))

            initial_edges = wg.get_edges_between("town_square", "player")
            initial_hosts = [(k, d) for k, d in initial_edges if d.get("relation") == "hosts"]

            # Mock enter_area 返回同区域
            session.enter_area = AsyncMock(return_value={"success": True, "area_id": "town_square"})

            result = await registry.navigate("town_square")
            assert result.get("success") is True

            # HOSTS 边数不变
            final_edges = wg.get_edges_between("town_square", "player")
            final_hosts = [(k, d) for k, d in final_edges if d.get("relation") == "hosts"]
            assert len(final_hosts) == len(initial_hosts)

            # 不应产生 narrative_hints（P1 is_area_change=False → 跳过）
            assert "narrative_hints" not in result

        asyncio.run(_run())

    def test_navigate_without_engine(self):
        """无 BehaviorEngine 时 navigate 正常完成，不触发 P1 逻辑。"""
        async def _run():
            session = _make_session_runtime()
            wg = GraphBuilder.build(session.world, session)
            session.world_graph = wg
            session._behavior_engine = None  # 不设置引擎

            registry = _make_registry(session)
            session.enter_area = AsyncMock(return_value={"success": True, "area_id": "dark_forest"})

            result = await registry.navigate("dark_forest")
            assert result.get("success") is True

            # 无引擎 → 不应有 narrative_hints（P1 块被 guard 跳过）
            assert "narrative_hints" not in result

            # C7c 区域状态仍应更新（C7c 不依赖引擎）
            dark_forest = wg.get_node("dark_forest")
            assert dark_forest.state.get("visited") is True

        asyncio.run(_run())

    def test_navigate_exit_context_old_location(self):
        """exit TickContext 的 player_location 是旧区域。"""
        session = _make_session_runtime()
        wg, engine = _build_graph_and_engine(session)

        # 在 town_square 添加 ON_EXIT behavior 来验证 ctx
        town = wg.get_node("town_square")
        captured_locations = []

        town.behaviors.append(Behavior(
            id="exit_town",
            trigger=TriggerType.ON_EXIT,
            actions=[
                Action(
                    type=ActionType.NARRATIVE_HINT,
                    params={"text": "离开了城镇广场", "priority": "normal"},
                ),
            ],
            once=True,
        ))

        # 模拟 P1 流程：构造 exit context 并覆写 player_location
        ctx_exit = session.build_tick_context("post")
        assert ctx_exit is not None
        ctx_exit.player_location = "town_square"  # 覆写为旧位置
        exit_result = engine.handle_exit("player", "town_square", ctx_exit)

        # 验证 exit behavior 被触发
        assert isinstance(exit_result, TickResult)
        assert len(exit_result.narrative_hints) > 0
        assert "离开了城镇广场" in exit_result.narrative_hints[0]


# =============================================================================
# P6: 战斗结束发射 WorldEvent (4 tests)
# =============================================================================


class TestP6CombatWorldEvent:
    """P6: 战斗结束发射 combat_ended WorldEvent。"""

    def test_combat_end_emits_world_event(self):
        """战斗结束调用 handle_event。"""
        session = _make_session_runtime()
        wg, engine = _build_graph_and_engine(session)

        from app.world.models import WorldEvent as WE
        ctx = session.build_tick_context("post")
        assert ctx is not None

        combat_event = WE(
            event_type="combat_ended",
            origin_node="town_square",
            actor="player",
            data={
                "result": "victory",
                "enemies": ["goblin"],
                "rewards": {"xp": 100},
                "combat_id": "c_01",
            },
            visibility="scope",
            game_day=ctx.game_day,
            game_hour=ctx.game_hour,
        )
        tick_result = engine.handle_event(combat_event, ctx)

        assert isinstance(tick_result, TickResult)
        # 事件应被记录到日志
        assert len(wg._event_log) > 0

    def test_combat_event_data_fields(self):
        """WorldEvent 包含 result/enemies/rewards 字段。"""
        from app.world.models import WorldEvent as WE

        event = WE(
            event_type="combat_ended",
            origin_node="town_square",
            actor="player",
            data={
                "result": "victory",
                "enemies": ["goblin", "orc"],
                "rewards": {"xp": 200, "gold": 50},
                "combat_id": "c_02",
            },
            visibility="scope",
            game_day=1,
            game_hour=14,
        )

        assert event.event_type == "combat_ended"
        assert event.data["result"] == "victory"
        assert "goblin" in event.data["enemies"]
        assert event.data["rewards"]["xp"] == 200
        assert event.actor == "player"

    def test_combat_event_triggers_behavior(self):
        """ON_EVENT("combat_ended") 行为触发。"""
        session = _make_session_runtime()
        wg, engine = _build_graph_and_engine(session)

        # 在 town_square 添加 ON_EVENT behavior
        town = wg.get_node("town_square")
        town.behaviors.append(Behavior(
            id="on_combat_ended",
            trigger=TriggerType.ON_EVENT,
            event_filter="combat_ended",
            actions=[
                Action(
                    type=ActionType.NARRATIVE_HINT,
                    params={"text": "战斗结束，和平回到了广场", "priority": "high"},
                ),
            ],
            once=True,
        ))

        from app.world.models import WorldEvent as WE
        ctx = session.build_tick_context("post")
        combat_event = WE(
            event_type="combat_ended",
            origin_node="town_square",
            actor="player",
            data={"result": "victory", "combat_id": "c_03"},
            visibility="scope",
            game_day=1,
            game_hour=10,
        )

        tick_result = engine.handle_event(combat_event, ctx)
        assert len(tick_result.narrative_hints) > 0
        assert any("战斗结束" in h for h in tick_result.narrative_hints)

    def test_combat_event_without_engine(self):
        """无引擎时 choose_combat_action 正常完成，不触发 P6 事件。"""
        async def _run():
            session = _make_session_runtime()
            wg = GraphBuilder.build(session.world, session)
            session.world_graph = wg
            session._behavior_engine = None

            session.game_state.combat_id = "c_no_engine"

            flash_cpu = MagicMock()
            flash_cpu.call_combat_tool = AsyncMock(side_effect=[
                {
                    "success": True,
                    "combat_state": {"is_ended": True},
                    "final_result": {"result": "victory"},
                },
                {"success": True, "resolved": True},
            ])

            registry = V4AgenticToolRegistry(
                session=session,
                flash_cpu=flash_cpu,
                graph_store=MagicMock(),
            )

            result = await registry.choose_combat_action("attack_1")

            # 无引擎 → 不应有 combat_event_hints
            assert "combat_event_hints" not in result
            # combat 本身仍正常结束
            assert session.game_state.combat_id is None

        asyncio.run(_run())


# =============================================================================
# P1 完整 navigate() 集成 (async)
# =============================================================================


class TestP1NavigateIntegration:
    """P1: 完整 navigate() 调用 → HOSTS + enter/exit + hints。"""

    def test_navigate_full_flow(self):
        """完整 navigate() 流程：HOSTS 移动 + enter/exit + hints。"""
        async def _run():
            session = _make_session_runtime(with_party=True)
            wg, engine = _build_graph_and_engine(session)
            registry = _make_registry(session)

            # 添加 ON_ENTER behavior 到 dark_forest
            dark_forest = wg.get_node("dark_forest")
            dark_forest.behaviors.append(Behavior(
                id="enter_dark_forest",
                trigger=TriggerType.ON_ENTER,
                actions=[
                    Action(
                        type=ActionType.NARRATIVE_HINT,
                        params={"text": "你进入了黑暗森林", "priority": "normal"},
                    ),
                ],
                once=True,
            ))

            # Mock enter_area
            session.enter_area = AsyncMock(return_value={"success": True, "area_id": "dark_forest"})

            result = await registry.navigate("dark_forest")

            assert result.get("success") is True

            # 验证 HOSTS 边移动
            edges_old = wg.get_edges_between("town_square", "player")
            hosts_old = [k for k, d in edges_old if d.get("relation") == "hosts"]
            assert len(hosts_old) == 0

            edges_new = wg.get_edges_between("dark_forest", "player")
            hosts_new = [k for k, d in edges_new if d.get("relation") == "hosts"]
            assert len(hosts_new) >= 1

            # 验证 player state
            player_node = wg.get_node("player")
            assert player_node.state["current_location"] == "dark_forest"

            # 验证 narrative hints
            assert "narrative_hints" in result
            assert any("黑暗森林" in h for h in result["narrative_hints"])

        asyncio.run(_run())

    def test_navigate_party_hosts_sync(self):
        """navigate() 后队友 HOSTS 也同步到新区域。"""
        async def _run():
            session = _make_session_runtime(with_party=True)
            wg, engine = _build_graph_and_engine(session)
            registry = _make_registry(session)

            # Mock enter_area
            session.enter_area = AsyncMock(return_value={"success": True, "area_id": "dark_forest"})

            result = await registry.navigate("dark_forest")
            assert result.get("success") is True

            # 验证队友 node state
            npc_node = wg.get_node("npc_smith")
            assert npc_node.state["current_location"] == "dark_forest"

        asyncio.run(_run())


# =============================================================================
# P6 完整 choose_combat_action() 集成 (async)
# =============================================================================


class TestP6CombatIntegration:
    """P6: 完整 choose_combat_action() → WorldEvent 发射。"""

    def test_choose_combat_action_emits_event(self):
        """choose_combat_action() 战斗结束时发射 combat_ended 事件。"""
        async def _run():
            session = _make_session_runtime()
            wg, engine = _build_graph_and_engine(session)

            # 设置 combat_id
            session.game_state.combat_id = "c_test"

            # 添加 ON_EVENT behavior 到 town_square
            town = wg.get_node("town_square")
            town.behaviors.append(Behavior(
                id="on_combat_win",
                trigger=TriggerType.ON_EVENT,
                event_filter="combat_ended",
                actions=[
                    Action(
                        type=ActionType.NARRATIVE_HINT,
                        params={"text": "胜利的欢呼声响彻广场", "priority": "high"},
                    ),
                ],
                once=True,
            ))

            # Mock flash_cpu
            flash_cpu = MagicMock()
            flash_cpu.call_combat_tool = AsyncMock(side_effect=[
                # execute_action_for_actor → 返回战斗结束状态
                {
                    "success": True,
                    "combat_state": {"is_ended": True},
                    "final_result": {
                        "result": "victory",
                        "enemies_defeated": ["goblin"],
                        "rewards": {"xp": 100, "gold": 30},
                    },
                },
                # resolve_combat_session_v3
                {"success": True, "resolved": True},
            ])

            registry = V4AgenticToolRegistry(
                session=session,
                flash_cpu=flash_cpu,
                graph_store=MagicMock(),
            )

            result = await registry.choose_combat_action("attack_1")

            # 验证事件已触发
            assert "combat_event_hints" in result
            assert any("胜利" in h for h in result["combat_event_hints"])

            # combat_id 应被清除
            assert session.game_state.combat_id is None

        asyncio.run(_run())
