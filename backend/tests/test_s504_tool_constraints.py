"""Tests for S5-04 — NPC Tool Call Constraint Guard.

Covers Phase 4 of the P24 plan:
  4a. npc_interaction.py injects role_data into AgentContext.metadata
  4b. OfferQuestTool.applicable_traits returns ["receptionist"]
  4c. OfferQuestTool.execute() validates quest_id against bulletins/dynamic_quests when role_data present
  4d. OfferTradeTool warns on missing shop_state (still returns ok=True)
  4e. RoleToolRegistry filters out OfferQuestTool for non-receptionist NPCs

Decision record: D-S504 (rules_engine.md)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.narrative import RoleToolRegistry, register_npc_tools
from app.game_core.narrative.character_tools import OfferQuestTool, OfferTradeTool
from app.game_core.narrative.context import AgentContext
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateContainer
from app.game_core.state.slices import RelationSlice, SceneSlice
from app.game_core.state.slices.quests import QuestSlice


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _recording_executor() -> tuple[list[Command], Any]:
    log: list[Command] = []

    def execute(cmd: Command) -> ExecuteResult:
        log.append(cmd)
        return ExecuteResult(executed=True)

    return log, execute


def _make_state(
    *,
    with_quests: bool = False,
    dynamic_quests: dict[str, Any] | None = None,
    with_relations: bool = False,
    shop_states: dict[str, Any] | None = None,
) -> StateContainer:
    state = StateContainer()
    scene = SceneSlice()
    scene.restore({})
    state.register(scene)

    if with_quests:
        quests = QuestSlice()
        quests.restore({"dynamic_quests": dynamic_quests or {}})
        state.register(quests)

    if with_relations:
        rel = RelationSlice()
        rel.restore({"shop_states": shop_states or {}})
        state.register(rel)

    return state


def _ctx(
    character_id: str = "guild_girl",
    *,
    metadata: dict[str, Any] | None = None,
    with_quests: bool = False,
    dynamic_quests: dict[str, Any] | None = None,
    with_relations: bool = False,
    shop_states: dict[str, Any] | None = None,
    execute_command: Any = None,
) -> AgentContext:
    state = _make_state(
        with_quests=with_quests,
        dynamic_quests=dynamic_quests,
        with_relations=with_relations,
        shop_states=shop_states,
    )
    ctx_metadata: dict[str, Any] = {"character_id": character_id}
    if metadata:
        ctx_metadata.update(metadata)
    return AgentContext(
        role="npc",
        world=WorldInstance("test"),
        state=state,
        metadata=ctx_metadata,
        execute_command=execute_command,
    )


# ------------------------------------------------------------------
# 1. OfferQuestTool.applicable_traits returns ["receptionist"]
# ------------------------------------------------------------------


def test_offer_quest_tool_applicable_traits() -> None:
    """OfferQuestTool must be gated behind the 'receptionist' trait."""
    tool = OfferQuestTool()
    assert tool.applicable_traits == ["receptionist"]


# ------------------------------------------------------------------
# 2. Receptionist NPC with valid quest_id in bulletins → success
# ------------------------------------------------------------------


def test_offer_quest_valid_bulletin_quest() -> None:
    """Receptionist with quest_id present in bulletins succeeds."""
    log, executor = _recording_executor()
    role_data = {
        "role": "receptionist",
        "bulletins": [{"quest_id": "goblin_q1", "title": "Goblin Hunt"}],
        "active_quests": [],
    }
    context = _ctx(
        metadata={"role_data": role_data},
        execute_command=executor,
    )
    result = asyncio.run(OfferQuestTool().execute({"quest_id": "goblin_q1"}, context))

    assert result.ok is True
    assert "goblin_q1" in result.message
    assert len(log) == 1
    assert log[0].type == "advance_quest"
    assert log[0].params["quest_id"] == "goblin_q1"


# ------------------------------------------------------------------
# 3. Receptionist with invalid quest_id (not in bulletins, not in dynamic_quests) → error
# ------------------------------------------------------------------


def test_offer_quest_invalid_quest_not_on_board() -> None:
    """Receptionist with quest_id not in bulletins and not in dynamic_quests → error."""
    log, executor = _recording_executor()
    role_data = {
        "role": "receptionist",
        "bulletins": [{"quest_id": "goblin_q1", "title": "Goblin Hunt"}],
        "active_quests": [],
    }
    context = _ctx(
        metadata={"role_data": role_data},
        with_quests=True,
        dynamic_quests={},
        execute_command=executor,
    )
    result = asyncio.run(OfferQuestTool().execute({"quest_id": "fake_quest"}, context))

    assert result.ok is False
    assert result.metadata["status"] == "quest_not_available"
    assert "fake_quest" in result.message
    # No command should have been issued
    assert len(log) == 0


# ------------------------------------------------------------------
# 4. P29-A2: OfferQuestTool bulletin-board-only validation
# (state.quests fallback removed — bulletin board is the canonical truth source)
# ------------------------------------------------------------------


def test_offer_quest_not_in_bulletins_is_rejected() -> None:
    """P29-A2: Quest not on bulletin board is rejected even if it exists in
    dynamic_quests as 'available'. Bulletin board is the canonical source."""
    log, executor = _recording_executor()
    role_data = {
        "role": "receptionist",
        "bulletins": [],  # empty bulletin board
        "active_quests": [],
    }
    context = _ctx(
        metadata={"role_data": role_data},
        with_quests=True,
        dynamic_quests={
            "special_q": {
                "quest_id": "special_q",
                "title": "Special Quest",
                "status": "available",
            }
        },
        execute_command=executor,
    )
    result = asyncio.run(OfferQuestTool().execute({"quest_id": "special_q"}, context))

    assert result.ok is False
    assert result.metadata["status"] == "quest_not_available"
    assert len(log) == 0


# ------------------------------------------------------------------
# 5. No role_data in metadata → no validation, quest is passed through (backward compat)
# ------------------------------------------------------------------


def test_offer_quest_no_role_data_skips_validation() -> None:
    """When role_data is absent, OfferQuestTool skips validation and executes."""
    log, executor = _recording_executor()
    context = _ctx(
        execute_command=executor,
        # No role_data in metadata
    )
    result = asyncio.run(OfferQuestTool().execute({"quest_id": "any_quest"}, context))

    assert result.ok is True
    assert len(log) == 1
    assert log[0].params["quest_id"] == "any_quest"


# ------------------------------------------------------------------
# 6. OfferTradeTool logs warning when no shop_state and still returns ok=True
# ------------------------------------------------------------------


def test_offer_trade_no_shop_state_warns_and_returns_ok(caplog: Any) -> None:
    """OfferTradeTool returns ok=True with empty shop + logs a warning."""
    log, executor = _recording_executor()
    context = _ctx(
        character_id="blacksmith",
        with_relations=True,
        shop_states={},  # No entry for "blacksmith"
        execute_command=executor,
    )
    with caplog.at_level(logging.WARNING, logger="app.game_core.narrative.character_tools"):
        result = asyncio.run(OfferTradeTool().execute({}, context))

    assert result.ok is True
    assert result.message == "No merchandise available."
    assert any("no shop_state" in record.message for record in caplog.records)


# ------------------------------------------------------------------
# 7. RoleToolRegistry: non-receptionist NPC does not get OfferQuestTool
# ------------------------------------------------------------------


def test_role_tool_registry_filters_offer_quest_for_non_receptionist() -> None:
    """RoleToolRegistry.get_tools_for('npc', traits=[...]) must exclude OfferQuestTool for non-receptionists."""
    registry = RoleToolRegistry()
    register_npc_tools(registry)

    # Non-receptionist NPC (e.g., merchant): should not see offer_quest
    merchant_tools = registry.get_tools_for("npc", traits=["merchant"])
    tool_names = {t.name for t in merchant_tools}
    assert "offer_quest" not in tool_names

    # Receptionist NPC: should see offer_quest
    receptionist_tools = registry.get_tools_for("npc", traits=["receptionist"])
    receptionist_tool_names = {t.name for t in receptionist_tools}
    assert "offer_quest" in receptionist_tool_names


# ------------------------------------------------------------------
# 8. OfferTradeTool.applicable_traits = ["merchant"] (unchanged)
# ------------------------------------------------------------------


def test_offer_trade_tool_applicable_traits() -> None:
    """OfferTradeTool must still require 'merchant' trait."""
    tool = OfferTradeTool()
    assert tool.applicable_traits == ["merchant"]
