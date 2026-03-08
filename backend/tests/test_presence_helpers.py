"""Tests for app/game_core/orchestration/presence.py.

Covers:
1. get_area_npcs — returns NPCs from AreaSlice primary source
2. get_area_npcs — CharacterRegistry fallback for content-only NPCs
3. get_area_npcs — ignores NPCs in other areas
4. get_area_npcs — empty area (no NPCs, no registry)
5. is_colocated — both None → True (area main scene)
6. is_colocated — same non-empty sub-location → True
7. is_colocated — different sub-locations → False
8. is_colocated — empty string treated as None (equal to None)
9. Integration — scene_views and interaction see the same NPC visibility
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.game_core.content import WorldInstance
from app.game_core.content.registries.characters import CharacterRegistry
from app.game_core.orchestration.presence import get_area_npcs, is_colocated
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, SceneSlice
from app.game_core.state.slices.player import PlayerSlice
from app.game_core.state.slices.quests import QuestSlice
from app.game_core.state.slices.relations import RelationSlice
from app.game_core.state.slices.time import TimeSlice


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_state(
    *,
    player_area: str = "town",
    player_location: str | None = None,
    area_npcs: dict[str, dict[str, str | None]] | None = None,
) -> StateContainer:
    """Build a minimal StateContainer with player + area slices."""
    state = StateContainer()

    player = PlayerSlice()
    player.restore({
        "current_area": player_area,
        "current_location": player_location or "",
    })
    state.register(player)

    area_slice = AreaSlice()
    areas_data: dict[str, dict] = {}
    if area_npcs is not None:
        for aid, npcs in area_npcs.items():
            areas_data[aid] = {"npc_locations": npcs}
    area_slice.restore({"areas": areas_data})
    state.register(area_slice)

    # Minimal additional slices required by StateContainer consumers
    rel = RelationSlice()
    rel.restore({
        "npc_dispositions": {},
        "relationship_stages": {},
        "faction_standings": {},
        "npc_impressions": {},
        "shop_states": {},
    })
    state.register(rel)

    quests = QuestSlice()
    quests.restore({})
    state.register(quests)

    scene = SceneSlice()
    scene.restore({})
    state.register(scene)

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9})
    state.register(time_slice)

    return state


def _make_world_no_registry() -> WorldInstance:
    """Return a WorldInstance with no registries."""
    return WorldInstance("test")


def _make_world_with_chars(chars: list[dict]) -> WorldInstance:
    """Return a WorldInstance with a CharacterRegistry loaded from *chars*.

    Each element of *chars* should be a dict suitable for CharacterRegistry.load().
    The dict key is used as the character ID.
    """
    world = WorldInstance("test")
    registry = CharacterRegistry()
    data = {c["id"]: c for c in chars}
    registry.load(data)
    world._registries["characters"] = registry
    return world


# ------------------------------------------------------------------
# Tests for get_area_npcs
# ------------------------------------------------------------------


def test_get_area_npcs_from_area_slice():
    """NPCs tracked in AreaSlice are returned with their sub-location."""
    state = _make_state(
        area_npcs={
            "town": {
                "npc_tavernkeeper": "tavern",
                "npc_smith": None,
            },
        }
    )
    world = _make_world_no_registry()

    result = get_area_npcs(state, world, "town")

    assert result["npc_tavernkeeper"] == "tavern"
    assert result["npc_smith"] is None


def test_get_area_npcs_character_registry_fallback():
    """NPCs in CharacterRegistry but not in AreaSlice are included via fallback."""
    state = _make_state(area_npcs={"town": {}})
    world = _make_world_with_chars([
        {"id": "static_npc", "name": "Old Man", "area_id": "town", "location_id": "square"},
    ])

    result = get_area_npcs(state, world, "town")

    assert "static_npc" in result
    assert result["static_npc"] == "square"


def test_get_area_npcs_ignores_other_areas():
    """NPCs in different areas are not included in the result."""
    state = _make_state(
        area_npcs={
            "town": {"npc_a": None},
            "forest": {"npc_b": "clearing"},
        }
    )
    world = _make_world_with_chars([
        {"id": "npc_c", "name": "Druid", "area_id": "forest", "location_id": "camp"},
    ])

    result = get_area_npcs(state, world, "town")

    assert "npc_a" in result
    assert "npc_b" not in result
    assert "npc_c" not in result


def test_get_area_npcs_empty_area_no_registry():
    """No NPCs when AreaSlice entry is empty and no CharacterRegistry exists."""
    state = _make_state(area_npcs={"town": {}})
    world = _make_world_no_registry()

    result = get_area_npcs(state, world, "town")

    assert result == {}


def test_get_area_npcs_area_slice_takes_priority_over_registry():
    """When an NPC is in both AreaSlice and CharacterRegistry, AreaSlice wins."""
    state = _make_state(
        area_npcs={
            "town": {"merchant": "market"},  # AreaSlice says market
        }
    )
    world = _make_world_with_chars([
        # CharacterRegistry says square, but AreaSlice should win
        {"id": "merchant", "name": "Merchant", "area_id": "town", "location_id": "square"},
    ])

    result = get_area_npcs(state, world, "town")

    # AreaSlice data takes priority
    assert result["merchant"] == "market"


def test_get_area_npcs_fallback_uses_current_area_field():
    """Fallback resolves area from 'current_area' when 'area_id' is empty."""
    state = _make_state(area_npcs={"town": {}})
    world = _make_world_with_chars([
        # Only 'current_area' is set, 'area_id' is empty
        {"id": "traveler", "name": "Traveler", "area_id": "", "current_area": "town"},
    ])

    result = get_area_npcs(state, world, "town")

    assert "traveler" in result


# ------------------------------------------------------------------
# Tests for is_colocated
# ------------------------------------------------------------------


def test_is_colocated_both_none():
    """Both None → both at area main scene → co-located."""
    assert is_colocated(None, None) is True


def test_is_colocated_same_sub_location():
    """Same non-empty sub-location string → co-located."""
    assert is_colocated("tavern", "tavern") is True


def test_is_colocated_different_sub_locations():
    """Different non-empty sub-locations → not co-located."""
    assert is_colocated("tavern", "market") is False


def test_is_colocated_empty_string_equals_none():
    """Empty string is treated as None (area main scene)."""
    assert is_colocated("", None) is True
    assert is_colocated(None, "") is True
    assert is_colocated("", "") is True


def test_is_colocated_npc_in_sub_location_player_at_main():
    """NPC in a sub-location, player at main → not co-located."""
    assert is_colocated("tavern", None) is False


def test_is_colocated_player_in_sub_location_npc_at_main():
    """Player in a sub-location, NPC at main → not co-located."""
    assert is_colocated(None, "tavern") is False


# ------------------------------------------------------------------
# Integration: scene_views and interaction see same visibility
# ------------------------------------------------------------------


def test_scene_views_and_interaction_consistent_visibility():
    """An NPC visible in scene_views is also reachable in interaction validation.

    This test uses get_area_npcs + is_colocated directly (both the path that
    scene_views.py and interaction.py now both use), verifying that when an NPC
    is placed in the same area+location as the player, both consumers agree
    the NPC is visible/reachable.
    """
    player_area = "town"
    player_location = "square"

    state = _make_state(
        player_area=player_area,
        player_location=player_location,
        area_npcs={
            "town": {
                "vendor": "square",   # same location as player → visible
                "guard": "gate",      # different location → hidden
            }
        }
    )
    world = _make_world_no_registry()

    area_npcs = get_area_npcs(state, world, player_area)

    visible = [
        npc_id for npc_id, sub_loc in area_npcs.items()
        if is_colocated(sub_loc, player_location)
    ]
    hidden = [
        npc_id for npc_id, sub_loc in area_npcs.items()
        if not is_colocated(sub_loc, player_location)
    ]

    assert "vendor" in visible
    assert "guard" in hidden
