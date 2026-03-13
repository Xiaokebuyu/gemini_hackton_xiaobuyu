"""Tests for AgenticExecutor multi-turn agentic loop."""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.adapters.llm import LlmResponse
from app.game_core.content import WorldInstance
from app.game_core.narrative import (
    AgentResult,
    AgenticExecutor,
    AgentTool,
    RoleToolRegistry,
    ToolResult,
)
from app.game_core.narrative.context import AgentContext
from app.game_core.state import StateContainer


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


class RecordingLlmProvider:
    """Pre-configured LLM that returns scripted responses in order."""

    def __init__(self, responses: list[LlmResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def generate(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        tool_declarations: list[dict[str, Any]],
    ) -> LlmResponse:
        self.calls.append({
            "system_prompt": system_prompt,
            "history": history,
            "tool_declarations": tool_declarations,
        })
        return self._responses.pop(0) if self._responses else LlmResponse()


class EchoTool(AgentTool):
    """Minimal tool that echoes its params — for testing."""

    name = "echo"
    description = "Echo input"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}
    allowed_roles = ["gm"]

    async def execute(self, params: dict[str, Any], context: AgentContext) -> ToolResult:
        text = params.get("text", "")
        return ToolResult(
            ok=True,
            message=text,
            metadata={"event_type": "echo", "echoed": text},
        )


class CounterTool(AgentTool):
    """Tool that increments a counter in context metadata — for testing."""

    name = "increment"
    description = "Increment counter"
    parameters = {"type": "object", "properties": {}}
    allowed_roles = ["gm"]

    async def execute(self, params: dict[str, Any], context: AgentContext) -> ToolResult:
        context.metadata.setdefault("counter", 0)
        context.metadata["counter"] += 1
        return ToolResult(
            ok=True,
            message=f"counter={context.metadata['counter']}",
            metadata={"counter": context.metadata["counter"]},
        )


class ScriptedNarrativeTool(AgentTool):
    """Test helper for NPC/teammate visible output and side-effect tools."""

    def __init__(
        self,
        name: str,
        *,
        roles: list[str],
        event_type: str,
        side_effect_key: str | None = None,
    ) -> None:
        self._name = name
        self._roles = list(roles)
        self._event_type = event_type
        self._side_effect_key = side_effect_key

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"tool:{self._name}"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"text": {"type": "string"}},
        }

    @property
    def allowed_roles(self) -> list[str]:
        return list(self._roles)

    async def execute(self, params: dict[str, Any], context: AgentContext) -> ToolResult:
        text = str(params.get("text", ""))
        if self._side_effect_key is not None:
            context.metadata.setdefault(self._side_effect_key, [])
            context.metadata[self._side_effect_key].append(text)
        return ToolResult(
            ok=True,
            message=text,
            metadata={"event_type": self._event_type},
        )


def _registry() -> RoleToolRegistry:
    reg = RoleToolRegistry()
    reg.register(EchoTool())
    reg.register(CounterTool())
    reg.register(ScriptedNarrativeTool(
        "speak",
        roles=["npc", "teammate"],
        event_type="speech",
    ))
    reg.register(ScriptedNarrativeTool(
        "emote",
        roles=["npc", "teammate"],
        event_type="emote",
    ))
    reg.register(ScriptedNarrativeTool(
        "refuse",
        roles=["npc"],
        event_type="refuse",
    ))
    reg.register(ScriptedNarrativeTool(
        "remember",
        roles=["npc"],
        event_type="memory_written",
        side_effect_key="remembered_notes",
    ))
    return reg


def _ctx(**overrides: Any) -> AgentContext:
    role = str(overrides.pop("role", "gm"))
    return AgentContext(
        role=role,
        world=WorldInstance("test"),
        state=StateContainer(),
        **overrides,
    )


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


def test_run_agentic_no_llm_returns_no_llm() -> None:
    """Without an LLM, run_agentic returns immediately with no_llm status."""
    executor = AgenticExecutor(tool_registry=_registry(), llm=None)
    result = asyncio.run(executor.run_agentic("gm", _ctx()))

    assert isinstance(result, AgentResult)
    assert result.metadata["status"] == "no_llm"
    assert result.turns_used == 0
    assert result.tool_results == []


