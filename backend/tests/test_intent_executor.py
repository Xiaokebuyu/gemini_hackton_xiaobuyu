"""Tests for IntentExecutor (Direction A.2)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.world.intent_executor import IntentExecutor, EngineResult, update_hosts_edges, update_party_hosts_edges
from app.world.intent_resolver import IntentType, ResolvedIntent
from app.world.scene_bus import BusEntryType, SceneBus


def _make_session(
    player_location="town_square",
    sub_location=None,
    chapter_id="ch1",
    time_day=1,
    time_hour=10,
    time_minute=0,
    has_world_graph=True,
):
    session = MagicMock()
    session.player_location = player_location
    session.sub_location = sub_location
    session.chapter_id = chapter_id

    time_mock = MagicMock()
    time_mock.day = time_day
    time_mock.hour = time_hour
    time_mock.minute = time_minute
    session.time = time_mock

    if has_world_graph:
        wg = MagicMock()
        wg.has_node.return_value = True
        node = MagicMock()
        node.state = {"visit_count": 0}
        wg.get_node.return_value = node
        wg.get_neighbors.return_value = [
            ("tavern_area", {"travel_time": "30 minutes"}),
        ]
        session.world_graph = wg
        session._world_graph_failed = False
        session._behavior_engine = None
    else:
        session.world_graph = None
        session._world_graph_failed = True

    narrative = MagicMock()
    narrative.npc_interactions = {}
    session.narrative = narrative
    session.mark_narrative_dirty = MagicMock()

    world = MagicMock()
    chapter_data = {"available_maps": ["town_square", "tavern_area", "forest"]}
    world.chapter_registry = {"ch1": chapter_data}
    session.world = world

    session.enter_area = AsyncMock(return_value={"success": True, "new_area": "tavern_area"})
    session.enter_sublocation = AsyncMock(return_value={"success": True, "sub_location": "smithy"})
    session.update_time = MagicMock()
    session.advance_time = MagicMock(return_value={"success": True, "time": {"day": 1, "hour": 11, "minute": 0}, "events": []})
    session.build_tick_context = MagicMock(return_value=None)

    return session


def _run(coro):
    return asyncio.run(coro)


class TestExecuteMove:
    def test_successful_move(self):
        session = _make_session()
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        intent = ResolvedIntent(
            type=IntentType.MOVE, target="tavern_area", target_name="酒馆",
            params={"is_sublocation": False},
        )
        result = _run(executor.dispatch(intent))
        assert result.success
        assert result.intent_type == "move_area"
        assert result.target == "tavern_area"
        assert len(result.bus_entries) == 1
        session.enter_area.assert_called_once_with("tavern_area")
        session.update_time.assert_called_once()

    def test_chapter_gate_blocks(self):
        session = _make_session()
        session.world.chapter_registry = {"ch1": {"available_maps": ["town_square"]}}
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        intent = ResolvedIntent(
            type=IntentType.MOVE, target="forbidden_zone",
            params={"is_sublocation": False},
        )
        result = _run(executor.dispatch(intent))
        assert not result.success
        assert "not available in chapter" in result.error

    def test_no_connection_fails(self):
        session = _make_session()
        # Add unreachable to available_maps to pass chapter gate, but no CONNECTS edge
        session.world.chapter_registry = {
            "ch1": {"available_maps": ["town_square", "unreachable"]}
        }
        session.world_graph.get_neighbors.return_value = [
            ("market", {"travel_time": "15 minutes"}),
        ]
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        intent = ResolvedIntent(
            type=IntentType.MOVE, target="unreachable",
            params={"is_sublocation": False},
        )
        result = _run(executor.dispatch(intent))
        assert not result.success
        assert "no connection" in result.error

    def test_blocked_path_fails(self):
        session = _make_session()
        session.world_graph.get_neighbors.return_value = [
            ("blocked_area", {"travel_time": "30 minutes", "blocked": True, "blocked_reason": "cave-in"}),
        ]
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        intent = ResolvedIntent(
            type=IntentType.MOVE, target="blocked_area",
            params={"is_sublocation": False},
        )
        result = _run(executor.dispatch(intent))
        assert not result.success
        assert "blocked" in result.error

    def test_enter_area_failure_not_marked(self):
        session = _make_session()
        session.enter_area = AsyncMock(return_value={"success": False, "error": "area locked"})
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        intent = ResolvedIntent(
            type=IntentType.MOVE, target="tavern_area",
            params={"is_sublocation": False},
        )
        result = _run(executor.dispatch(intent))
        assert not result.success
        assert "area locked" in result.error


class TestExecuteSublocation:
    def test_successful_sublocation_enter(self):
        session = _make_session()
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        intent = ResolvedIntent(
            type=IntentType.MOVE, target="smithy", target_name="铁匠铺",
            params={"is_sublocation": True},
        )
        result = _run(executor.dispatch(intent))
        assert result.success
        assert result.intent_type == "move_sublocation"
        assert result.target == "smithy"
        session.enter_sublocation.assert_called_once_with("smithy")

    def test_sublocation_graph_id_is_normalized_before_enter(self):
        """Graph node id `loc_<area>_<sub>` should be converted to runtime sub_id."""
        session = _make_session(player_location="frontier_town")
        bus = SceneBus(area_id="frontier_town")
        executor = IntentExecutor(session, bus)
        intent = ResolvedIntent(
            type=IntentType.MOVE,
            target="loc_frontier_town_tavern_guild",
            target_name="公会大厅",
            params={"is_sublocation": True},
        )
        result = _run(executor.dispatch(intent))
        assert result.success
        assert result.intent_type == "move_sublocation"
        assert result.target == "tavern_guild"
        session.enter_sublocation.assert_called_once_with("tavern_guild")

    def test_locked_sublocation(self):
        session = _make_session()
        node = MagicMock()
        node.state = {"locked": True, "lock_reason": "requires key"}
        session.world_graph.get_node.return_value = node
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        intent = ResolvedIntent(
            type=IntentType.MOVE, target="locked_room",
            params={"is_sublocation": True},
        )
        result = _run(executor.dispatch(intent))
        assert not result.success
        assert "locked" in result.error

    def test_sublocation_enter_injects_recall_entry(self):
        session = _make_session()
        session.world_id = "world_1"
        session.area_id = "town_square"
        session.player = MagicMock(character_id="player")
        bus = SceneBus(area_id="town_square")
        recall_result = MagicMock()
        recall_result.activated_nodes = {"topic_old_friend": 0.91, "event_quest_1": 0.72}
        recall_result.translated_memory = None
        recall_result.used_subgraph = True
        recall_orchestrator = MagicMock()
        recall_orchestrator.recall = AsyncMock(return_value=recall_result)
        executor = IntentExecutor(session, bus, recall_orchestrator=recall_orchestrator)
        intent = ResolvedIntent(
            type=IntentType.MOVE, target="smithy", target_name="铁匠铺",
            params={"is_sublocation": True},
        )

        result = _run(executor.dispatch(intent))

        assert result.success
        assert len(result.bus_entries) == 2
        assert result.bus_entries[1].type == BusEntryType.SYSTEM
        assert "历史线索" in result.bus_entries[1].content
        recall_orchestrator.recall.assert_called_once()
        kwargs = recall_orchestrator.recall.call_args.kwargs
        assert kwargs["intent_type"] == "enter_sublocation"
        assert kwargs["location_id"] == "smithy"

    def test_sublocation_enter_recall_failure_is_fail_open(self):
        session = _make_session()
        session.world_id = "world_1"
        session.area_id = "town_square"
        bus = SceneBus(area_id="town_square")
        recall_orchestrator = MagicMock()
        recall_orchestrator.recall = AsyncMock(side_effect=RuntimeError("boom"))
        executor = IntentExecutor(session, bus, recall_orchestrator=recall_orchestrator)
        intent = ResolvedIntent(
            type=IntentType.MOVE, target="smithy", target_name="铁匠铺",
            params={"is_sublocation": True},
        )

        result = _run(executor.dispatch(intent))

        assert result.success
        assert len(result.bus_entries) == 1


class TestExecuteTalkSetup:
    """Basic talk validation (no flash_cpu)."""

    def test_successful_talk_setup(self):
        session = _make_session()
        npc_node = MagicMock()
        npc_node.state = {"is_alive": True}
        session.world_graph.get_node.return_value = npc_node
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        intent = ResolvedIntent(
            type=IntentType.TALK, target="priestess", target_name="女祭司",
        )
        result = _run(executor.dispatch(intent))
        assert result.success
        assert result.intent_type == "talk_pending"  # no flash_cpu → NPC didn't respond
        assert result.target == "priestess"
        assert len(result.bus_entries) == 1
        assert session.narrative.npc_interactions["priestess"] == 1

    def test_talk_dead_npc_fails(self):
        session = _make_session()
        npc_node = MagicMock()
        npc_node.state = {"is_alive": False}
        session.world_graph.get_node.return_value = npc_node
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        intent = ResolvedIntent(
            type=IntentType.TALK, target="dead_npc", target_name="死亡NPC",
        )
        result = _run(executor.dispatch(intent))
        assert not result.success
        assert "not alive" in result.error

    def test_talk_missing_npc_fails(self):
        session = _make_session()
        session.world_graph.get_node.return_value = None
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        intent = ResolvedIntent(
            type=IntentType.TALK, target="ghost", target_name="幽灵",
        )
        result = _run(executor.dispatch(intent))
        assert not result.success
        assert "not found" in result.error


class TestExecuteTalk:
    """execute_talk returns talk_pending (NPC dialogue handled by Pipeline layer)."""

    def test_talk_always_returns_talk_pending(self):
        """execute_talk always returns talk_pending with ACTION entry only."""
        session = _make_session()
        npc_node = MagicMock()
        npc_node.state = {"is_alive": True}
        session.world_graph.get_node.return_value = npc_node
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        result = _run(executor.execute_talk("priestess", "女祭司", "你好啊"))
        assert result.success
        assert result.intent_type == "talk_pending"
        assert len(result.bus_entries) == 1
        assert result.bus_entries[0].type == BusEntryType.ACTION
        assert result.bus_entries[0].actor == "player"

    def test_talk_without_flash_cpu_only_action_entry(self):
        """No flash_cpu in constructor -> still returns talk_pending."""
        session = _make_session()
        npc_node = MagicMock()
        npc_node.state = {"is_alive": True}
        session.world_graph.get_node.return_value = npc_node
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        result = _run(executor.execute_talk("priestess", "女祭司"))
        assert result.success
        assert len(result.bus_entries) == 1
        assert result.bus_entries[0].type == BusEntryType.ACTION

    def test_talk_stores_player_message_in_bus_data(self):
        """player_message is stored in bus entry data for Pipeline layer."""
        session = _make_session()
        npc_node = MagicMock()
        npc_node.state = {"is_alive": True}
        session.world_graph.get_node.return_value = npc_node
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        result = _run(executor.execute_talk("priestess", "女祭司", "我需要治疗"))
        assert result.success
        assert result.bus_entries[0].data["message"] == "我需要治疗"
        assert result.bus_entries[0].data["npc_id"] == "priestess"


class TestExecuteLeave:
    def test_successful_leave(self):
        session = _make_session(sub_location="smithy")
        session.leave_sublocation = AsyncMock(return_value={"success": True})
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        intent = ResolvedIntent(
            type=IntentType.LEAVE, target="smithy",
        )
        result = _run(executor.dispatch(intent))
        assert result.success
        assert result.intent_type == "leave"
        assert result.target == "smithy"
        assert len(result.bus_entries) == 1
        session.leave_sublocation.assert_called_once()

    def test_leave_not_in_sublocation(self):
        session = _make_session(sub_location=None)
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        intent = ResolvedIntent(
            type=IntentType.LEAVE, target="",
        )
        result = _run(executor.dispatch(intent))
        assert not result.success
        assert "not in a sub-location" in result.error

    def test_leave_failure(self):
        session = _make_session(sub_location="locked_room")
        session.leave_sublocation = AsyncMock(return_value={"success": False, "error": "door is locked"})
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        intent = ResolvedIntent(
            type=IntentType.LEAVE, target="locked_room",
        )
        result = _run(executor.dispatch(intent))
        assert not result.success
        assert "door is locked" in result.error


class TestExecuteRest:
    def test_successful_rest(self):
        session = _make_session()
        player = MagicMock()
        player.current_hp = 50
        player.max_hp = 100
        session.player = player
        session.game_state = None
        session.mark_player_dirty = MagicMock()
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        intent = ResolvedIntent(
            type=IntentType.REST, target="rest",
        )
        result = _run(executor.dispatch(intent))
        assert result.success
        assert result.intent_type == "rest"
        assert len(result.bus_entries) == 1
        session.advance_time.assert_called_once_with(60)
        assert player.current_hp == 75  # 50 + 25% of 100
        session.mark_player_dirty.assert_called_once()

    def test_rest_hp_cap(self):
        session = _make_session()
        player = MagicMock()
        player.current_hp = 95
        player.max_hp = 100
        session.player = player
        session.game_state = None
        session.mark_player_dirty = MagicMock()
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        intent = ResolvedIntent(
            type=IntentType.REST, target="rest",
        )
        result = _run(executor.dispatch(intent))
        assert result.success
        assert player.current_hp == 100  # capped at max

    def test_rest_time_advance(self):
        session = _make_session(time_hour=10, time_minute=30)
        session.player = MagicMock(current_hp=100, max_hp=100)
        session.game_state = None
        session.mark_player_dirty = MagicMock()
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        intent = ResolvedIntent(
            type=IntentType.REST, target="rest",
        )
        result = _run(executor.dispatch(intent))
        assert result.success
        # advance_time should be called with 60 minutes
        session.advance_time.assert_called_once_with(60)

    def test_rest_blocked_during_combat(self):
        session = _make_session()
        session.player = MagicMock(current_hp=50, max_hp=100)
        game_state = MagicMock()
        game_state.combat_id = "combat_123"
        session.game_state = game_state
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        intent = ResolvedIntent(
            type=IntentType.REST, target="rest",
        )
        result = _run(executor.dispatch(intent))
        assert not result.success
        assert "combat" in result.error


class TestPeriodConsistency:
    def test_period_dawn(self):
        assert IntentExecutor._get_period(6) == "dawn"

    def test_period_day(self):
        assert IntentExecutor._get_period(12) == "day"

    def test_period_dusk(self):
        assert IntentExecutor._get_period(19) == "dusk"

    def test_period_night(self):
        assert IntentExecutor._get_period(23) == "night"

    def test_period_boundary_5_is_dawn(self):
        assert IntentExecutor._get_period(5) == "dawn"

    def test_period_boundary_8_is_day(self):
        assert IntentExecutor._get_period(8) == "day"

    def test_period_boundary_18_is_dusk(self):
        assert IntentExecutor._get_period(18) == "dusk"

    def test_period_boundary_20_is_night(self):
        assert IntentExecutor._get_period(20) == "night"


class TestTravelTime:
    def test_parse_minutes(self):
        assert IntentExecutor._parse_travel_time("30 minutes") == 30
        assert IntentExecutor._parse_travel_time("15分钟") == 15

    def test_parse_hours(self):
        assert IntentExecutor._parse_travel_time("2 hours") == 120
        assert IntentExecutor._parse_travel_time("1小时") == 60

    def test_parse_half_day(self):
        assert IntentExecutor._parse_travel_time("半天") == 360

    def test_parse_default(self):
        assert IntentExecutor._parse_travel_time("unknown") == 30

    def test_normalize_advance_minutes(self):
        """边界值验证: snap to nearest allowed bucket."""
        assert IntentExecutor._normalize_advance_minutes(7) == 5
        assert IntentExecutor._normalize_advance_minutes(1) == 5
        assert IntentExecutor._normalize_advance_minutes(0) == 5
        assert IntentExecutor._normalize_advance_minutes(30) == 30
        assert IntentExecutor._normalize_advance_minutes(45) == 30
        assert IntentExecutor._normalize_advance_minutes(46) == 60
        assert IntentExecutor._normalize_advance_minutes(720) == 720
        assert IntentExecutor._normalize_advance_minutes(9999) == 720


class TestExecuteMoveNormalization:
    def test_move_uses_normalized_time(self):
        """7min edge → bus_entries data 中 travel_minutes==5."""
        session = _make_session()
        session.world_graph.get_neighbors.return_value = [
            ("tavern_area", {"travel_time": "7 minutes"}),
        ]
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        intent = ResolvedIntent(
            type=IntentType.MOVE, target="tavern_area", target_name="酒馆",
            params={"is_sublocation": False},
        )
        result = _run(executor.dispatch(intent))
        assert result.success
        assert result.bus_entries[0].data["travel_minutes"] == 5


class TestHostsEdgeHelpers:
    def test_update_hosts_edges_player(self):
        """Player HOSTS edge should move from old to new area."""
        wg = MagicMock()
        wg.has_node.return_value = True
        update_hosts_edges(wg, "old_area", "new_area")
        wg.remove_edge.assert_called_once_with("old_area", "player", key="hosts_player")
        wg.add_edge.assert_called_once()
        wg.merge_state.assert_called_once_with("player", {"current_location": "new_area"})

    def test_update_hosts_edges_no_player_node(self):
        """No player node → skip silently."""
        wg = MagicMock()
        wg.has_node.return_value = False
        update_hosts_edges(wg, "old_area", "new_area")
        wg.remove_edge.assert_not_called()
        wg.add_edge.assert_not_called()

    def test_update_party_hosts_edges(self):
        """Party member edges should follow player."""
        wg = MagicMock()
        wg.has_node.return_value = True
        member = MagicMock()
        member.character_id = "priestess"
        npc_node = MagicMock()
        npc_node.state = {"current_location": "old_area"}
        wg.get_node.return_value = npc_node
        party = MagicMock()
        party.get_active_members.return_value = [member]
        update_party_hosts_edges(wg, "new_area", party)
        wg.remove_edge.assert_called_once_with("old_area", "priestess", key="hosts_priestess")
        wg.add_edge.assert_called_once()
        wg.merge_state.assert_called_once_with("priestess", {"current_location": "new_area"})

    def test_update_party_hosts_edges_no_party(self):
        """No party → skip silently."""
        wg = MagicMock()
        wg.has_node.return_value = True
        update_party_hosts_edges(wg, "new_area", None)
        wg.add_edge.assert_not_called()


class TestExecuteMoveHostsEdges:
    def test_hosts_edges_updated_on_move(self):
        """Move should trigger HOSTS edge updates."""
        session = _make_session()
        # Need party for update_party_hosts_edges
        member = MagicMock()
        member.character_id = "companion"
        party = MagicMock()
        party.get_active_members.return_value = [member]
        session.party = party

        # Configure wg to track calls
        wg = session.world_graph
        wg.has_node.return_value = True
        node = MagicMock()
        node.state = {"visit_count": 0, "current_location": "town_square"}
        wg.get_node.return_value = node

        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        intent = ResolvedIntent(
            type=IntentType.MOVE, target="tavern_area", target_name="酒馆",
            params={"is_sublocation": False},
        )
        result = _run(executor.dispatch(intent))
        assert result.success
        # Verify HOSTS edge calls happened (add_edge called for player + companion)
        assert wg.add_edge.call_count >= 2


class TestExecuteExamine:
    def test_examine_returns_success(self):
        """execute_examine 应返回 success=True 且 intent_type='examine'。"""
        session = _make_session()
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        intent = ResolvedIntent(
            type=IntentType.EXAMINE,
            target="treasure_chest",
            target_name="宝箱",
        )
        result = _run(executor.dispatch(intent))
        assert result.success
        assert result.intent_type == "examine"
        assert result.target == "treasure_chest"

    def test_examine_has_bus_entry(self):
        """execute_examine 应写入一条总线条目。"""
        session = _make_session()
        node = MagicMock()
        node.name = "古老典籍"
        node.type = "location"
        node.properties = {"description": "一本封皮斑驳、散发灰尘气息的古书。"}
        node.state = {}
        session.world_graph.get_node.return_value = node
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        result = _run(executor.execute_examine("ancient_tome", "古老典籍"))
        assert len(result.bus_entries) == 1
        entry = result.bus_entries[0]
        assert entry.data["tool"] == "examine"
        assert entry.data["target_id"] == "ancient_tome"
        assert entry.data["target_type"] == "location"
        assert "description" in entry.data

    def test_examine_has_narrative_hint(self):
        """execute_examine 应提供叙事提示，引导 GM 描写目标细节。"""
        session = _make_session()
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        result = _run(executor.execute_examine("guild_board", "公会告示板"))
        assert len(result.narrative_hints) > 0
        assert "公会告示板" in result.narrative_hints[0]

    def test_examine_does_not_modify_session_time(self):
        """execute_examine 不应修改游戏时间。"""
        session = _make_session()
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        _run(executor.execute_examine("some_item", "某物品"))
        session.update_time.assert_not_called()


class TestExecuteUseItem:
    @staticmethod
    def _attach_player_with_potion(session, *, hp=50, max_hp=100):
        player = MagicMock()
        player.current_hp = hp
        player.max_hp = max_hp
        player.inventory = [{"item_id": "healing_potion", "item_name": "治疗药水", "quantity": 1}]

        def _remove(item_id, quantity=1):
            for idx, item in enumerate(list(player.inventory)):
                if item.get("item_id") == item_id:
                    current_qty = int(item.get("quantity", 1))
                    if current_qty <= quantity:
                        player.inventory.pop(idx)
                    else:
                        item["quantity"] = current_qty - quantity
                    return True
            return False

        player.remove_item = MagicMock(side_effect=_remove)
        session.player = player
        session.mark_player_dirty = MagicMock()
        return player

    def test_use_item_returns_success(self):
        """execute_use_item 应返回 success=True 且 intent_type='use_item'。"""
        session = _make_session()
        player = self._attach_player_with_potion(session)
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        intent = ResolvedIntent(
            type=IntentType.USE_ITEM,
            target="healing_potion",
            target_name="治疗药水",
        )
        result = _run(executor.dispatch(intent))
        assert result.success
        assert result.intent_type == "use_item"
        assert result.target == "healing_potion"
        assert player.current_hp >= 50
        assert player.current_hp <= 100
        session.mark_player_dirty.assert_called_once()

    def test_use_item_has_bus_entry(self):
        """execute_use_item 应写入一条总线条目。"""
        session = _make_session()
        self._attach_player_with_potion(session)
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        result = _run(executor.execute_use_item("healing_potion", "治疗药水"))
        assert len(result.bus_entries) == 1
        entry = result.bus_entries[0]
        assert entry.data["tool"] == "use_item"
        assert entry.data["item_id"] == "healing_potion"
        assert entry.data["effect_type"] == "heal"
        assert entry.data["new_hp"] >= entry.data["old_hp"]

    def test_use_item_has_narrative_hint(self):
        """execute_use_item 应提供执行结果提示（HP 与消耗）。"""
        session = _make_session()
        self._attach_player_with_potion(session)
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        result = _run(executor.execute_use_item("healing_potion", "治疗药水"))
        assert len(result.narrative_hints) > 0
        assert "恢复" in result.narrative_hints[0]
        assert "消耗" in result.narrative_hints[0]

    def test_use_item_does_not_modify_session_time(self):
        """execute_use_item 不应修改游戏时间。"""
        session = _make_session()
        self._attach_player_with_potion(session)
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)
        _run(executor.execute_use_item("healing_potion", "治疗药水"))
        session.update_time.assert_not_called()

    def test_use_item_unsupported_effect_fails(self):
        """未配置 ITEM_EFFECTS 的物品应返回 error，由 GM 兜底。"""
        session = _make_session()
        player = MagicMock()
        player.current_hp = 50
        player.max_hp = 100
        player.inventory = [{"item_id": "mana_potion", "item_name": "魔力药水", "quantity": 1}]
        player.remove_item = MagicMock(return_value=True)
        session.player = player
        session.mark_player_dirty = MagicMock()
        bus = SceneBus(area_id="town_square")
        executor = IntentExecutor(session, bus)

        result = _run(executor.execute_use_item("mana_potion", "魔力药水"))
        assert not result.success
        assert "unsupported item effect" in result.error
        session.mark_player_dirty.assert_not_called()
