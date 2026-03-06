"""Tests for Phase 1 C3 companion event dispatch."""

from __future__ import annotations

import asyncio

from app.game_core.content import WorldInstance
from app.game_core.narrative.companion_runtime import (
    CompanionRuntimeManager,
    MAX_EVENT_LOG,
    TickRecord,
)
from app.game_core.orchestration.models import PipelineResult, SSEEvent
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.tick_coordinator import TickCoordinator
from app.game_core.rules import RulesEngine
from app.game_core.state import StateContainer
from app.game_core.state.slices import PartySlice, SceneSlice, TimeSlice


class _StaticPipeline:
    """Tiny pipeline stub that always returns a fixed PipelineResult."""

    def __init__(self, result: PipelineResult) -> None:
        self._result = result

    async def process(self, *args, **kwargs) -> PipelineResult:
        return self._result


def _make_state(*, with_party: bool = True, with_time: bool = True) -> StateContainer:
    state = StateContainer()
    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    if with_time:
        time_slice = TimeSlice()
        time_slice.restore({})
        state.register(time_slice)
    if with_party:
        party_slice = PartySlice()
        party_slice.restore(
            {
                "members": {
                    "ally_anna": {},
                    "ally_ben": {},
                },
                "companion_approval": {},
                "shared_experiences": [],
            }
        )
        state.register(party_slice)
    return state


def _make_coordinator(
    state: StateContainer,
    companion_manager: CompanionRuntimeManager | None = None,
) -> tuple[TickCoordinator, SceneBus]:
    scene_bus = SceneBus(state.scene)
    coordinator = TickCoordinator(
        world=WorldInstance("test_world"),
        state=state,
        rules_engine=RulesEngine(),
        scene_bus=scene_bus,
        companion_manager=companion_manager,
    )
    return coordinator, scene_bus


def _make_result(
    *,
    success: bool = True,
    action_type: str = "skill_check",
    narrative_hints: list[str] | None = None,
    sse_events: list[SSEEvent] | None = None,
    time_cost: float = 0.0,
) -> PipelineResult:
    return PipelineResult(
        success=success,
        action_type=action_type,
        narrative_hints=narrative_hints or [],
        sse_events=sse_events or [],
        time_cost=time_cost,
    )


def _add_entry(
    scene_bus: SceneBus,
    source: str,
    tags: list[str] | None = None,
) -> None:
    scene_bus.add_entry(
        {
            "source": source,
            "content": f"{source} entry",
            "visibility": "public",
            "tags": tags or [],
        }
    )


def test_tick_record_creation() -> None:
    record = TickRecord(
        tick=12,
        action_type="navigate",
        success=True,
        summary="Player moves to courtyard.",
    )
    assert record.tick == 12
    assert record.tags == []
    assert record.involved_npcs == []
    assert record.event_transitions == []
    assert record.has_rolls is False


def test_companion_instance_receive_tick() -> None:
    manager = CompanionRuntimeManager()
    instance = manager.get_or_create("ally_anna")
    record = TickRecord(
        tick=5,
        action_type="rest_short",
        success=True,
        summary="Player rests briefly.",
    )
    instance.receive_tick(record)
    assert instance.event_log == [record]


def test_event_log_sliding_window() -> None:
    manager = CompanionRuntimeManager()
    instance = manager.get_or_create("ally_anna")
    for tick in range(MAX_EVENT_LOG + 5):
        instance.receive_tick(
            TickRecord(
                tick=tick,
                action_type="move_area",
                success=True,
                summary=f"tick_{tick}",
            )
        )
    assert len(instance.event_log) == MAX_EVENT_LOG
    assert instance.event_log[0].tick == 5
    assert instance.event_log[-1].tick == MAX_EVENT_LOG + 4


def test_get_recent_events() -> None:
    manager = CompanionRuntimeManager()
    instance = manager.get_or_create("ally_anna")
    for tick in range(12):
        instance.receive_tick(
            TickRecord(
                tick=tick,
                action_type="navigate",
                success=True,
                summary=f"tick_{tick}",
            )
        )
    recent_three = instance.get_recent_events(3)
    assert [record.tick for record in recent_three] == [9, 10, 11]
    recent_default = instance.get_recent_events()
    assert [record.tick for record in recent_default] == list(range(2, 12))


