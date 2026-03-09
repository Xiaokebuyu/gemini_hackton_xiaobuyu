"""Tests for Phase 6b: Directive GC (prune_consumed_and_expired)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.rules import RulesEngine
from app.game_core.state import StateChange
from app.game_core.state.slices.narrative_plan import NarrativePlanSlice


# ------------------------------------------------------------------
# NarrativePlanSlice.prune_consumed_and_expired unit tests
# ------------------------------------------------------------------


def _make_slice_with_directives(directives: list[dict]) -> NarrativePlanSlice:
    sl = NarrativePlanSlice()
    sl.restore({})
    for d in directives:
        sl.add_directive(d)
    sl.clear_dirty()
    return sl


def test_prune_removes_consumed_directive() -> None:
    sl = _make_slice_with_directives([
        {"npc_id": "npc1", "consumed": True, "issued_at_tick": 0},
    ])
    pruned = sl.prune_consumed_and_expired(current_tick=10)
    assert pruned == 1
    assert len(sl.npc_directives) == 0


def test_prune_removes_expired_directive() -> None:
    sl = _make_slice_with_directives([
        {"npc_id": "npc1", "consumed": False, "expires_at_tick": 5, "issued_at_tick": 0},
    ])
    pruned = sl.prune_consumed_and_expired(current_tick=10)
    assert pruned == 1
    assert len(sl.npc_directives) == 0


def test_prune_keeps_active_directive() -> None:
    sl = _make_slice_with_directives([
        {"npc_id": "npc1", "consumed": False, "expires_at_tick": 20, "issued_at_tick": 0},
    ])
    pruned = sl.prune_consumed_and_expired(current_tick=10)
    assert pruned == 0
    assert len(sl.npc_directives) == 1


def test_prune_keeps_directive_without_expiry() -> None:
    """Directives with no expires_at_tick should not expire (default = tick+1)."""
    sl = _make_slice_with_directives([
        {"npc_id": "npc1", "consumed": False, "issued_at_tick": 0},
    ])
    pruned = sl.prune_consumed_and_expired(current_tick=10)
    assert pruned == 0
    assert len(sl.npc_directives) == 1


def test_prune_mixed_list() -> None:
    sl = _make_slice_with_directives([
        {"npc_id": "a", "consumed": True},                                  # removed
        {"npc_id": "b", "expires_at_tick": 5, "consumed": False},          # removed
        {"npc_id": "c", "expires_at_tick": 20, "consumed": False},         # kept
        {"npc_id": "d", "consumed": False},                                 # kept (no expiry)
    ])
    pruned = sl.prune_consumed_and_expired(current_tick=10)
    assert pruned == 2
    remaining_ids = {d["npc_id"] for d in sl.npc_directives}
    assert remaining_ids == {"c", "d"}


def test_prune_returns_zero_for_empty_list() -> None:
    sl = NarrativePlanSlice()
    sl.restore({})
    pruned = sl.prune_consumed_and_expired(current_tick=5)
    assert pruned == 0


def test_prune_marks_dirty_when_removed() -> None:
    sl = _make_slice_with_directives([
        {"npc_id": "x", "consumed": True},
    ])
    sl.clear_dirty()
    sl.prune_consumed_and_expired(current_tick=10)
    assert sl.dirty


def test_prune_does_not_mark_dirty_when_nothing_removed() -> None:
    sl = _make_slice_with_directives([
        {"npc_id": "x", "consumed": False, "expires_at_tick": 20},
    ])
    sl.clear_dirty()
    sl.prune_consumed_and_expired(current_tick=10)
    assert not sl.dirty


# ------------------------------------------------------------------
# NarrativePlannerHook.execute() calls GC (integration)
# ------------------------------------------------------------------


def test_narrative_planner_execute_prunes_consumed_directives() -> None:
    """After execute(), consumed directives should be removed from the slice."""
    from app.game_core.bootstrap import build_default_world, build_runtime_for_world
    from app.game_core.orchestration.hooks.narrative_planner import NarrativePlannerHook
    from app.game_core.orchestration.scene_bus import SceneBus
    from app.game_core.orchestration.settlement import SettlementContext
    from app.game_core.rules import RulesEngine
    from app.game_core.state import StateChange
    from app.game_core.state.slices import SceneSlice

    world = build_default_world("test", world_data={})
    runtime = build_runtime_for_world(world)
    state = runtime.state

    # Inject a consumed directive into narrative_plan
    state.narrative_plan.add_directive({
        "npc_id": "npc1",
        "consumed": True,
        "issued_at_tick": 0,
    })
    initial_count = len(state.narrative_plan.npc_directives)
    assert initial_count == 1

    # build_runtime_for_world already registers scene; reuse it
    scene_bus = SceneBus(state.scene)

    # Create context with a trigger change so the hook doesn't skip
    ctx = SettlementContext(
        change_log=[
            StateChange(slice="quests", operation="set", path="x", value="y")
        ],
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=RulesEngine(),
        _apply_delta=lambda d: None,
    )

    # Use a mock planner that returns an empty decision
    mock_planner = MagicMock()

    class FakeDecision:
        directives: list = []
        strategy_notes: str = ""
        next_scheduled_tick = None
        metadata: dict = {}

    mock_planner.plan = AsyncMock(return_value=FakeDecision())
    hook = NarrativePlannerHook(planner=mock_planner)

    # Wire a dispatcher with NarrativeWeaverSubSystem so directive GC runs
    from app.game_core.planning.subsystem import PlannerDispatcher
    from app.game_core.planning.narrative_weaver import NarrativeWeaverSubSystem
    dispatcher = PlannerDispatcher()
    dispatcher.register(NarrativeWeaverSubSystem(sse_collector=hook._pending_sse))
    hook._dispatcher = dispatcher

    asyncio.run(hook.execute(ctx))

    # The consumed directive should have been pruned
    assert len(state.narrative_plan.npc_directives) == 0
