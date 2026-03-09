"""ContextWindow — in-process working memory for NPC/Teammate agents.

Tracks the message sequence fed to an LLM agent.  Uses a FIFO sliding window
(max_tokens=32_768 by default): when tokens exceed the cap, the oldest messages
are evicted automatically on add_message().

A separate graphize_counter accumulates tokens across resets.  When it reaches
graphize_threshold (default 32_768), the caller can collect all un-graphized
messages via collect_for_graphize() and hand them to write_episode().

ContextWindow is intentionally limited to short-lived working memory. Instance-
level semantics such as directive queues belong to ``NPCInstance``.

Design reference: ❺ NPC与队友运行时规范 §四 (ContextWindow)
Decision record: D-N14-Phase1, D-P19c (narrative.md)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class WindowMessage:
    """A single message in the context window.

    ``is_graphized`` is set to True by ``collect_for_graphize`` to mark
    messages that have been handed off to MemoryGraphizer.
    """

    role: str           # "user" | "assistant" | "system"
    content: str
    token_count: int
    metadata: dict[str, Any] = field(default_factory=dict)
    is_graphized: bool = False


@dataclass(slots=True)
class ContextWindow:
    """Working memory window for a single NPC or Teammate agent.

    FIFO sliding window: oldest messages are evicted when current_tokens
    exceeds max_tokens.  A separate graphize_counter tracks cumulative tokens
    written since the last collect_for_graphize() call.

    Not thread-safe; callers are responsible for coordination.

    Attributes:
        actor_id: Character ID this window belongs to.
        max_tokens: FIFO cap (default 32_768).
        overflow_threshold: Legacy threshold kept for API compatibility (unused
            in FIFO mode; should_graphize is driven by graphize_counter).
        messages: Ordered message list (oldest first).
        current_tokens: Running total across active messages.
        graphize_counter: Cumulative tokens since last collect_for_graphize().
        graphize_threshold: Trigger threshold for graphization (default 32_768).
    """

    actor_id: str
    max_tokens: int = 32_768
    overflow_threshold: float = 0.9
    messages: list[WindowMessage] = field(default_factory=list)
    current_tokens: int = 0
    graphize_counter: int = 0
    graphize_threshold: int = 32_768

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
        """True when the graphize counter has reached or exceeded the threshold."""
        return self.graphize_counter >= self.graphize_threshold

    @property
    def available_tokens(self) -> int:
        """Remaining capacity before hard cap."""
        return max(0, self.max_tokens - self.current_tokens)

    # ----------------------------------------------------------------
    # Mutation helpers
    # ----------------------------------------------------------------

    def add_message(self, message: WindowMessage) -> bool:
        """Append one message, update counters, and FIFO-evict if needed.

        Returns:
            True if should_graphize is now True (caller may want to call
            collect_for_graphize and hand off to write_episode).
        """
        self.messages.append(message)
        self.current_tokens += message.token_count
        self.graphize_counter += message.token_count

        # FIFO eviction: remove oldest messages until within cap
        while self.current_tokens > self.max_tokens and len(self.messages) > 1:
            oldest = self.messages.pop(0)
            self.current_tokens -= oldest.token_count

        return self.should_graphize

    def collect_for_graphize(self) -> list[WindowMessage]:
        """Return all un-graphized messages and mark them as graphized.

        Resets graphize_counter to 0.  Messages are NOT removed from the
        window — they stay in the FIFO buffer until evicted by add_message().

        Returns:
            List of previously un-graphized WindowMessage objects.
        """
        candidates = [m for m in self.messages if not m.is_graphized]
        for msg in candidates:
            msg.is_graphized = True
        self.graphize_counter = 0
        return candidates

    def pop_oldest_for_graphize(self, fraction: float = 1 / 3) -> list[WindowMessage]:
        """Remove and return the oldest ``fraction`` of messages.

        Deprecated: kept for backward compatibility with InstanceManager eviction.
        Prefer collect_for_graphize() for the new graphize-counter-driven path.

        Marks each returned message ``is_graphized = True``.
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
    # Serialisation
    # ----------------------------------------------------------------

    def export_messages(self) -> list[dict[str, Any]]:
        """Serialize all messages to a JSON-compatible list."""
        return [
            {
                "role": m.role,
                "content": m.content,
                "token_count": m.token_count,
                "metadata": dict(m.metadata),
                "is_graphized": m.is_graphized,
            }
            for m in self.messages
        ]

    def import_messages(self, data: list[dict[str, Any]]) -> None:
        """Restore messages from a previously exported list.

        Replaces the current message list and recalculates current_tokens.
        graphize_counter is NOT restored — starts fresh after a reload.
        """
        self.messages = []
        for entry in (data or []):
            if not isinstance(entry, dict):
                continue
            role = str(entry.get("role", "user"))
            content = str(entry.get("content", ""))
            token_count = int(entry.get("token_count", max(1, len(content) // 4)))
            metadata = entry.get("metadata")
            is_graphized = bool(entry.get("is_graphized", False))
            self.messages.append(WindowMessage(
                role=role,
                content=content,
                token_count=token_count,
                metadata=dict(metadata) if isinstance(metadata, dict) else {},
                is_graphized=is_graphized,
            ))
        self.current_tokens = sum(m.token_count for m in self.messages)
        self.graphize_counter = 0

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serialisable summary snapshot."""
        return {
            "actor_id": self.actor_id,
            "max_tokens": self.max_tokens,
            "overflow_threshold": self.overflow_threshold,
            "current_tokens": self.current_tokens,
            "message_count": len(self.messages),
            "usage_ratio": round(self.usage_ratio, 4),
            "should_graphize": self.should_graphize,
            "graphize_counter": self.graphize_counter,
        }
