"""ContainerHandler implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content import WorldInstance
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.handler_utils import (
    coerce_int,
    get_non_empty_string,
    handler_success,
    handler_success_no_delta,
    normalize_tags,
)
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer


class ContainerHandler(StaticCommandHandler):
    COMMAND_TYPES = (
        "open_container",
        "disarm_trap",
        "take_from_container",
        "take_all",
        "interact_object",
    )

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        del world
        if cmd.type == "open_container":
            return self._validate_open_container(cmd, state)
        if cmd.type == "disarm_trap":
            return self._validate_disarm_trap(cmd, state)
        if cmd.type == "take_from_container":
            return self._validate_take_from_container(cmd, state)
        if cmd.type == "take_all":
            return self._validate_take_all(cmd, state)
        if cmd.type == "interact_object":
            return self._validate_interact_object(cmd, state)
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

        if cmd.type == "open_container":
            return self._compute_open_container(cmd, state)
        if cmd.type == "disarm_trap":
            return self._compute_disarm_trap(cmd, state)
        if cmd.type == "take_from_container":
            return self._compute_take_from_container(cmd, state)
        if cmd.type == "take_all":
            return self._compute_take_all(cmd, state)
        if cmd.type == "interact_object":
            return self._compute_interact_object(cmd, state)
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _validate_open_container(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        resolved = self._validate_container_presence(cmd, state)
        if resolved is not None:
            return resolved
        return ValidationResult(ok=True)

    def _validate_disarm_trap(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        resolved = self._validate_container_presence(cmd, state)
        if resolved is not None:
            return resolved
        container_id = str(cmd.params["container_id"]).strip()
        target = self._resolve_container(state, container_id)
        if target is None:
            return ValidationResult(ok=False, reason=f"unknown container: {container_id}")
        _, container_state = target
        if str(container_state.get("trap_status", "disarmed")) != "armed":
            return ValidationResult(ok=False, reason="container trap is not armed")
        if not bool(container_state.get("trap_detected", False)):
            return ValidationResult(ok=False, reason="trap must be detected before disarming")
        return ValidationResult(ok=True)

    def _validate_take_from_container(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        resolved = self._validate_container_presence(cmd, state)
        if resolved is not None:
            return resolved
        item_id = get_non_empty_string(cmd.params, "item_id")
        if item_id is None:
            return ValidationResult(ok=False, reason="item_id must be a non-empty string")
        count = coerce_int(cmd.params.get("count", 1))
        if count is None or count < 1:
            return ValidationResult(ok=False, reason="count must be an integer >= 1")
        container_id = str(cmd.params["container_id"]).strip()
        target = self._resolve_container(state, container_id)
        if target is None:
            return ValidationResult(ok=False, reason=f"unknown container: {container_id}")
        _, container_state = target
        gate = self._validate_open_unlocked_container(container_state)
        if gate is not None:
            return gate
        item_entry = self._find_container_item(container_state, item_id)
        if item_entry is None or int(item_entry.get("count", 0)) < count:
            return ValidationResult(
                ok=False,
                reason=f"not enough container items: {item_id}",
            )
        return ValidationResult(ok=True)

    def _validate_take_all(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        resolved = self._validate_container_presence(cmd, state)
        if resolved is not None:
            return resolved
        container_id = str(cmd.params["container_id"]).strip()
        target = self._resolve_container(state, container_id)
        if target is None:
            return ValidationResult(ok=False, reason=f"unknown container: {container_id}")
        _, container_state = target
        gate = self._validate_open_unlocked_container(container_state)
        if gate is not None:
            return gate
        return ValidationResult(ok=True)

    def _compute_open_container(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        container_id = str(cmd.params["container_id"]).strip()
        target = self._resolve_container(state, container_id)
        if target is None:
            return ExecuteResult.error(f"unknown container: {container_id}")
        area_id, container_state = target

        items = self._container_items(container_state)
        gold = int(container_state.get("remaining_gold", 0))
        lock_status = str(container_state.get("lock_status", "unlocked"))
        trap_status = str(container_state.get("trap_status", "disarmed"))

        if bool(container_state.get("opened", False)):
            return handler_success_no_delta(
                "container", "open_container",
                time_cost=1.0 / 6.0,
                metadata={
                    "status": "opened",
                    "container_id": container_id,
                    "trap_triggered": False,
                    "trap_damage": 0,
                    "lock_status": lock_status,
                    "trap_status": trap_status,
                    "gold": gold,
                    "items": items,
                },
            )

        if trap_status == "armed" and bool(container_state.get("trap_detected", False)):
            return handler_success_no_delta(
                "container", "open_container",
                time_cost=1.0 / 6.0,
                metadata={
                    "status": "trap_detected",
                    "container_id": container_id,
                    "trap_triggered": False,
                    "trap_damage": 0,
                    "lock_status": lock_status,
                    "trap_status": trap_status,
                    "gold": gold,
                    "items": items,
                },
            )

        changes: list[StateChange] = []
        updated_container = dict(container_state)
        updated_container["area_id"] = area_id
        trap_triggered = False
        trap_damage = 0
        if trap_status == "armed":
            trap_triggered = True
            trap_damage = self._trap_damage(container_state)
            updated_container["trap_status"] = "triggered"
            if trap_damage != 0:
                changes.append(StateChange("player", "add", "hp", -trap_damage))

        if lock_status == "locked":
            if trap_triggered:
                changes.append(
                    StateChange(
                        "areas",
                        "modify",
                        f"container_states.{container_id}",
                        updated_container,
                    )
                )
            return handler_success(
                "container", "open_container",
                changes=changes,
                time_cost=1.0 / 6.0,
                metadata={
                    "status": "locked",
                    "container_id": container_id,
                    "trap_triggered": trap_triggered,
                    "trap_damage": trap_damage,
                    "lock_status": "locked",
                    "trap_status": updated_container.get("trap_status", trap_status),
                    "gold": gold,
                    "items": items,
                },
            )

        updated_container["opened"] = True
        changes.append(
            StateChange(
                "areas",
                "modify",
                f"container_states.{container_id}",
                updated_container,
            )
        )
        return handler_success(
            "container", "open_container",
            changes=changes,
            time_cost=1.0 / 6.0,
            metadata={
                "status": "opened",
                "container_id": container_id,
                "trap_triggered": trap_triggered,
                "trap_damage": trap_damage,
                "lock_status": lock_status,
                "trap_status": updated_container.get("trap_status", trap_status),
                "gold": gold,
                "items": items,
            },
        )

    def _compute_disarm_trap(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        container_id = str(cmd.params["container_id"]).strip()
        target = self._resolve_container(state, container_id)
        if target is None:
            return ExecuteResult.error(f"unknown container: {container_id}")
        area_id, container_state = target

        dc = self._trap_disarm_dc(container_state)
        passive_total = 10 + state.player.get_skill_bonus("sleight_of_hand")
        passed = passive_total >= dc
        updated_container = dict(container_state)
        updated_container["area_id"] = area_id
        changes: list[StateChange] = []
        trap_damage = 0
        status = "disarmed"

        if passed:
            updated_container["trap_status"] = "disarmed"
        else:
            trap_damage = self._trap_damage(container_state)
            updated_container["trap_status"] = "triggered"
            status = "trap_triggered"
            if trap_damage != 0:
                changes.append(StateChange("player", "add", "hp", -trap_damage))

        changes.append(
            StateChange(
                "areas",
                "modify",
                f"container_states.{container_id}",
                updated_container,
            )
        )
        return handler_success(
            "container", "disarm_trap",
            changes=changes,
            time_cost=1.0 / 6.0,
            metadata={
                "status": status,
                "container_id": container_id,
                "dc": dc,
                "passive_total": passive_total,
                "passed": passed,
                "trap_damage": trap_damage,
            },
        )

    def _compute_take_from_container(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        container_id = str(cmd.params["container_id"]).strip()
        item_id = str(cmd.params["item_id"]).strip()
        count = int(cmd.params.get("count", 1))
        target = self._resolve_container(state, container_id)
        if target is None:
            return ExecuteResult.error(f"unknown container: {container_id}")
        area_id, container_state = target

        item_entry = self._find_container_item(container_state, item_id)
        updated_inventory = self._player_inventory_snapshot(state)
        self._add_to_inventory_snapshot(
            updated_inventory,
            item_id,
            count,
            normalize_tags(item_entry.get("tags", []) if item_entry is not None else []),
        )

        updated_container = self._remove_from_container_snapshot(container_state, item_id, count)
        updated_container["area_id"] = area_id
        looted = (
            not self._container_items(updated_container)
            and int(updated_container.get("remaining_gold", 0)) == 0
        )
        if looted:
            updated_container["looted"] = True

        return handler_success(
            "container", "take_from_container",
            changes=[
                StateChange("player", "set", "inventory", updated_inventory),
                StateChange(
                    "areas",
                    "modify",
                    f"container_states.{container_id}",
                    updated_container,
                ),
            ],
            metadata={
                "status": "taken",
                "container_id": container_id,
                "item_id": item_id,
                "count": count,
                "looted": looted,
            },
        )

    def _compute_take_all(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        container_id = str(cmd.params["container_id"]).strip()
        target = self._resolve_container(state, container_id)
        if target is None:
            return ExecuteResult.error(f"unknown container: {container_id}")
        area_id, container_state = target

        updated_inventory = self._player_inventory_snapshot(state)
        items = self._container_items(container_state)
        for item in items:
            item_id = get_non_empty_string(item, "item_id")
            count = coerce_int(item.get("count"))
            if item_id is None or count is None or count <= 0:
                continue
            self._add_to_inventory_snapshot(
                updated_inventory,
                item_id,
                count,
                normalize_tags(item.get("tags", [])),
            )

        gold = max(0, int(container_state.get("remaining_gold", 0)))
        updated_container = dict(container_state)
        updated_container["area_id"] = area_id
        updated_container["remaining_items"] = []
        updated_container["remaining_gold"] = 0
        updated_container["looted"] = True
        updated_container["opened"] = True

        changes = [
            StateChange("player", "set", "inventory", updated_inventory),
            StateChange(
                "areas",
                "modify",
                f"container_states.{container_id}",
                updated_container,
            ),
        ]
        if gold > 0:
            changes.insert(1, StateChange("player", "add", "gold", gold))

        return handler_success(
            "container", "take_all",
            changes=changes,
            metadata={
                "status": "looted",
                "container_id": container_id,
                "item_count": sum(
                    int(item.get("count", 0))
                    for item in items
                    if isinstance(item, Mapping)
                ),
                "gold": gold,
                "looted": True,
            },
        )

    def _validate_interact_object(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")
        if not state.has_slice("areas"):
            return ValidationResult(ok=False, reason="areas slice is required")
        object_id = get_non_empty_string(cmd.params, "object_id")
        if object_id is None:
            return ValidationResult(ok=False, reason="object_id must be a non-empty string")
        return ValidationResult(ok=True)

    def _compute_interact_object(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        object_id = str(cmd.params["object_id"]).strip()
        action = str(cmd.params.get("action", "examine")).strip() or "examine"
        area_id = state.player.current_area
        if not area_id:
            return ExecuteResult.error("player has no current area")

        area_state = state.areas.areas.get(area_id)
        if area_state is None:
            return ExecuteResult.error(f"unknown area: {area_id}")

        interactables = area_state.properties.get("interactables", {})
        if not isinstance(interactables, dict):
            interactables = {}
        obj_data = interactables.get(object_id)
        if obj_data is None:
            return ExecuteResult.error(f"object not found: {object_id}")
        if not isinstance(obj_data, dict):
            obj_data = {}

        obj_type = str(obj_data.get("type", "generic"))
        description = str(obj_data.get("description", ""))
        requires_check = bool(obj_data.get("requires_check", False))

        metadata: dict[str, Any] = {
            "status": "examined",
            "object_id": object_id,
            "action": action,
            "object_type": obj_type,
            "description": description,
        }
        if requires_check:
            metadata["requires_check"] = True
            check_skill = str(obj_data.get("check_skill", ""))
            check_dc = coerce_int(obj_data.get("check_dc"))
            if check_skill:
                metadata["check_skill"] = check_skill
            if check_dc is not None:
                metadata["check_dc"] = check_dc

        return handler_success_no_delta(
            "container",
            "interact_object",
            time_cost=1.0 / 6.0,
            metadata=metadata,
        )

    def _validate_container_presence(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult | None:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")
        if not state.has_slice("areas"):
            return ValidationResult(ok=False, reason="areas slice is required")
        container_id = get_non_empty_string(cmd.params, "container_id")
        if container_id is None:
            return ValidationResult(
                ok=False,
                reason="container_id must be a non-empty string",
            )
        if self._resolve_container(state, container_id) is None:
            return ValidationResult(ok=False, reason=f"unknown container: {container_id}")
        return None

    def _validate_open_unlocked_container(
        self,
        container_state: Mapping[str, Any],
    ) -> ValidationResult | None:
        if not bool(container_state.get("opened", False)):
            return ValidationResult(ok=False, reason="container must be opened")
        if str(container_state.get("lock_status", "unlocked")) == "locked":
            return ValidationResult(ok=False, reason="container is locked")
        return None

    def _resolve_container(
        self,
        state: StateContainer,
        container_id: str,
    ) -> tuple[str, dict[str, Any]] | None:
        current_area = state.player.current_area
        if current_area and current_area in state.areas.areas:
            current_state = state.areas.get_container_state(current_area, container_id)
            if current_state is not None:
                current_state["area_id"] = current_state.get("area_id") or current_area
                return current_area, current_state
        area_id = state.areas.find_container_area(container_id)
        if area_id is None:
            return None
        container_state = state.areas.get_container_state(area_id, container_id)
        if container_state is None:
            return None
        container_state["area_id"] = container_state.get("area_id") or area_id
        return area_id, container_state

    def _find_container_item(
        self,
        container_state: Mapping[str, Any],
        item_id: str,
    ) -> dict[str, Any] | None:
        for item in self._container_items(container_state):
            if get_non_empty_string(item, "item_id") == item_id:
                return item
        return None

    def _container_items(
        self,
        container_state: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        raw_items = container_state.get("remaining_items", [])
        if not isinstance(raw_items, list):
            return []
        items: list[dict[str, Any]] = []
        for item in raw_items:
            if isinstance(item, Mapping):
                items.append(dict(item))
        return items

    def _remove_from_container_snapshot(
        self,
        container_state: Mapping[str, Any],
        item_id: str,
        count: int,
    ) -> dict[str, Any]:
        updated_container = dict(container_state)
        remaining_to_remove = count
        updated_items: list[dict[str, Any]] = []
        for item in self._container_items(container_state):
            if get_non_empty_string(item, "item_id") != item_id or remaining_to_remove <= 0:
                updated_items.append(item)
                continue
            current = int(item.get("count", 0))
            next_count = current - remaining_to_remove
            if next_count > 0:
                next_item = dict(item)
                next_item["count"] = next_count
                updated_items.append(next_item)
                remaining_to_remove = 0
                continue
            remaining_to_remove = max(0, -next_count)
        updated_container["remaining_items"] = updated_items
        return updated_container

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
        inventory.append({"item_id": item_id, "count": count, "tags": list(tags or [])})

    def _trap_damage(self, container_state: Mapping[str, Any]) -> int:
        direct = coerce_int(container_state.get("trap_damage"))
        if direct is not None and direct >= 0:
            return direct
        trap_payload = container_state.get("trap")
        if isinstance(trap_payload, Mapping):
            nested = coerce_int(trap_payload.get("damage"))
            if nested is not None and nested >= 0:
                return nested
        return 0

    def _trap_disarm_dc(self, container_state: Mapping[str, Any]) -> int:
        direct = coerce_int(container_state.get("trap_disarm_dc"))
        if direct is not None and direct >= 0:
            return direct
        trap_payload = container_state.get("trap")
        if isinstance(trap_payload, Mapping):
            nested = coerce_int(trap_payload.get("disarm_dc"))
            if nested is not None and nested >= 0:
                return nested
        return 12

