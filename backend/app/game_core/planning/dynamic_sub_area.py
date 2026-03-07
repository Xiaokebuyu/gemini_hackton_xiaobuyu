"""Dynamic sub-area manager skeleton."""

from __future__ import annotations

from typing import Any

from app.game_core.state.slices import AreaSlice


class DynamicSubAreaManager:
    """Manage runtime-generated temporary sub-areas."""

    def __init__(self, areas: AreaSlice) -> None:
        self._areas = areas

    def create(self, area_id: str, spec: dict[str, Any]) -> dict[str, Any] | None:
        """Create a dynamic sub-area. Returns None if creation is blocked by capacity."""
        if not self._areas.has_cluster_capacity(area_id):
            return None
        tier = self._coerce_non_empty_string(spec.get("tier"), default="temporary")
        if tier == "permanent":
            if self._areas.count_dynamic_sub_areas(area_id).get("permanent", 0) >= self._max_permanent(area_id):
                return None

        raw_id = self._coerce_non_empty_string(spec.get("id"))
        if not raw_id:
            raw_id = f"dsa_{id(spec)}"
        raw_expiry = spec.get("expiry_ticks")
        if raw_expiry is None:
            raw_expiry = spec.get("expiry")
        expiry_ticks = self._coerce_int(
            raw_expiry,
            default=-1 if tier == "permanent" else 12,
        )
        sub_area = {
            "id": raw_id,
            "label": self._coerce_non_empty_string(spec.get("label"), default=""),
            "description": self._coerce_non_empty_string(spec.get("description"), default=""),
            "tags": list(self._normalize_sequence(spec.get("tags", []))),
            "type": self._coerce_non_empty_string(spec.get("type"), default="visit"),
            "tier": tier,
            "discovery_mode": self._coerce_non_empty_string(
                spec.get("discovery_mode"),
                default="auto",
            ),
            "discovery_dc": self._coerce_int(spec.get("discovery_dc"), default=0),
            "hostile_config": spec.get("hostile_config"),
            "interactables": list(self._normalize_sequence(spec.get("interactables", []))),
            "resident_npcs": list(self._normalize_sequence(spec.get("resident_npcs", []))),
            "linked_quest_id": self._coerce_optional_string(spec.get("linked_quest_id")),
            "linked_milestone": self._coerce_optional_string(spec.get("linked_milestone")),
            "source": self._coerce_non_empty_string(spec.get("source"), default="unknown"),
            "created_at_tick": self._coerce_int(spec.get("created_at_tick"), default=0),
            "expiry": expiry_ticks,
            "status": "active",
        }
        return self._areas.add_temporary_sub_area(area_id, sub_area)

    def expire(self, area_id: str, sub_area_id: str) -> bool:
        return self._areas.remove_temporary_sub_area(area_id, sub_area_id)

    def list_active(self, area_id: str) -> list[dict[str, Any]]:
        return self._areas.list_temporary_sub_areas(area_id)

    def get_cluster_status(self, area_id: str) -> dict[str, int]:
        return self._areas.count_dynamic_sub_areas(area_id)

    def tick_expiry(self, area_id: str, elapsed: int = 1) -> list[str]:
        """Tick down expiry counters. Returns removed sub-area IDs."""
        return self._areas.tick_expiry(area_id, elapsed)

    def _max_permanent(self, area_id: str) -> int:
        del area_id
        return 3

    @staticmethod
    def _coerce_non_empty_string(value: Any, *, default: str | None = "") -> str:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or (default if default is not None else "")
        return default or ""

    @staticmethod
    def _coerce_optional_string(value: Any) -> str | None:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return None

    @staticmethod
    def _coerce_int(value: Any, *, default: int = 0) -> int:
        if isinstance(value, bool):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _normalize_sequence(cls, value: Any) -> list[str]:
        if not isinstance(value, (list, tuple, set)):
            return []
        return [str(item).strip() for item in value if str(item).strip()]
