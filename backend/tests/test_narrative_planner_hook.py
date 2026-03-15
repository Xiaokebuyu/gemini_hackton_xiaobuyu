"""Tests for NarrativePlannerHook."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.narrative.instance_manager import InstanceManager
from app.game_core.content.registries.maps import MapRegistry
from app.game_core.planning.subsystem import PlannerDispatcher
from app.game_core.planning.quest_manager import QuestManagerSubSystem
from app.game_core.planning.npc_director import NpcDirectorSubSystem
from app.game_core.planning.world_builder import WorldBuilderSubSystem
from app.game_core.planning.pacing_controller import PacingControllerSubSystem
from app.game_core.planning.narrative_weaver import NarrativeWeaverSubSystem
from app.game_core.content.registries.factions import FactionRegistry
from app.game_core.content.registries.lore import LoreRegistry
from app.game_core.orchestration.hooks.narrative_planner import (
    NarrativePlannerDecision,
    NarrativePlannerHook,
)
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.planning.models import (
    CreateQuestPlan,
    FillAreaPlan,
    PlantEnvironmentalPlan,
    DirectNpcPlan,
    SpawnQuestNpcPlan,
)
from app.game_core.rules import RulesEngine
from app.game_core.rules.defaults import register_default_rules_handlers
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    AreaSlice,
    EventSlice,
    NarrativePlanSlice,
    PartySlice,
    PlayerSlice,
    QuestSlice,
    SceneSlice,
    TimeSlice,
)


class RecordingPlanner:
    def __init__(self, decision) -> None:
        self.decision = decision
        self.calls: list[dict[str, object]] = []

    async def plan(self, context):
        self.calls.append(dict(context))
        return self.decision


class ExplodingPlanner:
    async def plan(self, context):
        del context
        raise RuntimeError("planner unavailable")


class StaticBlackboard:
    def __init__(self, decision: Any | None = None) -> None:
        self.decision = decision or NarrativePlannerDecision(
            directives=[],
            metadata={"status": "noop", "reason": "stable", "provider": "test_blackboard"},
        )
        self.calls: list[dict[str, object]] = []

    async def plan(self, context):
        self.calls.append(dict(context))
        return self.decision


class PlannerBlackboardAdapter:
    def __init__(self, planner: Any) -> None:
        self._planner = planner

    async def plan(self, context):
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

    async def evaluate(self, context):
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
            filtered: list[dict[str, Any]] = []
            for directive in directives:
                directive_kind = self._directive_kind_hint(directive)
                if directive_kind not in self._allowed_directives:
                    continue
                normalized = NarrativePlannerHook._normalize_directive(directive)
                if normalized is None:
                    filtered.append(directive)
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

    @staticmethod
    def _directive_kind_hint(raw: Any) -> str:
        if isinstance(raw, dict):
            return str(raw.get("kind", "")).strip()
        if isinstance(raw, CreateQuestPlan):
            return "create_quest"
        if isinstance(raw, DirectNpcPlan):
            return "direct_npc"
        if isinstance(raw, SpawnQuestNpcPlan):
            return "spawn_quest_npc"
        if isinstance(raw, PlantEnvironmentalPlan):
            return "plant_environmental"
        if isinstance(raw, FillAreaPlan):
            return "fill_area"
        return str(getattr(raw, "kind", "")).strip()


class RecordingAgent:
    def __init__(self, directives: list[dict[str, Any]] | None = None) -> None:
        self.directives = list(directives or [])
        self.calls: list[dict[str, Any]] = []

    async def evaluate(self, context):
        self.calls.append(dict(context))
        return {
            "directives": list(self.directives),
            "story_facts": [],
            "strategy_notes": "",
            "metadata": {},
        }


def _non_processing_events(result) -> list:
    """Return SSE events that are not ai_processing (QF-4 channel events)."""
    return [e for e in result.sse_events if e.event_type != "ai_processing"]


def _make_context(
    *,
    change_log: list[StateChange] | None = None,
    narrative_plan_payload: dict[str, object] | None = None,
    quest_payload: dict[str, object] | None = None,
    area_payload: dict[str, object] | None = None,
    inject_semantic_seed: bool = True,
) -> SettlementContext:
    world = WorldInstance("test_world")
    maps = MapRegistry()
    maps.load({
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
    })
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

    if area_payload is None:
        area_payload = {"areas": {"forest": {}}}
    areas = AreaSlice()
    areas.restore(area_payload)
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


def _latest_board_bulletin(context: SettlementContext) -> dict[str, Any] | None:
    area_id = str(context.state.player.current_area or "").strip()
    if not area_id:
        return None
    area_state = context.state.areas.areas.get(area_id)
    if area_state is None:
        return None
    all_entries: list[dict[str, Any]] = []
    for entries in area_state.board_bulletins.values():
        all_entries.extend(
            [dict(entry) for entry in entries if isinstance(entry, dict)]
        )
    return all_entries[-1] if all_entries else None


def _build_test_hook(
    *,
    planner: Any | None = None,
    blackboard: Any | None = None,
    quest_agent: Any | None = None,
    npc_agent: Any | None = None,
    world_agent: Any | None = None,
    weaver_agent: Any | None = None,
    instance_manager: Any | None = None,
    sub_area_manager: Any | None = None,
) -> NarrativePlannerHook:
    # Phase 3d: In the unified planner architecture, the planner IS the blackboard
    # and returns directives directly.
    # execute() only calls blackboard.plan() (no sub-system agents).
    # bootstrap() calls _run_replay (agents) + blackboard.plan().
    # Setting both planner-as-blackboard AND agents leads to double-application in bootstrap.
    # Solution: when planner is given, use it as blackboard with NO sub-system agents.
    # Both execute() and bootstrap() then work through blackboard.plan() alone.
    if planner is not None:
        if blackboard is None:
            blackboard = planner  # unified planner returns directives directly
        # Do NOT create agent adapters — the unified planner handles all directives
        # via blackboard.plan().  Agents are no longer called from execute(), and
        # in bootstrap() we let blackboard.plan() handle everything too.
    if blackboard is None:
        blackboard = StaticBlackboard()

    hook = NarrativePlannerHook(blackboard=blackboard)
    dispatcher = PlannerDispatcher()
    quest_manager = QuestManagerSubSystem(
        dispatcher=dispatcher,
        agent=quest_agent,
    )
    dispatcher.register(quest_manager)
    dispatcher.register(NpcDirectorSubSystem(
        instance_manager=instance_manager,
        agent=npc_agent,
    ))
    dispatcher.register(WorldBuilderSubSystem(
        sub_area_manager=sub_area_manager,
        sse_collector=hook._pending_sse,
        agent=world_agent,
    ))
    dispatcher.register(PacingControllerSubSystem())
    dispatcher.register(NarrativeWeaverSubSystem(
        sse_collector=hook._pending_sse,
        agent=weaver_agent,
    ))
    hook._dispatcher = dispatcher
    return hook


def _make_hook_with_sub_area_manager(planner: Any, sub_area_manager: Any) -> NarrativePlannerHook:
    """Build a NarrativePlannerHook wired with a full dispatcher + custom sub_area_manager."""
    return _build_test_hook(planner=planner, sub_area_manager=sub_area_manager)


def _make_full_hook(planner: Any = None) -> NarrativePlannerHook:
    """Build a NarrativePlannerHook with all sub-systems wired."""
    return _build_test_hook(planner=planner)


def _make_full_hook_with_instance_manager(
    planner: Any, instance_manager: Any
) -> NarrativePlannerHook:
    """Build a NarrativePlannerHook wired with a specific InstanceManager."""
    return _build_test_hook(planner=planner, instance_manager=instance_manager)


def _make_bootstrap_planner(
    *,
    seeded: bool,
    metadata: dict[str, Any] | None = None,
) -> RecordingPlanner:
    directives: list[Any] = []
    if seeded:
        directives = [
            CreateQuestPlan(
                "dq_ms_1",
                {
                    "title": "Lead: Ms 1",
                    "summary": "Follow the new lead tied to ms_1.",
                    "metadata": {"source_milestone": "ms_1", "urgency": "medium"},
                },
            ),
            {
                "kind": "publish_bulletin",
                "payload": {
                    "board_id": "board",
                    "title": "New Lead Posted",
                    "content": "A fresh lead: Ms 1.",
                    "tags": ["quest", "planner"],
                    "urgency": "medium",
                    "posted_by": "narrative_planner",
                    "metadata": {"quest_id": "dq_ms_1", "source_milestone": "ms_1"},
                },
            },
        ]
    return RecordingPlanner(
        NarrativePlannerDecision(
            directives=directives,
            metadata=metadata or (
                {"status": "quest_seeded", "provider": "bootstrap_agent"}
                if seeded
                else {"status": "noop", "provider": "bootstrap_agent", "reason": "stable"}
            ),
        )
    )


class TestNarrativePlannerHook:
    def test_hook_priority_runs_after_relationship_and_before_time_advance(self) -> None:
        assert NarrativePlannerHook.HOOK_PRIORITY == 66

    def test_missing_required_slices_return_noop(self) -> None:
        missing_plan = _make_context()
        del missing_plan.state._slices["narrative_plan"]
        missing_quests = _make_context()
        del missing_quests.state._slices["quests"]
        missing_time = _make_context()
        del missing_time.state._slices["time"]

        result_missing_plan = asyncio.run(NarrativePlannerHook().execute(missing_plan))
        result_missing_quests = asyncio.run(NarrativePlannerHook().execute(missing_quests))
        result_missing_time = asyncio.run(NarrativePlannerHook().execute(missing_time))

        assert result_missing_plan.metadata["status"] == "noop"
        assert result_missing_plan.metadata["reason"] == "missing_slice"
        assert result_missing_quests.metadata["status"] == "noop"
        assert result_missing_time.metadata["status"] == "noop"

    def test_scene_only_change_respects_cooldown(self) -> None:
        # With FALLBACK_INTERVAL=1, cooldown fires only when last_run_tick == current_tick.
        # Context uses day=1, slot=9 → absolute_tick = 1*12+9 = 21.
        # Set last_run_tick=21 so ticks_since_last_run=0 < 1 → cooldown fires.
        context = _make_context(
            change_log=[
                StateChange(
                    slice="scene",
                    operation="add",
                    path="entries",
                    value={"source": "gm"},
                )
            ],
            narrative_plan_payload={"last_run_tick": 21},
            inject_semantic_seed=False,
        )

        result = asyncio.run(NarrativePlannerHook().execute(context))

        assert result.metadata["status"] == "noop"
        assert result.metadata["reason"] == "cooldown"
        assert result.metadata["evaluated"] is False

    def test_quiet_long_rest_slot_short_circuits_without_planner_call(self) -> None:
        planner = RecordingPlanner(
            NarrativePlannerDecision(
                directives=[
                    {
                        "kind": "create_quest",
                        "payload": {"quest_id": "dq_should_not_exist"},
                    }
                ]
            )
        )
        context = _make_context(change_log=[], inject_semantic_seed=False)
        context.action_log = [{"type": "rest_long", "time_cost": 1.0}]
        context.state.time.accumulated = 4.0
        context.state.time._dirty = True

        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert planner.calls == []
        assert result.metadata["status"] == "noop"
        assert result.metadata["reason"] == "quiet_rest_slot"

    def test_planner_evaluates_on_fallback_and_updates_bookkeeping(self) -> None:
        planner = RecordingPlanner(
            NarrativePlannerDecision(
                directives=[
                    {"kind": "adjust_pacing", "payload": {"frozen": True}},
                    {"kind": "escalate", "payload": {"delta": 1}},
                ],
                metadata={"status": "fallback_maintenance", "provider": "llm_planner"},
            )
        )
        context = _make_context(
            narrative_plan_payload={"last_run_tick": 3},
            inject_semantic_seed=False,
        )

        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert result.metadata["status"] == "updated"
        assert result.metadata["evaluated"] is True
        assert result.metadata["reason"] == "fallback"
        assert result.metadata["requested_count"] == 2
        assert result.metadata["applied_count"] == 2
        assert result.metadata["applied_kinds"] == [
            "adjust_pacing",
            "escalate",
        ]
        assert context.state.narrative_plan.pacing_frozen is True
        assert context.state.narrative_plan.escalation_level == 1
        assert context.state.narrative_plan.last_run_tick == 9
        assert context.state.narrative_plan.behavior_window[-1]["reason"] == "fallback"
        assert _non_processing_events(result)[0].event_type == "narrative_plan_updated"

    def test_planner_does_not_duplicate_existing_seeded_quest(self) -> None:
        planner = RecordingPlanner(
            NarrativePlannerDecision(
                directives=[],
                metadata={"status": "noop", "provider": "llm_planner", "reason": "stable"},
            )
        )
        context = _make_context(
            narrative_plan_payload={"last_run_tick": 3},
            quest_payload={
                "dynamic_quests": {
                    "dq_existing": {
                        "status": "active",
                        "title": "Existing",
                        "summary": "In progress",
                    },
                    "dq_ms_1": {
                        "status": "available",
                        "title": "Lead: Ms 1",
                        "summary": "Already seeded",
                    },
                }
            },
        )

        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert result.metadata["status"] == "updated"
        assert result.metadata["requested_count"] == 0
        assert result.metadata["applied_count"] == 0
        assert result.metadata["planner_metadata"]["status"] == "noop"
        assert result.metadata["planner_metadata"]["reason"] == "stable"
        assert len(context.state.quests.dynamic_quests) == 2

    def test_bootstrap_seeds_first_available_milestone_without_advancing_bookkeeping(self) -> None:
        planner = _make_bootstrap_planner(seeded=True)
        context = _make_context(
            narrative_plan_payload={
                "last_run_tick": 12,
                "ticks_since_milestone_progress": 9,
            },
            quest_payload={
                "dynamic_quests": {},
            },
            area_payload={"areas": {"forest": {}}},
        )

        result = asyncio.run(_make_full_hook(planner).bootstrap(context))

        assert result.metadata["status"] == "updated"
        assert result.metadata["reason"] == "bootstrap"
        assert result.metadata["applied_kinds"] == [
            "create_quest",
            "publish_bulletin",
        ]
        assert context.state.quests.get_dynamic_quest("dq_ms_1") is not None
        bulletin = _latest_board_bulletin(context)
        assert bulletin is not None
        assert bulletin["board_id"] == "board"
        # bootstrap sets last_run_tick far in the past so the first settlement
        # after opening bypasses the FALLBACK_INTERVAL cooldown
        assert context.state.narrative_plan.last_run_tick < 0
        assert context.state.narrative_plan.escalation_level == 0
        assert context.state.areas.list_temporary_sub_areas("forest") == []
        assert result.sse_events == []

    def test_bootstrap_noops_when_seeded_quest_already_exists(self) -> None:
        planner = _make_bootstrap_planner(seeded=False)
        context = _make_context(
            narrative_plan_payload={
                "last_run_tick": 4,
                "ticks_since_milestone_progress": 9,
            },
            quest_payload={
                "dynamic_quests": {
                    "dq_existing": {
                        "status": "active",
                        "title": "Existing",
                        "summary": "In progress",
                    },
                    "dq_ms_1": {
                        "status": "available",
                        "title": "Lead: Ms 1",
                        "summary": "Already seeded",
                    },
                },
            },
            area_payload={"areas": {"forest": {}}},
        )

        result = asyncio.run(_make_full_hook(planner).bootstrap(context))

        assert result.metadata["status"] == "noop"
        assert result.metadata["reason"] == "bootstrap"
        assert result.metadata["applied_count"] == 0
        assert result.metadata["planner_metadata"]["reason"] == "stable"
        assert context.state.narrative_plan.last_run_tick < 0
        assert context.state.areas.list_temporary_sub_areas("forest") == []

    def test_planner_stall_l1_publishes_hint(self) -> None:
        planner = RecordingPlanner(
            NarrativePlannerDecision(
                directives=[
                    {"kind": "publish_bulletin", "payload": {
                        "board_id": "board",
                        "title": "Stall Hint",
                        "content": "Things are stalling.",
                        "tags": ["hint"],
                        "urgency": "medium",
                        "posted_by": "narrative_planner",
                    }},
                    {"kind": "escalate", "payload": {"delta": 1}},
                ],
                metadata={"status": "escalation_l1", "provider": "llm_planner"},
            )
        )
        context = _make_context(
            narrative_plan_payload={
                "last_run_tick": 3,
                "ticks_since_milestone_progress": 6,
                "pacing_frozen": True,
            },
            quest_payload={
                "milestone_states": {"ms_a": {"state": "ACTIVE"}},
                "dynamic_quests": {
                    "dq_ms_a": {"status": "active", "title": "Lead", "summary": "ok"},
                },
            },
        )

        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert result.metadata["status"] == "updated"
        assert "publish_bulletin" in result.metadata["applied_kinds"]
        assert "escalate" in result.metadata["applied_kinds"]
        assert result.metadata["planner_metadata"]["status"] == "escalation_l1"
        assert context.state.narrative_plan.escalation_level == 1

    def test_planner_thaw_response_unfreezes_pacing(self) -> None:
        planner = RecordingPlanner(
            NarrativePlannerDecision(
                directives=[
                    {"kind": "adjust_pacing", "payload": {"frozen": False}},
                ],
                metadata={"status": "thaw", "provider": "llm_planner"},
            )
        )
        context = _make_context(
            narrative_plan_payload={
                "last_run_tick": 3,
                "ticks_since_milestone_progress": 1,
                "pacing_frozen": True,
            },
            quest_payload={
                "milestone_states": {},
                "dynamic_quests": {},
            },
        )

        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert result.metadata["status"] == "updated"
        assert result.metadata["applied_kinds"] == ["adjust_pacing"]
        assert result.metadata["planner_metadata"]["status"] == "thaw"
        assert context.state.narrative_plan.pacing_frozen is False

    def test_trigger_change_forces_evaluation_and_resets_milestone_stall_counter(self) -> None:
        planner = RecordingPlanner([])
        context = _make_context(
            change_log=[
                StateChange(
                    slice="quests",
                    operation="set",
                    path="milestone_states.ms_1",
                    value={"state": "ACTIVE"},
                )
            ],
            narrative_plan_payload={"last_run_tick": 8, "ticks_since_milestone_progress": 5},
        )

        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert result.metadata["status"] == "updated"
        assert result.metadata["reason"] == "trigger"
        # Phase 3d: pre-processing may add changes to the change_log before building context,
        # so changed_slices may include pre-processing-modified slices (narrative_plan etc.)
        assert "quests" in planner.calls[0]["changed_slices"]
        assert context.state.narrative_plan.ticks_since_milestone_progress == 0

    def test_execute_calls_blackboard_once_with_all_events(self) -> None:
        """Phase 3d: execute() calls blackboard.plan() exactly once with all semantic events.

        The unified planner replaces the multi-round replay model:
        no sub-system agents are called from execute(); instead all events are
        collected in a single pass and passed to blackboard.plan() in planner_events.
        """
        blackboard = StaticBlackboard(
            NarrativePlannerDecision(
                directives=[
                    {
                        "kind": "create_quest",
                        "payload": {
                            "quest_id": "dq_unified",
                            "title": "Unified Lead",
                            "summary": "Created by unified planner.",
                        },
                    }
                ],
                metadata={"status": "unified"},
            )
        )
        context = _make_context(
            change_log=[
                StateChange("quests", "modify", "dynamic_quests.dq_existing", {
                    "status": "active",
                    "title": "Existing",
                    "summary": "In progress",
                }),
                StateChange("player", "set", "current_area", "forest"),
                StateChange("player", "set", "current_location", None),
            ],
            area_payload={
                "areas": {
                    "forest": {
                        "temporary_sub_areas": [
                            {"id": "camp", "label": "Camp", "expiry": 6}
                        ]
                    }
                }
            },
        )
        context.action_log = [
            {
                "type": "board_accept_quest",
                "params": {"quest_id": "dq_existing", "board_id": "board"},
                "executed": True,
            },
            {
                "type": "move_area",
                "params": {"area_id": "forest"},
                "executed": True,
            },
        ]

        hook = _build_test_hook(blackboard=blackboard)
        result = asyncio.run(hook.execute(context))

        assert result.metadata["status"] == "updated"
        # Blackboard is called exactly once
        assert len(blackboard.calls) == 1
        # planner_events contains all collected semantic events from the tick
        event_kinds = [event["kind"] for event in blackboard.calls[0]["planner_events"]]
        assert "quest_accepted" in event_kinds
        assert "area_entered" in event_kinds
        # Unified flow: replay_round_count == 0, stop_reason == "unified"
        assert result.metadata["replay_round_count"] == 0
        assert result.metadata["replay_stop_reason"] == "unified"
        # Directive from blackboard was applied
        assert "create_quest" in result.metadata["applied_kinds"]
        assert context.state.quests.get_dynamic_quest("dq_unified") is not None

    def test_create_quest_applies_and_duplicate_is_counted_invalid(self) -> None:
        planner = RecordingPlanner(
            NarrativePlannerDecision(
                directives=[
                    CreateQuestPlan(
                        "dq_new",
                        {"title": "New Quest", "summary": "Find the clue"},
                    ),
                    {"kind": "create_quest", "payload": {"quest_id": "dq_existing"}},
                ]
            )
        )
        context = _make_context(change_log=[StateChange("flags", "set", "flags.x", True)])

        result = asyncio.run(_make_full_hook(planner).execute(context))

        created = context.state.quests.get_dynamic_quest("dq_new")
        assert result.metadata["status"] == "updated"
        assert result.metadata["requested_count"] == 2
        assert result.metadata["applied_count"] == 1
        assert result.metadata["skipped_invalid_count"] == 1
        assert result.metadata["applied_kinds"] == ["create_quest"]
        assert created is not None
        assert created["status"] == "available"
        assert created["source"] == "narrative_planner"
        assert context.state.narrative_plan.quest_history[-1]["kind"] == "create_quest"
        # A-2: directive rejections now emit planner_directive_rejected SSE before
        # the narrative_plan_updated event; check the event is present somewhere
        sse_types = [e.event_type for e in result.sse_events]
        assert "narrative_plan_updated" in sse_types
        assert "planner_directive_rejected" in sse_types

    def test_create_quest_stores_dynamic_fields(self) -> None:
        planner = RecordingPlanner(
            {
                "directives": [
                    {
                        "kind": "create_quest",
                        "payload": {
                            "quest_id": "dq_ext",
                            "title": "Shattered Signal",
                            "summary": "A long-forgotten transmission.",
                            "metadata": {
                                "source_milestone": "ms_ext",
                                "urgency": "high",
                                "generated_by_escalation": 4,
                                "planner_reasoning": "critical_path",
                                "planner_notes": "unused",
                            },
                            "objectives": [
                                {"type": "collect", "target": "signal_stone"},
                                {"type": "report", "target": "watchtower"},
                            ],
                            "rewards": {"xp": 120, "item": "signal_core"},
                            "delivery_method": "board",
                            "expiry_ticks": 12,
                            "on_expire": "escalate",
                        },
                    },
                ]
            }
        )
        context = _make_context(change_log=[StateChange("flags", "set", "flags.x", True)])

        result = asyncio.run(_make_full_hook(planner).execute(context))

        created = context.state.quests.get_dynamic_quest("dq_ext")
        assert result.metadata["applied_count"] == 1
        assert result.metadata["applied_kinds"] == ["create_quest"]
        assert created is not None
        assert created["target_milestone"] == "ms_ext"
        assert created["urgency"] == "high"
        # objectives are normalized on create_quest: description + completed added, original keys preserved
        assert len(created["objectives"]) == 2
        assert created["objectives"][0]["completed"] is False
        assert created["objectives"][1]["completed"] is False
        # original legacy keys preserved for backward compat
        assert created["objectives"][0]["type"] == "collect"
        assert created["objectives"][1]["type"] == "report"
        assert created["rewards"] == {"xp": 120, "item": "signal_core"}
        assert created["delivery_method"] == "board"
        assert created["expiry_ticks"] == 12
        assert created["on_expire"] == "escalate"
        assert created["generated_by_escalation"] == 4
        assert created["planner_reasoning"] == "critical_path"

    def test_create_quest_defaults_dynamic_fields(self) -> None:
        planner = RecordingPlanner(
            {
                "directives": [
                    {
                        "kind": "create_quest",
                        "payload": {
                            "quest_id": "dq_default",
                            "title": "Default Quest",
                            "summary": "Default payload path.",
                        },
                    }
                ]
            }
        )
        context = _make_context(change_log=[StateChange("flags", "set", "flags.x", True)])

        result = asyncio.run(_make_full_hook(planner).execute(context))

        created = context.state.quests.get_dynamic_quest("dq_default")
        assert result.metadata["applied_kinds"] == ["create_quest"]
        assert created is not None
        assert created["objectives"] == []
        assert created["rewards"] == {}
        assert created["delivery_method"] == "board"
        assert created["expiry_ticks"] is None
        assert created["on_expire"] == "ignore"
        assert created["generated_by_escalation"] == 0
        assert created["planner_reasoning"] == ""
        assert created.get("target_milestone") is None

    def test_create_quest_creates_objective_events(self) -> None:
        planner = RecordingPlanner(
            {
                "directives": [
                    {
                        "kind": "create_quest",
                        "payload": {
                            "quest_id": "dq_dynamic_obj",
                            "title": "Objective Quest",
                            "summary": "Collect clues and reach site.",
                            "objectives": [
                                {"type": "collect", "target": "signal_stone"},
                                {"type": "reach_location", "target": "ruins"},
                                {
                                    "type": "kill",
                                    "target": {"monster_type": "goblin", "count": 2},
                                    "optional": True,
                                },
                            ],
                        },
                    },
                ]
            }
        )
        context = _make_context(change_log=[StateChange("flags", "set", "flags.x", True)])

        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert result.metadata["applied_count"] == 1
        assert result.metadata["applied_kinds"] == ["create_quest"]
        # Objectives are stored on the quest but NO EventSlice entries are created
        created = context.state.quests.get_dynamic_quest("dq_dynamic_obj")
        assert created is not None
        assert len(created["objectives"]) == 3
        # No objective events in EventSlice
        event_0 = context.state.events.get_event("dq_dq_dynamic_obj_obj_0")
        event_1 = context.state.events.get_event("dq_dq_dynamic_obj_obj_1")
        assert event_0 is None
        assert event_1 is None

    def test_supported_directives_update_runtime_slices(self) -> None:
        planner = RecordingPlanner(
            {
                "strategy_notes": "push the tension",
                "next_scheduled_tick": 12,
                "directives": [
                    {
                        "kind": "direct_npc",
                        "payload": {"npc_id": "npc_guard", "directive": {"mood": "alert"}},
                    },
                    {
                        "kind": "publish_bulletin",
                        "payload": {
                            "board_id": "board_1",
                            "title": "Wanted",
                            "content": "Bandits nearby",
                        },
                    },
                    {"kind": "escalate", "payload": {"delta": 2}},
                    {"kind": "adjust_pacing", "payload": {"frozen": True}},
                    {"kind": "retire_quest", "payload": {"quest_id": "dq_existing"}},
                ],
            }
        )
        context = _make_context(change_log=[
            StateChange("quests", "set", "milestone_states.ms_1", {"state": "AVAILABLE"}),
            StateChange("player", "set", "current_area", "forest"),
        ])

        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert result.metadata["applied_count"] == 5
        assert context.state.narrative_plan.npc_directives[-1]["npc_id"] == "npc_guard"
        bulletin = _latest_board_bulletin(context)
        assert bulletin is not None
        assert bulletin["board_id"] == "board_1"
        assert context.state.narrative_plan.escalation_level == 2
        assert context.state.narrative_plan.pacing_frozen is True
        assert context.state.narrative_plan.strategy_notes == "push the tension"
        assert context.state.narrative_plan.next_scheduled_tick == 12
        assert context.state.quests.get_dynamic_quest("dq_existing")["status"] == "retired"
        assert context.state.narrative_plan.quest_history[-1]["kind"] == "retire_quest"

    def test_publish_bulletin_notifies_resident_npcs(self) -> None:
        maps = MapRegistry()
        maps.load({
            "forest": {
                "id": "forest",
                "sub_locations": {
                    "quest_hub": {
                        "id": "quest_hub",
                        "name": "Quest Hub",
                        "interactables": [
                            {
                                "id": "board_1",
                                "name": "Quest Board",
                                "type": "inspect",
                                "tags": ["quest_source"],
                            }
                        ],
                        "resident_npcs": ["npc_guard", "npc_scout"],
                    }
                },
            }
        })
        world = WorldInstance("test_world_bulletin")
        world.register(maps)
        context = _make_context(change_log=[
            StateChange("quests", "set", "milestone_states.ms_1", {"state": "AVAILABLE"}),
            StateChange("player", "set", "current_area", "forest"),
        ])
        context.world = world
        planner = RecordingPlanner(
            {
                "strategy_notes": "board now active",
                "directives": [
                    {
                        "kind": "publish_bulletin",
                        "payload": {
                            "board_id": "board_1",
                            "title": "Wanted",
                            "content": "Bandits nearby",
                            "notify_resident_npcs": True,
                        },
                    }
                ],
            }
        )

        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert result.metadata["applied_kinds"] == ["publish_bulletin"]
        assert len(context.state.narrative_plan.npc_directives) == 2
        npc_ids = {entry["npc_id"] for entry in context.state.narrative_plan.npc_directives}
        assert npc_ids == {"npc_guard", "npc_scout"}
        latest = _latest_board_bulletin(context)
        assert latest is not None
        assert latest["board_id"] == "board_1"

    def test_dynamic_quest_without_expiry_ticks_keeps_running(self) -> None:
        context = _make_context(
            narrative_plan_payload={"last_run_tick": 0},
            quest_payload={
                "milestone_states": {},
                "dynamic_quests": {
                    "dq_timed": {
                        "status": "active",
                        "created_at_tick": 4,
                        "title": "Timed Quest",
                        "summary": "No expiry settings",
                    },
                },
            },
        )
        context.state.player.current_area = "void"
        result = asyncio.run(
            NarrativePlannerHook().execute(context),
        )
        assert result.metadata["applied_count"] == 0
        quest = context.state.quests.get_dynamic_quest("dq_timed")
        assert quest is not None
        assert quest["status"] == "active"
        assert not any(event.event_type == "dynamic_quest_expired" for event in result.sse_events)
        assert context.state.narrative_plan.quest_history == []

    def test_dynamic_quest_expiry_is_not_mutated_by_planner_hook(self) -> None:
        planner = RecordingPlanner(NarrativePlannerDecision(directives=[]))
        context = _make_context(
            narrative_plan_payload={"last_run_tick": 0},
            quest_payload={
                "milestone_states": {},
                "dynamic_quests": {
                    "dq_timed": {
                        "status": "active",
                        "created_at_tick": 4,
                        "expiry_ticks": 3,
                        "title": "Timed Quest",
                        "summary": "Expires now",
                        "on_expire": "ignore",
                    },
                },
            },
        )
        context.state.player.current_area = "void"
        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert not any(event.event_type == "dynamic_quest_expired" for event in result.sse_events)
        assert context.state.quests.get_dynamic_quest("dq_timed")["status"] == "active"
        assert context.state.narrative_plan.quest_history == []

    def test_dynamic_quest_expiry_escalate_is_deferred_to_dedicated_hook(self) -> None:
        planner = RecordingPlanner(NarrativePlannerDecision(directives=[]))
        context = _make_context(
            narrative_plan_payload={"last_run_tick": 0},
            quest_payload={
                "milestone_states": {},
                "dynamic_quests": {
                    "dq_timed": {
                        "status": "active",
                        "created_at_tick": 4,
                        "expiry_ticks": 3,
                        "title": "Timed Quest",
                        "summary": "Need escalate",
                        "on_expire": "escalate",
                    },
                },
            },
        )
        context.state.player.current_area = "void"
        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert context.state.narrative_plan.escalation_level == 0
        assert context.state.quests.get_dynamic_quest("dq_timed")["status"] == "active"
        assert not any(event.event_type == "dynamic_quest_expired" for event in result.sse_events)
        assert context.state.narrative_plan.quest_history == []

    def test_dynamic_quest_expiry_retire_is_deferred_to_dedicated_hook(self) -> None:
        planner = RecordingPlanner(NarrativePlannerDecision(directives=[]))
        context = _make_context(
            narrative_plan_payload={"last_run_tick": 0},
            quest_payload={
                "milestone_states": {},
                "dynamic_quests": {
                    "dq_timed": {
                        "status": "active",
                        "created_at_tick": 4,
                        "expiry_ticks": 3,
                        "title": "Timed Quest",
                        "summary": "Need retire",
                        "on_expire": "retire",
                    },
                },
            },
        )
        context.state.player.current_area = "void"
        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert context.state.quests.get_dynamic_quest("dq_timed")["status"] == "active"
        assert not any(event.event_type == "dynamic_quest_expired" for event in result.sse_events)
        assert context.state.narrative_plan.quest_history == []

    def test_direct_npc_stores_in_narrative_plan_path_b(self) -> None:
        """direct_npc stores directive in narrative_plan (Path B via blackboard).

        Path A (inject_directive → instance directive_queue) has been removed.
        NpcAutonomyHook reads npc_directives and writes goals to blackboard.
        """
        planner = RecordingPlanner(
            {
                "directives": [
                    {
                        "kind": "direct_npc",
                        "payload": {
                            "npc_id": "npc_guard",
                            "directive": {"kind": "hint", "topic": "west_gate"},
                            "priority": "high",
                        },
                    },
                ]
            }
        )
        context = _make_context(change_log=[StateChange("player", "set", "current_area", "forest")])
        instance_manager = InstanceManager()
        active_instance = instance_manager.get_or_create("test", "npc_guard", current_tick=3)

        hook = _make_full_hook_with_instance_manager(planner, instance_manager)
        result = asyncio.run(hook.execute(context))

        assert result.metadata["applied_kinds"] == ["direct_npc"]
        assert len(context.state.narrative_plan.npc_directives) == 1
        stored = context.state.narrative_plan.npc_directives[-1]
        assert stored["priority"] == "high"
        assert stored["consumed"] is False
        # Path A removed: directive is NOT injected into instance queue
        assert active_instance.directive_queue == []

    def test_unsupported_directives_are_counted(self) -> None:
        planner = RecordingPlanner(
            {
                "directives": [
                    SpawnQuestNpcPlan("npc_sidequest", {"role": "quest"}),
                    {"kind": "unknown_kind", "payload": {}},
                ]
            }
        )
        context = _make_context(
            change_log=[StateChange("player", "set", "current_area", "forest")],
            inject_semantic_seed=False,
        )
        hook = _build_test_hook(
            planner=planner,
            npc_agent=PlannerAgentAdapter(
                planner,
                allowed_directives={"spawn_quest_npc", "unknown_kind"},
            ),
        )

        result = asyncio.run(hook.execute(context))

        assert result.metadata["applied_count"] == 0
        assert result.metadata["skipped_unsupported_count"] == 1
        assert result.metadata["skipped_invalid_count"] == 1
        # A-2: invalid_contract directives now emit planner_directive_rejected SSE
        rejected_events = [e for e in result.sse_events if e.event_type == "planner_directive_rejected"]
        assert len(rejected_events) == 1  # spawn_quest_npc (invalid_contract); unknown_kind is unsupported (no SSE)
        assert rejected_events[0].payload["kind"] == "spawn_quest_npc"

    def test_planner_error_returns_sse_and_preserves_state(self, caplog) -> None:
        context = _make_context(
            change_log=[StateChange("flags", "set", "flags.x", True)],
            narrative_plan_payload={"last_run_tick": 4},
        )

        with caplog.at_level(logging.ERROR):
            result = asyncio.run(
                _make_full_hook(planner=ExplodingPlanner()).execute(context)
            )

        assert result.metadata["status"] == "planner_error"
        assert result.metadata["evaluated"] is False
        assert _non_processing_events(result)[0].event_type == "narrative_planner_error"
        assert context.state.narrative_plan.last_run_tick == 4
        assert any(
            record.message == "hook failed: narrative_planner"
            and getattr(record, "hook_name", "") == "narrative_planner"
            and record.exc_info is not None
            for record in caplog.records
        )

    def test_spawn_quest_npc_registers_npc_and_directive(self) -> None:
        planner = RecordingPlanner(
            NarrativePlannerDecision(
                directives=[
                    SpawnQuestNpcPlan(
                        "npc_quest",
                        {"area_id": "forest", "role": "informant", "description": "A cloaked figure"},
                    ),
                ]
            )
        )
        context = _make_context(
            change_log=[StateChange("flags", "set", "flags.x", True)],
            area_payload={"areas": {"forest": {}}},
        )

        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert result.metadata["applied_count"] == 1
        assert result.metadata["applied_kinds"] == ["spawn_quest_npc"]
        assert context.state.areas.find_npc_area("npc_quest") == "forest"
        last_directive = context.state.narrative_plan.npc_directives[-1]
        assert last_directive["npc_id"] == "npc_quest"
        assert last_directive["directive"]["kind"] == "spawn_quest_npc"
        assert last_directive["directive"]["role"] == "informant"

    def test_spawn_quest_npc_rejects_duplicate(self) -> None:
        planner = RecordingPlanner(
            NarrativePlannerDecision(
                directives=[
                    SpawnQuestNpcPlan("npc_existing", {"area_id": "forest"}),
                ]
            )
        )
        context = _make_context(
            change_log=[StateChange("flags", "set", "flags.x", True)],
            area_payload={"areas": {"forest": {"npc_locations": {"npc_existing": None}}}},
        )

        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert result.metadata["applied_count"] == 0
        assert result.metadata["skipped_invalid_count"] == 1

    def test_spawn_quest_npc_generates_temp_id_and_stores_profile(self) -> None:
        planner = RecordingPlanner(
            NarrativePlannerDecision(
                directives=[
                    {
                        "kind": "spawn_quest_npc",
                        "payload": {
                            "area_id": "forest",
                            "location_id": "quest_hub",
                            "name": "Rook",
                            "appearance": "Shadowed figure",
                            "personality": "quiet",
                            "role": "informant",
                            "description": "A shadow waits near the board.",
                            "dialogue_hook": "ask for help",
                            "tags": ["informant", "mystery"],
                            "linked_quest_id": "dq_report_in",
                            "despawn_in_ticks": 4,
                        },
                    }
                ]
            )
        )
        context = _make_context(
            change_log=[StateChange("flags", "set", "flags.x", True)],
            area_payload={"areas": {"forest": {}}},
            quest_payload={"milestone_states": {}},
        )
        context.state.player.current_area = "void"

        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert result.metadata["applied_count"] == 1
        assert result.metadata["applied_kinds"] == ["spawn_quest_npc"]
        area = context.state.areas.areas["forest"]
        npc_ids = [npc_id for npc_id in area.npc_locations.keys()]
        assert len(npc_ids) == 1
        npc_id = npc_ids[0]
        assert npc_id.startswith("temp_npc_9")
        assert npc_id in area.npc_locations
        assert area.npc_locations[npc_id] == "quest_hub"
        assert context.state.narrative_plan.npc_directives[-1]["npc_id"] == npc_id
        assert context.state.narrative_plan.npc_directives[-1]["directive"]["kind"] == "spawn_quest_npc"
        assert context.state.narrative_plan.npc_directives[-1]["directive"]["personality"] == "quiet"
        assert context.state.narrative_plan.npc_directives[-1]["directive"]["dialogue_hook"] == "ask for help"
        temp_profile = context.state.narrative_plan.get_temporary_npc(npc_id)
        assert temp_profile is not None
        assert temp_profile["name"] == "Rook"
        assert temp_profile["personality"] == "quiet"
        assert temp_profile["dialogue_hook"] == "ask for help"
        assert temp_profile["tags"] == ["informant", "mystery"]
        assert temp_profile["despawn_tick"] == 13

        spawn_record = context.state.narrative_plan.quest_history[-1]
        assert spawn_record["kind"] == "spawn_quest_npc"
        assert spawn_record["npc_id"] == npc_id
        assert spawn_record["name"] == "Rook"
        assert spawn_record["appearance"] == "Shadowed figure"
        assert spawn_record["personality"] == "quiet"
        assert spawn_record["dialogue_hook"] == "ask for help"
        assert spawn_record["tags"] == ["informant", "mystery"]
        assert spawn_record["linked_quest_id"] == "dq_report_in"
        assert spawn_record["despawn_tick"] == 13

    def test_spawn_quest_npc_despawns_when_history_tick_reached(self) -> None:
        context = _make_context(
            change_log=[StateChange("flags", "set", "flags.x", True)],
            quest_payload={"milestone_states": {}},
            area_payload={
                "areas": {
                    "forest": {
                        "npc_locations": {
                            "temp_npc_9": "quest_hub",
                        }
                    }
                }
            },
        )
        context.state.narrative_plan.add_temporary_npc(
            "temp_npc_9",
            {
                "name": "Temp Guard",
                "personality": "strict",
                "dialogue_hook": "Watch your step",
                "despawn_tick": 9,
            },
        )
        context.state.player.current_area = "void"
        context.state.narrative_plan.add_history(
            {
                "kind": "spawn_quest_npc",
                "npc_id": "temp_npc_9",
                "despawn_tick": 9,
            }
        )
        planner = RecordingPlanner(NarrativePlannerDecision(directives=[]))
        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert result.metadata["applied_count"] == 0
        assert "temp_npc_9" not in context.state.areas.areas["forest"].npc_locations
        assert context.state.narrative_plan.get_temporary_npc("temp_npc_9") is None

    def test_plant_environmental_creates_temporary_sub_area(self) -> None:
        planner = RecordingPlanner(
            NarrativePlannerDecision(
                directives=[
                    PlantEnvironmentalPlan(
                        "forest",
                        {"clue_id": "blood_trail", "dc": 14, "description": "Dried blood"},
                    ),
                ]
            )
        )
        context = _make_context(
            change_log=[StateChange("flags", "set", "flags.x", True)],
            area_payload={"areas": {"forest": {}}},
        )

        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert result.metadata["applied_count"] == 1
        assert result.metadata["applied_kinds"] == ["plant_environmental"]
        sub_areas = context.state.areas.list_temporary_sub_areas("forest")
        assert len(sub_areas) == 1
        assert sub_areas[0]["id"] == "blood_trail"
        assert sub_areas[0]["type"] == "discovery"
        assert sub_areas[0]["tier"] == "temporary"
        assert sub_areas[0]["discovery_dc"] == 14

    def test_fill_area_creates_sub_area_with_capacity_check(self) -> None:
        planner = RecordingPlanner(
            NarrativePlannerDecision(
                directives=[
                    FillAreaPlan("forest", {"label": "Hidden Grove", "id": "grove_1"}),
                ]
            )
        )
        context = _make_context(
            change_log=[StateChange("flags", "set", "flags.x", True)],
            area_payload={"areas": {"forest": {}}},
        )

        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert result.metadata["applied_count"] == 1
        assert result.metadata["applied_kinds"] == ["fill_area"]
        sub_areas = context.state.areas.list_temporary_sub_areas("forest")
        assert len(sub_areas) == 1
        assert sub_areas[0]["id"] == "grove_1"
        assert sub_areas[0]["label"] == "Hidden Grove"
        assert sub_areas[0]["type"] == "visit"
        assert sub_areas[0]["tier"] == "permanent"
        assert sub_areas[0]["expiry"] == -1

    def test_plant_environmental_delegates_to_sub_area_manager(self) -> None:
        class CapturingManager:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, object]]] = []

            def create(self, area_id: str, spec: dict[str, object]) -> dict[str, object] | None:
                self.calls.append((area_id, spec))
                return {
                    "id": spec.get("id", "default"),
                    "label": spec.get("label", ""),
                    "description": spec.get("description", ""),
                    "type": spec.get("type", "discovery"),
                    "tier": spec.get("tier", "temporary"),
                    "discovery_mode": spec.get("discovery_mode", "check"),
                    "discovery_dc": spec.get("discovery_dc", 12),
                    "hostile_config": None,
                    "interactables": [],
                    "resident_npcs": [],
                    "linked_quest_id": spec.get("linked_quest_id"),
                    "linked_milestone": spec.get("linked_milestone"),
                    "source": spec.get("source"),
                    "created_at_tick": spec.get("created_at_tick", 0),
                    "expiry": spec.get("expiry_ticks", 12),
                    "status": "active",
                }

        manager = CapturingManager()
        planner = RecordingPlanner(
            NarrativePlannerDecision(
                directives=[
                    PlantEnvironmentalPlan(
                        "forest",
                        {
                            "clue_id": "blood_trail",
                            "dc": 14,
                            "description": "Dried blood",
                        },
                    )
                ]
            )
        )
        context = _make_context(
            change_log=[StateChange("flags", "set", "flags.x", True)],
            area_payload={"areas": {"forest": {}}},
        )

        hook = _make_hook_with_sub_area_manager(planner, manager)
        result = asyncio.run(hook.execute(context))

        assert result.metadata["applied_count"] == 1
        assert len(manager.calls) == 1
        assert manager.calls[0][0] == "forest"
        assert manager.calls[0][1]["id"] == "blood_trail"

    def test_fill_area_delegates_to_sub_area_manager(self) -> None:
        class CapturingManager:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, object]]] = []

            def create(self, area_id: str, spec: dict[str, object]) -> dict[str, object] | None:
                self.calls.append((area_id, spec))
                return {
                    "id": spec["id"],
                    "label": spec.get("label", ""),
                    "description": spec.get("description", ""),
                    "type": spec.get("type", "visit"),
                    "tier": spec.get("tier", "permanent"),
                    "discovery_mode": spec.get("discovery_mode", ""),
                    "discovery_dc": spec.get("discovery_dc", 0),
                    "hostile_config": None,
                    "interactables": [],
                    "resident_npcs": [],
                    "linked_quest_id": None,
                    "linked_milestone": None,
                    "source": spec.get("source"),
                    "created_at_tick": spec.get("created_at_tick", 0),
                    "expiry": spec.get("expiry_ticks", -1),
                    "status": "active",
                }

        manager = CapturingManager()
        planner = RecordingPlanner(
            NarrativePlannerDecision(
                directives=[FillAreaPlan("forest", {"label": "Hidden Grove", "id": "grove_1"})]
            )
        )
        context = _make_context(
            change_log=[StateChange("flags", "set", "flags.x", True)],
            area_payload={"areas": {"forest": {}}},
        )
        hook = _make_hook_with_sub_area_manager(planner, manager)
        result = asyncio.run(hook.execute(context))

        assert result.metadata["applied_count"] == 1
        assert len(manager.calls) == 1
        assert manager.calls[0][0] == "forest"
        assert manager.calls[0][1]["id"] == "grove_1"

    def test_escalation_l2_directs_npc(self) -> None:
        planner = RecordingPlanner(
            NarrativePlannerDecision(
                directives=[
                    {"kind": "direct_npc", "payload": {
                        "npc_id": "sailor",
                        "directive": {"kind": "hint", "topic": "ms_a"},
                        "priority": "high",
                    }},
                    {"kind": "escalate", "payload": {"delta": 1}},
                ],
                metadata={"status": "escalation_l2", "provider": "llm_planner"},
            )
        )
        context = _make_context(
            narrative_plan_payload={
                "last_run_tick": 3,
                "ticks_since_milestone_progress": 8,
                "escalation_level": 1,
                "pacing_frozen": True,
            },
            quest_payload={
                "milestone_states": {"ms_a": {"state": "ACTIVE"}},
                "dynamic_quests": {
                    "dq_ms_a": {"status": "active", "title": "Lead", "summary": "ok"},
                },
            },
            area_payload={"areas": {"forest": {"npc_locations": {"sailor": True}}}},
        )

        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert result.metadata["status"] == "updated"
        assert "direct_npc" in result.metadata["applied_kinds"]
        assert "escalate" in result.metadata["applied_kinds"]
        assert result.metadata["planner_metadata"]["status"] == "escalation_l2"
        assert context.state.narrative_plan.escalation_level == 2

    def test_escalation_l3_creates_quest_if_missing(self) -> None:
        planner = RecordingPlanner(
            NarrativePlannerDecision(
                directives=[
                    CreateQuestPlan(
                        "dq_ms_a",
                        {"title": "Urgent: Lead", "summary": "Escalated quest for ms_a."},
                    ),
                    {"kind": "direct_npc", "payload": {
                        "npc_id": "sailor",
                        "directive": {"kind": "hint", "topic": "ms_a"},
                        "priority": "high",
                    }},
                    {"kind": "escalate", "payload": {"delta": 1}},
                ],
                metadata={"status": "escalation_l3", "provider": "llm_planner"},
            )
        )
        context = _make_context(
            narrative_plan_payload={
                "last_run_tick": 3,
                "ticks_since_milestone_progress": 11,
                "escalation_level": 2,
                "pacing_frozen": True,
            },
            quest_payload={
                "milestone_states": {"ms_a": {"state": "ACTIVE"}},
                "dynamic_quests": {},
            },
            area_payload={"areas": {"forest": {"npc_locations": {"sailor": True}}}},
        )

        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert result.metadata["status"] == "updated"
        assert "create_quest" in result.metadata["applied_kinds"]
        assert "direct_npc" in result.metadata["applied_kinds"]
        assert "escalate" in result.metadata["applied_kinds"]
        assert result.metadata["planner_metadata"]["status"] == "escalation_l3"
        assert context.state.quests.get_dynamic_quest("dq_ms_a") is not None
        assert context.state.narrative_plan.escalation_level == 3

    def test_escalation_l4_crisis_with_freeze(self) -> None:
        planner = RecordingPlanner(
            NarrativePlannerDecision(
                directives=[
                    {"kind": "escalate", "payload": {"delta": 1}},
                    {"kind": "adjust_pacing", "payload": {"frozen": True}},
                    PlantEnvironmentalPlan(
                        "forest",
                        {"clue_id": "crisis_clue", "dc": 15, "description": "Crisis sign"},
                    ),
                ],
                metadata={"status": "escalation_l4", "provider": "llm_planner"},
            )
        )
        context = _make_context(
            narrative_plan_payload={
                "last_run_tick": 3,
                "ticks_since_milestone_progress": 14,
                "escalation_level": 3,
                "pacing_frozen": True,
            },
            quest_payload={
                "milestone_states": {"ms_a": {"state": "ACTIVE"}},
                "dynamic_quests": {
                    "dq_ms_a": {"status": "active", "title": "Lead", "summary": "ok"},
                },
            },
            area_payload={"areas": {"forest": {}}},
        )

        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert result.metadata["status"] == "updated"
        assert "escalate" in result.metadata["applied_kinds"]
        assert "adjust_pacing" in result.metadata["applied_kinds"]
        assert "plant_environmental" in result.metadata["applied_kinds"]
        assert result.metadata["planner_metadata"]["status"] == "escalation_l4"
        assert context.state.narrative_plan.escalation_level == 4
        assert context.state.narrative_plan.pacing_frozen is True

    def test_escalation_noop_without_milestone(self) -> None:
        planner = RecordingPlanner(
            NarrativePlannerDecision(
                directives=[],
                metadata={"status": "noop", "provider": "llm_planner"},
            )
        )
        context = _make_context(
            narrative_plan_payload={
                "last_run_tick": 3,
                "ticks_since_milestone_progress": 8,
                "pacing_frozen": True,
            },
            quest_payload={
                "milestone_states": {},
                "dynamic_quests": {},
            },
        )

        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert result.metadata["status"] == "updated"
        assert result.metadata["applied_count"] == 0
        assert result.metadata["planner_metadata"]["status"] == "noop"

    def test_area_fill_triggers_when_capacity_available(self) -> None:
        planner = RecordingPlanner(
            NarrativePlannerDecision(
                directives=[
                    FillAreaPlan("forest", {"label": "New Sub-area", "id": "fill_sub_1"}),
                ],
                metadata={"status": "area_fill", "provider": "llm_planner"},
            )
        )
        context = _make_context(
            narrative_plan_payload={"last_run_tick": 3},
            quest_payload={
                "milestone_states": {},
                "dynamic_quests": {"dq_existing": {"status": "active", "title": "X", "summary": "Y"}},
            },
            area_payload={"areas": {"forest": {}}},
        )

        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert result.metadata["status"] == "updated"
        assert "fill_area" in result.metadata["applied_kinds"]
        assert result.metadata["planner_metadata"]["status"] == "area_fill"
        sub_areas = context.state.areas.list_temporary_sub_areas("forest")
        assert len(sub_areas) >= 1


class TestP36MilestoneDetail:
    """P3-6: NarrativePlanner 精确引导 — milestone detail 注入与 NPC 选择。"""

    @staticmethod
    def _world_with_milestone(
        milestone_id: str,
        *,
        key_elements: list[str] | None = None,
        involved_npcs: list[str] | None = None,
        involved_locations: list[str] | None = None,
        narrative_context: str = "",
    ) -> WorldInstance:
        from app.game_core.content.registries import QuestRegistry
        world = WorldInstance("test_world")
        quests = QuestRegistry()
        quests.load({
            "milestones": {
                milestone_id: {
                    "id": milestone_id,
                    "chapter_id": "chapter_1",
                    "key_elements": key_elements or [],
                    "involved_npcs": involved_npcs or [],
                    "involved_locations": involved_locations or [],
                    "narrative_context": narrative_context,
                },
            },
            "chapters": [{"id": "chapter_1"}],
        })
        world.register(quests)
        return world

    @staticmethod
    def _context_with_world(base_ctx: SettlementContext, world: WorldInstance) -> SettlementContext:
        return SettlementContext(
            change_log=base_ctx.change_log,
            state=base_ctx.state,
            world=world,
            scene_bus=base_ctx.scene_bus,
            _rules_engine=base_ctx._rules_engine,
            _apply_delta=base_ctx._apply_delta,
        )

    def test_build_planner_context_includes_milestone_detail(self) -> None:
        """_build_planner_context() 注入 key_elements 和 involved_npcs。"""
        world = self._world_with_milestone(
            "ms_find_crypt",
            key_elements=["find_crypt", "ancient_seal"],
            involved_npcs=["captain_smith"],
        )
        context = self._context_with_world(
            _make_context(
                narrative_plan_payload={"current_target_milestone": "ms_find_crypt"},
            ),
            world,
        )
        result = NarrativePlannerHook()._build_planner_context(context, current_tick=1)
        detail = result["target_milestone_detail"]
        assert detail["key_elements"] == ["find_crypt", "ancient_seal"]
        assert detail["involved_npcs"] == ["captain_smith"]

    def test_build_planner_context_no_detail_without_target_milestone(self) -> None:
        """current_target_milestone = None → target_milestone_detail = {}。"""
        context = _make_context(
            narrative_plan_payload={"current_target_milestone": None},
        )
        result = NarrativePlannerHook()._build_planner_context(context, current_tick=1)
        assert result["target_milestone_detail"] == {}

    def test_build_planner_context_includes_osiris_scene_and_pending_event_digest(self) -> None:
        context = _make_context()
        context.scene_bus.add_entry(
            {
                "source": "ai_osiris",
                "content": "The guild starts whispering about the player.",
                "visibility": "system",
                "tags": ["ai_osiris", "visible_consequence", "create_rumor"],
                "metadata": {
                    "kind": "visible_consequence",
                    "command_type": "create_rumor",
                    "reason": "The guild starts whispering about the player.",
                    "visibility_hint": "visible",
                    "confidence": "high",
                    "refs": {"rumor_id": "rumor_1"},
                },
            }
        )
        context.state.events.restore(
            {
                "pending_events": [
                    {
                        "event_id": "evt_retaliation",
                        "event_type": "retaliation",
                        "trigger_condition": {"type": "time_slots_elapsed", "count": 2},
                        "source": "ai_osiris",
                    }
                ]
            }
        )

        result = NarrativePlannerHook()._build_planner_context(context, current_tick=1)

        scene = result["scene"]
        assert scene["visible_command_types"] == ["create_rumor"]
        assert scene["system_entries_digest"] == [
            {
                "source": "ai_osiris",
                "content": "The guild starts whispering about the player.",
                "tags": ["ai_osiris", "visible_consequence", "create_rumor"],
                "command_type": "create_rumor",
                "reason": "The guild starts whispering about the player.",
                "visibility_hint": "visible",
                "confidence": "high",
                "refs": {"rumor_id": "rumor_1"},
            }
        ]
        assert result["events"]["pending_events_digest"] == [
            {
                "event_id": "evt_retaliation",
                "event_type": "retaliation",
                "source": "ai_osiris",
                "trigger_condition": {"type": "time_slots_elapsed", "count": 2},
            }
        ]

    def test_build_planner_context_includes_live_area_context(self) -> None:
        context = _make_context(
            narrative_plan_payload={"play_style_tags": ["DIALOGUE_HEAVY", "EXPLORER"]},
            area_payload={"areas": {"forest": {"npc_locations": {"guild_girl": None, "merchant": None}}}},
        )
        party = PartySlice()
        party.restore({"members": {"ally_zhang": {"name": "Zhang"}}})
        context.state.register(party)

        result = NarrativePlannerHook()._build_planner_context(context, current_tick=3)

        assert result["area_npcs"] == ["guild_girl", "merchant"]
        assert result["area_boards"] == [{"id": "board", "sub_location": "quest_hub"}]
        assert result["party"] == [{"id": "ally_zhang"}]
        assert result["play_style_tags"] == ["DIALOGUE_HEAVY", "EXPLORER"]

    def test_build_planner_context_without_maps_registry_area_boards_empty(self) -> None:
        context = _make_context(
            area_payload={"areas": {"forest": {"npc_locations": {"guild_girl": None}}}},
        )
        context.world = WorldInstance("test_world_without_maps")

        result = NarrativePlannerHook()._build_planner_context(context, current_tick=4)

        assert result["area_npcs"] == ["guild_girl"]
        assert result["area_boards"] == []

    def test_build_planner_context_includes_world_context(self) -> None:
        world = WorldInstance("test_world_with_context")
        maps = MapRegistry()
        maps.load({
            "forest": {
                "id": "forest",
                "name": "Enchanted Forest",
                "description": "A dim forest with whispering trees.",
                "sub_locations": {},
            }
        })
        world.register(maps)

        factions = FactionRegistry()
        factions.load({
            "faction_merchant_guild": {
                "id": "faction_merchant_guild",
                "name": "Merchant Guild",
                "influence_areas": ["forest"],
            },
            "faction_outsiders": {
                "id": "faction_outsiders",
                "name": "Outsiders",
                "influence_areas": ["town_square"],
            },
        })
        world.register(factions)

        lore = LoreRegistry()
        lore.load({
            "rules": {
                "r_area_restricted": {
                    "id": "r_area_restricted",
                    "title": "Forbidden Zone",
                    "description": "No loud magic in this area.",
                    "scope": "area",
                    "scope_id": "forest",
                    "priority": 10,
                },
                "r_chapter_hint": {
                    "id": "r_chapter_hint",
                    "title": "Chapter Focus",
                    "description": "Follow the chapter cue closely.",
                    "scope": "chapter",
                    "scope_id": "chapter_1",
                    "priority": 5,
                },
                "r_other": {
                    "id": "r_other",
                    "title": "Town Order",
                    "description": "Stay away from crowds.",
                    "scope": "area",
                    "scope_id": "town_square",
                    "priority": 3,
                },
            }
        })
        world.register(lore)

        context = self._context_with_world(
            _make_context(
                narrative_plan_payload={"current_chapter": "chapter_1"},
                quest_payload={"milestone_states": {}},
            ),
            world,
        )

        result = NarrativePlannerHook()._build_planner_context(context, current_tick=12)
        world_context = result["world_context"]

        assert world_context["area_description"] == "A dim forest with whispering trees."
        assert world_context["relevant_factions"] == [
            {"id": "faction_merchant_guild", "name": "Merchant Guild"},
        ]
        assert world_context["world_rules"][0] == {
            "id": "r_area_restricted",
            "title": "Forbidden Zone",
            "description": "No loud magic in this area.",
        }

    def test_build_planner_context_world_context_empty_without_registries(self) -> None:
        context = _make_context()
        context.world = WorldInstance("test_world_without_registries")

        result = NarrativePlannerHook()._build_planner_context(context, current_tick=5)

        assert result["world_context"] == {}

    def test_derive_play_style_tags(self) -> None:
        hook = NarrativePlannerHook()
        dialogue_window = [{"action_type": "dialogue"} for _ in range(10)]
        mixed_window = dialogue_window + [{"action_type": "combat"} for _ in range(6)]
        assert hook._derive_play_style_tags(mixed_window) == [
            "DIALOGUE_HEAVY",
            "COMBAT_FOCUSED",
        ]
        assert hook._derive_play_style_tags([{"action_type": "dialogue"}] * 5) == []

    def test_execute_updates_play_style_tags_from_behavior_window(self) -> None:
        planner = RecordingPlanner(NarrativePlannerDecision(directives=[]))
        context = _make_context(
            change_log=[StateChange("flags", "set", "flags.behavior_window_trigger", True)],
            quest_payload={"milestone_states": {}},
        )
        for i in range(10):
            context.state.narrative_plan.record_behavior(
                {"tick": i, "action_type": "dialogue"},
            )

        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert result.metadata["status"] == "updated"
        assert context.state.narrative_plan.play_style_tags == ["DIALOGUE_HEAVY"]

    def test_direct_npc_empty_directive_plan_is_skipped(self) -> None:
        planner = RecordingPlanner(
            NarrativePlannerDecision(
                directives=[
                    DirectNpcPlan("npc_guard", {}),
                ]
            )
        )
        context = _make_context(
            change_log=[StateChange("flags", "set", "flags.x", True)],
        )

        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert result.metadata["applied_count"] == 0
        assert result.metadata["skipped_invalid_count"] == 1
        assert context.state.narrative_plan.npc_directives == []

    def test_direct_npc_none_directive_payload_is_skipped(self) -> None:
        planner = RecordingPlanner(
            {
                "directives": [
                    {
                        "kind": "direct_npc",
                        "payload": {"npc_id": "npc_guard", "directive": None},
                    }
                ]
            }
        )
        context = _make_context(change_log=[StateChange("flags", "set", "flags.x", True)])

        result = asyncio.run(_make_full_hook(planner).execute(context))

        assert result.metadata["applied_count"] == 0
        assert result.metadata["skipped_invalid_count"] == 1
        assert context.state.narrative_plan.npc_directives == []


# ---------------------------------------------------------------------------
# P29-A5/A6/A8/A9 Context Enhancement Tests
# ---------------------------------------------------------------------------

class TestP29ContextEnhancements:
    """P29-A5a-d/A5e/A6b-c/A8a/A8d/A9c/A9d: Planner context field enhancements."""

    # ------------------------------------------------------------------
    # A5a: area_cluster remaining_permanent + remaining_total
    # ------------------------------------------------------------------

    def test_area_cluster_has_remaining_capacity_fields(self) -> None:
        """area_cluster should expose remaining_permanent and remaining_total, not has_capacity."""
        context = _make_context(
            area_payload={"areas": {"forest": {}}},
        )
        result = NarrativePlannerHook()._build_planner_context(context, current_tick=1)
        cluster = result["area_cluster"]
        assert cluster is not None
        assert "remaining_permanent" in cluster
        assert "remaining_total" in cluster
        assert "has_capacity" not in cluster
        # Empty area: 0 dynamic subs → full remaining
        assert cluster["remaining_permanent"] == 8
        assert cluster["remaining_total"] == 15
        assert cluster["total_dynamic"] == 0

    # ------------------------------------------------------------------
    # A5b: existing_npc_ids
    # ------------------------------------------------------------------

    def test_existing_npc_ids_combines_runtime_and_static_residents(self) -> None:
        """existing_npc_ids should include both area_state.npc_locations and static resident_npcs."""
        from app.game_core.content import WorldInstance as WI
        world = WI("test_world_npcids")
        maps = MapRegistry()
        maps.load({
            "forest": {
                "id": "forest",
                "sub_locations": {
                    "tavern": {
                        "id": "tavern",
                        "name": "Tavern",
                        "resident_npcs": ["innkeeper"],
                        "interactables": [],
                    }
                },
            }
        })
        world.register(maps)
        context = _make_context(
            area_payload={
                "areas": {
                    "forest": {
                        "npc_locations": {
                            "guild_girl": None,
                            "merchant": None,
                        }
                    }
                }
            },
        )
        # Replace world so the map registry has resident_npcs
        from app.game_core.orchestration.settlement import SettlementContext
        ctx2 = SettlementContext(
            change_log=context.change_log,
            state=context.state,
            world=world,
            scene_bus=context.scene_bus,
            _rules_engine=context._rules_engine,
            _apply_delta=context._apply_delta,
        )
        result = NarrativePlannerHook()._build_planner_context(ctx2, current_tick=1)
        npc_ids = result["existing_npc_ids"]
        assert "guild_girl" in npc_ids
        assert "merchant" in npc_ids
        assert "innkeeper" in npc_ids
        # No duplicates
        assert len(npc_ids) == len(set(npc_ids))

    # ------------------------------------------------------------------
    # A5d: previous_directive_results includes entity_id
    # ------------------------------------------------------------------

    def test_previous_directive_results_includes_entity_id(self) -> None:
        """entity_id should be extracted from payload_digest when present."""
        context = _make_context(
            narrative_plan_payload={
                "last_planner_replay_trace": {
                    "directive_audit": [
                        {
                            "kind": "create_quest",
                            "status": "applied",
                            "reason_code": None,
                            "payload_digest": {
                                "quest_id": "dq_goblin_hunt",
                                "title": "Hunt Goblins",
                            },
                        },
                        {
                            "kind": "direct_npc",
                            "status": "invalid_contract",
                            "reason_code": "missing_npc_id",
                            "payload_digest": {},
                        },
                    ]
                }
            }
        )
        result = NarrativePlannerHook()._build_planner_context(context, current_tick=5)
        pdr = result["previous_directive_results"]
        assert len(pdr) == 2
        # First entry has quest_id → entity_id
        assert pdr[0]["entity_id"] == "dq_goblin_hunt"
        # Second entry has no entity in payload_digest → no entity_id key
        assert "entity_id" not in pdr[1]

    # ------------------------------------------------------------------
    # A5e: interactable_examples + fill_area_guidance
    # ------------------------------------------------------------------

    def test_interactable_examples_extracted_from_static_sub_locations(self) -> None:
        """interactable_examples should list interactable objects from static sub-locations."""
        context = _make_context()  # _make_context already sets up forest/quest_hub/board
        result = NarrativePlannerHook()._build_planner_context(context, current_tick=1)
        examples = result["interactable_examples"]
        assert isinstance(examples, list)
        assert len(examples) >= 1
        first = examples[0]
        assert "id" in first
        assert "name" in first
        assert "type" in first
        assert "tags" in first
        assert "checks" in first
        assert isinstance(result["fill_area_guidance"], str)
        assert len(result["fill_area_guidance"]) > 10

    # ------------------------------------------------------------------
    # A6b/A6c: hostile_config_locations + encounter_guidance
    # ------------------------------------------------------------------

    def test_hostile_config_locations_populated_for_area_with_hostile_sublocs(self) -> None:
        """hostile_config_locations should list sub-locations with hostile_config defined."""
        world = WorldInstance("test_world_hostile")
        maps = MapRegistry()
        maps.load({
            "ruins": {
                "id": "ruins",
                "sub_locations": {
                    "outer_cloisters": {
                        "id": "outer_cloisters",
                        "name": "Outer Cloisters",
                        "hostile_config": {
                            "hostile_groups": [
                                {"monster_ids": ["goblin", "goblin", "goblin"], "role": "patrol"}
                            ],
                            "stealth_dc": 12,
                            "blocking": False,
                        },
                        "interactables": [],
                    },
                    "safe_hall": {
                        "id": "safe_hall",
                        "name": "Safe Hall",
                        # No hostile_config
                        "interactables": [],
                    },
                },
            }
        })
        world.register(maps)

        from app.game_core.state.slices import PlayerSlice
        base_ctx = _make_context(
            area_payload={"areas": {"ruins": {}}},
        )
        base_ctx.state.player.current_area = "ruins"

        from app.game_core.orchestration.settlement import SettlementContext
        ctx = SettlementContext(
            change_log=base_ctx.change_log,
            state=base_ctx.state,
            world=world,
            scene_bus=base_ctx.scene_bus,
            _rules_engine=base_ctx._rules_engine,
            _apply_delta=base_ctx._apply_delta,
        )
        result = NarrativePlannerHook()._build_planner_context(ctx, current_tick=1)
        hc_locs = result["hostile_config_locations"]
        assert isinstance(hc_locs, list)
        assert len(hc_locs) == 1
        entry = hc_locs[0]
        assert entry["sub_location_id"] == "outer_cloisters"
        assert "goblin" in entry["monster_ids"]
        assert entry["stealth_dc"] == 12
        assert entry["blocking"] is False
        assert entry["already_planted"] is False
        assert entry["already_cleared"] is False
        assert isinstance(result["encounter_guidance"], str)

    # ------------------------------------------------------------------
    # A8a/A8d: supported_objective_conditions + active_quests
    # ------------------------------------------------------------------

    def test_supported_objective_conditions_present_with_required_types(self) -> None:
        """supported_objective_conditions should list all required condition types."""
        context = _make_context()
        result = NarrativePlannerHook()._build_planner_context(context, current_tick=1)
        conditions = result["supported_objective_conditions"]
        assert isinstance(conditions, list)
        condition_types = {c["type"] for c in conditions}
        expected = {"npc_talked", "item_obtained", "kill_count", "location_entered", "flag_set"}
        assert expected <= condition_types
        assert isinstance(result["quest_objective_guidance"], str)

    def test_active_quests_contains_objectives_with_condition_status(self) -> None:
        """active_quests should expose objective details including condition fields."""
        context = _make_context(
            quest_payload={
                "milestone_states": {},
                "dynamic_quests": {
                    "dq_patrol": {
                        "status": "active",
                        "title": "Patrol the Road",
                        "summary": "Keep the road safe.",
                        "objectives": [
                            {
                                "description": "Kill 3 goblins",
                                "completed": False,
                                "condition": {
                                    "type": "kill_count",
                                    "monster_id": "goblin",
                                    "count": 3,
                                },
                            },
                            {
                                "description": "Report back",
                                "completed": True,
                            },
                        ],
                    },
                    "dq_retired": {
                        "status": "retired",
                        "title": "Old Quest",
                        "summary": "Done.",
                        "objectives": [],
                    },
                },
            }
        )
        result = NarrativePlannerHook()._build_planner_context(context, current_tick=2)
        active_quests = result["active_quests"]
        assert isinstance(active_quests, list)
        # Only active quests, not retired
        quest_ids = {q["quest_id"] for q in active_quests}
        assert "dq_patrol" in quest_ids
        assert "dq_retired" not in quest_ids
        patrol = next(q for q in active_quests if q["quest_id"] == "dq_patrol")
        assert patrol["title"] == "Patrol the Road"
        assert len(patrol["objectives"]) == 2
        first_obj = patrol["objectives"][0]
        assert first_obj["completed"] is False
        assert first_obj["condition"]["type"] == "kill_count"

    # ------------------------------------------------------------------
    # A9c: _ensure_milestone_outline triggers on milestone change
    # ------------------------------------------------------------------

    def test_ensure_milestone_outline_generates_fallback_without_outline_generator(self) -> None:
        """When no outline_generator is set, a deterministic fallback outline should be built."""
        from app.game_core.content.registries import QuestRegistry

        world = WorldInstance("test_world_outline")
        quests = QuestRegistry()
        quests.load({
            "milestones": {
                "ms_explore": {
                    "id": "ms_explore",
                    "chapter_id": "ch1",
                    "key_elements": ["find_the_ruins", "defeat_boss", "collect_evidence"],
                    "involved_npcs": ["captain"],
                    "involved_locations": ["ruins"],
                    "narrative_context": "Explore the ancient ruins.",
                },
            },
            "chapters": [{"id": "ch1"}],
        })
        world.register(quests)

        base_ctx = _make_context(
            narrative_plan_payload={
                "current_target_milestone": "ms_explore",
                "current_chapter": "ch1",
            }
        )
        from app.game_core.orchestration.settlement import SettlementContext
        ctx = SettlementContext(
            change_log=base_ctx.change_log,
            state=base_ctx.state,
            world=world,
            scene_bus=base_ctx.scene_bus,
            _rules_engine=base_ctx._rules_engine,
            _apply_delta=base_ctx._apply_delta,
        )

        hook = NarrativePlannerHook()  # No outline_generator injected
        asyncio.run(hook._ensure_milestone_outline(ctx, current_tick=5))

        outline = ctx.state.narrative_plan.milestone_outline
        assert outline.get("target_milestone_id") == "ms_explore"
        steps = outline.get("steps", [])
        assert len(steps) == 3  # one per key_element
        assert steps[0]["description"] == "find_the_ruins"
        assert steps[0]["condition"]["type"] == "flag_set"
        assert steps[0]["completed"] is False

    def test_ensure_milestone_outline_skips_if_outline_already_matches(self) -> None:
        """Should not overwrite an existing valid outline for the same milestone."""
        existing_outline = {
            "target_milestone_id": "ms_defend",
            "chapter_id": "ch1",
            "computed_at_tick": 1,
            "steps": [{"index": 0, "description": "Defend the gate", "type": "combat",
                        "condition": {"type": "flag_set", "flag": "ms_defend_step_0"},
                        "related_npcs": [], "related_locations": [], "completed": False,
                        "quest_id": None}],
        }
        context = _make_context(
            narrative_plan_payload={
                "current_target_milestone": "ms_defend",
                "milestone_outline": existing_outline,
            }
        )
        hook = NarrativePlannerHook()
        asyncio.run(hook._ensure_milestone_outline(context, current_tick=10))

        # Outline should be unchanged (same tick, same steps)
        outline = context.state.narrative_plan.milestone_outline
        assert outline.get("computed_at_tick") == 1  # Not overwritten

    def test_build_planner_context_includes_milestone_outline_when_set(self) -> None:
        """_build_planner_context should include milestone_outline and outline_guidance."""
        context = _make_context(
            narrative_plan_payload={
                "current_target_milestone": "ms_test",
                "milestone_outline": {
                    "target_milestone_id": "ms_test",
                    "chapter_id": "ch1",
                    "computed_at_tick": 2,
                    "steps": [
                        {
                            "index": 0,
                            "description": "Talk to the elder",
                            "type": "dialogue",
                            "condition": {"type": "npc_talked", "npc_id": "elder"},
                            "related_npcs": ["elder"],
                            "related_locations": [],
                            "completed": False,
                            "quest_id": None,
                        },
                        {
                            "index": 1,
                            "description": "Reach the ruins",
                            "type": "exploration",
                            "condition": {"type": "location_entered", "area_id": "ruins", "location_id": "entrance"},
                            "related_npcs": [],
                            "related_locations": ["ruins"],
                            "completed": True,
                            "quest_id": "dq_ruins_entry",
                        },
                    ],
                },
            }
        )
        result = NarrativePlannerHook()._build_planner_context(context, current_tick=3)
        milestone_ctx = result["milestone_outline"]
        assert milestone_ctx is not None
        assert milestone_ctx["target"] == "ms_test"
        steps = milestone_ctx["steps"]
        assert len(steps) == 2
        assert steps[0]["description"] == "Talk to the elder"
        assert steps[0]["completed"] is False
        assert steps[1]["completed"] is True
        # current_step is the first incomplete step
        current = milestone_ctx["current_step"]
        assert current is not None
        assert current["index"] == 0
        # outline_guidance is present
        assert result["outline_guidance"] is not None
        assert "step" in result["outline_guidance"]
