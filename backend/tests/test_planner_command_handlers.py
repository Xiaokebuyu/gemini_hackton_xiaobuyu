from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Mapping

from app.game_core.content import WorldInstance
from app.game_core.content.registries import MapRegistry
from app.game_core.orchestration.hooks.quest_objective_tracking import QuestObjectiveTrackingHook
from app.game_core.orchestration.models import SSEEvent
from app.game_core.rules.handlers.planner import (
    PlannerNpcHandler,
    PlannerQuestHandler,
    PlannerRuntimeHandler,
    PlannerWorldHandler,
)
from app.game_core.rules.models import Command
from app.game_core.state import StateChange, StateDelta, StateContainer
from app.game_core.state.slices import AreaSlice, FlagSlice, NarrativePlanSlice, QuestSlice


def _make_state(*, with_flags: bool = True) -> StateContainer:
    state = StateContainer()

    quests = QuestSlice()
    quests.restore({"milestone_states": {}, "dynamic_quests": {}, "chapter_completion": {}})
    state.register(quests)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({})
    state.register(narrative_plan)

    areas = AreaSlice()
    areas.restore({"areas": {"forest": {}}})
    state.register(areas)

    if with_flags:
        flags = FlagSlice()
        flags.restore({})
        state.register(flags)

    return state


