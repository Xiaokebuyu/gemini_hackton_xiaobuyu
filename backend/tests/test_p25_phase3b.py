"""Tests for Phase 3b: check constraint injection into NPC system prompt.

Covers:
- 3b-4: _format_check_constraint text generation + injection into NPC system prompt

Note: Osiris locality prompt tests (3b-1) and nearby_npcs filtering tests (3b-2/3b-3)
have been removed — they depended on LLM evaluator inspection which was deleted when
AIOsirisHook was migrated to MechanicalOsirisEngine.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.content.registries import CharacterRegistry
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
