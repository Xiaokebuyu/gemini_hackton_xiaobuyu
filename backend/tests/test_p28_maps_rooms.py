"""Tests for P28 Track B-1: maps.json sub-location room expansion.

Verifies that frontier_town now has 9 sub-locations with 35 rooms,
cow_girl_farm has 3 sub-locations with 6 rooms, and all rooms load
correctly through MapRegistry.
"""

from __future__ import annotations

import json
import os

import pytest

from app.game_core.content.registries import MapRegistry


def _load_maps() -> MapRegistry:
    data_path = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "data",
            "goblin_slayer",
            "v2",
            "maps.json",
        )
    )
    with open(data_path) as f:
        raw = json.load(f)
    maps = MapRegistry()
    maps.load(raw)
    return maps


# ---------------------------------------------------------------------------
# frontier_town structure
# ---------------------------------------------------------------------------


class TestFrontierTownStructure:
    def test_frontier_town_has_9_sub_locations(self) -> None:
        maps = _load_maps()
        area = maps.get("frontier_town")
        assert area is not None
        assert len(area.sub_locations) == 9

    def test_frontier_town_has_35_rooms(self) -> None:
        maps = _load_maps()
        area = maps.get("frontier_town")
        total = sum(len(sub.rooms) for sub in area.sub_locations.values())
        assert total == 35

    def test_all_sub_locations_have_default_room(self) -> None:
        maps = _load_maps()
        area = maps.get("frontier_town")
        for sub_id, sub in area.sub_locations.items():
            assert sub.default_room, f"{sub_id} missing default_room"

    def test_all_default_rooms_exist_in_rooms(self) -> None:
        maps = _load_maps()
        area = maps.get("frontier_town")
        for sub_id, sub in area.sub_locations.items():
            if sub.default_room:
                assert sub.default_room in sub.rooms, (
                    f"{sub_id}.default_room={sub.default_room!r} not in rooms"
                )


# ---------------------------------------------------------------------------
# adventurer_guild +2 rooms
# ---------------------------------------------------------------------------


