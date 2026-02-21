"""B1 测试 — Teammate → AgenticExecutor 迁移。"""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# MCP stubs
def _install_mcp_stubs() -> None:
    if "mcp.client.session" in sys.modules:
        return
    mcp_mod = types.ModuleType("mcp")
    client_mod = types.ModuleType("mcp.client")
    session_mod = types.ModuleType("mcp.client.session")
    sse_mod = types.ModuleType("mcp.client.sse")
    stdio_mod = types.ModuleType("mcp.client.stdio")
    streamable_http_mod = types.ModuleType("mcp.client.streamable_http")
    session_mod.ClientSession = object
    sse_mod.sse_client = object
    stdio_mod.StdioServerParameters = object
    stdio_mod.stdio_client = object
    streamable_http_mod.streamable_http_client = object
    mcp_mod.client = client_mod
    client_mod.session = session_mod
    client_mod.sse = sse_mod
    client_mod.stdio = stdio_mod
    client_mod.streamable_http = streamable_http_mod
    sys.modules["mcp"] = mcp_mod
    sys.modules["mcp.client"] = client_mod
    sys.modules["mcp.client.session"] = session_mod
    sys.modules["mcp.client.sse"] = sse_mod
    sys.modules["mcp.client.stdio"] = stdio_mod
    sys.modules["mcp.client.streamable_http"] = streamable_http_mod

_install_mcp_stubs()

from app.services.teammate_response_service import (
    _is_combat_active,
    _make_combat_action_tool,
)


# =========================================================================
# _is_combat_active
# =========================================================================


class TestIsCombatActive:
    def test_active_when_combat_id_set(self):
        session = MagicMock()
        session.game_state.combat_id = "combat_123"
        assert _is_combat_active(session) is True

    def test_inactive_when_no_combat_id(self):
        session = MagicMock()
        session.game_state.combat_id = None
        assert _is_combat_active(session) is False

    def test_inactive_when_no_game_state(self):
        session = MagicMock(spec=[])  # no game_state attribute
        assert _is_combat_active(session) is False


# =========================================================================
# _make_combat_action_tool
# =========================================================================


class TestMakeCombatActionTool:
    def test_tool_has_correct_name(self):
        tool = _make_combat_action_tool(MagicMock(), MagicMock(), "tm_01")
        assert tool.__name__ == "choose_battle_action"

    def test_tool_has_annotations(self):
        tool = _make_combat_action_tool(MagicMock(), MagicMock(), "tm_01")
        assert "action_id" in tool.__annotations__

    def test_missing_action_id_returns_error(self):
        tool = _make_combat_action_tool(MagicMock(), MagicMock(), "tm_01")
        result = asyncio.run(tool(action_id=""))
        assert result["success"] is False
        assert "missing" in result["error"]

    def test_no_active_combat_returns_error(self):
        session = MagicMock()
        session.game_state.combat_id = None
        tool = _make_combat_action_tool(MagicMock(), session, "tm_01")
        result = asyncio.run(tool(action_id="attack"))
        assert result["success"] is False
        assert "no active combat" in result["error"]

    def test_calls_flash_cpu_combat_tool(self):
        flash_cpu = MagicMock()
        flash_cpu.call_combat_tool = AsyncMock(return_value={
            "success": True, "damage": 10,
        })
        session = MagicMock()
        session.game_state.combat_id = "c1"
        tool = _make_combat_action_tool(flash_cpu, session, "tm_01")

        result = asyncio.run(tool(action_id="slash"))
        flash_cpu.call_combat_tool.assert_called_once_with(
            "execute_action_for_actor",
            {"combat_id": "c1", "actor_id": "tm_01", "action_id": "slash"},
        )
        assert result["success"] is True
        assert result["actor_id"] == "tm_01"
        assert result["combat_id"] == "c1"


# =========================================================================
# _run_agentic_generation_payload (via AgenticExecutor)
# =========================================================================


