"""EventConditionHook implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, Mapping, Protocol

from app.game_core.content import WorldInstance
from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules.models import Command
from app.game_core.state import StateContainer

logger = logging.getLogger(__name__)

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


class EventConditionEvaluator(Protocol):
    def evaluate(
        self,
        state: StateContainer,
        world: WorldInstance,
    ) -> EventConditionDecision | Mapping[str, Any]:
        ...


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
        unsupported_condition_count = 0
        for event_id, event in state.events.list_active_events().items():
            transition, unsupported_count = self._evaluate_event(state, event_id, event)
            unsupported_condition_count += unsupported_count
            if transition is not None:
                transitions.append(transition)

        return EventConditionDecision(
            transitions=transitions,
            metadata={"unsupported_condition_count": unsupported_condition_count},
        )

    def _evaluate_event(
        self,
        state: StateContainer,
        event_id: str,
        event: Mapping[str, Any],
    ) -> tuple[EventTransition | None, int]:
        current_state = _canonical_state(event)
        conditions_met, unsupported_count = self._conditions_met(
            state,
            event.get("conditions"),
        )

        if current_state in {"locked", "dormant"} and conditions_met:
            return (
                EventTransition(
                    event_id=event_id,
                    from_state=current_state,
                    to_state="available",
                    reason="conditions_met",
                ),
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
                    unsupported_count,
                )

        return None, unsupported_count

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


class EventConditionHook(NoOpSettlementHook):
    HOOK_PRIORITY = 50
    HOOK_NAME = "event_conditions"

    def __init__(self, evaluator: EventConditionEvaluator | None = None) -> None:
        self._evaluator = evaluator or BasicEventConditionEvaluator()

    def should_skip(self, change_log: list[Any]) -> bool:
        del change_log
        return False

    async def execute(self, context: SettlementContext) -> HookResult:
        if not context.state.has_slice("events"):
            return HookResult(metadata=self._noop_metadata(checked_event_count=0))

        active_events = context.state.events.list_active_events()
        checked_event_count = len(active_events)
        if checked_event_count == 0:
            return HookResult(metadata=self._noop_metadata(checked_event_count=0))

        try:
            raw_decision = self._evaluator.evaluate(context.state, context.world)
        except Exception as exc:
            logger.exception(
                "hook failed: event_conditions",
                extra={
                    "hook_name": self.HOOK_NAME,
                    "checked_event_count": checked_event_count,
                },
            )
            return HookResult(
                sse_events=[
                    SSEEvent(
                        event_type="event_condition_error",
                        payload={"error": str(exc)},
                    )
                ],
                metadata={
                    "status": "evaluator_error",
                    "checked_event_count": checked_event_count,
                    "transitioned_count": 0,
                    "executed_count": 0,
                    "failed_count": 0,
                    "unsupported_condition_count": 0,
                    "skipped_invalid_transition_count": 0,
                    "skipped_invalid_command_count": 0,
                    "transitions": [],
                    "command_results": [],
                    "evaluator_metadata": {},
                },
            )

        decision = self._normalize_decision(raw_decision)
        transitions, transition_sse_events, skipped_invalid_transition_count = (
            self._apply_transitions(context, active_events, decision.transitions)
        )
        commands, skipped_invalid_command_count = self._normalize_commands(decision.commands)
        command_results, failed_count = self._execute_commands(context, commands)

        executed_count = len(command_results)
        transitioned_count = len(transitions)
        status = self._resolve_status(
            transitioned_count=transitioned_count,
            executed_count=executed_count,
            failed_count=failed_count,
        )
        evaluator_metadata = dict(decision.metadata)
        unsupported_condition_count = _coerce_int(
            evaluator_metadata.get("unsupported_condition_count")
        )
        if unsupported_condition_count is None:
            unsupported_condition_count = 0

        return HookResult(
            sse_events=transition_sse_events,
            metadata={
                "status": status,
                "checked_event_count": checked_event_count,
                "transitioned_count": transitioned_count,
                "executed_count": executed_count,
                "failed_count": failed_count,
                "unsupported_condition_count": unsupported_condition_count,
                "skipped_invalid_transition_count": skipped_invalid_transition_count,
                "skipped_invalid_command_count": skipped_invalid_command_count,
                "transitions": transitions,
                "command_results": command_results,
                "evaluator_metadata": evaluator_metadata,
            },
        )

    @staticmethod
    def _noop_metadata(*, checked_event_count: int) -> dict[str, Any]:
        return {
            "status": "noop",
            "checked_event_count": checked_event_count,
            "transitioned_count": 0,
            "executed_count": 0,
            "failed_count": 0,
            "unsupported_condition_count": 0,
            "skipped_invalid_transition_count": 0,
            "skipped_invalid_command_count": 0,
            "transitions": [],
            "command_results": [],
            "evaluator_metadata": {},
        }

    @classmethod
    def _normalize_decision(
        cls,
        raw_decision: EventConditionDecision | Mapping[str, Any],
    ) -> EventConditionDecision:
        if isinstance(raw_decision, EventConditionDecision):
            return EventConditionDecision(
                transitions=[
                    cls._copy_transition(transition)
                    for transition in raw_decision.transitions
                    if isinstance(transition, EventTransition)
                ],
                commands=list(raw_decision.commands),
                metadata=cls._normalize_mapping(raw_decision.metadata),
            )
        if not isinstance(raw_decision, Mapping):
            return EventConditionDecision(metadata={"status": "invalid_response"})

        raw_transitions = raw_decision.get("transitions", [])
        transitions: list[EventTransition] = []
        if isinstance(raw_transitions, list):
            for item in raw_transitions:
                normalized = cls._normalize_transition(item)
                if normalized is not None:
                    transitions.append(normalized)

        raw_commands = raw_decision.get("commands", [])
        commands = list(raw_commands) if isinstance(raw_commands, list) else []
        return EventConditionDecision(
            transitions=transitions,
            commands=commands,
            metadata=cls._normalize_mapping(raw_decision.get("metadata")),
        )

    @staticmethod
    def _copy_transition(transition: EventTransition) -> EventTransition:
        return EventTransition(
            event_id=transition.event_id,
            from_state=transition.from_state,
            to_state=transition.to_state,
            reason=transition.reason,
            patch=dict(transition.patch),
        )

    @classmethod
    def _normalize_transition(cls, raw_transition: Any) -> EventTransition | None:
        if isinstance(raw_transition, EventTransition):
            return cls._copy_transition(raw_transition)
        if not isinstance(raw_transition, Mapping):
            return None

        event_id = _coerce_non_empty_string(raw_transition.get("event_id"))
        to_state = _normalize_state_name(raw_transition.get("to_state"))
        if event_id is None or to_state is None:
            return None

        raw_patch = raw_transition.get("patch")
        patch = cls._normalize_mapping(raw_patch) if isinstance(raw_patch, Mapping) else {}
        from_state = _normalize_state_name(raw_transition.get("from_state")) or ""
        reason = _coerce_string(raw_transition.get("reason"))
        return EventTransition(
            event_id=event_id,
            from_state=from_state,
            to_state=to_state,
            reason=reason,
            patch=patch,
        )

    @classmethod
    def _normalize_commands(
        cls,
        raw_commands: list[Command | Mapping[str, Any]],
    ) -> tuple[list[Command], int]:
        commands: list[Command] = []
        skipped_invalid_command_count = 0
        for raw_command in raw_commands:
            command = cls._normalize_command(raw_command)
            if command is None:
                skipped_invalid_command_count += 1
                continue
            commands.append(command)
        return commands, skipped_invalid_command_count

    @classmethod
    def _normalize_command(cls, raw_command: Any) -> Command | None:
        if isinstance(raw_command, Command):
            if raw_command.type not in _ALLOWED_COMMAND_TYPES:
                return None
            return Command(
                type=raw_command.type,
                params=dict(raw_command.params),
                source="system",
                context=(
                    cls._normalize_mapping(raw_command.context)
                    if isinstance(raw_command.context, Mapping)
                    else None
                ),
            )

        if not isinstance(raw_command, Mapping):
            return None

        command_type = _coerce_non_empty_string(raw_command.get("type"))
        if command_type is None or command_type not in _ALLOWED_COMMAND_TYPES:
            return None

        params = cls._normalize_mapping(raw_command.get("params"))
        raw_context = raw_command.get("context")
        context = (
            cls._normalize_mapping(raw_context)
            if isinstance(raw_context, Mapping)
            else None
        )
        return Command(
            type=command_type,
            params=params,
            source="system",
            context=context,
        )

    @staticmethod
    def _apply_transitions(
        context: SettlementContext,
        active_events: Mapping[str, Mapping[str, Any]],
        transitions: list[EventTransition],
    ) -> tuple[list[dict[str, Any]], list[SSEEvent], int]:
        applied: list[dict[str, Any]] = []
        sse_events: list[SSEEvent] = []
        skipped_invalid_transition_count = 0
        known_events = {
            event_id: dict(event)
            for event_id, event in active_events.items()
        }

        for transition in transitions:
            if transition.event_id not in known_events:
                skipped_invalid_transition_count += 1
                continue
            if transition.to_state not in _SUPPORTED_STATES:
                skipped_invalid_transition_count += 1
                continue

            from_state = _canonical_state(known_events[transition.event_id])
            context.state.events.set_state(
                transition.event_id,
                transition.to_state,
                patch=transition.patch,
            )
            updated_event = context.state.events.get_event(transition.event_id) or {}
            known_events[transition.event_id] = updated_event
            reason = transition.reason or "state_changed"
            summary = {
                "event_id": transition.event_id,
                "from_state": from_state,
                "to_state": transition.to_state,
                "reason": reason,
            }
            applied.append(summary)
            sse_events.append(
                SSEEvent(
                    event_type="event_state_changed",
                    payload=dict(summary),
                )
            )

        return applied, sse_events, skipped_invalid_transition_count

    @staticmethod
    def _execute_commands(
        context: SettlementContext,
        commands: list[Command],
    ) -> tuple[list[dict[str, Any]], int]:
        results: list[dict[str, Any]] = []
        failed_count = 0
        for command in commands:
            result = context.execute_command(command)
            if not result.success:
                failed_count += 1
            applied_change_count = len(result.delta.changes) if result.delta is not None else 0
            results.append(
                {
                    "command_type": command.type,
                    "success": result.success,
                    "errors": list(result.errors),
                    "applied_change_count": applied_change_count,
                }
            )
        return results, failed_count

    @staticmethod
    def _resolve_status(
        *,
        transitioned_count: int,
        executed_count: int,
        failed_count: int,
    ) -> str:
        if transitioned_count == 0 and executed_count == 0:
            return "noop"
        if failed_count > 0:
            return "partial_failure"
        return "applied"

    @staticmethod
    def _normalize_mapping(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        return {str(key): raw_value for key, raw_value in value.items()}


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
