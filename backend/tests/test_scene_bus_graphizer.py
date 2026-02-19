"""Tests for SceneBus graphization adapter (F18)."""

import asyncio
import sys
import types
from types import SimpleNamespace
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

from app.models.graph_elements import GraphizeResult
from app.services.scene_bus_graphizer import (
    build_graphize_request_from_scene_bus,
    graphize_scene_bus_round,
)
from app.world.scene_bus import BusEntry, BusEntryType, SceneBus


def _run(coro):
    return asyncio.run(coro)


def test_build_graphize_request_from_scene_bus_basic():
    bus = SceneBus(area_id="town_square", sub_location="smithy")
    bus.publish(
        BusEntry(
            actor="player",
            type=BusEntryType.ACTION,
            content="我走进铁匠铺",
            topics=["铁匠铺", "调查"],
        )
    )

    request = build_graphize_request_from_scene_bus(
        bus,
        world_id="w1",
        session_id="s1",
        game_day=3,
        current_scene="smithy",
    )

    assert request is not None
    assert request.world_id == "w1"
    assert request.npc_id == "scene_bus:s1"
    assert request.game_day == 3
    assert len(request.messages) == 1
    message = request.messages[0]
    assert message.metadata["scene_bus"] is True
    assert message.metadata["entry_type"] == "action"
    assert message.metadata["topics"] == ["铁匠铺", "调查"]


def test_graphize_scene_bus_round_calls_memory_graphizer():
    bus = SceneBus(area_id="town_square", sub_location="smithy")
    bus.publish(
        BusEntry(
            actor="engine",
            type=BusEntryType.ENGINE_RESULT,
            content="entered sub-location smithy",
            data={"tool": "enter_sublocation"},
        )
    )

    session = SimpleNamespace(
        world_id="w1",
        session_id="s1",
        chapter_id="chapter_1",
        area_id="town_square",
        player_location="town_square",
        sub_location="smithy",
        time=SimpleNamespace(day=2),
    )

    memory_graphizer = MagicMock()
    memory_graphizer.graphize = AsyncMock(
        return_value=GraphizeResult(
            success=True,
            nodes_added=2,
            edges_added=3,
            messages_processed=1,
        )
    )

    result = _run(
        graphize_scene_bus_round(
            scene_bus=bus,
            session=session,
            memory_graphizer=memory_graphizer,
        )
    )

    assert result.success is True
    assert result.nodes_added == 2
    memory_graphizer.graphize.assert_called_once()
    kwargs = memory_graphizer.graphize.call_args.kwargs
    assert kwargs["mode"] == "scene_bus"
    scope = kwargs["target_scope"]
    assert scope.scope_type == "location"
    assert scope.chapter_id == "chapter_1"
    assert scope.area_id == "town_square"
    assert scope.location_id == "smithy"


def test_graphize_scene_bus_round_missing_scope_fails_fast():
    bus = SceneBus(area_id="town_square")
    bus.publish(
        BusEntry(
            actor="player",
            type=BusEntryType.ACTION,
            content="hello",
        )
    )

    session = SimpleNamespace(
        world_id="w1",
        session_id="s1",
        chapter_id=None,
        area_id="town_square",
        player_location="town_square",
        sub_location=None,
        time=SimpleNamespace(day=1),
    )
    memory_graphizer = MagicMock()
    memory_graphizer.graphize = AsyncMock()

    result = _run(
        graphize_scene_bus_round(
            scene_bus=bus,
            session=session,
            memory_graphizer=memory_graphizer,
        )
    )

    assert result.success is False
    assert "missing chapter_id/area_id/location_id" in (result.error or "")
    memory_graphizer.graphize.assert_not_called()
