"""Hostile Area 敌对区域进入 handler."""

from __future__ import annotations

from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.handler_utils import (
    build_dice_roll,
    coerce_int,
    handler_success,
    resolve_roll,
)
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer


class HostileAreaHandler(StaticCommandHandler):
    COMMAND_TYPES = ("enter_hostile",)

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        del world
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice is required")
        if not state.has_slice("areas"):
            return ValidationResult(ok=False, reason="areas slice is required")
        sub_area_id = self._non_empty_string(cmd.params.get("sub_area_id"))
        if sub_area_id is None:
            return ValidationResult(ok=False, reason="sub_area_id must be a non-empty string")
        payload = state.areas.get_hostile_state(sub_area_id)
        if payload is None:
            return ValidationResult(ok=False, reason=f"unknown hostile sub area: {sub_area_id}")
        if bool(payload.get("cleared", False)):
            return ValidationResult(ok=False, reason="hostile sub area is already cleared")
        if bool(payload.get("combat_active", False)):
            return ValidationResult(ok=False, reason="hostile sub area is already in combat")
        area_id = self._non_empty_string(payload.get("area_id"))
        current_area = self._non_empty_string(state.player.current_area)
        if current_area is not None and area_id is not None and current_area != area_id:
            return ValidationResult(ok=False, reason="hostile sub area is not in the current area")
        current_location = self._non_empty_string(state.player.current_location)
        if current_location != sub_area_id:
            return ValidationResult(
                ok=False,
                reason="player must already be inside the hostile sub area",
            )
        status = self._non_empty_string(payload.get("status")) or "spotted"
        if status != "spotted":
            return ValidationResult(
                ok=False,
                reason="hostile sub area is not ready for entry resolution",
            )
        return ValidationResult(ok=True)

    def compute(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        del world
        sub_area_id = str(cmd.params["sub_area_id"]).strip()
        payload = state.areas.get_hostile_state(sub_area_id)
        if payload is None:
            return ExecuteResult.error(f"unknown hostile sub area: {sub_area_id}")

        threat_level = self._non_empty_string(payload.get("threat_level")) or "moderate"
        stealth_dc = coerce_int(payload.get("stealth_dc"))
        if stealth_dc is None:
            stealth_dc = self._default_stealth_dc(threat_level)

        period = state.time.period if state.has_slice("time") else "day"
        advantage = period in {"dusk", "night"}
        disadvantage = False
        roll_result, all_rolls, dice = resolve_roll(
            advantage=advantage,
            disadvantage=disadvantage,
        )
        modifier = state.player.get_skill_bonus("stealth")
        total = roll_result + modifier
        success = total >= stealth_dc
        blocking = bool(payload.get("blocking", False))
        options: list[dict[str, str]] = []
        if success:
            options.append({"action": "surprise_attack", "label": "发动突袭"})
            if not blocking:
                options.append({"action": "sneak_through", "label": "潜行通过"})
            options.append({"action": "retreat", "label": "撤退"})

        if success:
            narrative = "你借着阴影悄然逼近，敌人尚未发现你的踪迹。"
            surprise_state = "player_surprise"
            status = "stealth_resolved"
        else:
            narrative = "你踩到碎石发出动静，敌人立刻警觉起来。"
            surprise_state = "enemy_surprise" if total <= stealth_dc - 5 else "none"
            status = "combat_pending"

        updated_payload = state.areas.copy_hostile_state(payload)
        updated_payload["stealth_dc"] = stealth_dc
        updated_payload["status"] = status
        updated_payload["entry_mode"] = "entered"
        updated_payload["last_stealth_result"] = {
            "success": success,
            "roll": roll_result,
            "dc": stealth_dc,
            "modifier": modifier,
            "advantage": advantage,
            "disadvantage": disadvantage,
            "narrative": narrative,
            "options": options,
            "surprise_state": surprise_state,
        }

        return handler_success(
            "hostile_area",
            "enter_hostile",
            changes=[
                StateChange(
                    "areas",
                    "modify",
                    f"hostile_tracking.{sub_area_id}",
                    updated_payload,
                )
            ],
            metadata={
                "status": status,
                "sub_area_id": sub_area_id,
                "area_id": self._non_empty_string(updated_payload.get("area_id")) or "",
                "success": success,
                "roll": roll_result,
                "all_rolls": list(all_rolls),
                "dc": stealth_dc,
                "modifier": modifier,
                "advantage": advantage,
                "disadvantage": disadvantage,
                "narrative": narrative,
                "options": options,
                "surprise_state": surprise_state,
                "blocking": blocking,
            },
            rolls=[
                build_dice_roll(
                    purpose="stealth",
                    dice=dice,
                    result=roll_result,
                    modifiers=[{"name": "stealth", "value": modifier}],
                    total=total,
                )
            ],
        )

    @staticmethod
    def _non_empty_string(value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _default_stealth_dc(threat_level: str) -> int:
        return {
            "easy": 10,
            "moderate": 12,
            "hard": 14,
            "deadly": 16,
        }.get(threat_level, 12)
