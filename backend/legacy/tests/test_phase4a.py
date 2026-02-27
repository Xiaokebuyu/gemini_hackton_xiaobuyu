"""Phase 4a 基础设施测试 — SceneBus 成员 + FEELING_MAP + RoleRegistry + recall_for_role。"""

from __future__ import annotations

import asyncio
import inspect
import sys
import types
from typing import Any, Dict, List, Optional, Set
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

from app.world.scene import BusEntry, BusEntryType, SceneBus


# =========================================================================
# SceneBus 成员模型
# =========================================================================


class TestSceneBusMembers:
    def test_default_no_members(self):
        bus = SceneBus(area_id="tavern")
        assert bus.permanent_members == set()
        assert bus.active_members == set()

    def test_init_with_permanent_members(self):
        bus = SceneBus(area_id="tavern", permanent_members={"player", "warrior"})
        assert bus.permanent_members == {"player", "warrior"}
        assert bus.active_members == set()

    def test_contact_adds_active_member(self):
        bus = SceneBus(area_id="tavern")
        bus.contact("merchant_01")
        assert "merchant_01" in bus.active_members

    def test_contact_idempotent(self):
        bus = SceneBus(area_id="tavern")
        bus.contact("npc_01")
        bus.contact("npc_01")
        assert bus.active_members == {"npc_01"}

    def test_end_contact_removes_active_member(self):
        bus = SceneBus(area_id="tavern")
        bus.contact("merchant_01")
        bus.end_contact("merchant_01")
        assert "merchant_01" not in bus.active_members

    def test_end_contact_nonexistent_no_error(self):
        bus = SceneBus(area_id="tavern")
        bus.end_contact("nobody")  # discard, 不应报错

    def test_is_member_checks_both_sets(self):
        bus = SceneBus(area_id="tavern", permanent_members={"player"})
        bus.contact("merchant_01")
        assert bus.is_member("player")
        assert bus.is_member("merchant_01")
        assert not bus.is_member("nobody")

    def test_get_members_union(self):
        bus = SceneBus(area_id="tavern", permanent_members={"player", "tm1"})
        bus.contact("npc_01")
        assert bus.get_members() == {"player", "tm1", "npc_01"}

    def test_reset_scene_clears_active_keeps_permanent(self):
        bus = SceneBus(area_id="tavern", permanent_members={"player"})
        bus.contact("merchant_01")
        bus.publish(BusEntry(actor="player", type=BusEntryType.ACTION, content="hi"))
        bus.reset_scene("forest")
        assert bus.area_id == "forest"
        assert bus.sub_location is None
        assert bus.permanent_members == {"player"}
        assert bus.active_members == set()
        assert len(bus.entries) == 0

    def test_clear_does_not_clear_active_members(self):
        bus = SceneBus(area_id="tavern")
        bus.contact("merchant_01")
        bus.publish(BusEntry(actor="player", type=BusEntryType.ACTION, content="hi"))
        bus.clear()
        assert len(bus.entries) == 0
        assert "merchant_01" in bus.active_members

    def test_serialization_round_trip_with_members(self):
        bus = SceneBus(area_id="tavern", permanent_members={"player", "warrior"})
        bus.contact("merchant_01")
        bus.publish(BusEntry(actor="player", type=BusEntryType.ACTION, content="hi"))
        data = bus.to_serializable()
        assert "permanent_members" in data
        assert "active_members" in data

        restored = SceneBus.from_serializable(data)
        assert restored.permanent_members == {"player", "warrior"}
        assert restored.active_members == {"merchant_01"}
        assert len(restored.entries) == 1

    def test_from_serializable_backward_compat(self):
        """旧格式（无 permanent_members/active_members）也能恢复。"""
        data = {"area_id": "tavern", "round_number": 1, "entries": []}
        bus = SceneBus.from_serializable(data)
        assert bus.permanent_members == set()
        assert bus.active_members == set()


# =========================================================================
# FEELING_MAP
# =========================================================================


