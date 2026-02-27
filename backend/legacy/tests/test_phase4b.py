"""Phase 4b 批次 A 测试 — AgenticContext + AgenticExecutor + 工具实装。"""

from __future__ import annotations

import asyncio
import inspect
import sys
import types
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# MCP stubs（测试环境无 mcp 包）
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

from app.agentic.immersive_tools import AgenticContext, bind_tool, get_tool_registry


# =========================================================================
# Helper: 构建 AgenticContext
# =========================================================================


def _make_ctx(
    agent_id: str = "npc_01",
    role: str = "npc",
    session: Any = None,
    scene_bus: Any = None,
    image_service: Any = None,
    world_id: str = "w1",
    api: Any = None,
) -> AgenticContext:
    if session is None:
        session = MagicMock()
        session.recall = AsyncMock(return_value=[])
        session.record_memory = MagicMock(return_value="mem_test")
    if api is None:
        api = MagicMock()
        api.recall = AsyncMock(return_value=[])
        api.record_memory = MagicMock(return_value="mem_test")
    return AgenticContext(
        session=session,
        agent_id=agent_id,
        role=role,
        scene_bus=scene_bus,
        world_id=world_id,
        image_service=image_service,
        api=api,
    )


# =========================================================================
# AgenticContext 基础测试
# =========================================================================


class TestAgenticContext:
    def test_create_minimal(self):
        ctx = AgenticContext(session=None, agent_id="test", role="npc", scene_bus=None)
        assert ctx.agent_id == "test"
        assert ctx.role == "npc"

    def test_bind_tool_strips_ctx(self):
        """bind_tool 后，ctx 不应出现在签名和注解中。"""
        ctx = _make_ctx()
        registry = get_tool_registry()
        react_def = next(td for td in registry if td.name == "react_to_interaction")
        bound = bind_tool(react_def, ctx)

        assert "ctx" not in bound.__annotations__
        sig = inspect.signature(bound)
        assert "ctx" not in sig.parameters
        # 但 LLM 可见参数应在
        assert "dimension" in sig.parameters
        assert "level" in sig.parameters

    def test_bound_tool_calls_with_ctx(self):
        """绑定的工具调用时应将 ctx 传入。"""
        mock_api = MagicMock()
        mock_api.update_disposition = MagicMock(return_value={"success": True, "approval": 55})
        ctx = _make_ctx(api=mock_api, agent_id="bartender")

        registry = get_tool_registry()
        react_def = next(td for td in registry if td.name == "react_to_interaction")
        bound = bind_tool(react_def, ctx)

        result = asyncio.run(bound(
            dimension="approval", level="slight", is_positive=True, reason="good beer",
        ))
        assert result["success"]
        mock_api.update_disposition.assert_called_once_with(
            npc_id="bartender", deltas={"approval": 5}, reason="good beer",
        )


# =========================================================================
# AgenticExecutor 测试
# =========================================================================


@dataclass
class _FakeThinking:
    thinking_enabled: bool = False
    thinking_level: str = "medium"
    thoughts_summary: str = "test thinking"
    thoughts_token_count: int = 10
    output_token_count: int = 50
    total_token_count: int = 60


@dataclass
class _FakeLLMResponse:
    text: str = "The tavern keeper nods."
    thinking: Any = None
    raw_response: Any = None


