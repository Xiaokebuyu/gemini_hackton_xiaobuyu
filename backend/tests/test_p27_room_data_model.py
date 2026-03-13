"""Tests for Phase 2: Room data model (P27).

Covers:
- RoomTemplate loading from maps registry
- SubLocationTemplate.rooms non-empty when rooms defined in data
- PlayerSlice current_room round-trip (restore/snapshot/apply_state_change)
- AreaState npc_rooms / discovered_rooms read-write
- StateChange paths: npc_room.{npc_id}, discovered_room.{sub_loc_id}.{room_id}
"""

from __future__ import annotations

from app.game_core.content.registries import MapRegistry
from app.game_core.state.delta import StateChange
from app.game_core.state.slices.area import AreaSlice, AreaState
from app.game_core.state.slices.player import PlayerSlice


# ---------------------------------------------------------------------------
# RoomTemplate loading
# ---------------------------------------------------------------------------


class TestRoomTemplateLoading:
    def _make_maps_with_rooms(self) -> MapRegistry:
        maps = MapRegistry()
        maps.load(
            {
                "town": {
                    "id": "town",
                    "sub_locations": {
                        "guild": {
                            "id": "guild",
                            "name": "Guild",
                            "default_room": "hall",
                            "rooms": {
                                "hall": {
                                    "id": "hall",
                                    "name": "Great Hall",
                                    "description": "A busy hall.",
                                    "discoverable": False,
                                    "discovery_dc": 0,
                                    "resident_npcs": ["npc_a"],
                                },
                                "office": {
                                    "id": "office",
                                    "name": "Guild Office",
                                    "description": "Quiet office.",
                                    "discoverable": True,
                                    "discovery_dc": 12,
                                },
                            },
                        }
                    },
                }
            }
        )
        return maps

    def test_room_template_loaded(self) -> None:
        maps = self._make_maps_with_rooms()
        area = maps.get("town")
        assert area is not None
        sub = area.sub_locations.get("guild")
        assert sub is not None
        assert "hall" in sub.rooms
        assert "office" in sub.rooms

    def test_room_template_fields(self) -> None:
        maps = self._make_maps_with_rooms()
        sub = maps.get("town").sub_locations["guild"]
        hall = sub.rooms["hall"]
        assert hall.id == "hall"
        assert hall.name == "Great Hall"
        assert hall.description == "A busy hall."
        assert hall.discoverable is False
        assert hall.discovery_dc == 0
        assert hall.resident_npcs == ["npc_a"]

    def test_discoverable_room_fields(self) -> None:
        maps = self._make_maps_with_rooms()
        sub = maps.get("town").sub_locations["guild"]
        office = sub.rooms["office"]
        assert office.discoverable is True
        assert office.discovery_dc == 12

    def test_sub_location_default_room_loaded(self) -> None:
        maps = self._make_maps_with_rooms()
        sub = maps.get("town").sub_locations["guild"]
        assert sub.default_room == "hall"

    def test_sub_location_without_rooms_has_empty_dict(self) -> None:
        maps = MapRegistry()
        maps.load(
            {
                "town": {
                    "id": "town",
                    "sub_locations": {
                        "square": {"id": "square", "name": "Town Square"}
                    },
                }
            }
        )
        sub = maps.get("town").sub_locations["square"]
        assert sub.rooms == {}
        assert sub.default_room == ""

    def test_room_list_format_also_loads(self) -> None:
        """rooms can also be a list of room objects (not just dict)."""
        maps = MapRegistry()
        maps.load(
            {
                "inn": {
                    "id": "inn",
                    "sub_locations": {
                        "main": {
                            "id": "main",
                            "name": "Main",
                            "rooms": [
                                {"id": "lobby", "name": "Lobby"},
                                {"id": "back_room", "name": "Back Room", "discoverable": True},
                            ],
                        }
                    },
                }
            }
        )
        sub = maps.get("inn").sub_locations["main"]
        assert "lobby" in sub.rooms
        assert "back_room" in sub.rooms
        assert sub.rooms["back_room"].discoverable is True

    def test_invalid_default_room_records_issue(self) -> None:
        maps = MapRegistry()
        maps.load(
            {
                "town": {
                    "id": "town",
                    "sub_locations": {
                        "guild": {
                            "id": "guild",
                            "name": "Guild",
                            "default_room": "   ",  # whitespace-only = invalid
                        }
                    },
                }
            }
        )
        issues = maps.validate()
        assert any("default_room" in issue for issue in issues)


# ---------------------------------------------------------------------------
# goblin_slayer world data: rooms loaded correctly
# ---------------------------------------------------------------------------


