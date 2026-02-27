"""Outbound adapter protocols."""

from __future__ import annotations

from typing import Any, Protocol


class OutputPort(Protocol):
    """Protocol for structured responses."""

    async def emit(self, payload: Any) -> None:
        """Emit one payload."""


class NullOutputPort:
    """No-op outbound adapter."""

    def __init__(self) -> None:
        self.last_payload: Any = None

    async def emit(self, payload: Any) -> None:
        self.last_payload = payload
