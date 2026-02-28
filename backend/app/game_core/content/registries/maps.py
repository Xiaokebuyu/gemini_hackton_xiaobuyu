"""MapRegistry implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content.base import ContentRegistry


class MapRegistry(ContentRegistry):
    """Registry for area and map templates."""

    def __init__(self) -> None:
        super().__init__("maps")
        self._items: dict[str, dict[str, Any]] = {}

    def load(self, data: dict[str, Any]) -> None:
        self._items = self._coerce_dict_mapping(data)

    def get(self, content_id: str) -> Any | None:
        item = self._items.get(content_id)
        return dict(item) if isinstance(item, dict) else item

    def list_all(self) -> list[Any]:
        return [dict(value) for value in self._items.values()]

    def starting_area(self) -> dict[str, Any] | None:
        explicit_keys = ("is_starting_area", "starting_area", "is_start")
        for item in self._items.values():
            if any(bool(item.get(key)) for key in explicit_keys):
                return dict(item)

        lowest_item: dict[str, Any] | None = None
        lowest_danger: float | None = None
        for item in self._items.values():
            raw_danger = item.get("base_danger", item.get("danger_level", 1.0))
            try:
                danger = float(raw_danger)
            except (TypeError, ValueError):
                danger = 1.0
            if lowest_danger is None or danger < lowest_danger:
                lowest_danger = danger
                lowest_item = item
        return dict(lowest_item) if isinstance(lowest_item, dict) else None

    def validate(self) -> list[str]:
        issues: list[str] = []
        for item_id, item in self._items.items():
            if not item.get("id"):
                issues.append(f"map '{item_id}' missing id")
            for field_name in ("base_danger", "danger_level"):
                if field_name not in item:
                    continue
                danger = self._coerce_float(item.get(field_name))
                if danger is None or danger < 0:
                    issues.append(f"map '{item_id}' has invalid {field_name}")

            sub_locations = item.get("sub_locations")
            if sub_locations is not None:
                if not isinstance(sub_locations, Mapping):
                    issues.append(f"map '{item_id}' has invalid sub_locations")
                else:
                    for sub_key, sub_location in sub_locations.items():
                        if not isinstance(sub_location, Mapping):
                            issues.append(
                                f"map '{item_id}' sub_location '{sub_key}' must be a mapping"
                            )
                            continue
                        if "id" in sub_location and (
                            self._coerce_non_empty_string(sub_location.get("id")) is None
                        ):
                            issues.append(
                                f"map '{item_id}' sub_location '{sub_key}' has invalid id"
                            )

            for field_name in ("is_starting_area", "starting_area", "is_start"):
                if field_name not in item:
                    continue
                if not self._is_bool_like(item.get(field_name)):
                    issues.append(f"map '{item_id}' has invalid {field_name}")

            encounter_profile = item.get("encounter_profile")
            if encounter_profile is None:
                continue
            if not isinstance(encounter_profile, Mapping):
                issues.append(f"map '{item_id}' has invalid encounter_profile")
                continue

            if "slot_capacity" in encounter_profile:
                slot_capacity = self._coerce_non_negative_int(
                    encounter_profile.get("slot_capacity")
                )
                if slot_capacity is None:
                    issues.append(
                        f"map '{item_id}' encounter_profile has invalid slot_capacity"
                    )

            if "templates" not in encounter_profile:
                continue
            templates = encounter_profile.get("templates")
            if not isinstance(templates, list):
                issues.append(
                    f"map '{item_id}' encounter_profile has invalid templates"
                )
                continue
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
        return issues
