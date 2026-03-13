"""Tests for P29-A9a: NarrativePlanSlice milestone_outline extension."""

import copy

from app.game_core.state.slices.narrative_plan import NarrativePlanSlice
from app.game_core.state.delta import StateChange


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_outline(milestone_id: str = "m1", tick: int = 10) -> dict:
    return {
        "target_milestone_id": milestone_id,
        "chapter_id": "ch1",
        "computed_at_tick": tick,
        "steps": [
            {
                "index": 0,
                "description": "Talk to the farmer",
                "type": "dialogue",
                "condition": {"type": "npc_talked", "npc_id": "farmer"},
                "related_npcs": ["farmer"],
                "related_locations": ["cow_girl_farm"],
                "completed": False,
                "quest_id": None,
            },
            {
                "index": 1,
                "description": "Defeat the goblin patrol",
                "type": "combat",
                "condition": {"type": "kill_count", "monster_id": "goblin", "count": 3},
                "related_npcs": [],
                "related_locations": ["outer_cloisters"],
                "completed": False,
                "quest_id": None,
            },
        ],
    }


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

def test_milestone_outline_defaults_to_empty():
    slice_ = NarrativePlanSlice()
    assert slice_.milestone_outline == {}


def test_get_current_outline_step_returns_none_when_empty():
    slice_ = NarrativePlanSlice()
    assert slice_.get_current_outline_step() is None


# ---------------------------------------------------------------------------
# set_milestone_outline
# ---------------------------------------------------------------------------

def test_set_milestone_outline_stores_data():
    slice_ = NarrativePlanSlice()
    outline = _make_outline()
    slice_.set_milestone_outline(outline)

    assert slice_.milestone_outline["target_milestone_id"] == "m1"
    assert slice_.milestone_outline["chapter_id"] == "ch1"
    assert len(slice_.milestone_outline["steps"]) == 2


def test_set_milestone_outline_marks_dirty():
    slice_ = NarrativePlanSlice()
    assert not slice_.dirty
    slice_.set_milestone_outline(_make_outline())
    assert slice_.dirty


def test_set_milestone_outline_deep_copies_steps():
    slice_ = NarrativePlanSlice()
    outline = _make_outline()
    original_steps = outline["steps"]
    slice_.set_milestone_outline(outline)

    # Mutating the original list should NOT affect the stored outline
    original_steps.append({"index": 99})
    assert len(slice_.milestone_outline["steps"]) == 2


def test_set_milestone_outline_ignores_non_mapping():
    slice_ = NarrativePlanSlice()
    slice_.set_milestone_outline("not a dict")  # type: ignore[arg-type]
    assert slice_.milestone_outline == {}


def test_set_milestone_outline_replaces_existing():
    slice_ = NarrativePlanSlice()
    slice_.set_milestone_outline(_make_outline("m1"))
    slice_.set_milestone_outline(_make_outline("m2"))
    assert slice_.milestone_outline["target_milestone_id"] == "m2"


# ---------------------------------------------------------------------------
# mark_outline_step_completed
# ---------------------------------------------------------------------------

def test_mark_outline_step_completed_marks_correct_step():
    slice_ = NarrativePlanSlice()
    slice_.set_milestone_outline(_make_outline())
    slice_.clear_dirty()

    slice_.mark_outline_step_completed(0)
    steps = slice_.milestone_outline["steps"]
    assert steps[0]["completed"] is True
    assert steps[1]["completed"] is False


def test_mark_outline_step_completed_marks_dirty():
    slice_ = NarrativePlanSlice()
    slice_.set_milestone_outline(_make_outline())
    slice_.clear_dirty()

    slice_.mark_outline_step_completed(1)
    assert slice_.dirty


def test_mark_outline_step_completed_ignores_unknown_index():
    slice_ = NarrativePlanSlice()
    slice_.set_milestone_outline(_make_outline())
    slice_.clear_dirty()

    slice_.mark_outline_step_completed(99)  # no such step
    assert not slice_.dirty


def test_mark_outline_step_completed_noop_when_no_outline():
    slice_ = NarrativePlanSlice()
    slice_.mark_outline_step_completed(0)  # should not raise
    assert not slice_.dirty


