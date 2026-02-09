import pytest

from app.models.admin_protocol import CoordinatorResponse
from app.routers.game_v2 import (
    AddTeammateRequest,
    CreatePartyRequest,
    PlayerInputRequest,
    add_teammate,
    create_party,
    get_agentic_trace_viewer,
    get_party_info,
    process_input_v2,
    remove_teammate,
)


class FakeCoordinator:
    def __init__(self) -> None:
        self.party_members = []
        self.v3_called = False

    async def process_player_input_v2(
        self,
        world_id: str,
        session_id: str,
        player_input: str,
        is_private: bool = False,
        private_target: str | None = None,
    ):
        return CoordinatorResponse(
            narration=f"ok:{player_input}",
            speaker="GM",
            teammate_responses=[],
            available_actions=[],
            state_delta=None,
            metadata={"world_id": world_id, "session_id": session_id},
        )

    async def process_player_input_v3(
        self,
        world_id: str,
        session_id: str,
        player_input: str,
        is_private: bool = False,
        private_target: str | None = None,
    ):
        self.v3_called = True
        return CoordinatorResponse(
            narration=f"v3:{player_input}",
            speaker="GM",
            teammate_responses=[],
            available_actions=[],
            state_delta=None,
            metadata={"world_id": world_id, "session_id": session_id},
        )

    async def create_party(self, world_id: str, session_id: str, leader_id: str = "player"):
        return {
            "party_id": "party_1",
            "leader_id": leader_id,
            "members": [],
        }

    async def get_party_info(self, world_id: str, session_id: str):
        return {
            "has_party": True,
            "party_id": "party_1",
            "leader_id": "player",
            "current_location": None,
            "members": list(self.party_members),
        }

    async def add_teammate(
        self,
        world_id: str,
        session_id: str,
        character_id: str,
        name: str,
        role: str = "support",
        personality: str = "",
        response_tendency: float = 0.5,
    ):
        member = {
            "character_id": character_id,
            "name": name,
            "role": role,
        }
        self.party_members.append(member)
        return {"success": True, **member}

    async def remove_teammate(self, world_id: str, session_id: str, character_id: str):
        self.party_members = [m for m in self.party_members if m["character_id"] != character_id]
        return {"success": True, "character_id": character_id}


class FakeCoordinatorV2Only:
    async def process_player_input_v2(
        self,
        world_id: str,
        session_id: str,
        player_input: str,
        is_private: bool = False,
        private_target: str | None = None,
    ):
        return CoordinatorResponse(
            narration=f"v2:{player_input}",
            speaker="GM",
            teammate_responses=[],
            available_actions=[],
            state_delta=None,
            metadata={"world_id": world_id, "session_id": session_id},
        )


@pytest.mark.asyncio
async def test_game_v2_input_route():
    fake = FakeCoordinator()
    response = await process_input_v2(
        world_id="test_world",
        session_id="test_session",
        payload=PlayerInputRequest(input="观察周围"),
        coordinator=fake,
    )
    assert response.narration == "v3:观察周围"
    assert response.speaker == "GM"
    assert fake.v3_called is True


@pytest.mark.asyncio
async def test_game_v2_input_route_fallback_v2():
    fake = FakeCoordinatorV2Only()
    response = await process_input_v2(
        world_id="test_world",
        session_id="test_session",
        payload=PlayerInputRequest(input="观察周围"),
        coordinator=fake,
    )
    assert response.narration == "v2:观察周围"


@pytest.mark.asyncio
async def test_game_v2_party_routes():
    fake = FakeCoordinator()
    world_id = "test_world"
    session_id = "test_session"

    create_resp = await create_party(
        world_id=world_id,
        session_id=session_id,
        payload=CreatePartyRequest(leader_id="player"),
        coordinator=fake,
    )
    assert create_resp["party_id"] == "party_1"

    add_resp = await add_teammate(
        world_id=world_id,
        session_id=session_id,
        payload=AddTeammateRequest(character_id="ally_1", name="Ally", role="support"),
        coordinator=fake,
    )
    assert add_resp["success"] is True

    info_resp = await get_party_info(
        world_id=world_id,
        session_id=session_id,
        coordinator=fake,
    )
    assert info_resp["has_party"] is True
    assert len(info_resp["members"]) == 1

    remove_resp = await remove_teammate(
        world_id=world_id,
        session_id=session_id,
        character_id="ally_1",
        coordinator=fake,
    )
    assert remove_resp["success"] is True


@pytest.mark.asyncio
async def test_agentic_trace_viewer_route_returns_html():
    response = await get_agentic_trace_viewer()
    assert response.status_code == 200
    html = response.body.decode("utf-8")
    assert "GM Agentic Trace Viewer" in html
