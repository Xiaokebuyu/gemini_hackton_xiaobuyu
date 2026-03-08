"""Tests for Block C: Session Resume Party→Area Sync.

Verifies that resume_session() calls CompanionManager.sync_to_player()
so party members are always at the player's current area after load.
"""

from __future__ import annotations

import asyncio

from app.game_core import GameRuntime
from app.game_core.adapters import NullPersistencePort, SaveStore
from app.game_core.state.delta import StateChange, StateDelta


def _make_runtime() -> GameRuntime:
    return GameRuntime(save_store=SaveStore(NullPersistencePort()))


def test_resume_syncs_companion_to_player_area() -> None:
    """After resume, a party member that was in area_A is moved to the player's area_B."""

    async def _run() -> None:
        runtime = _make_runtime()

        session = await runtime.create_session(
            "test_world",
            world_data={},
            session_id="sess_sync_test",
        )

        state = session.runtime.state

        # Set player to area_B via StateDelta so the player slice is marked dirty
        # and the change persists through the save/load cycle.
        state.apply(
            StateDelta(changes=[
                StateChange(slice="player", operation="set", path="current_area", value="area_B"),
            ])
        )

        # Ensure area_A and area_B exist in AreaSlice (lazy creation via get_area)
        state.areas.get_area("area_A")
        state.areas.get_area("area_B")

        # Place npc1 in area_A (different from player) — areas slice becomes dirty
        state.areas.move_npc("npc1", "area_A", None)

        # Add npc1 as a party member — party slice becomes dirty
        state.party.add_member("npc1", {"name": "Companion One"})

        # Save with the diverged companion position
        await runtime.save_session(session)

        # Resume — this should trigger sync_to_player()
        resumed = await runtime.resume_session(
            "test_world",
            "sess_sync_test",
            world_data={},
        )

        assert resumed is not None, "resume_session should return a ManagedSession"

        resumed_state = resumed.runtime.state

        # Companion must now be in area_B (player's area), not area_A
        npc_area = resumed_state.areas.find_npc_area("npc1")
        assert npc_area == "area_B", (
            f"Expected companion in area_B after resume sync, got: {npc_area!r}"
        )

        # Companion must NOT still be in area_A
        area_a_state = resumed_state.areas.get_area("area_A")
        assert "npc1" not in area_a_state.npc_locations, (
            "npc1 should have been removed from area_A after sync"
        )

    asyncio.run(_run())


def test_resume_without_companions_is_noop() -> None:
    """resume_session() with no party members completes without error."""

    async def _run() -> None:
        runtime = _make_runtime()

        session = await runtime.create_session(
            "test_world",
            world_data={},
            session_id="sess_empty_party",
        )

        # No party members — party slice starts empty by default
        assert session.runtime.state.party.members == {}

        await runtime.save_session(session)

        resumed = await runtime.resume_session(
            "test_world",
            "sess_empty_party",
            world_data={},
        )

        assert resumed is not None, "resume_session should succeed with no companions"
        assert resumed.runtime.state.party.members == {}, (
            "Empty party should remain empty after resume"
        )

    asyncio.run(_run())
