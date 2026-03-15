"""Tests for OfferHelpTool — NPC/Teammate spontaneous aid."""

from __future__ import annotations

import asyncio
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.help_tool import (
    HELP_TYPE_EFFECTS,
    TAG_HELP_ALLOWANCES,
    OfferHelpTool,
    _allowed_types_for_tags,
)
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateContainer
from app.game_core.state.slices import SceneSlice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _recording_executor() -> tuple[list[Command], Any]:
    log: list[Command] = []

    def execute(cmd: Command) -> ExecuteResult:
        log.append(cmd)
        return ExecuteResult(executed=True)

    return log, execute


def _ctx(
    *,
    character_id: str = "npc_alice",
    role: str = "npc",
    npc_tags: list[str] | None = None,
    execute_command: Any = None,
) -> AgentContext:
    state = StateContainer()
    scene = SceneSlice()
    scene.restore({})
    state.register(scene)

    metadata: dict[str, Any] = {}
    if character_id:
        metadata["character_id"] = character_id
    if npc_tags is not None:
        metadata["npc_tags"] = npc_tags

    return AgentContext(
        role=role,
        world=WorldInstance("test"),
        state=state,
        metadata=metadata,
        execute_command=execute_command,
    )


# ---------------------------------------------------------------------------
# TAG_HELP_ALLOWANCES / _allowed_types_for_tags
# ---------------------------------------------------------------------------


def test_allowed_types_default_no_tags() -> None:
    """No special tags → only information allowed."""
    allowed = _allowed_types_for_tags([])
    assert allowed == {"information"}


def test_allowed_types_healer_tags() -> None:
    allowed = _allowed_types_for_tags(["healer"])
    assert "heal" in allowed
    assert "buff" in allowed
    assert "cure" in allowed
    assert "information" in allowed  # default always included


def test_allowed_types_merchant_tag() -> None:
    allowed = _allowed_types_for_tags(["merchant"])
    assert "gift_item" in allowed
    assert "information" in allowed
    # heal not allowed for merchant-only NPC
    assert "heal" not in allowed


def test_allowed_types_recruitable_tag() -> None:
    allowed = _allowed_types_for_tags(["recruitable"])
    assert "gift_item" in allowed
    assert "heal" in allowed
    assert "information" in allowed
    # buff/cure not in recruitable
    assert "buff" not in allowed


def test_allowed_types_multiple_tags_union() -> None:
    """healer + merchant → union of both sets."""
    allowed = _allowed_types_for_tags(["healer", "merchant"])
    assert "heal" in allowed
    assert "buff" in allowed
    assert "cure" in allowed
    assert "gift_item" in allowed


# ---------------------------------------------------------------------------
# OfferHelpTool: missing character_id
# ---------------------------------------------------------------------------


def test_offer_help_no_character_id() -> None:
    ctx = _ctx(character_id="")
    result = asyncio.run(OfferHelpTool().execute(
        {"help_type": "heal", "reason": "compassion"}, ctx,
    ))
    assert result.ok is False
    assert result.metadata["status"] == "missing_character_id"


# ---------------------------------------------------------------------------
# OfferHelpTool: capability_exceeded (tag-based rejection)
# ---------------------------------------------------------------------------


def test_offer_help_exceed_capability_no_tags() -> None:
    """NPC with no special tags cannot heal."""
    _, executor = _recording_executor()
    ctx = _ctx(npc_tags=[], execute_command=executor)
    result = asyncio.run(OfferHelpTool().execute(
        {"help_type": "heal", "reason": "I want to help"},
        ctx,
    ))
    assert result.ok is False
    assert result.metadata["status"] == "capability_exceeded"
    assert "heal" not in result.metadata["allowed_types"]
    assert "information" in result.metadata["allowed_types"]


def test_offer_help_exceed_capability_guard_cannot_gift() -> None:
    """Guard tag → only information; gift_item refused."""
    _, executor = _recording_executor()
    ctx = _ctx(npc_tags=["guard"], execute_command=executor)
    result = asyncio.run(OfferHelpTool().execute(
        {"help_type": "gift_item", "reason": "goodwill"},
        ctx,
    ))
    assert result.ok is False
    assert result.metadata["status"] == "capability_exceeded"


# ---------------------------------------------------------------------------
# OfferHelpTool: heal (healer tag, with amount cap)
# ---------------------------------------------------------------------------


def test_offer_help_heal_success() -> None:
    log, executor = _recording_executor()
    ctx = _ctx(npc_tags=["healer"], execute_command=executor)
    result = asyncio.run(OfferHelpTool().execute(
        {"help_type": "heal", "reason": "you look wounded", "details": {"amount": 20}},
        ctx,
    ))
    assert result.ok is True
    assert result.metadata["event_type"] == "npc_help_offered"
    assert result.metadata["help_type"] == "heal"
    assert result.metadata["character_id"] == "npc_alice"
    # Check that restore_hp command was issued
    heal_cmds = [c for c in log if c.type == "npc_service_effect"
                 and c.params.get("effect_type") == "restore_hp"]
    assert len(heal_cmds) == 1
    assert heal_cmds[0].params["amount"] == 20


