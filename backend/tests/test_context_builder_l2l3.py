"""Tests for N-9: L2 dynamic_sub_area_counts and L3 dynamic_sub_areas fields."""

from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.narrative.context_builder import AgentContextBuilder
from app.game_core.state import StateContainer


def _builder() -> AgentContextBuilder:
    world = WorldInstance("test_world")
    state = StateContainer()
    return AgentContextBuilder(world=world, state=state)


def test_l2_dynamic_sub_area_counts_present_and_zero_when_no_sub_areas() -> None:
    builder = _builder()
    result = builder._build_l2("market", None)
    assert "dynamic_sub_area_counts" in result
    counts = result["dynamic_sub_area_counts"]
    assert counts == {"permanent": 0, "timed": 0, "temporary": 0, "total": 0}


def test_l2_dynamic_sub_area_counts_correct_values() -> None:
    builder = _builder()
    area_state = {
        "temporary_sub_areas": [
            {"id": "sa1", "expiry": -1},   # permanent
            {"id": "sa2", "expiry": 48},   # timed (>=24)
            {"id": "sa3", "expiry": 3},    # temporary
            {"id": "sa4", "expiry": 6},    # temporary
        ]
    }
    result = builder._build_l2("forest", area_state)
    counts = result["dynamic_sub_area_counts"]
    assert counts["permanent"] == 1
    assert counts["timed"] == 1
    assert counts["temporary"] == 2
    assert counts["total"] == 4


def test_l3_dynamic_sub_areas_present_and_empty_when_no_sub_areas() -> None:
    builder = _builder()
    result = builder._build_l3("market", None, None)
    assert "dynamic_sub_areas" in result
    assert result["dynamic_sub_areas"] == []


def test_l3_dynamic_sub_areas_returns_copies_of_all_sub_areas() -> None:
    builder = _builder()
    area_state = {
        "temporary_sub_areas": [
            {"id": "cave_entrance", "expiry": 5, "label": "Cave"},
            {"id": "ruins_east", "expiry": 48, "label": "Ruins"},
        ]
    }
    result = builder._build_l3("wilderness", None, area_state)
    sub_areas = result["dynamic_sub_areas"]
    assert len(sub_areas) == 2
    assert sub_areas[0]["id"] == "cave_entrance"
    assert sub_areas[1]["id"] == "ruins_east"
    # defensive copy — mutation should not affect area_state
    sub_areas[0]["id"] = "mutated"
    assert area_state["temporary_sub_areas"][0]["id"] == "cave_entrance"
