import json

import pytest

from app.combat import combat_mcp_server


class _MissingSessionStore:
    async def get_session(self, world_id: str, session_id: str):
        return None

    async def set_combat(self, world_id: str, session_id: str, combat_id: str, combat_context):
        raise AssertionError("set_combat should not be called when session is missing")


class _NeverStartEngine:
    def __init__(self) -> None:
        self.called = False

    def start_combat(self, **kwargs):
        self.called = True
        raise AssertionError("combat engine should not start when session is missing")


@pytest.mark.asyncio
async def test_start_combat_session_returns_error_when_session_missing(monkeypatch):
    store = _MissingSessionStore()
    engine = _NeverStartEngine()

    monkeypatch.setattr(combat_mcp_server, "session_store", store)
    monkeypatch.setattr(combat_mcp_server, "combat_engine", engine)

    raw = await combat_mcp_server.start_combat_session(
        world_id="world_x",
        session_id="missing_session",
        enemies=[{"type": "goblin", "level": 1}],
        player_state={
            "hp": 20,
            "max_hp": 20,
            "ac": 10,
            "attack_bonus": 2,
            "damage_dice": "1d6",
            "damage_bonus": 1,
        },
        environment={},
        allies=[],
        combat_context={},
    )
    payload = json.loads(raw)

    assert payload == {"error": "session not found"}
    assert engine.called is False