class TestAgenticExecutor:
    def setup_method(self):
        from app.agentic.agentic_executor import AgenticExecutor
        self.AgenticExecutor = AgenticExecutor

    def _make_executor(self, text: str = "The tavern keeper nods.") -> Any:
        mock_llm = MagicMock()
        mock_llm.agentic_generate = AsyncMock(
            return_value=_FakeLLMResponse(text=text, thinking=_FakeThinking()),
        )
        return self.AgenticExecutor(mock_llm), mock_llm

    def test_run_basic(self):
        """基本 run 流程：返回 AgenticResult。"""
        executor, mock_llm = self._make_executor()
        ctx = _make_ctx(role="npc")

        result = asyncio.run(executor.run(
            ctx=ctx,
            system_prompt="You are a bartender.",
            user_prompt="Hello!",
        ))

        assert result.narration == "The tavern keeper nods."
        assert result.thinking_summary == "test thinking"
        mock_llm.agentic_generate.assert_called_once()

    def test_run_pass_detection(self):
        """[PASS] 标记应导致空 narration。"""
        executor, _ = self._make_executor(text="[PASS]")
        ctx = _make_ctx(role="gm")

        result = asyncio.run(executor.run(
            ctx=ctx,
            system_prompt="You are the GM.",
            user_prompt="Observe the scene.",
        ))

        assert result.narration == ""

    def test_run_pass_case_insensitive(self):
        """[pass] 也应被检测。"""
        executor, _ = self._make_executor(text="[pass]")
        ctx = _make_ctx()

        result = asyncio.run(executor.run(
            ctx=ctx, system_prompt="", user_prompt="",
        ))
        assert result.narration == ""

    def test_tool_recording(self):
        """工具调用应被记录到 tool_calls。"""
        executor, mock_llm = self._make_executor()

        # 模拟 agentic_generate 调用工具后再返回文本
        # 由于我们 mock 了 agentic_generate，工具不会被真实调用
        # 但我们可以测试 _wrap_recording 的单独行为
        ctx = _make_ctx()

        async def dummy_tool(greeting: str) -> Dict[str, Any]:
            """A test tool."""
            return {"success": True, "echo": greeting}

        tool_calls = []
        wrapped = executor._wrap_recording(dummy_tool, tool_calls, None)

        result = asyncio.run(wrapped(greeting="hello"))
        assert result["success"]
        assert len(tool_calls) == 1
        assert tool_calls[0].name == "dummy_tool"
        assert tool_calls[0].args == {"greeting": "hello"}
        assert tool_calls[0].success is True
        assert tool_calls[0].duration_ms >= 0

    def test_sse_events(self):
        """event_queue 应收到 agentic_tool_call 事件。"""
        executor, _ = self._make_executor()
        queue = asyncio.Queue()

        async def dummy_tool(x: int) -> Dict[str, Any]:
            """Test."""
            return {"success": True, "value": x * 2}

        tool_calls = []
        wrapped = executor._wrap_recording(dummy_tool, tool_calls, queue)

        asyncio.run(wrapped(x=5))

        assert not queue.empty()
        event = queue.get_nowait()
        assert event["type"] == "agentic_tool_call"
        assert event["name"] == "dummy_tool"
        assert event["success"] is True
        assert event["duration_ms"] >= 0

    def test_wrap_recording_preserves_signature(self):
        """_wrap_recording 应保留工具的签名和注解。"""
        executor, _ = self._make_executor()

        async def my_tool(name: str, count: int = 1) -> Dict[str, Any]:
            """My tool doc."""
            return {"success": True}

        wrapped = executor._wrap_recording(my_tool, [], None)
        assert wrapped.__name__ == "my_tool"
        assert wrapped.__doc__ == "My tool doc."
        sig = inspect.signature(wrapped)
        assert "name" in sig.parameters
        assert "count" in sig.parameters

    def test_wrap_recording_handles_exception(self):
        """工具抛异常时应记录 error。"""
        executor, _ = self._make_executor()

        async def failing_tool() -> Dict[str, Any]:
            """Fails."""
            raise ValueError("test error")

        tool_calls = []
        wrapped = executor._wrap_recording(failing_tool, tool_calls, None)

        result = asyncio.run(wrapped())
        assert result["success"] is False
        assert len(tool_calls) == 1
        assert tool_calls[0].success is False
        assert "ValueError" in tool_calls[0].error

    def test_extra_tools_appended(self):
        """extra_tools 应追加到 RoleRegistry 工具之后。"""
        executor, mock_llm = self._make_executor()
        ctx = _make_ctx(role="npc")

        async def custom_combat_tool(target: str) -> Dict[str, Any]:
            """Combat tool."""
            return {"success": True}

        asyncio.run(executor.run(
            ctx=ctx,
            system_prompt="",
            user_prompt="",
            extra_tools=[custom_combat_tool],
        ))

        # 检查传给 agentic_generate 的 tools 列表末尾包含 extra tool
        call_args = mock_llm.agentic_generate.call_args
        tools_passed = call_args.kwargs["tools"]
        tool_names = [t.__name__ for t in tools_passed]
        assert "custom_combat_tool" in tool_names


