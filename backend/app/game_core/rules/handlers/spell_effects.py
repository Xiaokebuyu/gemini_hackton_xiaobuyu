"""Spell effect application helpers for SpellHandler."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Mapping
from uuid import uuid4

from app.game_core.content import WorldInstance
from app.game_core.content.registries.skills import StatusEffectTemplate
from app.game_core.rules.handler_utils import handler_success_no_delta
from app.game_core.rules.models import DiceRoll, ExecuteResult
from app.game_core.state import StateContainer

from app.game_core.rules.handlers.spell_concentration import (
    COMBAT_TARGET_KIND,
    clear_concentration_effects,
)
from app.game_core.rules.handlers.spell_resolver import (
    normalize_mapping,
    read_bool,
    read_int,
    read_list,
    read_mapping,
    read_non_empty_string,
    read_positive_int,
    resolve_combat_target,
)


def apply_self_target(
    state: StateContainer,
    template: Any,
    effect: Any,
    effect_type: str,
    spell_id: str,
    spell_level: int,
    resolved_slot_level: int,
    spellcasting_mod: int,
    resolved_targets: list[str],
    ctx: dict[str, Any],
    world: WorldInstance,
    *,
    roll_dice: Callable[[str], int],
) -> ExecuteResult | None:
    if effect_type == "heal":
        heal_total, heal_rolls = resolve_heal_amount(
            template,
            effect,
            spellcasting_mod,
            spell_level,
            resolved_slot_level,
            roll_dice=roll_dice,
        )
        if heal_total is None:
            return unsupported_cast_spell(
                status="unsupported_effect",
                spell_id=spell_id,
                slot_level=resolved_slot_level,
                target_mode="self",
                targets=resolved_targets,
            )
        ctx["rolls"].extend(heal_rolls)
        target_hp = min(int(state.player.max_hp), int(state.player.hp) + heal_total)
        ctx["hp_delta"] = target_hp - int(state.player.hp)
        ctx["target_hp"] = target_hp
        return None

    applies_status = read_non_empty_string(effect, template, "applies_status")
    se_template = None
    if applies_status and world.has_registry("skills"):
        se_template = world.skills.get_status_effect(applies_status)
    effect_instance, concentration_requested = build_spell_effect_instance(
        spell_id,
        effect_type,
        template,
        effect,
        status_effect_template=se_template,
    )
    if effect_instance is None:
        return unsupported_cast_spell(
            status="unsupported_effect",
            spell_id=spell_id,
            slot_level=resolved_slot_level,
            target_mode="self",
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
        ) = clear_concentration_effects(
            state,
            normalize_mapping(state.player.concentration),
        )
        ctx["pending_hostile_payloads"].update(cleared_target_payloads)
        ctx["broke_previous_concentration"] = (
            removed_count > 0 or state.player.concentration is not None
        )
    else:
        updated_effects = [dict(item) for item in updated_effects]

    updated_effects.append(effect_instance)
    ctx["player_effects_update"] = updated_effects
    ctx["applied_effect_ids"].append(str(effect_instance["instance_id"]))

    if concentration_requested:
        ctx["new_concentration_payload"] = {
            "spell_id": spell_id,
            "slot_level": resolved_slot_level,
            "remaining_duration": int(effect_instance.get("remaining_ticks", -1)),
            "applied_effects": [str(effect_instance["instance_id"])],
            "targets": list(resolved_targets),
            "target_refs": [],
        }
    return None


def apply_combat_target(
    state: StateContainer,
    template: Any,
    effect: Any,
    effect_type: str,
    spell_id: str,
    spell_level: int,
    resolved_slot_level: int,
    resolved_targets: list[str],
    ctx: dict[str, Any],
    world: WorldInstance,
    *,
    roll_dice: Callable[[str], int],
) -> ExecuteResult | None:
    combat_target = resolve_combat_target(state, resolved_targets[0])
    if combat_target is None:
        return unsupported_cast_spell(
            status="unsupported_target",
            spell_id=spell_id,
            slot_level=resolved_slot_level,
            target_mode="combat",
            targets=resolved_targets,
        )

    sub_area_id, hostile_payload, target_monster_id, _target_name = combat_target

    if effect_type == "damage":
        return apply_combat_damage(
            state,
            template,
            effect,
            spell_id,
            spell_level,
            resolved_slot_level,
            resolved_targets,
            sub_area_id,
            hostile_payload,
            target_monster_id,
            ctx,
            world,
            roll_dice=roll_dice,
        )

    return apply_combat_control(
        state,
        template,
        effect,
        spell_id,
        resolved_slot_level,
        resolved_targets,
        sub_area_id,
        hostile_payload,
        target_monster_id,
        ctx,
        world,
    )


def apply_combat_damage(
    state: StateContainer,
    template: Any,
    effect: Any,
    spell_id: str,
    spell_level: int,
    resolved_slot_level: int,
    resolved_targets: list[str],
    sub_area_id: str,
    hostile_payload: dict[str, Any],
    target_monster_id: str,
    ctx: dict[str, Any],
    world: WorldInstance,
    *,
    roll_dice: Callable[[str], int],
) -> ExecuteResult | None:
    damage_total, damage_rolls = resolve_damage_amount(
        template,
        effect,
        spell_level,
        resolved_slot_level,
        roll_dice=roll_dice,
    )
    if damage_total is None:
        return unsupported_cast_spell(
            status="unsupported_effect",
            spell_id=spell_id,
            slot_level=resolved_slot_level,
            target_mode="combat",
            targets=resolved_targets,
        )
    ctx["rolls"].extend(damage_rolls)

    # 豁免检定：若效果有 save 字段，让目标掷存档骰
    save_ability = read_non_empty_string(effect, template, "save")
    if save_ability is not None:
        monster_template = (
            world.monsters.get(target_monster_id)
            if world.has_registry("monsters") else None
        )
        abilities: dict[str, int] = getattr(monster_template, "abilities", {}) or {}
        ability_score = int(abilities.get(save_ability, 10))
        save_mod = (ability_score - 10) // 2
        save_raw = roll_dice("1d20")
        save_total = save_raw + save_mod
        spell_dc = int(ctx.get("spell_dc", 10))
        save_succeeded = save_total >= spell_dc
        half_on_save = read_bool(effect, template, "half_on_save")
        if save_succeeded:
            damage_total = max(1, damage_total // 2) if half_on_save else 0
        ctx.setdefault("save_rolls", []).append({
            "target_monster_id": target_monster_id,
            "save_ability": save_ability,
            "roll": save_raw,
            "mod": save_mod,
            "total": save_total,
            "dc": spell_dc,
            "succeeded": save_succeeded,
        })

    # 用 ctx 中已更新的 payload（多目标链式读取，与 apply_combat_control 对齐）
    current_payload = ctx["pending_hostile_payloads"].get(sub_area_id, hostile_payload)
    participants = state.areas.participant_snapshots(current_payload)
    target_resolution = state.areas.resolve_participant(
        target_monster_id,
        participants,
        by_monster_id_only=True,
    )
    if target_resolution is None:
        return unsupported_cast_spell(
            status="unsupported_target",
            spell_id=spell_id,
            slot_level=resolved_slot_level,
            target_mode="combat",
            targets=resolved_targets,
        )
    target_index, participant = target_resolution
    updated_target = dict(participant)
    remaining_hp = max(0, state.areas.participant_hp(participant) - damage_total)
    updated_target["hp"] = remaining_hp
    updated_target["alive"] = remaining_hp > 0
    participants[target_index] = updated_target
    updated_payload, combat_active, combat_cleared = state.areas.build_combat_hostile(
        current_payload,
        participants,
        blocking=bool(current_payload.get("blocking", False)),
        current_tick=_current_tick(state),
    )
    ctx["pending_hostile_payloads"][sub_area_id] = updated_payload
    ctx["damage_total"] = ctx.get("damage_total", 0) + damage_total  # 多目标累加
    ctx["target_hp"] = remaining_hp
    ctx["target_alive"] = remaining_hp > 0
    ctx["target_defeated"] = remaining_hp <= 0
    ctx["combat_active"] = combat_active
    ctx["combat_cleared"] = combat_cleared
    return None


def apply_combat_control(
    state: StateContainer,
    template: Any,
    effect: Any,
    spell_id: str,
    resolved_slot_level: int,
    resolved_targets: list[str],
    sub_area_id: str,
    hostile_payload: dict[str, Any],
    target_monster_id: str,
    ctx: dict[str, Any],
    world: WorldInstance,
) -> ExecuteResult | None:
    applies_status = read_non_empty_string(effect, template, "applies_status")
    se_template = None
    if applies_status and world.has_registry("skills"):
        se_template = world.skills.get_status_effect(applies_status)
    effect_instance, concentration_requested = build_spell_effect_instance(
        spell_id,
        "control",
        template,
        effect,
        status_effect_template=se_template,
    )
    if effect_instance is None:
        return unsupported_cast_spell(
            status="unsupported_effect",
            spell_id=spell_id,
            slot_level=resolved_slot_level,
            target_mode="combat",
            targets=resolved_targets,
        )

    if concentration_requested:
        (
            updated_effects,
            player_changed,
            cleared_target_payloads,
            removed_count,
            _,
        ) = clear_concentration_effects(
            state,
            normalize_mapping(state.player.concentration),
        )
        ctx["pending_hostile_payloads"].update(cleared_target_payloads)
        if player_changed:
            ctx["player_effects_update"] = updated_effects
        ctx["broke_previous_concentration"] = (
            removed_count > 0 or state.player.concentration is not None
        )

    payload_for_target = ctx["pending_hostile_payloads"].get(sub_area_id, hostile_payload)
    participants = state.areas.participant_snapshots(payload_for_target)
    target_resolution = state.areas.resolve_participant(
        target_monster_id,
        participants,
        by_monster_id_only=True,
    )
    if target_resolution is None:
        return unsupported_cast_spell(
            status="unsupported_target",
            spell_id=spell_id,
            slot_level=resolved_slot_level,
            target_mode="combat",
            targets=resolved_targets,
        )
    target_index, participant = target_resolution
    if not bool(participant.get("alive", False)):
        return unsupported_cast_spell(
            status="unsupported_target",
            spell_id=spell_id,
            slot_level=resolved_slot_level,
            target_mode="combat",
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
    ctx["pending_hostile_payloads"][sub_area_id] = updated_payload
    ctx["applied_effect_ids"].append(str(effect_instance["instance_id"]))
    ctx["target_effect_count"] = len(target_effects)
    ctx["combat_active"] = bool(updated_payload.get("combat_active", False))
    ctx["combat_cleared"] = bool(updated_payload.get("cleared", False))

    if concentration_requested:
        ctx["new_concentration_payload"] = {
            "spell_id": spell_id,
            "slot_level": resolved_slot_level,
            "remaining_duration": int(effect_instance.get("remaining_ticks", -1)),
            "applied_effects": [],
            "targets": list(resolved_targets),
            "target_refs": [
                {
                    "kind": COMBAT_TARGET_KIND,
                    "sub_area_id": sub_area_id,
                    "participant_monster_id": target_monster_id,
                    "effect_ids": [str(effect_instance["instance_id"])],
                }
            ],
        }
    return None


def merge_status_effect_template(
    effect_dict: dict[str, Any],
    template: StatusEffectTemplate | None,
) -> None:
    """将 StatusEffectTemplate 行为字段合并到效果实例 dict（实例已有值优先）。就地修改。"""
    if template is None:
        return
    if "disadvantage_checks" not in effect_dict and template.disadvantage_on:
        effect_dict["disadvantage_checks"] = list(template.disadvantage_on)
    if "advantage_on_attacks_against" not in effect_dict and template.advantage_on_attacks_against:
        effect_dict["advantage_on_attacks_against"] = True
    if "prevents_action" not in effect_dict and template.prevents_action:
        effect_dict["prevents_action"] = True
    if "save_end_of_turn" not in effect_dict and template.save_end_of_turn:
        effect_dict["save_end_of_turn"] = template.save_end_of_turn
    if "save_dc" not in effect_dict and template.save_dc is not None:
        effect_dict["save_dc"] = template.save_dc
    if "cure_conditions" not in effect_dict and template.cure_conditions:
        effect_dict["cure_conditions"] = list(template.cure_conditions)
    if not effect_dict.get("modifiers") and template.modifiers:
        effect_dict["modifiers"] = dict(template.modifiers)


def build_spell_effect_instance(
    spell_id: str,
    effect_type: str,
    template: Any,
    effect: Any,
    status_effect_template: StatusEffectTemplate | None = None,
) -> tuple[dict[str, Any] | None, bool]:
    applies_status = read_non_empty_string(effect, template, "applies_status")
    modifiers = read_mapping(effect, template, "modifiers")
    periodic = read_mapping(effect, template, "periodic")
    tags = read_list(effect, template, "tags")
    duration = read_int(effect, template, "status_duration", "duration_ticks", "duration")
    concentration = read_bool(effect, template, "concentration")

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
    merge_status_effect_template(effect_instance, status_effect_template)
    return (effect_instance, concentration)


def resolve_heal_amount(
    template: Any,
    effect: Any,
    spellcasting_mod: int,
    spell_level: int,
    slot_level: int,
    *,
    roll_dice: Callable[[str], int],
) -> tuple[int | None, list[DiceRoll]]:
    rolls: list[DiceRoll] = []

    fixed_value = read_positive_int(effect, template, "heal_amount", "heal")
    if fixed_value is not None:
        total = fixed_value + spellcasting_mod
        return (max(0, total), rolls)

    dice_expr = read_non_empty_string(effect, template, "dice")
    if dice_expr is None:
        return (None, [])

    rolled = roll_dice(dice_expr)
    rolls.append(
        DiceRoll(
            purpose="spell_heal",
            dice=dice_expr,
            result=rolled,
            total=rolled,
        )
    )
    total = rolled + spellcasting_mod

    upcast_dice = read_non_empty_string(effect, template, "upcast_dice")
    extra_levels = max(0, slot_level - spell_level)
    if upcast_dice is not None and extra_levels > 0:
        for _ in range(extra_levels):
            extra = roll_dice(upcast_dice)
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


def resolve_damage_amount(
    template: Any,
    effect: Any,
    spell_level: int,
    slot_level: int,
    *,
    roll_dice: Callable[[str], int],
) -> tuple[int | None, list[DiceRoll]]:
    rolls: list[DiceRoll] = []

    fixed_value = read_positive_int(effect, template, "damage_amount", "damage")
    if fixed_value is not None:
        total = fixed_value
    else:
        dice_expr = read_non_empty_string(effect, template, "dice")
        if dice_expr is None:
            return (None, [])
        rolled = roll_dice(dice_expr)
        rolls.append(
            DiceRoll(
                purpose="spell_damage",
                dice=dice_expr,
                result=rolled,
                total=rolled,
            )
        )
        total = rolled

    upcast_dice = read_non_empty_string(effect, template, "upcast_dice")
    extra_levels = max(0, slot_level - spell_level)
    if upcast_dice is not None and extra_levels > 0:
        for _ in range(extra_levels):
            extra = roll_dice(upcast_dice)
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


def unsupported_cast_spell(
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
    return handler_success_no_delta("spell", "cast_spell", metadata=metadata)


def _current_tick(state: StateContainer) -> int | None:
    if not state.has_slice("time"):
        return None
    return state.time.absolute_tick()
