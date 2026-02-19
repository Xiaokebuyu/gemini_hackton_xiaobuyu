"""Tests for programmatic tool filtering (Direction A — Items 1/5/6).

Uses sys.modules mocking to avoid deep import chain requiring 'mcp' package.
"""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest


# Pre-mock the 'mcp' package tree to avoid ImportError in test environment
_mcp_mock = MagicMock()
for mod_name in [
    "mcp", "mcp.client", "mcp.client.session", "mcp.client.sse",
    "mcp.client.stdio", "mcp.client.streamable_http", "mcp.types",
]:
    sys.modules.setdefault(mod_name, _mcp_mock)

from app.services.admin.v4_agentic_tools import V4AgenticToolRegistry, _ENGINE_TOOL_EXCLUSIONS
from app.services.admin.flash_cpu_service import FlashCPUService


def _make_registry(engine_executed=None):
    """Build a V4AgenticToolRegistry with mocked dependencies."""
    session = MagicMock()
    session.world_id = "test_world"
    session.session_id = "test_session"
    session.current_area = MagicMock()
    session.player_location = "town_square"
    session.chapter_id = "ch1"
    session.narrative = MagicMock()
    session.narrative.npc_interactions = {}
    session.player = MagicMock(hp=50, max_hp=100)
    session.time = MagicMock(day=1, hour=10, minute=0)
    session.world = MagicMock()

    flash_cpu = MagicMock()
    graph_store = MagicMock()

    return V4AgenticToolRegistry(
        session=session,
        flash_cpu=flash_cpu,
        graph_store=graph_store,
        engine_executed=engine_executed,
    )


def _get_tool_names(registry):
    """Get the set of tool function names from registry."""
    return {t.__name__ for t in registry.get_tools()}


class TestNoEngineExecuted:
    def test_no_engine_executed_full_tools(self):
        """engine_executed=None → 工具数不减少。"""
        reg = _make_registry(engine_executed=None)
        names = _get_tool_names(reg)
        assert "navigate" in names
        assert "enter_sublocation" in names
        assert "leave_sublocation" in names
        assert "heal_player" in names
        assert "update_time" in names


class TestMoveAreaFiltering:
    def test_move_area_removes_all_nav_tools(self):
        """engine_executed=move_area → navigate/enter_sublocation/leave_sublocation 不在列表中。"""
        reg = _make_registry(engine_executed={"type": "move_area"})
        names = _get_tool_names(reg)
        assert "navigate" not in names
        assert "enter_sublocation" not in names
        assert "leave_sublocation" not in names
        # Other tools remain
        assert "npc_dialogue" in names
        assert "heal_player" in names


class TestMoveSublocationFiltering:
    def test_move_sublocation_removes_only_enter(self):
        """engine_executed=move_sublocation → enter_sublocation 被移除，navigate/leave_sublocation 保留。"""
        reg = _make_registry(engine_executed={"type": "move_sublocation"})
        names = _get_tool_names(reg)
        assert "enter_sublocation" not in names
        assert "navigate" in names
        assert "leave_sublocation" in names


class TestLeaveFiltering:
    def test_leave_removes_leave_tool(self):
        """engine_executed=leave → leave_sublocation 不在列表中。"""
        reg = _make_registry(engine_executed={"type": "leave"})
        names = _get_tool_names(reg)
        assert "leave_sublocation" not in names
        # navigate should still be present
        assert "navigate" in names


class TestRestFiltering:
    def test_rest_removes_only_update_time(self):
        """engine_executed=rest → update_time 被移除，heal_player 保留。"""
        reg = _make_registry(engine_executed={"type": "rest"})
        names = _get_tool_names(reg)
        assert "update_time" not in names
        assert "heal_player" in names
        assert "navigate" in names


class TestTalkFiltering:
    def test_talk_removes_npc_dialogue(self):
        """F24: engine_executed=talk → npc_dialogue 被移除。"""
        reg = _make_registry(engine_executed={"type": "talk"})
        names = _get_tool_names(reg)
        assert "npc_dialogue" not in names
        # Other tools remain
        assert "navigate" in names
        assert "heal_player" in names


class TestSafetyNet:
    def test_safety_net_shortcircuit(self):
        """手动调用被排除工具 → 返回 already_executed_by_engine + 记录到 tool_calls。"""
        reg = _make_registry(engine_executed={"type": "move_area"})
        # Since navigate is filtered out of get_tools(), test via _wrap_tool_for_afc directly
        wrapped = reg._wrap_tool_for_afc(reg.navigate)
        result = asyncio.run(wrapped(destination="somewhere"))
        assert result["success"] is True
        assert result["already_executed_by_engine"] is True
        # Verify the call was recorded in tool_calls audit log
        assert len(reg.tool_calls) == 1
        assert reg.tool_calls[0].name == "navigate"
        assert reg.tool_calls[0].error == "blocked_by_engine_filter"
        assert reg.tool_calls[0].success is True


