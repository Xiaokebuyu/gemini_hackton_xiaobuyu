"""Tests for AgentOrchestrationService."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

from app.agent_orchestration import (
    AgentOrchestrationService,
    _gm_result_to_sse,
    _interaction_result_to_sse,
    _make_command_executor,
    _npc_result_to_sse,
    _teammate_result_to_sse,
)
from app.game_core.narrative.context_builder import (
    _build_npc_prompt_text,
    _build_teammate_prompt_text,
)
from app.game_core import ManagedSession
from app.game_core.bootstrap import build_default_world, build_runtime_for_world
from app.game_core.content import WorldInstance
from app.game_core.narrative.executor import AgenticExecutor
from app.game_core.narrative.models import AgentResult, ToolResult
from app.game_core.narrative.registry import RoleToolRegistry
from app.game_core.orchestration.defaults import build_default_action_dispatcher
from app.game_core.orchestration.models import PipelineResult
from app.game_core.orchestration.shared_context import SharedContext
from app.game_core.rules.models import Command
from app.game_core.state.slices.scene import SceneEntry


# ------------------------------------------------------------------
# Recording LLM provider for deterministic tests
# ------------------------------------------------------------------


class RecordingLlmProvider:
    """LLM provider that returns pre-configured responses."""

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


def _world_with_npc() -> WorldInstance:
    """Build a world with one test NPC."""
    world = build_default_world(
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
                "paladin_aria": {
                    "id": "paladin_aria",
                    "name": "Paladin Aria",
                    "personality": "A devout and brave paladin.",
                    "tags": ["holy", "warrior"],
                },
            },
        },
    )
    return world


def _session_with_npc() -> ManagedSession:
    """Build a ManagedSession with NPC relation data."""
    world = _world_with_npc()
    runtime = build_runtime_for_world(world)

    # Set up player
    runtime.state.player.restore({
        "character_name": "TestPlayer",
        "character_class": "warrior",
        "current_area": "town",
        "current_location": "market",
    })

    # Set up relations with merchant
    runtime.state.relations.restore({
        "npc_dispositions": {
            "merchant_tom": {"approval": 25, "trust": 15, "fear": 0, "romance": 0},
        },
        "relationship_stages": {
            "merchant_tom": "acquaintance",
        },
        "npc_impressions": {
            "merchant_tom": ["Bought a sword last time", "Seemed trustworthy"],
        },
    })

    return ManagedSession(
        world_id="test_world",
        session_id="test_session",
        runtime=runtime,
    )


def _session_for_post_action_round() -> ManagedSession:
    world = build_default_world(
        "test_world",
        world_data={
            "characters": {
                "merchant_tom": {
                    "id": "merchant_tom",
                    "name": "Merchant Tom",
                    "personality": "A shrewd but fair merchant.",
                    "dialogue_style": "Speaks with a slight drawl.",
                    "tags": ["merchant", "human"],
                    "response_tendency": 1.0,
                },
                "paladin_aria": {
                    "id": "paladin_aria",
                    "name": "Paladin Aria",
                    "personality": "A devout and brave paladin.",
                    "tags": ["holy", "warrior"],
                    "response_tendency": 1.0,
                },
            },
        },
    )
    runtime = build_runtime_for_world(world)
    runtime.state.player.restore({
        "character_name": "TestPlayer",
        "character_class": "warrior",
        "current_area": "town",
        "current_location": "market",
    })
    runtime.state.areas.get_area("town").npc_locations["merchant_tom"] = "market"
    runtime.state.party.restore({
        "members": {
            "paladin_aria": {
                "status": "active",
            },
        },
    })
    runtime.state.scene.add_entry(SceneEntry(
        source="player",
        content="The player moves decisively.",
        visibility="public",
        tags=["action"],
    ))
    return ManagedSession(
        world_id="test_world",
        session_id="test_post_action",
        runtime=runtime,
    )


def _npc_speak_response(text: str = "Hello.") -> dict[str, Any]:
    return {
        "tool_calls": [{"name": "speak", "args": {"text": text}}],
        "finish_reason": "tool_calls",
    }


def _gm_pass_turn_response() -> dict[str, Any]:
    return {
        "tool_calls": [{"name": "pass_turn", "args": {}}],
        "finish_reason": "tool_calls",
    }


def _stop_response(text: str = "") -> dict[str, Any]:
    return {"text": text, "tool_calls": [], "finish_reason": "stop"}


def _build_service(
    llm_responses: list[dict[str, Any]] | None = None,
) -> tuple[AgentOrchestrationService, RecordingLlmProvider]:
    """Build an AgentOrchestrationService with a recording LLM."""
    from app.game_core.narrative.gm_tools import register_gm_tools
    from app.game_core.narrative.character_tools import (
        register_npc_tools,
        register_teammate_tools,
    )

    llm = RecordingLlmProvider(llm_responses)
    registry = RoleToolRegistry()
    register_gm_tools(registry)
    register_npc_tools(registry)
    register_teammate_tools(registry)
    executor = AgenticExecutor(tool_registry=registry, llm=llm)
    service = AgentOrchestrationService(executor)
    return service, llm


class SequencedReactionExecutor:
    """Minimal executor that simulates post-action agent side effects."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run_agentic(
        self,
        *,
        role: str,
        context: Any,
        system_prompt: str,
        user_message: str,
        **_: Any,
    ) -> AgentResult:
        self.calls.append({
            "role": role,
            "scene_entries": [dict(entry) for entry in context.scene_entries],
            "user_message": user_message,
            "system_prompt": system_prompt,
        })
        if role == "gm":
            return AgentResult(
                tool_results=[
                    ToolResult(
                        success=True,
                        message="Dust rolls across the market.",
                        metadata={"event_type": "gm_narration"},
                    )
                ]
            )
        if role == "npc":
            context.state.scene.add_entry(SceneEntry(
                source="merchant_tom",
                content="Merchant Tom eyes the movement.",
                visibility="public",
                tags=["speech"],
            ))
            return AgentResult(
                tool_results=[
                    ToolResult(
                        success=True,
                        message="Merchant Tom eyes the movement.",
                        metadata={"event_type": "speech"},
                    )
                ]
            )
        if role == "teammate":
            context.state.scene.add_entry(SceneEntry(
                source="TEAMMATE:paladin_aria",
                content="Aria keeps a hand near her sword.",
                visibility="public",
                tags=["speech"],
            ))
            return AgentResult(
                tool_results=[
                    ToolResult(
                        success=True,
                        message="Aria keeps a hand near her sword.",
                        metadata={"event_type": "speech"},
                    )
                ]
            )
        return AgentResult()