def test_offer_help_heal_amount_capped_at_50() -> None:
    """Heal amount is capped at 50 regardless of details."""
    log, executor = _recording_executor()
    ctx = _ctx(npc_tags=["healer"], execute_command=executor)
    result = asyncio.run(OfferHelpTool().execute(
        {"help_type": "heal", "reason": "emergency", "details": {"amount": 999}},
        ctx,
    ))
    assert result.ok is True
    heal_cmds = [c for c in log if c.type == "npc_service_effect"
                 and c.params.get("effect_type") == "restore_hp"]
    assert heal_cmds[0].params["amount"] == 50


# ---------------------------------------------------------------------------
# OfferHelpTool: gift_item (merchant tag, with count cap)
# ---------------------------------------------------------------------------


def test_offer_help_gift_item_success() -> None:
    log, executor = _recording_executor()
    ctx = _ctx(npc_tags=["merchant"], execute_command=executor)
    result = asyncio.run(OfferHelpTool().execute(
        {
            "help_type": "gift_item",
            "reason": "you saved my life",
            "details": {"item_id": "healing_potion", "count": 2},
        },
        ctx,
    ))
    assert result.ok is True
    assert result.metadata["help_type"] == "gift_item"
    gift_cmds = [c for c in log if c.type == "pick_up"]
    assert len(gift_cmds) == 1
    assert gift_cmds[0].params["item_id"] == "healing_potion"
    assert gift_cmds[0].params["count"] == 2


def test_offer_help_gift_item_count_capped_at_3() -> None:
    log, executor = _recording_executor()
    ctx = _ctx(npc_tags=["merchant"], execute_command=executor)
    result = asyncio.run(OfferHelpTool().execute(
        {
            "help_type": "gift_item",
            "reason": "generous",
            "details": {"item_id": "apple", "count": 100},
        },
        ctx,
    ))
    assert result.ok is True
    gift_cmds = [c for c in log if c.type == "pick_up"]
    assert gift_cmds[0].params["count"] == 3


# ---------------------------------------------------------------------------
# OfferHelpTool: information (no special tags needed)
# ---------------------------------------------------------------------------


def test_offer_help_information_no_tags() -> None:
    """Any NPC can share information regardless of tags."""
    log, executor = _recording_executor()
    ctx = _ctx(npc_tags=[], execute_command=executor)
    result = asyncio.run(OfferHelpTool().execute(
        {
            "help_type": "information",
            "reason": "I overheard the bandits",
            "details": {"fact": "bandit_camp_location_east"},
        },
        ctx,
    ))
    assert result.ok is True
    assert result.metadata["help_type"] == "information"
    knowledge_cmds = [c for c in log if c.type == "add_knowledge"]
    assert len(knowledge_cmds) == 1
    assert knowledge_cmds[0].params["fact"] == "bandit_camp_location_east"


# ---------------------------------------------------------------------------
# OfferHelpTool: buff / cure (temple_keeper tag)
# ---------------------------------------------------------------------------


def test_offer_help_buff_temple_keeper() -> None:
    log, executor = _recording_executor()
    ctx = _ctx(npc_tags=["temple_keeper"], execute_command=executor)
    result = asyncio.run(OfferHelpTool().execute(
        {
            "help_type": "buff",
            "reason": "blessings of the goddess",
            "details": {"effect_id": "blessed"},
        },
        ctx,
    ))
    assert result.ok is True
    assert result.metadata["help_type"] == "buff"
    buff_cmds = [c for c in log if c.type == "apply_effect"]
    assert len(buff_cmds) == 1
    assert buff_cmds[0].params["effect_id"] == "blessed"


def test_offer_help_cure_temple_keeper() -> None:
    log, executor = _recording_executor()
    ctx = _ctx(npc_tags=["temple_keeper"], execute_command=executor)
    result = asyncio.run(OfferHelpTool().execute(
        {
            "help_type": "cure",
            "reason": "dispel the curse",
            "details": {"effect_id": "cursed"},
        },
        ctx,
    ))
    assert result.ok is True
    cure_cmds = [c for c in log if c.type == "remove_effect"]
    assert len(cure_cmds) == 1
    assert cure_cmds[0].params["effect_id"] == "cursed"


# ---------------------------------------------------------------------------
# OfferHelpTool: teammate role
# ---------------------------------------------------------------------------


def test_offer_help_teammate_role_allowed() -> None:
    """Teammates with recruitable tag can heal."""
    log, executor = _recording_executor()
    ctx = _ctx(
        character_id="companion_lena",
        role="teammate",
        npc_tags=["recruitable"],
        execute_command=executor,
    )
    result = asyncio.run(OfferHelpTool().execute(
        {"help_type": "heal", "reason": "I have potions", "details": {"amount": 15}},
        ctx,
    ))
    assert result.ok is True
    assert result.metadata["character_id"] == "companion_lena"


# ---------------------------------------------------------------------------
# OfferHelpTool: scene bus entry written
# ---------------------------------------------------------------------------


def test_offer_help_writes_scene_entry() -> None:
    _, executor = _recording_executor()
    ctx = _ctx(npc_tags=["healer"], execute_command=executor)
    asyncio.run(OfferHelpTool().execute(
        {"help_type": "heal", "reason": "you need it"},
        ctx,
    ))
    entries = ctx.state.scene.entries
    assert len(entries) >= 1
    tags = {tag for e in entries for tag in e.tags}
    assert "help" in tags
    assert "help_heal" in tags
