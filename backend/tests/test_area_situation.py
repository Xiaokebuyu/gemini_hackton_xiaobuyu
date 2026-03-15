"""Tests for AreaSlice area_situation + area_events (1-A).

Also covers NarrativePlannerHook._update_area_situation() (task 2 / 3-B).
"""

import asyncio

from app.game_core.content import WorldInstance
from app.game_core.orchestration.hooks.narrative_planner import NarrativePlannerHook
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.state import StateContainer
from app.game_core.state.delta import StateChange
from app.game_core.state.slices import SceneSlice, TimeSlice
from app.game_core.state.slices.area import AreaSlice, AreaState
from app.game_core.state.slices.player import PlayerSlice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_slice() -> AreaSlice:
    s = AreaSlice()
    return s


def _sample_event(tick: int = 1, text: str = "something happened") -> dict:
    return {
        "tick": tick,
        "event": text,
        "source": "test",
        "severity": "minor",
    }


# ---------------------------------------------------------------------------
# 1. AreaState dataclass defaults
# ---------------------------------------------------------------------------

def test_areastate_defaults():
    state = AreaState()
    assert state.area_situation == ""
    assert state.area_events == []


# ---------------------------------------------------------------------------
# 2. snapshot round-trip
# ---------------------------------------------------------------------------

def test_snapshot_includes_new_fields():
    slice_ = _make_slice()
    slice_.set_area_situation("zone_a", "Goblins are massing at the north gate.")
    slice_.append_area_event("zone_a", _sample_event(1, "First event"))
    snap = slice_.snapshot()
    area_snap = snap["areas"]["zone_a"]
    assert area_snap["area_situation"] == "Goblins are massing at the north gate."
    assert len(area_snap["area_events"]) == 1
    assert area_snap["area_events"][0]["event"] == "First event"


def test_snapshot_restore_round_trip():
    slice_ = _make_slice()
    slice_.set_area_situation("zone_b", "Situation normal.")
    for i in range(3):
        slice_.append_area_event("zone_b", _sample_event(i, f"Event {i}"))

    payload = slice_.snapshot()

    slice2 = AreaSlice()
    slice2.restore(payload)
    assert slice2.get_area_situation("zone_b") == "Situation normal."
    events = slice2.get_area_events("zone_b")
    assert len(events) == 3
    assert events[2]["event"] == "Event 2"


# ---------------------------------------------------------------------------
# 3. Old save (no new fields) restores with defaults
# ---------------------------------------------------------------------------

def test_old_save_compat_defaults():
    """A saved payload without area_situation / area_events should not crash."""
    old_payload = {
        "areas": {
            "old_zone": {
                "exploration": "discovered",
                "danger_level": 1.5,
                "properties": {},
                "tags": [],
                "temporary_sub_areas": [],
                "discovered_items": [],
                "npc_locations": {},
                "npc_rooms": {},
                "npc_presence_sources": {},
                "discovered_rooms": [],
                "board_bulletins": {},
                "container_states": {},
                "interactable_states": {},
                "hostile_tracking": {},
                "permanent_hostile_slots": {},
                "dynamic_rooms": [],
                "scoped_interactable_overlays": {},
                # intentionally omitting area_situation and area_events
            }
        }
    }
    slice_ = AreaSlice()
    slice_.restore(old_payload)
    assert slice_.get_area_situation("old_zone") == ""
    assert slice_.get_area_events("old_zone") == []


# ---------------------------------------------------------------------------
# 4. append_area_event + 20-entry rolling cap
# ---------------------------------------------------------------------------

def test_append_area_event_basic():
    slice_ = _make_slice()
    slice_.append_area_event("zone_c", _sample_event(1, "alpha"))
    slice_.append_area_event("zone_c", _sample_event(2, "beta"))
    events = slice_.get_area_events("zone_c")
    assert len(events) == 2
    assert events[0]["event"] == "alpha"
    assert events[1]["event"] == "beta"