def test_make_command_executor_records_change_log() -> None:
    """Dialogue tool commands should enter TickCoordinator change_log."""
    session = _session_with_npc()
    executor = _make_command_executor(session)

    result = executor(Command(
        type="modify_disposition",
        params={
            "npc_id": "merchant_tom",
            "dimension": "approval",
            "delta": 5,
        },
        source="merchant_tom",
    ))

    assert result.success is True
    assert session.runtime.state.relations.get_disposition("merchant_tom", "approval") == 30
    assert session.runtime.tick_coordinator.change_log
    assert session.runtime.tick_coordinator.change_log[-1].slice == "relations"


# ------------------------------------------------------------------
# Prompt builder tests
# ------------------------------------------------------------------


class TestPromptBuilders:
    def test_npc_prompt_includes_profile_and_disposition(self) -> None:
        prompt = _build_npc_prompt_text(
            npc_profile={"name": "Tom", "personality": "Shrewd merchant"},
            disposition={"approval": 30, "trust": 10, "fear": 0, "romance": 0},
            stage="friend",
            impressions=["Bought a sword", "Kind person"],
        )

        assert "Tom" in prompt
        assert "Shrewd merchant" in prompt
        assert "friend" in prompt
        assert "Approval: 30" in prompt
        assert "Bought a sword" in prompt
        assert "Kind person" in prompt

    def test_npc_prompt_handles_empty_profile(self) -> None:
        prompt = _build_npc_prompt_text(
            npc_profile={},
            disposition={},
            stage="stranger",
            impressions=[],
        )

        assert "Unknown NPC" in prompt
        assert "stranger" in prompt
        assert "No previous memories" in prompt

    def test_teammate_prompt_includes_personality(self) -> None:
        prompt = _build_teammate_prompt_text(
            profile={"name": "Aria", "personality": "A brave paladin."},
            disposition={"approval": 50, "trust": 60},
        )

        assert "Aria" in prompt
        assert "brave paladin" in prompt
        assert "50" in prompt
        assert "60" in prompt


# ------------------------------------------------------------------
# SSE converter tests
# ------------------------------------------------------------------


