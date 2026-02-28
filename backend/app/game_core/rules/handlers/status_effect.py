"""StatusEffectHandler implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content import WorldInstance
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer, StateDelta


class StatusEffectHandler(StaticCommandHandler):
    COMMAND_TYPES = (
        "apply_effect",
        "remove_effect",
        "remove_effect_by_type",
        "tick_effects",
    )

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        del world
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")

        if cmd.type == "apply_effect":
            return self._validate_apply_effect(cmd)
        if cmd.type == "remove_effect":
            return self._validate_remove_effect(cmd)
        if cmd.type == "remove_effect_by_type":
            return self._validate_remove_effect_by_type(cmd)
        if cmd.type == "tick_effects":
            return ValidationResult(ok=True)
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

        if cmd.type == "apply_effect":
            return self._compute_apply_effect(cmd, state)
        if cmd.type == "remove_effect":
            return self._compute_remove_effect(cmd, state)
        if cmd.type == "remove_effect_by_type":
            return self._compute_remove_effect_by_type(cmd, state)
        if cmd.type == "tick_effects":
            return self._compute_tick_effects(state)
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _validate_apply_effect(self, cmd: Command) -> ValidationResult:
        effect_id = self._coerce_non_empty_string(cmd.params.get("effect_id"))
        if effect_id is None:
            return ValidationResult(ok=False, reason="effect_id must be a non-empty string")

        effect_type = self._coerce_non_empty_string(cmd.params.get("effect_type"))
        if effect_type is None:
            return ValidationResult(
                ok=False,
                reason="effect_type must be a non-empty string",
            )

        duration_ticks = self._coerce_duration_ticks(cmd.params)
        if duration_ticks is None:
            return ValidationResult(
                ok=False,
                reason="duration_ticks/duration must be an integer >= 0",
            )

        if "modifiers" in cmd.params and not isinstance(cmd.params.get("modifiers"), Mapping):
            return ValidationResult(ok=False, reason="modifiers must be a mapping")
        if "periodic" in cmd.params and not isinstance(cmd.params.get("periodic"), Mapping):
            return ValidationResult(ok=False, reason="periodic must be a mapping")
        if "tags" in cmd.params and not isinstance(cmd.params.get("tags"), list):
            return ValidationResult(ok=False, reason="tags must be a list")
        return ValidationResult(ok=True)

    def _validate_remove_effect(self, cmd: Command) -> ValidationResult:
        effect_id = self._coerce_non_empty_string(cmd.params.get("effect_id"))
        if effect_id is None:
            return ValidationResult(ok=False, reason="effect_id must be a non-empty string")
        return ValidationResult(ok=True)

    def _validate_remove_effect_by_type(self, cmd: Command) -> ValidationResult:
        effect_type = self._coerce_effect_type_for_removal(cmd.params)
        if effect_type is None:
            return ValidationResult(
                ok=False,
                reason="effect_type/effect_id must be a non-empty string",
            )
        return ValidationResult(ok=True)

    def _compute_apply_effect(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        effect = self._build_effect_payload(cmd)
        current_effects = self._copy_effects(state)
        replaced_count = 0
        next_effects: list[dict[str, Any]] = []
        for existing in current_effects:
            if existing.get("effect_id") == effect["effect_id"]:
                replaced_count += 1
                continue
            next_effects.append(existing)
        next_effects.append(effect)
        return self._success(
            cmd.type,
            changes=[
                StateChange(
                    slice="player",
                    operation="set",
                    path="active_effects",
                    value=next_effects,
                )
            ],
            metadata={
                "applied_count": 1,
                "removed_count": replaced_count,
                "expired_count": 0,
                "hp_delta": 0,
                "effect_id": effect["effect_id"],
                "effect_type": effect["effect_type"],
            },
        )

    def _compute_remove_effect(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        effect_id = str(cmd.params["effect_id"]).strip()
        current_effects = self._copy_effects(state)
        next_effects = [
            effect
            for effect in current_effects
            if effect.get("effect_id") != effect_id
        ]
        removed_count = len(current_effects) - len(next_effects)
        return self._success(
            cmd.type,
            changes=[
                StateChange(
                    slice="player",
                    operation="set",
                    path="active_effects",
                    value=next_effects,
                )
            ],
            metadata={
                "applied_count": 0,
                "removed_count": removed_count,
                "expired_count": 0,
                "hp_delta": 0,
                "effect_id": effect_id,
            },
        )

    def _compute_remove_effect_by_type(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        effect_type = self._coerce_effect_type_for_removal(cmd.params) or ""
        current_effects = self._copy_effects(state)
        next_effects = [
            effect
            for effect in current_effects
            if effect.get("effect_type") != effect_type
        ]
        removed_count = len(current_effects) - len(next_effects)
        return self._success(
            cmd.type,
            changes=[
                StateChange(
                    slice="player",
                    operation="set",
                    path="active_effects",
                    value=next_effects,
                )
            ],
            metadata={
                "applied_count": 0,
                "removed_count": removed_count,
                "expired_count": 0,
                "hp_delta": 0,
                "effect_type": effect_type,
            },
        )

    def _compute_tick_effects(self, state: StateContainer) -> ExecuteResult:
        current_effects = self._copy_effects(state)
        current_hp = int(state.player.hp)
        next_hp = current_hp
        max_hp = int(state.player.max_hp)
        next_effects: list[dict[str, Any]] = []
        expired_count = 0
        processed_count = 0

        for raw_effect in current_effects:
            effect = dict(raw_effect)
            periodic = effect.get("periodic", {})
            if isinstance(periodic, Mapping):
                damage = self._coerce_non_negative_int(periodic.get("damage"), default=0)
                heal = self._coerce_non_negative_int(periodic.get("heal"), default=0)
                if damage > 0:
                    next_hp = max(0, next_hp - damage)
                if heal > 0:
                    next_hp = min(max_hp, next_hp + heal)

            remaining_ticks = self._coerce_effect_remaining(effect)
            if remaining_ticks is None:
                next_effects.append(effect)
                processed_count += 1
                continue

            remaining_ticks -= 1
            effect["remaining_ticks"] = remaining_ticks
            effect["remaining_duration"] = remaining_ticks
            processed_count += 1
            if remaining_ticks <= 0:
                expired_count += 1
                continue
            next_effects.append(effect)

        hp_delta = next_hp - current_hp
        changes: list[StateChange] = [
            StateChange(
                slice="player",
                operation="set",
                path="active_effects",
                value=next_effects,
            )
        ]
        if hp_delta != 0:
            changes.append(
                StateChange(
                    slice="player",
                    operation="add",
                    path="hp",
                    value=hp_delta,
                )
            )

        return self._success(
            "tick_effects",
            changes=changes,
            metadata={
                "applied_count": processed_count,
                "removed_count": expired_count,
                "expired_count": expired_count,
                "hp_delta": hp_delta,
            },
        )

    def _build_effect_payload(self, cmd: Command) -> dict[str, Any]:
        duration_ticks = self._coerce_duration_ticks(cmd.params)
        assert duration_ticks is not None
        modifiers = self._normalize_mapping(cmd.params.get("modifiers"))
        raw_periodic = cmd.params.get("periodic")
        periodic = {}
        if isinstance(raw_periodic, Mapping):
            periodic = {
                "damage": self._coerce_non_negative_int(raw_periodic.get("damage"), default=0),
                "heal": self._coerce_non_negative_int(raw_periodic.get("heal"), default=0),
            }
        return {
            "effect_id": str(cmd.params["effect_id"]).strip(),
            "effect_type": str(cmd.params["effect_type"]).strip(),
            "source": self._coerce_non_empty_string(cmd.params.get("source")) or cmd.source,
            "remaining_ticks": duration_ticks,
            "duration_ticks": duration_ticks,
            "remaining_duration": duration_ticks,
            "modifiers": modifiers,
            "periodic": periodic,
            "tags": self._normalize_tags(cmd.params.get("tags")),
        }

    def _success(
        self,
        command_type: str,
        *,
        changes: list[StateChange],
        metadata: dict[str, Any],
    ) -> ExecuteResult:
        return ExecuteResult(
            success=True,
            delta=StateDelta(
                changes=changes,
                reason=command_type,
                metadata={
                    "handler": "status_effect",
                    "command": command_type,
                    **metadata,
                },
            ),
            metadata={
                "handler": "status_effect",
                "command": command_type,
                **metadata,
            },
        )

    @staticmethod
    def _copy_effects(state: StateContainer) -> list[dict[str, Any]]:
        return [dict(effect) for effect in state.player.get_active_effects()]

    @staticmethod
    def _normalize_mapping(raw_value: Any) -> dict[str, Any]:
        if not isinstance(raw_value, Mapping):
            return {}
        return {str(key): value for key, value in raw_value.items()}

    @staticmethod
    def _normalize_tags(raw_value: Any) -> list[str]:
        if not isinstance(raw_value, list):
            return []
        return [str(tag) for tag in raw_value]

    @staticmethod
    def _coerce_non_empty_string(raw_value: Any) -> str | None:
        if not isinstance(raw_value, str):
            return None
        normalized = raw_value.strip()
        return normalized or None

    @classmethod
    def _coerce_effect_type_for_removal(cls, params: Mapping[str, Any]) -> str | None:
        return cls._coerce_non_empty_string(
            params.get("effect_type", params.get("effect_id"))
        )

    @staticmethod
    def _coerce_duration_ticks(params: Mapping[str, Any]) -> int | None:
        raw_value = params.get("duration_ticks", params.get("duration"))
        if not isinstance(raw_value, int):
            return None
        if raw_value < 0:
            return None
        return raw_value

    @staticmethod
    def _coerce_effect_remaining(effect: Mapping[str, Any]) -> int | None:
        raw_value = effect.get("remaining_ticks", effect.get("remaining_duration"))
        if not isinstance(raw_value, int):
            return None
        if raw_value < 0:
            return None
        return raw_value

    @staticmethod
    def _coerce_non_negative_int(raw_value: Any, *, default: int) -> int:
        if not isinstance(raw_value, int):
            return default
        return max(0, raw_value)
