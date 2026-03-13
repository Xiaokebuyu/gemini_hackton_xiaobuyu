"""Tests for Phase 3: Room navigation + visibility (P27).

Covers:
- enter_room / leave_room command validation and computation
- leave_sub_location also clears current_room
- is_colocated three-level (sub_location + room) filtering
- Osiris _build_nearby_npcs room-level locality
"""

from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.content.registries import MapRegistry
from app.game_core.orchestration.presence import is_colocated
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import NavigationHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import PlayerSlice
from app.game_core.state.slices.area import AreaSlice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_world() -> WorldInstance:
    """World with 'town' → 'guild' sub_location that has 'hall' and 'office' rooms."""
    world = WorldInstance("test_world")
    maps = MapRegistry()
    maps.load(
        {
            "start": {
                "id": "start",
                "name": "Start",
                "connections": [{"target_map_id": "town"}],
            },
            "town": {
                "id": "town",
                "name": "Town",
                "default_sub_location": "guild",
                "connections": [{"target_map_id": "start"}],
                "sub_locations": {
                    "guild": {
                        "id": "guild",
                        "name": "Guild",
                        "default_room": "hall",
                        "rooms": {
                            "hall": {
                                "id": "hall",
                                "name": "Great Hall",
                                "discoverable": False,
                                "discovery_dc": 0,
                                "resident_npcs": ["npc_a"],
                            },
                            "office": {
                                "id": "office",
                                "name": "Guild Office",
                                "discoverable": True,
                                "discovery_dc": 0,
                                "resident_npcs": ["npc_b"],
                            },
                        },
                    },
                    "temple": {"id": "temple", "name": "Temple"},
                },
            },
        }
    )
    world.register(maps)
    return world


def _make_state(
    *,
    area: str = "town",
    location: str | None = "guild",
    room: str | None = None,
) -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    player.restore(
        {"current_area": area, "current_location": location, "current_room": room}
    )
    state.register(player)
    return state


def _make_state_with_areas(
    *,
    area: str = "town",
    location: str | None = "guild",
    room: str | None = None,
    npc_rooms: dict[str, str | None] | None = None,
    discovered_rooms: set[str] | None = None,
) -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    player.restore(
        {"current_area": area, "current_location": location, "current_room": room}
    )
    state.register(player)
    areas = AreaSlice()
    areas.restore({"areas": {}})
    areas.get_area(area)  # ensure area exists
    if npc_rooms:
        for npc_id, r in npc_rooms.items():
            areas.set_npc_room(area, npc_id, r)
    if discovered_rooms:
        for room_key in discovered_rooms:
            # room_key format: "sub_loc_id__room_id"
            parts = room_key.split("__", 1)
            if len(parts) == 2:
                areas.mark_room_discovered(area, parts[0], parts[1])
    state.register(areas)
    return state


def _make_engine() -> RulesEngine:
    engine = RulesEngine()
    engine.register(NavigationHandler())
    return engine


def _apply(result, state: StateContainer) -> None:
    assert result.delta is not None
    state.apply(result.delta)


def _error_msg(result) -> str:
    """Extract error message from ExecuteResult.errors list."""
    return " ".join(result.errors) if result.errors else ""


# ---------------------------------------------------------------------------
# 3-1: enter_room / leave_room commands
# ---------------------------------------------------------------------------