# ---------------------------------------------------------------------------
# get_current_outline_step
# ---------------------------------------------------------------------------

def test_get_current_outline_step_returns_first_incomplete():
    slice_ = NarrativePlanSlice()
    outline = _make_outline()
    outline["steps"][0]["completed"] = True
    slice_.set_milestone_outline(outline)

    step = slice_.get_current_outline_step()
    assert step is not None
    assert step["index"] == 1


def test_get_current_outline_step_returns_none_when_all_done():
    slice_ = NarrativePlanSlice()
    outline = _make_outline()
    for s in outline["steps"]:
        s["completed"] = True
    slice_.set_milestone_outline(outline)

    assert slice_.get_current_outline_step() is None


def test_get_current_outline_step_returns_defensive_copy():
    slice_ = NarrativePlanSlice()
    slice_.set_milestone_outline(_make_outline())

    step = slice_.get_current_outline_step()
    assert step is not None
    step["completed"] = True  # mutate the copy

    # Internal state should be unchanged
    assert slice_.milestone_outline["steps"][0]["completed"] is False


# ---------------------------------------------------------------------------
# snapshot and restore round-trip
# ---------------------------------------------------------------------------

def test_snapshot_includes_milestone_outline():
    slice_ = NarrativePlanSlice()
    slice_.set_milestone_outline(_make_outline())

    snap = slice_.snapshot()
    assert "milestone_outline" in snap
    assert snap["milestone_outline"]["target_milestone_id"] == "m1"
    assert len(snap["milestone_outline"]["steps"]) == 2


def test_snapshot_milestone_outline_is_defensive_copy():
    slice_ = NarrativePlanSlice()
    slice_.set_milestone_outline(_make_outline())

    snap = slice_.snapshot()
    snap["milestone_outline"]["steps"].clear()

    # Internal state must remain intact
    assert len(slice_.milestone_outline["steps"]) == 2


def test_snapshot_empty_outline_returns_empty_dict():
    slice_ = NarrativePlanSlice()
    snap = slice_.snapshot()
    assert snap["milestone_outline"] == {}


def test_restore_round_trip():
    slice_ = NarrativePlanSlice()
    slice_.set_milestone_outline(_make_outline())
    snap = slice_.snapshot()

    slice2 = NarrativePlanSlice()
    slice2.restore(snap)

    assert slice2.milestone_outline["target_milestone_id"] == "m1"
    assert len(slice2.milestone_outline["steps"]) == 2
    assert slice2.milestone_outline["steps"][0]["index"] == 0


def test_restore_without_milestone_outline_defaults_to_empty():
    slice_ = NarrativePlanSlice()
    snap = slice_.snapshot()
    del snap["milestone_outline"]

    slice2 = NarrativePlanSlice()
    slice2.restore(snap)
    assert slice2.milestone_outline == {}


def test_restore_with_invalid_milestone_outline_defaults_to_empty():
    slice_ = NarrativePlanSlice()
    snap = slice_.snapshot()
    snap["milestone_outline"] = "not a dict"  # bad payload

    slice2 = NarrativePlanSlice()
    slice2.restore(snap)
    assert slice2.milestone_outline == {}


# ---------------------------------------------------------------------------
# apply_state_change integration
# ---------------------------------------------------------------------------

def test_apply_state_change_set_milestone_outline():
    slice_ = NarrativePlanSlice()
    outline = _make_outline()

    change = StateChange(
        slice="narrative_plan",
        operation="set",
        path="milestone_outline",
        value=outline,
    )
    slice_.apply_state_change(change)

    assert slice_.milestone_outline["target_milestone_id"] == "m1"
    assert slice_.dirty


def test_apply_state_change_step_completed():
    slice_ = NarrativePlanSlice()
    slice_.set_milestone_outline(_make_outline())
    slice_.clear_dirty()

    change = StateChange(
        slice="narrative_plan",
        operation="set",
        path="milestone_outline.step_completed",
        value=1,
    )
    slice_.apply_state_change(change)

    assert slice_.milestone_outline["steps"][1]["completed"] is True
    assert slice_.dirty
