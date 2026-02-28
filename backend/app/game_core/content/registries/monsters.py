"""MonsterRegistry implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content.base import ContentRegistry


class MonsterRegistry(ContentRegistry):
    """Registry for monster templates."""

    def __init__(self) -> None:
        super().__init__("monsters")
        self._items: dict[str, dict[str, Any]] = {}

    def load(self, data: dict[str, Any]) -> None:
        self._items = self._coerce_dict_mapping(data)

    def get(self, content_id: str) -> Any | None:
        item = self._items.get(content_id)
        return dict(item) if isinstance(item, dict) else item

    def list_all(self) -> list[Any]:
        return [dict(value) for value in self._items.values()]

    def validate(self) -> list[str]:
        issues: list[str] = []
        for item_id, item in self._items.items():
            if not item.get("id"):
                issues.append(f"monster '{item_id}' missing id")

            for field_name in ("hp", "max_hp", "ac"):
                if field_name not in item:
                    continue
                value = self._coerce_positive_int(item.get(field_name))
                if value is None:
                    issues.append(f"monster '{item_id}' has invalid {field_name}")

            for field_name in ("gold_drop", "gold", "gold_reward"):
                if field_name not in item:
                    continue
                if self._coerce_non_negative_int(item.get(field_name)) is None:
                    issues.append(f"monster '{item_id}' has invalid {field_name}")

            loot_table = item.get("loot_table")
            if loot_table is None:
                continue
            if not isinstance(loot_table, list):
                issues.append(f"monster '{item_id}' has invalid loot_table")
                continue

            for index, entry in enumerate(loot_table):
                if not isinstance(entry, Mapping):
                    issues.append(
                        f"monster '{item_id}' loot_table[{index}] must be a mapping"
                    )
                    continue
                if "item_id" in entry and (
                    self._coerce_non_empty_string(entry.get("item_id")) is None
                ):
                    issues.append(
                        f"monster '{item_id}' loot_table[{index}] has invalid item_id"
                    )
                if (
                    "count" in entry
                    and self._coerce_non_negative_int(entry.get("count")) is None
                ):
                    issues.append(
                        f"monster '{item_id}' loot_table[{index}] has invalid count"
                    )
                if "chance" in entry:
                    chance = self._coerce_float(entry.get("chance"))
                    if chance is None or chance < 0.0 or chance > 1.0:
                        issues.append(
                            f"monster '{item_id}' loot_table[{index}] has invalid chance"
                        )
        return issues
