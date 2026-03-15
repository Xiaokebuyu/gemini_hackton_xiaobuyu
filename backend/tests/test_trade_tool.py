"""Tests for SellToPlayerTool.

Tests cover:
- Normal purchase (gold deducted, item granted)
- Insufficient gold
- Insufficient stock (non-unlimited item)
- price_override clamped to [base_price*0.5, base_price*1.5]
- Unlimited item: remaining not decremented
- item_id not in shop_stock
"""

from __future__ import annotations

import asyncio
import math
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.trade_tool import SellToPlayerTool
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateContainer
from app.game_core.state.slices import RelationSlice, SceneSlice
from app.game_core.state.slices.player import PlayerSlice


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _recording_executor(
    succeed: bool = True,
    errors: list[str] | None = None,
) -> tuple[list[Command], Any]:
    """Return (log, executor) that records commands and optionally fails."""
    log: list[Command] = []

    def execute(cmd: Command) -> ExecuteResult:
        log.append(cmd)
        if succeed:
            return ExecuteResult(executed=True)
        return ExecuteResult(executed=False, errors=errors or ["command failed"])

    return log, execute


def _make_shop_stock(
    item_id: str,
    base_price: int,
    *,
    unlimited: bool = True,
    remaining: int | None = None,
    source: str = "base",
) -> dict[str, Any]:
    """Build a shop_stock entry for role_data injection."""
    return {
        "item_id": item_id,
        "name": item_id.replace("_", " ").title(),
        "base_price": base_price,
        "unlimited": unlimited,
        "remaining": remaining,
        "source": source,
        "price_override": None,
    }


def _make_ctx(
    *,
    character_id: str = "blacksmith",
    gold: int = 100,
    shop_stock: list[dict[str, Any]] | None = None,
    with_relations: bool = False,
    relations_shop_state: dict[str, Any] | None = None,
    executor_succeed: bool = True,
    executor_errors: list[str] | None = None,
) -> tuple[AgentContext, list[Command]]:
    """Construct an AgentContext with a PlayerSlice and optional relations."""
    state = StateContainer()

    # Register PlayerSlice with given gold
    player = PlayerSlice()
    player.restore({"gold": gold})
    state.register(player)

    # Register SceneSlice so _add_scene_entry works
    scene = SceneSlice()
    scene.restore({})
    state.register(scene)

    # Optionally register RelationSlice with shop_states
    if with_relations and relations_shop_state is not None:
        rel = RelationSlice()
        rel.restore({"shop_states": {character_id: relations_shop_state}})
        state.register(rel)

    cmd_log, executor = _recording_executor(
        succeed=executor_succeed, errors=executor_errors,
    )

    metadata: dict[str, Any] = {"character_id": character_id}
    if shop_stock is not None:
        metadata["role_data"] = {"shop_stock": shop_stock}

    ctx = AgentContext(
        role="npc",
        world=WorldInstance("test"),
        state=state,
        metadata=metadata,
        execute_command=executor,
    )
    return ctx, cmd_log


# ------------------------------------------------------------------
# Test: normal purchase
# ------------------------------------------------------------------


def test_sell_normal_purchase() -> None:
    """Normal purchase: gold deducted and item granted via commands."""
    stock = [_make_shop_stock("iron_sword", base_price=30, unlimited=True)]
    ctx, cmd_log = _make_ctx(gold=100, shop_stock=stock)

    result = asyncio.run(SellToPlayerTool().execute({"item_id": "iron_sword"}, ctx))

    assert result.ok is True
    assert result.metadata["event_type"] == "trade_completed"
    assert result.metadata["item_id"] == "iron_sword"
    assert result.metadata["count"] == 1
    assert result.metadata["price_paid"] == 30
    assert result.metadata["unit_price"] == 30

    # Verify commands issued: npc_service_effect (gold) then pick_up
    assert len(cmd_log) == 2
    gold_cmd = cmd_log[0]
    assert gold_cmd.type == "npc_service_effect"
    assert gold_cmd.params["effect_type"] == "modify_gold"
    assert gold_cmd.params["amount"] == -30

    item_cmd = cmd_log[1]
    assert item_cmd.type == "pick_up"
    assert item_cmd.params["item_id"] == "iron_sword"
    assert item_cmd.params["count"] == 1


