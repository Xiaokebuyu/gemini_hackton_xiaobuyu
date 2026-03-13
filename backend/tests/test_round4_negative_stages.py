"""Tests for Phase 6a: Negative relationship stage transitions."""

from __future__ import annotations

import asyncio

from app.game_core.content import WorldInstance
from app.game_core.orchestration.hooks.relationship import (
    RelationshipHook,
    _next_negative_stage,
)
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.rules.handlers.world_state import WorldStateHandler
from app.game_core.state import StateChange, StateContainer
from app.game_core.state.slices import SceneSlice
from app.game_core.state.slices.relations import RelationSlice


# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------


def _make_context(
    *,
    npc_dispositions: dict | None = None,
    relationship_stages: dict | None = None,
    change_log: list | None = None,
) -> SettlementContext:
    state = StateContainer()
    scene_sl = SceneSlice()
    scene_sl.restore({})
    state.register(scene_sl)

    rel = RelationSlice()
    rel.restore({
        "npc_dispositions": npc_dispositions or {},
        "relationship_stages": relationship_stages or {},
        "faction_standings": {},
        "npc_impressions": {},
        "shop_states": {},
    })
    state.register(rel)

    engine = RulesEngine()
    engine.register(WorldStateHandler())

    def _apply_delta(delta) -> None:
        if delta is None:
            return
        state.apply(delta)

    return SettlementContext(
        change_log=change_log or [StateChange(slice="relations", operation="set",
                                               path="x", value="y")],
        state=state,
        world=WorldInstance("test"),
        scene_bus=SceneBus(scene_sl),
        _rules_engine=engine,
        _apply_delta=_apply_delta,
    )


# ------------------------------------------------------------------
# _next_negative_stage unit tests
# ------------------------------------------------------------------


def test_acquaintance_below_threshold_returns_cold() -> None:
    assert _next_negative_stage("acquaintance", {"approval": -25}) == "cold"


def test_acquaintance_at_threshold_returns_none() -> None:
    # threshold is < -20 (strictly less), so -20 does NOT trigger
    assert _next_negative_stage("acquaintance", {"approval": -20}) is None


def test_acquaintance_above_threshold_returns_none() -> None:
    assert _next_negative_stage("acquaintance", {"approval": -15}) is None


def test_friend_below_threshold_returns_cold() -> None:
    assert _next_negative_stage("friend", {"approval": -35}) == "cold"


def test_close_friend_below_threshold_returns_cold() -> None:
    assert _next_negative_stage("close_friend", {"approval": -45}) == "cold"


def test_intimate_below_threshold_returns_cold() -> None:
    assert _next_negative_stage("intimate", {"approval": -55}) == "cold"


def test_stranger_always_returns_none() -> None:
    """Stranger does not enter negative stages regardless of approval."""
    assert _next_negative_stage("stranger", {"approval": -100}) is None


def test_cold_stage_returns_none() -> None:
    """Already in negative stage — no entry transition."""
    assert _next_negative_stage("cold", {"approval": -100}) is None


# ------------------------------------------------------------------
# RelationshipHook.execute() integration — negative transitions
# ------------------------------------------------------------------


def test_execute_transitions_acquaintance_to_cold() -> None:
    ctx = _make_context(
        npc_dispositions={"npc1": {"approval": -25, "trust": 0, "romance": 0}},
        relationship_stages={"npc1": "acquaintance"},
    )
    asyncio.run(RelationshipHook().execute(ctx))
    assert ctx.state.relations.get_stage("npc1") == "cold"


def test_execute_emits_relationship_stage_changed_sse() -> None:
    ctx = _make_context(
        npc_dispositions={"npc1": {"approval": -25}},
        relationship_stages={"npc1": "acquaintance"},
    )
    result = asyncio.run(RelationshipHook().execute(ctx))
    event_types = [ev.event_type for ev in result.sse_events]
    assert "relationship_stage_changed" in event_types


def test_execute_does_not_change_stranger_with_negative_approval() -> None:
    ctx = _make_context(
        npc_dispositions={"npc1": {"approval": -50}},
        relationship_stages={"npc1": "stranger"},
    )
    asyncio.run(RelationshipHook().execute(ctx))
    assert ctx.state.relations.get_stage("npc1") == "stranger"


def test_execute_does_not_change_already_cold_stage() -> None:
    ctx = _make_context(
        npc_dispositions={"npc1": {"approval": -100}},
        relationship_stages={"npc1": "cold"},
    )
    asyncio.run(RelationshipHook().execute(ctx))
    assert ctx.state.relations.get_stage("npc1") == "cold"


def test_positive_and_negative_check_do_not_conflict() -> None:
    """Same tick: one NPC advances positively, another drops to cold."""
    ctx = _make_context(
        npc_dispositions={
            "good_npc": {"approval": 15, "trust": 0, "romance": 0},
            "bad_npc":  {"approval": -25, "trust": 0, "romance": 0},
        },
        relationship_stages={
            "good_npc": "stranger",
            "bad_npc":  "acquaintance",
        },
    )
    asyncio.run(RelationshipHook().execute(ctx))
    assert ctx.state.relations.get_stage("good_npc") == "acquaintance"
    assert ctx.state.relations.get_stage("bad_npc") == "cold"
