"""Tests for P28 Wave 0 Track B — QF-1/3/4 independent fixes."""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.orchestration.hooks.ai_osiris import (
    AIOsirisDecision,
    AIOsirisHook,
)
from app.game_core.orchestration.hooks.narrative_planner import (
    NarrativePlannerDecision,
    NarrativePlannerHook,
)
from app.game_core.orchestration.models import SSEEvent
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.rules.defaults import register_default_rules_handlers
from app.game_core.rules.handlers import WorldStateHandler
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    AreaSlice,
    EventSlice,
    FlagSlice,
    NarrativePlanSlice,
    PartySlice,
    PlayerSlice,
    QuestSlice,
    SceneSlice,
    TimeSlice,
)


# ── Test helpers ──────────────────────────────────────────────────────────────


class RecordingEvaluator:
    """AIOsiris evaluator that records calls and returns a fixed decision."""

    def __init__(self, decision: AIOsirisDecision | None = None) -> None:
        self.decision = decision or AIOsirisDecision(metadata={"status": "noop"})
        self.calls: list[dict[str, Any]] = []

    async def evaluate(self, summary, snapshot, rules_context):
        self.calls.append({"summary": summary, "snapshot": snapshot})
        return self.decision


class ExplodingEvaluator:
    async def evaluate(self, summary, snapshot, rules_context):
        raise RuntimeError("osiris unavailable")


class RecordingPlanner:
    """Narrative planner that records calls and returns a fixed decision."""

    def __init__(self, decision: NarrativePlannerDecision | None = None) -> None:
        self.decision = decision or NarrativePlannerDecision(
            directives=[],
            metadata={"status": "noop", "reason": "stable"},
        )
        self.calls: list[dict[str, Any]] = []

    async def plan(self, context):
        self.calls.append(dict(context))
        return self.decision


class ExplodingPlanner:
    async def plan(self, context):
        raise RuntimeError("planner unavailable")


def _make_osiris_context(
    *,
    change_log: list[StateChange] | None = None,
    action_log: list[dict[str, Any]] | None = None,
) -> SettlementContext:
    world = WorldInstance("test_world")
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 2, "slot": 9})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": "forest", "current_location": "camp"})
    state.register(player)

    flags = FlagSlice()
    flags.restore({"flags": {}})
    state.register(flags)

    areas = AreaSlice()
    areas.restore({"areas": {"forest": {}}})
    state.register(areas)

    party = PartySlice()
    party.restore({"members": {}})
    state.register(party)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({"current_chapter": "chapter_1"})
    state.register(narrative_plan)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    rules_engine = RulesEngine()
    rules_engine.register(WorldStateHandler())

    active_change_log = list(change_log or [
        StateChange(slice="flags", operation="set", path="flags.quest_started", value=True)
    ])

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        active_change_log.extend(delta.changes)
        for change in delta.changes:
            scene_bus.record_state_change(change)

    return SettlementContext(
        change_log=active_change_log,
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=rules_engine,
        _apply_delta=_apply_delta,
        action_log=list(action_log) if action_log is not None else [],
    )


def _make_planner_context(
    *,
    change_log: list[StateChange] | None = None,
) -> SettlementContext:
    from app.game_core.content.registries.maps import MapRegistry

    world = WorldInstance("test_world")
    maps = MapRegistry()
    maps.load({"forest": {"id": "forest", "sub_locations": {}}})
    world.register(maps)

    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": "forest", "current_location": None})
    state.register(player)

    quests = QuestSlice()
    quests.restore({
        "milestone_states": {"ms_1": {"state": "AVAILABLE"}},
        "dynamic_quests": {},
        "chapter_completion": {"chapter_1": 0.0},
    })
    state.register(quests)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({"current_chapter": "chapter_1", "ticks_since_milestone_progress": 2})
    state.register(narrative_plan)

    areas = AreaSlice()
    areas.restore({"areas": {"forest": {}}})
    state.register(areas)

    events = EventSlice()
    events.restore({})
    state.register(events)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)

    active_change_log: list[StateChange] = list(change_log or [
        StateChange(slice="quests", operation="set", path="milestone_states.ms_1", value={"state": "AVAILABLE"}),
        StateChange(slice="player", operation="set", path="current_area", value="forest"),
    ])

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        active_change_log.extend(delta.changes)
        for change in delta.changes:
            scene_bus.record_state_change(change)

    return SettlementContext(
        change_log=active_change_log,
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=rules_engine,
        _apply_delta=_apply_delta,
        action_log=[],
    )


# ── QF-4: AIOsirisHook ai_processing SSE ─────────────────────────────────────


