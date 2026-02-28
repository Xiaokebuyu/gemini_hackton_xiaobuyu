"""CrimeHandler implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content import WorldInstance
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer, StateDelta


class CrimeHandler(StaticCommandHandler):
    COMMAND_TYPES = ("steal", "lockpick")

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        del world
        if cmd.type == "steal":
            return self._validate_steal(cmd, state)
        if cmd.type == "lockpick":
            return self._validate_lockpick(cmd, state)
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

        if cmd.type == "steal":
            return self._compute_steal(cmd, state)
        if cmd.type == "lockpick":
            return self._compute_lockpick(cmd, state)
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _validate_steal(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")
        if not state.has_slice("areas"):
            return ValidationResult(ok=False, reason="areas slice is required")

        item_id = self._get_non_empty_string(cmd.params, "item_id")
        if item_id is None:
            return ValidationResult(ok=False, reason="item_id must be a non-empty string")
        count = self._coerce_int(cmd.params.get("count", 1))
        if count is None or count < 1:
            return ValidationResult(ok=False, reason="count must be an integer >= 1")
        if "dc" in cmd.params:
            dc = self._coerce_int(cmd.params.get("dc"))
            if dc is None or dc < 0:
                return ValidationResult(ok=False, reason="dc must be an integer >= 0")

        container_id = self._resolve_container_id(cmd.params)
        if container_id is None:
            if self._get_non_empty_string(cmd.params, "target_npc") is not None:
                return ValidationResult(
                    ok=False,
                    reason="NPC theft unsupported in MVP",
                )
            return ValidationResult(
                ok=False,
                reason="container_id/container is required",
            )

        resolved = self._resolve_container(state, container_id)
        if resolved is None:
            return ValidationResult(ok=False, reason=f"unknown container: {container_id}")
        _, container_state = resolved

        if not bool(container_state.get("opened", False)):
            return ValidationResult(ok=False, reason="container must be opened")
        if str(container_state.get("lock_status", "unlocked")) == "locked":
            return ValidationResult(ok=False, reason="container is locked")
        if bool(container_state.get("looted", False)):
            return ValidationResult(ok=False, reason="container already looted")

        item_entry = self._find_container_item(container_state, item_id)
        if item_entry is None or int(item_entry.get("count", 0)) < count:
            return ValidationResult(
                ok=False,
                reason=f"not enough container items: {item_id}",
            )
        return ValidationResult(ok=True)

    def _validate_lockpick(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")
        if not state.has_slice("areas"):
            return ValidationResult(ok=False, reason="areas slice is required")

        container_id = self._resolve_container_id(cmd.params)
        if container_id is None:
            return ValidationResult(
                ok=False,
                reason="container_id/target must be a non-empty string",
            )
        if "dc" in cmd.params:
            dc = self._coerce_int(cmd.params.get("dc"))
            if dc is None or dc < 0:
                return ValidationResult(ok=False, reason="dc must be an integer >= 0")

        resolved = self._resolve_container(state, container_id)
        if resolved is None:
            return ValidationResult(ok=False, reason=f"unknown container: {container_id}")
        _, container_state = resolved
        if str(container_state.get("lock_status", "unlocked")) != "locked":
            return ValidationResult(ok=False, reason="container is not locked")
        return ValidationResult(ok=True)

    def _compute_steal(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        item_id = str(cmd.params["item_id"]).strip()
        count = int(cmd.params.get("count", 1))
        container_id = self._resolve_container_id(cmd.params) or ""
        resolved = self._resolve_container(state, container_id)
        if resolved is None:
            return ExecuteResult.error(f"unknown container: {container_id}")
        area_id, container_state = resolved

        dc = self._coerce_int(cmd.params.get("dc", 12)) or 12
        passive_total = self._stealth_total(state)
        passed = passive_total >= dc
        if not passed:
            return self._success_no_delta(
                "steal",
                time_cost=1.0 / 6.0,
                metadata={
                    "status": "detected",
                    "container_id": container_id,
                    "item_id": item_id,
                    "count": count,
                    "dc": dc,
                    "passive_total": passive_total,
                    "passed": False,
                    "detected": True,
                },
            )

        updated_inventory = self._player_inventory_snapshot(state)
        item_entry = self._find_container_item(container_state, item_id)
        tags = self._normalize_tags(item_entry.get("tags", []) if item_entry is not None else [])
        self._add_to_inventory_snapshot(updated_inventory, item_id, count, tags)

        updated_container = self._remove_from_container_snapshot(container_state, item_id, count)
        updated_container["area_id"] = area_id
        if not self._container_items(updated_container) and int(updated_container.get("remaining_gold", 0)) == 0:
            updated_container["looted"] = True

        return self._success(
            "steal",
            changes=[
                StateChange("player", "set", "inventory", updated_inventory),
                StateChange(
                    "areas",
                    "modify",
                    f"container_states.{container_id}",
                    updated_container,
                ),
            ],
            time_cost=1.0 / 6.0,
            metadata={
                "status": "stolen",
                "container_id": container_id,
                "item_id": item_id,
                "count": count,
                "dc": dc,
                "passive_total": passive_total,
                "passed": True,
                "detected": False,
            },
        )

    def _compute_lockpick(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        container_id = self._resolve_container_id(cmd.params) or ""
        resolved = self._resolve_container(state, container_id)
        if resolved is None:
            return ExecuteResult.error(f"unknown container: {container_id}")
        area_id, container_state = resolved

        dc = self._coerce_int(cmd.params.get("dc"))
        if dc is None:
            dc = self._coerce_int(container_state.get("lock_dc")) or 12
        passive_total = self._thieves_tools_total(state)
        passed = passive_total >= dc
        if not passed:
            return self._success_no_delta(
                "lockpick",
                time_cost=1.0 / 6.0,
                metadata={
                    "status": "failed",
                    "container_id": container_id,
                    "dc": dc,
                    "passive_total": passive_total,
                    "passed": False,
                },
            )

        updated_container = dict(container_state)
        updated_container["area_id"] = area_id
        updated_container["lock_status"] = "unlocked"
        return self._success(
            "lockpick",
            changes=[
                StateChange(
                    "areas",
                    "modify",
                    f"container_states.{container_id}",
                    updated_container,
                )
            ],
            time_cost=1.0 / 6.0,
            metadata={
                "status": "unlocked",
                "container_id": container_id,
                "dc": dc,
                "passive_total": passive_total,
                "passed": True,
            },
        )

    def _resolve_container(
        self,
        state: StateContainer,
        container_id: str,
    ) -> tuple[str, dict[str, Any]] | None:
        current_area = state.player.current_area if state.has_slice("player") else ""
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
            if self._get_non_empty_string(item, "item_id") == item_id:
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
            if self._get_non_empty_string(item, "item_id") != item_id or remaining_to_remove <= 0:
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

    def _success(
        self,
        command_type: str,
        *,
        changes: list[StateChange],
        metadata: dict[str, Any],
        time_cost: float = 0.0,
    ) -> ExecuteResult:
        payload = {"handler": "crime", "command": command_type, **metadata}
        delta = (
            StateDelta(changes=changes, reason=command_type, metadata=payload)
            if changes
            else None
        )
        return ExecuteResult(
            success=True,
            delta=delta,
            time_cost=time_cost,
            metadata=payload,
        )

    def _success_no_delta(
        self,
        command_type: str,
        *,
        metadata: dict[str, Any],
        time_cost: float = 0.0,
    ) -> ExecuteResult:
        return ExecuteResult(
            success=True,
            delta=None,
            time_cost=time_cost,
            metadata={"handler": "crime", "command": command_type, **metadata},
        )

    @staticmethod
    def _stealth_total(state: StateContainer) -> int:
        return 10 + state.player.get_skill_bonus("stealth")

    @staticmethod
    def _thieves_tools_total(state: StateContainer) -> int:
        return 10 + state.player.get_skill_bonus("sleight_of_hand")

    @staticmethod
    def _resolve_container_id(params: Mapping[str, Any]) -> str | None:
        container_id = CrimeHandler._get_non_empty_string(params, "container_id")
        if container_id is not None:
            return container_id
        container_id = CrimeHandler._get_non_empty_string(params, "container")
        if container_id is not None:
            return container_id
        return CrimeHandler._get_non_empty_string(params, "target")

    @staticmethod
    def _get_non_empty_string(params: Mapping[str, Any], key: str) -> str | None:
        value = params.get(key)
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if not value.is_integer():
                return None
            return int(value)
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return None
            try:
                return int(normalized)
            except ValueError:
                return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_tags(raw: Any) -> list[str]:
        if not isinstance(raw, list):
            return []
        return [str(tag) for tag in raw]
