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
from app.game_core.narrative.context_window import window_to_history
from app.game_core.orchestration.private_chat import (
    PrivateChatCoordinator,
    PrivateChatResult,
    _approx_tokens,
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
        history = window_to_history(window)
        assert len(history) == 1
        assert history[0]["parts"][0]["text"] == "visible"

    def test_window_to_history_uses_parts_when_present(self) -> None:
        """When parts is set on a message, it must be preferred over plain text."""
        from app.game_core.narrative.context_window import WindowMessage
        fc_part = {"function_call": {"name": "speak", "args": {"text": "hi"}}}
        window = ContextWindow(actor_id="npc_alice", max_tokens=10000)
        window.messages = [
            WindowMessage(role="user", content="player msg", token_count=2),
            WindowMessage(
                role="model", content="hi",
                token_count=2,
                parts=[fc_part],
            ),
        ]
        history = window_to_history(window)
        assert len(history) == 2
        # User message: text fallback
        assert history[0]["parts"] == [{"text": "player msg"}]
        # Model message: structured parts preferred
        assert history[1]["parts"] == [fc_part]

    def test_approx_tokens_minimum_one(self) -> None:
        """Empty string → token count is at least 1."""
        assert _approx_tokens("") == 1

    def test_approx_tokens_proportional(self) -> None:
        """'aaaa' (4 chars) → ~1 token."""
        assert _approx_tokens("aaaa") == 1
        assert _approx_tokens("a" * 40) == 10


# ------------------------------------------------------------------
# TestPrivateChatSceneDeduplication (Phase 9 fix)
# ------------------------------------------------------------------


class TestPrivateChatSceneDeduplication:
    """Phase 9: Verify僻静处 sub-area and SSE event are not duplicated across turns."""

    def test_first_call_creates_scene_and_marks_is_new(self) -> None:
        """First execute() call: scene_is_new=True, sub-area created in areas slice."""
        world = _world_with_characters()
        state = _state_with_relations(world)
        coordinator, _ = _build_coordinator(
            llm_responses=[_npc_speak_response("Hi!")],
            world=world,
            state=state,
        )

        result = asyncio.run(coordinator.execute(
            npc_id="merchant_tom",
            player_message="Hello",
            execute_command=_noop_executor,
        ))

        assert result.scene_is_new is True
        assert result.scene_id is not None
        assert result.scene_id.startswith("_private_merchant_tom_")

    def test_second_call_reuses_scene_and_marks_not_new(self) -> None:
        """Second execute() for the same NPC: scene_is_new=False, same scene_id returned."""
        world = _world_with_characters()
        state = _state_with_relations(world)
        coordinator, _ = _build_coordinator(
            llm_responses=[
                _npc_speak_response("Hello!"),
                _npc_speak_response("And again!"),
                # GM responses
                _stop_response(),
                _stop_response(),
            ],
            world=world,
            state=state,
        )

        result1 = asyncio.run(coordinator.execute(
            npc_id="merchant_tom",
            player_message="Hello",
            execute_command=_noop_executor,
        ))
        result2 = asyncio.run(coordinator.execute(
            npc_id="merchant_tom",
            player_message="Hello again",
            execute_command=_noop_executor,
        ))

        assert result1.scene_is_new is True
        assert result2.scene_is_new is False
        # Same scene_id returned (reused, not recreated)
        assert result1.scene_id == result2.scene_id

    def test_second_call_does_not_duplicate_sub_area_in_state(self) -> None:
        """After two execute() calls, only ONE private sub-area for the NPC exists in state."""
        world = _world_with_characters()
        state = _state_with_relations(world)
        coordinator, _ = _build_coordinator(
            llm_responses=[
                _npc_speak_response("Hello!"),
                _npc_speak_response("Again!"),
                _stop_response(),
                _stop_response(),
            ],
            world=world,
            state=state,
        )

        asyncio.run(coordinator.execute(
            npc_id="merchant_tom",
            player_message="Hello",
            execute_command=_noop_executor,
        ))
        asyncio.run(coordinator.execute(
            npc_id="merchant_tom",
            player_message="Hello again",
            execute_command=_noop_executor,
        ))

        sub_areas = state.areas.list_temporary_sub_areas("town")
        private_sub_areas = [
            sa for sa in sub_areas
            if sa.get("source") == "private_chat"
            and "merchant_tom" in sa.get("resident_npcs", [])
        ]
        assert len(private_sub_areas) == 1, (
            f"Expected exactly 1 private sub-area, got {len(private_sub_areas)}"
        )

    def test_scene_change_sse_emitted_only_on_first_call(self) -> None:
        """scene_change SSE must appear on first call (scene_is_new=True) but not on second."""
        # First call: scene_is_new=True → scene_change emitted
        result_new = PrivateChatResult(
            completed=True,
            npc_id="merchant_tom",
            scene_id="_private_merchant_tom_0",
            scene_name="酒馆二楼的小包间",
            scene_is_new=True,
        )
        events_new = _private_chat_result_to_sse(result_new)
        assert any(e.event_type == "scene_change" for e in events_new), (
            "scene_change must be emitted when scene_is_new=True"
        )

        # Second call: scene_is_new=False → scene_change NOT emitted
        result_reuse = PrivateChatResult(
            completed=True,
            npc_id="merchant_tom",
            scene_id="_private_merchant_tom_0",
            scene_name="酒馆二楼的小包间",
            scene_is_new=False,
        )
        events_reuse = _private_chat_result_to_sse(result_reuse)
        assert not any(e.event_type == "scene_change" for e in events_reuse), (
            "scene_change must NOT be emitted when scene_is_new=False"
        )

    def test_scene_change_payload_has_fade_transition(self) -> None:
        """When scene_is_new=True, scene_change payload includes transition='fade'."""
        result = PrivateChatResult(
            completed=True,
            npc_id="merchant_tom",
            scene_id="_private_merchant_tom_0",
            scene_name="僻静处",
            scene_is_new=True,
        )
        events = _private_chat_result_to_sse(result)
        scene_event = next(e for e in events if e.event_type == "scene_change")
        assert scene_event.payload["transition"] == "fade"
        assert scene_event.payload["location_id"] == "_private_merchant_tom_0"
        assert scene_event.payload["location_name"] == "僻静处"

    def test_different_npcs_get_separate_private_scenes(self) -> None:
        """Two NPCs in the same area each get their own non-overlapping sub-areas."""
        world = build_default_world(
            "test_world",
            world_data={
                "tags": {
                    "profession": {"id": "profession", "tags": ["merchant", "blacksmith"]},
                    "ancestry": {"id": "ancestry", "tags": ["human"]},
                },
                "characters": {
                    "merchant_tom": {
                        "id": "merchant_tom",
                        "name": "Merchant Tom",
                        "personality": "Shrewd.",
                        "tags": ["merchant", "human"],
                    },
                    "blacksmith_joe": {
                        "id": "blacksmith_joe",
                        "name": "Blacksmith Joe",
                        "personality": "Gruff.",
                        "tags": ["blacksmith", "human"],
                    },
                },
            },
        )
        from app.game_core.bootstrap import build_runtime_for_world
        state = build_runtime_for_world(world).state
        state.player.restore({
            "character_name": "Hero",
            "current_area": "town",
        })

        llm = RecordingLlmProvider([_npc_speak_response("Hi from Tom."), _stop_response()])
        from app.game_core.narrative.registry import RoleToolRegistry
        from app.game_core.narrative.character_tools import register_npc_tools
        from app.game_core.narrative.gm_tools import register_gm_tools
        registry = RoleToolRegistry()
        register_gm_tools(registry)
        register_npc_tools(registry)
        from app.game_core.narrative.executor import AgenticExecutor
        executor = AgenticExecutor(tool_registry=registry, llm=llm)
        coord = PrivateChatCoordinator(executor, world, state)

        result_tom = asyncio.run(coord.execute(
            npc_id="merchant_tom",
            player_message="Hi",
            execute_command=_noop_executor,
        ))

        llm2 = RecordingLlmProvider([_npc_speak_response("Hi from Joe."), _stop_response()])
        executor2 = AgenticExecutor(tool_registry=registry, llm=llm2)
        coord2 = PrivateChatCoordinator(executor2, world, state)
        result_joe = asyncio.run(coord2.execute(
            npc_id="blacksmith_joe",
            player_message="Hi",
            execute_command=_noop_executor,
        ))

        assert result_tom.scene_is_new is True
        assert result_joe.scene_is_new is True
        # Scenes must be different
        assert result_tom.scene_id != result_joe.scene_id

        # Two separate private sub-areas should exist
        sub_areas = state.areas.list_temporary_sub_areas("town")
        private_ids = {sa["id"] for sa in sub_areas if sa.get("source") == "private_chat"}
        assert len(private_ids) == 2