def test_sell_multiple_count() -> None:
    """Buying count=3 multiplies price and issues correct commands."""
    stock = [_make_shop_stock("torch", base_price=2, unlimited=True)]
    ctx, cmd_log = _make_ctx(gold=100, shop_stock=stock)

    result = asyncio.run(SellToPlayerTool().execute({"item_id": "torch", "count": 3}, ctx))

    assert result.ok is True
    assert result.metadata["price_paid"] == 6  # 2 * 3
    assert result.metadata["count"] == 3
    assert cmd_log[0].params["amount"] == -6
    assert cmd_log[1].params["count"] == 3


# ------------------------------------------------------------------
# Test: insufficient gold
# ------------------------------------------------------------------


def test_sell_insufficient_gold() -> None:
    """Returns failure when player lacks enough gold."""
    stock = [_make_shop_stock("rare_gem", base_price=200, unlimited=True)]
    ctx, cmd_log = _make_ctx(gold=50, shop_stock=stock)

    result = asyncio.run(SellToPlayerTool().execute({"item_id": "rare_gem"}, ctx))

    assert result.ok is False
    assert result.metadata["status"] == "insufficient_gold"
    assert result.metadata["required"] == 200
    assert result.metadata["current"] == 50
    assert len(cmd_log) == 0  # no commands issued


# ------------------------------------------------------------------
# Test: insufficient stock
# ------------------------------------------------------------------


def test_sell_insufficient_stock() -> None:
    """Returns failure when requested count exceeds remaining stock."""
    stock = [_make_shop_stock("health_potion", base_price=10, unlimited=False, remaining=1)]
    ctx, cmd_log = _make_ctx(gold=100, shop_stock=stock)

    result = asyncio.run(
        SellToPlayerTool().execute({"item_id": "health_potion", "count": 3}, ctx)
    )

    assert result.ok is False
    assert result.metadata["status"] == "insufficient_stock"
    assert result.metadata["available"] == 1
    assert result.metadata["requested"] == 3
    assert len(cmd_log) == 0


def test_sell_last_item_succeeds() -> None:
    """Buying exactly the remaining 1 unit should succeed."""
    stock = [_make_shop_stock("elixir", base_price=25, unlimited=False, remaining=1)]
    ctx, cmd_log = _make_ctx(gold=100, shop_stock=stock)

    result = asyncio.run(SellToPlayerTool().execute({"item_id": "elixir"}, ctx))

    assert result.ok is True
    assert result.metadata["count"] == 1


# ------------------------------------------------------------------
# Test: price_override clamped
# ------------------------------------------------------------------


def test_price_override_too_high_clamped() -> None:
    """price_override above 150% is clamped to 150%."""
    stock = [_make_shop_stock("sword", base_price=40, unlimited=True)]
    ctx, cmd_log = _make_ctx(gold=200, shop_stock=stock)

    # 200% override should clamp to 150% = 60
    result = asyncio.run(
        SellToPlayerTool().execute({"item_id": "sword", "price_override": 80}, ctx)
    )

    assert result.ok is True
    expected_max = math.floor(40 * 1.5)  # 60
    assert result.metadata["unit_price"] == expected_max
    assert result.metadata["price_paid"] == expected_max


def test_price_override_too_low_clamped() -> None:
    """price_override below 50% is clamped to 50%."""
    stock = [_make_shop_stock("sword", base_price=40, unlimited=True)]
    ctx, cmd_log = _make_ctx(gold=200, shop_stock=stock)

    # 10% override should clamp to 50% = 20 (ceil of 40*0.5)
    result = asyncio.run(
        SellToPlayerTool().execute({"item_id": "sword", "price_override": 4}, ctx)
    )

    assert result.ok is True
    expected_min = math.ceil(40 * 0.5)  # 20
    assert result.metadata["unit_price"] == expected_min
    assert result.metadata["price_paid"] == expected_min


def test_price_override_in_range_accepted() -> None:
    """Valid price_override within 50%-150% range is used as-is."""
    stock = [_make_shop_stock("shield", base_price=50, unlimited=True)]
    ctx, cmd_log = _make_ctx(gold=200, shop_stock=stock)

    result = asyncio.run(
        SellToPlayerTool().execute({"item_id": "shield", "price_override": 60}, ctx)
    )

    assert result.ok is True
    assert result.metadata["unit_price"] == 60


# ------------------------------------------------------------------
# Test: unlimited item does not decrement remaining
# ------------------------------------------------------------------