def test_append_area_event_rolling_cap():
    slice_ = _make_slice()
    for i in range(25):
        slice_.append_area_event("zone_d", _sample_event(i, f"event_{i}"))
    events = slice_.get_area_events("zone_d")
    assert len(events) == 20
    # oldest 5 (event_0..4) should be gone, newest retained
    assert events[0]["event"] == "event_5"
    assert events[-1]["event"] == "event_24"


def test_append_area_event_marks_dirty():
    slice_ = _make_slice()
    slice_.clear_dirty()
    slice_.append_area_event("zone_e", _sample_event())
    assert slice_.dirty


# ---------------------------------------------------------------------------
# 5. set_area_situation
# ---------------------------------------------------------------------------

def test_set_area_situation():
    slice_ = _make_slice()
    slice_.set_area_situation("zone_f", "Very dangerous.")
    assert slice_.get_area_situation("zone_f") == "Very dangerous."


def test_set_area_situation_marks_dirty():
    slice_ = _make_slice()
    slice_.clear_dirty()
    slice_.set_area_situation("zone_g", "Quiet.")
    assert slice_.dirty


def test_get_area_situation_unknown_area():
    slice_ = _make_slice()
    assert slice_.get_area_situation("nonexistent") == ""


def test_get_area_events_unknown_area():
    slice_ = _make_slice()
    assert slice_.get_area_events("nonexistent") == []


# ---------------------------------------------------------------------------
# 6. get_area_events returns defensive copy
# ---------------------------------------------------------------------------

def test_get_area_events_defensive_copy():
    slice_ = _make_slice()
    slice_.append_area_event("zone_h", _sample_event(1, "original"))
    copy1 = slice_.get_area_events("zone_h")
    copy1[0]["event"] = "mutated"
    # internal state should be unaffected
    copy2 = slice_.get_area_events("zone_h")
    assert copy2[0]["event"] == "original"


# ---------------------------------------------------------------------------
# 7. apply_state_change paths
# ---------------------------------------------------------------------------

def test_apply_state_change_area_events_add():
    slice_ = _make_slice()
    change = StateChange(
        slice="areas",
        operation="add",
        path="zone_i.area_events",
        value={"tick": 5, "event": "via state change", "source": "test", "severity": "major"},
    )
    slice_.apply_state_change(change)
    events = slice_.get_area_events("zone_i")
    assert len(events) == 1
    assert events[0]["event"] == "via state change"


def test_apply_state_change_area_events_set():
    slice_ = _make_slice()
    slice_.append_area_event("zone_j", _sample_event(1, "old"))
    change = StateChange(
        slice="areas",
        operation="set",
        path="zone_j.area_events",
        value=[{"tick": 10, "event": "replaced", "source": "test", "severity": "minor"}],
    )
    slice_.apply_state_change(change)
    events = slice_.get_area_events("zone_j")
    assert len(events) == 1
    assert events[0]["event"] == "replaced"


def test_apply_state_change_area_events_bad_operation_logs_warning(caplog):
    import logging
    slice_ = _make_slice()
    change = StateChange(
        slice="areas",
        operation="remove",
        path="zone_k.area_events",
        value={},
    )
    with caplog.at_level(logging.WARNING):
        slice_.apply_state_change(change)
    assert any("area_events" in rec.message for rec in caplog.records)


def test_apply_state_change_area_situation_set():
    slice_ = _make_slice()
    change = StateChange(
        slice="areas",
        operation="set",
        path="zone_l.area_situation",
        value="New situation text.",
    )
    slice_.apply_state_change(change)
    assert slice_.get_area_situation("zone_l") == "New situation text."


