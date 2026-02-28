"""Tests for NarrativePlannerHook."""

from __future__ import annotations

import asyncio
import logging

from app.game_core.content import WorldInstance
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
    SpawnQuestNpcPlan,
)
from app.game_core.rules import RulesEngine
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    AreaSlice,
    NarrativePlanSlice,
    PlayerSlice,
    QuestSlice,
    SceneSlice,
    TimeSlice,
)


class RecordingPlanner:
    def __init__(self, decision) -> None:
        self.decision = decision
        self.calls: list[dict[str, object]] = []

    def plan(self, context):
        self.calls.append(dict(context))
        return self.decision


class ExplodingPlanner:
    def plan(self, context):
        del context
        raise RuntimeError("planner unavailable")


def _make_context(
    *,
    change_log: list[StateChange] | None = None,
    narrative_plan_payload: dict[str, object] | None = None,
    quest_payload: dict[str, object] | None = None,
    area_payload: dict[str, object] | None = None,
) -> SettlementContext:
    world = WorldInstance("test_world")
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

    if area_payload is not None:
        areas = AreaSlice()
        areas.restore(area_payload)
        state.register(areas)

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

    return SettlementContext(
        change_log=active_change_log,
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=RulesEngine(),
        _apply_delta=_apply_delta,
    )


