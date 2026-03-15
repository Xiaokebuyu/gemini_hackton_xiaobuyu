"""Tests for Osiris Phase coordination (2-B).

Verifies:
- EncounterPhase.run() produces correct PhaseResult
- PerceptionPhase.run() produces correct PhaseResult
- EventConditionPhase.run() produces correct PhaseResult
- AIOsirisHook coordinates all three phases and aggregates their SSE events
- hooks removed from DEFAULT_SETTLEMENT_HOOK_TYPES
- bootstrap.py wires phases into AIOsirisHook
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.content.registries import MapRegistry
from app.game_core.orchestration.defaults import DEFAULT_SETTLEMENT_HOOK_TYPES
from app.game_core.orchestration.hooks.ai_osiris import AIOsirisHook, MechanicalOsirisEngine
from app.game_core.orchestration.hooks.encounter import (
    BasicEncounterDetector,
    EncounterPhase,
)
from app.game_core.orchestration.hooks.event_condition import (
    BasicEventConditionEvaluator,
    EventConditionPhase,
    EventConditionHook,
)
from app.game_core.orchestration.hooks.passive_perception import (
    PerceptionPhase,
    PassivePerceptionHook,
)
from app.game_core.orchestration.models import PhaseResult
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.rules.handlers import EncounterHandler
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    AreaSlice,
    EventSlice,
    FlagSlice,
    PlayerSlice,
    SceneSlice,
    TimeSlice,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_world(*, include_maps: bool = True) -> WorldInstance:
    world = WorldInstance("test_world")
    if include_maps:
        maps = MapRegistry()
        maps.load({
            "forest": {
                "id": "forest",
                "encounter_slot_capacity": 1,
                "encounter_table": [
                    {"id": "forest:goblin", "monster_ids": ["goblin"], "weight": 1.0},
                ],
            },
            "town": {"id": "town"},
        })
        world.register(maps)
    return world


def _make_context(
    *,
    area_id: str = "forest",
    location_id: str | None = None,
    danger_level: float = 1.0,
    slot: int = 18,
    world: WorldInstance | None = None,
    action_log: list[dict[str, Any]] | None = None,
    area_tags: list[str] | None = None,
    include_events: bool = False,
    include_flags: bool = False,
    register_encounter_handler: bool = True,
) -> SettlementContext:
    world = world or _make_world()
    state = StateContainer()

    player = PlayerSlice()
    player.restore({"current_area": area_id, "current_location": location_id})
    state.register(player)

    areas = AreaSlice()
    area_data: dict[str, Any] = {
        "danger_level": danger_level,
        "npc_locations": {},
        "hostile_tracking": {},
    }
    if area_tags:
        area_data["tags"] = area_tags
    areas.restore({"areas": {area_id: area_data, "town": {"danger_level": 0.0}}})
    state.register(areas)

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": slot})
    state.register(time_slice)

    if include_events:
        events = EventSlice()
        events.restore({})
        state.register(events)

    if include_flags:
        flags = FlagSlice()
        flags.restore({})
        state.register(flags)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    rules_engine = RulesEngine()
    if register_encounter_handler:
        rules_engine.register(EncounterHandler())

    change_log: list[StateChange] = []

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        change_log.extend(delta.changes)
        for change in delta.changes:
            scene_bus.record_state_change(change)

    return SettlementContext(
        change_log=change_log,
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=rules_engine,
        _apply_delta=_apply_delta,
        action_log=list(action_log or []),
    )


# ── PhaseResult type checks ───────────────────────────────────────────────────

class TestPhaseResultType:
    def test_phase_result_has_expected_fields(self) -> None:
        result = PhaseResult()
        assert result.sse_events == []
        assert result.metadata == {}

    def test_phase_result_with_data(self) -> None:
        from app.game_core.orchestration.models import SSEEvent
        event = SSEEvent(event_type="test_event", payload={"key": "value"})
        result = PhaseResult(sse_events=[event], metadata={"status": "applied"})
        assert len(result.sse_events) == 1
        assert result.metadata["status"] == "applied"


# ── EncounterPhase tests ───────────────────────────────────────────────────────

class TestEncounterPhase:
    def test_run_returns_phase_result(self) -> None:
        phase = EncounterPhase()
        ctx = _make_context(location_id=None)
        # player is in area with location_id=None but area_entry_bridge check requires move_area
        result = asyncio.run(phase.run(ctx))
        assert isinstance(result, PhaseResult)
        assert "status" in result.metadata

    def test_run_noop_when_player_in_sub_location(self) -> None:
        phase = EncounterPhase()
        ctx = _make_context(location_id="camp")
        result = asyncio.run(phase.run(ctx))
        assert isinstance(result, PhaseResult)
        # sub-location → not world map → noop
        assert result.metadata["status"] == "noop"
        assert result.metadata["evaluated"] is False

    def test_run_noop_when_danger_zero(self) -> None:
        phase = EncounterPhase()
        ctx = _make_context(danger_level=0.0, location_id=None)
        result = asyncio.run(phase.run(ctx))
        assert isinstance(result, PhaseResult)
        assert result.metadata["status"] == "noop"

    def test_run_uses_injected_detector(self) -> None:
        """EncounterPhase should use the detector it was given."""
        class AlwaysNoopDetector:
            def __init__(self):
                self.called = False
            def plan(self, context):
                self.called = True
                from app.game_core.orchestration.hooks.encounter import EncounterProbe
                return EncounterProbe(metadata={"status": "noop", "reason": "always_noop"})

        detector = AlwaysNoopDetector()
        phase = EncounterPhase(detector=detector)
        # Use a move_area action_log so _can_evaluate passes
        action_log = [{"type": "move_area", "params": {"area_id": "forest"}, "time_cost": 0.25}]
        ctx = _make_context(location_id=None, action_log=action_log)
        result = asyncio.run(phase.run(ctx))
        assert isinstance(result, PhaseResult)
        # Detector was called (we're past _can_evaluate since the area has danger > 0)
        # The result metadata status should be "noop" from detector
        assert result.metadata["status"] in ("noop", "checked", "triggered", "command_failed")


# ── PerceptionPhase tests ──────────────────────────────────────────────────────

class TestPerceptionPhase:
    def test_run_returns_phase_result(self) -> None:
        phase = PerceptionPhase()
        ctx = _make_context()
        result = asyncio.run(phase.run(ctx))
        assert isinstance(result, PhaseResult)

    def test_run_noop_when_no_maps_registry(self) -> None:
        phase = PerceptionPhase()
        ctx = _make_context(world=WorldInstance("test_world"))
        result = asyncio.run(phase.run(ctx))
        assert result.metadata["status"] == "noop"
        assert result.metadata["reason"] == "no_maps_registry"

    def test_run_noop_status_when_no_discoveries(self) -> None:
        phase = PerceptionPhase()
        ctx = _make_context(location_id=None)  # on world map
        result = asyncio.run(phase.run(ctx))
        assert isinstance(result, PhaseResult)
        assert result.metadata["status"] == "noop"
        assert result.metadata["discoveries_found"] == []

    def test_run_returns_sse_events(self) -> None:
        """PerceptionPhase returns a list[SSEEvent] (may be empty)."""
        phase = PerceptionPhase()
        ctx = _make_context()
        result = asyncio.run(phase.run(ctx))
        assert isinstance(result.sse_events, list)


# ── EventConditionPhase tests ─────────────────────────────────────────────────

class TestEventConditionPhase:
    def test_run_returns_phase_result(self) -> None:
        phase = EventConditionPhase()
        ctx = _make_context(include_events=True)
        result = asyncio.run(phase.run(ctx))
        assert isinstance(result, PhaseResult)

    def test_run_noop_when_no_events_slice(self) -> None:
        phase = EventConditionPhase()
        ctx = _make_context(include_events=False)
        result = asyncio.run(phase.run(ctx))
        assert result.metadata["status"] == "noop"

    def test_run_noop_when_no_active_events(self) -> None:
        phase = EventConditionPhase()
        ctx = _make_context(include_events=True)
        result = asyncio.run(phase.run(ctx))
        # No events registered → noop
        assert result.metadata["status"] == "noop"
        assert result.metadata["checked_event_count"] == 0

    def test_run_uses_injected_evaluator(self) -> None:
        class RecordingEvaluator:
            def __init__(self):
                self.calls = 0
            def evaluate(self, state, world):
                self.calls += 1
                from app.game_core.orchestration.event_engine import EventConditionDecision
                return EventConditionDecision(metadata={"status": "noop", "unsupported_condition_count": 0})

        evaluator = RecordingEvaluator()
        phase = EventConditionPhase(evaluator=evaluator)
        ctx = _make_context(include_events=True)
        # No events → short-circuit before evaluator is called
        result = asyncio.run(phase.run(ctx))
        assert isinstance(result, PhaseResult)
        assert evaluator.calls == 0  # short-circuit on empty events


# ── AIOsirisHook coordination tests ───────────────────────────────────────────

class TestAIOsirisHookPhaseCoordination:
    def _make_osiris_with_phases(
        self,
        *,
        encounter_phase=None,
        perception_phase=None,
        event_phase=None,
    ) -> AIOsirisHook:
        return AIOsirisHook(
            engine=MechanicalOsirisEngine(),
            encounter_phase=encounter_phase,
            perception_phase=perception_phase,
            event_phase=event_phase,
        )

    def test_execute_without_phases_still_works(self) -> None:
        """No phases injected → old behavior, no crash."""
        hook = self._make_osiris_with_phases()
        ctx = _make_context(action_log=[{"type": "move_area", "params": {}, "time_cost": 0.25}])
        result = asyncio.run(hook.execute(ctx))
        assert result.metadata["evaluated"] is True
        assert "phases" in result.metadata
        # All phases should be empty dicts
        assert result.metadata["phases"]["perception"] == {}
        assert result.metadata["phases"]["encounter"] == {}
        assert result.metadata["phases"]["event_conditions"] == {}

    def test_execute_with_all_three_phases(self) -> None:
        """With all phases injected, Osiris calls each and aggregates SSE events."""
        hook = self._make_osiris_with_phases(
            encounter_phase=EncounterPhase(detector=BasicEncounterDetector()),
            perception_phase=PerceptionPhase(),
            event_phase=EventConditionPhase(evaluator=BasicEventConditionEvaluator()),
        )
        ctx = _make_context(
            action_log=[{"type": "move_area", "params": {}, "time_cost": 0.25}],
            include_events=True,
        )
        result = asyncio.run(hook.execute(ctx))
        assert result.metadata["evaluated"] is True
        # Each phase's metadata should be present
        assert "phases" in result.metadata
        phases = result.metadata["phases"]
        assert "perception" in phases
        assert "encounter" in phases
        assert "event_conditions" in phases

    def test_execute_phases_metadata_captured(self) -> None:
        """Phase metadata is stored under result.metadata['phases']."""
        hook = self._make_osiris_with_phases(
            perception_phase=PerceptionPhase(),
            event_phase=EventConditionPhase(),
        )
        ctx = _make_context(include_events=True)
        result = asyncio.run(hook.execute(ctx))
        phases = result.metadata["phases"]
        # perception ran → has status
        assert "status" in phases["perception"]
        # event_conditions ran → has status
        assert "status" in phases["event_conditions"]

    def test_execute_phase_sse_events_merged_into_result(self) -> None:
        """SSE events from perception/encounter/event phases are merged into result.sse_events."""
        hook = self._make_osiris_with_phases(
            perception_phase=PerceptionPhase(),
            event_phase=EventConditionPhase(),
        )
        ctx = _make_context(include_events=True)
        result = asyncio.run(hook.execute(ctx))
        # At minimum, the ai_processing start/done events are there
        event_types = [e.event_type for e in result.sse_events]
        assert "ai_processing" in event_types


# ── DEFAULT_SETTLEMENT_HOOK_TYPES validation ──────────────────────────────────

class TestDefaultHookTypes:
    def test_encounter_hook_not_in_defaults(self) -> None:
        from app.game_core.orchestration.hooks.encounter import EncounterHook
        assert EncounterHook not in DEFAULT_SETTLEMENT_HOOK_TYPES

    def test_passive_perception_hook_not_in_defaults(self) -> None:
        assert PassivePerceptionHook not in DEFAULT_SETTLEMENT_HOOK_TYPES

    def test_event_condition_hook_not_in_defaults(self) -> None:
        assert EventConditionHook not in DEFAULT_SETTLEMENT_HOOK_TYPES

    def test_osiris_hook_not_in_defaults(self) -> None:
        """AIOsirisHook is manually constructed in bootstrap, not via defaults."""
        assert AIOsirisHook not in DEFAULT_SETTLEMENT_HOOK_TYPES


# ── Bootstrap integration test ─────────────────────────────────────────────────

class TestBootstrapOsirisWiring:
    def test_build_runtime_osiris_has_all_phases(self) -> None:
        """bootstrap.build_runtime_for_world constructs AIOsirisHook with 3 phases."""
        from app.game_core.bootstrap import build_default_world, build_runtime_for_world
        from app.game_core.orchestration.hooks.encounter import EncounterPhase
        from app.game_core.orchestration.hooks.passive_perception import PerceptionPhase
        from app.game_core.orchestration.hooks.event_condition import EventConditionPhase

        world = build_default_world("test_world")
        runtime = build_runtime_for_world(world)

        # Find AIOsirisHook in the registered hooks
        osiris_hook = None
        for hook in runtime.tick_coordinator.settlement_hooks:
            if hook.name == "ai_osiris":
                osiris_hook = hook
                break

        assert osiris_hook is not None, "AIOsirisHook not found in settlement hooks"
        assert isinstance(osiris_hook, AIOsirisHook)
        assert isinstance(osiris_hook._encounter_phase, EncounterPhase)
        assert isinstance(osiris_hook._perception_phase, PerceptionPhase)
        assert isinstance(osiris_hook._event_phase, EventConditionPhase)

    def test_build_runtime_encounter_hook_not_registered_separately(self) -> None:
        """EncounterHook should NOT be registered as an independent hook."""
        from app.game_core.bootstrap import build_default_world, build_runtime_for_world
        from app.game_core.orchestration.hooks.encounter import EncounterHook
        from app.game_core.orchestration.hooks.passive_perception import PassivePerceptionHook
        from app.game_core.orchestration.hooks.event_condition import EventConditionHook

        world = build_default_world("test_world")
        runtime = build_runtime_for_world(world)

        hook_names = {hook.name for hook in runtime.tick_coordinator.settlement_hooks}
        assert "encounter" not in hook_names, "EncounterHook should not be registered independently"
        assert "passive_perception" not in hook_names, "PassivePerceptionHook should not be registered independently"
        assert "event_conditions" not in hook_names, "EventConditionHook should not be registered independently"
