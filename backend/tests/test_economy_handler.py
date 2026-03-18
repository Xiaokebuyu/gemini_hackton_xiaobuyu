"""Tests for EconomyHandler."""

from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.content.registries import CharacterRegistry, ItemRegistry
from app.game_core.orchestration.defaults import build_default_action_dispatcher
from app.game_core.orchestration.models import StructuredAction
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import EconomyHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import PlayerSlice, RelationSlice, TimeSlice


def _make_world() -> WorldInstance:
    world = WorldInstance("test_world")

    items = ItemRegistry()
    items.load(
        {
            "potion": {"id": "potion", "base_price": 10},
            "sword": {"id": "sword", "base_price": 100},
            "bomb": {"id": "bomb", "base_price": 35},
            "elixir": {"id": "elixir", "base_price": 50},
        }
    )
    world.register(items)

    characters = CharacterRegistry()
    characters.load(
        {
            "merchant": {
                "id": "merchant",
                "sell_markup": 1.1,
                "buy_rate": 0.4,
                "shop_inventory": {
                    "base_pool": [
                        {"item_id": "potion", "count": 2},
                        {"item_id": "sword", "count": 1},
                    ],
                    "rotating_pool": [
                        {"item_id": "elixir", "count": 1},
                        {"item_id": "bomb", "count": 1},
                    ],
                    "rotating_slots": 1,
                },
            },
            "plain_vendor": {"id": "plain_vendor"},
        }
    )
    world.register(characters)
    return world


def _make_state(
    *,
    gold: int = 100,
    inventory: list[dict] | None = None,
    day: int = 1,
    slot: int = 9,
) -> StateContainer:
    state = StateContainer()

    player = PlayerSlice()
    player.restore(
        {
            "character_id": "pc_1",
            "gold": gold,
            "inventory": inventory or [],
        }
    )
    state.register(player)

    relations = RelationSlice()
    relations.restore({})
    state.register(relations)

    time_slice = TimeSlice()
    time_slice.restore({"day": day, "slot": slot})
    state.register(time_slice)

    return state


def _make_engine() -> RulesEngine:
    engine = RulesEngine()
    engine.register(EconomyHandler())
    return engine


def _apply(result, state: StateContainer) -> None:
    if result.delta is None:
        return
    state.apply(result.delta)


def _find_stock(state: StateContainer, npc_id: str, item_id: str) -> dict | None:
    stock = state.relations.shop_states.get(npc_id, {}).get("current_stock", [])
    if not isinstance(stock, list):
        return None
    for row in stock:
        if isinstance(row, dict) and row.get("item_id") == item_id:
            return row
    return None


