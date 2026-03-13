"""Tests for settlement.py execute_command safety net (Phase 1).

Verifies that ValueError / KeyError / TypeError raised inside _apply_delta
are caught, logged, and converted to ExecuteResult.error — so the hook
calling execute_command never crashes.
"""

from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import PlayerSlice, SceneSlice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_context(apply_delta_fn):
    """Build a SettlementContext that delegates _apply_delta to the given fn."""
    world = WorldInstance("test_world")
    state = StateContainer()

    player = PlayerSlice()
    player.restore({"current_area": "town"})
    state.register(player)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)

    scene_bus = SceneBus(scene_slice)

    # A rules engine whose execute() always returns a successful delta with one no-op change.
    class _AlwaysSuccessRulesEngine:
        def execute(self, cmd, state, world):
            delta = StateDelta(
                changes=[
                    StateChange(
                        slice="player",
                        operation="set",
                        path="current_area",
                        value="town",
                    )
                ]
            )
            return ExecuteResult(executed=True, delta=delta)

    return SettlementContext(
        change_log=[],
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=_AlwaysSuccessRulesEngine(),
        _apply_delta=apply_delta_fn,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_execute_command_catches_value_error():
    """ValueError in _apply_delta must return ExecuteResult.error, not crash."""

    def _bad_apply(delta):
        raise ValueError("invalid area state change")

    ctx = _make_minimal_context(_bad_apply)
    cmd = Command(type="dummy_cmd")
    result = ctx.execute_command(cmd)

    assert isinstance(result, ExecuteResult)
    assert result.executed is False
    assert len(result.errors) == 1
    assert "state_apply_failed" in result.errors[0]
    assert "invalid area state change" in result.errors[0]


def test_execute_command_catches_key_error():
    """KeyError in _apply_delta must return ExecuteResult.error, not crash."""

    def _bad_apply(delta):
        raise KeyError("npc_not_found")

    ctx = _make_minimal_context(_bad_apply)
    cmd = Command(type="planner_spawn_quest_npc")
    result = ctx.execute_command(cmd)

    assert isinstance(result, ExecuteResult)
    assert result.executed is False
    assert len(result.errors) == 1
    assert "state_apply_failed" in result.errors[0]
    # KeyError repr includes quotes around the key
    assert "npc_not_found" in result.errors[0]


def test_execute_command_catches_type_error():
    """TypeError in _apply_delta must return ExecuteResult.error, not crash."""

    def _bad_apply(delta):
        raise TypeError("expected list, got str")

    ctx = _make_minimal_context(_bad_apply)
    cmd = Command(type="planner_fill_area")
    result = ctx.execute_command(cmd)

    assert isinstance(result, ExecuteResult)
    assert result.executed is False
    assert len(result.errors) == 1
    assert "state_apply_failed" in result.errors[0]
    assert "expected list, got str" in result.errors[0]


def test_execute_command_succeeds_when_apply_delta_is_clean():
    """Happy path: no exception → result passes through unchanged."""
    applied = []

    def _clean_apply(delta):
        applied.append(delta)

    ctx = _make_minimal_context(_clean_apply)
    cmd = Command(type="dummy_cmd")
    result = ctx.execute_command(cmd)

    assert result.executed is True
    assert len(applied) == 1
    assert result.errors == []
