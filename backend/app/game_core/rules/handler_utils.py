"""Shared utilities for CommandHandler implementations.

Extracted from handler-local duplicated code to eliminate ~250+ lines of
identical implementations across 13 handlers.
"""

from __future__ import annotations

import re
import random
from typing import Any, Mapping

from app.game_core.content.registries.shared_types import Effect
from app.game_core.rules.models import DiceRoll, ExecuteResult
from app.game_core.state.delta import StateChange, StateDelta


# ---------------------------------------------------------------------------
# Type coercion
# ---------------------------------------------------------------------------

def resolve_item_heal_amount(item_template: Any) -> int | None:
    """Return the heal amount from an ItemTemplate, or None if not a healing item.

    Prefers ConsumableData.effect (canonical source) over the legacy heal_amount field.
    Supports both ``"dice": "2d4+2"`` (rolled on call) and ``"amount": N`` (static).
    """
    if item_template is None:
        return None
    consumable_data = getattr(item_template, "consumable_data", None)
    if consumable_data is not None:
        effect = getattr(consumable_data, "effect", None)
        if isinstance(effect, Effect) and effect.type == "heal":
            # Prefer dice expression (rolled each call)
            dice_str = effect.params.get("dice")
            if dice_str and isinstance(dice_str, str) and dice_str.strip():
                rolled = roll_damage_dice(dice_str.strip())
                return rolled if rolled > 0 else None
            # Fallback to static amount
            value = coerce_int(effect.params.get("amount"))
            if value is not None and value > 0:
                return value
    value = coerce_int(getattr(item_template, "heal_amount", None))
    return value if value is not None and value > 0 else None


def coerce_int(value: Any) -> int | None:
    """Coerce a value to int, returning None on failure."""
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


def coerce_float(value: Any) -> float | None:
    """Coerce a value to float, returning None on failure."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        try:
            return float(normalized)
        except ValueError:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def coerce_non_empty_string(raw_value: Any) -> str | None:
    """Coerce a value to a non-empty string, returning None on failure."""
    if raw_value is None:
        return None
    value = str(raw_value).strip()
    if not value:
        return None
    return value


def get_non_empty_string(params: Mapping[str, Any], key: str) -> str | None:
    """Extract and validate a non-empty string from a params mapping."""
    value = params.get(key)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def normalize_tags(raw: Any) -> list[str]:
    """Normalize a raw value into a list of string tags."""
    if not isinstance(raw, list):
        return []
    return [str(tag) for tag in raw]


# ---------------------------------------------------------------------------
# Dice utilities
# ---------------------------------------------------------------------------

_DAMAGE_DICE_RE = re.compile(r"^(\d+)d(\d+)([+-]\d+)?$", re.IGNORECASE)


def roll_damage_dice(dice_str: str) -> int:
    """Roll a damage dice expression and return total.

    Supported formats: "1d4", "2d6", "1d8+3", "2d6-1".
    Returns 1 on invalid input (safe fallback).
    """
    m = _DAMAGE_DICE_RE.match(dice_str.strip())
    if not m:
        return 1
    count = int(m.group(1))
    faces = int(m.group(2))
    bonus = int(m.group(3)) if m.group(3) else 0
    if count < 1 or faces < 1:
        return 1
    total = sum(random.randint(1, faces) for _ in range(count)) + bonus
    return max(1, total)


def roll_d20() -> int:
    """Roll a single d20."""
    return random.randint(1, 20)


def resolve_roll(
    roll_fn: Any = None,
    *,
    advantage: bool = False,
    disadvantage: bool = False,
) -> tuple[int, list[int], str]:
    """Resolve a d20 roll with optional advantage/disadvantage.

    Returns (selected_roll, all_rolls, dice_notation).
    """
    _roll = roll_fn if callable(roll_fn) else roll_d20
    if advantage and not disadvantage:
        rolls = [_roll(), _roll()]
        return max(rolls), rolls, "2d20kh1"
    if disadvantage and not advantage:
        rolls = [_roll(), _roll()]
        return min(rolls), rolls, "2d20kl1"
    r = _roll()
    return r, [r], "1d20"


def build_dice_roll(
    *,
    purpose: str,
    dice: str,
    result: int,
    modifiers: list[dict[str, Any]],
    total: int,
) -> DiceRoll:
    """Build a DiceRoll trace with automatic critical detection."""
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
        modifiers=modifiers,
        total=total,
        critical=critical,
    )


# ---------------------------------------------------------------------------
# Result builders
# ---------------------------------------------------------------------------

def handler_success(
    handler_name: str,
    command_type: str,
    *,
    changes: list[StateChange],
    metadata: dict[str, Any],
    time_cost: float = 0.0,
    rolls: list[DiceRoll] | None = None,
    narrative_hints: list[str] | None = None,
    omit_empty_delta: bool = True,
) -> ExecuteResult:
    """Build a successful ExecuteResult.

    When *omit_empty_delta* is True (default), an empty *changes* list produces
    ``delta=None`` instead of an empty StateDelta.
    """
    payload = {"handler": handler_name, "command": command_type, **metadata}
    if omit_empty_delta and not changes:
        delta = None
    else:
        delta = StateDelta(changes=changes, reason=command_type, metadata=payload)
    return ExecuteResult(
        executed=True,
        delta=delta,
        time_cost=time_cost,
        rolls=rolls or [],
        narrative_hints=narrative_hints or [],
        metadata=payload,
    )


def handler_success_no_delta(
    handler_name: str,
    command_type: str,
    *,
    metadata: dict[str, Any],
    time_cost: float = 0.0,
    rolls: list[DiceRoll] | None = None,
) -> ExecuteResult:
    """Build a successful ExecuteResult with no state delta."""
    return ExecuteResult(
        executed=True,
        delta=None,
        time_cost=time_cost,
        rolls=rolls or [],
        metadata={"handler": handler_name, "command": command_type, **metadata},
    )


def handler_failure(
    handler_name: str,
    command_type: str,
    *,
    errors: list[str],
) -> ExecuteResult:
    """Build a failed ExecuteResult."""
    return ExecuteResult(
        executed=False,
        errors=errors,
        metadata={"handler": handler_name, "command": command_type},
    )
