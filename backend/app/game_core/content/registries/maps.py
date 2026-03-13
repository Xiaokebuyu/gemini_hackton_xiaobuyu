"""MapRegistry implementation."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from app.game_core.content.base import ContentRegistry
from app.game_core.content.registries.map_types import (
    CheckPath,
    ContainerData,
    Discovery,
    EncounterEntry,
    HostileConfig,
    HostileGroup,
    HostileTemplate,
    InteractableTemplate,
    RoomTemplate,
    SubAreaClusterConfig,
    SubLocationTemplate,
    TrapData,
)
from app.game_core.content.registries.shared_types import LootTableDef
from app.game_core.scene_interactables import normalize_functional_binding


@dataclass(slots=True)
class Connection:
    """单条区域连接，含旅行元数据。"""

    target: str
    type: str = "travel"             # travel / gate / secret / teleport
    travel_time_minutes: int = 60    # 原始旅行分钟数
    travel_slots: int = 1            # 时间格数 = max(1, ceil(minutes / 60))
    description: str = ""
    tags: list[str] = field(default_factory=list)
    blocked: bool = False


@dataclass(slots=True)
class AreaTemplate:
    id: str
    name: str = ""
    region: str = ""
    base_danger: float | None = None
    connections: list[Connection] = field(default_factory=list)
    sub_locations: dict[str, SubLocationTemplate] = field(default_factory=dict)
    default_sub_location: str = ""   # 进入此区域时玩家自动放置的子地点 ID
    description: str = ""
    danger_level: str = ""                              # low / medium / high / extreme
    discoveries: list[Discovery] = field(default_factory=list)
    hostile_pool: list[HostileTemplate] | None = None
    sub_area_cluster_config: SubAreaClusterConfig | None = None
    encounter_table: list[EncounterEntry] = field(default_factory=list)
    encounter_slot_capacity: int = 1
    is_starting_area: bool = False
    tags: list[str] = field(default_factory=list)
    terrain_type: str = ""           # plains / forest / mountain / swamp / urban / underground


class MapRegistry(ContentRegistry):
    """Registry for area and map templates."""

    def __init__(self) -> None:
        super().__init__("maps")
        self._items: dict[str, AreaTemplate] = {}
        self._load_issues: list[str] = []

    def load(self, data: dict[str, Any]) -> None:
        self._items = {}
        self._load_issues = []
        raw_items = self._coerce_dict_mapping(data)
        for item_id, raw in raw_items.items():
            template = self._build_template(item_id, raw)
            if template is not None:
                self._items[item_id] = template

    def get(self, content_id: str) -> AreaTemplate | None:
        return self._items.get(content_id)

    def list_all(self) -> list[AreaTemplate]:
        return list(self._items.values())

    def starting_area(self) -> AreaTemplate | None:
        for item in self._items.values():
            if item.is_starting_area:
                return item

        lowest: AreaTemplate | None = None
        lowest_danger: float | None = None
        for item in self._items.values():
            danger = item.base_danger if item.base_danger is not None else 1.0
            if lowest_danger is None or danger < lowest_danger:
                lowest_danger = danger
                lowest = item
        return lowest

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_adjacent(self, area_id: str) -> list[str]:
        """Return adjacent area IDs for the given area."""
        item = self._items.get(area_id)
        if item is None:
            return []
        return [conn.target for conn in item.connections]

    def get_connections(self, area_id: str) -> list[Connection]:
        """Return full Connection objects for the given area."""
        item = self._items.get(area_id)
        if item is None:
            return []
        return list(item.connections)

    def get_connection(self, from_id: str, to_id: str) -> Connection | None:
        """Return the Connection from from_id to to_id, or None if not found."""
        item = self._items.get(from_id)
        if item is None:
            return None
        for conn in item.connections:
            if conn.target == to_id:
                return conn
        return None

    def resolve_encounter_map_category(
        self,
        area_id: str,
        encounter: EncounterEntry | Mapping[str, Any] | None = None,
    ) -> str | None:
        """Return the preferred battle-map category for one area encounter.

        Resolution order:
        1. explicit encounter.map_category
        2. area tag / area-id / area-name semantic match
        3. area terrain_type fallback
        """
        if isinstance(encounter, EncounterEntry):
            explicit = self._coerce_non_empty_string(encounter.map_category)
            if explicit is not None:
                return explicit
        elif isinstance(encounter, Mapping):
            explicit = self._coerce_non_empty_string(encounter.get("map_category"))
            if explicit is not None:
                return explicit

        area = self._items.get(area_id)
        if area is None:
            return None

        area_text = f"{area.id} {area.name}".strip().lower()
        tag_set = {
            str(tag).strip().lower()
            for tag in area.tags
            if str(tag).strip()
        }
        terrain_type = str(area.terrain_type or "").strip().lower()

        def _matches(*keywords: str) -> bool:
            for keyword in keywords:
                token = keyword.strip().lower()
                if not token:
                    continue
                if token in tag_set or token in area_text:
                    return True
            return False

        if _matches("bridge"):
            return "bridge"
        if _matches("temple", "church") or "religious" in tag_set:
            return "temple"
        if _matches("camp") or "home" in tag_set:
            return "camp"
        if _matches("ruins", "dungeon"):
            return "ruins"
        if terrain_type == "urban":
            return "town_street"
        if terrain_type == "plains":
            return "plains"
        if terrain_type == "underground":
            return "cave"
        if terrain_type in {"forest", "woodland"}:
            return "woodland"
        if terrain_type in {"hill", "hills", "mountain"}:
            return "hills"
        if terrain_type == "swamp":
            return "swamp"
        if _matches("forest", "woodland"):
            return "woodland"
        if _matches("hill", "mountain"):
            return "hills"
        if _matches("swamp"):
            return "swamp"
        return None

    def get_by_region(self, region: str) -> list[AreaTemplate]:
        """Return areas matching the given region."""
        normalized = region.strip().lower()
        return [
            item
            for item in self._items.values()
            if item.region.strip().lower() == normalized
        ]

    def get_sub_location(self, area_id: str, loc_id: str) -> SubLocationTemplate | None:
        """Get a specific sub-location from an area."""
        area = self.get(area_id)
        if area is None:
            return None
        return area.sub_locations.get(loc_id)

    def resolve_auto_sub_location(self, area_id: str) -> str | None:
        """Return the sub-location used for automatic area entry placement."""
        area = self.get(area_id)
        if area is None:
            return None
        sub_locations = area.sub_locations
        if not sub_locations:
            return None
        default = self._coerce_non_empty_string(area.default_sub_location)
        if default is not None and default in sub_locations:
            return default
        for key in sub_locations:
            normalized = self._coerce_non_empty_string(key)
            if normalized is not None:
                return normalized
        return None

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        issues = list(self._load_issues)

        starting_areas = [t.id for t in self._items.values() if t.is_starting_area]
        if len(starting_areas) > 1:
            ids = ", ".join(starting_areas)
            issues.append(f"multiple starting areas detected: {ids}")

        return issues

    # ------------------------------------------------------------------
    # Build helpers — AreaTemplate
    # ------------------------------------------------------------------

    def _build_template(
        self, item_id: str, raw: dict[str, Any],
    ) -> AreaTemplate | None:
        entry_id = self._coerce_non_empty_string(raw.get("id"))
        if not entry_id:
            self._load_issues.append(f"map '{item_id}' missing id")
            return None

        # base_danger — numeric preferred; string danger_level is a fallback
        raw_base_danger = raw.get("base_danger")
        # backward compat: if base_danger absent, try danger_level only when numeric
        if raw_base_danger is None and isinstance(raw.get("danger_level"), (int, float)):
            raw_base_danger = raw.get("danger_level")
        base_danger: float | None = None
        if raw_base_danger is not None:
            base_danger = self._coerce_float(raw_base_danger)
            if base_danger is None or base_danger < 0:
                self._load_issues.append(f"map '{item_id}' has invalid base_danger")
                base_danger = None
        # string→float fallback: convert semantic danger_level strings when no numeric value
        if base_danger is None:
            _DANGER_STR_MAP: dict[str, float] = {
                "none": 0.0,
                "low": 0.3,
                "medium": 0.6,
                "high": 1.0,
                "extreme": 1.5,
            }
            dl_raw = raw.get("danger_level")
            if isinstance(dl_raw, str):
                dl_key = dl_raw.strip().lower()
                if dl_key in _DANGER_STR_MAP:
                    base_danger = _DANGER_STR_MAP[dl_key]

        # connections — merge adjacent_areas alias
        connections = self._load_connections(raw, item_id)

        # sub_locations → dict[str, SubLocationTemplate]
        sub_locations = self._load_sub_locations(raw, item_id)

        # description / danger_level string
        description = str(raw.get("description", "")).strip()
        danger_level_raw = raw.get("danger_level")
        danger_level = (
            str(danger_level_raw).strip()
            if danger_level_raw is not None and not isinstance(danger_level_raw, (int, float))
            else ""
        )

        # discoveries
        discoveries: list[Discovery] = []
        raw_disc = raw.get("discoveries")
        if isinstance(raw_disc, list):
            for disc_raw in raw_disc:
                if isinstance(disc_raw, Mapping):
                    d = self._build_discovery(item_id, disc_raw)
                    if d is not None:
                        discoveries.append(d)

        # hostile_pool
        hostile_pool: list[HostileTemplate] | None = None
        raw_hp = raw.get("hostile_pool")
        if isinstance(raw_hp, list):
            hostile_pool = []
            for ht_raw in raw_hp:
                if isinstance(ht_raw, Mapping):
                    ht = self._build_hostile_template(item_id, ht_raw)
                    if ht is not None:
                        hostile_pool.append(ht)

        # sub_area_cluster_config
        sub_area_cluster_config: SubAreaClusterConfig | None = None
        raw_sac = raw.get("sub_area_cluster_config")
        if isinstance(raw_sac, Mapping):
            sub_area_cluster_config = self._build_cluster_config(raw_sac)

        # encounter_slot_capacity — 优先新字段，向后兼容旧 encounter_profile.slot_capacity
        encounter_slot_capacity = 1
        raw_sc = raw.get("encounter_slot_capacity")
        if raw_sc is None:
            old_profile = raw.get("encounter_profile")
            if isinstance(old_profile, Mapping):
                raw_sc = old_profile.get("slot_capacity")
        if raw_sc is not None:
            coerced_sc = self._coerce_non_negative_int(raw_sc)
            if coerced_sc is not None:
                encounter_slot_capacity = coerced_sc
            else:
                self._load_issues.append(
                    f"map '{item_id}' has invalid encounter_slot_capacity"
                )

        # encounter_table — 优先新格式，兼容旧 encounter_profile dict
        encounter_table: list[EncounterEntry] = []
        raw_encounter = raw.get("encounter_table") or raw.get("encounter_profile")
        if isinstance(raw_encounter, list):
            for idx, entry in enumerate(raw_encounter):
                if isinstance(entry, Mapping):
                    encounter_table.append(
                        self._build_encounter_entry(item_id, idx, entry)
                    )
        elif isinstance(raw_encounter, Mapping):
            templates = raw_encounter.get("templates", [])
            if isinstance(templates, list):
                for idx, entry in enumerate(templates):
                    if isinstance(entry, Mapping):
                        encounter_table.append(
                            self._build_encounter_entry(item_id, idx, entry)
                        )

        # is_starting_area — merge 3 flags
        is_starting = self._load_starting_flag(raw, item_id)

        # region
        region = ""
        raw_region = raw.get("region")
        if raw_region is not None:
            r = self._coerce_non_empty_string(raw_region)
            if r is None:
                self._load_issues.append(f"map '{item_id}' has invalid region")
            else:
                region = r

        # tags
        raw_tags = raw.get("tags", [])
        tags = (
            [str(t) for t in raw_tags if str(t).strip()]
            if isinstance(raw_tags, list)
            else []
        )

        # name
        name = ""
        raw_name = raw.get("name")
        if raw_name is not None:
            name = str(raw_name).strip()

        terrain_type = str(raw.get("terrain_type", "")).strip()

        # default_sub_location — must exist in sub_locations if non-empty
        default_sub_location = ""
        raw_dsl = raw.get("default_sub_location")
        if raw_dsl is not None:
            dsl = self._coerce_non_empty_string(raw_dsl)
            if dsl is None:
                self._load_issues.append(
                    f"map '{item_id}' has invalid default_sub_location"
                )
            else:
                default_sub_location = dsl

        return AreaTemplate(
            id=entry_id,
            name=name,
            region=region,
            base_danger=base_danger,
            connections=connections,
            sub_locations=sub_locations,
            default_sub_location=default_sub_location,
            description=description,
            danger_level=danger_level,
            discoveries=discoveries,
            hostile_pool=hostile_pool,
            sub_area_cluster_config=sub_area_cluster_config,
            encounter_table=encounter_table,
            encounter_slot_capacity=encounter_slot_capacity,
            is_starting_area=is_starting,
            tags=tags,
            terrain_type=terrain_type,
        )

    def _load_connections(
        self, raw: dict[str, Any], item_id: str,
    ) -> list[Connection]:
        connections: list[Connection] = []
        for conn_field in ("connections", "adjacent_areas"):
            raw_conn = raw.get(conn_field)
            if raw_conn is None:
                continue
            if not isinstance(raw_conn, list):
                self._load_issues.append(
                    f"map '{item_id}' has invalid {conn_field}"
                )
                continue
            for idx, entry in enumerate(raw_conn):
                conn = self._parse_connection_entry(entry)
                if conn is None:
                    self._load_issues.append(
                        f"map '{item_id}' {conn_field}[{idx}] must be a non-empty string"
                    )
                else:
                    connections.append(conn)
        return connections

    def _parse_connection_entry(self, entry: Any) -> Connection | None:
        """Parse a raw connection entry (str or dict) into a Connection."""
        if isinstance(entry, str):
            target = self._coerce_non_empty_string(entry)
            if target is None:
                return None
            return Connection(target=target)
        if isinstance(entry, Mapping):
            target_raw = entry.get("target_map_id") or entry.get("target")
            target = self._coerce_non_empty_string(target_raw)
            if target is None:
                return None
            conn_type_raw = entry.get("connection_type") or entry.get("type")
            conn_type = self._coerce_non_empty_string(conn_type_raw) or "travel"
            travel_time_raw = entry.get("travel_time", "")
            minutes = self._parse_travel_minutes(str(travel_time_raw)) if travel_time_raw else 60
            travel_slots = max(1, math.ceil(minutes / 60))
            description = str(entry.get("description", "")).strip()
            raw_tags = entry.get("tags", [])
            tags = [str(t) for t in raw_tags if str(t).strip()] if isinstance(raw_tags, list) else []
            blocked = bool(entry.get("blocked", False))
            return Connection(
                target=target,
                type=conn_type,
                travel_time_minutes=minutes,
                travel_slots=travel_slots,
                description=description,
                tags=tags,
                blocked=blocked,
            )
        return None

    @staticmethod
    def _parse_travel_minutes(travel_time: str) -> int:
        """Parse travel time strings like '30分钟', '2小时', '2小时30分钟' → minutes.

        Falls back to 60 if the string cannot be parsed.
        """
        total = 0
        hours_match = re.search(r"(\d+)\s*小时", travel_time)
        minutes_match = re.search(r"(\d+)\s*分钟", travel_time)
        if hours_match:
            total += int(hours_match.group(1)) * 60
        if minutes_match:
            total += int(minutes_match.group(1))
        return total if total > 0 else 60

    def _load_sub_locations(
        self, raw: dict[str, Any], item_id: str,
    ) -> dict[str, SubLocationTemplate]:
        raw_sub = raw.get("sub_locations")
        if raw_sub is None:
            return {}
        if isinstance(raw_sub, list):
            result: dict[str, SubLocationTemplate] = {}
            for entry in raw_sub:
                if not isinstance(entry, Mapping):
                    continue
                built = self._build_sub_location(item_id, entry)
                if built is not None:
                    result[built.id] = built
            return result
        if not isinstance(raw_sub, Mapping):
            self._load_issues.append(
                f"map '{item_id}' has invalid sub_locations"
            )
            return {}
        result = {}
        for sub_key, sub_val in raw_sub.items():
            if not isinstance(sub_val, Mapping):
                self._load_issues.append(
                    f"map '{item_id}' sub_location '{sub_key}' must be a mapping"
                )
                continue
            built = self._build_sub_location(item_id, sub_val, sub_key=str(sub_key))
            if built is not None:
                result[str(sub_key)] = built
        return result

    def _load_starting_flag(
        self, raw: dict[str, Any], item_id: str,
    ) -> bool:
        is_starting = False
        for flag in ("is_starting_area", "starting_area", "is_start"):
            val = raw.get(flag)
            if val is None:
                continue
            if not self._is_bool_like(val):
                self._load_issues.append(
                    f"map '{item_id}' has invalid {flag}"
                )
            elif bool(val):
                is_starting = True
        return is_starting

    # ------------------------------------------------------------------
    # Build helpers — SubLocationTemplate
    # ------------------------------------------------------------------

    def _build_sub_location(
        self, area_id: str, raw: dict[str, Any], sub_key: str | None = None,
    ) -> SubLocationTemplate | None:
        raw_id = raw.get("id")
        sid = self._coerce_non_empty_string(raw_id)
        if sid is None:
            if raw_id is not None and sub_key is not None:
                # id key present but value invalid (empty/whitespace)
                self._load_issues.append(
                    f"map '{area_id}' sub_location '{sub_key}' has invalid id"
                )
            else:
                self._load_issues.append(f"map '{area_id}' sub_location missing id")
            return None

        name = str(raw.get("name", "")).strip()
        description = str(raw.get("description", "")).strip()

        raw_tags = raw.get("tags", [])
        tags = [str(t) for t in raw_tags if str(t).strip()] if isinstance(raw_tags, list) else []

        # type: accept interaction_type as alias
        sl_type = str(raw.get("type") or raw.get("interaction_type") or "visit").strip() or "visit"

        # available_hours: (int, int) tuple or None
        available_hours: tuple[int, int] | None = None
        raw_ah = raw.get("available_hours")
        if raw_ah is not None:
            if isinstance(raw_ah, (list, tuple)) and len(raw_ah) == 2:
                try:
                    available_hours = (int(raw_ah[0]), int(raw_ah[1]))
                except (TypeError, ValueError):
                    self._load_issues.append(
                        f"map '{area_id}' sub_location '{sid}' has invalid available_hours"
                    )
            else:
                self._load_issues.append(
                    f"map '{area_id}' sub_location '{sid}' has invalid available_hours"
                )

        # resident_npcs
        raw_npcs = raw.get("resident_npcs", [])
        resident_npcs = (
            [str(n) for n in raw_npcs if str(n).strip()]
            if isinstance(raw_npcs, list)
            else []
        )

        # interactables
        interactables: list[InteractableTemplate] = []
        raw_ias = raw.get("interactables", [])
        if isinstance(raw_ias, list):
            for idx, raw_ia in enumerate(raw_ias):
                if isinstance(raw_ia, Mapping):
                    ia = self._build_interactable(area_id, sid, idx, raw_ia)
                    if ia is not None:
                        interactables.append(ia)

        # hostile_config
        hostile_config: HostileConfig | None = None
        raw_hc = raw.get("hostile_config")
        if isinstance(raw_hc, Mapping):
            hostile_config = self._build_hostile_config(area_id, sid, raw_hc)

        # rooms
        rooms = self._load_rooms(area_id, sid, raw)

        # default_room — must not be empty when set
        default_room = ""
        raw_dr = raw.get("default_room")
        if raw_dr is not None:
            dr = self._coerce_non_empty_string(raw_dr)
            if dr is None:
                self._load_issues.append(
                    f"map '{area_id}' sub_location '{sid}' has invalid default_room"
                )
            else:
                default_room = dr

        return SubLocationTemplate(
            id=sid,
            name=name,
            description=description,
            tags=tags,
            type=sl_type,
            available_hours=available_hours,
            resident_npcs=resident_npcs,
            interactables=interactables,
            hostile_config=hostile_config,
            rooms=rooms,
            default_room=default_room,
        )

    def _load_rooms(
        self, area_id: str, sub_id: str, raw: dict[str, Any],
    ) -> dict[str, RoomTemplate]:
        raw_rooms = raw.get("rooms")
        if raw_rooms is None:
            return {}
        if isinstance(raw_rooms, list):
            result: dict[str, RoomTemplate] = {}
            for entry in raw_rooms:
                if not isinstance(entry, Mapping):
                    continue
                built = self._build_room(area_id, sub_id, entry)
                if built is not None:
                    result[built.id] = built
            return result
        if not isinstance(raw_rooms, Mapping):
            self._load_issues.append(
                f"map '{area_id}' sub_location '{sub_id}' has invalid rooms"
            )
            return {}
        result = {}
        for room_key, room_val in raw_rooms.items():
            if not isinstance(room_val, Mapping):
                self._load_issues.append(
                    f"map '{area_id}' sub_location '{sub_id}' room '{room_key}' must be a mapping"
                )
                continue
            built = self._build_room(area_id, sub_id, room_val, room_key=str(room_key))
            if built is not None:
                result[str(room_key)] = built
        return result

    def _build_room(
        self, area_id: str, sub_id: str, raw: dict[str, Any],
        room_key: str | None = None,
    ) -> RoomTemplate | None:
        raw_id = raw.get("id")
        rid = self._coerce_non_empty_string(raw_id)
        if rid is None:
            if raw_id is not None and room_key is not None:
                self._load_issues.append(
                    f"map '{area_id}' sub_location '{sub_id}' room '{room_key}' has invalid id"
                )
            else:
                self._load_issues.append(
                    f"map '{area_id}' sub_location '{sub_id}' room missing id"
                )
            return None

        name = str(raw.get("name", "")).strip()
        description = str(raw.get("description", "")).strip()

        raw_tags = raw.get("tags", [])
        tags = [str(t) for t in raw_tags if str(t).strip()] if isinstance(raw_tags, list) else []

        discoverable = bool(raw.get("discoverable", False))
        discovery_dc_raw = raw.get("discovery_dc", 0)
        discovery_dc = self._coerce_non_negative_int(discovery_dc_raw) or 0

        raw_npcs = raw.get("resident_npcs", [])
        resident_npcs = (
            [str(n) for n in raw_npcs if str(n).strip()]
            if isinstance(raw_npcs, list)
            else []
        )

        interactables: list[InteractableTemplate] = []
        raw_ias = raw.get("interactables", [])
        if isinstance(raw_ias, list):
            for idx, raw_ia in enumerate(raw_ias):
                if isinstance(raw_ia, Mapping):
                    ia = self._build_interactable(area_id, f"{sub_id}/{rid}", idx, raw_ia)
                    if ia is not None:
                        interactables.append(ia)

        return RoomTemplate(
            id=rid,
            name=name,
            description=description,
            tags=tags,
            discoverable=discoverable,
            discovery_dc=discovery_dc,
            resident_npcs=resident_npcs,
            interactables=interactables,
        )

    def _build_interactable(
        self, area_id: str, sub_id: str, idx: int, raw: dict[str, Any],
    ) -> InteractableTemplate | None:
        ia_id = self._coerce_non_empty_string(raw.get("id"))
        if ia_id is None:
            self._load_issues.append(
                f"map '{area_id}' sub_location '{sub_id}' interactables[{idx}] missing id"
            )
            return None

        name = str(raw.get("name", "")).strip()
        description = str(raw.get("description", "")).strip()
        ia_type = str(raw.get("type", "inspect")).strip() or "inspect"

        raw_tags = raw.get("tags", [])
        tags = [str(t) for t in raw_tags if str(t).strip()] if isinstance(raw_tags, list) else []

        one_time = bool(raw.get("one_time", False))

        # visibility_dc
        visibility_dc: int | None = None
        raw_vdc = raw.get("visibility_dc")
        if raw_vdc is not None:
            vdc = self._coerce_non_negative_int(raw_vdc)
            if vdc is not None:
                visibility_dc = vdc

        # checks
        checks: list[CheckPath] = []
        raw_checks = raw.get("checks", [])
        if isinstance(raw_checks, list):
            for cp_raw in raw_checks:
                if isinstance(cp_raw, Mapping):
                    checks.append(self._build_check_path(cp_raw))

        # reward
        reward: dict[str, Any] | None = None
        raw_reward = raw.get("reward")
        if isinstance(raw_reward, Mapping):
            reward = dict(raw_reward)

        functional = normalize_functional_binding(raw.get("functional"))

        # container_data (only when type == "container")
        container_data: ContainerData | None = None
        if ia_type == "container":
            raw_cd = raw.get("container_data")
            if isinstance(raw_cd, Mapping):
                container_data = self._build_container_data(area_id, sub_id, raw_cd)

        return InteractableTemplate(
            id=ia_id,
            name=name,
            description=description,
            type=ia_type,
            visibility_dc=visibility_dc,
            checks=checks,
            reward=reward,
            one_time=one_time,
            tags=tags,
            functional=functional or None,
            container_data=container_data,
        )

    def _build_check_path(self, raw: dict[str, Any]) -> CheckPath:
        skill = str(raw.get("skill", "")).strip()
        dc_raw = raw.get("dc")
        dc = self._coerce_non_negative_int(dc_raw) if dc_raw is not None else None
        label = str(raw.get("label", "")).strip()
        fail_raw = raw.get("fail_consequence")
        fail_consequence = str(fail_raw).strip() if fail_raw is not None else None
        return CheckPath(
            skill=skill,
            dc=dc if dc is not None else 10,
            label=label,
            fail_consequence=fail_consequence,
        )

    def _build_trap_data(self, raw: dict[str, Any]) -> TrapData:
        detect_dc = self._coerce_non_negative_int(raw.get("detect_dc")) or 15
        disarm_dc = self._coerce_non_negative_int(raw.get("disarm_dc")) or 15
        damage = str(raw.get("damage", "1d6")).strip() or "1d6"
        damage_type = str(raw.get("damage_type", "piercing")).strip() or "piercing"
        effect_raw = raw.get("effect")
        effect = str(effect_raw).strip() if effect_raw is not None else None
        return TrapData(
            detect_dc=detect_dc,
            disarm_dc=disarm_dc,
            damage=damage,
            damage_type=damage_type,
            effect=effect,
        )

    def _build_container_data(
        self, area_id: str, sub_id: str, raw: dict[str, Any],
    ) -> ContainerData | None:
        container_type = str(raw.get("container_type", "chest")).strip() or "chest"
        locked = bool(raw.get("locked", False))
        breakable = bool(raw.get("breakable", False))

        trap: TrapData | None = None
        raw_trap = raw.get("trap")
        if isinstance(raw_trap, Mapping):
            trap = self._build_trap_data(raw_trap)

        loot = LootTableDef()
        raw_loot = raw.get("loot")
        if isinstance(raw_loot, Mapping):
            loot_gold = str(raw_loot.get("gold", "0")).strip() or "0"
            raw_loot_items = raw_loot.get("items", [])
            loot_items = list(raw_loot_items) if isinstance(raw_loot_items, list) else []
            loot = LootTableDef(gold=loot_gold, items=loot_items)

        return ContainerData(
            container_type=container_type,
            locked=locked,
            breakable=breakable,
            trap=trap,
            loot=loot,
        )

    def _build_hostile_config(
        self, area_id: str, sub_id: str, raw: dict[str, Any],
    ) -> HostileConfig:
        stealth_dc = self._coerce_non_negative_int(raw.get("stealth_dc")) or 12
        alert_state = str(raw.get("alert_state", "unaware")).strip() or "unaware"
        blocking = bool(raw.get("blocking", True))
        ambient_description = str(raw.get("ambient_description", "")).strip()

        hostile_groups: list[HostileGroup] = []
        raw_groups = raw.get("hostile_groups", [])
        if isinstance(raw_groups, list):
            for idx, grp_raw in enumerate(raw_groups):
                if isinstance(grp_raw, Mapping):
                    grp = self._build_hostile_group(area_id, idx, grp_raw)
                    if grp is not None:
                        hostile_groups.append(grp)

        return HostileConfig(
            hostile_groups=hostile_groups,
            stealth_dc=stealth_dc,
            alert_state=alert_state,
            blocking=blocking,
            ambient_description=ambient_description,
        )

    def _build_hostile_group(
        self, area_id: str, idx: int, raw: dict[str, Any],
    ) -> HostileGroup | None:
        raw_mids = raw.get("monster_ids", [])
        monster_ids = (
            [str(m) for m in raw_mids if str(m).strip()]
            if isinstance(raw_mids, list)
            else []
        )
        count = str(raw.get("count", "1")).strip() or "1"
        role = str(raw.get("role", "guard")).strip() or "guard"
        return HostileGroup(monster_ids=monster_ids, count=count, role=role)

    # ------------------------------------------------------------------
    # Build helpers — AreaTemplate new fields
    # ------------------------------------------------------------------

    def _build_discovery(
        self, area_id: str, raw: dict[str, Any],
    ) -> Discovery | None:
        disc_id = self._coerce_non_empty_string(raw.get("id"))
        if disc_id is None:
            self._load_issues.append(f"map '{area_id}' discovery missing id")
            return None

        name = str(raw.get("name", "")).strip()
        check_type = str(raw.get("check_type", "perception")).strip() or "perception"
        dc = self._coerce_non_negative_int(raw.get("dc")) or 15

        raw_reward = raw.get("reward", {})
        reward = dict(raw_reward) if isinstance(raw_reward, Mapping) else {}

        raw_tags = raw.get("tags", [])
        tags = [str(t) for t in raw_tags if str(t).strip()] if isinstance(raw_tags, list) else []

        return Discovery(
            id=disc_id,
            name=name,
            check_type=check_type,
            dc=dc,
            reward=reward,
            tags=tags,
        )

    def _build_hostile_template(
        self, area_id: str, raw: dict[str, Any],
    ) -> HostileTemplate | None:
        ht_id = self._coerce_non_empty_string(raw.get("id"))
        if ht_id is None:
            self._load_issues.append(f"map '{area_id}' hostile_pool entry missing id")
            return None

        name = str(raw.get("name", "")).strip()
        description = str(raw.get("description", "")).strip()

        raw_tags = raw.get("tags", [])
        tags = [str(t) for t in raw_tags if str(t).strip()] if isinstance(raw_tags, list) else []

        hostile_config = HostileConfig()
        raw_hc = raw.get("hostile_config")
        if isinstance(raw_hc, Mapping):
            hostile_config = self._build_hostile_config(area_id, ht_id, raw_hc)

        interactables: list[InteractableTemplate] = []
        raw_ias = raw.get("interactables", [])
        if isinstance(raw_ias, list):
            for idx, raw_ia in enumerate(raw_ias):
                if isinstance(raw_ia, Mapping):
                    ia = self._build_interactable(area_id, ht_id, idx, raw_ia)
                    if ia is not None:
                        interactables.append(ia)

        refresh_delay_ticks = self._coerce_non_negative_int(raw.get("refresh_delay_ticks")) or 12
        min_danger_raw = raw.get("min_danger")
        min_danger = float(min_danger_raw) if min_danger_raw is not None else 0.0

        return HostileTemplate(
            id=ht_id,
            name=name,
            description=description,
            tags=tags,
            hostile_config=hostile_config,
            interactables=interactables,
            refresh_delay_ticks=refresh_delay_ticks,
            min_danger=min_danger,
        )

    def _build_cluster_config(self, raw: dict[str, Any]) -> SubAreaClusterConfig:
        max_dynamic = self._coerce_non_negative_int(raw.get("max_dynamic")) or 5
        max_permanent_dynamic = self._coerce_non_negative_int(raw.get("max_permanent_dynamic")) or 2
        raw_fill_tags = raw.get("fill_tags", [])
        fill_tags = (
            [str(t) for t in raw_fill_tags if str(t).strip()]
            if isinstance(raw_fill_tags, list)
            else []
        )
        fill_density = str(raw.get("fill_density", "normal")).strip() or "normal"
        return SubAreaClusterConfig(
            max_dynamic=max_dynamic,
            max_permanent_dynamic=max_permanent_dynamic,
            fill_tags=fill_tags,
            fill_density=fill_density,
        )

    # ------------------------------------------------------------------
    # Build helpers — EncounterEntry
    # ------------------------------------------------------------------

    def _build_encounter_entry(
        self, area_id: str, idx: int, raw: dict[str, Any],
    ) -> EncounterEntry:
        raw_id = self._coerce_non_empty_string(raw.get("id"))
        entry_id = raw_id if raw_id else f"{area_id}_{idx}"

        raw_mids = raw.get("monster_ids", [])
        monster_ids = (
            [str(m) for m in raw_mids if str(m).strip()]
            if isinstance(raw_mids, list)
            else []
        )
        if not monster_ids:
            self._load_issues.append(
                f"map '{area_id}' encounter_table[{idx}] has no monster_ids"
            )

        weight = 1.0
        raw_weight = raw.get("weight")
        if raw_weight is not None:
            try:
                weight = max(0.0, float(raw_weight))
            except (TypeError, ValueError):
                self._load_issues.append(
                    f"map '{area_id}' encounter_table[{idx}] has invalid weight"
                )

        min_danger = 0.0
        raw_min = raw.get("min_danger")
        if raw_min is not None:
            try:
                min_danger = max(0.0, float(raw_min))
            except (TypeError, ValueError):
                pass

        description = str(raw.get("description", "")).strip()
        map_category = self._coerce_non_empty_string(raw.get("map_category"))

        return EncounterEntry(
            id=entry_id,
            monster_ids=monster_ids,
            weight=weight,
            min_danger=min_danger,
            description=description,
            map_category=map_category,
        )
