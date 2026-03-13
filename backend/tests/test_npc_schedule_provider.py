"""Tests for BasicNpcScheduleProvider per-character schedule support (O-4)."""

from __future__ import annotations

from app.game_core.content.registries.characters import CharacterTemplate
from app.game_core.orchestration.hooks.npc_schedule import BasicNpcScheduleProvider


def _make_provider() -> BasicNpcScheduleProvider:
    return BasicNpcScheduleProvider()


def _dusk_context(
    characters: list[dict],
    areas: dict | None = None,
) -> dict:
    if areas is None:
        areas = {
            "town": {"npc_locations": {}},
            "tavern": {"npc_locations": {}},
            "barracks": {"npc_locations": {}},
            "market": {"npc_locations": {}},
        }
    return {
        "period_change_pending": True,
        "next_period": "dusk",
        "areas": areas,
        "characters": characters,
        "party_members": [],
    }


def _dawn_context(
    characters: list[dict],
    areas: dict | None = None,
) -> dict:
    if areas is None:
        areas = {
            "town": {"npc_locations": {}},
            "tavern": {"npc_locations": {}},
            "barracks": {"npc_locations": {}},
            "market": {"npc_locations": {}},
        }
    return {
        "period_change_pending": True,
        "next_period": "dawn",
        "areas": areas,
        "characters": characters,
        "party_members": [],
    }


# ------------------------------------------------------------------
# O-4 schedule field in CharacterTemplate
# ------------------------------------------------------------------


def test_character_template_schedule_field() -> None:
    """CharacterTemplate should support schedule field and serialize correctly."""
    ct = CharacterTemplate(id="guard", schedule={"night": "barracks", "dawn": "market"})
    as_dict = {f.name: getattr(ct, f.name) for f in ct.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    assert as_dict["schedule"] == {"night": "barracks", "dawn": "market"}


def test_character_template_schedule_defaults_to_none() -> None:
    """CharacterTemplate without schedule should default to None."""
    ct = CharacterTemplate(id="npc1")
    assert ct.schedule is None


# ------------------------------------------------------------------
# _scheduled_destination helper
# ------------------------------------------------------------------


def test_scheduled_destination_returns_sub_location() -> None:
    """String schedule value → (None, location_id, None)."""
    char = {"id": "bartender", "schedule": {"dusk": "tavern"}}
    dest = BasicNpcScheduleProvider._scheduled_destination(char, "dusk", {"town"})
    assert dest == (None, "tavern", None)


def test_scheduled_destination_returns_cross_area() -> None:
    """Dict schedule value → (area_id, location_id, room_id)."""
    char = {"id": "gs", "schedule": {"night": {"area": "farm", "location": "warehouse"}}}
    dest = BasicNpcScheduleProvider._scheduled_destination(char, "night", {"town", "farm"})
    assert dest == ("farm", "warehouse", None)


def test_scheduled_destination_returns_room_aware_mapping() -> None:
    char = {
        "id": "guild_girl",
        "schedule": {"day": {"location": "adventurer_guild", "room": "guild_counter"}},
    }
    dest = BasicNpcScheduleProvider._scheduled_destination(char, "day", {"frontier_town"})
    assert dest == (None, "adventurer_guild", "guild_counter")


def test_scheduled_destination_returns_none_when_no_schedule() -> None:
    char = {"id": "wanderer"}
    dest = BasicNpcScheduleProvider._scheduled_destination(char, "dusk", {"town"})
    assert dest == (None, None, None)


def test_scheduled_destination_returns_none_for_missing_period() -> None:
    """No entry for the requested period → (None, None, None)."""
    char = {"id": "guard", "schedule": {"dawn": "barracks"}}
    dest = BasicNpcScheduleProvider._scheduled_destination(char, "dusk", {"town"})
    assert dest == (None, None, None)


# ------------------------------------------------------------------
# Integration: schedule overrides movement target
# ------------------------------------------------------------------


def test_schedule_moves_to_sub_location() -> None:
    """NPC with schedule dusk→tavern moves within home area to that sub-location."""
    provider = _make_provider()
    characters = [{"id": "bartender", "area_id": "market", "schedule": {"dusk": "counter"}}]
    decision = provider.plan(_dusk_context(characters))
    assert decision.metadata.get("status") == "deterministic"
    moves = decision.moves
    assert len(moves) == 1
    move = moves[0] if isinstance(moves[0], dict) else vars(moves[0])
    assert move["area_id"] == "market"
    assert move["location_id"] == "counter"
    assert move["room_id"] is None


def test_no_schedule_is_noop() -> None:
    """NPC without schedule does not move."""
    provider = _make_provider()
    characters = [{"id": "guard", "area_id": "barracks"}]
    decision = provider.plan(_dusk_context(characters))
    assert decision.metadata.get("status") == "noop"


def test_schedule_cross_area_move() -> None:
    """NPC with dict schedule value moves across areas."""
    provider = _make_provider()
    characters = [
        {
            "id": "farmer",
            "area_id": "town",
            "schedule": {"dawn": {"area": "market", "location": "stall"}},
        }
    ]
    decision = provider.plan(_dawn_context(characters))
    assert decision.metadata.get("status") == "deterministic"
    moves = decision.moves
    assert len(moves) == 1
    move = moves[0] if isinstance(moves[0], dict) else vars(moves[0])
    assert move["area_id"] == "market"
    assert move["location_id"] == "stall"
    assert move["room_id"] is None


def test_already_at_destination_is_noop() -> None:
    """NPC already at scheduled destination should not move."""
    provider = _make_provider()
    characters = [
        {
            "id": "bartender",
            "area_id": "tavern",
            "schedule": {"dusk": {"location": "counter", "room": "bar"}},
        }
    ]
    areas = {
        "tavern": {
            "npc_locations": {"bartender": "counter"},
            "npc_rooms": {"bartender": "bar"},
        },
        "town": {"npc_locations": {}},
    }
    decision = provider.plan(_dusk_context(characters, areas=areas))
    assert decision.metadata.get("status") == "noop"


def test_room_mismatch_produces_move() -> None:
    provider = _make_provider()
    characters = [
        {
            "id": "bartender",
            "area_id": "tavern",
            "schedule": {"dusk": {"location": "counter", "room": "bar"}},
        }
    ]
    areas = {
        "tavern": {
            "npc_locations": {"bartender": "counter"},
            "npc_rooms": {"bartender": "kitchen"},
        }
    }

    decision = provider.plan(_dusk_context(characters, areas=areas))

    assert decision.metadata.get("status") == "deterministic"
    move = decision.moves[0] if isinstance(decision.moves[0], dict) else vars(decision.moves[0])
    assert move["location_id"] == "counter"
    assert move["room_id"] == "bar"
