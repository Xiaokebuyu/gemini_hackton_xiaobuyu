"""Tests for AIOsirisHook."""

from __future__ import annotations

import asyncio
import logging

from app.game_core.content import WorldInstance
from app.game_core.content.registries import CharacterRegistry
from app.game_core.orchestration.hooks.ai_osiris import AIOsirisHook
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import WorldStateHandler
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    FlagSlice,
    NarrativePlanSlice,
    PartySlice,
    PlayerSlice,
    RelationSlice,
    SceneSlice,
    TimeSlice,
)


class RecordingEvaluator:
    def __init__(self, decision) -> None:
        self.decision = decision
        self.calls: list[dict[str, object]] = []

    def evaluate(self, summary, snapshot, rules_context):
        self.calls.append(
            {
                "summary": summary,
                "snapshot": snapshot,
                "rules_context": rules_context,
            }
        )
        return self.decision


class ExplodingEvaluator:
    def evaluate(self, summary, snapshot, rules_context):
        del summary, snapshot, rules_context
        raise RuntimeError("llm unavailable")


def _make_context(
    *,
    change_log: list[StateChange] | None = None,
    include_characters: bool = False,
) -> SettlementContext:
    world = WorldInstance("test_world")
    if include_characters:
        characters = CharacterRegistry()
        characters.load(
            {
                "npc_guard": {"id": "npc_guard", "name": "Guard", "area_id": "forest"},
                "npc_far": {"id": "npc_far", "name": "Far Away", "area_id": "city"},
            }
        )
        world.register(characters)

    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 2, "slot": 9})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": "forest", "current_location": "camp"})
    state.register(player)

    flags = FlagSlice()
    flags.restore({"flags": {"quest_started": True}})
    state.register(flags)

    relations = RelationSlice()
    relations.restore({"faction_standings": {"guild": 3}})
    state.register(relations)

    party = PartySlice()
    party.restore({"members": {"companion_1": {"id": "companion_1"}}})
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
        _rules_engine=rules_engine,
        _apply_delta=_apply_delta,
    )


