from fastapi.testclient import TestClient

from app.dependencies import get_coordinator
from app.main import app
from app.models.admin_protocol import CoordinatorResponse


class FakeCoordinator:
    def __init__(self) -> None:
        self.party_members = []

    async def process_player_input_v2(self, world_id: str, session_id: str, player_input: str):
        return CoordinatorResponse(
            narration=f"ok:{player_input}",
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


def test_game_v2_input_route():
    fake = FakeCoordinator()
    app.dependency_overrides[get_coordinator] = lambda: fake
    client = TestClient(app)
    try:
        response = client.post(
            "/api/game/test_world/sessions/test_session/input",
            json={"input": "观察周围"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["narration"] == "ok:观察周围"
        assert payload["speaker"] == "GM"
    finally:
        app.dependency_overrides.clear()


def test_game_v2_party_routes():
    fake = FakeCoordinator()
    app.dependency_overrides[get_coordinator] = lambda: fake
    client = TestClient(app)
    try:
        create_resp = client.post(
            "/api/game/test_world/sessions/test_session/party",
            json={"leader_id": "player"},
        )
        assert create_resp.status_code == 200

        add_resp = client.post(
            "/api/game/test_world/sessions/test_session/party/add",
            json={
                "character_id": "ally_1",
                "name": "Ally",
                "role": "support",
            },
        )
        assert add_resp.status_code == 200
        assert add_resp.json()["success"] is True

        info_resp = client.get(
            "/api/game/test_world/sessions/test_session/party",
        )
        assert info_resp.status_code == 200
        info_payload = info_resp.json()
        assert info_payload["has_party"] is True
        assert len(info_payload["members"]) == 1

        remove_resp = client.delete(
            "/api/game/test_world/sessions/test_session/party/ally_1",
        )
        assert remove_resp.status_code == 200
        assert remove_resp.json()["success"] is True
    finally:
        app.dependency_overrides.clear()
