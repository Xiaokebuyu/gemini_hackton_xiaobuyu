"""Unified utterance orchestration for player speech-like interactions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Mapping, Protocol

from app.game_core import ManagedSession
from app.game_core.orchestration.interaction import (
    build_interaction_policy_context,
    validate_presence,
)
from app.game_core.orchestration.models import PipelineResult, SSEEvent
from app.game_core.result_semantics import outcome_passed
from app.game_core.rules.models import Command


UtteranceScope = Literal["public", "party"]
_UTTERANCE_INTENTS = {"talk", "greet", "ask", "chat"}


class _UtteranceAgentOrchestration(Protocol):
    async def run_npc_interaction(
        self,
        session: ManagedSession,
        npc_id: str,
        player_message: str,
        intent: str = "talk",
        text_chunk_sink: Callable[[str], Awaitable[None]] | None = None,
        check_result: dict[str, Any] | None = None,
    ) -> list[SSEEvent]:
        ...

    async def run_public_utterance(
        self,
        session: ManagedSession,
        player_message: str,
        intent: str = "talk",
        text_chunk_sink: Callable[[str], Awaitable[None]] | None = None,
        check_result: dict[str, Any] | None = None,
    ) -> list[SSEEvent]:
        ...

    async def run_free_chat(
        self,
        session: ManagedSession,
        player_message: str,
    ) -> list[SSEEvent]:
        ...


@dataclass(slots=True, frozen=True)
class UtteranceTarget:
    kind: Literal["npc"]
    id: str


@dataclass(slots=True, frozen=True)
class UtteranceRequest:
    text: str
    scope: UtteranceScope
    intent: str = "talk"
    focus_target: UtteranceTarget | None = None
    check_skill: str | None = None
    check_dc: int | None = None


@dataclass(slots=True, frozen=True)
class UtteranceExecutionResult:
    completed: bool
    reason: str
    events: list[SSEEvent] = field(default_factory=list)
    turn_kind: str | None = None
    turn_npc_id: str | None = None
    turn_scope: UtteranceScope | None = None
    time_cost: float = 1 / 6


def build_utterance_request(payload: Mapping[str, Any]) -> UtteranceRequest | None:
    """Build one normalized utterance request from an interact payload."""

    intent = str(payload.get("intent") or "").strip().casefold()
    if intent not in _UTTERANCE_INTENTS:
        return None

    text = str(payload.get("message") or "").strip()
    scope = _normalize_scope(payload.get("scope"))

    target_kind = str(payload.get("target_kind") or "").strip().casefold()
    target_id = str(payload.get("target_id") or payload.get("npc_id") or "").strip()
    focus_target: UtteranceTarget | None = None
    if target_kind == "npc" and target_id:
        focus_target = UtteranceTarget(kind="npc", id=target_id)
    elif not target_kind and target_id:
        focus_target = UtteranceTarget(kind="npc", id=target_id)

    if scope is None:
        if focus_target is not None:
            scope = "public"
        elif intent == "chat":
            scope = "party"
        elif text:
            scope = "public"
        else:
            return None

    if scope == "party" and focus_target is not None:
        return None
    if not text:
        return None

    check_skill = str(payload.get("check_skill") or "").strip() or None
    raw_dc = payload.get("check_dc")
    check_dc = raw_dc if isinstance(raw_dc, int) else None

    return UtteranceRequest(
        text=text,
        scope=scope,
        intent=intent or "talk",
        focus_target=focus_target,
        check_skill=check_skill if check_skill and check_dc is not None else None,
        check_dc=check_dc if check_skill and check_dc is not None else None,
    )


class UtteranceOrchestrator:
    """Single truth source for speech-like player utterances."""

    def __init__(self, agent_orchestration: _UtteranceAgentOrchestration | None) -> None:
        self._agent_orchestration = agent_orchestration

    async def execute(
        self,
        session: ManagedSession,
        utterance: UtteranceRequest,
        *,
        text_chunk_sink: Callable[[str], Awaitable[None]] | None = None,
    ) -> UtteranceExecutionResult:
        if self._agent_orchestration is None:
            return UtteranceExecutionResult(
                completed=False,
                reason="no_llm",
                events=[SSEEvent("stream_error", {"code": "no_llm"})],
            )

        if utterance.scope == "public" and utterance.focus_target is not None:
            issue = validate_presence(
                build_interaction_policy_context(session.runtime.state, session.runtime.world),
                "npc",
                utterance.focus_target.id,
                utterance.intent,
            )
            if issue is not None:
                return UtteranceExecutionResult(
                    completed=False,
                    reason="interaction_rejected",
                    events=[_interaction_rejected_event(utterance, issue)],
                )

        events: list[SSEEvent] = [_interaction_resolved_event(utterance)]
        check_result: dict[str, Any] | None = None
        if utterance.check_skill and utterance.check_dc is not None:
            check_events, check_result, check_reason = _execute_skill_check(session, utterance)
            events.extend(check_events)
            if check_reason is not None:
                return UtteranceExecutionResult(
                    completed=False,
                    reason=check_reason,
                    events=events,
                )

        agent_events: list[SSEEvent]
        turn_kind: str | None = None
        turn_npc_id: str | None = None
        reason = "completed"

        if utterance.scope == "public" and utterance.focus_target is not None:
            turn_kind = "dialogue_turn"
            turn_npc_id = utterance.focus_target.id
            agent_events = await self._agent_orchestration.run_npc_interaction(
                session=session,
                npc_id=utterance.focus_target.id,
                player_message=utterance.text,
                intent=utterance.intent,
                text_chunk_sink=text_chunk_sink,
                check_result=check_result,
            )
            if not agent_events:
                reason = "npc_not_found"
                events.append(SSEEvent(
                    "npc_error",
                    {"npc_id": utterance.focus_target.id, "code": "npc_not_found"},
                ))
                return UtteranceExecutionResult(
                    completed=False,
                    reason=reason,
                    events=events,
                )
            completed = not any(
                event.event_type in {"npc_error", "npc_response_error"}
                for event in agent_events
            )
            if not completed:
                reason = "dialogue_failed"
        elif utterance.scope == "party":
            turn_kind = "party_chat_turn"
            agent_events = await self._agent_orchestration.run_free_chat(
                session=session,
                player_message=utterance.text,
            )
            completed = not any(
                event.event_type in {"npc_error", "npc_response_error", "stream_error"}
                for event in agent_events
            )
            if not completed:
                reason = "party_chat_failed"
        else:
            turn_kind = "public_utterance_turn"
            agent_events = await self._agent_orchestration.run_public_utterance(
                session=session,
                player_message=utterance.text,
                intent=utterance.intent,
                text_chunk_sink=text_chunk_sink,
                check_result=check_result,
            )
            completed = not any(
                event.event_type in {"npc_error", "npc_response_error", "stream_error"}
                for event in agent_events
            )
            if not completed:
                reason = "public_utterance_failed"

        events.extend(agent_events)
        return UtteranceExecutionResult(
            completed=completed,
            reason=reason,
            events=events,
            turn_kind=turn_kind,
            turn_npc_id=turn_npc_id,
            turn_scope=utterance.scope,
        )


def _normalize_scope(raw_scope: Any) -> UtteranceScope | None:
    normalized = str(raw_scope or "").strip().casefold()
    if normalized in {"public", "party"}:
        return normalized  # type: ignore[return-value]
    return None


def _interaction_resolved_event(utterance: UtteranceRequest) -> SSEEvent:
    target_kind = utterance.focus_target.kind if utterance.focus_target is not None else (
        "party" if utterance.scope == "party" else ""
    )
    target_id = utterance.focus_target.id if utterance.focus_target is not None else (
        "party" if utterance.scope == "party" else ""
    )
    return SSEEvent(
        "interaction_resolved",
        {
            "target_kind": target_kind,
            "target_id": target_id,
            "intent": utterance.intent,
            "item_id": None,
            "quest_id": None,
            "count": 1,
            "scope": utterance.scope,
        },
    )


def _interaction_rejected_event(
    utterance: UtteranceRequest,
    issue: Mapping[str, Any],
) -> SSEEvent:
    target_kind = utterance.focus_target.kind if utterance.focus_target is not None else (
        "party" if utterance.scope == "party" else None
    )
    target_id = utterance.focus_target.id if utterance.focus_target is not None else (
        "party" if utterance.scope == "party" else None
    )
    return SSEEvent(
        "interaction_rejected",
        {
            "target_kind": target_kind,
            "target_id": target_id,
            "intent": utterance.intent,
            "item_id": None,
            "quest_id": None,
            "count": 1,
            "code": str(issue.get("code") or "interaction_failed"),
            "message": str(issue.get("message") or "interaction failed"),
        },
    )


def _execute_skill_check(
    session: ManagedSession,
    utterance: UtteranceRequest,
) -> tuple[list[SSEEvent], dict[str, Any] | None, str | None]:
    command = Command(
        type="skill_check",
        params={"skill": utterance.check_skill, "dc": utterance.check_dc},
        source="player",
    )
    exec_result = session.runtime.rules_engine.execute(
        command,
        session.runtime.state,
        session.runtime.world,
    )
    session.runtime.tick_coordinator.apply_external_result(exec_result)

    events: list[SSEEvent] = []
    check_pipeline_result = PipelineResult(
        executed=exec_result.executed,
        action_type="skill_check",
        time_cost=exec_result.time_cost,
        errors=list(exec_result.errors),
        narrative_hints=list(exec_result.narrative_hints),
        rolls=list(exec_result.rolls),
        metadata=dict(exec_result.metadata),
    )
    if exec_result.rolls:
        roll = exec_result.rolls[0]
        outcome = check_pipeline_result.metadata.get("outcome")
        passed = outcome_passed(outcome if isinstance(outcome, Mapping) else None)
        events.append(SSEEvent(
            "dice_roll",
            {
                "type": str(getattr(roll, "dice", "")),
                "result": _coerce_int(getattr(roll, "result", 0)),
                "modifier": _modifier_total(roll),
                "total": _coerce_int(getattr(roll, "total", 0)),
                "dc": utterance.check_dc,
                "passed": (
                    passed
                    if passed is not None else bool(exec_result.metadata.get("passed", False))
                ),
                "skill": utterance.check_skill or "skill_check",
                "roller": "player",
                "roller_name": session.runtime.state.player.character_name or "Player",
                "roll": _coerce_int(getattr(roll, "result", 0)),
                "narrative_hints": list(exec_result.narrative_hints),
            },
        ))

    outcome = exec_result.metadata.get("outcome")
    passed = outcome_passed(outcome if isinstance(outcome, Mapping) else None)
    check_result = {
        "skill": utterance.check_skill,
        "dc": utterance.check_dc,
        "passed": passed if passed is not None else bool(exec_result.metadata.get("passed", False)),
        "total": exec_result.rolls[0].total if exec_result.rolls else 0,
        "narrative_hints": list(exec_result.narrative_hints),
        "outcome": dict(outcome) if isinstance(outcome, Mapping) else None,
    }
    if not exec_result.executed:
        reason = exec_result.errors[0] if exec_result.errors else "check_execution_failed"
        return events, None, reason
    return events, check_result, None


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _modifier_total(roll: Any) -> int:
    total = 0
    for modifier in getattr(roll, "modifiers", []) or []:
        try:
            if isinstance(modifier, Mapping):
                total += int(modifier.get("value", 0))
            else:
                total += int(getattr(modifier, "value", 0))
        except (TypeError, ValueError):
            continue
    return total
