import asyncio
from datetime import datetime, timezone

import pytest

from app.models.state_delta import StateDelta
from app.services.admin.state_manager import StateManager


@pytest.mark.asyncio
async def test_apply_delta_initializes_state_without_deadlock():
    manager = StateManager()
    delta = StateDelta(
        delta_id="delta_1",
        timestamp=datetime.now(timezone.utc),
        operation="start_combat",
        changes={"combat_id": "combat_1"},
    )

    state = await asyncio.wait_for(
        manager.apply_delta("world_x", "session_x", delta),
        timeout=0.5,
    )

    assert state.world_id == "world_x"
    assert state.session_id == "session_x"
    assert state.combat_id == "combat_1"
