"""Tests for UpdateBlackboardTool and SendNpcMessageTool."""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.narrative.blackboard_tools import SendNpcMessageTool, UpdateBlackboardTool
from app.game_core.narrative.context import AgentContext
from app.game_core.state import StateContainer
from app.game_core.state.slices import RelationSlice, SceneSlice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(
    *,
    character_id: str = "npc_alice",
    role: str = "npc",
    with_relations: bool = True,
    blackboards: dict[str, dict[str, Any]] | None = None,
    with_scene: bool = False,
    world: WorldInstance | None = None,
) -> AgentContext:
    state = StateContainer()

    if with_relations:
        rel = RelationSlice()
        payload: dict[str, Any] = {}
        if blackboards:
            payload["npc_blackboards"] = blackboards
        rel.restore(payload)
        state.register(rel)

    if with_scene:
        scene = SceneSlice()
        scene.restore({})
        state.register(scene)

    metadata: dict[str, Any] = {}
    if character_id:
        metadata["character_id"] = character_id

    return AgentContext(
        role=role,
        world=world or WorldInstance("test"),
        state=state,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# UpdateBlackboardTool — basic field updates
# ---------------------------------------------------------------------------


def test_update_blackboard_thoughts() -> None:
    ctx = _ctx()
    result = asyncio.run(
        UpdateBlackboardTool().execute({"thoughts": "这个冒险者看起来值得信任"}, ctx)
    )

    assert result.ok is True
    assert result.metadata["event_type"] == "blackboard_updated"
    assert "thoughts" in result.metadata["updated_fields"]

    board = ctx.state.relations.get_blackboard("npc_alice")
    assert board["thoughts"] == "这个冒险者看起来值得信任"


def test_update_blackboard_mood_and_attitude() -> None:
    ctx = _ctx()
    result = asyncio.run(
        UpdateBlackboardTool().execute(
            {"mood": "cautious", "attitude_towards_player": "好奇但保持距离"},
            ctx,
        )
    )

    assert result.ok is True
    board = ctx.state.relations.get_blackboard("npc_alice")
    assert board["mood"] == "cautious"
    assert board["attitude_towards_player"] == "好奇但保持距离"


def test_update_blackboard_new_observation_appends() -> None:
    # Start with existing observations.
    ctx = _ctx(blackboards={"npc_alice": {"observations": ["第一条观察"]}})
    result = asyncio.run(
        UpdateBlackboardTool().execute({"new_observation": "第二条观察"}, ctx)
    )

    assert result.ok is True
    board = ctx.state.relations.get_blackboard("npc_alice")
    assert board["observations"] == ["第一条观察", "第二条观察"]


def test_update_blackboard_observations_capped_at_ten() -> None:
    # Fill observations to 20 already; adding one more should truncate oldest.
    existing = [f"obs_{i}" for i in range(20)]
    ctx = _ctx(blackboards={"npc_alice": {"observations": existing}})
    result = asyncio.run(
        UpdateBlackboardTool().execute({"new_observation": "obs_new"}, ctx)
    )

    assert result.ok is True
    board = ctx.state.relations.get_blackboard("npc_alice")
    observations = board["observations"]
    assert len(observations) == 20
    assert observations[-1] == "obs_new"
    # Oldest entry was dropped.
    assert "obs_0" not in observations


def test_update_blackboard_no_fields_returns_error() -> None:
    ctx = _ctx()
    result = asyncio.run(UpdateBlackboardTool().execute({}, ctx))

    assert result.ok is False
    assert result.metadata["status"] == "no_fields_provided"


def test_update_blackboard_missing_character_id() -> None:
    ctx = _ctx(character_id="")
    result = asyncio.run(UpdateBlackboardTool().execute({"thoughts": "hello"}, ctx))

    assert result.ok is False
    assert result.metadata["status"] == "missing_character_id"


def test_update_blackboard_no_relations_slice() -> None:
    ctx = _ctx(with_relations=False)
    result = asyncio.run(UpdateBlackboardTool().execute({"thoughts": "hello"}, ctx))

    assert result.ok is False
    assert result.metadata["status"] == "no_relations_slice"


def test_update_blackboard_marks_relations_dirty() -> None:
    ctx = _ctx()
    assert not ctx.state.relations.dirty

    asyncio.run(UpdateBlackboardTool().execute({"thoughts": "some thought"}, ctx))

    assert ctx.state.relations.dirty


# ---------------------------------------------------------------------------
# SendNpcMessageTool
# ---------------------------------------------------------------------------


def test_send_npc_message_writes_to_target_observations() -> None:
    ctx = _ctx(character_id="npc_alice")
    # Bob's blackboard has no observations yet.
    ctx.state.relations.update_blackboard("npc_bob", {})

    result = asyncio.run(
        SendNpcMessageTool().execute(
            {"target_npc_id": "npc_bob", "message": "明天记得来铁匠铺"},
            ctx,
        )
    )

    assert result.ok is True
    assert result.metadata["target_npc_id"] == "npc_bob"

    target_board = ctx.state.relations.get_blackboard("npc_bob")
    obs = target_board.get("observations", [])
    assert any("npc_alice" in o or "明天记得来铁匠铺" in o for o in obs)


def test_send_npc_message_writes_outgoing_note_to_sender() -> None:
    ctx = _ctx(character_id="npc_alice")

    asyncio.run(
        SendNpcMessageTool().execute(
            {"target_npc_id": "npc_bob", "message": "有事相商"},
            ctx,
        )
    )

    sender_board = ctx.state.relations.get_blackboard("npc_alice")
    obs = sender_board.get("observations", [])
    assert any("npc_bob" in o or "有事相商" in o for o in obs)


def test_send_npc_message_rejects_self() -> None:
    ctx = _ctx(character_id="npc_alice")
    result = asyncio.run(
        SendNpcMessageTool().execute(
            {"target_npc_id": "npc_alice", "message": "自言自语"},
            ctx,
        )
    )

    assert result.ok is False
    assert result.metadata["status"] == "self_message_rejected"


def test_send_npc_message_missing_target_id() -> None:
    ctx = _ctx()
    result = asyncio.run(
        SendNpcMessageTool().execute({"message": "hello"}, ctx)
    )

    assert result.ok is False
    assert result.metadata["status"] == "missing_target_npc_id"


def test_send_npc_message_missing_message() -> None:
    ctx = _ctx()
    result = asyncio.run(
        SendNpcMessageTool().execute({"target_npc_id": "npc_bob"}, ctx)
    )

    assert result.ok is False
    assert result.metadata["status"] == "missing_message"


def test_send_npc_message_target_observations_capped() -> None:
    # Target has 19 existing observations; sending 1 more should not exceed 20.
    existing = [f"obs_{i}" for i in range(19)]
    ctx = _ctx(
        character_id="npc_alice",
        blackboards={"npc_bob": {"observations": existing}},
    )

    asyncio.run(
        SendNpcMessageTool().execute(
            {"target_npc_id": "npc_bob", "message": "第十条消息"},
            ctx,
        )
    )

    target_board = ctx.state.relations.get_blackboard("npc_bob")
    obs = target_board.get("observations", [])
    assert len(obs) == 20
