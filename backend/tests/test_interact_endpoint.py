"""B2 测试 — NPC /interact 端点相关逻辑。"""

from __future__ import annotations

import asyncio
import json
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


# =========================================================================
# Models
# =========================================================================


class TestInteractModels:
    def test_interact_request_fields(self):
        from app.models.admin_protocol import InteractRequest
        req = InteractRequest(npc_id="merchant_01", input="你好")
        assert req.npc_id == "merchant_01"
        assert req.input == "你好"
        assert req.context_hint == ""

    def test_dialogue_option_defaults(self):
        from app.models.admin_protocol import DialogueOption
        opt = DialogueOption(text="继续询问")
        assert opt.text == "继续询问"
        assert opt.intent == ""
        assert opt.tone == "neutral"

    def test_dialogue_option_with_tone(self):
        from app.models.admin_protocol import DialogueOption
        opt = DialogueOption(text="威胁他", intent="threaten", tone="threatening")
        assert opt.tone == "threatening"


# =========================================================================
# _build_npc_system_prompt
# =========================================================================


class TestBuildNpcSystemPrompt:
    def _make_orchestrator(self):
        from app.services.admin.pipeline_orchestrator import PipelineOrchestrator
        return PipelineOrchestrator(
            flash_cpu=MagicMock(),
            party_service=MagicMock(),
            narrative_service=MagicMock(),
            graph_store=MagicMock(),
            teammate_response_service=MagicMock(),
            session_history_manager=MagicMock(),
            character_store=MagicMock(),
            state_manager=MagicMock(),
        )

    def _make_node(self, **props):
        node = MagicMock()
        node.name = props.pop("name", "酒保")
        node.properties = props
        node.state = props.pop("state", {})
        return node

    def test_custom_system_prompt_takes_priority(self):
        orch = self._make_orchestrator()
        node = self._make_node(system_prompt="我是自定义提示词")
        result = orch._build_npc_system_prompt(node, MagicMock())
        assert result == "我是自定义提示词"

    def test_full_properties_prompt(self):
        orch = self._make_orchestrator()
        node = self._make_node(
            name="酒保",
            occupation="酒保",
            personality="友善",
            speech_pattern="慢吞吞",
            background="在这家酒馆工作了十年",
        )
        result = orch._build_npc_system_prompt(node, MagicMock())
        assert "你是酒保" in result
        assert "职业：酒保" in result
        assert "性格：友善" in result
        assert "说话风格：慢吞吞" in result
        assert "背景：在这家酒馆工作了十年" in result
        assert "以第一人称回应" in result

    def test_disposition_context(self):
        orch = self._make_orchestrator()
        node = self._make_node(
            name="商人",
            state={"dispositions": {"player": {"approval": 15, "trust": -5}}},
        )
        result = orch._build_npc_system_prompt(node, MagicMock())
        assert "好感+15" in result
        assert "信任-5" in result

    def test_minimal_node_prompt(self):
        orch = self._make_orchestrator()
        node = self._make_node(name="路人")
        result = orch._build_npc_system_prompt(node, MagicMock())
        assert "你是路人" in result
        assert "以第一人称回应" in result


# =========================================================================
# _select_npc_model
# =========================================================================


class TestSelectNpcModel:
    def _make_node(self, tier="secondary", is_essential=False):
        node = MagicMock()
        node.properties = {"tier": tier}
        node.state = {"is_essential": is_essential}
        return node

    @patch("app.services.admin.pipeline_orchestrator.settings")
    def test_main_tier_uses_main_model(self, mock_settings):
        from app.services.admin.pipeline_orchestrator import PipelineOrchestrator
        mock_settings.npc_tier_config.main_model = "gemini-main"
        mock_settings.npc_tier_config.main_thinking = "low"
        model, thinking = PipelineOrchestrator._select_npc_model(self._make_node(tier="main"))
        assert model == "gemini-main"
        assert thinking == "low"

    @patch("app.services.admin.pipeline_orchestrator.settings")
    def test_essential_npc_uses_main_model(self, mock_settings):
        from app.services.admin.pipeline_orchestrator import PipelineOrchestrator
        mock_settings.npc_tier_config.main_model = "gemini-main"
        mock_settings.npc_tier_config.main_thinking = "low"
        model, thinking = PipelineOrchestrator._select_npc_model(
            self._make_node(tier="secondary", is_essential=True),
        )
        assert model == "gemini-main"

    @patch("app.services.admin.pipeline_orchestrator.settings")
    def test_secondary_tier_uses_secondary_model(self, mock_settings):
        from app.services.admin.pipeline_orchestrator import PipelineOrchestrator
        mock_settings.npc_tier_config.secondary_model = "gemini-secondary"
        mock_settings.npc_tier_config.secondary_thinking = "medium"
        model, thinking = PipelineOrchestrator._select_npc_model(self._make_node(tier="secondary"))
        assert model == "gemini-secondary"
        assert thinking == "medium"


