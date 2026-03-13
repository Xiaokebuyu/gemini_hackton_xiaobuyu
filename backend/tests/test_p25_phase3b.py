"""Tests for Phase 3b: Osiris locality + check constraint injection.

Covers:
- 3b-1: OSIRIS_SYSTEM_PROMPT contains locality constraints
- 3b-2: nearby_npcs sub-location filtering (has sub_location → only return local NPCs)
- 3b-3: modify_disposition blocked for absent NPCs in execute()
- 3b-4: _format_check_constraint text generation + injection into NPC system prompt
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.evaluators import OSIRIS_SYSTEM_PROMPT
from app.game_core.content import WorldInstance
from app.game_core.content.registries import CharacterRegistry
from app.game_core.orchestration.hooks.ai_osiris import AIOsirisHook
from app.game_core.orchestration.npc_interaction import NpcInteractionCoordinator
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
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
    TimeSlice,
)


# ------------------------------------------------------------------
# Helper: build a minimal SettlementContext
# ------------------------------------------------------------------

class RecordingEvaluator:
    def __init__(self, decision: Any) -> None:
        self.decision = decision
        self.calls: list[dict[str, Any]] = []

    async def evaluate(self, summary: Any, snapshot: Any, rules_context: Any) -> Any:
        self.calls.append({"summary": summary, "snapshot": snapshot})
        return self.decision


def _make_context(
    *,
    area_npc_locations: dict[str, str | None] | None = None,
    current_location: str | None = "main_hall",
    include_characters: bool = False,
    char_data: dict[str, dict] | None = None,
    change_log: list[StateChange] | None = None,
) -> SettlementContext:
    world = WorldInstance("test_world")
    if include_characters or char_data:
        characters = CharacterRegistry()
        data = char_data or {
            "npc_local": {
                "id": "npc_local",
                "name": "Local NPC",
                "area_id": "forest",
                "location_id": "main_hall",
            },
            "npc_other": {
                "id": "npc_other",
                "name": "Other NPC",
                "area_id": "forest",
                "location_id": "back_room",
            },
            "npc_far": {
                "id": "npc_far",
                "name": "Far NPC",
                "area_id": "city",
            },
        }
        characters.load(data)
        world.register(characters)

    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": 5})
    state.register(time_slice)

    player = PlayerSlice()
    player.restore({"current_area": "forest", "current_location": current_location})
    state.register(player)

    flags = FlagSlice()
    flags.restore({"flags": {}})
    state.register(flags)

    relations = RelationSlice()
    relations.restore({"faction_standings": {}})
    state.register(relations)

    areas = AreaSlice()
    area_data: dict[str, Any] = {}
    if area_npc_locations is not None:
        area_data = {"areas": {"forest": {"npc_locations": area_npc_locations}}}
    areas.restore(area_data)
    state.register(areas)

    party = PartySlice()
    party.restore({"members": {}})
    state.register(party)

    narrative_plan = NarrativePlanSlice()
    narrative_plan.restore({"current_chapter": "ch1"})
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
        action_log=[],
    )


# ------------------------------------------------------------------
# 3b-1: OSIRIS_SYSTEM_PROMPT locality constraints
# ------------------------------------------------------------------


class TestOsirisLocalityPrompt:
    def test_locality_constraints_section_present(self) -> None:
        assert "## Locality constraints" in OSIRIS_SYSTEM_PROMPT

    def test_locality_constraint_present_character_ids(self) -> None:
        assert "present_character_ids" in OSIRIS_SYSTEM_PROMPT

    def test_locality_constraint_modify_disposition_rule(self) -> None:
        assert "modify_disposition MUST only target NPCs in present_character_ids" in OSIRIS_SYSTEM_PROMPT

    def test_locality_constraint_create_rumor_alternative(self) -> None:
        assert "create_rumor" in OSIRIS_SYSTEM_PROMPT
        assert "distant NPCs" in OSIRIS_SYSTEM_PROMPT

    def test_locality_constraint_nearby_definition(self) -> None:
        assert "same sub_location" in OSIRIS_SYSTEM_PROMPT


# ------------------------------------------------------------------
# 3b-2: nearby_npcs sub-location filtering
# ------------------------------------------------------------------


class TestNearbyNpcsSubLocationFilter:
    def test_with_sublocation_only_returns_local_dynamic_npcs(self) -> None:
        """When player has a sub_location, only NPCs in same sub_location returned (dynamic)."""
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            area_npc_locations={
                "npc_local": "main_hall",    # same sub_location as player
                "npc_other": "back_room",    # different sub_location
            },
            current_location="main_hall",
            change_log=[StateChange(slice="flags", operation="set", path="flags.x", value=1)],
        )

        async def _run() -> None:
            await AIOsirisHook(evaluator=evaluator).execute(context)

        asyncio.run(_run())

        snapshot = evaluator.calls[0]["snapshot"]
        present_ids = snapshot["scene_presence"]["present_character_ids"]
        assert "npc_local" in present_ids
        assert "npc_other" not in present_ids

    def test_without_sublocation_returns_all_area_dynamic_npcs(self) -> None:
        """When player has no sub_location, all NPCs in the same area are returned."""
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            area_npc_locations={
                "npc_local": "main_hall",
                "npc_other": "back_room",
            },
            current_location=None,
            change_log=[StateChange(slice="flags", operation="set", path="flags.x", value=1)],
        )

        async def _run() -> None:
            await AIOsirisHook(evaluator=evaluator).execute(context)

        asyncio.run(_run())

        snapshot = evaluator.calls[0]["snapshot"]
        present_ids = snapshot["scene_presence"]["present_character_ids"]
        assert "npc_local" in present_ids
        assert "npc_other" in present_ids

    def test_with_sublocation_only_returns_local_static_npcs(self) -> None:
        """When player has a sub_location, static (CharacterRegistry) NPCs are also filtered."""
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        # No dynamic NPC locations — rely on static registry
        context = _make_context(
            area_npc_locations={},
            current_location="main_hall",
            include_characters=True,
            # char_data uses defaults: npc_local at main_hall, npc_other at back_room
            change_log=[StateChange(slice="flags", operation="set", path="flags.x", value=1)],
        )

        async def _run() -> None:
            await AIOsirisHook(evaluator=evaluator).execute(context)

        asyncio.run(_run())

        snapshot = evaluator.calls[0]["snapshot"]
        present_ids = snapshot["scene_presence"]["present_character_ids"]
        assert "npc_local" in present_ids
        assert "npc_other" not in present_ids

    def test_without_sublocation_returns_all_static_npcs_in_area(self) -> None:
        """When player has no sub_location, all same-area static NPCs are returned."""
        evaluator = RecordingEvaluator({"consequences": [], "reasoning": "ok"})
        context = _make_context(
            area_npc_locations={},
            current_location=None,
            include_characters=True,
            change_log=[StateChange(slice="flags", operation="set", path="flags.x", value=1)],
        )

        async def _run() -> None:
            await AIOsirisHook(evaluator=evaluator).execute(context)

        asyncio.run(_run())

        snapshot = evaluator.calls[0]["snapshot"]
        present_ids = snapshot["scene_presence"]["present_character_ids"]
        assert "npc_local" in present_ids
        assert "npc_other" in present_ids
        # NPC in different area must never appear
        assert "npc_far" not in present_ids


# ------------------------------------------------------------------
# 3b-3: modify_disposition blocked for absent NPCs
# ------------------------------------------------------------------


class TestModifyDispositionLocalityCheck:
    def test_modify_disposition_for_absent_npc_is_blocked(self) -> None:
        """modify_disposition targeting an NPC NOT in present_character_ids is skipped."""
        # Only npc_local will be in present_ids (same sub_location as player at main_hall)
        # npc_other is at back_room → absent → locality check should block it
        evaluator = RecordingEvaluator({
            "consequences": [
                {
                    "type": "modify_disposition",
                    "params": {
                        "npc_id": "npc_other",
                        "dimension": "approval",
                        "delta": 5,
                    },
                }
            ],
            "reasoning": "npc saw the action",
            "visible_change": False,
        })
        context = _make_context(
            area_npc_locations={"npc_local": "main_hall", "npc_other": "back_room"},
            current_location="main_hall",
            change_log=[StateChange(slice="flags", operation="set", path="flags.x", value=1)],
        )

        async def _run() -> HookResult:
            from app.game_core.orchestration.models import HookResult
            return await AIOsirisHook(evaluator=evaluator).execute(context)

        result = asyncio.run(_run())
        # The absent NPC consequence should have been skipped
        assert result.metadata["skipped_invalid_count"] >= 1

    def test_modify_disposition_for_present_npc_is_not_blocked_by_locality(self) -> None:
        """modify_disposition targeting a present NPC is NOT blocked by locality check.

        The command must have 'dimension' to pass semantic validation; we then
        verify locality doesn't add extra skips on top.
        """
        evaluator = RecordingEvaluator({
            "consequences": [
                {
                    "type": "modify_disposition",
                    "params": {
                        "npc_id": "npc_local",
                        "dimension": "approval",
                        "delta": 5,
                    },
                }
            ],
            "reasoning": "npc saw the action",
            "visible_change": False,
        })
        context = _make_context(
            area_npc_locations={"npc_local": "main_hall"},
            current_location="main_hall",
            change_log=[StateChange(slice="flags", operation="set", path="flags.x", value=1)],
        )

        async def _run() -> Any:
            return await AIOsirisHook(evaluator=evaluator).execute(context)

        result = asyncio.run(_run())
        # skipped_invalid should be 0 (no locality block for present NPC)
        assert result.metadata["skipped_invalid_count"] == 0

    def test_modify_disposition_not_blocked_when_present_ids_empty(self) -> None:
        """When present_ids is empty (no player_id, no NPCs), locality check is bypassed.

        We use a valid modify_disposition payload (with 'dimension') so semantic
        validation passes, then confirm locality doesn't add extra skips.
        """
        evaluator = RecordingEvaluator({
            "consequences": [
                {
                    "type": "modify_disposition",
                    "params": {
                        "npc_id": "some_npc",
                        "dimension": "approval",
                        "delta": 5,
                    },
                }
            ],
            "reasoning": "test",
            "visible_change": False,
        })
        # Build context with no player character_id → present_ids stays empty
        context = _make_context(
            area_npc_locations={},
            current_location=None,
            change_log=[StateChange(slice="flags", operation="set", path="flags.x", value=1)],
        )
        # Clear character_id so present_ids won't have player entry
        context.state.player.restore({"current_area": "forest", "current_location": None, "character_id": ""})

        async def _run() -> Any:
            return await AIOsirisHook(evaluator=evaluator).execute(context)

        result = asyncio.run(_run())
        # With empty present_ids the locality check is bypassed (condition: `if present_ids`)
        # Command may fail at execution level but should not be SKIPPED by locality check
        assert result.metadata["skipped_invalid_count"] == 0


# ------------------------------------------------------------------
# 3b-4: _format_check_constraint text generation
# ------------------------------------------------------------------


class TestFormatCheckConstraint:
    def _make_coordinator(self) -> NpcInteractionCoordinator:
        from app.game_core.narrative.executor import AgenticExecutor
        world = WorldInstance("test_world")
        state = StateContainer()
        executor = AgenticExecutor.__new__(AgenticExecutor)
        return NpcInteractionCoordinator(
            executor=executor,
            world=world,
            state=state,
        )

    def test_persuasion_pass_contains_effect(self) -> None:
        coord = self._make_coordinator()
        text = coord._format_check_constraint({
            "skill": "persuasion",
            "dc": 12,
            "total": 15,
            "passed": True,
        })
        assert "persuasion" in text
        assert "成功" in text
        assert "让步" in text
        assert "DC 12" in text
        assert "15" in text

    def test_persuasion_fail_contains_effect(self) -> None:
        coord = self._make_coordinator()
        text = coord._format_check_constraint({
            "skill": "persuasion",
            "dc": 12,
            "total": 8,
            "passed": False,
        })
        assert "失败" in text
        assert "警觉" in text

    def test_intimidation_pass(self) -> None:
        coord = self._make_coordinator()
        text = coord._format_check_constraint({
            "skill": "intimidation",
            "dc": 14,
            "total": 18,
            "passed": True,
        })
        assert "畏惧" in text
        assert "成功" in text

    def test_intimidation_fail(self) -> None:
        coord = self._make_coordinator()
        text = coord._format_check_constraint({
            "skill": "intimidation",
            "dc": 14,
            "total": 9,
            "passed": False,
        })
        assert "愤怒" in text
        assert "失败" in text

    def test_deception_pass(self) -> None:
        coord = self._make_coordinator()
        text = coord._format_check_constraint({
            "skill": "deception",
            "dc": 13,
            "total": 16,
            "passed": True,
        })
        assert "相信" in text

    def test_deception_fail(self) -> None:
        coord = self._make_coordinator()
        text = coord._format_check_constraint({
            "skill": "deception",
            "dc": 13,
            "total": 7,
            "passed": False,
        })
        assert "识破" in text

    def test_unknown_skill_uses_fallback(self) -> None:
        coord = self._make_coordinator()
        text_pass = coord._format_check_constraint({
            "skill": "athletics",
            "dc": 10,
            "total": 14,
            "passed": True,
        })
        assert "有利" in text_pass

        text_fail = coord._format_check_constraint({
            "skill": "athletics",
            "dc": 10,
            "total": 6,
            "passed": False,
        })
        assert "维持" in text_fail

    def test_constraint_header_present(self) -> None:
        coord = self._make_coordinator()
        text = coord._format_check_constraint({
            "skill": "persuasion",
            "dc": 12,
            "total": 14,
            "passed": True,
        })
        assert "当前检定结果（必须遵守）" in text


# ------------------------------------------------------------------
# 3b-4: check constraint injected into system_prompt
# ------------------------------------------------------------------


class TestCheckConstraintInjection:
    def test_check_result_appended_to_system_prompt(self) -> None:
        """When check_result is provided, system_prompt is extended with constraint text."""
        from app.game_core.bootstrap import build_default_world, build_runtime_for_world
        from app.game_core.narrative.executor import AgenticExecutor
        from app.game_core.rules.models import ExecuteResult

        world = build_default_world(
            "test_world",
            world_data={
                "tags": {"profession": {"id": "profession", "tags": ["merchant"]}},
                "characters": {
                    "merchant_bob": {
                        "id": "merchant_bob",
                        "name": "Merchant Bob",
                        "personality": "Grumpy merchant",
                        "tags": ["merchant"],
                        "response_tendency": 0.0,
                    }
                },
            },
        )
        runtime = build_runtime_for_world(world)
        state = runtime.state
        state.player.restore({"current_area": "town", "current_location": "market"})

        captured_prompts: list[str] = []

        class CapturingExecutor:
            async def run_agentic(self, *, role, context, system_prompt, **kwargs):
                captured_prompts.append(system_prompt)
                from app.game_core.narrative.models import AgentResult, ToolResult
                return AgentResult(tool_results=[
                    ToolResult(ok=True, message="Hello!", metadata={"event_type": "speech"})
                ])

        coord = NpcInteractionCoordinator(
            executor=CapturingExecutor(),
            world=world,
            state=state,
        )

        check = {"skill": "persuasion", "dc": 12, "total": 15, "passed": True}

        async def _run() -> None:
            await coord.execute_interaction(
                "merchant_bob",
                "I need a discount",
                lambda cmd: ExecuteResult(executed=True),
                check_result=check,
            )

        asyncio.run(_run())

        assert len(captured_prompts) > 0, "executor was never called"
        npc_prompt = captured_prompts[0]
        assert "当前检定结果（必须遵守）" in npc_prompt
        assert "成功" in npc_prompt

    def test_no_check_result_does_not_append_constraint(self) -> None:
        """When check_result is None, system_prompt does NOT contain constraint text."""
        from app.game_core.bootstrap import build_default_world, build_runtime_for_world
        from app.game_core.rules.models import ExecuteResult

        world = build_default_world(
            "test_world",
            world_data={
                "tags": {"profession": {"id": "profession", "tags": ["merchant"]}},
                "characters": {
                    "merchant_bob": {
                        "id": "merchant_bob",
                        "name": "Merchant Bob",
                        "personality": "Grumpy merchant",
                        "tags": ["merchant"],
                        "response_tendency": 0.0,
                    }
                },
            },
        )
        runtime = build_runtime_for_world(world)
        state = runtime.state
        state.player.restore({"current_area": "town", "current_location": "market"})

        captured_prompts: list[str] = []

        class CapturingExecutor:
            async def run_agentic(self, *, role, context, system_prompt, **kwargs):
                captured_prompts.append(system_prompt)
                from app.game_core.narrative.models import AgentResult, ToolResult
                return AgentResult(tool_results=[
                    ToolResult(ok=True, message="Hello!", metadata={"event_type": "speech"})
                ])

        coord = NpcInteractionCoordinator(
            executor=CapturingExecutor(),
            world=world,
            state=state,
        )

        async def _run() -> None:
            await coord.execute_interaction(
                "merchant_bob",
                "How are you?",
                lambda cmd: ExecuteResult(executed=True),
                check_result=None,
            )

        asyncio.run(_run())

        assert len(captured_prompts) > 0
        npc_prompt = captured_prompts[0]
        assert "当前检定结果（必须遵守）" not in npc_prompt
