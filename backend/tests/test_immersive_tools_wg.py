"""Tests for immersive tools WorldGraph paths (L3 M2).

Covers: form_impression, create_memory, recall_experience
through SessionRuntime memory facade contracts.
"""

import asyncio
import sys
import types
import uuid
from typing import Any, Dict, List, Optional


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

from app.world.immersive_tools import (
    AgenticContext,
    create_memory,
    form_impression,
    recall_experience,
)
from app.world.models import WorldNode
from app.world.world_graph import WorldGraph


def _run(coro):
    return asyncio.run(coro)


class _FakeSession:
    def __init__(self, world_graph: Optional[WorldGraph], recall_records: Optional[List[Dict[str, Any]]] = None):
        self.world_graph = world_graph
        self.recall_records = recall_records or []
        self.last_recall_kwargs: Dict[str, Any] = {}

    async def recall(self, **kwargs):
        self.last_recall_kwargs = kwargs
        return list(self.recall_records)

    def record_memory(self, **kwargs):
        if self.world_graph is None:
            raise RuntimeError("WorldGraph unavailable")

        owner_id = kwargs["owner_id"]
        memory_type = kwargs["memory_type"]
        node_id = kwargs.get("node_id") or f"{memory_type}_{uuid.uuid4().hex[:8]}"
        name = kwargs["name"]
        importance = kwargs["importance"]
        summary = kwargs["summary"]
        props = dict(kwargs)
        props.pop("owner_id", None)
        props.pop("memory_type", None)
        props.pop("name", None)
        props.pop("importance", None)
        props.pop("role", None)
        props["owner"] = owner_id
        props["summary"] = summary

        self.world_graph.add_node(
            WorldNode(
                id=node_id,
                type=memory_type,
                name=name,
                importance=importance,
                properties=props,
            )
        )
        self.world_graph.add_edge(
            owner_id,
            node_id,
            "has_memory",
            key=f"edge_{owner_id}_has_{node_id}",
        )
        return node_id


def _make_ctx(world_graph=None, agent_id="npc_1", role="npc", recall_records=None):
    session = _FakeSession(world_graph=world_graph, recall_records=recall_records)
    return AgenticContext(
        session=session,
        agent_id=agent_id,
        role=role,
        scene_bus=None,
        world_id="world_1",
        chapter_id="ch_1",
        area_id="area_1",
        location_id="loc_1",
        world_graph=world_graph,
    )


# =========================================================================
# form_impression tests
# =========================================================================


def test_form_impression_world_graph():
    wg = WorldGraph()
    wg.add_node(WorldNode(id="npc_1", type="npc", name="TestNPC"))

    ctx = _make_ctx(world_graph=wg, agent_id="npc_1")
    result = _run(
        form_impression(
            ctx=ctx,
            about="player",
            impression="Seems trustworthy",
            significance="high",
        )
    )

    assert result["success"] is True
    node_id = result["node_id"]
    node = wg.get_node(node_id)
    assert node is not None
    assert node.type == "impression"
    assert node.properties["owner"] == "npc_1"
    assert node.properties["about"] == "player"
    assert node.importance == 0.8


def test_form_impression_stub_without_session():
    ctx = _make_ctx(world_graph=None)
    ctx.session = None
    result = _run(form_impression(ctx=ctx, about="player", impression="Seems nice"))
    assert result["success"] is True
    assert result.get("stub") is True


# =========================================================================
# create_memory tests
# =========================================================================


def test_create_memory_world_graph():
    wg = WorldGraph()
    wg.add_node(WorldNode(id="area_1", type="area", name="TestArea"))

    ctx = _make_ctx(world_graph=wg, agent_id="npc_1", role="gm")
    result = _run(
        create_memory(
            ctx=ctx,
            content="A mysterious stranger arrived",
            importance=0.7,
            scope="area",
        )
    )

    assert result["success"] is True
    node_id = result["node_id"]
    node = wg.get_node(node_id)
    assert node is not None
    assert node.type == "memory"
    assert node.properties["owner"] == "area_1"
    assert node.properties["content"] == "A mysterious stranger arrived"


def test_create_memory_empty_content():
    ctx = _make_ctx(world_graph=None, role="gm")
    result = _run(create_memory(ctx=ctx, content="", importance=0.5))
    assert result["success"] is False
    assert "empty" in result["error"].lower()


def test_create_memory_owner_player():
    wg = WorldGraph()
    wg.add_node(WorldNode(id="player", type="player", name="Player"))

    ctx = _make_ctx(world_graph=wg, role="gm")
    result = _run(
        create_memory(
            ctx=ctx,
            content="Player character memory",
            importance=0.5,
            scope="character",
        )
    )
    node = wg.get_node(result["node_id"])
    assert node is not None
    assert node.properties["owner"] == "player"


def test_create_memory_owner_area():
    wg = WorldGraph()
    wg.add_node(WorldNode(id="area_1", type="area", name="TestArea"))

    ctx = _make_ctx(world_graph=wg, role="gm")
    ctx.area_id = "area_1"
    result = _run(
        create_memory(
            ctx=ctx,
            content="Something happened in this area",
            importance=0.6,
            scope="area",
        )
    )
    node = wg.get_node(result["node_id"])
    assert node is not None
    assert node.properties["owner"] == "area_1"


# =========================================================================
# recall_experience tests
# =========================================================================


def test_recall_experience_with_session_recall():
    wg = WorldGraph()
    wg.add_node(WorldNode(id="npc_1", type="npc", name="TestNPC"))
    recall_records = [
        {"node_id": "eg_mem", "name": "Battle of Dawn", "summary": "A fierce dawn battle", "relevance": 0.9},
        {"node_id": "topic_old_friend", "name": "Old Friend", "summary": "", "relevance": 0.7},
    ]
    ctx = _make_ctx(world_graph=wg, agent_id="npc_1", role="npc", recall_records=recall_records)

    result = _run(recall_experience(ctx=ctx, seeds=["npc_1"]))
    assert result["success"] is True
    assert len(result["memories"]) == 2
    assert result["memories"][0]["concept"] == "Battle of Dawn"
    assert ctx.session.last_recall_kwargs["role"] == "npc"
    assert ctx.session.last_recall_kwargs["actor_id"] == "npc_1"


def test_recall_experience_stub_without_session():
    ctx = _make_ctx(world_graph=None)
    ctx.session = None
    result = _run(recall_experience(ctx=ctx, seeds=["anything"]))
    assert result["success"] is True
    assert result.get("stub") is True
