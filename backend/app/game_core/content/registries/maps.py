"""MapRegistry implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.game_core.content.base import ContentRegistry


@dataclass(slots=True)
class AreaTemplate:
    id: str
    name: str = ""
    region: str = ""
    base_danger: float | None = None
    connections: list[str] = field(default_factory=list)
    sub_locations: dict[str, dict[str, Any]] = field(default_factory=dict)
    encounter_profile: dict[str, Any] | None = None
    is_starting_area: bool = False
    tags: list[str] = field(default_factory=list)


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
        return list(item.connections)

    def get_by_region(self, region: str) -> list[AreaTemplate]:
        """Return areas matching the given region."""
        normalized = region.strip().lower()
        return [
            item
            for item in self._items.values()
            if item.region.strip().lower() == normalized
        ]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        issues = list(self._load_issues)

        starting_areas = [t.id for t in self._items.values() if t.is_starting_area]
        if len(starting_areas) > 1:
            ids = ", ".join(starting_areas)
            issues.append(f"multiple starting areas detected: {ids}")

        for item_id, item in self._items.items():
            self._validate_encounter_profile(item_id, item.encounter_profile, issues)
        return issues

    # ------------------------------------------------------------------
    # Build helpers
    # ------------------------------------------------------------------

    def _build_template(
        self, item_id: str, raw: dict[str, Any],
    ) -> AreaTemplate | None:
        entry_id = self._coerce_non_empty_string(raw.get("id"))
        if not entry_id:
            self._load_issues.append(f"map '{item_id}' missing id")
            return None

        # base_danger — merge danger_level alias
        raw_danger = raw.get("base_danger", raw.get("danger_level"))
        base_danger: float | None = None
        if raw_danger is not None:
            base_danger = self._coerce_float(raw_danger)
            if base_danger is None or base_danger < 0:
                field_name = "base_danger" if "base_danger" in raw else "danger_level"
                self._load_issues.append(f"map '{item_id}' has invalid {field_name}")
                base_danger = None

        # connections — merge adjacent_areas alias
        connections = self._load_connections(raw, item_id)

        # sub_locations
        sub_locations = self._load_sub_locations(raw, item_id)

        # encounter_profile — keep as dict, deep validation in validate()
        raw_profile = raw.get("encounter_profile")
        encounter_profile: dict[str, Any] | None = None
        if raw_profile is not None:
            if isinstance(raw_profile, Mapping):
                encounter_profile = dict(raw_profile)
            else:
                self._load_issues.append(
                    f"map '{item_id}' has invalid encounter_profile"
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

        return AreaTemplate(
            id=entry_id,
            name=name,
            region=region,
            base_danger=base_danger,
            connections=connections,
            sub_locations=sub_locations,
            encounter_profile=encounter_profile,
            is_starting_area=is_starting,
            tags=tags,
        )

    def _load_connections(
        self, raw: dict[str, Any], item_id: str,
    ) -> list[str]:
        connections: list[str] = []
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
                s = self._coerce_non_empty_string(entry)
                if s is None:
                    self._load_issues.append(
                        f"map '{item_id}' {conn_field}[{idx}] must be a non-empty string"
                    )
                else:
                    connections.append(s)
        return connections

    def _load_sub_locations(
        self, raw: dict[str, Any], item_id: str,
    ) -> dict[str, dict[str, Any]]:
        raw_sub = raw.get("sub_locations")
        if raw_sub is None:
            return {}
        if isinstance(raw_sub, list):
            result: dict[str, dict[str, Any]] = {}
            for entry in raw_sub:
                if not isinstance(entry, Mapping):
                    continue
                sid = self._coerce_non_empty_string(entry.get("id"))
                if sid is not None:
                    result[sid] = dict(entry)
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
            sub_dict = dict(sub_val)
            if "id" in sub_dict:
                sid = self._coerce_non_empty_string(sub_dict.get("id"))
                if sid is None:
                    self._load_issues.append(
                        f"map '{item_id}' sub_location '{sub_key}' has invalid id"
                    )
            result[str(sub_key)] = sub_dict
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
    # Encounter profile deep validation (called from validate())
    # ------------------------------------------------------------------

    def _validate_encounter_profile(
        self,
        item_id: str,
        profile: dict[str, Any] | None,
        issues: list[str],
    ) -> None:
        if profile is None:
            return

        if "slot_capacity" in profile:
            slot_capacity = self._coerce_non_negative_int(
                profile.get("slot_capacity")
            )
            if slot_capacity is None:
                issues.append(
                    f"map '{item_id}' encounter_profile has invalid slot_capacity"
                )

        if "templates" not in profile:
            return
        templates = profile.get("templates")
        if not isinstance(templates, list):
            issues.append(
                f"map '{item_id}' encounter_profile has invalid templates"
            )
            return
        for index, template in enumerate(templates):
            if not isinstance(template, Mapping):
                issues.append(
                    f"map '{item_id}' encounter_profile template {index} must be a mapping"
                )
                continue
            if self._coerce_non_empty_string(template.get("id")) is None:
                issues.append(
                    f"map '{item_id}' encounter_profile template {index} has invalid id"
                )
            if "periods" in template:
                periods = template.get("periods")
                if not isinstance(periods, list):
                    issues.append(
                        f"map '{item_id}' encounter_profile template {index} has invalid periods"
                    )
                else:
                    for period_index, period in enumerate(periods):
                        if self._coerce_non_empty_string(period) is None:
                            issues.append(
                                f"map '{item_id}' encounter_profile template {index} period {period_index} must be a non-empty string"
                            )
            if "source" in template and (
                self._coerce_non_empty_string(template.get("source")) is None
            ):
                issues.append(
                    f"map '{item_id}' encounter_profile template {index} has invalid source"
                )
            if "weight" in template:
                w = self._coerce_float(template.get("weight"))
                if w is None or w <= 0:
                    issues.append(
                        f"map '{item_id}' encounter_profile template {index} has invalid weight"
                    )