class TestFeelingMap:
    def setup_method(self):
        from app.agentic.immersive_tools import FEELING_MAP
        self.fmap = FEELING_MAP

    def test_24_entries(self):
        assert len(self.fmap) == 24

    def test_all_dimensions(self):
        dims = {k[0] for k in self.fmap}
        assert dims == {"approval", "trust", "fear", "romance"}

    def test_all_levels(self):
        levels = {k[1] for k in self.fmap}
        assert levels == {"slight", "moderate", "strong"}

    def test_polarity_coverage(self):
        polarities = {k[2] for k in self.fmap}
        assert polarities == {True, False}

    def test_positive_entries_have_positive_values(self):
        for (dim, level, is_positive), deltas in self.fmap.items():
            for v in deltas.values():
                if is_positive:
                    assert v > 0, f"({dim}, {level}, True) should be positive"
                else:
                    assert v < 0, f"({dim}, {level}, False) should be negative"

    def test_strong_greater_than_moderate_greater_than_slight(self):
        for dim in ("approval", "trust", "fear", "romance"):
            s = abs(list(self.fmap[(dim, "slight", True)].values())[0])
            m = abs(list(self.fmap[(dim, "moderate", True)].values())[0])
            st = abs(list(self.fmap[(dim, "strong", True)].values())[0])
            assert s < m < st, f"{dim}: {s} < {m} < {st} should hold"


# =========================================================================
# RoleRegistry
# =========================================================================


class TestRoleRegistry:
    def setup_method(self):
        from app.agentic.immersive_tools import AgenticContext
        from app.agentic.role_registry import RoleRegistry
        self.RoleRegistry = RoleRegistry
        self.AgenticContext = AgenticContext

    def _make_ctx(self, agent_id: str, role: str = "npc") -> Any:
        return self.AgenticContext(session=None, agent_id=agent_id, role=role, scene_bus=None)

    def test_gm_gets_base_and_gm_tools(self):
        tools = self.RoleRegistry.get_tools(role="gm", ctx=self._make_ctx("gm_01", "gm"))
        names = {t.__name__ for t in tools}
        assert "react_to_interaction" in names
        assert "generate_scene_image" in names
        assert "complete_event" in names
        assert "express_need" not in names

    def test_npc_gets_only_base_tools(self):
        tools = self.RoleRegistry.get_tools(role="npc", ctx=self._make_ctx("npc_01"))
        names = {t.__name__ for t in tools}
        assert "react_to_interaction" in names
        assert "share_thought" in names
        assert "generate_scene_image" not in names
        assert "evaluate_offer" not in names

    def test_npc_with_merchant_trait_gets_trade_tools(self):
        tools = self.RoleRegistry.get_tools(
            role="npc", traits={"merchant"}, ctx=self._make_ctx("merchant_01"),
        )
        names = {t.__name__ for t in tools}
        assert "evaluate_offer" in names
        assert "propose_deal" in names
        assert "adjust_my_prices" in names
        assert "grant_passage" not in names

    def test_teammate_gets_base_and_teammate_tools(self):
        tools = self.RoleRegistry.get_tools(role="teammate", ctx=self._make_ctx("tm_01", "teammate"))
        names = {t.__name__ for t in tools}
        assert "react_to_interaction" in names
        assert "express_need" in names
        assert "generate_scene_image" not in names

    def test_binding_strips_internal_params(self):
        tools = self.RoleRegistry.get_tools(role="npc", ctx=self._make_ctx("npc_01"))
        for tool in tools:
            assert "ctx" not in tool.__annotations__, f"{tool.__name__} leaks ctx"
            sig = inspect.signature(tool)
            assert "ctx" not in sig.parameters, f"{tool.__name__} leaks ctx in signature"

    def test_bound_tool_has_name_and_doc(self):
        tools = self.RoleRegistry.get_tools(role="npc", ctx=self._make_ctx("npc_01"))
        react = next(t for t in tools if t.__name__ == "react_to_interaction")
        assert react.__name__ == "react_to_interaction"
        assert react.__doc__  # 有描述


# =========================================================================
# RecallOrchestrator tests removed in L3 M3 — replaced by WorldGraphRecallOrchestrator
# See tests/test_world_graph_recall.py
# =========================================================================
