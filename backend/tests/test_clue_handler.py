from __future__ import annotations

from app.game_core.bootstrap import build_default_world
from app.game_core.rules.handlers.clue import ClueHandler
from app.game_core.rules.models import Command
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, FlagSlice, PlayerSlice, TimeSlice


def _build_world():
    return build_default_world(
        "test_world",
        world_data={
            "maps": {
                "frontier_town": {
                    "id": "frontier_town",
                    "name": "边境小镇",
                    "sub_locations": {
                        "adventurer_guild": {
                            "id": "adventurer_guild",
                            "name": "冒险者公会",
                            "default_room": "guild_counter",
                            "rooms": {
                                "guild_counter": {
                                    "id": "guild_counter",
                                    "name": "受付柜台",
                                }
                            },
                        }
                    },
                }
            }
        },
    )


def _build_state() -> StateContainer:
    state = StateContainer()

    player = PlayerSlice()
    player.restore(
        {
            "current_area": "frontier_town",
            "current_location": "adventurer_guild",
            "current_room": "guild_counter",
            "stats": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 12, "cha": 10},
            "skill_proficiencies": ["investigation"],
        }
    )
    state.register(player)

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9, "absolute_tick": 12, "accumulated": 0.0})
    state.register(time_slice)

    flags = FlagSlice()
    flags.restore({})
    state.register(flags)

    areas = AreaSlice()
    areas.restore({"areas": {"frontier_town": {}}})
    areas.set_scoped_interactable_overlays(
        "frontier_town",
        "adventurer_guild__guild_counter",
        [
            {
                "id": "blood_trail_clue",
                "name": "拖拽血迹",
                "description": "半干的血迹断断续续地拖向后门。",
                "type": "inspect",
                "tags": ["clue"],
                "functional": {
                    "type": "investigate_clue",
                    "params": {
                        "clue_id": "blood_trail",
                        "on_first_inspect": [
                            {
                                "type": "set_flag",
                                "params": {"key": "clue_blood_trail_seen", "value": True},
                            }
                        ],
                        "options": [
                            {"id": "examine", "label": "仔细检查"},
                            {"id": "follow", "label": "顺着痕迹追过去", "check": {"skill": "investigation", "dc": 10}},
                        ],
                        "outcomes": {
                            "examine": [],
                            "follow": [
                                {
                                    "type": "unlock_sub_location",
                                    "params": {
                                        "id": "north_alley_hideout",
                                        "label": "北巷暗门",
                                        "description": "血迹尽头的一扇侧门。",
                                    },
                                }
                            ],
                        },
                    },
                },
            }
        ],
    )
    state.register(areas)
    return state


def test_investigate_clue_applies_first_inspect_effect_and_records_state() -> None:
    world = _build_world()
    state = _build_state()
    handler = ClueHandler()

    result = handler.compute(
        Command(
            type="investigate_clue",
            params={"interactable_id": "blood_trail_clue"},
            source="player",
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.delta is not None
    state.apply(result.delta)

    assert state.flags.get("clue_blood_trail_seen") is True
    clue_state = state.areas.get_area("frontier_town").interactable_states["blood_trail_clue"]
    assert clue_state["first_inspected"] is True
    assert clue_state["clue_id"] == "blood_trail"
    assert result.metadata["first_inspect_applied"] is True
    assert [option["id"] for option in result.metadata["options"]] == ["examine", "follow"]


def test_resolve_clue_option_unlocks_sub_location_and_hides_overlay() -> None:
    world = _build_world()
    state = _build_state()
    handler = ClueHandler()

    investigate = handler.compute(
        Command(
            type="investigate_clue",
            params={"interactable_id": "blood_trail_clue"},
            source="player",
        ),
        state,
        world,
    )
    assert investigate.delta is not None
    state.apply(investigate.delta)

    resolve = handler.compute(
        Command(
            type="resolve_clue_option",
            params={"interactable_id": "blood_trail_clue", "option_id": "follow"},
            source="player",
        ),
        state,
        world,
    )

    assert resolve.executed is True
    assert resolve.delta is not None
    state.apply(resolve.delta)

    assert resolve.metadata["removed_from_scene"] is True
    assert resolve.metadata["option_id"] == "follow"
    assert "unlock_sub_location" in resolve.metadata["effect_types"]
    clue_state = state.areas.get_area("frontier_town").interactable_states["blood_trail_clue"]
    assert clue_state["resolved_option_id"] == "follow"
    assert clue_state["resolved_at_tick"] == state.time.absolute_tick()
    assert state.areas.list_scoped_interactable_overlays(
        "frontier_town",
        "adventurer_guild",
        "guild_counter",
    ) == []
    unlocked = state.areas.list_temporary_sub_areas("frontier_town")
    assert any(item["id"] == "north_alley_hideout" for item in unlocked)