class TestSSEConverters:
    def test_npc_result_extracts_speech_events(self) -> None:
        result = AgentResult(
            tool_results=[
                ToolResult(
                    success=True,
                    message="Hello, traveler!",
                    metadata={"event_type": "speech"},
                ),
                ToolResult(
                    success=True,
                    message="nods warmly",
                    metadata={"event_type": "emote"},
                ),
            ],
        )

        events = _npc_result_to_sse("merchant_tom", result)

        assert len(events) == 2
        assert events[0].event_type == "npc_response"
        assert events[0].payload["npc_id"] == "merchant_tom"
        assert events[0].payload["content"] == "Hello, traveler!"
        assert events[1].event_type == "npc_emote"
        assert events[1].payload["action"] == "nods warmly"

    def test_npc_result_skips_failed_tools(self) -> None:
        result = AgentResult(
            tool_results=[
                ToolResult(success=False, message="error", metadata={"event_type": "speech"}),
                ToolResult(success=True, message="ok", metadata={"event_type": "speech"}),
            ],
        )

        events = _npc_result_to_sse("npc1", result)

        assert len(events) == 1
        assert events[0].payload["content"] == "ok"

    def test_npc_result_handles_refuse(self) -> None:
        result = AgentResult(
            tool_results=[
                ToolResult(
                    success=True,
                    message="I cannot help you with that.",
                    metadata={"event_type": "refuse"},
                ),
            ],
        )

        events = _npc_result_to_sse("npc1", result)

        assert len(events) == 1
        assert events[0].event_type == "npc_response"
        assert events[0].payload["type"] == "refuse"

    def test_gm_result_extracts_narration_and_comment(self) -> None:
        result = AgentResult(
            tool_results=[
                ToolResult(
                    success=True,
                    message="The market bustles with activity.",
                    metadata={"event_type": "gm_narration"},
                ),
                ToolResult(
                    success=True,
                    message="Another shopping trip. How original.",
                    metadata={"event_type": "gm_comment"},
                ),
            ],
        )

        events = _gm_result_to_sse(result)

        assert len(events) == 2
        assert events[0].event_type == "gm_narration"
        assert events[0].payload["content"] == "The market bustles with activity."
        assert events[1].event_type == "gm_comment"

    def test_gm_result_pass_turn_produces_no_events(self) -> None:
        result = AgentResult(
            tool_results=[
                ToolResult(success=True, message="", metadata={"event_type": "pass"}),
            ],
        )

        events = _gm_result_to_sse(result)

        assert events == []

    def test_teammate_result_extracts_speech_and_emote(self) -> None:
        result = AgentResult(
            tool_results=[
                ToolResult(
                    success=True,
                    message="I agree with that decision.",
                    metadata={"event_type": "speech"},
                ),
            ],
        )

        events = _teammate_result_to_sse("paladin_aria", result)

        assert len(events) == 1
        assert events[0].event_type == "teammate_response"
        assert events[0].payload["character_id"] == "paladin_aria"
        assert events[0].payload["content"] == "I agree with that decision."


# ------------------------------------------------------------------
# Command executor tests
# ------------------------------------------------------------------


class TestCommandExecutor:
    def test_make_command_executor_applies_delta(self) -> None:
        session = _session_with_npc()
        executor = _make_command_executor(session)

        from app.game_core.rules.models import Command

        result = executor(Command(
            type="modify_disposition",
            source="ai_osiris",
            params={
                "npc_id": "merchant_tom",
                "dimension": "approval",
                "delta": 5,
            },
        ))

        assert result.success is True
        new_approval = session.runtime.state.relations.npc_dispositions["merchant_tom"]["approval"]
        assert new_approval == 30  # 25 + 5


# ------------------------------------------------------------------
# Integration tests (with recording LLM)
# ------------------------------------------------------------------