# =========================================================================
# _generate_dialogue_options
# =========================================================================


class TestGenerateDialogueOptions:
    def _make_orchestrator(self):
        from app.services.admin.pipeline_orchestrator import PipelineOrchestrator
        orch = PipelineOrchestrator(
            flash_cpu=MagicMock(),
            party_service=MagicMock(),
            narrative_service=MagicMock(),
            graph_store=MagicMock(),
            teammate_response_service=MagicMock(),
            session_history_manager=MagicMock(),
            character_store=MagicMock(),
            state_manager=MagicMock(),
        )
        return orch

    @patch("app.services.admin.pipeline_orchestrator.settings")
    def test_llm_returns_valid_options(self, mock_settings):
        mock_settings.npc_tier_config.passerby_model = "flash"
        orch = self._make_orchestrator()
        orch.flash_cpu.llm_service.generate_simple = AsyncMock(return_value=json.dumps([
            {"text": "告诉我更多", "intent": "inquire", "tone": "curious"},
            {"text": "谢谢你", "intent": "thank", "tone": "friendly"},
            {"text": "我走了", "intent": "leave", "tone": "neutral"},
            {"text": "你在隐瞒什么", "intent": "pressure", "tone": "threatening"},
        ]))
        orch.flash_cpu.llm_service._strip_code_block = lambda x: x

        options = asyncio.run(orch._generate_dialogue_options(
            "酒保", MagicMock(), "你好", "欢迎光临！", MagicMock(),
        ))
        assert len(options) == 4
        assert options[0].text == "告诉我更多"
        assert options[0].tone == "curious"

    @patch("app.services.admin.pipeline_orchestrator.settings")
    def test_llm_failure_returns_fallback(self, mock_settings):
        mock_settings.npc_tier_config.passerby_model = "flash"
        orch = self._make_orchestrator()
        orch.flash_cpu.llm_service.generate_simple = AsyncMock(side_effect=Exception("LLM error"))

        options = asyncio.run(orch._generate_dialogue_options(
            "酒保", MagicMock(), "你好", "欢迎光临！", MagicMock(),
        ))
        assert len(options) == 4
        assert options[0].text == "继续询问"
        assert options[2].text == "告辞离开"

    @patch("app.services.admin.pipeline_orchestrator.settings")
    def test_invalid_json_returns_fallback(self, mock_settings):
        mock_settings.npc_tier_config.passerby_model = "flash"
        orch = self._make_orchestrator()
        orch.flash_cpu.llm_service.generate_simple = AsyncMock(return_value="not json at all")
        orch.flash_cpu.llm_service._strip_code_block = lambda x: x

        options = asyncio.run(orch._generate_dialogue_options(
            "酒保", MagicMock(), "你好", "回复", MagicMock(),
        ))
        assert len(options) == 4
        assert options[0].text == "继续询问"


# =========================================================================
# process_interact_stream — 集成流事件序列
# =========================================================================