def test_run_agentic_no_tool_calls_returns_text() -> None:
    """LLM returns text-only on first turn — loop exits with text."""
    llm = RecordingLlmProvider([
        LlmResponse(text="All is calm.", finish_reason="stop"),
    ])
    executor = AgenticExecutor(tool_registry=_registry(), llm=llm)
    result = asyncio.run(executor.run_agentic("gm", _ctx()))

    assert result.text == "All is calm."
    assert result.turns_used == 1
    assert result.tool_results == []
    assert result.metadata["status"] == "completed"
    assert result.metadata["finish_reason"] == "stop"
    assert len(llm.calls) == 1


def test_run_agentic_single_turn_tool_call() -> None:
    """LLM returns a tool call, then text — 2-turn loop."""
    llm = RecordingLlmProvider([
        LlmResponse(
            tool_calls=[{"name": "echo", "args": {"text": "hello"}}],
            finish_reason="tool_calls",
        ),
        LlmResponse(text="Done echoing.", finish_reason="stop"),
    ])
    executor = AgenticExecutor(tool_registry=_registry(), llm=llm)
    result = asyncio.run(executor.run_agentic("gm", _ctx()))

    assert result.text == "Done echoing."
    assert result.turns_used == 2
    assert len(result.tool_results) == 1
    assert result.tool_results[0].ok is True
    assert result.tool_results[0].metadata["echoed"] == "hello"
    assert result.metadata["status"] == "completed"


def test_run_agentic_multi_turn() -> None:
    """LLM makes tool calls across two turns before returning text."""
    llm = RecordingLlmProvider([
        LlmResponse(
            tool_calls=[{"name": "increment", "args": {}}],
            finish_reason="tool_calls",
        ),
        LlmResponse(
            tool_calls=[{"name": "increment", "args": {}}],
            finish_reason="tool_calls",
        ),
        LlmResponse(text="Counter is 2.", finish_reason="stop"),
    ])
    executor = AgenticExecutor(tool_registry=_registry(), llm=llm)
    ctx = _ctx()
    result = asyncio.run(executor.run_agentic("gm", ctx))

    assert result.turns_used == 3
    assert len(result.tool_results) == 2
    assert ctx.metadata["counter"] == 2
    assert result.text == "Counter is 2."
    assert result.metadata["status"] == "completed"


def test_run_agentic_max_turns_reached() -> None:
    """Loop stops when max_turns is reached — status reflects this."""
    llm = RecordingLlmProvider([
        LlmResponse(
            tool_calls=[{"name": "increment", "args": {}}],
            finish_reason="tool_calls",
        ),
        LlmResponse(
            tool_calls=[{"name": "increment", "args": {}}],
            finish_reason="tool_calls",
        ),
        LlmResponse(
            tool_calls=[{"name": "increment", "args": {}}],
            finish_reason="tool_calls",
        ),
    ])
    executor = AgenticExecutor(tool_registry=_registry(), llm=llm)
    result = asyncio.run(executor.run_agentic("gm", _ctx(), max_turns=2))

    assert result.metadata["status"] == "max_turns_reached"
    assert result.turns_used == 2
    assert len(result.tool_results) == 2
    assert result.text == ""


def test_run_agentic_parallel_tool_calls() -> None:
    """LLM returns multiple tool calls in a single turn — all executed."""
    llm = RecordingLlmProvider([
        LlmResponse(
            tool_calls=[
                {"name": "echo", "args": {"text": "first"}},
                {"name": "echo", "args": {"text": "second"}},
                {"name": "increment", "args": {}},
            ],
            finish_reason="tool_calls",
        ),
        LlmResponse(text="All done.", finish_reason="stop"),
    ])
    executor = AgenticExecutor(tool_registry=_registry(), llm=llm)
    result = asyncio.run(executor.run_agentic("gm", _ctx()))

    assert result.turns_used == 2
    assert len(result.tool_results) == 3
    assert result.tool_results[0].metadata["echoed"] == "first"
    assert result.tool_results[1].metadata["echoed"] == "second"
    assert result.tool_results[2].metadata["counter"] == 1


def test_npc_visible_output_stops_multi_turn_loop() -> None:
    """NPC/teammate turns stop after the first visible reply batch."""
    llm = RecordingLlmProvider([
        LlmResponse(
            tool_calls=[{"name": "speak", "args": {"text": "Welcome."}}],
            finish_reason="tool_calls",
        ),
        LlmResponse(
            tool_calls=[{"name": "speak", "args": {"text": "This should not happen."}}],
            finish_reason="tool_calls",
        ),
    ])
    executor = AgenticExecutor(tool_registry=_registry(), llm=llm)

    result = asyncio.run(executor.run_agentic("npc", _ctx(role="npc")))

    assert result.turns_used == 1
    assert len(llm.calls) == 1
    assert [tr.message for tr in result.tool_results if tr.metadata.get("event_type") == "speech"] == [
        "Welcome.",
    ]
    assert result.metadata["status"] == "completed"
    assert result.metadata["finish_reason"] == "visible_output_emitted"