class TestNpcResponse:
    def test_generate_npc_response_calls_llm_with_npc_tools(self) -> None:
        # LLM returns a speak tool call, then stops
        service, llm = _build_service([
            _npc_speak_response("Welcome, friend!"),
        ])
        session = _session_with_npc()

        events = asyncio.run(service.generate_npc_response(
            session=session,
            npc_id="merchant_tom",
            player_message="Hello there!",
        ))

        # LLM was called
        assert len(llm.calls) >= 1
        # System prompt includes NPC name
        assert "Merchant Tom" in llm.calls[0]["system_prompt"]
        # Speak tool produced an SSE event
        assert any(e.event_type == "npc_response" for e in events)
        speech_event = next(e for e in events if e.event_type == "npc_response")
        assert speech_event.payload["content"] == "Welcome, friend!"

    def test_generate_npc_response_unknown_npc_returns_empty(self) -> None:
        service, _ = _build_service()
        session = _session_with_npc()

        events = asyncio.run(service.generate_npc_response(
            session=session,
            npc_id="nonexistent",
            player_message="Hello?",
        ))

        assert events == []

    def test_generate_npc_response_writes_player_message_to_scene(self) -> None:
        service, _ = _build_service([
            _npc_speak_response("Testing scene write acknowledged."),
        ])
        session = _session_with_npc()

        asyncio.run(service.generate_npc_response(
            session=session,
            npc_id="merchant_tom",
            player_message="Testing scene write",
        ))

        entries = session.runtime.state.scene.snapshot()["entries"]
        assert any(
            e["source"] == "player" and "Testing scene write" in e["content"]
            for e in entries
        )

    def test_generate_npc_response_text_only_returns_invalid_agent_response(self) -> None:
        service, _ = _build_service([
            _stop_response("I should have used speak."),
        ])
        session = _session_with_npc()

        events = asyncio.run(service.generate_npc_response(
            session=session,
            npc_id="merchant_tom",
            player_message="Hello",
        ))

        assert len(events) == 1
        assert events[0].event_type == "npc_response_error"
        assert events[0].payload["code"] == "invalid_agent_response"
        assert events[0].payload["reason"] == "text_without_tool"


class TestPostActionReactions:
    def test_gm_reaction_with_pass_produces_no_events(self) -> None:
        # LLM calls pass_turn
        service, _ = _build_service([
            _gm_pass_turn_response(),
            _stop_response(),
        ])
        session = _session_with_npc()
        pipeline_result = PipelineResult(
            success=True,
            action_type="move_area",
            narrative_hints=["You moved to the forest."],
        )

        events = asyncio.run(service.generate_post_action_reactions(
            session=session, result=pipeline_result,
        ))

        # pass_turn produces no SSE events
        gm_events = [e for e in events if e.event_type in ("gm_narration", "gm_comment")]
        assert gm_events == []

    def test_gm_reaction_with_narrate_produces_sse(self) -> None:
        service, _ = _build_service([
            {
                "tool_calls": [
                    {"name": "narrate", "args": {"text": "The forest looms ahead."}},
                ],
                "finish_reason": "tool_calls",
            },
            _stop_response(),
        ])
        session = _session_with_npc()
        pipeline_result = PipelineResult(
            success=True,
            action_type="move_area",
            narrative_hints=["Entered forest."],
        )

        events = asyncio.run(service.generate_post_action_reactions(
            session=session, result=pipeline_result,
        ))

        gm_events = [e for e in events if e.event_type == "gm_narration"]
        assert len(gm_events) == 1
        assert gm_events[0].payload["content"] == "The forest looms ahead."

    def test_no_teammates_produces_no_teammate_events(self) -> None:
        # GM does pass_turn, no teammates in party
        service, _ = _build_service([
            _stop_response(),
        ])
        session = _session_with_npc()
        pipeline_result = PipelineResult(success=True, action_type="rest")

        events = asyncio.run(service.generate_post_action_reactions(
            session=session, result=pipeline_result,
        ))

        teammate_events = [e for e in events if e.event_type == "teammate_response"]
        assert teammate_events == []

    def test_run_post_action_round_shares_scene_bus_in_order(self) -> None:
        session = _session_for_post_action_round()
        executor = SequencedReactionExecutor()
        service = AgentOrchestrationService(executor)  # type: ignore[arg-type]
        shared = SharedContext(
            world=session.runtime.world,
            state=session.runtime.state,
            rules_engine=session.runtime.rules_engine,
            scene_bus=session.runtime.scene_bus,
        )

        collected: list[tuple[str, dict[str, Any]]] = []

        async def _event_sink(event) -> None:
            collected.append((event.event_type, dict(event.payload)))

        pipeline_result = PipelineResult(
            success=True,
            action_type="move_area",
            narrative_hints=["The player advances."],
            time_cost=1 / 6,
        )

        with patch("app.agent_orchestration.random.random", return_value=0.0):
            events = asyncio.run(
                service.run_post_action_round(
                    shared,
                    pipeline_result,
                    session.runtime.tick_coordinator._apply_delta,
                    _event_sink,
                )
            )

        assert [event.event_type for event in events] == [
            "gm_narration",
            "npc_response",
            "teammate_response",
        ]
        assert [event_type for event_type, _ in collected] == [
            "gm_narration",
            "npc_response",
            "teammate_response",
        ]

        roles = [call["role"] for call in executor.calls]
        assert roles == ["gm", "npc", "teammate"]
        npc_scene = executor.calls[1]["scene_entries"]
        teammate_scene = executor.calls[2]["scene_entries"]
        assert any(entry["source"] == "GM" for entry in npc_scene)
        assert any(entry["content"] == "Merchant Tom eyes the movement." for entry in teammate_scene)
        assert any(entry["content"] == "Dust rolls across the market." for entry in teammate_scene)

    def test_run_post_action_round_drops_protocol_error_reactions(self) -> None:
        session = _session_for_post_action_round()
        shared = SharedContext(
            world=session.runtime.world,
            state=session.runtime.state,
            rules_engine=session.runtime.rules_engine,
            scene_bus=session.runtime.scene_bus,
        )

        class ProtocolErrorExecutor:
            async def run_agentic(
                self,
                *,
                role: str,
                context: Any,
                system_prompt: str,
                user_message: str,
                **_: Any,
            ) -> AgentResult:
                if role == "gm":
                    return AgentResult(
                        tool_results=[
                            ToolResult(
                                success=True,
                                message="The square goes quiet for a beat.",
                                metadata={"event_type": "gm_narration"},
                            )
                        ]
                    )
                return AgentResult(
                    text="",
                    tool_results=[],
                    metadata={
                        "status": "protocol_error",
                        "reason": "text_without_tool",
                        "tool_call_names": [],
                        "text_present": True,
                    },
                )

        service = AgentOrchestrationService(ProtocolErrorExecutor())  # type: ignore[arg-type]
        pipeline_result = PipelineResult(
            success=True,
            action_type="move_area",
            narrative_hints=["The player advances."],
            time_cost=1 / 6,
        )

        with patch("app.agent_orchestration.random.random", return_value=0.0):
            events = asyncio.run(
                service.run_post_action_round(
                    shared,
                    pipeline_result,
                    session.runtime.tick_coordinator._apply_delta,
                )
            )

        assert [event.event_type for event in events] == ["gm_narration"]