class TestNarrativePlannerHook:
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
        context = _make_context(
            change_log=[
                StateChange(
                    slice="scene",
                    operation="add",
                    path="entries",
                    value={"source": "gm"},
                )
            ],
            narrative_plan_payload={"last_run_tick": 8},
        )

        result = asyncio.run(NarrativePlannerHook().execute(context))

        assert result.metadata["status"] == "noop"
        assert result.metadata["reason"] == "cooldown"
        assert result.metadata["evaluated"] is False

    def test_default_planner_evaluates_on_fallback_and_updates_bookkeeping(self) -> None:
        context = _make_context(narrative_plan_payload={"last_run_tick": 3})

        result = asyncio.run(NarrativePlannerHook().execute(context))

        assert result.metadata["status"] == "updated"
        assert result.metadata["evaluated"] is True
        assert result.metadata["reason"] == "fallback"
        assert result.metadata["requested_count"] == 3
        assert result.metadata["applied_count"] == 3
        assert result.metadata["applied_kinds"] == [
            "create_quest",
            "publish_bulletin",
            "direct_npc",
        ]
        assert context.state.quests.get_dynamic_quest("dq_ms_1") is not None
        assert context.state.narrative_plan.active_bulletins[-1]["board_id"] == "board"
        assert context.state.narrative_plan.npc_directives[-1]["npc_id"] == "guild_clerk"
        assert context.state.narrative_plan.last_run_tick == 9
        assert context.state.narrative_plan.behavior_window[-1]["reason"] == "fallback"
        assert result.sse_events[0].event_type == "narrative_plan_updated"

    def test_default_planner_does_not_duplicate_existing_seeded_quest(self) -> None:
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

        result = asyncio.run(NarrativePlannerHook().execute(context))

        assert result.metadata["status"] == "updated"
        assert result.metadata["requested_count"] == 0
        assert result.metadata["applied_count"] == 0
        assert result.metadata["planner_metadata"]["status"] == "noop"
        assert result.metadata["planner_metadata"]["reason"] == "stable"
        assert len(context.state.quests.dynamic_quests) == 2

    def test_default_planner_stall_l1_publishes_hint(self) -> None:
        context = _make_context(
            narrative_plan_payload={
                "last_run_tick": 3,
                "ticks_since_milestone_progress": 6,
            },
            quest_payload={
                "milestone_states": {"ms_a": {"state": "ACTIVE"}},
                "dynamic_quests": {
                    "dq_ms_a": {"status": "active", "title": "Lead", "summary": "ok"},
                },
            },
        )

        result = asyncio.run(NarrativePlannerHook().execute(context))

        assert result.metadata["status"] == "updated"
        assert "publish_bulletin" in result.metadata["applied_kinds"]
        assert "escalate" in result.metadata["applied_kinds"]
        assert result.metadata["planner_metadata"]["status"] == "escalation_l1"
        assert context.state.narrative_plan.escalation_level == 1

    def test_default_planner_thaw_response_unfreezes_pacing(self) -> None:
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

        result = asyncio.run(NarrativePlannerHook().execute(context))

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

        result = asyncio.run(NarrativePlannerHook(planner=planner).execute(context))

        assert result.metadata["status"] == "updated"
        assert result.metadata["reason"] == "trigger"
        assert planner.calls[0]["changed_slices"] == ["quests"]
        assert context.state.narrative_plan.ticks_since_milestone_progress == 0

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

        result = asyncio.run(NarrativePlannerHook(planner=planner).execute(context))

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
        assert result.sse_events[0].event_type == "narrative_plan_updated"

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
        context = _make_context(change_log=[StateChange("player", "set", "current_area", "forest")])

        result = asyncio.run(NarrativePlannerHook(planner=planner).execute(context))

        assert result.metadata["applied_count"] == 5
        assert context.state.narrative_plan.npc_directives[-1]["npc_id"] == "npc_guard"
        assert context.state.narrative_plan.active_bulletins[-1]["board_id"] == "board_1"
        assert context.state.narrative_plan.escalation_level == 2
        assert context.state.narrative_plan.pacing_frozen is True
        assert context.state.narrative_plan.strategy_notes == "push the tension"
        assert context.state.narrative_plan.next_scheduled_tick == 12
        assert context.state.quests.get_dynamic_quest("dq_existing")["status"] == "retired"
        assert context.state.narrative_plan.quest_history[-1]["kind"] == "retire_quest"

    def test_unsupported_directives_are_counted(self) -> None:
        planner = RecordingPlanner(
            {
                "directives": [
                    SpawnQuestNpcPlan("npc_sidequest", {"role": "quest"}),
                    {"kind": "unknown_kind", "payload": {}},
                ]
            }
        )
        context = _make_context(change_log=[StateChange("flags", "set", "flags.x", True)])

        result = asyncio.run(NarrativePlannerHook(planner=planner).execute(context))

        assert result.metadata["applied_count"] == 0
        assert result.metadata["skipped_unsupported_count"] == 1
        assert result.metadata["skipped_invalid_count"] == 1
        assert result.sse_events == []

    def test_planner_error_returns_sse_and_preserves_state(self, caplog) -> None:
        context = _make_context(
            change_log=[StateChange("flags", "set", "flags.x", True)],
            narrative_plan_payload={"last_run_tick": 4},
        )

        with caplog.at_level(logging.ERROR):
            result = asyncio.run(
                NarrativePlannerHook(planner=ExplodingPlanner()).execute(context)
            )

        assert result.metadata["status"] == "planner_error"
        assert result.metadata["evaluated"] is False
        assert result.sse_events[0].event_type == "narrative_planner_error"
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

        result = asyncio.run(NarrativePlannerHook(planner=planner).execute(context))

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

        result = asyncio.run(NarrativePlannerHook(planner=planner).execute(context))

        assert result.metadata["applied_count"] == 0
        assert result.metadata["skipped_invalid_count"] == 1

    def test_plant_environmental_creates_search_target(self) -> None:
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

        result = asyncio.run(NarrativePlannerHook(planner=planner).execute(context))

        assert result.metadata["applied_count"] == 1
        assert result.metadata["applied_kinds"] == ["plant_environmental"]
        area = context.state.areas.areas["forest"]
        assert "blood_trail" in area.properties.get("search_targets", {})
        assert area.properties["search_targets"]["blood_trail"]["dc"] == 14

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

        result = asyncio.run(NarrativePlannerHook(planner=planner).execute(context))

        assert result.metadata["applied_count"] == 1
        assert result.metadata["applied_kinds"] == ["fill_area"]
        sub_areas = context.state.areas.list_temporary_sub_areas("forest")
        assert len(sub_areas) >= 1
        assert sub_areas[0]["id"] == "grove_1"
        assert sub_areas[0]["label"] == "Hidden Grove"

    def test_escalation_l2_directs_npc(self) -> None:
        context = _make_context(
            narrative_plan_payload={
                "last_run_tick": 3,
                "ticks_since_milestone_progress": 8,
                "escalation_level": 1,
            },
            quest_payload={
                "milestone_states": {"ms_a": {"state": "ACTIVE"}},
                "dynamic_quests": {
                    "dq_ms_a": {"status": "active", "title": "Lead", "summary": "ok"},
                },
            },
        )

        result = asyncio.run(NarrativePlannerHook().execute(context))

        assert result.metadata["status"] == "updated"
        assert "direct_npc" in result.metadata["applied_kinds"]
        assert "escalate" in result.metadata["applied_kinds"]
        assert result.metadata["planner_metadata"]["status"] == "escalation_l2"
        assert context.state.narrative_plan.escalation_level == 2

    def test_escalation_l3_creates_quest_if_missing(self) -> None:
        context = _make_context(
            narrative_plan_payload={
                "last_run_tick": 3,
                "ticks_since_milestone_progress": 11,
                "escalation_level": 2,
            },
            quest_payload={
                "milestone_states": {"ms_a": {"state": "ACTIVE"}},
                "dynamic_quests": {},
            },
        )

        result = asyncio.run(NarrativePlannerHook().execute(context))

        assert result.metadata["status"] == "updated"
        assert "create_quest" in result.metadata["applied_kinds"]
        assert "direct_npc" in result.metadata["applied_kinds"]
        assert "escalate" in result.metadata["applied_kinds"]
        assert result.metadata["planner_metadata"]["status"] == "escalation_l3"
        assert context.state.quests.get_dynamic_quest("dq_ms_a") is not None
        assert context.state.narrative_plan.escalation_level == 3

    def test_escalation_l4_crisis_with_freeze(self) -> None:
        context = _make_context(
            narrative_plan_payload={
                "last_run_tick": 3,
                "ticks_since_milestone_progress": 14,
                "escalation_level": 3,
            },
            quest_payload={
                "milestone_states": {"ms_a": {"state": "ACTIVE"}},
                "dynamic_quests": {
                    "dq_ms_a": {"status": "active", "title": "Lead", "summary": "ok"},
                },
            },
            area_payload={"areas": {"forest": {}}},
        )

        result = asyncio.run(NarrativePlannerHook().execute(context))

        assert result.metadata["status"] == "updated"
        assert "escalate" in result.metadata["applied_kinds"]
        assert "adjust_pacing" in result.metadata["applied_kinds"]
        assert "plant_environmental" in result.metadata["applied_kinds"]
        assert result.metadata["planner_metadata"]["status"] == "escalation_l4"
        assert context.state.narrative_plan.escalation_level == 4
        assert context.state.narrative_plan.pacing_frozen is True

    def test_escalation_noop_without_milestone(self) -> None:
        context = _make_context(
            narrative_plan_payload={
                "last_run_tick": 3,
                "ticks_since_milestone_progress": 8,
            },
            quest_payload={
                "milestone_states": {},
                "dynamic_quests": {},
            },
        )

        result = asyncio.run(NarrativePlannerHook().execute(context))

        assert result.metadata["status"] == "updated"
        assert result.metadata["applied_count"] == 0
        assert result.metadata["planner_metadata"]["status"] == "noop"

    def test_area_fill_triggers_when_capacity_available(self) -> None:
        context = _make_context(
            narrative_plan_payload={"last_run_tick": 3},
            quest_payload={
                "milestone_states": {},
                "dynamic_quests": {"dq_existing": {"status": "active", "title": "X", "summary": "Y"}},
            },
            area_payload={"areas": {"forest": {}}},
        )

        result = asyncio.run(NarrativePlannerHook().execute(context))

        assert result.metadata["status"] == "updated"
        assert "fill_area" in result.metadata["applied_kinds"]
        assert result.metadata["planner_metadata"]["status"] == "area_fill"
        sub_areas = context.state.areas.list_temporary_sub_areas("forest")
        assert len(sub_areas) >= 1
