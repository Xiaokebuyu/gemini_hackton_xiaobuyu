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
from app.game_core.narrative.instance_manager import NPCInstance
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
            "tags": {
                "profession": {"id": "profession", "tags": ["merchant"]},
                "ancestry": {"id": "ancestry", "tags": ["human"]},
            },
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
    return ExecuteResult(executed=True)


def _npc_speak_response(text: str = "Hello.") -> dict[str, Any]:
    return {
        "tool_calls": [{"name": "speak", "args": {"text": text}}],
        "finish_reason": "tool_calls",
    }


def _stop_response(text: str = "") -> dict[str, Any]:
    return {"text": text, "finish_reason": "stop"}


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
    completed: bool = True,
) -> PrivateChatResult:
    return PrivateChatResult(
        completed=completed,
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
        """World has no characters registry → completed=False, error='npc_not_found'."""
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

        assert result.completed is False
        assert result.error == "npc_not_found"
        assert result.npc_result is None

    def test_npc_found_no_llm_returns_result(self) -> None:
        """No LLM provider → executor degrades gracefully → coordinator completes."""
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

        assert result.completed is True
        assert result.npc_id == "merchant_tom"

    def test_npc_agent_exception_returns_agent_failed(self) -> None:
        """LLM throws → coordinator returns completed=False, error='agent_failed'."""
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

        assert result.completed is False
        assert result.error == "agent_failed"
        assert result.npc_id == "merchant_tom"

    def test_scene_entry_is_private(self) -> None:
        """Player message scene entry should have visibility='private'."""
        world = _world_with_characters()
        state = _state_with_relations(world)
        coordinator, _ = _build_coordinator(
            llm_responses=[_npc_speak_response("当然，我们私下谈。")],
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
            llm_responses=[_npc_speak_response("没人会听见。")],
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

    def test_npc_speech_entry_is_also_private(self) -> None:
        """NPC speech emitted during private chat must remain private on SceneBus."""
        world = _world_with_characters()
        state = _state_with_relations(world)
        coordinator, _ = _build_coordinator(
            llm_responses=[
                {
                    "tool_calls": [
                        {"name": "speak", "args": {"text": "Keep this between us."}},
                    ],
                },
            ],
            world=world,
            state=state,
        )

        asyncio.run(coordinator.execute(
            npc_id="merchant_tom",
            player_message="Tell me quietly.",
            execute_command=_noop_executor,
        ))

        entries = state.scene.snapshot()["entries"]
        npc_entries = [e for e in entries if e["source"] == "merchant_tom"]
        assert npc_entries, "Expected at least one NPC scene entry"
        assert npc_entries[-1]["visibility"] == "private"
        assert npc_entries[-1]["audience"] == ["player", "npc:merchant_tom"]

    def test_result_has_no_gm_or_teammate_fields(self) -> None:
        """PrivateChatResult must not have gm_result or teammate_results."""
        coordinator, _ = _build_coordinator(
            llm_responses=[_npc_speak_response("说吧。")],
        )
        result = asyncio.run(coordinator.execute(
            npc_id="merchant_tom",
            player_message="Hello",
            execute_command=_noop_executor,
        ))

        # gm_result is Phase 2b inner monologue (present but may be None)
        assert hasattr(result, "gm_result")
        assert not hasattr(result, "teammate_results")

    def test_dialogue_options_present(self) -> None:
        """Dialogue options must include at least 'talk' and 'farewell'."""
        coordinator, _ = _build_coordinator(
            llm_responses=[_npc_speak_response("我在听。")],
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
            llm_responses=[_npc_speak_response("Testing window update acknowledged.")],
        )
        instance = NPCInstance(
            actor_id="merchant_tom",
            context_window=ContextWindow(actor_id="merchant_tom", max_tokens=10000),
        )
        initial_count = len(instance.context_window.messages)

        asyncio.run(coordinator.execute(
            npc_id="merchant_tom",
            player_message="Testing window update",
            execute_command=_noop_executor,
            instance=instance,
        ))

        assert len(instance.context_window.messages) == initial_count + 2

    def test_overflow_populates_graphize_candidates(self) -> None:
        """Tiny graphize_threshold forces graphize trigger → graphize_candidates non-empty."""
        coordinator, _ = _build_coordinator(
            llm_responses=[_npc_speak_response("overflow acknowledged")],
        )
        # graphize_threshold=1 will trigger immediately on any message add
        instance = NPCInstance(
            actor_id="merchant_tom",
            context_window=ContextWindow(
                actor_id="merchant_tom",
                max_tokens=10000,
                graphize_threshold=1,
            ),
        )

        result = asyncio.run(coordinator.execute(
            npc_id="merchant_tom",
            player_message="overflow test",
            execute_command=_noop_executor,
            instance=instance,
        ))

        assert len(result.graphize_candidates) > 0

    def test_private_chat_with_instance_completes_successfully(self) -> None:
        """Private chat with an NPCInstance (containing a directive) completes correctly.

        Path A (consume_directive → prompt injection) has been removed.
        Directives now flow through the blackboard (Path B) via NpcAutonomyHook.
        """
        world = _world_with_characters()
        state = _state_with_relations(world)
        coordinator, llm = _build_coordinator(
            llm_responses=[_npc_speak_response("The hidden cellar stays quiet.")],
            world=world,
            state=state,
        )
        directive = {
            "npc_id": "merchant_tom",
            "directive": {"kind": "hint", "topic": "hidden_cellar"},
            "priority": "high",
            "expires_at_tick": 12,
            "consumed": False,
        }
        instance = NPCInstance(
            actor_id="merchant_tom",
            context_window=ContextWindow(actor_id="merchant_tom", max_tokens=10000),
            directive_queue=[directive],
        )

        result = asyncio.run(coordinator.execute(
            npc_id="merchant_tom",
            player_message="Tell me privately.",
            execute_command=_noop_executor,
            instance=instance,
        ))

        assert result.completed is True
        # Directive is no longer consumed by the coordinator (Path A removed).
        # It remains in the queue until NpcAutonomyHook processes it via blackboard.
        assert directive["consumed"] is False

    def test_time_cost_is_one_sixth(self) -> None:
        """Private chat time cost must equal 1/6."""
        coordinator, _ = _build_coordinator(
            llm_responses=[_npc_speak_response("Quick answer.")],
        )
        result = asyncio.run(coordinator.execute(
            npc_id="merchant_tom",
            player_message="Quick chat",
            execute_command=_noop_executor,
        ))

        assert abs(result.time_cost - (1 / 6)) < 1e-9

    def test_text_only_npc_response_is_gracefully_handled(self) -> None:
        """P29-A1: NPC text without a tool is now gracefully wrapped as synthetic
        speech instead of triggering a protocol_error."""
        coordinator, _ = _build_coordinator(
            llm_responses=[_stop_response("I should have used speak.")],
        )

        result = asyncio.run(coordinator.execute(
            npc_id="merchant_tom",
            player_message="Hello",
            execute_command=_noop_executor,
        ))

        # With A1 graceful degradation, text-only NPC response succeeds
        assert result.completed is True
        assert result.error is None
        assert result.npc_result is not None
        assert result.npc_result.metadata.get("finish_reason") == "text_fallback"


# ------------------------------------------------------------------
# TestPrivateChatResultToSSE
# ------------------------------------------------------------------


class TestPrivateChatResultToSSE:
    def test_npc_speech_generates_npc_event(self) -> None:
        """AgentResult with speech tool_result → npc_response SSE event."""
        npc_result = AgentResult(
            tool_results=[
                ToolResult(
                    ok=True,
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

    def test_private_talk_option_dispatch_uses_private_scope(self) -> None:
        result = _make_private_result(
            dialogue_options=[{"text": "继续交谈", "intent": "talk"}],
        )

        events = _private_chat_result_to_sse(result)

        option_payload = [e for e in events if e.event_type == "dialogue_options"][0].payload["options"][0]
        assert option_payload["dispatch"]["kind"] == "interact"
        assert option_payload["dispatch"]["payload"] == {
            "scope": "private",
            "intent": "talk",
            "target_kind": "npc",
            "target_id": "merchant_tom",
            "message": "继续交谈",
        }

    def test_private_checked_option_dispatch_uses_private_scope(self) -> None:
        result = _make_private_result(
            dialogue_options=[
                {
                    "text": "[说服 DC12] 再追问一次",
                    "intent": "talk",
                    "check": {"skill": "persuasion", "dc": 12},
                },
            ],
        )

        events = _private_chat_result_to_sse(result)

        option_payload = [e for e in events if e.event_type == "dialogue_options"][0].payload["options"][0]
        assert option_payload["dispatch"]["kind"] == "interact"
        assert option_payload["dispatch"]["payload"] == {
            "scope": "private",
            "intent": "talk",
            "target_kind": "npc",
            "target_id": "merchant_tom",
            "message": "[说服 DC12] 再追问一次",
            "check_skill": "persuasion",
            "check_dc": 12,
        }

    def test_private_browse_option_does_not_generate_dispatch(self) -> None:
        result = _make_private_result(
            dialogue_options=[{"text": "查看商品", "intent": "browse"}],
        )

        events = _private_chat_result_to_sse(result)

        option_payload = [e for e in events if e.event_type == "dialogue_options"][0].payload["options"][0]
        assert "dispatch" not in option_payload

    def test_private_farewell_option_stays_local(self) -> None:
        result = _make_private_result(
            dialogue_options=[{"text": "告别", "intent": "farewell"}],
        )

        events = _private_chat_result_to_sse(result)

        option_payload = [e for e in events if e.event_type == "dialogue_options"][0].payload["options"][0]
        assert option_payload["dispatch"] == {
            "kind": "local",
            "payload": {"action": "leave_dialogue"},
        }

    def test_no_gm_or_teammate_events_in_output(self) -> None:
        """Output must not contain gm_narration or teammate_response events."""
        npc_result = AgentResult(
            tool_results=[
                ToolResult(
                    ok=True,
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

    def test_gm_comment_is_kept_as_introspective_output(self) -> None:
        result = _make_private_result(
            npc_result=None,
        )
        result.gm_result = AgentResult(
            tool_results=[
                ToolResult(
                    ok=True,
                    message="你意识到她比柜台后的微笑更疲惫。",
                    metadata={"event_type": "gm_comment"},
                ),
            ],
        )

        events = _private_chat_result_to_sse(result)

        gm_event = [event for event in events if event.event_type == "gm_comment"][0]
        assert gm_event.payload == {
            "content": "你意识到她比柜台后的微笑更疲惫。",
            "tone": "introspective",
        }

    def test_empty_dialogue_options_no_options_event(self) -> None:
        """Empty dialogue_options → no dialogue_options SSE event emitted."""
        result = PrivateChatResult(
            completed=True,
            npc_id="merchant_tom",
            dialogue_options=[],
        )
        events = _private_chat_result_to_sse(result)
        event_types = [e.event_type for e in events]
        assert "dialogue_options" not in event_types

    def test_none_npc_result_no_npc_event(self) -> None:
        """npc_result=None → no npc_response event (NPC stayed silent)."""
        result = PrivateChatResult(
            completed=True,
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