class TestGoblinSlayerRooms:
    def _load_maps(self) -> MapRegistry:
        import json
        import os

        data_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "data",
            "goblin_slayer",
            "v2",
            "maps.json",
        )
        with open(os.path.abspath(data_path)) as f:
            raw = json.load(f)
        maps = MapRegistry()
        maps.load(raw)
        return maps

    def test_adventurer_guild_has_rooms(self) -> None:
        maps = self._load_maps()
        area = maps.get("frontier_town")
        assert area is not None
        sub = area.sub_locations.get("adventurer_guild")
        assert sub is not None
        assert len(sub.rooms) > 0
        assert "guild_hall" in sub.rooms
        assert "guild_counter" in sub.rooms
        assert "private_room" in sub.rooms

    def test_adventurer_guild_default_room(self) -> None:
        maps = self._load_maps()
        sub = maps.get("frontier_town").sub_locations["adventurer_guild"]
        assert sub.default_room == "guild_hall"

    def test_guild_hall_is_not_discoverable(self) -> None:
        maps = self._load_maps()
        sub = maps.get("frontier_town").sub_locations["adventurer_guild"]
        assert sub.rooms["guild_hall"].discoverable is False

    def test_private_room_is_discoverable(self) -> None:
        maps = self._load_maps()
        sub = maps.get("frontier_town").sub_locations["adventurer_guild"]
        assert sub.rooms["private_room"].discoverable is True

    def test_mother_earth_temple_has_rooms(self) -> None:
        maps = self._load_maps()
        sub = maps.get("frontier_town").sub_locations.get("mother_earth_temple")
        assert sub is not None
        assert "prayer_hall" in sub.rooms
        assert "inner_sanctum" in sub.rooms
        assert sub.default_room == "prayer_hall"

    def test_tavern_has_rooms(self) -> None:
        maps = self._load_maps()
        sub = maps.get("frontier_town").sub_locations.get("tavern")
        assert sub is not None
        assert "main_hall" in sub.rooms
        assert "upstairs_rooms" in sub.rooms
        assert sub.default_room == "main_hall"


# ---------------------------------------------------------------------------
# PlayerSlice current_room round-trip
# ---------------------------------------------------------------------------


class TestPlayerSliceCurrentRoom:
    def test_current_room_default_is_none(self) -> None:
        player = PlayerSlice()
        assert player.current_room is None

    def test_current_room_restored_from_payload(self) -> None:
        player = PlayerSlice()
        player.restore({"current_room": "guild_hall"})
        assert player.current_room == "guild_hall"

    def test_current_room_restored_as_none_when_absent(self) -> None:
        player = PlayerSlice()
        player.restore({})
        assert player.current_room is None

    def test_current_room_restored_as_none_when_null(self) -> None:
        player = PlayerSlice()
        player.restore({"current_room": None})
        assert player.current_room is None

    def test_current_room_in_snapshot(self) -> None:
        player = PlayerSlice()
        player.restore({"current_room": "guild_hall"})
        snap = player.snapshot()
        assert snap["current_room"] == "guild_hall"

    def test_current_room_none_in_snapshot(self) -> None:
        player = PlayerSlice()
        snap = player.snapshot()
        assert snap["current_room"] is None

    def test_apply_state_change_sets_current_room(self) -> None:
        player = PlayerSlice()
        change = StateChange(slice="player", operation="set", path="current_room", value="office")
        player.apply_state_change(change)
        assert player.current_room == "office"
        assert player._dirty is True

    def test_apply_state_change_clears_current_room(self) -> None:
        player = PlayerSlice()
        player.restore({"current_room": "guild_hall"})
        change = StateChange(slice="player", operation="set", path="current_room", value=None)
        player.apply_state_change(change)
        assert player.current_room is None

    def test_current_room_round_trip(self) -> None:
        """Snapshot → restore preserves current_room."""
        player = PlayerSlice()
        player.restore({"current_room": "guild_hall", "current_area": "frontier_town"})
        snap = player.snapshot()

        player2 = PlayerSlice()
        player2.restore(snap)
        assert player2.current_room == "guild_hall"
        assert player2.current_area == "frontier_town"


# ---------------------------------------------------------------------------
# AreaSlice npc_rooms / discovered_rooms read-write
# ---------------------------------------------------------------------------


