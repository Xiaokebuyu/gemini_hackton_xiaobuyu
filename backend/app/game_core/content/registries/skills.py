"""SkillRegistry implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content.base import ContentRegistry


class SkillRegistry(ContentRegistry):
    """Registry for skills, spells, and status-effect templates."""

    def __init__(self) -> None:
        super().__init__("skills")
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
                issues.append(f"skill entry '{item_id}' missing id")
            if not self._is_spell_entry(item):
                continue

            spell_level = item.get("spell_level", item.get("level"))
            if spell_level is not None and self._coerce_non_negative_int(spell_level) is None:
                issues.append(f"spell '{item_id}' has invalid spell level")

            effect = item.get("effect")
            if effect is not None and not isinstance(effect, Mapping):
                issues.append(f"spell '{item_id}' has invalid effect")
                continue

            cost = item.get("cost")
            if cost is not None and not isinstance(cost, Mapping):
                issues.append(f"spell '{item_id}' has invalid cost")
            elif isinstance(cost, Mapping):
                if (
                    ("resource_amount" in cost or "amount" in cost)
                    and self._coerce_positive_int(
                        cost.get("resource_amount", cost.get("amount"))
                    )
                    is None
                ):
                    issues.append(f"spell '{item_id}' has invalid resource amount")
                if (
                    "action_type" in cost
                    and self._coerce_non_empty_string(cost.get("action_type")) is None
                ):
                    issues.append(f"spell '{item_id}' has invalid action_type")

            if "upcast_dice" in item and (
                self._coerce_non_empty_string(item.get("upcast_dice")) is None
            ):
                issues.append(f"spell '{item_id}' has invalid upcast_dice")

            if not isinstance(effect, Mapping):
                continue

            effect_type = self._coerce_non_empty_string(effect.get("type"))
            if (
                "applies_status" in effect
                and self._coerce_non_empty_string(effect.get("applies_status")) is None
            ):
                issues.append(f"spell '{item_id}' has invalid applies_status")

            if effect_type == "heal":
                has_fixed_value = any(
                    field_name in effect or field_name in item
                    for field_name in ("heal_amount", "heal")
                )
                if has_fixed_value:
                    for field_name in ("heal_amount", "heal"):
                        if field_name in effect and (
                            self._coerce_non_negative_int(effect.get(field_name)) is None
                        ):
                            issues.append(f"spell '{item_id}' has invalid {field_name}")
                        if field_name in item and (
                            self._coerce_non_negative_int(item.get(field_name)) is None
                        ):
                            issues.append(f"spell '{item_id}' has invalid {field_name}")
                elif self._coerce_non_empty_string(
                    effect.get("dice", item.get("dice"))
                ) is None:
                    issues.append(f"spell '{item_id}' heal effect missing dice")

            if effect_type == "damage":
                has_fixed_value = any(
                    field_name in effect or field_name in item
                    for field_name in ("damage_amount", "damage")
                )
                if has_fixed_value:
                    for field_name in ("damage_amount", "damage"):
                        if field_name in effect and (
                            self._coerce_non_negative_int(effect.get(field_name)) is None
                        ):
                            issues.append(f"spell '{item_id}' has invalid {field_name}")
                        if field_name in item and (
                            self._coerce_non_negative_int(item.get(field_name)) is None
                        ):
                            issues.append(f"spell '{item_id}' has invalid {field_name}")
                elif self._coerce_non_empty_string(
                    effect.get("dice", item.get("dice"))
                ) is None:
                    issues.append(f"spell '{item_id}' damage effect missing dice")
        return issues

    def _is_spell_entry(self, item: Mapping[str, Any]) -> bool:
        for field_name in ("category", "type", "kind"):
            if str(item.get(field_name, "")).strip().lower() == "spell":
                return True
        if "spell_level" in item:
            return True
        return "level" in item and "effect" in item
