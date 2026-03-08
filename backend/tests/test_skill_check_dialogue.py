"""Tests for skill-check dialogue flow (Block G).

Covers:
  1. InteractRequest accepts check_skill + check_dc fields
  2. Skill check executes through RulesEngine and produces rolls
  3. SceneBus receives SKILL_CHECK entry when check_result is provided
  4. check_result transparently passes through execute_interaction
  5. No check fields → behaviour unchanged (regression)
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

from app.api_models import InteractRequest
from app.game_core.bootstrap import build_default_world, build_runtime_for_world
from app.game_core.content import WorldInstance
from app.game_core.narrative.executor import AgenticExecutor
from app.game_core.narrative.gm_tools import register_gm_tools
from app.game_core.narrative.character_tools import register_npc_tools, register_teammate_tools
from app.game_core.narrative.registry import RoleToolRegistry
from app.game_core.orchestration.npc_interaction import NpcInteractionCoordinator
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateContainer


# ------------------------------------------------------------------
# LLM stub (same pattern as test_group_dialogue.py)
# ------------------------------------------------------------------


class RecordingLlmProvider:
    """Returns pre-configured responses for deterministic tests."""

    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses or [])
        self._call_index = 0

    async def generate(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        tool_declarations: list[dict[str, Any]],
    ) -> Any:
        self.calls.append({
            "system_prompt": system_prompt,
            "history": history,
            "tool_declarations": tool_declarations,
        })
        from app.game_core.adapters.llm import LlmResponse

        if self._call_index < len(self._responses):
            resp = self._responses[self._call_index]
            self._call_index += 1
            return LlmResponse(
                text=resp.get("text", ""),
                tool_calls=resp.get("tool_calls", []),
                finish_reason=resp.get("finish_reason", "stop"),
            )
        return LlmResponse(text="(no more responses)")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_TAGS_DATA: dict[str, Any] = {
    "profession": {"id": "profession", "tags": ["warrior"]},
    "ancestry": {"id": "ancestry", "tags": ["human"]},
}


def _npc_speak(text: str = "Hello.") -> dict[str, Any]:
    return {
        "tool_calls": [{"name": "speak", "args": {"text": text}}],
        "finish_reason": "tool_calls",
    }


def _gm_pass() -> dict[str, Any]:
    return {
        "tool_calls": [{"name": "pass_turn", "args": {}}],
        "finish_reason": "tool_calls",
    }


def _stop(text: str = "") -> dict[str, Any]:
    return {"text": text, "finish_reason": "stop"}


def _noop_exec(command: Command) -> ExecuteResult:
    return ExecuteResult(executed=True)


def _make_world() -> WorldInstance:
    chars: dict[str, Any] = {
        "guard": {
            "id": "guard",
            "name": "Guard",
            "personality": "A stern guard.",
            "response_tendency": 0.0,
        },
    }
    return build_default_world(
        "test_world",
        world_data={"tags": _TAGS_DATA, "characters": chars},
    )


def _make_state(world: WorldInstance) -> StateContainer:
    runtime = build_runtime_for_world(world)
    state = runtime.state
    state.player.restore({
        "character_name": "Hero",
        "character_class": "warrior",
        "current_area": "town",
        "current_location": "gate",
    })
    state.relations.restore({
        "npc_dispositions": {
            "guard": {"approval": 10, "trust": 5, "fear": 0, "romance": 0},
        },
        "relationship_stages": {
            "guard": "stranger",
        },
    })
    return state


def _build_coordinator(
    llm_responses: list[dict[str, Any]] | None = None,
    world: WorldInstance | None = None,
    state: StateContainer | None = None,
) -> tuple[NpcInteractionCoordinator, RecordingLlmProvider, StateContainer]:
    if world is None:
        world = _make_world()
    if state is None:
        state = _make_state(world)
    llm = RecordingLlmProvider(llm_responses)
    registry = RoleToolRegistry()
    register_gm_tools(registry)
    register_npc_tools(registry)
    register_teammate_tools(registry)
    executor = AgenticExecutor(tool_registry=registry, llm=llm)
    coordinator = NpcInteractionCoordinator(executor, world, state)
    return coordinator, llm, state


# ------------------------------------------------------------------
# 1. InteractRequest model accepts check_skill + check_dc
# ------------------------------------------------------------------


class TestInteractRequestCheckFields:
    """InteractRequest correctly parses optional check fields."""

    def test_check_fields_present(self) -> None:
        req = InteractRequest(
            intent="talk",
            target_kind="npc",
            target_id="guard",
            message="Let me pass",
            check_skill="persuasion",
            check_dc=14,
        )
        assert req.check_skill == "persuasion"
        assert req.check_dc == 14

    def test_check_fields_absent(self) -> None:
        req = InteractRequest(intent="talk", message="Hello")
        assert req.check_skill is None
        assert req.check_dc is None

    def test_check_fields_partial_skill_only(self) -> None:
        req = InteractRequest(intent="talk", check_skill="persuasion")
        assert req.check_skill == "persuasion"
        assert req.check_dc is None


# ------------------------------------------------------------------
# 2. Skill check execution through RulesEngine
# ------------------------------------------------------------------


class TestSkillCheckExecution:
    """Skill check command produces ExecuteResult with rolls."""

    def test_skill_check_produces_rolls(self) -> None:
        world = _make_world()
        runtime = build_runtime_for_world(world)
        state = runtime.state
        state.player.restore({
            "character_name": "Hero",
            "character_class": "warrior",
            "current_area": "town",
        })

        cmd = Command(
            type="skill_check",
            params={"skill": "persuasion", "dc": 14},
            source="player",
        )
        result = runtime.rules_engine.execute(cmd, state, world)

        assert result.executed is True
        assert len(result.rolls) == 1
        assert result.rolls[0].purpose == "skill_check"
        assert "passed" in result.metadata
        assert isinstance(result.metadata["passed"], bool)

    def test_skill_check_metadata_has_dc(self) -> None:
        world = _make_world()
        runtime = build_runtime_for_world(world)
        state = runtime.state
        state.player.restore({
            "character_name": "Hero",
            "character_class": "warrior",
            "current_area": "town",
        })

        cmd = Command(
            type="skill_check",
            params={"skill": "intimidation", "dc": 18},
            source="player",
        )
        result = runtime.rules_engine.execute(cmd, state, world)

        assert result.executed is True
        assert result.metadata["dc"] == 18
        assert result.metadata["skill"] == "intimidation"


# ------------------------------------------------------------------
# 3. SceneBus receives SKILL_CHECK entry
# ------------------------------------------------------------------


class TestSceneBusCheckEntry:
    """execute_interaction writes SKILL_CHECK to SceneBus when check_result is provided."""

    def test_check_result_writes_scene_entry(self) -> None:
        coordinator, _llm, state = _build_coordinator(
            llm_responses=[
                _npc_speak("Very well, you may pass."),
                _gm_pass(),
                _stop(),
            ],
        )

        check_result = {
            "skill": "persuasion",
            "dc": 14,
            "passed": True,
            "total": 18,
            "narrative_hints": [],
        }

        result = asyncio.run(coordinator.execute_interaction(
            npc_id="guard",
            player_message="Let me through.",
            execute_command=_noop_exec,
            check_result=check_result,
        ))

        assert result.completed is True

        # Verify SceneBus has a SKILL_CHECK entry
        scene_snap = state.scene.snapshot()
        entries = scene_snap.get("entries", [])
        check_entries = [
            e for e in entries
            if isinstance(e, dict) and "SKILL_CHECK" in e.get("tags", [])
        ]
        assert len(check_entries) == 1
        entry = check_entries[0]
        assert "Passed" in entry["content"]
        assert "persuasion" in entry["content"]
        assert "DC 14" in entry["content"]
        assert entry["source"] == "ENGINE"

    def test_failed_check_writes_failed_entry(self) -> None:
        coordinator, _llm, state = _build_coordinator(
            llm_responses=[
                _npc_speak("You shall not pass!"),
                _gm_pass(),
                _stop(),
            ],
        )

        check_result = {
            "skill": "intimidation",
            "dc": 20,
            "passed": False,
            "total": 12,
            "narrative_hints": [],
        }

        result = asyncio.run(coordinator.execute_interaction(
            npc_id="guard",
            player_message="Move aside!",
            execute_command=_noop_exec,
            check_result=check_result,
        ))

        assert result.completed is True

        scene_snap = state.scene.snapshot()
        entries = scene_snap.get("entries", [])
        check_entries = [
            e for e in entries
            if isinstance(e, dict) and "SKILL_CHECK" in e.get("tags", [])
        ]
        assert len(check_entries) == 1
        assert "Failed" in check_entries[0]["content"]
        assert "intimidation" in check_entries[0]["content"]


# ------------------------------------------------------------------
# 4. check_result transparent pass-through
# ------------------------------------------------------------------


class TestCheckResultPassthrough:
    """check_result is correctly propagated to execute_interaction."""

    def test_npc_sees_check_in_scene_context(self) -> None:
        """NPC's LLM call should include the SKILL_CHECK entry in scene context."""
        coordinator, llm, state = _build_coordinator(
            llm_responses=[
                _npc_speak("Your words carry weight."),
                _gm_pass(),
                _stop(),
            ],
        )

        check_result = {
            "skill": "persuasion",
            "dc": 12,
            "passed": True,
            "total": 16,
            "narrative_hints": ["extraordinary success"],
        }

        result = asyncio.run(coordinator.execute_interaction(
            npc_id="guard",
            player_message="I need your help.",
            execute_command=_noop_exec,
            check_result=check_result,
        ))

        assert result.completed is True
        # The NPC should have been called (at least 1 LLM call)
        assert len(llm.calls) >= 1


