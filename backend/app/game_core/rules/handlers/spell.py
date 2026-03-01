"""SpellHandler implementation."""

from __future__ import annotations

import random
import re
from typing import Any, Mapping

from app.game_core.content import WorldInstance
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.handler_utils import get_non_empty_string, handler_success
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer

from app.game_core.rules.handlers.spell_concentration import (
    compute_break_concentration,
    hostile_changes as build_hostile_changes,
    normalize_effect_ids as coerce_effect_ids,
    validate_break_concentration,
)
from app.game_core.rules.handlers.spell_effects import (
    apply_combat_target,
    apply_self_target,
    unsupported_cast_spell as build_unsupported_cast_spell,
)
from app.game_core.rules.handlers.spell_preparation import (
    compute_prepare_spells,
    prepare_mode,
    validate_prepare_spells,
)
from app.game_core.rules.handlers.spell_resolver import (
    coerce_int,
    get_class_template,
    normalize_mapping,
    read_bool,
    read_int,
    read_list,
    read_mapping,
    read_non_empty_string,
    read_positive_int,
    resolve_action_type,
    resolve_cast_targets,
    resolve_effect_payload,
    resolve_effect_type,
    resolve_resource_cost,
    resolve_spell_level,
    resolve_spell_template,
    resolve_spellcasting_ability,
    source_get,
    source_has,
    validate_character_identity,
)