def test_get_events_by_tag() -> None:
    manager = CompanionRuntimeManager()
    instance = manager.get_or_create("ally_anna")
    instance.receive_tick(
        TickRecord(
            tick=1,
            action_type="attack",
            success=True,
            summary="combat",
            tags=["COMBAT"],
        )
    )
    instance.receive_tick(
        TickRecord(
            tick=2,
            action_type="rest_long",
            success=True,
            summary="rest",
            tags=["REST", "REST"],
        )
    )
    instance.receive_tick(
        TickRecord(
            tick=3,
            action_type="move_area",
            success=True,
            summary="travel",
            tags=["NAVIGATION"],
        )
    )
    assert [record.tick for record in instance.get_events_by_tag("COMBAT")] == [1]
    assert [record.tick for record in instance.get_events_by_tag("REST")] == [2]
    assert [record.tick for record in instance.get_events_by_tag("NAVIGATION")] == [3]
    assert instance.get_events_by_tag("UNKNOWN") == []


def test_manager_dispatch_tick() -> None:
    manager = CompanionRuntimeManager()
    anna = manager.get_or_create("ally_anna")
    ben = manager.get_or_create("ally_ben")
    record = TickRecord(
        tick=7,
        action_type="skill_check",
        success=True,
        summary="Player checks lock.",
    )
    manager.dispatch_tick(record)
    assert anna.event_log == [record]
    assert ben.event_log == [record]
    assert len(anna.get_recent_events()) == 1


def _latest_dispatch_record(manager: CompanionRuntimeManager) -> TickRecord:
    anna = manager.get("ally_anna")
    if anna is None or not anna.event_log:
        raise AssertionError("Expected ally_anna to receive a dispatch event")
    return anna.get_recent_events(1)[0]


def test_dispatch_skipped_without_companion_manager() -> None:
    state = _make_state(with_party=True, with_time=True)
    coordinator, scene_bus = _make_coordinator(state, companion_manager=None)
    _add_entry(scene_bus, "ENGINE", tags=["QUEST_PROGRESS"])
    result = _make_result(action_type="advance_quest", narrative_hints=["Quest advances."])
    coordinator._dispatch_companion_events(result)


def test_dispatch_skipped_without_party() -> None:
    state = _make_state(with_party=False, with_time=True)
    manager = CompanionRuntimeManager()
    coordinator, scene_bus = _make_coordinator(state, manager)
    _add_entry(scene_bus, "ENGINE", tags=["REST"])
    result = _make_result(action_type="rest_short", narrative_hints=["Party not found."])
    coordinator._dispatch_companion_events(result)
    assert manager.get("ally_anna") is None


def test_dispatch_skipped_on_failure() -> None:
    state = _make_state()
    manager = CompanionRuntimeManager()
    coordinator, scene_bus = _make_coordinator(state, manager)
    _add_entry(scene_bus, "ENGINE", tags=["COMBAT"])
    result = _make_result(success=False, action_type="attack")
    coordinator._dispatch_companion_events(result)
    assert manager.get("ally_anna") is None


def test_dispatch_collects_engine_tags() -> None:
    state = _make_state()
    manager = CompanionRuntimeManager()
    coordinator, scene_bus = _make_coordinator(state, manager)
    _add_entry(scene_bus, "ENGINE", tags=["QUEST_PROGRESS", "COMBAT"])
    _add_entry(scene_bus, "NPC:ally_anna")
    result = _make_result(action_type="skill_check", narrative_hints=["A hidden script triggers."])
    coordinator._dispatch_companion_events(result)
    latest = _latest_dispatch_record(manager)
    assert set(latest.tags) == {"QUEST_PROGRESS", "COMBAT"}


