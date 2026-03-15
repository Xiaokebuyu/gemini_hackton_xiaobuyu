"""Tests for NpcAutonomyHook (4-A)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.game_core.orchestration.hooks.npc_autonomy import NpcAutonomyHook
from app.game_core.orchestration.models import HookResult
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, SceneSlice, TimeSlice
from app.game_core.state.slices.narrative_plan import NarrativePlanSlice
from app.game_core.state.slices.party import PartySlice
from app.game_core.state.slices.player import PlayerSlice
from app.game_core.state.slices.relations import RelationSlice
from app.game_core.content import WorldInstance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(
    *,
    player_area: str = "frontier_town",
    player_location: str | None = "north_gate",
    player_room: str | None = None,
    npc_locations: dict[str, str | None] | None = None,
    npc_rooms: dict[str, str | None] | None = None,
    area_events: list[dict[str, Any]] | None = None,
    companions: list[str] | None = None,
    npc_directives: list[dict[str, Any]] | None = None,
    scoped_overlays: dict[str, list[dict[str, Any]]] | None = None,
    slot: int = 10,
) -> SettlementContext:
    state = StateContainer()

    # time
    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": slot})
    state.register(time_slice)

    # player
    player_slice = PlayerSlice()
    player_slice.restore({
        "current_area": player_area,
        "current_location": player_location,
        "current_room": player_room,
    })
    state.register(player_slice)

    # areas
    area_slice = AreaSlice()
    area_data: dict[str, Any] = {
        "areas": {
            player_area: {
                "npc_locations": npc_locations or {},
                "npc_rooms": npc_rooms or {},
                "area_events": area_events or [],
            }
        }
    }
    if scoped_overlays is not None:
        area_data["areas"][player_area]["scoped_interactable_overlays"] = scoped_overlays
    area_slice.restore(area_data)
    state.register(area_slice)

    # relations
    rel_slice = RelationSlice()
    state.register(rel_slice)

    # party
    party_slice = PartySlice()
    members: dict[str, dict[str, Any]] = {}
    if companions:
        for c_id in companions:
            members[c_id] = {"id": c_id}
    party_slice.restore({"members": members})
    state.register(party_slice)

    # narrative_plan (optional)
    if npc_directives is not None:
        np_slice = NarrativePlanSlice()
        np_slice.restore({"npc_directives": npc_directives})
        state.register(np_slice)

    # scene (required for SceneBus)
    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)

    world = WorldInstance("test_world")
    engine = RulesEngine()

    def _apply_delta(delta) -> None:
        if delta is not None:
            state.apply(delta)

    return SettlementContext(
        change_log=[],
        state=state,
        world=world,
        scene_bus=SceneBus(scene_slice),
        _rules_engine=engine,
        _apply_delta=_apply_delta,
    )


# ---------------------------------------------------------------------------
# 1. No player slice → noop
# ---------------------------------------------------------------------------

def test_no_player_slice_returns_noop():
    """Hook returns noop metadata when player slice is absent."""
    state = StateContainer()
    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    rel_slice = RelationSlice()
    state.register(rel_slice)

    def _noop_delta(_):
        pass

    ctx = SettlementContext(
        change_log=[],
        state=state,
        world=WorldInstance("t"),
        scene_bus=SceneBus(scene_slice),
        _rules_engine=RulesEngine(),
        _apply_delta=_noop_delta,
    )
    result = asyncio.run(NpcAutonomyHook().execute(ctx))
    assert result.metadata.get("status") == "noop"


# ---------------------------------------------------------------------------
# 2. No colocated NPCs, no companions → ok with 0 updated
# ---------------------------------------------------------------------------

def test_no_npcs_no_companions_updates_zero():
    """Hook succeeds but updates nobody when area is empty."""
    ctx = _make_context()
    result = asyncio.run(NpcAutonomyHook().execute(ctx))
    assert result.metadata["status"] == "ok"
    assert result.metadata["updated_npcs"] == []


# ---------------------------------------------------------------------------
# 3. Colocated NPC gets observations from area_events
# ---------------------------------------------------------------------------

def test_colocated_npc_observations_from_area_events():
    """NPC blackboard.observations is populated from area_events."""
    events = [
        {"tick": 5, "event": "guard patrols north gate", "source": "engine", "severity": "minor"},
        {"tick": 6, "event": "merchant arrives", "source": "engine", "severity": "minor"},
        {"tick": 7, "event": "fight breaks out", "source": "combat", "severity": "major"},
    ]
    ctx = _make_context(
        npc_locations={"guard_captain": "north_gate"},
        area_events=events,
    )
    asyncio.run(NpcAutonomyHook().execute(ctx))

    board = ctx.state.relations.get_blackboard("guard_captain")
    assert "observations" in board
    # Should have 3 observations (one per event)
    assert len(board["observations"]) == 3
    # Should contain formatted text
    assert any("fight breaks out" in obs for obs in board["observations"])


# ---------------------------------------------------------------------------
# 4. NPC not in same location is NOT updated
# ---------------------------------------------------------------------------

def test_npc_in_different_location_not_updated():
    """NPC in a different location doesn't get a blackboard update."""
    events = [{"tick": 1, "event": "something happens", "source": "engine", "severity": "minor"}]
    ctx = _make_context(
        player_location="north_gate",
        npc_locations={"distant_npc": "market"},  # different location
        area_events=events,
    )
    asyncio.run(NpcAutonomyHook().execute(ctx))

    board = ctx.state.relations.get_blackboard("distant_npc")
    # Should not have been updated (no observations injected)
    assert board == {}


