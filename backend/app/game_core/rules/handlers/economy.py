"""EconomyHandler implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content import WorldInstance
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.handler_utils import (
    coerce_float,
    coerce_int,
    get_non_empty_string,
    handler_success,
    handler_success_no_delta,
)
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer


class EconomyHandler(StaticCommandHandler):
    COMMAND_TYPES = ("trade_buy", "trade_sell", "refresh_shop")

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if cmd.type == "trade_buy":
            return self._validate_trade_buy(cmd, state, world)
        if cmd.type == "trade_sell":
            return self._validate_trade_sell(cmd, state, world)
        if cmd.type == "refresh_shop":
            return self._validate_refresh_shop(cmd, state, world)
        return ValidationResult(ok=False, reason=f"unsupported command: {cmd.type}")

    def compute(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        validation = self.validate(cmd, state, world)
        if not validation.ok:
            return ExecuteResult.error(validation.reason or "validation failed")

        if cmd.type == "trade_buy":
            return self._compute_trade_buy(cmd, state, world)
        if cmd.type == "trade_sell":
            return self._compute_trade_sell(cmd, state, world)
        if cmd.type == "refresh_shop":
            return self._compute_refresh_shop(cmd, state, world)
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _validate_trade_buy(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")
        if not state.has_slice("relations"):
            return ValidationResult(ok=False, reason="relations slice is required")
        if not world.has_registry("characters"):
            return ValidationResult(ok=False, reason="characters registry is required")

        seller_npc = get_non_empty_string(cmd.params, "seller_npc")
        if seller_npc is None:
            return ValidationResult(
                ok=False,
                reason="seller_npc must be a non-empty string",
            )
        item_id = get_non_empty_string(cmd.params, "item_id")
        if item_id is None:
            return ValidationResult(ok=False, reason="item_id must be a non-empty string")
        count = coerce_int(cmd.params.get("count", 1))
        if count is None or count < 1:
            return ValidationResult(ok=False, reason="count must be an integer >= 1")
        buyer_check = self._validate_player_actor(cmd.params, state, "buyer")
        if buyer_check is not None:
            return buyer_check

        merchant = world.characters.get(seller_npc)
        if merchant is None:
            return ValidationResult(ok=False, reason=f"unknown character: {seller_npc}")

        shop_state, _ = self._resolve_or_initialize_shop_state(seller_npc, merchant, state, world)
        if shop_state is None:
            return ValidationResult(
                ok=False,
                reason=f"seller has no shop inventory: {seller_npc}",
            )

        stock_index = self._find_stock_index(shop_state, item_id)
        if stock_index is None:
            return ValidationResult(ok=False, reason=f"item not sold: {item_id}")

        stock_item = shop_state["current_stock"][stock_index]
        remaining = stock_item.get("remaining")
        if isinstance(remaining, int) and remaining < count:
            return ValidationResult(ok=False, reason=f"not enough stock: {item_id}")

        unit_price = self._resolve_buy_unit_price(stock_item, merchant, state, seller_npc)
        total_price = unit_price * count
        if int(state.player.gold) < total_price:
            return ValidationResult(ok=False, reason="not enough gold")
        return ValidationResult(ok=True)

    def _validate_trade_sell(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")
        if not state.has_slice("relations"):
            return ValidationResult(ok=False, reason="relations slice is required")
        if not world.has_registry("characters"):
            return ValidationResult(ok=False, reason="characters registry is required")

        buyer_npc = get_non_empty_string(cmd.params, "buyer_npc")
        if buyer_npc is None:
            return ValidationResult(ok=False, reason="buyer_npc must be a non-empty string")
        item_id = get_non_empty_string(cmd.params, "item_id")
        if item_id is None:
            return ValidationResult(ok=False, reason="item_id must be a non-empty string")
        count = coerce_int(cmd.params.get("count", 1))
        if count is None or count < 1:
            return ValidationResult(ok=False, reason="count must be an integer >= 1")
        seller_check = self._validate_player_actor(cmd.params, state, "seller")
        if seller_check is not None:
            return seller_check
        if state.player.get_item_count(item_id) < count:
            return ValidationResult(ok=False, reason=f"not enough items: {item_id}")
        if world.characters.get(buyer_npc) is None:
            return ValidationResult(ok=False, reason=f"unknown character: {buyer_npc}")
        return ValidationResult(ok=True)

    def _validate_refresh_shop(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not state.has_slice("relations"):
            return ValidationResult(ok=False, reason="relations slice is required")
        if not world.has_registry("characters"):
            return ValidationResult(ok=False, reason="characters registry is required")
        npc_id = get_non_empty_string(cmd.params, "npc_id")
        if npc_id is None:
            return ValidationResult(ok=False, reason="npc_id must be a non-empty string")
        if world.characters.get(npc_id) is None:
            return ValidationResult(ok=False, reason=f"unknown character: {npc_id}")
        return ValidationResult(ok=True)

    def _compute_trade_buy(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        seller_npc = str(cmd.params["seller_npc"]).strip()
        item_id = str(cmd.params["item_id"]).strip()
        count = int(cmd.params.get("count", 1))
        merchant = world.characters.get(seller_npc)
        if merchant is None:
            return ExecuteResult.error(f"unknown seller: {seller_npc}")
        shop_state, initialized = self._resolve_or_initialize_shop_state(
            seller_npc,
            merchant,
            state,
            world,
        )
        if shop_state is None:
            return ExecuteResult.error(f"seller has no shop inventory: {seller_npc}")

        stock_index = self._find_stock_index(shop_state, item_id)
        if stock_index is None:
            return ExecuteResult.error(f"item not sold: {item_id}")

        stock_item = dict(shop_state["current_stock"][stock_index])
        unit_price = self._resolve_buy_unit_price(stock_item, merchant, state, seller_npc)
        total_price = unit_price * count
        remaining = stock_item.get("remaining")
        if isinstance(remaining, int):
            stock_item["remaining"] = max(0, remaining - count)
        shop_state["current_stock"][stock_index] = stock_item

        inventory = self._player_inventory_snapshot(state)
        self._add_to_inventory_snapshot(inventory, item_id, count)
        changes = [
            StateChange("player", "add", "gold", -total_price),
            StateChange("player", "set", "inventory", inventory),
            StateChange("relations", "modify", f"shop_states.{seller_npc}", shop_state),
        ]
        return handler_success(
            "economy",
            "trade_buy",
            changes=changes,
            time_cost=1.0 / 6.0,
            metadata={
                "status": "purchased",
                "seller_npc": seller_npc,
                "item_id": item_id,
                "count": count,
                "unit_price": unit_price,
                "total_price": total_price,
                "shop_initialized": initialized,
            },
            omit_empty_delta=False,
        )

    def _compute_trade_sell(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        buyer_npc = str(cmd.params["buyer_npc"]).strip()
        item_id = str(cmd.params["item_id"]).strip()
        count = int(cmd.params.get("count", 1))
        merchant = world.characters.get(buyer_npc)
        if merchant is None:
            return ExecuteResult.error(f"unknown buyer: {buyer_npc}")

        inventory = self._player_inventory_snapshot(state)
        updated_inventory = self._remove_from_inventory_snapshot(inventory, item_id, count)
        unit_price = self._resolve_sell_unit_price(item_id, merchant, world)
        total_price = unit_price * count

        shop_state, initialized = self._resolve_or_initialize_shop_state(
            buyer_npc,
            merchant,
            state,
            world,
        )
        shop_updated = False
        if shop_state is not None:
            self._merge_buyback_row(
                shop_state,
                item_id,
                count,
                self._base_price_for_item(item_id, None, world),
            )
            shop_updated = True

        changes = [
            StateChange("player", "add", "gold", total_price),
            StateChange("player", "set", "inventory", updated_inventory),
        ]
        if shop_updated and shop_state is not None:
            changes.append(
                StateChange(
                    "relations",
                    "modify",
                    f"shop_states.{buyer_npc}",
                    shop_state,
                )
            )

        return handler_success(
            "economy",
            "trade_sell",
            changes=changes,
            time_cost=1.0 / 6.0,
            metadata={
                "status": "sold",
                "buyer_npc": buyer_npc,
                "item_id": item_id,
                "count": count,
                "unit_price": unit_price,
                "total_price": total_price,
                "shop_initialized": initialized,
                "shop_updated": shop_updated,
            },
            omit_empty_delta=False,
        )

    def _compute_refresh_shop(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        npc_id = str(cmd.params["npc_id"]).strip()
        merchant = world.characters.get(npc_id)
        if merchant is None:
            return handler_success_no_delta(
                "economy",
                "refresh_shop",
                metadata={
                    "status": "noop",
                    "npc_id": npc_id,
                    "stock_count": 0,
                    "rotating_count": 0,
                    "last_refresh_tick": self._current_tick(state),
                },
            )
        shop_state = self._refresh_shop_state(
            npc_id,
            merchant,
            state,
            world,
            self._shop_state_snapshot(state.relations.shop_states.get(npc_id), npc_id),
        )
        if shop_state is None:
            return handler_success_no_delta(
                "economy",
                "refresh_shop",
                metadata={
                    "status": "noop",
                    "npc_id": npc_id,
                    "stock_count": 0,
                    "rotating_count": 0,
                    "last_refresh_tick": self._current_tick(state),
                },
            )

        stock_count = len(shop_state.get("current_stock", []))
        rotating_count = sum(
            1
            for row in shop_state.get("current_stock", [])
            if isinstance(row, Mapping) and row.get("source") == "rotating"
        )
        return handler_success(
            "economy",
            "refresh_shop",
            changes=[
                StateChange("relations", "modify", f"shop_states.{npc_id}", shop_state),
            ],
            metadata={
                "status": "refreshed",
                "npc_id": npc_id,
                "stock_count": stock_count,
                "rotating_count": rotating_count,
                "last_refresh_tick": shop_state["last_refresh_tick"],
            },
            omit_empty_delta=False,
        )

    def _resolve_or_initialize_shop_state(
        self,
        npc_id: str,
        merchant: Any,
        state: StateContainer,
        world: WorldInstance,
    ) -> tuple[dict[str, Any] | None, bool]:
        existing = self._shop_state_snapshot(state.relations.shop_states.get(npc_id), npc_id)
        if existing is not None:
            return existing, False
        refreshed = self._refresh_shop_state(npc_id, merchant, state, world, None)
        if refreshed is None:
            return None, False
        return refreshed, True

    def _refresh_shop_state(
        self,
        npc_id: str,
        merchant: Any,
        state: StateContainer,
        world: WorldInstance,
        existing_state: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        shop_inventory = self._merchant_shop_inventory(merchant)
        if shop_inventory is None:
            return None

        current_tick = self._current_tick(state)
        previous_base_rows: dict[str, dict[str, Any]] = {}
        if existing_state is not None:
            for row in existing_state.get("current_stock", []):
                if not isinstance(row, Mapping):
                    continue
                if row.get("source") != "base":
                    continue
                item_id = get_non_empty_string(row, "item_id")
                if item_id is not None:
                    previous_base_rows[item_id] = dict(row)

        current_stock: list[dict[str, Any]] = []
        base_pool = shop_inventory.base_pool
        for entry in base_pool:
            normalized = self._normalize_shop_entry(
                entry,
                "base",
                world,
                previous_row=previous_base_rows.get(
                    get_non_empty_string(entry, "item_id") or ""
                ),
            )
            if normalized is not None:
                current_stock.append(normalized)

        rotating_pool = shop_inventory.rotating_pool
        rotating_slots = shop_inventory.rotating_slots
        current_stock.extend(
            self._select_rotating_entries(
                rotating_pool,
                rotating_slots,
                state,
                world,
            )
        )

        refresh_on: Any = getattr(merchant, "refresh_on", None)
        if refresh_on is None:
            refresh_on = shop_inventory.refresh_on
        if isinstance(refresh_on, list):
            refresh_payload: Any = [str(item) for item in refresh_on]
        elif isinstance(refresh_on, str):
            refresh_payload = refresh_on
        else:
            refresh_payload = None

        return {
            "npc_id": npc_id,
            "current_stock": current_stock,
            "refresh_on": refresh_payload,
            "last_refresh_tick": current_tick,
        }

    def _normalize_shop_entry(
        self,
        entry: Any,
        source: str,
        world: WorldInstance,
        *,
        previous_row: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not isinstance(entry, Mapping):
            return None
        item_id = get_non_empty_string(entry, "item_id")
        if item_id is None:
            return None

        count = coerce_int(entry.get("count", 1))
        if count is None or count < 1:
            return None
        unlimited = bool(entry.get("unlimited", False))
        restock = bool(entry.get("restock", True))

        base_price = self._base_price_for_item(item_id, entry, world)
        price_override = self._coerce_non_negative_int(entry.get("price"))
        if unlimited:
            remaining: int | None = None
        elif (
            source == "base"
            and restock is False
            and isinstance(previous_row, Mapping)
            and previous_row.get("item_id") == item_id
        ):
            previous_remaining = previous_row.get("remaining")
            if previous_remaining is None:
                remaining = None
            else:
                previous_value = coerce_int(previous_remaining)
                remaining = previous_value if previous_value is not None else count
        else:
            remaining = count

        return {
            "item_id": item_id,
            "remaining": remaining,
            "unlimited": unlimited,
            "base_price": base_price,
            "price_override": price_override,
            "source": source,
        }

    def _select_rotating_entries(
        self,
        rotating_pool: Any,
        rotating_slots: int,
        state: StateContainer,
        world: WorldInstance,
    ) -> list[dict[str, Any]]:
        if not isinstance(rotating_pool, list) or rotating_slots <= 0:
            return []

        player_level = int(state.player.level) if state.has_slice("player") else 1
        eligible: list[dict[str, Any]] = []
        for entry in rotating_pool:
            if not isinstance(entry, Mapping):
                continue
            min_player_level = coerce_int(entry.get("min_player_level"))
            if min_player_level is not None and player_level < min_player_level:
                continue
            normalized = self._normalize_shop_entry(entry, "rotating", world)
            if normalized is not None:
                eligible.append(normalized)
        if not eligible:
            return []

        count = min(rotating_slots, len(eligible))
        start = self._current_tick(state) % len(eligible)
        selected: list[dict[str, Any]] = []
        for offset in range(count):
            index = (start + offset) % len(eligible)
            selected.append(dict(eligible[index]))
        return selected

    def _resolve_buy_unit_price(
        self,
        stock_item: Mapping[str, Any],
        merchant: Any,
        state: StateContainer,
        seller_npc: str,
    ) -> int:
        price_override = self._coerce_non_negative_int(stock_item.get("price_override"))
        if price_override is not None:
            return price_override

        base_price = self._coerce_non_negative_int(stock_item.get("base_price")) or 0
        shop_inventory = self._merchant_shop_inventory(merchant)
        sell_markup = coerce_float(getattr(merchant, "sell_markup", None))
        if sell_markup is None and shop_inventory is not None:
            sell_markup = coerce_float(shop_inventory.sell_markup)
        if sell_markup is None or sell_markup < 0:
            sell_markup = 1.0

        approval = int(
            state.relations.npc_dispositions.get(seller_npc, {}).get("approval", 0)
        )
        discount = self._approval_discount(approval)
        return max(1, int(round(base_price * sell_markup * discount)))

    def _resolve_sell_unit_price(
        self,
        item_id: str,
        merchant: Any,
        world: WorldInstance,
    ) -> int:
        base_price = self._base_price_for_item(item_id, None, world)
        shop_inventory = self._merchant_shop_inventory(merchant)
        buy_rate = coerce_float(getattr(merchant, "buy_rate", None))
        if buy_rate is None and shop_inventory is not None:
            buy_rate = coerce_float(shop_inventory.buy_rate)
        if buy_rate is None:
            buy_rate = 0.5
        return max(1, int(round(base_price * buy_rate)))

    def _merge_buyback_row(
        self,
        shop_state: dict[str, Any],
        item_id: str,
        count: int,
        base_price: int,
    ) -> None:
        stock = self._normalize_current_stock(shop_state.get("current_stock"))
        for index, row in enumerate(stock):
            if row.get("item_id") != item_id:
                continue
            remaining = row.get("remaining")
            if remaining is None:
                shop_state["current_stock"] = stock
                return
            row["remaining"] = int(remaining) + count
            stock[index] = row
            shop_state["current_stock"] = stock
            return
        stock.append(
            {
                "item_id": item_id,
                "remaining": count,
                "unlimited": False,
                "base_price": base_price,
                "price_override": None,
                "source": "buyback",
            }
        )
        shop_state["current_stock"] = stock

    def _shop_state_snapshot(
        self,
        raw_state: Any,
        npc_id: str,
    ) -> dict[str, Any] | None:
        if not isinstance(raw_state, Mapping):
            return None
        refresh_on = raw_state.get("refresh_on")
        if isinstance(refresh_on, list):
            refresh_payload: Any = [str(item) for item in refresh_on]
        elif isinstance(refresh_on, str):
            refresh_payload = refresh_on
        else:
            refresh_payload = None
        return {
            "npc_id": get_non_empty_string(raw_state, "npc_id") or npc_id,
            "current_stock": self._normalize_current_stock(raw_state.get("current_stock")),
            "refresh_on": refresh_payload,
            "last_refresh_tick": coerce_int(raw_state.get("last_refresh_tick")) or 0,
        }

    def _normalize_current_stock(self, raw_stock: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_stock, list):
            return []
        stock: list[dict[str, Any]] = []
        for entry in raw_stock:
            if isinstance(entry, Mapping):
                stock.append(dict(entry))
        return stock

    def _find_stock_index(self, shop_state: Mapping[str, Any], item_id: str) -> int | None:
        raw_stock = shop_state.get("current_stock", [])
        if not isinstance(raw_stock, list):
            return None
        for index, entry in enumerate(raw_stock):
            if not isinstance(entry, Mapping):
                continue
            if get_non_empty_string(entry, "item_id") == item_id:
                return index
        return None

    def _merchant_shop_inventory(
        self,
        merchant: Any,
    ) -> Any:
        return getattr(merchant, "shop_inventory", None)

    def _base_price_for_item(
        self,
        item_id: str,
        entry: Mapping[str, Any] | None,
        world: WorldInstance,
    ) -> int:
        if entry is not None:
            entry_price = self._coerce_non_negative_int(entry.get("base_price"))
            if entry_price is not None:
                return entry_price

        item_template = None
        if world.has_registry("items"):
            item_template = world.items.get(item_id)
        if item_template is not None:
            if item_template.base_price is not None:
                return item_template.base_price
            if item_template.price is not None:
                return item_template.price

        if entry is not None:
            entry_fallback = self._coerce_non_negative_int(entry.get("price"))
            if entry_fallback is not None:
                return entry_fallback
        return 0

    def _player_inventory_snapshot(self, state: StateContainer) -> list[dict[str, Any]]:
        snapshot = state.player.snapshot().get("inventory", [])
        if not isinstance(snapshot, list):
            return []
        inventory: list[dict[str, Any]] = []
        for item in snapshot:
            if isinstance(item, Mapping):
                inventory.append(dict(item))
        return inventory

    def _add_to_inventory_snapshot(
        self,
        inventory: list[dict[str, Any]],
        item_id: str,
        count: int,
        tags: list[str] | None = None,
    ) -> None:
        for item in inventory:
            if item.get("item_id") != item_id:
                continue
            item["count"] = int(item.get("count", 0)) + count
            if tags:
                existing_tags = item.get("tags", [])
                normalized_tags = [str(tag) for tag in existing_tags] if isinstance(existing_tags, list) else []
                item["tags"] = sorted(set([*normalized_tags, *tags]))
            return
        inventory.append(
            {
                "item_id": item_id,
                "count": count,
                "tags": list(tags or []),
            }
        )

    def _remove_from_inventory_snapshot(
        self,
        inventory: list[dict[str, Any]],
        item_id: str,
        count: int,
    ) -> list[dict[str, Any]]:
        remaining_to_remove = count
        updated: list[dict[str, Any]] = []
        for item in inventory:
            if item.get("item_id") != item_id or remaining_to_remove <= 0:
                updated.append(item)
                continue
            current = int(item.get("count", 0))
            next_count = current - remaining_to_remove
            if next_count > 0:
                next_item = dict(item)
                next_item["count"] = next_count
                updated.append(next_item)
                remaining_to_remove = 0
                continue
            remaining_to_remove = max(0, -next_count)
        return updated

    def _validate_player_actor(
        self,
        params: Mapping[str, Any],
        state: StateContainer,
        key: str,
    ) -> ValidationResult | None:
        if key not in params:
            return None
        actor = get_non_empty_string(params, key)
        if actor is None:
            return ValidationResult(ok=False, reason=f"{key} must be a non-empty string")
        player_character_id = get_non_empty_string({"character_id": state.player.character_id}, "character_id")
        if actor == "player":
            return None
        if player_character_id is not None and actor == player_character_id:
            return None
        return ValidationResult(
            ok=False,
            reason=f"{key} must reference the current player",
        )

    @staticmethod
    def _approval_discount(approval: int) -> float:
        if approval >= 80:
            return 0.70
        if approval >= 50:
            return 0.80
        if approval >= 20:
            return 0.90
        return 1.00

    @staticmethod
    def _current_tick(state: StateContainer) -> int:
        if state.has_slice("time"):
            return int(state.time.absolute_tick())
        return 0

    @staticmethod
    def _coerce_non_negative_int(value: Any) -> int | None:
        coerced = coerce_int(value)
        if coerced is None or coerced < 0:
            return None
        return coerced
