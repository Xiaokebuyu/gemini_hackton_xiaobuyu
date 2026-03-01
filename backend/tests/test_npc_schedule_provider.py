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


def test_scheduled_destination_returns_override() -> None:
    char = {"id": "bartender", "schedule": {"dusk": "tavern"}}
    dest = BasicNpcScheduleProvider._scheduled_destination(char, "dusk", {"town", "tavern"})
    assert dest == "tavern"


def test_scheduled_destination_returns_none_when_area_invalid() -> None:
    """If scheduled area is not in valid_area_ids, treat as no schedule."""
    char = {"id": "ghost", "schedule": {"dusk": "ruins"}}
    dest = BasicNpcScheduleProvider._scheduled_destination(char, "dusk", {"town", "tavern"})
    assert dest is None


def test_scheduled_destination_returns_none_when_no_schedule() -> None:
    char = {"id": "wanderer"}
    dest = BasicNpcScheduleProvider._scheduled_destination(char, "dusk", {"town"})
    assert dest is None


# ------------------------------------------------------------------
# Integration: schedule overrides movement target
# ------------------------------------------------------------------


def test_schedule_overrides_dusk_destination() -> None:
    """NPC with schedule dusk→tavern should move to tavern, not town."""
    provider = _make_provider()
    characters = [{"id": "bartender", "area_id": "market", "schedule": {"dusk": "tavern"}}]
    decision = provider.plan(_dusk_context(characters))
    assert decision.metadata.get("status") == "deterministic"
    moves = decision.moves
    assert len(moves) == 1
    move = moves[0] if isinstance(moves[0], dict) else vars(moves[0])
    assert move["area_id"] == "tavern"


def test_no_schedule_uses_default_town_at_dusk() -> None:
    """NPC without schedule still moves to town at dusk (backward compat)."""
    provider = _make_provider()
    characters = [{"id": "guard", "area_id": "barracks"}]
    decision = provider.plan(_dusk_context(characters))
    assert decision.metadata.get("status") == "deterministic"
    moves = decision.moves
    assert len(moves) == 1
    move = moves[0] if isinstance(moves[0], dict) else vars(moves[0])
    assert move["area_id"] == "town"


def test_schedule_overrides_dawn_destination() -> None:
    """NPC with schedule dawn→market should move to market at dawn."""
    provider = _make_provider()
    characters = [{"id": "trader", "area_id": "town", "schedule": {"dawn": "market"}}]
    decision = provider.plan(_dawn_context(characters))
    assert decision.metadata.get("status") == "deterministic"
    moves = decision.moves
    assert len(moves) == 1
    move = moves[0] if isinstance(moves[0], dict) else vars(moves[0])
    assert move["area_id"] == "market"


def test_schedule_invalid_area_falls_back_to_area_id() -> None:
    """If scheduled area doesn't exist, fall back to area_id (home area)."""
    provider = _make_provider()
    characters = [{"id": "npc1", "area_id": "barracks", "schedule": {"dawn": "nonexistent"}}]
    decision = provider.plan(_dawn_context(characters))
    assert decision.metadata.get("status") == "deterministic"
    moves = decision.moves
    assert len(moves) == 1
    move = moves[0] if isinstance(moves[0], dict) else vars(moves[0])
    assert move["area_id"] == "barracks"
