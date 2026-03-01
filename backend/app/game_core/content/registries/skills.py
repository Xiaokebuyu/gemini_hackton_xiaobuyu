"""SkillRegistry implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
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


@dataclass(slots=True)
class SkillTemplate:
    """Typed skill/spell definition."""

    id: str
    name: str = ""
    category: str = ""
    spell_level: int | None = None
    school: str = ""
    concentration: bool = False
    ritual: bool = False
    range: str | int | None = None
    targets: str | int | None = None
    action_type: str = ""
    applies_status: str = ""
    duration: int | None = None
    status_duration: int | None = None
    duration_ticks: int | None = None
    upcast_dice: str = ""
    effect: dict[str, Any] = field(default_factory=dict)
    cost: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


class SkillRegistry(ContentRegistry):
    """Registry for skills, spells, and status-effect templates."""

    def __init__(self) -> None:
        super().__init__("skills")
        self._items: dict[str, SkillTemplate] = {}
        self._load_issues: list[str] = []

    def load(self, data: dict[str, Any]) -> None:
        self._items = {}
        self._load_issues = []
        coerced = self._coerce_dict_mapping(data)
        for sid, raw in coerced.items():
            if not raw.get("id"):
                self._load_issues.append(f"skill entry '{sid}' missing id")

            is_spell = self._is_spell_entry(raw)
            category = ""
            if is_spell:
                category = "spell"
                self._validate_spell_fields(sid, raw)

            # Resolve spell_level from "spell_level" or "level"
            spell_level = self._coerce_non_negative_int(
                raw.get("spell_level", raw.get("level"))
            )

            # Effect and cost stay as dicts
            raw_effect = raw.get("effect")
            effect: dict[str, Any] = (
                dict(raw_effect) if isinstance(raw_effect, Mapping) else {}
            )

            raw_cost = raw.get("cost")
            cost: dict[str, Any] = (
                dict(raw_cost) if isinstance(raw_cost, Mapping) else {}
            )

            # Bool-like fields
            concentration = False
            if self._is_bool_like(raw.get("concentration")):
                concentration = bool(raw["concentration"])

            ritual = False
            if self._is_bool_like(raw.get("ritual")):
                ritual = bool(raw["ritual"])

            # Range and targets — preserve original type
            raw_range = raw.get("range")
            range_val: str | int | None = None
            if isinstance(raw_range, str):
                s = self._coerce_non_empty_string(raw_range)
                if s is not None:
                    range_val = s
            elif raw_range is not None and not isinstance(raw_range, bool):
                v = self._coerce_non_negative_int(raw_range)
                if v is not None:
                    range_val = v

            raw_targets = raw.get("targets")
            targets_val: str | int | None = None
            if isinstance(raw_targets, str):
                s = self._coerce_non_empty_string(raw_targets)
                if s is not None:
                    targets_val = s
            elif raw_targets is not None and not isinstance(raw_targets, bool):
                v = self._coerce_positive_int(raw_targets)
                if v is not None:
                    targets_val = v

            # Tags
            raw_tags = raw.get("tags")
            tags: list[str] = []
            if isinstance(raw_tags, list):
                tags = [str(t) for t in raw_tags if isinstance(t, str) and str(t).strip()]

            # Applies status — from effect or top-level
            applies_status = ""
            if isinstance(raw_effect, Mapping):
                s = self._coerce_non_empty_string(raw_effect.get("applies_status"))
                if s is not None:
                    applies_status = s
            if not applies_status:
                s = self._coerce_non_empty_string(raw.get("applies_status"))
                if s is not None:
                    applies_status = s

            self._items[sid] = SkillTemplate(
                id=str(raw.get("id", sid)),
                name=str(raw.get("name") or ""),
                category=category,
                spell_level=spell_level,
                school=str(raw.get("school") or "").strip().lower(),
                concentration=concentration,
                ritual=ritual,
                range=range_val,
                targets=targets_val,
                action_type=str(raw.get("action_type") or "").strip().lower(),
                applies_status=applies_status,
                duration=self._coerce_non_negative_int(raw.get("duration")),
                status_duration=self._coerce_non_negative_int(raw.get("status_duration")),
                duration_ticks=self._coerce_non_negative_int(raw.get("duration_ticks")),
                upcast_dice=str(raw.get("upcast_dice") or "").strip(),
                effect=effect,
                cost=cost,
                tags=tags,
            )

    def get(self, content_id: str) -> SkillTemplate | None:
        return self._items.get(content_id)

    def list_all(self) -> list[SkillTemplate]:
        return list(self._items.values())

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_spells(self) -> list[SkillTemplate]:
        """Return all entries classified as spells."""
        return [s for s in self._items.values() if s.category == "spell"]

    def get_spells_by_level(self, level: int) -> list[SkillTemplate]:
        """Return spells matching the given spell level."""
        return [
            s for s in self._items.values()
            if s.category == "spell" and s.spell_level == level
        ]

    def get_spells_by_school(self, school: str) -> list[SkillTemplate]:
        """Return spells matching the given school."""
        normalized = school.strip().lower()
        return [
            s for s in self._items.values()
            if s.category == "spell" and s.school == normalized
        ]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        return list(self._load_issues)

    # ------------------------------------------------------------------
    # Load-time validation helpers
    # ------------------------------------------------------------------

    def _validate_spell_fields(self, sid: str, raw: dict[str, Any]) -> None:
        """Validate spell-specific fields and collect issues."""
        # Spell level
        spell_level = raw.get("spell_level", raw.get("level"))
        if spell_level is not None and self._coerce_non_negative_int(spell_level) is None:
            self._load_issues.append(f"spell '{sid}' has invalid spell level")

        # Effect
        raw_effect = raw.get("effect")
        if raw_effect is not None and not isinstance(raw_effect, Mapping):
            self._load_issues.append(f"spell '{sid}' has invalid effect")
            return

        # Cost
        raw_cost = raw.get("cost")
        if raw_cost is not None and not isinstance(raw_cost, Mapping):
            self._load_issues.append(f"spell '{sid}' has invalid cost")
        elif isinstance(raw_cost, Mapping):
            if (
                ("resource_amount" in raw_cost or "amount" in raw_cost)
                and self._coerce_positive_int(
                    raw_cost.get("resource_amount", raw_cost.get("amount"))
                ) is None
            ):
                self._load_issues.append(f"spell '{sid}' has invalid resource amount")
            if (
                "action_type" in raw_cost
                and self._coerce_non_empty_string(raw_cost.get("action_type")) is None
            ):
                self._load_issues.append(f"spell '{sid}' has invalid action_type")

        # Upcast dice
        if "upcast_dice" in raw and (
            self._coerce_non_empty_string(raw.get("upcast_dice")) is None
        ):
            self._load_issues.append(f"spell '{sid}' has invalid upcast_dice")

        if not isinstance(raw_effect, Mapping):
            return

        # Effect type and sub-fields
        effect_type = self._coerce_non_empty_string(raw_effect.get("type"))

        if (
            "applies_status" in raw_effect
            and self._coerce_non_empty_string(raw_effect.get("applies_status")) is None
        ):
            self._load_issues.append(f"spell '{sid}' has invalid applies_status")

        if effect_type == "heal":
            has_fixed = any(
                fn in raw_effect or fn in raw
                for fn in ("heal_amount", "heal")
            )
            if has_fixed:
                for fn in ("heal_amount", "heal"):
                    if fn in raw_effect and self._coerce_non_negative_int(raw_effect.get(fn)) is None:
                        self._load_issues.append(f"spell '{sid}' has invalid {fn}")
                    if fn in raw and self._coerce_non_negative_int(raw.get(fn)) is None:
                        self._load_issues.append(f"spell '{sid}' has invalid {fn}")
            elif self._coerce_non_empty_string(
                raw_effect.get("dice", raw.get("dice"))
            ) is None:
                self._load_issues.append(f"spell '{sid}' heal effect missing dice")

        if effect_type == "damage":
            has_fixed = any(
                fn in raw_effect or fn in raw
                for fn in ("damage_amount", "damage")
            )
            if has_fixed:
                for fn in ("damage_amount", "damage"):
                    if fn in raw_effect and self._coerce_non_negative_int(raw_effect.get(fn)) is None:
                        self._load_issues.append(f"spell '{sid}' has invalid {fn}")
                    if fn in raw and self._coerce_non_negative_int(raw.get(fn)) is None:
                        self._load_issues.append(f"spell '{sid}' has invalid {fn}")
            elif self._coerce_non_empty_string(
                raw_effect.get("dice", raw.get("dice"))
            ) is None:
                self._load_issues.append(f"spell '{sid}' damage effect missing dice")

        if effect_type is not None and effect_type.lower() not in _EFFECT_TYPES:
            self._load_issues.append(f"spell '{sid}' has invalid effect type '{effect_type}'")

        # Concentration / duration
        if "concentration" in raw and not self._is_bool_like(raw.get("concentration")):
            self._load_issues.append(f"spell '{sid}' has invalid concentration")

        for dur_field in ("duration", "status_duration", "duration_ticks"):
            if dur_field in raw and self._coerce_non_negative_int(raw.get(dur_field)) is None:
                self._load_issues.append(f"spell '{sid}' has invalid {dur_field}")

        # Action type whitelist
        top_action = self._coerce_non_empty_string(raw.get("action_type"))
        cost_action = (
            self._coerce_non_empty_string(raw_cost.get("action_type"))
            if isinstance(raw_cost, Mapping) and "action_type" in raw_cost
            else None
        )
        for at_value, at_source in [(top_action, "action_type"), (cost_action, "cost.action_type")]:
            if at_value is not None and at_value.lower() not in _ACTION_TYPES:
                self._load_issues.append(f"spell '{sid}' has invalid {at_source} '{at_value}'")

        # School
        if "school" in raw:
            school = self._coerce_non_empty_string(raw.get("school"))
            if school is None or school.lower() not in _SPELL_SCHOOLS:
                self._load_issues.append(f"spell '{sid}' has invalid school")

        # Ritual / range / targets
        if "ritual" in raw and not self._is_bool_like(raw.get("ritual")):
            self._load_issues.append(f"spell '{sid}' has invalid ritual")

        if "range" in raw:
            r = raw.get("range")
            r_valid = (
                (isinstance(r, str) and self._coerce_non_empty_string(r) is not None)
                or (not isinstance(r, (str, bool)) and self._coerce_non_negative_int(r) is not None)
            )
            if not r_valid:
                self._load_issues.append(f"spell '{sid}' has invalid range")

        if "targets" in raw:
            t = raw.get("targets")
            t_valid = (
                (isinstance(t, str) and self._coerce_non_empty_string(t) is not None)
                or (not isinstance(t, (str, bool)) and self._coerce_positive_int(t) is not None)
            )
            if not t_valid:
                self._load_issues.append(f"spell '{sid}' has invalid targets")

    def _is_spell_entry(self, item: Mapping[str, Any]) -> bool:
        for field_name in ("category", "type", "kind"):
            if str(item.get(field_name, "")).strip().lower() == "spell":
                return True
        if "spell_level" in item:
            return True
        return "level" in item and "effect" in item
