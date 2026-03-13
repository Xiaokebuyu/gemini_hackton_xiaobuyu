"""Tests for A5e data pipeline fix: interactable dict preservation.

Covers:
- DynamicSubAreaManager.create() preserves dict interactables (not stringified)
- DynamicSubAreaManager.create() still handles plain string interactables
- directive_contracts._normalize_legacy_fill_area_entry() preserves dict
- scene_views.build_location_overview() reads interactables from temporary sub-area
"""

from __future__ import annotations

from types import SimpleNamespace

from app.game_core.planning import DynamicSubAreaManager
from app.game_core.planning.directive_contracts import _normalize_legacy_fill_area_entry
from app.game_core.state.slices import AreaSlice
from app.scene_views import build_location_overview


# ---------------------------------------------------------------------------
# DynamicSubAreaManager – dict interactables are preserved
# ---------------------------------------------------------------------------

def test_dynamic_sub_area_manager_preserves_interactable_dicts() -> None:
    """create() must keep interactable dicts intact, not stringify them."""
    areas = AreaSlice()
    areas.restore({"areas": {"forest": {}}})
    manager = DynamicSubAreaManager(areas)

    iact_dict = {
        "id": "ancient_altar",
        "name": "Ancient Altar",
        "description": "A crumbling stone altar.",
        "type": "interactive",
        "tags": ["ritual", "investigate"],
        "checks": [{"skill": "arcana", "dc": 14}],
    }

    created = manager.create(
        "forest",
        {
            "id": "ruins_shrine",
            "label": "Ruins Shrine",
            "interactables": [iact_dict],
        },
    )

    assert created is not None
    assert len(created["interactables"]) == 1
    stored = created["interactables"][0]
    # Must be the original dict, not a stringified representation
    assert isinstance(stored, dict), f"Expected dict, got {type(stored)}: {stored!r}"
    assert stored["id"] == "ancient_altar"
    assert stored["name"] == "Ancient Altar"
    assert stored["tags"] == ["ritual", "investigate"]
    assert stored["checks"] == [{"skill": "arcana", "dc": 14}]


def test_dynamic_sub_area_manager_preserves_string_interactables() -> None:
    """Plain string interactables (legacy format) must still be accepted."""
    areas = AreaSlice()
    areas.restore({"areas": {"forest": {}}})
    manager = DynamicSubAreaManager(areas)

    created = manager.create(
        "forest",
        {
            "id": "ruins_camp",
            "label": "Camp Ruins",
            "interactables": ["campfire", "chest"],
        },
    )

    assert created is not None
    assert created["interactables"] == ["campfire", "chest"]


def test_dynamic_sub_area_manager_preserves_mixed_interactables() -> None:
    """Mixed dict + string interactable list handled correctly."""
    areas = AreaSlice()
    areas.restore({"areas": {"forest": {}}})
    manager = DynamicSubAreaManager(areas)

    iact_dict = {"id": "door", "name": "Locked Door", "description": "Old oak door."}
    created = manager.create(
        "forest",
        {
            "id": "mix_area",
            "label": "Mixed",
            "interactables": [iact_dict, "window"],
        },
    )

    assert created is not None
    assert len(created["interactables"]) == 2
    assert isinstance(created["interactables"][0], dict)
    assert created["interactables"][0]["id"] == "door"
    assert created["interactables"][1] == "window"


# ---------------------------------------------------------------------------
# directive_contracts – _normalize_legacy_fill_area_entry preserves dicts
# ---------------------------------------------------------------------------

def test_normalize_legacy_fill_area_entry_preserves_interactable_dicts() -> None:
    """Legacy fill_area expansion must not stringify interactable dicts."""
    iact_dict = {
        "id": "chest_001",
        "name": "Old Chest",
        "description": "A locked chest.",
        "type": "container",
        "tags": ["lockable"],
    }
    base_payload = {"area_id": "dungeon"}
    raw_item = {
        "id": "storage_room",
        "label": "Storage Room",
        "description": "A damp storage room.",
        "interactables": [iact_dict],
    }
    result = _normalize_legacy_fill_area_entry(base_payload, raw_item)

    assert result is not None
    assert "interactables" in result
    assert len(result["interactables"]) == 1
    stored = result["interactables"][0]
    assert isinstance(stored, dict), f"Expected dict, got {type(stored)}: {stored!r}"
    assert stored["id"] == "chest_001"
    assert stored["tags"] == ["lockable"]


