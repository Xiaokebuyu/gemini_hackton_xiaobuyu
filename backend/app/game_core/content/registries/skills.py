"""SkillRegistry implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content.base import ContentRegistry


_EFFECT_TYPES = frozenset({
    "heal", "damage", "buff", "debuff", "utility", "summon", "control",
})

_SPELL_SCHOOLS = frozenset({
    "abjuration", "conjuration", "divination", "enchantment",
    "evocation", "illusion", "necromancy", "transmutation",
})

_ACTION_TYPES = frozenset({"action", "bonus_action", "reaction", "free"})


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

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_spells(self) -> list[dict[str, Any]]:
        """Return all entries classified as spells."""
        return [dict(item) for item in self._items.values() if self._is_spell_entry(item)]

    def get_spells_by_level(self, level: int) -> list[dict[str, Any]]:
        """Return spells matching the given spell level."""
        return [
            dict(item)
            for item in self._items.values()
            if self._is_spell_entry(item)
            and self._coerce_non_negative_int(
                item.get("spell_level", item.get("level"))
            ) == level
        ]

    def get_spells_by_school(self, school: str) -> list[dict[str, Any]]:
        """Return spells matching the given school."""
        normalized = school.strip().lower()
        return [
            dict(item)
            for item in self._items.values()
            if self._is_spell_entry(item)
            and str(item.get("school", "")).strip().lower() == normalized
        ]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

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

            # -- Game mechanic fields --
            if effect_type is not None and effect_type.lower() not in _EFFECT_TYPES:
                issues.append(f"spell '{item_id}' has invalid effect type '{effect_type}'")

            # -- Concentration / duration (spell.py consumes these) --
            if "concentration" in item and not self._is_bool_like(item.get("concentration")):
                issues.append(f"spell '{item_id}' has invalid concentration")

            for dur_field in ("duration", "status_duration", "duration_ticks"):
                if dur_field in item and self._coerce_non_negative_int(item.get(dur_field)) is None:
                    issues.append(f"spell '{item_id}' has invalid {dur_field}")

            # -- Action type whitelist --
            top_action = self._coerce_non_empty_string(item.get("action_type"))
            cost_action = (
                self._coerce_non_empty_string(cost.get("action_type"))
                if isinstance(cost, Mapping) and "action_type" in cost
                else None
            )
            for at_value, at_source in [(top_action, "action_type"), (cost_action, "cost.action_type")]:
                if at_value is not None and at_value.lower() not in _ACTION_TYPES:
                    issues.append(f"spell '{item_id}' has invalid {at_source} '{at_value}'")

            # -- School --
            if "school" in item:
                school = self._coerce_non_empty_string(item.get("school"))
                if school is None or school.lower() not in _SPELL_SCHOOLS:
                    issues.append(f"spell '{item_id}' has invalid school")

            # -- Ritual / range / targets --
            if "ritual" in item and not self._is_bool_like(item.get("ritual")):
                issues.append(f"spell '{item_id}' has invalid ritual")

            if "range" in item:
                r = item.get("range")
                r_valid = (
                    (isinstance(r, str) and self._coerce_non_empty_string(r) is not None)
                    or (not isinstance(r, (str, bool)) and self._coerce_non_negative_int(r) is not None)
                )
                if not r_valid:
                    issues.append(f"spell '{item_id}' has invalid range")

            if "targets" in item:
                t = item.get("targets")
                t_valid = (
                    (isinstance(t, str) and self._coerce_non_empty_string(t) is not None)
                    or (not isinstance(t, (str, bool)) and self._coerce_positive_int(t) is not None)
                )
                if not t_valid:
                    issues.append(f"spell '{item_id}' has invalid targets")
        return issues

    def _is_spell_entry(self, item: Mapping[str, Any]) -> bool:
        for field_name in ("category", "type", "kind"):
            if str(item.get(field_name, "")).strip().lower() == "spell":
                return True
        if "spell_level" in item:
            return True
        return "level" in item and "effect" in item
