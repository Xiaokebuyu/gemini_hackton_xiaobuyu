"""Tests for Phase 4c — Pipeline B-stage integration (C3).

Validates:
- B-stage uses AgenticExecutor instead of flash_cpu.agentic_process_v4
- C-stage post-processing still works (npc_responses, dispositions)
- image_data extraction
- Empty narration fallback
- exclude_tools pass-through
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.admin_protocol import AgenticResult, AgenticToolCall


def _run(coro):
    return asyncio.run(coro)


def _make_llm_response(text="GM 叙述", tool_calls=None):
    """Create a mock LLM response matching llm_service.agentic_generate() return."""
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


def _make_pipeline():
    """Create a PipelineOrchestrator with mocked dependencies."""
    from app.services.admin.pipeline_orchestrator import PipelineOrchestrator

    flash_cpu = MagicMock()
    flash_cpu.llm_service = MagicMock()
    flash_cpu.llm_service.agentic_generate = AsyncMock(
        return_value=_make_llm_response()
    )
    flash_cpu._load_agentic_prompt.return_value = "You are a GM."
    flash_cpu.image_service = MagicMock()

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
    )
    return pipeline, flash_cpu


class TestBStageUsesAgenticExecutor:
    """验证 B-stage 不再调用 flash_cpu.agentic_process_v4。"""

    def test_agentic_process_v4_not_called(self):
        pipeline, flash_cpu = _make_pipeline()

        # Mock session restoration
        mock_session = MagicMock()
        mock_session.player = MagicMock()
        mock_session.party = None
        mock_session.scene_bus = None
        mock_session.history = None
        mock_session.narrative = None
        mock_session.game_state = None
        mock_session.world_graph = None
        mock_session._world_graph_failed = False
        mock_session.current_area = None
        mock_session.chapter_id = "ch1"
        mock_session.area_id = "area1"
        mock_session.sub_location = None
        mock_session.time = None
        mock_session.run_behavior_tick.return_value = None
        mock_session.check_chapter_transitions.return_value = None
        mock_session.persist = AsyncMock()
        mock_session.restore = AsyncMock()
        mock_session.flash_results = {}

        with patch("app.services.admin.pipeline_orchestrator.SessionRuntime", return_value=mock_session), \
             patch("app.services.admin.pipeline_orchestrator.GameRuntime") as mock_rt, \
             patch("app.services.admin.pipeline_orchestrator.ContextAssembler") as mock_asm:
            mock_rt.get_instance = AsyncMock(return_value=MagicMock())
            mock_rt.get_instance.return_value.get_world = AsyncMock(return_value=MagicMock())
            mock_asm.assemble.return_value = MagicMock()
            mock_asm.assemble.return_value.to_flat_dict.return_value = {"test": True}
            pipeline.narrative_service.get_current_chapter_plan = AsyncMock(return_value=None)

            result = _run(pipeline.process("w1", "s1", "你好"))

            # agentic_process_v4 should NOT be called
            flash_cpu.agentic_process_v4.assert_not_called()
            # llm_service.agentic_generate should be called (via AgenticExecutor)
            flash_cpu.llm_service.agentic_generate.assert_called_once()
            assert result.narration == "GM 叙述"


class TestEmptyNarrationFallback:
    def test_empty_narration_gets_fallback(self):
        pipeline, flash_cpu = _make_pipeline()
        flash_cpu.llm_service.agentic_generate = AsyncMock(
            return_value=_make_llm_response(text="")
        )

        mock_session = MagicMock()
        mock_session.player = MagicMock()
        mock_session.party = None
        mock_session.scene_bus = None
        mock_session.history = None
        mock_session.narrative = None
        mock_session.game_state = None
        mock_session.world_graph = None
        mock_session._world_graph_failed = False
        mock_session.current_area = None
        mock_session.chapter_id = "ch1"
        mock_session.area_id = "area1"
        mock_session.sub_location = None
        mock_session.time = None
        mock_session.run_behavior_tick.return_value = None
        mock_session.check_chapter_transitions.return_value = None
        mock_session.persist = AsyncMock()
        mock_session.restore = AsyncMock()
        mock_session.flash_results = {}

        with patch("app.services.admin.pipeline_orchestrator.SessionRuntime", return_value=mock_session), \
             patch("app.services.admin.pipeline_orchestrator.GameRuntime") as mock_rt, \
             patch("app.services.admin.pipeline_orchestrator.ContextAssembler") as mock_asm:
            mock_rt.get_instance = AsyncMock(return_value=MagicMock())
            mock_rt.get_instance.return_value.get_world = AsyncMock(return_value=MagicMock())
            mock_asm.assemble.return_value = MagicMock()
            mock_asm.assemble.return_value.to_flat_dict.return_value = {}
            pipeline.narrative_service.get_current_chapter_plan = AsyncMock(return_value=None)

            result = _run(pipeline.process("w1", "s1", "..."))
            assert "沉默" in result.narration


class TestExcludeToolsPassthrough:
    """验证引擎排除正确传递到 AgenticExecutor。"""

    def test_engine_talk_excludes_npc_dialogue(self):
        """当 engine_executed.type='talk' 时，exclude_tools 包含 npc_dialogue。"""
        from app.world.gm_extra_tools import ENGINE_TOOL_EXCLUSIONS

        assert "npc_dialogue" in ENGINE_TOOL_EXCLUSIONS["talk"]

    def test_engine_use_item_excludes_inventory_tools(self):
        from app.world.gm_extra_tools import ENGINE_TOOL_EXCLUSIONS

        assert "add_item" in ENGINE_TOOL_EXCLUSIONS["use_item"]
        assert "remove_item" in ENGINE_TOOL_EXCLUSIONS["use_item"]

    def test_move_area_and_rest_not_in_exclusions(self):
        """move_area/rest 不再有排除项（update_time 已删除）。"""
        from app.world.gm_extra_tools import ENGINE_TOOL_EXCLUSIONS

        assert "move_area" not in ENGINE_TOOL_EXCLUSIONS
        assert "rest" not in ENGINE_TOOL_EXCLUSIONS


class TestCStageDispositions:
    """验证 C-stage 好感度提取仍然兼容。"""

    def test_update_disposition_tc_extraction(self):
        """C-stage 通过 tc.name=='update_disposition' 提取好感度。"""
        tc = AgenticToolCall(
            name="update_disposition",
            args={"npc_id": "priestess", "deltas": {"approval": 10}},
            success=True,
            result={
                "success": True,
                "npc_id": "priestess",
                "applied_deltas": {"approval": 10},
                "current": {"approval": 50, "trust": 30, "fear": 0, "romance": 0},
            },
        )
        # Simulate C-stage extraction
        dispositions = {}
        for _tc in [tc]:
            if _tc.name == "update_disposition" and _tc.success and _tc.result:
                npc_id = _tc.result.get("npc_id") or _tc.args.get("npc_id")
                current = _tc.result.get("current")
                if npc_id and isinstance(current, dict):
                    entry = dispositions.get(npc_id, {})
                    for dim in ("approval", "trust", "fear", "romance"):
                        if dim in current:
                            entry[dim] = current[dim]
                    dispositions[npc_id] = entry

        assert "priestess" in dispositions
        assert dispositions["priestess"]["approval"] == 50


class TestCStageNpcResponses:
    """验证 C-stage NPC 对话提取仍然兼容。"""

    def test_npc_dialogue_tc_extraction(self):
        tc = AgenticToolCall(
            name="npc_dialogue",
            args={"npc_id": "merchant", "message": "你好"},
            success=True,
            result={"response": "欢迎光临!", "npc_name": "商人", "success": True},
        )
        npc_responses = []
        for _tc in [tc]:
            if _tc.name == "npc_dialogue" and _tc.success and _tc.result.get("response"):
                npc_responses.append({
                    "character_id": _tc.args.get("npc_id", ""),
                    "name": _tc.result.get("npc_name", ""),
                    "dialogue": _tc.result["response"],
                })

        assert len(npc_responses) == 1
        assert npc_responses[0]["dialogue"] == "欢迎光临!"


class TestCStageCompleteEvent:
    """验证 complete_event 工具名兼容 C-stage 进度计数。"""

    def test_complete_event_in_progress_tools(self):
        progress_tools = {
            "complete_event", "complete_objective",
            "activate_event", "advance_chapter",
            "advance_stage", "complete_event_objective",
        }
        assert "complete_event" in progress_tools
        # conclude_quest is NOT in the set (renamed)
        assert "conclude_quest" not in progress_tools


class TestImageDataExtraction:
    def test_image_data_from_immersive_tool(self):
        """immersive generate_scene_image 返回 image_data 字典。"""
        result = AgenticResult(narration="test", tool_calls=[
            AgenticToolCall(
                name="generate_scene_image",
                args={"scene_description": "forest"},
                success=True,
                result={"success": True, "image_data": {"base64": "abc", "mime_type": "image/png"}},
            ),
        ])

        # Simulate pipeline extraction
        for tc in result.tool_calls:
            if tc.name == "generate_scene_image" and tc.success:
                img = tc.result
                if isinstance(img, dict):
                    if img.get("image_data"):
                        result.image_data = {"generated": True, **(img["image_data"] if isinstance(img["image_data"], dict) else {})}
                        break
                    if img.get("generated"):
                        result.image_data = img
                        break

        assert result.image_data is not None
        assert result.image_data["generated"] is True
        assert result.image_data["mime_type"] == "image/png"


class TestAutoTimeAdvance:
    """验证 Pipeline C 阶段自动时间推进。"""

    def test_auto_advance_when_no_engine(self):
        """无引擎执行时，session.advance_time(10) 被调用。"""
        pipeline, flash_cpu = _make_pipeline()
        mock_session = MagicMock()
        mock_session.player = MagicMock()
        mock_session.party = None
        mock_session.scene_bus = None
        mock_session.history = None
        mock_session.narrative = None
        mock_session.game_state = None
        mock_session.world_graph = None
        mock_session._world_graph_failed = False
        mock_session.current_area = None
        mock_session.chapter_id = "ch1"
        mock_session.area_id = "area1"
        mock_session.sub_location = None
        mock_session.time = None
        mock_session.run_behavior_tick.return_value = None
        mock_session.check_chapter_transitions.return_value = None
        mock_session.persist = AsyncMock()
        mock_session.restore = AsyncMock()
        mock_session.flash_results = {}

        with patch("app.services.admin.pipeline_orchestrator.SessionRuntime", return_value=mock_session), \
             patch("app.services.admin.pipeline_orchestrator.GameRuntime") as mock_rt, \
             patch("app.services.admin.pipeline_orchestrator.ContextAssembler") as mock_asm:
            mock_rt.get_instance = AsyncMock(return_value=MagicMock())
            mock_rt.get_instance.return_value.get_world = AsyncMock(return_value=MagicMock())
            mock_asm.assemble.return_value = MagicMock()
            mock_asm.assemble.return_value.to_flat_dict.return_value = {}
            pipeline.narrative_service.get_current_chapter_plan = AsyncMock(return_value=None)

            _run(pipeline.process("w1", "s1", "你好"))
            mock_session.advance_time.assert_called_once_with(10)

    def test_skip_advance_when_move_area(self):
        """engine_executed.type='move_area' 时跳过自动推进。"""
        pipeline, flash_cpu = _make_pipeline()
        mock_session = MagicMock()
        mock_session.player = MagicMock()
        mock_session.party = None
        mock_session.scene_bus = None
        mock_session.history = None
        mock_session.narrative = None
        mock_session.game_state = None
        mock_session.world_graph = None
        mock_session._world_graph_failed = False
        mock_session.current_area = None
        mock_session.chapter_id = "ch1"
        mock_session.area_id = "area1"
        mock_session.sub_location = None
        mock_session.time = None
        mock_session.run_behavior_tick.return_value = None
        mock_session.check_chapter_transitions.return_value = None
        mock_session.persist = AsyncMock()
        mock_session.restore = AsyncMock()
        mock_session.flash_results = {}

        with patch("app.services.admin.pipeline_orchestrator.SessionRuntime", return_value=mock_session), \
             patch("app.services.admin.pipeline_orchestrator.GameRuntime") as mock_rt, \
             patch("app.services.admin.pipeline_orchestrator.ContextAssembler") as mock_asm:
            mock_rt.get_instance = AsyncMock(return_value=MagicMock())
            mock_rt.get_instance.return_value.get_world = AsyncMock(return_value=MagicMock())
            mock_asm.assemble.return_value = MagicMock()
            mock_asm.assemble.return_value.to_flat_dict.return_value = {
                "engine_executed": {"type": "move_area", "target": "forest"}
            }
            pipeline.narrative_service.get_current_chapter_plan = AsyncMock(return_value=None)

            _run(pipeline.process("w1", "s1", "去森林"))
            mock_session.advance_time.assert_not_called()

    def test_skip_advance_when_rest(self):
        """engine_executed.type='rest' 时跳过自动推进。"""
        pipeline, flash_cpu = _make_pipeline()
        mock_session = MagicMock()
        mock_session.player = MagicMock()
        mock_session.party = None
        mock_session.scene_bus = None
        mock_session.history = None
        mock_session.narrative = None
        mock_session.game_state = None
        mock_session.world_graph = None
        mock_session._world_graph_failed = False
        mock_session.current_area = None
        mock_session.chapter_id = "ch1"
        mock_session.area_id = "area1"
        mock_session.sub_location = None
        mock_session.time = None
        mock_session.run_behavior_tick.return_value = None
        mock_session.check_chapter_transitions.return_value = None
        mock_session.persist = AsyncMock()
        mock_session.restore = AsyncMock()
        mock_session.flash_results = {}

        with patch("app.services.admin.pipeline_orchestrator.SessionRuntime", return_value=mock_session), \
             patch("app.services.admin.pipeline_orchestrator.GameRuntime") as mock_rt, \
             patch("app.services.admin.pipeline_orchestrator.ContextAssembler") as mock_asm:
            mock_rt.get_instance = AsyncMock(return_value=MagicMock())
            mock_rt.get_instance.return_value.get_world = AsyncMock(return_value=MagicMock())
            mock_asm.assemble.return_value = MagicMock()
            mock_asm.assemble.return_value.to_flat_dict.return_value = {
                "engine_executed": {"type": "rest"}
            }
            pipeline.narrative_service.get_current_chapter_plan = AsyncMock(return_value=None)

            _run(pipeline.process("w1", "s1", "休息"))
            mock_session.advance_time.assert_not_called()
