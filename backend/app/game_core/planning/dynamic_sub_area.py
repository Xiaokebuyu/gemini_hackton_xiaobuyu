"""Dynamic sub-area manager skeleton."""

from __future__ import annotations

from typing import Any

from app.game_core.state.slices import AreaSlice


class DynamicSubAreaManager:
    """Manage runtime-generated temporary sub-areas."""

    def __init__(self, areas: AreaSlice) -> None:
        self._areas = areas

    def create(self, area_id: str, spec: dict[str, Any]) -> dict[str, Any]:
        return self._areas.add_temporary_sub_area(area_id, spec)

    def expire(self, area_id: str, sub_area_id: str) -> bool:
        return self._areas.remove_temporary_sub_area(area_id, sub_area_id)

    def list_active(self, area_id: str) -> list[dict[str, Any]]:
        return self._areas.list_temporary_sub_areas(area_id)

    def get_cluster_status(self, area_id: str) -> dict[str, int]:
        return self._areas.count_dynamic_sub_areas(area_id)

    def tick_expiry(self, area_id: str, elapsed: int = 1) -> list[str]:
        """Tick down expiry counters. Returns removed sub-area IDs."""
        return self._areas.tick_expiry(area_id, elapsed)
