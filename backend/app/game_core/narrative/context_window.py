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

    ``parts`` stores the original structured Gemini parts list for model
    messages (e.g. function_call parts).  When present, it is preferred over
    the plain-text ``content`` when reconstructing conversation history so
    that the LLM sees "I previously called tool X" rather than just text.
    User messages always use plain text; set ``parts=None`` for them.
    """

    role: str           # "user" | "assistant" | "system"
    content: str
    token_count: int
    metadata: dict[str, Any] = field(default_factory=dict)
    is_graphized: bool = False
    parts: list[dict[str, Any]] | None = None  # structured parts (model only)


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

    # ----------------------------------------------------------------
    # Serialisation
    # ----------------------------------------------------------------

    def export_messages(self) -> dict[str, Any]:
        """Serialize the context window to a JSON-compatible dict.

        Returns a dict with keys:
            - ``messages``: serialized WindowMessage list
            - ``graphize_counter``: current graphize counter (for persistence)
        """
        rows = []
        for m in self.messages:
            row: dict[str, Any] = {
                "role": m.role,
                "content": m.content,
                "token_count": m.token_count,
                "metadata": dict(m.metadata),
                "is_graphized": m.is_graphized,
            }
            if m.parts is not None:
                row["parts"] = list(m.parts)
            rows.append(row)
        return {
            "messages": rows,
            "graphize_counter": self.graphize_counter,
        }

    def import_messages(self, data: dict[str, Any] | list[dict[str, Any]]) -> None:
        """Restore messages from a previously exported dict or legacy list.

        Accepts both the new dict format (from export_messages) and the
        legacy list format (plain list of message dicts) for backward compat.

        Restores graphize_counter from the dict format; defaults to 0 for
        legacy list format.
        """
        if isinstance(data, dict):
            raw_messages = data.get("messages") or []
            self.graphize_counter = int(data.get("graphize_counter", 0))
        else:
            raw_messages = data or []
            self.graphize_counter = 0

        self.messages = []
        for entry in raw_messages:
            if not isinstance(entry, dict):
                continue
            role = str(entry.get("role", "user"))
            content = str(entry.get("content", ""))
            token_count = int(entry.get("token_count", max(1, len(content) // 4)))
            metadata = entry.get("metadata")
            is_graphized = bool(entry.get("is_graphized", False))
            raw_parts = entry.get("parts")
            parts: list[dict[str, Any]] | None = None
            if isinstance(raw_parts, list):
                parts = [p for p in raw_parts if isinstance(p, dict)]
            self.messages.append(WindowMessage(
                role=role,
                content=content,
                token_count=token_count,
                metadata=dict(metadata) if isinstance(metadata, dict) else {},
                is_graphized=is_graphized,
                parts=parts,
            ))
        self.current_tokens = sum(m.token_count for m in self.messages)

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


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def window_to_history(window: ContextWindow) -> list[dict[str, Any]]:
    """Convert ContextWindow messages to Gemini conversation history format.

    Skips messages flagged as graphized (they have been compressed into the
    knowledge graph and should not be re-sent to the LLM).

    When a WindowMessage has a ``parts`` list (i.e. a model message that
    contains function_call entries), those structured parts are used directly
    so the model sees its own prior tool invocations.  Otherwise the plain
    ``content`` text is wrapped in a single text part.
    """
    result: list[dict[str, Any]] = []
    for msg in window.messages:
        if msg.is_graphized:
            continue
        if msg.parts is not None:
            result.append({"role": msg.role, "parts": list(msg.parts)})
        else:
            result.append({"role": msg.role, "parts": [{"text": msg.content}]})
    return result
