"""Tests for SSEPresentationPort — collect-drain SSE formatting adapter."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest

from app.game_core.adapters.presentation import SSEPresentationPort
from app.game_core.orchestration.models import SSEEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _DuckTypedEvent:
    """Mimics InteractionOutputEvent structure."""

    event_type: str
    payload: dict[str, Any]


def _parse_sse_chunk(chunk: str) -> tuple[str, dict[str, Any]]:
    """Parse one SSE chunk into (event_type, payload)."""
    lines = chunk.strip().split("\n")
    event_type = ""
    data = ""
    for line in lines:
        if line.startswith("event: "):
            event_type = line[len("event: "):]
        elif line.startswith("data: "):
            data = line[len("data: "):]
    return event_type, json.loads(data)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSSEPresentationPort:

    def test_publish_sse_event(self) -> None:
        port = SSEPresentationPort()
        event = SSEEvent(event_type="test_event", payload={"key": "value"})
        asyncio.run(port.publish(event))
        chunks = port.drain()
        assert len(chunks) == 1
        event_type, payload = _parse_sse_chunk(chunks[0])
        assert event_type == "test_event"
        assert payload == {"key": "value"}

    def test_publish_raw(self) -> None:
        port = SSEPresentationPort()
        port.publish_raw("action_result", {"success": True, "action_type": "move_area"})
        chunks = port.drain()
        assert len(chunks) == 1
        event_type, payload = _parse_sse_chunk(chunks[0])
        assert event_type == "action_result"
        assert payload["success"] is True
        assert payload["action_type"] == "move_area"

    def test_publish_many(self) -> None:
        port = SSEPresentationPort()
        events = [
            SSEEvent(event_type="ev1", payload={"a": 1}),
            SSEEvent(event_type="ev2", payload={"b": 2}),
            SSEEvent(event_type="ev3", payload={"c": 3}),
        ]
        port.publish_many(events)
        chunks = port.drain()
        assert len(chunks) == 3
        types = [_parse_sse_chunk(c)[0] for c in chunks]
        assert types == ["ev1", "ev2", "ev3"]

    def test_drain_clears_buffer(self) -> None:
        port = SSEPresentationPort()
        port.publish_raw("test", {"x": 1})
        first = port.drain()
        assert len(first) == 1
        second = port.drain()
        assert len(second) == 0

    def test_publish_ignores_invalid(self) -> None:
        port = SSEPresentationPort()
        # Missing payload
        asyncio.run(port.publish(type("Obj", (), {"event_type": "x"})()))
        # Missing event_type
        asyncio.run(port.publish(type("Obj", (), {"payload": {}})()))
        # None
        asyncio.run(port.publish(None))
        # String
        asyncio.run(port.publish("not an event"))
        assert port.drain() == []

    def test_publish_many_skips_invalid(self) -> None:
        port = SSEPresentationPort()
        events = [
            SSEEvent(event_type="valid", payload={"ok": True}),
            "not an event",
            None,
            SSEEvent(event_type="also_valid", payload={"ok": True}),
        ]
        port.publish_many(events)
        chunks = port.drain()
        assert len(chunks) == 2

    def test_format_matches_legacy(self) -> None:
        """SSE format must match the original _format_sse_event output exactly."""
        port = SSEPresentationPort()
        payload = {"name": "测试", "value": 42, "nested": {"a": [1, 2]}}
        port.publish_raw("test_event", payload)
        chunk = port.drain()[0]
        # Must use ensure_ascii=False and compact separators
        expected_data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        assert chunk == f"event: test_event\ndata: {expected_data}\n\n"

    def test_duck_typed_event(self) -> None:
        """InteractionOutputEvent (frozen dataclass) should work via duck typing."""
        port = SSEPresentationPort()
        event = _DuckTypedEvent(event_type="talk_snapshot", payload={"npc": "merchant"})
        asyncio.run(port.publish(event))
        chunks = port.drain()
        assert len(chunks) == 1
        event_type, payload = _parse_sse_chunk(chunks[0])
        assert event_type == "talk_snapshot"
        assert payload == {"npc": "merchant"}

    def test_mixed_publish_ordering(self) -> None:
        """Raw, duck-typed, and batch publishes maintain insertion order."""
        port = SSEPresentationPort()
        port.publish_raw("first", {"seq": 1})
        asyncio.run(port.publish(SSEEvent(event_type="second", payload={"seq": 2})))
        port.publish_many([
            SSEEvent(event_type="third", payload={"seq": 3}),
            SSEEvent(event_type="fourth", payload={"seq": 4}),
        ])
        port.publish_raw("fifth", {"seq": 5})
        chunks = port.drain()
        assert len(chunks) == 5
        for i, chunk in enumerate(chunks, 1):
            _, payload = _parse_sse_chunk(chunk)
            assert payload["seq"] == i
