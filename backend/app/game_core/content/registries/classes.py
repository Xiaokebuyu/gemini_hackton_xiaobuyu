"""ClassRegistry implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.game_core.content.base import ContentRegistry
from app.game_core.content.registries.class_types import Feature, ResourceConfig, SpellcastingConfig


@dataclass(slots=True)
class ClassTemplate:
    id: str
    name: str = ""
    description: str = ""
    hit_die: str | int | None = None
    base_hp: int | None = None
    hp_per_level: int | None = None
    base_ac: int | None = None
    starting_gold: int | None = None
    subclass_level: int | None = None
    spellcasting_ability: str = ""
    prepared_limit: int | None = None
    prepared_formula: str = ""
    level_features: dict[str, list[Feature]] = field(default_factory=dict)
    # 格式：{"resource_key": {"max_at_level": {"1": 1, "5": 2}, "recovery": "short_rest"}}
    class_resources_schema: dict[str, Any] = field(default_factory=dict)
    starting_equipment: list[str] = field(default_factory=list)
    default_equipped: dict[str, str] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    armor_proficiency: list[str] = field(default_factory=list)
    weapon_proficiency: list[str] = field(default_factory=list)
    save_proficiency: list[str] = field(default_factory=list)
    skill_choices: dict[str, Any] = field(default_factory=dict)
    spellcasting: SpellcastingConfig | None = None
    subclass_options: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SubclassTemplate:
    id: str
    class_id: str = ""
    features: list[str] = field(default_factory=list)
    level_features: dict[str, list[Feature]] = field(default_factory=dict)
    name: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    requirements: dict[str, Any] = field(default_factory=dict)
    additional_proficiency: list[str] = field(default_factory=list)
    additional_spellcasting: SpellcastingConfig | None = None


@dataclass(slots=True)
class RaceTemplate:
    id: str
    name: str = ""
    description: str = ""
    stat_bonuses: dict[str, int] = field(default_factory=dict)
    racial_traits: list[Feature] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    speed: int = 30
    languages: list[str] = field(default_factory=list)
    size: str = "medium"


@dataclass(slots=True)
class BackgroundTemplate:
    id: str
    name: str = ""
    description: str = ""
    feature: Feature | None = None
    gold_bonus: int | None = None
    starting_gold: int | None = None
    skill_proficiency: list[str] = field(default_factory=list)
    tool_proficiency: list[str] = field(default_factory=list)
    equipment: list[str] = field(default_factory=list)


class ClassRegistry(ContentRegistry):
    """Registry for classes, races, and backgrounds."""

    def __init__(self) -> None:
        super().__init__("classes")
        self._classes: dict[str, ClassTemplate] = {}
        self._subclasses: dict[str, SubclassTemplate] = {}
        self._races: dict[str, RaceTemplate] = {}
        self._backgrounds: dict[str, BackgroundTemplate] = {}
        self._xp_curve: list[Any] | dict[str, Any] = {}
        self._load_issues: list[str] = []

    def load(self, data: dict[str, Any]) -> None:
        self._classes = {}
        self._subclasses = {}
        self._races = {}
        self._backgrounds = {}
        self._xp_curve = {}
        self._load_issues = []

        structured_keys = {"classes", "subclasses", "races", "backgrounds", "xp_curve"}
        is_structured = (
            isinstance(data, Mapping) and any(key in data for key in structured_keys)
        )
        if is_structured:
            raw_classes = self._coerce_dict_mapping(data.get("classes", {}))
            raw_subclasses = self._coerce_dict_mapping(data.get("subclasses", {}))
            raw_races = self._coerce_dict_mapping(data.get("races", {}))
            raw_backgrounds = self._coerce_dict_mapping(data.get("backgrounds", {}))
            raw_xp_curve = data.get("xp_curve", {})
            if isinstance(raw_xp_curve, list):
                self._xp_curve = list(raw_xp_curve)
            elif isinstance(raw_xp_curve, Mapping):
                self._xp_curve = dict(raw_xp_curve)
            else:
                self._xp_curve = {}
        else:
            raw_classes = self._coerce_dict_mapping(data)
            raw_subclasses = {}
            raw_races = {}
            raw_backgrounds = {}

        for item_id, raw in raw_classes.items():
            template = self._build_class_template(item_id, raw)
            if template is not None:
                self._classes[item_id] = template

        for item_id, raw in raw_subclasses.items():
            template = self._build_subclass_template(item_id, raw)
            if template is not None:
                self._subclasses[item_id] = template

        for item_id, raw in raw_races.items():
            template = self._build_race_template(item_id, raw)
            if template is not None:
                self._races[item_id] = template

        for item_id, raw in raw_backgrounds.items():
            template = self._build_background_template(item_id, raw)
            if template is not None:
                self._backgrounds[item_id] = template

    def _build_class_template(
        self, item_id: str, raw: dict[str, Any],
    ) -> ClassTemplate | None:
        entry_id = self._coerce_non_empty_string(raw.get("id"))
        if not entry_id:
            self._load_issues.append(f"class entry '{item_id}' missing id")
            return None

        raw_hit_die = raw.get("hit_die")
        if raw_hit_die is not None:
            if isinstance(raw_hit_die, str):
                stripped = raw_hit_die.strip()
                if stripped:
                    hit_die: str | int | None = stripped
                else:
                    hit_die = None
                    self._load_issues.append(f"class entry '{item_id}' has invalid hit_die")
            elif isinstance(raw_hit_die, bool):
                hit_die = None
                self._load_issues.append(f"class entry '{item_id}' has invalid hit_die")
            else:
                hit_die = self._coerce_positive_int(raw_hit_die)
                if hit_die is None:
                    self._load_issues.append(f"class entry '{item_id}' has invalid hit_die")
        else:
            hit_die = None

        base_hp = self._safe_positive_int(raw, "base_hp", item_id, "class")
        hp_per_level = self._safe_positive_int(raw, "hp_per_level", item_id, "class")
        base_ac = self._safe_non_negative_int(raw, "base_ac", item_id, "class")
        starting_gold = self._safe_non_negative_int(raw, "starting_gold", item_id, "class")
        subclass_level = self._safe_positive_int(raw, "subclass_level", item_id, "class")
        prepared_limit = self._safe_non_negative_int(raw, "prepared_limit", item_id, "class")

        spellcasting_raw = raw.get("spellcasting_ability")
        spellcasting_ability = ""
        if spellcasting_raw is not None:
            s = self._coerce_non_empty_string(spellcasting_raw)
            if s is None:
                self._load_issues.append(
                    f"class entry '{item_id}' has invalid spellcasting_ability"
                )
            else:
                spellcasting_ability = s

        prepared_formula_raw = raw.get("prepared_formula")
        prepared_formula = ""
        if prepared_formula_raw is not None:
            s = self._coerce_non_empty_string(prepared_formula_raw)
            if s is None:
                self._load_issues.append(
                    f"class entry '{item_id}' has invalid prepared_formula"
                )
            else:
                prepared_formula = s

        level_features = self._load_level_features(raw, item_id, "class")
        class_resources_schema = self._load_class_resources_schema(raw, item_id)

        starting_equipment = self._load_string_list(raw, "starting_equipment")
        default_equipped = self._load_string_dict(raw, "default_equipped")

        tags = self._load_string_list(raw, "tags")
        armor_proficiency = self._load_string_list(raw, "armor_proficiency")
        weapon_proficiency = self._load_string_list(raw, "weapon_proficiency")
        save_proficiency = self._load_string_list(raw, "save_proficiency")
        subclass_options = self._load_string_list(raw, "subclass_options")
        raw_skill_choices = raw.get("skill_choices")
        skill_choices = dict(raw_skill_choices) if isinstance(raw_skill_choices, Mapping) else {}
        spellcasting = self._build_spellcasting_config(item_id, raw)

        # backward-compat: fill flat fields from SpellcastingConfig when absent
        if spellcasting is not None:
            if not spellcasting_ability:
                spellcasting_ability = spellcasting.stat
            if not prepared_formula and spellcasting.prepared_formula:
                prepared_formula = spellcasting.prepared_formula

        return ClassTemplate(
            id=entry_id,
            name=self._extract_string(raw, "name"),
            description=self._extract_string(raw, "description"),
            hit_die=hit_die,
            base_hp=base_hp,
            hp_per_level=hp_per_level,
            base_ac=base_ac,
            starting_gold=starting_gold,
            subclass_level=subclass_level,
            spellcasting_ability=spellcasting_ability,
            prepared_limit=prepared_limit,
            prepared_formula=prepared_formula,
            level_features=level_features,
            class_resources_schema=class_resources_schema,
            starting_equipment=starting_equipment,
            default_equipped=default_equipped,
            tags=tags,
            armor_proficiency=armor_proficiency,
            weapon_proficiency=weapon_proficiency,
            save_proficiency=save_proficiency,
            skill_choices=skill_choices,
            spellcasting=spellcasting,
            subclass_options=subclass_options,
        )

    def _build_subclass_template(
        self, item_id: str, raw: dict[str, Any],
    ) -> SubclassTemplate | None:
        entry_id = self._coerce_non_empty_string(raw.get("id"))
        if not entry_id:
            self._load_issues.append(f"subclass entry '{item_id}' missing id")
            return None

        class_id = self._extract_string(raw, "class_id")
        features = self._load_validated_string_list(raw, "features", item_id, "subclass")
        level_features = self._load_level_features(raw, item_id, "subclass")
        name = self._extract_string(raw, "name")
        description = self._extract_string(raw, "description")
        tags = self._load_string_list(raw, "tags")
        raw_req = raw.get("requirements")
        requirements = dict(raw_req) if isinstance(raw_req, Mapping) else {}
        additional_proficiency = self._load_string_list(raw, "additional_proficiency")
        additional_spellcasting = self._build_spellcasting_config(item_id, raw)

        return SubclassTemplate(
            id=entry_id,
            class_id=class_id,
            features=features,
            level_features=level_features,
            name=name,
            description=description,
            tags=tags,
            requirements=requirements,
            additional_proficiency=additional_proficiency,
            additional_spellcasting=additional_spellcasting,
        )

    def _build_race_template(
        self, item_id: str, raw: dict[str, Any],
    ) -> RaceTemplate | None:
        entry_id = self._coerce_non_empty_string(raw.get("id"))
        if not entry_id:
            self._load_issues.append(f"race entry '{item_id}' missing id")
            return None

        raw_bonuses = raw.get("stat_bonuses")
        stat_bonuses: dict[str, int] = {}
        if raw_bonuses is not None:
            if isinstance(raw_bonuses, Mapping):
                for k, v in raw_bonuses.items():
                    parsed = self._coerce_non_negative_int(v)
                    if parsed is None:
                        try:
                            parsed = int(v)
                        except (TypeError, ValueError):
                            parsed = None
                    if parsed is not None:
                        stat_bonuses[str(k)] = parsed
            else:
                self._load_issues.append(
                    f"race entry '{item_id}' has invalid stat_bonuses"
                )

        raw_traits = raw.get("racial_traits")
        racial_traits: list[Feature] = []
        if raw_traits is not None:
            if not isinstance(raw_traits, list):
                self._load_issues.append(f"race entry '{item_id}' has invalid racial_traits")
            else:
                for idx, entry in enumerate(raw_traits):
                    if isinstance(entry, str):
                        s = entry.strip()
                        if s:
                            racial_traits.append(Feature(id=s, name=s))
                        else:
                            self._load_issues.append(
                                f"race entry '{item_id}' racial_traits[{idx}] must be a non-empty string"
                            )
                    elif isinstance(entry, Mapping):
                        built = self._build_feature(item_id, "racial_traits", idx, entry)
                        if built is not None:
                            racial_traits.append(built)
                    else:
                        self._load_issues.append(
                            f"race entry '{item_id}' racial_traits[{idx}] must be a non-empty string"
                        )
        tags = self._load_string_list(raw, "tags")
        speed_raw = self._safe_positive_int(raw, "speed", item_id, "race")
        speed = speed_raw if speed_raw is not None else 30
        languages = self._load_string_list(raw, "languages")
        size_raw = self._extract_string(raw, "size")
        size = size_raw if size_raw else "medium"

        return RaceTemplate(
            id=entry_id,
            name=self._extract_string(raw, "name"),
            description=self._extract_string(raw, "description"),
            stat_bonuses=stat_bonuses,
            racial_traits=racial_traits,
            tags=tags,
            speed=speed,
            languages=languages,
            size=size,
        )

    def _build_background_template(
        self, item_id: str, raw: dict[str, Any],
    ) -> BackgroundTemplate | None:
        entry_id = self._coerce_non_empty_string(raw.get("id"))
        if not entry_id:
            self._load_issues.append(f"background entry '{item_id}' missing id")
            return None

        feature: Feature | None = None
        raw_feature = raw.get("feature")
        if raw_feature is not None:
            if isinstance(raw_feature, str):
                s = raw_feature.strip()
                if s:
                    feature = Feature(id=s, name=s)
                else:
                    self._load_issues.append(f"background entry '{item_id}' has invalid feature")
            elif isinstance(raw_feature, Mapping):
                feature = self._build_feature(item_id, "background", 0, raw_feature)
            else:
                self._load_issues.append(f"background entry '{item_id}' has invalid feature")

        gold_bonus = self._safe_non_negative_int(raw, "gold_bonus", item_id, "background")
        starting_gold = self._safe_non_negative_int(raw, "starting_gold", item_id, "background")
        skill_proficiency = self._load_string_list(raw, "skill_proficiency")
        tool_proficiency = self._load_string_list(raw, "tool_proficiency")
        equipment = self._load_string_list(raw, "equipment")

        return BackgroundTemplate(
            id=entry_id,
            name=self._extract_string(raw, "name"),
            description=self._extract_string(raw, "description"),
            feature=feature,
            gold_bonus=gold_bonus,
            starting_gold=starting_gold,
            skill_proficiency=skill_proficiency,
            tool_proficiency=tool_proficiency,
            equipment=equipment,
        )

    # -- Getters --

    def get(self, content_id: str) -> ClassTemplate | None:
        return self._classes.get(content_id)

    def list_all(self) -> list[ClassTemplate]:
        return list(self._classes.values())

    def get_class(self, class_id: str) -> ClassTemplate | None:
        return self._classes.get(class_id)

    def get_subclass(self, subclass_id: str) -> SubclassTemplate | None:
        return self._subclasses.get(subclass_id)

    def get_race(self, race_id: str) -> RaceTemplate | None:
        return self._races.get(race_id)

    def get_background(self, background_id: str) -> BackgroundTemplate | None:
        return self._backgrounds.get(background_id)

    def list_classes(self) -> list[ClassTemplate]:
        return list(self._classes.values())

    def list_races(self) -> list[RaceTemplate]:
        return list(self._races.values())

    def list_backgrounds(self) -> list[BackgroundTemplate]:
        return list(self._backgrounds.values())

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
        issues = list(self._load_issues)

        # subclass → class cross-reference
        for item_id, sub in self._subclasses.items():
            if not sub.class_id:
                continue
            if sub.class_id not in self._classes:
                issues.append(
                    f"subclass entry '{item_id}' references unknown class '{sub.class_id}'"
                )

        self._validate_xp_curve(issues)
        return issues

    # -- Load helpers --

    def _build_spellcasting_config(
        self, item_id: str, raw: dict[str, Any],
    ) -> SpellcastingConfig | None:
        """Build SpellcastingConfig from a raw entry dict (reads 'spellcasting' key)."""
        raw_sc = raw.get("spellcasting")
        if raw_sc is None or not isinstance(raw_sc, Mapping):
            return None

        stat = str(raw_sc.get("stat", "")).strip()
        if not stat:
            self._load_issues.append(f"class/subclass entry '{item_id}' spellcasting missing stat")
            return None

        cantrips_known: dict[str, int] = {}
        raw_ck = raw_sc.get("cantrips_known")
        if isinstance(raw_ck, Mapping):
            for k, v in raw_ck.items():
                try:
                    cantrips_known[str(k)] = int(v)
                except (TypeError, ValueError):
                    pass

        spell_slots: dict[str, dict[str, int]] = {}
        raw_ss = raw_sc.get("spell_slots")
        if isinstance(raw_ss, Mapping):
            for level_key, slots_map in raw_ss.items():
                if not isinstance(slots_map, Mapping):
                    continue
                inner: dict[str, int] = {}
                for slot_key, slot_count in slots_map.items():
                    try:
                        inner[str(slot_key)] = int(slot_count)
                    except (TypeError, ValueError):
                        pass
                spell_slots[str(level_key)] = inner

        spells_known: dict[str, int] | None = None
        raw_sk = raw_sc.get("spells_known")
        if isinstance(raw_sk, Mapping):
            spells_known = {}
            for k, v in raw_sk.items():
                try:
                    spells_known[str(k)] = int(v)
                except (TypeError, ValueError):
                    pass

        raw_pf = raw_sc.get("prepared_formula")
        prepared_formula: str | None = None
        if raw_pf is not None:
            s = str(raw_pf).strip()
            if s:
                prepared_formula = s

        return SpellcastingConfig(
            stat=stat,
            cantrips_known=cantrips_known,
            spell_slots=spell_slots,
            spells_known=spells_known,
            prepared_formula=prepared_formula,
        )

    @staticmethod
    def _extract_string(raw: dict[str, Any], field_name: str) -> str:
        value = raw.get(field_name)
        if value is None:
            return ""
        return str(value).strip()

    def _safe_positive_int(
        self, raw: dict[str, Any], field_name: str, item_id: str, group: str,
    ) -> int | None:
        value = raw.get(field_name)
        if value is None:
            return None
        result = self._coerce_positive_int(value)
        if result is None:
            self._load_issues.append(f"{group} entry '{item_id}' has invalid {field_name}")
        return result

    def _safe_non_negative_int(
        self, raw: dict[str, Any], field_name: str, item_id: str, group: str,
    ) -> int | None:
        value = raw.get(field_name)
        if value is None:
            return None
        result = self._coerce_non_negative_int(value)
        if result is None:
            self._load_issues.append(f"{group} entry '{item_id}' has invalid {field_name}")
        return result

    def _build_resource_config(
        self, item_id: str, raw: dict[str, Any],
    ) -> ResourceConfig | None:
        if not isinstance(raw, Mapping):
            return None
        max_at_level_raw = raw.get("max_at_level", {})
        max_at_level: dict[str, int] = {}
        if isinstance(max_at_level_raw, Mapping):
            for k, v in max_at_level_raw.items():
                try:
                    max_at_level[str(k)] = int(v)
                except (ValueError, TypeError):
                    self._load_issues.append(
                        f"class '{item_id}' resource_config max_at_level[{k}] invalid"
                    )
        if not max_at_level:
            return None
        recovery = str(raw.get("recovery", "long_rest")).strip() or "long_rest"
        return ResourceConfig(max_at_level=max_at_level, recovery=recovery)

    def _build_feature(
        self, item_id: str, level_key: str, idx: int, raw: dict[str, Any],
    ) -> Feature | None:
        if not isinstance(raw, Mapping):
            self._load_issues.append(
                f"class '{item_id}' level_features[{level_key}][{idx}] must be a mapping"
            )
            return None
        fid = str(raw.get("id", "")).strip()
        if not fid:
            name_raw = str(raw.get("name", "")).strip()
            if not name_raw:
                self._load_issues.append(
                    f"class '{item_id}' level_features[{level_key}][{idx}] missing id and name"
                )
                return None
            fid = name_raw.lower().replace(" ", "_")
        name = str(raw.get("name", "")).strip() or fid
        description = str(raw.get("description", "")).strip()
        ftype = str(raw.get("type", "passive")).strip() or "passive"
        skill_id_raw = raw.get("skill_id")
        skill_id = str(skill_id_raw).strip() if skill_id_raw and str(skill_id_raw).strip() else None
        resource_config: ResourceConfig | None = None
        rc_raw = raw.get("resource_config")
        if isinstance(rc_raw, Mapping):
            resource_config = self._build_resource_config(item_id, rc_raw)
        return Feature(
            id=fid,
            name=name,
            description=description,
            type=ftype,
            skill_id=skill_id,
            resource_config=resource_config,
        )

    def _load_level_features(
        self, raw: dict[str, Any], item_id: str, group: str,
    ) -> dict[str, list[Feature]]:
        lf = raw.get("level_features")
        if lf is None:
            return {}
        if not isinstance(lf, Mapping):
            self._load_issues.append(
                f"{group} entry '{item_id}' has invalid level_features"
            )
            return {}
        result: dict[str, list[Feature]] = {}
        for key, entries in lf.items():
            level_str = str(key)
            if self._coerce_non_negative_int(key) is None:
                self._load_issues.append(
                    f"{group} entry '{item_id}' level_features key '{key}' must be numeric"
                )
            if isinstance(entries, list):
                features: list[Feature] = []
                for idx, entry in enumerate(entries):
                    if isinstance(entry, str):
                        s = entry.strip()
                        if s:
                            features.append(Feature(id=s, name=s))
                    elif isinstance(entry, Mapping):
                        built = self._build_feature(item_id, level_str, idx, entry)
                        if built is not None:
                            features.append(built)
                    else:
                        self._load_issues.append(
                            f"{group} entry '{item_id}' level_features[{level_str}][{idx}] invalid type"
                        )
                if features:
                    result[level_str] = features
            elif isinstance(entries, str):
                s = entries.strip()
                if s:
                    result[level_str] = [Feature(id=s, name=s)]
            else:
                self._load_issues.append(
                    f"{group} entry '{item_id}' level_features[{level_str}] must be list or string"
                )
        return result

    def _load_class_resources_schema(
        self, raw: dict[str, Any], item_id: str,
    ) -> dict[str, Any]:
        """Load and validate class_resources_schema field.

        Expected format:
            {"action_surge": {"max_at_level": {"2": 1, "17": 2}, "recovery": "short_rest"}}
        """
        schema = raw.get("class_resources_schema")
        if schema is None:
            return {}
        if not isinstance(schema, Mapping):
            self._load_issues.append(
                f"class entry '{item_id}' has invalid class_resources_schema"
            )
            return {}
        result: dict[str, Any] = {}
        for key, config in schema.items():
            key_str = self._coerce_non_empty_string(str(key))
            if key_str is None:
                continue
            if not isinstance(config, Mapping):
                self._load_issues.append(
                    f"class entry '{item_id}' class_resources_schema['{key_str}'] must be a mapping"
                )
                continue
            max_at_level = config.get("max_at_level", {})
            if not isinstance(max_at_level, Mapping):
                self._load_issues.append(
                    f"class entry '{item_id}' class_resources_schema['{key_str}'].max_at_level must be a mapping"
                )
                continue
            recovery_raw = config.get("recovery", "long_rest")
            if self._coerce_non_empty_string(str(recovery_raw)) is None:
                self._load_issues.append(
                    f"class entry '{item_id}' class_resources_schema['{key_str}'].recovery must be a non-empty string"
                )
                continue
            result[key_str] = dict(config)
        return result

    @staticmethod
    def _load_string_list(raw: dict[str, Any], field_name: str) -> list[str]:
        value = raw.get(field_name)
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def _load_validated_string_list(
        self, raw: dict[str, Any], field_name: str, item_id: str, group: str,
    ) -> list[str]:
        value = raw.get(field_name)
        if value is None:
            return []
        if not isinstance(value, list):
            self._load_issues.append(f"{group} entry '{item_id}' has invalid {field_name}")
            return []
        result: list[str] = []
        for index, entry in enumerate(value):
            s = self._coerce_non_empty_string(entry)
            if s is None:
                self._load_issues.append(
                    f"{group} entry '{item_id}' {field_name}[{index}] must be a non-empty string"
                )
            else:
                result.append(s)
        return result

    @staticmethod
    def _load_string_dict(raw: dict[str, Any], field_name: str) -> dict[str, str]:
        value = raw.get(field_name)
        if not isinstance(value, Mapping):
            return {}
        result: dict[str, str] = {}
        for k, v in value.items():
            ks = str(k).strip()
            vs = str(v).strip()
            if ks and vs:
                result[ks] = vs
        return result

    # -- Validation helpers (unchanged) --

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