class SpellHandler(StaticCommandHandler):
    COMMAND_TYPES = ("cast_spell", "prepare_spells", "break_concentration")

    _SUPPORTED_SELF_TARGETS = frozenset({"self", "player"})
    _SUPPORTED_SELF_EFFECTS = frozenset({"heal", "buff", "control", "utility"})
    _SUPPORTED_COMBAT_EFFECTS = frozenset({"damage", "control"})

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")

        if cmd.type == "break_concentration":
            return validate_break_concentration(cmd, state)
        if cmd.type == "prepare_spells":
            return validate_prepare_spells(cmd, state, world)
        if cmd.type == "cast_spell":
            return self._validate_cast_spell(cmd, state, world)
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

        if cmd.type == "break_concentration":
            return compute_break_concentration(state)
        if cmd.type == "prepare_spells":
            return compute_prepare_spells(cmd, state, world)
        if cmd.type == "cast_spell":
            return self._compute_cast_spell(cmd, state, world)
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _validate_cast_spell(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not world.has_registry("skills"):
            return ValidationResult(ok=False, reason="skills registry is required")
        identity_check = self._validate_character_identity(cmd.params, state, key="caster")
        if identity_check is not None:
            return identity_check

        spell_id = get_non_empty_string(cmd.params, "spell_id")
        if spell_id is None:
            return ValidationResult(ok=False, reason="spell_id must be a non-empty string")
        template = resolve_spell_template(spell_id, world)
        if template is None:
            return ValidationResult(ok=False, reason=f"invalid spell template: {spell_id}")
        if spell_id not in state.player.known_spells:
            return ValidationResult(ok=False, reason=f"unknown known spell: {spell_id}")

        spell_level = resolve_spell_level(template)
        uses_prepared = prepare_mode(state, world) == "prepared"
        if uses_prepared and spell_level > 0 and spell_id not in state.player.prepared_spells:
            return ValidationResult(ok=False, reason=f"spell not prepared: {spell_id}")

        resolved_slot_level = spell_level
        if spell_level > 0:
            slot_level = self._coerce_int(cmd.params.get("slot_level"))
            if slot_level is not None:
                resolved_slot_level = slot_level
            if resolved_slot_level < spell_level:
                return ValidationResult(
                    ok=False,
                    reason=f"slot_level must be >= spell level: {spell_level}",
                )
            if not state.player.has_spell_slot(resolved_slot_level):
                return ValidationResult(
                    ok=False,
                    reason=f"no spell slot available at level {resolved_slot_level}",
                )

        resource_key, resource_amount = resolve_resource_cost(template)
        if resource_key is not None and not state.player.has_resource(resource_key, resource_amount):
            return ValidationResult(ok=False, reason=f"insufficient resource: {resource_key}")
        return ValidationResult(ok=True)

    def _compute_cast_spell(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        spell_id = str(cmd.params["spell_id"]).strip()
        template = resolve_spell_template(spell_id, world)
        assert template is not None
        spell_level = resolve_spell_level(template)
        resolved_slot_level = spell_level
        slot_level = self._coerce_int(cmd.params.get("slot_level"))
        if slot_level is not None:
            resolved_slot_level = slot_level

        target_spec = resolve_cast_targets(cmd.params.get("targets"))
        if target_spec is None:
            return self._unsupported_cast_spell(
                status="unsupported_target",
                spell_id=spell_id,
                slot_level=resolved_slot_level,
            )
        target_mode = str(target_spec["mode"])
        resolved_targets = [str(item) for item in target_spec["targets"]]

        effect = resolve_effect_payload(template)
        effect_type = resolve_effect_type(template, effect)
        supported = (
            self._SUPPORTED_SELF_EFFECTS if target_mode == "self" else self._SUPPORTED_COMBAT_EFFECTS
        )
        if effect_type not in supported:
            return self._unsupported_cast_spell(
                status="unsupported_effect",
                spell_id=spell_id,
                slot_level=resolved_slot_level,
                target_mode=target_mode,
                targets=resolved_targets,
            )

        resource_key, resource_amount = resolve_resource_cost(template)
        action_type = resolve_action_type(template)
        time_cost = self._time_cost_for_action(action_type)
        spellcasting_mod = state.player.get_modifier(resolve_spellcasting_ability(state, world))

        ctx: dict[str, Any] = {
            "rolls": [],
            "changes": [],
            "pending_hostile_payloads": {},
            "hp_delta": 0,
            "applied_effect_ids": [],
            "broke_previous_concentration": False,
            "new_concentration_payload": None,
            "player_effects_update": None,
            "target_hp": None,
            "target_alive": None,
            "target_defeated": False,
            "damage_total": 0,
            "target_effect_count": 0,
            "combat_active": False,
            "combat_cleared": False,
        }

        if target_mode == "self":
            early = apply_self_target(
                state,
                template,
                effect,
                effect_type,
                spell_id,
                spell_level,
                resolved_slot_level,
                spellcasting_mod,
                resolved_targets,
                ctx,
                roll_dice=self._roll_dice_expression,
            )
        else:
            early = apply_combat_target(
                state,
                template,
                effect,
                effect_type,
                spell_id,
                spell_level,
                resolved_slot_level,
                resolved_targets,
                ctx,
                roll_dice=self._roll_dice_expression,
            )
        if early is not None:
            return early

        return self._build_cast_result(
            state,
            ctx,
            spell_id=spell_id,
            spell_level=spell_level,
            resolved_slot_level=resolved_slot_level,
            resource_key=resource_key,
            resource_amount=resource_amount,
            time_cost=time_cost,
            target_mode=target_mode,
            effect_type=effect_type,
            resolved_targets=resolved_targets,
        )

    def _build_cast_result(
        self,
        state: StateContainer,
        ctx: dict[str, Any],
        *,
        spell_id: str,
        spell_level: int,
        resolved_slot_level: int,
        resource_key: str | None,
        resource_amount: int,
        time_cost: float,
        target_mode: str,
        effect_type: str,
        resolved_targets: list[str],
    ) -> ExecuteResult:
        changes: list[StateChange] = list(ctx["changes"])

        if spell_level > 0:
            slot_state = state.player.get_spell_slots(resolved_slot_level) or {"current": 0, "max": 0}
            updated_slot_state = {
                "current": max(0, int(slot_state.get("current", 0)) - 1),
                "max": int(slot_state.get("max", 0)),
            }
            changes.append(
                StateChange(
                    "player",
                    "modify",
                    f"spell_slots.{resolved_slot_level}",
                    updated_slot_state,
                )
            )

        if resource_key is not None:
            resource = state.player.get_resource(resource_key) or {
                "current": 0,
                "max": 0,
                "recovery": "long_rest",
            }
            updated_resource = {
                **resource,
                "current": max(0, int(resource.get("current", 0)) - resource_amount),
            }
            changes.append(
                StateChange(
                    "player",
                    "modify",
                    f"class_resources.{resource_key}",
                    updated_resource,
                )
            )

        hp_delta = ctx["hp_delta"]
        if hp_delta != 0:
            changes.append(StateChange("player", "add", "hp", hp_delta))
        if ctx["player_effects_update"] is not None:
            changes.append(StateChange("player", "set", "active_effects", ctx["player_effects_update"]))
        if ctx["new_concentration_payload"] is not None:
            changes.append(StateChange("player", "set", "concentration", ctx["new_concentration_payload"]))
        changes.extend(self._hostile_changes(ctx["pending_hostile_payloads"]))

        metadata: dict[str, Any] = {
            "status": "cast",
            "spell_id": spell_id,
            "slot_level": resolved_slot_level,
            "consumed_slot": spell_level > 0,
            "resource_key": resource_key,
            "resource_amount": resource_amount if resource_key is not None else 0,
            "hp_delta": hp_delta,
            "applied_effect_ids": ctx["applied_effect_ids"],
            "broke_previous_concentration": ctx["broke_previous_concentration"],
            "concentration": ctx["new_concentration_payload"] is not None,
            "target_mode": target_mode,
            "targets": list(resolved_targets),
        }
        if target_mode == "combat" and effect_type == "damage":
            metadata.update(
                {
                    "damage_total": ctx["damage_total"],
                    "target_hp": ctx["target_hp"],
                    "target_alive": ctx["target_alive"],
                    "target_defeated": ctx["target_defeated"],
                    "combat_active": ctx["combat_active"],
                    "combat_cleared": ctx["combat_cleared"],
                }
            )
        if target_mode == "combat" and effect_type == "control":
            metadata.update(
                {
                    "target_effect_applied": True,
                    "target_effect_count": ctx["target_effect_count"],
                    "combat_active": ctx["combat_active"],
                    "combat_cleared": ctx["combat_cleared"],
                }
            )
        return handler_success(
            "spell",
            "cast_spell",
            changes=changes,
            time_cost=time_cost,
            rolls=ctx["rolls"],
            metadata=metadata,
            omit_empty_delta=False,
        )

    def _hostile_changes(self, payloads: Mapping[str, Mapping[str, Any]]) -> list[StateChange]:
        return build_hostile_changes(payloads)

    def _unsupported_cast_spell(
        self,
        *,
        status: str,
        spell_id: str,
        slot_level: int,
        target_mode: str | None = None,
        targets: list[str] | None = None,
    ) -> ExecuteResult:
        return build_unsupported_cast_spell(
            status=status,
            spell_id=spell_id,
            slot_level=slot_level,
            target_mode=target_mode,
            targets=targets,
        )

    def _time_cost_for_action(self, action_type: str) -> float:
        normalized = action_type.strip().lower()
        if normalized in {"bonus_action", "reaction"}:
            return 0.0
        return 1.0 / 6.0

    @staticmethod
    def _current_tick(state: StateContainer) -> int | None:
        if not state.has_slice("time"):
            return None
        return state.time.absolute_tick()

    def _get_class_template(
        self,
        state: StateContainer,
        world: WorldInstance,
    ) -> Any:
        return get_class_template(state, world)

    @staticmethod
    def _source_get(source: Any, key: str) -> Any:
        return source_get(source, key)

    @staticmethod
    def _source_has(source: Any, key: str) -> bool:
        return source_has(source, key)

    @classmethod
    def _read_mapping(cls, primary: Any, fallback: Any, key: str) -> dict[str, Any]:
        del cls
        return read_mapping(primary, fallback, key)

    @classmethod
    def _read_list(cls, primary: Any, fallback: Any, key: str) -> list[str]:
        del cls
        return read_list(primary, fallback, key)

    @classmethod
    def _read_non_empty_string(
        cls,
        primary: Any,
        fallback: Any,
        *keys: str,
    ) -> str | None:
        del cls
        return read_non_empty_string(primary, fallback, *keys)

    @classmethod
    def _read_int(
        cls,
        primary: Any,
        fallback: Any,
        *keys: str,
    ) -> int | None:
        del cls
        return read_int(primary, fallback, *keys)

    @classmethod
    def _read_positive_int(
        cls,
        primary: Any,
        fallback: Any,
        *keys: str,
    ) -> int | None:
        del cls
        return read_positive_int(primary, fallback, *keys)

    @classmethod
    def _read_bool(
        cls,
        primary: Any,
        fallback: Any,
        key: str,
    ) -> bool:
        del cls
        return read_bool(primary, fallback, key)

    def _roll_dice_expression(self, expression: str) -> int:
        match = re.fullmatch(r"\s*(\d+)\s*[dD]\s*(\d+)\s*", expression)
        if match is None:
            raise ValueError(f"unsupported dice expression: {expression}")
        count = int(match.group(1))
        sides = int(match.group(2))
        if count < 1 or sides < 1:
            raise ValueError(f"unsupported dice expression: {expression}")
        return sum(random.randint(1, sides) for _ in range(count))

    @staticmethod
    def _validate_character_identity(
        params: Mapping[str, Any],
        state: StateContainer,
        *,
        key: str = "character",
    ) -> ValidationResult | None:
        return validate_character_identity(params, state, key=key)

    @staticmethod
    def _normalize_mapping(raw_value: Any) -> dict[str, Any]:
        return normalize_mapping(raw_value)

    @staticmethod
    def _normalize_effect_ids(raw_effect_ids: Any) -> set[str]:
        return coerce_effect_ids(raw_effect_ids)

    @staticmethod
    def _coerce_int(raw_value: Any) -> int | None:
        return coerce_int(raw_value)
