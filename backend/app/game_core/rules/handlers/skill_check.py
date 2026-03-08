"""SkillCheckHandler implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content import WorldInstance
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.handler_utils import (
    build_dice_roll,
    coerce_int,
    get_non_empty_string,
    handler_success,
    handler_success_no_delta,
    resolve_roll,
)
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer


class SkillCheckHandler(StaticCommandHandler):
    COMMAND_TYPES = ("skill_check", "saving_throw", "contest", "investigate")

    _VALID_ABILITIES = frozenset({"str", "dex", "con", "int", "wis", "cha"})

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        del world
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")
        if cmd.type == "skill_check":
            return self._validate_skill_check(cmd)
        if cmd.type == "saving_throw":
            return self._validate_saving_throw(cmd)
        if cmd.type == "contest":
            return self._validate_contest(cmd)
        if cmd.type == "investigate":
            return self._validate_investigate(cmd, state)
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

        if cmd.type == "skill_check":
            return self._compute_skill_check(cmd, state)
        if cmd.type == "saving_throw":
            return self._compute_saving_throw(cmd, state)
        if cmd.type == "contest":
            return self._compute_contest(cmd, state)
        if cmd.type == "investigate":
            return self._compute_investigate(cmd, state)
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _validate_skill_check(self, cmd: Command) -> ValidationResult:
        skill = get_non_empty_string(cmd.params, "skill")
        if skill is None:
            return ValidationResult(ok=False, reason="skill must be a non-empty string")
        dc = coerce_int(cmd.params.get("dc"))
        if dc is None or dc < 0:
            return ValidationResult(ok=False, reason="dc must be an integer >= 0")
        character_issue = self._validate_optional_actor(cmd.params, "character")
        if character_issue:
            return ValidationResult(ok=False, reason=character_issue)
        flag_issue = self._validate_roll_flags(cmd.params, ("advantage", "disadvantage"))
        if flag_issue:
            return ValidationResult(ok=False, reason=flag_issue)
        del skill, dc
        return ValidationResult(ok=True)

    def _validate_saving_throw(self, cmd: Command) -> ValidationResult:
        ability = get_non_empty_string(cmd.params, "ability")
        if ability is None:
            return ValidationResult(ok=False, reason="ability must be a non-empty string")
        if ability not in self._VALID_ABILITIES:
            return ValidationResult(ok=False, reason=f"unsupported ability: {ability}")
        dc = coerce_int(cmd.params.get("dc"))
        if dc is None or dc < 0:
            return ValidationResult(ok=False, reason="dc must be an integer >= 0")
        character_issue = self._validate_optional_actor(cmd.params, "character")
        if character_issue:
            return ValidationResult(ok=False, reason=character_issue)
        flag_issue = self._validate_roll_flags(cmd.params, ("advantage", "disadvantage"))
        if flag_issue:
            return ValidationResult(ok=False, reason=flag_issue)
        del ability, dc
        return ValidationResult(ok=True)

    def _validate_contest(self, cmd: Command) -> ValidationResult:
        actor_skill = get_non_empty_string(cmd.params, "actor_skill")
        if actor_skill is None:
            return ValidationResult(
                ok=False,
                reason="actor_skill must be a non-empty string",
            )
        target_skill = get_non_empty_string(cmd.params, "target_skill")
        if target_skill is None:
            return ValidationResult(
                ok=False,
                reason="target_skill must be a non-empty string",
            )

        actor_issue = self._validate_optional_actor(cmd.params, "actor")
        if actor_issue:
            return ValidationResult(ok=False, reason=actor_issue)
        target_issue = self._validate_optional_actor(cmd.params, "target")
        if target_issue:
            return ValidationResult(ok=False, reason=target_issue)

        actor_bonus = coerce_int(cmd.params.get("actor_bonus"))
        if "actor_bonus" in cmd.params and actor_bonus is None:
            return ValidationResult(ok=False, reason="actor_bonus must be an integer")
        target_bonus = coerce_int(cmd.params.get("target_bonus"))
        if "target_bonus" in cmd.params and target_bonus is None:
            return ValidationResult(ok=False, reason="target_bonus must be an integer")

        target_id = self._get_optional_non_empty_string(cmd.params.get("target"))
        if target_id not in {None, "player"} and target_bonus is None:
            return ValidationResult(
                ok=False,
                reason="target_bonus is required for non-player targets",
            )

        flag_issue = self._validate_roll_flags(
            cmd.params,
            (
                "actor_advantage",
                "actor_disadvantage",
                "target_advantage",
                "target_disadvantage",
            ),
        )
        if flag_issue:
            return ValidationResult(ok=False, reason=flag_issue)

        del actor_skill, target_skill, actor_bonus, target_bonus
        return ValidationResult(ok=True)

    def _compute_skill_check(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        skill = str(cmd.params["skill"]).strip()
        dc = int(cmd.params["dc"])
        explicit_adv = bool(cmd.params.get("advantage", False))
        explicit_dis = bool(cmd.params.get("disadvantage", False))
        if not explicit_dis:
            dis_checks = state.player.get_disadvantage_checks()
            if skill in dis_checks or "all" in dis_checks:
                explicit_dis = True
        roll_result, all_rolls, dice = resolve_roll(
            advantage=explicit_adv,
            disadvantage=explicit_dis,
        )
        modifier = state.player.get_skill_bonus(skill)
        total = roll_result + modifier
        return ExecuteResult(
            executed=True,
            rolls=[
                build_dice_roll(
                    purpose="skill_check",
                    dice=dice,
                    result=roll_result,
                    modifiers=[{"name": "skill_bonus", "value": modifier}],
                    total=total,
                )
            ],
            narrative_hints=self._critical_hints(roll_result),
            time_cost=1.0 / 6.0,
            metadata={
                "handler": "skill_check",
                "command": cmd.type,
                "skill": skill,
                "passed": total >= dc,
                "dc": dc,
                "raw_roll": roll_result,
                "selected_roll": roll_result,
                "all_rolls": list(all_rolls),
                "modifier": modifier,
                "total": total,
                "auto_disadvantage": explicit_dis and not bool(cmd.params.get("disadvantage", False)),
            },
        )

    def _compute_saving_throw(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        ability = str(cmd.params["ability"]).strip()
        dc = int(cmd.params["dc"])
        explicit_dis = bool(cmd.params.get("disadvantage", False))
        if not explicit_dis:
            dis_checks = state.player.get_disadvantage_checks()
            if ability in dis_checks or "all" in dis_checks:
                explicit_dis = True
        roll_result, all_rolls, dice = resolve_roll(
            advantage=bool(cmd.params.get("advantage", False)),
            disadvantage=explicit_dis,
        )
        modifier = state.player.get_modifier(ability)
        if ability in getattr(state.player, "save_proficiencies", []):
            modifier += state.player.proficiency_bonus
        total = roll_result + modifier
        return ExecuteResult(
            executed=True,
            rolls=[
                build_dice_roll(
                    purpose="saving_throw",
                    dice=dice,
                    result=roll_result,
                    modifiers=[{"name": "save_bonus", "value": modifier}],
                    total=total,
                )
            ],
            narrative_hints=self._critical_hints(roll_result),
            metadata={
                "handler": "skill_check",
                "command": cmd.type,
                "ability": ability,
                "passed": total >= dc,
                "dc": dc,
                "raw_roll": roll_result,
                "selected_roll": roll_result,
                "all_rolls": list(all_rolls),
                "modifier": modifier,
                "total": total,
                "auto_disadvantage": explicit_dis and not bool(cmd.params.get("disadvantage", False)),
            },
        )

    def _compute_contest(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        actor_skill = str(cmd.params["actor_skill"]).strip()
        target_skill = str(cmd.params["target_skill"]).strip()

        actor_bonus = coerce_int(cmd.params.get("actor_bonus"))
        if actor_bonus is None:
            actor_bonus = state.player.get_skill_bonus(actor_skill)

        target_bonus = coerce_int(cmd.params.get("target_bonus"))
        if target_bonus is None:
            target_bonus = state.player.get_skill_bonus(target_skill)

        actor_roll, actor_rolls, actor_dice = resolve_roll(
            advantage=bool(cmd.params.get("actor_advantage", False)),
            disadvantage=bool(cmd.params.get("actor_disadvantage", False)),
        )
        target_roll, target_rolls, target_dice = resolve_roll(
            advantage=bool(cmd.params.get("target_advantage", False)),
            disadvantage=bool(cmd.params.get("target_disadvantage", False)),
        )

        actor_total = actor_roll + actor_bonus
        target_total = target_roll + target_bonus
        if actor_total > target_total:
            winner = "actor"
        elif target_total > actor_total:
            winner = "target"
        else:
            winner = "tie"

        return ExecuteResult(
            executed=True,
            rolls=[
                build_dice_roll(
                    purpose="contest_actor",
                    dice=actor_dice,
                    result=actor_roll,
                    modifiers=[{"name": "actor_bonus", "value": actor_bonus}],
                    total=actor_total,
                ),
                build_dice_roll(
                    purpose="contest_target",
                    dice=target_dice,
                    result=target_roll,
                    modifiers=[{"name": "target_bonus", "value": target_bonus}],
                    total=target_total,
                ),
            ],
            narrative_hints=(
                self._critical_hints(actor_roll, prefix="actor ")
                + self._critical_hints(target_roll, prefix="target ")
            ),
            metadata={
                "handler": "skill_check",
                "command": cmd.type,
                "actor_skill": actor_skill,
                "target_skill": target_skill,
                "winner": winner,
                "actor_total": actor_total,
                "target_total": target_total,
                "actor_roll": actor_roll,
                "target_roll": target_roll,
                "actor_bonus": actor_bonus,
                "target_bonus": target_bonus,
                "actor_all_rolls": list(actor_rolls),
                "target_all_rolls": list(target_rolls),
            },
        )

    _INVESTIGATE_SKILLS = frozenset({"perception", "investigation", "survival", "nature"})

    def _validate_investigate(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ValidationResult:
        if not state.has_slice("areas"):
            return ValidationResult(ok=False, reason="areas slice is required")
        skill = self._get_optional_non_empty_string(cmd.params.get("skill"))
        if skill is not None and skill not in self._INVESTIGATE_SKILLS:
            return ValidationResult(ok=False, reason=f"unsupported investigate skill: {skill}")
        return ValidationResult(ok=True)

    def _compute_investigate(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        area_id = state.player.current_area
        if not area_id:
            return handler_success_no_delta(
                "skill_check",
                "investigate",
                time_cost=1.0 / 6.0,
                metadata={"status": "no_area"},
            )
        area_state = state.areas.areas.get(area_id)
        if area_state is None:
            return handler_success_no_delta(
                "skill_check",
                "investigate",
                time_cost=1.0 / 6.0,
                metadata={"status": "no_area"},
            )

        search_targets = area_state.properties.get("search_targets", {})
        if not isinstance(search_targets, dict) or not search_targets:
            return handler_success_no_delta(
                "skill_check",
                "investigate",
                time_cost=1.0 / 6.0,
                metadata={"status": "nothing_to_find", "area_id": area_id},
            )

        target_id = self._get_optional_non_empty_string(cmd.params.get("target_id"))
        if target_id is not None:
            target = search_targets.get(target_id)
            if target is None:
                return handler_success_no_delta(
                    "skill_check",
                    "investigate",
                    time_cost=1.0 / 6.0,
                    metadata={"status": "nothing_to_find", "area_id": area_id},
                )
            targets_to_check = {target_id: target}
        else:
            targets_to_check = dict(search_targets)

        # Filter already-discovered targets
        discoveries = area_state.properties.get("discoveries", {})
        if not isinstance(discoveries, dict):
            discoveries = {}
        undiscovered = {
            k: v for k, v in targets_to_check.items()
            if not discoveries.get(k)
        }
        if not undiscovered:
            return handler_success_no_delta(
                "skill_check",
                "investigate",
                time_cost=1.0 / 6.0,
                metadata={"status": "already_discovered", "area_id": area_id},
            )

        skill = self._get_optional_non_empty_string(cmd.params.get("skill")) or "perception"
        default_dc = coerce_int(area_state.properties.get("search_dc")) or 12

        roll_result, all_rolls, dice = resolve_roll()
        modifier = state.player.get_skill_bonus(skill)
        total = roll_result + modifier

        found: list[str] = []
        changes: list[StateChange] = []
        for tid, tdata in undiscovered.items():
            dc = default_dc
            if isinstance(tdata, dict):
                target_dc = coerce_int(tdata.get("dc"))
                if target_dc is not None:
                    dc = target_dc
            if total >= dc:
                found.append(tid)
                current_discoveries = dict(discoveries)
                current_discoveries[tid] = True
                changes.append(
                    StateChange(
                        "areas", "modify",
                        f"{area_id}.properties.discoveries",
                        current_discoveries,
                    )
                )

        check_roll = build_dice_roll(
            purpose="investigate",
            dice=dice,
            result=roll_result,
            modifiers=[{"name": "skill_bonus", "value": modifier}],
            total=total,
        )

        if found:
            return handler_success(
                "skill_check",
                "investigate",
                changes=changes,
                time_cost=1.0 / 6.0,
                metadata={
                    "status": "discovered",
                    "area_id": area_id,
                    "skill": skill,
                    "passed": True,
                    "found": found,
                    "raw_roll": roll_result,
                    "all_rolls": list(all_rolls),
                    "modifier": modifier,
                    "total": total,
                },
                rolls=[check_roll],
                narrative_hints=self._critical_hints(roll_result),
            )

        return handler_success_no_delta(
            "skill_check",
            "investigate",
            time_cost=1.0 / 6.0,
            metadata={
                "status": "found_nothing",
                "area_id": area_id,
                "skill": skill,
                "passed": False,
                "raw_roll": roll_result,
                "all_rolls": list(all_rolls),
                "modifier": modifier,
                "total": total,
            },
            rolls=[check_roll],
        )

    @staticmethod
    def _critical_hints(result: int, prefix: str = "") -> list[str]:
        if result == 20:
            return [f"{prefix}extraordinary success".strip()]
        if result == 1:
            return [f"{prefix}disastrous failure".strip()]
        return []

    @staticmethod
    def _validate_roll_flags(
        params: Mapping[str, Any],
        keys: tuple[str, ...],
    ) -> str | None:
        for key in keys:
            if key in params and not isinstance(params[key], bool):
                return f"{key} must be a boolean"
        return None

    @staticmethod
    def _validate_optional_actor(
        params: Mapping[str, Any],
        key: str,
    ) -> str | None:
        if key not in params:
            return None
        value = SkillCheckHandler._get_optional_non_empty_string(params.get(key))
        if value is None:
            return f"{key} must be a non-empty string"
        return None

    @staticmethod
    def _get_optional_non_empty_string(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None
