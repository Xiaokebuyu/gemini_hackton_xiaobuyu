"""ItemDesigner sub-system — dynamic item / reward design.

Handles: design_reward, curate_shop.

curate_shop: adjust a merchant's shop inventory (add/remove/restock items).
design_reward: deferred — not yet implemented.

Decision record: D-P36 (narrative.md)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar

from app.game_core.planning.subsystem import PlannerEvent, SubSystemResult
from app.game_core.state import StateChange

if TYPE_CHECKING:
    from app.game_core.orchestration.settlement import SettlementContext

logger = logging.getLogger(__name__)


class ItemDesignerSubSystem:
    """PlannerSubSystem for item and reward design directives."""

    _HANDLES: ClassVar[frozenset[str]] = frozenset({"design_reward", "curate_shop"})

    def __init__(self, *, sse_collector: list | None = None) -> None:
        self._sse_collector = sse_collector

    # ------------------------------------------------------------------
    # PlannerSubSystem protocol
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "item_designer"

    @property
    def handles(self) -> frozenset[str]:
        return self._HANDLES

    def accepts_event(self, event: PlannerEvent) -> bool:
        return event.kind in {
            "shop_refreshed",
            "shop_inventory_changed",
            "quest_completed",
        }

    async def evaluate(
        self, event: PlannerEvent, context: Any
    ) -> SubSystemResult:
        return SubSystemResult()

    def apply_directive(
        self,
        kind: str,
        payload: dict[str, Any],
        context: Any,
        *,
        current_tick: int,
    ) -> bool:
        if kind == "curate_shop":
            return self._apply_curate_shop(payload, context, current_tick=current_tick)
        logger.debug("ItemDesignerSubSystem: '%s' not yet implemented", kind)
        return False

    # ------------------------------------------------------------------
    # curate_shop
    # ------------------------------------------------------------------

    def _apply_curate_shop(
        self,
        payload: dict[str, Any],
        context: "SettlementContext",
        *,
        current_tick: int,
    ) -> bool:
        """Apply a curate_shop directive to adjust a merchant's inventory.

        payload schema:
            npc_id: str
            add_items: list[{item_id, count?, price_override?}]
            remove_items: list[str]  # item_ids to remove
            restock_items: list[{item_id, count}]

        Returns True if the shop was successfully updated, False otherwise.
        """
        npc_id = str(payload.get("npc_id", "")).strip()
        if not npc_id:
            logger.debug("curate_shop: missing npc_id")
            return False

        # Validate NPC exists in the world (only when character registry is loaded)
        if context.world.has_registry("characters"):
            if context.world.characters.get(npc_id) is None:
                logger.debug("curate_shop: unknown npc_id '%s'", npc_id)
                return False

        # Shop must already be initialized by EconomyHandler — we do not
        # create a shop state here; that is EconomyHandler's responsibility.
        if not context.state.has_slice("relations"):
            logger.debug("curate_shop: no relations slice")
            return False
        raw_shop = context.state.relations.shop_states.get(npc_id)
        if not isinstance(raw_shop, dict):
            logger.debug("curate_shop: shop not yet initialized for npc '%s'", npc_id)
            return False

        # Work on a mutable copy so we don't corrupt the live state
        shop_state: dict[str, Any] = dict(raw_shop)
        current_stock: list[dict[str, Any]] = [
            dict(row) for row in shop_state.get("current_stock", [])
            if isinstance(row, dict)
        ]

        # --- remove_items --------------------------------------------------
        remove_ids: set[str] = set()
        for entry in payload.get("remove_items", []):
            if isinstance(entry, str) and entry.strip():
                remove_ids.add(entry.strip())
        if remove_ids:
            current_stock = [
                row for row in current_stock
                if row.get("item_id") not in remove_ids
            ]

        # --- add_items -----------------------------------------------------
        for entry in payload.get("add_items", []):
            if not isinstance(entry, dict):
                continue
            item_id = str(entry.get("item_id", "")).strip()
            if not item_id:
                continue
            # Resolve base_price from item registry, allow price_override
            base_price: int | None = None
            price_override = entry.get("price_override")
            if price_override is not None:
                try:
                    base_price = int(price_override)
                except (TypeError, ValueError):
                    pass
            if base_price is None and context.world.has_registry("items"):
                item_template = context.world.items.get(item_id)
                if item_template is not None and item_template.base_price is not None:
                    base_price = item_template.base_price
            # Parse count — None means unlimited (no "remaining" key)
            raw_count = entry.get("count")
            remaining: int | None = None
            if raw_count is not None:
                try:
                    remaining = int(raw_count)
                except (TypeError, ValueError):
                    remaining = None
            new_row: dict[str, Any] = {
                "item_id": item_id,
                "base_price": base_price if base_price is not None else 0,
                "source": "curated",
            }
            if remaining is not None:
                new_row["remaining"] = remaining
            current_stock.append(new_row)

        # --- restock_items -------------------------------------------------
        for entry in payload.get("restock_items", []):
            if not isinstance(entry, dict):
                continue
            item_id = str(entry.get("item_id", "")).strip()
            if not item_id:
                continue
            try:
                new_count = int(entry.get("count", 0))
            except (TypeError, ValueError):
                continue
            for row in current_stock:
                if row.get("item_id") == item_id:
                    row["remaining"] = new_count
                    break

        # Write back to relations slice
        shop_state["current_stock"] = current_stock
        context.state.relations.shop_states[npc_id] = shop_state
        context.state.relations._dirty = True
        context.record_change(
            StateChange("relations", "modify", f"shop_states.{npc_id}", shop_state)
        )

        # SSE notification
        if self._sse_collector is not None:
            from app.game_core.orchestration.models import SSEEvent
            self._sse_collector.append(
                SSEEvent(event_type="shop_curated", payload={"npc_id": npc_id})
            )

        logger.debug(
            "curate_shop: updated shop for '%s' at tick %d (stock_count=%d)",
            npc_id,
            current_tick,
            len(current_stock),
        )
        return True
