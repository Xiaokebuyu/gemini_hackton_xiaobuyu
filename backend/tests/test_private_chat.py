"""Tests for PrivateChatCoordinator — 4-step private NPC chat pipeline."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

from app.game_core.bootstrap import build_default_world, build_runtime_for_world
from app.game_core.content import WorldInstance
from app.game_core.narrative.context_window import ContextWindow
from app.game_core.narrative.executor import AgenticExecutor
from app.game_core.narrative.gm_tools import register_gm_tools
from app.game_core.narrative.character_tools import register_npc_tools, register_teammate_tools
from app.game_core.narrative.models import AgentResult, ToolResult
from app.game_core.narrative.registry import RoleToolRegistry
from app.game_core.orchestration.private_chat import (
    PrivateChatCoordinator,
    PrivateChatResult,
    _approx_tokens,
    _window_to_history,
)
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateContainer
from app.agent_orchestration import _private_chat_result_to_sse
from app.game_core.orchestration.models import SSEEvent


# ------------------------------------------------------------------
# Stubs / helpers
# ------------------------------------------------------------------


class ThrowingLlmProvider:
    """LLM that always raises — used to test exception-path coordinator semantics."""

    async def generate(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("simulated LLM failure")


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


def _world_with_characters() -> WorldInstance:
    return build_default_world(
        "test_world",
        world_data={
            "characters": {
                "merchant_tom": {
                    "id": "merchant_tom",
                    "name": "Merchant Tom",
                    "personality": "A shrewd but fair merchant.",
                    "dialogue_style": "Speaks with a slight drawl.",
                    "tags": ["merchant", "human"],
                },
            },
        },
    )


def _state_with_relations(world: WorldInstance) -> StateContainer:
    runtime = build_runtime_for_world(world)
    state = runtime.state
    state.player.restore({
        "character_name": "Hero",
        "character_class": "warrior",
        "current_area": "town",
        "current_location": "market",
    })
    state.relations.restore({
        "npc_dispositions": {
            "merchant_tom": {"approval": 25, "trust": 15, "fear": 0, "romance": 0},
        },
        "relationship_stages": {"merchant_tom": "acquaintance"},
        "npc_impressions": {
            "merchant_tom": ["Bought a sword last time"],
        },
    })
    return state


def _noop_executor(command: Command) -> ExecuteResult:
    return ExecuteResult(success=True)


def _build_coordinator(
    llm_responses: list[dict[str, Any]] | None = None,
    world: WorldInstance | None = None,
    state: StateContainer | None = None,
) -> tuple[PrivateChatCoordinator, RecordingLlmProvider]:
    if world is None:
        world = _world_with_characters()
    if state is None:
        state = _state_with_relations(world)
    llm = RecordingLlmProvider(llm_responses)
    registry = RoleToolRegistry()
    register_gm_tools(registry)
    register_npc_tools(registry)
    register_teammate_tools(registry)
    executor = AgenticExecutor(tool_registry=registry, llm=llm)
    coordinator = PrivateChatCoordinator(executor, world, state)
    return coordinator, llm


def _make_private_result(
    npc_result: AgentResult | None = None,
    dialogue_options: list[dict[str, Any]] | None = None,
    success: bool = True,
) -> PrivateChatResult:
    return PrivateChatResult(
        success=success,
        npc_id="merchant_tom",
        npc_result=npc_result,
        dialogue_options=dialogue_options or [
            {"text": "继续交谈", "intent": "talk"},
            {"text": "告别", "intent": "farewell"},
        ],
        time_cost=1 / 6,
    )


# ------------------------------------------------------------------
# TestPrivateChatCoordinator
# ------------------------------------------------------------------


class TestPrivateChatCoordinator:
    def test_returns_failure_when_npc_not_found(self) -> None:
        """World has no characters registry → success=False, error='npc_not_found'."""
        world = build_default_world("empty_world", world_data={})
        runtime = build_runtime_for_world(world)
        state = runtime.state
        state.player.restore({"character_name": "Hero", "current_area": "town"})

        llm = RecordingLlmProvider()
        registry = RoleToolRegistry()
        register_npc_tools(registry)
        executor = AgenticExecutor(tool_registry=registry, llm=llm)
        coordinator = PrivateChatCoordinator(executor, world, state)

        result = asyncio.run(coordinator.execute(
            npc_id="merchant_tom",
            player_message="Hello",
            execute_command=_noop_executor,
        ))

        assert result.success is False
        assert result.error == "npc_not_found"
        assert result.npc_result is None

    def test_npc_found_no_llm_returns_result(self) -> None:
        """No LLM provider → executor degrades gracefully → coordinator returns success=True."""
        world = _world_with_characters()
        state = _state_with_relations(world)
        registry = RoleToolRegistry()
        register_npc_tools(registry)
        executor = AgenticExecutor(tool_registry=registry, llm=None)
        coordinator = PrivateChatCoordinator(executor, world, state)

        result = asyncio.run(coordinator.execute(
            npc_id="merchant_tom",
            player_message="Hello",
            execute_command=_noop_executor,
        ))

        assert result.success is True
        assert result.npc_id == "merchant_tom"

    def test_npc_agent_exception_returns_agent_failed(self) -> None:
        """LLM throws → coordinator returns success=False, error='agent_failed'."""
        world = _world_with_characters()
        state = _state_with_relations(world)
        registry = RoleToolRegistry()
        register_npc_tools(registry)
        executor = AgenticExecutor(tool_registry=registry, llm=ThrowingLlmProvider())
        coordinator = PrivateChatCoordinator(executor, world, state)

        result = asyncio.run(coordinator.execute(
            npc_id="merchant_tom",
            player_message="Hello",
            execute_command=_noop_executor,
        ))

        assert result.success is False
        assert result.error == "agent_failed"
        assert result.npc_id == "merchant_tom"

    def test_scene_entry_is_private(self) -> None:
        """Player message scene entry should have visibility='private'."""
        world = _world_with_characters()
        state = _state_with_relations(world)
        coordinator, _ = _build_coordinator(
            llm_responses=[{"text": "", "finish_reason": "stop"}],
            world=world, state=state,
        )

        asyncio.run(coordinator.execute(
            npc_id="merchant_tom",
            player_message="Can we speak in private?",
            execute_command=_noop_executor,
        ))

        entries = state.scene.snapshot()["entries"]
        player_entries = [e for e in entries if e["source"] == "player"]
        assert player_entries, "Expected at least one player scene entry"
        assert player_entries[-1]["visibility"] == "private"

    def test_scene_entry_audience_contains_player_and_npc(self) -> None:
        """Scene entry audience must include 'player' and 'npc:merchant_tom'."""
        world = _world_with_characters()
        state = _state_with_relations(world)
        coordinator, _ = _build_coordinator(
            llm_responses=[{"text": "", "finish_reason": "stop"}],
            world=world, state=state,
        )

        asyncio.run(coordinator.execute(
            npc_id="merchant_tom",
            player_message="Private message",
            execute_command=_noop_executor,
        ))

        entries = state.scene.snapshot()["entries"]
        player_entries = [e for e in entries if e["source"] == "player"]
        audience = player_entries[-1].get("audience", [])
        assert "player" in audience
        assert "npc:merchant_tom" in audience

    def test_result_has_no_gm_or_teammate_fields(self) -> None:
        """PrivateChatResult must not have gm_result or teammate_results."""
        coordinator, _ = _build_coordinator(
            llm_responses=[{"text": "", "finish_reason": "stop"}],
        )
        result = asyncio.run(coordinator.execute(
            npc_id="merchant_tom",
            player_message="Hello",
            execute_command=_noop_executor,
        ))

        assert not hasattr(result, "gm_result")
        assert not hasattr(result, "teammate_results")

    def test_dialogue_options_present(self) -> None:
        """Dialogue options must include at least 'talk' and 'farewell'."""
        coordinator, _ = _build_coordinator(
            llm_responses=[{"text": "", "finish_reason": "stop"}],
        )
        result = asyncio.run(coordinator.execute(
            npc_id="merchant_tom",
            player_message="Hello",
            execute_command=_noop_executor,
        ))

        intents = [o["intent"] for o in result.dialogue_options]
        assert "talk" in intents
        assert "farewell" in intents

    def test_context_window_updated_after_interaction(self) -> None:
        """Passing a ContextWindow → it should contain 2 new messages after execute."""
        coordinator, _ = _build_coordinator(
            llm_responses=[{"text": "", "finish_reason": "stop"}],
        )
        window = ContextWindow(actor_id="merchant_tom", max_tokens=10000)
        initial_count = len(window.messages)

        asyncio.run(coordinator.execute(
            npc_id="merchant_tom",
            player_message="Testing window update",
            execute_command=_noop_executor,
            context_window=window,
        ))

        assert len(window.messages) == initial_count + 2

    def test_overflow_populates_graphize_candidates(self) -> None:
        """Tiny max_tokens forces overflow → graphize_candidates non-empty."""
        coordinator, _ = _build_coordinator(
            llm_responses=[{"text": "", "finish_reason": "stop"}],
        )
        # max_tokens=1 will overflow immediately
        window = ContextWindow(actor_id="merchant_tom", max_tokens=1)

        result = asyncio.run(coordinator.execute(
            npc_id="merchant_tom",
            player_message="overflow test",
            execute_command=_noop_executor,
            context_window=window,
        ))

        assert len(result.graphize_candidates) > 0

    def test_time_cost_is_one_sixth(self) -> None:
        """Private chat time cost must equal 1/6."""
        coordinator, _ = _build_coordinator(
            llm_responses=[{"text": "", "finish_reason": "stop"}],
        )
        result = asyncio.run(coordinator.execute(
            npc_id="merchant_tom",
            player_message="Quick chat",
            execute_command=_noop_executor,
        ))

        assert abs(result.time_cost - (1 / 6)) < 1e-9


# ------------------------------------------------------------------
# TestPrivateChatResultToSSE
# ------------------------------------------------------------------


class TestPrivateChatResultToSSE:
    def test_npc_speech_generates_npc_event(self) -> None:
        """AgentResult with speech tool_result → npc_response SSE event."""
        npc_result = AgentResult(
            tool_results=[
                ToolResult(
                    success=True,
                    message="Of course, traveller.",
                    metadata={"event_type": "speech"},
                ),
            ],
        )
        result = _make_private_result(npc_result=npc_result)
        events = _private_chat_result_to_sse(result)
        event_types = [e.event_type for e in events]
        assert "npc_response" in event_types

    def test_dialogue_options_event_present(self) -> None:
        """Non-empty dialogue_options → dialogue_options SSE event."""
        result = _make_private_result()
        events = _private_chat_result_to_sse(result)
        event_types = [e.event_type for e in events]
        assert "dialogue_options" in event_types

    def test_no_gm_or_teammate_events_in_output(self) -> None:
        """Output must not contain gm_narration or teammate_response events."""
        npc_result = AgentResult(
            tool_results=[
                ToolResult(
                    success=True,
                    message="Hello.",
                    metadata={"event_type": "speech"},
                ),
            ],
        )
        result = _make_private_result(npc_result=npc_result)
        events = _private_chat_result_to_sse(result)
        event_types = [e.event_type for e in events]
        assert "gm_narration" not in event_types
        assert "teammate_response" not in event_types

    def test_empty_dialogue_options_no_options_event(self) -> None:
        """Empty dialogue_options → no dialogue_options SSE event emitted."""
        result = PrivateChatResult(
            success=True,
            npc_id="merchant_tom",
            dialogue_options=[],
        )
        events = _private_chat_result_to_sse(result)
        event_types = [e.event_type for e in events]
        assert "dialogue_options" not in event_types

    def test_none_npc_result_no_npc_event(self) -> None:
        """npc_result=None → no npc_response event (NPC stayed silent)."""
        result = PrivateChatResult(
            success=True,
            npc_id="merchant_tom",
            npc_result=None,
            dialogue_options=[{"text": "继续交谈", "intent": "talk"}],
        )
        events = _private_chat_result_to_sse(result)
        event_types = [e.event_type for e in events]
        assert "npc_response" not in event_types


# ------------------------------------------------------------------
# TestWindowHelpers
# ------------------------------------------------------------------


class TestWindowHelpers:
    def test_window_to_history_excludes_graphized(self) -> None:
        """Messages flagged is_graphized=True must be excluded from history."""
        from app.game_core.narrative.context_window import WindowMessage
        window = ContextWindow(actor_id="merchant_tom", max_tokens=10000)
        # Inject messages directly (slots=True dataclass, use public field)
        window.messages = [
            WindowMessage(role="user", content="visible", token_count=1, metadata={}),
            WindowMessage(role="model", content="graphized", token_count=1, metadata={},
                          is_graphized=True),
        ]
        history = _window_to_history(window)
        assert len(history) == 1
        assert history[0]["parts"][0]["text"] == "visible"

    def test_approx_tokens_minimum_one(self) -> None:
        """Empty string → token count is at least 1."""
        assert _approx_tokens("") == 1

    def test_approx_tokens_proportional(self) -> None:
        """'aaaa' (4 chars) → ~1 token."""
        assert _approx_tokens("aaaa") == 1
        assert _approx_tokens("a" * 40) == 10
