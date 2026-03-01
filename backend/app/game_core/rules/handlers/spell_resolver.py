"""Spell handler template and target resolution helpers."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content import WorldInstance
from app.game_core.rules.handler_utils import coerce_non_empty_string
from app.game_core.rules.models import ValidationResult
from app.game_core.state import StateContainer

_SUPPORTED_SELF_TARGETS = frozenset({"self", "player"})


def resolve_spell_template(
    spell_id: str,
    world: WorldInstance,
) -> Any | None:
    if not world.has_registry("skills"):
        return None
    template = world.skills.get(spell_id)
    if template is None:
        return None
    if getattr(template, "category", "") != "spell":
        return None
    return template


def resolve_spell_level(template: Any) -> int:
    level = getattr(template, "spell_level", None)
    if level is not None and isinstance(level, int) and level >= 0:
        return level
    return 0


def resolve_effect_payload(template: Any) -> dict[str, Any]:
    effect = getattr(template, "effect", None)
    if isinstance(effect, Mapping):
        return dict(effect)
    return {}


def resolve_effect_type(
    template: Any,
    effect: Mapping[str, Any],
) -> str:
    value = coerce_non_empty_string(effect.get("type"))
    if value is not None:
        return value
    if read_positive_int(effect, template, "heal_amount", "heal") is not None:
        return "heal"
    if coerce_non_empty_string(effect.get("applies_status")) is not None:
        return "buff"
    applies = getattr(template, "applies_status", None)
    if applies and isinstance(applies, str):
        return "buff"
    return ""


def resolve_spellcasting_ability(
    state: StateContainer,
    world: WorldInstance,
) -> str:
    class_template = get_class_template(state, world)
    ability = (
        coerce_non_empty_string(getattr(class_template, "spellcasting_ability", None))
        if class_template is not None
        else None
    )
    if ability is not None:
        ability = ability.lower()
    if ability is None or ability not in state.player.stats:
        return "int"
    return ability


def resolve_action_type(template: Any) -> str:
    cost = getattr(template, "cost", None)
    if isinstance(cost, Mapping) and cost:
        action_type = coerce_non_empty_string(cost.get("action_type"))
        if action_type is not None:
            return action_type
    return getattr(template, "action_type", "") or "action"


def resolve_resource_cost(template: Any) -> tuple[str | None, int]:
    cost = getattr(template, "cost", None)
    if not isinstance(cost, Mapping) or not cost:
        return (None, 0)
    resource_key = coerce_non_empty_string(cost.get("resource"))
    if resource_key is None:
        return (None, 0)
    amount = coerce_int(cost.get("resource_amount", cost.get("amount", 1)))
    if amount is None or amount < 1:
        amount = 1
    return (resource_key, amount)


def resolve_cast_targets(raw_targets: Any) -> dict[str, Any] | None:
    if raw_targets is None:
        return {"mode": "self", "targets": ["player"]}
    if isinstance(raw_targets, str):
        target = coerce_non_empty_string(raw_targets)
        if target is None:
            return None
        if target.lower() in _SUPPORTED_SELF_TARGETS:
            return {"mode": "self", "targets": ["player"]}
        return {"mode": "combat", "targets": [target]}
    if isinstance(raw_targets, list) and len(raw_targets) == 1:
        target = coerce_non_empty_string(raw_targets[0])
        if target is None:
            return None
        if target.lower() in _SUPPORTED_SELF_TARGETS:
            return {"mode": "self", "targets": ["player"]}
        return {"mode": "combat", "targets": [target]}
    return None


def resolve_combat_target(
    state: StateContainer,
    target_id: str,
) -> tuple[str, dict[str, Any], str, str] | None:
    if not state.has_slice("areas"):
        return None
    area_id = coerce_non_empty_string(state.player.current_area)
    if area_id is None:
        return None
    area = state.areas.areas.get(area_id)
    if area is None:
        return None

    active_combat: list[tuple[str, dict[str, Any]]] = []
    for sub_area_id, payload in area.hostile_tracking.items():
        if not isinstance(payload, Mapping):
            continue
        normalized_payload = state.areas.copy_hostile_state(payload)
        if bool(normalized_payload.get("combat_active")):
            active_combat.append((str(sub_area_id), normalized_payload))
    if len(active_combat) != 1:
        return None

    sub_area_id, payload = active_combat[0]
    participants = state.areas.participant_snapshots(payload)
    if not participants:
        return None
    target_resolution = state.areas.resolve_participant(target_id, participants)
    if target_resolution is None:
        return None
    _, participant = target_resolution
    if not bool(participant.get("alive", False)):
        return None
    return (
        sub_area_id,
        payload,
        state.areas.participant_monster_id(participant),
        state.areas.participant_name(participant),
    )


def validate_character_identity(
    params: Mapping[str, Any],
    state: StateContainer,
    *,
    key: str = "character",
) -> ValidationResult | None:
    raw_value = params.get(key)
    if raw_value is None:
        return None
    identity = coerce_non_empty_string(raw_value)
    if identity is None:
        return ValidationResult(ok=False, reason=f"{key} must be a non-empty string")
    if identity == "player":
        return None
    player_character_id = coerce_non_empty_string(state.player.character_id)
    if player_character_id is not None and identity == player_character_id:
        return None
    return ValidationResult(ok=False, reason=f"{key} must refer to the current player")


def normalize_mapping(raw_value: Any) -> dict[str, Any]:
    if isinstance(raw_value, Mapping):
        return dict(raw_value)
    return {}


def coerce_int(raw_value: Any) -> int | None:
    if raw_value is None or isinstance(raw_value, bool):
        return None
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return None


def get_class_template(
    state: StateContainer,
    world: WorldInstance,
) -> Any:
    if not world.has_registry("classes"):
        return None
    class_id = coerce_non_empty_string(state.player.character_class)
    if class_id is None:
        return None
    return world.classes.get_class(class_id)


def source_get(source: Any, key: str) -> Any:
    if isinstance(source, Mapping):
        return source.get(key)
    return getattr(source, key, None)


def source_has(source: Any, key: str) -> bool:
    if isinstance(source, Mapping):
        return key in source
    return hasattr(source, key)


def read_mapping(primary: Any, fallback: Any, key: str) -> dict[str, Any]:
    for source in (primary, fallback):
        raw_value = source_get(source, key)
        if isinstance(raw_value, Mapping):
            return dict(raw_value)
    return {}


def read_list(primary: Any, fallback: Any, key: str) -> list[str]:
    for source in (primary, fallback):
        raw_value = source_get(source, key)
        if isinstance(raw_value, list):
            return [str(item) for item in raw_value]
    return []


def read_non_empty_string(
    primary: Any,
    fallback: Any,
    *keys: str,
) -> str | None:
    for key in keys:
        for source in (primary, fallback):
            value = coerce_non_empty_string(source_get(source, key))
            if value is not None:
                return value
    return None


def read_int(
    primary: Any,
    fallback: Any,
    *keys: str,
) -> int | None:
    for key in keys:
        for source in (primary, fallback):
            value = coerce_int(source_get(source, key))
            if value is not None:
                return value
    return None


def read_positive_int(
    primary: Any,
    fallback: Any,
    *keys: str,
) -> int | None:
    value = read_int(primary, fallback, *keys)
    if value is None or value < 0:
        return None
    return value


def read_bool(
    primary: Any,
    fallback: Any,
    key: str,
) -> bool:
    for source in (primary, fallback):
        if source_has(source, key):
            value = source_get(source, key)
            if value is not None:
                return bool(value)
    return False