class TestAdventurerGuildNewRooms:
    def test_archive_room_loaded(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["adventurer_guild"]
        assert "archive_room" in sub.rooms

    def test_archive_room_discoverable(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["adventurer_guild"]
        room = sub.rooms["archive_room"]
        assert room.discoverable is True
        assert room.discovery_dc == 10

    def test_basement_armory_loaded(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["adventurer_guild"]
        assert "basement_armory" in sub.rooms

    def test_basement_armory_discoverable(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["adventurer_guild"]
        room = sub.rooms["basement_armory"]
        assert room.discoverable is True
        assert room.discovery_dc == 12

    def test_adventurer_guild_now_has_5_rooms(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["adventurer_guild"]
        assert len(sub.rooms) == 5


# ---------------------------------------------------------------------------
# mother_earth_temple +2 rooms
# ---------------------------------------------------------------------------


class TestMotherEarthTempleNewRooms:
    def test_herb_garden_loaded(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["mother_earth_temple"]
        assert "herb_garden" in sub.rooms

    def test_herb_garden_not_discoverable(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["mother_earth_temple"]
        assert sub.rooms["herb_garden"].discoverable is False

    def test_confession_room_loaded(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["mother_earth_temple"]
        assert "confession_room" in sub.rooms

    def test_confession_room_discoverable_dc0(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["mother_earth_temple"]
        room = sub.rooms["confession_room"]
        assert room.discoverable is True
        assert room.discovery_dc == 0

    def test_temple_now_has_4_rooms(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["mother_earth_temple"]
        assert len(sub.rooms) == 4


# ---------------------------------------------------------------------------
# tavern +3 rooms
# ---------------------------------------------------------------------------


class TestTavernNewRooms:
    def test_kitchen_loaded(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["tavern"]
        assert "kitchen" in sub.rooms

    def test_wine_cellar_loaded_and_discoverable(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["tavern"]
        room = sub.rooms["wine_cellar"]
        assert room.discoverable is True
        assert room.discovery_dc == 8

    def test_back_yard_loaded_not_discoverable(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["tavern"]
        assert sub.rooms["back_yard"].discoverable is False

    def test_tavern_now_has_5_rooms(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["tavern"]
        assert len(sub.rooms) == 5


# ---------------------------------------------------------------------------
# blacksmith_shop rooms (newly added)
# ---------------------------------------------------------------------------


class TestBlacksmithShopRooms:
    def test_blacksmith_has_4_rooms(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["blacksmith_shop"]
        assert len(sub.rooms) == 4

    def test_default_room_is_forge(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["blacksmith_shop"]
        assert sub.default_room == "forge"

    def test_forge_has_blacksmith_resident(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["blacksmith_shop"]
        assert "blacksmith" in sub.rooms["forge"].resident_npcs

    def test_hidden_vault_discoverable_dc14(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["blacksmith_shop"]
        room = sub.rooms["hidden_vault"]
        assert room.discoverable is True
        assert room.discovery_dc == 14


# ---------------------------------------------------------------------------
# town_square rooms (newly added)
# ---------------------------------------------------------------------------


class TestTownSquareRooms:
    def test_town_square_has_4_rooms(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["town_square"]
        assert len(sub.rooms) == 4

    def test_default_room_is_fountain_plaza(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["town_square"]
        assert sub.default_room == "fountain_plaza"

    def test_fountain_plaza_has_town_guard_resident(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["town_square"]
        assert "town_guard" in sub.rooms["fountain_plaza"].resident_npcs

    def test_old_well_discoverable_dc10(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["town_square"]
        room = sub.rooms["old_well"]
        assert room.discoverable is True
        assert room.discovery_dc == 10


# ---------------------------------------------------------------------------
# north_gate rooms (newly added)
# ---------------------------------------------------------------------------


class TestNorthGateRooms:
    def test_north_gate_has_3_rooms(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["north_gate"]
        assert len(sub.rooms) == 3

    def test_default_room_is_guard_post(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["north_gate"]
        assert sub.default_room == "guard_post"

    def test_guard_post_has_warden_resident(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["north_gate"]
        assert "north_gate_warden" in sub.rooms["guard_post"].resident_npcs

    def test_watchtower_is_discoverable(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["north_gate"]
        room = sub.rooms["watchtower"]
        assert room.discoverable is True
        assert room.discovery_dc == 0


# ---------------------------------------------------------------------------
# market_plaza (new sub_location)
# ---------------------------------------------------------------------------


class TestMarketPlaza:
    def test_market_plaza_exists(self) -> None:
        maps = _load_maps()
        area = maps.get("frontier_town")
        assert "market_plaza" in area.sub_locations

    def test_market_plaza_has_4_rooms(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["market_plaza"]
        assert len(sub.rooms) == 4

    def test_default_room_is_open_market(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["market_plaza"]
        assert sub.default_room == "open_market"

    def test_open_market_has_traveling_merchant(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["market_plaza"]
        assert "traveling_merchant" in sub.rooms["open_market"].resident_npcs

    def test_back_stall_discoverable_dc12(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["market_plaza"]
        room = sub.rooms["back_stall"]
        assert room.discoverable is True
        assert room.discovery_dc == 12


# ---------------------------------------------------------------------------
# training_ground (new sub_location)
# ---------------------------------------------------------------------------


class TestTrainingGround:
    def test_training_ground_exists(self) -> None:
        maps = _load_maps()
        area = maps.get("frontier_town")
        assert "training_ground" in area.sub_locations

    def test_training_ground_has_3_rooms(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["training_ground"]
        assert len(sub.rooms) == 3

    def test_default_room_is_sparring_ring(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["training_ground"]
        assert sub.default_room == "sparring_ring"

    def test_equipment_shed_discoverable(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["training_ground"]
        assert sub.rooms["equipment_shed"].discoverable is True

    def test_sparring_ring_not_discoverable(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["training_ground"]
        assert sub.rooms["sparring_ring"].discoverable is False


# ---------------------------------------------------------------------------
# back_alley (new sub_location)
# ---------------------------------------------------------------------------


class TestBackAlley:
    def test_back_alley_exists(self) -> None:
        maps = _load_maps()
        area = maps.get("frontier_town")
        assert "back_alley" in area.sub_locations

    def test_back_alley_has_3_rooms(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["back_alley"]
        assert len(sub.rooms) == 3

    def test_default_room_is_narrow_path(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["back_alley"]
        assert sub.default_room == "narrow_path"

    def test_dead_end_has_informant(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["back_alley"]
        assert "informant" in sub.rooms["dead_end"].resident_npcs

    def test_sewer_entrance_discoverable_dc14(self) -> None:
        maps = _load_maps()
        sub = maps.get("frontier_town").sub_locations["back_alley"]
        room = sub.rooms["sewer_entrance"]
        assert room.discoverable is True
        assert room.discovery_dc == 14


# ---------------------------------------------------------------------------
# cow_girl_farm rooms
# ---------------------------------------------------------------------------


class TestCowGirlFarmRooms:
    def test_cow_girl_farm_has_3_sub_locations(self) -> None:
        maps = _load_maps()
        area = maps.get("cow_girl_farm")
        assert len(area.sub_locations) == 3

    def test_cow_girl_farm_has_6_total_rooms(self) -> None:
        maps = _load_maps()
        area = maps.get("cow_girl_farm")
        total = sum(len(sub.rooms) for sub in area.sub_locations.values())
        assert total == 6

    def test_main_house_living_room_has_cow_girl(self) -> None:
        maps = _load_maps()
        sub = maps.get("cow_girl_farm").sub_locations["main_house"]
        assert sub.default_room == "living_room"
        assert "cow_girl" in sub.rooms["living_room"].resident_npcs

    def test_main_house_has_dining_area(self) -> None:
        maps = _load_maps()
        sub = maps.get("cow_girl_farm").sub_locations["main_house"]
        assert "dining_area" in sub.rooms

    def test_gs_warehouse_default_room_is_storage_area(self) -> None:
        maps = _load_maps()
        sub = maps.get("cow_girl_farm").sub_locations["gs_warehouse"]
        assert sub.default_room == "storage_area"
        assert "workbench" in sub.rooms

    def test_farm_field_default_room_is_pasture(self) -> None:
        maps = _load_maps()
        sub = maps.get("cow_girl_farm").sub_locations["farm_field"]
        assert sub.default_room == "pasture"
        assert "well_area" in sub.rooms

    def test_all_cow_girl_farm_sub_locations_have_default_room(self) -> None:
        maps = _load_maps()
        area = maps.get("cow_girl_farm")
        for sub_id, sub in area.sub_locations.items():
            assert sub.default_room, f"{sub_id} missing default_room"
