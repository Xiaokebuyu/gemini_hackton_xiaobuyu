"""Presentation adapter protocols."""

from __future__ import annotations

import json
from typing import Any, Iterable, Protocol


class PresentationPort(Protocol):
    """Protocol for presentation/event streaming."""

    async def publish(self, event: Any) -> None:
        """Publish one presentation event."""


class NullPresentationPort:
    """No-op presentation adapter."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


def format_sse_event(event_type: str, payload: dict[str, Any]) -> str:
    """Format one event as SSE wire text."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"event: {event_type}\ndata: {encoded}\n\n"


class SSEPresentationPort:
    """SSE-capable presentation adapter.

    Buffers published events as SSE-formatted text chunks.
    Caller drains the buffer to build a StreamingResponse.
    """

    def __init__(self) -> None:
        self._buffer: list[str] = []

    async def publish(self, event: Any) -> None:
        """Accept an SSEEvent or duck-typed compatible object."""
        event_type = getattr(event, "event_type", None)
        payload = getattr(event, "payload", None)
        if event_type is None or payload is None:
            return
        self._buffer.append(self._format(str(event_type), dict(payload)))

    def publish_raw(self, event_type: str, payload: dict[str, Any]) -> None:
        """Publish a raw event_type + payload pair (for envelope events)."""
        self._buffer.append(self._format(event_type, payload))

    def publish_many(self, events: Iterable[Any]) -> None:
        """Publish a batch of duck-typed events."""
        for event in events:
            event_type = getattr(event, "event_type", None)
            payload = getattr(event, "payload", None)
            if event_type is not None and payload is not None:
                self._buffer.append(self._format(str(event_type), dict(payload)))

    def drain(self) -> list[str]:
        """Return and clear all buffered SSE chunks."""
        chunks = list(self._buffer)
        self._buffer.clear()
        return chunks

    @staticmethod
    def _format(event_type: str, payload: dict[str, Any]) -> str:
        """Format one event as SSE wire text."""
        return format_sse_event(event_type, payload)
