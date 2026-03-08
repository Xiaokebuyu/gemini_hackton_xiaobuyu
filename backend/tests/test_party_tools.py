"""Tests for JoinPartyTool, LeavePartyTool and their SSE conversion.

Block F — Phase F3.  Covers:
  JoinPartyTool (~7): recruit ok, trait filter, stranger refused, approval refused,
                      party full, already member, no character_id
  LeavePartyTool (~4): dismiss ok, not member refused, default reason, no character_id
  SSE conversion (~2): companion_recruited / companion_dismissed ToolResult -> SSEEvent
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock
from pathlib import Path

from app.game_core.content import WorldInstance
from app.game_core.narrative.context_builder import AgentContextBuilder
from app.game_core.narrative.character_tools import (
    JoinPartyTool,
    LeavePartyTool,
    register_npc_tools,
    register_teammate_tools,
)
from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.models import AgentResult, ToolResult
from app.game_core.narrative.registry import RoleToolRegistry
from app.game_core.orchestration.models import SSEEvent
from app.game_core.rules import Command, ExecuteResult, RulesEngine
from app.game_core.rules.handlers import CompanionHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices.party import PartySlice
from app.game_core.state.slices.player import PlayerSlice
from app.game_core.state.slices.relations import RelationSlice
from app.game_core.state.slices.scene import SceneSlice
from app.game_core.state.slices.time import TimeSlice


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_world_with_npc(
    npc_id: str,
    *,
    tags: list[str] | None = None,
    extra_npcs: dict[str, list[str]] | None = None,
) -> WorldInstance:
    """Return a WorldInstance with a fake character registry (MagicMock).

    Follows the pattern from test_round5_companion.py to avoid tag-validation
    issues with build_default_world.
    """
    world = WorldInstance("test")

    profiles: dict[str, Any] = {}

    # Primary NPC
    profile = MagicMock()
    profile.name = npc_id
    profile.class_id = ""
    profile.tags = tags or []
    profiles[npc_id] = profile

    # Extra NPCs (e.g. teammates, filler)
    for eid, etags in (extra_npcs or {}).items():
        p = MagicMock()
        p.name = eid
        p.class_id = ""
        p.tags = etags
        profiles[eid] = p

    char_registry = MagicMock()
    char_registry.get = lambda cid: profiles.get(cid)

    world._registries["characters"] = char_registry
    return world


def _make_state(
    *,
    party_members: dict[str, Any] | None = None,
    npc_dispositions: dict[str, Any] | None = None,
    relationship_stages: dict[str, Any] | None = None,
) -> StateContainer:
    state = StateContainer()

    scene_sl = SceneSlice()
    scene_sl.restore({})
    state.register(scene_sl)

    time_sl = TimeSlice()
    time_sl.restore({})
    state.register(time_sl)

    player_sl = PlayerSlice()
    player_sl.restore({
        "character_name": "Hero",
        "character_class": "warrior",
        "current_area": "town",
        "current_location": "market",
    })
    state.register(player_sl)

    party = PartySlice()
    party.restore({
        "members": party_members or {},
        "companion_approval": {},
        "shared_experiences": [],
    })
    state.register(party)

    rel = RelationSlice()
    rel.restore({
        "npc_dispositions": npc_dispositions or {},
        "relationship_stages": relationship_stages or {},
        "faction_standings": {},
        "npc_impressions": {},
        "shop_states": {},
    })
    state.register(rel)

    return state


def _npc_context(
    world: WorldInstance,
    state: StateContainer,
    character_id: str,
) -> AgentContext:
    executor = _command_executor(world, state)
    return AgentContext(
        role="npc",
        world=world,
        state=state,
        metadata={"character_id": character_id},
        execute_command=executor,
    )


def _teammate_context(
    world: WorldInstance,
    state: StateContainer,
    character_id: str,
) -> AgentContext:
    executor = _command_executor(world, state)
    return AgentContext(
        role="teammate",
        world=world,
        state=state,
        metadata={"character_id": character_id},
        execute_command=executor,
    )


def _command_executor(world: WorldInstance, state: StateContainer):
    engine = RulesEngine()
    engine.register(CompanionHandler())

    def _execute(command: Command) -> ExecuteResult:
        result = engine.execute(command, state, world)
        if result.executed and result.delta is not None:
            state.apply(result.delta)
        return result

    return _execute


# ------------------------------------------------------------------
# JoinPartyTool
# ------------------------------------------------------------------


def test_join_party_success() -> None:
    """Recruitable NPC with approval > 0 and not stranger joins successfully."""
    world = _make_world_with_npc("recruit_npc", tags=["recruitable"])
    state = _make_state(
        npc_dispositions={"recruit_npc": {"approval": 20, "trust": 10}},
        relationship_stages={"recruit_npc": "acquaintance"},
    )
    ctx = _npc_context(world, state, "recruit_npc")
    tool = JoinPartyTool()

    result = asyncio.run(tool.execute({}, ctx))

    assert result.ok
    assert result.metadata["event_type"] == "companion_recruited"
    assert result.metadata["npc_id"] == "recruit_npc"
    assert "recruit_npc" in result.metadata["party_members"]
    # Verify state actually changed
    assert "recruit_npc" in state.party.members


def test_join_party_trait_filter() -> None:
    """JoinPartyTool has applicable_traits=['recruitable'] for registry filtering."""
    tool = JoinPartyTool()
    assert tool.applicable_traits == ["recruitable"]
    assert tool.allowed_roles == ["npc"]

    # Verify registry filtering: non-recruitable NPC should not see join_party
    registry = RoleToolRegistry()
    register_npc_tools(registry)
    tools_with_trait = registry.get_tools_for("npc", ["recruitable"])
    tool_names_with = [t.name for t in tools_with_trait]
    assert "join_party" in tool_names_with

    tools_without_trait = registry.get_tools_for("npc", ["merchant"])
    tool_names_without = [t.name for t in tools_without_trait]
    assert "join_party" not in tool_names_without


def test_join_party_stranger_refused() -> None:
    """NPC at stranger stage cannot be recruited."""
    world = _make_world_with_npc("recruit_npc", tags=["recruitable"])
    state = _make_state(
        npc_dispositions={"recruit_npc": {"approval": 20, "trust": 10}},
        relationship_stages={"recruit_npc": "stranger"},
    )
    ctx = _npc_context(world, state, "recruit_npc")
    tool = JoinPartyTool()

    result = asyncio.run(tool.execute({}, ctx))

    assert not result.ok
    assert result.message == "stranger"


def test_join_party_approval_refused() -> None:
    """NPC with approval <= 0 refuses to join."""
    world = _make_world_with_npc("recruit_npc", tags=["recruitable"])
    state = _make_state(
        npc_dispositions={"recruit_npc": {"approval": 0, "trust": 10}},
        relationship_stages={"recruit_npc": "acquaintance"},
    )
    ctx = _npc_context(world, state, "recruit_npc")
    tool = JoinPartyTool()

    result = asyncio.run(tool.execute({}, ctx))

    assert not result.ok
    assert result.message == "npc_refuses"


def test_join_party_full() -> None:
    """Party at MAX_PARTY_SIZE refuses recruitment."""
    world = _make_world_with_npc(
        "recruit_npc",
        tags=["recruitable"],
        extra_npcs={
            "tm_a": [], "tm_b": [], "tm_c": [], "tm_d": [],
        },
    )
    state = _make_state(
        party_members={
            "tm_a": {"role": "warrior"},
            "tm_b": {"role": "rogue"},
            "tm_c": {"role": "mage"},
            "tm_d": {"role": "healer"},
        },
        npc_dispositions={"recruit_npc": {"approval": 20, "trust": 10}},
        relationship_stages={"recruit_npc": "acquaintance"},
    )
    ctx = _npc_context(world, state, "recruit_npc")
    tool = JoinPartyTool()

    result = asyncio.run(tool.execute({}, ctx))

    assert not result.ok
    assert result.message == "party_full"


def test_join_party_already_member() -> None:
    """NPC already in party cannot be recruited again."""
    world = _make_world_with_npc("recruit_npc", tags=["recruitable"])
    state = _make_state(
        party_members={"recruit_npc": {"role": "fighter"}},
        npc_dispositions={"recruit_npc": {"approval": 20, "trust": 10}},
        relationship_stages={"recruit_npc": "acquaintance"},
    )
    ctx = _npc_context(world, state, "recruit_npc")
    tool = JoinPartyTool()

    result = asyncio.run(tool.execute({}, ctx))

    assert not result.ok
    assert result.message == "already_member"


def test_join_party_no_character_id() -> None:
    """Missing character_id returns failure."""
    world = _make_world_with_npc("recruit_npc", tags=["recruitable"])
    state = _make_state()
    ctx = AgentContext(role="npc", world=world, state=state, metadata={})
    tool = JoinPartyTool()

    result = asyncio.run(tool.execute({}, ctx))

    assert not result.ok
    assert "character_id" in result.message.lower()


def test_join_party_succeeds_with_role_state_proxy_context() -> None:
    """Real runtime NPC context should recruit through command path."""
    world = _make_world_with_npc("recruit_npc", tags=["recruitable"])
    state = _make_state(
        npc_dispositions={"recruit_npc": {"approval": 20, "trust": 10}},
        relationship_stages={"recruit_npc": "acquaintance"},
    )
    builder = AgentContextBuilder(world, state)
    ctx = builder.build_agent_context(
        "npc",
        "recruit_npc",
        execute_command=_command_executor(world, state),
    )

    result = asyncio.run(JoinPartyTool().execute({}, ctx))

    assert result.ok
    assert result.metadata["party_members"] == ["recruit_npc"]
    assert "recruit_npc" in state.party.members


# ------------------------------------------------------------------
# LeavePartyTool
# ------------------------------------------------------------------


def test_leave_party_success() -> None:
    """Teammate currently in party can leave."""
    world = _make_world_with_npc("tm_a")
    state = _make_state(party_members={"tm_a": {"role": "warrior"}})
    assert "tm_a" in state.party.members

    ctx = _teammate_context(world, state, "tm_a")
    tool = LeavePartyTool()

    result = asyncio.run(tool.execute({"reason": "I disagree with your choices."}, ctx))

    assert result.ok
    assert result.metadata["event_type"] == "companion_dismissed"
    assert result.metadata["npc_id"] == "tm_a"
    assert result.metadata["reason"] == "I disagree with your choices."
    # Verify state actually changed
    assert "tm_a" not in state.party.members


def test_leave_party_not_member() -> None:
    """Attempting to leave when not a member fails."""
    world = _make_world_with_npc("tm_a")
    state = _make_state(party_members={})
    ctx = _teammate_context(world, state, "tm_a")
    tool = LeavePartyTool()

    result = asyncio.run(tool.execute({}, ctx))

    assert not result.ok
    assert result.message == "not_member"


def test_leave_party_default_reason() -> None:
    """When no reason provided, uses 'voluntary' as default."""
    world = _make_world_with_npc("tm_a")
    state = _make_state(party_members={"tm_a": {"role": "warrior"}})
    ctx = _teammate_context(world, state, "tm_a")
    tool = LeavePartyTool()

    result = asyncio.run(tool.execute({}, ctx))

    assert result.ok
    assert result.metadata["reason"] == "voluntary"


def test_leave_party_no_character_id() -> None:
    """Missing character_id returns failure."""
    world = _make_world_with_npc("tm_a")
    state = _make_state(party_members={"tm_a": {"role": "warrior"}})
    ctx = AgentContext(role="teammate", world=world, state=state, metadata={})
    tool = LeavePartyTool()

    result = asyncio.run(tool.execute({}, ctx))

    assert not result.ok
    assert "character_id" in result.message.lower()


# ------------------------------------------------------------------
# SSE conversion
# ------------------------------------------------------------------


def test_npc_result_to_sse_companion_recruited() -> None:
    """companion_recruited ToolResult converts to SSEEvent."""
    from app.agent_orchestration import _npc_result_to_sse

    tr = ToolResult(
        ok=True,
        message="recruit_npc joined the party.",
        metadata={
            "status": "ok",
            "event_type": "companion_recruited",
            "npc_id": "recruit_npc",
            "party_members": ["tm_a", "recruit_npc"],
        },
    )
    agent_result = AgentResult(tool_results=[tr])
    events = _npc_result_to_sse("recruit_npc", agent_result)

    assert len(events) == 1
    evt = events[0]
    assert evt.event_type == "companion_recruited"
    assert evt.payload["npc_id"] == "recruit_npc"
    assert evt.payload["party_members"] == ["tm_a", "recruit_npc"]


def test_teammate_result_to_sse_companion_dismissed() -> None:
    """companion_dismissed ToolResult converts to SSEEvent."""
    from app.agent_orchestration import _teammate_result_to_sse

    tr = ToolResult(
        ok=True,
        message="tm_a left the party: I disagree.",
        metadata={
            "status": "ok",
            "event_type": "companion_dismissed",
            "npc_id": "tm_a",
            "reason": "I disagree.",
            "party_members": [],
        },
    )
    agent_result = AgentResult(tool_results=[tr])
    events = _teammate_result_to_sse("tm_a", agent_result)

    assert len(events) == 1
    evt = events[0]
    assert evt.event_type == "companion_dismissed"
    assert evt.payload["npc_id"] == "tm_a"
    assert evt.payload["reason"] == "I disagree."
    assert evt.payload["party_members"] == []


def test_curated_named_npcs_are_marked_recruitable() -> None:
    data = json.loads(
        Path("data/goblin_slayer/v2/characters.json").read_text(encoding="utf-8")
    )

    curated = {
        "priestess",
        "high_elf_archer",
        "dwarf_shaman",
        "lizard_priest",
        "guild_girl",
        "cow_girl",
    }
    for npc_id in curated:
        assert "recruitable" in data[npc_id]["tags"]

    assert "recruitable" not in data["goblin_slayer"]["tags"]
