"""End-to-end game session tests: create → 10 tick lifecycle.

All tests use asyncio.run() — no pytest-asyncio dependency required.
"""

from __future__ import annotations

import asyncio

from app.game_core.bootstrap import build_default_runtime
from app.game_core.orchestration.models import SSEEvent

MINIMAL_WORLD_DATA = {
    "maps": {
        "town": {
            "id": "town",
            "name": "Town",
            "is_starting_area": True,
            "sub_locations": {"tavern": {"id": "tavern", "name": "Tavern"}},
        },
        "forest": {"id": "forest", "name": "Forest"},
    },
}


class TestE2EGameSession:
    def test_10_ticks_no_crash(self) -> None:
        """10 set_flag actions — all succeed, no exceptions."""

        async def _run() -> None:
            runtime = build_default_runtime("test_world", world_data=MINIMAL_WORLD_DATA)
            for i in range(10):
                result = await runtime.tick_coordinator.process(
                    {"action_type": "set_flag", "params": {"key": f"flag_{i}", "value": True}}
                )
                assert result.success is True

        asyncio.run(_run())

    def test_time_advances_over_10_ticks(self) -> None:
        """10 rest_short (time_cost=1.0) — absolute_tick advances by 10."""

        async def _run() -> None:
            runtime = build_default_runtime("test_world", world_data=MINIMAL_WORLD_DATA)
            tick_before = runtime.state.time.absolute_tick()
            for _ in range(10):
                await runtime.tick_coordinator.process(
                    {"action_type": "rest_short", "params": {}}
                )
            tick_after = runtime.state.time.absolute_tick()
            assert tick_after == tick_before + 10

        asyncio.run(_run())

    def test_settlement_triggers_at_least_once(self) -> None:
        """rest_short triggers settlement → time_advanced SSE event produced."""

        async def _run() -> None:
            runtime = build_default_runtime("test_world", world_data=MINIMAL_WORLD_DATA)
            all_events: list[SSEEvent] = []

            async def collect(event: SSEEvent) -> None:
                all_events.append(event)

            for _ in range(3):
                await runtime.tick_coordinator.process(
                    {"action_type": "rest_short", "params": {}},
                    event_sink=collect,
                )

            event_types = {e.event_type for e in all_events}
            assert "time_advanced" in event_types

        asyncio.run(_run())

    def test_state_mutates_correctly(self) -> None:
        """set_flag mutations are persisted in FlagSlice after each tick."""

        async def _run() -> None:
            runtime = build_default_runtime("test_world", world_data=MINIMAL_WORLD_DATA)
            for i in range(5):
                result = await runtime.tick_coordinator.process(
                    {"action_type": "set_flag", "params": {"key": f"quest_{i}_done", "value": True}}
                )
                assert result.success is True

            for i in range(5):
                assert runtime.state.flags.flags.get(f"quest_{i}_done") is True

        asyncio.run(_run())

    def test_mixed_actions_sequence(self) -> None:
        """move_area → set_flag → rest_short → skill_check — no crash, tick advances."""

        async def _run() -> None:
            runtime = build_default_runtime("test_world", world_data=MINIMAL_WORLD_DATA)
            tick_before = runtime.state.time.absolute_tick()

            r1 = await runtime.tick_coordinator.process(
                {"action_type": "move_area", "params": {"area_id": "forest"}}
            )
            r2 = await runtime.tick_coordinator.process(
                {"action_type": "set_flag", "params": {"key": "entered_forest", "value": True}}
            )
            r3 = await runtime.tick_coordinator.process(
                {"action_type": "rest_short", "params": {}}
            )
            r4 = await runtime.tick_coordinator.process(
                {"action_type": "skill_check", "params": {"skill": "perception", "dc": 10}}
            )

            # All must produce a result (not crash)
            for result in (r1, r2, r3, r4):
                assert result is not None

            # At least rest_short contributed 1.0 time_cost → tick must advance
            tick_after = runtime.state.time.absolute_tick()
            assert tick_after > tick_before

        asyncio.run(_run())

    def test_event_sink_receives_sse_events(self) -> None:
        """event_sink collects settlement SSE events (time_advanced from rest_short)."""

        async def _run() -> None:
            runtime = build_default_runtime("test_world", world_data=MINIMAL_WORLD_DATA)
            collected: list[SSEEvent] = []

            async def sink(event: SSEEvent) -> None:
                collected.append(event)

            await runtime.tick_coordinator.process(
                {"action_type": "rest_short", "params": {}},
                event_sink=sink,
            )

            assert len(collected) > 0
            assert any(e.event_type == "time_advanced" for e in collected)

        asyncio.run(_run())