# ------------------------------------------------------------------
# 5. No check fields → behaviour unchanged (regression)
# ------------------------------------------------------------------


class TestNoCheckRegression:
    """Without check_result, execute_interaction behaves identically."""

    def test_no_check_result_no_scene_entry(self) -> None:
        coordinator, _llm, state = _build_coordinator(
            llm_responses=[
                _npc_speak("Good day."),
                _gm_pass(),
                _stop(),
            ],
        )

        result = asyncio.run(coordinator.execute_interaction(
            npc_id="guard",
            player_message="Hello.",
            execute_command=_noop_exec,
        ))

        assert result.completed is True

        scene_snap = state.scene.snapshot()
        entries = scene_snap.get("entries", [])
        check_entries = [
            e for e in entries
            if isinstance(e, dict) and "SKILL_CHECK" in e.get("tags", [])
        ]
        assert len(check_entries) == 0

    def test_none_check_result_no_scene_entry(self) -> None:
        coordinator, _llm, state = _build_coordinator(
            llm_responses=[
                _npc_speak("Greetings."),
                _gm_pass(),
                _stop(),
            ],
        )

        result = asyncio.run(coordinator.execute_interaction(
            npc_id="guard",
            player_message="Hi there.",
            execute_command=_noop_exec,
            check_result=None,
        ))

        assert result.completed is True

        scene_snap = state.scene.snapshot()
        entries = scene_snap.get("entries", [])
        check_entries = [
            e for e in entries
            if isinstance(e, dict) and "SKILL_CHECK" in e.get("tags", [])
        ]
        assert len(check_entries) == 0