def test_apply_state_change_area_situation_bad_operation_logs_warning(caplog):
    import logging
    slice_ = _make_slice()
    change = StateChange(
        slice="areas",
        operation="add",
        path="zone_m.area_situation",
        value="bad",
    )
    with caplog.at_level(logging.WARNING):
        slice_.apply_state_change(change)
    assert any("area_situation" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# 8. validate() checks
# ---------------------------------------------------------------------------

def test_validate_passes_with_new_fields():
    slice_ = _make_slice()
    slice_.set_area_situation("zone_n", "Fine.")
    slice_.append_area_event("zone_n", _sample_event())
    issues = slice_.validate()
    assert issues == []


def test_validate_catches_bad_area_situation():
    slice_ = _make_slice()
    # Directly corrupt the field (bypassing the setter)
    area = slice_.get_area("zone_o")
    object.__setattr__(area, "area_situation", 123)
    issues = slice_.validate()
    assert any("area_situation" in i for i in issues)


def test_validate_catches_bad_area_events():
    slice_ = _make_slice()
    area = slice_.get_area("zone_p")
    object.__setattr__(area, "area_events", "not a list")
    issues = slice_.validate()
    assert any("area_events" in i for i in issues)


# ---------------------------------------------------------------------------
# 9. NarrativePlannerHook._update_area_situation() integration (task 2 / 3-B)
# ---------------------------------------------------------------------------


def _make_situation_context(
    *,
    area_id: str = "town",
    danger_level: float = 1.0,
    area_tags: list | None = None,
    events: list | None = None,
    hostile_tracking: dict | None = None,
) -> SettlementContext:
    """Build a minimal SettlementContext for testing _update_area_situation."""
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 8})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": area_id, "current_location": "main_square"})
    state.register(player)

    areas = AreaSlice()
    area_data: dict = {
        "danger_level": danger_level,
        "tags": area_tags or [],
        "area_events": events or [],
        "hostile_tracking": hostile_tracking or {},
    }
    areas.restore({"areas": {area_id: area_data}})
    state.register(areas)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)

    return SettlementContext(
        change_log=[],
        state=state,
        world=WorldInstance("test_world"),
        scene_bus=SceneBus(scene_slice),
        _rules_engine=RulesEngine(),
        _apply_delta=lambda delta: None,
    )


def test_update_area_situation_empty_state_produces_empty_string():
    """No events, low danger, no hostiles → situation is empty string."""
    ctx = _make_situation_context(danger_level=1.0)
    hook = NarrativePlannerHook()
    hook._update_area_situation(ctx)
    assert ctx.state.areas.get_area_situation("town") == ""


def test_update_area_situation_high_danger_includes_warning():
    """danger_level >= 3.0 → situation text mentions danger."""
    ctx = _make_situation_context(danger_level=3.5)
    hook = NarrativePlannerHook()
    hook._update_area_situation(ctx)
    situation = ctx.state.areas.get_area_situation("town")
    assert "危险等级较高" in situation
    assert "3.5" in situation


def test_update_area_situation_recent_events_included():
    """Recent area_events (up to last 3) are concatenated into situation text."""
    events = [
        {"tick": 1, "event": "冒险者抵达", "source": "test", "severity": "minor"},
        {"tick": 2, "event": "战斗结束", "source": "test", "severity": "major"},
        {"tick": 3, "event": "居民逃跑", "source": "test", "severity": "major"},
    ]
    ctx = _make_situation_context(events=events)
    hook = NarrativePlannerHook()
    hook._update_area_situation(ctx)
    situation = ctx.state.areas.get_area_situation("town")
    assert "近期事件" in situation
    assert "居民逃跑" in situation


def test_update_area_situation_hostile_tracking_counts():
    """Active and cleared encounters are counted correctly."""
    hostile_tracking = {
        "dungeon_north": {"status": "active", "encounter_id": "enc_1"},
        "dungeon_south": {"status": "active", "encounter_id": "enc_2"},
        "dungeon_west": {"status": "cleared", "encounter_id": "enc_3"},
    }
    ctx = _make_situation_context(hostile_tracking=hostile_tracking)
    hook = NarrativePlannerHook()
    hook._update_area_situation(ctx)
    situation = ctx.state.areas.get_area_situation("town")
    assert "2处已知敌对遭遇" in situation
    assert "已清除1处遭遇" in situation
