"""StatusEffectHandler implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content import WorldInstance
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.handler_utils import (
    coerce_non_empty_string,
    handler_success,
    normalize_tags,
)
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer


class StatusEffectHandler(StaticCommandHandler):
    COMMAND_TYPES = (
        "apply_effect",
        "remove_effect",
        "remove_effect_by_type",
        "tick_effects",
        "tick_combat_effects",
    )

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        del world
        if cmd.type == "tick_combat_effects":
            if not state.has_slice("areas"):
                return ValidationResult(ok=False, reason="areas slice is required")
            return ValidationResult(ok=True)

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
        if cmd.type == "tick_combat_effects":
            return self._compute_tick_combat_effects(state)
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _validate_apply_effect(self, cmd: Command) -> ValidationResult:
        effect_id = coerce_non_empty_string(cmd.params.get("effect_id"))
        if effect_id is None:
            return ValidationResult(ok=False, reason="effect_id must be a non-empty string")

        effect_type = coerce_non_empty_string(cmd.params.get("effect_type"))
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
        effect_id = coerce_non_empty_string(cmd.params.get("effect_id"))
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
        return handler_success(
            "status_effect",
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
            omit_empty_delta=False,
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
        return handler_success(
            "status_effect",
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
            omit_empty_delta=False,
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
        return handler_success(
            "status_effect",
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
            omit_empty_delta=False,
        )

    def _tick_effect_list(
        self,
        effects: list[dict[str, Any]],
        hp: int,
        max_hp: int,
    ) -> tuple[list[dict[str, Any]], int, int, int]:
        """Tick a list of effects. Returns (next_effects, next_hp, expired_count, processed_count)."""
        next_effects: list[dict[str, Any]] = []
        next_hp = hp
        expired_count = 0
        processed_count = 0

        for raw_effect in effects:
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

        return next_effects, next_hp, expired_count, processed_count

    def _compute_tick_effects(self, state: StateContainer) -> ExecuteResult:
        current_effects = self._copy_effects(state)
        current_hp = int(state.player.hp)
        max_hp = int(state.player.max_hp)

        next_effects, next_hp, expired_count, processed_count = self._tick_effect_list(
            current_effects, current_hp, max_hp
        )

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

        return handler_success(
            "status_effect",
            "tick_effects",
            changes=changes,
            metadata={
                "applied_count": processed_count,
                "removed_count": expired_count,
                "expired_count": expired_count,
                "hp_delta": hp_delta,
            },
            omit_empty_delta=False,
        )

    def _compute_tick_combat_effects(self, state: StateContainer) -> ExecuteResult:
        changes: list[StateChange] = []
        combats_processed = 0
        participants_ticked = 0
        effects_expired = 0
        total_hp_delta = 0

        for area_id, area in state.areas.areas.items():
            for sub_area_id, hostile in area.hostile_tracking.items():
                if not isinstance(hostile, Mapping):
                    continue
                if not hostile.get("combat_active"):
                    continue
                raw_participants = hostile.get("participants", [])
                if not isinstance(raw_participants, list):
                    continue

                combats_processed += 1
                updated_participants: list[dict[str, Any]] = []
                combat_changed = False

                for participant in raw_participants:
                    if not isinstance(participant, Mapping):
                        updated_participants.append(dict(participant) if isinstance(participant, dict) else {})
                        continue
                    p = dict(participant)
                    if not p.get("alive", True):
                        updated_participants.append(p)
                        continue

                    raw_effects = p.get("active_effects", [])
                    if not isinstance(raw_effects, list) or not raw_effects:
                        updated_participants.append(p)
                        continue

                    p_hp = self._coerce_non_negative_int(p.get("hp"), default=0)
                    p_max_hp = self._coerce_non_negative_int(p.get("max_hp"), default=p_hp)
                    effect_list = [dict(e) for e in raw_effects if isinstance(e, Mapping)]

                    next_effects, next_hp, expired, _ = self._tick_effect_list(
                        effect_list, p_hp, p_max_hp
                    )

                    if next_effects != effect_list or next_hp != p_hp:
                        combat_changed = True
                        participants_ticked += 1
                        effects_expired += expired
                        total_hp_delta += next_hp - p_hp
                        p["active_effects"] = next_effects
                        p["hp"] = next_hp
                        if next_hp <= 0:
                            p["alive"] = False
                    updated_participants.append(p)

                if combat_changed:
                    updated_hostile = dict(hostile)
                    updated_hostile["participants"] = updated_participants
                    updated_hostile["area_id"] = area_id
                    changes.append(
                        StateChange(
                            slice="areas",
                            operation="set",
                            path=f"hostile_tracking.{sub_area_id}",
                            value=updated_hostile,
                        )
                    )

        return handler_success(
            "status_effect",
            "tick_combat_effects",
            changes=changes,
            metadata={
                "combats_processed": combats_processed,
                "participants_ticked": participants_ticked,
                "effects_expired": effects_expired,
                "total_hp_delta": total_hp_delta,
            },
            omit_empty_delta=False,
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
            "source": coerce_non_empty_string(cmd.params.get("source")) or cmd.source,
            "remaining_ticks": duration_ticks,
            "duration_ticks": duration_ticks,
            "remaining_duration": duration_ticks,
            "modifiers": modifiers,
            "periodic": periodic,
            "tags": normalize_tags(cmd.params.get("tags")),
        }

    @staticmethod
    def _copy_effects(state: StateContainer) -> list[dict[str, Any]]:
        return [dict(effect) for effect in state.player.get_active_effects()]

    @staticmethod
    def _normalize_mapping(raw_value: Any) -> dict[str, Any]:
        if not isinstance(raw_value, Mapping):
            return {}
        return {str(key): value for key, value in raw_value.items()}

    @staticmethod
    def _coerce_effect_type_for_removal(params: Mapping[str, Any]) -> str | None:
        return coerce_non_empty_string(
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
