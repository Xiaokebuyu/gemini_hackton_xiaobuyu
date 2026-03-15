"""AreaSlice implementation."""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping

from app.game_core.scene_interactables import duplicate_facility_target
from app.game_core.state.base import StateSlice
from app.game_core.state.delta import StateChange

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BulletinEntry:
    board_id: str
    quest_id: str = ""
    title: str = ""
    content: str = ""
    published_at_tick: int = 0
    source: str = "narrative_planner"


@dataclass(slots=True)
class AreaState:
    exploration: str = "undiscovered"
    danger_level: float = 1.0
    properties: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    temporary_sub_areas: list[dict[str, Any]] = field(default_factory=list)
    discovered_items: set[str] = field(default_factory=set)
    npc_locations: dict[str, str | None] = field(default_factory=dict)
    npc_rooms: dict[str, str | None] = field(default_factory=dict)
    npc_presence_sources: dict[str, str] = field(default_factory=dict)
    discovered_rooms: set[str] = field(default_factory=set)
    container_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    interactable_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    board_bulletins: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    hostile_tracking: dict[str, dict[str, Any]] = field(default_factory=dict)
    permanent_hostile_slots: dict[str, dict[str, Any]] = field(default_factory=dict)
    dynamic_rooms: list[dict[str, Any]] = field(default_factory=list)
    scoped_interactable_overlays: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    area_situation: str = ""
    area_events: list[dict[str, Any]] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "exploration": self.exploration,
            "danger_level": self.danger_level,
            "properties": deepcopy(self.properties),
            "tags": list(self.tags),
            "temporary_sub_areas": [dict(item) for item in self.temporary_sub_areas],
            "discovered_items": sorted(self.discovered_items),
            "npc_locations": dict(self.npc_locations),
            "npc_rooms": dict(self.npc_rooms),
            "npc_presence_sources": dict(self.npc_presence_sources),
            "discovered_rooms": sorted(self.discovered_rooms),
            "board_bulletins": {
                board_id: [dict(entry) for entry in entries]
                for board_id, entries in self.board_bulletins.items()
            },
            "container_states": deepcopy(self.container_states),
            "interactable_states": deepcopy(self.interactable_states),
            "hostile_tracking": {
                key: AreaSlice._copy_hostile_payload(value)
                for key, value in self.hostile_tracking.items()
            },
            "permanent_hostile_slots": {
                key: AreaSlice._copy_permanent_slot_bucket(value)
                for key, value in self.permanent_hostile_slots.items()
            },
            "dynamic_rooms": [dict(item) for item in self.dynamic_rooms],
            "scoped_interactable_overlays": {
                key: [dict(item) for item in entries]
                for key, entries in self.scoped_interactable_overlays.items()
            },
            "area_situation": self.area_situation,
            "area_events": [dict(e) for e in self.area_events],
        }


