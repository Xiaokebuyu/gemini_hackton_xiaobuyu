"""Clue investigation handlers."""

from __future__ import annotations

import random
from typing import Any

from app.game_core.clue_investigation import (
    CLUE_FUNCTIONAL_TYPE,
    apply_clue_effects,
    clue_option_by_id,
    effect_types_for_option,
    normalize_clue_definition,
    select_option_effects,
    validate_clue_definition,
)
from app.game_core.environment_access import find_current_interactable
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.handler_utils import (
    build_dice_roll,
    get_non_empty_string,
    handler_success,
)
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer


class ClueHandler(StaticCommandHandler):
    COMMAND_TYPES = ("investigate_clue", "resolve_clue_option")

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: Any,
    ) -> ValidationResult:
        if not state.has_slice("player"):
            return ValidationResult(ok=False, reason="player slice required")
        if not state.has_slice("areas"):
            return ValidationResult(ok=False, reason="areas slice required")
        interactable_id = get_non_empty_string(cmd.params, "interactable_id")
        if interactable_id is None:
            return ValidationResult(ok=False, reason="interactable_id required")
        if cmd.type == "resolve_clue_option":
            option_id = get_non_empty_string(cmd.params, "option_id")
            if option_id is None:
                return ValidationResult(ok=False, reason="option_id required")
        return ValidationResult(ok=True)

    def compute(
        self,
        cmd: Command,
        state: StateContainer,
        world: Any,
    ) -> ExecuteResult:
        validation = self.validate(cmd, state, world)
        if not validation.ok:
            return ExecuteResult.error(validation.reason or "validation failed")
        if cmd.type == "investigate_clue":
            return self._compute_investigate(cmd, state, world)
        if cmd.type == "resolve_clue_option":
            return self._compute_resolve(cmd, state, world)
        return ExecuteResult.error(f"unsupported command: {cmd.type}")

    def _compute_investigate(
        self,
        cmd: Command,
        state: StateContainer,
        world: Any,
    ) -> ExecuteResult:
        resolved = _resolve_current_clue(state, world, cmd)
        if isinstance(resolved, ExecuteResult):
            return resolved
        entry, clue, clue_state = resolved

        if clue_state.get("resolved_option_id"):
            return ExecuteResult.error("clue_already_resolved")

        area_id = entry.area_id
        changes: list[StateChange] = []
        effect_summary = {"applied": []}
        first_inspect_applied = False
        if not bool(clue_state.get("first_inspected", False)):
            effect_changes, effect_summary, error = apply_clue_effects(
                state,
                world,
                list(clue.get("on_first_inspect", [])),
                area_id=area_id,
                source=entry.source,
            )
            if error is not None:
                return ExecuteResult.error(error)
            changes.extend(effect_changes)
            first_inspect_applied = bool(effect_changes or effect_summary.get("applied"))

        updated_state = dict(clue_state)
        updated_state.update({
            "area_id": area_id,
            "clue_id": clue["clue_id"],
            "first_inspected": True,
            "resolved_option_id": None,
        })
        changes.append(StateChange(
            "areas",
            "set",
            f"interactable_states.{entry.interactable_id}",
            updated_state,
        ))

        return handler_success(
            "clue",
            "investigate_clue",
            changes=changes,
            time_cost=1.0 / 6.0,
            metadata={
                "clue_id": clue["clue_id"],
                "interactable_id": entry.interactable_id,
                "clue_name": clue.get("name") or entry.name,
                "description": clue.get("description") or entry.description,
                "topic": clue.get("topic"),
                "linked_quest_id": clue.get("linked_quest_id"),
                "linked_milestone": clue.get("linked_milestone"),
                "options": [dict(option) for option in clue.get("options", [])],
                "party_prompt_hints": list(clue.get("party_prompt_hints", [])),
                "first_inspect_applied": first_inspect_applied,
                "first_inspect_result": effect_summary,
                "source": entry.source,
                "dynamic": entry.dynamic,
                "overlay": entry.overlay,
                "room_id": entry.room_id,
                "location_id": entry.location_id,
                "area_id": entry.area_id,
                "functional_type": CLUE_FUNCTIONAL_TYPE,
            },
            omit_empty_delta=False,
        )

    def _compute_resolve(
        self,
        cmd: Command,
        state: StateContainer,
        world: Any,
    ) -> ExecuteResult:
        resolved = _resolve_current_clue(state, world, cmd)
        if isinstance(resolved, ExecuteResult):
            return resolved
        entry, clue, clue_state = resolved

        option_id = get_non_empty_string(cmd.params, "option_id") or ""
        option = clue_option_by_id(clue, option_id)
        if option is None:
            return ExecuteResult.error("unknown_clue_option")
        if clue_state.get("resolved_option_id"):
            return ExecuteResult.error("clue_already_resolved")

        area_id = entry.area_id
        check = option.get("check") if isinstance(option, dict) else None
        passed: bool | None = None
        rolls = []
        if isinstance(check, dict):
            skill = str(check.get("skill") or "").strip() or "investigation"
            try:
                dc = int(check.get("dc"))
            except (TypeError, ValueError):
                return ExecuteResult.error("invalid_clue_option_check")
            try:
                modifier = state.player.get_skill_bonus(skill)
            except (KeyError, TypeError):
                modifier = 0
            raw_roll = random.randint(1, 20)
            total = raw_roll + modifier
            passed = total >= dc
            rolls.append(build_dice_roll(
                purpose=f"clue_{entry.interactable_id}_{option_id}",
                dice="1d20",
                result=raw_roll,
                modifiers=[{"name": skill, "value": modifier}],
                total=total,
            ))
        effect_changes, effect_summary, error = apply_clue_effects(
            state,
            world,
            select_option_effects(clue, option_id, passed=passed),
            area_id=area_id,
            source=entry.source,
        )
        if error is not None:
            return ExecuteResult.error(error)

        changes: list[StateChange] = list(effect_changes)
        updated_state = dict(clue_state)
        updated_state.update({
            "area_id": area_id,
            "clue_id": clue["clue_id"],
            "first_inspected": True,
            "resolved_option_id": option_id,
        })
        if state.has_slice("time"):
            updated_state["resolved_at_tick"] = state.time.absolute_tick()
        changes.append(StateChange(
            "areas",
            "set",
            f"interactable_states.{entry.interactable_id}",
            updated_state,
        ))

        removed_from_scene = False
        if bool(clue.get("hide_on_resolve", True)) and entry.overlay:
            scoped_entries = state.areas.list_scoped_interactable_overlays(
                area_id,
                entry.location_id,
                entry.room_id,
            )
            filtered_entries = [
                item
                for item in scoped_entries
                if str(item.get("id", "")).strip() != entry.interactable_id
            ]
            if len(filtered_entries) != len(scoped_entries):
                scope_key = state.areas.interactable_scope_key(entry.location_id, entry.room_id)
                changes.append(StateChange(
                    "areas",
                    "set",
                    f"{area_id}.scoped_interactable_overlay.{scope_key}",
                    filtered_entries,
                ))
                removed_from_scene = True

        metadata = {
            "clue_id": clue["clue_id"],
            "interactable_id": entry.interactable_id,
            "clue_name": clue.get("name") or entry.name,
            "option_id": option_id,
            "option_label": option.get("label"),
            "passed": passed,
            "applied_effects": effect_summary.get("applied", []),
            "effect_types": effect_types_for_option(clue, option_id, passed=passed),
            "topic": clue.get("topic"),
            "removed_from_scene": removed_from_scene,
            "linked_quest_id": clue.get("linked_quest_id"),
            "linked_milestone": clue.get("linked_milestone"),
            "source": entry.source,
            "dynamic": entry.dynamic,
            "overlay": entry.overlay,
            "room_id": entry.room_id,
            "location_id": entry.location_id,
            "area_id": entry.area_id,
        }
        if isinstance(check, dict):
            metadata["check"] = {
                "skill": check.get("skill"),
                "dc": check.get("dc"),
            }

        return handler_success(
            "clue",
            "resolve_clue_option",
            changes=changes,
            time_cost=1.0 / 6.0,
            rolls=rolls,
            metadata=metadata,
            omit_empty_delta=False,
        )


def _resolve_current_clue(
    state: StateContainer,
    world: Any,
    cmd: Command,
) -> tuple[Any, dict[str, Any], dict[str, Any]] | ExecuteResult:
    interactable_id = get_non_empty_string(cmd.params, "interactable_id") or ""
    entry, visible = find_current_interactable(state, world, interactable_id)
    if entry is None:
        return ExecuteResult.error("clue_not_available")
    if not visible:
        return ExecuteResult.error("clue_not_revealed")
    clue = normalize_clue_definition(
        entry.functional,
        interactable_id=entry.interactable_id,
        name=entry.name,
        description=entry.description,
    )
    if not clue:
        return ExecuteResult.error("not_a_clue")
    validation_error = validate_clue_definition(clue)
    if validation_error is not None:
        return ExecuteResult.error(f"invalid_clue_definition:{validation_error}")
    clue_state = (
        state.areas.get_area(entry.area_id).interactable_states.get(entry.interactable_id, {})
        if state.has_slice("areas")
        else {}
    )
    if not isinstance(clue_state, dict):
        clue_state = {}
    return entry, clue, clue_state