# ---------------------------------------------------------------------------
# 5. Co-located NPC in same room is updated; NPC in different room is not
# ---------------------------------------------------------------------------

def test_room_granularity_filters_npcs():
    """When player is in a specific room, only room-matched NPCs update."""
    events = [{"tick": 1, "event": "door creaks", "source": "engine", "severity": "minor"}]
    ctx = _make_context(
        player_location="north_gate",
        player_room="guard_post",
        npc_locations={
            "guard_in_room": "north_gate",
            "guard_elsewhere": "north_gate",
        },
        npc_rooms={
            "guard_in_room": "guard_post",
            "guard_elsewhere": "watchtower",  # different room
        },
        area_events=events,
    )
    asyncio.run(NpcAutonomyHook().execute(ctx))

    in_room_board = ctx.state.relations.get_blackboard("guard_in_room")
    elsewhere_board = ctx.state.relations.get_blackboard("guard_elsewhere")

    assert len(in_room_board.get("observations", [])) > 0
    assert elsewhere_board == {}


# ---------------------------------------------------------------------------
# 6. Active directive goal appears in NPC blackboard.goals
# ---------------------------------------------------------------------------

def test_active_directive_goal_written_to_blackboard_via_topic():
    """When NPC has an active Planner directive with 'topic', it is added to goals."""
    directives = [
        {
            "npc_id": "guard_captain",
            "directive": {"topic": "investigate the abandoned warehouse"},
            "consumed": False,
        }
    ]
    ctx = _make_context(
        npc_locations={"guard_captain": "north_gate"},
        npc_directives=directives,
    )
    asyncio.run(NpcAutonomyHook().execute(ctx))

    board = ctx.state.relations.get_blackboard("guard_captain")
    assert "goals" in board
    assert "investigate the abandoned warehouse" in board["goals"]


def test_active_directive_goal_written_to_blackboard_via_npc_goal_fallback():
    """npc_goal field still works as fallback when topic is absent."""
    directives = [
        {
            "npc_id": "guard_captain",
            "directive": {"npc_goal": "patrol the east corridor"},
            "consumed": False,
        }
    ]
    ctx = _make_context(
        npc_locations={"guard_captain": "north_gate"},
        npc_directives=directives,
    )
    asyncio.run(NpcAutonomyHook().execute(ctx))

    board = ctx.state.relations.get_blackboard("guard_captain")
    assert "goals" in board
    assert "patrol the east corridor" in board["goals"]


# ---------------------------------------------------------------------------
# 7. Consumed directive is ignored
# ---------------------------------------------------------------------------

def test_consumed_directive_not_added_to_goals():
    """Consumed directives are skipped when building goals."""
    directives = [
        {
            "npc_id": "guard_captain",
            "directive": {"npc_goal": "old task (done)"},
            "consumed": True,
        }
    ]
    ctx = _make_context(
        npc_locations={"guard_captain": "north_gate"},
        npc_directives=directives,
    )
    asyncio.run(NpcAutonomyHook().execute(ctx))

    board = ctx.state.relations.get_blackboard("guard_captain")
    goals = board.get("goals", [])
    assert "old task (done)" not in goals


# ---------------------------------------------------------------------------
# 8. updated_tick is stamped from TimeSlice slot
# ---------------------------------------------------------------------------

def test_updated_tick_is_stamped():
    """Blackboard gets updated_tick = current slot."""
    ctx = _make_context(
        npc_locations={"guard": "north_gate"},
        slot=42,
    )
    asyncio.run(NpcAutonomyHook().execute(ctx))

    board = ctx.state.relations.get_blackboard("guard")
    assert board.get("updated_tick") == 42


# ---------------------------------------------------------------------------
# 9. Companion is updated even when not co-located with player room
# ---------------------------------------------------------------------------

