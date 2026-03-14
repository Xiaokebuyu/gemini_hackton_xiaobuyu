"""Tests for R3: Planner visibility improvements.

R3-A: success_conditions in _build_target_milestone_detail
R3-B: success_conditions serialization fix in MilestoneOutlineGenerator._build_user_message
R3-C: Planner feedback richness (_build_planner_feedback + _format_planner_context rendering)
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.content.registries.quests import (
    MilestoneCondition,
    MilestoneTemplate,
    QuestRegistry,
)
from app.game_core.orchestration.hooks.narrative_planner import NarrativePlannerHook
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.rules.defaults import register_default_rules_handlers
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    AreaSlice,
    FlagSlice,
    NarrativePlanSlice,
    PlayerSlice,
    QuestSlice,
    SceneSlice,
    TimeSlice,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_world_with_milestone(
    milestone_id: str = "ms_test",
    success_conditions: list[dict[str, Any]] | None = None,
) -> WorldInstance:
    world = WorldInstance("test_world")
    registry = QuestRegistry()
    raw_conds = success_conditions or []
    registry.load({
        "milestones": {
            milestone_id: {
                "id": milestone_id,
                "title": "Test Milestone",
                "description": "A test milestone",
                "narrative_context": "Some narrative",
                "key_elements": ["elem1", "elem2"],
                "involved_npcs": ["npc_a"],
                "involved_locations": ["loc_x"],
                "success_conditions": raw_conds,
            }
        },
        "chapters": [],
        "initial_events": [],
    })
    world.register(registry)
    return world


def _make_context(
    world: WorldInstance,
    *,
    target_milestone: str = "ms_test",
    flags: dict[str, Any] | None = None,
    npc_directives: list[dict[str, Any]] | None = None,
    area_interactable_states: dict[str, dict[str, Any]] | None = None,
    dynamic_quests: dict[str, Any] | None = None,
) -> SettlementContext:
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 9})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": "area_1", "current_location": None})
    state.register(player)

    quests = QuestSlice()
    quests.restore({
        "milestone_states": {target_milestone: {"state": "ACTIVE"}},
        "dynamic_quests": dynamic_quests or {},
        "chapter_completion": {},
    })
    state.register(quests)

    np_slice = NarrativePlanSlice()
    np_slice.restore({
        "current_chapter": "ch_1",
        "current_target_milestone": target_milestone,
        "npc_directives": npc_directives or [],
    })
    state.register(np_slice)

    areas = AreaSlice()
    raw_area: dict[str, Any] = {}
    if area_interactable_states:
        raw_area["interactable_states"] = area_interactable_states
    areas.restore({"areas": {"area_1": raw_area}})
    state.register(areas)

    flag_slice = FlagSlice()
    flag_slice.restore({"flags": flags or {}})
    state.register(flag_slice)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    active_change_log: list[StateChange] = [
        StateChange(slice="player", operation="set", path="current_area", value="area_1"),
    ]

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        active_change_log.extend(delta.changes)

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


# ---------------------------------------------------------------------------
# R3-A: success_conditions in _build_target_milestone_detail
# ---------------------------------------------------------------------------

def test_target_milestone_detail_includes_success_conditions() -> None:
    """R3-A: _build_target_milestone_detail must include serialized success_conditions."""
    world = _make_world_with_milestone(
        milestone_id="ms_test",
        success_conditions=[
            {"type": "npc_talked", "params": {"npc_id": "guild_girl"}},
            {"type": "location_visited", "params": {"area_id": "frontier_town"}, "optional": True},
        ],
    )
    context = _make_context(world)

    hook = NarrativePlannerHook()
    detail = hook._build_target_milestone_detail(context)

    assert "success_conditions" in detail
    conditions = detail["success_conditions"]
    assert isinstance(conditions, list)
    assert len(conditions) == 2

    # First condition: npc_talked
    c0 = conditions[0]
    assert c0["type"] == "npc_talked"
    assert c0["params"] == {"npc_id": "guild_girl"}

    # Second condition: location_visited + optional
    c1 = conditions[1]
    assert c1["type"] == "location_visited"
    assert c1["params"] == {"area_id": "frontier_town"}
    assert c1.get("optional") is True


def test_target_milestone_detail_empty_success_conditions() -> None:
    """R3-A: Empty success_conditions list is returned as empty list."""
    world = _make_world_with_milestone(milestone_id="ms_empty", success_conditions=[])
    context = _make_context(world, target_milestone="ms_empty")

    hook = NarrativePlannerHook()
    detail = hook._build_target_milestone_detail(context)

    assert "success_conditions" in detail
    assert detail["success_conditions"] == []


def test_target_milestone_detail_with_dataclass_conditions() -> None:
    """R3-A: MilestoneCondition dataclasses are correctly serialized to dicts."""
    world = WorldInstance("test_world")
    registry = QuestRegistry()
    # Build a MilestoneTemplate directly with dataclass conditions
    template = MilestoneTemplate(
        id="ms_dc",
        title="Dataclass test",
        success_conditions=[
            MilestoneCondition(type="flag_set", params={"key": "goblin_killed"}, optional=False),
            MilestoneCondition(type="kill_count", params={"monster_id": "goblin", "count": 3}, optional=True),
        ],
    )
    # Inject via internal dict (test only)
    registry._milestones["ms_dc"] = template
    world.register(registry)

    context = _make_context(world, target_milestone="ms_dc")
    hook = NarrativePlannerHook()
    detail = hook._build_target_milestone_detail(context)

    conditions = detail["success_conditions"]
    assert len(conditions) == 2
    assert conditions[0]["type"] == "flag_set"
    assert conditions[0]["params"] == {"key": "goblin_killed"}
    assert conditions[0].get("optional") is None or conditions[0].get("optional") is False
    assert conditions[1]["type"] == "kill_count"
    assert conditions[1].get("optional") is True


# ---------------------------------------------------------------------------
# R3-B: success_conditions serialization in MilestoneOutlineGenerator
# ---------------------------------------------------------------------------

def test_outline_generator_formats_conditions_as_dicts() -> None:
    """R3-B: _build_user_message must render conditions as structured text (type=/params=),
    not Python repr strings like MilestoneCondition(type=...).
    """
    from app.narrators import MilestoneOutlineGenerator

    gen = MilestoneOutlineGenerator(llm=None)
    milestone_template = {
        "narrative_context": "Some context",
        "key_elements": ["Investigate the town"],
        "success_conditions": [
            {"type": "npc_talked", "params": {"npc_id": "guild_girl"}},
            {"type": "location_visited", "params": {"area_id": "frontier"}},
        ],
        "involved_npcs": ["guild_girl"],
        "involved_locations": ["frontier"],
    }

    msg = gen._build_user_message(
        milestone_template=milestone_template,
        target_milestone_id="ms_test",
        chapter_id="ch_1",
        current_tick=42,
        game_state_summary={},
        supported_condition_types=["npc_talked", "location_visited", "flag_set"],
    )

    # Must not contain MilestoneCondition repr
    assert "MilestoneCondition(" not in msg

    # Must contain structured type= lines
    assert "type=npc_talked" in msg
    assert "type=location_visited" in msg
    assert "npc_id" in msg
    assert "frontier" in msg


def test_outline_generator_formats_dataclass_conditions() -> None:
    """R3-B: MilestoneCondition dataclasses in milestone_template are also rendered properly."""
    from app.narrators import MilestoneOutlineGenerator

    gen = MilestoneOutlineGenerator(llm=None)
    milestone_template = {
        "narrative_context": "context",
        "key_elements": [],
        "success_conditions": [
            MilestoneCondition(type="flag_set", params={"key": "my_flag"}, optional=False),
        ],
        "involved_npcs": [],
        "involved_locations": [],
    }

    msg = gen._build_user_message(
        milestone_template=milestone_template,
        target_milestone_id="ms_dc",
        chapter_id="ch_1",
        current_tick=1,
        game_state_summary={},
        supported_condition_types=[],
    )

    # Must not contain raw repr
    assert "MilestoneCondition(" not in msg
    # Must contain structured output
    assert "type=flag_set" in msg
    assert "my_flag" in msg


# ---------------------------------------------------------------------------
# R3-C: Planner feedback — quest progress, milestone satisfaction, directives, clues
# ---------------------------------------------------------------------------

def test_planner_context_includes_quest_progress() -> None:
    """R3-C: Active quests with objectives appear in planner_feedback.quest_progress."""
    world = _make_world_with_milestone(
        success_conditions=[{"type": "flag_set", "params": {"key": "done"}}],
    )
    dynamic_quests = {
        "dq_1": {
            "status": "active",
            "title": "Find the goblin",
            "objectives": [
                {"description": "Talk to guild girl", "completed": True},
                {"description": "Visit the site", "completed": False},
            ],
        }
    }
    context = _make_context(world, dynamic_quests=dynamic_quests)

    hook = NarrativePlannerHook()
    planner_ctx = hook._build_planner_context(context, current_tick=10)

    feedback = planner_ctx.get("planner_feedback")
    assert isinstance(feedback, dict)
    quest_progress = feedback.get("quest_progress", [])
    assert len(quest_progress) == 1

    qp = quest_progress[0]
    assert qp["quest_id"] == "dq_1"
    assert qp["completed_objectives"] == 1
    assert qp["total_objectives"] == 2
    assert qp["ratio"] == 0.5


def test_planner_context_includes_milestone_satisfaction() -> None:
    """R3-C: Milestone conditions are evaluated and reported in planner_feedback."""
    world = _make_world_with_milestone(
        success_conditions=[
            {"type": "flag_set", "params": {"key": "arrived"}},
            {"type": "npc_talked", "params": {"npc_id": "guild_girl"}},
        ],
    )
    # Set the "arrived" flag but not talked_to_guild_girl
    context = _make_context(
        world,
        flags={"arrived": True},
    )

    hook = NarrativePlannerHook()
    planner_ctx = hook._build_planner_context(context, current_tick=5)

    feedback = planner_ctx.get("planner_feedback")
    satisfaction = feedback.get("milestone_satisfaction", [])
    assert len(satisfaction) == 2

    # flag_set should be met
    flag_cond = next(c for c in satisfaction if c["type"] == "flag_set")
    assert flag_cond["met"] is True

    # npc_talked should not be met (no flag set)
    npc_cond = next(c for c in satisfaction if c["type"] == "npc_talked")
    assert npc_cond["met"] is False


def test_planner_context_milestone_satisfaction_empty_when_no_target() -> None:
    """R3-C: When there is no target milestone, milestone_satisfaction is empty."""
    world = WorldInstance("test_world")
    state = StateContainer()
    np_slice = NarrativePlanSlice()
    np_slice.restore({"current_target_milestone": None})
    state.register(np_slice)
    quests = QuestSlice()
    quests.restore({"milestone_states": {}, "dynamic_quests": {}, "chapter_completion": {}})
    state.register(quests)
    flag_slice = FlagSlice()
    flag_slice.restore({})
    state.register(flag_slice)
    player = PlayerSlice()
    player.restore({"current_area": "area_1"})
    state.register(player)
    areas = AreaSlice()
    areas.restore({"areas": {"area_1": {}}})
    state.register(areas)
    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 1})
    state.register(time_slice)
    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    def _apply_delta(delta):
        if delta:
            state.apply(delta)

    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)

    context = SettlementContext(
        change_log=[],
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=rules_engine,
        _apply_delta=_apply_delta,
    )

    hook = NarrativePlannerHook()
    planner_ctx = hook._build_planner_context(context, current_tick=1)
    feedback = planner_ctx.get("planner_feedback", {})
    assert feedback.get("milestone_satisfaction", []) == []


def test_planner_context_npc_directive_confirmation() -> None:
    """R3-C: NPC directive talk confirmations are tracked via talked_to_ flags."""
    world = _make_world_with_milestone()
    npc_directives = [
        {
            "npc_id": "guild_girl",
            "directive": {"kind": "talk"},
            "priority": "high",
            "issued_at_tick": 3,
            "consumed": False,
            "expires_at_tick": 100,
        }
    ]
    # guild_girl has been talked to
    context = _make_context(
        world,
        npc_directives=npc_directives,
        flags={"talked_to_guild_girl": True},
    )

    hook = NarrativePlannerHook()
    planner_ctx = hook._build_planner_context(context, current_tick=10)

    feedback = planner_ctx.get("planner_feedback", {})
    confirmations = feedback.get("npc_directive_confirmations", [])
    assert len(confirmations) == 1
    assert confirmations[0]["npc_id"] == "guild_girl"
    assert confirmations[0]["talked_flag_set"] is True


def test_planner_context_npc_directive_unconfirmed() -> None:
    """R3-C: NPC directive without corresponding flag is reported as unconfirmed."""
    world = _make_world_with_milestone()
    npc_directives = [
        {
            "npc_id": "goblin_slayer",
            "directive": {"kind": "talk"},
            "priority": "medium",
            "issued_at_tick": 5,
            "consumed": False,
            "expires_at_tick": 100,
        }
    ]
    # No talked_to flag
    context = _make_context(world, npc_directives=npc_directives)

    hook = NarrativePlannerHook()
    planner_ctx = hook._build_planner_context(context, current_tick=10)

    feedback = planner_ctx.get("planner_feedback", {})
    confirmations = feedback.get("npc_directive_confirmations", [])
    assert len(confirmations) == 1
    assert confirmations[0]["talked_flag_set"] is False


def test_planner_context_clue_interaction_summary() -> None:
    """R3-C: Clue interactable states are summarized (discovered/examined/resolved/unexamined)."""
    world = _make_world_with_milestone()
    interactable_states = {
        "clue_a": {
            "functional_type": "investigate_clue",
            "area_id": "area_1",
            "first_inspected": True,
            "resolved_option_id": "option_1",
        },
        "clue_b": {
            "functional_type": "investigate_clue",
            "area_id": "area_1",
            "first_inspected": True,
        },
        "clue_c": {
            "functional_type": "investigate_clue",
            "area_id": "area_1",
        },
        # Non-clue interactable — should not be counted
        "chest": {
            "area_id": "area_1",
            "used": True,
        },
    }
    context = _make_context(world, area_interactable_states=interactable_states)

    hook = NarrativePlannerHook()
    planner_ctx = hook._build_planner_context(context, current_tick=7)

    feedback = planner_ctx.get("planner_feedback", {})
    clue_summary = feedback.get("clue_interaction_summary", {})
    assert clue_summary.get("discovered") == 3
    assert clue_summary.get("resolved") == 1
    assert clue_summary.get("examined") == 1
    assert clue_summary.get("unexamined") == 1


def test_format_planner_context_renders_feedback() -> None:
    """R3-C: _format_planner_context in narrators.py renders planner_feedback sections."""
    from app.narrators import _format_planner_context

    ctx: dict[str, Any] = {
        "current_tick": 10,
        "time": {"day": 1, "slot": 2, "period": "morning"},
        "location": {"area_id": "area_1", "location_id": None},
        "narrative_plan": {
            "current_chapter": "ch_1",
            "current_target_milestone": "ms_test",
            "escalation_level": 1,
            "ticks_since_milestone_progress": 3,
            "chapter_completion": 0.2,
            "strategy_notes": "",
            "pacing_frozen": False,
            "behavior_window": [],
            "npc_directives": [],
            "last_run_tick": 0,
            "next_scheduled_tick": 0,
        },
        "quests": {
            "available_milestones": [],
            "active_milestones": ["ms_test"],
            "completed_milestones": [],
            "dynamic_quests": {},
            "completed_dynamic_quests": [],
            "report_ready_dynamic_quests": [],
        },
        "target_milestone_detail": {
            "key_elements": ["elem1"],
            "involved_npcs": [],
            "involved_locations": [],
            "narrative_context": "Test context",
            "failure_fallback": None,
            "success_conditions": [
                {"type": "npc_talked", "params": {"npc_id": "guild_girl"}},
            ],
        },
        "planner_feedback": {
            "quest_progress": [
                {"quest_id": "dq_1", "title": "Find goblin", "completed_objectives": 1, "total_objectives": 2, "ratio": 0.5},
            ],
            "milestone_satisfaction": [
                {"type": "npc_talked", "params": {"npc_id": "guild_girl"}, "met": False, "optional": False},
            ],
            "npc_directive_confirmations": [
                {"npc_id": "guild_girl", "directive_kind": "talk", "issued_at_tick": 3, "talked_flag_set": True},
            ],
            "clue_interaction_summary": {
                "discovered": 2,
                "examined": 1,
                "resolved": 1,
                "unexamined": 0,
            },
        },
        "story_facts": [],
        "previous_directive_results": [],
        "area_npcs": [],
        "area_npc_summaries": [],
        "existing_npc_ids": [],
        "area_boards": [],
        "all_area_ids": [],
        "current_sub_area_ids": [],
        "party": [],
        "play_style_tags": [],
        "danger_level": 0.0,
        "changed_slices": [],
        "change_count": 0,
        "recent_changes": [],
        "scene": {"entry_count": 0, "state_change_count": 0, "system_entries_digest": [], "visible_command_types": []},
        "events": {"pending_events_digest": []},
        "world_context": {},
        "player_level": 1,
        "player_xp": 0,
    }

    rendered = _format_planner_context(ctx)

    # success_conditions from target_detail
    assert "type=npc_talked" in rendered
    assert "guild_girl" in rendered

    # quest progress
    assert "任务目标进度" in rendered
    assert "Find goblin" in rendered
    assert "1/2" in rendered

    # milestone satisfaction
    assert "里程碑条件满足度" in rendered
    assert "✗" in rendered  # npc_talked not met

    # NPC directive confirmation
    assert "NPC指令执行确认" in rendered
    assert "已确认对话" in rendered

    # clue summary
    assert "线索交互状态" in rendered
    assert "discovered=2" in rendered
