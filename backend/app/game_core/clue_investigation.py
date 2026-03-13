"""Shared clue-investigation schema and effect helpers."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.environment_rewards import apply_environment_reward
from app.game_core.location_utils import coerce_non_empty_string
from app.game_core.rules.handlers.world_state import WorldStateHandler
from app.game_core.rules.models import Command
from app.game_core.state import StateChange, StateContainer, StateDelta

CLUE_FUNCTIONAL_TYPE = "investigate_clue"
CLUE_OPTION_MIN = 2
CLUE_OPTION_MAX = 4
CLUE_EFFECT_TYPES = frozenset({
    "set_flag",
    "remove_flag",
    "advance_quest",
    "add_knowledge",
    "modify_approval",
    "unlock_sub_location",
})


def normalize_clue_definition(
    raw: Any,
    *,
    interactable_id: str | None = None,
    name: str = "",
    description: str = "",
) -> dict[str, Any]:
    mapping = _mapping(raw)
    params = _mapping(mapping.get("params")) if "params" in mapping else mapping
    clue_id = (
        coerce_non_empty_string(params.get("clue_id"))
        or coerce_non_empty_string(params.get("id"))
        or coerce_non_empty_string(interactable_id)
    )
    if clue_id is None:
        return {}

    normalized: dict[str, Any] = {
        "clue_id": clue_id,
        "name": coerce_non_empty_string(params.get("name")) or name or clue_id,
        "description": str(params.get("description") or description or "").strip(),
        "hide_on_resolve": bool(params.get("hide_on_resolve", True)),
    }

    topic = coerce_non_empty_string(params.get("topic"))
    if topic is not None:
        normalized["topic"] = topic
    linked_quest_id = coerce_non_empty_string(params.get("linked_quest_id"))
    if linked_quest_id is not None:
        normalized["linked_quest_id"] = linked_quest_id
    linked_milestone = coerce_non_empty_string(params.get("linked_milestone"))
    if linked_milestone is not None:
        normalized["linked_milestone"] = linked_milestone

    prompt_hints = _string_list(params.get("party_prompt_hints"))
    if prompt_hints:
        normalized["party_prompt_hints"] = prompt_hints

    options = []
    for raw_option in params.get("options", []):
        option = _normalize_clue_option(raw_option)
        if option:
            options.append(option)
    normalized["options"] = options

    outcomes: dict[str, dict[str, list[dict[str, Any]]]] = {}
    raw_outcomes = _mapping(params.get("outcomes"))
    for option in options:
        option_id = option["id"]
        bundle = _normalize_outcome_bundle(raw_outcomes.get(option_id))
        outcomes[option_id] = bundle
    normalized["outcomes"] = outcomes

    normalized["on_first_inspect"] = _normalize_effect_list(params.get("on_first_inspect"))
    return normalized


def validate_clue_definition(clue: Mapping[str, Any]) -> str | None:
    clue_id = coerce_non_empty_string(clue.get("clue_id"))
    if clue_id is None:
        return "missing_clue_id"
    options = clue.get("options")
    if not isinstance(options, list):
        return "missing_options"
    if len(options) < CLUE_OPTION_MIN or len(options) > CLUE_OPTION_MAX:
        return "invalid_option_count"
    option_ids: set[str] = set()
    for option in options:
        if not isinstance(option, Mapping):
            return "invalid_option"
        option_id = coerce_non_empty_string(option.get("id"))
        if option_id is None:
            return "invalid_option_id"
        if option_id in option_ids:
            return "duplicate_option_id"
        option_ids.add(option_id)
        label = coerce_non_empty_string(option.get("label"))
        if label is None:
            return "invalid_option_label"
        check = option.get("check")
        if check is not None:
            if not isinstance(check, Mapping):
                return "invalid_option_check"
            skill = coerce_non_empty_string(check.get("skill"))
            if skill is None:
                return "invalid_option_check_skill"
            try:
                dc = int(check.get("dc"))
            except (TypeError, ValueError):
                return "invalid_option_check_dc"
            if dc < 0:
                return "invalid_option_check_dc"
    on_first_inspect = clue.get("on_first_inspect", [])
    if not isinstance(on_first_inspect, list):
        return "invalid_on_first_inspect"
    for effect in on_first_inspect:
        effect_type = coerce_non_empty_string(effect.get("type")) if isinstance(effect, Mapping) else None
        if effect_type not in CLUE_EFFECT_TYPES:
            return "invalid_effect_type"
    outcomes = clue.get("outcomes")
    if not isinstance(outcomes, Mapping):
        return "invalid_outcomes"
    for option_id in option_ids:
        bundle = outcomes.get(option_id, {})
        if not isinstance(bundle, Mapping):
            return "invalid_outcome_bundle"
        for branch_key in ("always", "on_pass", "on_fail"):
            effects = bundle.get(branch_key, [])
            if not isinstance(effects, list):
                return "invalid_outcome_bundle"
            for effect in effects:
                effect_type = coerce_non_empty_string(effect.get("type")) if isinstance(effect, Mapping) else None
                if effect_type not in CLUE_EFFECT_TYPES:
                    return "invalid_effect_type"
    return None


def build_clue_dialogue_options(
    interactable_id: str,
    clue: Mapping[str, Any],
) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for raw_option in clue.get("options", []):
        if not isinstance(raw_option, Mapping):
            continue
        option_id = coerce_non_empty_string(raw_option.get("id"))
        label = coerce_non_empty_string(raw_option.get("label"))
        if option_id is None or label is None:
            continue
        entry: dict[str, Any] = {
            "id": option_id,
            "text": label,
            "label": label,
            "dispatch": {
                "kind": "action",
                "payload": {
                    "action_type": "resolve_clue_option",
                    "params": {
                        "interactable_id": interactable_id,
                        "option_id": option_id,
                    },
                },
            },
        }
        check = raw_option.get("check")
        if isinstance(check, Mapping):
            skill = coerce_non_empty_string(check.get("skill"))
            try:
                dc = int(check.get("dc"))
            except (TypeError, ValueError):
                dc = None
            if skill is not None and dc is not None:
                entry["check"] = {"skill": skill, "dc": dc}
        options.append(entry)
    return options


def effect_types_for_option(
    clue: Mapping[str, Any],
    option_id: str,
    *,
    passed: bool | None = None,
) -> list[str]:
    bundle = _mapping(_mapping(clue.get("outcomes")).get(option_id))
    effect_types = [
        str(effect.get("type"))
        for effect in _normalize_effect_list(bundle.get("always"))
    ]
    if passed is True:
        effect_types.extend(
            str(effect.get("type"))
            for effect in _normalize_effect_list(bundle.get("on_pass"))
        )
    if passed is False:
        effect_types.extend(
            str(effect.get("type"))
            for effect in _normalize_effect_list(bundle.get("on_fail"))
        )
    return [effect_type for effect_type in effect_types if effect_type]


def apply_clue_effects(
    state: StateContainer,
    world: Any,
    effects: list[dict[str, Any]],
    *,
    area_id: str,
    source: str,
) -> tuple[list[StateChange], dict[str, Any], str | None]:
    if not effects:
        return [], {"applied": []}, None

    working_state = StateContainer.create_restored(world, state.snapshot())
    world_state_handler = WorldStateHandler()
    collected_changes: list[StateChange] = []
    applied: list[dict[str, Any]] = []

    for effect in effects:
        effect_type = coerce_non_empty_string(effect.get("type"))
        params = _mapping(effect.get("params"))
        if effect_type is None or effect_type not in CLUE_EFFECT_TYPES:
            return [], {"applied": applied}, "invalid_effect_type"
        if effect_type == "unlock_sub_location":
            reward = dict(params)
            reward["type"] = "sub_location"
            changes, reward_result = apply_environment_reward(
                working_state,
                reward,
                area_id=area_id,
                source=source,
            )
            if changes:
                working_state.apply(StateDelta(changes=changes, reason="clue_effect"))
                collected_changes.extend(changes)
            applied.extend(_coerce_applied_entries(reward_result, default_type="unlock_sub_location"))
            continue

        expanded = _expand_effect_commands(effect_type, params, working_state)
        if not expanded:
            return [], {"applied": applied}, "invalid_effect_params"
        for command in expanded:
            result = world_state_handler.compute(command, working_state, world)
            if not result.executed:
                reason = result.errors[0] if result.errors else "effect_command_failed"
                return [], {"applied": applied}, reason
            delta = result.delta
            if delta is not None and delta.changes:
                working_state.apply(delta)
                collected_changes.extend(delta.changes)
            applied.append({
                "type": effect_type,
                "params": dict(command.params),
            })

    return collected_changes, {"applied": applied}, None


def select_option_effects(
    clue: Mapping[str, Any],
    option_id: str,
    *,
    passed: bool | None,
) -> list[dict[str, Any]]:
    outcomes = _mapping(clue.get("outcomes"))
    bundle = _mapping(outcomes.get(option_id))
    selected = _normalize_effect_list(bundle.get("always"))
    if passed is True:
        selected.extend(_normalize_effect_list(bundle.get("on_pass")))
    elif passed is False:
        selected.extend(_normalize_effect_list(bundle.get("on_fail")))
    return selected


def clue_option_by_id(clue: Mapping[str, Any], option_id: str) -> dict[str, Any] | None:
    target = coerce_non_empty_string(option_id)
    if target is None:
        return None
    for raw_option in clue.get("options", []):
        if not isinstance(raw_option, Mapping):
            continue
        if coerce_non_empty_string(raw_option.get("id")) != target:
            continue
        return dict(raw_option)
    return None


def is_clue_functional(functional: Any) -> bool:
    mapping = _mapping(functional)
    return coerce_non_empty_string(mapping.get("type")) == CLUE_FUNCTIONAL_TYPE


def _normalize_clue_option(raw: Any) -> dict[str, Any] | None:
    mapping = _mapping(raw)
    option_id = coerce_non_empty_string(mapping.get("id"))
    label = coerce_non_empty_string(mapping.get("label")) or coerce_non_empty_string(mapping.get("text"))
    if option_id is None or label is None:
        return None
    normalized: dict[str, Any] = {"id": option_id, "label": label}
    check = _mapping(mapping.get("check"))
    skill = coerce_non_empty_string(check.get("skill"))
    if skill is not None:
        try:
            dc = int(check.get("dc"))
        except (TypeError, ValueError):
            return None
        normalized["check"] = {"skill": skill, "dc": dc}
    return normalized


def _normalize_outcome_bundle(raw: Any) -> dict[str, list[dict[str, Any]]]:
    if isinstance(raw, list):
        return {
            "always": _normalize_effect_list(raw),
            "on_pass": [],
            "on_fail": [],
        }
    mapping = _mapping(raw)
    return {
        "always": _normalize_effect_list(mapping.get("always")),
        "on_pass": _normalize_effect_list(mapping.get("on_pass")),
        "on_fail": _normalize_effect_list(mapping.get("on_fail")),
    }


def _normalize_effect_list(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in raw:
        mapping = _mapping(item)
        effect_type = coerce_non_empty_string(mapping.get("type"))
        if effect_type is None:
            continue
        params = _mapping(mapping.get("params"))
        if not params:
            params = {
                str(key): value
                for key, value in mapping.items()
                if str(key) != "type"
            }
        normalized.append({
            "type": effect_type,
            "params": params,
        })
    return normalized


def _expand_effect_commands(
    effect_type: str,
    params: Mapping[str, Any],
    state: StateContainer,
) -> list[Command]:
    if effect_type in {"set_flag", "remove_flag", "advance_quest"}:
        return [Command(type=effect_type, params=dict(params), source="system")]

    if effect_type in {"add_knowledge", "modify_approval"}:
        scope = coerce_non_empty_string(params.get("scope"))
        if scope == "party" and state.has_slice("party"):
            commands: list[Command] = []
            for member_id in state.party.members.keys():
                expanded_params = dict(params)
                if effect_type == "add_knowledge":
                    expanded_params.pop("scope", None)
                    expanded_params.setdefault("character_id", member_id)
                else:
                    expanded_params.pop("scope", None)
                    expanded_params.setdefault("character_id", member_id)
                commands.append(Command(type=effect_type, params=expanded_params, source="system"))
            return commands
        return [Command(type=effect_type, params=dict(params), source="system")]

    return []


def _coerce_applied_entries(
    reward_result: Mapping[str, Any],
    *,
    default_type: str,
) -> list[dict[str, Any]]:
    applied = reward_result.get("applied")
    if not isinstance(applied, list):
        return [{"type": default_type}]
    normalized: list[dict[str, Any]] = []
    for item in applied:
        if isinstance(item, Mapping):
            normalized.append(dict(item))
    return normalized or [{"type": default_type}]


def _string_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    values = []
    for item in raw:
        text = coerce_non_empty_string(item)
        if text is not None:
            values.append(text)
    return values


def _mapping(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    return {str(key): value for key, value in raw.items()}
