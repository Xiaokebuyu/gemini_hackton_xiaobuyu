"""Companion runtime pool for active teammate instances."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field

from app.game_core.narrative.context_window import ContextWindow

MAX_EVENT_LOG = 50


@dataclass(slots=True)
class TickRecord:
    """Structured tick observation captured for companion working memory."""

    tick: int
    action_type: str
    success: bool
    summary: str
    tags: list[str] = field(default_factory=list)
    involved_npcs: list[str] = field(default_factory=list)
    has_rolls: bool = False
    event_transitions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CompanionInstance:
    """Runtime wrapper for one active teammate."""

    actor_id: str
    context_window: ContextWindow
    last_interaction_tick: int = 0
    interaction_count: int = 0
    event_log: list[TickRecord] = field(default_factory=list)

    def record_interaction(self, current_tick: int) -> None:
        self.last_interaction_tick = max(self.last_interaction_tick, current_tick)
        self.interaction_count += 1

    def receive_tick(self, record: TickRecord) -> None:
        """Append one tick record and enforce the sliding window cap."""
        self.event_log.append(record)
        if len(self.event_log) > MAX_EVENT_LOG:
            self.event_log = self.event_log[-MAX_EVENT_LOG:]

    def get_recent_events(self, n: int = 10) -> list[TickRecord]:
        """Return the most recent n tick records."""
        if n <= 0:
            return []
        return list(self.event_log[-n:])

    def get_events_by_tag(self, tag: str) -> list[TickRecord]:
        """Return all tick records that contain the given tag."""
        return [record for record in self.event_log if tag in record.tags]


class CompanionRuntimeManager:
    """Session-scoped active teammate instance pool."""

    def __init__(
        self,
        max_instances: int = 8,
        max_tokens_per_instance: int = 80_000,
        overflow_threshold: float = 0.9,
    ) -> None:
        self._pool: OrderedDict[str, CompanionInstance] = OrderedDict()
        self._max_instances = max_instances
        self._max_tokens = max_tokens_per_instance
        self._overflow_threshold = overflow_threshold

    def sync_members(
        self,
        member_ids: list[str],
        *,
        current_tick: int = 0,
    ) -> None:
        active = {member_id for member_id in member_ids if member_id}
        for actor_id in list(self._pool.keys()):
            if actor_id not in active:
                self._pool.pop(actor_id, None)
        for actor_id in active:
            self.get_or_create(actor_id, current_tick=current_tick)

    def get_or_create(
        self,
        actor_id: str,
        *,
        current_tick: int = 0,
    ) -> CompanionInstance:
        instance = self._pool.get(actor_id)
        if instance is not None:
            self._pool.move_to_end(actor_id)
            instance.record_interaction(current_tick)
            return instance

        while len(self._pool) >= self._max_instances:
            self._pool.popitem(last=False)

        instance = CompanionInstance(
            actor_id=actor_id,
            context_window=ContextWindow(
                actor_id=actor_id,
                max_tokens=self._max_tokens,
                overflow_threshold=self._overflow_threshold,
            ),
        )
        instance.record_interaction(current_tick)
        self._pool[actor_id] = instance
        return instance

    def remove(self, actor_id: str) -> None:
        self._pool.pop(actor_id, None)

    def contains(self, actor_id: str) -> bool:
        return actor_id in self._pool

    def get(self, actor_id: str) -> CompanionInstance | None:
        instance = self._pool.get(actor_id)
        if instance is not None:
            self._pool.move_to_end(actor_id)
        return instance

    def dispatch_tick(self, record: TickRecord) -> None:
        """Push a tick record to all active companion instances."""
        for instance in self._pool.values():
            instance.receive_tick(record)

    def snapshot(self) -> list[dict[str, int | str]]:
        return [
            {
                "actor_id": instance.actor_id,
                "interaction_count": instance.interaction_count,
                "last_interaction_tick": instance.last_interaction_tick,
                "event_log_size": len(instance.event_log),
            }
            for instance in self._pool.values()
        ]
