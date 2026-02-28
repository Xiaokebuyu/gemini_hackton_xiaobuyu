"""ClassRegistry implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content.base import ContentRegistry


class ClassRegistry(ContentRegistry):
    """Registry for classes, races, and backgrounds."""

    def __init__(self) -> None:
        super().__init__("classes")
        self._classes: dict[str, dict[str, Any]] = {}
        self._subclasses: dict[str, dict[str, Any]] = {}
        self._races: dict[str, dict[str, Any]] = {}
        self._backgrounds: dict[str, dict[str, Any]] = {}
        self._xp_curve: list[Any] | dict[str, Any] = {}

    def load(self, data: dict[str, Any]) -> None:
        structured_keys = {"classes", "subclasses", "races", "backgrounds", "xp_curve"}
        is_structured = (
            isinstance(data, Mapping) and any(key in data for key in structured_keys)
        )
        if is_structured:
            self._classes = self._coerce_dict_mapping(data.get("classes", {}))
            self._subclasses = self._coerce_dict_mapping(data.get("subclasses", {}))
            self._races = self._coerce_dict_mapping(data.get("races", {}))
            self._backgrounds = self._coerce_dict_mapping(data.get("backgrounds", {}))
            raw_xp_curve = data.get("xp_curve", {})
            if isinstance(raw_xp_curve, list):
                self._xp_curve = list(raw_xp_curve)
            elif isinstance(raw_xp_curve, Mapping):
                self._xp_curve = dict(raw_xp_curve)
            else:
                self._xp_curve = {}
            return
        self._classes = self._coerce_dict_mapping(data)
        self._subclasses = {}
        self._races = {}
        self._backgrounds = {}
        self._xp_curve = {}

    def get(self, content_id: str) -> Any | None:
        item = self._classes.get(content_id)
        return dict(item) if isinstance(item, dict) else item

    def list_all(self) -> list[Any]:
        return [dict(value) for value in self._classes.values()]

    def get_class(self, class_id: str) -> dict[str, Any] | None:
        item = self._classes.get(class_id)
        return dict(item) if isinstance(item, dict) else None

    def get_subclass(self, subclass_id: str) -> dict[str, Any] | None:
        item = self._subclasses.get(subclass_id)
        return dict(item) if isinstance(item, dict) else None

    def get_race(self, race_id: str) -> dict[str, Any] | None:
        item = self._races.get(race_id)
        return dict(item) if isinstance(item, dict) else None

    def get_background(self, background_id: str) -> dict[str, Any] | None:
        item = self._backgrounds.get(background_id)
        return dict(item) if isinstance(item, dict) else None

    def list_classes(self) -> list[dict[str, Any]]:
        return [dict(value) for value in self._classes.values()]

    def list_races(self) -> list[dict[str, Any]]:
        return [dict(value) for value in self._races.values()]

    def list_backgrounds(self) -> list[dict[str, Any]]:
        return [dict(value) for value in self._backgrounds.values()]

    def xp_threshold_for_level(self, level: int) -> int | None:
        if level < 1:
            return None
        if isinstance(self._xp_curve, list):
            index = level - 1
            if not 0 <= index < len(self._xp_curve):
                return None
            return self._coerce_threshold(self._xp_curve[index])
        if isinstance(self._xp_curve, Mapping):
            for key in (level, str(level)):
                if key in self._xp_curve:
                    return self._coerce_threshold(self._xp_curve[key])
        return None

    def validate(self) -> list[str]:
        issues: list[str] = []
        for item_id, item in self._classes.items():
            if not item.get("id"):
                issues.append(f"class entry '{item_id}' missing id")
            if (
                "spellcasting_ability" in item
                and self._coerce_non_empty_string(item.get("spellcasting_ability")) is None
            ):
                issues.append(
                    f"class entry '{item_id}' has invalid spellcasting_ability"
                )
            if (
                "prepared_limit" in item
                and self._coerce_non_negative_int(item.get("prepared_limit")) is None
            ):
                issues.append(f"class entry '{item_id}' has invalid prepared_limit")
            if (
                "prepared_formula" in item
                and self._coerce_non_empty_string(item.get("prepared_formula")) is None
            ):
                issues.append(f"class entry '{item_id}' has invalid prepared_formula")

        for item_id, item in self._subclasses.items():
            if not item.get("id"):
                issues.append(f"subclass entry '{item_id}' missing id")
            if "class_id" not in item:
                continue
            class_id = self._coerce_non_empty_string(item.get("class_id"))
            if class_id is None:
                issues.append(f"subclass entry '{item_id}' has invalid class_id")
                continue
            if class_id not in self._classes:
                issues.append(
                    f"subclass entry '{item_id}' references unknown class '{class_id}'"
                )

        for group_name, payload in (
            ("race", self._races),
            ("background", self._backgrounds),
        ):
            for item_id, item in payload.items():
                if not item.get("id"):
                    issues.append(f"{group_name} entry '{item_id}' missing id")

        if isinstance(self._xp_curve, list):
            for index, value in enumerate(self._xp_curve):
                if self._coerce_non_negative_int(value) is None:
                    issues.append(f"xp_curve[{index}] must be a non-negative int")
        elif isinstance(self._xp_curve, Mapping):
            for level, threshold in self._xp_curve.items():
                if self._coerce_non_negative_int(level) is None:
                    issues.append(f"xp_curve key '{level}' must be numeric")
                if self._coerce_non_negative_int(threshold) is None:
                    issues.append(
                        f"xp_curve entry '{level}' must be a non-negative int"
                    )
        return issues

    @staticmethod
    def _coerce_threshold(value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            threshold = int(value)
        except (TypeError, ValueError):
            return None
        if threshold < 0:
            return None
        return threshold
