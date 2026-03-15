"""InstanceManager — per-actor NPCInstance LRU pool with session isolation."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Iterator

from app.game_core.narrative.context_window import ContextWindow, WindowMessage

_PRIORITY_MAP: dict[str, int] = {"high": 3, "medium": 2, "low": 1}


def _directive_priority(directive: dict[str, Any]) -> int:
    priority = directive.get("priority", 0)
    if isinstance(priority, str):
        return _PRIORITY_MAP.get(priority.lower(), 0)
    try:
        return int(priority)
    except (TypeError, ValueError):
        return 0


@dataclass(slots=True)
class PendingInstanceWriteback:
    """Messages collected from an evicted instance awaiting graph writeback."""

    actor_id: str
    messages: list[WindowMessage]
    reason: str = "lru_eviction"
    session_id: str = ""


@dataclass(slots=True)
class NPCInstance:
    """Runtime wrapper for one active NPC/teammate."""

    actor_id: str
    context_window: ContextWindow
    session_id: str = ""
    directive_queue: list[dict[str, Any]] = field(default_factory=list)
    last_interaction_tick: int = 0
    interaction_count: int = 0
    model_tier: str = "primary"
    thinking_level: str = "low"

    def record_interaction(self, current_tick: int) -> None:
        self.last_interaction_tick = max(self.last_interaction_tick, current_tick)
        self.interaction_count += 1

    def sync_directives(
        self,
        directives: list[dict[str, Any]] | None,
        *,
        current_tick: int,
    ) -> int:
        """Inject all active directives for this actor without duplicating refs."""
        added = 0
        if directives is not None:
            for directive in directives:
                if self.add_directive(directive, current_tick=current_tick):
                    added += 1
        self._prune_directives(current_tick)
        return added

    def add_directive(
        self,
        directive: dict[str, Any],
        *,
        current_tick: int,
    ) -> bool:
        if directive.get("npc_id") != self.actor_id:
            return False
        if directive.get("consumed", False):
            return False
        expires_at = self._directive_expiry(directive, current_tick=current_tick)
        if expires_at < current_tick:
            return False
        if any(existing is directive for existing in self.directive_queue):
            return False
        self.directive_queue.append(directive)
        return True

    def has_pending_directives(self, current_tick: int) -> bool:
        self._prune_directives(current_tick)
        return bool(self.directive_queue)

    def consume_directive(self, current_tick: int) -> dict[str, Any] | None:
        """Pop the highest-priority active directive and mark it consumed."""
        self._prune_directives(current_tick)
        if not self.directive_queue:
            return None
        best = max(self.directive_queue, key=_directive_priority)
        self.directive_queue = [item for item in self.directive_queue if item is not best]
        best["consumed"] = True
        return best

    def snapshot(self, current_tick: int | None = None) -> dict[str, Any]:
        active_queue = list(self.directive_queue)
        if current_tick is not None:
            self._prune_directives(current_tick)
            active_queue = list(self.directive_queue)
        return {
            "actor_id": self.actor_id,
            "interaction_count": self.interaction_count,
            "last_interaction_tick": self.last_interaction_tick,
            "directive_count": len(active_queue),
            "context_window": self.context_window.snapshot(),
            "model_tier": self.model_tier,
            "thinking_level": self.thinking_level,
        }

    def _prune_directives(self, current_tick: int) -> None:
        self.directive_queue = [
            directive
            for directive in self.directive_queue
            if not directive.get("consumed", False)
            and self._directive_expiry(directive, current_tick=current_tick) >= current_tick
        ]

    @staticmethod
    def _directive_expiry(
        directive: dict[str, Any],
        *,
        current_tick: int,
    ) -> int:
        raw_expiry = directive.get("expires_at_tick")
        if raw_expiry is None:
            return current_tick + 24
        try:
            return int(raw_expiry)
        except (TypeError, ValueError):
            return current_tick + 24


class InstanceManager:
    """LRU pool of active NPCInstance objects with per-session isolation.

    Pool keys are ``"{session_id}/{actor_id}"`` so different sessions can hold
    separate instances for the same actor without colliding.
    """

    def __init__(
        self,
        max_instances: int = 200,
        max_tokens_per_instance: int = 32_768,
        overflow_threshold: float = 0.9,
    ) -> None:
        self._pool: OrderedDict[str, NPCInstance] = OrderedDict()
        self._pending_writebacks: list[PendingInstanceWriteback] = []
        self._max_instances = max_instances
        self._max_tokens = max_tokens_per_instance
        self._overflow_threshold = overflow_threshold

    # ------------------------------------------------------------------
    # Internal key helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pool_key(session_id: str, actor_id: str) -> str:
        return f"{session_id}/{actor_id}"

    # ------------------------------------------------------------------
    # Public API (all methods take session_id as first positional arg)
    # ------------------------------------------------------------------

    def get_or_create(
        self,
        session_id: str,
        actor_id: str,
        npc_directives: list[dict[str, Any]] | None = None,
        current_tick: int = 0,
    ) -> NPCInstance:
        """Return an active instance, creating it if necessary."""
        key = self._pool_key(session_id, actor_id)
        if key in self._pool:
            instance = self._pool[key]
            self._pool.move_to_end(key)
            instance.sync_directives(npc_directives, current_tick=current_tick)
            instance.record_interaction(current_tick)
            return instance

        while len(self._pool) >= self._max_instances:
            victim_key = self._select_eviction_candidate(current_tick=current_tick)
            self._evict_by_key(victim_key, reason="lru_eviction")

        context_window = ContextWindow(
            actor_id=actor_id,
            max_tokens=self._max_tokens,
            overflow_threshold=self._overflow_threshold,
        )
        instance = NPCInstance(
            actor_id=actor_id,
            context_window=context_window,
            session_id=session_id,
        )
        instance.sync_directives(npc_directives, current_tick=current_tick)
        instance.record_interaction(current_tick)
        self._pool[key] = instance
        return instance

    def get(self, session_id: str, actor_id: str) -> NPCInstance | None:
        key = self._pool_key(session_id, actor_id)
        if key in self._pool:
            self._pool.move_to_end(key)
            return self._pool[key]
        return None

    def inject_directive(
        self,
        session_id: str,
        actor_id: str,
        directive: dict[str, Any],
        *,
        current_tick: int = 0,
    ) -> bool:
        """Hot-inject one directive into an already active instance."""
        key = self._pool_key(session_id, actor_id)
        instance = self._pool.get(key)
        if instance is None:
            return False
        self._pool.move_to_end(key)
        return instance.add_directive(directive, current_tick=current_tick)

    def evict(self, session_id: str, actor_id: str) -> NPCInstance | None:
        key = self._pool_key(session_id, actor_id)
        return self._evict_by_key(key, reason="manual_evict")

    def contains(self, session_id: str, actor_id: str) -> bool:
        return self._pool_key(session_id, actor_id) in self._pool

    def get_active_instances(
        self,
        session_id: str | None = None,
    ) -> list[NPCInstance]:
        if session_id is None:
            return list(self._pool.values())
        prefix = f"{session_id}/"
        return [inst for key, inst in self._pool.items() if key.startswith(prefix)]

    def iter_instances(
        self,
        session_id: str | None = None,
    ) -> Iterator[tuple[str, NPCInstance]]:
        """Yield (actor_id, instance) pairs for persistence.

        When *session_id* is provided, only instances belonging to that session
        are yielded and the actor_id (not the compound pool key) is returned.
        """
        if session_id is None:
            for key, instance in self._pool.items():
                yield instance.actor_id, instance
        else:
            prefix = f"{session_id}/"
            for key, instance in self._pool.items():
                if key.startswith(prefix):
                    yield instance.actor_id, instance

    def get_instance_stats(
        self,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if session_id is None:
            return {
                "count": len(self._pool),
                "max_instances": self._max_instances,
                "lru_order": list(self._pool.keys()),
                "pending_writebacks": len(self._pending_writebacks),
            }
        prefix = f"{session_id}/"
        session_keys = [k for k in self._pool if k.startswith(prefix)]
        return {
            "count": len(session_keys),
            "max_instances": self._max_instances,
            "lru_order": session_keys,
            "pending_writebacks": sum(
                1 for wb in self._pending_writebacks if wb.session_id == session_id
            ),
        }

    def drain_pending_writebacks(
        self,
        session_id: str | None = None,
    ) -> list[PendingInstanceWriteback]:
        if session_id is None:
            pending = list(self._pending_writebacks)
            self._pending_writebacks = []
            return pending
        pending = [wb for wb in self._pending_writebacks if wb.session_id == session_id]
        self._pending_writebacks = [
            wb for wb in self._pending_writebacks if wb.session_id != session_id
        ]
        return pending

    def clear_session(self, session_id: str) -> None:
        """Remove all pool entries and pending writebacks belonging to *session_id*."""
        prefix = f"{session_id}/"
        keys_to_remove = [k for k in self._pool if k.startswith(prefix)]
        for key in keys_to_remove:
            self._pool.pop(key, None)
        self._pending_writebacks = [
            wb for wb in self._pending_writebacks if wb.session_id != session_id
        ]

    def instance_count(self, session_id: str | None = None) -> int:
        if session_id is None:
            return len(self._pool)
        prefix = f"{session_id}/"
        return sum(1 for k in self._pool if k.startswith(prefix))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _select_eviction_candidate(self, *, current_tick: int) -> str:
        """Return the pool key of the best eviction candidate."""
        for pool_key, instance in self._pool.items():
            if not instance.has_pending_directives(current_tick):
                return pool_key
        return next(iter(self._pool))

    def _evict_by_key(self, pool_key: str, *, reason: str) -> NPCInstance | None:
        instance = self._pool.pop(pool_key, None)
        if instance is None:
            return None
        messages = instance.context_window.collect_for_graphize()
        if messages:
            self._pending_writebacks.append(
                PendingInstanceWriteback(
                    actor_id=instance.actor_id,
                    messages=messages,
                    reason=reason,
                    session_id=instance.session_id,
                )
            )
        return instance