class AreaSlice(StateSlice):
    """World area runtime state."""

    # ── 1. Core lifecycle (restore / serialize / snapshot / basic getters) ────

    def __init__(self) -> None:
        super().__init__("areas")
        self.areas: dict[str, AreaState] = {}

    def restore(self, payload: Mapping[str, Any]) -> None:
        raw_areas = payload.get("areas", {})
        self.areas = {}
        if isinstance(raw_areas, Mapping):
            for area_id, area_payload in raw_areas.items():
                normalized_area_id = str(area_id)
                self.areas[normalized_area_id] = self._coerce_area_state(
                    normalized_area_id,
                    area_payload,
                )
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

    def _ensure_area(self, area_id: str) -> AreaState:
        return self.get_area(area_id)

    def get_danger(self, area_id: str) -> float:
        return self.get_area(area_id).danger_level

    def add_board_bulletin(
        self,
        area_id: str,
        board_id: str,
        entry: dict[str, Any],
    ) -> None:
        """Append one bulletin entry to a board in the specified area."""
        board_id = board_id.strip()
        if not board_id:
            return
        area = self._ensure_area(area_id)
        board_entries = area.board_bulletins.setdefault(board_id, [])
        board_entries.append(dict(entry))
        self._dirty = True

    def get_board_bulletins(self, area_id: str, board_id: str) -> list[dict[str, Any]]:
        """Return a defensive copy of board bulletins for a specific board."""
        area_state = self.areas.get(area_id)
        if area_state is None:
            return []
        entries = area_state.board_bulletins.get(board_id, [])
        if not isinstance(entries, list):
            return []
        return [dict(entry) for entry in entries if isinstance(entry, Mapping)]

    def get_all_board_bulletins(self, area_id: str) -> dict[str, list[dict[str, Any]]]:
        """Return all board bulletins for an area, keyed by board_id."""
        area_state = self.areas.get(area_id)
        if area_state is None:
            return {}
        raw = area_state.board_bulletins
        if not isinstance(raw, dict):
            return {}
        return {
            str(k): [dict(e) for e in v if isinstance(e, Mapping)]
            for k, v in raw.items()
            if isinstance(v, list)
        }

    def remove_board_bulletin(self, area_id: str, board_id: str, quest_id: str) -> bool:
        """Remove the matching bulletin by quest_id from an area's board."""
        area_state = self.areas.get(area_id)
        if area_state is None:
            return False
        entries = area_state.board_bulletins.get(board_id)
        if not isinstance(entries, list):
            return False
        for index, entry in enumerate(entries):
            if str(entry.get("quest_id", "")).strip() == quest_id.strip():
                entries.pop(index)
                self._dirty = True
                return True
        return False

    def is_discovered(self, area_id: str) -> bool:
        return self.get_area(area_id).exploration != "undiscovered"

    def is_discovery_found(self, area_id: str, discovery_id: str) -> bool:
        return discovery_id in self.get_area(area_id).discovered_items

    def get_discovered(self, area_id: str) -> set[str]:
        """Return the set of discovery ids found in this area."""
        return set(self.get_area(area_id).discovered_items)

    def is_hostile_cleared(self, area_id: str, sub_area_id: str) -> bool:
        """Return True if the hostile sub-area has been cleared."""
        state = self.get_area(area_id).hostile_tracking.get(sub_area_id)
        if not isinstance(state, Mapping):
            return False
        return str(state.get("status", "")) == "cleared"

    def is_container_opened(self, area_id: str, container_id: str) -> bool:
        """Return True if the container has been opened or looted."""
        state = self.get_area(area_id).container_states.get(container_id)
        if not isinstance(state, Mapping):
            return False
        return bool(state.get("opened") or state.get("looted"))

    def is_trap_detected(self, area_id: str, interactable_id: str) -> bool:
        """Return True if the trap on this container/interactable has been detected."""
        state = self.get_area(area_id).container_states.get(interactable_id)
        if not isinstance(state, Mapping):
            return False
        return bool(state.get("trap_detected"))

    def is_interactable_used(self, area_id: str, interactable_id: str) -> bool:
        """Return True if this one-time interactable has been used."""
        state = self.get_area(area_id).interactable_states.get(interactable_id)
        if not isinstance(state, Mapping):
            return False
        return bool(state.get("used"))

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
            if isinstance(state, Mapping):
                return self._copy_hostile_payload(state)
        return None

    def find_container_area(self, container_id: str) -> str | None:
        for area_id, area in self.areas.items():
            if container_id in area.container_states:
                return area_id
        return None

    def find_hostile_area(self, sub_area_id: str) -> str | None:
        for area_id, area in self.areas.items():
            if sub_area_id in area.hostile_tracking:
                return area_id
        return None

    def get_permanent_slots(self, area_id: str) -> dict[str, Any] | None:
        state = self.get_area(area_id).permanent_hostile_slots.get(area_id)
        if not isinstance(state, Mapping):
            return None
        normalized = self._normalize_permanent_slot_bucket(state, default_max_slots=0)
        return self._copy_permanent_slot_bucket(normalized)

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

    def mark_interactable_used(self, area_id: str, interactable_id: str) -> None:
        """Mark a one-time interactable as used."""
        state = self.get_area(area_id).interactable_states.setdefault(interactable_id, {})
        state["used"] = True
        self._dirty = True

    def mark_trap_detected(self, area_id: str, interactable_id: str) -> None:
        """Mark a trap on this container/interactable as detected."""
        state = self.get_area(area_id).container_states.setdefault(interactable_id, {})
        state["trap_detected"] = True
        self._dirty = True

    # ── 2. Container state ───────────────────────────────────────────────────

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
                payload = area.hostile_tracking[sub_area_id]
                if not isinstance(payload, Mapping):
                    raise KeyError(f"unknown hostile sub area: {sub_area_id}")
                area.hostile_tracking[sub_area_id] = self.build_cleared_hostile(
                    payload,
                    current_tick=tick,
                )
                self._dirty = True
                return
        raise KeyError(f"unknown hostile sub area: {sub_area_id}")

    # ── 3. Hostile tracking ──────────────────────────────────────────────────

    def register_hostile(self, sub_area_id: str, state: dict[str, Any]) -> None:
        self.upsert_hostile(sub_area_id, state)
        self._dirty = True

    def upsert_hostile(self, sub_area_id: str, state: dict[str, Any]) -> None:
        area_id = str(state.get("area_id", "")).strip()
        if not area_id:
            logger.warning(
                "upsert_hostile: hostile state must include area_id, sub_area_id=%s, skipping",
                sub_area_id,
            )
            return
        area = self.get_area(area_id)
        area.hostile_tracking[sub_area_id] = self._normalize_hostile_payload(state)
        self._dirty = True

    def upsert_container_state(self, container_id: str, state: dict[str, Any]) -> None:
        area_id = str(state.get("area_id", "")).strip()
        if not area_id:
            area_id = self.find_container_area(container_id) or ""
        if not area_id:
            raise ValueError("container state must include area_id")
        payload = dict(state)
        payload["area_id"] = area_id
        target_area = self.get_area(area_id)
        target_area.container_states[container_id] = payload
        self._dirty = True

    def schedule_refresh(
        self,
        area_id: str,
        refresh_at_tick: int,
        used_ids: list[str],
    ) -> None:
        area = self.get_area(area_id)
        bucket = self._normalize_permanent_slot_bucket(
            area.permanent_hostile_slots.get(area_id),
            default_max_slots=0,
        )
        bucket["refresh_queue"].append(
            {
                "refresh_at_tick": int(refresh_at_tick),
                "used_template_ids": self._normalize_slot_ids(used_ids),
            }
        )
        area.permanent_hostile_slots[area_id] = self._copy_permanent_slot_bucket(bucket)
        self._dirty = True

    # ── 4. Permanent slots & respawn queue ───────────────────────────────────

    def update_permanent_slots(self, area_id: str, max_slots: int) -> None:
        area = self.get_area(area_id)
        normalized_max_slots = max(0, int(max_slots))
        bucket = self._normalize_permanent_slot_bucket(
            area.permanent_hostile_slots.get(area_id),
            default_max_slots=normalized_max_slots,
        )
        bucket["max_slots"] = normalized_max_slots
        area.permanent_hostile_slots[area_id] = self._copy_permanent_slot_bucket(bucket)
        self._dirty = True

    def sync_permanent_hostile_slots(
        self,
        area_id: str,
        *,
        current_tick: int,
        default_max_slots: int = 1,
        clear_refresh_delay: int = 12,
    ) -> dict[str, Any]:
        area = self.get_area(area_id)
        existing = area.permanent_hostile_slots.get(area_id)
        normalized = self._normalize_permanent_slot_bucket(
            existing,
            default_max_slots=default_max_slots,
        )
        refresh_queue = [
            self._copy_refresh_queue_entry(entry)
            for entry in normalized["refresh_queue"]
            if int(entry["refresh_at_tick"]) > current_tick
        ]
        active_ids: list[str] = []
        for hostile_id in normalized["active_ids"]:
            hostile_state = self.get_hostile_state(hostile_id)
            if not isinstance(hostile_state, Mapping):
                continue
            status = str(hostile_state.get("status", "active")).strip().lower()
            cleared = bool(hostile_state.get("cleared", False))
            if status != "cleared" and not cleared:
                active_ids.append(hostile_id)
                continue
            cooldown_token = (
                self._coerce_non_empty_string(hostile_state.get("template_id"))
                or hostile_id
            )
            cleared_at_tick = self._coerce_int(hostile_state.get("cleared_at_tick"))
            base_tick = cleared_at_tick if cleared_at_tick is not None else current_tick
            refresh_at_tick = base_tick + clear_refresh_delay
            extended_existing = False
            for entry in refresh_queue:
                used_template_ids = entry.get("used_template_ids", [])
                if cooldown_token not in used_template_ids:
                    continue
                entry["refresh_at_tick"] = max(
                    self._coerce_int(entry.get("refresh_at_tick")) or 0,
                    refresh_at_tick,
                )
                extended_existing = True
                break
            if extended_existing:
                continue
            self._upsert_refresh_queue_entry(
                refresh_queue,
                refresh_at_tick=refresh_at_tick,
                used_ids=[cooldown_token],
            )
        canonical = {
            "max_slots": normalized["max_slots"],
            "active_ids": active_ids,
            "refresh_queue": refresh_queue,
        }
        if not isinstance(existing, Mapping) or existing != canonical:
            area.permanent_hostile_slots[area_id] = self._copy_permanent_slot_bucket(
                canonical
            )
            self._dirty = True
        return self._copy_permanent_slot_bucket(area.permanent_hostile_slots[area_id])

    def occupy_permanent_hostile_slot(
        self,
        area_id: str,
        hostile_id: str,
        *,
        default_max_slots: int = 1,
    ) -> dict[str, Any]:
        area = self.get_area(area_id)
        existing = area.permanent_hostile_slots.get(area_id)
        bucket = self._normalize_permanent_slot_bucket(
            existing,
            default_max_slots=default_max_slots,
        )
        normalized_id = self._coerce_non_empty_string(hostile_id)
        if normalized_id is not None:
            occupied = len(bucket["active_ids"])
            if (
                bucket["max_slots"] > 0
                and normalized_id not in bucket["active_ids"]
                and occupied < bucket["max_slots"]
            ):
                bucket["active_ids"].append(normalized_id)
                area.permanent_hostile_slots[area_id] = self._copy_permanent_slot_bucket(
                    bucket
                )
                self._dirty = True
                return self._copy_permanent_slot_bucket(bucket)
        if not isinstance(existing, Mapping) or existing != bucket:
            area.permanent_hostile_slots[area_id] = self._copy_permanent_slot_bucket(
                bucket
            )
            self._dirty = True
        return self._copy_permanent_slot_bucket(area.permanent_hostile_slots[area_id])

    def cooldown_permanent_hostile_slot(
        self,
        area_id: str,
        *,
        refresh_at_tick: int,
        used_ids: list[str] | None = None,
        default_max_slots: int = 1,
    ) -> dict[str, Any]:
        area = self.get_area(area_id)
        existing = area.permanent_hostile_slots.get(area_id)
        bucket = self._normalize_permanent_slot_bucket(
            existing,
            default_max_slots=default_max_slots,
        )
        if bucket["max_slots"] > 0:
            self._upsert_refresh_queue_entry(
                bucket["refresh_queue"],
                refresh_at_tick=int(refresh_at_tick),
                used_ids=self._normalize_slot_ids(used_ids),
            )
            area.permanent_hostile_slots[area_id] = self._copy_permanent_slot_bucket(
                bucket
            )
            self._dirty = True
            return self._copy_permanent_slot_bucket(bucket)
        if not isinstance(existing, Mapping) or existing != bucket:
            area.permanent_hostile_slots[area_id] = self._copy_permanent_slot_bucket(
                bucket
            )
            self._dirty = True
        return self._copy_permanent_slot_bucket(area.permanent_hostile_slots[area_id])

    # ── 5. NPC locations ─────────────────────────────────────────────────────

    def update_npc_location(self, character_id: str, location_id: str | None) -> None:
        for area in self.areas.values():
            if character_id in area.npc_locations:
                if area.npc_locations.get(character_id) != location_id:
                    area.npc_rooms.pop(character_id, None)
                area.npc_locations[character_id] = location_id
                self._dirty = True
                return
        if not self.areas:
            self.areas["default"] = AreaState()
        first_area = next(iter(self.areas.values()))
        first_area.npc_locations[character_id] = location_id
        self._dirty = True

    def find_npc_area(self, character_id: str) -> str | None:
        for area_id, area in self.areas.items():
            if character_id in area.npc_locations:
                return area_id
        return None

    def move_npc(
        self,
        character_id: str,
        area_id: str,
        location_id: str | None,
        room_id: str | None = None,
        source: str = "resident",
    ) -> None:
        for area in self.areas.values():
            area.npc_locations.pop(character_id, None)
            area.npc_rooms.pop(character_id, None)
            area.npc_presence_sources.pop(character_id, None)

        target_area = self.get_area(area_id)
        target_area.npc_locations[character_id] = location_id
        if location_id is not None and room_id is not None:
            target_area.npc_rooms[character_id] = room_id
        else:
            target_area.npc_rooms.pop(character_id, None)
        target_area.npc_presence_sources[character_id] = source
        self._dirty = True

    def remove_npc_presence(self, character_id: str) -> bool:
        removed = False
        for area in self.areas.values():
            if character_id in area.npc_locations:
                area.npc_locations.pop(character_id, None)
                removed = True
            if character_id in area.npc_rooms:
                area.npc_rooms.pop(character_id, None)
                removed = True
            if character_id in area.npc_presence_sources:
                area.npc_presence_sources.pop(character_id, None)
                removed = True
        if removed:
            self._dirty = True
        return removed

    # ── 5b. NPC rooms ─────────────────────────────────────────────────────────

    def set_npc_room(self, area_id: str, character_id: str, room_id: str | None) -> None:
        """Set which room within a sub_location an NPC is currently in."""
        area = self.get_area(area_id)
        if room_id is None:
            area.npc_rooms.pop(character_id, None)
        else:
            area.npc_rooms[character_id] = room_id
        self._dirty = True

    def get_npc_room(self, area_id: str, character_id: str) -> str | None:
        """Return the room ID for an NPC in the given area, or None."""
        area = self.areas.get(area_id)
        if area is None:
            return None
        return area.npc_rooms.get(character_id)

    # ── 5c. Discovered rooms ──────────────────────────────────────────────────

    def mark_room_discovered(
        self, area_id: str, sub_loc_id: str, room_id: str,
    ) -> None:
        """Record that a room within a sub_location has been discovered."""
        room_key = f"{sub_loc_id}__{room_id}"
        self.get_area(area_id).discovered_rooms.add(room_key)
        self._dirty = True

    def is_room_discovered(
        self, area_id: str, sub_loc_id: str, room_id: str,
    ) -> bool:
        """Return True if the room has been discovered."""
        room_key = f"{sub_loc_id}__{room_id}"
        area = self.areas.get(area_id)
        if area is None:
            return False
        return room_key in area.discovered_rooms

    def get_discovered_rooms(self, area_id: str) -> set[str]:
        """Return a defensive copy of the discovered room keys for an area."""
        area = self.areas.get(area_id)
        if area is None:
            return set()
        return set(area.discovered_rooms)

    # ── 5d. Dynamic rooms ─────────────────────────────────────────────────────

    def add_dynamic_room(self, area_id: str, room_dict: dict[str, Any]) -> None:
        """Append a dynamic room entry to the specified area."""
        self.get_area(area_id).dynamic_rooms.append(dict(room_dict))
        self._dirty = True

    def list_dynamic_rooms(
        self, area_id: str, sub_loc_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return dynamic rooms for an area, optionally filtered by sub_loc_id."""
        area = self.areas.get(area_id)
        if area is None:
            return []
        rooms = [dict(item) for item in area.dynamic_rooms]
        if sub_loc_id is not None:
            rooms = [r for r in rooms if r.get("sub_loc_id") == sub_loc_id]
        return rooms

    def count_dynamic_rooms(self, area_id: str, sub_loc_id: str) -> int:
        """Return the number of dynamic rooms for a specific sub_location."""
        return len(self.list_dynamic_rooms(area_id, sub_loc_id))

    # ── 5e. Area situation & events ───────────────────────────────────────────

    def append_area_event(self, area_id: str, event_dict: dict[str, Any]) -> None:
        """Append one event to area_events, keeping only the most recent 20 entries."""
        area = self._ensure_area(area_id)
        area.area_events.append(dict(event_dict))
        if len(area.area_events) > 20:
            del area.area_events[: len(area.area_events) - 20]
        self._dirty = True

    def set_area_situation(self, area_id: str, text: str) -> None:
        """Set the natural-language situation summary for an area."""
        self._ensure_area(area_id).area_situation = str(text)
        self._dirty = True

    def get_area_events(self, area_id: str) -> list[dict[str, Any]]:
        """Return a defensive copy of the area_events list for an area."""
        area = self.areas.get(area_id)
        if area is None:
            return []
        return [dict(e) for e in area.area_events]

    def get_area_situation(self, area_id: str) -> str:
        """Return the current situation summary for an area (empty string if none)."""
        area = self.areas.get(area_id)
        if area is None:
            return ""
        return area.area_situation

    # ── 5f. Scene-scoped interactable overlays ───────────────────────────────

    @staticmethod
    def interactable_scope_key(location_id: str, room_id: str | None = None) -> str:
        normalized_location = str(location_id).strip()
        normalized_room = str(room_id).strip() if room_id is not None else ""
        if not normalized_location:
            raise ValueError("location_id must be non-empty")
        return (
            f"{normalized_location}__{normalized_room}"
            if normalized_room
            else normalized_location
        )

    def list_scoped_interactable_overlays(
        self,
        area_id: str,
        location_id: str,
        room_id: str | None = None,
    ) -> list[dict[str, Any]]:
        area = self.areas.get(area_id)
        if area is None:
            return []
        key = self.interactable_scope_key(location_id, room_id)
        entries = area.scoped_interactable_overlays.get(key, [])
        if not isinstance(entries, list):
            return []
        return [
            dict(item)
            for item in entries
            if isinstance(item, Mapping)
        ]

    def count_scoped_interactable_overlays(
        self,
        area_id: str,
        location_id: str,
        room_id: str | None = None,
    ) -> int:
        return len(self.list_scoped_interactable_overlays(area_id, location_id, room_id))

    def set_scoped_interactable_overlays(
        self,
        area_id: str,
        scope_key: str,
        entries: list[dict[str, Any]],
    ) -> None:
        normalized = [
            dict(item)
            for item in entries
            if isinstance(item, Mapping) and str(item.get("id", "")).strip()
        ]
        self.get_area(area_id).scoped_interactable_overlays[scope_key] = normalized
        self._dirty = True

    def upsert_scoped_interactable_overlays(
        self,
        area_id: str,
        location_id: str,
        interactables: list[dict[str, Any]],
        *,
        room_id: str | None = None,
        max_entries: int = 4,
    ) -> list[dict[str, Any]]:
        key = self.interactable_scope_key(location_id, room_id)
        area = self.get_area(area_id)
        existing = [
            dict(item)
            for item in area.scoped_interactable_overlays.get(key, [])
            if isinstance(item, Mapping)
        ]
        by_id = {
            str(item.get("id", "")).strip(): index
            for index, item in enumerate(existing)
            if str(item.get("id", "")).strip()
        }
        for raw_item in interactables:
            if not isinstance(raw_item, Mapping):
                continue
            normalized = dict(raw_item)
            interactable_id = str(normalized.get("id", "")).strip()
            if not interactable_id:
                continue
            existing_index = by_id.get(interactable_id)
            if existing_index is not None:
                merged = dict(existing[existing_index])
                merged.update(normalized)
                existing[existing_index] = merged
                continue
            if len(existing) >= max_entries:
                logger.warning(
                    "upsert_scoped_interactable_overlays: capacity exceeded (%d), truncating remaining entries",
                    max_entries,
                )
                break
            existing.append(normalized)
            by_id[interactable_id] = len(existing) - 1
        area.scoped_interactable_overlays[key] = existing
        self._dirty = True
        return [dict(item) for item in existing]

    # ── 6. Temporary sub-areas ───────────────────────────────────────────────

    def add_temporary_sub_area(
        self,
        area_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized = dict(payload)
        self.get_area(area_id).temporary_sub_areas.append(normalized)
        self._dirty = True
        return dict(normalized)

    def remove_temporary_sub_area(
        self,
        area_id: str,
        sub_area_id: str,
    ) -> bool:
        area = self.areas.get(area_id)
        if not isinstance(area, AreaState):
            return False
        original_size = len(area.temporary_sub_areas)
        area.temporary_sub_areas = [
            dict(item)
            for item in area.temporary_sub_areas
            if str(item.get("id", "")) != sub_area_id
        ]
        removed = len(area.temporary_sub_areas) != original_size
        if removed:
            self._dirty = True
        return removed

    def list_temporary_sub_areas(self, area_id: str) -> list[dict[str, Any]]:
        area = self.areas.get(area_id)
        if not isinstance(area, AreaState):
            return []
        return [dict(item) for item in area.temporary_sub_areas]

    def count_dynamic_sub_areas(self, area_id: str) -> dict[str, int]:
        counts = {"permanent": 0, "timed": 0, "temporary": 0, "total": 0}
        for sub_area in self.list_temporary_sub_areas(area_id):
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
            return counts["permanent"] < 8
        if tier == "timed":
            return counts["timed"] < 5
        if tier == "temporary":
            return counts["temporary"] < 3
        return counts["total"] < 15

    def tick_expiry(self, area_id: str, elapsed: int = 1) -> list[str]:
        """Decrement positive expiry values and remove expired sub-areas.

        Returns list of removed sub-area IDs.
        Permanent sub-areas (expiry == -1) are never touched.
        """
        area = self.areas.get(area_id)
        if not isinstance(area, AreaState):
            return []
        surviving: list[dict[str, Any]] = []
        removed_ids: list[str] = []
        for item in area.temporary_sub_areas:
            expiry = item.get("expiry", 0)
            if not isinstance(expiry, (int, float)):
                expiry = 0
            expiry = int(expiry)
            if expiry == -1:
                surviving.append(item)
                continue
            expiry = max(0, expiry - elapsed)
            if expiry <= 0:
                removed_ids.append(str(item.get("id", "")))
                continue
            item["expiry"] = expiry
            surviving.append(item)
        if removed_ids:
            area.temporary_sub_areas = surviving
            self._dirty = True
        return removed_ids

    # ── 7. Validation & state-change delta ───────────────────────────────────

    def validate(self) -> list[str]:
        issues: list[str] = []
        if not isinstance(self.areas, dict):
            return ["areas must be a dict"]

        for area_id, area in self.areas.items():
            if not isinstance(area, AreaState):
                issues.append(f"area '{area_id}' must be an AreaState")
                continue
            if not isinstance(area.danger_level, (int, float)):
                issues.append(f"area '{area_id}' danger_level must be numeric")
            elif area.danger_level < 0:
                issues.append(f"area '{area_id}' danger_level must be >= 0")
            if not isinstance(area.npc_locations, dict):
                issues.append(f"area '{area_id}' npc_locations must be a dict")
            if not isinstance(area.board_bulletins, dict):
                issues.append(f"area '{area_id}' board_bulletins must be a dict")
            else:
                for board_id, entries in area.board_bulletins.items():
                    if not isinstance(board_id, str):
                        issues.append(
                            f"area '{area_id}' board_bulletins key must be a string"
                        )
                    if not isinstance(entries, list):
                        issues.append(
                            f"area '{area_id}' board_bulletins '{board_id}' must be a list"
                        )
            if not isinstance(area.scoped_interactable_overlays, dict):
                issues.append(
                    f"area '{area_id}' scoped_interactable_overlays must be a dict"
                )
            else:
                for scope_key, entries in area.scoped_interactable_overlays.items():
                    if not isinstance(scope_key, str):
                        issues.append(
                            f"area '{area_id}' scoped_interactable_overlays key must be a string"
                        )
                        continue
                    if not isinstance(entries, list):
                        issues.append(
                            f"area '{area_id}' scoped_interactable_overlays '{scope_key}' must be a list"
                        )
                        continue
                    for index, item in enumerate(entries):
                        if not isinstance(item, Mapping):
                            issues.append(
                                f"area '{area_id}' scoped_interactable_overlays '{scope_key}' entry {index} must be a mapping"
                            )
                            continue
                        if not self._coerce_non_empty_string(item.get("id")):
                            issues.append(
                                f"area '{area_id}' scoped_interactable_overlays '{scope_key}' entry {index} missing id"
                            )
            if not isinstance(area.container_states, dict):
                issues.append(f"area '{area_id}' container_states must be a dict")
            if not isinstance(area.interactable_states, dict):
                issues.append(f"area '{area_id}' interactable_states must be a dict")
            if not isinstance(area.hostile_tracking, dict):
                issues.append(f"area '{area_id}' hostile_tracking must be a dict")
            else:
                for sub_area_id, hostile_state in area.hostile_tracking.items():
                    if not isinstance(hostile_state, Mapping):
                        issues.append(
                            f"area '{area_id}' hostile '{sub_area_id}' must be a mapping"
                        )
                        continue
                    raw_participants = hostile_state.get("participants")
                    if raw_participants is not None:
                        if not isinstance(raw_participants, list):
                            issues.append(
                                f"area '{area_id}' hostile '{sub_area_id}' participants must be a list"
                            )
                        else:
                            for index, participant in enumerate(raw_participants):
                                if not isinstance(participant, Mapping):
                                    issues.append(
                                        f"area '{area_id}' hostile '{sub_area_id}' participant {index} must be a mapping"
                                    )
                                    continue
                                raw_effects = participant.get("active_effects")
                                if raw_effects is None:
                                    continue
                                if not isinstance(raw_effects, list):
                                    issues.append(
                                        f"area '{area_id}' hostile '{sub_area_id}' participant {index} active_effects must be a list"
                                    )
                                    continue
                                for effect_index, effect in enumerate(raw_effects):
                                    if not isinstance(effect, Mapping):
                                        issues.append(
                                            f"area '{area_id}' hostile '{sub_area_id}' participant {index} active_effect {effect_index} must be a mapping"
                                        )
            if not isinstance(area.area_situation, str):
                issues.append(f"area '{area_id}' area_situation must be a str")
            if not isinstance(area.area_events, list):
                issues.append(f"area '{area_id}' area_events must be a list")
            else:
                for index, event in enumerate(area.area_events):
                    if not isinstance(event, dict):
                        issues.append(
                            f"area '{area_id}' area_events entry {index} must be a dict"
                        )
            if not isinstance(area.permanent_hostile_slots, dict):
                issues.append(
                    f"area '{area_id}' permanent_hostile_slots must be a dict"
                )
            else:
                for bucket_id, bucket in area.permanent_hostile_slots.items():
                    if not isinstance(bucket, Mapping):
                        issues.append(
                            f"area '{area_id}' permanent slot '{bucket_id}' must be a mapping"
                        )
                        continue
                    if "max_slots" in bucket:
                        max_slots = self._coerce_int(bucket.get("max_slots"))
                        if max_slots is None or max_slots < 0:
                            issues.append(
                                f"area '{area_id}' permanent slot '{bucket_id}' max_slots must be an integer >= 0"
                            )
                    if "active_ids" in bucket:
                        active_ids = bucket.get("active_ids")
                        if not isinstance(active_ids, list):
                            issues.append(
                                f"area '{area_id}' permanent slot '{bucket_id}' active_ids must be a list"
                            )
                        else:
                            for index, active_id in enumerate(active_ids):
                                if self._coerce_non_empty_string(active_id) is None:
                                    issues.append(
                                        f"area '{area_id}' permanent slot '{bucket_id}' active_id {index} must be a non-empty string"
                                    )
                    if "refresh_queue" in bucket:
                        refresh_queue = bucket.get("refresh_queue")
                        if not isinstance(refresh_queue, list):
                            issues.append(
                                f"area '{area_id}' permanent slot '{bucket_id}' refresh_queue must be a list"
                            )
                        else:
                            for index, entry in enumerate(refresh_queue):
                                if not isinstance(entry, Mapping):
                                    issues.append(
                                        f"area '{area_id}' permanent slot '{bucket_id}' refresh entry {index} must be a mapping"
                                    )
                                    continue
                                refresh_at_tick = self._coerce_int(
                                    entry.get("refresh_at_tick")
                                )
                                if refresh_at_tick is None:
                                    issues.append(
                                        f"area '{area_id}' permanent slot '{bucket_id}' refresh entry {index} refresh_at_tick must be an integer"
                                    )
                                if "used_template_ids" in entry:
                                    used_ids = entry.get("used_template_ids")
                                    if not isinstance(used_ids, list):
                                        issues.append(
                                            f"area '{area_id}' permanent slot '{bucket_id}' refresh entry {index} used_template_ids must be a list"
                                        )
                                    else:
                                        for used_index, used_id in enumerate(used_ids):
                                            if self._coerce_non_empty_string(used_id) is None:
                                                issues.append(
                                                    f"area '{area_id}' permanent slot '{bucket_id}' refresh entry {index} used_template_id {used_index} must be a non-empty string"
                                                )

        return issues

    def apply_state_change(self, change: StateChange) -> None:
        if change.path.startswith("npc_presence."):
            _, character_id = change.path.split(".", 1)
            if change.operation == "remove":
                self.remove_npc_presence(character_id)
                return
            if change.operation not in {"set", "modify"}:
                logger.warning(
                    "apply_state_change: unsupported npc presence state change, skipping: %s",
                    change.path,
                )
                return
            if not isinstance(change.value, Mapping):
                logger.warning(
                    "apply_state_change: npc presence payload must be a mapping, skipping: %s",
                    change.path,
                )
                return
            area_id = self._coerce_non_empty_string(change.value.get("area_id"))
            if area_id is None:
                logger.warning(
                    "apply_state_change: npc presence payload must include area_id, skipping: %s",
                    change.path,
                )
                return
            location_id = change.value.get("location_id")
            normalized_location = (
                self._coerce_non_empty_string(location_id)
                if location_id is not None else None
            )
            room_id = change.value.get("room_id")
            normalized_room = (
                self._coerce_non_empty_string(room_id)
                if room_id is not None else None
            )
            if normalized_room is not None and normalized_location is None:
                logger.warning(
                    "apply_state_change: npc presence room_id requires location_id, skipping: %s",
                    change.path,
                )
                return
            source = self._coerce_non_empty_string(change.value.get("source")) or "resident"
            self.move_npc(
                character_id,
                area_id,
                normalized_location,
                room_id=normalized_room,
                source=source,
            )
            return
        if change.path.startswith("board_bulletins."):
            if change.operation != "append":
                logger.warning(
                    "apply_state_change: board_bulletins only supports append operation, skipping: %s",
                    change.path,
                )
                return
            if not isinstance(change.value, Mapping):
                logger.warning(
                    "apply_state_change: bulletin entry must be a mapping, skipping: %s",
                    change.path,
                )
                return
            _, board_id = change.path.split(".", 1)
            area_id = str(change.value.get("area_id", "")).strip()
            if not area_id:
                logger.warning(
                    "apply_state_change: bulletin must include area_id, skipping: %s",
                    change.path,
                )
                return
            payload = dict(change.value)
            payload.pop("area_id", None)
            self.add_board_bulletin(area_id, board_id, payload)
            return
        if change.path.startswith("hostile_tracking."):
            if change.operation not in {"set", "modify"}:
                logger.warning(
                    "apply_state_change: unsupported hostile state change, skipping: %s",
                    change.path,
                )
                return
            if not isinstance(change.value, Mapping):
                logger.warning(
                    "apply_state_change: hostile state change payload must be a mapping, skipping: %s",
                    change.path,
                )
                return
            _, sub_area_id = change.path.split(".", 1)
            self.upsert_hostile(sub_area_id, dict(change.value))
            return
        if change.path.startswith("container_states."):
            if change.operation not in {"set", "modify"}:
                raise ValueError(
                    f"unsupported container state change: {change.operation} {change.path}"
                )
            if not isinstance(change.value, Mapping):
                raise ValueError("container state change payload must be a mapping")
            _, container_id = change.path.split(".", 1)
            self.upsert_container_state(container_id, dict(change.value))
            return
        if change.path.startswith("interactable_states."):
            if change.operation not in {"set", "modify"}:
                raise ValueError(
                    f"unsupported interactable state change: {change.operation} {change.path}"
                )
            if not isinstance(change.value, Mapping):
                raise ValueError("interactable state change payload must be a mapping")
            _, interactable_id = change.path.split(".", 1)
            area_id = str(change.value.get("area_id", "")).strip()
            if not area_id:
                raise ValueError("interactable state change must include area_id")
            self.get_area(area_id).interactable_states[interactable_id] = dict(change.value)
            self._dirty = True
            return
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
        if field_name == "temporary_sub_areas":
            if change.operation not in {"set", "modify"}:
                logger.warning(
                    "apply_state_change: unsupported temporary sub-area change, skipping: %s",
                    change.path,
                )
                return
            if not isinstance(change.value, list):
                logger.warning(
                    "apply_state_change: temporary sub-area payload must be a list, skipping: %s",
                    change.path,
                )
                return
            normalized: list[dict[str, Any]] = []
            for item in change.value:
                if not isinstance(item, Mapping):
                    logger.warning(
                        "apply_state_change: temporary sub-area entries must be mappings, skipping entry in: %s",
                        change.path,
                    )
                    continue
                normalized.append(dict(item))
            self.get_area(area_id).temporary_sub_areas = normalized
            self._dirty = True
            return
        if field_name.startswith("scoped_interactable_overlay."):
            if change.operation not in {"set", "modify"}:
                logger.warning(
                    "apply_state_change: unsupported scoped interactable overlay change, skipping: %s",
                    change.path,
                )
                return
            if not isinstance(change.value, list):
                logger.warning(
                    "apply_state_change: scoped interactable overlay payload must be a list, skipping: %s",
                    change.path,
                )
                return
            scope_key = field_name.split(".", 1)[1]
            normalized_entries = [
                dict(item)
                for item in change.value
                if isinstance(item, Mapping)
            ]
            if change.operation == "set":
                self.set_scoped_interactable_overlays(area_id, scope_key, normalized_entries)
            else:
                location_id, room_id = self._split_interactable_scope_key(scope_key)
                if location_id is None:
                    logger.warning(
                        "apply_state_change: invalid scoped interactable overlay key, skipping: %s",
                        change.path,
                    )
                    return
                self.upsert_scoped_interactable_overlays(
                    area_id,
                    location_id,
                    normalized_entries,
                    room_id=room_id,
                )
            return
        if field_name == "board_bulletins":
            if change.operation not in {"set", "modify"}:
                raise ValueError(
                    f"unsupported board bulletin change: {change.operation} {change.path}"
                )
            if not isinstance(change.value, Mapping):
                raise ValueError("board bulletin payload must be a mapping")
            self.get_area(area_id).board_bulletins = self._coerce_board_bulletins(change.value)
            self._dirty = True
            return
        if field_name.startswith("properties.") and change.operation in {"set", "modify"}:
            key = field_name.split(".", 1)[1]
            self.modify_property(area_id, key, change.value)
            return
        if field_name.startswith("discovered_items.") and change.operation in {"set", "add"}:
            discovery_id = field_name.split(".", 1)[1]
            if discovery_id:
                self.mark_discovery(area_id, discovery_id)
            return
        if field_name.startswith("npc_room.") and change.operation in {"set", "modify"}:
            npc_id = field_name.split(".", 1)[1]
            if npc_id:
                room_value = (
                    self._coerce_non_empty_string(change.value)
                    if change.value is not None
                    else None
                )
                self.set_npc_room(area_id, npc_id, room_value)
            return
        if field_name.startswith("discovered_room.") and change.operation in {"set", "add"}:
            # path: discovered_room.{sub_loc_id}.{room_id}
            rest = field_name.split(".", 1)[1]
            if rest:
                # compound key: sub_loc_id__room_id
                parts = rest.split(".", 1)
                if len(parts) == 2:
                    sub_loc_id, room_id = parts
                    room_key = f"{sub_loc_id}__{room_id}"
                else:
                    room_key = rest
                if room_key:
                    self.get_area(area_id).discovered_rooms.add(room_key)
                    self._dirty = True
            return
        if field_name == "dynamic_room" and change.operation == "add":
            if not isinstance(change.value, Mapping):
                raise ValueError("dynamic_room entry must be a mapping")
            self.add_dynamic_room(area_id, dict(change.value))
            return
        if field_name == "area_events":
            if change.operation == "add":
                if not isinstance(change.value, Mapping):
                    logger.warning(
                        "apply_state_change: area_events entry must be a mapping, skipping: %s",
                        change.path,
                    )
                    return
                self.append_area_event(area_id, dict(change.value))
            elif change.operation == "set":
                if not isinstance(change.value, list):
                    logger.warning(
                        "apply_state_change: area_events set value must be a list, skipping: %s",
                        change.path,
                    )
                    return
                self.get_area(area_id).area_events = [
                    dict(e) for e in change.value if isinstance(e, Mapping)
                ]
                self._dirty = True
            else:
                logger.warning(
                    "apply_state_change: area_events only supports add/set operations, skipping: %s",
                    change.path,
                )
            return
        if field_name == "area_situation":
            if change.operation != "set":
                logger.warning(
                    "apply_state_change: area_situation only supports set operation, skipping: %s",
                    change.path,
                )
                return
            self.set_area_situation(area_id, str(change.value))
            return
        raise ValueError(
            f"unsupported area state change: {change.operation} {change.path}"
        )

    # ── 8. Normalization & coercion helpers ──────────────────────────────────

    @staticmethod
    def _coerce_area_state(area_id: str, raw: Any) -> AreaState:
        if isinstance(raw, AreaState):
            state = AreaState(
                exploration=raw.exploration,
                danger_level=raw.danger_level,
                properties=dict(raw.properties),
                tags=list(raw.tags),
                temporary_sub_areas=[dict(item) for item in raw.temporary_sub_areas],
                discovered_items=set(raw.discovered_items),
                npc_locations=dict(raw.npc_locations),
                npc_rooms=dict(raw.npc_rooms),
                npc_presence_sources=dict(raw.npc_presence_sources),
                discovered_rooms=set(raw.discovered_rooms),
                container_states={k: dict(v) for k, v in raw.container_states.items()},
                interactable_states={k: dict(v) for k, v in raw.interactable_states.items()},
                dynamic_rooms=[dict(item) for item in raw.dynamic_rooms],
                board_bulletins={
                    str(key): [dict(entry) for entry in entries if isinstance(entry, Mapping)]
                    for key, entries in raw.board_bulletins.items()
                },
                hostile_tracking={
                    k: AreaSlice._copy_hostile_payload(v)
                    for k, v in raw.hostile_tracking.items()
                },
                permanent_hostile_slots={
                    k: AreaSlice._normalize_permanent_slot_bucket(v, default_max_slots=0)
                    for k, v in raw.permanent_hostile_slots.items()
                },
                scoped_interactable_overlays={
                    str(key): [dict(item) for item in value if isinstance(item, Mapping)]
                    for key, value in raw.scoped_interactable_overlays.items()
                },
                area_situation=raw.area_situation,
                area_events=[dict(e) for e in raw.area_events],
            )
            AreaSlice._migrate_duplicate_facilities(area_id, state)
            return state
        if not isinstance(raw, Mapping):
            raise ValueError(f"invalid area state: {raw!r}")
        state = AreaState(
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
            npc_rooms={
                str(key): (str(value) if value is not None else None)
                for key, value in raw.get("npc_rooms", {}).items()
            },
            npc_presence_sources={
                str(key): str(value)
                for key, value in raw.get("npc_presence_sources", {}).items()
            },
            discovered_rooms={
                str(item) for item in raw.get("discovered_rooms", [])
            },
            container_states={
                str(key): dict(value)
                for key, value in raw.get("container_states", {}).items()
            },
            interactable_states={
                str(key): dict(value)
                for key, value in raw.get("interactable_states", {}).items()
                if isinstance(value, Mapping)
            },
            dynamic_rooms=[
                dict(item) for item in raw.get("dynamic_rooms", [])
                if isinstance(item, Mapping)
            ],
            board_bulletins=AreaSlice._coerce_board_bulletins(
                raw.get("board_bulletins", {})
            ),
            hostile_tracking={
                str(key): AreaSlice._normalize_hostile_payload(value)
                for key, value in raw.get("hostile_tracking", {}).items()
                if isinstance(value, Mapping)
            },
            permanent_hostile_slots={
                str(key): AreaSlice._normalize_permanent_slot_bucket(
                    value,
                    default_max_slots=0,
                )
                for key, value in raw.get("permanent_hostile_slots", {}).items()
            },
            scoped_interactable_overlays=AreaSlice._coerce_scoped_interactable_overlays(
                raw.get("scoped_interactable_overlays", {})
            ),
            area_situation=str(raw.get("area_situation", "")),
            area_events=[
                dict(e) for e in raw.get("area_events", [])
                if isinstance(e, Mapping)
            ],
        )
        AreaSlice._migrate_duplicate_facilities(area_id, state)
        return state

    @staticmethod
    def _coerce_board_bulletins(raw_board_bulletins: Any) -> dict[str, list[dict[str, Any]]]:
        board_bulletins: dict[str, list[dict[str, Any]]] = {}
        if not isinstance(raw_board_bulletins, Mapping):
            return board_bulletins
        for board_id, entries in raw_board_bulletins.items():
            if not isinstance(entries, list):
                continue
            board_bulletins[str(board_id)] = [
                dict(entry) for entry in entries if isinstance(entry, Mapping)
            ]
        return board_bulletins

    @staticmethod
    def _coerce_scoped_interactable_overlays(
        raw_overlays: Any,
    ) -> dict[str, list[dict[str, Any]]]:
        overlays: dict[str, list[dict[str, Any]]] = {}
        if not isinstance(raw_overlays, Mapping):
            return overlays
        for scope_key, entries in raw_overlays.items():
            if not isinstance(entries, list):
                continue
            normalized_entries = [
                dict(item)
                for item in entries
                if isinstance(item, Mapping) and str(item.get("id", "")).strip()
            ]
            if normalized_entries:
                overlays[str(scope_key)] = normalized_entries
        return overlays

    @staticmethod
    def _split_interactable_scope_key(scope_key: str) -> tuple[str | None, str | None]:
        normalized = str(scope_key).strip()
        if not normalized:
            return None, None
        if "__" not in normalized:
            return normalized, None
        location_id, room_id = normalized.split("__", 1)
        location_id = location_id.strip()
        room_id = room_id.strip()
        if not location_id:
            return None, None
        return location_id, room_id or None

    @classmethod
    def _migrate_duplicate_facilities(cls, area_id: str, state: AreaState) -> None:
        if not state.temporary_sub_areas:
            return
        surviving: list[dict[str, Any]] = []
        for raw_sub_area in state.temporary_sub_areas:
            if not isinstance(raw_sub_area, Mapping):
                continue
            sub_area = dict(raw_sub_area)
            sub_area_id = cls._coerce_non_empty_string(sub_area.get("id"))
            if sub_area_id is None:
                continue
            target = duplicate_facility_target(area_id, sub_area_id)
            if target is None:
                surviving.append(sub_area)
                continue
            cls._merge_duplicate_sub_area_into_overlay(area_id, state, sub_area, target)
        state.temporary_sub_areas = surviving

    @classmethod
    def _merge_duplicate_sub_area_into_overlay(
        cls,
        area_id: str,
        state: AreaState,
        sub_area: dict[str, Any],
        target: Mapping[str, Any],
    ) -> None:
        location_id = cls._coerce_non_empty_string(target.get("location_id"))
        if location_id is None:
            return
        room_id = cls._coerce_non_empty_string(target.get("room_id"))
        scope_key = cls.interactable_scope_key(location_id, room_id)
        bucket = [
            dict(item)
            for item in state.scoped_interactable_overlays.get(scope_key, [])
            if isinstance(item, Mapping)
        ]
        cls._merge_overlay_entries(
            bucket,
            cls._build_duplicate_overlay_entries(area_id, sub_area, target),
        )
        if bucket:
            state.scoped_interactable_overlays[scope_key] = bucket

    @classmethod
    def _build_duplicate_overlay_entries(
        cls,
        area_id: str,
        sub_area: Mapping[str, Any],
        target: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        canonical_id = cls._coerce_non_empty_string(target.get("interactable_id"))
        if (
            canonical_id is not None
            or cls._coerce_non_empty_string(sub_area.get("description")) is not None
            or cls._coerce_non_empty_string(sub_area.get("label")) is not None
        ):
            synthesized = cls._overlay_from_sub_area(sub_area, default_id=canonical_id)
            if synthesized is not None:
                entries.append(synthesized)
        raw_interactables = sub_area.get("interactables", [])
        if isinstance(raw_interactables, list):
            for raw_item in raw_interactables:
                remapped = cls._remap_duplicate_overlay_entry(area_id, raw_item)
                if remapped is not None:
                    entries.append(remapped)
        return entries

    @classmethod
    def _overlay_from_sub_area(
        cls,
        sub_area: Mapping[str, Any],
        *,
        default_id: str | None = None,
    ) -> dict[str, Any] | None:
        overlay_id = default_id or cls._coerce_non_empty_string(sub_area.get("id"))
        if overlay_id is None:
            return None
        name = (
            cls._coerce_non_empty_string(sub_area.get("label"))
            or cls._coerce_non_empty_string(sub_area.get("name"))
            or overlay_id
        )
        description = str(sub_area.get("description", "")).strip()
        overlay: dict[str, Any] = {
            "id": overlay_id,
            "name": name,
            "description": description,
            "type": "inspect",
            "tags": [
                str(tag).strip()
                for tag in sub_area.get("tags", [])
                if str(tag).strip()
            ] if isinstance(sub_area.get("tags"), list) else [],
        }
        return overlay

    @classmethod
    def _remap_duplicate_overlay_entry(
        cls,
        area_id: str,
        raw_item: Any,
    ) -> dict[str, Any] | None:
        if isinstance(raw_item, Mapping):
            normalized = dict(raw_item)
            original_id = cls._coerce_non_empty_string(normalized.get("id"))
            if original_id is None:
                return None
            target = duplicate_facility_target(area_id, original_id)
            canonical_id = cls._coerce_non_empty_string(
                target.get("interactable_id")
            ) if isinstance(target, Mapping) else None
            if canonical_id is not None:
                normalized["id"] = canonical_id
                normalized.setdefault("type", "inspect")
            return normalized
        if isinstance(raw_item, str):
            interactable_id = raw_item.strip()
            if not interactable_id:
                return None
            target = duplicate_facility_target(area_id, interactable_id)
            canonical_id = cls._coerce_non_empty_string(
                target.get("interactable_id")
            ) if isinstance(target, Mapping) else None
            resolved_id = canonical_id or interactable_id
            return {"id": resolved_id, "name": resolved_id, "description": "", "type": "inspect"}
        return None

    @staticmethod
    def _merge_overlay_entries(
        bucket: list[dict[str, Any]],
        new_entries: list[dict[str, Any]],
    ) -> None:
        by_id = {
            str(item.get("id", "")).strip(): index
            for index, item in enumerate(bucket)
            if str(item.get("id", "")).strip()
        }
        for raw_entry in new_entries:
            if not isinstance(raw_entry, Mapping):
                continue
            entry = dict(raw_entry)
            entry_id = str(entry.get("id", "")).strip()
            if not entry_id:
                continue
            existing_index = by_id.get(entry_id)
            if existing_index is not None:
                merged = dict(bucket[existing_index])
                merged.update(entry)
                bucket[existing_index] = merged
                continue
            bucket.append(entry)
            by_id[entry_id] = len(bucket) - 1

    def copy_hostile_state(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._copy_hostile_payload(payload)

    def participant_snapshots(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        raw_participants = payload.get("participants", [])
        return self._normalized_participants(raw_participants)

    def update_hostile_participants(
        self,
        payload: Mapping[str, Any],
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        updated = self._copy_hostile_payload(payload)
        updated["participants"] = self._normalized_participants(participants)
        return self._normalize_hostile_payload(updated)

    def build_cleared_hostile(
        self,
        payload: Mapping[str, Any],
        *,
        current_tick: int | None,
    ) -> dict[str, Any]:
        updated = self._copy_hostile_payload(payload)
        updated["status"] = "cleared"
        updated["cleared"] = True
        updated["blocking"] = False
        updated["combat_active"] = False
        updated["cleared_at_tick"] = current_tick
        return self._normalize_hostile_payload(updated)

    def build_combat_hostile(
        self,
        payload: Mapping[str, Any],
        participants: list[dict[str, Any]],
        *,
        blocking: bool,
        player_flags: Mapping[str, Any] | None = None,
        current_tick: int | None = None,
        status_if_active: str = "engaged",
    ) -> tuple[dict[str, Any], bool, bool]:
        updated = self.update_hostile_participants(payload, participants)
        combat_cleared = self.all_participants_defeated(
            self.participant_snapshots(updated)
        )
        if player_flags is not None:
            updated["player_flags"] = {
                str(key): bool(value)
                for key, value in player_flags.items()
            }
        updated["blocking"] = False if combat_cleared else bool(blocking)
        if combat_cleared:
            updated = self.build_cleared_hostile(updated, current_tick=current_tick)
        else:
            updated["status"] = status_if_active
            updated["cleared"] = False
            updated["combat_active"] = True
            updated.pop("cleared_at_tick", None)
            updated = self._normalize_hostile_payload(updated)
        return (
            updated,
            bool(updated.get("combat_active", False)),
            bool(updated.get("cleared", False)),
        )

    def resolve_participant(
        self,
        target: str,
        participants: list[dict[str, Any]],
        *,
        by_monster_id_only: bool = False,
    ) -> tuple[int, dict[str, Any]] | None:
        for index, participant in enumerate(participants):
            if str(participant.get("id", "")).strip() == target:
                return (index, self._copy_participant(participant))
        for index, participant in enumerate(participants):
            if self.participant_monster_id(participant) == target:
                return (index, self._copy_participant(participant))
        if by_monster_id_only:
            return None

        target_folded = target.casefold()
        for index, participant in enumerate(participants):
            if self.participant_name(participant).casefold() == target_folded:
                return (index, self._copy_participant(participant))
        return None

    def participant_monster_id(self, participant: Mapping[str, Any]) -> str:
        return self._coerce_non_empty_string(participant.get("monster_id")) or ""

    def participant_name(self, participant: Mapping[str, Any]) -> str:
        return (
            self._coerce_non_empty_string(participant.get("name"))
            or self.participant_monster_id(participant)
            or "unknown_target"
        )

    def participant_hp(self, participant: Mapping[str, Any]) -> int:
        hp = self._coerce_int(participant.get("hp"))
        if hp is None:
            hp = self._coerce_int(participant.get("max_hp"))
        if hp is None:
            return 0
        return max(0, hp)

    def participant_ac(self, participant: Mapping[str, Any]) -> int:
        ac = self._coerce_int(participant.get("ac"))
        if ac is None or ac < 1:
            return 10
        return ac

    def participant_effects(self, participant: Mapping[str, Any]) -> list[dict[str, Any]]:
        raw_effects = participant.get("active_effects", [])
        if not isinstance(raw_effects, list):
            return []
        return [
            dict(effect)
            for effect in raw_effects
            if isinstance(effect, Mapping)
        ]

    def all_participants_defeated(self, participants: list[dict[str, Any]]) -> bool:
        return bool(participants) and all(
            not bool(participant.get("alive", False))
            for participant in participants
        )

    @classmethod
    def _copy_permanent_slot_bucket(cls, bucket: Mapping[str, Any]) -> dict[str, Any]:
        normalized = cls._normalize_permanent_slot_bucket(bucket, default_max_slots=0)
        return {
            "max_slots": normalized["max_slots"],
            "active_ids": list(normalized["active_ids"]),
            "refresh_queue": [
                cls._copy_refresh_queue_entry(entry)
                for entry in normalized["refresh_queue"]
            ],
        }

    @classmethod
    def _normalize_permanent_slot_bucket(
        cls,
        raw: Any,
        *,
        default_max_slots: int,
    ) -> dict[str, Any]:
        fallback_max_slots = max(0, cls._coerce_int(default_max_slots) or 0)
        if not isinstance(raw, Mapping):
            return {
                "max_slots": fallback_max_slots,
                "active_ids": [],
                "refresh_queue": [],
            }

        max_slots = cls._coerce_int(raw.get("max_slots"))
        if max_slots is None:
            max_slots = fallback_max_slots
        max_slots = max(0, max_slots)

        active_ids: list[str] = []
        seen_active_ids: set[str] = set()
        raw_active_ids = raw.get("active_ids", [])
        if isinstance(raw_active_ids, list):
            for value in raw_active_ids:
                normalized_id = cls._coerce_non_empty_string(value)
                if normalized_id is None or normalized_id in seen_active_ids:
                    continue
                seen_active_ids.add(normalized_id)
                active_ids.append(normalized_id)

        refresh_queue: list[dict[str, Any]] = []
        raw_refresh_queue = raw.get("refresh_queue", [])
        if isinstance(raw_refresh_queue, list):
            for entry in raw_refresh_queue:
                if not isinstance(entry, Mapping):
                    continue
                refresh_at_tick = cls._coerce_int(entry.get("refresh_at_tick"))
                if refresh_at_tick is None:
                    continue
                refresh_queue.append(
                    {
                        "refresh_at_tick": refresh_at_tick,
                        "used_template_ids": cls._normalize_slot_ids(
                            entry.get("used_template_ids")
                        ),
                    }
                )

        return {
            "max_slots": max_slots,
            "active_ids": active_ids,
            "refresh_queue": refresh_queue,
        }

    @classmethod
    def _copy_refresh_queue_entry(cls, entry: Mapping[str, Any]) -> dict[str, Any]:
        refresh_at_tick = cls._coerce_int(entry.get("refresh_at_tick")) or 0
        return {
            "refresh_at_tick": refresh_at_tick,
            "used_template_ids": cls._normalize_slot_ids(
                entry.get("used_template_ids")
            ),
        }

    @classmethod
    def _upsert_refresh_queue_entry(
        cls,
        refresh_queue: list[dict[str, Any]],
        *,
        refresh_at_tick: int,
        used_ids: list[str],
    ) -> None:
        normalized_used_ids = cls._normalize_slot_ids(used_ids)
        if normalized_used_ids:
            for entry in refresh_queue:
                if entry.get("used_template_ids") != normalized_used_ids:
                    continue
                existing_tick = cls._coerce_int(entry.get("refresh_at_tick")) or 0
                entry["refresh_at_tick"] = max(existing_tick, int(refresh_at_tick))
                return
        refresh_queue.append(
            {
                "refresh_at_tick": int(refresh_at_tick),
                "used_template_ids": normalized_used_ids,
            }
        )

    @classmethod
    def _normalize_slot_ids(cls, raw_ids: Any) -> list[str]:
        if not isinstance(raw_ids, list):
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for value in raw_ids:
            normalized_id = cls._coerce_non_empty_string(value)
            if normalized_id is None or normalized_id in seen:
                continue
            seen.add(normalized_id)
            normalized.append(normalized_id)
        return normalized

    @staticmethod
    def _normalize_hostile_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized = {str(key): value for key, value in payload.items()}

        if "area_id" in normalized:
            normalized["area_id"] = AreaSlice._coerce_text(normalized.get("area_id"))
        if "status" in normalized:
            normalized["status"] = AreaSlice._coerce_text(normalized.get("status"))
        for key in ("cleared", "blocking", "combat_active"):
            if key in normalized:
                normalized[key] = bool(normalized.get(key))
        if "cleared_at_tick" in normalized:
            cleared_tick = AreaSlice._coerce_int(normalized.get("cleared_at_tick"))
            normalized["cleared_at_tick"] = (
                cleared_tick if cleared_tick is not None else None
            )

        if "participants" in payload:
            normalized["participants"] = AreaSlice._normalized_participants(
                payload.get("participants")
            )
        elif "participants" in normalized:
            normalized["participants"] = []
        return normalized

    @staticmethod
    def _copy_hostile_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
        copied = {str(key): value for key, value in payload.items()}
        if "participants" in payload:
            copied["participants"] = AreaSlice._normalized_participants(
                payload.get("participants")
            )
        return copied

    @staticmethod
    def _normalized_participants(raw_participants: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_participants, list):
            return []
        return [
            AreaSlice._normalize_participant(participant)
            for participant in raw_participants
            if isinstance(participant, Mapping)
        ]

    @staticmethod
    def _copy_participant(participant: Mapping[str, Any]) -> dict[str, Any]:
        return AreaSlice._normalize_participant(participant)

    @staticmethod
    def _normalize_participant(participant: Mapping[str, Any]) -> dict[str, Any]:
        normalized = {str(key): value for key, value in participant.items()}

        for key in ("id", "monster_id", "name"):
            if key in normalized:
                normalized[key] = AreaSlice._coerce_text(normalized.get(key))
        for key in ("hp", "max_hp", "ac"):
            value = AreaSlice._coerce_int(normalized.get(key))
            if value is not None:
                normalized[key] = value
        if "alive" in normalized:
            normalized["alive"] = bool(normalized.get("alive"))

        if "active_effects" in participant:
            raw_effects = participant.get("active_effects")
            if isinstance(raw_effects, list):
                normalized_effects = [
                    dict(effect)
                    for effect in raw_effects
                    if isinstance(effect, Mapping)
                ]
                if normalized_effects:
                    normalized["active_effects"] = normalized_effects
                else:
                    normalized.pop("active_effects", None)
            else:
                normalized.pop("active_effects", None)
        return normalized

    @staticmethod
    def _coerce_text(value: Any) -> str:
        if value is None:
            return ""
        return str(value)

    @staticmethod
    def _coerce_non_empty_string(value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            return None
        return normalized

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return None
            try:
                return int(normalized)
            except ValueError:
                return None
        return None