def test_companion_updated_regardless_of_room():
    """Party companions are always updated, even if they are not in the player room."""
    events = [{"tick": 3, "event": "campfire started", "source": "engine", "severity": "minor"}]
    ctx = _make_context(
        player_location="north_gate",
        player_room="guard_post",
        companions=["hero_companion"],
        # Companion is elsewhere in the area
        npc_locations={"hero_companion": "market"},
        area_events=events,
    )
    asyncio.run(NpcAutonomyHook().execute(ctx))

    board = ctx.state.relations.get_blackboard("hero_companion")
    assert "updated_tick" in board
    assert len(board.get("observations", [])) > 0


# ---------------------------------------------------------------------------
# 10. Companion sees unresolved clues in scoped_interactable_overlays
# ---------------------------------------------------------------------------

def test_companion_notices_unresolved_clues():
    """Companion's observations include clue notices for unresolved scoped interactables."""
    scoped = {
        "north_gate": [
            {"id": "hidden_letter", "type": "clue", "name": "Hidden Letter"},
            {"id": "cracked_wall", "type": "clue", "name": "Cracked Wall"},
        ]
    }
    ctx = _make_context(
        player_location="north_gate",
        companions=["hero_companion"],
        scoped_overlays=scoped,
    )
    asyncio.run(NpcAutonomyHook().execute(ctx))

    board = ctx.state.relations.get_blackboard("hero_companion")
    obs = board.get("observations", [])
    clue_obs = [o for o in obs if "clue" in o.lower() or "hidden_letter" in o or "cracked_wall" in o]
    assert len(clue_obs) >= 1


# ---------------------------------------------------------------------------
# 11. Already-resolved clue not reported again by companion
# ---------------------------------------------------------------------------

def test_companion_skips_resolved_clues():
    """Clues that are already in interactable_states are NOT reported."""
    scoped = {
        "north_gate": [
            {"id": "old_note", "type": "clue", "name": "Old Note"},
        ]
    }
    # Pre-resolve old_note
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 5})
    state.register(time_slice)

    player_slice = PlayerSlice()
    player_slice.restore({"current_area": "frontier_town", "current_location": "north_gate"})
    state.register(player_slice)

    area_slice = AreaSlice()
    area_slice.restore({
        "areas": {
            "frontier_town": {
                "npc_locations": {},
                "npc_rooms": {},
                "area_events": [],
                "scoped_interactable_overlays": scoped,
                "interactable_states": {
                    "old_note": {"resolved_option_id": "read", "area_id": "frontier_town"},
                },
            }
        }
    })
    state.register(area_slice)

    rel_slice = RelationSlice()
    state.register(rel_slice)

    party_slice = PartySlice()
    party_slice.restore({"members": {"hero_companion": {"id": "hero_companion"}}})
    state.register(party_slice)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)

    ctx = SettlementContext(
        change_log=[],
        state=state,
        world=WorldInstance("t"),
        scene_bus=SceneBus(scene_slice),
        _rules_engine=RulesEngine(),
        _apply_delta=lambda d: state.apply(d) if d else None,
    )

    asyncio.run(NpcAutonomyHook().execute(ctx))

    board = ctx.state.relations.get_blackboard("hero_companion")
    obs = board.get("observations", [])
    assert not any("old_note" in o for o in obs)


# ---------------------------------------------------------------------------
# 12. Companion NOT listed as colocated NPC (excluded from NPC path)
# ---------------------------------------------------------------------------

def test_companion_excluded_from_colocated_npc_list():
    """A party companion in the player's room appears in companion path, not NPC path."""
    events = [{"tick": 1, "event": "noise from north", "source": "engine", "severity": "minor"}]
    ctx = _make_context(
        player_location="north_gate",
        player_room="guard_post",
        companions=["partner"],
        npc_locations={"partner": "north_gate"},
        npc_rooms={"partner": "guard_post"},
        area_events=events,
    )
    result = asyncio.run(NpcAutonomyHook().execute(ctx))

    # 'partner' should show up as updated via companion path, not counted as colocated NPC
    assert result.metadata["colocated_count"] == 0
    assert "partner" in result.metadata["updated_npcs"]


# ---------------------------------------------------------------------------
# 4-C: Impression → blackboard seeding (first-run behaviour)
# ---------------------------------------------------------------------------