def test_dispatch_skipped_noop_action() -> None:
    state = _make_state()
    manager = CompanionRuntimeManager()
    coordinator, scene_bus = _make_coordinator(state, manager)
    _add_entry(scene_bus, "ENGINE", tags=["REST"])
    result = _make_result(action_type="noop", narrative_hints=["Nothing happened."])
    coordinator._dispatch_companion_events(result)
    assert manager.get("ally_anna") is None

def test_dispatch_collects_npc_involvement() -> None:
    state = _make_state()
    manager = CompanionRuntimeManager()
    coordinator, scene_bus = _make_coordinator(state, manager)
    _add_entry(scene_bus, "NPC:ally_anna")
    _add_entry(scene_bus, "npc:ally_ben")
    _add_entry(scene_bus, "NPC:ally_anna")
    result = _make_result(action_type="attack", narrative_hints=["Ally blocks the path."])
    coordinator._dispatch_companion_events(result)
    latest = _latest_dispatch_record(manager)
    assert latest.involved_npcs == ["ally_anna", "ally_ben"]


def test_dispatch_collects_event_transitions() -> None:
    state = _make_state()
    manager = CompanionRuntimeManager()
    coordinator, scene_bus = _make_coordinator(state, manager)
    result = _make_result(
        action_type="advance_quest",
        narrative_hints=["A quest is cleared."],
        sse_events=[
            SSEEvent(event_type="event_state_changed", payload={"event_id": "evt_1"}),
            SSEEvent(event_type="event_state_changed", payload={"event_id": ""}),
            SSEEvent(event_type="other", payload={"event_id": "evt_2"}),
        ],
    )
    coordinator._dispatch_companion_events(result)
    latest = _latest_dispatch_record(manager)
    assert latest.event_transitions == ["evt_1"]


def test_dispatch_uses_narrative_hint_as_summary() -> None:
    state = _make_state()
    manager = CompanionRuntimeManager()
    coordinator, scene_bus = _make_coordinator(state, manager)
    result = _make_result(
        action_type="navigate",
        narrative_hints=["Player entered the library."],
    )
    coordinator._dispatch_companion_events(result)
    latest = _latest_dispatch_record(manager)
    assert latest.summary == "Player entered the library."


def test_dispatch_fallback_summary_to_action_type() -> None:
    state = _make_state()
    manager = CompanionRuntimeManager()
    coordinator, scene_bus = _make_coordinator(state, manager)
    result = _make_result(action_type="navigate", narrative_hints=[])
    coordinator._dispatch_companion_events(result)
    latest = _latest_dispatch_record(manager)
    assert latest.summary == "navigate"


def test_dispatch_companion_events_in_process() -> None:
    state = _make_state()
    manager = CompanionRuntimeManager()
    coordinator, scene_bus = _make_coordinator(state, manager)
    _add_entry(scene_bus, "NPC:ally_anna")
    coordinator.pipeline = _StaticPipeline(
        _make_result(
            action_type="skill_check",
            narrative_hints=["Player inspects the rune."],
            sse_events=[
                SSEEvent(
                    event_type="event_state_changed",
                    payload={"event_id": "evt_process"},
                ),
            ],
            time_cost=0.25,
        )
    )
    asyncio.run(coordinator.process({"action_type": "noop"}))
    latest = _latest_dispatch_record(manager)
    assert latest.action_type == "skill_check"
    assert latest.summary == "Player inspects the rune."
    assert latest.event_transitions == ["evt_process"]
    assert latest.tick == state.time.absolute_tick()
    assert "SKILL_CHECK" in latest.tags


def test_snapshot_includes_event_log_size() -> None:
    manager = CompanionRuntimeManager()
    anna = manager.get_or_create("ally_anna")
    anna.receive_tick(
        TickRecord(
            tick=1,
            action_type="move_area",
            success=True,
            summary="move",
        )
    )
    anna.receive_tick(
        TickRecord(
            tick=2,
            action_type="rest_short",
            success=True,
            summary="rest",
        )
    )
    snapshot = manager.snapshot()
    assert len(snapshot) == 1
    assert snapshot[0]["event_log_size"] == 2
