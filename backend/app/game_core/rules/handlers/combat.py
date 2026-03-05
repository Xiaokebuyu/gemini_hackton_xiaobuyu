"""CombatHandler implementation."""

from __future__ import annotations

import random
import re
from typing import Any, Mapping

from app.game_core.content import WorldInstance
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.handler_utils import (
    build_dice_roll,
    coerce_int,
    coerce_non_empty_string,
    get_non_empty_string,
    handler_success,
    handler_success_no_delta,
    resolve_item_heal_amount,
    resolve_roll,
    roll_damage_dice,
)
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer


def _participant_effect_ac_mod(participant: Mapping[str, Any]) -> int:
    """从战斗参与者的 active_effects 中提取 AC 修正总量。"""
    total = 0
    for effect in participant.get("active_effects", []):
        mods = effect.get("modifiers")
        if isinstance(mods, Mapping):
            raw = mods.get("ac", 0)
            try:
                total += int(raw)
            except (TypeError, ValueError):
                pass
    return total


class CombatHandler(StaticCommandHandler):
    COMMAND_TYPES = (
        "attack",
        "defend",
        "disengage",
        "dash",
        "shove",
        "flee",
        "use_combat_item",
        "offhand_attack",
        "start_combat",
        "stand_up",
        "advance_combat_round",
    )

    _FLAG_COMMANDS = {
        "defend": "defending",
        "disengage": "disengaged",
        "dash": "dashed",
    }
    _DIRECT_RESOLUTION_COMMANDS = frozenset({"attack", "shove", "offhand_attack"})
    _SURPRISE_STATES = frozenset({"none", "player_surprise", "enemy_surprise"})

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if cmd.type == "start_combat":
            return self._validate_start_combat(cmd, state, world)
        if cmd.type == "advance_combat_round":
            return self._validate_advance_combat_round(cmd, state, world)
        if state.player.is_action_prevented():
            return ValidationResult(ok=False, reason="action prevented by active effect")
        if cmd.type in self._FLAG_COMMANDS:
            return self._validate_flag_command(cmd, state, world)
        if cmd.type == "flee":
            return self._validate_flee(cmd, state, world)
        if cmd.type == "use_combat_item":
            return self._validate_use_combat_item(cmd, state, world)
        if cmd.type in self._DIRECT_RESOLUTION_COMMANDS:
            return self._validate_direct_resolution_command(cmd, state, world)
        if cmd.type == "stand_up":
            return self._validate_stand_up(cmd, state)
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

        if cmd.type == "start_combat":
            return self._compute_start_combat(cmd, state, world)
        if cmd.type == "advance_combat_round":
            return self._compute_advance_combat_round(cmd, state, world)
        if cmd.type in self._FLAG_COMMANDS:
            return self._compute_flag_command(cmd, state)
        if cmd.type == "flee":
            return self._compute_flee(cmd, state)
        if cmd.type == "use_combat_item":
            return self._compute_use_combat_item(cmd, state, world)
        if cmd.type in self._DIRECT_RESOLUTION_COMMANDS:
            return self._compute_direct_resolution_command(cmd, state, world)
        if cmd.type == "stand_up":
            return self._compute_stand_up(cmd, state)
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _validate_start_combat(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if cmd.source not in {"engine", "system"}:
            return ValidationResult(ok=False, reason="start_combat is restricted to engine/system")
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")
        if not state.has_slice("areas"):
            return ValidationResult(ok=False, reason="areas slice is required")
        if not world.has_registry("monsters"):
            return ValidationResult(ok=False, reason="monsters registry is required")

        area_id = self._resolve_area_id(cmd.params, state)
        if area_id is None:
            return ValidationResult(ok=False, reason="area_id must be available")
        if not self._area_exists(area_id, state, world):
            return ValidationResult(ok=False, reason=f"unknown area: {area_id}")

        surprise_state = coerce_non_empty_string(cmd.params.get("surprise_state")) or "none"
        if surprise_state not in self._SURPRISE_STATES:
            return ValidationResult(ok=False, reason=f"unsupported surprise_state: {surprise_state}")

        explicit_sub_area = coerce_non_empty_string(cmd.params.get("sub_area_id"))
        if "sub_area_id" in cmd.params and explicit_sub_area is None:
            return ValidationResult(ok=False, reason="sub_area_id must be a non-empty string")

        monster_ids = self._normalize_monster_ids(cmd.params.get("monsters"))
        if cmd.params.get("monsters") is not None and not monster_ids:
            return ValidationResult(ok=False, reason="monsters must be a non-empty list")

        if not monster_ids:
            monster_ids = self._resolve_monsters_from_existing_hostile(
                area_id,
                explicit_sub_area,
                state,
            )
            if not monster_ids:
                return ValidationResult(
                    ok=False,
                    reason="monsters are required when no hostile monster_ids are available",
                )

        for monster_id in monster_ids:
            if world.monsters.get(monster_id) is None:
                return ValidationResult(ok=False, reason=f"unknown monster: {monster_id}")

        return ValidationResult(ok=True)

    def _validate_flag_command(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")
        if not state.has_slice("areas"):
            return ValidationResult(ok=False, reason="areas slice is required")
        identity_check = self._validate_character_identity(cmd.params, state)
        if identity_check is not None:
            return identity_check
        resolved = self._resolve_active_combat(cmd.params, state, world)
        if resolved is None:
            return ValidationResult(ok=False, reason="active combat sub_area_id is required")
        return ValidationResult(ok=True)

    def _validate_advance_combat_round(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if cmd.source not in {"engine", "system"}:
            return ValidationResult(
                ok=False,
                reason="advance_combat_round is restricted to engine/system",
            )
        if not state.has_slice("areas"):
            return ValidationResult(ok=False, reason="areas slice is required")
        resolved = self._resolve_active_combat(cmd.params, state, world)
        if resolved is None:
            return ValidationResult(ok=False, reason="active combat sub_area_id is required")
        return ValidationResult(ok=True)

    def _validate_flee(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        return self._validate_flag_command(cmd, state, world)

    def _validate_use_combat_item(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        common = self._validate_flag_command(cmd, state, world)
        if not common.ok:
            return common
        if not world.has_registry("items"):
            return ValidationResult(ok=False, reason="items registry is required")
        item_id = get_non_empty_string(cmd.params, "item_id")
        if item_id is None:
            return ValidationResult(ok=False, reason="item_id must be a non-empty string")
        if world.items.get(item_id) is None:
            return ValidationResult(ok=False, reason=f"unknown item: {item_id}")
        if state.player.get_item_count(item_id) < 1:
            return ValidationResult(ok=False, reason=f"item not in inventory: {item_id}")
        if "target" in cmd.params:
            target = coerce_non_empty_string(cmd.params.get("target"))
            if target is None:
                return ValidationResult(ok=False, reason="target must be player/self")
            if target not in {"player", "self"}:
                return ValidationResult(ok=False, reason="target must be player/self")
        return ValidationResult(ok=True)

    def _validate_direct_resolution_command(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        target = get_non_empty_string(cmd.params, "target")
        if target is None:
            return ValidationResult(ok=False, reason="target must be a non-empty string")
        if "sub_area_id" in cmd.params and coerce_non_empty_string(cmd.params.get("sub_area_id")) is None:
            return ValidationResult(ok=False, reason="sub_area_id must be a non-empty string")
        common = self._validate_flag_command(cmd, state, world)
        if not common.ok:
            return common
        resolved = self._resolve_active_combat(cmd.params, state, world)
        if resolved is None:
            return ValidationResult(ok=False, reason="active combat sub_area_id is required")
        _, payload, _ = resolved
        participants = state.areas.participant_snapshots(payload)
        if not participants:
            return ValidationResult(ok=False, reason="combat participants are required")
        target_resolution = state.areas.resolve_participant(target, participants)
        if target_resolution is None:
            return ValidationResult(ok=False, reason=f"unknown combat target: {target}")
        _, participant = target_resolution
        if not bool(participant.get("alive", False)):
            return ValidationResult(ok=False, reason=f"target is not alive: {target}")
        return ValidationResult(ok=True)

    def _compute_start_combat(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        area_id = self._resolve_area_id(cmd.params, state) or ""
        explicit_sub_area = coerce_non_empty_string(cmd.params.get("sub_area_id"))
        surprise_state = coerce_non_empty_string(cmd.params.get("surprise_state")) or "none"

        existing_sub_area = explicit_sub_area
        if existing_sub_area is None:
            uncleared_hostiles = self._uncleared_hostiles_in_area(area_id, state)
            if len(uncleared_hostiles) == 1:
                existing_sub_area = uncleared_hostiles[0][0]

        if existing_sub_area is None:
            if state.has_slice("time"):
                existing_sub_area = f"_combat_{area_id}_{state.time.absolute_tick()}"
            else:
                existing_sub_area = f"_combat_{area_id}_static"

        existing_payload = self._hostile_payload_for(existing_sub_area, area_id, state)
        if existing_payload and bool(existing_payload.get("combat_active")):
            return handler_success_no_delta(
                "combat",
                "start_combat",
                metadata={
                    "status": "already_active",
                    "area_id": area_id,
                    "sub_area_id": existing_sub_area,
                    "monster_count": len(existing_payload.get("monster_ids", []))
                    if isinstance(existing_payload.get("monster_ids"), list)
                    else 0,
                    "surprise_state": coerce_non_empty_string(existing_payload.get("surprise_state")) or surprise_state,
                    "combat_round": int(existing_payload.get("combat_round", 0)),
                },
            )

        monster_ids = self._normalize_monster_ids(cmd.params.get("monsters"))
        if not monster_ids and existing_payload:
            monster_ids = self._normalize_monster_ids(existing_payload.get("monster_ids"))
        participants = self._build_participants(monster_ids, world)
        existing_payload = existing_payload or {}
        combat_round = 0 if surprise_state != "none" else 1
        combat_seed = {
            **existing_payload,
            "area_id": area_id,
            "blocking": bool(existing_payload.get("blocking", True)),
            "combat_round": combat_round,
            "surprise_state": surprise_state,
            "combat_started_at_tick": state.time.absolute_tick() if state.has_slice("time") else None,
            "monster_ids": list(monster_ids),
        }
        combat_payload, _, _ = state.areas.build_combat_hostile(
            combat_seed,
            participants,
            blocking=bool(existing_payload.get("blocking", True)),
            player_flags=self._default_player_flags(),
            current_tick=self._current_tick(state),
        )

        return handler_success(
            "combat",
            "start_combat",
            changes=[
                StateChange(
                    "areas",
                    "modify",
                    f"hostile_tracking.{existing_sub_area}",
                    combat_payload,
                )
            ],
            metadata={
                "status": "started",
                "area_id": area_id,
                "sub_area_id": existing_sub_area,
                "monster_count": len(monster_ids),
                "surprise_state": surprise_state,
                "combat_round": combat_round,
            },
            omit_empty_delta=False,
        )

    def _compute_flag_command(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        resolved = self._resolve_active_combat(cmd.params, state, None)
        if resolved is None:
            return ExecuteResult.error("active combat not found")
        sub_area_id, payload, _ = resolved
        updated_payload = dict(payload)
        flags = self._normalized_player_flags(updated_payload)
        flag_key = self._FLAG_COMMANDS[cmd.type]
        flags[flag_key] = True
        updated_payload["player_flags"] = flags
        return handler_success(
            "combat",
            cmd.type,
            changes=[
                StateChange(
                    "areas",
                    "modify",
                    f"hostile_tracking.{sub_area_id}",
                    updated_payload,
                )
            ],
            metadata={
                "status": flag_key,
                "sub_area_id": sub_area_id,
            },
            omit_empty_delta=False,
        )

    def _compute_advance_combat_round(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        resolved = self._resolve_active_combat(cmd.params, state, world)
        if resolved is None:
            return ExecuteResult.error("active combat not found")
        sub_area_id, payload, _ = resolved
        updated_payload = state.areas.copy_hostile_state(payload)
        current_round = int(updated_payload.get("combat_round", 1))
        next_round = 1 if current_round < 1 else current_round + 1
        updated_payload["combat_round"] = next_round
        updated_payload["player_flags"] = self._default_player_flags()
        return handler_success(
            "combat",
            "advance_combat_round",
            changes=[
                StateChange(
                    "areas",
                    "modify",
                    f"hostile_tracking.{sub_area_id}",
                    updated_payload,
                )
            ],
            metadata={
                "status": "advanced",
                "sub_area_id": sub_area_id,
                "combat_round": next_round,
            },
            omit_empty_delta=False,
        )

    def _compute_flee(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        resolved = self._resolve_active_combat(cmd.params, state, None)
        if resolved is None:
            return ExecuteResult.error("active combat not found")
        sub_area_id, payload, _ = resolved
        flags = self._normalized_player_flags(payload)

        escape_dc = 10
        if bool(payload.get("blocking", False)):
            escape_dc += 2
        if flags["disengaged"]:
            escape_dc -= 2
        if flags["dashed"]:
            escape_dc -= 2

        roll_result, all_rolls, dice = resolve_roll()
        flee_bonus = max(
            state.player.get_skill_bonus("athletics"),
            state.player.get_skill_bonus("acrobatics"),
        )
        flee_total = roll_result + flee_bonus

        flee_roll = build_dice_roll(
            purpose="flee",
            dice=dice,
            result=roll_result,
            modifiers=[{"name": "athletics_or_acrobatics", "value": flee_bonus}],
            total=flee_total,
        )

        passed = flee_total >= escape_dc
        if not passed:
            return handler_success_no_delta(
                "combat",
                "flee",
                metadata={
                    "status": "failed",
                    "sub_area_id": sub_area_id,
                    "escape_dc": escape_dc,
                    "raw_roll": roll_result,
                    "all_rolls": list(all_rolls),
                    "flee_total": flee_total,
                    "passed": False,
                },
                rolls=[flee_roll],
            )

        updated_payload = state.areas.copy_hostile_state(payload)
        updated_payload["status"] = "active"
        updated_payload["combat_active"] = False
        updated_payload["player_flags"] = self._default_player_flags()
        updated_payload.pop("cleared_at_tick", None)
        return handler_success(
            "combat",
            "flee",
            changes=[
                StateChange(
                    "areas",
                    "modify",
                    f"hostile_tracking.{sub_area_id}",
                    updated_payload,
                )
            ],
            metadata={
                "status": "fled",
                "sub_area_id": sub_area_id,
                "escape_dc": escape_dc,
                "raw_roll": roll_result,
                "all_rolls": list(all_rolls),
                "flee_total": flee_total,
                "passed": True,
            },
            rolls=[flee_roll],
            omit_empty_delta=False,
        )

    def _compute_use_combat_item(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        resolved = self._resolve_active_combat(cmd.params, state, world)
        if resolved is None:
            return ExecuteResult.error("active combat not found")
        sub_area_id, _, _ = resolved
        item_id = str(cmd.params["item_id"]).strip()
        item_template = world.items.get(item_id)
        heal_amount = resolve_item_heal_amount(item_template)
        if heal_amount is None:
            return handler_success_no_delta(
                "combat",
                "use_combat_item",
                metadata={
                    "status": "no_effect",
                    "sub_area_id": sub_area_id,
                    "item_id": item_id,
                    "hp_delta": 0,
                },
            )

        inventory = self._player_inventory_snapshot(state)
        updated_inventory = self._remove_from_inventory(inventory, item_id, 1)
        target_hp = min(int(state.player.max_hp), int(state.player.hp) + heal_amount)
        hp_delta = target_hp - int(state.player.hp)

        changes: list[StateChange] = [
            StateChange("player", "set", "inventory", updated_inventory),
        ]
        if hp_delta != 0:
            changes.append(StateChange("player", "add", "hp", hp_delta))

        return handler_success(
            "combat",
            "use_combat_item",
            changes=changes,
            metadata={
                "status": "used",
                "sub_area_id": sub_area_id,
                "item_id": item_id,
                "hp_delta": hp_delta,
            },
            omit_empty_delta=False,
        )

    def _compute_direct_resolution_command(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        target = coerce_non_empty_string(cmd.params.get("target")) or ""
        resolved = self._resolve_active_combat(cmd.params, state, None)
        if resolved is None:
            return ExecuteResult.error("active combat not found")
        sub_area_id, payload, _ = resolved
        participants = state.areas.participant_snapshots(payload)
        target_resolution = state.areas.resolve_participant(target, participants)
        if target_resolution is None:
            return ExecuteResult.error(f"unknown combat target: {target}")
        target_index, participant = target_resolution
        if not bool(participant.get("alive", False)):
            return ExecuteResult.error(f"target is not alive: {target}")

        if cmd.type == "shove":
            return self._compute_shove_resolution(
                state=state,
                sub_area_id=sub_area_id,
                target=target,
                payload=payload,
                participants=participants,
                participant=participant,
            )
        return self._compute_attack_resolution(
            command_type=cmd.type,
            state=state,
            world=world,
            sub_area_id=sub_area_id,
            target=target,
            payload=payload,
            participants=participants,
            target_index=target_index,
            participant=participant,
            damage_type=str(cmd.params.get("damage_type", "physical")).strip().lower(),
        )

    def _compute_attack_resolution(
        self,
        *,
        command_type: str,
        state: StateContainer,
        world: WorldInstance,
        sub_area_id: str,
        target: str,
        payload: Mapping[str, Any],
        participants: list[dict[str, Any]],
        target_index: int,
        participant: Mapping[str, Any],
        damage_type: str = "physical",
    ) -> ExecuteResult:
        target_effects = participant.get("active_effects", [])
        adv = any(bool(e.get("advantage_on_attacks_against")) for e in target_effects)
        roll_result, all_rolls, dice = resolve_roll(advantage=adv)
        strength_mod = state.player.get_modifier("str")
        prof = state.player.proficiency_bonus
        effect_mods = state.player.get_effect_modifiers()
        attack_bonus_from_effects = effect_mods.get("attack", 0)
        attack_total = roll_result + strength_mod + prof + attack_bonus_from_effects
        target_ac = state.areas.participant_ac(participant) + _participant_effect_ac_mod(participant)
        hit = attack_total >= target_ac

        attack_roll = build_dice_roll(
            purpose="attack",
            dice=dice,
            result=roll_result,
            modifiers=[
                {"name": "str", "value": strength_mod},
                {"name": "proficiency", "value": prof},
                {"name": "effects", "value": attack_bonus_from_effects},
            ],
            total=attack_total,
        )

        updated_target = dict(participant)
        damage = 0
        damage_multiplier = 1.0
        if hit:
            raw_damage = 1 if command_type == "offhand_attack" else max(
                1,
                prof + strength_mod,
            )
            monster_id = state.areas.participant_monster_id(participant)
            template = world.monsters.get(monster_id) if world.has_registry("monsters") else None
            if template is not None:
                if damage_type in getattr(template, "immunities", []):
                    raw_damage = 0
                    damage_multiplier = 0.0
                elif damage_type in getattr(template, "resistances", []):
                    raw_damage = max(1, raw_damage // 2)
                    damage_multiplier = 0.5
                elif damage_type in getattr(template, "vulnerabilities", []):
                    raw_damage = raw_damage * 2
                    damage_multiplier = 2.0
            damage = raw_damage
            remaining_hp = max(0, state.areas.participant_hp(participant) - damage)
            updated_target["hp"] = remaining_hp
            updated_target["alive"] = remaining_hp > 0

        participants[target_index] = updated_target
        updated_payload, combat_active, combat_cleared = state.areas.build_combat_hostile(
            payload,
            participants,
            blocking=bool(payload.get("blocking", False)),
            player_flags=self._default_player_flags(),
            current_tick=self._current_tick(state),
        )
        target_name = state.areas.participant_name(updated_target)
        target_monster_id = state.areas.participant_monster_id(updated_target)
        target_alive = bool(updated_target.get("alive", False))

        # 怪物回合：仅在战斗仍活跃时执行
        extra_changes: list[StateChange] = []
        extra_rolls: list[Any] = []
        monster_responses: list[dict[str, Any]] = []

        if combat_active and not combat_cleared:
            updated_participants, extra_changes, extra_rolls, monster_responses = \
                self._resolve_monster_responses(
                    participants=state.areas.participant_snapshots(updated_payload),
                    state=state,
                    world=world,
                )
            # 若怪物逃跑导致战斗结束，重建 payload
            if any(r.get("action") == "flee" for r in monster_responses):
                updated_payload, combat_active, combat_cleared = state.areas.build_combat_hostile(
                    updated_payload,
                    updated_participants,
                    blocking=bool(updated_payload.get("blocking", False)),
                    player_flags=self._default_player_flags(),
                    current_tick=self._current_tick(state),
                )

        # XP 分发（combat_cleared 可能在怪物逃跑后才为 True）
        xp_awarded = 0
        if combat_cleared:
            xp_awarded = self._compute_combat_xp(
                state.areas.participant_snapshots(updated_payload), world
            )
            if xp_awarded > 0:
                extra_changes.append(
                    StateChange("player", "set", "xp", state.player.xp + xp_awarded)
                )
            # kill_count flags for defeated (non-fled) monsters
            if state.has_slice("flags"):
                for p in state.areas.participant_snapshots(updated_payload):
                    if not p.get("alive", True) and not p.get("fled", False):
                        mid = state.areas.participant_monster_id(p)
                        if mid:
                            key = f"kill_count_{mid}"
                            cur = int(state.flags.get(key, 0) or 0)
                            extra_changes.append(
                                StateChange("flags", "set", f"flags.{key}", cur + 1)
                            )

        all_changes = [
            StateChange(
                "areas",
                "modify",
                f"hostile_tracking.{sub_area_id}",
                updated_payload,
            ),
            *extra_changes,
        ]

        return handler_success(
            "combat",
            command_type,
            changes=all_changes,
            metadata={
                "status": "hit" if hit else "miss",
                "sub_area_id": sub_area_id,
                "target": target,
                "target_monster_id": target_monster_id,
                "target_name": target_name,
                "raw_roll": roll_result,
                "all_rolls": list(all_rolls),
                "attack_total": attack_total,
                "target_ac": target_ac,
                "damage": damage,
                "damage_type": damage_type,
                "damage_multiplier": damage_multiplier,
                "advantage": adv,
                "hit": hit,
                "target_hp": state.areas.participant_hp(updated_target),
                "target_alive": target_alive,
                "target_defeated": hit and not target_alive,
                "combat_active": combat_active,
                "combat_cleared": combat_cleared,
                "monster_responses": monster_responses,
                "xp_awarded": xp_awarded,
            },
            rolls=[attack_roll, *extra_rolls],
            omit_empty_delta=False,
        )

    def _compute_shove_resolution(
        self,
        *,
        state: StateContainer,
        sub_area_id: str,
        target: str,
        payload: Mapping[str, Any],
        participants: list[dict[str, Any]],
        participant: Mapping[str, Any],
    ) -> ExecuteResult:
        roll_result, all_rolls, dice = resolve_roll()
        athletics_bonus = state.player.get_skill_bonus("athletics")
        shove_total = roll_result + athletics_bonus
        target_ac = state.areas.participant_ac(participant) + _participant_effect_ac_mod(participant)
        resist_dc = 10 + max(0, target_ac - 10)
        passed = shove_total >= resist_dc

        shove_roll = build_dice_roll(
            purpose="shove",
            dice=dice,
            result=roll_result,
            modifiers=[{"name": "athletics", "value": athletics_bonus}],
            total=shove_total,
        )

        blocking = False if passed else bool(payload.get("blocking", False))
        updated_payload, combat_active, combat_cleared = state.areas.build_combat_hostile(
            payload,
            participants,
            blocking=blocking,
            player_flags=self._default_player_flags(),
            current_tick=self._current_tick(state),
        )

        return handler_success(
            "combat",
            "shove",
            changes=[
                StateChange(
                    "areas",
                    "modify",
                    f"hostile_tracking.{sub_area_id}",
                    updated_payload,
                )
            ],
            metadata={
                "status": "shoved" if passed else "resisted",
                "sub_area_id": sub_area_id,
                "target": target,
                "target_monster_id": state.areas.participant_monster_id(participant),
                "target_name": state.areas.participant_name(participant),
                "raw_roll": roll_result,
                "all_rolls": list(all_rolls),
                "shove_total": shove_total,
                "resist_dc": resist_dc,
                "passed": passed,
                "blocking": bool(updated_payload.get("blocking", False)),
                "combat_active": combat_active,
                "combat_cleared": combat_cleared,
            },
            rolls=[shove_roll],
            omit_empty_delta=False,
        )

    def _validate_stand_up(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")
        return ValidationResult(ok=True)

    def _compute_stand_up(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        effects = state.player.active_effects
        prone_effects = [e for e in effects if e.get("effect_id") == "prone"]
        if not prone_effects:
            return handler_success_no_delta(
                "combat",
                "stand_up",
                metadata={"status": "not_prone"},
            )
        updated = [e for e in effects if e.get("effect_id") != "prone"]
        changes = [StateChange("player", "set", "active_effects", updated)]
        return handler_success(
            "combat",
            "stand_up",
            changes=changes,
            metadata={
                "status": "stood_up",
                "removed_count": len(prone_effects),
            },
            narrative_hints=["你从地面起身，重新站稳脚跟。"],
        )

    def _resolve_active_combat(
        self,
        params: Mapping[str, Any],
        state: StateContainer,
        world: WorldInstance | None,
    ) -> tuple[str, dict[str, Any], str] | None:
        area_id = self._resolve_area_id(params, state)
        explicit_area = coerce_non_empty_string(params.get("area_id"))
        if explicit_area is not None and state.player.current_area and explicit_area != state.player.current_area:
            return None
        if area_id is None:
            return None
        if world is not None and not self._area_exists(area_id, state, world):
            return None

        sub_area_id = coerce_non_empty_string(params.get("sub_area_id"))
        if sub_area_id is not None:
            payload = self._hostile_payload_for(sub_area_id, area_id, state)
            if payload is None or not bool(payload.get("combat_active")):
                return None
            return (sub_area_id, payload, area_id)

        active = [
            (candidate_id, payload)
            for candidate_id, payload in self._active_hostiles_in_area(area_id, state)
            if bool(payload.get("combat_active"))
        ]
        if len(active) != 1:
            return None
        return (active[0][0], active[0][1], area_id)

    def _resolve_area_id(
        self,
        params: Mapping[str, Any],
        state: StateContainer,
    ) -> str | None:
        explicit = coerce_non_empty_string(params.get("area_id"))
        current = coerce_non_empty_string(state.player.current_area)
        if explicit is not None:
            if current is not None and explicit != current:
                return None
            return explicit
        return current

    def _resolve_monsters_from_existing_hostile(
        self,
        area_id: str,
        explicit_sub_area: str | None,
        state: StateContainer,
    ) -> list[str]:
        if explicit_sub_area is not None:
            payload = self._hostile_payload_for(explicit_sub_area, area_id, state)
            if payload is None:
                return []
            return self._normalize_monster_ids(payload.get("monster_ids"))
        uncleared = self._uncleared_hostiles_in_area(area_id, state)
        if len(uncleared) != 1:
            return []
        return self._normalize_monster_ids(uncleared[0][1].get("monster_ids"))

    def _build_participants(
        self,
        monster_ids: list[str],
        world: WorldInstance,
    ) -> list[dict[str, Any]]:
        participants: list[dict[str, Any]] = []
        seen: dict[str, int] = {}
        for monster_id in monster_ids:
            template = world.monsters.get(monster_id)
            if template is None:
                max_hp = 10
                ac = 10
                name = monster_id
            else:
                max_hp = template.hp or template.max_hp
                if max_hp is None or max_hp < 1:
                    max_hp = 10
                ac = template.ac if template.ac is not None and template.ac >= 1 else 10
                name = template.name or monster_id
            seen[monster_id] = seen.get(monster_id, 0) + 1
            participants.append(
                {
                    "id": f"{monster_id}_{seen[monster_id]}",
                    "monster_id": monster_id,
                    "name": name,
                    "hp": max_hp,
                    "max_hp": max_hp,
                    "ac": ac,
                    "alive": True,
                }
            )
        return participants

    def _area_exists(
        self,
        area_id: str,
        state: StateContainer,
        world: WorldInstance,
    ) -> bool:
        if world.has_registry("maps"):
            return world.maps.get(area_id) is not None
        return area_id in state.areas.areas

    def _uncleared_hostiles_in_area(
        self,
        area_id: str,
        state: StateContainer,
    ) -> list[tuple[str, dict[str, Any]]]:
        return [
            (sub_area_id, dict(payload))
            for sub_area_id, payload in self._hostiles_in_area(area_id, state)
            if not bool(payload.get("cleared", False))
        ]

    def _active_hostiles_in_area(
        self,
        area_id: str,
        state: StateContainer,
    ) -> list[tuple[str, dict[str, Any]]]:
        return [
            (sub_area_id, payload)
            for sub_area_id, payload in self._hostiles_in_area(area_id, state)
            if not bool(payload.get("cleared", False))
        ]

    def _hostiles_in_area(
        self,
        area_id: str,
        state: StateContainer,
    ) -> list[tuple[str, dict[str, Any]]]:
        area = state.areas.areas.get(area_id)
        if area is None:
            return []
        hostiles: list[tuple[str, dict[str, Any]]] = []
        for sub_area_id, payload in area.hostile_tracking.items():
            if isinstance(payload, Mapping):
                hostiles.append((str(sub_area_id), state.areas.copy_hostile_state(payload)))
        return hostiles

    def _hostile_payload_for(
        self,
        sub_area_id: str,
        area_id: str,
        state: StateContainer,
    ) -> dict[str, Any] | None:
        payload = state.areas.get_hostile_state(sub_area_id)
        if not isinstance(payload, Mapping):
            return None
        resolved_area = coerce_non_empty_string(payload.get("area_id"))
        if resolved_area is not None and resolved_area != area_id:
            return None
        normalized = state.areas.copy_hostile_state(payload)
        normalized["area_id"] = area_id
        return normalized

    @staticmethod
    def _current_tick(state: StateContainer) -> int | None:
        if not state.has_slice("time"):
            return None
        return state.time.absolute_tick()

    @staticmethod
    def _default_player_flags() -> dict[str, bool]:
        return {
            "defending": False,
            "disengaged": False,
            "dashed": False,
        }

    @staticmethod
    def _normalized_player_flags(payload: Mapping[str, Any]) -> dict[str, bool]:
        raw_flags = payload.get("player_flags", {})
        if not isinstance(raw_flags, Mapping):
            raw_flags = {}
        return {
            "defending": bool(raw_flags.get("defending", False)),
            "disengaged": bool(raw_flags.get("disengaged", False)),
            "dashed": bool(raw_flags.get("dashed", False)),
        }


    @staticmethod
    def _player_inventory_snapshot(state: StateContainer) -> list[dict[str, Any]]:
        return [item.snapshot() for item in state.player.inventory]

    # ------------------------------------------------------------------
    # Combat AI helpers（怪物回合决策 + XP 分发）
    # ------------------------------------------------------------------

    @staticmethod
    def _decide_monster_action(
        ai_personality: str,
        hp_ratio: float,
        flee_threshold: float,
        flee_chance: float = 0.5,
    ) -> str:
        """Decide what action the monster takes this turn.

        Returns "attack" or "flee".
        flee_threshold == 0.0 means the monster never flees voluntarily.
        flee_chance: probability of actually fleeing once below flee_threshold.
        """
        if flee_threshold <= 0.0 or hp_ratio > flee_threshold:
            return "attack"
        if ai_personality == "aggressive" and hp_ratio >= 0.1:
            return "attack"
        return "flee" if random.random() < flee_chance else "attack"

    @staticmethod
    def _estimate_damage(dice_str: str) -> float:
        """期望伤害估算，如 '2d6' → 7.0，用于攻击选择。"""
        m = re.match(r"(\d+)d(\d+)", str(dice_str))
        if not m:
            return 1.0
        n, d = int(m.group(1)), int(m.group(2))
        return n * (d + 1) / 2.0

    @staticmethod
    def _select_attack(attacks: list[Any], ai_personality: str) -> Any:
        """根据 ai_personality 从多攻击中选择最优攻击。"""
        if len(attacks) <= 1:
            return attacks[0]
        if ai_personality == "aggressive":
            return max(
                attacks,
                key=lambda a: CombatHandler._estimate_damage(getattr(a, "damage_dice", "1d4")),
            )
        if ai_personality == "defensive":
            return max(attacks, key=lambda a: int(getattr(a, "hit_bonus", 0) or 0))
        # cowardly: 选射程最远
        return max(attacks, key=lambda a: int(getattr(a, "range", 1) or 1))

    @staticmethod
    def _apply_player_damage_resistance(
        damage: int,
        damage_type: str,
        state: StateContainer,
    ) -> int:
        """检查玩家 active_effects 的 tags，应用 immunity/resistance/vulnerability。"""
        for effect in state.player.get_active_effects():
            tags = effect.get("tags", [])
            if f"{damage_type}_immunity" in tags:
                return 0
            if f"{damage_type}_resistance" in tags:
                return max(1, damage // 2)
            if f"{damage_type}_vulnerability" in tags:
                return damage * 2
        return damage

    @staticmethod
    def _roll_monster_attack(
        attack_name: str,
        damage_dice: str,
        hit_bonus: int,
        player_ac: int,
        advantage: bool = False,
    ) -> tuple[bool, int, Any]:
        """Roll a monster attack against the player's AC.

        Returns (hit, damage, DiceRoll).
        """
        d20, all_d20, dice_notation = resolve_roll(advantage=advantage)
        total = d20 + hit_bonus
        hit = total >= player_ac
        damage = roll_damage_dice(damage_dice) if hit else 0
        roll = build_dice_roll(
            purpose=f"monster_attack:{attack_name}",
            dice=dice_notation,
            result=d20,
            modifiers=[{"name": "hit_bonus", "value": hit_bonus}],
            total=total,
        )
        return hit, damage, roll

    def _resolve_monster_responses(
        self,
        *,
        participants: list[dict[str, Any]],
        state: StateContainer,
        world: WorldInstance,
    ) -> tuple[list[dict[str, Any]], list[StateChange], list[Any], list[dict[str, Any]]]:
        """Resolve each alive monster's action this turn.

        Returns:
            updated_participants – participant list with fled monsters marked
            extra_changes        – StateChange list (player HP change if hit)
            extra_rolls          – DiceRoll list from monster attacks
            responses            – per-monster action metadata
        """
        # 玩家 AC：使用 stored ac 字段 + 活跃效果修正（如防御姿态 +2 AC）
        effect_mods = state.player.get_effect_modifiers()
        player_ac = state.player.ac + effect_mods.get("ac", 0)
        monster_adv_on_player = state.player.get_advantage_on_attacks_against()
        player_hp = state.player.hp
        updated_participants = [dict(p) for p in participants]
        extra_changes: list[StateChange] = []
        extra_rolls: list[Any] = []
        responses: list[dict[str, Any]] = []
        total_player_damage = 0

        for idx, p in enumerate(updated_participants):
            if not bool(p.get("alive", False)):
                continue  # 已死亡或已逃跑的怪物不再行动

            monster_id = str(p.get("monster_id", ""))
            hp = int(p.get("hp", 0))
            max_hp = int(p.get("max_hp", 1))
            hp_ratio = hp / max_hp if max_hp > 0 else 0.0

            template = world.monsters.get(monster_id) if world.has_registry("monsters") else None
            ai_personality = getattr(template, "ai_personality", "aggressive") if template else "aggressive"
            flee_threshold = getattr(template, "flee_threshold", 0.0) if template else 0.0
            flee_chance = float(getattr(template, "flee_chance", 0.5)) if template else 0.5

            # Phase 4: 若怪物受 prevents_action 效果影响，跳过本回合
            monster_effects = p.get("active_effects", [])
            if any(bool(e.get("prevents_action")) for e in monster_effects):
                responses.append({
                    "monster_id": monster_id,
                    "action": "stunned",
                    "hit": False,
                    "damage": 0,
                })
                continue

            action = self._decide_monster_action(ai_personality, hp_ratio, flee_threshold, flee_chance)

            if action == "flee":
                updated_participants[idx]["alive"] = False
                updated_participants[idx]["fled"] = True
                responses.append({
                    "monster_id": monster_id,
                    "action": "flee",
                    "hit": False,
                    "damage": 0,
                })
                continue

            # action == "attack"：仅当怪物模板有 attacks 时才发起攻击
            attacks = getattr(template, "attacks", []) if template else []
            if not attacks:
                # 无 attacks 定义 → 本回合不行动
                responses.append({
                    "monster_id": monster_id,
                    "action": "hold",
                    "hit": False,
                    "damage": 0,
                })
                continue

            selected_attack = self._select_attack(attacks, ai_personality)
            attack_name = getattr(selected_attack, "name", "strike") or "strike"
            damage_dice = getattr(selected_attack, "damage_dice", "1d4") or "1d4"
            hit_bonus = int(getattr(selected_attack, "hit_bonus", 0) or 0)
            damage_type = getattr(selected_attack, "damage_type", "physical") or "physical"

            hit, dmg, roll = self._roll_monster_attack(
                attack_name, damage_dice, hit_bonus, player_ac,
                advantage=monster_adv_on_player,
            )
            if hit and dmg > 0:
                dmg = self._apply_player_damage_resistance(dmg, damage_type, state)
            extra_rolls.append(roll)
            total_player_damage += dmg
            responses.append({
                "monster_id": monster_id,
                "action": "attack",
                "hit": hit,
                "damage": dmg,
                "damage_type": damage_type,
                "attack_name": attack_name,
            })

        if total_player_damage > 0:
            new_hp = max(0, player_hp - total_player_damage)
            extra_changes.append(StateChange("player", "set", "hp", new_hp))

        return updated_participants, extra_changes, extra_rolls, responses

    def _compute_combat_xp(
        self,
        participants: list[dict[str, Any]],
        world: WorldInstance,
    ) -> int:
        """Sum xp_reward for killed monsters (dead but not fled)."""
        if not world.has_registry("monsters"):
            return 0
        total = 0
        for p in participants:
            if bool(p.get("alive", True)):
                continue  # still alive
            if bool(p.get("fled", False)):
                continue  # fled, no XP
            monster_id = str(p.get("monster_id", ""))
            template = world.monsters.get(monster_id)
            if template is not None:
                total += int(getattr(template, "xp_reward", 0) or 0)
        return total

    @staticmethod
    def _remove_from_inventory(
        inventory: list[dict[str, Any]],
        item_id: str,
        count: int,
    ) -> list[dict[str, Any]]:
        updated: list[dict[str, Any]] = []
        removed = False
        for item in inventory:
            if removed or str(item.get("item_id")) != item_id:
                updated.append(dict(item))
                continue
            current_count = coerce_int(item.get("count")) or 0
            remaining = current_count - count
            if remaining < 0:
                raise ValueError(f"not enough items: {item_id}")
            if remaining > 0:
                new_item = dict(item)
                new_item["count"] = remaining
                updated.append(new_item)
            removed = True
        if not removed:
            raise ValueError(f"item not found: {item_id}")
        return updated

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
        value = coerce_non_empty_string(raw_value)
        if value is None:
            return ValidationResult(ok=False, reason=f"{key} must be a non-empty string")
        if value == "player":
            return None
        player_character_id = coerce_non_empty_string(state.player.character_id)
        if player_character_id is not None and value == player_character_id:
            return None
        return ValidationResult(ok=False, reason=f"{key} must refer to the current player")

    @staticmethod
    def _normalize_monster_ids(raw_value: Any) -> list[str]:
        if not isinstance(raw_value, list):
            return []
        monster_ids: list[str] = []
        for item in raw_value:
            monster_id = coerce_non_empty_string(item)
            if monster_id is not None:
                monster_ids.append(monster_id)
        return monster_ids