class TestProcessInteractStream:
    """测试 process_interact_stream 事件序列。"""

    def _make_orchestrator(self):
        from app.services.admin.pipeline_orchestrator import PipelineOrchestrator
        orch = PipelineOrchestrator(
            flash_cpu=MagicMock(),
            party_service=MagicMock(),
            narrative_service=MagicMock(),
            graph_store=MagicMock(),
            teammate_response_service=MagicMock(),
            session_history_manager=MagicMock(),
            character_store=MagicMock(),
            state_manager=MagicMock(),
        )
        return orch

    def _make_session(self, has_player=True, has_wg=True, npc_exists=True):
        session = MagicMock()
        if has_player:
            session.player = MagicMock()
        else:
            session.player = None
        session.world_id = "w1"
        session.chapter_id = "ch1"
        session.area_id = "tavern"
        session.sub_location = "bar"
        session.party = None
        session.history = MagicMock()
        session.narrative = MagicMock()
        session.narrative.npc_interactions = {}

        # SceneBus
        scene_bus = MagicMock()
        scene_bus.get_round_summary = MagicMock(return_value="玩家说：你好")
        session.scene_bus = scene_bus

        # WorldGraph
        if has_wg:
            wg = MagicMock()
            npc_node = MagicMock()
            npc_node.name = "酒保"
            npc_node.properties = {"personality": "友善", "traits": []}
            npc_node.state = {}
            if npc_exists:
                wg.get_node = MagicMock(return_value=npc_node)
            else:
                wg.get_node = MagicMock(return_value=None)
            session.world_graph = wg
        else:
            session.world_graph = None

        return session

    @patch("app.services.admin.pipeline_orchestrator.GameRuntime")
    @patch("app.services.admin.pipeline_orchestrator.SessionRuntime")
    @patch("app.world.agentic_executor.AgenticExecutor")
    @patch("app.services.admin.pipeline_orchestrator.settings")
    def test_npc_not_found_yields_error(self, mock_settings, MockExecutor, MockSR, MockGR):
        mock_settings.npc_tier_config.passerby_model = "flash"
        mock_settings.admin_agentic_model = "flash"
        mock_settings.admin_agentic_thinking = "low"

        session = self._make_session(npc_exists=False)
        MockSR.return_value = session
        MockSR.return_value.restore = AsyncMock()
        MockGR.get_instance = AsyncMock(return_value=MagicMock(
            get_world=AsyncMock(return_value=MagicMock()),
        ))

        orch = self._make_orchestrator()
        events = []

        async def collect():
            async for evt in orch.process_interact_stream(
                world_id="w1", session_id="s1", npc_id="nobody", player_input="你好",
            ):
                events.append(evt)

        asyncio.run(collect())
        error_events = [e for e in events if e.get("type") == "error"]
        assert len(error_events) >= 1
        assert "不存在" in error_events[0]["error"]

    @patch("app.services.admin.pipeline_orchestrator.GameRuntime")
    @patch("app.services.admin.pipeline_orchestrator.SessionRuntime")
    @patch("app.world.agentic_executor.AgenticExecutor")
    @patch("app.services.admin.pipeline_orchestrator.settings")
    def test_event_sequence_has_interact_start_and_complete(
        self, mock_settings, MockExecutor, MockSR, MockGR,
    ):
        from app.models.admin_protocol import AgenticResult
        mock_settings.npc_tier_config.passerby_model = "flash"
        mock_settings.npc_tier_config.secondary_model = "flash"
        mock_settings.npc_tier_config.secondary_thinking = None
        mock_settings.admin_agentic_model = "flash"
        mock_settings.admin_agentic_thinking = "low"

        session = self._make_session()
        session.persist = AsyncMock()
        MockSR.return_value = session
        MockSR.return_value.restore = AsyncMock()
        MockGR.get_instance = AsyncMock(return_value=MagicMock(
            get_world=AsyncMock(return_value=MagicMock()),
        ))

        # NPC executor returns dialogue, GM returns [PASS]
        call_count = 0

        async def fake_run(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return AgenticResult(narration="欢迎光临我的酒馆！")
            return AgenticResult(narration="")  # GM [PASS] → empty

        MockExecutor.return_value.run = fake_run

        orch = self._make_orchestrator()
        orch.flash_cpu.llm_service.generate_simple = AsyncMock(return_value="[]")
        orch.flash_cpu.llm_service._strip_code_block = lambda x: x
        orch.memory_graphizer = None

        events = []

        async def collect():
            async for evt in orch.process_interact_stream(
                world_id="w1", session_id="s1", npc_id="bartender", player_input="你好",
            ):
                events.append(evt)

        asyncio.run(collect())

        types_seen = [e["type"] for e in events]
        assert "interact_start" in types_seen
        assert "npc_response" in types_seen
        assert "dialogue_options" in types_seen
        assert "complete" in types_seen

    @patch("app.services.admin.pipeline_orchestrator.GameRuntime")
    @patch("app.services.admin.pipeline_orchestrator.SessionRuntime")
    @patch("app.world.agentic_executor.AgenticExecutor")
    @patch("app.services.admin.pipeline_orchestrator.settings")
    def test_gm_pass_produces_no_observation(
        self, mock_settings, MockExecutor, MockSR, MockGR,
    ):
        """GM 返回空 narration（[PASS] 被 executor 清空）→ 无 gm_observation。"""
        from app.models.admin_protocol import AgenticResult
        mock_settings.npc_tier_config.passerby_model = "flash"
        mock_settings.npc_tier_config.secondary_model = "flash"
        mock_settings.npc_tier_config.secondary_thinking = None
        mock_settings.admin_agentic_model = "flash"
        mock_settings.admin_agentic_thinking = "low"

        session = self._make_session()
        session.persist = AsyncMock()
        MockSR.return_value = session
        MockSR.return_value.restore = AsyncMock()
        MockGR.get_instance = AsyncMock(return_value=MagicMock(
            get_world=AsyncMock(return_value=MagicMock()),
        ))

        call_count = 0

        async def fake_run(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return AgenticResult(narration="你好啊冒险者")
            return AgenticResult(narration="")  # [PASS]

        MockExecutor.return_value.run = fake_run

        orch = self._make_orchestrator()
        orch.flash_cpu.llm_service.generate_simple = AsyncMock(return_value="[]")
        orch.flash_cpu.llm_service._strip_code_block = lambda x: x
        orch.memory_graphizer = None

        events = []

        async def collect():
            async for evt in orch.process_interact_stream(
                world_id="w1", session_id="s1", npc_id="bartender", player_input="你好",
            ):
                events.append(evt)

        asyncio.run(collect())
        assert not any(e.get("type") == "gm_observation" for e in events)

    @patch("app.services.admin.pipeline_orchestrator.GameRuntime")
    @patch("app.services.admin.pipeline_orchestrator.SessionRuntime")
    @patch("app.world.agentic_executor.AgenticExecutor")
    @patch("app.services.admin.pipeline_orchestrator.settings")
    def test_scene_bus_cleared_after_interact(
        self, mock_settings, MockExecutor, MockSR, MockGR,
    ):
        from app.models.admin_protocol import AgenticResult
        mock_settings.npc_tier_config.passerby_model = "flash"
        mock_settings.npc_tier_config.secondary_model = "flash"
        mock_settings.npc_tier_config.secondary_thinking = None
        mock_settings.admin_agentic_model = "flash"
        mock_settings.admin_agentic_thinking = "low"

        session = self._make_session()
        session.persist = AsyncMock()
        MockSR.return_value = session
        MockSR.return_value.restore = AsyncMock()
        MockGR.get_instance = AsyncMock(return_value=MagicMock(
            get_world=AsyncMock(return_value=MagicMock()),
        ))

        MockExecutor.return_value.run = AsyncMock(
            return_value=AgenticResult(narration="回复"),
        )

        orch = self._make_orchestrator()
        orch.flash_cpu.llm_service.generate_simple = AsyncMock(return_value="[]")
        orch.flash_cpu.llm_service._strip_code_block = lambda x: x
        orch.memory_graphizer = None

        async def collect():
            async for _ in orch.process_interact_stream(
                world_id="w1", session_id="s1", npc_id="bartender", player_input="你好",
            ):
                pass

        asyncio.run(collect())
        session.scene_bus.clear.assert_called()
        session.persist.assert_called_once()

    @patch("app.services.admin.pipeline_orchestrator.GameRuntime")
    @patch("app.services.admin.pipeline_orchestrator.SessionRuntime")
    @patch("app.world.agentic_executor.AgenticExecutor")
    @patch("app.services.admin.pipeline_orchestrator.settings")
    def test_no_player_yields_error(
        self, mock_settings, MockExecutor, MockSR, MockGR,
    ):
        mock_settings.npc_tier_config.passerby_model = "flash"

        session = self._make_session(has_player=False)
        MockSR.return_value = session
        MockSR.return_value.restore = AsyncMock()
        MockGR.get_instance = AsyncMock(return_value=MagicMock(
            get_world=AsyncMock(return_value=MagicMock()),
        ))

        orch = self._make_orchestrator()
        events = []

        async def collect():
            async for evt in orch.process_interact_stream(
                world_id="w1", session_id="s1", npc_id="npc1", player_input="hi",
            ):
                events.append(evt)

        asyncio.run(collect())
        assert any(e.get("type") == "error" for e in events)