class TestEnterRoomCommand:
    def test_enter_room_succeeds_for_non_discoverable_room(self) -> None:
        """Entering a non-discoverable room always succeeds."""
        state = _make_state(area="town", location="guild", room=None)
        result = _make_engine().execute(
            Command(type="enter_room", params={"room_id": "hall"}),
            state,
            _make_world(),
        )
        assert result.executed is True
        _apply(result, state)
        assert state.player.current_room == "hall"

    def test_enter_room_sets_current_room(self) -> None:
        """After enter_room, current_room matches the requested room."""
        state = _make_state(area="town", location="guild", room=None)
        result = _make_engine().execute(
            Command(type="enter_room", params={"room_id": "hall"}),
            state,
            _make_world(),
        )
        _apply(result, state)
        assert state.player.current_room == "hall"
        assert state.player.current_location == "guild"  # unchanged

    def test_enter_room_accepts_room_alias(self) -> None:
        """enter_room also accepts 'room' as parameter key alias."""
        state = _make_state(area="town", location="guild", room=None)
        result = _make_engine().execute(
            Command(type="enter_room", params={"room": "hall"}),
            state,
            _make_world(),
        )
        assert result.executed is True
        _apply(result, state)
        assert state.player.current_room == "hall"

    def test_enter_room_metadata(self) -> None:
        """enter_room metadata contains sub_location and room info."""
        state = _make_state(area="town", location="guild", room=None)
        result = _make_engine().execute(
            Command(type="enter_room", params={"room_id": "hall"}),
            state,
            _make_world(),
        )
        assert result.executed is True
        assert result.metadata["sub_location_id"] == "guild"
        assert result.metadata["to_room"] == "hall"
        assert result.metadata["from_room"] is None

    def test_enter_room_fails_when_not_in_sub_location(self) -> None:
        """enter_room fails when player is not in a sub_location."""
        state = _make_state(area="town", location=None, room=None)
        result = _make_engine().execute(
            Command(type="enter_room", params={"room_id": "hall"}),
            state,
            _make_world(),
        )
        assert result.executed is False
        assert "sub_location" in _error_msg(result)

    def test_enter_room_fails_for_unknown_room(self) -> None:
        """enter_room fails when room_id doesn't exist in current sub_location."""
        state = _make_state(area="town", location="guild", room=None)
        result = _make_engine().execute(
            Command(type="enter_room", params={"room_id": "nonexistent_room"}),
            state,
            _make_world(),
        )
        assert result.executed is False
        assert "unknown room" in _error_msg(result)

    def test_enter_room_fails_for_missing_room_id(self) -> None:
        """enter_room fails with empty params."""
        state = _make_state(area="town", location="guild", room=None)
        result = _make_engine().execute(
            Command(type="enter_room", params={}),
            state,
            _make_world(),
        )
        assert result.executed is False

    def test_enter_discoverable_room_fails_without_discovery(self) -> None:
        """Entering a discoverable room fails when not yet discovered."""
        state = _make_state_with_areas(
            area="town", location="guild", room=None,
            discovered_rooms=set(),
        )
        result = _make_engine().execute(
            Command(type="enter_room", params={"room_id": "office"}),
            state,
            _make_world(),
        )
        assert result.executed is False
        assert "not been discovered" in _error_msg(result)

    def test_enter_discoverable_room_succeeds_after_discovery(self) -> None:
        """Entering a discoverable room succeeds when room is marked discovered."""
        state = _make_state_with_areas(
            area="town", location="guild", room=None,
            discovered_rooms={"guild__office"},
        )
        result = _make_engine().execute(
            Command(type="enter_room", params={"room_id": "office"}),
            state,
            _make_world(),
        )
        assert result.executed is True
        _apply(result, state)
        assert state.player.current_room == "office"

    def test_enter_room_time_cost(self) -> None:
        """enter_room has a minimal time cost (~1/24 slot)."""
        state = _make_state(area="town", location="guild", room=None)
        result = _make_engine().execute(
            Command(type="enter_room", params={"room_id": "hall"}),
            state,
            _make_world(),
        )
        assert result.executed is True
        assert abs(result.time_cost - 1.0 / 24.0) < 0.001


class TestLeaveRoomCommand:
    def test_leave_room_clears_current_room(self) -> None:
        """leave_room sets current_room to None."""
        state = _make_state(area="town", location="guild", room="hall")
        result = _make_engine().execute(
            Command(type="leave_room", params={}),
            state,
            _make_world(),
        )
        assert result.executed is True
        _apply(result, state)
        assert state.player.current_room is None
        assert state.player.current_location == "guild"  # unchanged

    def test_leave_room_zero_time_cost(self) -> None:
        """leave_room has zero time cost."""
        state = _make_state(area="town", location="guild", room="hall")
        result = _make_engine().execute(
            Command(type="leave_room", params={}),
            state,
            _make_world(),
        )
        assert result.executed is True
        assert result.time_cost == 0.0

    def test_leave_room_fails_when_not_in_room(self) -> None:
        """leave_room fails when player is not in a room."""
        state = _make_state(area="town", location="guild", room=None)
        result = _make_engine().execute(
            Command(type="leave_room", params={}),
            state,
            _make_world(),
        )
        assert result.executed is False
        assert "not currently in a room" in _error_msg(result)

    def test_leave_room_metadata(self) -> None:
        """leave_room metadata contains the room being left."""
        state = _make_state(area="town", location="guild", room="hall")
        result = _make_engine().execute(
            Command(type="leave_room", params={}),
            state,
            _make_world(),
        )
        assert result.executed is True
        assert result.metadata["from_room"] == "hall"
        assert result.metadata["to_room"] is None


