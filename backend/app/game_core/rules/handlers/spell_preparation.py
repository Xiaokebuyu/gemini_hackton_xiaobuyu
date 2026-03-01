"""Spell preparation helpers for SpellHandler."""

from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.rules.handler_utils import coerce_non_empty_string, handler_success, handler_success_no_delta
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer

from app.game_core.rules.handlers.spell_resolver import (
    coerce_int,
    get_class_template,
    resolve_spell_level,
    resolve_spell_template,
    validate_character_identity,
)


def validate_prepare_spells(
    cmd: Command,
    state: StateContainer,
    world: WorldInstance,
) -> ValidationResult:
    if not world.has_registry("skills"):
        return ValidationResult(ok=False, reason="skills registry is required")
    identity_check = validate_character_identity(cmd.params, state)
    if identity_check is not None:
        return identity_check

    raw_spell_ids = cmd.params.get("spell_ids")
    if not isinstance(raw_spell_ids, list):
        return ValidationResult(ok=False, reason="spell_ids must be a list")
    normalized_spell_ids: list[str] = []
    for raw_spell_id in raw_spell_ids:
        spell_id = coerce_non_empty_string(raw_spell_id)
        if spell_id is None:
            return ValidationResult(
                ok=False,
                reason="spell_ids must only contain non-empty strings",
            )
        if spell_id not in state.player.known_spells:
            return ValidationResult(ok=False, reason=f"unknown known spell: {spell_id}")
        if resolve_spell_template(spell_id, world) is None:
            return ValidationResult(ok=False, reason=f"invalid spell template: {spell_id}")
        normalized_spell_ids.append(spell_id)

    mode = prepare_mode(state, world)
    if mode == "not_applicable":
        return ValidationResult(ok=True)

    highest_slot_level = _highest_spell_slot_level(state)
    for spell_id in normalized_spell_ids:
        template = resolve_spell_template(spell_id, world)
        if template is None:
            continue
        spell_level = resolve_spell_level(template)
        if spell_level > highest_slot_level:
            return ValidationResult(
                ok=False,
                reason=f"spell exceeds available slot level: {spell_id}",
            )

    max_prepared = resolve_prepared_limit(state, world, mode)
    if max_prepared is None:
        return ValidationResult(ok=False, reason="invalid prepared_formula")
    if len(normalized_spell_ids) > max_prepared:
        return ValidationResult(
            ok=False,
            reason=f"prepared spell count exceeds max: {len(normalized_spell_ids)} > {max_prepared}",
        )
    return ValidationResult(ok=True)


def compute_prepare_spells(
    cmd: Command,
    state: StateContainer,
    world: WorldInstance,
) -> ExecuteResult:
    spell_ids = [str(spell_id).strip() for spell_id in cmd.params.get("spell_ids", [])]
    mode = prepare_mode(state, world)
    if mode == "not_applicable":
        return handler_success_no_delta(
            "spell",
            "prepare_spells",
            metadata={
                "status": "not_applicable",
                "prepared_count": len(spell_ids),
                "max_prepared": len(state.player.known_spells),
                "used_fallback_limit": False,
            },
        )

    max_prepared = resolve_prepared_limit(state, world, mode)
    if max_prepared is None:
        return ExecuteResult.error("invalid prepared_formula")

    return handler_success(
        "spell",
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
        omit_empty_delta=False,
    )


def prepare_mode(
    state: StateContainer,
    world: WorldInstance,
) -> str:
    if not world.has_registry("classes"):
        return "fallback"
    class_template = get_class_template(state, world)
    if class_template is None:
        return "fallback"
    has_formula = coerce_non_empty_string(class_template.prepared_formula) is not None
    has_limit = coerce_int(class_template.prepared_limit) is not None
    if has_formula or has_limit:
        return "prepared"
    return "not_applicable"


def resolve_prepared_limit(
    state: StateContainer,
    world: WorldInstance,
    mode: str,
) -> int | None:
    if mode == "fallback":
        return len(state.player.known_spells)
    class_template = get_class_template(state, world)
    if class_template is None:
        return len(state.player.known_spells)
    explicit_limit = coerce_int(class_template.prepared_limit)
    if explicit_limit is not None:
        return max(0, explicit_limit)
    formula = coerce_non_empty_string(class_template.prepared_formula)
    if formula is None:
        return len(state.player.known_spells)
    return evaluate_prepared_formula(formula, state)


def evaluate_prepared_formula(
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
        value = formula_token_value(token, state)
        if value is None:
            return None
        total += value
    return max(0, total)


def formula_token_value(
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


def _highest_spell_slot_level(state: StateContainer) -> int:
    highest = 0
    for level, slot_state in state.player.spell_slots.items():
        if int(slot_state.get("max", 0)) > 0:
            highest = max(highest, int(level))
    return highest
