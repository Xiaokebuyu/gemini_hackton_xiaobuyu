"""MonsterRegistry implementation."""

from __future__ import annotations

from typing import Any, Mapping

from app.game_core.content.base import ContentRegistry

_CREATURE_TYPES = frozenset({
    "humanoid", "beast", "undead", "fiend", "dragon", "construct",
    "aberration", "celestial", "elemental", "fey", "giant",
    "monstrosity", "ooze", "plant", "swarm",
})

_ABILITY_NAMES = ("str", "dex", "con", "int", "wis", "cha")

# CR-to-stat heuristic ranges for balance warnings.
_CR_HP_RANGES = [(1, 1, 50), (5, 20, 150), (10, 50, 250), (999, 100, 500)]
_CR_AC_RANGES = [(5, 10, 18), (10, 13, 20), (999, 15, 22)]


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

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_by_cr(self, min_cr: float = 0, max_cr: float = 999) -> list[dict[str, Any]]:
        """Return monsters whose CR falls within [min_cr, max_cr]."""
        results: list[dict[str, Any]] = []
        for item in self._items.values():
            cr = self._coerce_float(item.get("cr"))
            if cr is not None and min_cr <= cr <= max_cr:
                results.append(dict(item))
        return results

    def get_by_type(self, creature_type: str) -> list[dict[str, Any]]:
        """Return monsters matching the given creature_type."""
        normalized = creature_type.strip().lower()
        return [
            dict(item)
            for item in self._items.values()
            if str(item.get("creature_type", "")).strip().lower() == normalized
        ]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        issues: list[str] = []
        for item_id, item in self._items.items():
            if not item.get("id"):
                issues.append(f"monster '{item_id}' missing id")

            # -- Consumer fields (combat.py, encounter.py) --
            if "name" in item and self._coerce_non_empty_string(item.get("name")) is None:
                issues.append(f"monster '{item_id}' has invalid name")

            for field_name in ("hp", "max_hp", "ac"):
                if field_name not in item:
                    continue
                if self._coerce_positive_int(item.get(field_name)) is None:
                    issues.append(f"monster '{item_id}' has invalid {field_name}")

            for field_name in ("gold_drop", "gold", "gold_reward"):
                if field_name not in item:
                    continue
                if self._coerce_non_negative_int(item.get(field_name)) is None:
                    issues.append(f"monster '{item_id}' has invalid {field_name}")

            # -- Game mechanic fields --
            if "cr" in item:
                cr = self._coerce_float(item.get("cr"))
                if cr is None or cr < 0:
                    issues.append(f"monster '{item_id}' has invalid cr")

            if "creature_type" in item:
                ct = self._coerce_non_empty_string(item.get("creature_type"))
                if ct is None or ct.lower() not in _CREATURE_TYPES:
                    issues.append(f"monster '{item_id}' has invalid creature_type")

            self._validate_abilities(item_id, item, issues)
            self._validate_list_of_strings(item_id, item, "resistances", issues)
            self._validate_list_of_strings(item_id, item, "immunities", issues)
            self._validate_attacks(item_id, item, issues)

            # -- Loot table --
            self._validate_loot_table(item_id, item, issues)

            # -- Balance warnings --
            self._check_balance(item_id, item, issues)
        return issues

    def _validate_abilities(
        self, item_id: str, item: dict[str, Any], issues: list[str],
    ) -> None:
        abilities = item.get("abilities")
        if abilities is None:
            return
        if not isinstance(abilities, Mapping):
            issues.append(f"monster '{item_id}' has invalid abilities")
            return
        for attr in _ABILITY_NAMES:
            if attr not in abilities:
                continue
            if self._coerce_positive_int(abilities.get(attr)) is None:
                issues.append(f"monster '{item_id}' abilities has invalid {attr}")

    def _validate_list_of_strings(
        self, item_id: str, item: dict[str, Any], field: str, issues: list[str],
    ) -> None:
        value = item.get(field)
        if value is None:
            return
        if not isinstance(value, list):
            issues.append(f"monster '{item_id}' has invalid {field}")
            return
        for index, entry in enumerate(value):
            if self._coerce_non_empty_string(entry) is None:
                issues.append(f"monster '{item_id}' {field}[{index}] must be a non-empty string")

    def _validate_attacks(
        self, item_id: str, item: dict[str, Any], issues: list[str],
    ) -> None:
        attacks = item.get("attacks")
        if attacks is None:
            return
        if not isinstance(attacks, list):
            issues.append(f"monster '{item_id}' has invalid attacks")
            return
        for index, entry in enumerate(attacks):
            if not isinstance(entry, Mapping):
                issues.append(f"monster '{item_id}' attacks[{index}] must be a mapping")
                continue
            if self._coerce_non_empty_string(entry.get("name")) is None:
                issues.append(f"monster '{item_id}' attacks[{index}] has invalid name")

    def _validate_loot_table(
        self, item_id: str, item: dict[str, Any], issues: list[str],
    ) -> None:
        loot_table = item.get("loot_table")
        if loot_table is None:
            return
        if not isinstance(loot_table, list):
            issues.append(f"monster '{item_id}' has invalid loot_table")
            return
        for index, entry in enumerate(loot_table):
            if not isinstance(entry, Mapping):
                issues.append(f"monster '{item_id}' loot_table[{index}] must be a mapping")
                continue
            if "item_id" in entry and self._coerce_non_empty_string(entry.get("item_id")) is None:
                issues.append(f"monster '{item_id}' loot_table[{index}] has invalid item_id")
            if "count" in entry and self._coerce_non_negative_int(entry.get("count")) is None:
                issues.append(f"monster '{item_id}' loot_table[{index}] has invalid count")
            if "chance" in entry:
                chance = self._coerce_float(entry.get("chance"))
                if chance is None or chance < 0.0 or chance > 1.0:
                    issues.append(f"monster '{item_id}' loot_table[{index}] has invalid chance")

    def _check_balance(
        self, item_id: str, item: dict[str, Any], issues: list[str],
    ) -> None:
        cr = self._coerce_float(item.get("cr"))
        if cr is None or cr < 0:
            return
        hp = self._coerce_positive_int(item.get("hp", item.get("max_hp")))
        if hp is not None:
            for max_cr, low, high in _CR_HP_RANGES:
                if cr <= max_cr:
                    if hp < low or hp > high:
                        issues.append(
                            f"monster '{item_id}' balance warning: CR {cr} with HP {hp} outside expected range {low}-{high}"
                        )
                    break
        ac = self._coerce_positive_int(item.get("ac"))
        if ac is not None:
            for max_cr, low, high in _CR_AC_RANGES:
                if cr <= max_cr:
                    if ac < low or ac > high:
                        issues.append(
                            f"monster '{item_id}' balance warning: CR {cr} with AC {ac} outside expected range {low}-{high}"
                        )
                    break
