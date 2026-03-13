"""Tests for InventoryHandler."""

from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.content.registries import ItemRegistry
from app.game_core.orchestration.defaults import build_default_action_dispatcher
from app.game_core.orchestration.models import StructuredAction
from app.game_core.rules import Command, RulesEngine
from app.game_core.rules.handlers import InventoryHandler
from app.game_core.state import StateContainer
from app.game_core.state.slices import PlayerSlice


def _make_world() -> WorldInstance:
    world = WorldInstance("test_world")
    items = ItemRegistry()
    items.load(
        {
            "potion": {"id": "potion", "heal_amount": 5},
            "trinket": {"id": "trinket", "name": "Odd Trinket"},
            "sword": {"id": "sword", "name": "Sword"},
        }
    )
    world.register(items)
    return world


def _make_state(
    *,
    inventory: list[dict] | None = None,
    equipment: dict | None = None,
    hp: int = 8,
) -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    payload = {
        "hp": hp,
        "max_hp": 12,
        "inventory": inventory or [],
    }
    if equipment is not None:
        payload["equipment"] = equipment
    player.restore(payload)
    state.register(player)
    return state


def _make_engine() -> RulesEngine:
    engine = RulesEngine()
    engine.register(InventoryHandler())
    return engine


def _apply(result, state: StateContainer) -> None:
    if result.delta is None:
        return
    state.apply(result.delta)


