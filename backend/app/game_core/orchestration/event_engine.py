"""EventEngine — reusable event condition evaluation core (O-2).

Extracted from event_condition.py so the evaluation logic can be
imported, tested, and replaced independently of the settlement hook.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, TYPE_CHECKING, Mapping, Protocol

from app.game_core.content import WorldInstance
from app.game_core.rules.models import Command
from app.game_core.state import StateContainer
from app.game_core.state.delta import StateChange

if TYPE_CHECKING:
    from app.game_core.orchestration.scene_bus import SceneBus
    from app.game_core.rules import RulesEngine
    from app.game_core.state import StateDelta


# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

_ALLOWED_COMMAND_TYPES: tuple[str, ...] = (
    "set_flag",
    "modify_disposition",
    "modify_approval",
    "advance_quest",
    "schedule_event",
    "create_rumor",
    "modify_location",
    "add_knowledge",
    "modify_completion",
    "adjust_danger",
)

_SUPPORTED_STATES = frozenset(
    {
        "locked",
        "dormant",
        "triggered",
        "available",
        "active",
        "resolved",
        "expired",
        "cancelled",
    }
)


# ------------------------------------------------------------------
# Data structures
# ------------------------------------------------------------------


@dataclass(slots=True)
class EventTransition:
    event_id: str
    from_state: str
    to_state: str
    reason: str = ""
    patch: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EventConditionDecision:
    transitions: list[EventTransition] = field(default_factory=list)
    commands: list[Command | Mapping[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------
# Protocol
# ------------------------------------------------------------------


class EventConditionEvaluator(Protocol):
    def evaluate(
        self,
        state: StateContainer,
        world: WorldInstance,
    ) -> EventConditionDecision | Mapping[str, Any]:
        ...


# ------------------------------------------------------------------
# Default deterministic evaluator
# ------------------------------------------------------------------


class BasicEventConditionEvaluator:
    def evaluate(
        self,
        state: StateContainer,
        world: WorldInstance,
    ) -> EventConditionDecision:
        del world
        if not state.has_slice("events"):
            return EventConditionDecision(metadata={"unsupported_condition_count": 0})

        transitions: list[EventTransition] = []
        all_commands: list[Any] = []
        unsupported_condition_count = 0
        for event_id, event in state.events.list_active_events().items():
            transition, commands, unsupported_count = self._evaluate_event(
                state, event_id, event,
            )
            unsupported_condition_count += unsupported_count
            if transition is not None:
                transitions.append(transition)
            all_commands.extend(commands)

        return EventConditionDecision(
            transitions=transitions,
            commands=all_commands,
            metadata={"unsupported_condition_count": unsupported_condition_count},
        )

    def _evaluate_event(
        self,
        state: StateContainer,
        event_id: str,
        event: Mapping[str, Any],
    ) -> tuple[EventTransition | None, list[dict[str, Any]], int]:
        """Evaluate a single event and return (transition, on_trigger_commands, unsupported_count).

        When a dormant/locked event's conditions are met:
        - If the event has ``on_trigger`` commands → transition to "resolved" (one-shot)
          and return those commands for immediate execution.
        - Otherwise → transition to "available" (standard state machine path).
        """
        current_state = _canonical_state(event)
        conditions_met, unsupported_count = self._conditions_met(
            state,
            event.get("conditions"),
        )

        if current_state in {"locked", "dormant"} and conditions_met:
            raw_on_trigger = event.get("on_trigger")
            if raw_on_trigger and isinstance(raw_on_trigger, list):
                # One-shot: fire commands and mark resolved so the event doesn't re-trigger.
                commands = [
                    dict(c) for c in raw_on_trigger if isinstance(c, Mapping)
                ]
                return (
                    EventTransition(
                        event_id=event_id,
                        from_state=current_state,
                        to_state="resolved",
                        reason="conditions_met_triggered",
                    ),
                    commands,
                    unsupported_count,
                )
            # Standard: no on_trigger → transition to available
            return (
                EventTransition(
                    event_id=event_id,
                    from_state=current_state,
                    to_state="available",
                    reason="conditions_met",
                ),
                [],
                unsupported_count,
            )

        if current_state == "triggered":
            raw_conditions = self._normalize_conditions(event.get("conditions"))
            if not raw_conditions or conditions_met:
                return (
                    EventTransition(
                        event_id=event_id,
                        from_state=current_state,
                        to_state="active",
                        reason="triggered_ready",
                    ),
                    [],
                    unsupported_count,
                )

        return None, [], unsupported_count

    def _conditions_met(
        self,
        state: StateContainer,
        raw_conditions: Any,
    ) -> tuple[bool, int]:
        conditions = self._normalize_conditions(raw_conditions)
        if not conditions:
            return True, 0

        all_met = True
        unsupported_condition_count = 0
        for condition in conditions:
            matched, unsupported_count = self._condition_met(state, condition)
            all_met = all_met and matched
            unsupported_condition_count += unsupported_count
        return all_met, unsupported_condition_count

    @staticmethod
    def _normalize_conditions(raw_conditions: Any) -> list[dict[str, Any]]:
        if raw_conditions is None:
            return []
        if isinstance(raw_conditions, Mapping):
            return [{str(key): value for key, value in raw_conditions.items()}]
        if not isinstance(raw_conditions, list):
            return [{"type": "__invalid__"}]

        normalized: list[dict[str, Any]] = []
        for item in raw_conditions:
            if isinstance(item, Mapping):
                normalized.append({str(key): value for key, value in item.items()})
            else:
                normalized.append({"type": "__invalid__"})
        return normalized

    def _condition_met(
        self,
        state: StateContainer,
        condition: Mapping[str, Any],
    ) -> tuple[bool, int]:
        condition_type = _coerce_string(condition.get("type")).lower()
        params = self._condition_params(condition)

        if condition_type in {"", "__invalid__"}:
            return False, 1
        if condition_type == "flag_set":
            return self._check_flag_set(state, params), 0
        if condition_type == "location_entered":
            return self._check_location_entered(state, params), 0
        if condition_type == "period_reached":
            return self._check_period_reached(state, params), 0
        if condition_type == "time_reached":
            return self._check_time_reached(state, params), 0
        if condition_type == "quest_state":
            return self._check_quest_state(state, params), 0
        if condition_type == "disposition":
            return self._check_disposition(state, params), 0
        if condition_type == "time_elapsed":
            return self._check_time_elapsed(state, params), 0
        if condition_type == "custom":
            return self._check_custom(state, params), 0
        if condition_type == "npc_talked":
            return self._check_npc_talked(state, params), 0
        if condition_type == "item_obtained":
            return self._check_item_obtained(state, params), 0
        if condition_type == "kill_count":
            return self._check_kill_count(state, params), 0
        return False, 1

    @staticmethod
    def _condition_params(condition: Mapping[str, Any]) -> dict[str, Any]:
        params: dict[str, Any] = {}
        raw_params = condition.get("params")
        if isinstance(raw_params, Mapping):
            params.update({str(key): value for key, value in raw_params.items()})
        for key, value in condition.items():
            if key in {"type", "params"}:
                continue
            params[str(key)] = value
        return params

    @staticmethod
    def _check_flag_set(state: StateContainer, params: Mapping[str, Any]) -> bool:
        if not state.has_slice("flags"):
            return False
        key = _coerce_non_empty_string(params.get("key")) or _coerce_non_empty_string(
            params.get("flag_key")
        )
        if key is None:
            return False
        expected_value = params.get("value", True)
        return state.flags.get(key) == expected_value

    @staticmethod
    def _check_location_entered(
        state: StateContainer,
        params: Mapping[str, Any],
    ) -> bool:
        if not state.has_slice("player"):
            return False

        area_id = _coerce_non_empty_string(params.get("area_id"))
        location_id = _coerce_non_empty_string(params.get("location_id"))
        if area_id is None and location_id is None:
            return False

        area_ok = True
        if area_id is not None:
            area_ok = state.player.current_area == area_id

        location_ok = True
        if location_id is not None:
            location_ok = state.player.current_location == location_id

        return area_ok and location_ok

    @staticmethod
    def _check_period_reached(state: StateContainer, params: Mapping[str, Any]) -> bool:
        if not state.has_slice("time"):
            return False
        period = _coerce_non_empty_string(params.get("period"))
        if period is None:
            return False
        return state.time.get_current_time()["period"] == period

    @staticmethod
    def _check_time_reached(state: StateContainer, params: Mapping[str, Any]) -> bool:
        if not state.has_slice("time"):
            return False
        target_day = _coerce_int(params.get("day"))
        if target_day is None:
            return False

        target_slot = _coerce_int(params.get("slot"))
        current_day = int(state.time.day)
        current_slot = int(state.time.slot)
        if current_day > target_day:
            return True
        if current_day < target_day:
            return False
        if target_slot is None:
            return True
        return current_slot >= target_slot

    @staticmethod
    def _check_quest_state(state: StateContainer, params: Mapping[str, Any]) -> bool:
        if not state.has_slice("quests"):
            return False

        quest_id = _coerce_non_empty_string(params.get("quest_id"))
        expected_state = _coerce_non_empty_string(params.get("state"))
        if quest_id is None or expected_state is None:
            return False

        kind = _coerce_string(params.get("kind")).lower() or "auto"
        if kind == "milestone":
            return BasicEventConditionEvaluator._milestone_state_matches(
                state,
                quest_id,
                expected_state,
            )
        if kind == "dynamic":
            return BasicEventConditionEvaluator._dynamic_state_matches(
                state,
                quest_id,
                expected_state,
            )
        return (
            BasicEventConditionEvaluator._milestone_state_matches(
                state,
                quest_id,
                expected_state,
            )
            or BasicEventConditionEvaluator._dynamic_state_matches(
                state,
                quest_id,
                expected_state,
            )
        )

    @staticmethod
    def _milestone_state_matches(
        state: StateContainer,
        quest_id: str,
        expected_state: str,
    ) -> bool:
        milestone_state = state.quests.get_milestone_state(quest_id)
        if milestone_state is None:
            return False
        return str(milestone_state).upper() == expected_state.upper()

    @staticmethod
    def _dynamic_state_matches(
        state: StateContainer,
        quest_id: str,
        expected_state: str,
    ) -> bool:
        quest = state.quests.get_dynamic_quest(quest_id)
        if not isinstance(quest, dict):
            return False
        return str(quest.get("status", "")).lower() == expected_state.lower()

    @staticmethod
    def _check_disposition(state: StateContainer, params: dict[str, Any]) -> bool:
        """Check if NPC disposition dimension meets threshold.

        params:
            npc_id: str, dimension: str (approval/trust/fear/romance),
            threshold: int, operator: str (gte/lte/eq, default gte)
        """
        if not state.has_slice("relations"):
            return False
        npc_id = _coerce_non_empty_string(params.get("npc_id"))
        dimension = _coerce_non_empty_string(params.get("dimension"))
        if not npc_id or not dimension:
            return False
        threshold = _coerce_int(params.get("threshold", 0))
        if threshold is None:
            return False
        value = state.relations.get_disposition(npc_id, dimension)
        if not isinstance(value, int):
            return False
        operator = _coerce_string(params.get("operator")).lower() or "gte"
        if operator == "lte":
            return value <= threshold
        if operator == "eq":
            return value == threshold
        return value >= threshold  # default: gte

    @staticmethod
    def _check_time_elapsed(state: StateContainer, params: dict[str, Any]) -> bool:
        """Check if N ticks have elapsed since a reference tick.

        params:
            since_tick: int, elapsed: int (required ticks)
        """
        if not state.has_slice("time"):
            return False
        since_tick = _coerce_int(params.get("since_tick"))
        elapsed = _coerce_int(params.get("elapsed", 0))
        if since_tick is None or elapsed is None:
            return False
        return (state.time.absolute_tick() - since_tick) >= elapsed

    @staticmethod
    def _check_custom(state: StateContainer, params: dict[str, Any]) -> bool:
        """Extension point — always False in BasicEvaluator."""
        del state, params
        return False

    @staticmethod
    def _check_npc_talked(state: StateContainer, params: Mapping[str, Any]) -> bool:
        """Check if the player has talked to a specific NPC.

        Reads the ``talked_to_{npc_id}`` flag written by NpcInteractionCoordinator
        and PrivateChatCoordinator at the start of each interaction.

        params:
            npc_id: str — NPC identifier
        """
        npc_id = _coerce_non_empty_string(params.get("npc_id"))
        if npc_id is None or not state.has_slice("flags"):
            return False
        return bool(state.flags.get(f"talked_to_{npc_id}"))

    @staticmethod
    def _check_item_obtained(state: StateContainer, params: Mapping[str, Any]) -> bool:
        """Check if the player currently holds a specific item.

        Searches the player's inventory for an entry with matching item_id.

        params:
            item_id: str — item identifier to look for
        """
        item_id = _coerce_non_empty_string(params.get("item_id"))
        if item_id is None or not state.has_slice("player"):
            return False
        inventory = state.player.snapshot().get("inventory", [])
        return any(
            isinstance(item, dict) and item.get("item_id") == item_id
            for item in inventory
        )

    @staticmethod
    def _check_kill_count(state: StateContainer, params: Mapping[str, Any]) -> bool:
        """Check if kill count for a monster type meets the required threshold.

        Reads ``kill_count_{monster_type}`` from FlagSlice, which is written
        by the combat handler when a monster of that type is defeated.

        params:
            monster_type: str — monster type key (e.g. "goblin")
            count: int       — required kill count (default 1)
        """
        monster_type = _coerce_non_empty_string(params.get("monster_type"))
        required = _coerce_int(params.get("count", 1)) or 1
        if monster_type is None or not state.has_slice("flags"):
            return False
        current = state.flags.get(f"kill_count_{monster_type}", 0)
        try:
            return int(current) >= required
        except (TypeError, ValueError):
            return False


# ------------------------------------------------------------------
# Utility functions
# ------------------------------------------------------------------


def _canonical_state(event: Mapping[str, Any]) -> str:
    state = _normalize_state_name(event.get("state"))
    if state is not None:
        return state
    status = _normalize_state_name(event.get("status"))
    if status is not None:
        return status
    return "locked"


def _normalize_state_name(value: Any) -> str | None:
    normalized = _coerce_non_empty_string(value)
    if normalized is None:
        return None
    return normalized.lower()


def _coerce_non_empty_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _coerce_string(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): raw_value for key, raw_value in value.items()}


def run_inline_event_check(
    *,
    state: StateContainer,
    world: WorldInstance,
    rules_engine: "RulesEngine",
    apply_delta: Callable[["StateDelta | None"], None],
    change_log: list[StateChange],
    scene_bus: "SceneBus",
    label: str,
) -> list[dict[str, Any]]:
    """Evaluate event conditions, apply transitions, and execute on_trigger commands.

    This is the lightweight inline counterpart to ``EventConditionHook``.
    A6/C1/external checks use this for immediate in-tick reactivity.
    """
    if not state.has_slice("events"):
        return []

    evaluator = BasicEventConditionEvaluator()
    decision = evaluator.evaluate(state, world)

    payloads: list[dict[str, Any]] = []

    for transition in decision.transitions:
        event_snapshot = state.events.get_event(transition.event_id)
        if event_snapshot is None:
            continue
        if transition.to_state not in _SUPPORTED_STATES:
            continue

        from_state = _canonical_state(event_snapshot)
        state.events.set_state(
            transition.event_id,
            transition.to_state,
            patch=transition.patch,
        )
        change = StateChange(
            slice="events",
            operation="set",
            path=f"state.{transition.event_id}",
            value=transition.to_state,
        )
        change_log.append(change)
        scene_bus.record_state_change(change)
        payloads.append(
            {
                "event_id": transition.event_id,
                "from_state": from_state,
                "to_state": transition.to_state,
                "reason": transition.reason,
                "source": label,
            },
        )

    for raw_command in decision.commands:
        command = _coerce_event_command(raw_command)
        if command is None:
            continue
        try:
            result = rules_engine.execute(command, state, world)
        except Exception:
            continue
        if result.success and result.delta is not None:
            apply_delta(result.delta)

    return payloads


def _coerce_event_command(raw: Any) -> Command | None:
    if isinstance(raw, Command):
        if raw.type in _ALLOWED_COMMAND_TYPES:
            return Command(type=raw.type, params=dict(raw.params), source="system")
        return None
    if not isinstance(raw, Mapping):
        return None

    command_type = _coerce_string(raw.get("type"))
    if not command_type or command_type not in _ALLOWED_COMMAND_TYPES:
        return None
    raw_params = raw.get("params")
    params = _normalize_mapping(raw_params) if isinstance(raw_params, Mapping) else {}
    raw_context = raw.get("context")
    context = (
        _normalize_mapping(raw_context)
        if isinstance(raw_context, Mapping)
        else None
    )
    return Command(type=command_type, params=params, source="system", context=context)
