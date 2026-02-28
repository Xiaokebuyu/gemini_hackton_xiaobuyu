"""RestHandler implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content import WorldInstance
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer, StateDelta


class RestHandler(StaticCommandHandler):
    COMMAND_TYPES = ("rest_short", "rest_long", "night_watch", "set_camp")
    _CAMP_TYPES = frozenset({"safe", "wilderness"})

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if cmd.type == "rest_short":
            return self._validate_rest_short(cmd, state)
        if cmd.type == "rest_long":
            return self._validate_rest_long(cmd, state)
        if cmd.type == "night_watch":
            return self._validate_night_watch(cmd, state, world)
        if cmd.type == "set_camp":
            return self._validate_set_camp(cmd, state, world)
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

        if cmd.type == "rest_short":
            return self._compute_rest_short(state)
        if cmd.type == "rest_long":
            return self._compute_rest_long(state)
        if cmd.type == "night_watch":
            return self._compute_night_watch(cmd, state)
        if cmd.type == "set_camp":
            return self._compute_set_camp(cmd, state)
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _validate_rest_short(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")
        identity_check = self._validate_character_identity(cmd.params, state)
        if identity_check is not None:
            return identity_check
        return ValidationResult(ok=True)

    def _validate_rest_long(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")
        identity_check = self._validate_character_identity(cmd.params, state)
        if identity_check is not None:
            return identity_check
        return ValidationResult(ok=True)

    def _validate_night_watch(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")
        if not state.has_slice("areas"):
            return ValidationResult(ok=False, reason="areas slice is required")
        area_id = self._get_non_empty_string(cmd.params, "area_id")
        if area_id is None:
            return ValidationResult(ok=False, reason="area_id must be a non-empty string")
        if not self._area_exists(area_id, state, world):
            return ValidationResult(ok=False, reason=f"unknown area: {area_id}")
        if "camp_type" in cmd.params:
            camp_type = self._get_non_empty_string(cmd.params, "camp_type")
            if camp_type is None or camp_type not in self._CAMP_TYPES:
                return ValidationResult(
                    ok=False,
                    reason="camp_type must be 'safe' or 'wilderness'",
                )
        return ValidationResult(ok=True)

    def _validate_set_camp(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not state.has_slice("areas"):
            return ValidationResult(ok=False, reason="areas slice is required")
        area_id = self._get_non_empty_string(cmd.params, "area_id")
        if area_id is None:
            return ValidationResult(ok=False, reason="area_id must be a non-empty string")
        if not self._area_exists(area_id, state, world):
            return ValidationResult(ok=False, reason=f"unknown area: {area_id}")
        if state.has_slice("player") and state.player.current_area:
            if area_id != state.player.current_area:
                return ValidationResult(
                    ok=False,
                    reason="camp can only be set in the current player area",
                )
        if "location_id" in cmd.params:
            location_id = self._get_non_empty_string(cmd.params, "location_id")
            if location_id is None:
                return ValidationResult(
                    ok=False,
                    reason="location_id must be a non-empty string",
                )
        if "camp_type" in cmd.params:
            camp_type = self._get_non_empty_string(cmd.params, "camp_type")
            if camp_type is None or camp_type not in self._CAMP_TYPES:
                return ValidationResult(
                    ok=False,
                    reason="camp_type must be 'safe' or 'wilderness'",
                )
        return ValidationResult(ok=True)

    def _compute_rest_short(
        self,
        state: StateContainer,
    ) -> ExecuteResult:
        changes: list[StateChange] = []
        healed = 0
        heal_amount = (int(state.player.max_hp) + 3) // 4
        if heal_amount > 0:
            healed = min(heal_amount, max(0, int(state.player.max_hp) - int(state.player.hp)))
            if healed > 0:
                changes.append(StateChange("player", "add", "hp", healed))

        restored_keys: list[str] = []
        for key, resource in self._class_resources_snapshot(state).items():
            if str(resource.get("recovery", "long_rest")) != "short_rest":
                continue
            current = int(resource.get("current", 0))
            maximum = int(resource.get("max", 0))
            if current >= maximum:
                continue
            updated = dict(resource)
            updated["current"] = maximum
            restored_keys.append(key)
            changes.append(StateChange("player", "modify", f"class_resources.{key}", updated))

        filtered_effects, removed_effect_count = self._filter_effects_by_cure(state, "short_rest")
        if removed_effect_count > 0:
            changes.append(StateChange("player", "set", "active_effects", filtered_effects))

        return self._success(
            "rest_short",
            changes=changes,
            time_cost=1.0,
            metadata={
                "status": "short_rest",
                "healed": healed,
                "restored_resource_keys": restored_keys,
                "removed_effect_count": removed_effect_count,
            },
        )

    def _compute_rest_long(
        self,
        state: StateContainer,
    ) -> ExecuteResult:
        changes: list[StateChange] = []

        if int(state.player.hp) != int(state.player.max_hp):
            changes.append(StateChange("player", "set", "hp", int(state.player.max_hp)))

        restored_spell_slot_levels: list[int] = []
        for level, slot_state in self._spell_slots_snapshot(state).items():
            current = int(slot_state.get("current", 0))
            maximum = int(slot_state.get("max", 0))
            if current >= maximum:
                continue
            restored_spell_slot_levels.append(level)
            changes.append(
                StateChange(
                    "player",
                    "modify",
                    f"spell_slots.{level}",
                    {"current": maximum, "max": maximum},
                )
            )

        restored_resource_keys: list[str] = []
        for key, resource in self._class_resources_snapshot(state).items():
            current = int(resource.get("current", 0))
            maximum = int(resource.get("max", 0))
            if current >= maximum:
                continue
            updated = dict(resource)
            updated["current"] = maximum
            restored_resource_keys.append(key)
            changes.append(StateChange("player", "modify", f"class_resources.{key}", updated))

        broke_concentration = state.player.concentration is not None
        if broke_concentration:
            changes.append(StateChange("player", "set", "concentration", None))

        filtered_effects, removed_effect_count = self._filter_effects_by_cure(state, "long_rest")
        if removed_effect_count > 0:
            changes.append(StateChange("player", "set", "active_effects", filtered_effects))

        camp_type = self._resolve_camp_type(state)
        time_cost = self._long_rest_time_cost(state)

        return self._success(
            "rest_long",
            changes=changes,
            time_cost=time_cost,
            metadata={
                "status": "long_rest",
                "camp_type": camp_type,
                "night_watch_required": camp_type != "safe",
                "restored_spell_slot_levels": restored_spell_slot_levels,
                "restored_resource_keys": restored_resource_keys,
                "removed_effect_count": removed_effect_count,
                "broke_concentration": broke_concentration,
                "time_cost": time_cost,
            },
        )

    def _compute_night_watch(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        area_id = str(cmd.params["area_id"]).strip()
        camp_type = self._resolve_explicit_camp_type(cmd.params) or self._resolve_camp_type(
            state,
            area_id=area_id,
        )
        if camp_type == "safe":
            return self._success_no_delta(
                "night_watch",
                metadata={
                    "status": "skipped_safe_camp",
                    "area_id": area_id,
                    "camp_type": camp_type,
                    "dc": None,
                    "passive_total": None,
                    "passed": True,
                    "ambush": False,
                    "recovery_multiplier": 1.0,
                },
            )

        danger = float(state.areas.get_danger(area_id))
        dc = self._night_watch_dc(danger)
        passive_total = 10 + state.player.get_skill_bonus("perception")
        passed = passive_total >= dc
        return self._success_no_delta(
            "night_watch",
            metadata={
                "status": "checked",
                "area_id": area_id,
                "camp_type": camp_type,
                "dc": dc,
                "passive_total": passive_total,
                "passed": passed,
                "ambush": not passed,
                "recovery_multiplier": 1.0 if passed else 0.75,
            },
        )

    def _compute_set_camp(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        area_id = str(cmd.params["area_id"]).strip()
        resolved_location_id = self._get_non_empty_string(cmd.params, "location_id")
        if resolved_location_id is None and state.has_slice("player"):
            resolved_location_id = state.player.current_location
        camp_type = self._resolve_explicit_camp_type(cmd.params) or "wilderness"
        payload = {
            "camp_type": camp_type,
            "location_id": resolved_location_id,
            "created_at_tick": int(state.time.absolute_tick()) if state.has_slice("time") else None,
            "source": "rest",
        }
        return self._success(
            "set_camp",
            changes=[
                StateChange(
                    "areas",
                    "modify",
                    f"{area_id}.properties.active_camp",
                    payload,
                )
            ],
            time_cost=1.0 / 6.0,
            metadata={
                "status": "camp_set",
                "area_id": area_id,
                "location_id": resolved_location_id,
                "camp_type": camp_type,
            },
        )

    def _filter_effects_by_cure(
        self,
        state: StateContainer,
        cure_key: str,
    ) -> tuple[list[dict[str, Any]], int]:
        effects = state.player.snapshot().get("active_effects", [])
        if not isinstance(effects, list):
            return [], 0
        filtered: list[dict[str, Any]] = []
        removed = 0
        for effect in effects:
            if not isinstance(effect, Mapping):
                continue
            cure_conditions = effect.get("cure_conditions", [])
            if isinstance(cure_conditions, list) and cure_key in {
                str(value) for value in cure_conditions
            }:
                removed += 1
                continue
            filtered.append(dict(effect))
        return filtered, removed

    def _class_resources_snapshot(self, state: StateContainer) -> dict[str, dict[str, Any]]:
        raw = state.player.snapshot().get("class_resources", {})
        if not isinstance(raw, Mapping):
            return {}
        resources: dict[str, dict[str, Any]] = {}
        for key, value in raw.items():
            if isinstance(value, Mapping):
                resources[str(key)] = dict(value)
        return resources

    def _spell_slots_snapshot(self, state: StateContainer) -> dict[int, dict[str, Any]]:
        raw = state.player.snapshot().get("spell_slots", {})
        if not isinstance(raw, Mapping):
            return {}
        slots: dict[int, dict[str, Any]] = {}
        for key, value in raw.items():
            if not isinstance(value, Mapping):
                continue
            level = self._coerce_int(key)
            if level is None:
                continue
            slots[level] = dict(value)
        return slots

    def _resolve_camp_type(
        self,
        state: StateContainer,
        *,
        area_id: str | None = None,
    ) -> str:
        if not state.has_slice("areas"):
            return "wilderness"
        target_area = area_id
        if target_area is None and state.has_slice("player"):
            target_area = state.player.current_area or None
        if not target_area:
            return "wilderness"
        area = state.areas.areas.get(target_area)
        if area is None:
            return "wilderness"
        active_camp = area.properties.get("active_camp")
        if isinstance(active_camp, Mapping):
            camp_type = self._get_non_empty_string(active_camp, "camp_type")
            if camp_type in self._CAMP_TYPES:
                return camp_type
        return "wilderness"

    def _long_rest_time_cost(self, state: StateContainer) -> float:
        if not state.has_slice("time"):
            return 8.0
        current_slot = int(state.time.slot)
        return float((24 - current_slot) + 5)

    @staticmethod
    def _night_watch_dc(danger: float) -> int:
        if danger <= 0.5:
            return 8
        if danger <= 1.0:
            return 12
        if danger <= 1.5:
            return 15
        return 18

    def _area_exists(
        self,
        area_id: str,
        state: StateContainer,
        world: WorldInstance,
    ) -> bool:
        if world.has_registry("maps"):
            return world.maps.get(area_id) is not None
        return area_id in state.areas.areas

    def _validate_character_identity(
        self,
        params: Mapping[str, Any],
        state: StateContainer,
    ) -> ValidationResult | None:
        if "character" not in params:
            return None
        character = self._get_non_empty_string(params, "character")
        if character is None:
            return ValidationResult(
                ok=False,
                reason="character must be a non-empty string",
            )
        if character == "player":
            return None
        if state.player.character_id and character == state.player.character_id:
            return None
        return ValidationResult(
            ok=False,
            reason="character must reference the current player",
        )

    def _success(
        self,
        command_type: str,
        *,
        changes: list[StateChange],
        metadata: dict[str, Any],
        time_cost: float = 0.0,
    ) -> ExecuteResult:
        payload = {"handler": "rest", "command": command_type, **metadata}
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
            metadata={"handler": "rest", "command": command_type, **metadata},
        )

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

    def _resolve_explicit_camp_type(self, params: Mapping[str, Any]) -> str | None:
        camp_type = self._get_non_empty_string(params, "camp_type")
        if camp_type in self._CAMP_TYPES:
            return camp_type
        return None