def test_normalize_legacy_fill_area_entry_string_interactables_still_work() -> None:
    """String interactables in legacy entry still produce string list items."""
    base_payload = {"area_id": "dungeon"}
    raw_item = {
        "id": "entry_hall",
        "description": "Entry hall.",
        "interactables": ["torch_bracket", "door_handle"],
    }
    result = _normalize_legacy_fill_area_entry(base_payload, raw_item)

    assert result is not None
    assert result["interactables"] == ["torch_bracket", "door_handle"]


# ---------------------------------------------------------------------------
# scene_views.build_location_overview() – temporary sub-area interactables
# ---------------------------------------------------------------------------

def _make_session_with_temp_sub_area(*, sub_area_id: str, interactables: list) -> object:
    """Build a minimal session stub where player is in a temporary sub-area."""
    area_slice = AreaSlice()
    area_slice.restore({"areas": {"frontier_town": {}}})
    area_slice.add_temporary_sub_area(
        "frontier_town",
        {
            "id": sub_area_id,
            "label": "Hidden Cellar",
            "description": "A damp cellar.",
            "interactables": interactables,
            "status": "active",
        },
    )

    class _Relations:
        npc_dispositions: dict = {}
        relationship_stages: dict = {}

    class _PartySlice:
        members: dict = {}

    class _Player:
        current_area = "frontier_town"
        current_location = sub_area_id
        current_room = None

    class _State:
        player = _Player()
        areas = area_slice
        relations = _Relations()
        party = _PartySlice()

        def has_slice(self, name: str) -> bool:
            return name == "party"

    class _World:
        def has_registry(self, name: str) -> bool:
            return False

    runtime = SimpleNamespace(state=_State(), world=_World())
    return SimpleNamespace(runtime=runtime)


def test_build_location_overview_reads_interactables_from_temporary_sub_area() -> None:
    """When player is inside a temporary sub-area, its dict interactables appear."""
    iact = {
        "id": "secret_lever",
        "name": "Secret Lever",
        "description": "A rusty lever.",
        "type": "interactive",
        "tags": ["mechanism"],
        "checks": [{"skill": "investigation", "dc": 12}],
    }
    session = _make_session_with_temp_sub_area(
        sub_area_id="hidden_cellar",
        interactables=[iact],
    )
    overview = build_location_overview(session)

    assert overview["location_id"] == "hidden_cellar"
    assert len(overview["interactables"]) == 1
    found = overview["interactables"][0]
    assert found["id"] == "secret_lever"
    assert found["name"] == "Secret Lever"
    assert found["description_hint"] == "A rusty lever."
    assert found["requires_check"] is True
    assert "mechanism" in found["tags"]


def test_build_location_overview_reads_string_interactables_from_temporary_sub_area() -> None:
    """Legacy plain-string entries in a temporary sub-area also appear."""
    session = _make_session_with_temp_sub_area(
        sub_area_id="storage_room",
        interactables=["crate", "barrel"],
    )
    overview = build_location_overview(session)

    assert overview["location_id"] == "storage_room"
    assert len(overview["interactables"]) == 2
    ids = [i["id"] for i in overview["interactables"]]
    assert "crate" in ids
    assert "barrel" in ids


def test_build_location_overview_no_interactables_when_not_in_temp_sub_area() -> None:
    """When player is not in any sub-area, interactables list is empty."""
    area_slice = AreaSlice()
    area_slice.restore({"areas": {"frontier_town": {}}})
    # Add a temp sub-area but player is NOT in it
    area_slice.add_temporary_sub_area(
        "frontier_town",
        {
            "id": "hidden_cellar",
            "label": "Cellar",
            "interactables": [{"id": "lever", "name": "Lever"}],
            "status": "active",
        },
    )

    class _Relations:
        npc_dispositions: dict = {}
        relationship_stages: dict = {}

    class _PartySlice:
        members: dict = {}

    class _Player:
        current_area = "frontier_town"
        current_location = None  # at area level, not in any sub-area
        current_room = None

    class _State:
        player = _Player()
        areas = area_slice
        relations = _Relations()
        party = _PartySlice()

        def has_slice(self, name: str) -> bool:
            return name == "party"

    class _World:
        def has_registry(self, name: str) -> bool:
            return False

    session = SimpleNamespace(
        runtime=SimpleNamespace(state=_State(), world=_World())
    )
    overview = build_location_overview(session)
    assert overview["interactables"] == []
