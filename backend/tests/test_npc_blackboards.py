"""Tests for RelationSlice.npc_blackboards (1-B)."""

import pytest

from app.game_core.state.slices.relations import RelationSlice
from app.game_core.state.delta import StateChange


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_slice() -> RelationSlice:
    return RelationSlice()


# ---------------------------------------------------------------------------
# 1. Initial state
# ---------------------------------------------------------------------------

def test_npc_blackboards_initial_empty():
    s = _make_slice()
    assert s.npc_blackboards == {}


# ---------------------------------------------------------------------------
# 2. get_blackboard — unknown NPC returns empty dict
# ---------------------------------------------------------------------------

def test_get_blackboard_unknown_npc_returns_empty():
    s = _make_slice()
    board = s.get_blackboard("nonexistent_npc")
    assert board == {}


# ---------------------------------------------------------------------------
# 3. get_blackboard — returns defensive copy
# ---------------------------------------------------------------------------

def test_get_blackboard_returns_defensive_copy():
    s = _make_slice()
    s.update_blackboard("goblin_slayer", {"mood": "alert"})

    copy = s.get_blackboard("goblin_slayer")
    copy["mood"] = "MUTATED"

    assert s.npc_blackboards["goblin_slayer"]["mood"] == "alert"


# ---------------------------------------------------------------------------
# 4. update_blackboard — creates entry if absent
# ---------------------------------------------------------------------------

def test_update_blackboard_creates_entry():
    s = _make_slice()
    s.update_blackboard("innkeeper", {"thoughts": "business is slow"})

    assert s.npc_blackboards["innkeeper"]["thoughts"] == "business is slow"


# ---------------------------------------------------------------------------
# 5. update_blackboard — merges into existing (does not overwrite whole dict)
# ---------------------------------------------------------------------------

def test_update_blackboard_merges():
    s = _make_slice()
    s.update_blackboard("innkeeper", {"mood": "neutral", "goals": ["serve guests"]})
    s.update_blackboard("innkeeper", {"mood": "happy"})

    board = s.get_blackboard("innkeeper")
    assert board["mood"] == "happy"
    assert board["goals"] == ["serve guests"]


# ---------------------------------------------------------------------------
# 6. update_blackboard — marks slice dirty
# ---------------------------------------------------------------------------

def test_update_blackboard_marks_dirty():
    s = _make_slice()
    assert not s.dirty
    s.update_blackboard("knight", {"thoughts": "glory awaits"})
    assert s.dirty


# ---------------------------------------------------------------------------
# 7. snapshot / restore round-trip
# ---------------------------------------------------------------------------

def test_snapshot_restore_roundtrip():
    s = _make_slice()
    s.update_blackboard("goblin_slayer", {
        "thoughts": "goblins are organized",
        "goals": ["scout ruins", "wait for adventurer"],
        "mood": "vigilant",
        "updated_tick": 15,
    })

    snap = s.snapshot()
    assert "npc_blackboards" in snap

    s2 = _make_slice()
    s2.restore(snap)

    board = s2.get_blackboard("goblin_slayer")
    assert board["thoughts"] == "goblins are organized"
    assert board["goals"] == ["scout ruins", "wait for adventurer"]
    assert board["mood"] == "vigilant"
    assert board["updated_tick"] == 15


# ---------------------------------------------------------------------------
# 8. restore from old save (no npc_blackboards key) — defaults to empty
# ---------------------------------------------------------------------------

def test_restore_legacy_payload_no_npc_blackboards():
    s = _make_slice()
    old_payload = {
        "npc_dispositions": {},
        "relationship_stages": {},
        "faction_standings": {},
        "npc_impressions": {},
        "shop_states": {},
        # npc_blackboards intentionally absent
    }
    s.restore(old_payload)
    assert s.npc_blackboards == {}
    assert s.get_blackboard("anyone") == {}


# ---------------------------------------------------------------------------
# 9. apply_state_change — operation "set" replaces whole blackboard
# ---------------------------------------------------------------------------

def test_apply_state_change_set_replaces_blackboard():
    s = _make_slice()
    s.update_blackboard("merchant", {"mood": "greedy", "goals": ["sell stuff"]})

    new_board = {"mood": "content", "attitude_towards_player": "trustworthy"}
    change = StateChange(
        slice="relations",
        operation="set",
        path="npc_blackboards.merchant",
        value=new_board,
    )
    s.apply_state_change(change)

    board = s.get_blackboard("merchant")
    assert board["mood"] == "content"
    assert board["attitude_towards_player"] == "trustworthy"
    # old key gone
    assert "goals" not in board


# ---------------------------------------------------------------------------
# 10. apply_state_change — marks dirty
# ---------------------------------------------------------------------------

def test_apply_state_change_marks_dirty():
    s = _make_slice()
    s.clear_dirty()

    change = StateChange(
        slice="relations",
        operation="set",
        path="npc_blackboards.smith",
        value={"mood": "focused"},
    )
    s.apply_state_change(change)
    assert s.dirty


# ---------------------------------------------------------------------------
# 11. validate — passes when blackboards are dicts
# ---------------------------------------------------------------------------

def test_validate_passes_for_valid_blackboards():
    s = _make_slice()
    s.update_blackboard("npc_a", {"key": "value"})
    s.update_blackboard("npc_b", {})
    issues = s.validate()
    assert issues == []


# ---------------------------------------------------------------------------
# 12. unsupported path still raises ValueError
# ---------------------------------------------------------------------------

def test_apply_state_change_unsupported_path_raises():
    s = _make_slice()
    change = StateChange(
        slice="relations",
        operation="set",
        path="unknown_field.foo",
        value="bar",
    )
    with pytest.raises(ValueError, match="unsupported relation state change"):
        s.apply_state_change(change)
