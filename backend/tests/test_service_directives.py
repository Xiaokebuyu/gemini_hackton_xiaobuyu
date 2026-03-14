"""Tests for Phase 2: assign_service / revoke_service directive contracts,
command handlers, and NpcDirector apply_directive integration.

Decision record: D-Svc02 (narrative.md)
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.orchestration.models import SSEEvent
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.planning.directive_contracts import (
    SUPPORTED_PLANNER_DIRECTIVE_KINDS,
    validate_planner_directive,
)
from app.game_core.planning.npc_director import NpcDirectorSubSystem
from app.game_core.rules.defaults import register_default_rules_handlers
from app.game_core.rules.engine import RulesEngine
from app.game_core.rules.handlers.planner import PlannerNpcHandler
from app.game_core.rules.models import Command
from app.game_core.state import StateDelta, StateChange, StateContainer
from app.game_core.state.slices import NarrativePlanSlice, SceneSlice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state() -> StateContainer:
    state = StateContainer()
    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({})
    state.register(narrative_plan)
    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    return state


def _make_world() -> WorldInstance:
    return WorldInstance("test")


def _make_context(state: StateContainer, world: WorldInstance) -> SettlementContext:
    engine = RulesEngine()
    register_default_rules_handlers(engine)
    engine.register(PlannerNpcHandler())
    scene_bus = SceneBus(state.scene)
    change_log: list[StateChange] = []

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        change_log.extend(delta.changes)

    return SettlementContext(
        change_log=change_log,
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=engine,
        _apply_delta=_apply_delta,
    )


# ---------------------------------------------------------------------------
# Directive contract: assign_service
# ---------------------------------------------------------------------------

def test_assign_service_in_supported_kinds() -> None:
    assert "assign_service" in SUPPORTED_PLANNER_DIRECTIVE_KINDS


def test_revoke_service_in_supported_kinds() -> None:
    assert "revoke_service" in SUPPORTED_PLANNER_DIRECTIVE_KINDS


def test_assign_service_contract_minimal_valid() -> None:
    result = validate_planner_directive({
        "kind": "assign_service",
        "payload": {
            "npc_id": "npc_healer",
            "service_id": "heal",
            "label": "Healing Touch",
        },
    })
    assert result.ok is True
    assert result.reason_code is None
    assert result.payload["npc_id"] == "npc_healer"
    assert result.payload["service_id"] == "heal"
    assert result.payload["label"] == "Healing Touch"


def test_assign_service_contract_full_payload() -> None:
    result = validate_planner_directive({
        "kind": "assign_service",
        "payload": {
            "npc_id": "npc_healer",
            "service_id": "heal",
            "label": "Full Healing",
            "price": 50,
            "effects": [{"type": "restore_hp", "amount": 100}],
            "notes": "Emergency heal",
            "expiry_ticks": 10,
            "preconditions": {"min_gold": 50},
            "one_shot": True,
        },
    })
    assert result.ok is True
    assert result.payload["price"] == 50
    assert result.payload["expiry_ticks"] == 10
    assert result.payload["one_shot"] is True


def test_assign_service_contract_missing_npc_id() -> None:
    result = validate_planner_directive({
        "kind": "assign_service",
        "payload": {"service_id": "heal", "label": "Heal"},
    })
    assert result.ok is False
    assert result.reason_code == "missing_npc_id"


def test_assign_service_contract_missing_service_id() -> None:
    result = validate_planner_directive({
        "kind": "assign_service",
        "payload": {"npc_id": "npc_healer", "label": "Heal"},
    })
    assert result.ok is False
    assert result.reason_code == "missing_service_id"


def test_assign_service_contract_missing_label() -> None:
    result = validate_planner_directive({
        "kind": "assign_service",
        "payload": {"npc_id": "npc_healer", "service_id": "heal"},
    })
    assert result.ok is False
    assert result.reason_code == "missing_label"


def test_assign_service_contract_invalid_price_negative() -> None:
    result = validate_planner_directive({
        "kind": "assign_service",
        "payload": {
            "npc_id": "npc_healer",
            "service_id": "heal",
            "label": "Heal",
            "price": -10,
        },
    })
    assert result.ok is False
    assert result.reason_code == "invalid_price"


def test_assign_service_contract_invalid_effects_unknown_type() -> None:
    result = validate_planner_directive({
        "kind": "assign_service",
        "payload": {
            "npc_id": "npc_healer",
            "service_id": "heal",
            "label": "Heal",
            "effects": [{"type": "do_magic"}],
        },
    })
    assert result.ok is False
    assert result.reason_code == "invalid_effects"


def test_assign_service_contract_invalid_effects_not_a_list() -> None:
    result = validate_planner_directive({
        "kind": "assign_service",
        "payload": {
            "npc_id": "npc_healer",
            "service_id": "heal",
            "label": "Heal",
            "effects": "restore_hp",
        },
    })
    assert result.ok is False
    assert result.reason_code == "invalid_effects"


def test_assign_service_contract_invalid_expiry_ticks_negative() -> None:
    result = validate_planner_directive({
        "kind": "assign_service",
        "payload": {
            "npc_id": "npc_healer",
            "service_id": "heal",
            "label": "Heal",
            "expiry_ticks": -1,
        },
    })
    assert result.ok is False
    assert result.reason_code == "invalid_expiry_ticks"


# ---------------------------------------------------------------------------
# Directive contract: revoke_service
# ---------------------------------------------------------------------------

def test_revoke_service_contract_valid() -> None:
    result = validate_planner_directive({
        "kind": "revoke_service",
        "payload": {"npc_id": "npc_healer", "service_id": "heal"},
    })
    assert result.ok is True
    assert result.reason_code is None
    assert result.payload["npc_id"] == "npc_healer"
    assert result.payload["service_id"] == "heal"


def test_revoke_service_contract_missing_npc_id() -> None:
    result = validate_planner_directive({
        "kind": "revoke_service",
        "payload": {"service_id": "heal"},
    })
    assert result.ok is False
    assert result.reason_code == "missing_npc_id"


def test_revoke_service_contract_missing_service_id() -> None:
    result = validate_planner_directive({
        "kind": "revoke_service",
        "payload": {"npc_id": "npc_healer"},
    })
    assert result.ok is False
    assert result.reason_code == "missing_service_id"


# ---------------------------------------------------------------------------
# Command handler: PlannerNpcHandler planner_assign_service
# ---------------------------------------------------------------------------

def test_planner_assign_service_handler_basic() -> None:
    state = _make_state()
    world = _make_world()
    handler = PlannerNpcHandler()

    result = handler.compute(
        Command(
            type="planner_assign_service",
            params={
                "npc_id": "npc_healer",
                "service_id": "heal",
                "label": "Healing Touch",
                "current_tick": 5,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True, result.errors
    assert len(result.delta.changes) == 1
    change = result.delta.changes[0]
    assert change.slice == "narrative_plan"
    assert change.path == "npc_services.assign"
    assert change.value["npc_id"] == "npc_healer"
    assert change.value["service_id"] == "heal"
    assert change.value["label"] == "Healing Touch"
    assert change.value["assigned_tick"] == 5
    assert change.value["source"] == "planner"


def test_planner_assign_service_handler_with_expiry() -> None:
    state = _make_state()
    world = _make_world()
    handler = PlannerNpcHandler()

    result = handler.compute(
        Command(
            type="planner_assign_service",
            params={
                "npc_id": "npc_healer",
                "service_id": "heal",
                "label": "Heal",
                "current_tick": 10,
                "expiry_ticks": 5,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    change = result.delta.changes[0]
    # expiry_tick = current_tick + expiry_ticks = 10 + 5 = 15
    assert change.value["expiry_tick"] == 15


def test_planner_assign_service_handler_defaults_price_zero() -> None:
    state = _make_state()
    world = _make_world()
    handler = PlannerNpcHandler()

    result = handler.compute(
        Command(
            type="planner_assign_service",
            params={
                "npc_id": "npc_healer",
                "service_id": "heal",
                "label": "Free Heal",
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.delta.changes[0].value["price"] == 0


def test_planner_assign_service_handler_missing_npc_id_rejected() -> None:
    state = _make_state()
    world = _make_world()
    handler = PlannerNpcHandler()

    result = handler.compute(
        Command(
            type="planner_assign_service",
            params={"service_id": "heal", "label": "Heal"},
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is False


def test_planner_revoke_service_handler_basic() -> None:
    state = _make_state()
    world = _make_world()
    handler = PlannerNpcHandler()

    result = handler.compute(
        Command(
            type="planner_revoke_service",
            params={"npc_id": "npc_healer", "service_id": "heal"},
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    assert len(result.delta.changes) == 1
    change = result.delta.changes[0]
    assert change.slice == "narrative_plan"
    assert change.path == "npc_services.revoke"
    assert change.operation == "remove"
    assert change.value["npc_id"] == "npc_healer"
    assert change.value["service_id"] == "heal"


def test_planner_revoke_service_handler_missing_service_id_rejected() -> None:
    state = _make_state()
    world = _make_world()
    handler = PlannerNpcHandler()

    result = handler.compute(
        Command(
            type="planner_revoke_service",
            params={"npc_id": "npc_healer"},
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is False


# ---------------------------------------------------------------------------
# NpcDirector apply_directive integration
# ---------------------------------------------------------------------------

def test_npc_director_handles_assign_service() -> None:
    assert "assign_service" in NpcDirectorSubSystem._HANDLES


def test_npc_director_handles_revoke_service() -> None:
    assert "revoke_service" in NpcDirectorSubSystem._HANDLES


def test_npc_director_apply_assign_service_e2e() -> None:
    """Full end-to-end: apply_directive -> command -> state change applied."""
    async def _run() -> None:
        state = _make_state()
        world = _make_world()
        context = _make_context(state, world)

        director = NpcDirectorSubSystem()
        result = director.apply_directive(
            "assign_service",
            {
                "npc_id": "npc_healer",
                "service_id": "heal",
                "label": "Healing Touch",
                "price": 20,
                "effects": [{"type": "restore_hp", "amount": 30}],
            },
            context,
            current_tick=3,
        )

        assert result is True
        services = state.narrative_plan.get_services("npc_healer")
        assert len(services) == 1
        assert services[0]["service_id"] == "heal"
        assert services[0]["label"] == "Healing Touch"
        assert services[0]["price"] == 20
        assert services[0]["effects"] == [{"type": "restore_hp", "amount": 30}]
        assert services[0]["assigned_tick"] == 3
        assert services[0]["source"] == "planner"

    asyncio.run(_run())


def test_npc_director_apply_revoke_service_e2e() -> None:
    """assign then revoke: service is removed from the slice."""
    async def _run() -> None:
        state = _make_state()
        world = _make_world()
        context = _make_context(state, world)

        director = NpcDirectorSubSystem()

        # Assign first
        director.apply_directive(
            "assign_service",
            {"npc_id": "npc_healer", "service_id": "heal", "label": "Heal"},
            context,
            current_tick=1,
        )
        assert len(state.narrative_plan.get_services("npc_healer")) == 1

        # Now revoke
        result = director.apply_directive(
            "revoke_service",
            {"npc_id": "npc_healer", "service_id": "heal"},
            context,
            current_tick=2,
        )

        assert result is True
        services = state.narrative_plan.get_services("npc_healer")
        assert len(services) == 0

    asyncio.run(_run())


def test_npc_director_unsupported_kind_returns_error() -> None:
    async def _run() -> None:
        state = _make_state()
        world = _make_world()
        context = _make_context(state, world)

        director = NpcDirectorSubSystem()
        result = director.apply_directive(
            "unknown_service_kind",
            {},
            context,
            current_tick=0,
        )
        assert result == "unsupported_kind"

    asyncio.run(_run())