def test_npc_side_effects_and_single_visible_reply_share_one_turn() -> None:
    """NPC may batch side effects with one visible reply, then stop immediately."""
    llm = RecordingLlmProvider([
        LlmResponse(
            tool_calls=[
                {"name": "remember", "args": {"text": "player asked about the ledger"}},
                {"name": "speak", "args": {"text": "The ledger stays locked."}},
                {"name": "emote", "args": {"text": "*she taps the counter*"}},
                {"name": "remember", "args": {"text": "player seemed suspicious"}},
            ],
            finish_reason="tool_calls",
        ),
    ])
    executor = AgenticExecutor(tool_registry=_registry(), llm=llm)
    ctx = _ctx(role="npc")

    result = asyncio.run(executor.run_agentic("npc", ctx))

    assert ctx.metadata["remembered_notes"] == [
        "player asked about the ledger",
        "player seemed suspicious",
    ]
    assert [
        tr.message
        for tr in result.tool_results
        if tr.metadata.get("event_type") == "speech"
    ] == [
        "The ledger stays locked.",
    ]
    assert [tr.message for tr in result.tool_results if tr.metadata.get("event_type") == "emote"] == [
        "*she taps the counter*",
    ]
    assert result.metadata["finish_reason"] == "visible_output_emitted"


def test_npc_text_only_response_is_gracefully_wrapped() -> None:
    """NPC text without a visible tool call is gracefully wrapped as a synthetic
    speech ToolResult (P29-A1) instead of a protocol_error."""
    llm = RecordingLlmProvider([
        LlmResponse(text="I should have used speak.", finish_reason="stop"),
    ])
    executor = AgenticExecutor(tool_registry=_registry(), llm=llm)

    result = asyncio.run(executor.run_agentic("npc", _ctx(role="npc")))

    assert result.text == ""
    assert result.metadata["status"] == "completed"
    assert result.metadata["finish_reason"] == "text_fallback"
    assert len(result.tool_results) == 1
    tr = result.tool_results[0]
    assert tr.ok is True
    assert tr.message == "I should have used speak."
    assert tr.metadata["event_type"] == "speech"
    assert tr.metadata.get("synthetic") is True


def test_npc_passive_metadata_allows_silent_pass_turn() -> None:
    llm = RecordingLlmProvider([
        LlmResponse(text="", finish_reason="stop"),
    ])
    executor = AgenticExecutor(tool_registry=_registry(), llm=llm)
    ctx = _ctx(role="npc", metadata={"is_passive": True})

    result = asyncio.run(executor.run_agentic("npc", ctx))

    assert result.text == ""
    assert result.tool_results == []
    assert result.metadata["status"] == "completed"
    assert result.metadata["finish_reason"] == "pass_turn"


def test_npc_multiple_dialogue_tools_is_protocol_error() -> None:
    """NPC cannot emit two dialogue tools in the same turn."""
    llm = RecordingLlmProvider([
        LlmResponse(
            tool_calls=[
                {"name": "speak", "args": {"text": "One."}},
                {"name": "refuse", "args": {"text": "Two."}},
            ],
            finish_reason="tool_calls",
        ),
    ])
    executor = AgenticExecutor(tool_registry=_registry(), llm=llm)

    result = asyncio.run(executor.run_agentic("npc", _ctx(role="npc")))

    assert result.text == ""
    assert result.tool_results == []
    assert result.metadata["status"] == "protocol_error"
    assert result.metadata["reason"] == "multiple_dialogue_tools"


def test_teammate_empty_response_is_pass_turn() -> None:
    """Teammates may stay silent by returning no tool calls and no text."""
    llm = RecordingLlmProvider([
        LlmResponse(text="", finish_reason="stop"),
    ])
    executor = AgenticExecutor(tool_registry=_registry(), llm=llm)

    result = asyncio.run(executor.run_agentic("teammate", _ctx(role="teammate")))

    assert result.text == ""
    assert result.tool_results == []
    assert result.metadata["status"] == "completed"
    assert result.metadata["finish_reason"] == "pass_turn"


