"""Tests for D11 — Private Chat Pipeline Integration.

Validates:
- Private chat uses PipelineOrchestrator (SessionRuntime + AgenticExecutor)
- InstanceManager dual-layer cognition preserved (context_window read/write)
- SceneBus writes (contact + player speech + NPC speech)
- SessionHistory recording
- GM/teammate observation skipped (private mode)
- Dialogue options generated
- SSE event format unified with /interact/stream
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.admin_protocol import AgenticResult, AgenticToolCall


def _run(coro):
    return asyncio.run(coro)


def _collect(async_gen):
    """Collect all events from an async generator."""
    async def _inner():
        events = []
        async for evt in async_gen:
            events.append(evt)
        return events
    return asyncio.run(_inner())


def _make_llm_response(text="NPC 回复", tool_calls=None):
    resp = MagicMock()
    resp.text = text
    resp.thinking = MagicMock()
    resp.thinking.thoughts_token_count = 100
    resp.thinking.output_token_count = 50
    resp.thinking.total_token_count = 150
    resp.thinking.thoughts_summary = "thinking..."
    resp.raw_response = MagicMock()
    resp.raw_response.candidates = []
    return resp


def _make_npc_node(npc_id="priestess", name="女祭司"):
    node = MagicMock()
    node.name = name
    node.properties = {"traits": [], "tier": "secondary"}
    node.state = {"is_essential": False, "dispositions": {}}
    return node


def _make_instance(character_name="女祭司"):
    instance = MagicMock()
    instance.config = MagicMock()
    instance.config.name = character_name
    # context_window mocks
    instance.context_window = MagicMock()
    instance.context_window.get_system_prompt.return_value = "你是女祭司。"
    msg1 = MagicMock(role="user", content="你好")
    msg2 = MagicMock(role="assistant", content="冒险者，有何贵干？")
    instance.context_window.get_all_messages.return_value = [msg1, msg2]
    return instance


def _make_mock_session():
    session = MagicMock()
    session.player = MagicMock()
    session.party = None
    session.scene_bus = MagicMock()
    session.history = MagicMock()
    session.narrative = MagicMock()
    session.narrative.npc_interactions = {}
    session.game_state = None
    session.world_graph = MagicMock()
    session._world_graph_failed = False
    session.current_area = None
    session.chapter_id = "ch1"
    session.area_id = "area1"
    session.sub_location = None
    session.time = None
    session.persist = AsyncMock()
    session.restore = AsyncMock()
    session.flash_results = {}
    # world_graph.get_node returns NPC node
    session.world_graph.get_node = MagicMock(return_value=_make_npc_node())
    return session


def _make_pipeline_with_instance_manager():
    from app.services.admin.pipeline_orchestrator import PipelineOrchestrator

    flash_cpu = MagicMock()
    flash_cpu.llm_service = MagicMock()
    flash_cpu.llm_service.agentic_generate = AsyncMock(
        return_value=_make_llm_response()
    )
    flash_cpu.llm_service.generate_simple = AsyncMock(
        return_value='[{"text":"继续询问","intent":"continue","tone":"curious"}]'
    )
    flash_cpu.llm_service._strip_code_block = lambda x: x

    instance_manager = AsyncMock()
    instance_manager.get_or_create = AsyncMock(return_value=_make_instance())
    instance_manager.maybe_graphize_instance = AsyncMock()

    pipeline = PipelineOrchestrator(
        flash_cpu=flash_cpu,
        party_service=MagicMock(),
        narrative_service=MagicMock(),
        graph_store=MagicMock(),
        teammate_response_service=MagicMock(),
        session_history_manager=MagicMock(),
        character_store=MagicMock(),
        state_manager=MagicMock(),
        session_store=MagicMock(),
        recall_orchestrator=MagicMock(),
        instance_manager=instance_manager,
    )
    return pipeline, flash_cpu, instance_manager


class TestPrivateChatUsesSessionRuntime:
    """验证私聊使用 SessionRuntime restore/persist。"""

    def test_session_restore_and_persist_called(self):
        pipeline, flash_cpu, im = _make_pipeline_with_instance_manager()
        mock_session = _make_mock_session()

        with patch("app.services.admin.pipeline_orchestrator.SessionRuntime", return_value=mock_session), \
             patch("app.services.admin.pipeline_orchestrator.GameRuntime") as mock_rt:
            mock_rt.get_instance = AsyncMock(return_value=MagicMock())
            mock_rt.get_instance.return_value.get_world = AsyncMock(return_value=MagicMock())

            events = _collect(pipeline.process_private_chat_stream("w1", "s1", "priestess", "你好"))

            mock_session.restore.assert_called_once()
            mock_session.persist.assert_called_once()


class TestPrivateChatUsesAgenticExecutor:
    """验证私聊使用 AgenticExecutor 而非 generate_simple_stream。"""

    def test_agentic_generate_called(self):
        pipeline, flash_cpu, im = _make_pipeline_with_instance_manager()
        mock_session = _make_mock_session()

        with patch("app.services.admin.pipeline_orchestrator.SessionRuntime", return_value=mock_session), \
             patch("app.services.admin.pipeline_orchestrator.GameRuntime") as mock_rt:
            mock_rt.get_instance = AsyncMock(return_value=MagicMock())
            mock_rt.get_instance.return_value.get_world = AsyncMock(return_value=MagicMock())

            events = _collect(pipeline.process_private_chat_stream("w1", "s1", "priestess", "你好"))

            flash_cpu.llm_service.agentic_generate.assert_called_once()


class TestPrivateChatRecordsHistory:
    """验证私聊记录到 SessionHistory。"""

    def test_history_record_round_called(self):
        pipeline, flash_cpu, im = _make_pipeline_with_instance_manager()
        mock_session = _make_mock_session()

        with patch("app.services.admin.pipeline_orchestrator.SessionRuntime", return_value=mock_session), \
             patch("app.services.admin.pipeline_orchestrator.GameRuntime") as mock_rt:
            mock_rt.get_instance = AsyncMock(return_value=MagicMock())
            mock_rt.get_instance.return_value.get_world = AsyncMock(return_value=MagicMock())

            events = _collect(pipeline.process_private_chat_stream("w1", "s1", "priestess", "你好"))

            mock_session.history.record_round.assert_called_once()
            call_kwargs = mock_session.history.record_round.call_args
            assert call_kwargs[1]["metadata"]["source"] == "private_chat"

            mock_session.history.record_npc_response.assert_called_once()


class TestPrivateChatSkipsGmAndTeammate:
    """验证私聊跳过 GM 观察和队友旁观。"""

    def test_no_gm_observation_event(self):
        pipeline, flash_cpu, im = _make_pipeline_with_instance_manager()
        mock_session = _make_mock_session()

        with patch("app.services.admin.pipeline_orchestrator.SessionRuntime", return_value=mock_session), \
             patch("app.services.admin.pipeline_orchestrator.GameRuntime") as mock_rt:
            mock_rt.get_instance = AsyncMock(return_value=MagicMock())
            mock_rt.get_instance.return_value.get_world = AsyncMock(return_value=MagicMock())

            events = _collect(pipeline.process_private_chat_stream("w1", "s1", "priestess", "你好"))

            event_types = [e["type"] for e in events]
            assert "gm_observation" not in event_types
            assert "teammate_start" not in event_types
            assert "teammate_chunk" not in event_types
            assert "teammate_end" not in event_types

    def test_agentic_generate_called_only_once(self):
        """只调用一次 agentic_generate (NPC), 不调用 GM/队友。"""
        pipeline, flash_cpu, im = _make_pipeline_with_instance_manager()
        mock_session = _make_mock_session()

        with patch("app.services.admin.pipeline_orchestrator.SessionRuntime", return_value=mock_session), \
             patch("app.services.admin.pipeline_orchestrator.GameRuntime") as mock_rt:
            mock_rt.get_instance = AsyncMock(return_value=MagicMock())
            mock_rt.get_instance.return_value.get_world = AsyncMock(return_value=MagicMock())

            events = _collect(pipeline.process_private_chat_stream("w1", "s1", "priestess", "你好"))

            # Only NPC uses agentic_generate, no GM or teammate
            assert flash_cpu.llm_service.agentic_generate.call_count == 1


class TestPrivateChatGeneratesDialogueOptions:
    """验证私聊生成对话选项。"""

    def test_dialogue_options_in_events(self):
        pipeline, flash_cpu, im = _make_pipeline_with_instance_manager()
        mock_session = _make_mock_session()

        with patch("app.services.admin.pipeline_orchestrator.SessionRuntime", return_value=mock_session), \
             patch("app.services.admin.pipeline_orchestrator.GameRuntime") as mock_rt:
            mock_rt.get_instance = AsyncMock(return_value=MagicMock())
            mock_rt.get_instance.return_value.get_world = AsyncMock(return_value=MagicMock())

            events = _collect(pipeline.process_private_chat_stream("w1", "s1", "priestess", "你好"))

            option_events = [e for e in events if e["type"] == "dialogue_options"]
            assert len(option_events) == 1
            assert "options" in option_events[0]


class TestPrivateChatWritesInstanceContext:
    """验证私聊写回 InstanceManager 上下文。"""

    def test_context_window_receives_messages(self):
        pipeline, flash_cpu, im = _make_pipeline_with_instance_manager()
        mock_session = _make_mock_session()
        instance = _make_instance()
        im.get_or_create = AsyncMock(return_value=instance)

        with patch("app.services.admin.pipeline_orchestrator.SessionRuntime", return_value=mock_session), \
             patch("app.services.admin.pipeline_orchestrator.GameRuntime") as mock_rt:
            mock_rt.get_instance = AsyncMock(return_value=MagicMock())
            mock_rt.get_instance.return_value.get_world = AsyncMock(return_value=MagicMock())

            events = _collect(pipeline.process_private_chat_stream("w1", "s1", "priestess", "你好"))

            # Player message added
            instance.context_window.add_message.assert_any_call("user", "你好")
            # NPC response added
            instance.context_window.add_message.assert_any_call("assistant", "NPC 回复")


class TestPrivateChatPublishesToSceneBus:
    """验证私聊写入 SceneBus。"""

    def test_scene_bus_contact_and_publish(self):
        pipeline, flash_cpu, im = _make_pipeline_with_instance_manager()
        mock_session = _make_mock_session()

        with patch("app.services.admin.pipeline_orchestrator.SessionRuntime", return_value=mock_session), \
             patch("app.services.admin.pipeline_orchestrator.GameRuntime") as mock_rt:
            mock_rt.get_instance = AsyncMock(return_value=MagicMock())
            mock_rt.get_instance.return_value.get_world = AsyncMock(return_value=MagicMock())

            events = _collect(pipeline.process_private_chat_stream("w1", "s1", "priestess", "你好"))

            mock_session.scene_bus.contact.assert_called_once_with("priestess")
            # At least 2 publishes: player speech + NPC speech
            assert mock_session.scene_bus.publish.call_count >= 2


class TestSseEventsMatchInteractFormat:
    """验证 SSE 事件格式与 /interact/stream 统一。"""

    def test_event_types(self):
        pipeline, flash_cpu, im = _make_pipeline_with_instance_manager()
        mock_session = _make_mock_session()

        with patch("app.services.admin.pipeline_orchestrator.SessionRuntime", return_value=mock_session), \
             patch("app.services.admin.pipeline_orchestrator.GameRuntime") as mock_rt:
            mock_rt.get_instance = AsyncMock(return_value=MagicMock())
            mock_rt.get_instance.return_value.get_world = AsyncMock(return_value=MagicMock())

            events = _collect(pipeline.process_private_chat_stream("w1", "s1", "priestess", "你好"))

            event_types = [e["type"] for e in events]
            # Must have unified interact format
            assert "interact_start" in event_types
            assert "npc_response" in event_types
            assert "dialogue_options" in event_types
            assert "complete" in event_types
            # Must NOT have old chat format
            assert "chat_start" not in event_types
            assert "chat_chunk" not in event_types
            assert "chat_end" not in event_types

    def test_complete_event_has_required_fields(self):
        pipeline, flash_cpu, im = _make_pipeline_with_instance_manager()
        mock_session = _make_mock_session()

        with patch("app.services.admin.pipeline_orchestrator.SessionRuntime", return_value=mock_session), \
             patch("app.services.admin.pipeline_orchestrator.GameRuntime") as mock_rt:
            mock_rt.get_instance = AsyncMock(return_value=MagicMock())
            mock_rt.get_instance.return_value.get_world = AsyncMock(return_value=MagicMock())

            events = _collect(pipeline.process_private_chat_stream("w1", "s1", "priestess", "你好"))

            complete = [e for e in events if e["type"] == "complete"][0]
            assert complete["npc_id"] == "priestess"
            assert complete["npc_response"] == "NPC 回复"
            assert complete["gm_observation"] == ""
            assert complete["teammate_responses"] == []


class TestPrivateChatInstanceManagerGraphize:
    """验证 InstanceManager 图谱化检查被调用。"""

    def test_maybe_graphize_called(self):
        pipeline, flash_cpu, im = _make_pipeline_with_instance_manager()
        mock_session = _make_mock_session()

        with patch("app.services.admin.pipeline_orchestrator.SessionRuntime", return_value=mock_session), \
             patch("app.services.admin.pipeline_orchestrator.GameRuntime") as mock_rt:
            mock_rt.get_instance = AsyncMock(return_value=MagicMock())
            mock_rt.get_instance.return_value.get_world = AsyncMock(return_value=MagicMock())

            events = _collect(pipeline.process_private_chat_stream("w1", "s1", "priestess", "你好"))

            im.maybe_graphize_instance.assert_called_once_with("w1", "priestess")


class TestPrivateChatNarrativeCounting:
    """验证私聊更新叙事交互计数。"""

    def test_npc_interaction_count_incremented(self):
        pipeline, flash_cpu, im = _make_pipeline_with_instance_manager()
        mock_session = _make_mock_session()
        mock_session.narrative.npc_interactions = {"priestess": 5}

        with patch("app.services.admin.pipeline_orchestrator.SessionRuntime", return_value=mock_session), \
             patch("app.services.admin.pipeline_orchestrator.GameRuntime") as mock_rt:
            mock_rt.get_instance = AsyncMock(return_value=MagicMock())
            mock_rt.get_instance.return_value.get_world = AsyncMock(return_value=MagicMock())

            events = _collect(pipeline.process_private_chat_stream("w1", "s1", "priestess", "你好"))

            assert mock_session.narrative.npc_interactions["priestess"] == 6
            mock_session.mark_narrative_dirty.assert_called()
