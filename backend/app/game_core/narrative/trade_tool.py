"""SellToPlayerTool — NPC meta-tool for executing real item trades.

A merchant NPC uses this tool to sell items from its shop stock to the
player.  The tool:

1. Looks up the requested ``item_id`` in ``context.metadata["role_data"]["shop_stock"]``
   (injected by AgentContextBuilder for merchant NPCs).
2. Validates inventory availability (``unlimited`` flag or ``remaining >= count``).
3. Computes the final price, clamping any ``price_override`` to
   ``[base_price * 0.5, base_price * 1.5]``.
4. Checks that the player holds enough gold.
5. Executes two commands:
   - ``npc_service_effect`` with ``effect_type="modify_gold"`` to deduct gold.
   - ``pick_up`` to add the item(s) to player inventory.
6. Updates ``remaining`` in the runtime shop_states via a StateChange if the
   item is not unlimited.
7. Returns a ToolResult with ``event_type="trade_completed"``.

Decision record: adaptive-roaming-milner Phase 3.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from app.game_core.narrative.character_tools import _CharacterTool
from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.models import ToolResult
from app.game_core.rules.models import Command
from app.game_core.state import StateDelta, StateChange

logger = logging.getLogger(__name__)


class SellToPlayerTool(_CharacterTool):
    """Execute a real item trade: deduct gold + grant item from merchant stock.

    The NPC should only call this after the player has confirmed they want to
    buy the item at the stated price.
    """

    @property
    def name(self) -> str:
        return "sell_to_player"

    @property
    def description(self) -> str:
        return (
            "Sell an item from your shop stock to the player. "
            "Supply item_id (must be in your shop_stock), optional count "
            "(default 1), and optional price_override (clamped to 50%-150% "
            "of base price). Only call this after the player has confirmed "
            "they want to buy."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "item_id": {
                    "type": "string",
                    "description": "The item_id to sell (must be in your current shop_stock).",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of units to sell (default 1, minimum 1).",
                },
                "price_override": {
                    "type": "integer",
                    "description": (
                        "Override unit price in gold coins. "
                        "Clamped to [base_price * 0.5, base_price * 1.5]."
                    ),
                },
            },
            "required": ["item_id"],
        }

    @property
    def allowed_roles(self) -> list[str]:
        return ["npc"]

    @property
    def applicable_traits(self) -> list[str]:
        return ["merchant"]

    # ------------------------------------------------------------------
    # Main execute
    # ------------------------------------------------------------------

    async def execute(
        self, params: dict[str, Any], context: AgentContext,
    ) -> ToolResult:
        character_id = self._get_character_id(context)
        if not character_id:
            return self._no_character_id()

        # 1. Resolve item_id param
        item_id = params.get("item_id", "")
        if not isinstance(item_id, str) or not item_id.strip():
            return ToolResult(
                ok=False,
                message="item_id is required.",
                metadata={"status": "invalid_params"},
            )
        item_id = item_id.strip()

        # 2. Resolve count param (default 1, minimum 1)
        count = 1
        raw_count = params.get("count", 1)
        try:
            count = max(1, int(raw_count))
        except (TypeError, ValueError):
            count = 1

        # 3. Look up item in shop_stock injected by context builder
        stock_item = self._find_stock_item(context, item_id)
        if stock_item is None:
            return ToolResult(
                ok=False,
                message=f"商品 '{item_id}' 不在你的库存中。",
                metadata={"status": "item_not_in_stock", "item_id": item_id},
            )

        base_price: int = int(stock_item.get("base_price", 0))
        unlimited: bool = bool(stock_item.get("unlimited", True))
        remaining = stock_item.get("remaining")  # None means unlimited

        # 4. Validate inventory availability
        if not unlimited:
            avail = int(remaining) if remaining is not None else 0
            if avail < count:
                return ToolResult(
                    ok=False,
                    message=f"库存不足：{item_id} 剩余 {avail} 件，无法出售 {count} 件。",
                    metadata={
                        "status": "insufficient_stock",
                        "item_id": item_id,
                        "available": avail,
                        "requested": count,
                    },
                )

        # 5. Compute unit price
        unit_price = base_price
        raw_override = params.get("price_override")
        if raw_override is not None:
            try:
                override = int(raw_override)
                min_price = math.ceil(base_price * 0.5)
                max_price = math.floor(base_price * 1.5)
                unit_price = max(min_price, min(override, max_price))
            except (TypeError, ValueError):
                pass  # keep base_price

        total_price = unit_price * count

        # 6. Check player gold
        if not context.state.has_slice("player"):
            return ToolResult(
                ok=False,
                message="无法验证玩家金币（player 状态切片不可用）。",
                metadata={"status": "state_unavailable"},
            )
        player_gold = int(context.state.player.gold)
        if total_price > 0 and player_gold < total_price:
            return ToolResult(
                ok=False,
                message=f"玩家金币不足：需要 {total_price} 枚金币，当前持有 {player_gold} 枚。",
                metadata={
                    "status": "insufficient_gold",
                    "required": total_price,
                    "current": player_gold,
                },
            )

        # 7. Deduct gold via npc_service_effect command
        if total_price > 0:
            gold_cmd = Command(
                type="npc_service_effect",
                params={"effect_type": "modify_gold", "amount": -total_price},
                source="npc_trade",
            )
            gold_result = context.run_command(gold_cmd)
            if not gold_result.executed:
                err = gold_result.errors[0] if gold_result.errors else "command failed"
                return ToolResult(
                    ok=False,
                    message=f"金币扣除失败：{err}",
                    metadata={"status": "gold_deduction_failed"},
                )

        # 8. Grant item via pick_up command
        pickup_cmd = Command(
            type="pick_up",
            params={"item_id": item_id, "count": count},
            source="npc_trade",
        )
        pickup_result = context.run_command(pickup_cmd)
        if not pickup_result.executed:
            err = pickup_result.errors[0] if pickup_result.errors else "command failed"
            return ToolResult(
                ok=False,
                message=f"物品发放失败：{err}",
                metadata={"status": "item_grant_failed"},
            )

        # 9. Update remaining stock if not unlimited
        if not unlimited and remaining is not None:
            new_remaining = int(remaining) - count
            self._update_stock_remaining(context, character_id, item_id, new_remaining)

        # 10. Write to SceneBus
        item_name = str(stock_item.get("name") or item_id)
        self._add_scene_entry(
            context,
            character_id,
            f"卖出了 {item_name} x{count}，收取 {total_price} 金币",
            tags=["trade", "sell_to_player"],
        )

        return ToolResult(
            ok=True,
            message=f"已售出 {item_name} x{count}，收取 {total_price} 金币",
            metadata={
                "status": "ok",
                "event_type": "trade_completed",
                "character_id": character_id,
                "item_id": item_id,
                "item_name": item_name,
                "count": count,
                "price_paid": total_price,
                "unit_price": unit_price,
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_stock_item(
        context: AgentContext, item_id: str,
    ) -> dict[str, Any] | None:
        """Find an item in the shop_stock injected into context.metadata."""
        metadata = context.metadata if isinstance(context.metadata, dict) else {}
        role_data = metadata.get("role_data")
        if not isinstance(role_data, dict):
            return None
        shop_stock = role_data.get("shop_stock")
        if not isinstance(shop_stock, list):
            return None
        return next(
            (s for s in shop_stock if isinstance(s, dict) and s.get("item_id") == item_id),
            None,
        )

    @staticmethod
    def _update_stock_remaining(
        context: AgentContext,
        npc_id: str,
        item_id: str,
        new_remaining: int,
    ) -> None:
        """Update shop remaining count via StateDelta applied to context state.

        Reads current shop_state, updates the matching item's remaining count,
        and applies via state.apply() using the same path format as other
        shop state handlers.
        """
        if not context.state.has_slice("relations"):
            return
        shop = context.state.relations.get_shop_state(npc_id)
        if not isinstance(shop, dict):
            return
        updated_shop = dict(shop)
        updated_stock = []
        for row in updated_shop.get("current_stock", []):
            if not isinstance(row, dict):
                updated_stock.append(row)
                continue
            if row.get("item_id") == item_id:
                updated_row = dict(row)
                updated_row["remaining"] = new_remaining
                updated_stock.append(updated_row)
            else:
                updated_stock.append(row)
        updated_shop["current_stock"] = updated_stock
        delta = StateDelta(
            changes=[StateChange("relations", "modify", f"shop_states.{npc_id}", updated_shop)],
        )
        try:
            context.state.apply(delta)
        except Exception:  # noqa: BLE001
            logger.warning(
                "SellToPlayerTool: failed to update remaining for %s/%s", npc_id, item_id,
            )
