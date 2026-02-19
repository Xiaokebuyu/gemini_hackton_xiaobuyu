from types import SimpleNamespace
import sys
import types
import asyncio
from unittest.mock import AsyncMock

if "mcp.client.session" not in sys.modules:
    mcp_module = types.ModuleType("mcp")
    client_module = types.ModuleType("mcp.client")
    session_module = types.ModuleType("mcp.client.session")
    sse_module = types.ModuleType("mcp.client.sse")
    stdio_module = types.ModuleType("mcp.client.stdio")
    stream_http_module = types.ModuleType("mcp.client.streamable_http")

    class _ClientSession:
        pass

    class _StdioServerParameters:
        def __init__(self, *args, **kwargs):
            pass

    async def _noop_async(*args, **kwargs):
        return None

    session_module.ClientSession = _ClientSession
    sse_module.sse_client = _noop_async
    stdio_module.StdioServerParameters = _StdioServerParameters
    stdio_module.stdio_client = _noop_async
    stream_http_module.streamable_http_client = _noop_async

    mcp_module.client = client_module
    client_module.session = session_module
    client_module.sse = sse_module
    client_module.stdio = stdio_module
    client_module.streamable_http = stream_http_module

    sys.modules["mcp"] = mcp_module
    sys.modules["mcp.client"] = client_module
    sys.modules["mcp.client.session"] = session_module
    sys.modules["mcp.client.sse"] = sse_module
    sys.modules["mcp.client.stdio"] = stdio_module
    sys.modules["mcp.client.streamable_http"] = stream_http_module

from app.models.party import PartyMember, TeammateRole
from app.services.admin.teammate_agentic_tools import TeammateAgenticToolRegistry


def _member() -> PartyMember:
    return PartyMember(
        character_id="priestess",
        name="女神官",
        role=TeammateRole.HEALER,
        response_tendency=0.6,
    )


def _session(combat_id=None):
    return SimpleNamespace(
        world_id="world_1",
        chapter_id="chapter_1",
        area_id="guild_area",
        sub_location="guild_hall",
        game_state=SimpleNamespace(combat_id=combat_id),
        time=SimpleNamespace(day=3),
    )


def test_update_my_disposition_rejects_self_target():
    member = _member()
    graph_store = SimpleNamespace(update_disposition=AsyncMock())
    registry = TeammateAgenticToolRegistry(
        member=member,
        session=_session(),
        graph_store=graph_store,
    )

    result = asyncio.run(
        registry.update_my_disposition(
            target_id="priestess",
            deltas={"approval": 10},
            reason="self-change",
        )
    )

    assert result["success"] is False
    assert "self" in result["error"]
    graph_store.update_disposition.assert_not_called()


def test_update_my_disposition_uses_member_as_source():
    member = _member()
    graph_store = SimpleNamespace(
        update_disposition=AsyncMock(
            return_value={
                "approval": 15,
                "trust": 8,
                "fear": 0,
                "romance": 1,
            }
        )
    )
    registry = TeammateAgenticToolRegistry(
        member=member,
        session=_session(),
        graph_store=graph_store,
    )

    result = asyncio.run(
        registry.update_my_disposition(
            target_id="player",
            deltas={"approval": 12, "trust": 5},
            reason="saved me",
        )
    )

    assert result["success"] is True
    assert result["character_id"] == "priestess"
    assert result["target_id"] == "player"
    graph_store.update_disposition.assert_awaited_once()
    call_kwargs = graph_store.update_disposition.await_args.kwargs
    assert call_kwargs["character_id"] == "priestess"
    assert call_kwargs["target_id"] == "player"
    assert call_kwargs["world_id"] == "world_1"


def test_recall_my_memory_uses_member_scope():
    member = _member()

    class _RecallResult:
        def model_dump(self):
            return {"activated_nodes": {"memory_1": 0.8}}

    recall_orchestrator = SimpleNamespace(
        recall=AsyncMock(return_value=_RecallResult())
    )
    registry = TeammateAgenticToolRegistry(
        member=member,
        session=_session(),
        recall_orchestrator=recall_orchestrator,
    )

    result = asyncio.run(registry.recall_my_memory(["哥布林", "公会"]))

    assert result["success"] is True
    assert result["character_id"] == "priestess"
    recall_orchestrator.recall.assert_awaited_once()
    call_kwargs = recall_orchestrator.recall.await_args.kwargs
    assert call_kwargs["character_id"] == "priestess"
    assert call_kwargs["world_id"] == "world_1"


def test_choose_my_combat_action_requires_active_combat():
    member = _member()
    flash_cpu = SimpleNamespace(call_combat_tool=AsyncMock())
    registry = TeammateAgenticToolRegistry(
        member=member,
        session=_session(combat_id=None),
        flash_cpu=flash_cpu,
    )

    result = asyncio.run(registry.choose_my_combat_action("attack_1"))

    assert result["success"] is False
    assert "active combat" in result["error"]
    flash_cpu.call_combat_tool.assert_not_called()


def test_choose_my_combat_action_binds_actor_id():
    member = _member()
    flash_cpu = SimpleNamespace(
        call_combat_tool=AsyncMock(return_value={"result": "ok"})
    )
    registry = TeammateAgenticToolRegistry(
        member=member,
        session=_session(combat_id="combat_123"),
        flash_cpu=flash_cpu,
    )

    result = asyncio.run(registry.choose_my_combat_action("attack_1"))

    assert result["success"] is True
    assert result["actor_id"] == "priestess"
    assert result["combat_id"] == "combat_123"
    flash_cpu.call_combat_tool.assert_awaited()
    first_call = flash_cpu.call_combat_tool.await_args_list[0]
    assert first_call.args[0] == "execute_action_for_actor"
    assert first_call.args[1]["actor_id"] == "priestess"
