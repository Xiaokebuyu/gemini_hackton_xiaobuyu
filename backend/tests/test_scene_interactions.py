"""Tests for merged scene interactables, donation flow, and duplicate migration."""

from __future__ import annotations

from types import SimpleNamespace

from app.game_core.content import WorldInstance
from app.game_core.content.registries import CharacterRegistry, MapRegistry
from app.game_core.rules import Command
from app.game_core.rules.handlers import DonationHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, PlayerSlice, RelationSlice, TimeSlice
from app.scene_views import build_location_overview


def _build_scene_world() -> WorldInstance:
    world = WorldInstance("test_world")
    maps = MapRegistry()
    maps.load(
        {
            "frontier_town": {
                "id": "frontier_town",
                "name": "边境小镇",
                "sub_locations": {
                    "adventurer_guild": {
                        "id": "adventurer_guild",
                        "name": "冒险者公会",
                        "interactables": [
                            {
                                "id": "quest_board",
                                "name": "委托板",
                                "description": "大厅里的旧委托板。",
                                "type": "readable",
                                "functional": {"type": "board_browse"},
                            }
                        ],
                        "rooms": {
                            "guild_counter": {
                                "id": "guild_counter",
                                "name": "柜台",
                                "interactables": [
                                    {
                                        "id": "quest_board",
                                        "name": "委托板",
                                        "description": "柜台旁的委托板。",
                                        "type": "readable",
                                        "functional": {"type": "board_browse"},
                                    }
                                ],
                            }
                        },
                    },
                    "mother_earth_temple": {
                        "id": "mother_earth_temple",
                        "name": "地母神殿",
                        "default_room": "prayer_hall",
                        "resident_npcs": ["priestess"],
                        "rooms": {
                            "prayer_hall": {
                                "id": "prayer_hall",
                                "name": "礼拜堂",
                                "resident_npcs": ["priestess"],
                                "interactables": [
                                    {
                                        "id": "charity_box",
                                        "name": "奉献箱",
                                        "description": "木制奉献箱。",
                                        "type": "inspect",
                                        "functional": {"type": "donation"},
                                    }
                                ],
                            }
                        },
                    },
                },
            }
        }
    )
    world.register(maps)

    characters = CharacterRegistry()
    characters.load(
        {
            "priestess": {
                "id": "priestess",
                "name": "女神官",
                "area_id": "frontier_town",
                "location_id": "mother_earth_temple",
                "shop": {
                    "services": [
                        {"service_id": "donation", "label": "奉献捐赠", "price": 5}
                    ]
                },
            }
        }
    )
    world.register(characters)
    return world


def _build_state(
    *,
    current_location: str,
    current_room: str | None = None,
    gold: int = 30,
    area_payload: dict | None = None,
) -> StateContainer:
    state = StateContainer()

    player = PlayerSlice()
    player.restore(
        {
            "current_area": "frontier_town",
            "current_location": current_location,
            "current_room": current_room,
            "gold": gold,
        }
    )
    state.register(player)

    time_slice = TimeSlice()
    time_slice.restore({"day": 2, "slot": 9})
    state.register(time_slice)

    areas = AreaSlice()
    areas.restore(area_payload or {"areas": {"frontier_town": {}}})
    state.register(areas)

    relations = RelationSlice()
    relations.restore({})
    state.register(relations)

    return state


def test_build_location_overview_merges_room_location_and_overlay_actions() -> None:
    world = _build_scene_world()
    state = _build_state(
        current_location="adventurer_guild",
        current_room="guild_counter",
        area_payload={
            "areas": {
                "frontier_town": {
                    "scoped_interactable_overlays": {
                        "adventurer_guild__guild_counter": [
                            {
                                "id": "quest_board",
                                "name": "委托板",
                                "description": "新的悬赏单覆盖了旧纸页。",
                                "type": "readable",
                            }
                        ]
                    }
                }
            }
        },
    )
    session = SimpleNamespace(runtime=SimpleNamespace(state=state, world=world))

    overview = build_location_overview(session)

    assert len(overview["interactables"]) == 1
    board = overview["interactables"][0]
    assert board["id"] == "quest_board"
    assert board["description_hint"] == "新的悬赏单覆盖了旧纸页。"
    assert board["interaction_kind"] == "board"
    assert board["primary_action"] == {
        "action_type": "browse_board",
        "params": {"board_id": "quest_board"},
    }


def test_build_location_overview_respects_explicit_functional_action_type() -> None:
    world = _build_scene_world()
    world.maps.get("frontier_town").sub_locations["mother_earth_temple"].rooms[
        "prayer_hall"
    ].interactables.append(
        SimpleNamespace(
            id="prayer_mat",
            name="祈祷垫",
            description="编织草垫前放着一张小牌。",
            type="use",
            tags=[],
            checks=[],
            visibility_dc=None,
            reward=None,
            one_time=False,
            functional={
                "type": "ritual_prayer",
                "action_type": "pray_at_shrine",
                "params": {"ritual_id": "mother_earth_daily_prayer"},
            },
            container_data=None,
        )
    )
    state = _build_state(
        current_location="mother_earth_temple",
        current_room="prayer_hall",
    )
    session = SimpleNamespace(runtime=SimpleNamespace(state=state, world=world))

    overview = build_location_overview(session)
    prayer_mat = next(
        entry for entry in overview["interactables"] if entry["id"] == "prayer_mat"
    )

    assert prayer_mat["primary_action"] == {
        "action_type": "pray_at_shrine",
        "params": {"ritual_id": "mother_earth_daily_prayer"},
    }