def test_build_declarations_from_tools() -> None:
    """_build_declarations converts AgentTool list to function declarations."""
    tools = [EchoTool(), CounterTool()]
    declarations = AgenticExecutor._build_declarations(tools)

    assert len(declarations) == 2
    assert declarations[0]["name"] == "echo"
    assert declarations[0]["description"] == "Echo input"
    assert declarations[0]["parameters"]["type"] == "object"
    assert declarations[1]["name"] == "increment"


def test_tool_response_fed_back_to_history() -> None:
    """Verify that tool results are correctly fed back into LLM history."""
    llm = RecordingLlmProvider([
        LlmResponse(
            tool_calls=[{"name": "echo", "args": {"text": "ping"}}],
            finish_reason="tool_calls",
        ),
        LlmResponse(text="pong", finish_reason="stop"),
    ])
    executor = AgenticExecutor(tool_registry=_registry(), llm=llm)
    asyncio.run(executor.run_agentic("gm", _ctx(), system_prompt="Be helpful."))

    # Second LLM call should have the tool result in history
    assert len(llm.calls) == 2
    second_call = llm.calls[1]

    assert second_call["system_prompt"] == "Be helpful."

    # History should have: initial user msg + model tool_call + user tool_response
    history = second_call["history"]
    assert len(history) == 3

    # First entry: initial user message
    assert history[0]["role"] == "user"

    # Second entry: model's function call
    assert history[1]["role"] == "model"
    fc_part = history[1]["parts"][0]
    assert "function_call" in fc_part
    assert fc_part["function_call"]["name"] == "echo"

    # Third entry: tool response
    assert history[2]["role"] == "user"
    fr_part = history[2]["parts"][0]
    assert "function_response" in fr_part
    assert fr_part["function_response"]["name"] == "echo"
    assert fr_part["function_response"]["response"]["ok"] is True
    assert fr_part["function_response"]["response"]["echoed"] == "ping"


# ------------------------------------------------------------------
# TestSerializeContextLayers — unit tests for _serialize_context_layers
# ------------------------------------------------------------------


class TestSerializeContextLayers:
    """Unit tests for AgenticExecutor._serialize_context_layers() (N-7)."""

    def _full_layers(self) -> dict[str, Any]:
        return {
            "l0_world_constants": {"world_id": "test_world", "lore": [], "factions": []},
            "l1_chapter_state": None,
            "l2_area_environment": {
                "area_id": "town",
                "template": {"name": "Town Square", "description": "A busy square."},
                "state": {"danger_level": 2},
            },
            "l3_location_details": {
                "location_id": "market",
                "template": {"name": "Market Stall", "description": "A merchant's stall."},
                "is_dynamic": False,
                "discovered_items": ["sword", "potion"],
            },
            "l4_dynamic_state": {},
            "l5_scene_bus": {
                "entries": [
                    {"source": "player", "content": "Hello there!", "visibility": "public"},
                ],
            },
            "l6_memory_recall": None,
            "l7_engine_result": {"narrative_hints": ["Player is in danger."]},
        }

    def test_npc_contains_l2_and_l3(self) -> None:
        """NPC serialization includes area and location names."""
        text = AgenticExecutor._serialize_context_layers("npc", self._full_layers())
        assert "Town Square" in text
        assert "Market Stall" in text

    def test_npc_contains_l5_scene(self) -> None:
        """NPC serialization includes L5 scene entries."""
        text = AgenticExecutor._serialize_context_layers("npc", self._full_layers())
        assert "Hello there!" in text

    def test_npc_excludes_l7_hints(self) -> None:
        """NPC serialization must NOT include L7 narrative hints (GM-only)."""
        text = AgenticExecutor._serialize_context_layers("npc", self._full_layers())
        assert "Player is in danger." not in text
        assert "Narrative Hints" not in text

    def test_gm_includes_l7_hints(self) -> None:
        """GM serialization includes L7 narrative hints."""
        text = AgenticExecutor._serialize_context_layers("gm", self._full_layers())
        assert "Player is in danger." in text
        assert "Narrative Hints" in text

    def test_empty_layers_returns_empty_string(self) -> None:
        """All-None layers produces empty string (no blocks to render)."""
        layers: dict[str, Any] = {k: None for k in (
            "l0_world_constants", "l1_chapter_state", "l2_area_environment",
            "l3_location_details", "l4_dynamic_state", "l5_scene_bus",
            "l6_memory_recall", "l7_engine_result",
        )}
        text = AgenticExecutor._serialize_context_layers("npc", layers)
        assert text == ""


# ------------------------------------------------------------------
# TestContextLayersInjection — integration tests for N-7 injection
# ------------------------------------------------------------------


