"""Tests for P28 Track B — building tools (AcceptQuestTool, discover_room, fill_room).

Covers:
- B-1: AcceptQuestTool (NPC tool, receptionist trait)
- B-2: discover_room directive contract + PlannerWorldHandler
- B-3: fill_room directive contract + PlannerWorldHandler + AreaSlice.dynamic_rooms
- B-3b: navigation enter_room supports dynamic rooms
- B-4: capacity increase (permanent 8, total 15)

Decision record: P28-动态能力系统与综合体验修复.md §8
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.narrative.character_tools import AcceptQuestTool
from app.game_core.narrative.context import AgentContext
from app.game_core.planning.directive_contracts import validate_planner_directive
from app.game_core.rules import RulesEngine
from app.game_core.rules.defaults import register_default_rules_handlers
from app.game_core.rules.handlers.planner import PlannerWorldHandler
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, PlayerSlice, QuestSlice


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_world_with_room(
    area_id: str = "frontier_town",
    location_id: str = "guild_hall",
    room_id: str = "secret_vault",
    *,
    room_discoverable: bool = True,
) -> WorldInstance:
    """Build a minimal WorldInstance with one area + sub_location + room."""
    from app.game_core.content.registries.maps import MapRegistry

    registry = MapRegistry()
    registry.load({
        area_id: {
            "id": area_id,
            "name": area_id,
            "description": "Test area",
            "sub_locations": {
                location_id: {
                    "id": location_id,
                    "name": location_id,
                    "rooms": {
                        room_id: {
                            "id": room_id,
                            "name": f"{room_id} name",
                            "description": "",
                            "discoverable": room_discoverable,
                        },
                    },
                },
            },
        },
    })
    world = WorldInstance("test_world")
    world.register(registry)
    return world


def _make_world_with_empty_sub_loc(
    area_id: str = "frontier_town",
    location_id: str = "inn",
) -> WorldInstance:
    from app.game_core.content.registries.maps import MapRegistry

    registry = MapRegistry()
    registry.load({
        area_id: {
            "id": area_id,
            "name": area_id,
            "description": "",
            "sub_locations": {
                location_id: {
                    "id": location_id,
                    "name": location_id,
                    "rooms": {},
                },
            },
        },
    })
    world = WorldInstance("test_world")
    world.register(registry)
    return world


def _make_minimal_world() -> WorldInstance:
    return WorldInstance("test_world")


def _make_state_with_area(area_id: str = "frontier_town") -> StateContainer:
    state = StateContainer()
    areas = AreaSlice()
    areas.restore({"areas": {area_id: {}}})
    state.register(areas)
    return state


def _make_planner_world_handler() -> PlannerWorldHandler:
    return PlannerWorldHandler()


# ---------------------------------------------------------------------------
# TestAcceptQuestTool
# ---------------------------------------------------------------------------


class TestAcceptQuestTool:

    def _make_context(
        self,
        character_id: str,
        *,
        quests_data: dict[str, Any] | None = None,
    ) -> AgentContext:
        state = StateContainer()
        quests = QuestSlice()
        quests.restore({
            "dynamic_quests": quests_data or {},
            "milestone_states": {},
            "chapter_completion": {},
        })
        state.register(quests)

        engine = RulesEngine()
        register_default_rules_handlers(engine)
        world = _make_minimal_world()

        # Add player + area for context completeness
        player = PlayerSlice()
        player.restore({
            "current_area": "frontier_town",
            "current_location": "guild_counter",
        })
        state.register(player)
        areas = AreaSlice()
        areas.restore({"areas": {"frontier_town": {
            "npc_locations": {character_id: "guild_counter"},
        }}})
        state.register(areas)

        def run_command(cmd: Command) -> ExecuteResult:
            return engine.execute(cmd, state, world)

        return AgentContext(
            role="npc",
            world=world,
            state=state,
            metadata={"character_id": character_id},
            execute_command=run_command,
        )

    def test_quest_not_found(self) -> None:
        """Without role_data (P29-A2): AcceptQuestTool requires receptionist
        role_data — returns missing_role_data if not present."""
        # No role_data in metadata
        ctx = self._make_context("guild_girl", quests_data={})
        tool = AcceptQuestTool()

        async def _run():
            return await tool.execute({"quest_id": "dq_missing"}, ctx)

        result = asyncio.run(_run())
        assert result.ok is False
        assert result.metadata["status"] == "missing_role_data"

    def test_quest_not_available(self) -> None:
        """With role_data but quest not on bulletin board → quest_not_available."""
        ctx = self._make_context(
            "guild_girl",
            quests_data={"dq_active": {"status": "active", "title": "Active Quest"}},
        )
        # Inject role_data with empty bulletins — quest not on board
        ctx.metadata["role_data"] = {
            "role": "receptionist",
            "bulletins": [],
            "active_quests": [],
        }
        tool = AcceptQuestTool()

        async def _run():
            return await tool.execute({"quest_id": "dq_active"}, ctx)

        result = asyncio.run(_run())
        assert result.ok is False
        assert result.metadata["status"] == "quest_not_available"

    def test_trait_filter(self) -> None:
        tool = AcceptQuestTool()
        assert "receptionist" in tool.applicable_traits
        assert tool.allowed_roles == ["npc"]

    def test_missing_quest_id(self) -> None:
        ctx = self._make_context("guild_girl", quests_data={})
        tool = AcceptQuestTool()

        async def _run():
            return await tool.execute({}, ctx)

        result = asyncio.run(_run())
        assert result.ok is False
        assert result.metadata["status"] == "invalid_params"


# ---------------------------------------------------------------------------
# TestDiscoverRoomContract
# ---------------------------------------------------------------------------


class TestDiscoverRoomContract:

    def test_valid_discover_room(self) -> None:
        result = validate_planner_directive({
            "kind": "discover_room",
            "payload": {
                "area_id": "frontier_town",
                "location_id": "guild_hall",
                "room_id": "secret_vault",
            },
        })
        assert result.ok is True
        assert result.kind == "discover_room"

    def test_missing_room_id(self) -> None:
        result = validate_planner_directive({
            "kind": "discover_room",
            "payload": {
                "area_id": "frontier_town",
                "location_id": "guild_hall",
            },
        })
        assert result.ok is False
        assert result.reason_code == "missing_room_id"

    def test_missing_location_id(self) -> None:
        result = validate_planner_directive({
            "kind": "discover_room",
            "payload": {
                "area_id": "frontier_town",
                "room_id": "vault",
            },
        })
        assert result.ok is False
        assert result.reason_code == "missing_location_id"

    def test_missing_area_id(self) -> None:
        result = validate_planner_directive({
            "kind": "discover_room",
            "payload": {
                "location_id": "guild_hall",
                "room_id": "vault",
            },
        })
        assert result.ok is False
        assert result.reason_code == "missing_area_id"


# ---------------------------------------------------------------------------
# TestDiscoverRoomHandler
# ---------------------------------------------------------------------------


class TestDiscoverRoomHandler:

    def test_success(self) -> None:
        world = _make_world_with_room(
            area_id="frontier_town",
            location_id="guild_hall",
            room_id="secret_vault",
            room_discoverable=True,
        )
        state = _make_state_with_area("frontier_town")
        handler = _make_planner_world_handler()
        cmd = Command(
            type="planner_discover_room",
            params={
                "area_id": "frontier_town",
                "location_id": "guild_hall",
                "room_id": "secret_vault",
            },
            source="narrative_planner",
        )
        result = handler.compute(cmd, state, world)
        assert result.executed, result.errors
        # The StateChange should set discovered_room path
        changes = result.delta.changes if result.delta else []
        assert len(changes) == 1
        assert "discovered_room.guild_hall.secret_vault" in changes[0].path

    def test_not_discoverable(self) -> None:
        world = _make_world_with_room(
            area_id="frontier_town",
            location_id="guild_hall",
            room_id="common_room",
            room_discoverable=False,
        )
        state = _make_state_with_area("frontier_town")
        handler = _make_planner_world_handler()
        cmd = Command(
            type="planner_discover_room",
            params={
                "area_id": "frontier_town",
                "location_id": "guild_hall",
                "room_id": "common_room",
            },
            source="narrative_planner",
        )
        result = handler.compute(cmd, state, world)
        assert result.executed is False
        assert "not discoverable" in (result.errors[0] if result.errors else "")

    def test_room_not_found(self) -> None:
        world = _make_world_with_room(
            area_id="frontier_town",
            location_id="guild_hall",
            room_id="existing_room",
        )
        state = _make_state_with_area("frontier_town")
        handler = _make_planner_world_handler()
        cmd = Command(
            type="planner_discover_room",
            params={
                "area_id": "frontier_town",
                "location_id": "guild_hall",
                "room_id": "nonexistent_room",
            },
            source="narrative_planner",
        )
        result = handler.compute(cmd, state, world)
        assert result.executed is False
        assert "unknown room" in (result.errors[0] if result.errors else "").lower()


# ---------------------------------------------------------------------------
# TestFillRoomContract
# ---------------------------------------------------------------------------


class TestFillRoomContract:

    def test_valid_fill_room(self) -> None:
        result = validate_planner_directive({
            "kind": "fill_room",
            "payload": {
                "area_id": "frontier_town",
                "location_id": "inn",
                "room_id": "storage_room",
                "name": "储藏室",
                "description": "杂物堆积的储藏室",
            },
        })
        assert result.ok is True
        assert result.kind == "fill_room"

    def test_missing_name(self) -> None:
        result = validate_planner_directive({
            "kind": "fill_room",
            "payload": {
                "area_id": "frontier_town",
                "location_id": "inn",
                "room_id": "storage_room",
            },
        })
        assert result.ok is False
        assert result.reason_code == "missing_name"

    def test_invalid_discoverable_type(self) -> None:
        result = validate_planner_directive({
            "kind": "fill_room",
            "payload": {
                "area_id": "frontier_town",
                "location_id": "inn",
                "room_id": "storage_room",
                "name": "储藏室",
                "discoverable": "yes",  # should be bool
            },
        })
        assert result.ok is False
        assert result.reason_code == "invalid_discoverable"

    def test_discoverable_bool_accepted(self) -> None:
        result = validate_planner_directive({
            "kind": "fill_room",
            "payload": {
                "area_id": "frontier_town",
                "location_id": "inn",
                "room_id": "storage_room",
                "name": "储藏室",
                "discoverable": True,
            },
        })
        assert result.ok is True
        assert result.payload["discoverable"] is True

    def test_expiry_ticks_normalized(self) -> None:
        result = validate_planner_directive({
            "kind": "fill_room",
            "payload": {
                "area_id": "frontier_town",
                "location_id": "inn",
                "room_id": "storage_room",
                "name": "储藏室",
                "expiry_ticks": 24,
            },
        })
        assert result.ok is True
        assert result.payload["expiry_ticks"] == 24


# ---------------------------------------------------------------------------
# TestFillRoomHandler
# ---------------------------------------------------------------------------


class TestFillRoomHandler:

    def test_success(self) -> None:
        world = _make_world_with_empty_sub_loc("frontier_town", "inn")
        state = _make_state_with_area("frontier_town")
        handler = _make_planner_world_handler()
        cmd = Command(
            type="planner_fill_room",
            params={
                "area_id": "frontier_town",
                "location_id": "inn",
                "room_id": "storage_room",
                "name": "储藏室",
                "description": "一间储藏室",
            },
            source="narrative_planner",
        )
        result = handler.compute(cmd, state, world)
        assert result.executed, result.errors
        changes = result.delta.changes if result.delta else []
        assert len(changes) == 1
        assert changes[0].path == "frontier_town.dynamic_room"
        assert changes[0].operation == "add"
        room_val = changes[0].value
        assert room_val["room_id"] == "storage_room"
        assert room_val["name"] == "储藏室"
        assert room_val["sub_loc_id"] == "inn"

    def test_capacity_exceeded(self) -> None:
        world = _make_world_with_empty_sub_loc("frontier_town", "inn")
        state = _make_state_with_area("frontier_town")
        # Fill up 5 dynamic rooms
        for i in range(5):
            state.areas.add_dynamic_room("frontier_town", {
                "sub_loc_id": "inn",
                "room_id": f"room_{i}",
                "name": f"Room {i}",
            })
        handler = _make_planner_world_handler()
        cmd = Command(
            type="planner_fill_room",
            params={
                "area_id": "frontier_town",
                "location_id": "inn",
                "room_id": "overflow_room",
                "name": "Overflow",
            },
            source="narrative_planner",
        )
        result = handler.compute(cmd, state, world)
        assert result.executed is False
        assert "capacity exceeded" in (result.errors[0] if result.errors else "").lower()

    def test_duplicate_room_id(self) -> None:
        world = _make_world_with_empty_sub_loc("frontier_town", "inn")
        state = _make_state_with_area("frontier_town")
        state.areas.add_dynamic_room("frontier_town", {
            "sub_loc_id": "inn",
            "room_id": "storage_room",
            "name": "Already Exists",
        })
        handler = _make_planner_world_handler()
        cmd = Command(
            type="planner_fill_room",
            params={
                "area_id": "frontier_town",
                "location_id": "inn",
                "room_id": "storage_room",
                "name": "Duplicate",
            },
            source="narrative_planner",
        )
        result = handler.compute(cmd, state, world)
        assert result.executed is False
        assert "already exists" in (result.errors[0] if result.errors else "").lower()


# ---------------------------------------------------------------------------
# TestDynamicRoomNavigation
# ---------------------------------------------------------------------------


class TestDynamicRoomNavigation:

    def _make_state_with_player_in_location(
        self,
        area_id: str,
        location_id: str,
        *,
        dynamic_rooms: list[dict] | None = None,
    ) -> StateContainer:
        state = StateContainer()
        player = PlayerSlice()
        player.restore({
            "current_area": area_id,
            "current_location": location_id,
            "current_room": None,
        })
        state.register(player)
        areas = AreaSlice()
        area_data: dict[str, Any] = {}
        if dynamic_rooms:
            area_data["dynamic_rooms"] = dynamic_rooms
        areas.restore({"areas": {area_id: area_data}})
        state.register(areas)
        return state

    def test_enter_dynamic_room(self) -> None:
        """Player can enter a non-discoverable dynamic room."""
        from app.game_core.rules.handlers.navigation import NavigationHandler

        dynamic_rooms: list[dict] = [
            {"sub_loc_id": "inn", "room_id": "storage_room", "name": "Storage", "discoverable": False},
        ]
        state = self._make_state_with_player_in_location(
            "frontier_town", "inn", dynamic_rooms=dynamic_rooms,
        )
        world = _make_world_with_empty_sub_loc("frontier_town", "inn")
        handler = NavigationHandler()
        cmd = Command(
            type="enter_room",
            params={"room_id": "storage_room"},
            source="player",
        )
        validation = handler.validate(cmd, state, world)
        assert validation.ok is True

    def test_discoverable_dynamic_room_blocked_until_discovered(self) -> None:
        """Player cannot enter an undiscovered discoverable dynamic room."""
        from app.game_core.rules.handlers.navigation import NavigationHandler

        dynamic_rooms: list[dict] = [
            {"sub_loc_id": "inn", "room_id": "hidden_room", "name": "Hidden", "discoverable": True},
        ]
        state = self._make_state_with_player_in_location(
            "frontier_town", "inn", dynamic_rooms=dynamic_rooms,
        )
        world = _make_world_with_empty_sub_loc("frontier_town", "inn")
        handler = NavigationHandler()
        cmd = Command(
            type="enter_room",
            params={"room_id": "hidden_room"},
            source="player",
        )
        validation = handler.validate(cmd, state, world)
        assert validation.ok is False
        assert "not been discovered" in validation.reason

    def test_enter_unknown_dynamic_room_fails(self) -> None:
        """Player cannot enter a room that does not exist as static or dynamic."""
        from app.game_core.rules.handlers.navigation import NavigationHandler

        state = self._make_state_with_player_in_location("frontier_town", "inn")
        world = _make_world_with_empty_sub_loc("frontier_town", "inn")
        handler = NavigationHandler()
        cmd = Command(
            type="enter_room",
            params={"room_id": "totally_unknown"},
            source="player",
        )
        validation = handler.validate(cmd, state, world)
        assert validation.ok is False
        assert "unknown room" in validation.reason.lower()


# ---------------------------------------------------------------------------
# TestCapacityIncrease
# ---------------------------------------------------------------------------


class TestCapacityIncrease:

    def test_permanent_8(self) -> None:
        """DynamicSubAreaManager respects permanent limit of 8."""
        areas = AreaSlice()
        areas.restore({"areas": {"forest": {}}})
        from app.game_core.planning.dynamic_sub_area import DynamicSubAreaManager
        manager = DynamicSubAreaManager(areas)
        for i in range(8):
            r = manager.create("forest", {"id": f"perm_{i}", "tier": "permanent"})
            assert r is not None, f"perm_{i} creation failed"
        overflow = manager.create("forest", {"id": "perm_overflow", "tier": "permanent"})
        assert overflow is None

    def test_total_15(self) -> None:
        """DynamicSubAreaManager respects total limit of 15."""
        areas = AreaSlice()
        areas.restore({"areas": {"forest": {}}})
        from app.game_core.planning.dynamic_sub_area import DynamicSubAreaManager
        manager = DynamicSubAreaManager(areas)
        for i in range(15):
            r = manager.create("forest", {"id": f"temp_{i}"})
            assert r is not None, f"temp_{i} creation failed"
        overflow = manager.create("forest", {"id": "temp_overflow"})
        assert overflow is None

    def test_has_cluster_capacity_permanent_tier(self) -> None:
        """has_cluster_capacity('permanent') returns False at >= 8."""
        areas = AreaSlice()
        areas.restore({"areas": {"x": {}}})
        for i in range(8):
            areas.add_temporary_sub_area("x", {"id": f"p{i}", "expiry": -1})
        assert areas.has_cluster_capacity("x", tier="permanent") is False
        # 8 items below the total limit of 15, so "any" still returns True
        assert areas.has_cluster_capacity("x", tier="any") is True

    def test_has_cluster_capacity_any_tier_below_15(self) -> None:
        """has_cluster_capacity('any') returns True when below 15."""
        areas = AreaSlice()
        areas.restore({"areas": {"x": {}}})
        for i in range(14):
            areas.add_temporary_sub_area("x", {"id": f"t{i}", "expiry": 5})
        assert areas.has_cluster_capacity("x") is True

    def test_has_cluster_capacity_any_tier_at_15(self) -> None:
        """has_cluster_capacity('any') returns False at 15."""
        areas = AreaSlice()
        areas.restore({"areas": {"x": {}}})
        for i in range(15):
            areas.add_temporary_sub_area("x", {"id": f"t{i}", "expiry": 5})
        assert areas.has_cluster_capacity("x") is False