class TestAreaSliceRooms:
    def test_npc_rooms_default_empty(self) -> None:
        areas = AreaSlice()
        area = areas.get_area("town")
        assert area.npc_rooms == {}

    def test_set_npc_room(self) -> None:
        areas = AreaSlice()
        areas.set_npc_room("town", "guild_girl", "guild_counter")
        assert areas.get_npc_room("town", "guild_girl") == "guild_counter"
        assert areas._dirty is True

    def test_set_npc_room_to_none(self) -> None:
        areas = AreaSlice()
        areas.set_npc_room("town", "guild_girl", "guild_counter")
        areas.set_npc_room("town", "guild_girl", None)
        assert areas.get_npc_room("town", "guild_girl") is None

    def test_get_npc_room_nonexistent_area(self) -> None:
        areas = AreaSlice()
        assert areas.get_npc_room("nonexistent", "npc") is None

    def test_get_npc_room_nonexistent_npc(self) -> None:
        areas = AreaSlice()
        areas.get_area("town")  # ensure area created
        assert areas.get_npc_room("town", "unknown_npc") is None

    def test_discovered_rooms_default_empty(self) -> None:
        areas = AreaSlice()
        area = areas.get_area("town")
        assert area.discovered_rooms == set()

    def test_mark_room_discovered(self) -> None:
        areas = AreaSlice()
        areas.mark_room_discovered("town", "adventurer_guild", "private_room")
        assert areas.is_room_discovered("town", "adventurer_guild", "private_room") is True
        assert areas._dirty is True

    def test_is_room_discovered_false_when_not_marked(self) -> None:
        areas = AreaSlice()
        assert areas.is_room_discovered("town", "adventurer_guild", "private_room") is False

    def test_is_room_discovered_nonexistent_area(self) -> None:
        areas = AreaSlice()
        assert areas.is_room_discovered("nonexistent", "sub", "room") is False

    def test_get_discovered_rooms_returns_defensive_copy(self) -> None:
        areas = AreaSlice()
        areas.mark_room_discovered("town", "adventurer_guild", "private_room")
        rooms = areas.get_discovered_rooms("town")
        rooms.add("should_not_affect_original")
        assert "should_not_affect_original__bogus" not in areas.get_area("town").discovered_rooms

    def test_npc_rooms_in_snapshot(self) -> None:
        areas = AreaSlice()
        areas.set_npc_room("frontier_town", "guild_girl", "guild_counter")
        snap = areas.snapshot()
        assert snap["areas"]["frontier_town"]["npc_rooms"]["guild_girl"] == "guild_counter"

    def test_discovered_rooms_in_snapshot(self) -> None:
        areas = AreaSlice()
        areas.mark_room_discovered("frontier_town", "adventurer_guild", "private_room")
        snap = areas.snapshot()
        assert "adventurer_guild__private_room" in snap["areas"]["frontier_town"]["discovered_rooms"]

    def test_npc_rooms_round_trip(self) -> None:
        """Snapshot → restore preserves npc_rooms."""
        areas = AreaSlice()
        areas.set_npc_room("frontier_town", "guild_girl", "guild_counter")
        snap = areas.snapshot()

        areas2 = AreaSlice()
        areas2.restore(snap)
        assert areas2.get_npc_room("frontier_town", "guild_girl") == "guild_counter"

    def test_discovered_rooms_round_trip(self) -> None:
        """Snapshot → restore preserves discovered_rooms."""
        areas = AreaSlice()
        areas.mark_room_discovered("frontier_town", "adventurer_guild", "private_room")
        snap = areas.snapshot()

        areas2 = AreaSlice()
        areas2.restore(snap)
        assert areas2.is_room_discovered("frontier_town", "adventurer_guild", "private_room") is True


# ---------------------------------------------------------------------------
# AreaSlice apply_state_change for new paths
# ---------------------------------------------------------------------------


class TestAreaSliceStateChangePaths:
    def test_apply_npc_room_set(self) -> None:
        areas = AreaSlice()
        areas.get_area("frontier_town")  # ensure area exists
        change = StateChange(
            slice="areas",
            operation="set",
            path="frontier_town.npc_room.guild_girl",
            value="guild_counter",
        )
        areas.apply_state_change(change)
        assert areas.get_npc_room("frontier_town", "guild_girl") == "guild_counter"

    def test_apply_npc_room_set_none(self) -> None:
        areas = AreaSlice()
        areas.set_npc_room("frontier_town", "guild_girl", "guild_counter")
        change = StateChange(
            slice="areas",
            operation="set",
            path="frontier_town.npc_room.guild_girl",
            value=None,
        )
        areas.apply_state_change(change)
        assert areas.get_npc_room("frontier_town", "guild_girl") is None

    def test_apply_discovered_room_set(self) -> None:
        areas = AreaSlice()
        areas.get_area("frontier_town")  # ensure area exists
        change = StateChange(
            slice="areas",
            operation="set",
            path="frontier_town.discovered_room.adventurer_guild.private_room",
            value=True,
        )
        areas.apply_state_change(change)
        assert areas.is_room_discovered("frontier_town", "adventurer_guild", "private_room") is True

    def test_apply_discovered_room_add(self) -> None:
        areas = AreaSlice()
        areas.get_area("frontier_town")  # ensure area exists
        change = StateChange(
            slice="areas",
            operation="add",
            path="frontier_town.discovered_room.adventurer_guild.private_room",
            value=True,
        )
        areas.apply_state_change(change)
        assert areas.is_room_discovered("frontier_town", "adventurer_guild", "private_room") is True
