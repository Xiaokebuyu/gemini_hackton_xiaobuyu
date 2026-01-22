"""
Simple in-memory event bus.
"""
import asyncio
from typing import Awaitable, Callable, Dict, List

from app.models.event import Event, EventType

EventHandler = Callable[[Event], Awaitable[None] | None]


class EventBus:
    """In-memory pub/sub for events."""

    def __init__(self) -> None:
        self._subscribers: Dict[EventType, List[EventHandler]] = {}

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        self._subscribers.setdefault(event_type, []).append(handler)

    async def publish(self, event: Event) -> None:
        handlers = list(self._subscribers.get(event.type, []))
        for handler in handlers:
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result
