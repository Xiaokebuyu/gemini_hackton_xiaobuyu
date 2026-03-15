"""Tests for P32 Phase 3a/3b/3c: Planner pending_topic blackboard + NPC prompt injection.

Phase 3a: NpcDirectorSubSystem._apply_direct_npc writes pending_topic to NPC blackboard.
Phase 3b: NpcInteractionCoordinator injects pending_topic into NPC system prompt,
          clears it after successful interaction.
Phase 3c: DirectiveTriggerHook SSE payload includes 'topic' field from directive.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.bootstrap import build_default_world, build_runtime_for_world
from app.game_core.content import WorldInstance
from app.game_core.narrative.executor import AgenticExecutor
from app.game_core.narrative.gm_tools import register_gm_tools
from app.game_core.narrative.character_tools import register_npc_tools, register_teammate_tools
from app.game_core.narrative.registry import RoleToolRegistry
from app.game_core.orchestration.npc_interaction import (
    NpcInteractionCoordinator,
    _build_pending_topic_prompt,
)
from app.game_core.planning.npc_director import NpcDirectorSubSystem
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateContainer


# ---------------------------------------------------------------------------
# Helpers shared across phases
# ---------------------------------------------------------------------------


def _test_world_tags() -> dict[str, Any]:
    return {
        "profession": {"id": "profession", "tags": ["merchant"]},
        "ancestry": {"id": "ancestry", "tags": ["human"]},
    }


def _world_with_npc() -> WorldInstance:
    return build_default_world(
        "test_world",
        world_data={
            "tags": _test_world_tags(),
            "characters": {
                "guard_npc": {
                    "id": "guard_npc",
                    "name": "Guard Helm",
                    "personality": "A stern gate guard.",
                    "tags": ["human"],
                    "response_tendency": 0.0,
                },
            },
        },
    )


def _state_with_relations(world: WorldInstance) -> StateContainer:
    runtime = build_runtime_for_world(world)
    state = runtime.state
    state.player.restore({
        "character_name": "Hero",
        "current_area": "frontier_town",
        "current_location": "main_gate",
    })
    state.relations.restore({
        "npc_dispositions": {
            "guard_npc": {"approval": 10, "trust": 5, "fear": 0, "romance": 0},
        },
        "relationship_stages": {"guard_npc": "acquaintance"},
    })
    return state


def _noop_executor(command: Command) -> ExecuteResult:
    return ExecuteResult(executed=True)


class _FakeCommandResult:
    executed = True
    errors: list = []
    metadata: dict = {}


class _FakeContext:
    """Minimal SettlementContext substitute for NpcDirectorSubSystem tests."""

    def __init__(self, state: StateContainer) -> None:
        self.state = state
        self._commands: list[Command] = []

    def execute_command(self, cmd: Command) -> _FakeCommandResult:
        self._commands.append(cmd)
        return _FakeCommandResult()


# ---------------------------------------------------------------------------
# Phase 3a: NpcDirectorSubSystem writes pending_topic to blackboard
# ---------------------------------------------------------------------------


class TestPhase3aDirectorBlackboard:
    def test_pending_topic_written_when_directive_has_topic(self) -> None:
        """direct_npc with topic writes pending_topic to NPC blackboard."""
        world = _world_with_npc()
        state = _state_with_relations(world)
        ctx = _FakeContext(state)

        director = NpcDirectorSubSystem()

        # Simulate the post-execute_command part of _apply_direct_npc by calling it
        # indirectly. We'll patch the execute_command to return success so the
        # director proceeds to the blackboard write.
        payload = {
            "npc_id": "guard_npc",
            "directive": {"kind": "talk", "topic": "防守方式"},
        }
        # The director calls context.execute_command with planner_direct_npc.
        # Our FakeContext returns success, so the blackboard write runs.
        director._apply_direct_npc(payload, ctx, current_tick=5)  # type: ignore[arg-type]

        bb = state.relations.get_blackboard("guard_npc")
        assert bb.get("pending_topic") == "防守方式"

    def test_pending_topic_written_for_npc_goal_fallback(self) -> None:
        """When directive has npc_goal but not topic, npc_goal is used as pending_topic."""
        world = _world_with_npc()
        state = _state_with_relations(world)
        ctx = _FakeContext(state)
        director = NpcDirectorSubSystem()

        payload = {
            "npc_id": "guard_npc",
            "directive": {"kind": "approach", "npc_goal": "向玩家打招呼"},
        }
        director._apply_direct_npc(payload, ctx, current_tick=5)  # type: ignore[arg-type]

        bb = state.relations.get_blackboard("guard_npc")
        assert bb.get("pending_topic") == "向玩家打招呼"

    def test_no_pending_topic_when_directive_has_no_topic(self) -> None:
        """Directive without topic/npc_goal does not write pending_topic."""
        world = _world_with_npc()
        state = _state_with_relations(world)
        ctx = _FakeContext(state)
        director = NpcDirectorSubSystem()

        payload = {
            "npc_id": "guard_npc",
            "directive": {"kind": "move"},
        }
        director._apply_direct_npc(payload, ctx, current_tick=5)  # type: ignore[arg-type]

        bb = state.relations.get_blackboard("guard_npc")
        # pending_topic should not be set (or empty)
        assert not bb.get("pending_topic")

    def test_topic_also_added_to_goals_list(self) -> None:
        """pending_topic write also appends to the goals list in the blackboard."""
        world = _world_with_npc()
        state = _state_with_relations(world)
        ctx = _FakeContext(state)
        director = NpcDirectorSubSystem()

        payload = {
            "npc_id": "guard_npc",
            "directive": {"kind": "talk", "topic": "北门的可疑人"},
        }
        director._apply_direct_npc(payload, ctx, current_tick=5)  # type: ignore[arg-type]

        bb = state.relations.get_blackboard("guard_npc")
        goals = bb.get("goals", [])
        assert isinstance(goals, list)
        assert "北门的可疑人" in goals
        assert bb.get("pending_topic") == "北门的可疑人"

    def test_duplicate_goal_not_appended_twice(self) -> None:
        """Calling apply twice with same topic does not duplicate it in goals."""
        world = _world_with_npc()
        state = _state_with_relations(world)
        ctx = _FakeContext(state)
        director = NpcDirectorSubSystem()

        payload = {
            "npc_id": "guard_npc",
            "directive": {"kind": "talk", "topic": "同一话题"},
        }
        director._apply_direct_npc(payload, ctx, current_tick=5)  # type: ignore[arg-type]
        director._apply_direct_npc(payload, ctx, current_tick=6)  # type: ignore[arg-type]

        bb = state.relations.get_blackboard("guard_npc")
        assert bb.get("goals", []).count("同一话题") == 1


# ---------------------------------------------------------------------------
# Phase 3b helpers: _build_pending_topic_prompt
# ---------------------------------------------------------------------------


class TestPendingTopicPromptHelper:
    def test_prompt_contains_topic_text(self) -> None:
        prompt = _build_pending_topic_prompt("防守方式")
        assert "防守方式" in prompt

    def test_prompt_has_section_header(self) -> None:
        prompt = _build_pending_topic_prompt("某话题")
        assert "## 你有话想对玩家说" in prompt

    def test_prompt_is_nonempty_string(self) -> None:
        prompt = _build_pending_topic_prompt("x")
        assert isinstance(prompt, str) and len(prompt) > 0


# ---------------------------------------------------------------------------
# Phase 3b integration: NpcInteractionCoordinator injects and clears topic
# ---------------------------------------------------------------------------


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


def _npc_speak_response(text: str = "Hello.") -> dict[str, Any]:
    return {
        "tool_calls": [{"name": "speak", "args": {"text": text}}],
        "finish_reason": "tool_calls",
    }


def _stop_response(text: str = "") -> dict[str, Any]:
    return {"text": text, "finish_reason": "stop"}


def _build_coordinator_with_state(
    state: StateContainer,
    world: WorldInstance,
    llm_responses: list[dict[str, Any]] | None = None,
) -> tuple[NpcInteractionCoordinator, RecordingLlmProvider]:
    llm = RecordingLlmProvider(llm_responses)
    registry = RoleToolRegistry()
    register_gm_tools(registry)
    register_npc_tools(registry)
    register_teammate_tools(registry)
    executor = AgenticExecutor(tool_registry=registry, llm=llm)
    coordinator = NpcInteractionCoordinator(executor, world, state)
    return coordinator, llm


class TestPhase3bNpcInteractionTopicInjection:
    def test_pending_topic_injected_into_system_prompt(self) -> None:
        """When NPC has pending_topic in blackboard, it appears in the LLM system prompt."""
        world = _world_with_npc()
        state = _state_with_relations(world)

        # Set pending_topic on the blackboard
        state.relations.update_blackboard("guard_npc", {"pending_topic": "北城门的威胁"})

        coordinator, llm = _build_coordinator_with_state(
            state, world,
            llm_responses=[
                _npc_speak_response("我需要和你谈谈北城门。"),
                _stop_response(),
            ],
        )

        result = asyncio.run(coordinator.execute_interaction(
            npc_id="guard_npc",
            player_message="你好！",
            execute_command=_noop_executor,
        ))

        assert result.completed is True
        # The NPC agent was called — check the system_prompt seen by LLM
        assert len(llm.calls) >= 1
        npc_call_prompt = llm.calls[0]["system_prompt"]
        assert "北城门的威胁" in npc_call_prompt
        assert "## 你有话想对玩家说" in npc_call_prompt

    def test_pending_topic_cleared_after_successful_interaction(self) -> None:
        """After a successful NPC interaction, pending_topic is cleared from blackboard."""
        world = _world_with_npc()
        state = _state_with_relations(world)
        state.relations.update_blackboard("guard_npc", {"pending_topic": "清除这个话题"})

        coordinator, _ = _build_coordinator_with_state(
            state, world,
            llm_responses=[
                _npc_speak_response("已处理。"),
                _stop_response(),
            ],
        )

        result = asyncio.run(coordinator.execute_interaction(
            npc_id="guard_npc",
            player_message="请说。",
            execute_command=_noop_executor,
        ))

        assert result.completed is True
        bb = state.relations.get_blackboard("guard_npc")
        # pending_topic should be cleared (empty string or absent)
        assert not bb.get("pending_topic")

    def test_no_topic_injection_when_blackboard_empty(self) -> None:
        """Without pending_topic, system prompt does NOT include the topic section."""
        world = _world_with_npc()
        state = _state_with_relations(world)
        # No blackboard set — clean state

        coordinator, llm = _build_coordinator_with_state(
            state, world,
            llm_responses=[
                _npc_speak_response("普通对话。"),
                _stop_response(),
            ],
        )

        asyncio.run(coordinator.execute_interaction(
            npc_id="guard_npc",
            player_message="嗨",
            execute_command=_noop_executor,
        ))

        npc_call_prompt = llm.calls[0]["system_prompt"]
        assert "## 你有话想对玩家说" not in npc_call_prompt

    def test_pending_topic_not_cleared_when_npc_not_found(self) -> None:
        """If interaction fails at NPC-not-found, blackboard is unchanged."""
        world = _world_with_npc()
        state = _state_with_relations(world)
        state.relations.update_blackboard("guard_npc", {"pending_topic": "保持这个"})

        coordinator, _ = _build_coordinator_with_state(state, world)

        result = asyncio.run(coordinator.execute_interaction(
            npc_id="nonexistent_npc",
            player_message="Hi",
            execute_command=_noop_executor,
        ))

        assert result.completed is False
        # blackboard for guard_npc should still have the topic (different NPC)
        bb = state.relations.get_blackboard("guard_npc")
        assert bb.get("pending_topic") == "保持这个"


# ---------------------------------------------------------------------------
# Phase 3c: DirectiveTriggerHook SSE payload includes 'topic' field
# ---------------------------------------------------------------------------


class TestPhase3cDirectiveTriggerTopic:
    """Tests for DirectiveTriggerHook including topic in SSE payload."""

    def _make_context(self, directive_entry: dict[str, Any]) -> Any:
        """Build a minimal SettlementContext-like fake for DirectiveTriggerHook."""
        from app.game_core.orchestration.settlement import SettlementContext

        world = _world_with_npc()
        runtime = build_runtime_for_world(world)
        state = runtime.state

        state.player.restore({
            "current_area": "frontier_town",
            "current_location": "main_gate",
        })
        # Place the NPC in the same area
        state.areas.move_npc("guard_npc", "frontier_town", "main_gate")

        # Set up narrative_plan with the directive
        state.narrative_plan.restore({
            "npc_directives": [directive_entry],
        })

        class _FakeSettlementContext:
            def __init__(self) -> None:
                self.state = state
                self.world = world
                self._commands: list = []

            def execute_command(self, cmd: Command) -> _FakeCommandResult:
                self._commands.append(cmd)
                return _FakeCommandResult()

        return _FakeSettlementContext()

    def test_topic_included_in_sse_payload_when_present(self) -> None:
        """npc_wants_to_chat SSE payload includes 'topic' from directive."""
        from app.game_core.orchestration.hooks.directive_trigger import DirectiveTriggerHook
        from unittest.mock import patch
        import app.game_core.orchestration.hooks.directive_trigger as _dt_mod

        directive_entry = {
            "npc_id": "guard_npc",
            "consumed": False,
            "expires_at_tick": 999,
            "priority": "high",
            "directive": {"kind": "talk", "topic": "城门防守计划"},
        }
        ctx = self._make_context(directive_entry)

        hook = DirectiveTriggerHook()

        # Force probability gate to pass
        with patch.object(_dt_mod.random, "random", return_value=0.0):
            result = asyncio.run(hook.execute(ctx))  # type: ignore[arg-type]

        # Find the npc_wants_to_chat event
        chat_events = [e for e in result.sse_events if e.event_type == "npc_wants_to_chat"]
        assert len(chat_events) == 1
        payload = chat_events[0].payload
        assert payload.get("topic") == "城门防守计划"

    def test_topic_absent_when_directive_has_no_topic(self) -> None:
        """npc_wants_to_chat SSE payload has no 'topic' when directive lacks one."""
        from app.game_core.orchestration.hooks.directive_trigger import DirectiveTriggerHook
        from unittest.mock import patch
        import app.game_core.orchestration.hooks.directive_trigger as _dt_mod

        directive_entry = {
            "npc_id": "guard_npc",
            "consumed": False,
            "expires_at_tick": 999,
            "priority": "high",
            "directive": {"kind": "move"},
        }
        ctx = self._make_context(directive_entry)

        hook = DirectiveTriggerHook()

        with patch.object(_dt_mod.random, "random", return_value=0.0):
            result = asyncio.run(hook.execute(ctx))  # type: ignore[arg-type]

        chat_events = [e for e in result.sse_events if e.event_type == "npc_wants_to_chat"]
        if chat_events:
            # topic key should not be present
            assert "topic" not in chat_events[0].payload

    def test_topic_uses_npc_goal_when_topic_absent(self) -> None:
        """If directive has npc_goal but not topic, npc_goal is used as topic."""
        from app.game_core.orchestration.hooks.directive_trigger import DirectiveTriggerHook
        from unittest.mock import patch
        import app.game_core.orchestration.hooks.directive_trigger as _dt_mod

        directive_entry = {
            "npc_id": "guard_npc",
            "consumed": False,
            "expires_at_tick": 999,
            "priority": "high",
            "directive": {"kind": "approach", "npc_goal": "向玩家报告情报"},
        }
        ctx = self._make_context(directive_entry)

        hook = DirectiveTriggerHook()

        with patch.object(_dt_mod.random, "random", return_value=0.0):
            result = asyncio.run(hook.execute(ctx))  # type: ignore[arg-type]

        chat_events = [e for e in result.sse_events if e.event_type == "npc_wants_to_chat"]
        assert len(chat_events) == 1
        assert chat_events[0].payload.get("topic") == "向玩家报告情报"
