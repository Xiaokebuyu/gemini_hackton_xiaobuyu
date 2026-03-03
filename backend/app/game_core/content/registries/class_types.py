"""Class-related typed dataclasses for ClassRegistry sub-structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ResourceConfig:
    """Per-resource recovery configuration for a class feature."""

    max_at_level: dict[str, int] = field(default_factory=dict)
    # {"1": 1, "5": 2, "11": 3}
    recovery: str = "long_rest"  # short_rest / long_rest / dawn


@dataclass(slots=True)
class Feature:
    """A single class or subclass feature."""

    id: str = ""
    name: str = ""
    description: str = ""
    type: str = "passive"  # passive / active / resource
    skill_id: str | None = None  # related SkillRegistry id (type="active")
    resource_config: ResourceConfig | None = None  # only when type="resource"


@dataclass(slots=True)
class SpellcastingConfig:
    """Full spellcasting table for a class or subclass."""

    stat: str = ""  # spellcasting ability: int / wis / cha
    cantrips_known: dict[str, int] = field(default_factory=dict)
    # {level_str: count}
    spell_slots: dict[str, dict[str, int]] = field(default_factory=dict)
    # {level_str: {slot_level_str: count}}
    spells_known: dict[str, int] | None = None
    # None = prepared caster; dict = known caster {level_str: count}
    prepared_formula: str | None = None  # e.g. "WIS_mod + level"