# ---------------------------------------------------------------------------
# 3-2: leave_sub_location clears current_room
# ---------------------------------------------------------------------------


class TestLeaveSubLocationClearsRoom:
    def test_leave_sub_location_clears_room_when_in_one(self) -> None:
        """Leaving a sub_location when in a room also clears current_room."""
        state = _make_state(area="town", location="guild", room="hall")
        result = _make_engine().execute(
            Command(type="leave_sub_location", params={}),
            state,
            _make_world(),
        )
        assert result.executed is True
        _apply(result, state)
        assert state.player.current_location is None
        assert state.player.current_room is None

    def test_leave_sub_location_works_when_not_in_room(self) -> None:
        """Leaving a sub_location when not in a room still works normally."""
        state = _make_state(area="town", location="guild", room=None)
        result = _make_engine().execute(
            Command(type="leave_sub_location", params={}),
            state,
            _make_world(),
        )
        assert result.executed is True
        _apply(result, state)
        assert state.player.current_location is None
        assert state.player.current_room is None


class TestCrossLocationNavigationClearsRoom:
    def test_move_area_clears_room(self) -> None:
        """Moving to another area resets current_room."""
        state = _make_state(area="town", location="guild", room="hall")
        result = _make_engine().execute(
            Command(type="move_area", params={"area_id": "start"}),
            state,
            _make_world(),
        )
        assert result.executed is True
        _apply(result, state)
        assert state.player.current_area == "start"
        assert state.player.current_location is None
        assert state.player.current_room is None

    def test_enter_sub_location_clears_room(self) -> None:
        """Entering a different sub_location resets current_room."""
        state = _make_state(area="town", location="guild", room="hall")
        result = _make_engine().execute(
            Command(type="enter_sub_location", params={"location_id": "temple"}),
            state,
            _make_world(),
        )
        assert result.executed is True
        _apply(result, state)
        assert state.player.current_location == "temple"
        assert state.player.current_room is None


# ---------------------------------------------------------------------------
# 3-3: is_colocated three-level filtering
# ---------------------------------------------------------------------------


class TestIsColocatedWithRooms:
    def test_player_not_in_room_any_npc_in_sub_location_is_visible(self) -> None:
        """When player has no room, all NPCs in same sub_location are visible."""
        assert is_colocated("guild", "guild", npc_room="hall", player_room=None) is True
        assert is_colocated("guild", "guild", npc_room=None, player_room=None) is True
        assert is_colocated("guild", "guild", npc_room="office", player_room=None) is True

    def test_player_in_room_only_same_room_npcs_visible(self) -> None:
        """When player is in a room, only NPCs in the same room are visible."""
        # NPC in same room → visible
        assert is_colocated("guild", "guild", npc_room="hall", player_room="hall") is True
        # NPC in different room → not visible
        assert is_colocated("guild", "guild", npc_room="office", player_room="hall") is False
        # NPC with no room → not visible when player is in a room
        assert is_colocated("guild", "guild", npc_room=None, player_room="hall") is False

    def test_different_sub_locations_always_not_colocated(self) -> None:
        """Different sub_locations are always not colocated regardless of room."""
        assert is_colocated("guild", "temple", npc_room="hall", player_room="hall") is False
        assert is_colocated("guild", "temple", npc_room=None, player_room=None) is False

    def test_backward_compat_no_room_args(self) -> None:
        """Callers that omit room args retain original two-level semantics."""
        assert is_colocated("guild", "guild") is True
        assert is_colocated("guild", "temple") is False
        assert is_colocated(None, None) is True
        assert is_colocated("guild", None) is False


# ---------------------------------------------------------------------------
# 3-5: Osiris _build_nearby_npcs room-level locality
# ---------------------------------------------------------------------------


