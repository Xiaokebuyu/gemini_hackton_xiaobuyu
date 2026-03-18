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
    _serialize_opening_options,
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
from app.game_core.orchestration.models import PipelineResult, SSEEvent
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


def _test_world_tags() -> dict[str, Any]:
    return {
        "profession": {"id": "profession", "tags": ["merchant", "warrior", "guard"]},
        "ancestry": {"id": "ancestry", "tags": ["human"]},
        "affinity": {"id": "affinity", "tags": ["holy"]},
    }


def _world_with_npc() -> WorldInstance:
    """Build a world with one test NPC."""
    world = build_default_world(
        "test_world",
        world_data={
            "tags": _test_world_tags(),
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
            "tags": _test_world_tags(),
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
                        ok=True,
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
                        ok=True,
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
                        ok=True,
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

    assert result.executed is True
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
        )

        assert "Tom" in prompt
        assert "Shrewd merchant" in prompt
        assert "friend" in prompt
        # 3-C: compact Chinese relationship format
        assert "好感：30" in prompt
        # 3-C: impressions no longer injected into prompt
        assert "Bought a sword" not in prompt

    def test_npc_prompt_passive_mode_skips_direct_response_constraint(self) -> None:
        prompt = _build_npc_prompt_text(
            npc_profile={"name": "Tom", "personality": "Shrewd merchant"},
            disposition={"approval": 30, "trust": 10, "fear": 0, "romance": 0},
            stage="stranger",
            is_passive=True,
        )

        assert "没有人在和你说话" in prompt
        assert "pass_turn" not in prompt

    def test_npc_prompt_default_mode_forces_direct_response(self) -> None:
        prompt = _build_npc_prompt_text(
            npc_profile={"name": "Tom", "personality": "Shrewd merchant"},
            disposition={"approval": 30, "trust": 10, "fear": 0, "romance": 0},
            stage="stranger",
            is_passive=False,
        )

        assert "被对话时你必须回应" in prompt

    def test_npc_prompt_handles_empty_profile(self) -> None:
        prompt = _build_npc_prompt_text(
            npc_profile={},
            disposition={},
            stage="stranger",
        )

        assert "Unknown NPC" in prompt
        assert "stranger" in prompt
        # 3-C: "No previous memories" block removed; impressions no longer injected
        assert "No previous memories" not in prompt

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
                    ok=True,
                    message="Hello, traveler!",
                    metadata={"event_type": "speech"},
                ),
                ToolResult(
                    ok=True,
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
                ToolResult(ok=False, message="error", metadata={"event_type": "speech"}),
                ToolResult(ok=True, message="ok", metadata={"event_type": "speech"}),
            ],
        )

        events = _npc_result_to_sse("npc1", result)

        assert len(events) == 1
        assert events[0].payload["content"] == "ok"

    def test_npc_result_handles_refuse(self) -> None:
        result = AgentResult(
            tool_results=[
                ToolResult(
                    ok=True,
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
                    ok=True,
                    message="The market bustles with activity.",
                    metadata={"event_type": "gm_narration"},
                ),
                ToolResult(
                    ok=True,
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

    def test_gm_result_preserves_comment_tone(self) -> None:
        result = AgentResult(
            tool_results=[
                ToolResult(
                    ok=True,
                    message="That landed harder than you meant it to.",
                    metadata={"event_type": "gm_comment", "tone": "introspective"},
                ),
            ],
        )

        events = _gm_result_to_sse(result)

        assert events == [
            SSEEvent(
                event_type="gm_comment",
                payload={
                    "content": "That landed harder than you meant it to.",
                    "tone": "introspective",
                },
            )
        ]

    def test_gm_result_pass_turn_produces_no_events(self) -> None:
        result = AgentResult(
            tool_results=[
                ToolResult(ok=True, message="", metadata={"event_type": "pass"}),
            ],
        )

        events = _gm_result_to_sse(result)

        assert events == []

    def test_teammate_result_extracts_speech_and_emote(self) -> None:
        result = AgentResult(
            tool_results=[
                ToolResult(
                    ok=True,
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

        assert result.executed is True
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

    def test_generate_npc_response_text_only_returns_npc_response(self) -> None:
        """P29-A1: Text-only NPC response is gracefully wrapped as synthetic
        speech and produces a normal npc_response SSE event."""
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
        assert events[0].event_type == "npc_response"
        assert events[0].payload["npc_id"] == "merchant_tom"
        assert events[0].payload["content"] == "I should have used speak."


class TestPostActionReactions:
    def test_gm_reaction_with_pass_produces_no_events(self) -> None:
        # LLM calls pass_turn
        service, _ = _build_service([
            _gm_pass_turn_response(),
            _stop_response(),
        ])
        session = _session_with_npc()
        pipeline_result = PipelineResult(
            executed=True,
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
            executed=True,
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
        pipeline_result = PipelineResult(executed=True, action_type="rest")

        events = asyncio.run(service.generate_post_action_reactions(
            session=session, result=pipeline_result,
        ))

        teammate_events = [e for e in events if e.event_type == "teammate_response"]
        assert teammate_events == []

    def test_run_post_action_round_skips_meta_actions_from_npc_and_teammate(self) -> None:
        class NoReactionExecutor:
            def __init__(self) -> None:
                self.roles: list[str] = []

            async def run_agentic(
                self,
                *,
                role: str,
                context: Any,
                system_prompt: str,
                user_message: str,
                **_: Any,
            ) -> AgentResult:
                self.roles.append(role)
                if role == "gm":
                    return AgentResult(
                        tool_results=[
                            ToolResult(
                                ok=True,
                                message="Player checked the inventory.",
                                metadata={"event_type": "gm_narration"},
                            )
                        ]
                    )
                return AgentResult(
                    metadata={
                        "status": "completed",
                        "finish_reason": "pass_turn",
                    }
                )

        service = AgentOrchestrationService(NoReactionExecutor())  # type: ignore[arg-type]
        session = _session_with_npc()
        shared = SharedContext(
            world=session.runtime.world,
            state=session.runtime.state,
            rules_engine=session.runtime.rules_engine,
            scene_bus=session.runtime.scene_bus,
        )

        pipeline_result = PipelineResult(
            executed=True,
            action_type="look_inventory",
            narrative_hints=["Player checks inventory."],
            time_cost=0.1,
        )

        events = asyncio.run(service.run_post_action_round(
            shared,
            pipeline_result,
            session.runtime.tick_coordinator._apply_delta,
        ))

        assert [e.event_type for e in events] == ["gm_narration"]
        assert service._executor.roles == ["gm"]  # type: ignore[attr-defined]

    def test_run_post_action_round_marks_passive_observation_context(self) -> None:
        class CaptureExecutor:
            def __init__(self) -> None:
                self.user_messages: dict[str, str] = {}

            async def run_agentic(
                self,
                *,
                role: str,
                context: Any,
                system_prompt: str,
                user_message: str,
                **_: Any,
            ) -> AgentResult:
                self.user_messages[role] = user_message
                if role == "gm":
                    return AgentResult(
                        tool_results=[
                            ToolResult(
                                ok=True,
                                message="Dust rolls across the market.",
                                metadata={"event_type": "gm_narration"},
                            )
                        ]
                    )
                if role == "npc":
                    return AgentResult(
                        metadata={
                            "status": "completed",
                            "finish_reason": "pass_turn",
                        }
                    )
                return AgentResult(
                    metadata={
                        "status": "completed",
                        "finish_reason": "pass_turn",
                    }
                )

        executor = CaptureExecutor()
        service = AgentOrchestrationService(executor)  # type: ignore[arg-type]
        session = _session_for_post_action_round()
        shared = SharedContext(
            world=session.runtime.world,
            state=session.runtime.state,
            rules_engine=session.runtime.rules_engine,
            scene_bus=session.runtime.scene_bus,
        )
        pipeline_result = PipelineResult(
            executed=True,
            action_type="move_area",
            narrative_hints=["Player shifts to a new alley."],
            time_cost=1 / 6,
        )

        with patch("app.agent_orchestration.random.random", return_value=0.0):
            asyncio.run(service.run_post_action_round(
                shared,
                pipeline_result,
                session.runtime.tick_coordinator._apply_delta,
            ))

        npc_user_message = executor.user_messages.get("npc", "")
        assert "passive_observation" in npc_user_message
        assert "Player shifts to a new alley." in npc_user_message
        teammate_user_message = executor.user_messages.get("teammate", "")
        assert teammate_user_message is not None

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
            executed=True,
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
        assert any(
            e["source"] == "NPC:merchant_tom"
            for e in session.runtime.state.scene.snapshot()["entries"]
        )
        assert any(
            e["source"] == "TEAMMATE:paladin_aria"
            for e in session.runtime.state.scene.snapshot()["entries"]
        )

    def test_run_post_action_round_uses_clue_specific_round_and_caps_teammates(self) -> None:
        class ClueExecutor:
            def __init__(self) -> None:
                self.roles: list[str] = []

            async def run_agentic(
                self,
                *,
                role: str,
                context: Any,
                system_prompt: str,
                user_message: str,
                **_: Any,
            ) -> AgentResult:
                self.roles.append(role)
                if role == "gm":
                    return AgentResult(
                        tool_results=[
                            ToolResult(
                                ok=True,
                                message="血迹指得很直，却还不肯把终点写在墙上。",
                                metadata={"event_type": "gm_comment"},
                            )
                        ]
                    )
                return AgentResult(
                    tool_results=[
                        ToolResult(
                            ok=True,
                            message="这不像意外，更像有人故意把人往后门拖。",
                            metadata={"event_type": "speech"},
                        )
                    ]
                )

        session = _session_for_post_action_round()
        session.runtime.world.characters.load(
            {
                "ranger_lia": {
                    "id": "ranger_lia",
                    "name": "游侠莉娅",
                    "personality": "善于追踪，讲话干脆。",
                    "tags": ["human"],
                    "response_tendency": 1.0,
                },
                "scholar_milo": {
                    "id": "scholar_milo",
                    "name": "学者米洛",
                    "personality": "谨慎，多疑，喜欢从细节里找逻辑。",
                    "tags": ["human"],
                    "response_tendency": 1.0,
                },
            }
        )
        session.runtime.state.party.restore(
            {
                "members": {
                    "paladin_aria": {"status": "active"},
                    "ranger_lia": {"status": "active"},
                    "scholar_milo": {"status": "active"},
                },
            }
        )
        service = AgentOrchestrationService(ClueExecutor())  # type: ignore[arg-type]
        shared = SharedContext(
            world=session.runtime.world,
            state=session.runtime.state,
            rules_engine=session.runtime.rules_engine,
            scene_bus=session.runtime.scene_bus,
            companion_manager=session.runtime.companion_manager,
        )
        result = PipelineResult(
            executed=True,
            action_type="investigate_clue",
            commands=[
                Command(
                    type="investigate_clue",
                    params={"interactable_id": "blood_trail_clue"},
                    source="player",
                )
            ],
            metadata={
                "clue_id": "blood_trail",
                "interactable_id": "blood_trail_clue",
                "clue_name": "拖拽血迹",
                "description": "血迹一直拖向后门。",
                "options": [
                    {"id": "examine", "label": "仔细检查"},
                    {"id": "follow", "label": "顺着痕迹追过去"},
                ],
                "party_prompt_hints": ["血迹在拐角处突然变浅。"],
            },
        )

        with patch("app.agent_orchestration.random.random", return_value=0.0):
            events = asyncio.run(
                service.run_post_action_round(
                    shared,
                    result,
                    session.runtime.tick_coordinator._apply_delta,
                )
            )

        event_types = [event.event_type for event in events]
        assert event_types[0] == "gm_comment"
        assert "dialogue_options" in event_types
        assert "npc_response" not in event_types
        teammate_events = [event for event in events if event.event_type == "teammate_response"]
        assert len(teammate_events) == 2
        dialogue_event = next(event for event in events if event.event_type == "dialogue_options")
        assert [option["id"] for option in dialogue_event.payload["options"]] == ["examine", "follow"]
        assert all(
            option["dispatch"]["payload"]["action_type"] == "resolve_clue_option"
            for option in dialogue_event.payload["options"]
        )

    def test_run_post_action_round_uses_clue_resolution_fallback_when_llm_unavailable(self) -> None:
        """When LLM executor returns empty result, resolve falls back to deterministic comment."""
        class EmptyExecutor:
            async def run_agentic(self, **_: Any) -> AgentResult:
                return AgentResult()

        session = _session_for_post_action_round()
        service = AgentOrchestrationService(EmptyExecutor())  # type: ignore[arg-type]
        shared = SharedContext(
            world=session.runtime.world,
            state=session.runtime.state,
            rules_engine=session.runtime.rules_engine,
            scene_bus=session.runtime.scene_bus,
        )
        result = PipelineResult(
            executed=True,
            action_type="resolve_clue_option",
            metadata={
                "clue_id": "blood_trail",
                "clue_name": "拖拽血迹",
                "option_id": "follow",
                "option_label": "顺着痕迹追过去",
                "passed": True,
                "effect_types": ["unlock_sub_location", "advance_quest"],
            },
        )

        events = asyncio.run(
            service.run_post_action_round(
                shared,
                result,
                session.runtime.tick_coordinator._apply_delta,
            )
        )

        # Fallback deterministic comment should appear + panel closed.
        event_types = [event.event_type for event in events]
        assert "gm_comment" in event_types
        assert "dialogue_options_unavailable" in event_types
        assert "露出了一条能追下去的路" in events[0].payload["content"]
        assert events[-1].payload["reason"] == "clue_resolved"

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
                                ok=True,
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
            executed=True,
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

    def test_npc_passive_reaction_events_carry_passive_flag(self) -> None:
        """Passive NPC reactions must include passive=True in SSE payload."""

        class SpeechExecutor:
            async def run_agentic(
                self, *, role: str, context: Any, system_prompt: str,
                user_message: str, **_: Any,
            ) -> AgentResult:
                if role == "gm":
                    return AgentResult(
                        tool_results=[ToolResult(
                            ok=True, message="Wind blows.",
                            metadata={"event_type": "gm_narration"},
                        )]
                    )
                if role == "npc":
                    return AgentResult(
                        tool_results=[ToolResult(
                            ok=True, message="Be careful out there.",
                            metadata={"event_type": "speech"},
                        )]
                    )
                return AgentResult(metadata={"status": "completed", "finish_reason": "pass_turn"})

        session = _session_for_post_action_round()
        service = AgentOrchestrationService(SpeechExecutor())  # type: ignore[arg-type]
        shared = SharedContext(
            world=session.runtime.world,
            state=session.runtime.state,
            rules_engine=session.runtime.rules_engine,
            scene_bus=session.runtime.scene_bus,
        )
        pipeline_result = PipelineResult(
            executed=True, action_type="move_area",
            narrative_hints=["The player moves."], time_cost=1 / 6,
        )

        with patch("app.agent_orchestration.random.random", return_value=0.0):
            events = asyncio.run(service.run_post_action_round(
                shared, pipeline_result,
                session.runtime.tick_coordinator._apply_delta,
            ))

        npc_events = [e for e in events if e.event_type == "npc_response"]
        assert len(npc_events) >= 1
        for e in npc_events:
            assert e.payload.get("passive") is True, "passive flag missing from NPC passive reaction"

    def test_run_post_action_round_uses_presence_fallback_for_static_npcs(self) -> None:
        class SpeechExecutor:
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
                        tool_results=[ToolResult(
                            ok=True,
                            message="The street hums with passing footsteps.",
                            metadata={"event_type": "gm_narration"},
                        )]
                    )
                if role == "npc":
                    return AgentResult(
                        tool_results=[ToolResult(
                            ok=True,
                            message="Easy there, traveler.",
                            metadata={"event_type": "speech"},
                        )]
                    )
                return AgentResult(
                    metadata={"status": "completed", "finish_reason": "pass_turn"}
                )

        world = build_default_world(
            "test_world",
            world_data={
                "tags": _test_world_tags(),
                "characters": {
                    "merchant_tom": {
                        "id": "merchant_tom",
                        "name": "Merchant Tom",
                        "personality": "A shrewd but fair merchant.",
                        "response_tendency": 1.0,
                    },
                },
            },
        )
        merchant = world.characters.get("merchant_tom")
        assert merchant is not None
        merchant.current_area = "town"
        merchant.current_location = "market"
        runtime = build_runtime_for_world(world)
        runtime.state.player.restore({
            "character_name": "TestPlayer",
            "character_class": "warrior",
            "current_area": "town",
            "current_location": "market",
        })
        shared = SharedContext(
            world=runtime.world,
            state=runtime.state,
            rules_engine=runtime.rules_engine,
            scene_bus=runtime.scene_bus,
        )
        service = AgentOrchestrationService(SpeechExecutor())  # type: ignore[arg-type]
        pipeline_result = PipelineResult(
            executed=True,
            action_type="move_area",
            narrative_hints=["The player pushes through the crowd."],
            time_cost=1 / 6,
        )

        with patch("app.agent_orchestration.random.random", return_value=0.0):
            events = asyncio.run(service.run_post_action_round(
                shared,
                pipeline_result,
                runtime.tick_coordinator._apply_delta,
            ))

        assert any(event.event_type == "npc_response" for event in events)

    def test_run_post_action_round_uses_system_tags_for_npc_response_gate(self) -> None:
        class SpeechExecutor:
            async def run_agentic(
                self,
                *,
                role: str,
                context: Any,
                system_prompt: str,
                user_message: str,
                **_: Any,
            ) -> AgentResult:
                if role == "npc":
                    return AgentResult(
                        tool_results=[ToolResult(
                            ok=True,
                            message="Stay sharp. Something is wrong here.",
                            metadata={"event_type": "speech"},
                        )]
                    )
                return AgentResult(
                    metadata={"status": "completed", "finish_reason": "pass_turn"}
                )

        world = build_default_world(
            "test_world",
            world_data={
                "tags": _test_world_tags(),
                "characters": {
                    "merchant_tom": {
                        "id": "merchant_tom",
                        "name": "Merchant Tom",
                        "personality": "A shrewd but fair merchant.",
                        "response_tendency": 0.0,
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
        runtime.state.scene.add_entry(SceneEntry(
            source="ENGINE",
            content="[move_area] A dangerous hush falls over the market.",
            visibility="system",
            tags=["CRISIS"],
        ))
        shared = SharedContext(
            world=runtime.world,
            state=runtime.state,
            rules_engine=runtime.rules_engine,
            scene_bus=runtime.scene_bus,
        )
        service = AgentOrchestrationService(SpeechExecutor())  # type: ignore[arg-type]
        pipeline_result = PipelineResult(
            executed=True,
            action_type="move_area",
            narrative_hints=["The crowd recoils as danger spreads."],
            time_cost=1 / 6,
        )

        with patch("app.agent_orchestration.random.random", return_value=0.2):
            events = asyncio.run(service.run_post_action_round(
                shared,
                pipeline_result,
                runtime.tick_coordinator._apply_delta,
            ))

        assert any(event.event_type == "npc_response" for event in events)

    def test_resolve_clue_emits_dialogue_options_unavailable(self) -> None:
        """resolve_clue_option always appends dialogue_options_unavailable to close the panel."""

        class EmptyExecutor:
            async def run_agentic(self, **_: Any) -> AgentResult:
                return AgentResult()

        session = _session_for_post_action_round()
        service = AgentOrchestrationService(EmptyExecutor())  # type: ignore[arg-type]
        shared = SharedContext(
            world=session.runtime.world,
            state=session.runtime.state,
            rules_engine=session.runtime.rules_engine,
            scene_bus=session.runtime.scene_bus,
        )
        result = PipelineResult(
            executed=True,
            action_type="resolve_clue_option",
            metadata={
                "clue_id": "blood_trail",
                "clue_name": "拖拽血迹",
                "option_id": "examine",
                "option_label": "仔细检查",
                "passed": False,
                "effect_types": [],
            },
        )

        events = asyncio.run(
            service.run_post_action_round(
                shared,
                result,
                session.runtime.tick_coordinator._apply_delta,
            )
        )

        event_types = [event.event_type for event in events]
        assert "dialogue_options_unavailable" in event_types
        unavailable_event = next(e for e in events if e.event_type == "dialogue_options_unavailable")
        assert unavailable_event.payload["reason"] == "clue_resolved"

    def test_resolve_clue_option_generates_gm_narration(self) -> None:
        """R2-A: resolve_clue_option uses LLM GM narration when executor succeeds."""
        class GmNarrationExecutor:
            async def run_agentic(self, *, role: str, **_: Any) -> AgentResult:
                if role == "gm":
                    return AgentResult(
                        tool_results=[
                            ToolResult(
                                ok=True,
                                message="痕迹在这里断掉了，但方向已经清晰。",
                                metadata={"event_type": "gm_narration"},
                            )
                        ]
                    )
                return AgentResult()

        session = _session_for_post_action_round()
        service = AgentOrchestrationService(GmNarrationExecutor())  # type: ignore[arg-type]
        shared = SharedContext(
            world=session.runtime.world,
            state=session.runtime.state,
            rules_engine=session.runtime.rules_engine,
            scene_bus=session.runtime.scene_bus,
        )
        result = PipelineResult(
            executed=True,
            action_type="resolve_clue_option",
            metadata={
                "clue_id": "dust_marks",
                "clue_name": "灰尘痕迹",
                "option_id": "examine",
                "option_label": "仔细检查",
                "passed": True,
                "effect_types": [],
            },
        )

        events = asyncio.run(
            service.run_post_action_round(
                shared,
                result,
                session.runtime.tick_coordinator._apply_delta,
            )
        )

        event_types = [event.event_type for event in events]
        # LLM gm_narration should appear, not the deterministic gm_comment fallback.
        assert "gm_narration" in event_types
        assert "gm_comment" not in event_types
        narration_event = next(e for e in events if e.event_type == "gm_narration")
        assert "痕迹在这里断掉了" in narration_event.payload["content"]
        # Panel still closed at the end.
        assert event_types[-1] == "dialogue_options_unavailable"

    def test_resolve_clue_option_generates_teammate_reactions(self) -> None:
        """R2-A: resolve_clue_option generates teammate reactions when party is present."""
        class ResolveExecutor:
            def __init__(self) -> None:
                self.roles: list[str] = []

            async def run_agentic(self, *, role: str, **_: Any) -> AgentResult:
                self.roles.append(role)
                if role == "gm":
                    return AgentResult(
                        tool_results=[
                            ToolResult(
                                ok=True,
                                message="谜题的一角掀开了。",
                                metadata={"event_type": "gm_narration"},
                            )
                        ]
                    )
                if role == "teammate":
                    return AgentResult(
                        tool_results=[
                            ToolResult(
                                ok=True,
                                message="这条线比我想象的还要直。",
                                metadata={"event_type": "speech"},
                            )
                        ]
                    )
                return AgentResult()

        session = _session_for_post_action_round()
        executor_obj = ResolveExecutor()
        service = AgentOrchestrationService(executor_obj)  # type: ignore[arg-type]
        shared = SharedContext(
            world=session.runtime.world,
            state=session.runtime.state,
            rules_engine=session.runtime.rules_engine,
            scene_bus=session.runtime.scene_bus,
            companion_manager=session.runtime.companion_manager,
        )
        result = PipelineResult(
            executed=True,
            action_type="resolve_clue_option",
            metadata={
                "clue_id": "blood_trail",
                "clue_name": "拖拽血迹",
                "option_id": "follow",
                "option_label": "顺着痕迹追过去",
                "passed": True,
                "effect_types": [],
            },
        )

        with patch("app.agent_orchestration.random.random", return_value=0.0):
            events = asyncio.run(
                service.run_post_action_round(
                    shared,
                    result,
                    session.runtime.tick_coordinator._apply_delta,
                )
            )

        event_types = [event.event_type for event in events]
        assert "gm_narration" in event_types
        assert "teammate_response" in event_types
        assert "dialogue_options_unavailable" in event_types
        # dialogue_options panel should NOT appear for resolve (only for investigate).
        assert "dialogue_options" not in event_types
        # Teammate called.
        assert "teammate" in executor_obj.roles

    def test_party_prompt_hints_injected_into_teammate_prompt(self) -> None:
        """R2-B: party_prompt_hints from clue_payload are appended to the teammate system prompt."""
        captured_prompts: list[str] = []

        class CapturingExecutor:
            async def run_agentic(
                self,
                *,
                role: str,
                system_prompt: str,
                **_: Any,
            ) -> AgentResult:
                if role == "teammate":
                    captured_prompts.append(system_prompt)
                    return AgentResult(
                        tool_results=[
                            ToolResult(
                                ok=True,
                                message="我注意到了那条线索。",
                                metadata={"event_type": "speech"},
                            )
                        ]
                    )
                return AgentResult()

        session = _session_for_post_action_round()
        service = AgentOrchestrationService(CapturingExecutor())  # type: ignore[arg-type]
        shared = SharedContext(
            world=session.runtime.world,
            state=session.runtime.state,
            rules_engine=session.runtime.rules_engine,
            scene_bus=session.runtime.scene_bus,
            companion_manager=session.runtime.companion_manager,
        )
        result = PipelineResult(
            executed=True,
            action_type="investigate_clue",
            metadata={
                "clue_id": "old_map",
                "interactable_id": "old_map_interactable",
                "clue_name": "旧地图",
                "description": "一张褪色的地图，角落里有标记。",
                "options": [
                    {"id": "study", "label": "仔细研读"},
                    {"id": "compare", "label": "对照现有地图"},
                ],
                "party_prompt_hints": ["注意地图上的十字标记", "西北角标注有特殊符号"],
            },
        )

        with patch("app.agent_orchestration.random.random", return_value=0.0):
            asyncio.run(
                service.run_post_action_round(
                    shared,
                    result,
                    session.runtime.tick_coordinator._apply_delta,
                )
            )

        assert len(captured_prompts) > 0
        prompt = captured_prompts[0]
        assert "## 线索提示" in prompt
        assert "注意地图上的十字标记" in prompt
        assert "西北角标注有特殊符号" in prompt


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
            completed=True,
            npc_id="merchant_tom",
            npc_result=AgentResult(
                tool_results=[
                    ToolResult(
                        ok=True,
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
            completed=True,
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

    def test_dialogue_check_option_maps_to_interact_dispatch(self) -> None:
        from app.game_core.orchestration.npc_interaction import NpcInteractionResult

        result = NpcInteractionResult(
            completed=True,
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
        assert option_payload["dispatch"]["kind"] == "interact"
        assert option_payload["dispatch"]["payload"] == {
            "scope": "public",
            "intent": "talk",
            "target_kind": "npc",
            "target_id": "merchant_tom",
            "message": "安抚她",
            "check_skill": "persuasion",
            "check_dc": 12,
        }

    def test_dialogue_context_forces_matching_npc_post_action_reaction(self) -> None:
        world = build_default_world(
            "test_world",
            world_data={
                "tags": _test_world_tags(),
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
                    if "对话选项" in system_prompt or "dialogue options" in system_prompt.lower():
                        return AgentResult(
                            tool_results=[
                                ToolResult(
                                    ok=True,
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
                                ok=True,
                                message=f"{character_id} reacts",
                                metadata={"event_type": "speech"},
                            )
                        ]
                    )
                return AgentResult()

        executor = TargetedNpcExecutor()
        service = AgentOrchestrationService(executor)  # type: ignore[arg-type]
        result = PipelineResult(
            executed=True,
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
                "tags": _test_world_tags(),
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
                                ok=True,
                                message="merchant_tom reacts",
                                metadata={"event_type": "speech"},
                            )
                        ]
                    )
                return AgentResult()

        service = AgentOrchestrationService(NoOptionsExecutor())  # type: ignore[arg-type]
        result = PipelineResult(
            executed=True,
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
            completed=True,
            npc_id="merchant_tom",
            dialogue_options=[
                {"text": "告别", "intent": "farewell"},
            ],
        )

        events = _interaction_result_to_sse(result)

        option_payload = [e for e in events if e.event_type == "dialogue_options"][0].payload["options"][0]
        assert option_payload["dispatch"]["kind"] == "local"
        assert option_payload["dispatch"]["payload"] == {"action": "leave_dialogue"}

    def test_talk_option_maps_to_interact_dispatch_with_message(self) -> None:
        from app.game_core.orchestration.npc_interaction import NpcInteractionResult

        result = NpcInteractionResult(
            completed=True,
            npc_id="merchant_tom",
            dialogue_options=[
                {"text": "追问关于那封信的事", "intent": "talk"},
            ],
        )

        events = _interaction_result_to_sse(result)

        option_payload = [e for e in events if e.event_type == "dialogue_options"][0].payload["options"][0]
        assert option_payload["dispatch"]["kind"] == "interact"
        assert option_payload["dispatch"]["payload"] == {
            "scope": "public",
            "intent": "talk",
            "target_kind": "npc",
            "target_id": "merchant_tom",
            "message": "追问关于那封信的事",
        }

    def test_checked_talk_option_maps_to_interact_dispatch_with_check_fields(self) -> None:
        from app.game_core.orchestration.npc_interaction import NpcInteractionResult

        result = NpcInteractionResult(
            completed=True,
            npc_id="merchant_tom",
            dialogue_options=[
                {
                    "text": "[说服 DC14] 请他让路",
                    "intent": "talk",
                    "check": {"skill": "persuasion", "dc": 14},
                },
            ],
        )

        events = _interaction_result_to_sse(result)

        option_payload = [e for e in events if e.event_type == "dialogue_options"][0].payload["options"][0]
        assert option_payload["dispatch"]["kind"] == "interact"
        assert option_payload["dispatch"]["payload"] == {
            "scope": "public",
            "intent": "talk",
            "target_kind": "npc",
            "target_id": "merchant_tom",
            "message": "[说服 DC14] 请他让路",
            "check_skill": "persuasion",
            "check_dc": 14,
        }

    def test_empty_result_produces_only_options(self) -> None:
        from app.game_core.orchestration.npc_interaction import NpcInteractionResult

        result = NpcInteractionResult(
            completed=True,
            npc_id="npc1",
            dialogue_options=[{"text": "继续交谈", "intent": "talk"}],
        )

        events = _interaction_result_to_sse(result)

        assert len(events) == 1
        assert events[0].event_type == "dialogue_options"

    def test_opening_talk_option_includes_starter_message(self) -> None:
        session = _session_for_post_action_round()

        options = _serialize_opening_options(
            session,
            [{"text": "和 Merchant Tom 说话", "action": "talk_first_npc"}],
        )

        assert options[0]["dispatch"]["kind"] == "interact"
        assert options[0]["dispatch"]["payload"] == {
            "scope": "public",
            "intent": "talk",
            "target_kind": "npc",
            "target_id": "merchant_tom",
            "message": "你好。",
        }

    def test_opening_check_option_with_npc_id_becomes_dialogue_context(self) -> None:
        session = _session_for_post_action_round()

        options = _serialize_opening_options(
            session,
            [{
                "text": "[说服 DC14] 请他让路",
                "check": {"skill": "persuasion", "dc": 14},
                "npc_id": "merchant_tom",
            }],
        )

        assert options[0]["dispatch"]["kind"] == "interact"
        assert options[0]["dispatch"]["payload"] == {
            "scope": "public",
            "intent": "talk",
            "target_kind": "npc",
            "target_id": "merchant_tom",
            "message": "你好。",
            "check_skill": "persuasion",
            "check_dc": 14,
        }

    def test_opening_check_option_infers_single_visible_npc_for_dialogue_context(self) -> None:
        session = _session_for_post_action_round()

        options = _serialize_opening_options(
            session,
            [{
                "text": "[说服 DC14] 请他让路",
                "check": {"skill": "persuasion", "dc": 14},
            }],
        )

        assert options[0]["dispatch"]["kind"] == "interact"
        assert options[0]["dispatch"]["payload"] == {
            "scope": "public",
            "intent": "talk",
            "target_kind": "npc",
            "target_id": "merchant_tom",
            "message": "你好。",
            "check_skill": "persuasion",
            "check_dc": 14,
        }

    def test_gm_narration_included_after_npc(self) -> None:
        from app.game_core.narrative.models import AgentResult, ToolResult
        from app.game_core.orchestration.npc_interaction import NpcInteractionResult

        result = NpcInteractionResult(
            completed=True,
            npc_id="npc1",
            npc_result=AgentResult(
                tool_results=[
                    ToolResult(
                        ok=True,
                        message="Hello!",
                        metadata={"event_type": "speech"},
                    ),
                ],
            ),
            gm_result=AgentResult(
                tool_results=[
                    ToolResult(
                        ok=True,
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
            completed=True,
            npc_id="npc1",
            gm_result=AgentResult(
                tool_results=[
                    ToolResult(
                        ok=True,
                        message="A shadow falls.",
                        metadata={"event_type": "gm_comment"},
                    ),
                ],
            ),
            teammate_results={
                "paladin_aria": AgentResult(
                    tool_results=[
                        ToolResult(
                            ok=True,
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

    def test_run_npc_interaction_text_only_is_gracefully_handled(self) -> None:
        """P29-A1: Text-only NPC response in interaction produces npc_response,
        not npc_response_error."""
        service, _ = _build_service([
            _stop_response("I should have used speak."),
        ])
        session = _session_with_npc()

        events = asyncio.run(service.run_npc_interaction(
            session=session,
            npc_id="merchant_tom",
            player_message="Hello",
        ))

        npc_events = [e for e in events if e.event_type == "npc_response"]
        assert len(npc_events) >= 1
        assert npc_events[0].payload["npc_id"] == "merchant_tom"
        assert npc_events[0].payload["content"] == "I should have used speak."

    def test_run_npc_interaction_adds_fallback_gm_comment(self) -> None:
        service, _ = _build_service([
            _npc_speak_response("Hello traveler!"),
            _gm_pass_turn_response(),
        ])
        session = _session_with_npc()

        events = asyncio.run(service.run_npc_interaction(
            session=session,
            npc_id="merchant_tom",
            player_message="Hello!",
            intent="talk",
        ))

        event_types = [event.event_type for event in events]
        assert "gm_comment" in event_types
        gm_idx = event_types.index("gm_comment")
        options_idx = event_types.index("dialogue_options")
        assert gm_idx < options_idx


class TestRunPublicUtterance:
    def test_run_public_utterance_adds_fallback_gm_comment(self) -> None:
        service, _ = _build_service([
            _gm_pass_turn_response(),
        ])
        session = _session_with_npc()

        events = asyncio.run(service.run_public_utterance(
            session=session,
            player_message="有人听见吗？",
            intent="talk",
        ))

        gm_events = [event for event in events if event.event_type == "gm_comment"]
        assert len(gm_events) == 1
        assert gm_events[0].payload["content"] == "你的话落进空气里，而空气通常比措辞更诚实。"


class TestRunFreeChat:
    def test_run_free_chat_silent_round_uses_gm_comment_fallback(self) -> None:
        import app.game_core.orchestration.npc_interaction as _npc_mod

        service, _ = _build_service([
            _gm_pass_turn_response(),
        ])
        session = _session_for_post_action_round()

        with patch.object(_npc_mod.random, "random", return_value=1.0):
            events = asyncio.run(service.run_free_chat(
                session=session,
                player_message="先别急着表态。",
            ))

        event_types = [event.event_type for event in events]
        assert "teammate_response" not in event_types
        assert "gm_comment" in event_types
        assert [event.payload["content"] for event in events if event.event_type == "gm_comment"] == [
            "你的战术讨论收获颇丰：一阵足以切开的沉默。",
        ]

    def test_run_free_chat_silent_round_prefers_model_gm_comment(self) -> None:
        import app.game_core.orchestration.npc_interaction as _npc_mod

        service, _ = _build_service([
            {
                "tool_calls": [{"name": "comment", "args": {"text": "他们都在等别人先开口。"}}],
                "finish_reason": "tool_calls",
            },
        ])
        session = _session_for_post_action_round()

        with patch.object(_npc_mod.random, "random", return_value=1.0):
            events = asyncio.run(service.run_free_chat(
                session=session,
                player_message="先别急着表态。",
            ))

        gm_events = [event for event in events if event.event_type == "gm_comment"]
        assert len(gm_events) == 1
        assert gm_events[0].payload["content"] == "他们都在等别人先开口。"

    def test_run_free_chat_with_visible_reply_also_adds_gm_comment(self) -> None:
        import app.game_core.orchestration.npc_interaction as _npc_mod

        service, _ = _build_service([
            {
                "tool_calls": [{"name": "speak", "args": {"text": "我听着呢。"}}],
                "finish_reason": "tool_calls",
            },
            {
                "tool_calls": [{"name": "comment", "args": {"text": "至少这群人终于像个队伍了。"}}],
                "finish_reason": "tool_calls",
            },
        ])
        session = _session_for_post_action_round()

        with patch.object(_npc_mod.random, "random", side_effect=[0.0, 1.0]):
            events = asyncio.run(service.run_free_chat(
                session=session,
                player_message="都说两句。",
            ))

        event_types = [event.event_type for event in events]
        assert "teammate_response" in event_types
        assert "gm_comment" in event_types
        assert [event.payload["content"] for event in events if event.event_type == "gm_comment"] == [
            "至少这群人终于像个队伍了。",
        ]
