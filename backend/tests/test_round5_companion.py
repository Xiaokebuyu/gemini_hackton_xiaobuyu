"""Tests for companion commands and negative relationship stages."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from app.game_core.content import WorldInstance
from app.game_core.orchestration.hooks.relationship import (
    RelationshipHook,
    _next_negative_stage,
)
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import CompanionHandler
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import AreaSlice, SceneSlice
from app.game_core.state.slices.party import PartySlice
from app.game_core.state.slices.player import PlayerSlice
from app.game_core.state.slices.relations import RelationSlice
from app.game_core.state.slices.time import TimeSlice


def _make_world_with_npc(npc_id: str, *, tags: list[str] | None = None) -> WorldInstance:
    """Return a WorldInstance with a fake character registry."""
    world = WorldInstance("test")
    profile = MagicMock()
    profile.name = npc_id
    profile.class_id = ""
    profile.tags = tags or []

    char_registry = MagicMock()
    char_registry.get = lambda cid: profile if cid == npc_id else None

    world._registries["characters"] = char_registry
    return world


def _make_state(
    *,
    party_members: dict | None = None,
    npc_dispositions: dict | None = None,
    relationship_stages: dict | None = None,
    player_area: str = "town",
    player_location: str | None = "square",
) -> StateContainer:
    state = StateContainer()

    scene_sl = SceneSlice()
    scene_sl.restore({})
    state.register(scene_sl)

    area_slice = AreaSlice()
    area_slice.restore({"areas": {"town": {"npc_locations": {}}, "forest": {"npc_locations": {}}}})
    state.register(area_slice)

    player = PlayerSlice()
    player.restore({
        "current_area": player_area,
        "current_location": player_location,
    })
    state.register(player)

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 8})
    state.register(time_slice)

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

    return state


def _make_engine() -> RulesEngine:
    engine = RulesEngine()
    engine.register(CompanionHandler())
    return engine


def _execute(command: Command, state: StateContainer, world: WorldInstance):
    return _make_engine().execute(command, state, world)


def _apply(result, state: StateContainer) -> None:
    assert result.delta is not None
    state.apply(result.delta)


def _make_relationship_context(
    *,
    npc_dispositions: dict | None = None,
    relationship_stages: dict | None = None,
    party_members: dict | None = None,
    change_log: list[StateChange] | None = None,
) -> SettlementContext:
    state = _make_state(
        party_members=party_members,
        npc_dispositions=npc_dispositions,
        relationship_stages=relationship_stages,
    )
    scene_sl = state.scene
    engine = _make_engine()
    scene_bus = SceneBus(scene_sl)
    recorded_changes = (
        change_log
        if change_log is not None else [StateChange(slice="relations", operation="set", path="x", value="y")]
    )

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        recorded_changes.extend(delta.changes)
        for change in delta.changes:
            scene_bus.record_state_change(change)

    return SettlementContext(
        change_log=recorded_changes,
        state=state,
        world=WorldInstance("test"),
        scene_bus=scene_bus,
        _rules_engine=engine,
        _apply_delta=_apply_delta,
    )


def test_recruit_success() -> None:
    world = _make_world_with_npc("npc1", tags=["recruitable"])
    state = _make_state(
        npc_dispositions={"npc1": {"approval": 10, "trust": 0, "romance": 0}},
        relationship_stages={"npc1": "acquaintance"},
    )
    result = _execute(
        Command(type="recruit_companion", params={"npc_id": "npc1"}),
        state,
        world,
    )

    assert result.executed is True
    assert result.time_cost == 0
    _apply(result, state)
    assert "npc1" in state.party.members
    assert state.areas.find_npc_area("npc1") == "town"
    assert result.metadata["event_type"] == "companion_recruited"
    assert result.metadata["party_members"] == ["npc1"]


def test_recruit_not_recruitable() -> None:
    result = _execute(
        Command(type="recruit_companion", params={"npc_id": "npc1"}),
        _make_state(
            npc_dispositions={"npc1": {"approval": 20}},
            relationship_stages={"npc1": "acquaintance"},
        ),
        _make_world_with_npc("npc1", tags=["merchant"]),
    )
    assert result.executed is False
    assert result.errors == ["not_recruitable"]


def test_recruit_stranger_stage() -> None:
    result = _execute(
        Command(type="recruit_companion", params={"npc_id": "npc1"}),
        _make_state(
            npc_dispositions={"npc1": {"approval": 20}},
            relationship_stages={"npc1": "stranger"},
        ),
        _make_world_with_npc("npc1", tags=["recruitable"]),
    )
    assert result.executed is False
    assert result.errors == ["stranger"]


def test_recruit_negative_approval() -> None:
    result = _execute(
        Command(type="recruit_companion", params={"npc_id": "npc1"}),
        _make_state(
            npc_dispositions={"npc1": {"approval": 0}},
            relationship_stages={"npc1": "acquaintance"},
        ),
        _make_world_with_npc("npc1", tags=["recruitable"]),
    )
    assert result.executed is False
    assert result.errors == ["npc_refuses"]


def test_recruit_party_full() -> None:
    state = _make_state(
        party_members={f"member{i}": {} for i in range(CompanionHandler.MAX_PARTY_SIZE)},
        npc_dispositions={"npc1": {"approval": 20}},
        relationship_stages={"npc1": "acquaintance"},
    )
    result = _execute(
        Command(type="recruit_companion", params={"npc_id": "npc1"}),
        state,
        _make_world_with_npc("npc1", tags=["recruitable"]),
    )
    assert result.executed is False
    assert result.errors == ["party_full"]


def test_recruit_already_member() -> None:
    state = _make_state(
        party_members={"npc1": {"name": "npc1"}},
        npc_dispositions={"npc1": {"approval": 20}},
        relationship_stages={"npc1": "friend"},
    )
    result = _execute(
        Command(type="recruit_companion", params={"npc_id": "npc1"}),
        state,
        _make_world_with_npc("npc1", tags=["recruitable"]),
    )
    assert result.executed is False
    assert result.errors == ["already_member"]


def test_recruit_npc_not_found() -> None:
    world = WorldInstance("empty")
    world._registries["characters"] = MagicMock(get=lambda cid: None)
    result = _execute(
        Command(type="recruit_companion", params={"npc_id": "unknown_npc"}),
        _make_state(),
        world,
    )
    assert result.executed is False
    assert result.errors == ["npc_not_found"]


def test_dismiss_success() -> None:
    state = _make_state(party_members={"npc1": {"name": "npc1"}})
    result = _execute(
        Command(type="dismiss_companion", params={"npc_id": "npc1"}),
        state,
        WorldInstance("test"),
    )
    assert result.executed is True
    _apply(result, state)
    assert "npc1" not in state.party.members
    assert result.metadata["reason"] == "dismissed"


def test_dismiss_not_member() -> None:
    result = _execute(
        Command(type="dismiss_companion", params={"npc_id": "npc1"}),
        _make_state(),
        WorldInstance("test"),
    )
    assert result.executed is False
    assert result.errors == ["not_member"]


def test_force_leave_sets_reason() -> None:
    state = _make_state(party_members={"npc1": {"name": "npc1"}})
    result = _execute(
        Command(
            type="force_leave_companion",
            params={"npc_id": "npc1", "reason": "relationship_hostile"},
        ),
        state,
        WorldInstance("test"),
    )
    assert result.executed is True
    _apply(result, state)
    assert "npc1" not in state.party.members
    assert result.metadata["reason"] == "relationship_hostile"


def test_force_leave_not_member_returns_failure() -> None:
    result = _execute(
        Command(
            type="force_leave_companion",
            params={"npc_id": "npc1", "reason": "relationship_hostile"},
        ),
        _make_state(),
        WorldInstance("test"),
    )
    assert result.executed is False
    assert result.errors == ["not_member"]


def test_cold_both_thresholds_met_returns_hostile() -> None:
    assert _next_negative_stage("cold", {"approval": -55, "trust": -35}) == "hostile"


def test_cold_only_approval_low_stays() -> None:
    """trust not low enough — no transition."""
    assert _next_negative_stage("cold", {"approval": -55, "trust": -20}) is None


def test_cold_only_trust_low_stays() -> None:
    """approval not low enough — no transition."""
    assert _next_negative_stage("cold", {"approval": -40, "trust": -35}) is None


def test_cold_both_at_threshold_boundary_stays() -> None:
    """Boundary: approval=-50, trust=-30 — strictly less than required, so no transition."""
    assert _next_negative_stage("cold", {"approval": -50, "trust": -30}) is None


def test_hostile_trust_below_returns_enemy() -> None:
    assert _next_negative_stage("hostile", {"approval": -90, "trust": -65}) == "enemy"


def test_hostile_trust_above_stays() -> None:
    assert _next_negative_stage("hostile", {"approval": -90, "trust": -55}) is None


def test_enemy_returns_none() -> None:
    """No further progression past enemy."""
    assert _next_negative_stage("enemy", {"approval": -100, "trust": -100}) is None


def test_hook_force_leaves_party_member_on_hostile() -> None:
    """When a party member's stage reaches hostile, they should be auto-dismissed."""
    ctx = _make_relationship_context(
        npc_dispositions={"npc1": {"approval": -55, "trust": -35, "romance": 0}},
        relationship_stages={"npc1": "cold"},
        party_members={"npc1": {"name": "npc1"}},
    )
    asyncio.run(RelationshipHook().execute(ctx))

    assert ctx.state.relations.get_stage("npc1") == "hostile"
    assert "npc1" not in ctx.state.party.members


def test_hook_emits_companion_dismissed_sse() -> None:
    ctx = _make_relationship_context(
        npc_dispositions={"npc1": {"approval": -55, "trust": -35, "romance": 0}},
        relationship_stages={"npc1": "cold"},
        party_members={"npc1": {"name": "npc1"}},
    )
    result = asyncio.run(RelationshipHook().execute(ctx))
    event_types = [ev.event_type for ev in result.sse_events]
    assert "companion_dismissed" in event_types


def test_hook_does_not_dismiss_non_party_member() -> None:
    """When NPC enters hostile but is not a party member, no dismissal side-effects."""
    ctx = _make_relationship_context(
        npc_dispositions={"npc1": {"approval": -55, "trust": -35, "romance": 0}},
        relationship_stages={"npc1": "cold"},
        party_members={},
    )
    result = asyncio.run(RelationshipHook().execute(ctx))
    assert ctx.state.relations.get_stage("npc1") == "hostile"
    event_types = [ev.event_type for ev in result.sse_events]
    assert "companion_dismissed" not in event_types
