"""Tests for behavior-window recording in TickCoordinator."""

from __future__ import annotations

import asyncio

from app.game_core.content import WorldInstance
from app.game_core.orchestration.defaults import build_default_action_dispatcher
from app.game_core.orchestration.pipeline import PipelineOrchestrator
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.tick_coordinator import TickCoordinator
from app.game_core.rules import RulesEngine
from app.game_core.rules.handlers import WorldStateHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import FlagSlice, NarrativePlanSlice, PlayerSlice, SceneSlice, TimeSlice


def _make_coordinator() -> TickCoordinator:
    state = StateContainer()
    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 1})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": "forest", "current_location": "gate"})
    state.register(player)

    flags = FlagSlice()
    flags.restore({})
    state.register(flags)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({})
    state.register(narrative_plan)

    scene = SceneSlice()
    scene.restore({})
    state.register(scene)

    scene_bus = SceneBus(scene)
    dispatcher = build_default_action_dispatcher()
    pipeline = PipelineOrchestrator(action_dispatcher=dispatcher)
    engine = RulesEngine()
    engine.register(WorldStateHandler())

    return TickCoordinator(
        world=WorldInstance("test_world"),
        state=state,
        rules_engine=engine,
        scene_bus=scene_bus,
        pipeline=pipeline,
    )


def test_process_records_action_type_into_behavior_window() -> None:
    coordinator = _make_coordinator()
    asyncio.run(
        coordinator.process(
            {"action_type": "set_flag", "params": {"key": "phase6", "value": True}},
        )
    )

    behavior = coordinator.state.narrative_plan.behavior_window
    assert len(behavior) == 1
    assert behavior[-1]["action_type"] == "set_flag"
    assert behavior[-1]["tick"] == coordinator.state.time.absolute_tick()
