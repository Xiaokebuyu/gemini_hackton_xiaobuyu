"""InstanceManager — per-actor ContextWindow LRU pool.

Maintains a bounded in-memory pool of ContextWindow instances keyed by
actor_id (NPC or teammate character ID).  When the pool is full the
least-recently-used window is silently evicted.

Phase 3a: in-memory only (no persistence).
Phase 3c: add snapshot → save_store hook on eviction.

Decision record: D-N16 (narrative.md)
"""

from __future__ import annotations

from collections import OrderedDict

from app.game_core.narrative.context_window import ContextWindow


class InstanceManager:
    """LRU pool of per-actor ContextWindow instances.

    Args:
        max_instances: Maximum number of active NPC/teammate windows in memory.
            When this limit is reached the least-recently-used window is
            evicted to make room for the new one.
        max_tokens_per_instance: Token budget forwarded to each new
            ContextWindow (default 200 000, matching Gemini 1.5 context).
        overflow_threshold: Fraction of max_tokens that triggers a graphize
            signal in each ContextWindow (default 0.9 = 90 %).
    """

    def __init__(
        self,
        max_instances: int = 200,
        max_tokens_per_instance: int = 200_000,
        overflow_threshold: float = 0.9,
    ) -> None:
        self._pool: OrderedDict[str, ContextWindow] = OrderedDict()
        self._max_instances = max_instances
        self._max_tokens = max_tokens_per_instance
        self._overflow_threshold = overflow_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_or_create(self, actor_id: str) -> ContextWindow:
        """Return the existing ContextWindow for *actor_id* or create one.

        Accessing an existing window promotes it to MRU (most-recently-used)
        position, postponing its eviction.  When the pool is at capacity,
        the LRU window is removed before inserting the new one.
        """
        if actor_id in self._pool:
            self._pool.move_to_end(actor_id)
            return self._pool[actor_id]

        # Evict until we have room for the new entry.
        while len(self._pool) >= self._max_instances:
            self._pool.popitem(last=False)  # removes LRU (first-inserted) item

        window = ContextWindow(
            actor_id=actor_id,
            max_tokens=self._max_tokens,
            overflow_threshold=self._overflow_threshold,
        )
        self._pool[actor_id] = window
        return window

    def get(self, actor_id: str) -> ContextWindow | None:
        """Return the existing ContextWindow or *None* without creating one.

        Promotes to MRU on hit, same as :meth:`get_or_create`.
        """
        if actor_id in self._pool:
            self._pool.move_to_end(actor_id)
            return self._pool[actor_id]
        return None

    def contains(self, actor_id: str) -> bool:
        """Return *True* if *actor_id* has an active window in the pool."""
        return actor_id in self._pool

    def instance_count(self) -> int:
        """Return the current number of windows in the pool."""
        return len(self._pool)
