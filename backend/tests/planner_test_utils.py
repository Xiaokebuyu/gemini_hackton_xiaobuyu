from __future__ import annotations

from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.content.registries.maps import MapRegistry
from app.game_core.narrative.instance_manager import InstanceManager
from app.game_core.orchestration.hooks.narrative_planner import (
    NarrativePlannerDecision,
    NarrativePlannerHook,
)
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.planning.narrative_weaver import NarrativeWeaverSubSystem
from app.game_core.planning.npc_director import NpcDirectorSubSystem
from app.game_core.planning.pacing_controller import PacingControllerSubSystem
from app.game_core.planning.quest_manager import QuestManagerSubSystem
from app.game_core.planning.subsystem import PlannerDispatcher
from app.game_core.planning.world_builder import WorldBuilderSubSystem
from app.game_core.rules import RulesEngine
from app.game_core.rules.defaults import register_default_rules_handlers
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


class RecordingPlanner:
    def __init__(self, decision: Any) -> None:
        self.decision = decision
        self.calls: list[dict[str, object]] = []

    async def plan(self, context: dict[str, Any]) -> Any:
        self.calls.append(dict(context))
        return self.decision


class StaticBlackboard:
    def __init__(self, decision: Any | None = None) -> None:
        self.decision = decision or NarrativePlannerDecision(
            directives=[],
            metadata={"status": "noop", "reason": "stable", "provider": "test_blackboard"},
        )
        self.calls: list[dict[str, object]] = []

    async def plan(self, context: dict[str, Any]) -> Any:
        self.calls.append(dict(context))
        return self.decision


class PlannerBlackboardAdapter:
    def __init__(self, planner: Any) -> None:
        self._planner = planner

    async def plan(self, context: dict[str, Any]) -> NarrativePlannerDecision:
        raw = await self._planner.plan(context)
        decision = NarrativePlannerHook._normalize_decision(raw)
        return NarrativePlannerDecision(
            directives=[],
            story_facts=list(decision.story_facts),
            strategy_notes=decision.strategy_notes,
            next_scheduled_tick=decision.next_scheduled_tick,
            metadata=dict(decision.metadata),
        )


class PlannerAgentAdapter:
    def __init__(self, planner: Any, *, allowed_directives: set[str] | None = None) -> None:
        self._planner = planner
        self._allowed_directives = set(allowed_directives or set())
        self._emitted = False

    async def evaluate(self, context: dict[str, Any]) -> dict[str, Any]:
        if self._emitted and self._allowed_directives:
            return {
                "directives": [],
                "story_facts": [],
                "strategy_notes": "",
                "metadata": {},
            }
        raw = await self._planner.plan(context)
        decision = NarrativePlannerHook._normalize_decision(raw)
        directives = list(decision.directives)
        if self._allowed_directives:
            filtered: list[Any] = []
            for directive in directives:
                normalized = NarrativePlannerHook._normalize_directive(directive)
                kind = normalized[0] if normalized is not None else ""
                if kind not in self._allowed_directives:
                    continue
                filtered.append(directive)
            directives = filtered
        if directives:
            self._emitted = True
        return {
            "directives": directives,
            "story_facts": [],
            "strategy_notes": "",
            "metadata": {},
        }


def make_context(
    *,
    change_log: list[StateChange] | None = None,
    narrative_plan_payload: dict[str, object] | None = None,
    quest_payload: dict[str, object] | None = None,
    area_payload: dict[str, object] | None = None,
    inject_semantic_seed: bool = True,
) -> SettlementContext:
    world = WorldInstance("test_world")
    maps = MapRegistry()
    maps.load(
        {
            "forest": {
                "id": "forest",
                "sub_locations": {
                    "quest_hub": {
                        "id": "quest_hub",
                        "name": "Quest Hub",
                        "interactables": [
                            {
                                "id": "board",
                                "name": "Quest Board",
                                "type": "inspect",
                                "tags": ["quest_source"],
                            }
                        ],
                    }
                },
            }
        }
    )
    world.register(maps)
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": "forest", "current_location": None})
    state.register(player)

    quests = QuestSlice()
    base_quest_payload: dict[str, object] = {
        "milestone_states": {"ms_1": {"state": "AVAILABLE"}},
        "dynamic_quests": {
            "dq_existing": {
                "status": "active",
                "title": "Existing",
                "summary": "In progress",
            }
        },
        "chapter_completion": {"chapter_1": 0.2},
    }
    if quest_payload:
        base_quest_payload.update(quest_payload)
    quests.restore(base_quest_payload)
    state.register(quests)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore(
        {
            "current_chapter": "chapter_1",
            "ticks_since_milestone_progress": 2,
            **(narrative_plan_payload or {}),
        }
    )
    state.register(narrative_plan)

    areas = AreaSlice()
    areas.restore(area_payload or {"areas": {"forest": {}}})
    state.register(areas)

    events = EventSlice()
    events.restore({})
    state.register(events)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    active_change_log = list(change_log or [])
    has_semantic_seed = any(
        (
            change.slice == "quests"
            and (
                change.path.startswith("milestone_states.")
                or change.path.startswith("dynamic_quests.")
            )
        )
        or (change.slice == "player" and change.path in {"current_area", "current_location"})
        or (change.slice == "events" and change.path.startswith("state."))
        or (change.slice == "relations" and change.path.startswith("shop_states."))
        for change in active_change_log
    )
    if inject_semantic_seed and not has_semantic_seed:
        active_change_log.extend(
            [
                StateChange(
                    slice="quests",
                    operation="set",
                    path="milestone_states.ms_1",
                    value={"state": "AVAILABLE"},
                ),
                StateChange(
                    slice="player",
                    operation="set",
                    path="current_area",
                    value=state.player.current_area,
                ),
                StateChange(
                    slice="player",
                    operation="set",
                    path="current_location",
                    value=state.player.current_location,
                ),
            ]
        )

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


def build_test_hook(
    *,
    planner: Any | None = None,
    blackboard: Any | None = None,
    quest_agent: Any | None = None,
    npc_agent: Any | None = None,
    world_agent: Any | None = None,
    weaver_agent: Any | None = None,
    instance_manager: InstanceManager | None = None,
) -> NarrativePlannerHook:
    # Phase 3d: In the unified planner architecture, the planner IS the blackboard
    # and returns directives directly.  No longer wrapped in PlannerBlackboardAdapter.
    # Sub-system agents (when explicitly provided) are still wired for bootstrap().
    # When planner is given without explicit agents, no agents are created —
    # the unified planner handles all directives via blackboard.plan().
    if planner is not None:
        if blackboard is None:
            blackboard = planner  # unified planner returns directives directly
        # Do NOT create agent adapters by default.  If tests need agents (e.g.
        # for bootstrap testing), pass quest_agent/npc_agent/etc. explicitly.
    if blackboard is None:
        blackboard = StaticBlackboard()

    hook = NarrativePlannerHook(blackboard=blackboard)
    dispatcher = PlannerDispatcher()
    dispatcher.register(QuestManagerSubSystem(dispatcher=dispatcher, agent=quest_agent))
    dispatcher.register(
        NpcDirectorSubSystem(
            instance_manager=instance_manager,
            agent=npc_agent,
        )
    )
    dispatcher.register(
        WorldBuilderSubSystem(
            sse_collector=hook._pending_sse,
            agent=world_agent,
        )
    )
    dispatcher.register(PacingControllerSubSystem())
    dispatcher.register(
        NarrativeWeaverSubSystem(
            sse_collector=hook._pending_sse,
            agent=weaver_agent,
        )
    )
    hook._dispatcher = dispatcher
    return hook
