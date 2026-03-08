"""Shared result-semantics helpers.

These helpers normalize "did the command execute?" vs
"what was the gameplay outcome?" so transport and agent layers stop
mixing execution state with outcome semantics.
"""

from __future__ import annotations

from typing import Any, Mapping


_CHECK_COMMANDS = frozenset({
    "skill_check",
    "saving_throw",
    "investigate",
    "discover",
    "interact_object_v2",
    "disarm_trap",
})
_BINARY_ACTION_COMMANDS = frozenset({
    "steal",
    "lockpick",
    "night_watch",
    "enter_hostile",
    "flee",
    "shove",
})


def normalize_outcome(
    command_type: str | None,
    metadata: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Return a normalized outcome object for legacy handler metadata."""
    if not isinstance(metadata, Mapping):
        return None

    existing = metadata.get("outcome")
    if isinstance(existing, Mapping):
        return _normalize_existing_outcome(existing)

    command = str(command_type or metadata.get("command") or "").strip()

    winner = str(metadata.get("winner") or "").strip()
    if winner:
        outcome: dict[str, Any] = {
            "category": "contest",
            "winner": winner,
            "passed": winner == "actor",
        }
        margin = _contest_margin(metadata)
        if margin is not None:
            outcome["margin"] = margin
        return outcome

    if command == "enter_hostile" and "passed" in metadata:
        passed = bool(metadata.get("passed", False))
        outcome = {
            "category": "binary_action",
            "passed": passed,
        }
        margin = _margin_from_pairs(
            metadata,
            ("total", "dc"),
            ("stealth_total", "dc"),
            ("passive_total", "dc"),
        )
        if margin is None:
            roll = _coerce_int(metadata.get("roll"))
            modifier = _coerce_int(metadata.get("modifier"))
            dc = _coerce_int(metadata.get("dc"))
            if roll is not None and modifier is not None and dc is not None:
                margin = (roll + modifier) - dc
        if margin is not None:
            outcome["margin"] = margin
        _copy_optional_fields(
            outcome,
            metadata,
            ("detected", "ambush", "recovery_multiplier", "surprise_state"),
        )
        return outcome

    if "passed" in metadata:
        outcome = {
            "category": _category_for_passed(command),
            "passed": bool(metadata.get("passed", False)),
        }
        margin = _margin_from_pairs(
            metadata,
            ("total", "dc"),
            ("passive_total", "dc"),
            ("flee_total", "escape_dc"),
            ("shove_total", "resist_dc"),
        )
        if margin is not None:
            outcome["margin"] = margin
        _copy_optional_fields(
            outcome,
            metadata,
            ("detected", "ambush", "recovery_multiplier", "surprise_state"),
        )
        return outcome

    return None


def outcome_passed(outcome: Mapping[str, Any] | None) -> bool | None:
    """Return the actor-facing pass/fail meaning of an outcome if present."""
    if not isinstance(outcome, Mapping):
        return None
    passed = outcome.get("passed")
    if isinstance(passed, bool):
        return passed
    winner = outcome.get("winner")
    if isinstance(winner, str) and winner.strip():
        return winner.strip() == "actor"
    return None


def _normalize_existing_outcome(outcome: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(outcome)
    category = str(normalized.get("category") or "").strip()
    if category not in {"check", "contest", "binary_action"}:
        if "winner" in normalized:
            category = "contest"
        elif "passed" in normalized:
            category = "check"
        else:
            category = "binary_action"
    normalized["category"] = category
    if "passed" in normalized:
        normalized["passed"] = bool(normalized.get("passed", False))
    winner = normalized.get("winner")
    if winner is not None:
        normalized["winner"] = str(winner).strip() or "tie"
    margin = _coerce_int(normalized.get("margin"))
    if margin is not None:
        normalized["margin"] = margin
    elif "margin" in normalized:
        normalized.pop("margin", None)
    return normalized


def _copy_optional_fields(
    target: dict[str, Any],
    source: Mapping[str, Any],
    keys: tuple[str, ...],
) -> None:
    for key in keys:
        if key not in source:
            continue
        value = source.get(key)
        if value is None:
            continue
        target[key] = value


def _category_for_passed(command: str) -> str:
    if command in _CHECK_COMMANDS:
        return "check"
    if command in _BINARY_ACTION_COMMANDS:
        return "binary_action"
    return "check"


def _contest_margin(metadata: Mapping[str, Any]) -> int | None:
    actor_total = _coerce_int(metadata.get("actor_total"))
    target_total = _coerce_int(metadata.get("target_total"))
    if actor_total is None or target_total is None:
        return None
    return actor_total - target_total


def _margin_from_pairs(
    metadata: Mapping[str, Any],
    *pairs: tuple[str, str],
) -> int | None:
    for left_key, right_key in pairs:
        left = _coerce_int(metadata.get(left_key))
        right = _coerce_int(metadata.get(right_key))
        if left is None or right is None:
            continue
        return left - right
    return None


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
        raw = value.strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