# =========================================================================
# 沉浸式工具实装测试
# =========================================================================


class TestShareThought:
    def test_publishes_to_scene_bus(self):
        """share_thought 应 publish 到 SceneBus。"""
        from app.agentic.immersive_tools import share_thought

        mock_bus = MagicMock()
        ctx = _make_ctx(agent_id="warrior", scene_bus=mock_bus)

        result = asyncio.run(share_thought(ctx=ctx, thought="I need a drink", visibility="spoken"))
        assert result["success"]
        mock_bus.publish.assert_called_once()
        entry = mock_bus.publish.call_args[0][0]
        assert entry.actor == "warrior"
        assert entry.content == "I need a drink"

    def test_internal_visibility_is_private(self):
        """visibility=internal 应设为 private。"""
        from app.agentic.immersive_tools import share_thought

        mock_bus = MagicMock()
        ctx = _make_ctx(agent_id="mage", scene_bus=mock_bus)

        asyncio.run(share_thought(ctx=ctx, thought="Hmm suspicious", visibility="internal"))
        entry = mock_bus.publish.call_args[0][0]
        assert "private:" in entry.visibility

    def test_no_scene_bus_returns_stub(self):
        """无 scene_bus 时返回 stub。"""
        from app.agentic.immersive_tools import share_thought

        ctx = _make_ctx(scene_bus=None)
        result = asyncio.run(share_thought(ctx=ctx, thought="test"))
        assert result.get("stub") is True


class TestRecallExperience:
    def test_calls_api_recall(self):
        """recall_experience 应调用 api.recall。"""
        from app.agentic.immersive_tools import recall_experience

        mock_api = MagicMock()
        mock_api.recall = AsyncMock(
            return_value=[
                {"name": "beer", "summary": "", "relevance": 0.9},
                {"name": "tavern", "summary": "", "relevance": 0.7},
                {"name": "quest", "summary": "", "relevance": 0.5},
            ]
        )
        ctx = _make_ctx(
            agent_id="bartender",
            api=mock_api,
            world_id="w1",
        )

        result = asyncio.run(recall_experience(ctx=ctx, seeds=["beer", "rumors"]))
        assert result["success"]
        assert len(result["memories"]) == 3
        assert result["memories"][0]["concept"] == "beer"
        mock_api.recall.assert_called_once_with(
            role="npc",
            actor_id="bartender",
            seeds=["beer", "rumors"],
            intent_type="recall",
            limit=10,
        )

    def test_no_api_returns_stub(self):
        """无 api 时返回 stub。"""
        from app.agentic.immersive_tools import recall_experience

        ctx = _make_ctx()
        ctx.api = None
        result = asyncio.run(recall_experience(ctx=ctx, seeds=["test"]))
        assert result.get("stub") is True


class TestFormImpression:
    def test_calls_api_record_memory(self):
        """form_impression 应调用 api.record_memory。"""
        from app.agentic.immersive_tools import form_impression

        mock_api = MagicMock()
        mock_api.record_memory = MagicMock(return_value="impression_1")
        ctx = _make_ctx(agent_id="bartender", api=mock_api, world_id="w1")

        result = asyncio.run(form_impression(
            ctx=ctx, about="adventurer", impression="Seems trustworthy",
        ))
        assert result["success"]
        assert result["about"] == "adventurer"
        assert result["node_id"] == "impression_1"
        mock_api.record_memory.assert_called_once()
        call_kwargs = mock_api.record_memory.call_args.kwargs
        assert call_kwargs["memory_type"] == "impression"
        assert call_kwargs["owner_id"] == "bartender"
        assert call_kwargs["role"] == "npc"

    def test_no_api_returns_stub(self):
        """无 api 时返回 stub。"""
        from app.agentic.immersive_tools import form_impression

        ctx = _make_ctx()
        ctx.api = None
        result = asyncio.run(form_impression(ctx=ctx, about="x", impression="y"))
        assert result.get("stub") is True