def _make_osiris_context(
    *,
    area: str = "town",
    location: str | None = "guild",
    room: str | None = None,
    npc_rooms: dict[str, str | None] | None = None,
):
    """Build a minimal SettlementContext for _build_nearby_npcs testing."""
    from app.game_core.orchestration.scene_bus import SceneBus
    from app.game_core.orchestration.settlement import SettlementContext
    from app.game_core.rules import RulesEngine
    from app.game_core.state import StateDelta
    from app.game_core.state.slices import RelationSlice, SceneSlice

    world = _make_world()
    state = StateContainer()

    player = PlayerSlice()
    player.restore(
        {"current_area": area, "current_location": location, "current_room": room}
    )
    state.register(player)

    areas = AreaSlice()
    areas.restore({"areas": {}})
    # Place NPCs in sub_location
    if location is not None:
        areas.move_npc("npc_a", area, location)
        areas.move_npc("npc_b", area, location)
    else:
        areas.move_npc("npc_a", area, None)
        areas.move_npc("npc_b", area, None)
    if npc_rooms:
        for npc_id, r in npc_rooms.items():
            areas.set_npc_room(area, npc_id, r)
    state.register(areas)

    relations = RelationSlice()
    state.register(relations)

    scene = SceneSlice()
    scene.restore({})
    state.register(scene)
    scene_bus = SceneBus(scene)

    def _apply_delta(delta: StateDelta | None) -> None:
        pass

    ctx = SettlementContext(
        change_log=[],
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=RulesEngine(),
        _apply_delta=_apply_delta,
    )
    return ctx


class TestOsirisRoomLocality:
    """Verify AIOsirisHook._build_nearby_npcs filters by room when player is in one."""

    def test_no_room_filter_returns_all_sub_location_npcs(self) -> None:
        """Without room filter, all NPCs in same sub_location are returned."""
        from app.game_core.orchestration.hooks.ai_osiris import AIOsirisHook

        ctx = _make_osiris_context(
            area="town",
            location="guild",
            room=None,
            npc_rooms={"npc_a": "hall", "npc_b": "office"},
        )
        nearby = AIOsirisHook._build_nearby_npcs(ctx, "town", "guild", current_room=None)
        nearby_ids = {entry["id"] for entry in nearby}
        assert "npc_a" in nearby_ids
        assert "npc_b" in nearby_ids

    def test_room_filter_includes_only_same_room_npc(self) -> None:
        """With room filter, only NPC in same room is returned."""
        from app.game_core.orchestration.hooks.ai_osiris import AIOsirisHook

        ctx = _make_osiris_context(
            area="town",
            location="guild",
            room="hall",
            npc_rooms={"npc_a": "hall", "npc_b": "office"},
        )
        nearby = AIOsirisHook._build_nearby_npcs(ctx, "town", "guild", current_room="hall")
        nearby_ids = {entry["id"] for entry in nearby}
        assert "npc_a" in nearby_ids
        assert "npc_b" not in nearby_ids

    def test_room_filter_excludes_npc_with_no_room(self) -> None:
        """With room filter, NPC without a room assignment is excluded."""
        from app.game_core.orchestration.hooks.ai_osiris import AIOsirisHook

        ctx = _make_osiris_context(
            area="town",
            location="guild",
            room="hall",
            npc_rooms={"npc_b": "hall"},  # npc_a has no room
        )
        nearby = AIOsirisHook._build_nearby_npcs(ctx, "town", "guild", current_room="hall")
        nearby_ids = {entry["id"] for entry in nearby}
        assert "npc_b" in nearby_ids
        assert "npc_a" not in nearby_ids

    def test_no_location_returns_all_area_npcs_ignoring_room(self) -> None:
        """When player has no sub_location, room filter is not applied."""
        from app.game_core.orchestration.hooks.ai_osiris import AIOsirisHook

        ctx = _make_osiris_context(
            area="town",
            location=None,
            room=None,
            npc_rooms={"npc_a": "hall", "npc_b": "office"},
        )
        nearby = AIOsirisHook._build_nearby_npcs(ctx, "town", None, current_room=None)
        nearby_ids = {entry["id"] for entry in nearby}
        # Both NPCs are in area with no sub_location filter → all included
        assert "npc_a" in nearby_ids
        assert "npc_b" in nearby_ids
