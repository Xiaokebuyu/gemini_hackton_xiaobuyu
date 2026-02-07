"""
Tests for teammate response policy and round flow.
"""
from unittest.mock import AsyncMock

import pytest

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


@pytest.mark.asyncio
async def test_process_round_broadcasts_to_all_before_decision():
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

    result = await service.process_round(
        party=party,
        player_input="我们接下来怎么办？",
        gm_response="你们站在岔路口。",
        context={"world_id": "world_1"},
    )

    assert service._inject_round_to_instance.await_count == 2
    assert service._generate_single_response.await_count == 1
    assert len(result.responses) == 2
    assert result.responding_count == 1
    skipped = [r for r in result.responses if r.character_id == "lizardman"][0]
    assert skipped.response is None
    assert skipped.model_used == "skip"
