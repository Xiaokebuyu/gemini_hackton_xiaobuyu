"""Tests for AIOsirisHook."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.content.registries import (
    CharacterRegistry,
    FactionRegistry,
    ItemRegistry,
    LoreRegistry,
    SkillRegistry,
    TagRegistry,
)
from app.game_core.orchestration.hooks.ai_osiris import AIOsirisHook
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import WorldStateHandler
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import (
    AreaSlice,
    FlagSlice,
    NarrativePlanSlice,
    PartySlice,
    PlayerSlice,
    QuestSlice,
    RelationSlice,
    SceneSlice,
    EventSlice,
    TimeSlice,
)


class RecordingEvaluator:
    def __init__(self, decision) -> None:
        self.decision = decision
        self.calls: list[dict[str, object]] = []

    async def evaluate(self, summary, snapshot, rules_context):
        self.calls.append(
            {
                "summary": summary,
                "snapshot": snapshot,
                "rules_context": rules_context,
            }
        )
        return self.decision


class ExplodingEvaluator:
    async def evaluate(self, summary, snapshot, rules_context):
        del summary, snapshot, rules_context
        raise RuntimeError("llm unavailable")


def _make_context(
    *,
    change_log: list[StateChange] | None = None,
    include_characters: bool = False,
    include_lore: bool = False,
    include_factions: bool = False,
    include_tags: bool = False,
    include_quests: bool = False,
    include_events: bool = False,
    event_pending: list[dict[str, Any]] | None = None,
    narrative_plan_data: dict | None = None,
    area_npc_locations: dict[str, str | None] | None = None,
    area_danger_level: float | None = None,
    action_log: list[dict] | None = None,
) -> SettlementContext:
    world = WorldInstance("test_world")
    if include_characters:
        characters = CharacterRegistry()
        characters.load(
            {
                "npc_guard": {
                    "id": "npc_guard",
                    "name": "Guard",
                    "area_id": "forest",
                    "tags": ["GUARD", "SOLDIER"],
                },
                "npc_far": {"id": "npc_far", "name": "Far Away", "area_id": "city"},
                "companion_1": {
                    "id": "companion_1",
                    "name": "Companion",
                    "tags": ["COMPANION", "PALADIN"],
                    "faction": "temple_order",
                },
                "player_char": {
                    "id": "player_char",
                    "name": "Hero",
                    "tags": ["ADVENTURER", "NEWCOMER"],
                },
            }
        )
        world.register(characters)
    if include_lore:
        lore = LoreRegistry()
        lore.load({
            "theft_rules": {
                "id": "theft_rules",
                "content": "Merchant guild has zero tolerance for theft.",
                "tags": ["CRIME", "COMMERCE"],
            },
        })
        world.register(lore)
    if include_factions:
        factions = FactionRegistry()
        factions.load({
            "merchant_guild": {
                "id": "merchant_guild",
                "name": "Merchant Guild",
                "alignment": "lawful_neutral",
                "behavioral_rules": "Zero tolerance for theft; triggers city-wide bounty.",
            },
        })
        world.register(factions)
    if include_tags:
        tags = TagRegistry()
        tags.load({
            "moral": {
                "id": "moral",
                "description": "Moral alignment tags",
                "tags": ["SACRED", "PROFANE", "NEUTRAL"],
            },
        })
        world.register(tags)

    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 2, "slot": 9})
    state.register(time_slice)

    player = PlayerSlice()
    player_data: dict = {"current_area": "forest", "current_location": "camp"}
    if include_characters:
        player_data["character_id"] = "player_char"
    player.restore(player_data)
    state.register(player)

    flags = FlagSlice()
    flags.restore({"flags": {"quest_started": True}})
    state.register(flags)

    relations = RelationSlice()
    if include_characters:
        relations.restore({
            "faction_standings": {"guild": 3},
            "npc_dispositions": {"companion_1": {"trust": 40, "admiration": 30}},
            "relationship_stages": {"companion_1": "acquaintance"},
        })
    else:
        relations.restore({"faction_standings": {"guild": 3}})
    state.register(relations)

    areas = AreaSlice()
    area_data: dict = {}
    if area_npc_locations is not None:
        area_data = {"areas": {"forest": {"npc_locations": area_npc_locations}}}
    if area_danger_level is not None:
        area_state = area_data.setdefault("areas", {}).setdefault("forest", {})
        area_state["danger_level"] = area_danger_level
    areas.restore(area_data)
    state.register(areas)

    party = PartySlice()
    party_data: dict = {"members": {"companion_1": {"id": "companion_1"}}}
    if include_characters:
        party_data["companion_approval"] = {"companion_1": 65}
    party.restore(party_data)
    state.register(party)

    if include_quests:
        quests_slice = QuestSlice()
        quests_slice.restore({
            "milestone_states": {
                "main_quest_1": {"state": "ACTIVE", "activated_tick": 10},
                "side_quest_1": {"state": "AVAILABLE"},
                "completed_quest": {"state": "COMPLETED", "completed_tick": 20},
            },
            "dynamic_quests": {
                "dynamic_1": {"status": "active", "title": "Dynamic Quest"},
                "dynamic_done": {"status": "completed", "title": "Done Quest"},
            },
        })
        state.register(quests_slice)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore(
        narrative_plan_data or {"current_chapter": "chapter_1"}
    )
    state.register(narrative_plan)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    if include_events:
        events = EventSlice()
        events.restore({
            "pending_events": [
                dict(item)
                for item in (event_pending if event_pending is not None else [])
                if isinstance(item, dict)
            ],
            "active_events": {},
            "rumors": [],
        })
        state.register(events)

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
        action_log=list(action_log) if action_log is not None else [],
    )


def _non_processing_events(result) -> list:
    """Return SSE events that are not ai_processing (QF-4 channel events)."""
    return [e for e in result.sse_events if e.event_type != "ai_processing"]


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
            is False
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

    def test_should_skip_when_all_actions_are_trivial_zero_cost(self) -> None:
        hook = AIOsirisHook()
        action_log = [
            {"type": "look_inventory", "time_cost": 0.0},
            {"type": "equip", "time_cost": 0},
        ]

        assert hook.should_skip([], action_log=action_log) is True
        assert hook.should_skip([StateChange(slice="scene", operation="set", path="entries", value=[])], action_log=action_log) is True

    def test_should_not_skip_when_trivial_action_has_cost(self) -> None:
        hook = AIOsirisHook()
        action_log = [{"type": "look_inventory", "time_cost": 0.2}]

        assert hook.should_skip([], action_log=action_log) is False

    def test_should_not_skip_when_action_is_non_trivial(self) -> None:
        hook = AIOsirisHook()
        action_log = [{"type": "trade_buy", "time_cost": 0.0}]

        assert hook.should_skip([], action_log=action_log) is False

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
        assert _non_processing_events(result)[0].event_type == "ai_osiris_applied"
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
        assert _non_processing_events(result) == []

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
        assert _non_processing_events(result) == []

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
        assert summary["tick_kind"] == "normal"
        assert summary["time_cost"] == 0.0
        assert summary["duration_minutes"] == 0.0

        assert snapshot["current_chapter"] == "chapter_1"
        assert snapshot["chapter_completion"] == 0.0
        assert snapshot["active_flags"] == {"quest_started": True}
        assert snapshot["faction_standings"] == {"guild": 3}
        # With sub-location filtering active (player at "camp"),
        # npc_guard has no location_id (None) so it's excluded from nearby
        assert len(snapshot["nearby_npcs"]) == 0
        assert snapshot["scene_presence"] == {
            "area_id": "forest",
            "location_id": "camp",
            "present_character_ids": ["player_char"],
        }

        assert rules_context["command_source"] == "ai_osiris"
        assert "set_flag" in rules_context["allowed_commands"]
        assert rules_context["constraints"]["scene_bus_text_deferred"] is True
        assert "command_schema" in rules_context
        assert "trigger_condition_schema" in rules_context
        assert "visibility_rules" in rules_context
        assert "semantic_constraints" in rules_context
        assert isinstance(rules_context["world_lore"], list)
        assert rules_context["world_lore"] == []
        assert isinstance(rules_context["faction_rules"], list)
        assert rules_context["faction_rules"] == []
        assert isinstance(rules_context["tag_dimensions"], dict)
        assert rules_context["tag_dimensions"] == {}

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
        non_proc = _non_processing_events(result)
        assert non_proc[0].event_type == "ai_osiris_applied"
        assert non_proc[0].payload["command_types"] == ["set_flag"]
        assert context.scene_bus.snapshot()["entries"] == []

    def test_visible_change_false_skips_visible_render(self) -> None:
        evaluator = RecordingEvaluator(
            {
                "consequences": [
                    {
                        "type": "create_rumor",
                        "params": {
                            "text": "Rumor of wolves",
                            "event_id": "rumor_event",
                            "trigger_condition": {"type": "absolute_tick", "tick": 10},
                        },
                    }
                ],
                "reasoning": "seed rumor",
                "visible_change": False,
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
            ],
            include_events=True,
        )

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        assert context.scene_bus.snapshot()["entries"] == []

    def test_visible_change_true_renders_system_scene_entries(self) -> None:
        evaluator = RecordingEvaluator(
            {
                "consequences": [
                    {
                        "type": "create_rumor",
                        "params": {
                            "text": "Rumor of wolves",
                            "event_id": "rumor_event",
                            "trigger_condition": {"type": "absolute_tick", "tick": 10},
                        },
                        "reason": "Rumor of wolves starts spreading through the camp.",
                        "visibility_hint": "visible",
                        "confidence": "high",
                    },
                    {
                        "type": "set_flag",
                        "params": {
                            "key": "ai_flag",
                            "value": 7,
                        },
                    },
                ],
                "reasoning": "seed rumor",
                "visible_change": True,
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
            ],
            include_events=True,
        )

        result = asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        assert result.metadata["visible_change_count"] == 1
        entries = context.scene_bus.snapshot()["entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "ai_osiris"
        assert entries[0]["visibility"] == "system"
        assert entries[0]["tags"] == ["ai_osiris", "visible_consequence", "create_rumor"]
        assert entries[0]["content"] == "Rumor of wolves starts spreading through the camp."
        assert entries[0]["metadata"] == {
            "kind": "visible_consequence",
            "command_type": "create_rumor",
            "reason": "Rumor of wolves starts spreading through the camp.",
            "visibility_hint": "visible",
            "confidence": "high",
            "refs": {},
        }

    def test_telemetry_records_raw_and_normalized_consequence_counts(self) -> None:
        evaluator = RecordingEvaluator(
            {
                "consequences": [
                    {
                        "type": "create_rumor",
                        "params": {
                            "text": "Rumor of wolves",
                            "event_id": "rumor_event",
                            "trigger_condition": {"type": "absolute_tick", "tick": 10},
                        },
                    },
                    {"params": {"foo": "bar"}},
                ],
                "visible_change": True,
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
            ],
            include_events=True,
        )

        result = asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        assert result.metadata["raw_consequence_count"] == 2
        assert result.metadata["normalized_consequence_count"] == 1
        assert result.metadata["normalized_count"] == 1
        assert result.metadata["skipped_invalid_count"] == 1
        assert result.metadata["invalid_count"] == 1
        assert result.metadata["visible_change_count"] == 1
        assert result.metadata["visible_command_types"] == ["create_rumor"]
        assert result.metadata["evaluation_ms"] >= 0.0

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
        assert _non_processing_events(result) == []

    def test_minimal_semantic_validation_rejects_empty_schedule_event(self) -> None:
        evaluator = RecordingEvaluator(
            {
                "consequences": [
                    {"type": "schedule_event", "params": {"event_id": "ev_missing_trigger"}},
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
        assert result.metadata["skipped_invalid_count"] == 1

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
        assert _non_processing_events(result)[0].payload["command_types"] == [
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
        assert result.metadata["command_results"][0]["executed"] is False
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
        assert _non_processing_events(result)[0].event_type == "ai_osiris_error"
        assert _non_processing_events(result)[0].payload["error"] == "llm unavailable"
        assert any(
            record.message == "hook failed: ai_osiris"
            and getattr(record, "hook_name", "") == "ai_osiris"
            and record.exc_info is not None
            for record in caplog.records
        )

    def test_rules_context_includes_world_lore_when_registry_loaded(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
            include_lore=True,
        )

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        rules_context = evaluator.calls[0]["rules_context"]
        assert len(rules_context["world_lore"]) == 1
        lore_entry = rules_context["world_lore"][0]
        assert lore_entry["id"] == "theft_rules"
        assert "zero tolerance" in lore_entry["content"].lower()
        assert lore_entry["tags"] == ["CRIME", "COMMERCE"]

    def test_rules_context_includes_faction_rules_when_registry_loaded(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
            include_factions=True,
        )

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        rules_context = evaluator.calls[0]["rules_context"]
        assert len(rules_context["faction_rules"]) == 1
        faction = rules_context["faction_rules"][0]
        assert faction["id"] == "merchant_guild"
        assert faction["name"] == "Merchant Guild"
        assert faction["alignment"] == "lawful_neutral"
        assert "behavioral_rules" in faction

    def test_rules_context_includes_tag_dimensions_when_registry_loaded(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
            include_tags=True,
        )

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        rules_context = evaluator.calls[0]["rules_context"]
        assert "moral" in rules_context["tag_dimensions"]
        assert rules_context["tag_dimensions"]["moral"] == ["SACRED", "PROFANE", "NEUTRAL"]

    def test_snapshot_chapter_completion_populated(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
            narrative_plan_data={
                "current_chapter": "ch1_goblin_crisis",
                "chapter_completion": 0.15,
            },
        )

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        snapshot = evaluator.calls[0]["snapshot"]
        assert snapshot["current_chapter"] == "ch1_goblin_crisis"
        assert snapshot["chapter_completion"] == 0.15

    def test_nearby_npcs_uses_dynamic_positions(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        # Place dynamic NPC at "camp" (same location as player) so sub-location
        # filtering includes it.
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
            area_npc_locations={"npc_dynamic": "camp"},
        )

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        nearby = evaluator.calls[0]["snapshot"]["nearby_npcs"]
        assert len(nearby) == 1
        assert nearby[0]["id"] == "npc_dynamic"
        assert nearby[0]["location_id"] == "camp"

    def test_nearby_npcs_merges_dynamic_and_static(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        # Place npc_guard at "camp" (same sub-location as player) so
        # sub-location filtering includes it. npc_far is in a different area.
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
            include_characters=True,
            area_npc_locations={"npc_guard": "camp"},
        )

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        nearby = evaluator.calls[0]["snapshot"]["nearby_npcs"]
        ids = [npc["id"] for npc in nearby]
        assert "npc_guard" in ids
        assert "npc_far" not in ids  # npc_far is in "city", not "forest"
        # npc_guard comes from dynamic source with location_id
        guard = next(npc for npc in nearby if npc["id"] == "npc_guard")
        assert guard["location_id"] == "camp"
        assert guard["name"] == "Guard"

    def test_nearby_npcs_includes_disposition(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        # Place npc_guard at "camp" (same sub-location as player) so
        # sub-location filtering includes it, allowing disposition test.
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
            include_characters=True,
            area_npc_locations={"npc_guard": "camp"},
        )
        # Set disposition data on the relations slice
        context.state.relations.npc_dispositions["npc_guard"] = {"trust": 40, "respect": 60}

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        nearby = evaluator.calls[0]["snapshot"]["nearby_npcs"]
        guard = next(npc for npc in nearby if npc["id"] == "npc_guard")
        assert guard["disposition"] == {"trust": 40, "respect": 60}

    def test_nearby_npcs_scene_local_only_when_sublocation_known(self) -> None:
        """When player has a known sub_location, only NPCs at that sub_location are returned."""
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
            include_characters=True,
            area_npc_locations={
                "npc_guard": "watchtower",
                "npc_far": "plaza",
                "companion_1": "plaza",
            },
        )
        context.state.player.current_location = "watchtower"

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        nearby = evaluator.calls[0]["snapshot"]["nearby_npcs"]
        # With sub_location filtering: only npc_guard (at watchtower) is included
        # npc_far and companion_1 are at plaza — excluded
        assert nearby[0]["id"] == "npc_guard"
        assert nearby[0]["location_id"] == "watchtower"
        assert [npc["id"] for npc in nearby] == ["npc_guard"]

    def test_summary_actions_populated_from_action_log(self) -> None:
        action_records = [
            {"type": "trade_buy", "actor": "player", "params": {"item_id": "dagger"}, "executed": True, "time_cost": 1.0 / 6.0},
            {"type": "skill_check", "actor": "player", "params": {"skill": "perception"}, "executed": False, "time_cost": 1.0 / 6.0},
        ]
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
            action_log=action_records,
        )

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        summary = evaluator.calls[0]["summary"]
        assert summary["tick_kind"] == "normal"
        assert summary["time_cost"] == 1.0 / 3.0
        assert summary["duration_minutes"] == 20.0
        assert len(summary["actions"]) == 2
        assert summary["actions"][0]["type"] == "trade_buy"
        assert summary["actions"][0]["actor"] == "player"
        assert summary["actions"][0]["params"]["item_id"] == "dagger"
        assert summary["actions"][1]["type"] == "skill_check"
        assert summary["actions"][1]["executed"] is False
        assert summary["actions"][0]["visibility_scope"] == "local"
        assert summary["actions"][0]["witnessed_by"] == ["companion_1"]

    def test_summary_actions_empty_when_no_action_log(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
        )

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        summary = evaluator.calls[0]["summary"]
        assert summary["actions"] == []
        assert summary["tick_kind"] == "normal"
        assert summary["time_cost"] == 0.0

    def test_summary_tick_kind_by_action_category(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
            action_log=[
                {"type": "move_area", "time_cost": 1.0, "actor": "player"},
                {"type": "look_inventory", "time_cost": 0.0, "actor": "player"},
            ],
        )

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        summary = evaluator.calls[0]["summary"]
        assert summary["tick_kind"] == "travel"
        assert summary["time_cost"] == 1.0
        assert summary["duration_minutes"] == 60.0

    def test_summary_tick_kind_detects_external_dialogue_turns(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
            action_log=[
                {
                    "type": "dialogue_turn",
                    "time_cost": 1.0 / 6.0,
                    "actor": "player",
                    "params": {"npc_id": "guild_girl"},
                }
            ],
        )

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        summary = evaluator.calls[0]["summary"]
        assert summary["tick_kind"] == "conversation"
        assert summary["time_cost"] == 1.0 / 6.0

    def test_quiet_long_rest_slot_suppresses_provider_consequences(self) -> None:
        evaluator = RecordingEvaluator(
            {
                "consequences": [
                    {
                        "type": "create_rumor",
                        "params": {"text": "Sleepy gossip"},
                        "reason": "The camp whispers spread.",
                        "visibility_hint": "visible",
                        "confidence": "medium",
                    }
                ],
                "reasoning": "quiet slot",
                "visible_change": True,
            }
        )
        context = _make_context(action_log=[{"type": "rest_long", "time_cost": 1.0}])
        context.state.time.accumulated = 4.0
        context.state.time._dirty = True

        result = asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        assert len(evaluator.calls) == 1
        assert result.metadata["status"] == "noop"
        assert result.metadata["quiet_rest_slot"] is True
        assert result.metadata["executed_count"] == 0
        assert result.metadata["requested_count"] == 0
        assert _non_processing_events(result) == []
        assert context.scene_bus.snapshot()["entries"] == []

    def test_snapshot_player_curated_fields(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
        )

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        player = evaluator.calls[0]["snapshot"]["player"]
        expected_keys = {
            "character_id", "character_name", "level", "hp", "max_hp",
            "gold", "character_class", "current_area", "current_location",
            "guild_rank", "ac", "active_quests", "tags",
        }
        assert set(player.keys()) == expected_keys
        assert player["current_area"] == "forest"
        assert player["current_location"] == "camp"
        assert player["active_quests"] == []
        assert player["tags"] == []

    def test_snapshot_player_with_quests_and_tags(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
            include_characters=True,
            include_quests=True,
        )

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        player = evaluator.calls[0]["snapshot"]["player"]
        assert player["tags"] == ["ADVENTURER", "NEWCOMER"]
        assert set(player["active_quests"]) == {
            "main_quest_1", "side_quest_1", "dynamic_1",
        }
        assert "completed_quest" not in player["active_quests"]
        assert "dynamic_done" not in player["active_quests"]

    def test_snapshot_includes_pending_events_danger_and_active_dynamic_quests(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
            include_characters=True,
            include_quests=True,
            include_events=True,
            event_pending=[
                {"event_id": "ev_1", "trigger_condition": {"type": "absolute_tick", "tick": 10}}
            ],
            area_danger_level=1.75,
            action_log=[],
        )
        context.state.areas.areas["forest"].temporary_sub_areas.append(
            {"id": "camp", "threat_level": "high"}
        )

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        snapshot = evaluator.calls[0]["snapshot"]
        assert snapshot["pending_events"] == [
            {"event_id": "ev_1", "trigger_condition": {"type": "absolute_tick", "tick": 10}}
        ]
        assert snapshot["danger"]["area_id"] == "forest"
        assert snapshot["danger"]["area_level"] == 1.75
        assert snapshot["danger"]["location_id"] == "camp"
        assert snapshot["danger"]["location_level"] == "high"
        assert snapshot["active_dynamic_quests"] == [
            {"status": "active", "title": "Dynamic Quest"}
        ]

    def test_snapshot_party_enriched_list(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
            include_characters=True,
        )

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        party = evaluator.calls[0]["snapshot"]["party"]
        assert isinstance(party, list)
        assert len(party) == 1
        member = party[0]
        assert member["id"] == "companion_1"
        assert member["approval"] == 65
        assert member["disposition"] == {"trust": 40, "admiration": 30}
        assert member["relationship_stage"] == "acquaintance"
        assert member["name"] == "Companion"
        assert member["tags"] == ["COMPANION", "PALADIN"]
        assert member["faction"] == "temple_order"

    def test_snapshot_party_without_registries(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
        )

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        party = evaluator.calls[0]["snapshot"]["party"]
        assert isinstance(party, list)
        assert len(party) == 1
        member = party[0]
        assert member["id"] == "companion_1"
        assert member["approval"] == 0
        assert "tags" not in member
        assert "faction" not in member
        assert "disposition" not in member

    def test_enriched_actions_extracts_target_from_params(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
            action_log=[
                {
                    "type": "trade_buy",
                    "actor": "player",
                    "params": {"seller_npc": "merchant_tom", "item_id": "dagger"},
                    "executed": True,
                    "time_cost": 1.0 / 6.0,
                },
            ],
        )

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        actions = evaluator.calls[0]["summary"]["actions"]
        assert actions[0]["target"] == "merchant_tom"
        assert actions[0]["type"] == "trade_buy"
        assert actions[0]["actor"] == "player"
        assert actions[0]["params"]["item_id"] == "dagger"

    def test_enriched_actions_adds_category_tags(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
            action_log=[
                {"type": "trade_buy", "actor": "player", "params": {}, "executed": True, "time_cost": 0.0},
                {"type": "steal", "actor": "player", "params": {}, "executed": True, "time_cost": 0.0},
                {"type": "move_area", "actor": "player", "params": {}, "executed": True, "time_cost": 0.0},
            ],
        )

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        actions = evaluator.calls[0]["summary"]["actions"]
        assert "TRANSACTION" in actions[0]["tags"]
        assert "CRIME" in actions[1]["tags"]
        assert "THEFT" in actions[1]["tags"]
        assert "NAVIGATION" in actions[2]["tags"]

    def test_enriched_actions_adds_item_tags_from_registry(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
            action_log=[
                {
                    "type": "trade_buy",
                    "actor": "player",
                    "params": {"item_id": "dagger"},
                    "executed": True,
                    "time_cost": 0.0,
                },
            ],
        )
        items = ItemRegistry()
        items.load({"dagger": {"id": "dagger", "type": "weapon", "tags": ["melee"]}})
        context.world.register(items)

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        tags = evaluator.calls[0]["summary"]["actions"][0]["tags"]
        assert "TRANSACTION" in tags
        assert "MELEE" in tags
        assert "WEAPON" in tags

    def test_enriched_actions_adds_spell_tags_from_registry(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
            action_log=[
                {
                    "type": "cast_spell",
                    "actor": "player",
                    "params": {"spell_id": "magic_missile"},
                    "executed": True,
                    "time_cost": 0.0,
                },
            ],
        )
        skills = SkillRegistry()
        skills.load({
            "magic_missile": {
                "id": "magic_missile",
                "school": "evocation",
                "effect": {"type": "damage"},
            },
        })
        context.world.register(skills)

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        tags = evaluator.calls[0]["summary"]["actions"][0]["tags"]
        assert "SPELLCASTING" in tags
        assert "EVOCATION" in tags
        assert "DAMAGE" in tags

    def test_enriched_actions_handles_unknown_type_and_missing_registries(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
            action_log=[
                {
                    "type": "unknown_action",
                    "actor": "player",
                    "params": {"target": "npc_1"},
                    "executed": True,
                    "time_cost": 0.0,
                },
            ],
        )

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        action = evaluator.calls[0]["summary"]["actions"][0]
        assert action["target"] == "npc_1"
        assert "tags" not in action

    def test_enriched_actions_detail_trade_with_registry(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
            action_log=[
                {
                    "type": "trade_buy",
                    "actor": "player",
                    "params": {"seller_npc": "merchant_tom", "item_id": "dagger"},
                    "executed": True,
                    "time_cost": 0.0,
                },
            ],
        )
        items = ItemRegistry()
        items.load({"dagger": {"id": "dagger", "name": "Dagger"}})
        context.world.register(items)
        characters = CharacterRegistry()
        characters.load({"merchant_tom": {"id": "merchant_tom", "name": "Merchant Tom"}})
        context.world.register(characters)

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        action = evaluator.calls[0]["summary"]["actions"][0]
        assert action["detail"] == "purchased Dagger from Merchant Tom"

    def test_enriched_actions_detail_skill_check_with_dc(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
            action_log=[
                {
                    "type": "skill_check",
                    "actor": "player",
                    "params": {"skill": "perception", "dc": 15},
                    "executed": True,
                    "time_cost": 0.0,
                },
            ],
        )

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        action = evaluator.calls[0]["summary"]["actions"][0]
        assert action["detail"] == "perception check (DC 15)"

    def test_enriched_actions_detail_failed_with_hints(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
            action_log=[
                {
                    "type": "attack",
                    "actor": "player",
                    "params": {"target": "goblin"},
                    "executed": False,
                    "time_cost": 0.0,
                    "narrative_hints": ["disastrous failure"],
                },
            ],
        )

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        action = evaluator.calls[0]["summary"]["actions"][0]
        assert "attacked" in action["detail"]
        assert "goblin" in action["detail"]
        assert "disastrous failure" in action["detail"]
        assert "failed" in action["detail"]

    def test_enriched_actions_detail_fallback_unknown_type(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
            action_log=[
                {
                    "type": "some_new_action",
                    "actor": "player",
                    "params": {},
                    "executed": True,
                    "time_cost": 0.0,
                },
            ],
        )

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        action = evaluator.calls[0]["summary"]["actions"][0]
        assert action["detail"] == "some new action"

    def test_enriched_actions_witnessed_by(self) -> None:
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            change_log=[
                StateChange(slice="flags", operation="set", path="flags.x", value=1)
            ],
            include_characters=True,
            action_log=[
                {
                    "type": "steal",
                    "actor": "player",
                    "params": {"container_id": "chest_01"},
                    "executed": True,
                    "time_cost": 0.0,
                },
            ],
        )

        asyncio.run(AIOsirisHook(evaluator=evaluator).execute(context))

        action = evaluator.calls[0]["summary"]["actions"][0]
        witnesses = action["witnessed_by"]
        # companion_1 is party member → witness
        assert "companion_1" in witnesses
        # npc_guard has area_id=forest matching player's current_area → witness
        assert "npc_guard" in witnesses
        # target is chest_01 (not a character) so it doesn't appear in witnesses
        # npc_far is in city → not witness
        assert "npc_far" not in witnesses
