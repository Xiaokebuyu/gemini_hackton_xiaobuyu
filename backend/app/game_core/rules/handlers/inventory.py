"""InventoryHandler implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from app.game_core.content import WorldInstance

if TYPE_CHECKING:
    from app.game_core.content.registries.items import ItemTemplate
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.handler_utils import (
    coerce_int,
    get_non_empty_string,
    handler_failure,
    handler_success,
    handler_success_no_delta,
    resolve_item_heal_amount,
)
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer


class InventoryHandler(StaticCommandHandler):
    COMMAND_TYPES = ("pick_up", "drop", "equip", "unequip", "use_item", "consume_resource")

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if cmd.type == "pick_up":
            return self._validate_pick_up(cmd, state, world)
        if cmd.type == "drop":
            return self._validate_drop(cmd, state)
        if cmd.type == "equip":
            return self._validate_equip(cmd, state, world)
        if cmd.type == "unequip":
            return self._validate_unequip(cmd, state)
        if cmd.type == "use_item":
            return self._validate_use_item(cmd, state, world)
        if cmd.type == "consume_resource":
            return self._validate_consume_resource(cmd, state)
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

        if cmd.type == "pick_up":
            return self._compute_pick_up(cmd, state)
        if cmd.type == "drop":
            return self._compute_drop(cmd, state)
        if cmd.type == "equip":
            return self._compute_equip(cmd, state, world)
        if cmd.type == "unequip":
            return self._compute_unequip(cmd, state, world)
        if cmd.type == "use_item":
            return self._compute_use_item(cmd, state, world)
        if cmd.type == "consume_resource":
            return self._compute_consume_resource(cmd, state)
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _validate_pick_up(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")
        if not world.has_registry("items"):
            return ValidationResult(ok=False, reason="items registry is required")
        item_id = get_non_empty_string(cmd.params, "item_id")
        if item_id is None:
            return ValidationResult(ok=False, reason="item_id must be a non-empty string")
        count = coerce_int(cmd.params.get("count", 1))
        if count is None or count < 1:
            return ValidationResult(ok=False, reason="count must be an integer >= 1")
        if world.items.get(item_id) is None:
            return ValidationResult(ok=False, reason=f"unknown item: {item_id}")
        tags = cmd.params.get("tags", [])
        if not isinstance(tags, list):
            return ValidationResult(ok=False, reason="tags must be a list")
        return ValidationResult(ok=True)

    def _validate_drop(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")
        item_id = get_non_empty_string(cmd.params, "item_id")
        if item_id is None:
            return ValidationResult(ok=False, reason="item_id must be a non-empty string")
        count = coerce_int(cmd.params.get("count", 1))
        if count is None or count < 1:
            return ValidationResult(ok=False, reason="count must be an integer >= 1")
        if state.player.get_item_count(item_id) < count:
            return ValidationResult(
                ok=False,
                reason=f"not enough items: {item_id}",
            )
        return ValidationResult(ok=True)

    def _validate_equip(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")
        item_id = get_non_empty_string(cmd.params, "item_id")
        if item_id is None:
            return ValidationResult(ok=False, reason="item_id must be a non-empty string")
        slot = get_non_empty_string(cmd.params, "slot")
        if slot is None:
            return ValidationResult(ok=False, reason="slot must be a non-empty string")
        if slot not in state.player.equipment:
            return ValidationResult(ok=False, reason=f"unknown equipment slot: {slot}")
        if state.player.get_item_count(item_id) < 1:
            return ValidationResult(
                ok=False,
                reason=f"item not in inventory: {item_id}",
            )
        # Type ↔ slot constraint (requires items registry; permissive when registry absent)
        if world.has_registry("items"):
            template = world.items.get(item_id)
            if template is not None:
                allowed = self._allowed_slots(template)
                if allowed is not None and slot not in allowed:
                    return ValidationResult(
                        ok=False,
                        reason=f"item '{item_id}' cannot be equipped in slot '{slot}'",
                    )
        return ValidationResult(ok=True)

    def _validate_unequip(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")
        slot = get_non_empty_string(cmd.params, "slot")
        if slot is None:
            return ValidationResult(ok=False, reason="slot must be a non-empty string")
        if slot not in state.player.equipment:
            return ValidationResult(ok=False, reason=f"unknown equipment slot: {slot}")
        return ValidationResult(ok=True)

    def _validate_use_item(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")
        if not world.has_registry("items"):
            return ValidationResult(ok=False, reason="items registry is required")
        item_id = get_non_empty_string(cmd.params, "item_id")
        if item_id is None:
            return ValidationResult(ok=False, reason="item_id must be a non-empty string")
        if state.player.get_item_count(item_id) < 1:
            return ValidationResult(ok=False, reason=f"item not in inventory: {item_id}")
        if world.items.get(item_id) is None:
            return ValidationResult(ok=False, reason=f"unknown item: {item_id}")
        return ValidationResult(ok=True)

    def _compute_pick_up(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        item_id = str(cmd.params["item_id"]).strip()
        count = int(cmd.params.get("count", 1))
        tags = self._normalize_tags(cmd.params.get("tags", []))
        inventory = self._player_inventory_snapshot(state)

        merged = False
        for item in inventory:
            if item.get("item_id") != item_id:
                continue
            item["count"] = int(item.get("count", 0)) + count
            existing_tags = item.get("tags", [])
            combined_tags = (
                [str(tag) for tag in existing_tags]
                if isinstance(existing_tags, list)
                else []
            )
            item["tags"] = sorted(set([*combined_tags, *tags]))
            merged = True
            break

        if not merged:
            inventory.append(
                {
                    "item_id": item_id,
                    "count": count,
                    "tags": sorted(set(tags)),
                }
            )

        return handler_success(
            "inventory",
            "pick_up",
            changes=[
                StateChange("player", "set", "inventory", inventory),
            ],
            metadata={
                "item_id": item_id,
                "count": count,
                "status": "picked_up",
            },
            omit_empty_delta=False,
        )

    def _compute_drop(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        item_id = str(cmd.params["item_id"]).strip()
        count = int(cmd.params.get("count", 1))
        inventory = self._player_inventory_snapshot(state)
        next_inventory = self._remove_from_inventory(inventory, item_id, count)
        return handler_success(
            "inventory",
            "drop",
            changes=[
                StateChange("player", "set", "inventory", next_inventory),
            ],
            metadata={
                "item_id": item_id,
                "count": count,
                "status": "dropped",
            },
            omit_empty_delta=False,
        )

    def _compute_equip(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        item_id = str(cmd.params["item_id"]).strip()
        slot = str(cmd.params["slot"]).strip()
        equipment = self._player_equipment_snapshot(state)
        previous_item_id = self._equipped_item_id(equipment.get(slot))
        equipment[slot] = {"item_id": item_id}

        changes: list[StateChange] = [StateChange("player", "set", "equipment", equipment)]
        new_ac = self._compute_ac(equipment, state, world)
        if new_ac is not None and new_ac != state.player.ac:
            changes.append(StateChange("player", "set", "ac", new_ac))

        return handler_success(
            "inventory",
            "equip",
            changes=changes,
            metadata={
                "item_id": item_id,
                "slot": slot,
                "previous_item_id": previous_item_id,
                "status": "equipped",
                "ac": new_ac,
            },
            omit_empty_delta=False,
        )

    def _compute_unequip(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        slot = str(cmd.params["slot"]).strip()
        equipment = self._player_equipment_snapshot(state)
        removed_item_id = self._equipped_item_id(equipment.get(slot))
        if removed_item_id is None:
            return handler_success_no_delta(
                "inventory",
                "unequip",
                metadata={
                    "slot": slot,
                    "removed_item_id": None,
                    "status": "noop",
                },
            )
        equipment[slot] = None

        changes: list[StateChange] = [StateChange("player", "set", "equipment", equipment)]
        new_ac = self._compute_ac(equipment, state, world)
        if new_ac is not None and new_ac != state.player.ac:
            changes.append(StateChange("player", "set", "ac", new_ac))

        return handler_success(
            "inventory",
            "unequip",
            changes=changes,
            metadata={
                "slot": slot,
                "removed_item_id": removed_item_id,
                "status": "unequipped",
                "ac": new_ac,
            },
            omit_empty_delta=False,
        )

    def _compute_use_item(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        item_id = str(cmd.params["item_id"]).strip()
        item_template = world.items.get(item_id)
        heal_amount = resolve_item_heal_amount(item_template)
        if heal_amount is None:
            return ExecuteResult(
                success=True,
                delta=None,
                metadata={
                    "handler": "inventory",
                    "command": "use_item",
                    "item_id": item_id,
                    "status": "no_effect",
                    "hp_delta": 0,
                },
            )

        inventory = self._player_inventory_snapshot(state)
        next_inventory = self._remove_from_inventory(inventory, item_id, 1)
        actual_heal = min(heal_amount, max(0, state.player.max_hp - state.player.hp))
        changes = [
            StateChange("player", "set", "inventory", next_inventory),
        ]
        if actual_heal > 0:
            changes.append(
                StateChange("player", "add", "hp", actual_heal),
            )
        return handler_success(
            "inventory",
            "use_item",
            changes=changes,
            time_cost=1.0 / 6.0,
            metadata={
                "item_id": item_id,
                "status": "consumed",
                "hp_delta": actual_heal,
            },
            omit_empty_delta=False,
        )

    def _validate_consume_resource(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")
        resource_key = get_non_empty_string(cmd.params, "resource_key")
        if resource_key is None:
            return ValidationResult(ok=False, reason="resource_key must be a non-empty string")
        amount = coerce_int(cmd.params.get("amount", 1))
        if amount is None or amount < 1:
            return ValidationResult(ok=False, reason="amount must be an integer >= 1")
        return ValidationResult(ok=True)

    def _compute_consume_resource(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        resource_key = str(cmd.params["resource_key"]).strip()
        amount = int(cmd.params.get("amount", 1))
        resource = state.player.get_resource(resource_key)
        if resource is None:
            return handler_failure(
                "inventory",
                "consume_resource",
                errors=[f"unknown resource: {resource_key}"],
            )
        current = int(resource.get("current", 0))
        if current < amount:
            return handler_failure(
                "inventory",
                "consume_resource",
                errors=[f"insufficient resource: {resource_key}"],
            )
        new_current = current - amount
        updated = {**resource, "current": new_current}
        return handler_success(
            "inventory",
            "consume_resource",
            changes=[
                StateChange("player", "modify", f"class_resources.{resource_key}", updated),
            ],
            metadata={
                "status": "consumed",
                "resource_key": resource_key,
                "amount": amount,
                "remaining": new_current,
                "max": int(resource.get("max", 0)),
            },
        )

    # ------------------------------------------------------------------
    # AC calculation
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_ac(
        equipment: dict[str, Any],
        state: StateContainer,
        world: WorldInstance,
    ) -> int | None:
        """Recalculate AC from the given equipment snapshot.

        Returns None when no items registry is available (no update).
        Formula (per design spec §6.3; base_ac is absolute for non-shield armor):
          - unarmored:  10 + DEX_mod
          - light:      base_ac + DEX_mod         (base_ac = 10 + raw ac_bonus)
          - medium:     base_ac + min(DEX_mod, 2)
          - heavy:      base_ac
          - shield:     additive bonus (shield.base_ac = raw ac_bonus, e.g. 2)
        """
        if not world.has_registry("items"):
            return None

        # chest slot → armor
        armor_data = None
        chest_entry = equipment.get("chest")
        if isinstance(chest_entry, Mapping):
            chest_id = chest_entry.get("item_id")
            if chest_id:
                t = world.items.get(str(chest_id))
                if t is not None:
                    armor_data = t.armor_data

        # off_hand slot → shield (only if ArmorData.armor_type == "shield")
        shield_data = None
        off_entry = equipment.get("off_hand")
        if isinstance(off_entry, Mapping):
            off_id = off_entry.get("item_id")
            if off_id:
                t = world.items.get(str(off_id))
                if t is not None and t.armor_data is not None:
                    if t.armor_data.armor_type == "shield":
                        shield_data = t.armor_data

        dex_mod = state.player.get_modifier("dex")

        if armor_data is None:
            base = 10 + dex_mod
        elif armor_data.armor_type == "light":
            base = armor_data.base_ac + dex_mod
        elif armor_data.armor_type == "medium":
            base = armor_data.base_ac + min(dex_mod, 2)
        elif armor_data.armor_type == "heavy":
            base = armor_data.base_ac
        else:
            # unknown armor_type → treat as unarmored
            base = 10 + dex_mod

        if shield_data is not None:
            base += shield_data.base_ac

        return base

    # ------------------------------------------------------------------
    # Slot constraint helper
    # ------------------------------------------------------------------

    @staticmethod
    def _allowed_slots(template: "ItemTemplate") -> set[str] | None:
        """Return the set of valid slots for this item, or None for permissive."""
        if template.armor_data is not None:
            if template.armor_data.armor_type == "shield":
                return {"off_hand"}
            return {"chest"}
        if template.weapon_data is not None:
            ws = template.weapon_data.slot
            # two_handed weapon occupies main_hand slot
            return {"main_hand"} if ws == "two_handed" else {ws}
        return None  # misc / no sub-struct → any slot

    # ------------------------------------------------------------------
    # Snapshot helpers
    # ------------------------------------------------------------------

    def _player_inventory_snapshot(self, state: StateContainer) -> list[dict[str, Any]]:
        snapshot = state.player.snapshot().get("inventory", [])
        if not isinstance(snapshot, list):
            return []
        inventory: list[dict[str, Any]] = []
        for item in snapshot:
            if isinstance(item, Mapping):
                inventory.append(dict(item))
        return inventory

    def _player_equipment_snapshot(self, state: StateContainer) -> dict[str, Any]:
        snapshot = state.player.snapshot().get("equipment", {})
        if not isinstance(snapshot, Mapping):
            return dict(state.player.equipment)
        return dict(snapshot)

    def _remove_from_inventory(
        self,
        inventory: list[dict[str, Any]],
        item_id: str,
        count: int,
    ) -> list[dict[str, Any]]:
        next_inventory: list[dict[str, Any]] = []
        remaining_to_remove = count
        for item in inventory:
            if item.get("item_id") != item_id or remaining_to_remove <= 0:
                next_inventory.append(item)
                continue
            remaining = int(item.get("count", 0)) - remaining_to_remove
            if remaining > 0:
                updated = dict(item)
                updated["count"] = remaining
                next_inventory.append(updated)
                remaining_to_remove = 0
                continue
            remaining_to_remove = max(0, -remaining)
        return next_inventory

    @staticmethod
    def _equipped_item_id(raw_value: Any) -> str | None:
        if not isinstance(raw_value, Mapping):
            return None
        value = raw_value.get("item_id")
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _normalize_tags(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        seen: list[str] = []
        for item in value:
            tag = str(item)
            if tag not in seen:
                seen.append(tag)
        return seen
