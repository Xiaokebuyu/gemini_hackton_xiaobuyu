"""
Tests for teammate response policy and round flow.
"""
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

from app.models.party import (
    Party,
    PartyMember,
    TeammateResponseDecision,
    TeammateResponseResult,
    TeammateRole,
)
from app.services.teammate_response_service import TeammateResponseService


def _build_party() -> Party:
    return Party(
        party_id="party_1",
        world_id="world_1",
        session_id="session_1",
        leader_id="player",
        members=[
            PartyMember(
                character_id="priestess",
                name="女神官",
                role=TeammateRole.HEALER,
                response_tendency=0.7,
            ),
            PartyMember(
                character_id="lizardman",
                name="蜥蜴人僧侣",
                role=TeammateRole.WARRIOR,
                response_tendency=0.2,
            ),
        ],
    )


def test_simple_decision_is_deterministic():
    service = TeammateResponseService()
    member = PartyMember(
        character_id="silent_scout",
        name="斥候",
        role=TeammateRole.SCOUT,
        response_tendency=0.1,
    )
    player_input = "我们继续前进。"
    gm_response = "道路很安静，没有异常。"

    first = service._simple_decision(member, player_input, gm_response, context={})
    second = service._simple_decision(member, player_input, gm_response, context={})

    assert first.should_respond is False
    assert second.should_respond is False
    assert first.reason == second.reason
    assert first.priority == second.priority


def test_simple_decision_mentions_member():
    service = TeammateResponseService()
    member = PartyMember(
        character_id="priestess",
        name="女神官",
        role=TeammateRole.HEALER,
        response_tendency=0.3,
    )

    decision = service._simple_decision(
        member,
        "女神官，你觉得要不要先治疗？",
        "GM: 你们刚结束战斗。",
        context={},
    )
    assert decision.should_respond is True
    assert decision.priority >= 9


def test_process_round_broadcasts_to_all_before_decision():
    party = _build_party()
    service = TeammateResponseService()
    service.instance_manager = object()  # 开启实例广播分支

    service._inject_round_to_instance = AsyncMock(
        side_effect=lambda member, world_id, player_input, gm_response: f"history:{member.character_id}"
    )
    service.decide_responses = AsyncMock(
        return_value=[
            TeammateResponseDecision(
                character_id="priestess",
                should_respond=True,
                reason="玩家提问",
                priority=8,
            ),
            TeammateResponseDecision(
                character_id="lizardman",
                should_respond=False,
                reason="话题关联较低",
                priority=1,
            ),
        ]
    )

    async def _fake_generate(**kwargs):
        member = kwargs["member"]
        return TeammateResponseResult(
            character_id=member.character_id,
            name=member.name,
            response="收到。",
            reaction="点头",
            model_used="flash",
        )

    service._generate_single_response = AsyncMock(side_effect=_fake_generate)

    result = asyncio.run(
        service.process_round(
            party=party,
            player_input="我们接下来怎么办？",
            gm_response="你们站在岔路口。",
            context={"world_id": "world_1"},
        )
    )

    assert service._inject_round_to_instance.await_count == 2
    assert service._generate_single_response.await_count == 1
    assert len(result.responses) == 2
    assert result.responding_count == 1
    skipped = [r for r in result.responses if r.character_id == "lizardman"][0]
    assert skipped.response is None
    assert skipped.model_used == "skip"


def test_process_round_stream_private_mode_skips_non_target():
    party = _build_party()
    service = TeammateResponseService()

    service._inject_last_teammate_responses = AsyncMock(return_value=None)
    service._inject_round_histories = AsyncMock(
        return_value={"priestess": "history:priestess"}
    )
    service.decide_responses = AsyncMock(
        return_value=[
            TeammateResponseDecision(
                character_id="priestess",
                should_respond=True,
                reason="被私聊",
                priority=10,
            )
        ]
    )
    service._generate_single_response_core = AsyncMock(
        return_value=(
            TeammateResponseResult(
                character_id="priestess",
                name="女神官",
                response="我在听。",
                reaction="轻声回应",
                model_used="flash",
            ),
            [],
        )
    )

    async def _collect():
        return [
            event
            async for event in service.process_round_stream(
                party=party,
                player_input="女神官，我想和你单独聊聊。",
                gm_response="你们暂时脱离了队伍视线。",
                context={
                    "world_id": "world_1",
                    "is_private": True,
                    "private_target": "priestess",
                },
            )
        ]

    events = asyncio.run(_collect())

    event_types = [event["type"] for event in events]
    assert "teammate_start" in event_types
    assert "teammate_chunk" in event_types
    assert "teammate_end" in event_types
    skipped = [
        event for event in events
        if event["type"] == "teammate_skip" and event["character_id"] == "lizardman"
    ]
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "私密对话"


def test_generate_response_payload_falls_back_to_simple():
    service = TeammateResponseService()
    member = PartyMember(
        character_id="priestess",
        name="女神官",
        role=TeammateRole.HEALER,
        response_tendency=0.6,
    )

    service._run_agentic_generation_payload = AsyncMock(side_effect=RuntimeError("boom"))
    service._run_simple_generation_payload = AsyncMock(
        return_value={
            "response": "收到。",
            "reaction": "点头",
            "updated_mood": "steady",
        }
    )

    parsed, tool_events = asyncio.run(
        service._generate_response_payload(
            member=member,
            context={},
            prompt="test",
            model="gemini-3-flash-preview",
            thinking_level="low",
        )
    )

    assert parsed is not None
    assert parsed["response"] == "收到。"
    assert tool_events == []


def test_process_round_stream_emits_tool_call_events():
    party = _build_party()
    service = TeammateResponseService()

    service._inject_last_teammate_responses = AsyncMock(return_value=None)
    service._inject_round_histories = AsyncMock(return_value={"priestess": "history"})
    service.decide_responses = AsyncMock(
        return_value=[
            TeammateResponseDecision(
                character_id="priestess",
                should_respond=True,
                reason="需要回应",
                priority=8,
            ),
            TeammateResponseDecision(
                character_id="lizardman",
                should_respond=False,
                reason="无关话题",
                priority=1,
            ),
        ]
    )
    service._generate_single_response_core = AsyncMock(
        return_value=(
            TeammateResponseResult(
                character_id="priestess",
                name="女神官",
                response="我会先检查伤势。",
                reaction="认真观察",
                model_used="flash",
            ),
            [
                {
                    "type": "teammate_tool_call",
                    "character_id": "priestess",
                    "tool_name": "recall_my_memory",
                    "success": True,
                }
            ],
        )
    )

    async def _collect():
        return [
            event
            async for event in service.process_round_stream(
                party=party,
                player_input="这里很危险。",
                gm_response="洞窟里传来低吼。",
                context={"world_id": "world_1"},
            )
        ]

    events = asyncio.run(_collect())

    tool_idx = next(
        idx for idx, event in enumerate(events)
        if event.get("type") == "teammate_tool_call"
    )
    end_idx = next(
        idx for idx, event in enumerate(events)
        if event.get("type") == "teammate_end" and event.get("character_id") == "priestess"
    )
    assert tool_idx < end_idx