class TestGracefulDegradation:
    def test_no_llm_produces_no_events(self) -> None:
        """AgenticExecutor with no LLM returns empty result."""
        registry = RoleToolRegistry()
        executor = AgenticExecutor(tool_registry=registry, llm=None)
        service = AgentOrchestrationService(executor)
        session = _session_with_npc()

        npc_events = asyncio.run(service.generate_npc_response(
            session=session,
            npc_id="merchant_tom",
            player_message="Hello",
        ))

        assert npc_events == []

    def test_llm_exception_returns_error_event(self) -> None:
        """LLM exception produces an error SSE event for NPC."""

        class ExplodingLlm:
            async def generate(self, *args, **kwargs):
                raise RuntimeError("LLM down")

        registry = RoleToolRegistry()
        executor = AgenticExecutor(tool_registry=registry, llm=ExplodingLlm())
        service = AgentOrchestrationService(executor)
        session = _session_with_npc()

        events = asyncio.run(service.generate_npc_response(
            session=session,
            npc_id="merchant_tom",
            player_message="Hello",
        ))

        assert len(events) == 1
        assert events[0].event_type == "npc_response_error"


# ------------------------------------------------------------------
# TestInteractionResultToSSE — _interaction_result_to_sse converter
# ------------------------------------------------------------------


