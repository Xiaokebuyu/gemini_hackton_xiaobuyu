"""Tests for companion spatial following (Block C core).

Covers:
- recruit() syncs companion to player position
- sync_to_player() moves companions after player navigation
- dismiss() leaves companion in place (no position change)
- sync_to_player() skips dismissed companions
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.game_core.content import WorldInstance
from app.game_core.orchestration.companion_manager import CompanionManager
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import CompanionHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, PartySlice, SceneSlice
from app.game_core.state.slices.player import PlayerSlice
from app.game_core.state.slices.relations import RelationSlice
from app.game_core.state.slices.time import TimeSlice


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_world(npc_id: str, *, tags: list[str] | None = None) -> WorldInstance:
    """Return a WorldInstance with a single character in the character registry."""
    world = WorldInstance("test")
    profile = MagicMock()
    profile.name = npc_id
    profile.class_id = "warrior"
    profile.tags = tags or []

    char_registry = MagicMock()
    char_registry.get = lambda cid: profile if cid == npc_id else None

    world._registries["characters"] = char_registry
    return world


def _make_world_multi(npc_specs: dict[str, list[str]]) -> WorldInstance:
    """Return a WorldInstance with multiple characters.

    npc_specs: {npc_id: [tags]}
    """
    world = WorldInstance("test")
    profiles: dict[str, MagicMock] = {}
    for npc_id, tags in npc_specs.items():
        p = MagicMock()
        p.name = npc_id
        p.class_id = "warrior"
        p.tags = tags
        profiles[npc_id] = p

    char_registry = MagicMock()
    char_registry.get = lambda cid: profiles.get(cid)
    world._registries["characters"] = char_registry
    return world


def _make_state(
    *,
    player_area: str = "town",
    player_location: str | None = "square",
    npc_locations: dict[str, dict[str, str | None]] | None = None,
    party_members: dict[str, dict] | None = None,
    relationship_stages: dict[str, str] | None = None,
    npc_dispositions: dict[str, dict] | None = None,
) -> StateContainer:
    """Build a StateContainer with player, area, party, and relations slices."""
    state = StateContainer()

    player = PlayerSlice()
    player.restore({
        "current_area": player_area,
        "current_location": player_location,
    })
    state.register(player)

    area_slice = AreaSlice()
    areas_data: dict[str, dict] = {}
    if npc_locations is not None:
        for area_id, npcs in npc_locations.items():
            areas_data[area_id] = {"npc_locations": npcs}
    else:
        areas_data = {
            "town": {"npc_locations": {}},
            "forest": {"npc_locations": {}},
        }
    area_slice.restore({"areas": areas_data})
    state.register(area_slice)

    party = PartySlice()
    party.restore({
        "members": party_members or {},
        "companion_approval": {},
        "shared_experiences": [],
    })
    state.register(party)

    rel = RelationSlice()
    rel.restore({
        "npc_dispositions": npc_dispositions or {},
        "relationship_stages": relationship_stages or {},
        "faction_standings": {},
        "npc_impressions": {},
        "shop_states": {},
    })
    state.register(rel)

    scene = SceneSlice()
    scene.restore({})
    state.register(scene)

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9})
    state.register(time_slice)

    return state


def _execute(state: StateContainer, world: WorldInstance, command: Command):
    engine = RulesEngine()
    engine.register(CompanionHandler())
    result = engine.execute(command, state, world)
    if result.executed and result.delta is not None:
        state.apply(result.delta)
    return result


# ------------------------------------------------------------------
# 1. recruit() syncs companion to player area
# ------------------------------------------------------------------


def test_recruit_places_companion_at_player_area() -> None:
    """After recruit(), the companion should appear in the player's area."""
    world = _make_world("npc1", tags=["recruitable"])
    state = _make_state(
        player_area="town",
        player_location=None,
        npc_locations={
            "town": {"npc1": None},
            "forest": {},
        },
        npc_dispositions={"npc1": {"approval": 20}},
        relationship_stages={"npc1": "acquaintance"},
    )
    result = _execute(
        state,
        world,
        Command(type="recruit_companion", params={"npc_id": "npc1"}),
    )

    assert result.executed is True
    # Companion should be at player's area
    assert state.areas.find_npc_area("npc1") == "town"