def test_impressions_seeded_as_observations_on_first_run():
    """When blackboard has no updated_tick, past impressions become initial observations."""
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 5})
    state.register(time_slice)

    player_slice = PlayerSlice()
    player_slice.restore({"current_area": "frontier_town", "current_location": "north_gate"})
    state.register(player_slice)

    area_slice = AreaSlice()
    area_slice.restore({
        "areas": {
            "frontier_town": {
                "npc_locations": {"guard_captain": "north_gate"},
                "npc_rooms": {},
                "area_events": [],
            }
        }
    })
    state.register(area_slice)

    rel_slice = RelationSlice()
    # Pre-load impressions (simulating a previous conversation)
    rel_slice.restore({
        "npc_impressions": {
            "guard_captain": [
                "Stranger seems capable",
                "Helped clear goblins from the road",
            ]
        }
    })
    state.register(rel_slice)

    party_slice = PartySlice()
    party_slice.restore({"members": {}})
    state.register(party_slice)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)

    ctx = SettlementContext(
        change_log=[],
        state=state,
        world=WorldInstance("t"),
        scene_bus=SceneBus(scene_slice),
        _rules_engine=RulesEngine(),
        _apply_delta=lambda d: state.apply(d) if d else None,
    )
    asyncio.run(NpcAutonomyHook().execute(ctx))

    board = ctx.state.relations.get_blackboard("guard_captain")
    obs = board.get("observations", [])
    # Impressions should be seeded into observations
    assert any("capable" in o for o in obs), f"Expected impression in observations: {obs}"
    assert any("goblins" in o for o in obs), f"Expected impression in observations: {obs}"


def test_impressions_seed_attitude_towards_player_on_first_run():
    """Last impression is used as initial attitude_towards_player on first run."""
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 5})
    state.register(time_slice)

    player_slice = PlayerSlice()
    player_slice.restore({"current_area": "frontier_town", "current_location": "north_gate"})
    state.register(player_slice)

    area_slice = AreaSlice()
    area_slice.restore({
        "areas": {
            "frontier_town": {
                "npc_locations": {"merchant": "north_gate"},
                "npc_rooms": {},
                "area_events": [],
            }
        }
    })
    state.register(area_slice)

    rel_slice = RelationSlice()
    rel_slice.restore({
        "npc_impressions": {
            "merchant": ["Paid fairly", "Trustworthy adventurer"]
        }
    })
    state.register(rel_slice)

    party_slice = PartySlice()
    party_slice.restore({"members": {}})
    state.register(party_slice)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)

    ctx = SettlementContext(
        change_log=[],
        state=state,
        world=WorldInstance("t"),
        scene_bus=SceneBus(scene_slice),
        _rules_engine=RulesEngine(),
        _apply_delta=lambda d: state.apply(d) if d else None,
    )
    asyncio.run(NpcAutonomyHook().execute(ctx))

    board = ctx.state.relations.get_blackboard("merchant")
    assert "attitude_towards_player" in board
    assert "Trustworthy" in board["attitude_towards_player"]


def test_no_impressions_no_seeding():
    """NPC with no impressions gets an empty blackboard (only updated_tick)."""
    ctx = _make_context(npc_locations={"fresh_npc": "north_gate"})
    asyncio.run(NpcAutonomyHook().execute(ctx))

    board = ctx.state.relations.get_blackboard("fresh_npc")
    # Should have updated_tick but no observations, no attitude
    assert "updated_tick" in board
    assert "attitude_towards_player" not in board
    obs = board.get("observations", [])
    assert not any("[memory]" in o for o in obs)


def test_second_run_does_not_re_seed():
    """If updated_tick already present, impressions are not re-seeded."""
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 10})
    state.register(time_slice)

    player_slice = PlayerSlice()
    player_slice.restore({"current_area": "frontier_town", "current_location": "north_gate"})
    state.register(player_slice)

    area_slice = AreaSlice()
    area_slice.restore({
        "areas": {
            "frontier_town": {
                "npc_locations": {"veteran_guard": "north_gate"},
                "npc_rooms": {},
                "area_events": [],
            }
        }
    })
    state.register(area_slice)

    rel_slice = RelationSlice()
    # Pre-populate blackboard with updated_tick already present
    rel_slice.restore({
        "npc_impressions": {
            "veteran_guard": ["Some old impression"]
        }
    })
    state.register(rel_slice)
    # Manually set updated_tick to simulate a previous run
    rel_slice.update_blackboard("veteran_guard", {"updated_tick": 5, "observations": ["existing obs"]})

    party_slice = PartySlice()
    party_slice.restore({"members": {}})
    state.register(party_slice)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)

    ctx = SettlementContext(
        change_log=[],
        state=state,
        world=WorldInstance("t"),
        scene_bus=SceneBus(scene_slice),
        _rules_engine=RulesEngine(),
        _apply_delta=lambda d: state.apply(d) if d else None,
    )
    asyncio.run(NpcAutonomyHook().execute(ctx))

    board = ctx.state.relations.get_blackboard("veteran_guard")
    obs = board.get("observations", [])
    # The [memory] prefix should NOT appear — no re-seeding on second run
    assert not any("[memory]" in o for o in obs)
