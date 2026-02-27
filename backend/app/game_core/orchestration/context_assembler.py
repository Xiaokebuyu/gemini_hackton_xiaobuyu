"""ContextAssembler skeleton."""

from __future__ import annotations

from typing import Any

from app.game_core.orchestration.shared_context import SharedContext


class ContextAssembler:
    """Build stable, layered context payloads for upper layers."""

    def assemble(self, shared: SharedContext) -> dict[str, Any]:
        state = shared.state
        has_time = state.has_slice("time")
        has_player = state.has_slice("player")
        has_areas = state.has_slice("areas")
        has_narrative_plan = state.has_slice("narrative_plan")
        return {
            "layer0_meta": {
                "world_id": shared.world.world_id,
            },
            "layer1_time": state.time.snapshot() if has_time else {},
            "layer2_player": state.player.snapshot() if has_player else {},
            "layer3_area": state.areas.snapshot() if has_areas else {},
            "layer4_world": shared.world.snapshot(),
            "layer5_scene": shared.scene_bus.snapshot(),
            "layer6_narrative": state.narrative_plan.snapshot() if has_narrative_plan else {},
        }
