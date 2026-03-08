"""Tests for GM agent tools."""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.narrative import RoleToolRegistry, register_gm_tools
from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.gm_tools import (
    CommentTool,
    DescribeEnvironmentTool,
    NarrateTool,
    PassTurnTool,
    SuggestOptionsTool,
)
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, PlayerSlice, TimeSlice


def _context(**overrides: Any) -> AgentContext:
    return AgentContext(
        role="gm",
        world=overrides.pop("world", WorldInstance("test")),
        state=overrides.pop("state", StateContainer()),
        **overrides,
    )


def _context_with_area(
    area_id: str = "tavern",
    npc_ids: dict[str, str | None] | None = None,
    day: int = 1,
    slot: int = 9,
) -> AgentContext:
    """Build a context with player in an area, optionally with NPCs."""
    state = StateContainer()

    player = PlayerSlice()
    player.restore({"current_area": area_id})
    state.register(player)

    area_slice = AreaSlice()
    area_slice.restore({"areas": {area_id: {
        "exploration": "explored",
        "danger_level": 0.5,
        "npc_locations": npc_ids or {},
    }}})
    state.register(area_slice)

    time_slice = TimeSlice()
    time_slice.restore({"day": day, "slot": slot})
    state.register(time_slice)

    return AgentContext(role="gm", world=WorldInstance("test"), state=state)


# ------------------------------------------------------------------
# describe_environment
# ------------------------------------------------------------------


def test_describe_environment_gathers_area_info() -> None:
    ctx = _context_with_area(
        area_id="tavern",
        npc_ids={"bartender": "bar", "guard": "entrance"},
    )
    result = asyncio.run(DescribeEnvironmentTool().execute({}, ctx))

    assert result.ok is True
    assert result.metadata["status"] == "ok"
    ref = result.metadata["reference"]
    assert ref["area"]["area_id"] == "tavern"
    assert ref["area"]["exploration"] == "explored"
    assert ref["area"]["danger_level"] == 0.5
    assert "bartender" in ref["area"]["npc_locations"]
    assert ref["time"] == {"day": 1, "slot": 9}
    # No map content registered in test world
    assert ref["map_content"] is None


def test_describe_environment_no_area() -> None:
    result = asyncio.run(DescribeEnvironmentTool().execute({}, _context()))

    assert result.ok is True
    assert result.metadata["status"] == "no_area"
    assert "No current area" in result.message


# ------------------------------------------------------------------
# narrate
# ------------------------------------------------------------------


def test_narrate_returns_text_with_event_type() -> None:
    text = "The fire crackles in the hearth."
    result = asyncio.run(NarrateTool().execute({"text": text}, _context()))

    assert result.ok is True
    assert result.message == text
    assert result.metadata["event_type"] == "gm_narration"


def test_narrate_rejects_empty_text() -> None:
    for bad in ["", "   ", 42, None]:
        result = asyncio.run(NarrateTool().execute({"text": bad}, _context()))
        assert result.ok is False
        assert result.metadata["status"] == "invalid_params"


# ------------------------------------------------------------------
# comment
# ------------------------------------------------------------------


def test_comment_returns_text_with_event_type() -> None:
    text = "Brilliant strategy — if the goal was to die."
    result = asyncio.run(CommentTool().execute({"text": text}, _context()))

    assert result.ok is True
    assert result.message == text
    assert result.metadata["event_type"] == "gm_comment"


# ------------------------------------------------------------------
# pass_turn
# ------------------------------------------------------------------


def test_pass_turn_returns_empty() -> None:
    result = asyncio.run(PassTurnTool().execute({}, _context()))

    assert result.ok is True
    assert result.message == ""
    assert result.metadata["event_type"] == "pass"


# ------------------------------------------------------------------
# suggest_options
# ------------------------------------------------------------------


def test_suggest_options_validates_and_filters() -> None:
    raw_options = [
        {"text": "Persuade the guard", "check": {"skill": "persuasion"}},
        {"text": "Draw sword", "action": "initiate_combat"},
        {"text": "", "action": "bad"},          # empty text → filtered
        {"text": "No intent"},                  # no check/action → filtered
        42,                                     # not a dict → filtered
    ]
    result = asyncio.run(
        SuggestOptionsTool().execute({"options": raw_options}, _context())
    )

    assert result.ok is True
    assert result.metadata["status"] == "ok"
    assert result.metadata["event_type"] == "dialogue_options"
    opts = result.metadata["options"]
    assert len(opts) == 2
    assert opts[0] == {"text": "Persuade the guard", "check": {"skill": "persuasion"}}
    assert opts[1] == {"text": "Draw sword", "action": "initiate_combat"}


def test_suggest_options_rejects_all_invalid() -> None:
    result = asyncio.run(
        SuggestOptionsTool().execute(
            {"options": [{"text": "no intent"}, 42]}, _context(),
        )
    )
    assert result.ok is False
    assert result.metadata["status"] == "invalid_params"


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------


def test_register_gm_tools_registers_all_five() -> None:
    registry = RoleToolRegistry()
    register_gm_tools(registry)

    tools = registry.get_tools_for("gm")
    names = [t.name for t in tools]
    assert len(names) == 5
    assert set(names) == {
        "describe_environment", "narrate", "comment", "pass_turn", "suggest_options",
    }
    # GM tools should not appear in other roles
    assert registry.get_tools_for("npc") == []
    assert registry.get_tools_for("teammate") == []
