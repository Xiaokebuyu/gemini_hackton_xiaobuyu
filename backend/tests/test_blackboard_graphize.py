"""Tests for NpcAutonomyHook observations overflow → pending_graphize → knowledge graph.

Covers:
1. Overflow observations accumulate in pending_graphize.
2. Non-overflow does NOT populate pending_graphize.
3. pending_graphize < 100 does NOT trigger graphize.
4. pending_graphize >= 100 triggers _graphize_pending().
5. No LLM → fallback raw triples (npc_id, "观察到", text).
6. With LLM → triples extracted from LLM response, written via add_triple.
7. After graphize, pending_graphize is cleared.
8. SendNpcMessageTool overflow also feeds pending_graphize.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest import mock

from app.game_core.adapters.llm import LlmResponse
from app.game_core.content import WorldInstance
from app.game_core.narrative.blackboard_tools import SendNpcMessageTool
from app.game_core.narrative.context import AgentContext
from app.game_core.orchestration.hooks.npc_autonomy import NpcAutonomyHook
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules import RulesEngine
from app.game_core.state import StateContainer
from app.game_core.state.slices import AreaSlice, SceneSlice, TimeSlice
from app.game_core.state.slices.party import PartySlice
from app.game_core.state.slices.player import PlayerSlice
from app.game_core.state.slices.relations import RelationSlice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(
    *,
    player_area: str = "frontier_town",
    player_location: str | None = "north_gate",
    npc_locations: dict[str, str | None] | None = None,
    area_events: list[dict[str, Any]] | None = None,
    companions: list[str] | None = None,
    blackboards: dict[str, dict[str, Any]] | None = None,
    knowledge_graph: Any = None,
    slot: int = 10,
) -> SettlementContext:
    state = StateContainer()

    # time
    time_slice = TimeSlice()
    time_slice.restore({"day": 1, "slot": slot})
    state.register(time_slice)

    # player
    player_slice = PlayerSlice()
    player_slice.restore({
        "current_area": player_area,
        "current_location": player_location,
    })
    state.register(player_slice)

    # areas
    area_slice = AreaSlice()
    area_data: dict[str, Any] = {
        "areas": {
            player_area: {
                "npc_locations": npc_locations or {},
                "npc_rooms": {},
                "area_events": area_events or [],
            }
        }
    }
    area_slice.restore(area_data)
    state.register(area_slice)

    # relations — optionally seeded with pre-built blackboards
    rel_slice = RelationSlice()
    rel_payload: dict[str, Any] = {}
    if blackboards:
        rel_payload["npc_blackboards"] = blackboards
    rel_slice.restore(rel_payload)
    state.register(rel_slice)

    # party
    party_slice = PartySlice()
    members: dict[str, dict[str, Any]] = {}
    if companions:
        for c_id in companions:
            members[c_id] = {"id": c_id}
    party_slice.restore({"members": members})
    state.register(party_slice)

    # scene (required for SceneBus)
    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)

    world = WorldInstance("test_world")
    engine = RulesEngine()

    def _apply_delta(delta) -> None:
        if delta is not None:
            state.apply(delta)

    return SettlementContext(
        change_log=[],
        state=state,
        world=world,
        scene_bus=SceneBus(scene_slice),
        _rules_engine=engine,
        _apply_delta=_apply_delta,
        knowledge_graph=knowledge_graph,
    )


def _make_mock_kg() -> Any:
    """Return a mock object with add_triple and add_raw_triple methods."""
    kg = mock.MagicMock()
    kg.add_triple = mock.MagicMock()
    kg.add_raw_triple = mock.MagicMock()
    return kg


# ---------------------------------------------------------------------------
# 1. Overflow accumulates in pending_graphize (via NpcAutonomyHook)
# ---------------------------------------------------------------------------


def test_overflow_goes_into_pending_graphize() -> None:
    """When merged_obs exceeds 10, displaced entries go to pending_graphize."""
    # NPC starts with 9 observations; area has 3 events → merged=12, overflow=2.
    existing_obs = [f"obs_{i}" for i in range(19)]
    area_events = [
        {"event": "event_A", "tick": 1},
        {"event": "event_B", "tick": 2},
        {"event": "event_C", "tick": 3},
    ]
    ctx = _make_context(
        npc_locations={"npc_alice": "north_gate"},
        area_events=area_events,
        blackboards={"npc_alice": {"updated_tick": 0, "observations": existing_obs}},
    )
    hook = NpcAutonomyHook()
    asyncio.run(hook.execute(ctx))

    board = ctx.state.relations.get_blackboard("npc_alice")
    obs = board.get("observations", [])
    pending = board.get("pending_graphize", [])

    # Observations capped at 20.
    assert len(obs) == 20
    # Overflow entries entered pending_graphize.
    assert len(pending) > 0


# ---------------------------------------------------------------------------
# 2. No overflow → pending_graphize stays empty
# ---------------------------------------------------------------------------


def test_no_overflow_no_pending_graphize() -> None:
    """When merged_obs <= 10, pending_graphize is not populated."""
    # NPC starts with 2 observations; area has 1 event → merged=3, no overflow.
    existing_obs = ["obs_0", "obs_1"]
    area_events = [{"event": "event_X", "tick": 5}]
    ctx = _make_context(
        npc_locations={"npc_alice": "north_gate"},
        area_events=area_events,
        blackboards={"npc_alice": {"updated_tick": 0, "observations": existing_obs}},
    )
    hook = NpcAutonomyHook()
    asyncio.run(hook.execute(ctx))

    board = ctx.state.relations.get_blackboard("npc_alice")
    pending = board.get("pending_graphize", [])
    assert pending == []


# ---------------------------------------------------------------------------
# 3. pending_graphize < 100 does NOT trigger _graphize_pending
# ---------------------------------------------------------------------------


def test_pending_below_threshold_no_graphize() -> None:
    """If pending_graphize has fewer than 100 entries, no graphize call is made."""
    # 9 existing obs + 3 events = 12 → 2 overflow.  pending_graphize will have 2 entries.
    existing_obs = [f"obs_{i}" for i in range(19)]
    area_events = [
        {"event": "e1", "tick": 1},
        {"event": "e2", "tick": 2},
        {"event": "e3", "tick": 3},
    ]
    mock_kg = _make_mock_kg()
    ctx = _make_context(
        npc_locations={"npc_alice": "north_gate"},
        area_events=area_events,
        blackboards={"npc_alice": {"updated_tick": 0, "observations": existing_obs}},
        knowledge_graph=mock_kg,
    )
    hook = NpcAutonomyHook()
    asyncio.run(hook.execute(ctx))

    # Knowledge graph methods should NOT have been called — threshold not reached.
    mock_kg.add_triple.assert_not_called()
    mock_kg.add_raw_triple.assert_not_called()

    board = ctx.state.relations.get_blackboard("npc_alice")
    # pending_graphize should still have the 2 overflow entries (not cleared).
    pending = board.get("pending_graphize", [])
    assert len(pending) > 0


# ---------------------------------------------------------------------------
# 4. pending_graphize >= 100 triggers _graphize_pending (fallback path)
# ---------------------------------------------------------------------------


def test_pending_at_threshold_triggers_graphize_fallback() -> None:
    """When pending_graphize reaches 100 entries, fallback graphize fires."""
    # Start with 100 pending_graphize entries + enough to trigger overflow this tick.
    pending_100 = [f"old_obs_{i}" for i in range(100)]
    existing_obs = [f"obs_{i}" for i in range(19)]
    area_events = [
        {"event": "new_event", "tick": 99},
        {"event": "new_event_2", "tick": 100},
        {"event": "new_event_3", "tick": 101},
    ]
    mock_kg = _make_mock_kg()
    ctx = _make_context(
        npc_locations={"npc_alice": "north_gate"},
        area_events=area_events,
        blackboards={
            "npc_alice": {
                "updated_tick": 0,
                "observations": existing_obs,
                "pending_graphize": pending_100,
            }
        },
        knowledge_graph=mock_kg,
    )
    hook = NpcAutonomyHook()  # No LLM → fallback path
    asyncio.run(hook.execute(ctx))

    # Fallback triples should have been written.
    assert mock_kg.add_raw_triple.call_count > 0

    # pending_graphize must be cleared after graphize.
    board = ctx.state.relations.get_blackboard("npc_alice")
    pending = board.get("pending_graphize", [])
    assert pending == []


# ---------------------------------------------------------------------------
# 5. With LLM: triples are extracted and written via add_triple
# ---------------------------------------------------------------------------


def test_graphize_with_llm_calls_add_triple() -> None:
    """When LLM is provided, extracted triples are written via knowledge_graph.add_triple."""
    pending_100 = [f"old_obs_{i}" for i in range(100)]
    existing_obs = [f"obs_{i}" for i in range(19)]
    area_events = [
        {"event": "e1", "tick": 1},
        {"event": "e2", "tick": 2},
        {"event": "e3", "tick": 3},
    ]

    # LLM returns two record_triple tool calls.
    llm_response = LlmResponse(
        tool_calls=[
            {
                "name": "record_triple",
                "args": {
                    "subject": "Alice",
                    "relation": "knows_about",
                    "object": "Guild",
                    "weight": 0.9,
                },
            },
            {
                "name": "record_triple",
                "args": {
                    "subject": "Merchant",
                    "relation": "interacted_with",
                    "object": "Player",
                },
            },
        ]
    )
    mock_llm = mock.AsyncMock()
    mock_llm.generate = mock.AsyncMock(return_value=llm_response)

    mock_kg = _make_mock_kg()
    ctx = _make_context(
        npc_locations={"npc_alice": "north_gate"},
        area_events=area_events,
        blackboards={
            "npc_alice": {
                "updated_tick": 0,
                "observations": existing_obs,
                "pending_graphize": pending_100,
            }
        },
        knowledge_graph=mock_kg,
    )
    hook = NpcAutonomyHook(llm_provider=mock_llm)
    asyncio.run(hook.execute(ctx))

    # LLM generate should have been called once.
    assert mock_llm.generate.call_count == 1
    # add_triple should have been called for each extracted triple.
    assert mock_kg.add_triple.call_count == 2

    # Check one specific call.
    calls = mock_kg.add_triple.call_args_list
    first_call = calls[0]
    assert first_call.args[0] == "Alice"
    assert first_call.args[1] == "knows_about"
    assert first_call.args[2] == "Guild"

    # Fallback should NOT have been used.
    mock_kg.add_raw_triple.assert_not_called()


# ---------------------------------------------------------------------------
# 6. After graphize, pending_graphize is cleared
# ---------------------------------------------------------------------------


def test_graphize_clears_pending() -> None:
    """After graphize (LLM or fallback), pending_graphize is reset to []."""
    pending_100 = [f"item_{i}" for i in range(100)]
    existing_obs = [f"obs_{i}" for i in range(19)]
    area_events = [{"event": "ev", "tick": 1}, {"event": "ev2", "tick": 2}, {"event": "ev3", "tick": 3}]

    mock_kg = _make_mock_kg()
    ctx = _make_context(
        npc_locations={"npc_alice": "north_gate"},
        area_events=area_events,
        blackboards={
            "npc_alice": {
                "updated_tick": 0,
                "observations": existing_obs,
                "pending_graphize": pending_100,
            }
        },
        knowledge_graph=mock_kg,
    )
    hook = NpcAutonomyHook()
    asyncio.run(hook.execute(ctx))

    board = ctx.state.relations.get_blackboard("npc_alice")
    assert board.get("pending_graphize", None) == []


# ---------------------------------------------------------------------------
# 7. No knowledge_graph → pending cleared silently
# ---------------------------------------------------------------------------


def test_graphize_without_knowledge_graph_clears_pending() -> None:
    """When no knowledge_graph is on context, pending_graphize is still cleared."""
    pending_100 = [f"item_{i}" for i in range(100)]
    existing_obs = [f"obs_{i}" for i in range(19)]
    area_events = [{"event": "ev", "tick": 1}, {"event": "ev2", "tick": 2}, {"event": "ev3", "tick": 3}]

    ctx = _make_context(
        npc_locations={"npc_alice": "north_gate"},
        area_events=area_events,
        blackboards={
            "npc_alice": {
                "updated_tick": 0,
                "observations": existing_obs,
                "pending_graphize": pending_100,
            }
        },
        knowledge_graph=None,  # no graph
    )
    hook = NpcAutonomyHook()
    asyncio.run(hook.execute(ctx))

    board = ctx.state.relations.get_blackboard("npc_alice")
    assert board.get("pending_graphize", None) == []


# ---------------------------------------------------------------------------
# 8. SendNpcMessageTool overflow feeds pending_graphize for target
# ---------------------------------------------------------------------------


def test_send_npc_message_overflow_goes_to_pending_graphize() -> None:
    """When SendNpcMessageTool appends message causing >10 obs, overflow → pending_graphize."""
    # Target already has 10 observations; adding one more overflows.
    existing_target_obs = [f"t_obs_{i}" for i in range(20)]
    state = StateContainer()
    rel_slice = RelationSlice()
    rel_slice.restore({
        "npc_blackboards": {
            "npc_alice": {"observations": []},
            "npc_bob": {"observations": existing_target_obs},
        }
    })
    state.register(rel_slice)

    ctx = AgentContext(
        role="npc",
        world=WorldInstance("test"),
        state=state,
        metadata={"character_id": "npc_alice"},
    )

    result = asyncio.run(
        SendNpcMessageTool().execute(
            {"target_npc_id": "npc_bob", "message": "extra message"},
            ctx,
        )
    )
    assert result.ok is True

    target_board = ctx.state.relations.get_blackboard("npc_bob")
    obs = target_board.get("observations", [])
    pending = target_board.get("pending_graphize", [])

    # Observations capped at 20.
    assert len(obs) == 20
    # One entry overflowed into pending_graphize.
    assert len(pending) == 1
