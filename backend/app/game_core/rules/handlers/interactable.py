"""InteractableHandler — interact_object_v2 command (multi-check-path interactables)."""

from __future__ import annotations

import random
from typing import Any

from app.game_core.content import WorldInstance
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.handler_utils import (
    build_dice_roll,
    coerce_int,
    get_non_empty_string,
    handler_success,
    handler_success_no_delta,
)
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer


class InteractableHandler(StaticCommandHandler):
    COMMAND_TYPES = ("interact_object_v2",)

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice required")
        if not world.has_registry("maps"):
            return ValidationResult(ok=False, reason="maps registry required")
        interactable_id = get_non_empty_string(cmd.params, "interactable_id")
        if interactable_id is None:
            return ValidationResult(ok=False, reason="interactable_id required")
        return ValidationResult(ok=True)

    def compute(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        interactable_id = get_non_empty_string(cmd.params, "interactable_id") or ""
        check_index = coerce_int(cmd.params.get("check_index")) or 0

        area_id = state.player.current_area or ""
        location_id = state.player.current_location

        if not location_id:
            return ExecuteResult.error("not_in_sub_location")

        sub_loc = world.maps.get_sub_location(area_id, location_id)
        if sub_loc is not None:
            template = next(
                (i for i in sub_loc.interactables if i.id == interactable_id),
                None,
            )
        else:
            template = None

        if template is None and state.has_slice("areas"):
            # Fallback: dynamic sub-area created by plant_environmental
            dyn = _find_dynamic_interactable(state, area_id, location_id, interactable_id)
            if dyn is not None:
                return _compute_dynamic(
                    dyn, interactable_id, check_index, area_id, location_id, state
                )

        if template is None:
            return ExecuteResult.error(f"interactable_not_found: {interactable_id}")

        # One-time check
        if template.one_time and state.has_slice("areas"):
            if state.areas.is_interactable_used(area_id, interactable_id):
                return ExecuteResult.error("already_used")

        # No checks needed (inspect-type, always succeeds)
        if not template.checks:
            changes: list[StateChange] = []
            if template.one_time and state.has_slice("areas"):
                changes.append(StateChange(
                    "areas", "set",
                    f"interactable_states.{interactable_id}",
                    {"area_id": area_id, "used": True},
                ))
            return handler_success(
                "interactable", "interact_object_v2",
                changes=changes,
                time_cost=1.0 / 6.0,
                metadata={
                    "passed": True,
                    "interactable_id": interactable_id,
                    "area_id": area_id,
                    "location_id": location_id,
                    "reward": _reward_payload(template.reward),
                    "check_path": None,
                },
                omit_empty_delta=False,
            )

        # Resolve check path
        if check_index < 0 or check_index >= len(template.checks):
            check_index = 0
        check_path = template.checks[check_index]

        skill = check_path.skill or "perception"
        dc = check_path.dc

        try:
            modifier = state.player.get_skill_bonus(skill)
        except (KeyError, TypeError):
            modifier = 0

        raw_roll = random.randint(1, 20)
        total = raw_roll + modifier
        passed = total >= dc

        dice_roll = build_dice_roll(
            purpose=f"interact_{interactable_id}",
            dice="1d20",
            result=raw_roll,
            modifiers=[{"name": skill, "value": modifier}],
            total=total,
        )

        base_meta: dict[str, Any] = {
            "passed": passed,
            "interactable_id": interactable_id,
            "area_id": area_id,
            "location_id": location_id,
            "skill": skill,
            "roll": raw_roll,
            "modifier": modifier,
            "total": total,
            "dc": dc,
            "check_path": check_index,
        }

        if not passed:
            fail_meta = dict(base_meta)
            fail_consequence = getattr(check_path, "fail_consequence", None)
            if fail_consequence:
                fail_meta["fail_consequence"] = str(fail_consequence)
            return handler_success_no_delta(
                "interactable", "interact_object_v2",
                time_cost=1.0 / 6.0,
                rolls=[dice_roll],
                metadata=fail_meta,
            )

        success_changes: list[StateChange] = []
        if template.one_time and state.has_slice("areas"):
            success_changes.append(StateChange(
                "areas", "set",
                f"interactable_states.{interactable_id}",
                {"area_id": area_id, "used": True},
            ))

        success_meta = dict(base_meta)
        success_meta["reward"] = _reward_payload(template.reward)

        return handler_success(
            "interactable", "interact_object_v2",
            changes=success_changes,
            time_cost=1.0 / 6.0,
            rolls=[dice_roll],
            metadata=success_meta,
            omit_empty_delta=False,
        )


def _reward_payload(reward: Any) -> dict[str, Any]:
    if reward is None:
        return {}
    if isinstance(reward, dict):
        return dict(reward)
    return {}


def _find_dynamic_interactable(
    state: StateContainer,
    area_id: str,
    location_id: str,
    interactable_id: str,
) -> dict[str, Any] | None:
    """Search temporary_sub_areas for a sub-area matching location_id, then find the interactable."""
    for sub_area in state.areas.list_temporary_sub_areas(area_id):
        if not isinstance(sub_area, dict):
            continue
        if str(sub_area.get("id", "")) != location_id:
            continue
        for ia in sub_area.get("interactables", []):
            if isinstance(ia, dict) and str(ia.get("id", "")) == interactable_id:
                return ia
    return None


def _compute_dynamic(
    ia_dict: dict[str, Any],
    interactable_id: str,
    check_index: int,
    area_id: str,
    location_id: str,
    state: StateContainer,
) -> ExecuteResult:
    """Execute an interact_object_v2 command against a raw dict interactable (dynamic sub-area)."""
    one_time = bool(ia_dict.get("one_time", False))

    # One-time check
    if one_time and state.has_slice("areas"):
        if state.areas.is_interactable_used(area_id, interactable_id):
            return ExecuteResult.error("already_used")

    checks_raw = ia_dict.get("checks", [])
    if not isinstance(checks_raw, list):
        checks_raw = []

    # No checks — inspect-type, always succeeds
    if not checks_raw:
        changes: list[StateChange] = []
        if one_time and state.has_slice("areas"):
            changes.append(StateChange(
                "areas", "set",
                f"interactable_states.{interactable_id}",
                {"area_id": area_id, "used": True},
            ))
        return handler_success(
            "interactable", "interact_object_v2",
            changes=changes,
            time_cost=1.0 / 6.0,
            metadata={
                "passed": True,
                "interactable_id": interactable_id,
                "area_id": area_id,
                "location_id": location_id,
                "reward": _reward_payload(ia_dict.get("reward")),
                "check_path": None,
                "dynamic": True,
            },
            omit_empty_delta=False,
        )

    # Resolve check path
    if check_index < 0 or check_index >= len(checks_raw):
        check_index = 0
    check_path = checks_raw[check_index]
    if not isinstance(check_path, dict):
        check_path = {}

    skill = str(check_path.get("skill") or "perception")
    dc_raw = check_path.get("dc")
    try:
        dc = int(dc_raw) if dc_raw is not None else 10
    except (TypeError, ValueError):
        dc = 10

    try:
        modifier = state.player.get_skill_bonus(skill)
    except (KeyError, TypeError):
        modifier = 0

    raw_roll = random.randint(1, 20)
    total = raw_roll + modifier
    passed = total >= dc

    dice_roll = build_dice_roll(
        purpose=f"interact_{interactable_id}",
        dice="1d20",
        result=raw_roll,
        modifiers=[{"name": skill, "value": modifier}],
        total=total,
    )

    base_meta: dict[str, Any] = {
        "passed": passed,
        "interactable_id": interactable_id,
        "area_id": area_id,
        "location_id": location_id,
        "skill": skill,
        "roll": raw_roll,
        "modifier": modifier,
        "total": total,
        "dc": dc,
        "check_path": check_index,
        "dynamic": True,
    }

    if not passed:
        fail_meta = dict(base_meta)
        fail_consequence = check_path.get("fail_consequence")
        if fail_consequence:
            fail_meta["fail_consequence"] = str(fail_consequence)
        return handler_success_no_delta(
            "interactable", "interact_object_v2",
            time_cost=1.0 / 6.0,
            rolls=[dice_roll],
            metadata=fail_meta,
        )

    success_changes: list[StateChange] = []
    if one_time and state.has_slice("areas"):
        success_changes.append(StateChange(
            "areas", "set",
            f"interactable_states.{interactable_id}",
            {"area_id": area_id, "used": True},
        ))

    success_meta = dict(base_meta)
    success_meta["reward"] = _reward_payload(ia_dict.get("reward"))

    return handler_success(
        "interactable", "interact_object_v2",
        changes=success_changes,
        time_cost=1.0 / 6.0,
        rolls=[dice_roll],
        metadata=success_meta,
        omit_empty_delta=False,
    )
