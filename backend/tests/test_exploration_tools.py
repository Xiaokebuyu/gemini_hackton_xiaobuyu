"""Tests for DiscoverClueTool and ShareDiscoveryTool (4-B)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest import mock

import pytest

from app.game_core.content import WorldInstance
from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.exploration_tools import DiscoverClueTool, ShareDiscoveryTool
from app.game_core.narrative.models import ToolResult
from app.game_core.rules.models import ExecuteResult
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, SceneSlice, TimeSlice
from app.game_core.state.slices.player import PlayerSlice


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_context(
    *,
    character_id: str = "guard_captain",
    role: str = "npc",
    player_area: str = "frontier_town",
    player_location: str = "north_gate",
    scoped_overlays: dict[str, list[dict[str, Any]]] | None = None,
    interactable_states: dict[str, Any] | None = None,
    slot: int = 5,
    execute_result: ExecuteResult | None = None,
) -> AgentContext:
    """Build a minimal AgentContext for exploration tool tests."""
    state = StateContainer()

    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": slot})
    state.register(time_slice)

    player_slice = PlayerSlice()
    player_slice.restore({
        "current_area": player_area,
        "current_location": player_location,
    })
    state.register(player_slice)

    area_slice = AreaSlice()
    area_data: dict[str, Any] = {
        "areas": {
            player_area: {
                "npc_locations": {},
                "npc_rooms": {},
                "area_events": [],
            }
        }
    }
    if scoped_overlays is not None:
        area_data["areas"][player_area]["scoped_interactable_overlays"] = scoped_overlays
    if interactable_states is not None:
        area_data["areas"][player_area]["interactable_states"] = interactable_states
    area_slice.restore(area_data)
    state.register(area_slice)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)

    # Default execute result: success
    default_result = execute_result or ExecuteResult(
        executed=True,
        metadata={"clue_id": "hidden_letter"},
    )

    ctx = AgentContext(
        role=role,
        world=WorldInstance("test_world"),
        state=state,
        metadata={"character_id": character_id},
        execute_command=lambda cmd: default_result,
    )
    return ctx


# ---------------------------------------------------------------------------
# DiscoverClueTool tests
# ---------------------------------------------------------------------------


def test_discover_clue_missing_clue_id():
    """Tool returns error when clue_id param is absent."""
    tool = DiscoverClueTool()
    ctx = _make_context()

    result = asyncio.run(tool.execute({}, ctx))
    assert result.ok is False
    assert result.metadata["status"] == "invalid_params"


def test_discover_clue_not_in_overlays():
    """Tool returns clue_not_found when clue_id is absent from overlays."""
    tool = DiscoverClueTool()
    ctx = _make_context(scoped_overlays={
        "north_gate": [{"id": "some_other_clue", "type": "clue", "name": "Other Clue"}]
    })

    result = asyncio.run(tool.execute({"clue_id": "hidden_letter"}, ctx))
    assert result.ok is False
    assert result.metadata["status"] == "clue_not_found"


def test_discover_clue_already_resolved():
    """Tool returns clue_already_resolved when clue is in interactable_states."""
    tool = DiscoverClueTool()
    ctx = _make_context(
        scoped_overlays={
            "north_gate": [{"id": "old_note", "type": "clue", "name": "Old Note"}]
        },
        interactable_states={
            "old_note": {"resolved_option_id": "read", "area_id": "frontier_town"},
        },
    )

    result = asyncio.run(tool.execute({"clue_id": "old_note"}, ctx))
    assert result.ok is False
    assert result.metadata["status"] == "clue_already_resolved"


def test_discover_clue_success_writes_area_event():
    """Successful discovery writes an area_event and returns ok."""
    tool = DiscoverClueTool()
    ctx = _make_context(
        scoped_overlays={
            "north_gate": [{"id": "hidden_letter", "type": "clue", "name": "Hidden Letter"}]
        },
    )

    result = asyncio.run(tool.execute({"clue_id": "hidden_letter"}, ctx))
    assert result.ok is True
    assert result.metadata["event_type"] == "clue_investigated"
    assert result.metadata["clue_id"] == "hidden_letter"

    # Verify area_event was written
    events = ctx.state.areas.get_area_events("frontier_town")
    assert any("hidden_letter" in e.get("event", "") for e in events)


def test_discover_clue_command_failure():
    """Tool returns command_failed when execute_command returns not-executed."""
    tool = DiscoverClueTool()
    failed = ExecuteResult(
        executed=False,
        errors=["clue_not_found"],
    )
    ctx = _make_context(
        scoped_overlays={
            "north_gate": [{"id": "broken_clue", "type": "clue", "name": "Broken Clue"}]
        },
        execute_result=failed,
    )

    result = asyncio.run(tool.execute({"clue_id": "broken_clue"}, ctx))
    assert result.ok is False
    assert result.metadata["status"] == "command_failed"


def test_discover_clue_no_player_slice():
    """Tool returns error when player slice is absent."""
    tool = DiscoverClueTool()
    state = StateContainer()
    area_slice = AreaSlice()
    area_slice.restore({})
    state.register(area_slice)

    ctx = AgentContext(
        role="npc",
        world=WorldInstance("t"),
        state=state,
        metadata={"character_id": "guard"},
        execute_command=lambda cmd: ExecuteResult(executed=True),
    )
    result = asyncio.run(tool.execute({"clue_id": "some_clue"}, ctx))
    assert result.ok is False
    assert result.metadata["status"] == "state_unavailable"


def test_discover_clue_scope_key_with_room():
    """Clue under a 'location__room' scope key is found when player is in that location."""
    tool = DiscoverClueTool()
    ctx = _make_context(
        player_location="north_gate",
        scoped_overlays={
            "north_gate__guard_post": [
                {"id": "scratched_mark", "type": "clue", "name": "Scratched Mark"}
            ]
        },
    )

    result = asyncio.run(tool.execute({"clue_id": "scratched_mark"}, ctx))
    assert result.ok is True
    assert result.metadata["clue_id"] == "scratched_mark"


def test_discover_clue_tick_stamped_in_area_event():
    """area_event written by discover_clue contains the current slot as tick."""
    tool = DiscoverClueTool()
    ctx = _make_context(
        slot=77,
        scoped_overlays={
            "north_gate": [{"id": "letter", "type": "clue", "name": "Letter"}]
        },
    )
    asyncio.run(tool.execute({"clue_id": "letter"}, ctx))

    events = ctx.state.areas.get_area_events("frontier_town")
    assert any(e.get("tick") == 77 for e in events)


# ---------------------------------------------------------------------------
# ShareDiscoveryTool tests
# ---------------------------------------------------------------------------


def test_share_discovery_missing_content():
    """Tool returns error when content param is absent."""
    tool = ShareDiscoveryTool()
    ctx = _make_context(role="teammate")

    result = asyncio.run(tool.execute({}, ctx))
    assert result.ok is False
    assert result.metadata["status"] == "invalid_params"


def test_share_discovery_success():
    """Successful share_discovery returns teammate_discovery event_type."""
    tool = ShareDiscoveryTool()
    ctx = _make_context(role="teammate", character_id="hero_companion")

    result = asyncio.run(tool.execute({"content": "I found a hidden passage!"}, ctx))
    assert result.ok is True
    assert result.metadata["event_type"] == "teammate_discovery"
    assert result.metadata["content"] == "I found a hidden passage!"
    assert result.metadata["character_id"] == "hero_companion"


def test_share_discovery_writes_to_scene():
    """Share discovery writes to SceneBus when scene slice is available."""
    tool = ShareDiscoveryTool()
    ctx = _make_context(role="teammate", character_id="hero_companion")

    asyncio.run(tool.execute({"content": "There's a trapdoor here."}, ctx))

    # Scene should have an entry with the discovery content
    entries = ctx.state.scene.entries
    assert any("trapdoor" in e.content for e in entries)