def test_unlimited_item_no_stock_update() -> None:
    """Unlimited items do not trigger a shop_states update."""
    shop_state = {
        "npc_id": "blacksmith",
        "current_stock": [
            {"item_id": "torch", "base_price": 2, "remaining": None},
        ],
        "refresh_on": None,
        "last_refresh_tick": 0,
    }
    stock = [_make_shop_stock("torch", base_price=2, unlimited=True, remaining=None)]
    ctx, cmd_log = _make_ctx(
        gold=100,
        shop_stock=stock,
        with_relations=True,
        relations_shop_state=shop_state,
    )

    result = asyncio.run(SellToPlayerTool().execute({"item_id": "torch"}, ctx))

    assert result.ok is True
    # Verify shop_states remaining is still None (not updated)
    updated_shop = ctx.state.relations.get_shop_state("blacksmith")
    assert updated_shop["current_stock"][0]["remaining"] is None


def test_limited_item_remaining_decremented() -> None:
    """Non-unlimited items have remaining decremented after purchase."""
    shop_state = {
        "npc_id": "blacksmith",
        "current_stock": [
            {"item_id": "magic_arrow", "base_price": 5, "remaining": 10},
        ],
        "refresh_on": None,
        "last_refresh_tick": 0,
    }
    stock = [_make_shop_stock("magic_arrow", base_price=5, unlimited=False, remaining=10)]
    ctx, cmd_log = _make_ctx(
        gold=100,
        shop_stock=stock,
        with_relations=True,
        relations_shop_state=shop_state,
    )

    result = asyncio.run(
        SellToPlayerTool().execute({"item_id": "magic_arrow", "count": 3}, ctx)
    )

    assert result.ok is True
    # remaining should be 10 - 3 = 7
    updated_shop = ctx.state.relations.get_shop_state("blacksmith")
    remaining = updated_shop["current_stock"][0]["remaining"]
    assert remaining == 7


# ------------------------------------------------------------------
# Test: item not in shop_stock
# ------------------------------------------------------------------


def test_sell_item_not_in_stock() -> None:
    """Returns failure when item_id is not in shop_stock."""
    stock = [_make_shop_stock("potion", base_price=10, unlimited=True)]
    ctx, cmd_log = _make_ctx(gold=100, shop_stock=stock)

    result = asyncio.run(
        SellToPlayerTool().execute({"item_id": "nonexistent_item"}, ctx)
    )

    assert result.ok is False
    assert result.metadata["status"] == "item_not_in_stock"
    assert result.metadata["item_id"] == "nonexistent_item"
    assert len(cmd_log) == 0


def test_sell_no_role_data_in_metadata() -> None:
    """Returns failure when no shop_stock in context metadata."""
    ctx, cmd_log = _make_ctx(gold=100, shop_stock=None)  # No shop_stock injected

    result = asyncio.run(SellToPlayerTool().execute({"item_id": "potion"}, ctx))

    assert result.ok is False
    assert result.metadata["status"] == "item_not_in_stock"
    assert len(cmd_log) == 0


# ------------------------------------------------------------------
# Test: free items (price=0)
# ------------------------------------------------------------------


def test_sell_free_item_no_gold_command() -> None:
    """Items with base_price=0 do not issue a gold deduction command."""
    stock = [_make_shop_stock("pamphlet", base_price=0, unlimited=True)]
    ctx, cmd_log = _make_ctx(gold=0, shop_stock=stock)  # player has 0 gold, but item is free

    result = asyncio.run(SellToPlayerTool().execute({"item_id": "pamphlet"}, ctx))

    assert result.ok is True
    assert result.metadata["price_paid"] == 0
    # Only pick_up command should be issued (no gold command)
    assert len(cmd_log) == 1
    assert cmd_log[0].type == "pick_up"


# ------------------------------------------------------------------
# Test: applicable_traits
# ------------------------------------------------------------------


def test_sell_to_player_applicable_traits() -> None:
    """SellToPlayerTool has applicable_traits=['merchant']."""
    tool = SellToPlayerTool()
    assert tool.applicable_traits == ["merchant"]
    assert tool.allowed_roles == ["npc"]


def test_sell_to_player_filtered_by_registry() -> None:
    """Trait-filtered registry: merchant NPC gets sell_to_player, non-merchant doesn't."""
    from app.game_core.narrative import RoleToolRegistry, register_npc_tools

    registry = RoleToolRegistry()
    register_npc_tools(registry)

    # Merchant NPC: should get sell_to_player
    merchant_tools = {t.name for t in registry.get_tools_for("npc", traits=["merchant"])}
    assert "sell_to_player" in merchant_tools

    # NPC with no merchant tag: should NOT get sell_to_player
    non_merchant_tools = {t.name for t in registry.get_tools_for("npc", traits=[])}
    assert "sell_to_player" not in non_merchant_tools
