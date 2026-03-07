"""Shared rest-phase semantics for settlement hooks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Mapping

from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.state import StateChange


_REST_SLOT_TOTALS: dict[str, int] = {
    "rest_long": 8,
    "rest_short": 1,
    "night_watch": 1,
}
_VISIBLE_SYSTEM_TAGS: frozenset[str] = frozenset({
    "visible_consequence",
    "campfire_dialogue",
    "npc_wants_to_chat",
    "event_state_changed",
})
_QUIET_REST_CHANGE_SLICES: frozenset[str] = frozenset({
    "player",
    "areas",
    "party",
    "relations",
    "quests",
    "events",
})


@dataclass(slots=True)
class RestPhaseInfo:
    tick_kind: str
    is_rest_tick: bool
    rest_action_type: str
    rest_total_slots: int
    rest_slots_remaining: int
    rest_slot_index: int
    is_final_rest_slot: bool
    camp_type: str | None
    night_watch_required: bool | None
    crossed_day: bool

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


def resolve_rest_phase(context: SettlementContext) -> RestPhaseInfo | None:
    cached = getattr(context, "rest_phase", None)
    if isinstance(cached, RestPhaseInfo):
        return cached
    return build_rest_phase(
        action_log=context.action_log,
        current_time=(
            context.state.time if context.state.has_slice("time") else None
        ),
    )


def build_rest_phase(
    *,
    action_log: list[dict[str, Any]] | None,
    current_time: Any | None,
) -> RestPhaseInfo | None:
    if not isinstance(action_log, list) or not action_log:
        return None

    positive_actions: list[Mapping[str, Any]] = []
    for raw_action in action_log:
        if not isinstance(raw_action, Mapping):
            continue
        time_cost = _coerce_float(raw_action.get("time_cost"))
        if time_cost <= 0.0:
            continue
        positive_actions.append(raw_action)

    if not positive_actions:
        return None

    rest_types = {
        _normalized_action_type(action)
        for action in positive_actions
        if _normalized_action_type(action) in _REST_SLOT_TOTALS
    }
    if len(rest_types) != 1:
        return None
    if any(_normalized_action_type(action) not in _REST_SLOT_TOTALS for action in positive_actions):
        return None

    rest_action_type = next(iter(rest_types))
    rest_total_slots = _REST_SLOT_TOTALS[rest_action_type]
    rest_slots_remaining = rest_total_slots
    crossed_day = False
    if current_time is not None:
        accumulated = _coerce_float(getattr(current_time, "accumulated", 0.0))
        if rest_action_type == "rest_long" and accumulated > 0.0:
            rest_slots_remaining = max(
                1,
                min(rest_total_slots, int(math.ceil(accumulated - 1e-9))),
            )
        crossed_day = bool(getattr(current_time, "slot", 0) >= 24)

    rest_slot_index = max(1, rest_total_slots - rest_slots_remaining + 1)
    reference = positive_actions[0]
    return RestPhaseInfo(
        tick_kind="rest",
        is_rest_tick=True,
        rest_action_type=rest_action_type,
        rest_total_slots=rest_total_slots,
        rest_slots_remaining=rest_slots_remaining,
        rest_slot_index=rest_slot_index,
        is_final_rest_slot=rest_slots_remaining <= 1,
        camp_type=_coerce_non_empty_string(reference.get("camp_type")),
        night_watch_required=_coerce_optional_bool(reference.get("night_watch_required")),
        crossed_day=crossed_day,
    )


def is_quiet_rest_slot(
    context: SettlementContext,
    rest_phase: RestPhaseInfo | None = None,
) -> bool:
    phase = rest_phase or resolve_rest_phase(context)
    if phase is None:
        return False
    if phase.rest_action_type != "rest_long":
        return False
    if phase.is_final_rest_slot:
        return False
    if _has_player_perceivable_system_entries(context):
        return False
    if _has_meaningful_rest_changes(context.change_log):
        return False
    return True


def has_player_perceivable_rest_signals(
    context: SettlementContext,
    rest_phase: RestPhaseInfo | None = None,
) -> bool:
    phase = rest_phase or resolve_rest_phase(context)
    if phase is None:
        return False
    if _has_player_perceivable_system_entries(context):
        return True
    return _has_meaningful_rest_changes(context.change_log)


def _has_player_perceivable_system_entries(context: SettlementContext) -> bool:
    snapshot = context.scene_bus.snapshot()
    raw_entries = snapshot.get("entries", [])
    if not isinstance(raw_entries, list):
        return False
    for entry in raw_entries:
        if not isinstance(entry, Mapping):
            continue
        if str(entry.get("visibility", "")).strip().lower() != "system":
            continue
        source = str(entry.get("source", "")).strip().upper()
        tags = entry.get("tags", [])
        normalized_tags = {
            str(tag).strip()
            for tag in tags
            if isinstance(tag, str) and str(tag).strip()
        }
        if source != "ENGINE":
            return True
        if normalized_tags & _VISIBLE_SYSTEM_TAGS:
            return True
    return False


def _has_meaningful_rest_changes(change_log: list[StateChange]) -> bool:
    for change in change_log:
        if change.slice in _QUIET_REST_CHANGE_SLICES:
            return True
    return False


def _normalized_action_type(action: Mapping[str, Any]) -> str:
    return str(action.get("type", "")).strip().lower()


def _coerce_float(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _coerce_non_empty_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _coerce_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None
