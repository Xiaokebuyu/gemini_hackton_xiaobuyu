"""Tests for P29-A1 (Private Chat graceful degradation) and
P29-A2 (NPC quest tool permission fix)."""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.adapters.llm import LlmResponse
from app.game_core.content import WorldInstance
from app.game_core.narrative import (
    AgentResult,
    AgenticExecutor,
    RoleToolRegistry,
    ToolResult,
)
from app.game_core.narrative.character_tools import AcceptQuestTool, OfferQuestTool
from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.tools import AgentTool
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateContainer


# ------------------------------------------------------------------
# Shared helpers
# ------------------------------------------------------------------


class RecordingLlmProvider:
    """Returns scripted LlmResponse objects in order."""

    def __init__(self, responses: list[LlmResponse]) -> None:
        self._responses = list(responses)

    async def generate(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        tool_declarations: list[dict[str, Any]],
    ) -> LlmResponse:
        return self._responses.pop(0) if self._responses else LlmResponse()


class SpeakToolStub(AgentTool):
    """Minimal speak tool for agentic loop tests."""

    name = "speak"
    description = "Say something"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}
    allowed_roles = ["npc", "teammate"]

    async def execute(self, params: dict[str, Any], context: AgentContext) -> ToolResult:
        text = str(params.get("text", ""))
        return ToolResult(
            ok=True,
            message=text,
            metadata={"event_type": "speech"},
        )


def _recording_executor() -> tuple[list[Command], Any]:
    log: list[Command] = []

    def execute(cmd: Command) -> ExecuteResult:
        log.append(cmd)
        return ExecuteResult(executed=True)

    return log, execute


def _ctx_with_role_data(
    *,
    character_id: str = "npc_clerk",
    role_data: dict[str, Any] | None = None,
    execute_command: Any = None,
) -> AgentContext:
    metadata: dict[str, Any] = {"character_id": character_id}
    if role_data is not None:
        metadata["role_data"] = role_data
    return AgentContext(
        role="npc",
        world=WorldInstance("test"),
        state=StateContainer(),
        metadata=metadata,
        execute_command=execute_command,
    )


def _npc_registry() -> RoleToolRegistry:
    reg = RoleToolRegistry()
    reg.register(SpeakToolStub())
    return reg


# ==================================================================
# A1: NPC graceful degradation (text without tool → synthetic speech)
# ==================================================================


def test_a1_npc_plain_text_wrapped_as_synthetic_speech() -> None:
    """When NPC returns plain text (no tool call), it should be wrapped
    as a synthetic speech ToolResult instead of a protocol_error."""

    llm = RecordingLlmProvider([
        LlmResponse(text="Hello adventurer, welcome to the guild!", finish_reason="stop"),
    ])
    executor = AgenticExecutor(tool_registry=_npc_registry(), llm=llm)
    ctx = AgentContext(
        role="npc",
        world=WorldInstance("test"),
        state=StateContainer(),
        metadata={"character_id": "npc_clerk"},
    )

    result = asyncio.run(executor.run_agentic("npc", ctx, user_message="Hi"))

    assert result.metadata["status"] == "completed"
    assert result.metadata["finish_reason"] == "text_fallback"
    assert len(result.tool_results) == 1
    tr = result.tool_results[0]
    assert tr.ok is True
    assert tr.message == "Hello adventurer, welcome to the guild!"
    assert tr.metadata["event_type"] == "speech"
    assert tr.metadata["synthetic"] is True


def test_a1_npc_plain_text_extractable_by_visible_reply() -> None:
    """Synthetic speech ToolResult should be picked up by
    _extract_visible_reply_text (event_type == 'speech')."""
    from app.game_core.orchestration.npc_interaction import _extract_visible_reply_text

    llm = RecordingLlmProvider([
        LlmResponse(text="The quest board is over there.", finish_reason="stop"),
    ])
    executor = AgenticExecutor(tool_registry=_npc_registry(), llm=llm)
    ctx = AgentContext(
        role="npc",
        world=WorldInstance("test"),
        state=StateContainer(),
        metadata={"character_id": "npc_clerk"},
    )

    result = asyncio.run(executor.run_agentic("npc", ctx, user_message="Where?"))

    speech = _extract_visible_reply_text(result)
    assert speech == "The quest board is over there."


def test_a1_npc_empty_text_still_returns_protocol_error() -> None:
    """An empty NPC response (no text, no tool calls) should still be
    a protocol_error with reason 'empty_response'."""

    llm = RecordingLlmProvider([
        LlmResponse(text="", finish_reason="stop"),
    ])
    executor = AgenticExecutor(tool_registry=_npc_registry(), llm=llm)
    ctx = AgentContext(
        role="npc",
        world=WorldInstance("test"),
        state=StateContainer(),
        metadata={"character_id": "npc_clerk"},
    )

    result = asyncio.run(executor.run_agentic("npc", ctx, user_message="Hi"))

    assert result.metadata["status"] == "protocol_error"
    assert result.metadata["reason"] == "empty_response"


def test_a1_teammate_plain_text_still_protocol_error() -> None:
    """Teammate role should NOT be affected by the NPC degradation —
    returning text without a tool is still a protocol_error for teammates."""

    llm = RecordingLlmProvider([
        LlmResponse(text="I think we should attack.", finish_reason="stop"),
    ])
    executor = AgenticExecutor(tool_registry=_npc_registry(), llm=llm)
    ctx = AgentContext(
        role="teammate",
        world=WorldInstance("test"),
        state=StateContainer(),
        metadata={"character_id": "companion_alex"},
    )

    result = asyncio.run(executor.run_agentic("teammate", ctx, user_message="What do?"))

    assert result.metadata["status"] == "protocol_error"
    assert result.metadata["reason"] == "text_without_tool"


