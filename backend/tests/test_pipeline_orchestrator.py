from __future__ import annotations

import asyncio

from app.game_core.bootstrap import build_default_runtime


def test_pipeline_result_exposes_execute_errors() -> None:
    runtime = build_default_runtime(
        "test_world",
        world_data={
            "maps": {
                "town": {
                    "id": "town",
                    "is_starting_area": True,
                }
            }
        },
    )

    result = asyncio.run(
        runtime.tick_coordinator.process(
            {
                "action_type": "move_area",
                "params": {"area_id": "missing"},
            }
        )
    )

    assert result.executed is False
    assert result.action_type == "move_area"
    assert result.errors == ["unknown area: missing"]
