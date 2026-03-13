"""PlannerSubSystem protocol + PlannerDispatcher.

Provides the plug-in architecture for Planner sub-systems.  Each sub-system
handles a disjoint set of event kinds; the dispatcher routes PlannerEvents to
sub-systems, enforces a per-sub-system busy-lock, and drains a bounded queue
after each evaluate() completes.

Decision record: D-P20a (narrative.md)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PlannerEvent:
    """A discrete event dispatched to planner sub-systems."""

    kind: str  # "tick_settlement" | "milestone_completed" | "quest_accepted" | …
    tick: int
    source: str = ""
    priority: int = 0
    dedupe_key: str = ""
    round_index: int = 0
    emitter: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SubSystemResult:
    """Aggregate output from one sub-system evaluate() call."""

    directives: list[Any] = field(default_factory=list)
    story_facts: list[dict[str, Any]] = field(default_factory=list)
    strategy_notes: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class PlannerSubSystem(Protocol):
    """Contract that every planner sub-system must satisfy."""

    @property
    def name(self) -> str:
        """Unique sub-system identifier."""
        ...

    @property
    def handles(self) -> frozenset[str]:
        """Set of directive kinds this sub-system is authoritative for."""
        ...

    def accepts_event(self, event: PlannerEvent) -> bool:
        """Return True if this sub-system wants to react to *event*."""
        ...

    async def evaluate(
        self, event: PlannerEvent, context: Any
    ) -> SubSystemResult:
        """Produce directives / facts for this event.

        This method is guarded by the dispatcher's busy-lock; it will not be
        called concurrently for the same sub-system.
        """
        ...

    def apply_directive(
        self,
        kind: str,
        payload: dict[str, Any],
        context: Any,
        *,
        current_tick: int,
    ) -> bool | str:
        """Apply a single directive of *kind*.

        Returns True when the directive was successfully applied.
        Returns a non-empty string (reason code) when it was rejected
        (e.g. invalid payload, command failed).
        """
        ...


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class PlannerDispatcher:
    """Routes PlannerEvents to registered sub-systems.

    Busy semantics:
    - A sub-system that is mid-evaluate is marked busy.
    - New events targeting a busy sub-system are queued.
    - The queue is drained (FIFO, in the same call stack) after each evaluate.
    - Events that overflow the queue cap are silently dropped.

    apply_directive() finds the *first* sub-system whose ``handles`` frozenset
    contains the directive kind and delegates to it.
    """

    _MAX_QUEUE_DEPTH: ClassVar[int] = 3
    _MAX_DIRECTIVE_DEPTH: ClassVar[int] = 6

    def __init__(self) -> None:
        self._subsystems: list[PlannerSubSystem] = []
        self._busy: dict[str, bool] = {}          # name → busy
        self._queue: dict[str, list[PlannerEvent]] = {}  # name → pending
        self._directive_stack: list[str] = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, subsystem: PlannerSubSystem) -> None:
        """Add a sub-system to the dispatcher."""
        name = subsystem.name
        if any(s.name == name for s in self._subsystems):
            logger.warning(
                "PlannerDispatcher: duplicate sub-system name %r — replacing", name
            )
            self._subsystems = [s for s in self._subsystems if s.name != name]
        self._subsystems.append(subsystem)
        self._busy.setdefault(name, False)
        self._queue.setdefault(name, [])

    @property
    def subsystems(self) -> tuple[PlannerSubSystem, ...]:
        """Registered sub-systems in dispatch order."""
        return tuple(self._subsystems)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def dispatch(
        self, event: PlannerEvent, context: Any
    ) -> list[SubSystemResult]:
        """Route *event* to all accepting sub-systems.

        Returns the list of SubSystemResults collected this call.
        """
        results: list[SubSystemResult] = []
        for subsystem in self._subsystems:
            if not subsystem.accepts_event(event):
                continue
            name = subsystem.name
            if self._busy.get(name):
                self._enqueue(name, event)
            else:
                result = await self._run_and_drain(subsystem, event, context)
                results.extend(result)
        return results

    async def _run_and_drain(
        self,
        subsystem: PlannerSubSystem,
        event: PlannerEvent,
        context: Any,
    ) -> list[SubSystemResult]:
        """Execute evaluate() for *subsystem* and drain its queue afterwards."""
        results: list[SubSystemResult] = []
        name = subsystem.name
        self._busy[name] = True
        try:
            result = await subsystem.evaluate(event, context)
            metadata = dict(result.metadata) if isinstance(result.metadata, dict) else {}
            metadata.setdefault("subsystem", name)
            metadata.setdefault("event_kind", event.kind)
            result.metadata = metadata
            results.append(result)
        except Exception:
            logger.exception(
                "PlannerDispatcher: sub-system %r raised during evaluate()", name
            )
        finally:
            self._busy[name] = False

        # Drain queue (process queued events one at a time)
        queue = self._queue.get(name, [])
        while queue:
            queued_event = queue.pop(0)
            self._busy[name] = True
            try:
                result = await subsystem.evaluate(queued_event, context)
                metadata = dict(result.metadata) if isinstance(result.metadata, dict) else {}
                metadata.setdefault("subsystem", name)
                metadata.setdefault("event_kind", queued_event.kind)
                result.metadata = metadata
                results.append(result)
            except Exception:
                logger.exception(
                    "PlannerDispatcher: sub-system %r raised during drain", name
                )
            finally:
                self._busy[name] = False

        return results

    def _enqueue(self, name: str, event: PlannerEvent) -> None:
        """Enqueue *event* for *name*; drop if over depth limit."""
        queue = self._queue.setdefault(name, [])
        if len(queue) >= self._MAX_QUEUE_DEPTH:
            logger.debug(
                "PlannerDispatcher: queue full for %r, dropping event kind=%r",
                name,
                event.kind,
            )
            return
        queue.append(event)

    # ------------------------------------------------------------------
    # Directive application
    # ------------------------------------------------------------------

    def apply_directive(
        self,
        kind: str,
        payload: dict[str, Any],
        context: Any,
        *,
        current_tick: int,
    ) -> bool | str:
        """Delegate a directive to the first sub-system that handles *kind*.

        Returns True when a sub-system accepted the directive.
        Returns a non-empty reason string when rejected (cyclic call, depth
        overflow, no handler, or sub-system rejection).
        """
        for subsystem in self._subsystems:
            if kind not in subsystem.handles:
                continue
            target = subsystem.name
            if target in self._directive_stack:
                logger.debug(
                    "PlannerDispatcher.apply_directive: blocked cyclic call %s -> %s for kind=%r",
                    " -> ".join(self._directive_stack) or "(root)",
                    target,
                    kind,
                )
                return "cyclic_call_blocked"
            if len(self._directive_stack) >= self._MAX_DIRECTIVE_DEPTH:
                logger.debug(
                    "PlannerDispatcher.apply_directive: blocked depth overflow stack=%s kind=%r",
                    " -> ".join(self._directive_stack),
                    kind,
                )
                return "depth_overflow"
            self._directive_stack.append(target)
            try:
                return subsystem.apply_directive(
                    kind, payload, context, current_tick=current_tick
                )
            finally:
                if self._directive_stack and self._directive_stack[-1] == target:
                    self._directive_stack.pop()
        logger.debug(
            "PlannerDispatcher.apply_directive: no sub-system handles kind=%r", kind
        )
        return "no_handler_for_kind"
