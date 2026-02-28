"""SkillCheckHandler implementation."""

from __future__ import annotations

import random
from typing import Any, Mapping

from app.game_core.content import WorldInstance
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.models import Command, DiceRoll, ExecuteResult, ValidationResult
from app.game_core.state import StateContainer


class SkillCheckHandler(StaticCommandHandler):
    COMMAND_TYPES = ("skill_check", "saving_throw", "contest")

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
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _validate_skill_check(self, cmd: Command) -> ValidationResult:
        skill = self._get_non_empty_string(cmd.params, "skill")
        if skill is None:
            return ValidationResult(ok=False, reason="skill must be a non-empty string")
        dc = self._coerce_int(cmd.params.get("dc"))
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
        ability = self._get_non_empty_string(cmd.params, "ability")
        if ability is None:
            return ValidationResult(ok=False, reason="ability must be a non-empty string")
        if ability not in self._VALID_ABILITIES:
            return ValidationResult(ok=False, reason=f"unsupported ability: {ability}")
        dc = self._coerce_int(cmd.params.get("dc"))
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
        actor_skill = self._get_non_empty_string(cmd.params, "actor_skill")
        if actor_skill is None:
            return ValidationResult(
                ok=False,
                reason="actor_skill must be a non-empty string",
            )
        target_skill = self._get_non_empty_string(cmd.params, "target_skill")
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

        actor_bonus = self._coerce_optional_int(cmd.params.get("actor_bonus"))
        if "actor_bonus" in cmd.params and actor_bonus is None:
            return ValidationResult(ok=False, reason="actor_bonus must be an integer")
        target_bonus = self._coerce_optional_int(cmd.params.get("target_bonus"))
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
        roll_result, all_rolls, dice = self._resolve_roll(
            advantage=bool(cmd.params.get("advantage", False)),
            disadvantage=bool(cmd.params.get("disadvantage", False)),
        )
        modifier = state.player.get_skill_bonus(skill)
        total = roll_result + modifier
        return ExecuteResult(
            success=True,
            rolls=[
                self._build_dice_roll(
                    purpose="skill_check",
                    dice=dice,
                    result=roll_result,
                    modifier_name="skill_bonus",
                    modifier_value=modifier,
                    total=total,
                )
            ],
            narrative_hints=self._critical_hints(roll_result),
            time_cost=1.0 / 6.0,
            metadata={
                "handler": "skill_check",
                "command": cmd.type,
                "passed": total >= dc,
                "dc": dc,
                "raw_roll": roll_result,
                "selected_roll": roll_result,
                "all_rolls": list(all_rolls),
                "modifier": modifier,
                "total": total,
            },
        )

    def _compute_saving_throw(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        ability = str(cmd.params["ability"]).strip()
        dc = int(cmd.params["dc"])
        roll_result, all_rolls, dice = self._resolve_roll(
            advantage=bool(cmd.params.get("advantage", False)),
            disadvantage=bool(cmd.params.get("disadvantage", False)),
        )
        modifier = state.player.get_modifier(ability) + state.player.proficiency_bonus
        total = roll_result + modifier
        return ExecuteResult(
            success=True,
            rolls=[
                self._build_dice_roll(
                    purpose="saving_throw",
                    dice=dice,
                    result=roll_result,
                    modifier_name="save_bonus",
                    modifier_value=modifier,
                    total=total,
                )
            ],
            narrative_hints=self._critical_hints(roll_result),
            metadata={
                "handler": "skill_check",
                "command": cmd.type,
                "passed": total >= dc,
                "dc": dc,
                "raw_roll": roll_result,
                "selected_roll": roll_result,
                "all_rolls": list(all_rolls),
                "modifier": modifier,
                "total": total,
            },
        )

    def _compute_contest(
        self,
        cmd: Command,
        state: StateContainer,
    ) -> ExecuteResult:
        actor_skill = str(cmd.params["actor_skill"]).strip()
        target_skill = str(cmd.params["target_skill"]).strip()

        actor_bonus = self._coerce_optional_int(cmd.params.get("actor_bonus"))
        if actor_bonus is None:
            actor_bonus = state.player.get_skill_bonus(actor_skill)

        target_bonus = self._coerce_optional_int(cmd.params.get("target_bonus"))
        if target_bonus is None:
            target_bonus = state.player.get_skill_bonus(target_skill)

        actor_roll, actor_rolls, actor_dice = self._resolve_roll(
            advantage=bool(cmd.params.get("actor_advantage", False)),
            disadvantage=bool(cmd.params.get("actor_disadvantage", False)),
        )
        target_roll, target_rolls, target_dice = self._resolve_roll(
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
            success=True,
            rolls=[
                self._build_dice_roll(
                    purpose="contest_actor",
                    dice=actor_dice,
                    result=actor_roll,
                    modifier_name="actor_bonus",
                    modifier_value=actor_bonus,
                    total=actor_total,
                ),
                self._build_dice_roll(
                    purpose="contest_target",
                    dice=target_dice,
                    result=target_roll,
                    modifier_name="target_bonus",
                    modifier_value=target_bonus,
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

    def _resolve_roll(
        self,
        *,
        advantage: bool,
        disadvantage: bool,
    ) -> tuple[int, list[int], str]:
        if advantage and not disadvantage:
            rolls = [self._roll_d20(), self._roll_d20()]
            return max(rolls), rolls, "2d20kh1"
        if disadvantage and not advantage:
            rolls = [self._roll_d20(), self._roll_d20()]
            return min(rolls), rolls, "2d20kl1"
        roll = self._roll_d20()
        return roll, [roll], "1d20"

    def _roll_d20(self) -> int:
        return random.randint(1, 20)

    @staticmethod
    def _build_dice_roll(
        *,
        purpose: str,
        dice: str,
        result: int,
        modifier_name: str,
        modifier_value: int,
        total: int,
    ) -> DiceRoll:
        if result == 20:
            critical: bool | None = True
        elif result == 1:
            critical = False
        else:
            critical = None
        return DiceRoll(
            purpose=purpose,
            dice=dice,
            result=result,
            modifiers=[{"name": modifier_name, "value": modifier_value}],
            total=total,
            critical=critical,
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
    def _get_non_empty_string(params: Mapping[str, Any], key: str) -> str | None:
        value = params.get(key)
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _get_optional_non_empty_string(value: Any) -> str | None:
        if value is None:
            return None
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

    @classmethod
    def _coerce_optional_int(cls, value: Any) -> int | None:
        if value is None:
            return None
        return cls._coerce_int(value)