def test_build_location_overview_accepts_action_type_without_functional_type() -> None:
    world = _build_scene_world()
    world.maps.get("frontier_town").sub_locations["mother_earth_temple"].rooms[
        "prayer_hall"
    ].interactables.append(
        SimpleNamespace(
            id="offering_candle",
            name="供灯",
            description="烛台前挂着一小串祷词牌。",
            type="use",
            tags=[],
            checks=[],
            visibility_dc=None,
            reward=None,
            one_time=False,
            functional={
                "action_type": "light_offering_candle",
                "params": {"ritual_id": "mother_earth_evening_light"},
            },
            container_data=None,
        )
    )
    state = _build_state(
        current_location="mother_earth_temple",
        current_room="prayer_hall",
    )
    session = SimpleNamespace(runtime=SimpleNamespace(state=state, world=world))

    overview = build_location_overview(session)
    candle = next(
        entry for entry in overview["interactables"] if entry["id"] == "offering_candle"
    )

    assert candle["primary_action"] == {
        "action_type": "light_offering_candle",
        "params": {"ritual_id": "mother_earth_evening_light"},
    }


def test_build_location_overview_maps_clue_functional_to_investigate_action() -> None:
    world = _build_scene_world()
    world.maps.get("frontier_town").sub_locations["adventurer_guild"].rooms[
        "guild_counter"
    ].interactables.append(
        SimpleNamespace(
            id="blood_trail_clue",
            name="拖拽血迹",
            description="一串血点在柜台脚边向后门断断续续地延伸。",
            type="inspect",
            tags=["clue"],
            checks=[],
            visibility_dc=None,
            reward=None,
            one_time=False,
            functional={
                "type": "investigate_clue",
                "params": {
                    "clue_id": "blood_trail",
                    "options": [
                        {"id": "examine", "label": "仔细检查"},
                        {"id": "follow", "label": "顺着痕迹追过去"},
                    ],
                    "outcomes": {
                        "examine": [],
                        "follow": [],
                    },
                },
            },
            container_data=None,
        )
    )
    state = _build_state(
        current_location="adventurer_guild",
        current_room="guild_counter",
    )
    session = SimpleNamespace(runtime=SimpleNamespace(state=state, world=world))

    overview = build_location_overview(session)
    clue = next(
        entry for entry in overview["interactables"] if entry["id"] == "blood_trail_clue"
    )

    assert clue["primary_action"] == {
        "action_type": "investigate_clue",
        "params": {
            "clue_id": "blood_trail",
            "interactable_id": "blood_trail_clue",
            "options": [
                {"id": "examine", "label": "仔细检查"},
                {"id": "follow", "label": "顺着痕迹追过去"},
            ],
            "outcomes": {
                "examine": [],
                "follow": [],
            },
        },
    }


def test_area_slice_restores_duplicate_facility_into_scene_overlay() -> None:
    area_slice = AreaSlice()
    area_slice.restore(
        {
            "areas": {
                "frontier_town": {
                    "temporary_sub_areas": [
                        {
                            "id": "charity_box",
                            "label": "慈悲捐献箱",
                            "description": "铜边木箱上刻着地母徽记。",
                            "interactables": [
                                {
                                    "id": "donation_slot",
                                    "name": "投币口",
                                    "description": "狭长的投入口。",
                                }
                            ],
                        }
                    ]
                }
            }
        }
    )

    assert area_slice.list_temporary_sub_areas("frontier_town") == []
    overlays = area_slice.list_scoped_interactable_overlays(
        "frontier_town",
        "mother_earth_temple",
        "prayer_hall",
    )
    assert len(overlays) == 1
    assert overlays[0]["id"] == "charity_box"
    assert overlays[0]["description"] == "狭长的投入口。"


def test_donation_handler_accepts_interactable_donation_and_awards_daily_trust() -> None:
    world = _build_scene_world()
    state = _build_state(
        current_location="mother_earth_temple",
        current_room="prayer_hall",
        gold=30,
    )
    handler = DonationHandler()

    result = handler.compute(
        Command(
            type="donate",
            params={
                "target_kind": "interactable",
                "target_id": "charity_box",
                "amount": 10,
            },
            source="player",
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.delta is not None
    state.apply(result.delta)
    assert state.player.gold == 20
    assert state.areas.get_area("frontier_town").properties["temple_donation_total"] == 10
    assert state.areas.get_area("frontier_town").properties["temple_last_donation_day"] == 2
    assert state.relations.npc_dispositions["priestess"]["trust"] == 1
    assert result.metadata["remaining_gold"] == 20
    assert result.metadata["first_donation_today"] is True


def test_donation_handler_accepts_npc_donation_without_second_daily_trust() -> None:
    world = _build_scene_world()
    state = _build_state(
        current_location="mother_earth_temple",
        current_room="prayer_hall",
        gold=25,
        area_payload={
            "areas": {
                "frontier_town": {
                    "properties": {
                        "temple_donation_total": 10,
                        "temple_last_donation_day": 2,
                    }
                }
            }
        },
    )
    handler = DonationHandler()

    result = handler.compute(
        Command(
            type="donate",
            params={
                "target_kind": "npc",
                "target_id": "priestess",
                "amount": 5,
            },
            source="player",
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.delta is not None
    state.apply(result.delta)
    assert state.player.gold == 20
    assert state.areas.get_area("frontier_town").properties["temple_donation_total"] == 15
    assert result.metadata["first_donation_today"] is False
    assert result.metadata["trust_delta"] == 0
