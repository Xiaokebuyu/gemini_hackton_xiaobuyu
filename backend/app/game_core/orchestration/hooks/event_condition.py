"""EventConditionHook — settlement hook that applies event state transitions."""

from __future__ import annotations

import logging
from typing import Any, Mapping

from app.game_core.orchestration.event_engine import (
    BasicEventConditionEvaluator,
    EventConditionDecision,
    EventConditionEvaluator,
    EventTransition,
    _ALLOWED_COMMAND_TYPES,
    _SUPPORTED_STATES,
    _canonical_state,
    _coerce_int,
    _coerce_non_empty_string,
    _coerce_string,
    _normalize_mapping,
    _normalize_state_name,
)
from app.game_core.orchestration.hooks.base import NoOpSettlementHook
from app.game_core.orchestration.models import HookResult, SSEEvent
from app.game_core.orchestration.settlement import SettlementContext
from app.game_core.rules.models import Command

logger = logging.getLogger(__name__)


class EventConditionHook(NoOpSettlementHook):
    HOOK_PRIORITY = 50
    HOOK_NAME = "event_conditions"

    def __init__(self, evaluator: EventConditionEvaluator | None = None) -> None:
        self._evaluator = evaluator or BasicEventConditionEvaluator()

    def should_skip(
        self,
        change_log: list[Any],
        action_log: list[dict[str, Any]] | None = None,
    ) -> bool:
        del change_log
        del action_log
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
                metadata=_normalize_mapping(raw_decision.metadata),
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
            metadata=_normalize_mapping(raw_decision.get("metadata")),
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
        patch = _normalize_mapping(raw_patch) if isinstance(raw_patch, Mapping) else {}
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
                    _normalize_mapping(raw_command.context)
                    if isinstance(raw_command.context, Mapping)
                    else None
                ),
            )

        if not isinstance(raw_command, Mapping):
            return None

        command_type = _coerce_non_empty_string(raw_command.get("type"))
        if command_type is None or command_type not in _ALLOWED_COMMAND_TYPES:
            return None

        params = _normalize_mapping(raw_command.get("params"))
        raw_context = raw_command.get("context")
        context = (
            _normalize_mapping(raw_context)
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
            patch = dict(transition.patch)
            patch.setdefault("from_state", from_state)
            patch.setdefault("reason", transition.reason)
            transition_result = context.execute_command(Command(
                type="transition_event_state",
                params={
                    "event_id": transition.event_id,
                    "to_state": transition.to_state,
                    "patch": patch,
                },
                source="system",
            ))
            if not transition_result.executed:
                skipped_invalid_transition_count += 1
                continue
            updated_event = context.state.events.get_event(transition.event_id) or {}
            known_events[transition.event_id] = updated_event
            reason = transition.reason or "state_changed"
            summary = {
                "event_id": transition.event_id,
                "from_state": from_state,
                "to_state": transition.to_state,
                "reason": reason,
            }
            title = EventConditionHook._event_display_title(updated_event)
            if title is not None:
                summary["title"] = title
            applied.append(summary)
            sse_events.append(
                SSEEvent(
                    event_type="event_state_changed",
                    payload=dict(summary),
                )
            )

        return applied, sse_events, skipped_invalid_transition_count

    @staticmethod
    def _event_display_title(event: Mapping[str, Any]) -> str | None:
        for key in ("title", "name", "label"):
            raw = event.get(key)
            if raw is None:
                continue
            normalized = str(raw).strip()
            if normalized:
                return normalized
        return None

    @staticmethod
    def _execute_commands(
        context: SettlementContext,
        commands: list[Command],
    ) -> tuple[list[dict[str, Any]], int]:
        results: list[dict[str, Any]] = []
        failed_count = 0
        for command in commands:
            result = context.execute_command(command)
            if not result.executed:
                failed_count += 1
            applied_change_count = len(result.delta.changes) if result.delta is not None else 0
            results.append(
                {
                    "command_type": command.type,
                    "executed": result.executed,
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
