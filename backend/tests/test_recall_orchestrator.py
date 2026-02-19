"""Tests for RecallOrchestrator location-scope loading (F19)."""

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock

def _install_mcp_stubs() -> None:
    """Install lightweight MCP stubs for test environments without mcp package."""
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

from app.models.graph import GraphData
from app.services.admin.recall_orchestrator import RecallOrchestrator


def _run(coro):
    return asyncio.run(coro)


async def _empty_character_ids(_: str) -> set:
    return set()


async def _empty_area_chapter_map(_: str) -> dict:
    return {}


def _build_orchestrator() -> tuple[RecallOrchestrator, MagicMock]:
    graph_store = MagicMock()
    graph_store.load_graph_v2 = AsyncMock(return_value=GraphData(nodes=[], edges=[]))
    graph_store.get_all_dispositions = AsyncMock(return_value={})
    orchestrator = RecallOrchestrator(
        graph_store=graph_store,
        get_character_id_set=_empty_character_ids,
        get_area_chapter_map=_empty_area_chapter_map,
    )
    return orchestrator, graph_store


def test_recall_includes_location_scope_when_provided():
    orchestrator, graph_store = _build_orchestrator()

    _run(
        orchestrator.recall(
            world_id="world_1",
            character_id="player",
            seed_nodes=["smithy"],
            intent_type="enter_sublocation",
            chapter_id="chapter_1",
            area_id="town_square",
            location_id="smithy",
        )
    )

    loaded_scopes = [
        call.args[1]
        for call in graph_store.load_graph_v2.call_args_list
        if len(call.args) >= 2
    ]
    assert any(
        scope.scope_type == "location"
        and scope.chapter_id == "chapter_1"
        and scope.area_id == "town_square"
        and scope.location_id == "smithy"
        for scope in loaded_scopes
    )


def test_recall_v4_includes_location_scope_when_provided():
    orchestrator, graph_store = _build_orchestrator()

    _run(
        orchestrator.recall_v4(
            world_id="world_1",
            character_id="player",
            seed_nodes=["smithy"],
            intent_type="enter_sublocation",
            chapter_id="chapter_1",
            area_id="town_square",
            location_id="smithy",
        )
    )

    loaded_scopes = [
        call.args[1]
        for call in graph_store.load_graph_v2.call_args_list
        if len(call.args) >= 2
    ]
    assert any(
        scope.scope_type == "location"
        and scope.chapter_id == "chapter_1"
        and scope.area_id == "town_square"
        and scope.location_id == "smithy"
        for scope in loaded_scopes
    )
