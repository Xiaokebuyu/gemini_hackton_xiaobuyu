"""Tests for NPC + Teammate agent tools."""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.narrative import RoleToolRegistry, register_npc_tools, register_teammate_tools
from app.game_core.narrative.character_tools import (
    EmoteTool,
    ExpressOpinionTool,
    OfferQuestTool,
    OfferTradeTool,
    RefuseTool,
    RememberTool,
    RequestActionTool,
    RevealSecretTool,
    ShareMemoryTool,
    SpeakTool,
    SuggestTacticTool,
    UpdateFeelingTool,
)
from app.game_core.narrative.context import AgentContext
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateContainer
from app.game_core.state.slices import RelationSlice, SceneSlice
from app.game_core.state.slices.party import PartySlice


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _recording_executor() -> tuple[
    list[Command], "Callable[[Command], ExecuteResult]"
]:
    """Return (log, executor) where executor records commands and succeeds."""
    log: list[Command] = []

    def execute(cmd: Command) -> ExecuteResult:
        log.append(cmd)
        return ExecuteResult(executed=True)

    return log, execute


def _ctx(
    *,
    character_id: str = "npc_alice",
    role: str = "npc",
    with_scene: bool = True,
    with_relations: bool = False,
    relations_payload: dict[str, Any] | None = None,
    with_party: bool = False,
    party_payload: dict[str, Any] | None = None,
    execute_command: Any = None,
    metadata: dict[str, Any] | None = None,
) -> AgentContext:
    state = StateContainer()

    if with_scene:
        scene = SceneSlice()
        scene.restore({})
        state.register(scene)

    if with_relations:
        rel = RelationSlice()
        rel.restore(relations_payload or {})
        state.register(rel)

    if with_party:
        party = PartySlice()
        party.restore(party_payload or {})
        state.register(party)

    context_metadata = {"character_id": character_id} if character_id else {}
    if metadata:
        context_metadata.update(dict(metadata))
    return AgentContext(
        role=role,
        world=WorldInstance("test"),
        state=state,
        metadata=context_metadata,
        execute_command=execute_command,
    )


# ------------------------------------------------------------------
# SceneBus tools: speak
# ------------------------------------------------------------------


def test_speak_writes_scene_entry() -> None:
    context = _ctx(character_id="npc_bob")
    result = asyncio.run(SpeakTool().execute({"text": "Hello there!"}, context))

    assert result.ok is True
    assert result.message == "Hello there!"
    assert result.metadata["event_type"] == "speech"
    assert result.metadata["character_id"] == "npc_bob"

    entries = context.state.scene.entries
    assert len(entries) == 1
    assert entries[0].source == "npc_bob"
    assert entries[0].content == "Hello there!"
    assert "speech" in entries[0].tags


def test_speak_fails_without_character_id() -> None:
    context = _ctx(character_id="")
    result = asyncio.run(SpeakTool().execute({"text": "Hi"}, context))

    assert result.ok is False
    assert result.metadata["status"] == "missing_character_id"


# ------------------------------------------------------------------
# SceneBus tools: emote
# ------------------------------------------------------------------


def test_emote_writes_scene_entry() -> None:
    context = _ctx(character_id="npc_bob")
    result = asyncio.run(EmoteTool().execute({"action": "sighs deeply"}, context))

    assert result.ok is True
    assert result.metadata["event_type"] == "emote"

    entries = context.state.scene.entries
    assert len(entries) == 1
    assert entries[0].content == "sighs deeply"
    assert "emote" in entries[0].tags


# ------------------------------------------------------------------
# SceneBus tools: refuse
# ------------------------------------------------------------------


def test_refuse_writes_scene_entry() -> None:
    context = _ctx(character_id="npc_guard")
    result = asyncio.run(
        RefuseTool().execute({"reason": "You lack the authority."}, context)
    )

    assert result.ok is True
    assert result.metadata["event_type"] == "refuse"

    entries = context.state.scene.entries
    assert len(entries) == 1
    assert entries[0].content == "You lack the authority."
    assert "refuse" in entries[0].tags


# ------------------------------------------------------------------
# Command tools: update_feeling
# ------------------------------------------------------------------


def test_update_feeling_constructs_disposition_command() -> None:
    log, executor = _recording_executor()
    context = _ctx(character_id="npc_alice", execute_command=executor)

    result = asyncio.run(
        UpdateFeelingTool().execute(
            {"dimension": "trust", "delta": 2}, context,
        )
    )

    assert result.ok is True
    assert result.metadata["dimension"] == "trust"
    assert result.metadata["delta"] == 2
    assert len(log) == 1
    assert log[0].type == "modify_disposition"
    assert log[0].params["npc_id"] == "npc_alice"
    assert log[0].params["dimension"] == "trust"
    assert log[0].params["delta"] == 2
    assert log[0].source == "ai_osiris"


