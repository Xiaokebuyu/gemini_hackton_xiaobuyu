"""Tests for Phase 3.6: Shop activation — blacksmith data + curate_shop directive.

Tests cover:
- characters.json blacksmith data (shop_inventory, tags)
- maps.json blacksmith_shop resident_npcs
- ItemDesignerSubSystem.apply_directive("curate_shop") — add/remove/restock
- Guard clauses (unknown npc, no shop state)
- SSE notification (shop_curated)
- NarrativePlannerHook._SUPPORTED_DIRECTIVES membership
- _extract_merchant_data bug fix (current_stock / base_price / remaining)

Decision record: D-P36 (narrative.md)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.content.registries.characters import CharacterRegistry
from app.game_core.narrative.context_builder import (
    _extract_merchant_data,
    _format_role_constraint_block,
)
from app.game_core.orchestration.hooks.narrative_planner import NarrativePlannerHook
from app.game_core.orchestration.models import SSEEvent
from app.game_core.orchestration.scene_bus import SceneBus
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.planning.item_designer import ItemDesignerSubSystem
from app.game_core.rules import RulesEngine
from app.game_core.rules.defaults import register_default_rules_handlers
from app.game_core.state import StateChange, StateContainer, StateDelta
from app.game_core.state.slices import RelationSlice, SceneSlice

# ---------------------------------------------------------------------------
# Data file paths
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parent.parent / "data" / "goblin_slayer" / "v2"


def _load_characters() -> dict[str, Any]:
    with open(_DATA_DIR / "characters.json", encoding="utf-8") as f:
        return json.load(f)


def _load_maps() -> dict[str, Any]:
    with open(_DATA_DIR / "maps.json", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_world_with_blacksmith() -> WorldInstance:
    """WorldInstance with a minimal CharacterRegistry containing only 'blacksmith'."""
    world = WorldInstance("test_world")
    char_registry = CharacterRegistry()
    char_registry.load({
        "blacksmith": {
            "id": "blacksmith",
            "name": "碎盾铁匠",
            "tags": ["merchant", "craftsman"],
        }
    })
    world.register(char_registry)
    return world


def _make_world_with_empty_chars() -> WorldInstance:
    """WorldInstance with an empty CharacterRegistry (npc lookup will return None)."""
    world = WorldInstance("test_world")
    char_registry = CharacterRegistry()
    char_registry.load({})
    world.register(char_registry)
    return world


def _make_context(
    *,
    shop_states: dict[str, Any] | None = None,
    world: WorldInstance | None = None,
) -> SettlementContext:
    """Minimal SettlementContext with RelationSlice and SceneSlice."""
    if world is None:
        world = WorldInstance("test_world")
    state = StateContainer()

    relation_slice = RelationSlice()
    relation_slice.restore({"shop_states": shop_states or {}})
    state.register(relation_slice)

    scene_slice = SceneSlice()
    scene_slice.restore({})
    state.register(scene_slice)
    scene_bus = SceneBus(scene_slice)

    change_log: list[StateChange] = []

    def _apply_delta(delta: StateDelta | None) -> None:
        if delta is None:
            return
        state.apply(delta)
        change_log.extend(delta.changes)

    return SettlementContext(
        change_log=change_log,
        state=state,
        world=world,
        scene_bus=scene_bus,
        _rules_engine=_build_rules_engine(),
        _apply_delta=_apply_delta,
    )


def _make_shop_state(items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "npc_id": "blacksmith",
        "current_stock": items or [],
        "refresh_on": None,
        "last_refresh_tick": 0,
    }


def _make_designer(*, sse_collector: list | None = None) -> ItemDesignerSubSystem:
    return ItemDesignerSubSystem(sse_collector=sse_collector)


def _build_rules_engine() -> RulesEngine:
    rules_engine = RulesEngine()
    register_default_rules_handlers(rules_engine)
    return rules_engine


# ---------------------------------------------------------------------------
# Phase 1: Data file tests
# ---------------------------------------------------------------------------


def test_blacksmith_data_loads() -> None:
    """characters.json blacksmith has shop_inventory with non-empty base_pool."""
    chars = _load_characters()
    assert "blacksmith" in chars, "blacksmith must be in characters.json"
    bs = chars["blacksmith"]
    shop_inv = bs.get("shop_inventory")
    assert isinstance(shop_inv, dict), "shop_inventory must be a dict"
    base_pool = shop_inv.get("base_pool")
    assert isinstance(base_pool, list) and len(base_pool) > 0, (
        "base_pool must be a non-empty list"
    )


def test_blacksmith_in_maps() -> None:
    """maps.json blacksmith_shop.resident_npcs contains 'blacksmith'."""
    maps = _load_maps()
    ft = maps.get("frontier_town", {})
    sub = ft.get("sub_locations", {}).get("blacksmith_shop", {})
    resident_npcs = sub.get("resident_npcs", [])
    assert "blacksmith" in resident_npcs, (
        "blacksmith_shop.resident_npcs must include 'blacksmith'"
    )


def test_blacksmith_has_merchant_tag() -> None:
    """characters.json blacksmith has 'merchant' and 'craftsman' tags."""
    chars = _load_characters()
    bs = chars["blacksmith"]
    tags = bs.get("tags", [])
    assert "merchant" in tags, "blacksmith must have 'merchant' tag"
    assert "craftsman" in tags, "blacksmith must have 'craftsman' tag"


def test_guild_quartermaster_has_supply_shop_inventory() -> None:
    chars = _load_characters()
    quartermaster = chars["guild_quartermaster"]

    assert "merchant" in quartermaster.get("tags", [])
    assert "guild_staff" in quartermaster.get("tags", [])
    assert quartermaster.get("refresh_on") == "daily"

    shop_inv = quartermaster.get("shop_inventory")
    assert isinstance(shop_inv, dict)
    base_ids = {entry.get("item_id") for entry in shop_inv.get("base_pool", [])}
    rotating_ids = {entry.get("item_id") for entry in shop_inv.get("rotating_pool", [])}
    assert {"trail_rations", "waterskin", "exploration_torch"}.issubset(base_ids)
    assert {"bandage_roll", "chalk_bundle", "blessed_salt_packet"}.issubset(rotating_ids)


def test_tavern_keeper_shop_is_placed_in_tavern() -> None:
    chars = _load_characters()
    maps = _load_maps()

    tavern_keeper = chars["tavern_keeper"]
    assert "merchant" in tavern_keeper.get("tags", [])
    assert tavern_keeper.get("location_id") == "tavern"
    assert tavern_keeper.get("refresh_on") == "daily"

    tavern = maps.get("frontier_town", {}).get("sub_locations", {}).get("tavern", {})
    assert "tavern_keeper" in tavern.get("resident_npcs", [])


# ---------------------------------------------------------------------------
# Phase 3: curate_shop directive tests
# ---------------------------------------------------------------------------


def test_curate_shop_add_items() -> None:
    """curate_shop add_items appends new stock entries."""
    world = _make_world_with_blacksmith()
    context = _make_context(
        shop_states={"blacksmith": _make_shop_state([])},
        world=world,
    )
    designer = _make_designer()

    result = designer.apply_directive(
        "curate_shop",
        {
            "npc_id": "blacksmith",
            "add_items": [{"item_id": "healing_potion", "count": 5}],
        },
        context,
        current_tick=1,
    )

    assert result is True
    stock = context.state.relations.shop_states["blacksmith"]["current_stock"]
    item_ids = [row["item_id"] for row in stock]
    assert "healing_potion" in item_ids


def test_curate_shop_remove_items() -> None:
    """curate_shop remove_items removes the specified entries from current_stock."""
    initial_stock = [
        {"item_id": "cheap_shortsword", "base_price": 10, "source": "base"},
        {"item_id": "leather_armor", "base_price": 15, "source": "base"},
    ]
    world = _make_world_with_blacksmith()
    context = _make_context(
        shop_states={"blacksmith": _make_shop_state(initial_stock)},
        world=world,
    )
    designer = _make_designer()

    result = designer.apply_directive(
        "curate_shop",
        {
            "npc_id": "blacksmith",
            "remove_items": ["cheap_shortsword"],
        },
        context,
        current_tick=2,
    )

    assert result is True
    stock = context.state.relations.shop_states["blacksmith"]["current_stock"]
    item_ids = [row["item_id"] for row in stock]
    assert "cheap_shortsword" not in item_ids
    assert "leather_armor" in item_ids


def test_curate_shop_restock_items() -> None:
    """curate_shop restock_items updates remaining count for existing item."""
    initial_stock = [
        {"item_id": "round_shield", "base_price": 8, "remaining": 1, "source": "base"},
    ]
    world = _make_world_with_blacksmith()
    context = _make_context(
        shop_states={"blacksmith": _make_shop_state(initial_stock)},
        world=world,
    )
    designer = _make_designer()

    result = designer.apply_directive(
        "curate_shop",
        {
            "npc_id": "blacksmith",
            "restock_items": [{"item_id": "round_shield", "count": 10}],
        },
        context,
        current_tick=3,
    )

    assert result is True
    stock = context.state.relations.shop_states["blacksmith"]["current_stock"]
    shield = next(r for r in stock if r["item_id"] == "round_shield")
    assert shield["remaining"] == 10


def test_curate_shop_unknown_npc() -> None:
    """curate_shop returns False when npc_id is not in world characters."""
    # Use a world that has a character registry but no 'unknown_npc' entry
    world = _make_world_with_empty_chars()
    context = _make_context(
        shop_states={"unknown_npc": _make_shop_state([])},
        world=world,
    )
    designer = _make_designer()

    result = designer.apply_directive(
        "curate_shop",
        {"npc_id": "unknown_npc"},
        context,
        current_tick=1,
    )

    assert result is not True  # Returns a rejection reason string


def test_curate_shop_no_shop_state() -> None:
    """curate_shop returns a rejection reason when shop state is not yet initialized."""
    world = _make_world_with_blacksmith()
    context = _make_context(shop_states={}, world=world)
    designer = _make_designer()

    result = designer.apply_directive(
        "curate_shop",
        {"npc_id": "blacksmith"},
        context,
        current_tick=1,
    )

    assert result is not True  # Returns a rejection reason string


def test_curate_shop_sse_emitted() -> None:
    """curate_shop appends shop_curated SSEEvent to sse_collector."""
    sse_collector: list[SSEEvent] = []
    world = _make_world_with_blacksmith()
    context = _make_context(
        shop_states={"blacksmith": _make_shop_state([])},
        world=world,
    )
    designer = _make_designer(sse_collector=sse_collector)

    designer.apply_directive(
        "curate_shop",
        {"npc_id": "blacksmith"},
        context,
        current_tick=5,
    )

    assert len(sse_collector) == 1
    event = sse_collector[0]
    assert isinstance(event, SSEEvent)
    assert event.event_type == "shop_curated"
    assert event.payload["npc_id"] == "blacksmith"


def test_curate_shop_no_sse_when_collector_none() -> None:
    """curate_shop does not raise when sse_collector is None."""
    world = _make_world_with_blacksmith()
    context = _make_context(
        shop_states={"blacksmith": _make_shop_state([])},
        world=world,
    )
    designer = _make_designer(sse_collector=None)

    result = designer.apply_directive(
        "curate_shop",
        {"npc_id": "blacksmith"},
        context,
        current_tick=1,
    )

    assert result is True


def test_curate_shop_in_supported_directives() -> None:
    """NarrativePlannerHook._SUPPORTED_DIRECTIVES must include 'curate_shop'."""
    assert "curate_shop" in NarrativePlannerHook._SUPPORTED_DIRECTIVES


# ---------------------------------------------------------------------------
# Phase 2: _extract_merchant_data bug fix tests
# ---------------------------------------------------------------------------


def test_merchant_prompt_reads_current_stock() -> None:
    """_extract_merchant_data reads 'current_stock' key (bug fix)."""
    state = StateContainer()
    relation_slice = RelationSlice()
    relation_slice.restore({
        "shop_states": {
            "blacksmith": {
                "npc_id": "blacksmith",
                "current_stock": [
                    {"item_id": "cheap_shortsword", "base_price": 10, "remaining": 5},
                ],
                "refresh_on": None,
                "last_refresh_tick": 0,
            }
        }
    })
    state.register(relation_slice)

    result = _extract_merchant_data("blacksmith", state)

    assert result["role"] == "merchant"
    assert len(result["inventory"]) == 1
    item = result["inventory"][0]
    assert item["item_id"] == "cheap_shortsword"
    assert item["price"] == 10
    assert item["stock"] == 5


def test_merchant_prompt_unlimited_stock_display() -> None:
    """remaining=None in current_stock displays as '无限' in the format block."""
    state = StateContainer()
    relation_slice = RelationSlice()
    relation_slice.restore({
        "shop_states": {
            "blacksmith": {
                "npc_id": "blacksmith",
                "current_stock": [
                    {
                        "item_id": "hempen_rope",
                        "base_price": 2,
                        # no "remaining" key → unlimited
                    },
                ],
                "refresh_on": None,
                "last_refresh_tick": 0,
            }
        }
    })
    state.register(relation_slice)

    role_data = _extract_merchant_data("blacksmith", state)
    # remaining key is absent → stock=None
    assert role_data["inventory"][0]["stock"] is None

    prompt = _format_role_constraint_block(role_data)
    assert "无限" in prompt, f"Expected '无限' in prompt, got:\n{prompt}"
