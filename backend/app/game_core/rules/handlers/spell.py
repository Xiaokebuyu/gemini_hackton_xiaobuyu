"""SpellHandler implementation."""

from __future__ import annotations

import random
import re
from typing import Any, Mapping
from uuid import uuid4

from app.game_core.content import WorldInstance
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.models import Command, DiceRoll, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer, StateDelta


class SpellHandler(StaticCommandHandler):
    COMMAND_TYPES = ("cast_spell", "prepare_spells", "break_concentration")

    _SUPPORTED_SELF_TARGETS = frozenset({"self", "player"})
    _SUPPORTED_SELF_EFFECTS = frozenset({"heal", "buff", "control", "utility"})
    _SUPPORTED_COMBAT_EFFECTS = frozenset({"damage", "control"})
    _COMBAT_TARGET_KIND = "combat_participant"

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")

        if cmd.type == "break_concentration":
            return self._validate_break_concentration(cmd, state)
        if cmd.type == "prepare_spells":
            return self._validate_prepare_spells(cmd, state, world)
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
            return self._compute_break_concentration(state)
        if cmd.type == "prepare_spells":
            return self._compute_prepare_spells(cmd, state, world)
        if cmd.type == "cast_spell":
            return self._compute_cast_spell(cmd, state, world)
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _validate_break_concentration(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        identity_check = self._validate_character_identity(cmd.params, state)
        if identity_check is not None:
            return identity_check
        return ValidationResult(ok=True)

    def _validate_prepare_spells(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not world.has_registry("skills"):
            return ValidationResult(ok=False, reason="skills registry is required")
        identity_check = self._validate_character_identity(cmd.params, state)
        if identity_check is not None:
            return identity_check

        raw_spell_ids = cmd.params.get("spell_ids")
        if not isinstance(raw_spell_ids, list):
            return ValidationResult(ok=False, reason="spell_ids must be a list")
        normalized_spell_ids: list[str] = []
        for raw_spell_id in raw_spell_ids:
            spell_id = self._coerce_non_empty_string(raw_spell_id)
            if spell_id is None:
                return ValidationResult(
                    ok=False,
                    reason="spell_ids must only contain non-empty strings",
                )
            if spell_id not in state.player.known_spells:
                return ValidationResult(ok=False, reason=f"unknown known spell: {spell_id}")
            if self._resolve_spell_template(world, spell_id) is None:
                return ValidationResult(ok=False, reason=f"invalid spell template: {spell_id}")
            normalized_spell_ids.append(spell_id)

        mode = self._prepare_mode(state, world)
        if mode == "not_applicable":
            return ValidationResult(ok=True)

        highest_slot_level = self._highest_spell_slot_level(state)
        for spell_id in normalized_spell_ids:
            template = self._resolve_spell_template(world, spell_id)
            if template is None:
                continue
            spell_level = self._resolve_spell_level(template)
            if spell_level > highest_slot_level:
                return ValidationResult(
                    ok=False,
                    reason=f"spell exceeds available slot level: {spell_id}",
                )

        max_prepared = self._resolve_prepared_limit(state, world, mode)
        if max_prepared is None:
            return ValidationResult(ok=False, reason="invalid prepared_formula")
        if len(normalized_spell_ids) > max_prepared:
            return ValidationResult(
                ok=False,
                reason=f"prepared spell count exceeds max: {len(normalized_spell_ids)} > {max_prepared}",
            )
        return ValidationResult(ok=True)

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

        spell_id = self._get_non_empty_string(cmd.params, "spell_id")
        if spell_id is None:
            return ValidationResult(ok=False, reason="spell_id must be a non-empty string")
        template = self._resolve_spell_template(world, spell_id)
        if template is None:
            return ValidationResult(ok=False, reason=f"invalid spell template: {spell_id}")
        if spell_id not in state.player.known_spells:
            return ValidationResult(ok=False, reason=f"unknown known spell: {spell_id}")

        spell_level = self._resolve_spell_level(template)
        uses_prepared = self._prepare_mode(state, world) == "prepared"
        if uses_prepared and spell_level > 0 and spell_id not in state.player.prepared_spells:
            return ValidationResult(
                ok=False,
                reason=f"spell not prepared: {spell_id}",
            )

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

        resource_key, resource_amount = self._resolve_resource_cost(template)
        if resource_key is not None and not state.player.has_resource(resource_key, resource_amount):
            return ValidationResult(
                ok=False,
                reason=f"insufficient resource: {resource_key}",
            )
        return ValidationResult(ok=True)

    def _compute_break_concentration(
        self,
        state: StateContainer,
    ) -> ExecuteResult:
        concentration = self._normalize_mapping(state.player.concentration)
        if not concentration:
            return self._success_no_delta(
                "break_concentration",
                metadata={
                    "status": "noop",
                    "spell_id": None,
                    "removed_effect_count": 0,
                },
            )

        updated_effects, player_changed, target_payloads, removed_count, spell_id = self._clear_concentration_effects(
            state,
            concentration,
        )
        changes = [
            StateChange("player", "set", "concentration", None),
        ]
        if player_changed:
            changes.append(StateChange("player", "set", "active_effects", updated_effects))
        changes.extend(self._hostile_changes(target_payloads))

        return self._success(
            "break_concentration",
            changes=changes,
            metadata={
                "status": "broken",
                "spell_id": spell_id,
                "removed_effect_count": removed_count,
            },
        )

    def _compute_prepare_spells(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        spell_ids = [str(spell_id).strip() for spell_id in cmd.params.get("spell_ids", [])]
        mode = self._prepare_mode(state, world)
        if mode == "not_applicable":
            return self._success_no_delta(
                "prepare_spells",
                metadata={
                    "status": "not_applicable",
                    "prepared_count": len(spell_ids),
                    "max_prepared": len(state.player.known_spells),
                    "used_fallback_limit": False,
                },
            )

        max_prepared = self._resolve_prepared_limit(state, world, mode)
        if max_prepared is None:
            return ExecuteResult.error("invalid prepared_formula")

        return self._success(
            "prepare_spells",
            changes=[
                StateChange("player", "set", "prepared_spells", spell_ids),
            ],
            metadata={
                "status": "prepared_fallback" if mode == "fallback" else "prepared",
                "prepared_count": len(spell_ids),
                "max_prepared": max_prepared,
                "used_fallback_limit": mode == "fallback",
            },
        )

    def _compute_cast_spell(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        spell_id = str(cmd.params["spell_id"]).strip()
        template = self._resolve_spell_template(world, spell_id)
        assert template is not None
        spell_level = self._resolve_spell_level(template)
        resolved_slot_level = spell_level
        slot_level = self._coerce_int(cmd.params.get("slot_level"))
        if slot_level is not None:
            resolved_slot_level = slot_level

        target_spec = self._resolve_cast_targets(cmd.params.get("targets"))
        if target_spec is None:
            return self._unsupported_cast_spell(
                status="unsupported_target",
                spell_id=spell_id,
                slot_level=resolved_slot_level,
            )
        target_mode = str(target_spec["mode"])
        resolved_targets = [str(item) for item in target_spec["targets"]]

        effect = self._resolve_effect_payload(template)
        effect_type = self._resolve_effect_type(template, effect)
        if target_mode == "self":
            if effect_type not in self._SUPPORTED_SELF_EFFECTS:
                return self._unsupported_cast_spell(
                    status="unsupported_effect",
                    spell_id=spell_id,
                    slot_level=resolved_slot_level,
                    target_mode=target_mode,
                    targets=resolved_targets,
                )
        else:
            if effect_type not in self._SUPPORTED_COMBAT_EFFECTS:
                return self._unsupported_cast_spell(
                    status="unsupported_effect",
                    spell_id=spell_id,
                    slot_level=resolved_slot_level,
                    target_mode=target_mode,
                    targets=resolved_targets,
                )

        resource_key, resource_amount = self._resolve_resource_cost(template)
        action_type = self._resolve_action_type(template)
        time_cost = self._time_cost_for_action(action_type)
        spellcasting_mod = state.player.get_modifier(
            self._resolve_spellcasting_ability(state, world)
        )

        rolls: list[DiceRoll] = []
        changes: list[StateChange] = []
        pending_hostile_payloads: dict[str, dict[str, Any]] = {}
        hp_delta = 0
        applied_effect_ids: list[str] = []
        broke_previous_concentration = False
        new_concentration_payload: dict[str, Any] | None = None
        player_effects_update: list[dict[str, Any]] | None = None

        target_hp: int | None = None
        target_alive: bool | None = None
        target_defeated = False
        damage_total = 0
        target_effect_count = 0
        combat_active = False
        combat_cleared = False

        if target_mode == "self":
            if effect_type == "heal":
                heal_total, heal_rolls = self._resolve_heal_amount(
                    template,
                    effect,
                    spellcasting_mod,
                    spell_level,
                    resolved_slot_level,
                )
                if heal_total is None:
                    return self._unsupported_cast_spell(
                        status="unsupported_effect",
                        spell_id=spell_id,
                        slot_level=resolved_slot_level,
                        target_mode=target_mode,
                        targets=resolved_targets,
                    )
                rolls.extend(heal_rolls)
                target_hp = min(int(state.player.max_hp), int(state.player.hp) + heal_total)
                hp_delta = target_hp - int(state.player.hp)
            else:
                effect_instance, concentration_requested = self._build_spell_effect_instance(
                    spell_id,
                    effect_type,
                    template,
                    effect,
                )
                if effect_instance is None:
                    return self._unsupported_cast_spell(
                        status="unsupported_effect",
                        spell_id=spell_id,
                        slot_level=resolved_slot_level,
                        target_mode=target_mode,
                        targets=resolved_targets,
                    )

                updated_effects = state.player.get_active_effects()
                if concentration_requested:
                    (
                        updated_effects,
                        _player_changed,
                        cleared_target_payloads,
                        removed_count,
                        _,
                    ) = self._clear_concentration_effects(
                        state,
                        self._normalize_mapping(state.player.concentration),
                    )
                    pending_hostile_payloads.update(cleared_target_payloads)
                    broke_previous_concentration = (
                        removed_count > 0 or state.player.concentration is not None
                    )
                else:
                    updated_effects = [dict(item) for item in updated_effects]

                updated_effects.append(effect_instance)
                player_effects_update = updated_effects
                applied_effect_ids.append(str(effect_instance["instance_id"]))

                if concentration_requested:
                    new_concentration_payload = {
                        "spell_id": spell_id,
                        "slot_level": resolved_slot_level,
                        "remaining_duration": int(effect_instance.get("remaining_ticks", -1)),
                        "applied_effects": [str(effect_instance["instance_id"])],
                        "targets": list(resolved_targets),
                        "target_refs": [],
                    }
        else:
            combat_target = self._resolve_combat_target(state, resolved_targets[0])
            if combat_target is None:
                return self._unsupported_cast_spell(
                    status="unsupported_target",
                    spell_id=spell_id,
                    slot_level=resolved_slot_level,
                    target_mode=target_mode,
                    targets=resolved_targets,
                )

            sub_area_id, hostile_payload, target_monster_id, _target_name = combat_target
            if effect_type == "damage":
                damage_total, damage_rolls = self._resolve_damage_amount(
                    template,
                    effect,
                    spell_level,
                    resolved_slot_level,
                )
                if damage_total is None:
                    return self._unsupported_cast_spell(
                        status="unsupported_effect",
                        spell_id=spell_id,
                        slot_level=resolved_slot_level,
                        target_mode=target_mode,
                        targets=resolved_targets,
                    )
                rolls.extend(damage_rolls)

                participants = state.areas.participant_snapshots(hostile_payload)
                target_resolution = state.areas.resolve_participant(
                    target_monster_id,
                    participants,
                    by_monster_id_only=True,
                )
                if target_resolution is None:
                    return self._unsupported_cast_spell(
                        status="unsupported_target",
                        spell_id=spell_id,
                        slot_level=resolved_slot_level,
                        target_mode=target_mode,
                        targets=resolved_targets,
                    )
                target_index, participant = target_resolution
                updated_target = dict(participant)
                remaining_hp = max(0, state.areas.participant_hp(participant) - damage_total)
                updated_target["hp"] = remaining_hp
                updated_target["alive"] = remaining_hp > 0
                participants[target_index] = updated_target
                updated_payload, combat_active, combat_cleared = state.areas.build_combat_hostile(
                    hostile_payload,
                    participants,
                    blocking=bool(hostile_payload.get("blocking", False)),
                    current_tick=self._current_tick(state),
                )
                pending_hostile_payloads[sub_area_id] = updated_payload
                target_hp = remaining_hp
                target_alive = remaining_hp > 0
                target_defeated = not target_alive
            else:
                effect_instance, concentration_requested = self._build_spell_effect_instance(
                    spell_id,
                    effect_type,
                    template,
                    effect,
                )
                if effect_instance is None:
                    return self._unsupported_cast_spell(
                        status="unsupported_effect",
                        spell_id=spell_id,
                        slot_level=resolved_slot_level,
                        target_mode=target_mode,
                        targets=resolved_targets,
                    )

                if concentration_requested:
                    (
                        updated_effects,
                        player_changed,
                        cleared_target_payloads,
                        removed_count,
                        _,
                    ) = self._clear_concentration_effects(
                        state,
                        self._normalize_mapping(state.player.concentration),
                    )
                    pending_hostile_payloads.update(cleared_target_payloads)
                    if player_changed:
                        player_effects_update = updated_effects
                    broke_previous_concentration = (
                        removed_count > 0 or state.player.concentration is not None
                    )

                payload_for_target = pending_hostile_payloads.get(sub_area_id, hostile_payload)
                participants = state.areas.participant_snapshots(payload_for_target)
                target_resolution = state.areas.resolve_participant(
                    target_monster_id,
                    participants,
                    by_monster_id_only=True,
                )
                if target_resolution is None:
                    return self._unsupported_cast_spell(
                        status="unsupported_target",
                        spell_id=spell_id,
                        slot_level=resolved_slot_level,
                        target_mode=target_mode,
                        targets=resolved_targets,
                    )
                target_index, participant = target_resolution
                if not bool(participant.get("alive", False)):
                    return self._unsupported_cast_spell(
                        status="unsupported_target",
                        spell_id=spell_id,
                        slot_level=resolved_slot_level,
                        target_mode=target_mode,
                        targets=resolved_targets,
                    )
                updated_target = dict(participant)
                target_effects = state.areas.participant_effects(updated_target)
                target_effects.append(effect_instance)
                updated_target["active_effects"] = target_effects
                participants[target_index] = updated_target
                updated_payload = state.areas.update_hostile_participants(
                    payload_for_target,
                    participants,
                )
                pending_hostile_payloads[sub_area_id] = updated_payload
                applied_effect_ids.append(str(effect_instance["instance_id"]))
                target_effect_count = len(target_effects)
                combat_active = bool(updated_payload.get("combat_active", False))
                combat_cleared = bool(updated_payload.get("cleared", False))

                if concentration_requested:
                    new_concentration_payload = {
                        "spell_id": spell_id,
                        "slot_level": resolved_slot_level,
                        "remaining_duration": int(effect_instance.get("remaining_ticks", -1)),
                        "applied_effects": [],
                        "targets": list(resolved_targets),
                        "target_refs": [
                            {
                                "kind": self._COMBAT_TARGET_KIND,
                                "sub_area_id": sub_area_id,
                                "participant_monster_id": target_monster_id,
                                "effect_ids": [str(effect_instance["instance_id"])],
                            }
                        ],
                    }

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

        if hp_delta != 0:
            changes.append(StateChange("player", "add", "hp", hp_delta))
        if player_effects_update is not None:
            changes.append(StateChange("player", "set", "active_effects", player_effects_update))
        if new_concentration_payload is not None:
            changes.append(
                StateChange(
                    "player",
                    "set",
                    "concentration",
                    new_concentration_payload,
                )
            )
        changes.extend(self._hostile_changes(pending_hostile_payloads))

        metadata = {
            "status": "cast",
            "spell_id": spell_id,
            "slot_level": resolved_slot_level,
            "consumed_slot": spell_level > 0,
            "resource_key": resource_key,
            "resource_amount": resource_amount if resource_key is not None else 0,
            "hp_delta": hp_delta,
            "applied_effect_ids": applied_effect_ids,
            "broke_previous_concentration": broke_previous_concentration,
            "concentration": new_concentration_payload is not None,
            "target_mode": target_mode,
            "targets": list(resolved_targets),
        }
        if target_mode == "combat" and effect_type == "damage":
            metadata.update(
                {
                    "damage_total": damage_total,
                    "target_hp": target_hp,
                    "target_alive": target_alive,
                    "target_defeated": target_defeated,
                    "combat_active": combat_active,
                    "combat_cleared": combat_cleared,
                }
            )
        if target_mode == "combat" and effect_type == "control":
            metadata.update(
                {
                    "target_effect_applied": True,
                    "target_effect_count": target_effect_count,
                    "combat_active": combat_active,
                    "combat_cleared": combat_cleared,
                }
            )
        return self._success(
            "cast_spell",
            changes=changes,
            time_cost=time_cost,
            rolls=rolls,
            metadata=metadata,
        )

    def _resolve_spell_template(
        self,
        world: WorldInstance,
        spell_id: str,
    ) -> dict[str, Any] | None:
        if not world.has_registry("skills"):
            return None
        raw_template = world.skills.get(spell_id)
        if not isinstance(raw_template, Mapping):
            return None
        template = dict(raw_template)
        if self._is_spell_template(template):
            return template
        return None

    def _is_spell_template(self, template: Mapping[str, Any]) -> bool:
        for key in ("category", "type", "kind"):
            value = self._coerce_non_empty_string(template.get(key))
            if value == "spell":
                return True
        if "spell_level" in template:
            return self._coerce_int(template.get("spell_level")) is not None
        if "level" in template and "effect" in template:
            return self._coerce_int(template.get("level")) is not None
        return False

    def _resolve_spell_level(self, template: Mapping[str, Any]) -> int:
        for key in ("spell_level", "level"):
            level = self._coerce_int(template.get(key))
            if level is not None and level >= 0:
                return level
        return 0

    def _resolve_effect_payload(self, template: Mapping[str, Any]) -> dict[str, Any]:
        raw_effect = template.get("effect")
        if isinstance(raw_effect, Mapping):
            return dict(raw_effect)
        return {}

    def _resolve_effect_type(
        self,
        template: Mapping[str, Any],
        effect: Mapping[str, Any],
    ) -> str:
        for source, key in ((effect, "type"), (template, "effect_type")):
            value = self._coerce_non_empty_string(source.get(key))
            if value is not None:
                return value
        if self._read_positive_int(effect, template, "heal_amount", "heal") is not None:
            return "heal"
        if self._coerce_non_empty_string(effect.get("applies_status")) is not None:
            return "buff"
        if self._coerce_non_empty_string(template.get("applies_status")) is not None:
            return "buff"
        return ""

    def _resolve_spellcasting_ability(
        self,
        state: StateContainer,
        world: WorldInstance,
    ) -> str:
        class_template = self._get_class_template(state, world)
        ability = self._coerce_non_empty_string(class_template.get("spellcasting_ability"))
        if ability is not None:
            ability = ability.lower()
        if ability is None or ability not in state.player.stats:
            return "int"
        return ability

    def _resolve_action_type(self, template: Mapping[str, Any]) -> str:
        raw_cost = template.get("cost")
        if isinstance(raw_cost, Mapping):
            action_type = self._coerce_non_empty_string(raw_cost.get("action_type"))
            if action_type is not None:
                return action_type
        return self._coerce_non_empty_string(template.get("action_type")) or "action"

    def _resolve_resource_cost(
        self,
        template: Mapping[str, Any],
    ) -> tuple[str | None, int]:
        raw_cost = template.get("cost")
        if not isinstance(raw_cost, Mapping):
            return (None, 0)
        resource_key = self._coerce_non_empty_string(raw_cost.get("resource"))
        if resource_key is None:
            return (None, 0)
        amount = self._coerce_int(
            raw_cost.get("resource_amount", raw_cost.get("amount", 1))
        )
        if amount is None or amount < 1:
            amount = 1
        return (resource_key, amount)

    def _resolve_cast_targets(self, raw_targets: Any) -> dict[str, Any] | None:
        if raw_targets is None:
            return {"mode": "self", "targets": ["player"]}
        if isinstance(raw_targets, str):
            target = self._coerce_non_empty_string(raw_targets)
            if target is None:
                return None
            if target.lower() in self._SUPPORTED_SELF_TARGETS:
                return {"mode": "self", "targets": ["player"]}
            return {"mode": "combat", "targets": [target]}
        if isinstance(raw_targets, list) and len(raw_targets) == 1:
            target = self._coerce_non_empty_string(raw_targets[0])
            if target is None:
                return None
            if target.lower() in self._SUPPORTED_SELF_TARGETS:
                return {"mode": "self", "targets": ["player"]}
            return {"mode": "combat", "targets": [target]}
        return None

    def _prepare_mode(
        self,
        state: StateContainer,
        world: WorldInstance,
    ) -> str:
        if not world.has_registry("classes"):
            return "fallback"
        class_template = self._get_class_template(state, world)
        if not class_template:
            return "fallback"
        has_formula = self._coerce_non_empty_string(class_template.get("prepared_formula")) is not None
        has_limit = self._coerce_int(class_template.get("prepared_limit")) is not None
        if has_formula or has_limit:
            return "prepared"
        return "not_applicable"

    def _resolve_prepared_limit(
        self,
        state: StateContainer,
        world: WorldInstance,
        mode: str,
    ) -> int | None:
        if mode == "fallback":
            return len(state.player.known_spells)
        class_template = self._get_class_template(state, world)
        explicit_limit = self._coerce_int(class_template.get("prepared_limit"))
        if explicit_limit is not None:
            return max(0, explicit_limit)
        formula = self._coerce_non_empty_string(class_template.get("prepared_formula"))
        if formula is None:
            return len(state.player.known_spells)
        return self._evaluate_prepared_formula(formula, state)

    def _evaluate_prepared_formula(
        self,
        formula: str,
        state: StateContainer,
    ) -> int | None:
        tokens = [token.strip().lower() for token in formula.split("+")]
        if not tokens:
            return None
        total = 0
        for token in tokens:
            if not token:
                return None
            value = self._formula_token_value(token, state)
            if value is None:
                return None
            total += value
        return max(0, total)

    def _formula_token_value(
        self,
        token: str,
        state: StateContainer,
    ) -> int | None:
        if token.isdigit():
            return int(token)
        if token == "level":
            return int(state.player.level)
        if token == "proficiency_bonus":
            return int(state.player.proficiency_bonus)
        if token.endswith("_mod") and token[:-4] in state.player.stats:
            return state.player.get_modifier(token[:-4])
        return None

    def _highest_spell_slot_level(self, state: StateContainer) -> int:
        highest = 0
        for level, slot_state in state.player.spell_slots.items():
            if int(slot_state.get("max", 0)) > 0:
                highest = max(highest, int(level))
        return highest

    def _resolve_heal_amount(
        self,
        template: Mapping[str, Any],
        effect: Mapping[str, Any],
        spellcasting_mod: int,
        spell_level: int,
        slot_level: int,
    ) -> tuple[int | None, list[DiceRoll]]:
        rolls: list[DiceRoll] = []

        fixed_value = self._read_positive_int(effect, template, "heal_amount", "heal")
        if fixed_value is not None:
            total = fixed_value + spellcasting_mod
            return (max(0, total), rolls)

        dice_expr = self._read_non_empty_string(effect, template, "dice")
        if dice_expr is None:
            return (None, [])

        rolled = self._roll_dice_expression(dice_expr)
        rolls.append(
            DiceRoll(
                purpose="spell_heal",
                dice=dice_expr,
                result=rolled,
                total=rolled,
            )
        )
        total = rolled + spellcasting_mod

        upcast_dice = self._read_non_empty_string(effect, template, "upcast_dice")
        extra_levels = max(0, slot_level - spell_level)
        if upcast_dice is not None and extra_levels > 0:
            for _ in range(extra_levels):
                extra = self._roll_dice_expression(upcast_dice)
                rolls.append(
                    DiceRoll(
                        purpose="spell_upcast_heal",
                        dice=upcast_dice,
                        result=extra,
                        total=extra,
                    )
                )
                total += extra
        return (max(0, total), rolls)

    def _resolve_damage_amount(
        self,
        template: Mapping[str, Any],
        effect: Mapping[str, Any],
        spell_level: int,
        slot_level: int,
    ) -> tuple[int | None, list[DiceRoll]]:
        rolls: list[DiceRoll] = []

        fixed_value = self._read_positive_int(effect, template, "damage_amount", "damage")
        if fixed_value is not None:
            total = fixed_value
        else:
            dice_expr = self._read_non_empty_string(effect, template, "dice")
            if dice_expr is None:
                return (None, [])
            rolled = self._roll_dice_expression(dice_expr)
            rolls.append(
                DiceRoll(
                    purpose="spell_damage",
                    dice=dice_expr,
                    result=rolled,
                    total=rolled,
                )
            )
            total = rolled

        upcast_dice = self._read_non_empty_string(effect, template, "upcast_dice")
        extra_levels = max(0, slot_level - spell_level)
        if upcast_dice is not None and extra_levels > 0:
            for _ in range(extra_levels):
                extra = self._roll_dice_expression(upcast_dice)
                rolls.append(
                    DiceRoll(
                        purpose="spell_upcast_damage",
                        dice=upcast_dice,
                        result=extra,
                        total=extra,
                    )
                )
                total += extra
        return (max(0, total), rolls)

    def _build_spell_effect_instance(
        self,
        spell_id: str,
        effect_type: str,
        template: Mapping[str, Any],
        effect: Mapping[str, Any],
    ) -> tuple[dict[str, Any] | None, bool]:
        applies_status = self._read_non_empty_string(effect, template, "applies_status")
        modifiers = self._read_mapping(effect, template, "modifiers")
        periodic = self._read_mapping(effect, template, "periodic")
        tags = self._read_list(effect, template, "tags")
        duration = self._read_int(effect, template, "status_duration", "duration_ticks", "duration")
        concentration = self._read_bool(effect, template, "concentration")

        if (
            applies_status is None
            and not modifiers
            and not periodic
            and not tags
            and not concentration
        ):
            return (None, False)

        remaining = duration if duration is not None else -1
        effect_instance = {
            "effect_id": applies_status or spell_id,
            "effect_type": effect_type or "spell_effect",
            "instance_id": uuid4().hex,
            "source": "spell",
            "source_spell_id": spell_id,
            "remaining_ticks": remaining,
            "duration_ticks": remaining,
            "remaining_duration": remaining,
            "modifiers": modifiers,
            "periodic": periodic,
            "tags": tags,
            "from_concentration": concentration,
        }
        return (effect_instance, concentration)

    def _break_existing_concentration(
        self,
        current_effects: list[dict[str, Any]],
        concentration: Mapping[str, Any],
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        spell_id = self._coerce_non_empty_string(concentration.get("spell_id"))
        raw_applied = concentration.get("applied_effects")
        applied_effects: set[str] = set()
        if isinstance(raw_applied, list):
            applied_effects = {
                str(item)
                for item in raw_applied
                if str(item).strip()
            }

        updated_effects: list[dict[str, Any]] = []
        removed_count = 0
        for effect in current_effects:
            instance_id = self._coerce_non_empty_string(effect.get("instance_id"))
            if instance_id is not None and instance_id in applied_effects:
                removed_count += 1
                continue
            if not applied_effects and bool(effect.get("from_concentration")):
                if spell_id and self._coerce_non_empty_string(effect.get("source_spell_id")) == spell_id:
                    removed_count += 1
                    continue
            updated_effects.append(dict(effect))
        return (updated_effects, removed_count, spell_id)

    def _clear_concentration_effects(
        self,
        state: StateContainer,
        concentration: Mapping[str, Any],
    ) -> tuple[list[dict[str, Any]], bool, dict[str, dict[str, Any]], int, str | None]:
        updated_effects, removed_player_count, spell_id = self._break_existing_concentration(
            state.player.get_active_effects(),
            concentration,
        )
        target_payloads, removed_target_count = self._clear_concentration_target_refs(
            state,
            concentration,
        )
        return (
            updated_effects,
            removed_player_count > 0,
            target_payloads,
            removed_player_count + removed_target_count,
            spell_id,
        )

    def _clear_concentration_target_refs(
        self,
        state: StateContainer,
        concentration: Mapping[str, Any],
    ) -> tuple[dict[str, dict[str, Any]], int]:
        raw_target_refs = concentration.get("target_refs")
        if not isinstance(raw_target_refs, list):
            return ({}, 0)

        spell_id = self._coerce_non_empty_string(concentration.get("spell_id"))
        updates: dict[str, dict[str, Any]] = {}
        removed_count = 0
        for raw_ref in raw_target_refs:
            if not isinstance(raw_ref, Mapping):
                continue
            kind = self._coerce_non_empty_string(raw_ref.get("kind"))
            if kind != self._COMBAT_TARGET_KIND:
                continue
            sub_area_id = self._coerce_non_empty_string(raw_ref.get("sub_area_id"))
            participant_monster_id = self._coerce_non_empty_string(
                raw_ref.get("participant_monster_id")
            )
            if sub_area_id is None or participant_monster_id is None:
                continue
            payload = updates.get(sub_area_id)
            if payload is None:
                payload = self._get_hostile_payload(state, sub_area_id)
            if payload is None:
                continue

            participants = state.areas.participant_snapshots(payload)
            target_resolution = state.areas.resolve_participant(
                participant_monster_id,
                participants,
                by_monster_id_only=True,
            )
            if target_resolution is None:
                continue
            target_index, participant = target_resolution
            updated_target_effects, target_removed = self._remove_effect_instances(
                state.areas.participant_effects(participant),
                self._normalize_effect_ids(raw_ref.get("effect_ids")),
                spell_id,
            )
            if target_removed == 0:
                continue

            updated_participant = dict(participant)
            if updated_target_effects:
                updated_participant["active_effects"] = updated_target_effects
            else:
                updated_participant.pop("active_effects", None)
            participants[target_index] = updated_participant
            updated_payload = state.areas.update_hostile_participants(
                payload,
                participants,
            )
            updates[sub_area_id] = updated_payload
            removed_count += target_removed
        return (updates, removed_count)

    def _remove_effect_instances(
        self,
        current_effects: list[dict[str, Any]],
        effect_ids: set[str],
        spell_id: str | None,
    ) -> tuple[list[dict[str, Any]], int]:
        updated_effects: list[dict[str, Any]] = []
        removed_count = 0
        for effect in current_effects:
            instance_id = self._coerce_non_empty_string(effect.get("instance_id"))
            remove = False
            if instance_id is not None and instance_id in effect_ids:
                remove = True
            elif not effect_ids and spell_id and bool(effect.get("from_concentration")):
                if self._coerce_non_empty_string(effect.get("source_spell_id")) == spell_id:
                    remove = True
            if remove:
                removed_count += 1
                continue
            updated_effects.append(dict(effect))
        return (updated_effects, removed_count)

    def _resolve_combat_target(
        self,
        state: StateContainer,
        target: str,
    ) -> tuple[str, dict[str, Any], str, str] | None:
        if not state.has_slice("areas"):
            return None
        area_id = self._coerce_non_empty_string(state.player.current_area)
        if area_id is None:
            return None
        area = state.areas.areas.get(area_id)
        if area is None:
            return None

        active_combat: list[tuple[str, dict[str, Any]]] = []
        for sub_area_id, payload in area.hostile_tracking.items():
            if not isinstance(payload, Mapping):
                continue
            normalized_payload = state.areas.copy_hostile_state(payload)
            if bool(normalized_payload.get("combat_active")):
                active_combat.append((str(sub_area_id), normalized_payload))
        if len(active_combat) != 1:
            return None

        sub_area_id, payload = active_combat[0]
        participants = state.areas.participant_snapshots(payload)
        if not participants:
            return None
        target_resolution = state.areas.resolve_participant(target, participants)
        if target_resolution is None:
            return None
        _, participant = target_resolution
        if not bool(participant.get("alive", False)):
            return None
        return (
            sub_area_id,
            payload,
            state.areas.participant_monster_id(participant),
            state.areas.participant_name(participant),
        )

    def _hostile_changes(self, payloads: Mapping[str, Mapping[str, Any]]) -> list[StateChange]:
        changes: list[StateChange] = []
        for sub_area_id, payload in payloads.items():
            changes.append(
                StateChange(
                    "areas",
                    "modify",
                    f"hostile_tracking.{sub_area_id}",
                    dict(payload),
                )
            )
        return changes

    def _unsupported_cast_spell(
        self,
        *,
        status: str,
        spell_id: str,
        slot_level: int,
        target_mode: str | None = None,
        targets: list[str] | None = None,
    ) -> ExecuteResult:
        metadata = {
            "status": status,
            "spell_id": spell_id,
            "slot_level": slot_level,
            "consumed_slot": False,
            "resource_key": None,
            "resource_amount": 0,
            "hp_delta": 0,
            "applied_effect_ids": [],
            "broke_previous_concentration": False,
        }
        if target_mode is not None:
            metadata["target_mode"] = target_mode
        if targets is not None:
            metadata["targets"] = list(targets)
        return self._success_no_delta("cast_spell", metadata=metadata)

    def _get_hostile_payload(
        self,
        state: StateContainer,
        sub_area_id: str,
    ) -> dict[str, Any] | None:
        if not state.has_slice("areas"):
            return None
        payload = state.areas.get_hostile_state(sub_area_id)
        if isinstance(payload, Mapping):
            return state.areas.copy_hostile_state(payload)
        return None

    @staticmethod
    def _normalize_effect_ids(raw_effect_ids: Any) -> set[str]:
        if not isinstance(raw_effect_ids, list):
            return set()
        return {
            str(item)
            for item in raw_effect_ids
            if str(item).strip()
        }

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

    def _success(
        self,
        command_type: str,
        *,
        changes: list[StateChange],
        metadata: dict[str, Any],
        time_cost: float = 0.0,
        rolls: list[DiceRoll] | None = None,
    ) -> ExecuteResult:
        payload = {"handler": "spell", "command": command_type, **metadata}
        return ExecuteResult(
            success=True,
            delta=StateDelta(changes=changes, reason=command_type, metadata=payload),
            time_cost=time_cost,
            rolls=list(rolls or []),
            metadata=payload,
        )

    def _success_no_delta(
        self,
        command_type: str,
        *,
        metadata: dict[str, Any],
        time_cost: float = 0.0,
        rolls: list[DiceRoll] | None = None,
    ) -> ExecuteResult:
        return ExecuteResult(
            success=True,
            delta=None,
            time_cost=time_cost,
            rolls=list(rolls or []),
            metadata={"handler": "spell", "command": command_type, **metadata},
        )

    def _get_class_template(
        self,
        state: StateContainer,
        world: WorldInstance,
    ) -> dict[str, Any]:
        if not world.has_registry("classes"):
            return {}
        class_id = self._coerce_non_empty_string(state.player.character_class)
        if class_id is None:
            return {}
        template = world.classes.get_class(class_id)
        return dict(template) if isinstance(template, Mapping) else {}

    @staticmethod
    def _read_mapping(primary: Mapping[str, Any], fallback: Mapping[str, Any], key: str) -> dict[str, Any]:
        for source in (primary, fallback):
            raw_value = source.get(key)
            if isinstance(raw_value, Mapping):
                return dict(raw_value)
        return {}

    @staticmethod
    def _read_list(primary: Mapping[str, Any], fallback: Mapping[str, Any], key: str) -> list[str]:
        for source in (primary, fallback):
            raw_value = source.get(key)
            if isinstance(raw_value, list):
                return [str(item) for item in raw_value]
        return []

    @classmethod
    def _read_non_empty_string(
        cls,
        primary: Mapping[str, Any],
        fallback: Mapping[str, Any],
        *keys: str,
    ) -> str | None:
        for key in keys:
            for source in (primary, fallback):
                value = cls._coerce_non_empty_string(source.get(key))
                if value is not None:
                    return value
        return None

    @classmethod
    def _read_int(
        cls,
        primary: Mapping[str, Any],
        fallback: Mapping[str, Any],
        *keys: str,
    ) -> int | None:
        for key in keys:
            for source in (primary, fallback):
                value = cls._coerce_int(source.get(key))
                if value is not None:
                    return value
        return None

    @classmethod
    def _read_positive_int(
        cls,
        primary: Mapping[str, Any],
        fallback: Mapping[str, Any],
        *keys: str,
    ) -> int | None:
        value = cls._read_int(primary, fallback, *keys)
        if value is None or value < 0:
            return None
        return value

    @classmethod
    def _read_bool(
        cls,
        primary: Mapping[str, Any],
        fallback: Mapping[str, Any],
        key: str,
    ) -> bool:
        for source in (primary, fallback):
            if key in source:
                return bool(source.get(key))
        return False

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
        raw_value = params.get(key)
        if raw_value is None:
            return None
        identity = SpellHandler._coerce_non_empty_string(raw_value)
        if identity is None:
            return ValidationResult(ok=False, reason=f"{key} must be a non-empty string")
        if identity == "player":
            return None
        player_character_id = SpellHandler._coerce_non_empty_string(state.player.character_id)
        if player_character_id is not None and identity == player_character_id:
            return None
        return ValidationResult(ok=False, reason=f"{key} must refer to the current player")

    @staticmethod
    def _normalize_mapping(raw_value: Any) -> dict[str, Any]:
        if isinstance(raw_value, Mapping):
            return dict(raw_value)
        return {}

    @staticmethod
    def _get_non_empty_string(params: Mapping[str, Any], key: str) -> str | None:
        return SpellHandler._coerce_non_empty_string(params.get(key))

    @staticmethod
    def _coerce_non_empty_string(raw_value: Any) -> str | None:
        if raw_value is None:
            return None
        value = str(raw_value).strip()
        if not value:
            return None
        return value

    @staticmethod
    def _coerce_int(raw_value: Any) -> int | None:
        if raw_value is None or isinstance(raw_value, bool):
            return None
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            return None