def test_recruit_places_companion_at_player_sub_location() -> None:
    """After recruit(), the companion is at the same sub-location as the player."""
    world = _make_world("npc1", tags=["recruitable"])
    state = _make_state(
        player_area="town",
        player_location="square",
        npc_locations={
            "town": {},
            "forest": {"npc1": "camp"},
        },
        npc_dispositions={"npc1": {"approval": 20}},
        relationship_stages={"npc1": "acquaintance"},
    )
    result = _execute(
        state,
        world,
        Command(type="recruit_companion", params={"npc_id": "npc1"}),
    )

    assert result.executed is True
    assert state.areas.find_npc_area("npc1") == "town"
    area_state = state.areas.get_area("town")
    assert area_state.npc_locations.get("npc1") == "square"


# ------------------------------------------------------------------
# 2. sync_to_player() tests
# ------------------------------------------------------------------


def test_sync_to_player_moves_companions_after_navigation() -> None:
    """sync_to_player() should move party members to the player's position."""
    world = _make_world("npc1", tags=["recruitable"])
    state = _make_state(
        player_area="forest",
        player_location="camp",
        npc_locations={
            "town": {"npc1": "square"},
            "forest": {},
        },
        party_members={"npc1": {"name": "npc1"}},
    )
    mgr = CompanionManager(state)
    moved = mgr.sync_to_player()

    assert moved == ["npc1"]
    assert state.areas.find_npc_area("npc1") == "forest"
    area_state = state.areas.get_area("forest")
    assert area_state.npc_locations.get("npc1") == "camp"


def test_sync_to_player_skips_already_correct_position() -> None:
    """Companions already at the player's position should not be moved."""
    world = _make_world("npc1", tags=["recruitable"])
    state = _make_state(
        player_area="town",
        player_location="square",
        npc_locations={
            "town": {"npc1": "square"},
        },
        party_members={"npc1": {"name": "npc1"}},
    )
    mgr = CompanionManager(state)
    moved = mgr.sync_to_player()

    assert moved == []
    # Position unchanged
    assert state.areas.find_npc_area("npc1") == "town"
    area_state = state.areas.get_area("town")
    assert area_state.npc_locations.get("npc1") == "square"


def test_sync_to_player_no_party_members_returns_empty() -> None:
    """When there are no party members, sync_to_player returns an empty list."""
    world = _make_world("npc1", tags=["recruitable"])
    state = _make_state(
        player_area="town",
        player_location="square",
        party_members={},
    )
    mgr = CompanionManager(state)
    moved = mgr.sync_to_player()

    assert moved == []


def test_sync_to_player_moves_companion_in_same_area_different_location() -> None:
    """Companion in same area but different sub-location should be moved."""
    world = _make_world("npc1", tags=["recruitable"])
    state = _make_state(
        player_area="town",
        player_location="inn",
        npc_locations={
            "town": {"npc1": "square"},
        },
        party_members={"npc1": {"name": "npc1"}},
    )
    mgr = CompanionManager(state)
    moved = mgr.sync_to_player()

    assert moved == ["npc1"]
    area_state = state.areas.get_area("town")
    assert area_state.npc_locations.get("npc1") == "inn"


# ------------------------------------------------------------------
# 3. dismiss() leaves companion in place
# ------------------------------------------------------------------


def test_dismiss_leaves_companion_in_area() -> None:
    """After dismiss(), the companion should remain in AreaSlice at their position."""
    world = _make_world("npc1", tags=["recruitable"])
    state = _make_state(
        player_area="town",
        player_location="square",
        npc_locations={
            "town": {"npc1": "square"},
        },
        party_members={"npc1": {"name": "npc1"}},
    )
    result = _execute(
        state,
        world,
        Command(type="dismiss_companion", params={"npc_id": "npc1"}),
    )

    assert result.executed is True
    # NPC should still be present in the area
    assert state.areas.find_npc_area("npc1") == "town"
    area_state = state.areas.get_area("town")
    assert area_state.npc_locations.get("npc1") == "square"


def test_dismissed_companion_not_moved_by_sync() -> None:
    """After dismiss, sync_to_player should NOT move the dismissed NPC."""
    world = _make_world("npc1", tags=["recruitable"])
    state = _make_state(
        player_area="forest",
        player_location="camp",
        npc_locations={
            "town": {"npc1": "square"},
        },
        party_members={"npc1": {"name": "npc1"}},
    )
    mgr = CompanionManager(state)

    # Dismiss first
    result = _execute(
        state,
        world,
        Command(type="dismiss_companion", params={"npc_id": "npc1"}),
    )
    assert result.executed is True
    assert "npc1" not in state.party.members

    # Now sync — npc1 is no longer a member, should not be moved
    moved = mgr.sync_to_player()
    assert moved == []
    # NPC stays in town
    assert state.areas.find_npc_area("npc1") == "town"