class TestContextLayersInjection:
    """Tests that context_layers are correctly injected into initial history (N-7)."""

    def _make_executor(self) -> tuple[AgenticExecutor, RecordingLlmProvider]:
        llm = RecordingLlmProvider([LlmResponse(text="ok", finish_reason="stop")])
        executor = AgenticExecutor(tool_registry=_registry(), llm=llm)
        return executor, llm

    def _layers_with_l2_l5(self) -> dict[str, Any]:
        return {
            "l0_world_constants": {"world_id": "w", "lore": [], "factions": []},
            "l1_chapter_state": None,
            "l2_area_environment": {
                "area_id": "town",
                "template": {"name": "Town Square", "description": "A busy square."},
                "state": None,
            },
            "l3_location_details": {
                "location_id": "market",
                "template": {"name": "Market Stall", "description": "Merchant's stall."},
                "is_dynamic": False,
                "discovered_items": [],
            },
            "l4_dynamic_state": {},
            "l5_scene_bus": {
                "entries": [
                    {"source": "player", "content": "Hello NPC!", "visibility": "public"},
                ],
            },
            "l6_memory_recall": None,
            "l7_engine_result": None,
        }

    def test_context_layers_injected_into_initial_history(self) -> None:
        """context_layers content appears in history parts when conversation_history=None."""
        executor, llm = self._make_executor()
        asyncio.run(executor.run_agentic(
            "npc", _ctx(), user_message="Hi", context_layers=self._layers_with_l2_l5(),
        ))

        history = llm.calls[0]["history"]
        assert len(history) == 1
        parts = history[0]["parts"]
        all_text = " ".join(p.get("text", "") for p in parts)
        assert "Town Square" in all_text
        assert "Market Stall" in all_text

    def test_context_layers_skipped_when_conversation_history_provided(self) -> None:
        """When conversation_history is given, context_layers injection is skipped."""
        executor, llm = self._make_executor()
        prior = [{"role": "user", "parts": [{"text": "previous"}]}]
        asyncio.run(executor.run_agentic(
            "npc", _ctx(), user_message="Hi",
            conversation_history=prior,
            context_layers=self._layers_with_l2_l5(),
        ))

        history = llm.calls[0]["history"]
        all_text = " ".join(
            p.get("text", "") for msg in history for p in msg.get("parts", [])
        )
        assert "Town Square" not in all_text   # layers not injected
        assert "Hi" in all_text                # user_message still appended

    def test_l5_suppresses_raw_scene_when_layers_provided(self) -> None:
        """When context_layers has L5 entries, context.scene_entries are NOT re-injected."""
        executor, llm = self._make_executor()
        ctx = _ctx()
        ctx.scene_entries.append({
            "source": "player", "content": "Old scene entry", "visibility": "public",
        })

        asyncio.run(executor.run_agentic(
            "npc", ctx, user_message="Hi", context_layers=self._layers_with_l2_l5(),
        ))

        history = llm.calls[0]["history"]
        all_text = " ".join(
            p.get("text", "") for msg in history for p in msg.get("parts", [])
        )
        assert "Hello NPC!" in all_text       # L5 from layers present
        assert "Old scene entry" not in all_text  # raw scene_entries suppressed

    def test_context_layers_none_backward_compatible(self) -> None:
        """context_layers=None (default) still injects scene_entries as before."""
        executor, llm = self._make_executor()
        ctx = _ctx()
        ctx.scene_entries.append({
            "source": "player", "content": "A scene.", "visibility": "public",
        })

        asyncio.run(executor.run_agentic("npc", ctx, user_message="Hello"))

        history = llm.calls[0]["history"]
        all_text = " ".join(
            p.get("text", "") for msg in history for p in msg.get("parts", [])
        )
        assert "A scene." in all_text    # scene_entries injected via old path

    def test_gm_role_receives_l7_hints_in_layers_text(self) -> None:
        """GM role gets L7 narrative hints in serialized context_layers."""
        executor, llm = self._make_executor()
        layers = self._layers_with_l2_l5()
        layers["l7_engine_result"] = {"narrative_hints": ["Enemy nearby!"]}

        asyncio.run(executor.run_agentic(
            "gm", _ctx(), user_message="React.", context_layers=layers,
        ))

        history = llm.calls[0]["history"]
        all_text = " ".join(
            p.get("text", "") for msg in history for p in msg.get("parts", [])
        )
        assert "Enemy nearby!" in all_text
        assert "Narrative Hints" in all_text
