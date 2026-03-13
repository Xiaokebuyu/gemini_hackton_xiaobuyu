"""Tests for P30: current_target_milestone full chain.

Covers:
1. planner_create_quest sets current_target_milestone when null (Fix 1)
2. planner_create_quest does not override existing target (Fix 1)
3. _auto_select_target_milestone picks ACTIVE over AVAILABLE (Fix 2)
4. _auto_select_target_milestone uses sequence ordering within same state (Fix 2)
5. _auto_select_target_milestone advances after current target is COMPLETED (Fix 2)
6. _auto_select_target_milestone is a no-op when target is still ACTIVE (Fix 2)
7. execute() calls _auto_select and sets target when missing
8. bootstrap() also calls _auto_select after replay
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.content.registries.quests import QuestRegistry
from app.game_core.orchestration.hooks.narrative_planner import (
    NarrativePlannerDecision,
    NarrativePlannerHook,
)
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.rules.defaults import register_default_rules_handlers
from app.game_core.rules.handlers.planner import PlannerQuestHandler
from app.game_core.rules.models import Command
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    AreaSlice,
    EventSlice,
    NarrativePlanSlice,
    PlayerSlice,
    QuestSlice,
    SceneSlice,
    TimeSlice,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_quest_registry(milestones: dict[str, dict[str, Any]]) -> QuestRegistry:
    reg = QuestRegistry()
    reg.load({"milestones": milestones, "chapters": []})
    return reg


def _make_state(
    *,
    milestone_states: dict[str, Any] | None = None,
    current_target: str | None = None,
) -> StateContainer:
    state = StateContainer()

    quests = QuestSlice()
    quests.restore({
        "milestone_states": milestone_states or {},
        "dynamic_quests": {},
        "chapter_completion": {},
    })
    state.register(quests)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({
        "current_target_milestone": current_target,
    })
    state.register(narrative_plan)

    areas = AreaSlice()
    areas.restore({"areas": {"frontier_town": {}}})
    state.register(areas)

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9})
    state.register(time_slice)

    return state


def _make_context(
    *,
    milestone_states: dict[str, Any] | None = None,
    current_target: str | None = None,
    quest_registry: QuestRegistry | None = None,
    change_log: list[StateChange] | None = None,
) -> SettlementContext:
    world = WorldInstance("test_world")
    if quest_registry is not None:
        world.register(quest_registry)

    state = _make_state(milestone_states=milestone_states, current_target=current_target)

    player = PlayerSlice()
    player.restore({"current_area": "frontier_town"})
    state.register(player)

    events = EventSlice()
    events.restore({})
    state.register(events)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    active_change_log = list(change_log or [])

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        active_change_log.extend(delta.changes)
        for change in delta.changes:
            scene_bus.record_state_change(change)

    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)

    return SettlementContext(
        change_log=active_change_log,
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=rules_engine,
        _apply_delta=_apply_delta,
    )


class RecordingPlanner:
    def __init__(self, decision: NarrativePlannerDecision | None = None) -> None:
        self.decision = decision or NarrativePlannerDecision(directives=[])
        self.calls: list[dict[str, Any]] = []

    async def plan(self, context: dict[str, Any]) -> NarrativePlannerDecision:
        self.calls.append(dict(context))
        return self.decision


def _build_hook(planner: Any | None = None) -> NarrativePlannerHook:
    """Build a NarrativePlannerHook with all sub-systems wired up.

    Mirrors the _build_test_hook pattern from test_narrative_planner_hook.py
    but simplified: no agents, just a static blackboard (or no planner at all).
    """
    from app.game_core.planning.subsystem import PlannerDispatcher
    from app.game_core.planning.quest_manager import QuestManagerSubSystem
    from app.game_core.planning.npc_director import NpcDirectorSubSystem
    from app.game_core.planning.world_builder import WorldBuilderSubSystem
    from app.game_core.planning.pacing_controller import PacingControllerSubSystem
    from app.game_core.planning.narrative_weaver import NarrativeWeaverSubSystem
    from app.game_core.planning.item_designer import ItemDesignerSubSystem

    hook = NarrativePlannerHook(planner=planner)
    dispatcher = PlannerDispatcher()

    dispatcher.register(QuestManagerSubSystem(dispatcher=dispatcher))
    dispatcher.register(NpcDirectorSubSystem())
    dispatcher.register(WorldBuilderSubSystem(sse_collector=hook._pending_sse))
    dispatcher.register(PacingControllerSubSystem())
    dispatcher.register(NarrativeWeaverSubSystem(sse_collector=hook._pending_sse))
    dispatcher.register(ItemDesignerSubSystem())
    hook._dispatcher = dispatcher
    return hook


# ---------------------------------------------------------------------------
# Fix 1 Tests: planner_create_quest sets current_target_milestone
# ---------------------------------------------------------------------------

def test_create_quest_sets_target_milestone() -> None:
    """planner_create_quest AVAILABLE->ACTIVE should set current_target_milestone when null.

    The handler reads source_milestone from cmd.params["metadata"]["source_milestone"].
    """
    world = WorldInstance("test_world")
    state = _make_state(
        milestone_states={"ms_main": {"state": "AVAILABLE"}},
        current_target=None,
    )

    handler = PlannerQuestHandler()
    result = handler.compute(
        Command(
            type="planner_create_quest",
            params={
                "quest_id": "dq_1",
                "title": "Find the artifact",
                "summary": "Locate the lost artifact",
                "status": "active",
                "current_tick": 5,
                "metadata": {"source_milestone": "ms_main", "urgency": "high"},
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True, f"execute failed: {result}"
    assert result.delta is not None
    state.apply(result.delta)

    # milestone should be ACTIVE now
    assert state.quests.get_milestone_state("ms_main") == "ACTIVE"
    # current_target_milestone should be set
    assert state.narrative_plan.current_target_milestone == "ms_main"


def test_create_quest_does_not_override_existing_target() -> None:
    """planner_create_quest should NOT change current_target_milestone when already set."""
    world = WorldInstance("test_world")
    state = _make_state(
        milestone_states={
            "ms_main": {"state": "AVAILABLE"},
            "ms_other": {"state": "ACTIVE"},
        },
        current_target="ms_other",  # already tracking ms_other
    )

    handler = PlannerQuestHandler()
    result = handler.compute(
        Command(
            type="planner_create_quest",
            params={
                "quest_id": "dq_2",
                "title": "Side quest",
                "summary": "A side quest",
                "status": "active",
                "current_tick": 5,
                "metadata": {"source_milestone": "ms_main", "urgency": "medium"},
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True, f"execute failed: {result}"
    assert result.delta is not None
    state.apply(result.delta)

    # ms_main should be ACTIVE
    assert state.quests.get_milestone_state("ms_main") == "ACTIVE"
    # current_target should NOT have changed — ms_other is still being tracked
    assert state.narrative_plan.current_target_milestone == "ms_other"


# ---------------------------------------------------------------------------
# Fix 2 Tests: _auto_select_target_milestone
# ---------------------------------------------------------------------------

def test_auto_select_picks_active_over_available() -> None:
    """ACTIVE milestone wins over AVAILABLE regardless of sequence."""
    context = _make_context(
        milestone_states={
            "ms_a": {"state": "AVAILABLE"},  # sequence 0
            "ms_b": {"state": "ACTIVE"},     # sequence 0 but ACTIVE
        },
        current_target=None,
    )
    hook = NarrativePlannerHook()
    hook._auto_select_target_milestone(context)

    assert context.state.narrative_plan.current_target_milestone == "ms_b"


def test_auto_select_uses_sequence_ordering() -> None:
    """Among AVAILABLE milestones, pick the one with the lower sequence number."""
    reg = _make_quest_registry({
        "ms_early": {"id": "ms_early", "sequence": 1},
        "ms_late": {"id": "ms_late", "sequence": 5},
    })
    context = _make_context(
        milestone_states={
            "ms_late": {"state": "AVAILABLE"},
            "ms_early": {"state": "AVAILABLE"},
        },
        current_target=None,
        quest_registry=reg,
    )
    hook = NarrativePlannerHook()
    hook._auto_select_target_milestone(context)

    assert context.state.narrative_plan.current_target_milestone == "ms_early"


def test_auto_select_advances_after_completion() -> None:
    """When the tracked milestone is COMPLETED, select the next AVAILABLE one."""
    reg = _make_quest_registry({
        "ms_done": {"id": "ms_done", "sequence": 1},
        "ms_next": {"id": "ms_next", "sequence": 2},
    })
    context = _make_context(
        milestone_states={
            "ms_done": {"state": "COMPLETED"},
            "ms_next": {"state": "AVAILABLE"},
        },
        current_target="ms_done",  # stale — was tracking completed milestone
        quest_registry=reg,
    )
    hook = NarrativePlannerHook()
    hook._auto_select_target_milestone(context)

    assert context.state.narrative_plan.current_target_milestone == "ms_next"


def test_auto_select_noop_when_target_active() -> None:
    """When current target is still ACTIVE, _auto_select does nothing."""
    context = _make_context(
        milestone_states={
            "ms_current": {"state": "ACTIVE"},
            "ms_other": {"state": "AVAILABLE"},
        },
        current_target="ms_current",
    )
    hook = NarrativePlannerHook()
    hook._auto_select_target_milestone(context)

    # must NOT have changed
    assert context.state.narrative_plan.current_target_milestone == "ms_current"


def test_auto_select_noop_when_no_candidates() -> None:
    """When no ACTIVE or AVAILABLE milestones exist, target stays None."""
    context = _make_context(
        milestone_states={
            "ms_locked": {"state": "LOCKED"},
            "ms_done": {"state": "COMPLETED"},
        },
        current_target=None,
    )
    hook = NarrativePlannerHook()
    hook._auto_select_target_milestone(context)

    assert context.state.narrative_plan.current_target_milestone is None


# ---------------------------------------------------------------------------
# Fix 2b: execute() calls _auto_select before _ensure_milestone_outline
# ---------------------------------------------------------------------------

def test_execute_calls_auto_select_and_sets_target() -> None:
    """execute() should set current_target_milestone via _auto_select when missing."""

    async def _run() -> None:
        planner = RecordingPlanner()
        hook = _build_hook(planner)

        # Context has an AVAILABLE milestone but no current_target
        context = _make_context(
            milestone_states={"ms_main": {"state": "AVAILABLE"}},
            current_target=None,
            change_log=[
                StateChange(
                    slice="quests",
                    operation="set",
                    path="milestone_states.ms_main",
                    value={"state": "AVAILABLE"},
                ),
            ],
        )
        # Advance tick past cooldown threshold
        context.state.time.restore({"day": 5, "slot": 9})

        await hook.execute(context)

        # _auto_select should have updated current_target_milestone
        assert context.state.narrative_plan.current_target_milestone == "ms_main", (
            f"Expected ms_main, got {context.state.narrative_plan.current_target_milestone}"
        )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Fix 2c: bootstrap() also calls _auto_select after replay
# ---------------------------------------------------------------------------

def test_bootstrap_sets_target_after_replay() -> None:
    """bootstrap() should call _auto_select so target is set even without blackboard."""

    async def _run() -> None:
        # Simulate a session where planner_create_quest was already applied via
        # replay, so the milestone is ACTIVE but current_target is still null.
        hook = NarrativePlannerHook(planner=None)

        context = _make_context(
            milestone_states={"ms_opening": {"state": "ACTIVE"}},
            current_target=None,
            change_log=[],
        )

        await hook.bootstrap(context)

        # After bootstrap, _auto_select should have set the target
        assert context.state.narrative_plan.current_target_milestone == "ms_opening", (
            f"Expected ms_opening, got {context.state.narrative_plan.current_target_milestone}"
        )

    asyncio.run(_run())
