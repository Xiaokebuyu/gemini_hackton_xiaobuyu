"""Tests for P5 Phase 8: EventEngine new condition types.

Covers: npc_talked / item_obtained / kill_count
"""

from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.orchestration.event_engine import BasicEventConditionEvaluator
from app.game_core.state import StateContainer
from app.game_core.state.slices import SceneSlice
from app.game_core.state.slices.flags import FlagSlice
from app.game_core.state.slices.player import PlayerSlice


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_evaluator() -> BasicEventConditionEvaluator:
    return BasicEventConditionEvaluator()


def _make_state_with_flags(flags: dict) -> StateContainer:
    state = StateContainer()
    sl = SceneSlice()
    sl.restore({})
    state.register(sl)
    fl = FlagSlice()
    fl.restore({"flags": flags})
    state.register(fl)
    return state


def _make_state_with_player(inventory: list) -> StateContainer:
    state = StateContainer()
    sl = SceneSlice()
    sl.restore({})
    state.register(sl)
    player_sl = PlayerSlice()
    player_sl.restore({"inventory": inventory})
    state.register(player_sl)
    return state


def _condition_met(evaluator, state, condition_type, params) -> bool:
    condition = {"type": condition_type, **params}
    met, _ = evaluator._condition_met(state, condition)
    return met


# ------------------------------------------------------------------
# npc_talked
# ------------------------------------------------------------------


def test_npc_talked_flag_set_returns_true() -> None:
    state = _make_state_with_flags({"talked_to_npc1": True})
    ev = _make_evaluator()
    assert _condition_met(ev, state, "npc_talked", {"npc_id": "npc1"}) is True


def test_npc_talked_flag_not_set_returns_false() -> None:
    state = _make_state_with_flags({})
    ev = _make_evaluator()
    assert _condition_met(ev, state, "npc_talked", {"npc_id": "npc1"}) is False


def test_npc_talked_flag_false_returns_false() -> None:
    state = _make_state_with_flags({"talked_to_npc1": False})
    ev = _make_evaluator()
    assert _condition_met(ev, state, "npc_talked", {"npc_id": "npc1"}) is False


def test_npc_talked_no_flags_slice_returns_false() -> None:
    state = StateContainer()
    sl = SceneSlice()
    sl.restore({})
    state.register(sl)
    ev = _make_evaluator()
    assert _condition_met(ev, state, "npc_talked", {"npc_id": "npc1"}) is False


def test_npc_talked_missing_npc_id_returns_false() -> None:
    state = _make_state_with_flags({"talked_to_npc1": True})
    ev = _make_evaluator()
    assert _condition_met(ev, state, "npc_talked", {}) is False


# ------------------------------------------------------------------
# item_obtained
# ------------------------------------------------------------------


def test_item_obtained_in_inventory_returns_true() -> None:
    state = _make_state_with_player([{"item_id": "sword_01", "qty": 1}])
    ev = _make_evaluator()
    assert _condition_met(ev, state, "item_obtained", {"item_id": "sword_01"}) is True


def test_item_obtained_not_in_inventory_returns_false() -> None:
    state = _make_state_with_player([{"item_id": "shield_01", "qty": 1}])
    ev = _make_evaluator()
    assert _condition_met(ev, state, "item_obtained", {"item_id": "sword_01"}) is False


def test_item_obtained_empty_inventory_returns_false() -> None:
    state = _make_state_with_player([])
    ev = _make_evaluator()
    assert _condition_met(ev, state, "item_obtained", {"item_id": "sword_01"}) is False


def test_item_obtained_no_player_slice_returns_false() -> None:
    state = StateContainer()
    sl = SceneSlice()
    sl.restore({})
    state.register(sl)
    ev = _make_evaluator()
    assert _condition_met(ev, state, "item_obtained", {"item_id": "sword_01"}) is False


def test_item_obtained_missing_item_id_returns_false() -> None:
    state = _make_state_with_player([{"item_id": "sword_01"}])
    ev = _make_evaluator()
    assert _condition_met(ev, state, "item_obtained", {}) is False


# ------------------------------------------------------------------
# kill_count
# ------------------------------------------------------------------


def test_kill_count_reached_returns_true() -> None:
    state = _make_state_with_flags({"kill_count_goblin": 5})
    ev = _make_evaluator()
    assert _condition_met(ev, state, "kill_count",
                          {"monster_type": "goblin", "count": 5}) is True


def test_kill_count_exceeded_returns_true() -> None:
    state = _make_state_with_flags({"kill_count_goblin": 10})
    ev = _make_evaluator()
    assert _condition_met(ev, state, "kill_count",
                          {"monster_type": "goblin", "count": 5}) is True


def test_kill_count_not_reached_returns_false() -> None:
    state = _make_state_with_flags({"kill_count_goblin": 3})
    ev = _make_evaluator()
    assert _condition_met(ev, state, "kill_count",
                          {"monster_type": "goblin", "count": 5}) is False


def test_kill_count_no_flag_returns_false() -> None:
    state = _make_state_with_flags({})
    ev = _make_evaluator()
    assert _condition_met(ev, state, "kill_count",
                          {"monster_type": "goblin", "count": 1}) is False


def test_kill_count_no_flags_slice_returns_false() -> None:
    state = StateContainer()
    sl = SceneSlice()
    sl.restore({})
    state.register(sl)
    ev = _make_evaluator()
    assert _condition_met(ev, state, "kill_count",
                          {"monster_type": "goblin", "count": 1}) is False


def test_kill_count_missing_monster_type_returns_false() -> None:
    state = _make_state_with_flags({"kill_count_goblin": 5})
    ev = _make_evaluator()
    assert _condition_met(ev, state, "kill_count", {"count": 5}) is False


# ------------------------------------------------------------------
# Regression: unknown type still increments unsupported_count
# ------------------------------------------------------------------


def test_unknown_condition_type_returns_unsupported_count_1() -> None:
    state = StateContainer()
    sl = SceneSlice()
    sl.restore({})
    state.register(sl)
    ev = _make_evaluator()
    condition = {"type": "totally_unknown_xyz"}
    met, unsupported = ev._condition_met(state, condition)
    assert met is False
    assert unsupported == 1
