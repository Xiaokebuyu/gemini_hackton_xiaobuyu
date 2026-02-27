import pytest

from app.routers.game_v2 import list_sessions


class _FakeCoordinator:
    async def list_recoverable_sessions(self, world_id: str, user_id: str, limit: int = 20):
        assert world_id == "test_world"
        assert user_id == "player-001"
        assert limit == 2
        return [
            {
                "session_id": "sess_abc123",
                "world_id": "test_world",
                "status": "idle",
                "updated_at": "2026-02-06T17:30:00",
                "participants": ["player-001"],
                "player_location": "frontier_town",
                "chapter_id": "chapter_1",
                "sub_location": None,
            }
        ]


@pytest.mark.asyncio
async def test_list_sessions_returns_recoverable_sessions():
    response = await list_sessions(
        world_id="test_world",
        user_id="player-001",
        limit=2,
        coordinator=_FakeCoordinator(),
    )

    assert response.world_id == "test_world"
    assert response.user_id == "player-001"
    assert len(response.sessions) == 1
    assert response.sessions[0].session_id == "sess_abc123"
    assert response.sessions[0].player_location == "frontier_town"