def test_update_feeling_rejects_invalid_dimension() -> None:
    context = _ctx()
    result = asyncio.run(
        UpdateFeelingTool().execute(
            {"dimension": "rage", "delta": 5}, context,
        )
    )
    assert result.ok is False
    assert result.metadata["status"] == "invalid_params"


# ------------------------------------------------------------------
# Command tools: remember
# ------------------------------------------------------------------


def test_remember_writes_actor_memory() -> None:
    log, executor = _recording_executor()
    calls: list[dict[str, Any]] = []

    async def _memory_writer(actor_id: str, knowledge: str, context_payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(
            {
                "actor_id": actor_id,
                "knowledge": knowledge,
                "context": dict(context_payload),
            }
        )
        return {"memory_id": "memory:npc_alice:1"}

    context = _ctx(
        character_id="npc_alice",
        execute_command=executor,
        metadata={"memory_writer": _memory_writer},
    )

    result = asyncio.run(
        RememberTool().execute({"knowledge": "Player helped me once."}, context)
    )

    assert result.ok is True
    assert log == []
    assert len(calls) == 1
    assert calls[0]["actor_id"] == "npc_alice"
    assert calls[0]["knowledge"] == "Player helped me once."
    assert result.metadata["memory_id"] == "memory:npc_alice:1"


# ------------------------------------------------------------------
# Command tools: offer_quest
# ------------------------------------------------------------------


def test_offer_quest_constructs_advance_quest() -> None:
    log, executor = _recording_executor()
    context = _ctx(character_id="npc_clerk", execute_command=executor)

    result = asyncio.run(
        OfferQuestTool().execute({"quest_id": "find_artifact"}, context)
    )

    assert result.ok is True
    assert result.metadata["quest_id"] == "find_artifact"
    assert len(log) == 1
    assert log[0].type == "advance_quest"
    assert log[0].params["quest_id"] == "find_artifact"
    assert log[0].params["to_state"] == "AVAILABLE"


# ------------------------------------------------------------------
# Command tools: reveal_secret
# ------------------------------------------------------------------


def test_reveal_secret_checks_trust_threshold() -> None:
    context = _ctx(
        character_id="npc_alice",
        with_relations=True,
        relations_payload={
            "npc_dispositions": {"npc_alice": {"trust": 30}},
        },
    )

    result = asyncio.run(
        RevealSecretTool().execute(
            {"secret": "I am a spy.", "trust_required": 60}, context,
        )
    )

    assert result.ok is False
    assert result.metadata["status"] == "trust_insufficient"
    assert result.metadata["current_trust"] == 30
    assert result.metadata["required"] == 60
    # SceneBus should NOT have the secret
    assert len(context.state.scene.entries) == 0


def test_reveal_secret_writes_scene_and_command() -> None:
    log, executor = _recording_executor()
    context = _ctx(
        character_id="npc_alice",
        with_relations=True,
        relations_payload={
            "npc_dispositions": {"npc_alice": {"trust": 80}},
        },
        execute_command=executor,
    )

    result = asyncio.run(
        RevealSecretTool().execute(
            {"secret": "I am a spy.", "trust_required": 60}, context,
        )
    )

    assert result.ok is True
    assert result.metadata["event_type"] == "secret_reveal"
    assert result.metadata["knowledge_recorded"] is True

    # SceneBus has the secret
    entries = context.state.scene.entries
    assert len(entries) == 1
    assert entries[0].content == "I am a spy."
    assert "secret" in entries[0].tags

    # Knowledge command recorded
    assert len(log) == 1
    assert log[0].type == "add_knowledge"
    assert "Revealed:" in log[0].params["knowledge"]


# ------------------------------------------------------------------
# Command tools: express_opinion
# ------------------------------------------------------------------


def test_express_opinion_constructs_approval_command() -> None:
    log, executor = _recording_executor()
    context = _ctx(
        character_id="companion_gale",
        role="teammate",
        execute_command=executor,
    )

    result = asyncio.run(
        ExpressOpinionTool().execute(
            {"delta": -15, "reason": "Disapproves of cruelty."}, context,
        )
    )

    assert result.ok is True
    assert result.metadata["delta"] == -15
    assert result.metadata["reason"] == "Disapproves of cruelty."
    assert len(log) == 1
    assert log[0].type == "modify_approval"
    assert log[0].params["character_id"] == "companion_gale"
    assert log[0].params["delta"] == -15


# ------------------------------------------------------------------
# Read-only / narration tools
# ------------------------------------------------------------------


def test_offer_trade_returns_shop_data() -> None:
    context = _ctx(
        character_id="npc_merchant",
        with_relations=True,
        relations_payload={
            "shop_states": {
                "npc_merchant": {"inventory": ["potion", "sword"], "gold": 500},
            },
        },
    )

    result = asyncio.run(OfferTradeTool().execute({}, context))

    assert result.ok is True
    assert result.metadata["event_type"] == "offer_trade"
    assert result.metadata["shop"]["inventory"] == ["potion", "sword"]
    assert result.metadata["shop"]["gold"] == 500


def test_suggest_tactic_returns_text() -> None:
    context = _ctx(role="teammate")
    result = asyncio.run(
        SuggestTacticTool().execute({"tactic": "Flank from the left."}, context)
    )

    assert result.ok is True
    assert result.message == "Flank from the left."
    assert result.metadata["event_type"] == "suggest_tactic"


def test_share_memory_returns_impressions() -> None:
    context = _ctx(
        character_id="companion_gale",
        role="teammate",
        with_relations=True,
        relations_payload={
            "npc_impressions": {
                "companion_gale": [
                    "We fought goblins together.",
                    "Player saved my life.",
                ],
            },
        },
    )

    result = asyncio.run(
        ShareMemoryTool().execute({"topic": "goblins"}, context)
    )

    assert result.ok is True
    assert result.metadata["event_type"] == "share_memory"
    assert result.metadata["topic"] == "goblins"
    assert len(result.metadata["memories"]) == 2


def test_request_action_returns_text() -> None:
    context = _ctx(role="teammate")
    result = asyncio.run(
        RequestActionTool().execute(
            {"request": "Please heal me."}, context,
        )
    )

    assert result.ok is True
    assert result.message == "Please heal me."
    assert result.metadata["event_type"] == "request_action"


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------


def test_register_npc_tools_registers_nine() -> None:
    registry = RoleToolRegistry()
    register_npc_tools(registry)

    tools = registry.get_tools_for("npc")
    names = {t.name for t in tools}
    assert len(names) == 18
    assert names == {
        "speak", "emote",
        "update_feeling", "remember", "recall", "offer_quest",
        "accept_quest", "assign_quest", "offer_trade", "refuse", "reveal_secret",
        "join_party", "execute_service", "discover_clue", "offer_help",
        "update_blackboard", "send_npc_message", "sell_to_player",
    }


def test_register_teammate_tools_registers_seven() -> None:
    registry = RoleToolRegistry()
    register_teammate_tools(registry)

    tools = registry.get_tools_for("teammate")
    names = {t.name for t in tools}
    assert len(names) == 13
    assert names == {
        "speak", "emote",
        "recall", "express_opinion", "suggest_tactic",
        "share_memory", "request_action",
        "leave_party", "discover_clue", "share_discovery", "offer_help",
        "update_blackboard", "send_npc_message",
    }


# ------------------------------------------------------------------
# UpdateFeelingTool: delta clamping
# ------------------------------------------------------------------


def test_update_feeling_clamps_approval_above_max() -> None:
    log, executor = _recording_executor()
    context = _ctx(character_id="npc_alice", execute_command=executor)

    result = asyncio.run(
        UpdateFeelingTool().execute(
            {"dimension": "approval", "delta": 20}, context,
        )
    )

    assert result.ok is True
    assert result.metadata["delta"] == 3
    assert len(log) == 1
    assert log[0].params["delta"] == 3


def test_update_feeling_clamps_romance_below_min() -> None:
    log, executor = _recording_executor()
    context = _ctx(character_id="npc_alice", execute_command=executor)

    result = asyncio.run(
        UpdateFeelingTool().execute(
            {"dimension": "romance", "delta": -5}, context,
        )
    )

    assert result.ok is True
    assert result.metadata["delta"] == -1
    assert len(log) == 1
    assert log[0].params["delta"] == -1


def test_update_feeling_within_bounds_no_clamp() -> None:
    log, executor = _recording_executor()
    context = _ctx(character_id="npc_alice", execute_command=executor)

    result = asyncio.run(
        UpdateFeelingTool().execute(
            {"dimension": "approval", "delta": 2}, context,
        )
    )

    assert result.ok is True
    assert result.metadata["delta"] == 2
    assert len(log) == 1
    assert log[0].params["delta"] == 2
