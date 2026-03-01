"""Spell concentration helpers for SpellHandler."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.rules.handler_utils import coerce_non_empty_string, handler_success, handler_success_no_delta
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateChange, StateContainer

from app.game_core.rules.handlers.spell_resolver import normalize_mapping, validate_character_identity

COMBAT_TARGET_KIND = "combat_participant"


def validate_break_concentration(
    cmd: Command,
    state: StateContainer,
) -> ValidationResult:
    identity_check = validate_character_identity(cmd.params, state)
    if identity_check is not None:
        return identity_check
    return ValidationResult(ok=True)


def compute_break_concentration(
    state: StateContainer,
) -> ExecuteResult:
    concentration = normalize_mapping(state.player.concentration)
    if not concentration:
        return handler_success_no_delta(
            "spell",
            "break_concentration",
            metadata={
                "status": "noop",
                "spell_id": None,
                "removed_effect_count": 0,
            },
        )

    updated_effects, player_changed, target_payloads, removed_count, spell_id = clear_concentration_effects(
        state,
        concentration,
    )
    changes = [
        StateChange("player", "set", "concentration", None),
    ]
    if player_changed:
        changes.append(StateChange("player", "set", "active_effects", updated_effects))
    changes.extend(hostile_changes(target_payloads))

    return handler_success(
        "spell",
        "break_concentration",
        changes=changes,
        metadata={
            "status": "broken",
            "spell_id": spell_id,
            "removed_effect_count": removed_count,
        },
        omit_empty_delta=False,
    )


def break_existing_concentration(
    current_effects: list[dict[str, Any]],
    concentration_payload: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], int, str | None]:
    spell_id = coerce_non_empty_string(concentration_payload.get("spell_id"))
    raw_applied = concentration_payload.get("applied_effects")
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
        instance_id = coerce_non_empty_string(effect.get("instance_id"))
        if instance_id is not None and instance_id in applied_effects:
            removed_count += 1
            continue
        if not applied_effects and bool(effect.get("from_concentration")):
            if spell_id and coerce_non_empty_string(effect.get("source_spell_id")) == spell_id:
                removed_count += 1
                continue
        updated_effects.append(dict(effect))
    return (updated_effects, removed_count, spell_id)


def clear_concentration_effects(
    state: StateContainer,
    concentration_payload: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], bool, dict[str, dict[str, Any]], int, str | None]:
    updated_effects, removed_player_count, spell_id = break_existing_concentration(
        state.player.get_active_effects(),
        concentration_payload,
    )
    target_payloads, removed_target_count = clear_concentration_target_refs(
        state,
        concentration_payload,
    )
    return (
        updated_effects,
        removed_player_count > 0,
        target_payloads,
        removed_player_count + removed_target_count,
        spell_id,
    )


def clear_concentration_target_refs(
    state: StateContainer,
    concentration_payload: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], int]:
    raw_target_refs = concentration_payload.get("target_refs")
    if not isinstance(raw_target_refs, list):
        return ({}, 0)

    spell_id = coerce_non_empty_string(concentration_payload.get("spell_id"))
    updates: dict[str, dict[str, Any]] = {}
    removed_count = 0
    for raw_ref in raw_target_refs:
        if not isinstance(raw_ref, Mapping):
            continue
        kind = coerce_non_empty_string(raw_ref.get("kind"))
        if kind != COMBAT_TARGET_KIND:
            continue
        sub_area_id = coerce_non_empty_string(raw_ref.get("sub_area_id"))
        participant_monster_id = coerce_non_empty_string(
            raw_ref.get("participant_monster_id")
        )
        if sub_area_id is None or participant_monster_id is None:
            continue
        payload = updates.get(sub_area_id)
        if payload is None:
            payload = get_hostile_payload(state, sub_area_id)
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
        updated_target_effects, target_removed = remove_effect_instances(
            state.areas.participant_effects(participant),
            normalize_effect_ids(raw_ref.get("effect_ids")),
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


def remove_effect_instances(
    current_effects: list[dict[str, Any]],
    effect_ids: set[str],
    spell_id: str | None,
) -> tuple[list[dict[str, Any]], int]:
    updated_effects: list[dict[str, Any]] = []
    removed_count = 0
    for effect in current_effects:
        instance_id = coerce_non_empty_string(effect.get("instance_id"))
        remove = False
        if instance_id is not None and instance_id in effect_ids:
            remove = True
        elif not effect_ids and spell_id and bool(effect.get("from_concentration")):
            if coerce_non_empty_string(effect.get("source_spell_id")) == spell_id:
                remove = True
        if remove:
            removed_count += 1
            continue
        updated_effects.append(dict(effect))
    return (updated_effects, removed_count)


def hostile_changes(payloads: Mapping[str, Mapping[str, Any]]) -> list[StateChange]:
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


def get_hostile_payload(
    state: StateContainer,
    sub_area_id: str,
) -> dict[str, Any] | None:
    if not state.has_slice("areas"):
        return None
    payload = state.areas.get_hostile_state(sub_area_id)
    if isinstance(payload, Mapping):
        return state.areas.copy_hostile_state(payload)
    return None


def normalize_effect_ids(raw_effect_ids: Any) -> set[str]:
    if not isinstance(raw_effect_ids, list):
        return set()
    return {
        str(item)
        for item in raw_effect_ids
        if str(item).strip()
    }
