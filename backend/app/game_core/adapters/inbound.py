"""Inbound adapter protocols."""

from __future__ import annotations

from typing import Any, Protocol


class InputPort(Protocol):
    """Protocol for inbound requests."""

    async def process_text(self, text: str) -> Any:
        """Handle free-text input."""

    async def process_action(self, action: dict[str, Any]) -> Any:
        """Handle structured action input."""


class NullInputPort:
    """No-op inbound adapter."""

    async def process_text(self, text: str) -> Any:
        return {"status": "stub", "text": text}

    async def process_action(self, action: dict[str, Any]) -> Any:
        return {"status": "stub", "action": dict(action)}
