"""AreaSlice implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.game_core.state.base import StateSlice
from app.game_core.state.delta import StateChange


@dataclass(slots=True)
class AreaState:
    exploration: str = "undiscovered"
    danger_level: float = 1.0
    properties: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    temporary_sub_areas: list[dict[str, Any]] = field(default_factory=list)
    discovered_items: set[str] = field(default_factory=set)
    npc_locations: dict[str, str | None] = field(default_factory=dict)
    container_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    hostile_tracking: dict[str, dict[str, Any]] = field(default_factory=dict)
    permanent_hostile_slots: dict[str, dict[str, Any]] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "exploration": self.exploration,
            "danger_level": self.danger_level,
            "properties": dict(self.properties),
            "tags": list(self.tags),
            "temporary_sub_areas": [dict(item) for item in self.temporary_sub_areas],
            "discovered_items": sorted(self.discovered_items),
            "npc_locations": dict(self.npc_locations),
            "container_states": {
                key: dict(value) for key, value in self.container_states.items()
            },
            "hostile_tracking": {
                key: dict(value) for key, value in self.hostile_tracking.items()
            },
            "permanent_hostile_slots": {
                key: dict(value)
                for key, value in self.permanent_hostile_slots.items()
            },
        }


class AreaSlice(StateSlice):
    """World area runtime state."""

    def __init__(self) -> None:
        super().__init__("areas")
        self.areas: dict[str, AreaState] = {}

    def restore(self, payload: Mapping[str, Any]) -> None:
        raw_areas = payload.get("areas", {})
        self.areas = {}
        if isinstance(raw_areas, Mapping):
            for area_id, area_payload in raw_areas.items():
                self.areas[str(area_id)] = self._coerce_area_state(area_payload)
        self.clear_dirty()

    def serialize(self) -> dict[str, Any]:
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {
            "areas": {
                area_id: area_state.snapshot()
                for area_id, area_state in self.areas.items()
            }
        }

    def get_area(self, area_id: str) -> AreaState:
        return self.areas.setdefault(area_id, AreaState())

    def get_danger(self, area_id: str) -> float:
        return self.get_area(area_id).danger_level

    def is_discovered(self, area_id: str) -> bool:
        return self.get_area(area_id).exploration != "undiscovered"

    def is_discovery_found(self, area_id: str, discovery_id: str) -> bool:
        return discovery_id in self.get_area(area_id).discovered_items

    def get_container_state(
        self,
        area_id: str,
        container_id: str,
    ) -> dict[str, Any] | None:
        state = self.get_area(area_id).container_states.get(container_id)
        return dict(state) if isinstance(state, dict) else None

    def get_hostile_state(self, sub_area_id: str) -> dict[str, Any] | None:
        for area in self.areas.values():
            state = area.hostile_tracking.get(sub_area_id)
            if isinstance(state, dict):
                return dict(state)
        return None

    def get_permanent_slots(self, area_id: str) -> dict[str, Any] | None:
        state = self.get_area(area_id).permanent_hostile_slots.get(area_id)
        return dict(state) if isinstance(state, dict) else None

    def set_exploration(self, area_id: str, state: str) -> None:
        self.get_area(area_id).exploration = state
        self._dirty = True

    def adjust_danger(self, area_id: str, delta: float) -> None:
        area = self.get_area(area_id)
        area.danger_level = max(0.0, area.danger_level + delta)
        self._dirty = True

    def modify_property(self, area_id: str, key: str, value: Any) -> None:
        self.get_area(area_id).properties[key] = value
        self._dirty = True

    def mark_discovery(self, area_id: str, discovery_id: str) -> None:
        self.get_area(area_id).discovered_items.add(discovery_id)
        self._dirty = True

    def init_container(
        self,
        area_id: str,
        container_id: str,
        state: dict[str, Any],
    ) -> None:
        self.get_area(area_id).container_states[container_id] = dict(state)
        self._dirty = True

    def update_container(
        self,
        area_id: str,
        container_id: str,
        **changes: Any,
    ) -> None:
        container = self.get_area(area_id).container_states.setdefault(container_id, {})
        container.update(changes)
        self._dirty = True

    def remove_item_from_container(
        self,
        area_id: str,
        container_id: str,
        item_id: str,
        count: int,
    ) -> None:
        if count <= 0:
            raise ValueError("count must be > 0")
        container = self.get_area(area_id).container_states.setdefault(container_id, {})
        remaining_items = list(container.get("remaining_items", []))
        updated: list[dict[str, Any]] = []
        removed = False
        for item in remaining_items:
            if removed or item.get("item_id") != item_id:
                updated.append(dict(item))
                continue
            current = int(item.get("count", 0))
            if current < count:
                raise ValueError(f"not enough container items: {item_id}")
            new_count = current - count
            if new_count > 0:
                new_item = dict(item)
                new_item["count"] = new_count
                updated.append(new_item)
            removed = True
        if not removed:
            raise ValueError(f"container item not found: {item_id}")
        container["remaining_items"] = updated
        container["looted"] = not updated and int(container.get("remaining_gold", 0)) == 0
        self._dirty = True

    def mark_cleared(self, sub_area_id: str, tick: int) -> None:
        for area in self.areas.values():
            if sub_area_id in area.hostile_tracking:
                area.hostile_tracking[sub_area_id]["cleared"] = True
                area.hostile_tracking[sub_area_id]["cleared_at_tick"] = tick
                self._dirty = True
                return
        raise KeyError(f"unknown hostile sub area: {sub_area_id}")

    def register_hostile(self, sub_area_id: str, state: dict[str, Any]) -> None:
        area_id = str(state.get("area_id", ""))
        if not area_id:
            raise ValueError("hostile state must include area_id")
        area = self.get_area(area_id)
        payload = dict(state)
        payload.pop("area_id", None)
        area.hostile_tracking[sub_area_id] = payload
        self._dirty = True

    def schedule_refresh(
        self,
        area_id: str,
        refresh_at_tick: int,
        used_ids: list[str],
    ) -> None:
        slots = self.get_area(area_id).permanent_hostile_slots.setdefault(
            area_id,
            {"max_slots": 0, "active_ids": [], "refresh_queue": []},
        )
        slots["refresh_queue"].append(
            {
                "refresh_at_tick": refresh_at_tick,
                "used_template_ids": list(used_ids),
            }
        )
        self._dirty = True

    def update_permanent_slots(self, area_id: str, max_slots: int) -> None:
        slots = self.get_area(area_id).permanent_hostile_slots.setdefault(
            area_id,
            {"max_slots": 0, "active_ids": [], "refresh_queue": []},
        )
        slots["max_slots"] = max_slots
        self._dirty = True

    def update_npc_location(self, character_id: str, location_id: str | None) -> None:
        for area in self.areas.values():
            if character_id in area.npc_locations:
                area.npc_locations[character_id] = location_id
                self._dirty = True
                return
        if not self.areas:
            self.areas["default"] = AreaState()
        first_area = next(iter(self.areas.values()))
        first_area.npc_locations[character_id] = location_id
        self._dirty = True

    def count_dynamic_sub_areas(self, area_id: str) -> dict[str, int]:
        counts = {"permanent": 0, "timed": 0, "temporary": 0, "total": 0}
        for sub_area in self.get_area(area_id).temporary_sub_areas:
            expiry = int(sub_area.get("expiry", 0))
            if expiry == -1:
                counts["permanent"] += 1
            elif expiry >= 24:
                counts["timed"] += 1
            else:
                counts["temporary"] += 1
            counts["total"] += 1
        return counts

    def has_cluster_capacity(self, area_id: str, tier: str = "any") -> bool:
        counts = self.count_dynamic_sub_areas(area_id)
        if tier == "permanent":
            return counts["permanent"] < 3
        if tier == "timed":
            return counts["timed"] < 5
        if tier == "temporary":
            return counts["temporary"] < 3
        return counts["total"] < 6

    def apply_state_change(self, change: StateChange) -> None:
        if "." not in change.path:
            raise ValueError(f"area change path must include area id: {change.path}")
        area_id, field_name = change.path.split(".", 1)
        if field_name == "danger_level":
            if change.operation == "add":
                self.adjust_danger(area_id, float(change.value))
            else:
                self.get_area(area_id).danger_level = max(0.0, float(change.value))
                self._dirty = True
            return
        if field_name == "exploration":
            self.set_exploration(area_id, str(change.value))
            return
        if field_name.startswith("properties.") and change.operation in {"set", "modify"}:
            key = field_name.split(".", 1)[1]
            self.modify_property(area_id, key, change.value)
            return
        raise ValueError(
            f"unsupported area state change: {change.operation} {change.path}"
        )

    @staticmethod
    def _coerce_area_state(raw: Any) -> AreaState:
        if isinstance(raw, AreaState):
            return AreaState(
                exploration=raw.exploration,
                danger_level=raw.danger_level,
                properties=dict(raw.properties),
                tags=list(raw.tags),
                temporary_sub_areas=[dict(item) for item in raw.temporary_sub_areas],
                discovered_items=set(raw.discovered_items),
                npc_locations=dict(raw.npc_locations),
                container_states={k: dict(v) for k, v in raw.container_states.items()},
                hostile_tracking={k: dict(v) for k, v in raw.hostile_tracking.items()},
                permanent_hostile_slots={
                    k: dict(v) for k, v in raw.permanent_hostile_slots.items()
                },
            )
        if not isinstance(raw, Mapping):
            raise ValueError(f"invalid area state: {raw!r}")
        return AreaState(
            exploration=str(raw.get("exploration", "undiscovered")),
            danger_level=float(raw.get("danger_level", 1.0)),
            properties=dict(raw.get("properties", {})),
            tags=[str(tag) for tag in raw.get("tags", [])],
            temporary_sub_areas=[
                dict(item) for item in raw.get("temporary_sub_areas", [])
            ],
            discovered_items={
                str(item) for item in raw.get("discovered_items", [])
            },
            npc_locations={
                str(key): (str(value) if value is not None else None)
                for key, value in raw.get("npc_locations", {}).items()
            },
            container_states={
                str(key): dict(value)
                for key, value in raw.get("container_states", {}).items()
            },
            hostile_tracking={
                str(key): dict(value)
                for key, value in raw.get("hostile_tracking", {}).items()
            },
            permanent_hostile_slots={
                str(key): dict(value)
                for key, value in raw.get("permanent_hostile_slots", {}).items()
            },
        )