class TestNoticeSomething:
    def test_publishes_to_scene_bus(self):
        """notice_something 应 publish 到 SceneBus。"""
        from app.agentic.immersive_tools import notice_something

        mock_bus = MagicMock()
        ctx = _make_ctx(agent_id="guard", scene_bus=mock_bus)

        result = asyncio.run(notice_something(
            ctx=ctx, observation="A stranger enters", reaction="Watchful",
        ))
        assert result["success"]
        mock_bus.publish.assert_called_once()
        entry = mock_bus.publish.call_args[0][0]
        assert entry.actor == "guard"
        assert "stranger" in entry.content

    def test_no_scene_bus_returns_stub(self):
        """无 scene_bus 时返回 stub。"""
        from app.agentic.immersive_tools import notice_something

        ctx = _make_ctx(scene_bus=None)
        result = asyncio.run(notice_something(ctx=ctx, observation="test"))
        assert result.get("stub") is True


class TestCompleteEvent:
    def test_calls_complete_event(self):
        """complete_event 应调用 api.complete_event。"""
        from app.agentic.immersive_tools import complete_event

        mock_api = MagicMock()
        mock_api.complete_event.return_value = {"success": True, "event_id": "quest_01"}
        ctx = _make_ctx(api=mock_api)

        result = asyncio.run(complete_event(ctx=ctx, event_id="quest_01"))
        assert result["success"]
        mock_api.complete_event.assert_called_once_with("quest_01", "")

    def test_no_api_returns_stub(self):
        """无 api 时返回 stub。"""
        from app.agentic.immersive_tools import complete_event

        ctx = AgenticContext(session=None, agent_id="test", role="npc", scene_bus=None)
        result = asyncio.run(complete_event(ctx=ctx, event_id="q1"))
        assert result.get("stub") is True


class TestGenerateSceneImage:
    def test_calls_image_service(self):
        """generate_scene_image 应调用 image_service.generate。"""
        from app.agentic.immersive_tools import generate_scene_image

        mock_img = MagicMock()
        mock_img.generate = AsyncMock(return_value={"url": "http://test.com/img.png"})
        ctx = _make_ctx(image_service=mock_img)

        result = asyncio.run(generate_scene_image(
            ctx=ctx, scene_description="A dark tavern",
        ))
        assert result["success"]
        assert "image_data" in result

    def test_no_image_service_returns_stub(self):
        """无 image_service 时返回 stub。"""
        from app.agentic.immersive_tools import generate_scene_image

        ctx = _make_ctx(image_service=None)
        result = asyncio.run(generate_scene_image(ctx=ctx, scene_description="test"))
        assert result.get("stub") is True


class TestStubToolsStillWork:
    """保持 stub 的工具仍应正常返回。"""

    def test_advance_chapter_calls_api(self):
        from app.agentic.immersive_tools import advance_chapter

        mock_api = MagicMock()
        mock_api.advance_chapter.return_value = {"success": True}
        ctx = _make_ctx(api=mock_api)
        result = asyncio.run(advance_chapter(ctx=ctx, target_chapter_id="ch2"))
        assert result["success"]
        mock_api.advance_chapter.assert_called_once()

    def test_fail_event_calls_api(self):
        from app.agentic.immersive_tools import fail_event

        mock_api = MagicMock()
        mock_api.fail_event.return_value = {"success": True}
        ctx = _make_ctx(api=mock_api)
        result = asyncio.run(fail_event(ctx=ctx, event_id="e1"))
        assert result["success"]
        mock_api.fail_event.assert_called_once()

    def test_evaluate_offer_stub(self):
        from app.agentic.immersive_tools import evaluate_offer

        ctx = _make_ctx()
        result = asyncio.run(evaluate_offer(ctx=ctx, item_id="sword", offered_price=100))
        assert result["success"]
        assert result.get("stub") is True

    def test_express_need_stub(self):
        from app.agentic.immersive_tools import express_need

        ctx = _make_ctx()
        result = asyncio.run(express_need(ctx=ctx, need="I need rest"))
        assert result["success"]
        assert result.get("stub") is True