class TestInteractionResultToSSE:
    def test_npc_speech_produces_npc_response_event(self) -> None:
        from app.game_core.narrative.models import AgentResult, ToolResult
        from app.game_core.orchestration.npc_interaction import NpcInteractionResult

        result = NpcInteractionResult(
            success=True,
            npc_id="merchant_tom",
            npc_result=AgentResult(
                tool_results=[
                    ToolResult(
                        success=True,
                        message="Welcome!",
                        metadata={"event_type": "speech"},
                    ),
                ],
            ),
        )

        events = _interaction_result_to_sse(result)

        npc_events = [e for e in events if e.event_type == "npc_response"]
        assert len(npc_events) == 1
        assert npc_events[0].payload["content"] == "Welcome!"
        assert npc_events[0].payload["npc_id"] == "merchant_tom"

    def test_dialogue_options_event_emitted(self) -> None:
        from app.game_core.orchestration.npc_interaction import NpcInteractionResult

        result = NpcInteractionResult(
            success=True,
            npc_id="merchant_tom",
            dialogue_options=[
                {"text": "继续交谈", "intent": "talk"},
                {"text": "告别", "intent": "farewell"},
            ],
        )

        events = _interaction_result_to_sse(result)

        option_events = [e for e in events if e.event_type == "dialogue_options"]
        assert len(option_events) == 1
        assert option_events[0].payload["npc_id"] == "merchant_tom"
        assert len(option_events[0].payload["options"]) == 2

    def test_dialogue_check_option_maps_to_action_dispatch(self) -> None:
        from app.game_core.orchestration.npc_interaction import NpcInteractionResult

        result = NpcInteractionResult(
            success=True,
            npc_id="merchant_tom",
            dialogue_options=[
                {"text": "安抚她", "check": {"skill": "persuasion", "dc": 12}},
            ],
        )

        events = _interaction_result_to_sse(
            result,
            action_dispatcher=build_default_action_dispatcher(),
        )

        option_payload = [e for e in events if e.event_type == "dialogue_options"][0].payload["options"][0]
        assert option_payload["dispatch"]["kind"] == "action"
        assert option_payload["dispatch"]["payload"] == {
            "action_type": "skill_check",
            "params": {"skill": "persuasion", "dc": 12},
            "context": {
                "dialogue_npc_id": "merchant_tom",
                "interaction_type": "dialogue_option",
            },
        }

    def test_dialogue_context_forces_matching_npc_post_action_reaction(self) -> None:
        world = build_default_world(
            "test_world",
            world_data={
                "characters": {
                    "merchant_tom": {
                        "id": "merchant_tom",
                        "name": "Merchant Tom",
                        "personality": "A shrewd but fair merchant.",
                        "tags": ["merchant"],
                        "response_tendency": 1.0,
                    },
                    "guard_lee": {
                        "id": "guard_lee",
                        "name": "Guard Lee",
                        "personality": "A watchful town guard.",
                        "tags": ["guard"],
                        "response_tendency": 1.0,
                    },
                },
            },
        )
        runtime = build_runtime_for_world(world)
        runtime.state.player.restore({
            "character_name": "TestPlayer",
            "character_class": "warrior",
            "current_area": "town",
            "current_location": "market",
        })
        runtime.state.areas.get_area("town").npc_locations["merchant_tom"] = "market"
        runtime.state.areas.get_area("town").npc_locations["guard_lee"] = "market"
        shared = SharedContext(
            world=runtime.world,
            state=runtime.state,
            rules_engine=runtime.rules_engine,
            scene_bus=runtime.scene_bus,
        )

        class TargetedNpcExecutor:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            async def run_agentic(
                self,
                *,
                role: str,
                context: Any,
                system_prompt: str = "",
                **_: Any,
            ) -> AgentResult:
                character_id = context.metadata.get("character_id")
                self.calls.append({
                    "role": role,
                    "character_id": character_id,
                    "system_prompt": system_prompt,
                })
                if role == "gm":
                    if "next actionable player dialogue options" in system_prompt:
                        return AgentResult(
                            tool_results=[
                                ToolResult(
                                    success=True,
                                    message="2 options generated.",
                                    metadata={
                                        "tool": "suggest_options",
                                        "options": [
                                            {"text": "继续追问细节"},
                                            {"text": "作势离开", "action": "leave"},
                                        ],
                                    },
                                )
                            ]
                        )
                    return AgentResult()
                if role == "npc":
                    return AgentResult(
                        tool_results=[
                            ToolResult(
                                success=True,
                                message=f"{character_id} reacts",
                                metadata={"event_type": "speech"},
                            )
                        ]
                    )
                return AgentResult()

        executor = TargetedNpcExecutor()
        service = AgentOrchestrationService(executor)  # type: ignore[arg-type]
        result = PipelineResult(
            success=True,
            action_type="skill_check",
            time_cost=1 / 6,
            metadata={
                "action_context": {
                    "dialogue_npc_id": "merchant_tom",
                    "interaction_type": "dialogue_option",
                }
            },
        )

        events = asyncio.run(
            service.run_post_action_round(
                shared,
                result,
                runtime.tick_coordinator._apply_delta,
            )
        )

        npc_calls = [call for call in executor.calls if call["role"] == "npc"]
        assert len(npc_calls) == 1
        assert npc_calls[0]["character_id"] == "merchant_tom"
        npc_events = [event for event in events if event.event_type == "npc_response"]
        assert len(npc_events) == 1
        assert npc_events[0].payload["npc_id"] == "merchant_tom"
        option_events = [event for event in events if event.event_type == "dialogue_options"]
        assert len(option_events) == 1
        assert option_events[0].payload["npc_id"] == "merchant_tom"
        assert option_events[0].payload["options"]
        assert any("dispatch" in option for option in option_events[0].payload["options"])
        unavailable_events = [
            event for event in events if event.event_type == "dialogue_options_unavailable"
        ]
        assert unavailable_events == []

    def test_dialogue_follow_up_emits_unavailable_when_agent_returns_no_options(self) -> None:
        world = build_default_world(
            "test_world",
            world_data={
                "characters": {
                    "merchant_tom": {
                        "id": "merchant_tom",
                        "name": "Merchant Tom",
                        "personality": "A shrewd but fair merchant.",
                        "tags": ["merchant"],
                        "response_tendency": 1.0,
                    },
                },
            },
        )
        runtime = build_runtime_for_world(world)
        runtime.state.player.restore({
            "character_name": "TestPlayer",
            "character_class": "warrior",
            "current_area": "town",
            "current_location": "market",
        })
        runtime.state.areas.get_area("town").npc_locations["merchant_tom"] = "market"
        shared = SharedContext(
            world=runtime.world,
            state=runtime.state,
            rules_engine=runtime.rules_engine,
            scene_bus=runtime.scene_bus,
        )

        class NoOptionsExecutor:
            async def run_agentic(self, *, role: str, context: Any, **_: Any) -> AgentResult:
                if role == "npc":
                    return AgentResult(
                        tool_results=[
                            ToolResult(
                                success=True,
                                message="merchant_tom reacts",
                                metadata={"event_type": "speech"},
                            )
                        ]
                    )
                return AgentResult()

        service = AgentOrchestrationService(NoOptionsExecutor())  # type: ignore[arg-type]
        result = PipelineResult(
            success=True,
            action_type="skill_check",
            time_cost=1 / 6,
            metadata={
                "action_context": {
                    "dialogue_npc_id": "merchant_tom",
                    "interaction_type": "dialogue_option",
                }
            },
        )

        events = asyncio.run(
            service.run_post_action_round(
                shared,
                result,
                runtime.tick_coordinator._apply_delta,
            )
        )

        option_events = [event for event in events if event.event_type == "dialogue_options"]
        assert option_events == []
        unavailable_events = [
            event for event in events if event.event_type == "dialogue_options_unavailable"
        ]
        assert len(unavailable_events) == 1
        assert unavailable_events[0].payload["npc_id"] == "merchant_tom"
        assert unavailable_events[0].payload["code"] == "empty_options"
        assert unavailable_events[0].payload["recoverable"] is True

    def test_farewell_option_maps_to_local_dispatch(self) -> None:
        from app.game_core.orchestration.npc_interaction import NpcInteractionResult

        result = NpcInteractionResult(
            success=True,
            npc_id="merchant_tom",
            dialogue_options=[
                {"text": "告别", "intent": "farewell"},
            ],
        )

        events = _interaction_result_to_sse(result)

        option_payload = [e for e in events if e.event_type == "dialogue_options"][0].payload["options"][0]
        assert option_payload["dispatch"]["kind"] == "local"
        assert option_payload["dispatch"]["payload"] == {"action": "leave_dialogue"}

    def test_empty_result_produces_only_options(self) -> None:
        from app.game_core.orchestration.npc_interaction import NpcInteractionResult

        result = NpcInteractionResult(
            success=True,
            npc_id="npc1",
            dialogue_options=[{"text": "继续交谈", "intent": "talk"}],
        )

        events = _interaction_result_to_sse(result)

        assert len(events) == 1
        assert events[0].event_type == "dialogue_options"

    def test_gm_narration_included_after_npc(self) -> None:
        from app.game_core.narrative.models import AgentResult, ToolResult
        from app.game_core.orchestration.npc_interaction import NpcInteractionResult

        result = NpcInteractionResult(
            success=True,
            npc_id="npc1",
            npc_result=AgentResult(
                tool_results=[
                    ToolResult(
                        success=True,
                        message="Hello!",
                        metadata={"event_type": "speech"},
                    ),
                ],
            ),
            gm_result=AgentResult(
                tool_results=[
                    ToolResult(
                        success=True,
                        message="The wind howls.",
                        metadata={"event_type": "gm_narration"},
                    ),
                ],
            ),
        )

        events = _interaction_result_to_sse(result)
        event_types = [e.event_type for e in events]

        npc_idx = event_types.index("npc_response")
        gm_idx = event_types.index("gm_narration")
        assert npc_idx < gm_idx, "NPC response must come before GM narration"

    def test_teammate_events_after_gm(self) -> None:
        from app.game_core.narrative.models import AgentResult, ToolResult
        from app.game_core.orchestration.npc_interaction import NpcInteractionResult

        result = NpcInteractionResult(
            success=True,
            npc_id="npc1",
            gm_result=AgentResult(
                tool_results=[
                    ToolResult(
                        success=True,
                        message="A shadow falls.",
                        metadata={"event_type": "gm_comment"},
                    ),
                ],
            ),
            teammate_results={
                "paladin_aria": AgentResult(
                    tool_results=[
                        ToolResult(
                            success=True,
                            message="Be careful.",
                            metadata={"event_type": "speech"},
                        ),
                    ],
                ),
            },
        )

        events = _interaction_result_to_sse(result)
        event_types = [e.event_type for e in events]

        gm_idx = event_types.index("gm_comment")
        tm_idx = event_types.index("teammate_response")
        assert gm_idx < tm_idx, "GM must come before Teammate"


