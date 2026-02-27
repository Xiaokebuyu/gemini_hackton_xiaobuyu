"""Presentation adapter protocols."""

from __future__ import annotations

from typing import Any, Protocol


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
