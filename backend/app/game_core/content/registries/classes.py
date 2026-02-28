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

            # -- Game mechanic fields (growth.py consumer) --
            if "hit_die" in item:
                hd = item.get("hit_die")
                hd_valid = (
                    (isinstance(hd, str) and self._coerce_non_empty_string(hd) is not None)
                    or (not isinstance(hd, (str, bool)) and self._coerce_positive_int(hd) is not None)
                )
                if not hd_valid:
                    issues.append(f"class entry '{item_id}' has invalid hit_die")

            for field_name in ("base_hp", "hp_per_level"):
                if field_name in item and self._coerce_positive_int(item.get(field_name)) is None:
                    issues.append(f"class entry '{item_id}' has invalid {field_name}")

            if "base_ac" in item and self._coerce_non_negative_int(item.get("base_ac")) is None:
                issues.append(f"class entry '{item_id}' has invalid base_ac")

            if "subclass_level" in item and self._coerce_positive_int(item.get("subclass_level")) is None:
                issues.append(f"class entry '{item_id}' has invalid subclass_level")

            if "starting_gold" in item and self._coerce_non_negative_int(item.get("starting_gold")) is None:
                issues.append(f"class entry '{item_id}' has invalid starting_gold")

            self._validate_level_features(item_id, "class", item, issues)

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

            # -- Subclass game mechanic fields --
            self._validate_string_list(item_id, "subclass", item, "features", issues)
            self._validate_level_features(item_id, "subclass", item, issues)

        for item_id, item in self._races.items():
            if not item.get("id"):
                issues.append(f"race entry '{item_id}' missing id")
            if "stat_bonuses" in item and not isinstance(item.get("stat_bonuses"), Mapping):
                issues.append(f"race entry '{item_id}' has invalid stat_bonuses")
            self._validate_string_list(item_id, "race", item, "racial_traits", issues)

        for item_id, item in self._backgrounds.items():
            if not item.get("id"):
                issues.append(f"background entry '{item_id}' missing id")
            if "feature" in item and self._coerce_non_empty_string(item.get("feature")) is None:
                issues.append(f"background entry '{item_id}' has invalid feature")
            if "gold_bonus" in item and self._coerce_non_negative_int(item.get("gold_bonus")) is None:
                issues.append(f"background entry '{item_id}' has invalid gold_bonus")

        self._validate_xp_curve(issues)
        return issues

    def _validate_level_features(
        self, item_id: str, group: str, item: dict[str, Any], issues: list[str],
    ) -> None:
        lf = item.get("level_features")
        if lf is None:
            return
        if not isinstance(lf, Mapping):
            issues.append(f"{group} entry '{item_id}' has invalid level_features")
            return
        for key, value in lf.items():
            if self._coerce_non_negative_int(key) is None:
                issues.append(f"{group} entry '{item_id}' level_features key '{key}' must be numeric")
            if not isinstance(value, list):
                issues.append(f"{group} entry '{item_id}' level_features[{key}] must be a list")

    def _validate_string_list(
        self, item_id: str, group: str, item: dict[str, Any], field: str, issues: list[str],
    ) -> None:
        value = item.get(field)
        if value is None:
            return
        if not isinstance(value, list):
            issues.append(f"{group} entry '{item_id}' has invalid {field}")
            return
        for index, entry in enumerate(value):
            if self._coerce_non_empty_string(entry) is None:
                issues.append(f"{group} entry '{item_id}' {field}[{index}] must be a non-empty string")

    def _validate_xp_curve(self, issues: list[str]) -> None:
        if isinstance(self._xp_curve, list):
            prev = 0
            for index, value in enumerate(self._xp_curve):
                threshold = self._coerce_non_negative_int(value)
                if threshold is None:
                    issues.append(f"xp_curve[{index}] must be a non-negative int")
                else:
                    if threshold < prev:
                        issues.append(
                            f"xp_curve[{index}] breaks monotonic increase ({threshold} < {prev})"
                        )
                    prev = threshold
        elif isinstance(self._xp_curve, Mapping):
            for level, threshold in self._xp_curve.items():
                if self._coerce_non_negative_int(level) is None:
                    issues.append(f"xp_curve key '{level}' must be numeric")
                if self._coerce_non_negative_int(threshold) is None:
                    issues.append(
                        f"xp_curve entry '{level}' must be a non-negative int"
                    )
            sorted_entries = sorted(
                (
                    (self._coerce_non_negative_int(k), self._coerce_non_negative_int(v))
                    for k, v in self._xp_curve.items()
                ),
                key=lambda pair: pair[0] if pair[0] is not None else -1,
            )
            prev = 0
            for level_num, threshold in sorted_entries:
                if level_num is None or threshold is None:
                    continue
                if threshold < prev:
                    issues.append(
                        f"xp_curve level {level_num} breaks monotonic increase ({threshold} < {prev})"
                    )
                prev = threshold

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
