"""Tests for P5 Phase 7: CompanionManager + cold→hostile→enemy transitions."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from app.game_core.content import WorldInstance
from app.game_core.orchestration.companion_manager import CompanionManager, RecruitResult
from app.game_core.orchestration.hooks.relationship import (
    RelationshipHook,
    _next_negative_stage,
)
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.state import StateChange, StateContainer
from app.game_core.state.slices import SceneSlice
from app.game_core.state.slices.party import PartySlice
from app.game_core.state.slices.relations import RelationSlice


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


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
) -> StateContainer:
    state = StateContainer()

    scene_sl = SceneSlice()
    scene_sl.restore({})
    state.register(scene_sl)

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


def _make_relationship_context(
    *,
    npc_dispositions: dict | None = None,
    relationship_stages: dict | None = None,
    party_members: dict | None = None,
    change_log: list | None = None,
) -> SettlementContext:
    state = _make_state(
        party_members=party_members,
        npc_dispositions=npc_dispositions,
        relationship_stages=relationship_stages,
    )
    scene_sl = state.scene

    return SettlementContext(
        change_log=change_log or [StateChange(slice="relations", operation="set",
                                               path="x", value="y")],
        state=state,
        world=WorldInstance("test"),
        scene_bus=SceneBus(scene_sl),
        _rules_engine=RulesEngine(),
        _apply_delta=lambda d: None,
    )


# ------------------------------------------------------------------
# CompanionManager unit tests
# ------------------------------------------------------------------


def test_recruit_success() -> None:
    world = _make_world_with_npc("npc1", tags=["recruitable"])
    state = _make_state(
        npc_dispositions={"npc1": {"approval": 10, "trust": 0, "romance": 0}},
        relationship_stages={"npc1": "acquaintance"},
    )
    mgr = CompanionManager(world, state)
    result = mgr.recruit("npc1")
    assert result.success is True
    assert result.reason == "recruited"
    assert "npc1" in state.party.members


def test_recruit_not_recruitable() -> None:
    world = _make_world_with_npc("npc1", tags=["merchant"])
    state = _make_state(
        npc_dispositions={"npc1": {"approval": 20}},
        relationship_stages={"npc1": "acquaintance"},
    )
    result = CompanionManager(world, state).recruit("npc1")
    assert result.success is False
    assert result.reason == "not_recruitable"


def test_recruit_stranger_stage() -> None:
    world = _make_world_with_npc("npc1", tags=["recruitable"])
    state = _make_state(
        npc_dispositions={"npc1": {"approval": 20}},
        relationship_stages={"npc1": "stranger"},
    )
    result = CompanionManager(world, state).recruit("npc1")
    assert result.success is False
    assert result.reason == "stranger"


def test_recruit_negative_approval() -> None:
    world = _make_world_with_npc("npc1", tags=["recruitable"])
    state = _make_state(
        npc_dispositions={"npc1": {"approval": 0}},
        relationship_stages={"npc1": "acquaintance"},
    )
    result = CompanionManager(world, state).recruit("npc1")
    assert result.success is False
    assert result.reason == "npc_refuses"


def test_recruit_party_full() -> None:
    world = _make_world_with_npc("npc1", tags=["recruitable"])
    state = _make_state(
        party_members={f"member{i}": {} for i in range(CompanionManager.MAX_PARTY_SIZE)},
        npc_dispositions={"npc1": {"approval": 20}},
        relationship_stages={"npc1": "acquaintance"},
    )
    result = CompanionManager(world, state).recruit("npc1")
    assert result.success is False
    assert result.reason == "party_full"


def test_recruit_already_member() -> None:
    world = _make_world_with_npc("npc1", tags=["recruitable"])
    state = _make_state(
        party_members={"npc1": {"name": "npc1"}},
        npc_dispositions={"npc1": {"approval": 20}},
        relationship_stages={"npc1": "friend"},
    )
    result = CompanionManager(world, state).recruit("npc1")
    assert result.success is False
    assert result.reason == "already_member"


def test_recruit_npc_not_found() -> None:
    world = WorldInstance("empty")
    world._registries["characters"] = MagicMock(get=lambda cid: None)
    state = _make_state()
    result = CompanionManager(world, state).recruit("unknown_npc")
    assert result.success is False
    assert result.reason == "npc_not_found"


def test_dismiss_success() -> None:
    world = WorldInstance("test")
    state = _make_state(party_members={"npc1": {"name": "npc1"}})
    result = CompanionManager(world, state).dismiss("npc1")
    assert result.success is True
    assert result.reason == "dismissed"
    assert "npc1" not in state.party.members


def test_dismiss_not_member() -> None:
    world = WorldInstance("test")
    state = _make_state()
    result = CompanionManager(world, state).dismiss("npc1")
    assert result.success is False
    assert result.reason == "not_member"


def test_force_leave_sets_reason() -> None:
    world = WorldInstance("test")
    state = _make_state(party_members={"npc1": {"name": "npc1"}})
    result = CompanionManager(world, state).force_leave("npc1", reason="hostile")
    assert result.success is True
    assert result.reason == "force_leave:hostile"
    assert "npc1" not in state.party.members


def test_force_leave_not_member_returns_failure() -> None:
    world = WorldInstance("test")
    state = _make_state()
    result = CompanionManager(world, state).force_leave("npc1", reason="hostile")
    assert result.success is False
    assert result.reason == "not_member"


# ------------------------------------------------------------------
# _next_negative_stage extension tests (cold → hostile → enemy)
# ------------------------------------------------------------------


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


# ------------------------------------------------------------------
# RelationshipHook integration: force_leave on hostile/enemy
# ------------------------------------------------------------------


def test_hook_force_leaves_party_member_on_hostile() -> None:
    """When a party member's stage reaches hostile, they should be auto-dismissed."""
    ctx = _make_relationship_context(
        npc_dispositions={"npc1": {"approval": -55, "trust": -35, "romance": 0}},
        relationship_stages={"npc1": "cold"},
        party_members={"npc1": {"name": "npc1"}},
    )
    asyncio.run(RelationshipHook().execute(ctx))

    # Stage should be hostile
    assert ctx.state.relations.get_stage("npc1") == "hostile"
    # NPC should no longer be in party
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
        party_members={},  # npc1 not in party
    )
    result = asyncio.run(RelationshipHook().execute(ctx))
    assert ctx.state.relations.get_stage("npc1") == "hostile"
    event_types = [ev.event_type for ev in result.sse_events]
    assert "companion_dismissed" not in event_types