class TestEconomyHandler:
    def test_default_action_dispatcher_routes_trade_buy(self) -> None:
        dispatcher = build_default_action_dispatcher()

        command = dispatcher.dispatch(
            StructuredAction(action_type="trade_buy", params={"seller_npc": "merchant"})
        )

        assert command is not None
        assert command.type == "trade_buy"

    def test_refresh_shop_builds_deterministic_stock(self) -> None:
        state = _make_state(slot=9)
        result = _make_engine().execute(
            Command(type="refresh_shop", params={"npc_id": "merchant"}),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.metadata["status"] == "refreshed"
        assert result.metadata["rotating_count"] == 1
        _apply(result, state)
        current_stock = state.relations.shop_states["merchant"]["current_stock"]
        assert current_stock[-1]["item_id"] == "bomb"

    def test_refresh_shop_without_shop_inventory_is_noop(self) -> None:
        result = _make_engine().execute(
            Command(type="refresh_shop", params={"npc_id": "plain_vendor"}),
            _make_state(),
            _make_world(),
        )

        assert result.executed is True
        assert result.delta is None
        assert result.metadata["status"] == "noop"

    def test_trade_buy_auto_initializes_shop_state_and_updates_gold_and_stock(self) -> None:
        state = _make_state(gold=100)
        result = _make_engine().execute(
            Command(
                type="trade_buy",
                params={"seller_npc": "merchant", "item_id": "potion", "count": 1},
            ),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.time_cost == 1.0 / 6.0
        assert result.metadata["status"] == "purchased"
        assert result.metadata["shop_initialized"] is True
        assert result.metadata["unit_price"] == 11
        assert result.metadata["total_price"] == 11
        _apply(result, state)
        assert state.player.gold == 89
        assert state.player.get_item_count("potion") == 1
        assert _find_stock(state, "merchant", "potion")["remaining"] == 1

    def test_trade_buy_rejects_when_gold_or_stock_is_insufficient(self) -> None:
        world = _make_world()

        no_gold = _make_engine().execute(
            Command(
                type="trade_buy",
                params={"seller_npc": "merchant", "item_id": "potion", "count": 1},
            ),
            _make_state(gold=5),
            world,
        )
        no_stock = _make_engine().execute(
            Command(
                type="trade_buy",
                params={"seller_npc": "merchant", "item_id": "potion", "count": 3},
            ),
            _make_state(gold=100),
            world,
        )

        assert no_gold.executed is False
        assert no_gold.errors == ["not enough gold"]
        assert no_stock.executed is False
        assert no_stock.errors == ["not enough stock: potion"]

    def test_trade_sell_updates_inventory_gold_and_buyback_state(self) -> None:
        state = _make_state(
            gold=0,
            inventory=[{"item_id": "sword", "count": 1, "tags": []}],
        )
        result = _make_engine().execute(
            Command(
                type="trade_sell",
                params={"buyer_npc": "merchant", "item_id": "sword", "count": 1},
            ),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.time_cost == 1.0 / 6.0
        assert result.metadata["status"] == "sold"
        assert result.metadata["unit_price"] == 40
        assert result.metadata["total_price"] == 40
        assert result.metadata["shop_initialized"] is True
        assert result.metadata["shop_updated"] is True
        _apply(result, state)
        assert state.player.gold == 40
        assert state.player.inventory == []
        assert _find_stock(state, "merchant", "sword")["remaining"] == 2

    def test_trade_sell_allows_merchants_without_shop_inventory(self) -> None:
        state = _make_state(
            gold=0,
            inventory=[{"item_id": "potion", "count": 1, "tags": []}],
        )
        result = _make_engine().execute(
            Command(
                type="trade_sell",
                params={"buyer_npc": "plain_vendor", "item_id": "potion", "count": 1},
            ),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.metadata["shop_initialized"] is False
        assert result.metadata["shop_updated"] is False
        _apply(result, state)
        assert state.player.gold == 5
        assert state.player.inventory == []
        assert "plain_vendor" not in state.relations.shop_states

    def test_trade_sell_applies_approval_sell_bonus(self) -> None:
        """High approval (>=80) should give 30% sell bonus over base sell price."""
        state = StateContainer()

        player = PlayerSlice()
        player.restore(
            {
                "character_id": "pc_1",
                "gold": 0,
                "inventory": [{"item_id": "sword", "count": 1, "tags": []}],
            }
        )
        state.register(player)

        relations = RelationSlice()
        relations.restore(
            {
                "npc_dispositions": {
                    "merchant": {"approval": 80},
                }
            }
        )
        state.register(relations)

        from app.game_core.state.slices import TimeSlice
        time_slice = TimeSlice()
        time_slice.restore({"day": 1, "slot": 9})
        state.register(time_slice)

        result = _make_engine().execute(
            Command(
                type="trade_sell",
                params={"buyer_npc": "merchant", "item_id": "sword", "count": 1},
            ),
            state,
            _make_world(),
        )

        assert result.executed is True
        # base_price=100, buy_rate=0.4, sell_bonus=1.30 → round(100 * 0.4 * 1.30) = 52
        assert result.metadata["unit_price"] == 52
        _apply(result, state)
        assert state.player.gold == 52

    def test_trade_sell_no_approval_bonus_when_approval_is_zero(self) -> None:
        """approval=0 → sell_bonus=1.0 → price is unchanged from base calculation."""
        state = _make_state(
            gold=0,
            inventory=[{"item_id": "sword", "count": 1, "tags": []}],
        )
        result = _make_engine().execute(
            Command(
                type="trade_sell",
                params={"buyer_npc": "merchant", "item_id": "sword", "count": 1},
            ),
            state,
            _make_world(),
        )

        assert result.executed is True
        # base_price=100, buy_rate=0.4, sell_bonus=1.0 → round(100 * 0.4 * 1.0) = 40
        assert result.metadata["unit_price"] == 40
