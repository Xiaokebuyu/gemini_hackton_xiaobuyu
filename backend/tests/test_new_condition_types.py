"""Tests for the 4 new event condition types added in 1-D (KI-07).

Covers:
  - encounter_cleared
  - clue_investigated
  - all_encounters_cleared
  - danger_below
"""

from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.orchestration.event_engine import BasicEventConditionEvaluator
from app.game_core.state import StateContainer
from app.game_core.state.slices.area import AreaSlice
from app.game_core.state.slices.scene import SceneSlice


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_evaluator() -> BasicEventConditionEvaluator:
    return BasicEventConditionEvaluator()


def _make_state_with_areas(areas_data: dict) -> StateContainer:
    state = StateContainer()
    scene = SceneSlice()
    scene.restore({})
    state.register(scene)
    areas = AreaSlice()
    areas.restore({"areas": areas_data})
    state.register(areas)
    return state


def _make_state_no_areas() -> StateContainer:
    """StateContainer without areas slice registered."""
    state = StateContainer()
    scene = SceneSlice()
    scene.restore({})
    state.register(scene)
    return state


def _condition_met(evaluator: BasicEventConditionEvaluator, state: StateContainer,
                   condition_type: str, params: dict) -> bool:
    condition = {"type": condition_type, **params}
    met, _ = evaluator._condition_met(state, condition)
    return met


# ------------------------------------------------------------------
# encounter_cleared
# ------------------------------------------------------------------


def test_encounter_cleared_returns_true_when_cleared() -> None:
    state = _make_state_with_areas({
        "forest": {
            "hostile_tracking": {
                "goblin_camp": {"cleared": True, "status": "cleared"},
            }
        }
    })
    ev = _make_evaluator()
    result = _condition_met(ev, state, "encounter_cleared",
                            {"area_id": "forest", "encounter_id": "goblin_camp"})
    assert result is True


def test_encounter_cleared_returns_false_when_not_cleared() -> None:
    state = _make_state_with_areas({
        "forest": {
            "hostile_tracking": {
                "goblin_camp": {"cleared": False, "status": "active"},
            }
        }
    })
    ev = _make_evaluator()
    result = _condition_met(ev, state, "encounter_cleared",
                            {"area_id": "forest", "encounter_id": "goblin_camp"})
    assert result is False


def test_encounter_cleared_returns_false_when_encounter_not_found() -> None:
    state = _make_state_with_areas({"forest": {"hostile_tracking": {}}})
    ev = _make_evaluator()
    result = _condition_met(ev, state, "encounter_cleared",
                            {"area_id": "forest", "encounter_id": "nonexistent"})
    assert result is False


def test_encounter_cleared_returns_false_when_no_areas_slice() -> None:
    state = _make_state_no_areas()
    ev = _make_evaluator()
    result = _condition_met(ev, state, "encounter_cleared",
                            {"area_id": "forest", "encounter_id": "goblin_camp"})
    assert result is False


# ------------------------------------------------------------------
# clue_investigated
# ------------------------------------------------------------------


def test_clue_investigated_returns_true_when_resolved() -> None:
    state = _make_state_with_areas({
        "dungeon": {
            "interactable_states": {
                "ancient_scroll": {
                    "resolved_option_id": "option_read",
                    "resolved_at_tick": 5,
                }
            }
        }
    })
    ev = _make_evaluator()
    result = _condition_met(ev, state, "clue_investigated",
                            {"area_id": "dungeon", "clue_id": "ancient_scroll"})
    assert result is True


def test_clue_investigated_returns_false_when_not_resolved() -> None:
    state = _make_state_with_areas({
        "dungeon": {
            "interactable_states": {
                "ancient_scroll": {
                    # No resolved_option_id yet
                    "first_inspected": True,
                }
            }
        }
    })
    ev = _make_evaluator()
    result = _condition_met(ev, state, "clue_investigated",
                            {"area_id": "dungeon", "clue_id": "ancient_scroll"})
    assert result is False