class TestInventoryHandler:
    def test_default_action_dispatcher_routes_pick_up_and_use_item(self) -> None:
        dispatcher = build_default_action_dispatcher()

        pick_up = dispatcher.dispatch(
            StructuredAction(action_type="pick_up", params={"item_id": "potion"})
        )
        use_item = dispatcher.dispatch(
            StructuredAction(action_type="use_item", params={"item_id": "potion"})
        )

        assert pick_up is not None
        assert pick_up.type == "pick_up"
        assert use_item is not None
        assert use_item.type == "use_item"

    def test_pick_up_stacks_and_merges_tags(self) -> None:
        state = _make_state(
            inventory=[{"item_id": "potion", "count": 1, "tags": ["loot"]}]
        )
        result = _make_engine().execute(
            Command(
                type="pick_up",
                params={"item_id": "potion", "count": 2, "tags": ["consumable", "loot"]},
            ),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.metadata["status"] == "picked_up"
        _apply(result, state)
        assert state.player.inventory[0].count == 3
        assert state.player.inventory[0].tags == ["consumable", "loot"]

    def test_drop_reduces_count_and_can_remove_stack(self) -> None:
        state = _make_state(inventory=[{"item_id": "potion", "count": 2, "tags": []}])
        result = _make_engine().execute(
            Command(type="drop", params={"item_id": "potion", "count": 2}),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.metadata["status"] == "dropped"
        _apply(result, state)
        assert state.player.inventory == []

    def test_drop_rejects_when_count_exceeds_owned(self) -> None:
        result = _make_engine().execute(
            Command(type="drop", params={"item_id": "potion", "count": 2}),
            _make_state(inventory=[{"item_id": "potion", "count": 1, "tags": []}]),
            _make_world(),
        )

        assert result.executed is False
        assert result.errors == ["not enough items: potion"]

    def test_equip_sets_slot_and_reports_previous_item(self) -> None:
        state = _make_state(
            inventory=[{"item_id": "sword", "count": 1, "tags": []}],
            equipment={"main_hand": {"item_id": "dagger"}},
        )
        result = _make_engine().execute(
            Command(type="equip", params={"item_id": "sword", "slot": "main_hand"}),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.metadata["previous_item_id"] == "dagger"
        assert result.metadata["status"] == "equipped"
        _apply(result, state)
        assert state.player.equipment["main_hand"] == {"item_id": "sword"}

    def test_unequip_empty_slot_returns_noop_without_delta(self) -> None:
        state = _make_state()
        result = _make_engine().execute(
            Command(type="unequip", params={"slot": "main_hand"}),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.delta is None
        assert result.metadata["status"] == "noop"
        assert result.metadata["removed_item_id"] is None

    def test_use_item_consumes_and_heals_when_template_has_heal(self) -> None:
        state = _make_state(
            inventory=[{"item_id": "potion", "count": 2, "tags": []}],
            hp=8,
        )
        result = _make_engine().execute(
            Command(type="use_item", params={"item_id": "potion"}),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.time_cost == 1.0 / 6.0
        assert result.metadata["status"] == "consumed"
        assert result.metadata["hp_delta"] == 4
        _apply(result, state)
        assert state.player.hp == 12
        assert state.player.inventory[0].count == 1

    def test_use_item_without_supported_effect_is_noop(self) -> None:
        state = _make_state(
            inventory=[{"item_id": "trinket", "count": 1, "tags": []}],
            hp=8,
        )
        result = _make_engine().execute(
            Command(type="use_item", params={"item_id": "trinket"}),
            state,
            _make_world(),
        )

        assert result.executed is True
        assert result.delta is None
        assert result.metadata["status"] == "no_effect"
        assert result.metadata["hp_delta"] == 0
        assert state.player.get_item_count("trinket") == 1

    def test_consume_resource_success(self) -> None:
        state = _make_state()
        state.player.class_resources = {
            "rage": {"current": 3, "max": 3, "recovery": "long_rest"},
        }
        result = _make_engine().execute(
            Command(type="consume_resource", params={"resource_key": "rage", "amount": 2}),
            state,
            _make_world(),
        )
        assert result.executed is True
        assert result.metadata["status"] == "consumed"
        assert result.metadata["remaining"] == 1
        _apply(result, state)
        assert state.player.class_resources["rage"]["current"] == 1

    def test_consume_resource_insufficient(self) -> None:
        state = _make_state()
        state.player.class_resources = {
            "rage": {"current": 1, "max": 3, "recovery": "long_rest"},
        }
        result = _make_engine().execute(
            Command(type="consume_resource", params={"resource_key": "rage", "amount": 2}),
            state,
            _make_world(),
        )
        assert result.executed is False
        assert "insufficient resource" in result.errors[0]

    def test_consume_resource_missing_key(self) -> None:
        state = _make_state()
        result = _make_engine().execute(
            Command(type="consume_resource", params={"resource_key": "nonexistent"}),
            state,
            _make_world(),
        )
        assert result.executed is False
        assert "unknown resource" in result.errors[0]

    def test_consume_resource_default_amount(self) -> None:
        state = _make_state()
        state.player.class_resources = {
            "channel_divinity": {"current": 2, "max": 2, "recovery": "short_rest"},
        }
        result = _make_engine().execute(
            Command(type="consume_resource", params={"resource_key": "channel_divinity"}),
            state,
            _make_world(),
        )
        assert result.executed is True
        assert result.metadata["amount"] == 1
        assert result.metadata["remaining"] == 1


# ---------------------------------------------------------------------------
# F-A: equip slot constraint + AC recalculation tests
# ---------------------------------------------------------------------------

def _make_typed_world() -> WorldInstance:
    """World with typed armor/shield/weapon items for F-A tests."""
    world = WorldInstance("test_typed")
    items = ItemRegistry()
    items.load({
        "potion": {"id": "potion", "heal_amount": 5},
        "trinket": {"id": "trinket", "name": "Odd Trinket"},
        "sword": {"id": "sword", "name": "Sword"},
        # armor (light): AC = 10 + 2 + DEX_mod
        "leather_armor": {
            "id": "leather_armor",
            "type": "armor",
            "subtype": "light",
            "ac_bonus": 2,
        },
        # armor (heavy): AC = 10 + 4 (DEX ignored)
        "chain_mail": {
            "id": "chain_mail",
            "type": "armor",
            "subtype": "heavy",
            "ac_bonus": 4,
        },
        # shield: adds base_ac on top of chest result
        "buckler": {
            "id": "buckler",
            "type": "armor",
            "subtype": "shield",
            "ac_bonus": 2,
        },
    })
    world.register(items)
    return world


def _make_typed_state(
    *,
    inventory: list[dict] | None = None,
    equipment: dict | None = None,
    hp: int = 10,
    dex: int = 10,
    ac: int = 10,
) -> StateContainer:
    state = StateContainer()
    player = PlayerSlice()
    payload: dict = {
        "hp": hp,
        "max_hp": 12,
        "ac": ac,
        "stats": {"str": 10, "dex": dex, "con": 10, "int": 10, "wis": 10, "cha": 10},
        "inventory": inventory or [],
    }
    if equipment is not None:
        payload["equipment"] = equipment
    player.restore(payload)
    state.register(player)
    return state


class TestInventoryHandlerEquipFA:
    def test_equip_armor_rejects_wrong_slot(self) -> None:
        """Armor (non-shield) can only go in 'chest', not main_hand."""
        state = _make_typed_state(
            inventory=[{"item_id": "leather_armor", "count": 1, "tags": []}],
        )
        result = _make_engine().execute(
            Command(type="equip", params={"item_id": "leather_armor", "slot": "main_hand"}),
            state,
            _make_typed_world(),
        )
        assert result.executed is False
        assert "cannot be equipped in slot" in (result.errors[0] if result.errors else "")

    def test_equip_shield_only_allows_off_hand(self) -> None:
        """Shield must go to off_hand; chest is rejected."""
        state = _make_typed_state(
            inventory=[{"item_id": "buckler", "count": 1, "tags": []}],
        )
        result = _make_engine().execute(
            Command(type="equip", params={"item_id": "buckler", "slot": "chest"}),
            state,
            _make_typed_world(),
        )
        assert result.executed is False
        assert "cannot be equipped in slot" in (result.errors[0] if result.errors else "")

    def test_equip_armor_updates_ac(self) -> None:
        """Equip light armor with DEX=12 → AC = 10 + 2 + 1 = 13."""
        state = _make_typed_state(
            inventory=[{"item_id": "leather_armor", "count": 1, "tags": []}],
            dex=12,  # DEX mod = +1
            ac=10,
        )
        result = _make_engine().execute(
            Command(type="equip", params={"item_id": "leather_armor", "slot": "chest"}),
            state,
            _make_typed_world(),
        )
        assert result.executed is True
        assert result.metadata["status"] == "equipped"
        assert result.metadata["ac"] == 13
        _apply(result, state)
        assert state.player.ac == 13

    def test_equip_heavy_armor_ignores_dex(self) -> None:
        """Heavy armor: AC = 10 + base_ac regardless of DEX."""
        state = _make_typed_state(
            inventory=[{"item_id": "chain_mail", "count": 1, "tags": []}],
            dex=16,  # DEX mod = +3, but heavy armor ignores it
            ac=10,
        )
        result = _make_engine().execute(
            Command(type="equip", params={"item_id": "chain_mail", "slot": "chest"}),
            state,
            _make_typed_world(),
        )
        assert result.executed is True
        assert result.metadata["ac"] == 14  # 10 + 4
        _apply(result, state)
        assert state.player.ac == 14

    def test_equip_shield_adds_ac_bonus(self) -> None:
        """Shield equipped to off_hand adds its base_ac on top of current chest AC."""
        # Start with leather armor already equipped
        state = _make_typed_state(
            inventory=[{"item_id": "buckler", "count": 1, "tags": []}],
            equipment={"chest": {"item_id": "leather_armor"}},
            dex=12,  # DEX mod = +1 → leather gives AC 13
            ac=13,
        )
        result = _make_engine().execute(
            Command(type="equip", params={"item_id": "buckler", "slot": "off_hand"}),
            state,
            _make_typed_world(),
        )
        assert result.executed is True
        assert result.metadata["ac"] == 15  # 13 (leather) + 2 (buckler)
        _apply(result, state)
        assert state.player.ac == 15

    def test_unequip_armor_recalculates_ac(self) -> None:
        """Unequipping armor reverts AC to unarmored (10 + DEX_mod)."""
        state = _make_typed_state(
            equipment={"chest": {"item_id": "leather_armor"}},
            dex=12,
            ac=13,  # was wearing leather armor
        )
        result = _make_engine().execute(
            Command(type="unequip", params={"slot": "chest"}),
            state,
            _make_typed_world(),
        )
        assert result.executed is True
        assert result.metadata["status"] == "unequipped"
        assert result.metadata["ac"] == 11  # unarmored: 10 + 1
        _apply(result, state)
        assert state.player.ac == 11


# ---------------------------------------------------------------------------
# Phase 4 — dice heal + buff/remove_status tests
# ---------------------------------------------------------------------------

def _make_world_with_dice_items() -> WorldInstance:
    """World with dice-based healing potion and an antidote."""
    world = WorldInstance("test_dice")
    items = ItemRegistry()
    items.load({
        "heal_potion": {
            "id": "heal_potion",
            "name": "Healing Potion",
            "type": "consumable",
            "consumable_data": {
                "trigger": "on_use",
                "charges": 1,
                "effect": {
                    "type": "heal",
                    "params": {"dice": "2d4+2"},
                    "target": "self",
                },
            },
        },
        "static_potion": {
            "id": "static_potion",
            "name": "Static Potion",
            "type": "consumable",
            "consumable_data": {
                "trigger": "on_use",
                "charges": 1,
                "effect": {
                    "type": "heal",
                    "params": {"amount": 8},
                    "target": "self",
                },
            },
        },
        "antidote": {
            "id": "antidote",
            "name": "Antidote",
            "type": "consumable",
            "consumable_data": {
                "trigger": "on_use",
                "charges": 1,
                "effect": {
                    "type": "buff",
                    "params": {"remove_status": "poisoned"},
                    "target": "self",
                },
            },
        },
        "torch": {
            "id": "torch",
            "name": "Torch",
            "type": "consumable",
            "consumable_data": {
                "trigger": "on_use",
                "charges": 1,
                "effect": {
                    "type": "utility",
                    "params": {"light_radius": 20},
                    "target": "self",
                },
            },
        },
    })
    world.register(items)
    return world


class TestPhase4UseItem:
    """Phase 4: dice-based heal amounts and buff/remove_status consumables."""

    def test_dice_heal_rolls_dice_expression(self, monkeypatch) -> None:
        """Heal potion with 'dice': '2d4+2' rolls dice instead of using fixed amount."""
        # Monkeypatch random.randint to return predictable rolls: both dice → 4 → total 4+4+2=10
        import random as _random
        rolls = iter([4, 4])
        monkeypatch.setattr(_random, "randint", lambda a, b: next(rolls))

        world = _make_world_with_dice_items()
        state = _make_state(
            inventory=[{"item_id": "heal_potion", "count": 1, "tags": []}],
            hp=5,
        )
        result = _make_engine().execute(
            Command(type="use_item", params={"item_id": "heal_potion"}),
            state,
            world,
        )
        assert result.executed is True
        assert result.metadata["status"] == "consumed"
        # 2d4+2 with rolls [4,4] = 10, but hp cap is max_hp (12) - current (5) = 7
        assert result.metadata["hp_delta"] == 7
        _apply(result, state)
        assert state.player.hp == 12

    def test_dice_heal_minimum_one(self, monkeypatch) -> None:
        """roll_damage_dice ensures minimum 1, and heal is capped to missing HP."""
        import random as _random
        monkeypatch.setattr(_random, "randint", lambda a, b: 1)  # both dice roll 1 → 2+2=4

        world = _make_world_with_dice_items()
        state = _make_state(
            inventory=[{"item_id": "heal_potion", "count": 1, "tags": []}],
            hp=10,
        )
        result = _make_engine().execute(
            Command(type="use_item", params={"item_id": "heal_potion"}),
            state,
            world,
        )
        assert result.executed is True
        assert result.metadata["hp_delta"] == 2  # min(4, 12-10)=2
        _apply(result, state)
        assert state.player.hp == 12

    def test_static_amount_fallback(self) -> None:
        """Heal potion with 'amount': 8 (no dice) still heals correctly."""
        world = _make_world_with_dice_items()
        state = _make_state(
            inventory=[{"item_id": "static_potion", "count": 1, "tags": []}],
            hp=4,
        )
        result = _make_engine().execute(
            Command(type="use_item", params={"item_id": "static_potion"}),
            state,
            world,
        )
        assert result.executed is True
        assert result.metadata["status"] == "consumed"
        assert result.metadata["hp_delta"] == 8
        _apply(result, state)
        assert state.player.hp == 12

    def test_antidote_removes_poisoned_status(self) -> None:
        """Antidote with buff/remove_status removes the 'poisoned' effect from player."""
        world = _make_world_with_dice_items()
        state = _make_state(
            inventory=[{"item_id": "antidote", "count": 1, "tags": []}],
            hp=8,
        )
        # Inject a poisoned active effect
        state.player.active_effects = [
            {"effect_id": "poisoned", "instance_id": "eff_001", "remaining_duration": 3},
            {"effect_id": "bleeding", "instance_id": "eff_002", "remaining_duration": 2},
        ]

        result = _make_engine().execute(
            Command(type="use_item", params={"item_id": "antidote"}),
            state,
            world,
        )
        assert result.executed is True
        assert result.metadata["status"] == "consumed"
        assert result.metadata["effect_type"] == "buff"
        assert result.metadata["remove_status"] == "poisoned"
        assert result.metadata["removed_count"] == 1
        assert result.metadata["hp_delta"] == 0

        _apply(result, state)
        # poisoned removed, bleeding intact
        remaining = [e["effect_id"] for e in state.player.active_effects]
        assert "poisoned" not in remaining
        assert "bleeding" in remaining
        # antidote consumed from inventory
        assert state.player.get_item_count("antidote") == 0

    def test_antidote_when_not_poisoned_still_consumes(self) -> None:
        """Antidote is consumed even if player is not currently poisoned."""
        world = _make_world_with_dice_items()
        state = _make_state(
            inventory=[{"item_id": "antidote", "count": 1, "tags": []}],
            hp=8,
        )
        # No active effects
        result = _make_engine().execute(
            Command(type="use_item", params={"item_id": "antidote"}),
            state,
            world,
        )
        assert result.executed is True
        assert result.metadata["status"] == "consumed"
        assert result.metadata["removed_count"] == 0
        _apply(result, state)
        assert state.player.get_item_count("antidote") == 0

    def test_utility_effect_item_is_noop(self) -> None:
        """Utility-type consumable (torch) returns no_effect without consuming."""
        world = _make_world_with_dice_items()
        state = _make_state(
            inventory=[{"item_id": "torch", "count": 1, "tags": []}],
            hp=8,
        )
        result = _make_engine().execute(
            Command(type="use_item", params={"item_id": "torch"}),
            state,
            world,
        )
        assert result.executed is True
        assert result.metadata["status"] == "no_effect"
        # Item not consumed (no delta)
        assert result.delta is None
        assert state.player.get_item_count("torch") == 1
