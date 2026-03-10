from __future__ import annotations

from pathlib import Path

from app.game_core.content import WorldInstance
from app.game_core.rules.handlers.planner import (
    PlannerNpcHandler,
    PlannerQuestHandler,
    PlannerRuntimeHandler,
)
from app.game_core.rules.models import Command
from app.game_core.state import StateContainer
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