def test_clue_investigated_returns_false_when_clue_not_found() -> None:
    state = _make_state_with_areas({"dungeon": {"interactable_states": {}}})
    ev = _make_evaluator()
    result = _condition_met(ev, state, "clue_investigated",
                            {"area_id": "dungeon", "clue_id": "missing_clue"})
    assert result is False


def test_clue_investigated_returns_false_when_no_areas_slice() -> None:
    state = _make_state_no_areas()
    ev = _make_evaluator()
    result = _condition_met(ev, state, "clue_investigated",
                            {"area_id": "dungeon", "clue_id": "ancient_scroll"})
    assert result is False


# ------------------------------------------------------------------
# all_encounters_cleared
# ------------------------------------------------------------------


def test_all_encounters_cleared_returns_true_when_all_cleared() -> None:
    state = _make_state_with_areas({
        "forest": {
            "hostile_tracking": {
                "goblin_camp": {"cleared": True},
                "orc_patrol": {"cleared": True},
            }
        }
    })
    ev = _make_evaluator()
    result = _condition_met(ev, state, "all_encounters_cleared", {"area_id": "forest"})
    assert result is True


def test_all_encounters_cleared_returns_false_when_some_not_cleared() -> None:
    state = _make_state_with_areas({
        "forest": {
            "hostile_tracking": {
                "goblin_camp": {"cleared": True},
                "orc_patrol": {"cleared": False},
            }
        }
    })
    ev = _make_evaluator()
    result = _condition_met(ev, state, "all_encounters_cleared", {"area_id": "forest"})
    assert result is False


def test_all_encounters_cleared_returns_true_when_tracking_empty() -> None:
    """Empty hostile_tracking means no encounters — treated as all cleared."""
    state = _make_state_with_areas({"forest": {"hostile_tracking": {}}})
    ev = _make_evaluator()
    result = _condition_met(ev, state, "all_encounters_cleared", {"area_id": "forest"})
    assert result is True


def test_all_encounters_cleared_returns_false_when_no_areas_slice() -> None:
    state = _make_state_no_areas()
    ev = _make_evaluator()
    result = _condition_met(ev, state, "all_encounters_cleared", {"area_id": "forest"})
    assert result is False


# ------------------------------------------------------------------
# danger_below
# ------------------------------------------------------------------


def test_danger_below_returns_true_when_danger_is_below_threshold() -> None:
    state = _make_state_with_areas({"forest": {"danger_level": 0.3}})
    ev = _make_evaluator()
    result = _condition_met(ev, state, "danger_below",
                            {"area_id": "forest", "threshold": 0.5})
    assert result is True


def test_danger_below_returns_false_when_danger_equals_threshold() -> None:
    state = _make_state_with_areas({"forest": {"danger_level": 0.5}})
    ev = _make_evaluator()
    # strict less-than: equal threshold should NOT satisfy
    result = _condition_met(ev, state, "danger_below",
                            {"area_id": "forest", "threshold": 0.5})
    assert result is False


def test_danger_below_returns_false_when_danger_above_threshold() -> None:
    state = _make_state_with_areas({"forest": {"danger_level": 0.8}})
    ev = _make_evaluator()
    result = _condition_met(ev, state, "danger_below",
                            {"area_id": "forest", "threshold": 0.5})
    assert result is False


def test_danger_below_returns_false_when_no_areas_slice() -> None:
    state = _make_state_no_areas()
    ev = _make_evaluator()
    result = _condition_met(ev, state, "danger_below",
                            {"area_id": "forest", "threshold": 0.5})
    assert result is False


# ------------------------------------------------------------------
# Unsupported conditions still return (False, 1)
# ------------------------------------------------------------------


def test_unknown_condition_type_returns_unsupported() -> None:
    state = _make_state_with_areas({})
    ev = _make_evaluator()
    condition = {"type": "totally_new_unknown_condition"}
    met, unsupported = ev._condition_met(state, condition)
    assert met is False
    assert unsupported == 1