class TestRunAgenticGenerationPayload:
    """测试 TeammateResponseService._run_agentic_generation_payload() 迁移后的行为。"""

    def _make_service(self, **overrides):
        from app.services.teammate_response_service import TeammateResponseService

        kwargs = dict(
            llm_service=MagicMock(),
            instance_manager=None,
            flash_cpu=MagicMock(),
            graph_store=MagicMock(),
            recall_orchestrator=MagicMock(),
        )
        kwargs.update(overrides)
        svc = TeammateResponseService(**kwargs)
        svc._get_agentic_system_prompt = MagicMock(return_value="You are a teammate.")
        return svc

    def _make_member(self, character_id: str = "warrior_01"):
        member = MagicMock()
        member.character_id = character_id
        member.name = "Warrior"
        return member

    def _make_session(self, combat_id=None):
        session = MagicMock()
        session.world_id = "w1"
        session.chapter_id = "ch1"
        session.area_id = "tavern"
        session.sub_location = "bar"
        session.scene_bus = MagicMock()
        session.game_state.combat_id = combat_id
        return session

    def test_session_none_returns_none(self):
        svc = self._make_service()
        context = {"_runtime_session": None}
        parsed, events = asyncio.run(
            svc._run_agentic_generation_payload(
                member=self._make_member(), context=context,
                prompt="test", model="flash", thinking_level="low",
            )
        )
        assert parsed is None
        assert events == []

    @patch("app.world.agentic_executor.AgenticExecutor")
    def test_ctx_built_correctly(self, MockExecutor):
        """验证 AgenticContext 从 session + member 正确构建。"""
        from app.world.immersive_tools import AgenticContext
        from app.models.admin_protocol import AgenticResult

        mock_run = AsyncMock(return_value=AgenticResult(narration='{"response":"hi"}'))
        MockExecutor.return_value.run = mock_run

        svc = self._make_service()
        svc.llm_service.parse_json = MagicMock(return_value={"response": "hi"})
        session = self._make_session()
        context = {"_runtime_session": session}

        asyncio.run(
            svc._run_agentic_generation_payload(
                member=self._make_member("tm_01"), context=context,
                prompt="test prompt", model="flash", thinking_level="low",
            )
        )

        call_kwargs = mock_run.call_args.kwargs
        ctx = call_kwargs["ctx"]
        assert isinstance(ctx, AgenticContext)
        assert ctx.agent_id == "tm_01"
        assert ctx.role == "teammate"
        assert ctx.world_id == "w1"
        assert ctx.area_id == "tavern"
        assert ctx.location_id == "bar"

    @patch("app.world.agentic_executor.AgenticExecutor")
    def test_no_combat_extra_tools(self, MockExecutor):
        """非战斗时 extra_tools=None。"""
        from app.models.admin_protocol import AgenticResult

        mock_run = AsyncMock(return_value=AgenticResult(narration="{}"))
        MockExecutor.return_value.run = mock_run

        svc = self._make_service()
        svc.llm_service.parse_json = MagicMock(return_value={})
        session = self._make_session(combat_id=None)

        asyncio.run(
            svc._run_agentic_generation_payload(
                member=self._make_member(), context={"_runtime_session": session},
                prompt="test", model="flash", thinking_level="low",
            )
        )

        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["extra_tools"] is None

    @patch("app.world.agentic_executor.AgenticExecutor")
    def test_combat_extra_tool_injected(self, MockExecutor):
        """战斗时 extra_tools 包含 choose_battle_action。"""
        from app.models.admin_protocol import AgenticResult

        mock_run = AsyncMock(return_value=AgenticResult(narration="{}"))
        MockExecutor.return_value.run = mock_run

        svc = self._make_service()
        svc.llm_service.parse_json = MagicMock(return_value={})
        session = self._make_session(combat_id="c1")

        asyncio.run(
            svc._run_agentic_generation_payload(
                member=self._make_member(), context={"_runtime_session": session},
                prompt="test", model="flash", thinking_level="low",
            )
        )

        call_kwargs = mock_run.call_args.kwargs
        extra = call_kwargs["extra_tools"]
        assert extra is not None
        assert len(extra) == 1
        assert extra[0].__name__ == "choose_battle_action"

    @patch("app.world.agentic_executor.AgenticExecutor")
    def test_tool_events_tagged(self, MockExecutor):
        """event_queue 事件被标记 character_id + teammate_tool_call。"""
        from app.models.admin_protocol import AgenticResult

        # AgenticExecutor.run() 会往 event_queue 写事件
        async def fake_run(**kwargs):
            eq = kwargs.get("event_queue")
            if eq:
                eq.put_nowait({"type": "agentic_tool_call", "name": "react_to_interaction", "success": True})
            return AgenticResult(narration='{"response":"ok"}')

        MockExecutor.return_value.run = fake_run

        svc = self._make_service()
        svc.llm_service.parse_json = MagicMock(return_value={"response": "ok"})
        session = self._make_session()

        parsed, events = asyncio.run(
            svc._run_agentic_generation_payload(
                member=self._make_member("warrior_01"),
                context={"_runtime_session": session},
                prompt="test", model="flash", thinking_level="low",
            )
        )

        assert len(events) == 1
        assert events[0]["type"] == "teammate_tool_call"
        assert events[0]["character_id"] == "warrior_01"
        assert events[0]["name"] == "react_to_interaction"

    @patch("app.world.agentic_executor.AgenticExecutor")
    def test_narration_parsed_as_json(self, MockExecutor):
        """AgenticResult.narration 被 parse_json 正确解析。"""
        from app.models.admin_protocol import AgenticResult

        mock_run = AsyncMock(return_value=AgenticResult(
            narration='{"response": "Hello!", "reaction": "smile", "updated_mood": "happy"}'
        ))
        MockExecutor.return_value.run = mock_run

        svc = self._make_service()
        svc.llm_service.parse_json = MagicMock(return_value={
            "response": "Hello!", "reaction": "smile", "updated_mood": "happy",
        })

        parsed, events = asyncio.run(
            svc._run_agentic_generation_payload(
                member=self._make_member(), context={"_runtime_session": self._make_session()},
                prompt="test", model="flash", thinking_level="low",
            )
        )

        assert parsed is not None
        assert parsed["response"] == "Hello!"
        assert parsed["reaction"] == "smile"