# ------------------------------------------------------------------
# TestRunNpcInteraction — integration via AgentOrchestrationService
# ------------------------------------------------------------------


class TestRunNpcInteraction:
    def test_run_npc_interaction_produces_sse(self) -> None:
        service, llm = _build_service([
            _npc_speak_response("Hello traveler!"),
            _stop_response(),
        ])
        session = _session_with_npc()

        events = asyncio.run(service.run_npc_interaction(
            session=session,
            npc_id="merchant_tom",
            player_message="Hello!",
            intent="talk",
        ))

        npc_events = [e for e in events if e.event_type == "npc_response"]
        assert len(npc_events) >= 1
        assert npc_events[0].payload["content"] == "Hello traveler!"

    def test_run_npc_interaction_unknown_npc_returns_empty(self) -> None:
        service, _ = _build_service()
        session = _session_with_npc()

        events = asyncio.run(service.run_npc_interaction(
            session=session,
            npc_id="nonexistent",
            player_message="Hello?",
        ))

        assert events == []

    def test_run_npc_interaction_always_has_dialogue_options(self) -> None:
        service, _ = _build_service([
            _npc_speak_response("Hi."),
            _stop_response(),
        ])
        session = _session_with_npc()

        events = asyncio.run(service.run_npc_interaction(
            session=session,
            npc_id="merchant_tom",
            player_message="Hi",
        ))

        option_events = [e for e in events if e.event_type == "dialogue_options"]
        assert len(option_events) == 1

    def test_run_npc_interaction_invalid_agent_response_maps_to_npc_response_error(self) -> None:
        service, _ = _build_service([
            _stop_response("I should have used speak."),
        ])
        session = _session_with_npc()

        events = asyncio.run(service.run_npc_interaction(
            session=session,
            npc_id="merchant_tom",
            player_message="Hello",
        ))

        assert len(events) == 1
        assert events[0].event_type == "npc_response_error"
        assert events[0].payload["code"] == "invalid_agent_response"
        assert events[0].payload["reason"] == "text_without_tool"


class TestRunPrivateChat:
    def test_run_private_chat_invalid_agent_response_maps_to_npc_response_error(self) -> None:
        service, _ = _build_service([
            _stop_response("I should have used speak."),
        ])
        session = _session_with_npc()

        events = asyncio.run(service.run_private_chat(
            session=session,
            npc_id="merchant_tom",
            player_message="Can we speak in private?",
        ))

        assert len(events) == 1
        assert events[0].event_type == "npc_response_error"
        assert events[0].payload["code"] == "invalid_agent_response"
        assert events[0].payload["reason"] == "text_without_tool"
