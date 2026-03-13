"""Tests for A7: Private Chat Co-location Validation.

A7c: utterance_orchestration.py validates NPC presence before entering
private chat scope.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.game_core.content import WorldInstance
from app.game_core.content.registries import CharacterRegistry
from app.game_core.orchestration.models import SSEEvent
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, PlayerSlice, SceneSlice
from app.game_core.state.slices.quests import QuestSlice
from app.utterance_orchestration import (
    UtteranceOrchestrator,
    UtteranceRequest,
    UtteranceTarget,
)


# ------------------------------------------------------------------
# Minimal fake runtime and session for A7c
# ------------------------------------------------------------------


class _FakeRuntime:
    """Minimum fake runtime providing state and world for presence validation."""

    def __init__(self, state: StateContainer, world: WorldInstance) -> None:
        self.state = state
        self.world = world


class _FakeSession:
    def __init__(self, state: StateContainer, world: WorldInstance) -> None:
        self.runtime = _FakeRuntime(state, world)


def _build_state(
    *,
    player_area: str,
    player_location: str | None,
    player_room: str | None,
    npc_id: str,
    npc_area: str,
    npc_location: str | None,
    npc_room: str | None,
) -> StateContainer:
    """Build a minimal StateContainer with player and area slices."""
    state = StateContainer()

    scene = SceneSlice()
    scene.restore({"entries": [], "state_changes": []})
    state.register(scene)

    player = PlayerSlice()
    player.restore({
        "current_area": player_area,
        "current_location": player_location or "",
        "current_room": player_room,
    })
    state.register(player)

    area = AreaSlice()
    area.restore({
        "areas": {
            npc_area: {
                "npc_locations": {npc_id: npc_location},
                "npc_rooms": {npc_id: npc_room},
            },
            # Also register the player's area if it's different (for context builder)
            player_area: {
                "npc_locations": {npc_id: npc_location} if npc_area == player_area else {},
                "npc_rooms": {npc_id: npc_room} if npc_area == player_area else {},
            },
        },
    })
    state.register(area)

    quests = QuestSlice()
    quests.restore({})
    state.register(quests)

    return state


def _build_world(npc_id: str) -> WorldInstance:
    world = WorldInstance("test_world")
    registry = CharacterRegistry()
    registry.load({npc_id: {"id": npc_id, "name": "Test NPC", "personality": "Friendly."}})
    world.register(registry)
    return world


def _private_utterance(npc_id: str, text: str = "hello") -> UtteranceRequest:
    return UtteranceRequest(
        text=text,
        scope="private",
        intent="talk",
        focus_target=UtteranceTarget(kind="npc", id=npc_id),
    )


# ------------------------------------------------------------------
# A7c tests: utterance_orchestration private scope presence validation
# ------------------------------------------------------------------


class TestA7cPrivateChatPresenceValidation:

    def test_private_chat_blocked_when_npc_in_different_area(self) -> None:
        """NPC in a different area → validate_presence rejects → interaction_rejected event."""
        state = _build_state(
            player_area="town",
            player_location="inn",
            player_room=None,
            npc_id="aria",
            npc_area="ruins",            # different area!
            npc_location="outer_hall",
            npc_room=None,
        )
        world = _build_world("aria")
        session = _FakeSession(state, world)

        mock_agent = AsyncMock()
        orchestrator = UtteranceOrchestrator(mock_agent)
        utterance = _private_utterance("aria")

        async def _run() -> Any:
            return await orchestrator.execute(session, utterance)  # type: ignore[arg-type]

        result = asyncio.run(_run())
        assert result.completed is False
        assert result.reason == "interaction_rejected"
        assert any(e.event_type == "interaction_rejected" for e in result.events)
        # agent should NOT have been called
        mock_agent.run_private_chat.assert_not_called()

    def test_private_chat_blocked_when_npc_in_different_sublocation(self) -> None:
        """NPC in same area but different sub-location → blocked."""
        state = _build_state(
            player_area="frontier_town",
            player_location="tavern",
            player_room=None,
            npc_id="guild_lady",
            npc_area="frontier_town",
            npc_location="guild_hall",  # different sub-location
            npc_room=None,
        )
        world = _build_world("guild_lady")
        session = _FakeSession(state, world)

        mock_agent = AsyncMock()
        orchestrator = UtteranceOrchestrator(mock_agent)
        utterance = _private_utterance("guild_lady")

        async def _run() -> Any:
            return await orchestrator.execute(session, utterance)  # type: ignore[arg-type]

        result = asyncio.run(_run())
        assert result.completed is False
        assert result.reason == "interaction_rejected"
        mock_agent.run_private_chat.assert_not_called()

    def test_private_chat_allowed_when_npc_colocated(self) -> None:
        """NPC in same area AND same sub-location → presence passes → agent invoked."""
        state = _build_state(
            player_area="frontier_town",
            player_location="tavern",
            player_room=None,
            npc_id="innkeeper",
            npc_area="frontier_town",
            npc_location="tavern",  # same sub-location as player
            npc_room=None,
        )
        world = _build_world("innkeeper")
        session = _FakeSession(state, world)

        expected_events = [SSEEvent("npc_speech", {"text": "Welcome!"})]
        mock_agent = AsyncMock()
        mock_agent.run_private_chat = AsyncMock(return_value=expected_events)
        orchestrator = UtteranceOrchestrator(mock_agent)
        utterance = _private_utterance("innkeeper")

        async def _run() -> Any:
            return await orchestrator.execute(session, utterance)  # type: ignore[arg-type]

        result = asyncio.run(_run())
        mock_agent.run_private_chat.assert_called_once()
        # Should not be blocked
        assert not any(e.event_type == "interaction_rejected" for e in result.events)

    def test_private_chat_allowed_when_both_at_area_root(self) -> None:
        """Both NPC and player at area root (None location) → colocated → allowed."""
        state = _build_state(
            player_area="frontier_town",
            player_location=None,   # area root
            player_room=None,
            npc_id="wanderer",
            npc_area="frontier_town",
            npc_location=None,      # also at area root
            npc_room=None,
        )
        world = _build_world("wanderer")
        session = _FakeSession(state, world)

        expected_events = [SSEEvent("npc_speech", {"text": "Hey."})]
        mock_agent = AsyncMock()
        mock_agent.run_private_chat = AsyncMock(return_value=expected_events)
        orchestrator = UtteranceOrchestrator(mock_agent)
        utterance = _private_utterance("wanderer")

        async def _run() -> Any:
            return await orchestrator.execute(session, utterance)  # type: ignore[arg-type]

        result = asyncio.run(_run())
        mock_agent.run_private_chat.assert_called_once()
        assert not any(e.event_type == "interaction_rejected" for e in result.events)

    def test_interaction_rejected_event_contains_npc_id(self) -> None:
        """Rejected private chat event payload includes target npc id."""
        state = _build_state(
            player_area="town",
            player_location="square",
            player_room=None,
            npc_id="remote_npc",
            npc_area="dungeon",
            npc_location="corridor",
            npc_room=None,
        )
        world = _build_world("remote_npc")
        session = _FakeSession(state, world)

        mock_agent = AsyncMock()
        orchestrator = UtteranceOrchestrator(mock_agent)
        utterance = _private_utterance("remote_npc")

        async def _run() -> Any:
            return await orchestrator.execute(session, utterance)  # type: ignore[arg-type]

        result = asyncio.run(_run())
        rejected = next(
            (e for e in result.events if e.event_type == "interaction_rejected"), None
        )
        assert rejected is not None
        assert rejected.payload.get("target_id") == "remote_npc"

    def test_private_chat_blocked_when_npc_in_different_room(self) -> None:
        """NPC in same sub-location but different room → blocked."""
        state = _build_state(
            player_area="frontier_town",
            player_location="guild_hall",
            player_room="office",
            npc_id="guild_lady",
            npc_area="frontier_town",
            npc_location="guild_hall",
            npc_room="counter",
        )
        world = _build_world("guild_lady")
        session = _FakeSession(state, world)

        mock_agent = AsyncMock()
        orchestrator = UtteranceOrchestrator(mock_agent)
        utterance = _private_utterance("guild_lady")

        async def _run() -> Any:
            return await orchestrator.execute(session, utterance)  # type: ignore[arg-type]

        result = asyncio.run(_run())
        assert result.completed is False
        assert result.reason == "interaction_rejected"
        mock_agent.run_private_chat.assert_not_called()
