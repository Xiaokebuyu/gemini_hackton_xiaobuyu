from types import SimpleNamespace
import sys
import types

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

from app.services.admin.pipeline_orchestrator import PipelineOrchestrator


def _orchestrator() -> PipelineOrchestrator:
    return PipelineOrchestrator(
        flash_cpu=object(),
        party_service=object(),
        narrative_service=object(),
        graph_store=object(),
        teammate_response_service=object(),
        session_history_manager=object(),
        character_store=object(),
        state_manager=object(),
        world_runtime=object(),
    )


def test_build_teammate_context_injects_runtime_and_round_data():
    orchestrator = _orchestrator()
    session = SimpleNamespace(
        world_id="world_1",
        chapter_id="chapter_1",
        area_id="guild_area",
        game_state=SimpleNamespace(combat_id="combat_1"),
        history=SimpleNamespace(
            get_last_teammate_responses=lambda: [
                {"character_id": "priestess", "name": "女神官", "response": "我在。"}
            ]
        ),
    )
    base_context = {
        "location_context": {"location_name": "公会大厅"},
        "dynamic_state": {},
    }
    agentic_result = SimpleNamespace(
        tool_calls=[
            SimpleNamespace(name="npc_dialogue", success=True, args={"npc_id": "guild_girl"}),
        ]
    )

    result = orchestrator._build_teammate_context(
        session=session,
        base_context=base_context,
        player_input="我们先观察一下。",
        gm_narration="你们走进了公会大厅。",
        world_state_update={"events_newly_available": []},
        agentic_result=agentic_result,
    )

    assert result["world_id"] == "world_1"
    assert result["chapter_id"] == "chapter_1"
    assert result["area_id"] == "guild_area"
    assert result["location"]["location_name"] == "公会大厅"
    assert result["gm_narration_full"] == "你们走进了公会大厅。"
    assert result["combat_active"] is True
    assert result["_runtime_session"] is session
    assert result["last_teammate_responses"][0]["character_id"] == "priestess"
    assert len(result["this_round_tools"]) == 1
    assert result["this_round_tools"][0]["name"] == "npc_dialogue"


def test_build_teammate_context_filters_heavy_tool_summaries():
    orchestrator = _orchestrator()
    session = SimpleNamespace(
        world_id="world_1",
        chapter_id="chapter_1",
        area_id="guild_area",
        game_state=SimpleNamespace(combat_id=None),
        history=None,
    )
    agentic_result = SimpleNamespace(
        tool_calls=[
            SimpleNamespace(name="recall_memory", success=True, args={"seeds": ["a"]}),
            SimpleNamespace(name="generate_scene_image", success=True, args={"style": "anime"}),
            SimpleNamespace(name="choose_combat_action", success=False, args={"action_id": "atk"}),
        ]
    )

    result = orchestrator._build_teammate_context(
        session=session,
        base_context={"location": {"location_name": "街道"}},
        player_input="继续前进",
        gm_narration="街道空无一人。",
        world_state_update=None,
        agentic_result=agentic_result,
    )

    assert "this_round_tools" in result
    assert len(result["this_round_tools"]) == 1
    assert result["this_round_tools"][0]["name"] == "choose_combat_action"
    assert result["combat_active"] is False