class TestAiOsirisProcessingSSE:
    def test_ai_processing_start_and_done_emitted_on_success(self) -> None:
        """AIOsirisHook.execute() emits ai_processing start+done SSE on success."""
        async def _run():
            evaluator = RecordingEvaluator()
            hook = AIOsirisHook(evaluator=evaluator)
            context = _make_osiris_context()
            result = await hook.execute(context)
            sse_types = [e.event_type for e in result.sse_events]
            assert "ai_processing" in sse_types, f"Expected ai_processing in {sse_types}"
            processing_events = [
                e for e in result.sse_events if e.event_type == "ai_processing"
            ]
            assert len(processing_events) == 2, (
                f"Expected start+done pair, got {len(processing_events)}: {processing_events}"
            )
            assert processing_events[0].payload == {"system": "osiris", "status": "start"}
            assert processing_events[1].payload == {"system": "osiris", "status": "done"}

        asyncio.run(_run())

    def test_ai_processing_done_emitted_when_evaluator_arg_ignored(self) -> None:
        """AIOsirisHook.execute() emits ai_processing start+done even when legacy evaluator arg is passed.

        With MechanicalOsirisEngine, the evaluator arg is fully ignored.
        The old ai_osiris_error SSE is no longer emitted (no LLM path).
        """
        async def _run():
            # ExplodingEvaluator is never called because MechanicalOsirisEngine is used.
            hook = AIOsirisHook(evaluator=ExplodingEvaluator())
            context = _make_osiris_context()
            result = await hook.execute(context)
            sse_types = [e.event_type for e in result.sse_events]
            assert "ai_processing" in sse_types, f"Expected ai_processing in {sse_types}"
            # No ai_osiris_error — that was the old LLM failure path; mechanical engine always succeeds
            assert "ai_osiris_error" not in sse_types, (
                f"ai_osiris_error should not appear in mechanical path: {sse_types}"
            )
            processing_events = [
                e for e in result.sse_events if e.event_type == "ai_processing"
            ]
            statuses = [e.payload.get("status") for e in processing_events]
            assert "start" in statuses
            assert "done" in statuses

        asyncio.run(_run())

    def test_ai_processing_precedes_osiris_applied(self) -> None:
        """ai_processing start/done appear before ai_osiris_applied in the event stream."""
        async def _run():
            from app.game_core.rules.models import Command
            evaluator = RecordingEvaluator(
                AIOsirisDecision(
                    consequences=[
                        {"type": "set_flag", "params": {"key": "osiris_test", "value": True}}
                    ],
                    reasoning="test",
                    visible_change=False,
                    metadata={"status": "deterministic"},
                )
            )
            hook = AIOsirisHook(evaluator=evaluator)
            context = _make_osiris_context()
            result = await hook.execute(context)
            sse_types = [e.event_type for e in result.sse_events]
            assert "ai_processing" in sse_types
            # ai_processing start should be before ai_osiris_applied (if present)
            if "ai_osiris_applied" in sse_types:
                start_idx = next(
                    i for i, e in enumerate(result.sse_events)
                    if e.event_type == "ai_processing" and e.payload.get("status") == "start"
                )
                applied_idx = sse_types.index("ai_osiris_applied")
                assert start_idx < applied_idx, (
                    f"ai_processing start ({start_idx}) should precede ai_osiris_applied ({applied_idx})"
                )

        asyncio.run(_run())


# ── QF-4: NarrativePlannerHook ai_processing SSE ─────────────────────────────


class TestNarrativePlannerProcessingSSE:
    def test_ai_processing_emitted_when_blackboard_present(self) -> None:
        """NarrativePlannerHook emits ai_processing start+done when blackboard runs."""
        async def _run():
            planner = RecordingPlanner()
            hook = NarrativePlannerHook(planner=planner)
            context = _make_planner_context()
            result = await hook.execute(context)
            processing_events = [
                e for e in result.sse_events if e.event_type == "ai_processing"
            ]
            assert len(processing_events) == 2, (
                f"Expected start+done, got {len(processing_events)}: {processing_events}"
            )
            assert processing_events[0].payload == {"system": "planner", "status": "start"}
            assert processing_events[1].payload == {"system": "planner", "status": "done"}

        asyncio.run(_run())

    def test_ai_processing_done_emitted_on_planner_error(self) -> None:
        """NarrativePlannerHook emits ai_processing done even when planner raises."""
        async def _run():
            hook = NarrativePlannerHook(planner=ExplodingPlanner())
            context = _make_planner_context()
            result = await hook.execute(context)
            sse_types = [e.event_type for e in result.sse_events]
            assert "ai_processing" in sse_types, (
                f"Expected ai_processing in planner error path, got {sse_types}"
            )
            processing_events = [
                e for e in result.sse_events if e.event_type == "ai_processing"
            ]
            statuses = [e.payload.get("status") for e in processing_events]
            assert "done" in statuses, f"Expected 'done' status in {statuses}"

        asyncio.run(_run())

    def test_no_ai_processing_without_blackboard(self) -> None:
        """NarrativePlannerHook does not emit ai_processing when no planner is set."""
        async def _run():
            hook = NarrativePlannerHook(planner=None)
            context = _make_planner_context()
            result = await hook.execute(context)
            processing_events = [
                e for e in result.sse_events if e.event_type == "ai_processing"
            ]
            assert len(processing_events) == 0, (
                f"Expected no ai_processing when no planner, got {processing_events}"
            )

        asyncio.run(_run())


# Private chat tests removed — system unified into NpcInteractionCoordinator.
