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
    COMMAND_TYPES = ("investigate_clue", "resolve_clue_option", "apply_clue_check_result")

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
        if cmd.type == "apply_clue_check_result":
            grade = get_non_empty_string(cmd.params, "grade")
            if grade is None:
                return ValidationResult(ok=False, reason="grade required")
            if grade not in {"excellent", "good", "poor", "bad"}:
                return ValidationResult(ok=False, reason=f"invalid grade: {grade}")
            # Verify pending_check exists in interactable state
            interactable_id = get_non_empty_string(cmd.params, "interactable_id") or ""
            if state.has_slice("areas"):
                area_id = state.player.current_area if state.has_slice("player") else None
                if area_id:
                    istate = (
                        state.areas.get_area(area_id).interactable_states.get(interactable_id, {})
                    )
                    if not isinstance(istate, dict) or "pending_check" not in istate:
                        return ValidationResult(ok=False, reason="no_pending_check")
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
        if cmd.type == "apply_clue_check_result":
            return self._compute_apply_check_result(cmd, state, world)
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

        # Dispatch to simplified-schema handler if appropriate
        if clue.get("simple"):
            return self._compute_investigate_simple(entry, clue, clue_state, state, world)

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

    def _compute_investigate_simple(
        self,
        entry: Any,
        clue: dict[str, Any],
        clue_state: dict[str, Any],
        state: StateContainer,
        world: Any,
    ) -> ExecuteResult:
        """Handle investigate_clue for the simplified schema (base_effects + optional check)."""
        area_id = entry.area_id
        changes: list[StateChange] = []

        # 1. Apply base_effects unconditionally
        base_effects = list(clue.get("base_effects", []))
        effect_changes, effect_summary, error = apply_clue_effects(
            state,
            world,
            base_effects,
            area_id=area_id,
            source=entry.source,
        )
        if error is not None:
            return ExecuteResult.error(error)
        changes.extend(effect_changes)

        # 2. Store pending_check if a check is defined
        check = clue.get("check")
        has_check = isinstance(check, dict) and bool(check.get("skill"))
        updated_state = dict(clue_state)
        updated_state.update({
            "area_id": area_id,
            "clue_id": clue["clue_id"],
            "first_inspected": True,
            # Mark as resolved with sentinel so clue_investigated condition works
            "resolved_option_id": "__simple__",
        })
        if has_check:
            updated_state["pending_check"] = {
                "skill": check["skill"],
                "dc": check["dc"],
                "check_effects": list(clue.get("check_effects", [])),
            }
        else:
            # Clear any stale pending_check
            updated_state.pop("pending_check", None)

        changes.append(StateChange(
            "areas",
            "set",
            f"interactable_states.{entry.interactable_id}",
            updated_state,
        ))

        # 3. Inject narrative into area_events and scene_bus if present
        narrative = str(clue.get("narrative") or "").strip()
        current_tick = state.time.absolute_tick() if state.has_slice("time") else 0
        if narrative:
            changes.append(StateChange(
                "areas",
                "add",
                f"{area_id}.area_events",
                {
                    "tick": current_tick,
                    "event": narrative[:200],
                    "source": "clue_narrative",
                    "severity": "minor",
                },
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
                "narrative": narrative,
                "has_check": has_check,
                "check": dict(check) if has_check else None,
                "base_effects_applied": effect_summary,
                "party_prompt_hints": list(clue.get("party_prompt_hints", [])),
                "source": entry.source,
                "dynamic": entry.dynamic,
                "overlay": entry.overlay,
                "room_id": entry.room_id,
                "location_id": entry.location_id,
                "area_id": entry.area_id,
                "functional_type": CLUE_FUNCTIONAL_TYPE,
                "simple": True,
            },
            omit_empty_delta=False,
        )

    def _compute_apply_check_result(
        self,
        cmd: Command,
        state: StateContainer,
        world: Any,
    ) -> ExecuteResult:
        """Apply check_effects from a previously stored pending_check based on grade."""
        interactable_id = get_non_empty_string(cmd.params, "interactable_id") or ""
        grade = get_non_empty_string(cmd.params, "grade") or "bad"

        area_id = state.player.current_area if state.has_slice("player") else None
        if not area_id:
            return ExecuteResult.error("no_current_area")

        clue_state = (
            state.areas.get_area(area_id).interactable_states.get(interactable_id, {})
            if state.has_slice("areas")
            else {}
        )
        if not isinstance(clue_state, dict):
            clue_state = {}

        pending_check = clue_state.get("pending_check")
        if not isinstance(pending_check, dict):
            return ExecuteResult.error("no_pending_check")

        check_effects = list(pending_check.get("check_effects", []))
        changes: list[StateChange] = []
        effect_summary: dict[str, Any] = {"applied": []}

        # Execute check_effects for excellent/good; skip for poor/bad
        if grade in {"excellent", "good"} and check_effects:
            effect_changes, effect_summary, error = apply_clue_effects(
                state,
                world,
                check_effects,
                area_id=area_id,
                source="system",
            )
            if error is not None:
                return ExecuteResult.error(error)
            changes.extend(effect_changes)

        # Clear pending_check from interactable state
        updated_state = dict(clue_state)
        updated_state.pop("pending_check", None)
        updated_state["check_grade"] = grade
        updated_state["check_passed"] = grade in {"excellent", "good"}
        changes.append(StateChange(
            "areas",
            "set",
            f"interactable_states.{interactable_id}",
            updated_state,
        ))

        return handler_success(
            "clue",
            "apply_clue_check_result",
            changes=changes,
            time_cost=0.0,
            metadata={
                "interactable_id": interactable_id,
                "grade": grade,
                "passed": grade in {"excellent", "good"},
                "check_effects_applied": effect_summary,
                "area_id": area_id,
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
        passed: bool | None = None if isinstance(check, dict) else True
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

        computed_effect_types = effect_types_for_option(clue, option_id, passed=passed)
        clue_name = str(clue.get("name") or entry.name or clue["clue_id"]).strip()
        option_label = str(option.get("label") or option_id).strip()
        raw_outcome_text = clue.get("raw_outcome_texts", {}).get(option_id)
        if isinstance(raw_outcome_text, str) and raw_outcome_text.strip():
            outcome_text = raw_outcome_text.strip()
        else:
            outcome_text = _derive_outcome_text(
                clue_name=clue_name,
                option_label=option_label,
                effect_types=computed_effect_types,
                passed=passed,
            )
        effects_applied = [
            str(item.get("type", ""))
            for item in effect_summary.get("applied", [])
            if isinstance(item, dict) and item.get("type")
        ]

        changes: list[StateChange] = list(effect_changes)
        current_tick = state.time.absolute_tick() if state.has_slice("time") else 0
        updated_state = dict(clue_state)
        updated_state.update({
            "area_id": area_id,
            "clue_id": clue["clue_id"],
            "first_inspected": True,
            "resolved_option_id": option_id,
            "outcome_text": outcome_text,
            "check_passed": passed,
            "effects_applied": effects_applied,
        })
        if state.has_slice("time"):
            updated_state["resolved_at_tick"] = current_tick
        changes.append(StateChange(
            "areas",
            "set",
            f"interactable_states.{entry.interactable_id}",
            updated_state,
        ))

        # Write area event so NPC/Planner can see what was investigated.
        changes.append(StateChange(
            "areas",
            "add",
            f"{area_id}.area_events",
            {
                "tick": current_tick,
                "event": f"调查了{clue_name}：{outcome_text[:80]}",
                "source": "clue_investigation",
                "severity": "minor",
            },
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
            "clue_name": clue_name,
            "option_id": option_id,
            "option_label": option.get("label"),
            "passed": passed,
            "applied_effects": effect_summary.get("applied", []),
            "effect_types": computed_effect_types,
            "outcome_text": outcome_text,
            "topic": clue.get("topic"),
            "removed_from_scene": removed_from_scene,
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


def _derive_outcome_text(
    *,
    clue_name: str,
    option_label: str,
    effect_types: list[str],
    passed: bool | None,
) -> str:
    """Generate a deterministic human-readable outcome summary for a clue resolution.

    This text is stored in state (interactable_states) and in metadata so that
    agent_orchestration fallback comments can use actual content instead of a
    generic template.
    """
    if "unlock_sub_location" in effect_types:
        return f"{option_label}让{clue_name}终于露出了一条能追下去的路。"
    if "advance_quest" in effect_types:
        return f"{option_label}把{clue_name}钉进了更清楚的方向，事情往前走了一步。"
    if passed is False:
        return f"{option_label}没能把{clue_name}彻底掰开，但至少排掉了一条岔路。"
    return f"{option_label}暂时替{clue_name}定住了一个方向。"


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
