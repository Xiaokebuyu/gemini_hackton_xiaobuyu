import pytest
from fastapi import HTTPException

from app.models.game import PlayerInputRequest
from app.routers.game_v2 import (
    CreateGameSessionRequest,
    process_input_v2,
    start_session,
)


class _CoordinatorRaisesValueError:
    async def process_player_input_v2(self, world_id: str, session_id: str, player_input: str):
        raise ValueError("世界未初始化地图数据")

    async def start_session(
        self,
        world_id: str,
        session_id=None,
        participants=None,
        known_characters=None,
        character_locations=None,
        starting_location=None,
        starting_time=None,
    ):
        raise ValueError("世界未初始化地图数据")


@pytest.mark.asyncio
async def test_process_input_v2_maps_value_error_to_400():
    coordinator = _CoordinatorRaisesValueError()
    with pytest.raises(HTTPException) as exc_info:
        await process_input_v2(
            world_id="test_world",
            session_id="test_session",
            payload=PlayerInputRequest(input="观察周围"),
            coordinator=coordinator,
        )
    assert exc_info.value.status_code == 400
    assert "未初始化地图数据" in exc_info.value.detail


@pytest.mark.asyncio
async def test_start_session_maps_value_error_to_400():
    coordinator = _CoordinatorRaisesValueError()
    with pytest.raises(HTTPException) as exc_info:
        await start_session(
            world_id="test_world",
            payload=CreateGameSessionRequest(user_id="player-001"),
            coordinator=coordinator,
        )
    assert exc_info.value.status_code == 400
    assert "未初始化地图数据" in exc_info.value.detail
