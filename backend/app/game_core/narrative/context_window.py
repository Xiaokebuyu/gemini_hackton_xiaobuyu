"""ContextWindow — in-process working memory for NPC/Teammate agents.

Tracks the message sequence fed to an LLM agent.  When ``current_tokens``
reaches ``overflow_threshold * max_tokens``, the caller should pop the oldest
messages and pass them to MemoryGraphizer (Phase 2-3) to free window space.

ContextWindow is intentionally limited to short-lived working memory. Instance-
level semantics such as directive queues belong to ``NPCInstance``.

Design reference: ❺ NPC与队友运行时规范 §四 (ContextWindow)
Decision record: D-N14-Phase1 (narrative.md)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class WindowMessage:
    """A single message in the context window.

    ``is_graphized`` is set to True by ``ContextWindow.pop_oldest_for_graphize``
    to mark messages that have been handed off to MemoryGraphizer.
    """

    role: str           # "user" | "assistant" | "system"
    content: str
    token_count: int
    metadata: dict[str, Any] = field(default_factory=dict)
    is_graphized: bool = False


@dataclass(slots=True)
class ContextWindow:
    """Working memory window for a single NPC or Teammate agent.

    Not thread-safe; callers are responsible for coordination (e.g.
    InstanceManager in Phase 3).

    Attributes:
        actor_id: Character ID this window belongs to.
        max_tokens: Hard cap (default 200 000, per design spec §四).
        overflow_threshold: Fraction at which graphization is triggered
            (default 0.9 — 90 % of max_tokens).
        messages: Ordered message list (oldest first).
        current_tokens: Running total of token_count across messages.
    """

    actor_id: str
    max_tokens: int = 200_000
    overflow_threshold: float = 0.9
    messages: list[WindowMessage] = field(default_factory=list)
    current_tokens: int = 0

    # ----------------------------------------------------------------
    # Properties
    # ----------------------------------------------------------------

    @property
    def usage_ratio(self) -> float:
        """Token utilisation as a fraction in [0.0, 1.0]."""
        if self.max_tokens == 0:
            return 0.0
        return self.current_tokens / self.max_tokens

    @property
    def should_graphize(self) -> bool:
        """True when the overflow threshold has been reached or exceeded."""
        return self.usage_ratio >= self.overflow_threshold

    @property
    def available_tokens(self) -> int:
        """Remaining capacity before hard cap."""
        return max(0, self.max_tokens - self.current_tokens)

    # ----------------------------------------------------------------
    # Mutation helpers
    # ----------------------------------------------------------------

    def add_message(self, message: WindowMessage) -> bool:
        """Append one message and update the token counter.

        Returns:
            True if the overflow threshold is now reached (caller should
            call ``pop_oldest_for_graphize`` and hand off to MemoryGraphizer).
        """
        self.messages.append(message)
        self.current_tokens += message.token_count
        return self.should_graphize

    def pop_oldest_for_graphize(self, fraction: float = 1 / 3) -> list[WindowMessage]:
        """Remove and return the oldest ``fraction`` of messages.

        Marks each returned message ``is_graphized = True`` so callers can
        track what has been handed off to MemoryGraphizer (Phase 2-3).

        Args:
            fraction: Fraction of current messages to remove (default 1/3,
                      per design spec — free ~33 % of the window).

        Returns:
            List of removed messages (oldest first), or empty list if the
            window is already empty.
        """
        if not self.messages:
            return []
        n = max(1, int(len(self.messages) * fraction))
        to_graphize = self.messages[:n]
        self.messages = self.messages[n:]
        self.current_tokens = sum(m.token_count for m in self.messages)
        for msg in to_graphize:
            msg.is_graphized = True
        return to_graphize

    # ----------------------------------------------------------------
    # Serialisation (Phase 2 persistence hook)
    # ----------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serialisable summary snapshot.

        Full message serialisation is deferred to Phase 2 when persistence
        is implemented.
        """
        return {
            "actor_id": self.actor_id,
            "max_tokens": self.max_tokens,
            "overflow_threshold": self.overflow_threshold,
            "current_tokens": self.current_tokens,
            "message_count": len(self.messages),
            "usage_ratio": round(self.usage_ratio, 4),
            "should_graphize": self.should_graphize,
        }
