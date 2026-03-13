"""Tests for P20 Phase 1c+1d: Directive sub-systems.

Covers QuestManagerSubSystem, NpcDirectorSubSystem, WorldBuilderSubSystem,
PacingControllerSubSystem, and integration of all 4 via PlannerDispatcher.

All async tests use asyncio.run() wrappers — pytest-asyncio is not installed.

Decision record: D-P20b (narrative.md)
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.game_core.content import WorldInstance
from app.game_core.content.registries.maps import MapRegistry
from app.game_core.content.registries.quests import QuestRegistry, MilestoneTemplate, MilestoneCondition
from app.game_core.narrative.instance_manager import InstanceManager
from app.game_core.orchestration.hooks.narrative_planner import (
    NarrativePlannerDecision,
    NarrativePlannerHook,
)
from app.game_core.orchestration.models import SSEEvent
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.planning.npc_director import NpcDirectorSubSystem
from app.game_core.planning.pacing_controller import PacingControllerSubSystem
from app.game_core.planning.quest_manager import QuestManagerSubSystem
from app.game_core.planning.subsystem import PlannerDispatcher, PlannerEvent, SubSystemResult
from app.game_core.planning.world_builder import WorldBuilderSubSystem
from app.game_core.rules import RulesEngine, register_default_rules_handlers
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    AreaSlice,
    EventSlice,
    FlagSlice,
    NarrativePlanSlice,
    PlayerSlice,
    QuestSlice,
    SceneSlice,
    TimeSlice,
)


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------


def _make_context(
    *,
    area_payload: dict[str, Any] | None = None,
    quest_payload: dict[str, Any] | None = None,
    player_area: str = "frontier_town",
    with_events: bool = True,
    world: WorldInstance | None = None,
    flags: bool = True,
) -> SettlementContext:
    """Build a minimal SettlementContext for directive testing."""
    if world is None:
        world = WorldInstance("test_world")

    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": player_area, "current_location": None})
    state.register(player)

    quests = QuestSlice()
    base_quest: dict[str, Any] = {
        "milestone_states": {},
        "dynamic_quests": {},
    }
    if quest_payload:
        base_quest.update(quest_payload)
    quests.restore(base_quest)
    state.register(quests)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({})
    state.register(narrative_plan)

    if area_payload is None:
        area_payload = {"areas": {player_area: {}}}
    areas = AreaSlice()
    areas.restore(area_payload)
    state.register(areas)

    if with_events:
        events = EventSlice()
        events.restore({})
        state.register(events)

    if flags:
        flag_slice = FlagSlice()
        flag_slice.restore({})
        state.register(flag_slice)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    change_log: list[StateChange] = []
    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)

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
    )


def _make_dispatcher_with_all(
    *,
    instance_manager: InstanceManager | None = None,
    sse_collector: list[SSEEvent] | None = None,
) -> tuple[PlannerDispatcher, list[SSEEvent]]:
    """Build a PlannerDispatcher with all 4 sub-systems registered."""
    pending_sse: list[SSEEvent] = sse_collector if sse_collector is not None else []
    dispatcher = PlannerDispatcher()
    quest_manager = QuestManagerSubSystem(dispatcher=dispatcher, sse_collector=pending_sse)
    dispatcher.register(quest_manager)
    dispatcher.register(NpcDirectorSubSystem(instance_manager=instance_manager))
    dispatcher.register(WorldBuilderSubSystem(sse_collector=pending_sse))
    dispatcher.register(PacingControllerSubSystem())
    return dispatcher, pending_sse


# ---------------------------------------------------------------------------
# QuestManager tests
# ---------------------------------------------------------------------------


def test_create_quest_adds_to_dynamic_quests():
    """create_quest adds the quest to dynamic_quests and returns True."""
    context = _make_context()
    dispatcher, _ = _make_dispatcher_with_all()

    ok = dispatcher.apply_directive(
        "create_quest",
        {"quest_id": "dq_test", "title": "A Test Quest", "summary": "Testing."},
        context,
        current_tick=1,
    )

    assert ok is True
    assert context.state.quests.get_dynamic_quest("dq_test") is not None
    quest = context.state.quests.get_dynamic_quest("dq_test")
    assert quest["title"] == "A Test Quest"
    assert quest["status"] == "available"


def test_create_quest_rejects_duplicate():
    """create_quest returns a rejection reason when quest_id already exists."""
    context = _make_context(
        quest_payload={"dynamic_quests": {"dq_existing": {"status": "active", "title": "Existing", "summary": ""}}}
    )
    dispatcher, _ = _make_dispatcher_with_all()

    ok = dispatcher.apply_directive(
        "create_quest",
        {"quest_id": "dq_existing"},
        context,
        current_tick=1,
    )

    assert ok is not True  # Returns a rejection reason string


def test_create_quest_records_history_and_change():
    """create_quest writes add_history entry and record_change."""
    context = _make_context()
    dispatcher, _ = _make_dispatcher_with_all()

    dispatcher.apply_directive(
        "create_quest",
        {"quest_id": "dq_hist"},
        context,
        current_tick=10,
    )

    history = context.state.narrative_plan.quest_history
    assert any(e["kind"] == "create_quest" and e["quest_id"] == "dq_hist" for e in history)
    # StateChange should have been recorded
    assert any(c.slice == "quests" and "dq_hist" in c.path for c in context.change_log)


def test_create_quest_emits_frontend_visible_sse() -> None:
    """create_quest emits quest SSE so the UI can refresh the quest panel immediately."""
    context = _make_context()
    dispatcher, pending_sse = _make_dispatcher_with_all()

    ok = dispatcher.apply_directive(
        "create_quest",
        {"quest_id": "dq_signal", "title": "Signal", "summary": "Track the signal."},
        context,
        current_tick=4,
    )

    assert ok is True
    sse_types = [event.event_type for event in pending_sse]
    assert "quest_created" in sse_types
    assert "quest_status_changed" in sse_types
    created_payload = next(
        event.payload for event in pending_sse if event.event_type == "quest_created"
    )
    assert created_payload["quest_id"] == "dq_signal"
    assert created_payload["status"] == "available"


def test_create_quest_creates_milestone_events():
    """create_quest registers dormant EventSlice events for milestone conditions."""
    world = WorldInstance("test_world")
    qr = QuestRegistry()
    milestone = MilestoneTemplate(
        id="ms_rescue",
        title="Rescue Mission",
        narrative_context="",
        key_elements=[],
        involved_npcs=[],
        involved_locations=[],
        failure_fallback=None,
        success_conditions=[MilestoneCondition(type="location_visited", params={"area_id": "cave"})],
        failure_conditions=[],
    )
    qr._milestones["ms_rescue"] = milestone
    world.register(qr)

    context = _make_context(world=world)
    dispatcher, _ = _make_dispatcher_with_all()
    dispatcher.apply_directive(
        "create_quest",
        {"quest_id": "ms_rescue", "title": "Rescue"},
        context,
        current_tick=1,
    )

    event_id = "milestone_ms_rescue_sc_0"
    event = context.state.events.get_event(event_id)
    assert event is not None
    assert event.get("state") == "dormant"


def test_create_quest_creates_objective_events():
    """create_quest stores objectives on the quest but does NOT create EventSlice entries."""
    context = _make_context()
    dispatcher, _ = _make_dispatcher_with_all()

    dispatcher.apply_directive(
        "create_quest",
        {
            "quest_id": "dq_obj_test",
            "objectives": [
                {"type": "kill", "target": {"monster_type": "bandit"}, "optional": False},
                {"type": "talk_to", "target": "innkeeper"},
            ],
        },
        context,
        current_tick=1,
    )

    # Objectives are stored on the quest itself
    created = context.state.quests.get_dynamic_quest("dq_obj_test")
    assert created is not None
    assert len(created["objectives"]) == 2
    # No EventSlice entries for objectives (Path B removed)
    event0 = context.state.events.get_event("dq_dq_obj_test_obj_0")
    event1 = context.state.events.get_event("dq_dq_obj_test_obj_1")
    assert event0 is None
    assert event1 is None


def test_publish_bulletin_adds_entry():
    """publish_bulletin adds a board entry to the area state."""
    context = _make_context(area_payload={"areas": {"frontier_town": {}}})
    dispatcher, _ = _make_dispatcher_with_all()

    ok = dispatcher.apply_directive(
        "publish_bulletin",
        {
            "board_id": "quest_board",
            "area_id": "frontier_town",
            "title": "New Job Available",
            "content": "Guards needed.",
        },
        context,
        current_tick=1,
    )

    assert ok is True
    area_state = context.state.areas.areas["frontier_town"]
    assert "quest_board" in area_state.board_bulletins


def test_publish_bulletin_notifies_resident_npcs():
    """publish_bulletin cross-routes direct_npc via dispatcher for resident NPCs."""
    world = WorldInstance("test_world")
    maps = MapRegistry()
    maps.load({
        "frontier_town": {
            "id": "frontier_town",
            "sub_locations": {
                "guild_hall": {
                    "id": "guild_hall",
                    "name": "Guild Hall",
                    "interactables": [
                        {"id": "job_board", "name": "Job Board", "type": "inspect", "tags": ["quest_source"]}
                    ],
                    "resident_npcs": ["guild_master"],
                }
            },
        }
    })
    world.register(maps)

    context = _make_context(
        world=world,
        area_payload={"areas": {"frontier_town": {}}},
    )
    dispatcher, _ = _make_dispatcher_with_all()

    dispatcher.apply_directive(
        "publish_bulletin",
        {
            "board_id": "job_board",
            "area_id": "frontier_town",
            "title": "Urgent Quest",
            "content": "Danger!",
            "location": {"area_id": "frontier_town", "sub_location": "guild_hall"},
            # No quest_id in metadata — avoids quest existence validation
            "metadata": {"source": "test"},
        },
        context,
        current_tick=1,
    )

    # Resident NPC guild_master should have received a direct_npc directive
    directives = context.state.narrative_plan.npc_directives
    npc_ids = [d.get("npc_id") for d in directives]
    assert "guild_master" in npc_ids


def test_retire_quest_sets_status_retired():
    """retire_quest marks the dynamic quest as retired."""
    context = _make_context(
        quest_payload={"dynamic_quests": {"dq_retire_me": {"status": "active", "title": "X", "summary": ""}}}
    )
    dispatcher, _ = _make_dispatcher_with_all()

    ok = dispatcher.apply_directive(
        "retire_quest",
        {"quest_id": "dq_retire_me"},
        context,
        current_tick=5,
    )

    assert ok is True
    quest = context.state.quests.get_dynamic_quest("dq_retire_me")
    assert quest["status"] == "retired"


def test_retire_quest_cascade_cleanup():
    """retire_quest removes linked board bulletins, sub-areas, and NPC directives."""
    area_id = "frontier_town"
    context = _make_context(
        quest_payload={"dynamic_quests": {"dq_cascade": {"status": "active", "title": "X", "summary": ""}}},
        area_payload={"areas": {area_id: {}}},
    )
    # Manually add a board bulletin for the quest
    context.state.areas.add_board_bulletin(area_id, "board_1", {"quest_id": "dq_cascade"})
    # Manually add a directive linked to the quest
    context.state.narrative_plan.npc_directives = [
        {"npc_id": "npc_a", "linked_quest_id": "dq_cascade", "directive": {}, "consumed": False}
    ]

    dispatcher, _ = _make_dispatcher_with_all()
    dispatcher.apply_directive("retire_quest", {"quest_id": "dq_cascade"}, context, current_tick=5)

    # Directive should be cleaned up
    remaining = [d for d in context.state.narrative_plan.npc_directives if d.get("linked_quest_id") == "dq_cascade"]
    assert remaining == []


# ---------------------------------------------------------------------------
# NpcDirector tests
# ---------------------------------------------------------------------------


def test_direct_npc_adds_directive():
    """direct_npc adds a directive entry to narrative_plan."""
    context = _make_context()
    npc_director = NpcDirectorSubSystem()

    ok = npc_director.apply_directive(
        "direct_npc",
        {
            "npc_id": "inn_keeper",
            "directive": {"kind": "hint", "topic": "goblin_cave"},
            "priority": "high",
        },
        context,
        current_tick=3,
    )

    assert ok is True
    directives = context.state.narrative_plan.npc_directives
    assert len(directives) == 1
    assert directives[0]["npc_id"] == "inn_keeper"
    assert directives[0]["priority"] == "high"
    assert directives[0]["consumed"] is False


def test_direct_npc_injects_to_instance_manager():
    """direct_npc calls inject_directive on an active InstanceManager."""
    im = InstanceManager()
    active = im.get_or_create("npc_scout", current_tick=1)

    context = _make_context()
    npc_director = NpcDirectorSubSystem(instance_manager=im)

    npc_director.apply_directive(
        "direct_npc",
        {
            "npc_id": "npc_scout",
            "directive": {"kind": "patrol", "route": "west"},
        },
        context,
        current_tick=2,
    )

    # inject_directive should have placed directive into the instance queue
    assert len(active.directive_queue) == 1
    assert active.directive_queue[0]["npc_id"] == "npc_scout"


def test_direct_npc_rejects_missing_directive():
    """direct_npc returns a rejection reason when no directive is provided."""
    context = _make_context()
    npc_director = NpcDirectorSubSystem()

    result = npc_director.apply_directive(
        "direct_npc",
        {"npc_id": "npc_a"},  # no directive key
        context,
        current_tick=1,
    )

    assert result is not True  # Returns a string reason code, not True
    assert context.state.narrative_plan.npc_directives == []


def test_spawn_quest_npc_creates_and_moves():
    """spawn_quest_npc creates a temporary NPC and places it in the area."""
    context = _make_context(area_payload={"areas": {"frontier_town": {}}})
    npc_director = NpcDirectorSubSystem()

    ok = npc_director.apply_directive(
        "spawn_quest_npc",
        {
            "npc_id": "stranger_001",
            "area_id": "frontier_town",
            "name": "A Stranger",
            "personality": "suspicious",
            "role": "witness",
        },
        context,
        current_tick=5,
    )

    assert ok is True
    # NPC should be in the area
    area_state = context.state.areas.areas["frontier_town"]
    assert "stranger_001" in area_state.npc_locations
    # Profile in temporary_npcs
    npcs = context.state.narrative_plan.temporary_npcs
    assert "stranger_001" in npcs


def test_spawn_quest_npc_auto_generates_id():
    """spawn_quest_npc auto-generates an ID when npc_id is not provided."""
    context = _make_context(area_payload={"areas": {"frontier_town": {}}})
    npc_director = NpcDirectorSubSystem()

    ok = npc_director.apply_directive(
        "spawn_quest_npc",
        {"area_id": "frontier_town"},  # no npc_id
        context,
        current_tick=7,
    )

    assert ok is True
    area_state = context.state.areas.areas["frontier_town"]
    # Some npc should have been placed
    assert len(area_state.npc_locations) > 0


def test_spawn_quest_npc_rejects_existing_npc():
    """spawn_quest_npc returns a rejection reason for a npc_id already present in areas."""
    context = _make_context(
        area_payload={"areas": {"frontier_town": {"npc_locations": {"guard_captain": True}}}}
    )
    npc_director = NpcDirectorSubSystem()

    ok = npc_director.apply_directive(
        "spawn_quest_npc",
        {"npc_id": "guard_captain", "area_id": "frontier_town"},
        context,
        current_tick=1,
    )

    assert ok is not True  # Returns a rejection reason string


# ---------------------------------------------------------------------------
# WorldBuilder tests
# ---------------------------------------------------------------------------


def test_plant_environmental_creates_discovery():
    """plant_environmental creates a temporary sub-area via DynamicSubAreaManager."""
    context = _make_context(area_payload={"areas": {"frontier_town": {}}})
    pending_sse: list[SSEEvent] = []
    world_builder = WorldBuilderSubSystem(sse_collector=pending_sse)

    ok = world_builder.apply_directive(
        "plant_environmental",
        {
            "area_id": "frontier_town",
            "clue_id": "blood_smear",
            "description": "A blood smear on the wall.",
            "dc": 14,
        },
        context,
        current_tick=1,
    )

    assert ok is True
    sub_areas = context.state.areas.list_temporary_sub_areas("frontier_town")
    assert len(sub_areas) == 1
    assert sub_areas[0]["id"] == "blood_smear"
    assert sub_areas[0]["tier"] == "temporary"


def test_plant_environmental_emits_sse():
    """plant_environmental appends an environment_changed SSE event."""
    context = _make_context(area_payload={"areas": {"frontier_town": {}}})
    pending_sse: list[SSEEvent] = []
    world_builder = WorldBuilderSubSystem(sse_collector=pending_sse)

    world_builder.apply_directive(
        "plant_environmental",
        {"area_id": "frontier_town", "description": "Footprints."},
        context,
        current_tick=2,
    )

    assert len(pending_sse) == 1
    assert pending_sse[0].event_type == "environment_changed"
    assert pending_sse[0].payload["change_type"] == "plant_environmental"
    assert pending_sse[0].payload["area_id"] == "frontier_town"


def test_plant_environmental_in_scene_context_translates_to_clue_interactable() -> None:
    world = WorldInstance("test_world")
    maps = MapRegistry()
    maps.load(
        {
            "frontier_town": {
                "id": "frontier_town",
                "name": "边境小镇",
                "default_sub_location": "adventurer_guild",
                "sub_locations": {
                    "adventurer_guild": {
                        "id": "adventurer_guild",
                        "name": "冒险者公会",
                        "default_room": "guild_counter",
                        "rooms": {
                            "guild_counter": {
                                "id": "guild_counter",
                                "name": "受付柜台",
                            }
                        },
                    }
                },
            }
        }
    )
    world.register(maps)
    context = _make_context(area_payload={"areas": {"frontier_town": {}}}, world=world)
    context.state.player.current_location = "adventurer_guild"
    context.state.player.current_room = "guild_counter"
    pending_sse: list[SSEEvent] = []
    world_builder = WorldBuilderSubSystem(sse_collector=pending_sse)

    ok = world_builder.apply_directive(
        "plant_environmental",
        {
            "area_id": "frontier_town",
            "clue_id": "blood_smear",
            "description": "墙边有一抹新鲜血迹。",
        },
        context,
        current_tick=3,
    )

    assert ok is True
    assert context.state.areas.list_temporary_sub_areas("frontier_town") == []
    overlays = context.state.areas.list_scoped_interactable_overlays(
        "frontier_town",
        "adventurer_guild",
        "guild_counter",
    )
    assert len(overlays) == 1
    assert overlays[0]["id"] == "blood_smear"
    assert overlays[0]["functional"]["type"] == "investigate_clue"
    assert pending_sse[0].payload["change_type"] == "fill_location"


def test_fill_area_creates_permanent_sub_area():
    """fill_area creates a permanent-tier sub-area."""
    context = _make_context(area_payload={"areas": {"frontier_town": {}}})
    pending_sse: list[SSEEvent] = []
    world_builder = WorldBuilderSubSystem(sse_collector=pending_sse)

    ok = world_builder.apply_directive(
        "fill_area",
        {
            "area_id": "frontier_town",
            "id": "hidden_grove",
            "label": "Hidden Grove",
            "description": "A secluded grove.",
        },
        context,
        current_tick=3,
    )

    assert ok is True
    sub_areas = context.state.areas.list_temporary_sub_areas("frontier_town")
    assert len(sub_areas) == 1
    assert sub_areas[0]["tier"] == "permanent"
    assert sub_areas[0]["expiry"] == -1


def test_fill_area_emits_sse():
    """fill_area appends an environment_changed SSE event."""
    context = _make_context(area_payload={"areas": {"frontier_town": {}}})
    pending_sse: list[SSEEvent] = []
    world_builder = WorldBuilderSubSystem(sse_collector=pending_sse)

    world_builder.apply_directive(
        "fill_area",
        {"area_id": "frontier_town", "label": "Tavern Back Room"},
        context,
        current_tick=4,
    )

    assert len(pending_sse) == 1
    assert pending_sse[0].payload["change_type"] == "fill_area"


def test_world_builder_rejects_invalid_area_id():
    """plant_environmental and fill_area return False for non-existent area_id."""
    context = _make_context(area_payload={"areas": {"frontier_town": {}}})
    pending_sse: list[SSEEvent] = []
    world_builder = WorldBuilderSubSystem(sse_collector=pending_sse)

    ok_plant = world_builder.apply_directive(
        "plant_environmental",
        {"area_id": "nonexistent_area"},
        context,
        current_tick=1,
    )
    ok_fill = world_builder.apply_directive(
        "fill_area",
        {"area_id": "nonexistent_area"},
        context,
        current_tick=1,
    )

    assert ok_plant is not True  # Returns a rejection reason string
    assert ok_fill is not True   # Returns a rejection reason string
    assert pending_sse == []


# ---------------------------------------------------------------------------
# PacingController tests
# ---------------------------------------------------------------------------


def test_escalate_adjusts_level_and_danger():
    """escalate calls adjust_escalation and executes adjust_danger + set_flag."""
    context = _make_context()
    pacing = PacingControllerSubSystem()

    ok = pacing.apply_directive(
        "escalate",
        {"delta": 1},
        context,
        current_tick=1,
    )

    assert ok is True
    assert context.state.narrative_plan.escalation_level == 1
    # set_flag should have recorded narrative_escalation_level
    flag_val = context.state.flags.get("narrative_escalation_level")
    assert flag_val == 1


def test_escalate_rejects_out_of_range():
    """escalate returns a rejection reason when |delta| > 3."""
    context = _make_context()
    pacing = PacingControllerSubSystem()

    ok_high = pacing.apply_directive("escalate", {"delta": 4}, context, current_tick=1)
    ok_low = pacing.apply_directive("escalate", {"delta": -4}, context, current_tick=1)
    ok_bool = pacing.apply_directive("escalate", {"delta": True}, context, current_tick=1)

    assert ok_high is not True  # Returns a rejection reason string
    assert ok_low is not True   # Returns a rejection reason string
    assert ok_bool is not True  # Returns a rejection reason string
    assert context.state.narrative_plan.escalation_level == 0  # unchanged


def test_adjust_pacing_sets_frozen():
    """adjust_pacing calls set_pacing_frozen with the correct bool."""
    context = _make_context()
    pacing = PacingControllerSubSystem()

    ok_freeze = pacing.apply_directive("adjust_pacing", {"frozen": True}, context, current_tick=1)
    assert ok_freeze is True
    assert context.state.narrative_plan.pacing_frozen is True

    ok_thaw = pacing.apply_directive("adjust_pacing", {"frozen": False}, context, current_tick=2)
    assert ok_thaw is True
    assert context.state.narrative_plan.pacing_frozen is False


def test_adjust_pacing_rejects_non_bool():
    """adjust_pacing returns a rejection reason when frozen is not a bool."""
    context = _make_context()
    pacing = PacingControllerSubSystem()

    ok = pacing.apply_directive("adjust_pacing", {"frozen": "true"}, context, current_tick=1)

    assert ok is not True  # Returns a rejection reason string


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


def test_dispatcher_routes_to_correct_subsystem():
    """All 4 sub-systems each handle their own directive kinds correctly."""
    context = _make_context(
        area_payload={"areas": {"frontier_town": {}}},
    )
    dispatcher, pending_sse = _make_dispatcher_with_all()

    # create_quest → QuestManager
    assert dispatcher.apply_directive("create_quest", {"quest_id": "dq_int"}, context, current_tick=1)
    assert context.state.quests.get_dynamic_quest("dq_int") is not None

    # direct_npc → NpcDirector
    assert dispatcher.apply_directive(
        "direct_npc",
        {"npc_id": "guide", "directive": {"kind": "lead"}},
        context,
        current_tick=1,
    )
    assert len(context.state.narrative_plan.npc_directives) >= 1
    npc_ids = {d.get("npc_id") for d in context.state.narrative_plan.npc_directives}
    assert "guide" in npc_ids

    # plant_environmental → WorldBuilder
    assert dispatcher.apply_directive(
        "plant_environmental",
        {"area_id": "frontier_town", "description": "Scratches on door."},
        context,
        current_tick=1,
    )
    sub_areas = context.state.areas.list_temporary_sub_areas("frontier_town")
    assert len(sub_areas) == 1

    # escalate → PacingController
    assert dispatcher.apply_directive("escalate", {"delta": 1}, context, current_tick=1)
    assert context.state.narrative_plan.escalation_level == 1


def test_full_decision_flow_no_legacy():
    """NarrativePlannerHook + 4 sub-systems end-to-end, no LegacyDirectiveSubSystem."""
    class _SimpleBlackboard:
        async def plan(self, ctx):
            del ctx
            return {"directives": [], "story_facts": [], "metadata": {"status": "noop"}}

    class _SimpleQuestAgent:
        async def evaluate(self, ctx):
            del ctx
            return {
                "directives": [
                    {"kind": "create_quest", "payload": {"quest_id": "dq_full_flow", "title": "Full Flow"}},
                    {"kind": "adjust_pacing", "payload": {"frozen": False}},
                ],
                "story_facts": [],
            }

    async def _run():
        hook = NarrativePlannerHook(blackboard=_SimpleBlackboard())
        pending_sse: list[SSEEvent] = hook._pending_sse
        dispatcher = PlannerDispatcher()
        quest_manager = QuestManagerSubSystem(
            dispatcher=dispatcher,
            agent=_SimpleQuestAgent(),
        )
        dispatcher.register(quest_manager)
        dispatcher.register(NpcDirectorSubSystem())
        dispatcher.register(WorldBuilderSubSystem(sse_collector=pending_sse))
        dispatcher.register(PacingControllerSubSystem())
        hook._dispatcher = dispatcher

        context = _make_context(area_payload={"areas": {"frontier_town": {}}})
        # Route QuestManager via a semantic event instead of generic flags.
        context.change_log.append(
            StateChange("quests", "set", "milestone_states.ms_1", {"state": "AVAILABLE"})
        )

        result = await hook.execute(context)

        assert result.metadata["applied_count"] == 3
        assert "create_quest" in result.metadata["applied_kinds"]
        assert "adjust_pacing" in result.metadata["applied_kinds"]
        assert context.state.quests.get_dynamic_quest("dq_full_flow") is not None
        assert result.metadata["replay_round_count"] == 2

    asyncio.run(_run())


def test_publish_bulletin_cross_system_direct_npc():
    """publish_bulletin routes direct_npc cross-system via QuestManager → Dispatcher → NpcDirector."""
    world = WorldInstance("test_world")
    maps = MapRegistry()
    maps.load({
        "frontier_town": {
            "id": "frontier_town",
            "sub_locations": {
                "market": {
                    "id": "market",
                    "name": "Market Square",
                    "interactables": [
                        {"id": "notice_board", "name": "Notice Board", "type": "inspect", "tags": ["quest_source"]}
                    ],
                    "resident_npcs": ["merchant_a", "crier"],
                }
            },
        }
    })
    world.register(maps)

    context = _make_context(
        world=world,
        area_payload={"areas": {"frontier_town": {}}},
    )
    dispatcher, _ = _make_dispatcher_with_all()

    dispatcher.apply_directive(
        "publish_bulletin",
        {
            "board_id": "notice_board",
            "area_id": "frontier_town",
            "title": "Caravan Job",
            "content": "Need guards for caravan.",
            "location": {"area_id": "frontier_town", "sub_location": "market"},
            # No quest_id — avoids quest existence validation (A-1 change)
            "metadata": {"source": "test"},
        },
        context,
        current_tick=3,
    )

    # Both resident NPCs should have received direct_npc directives
    npc_ids = {d.get("npc_id") for d in context.state.narrative_plan.npc_directives}
    assert "merchant_a" in npc_ids
    assert "crier" in npc_ids


def test_all_subsystem_handles_are_disjoint():
    """Each directive kind belongs to exactly one sub-system (no overlap)."""
    dispatcher = PlannerDispatcher()
    sse_col: list[SSEEvent] = []
    quest_manager = QuestManagerSubSystem(dispatcher=dispatcher)
    npc_director = NpcDirectorSubSystem()
    world_builder = WorldBuilderSubSystem(sse_collector=sse_col)
    pacing = PacingControllerSubSystem()

    all_handles = (
        list(quest_manager.handles)
        + list(npc_director.handles)
        + list(world_builder.handles)
        + list(pacing.handles)
    )
    assert len(all_handles) == len(set(all_handles)), "Duplicate handles found across sub-systems"


def test_all_subsystem_names_are_unique():
    """Sub-system names must be distinct (no duplicates in dispatcher)."""
    dispatcher = PlannerDispatcher()
    sse_col: list[SSEEvent] = []
    quest_manager = QuestManagerSubSystem(dispatcher=dispatcher)
    npc_director = NpcDirectorSubSystem()
    world_builder = WorldBuilderSubSystem(sse_collector=sse_col)
    pacing = PacingControllerSubSystem()

    names = [quest_manager.name, npc_director.name, world_builder.name, pacing.name]
    assert len(names) == len(set(names))


def test_subsystem_event_subscription_matrix():
    """Each sub-system subscribes only to its semantic event set."""
    dispatcher = PlannerDispatcher()
    sse_col: list[SSEEvent] = []
    subscriptions = {
        "quest_manager": (
            QuestManagerSubSystem(dispatcher=dispatcher),
            {
                "bootstrap",
                "milestone_available",
                "milestone_activated",
                "milestone_completed",
                "milestone_failed",
                "quest_accepted",
                "quest_created",
                "quest_updated",
                "quest_objective_completed",
                "quest_completed",
                "quest_retired",
                "quest_expired",
            },
        ),
        "npc_director": (
            NpcDirectorSubSystem(),
            {
                "quest_accepted",
                "quest_created",
                "quest_updated",
                "quest_objective_completed",
                "quest_completed",
                "milestone_completed",
                "milestone_failed",
                "area_entered",
                "sub_location_entered",
                "scene_changed",
                "relationship_stage_changed",
                "world_event_active",
                "world_event_resolved",
                "combat_resolved",
            },
        ),
        "world_builder": (
            WorldBuilderSubSystem(sse_collector=sse_col),
            {
                "area_entered",
                "sub_location_entered",
                "scene_changed",
                "area_sparse",
                "milestone_completed",
                "quest_created",
                "world_event_available",
                "world_event_active",
                "world_event_resolved",
                "rest_completed",
            },
        ),
        "pacing_controller": (
            PacingControllerSubSystem(),
            {
                "tick_settlement",
                "stagnation_threshold_reached",
                "milestone_completed",
                "milestone_failed",
                "quest_accepted",
                "quest_completed",
                "quest_expired",
                "combat_resolved",
                "rest_completed",
            },
        ),
    }

    all_kinds = {
        "bootstrap",
        "milestone_available",
        "milestone_activated",
        "milestone_completed",
        "milestone_failed",
        "quest_accepted",
        "quest_created",
        "quest_updated",
        "quest_objective_completed",
        "quest_completed",
        "quest_retired",
        "quest_expired",
        "area_entered",
        "sub_location_entered",
        "sub_location_left",
        "scene_changed",
        "area_sparse",
        "shop_refreshed",
        "shop_inventory_changed",
        "relationship_stage_changed",
        "world_event_available",
        "world_event_active",
        "world_event_resolved",
        "combat_resolved",
        "rest_completed",
        "stagnation_threshold_reached",
        "tick_settlement",
    }
    for name, (subsystem, accepted_kinds) in subscriptions.items():
        for kind in all_kinds:
            assert subsystem.accepts_event(PlannerEvent(kind=kind, tick=1)) is (kind in accepted_kinds), (
                f"{name} subscription mismatch for {kind}"
            )


def test_no_legacy_subsystem_in_module():
    """LegacyDirectiveSubSystem should no longer exist."""
    import importlib, sys
    assert "app.game_core.planning.legacy_subsystem" not in sys.modules
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.game_core.planning.legacy_subsystem")