# ==================================================================
# A2: NPC quest tool permission fix (OfferQuestTool + AcceptQuestTool)
# ==================================================================


class TestOfferQuestTool:
    """P29-A2: OfferQuestTool must reject quests not on the bulletin board."""

    def test_offer_quest_succeeds_when_in_bulletins(self) -> None:
        log, executor = _recording_executor()
        ctx = _ctx_with_role_data(
            execute_command=executor,
            role_data={
                "role": "receptionist",
                "bulletins": [{"quest_id": "hunt_goblins", "title": "Hunt goblins"}],
                "active_quests": [],
            },
        )

        result = asyncio.run(OfferQuestTool().execute({"quest_id": "hunt_goblins"}, ctx))

        assert result.ok is True
        assert result.metadata["quest_id"] == "hunt_goblins"
        assert len(log) == 1
        assert log[0].type == "advance_quest"

    def test_offer_quest_fails_when_not_in_bulletins(self) -> None:
        """No state.quests fallback — bulletin board is the only truth source."""
        log, executor = _recording_executor()
        ctx = _ctx_with_role_data(
            execute_command=executor,
            role_data={
                "role": "receptionist",
                "bulletins": [{"quest_id": "hunt_goblins", "title": "Hunt goblins"}],
                "active_quests": [],
            },
        )

        result = asyncio.run(OfferQuestTool().execute({"quest_id": "deliver_package"}, ctx))

        assert result.ok is False
        assert result.metadata["status"] == "quest_not_available"
        assert log == []

    def test_offer_quest_no_role_data_passes_through(self) -> None:
        """Without role_data, validation is skipped — command runs directly."""
        log, executor = _recording_executor()
        ctx = _ctx_with_role_data(execute_command=executor)  # no role_data

        result = asyncio.run(OfferQuestTool().execute({"quest_id": "any_quest"}, ctx))

        assert result.ok is True
        assert len(log) == 1


class TestAcceptQuestTool:
    """P29-A2: AcceptQuestTool uses role_data exclusively (no state.quests access)."""

    def test_accept_quest_succeeds_when_quest_on_board(self) -> None:
        log, executor = _recording_executor()
        ctx = _ctx_with_role_data(
            execute_command=executor,
            role_data={
                "role": "receptionist",
                "bulletins": [{"quest_id": "clear_ruins", "title": "Clear ruins"}],
                "active_quests": [],
            },
        )

        result = asyncio.run(AcceptQuestTool().execute({"quest_id": "clear_ruins"}, ctx))

        assert result.ok is True
        assert result.metadata["quest_id"] == "clear_ruins"
        assert len(log) == 1
        assert log[0].type == "receptionist_accept_quest"
        assert log[0].params["quest_id"] == "clear_ruins"

    def test_accept_quest_fails_when_not_in_bulletins(self) -> None:
        log, executor = _recording_executor()
        ctx = _ctx_with_role_data(
            execute_command=executor,
            role_data={
                "role": "receptionist",
                "bulletins": [{"quest_id": "clear_ruins", "title": "Clear ruins"}],
                "active_quests": [],
            },
        )

        result = asyncio.run(AcceptQuestTool().execute({"quest_id": "unknown_quest"}, ctx))

        assert result.ok is False
        assert result.metadata["status"] == "quest_not_available"
        assert log == []

    def test_accept_quest_fails_when_already_active(self) -> None:
        log, executor = _recording_executor()
        ctx = _ctx_with_role_data(
            execute_command=executor,
            role_data={
                "role": "receptionist",
                "bulletins": [{"quest_id": "clear_ruins", "title": "Clear ruins"}],
                "active_quests": [{"quest_id": "clear_ruins", "title": "Clear ruins"}],
            },
        )

        result = asyncio.run(AcceptQuestTool().execute({"quest_id": "clear_ruins"}, ctx))

        assert result.ok is False
        assert result.metadata["status"] == "quest_already_active"
        assert log == []

    def test_accept_quest_fails_without_role_data(self) -> None:
        """AcceptQuestTool requires role_data — no graceful fallback."""
        log, executor = _recording_executor()
        ctx = _ctx_with_role_data(execute_command=executor)  # no role_data

        result = asyncio.run(AcceptQuestTool().execute({"quest_id": "any_quest"}, ctx))

        assert result.ok is False
        assert result.metadata["status"] == "missing_role_data"
        assert log == []

    def test_accept_quest_fails_with_non_receptionist_role_data(self) -> None:
        """role_data with a different role (e.g. merchant) should be rejected."""
        log, executor = _recording_executor()
        ctx = _ctx_with_role_data(
            execute_command=executor,
            role_data={"role": "merchant", "inventory": []},
        )

        result = asyncio.run(AcceptQuestTool().execute({"quest_id": "any_quest"}, ctx))

        assert result.ok is False
        assert result.metadata["status"] == "missing_role_data"
        assert log == []

    def test_accept_quest_missing_quest_id_fails(self) -> None:
        log, executor = _recording_executor()
        ctx = _ctx_with_role_data(
            execute_command=executor,
            role_data={
                "role": "receptionist",
                "bulletins": [],
                "active_quests": [],
            },
        )

        result = asyncio.run(AcceptQuestTool().execute({}, ctx))

        assert result.ok is False
        assert result.metadata["status"] == "invalid_params"
        assert log == []