class TestAIOsirisHook:
    def test_should_skip_without_meaningful_changes(self) -> None:
        hook = AIOsirisHook()

        assert hook.should_skip([]) is True
        assert (
            hook.should_skip(
                [
                    StateChange(
                        slice="scene",
                        operation="add",
                        path="entries",
                        value={"source": "gm"},
                    )
                ]
            )
            is True
        )

    def test_should_not_skip_when_flags_changed(self) -> None:
        hook = AIOsirisHook()

        assert (
            hook.should_skip(
                [
                    StateChange(
                        slice="flags",
                        operation="set",
                        path="flags.quest_started",
                        value=True,
                    )
                ]
            )
            is False
        )

    def test_default_evaluator_sets_ack_flag_for_quest_started(self) -> None:
        context = _make_context(
            change_log=[
                StateChange(
                    slice="flags",
                    operation="set",
                    path="flags.quest_started",
                    value=True,
                )
            ]
        )

        result = asyncio.run(AIOsirisHook().execute(context))

        assert result.metadata["status"] == "applied"
        assert result.metadata["evaluated"] is True
        assert result.metadata["executed_count"] == 1
        assert result.metadata["truncated_count"] == 0
        assert result.metadata["allowed_command_enforced"] is True
        assert result.metadata["evaluator_metadata"] == {
            "status": "deterministic",
            "provider": "default_evaluator",
            "branch": "flags",
            "command_count": 1,
        }
        assert result.sse_events[0].event_type == "ai_osiris_applied"
        assert context.state.flags.get("quest_started") is True
        assert context.state.flags.get("osiris_ack_quest_started") is True

    def test_default_evaluator_records_current_chapter_on_quest_changes(self) -> None:
        context = _make_context(
            change_log=[
                StateChange(
                    slice="quests",
                    operation="set",
                    path="milestone_states.ms_1",
                    value={"state": "ACTIVE"},
                )
            ]
        )

        result = asyncio.run(AIOsirisHook().execute(context))

        assert result.metadata["status"] == "applied"
        assert context.state.flags.get("osiris_last_quest_change_chapter") == "chapter_1"
        assert result.metadata["evaluator_metadata"] == {
            "status": "deterministic",
            "provider": "default_evaluator",
            "branch": "quests",
            "command_count": 1,
        }

    def test_default_evaluator_is_noop_when_ack_flag_already_exists(self) -> None:
        context = _make_context(
            change_log=[
                StateChange(
                    slice="flags",
                    operation="set",
                    path="flags.quest_started",
                    value=True,
                )
            ]
        )
        context.state.flags.set("osiris_ack_quest_started", True)
        context.state.flags.clear_dirty()

        result = asyncio.run(AIOsirisHook().execute(context))

        assert result.metadata["status"] == "noop"
        assert result.metadata["executed_count"] == 0
        assert result.metadata["evaluator_metadata"] == {
            "status": "noop",
            "provider": "default_evaluator",
            "reason": "stable",
        }
        assert result.sse_events == []

    def test_default_evaluator_stays_noop_for_player_only_changes(self) -> None:
        context = _make_context(
            change_log=[
                StateChange(
                    slice="player",
                    operation="set",
                    path="current_area",
                    value="forest",
                )
            ]
        )

        result = asyncio.run(AIOsirisHook().execute(context))

        assert result.metadata["status"] == "noop"
        assert result.metadata["executed_count"] == 0
        assert result.metadata["evaluator_metadata"] == {
            "status": "noop",
            "provider": "default_evaluator",
            "reason": "stable",
        }
        assert result.sse_events == []

    def test_execute_passes_stable_summary_snapshot_and_rules_context(self) -> None:
        evaluator = RecordingEvaluator(
            {
                "consequences": [],
                "reasoning": "nothing to do",
                "metadata": {"model": "fake"},
            }
        )
        context = _make_context(
            change_log=[
                StateChange(
                    slice="player",
                    operation="set",
                    path="current_area",
                    value="forest",
                ),
                StateChange(
                    slice="flags",
                    operation="set",
                    path="flags.quest_started",
                    value=True,
                ),
            ],
            include_characters=True,
        )

        result = asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        assert result.metadata["status"] == "noop"
        call = evaluator.calls[0]
        summary = call["summary"]
        snapshot = call["snapshot"]
        rules_context = call["rules_context"]

        assert summary["change_count"] == 2
        assert summary["changed_slices"] == ["player", "flags"]
        assert summary["time_slot"]["absolute_tick"] == 33
        assert summary["location"] == {"area_id": "forest", "location_id": "camp"}
        assert summary["actions"] == []
        assert summary["duration_minutes"] == 0

        assert snapshot["current_chapter"] == "chapter_1"
        assert snapshot["active_flags"] == {"quest_started": True}
        assert snapshot["faction_standings"] == {"guild": 3}
        assert len(snapshot["nearby_npcs"]) == 1
        assert snapshot["nearby_npcs"][0]["id"] == "npc_guard"

        assert rules_context["command_source"] == "ai_osiris"
        assert "set_flag" in rules_context["allowed_commands"]
        assert rules_context["constraints"]["scene_bus_text_deferred"] is True

    def test_dict_consequence_executes_and_emits_sse_without_scene_text(self) -> None:
        evaluator = RecordingEvaluator(
            {
                "consequences": [
                    {
                        "type": "set_flag",
                        "params": {"key": "ai_flag", "value": 7},
                    }
                ],
                "reasoning": "set a flag",
                "visible_change": True,
                "metadata": {"model": "fake"},
            }
        )
        context = _make_context(
            change_log=[
                StateChange(
                    slice="player",
                    operation="set",
                    path="current_location",
                    value="camp",
                )
            ]
        )

        result = asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        assert context.state.flags.get("ai_flag") == 7
        assert result.metadata["status"] == "applied"
        assert result.metadata["executed_count"] == 1
        assert result.metadata["failed_count"] == 0
        assert result.metadata["decision_reasoning"] == "set a flag"
        assert result.sse_events[0].event_type == "ai_osiris_applied"
        assert result.sse_events[0].payload["command_types"] == ["set_flag"]
        assert context.scene_bus.snapshot()["entries"] == []

    def test_command_consequence_is_supported(self) -> None:
        evaluator = RecordingEvaluator(
            {
                "consequences": [
                    Command(
                        type="set_flag",
                        params={"key": "from_command", "value": "ok"},
                        source="engine",
                    )
                ]
            }
        )
        context = _make_context(
            change_log=[
                StateChange(
                    slice="flags",
                    operation="set",
                    path="flags.quest_started",
                    value=True,
                )
            ]
        )

        result = asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        assert context.state.flags.get("from_command") == "ok"
        assert result.metadata["status"] == "applied"
        assert result.metadata["command_results"][0]["command_type"] == "set_flag"

    def test_invalid_consequence_is_skipped(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [{"params": {"key": "x"}}]})
        context = _make_context(
            change_log=[
                StateChange(
                    slice="flags",
                    operation="set",
                    path="flags.quest_started",
                    value=True,
                )
            ]
        )

        result = asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        assert result.metadata["status"] == "invalid_consequences"
        assert result.metadata["requested_count"] == 1
        assert result.metadata["normalized_count"] == 0
        assert result.metadata["skipped_invalid_count"] == 1
        assert result.sse_events == []

    def test_unknown_command_type_is_rejected_by_whitelist(self) -> None:
        evaluator = RecordingEvaluator(
            {
                "consequences": [
                    {"type": "cast_spell", "params": {"spell_id": "fireball"}},
                ]
            }
        )
        context = _make_context(
            change_log=[
                StateChange(
                    slice="flags",
                    operation="set",
                    path="flags.quest_started",
                    value=True,
                )
            ]
        )

        result = asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        assert result.metadata["status"] == "invalid_consequences"
        assert result.metadata["normalized_count"] == 0
        assert result.metadata["skipped_invalid_count"] == 1
        assert result.metadata["allowed_command_enforced"] is True

    def test_consequence_list_is_truncated_to_maximum(self) -> None:
        consequences = [
            {
                "type": "set_flag",
                "params": {"key": f"flag_{index}", "value": index},
            }
            for index in range(7)
        ]
        evaluator = RecordingEvaluator({"consequences": consequences})
        context = _make_context(
            change_log=[
                StateChange(
                    slice="flags",
                    operation="set",
                    path="flags.quest_started",
                    value=True,
                )
            ]
        )

        result = asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        assert result.metadata["status"] == "applied"
        assert result.metadata["executed_count"] == 5
        assert result.metadata["truncated_count"] == 2
        assert context.state.flags.get("flag_4") == 4
        assert context.state.flags.get("flag_5") is None
        assert result.sse_events[0].payload["command_types"] == [
            "set_flag",
            "set_flag",
            "set_flag",
            "set_flag",
            "set_flag",
        ]

    def test_failed_command_does_not_stop_following_commands(self) -> None:
        evaluator = RecordingEvaluator(
            {
                "consequences": [
                    Command(
                        type="modify_location",
                        params={"area_id": "cave"},
                        source="engine",
                    ),
                    {"type": "set_flag", "params": {"key": "after_failure", "value": True}},
                ]
            }
        )
        context = _make_context(
            change_log=[
                StateChange(
                    slice="player",
                    operation="set",
                    path="current_area",
                    value="forest",
                )
            ]
        )

        result = asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        assert context.state.player.current_area == "forest"
        assert context.state.flags.get("after_failure") is True
        assert result.metadata["status"] == "partial_failure"
        assert result.metadata["executed_count"] == 2
        assert result.metadata["failed_count"] == 1
        assert result.metadata["command_results"][0]["success"] is False
        assert result.metadata["command_results"][0]["errors"] == [
            "ai_osiris cannot modify player location directly"
        ]

    def test_evaluator_exception_returns_error_sse(self, caplog) -> None:
        context = _make_context(
            change_log=[
                StateChange(
                    slice="flags",
                    operation="set",
                    path="flags.quest_started",
                    value=True,
                )
            ]
        )

        with caplog.at_level(logging.ERROR):
            result = asyncio.run(AIOsirisHook(evaluator=ExplodingEvaluator()).execute(context))

        assert result.metadata["status"] == "evaluator_error"
        assert result.metadata["evaluated"] is False
        assert result.metadata["truncated_count"] == 0
        assert result.metadata["allowed_command_enforced"] is True
        assert result.sse_events[0].event_type == "ai_osiris_error"
        assert result.sse_events[0].payload["error"] == "llm unavailable"
        assert any(
            record.message == "hook failed: ai_osiris"
            and getattr(record, "hook_name", "") == "ai_osiris"
            and record.exc_info is not None
            for record in caplog.records
        )
