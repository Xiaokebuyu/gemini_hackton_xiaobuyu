"""Tests for move_npc directive and quest_hint SSE event."""
from app.game_core.planning.directive_contracts import (
    validate_planner_directive,
)
from app.game_core.orchestration.hooks.narrative_planner import (
    NarrativePlannerDecision,
    NarrativePlannerHook,
)
from app.game_core.rules.handlers.planner import PlannerNpcHandler
from app.game_core.rules.models import Command
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, NarrativePlanSlice
from app.game_core.state.slices.area import AreaState


# ---------------------------------------------------------------------------
# Contract validation
# ---------------------------------------------------------------------------


def test_move_npc_contract_valid():
    result = validate_planner_directive({
        "kind": "move_npc",
        "payload": {
            "npc_id": "cow_girl",
            "area_id": "cow_girl_farm",
            "location_id": "main_house",
            "room_id": "dining_table",
        },
    })
    assert result.ok is True
    assert result.reason_code is None
    assert result.payload["npc_id"] == "cow_girl"
    assert result.payload["area_id"] == "cow_girl_farm"
    assert result.payload["location_id"] == "main_house"
    assert result.payload["room_id"] == "dining_table"


def test_move_npc_contract_minimal():
    result = validate_planner_directive({
        "kind": "move_npc",
        "payload": {"npc_id": "cow_girl", "area_id": "cow_girl_farm"},
    })
    assert result.ok is True
    assert result.reason_code is None
    assert "location_id" not in result.payload
    assert "room_id" not in result.payload


def test_move_npc_contract_missing_npc_id():
    result = validate_planner_directive({
        "kind": "move_npc",
        "payload": {"area_id": "cow_girl_farm"},
    })
    assert result.ok is False
    assert result.reason_code == "missing_npc_id"


def test_move_npc_contract_missing_area_id():
    result = validate_planner_directive({
        "kind": "move_npc",
        "payload": {"npc_id": "cow_girl"},
    })
    assert result.ok is False
    assert result.reason_code == "missing_area_id"


def test_move_npc_contract_room_requires_location():
    result = validate_planner_directive({
        "kind": "move_npc",
        "payload": {
            "npc_id": "cow_girl",
            "area_id": "cow_girl_farm",
            "room_id": "dining_table",
        },
    })
    assert result.ok is False
    assert result.reason_code == "room_id_requires_location_id"


# ---------------------------------------------------------------------------
# Handler validation
# ---------------------------------------------------------------------------


def _build_state_with_area(area_id: str = "cow_girl_farm") -> StateContainer:
    state = StateContainer()
    areas = AreaSlice()
    areas.areas[area_id] = AreaState()
    state.register(areas)
    state.register(NarrativePlanSlice())
    return state


def test_move_npc_handler_validates_area_exists():
    handler = PlannerNpcHandler()
    state = _build_state_with_area("cow_girl_farm")

    cmd_ok = Command(
        type="planner_move_npc",
        params={"npc_id": "cow_girl", "area_id": "cow_girl_farm"},
        source="narrative_planner",
    )
    result = handler.validate(cmd_ok, state, None)
    assert result.ok is True

    cmd_bad = Command(
        type="planner_move_npc",
        params={"npc_id": "cow_girl", "area_id": "nonexistent"},
        source="narrative_planner",
    )
    result = handler.validate(cmd_bad, state, None)
    assert result.ok is False
    assert "unknown area_id" in (result.reason or "")


def test_move_npc_handler_compute_state_change():
    handler = PlannerNpcHandler()
    state = _build_state_with_area("cow_girl_farm")

    cmd = Command(
        type="planner_move_npc",
        params={
            "npc_id": "cow_girl",
            "area_id": "cow_girl_farm",
            "location_id": "main_house",
            "room_id": "dining_table",
        },
        source="narrative_planner",
    )
    result = handler.compute(cmd, state, None)
    assert result.executed is True
    assert result.delta is not None
    changes = result.delta.changes
    presence_change = [c for c in changes if "npc_presence" in c.path]
    assert len(presence_change) == 1
    assert presence_change[0].value["area_id"] == "cow_girl_farm"
    assert presence_change[0].value["location_id"] == "main_house"
    assert presence_change[0].value["room_id"] == "dining_table"
    assert presence_change[0].value["source"] == "planner"


# ---------------------------------------------------------------------------
# NarrativePlannerDecision player_hint normalization
# ---------------------------------------------------------------------------


def test_normalize_decision_with_player_hint():
    decision = NarrativePlannerHook._normalize_decision({
        "directives": [],
        "player_hint": "回主屋和牧牛妹享用炖菜",
    })
    assert decision.player_hint == "回主屋和牧牛妹享用炖菜"


def test_normalize_decision_without_player_hint():
    decision = NarrativePlannerHook._normalize_decision({
        "directives": [],
    })
    assert decision.player_hint is None


def test_normalize_decision_empty_player_hint():
    decision = NarrativePlannerHook._normalize_decision({
        "directives": [],
        "player_hint": "   ",
    })
    assert decision.player_hint is None