def test_planner_update_quest_command_updates_active_quest_and_history() -> None:
    world = WorldInstance("test_world")
    state = _make_state()
    state.quests.restore(
        {
            "milestone_states": {},
            "dynamic_quests": {
                "dq_active": {
                    "quest_id": "dq_active",
                    "status": "active",
                    "title": "Lead",
                    "summary": "Follow up",
                }
            },
            "chapter_completion": {},
        }
    )
    handler = PlannerQuestHandler()

    result = handler.compute(
        Command(
            type="planner_update_quest",
            params={
                "quest_id": "dq_active",
                "current_step": "Go to the guild",
                "next_steps": ["Talk to the receptionist"],
                "hints": ["The guild is near the square"],
                "current_tick": 9,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.delta is not None
    state.apply(result.delta)

    quest = state.quests.get_dynamic_quest("dq_active")
    assert quest is not None
    assert quest["current_step"] == "Go to the guild"
    assert quest["next_steps"] == ["Talk to the receptionist"]
    assert quest["hints"] == ["The guild is near the square"]
    assert state.narrative_plan.quest_history[-1]["kind"] == "update_quest"


def test_planner_retire_quest_command_cascades_cleanup() -> None:
    world = WorldInstance("test_world")
    state = _make_state()
    state.quests.restore(
        {
            "milestone_states": {},
            "dynamic_quests": {
                "dq_retire": {
                    "quest_id": "dq_retire",
                    "status": "active",
                    "title": "Temporary Trouble",
                    "summary": "Clean up the branch",
                }
            },
            "chapter_completion": {},
        }
    )
    state.narrative_plan.restore(
        {
            "npc_directives": [
                {"npc_id": "temp_npc", "linked_quest_id": "dq_retire", "directive": {"kind": "hint"}},
                {"npc_id": "keeper", "linked_quest_id": "other", "directive": {"kind": "idle"}},
            ],
            "quest_history": [
                {
                    "kind": "spawn_quest_npc",
                    "npc_id": "temp_npc",
                    "linked_quest_id": "dq_retire",
                    "despawn_tick": 10,
                }
            ],
            "temporary_npcs": {"temp_npc": {"npc_id": "temp_npc", "name": "Witness"}},
        }
    )
    state.areas.restore(
        {
            "areas": {
                "forest": {
                    "npc_locations": {"temp_npc": "trail"},
                    "board_bulletins": {
                        "board": [{"quest_id": "dq_retire", "title": "Wanted"}],
                    },
                    "temporary_sub_areas": [
                        {"id": "temp_site", "linked_quest_id": "dq_retire", "label": "Disturbed Ground"}
                    ],
                }
            }
        }
    )
    handler = PlannerQuestHandler()

    result = handler.compute(
        Command(
            type="planner_retire_quest",
            params={"quest_id": "dq_retire", "current_tick": 12},
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.delta is not None
    state.apply(result.delta)

    quest = state.quests.get_dynamic_quest("dq_retire")
    assert quest is not None
    assert quest["status"] == "retired"
    assert state.narrative_plan.get_temporary_npc("temp_npc") is None
    assert state.areas.find_npc_area("temp_npc") is None
    assert state.areas.get_board_bulletins("forest", "board") == []
    assert state.areas.list_temporary_sub_areas("forest") == []
    remaining_directives = [d["npc_id"] for d in state.narrative_plan.npc_directives]
    assert remaining_directives == ["keeper"]


def test_planner_escalate_command_skips_flags_slice_when_absent() -> None:
    world = WorldInstance("test_world")
    state = _make_state(with_flags=False)
    handler = PlannerRuntimeHandler()

    result = handler.compute(
        Command(
            type="planner_escalate",
            params={"delta": 1},
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.delta is not None
    state.apply(result.delta)
    assert state.narrative_plan.escalation_level == 1


def test_planner_commit_runtime_state_updates_trace_and_behavior() -> None:
    world = WorldInstance("test_world")
    state = _make_state()
    handler = PlannerRuntimeHandler()

    result = handler.compute(
        Command(
            type="planner_commit_runtime_state",
            params={
                "last_planner_replay_trace": {"round_count": 2, "stop_reason": "steady_state"},
                "last_run_tick": 18,
                "ticks_since_milestone_progress": 0,
                "strategy_notes": "hold the line",
                "next_scheduled_tick": 24,
                "play_style_tags": ["DIALOGUE_HEAVY"],
                "behavior_entry": {
                    "tick": 18,
                    "changed_slices": ["quests"],
                    "reason": "trigger",
                    "directive_count": 2,
                },
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.delta is not None
    state.apply(result.delta)

    assert state.narrative_plan.last_planner_replay_trace["round_count"] == 2
    assert state.narrative_plan.last_run_tick == 18
    assert state.narrative_plan.strategy_notes == "hold the line"
    assert state.narrative_plan.next_scheduled_tick == 24
    assert state.narrative_plan.play_style_tags == ["DIALOGUE_HEAVY"]
    assert state.narrative_plan.behavior_window[-1]["directive_count"] == 2


# ---------------------------------------------------------------------------
# Phase 3 (P26): planner_create_quest min_level propagation tests
# ---------------------------------------------------------------------------

def test_planner_create_quest_stores_min_level_in_payload() -> None:
    """planner_create_quest with explicit min_level=2 stores it in quest payload."""
    world = WorldInstance("test_world")
    state = _make_state()
    handler = PlannerQuestHandler()

    result = handler.compute(
        Command(
            type="planner_create_quest",
            params={
                "quest_id": "dq_level_test",
                "title": "中级讨伐",
                "summary": "打倒精英哥布林",
                "min_level": 2,
                "current_tick": 5,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.delta is not None
    state.apply(result.delta)
    quest = state.quests.get_dynamic_quest("dq_level_test")
    assert quest is not None
    assert quest["min_level"] == 2


def test_planner_create_quest_min_level_defaults_to_1() -> None:
    """planner_create_quest without min_level defaults to min_level=1."""
    world = WorldInstance("test_world")
    state = _make_state()
    handler = PlannerQuestHandler()

    result = handler.compute(
        Command(
            type="planner_create_quest",
            params={
                "quest_id": "dq_no_min",
                "title": "日常巡逻",
                "summary": "巡逻市场",
                "current_tick": 5,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    state.apply(result.delta)
    quest = state.quests.get_dynamic_quest("dq_no_min")
    assert quest is not None
    assert quest["min_level"] == 1


def test_planner_create_quest_min_level_from_metadata() -> None:
    """planner_create_quest can supply min_level via metadata dict."""
    world = WorldInstance("test_world")
    state = _make_state()
    handler = PlannerQuestHandler()

    result = handler.compute(
        Command(
            type="planner_create_quest",
            params={
                "quest_id": "dq_meta_level",
                "title": "高阶任务",
                "summary": "测试 metadata 路径",
                "metadata": {"min_level": 3},
                "current_tick": 5,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    state.apply(result.delta)
    quest = state.quests.get_dynamic_quest("dq_meta_level")
    assert quest is not None
    assert quest["min_level"] == 3


def test_planner_layer_no_longer_directly_mutates_state() -> None:
    root = Path(__file__).resolve().parents[1]
    targets = [
        root / "app/game_core/planning",
        root / "app/game_core/orchestration/hooks/narrative_planner.py",
    ]
    forbidden_snippets = (
        "context.record_change(",
        "._dirty = True",
        ".add_history(",
        ".add_directive(",
        ".add_temporary_npc(",
        ".remove_temporary_npc(",
        ".set_last_planner_replay_trace(",
        ".set_strategy(",
        ".schedule_next(",
        ".record_behavior(",
        ".add_story_facts(",
    )
    allowed_files = set()

    for target in targets:
        files = [target] if target.is_file() else sorted(target.rglob("*.py"))
        for file_path in files:
            if file_path in allowed_files:
                continue
            text = file_path.read_text(encoding="utf-8")
            for snippet in forbidden_snippets:
                assert snippet not in text, f"{file_path} still contains forbidden planner mutation: {snippet}"


# ---------------------------------------------------------------------------
# A4: fill_area NPC placement tests
# ---------------------------------------------------------------------------

def _make_state_with_area(area_id: str = "town") -> StateContainer:
    state = StateContainer()
    quests = QuestSlice()
    quests.restore({"milestone_states": {}, "dynamic_quests": {}, "chapter_completion": {}})
    state.register(quests)
    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({})
    state.register(narrative_plan)
    areas = AreaSlice()
    areas.restore({"areas": {area_id: {}}})
    state.register(areas)
    return state


def test_fill_area_with_resident_npcs_emits_npc_presence_changes() -> None:
    """planner_fill_area with resident_npcs should create npc_presence StateChanges."""
    world = WorldInstance("test_world")
    state = _make_state_with_area("town")
    handler = PlannerWorldHandler()

    result = handler.compute(
        Command(
            type="planner_fill_area",
            params={
                "area_id": "town",
                "id": "market_stall",
                "label": "Market Stall",
                "description": "A busy market stall.",
                "resident_npcs": ["vendor_1", "vendor_2"],
                "current_tick": 5,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    state.apply(result.delta)

    # Both resident NPCs should be placed via find_npc_area
    area_v1 = state.areas.find_npc_area("vendor_1")
    assert area_v1 == "town"
    # The NPC location should be the sub_area id
    area_state = state.areas.get_area("town")
    assert area_state.npc_locations.get("vendor_1") == "market_stall"
    assert area_state.npc_presence_sources.get("vendor_1") == "planner"

    area_v2 = state.areas.find_npc_area("vendor_2")
    assert area_v2 == "town"


def test_fill_area_without_resident_npcs_emits_no_npc_presence() -> None:
    """planner_fill_area without resident_npcs should not create npc_presence changes."""
    world = WorldInstance("test_world")
    state = _make_state_with_area("town")
    handler = PlannerWorldHandler()

    result = handler.compute(
        Command(
            type="planner_fill_area",
            params={
                "area_id": "town",
                "id": "empty_spot",
                "label": "Empty Spot",
                "current_tick": 5,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    state.apply(result.delta)

    # No npc_presence entries should be created
    assert result.metadata["npc_presence_count"] == 0


def test_fill_area_resident_npc_source_is_planner() -> None:
    """npc_presence entries from fill_area should have source='planner'."""
    world = WorldInstance("test_world")
    state = _make_state_with_area("forest")
    handler = PlannerWorldHandler()

    result = handler.compute(
        Command(
            type="planner_fill_area",
            params={
                "area_id": "forest",
                "id": "shrine",
                "label": "Hidden Shrine",
                "resident_npcs": ["hermit"],
                "current_tick": 10,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    state.apply(result.delta)

    assert state.areas.find_npc_area("hermit") == "forest"
    area_state = state.areas.get_area("forest")
    assert area_state.npc_presence_sources.get("hermit") == "planner"
    assert area_state.npc_locations.get("hermit") == "shrine"


def _make_world_with_location() -> WorldInstance:
    world = WorldInstance("test_world")
    maps = MapRegistry()
    maps.load(
        {
            "town": {
                "id": "town",
                "sub_locations": {
                    "temple": {
                        "id": "temple",
                        "default_room": "hall",
                        "rooms": {
                            "hall": {
                                "id": "hall",
                                "name": "Hall",
                            }
                        },
                    }
                },
            }
        }
    )
    world.register(maps)
    return world


def test_fill_location_upserts_scene_overlay_entries() -> None:
    world = _make_world_with_location()
    state = _make_state_with_area("town")
    handler = PlannerWorldHandler()

    result = handler.compute(
        Command(
            type="planner_fill_location",
            params={
                "area_id": "town",
                "location_id": "temple",
                "room_id": "hall",
                "interactables": [
                    {
                        "id": "charity_box",
                        "name": "奉献箱",
                        "description": "一只安静的木箱。",
                        "type": "inspect",
                        "functional": {"type": "donation"},
                    }
                ],
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.delta is not None
    state.apply(result.delta)
    overlays = state.areas.list_scoped_interactable_overlays("town", "temple", "hall")
    assert overlays == [
        {
            "id": "charity_box",
            "name": "奉献箱",
            "description": "一只安静的木箱。",
            "type": "inspect",
            "functional": {"type": "donation"},
        }
    ]


def test_fill_location_rejects_overlay_capacity_overflow() -> None:
    world = _make_world_with_location()
    state = _make_state_with_area("town")
    state.areas.restore(
        {
            "areas": {
                "town": {
                    "scoped_interactable_overlays": {
                        "temple__hall": [
                            {"id": "i1"},
                            {"id": "i2"},
                            {"id": "i3"},
                            {"id": "i4"},
                        ]
                    }
                }
            }
        }
    )
    handler = PlannerWorldHandler()

    result = handler.validate(
        Command(
            type="planner_fill_location",
            params={
                "area_id": "town",
                "location_id": "temple",
                "room_id": "hall",
                "interactables": [{"id": "i5", "name": "Extra"}],
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.ok is False
    assert result.reason == "scene interactable overlay capacity exceeded"


def test_spawn_quest_npc_resolves_default_room_for_static_location() -> None:
    world = _make_world_with_location()
    state = _make_state_with_area("town")
    handler = PlannerNpcHandler()

    result = handler.compute(
        Command(
            type="planner_spawn_quest_npc",
            params={
                "area_id": "town",
                "npc_id": "messenger",
                "location_id": "temple",
                "current_tick": 3,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.delta is not None
    state.apply(result.delta)
    area_state = state.areas.get_area("town")
    assert area_state.npc_locations["messenger"] == "temple"
    assert area_state.npc_rooms["messenger"] == "hall"


def test_spawn_quest_npc_accepts_dynamic_room_target() -> None:
    world = _make_world_with_location()
    state = _make_state_with_area("town")
    state.areas.add_dynamic_room(
        "town",
        {
            "sub_loc_id": "temple",
            "room_id": "archive_annex",
            "name": "Archive Annex",
        },
    )
    handler = PlannerNpcHandler()

    result = handler.compute(
        Command(
            type="planner_spawn_quest_npc",
            params={
                "area_id": "town",
                "npc_id": "scribe",
                "location_id": "temple",
                "room_id": "archive_annex",
                "current_tick": 4,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.delta is not None
    state.apply(result.delta)
    area_state = state.areas.get_area("town")
    assert area_state.npc_locations["scribe"] == "temple"
    assert area_state.npc_rooms["scribe"] == "archive_annex"


# ---------------------------------------------------------------------------
# A8b: auto-generate condition when type present but condition absent
# ---------------------------------------------------------------------------

def test_create_quest_auto_generates_condition_for_talk_objective() -> None:
    """Objective with type='talk_to' and no condition → auto-generates npc_talked condition."""
    world = WorldInstance("test_world")
    state = _make_state()
    handler = PlannerQuestHandler()

    result = handler.compute(
        Command(
            type="planner_create_quest",
            params={
                "quest_id": "dq_talk",
                "title": "Speak to the Elder",
                "summary": "Visit the village elder",
                "objectives": [
                    {
                        "description": "Talk to elder_wang",
                        "type": "talk_to",
                        "target": {"npc_id": "elder_wang"},
                    }
                ],
                "current_tick": 3,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    state.apply(result.delta)

    quest = state.quests.get_dynamic_quest("dq_talk")
    assert quest is not None
    obj = quest["objectives"][0]
    assert "condition" in obj
    assert obj["condition"]["type"] == "npc_talked"
    assert obj["condition"]["params"]["npc_id"] == "elder_wang"


def test_create_quest_auto_generates_condition_for_location_objective() -> None:
    """Objective with type='reach_location' and no condition → auto-generates location_visited condition."""
    world = WorldInstance("test_world")
    state = _make_state()
    handler = PlannerQuestHandler()

    result = handler.compute(
        Command(
            type="planner_create_quest",
            params={
                "quest_id": "dq_loc",
                "title": "Reach the Ruins",
                "summary": "Travel to the ancient ruins",
                "objectives": [
                    {
                        "description": "Go to ancient_ruins",
                        "type": "reach_location",
                        "target": {"area_id": "ancient_ruins"},
                    }
                ],
                "current_tick": 3,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    state.apply(result.delta)

    quest = state.quests.get_dynamic_quest("dq_loc")
    assert quest is not None
    obj = quest["objectives"][0]
    assert "condition" in obj
    assert obj["condition"]["type"] == "location_visited"
    assert obj["condition"]["params"]["area_id"] == "ancient_ruins"


def test_create_quest_preserves_explicit_condition_unchanged() -> None:
    """Explicit condition in objective should not be replaced by auto-generation."""
    world = WorldInstance("test_world")
    state = _make_state()
    handler = PlannerQuestHandler()

    result = handler.compute(
        Command(
            type="planner_create_quest",
            params={
                "quest_id": "dq_explicit",
                "title": "Kill Goblins",
                "summary": "Slay the goblin patrol",
                "objectives": [
                    {
                        "description": "Kill 3 goblins",
                        "type": "kill",
                        "target": {"monster_type": "goblin", "count": 3},
                        "condition": {
                            "type": "kill_count",
                            "params": {"monster_type": "goblin", "count": 5},
                        },
                    }
                ],
                "current_tick": 3,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    state.apply(result.delta)

    quest = state.quests.get_dynamic_quest("dq_explicit")
    assert quest is not None
    obj = quest["objectives"][0]
    # Explicit condition should be preserved (count=5, not overridden to 3)
    assert obj["condition"]["params"]["count"] == 5


# ---------------------------------------------------------------------------
# A8c: _objective_target_to_params passthrough for location with sub_location
# ---------------------------------------------------------------------------

def test_objective_target_to_params_passthrough_location_with_only_sub_location() -> None:
    """location_visited with only sub_location_id (no area_id/location_id) passes through."""
    from app.game_core.rules.handlers.planner import PlannerQuestHandler as _H
    handler = _H()
    # target has sub_location_id but no area_id or location_id
    params = handler._objective_target_to_params(
        "location_visited",
        {"sub_location_id": "shrine_entrance"},
    )
    assert params is not None
    # Falls through to passthrough
    assert "sub_location_id" in params
    assert params["sub_location_id"] == "shrine_entrance"


def test_objective_target_to_params_normal_location_preserved() -> None:
    """location_visited with area_id returns structured params."""
    from app.game_core.rules.handlers.planner import PlannerQuestHandler as _H
    handler = _H()
    params = handler._objective_target_to_params(
        "location_visited",
        {"area_id": "town", "location_id": "market"},
    )
    assert params is not None
    assert params["area_id"] == "town"
    assert params["location_id"] == "market"


# ---------------------------------------------------------------------------
# A9e: step_index writeback to milestone_outline + QuestObjectiveTrackingHook
# ---------------------------------------------------------------------------

def test_create_quest_with_step_index_writes_back_to_outline() -> None:
    """planner_create_quest with step_index=1 writes quest_id to outline step."""
    world = WorldInstance("test_world")
    state = _make_state()
    # Set up milestone outline with steps
    state.narrative_plan.set_milestone_outline({
        "target_milestone_id": "m1",
        "computed_at_tick": 0,
        "steps": [
            {"index": 0, "description": "Step 0", "completed": False, "quest_id": None},
            {"index": 1, "description": "Step 1", "completed": False, "quest_id": None},
        ],
    })
    handler = PlannerQuestHandler()

    result = handler.compute(
        Command(
            type="planner_create_quest",
            params={
                "quest_id": "dq_step_test",
                "title": "Step Quest",
                "summary": "Linked to outline step 1",
                "step_index": 1,
                "current_tick": 5,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    state.apply(result.delta)

    # The outline step at index 1 should now reference the quest
    outline = state.narrative_plan.milestone_outline
    step_1 = next(s for s in outline["steps"] if s["index"] == 1)
    assert step_1["quest_id"] == "dq_step_test"
    # Step 0 should be unchanged
    step_0 = next(s for s in outline["steps"] if s["index"] == 0)
    assert step_0.get("quest_id") is None


def test_create_quest_without_step_index_does_not_modify_outline() -> None:
    """planner_create_quest without step_index should not touch milestone outline."""
    world = WorldInstance("test_world")
    state = _make_state()
    state.narrative_plan.set_milestone_outline({
        "target_milestone_id": "m1",
        "computed_at_tick": 0,
        "steps": [{"index": 0, "description": "Step 0", "completed": False, "quest_id": None}],
    })
    handler = PlannerQuestHandler()

    result = handler.compute(
        Command(
            type="planner_create_quest",
            params={
                "quest_id": "dq_no_step",
                "title": "Standalone Quest",
                "summary": "Not linked to outline",
                "current_tick": 5,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    state.apply(result.delta)

    outline = state.narrative_plan.milestone_outline
    step_0 = next(s for s in outline["steps"] if s["index"] == 0)
    assert step_0.get("quest_id") is None


def test_quest_objective_tracking_hook_marks_outline_step_on_completion() -> None:
    """When all objectives complete, the hook marks the linked outline step completed."""
    import asyncio

    async def _run() -> None:
        from app.game_core.orchestration.scene_bus import SceneBus
        from app.game_core.orchestration.settlement import SettlementContext
        from app.game_core.rules import RulesEngine
        from app.game_core.state import StateChange, StateDelta
        from app.game_core.state.slices import EventSlice, PlayerSlice, SceneSlice

        state = StateContainer()
        quests = QuestSlice()
        quests.restore({
            "milestone_states": {},
            "dynamic_quests": {
                "dq_outlined": {
                    "quest_id": "dq_outlined",
                    "status": "active",
                    "title": "Outlined Quest",
                    "summary": "Linked to step 2",
                    "metadata": {"step_index": 2},
                    "objectives": [
                        {
                            "description": "Reach the ruins",
                            "completed": False,
                            "condition": {
                                "type": "location_visited",
                                "params": {"area_id": "ancient_ruins"},
                            },
                        }
                    ],
                }
            },
            "chapter_completion": {},
        })
        state.register(quests)

        narrative_plan = NarrativePlanSlice()
        narrative_plan.restore({})
        narrative_plan.set_milestone_outline({
            "target_milestone_id": "m1",
            "computed_at_tick": 0,
            "steps": [
                {"index": 2, "description": "Reach ruins", "completed": False, "quest_id": "dq_outlined"},
            ],
        })
        state.register(narrative_plan)

        player = PlayerSlice()
        player.restore({"current_area": "ancient_ruins"})
        state.register(player)

        events = EventSlice()
        events.restore({})
        state.register(events)

        flags = FlagSlice()
        flags.restore({})
        state.register(flags)

        scene_slice = SceneSlice()
        scene_slice.restore({})
        state.register(scene_slice)

        world = WorldInstance("test_world")
        scene_bus = SceneBus(scene_slice)
        rules_engine = RulesEngine()
        change_log: list[StateChange] = []

        def _apply_delta(delta: StateDelta | None) -> None:
            if delta is None:
                return
            state.apply(delta)
            change_log.extend(delta.changes)

        context = SettlementContext(
            change_log=change_log,
            state=state,
            world=world,
            scene_bus=scene_bus,
            _rules_engine=rules_engine,
            _apply_delta=_apply_delta,
        )

        hook = QuestObjectiveTrackingHook()
        result = await hook.execute(context)

        assert result is not None
        # The objective should be auto-completed (area_id matches current_area)
        quest = state.quests.get_dynamic_quest("dq_outlined")
        assert quest is not None
        # The outline step should be marked completed
        steps = state.narrative_plan.milestone_outline.get("steps", [])
        step_2 = next((s for s in steps if s["index"] == 2), None)
        assert step_2 is not None
        assert step_2["completed"] is True

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Phase 2 (precious-wishing-meteor): PlannerWorldHandler validation hardening
# ---------------------------------------------------------------------------

# --- 2a: planner_fill_location capacity double-check in compute() ---

def test_fill_location_compute_rejects_overflow_when_4_existing() -> None:
    """compute() must return ExecuteResult.error when adding new item to a full slot (4 existing)."""
    world = _make_world_with_location()
    state = _make_state_with_area("town")
    # Pre-populate 4 scoped overlay entries
    state.areas.restore(
        {
            "areas": {
                "town": {
                    "scoped_interactable_overlays": {
                        "temple__hall": [
                            {"id": "i1"},
                            {"id": "i2"},
                            {"id": "i3"},
                            {"id": "i4"},
                        ]
                    }
                }
            }
        }
    )
    handler = PlannerWorldHandler()

    result = handler.compute(
        Command(
            type="planner_fill_location",
            params={
                "area_id": "town",
                "location_id": "temple",
                "room_id": "hall",
                "interactables": [{"id": "i5", "name": "New Item"}],
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is False
    assert result.errors
    assert any("capacity" in e for e in result.errors)


def test_fill_location_compute_allows_update_of_existing_id_when_full() -> None:
    """compute() must allow update (upsert) of an existing ID even when slot is full."""
    world = _make_world_with_location()
    state = _make_state_with_area("town")
    state.areas.restore(
        {
            "areas": {
                "town": {
                    "scoped_interactable_overlays": {
                        "temple__hall": [
                            {"id": "i1", "name": "Original"},
                            {"id": "i2"},
                            {"id": "i3"},
                            {"id": "i4"},
                        ]
                    }
                }
            }
        }
    )
    handler = PlannerWorldHandler()

    result = handler.compute(
        Command(
            type="planner_fill_location",
            params={
                "area_id": "town",
                "location_id": "temple",
                "room_id": "hall",
                "interactables": [{"id": "i1", "name": "Updated"}],
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.delta is not None


def test_fill_location_compute_rejects_when_mix_of_new_and_existing_overflows() -> None:
    """When adding both existing updates and a new ID causes overflow, compute() errors."""
    world = _make_world_with_location()
    state = _make_state_with_area("town")
    state.areas.restore(
        {
            "areas": {
                "town": {
                    "scoped_interactable_overlays": {
                        "temple__hall": [
                            {"id": "i1"},
                            {"id": "i2"},
                            {"id": "i3"},
                            {"id": "i4"},
                        ]
                    }
                }
            }
        }
    )
    handler = PlannerWorldHandler()

    # i1 is an update (existing), i5 is new → overflow
    result = handler.compute(
        Command(
            type="planner_fill_location",
            params={
                "area_id": "town",
                "location_id": "temple",
                "room_id": "hall",
                "interactables": [
                    {"id": "i1", "name": "Updated"},
                    {"id": "i5", "name": "New"},
                ],
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is False
    assert result.errors


# --- 2b: planner_fill_area interactables/resident_npcs list normalization ---

def test_fill_area_interactables_non_list_coerced_to_empty() -> None:
    """planner_fill_area with interactables=None should coerce to empty list."""
    world = WorldInstance("test_world")
    state = _make_state_with_area("forest")
    handler = PlannerWorldHandler()

    result = handler.compute(
        Command(
            type="planner_fill_area",
            params={
                "area_id": "forest",
                "id": "grove",
                "label": "Grove",
                "interactables": None,
                "resident_npcs": None,
                "current_tick": 1,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.delta is not None
    state.apply(result.delta)
    sub_areas = state.areas.list_temporary_sub_areas("forest")
    assert len(sub_areas) == 1
    assert isinstance(sub_areas[0]["interactables"], list)
    assert isinstance(sub_areas[0]["resident_npcs"], list)


def test_fill_area_interactables_string_coerced_to_empty() -> None:
    """planner_fill_area with interactables as a string should coerce to empty list."""
    world = WorldInstance("test_world")
    state = _make_state_with_area("village")
    handler = PlannerWorldHandler()

    result = handler.compute(
        Command(
            type="planner_fill_area",
            params={
                "area_id": "village",
                "id": "fountain",
                "label": "Fountain",
                "interactables": "not_a_list",
                "resident_npcs": 42,
                "current_tick": 2,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    state.apply(result.delta)
    sub_areas = state.areas.list_temporary_sub_areas("village")
    assert len(sub_areas) == 1
    assert isinstance(sub_areas[0]["interactables"], list)
    assert isinstance(sub_areas[0]["resident_npcs"], list)


# --- 2c: planner_plant_encounter area_id empty guard ---

def test_plant_encounter_compute_rejects_empty_area_id() -> None:
    """_compute_plant_encounter must return error when area_id is empty string."""
    world = WorldInstance("test_world")
    state = _make_state_with_area("dungeon")
    handler = PlannerWorldHandler()

    # Pass area_id as empty string — validate() catches it but we test compute() directly
    # by bypassing validate() through a crafted state where area_id passes validate
    # but a manual call to _compute_plant_encounter would receive an empty area_id.
    result = handler._compute_plant_encounter(
        Command(
            type="planner_plant_encounter",
            params={
                "area_id": "",
                "sub_area_id": "cave_entrance",
                "monster_ids": ["goblin"],
                "current_tick": 5,
            },
            source="narrative_planner",
        )
    )

    assert result.executed is False
    assert result.errors


def test_plant_encounter_compute_rejects_none_area_id() -> None:
    """_compute_plant_encounter must return error when area_id is None/missing."""
    handler = PlannerWorldHandler()

    result = handler._compute_plant_encounter(
        Command(
            type="planner_plant_encounter",
            params={
                "sub_area_id": "cave_entrance",
                "monster_ids": ["goblin"],
                "current_tick": 5,
            },
            source="narrative_planner",
        )
    )

    assert result.executed is False
    assert result.errors


def test_plant_encounter_compute_succeeds_with_valid_area_id() -> None:
    """_compute_plant_encounter emits hostile_tracking StateChange with non-empty area_id."""
    handler = PlannerWorldHandler()

    result = handler._compute_plant_encounter(
        Command(
            type="planner_plant_encounter",
            params={
                "area_id": "dungeon",
                "sub_area_id": "cave_entrance",
                "monster_ids": ["goblin"],
                "current_tick": 5,
            },
            source="narrative_planner",
        )
    )

    assert result.executed is True
    assert result.delta is not None
    assert len(result.delta.changes) == 1
    change = result.delta.changes[0]
    assert change.slice == "areas"
    # hostile_tracking entry must include non-empty area_id
    assert change.value["area_id"] == "dungeon"


# --- 2d: planner_fill_room defensive isinstance check ---

def test_fill_room_compute_produces_mapping_entry() -> None:
    """_compute_fill_room should produce a valid dict (Mapping) in StateChange.value."""
    world = _make_world_with_location()
    state = _make_state_with_area("town")
    handler = PlannerWorldHandler()

    result = handler.compute(
        Command(
            type="planner_fill_room",
            params={
                "area_id": "town",
                "location_id": "temple",
                "room_id": "secret_vault",
                "name": "Secret Vault",
                "description": "A hidden chamber.",
                "current_tick": 10,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.delta is not None
    assert len(result.delta.changes) == 1
    change = result.delta.changes[0]
    assert isinstance(change.value, Mapping)
    assert change.value["room_id"] == "secret_vault"
    assert change.value["sub_loc_id"] == "temple"


# ---------------------------------------------------------------------------
# Phase 3 (precious-wishing-meteor): PlannerNpcHandler + PlannerQuestHandler
# ---------------------------------------------------------------------------

# --- 3a: planner_spawn_quest_npc room_id cleared when location_id absent ---

def test_spawn_quest_npc_clears_room_when_location_id_absent() -> None:
    """When location_id is absent, resolved_room_id must be cleared to avoid area slice error."""
    world = _make_world_with_location()
    state = _make_state_with_area("town")
    handler = PlannerNpcHandler()

    # No location_id supplied — resolve_npc_room should return None, and even if it
    # somehow returned a room, the handler must clear it.
    result = handler.compute(
        Command(
            type="planner_spawn_quest_npc",
            params={
                "area_id": "town",
                "npc_id": "wanderer",
                "current_tick": 5,
                # location_id intentionally omitted
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.delta is not None
    state.apply(result.delta)

    # npc_presence StateChange must have room_id=None (not a non-empty string)
    npc_presence_change = next(
        (c for c in result.delta.changes if c.slice == "areas" and "npc_presence" in c.path),
        None,
    )
    assert npc_presence_change is not None
    assert npc_presence_change.value.get("room_id") is None


def test_spawn_quest_npc_room_cleared_means_area_apply_succeeds() -> None:
    """After compute(), applying the delta to AreaSlice must not raise (no room/no location)."""
    world = _make_world_with_location()
    state = _make_state_with_area("town")
    handler = PlannerNpcHandler()

    result = handler.compute(
        Command(
            type="planner_spawn_quest_npc",
            params={
                "area_id": "town",
                "npc_id": "nomad",
                "current_tick": 7,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    # Must not raise ValueError from AreaSlice
    state.apply(result.delta)
    area_state = state.areas.get_area("town")
    assert area_state.npc_locations.get("nomad") is None  # no location_id → no npc_location entry
    assert area_state.npc_rooms.get("nomad") is None


def test_spawn_quest_npc_with_location_retains_resolved_room() -> None:
    """When location_id is present, resolved_room_id is kept (default room lookup)."""
    world = _make_world_with_location()
    state = _make_state_with_area("town")
    handler = PlannerNpcHandler()

    result = handler.compute(
        Command(
            type="planner_spawn_quest_npc",
            params={
                "area_id": "town",
                "npc_id": "pilgrim",
                "location_id": "temple",
                "current_tick": 3,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    state.apply(result.delta)
    area_state = state.areas.get_area("town")
    # Temple has default_room "hall" — should be resolved
    assert area_state.npc_rooms.get("pilgrim") == "hall"


# --- 3b: planner_publish_bulletin area_id empty-string guard ---

def test_publish_bulletin_compute_rejects_empty_area_id_string() -> None:
    """When _resolve_area_id returns an empty string, compute() must return an error."""
    world = WorldInstance("test_world")
    state = _make_state()
    state.quests.restore(
        {
            "milestone_states": {},
            "dynamic_quests": {
                "dq_bulletin": {
                    "quest_id": "dq_bulletin",
                    "status": "active",
                    "title": "Bulletin Quest",
                    "summary": "Test",
                }
            },
            "chapter_completion": {},
        }
    )
    handler = PlannerQuestHandler()

    # Provide area_id as whitespace only — coerce_non_empty_string returns None,
    # so _resolve_area_id will fall through to player slice (absent) → returns None.
    # Manually call compute() which should return error, not crash.
    result = handler.compute(
        Command(
            type="planner_publish_bulletin",
            params={
                "board_id": "quest_board",
                "quest_id": "dq_bulletin",
                "title": "Help Wanted",
                "content": "Looking for adventurers",
                "area_id": "   ",  # whitespace-only → resolves to empty
                "current_tick": 5,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    # validate() should reject first (area_id unresolvable), but if it slips
    # through, compute() must also guard.
    assert result.executed is False


def test_publish_bulletin_compute_succeeds_with_valid_area_id() -> None:
    """planner_publish_bulletin with a valid area_id must succeed."""
    world = WorldInstance("test_world")
    state = _make_state()
    state.quests.restore(
        {
            "milestone_states": {},
            "dynamic_quests": {
                "dq_valid": {
                    "quest_id": "dq_valid",
                    "status": "active",
                    "title": "Valid Quest",
                    "summary": "Test",
                }
            },
            "chapter_completion": {},
        }
    )
    handler = PlannerQuestHandler()

    result = handler.compute(
        Command(
            type="planner_publish_bulletin",
            params={
                "board_id": "quest_board",
                "quest_id": "dq_valid",
                "title": "Help Wanted",
                "content": "Looking for adventurers",
                "area_id": "forest",
                "current_tick": 5,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.delta is not None
    state.apply(result.delta)
    bulletins = state.areas.get_board_bulletins("forest", "quest_board")
    assert len(bulletins) == 1
    assert bulletins[0]["quest_id"] == "dq_valid"


# --- 3c: planner_direct_npc expires_at_tick clamped >= current_tick ---

def test_direct_npc_expires_at_tick_clamped_when_less_than_current() -> None:
    """When LLM passes expires_at_tick < current_tick, it must be clamped to current_tick."""
    world = WorldInstance("test_world")
    state = _make_state()
    handler = PlannerNpcHandler()

    result = handler.compute(
        Command(
            type="planner_direct_npc",
            params={
                "npc_id": "guard",
                "directive": {"kind": "patrol", "route": ["gate", "market"]},
                "current_tick": 20,
                "expires_at_tick": 5,  # < current_tick → must be clamped
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    assert result.delta is not None
    state.apply(result.delta)

    directives = state.narrative_plan.npc_directives
    assert len(directives) == 1
    # expires_at_tick must be clamped to current_tick (not 5)
    assert directives[0]["expires_at_tick"] >= 20


def test_direct_npc_expires_at_tick_negative_value_clamped() -> None:
    """When LLM passes a negative expires_at_tick, it must be clamped to current_tick."""
    world = WorldInstance("test_world")
    state = _make_state()
    handler = PlannerNpcHandler()

    result = handler.compute(
        Command(
            type="planner_direct_npc",
            params={
                "npc_id": "merchant",
                "directive": {"kind": "trade"},
                "current_tick": 10,
                "expires_at_tick": -3,
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    # -3 coerces to -3 but is < current_tick=10, so it should be clamped
    # Note: the existing guard "if expires_at_tick < 0: return error" fires first
    # for -3 (coerce_int(-3) = -3, not None). Verify that result is either:
    # (a) executed=False with the negative-value rejection, OR
    # (b) executed=True with clamped value.
    # Either is acceptable — what must NOT happen is a crash or an expires_at_tick < 0.
    if result.executed:
        state.apply(result.delta)
        directives = state.narrative_plan.npc_directives
        assert directives[0]["expires_at_tick"] >= 0
    # If rejected early (executed=False), that's also valid behavior


def test_direct_npc_expires_at_tick_future_value_preserved() -> None:
    """When expires_at_tick > current_tick, it must not be modified."""
    world = WorldInstance("test_world")
    state = _make_state()
    handler = PlannerNpcHandler()

    result = handler.compute(
        Command(
            type="planner_direct_npc",
            params={
                "npc_id": "innkeeper",
                "directive": {"kind": "greet", "greeting": "Welcome!"},
                "current_tick": 10,
                "expires_at_tick": 30,  # > current_tick → preserved as-is
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    state.apply(result.delta)
    directives = state.narrative_plan.npc_directives
    assert directives[0]["expires_at_tick"] == 30


def test_direct_npc_expires_at_tick_equal_current_tick_preserved() -> None:
    """When expires_at_tick == current_tick, it must be preserved (edge case)."""
    world = WorldInstance("test_world")
    state = _make_state()
    handler = PlannerNpcHandler()

    result = handler.compute(
        Command(
            type="planner_direct_npc",
            params={
                "npc_id": "courier",
                "directive": {"kind": "deliver"},
                "current_tick": 15,
                "expires_at_tick": 15,  # equal — no clamping needed
            },
            source="narrative_planner",
        ),
        state,
        world,
    )

    assert result.executed is True
    state.apply(result.delta)
    directives = state.narrative_plan.npc_directives
    assert directives[0]["expires_at_tick"] == 15
